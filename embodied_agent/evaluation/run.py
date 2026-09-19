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

from ..adapters.deepseek import DeepSeekPlanner, FixturePlanner
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


# ---------------------------------------------------------------- source -----


def make_source(mode: str, case: TaskCase, planner, rt: Runtime, goal: GoalSpec,
                log_call=None):
    """The only place the three arms differ (SPEC 11.2: same everything else).

    Mode A's plan is drawn from `rt.initial_context(...)` — the identical
    context a mode B round gets at t0 — and a failing one-shot request is the
    baseline's own outcome, not something to paper over with the rule plan.
    """
    if planner is None:
        rule = RulePlanner(case.verify)
        if mode == "A":
            ctx = rt.initial_context(task_input(case), goal)
            return OneShotPlanSource(rule.plan(goal, ctx.world, plan_id=f"p_{ctx.context_id}"))
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
                    planner, root: str, set_name: str, *, frames: bool = True) -> dict:
    """One episode, start to finish, with its artifacts.

    Nothing here decides *what to do*: the source chooses, the runtime executes
    and validates, the evaluator scores afterwards. A raised error escapes to
    `run_group`, which records it rather than retrying — a retry would be the
    runner repairing the sample it is supposed to report.
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
        runtime = Runtime(scene, executor, ep_dir, case.budgets, episode_id,
                          config=case.verify, environment=controller, store=store)
        executor.world_provider = runtime.observe

        def log_call(payload, latency_s):
            _append_jsonl(os.path.join(ep_dir, "model_calls.jsonl"),
                          {**payload, "latency_s": round(latency_s, 3),
                           "episode_id": episode_id, "mode": mode, "case_id": case.task_id,
                           "repeat": repeat, "recorded_at": time.time()})

        source = make_source(mode, case, planner, runtime, resolution.goal, log_call)
        result = runtime.run_episode(task_input(case), resolution.goal, source, mode=mode,
                                     prologue=resolution.prologue())
        events = store.read_all()
        snapshot = runtime.terminal_snapshot
        score = IndependentEvaluator(snapshot, case.eval_spec).score(result)
        probe = run_probe(case, result, score, events,
                          {"needs_clarification": str(result.failure_type or "")})
        summary = {
            "episode_id": episode_id, "case_id": case.task_id, "set": set_name,
            "subset": case.subset, "mode": mode, "repeat": repeat,
            "expected": case.expected, "probe": probe, "score": score,
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


def _infra_row(case: TaskCase, repeat: int, mode: str, where: str, error: str,
               wall_s: float, set_name: str) -> dict:
    return {"episode_id": f"{case.task_id}.{mode}.r{repeat}", "case_id": case.task_id,
            "set": set_name, "subset": case.subset, "mode": mode, "repeat": repeat,
            "expected": case.expected, "outcome": "infrastructure_error",
            "infrastructure_error": {"where": where, "error": error, "traceback_tail": ""},
            "wall_time_s": round(wall_s, 2), "probe": {}, "score": {},
            "result": {"terminal_status": "failed", "failure_type": None}}


def _row_of(summary: dict) -> dict:
    return {**summary, "outcome": (summary.get("result") or {}).get("terminal_status", "unknown")}


def run_group(set_name: str, *, modes=MODES, planner_kind: str = "rule", repeats: int = 1,
              out_root: str = "runs", case_ids: list[str] | None = None,
              limit: int | None = None, frames: bool = True,
              model_config: str | None = None, frozen_path: str | None = None) -> dict:
    """Run `cases x modes x repeats` with the modes interleaved.

    Interleaving is a measurement decision: a provider that changes behaviour
    mid-batch then perturbs all three arms of a pair equally instead of
    contaminating one side of every comparison (SPEC 11.2)."""
    if planner_kind not in PLANNERS:
        raise ValueError(f"--planner must be one of {PLANNERS}")
    cases = build_set(set_name)
    if case_ids:
        cases = [c for c in cases if c.task_id in set(case_ids)]
    if limit:
        cases = cases[:limit]
    if not cases:
        raise ValueError(f"no cases selected from set {set_name!r}")

    run_id = f"{set_name}_{planner_kind}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    root = os.path.join(out_root, run_id)
    os.makedirs(root, exist_ok=True)

    planner = None
    if planner_kind == "fixture":
        planner = FixturePlanner()
    elif planner_kind == "deepseek":
        planner = DeepSeekPlanner.from_env(model_config)

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

    write_manifest(root, run_id=run_id, set=set_name, planner=planner_kind,
                   modes=list(modes), repeats=repeats, cases=[c.task_id for c in cases],
                   budget_profile={c.task_id: c.budgets.model_dump(mode="json") for c in cases},
                   scoring_version="IndependentEvaluator/EvalSpec.tolerance_version",
                   frozen=freeze,
                   offline=planner_kind != "deepseek",
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
                                           f"{type(e).__name__}: {e}", 0.0, set_name))
                # the table is the report's input, so the rows a crash produced
                # have to be in it as much as the rows an episode produced
                _write_rows(rows, root)
                continue
            if resolution.error is not None or resolution.goal is None:
                # SPEC 11.2: the whole pair fails together; no mode is dropped.
                for mode in modes:
                    row = _infra_row(case, repeat, mode, "goal_resolution",
                                     str(resolution.error), resolution.wall_s, set_name)
                    row["outcome"] = "goal_resolution_error"
                    row["goal_resolution"] = {"artifact": resolution.artifact,
                                              "counters": resolution.counters}
                    rows.append(row)
                _write_rows(rows, root)
                continue
            for mode in modes:
                t0 = time.time()
                try:
                    summary = run_one_episode(case, repeat, mode, resolution, planner, root,
                                              set_name, frames=frames)
                    rows.append(_row_of(summary))
                except Exception as e:  # noqa: BLE001 - a crash is a sample, not an absence
                    rows.append(_infra_row(case, repeat, mode, "episode",
                                           f"{type(e).__name__}: {e}", time.time() - t0,
                                           set_name))
                    rows[-1]["infrastructure_error"]["traceback_tail"] = traceback.format_exc()[-2000:]
                    _append_jsonl(os.path.join(root, "errors.jsonl"), rows[-1]["infrastructure_error"]
                                  | {"episode_id": rows[-1]["episode_id"],
                                     "case_id": case.task_id, "mode": mode, "repeat": repeat})
                _write_rows(rows, root)
    stats = {"run_id": run_id, "root": root, "planned": len(rows), "rows": len(rows),
             "planner": planner_kind, "offline": planner_kind != "deepseek",
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
                  frozen_path=a.frozen)
    elif a.cmd == "replay-recorded":
        recorded_replay(a.run_dir)
    else:
        plan_rerun(a.run_dir, a.set)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
