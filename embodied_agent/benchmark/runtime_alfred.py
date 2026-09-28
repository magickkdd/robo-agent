"""The v0.1 decision loop, attached to an ALFWorld text game (SPEC-BST 3, 6).

This is the whole of the benchmark attachment, and the constraint that shaped it is
that the loop below is *imported, not copied*: `Runtime.run_episode` still owns when
to observe, when to ask, when to refuse, when to stop. Every difference lives in a
method the base class already asked about — how a snapshot is measured, what the
catalogue says, what "legal" means, who ended the episode — so a result here is a
statement about continuous decision-making under v0.1's rules, not about a
second, benchmark-shaped agent (SPEC-BST 3.2).

What the text backend has to answer differently, and why each answer is the
environment's rather than a strategy:

* **measurement** (`_capture_world`, `observe`): one cached public response is one
  snapshot, and re-reading it returns the same observation ref instead of pretending
  a second measurement happened (SPEC-BST 4.3).
* **legality** (`_validate_execution`): the desktop validator can *prove* a
  geometric precondition; a text engine cannot be interrogated without asking it to
  execute. So the check is that the command is one the grammar can be built from,
  verbatim — and whether the engine accepts it is the model's own next observation.
  A hidden legality filter would be the answer sheet (SPEC-BST 3.5, 4.5).
* **verification** (`_verifier`, `_verification_reports`): the environment's sentence
  is restated, never interpreted. Task success is never inferred from it, because no
  public text states that an instruction is satisfied (SPEC-BST 4.6).
* **termination** (`_finish_check`, `_backend_termination`, `_finalize`): `finish`
  ends the episode. The desktop loop's `FINISH_REJECTED` loop back into the next
  round would need a verdict the public channel does not have, and the only verdict
  this backend holds is the hidden official flag — feeding that back would turn
  scoring into a policy oracle (SPEC-BST 6.2). So the flag is read *after* the loop
  stops, and a disagreement between "the agent stopped" and "the game was won" is
  recorded as `false_finish`, never resolved by another request.

The one thing that is not a base-class seam is `_preflight_trip`, which bounds the
two budgets `Budgets` has no field for — a per-episode token cap and the HTTP cap,
which the v0.1 loop reports but never enforces. It exists so this backend can honour
SPEC-BST 5.3 without editing the frozen v0.1 budget record.
"""
from __future__ import annotations

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
from .executor import AlfredTextExecutor, TextScene
from .native_actions import CATALOGUE, TEMPLATES, catalogue_sha256, render
from .prompts import TextDecisionContext
from .state import (
    TextVerifier,
    fact_key,
    text_fingerprint,
    text_state_diff,
    text_world_state,
)

# SPEC-BST 5.3: the default episode budgets. These are values of the *run*, not of
# `Budgets`; a per-episode `Budgets` instance is built from them so the frozen
# desktop defaults stay untouched.
BST_ENV_ACTIONS = 60
BST_DECISION_ROUNDS = 80
BST_HTTP_REQUESTS = 90
BST_EPISODE_TOKENS = 600_000
BST_HTTP_TIMEOUT_S = 60.0
BST_COMMAND_TIMEOUT_S = 10.0
BST_EPISODE_WALL_S = 900.0
BST_SETTLE_S = 0.0


def bst_budgets(*, env_actions: int = BST_ENV_ACTIONS, decision_rounds: int = BST_DECISION_ROUNDS,
                http_requests: int = BST_HTTP_REQUESTS,
                wall_clock_s: float = BST_EPISODE_WALL_S) -> Budgets:
    """The SPEC-BST 5.3 budget table, in the only shape the frozen loop reads.

    `max_skill_calls` *is* the native action budget here: one legal `execute` is
    exactly one `env.step` (SPEC-BST 3.5), which the executor guarantees by
    rendering at most one command per call. `per_skill_sim_timeout_s` carries the
    environment command timeout, the closest existing meaning."""
    return Budgets(max_decision_rounds=decision_rounds, max_http_requests=http_requests,
                   max_skill_calls=env_actions, per_skill_sim_timeout_s=BST_COMMAND_TIMEOUT_S,
                   wall_clock_s=wall_clock_s)


class AlfredRuntime(Runtime):
    """`Runtime` over one ALFWorld game. No method here chooses an action."""

    context_class = TextDecisionContext

    def __init__(self, backend, run_dir: str, budgets: Budgets, episode_id: str, *,
                 config: VerifyConfig | None = None, store: EpisodeStore | None = None,
                 max_episode_tokens: int = BST_EPISODE_TOKENS):
        self.backend = backend
        self.max_episode_tokens = int(max_episode_tokens)
        self.prompt_sha256 = catalogue_sha256()
        self._obs_key: tuple | None = None
        # which of the two events this backend cannot derive from a status/failure
        # pair actually happened; set at the exact place the loop handles them
        self._agent_finished = False
        self._env_terminated = False
        super().__init__(TextScene(), AlfredTextExecutor(backend), run_dir, budgets,
                         episode_id, config=config, environment=None, store=store)

    # ---------- observation channel ----------
    def observe(self) -> WorldState:
        """Read the response this process already has.

        A new environment step may return the same sentence, and that is a new
        measurement; reading the same response twice is not (SPEC-BST 4.3). The two
        are told apart by the step counter, never by handing out a fresh ref for a
        cached string — which is also what makes the repeat guard able to see that
        nothing arrived."""
        key = (str(self.backend.raw), int(self.backend.env_steps))
        if self.world is not None and key == self._obs_key:
            self.store.log("observation_cached", observation_ref=self.world.observation_ref,
                           state_version=self.world.state_version,
                           note="the same public response was read again; no new measurement "
                                "was taken and no ref was invented for it (SPEC-BST 4.3)")
            return self.world
        self._obs_key = key
        return super().observe()

    # ---------- backend seams ----------
    def _capture_world(self, state_version: int, observation_ref: str) -> WorldState:
        return text_world_state(self.backend.raw, state_version, observation_ref,
                                sim_time=self.scene.sim_time)

    def _verifier(self, world: WorldState):
        return TextVerifier(world, self.config)

    def _skill_catalogue(self) -> dict:
        """The static action description of this environment (SPEC-BST 4.5: 模型可见
        静态动作说明). Each entry carries the one command its skill maps to, because
        "one skill is one command" is a property of the world, not a hint about the
        task; nothing here says which command is worth trying next."""
        return {name: {**spec, "command": TEMPLATES[name][0]} for name, spec in CATALOGUE.items()}

    def _context_candidates(self, world: WorldState, progress) -> tuple[list, int]:
        """No dynamic candidate list exists for a text world, and none is inferred
        from the text: offering names the environment did not put in a geometry
        would be the runtime choosing (SPEC-BST 4.5)."""
        return [], 0

    def _fingerprint(self, world: WorldState) -> tuple:
        return text_fingerprint(world)

    def _state_diff(self, pre_world: WorldState, post_world: WorldState) -> dict:
        return text_state_diff(pre_world, post_world)

    def _verification_reports(self, call: SkillCall, result: SkillResult, pre_world: WorldState,
                              post_world: WorldState, verifier) -> list:
        if result.status == SkillStatus.rejected:
            # the command never left this process, so the environment said nothing
            # about an effect; the reasons are already in `rejection_reasons`
            return []
        return verifier.verify_action(call.skill, dict(call.args or {}))

    def _capture_terminal_snapshot(self) -> TerminalSnapshot:
        """One frozen public sample: the final response and the facts read out of
        it. No physics is stepped to get it and no official flag enters it — the
        snapshot is what the episode *saw*, and the verdict is read separately."""
        w = self.observe()
        return TerminalSnapshot(
            observation_ref=w.observation_ref, state_version=w.state_version,
            sim_time=self.scene.sim_time, settle_seconds=BST_SETTLE_S,
            entities={e.entity_id: {"attributes": dict(e.attributes), "held": str(e.held),
                                    "facts_named_here": sorted(
                                        fact_key(f) for f in w.facts
                                        if e.entity_id in (f.subject, f.related))}
                      for e in w.entities},
            targets={"public_response": {
                "text": w.raw_observation, "parse_status": w.parse_status,
                "unparsed": list(w.unparsed), "location": str(w.location),
                "note": "a text backend has no geometric target regions to freeze; this is "
                        "the last public observation, which is what this snapshot holds"}})

    def _episode_success(self, status: TerminalStatus, items, completed: int) -> bool:
        """Official success, read once, after the loop stopped.

        `items` is a single `unknown` row by construction (SPEC-BST 4.6), so the
        desktop rule "and every goal was measured true" could only ever answer
        False here — that would be a fabricated failure rate, not a measurement."""
        return bool(self.backend.eval_view().get("official_won"))

    def _backend_termination(self):
        view = self.backend.eval_view()
        if view.get("error"):
            # checked before `done`, because a fault and a lifecycle event can arrive
            # in the same response and only one of them is a measurement of the agent
            err = view["error"]
            return (TerminalStatus.failed, FailureCode.ENVIRONMENT_ERROR,
                    f"the text backend faulted during {err.get('phase')!r} "
                    f"({err.get('type')}): {str(err.get('message'))[:200]}. The episode ends "
                    f"here: whether the command reached the engine is not knowable from this "
                    f"side, so it is never replayed to find out, and the round is scored as "
                    f"infrastructure rather than a model failure (SPEC-BST 6.3)")
        if not view.get("env_done"):
            return None
        self._env_terminated = True
        won = bool(view.get("official_won"))
        return (TerminalStatus.success if won else TerminalStatus.failed, None,
                f"the environment terminated this episode after {view['env_steps']} native "
                f"actions; official success={won}. done may mean won or truncated, and this "
                f"classification is an evaluation result: it was never an observation and is "
                f"not the model's claim (SPEC-BST 6.2)")

    def _map_failure(self, result: SkillResult) -> FailureCode | None:
        """A completed command that the engine answered is not a failure, even when
        the answer was "Nothing happens." — that sentence is the result, and the
        desktop fallback (`ANOMALOUS_CONTACT`) would name a physics event that this
        backend cannot observe."""
        for fc in FailureCode:
            if fc.value == result.failure_code:
                return fc
        if result.status == SkillStatus.completed:
            return None
        return (FailureCode.ADAPTER_ERROR if result.status == SkillStatus.rejected
                else FailureCode.ENVIRONMENT_ERROR)

    # ---------- request accounting ----------
    def tokens_used(self) -> int:
        return self._billed("prompt_tokens") + self._billed("completion_tokens")

    def _conservative_next_estimate(self, ledger) -> int:
        """Spend so far plus one round's mean spend: the estimate of the request
        about to be sent (SPEC-BST 5.3: 硬限制使用发起请求前的保守估计). It is drawn
        from this episode's own measured traffic, so a longer prompt late in an
        episode is not estimated cheaper than it is, and the actual figure is
        reported beside the cap rather than replaced by the estimate."""
        spent = self.tokens_used()
        return spent + (spent // max(1, ledger.decision_rounds) if spent else 0)

    def _preflight_trip(self, ledger: BudgetLedger) -> tuple | None:
        if ledger.http_requests >= self.budgets.max_http_requests:
            return (TerminalStatus.failed, FailureCode.BUDGET_EXHAUSTED,
                    f"http request budget {self.budgets.max_http_requests} spent before the "
                    f"next request (SPEC-BST 5.3)")
        estimate = self._conservative_next_estimate(ledger)
        if estimate > self.max_episode_tokens:
            return (TerminalStatus.failed, FailureCode.BUDGET_EXHAUSTED,
                    f"episode token cap {self.max_episode_tokens} would be exceeded by the "
                    f"next request (spent {self.tokens_used()}, conservative estimate "
                    f"{estimate} from this episode's own mean)")
        return None

    # ---------- context ----------
    def _build_context(self, task, goal, world, progress, ledger) -> DecisionContext:
        ctx = super()._build_context(task, goal, world, progress, ledger)
        return ctx.model_copy(update={"budget": self._budget_view(ledger)})

    def _budget_view(self, ledger) -> dict:
        """The same remaining-budget claim, in this backend's own names: the
        desktop loop's `skill_calls` *is* the native environment action count here,
        and the two caps `Budgets` cannot carry are shown beside it."""
        view = ledger.remaining_payload()
        env = self.backend.eval_view()
        view.update({
            "env_actions_used": int(env["env_steps"]),
            "env_actions_total": int(env["max_env_actions"]),
            "episode_tokens_used": self.tokens_used(),
            "episode_tokens_total": self.max_episode_tokens,
            "note": "skill_calls_used is the native environment action count in this "
                    "backend: one legal execute sends exactly one command; "
                    "per_skill_sim_timeout_s is that command's own timeout",
        })
        return view

    # ---------- decision validation ----------
    def _validate_decision(self, decision: Decision, ctx: DecisionContext,
                           world: WorldState) -> tuple[bool, list[str]]:
        ok, errs = super()._validate_decision(decision, ctx, world)
        # the loop only acts on a `finish` that validated, so this is the moment the
        # agent ended the episode — not `_finish_check`, which `_finalize` also calls
        # to freeze a snapshot on every exit path. Reading that call as a finish would
        # label every truncated or blocked episode a false finish (SPEC-BST 6.2)
        if ok and decision.action == "finish":
            self._agent_finished = True
        return ok, errs

    def _validate_execution(self, decision: Decision, ctx: DecisionContext,
                            world: WorldState) -> tuple[bool, list[str]]:
        ex = decision.execute
        command, reasons = render(ex.skill, dict(ex.args or {}))
        if command is None:
            return False, [f"skill {ex.skill!r} does not map to one environment command: {r}"
                           for r in reasons]
        if ex.candidate_id:
            return False, ["this backend offers no placement candidate registry, so "
                           "execute.candidate_id must be null"]
        return True, []

    # ---------- terminal protocol ----------
    def _finish_check(self, goal: GoalSpec):
        """The agent's stop is honoured; its claim is neither accepted nor refuted.

        `TextVerifier.verify_goals` answers `unknown`, and the base class reads
        `overall == true` out of that as a rejection, which in this backend would
        mean re-asking until the model stopped claiming success — the only available
        answer to that question is the hidden official flag (SPEC-BST 4.5). So the
        flag is not consulted here: the episode ends, and `_finalize` compares the
        two afterwards."""
        snapshot = self._capture_terminal_snapshot()
        world = self.observe()
        snapshot = snapshot.model_copy(update={"observation_ref": world.observation_ref,
                                               "state_version": world.state_version})
        self.terminal_world = world
        self.terminal_snapshot = snapshot
        report = self._verifier(world).verify_goals(goal)
        self.store.log("finish_check", accepted=True, verifier=report.overall.value,
                       note="the episode ends at the agent's finish; the verifier of a text "
                            "task instruction does not exist in this backend, so no verdict "
                            "was manufactured to keep the loop going (SPEC-BST 6.2)")
        return True, report, snapshot

    def _build_feedback(self, decision, ctx, call, result, pre_world, post_world, goal):
        fb = super()._build_feedback(decision, ctx, call, result, pre_world, post_world, goal)
        if result.status == SkillStatus.failed and result.notes:
            # the frozen loop carries a *rejected* call's notes into the feedback row
            # and drops a failed one's, because on a physics backend the executed
            # stages say it all. Here those notes hold the one claim SPEC-BST 6.3
            # requires to be auditable — the command may have reached the engine and
            # is not sent again to find out — so they are logged beside the episode,
            # and the model-visible row stays exactly what the loop built.
            self.store.log("backend_fault_detail", call_id=result.call_id,
                           skill=call.skill, notes=list(result.notes),
                           env_steps=int(self.backend.env_steps),
                           note="the feedback row is unchanged; this record exists so the "
                                "indeterminate execution can be reconstructed afterwards "
                                "(SPEC-BST 6.3)")
        return fb

    def _record_feedback(self, fb):
        """Correct one assertion the desktop loop makes about itself.

        Its finish row claims a new measurement, because on a physics backend the
        settle step takes one. A text episode ends on the response it already holds,
        and SPEC-BST 4.3 forbids claiming a measurement for a cached read — so the
        claim is repaired against what this backend measured, and the repair itself
        is logged rather than made silently."""
        if (fb.skill is None and fb.new_measurement
                and fb.pre_observation_ref == fb.post_observation_ref):
            self.store.log("feedback_claim_corrected", decision_id=fb.decision_id,
                           observation_ref=fb.post_observation_ref,
                           note="new_measurement=False: the terminal read was the same cached "
                                "public response, not a fresh measurement (SPEC-BST 4.3)")
            fb = fb.model_copy(update={"new_measurement": False})
        super()._record_feedback(fb)

    def _termination_of(self, status: TerminalStatus, failure: FailureCode | None,
                        note: str) -> str:
        """SPEC-BST 6.2, in priority order: the two events this backend observes
        itself come first, then the guards and budgets of the frozen loop."""
        if self._env_terminated:
            return "ENV_TERMINATED"
        if self._agent_finished:
            return "AGENT_FINISH"
        if status == TerminalStatus.needs_clarification:
            return "NEEDS_CLARIFICATION"
        if failure in (FailureCode.ENVIRONMENT_ERROR,):
            return "ENVIRONMENT_ERROR"
        if failure in (FailureCode.ADAPTER_ERROR,):
            return "ADAPTER_ERROR"
        if failure in (FailureCode.MODEL_ERROR, FailureCode.PROVIDER_ERROR):
            return "MODEL_ERROR"
        if failure == FailureCode.REPEATED_INVALID:
            return "REPEAT_GUARD"
        if failure in (FailureCode.BUDGET_EXHAUSTED, FailureCode.TIMEOUT):
            return "BUDGET_EXHAUSTED"
        if failure == FailureCode.INVALID_DECISION:
            # the loop reaches this code at exactly one bound — `note` says which, and
            # a repeated invalid *action* arrives as REPEATED_INVALID instead, so the
            # two SPEC-BST 6.2 names stay distinguishable rather than being merged
            return "BUDGET_EXHAUSTED" if "budget" in note else "MODEL_ERROR"
        if failure is None:
            return "AGENT_BLOCKED"
        return "MODEL_ERROR"

    def _finalize(self, task, ledger: BudgetLedger, status: TerminalStatus,
                  failure: FailureCode | None, note: str, mode: str) -> EpisodeResult:
        if self.termination_reason is None:
            self.termination_reason = self._termination_of(status, failure, note)
        env = self.backend.eval_view()
        if self.termination_reason == "AGENT_FINISH":
            # the frozen loop calls its own finish branch `success`, because on a
            # physics backend an accepted finish *is* a verified one. Here a finish
            # is the agent's claim and nothing else, so leaving the status would
            # report an unverified claim as an outcome; the episode's status is the
            # official verdict, which is read at this moment and not earlier.
            won = bool(env.get("official_won"))
            self.store.log("terminal_status_restated",
                           from_status=status.value, to_status=(TerminalStatus.success.value
                                                               if won else TerminalStatus.failed.value),
                           note="an agent finish is not a verified success in a text backend; "
                                "only the official flag says whether the task was solved "
                                "(SPEC-BST 6.2)")
            status = TerminalStatus.success if won else TerminalStatus.failed
        self.store.log("termination", reason=self.termination_reason,
                       terminal_status=status.value, failure_code=failure.value if failure else None,
                       official_won=bool(env.get("official_won")), env_steps=int(env["env_steps"]),
                       env_done=bool(env.get("env_done")), note=note)
        result = super()._finalize(task, ledger, status, failure, note, mode)
        self.store.log("episode_official", official_success=bool(env.get("official_won")),
                       termination_reason=self.termination_reason,
                       false_finish=bool(self._agent_finished and not env.get("official_won")),
                       env_steps=int(env["env_steps"]),
                       episode_tokens=self.tokens_used(),
                       note="read after the loop stopped; never an input to a decision "
                            "(SPEC-BST 4.1, 6.2)")
        return result
