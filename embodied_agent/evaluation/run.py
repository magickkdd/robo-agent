"""Experiment runner — one code path for all three modes (SPEC 8, 11.2, 12.1.6).

What this file guarantees, and why each part is here:

* **A shared goal, resolved once per (case, repeat).** Every mode is handed the
  *same* `GoalSpec` object's JSON, so a difference between A, B and C can never
  be an artefact of three different parses. Its requests, tokens and wall-clock
  duration are billed into every one of the three episodes as a `prologue`
  (SPEC 11.2: 初始解析成本按每组逻辑执行一次报告), and the episode's wall clock
  is shifted back by the parse duration so no mode gets a cheaper budget than the
  run that physically paid for it. A shared parse that *fails* fails the whole
  trio — the pair is never dropped and the survivors never reported alone.
* **A fresh scene per episode, always closed.** The declared environment events
  are re-instantiated per episode (`case.fresh_events()`), otherwise the first
  mode would consume the perturbation and the other two would face a different
  scenario (SPEC 10.3).
* **Mode is a label on the source, not on the machinery.** Same executor,
  verifier, budgets, candidate registry, evaluator and settle protocol.
* **Every planned run enters the table.** A crash, a provider error and a
  refused decision are rows with their own `outcome` category, not absences
  (SPEC 11.4: 所有预定运行均进入可靠性分母).

Naming follows SPEC 7 and is deliberate: `recorded_replay` reads back what
actually executed (no physics), while `plan_rerun` re-executes the *last* plan of
an episode in a fresh scene. The latter is not a reproduction of the recorded
trajectory — only the final plan is replayed, so it can never contradict a
mid-episode decision — and it says so in its own output.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime

from ..adapters.deepseek import DeepSeekAdapter, DeepSeekPlanner, FixturePlanner, LLMError
from ..core.contracts import GoalSpec, SkillCall, TaskInput
from ..core.events import EpisodeStore, git_state, write_manifest
from ..core.fault_injection import EnvironmentController
from ..core.interpreter import interpret
from ..core.planner import OneShotPlanSource, RulePlanner
from ..core.runtime import Runtime
from ..core.scene import PhysicsScene
from ..core.skills import SkillExecutor
from ..core.verify import build_world_state
from .evaluator import IndependentEvaluator
from .preregistration import PREREG_PATH, check_prereg, load_prereg, matrix_mismatch
from .protocol import run_probe
from .sources import (
    AblatedFeedbackSource,
    LLMDecisionSource,
    RulePolicySource,
    llm_one_shot_plan,
)
from .tasks import FROZEN_PATH, TaskCase, build_set, check_frozen, frozen_manifest

MODES = ("A", "B", "C")
PLANNERS = ("rule", "fixture", "deepseek")
# The three provider counters a prologue may carry, mirroring the runtime's list.
PROLOGUE_COUNTERS = ("http_requests", "prompt_tokens", "completion_tokens", "api_errors",
                     "transport_retries", "format_repairs")


class InfraError(Exception):
    """The runner itself failed (scene would not build, artifact would not
    write). Such a run is reported as `infrastructure_error` and stays in the
    denominator; it is never folded into `failed`."""


def task_input(case: TaskCase) -> TaskInput:
    return TaskInput(task_id=case.task_id, utterance=case.utterance)


def build_scene(case: TaskCase, gui: bool = False) -> PhysicsScene:
    return PhysicsScene(seed=case.seed, object_layout=case.objects, gui=gui)


def _delta(planner, before: dict) -> dict:
    return {k: max(0, int(getattr(planner, k, 0) or 0) - int(before.get(k, 0) or 0))
            for k in PROLOGUE_COUNTERS if k != "http_requests"}


# What a provider call must leave behind in the record (SPEC 7): the text the model
# returned, and the metadata that identifies which response it was.
CALL_EVIDENCE = ("kind", "prompt_version", "provider", "requested_model", "returned_model",
                 "completion_id", "created", "finish_reason", "usage", "transport_attempts",
                 "transport_retries", "prompt_chars", "raw_chars", "raw_response",
                 "first_parse_error", "parse_error", "goal_semantic_problems")


def _evidence(meta: dict | None) -> dict:
    return {k: (meta or {}).get(k) for k in CALL_EVIDENCE if (meta or {}).get(k) is not None}


# ------------------------------------------------------------------ goal -----


@dataclass
class GoalResolution:
    """One shared goal parse, billed to each of the three modes."""

    case_id: str
    repeat: int
    planner: str
    goal: GoalSpec | None
    well_formed: bool
    error: str | None
    counters: dict[str, int]
    wall_s: float
    model: str
    prompt_version: str
    artifact: str = ""
    assignments: list = field(default_factory=list)
    provider_meta: dict = field(default_factory=dict)

    def prologue(self) -> dict:
        return {**{k: int(self.counters.get(k, 0)) for k in PROLOGUE_COUNTERS},
                "wall_start": time.time() - max(0.0, self.wall_s),
                "shared_goal": f"{self.case_id}__r{self.repeat}",
                "shared_goal_artifact": self.artifact}

    def as_record(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "goal"} | {
            "goal": self.goal.model_dump(mode="json") if self.goal else None}


def resolve_goal(case: TaskCase, repeat: int, planner, root: str,
                 log_call=None) -> GoalResolution:
    """Parse the utterance against the *initial* scene, once per (case, repeat).

    `planner=None` is the deterministic rule interpreter: it exists to make the
    harness runnable offline and is labelled `rule`, never reported as a model
    result. `FixturePlanner` is an offline double with the same interface;
    `DeepSeekPlanner` is the online one whose goal the experiment shares.
    """
    stamp = f"{case.task_id}__r{repeat}"
    path = os.path.join(root, "goal_resolutions", f"{stamp}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    kind = "rule" if planner is None else getattr(planner, "provider", "unknown")
    before = {k: int(getattr(planner, k, 0) or 0) for k in PROLOGUE_COUNTERS} if planner else {}
    t0 = time.time()
    scene = None
    try:
        scene = build_scene(case)
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        task = task_input(case)
        if planner is None:
            goal = interpret(task, world)
            meta = {"kind": "goal_parse", "provider": "rule", "model": "rule-interpreter",
                    "prompt_version": "no-prompt", "http_requests_this_call": 0,
                    "raw_response": json.dumps({"utterance": task.utterance}, ensure_ascii=False)}
        else:
            goal, meta = planner.parse_goal(task, world)
        counters = {**{"http_requests": int(meta.get("http_requests_this_call") or 0)},
                    **_delta(planner, before)}
        resolution = GoalResolution(
            case_id=case.task_id, repeat=repeat, planner=kind, goal=goal,
            well_formed=bool(goal.well_formed), error=None, counters=counters,
            wall_s=round(time.time() - t0, 3),
            # a record that says only "deepseek" cannot show which model answered;
            # the provider's own name for it is kept separately in `provider_meta`
            model=str(meta.get("model") or meta.get("requested_model") or kind),
            prompt_version=str(meta.get("prompt_version", "")),
            provider_meta=_evidence(meta),
            assignments=[[a.entity.entity_id or a.entity.attributes, a.target_id]
                         for a in goal.assignments])
    except Exception as e:  # noqa: BLE001 - a shared parse failing fails the trio
        counters = ({k: max(0, int(getattr(planner, k, 0) or 0) - int(before.get(k, 0) or 0))
                     for k in PROLOGUE_COUNTERS} if planner
                    else {k: 0 for k in PROLOGUE_COUNTERS})
        # a refused parse still carries the text that was refused: that text is the
        # evidence for *why*, and dropping it would leave only our own exception
        evidence = _evidence(getattr(e, "meta", None))
        resolution = GoalResolution(
            case_id=case.task_id, repeat=repeat, planner=kind, goal=None, well_formed=False,
            error=f"{type(e).__name__}: {e}", counters=counters,
            wall_s=round(time.time() - t0, 3),
            model=str(getattr(planner, "model", kind)),
            prompt_version=str(evidence.get("prompt_version", "")),
            provider_meta=evidence, assignments=[])
    finally:
        if scene is not None:
            scene.close()
    if log_call:
        log_call({"kind": "goal_parse", "case_id": case.task_id, "repeat": repeat,
                  "provider": kind, "error": resolution.error,
                  "model": resolution.model,
                  "assignments": resolution.assignments,
                  "http_requests_this_call": resolution.counters.get("http_requests", 0),
                  **resolution.provider_meta},
                 resolution.wall_s)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"task_id": case.task_id, "repeat": repeat, "seed": case.seed,
                   "utterance": case.utterance, "shared_by_modes": list(MODES),
                   **resolution.as_record(), "prologue": resolution.prologue()},
                  f, ensure_ascii=False, indent=2, default=str)
    resolution.artifact = path
    return resolution


def installed_module_refusals(condition: str, *, experience_store, skill_memory) -> list[str]:
    """Why a gate on an *uninstalled* module is a different experiment — on any channel.

    P5 runs the memory and acquisition arms on a camera as well as on the privileged loop, and the
    rule that keeps §9's rows unambiguous cannot depend on which one: `wo_episodic_memory` and
    `wo_skill_acquisition` are switches on things that must be there to be switched off. A batch
    without the store/library takes a different code path that merely happens to behave the same,
    which is the two-experiments-under-one-name failure the check exists to prevent — so it is
    stated once here and consulted by both channel branches rather than paraphrased twice.
    """
    from ..episodic.arm import EPISODIC_ARMS
    from ..planning.arm import PLANNING_ARMS

    reasons: list[str] = []
    if condition in EPISODIC_ARMS and condition not in PLANNING_ARMS and experience_store is None:
        reasons.append(
            f"arm {condition!r} needs --experience-store: the contrast is the module switched off "
            f"on a loop that has a store, not a loop built without one, or the two arms differ by "
            f"more than the gate")
    if condition == "wo_skill_acquisition" and skill_memory is None:
        reasons.append(
            f"arm {condition!r} needs --skill-memory: the arm switches §5.6's pipeline off *an "
            f"installed library*, and a loop built with no library at all takes a different code "
            f"path that merely happens to offer nothing")
    return reasons


# ---------------------------------------------------------------- source -----


def make_source(mode: str, case: TaskCase, planner, rt: Runtime, goal: GoalSpec,
                log_call=None, policy: str | None = None):
    """The only place the three arms differ (SPEC 11.2: same everything else).

    Mode A's plan is drawn from `rt.initial_context(...)` — the identical
    context a mode B round gets at t0 — and a failing one-shot request is the
    baseline's own outcome, not something to paper over with the rule plan.

    `policy` switches mode B's *control*, and is refused anywhere else. `rule` is the v0.1
    deterministic policy: it reads pending goals and the measured hold state and never a plan, so
    switching the plan off changes nothing it can see. `payload` is `planning.policy.PlanPolicy`,
    which reads `ctx.model_payload()` and nothing else — the same page a model would read. Without
    that second object there is no way to tell "the arm removed information" from "the decision
    maker never used the information", which is the whole of 9's planning contrast (P2-c's
    measurement, now reachable from this entry point).

    `memory` is one step further on the same logic: `episodic.policy.MemoryPolicy` is `PlanPolicy`
    with one extra input, the order of objects in the recalled experiences, so a `full` vs
    `wo_episodic_memory` difference under it is a difference in what the page said and nothing else.
    It is refused on a runtime with no experience store, because such an episode would read a page
    with no `recalled` key and be `payload` answering to two names — the one thing a policy switch
    must never produce.
    """
    if policy not in (None, "", "rule", "payload", "memory"):
        raise ValueError(f"unknown policy {policy!r}; declared: 'rule' (the v0.1 control), "
                         f"'payload' or 'memory' (both read only ctx.model_payload())")
    if policy in ("payload", "memory") and (mode != "B" or planner is not None):
        raise ValueError(
            f"--policy {policy} is the zero-spend control for mode B only: mode A's claim is a plan "
            f"drawn once at t0, mode C's is an ablated feedback stream, and a batch with a model "
            f"planner already has its decision maker")
    if policy == "memory" and getattr(rt, "experience_store", None) is None:
        raise ValueError(
            "--policy memory needs an episodic arm with a store on the runtime: with no "
            "`working_memory.recalled` section to read it is planning.policy.PlanPolicy under a "
            "second name, which is a renamed run rather than a contrast. Pass "
            "`--experience-store` (an empty file is a legitimate cold start)")
    if planner is None:
        rule = RulePlanner(case.verify)
        if mode == "A":
            ctx = rt.initial_context(task_input(case), goal)
            return OneShotPlanSource(rule.plan(goal, ctx.world, plan_id=f"p_{ctx.context_id}"))
        if policy == "payload":
            from ..planning.policy import PlanPolicy

            return PlanPolicy()
        if policy == "memory":
            from ..episodic.policy import MemoryPolicy

            return MemoryPolicy()
        source = RulePolicySource(case.verify)
    else:
        if mode == "A":
            ctx = rt.initial_context(task_input(case), goal)
            return llm_one_shot_plan(planner, ctx, per_action_extra_repeats=2, log=log_call)
        source = LLMDecisionSource(planner, log=log_call)
    return AblatedFeedbackSource(source) if mode == "C" else source


class _FramedExecutor(SkillExecutor):
    """Same executor, one render after each skill boundary.

    The frames index the event log visually; they are produced by the physics
    that ran, and nothing reads them to decide anything.
    """

    def __init__(self, *a, frame_dir: str | None = None, **kw):
        super().__init__(*a, **kw)
        self.frame_dir = frame_dir
        self._frames = 0

    def execute(self, call: SkillCall):
        result = super().execute(call)
        if self.frame_dir:
            os.makedirs(self.frame_dir, exist_ok=True)
            self._frames += 1
            try:
                self.scene.render(os.path.join(
                    self.frame_dir, f"frame_{self._frames:03d}_{call.skill}.png"))
            except Exception:  # noqa: BLE001 - a missing frame must not end a run
                pass
        return result


# --------------------------------------------------------------- episode -----


def run_one_episode(case: TaskCase, repeat: int, mode: str, resolution: GoalResolution,
                    planner, root: str, set_name: str, *, frames: bool = True,
                    perceive: str = "privileged", ablation=None, adapter=None,
                    views: tuple[str, ...] | None = None,
                    default_view: str = "main", policy: str | None = None,
               experience_store=None, skill_memory=None, propose: str = "rule",
                    propose_ask=None, validation_budget: int = 1) -> dict:
    """One episode, start to finish, with its artifacts.

    Nothing here decides *what to do*: the source chooses, the runtime executes
    and validates, the evaluator scores afterwards. A raised error escapes to
    `run_group`, which records it rather than retrying — a retry would be the
    runner repairing the sample it is supposed to report.

    `perceive` chooses which arm the loop runs in and is the only place that decision is
    made (SPEC-v0.2 §9, §13 P1-e). Its default is the v0.1 path: `build_arm("privileged")`
    hands back a plain `Runtime`, so an untouched call to this function is the sealed
    baseline, not a new implementation of it. `PerceptionUnavailable` escapes here too, for
    the same reason a crash escapes: an episode whose camera never answered is not a
    failure the arm earned.
    """
    episode_id = f"{case.task_id}.{mode}.r{repeat}"
    ep_dir = os.path.join(root, "episodes", episode_id)
    os.makedirs(ep_dir, exist_ok=True)
    started = time.time()
    scene = build_scene(case)
    try:
        controller = EnvironmentController(scene, case.fresh_events())
        executor = _FramedExecutor(scene, world_provider=lambda: None,
                                   frame_dir=ep_dir if frames else None, config=case.verify)
        store = EpisodeStore(ep_dir, episode_id)

        def log_call(payload, latency_s):
            """The episode's request ledger. One file, one row shape, whatever asked.

            Both writers land here: the decision source calls it with `(payload, latency_s)`,
            and the camera's reader calls `log_perception_call` with a single row it has
            already assembled. The second writer is v0.2 residual #109 — `VLMReader` built
            that row for every vision request including the ones that died, and
            `observe.py:_file_row` returned at once when handed no callback, so a real
            `--perceive vlm` episode spent money and left the Cost group a denominator of
            zero. A batch is billed from this file, so a request that does not appear here
            did not happen as far as any later reading is concerned."""
            _append_jsonl(os.path.join(ep_dir, "model_calls.jsonl"),
                          {**payload, "latency_s": round(latency_s, 3),
                           "episode_id": episode_id, "mode": mode, "case_id": case.task_id,
                           "repeat": repeat, "recorded_at": time.time()})

        def log_perception_call(row):
            """The reader's one-argument callback, folded into the same ledger.

            Its row already carries `latency_s` from the adapter's `_sent_meta`, plus the
            per-call token deltas and the `#155` keys, so it is summed the same way a
            decision row is; `ok=False` rows are the ones a refusal cost."""
            log_call(row, float(row.get("latency_s") or 0.0))

        if perceive == "privileged" and ablation is None and experience_store is None \
                and skill_memory is None:
            runtime = Runtime(scene, executor, ep_dir, case.budgets, episode_id,
                              config=case.verify, environment=controller, store=store)
        elif perceive == "privileged" and skill_memory is not None:
            # §5.6's arm, and it is checked *before* the memory arm because it is the wider one:
            # `build_skill_arm` stacks acquisition on the episodic arm and passes an
            # `experience_store` through, so a batch that installed a library gets the whole v0.2
            # loop and a batch that installed only a store keeps P3's code path untouched. Refusing
            # a camera channel here is the store's own refusal, one module later.
            from ..acquisition.arm import build_skill_arm

            runtime = build_skill_arm(case=case, scene=scene, executor=executor, store=store,
                                      run_dir=ep_dir, episode_id=episode_id,
                                      budgets=case.budgets, perceive=perceive,
                                      ablation=ablation, environment=controller,
                                      experience_store=experience_store, task_kind=set_name,
                                      skill_memory=skill_memory, ask=propose_ask,
                                      proposer=propose, validation_budget=validation_budget)
        elif perceive == "privileged" and experience_store is not None:
            # 9's *memory* arm: the plan, the ledger, the re-derivation and now the store, on the
            # zero-spend channel. `set_name` is the §5.4 任务上下文 kind term — the loop cannot
            # invent it from the utterance without making the experiment's fact into the runtime's
            # opinion, and a store full of `long_horizon` rows would otherwise be queried by a
            # batch that never said which set it was running.
            from ..episodic.arm import build_episodic_arm

            runtime = build_episodic_arm(case=case, scene=scene, executor=executor, store=store,
                                         run_dir=ep_dir, episode_id=episode_id,
                                         budgets=case.budgets, perceive=perceive,
                                         ablation=ablation, environment=controller,
                                         experience_store=experience_store, task_kind=set_name)
        elif perceive == "privileged":
            # A privileged batch with a declared arm is 9's *planning* contrast: the plan, the
            # ledger and the re-derivation are gated, and the vision model was never in the loop
            # to begin with. `build_planning_arm` files that asymmetry in the record's own `notes`
            # rather than hiding it, which is what makes a `full` row here readable as "planning
            # full, no camera" and not as the whole system.
            from ..planning.arm import build_planning_arm

            runtime = build_planning_arm(case=case, scene=scene, executor=executor,
                                         store=store, run_dir=ep_dir, episode_id=episode_id,
                                         budgets=case.budgets, perceive=perceive,
                                         ablation=ablation, environment=controller)
        elif skill_memory is not None or experience_store is not None:
            # §13 P5's closed loop: the camera channel with the memory arm, the library, or both
            # installed. The perceiver and the grounding map come from
            # `perception/arm.py:build_perceiver` — the very function `build_arm` calls — so "how a
            # look is wired" stays stated in one place, and a joined arm cannot grow a second
            # opinion about which body `obj_red_1` names. Each builder still refuses a perceiver it
            # was not handed, and `PerceptRuntime.__init__` still runs `arm_coherence`: joining the
            # arms adds a capability and removes no gate.
            from ..perception.arm import arm_for_channel, build_perceiver
            from .calibration import cell_catalog

            perceiver, gmap = build_perceiver(case=case, scene=scene, run_dir=ep_dir,
                                              episode_id=episode_id, perceive=perceive,
                                              adapter=adapter, views=views,
                                              catalog=cell_catalog(scene.trays.values()),
                                              default_view=default_view,
                                              on_call=log_perception_call)
            channel_arm = arm_for_channel(perceive, ablation)
            if skill_memory is not None:
                # Checked first for the reason the privileged branch above gives: the acquisition
                # arm stacks on the episodic one and passes the store through, so one builder
                # assembles the whole loop.
                from ..acquisition.arm import build_skill_arm

                runtime = build_skill_arm(case=case, scene=scene, executor=executor, store=store,
                                          run_dir=ep_dir, episode_id=episode_id,
                                          budgets=case.budgets, perceive=perceive,
                                          ablation=channel_arm, environment=controller,
                                          perceiver=perceiver, gmap=gmap,
                                          experience_store=experience_store, task_kind=set_name,
                                          skill_memory=skill_memory, ask=propose_ask,
                                          proposer=propose, validation_budget=validation_budget)
            else:
                from ..episodic.arm import build_episodic_arm

                runtime = build_episodic_arm(case=case, scene=scene, executor=executor,
                                             store=store, run_dir=ep_dir,
                                             episode_id=episode_id, budgets=case.budgets,
                                             perceive=perceive, ablation=channel_arm,
                                             perceiver=perceiver, gmap=gmap,
                                             environment=controller,
                                             experience_store=experience_store,
                                             task_kind=set_name)
        else:
            from ..perception.arm import build_arm
            from ..perception.grounding import GroundingMap
            from .calibration import cell_catalog

            runtime = build_arm(case=case, scene=scene, executor=executor, store=store,
                                run_dir=ep_dir, episode_id=episode_id, perceive=perceive,
                                catalog=cell_catalog(scene.trays.values()),
                                gmap=GroundingMap.from_objects(case.objects),
                                budgets=case.budgets, environment=controller,
                                ablation=ablation, adapter=adapter, views=views,
                                default_view=default_view,
                                on_call=log_perception_call)
        executor.world_provider = runtime.observe

        source = make_source(mode, case, planner, runtime, resolution.goal, log_call,
                             policy=policy)
        result = runtime.run_episode(task_input(case), resolution.goal, source, mode=mode,
                                     prologue=resolution.prologue())
        events = store.read_all()
        snapshot = runtime.terminal_snapshot
        score = IndependentEvaluator(snapshot, case.eval_spec).score(result)
        probe = run_probe(case, result, score, events,
                          {"needs_clarification": str(result.failure_type or "")})
        perceiver = getattr(runtime, "perceiver", None)
        perception = {
            "channel": perceive,
            "arm": getattr(getattr(runtime, "ablation", None), "condition", "unset"),
            "views": sorted(perceiver.views) if perceiver else None,
            "default_view": perceiver.default_view if perceiver else None,
            "looks": len(perceiver.percepts) if perceiver else 0,
            "look_tokens": perceiver.usage() if perceiver else {},
            "look_http_requests": perceiver.http_requests if perceiver else 0,
            "grounding_map_sha256": (runtime.gmap.sha256() if perceiver else None),
            "perception_events": sum(1 for e in events if e["type"] == "perception"),
            "ablation_events": sum(1 for e in events if e["type"] == "ablation"),
        }
        summary = {
            "episode_id": episode_id, "case_id": case.task_id, "set": set_name,
            "subset": case.subset, "mode": mode, "repeat": repeat,
            "expected": case.expected, "probe": probe, "score": score,
            "perception": perception,
            # Who answered the rounds, named from the object that answered them. An arm's row is
            # only readable if the row says whether the decision maker could see the plan at all:
            # the v0.1 control reads pending goals and no plan, so without this field a `full` vs
            # `wo_planning` difference and a policy swap would look identical in the table.
            "decision_source": {"mode": mode, "policy": policy or "rule",
                                "provider": getattr(source, "provider", "unknown")},
            "result": result.model_dump(mode="json"),
            "goal_resolution": {"planner": resolution.planner, "artifact": resolution.artifact,
                                "shared": True, "counters": resolution.counters,
                                "wall_s": resolution.wall_s, "assignments": resolution.assignments},
            "environment_events": {"declared": controller.declared(),
                                   "unfired": controller.unfired(),
                                   "fired": [a.get("event_id") for a in controller.applied]},
            "artifacts": {"episode_dir": ep_dir, "events": store.path,
                          "terminal_snapshot": snapshot.observation_ref if snapshot else None,
                          "frames": int(getattr(executor, "_frames", 0))},
            "wall_time_s": round(time.time() - started, 2),
        }
        # §5.4's write, and the only place the store is touched by a run. It goes through the same
        # `experience_from_episode` an offline reader calls on the archive, so a row a batch acted
        # on cannot differ from a row somebody rebuilds afterwards; a batch that differed would be
        # measuring a memory nobody could check. The store's state is captured on both sides of it
        # because a run that appended nothing and a run that appended one row look the same in the
        # counts alone, and RQ3 is answered from this field rather than from the log.
        writer = getattr(runtime, "write_experience", None)
        if writer is not None:
            before = list(experience_store.ids()) if experience_store is not None else []
            experience = writer(summary, run_ref=ep_dir)
            summary["episodic"] = {
                "arm": str(getattr(getattr(runtime, "ablation", None), "condition", "unset")),
                "store_path": getattr(experience_store, "path", None),
                "store_size_before": len(before),
                "experiences_before": before,
                "retrievals": getattr(runtime, "retrievals", 0),
                "rows_retrieved": getattr(runtime, "retrieved_rows", 0),
                "rows_used": getattr(runtime, "used_rows", 0),
                "written_experience_id": (experience.experience_id if experience else None),
                "store_size_after": (len(experience_store.ids()) if experience_store else 0),
                "store_fingerprint": (experience_store.fingerprint() if experience_store else None),
                "memory_events": {key: sum(1 for e in store.read_all() if e["type"] == key)
                                  for key in ("memory_retrieval", "memory_use", "memory_write")},
            }
        # §5.6's five boxes, run over the episode that just finished, and the only place the skill
        # memory is touched by a run. It sits *after* `memory_write` for the same reason that write
        # sits after `episode_end`: a gap is a claim about a finished run's records, and a pipeline
        # that repaired the capability set mid-episode would be a runtime making a strategic choice
        # about its own library (§9). The report is kept whether or not anything was acquired —
        # including on `wo_skill_acquisition`, where it says so with the stages listed as
        # not attempted — because §11's candidate-generation rate needs the episodes that produced
        # no candidate as much as the ones that produced three, and an absent key cannot tell the two
        # apart. `Ablation.violations()` is what proves the disarmed arm filed no `skill_*` record.
        acquirer = getattr(runtime, "acquire_from_episode", None)
        if acquirer is not None:
            acquisition = acquirer(summary, run_ref=ep_dir)
            if acquisition is not None:
                acquisition["events_filed_in_log"] = {
                    key: sum(1 for e in store.read_all() if e["type"] == key)
                    for key in ("skill_gap", "skill_candidate", "skill_validation",
                                "skill_library")}
                acquisition["store_size_after"] = (len(skill_memory.entries)
                                                   if skill_memory is not None else 0)
                acquisition["refusals_in_store"] = (len(skill_memory.refusals)
                                                    if skill_memory is not None else 0)
                summary["skill_acquisition"] = acquisition
        # What the control policy says it saw, per round. `PlanPolicy.trace` and
        # `MemoryPolicy.memory_order` / `memory_declined` exist only on the policy object, which is
        # built here and thrown away when the episode ends, so without this line §11's memory rows
        # are unmeasurable: `retrieval relevance` and `stale memory usage` can be read off the
        # `memory_retrieval` records, but *successful reuse* and *negative transfer* are claims about
        # what the recall did to the choice, and the only artifact of a choice is a rationale that
        # names it. Filed once, at the end, next to the trajectory it explains.
        trace = getattr(source, "trace", None)
        if trace:
            summary["policy"] = {
                "provider": getattr(source, "provider", "rule"),
                "rounds": len(trace),
                "recalled_ids": sorted({str(x) for entry in trace
                                        for x in (entry.get("recalled") or [])}),
                "declines": sorted({str(k) for entry in trace
                                    for k in (entry.get("memory_declined") or {})}),
                "followed_rounds": [entry["round_index"] for entry in trace
                                    if entry.get("memory_followed")],
                "trace": list(trace)}
        with open(os.path.join(ep_dir, "episode_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        return summary
    finally:
        scene.close()


# ------------------------------------------------------------------ group -----


def _append_jsonl(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def goal_logger(root: str):
    """A `log_call` sink that collects a run's goal parses into one ledger, so the
    single-episode entry point leaves the same model-exchange evidence a batch does."""
    path = os.path.join(root, "goal_resolutions", "goal_calls.jsonl")

    def _log(payload, latency_s):
        _append_jsonl(path, {**payload, "latency_s": round(latency_s, 3)})

    return _log


#: the per-episode request ledger's name. Named here because `batch_spend` below is about the
#: two ledgers together and a driver should not have to spell the filename twice.
LEDGER_NAME = "model_calls.jsonl"
#: ... and the run-level one, which is where the goal parses go.
GOAL_LEDGER_NAME = os.path.join("goal_resolutions", "goal_calls.jsonl")


def batch_spend(run_root: str) -> dict:
    """What a run root actually cost, from **both** of its ledgers.

    A run's requests live in two files, and that is not a design choice so much as an
    arithmetic fact: §7 bills the goal parse once per `(case, repeat)`, shared across the
    modes, and it happens *before* any episode directory exists — so those rows cannot belong
    to one episode and go to the run-level `goal_resolutions/goal_calls.jsonl`, while every
    decision and every look goes to the episode's own `model_calls.jsonl`.

    A driver that reads only the second file therefore **understates the batch**, and this was
    measured rather than suspected on the v0.3 E1 batch: 2,936 requests from the per-episode
    ledgers against 3,292 in the two ledgers together — 356 missing, 10.8%, all of it the goal
    parses. The v0.3 report first put that gap at 281, which was wrong: 281 is what the
    instrument's own counter said, and it was short by 75 for a separate reason (below), so
    deriving the gap by subtraction imported the instrument's blind spot instead of measuring it.

    A second, independent blind spot showed up in the same batch: **15 of the 262 goal parses
    exhausted all five attempts and errored** (HTTP 429). Those rows carry no usage, so a reader
    that tallies tokens sees no gap at all — tokens agreed with the instrument to the last unit.
    But each of those rows still made 5 requests, and the provider's rate limiter is the actual
    constraint on this channel. So there are two numbers and neither is "the" spend: 3,217
    token-bearing requests, 3,292 requests made. The difference is exactly 15 x 5 = 75.

    `http_requests_this_call` is the per-call figure and `http_requests_total` is a
    **cumulative** counter carried on every row; summing the latter across rows counts each
    request once per row after it — 187,279 against a true 2,936 on this batch, a 64x
    overstatement. Both mistakes have been made on this project and both are named in the
    docstrings of the callers that made them.

    Tokens are nested under `usage`, not at the top level. The return carries the two ledgers
    separately so a reader can see which file a number came from, and `http_requests` is their
    sum — the number a bound has to be compared against.
    """
    import glob as _glob

    def _rows(path):
        if not os.path.exists(path):
            return []
        out = []
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _tally(rows):
        prompt = completion = http = calls = 0
        failed_rows = failed_http = 0
        for r in rows:
            usage = r.get("usage") or {}
            prompt += int(usage.get("prompt_tokens") or 0)
            completion += int(usage.get("completion_tokens") or 0)
            this_call = int(r.get("http_requests_this_call") or 0)
            http += this_call
            calls += 1
            if r.get("error"):
                # A row that ended in an error still made requests. On this provider those are
                # 429s: they return nothing, so they carry no usage, but they did consume
                # rate-limit quota — and that quota is what throttles the batch. Counting only
                # the token-bearing rows understates the pressure the run actually created, so
                # the two are reported separately and neither is presented as "the" spend.
                failed_rows += 1
                failed_http += this_call
        lat = [float(r["latency_s"]) for r in rows if r.get("latency_s") is not None]
        return {"rows": calls, "http_requests": http, "prompt_tokens": prompt,
                "completion_tokens": completion, "errored_rows": failed_rows,
                "errored_http_requests": failed_http,
                "http_requests_successful": http - failed_http,
                "latency_s_min": round(min(lat), 3) if lat else None,
                "latency_s_max": round(max(lat), 3) if lat else None}

    per_episode = []
    for path in sorted(_glob.glob(os.path.join(run_root, "episodes", "*", LEDGER_NAME))):
        t = _tally(_rows(path))
        t["episode"] = os.path.basename(os.path.dirname(path))
        per_episode.append(t)
    goal = _tally(_rows(os.path.join(run_root, GOAL_LEDGER_NAME)))

    def _sum(key):
        return sum(t[key] for t in per_episode) + (goal[key] if key in goal else 0)

    return {
        "per_episode_ledgers": {
            "files": len(per_episode),
            "rows": sum(t["rows"] for t in per_episode),
            "http_requests": sum(t["http_requests"] for t in per_episode),
            "http_requests_successful": sum(t["http_requests_successful"] for t in per_episode),
            "errored_rows": sum(t["errored_rows"] for t in per_episode),
            "errored_http_requests": sum(t["errored_http_requests"] for t in per_episode),
            "prompt_tokens": sum(t["prompt_tokens"] for t in per_episode),
            "completion_tokens": sum(t["completion_tokens"] for t in per_episode),
        },
        "goal_parse_ledger": goal,
        "http_requests": _sum("http_requests"),
        "http_requests_successful": _sum("http_requests_successful"),
        "errored_rows": _sum("errored_rows"),
        "errored_http_requests": _sum("errored_http_requests"),
        "prompt_tokens": _sum("prompt_tokens"),
        "completion_tokens": _sum("completion_tokens"),
        "per_episode": per_episode,
        "note": "the batch's spend is the sum of both ledgers; the goal-parse rows are run-level "
                "because §7 bills them once per (case, repeat), shared across modes, before any "
                "episode directory exists. `http_requests` counts every request made, including "
                "the ones that errored (429s: no usage, but rate-limit quota consumed); "
                "`http_requests_successful` is the token-bearing subset. A bound is checked "
                "against `http_requests` — a request that was throttled still happened.",
    }


def _infra_row(case: TaskCase, repeat: int, mode: str, where: str, error: str,
               wall_s: float, set_name: str, perceive: str = "privileged") -> dict:
    return {"episode_id": f"{case.task_id}.{mode}.r{repeat}", "case_id": case.task_id,
            "set": set_name, "subset": case.subset, "mode": mode, "repeat": repeat,
            "expected": case.expected, "outcome": "infrastructure_error",
            # the arm a crashed episode belonged to, because §11's arms are counted over
            # every planned run: a row that lost its channel would be pooled into another
            # arm's denominator
            "perception": {"channel": perceive, "looks": 0},
            "infrastructure_error": {"where": where, "error": error, "traceback_tail": ""},
            "wall_time_s": round(wall_s, 2), "probe": {}, "score": {},
            "result": {"terminal_status": "failed", "failure_type": None}}


def _record_infra(root: str, row: dict) -> None:
    """Every row that never reached an episode summary gets its cause in the batch's one
    errors ledger, whichever path produced it (v0.4 R1: E2's c3 rows prove the in-episode
    path already did this; E1's sixteen goal_resolution errors prove the other two paths
    did not — and which path a dead episode takes is not a fact a reader can see, so
    coverage cannot be left to chance). The row keeps its identity and its final outcome:
    a `goal_resolution_error` is a different answer to "why is there no result" than an
    `infrastructure_error`, and the ledger is where that question is asked."""
    _append_jsonl(os.path.join(root, "errors.jsonl"),
                  {**row["infrastructure_error"],
                   "episode_id": row["episode_id"], "case_id": row["case_id"],
                   "mode": row["mode"], "repeat": row["repeat"],
                   "outcome": row["outcome"]})


def _row_of(summary: dict) -> dict:
    return {**summary, "outcome": (summary.get("result") or {}).get("terminal_status", "unknown")}


def _batch_is_offline(planner_kind: str, perceive: str) -> bool:
    """Whether this batch can spend at all — asked of the batch, not of the decision seat.

    It used to be `planner_kind != "deepseek"`, which is one half of the question. A
    `--planner rule --perceive vlm` batch asks a vision model one question per look, so it
    spends while its planner says `rule`; measured on this round, such a manifest reported
    `offline: true` over a billed 4096-token generation. The Cost group is read off the
    columns next to this flag, so a flag that under-reports spend corrupts the reading it
    is supposed to qualify — the same argument §7 makes about not filing a VLM episode as
    free.

    `privileged` and `stub` consult no model, so they cost nothing whatever the planner is;
    `vlm` always costs, and it is refused above unless a vision config was supplied, so by
    the time this is asked the answer is yes."""
    return perceive != "vlm" and planner_kind != "deepseek"


def _api_key_env_of(model_config: str | None) -> str:
    """The NAME of the env var a model config declares, for a refusal to quote.

    Read with the same loader the adapter uses, so the sentence cannot name a different
    variable than the one that failed. The value is never touched (SPEC 7)."""
    import yaml

    try:
        with open(model_config, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return "(unreadable config; the adapter's own default applies)"
    return str(cfg.get("api_key_env", "DEEPSEEK_API_KEY"))


def run_group(set_name: str, *, modes=MODES, planner_kind: str = "rule", repeats: int = 1,
              out_root: str = "runs", case_ids: list[str] | None = None,
              limit: int | None = None, frames: bool = True,
              model_config: str | None = None, frozen_path: str | None = None,
              prereg_path: str | None = None, perceive: str = "privileged",
              ablation=None, views: tuple[str, ...] | None = None,
              default_view: str = "main", policy: str | None = None,
               experience_store=None, skill_memory=None, propose: str = "rule",
               propose_ask=None, validation_budget: int = 1, adapter=None) -> dict:
    """Run `cases x modes x repeats` with the modes interleaved.

    Interleaving is a measurement decision: a provider that changes behaviour
    mid-batch then perturbs all three arms of a pair equally instead of
    contaminating one side of every comparison (SPEC 11.2).

    One batch is one perception arm. `perceive` therefore goes into the run id and the
    manifest as well as into every episode row: a directory that mixed a privileged world and
    a camera would hold rows that look comparable and are not, and §12.1's "same task, model
    and environment across arms" is a claim about a batch, so which arm a batch ran in has to
    be readable from the batch."""
    if planner_kind not in PLANNERS:
        raise ValueError(f"--planner must be one of {PLANNERS}")
    # The control policy is refused here, before a run directory exists, for the same reason the
    # arm is: `payload` replaces mode B's decision maker, so a batch that asked for it while
    # handing that mode to a model planner, or while asking for mode A's one-shot plan, would
    # produce rows whose source nobody could name afterwards.
    if policy not in (None, "", "rule", "payload", "memory"):
        raise InfraError(f"unknown policy {policy!r}; declared: 'rule' (the v0.1 control), "
                         f"'payload' (planning.policy.PlanPolicy) or 'memory' "
                         f"(episodic.policy.MemoryPolicy), both of which read only the model "
                         f"payload")
    if policy in ("payload", "memory") and (planner_kind != "rule" or set(modes) != {"B"}):
        raise InfraError(
            f"--policy {policy} needs modes=B and planner=rule (got modes={list(modes)}, "
            f"planner={planner_kind}): it is the zero-spend control that answers from "
            f"ctx.model_payload(). Mode A's claim is one plan drawn at t0, mode C ablates the "
            f"feedback stream, and a model planner already has its own decision maker")
    if policy == "memory" and experience_store is None:
        raise InfraError(
            "--policy memory needs --experience-store: with no store installed the runtime files "
            "no retrieval record and writes no residue, so the batch would run planning.policy."
            "PlanPolicy under a second name and report it as a memory arm")
    if experience_store is not None and len(set(modes)) > 1:
        # The store is batch-level state, and §7's modes are interleaved *within* a case. With A, B
        # and C all writing a residue for the same task, C's episode would retrieve a memory A's
        # episode produced seconds earlier: the three arms would no longer start from one state, so
        # §12.1's "same task, model and environment across arms" would be false of the batch and
        # §13's RQ3 (a *later similar* task) would be answered about the same task instead.
        raise InfraError(
            f"a memory batch runs one mode, got {sorted(set(modes))}: modes are interleaved per "
            f"case, and each episode appends to the store the next one reads, so a multi-mode "
            f"batch would let mode C of a task retrieve the residue mode A of the same task "
            f"produced. Run the memory arms as separate single-mode batches")
    if propose not in ("rule", "model"):
        raise InfraError(f"unknown proposer {propose!r}; declared: 'rule' "
                         f"(acquisition.propose.rule_candidate, zero spend) or 'model' "
                         f"(acquisition.propose.model_candidate, which bills)")
    if propose == "model" and propose_ask is None and skill_memory is not None:
        # The refusal is about the batch, not the module: `model_candidate` takes an `ask` callable
        # and has no offline fallback, so asking for a model proposer without wiring one would turn
        # §5.6's proposal step into an exception inside every episode of the run.
        raise InfraError("--propose model needs a caller-supplied asker (propose_ask=): "
                         "acquisition.propose.model_candidate has no built-in provider and no "
                         "offline fallback, and a batch that spends has to say who it is spending "
                         "with. A skill proposal is also a *model* decision, so this run is no "
                         "longer the zero-spend control the rest of v0.2 measures")
    if skill_memory is not None and len(set(modes)) > 1:
        # The same argument the store gets above, one module later: the library is batch-level state
        # that an episode both reads (the offer) and writes (an admission), so interleaving modes
        # would let one mode's episodes reuse a program another mode's episode acquired. §9's
        # `w/o Skill Acquisition` contrast is two batches that differ by the gate, not one batch
        # whose second half is contaminated by its first.
        raise InfraError(
            f"a skill-acquisition batch runs one mode, got {sorted(set(modes))}: episodes read the "
            f"library through the offer and write it through admission, so a multi-mode batch would "
            f"compare an arm against itself. Run the acquisition arms as separate single-mode "
            f"batches")
    cases = build_set(set_name)
    if case_ids:
        cases = [c for c in cases if c.task_id in set(case_ids)]
    if limit:
        cases = cases[:limit]
    if not cases:
        raise ValueError(f"no cases selected from set {set_name!r}")

    planner = None
    if planner_kind == "fixture":
        planner = FixturePlanner()
    elif planner_kind == "deepseek":
        planner = DeepSeekPlanner.from_env(model_config)

    # A camera channel needs an adapter whether or not the *decision* seat has one. Before
    # this, `--perceive vlm` was reachable only together with `--planner deepseek`, because the
    # adapter came along inside the planner — which made the cheapest `full`-on-VLM contrast
    # (swap the perception channel, keep the rule policy) impossible to even ask for, and left
    # the gate below answering `channel_readiness` with `adapter=None` on every other path.
    #
    # It is built *after* that gate on purpose. The gate's question is answered by the config
    # file alone — does it declare `vision` — while building the adapter also needs a loadable
    # key, and a config that declares vision without a key behind it is a different refusal
    # with a different sentence. Reading the YAML first means each failure is named by the
    # thing that actually failed. The planner's own adapter is reused when it has one, so a
    # batch that put the model in both seats reports one `config_sha256` rather than two
    # constructions of one file.
    def _camera_adapter():
        nonlocal adapter
        if adapter is not None or perceive != "vlm" or not model_config:
            return adapter
        adapter = getattr(planner, "adapter", None)
        if adapter is not None:
            return adapter
        try:
            adapter = DeepSeekAdapter.from_config(model_config)
        except LLMError as e:
            raise InfraError(
                f"--perceive vlm with --model-config {model_config!r} cannot be built: {e}. "
                f"Export {_api_key_env_of(model_config)} (that is the NAME the config declares; "
                f"its value is never recorded, and this process reads it from the environment or "
                f"from the repo-local .env) and the gate above will still pass on the same config, "
                f"because it only ever read the file") from None
        return adapter

    # The arm is resolved before a run directory exists, for the same reason the
    # pre-registration is: a refusal must not leave a half-written batch behind, and a batch
    # that ran under an arm nobody declared is not an arm, it is a contaminated comparison.
    arm = None
    if perceive == "privileged":
        # P2-e puts 9's *planning* arms on the zero-spend channel, so a privileged batch with a
        # declared condition has to be one this channel can mean. `arm_coherence` is deliberately
        # not consulted: both of its rules are about the `perception` record, which a privileged
        # world never produces, and applying it here would refuse every planning arm because none
        # of them turns the vision model off. The disclosure the check would have made instead —
        # that this `full` consulted no model — is filed by the arm in the record's `notes` and
        # repeated in the manifest below, where a reader finds it before a number.
        if ablation is not None:
            from ..acquisition.arm import SKILL_ARMS
            from ..core.v02 import ABLATION_CONDITIONS
            from ..core.v02 import ablation as ablation_record

            condition = getattr(ablation, "condition", ablation)
            if condition not in ABLATION_CONDITIONS:
                raise InfraError(f"unknown ablation condition {condition!r}; registered: "
                                 f"{sorted(ABLATION_CONDITIONS)}")
            if condition not in SKILL_ARMS:
                raise InfraError(
                    f"channel 'privileged' cannot carry arm {condition!r}: it turns off "
                    f"{list(ABLATION_CONDITIONS[condition]) or 'nothing'}, and a privileged "
                    f"world consults no vision model, so the row would be the baseline wearing a "
                    f"name. Planning, memory and acquisition arms runnable here: {list(SKILL_ARMS)}")
            reasons = installed_module_refusals(
                condition, experience_store=experience_store, skill_memory=skill_memory)
            if reasons:
                raise InfraError("; ".join(reasons))
            arm = (ablation if getattr(ablation, "modules_off", None) is not None
                   else ablation_record(condition))
    else:
        from ..core.v02 import ablation as ablation_record
        from ..perception.grounding import (
            PERCEIVE_CHANNELS,
            arm_coherence,
            channel_readiness,
        )

        if perceive not in PERCEIVE_CHANNELS:
            raise InfraError(f"unknown perception channel {perceive!r}; declared: "
                             f"{list(PERCEIVE_CHANNELS)}")
        not_ready = channel_readiness(perceive, adapter, config_path=model_config)
        if not_ready:
            raise InfraError("; ".join(not_ready))
        _camera_adapter()
        # The default arm follows the channel, exactly as `cli._perception_kwargs` has always
        # done it: a camera channel that consults a vision model is `full`, and defaulting it to
        # `wo_vlm` instead made the next line refuse a request for an arm the caller never named
        # ("channel 'vlm' cannot carry arm 'wo_vlm'") — a refusal that describes the code's
        # default rather than the operator's command.
        arm = (ablation if ablation is not None
               else ablation_record("full" if perceive == "vlm" else "wo_vlm"))
        clash = arm_coherence(arm, perceive)
        if clash:
            raise InfraError(f"channel {perceive!r} cannot carry arm {arm.condition!r}: "
                             + "; ".join(clash))
        # §13 P5: a store and a library are runnable on this channel now, and what keeps §9's rows
        # unambiguous is not the channel but the two checks below — a gate needs the module it gates,
        # and `arm_coherence` already decided which conditions this camera may carry at all (`stub`
        # consults no vision model, so only `wo_vlm` may claim it; `vlm` produces the record, so
        # `wo_vlm` may not). Resolved before a run directory exists, for the standing reason: a
        # refusal must not leave a half-written batch behind, and must not spend a request on its way
        # to saying no.
        reasons = installed_module_refusals(getattr(arm, "condition", arm),
                                            experience_store=experience_store,
                                            skill_memory=skill_memory)
        if reasons:
            raise InfraError("; ".join(reasons))

    # The pre-registration is checked before a run directory exists: a refusal must
    # leave no half-written batch behind, and must not spend a request on its way to
    # saying no (SPEC 9 P3).
    pre = None
    check_path = prereg_path or (PREREG_PATH if os.path.exists(PREREG_PATH) else None)
    if check_path:
        ok, msg = check_prereg(check_path)
        pre = {"prereg_path": check_path, "matches": ok, "detail": msg[:400],
               "rules_sha256": msg if ok else None, "enforced": bool(prereg_path)}
        if prereg_path:
            if not ok:
                raise InfraError(f"refusing to run against a drifted pre-registration: {msg}")
            rules = load_prereg(check_path)["rules"]
            reasons = matrix_mismatch(
                rules, set_name=set_name, modes=modes,
                repeats=repeats, planner_kind=planner_kind, case_ids=case_ids, limit=limit,
                sampling=getattr(getattr(planner, "adapter", None), "sampling", None))
            if reasons:
                raise InfraError("this batch is not the pre-registered one: "
                                 + "; ".join(reasons))
            pre["prereg_id"] = rules["prereg_id"]

    # The channel name is in the run id wherever the channel is not the whole arm. On a camera
    # channel it is: `stub` can only carry `wo_vlm` and `vlm` only `full`, so the channel already
    # discriminates. On the privileged channel P2-e runs four arms, so the condition joins the id
    # exactly where the channel stopped identifying the batch — two arms of one comparison left in
    # directories with the same name would be pooled by whoever globbed them next. The control
    # policy joins it for the same reason: `full` under the v0.1 control and `full` under the
    # payload-reading one are two experiments, not one run twice.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tags = ([arm.condition] if perceive == "privileged" and arm is not None else []) + \
           ([perceive] if perceive != "privileged" else []) + \
           ([policy] if policy in ("payload", "memory") else []) + \
           (["memstore"] if experience_store is not None else []) + \
           (["skillmem"] if skill_memory is not None else []) + \
           ([f"propose-{propose}"] if skill_memory is not None and propose != "rule" else [])
    run_id = f"{set_name}_{planner_kind}_{'_'.join(tags)}_{stamp}" if tags else \
        f"{set_name}_{planner_kind}_{stamp}"
    root = os.path.join(out_root, run_id)
    os.makedirs(root, exist_ok=True)

    goal_log = goal_logger(root)

    freeze = None
    # `--frozen` is the refusal; the canonical file is the *record*. Both read the
    # same path, so "the manifest was written" and "the manifest was checked"
    # cannot end up describing two different files.
    check_path = frozen_path or (FROZEN_PATH if os.path.exists(FROZEN_PATH) else None)
    if check_path:
        ok, msg = check_frozen(check_path)
        freeze = {"frozen_path": check_path, "matches": ok, "detail": msg,
                  "enforced": bool(frozen_path)}
        if frozen_path and not ok:
            raise InfraError(f"refusing to run against a drifted task list: {msg}")
    with open(os.path.join(root, "task_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(frozen_manifest(), f, ensure_ascii=False, indent=1, sort_keys=True)

    from ..acquisition.arm import PIPELINE  # the arm's own section list, not a restatement of it

    write_manifest(root, run_id=run_id, set=set_name, planner=planner_kind,
                   perception={"channel": perceive,
                               "arm": arm.condition if arm else "unset",
                               "modules_off": list(arm.modules_off) if arm else [],
                               "views": list(views or ()), "default_view": default_view,
                               "note": ("v0.1 path: the loop reads the simulator's own "
                                        "inventory, no camera is rendered"
                                        if perceive == "privileged" and arm is None else
                                        "9's planning arm on a privileged world: the loop reads "
                                        "the simulator's own inventory and consults no vision "
                                        "model, so this arm's `full` means planning-full and not "
                                        "the whole system (the claim is in every episode's "
                                        "`ablation` record too)"
                                        if perceive == "privileged" else
                                        "the loop's world comes from a rendered frame")},
                   modes=list(modes), repeats=repeats, cases=[c.task_id for c in cases],
                   decision_source={"policy": policy or "rule",
                                    "note": ("'rule' is v0.1's deterministic control: it reads "
                                             "pending goals and the measured hold state and never a "
                                             "plan, so an ablation of the plan changes nothing it "
                                             "can see. 'payload' is planning.policy.PlanPolicy, "
                                             "which reads only ctx.model_payload() — the same page "
                                             "a model would read — so the arms differ by what is on "
                                             "the page and not by who is reading it. 'memory' is "
                                             "episodic.policy.MemoryPolicy: that same policy with "
                                             "one extra input to *row order*, the sequence of "
                                             "objects in the unrefuted recalled experiences, and "
                                             "no power to add, remove or re-aim an action the plan "
                                             "did not already ask for."
                                             if policy in ("payload", "memory") else
                                             "the v0.1 control, unchanged")},
                   # Named at batch level and per episode, because the store is not a constant of
                   # the batch: every episode that ran the arm appends a row, so the tenth episode
                   # of a `full` batch queries a different store from the first. The fingerprint
                   # here is the state the batch *started* from (a frozen seed, if there was one),
                   # and each row's `episodic` block carries its own before/after ids.
                   episodic={
                       "installed": experience_store is not None,
                       "store_path": getattr(experience_store, "path", None),
                       "fingerprint_at_start": (experience_store.fingerprint()
                                                if experience_store is not None else None),
                       "size_at_start": (len(experience_store) if experience_store is not None
                                         else 0),
                       "task_kind": set_name,
                       "module_off": bool(arm is not None
                                          and not arm.enabled("episodic_memory")),
                       "note": ("episodes write their residue to this file after `episode_end`, "
                                "and a later episode retrieves from whatever the file holds at "
                                "that moment, so §13's RQ3 is answered from the batch order named "
                                "in `cases` below"
                                if experience_store is not None else
                                "no store installed: §5.4's module is absent from this batch, "
                                "which is not the same fact as `wo_episodic_memory`, where it is "
                                "installed and switched off")},
                   # §5.6's library, at the same two levels as the store above: batch-level for the
                   # state it started from, per-episode for what each episode found and left. The
                   # names are recorded rather than a hash because the useful question is "could this
                   # episode have been offered `pick_and_place`?", and a hash cannot answer it. The
                   # note says the thing a reader would otherwise assume: an empty `names_at_start`
                   # means every offer in the batch was cold, so §11's `skill reuse success` row is
                   # about the batch's own later episodes and not about a pretrained library.
                   skill_acquisition={
                       "installed": skill_memory is not None,
                       "store_path": getattr(skill_memory, "path", None),
                       "names_at_start": (sorted(skill_memory.names()) if skill_memory is not None
                                          else []),
                       "size_at_start": (len(skill_memory.entries) if skill_memory is not None
                                         else 0),
                       "refusals_at_start": (len(skill_memory.refusals) if skill_memory is not None
                                             else 0),
                       "proposer": propose,
                       "asker_wired": propose_ask is not None,
                       "validation_budget_per_episode": int(validation_budget),
                       "module_off": bool(arm is not None
                                          and not arm.enabled("skill_acquisition")),
                       "pipeline": list(PIPELINE),
                       "note": ("§5.6's whole pipeline runs inside the episode: gap detection, "
                                "proposal, sandbox + verifier, cross-instance validation, admission. "
                                "The `rule` proposer spends nothing; a `model` proposer needs an "
                                "asker and is not the zero-spend control. A reuse is measured from "
                                "the primitives the loop actually executed, because the frozen "
                                "action space cannot name an acquired program (D49)")
                       if skill_memory is not None else
                       "no library installed: §5.6's module is absent from this batch, which is "
                       "not the same fact as `wo_skill_acquisition`, where it is installed and "
                       "switched off"},
                   budget_profile={c.task_id: c.budgets.model_dump(mode="json") for c in cases},
                   scoring_version="IndependentEvaluator/EvalSpec.tolerance_version",
                   frozen=freeze,
                   pre_registration=pre,
                   offline=_batch_is_offline(planner_kind, perceive),
                   model={"provider": getattr(planner, "provider", "rule"),
                          "model": getattr(planner, "model", "rule-interpreter"),
                          "prompt_version": getattr(planner, "prompt_version", "no-prompt"),
                          "sampling": getattr(getattr(planner, "adapter", None), "sampling", None),
                          "pricing_configured": bool(getattr(planner, "pricing", None))},
                   goal_resolution={"shared_per": "(case, repeat)", "billed_to_modes": list(modes),
                                    "artifact_dir": os.path.join(root, "goal_resolutions")},
                   scene={"sim": "PyBullet", "robot": "franka_panda/panda.urdf",
                          "declared_constants": _declared_constants()},
                   code=git_state())

    rows: list[dict] = []
    for repeat in range(repeats):
        for case in cases:
            resolution: GoalResolution | None = None
            try:
                resolution = resolve_goal(case, repeat, planner, root, log_call=goal_log)
            except Exception as e:  # noqa: BLE001 - should be unreachable; still a row
                for mode in modes:
                    rows.append(_infra_row(case, repeat, mode, "goal_resolution",
                                           f"{type(e).__name__}: {e}", 0.0, set_name,
                                           perceive=perceive))
                    _record_infra(root, rows[-1])
                # the table is the report's input, so the rows a crash produced
                # have to be in it as much as the rows an episode produced
                _write_rows(rows, root)
                continue
            if resolution.error is not None or resolution.goal is None:
                # SPEC 11.2: the whole pair fails together; no mode is dropped.
                for mode in modes:
                    row = _infra_row(case, repeat, mode, "goal_resolution",
                                     str(resolution.error), resolution.wall_s, set_name,
                                     perceive=perceive)
                    row["outcome"] = "goal_resolution_error"
                    row["goal_resolution"] = {"artifact": resolution.artifact,
                                              "counters": resolution.counters}
                    rows.append(row)
                    _record_infra(root, row)
                _write_rows(rows, root)
                continue
            for mode in modes:
                t0 = time.time()
                try:
                    summary = run_one_episode(case, repeat, mode, resolution, planner, root,
                                              set_name, frames=frames, perceive=perceive,
                                              ablation=arm, views=views, adapter=adapter,
                                              default_view=default_view, policy=policy,
                                              experience_store=experience_store,
                                              skill_memory=skill_memory, propose=propose,
                                              propose_ask=propose_ask,
                                              validation_budget=validation_budget)
                    rows.append(_row_of(summary))
                except Exception as e:  # noqa: BLE001 - a crash is a sample, not an absence
                    rows.append(_infra_row(case, repeat, mode, "episode",
                                           f"{type(e).__name__}: {e}", time.time() - t0,
                                           set_name, perceive=perceive))
                    rows[-1]["infrastructure_error"]["traceback_tail"] = traceback.format_exc()[-2000:]
                    _record_infra(root, rows[-1])
                _write_rows(rows, root)
    stats = {"run_id": run_id, "root": root, "planned": len(rows), "rows": len(rows),
             "planner": planner_kind, "offline": _batch_is_offline(planner_kind, perceive),
             "cost_estimate_usd": (planner.cost_estimate() if planner else None),
             "outcomes": {}}
    for r in rows:
        stats["outcomes"][r["outcome"]] = stats["outcomes"].get(r["outcome"], 0) + 1
    with open(os.path.join(root, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return stats


def _declared_constants() -> dict:
    """Calibration constants that decide where an object is handed off and how
    far a waypoint may be interpolated. They are environment facts, fixed per
    scenario and identical for every mode (SPEC 10.3), so they belong in the
    manifest rather than in a prompt."""
    from ..core import scene as scene_mod
    from ..core.placement_planner import PlacementPlanner

    return {"MOVE_TCP_TOL_M": scene_mod.MOVE_TCP_TOL_M,
            "MOVE_TOL_RAD": scene_mod.MOVE_TOL_RAD,
            "SERVO_SETTLE_ALLOWANCE_M": scene_mod.SERVO_SETTLE_ALLOWANCE_M,
            "TRANSFER_WAYPOINT_M": scene_mod.TRANSFER_WAYPOINT_M,
            "HAND_OFF_CLEARANCE_M": PlacementPlanner.HAND_OFF_CLEARANCE_M,
            "CANDIDATE_CLEARANCE_M": PlacementPlanner.CLEARANCE_M,
            "HAND_SWEEP_RADIUS_M": PlacementPlanner.HAND_SWEEP_RADIUS_M,
            "GRID_STEP_M": PlacementPlanner.GRID_STEP_M,
            "touchdown_proximity_m": 0.003}


CSV_FIELDS = ("episode_id", "case_id", "set", "subset", "mode", "repeat", "expected", "outcome",
              "failure_type", "independent_complete_success", "objects_completed", "objects_total",
              "agent_claims_success", "decision_rounds", "skill_calls", "http_requests",
              "prompt_tokens", "completion_tokens", "sim_time_s", "wall_time_s",
              "rejected_decisions", "false_finish_attempts", "identical_invalid_attempts",
              "identical_repeats_total", "model_errors", "semantic_repairs",
              "slot_resolution_fallbacks", "probe", "probe_met", "events_unfired",
              "protocol_notes")


def _write_rows(rows: list[dict], root: str) -> None:
    """Rewritten after every episode so a killed batch still leaves a table."""
    path = os.path.join(root, "episodes.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(_csv_row(r))


def _csv_row(r: dict) -> dict:
    result = r.get("result") or {}
    score = r.get("score") or {}
    probe = r.get("probe") or {}
    return {
        **{k: r.get(k) for k in ("episode_id", "case_id", "set", "subset", "mode", "repeat",
                                 "expected", "outcome")},
        "failure_type": result.get("failure_type"),
        "independent_complete_success": score.get("complete_success"),
        "objects_completed": score.get("objects_completed"),
        "objects_total": score.get("objects_total"),
        "agent_claims_success": result.get("terminal_status") == "success",
        **{k: result.get(k) for k in ("decision_rounds", "skill_calls", "http_requests",
                                      "prompt_tokens", "completion_tokens", "sim_time_s",
                                      "wall_time_s", "rejected_decisions", "false_finish_attempts",
                                      "identical_invalid_attempts", "identical_repeats_total",
                                      "model_errors", "semantic_repairs",
                                      "slot_resolution_fallbacks")},
        "probe": probe.get("probe"), "probe_met": probe.get("met"),
        "events_unfired": len(((r.get("environment_events") or {}).get("unfired")) or []),
        "protocol_notes": "; ".join(score.get("protocol_notes") or []),
    }


# ---------------------------------------------------------------- replay -----


def recorded_replay(run_dir: str) -> dict:
    """Read back the logged trajectory. No physics, no re-deciding (SPEC 7)."""
    path = os.path.join(run_dir, "events.jsonl")
    if not os.path.exists(path):
        raise InfraError(f"no event log at {path}")
    with open(path, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    out = {"kind": "recorded_replay", "run_dir": run_dir, "events": len(events),
           "sim_time_final": events[-1]["sim_time"] if events else None,
           "sequence": [{"seq": e["sequence"], "type": e["type"], "sim_time": e["sim_time"],
                         "skill": (e["payload"].get("call") or {}).get("skill")
                         or (e["payload"].get("feedback") or {}).get("skill")}
                        for e in events]}
    print(json.dumps({"kind": out["kind"], "events": out["events"],
                      "terminal": out["sequence"][-3:]}, ensure_ascii=False, indent=2))
    return out


def plan_rerun(run_dir: str, set_name: str | None = None) -> dict:
    """Re-execute an episode's *last* plan in a fresh scene.

    Explicitly not a trajectory reproduction: only the final plan is stored, so
    any decision taken at a later feedback boundary is absent, and the outcome is
    evidence about the plan and the physics, not about the model's run."""
    summary_path = os.path.join(run_dir, "episode_summary.json")
    if not os.path.exists(summary_path):
        raise InfraError(f"no episode_summary.json at {run_dir}")
    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)
    case_id = summary["case_id"]
    case = next((c for c in build_set(set_name or summary["set"]) if c.task_id == case_id), None)
    if case is None:
        raise InfraError(f"case {case_id} is not in set {set_name or summary['set']!r}")
    events = []
    with open(os.path.join(run_dir, "events.jsonl"), encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e["type"] == "decision" and e["payload"].get("action") == "execute":
                ex = e["payload"].get("execute") or {}
                events.append([ex.get("skill"), ex.get("args"), ex.get("candidate_id")])
    scene = build_scene(case)
    try:
        seen = {"v": 0}

        def replay_world():
            # the executor refuses to act blind; a replay owes it the same
            # measured snapshot a runtime round would have handed it
            seen["v"] += 1
            return build_world_state(scene, seen["v"], f"replay_{seen['v']:04d}", case.verify)

        executor = SkillExecutor(scene, world_provider=replay_world, config=case.verify)
        outcomes = []
        for skill, args, cid in events:
            call = SkillCall(call_id="replay", plan_id="plan_rerun", step_id="s", skill=skill,
                             args=dict(args or {}), candidate_id=cid)
            r = executor.execute(call)
            outcomes.append({"skill": skill, "args": args, "candidate_id": cid,
                             "status": r.status.value, "failure_code": r.failure_code})
    finally:
        scene.close()
    out = {"kind": "plan_rerun", "not_a_trajectory_reproduction": True, "run_dir": run_dir,
           "steps": len(events), "outcomes": outcomes}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="embodied_agent.evaluation.run",
                                description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a set through the shared base")
    r.add_argument("--set", default="smoke")
    r.add_argument("--planner", choices=PLANNERS, default="rule")
    r.add_argument("--modes", default=",".join(MODES))
    r.add_argument("--repeats", type=int, default=1)
    r.add_argument("--out-root", default="runs")
    r.add_argument("--cases", default="")
    r.add_argument("--limit", type=int)
    r.add_argument("--no-frames", action="store_true")
    r.add_argument("--model-config")
    r.add_argument("--frozen", nargs="?", const=FROZEN_PATH, default=None, metavar="PATH",
                   help="refuse to run unless the task list matches this hash")
    r.add_argument("--prereg", nargs="?", const=PREREG_PATH, default=None, metavar="PATH",
                   help="refuse to run unless the pre-registration matches this file and "
                        "this batch is the matrix it pre-registered")
    e = sub.add_parser("replay-recorded", help="read back a logged trajectory (no physics)")
    e.add_argument("--run-dir", required=True)
    q = sub.add_parser("rerun-plan", help="re-execute an episode's last plan in a fresh scene")
    q.add_argument("--run-dir", required=True)
    q.add_argument("--set")
    a = p.parse_args(argv)
    if a.cmd == "run":
        run_group(a.set, modes=tuple(m.strip().upper() for m in a.modes.split(",") if m.strip()),
                  planner_kind=a.planner, repeats=a.repeats, out_root=a.out_root,
                  case_ids=[c for c in a.cases.split(",") if c.strip()] or None,
                  limit=a.limit, frames=not a.no_frames, model_config=a.model_config,
                  frozen_path=a.frozen, prereg_path=a.prereg)
    elif a.cmd == "replay-recorded":
        recorded_replay(a.run_dir)
    else:
        plan_rerun(a.run_dir, a.set)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
