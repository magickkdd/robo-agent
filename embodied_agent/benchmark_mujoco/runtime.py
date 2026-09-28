"""The v0.1/v0.2 decision loop, attached to one MuJoCo task instance (SPEC-v0.2 §10).

`Runtime.run_episode` still owns when to observe, when to ask, when to refuse and when
to stop; every difference below is a method the base class already asked about. The
claim this file exists to make is the same one `benchmark/runtime_alfred.py` makes: a
result here is about continuous decision-making, not about a second agent shaped like
the benchmark.

What this channel answers differently from the text one, and why each answer belongs
to the world rather than to a strategy:

* **measurement** — poses, not sentences, so `progress` and the finish check get real
  geometric verdicts and the desktop finish protocol stays intact;
* **termination** — the bench ends an episode by *horizon*, not by a `done` flag it
  tells the agent about, so `_backend_termination` reads the step count and the
  official flag is consulted once, after the loop stops;
* **the score** — `eval_view()` is read in exactly three places (`_episode_success`,
  `_backend_termination`, `_finalize`), all of which the base class calls after or
  beside a decision, never into one.

Budgets: `Budgets.max_skill_calls` is *not* the environment step count here — one skill
consumes many steps — so it carries the skill-call cap and the bench's own horizon is
shown beside it in `_budget_view`, under its own name.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ..core.contracts import (
    Budgets,
    Decision,
    DecisionContext,
    EpisodeResult,
    FailureCode,
    GoalSpec,
    SkillCall,
    SkillResult,
    SkillStatus,
    TerminalSnapshot,
    TerminalStatus,
    VerifyConfig,
    WorldState,
)
from ..core.events import EpisodeStore
from ..core.runtime import BudgetLedger, Runtime
from .executor import MujocoScene, MujocoSkillExecutor
from .prompts import MujocoDecisionContext
from .skills import CATALOGUE, catalogue_sha256, render
from .state import MujocoVerifier, mw_fingerprint, mw_state_diff, mw_world_state

MW_SKILL_CALLS = 24
MW_DECISION_ROUNDS = 32
MW_HTTP_REQUESTS = 40
MW_EPISODE_WALL_S = 900.0


def mw_budgets(*, skill_calls: int = MW_SKILL_CALLS, decision_rounds: int = MW_DECISION_ROUNDS,
               http_requests: int = MW_HTTP_REQUESTS,
               wall_clock_s: float = MW_EPISODE_WALL_S) -> Budgets:
    return Budgets(max_decision_rounds=decision_rounds, max_http_requests=http_requests,
                   max_skill_calls=skill_calls, wall_clock_s=wall_clock_s)


class MujocoRuntime(Runtime):
    """`Runtime` over one MetaWorld task instance. No method here chooses an action."""

    context_class = MujocoDecisionContext

    def __init__(self, backend, run_dir: str, budgets: Budgets, episode_id: str, *,
                 config: VerifyConfig | None = None, store: EpisodeStore | None = None,
                 frames_dir: Optional[str] = None):
        self.backend = backend
        self.prompt_sha256 = catalogue_sha256()
        self.frames_dir = frames_dir
        self._frame_seq = 0
        self._agent_finished = False
        self._env_terminated = False
        super().__init__(MujocoScene(backend), MujocoSkillExecutor(backend), run_dir, budgets,
                         episode_id, config=config, environment=None, store=store)

    # ---------- observation channel ----------
    def observe(self) -> WorldState:
        w = super().observe()
        self._write_frames(w)
        return w

    def _write_frames(self, world: WorldState) -> None:
        """Frames are evidence, not decoration: each one is named by the observation ref
        of the snapshot it was captured for, so a claim about what was seen can be opened."""
        if not self.frames_dir:
            return
        os.makedirs(self.frames_dir, exist_ok=True)
        for view, frame in sorted(self.backend.capture_all().items()):
            if frame is None:
                continue
            try:
                import imageio.v2 as iio
                path = os.path.join(self.frames_dir, f"{world.observation_ref}_{view}.png")
                iio.imwrite(path, frame)
            except Exception:  # noqa: BLE001 - a failed thumbnail must not end an episode
                self.store.log("frame_write_failed", observation_ref=world.observation_ref,
                               view=view, note="the snapshot stands; the image is a copy")

    def _capture_world(self, state_version: int, observation_ref: str) -> WorldState:
        return mw_world_state(self.backend, state_version, observation_ref,
                              init_z=getattr(self.backend, "init_z", None))

    def _verifier(self, world: WorldState):
        return MujocoVerifier(world, self.config)

    def _skill_catalogue(self) -> dict:
        catalogue = {name: dict(spec) for name, spec in CATALOGUE.items()}
        perceiver = getattr(self, "perceiver", None)
        if perceiver is not None and "observe" in catalogue:
            spec = dict(catalogue["observe"])
            spec["args"] = {**spec.get("args", {}),
                            "view": "one of: " + ", ".join(sorted(perceiver.views))
                                    + " (this run's cameras)"}
            catalogue["observe"] = spec
        return catalogue

    def _context_candidates(self, world: WorldState, progress) -> tuple[list, int]:
        return [], 0

    def _fingerprint(self, world: WorldState) -> tuple:
        return mw_fingerprint(world)

    def _state_diff(self, pre_world: WorldState, post_world: WorldState) -> dict:
        return mw_state_diff(pre_world, post_world)

    def _verification_reports(self, call: SkillCall, result: SkillResult, pre_world: WorldState,
                              post_world: WorldState, verifier) -> list:
        if result.status == SkillStatus.rejected:
            return []
        return verifier.verify_action(call.skill, dict(call.args or {}))

    def _capture_terminal_snapshot(self) -> TerminalSnapshot:
        w = self.observe()
        return TerminalSnapshot(
            observation_ref=w.observation_ref, state_version=w.state_version,
            sim_time=self.scene.sim_time, settle_seconds=0.0,
            entities={e.entity_id: {"pose": ([e.pose.position.x, e.pose.position.y,
                                              e.pose.position.z] if e.pose else None),
                                    "held": str(e.held), "attributes": dict(e.attributes)}
                      for e in w.entities},
            targets={t.target_id: {"center": [t.center.x, t.center.y, t.center.z],
                                   "inner_half_m": t.inner_half} for t in w.targets})

    # ---------- termination ----------
    def _episode_success(self, status: TerminalStatus, items, completed: int) -> bool:
        return bool(self.backend.eval_view().get("official_success"))

    def _backend_termination(self):
        view = self.backend.eval_view()
        if view.get("error"):
            err = view["error"]
            return (TerminalStatus.failed, FailureCode.ENVIRONMENT_ERROR,
                    f"the MuJoCo backend faulted during {err.get('phase')!r} "
                    f"({err.get('type')}): {str(err.get('message'))[:200]}. The round is "
                    f"scored as infrastructure, not as a model failure (SPEC-BST 6.3)")
        if int(view["env_steps"]) >= int(view["max_env_actions"]):
            self._env_terminated = True
            won = bool(view.get("official_success"))
            return (TerminalStatus.success if won else TerminalStatus.failed, None,
                    f"the benchmark horizon ({view['env_steps']} simulator steps) ran out; "
                    f"official success={won}. This classification is an evaluation result "
                    f"read after the loop, never an observation (SPEC-v0.2 §10)")
        return None

    def _map_failure(self, result: SkillResult) -> Optional[FailureCode]:
        for fc in FailureCode:
            if fc.value == result.failure_code:
                return fc
        if result.status == SkillStatus.completed:
            return None
        if result.status == SkillStatus.timeout:
            return FailureCode.TIMEOUT
        return (FailureCode.ADAPTER_ERROR if result.status == SkillStatus.rejected
                else FailureCode.ENVIRONMENT_ERROR)

    # ---------- budgets ----------
    def _budget_view(self, ledger) -> dict:
        view = ledger.remaining_payload()
        env = self.backend.eval_view()
        view.update({"env_steps_used": int(env["env_steps"]),
                     "env_steps_total": int(env["max_env_actions"]),
                     "note": "skill_calls_used counts skill invocations, not simulator "
                             "steps: one motion here consumes many steps, and the bench's "
                             "own horizon is shown beside it under env_steps"})
        return view

    def _build_context(self, task, goal, world, progress, ledger) -> DecisionContext:
        ctx = super()._build_context(task, goal, world, progress, ledger)
        return ctx.model_copy(update={"budget": self._budget_view(ledger)})

    # ---------- decision validation ----------
    def _validate_decision(self, decision: Decision, ctx: DecisionContext,
                           world: WorldState) -> tuple[bool, list[str]]:
        ok, errs = super()._validate_decision(decision, ctx, world)
        if ok and decision.action == "finish":
            self._agent_finished = True
        return ok, errs

    def _validate_execution(self, decision: Decision, ctx: DecisionContext,
                            world: WorldState) -> tuple[bool, list[str]]:
        ex = decision.execute
        args, reasons = render(ex.skill, dict(ex.args or {}))
        if args is None:
            return False, [f"skill {ex.skill!r} is not one motion of this world: {r}"
                           for r in reasons]
        if ex.candidate_id:
            return False, ["this backend offers no candidate registry, so "
                           "execute.candidate_id must be null"]
        if str(dict(ex.args or {}).get("view") or "") and getattr(self, "perceiver", None) is None:
            # the arm that reads `view` is the camera arm; without it the number would be a word
            # about a viewpoint and the measurement would come from the simulator's own state
            return False, ["`observe` with a `view` names a camera, and this arm has none to "
                           "point: it measures from the simulator's state, so every view is "
                           "the same measurement"]
        for name in ("object_id", "target_id"):
            value = args.get(name)
            if value and not world.has_entity(value) and not world.target(value):
                return False, [f"args[{name!r}]={value!r} is not an entity this snapshot "
                               f"measured; naming a thing that is not in the world is a "
                               f"refusal, not a guess"]
        return True, []

    # ---------- terminal protocol ----------
    def _finalize(self, task, ledger: BudgetLedger, status: TerminalStatus,
                  failure: Optional[FailureCode], note: str, mode: str) -> EpisodeResult:
        if self.termination_reason is None:
            self.termination_reason = self._termination_of(status, failure, note)
        env = self.backend.eval_view()
        if self._agent_finished and not env.get("official_success"):
            self.store.log("terminal_status_restated", from_status=status.value,
                           to_status=TerminalStatus.failed.value,
                           note="an accepted finish means the geometry verified, not that the "
                                "benchmark scored it; the two are reported side by side")
        self.store.log("termination", reason=self.termination_reason,
                       terminal_status=status.value,
                       failure_code=failure.value if failure else None,
                       official_success=bool(env.get("official_success")),
                       env_steps=int(env["env_steps"]),
                       obj_to_target_m=env.get("obj_to_target_m"), note=note)
        result = super()._finalize(task, ledger, status, failure, note, mode)
        self.store.log("episode_official", official_success=bool(env.get("official_success")),
                       termination_reason=self.termination_reason,
                       false_finish=bool(self._agent_finished and not env.get("official_success")),
                       env_steps=int(env["env_steps"]),
                       reward_total=env.get("reward_total"),
                       note="read after the loop stopped; never an input to a decision "
                            "(SPEC-BST 4.1, SPEC-v0.2 §10)")
        return result

    def _termination_of(self, status: TerminalStatus, failure: Optional[FailureCode],
                        note: str) -> str:
        if self._env_terminated:
            return "ENV_TERMINATED"
        if self._agent_finished:
            return "AGENT_FINISH"
        if status == TerminalStatus.needs_clarification:
            return "NEEDS_CLARIFICATION"
        if failure == FailureCode.ENVIRONMENT_ERROR:
            return "ENVIRONMENT_ERROR"
        if failure == FailureCode.ADAPTER_ERROR:
            return "ADAPTER_ERROR"
        if failure in (FailureCode.MODEL_ERROR, FailureCode.PROVIDER_ERROR):
            return "MODEL_ERROR"
        if failure == FailureCode.REPEATED_INVALID:
            return "REPEAT_GUARD"
        if failure in (FailureCode.BUDGET_EXHAUSTED, FailureCode.TIMEOUT):
            return "BUDGET_EXHAUSTED"
        if failure == FailureCode.INVALID_DECISION:
            return "BUDGET_EXHAUSTED" if "budget" in note else "MODEL_ERROR"
        if failure is None:
            return "AGENT_BLOCKED"
        return "MODEL_ERROR"


__all__ = ["MujocoRuntime", "mw_budgets", "MW_SKILL_CALLS", "MW_DECISION_ROUNDS"]
