"""Bounded Runtime state machine (spec 7, 8, 10.4, 11).

Guarantees:
- every pick/place is followed by a forced Runtime verification;
- all budgets (per-object retries, replans, skill calls, model requests,
  wall clock, sim-time skill timeout, no-progress streak) also cover
  verification-failed branches -> no infinite loops (fixes prototype defect);
- retry / recovery / replan are counted separately (spec 11.3);
- recovery for GRASP_MISS is bounded and deterministic (spec 4.4, 11.3);
- the runtime never reads hidden scoring truth; it only sees the privileged
  observation channel (S1) through WorldState.
"""
from __future__ import annotations

import os
import time

import numpy as np

from .contracts import (
    Budgets,
    EpisodeResult,
    EvalSpec,
    FailureCode,
    GoalSpec,
    Plan,
    SkillCall,
    SkillResult,
    SkillStatus,
    Source,
    TaskInput,
    TerminalStatus,
    VerificationReport,
)
from .events import EpisodeStore
from .planner import PlanValidator, RulePlanner
from .scene import PhysicsScene
from .skills import SkillExecutor
from .verify import RuntimeVerifier, build_world_state


class BudgetExceeded(Exception):
    pass


class BudgetLedger:
    def __init__(self, budgets: Budgets, n_objects: int):
        self.b = budgets
        self.n_objects = n_objects
        self.skill_calls = 0
        self.model_requests = 0
        self.replans = 0
        self.recoveries = 0
        self.retries = 0
        self.per_object_attempts: dict[str, int] = {}
        self.t_start_wall = time.time()

    def can_skill_call(self) -> bool:
        return self.skill_calls < self.b.max_skill_calls

    def can_replan(self) -> bool:
        return self.replans < self.b.max_global_replans

    def can_retry_object(self, eid: str) -> bool:
        return self.per_object_attempts.get(eid, 0) < self.b.per_object_extra_attempts

    def wall_remaining(self) -> float:
        return self.b.wall_clock_s - (time.time() - self.t_start_wall)


class RecoveryPolicy:
    """Deterministic S1 recovery: GRASP_MISS -> re-observe, safe_retreat, retry
    with the next grasp candidate. Everything else is reported, not recovered."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def decide(self, result: SkillResult, eid: str, ledger: BudgetLedger, skill: str = "pick") -> str:
        if result.status == SkillStatus.completed:
            return "continue"
        if not self.enabled:
            return "fail_step"
        if result.failure_code == FailureCode.GRASP_MISS.value:
            if ledger.can_retry_object(eid):
                return "recover_retry"
            return "fail_step"
        if (
            skill == "place"
            and result.failure_code in (FailureCode.IK_UNREACHABLE.value, FailureCode.VERIFY_FAILED.value)
            and result.held_state == eid
            and ledger.can_retry_object(eid)
        ):
            return "recover_retry_place"  # still holding: bounded re-attempt of the place
        if result.failure_code == FailureCode.TARGET_FULL.value:
            return "fail_step"  # never blind-place (spec 11.3)
        return "fail_step"


class Runtime:
    def __init__(
        self,
        scene: PhysicsScene,
        executor: SkillExecutor,
        run_dir: str,
        budgets: Budgets,
        episode_id: str,
    ):
        self.scene = scene
        self.executor = executor
        self.store = EpisodeStore(run_dir, episode_id)
        self.episode_id = episode_id
        self.budgets = budgets
        self.state_version = 0
        self._obs_seq = 0
        self.store._sim_time_fn = lambda: self.scene.sim_time

    # ---------- observation channel ----------
    def observe(self) -> "WorldState":
        from .contracts import WorldState  # local import to avoid cycle at module load

        self._obs_seq += 1
        w = build_world_state(self.scene, self.state_version, obs_ref=f"obs_{self._obs_seq}")
        w.wall_time = time.time()
        return w

    def bump_state(self):
        self.state_version += 1

    def ledger_model_request(self):
        """Model requests made before the episode loop starts (e.g. the initial
        LLM plan) still count against the request budget (spec 10.4)."""
        if getattr(self, "_ledger", None) is not None:
            self._ledger.model_requests += 1
        else:
            self._pending_model_requests = getattr(self, "_pending_model_requests", 0) + 1

    # ---------- episode ----------
    def run_episode(
        self,
        task: TaskInput,
        goal: GoalSpec,
        eval_spec: EvalSpec,
        planner: RulePlanner,
        recovery_enabled: bool = True,
        planner_kind: str = "rule",
        fault: tuple | None = None,
    ) -> EpisodeResult:
        store = self.store
        store.log("episode_start", task_id=task.task_id, planner=planner_kind, recovery=recovery_enabled)
        ledger = BudgetLedger(self.budgets, n_objects=len(eval_spec.assignments))
        self._active_eval_spec = eval_spec
        self._ledger = ledger
        ledger.model_requests += getattr(self, "_pending_model_requests", 0)
        self._fault = fault          # frozen fault config, applied to the FIRST pick only
        self._first_pick_done = False

        if not goal.well_formed:
            store.log("needs_clarification", reason=goal.clarification_request)
            return self._finalize(task, ledger, TerminalStatus.needs_clarification,
                                  failure=FailureCode.AMBIGUOUS_TASK, eval_spec=eval_spec)

        verifier = RuntimeVerifier(self.scene, self.observe(), eval_spec.tolerance_version)
        validator = PlanValidator(self.observe())
        no_progress = 0
        last_failure: FailureCode | None = None

        while True:
            if not ledger.can_replan():
                return self._finalize(task, ledger, TerminalStatus.failed,
                                      failure=FailureCode.BUDGET_EXHAUSTED, eval_spec=eval_spec,
                                      note="replan budget exhausted")
            if ledger.wall_remaining() <= 0:
                return self._finalize(task, ledger, TerminalStatus.failed,
                                      failure=FailureCode.TIMEOUT, eval_spec=eval_spec)

            world = self.observe()
            plan = planner.plan(goal, world)
            ledger.replans += 1  # every planning round consumes replan budget
            store.log("plan_generated", plan=plan.model_dump(mode="json"), state_version=world.state_version)
            ok, errs = validator.validate(plan)
            if not ok:
                store.log("plan_rejected", errors=errs)
                return self._finalize(task, ledger, TerminalStatus.failed,
                                      failure=FailureCode.INVALID_PLAN, eval_spec=eval_spec, note=";".join(errs))

            progress_before = self._progress_signature(plan)
            step_failure, terminal = self._execute_plan(plan, verifier, ledger, recovery_enabled, store)
            if terminal:
                return self._finalize(task, ledger, TerminalStatus.failed,
                                      failure=step_failure or FailureCode.BUDGET_EXHAUSTED,
                                      eval_spec=eval_spec)
            if step_failure is None:
                # all steps of this plan done; check goal predicates
                report = self._final_verify(eval_spec)
                if report.overall.value == "true":
                    return self._finalize(task, ledger, TerminalStatus.success,
                                          failure=None, eval_spec=eval_spec)
                last_failure = FailureCode.VERIFY_FAILED
                store.log("final_verify_failed", reports=[r.model_dump(mode="json") for r in report.reports])
                no_progress += 1
            else:
                last_failure = step_failure
                no_progress += 1
                self.last_step_failure = f"{step_failure.value if step_failure else None}"
            if progress_before == self._progress_signature(plan) and step_failure is not None:
                pass  # signature equality already reflected in no_progress
            if no_progress >= self.budgets.max_consecutive_no_progress:
                return self._finalize(task, ledger, TerminalStatus.failed,
                                      failure=last_failure or FailureCode.BUDGET_EXHAUSTED,
                                      eval_spec=eval_spec, note="no-progress streak")

    # ---------- inner loop ----------
    def _maybe_inject_fault(self, call: SkillCall, step, store: EpisodeStore) -> bool:
        """Fault injector (spec 4.4): the frozen offset enters only the FIRST
        grasp approach of the episode; the agent cannot detect it as an
        injection, it just observes a grasp miss."""
        if self._fault is None or self._first_pick_done or step.skill != "pick":
            return False
        self._first_pick_done = True
        fault_type, offset = self._fault
        if fault_type != "grasp_offset":
            return False
        call.args["grasp_offset"] = ",".join(str(v) for v in offset)
        store.log("fault_injection", type=fault_type, offset=list(offset), call_id=call.call_id)
        return True

    def _execute_plan(self, plan: Plan, verifier: RuntimeVerifier, ledger: BudgetLedger,
                      recovery_enabled: bool, store: EpisodeStore) -> tuple[FailureCode | None, bool]:
        """Execute steps sequentially with forced verification. Returns
        (failure_code, terminate): terminate=True ends the episode immediately
        (recovery disabled or recovery budget exhausted); failure=None with
        terminate=False means every step verified."""
        recovery = RecoveryPolicy(enabled=recovery_enabled)
        for step in plan.steps:
            import os as _os
            if _os.environ.get("DBG_RUNTIME"):
                print("STEP", step.id, step.skill, step.args)
            if not ledger.can_skill_call():
                return FailureCode.BUDGET_EXHAUSTED, True
            call = SkillCall(
                plan_id=plan.plan_id,
                step_id=step.id,
                skill=step.skill,
                args=dict(step.args),
                timeout_s=self.budgets.per_skill_sim_timeout_s,
                expected_state_version=self.state_version,
            )
            if fault_applied := self._maybe_inject_fault(call, step, store):
                pass  # logged inside; call now carries the frozen offset
            store.log("skill_call", call=call.model_dump(mode="json"))
            ledger.skill_calls += 1
            result = self.executor.execute(call)
            store.log("skill_result", call_id=result.call_id, result=result.model_dump(mode="json"))

            eid = step.args.get("object_id", "")
            if step.skill == "pick":
                report = verifier.verify_grasp(eid) if result.status == SkillStatus.completed else None
            elif step.skill == "place":
                report = verifier.verify_place_step(eid, step.args["target_id"], self._active_eval_spec) \
                    if result.status == SkillStatus.completed else None
            else:
                report = None
            if report is not None:
                store.log("step_verify", call_id=result.call_id, report=report.model_dump(mode="json"))

            decision = recovery.decide(result, eid, ledger, skill=step.skill)
            if report is not None and report.value.value == "false":
                decision = recovery.decide(
                    SkillResult(call_id=result.call_id, status=SkillStatus.failed,
                                failure_code=FailureCode.VERIFY_FAILED.value,
                                t_start=result.t_start, t_end=result.t_end,
                                held_state=result.held_state),
                    eid, ledger, skill=step.skill,
                )
            if decision == "continue":
                self.bump_state()
                if step.skill == "pick":
                    ledger.per_object_attempts[eid] = 0
                continue
            if decision in ("recover_retry", "recover_retry_place"):
                ledger.recoveries += 1
                ledger.retries += 1
                ledger.per_object_attempts[eid] = ledger.per_object_attempts.get(eid, 0) + 1
                attempt = ledger.per_object_attempts[eid]
                retry_args = dict(step.args)
                if decision == "recover_retry":
                    retry_args["candidate_index"] = str(attempt)
                    self._safe_retreat(ledger, store)  # retreat + re-observe before regrasp
                    self.bump_state()
                store.log("recovery", type=decision, object=eid, attempt=attempt)
                retry_call = SkillCall(
                    plan_id=plan.plan_id, step_id=step.id, skill=step.skill,
                    args=retry_args,
                    timeout_s=self.budgets.per_skill_sim_timeout_s,
                )
                if not ledger.can_skill_call():
                    return FailureCode.BUDGET_EXHAUSTED, True
                ledger.skill_calls += 1
                retry = self.executor.execute(retry_call)
                store.log("skill_result", call_id=retry.call_id, result=retry.model_dump(mode="json"),
                          retry_of=call.call_id)
                if retry.status == SkillStatus.completed:
                    if step.skill == "pick":
                        rreport = verifier.verify_grasp(eid)
                    else:
                        rreport = verifier.verify_place_step(eid, step.args["target_id"], self._active_eval_spec)
                    store.log("step_verify", call_id=retry.call_id, report=rreport.model_dump(mode="json"))
                    if rreport.value.value == "true":
                        self.bump_state()
                        ledger.per_object_attempts[eid] = 0
                        continue
                store.log("recovery_exhausted", object=eid)
                return self._map_failure(retry), True
            return self._map_failure(result), not recovery_enabled

        return None, False

    def _safe_retreat(self, ledger: BudgetLedger, store: EpisodeStore):
        if not ledger.can_skill_call():
            return
        call = SkillCall(plan_id="recovery", step_id="retreat", skill="safe_retreat",
                         timeout_s=self.budgets.per_skill_sim_timeout_s)
        ledger.skill_calls += 1
        r = self.executor.execute(call)
        store.log("skill_result", call_id=r.call_id, result=r.model_dump(mode="json"), phase="recovery")

    def _map_failure(self, result: SkillResult) -> FailureCode:
        code = result.failure_code
        for fc in FailureCode:
            if fc.value == code:
                return fc
        return FailureCode.ANOMALOUS_CONTACT

    def _progress_signature(self, plan: Plan) -> str:
        return ",".join(f"{eid}:{self._tray_of(eid) or '-'}" for eid in self.scene.objects)

    def _tray_of(self, eid: str) -> str | None:
        pos, _ = self.scene.object_pose(eid)
        for t in self.scene.trays.values():
            if abs(pos[0] - t["center"][0]) < t["inner_half"] and abs(pos[1] - t["center"][1]) < t["inner_half"]:
                return t["target_id"]
        return None

    # ---------- final verification ----------
    _active_eval_spec: EvalSpec | None = None

    def _final_verify(self, eval_spec: EvalSpec) -> VerificationReport:
        # settle window before judging (spec 11.2)
        self.scene.settle(eval_spec.settle_time_s)
        v = RuntimeVerifier(self.scene, self.observe(), eval_spec.tolerance_version)
        return VerificationReport(
            reports=v.verify_goals(eval_spec),
            source=Source.privileged,
            tolerance_version=eval_spec.tolerance_version,
        )

    def _finalize(self, task: TaskInput, ledger: BudgetLedger, status: TerminalStatus,
                  failure: FailureCode | None, eval_spec: EvalSpec, note: str | None = None) -> EpisodeResult:
        report = self._final_verify(eval_spec)
        completed = sum(1 for r in report.reports if r.value.value == "true")
        success = status == TerminalStatus.success and report.overall.value == "true"
        result = EpisodeResult(
            episode_id=self.episode_id,
            task_id=task.task_id,
            terminal_status=status,
            score_complete_success=bool(success),
            objects_total=len(eval_spec.assignments),
            objects_completed=completed,
            failure_type=failure,
            recovery_events=ledger.recoveries,
            retry_events=ledger.retries,
            replan_events=ledger.replans,
            model_requests=ledger.model_requests,
            sim_time_s=round(self.scene.sim_time, 2),
            wall_time_s=round(time.time() - ledger.t_start_wall, 2),
        )
        self.store.log("episode_end", result=result.model_dump(mode="json"), note=note)
        return result
