"""One-shot plan baseline and state-aware plan validation (SPEC 5.3, 8, 11.2).

`RulePlanner` is the Mode A control condition: it reads the goals and the state
once, emits a full plan, and does not revise it. It is *not* a general Chinese
NL parser and it is not smarter than the model on purpose.

`PlanValidator` checks a plan against the **latest** snapshot rather than the
version the plan was generated from, so advancing state versions alone never
invalidate an action that is still legal (SPEC 6.1). It uses no scoring truth.
"""
from __future__ import annotations

from .contracts import (
    Decision,
    DecisionContext,
    DecisionExecute,
    GoalSpec,
    Plan,
    PlanStep,
    PredicateVerdict,
    VerifyConfig,
    WorldState,
)
from .verify import RuntimeVerifier

REQUIRED_ARGS = {
    "pick": {"object_id"},
    "place": {"object_id", "target_id"},
    "observe": set(),
    "safe_retreat": set(),
}
OPTIONAL_ARGS = {
    "pick": {"grasp_candidate_index"},
    "place": {"candidate_id"},
    "observe": set(),
    "safe_retreat": set(),
}


class PlanValidator:
    """Schema, reference and symbolic-precondition checks against one snapshot.

    Two defects fixed here (SPEC 2.1, 8): targets are read from the world
    instead of a hard-coded whitelist, and the hold state is tracked through the
    plan instead of assuming the gripper starts empty — a plan that begins with
    `place` is legal when the object is already held."""

    def __init__(self, world: WorldState, config: VerifyConfig | None = None):
        self.world = world
        self.config = config or VerifyConfig()

    def validate(self, plan: Plan) -> tuple[bool, list[str]]:
        errs = list(plan.validate_schema())
        if plan.schema_version != self.world.schema_version:
            errs.append(f"unsupported schema_version {plan.schema_version}")
        if not plan.steps:
            errs.append("empty plan (a legal no-op must be represented explicitly)")

        known_entities = {e.entity_id for e in self.world.entities}
        known_targets = {t.target_id for t in self.world.targets}
        held = self.world.held_object
        unknown_hold = held == "unknown"

        for s in plan.steps:
            if s.skill not in REQUIRED_ARGS:
                errs.append(f"step {s.id}: unknown skill {s.skill}")
                continue
            for a in REQUIRED_ARGS[s.skill] - set(s.args):
                errs.append(f"step {s.id}: missing required argument {a} for {s.skill}")
            for a in set(s.args) - REQUIRED_ARGS[s.skill] - OPTIONAL_ARGS[s.skill]:
                errs.append(f"step {s.id}: unexpected argument {a} for {s.skill}")
            if s.skill == "pick" and s.args.get("object_id") not in known_entities:
                errs.append(f"step {s.id}: object {s.args.get('object_id')} is not in "
                            f"observation {self.world.observation_ref}")
            if s.skill == "place":
                if s.args.get("object_id") not in known_entities:
                    errs.append(f"step {s.id}: object {s.args.get('object_id')} is not in "
                                f"observation {self.world.observation_ref}")
                if s.args.get("target_id") not in known_targets:
                    errs.append(f"step {s.id}: target {s.args.get('target_id')} is not in "
                                f"observation {self.world.observation_ref}")

            # symbolic hold tracking against the snapshot's initial hold state
            if s.skill == "pick":
                oid = s.args.get("object_id")
                if unknown_hold:
                    errs.append(f"step {s.id}: pick while the hold state is unknown")
                elif held is not None and held != oid:
                    errs.append(f"step {s.id}: cannot pick {oid}, gripper holds {held}")
                else:
                    held = oid
            elif s.skill == "place":
                oid = s.args.get("object_id")
                if unknown_hold:
                    errs.append(f"step {s.id}: place while the hold state is unknown")
                elif held != oid:
                    errs.append(f"step {s.id}: place {oid} before it is held "
                                f"(gripper holds {held!r})")
                else:
                    held = None
        return len(errs) == 0, errs


class RulePlanner:
    """Generates one pick/place sequence per unsatisfied assignment.

    Progress is recomputed from the *current* snapshot every time `plan` is
    called, with the same verifier the runtime uses — there is no permanent
    "already placed" flag (SPEC 5.1)."""

    def __init__(self, config: VerifyConfig | None = None):
        self.config = config or VerifyConfig()

    def pending_assignments(self, goal: GoalSpec, world: WorldState) -> list[tuple[str, str]]:
        v = RuntimeVerifier(world, self.config)
        pending = []
        for item in v.progress(goal):
            # `unknown` is pending as well: unmeasurable is not satisfied
            if item.value != PredicateVerdict.true and world.has_entity(item.entity_id):
                pending.append((item.entity_id, item.target_id))
        return pending

    def plan(self, goal: GoalSpec, world: WorldState, plan_id: str | None = None) -> Plan:
        """Steps name the target region, not a slot.

        A one-shot plan cannot know which slot will be free when the gripper
        arrives, and freezing a t0 slot would punish the baseline for the state
        its own plan had to anticipate (SPEC 5.5). Both modes therefore share the
        documented fallback resolution rule, and its use is recorded per step.
        """
        pending = self.pending_assignments(goal, world)
        steps: list[PlanStep] = []
        for i, (eid, tid) in enumerate(pending):
            steps.append(PlanStep(id=f"s{2 * i}", skill="pick", args={"object_id": eid}))
            steps.append(PlanStep(id=f"s{2 * i + 1}", skill="place",
                                  args={"object_id": eid, "target_id": tid}))
        return Plan(
            plan_id=plan_id or f"p_rule_{len(pending)}",
            based_on_state_version=world.state_version,
            goal_ref=goal.goal_id,
            steps=steps,
        )

    def decide(self, ctx: DecisionContext, prefer: str | None = None) -> Decision:
        """One action for one context: the deterministic offline control policy.

        This is the single implementation of the rule policy — the offline mode B
        source and the offline fixture planner both reach the decision through it,
        so "the rule baseline" cannot mean two slightly different things in two
        files. It reads only the context: pending goals and the measured hold
        state decide, never a plan of its own.
        """
        pending = self.pending_assignments(ctx.goal, ctx.world)
        if prefer:
            pending.sort(key=lambda t: (t[0] != prefer, t[0]))
        held = ctx.world.held_object
        if isinstance(held, str) and held and held != "unknown":
            target = next((t for e, t in pending if e == held), None)
            if target is None:
                return _control(ctx, "blocked", f"holding {held} but no pending goal names it")
            return _execute(ctx, "place", {"object_id": held, "target_id": target},
                            f"place {held} into {target}")
        if not pending:
            return _control(ctx, "finish", "every assignment measured satisfied")
        eid, _tid = pending[0]
        return _execute(ctx, "pick", {"object_id": eid}, f"pick {eid}")


def _execute(ctx: DecisionContext, skill: str, args: dict, why: str) -> Decision:
    return Decision(context_id=ctx.context_id, based_on_state_version=ctx.state_version,
                    goal_ref=ctx.goal.goal_id, action="execute",
                    execute=DecisionExecute(skill=skill, args=args),
                    rationale=why, evidence_refs=[ctx.observation_ref])


def _control(ctx: DecisionContext, action: str, why: str) -> Decision:
    return Decision(context_id=ctx.context_id, based_on_state_version=ctx.state_version,
                    goal_ref=ctx.goal.goal_id, action=action, rationale=why,
                    evidence_refs=[ctx.observation_ref],
                    missing_information=why if action == "clarify" else None)


class OneShotPlanSource:
    """Mode A decision source: emit the pre-computed steps, fixed repeat rule,
    no revision (SPEC 11.2).

    The only reaction it is allowed is the frozen one: when a step that really
    executed fails, and the action was not rejected on preconditions, submit the
    identical skill / object / candidate at most `per_action_extra_repeats` more
    times, then stop trying. It never adds an action, never reorders, never
    chooses a different target, and at the end of the plan it asks for `finish`
    so the agent-side goals are checked under the same protocol as Mode B.
    """

    provider = "one_shot_plan"
    # a class attribute, not an instance one: a subclass that reports the real
    # provider's cumulative counter overrides it with a property
    http_requests = 0

    def __init__(self, plan: Plan, per_action_extra_repeats: int = 2):
        self.plan = plan
        self.per_action_extra_repeats = per_action_extra_repeats
        self._index = 0
        self._repeats = 0
        self._pending_step: PlanStep | None = None

    def decide(self, ctx: DecisionContext) -> Decision:
        if self._pending_step is not None:
            self._settle_pending(ctx.last_feedback)
        if self._pending_step is None:
            self._pending_step = self._pop_step()
        step = self._pending_step
        if step is None:
            return self._finish(ctx, "one-shot plan exhausted")
        self._pending_step = step
        candidate_id = step.args.get("candidate_id")
        args = {k: v for k, v in step.args.items() if k != "candidate_id"}
        return Decision(
            context_id=ctx.context_id, based_on_state_version=ctx.state_version,
            goal_ref=self.plan.goal_ref, action="execute",
            execute=DecisionExecute(skill=step.skill, args=args, candidate_id=candidate_id),
            rationale=f"one-shot step {step.id}, planned at state v"
                      f"{self.plan.based_on_state_version}, repeat {self._repeats}",
            evidence_refs=[ctx.observation_ref],
        )

    def _settle_pending(self, fb) -> None:
        if fb is None or fb.status in ("model_error",):
            return                      # nothing was executed; the step stays pending
        if fb.status == "completed" or fb.status == "rejected":
            # rejected = a guard the baseline is not allowed to argue with
            self._pending_step, self._repeats = None, 0
            return
        if fb.status in ("failed", "timeout", "uncertain"):
            if self._repeats >= self.per_action_extra_repeats:
                self._pending_step, self._repeats = None, 0
            else:
                self._repeats += 1      # the one permitted reaction: identical repeat

    def _finish(self, ctx: DecisionContext, why: str) -> Decision:
        return Decision(context_id=ctx.context_id, based_on_state_version=ctx.state_version,
                        goal_ref=self.plan.goal_ref, action="finish", rationale=why,
                        evidence_refs=[ctx.observation_ref])

    def _pop_step(self) -> PlanStep | None:
        if self._index >= len(self.plan.steps):
            return None
        step = self.plan.steps[self._index]
        self._index += 1
        self._repeats = 0
        return step
