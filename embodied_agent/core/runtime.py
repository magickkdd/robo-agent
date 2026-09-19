"""Model-driven decision Runtime (SPEC 6).

One loop, one shape of work: observe -> build context -> ask for one Decision ->
validate it against the newest snapshot -> execute at most one skill -> feed the
measured result back. The Runtime holds **no** strategy:

* it does not recover, replan, swap candidates or add actions on the model's
  behalf;
* it does not read `EvalSpec` or the environment event configuration;
* it only refuses with structured reasons, reports what it measured, and stops
  when a budget or a local guard trips (SPEC 6.1).

Modes A (one-shot plan) and B (continuous decision) differ only in who produces
the `Decision`. Both enter through `Runtime.run_episode`, are validated by the
same `PlanValidator`, executed by the same `SkillExecutor` and concluded by the
same `finish` check on the same terminal snapshot (SPEC 11.2, 12.1.4).
"""
from __future__ import annotations

import os
import time

from .contracts import (
    AttemptRecord,
    Budgets,
    Decision,
    DecisionContext,
    DecisionSchemaError,
    EpisodeResult,
    ExecutionFeedback,
    FailureCode,
    GoalSpec,
    ObservationRecord,
    Plan,
    PlanStep,
    PredicateVerdict,
    SkillCall,
    SkillResult,
    SkillStatus,
    TaskInput,
    TerminalSnapshot,
    TerminalStatus,
    VerificationReport,
    VerifyConfig,
    WorldState,
)
from .events import EpisodeStore
from .planner import PlanValidator
from .placement_planner import PlacementPlanner
from .scene import PhysicsScene
from .skills import SkillExecutor, SkillRegistry
from .verify import RuntimeVerifier, build_world_state, diff_states

MAX_MODEL_ERRORS = 3
MAX_FALSE_FINISHES = 3
# how much of the episode a round is shown: the two histories the decision context
# carries, bounded so a long-horizon task cannot grow its own prompt (SPEC 5.2
# 有界历史). `model_payload` thins the feedback further to `feedback_depth`.
HISTORY_CAP = 12
# what a model-facing source is expected to report cumulatively; the runtime
# bills an episode the delta, so a shared adapter across a batch cannot charge
# one episode for another episode's traffic (SPEC 6.2, 11.4)
SOURCE_COUNTERS = ("prompt_tokens", "completion_tokens", "api_errors", "transport_retries",
                   "format_repairs")


class BudgetLedger:
    """Separate counters per SPEC 6.2: decision rounds, HTTP requests, skill
    calls, per-skill simulated seconds and wall clock are not interchangeable.

    `can_*` answers "may I *start* one more?", so a budget of N permits exactly
    N uses and the (N+1)-th check returns False."""

    def __init__(self, budgets: Budgets):
        self.b = budgets
        self.decision_rounds = 0
        self.http_requests = 0
        self.skill_calls = 0
        self.sim_seconds_by_skill: dict[str, float] = {}
        self.rejected_decisions = 0
        self.semantic_repairs = 0
        self.false_finish_attempts = 0
        self.identical_invalid = 0
        # `identical_invalid` is the length of the *current* identical-attempt run,
        # which is what the SPEC 6.2 budget is a bound on. It says nothing about
        # the rest of the episode: an episode that repeated four times and then
        # moved on ends with a run of 1. This is the total, so the two numbers a
        # report shows for one episode can actually be compared.
        self.identical_repeats_total = 0
        self.model_errors = 0
        self.t_start_wall = time.time()

    # --- pre-flight checks ---
    def can_decide(self) -> bool:
        return self.decision_rounds < self.b.max_decision_rounds

    def can_call_skill(self) -> bool:
        return self.skill_calls < self.b.max_skill_calls

    def wall_remaining(self) -> float:
        return self.b.wall_clock_s - (time.time() - self.t_start_wall)

    # --- charges ---
    def charge_decision(self):
        self.decision_rounds += 1

    def charge_requests(self, n: int):
        self.http_requests += max(0, int(n))

    def charge_skill(self, skill: str, sim_seconds: float):
        self.skill_calls += 1
        self.sim_seconds_by_skill[skill] = round(
            self.sim_seconds_by_skill.get(skill, 0.0) + float(sim_seconds), 3)

    def remaining_payload(self) -> dict:
        return {
            "decision_rounds_used": self.decision_rounds,
            "decision_rounds_total": self.b.max_decision_rounds,
            "http_requests_used": self.http_requests,
            "http_requests_total": self.b.max_http_requests,
            "skill_calls_used": self.skill_calls,
            "skill_calls_total": self.b.max_skill_calls,
            "per_skill_sim_timeout_s": self.b.per_skill_sim_timeout_s,
            "wall_clock_remaining_s": round(self.wall_remaining(), 1),
            "identical_invalid_remaining": max(
                0, self.b.max_identical_invalid_attempts - self.identical_invalid),
            "semantic_repairs_remaining": max(
                0, self.b.max_semantic_repairs - self.semantic_repairs),
        }


class Runtime:
    def __init__(self, scene: PhysicsScene, executor: SkillExecutor, run_dir: str,
                 budgets: Budgets, episode_id: str, *, config: VerifyConfig | None = None,
                 environment=None, store: EpisodeStore | None = None):
        self.scene = scene
        self.executor = executor
        self.episode_id = episode_id
        self.budgets = budgets
        self.config = config or VerifyConfig()
        self.environment = environment
        os.makedirs(run_dir, exist_ok=True)
        self.store = store or EpisodeStore(run_dir, episode_id)
        self.store._sim_time_fn = lambda: self.scene.sim_time
        self.placement_planner: PlacementPlanner = executor.placement_planner
        self.verbose = False

        self._obs_seq = 0
        self.state_version = 0
        self.world: WorldState | None = None
        self.observations: list[ObservationRecord] = []
        self.feedback_history: list[ExecutionFeedback] = []
        self.attempts: list[AttemptRecord] = []
        self.terminal_snapshot: TerminalSnapshot | None = None
        self.terminal_world: WorldState | None = None
        self.goal: GoalSpec | None = None
        self.slot_resolution_fallbacks = 0
        self.usage = {k: 0 for k in SOURCE_COUNTERS}
        self._usage_base = {k: 0 for k in SOURCE_COUNTERS}
        self._usage_offset = {k: 0 for k in SOURCE_COUNTERS}
        self._last_signature: tuple | None = None
        self._last_signature_fp: tuple | None = None

    # ---------- observation channel ----------
    def observe(self) -> WorldState:
        """One measurement, one unique ref, one monotonic version step. A new
        version is not a claim that the world changed; `diff_states` says that
        (SPEC 5.1)."""
        self._obs_seq += 1
        self.state_version = self._obs_seq
        w = build_world_state(self.scene, self.state_version, f"obs_{self._obs_seq:04d}", self.config)
        self.world = w
        rec = ObservationRecord(
            observation_ref=w.observation_ref, state_version=w.state_version, sim_time=w.sim_time,
            wall_time=w.wall_time, source=w.source, held_object=w.held_object, n_entities=len(w.entities))
        self.observations.append(rec)
        self.store.log("observation", observation=rec.model_dump(mode="json"))
        return w

    # ---------- request accounting ----------
    @staticmethod
    def _requests_used(source, before) -> int:
        """Retries must not hide inside the adapter (SPEC 6.2): a source that
        reports a cumulative counter is charged its delta, anything else costs
        one request per call."""
        if before is None:
            return 1
        return max(0, int(getattr(source, "http_requests", before)) - int(before))

    # ---------- episode ----------
    def run_episode(self, task: TaskInput, goal: GoalSpec, source, *, mode: str = "B",
                    prologue: dict | None = None) -> EpisodeResult:
        """Run one episode.

        `prologue` carries what already happened *before* this loop for this
        logical execution: `wall_start` (when task processing began, so the model
        requests that parsed the goal and drew up the plan are inside the wall
        clock, SPEC 6.2) and `http_requests` (those requests, measured as deltas
        by the runner — never by reading a shared adapter's absolute counter,
        which would bill one episode for the whole batch).

        The provider counters (`prompt_tokens`, `api_errors`, ...) may be charged
        the same way. They are an *offset* rather than a pre-seeded `usage` value
        because `usage` tracks the maximum cumulative delta of the decision
        source: seeding it with the goal parse would let a later round overwrite
        the total instead of adding to it. A shared goal parse is billed to every
        mode identically (SPEC 11.2), and the wall clock is shifted back by its
        measured duration so no mode gets a cheaper start than the one that paid
        for the parse.
        """
        ledger = BudgetLedger(self.budgets)
        prologue = prologue or {}
        if prologue.get("wall_start"):
            ledger.t_start_wall = float(prologue["wall_start"])
        ledger.charge_requests(int(prologue.get("http_requests") or 0))
        self._usage_base = {k: int(getattr(source, k, 0) or 0) for k in self.usage}
        self._usage_offset = {k: int(prologue.get(k) or 0) for k in SOURCE_COUNTERS}
        self.goal = goal
        self.store.log("episode_start", task_id=task.task_id, episode_id=self.episode_id, mode=mode,
                       planner=getattr(source, "provider", type(source).__name__),
                       prologue={k: v for k, v in prologue.items() if k != "wall_start"},
                       budgets=self.budgets.model_dump(mode="json"),
                       verify_config=self.config.model_dump(mode="json"))
        if self.environment is not None:
            self.environment.bind_store(self.store)

        if not goal.well_formed:
            self.store.log("needs_clarification", reason=goal.clarification_request)
            return self._finalize(task, ledger, TerminalStatus.needs_clarification,
                                  FailureCode.AMBIGUOUS_TASK, goal.clarification_request or "", mode)

        last_failure: FailureCode | None = None
        last_note = ""

        while True:
            # ---- 1. budgets are checked before the operation is started ----
            if not ledger.can_decide():
                return self._finalize(task, ledger, TerminalStatus.failed, FailureCode.BUDGET_EXHAUSTED,
                                      "decision round budget", mode)
            if ledger.wall_remaining() <= 0:
                return self._finalize(task, ledger, TerminalStatus.failed, FailureCode.TIMEOUT,
                                      "wall clock budget", mode)

            # ---- 2. observe, and recompute every goal from current evidence ----
            world = self.observe()
            verifier = RuntimeVerifier(world, self.config)
            progress = verifier.progress(goal)
            ledger.charge_decision()
            ctx = self._build_context(task, goal, world, progress, ledger)
            self.store.log("decision_context", context_id=ctx.context_id, round_index=ctx.round_index,
                           state_version=ctx.state_version, observation_ref=ctx.observation_ref,
                           goal_version=ctx.goal_version,
                           progress=[p.model_dump(mode="json") for p in progress],
                           candidates=[c.summary() for c in ctx.candidates],
                           candidates_truncated=ctx.candidates_truncated,
                           attempts=[a.model_dump(mode="json") for a in ctx.attempts],
                           budget=ctx.budget, feedbacks_included=len(ctx.recent_feedbacks))
            if self.verbose:
                print(f"── round {ctx.round_index} v{world.state_version} held={world.held_object} "
                      f"progress={[(p.entity_id, p.value.value) for p in progress]} ──", flush=True)

            # ---- 3. one decision ----
            req_before = getattr(source, "http_requests", None)
            decision, schema_reasons = None, []
            model_error: tuple[FailureCode, str] | None = None
            try:
                decision = source.decide(ctx)
            except DecisionSchemaError as e:
                # a payload that is not a Decision is a rejection the model must
                # repair, not a transport failure (SPEC 6.1); the reasons are the
                # field-level errors, unedited
                schema_reasons = list(e.reasons)
            except Exception as e:  # noqa: BLE001 - a model error is an outcome, not a crash
                model_error = (FailureCode.MODEL_ERROR, f"{type(e).__name__}: {e}")
            ledger.charge_requests(self._requests_used(source, req_before))
            self._accumulate_usage(source)

            if model_error is not None:
                last_failure, last_note = model_error
                self._record_feedback(ExecutionFeedback(
                    context_id=ctx.context_id, executed=False, status="model_error",
                    failure_code=last_failure.value,
                    pre_observation_ref=world.observation_ref, post_observation_ref=world.observation_ref,
                    pre_state_version=world.state_version, post_state_version=world.state_version,
                    rejection_reasons=[last_note, "no action was executed"],
                    held_object_after=world.held_object))
                ledger.model_errors += 1
                if ledger.model_errors >= MAX_MODEL_ERRORS:
                    return self._finalize(task, ledger, TerminalStatus.failed, last_failure,
                                          "repeated model errors", mode)
                continue

            if decision is not None and isinstance(decision, Decision):
                self.store.log("decision", **decision.model_dump(mode="json"))
                ok, errs = self._validate_decision(decision, ctx, world)
            else:
                ok, errs = False, schema_reasons or [
                    f"decision payload is {type(decision).__name__}, not a Decision"]

            # ---- 4. rejection is feedback, bounded, and never a silent fix ----
            if not ok:
                last_failure = FailureCode.INVALID_DECISION
                ledger.rejected_decisions += 1
                ledger.semantic_repairs += 1
                ex = getattr(decision, "execute", None)
                self._record_feedback(ExecutionFeedback(
                    decision_id=getattr(decision, "decision_id", None), context_id=ctx.context_id,
                    skill=getattr(ex, "skill", None), entity_id=_arg(decision, "object_id"),
                    target_id=_arg(decision, "target_id"),
                    candidate_id=getattr(ex, "candidate_id", None),
                    executed=False, status="rejected", failure_code=last_failure.value,
                    pre_observation_ref=world.observation_ref, post_observation_ref=world.observation_ref,
                    pre_state_version=world.state_version, post_state_version=world.state_version,
                    rejection_reasons=errs, held_object_after=world.held_object))
                last_note = "; ".join(errs)
                if ledger.semantic_repairs > self.budgets.max_semantic_repairs:
                    return self._finalize(task, ledger, TerminalStatus.failed, last_failure,
                                          "semantic repair budget", mode)
                if self._repeated_without_new_evidence(_signature(decision), world, ledger):
                    return self._finalize(task, ledger, TerminalStatus.failed,
                                          FailureCode.REPEATED_INVALID, "identical invalid decisions", mode)
                continue

            # ---- 5. control outcomes ----
            if decision.action == "clarify":
                self.store.log("needs_clarification", missing_information=decision.missing_information,
                               decision_id=decision.decision_id)
                return self._finalize(task, ledger, TerminalStatus.needs_clarification,
                                      FailureCode.AMBIGUOUS_TASK, decision.missing_information or "", mode)
            if decision.action == "blocked":
                self.store.log("terminated_blocked", decision_id=decision.decision_id,
                               note="the model found no feasible next action; that is not a proof "
                                    "that the task is unsolvable")
                return self._finalize(task, ledger, TerminalStatus.failed, None, "blocked by model", mode)
            if decision.action == "finish":
                accepted, report, snapshot = self._finish_check(goal)
                self._record_feedback(ExecutionFeedback(
                    decision_id=decision.decision_id, context_id=ctx.context_id,
                    executed=False,
                    status="finish_accepted" if accepted else "finish_rejected",
                    failure_code=None if accepted else FailureCode.FINISH_REJECTED.value,
                    pre_observation_ref=world.observation_ref,
                    post_observation_ref=snapshot.observation_ref, new_measurement=True,
                    pre_state_version=world.state_version, post_state_version=snapshot.state_version,
                    verification=report,
                    held_object_after=self.terminal_world.held_object,
                    state_diff=diff_states(world, self.terminal_world)))
                self.store.log("finish_check", accepted=accepted, decision_id=decision.decision_id,
                               reports=[r.model_dump(mode="json") for r in report.reports])
                if accepted:
                    return self._finalize(task, ledger, TerminalStatus.success, None, "", mode)
                ledger.false_finish_attempts += 1
                last_failure, last_note = FailureCode.FINISH_REJECTED, "agent goals not satisfied"
                if ledger.false_finish_attempts >= MAX_FALSE_FINISHES:
                    return self._finalize(task, ledger, TerminalStatus.failed, last_failure,
                                          "repeated false finish", mode)
                continue

            # ---- 6. execute exactly one skill ----
            if not ledger.can_call_skill():
                return self._finalize(task, ledger, TerminalStatus.failed, FailureCode.BUDGET_EXHAUSTED,
                                      "skill call budget", mode)
            call = decision.skill_call(plan_id=f"mode{mode}_{decision.decision_id}",
                                       step_id=f"r{ctx.round_index}")
            call.timeout_s = self.budgets.per_skill_sim_timeout_s
            call.expected_state_version = world.state_version
            if self.environment is not None:
                for ev in self.environment.before_skill(call):
                    # nested so the injection's own `event_id` is not eaten by the
                    # envelope-reserved-key guard
                    self.store.log("environment_event", phase="before_skill", injection=ev)
            self.store.log("skill_call", decision_id=decision.decision_id, context_id=ctx.context_id,
                           call=call.model_dump(mode="json"))
            pre_world = world
            result = self.executor.execute(call)
            ledger.charge_skill(call.skill, result.sim_seconds_used)
            if result.candidate_resolution == "runtime_fallback_rule":
                self.slot_resolution_fallbacks += 1
            if self.environment is not None:
                for ev in self.environment.after_skill(call, result):
                    self.store.log("environment_event", phase="after_skill", injection=ev)
            if self.scene.action_in_progress:
                raise RuntimeError("a skill returned while its action budget was still open")

            # ---- 7. re-observe and verify for success, failure, timeout alike ----
            post_world = self.observe()
            fb = self._build_feedback(decision, ctx, call, result, pre_world, post_world, goal)
            self._record_feedback(fb)
            last_failure = self._map_failure(result)
            last_note = "; ".join(result.notes) or (result.failure_code or "")
            if self._repeated_without_new_evidence(_signature(decision), post_world, ledger):
                return self._finalize(task, ledger, TerminalStatus.failed, FailureCode.REPEATED_INVALID,
                                      "identical action with no new evidence", mode)

    # ---------- context ----------
    def initial_context(self, task: TaskInput, goal: GoalSpec) -> DecisionContext:
        """The context for a request made *before* the loop — mode A's single
        planning call. It is built by the same `_build_context` as a continuous
        round, so the baseline is not given a poorer view than mode B, and the
        ledger it carries shows the untouched budget the plan was drawn under."""
        world = self.observe()
        progress = RuntimeVerifier(world, self.config).progress(goal)
        ctx = self._build_context(task, goal, world, progress, BudgetLedger(self.budgets))
        self.store.log("decision_context", context_id=ctx.context_id, round_index=ctx.round_index,
                       state_version=ctx.state_version, observation_ref=ctx.observation_ref,
                       goal_version=ctx.goal_version, purpose="one_shot_plan_request",
                       progress=[p.model_dump(mode="json") for p in progress],
                       candidates=[c.summary() for c in ctx.candidates],
                       candidates_truncated=ctx.candidates_truncated,
                       attempts=[], budget=ctx.budget, feedbacks_included=0)
        return ctx

    PER_GOAL_CANDIDATES = 3

    def _build_context(self, task, goal, world, progress, ledger) -> DecisionContext:
        """Candidates for pending goals are offered even while the gripper is
        empty: a geometric option existing is distinct from an action being
        executable now, and the model must see both (SPEC 5.5).

        The offer is a *bounded* sample of a larger feasible set, so the count
        left out is reported. An empty list in the payload then means "these are
        the ones we showed you", never "this is all there is".
        """
        candidates = []
        seen: set[str] = set()
        truncated = 0
        pending = [p for p in progress if p.value != PredicateVerdict.true]
        for p in pending[:4]:
            if not world.has_entity(p.entity_id) or world.target(p.target_id) is None:
                continue
            full = self.placement_planner.generate(p.target_id, p.entity_id, world, limit=None)
            checked = [self.placement_planner.recheck(c, world) for c in full]
            # feasible options first, then the blocked ones, each group in the
            # generator's own deterministic order: the sample must stay stable
            # across rounds, but it must not be all-blocked-by-construction
            checked.sort(key=lambda ch: (not ch.ok, ch.candidate.candidate_id))
            truncated += max(0, len(checked) - self.PER_GOAL_CANDIDATES)
            for chk in checked[: self.PER_GOAL_CANDIDATES]:
                if chk.candidate.candidate_id not in seen:
                    seen.add(chk.candidate.candidate_id)
                    candidates.append(chk.candidate)
        return DecisionContext(
            episode_id=self.episode_id, round_index=ledger.decision_rounds, task=task, goal=goal,
            public_constraints=list(task.declared_constraints),
            state_version=world.state_version, observation_ref=world.observation_ref,
            sim_time=world.sim_time, world=world, progress=progress,
            last_feedback=self.feedback_history[-1] if self.feedback_history else None,
            recent_feedbacks=list(self.feedback_history), attempts=list(self.attempts),
            skill_catalogue=SkillRegistry.CATALOGUE, candidates=candidates,
            candidates_truncated=truncated,
            budget=ledger.remaining_payload())

    # ---------- decision validation ----------
    def _validate_decision(self, decision: Decision, ctx: DecisionContext,
                           world: WorldState) -> tuple[bool, list[str]]:
        errs: list[str] = []
        if decision.context_id != ctx.context_id:
            errs.append(f"decision names context {decision.context_id}, not {ctx.context_id}")
        if decision.goal_ref != ctx.goal.goal_id:
            errs.append(f"decision goal_ref {decision.goal_ref} does not match the active goal "
                        f"{ctx.goal.goal_id}")
        if decision.based_on_state_version > world.state_version:
            errs.append(f"decision claims state version {decision.based_on_state_version}; "
                        f"the latest is {world.state_version}")
        elif decision.based_on_state_version < world.state_version:
            # a stale claim alone never invalidates an action: re-check instead
            self.store.log("state_version_stale", claimed=decision.based_on_state_version,
                           latest=world.state_version, decision_id=decision.decision_id,
                           note="re-checked against the latest snapshot; the version step by "
                                "itself is not a rejection reason")
        if decision.action != "execute":
            return (not errs), errs
        step = PlanStep(id="d0", skill=decision.execute.skill, args=dict(decision.execute.args))
        single = Plan(plan_id="decision", based_on_state_version=world.state_version,
                      goal_ref=decision.goal_ref, steps=[step])
        ok, plan_errs = PlanValidator(world, self.config).validate(single)
        return (ok and not errs), (errs + plan_errs)

    def _repeated_without_new_evidence(self, signature, world: WorldState, ledger) -> bool:
        """Same action, same arguments, same candidate, and a world that is not
        measurably different from the last time that exact combination was
        attempted — i.e. nothing arrived that could plausibly change the
        outcome (SPEC 6.2: 相同无效尝试, "在无相关新证据下重复")."""
        if signature is None:
            return False
        fp = _world_fingerprint(world)
        if signature == self._last_signature and fp == self._last_signature_fp:
            ledger.identical_invalid += 1
            ledger.identical_repeats_total += 1
        else:
            self._last_signature = signature
            ledger.identical_invalid = 1
        self._last_signature_fp = fp
        return ledger.identical_invalid > self.budgets.max_identical_invalid_attempts

    def _accumulate_usage(self, source):
        """Per-episode token cost. One adapter is reused across a whole batch, so
        its counter is a baseline to subtract, not this episode's spend."""
        for k in self.usage:
            current = int(getattr(source, k, self._usage_base[k]) or 0)
            self.usage[k] = max(self.usage[k], current - self._usage_base[k])

    def _billed(self, key: str) -> int:
        return int(self.usage[key]) + int(self._usage_offset.get(key, 0))

    # ---------- feedback ----------
    def _build_feedback(self, decision, ctx, call: SkillCall, result: SkillResult,
                        pre_world: WorldState, post_world: WorldState, goal: GoalSpec) -> ExecutionFeedback:
        verifier = RuntimeVerifier(post_world, self.config)
        eid, tid = call.args.get("object_id"), call.args.get("target_id")
        reports = []
        if eid and call.skill == "pick" and result.status == SkillStatus.completed:
            reports.append(verifier.verify_grasp(eid, before=pre_world))
        elif eid and tid and call.skill == "place":
            reports.append(verifier.verify_placement(eid, tid))
        report = VerificationReport(
            reports=reports, source=post_world.source, tolerance_version=self.config.tolerance_version,
            world_observation_ref=post_world.observation_ref, state_version=post_world.state_version)
        before_prog = {f"{p.entity_id}->{p.target_id}": p.value.value for p in ctx.progress}
        changes = [{"assignment": f"{p.entity_id}->{p.target_id}",
                    "from": before_prog.get(f"{p.entity_id}->{p.target_id}", "absent"), "to": p.value.value}
                   for p in verifier.progress(goal)
                   if before_prog.get(f"{p.entity_id}->{p.target_id}") != p.value.value]
        diff = diff_states(pre_world, post_world)
        affected = sorted(({m["entity_id"] for m in diff["moved"]}
                           | {c["entity_id"] for c in diff["state_changed"]}) - {eid or ""})
        return ExecutionFeedback(
            decision_id=decision.decision_id, call_id=result.call_id, context_id=ctx.context_id,
            skill=call.skill, entity_id=eid, target_id=tid, candidate_id=call.candidate_id,
            candidate_resolution=result.candidate_resolution,
            executed=result.status != SkillStatus.rejected,
            stages_executed=list(result.stages_executed),
            status=result.status.value, failure_code=result.failure_code,
            # The refs must name the same measurements the versions and the diff
            # are computed from: this record describes what the *loop* measured
            # around the action, and a skill's own intermediate snapshot is a
            # different observation. Reporting the skill's ref beside the loop's
            # version would let a completed pick say "no new measurement" while
            # the state version it reports moved (SPEC 5.1).
            pre_observation_ref=pre_world.observation_ref,
            post_observation_ref=post_world.observation_ref,
            new_measurement=post_world.state_version > pre_world.state_version,
            pre_state_version=pre_world.state_version, post_state_version=post_world.state_version,
            verification=report if reports else None, held_object_after=post_world.held_object,
            progress_changes=changes, affected_entities=affected,
            measurements={k: round(v, 4) for k, v in result.measurements.items()},
            state_diff=diff,
            rejection_reasons=list(result.notes) if result.status == SkillStatus.rejected else [],
            sim_seconds_used=result.sim_seconds_used)

    def _record_feedback(self, fb: ExecutionFeedback):
        self.feedback_history.append(fb)
        if len(self.feedback_history) > HISTORY_CAP:
            self.feedback_history.pop(0)
        self.store.log("execution_feedback", feedback=fb.model_dump(mode="json"))
        for a in self.attempts:
            if ((a.skill, a.entity_id, a.target_id, a.candidate_id, a.failure_code)
                    == (fb.skill, fb.entity_id, fb.target_id, fb.candidate_id, fb.failure_code)):
                a.count += 1
                a.at_state_version = fb.post_state_version or a.at_state_version
                a.last_observation_ref = fb.post_observation_ref
                break
        else:
            self.attempts.append(AttemptRecord(
                skill=str(fb.skill), entity_id=fb.entity_id, target_id=fb.target_id,
                candidate_id=fb.candidate_id, failure_code=fb.failure_code,
                at_state_version=fb.post_state_version or 0,
                last_observation_ref=fb.post_observation_ref))
        if len(self.attempts) > HISTORY_CAP:
            self.attempts.pop(0)

    @staticmethod
    def _map_failure(result: SkillResult) -> FailureCode | None:
        for fc in FailureCode:
            if fc.value == result.failure_code:
                return fc
        return None if result.status == SkillStatus.completed else FailureCode.ANOMALOUS_CONTACT

    # ---------- terminal protocol ----------
    def _finish_check(self, goal: GoalSpec):
        """The single end-of-episode protocol: settle once, freeze one snapshot,
        and let both judges work from *that* sample (SPEC 7)."""
        snapshot = TerminalSnapshot.capture(self.scene, self.config.settle_time_s, "terminal", 0)
        world = self.observe()
        snapshot = snapshot.model_copy(update={"observation_ref": world.observation_ref,
                                               "state_version": world.state_version})
        self.terminal_world = world
        self.terminal_snapshot = snapshot
        report = RuntimeVerifier(world, self.config).verify_goals(goal)
        return report.overall == PredicateVerdict.true, report, snapshot

    def _finalize(self, task: TaskInput, ledger: BudgetLedger, status: TerminalStatus,
                  failure: FailureCode | None, note: str, mode: str) -> EpisodeResult:
        if self.terminal_world is None:
            self._finish_check(self.goal)
        items = RuntimeVerifier(self.terminal_world, self.config).progress(self.goal)
        completed = sum(1 for i in items if i.value == PredicateVerdict.true)
        unknown = sum(1 for i in items if i.value == PredicateVerdict.unknown)
        success = (status == TerminalStatus.success and bool(items) and completed == len(items))
        result = EpisodeResult(
            episode_id=self.episode_id, task_id=task.task_id, terminal_status=status, mode=mode,
            decision_rounds=ledger.decision_rounds, skill_calls=ledger.skill_calls,
            http_requests=ledger.http_requests,
            rejected_decisions=ledger.rejected_decisions, false_finish_attempts=ledger.false_finish_attempts,
            identical_invalid_attempts=ledger.identical_invalid,
            identical_repeats_total=ledger.identical_repeats_total,
            model_errors=ledger.model_errors,
            semantic_repairs=ledger.semantic_repairs,
            prompt_tokens=self._billed("prompt_tokens"),
            completion_tokens=self._billed("completion_tokens"),
            provider_counters={k: self._billed(k) for k in SOURCE_COUNTERS
                               if k not in ("prompt_tokens", "completion_tokens")},
            score_complete_success=success, objects_total=len(items), objects_completed=completed,
            failure_type=failure, sim_time_s=round(self.scene.sim_time, 2),
            wall_time_s=round(time.time() - ledger.t_start_wall, 2),
            slot_resolution_fallbacks=self.slot_resolution_fallbacks,
            artifacts={"terminal_state_version": str(self.terminal_world.state_version),
                       "terminal_observation_ref": str(self.terminal_world.observation_ref),
                       "unknown_goals": str(unknown), "note": note})
        self.store.log("episode_end", result=result.model_dump(mode="json"), note=note)
        return result


def _arg(decision, name) -> str | None:
    ex = getattr(decision, "execute", None)
    return getattr(ex, "args", {}).get(name) if ex is not None else None


def _signature(decision) -> tuple | None:
    if decision is None:
        return None
    if getattr(decision, "action", None) != "execute":
        return (decision.action, None, None, None)
    ex = decision.execute
    return (decision.action, ex.skill, tuple(sorted(ex.args.items())), ex.candidate_id)


def _world_fingerprint(world: WorldState) -> tuple:
    """Millimetre-level summary of what the world looks like. Two attempts made
    under the same fingerprint cannot be distinguished by any new measurement."""
    return (str(world.held_object),
            tuple(sorted((e.entity_id, round(e.pose.position.x, 3), round(e.pose.position.y, 3),
                          round(e.pose.position.z, 3), str(e.held)) for e in world.entities)))
