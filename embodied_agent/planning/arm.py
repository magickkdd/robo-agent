"""The planning loop wired into the decision loop, and gated by the arm (§5.2, §5.3, §7, §9).

`PerceptRuntime` is the template: one class, a handful of overridden seams, and a name bridge,
which is the whole of it. `test_the_arm_overrides_the_seams_it_names_and_nothing_else` pins the
set. Six seams move, and every one of them is a place where the loop already had a slot for
something to be put:

* `_build_context` — §7 step 2, "update plan/subgoals from the latest evidence". The plan is
  authored once and re-read every round, the memory is synced against it, and the two sections are
  copied into the context before the payload is rendered. This is the only place the plan is
  *offered*; nothing here decides which row to work.
* `_validate_decision` — §7 step 6, "record intent as a subgoal". It calls the base validator
  first and **returns its verdict unchanged**: the arm adds records, never rejection reasons, so
  the arms differ only in what the decision maker was shown. Whatever it accepts it hands to
  `WorkingMemory.commit()` and `TaskPlanner.mark_attempt()`.
* `_build_feedback` — not to change the feedback, which it must not, but because it is the only
  seam that receives *both* snapshots of an action. It remembers them so the memory can settle a
  debt against the post-world rather than against a stale one.
* `_record_feedback` — §7 steps 9-10, "mark failed, record what must be restored".
* `_finalize` — the terminal `obligation_check`, §11's row of the same name, filed before
  `episode_end` so the audit is on the record before the result claims a status.
* `_begin_episode` — per-episode state, so a reused runtime cannot carry one episode's debts into
  the next.

## What the gate is allowed to switch

`core/v02.py:EVENT_MODULE` already says which module owns which record, and that mapping *is* the
gate: `planning` off means no `task_understanding`, no `plan`, no `plan_revision`, no `recovery_action`
and no `plan` payload section; `working_memory` off means no `working_memory` event, no
`obligation_check` and no `working_memory` section; `replanning` off means the work graph is never
re-derived — the world is still re-measured, because that is verification and §9 does not offer an
arm for "stop noticing". A revision can therefore still appear under `wo_replanning` when a done
row's measurement flips, and the registry attributes `plan_revision` to `planning`, not to
`replanning`, which is why that arm stays coherent rather than being special-cased here.

No event type outside `V02_EVENT_TYPES` is ever written, and that is a hard constraint rather than
tidiness: `Ablation.violations()` raises on an unknown type, so an arm that invented a record for
its own convenience would make every episode it produced unmeasurable. Two places in this module
were written for exactly that reason as an outcome of a *later* read rather than a second event —
what a recovery turned into (§5.7) and what a stale row became (§11's rework) — and both are
joined offline from the `decision_id` the records already carry.

An absent module leaves **no key** in the payload. `"plan": null` and a sentence explaining that
planning is switched off are both hints about the experiment, and a model that can tell which arm
it is in makes the arm's effect unattributable. What `wo_planning` answers to is the v0.1 payload,
key for key, and the test asserts the set relation rather than being told it.

## Why the empty plan under `wo_planning` is not a fake

The memory is a ledger *about rows*, so with planning off the arm syncs it against a plan that
carries none. Promises, failures and unanswered questions are still booked — those are §5.3's own
lists and do not need a plan to exist. What cannot be booked is a suspended relation, because
"you moved X out of the way" is a fact about a `clear:` row and there is no row. So `wo_planning`
measures "no plan **and** the debts a plan is the only place to write them down", which is the
honest reading of removing the module. `Ablation.violations()` catches any attempt to dress that up
as something narrower.

## Two things this module does not do

* **it never chooses an action.** No `ready()` row is ranked, no candidate is named, no viewpoint is
  picked, and `current_subgoal` is filled from the *agent's own* last named row (`commit()` wrote
  it), not from the planner's preference — on round one it is `None`, which is what
  `DecisionContext.intent_note` says it is. The one decision-shaped record here is
  `recovery_action`, and it files the choice the model made, classified from the loop's own attempt
  history.
* **it does not need a model.** The dev runs of this phase spend nothing: `planning/policy.py`
  answers from the payload, so the loop, the view, the gate and the records are all measurable
  before any request is billed. That is the point of §9's *w/o VLM (… limited semantic baseline)*
  arm existing at all.

## One limitation, found while wiring and not papered over

`task_planner.observe_subgoal()` is the consumer P1 left unwritten — a look the plan asks for
because `PerceptVerifier.would_resolve()` named a view that could answer a predicate. It cannot be
fed through `TaskPlanner.refresh(new_obligations=...)`: an observe row is keyed to the predicate it
asks about, so its `row_key` is the placement row's `row_key`, and `refresh`'s de-duplication
(`task_planner.py:248`) discards it as already carried. Every unknown `placed:` predicate is already
a row, so every such look row is a duplicate by construction. Wiring it needs a keying decision in
§5.2's module, not a workaround here, so the hook stays unfed, this docstring carries the reason,
and P3 inherits it as a named gap rather than discovering it as a surprise.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..core.contracts import (
    Decision,
    DecisionContext,
    ExecutionFeedback,
    GoalSpec,
    TaskInput,
    WorldState,
)
from ..core.runtime import Runtime
# the schema generation this module's records belong to, read off the module that defines the
# vocabulary: `core/events.py` re-exports v0.1's "3", and filing a plan as "3" would pool it into
# the baseline log instead of the v0.2 one
from ..core.v02 import SCHEMA_VERSION, Ablation, TaskPlan, registry_sha256
from ..perception.arm import PerceptRuntime
from .subgoals import row_key
from .task_planner import Refresh, TaskPlanner
from .understanding import understand
from .view import V02DecisionContext, memory_view, plan_view, view_sha256
from .working_memory import (RECALL_BUDGET_CHARS, RENDER_BUDGET_CHARS, WorkingMemory, row_for)

#: the arms this phase can be checked against at zero spend (`full` here means "every planning
# module on", which is all of §9's list except the VLM claim — see `arm_coherence`)
PLANNING_ARMS = ("full", "wo_planning", "wo_working_memory", "wo_replanning")


class PlanningMixin:
    """`Runtime` with the plan offered, the memory kept, and both switchable.

    Construct it through `PlannedRuntime` / `PlannedPerceptRuntime` rather than by mixing it into an
    ad-hoc class: the two concrete arms declare their bases in one place, which is what keeps "which
    loop ran" answerable from a class name.
    """

    #: the round is built in the v0.2 rendering — the same class with two more fields
    context_class = V02DecisionContext
    #: whether this arm writes the `ablation` claim itself. `PerceptRuntime` already files it,
    # with the channel and the grounding table in its `notes`, so the camera arm of *this* module
    # must not file a second one; the privileged arm has no other place for the claim to go.
    files_ablation_claim = True
    #: the module whose rules read this channel's task text, named so the `task_understanding`
    # record attributes its parse to the code that produced it. `None` is `core/interpreter.py`,
    # the desktop `GoalSpec` parser; a text game installs `benchmark/obligations.py`, because a
    # record saying the desktop parser read a sentence it has no rule for is a record that
    # misattributes its own evidence (§12.3: never attribute a measurement to the wrong instrument).
    understanding_parser: Any = None
    #: the module that answers "which rows does this task oblige", handed to `TaskPlanner` when the
    # runner does not pass one. `None` is `subgoals.derive` — placements read off a `GoalSpec` and a
    # geometry instrument — which is the desktop channel's answer and nothing else's. It is a class
    # attribute for the same reason `understanding_parser` is one: it is a fact about the channel
    # the loop runs on, known by the class that declares its backend, and not a choice a runner
    # should have to remember to make (or it silently plans placements for a text game).
    task_planner_derivation: Any = None
    # class-level defaults, so an instance that never installed the arm is the base loop
    planner_engine: Optional[TaskPlanner] = None
    memory: Optional[WorkingMemory] = None
    plan: Optional[TaskPlan] = None
    ablation: Optional[Ablation] = None

    def __init__(self, *args, task_planner: Optional[TaskPlanner] = None,
                 memory_render_budget: int = RENDER_BUDGET_CHARS,
                 memory_recall_budget: int = RECALL_BUDGET_CHARS, **kw):
        self._task_planner = task_planner
        self.memory_render_budget = int(memory_render_budget)
        self.memory_recall_budget = int(memory_recall_budget)
        super().__init__(*args, **kw)
        # read off the *class*, not the instance: a function-valued class attribute fetched through
        # `self` is a bound method, and `TaskPlanner` would then call the derivation rule with this
        # runtime as its first argument. The lookup below is what keeps the hook a plain function.
        derivation = getattr(type(self), "task_planner_derivation", None)
        self.planner_engine = self._task_planner or TaskPlanner(config=self.config,
                                                                derivation=derivation)

    # ------------------------------------------------------------------ arm state ----
    def _begin_episode(self) -> None:
        """Fresh ledger, no plan yet, no memories. The loop calls this before it logs anything."""
        super()._begin_episode()
        if self.ablation is not None and self.files_ablation_claim:
            # The claim is filed with the disclosure the §9 table cannot carry: a privileged
            # snapshot consults no vision model, so this episode is the *planning* contrast at
            # this setting and not the full system, whatever the condition word says. Records are
            # never rendered into a payload, so saying it here informs the reader without giving
            # the decision maker a hint about which arm it is in.
            self.store.log_record(
                "ablation", self.ablation.model_copy(update={
                    "episode_id": self.episode_id,
                    "registry_sha256": self.ablation.registry_sha256 or registry_sha256(),
                    "notes": self._ablation_note()}))
        self.memory = WorkingMemory(episode_id=self.episode_id,
                                    render_budget=self.memory_render_budget,
                                    recall_budget=self.memory_recall_budget)
        self.plan = None
        self.current_round = 0
        self.decision_by_id: dict[str, Decision] = {}
        self.world_by_ref: dict[str, WorldState] = {}
        self.rounds: list[dict[str, Any]] = []
        self.plan_events = 0
        self.revision_events = 0
        self.recovery_events = 0
        self._deviations: list[dict[str, Any]] = []

    def _on(self, module: str) -> bool:
        """Is this capability on for this episode?

        `ablation is None` means no arm claim was made, and an unclaimed episode runs everything:
        the v0.1 regression depends on that, and a missing claim must not silently switch a module
        off. Where an arm *is* named, the registry entry is the only source of the answer.
        """
        ablation = getattr(self, "ablation", None)
        return True if ablation is None else bool(ablation.enabled(module))

    @property
    def _arm(self) -> str:
        return str(getattr(getattr(self, "ablation", None), "condition", "unarmed"))

    def _ablation_note(self) -> str:
        """The disclosure the §9 table cannot carry, in this channel's own words.

        A method rather than a sentence because the sentence is a claim about the world the arm ran
        on: "a privileged world" is true of a simulator snapshot that consults no vision model and
        false of a text game whose state *is* the public response. A second channel that inherits the
        note would file a record describing someone else's experiment.
        """
        return (f"planning arm on a privileged world: "
                f"modules off {list(self.ablation.modules_off) or 'none'}; no "
                f"vision model was consulted, so this row is the planning "
                f"contrast at {self.ablation.condition!r} and not §9's full "
                f"system")

    # ------------------------------------------------------- §7 step 2: the offer ----
    def _build_context(self, task, goal, world, progress, ledger) -> DecisionContext:
        ctx = super()._build_context(task, goal, world, progress, ledger)
        if self.planner_engine is None:
            return ctx
        self.current_round = int(ctx.round_index)
        verifier = self._verifier(world)
        refresh = self._offer(task, goal, world, progress, verifier, ctx.round_index)
        plan = refresh.plan
        state = rendered = None
        if self.memory is not None and self._on("working_memory"):
            state = self.memory.sync(plan, world, verifier=verifier,
                                     round_index=ctx.round_index, revision=refresh)
        # §5.4's offer goes out before the ledger is rendered, so a recalled row is inside the same
        # character budget as a debt line rather than appended after it. `state` is None when the
        # ledger is ablated, and the hook still runs: retrieval happened, there is nowhere to put it.
        memories = self._offer_memories(state, world, ctx.round_index)
        if state is not None:
            rendered = self.memory.render(state, recalled=memories)
            # §5.3's own field, and only the rows that survived the budget: a memory the model was
            # never shown cannot be counted as one it was offered, and the drop is reported by
            # `render()`'s counts rather than by an entry that quietly disappears.
            state.recalled = list(rendered.get("recalled") or [])

        updates: dict[str, Any] = {}
        if self._on("planning"):
            updates["planning_view"] = plan_view(
                plan, ready=self.planner_engine.ready(plan), changed=_changed(refresh),
                violations=refresh.violations,
                revision_note=(plan.revisions[-1].rationale if plan.revisions else ""))
            # `current_subgoal` is the agent's own word even though this line looks like the
            # planner's: `sync()` never writes it, only `commit()` does, so on round one it is
            # None and the payload's `intent_note` stays true of it.
            updates["current_subgoal"] = (state.current_subgoal_id if state is not None else None)
            updates["todo_summary"] = plan.summary
        if rendered is not None:
            updates["memory_view"] = memory_view(rendered)
        if updates:
            ctx = ctx.model_copy(update=updates)
        self._note_world(world)
        self._file_round(ctx, world, state, plan, rendered)
        return ctx

    def _offer(self, task: TaskInput, goal: GoalSpec, world: WorldState, progress, verifier,
               round_index: int) -> Refresh:
        """Author v1 once; after that, re-read the world at the latest evidence.

        `replanning` decides whether the *work graph* may change. With it off the same `refresh()`
        still runs — rows keep being answered and a regression still reopens — but `task`/`goal` are
        withheld, so `derive()` is never run again and no new obligation can enter. That is the
        difference §9 is asking about, and it is expressed by not passing an argument rather than by
        a branch inside the planner.

        The two early returns are this function's whole gate: everything below the first one can only
        run with planning on, so the records it files need no second check. That is why
        `test_planning_off_files_no_planning_record` counts event types in a log rather than reading
        this function — the invariant belongs to the record, not to the code shape.
        """
        engine = self.planner_engine
        if not self._on("planning"):
            # Planning off is not "a plan that is not shown": the module is off, so no graph is
            # authored at all and the memory is synced against a ledger with no rows in it. That is
            # the honest shape of the arm — §5.3's promises, failures and unknowns still get booked,
            # and the debts a plan is the only place to write down (§8's temporary move) are
            # measurably absent, which is what the contrast is for.
            if self.plan is None:
                self.plan = _planless(task, goal, world)
            return Refresh(plan=self.plan)
        if self.plan is None:
            self.plan = engine.initial(task, goal, world, verifier=verifier,
                                       planner=self._placement_instrument(), progress=progress)
            self._file_record("task_understanding",
                              understand(task, goal, world, progress=progress,
                                         planner=self._placement_instrument(),
                                         parser=self.understanding_parser,
                                         extra_problems=self._understanding_problems(
                                             task, goal, world, progress)),
                              state_version=world.state_version,
                              observation_ref=world.observation_ref)
            self._file("plan", {"trigger": "initial",
                                "view": plan_view(self.plan, ready=engine.ready(self.plan))})
            return Refresh(plan=self.plan)

        rederive = self._on("replanning")
        before = self.plan
        refresh = engine.refresh(
            before, world, verifier=verifier, planner=self._placement_instrument(),
            **({"task": task, "goal": goal, "progress": progress} if rederive else {}),
            evidence_refs=[world.observation_ref or ""])
        self.plan = refresh.plan
        if refresh.violations:
            # a violated edge is the one plan fact that has to survive into the record even when
            # the ledger is off, because §11 counts it per episode rather than per round
            self._deviations.append({"round_index": round_index,
                                     "violated": list(refresh.violations),
                                     "plan_version": refresh.plan.version,
                                     "state_version": world.state_version})
        if refresh.version_bumped:
            revision = refresh.plan.revisions[-1]
            self._file("plan_revision", {
                "from_version": before.version, "to_version": refresh.plan.version,
                "replanning_enabled": rederive, "trigger": revision.trigger,
                "rationale": revision.rationale,
                "view": plan_view(refresh.plan, ready=engine.ready(refresh.plan),
                                  changed=_changed(refresh), violations=refresh.violations)})
        return refresh

    def _placement_instrument(self):
        """The executor's own generator/re-checker, or None on a backend without one."""
        return getattr(self, "placement_planner", None)

    def _understanding_problems(self, task: TaskInput, goal: GoalSpec, world: WorldState,
                                progress) -> list[str]:
        """Problems in this task text that only the channel's own reader can name.

        Empty on the desktop channel: there `understand()` reads the failed clauses off the
        `GoalSpec` and `progress` rows it was given, so an empty list adds nothing to the record. It
        exists for the second channel's reader — a sentence a text game's rules did not match is not
        a *placement* the snapshot failed to measure, so it has no route into
        `implicit_requirements` and arrives here instead, already named by the module that found it.
        """
        return []

    def _offer_memories(self, state, world: WorldState,
                        round_index: int) -> Optional[list[tuple[Any, str]]]:
        """§5.4's hook, empty on a loop with no episodic arm installed.

        `None` and `[]` are different answers and the difference is a measurement: `None` means
        no arm looked, so the payload carries no `recalled` key at all, while `[]` means an arm
        queried the store and nothing in it was relevant, which is §11's *retrieval relevance*
        denominator and must be visible as an empty list. The planning mixin has no store to
        query, so it returns `None`; `episodic/arm.py:EpisodicMixin` overrides this and the
        `wo_episodic_memory` gate is expressed by returning `None` there too.

        The rows come back paired with the line each was rendered to, because the line needs the
        stored record (`EpisodicExperience.as_reference`) and the budget that decides whether it
        is shown belongs to the ledger. Handing over both keeps each module's own knowledge: this
        one spends the characters, that one decides what is worth a sentence.
        """
        return None

    # ------------------------------------------------ §7 step 6: record the intent ----
    def _validate_decision(self, decision: Decision, ctx: DecisionContext,
                           world: WorldState) -> tuple[bool, list[str]]:
        ok, errs = super()._validate_decision(decision, ctx, world)
        if self.planner_engine is None or self.memory is None:
            return ok, errs
        self.decision_by_id[str(decision.decision_id)] = decision
        if not ok:
            # A refusal never became an action, so nothing is promised by it. The *attempt* is
            # still booked — by `_record_feedback`, which the loop calls next with the rejection
            # feedback — and booking the promise too would let a decision the executor never saw
            # hold an open debt to the end of the episode (§12.3: 以实际动作和最终状态为准).
            return ok, errs
        self._note_world(world)
        if self._on("planning") and decision.action == "execute":
            self.plan, hit = self.planner_engine.mark_attempt(
                self.plan, decision,
                subgoal=(decision.execute.subgoal if decision.execute else None))
            if hit is None:
                self._deviations.append({
                    "decision_id": decision.decision_id, "round_index": ctx.round_index,
                    "unbound": f"{(decision.execute.skill if decision.execute else decision.action)}"
                               f" names no open row of plan v{self.plan.version}",
                    "named_subgoal": (decision.execute.subgoal if decision.execute else None),
                    "state_version": world.state_version})
        if self._on("working_memory"):
            self.memory.commit(decision, self.plan, world)
        if self._on("replanning"):
            self._file_recovery(decision, ctx, world)
        return ok, errs

    def _file_recovery(self, decision: Decision, ctx: DecisionContext,
                       world: WorldState) -> None:
        """§5.7 as data: the model chose a recovery, and the row it chose is named.

        Filed for the accepted decision before the executor has answered, because the record is
        about the *choice*. What the choice produced is not written here: it is joined offline from
        the `decision_id`, which the execution feedback already carries, and grading a recovery in
        the same breath as announcing it is how a system starts scoring its own intentions (§12.3).

        "Was this a recovery at all" is answered from the loop's own bounded attempt history, not
        from the memory, so the record exists in `wo_working_memory` too. A forward step the agent
        has never attempted is not a recovery; a repeat against the same failure is, and the three
        names are §5.7's own list.
        """
        ex = decision.execute
        if ex is None:
            return
        row = row_for(self.plan, str(ex.subgoal or ""))
        args = dict(ex.args or {})
        if row is not None and row.kind == "recover":
            choice, family = "restore", "temporary-move-restore"
        elif ex.skill == "observe":
            prior = [a for a in ctx.attempts if str(a.skill) == "observe"]
            if not prior:
                return
            choice, family = "re_observe", "look-for-evidence"
        else:
            same = [a for a in ctx.attempts
                    if str(a.skill) == str(ex.skill) and a.entity_id == args.get("object_id")
                    and (a.target_id or None) in (None, args.get("target_id"))]
            if not same:
                return
            repeated = any((a.target_id or None) == (args.get("target_id") or None)
                           and (a.candidate_id or None) == (ex.candidate_id or None) for a in same)
            choice = "retry" if repeated else "change_candidate"
            family = f"follow-up:{choice}"
        self._file("recovery_action", {
            "decision_id": decision.decision_id, "round_index": ctx.round_index,
            "skill": ex.skill, "args": args,
            "row_key": (row_key(row) if row is not None else ""),
            "subgoal_id": (row.subgoal_id if row is not None else None),
            "row_kind": (row.kind if row is not None else None),
            "plan_version": self.plan.version if self.plan is not None else None,
            "state_version": world.state_version, "observation_ref": world.observation_ref,
            "choice": choice, "recovery_kind": family,
            "expected_effect": decision.expected_effect})
        self.recovery_events += 1

    # -------------------------------------------------- §7 steps 9-10: what happened ----
    def _build_feedback(self, decision, ctx, call, result, pre_world, post_world, goal):
        """The feedback itself is untouched; both snapshots are remembered.

        `_record_feedback` is handed only the feedback, and a debt settled against the *pre*-world
        would be discharged by the evidence that preceded the action that owed it."""
        fb = super()._build_feedback(decision, ctx, call, result, pre_world, post_world, goal)
        self._note_world(pre_world)
        self._note_world(post_world)
        return fb

    def _record_feedback(self, fb: ExecutionFeedback) -> None:
        super()._record_feedback(fb)
        if self.planner_engine is None or self.memory is None:
            return
        world = self._world_for(fb.post_observation_ref)
        if world is None:
            # Reaching this needs a feedback that names a snapshot the arm never measured. The loop
            # builds exactly one of those — the model-error feedback, whose two refs are the
            # round's own world, which `_build_context` did register — so a miss here is a defect
            # in this module, and it is recorded as a deviation rather than settled against a world
            # that may be stale.
            self._deviations.append({
                "round_index": self.current_round,
                "unsettled": f"{fb.status} feedback names no snapshot this arm measured "
                             f"({fb.post_observation_ref})"})
            return
        if self._on("working_memory"):
            decision = self.decision_by_id.get(str(fb.decision_id or ""))
            self.memory.settle(decision, fb, world, plan=self.plan,
                               verifier=self._verifier(world), round_index=self.current_round)

    # ---------------------------------------------------------------- §11's audit ----
    def _finalize(self, task, ledger, status, failure, note, mode):
        """One `obligation_check` per episode that ran with a memory, filed before `episode_end`."""
        if self.planner_engine is None:
            return super()._finalize(task, ledger, status, failure, note, mode)
        if self.terminal_world is None and getattr(self, "goal", None) is not None:
            # The base loop takes this measurement itself when it is missing; taking it here costs
            # nothing extra (the perception arm's `_finalize` then finds it set and spends no
            # second look) and is what lets the audit answer its debts against the *terminal*
            # snapshot rather than against the last one a decision happened to look at.
            self._finish_check(self.goal)
        world = self.terminal_world or self.observe()
        audit = {"rounds": len(self.rounds), "plan_events": self.plan_events,
                 "revision_events": self.revision_events,
                 "recovery_events": self.recovery_events,
                 "sections_seen": sorted({key for row in self.rounds for key in row["sections"]}),
                 "deviations": list(self._deviations)}
        if self.memory is not None and self._on("working_memory") and self.plan is not None:
            self.memory.sync(self.plan, world, verifier=self._verifier(world),
                             round_index=ledger.decision_rounds)
            check = self.memory.obligation_check(
                status=str(getattr(status, "value", status)), plan=self.plan,
                final_round=ledger.decision_rounds)
            self._file("obligation_check", {**check, **audit,
                                            "state_version": world.state_version,
                                            "observation_ref": world.observation_ref,
                                            "terminal_status": str(getattr(status, "value", status)),
                                            "decision_rounds": ledger.decision_rounds,
                                            "skill_calls": ledger.skill_calls})
        elif self.plan is not None and self._on("planning"):
            # `wo_working_memory` still owes §11's obligation row a denominator, and the plan is the
            # only ledger left. It is filed as the plan's own terminal reading and labelled as not a
            # memory audit, because the two count different things and a reader must be able to tell.
            self._file("plan", {"trigger": "terminal", "memory_ablated": True, **audit,
                                "open_obligations": self.plan.open_obligation_count,
                                "view": plan_view(self.plan, ready=self.planner_engine.ready(
                                    self.plan))})
        return super()._finalize(task, ledger, status, failure, note, mode)

    # ------------------------------------------------------------------ records ----
    def _note_world(self, world: WorldState) -> None:
        self.world_by_ref[str(world.observation_ref)] = world
        for key in list(self.world_by_ref)[:-24]:
            self.world_by_ref.pop(key, None)

    def _world_for(self, observation_ref: Optional[str]) -> Optional[WorldState]:
        return self.world_by_ref.get(str(observation_ref or ""))

    def _file_round(self, ctx: DecisionContext, world: WorldState, state, plan: TaskPlan,
                    rendered: Optional[dict]) -> None:
        """The round's footprint: which sections were on the page, and how big the page was."""
        payload = ctx.model_payload()
        view_bytes = len(json.dumps(payload, ensure_ascii=False))
        sections = [key for key in ("plan", "working_memory") if key in payload]
        self.rounds.append({
            "round_index": ctx.round_index, "state_version": world.state_version,
            "observation_ref": world.observation_ref, "sections": sections,
            "view_sha256": view_sha256(), "view_bytes": view_bytes,
            "plan_version": plan.version,
            "deviations": [d for d in self._deviations if d.get("round_index") == ctx.round_index]})
        if state is not None and rendered is not None:
            # `round_index` and `plan_version` are fields of the record itself; passing them again
            # as payload keywords is a duplicate argument, and the store would be right to refuse.
            self._file_record("working_memory", state,
                              state_version=world.state_version,
                              observation_ref=world.observation_ref,
                              sections=sections, view_bytes=view_bytes,
                              view_sha256=view_sha256(), render=rendered["counts"],
                              lines_dropped=rendered["lines_dropped"],
                              deviations=[d for d in self._deviations
                                          if d.get("round_index") == ctx.round_index])

    def _file(self, event_type: str, payload: dict) -> None:
        """A v0.2 record, in schema 4, tagged with the arm that produced it."""
        self.store.log(event_type, contract_version=SCHEMA_VERSION, arm=self._arm, **payload)
        if event_type == "plan":
            self.plan_events += 1
        elif event_type == "plan_revision":
            self.revision_events += 1

    def _file_record(self, event_type: str, record, **payload) -> None:
        """A record, filed at the version the record itself declares.

        `log_record` reads the envelope version off the object and passes it to `log` as a named
        argument, so a `contract_version=` here would be a duplicate keyword — the store refuses a
        version that contradicts the record, which is the whole reason this entry point exists.
        """
        self.store.log_record(event_type, record, arm=self._arm, **payload)


def _changed(refresh: Refresh) -> dict[str, Any]:
    """What this round's reading did to the plan, in the four words the plan uses."""
    return {"added": list(refresh.added), "dropped": list(refresh.dropped),
            "reopened": list(refresh.reopened), "relabelled": list(refresh.relabelled),
            "rewired": list(refresh.rewired)}


def _planless(task: TaskInput, goal: GoalSpec, world: WorldState) -> TaskPlan:
    """What `wo_planning` carries: a plan object with no rows in it, and the reason in its provenance.

    It is empty because the module that fills it is switched off, not because the task has no
    obligations, and the distinction is why this is a real `TaskPlan` rather than a `None`: the
    memory is synced against it, so the arm measures exactly "the debts a plan is the only place to
    write down" and nothing else. `summary` stays the empty string rather than a sentence about the
    experiment — that field reaches the payload through `todo_summary` on the armed path, and a
    decision maker that could read "planning is off" here would know which arm it was in.
    """
    return TaskPlan(goal_ref=goal.goal_id, task_utterance=task.utterance, version=1,
                    subgoals=[], dependencies=[], authored_by="rule",
                    based_on_state_version=world.state_version,
                    based_on_observation_ref=world.observation_ref,
                    provenance={"author": "planning/arm.py:_planless",
                                "planning_ablated": True,
                                "why_empty": "the planning module is off for this episode, so no "
                                             "work graph was authored; §5.3's promises, failures "
                                             "and unknowns are still booked and no row-bound "
                                             "suspended relation can be"})


class PlannedRuntime(PlanningMixin, Runtime):
    """The desktop loop, privileged world, with the planning arm installed.

    It takes the `ablation` claim itself because `Runtime.__init__` has no such keyword: the base
    loop is the v0.1 loop, and an unclaimed episode is what it has always meant. Setting the claim
    *after* `super().__init__()` is the whole of the difference from the camera arm, whose base
    already accepts the keyword and checks it against the channel.
    """

    def __init__(self, *args, ablation: Optional[Ablation] = None, **kw):
        super().__init__(*args, **kw)
        self.ablation = ablation


class PlannedPerceptRuntime(PlanningMixin, PerceptRuntime):
    """The camera loop with the planning arm installed.

    No `__init__` of its own: `PerceptRuntime.__init__` already takes `ablation`, runs
    `arm_coherence` against the perceiver's channel and refuses a claim the channel cannot carry,
    and files the `ablation` record with the channel, the views and the grounding table in it —
    which is why this arm does not file the claim again.
    """

    files_ablation_claim = False


def build_planning_arm(*, case, scene, executor, store, run_dir: str, episode_id: str,
                       budgets, perceive: str = "privileged",
                       ablation: Optional[Ablation] = None,
                       perceiver=None, gmap=None, environment=None,
                       task_planner: Optional[TaskPlanner] = None,
                       memory_render_budget: int = RENDER_BUDGET_CHARS,
                       memory_recall_budget: int = RECALL_BUDGET_CHARS) -> Runtime:
    """The one place a planning arm is assembled, so a runner cannot build a half-switched loop.

    `perceive='privileged'` gives `PlannedRuntime`: the world the simulator reports, the zero-spend
    path every planning claim is checked on first. The camera path needs a `perceiver` and a
    `gmap`, and asks for them rather than trying to make them — a perceiver is built by
    `perception/arm.py:build_arm` out of a catalogue and a grounding map the *caller* owns (see
    `evaluation/run.py`, which passes `cell_catalog(scene.trays.values())` and
    `GroundingMap.from_objects(case.objects)`), and this function has no honest way to reconstruct
    either from what it was handed. Re-basing a finished `PerceptRuntime` by reading attributes off
    it would be the same guess wearing a different name.

    Nothing here pre-checks `arm_coherence`. On the camera path the claim is checked where the
    channel is known — `PerceptRuntime.__init__` raises with all the reasons at once, before the
    episode starts — and on the privileged path there is no channel to be coherent with: the
    condition word is about the *planning* modules, and the record says so in its `notes`. That
    asymmetry is P2's open measurement question, recorded for the phase log rather than resolved by
    a switch: a §9 `full` episode that consults no vision model is not the full system, so P5 has
    to budget the `vlm` channel to make the word mean what §9 says.
    """
    if perceive == "privileged":
        return PlannedRuntime(scene, executor, run_dir, budgets, episode_id, config=case.verify,
                              environment=environment, store=store, ablation=ablation,
                              task_planner=task_planner,
                              memory_render_budget=memory_render_budget,
                              memory_recall_budget=memory_recall_budget)
    if perceiver is None or gmap is None:
        raise ValueError(
            f"the camera planning arm needs both `perceiver` and `gmap` (got "
            f"perceiver={type(perceiver).__name__}, gmap={type(gmap).__name__}): build them with "
            f"`perception/arm.py:build_arm`'s inputs and pass them in, or run the planning "
            f"contrast on `perceive='privileged'`, which spends nothing")
    return PlannedPerceptRuntime(scene, executor, run_dir, budgets, episode_id,
                                 perceiver=perceiver, gmap=gmap, ablation=ablation,
                                 config=case.verify, environment=environment, store=store,
                                 task_planner=task_planner,
                                 memory_render_budget=memory_render_budget,
                                 memory_recall_budget=memory_recall_budget)


__all__ = ["PLANNING_ARMS", "PlannedPerceptRuntime", "PlannedRuntime", "PlanningMixin",
           "build_planning_arm"]
