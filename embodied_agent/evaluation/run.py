"""Run CLI: run / evaluate / replay entry points (spec 14).

Examples:
  python -m embodied_agent.evaluation.run run --set smoke --planner rule --recovery on
  python -m embodied_agent.evaluation.run run --set s1_fault --recovery off --runs 3
  python -m embodied_agent.evaluation.run evaluate --set smoke   (runs + Wilson report)
  python -m embodied_agent.evaluation.run replay --run-dir runs/<id>  (trajectory replay)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime

from ..adapters.deepseek import DeepSeekPlanner, FixtureLLM
from ..core.contracts import Budgets, FailureCode, TaskInput
from ..core.interpreter import interpret
from ..core.planner import RulePlanner
from ..core.runtime import Runtime
from ..core.scene import PhysicsScene
from ..core.skills import SkillExecutor
from .evaluator import IndependentEvaluator
from .tasks import TaskCase, build_set, smoke_set


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    ph = k / n
    denom = 1 + z * z / n
    center = (ph + z * z / (2 * n)) / denom
    half = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def run_episode(case: TaskCase, planner_kind: str, recovery: bool, runs_root: str,
                llm=None, gui: bool = False, snapshot_frames: bool = True) -> dict:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    episode_id = f"{case.task_id}_{stamp}"
    run_dir = os.path.join(runs_root, episode_id)
    os.makedirs(run_dir, exist_ok=True)

    config = {
        "episode_id": episode_id,
        "task_id": case.task_id,
        "set": case.set_name,
        "utterance": case.utterance,
        "seed": case.seed,
        "planner": planner_kind,
        "recovery": recovery,
        "fault": {"type": case.fault.type, "offset": list(case.fault.offset)} if case.fault else None,
        "objects": case.objects,
        "eval_spec": case.eval_spec.model_dump(mode="json"),
        "budgets": case.eval_spec.budgets.model_dump(),
        "framework": {"sim": "PyBullet 3.2.7", "robot": "franka_panda/panda.urdf (pybullet_data)", "time_step": "1/240"},
    }
    with open(os.path.join(run_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    frames_dir = os.path.join(run_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects, gui=gui)
    executor = SkillExecutor(scene, world_provider=lambda: None)
    runtime = Runtime(scene, executor, run_dir, case.eval_spec.budgets, episode_id)
    frame_idx = [0]
    _orig_exec = executor.execute
    def _frame_exec(call):
        r = _orig_exec(call)
        # GUI mode: the live window is the visualization; off-screen tiny-render
        # inside a GUI client crashes the GL context (WSLg/D3D12) — skip frames
        if not gui:
            frame_idx[0] += 1
            try:
                scene.render(os.path.join(frames_dir, f"{frame_idx[0]:02d}_{call.skill}.png"))
            except Exception:  # noqa: BLE001 - recording must not affect scoring
                pass
        return r
    executor.execute = _frame_exec
    world = runtime.observe()
    task = TaskInput(task_id=case.task_id, utterance=case.utterance)

    if planner_kind == "rule":
        goal = interpret(task, world)
        planner = RulePlanner(scene)
    elif planner_kind in ("deepseek", "fixture"):
        llm = llm or (FixtureLLM() if planner_kind == "fixture" else DeepSeekPlanner.from_env())
        goal, planner = None, None  # filled below by LLM path
        goal, planner = llm_plan(scene, llm, task, world, runtime, case)
    else:
        raise ValueError(planner_kind)

    t0 = time.time()
    fault = (case.fault.type, tuple(case.fault.offset)) if case.fault else None
    try:
        result = runtime.run_episode(
            task, goal, case.eval_spec, planner,
            recovery_enabled=recovery, planner_kind=planner_kind, fault=fault,
        )
    finally:
        if snapshot_frames and not gui:
            scene.render(os.path.join(run_dir, "final_frame.png"))
        # scene stays open here: independent scoring below still needs physics;
        # disconnect happens after scoring (GUI never disconnects — pybullet's
        # GUI teardown segfaults, so GUI mode hard-exits with the window open)

    # independent scoring (separate from the agent's own verification)
    score = IndependentEvaluator(scene, case.eval_spec).score()
    if not gui:
        scene.close()
    artifact_error = None
    try:
        import imageio.v2 as imageio

        frame_files = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))
        if frame_files:
            vid = imageio.get_writer(os.path.join(run_dir, "video.mp4"), fps=4)
            for fn in frame_files:
                vid.append_data(imageio.imread(os.path.join(frames_dir, fn)))
            vid.close()
    except Exception as e:  # noqa: BLE001 - video failure must not change scoring
        artifact_error = f"video assembly failed: {e}"

    summary = {
        "episode_id": episode_id,
        "task_id": case.task_id,
        "set": case.set_name,
        "planner": planner_kind,
        "recovery": recovery,
        "terminal_status": result.terminal_status.value,
        "agent_complete_success": result.score_complete_success,
        "independent_complete_success": score["complete_success"],
        "objects_total": score["objects_total"],
        "objects_completed": score["objects_completed"],
        "expected": case.expected,
        "expected_met": _expected_met(case, result, score),
        "failure_type": result.failure_type.value if result.failure_type else None,
        "recovery_events": result.recovery_events,
        "retry_events": result.retry_events,
        "replan_events": result.replan_events,
        "model_requests": result.model_requests,
        "sim_time_s": result.sim_time_s,
        "wall_time_s": round(time.time() - t0, 2),
        "score": score,
        "artifact_error": artifact_error,
    }
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    return summary


def _expected_met(case: TaskCase, result, score: dict) -> bool:
    if case.expected == "success":
        return result.terminal_status.value == "success" and score["complete_success"]
    if case.expected == "needs_clarification":
        return result.terminal_status.value == "needs_clarification"
    if case.expected == "plan_rejected":
        return result.failure_type is not None
    if case.expected == "bounded_failure":
        return (result.terminal_status.value == "failed"
                and result.failure_type in (FailureCode.TARGET_FULL, FailureCode.BUDGET_EXHAUSTED,
                                            FailureCode.GRASP_MISS, FailureCode.IK_UNREACHABLE))
    return False


VERBOSE = False


def set_verbose(v: bool):
    global VERBOSE
    VERBOSE = bool(v)


def _log_model_call(runtime, payload, latency_s):
    """Dedicated model_calls.jsonl (spec 11): provider/model/prompt version/
    usage/latency; never the API key (it exists only in the environment)."""
    with open(os.path.join(runtime.store.run_dir, "model_calls.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "provider": payload.get("provider"), "model": payload.get("model"),
            "prompt_version": payload.get("prompt_version"), "usage": payload.get("usage"),
            "latency_s": round(latency_s, 3), "replan": payload.get("_replan", False),
        }, ensure_ascii=False) + "\n")
    if VERBOSE:
        kind = "重规划" if payload.get("_replan") else "初始规划"
        parsed = payload.get("_parsed", {})
        print(f"── LLM {kind} {payload.get('provider')}/{payload.get('model')} "
              f"({latency_s:.1f}s, {payload.get('usage', {}).get('total_tokens', '?')} tokens)──", flush=True)
        goal = parsed.get("goal_spec", {})
        if goal.get("ambiguous"):
            print(f"  [needs_clarification] {goal.get('clarification_request')}", flush=True)
        for a in goal.get("assignments", []):
            ent = a["entity"].get("entity_id") or a["entity"].get("attributes")
            print(f"  绑定 {ent} -> {a['target_id']}", flush=True)
        for s in parsed.get("plan", {}).get("steps", []):
            print(f"    {s['skill']:6s} {s['args']}", flush=True)


def llm_plan(scene, llm, task, world, runtime, case):
    """One structured request -> GoalSpec + Plan (spec 10.1)."""
    skill_catalogue = {
        "pick": {"args": {"object_id": "str"}},
        "place": {"args": {"object_id": "str", "target_id": "str"}},
    }
    import time as _time
    t0 = _time.time()
    payload = llm.plan(task.utterance, world, skill_catalogue)
    payload["_parsed"] = {k: v for k, v in payload.items() if not k.startswith("_")}
    _log_model_call(runtime, payload, _time.time() - t0)
    runtime.store.log("model_response", raw=payload.get("raw", ""), parsed=payload)
    runtime.ledger_model_request()
    from ..core.contracts import GoalSpec, Plan
    from ..core.planner import RulePlanner

    goal = GoalSpec.model_validate(payload["goal_spec"])
    first_plan = Plan.model_validate(payload["plan"])

    class _LLMPlanner(RulePlanner):
        """First call returns the LLM plan; later replan rounds call the LLM
        again (bounded by the runtime replan budget, spec 10.2)."""

        def __init__(self, scene):
            super().__init__(scene)
            self._first_used = False

        def plan(self, goal, world):
            if not self._first_used:
                self._first_used = True
                return first_plan
            import time as _time
            _t0 = _time.time()
            payload = llm.plan(task.utterance, world, skill_catalogue, replan=True)
            payload["_replan"] = True
            payload["_parsed"] = {k: v for k, v in payload.items() if not k.startswith("_")}
            _log_model_call(runtime, payload, _time.time() - _t0)
            runtime.store.log("model_response", raw=payload.get("raw", ""), parsed=payload, replan=True)
            runtime.ledger_model_request()
            return Plan.model_validate(payload["plan"])

    return goal, _LLMPlanner(scene)


def evaluate_set(name: str, planner: str, recovery: bool, out_root: str, limit: int | None = None,
                 runs: int = 1):
    cases = build_set(name)
    if limit:
        cases = cases[:limit]
    if runs > 1:
        cases = cases * runs  # repeated runs of frozen configs (deterministic seeds)
    runs_root = os.path.join(out_root, f"{name}_{planner}_recovery{'on' if recovery else 'off'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(runs_root, exist_ok=True)
    llm = None
    if planner == "deepseek":
        from ..adapters.deepseek import DeepSeekPlanner

        llm = DeepSeekPlanner.from_env()
    summaries = []
    for i, case in enumerate(cases):
        try:
            s = run_episode(case, planner, recovery, runs_root, llm=llm)
        except Exception as e:  # noqa: BLE001 - batch runner: keep original error, continue (spec 15)
            print(f"[{i + 1}/{len(cases)}] {case.task_id}: INFRA ERROR {type(e).__name__}: {e}", flush=True)
            summaries.append({"episode_id": f"infra_error_{i}", "task_id": case.task_id, "set": name,
                              "planner": planner, "recovery": recovery, "terminal_status": "failed",
                              "independent_complete_success": False, "objects_completed": 0,
                              "objects_total": 0, "expected": case.expected, "expected_met": False,
                              "failure_type": "INFRASTRUCTURE_ERROR", "recovery_events": 0,
                              "retry_events": 0, "replan_events": 0, "model_requests": 0,
                              "sim_time_s": 0, "wall_time_s": 0, "score": {}})
            continue
        summaries.append(s)
        print(f"[{i + 1}/{len(cases)}] {case.task_id}: {'OK' if s['expected_met'] else 'MISS'} "
              f"({s['terminal_status']}, objs {s['objects_completed']}/{s['objects_total']})")
    report = summarize(summaries, name)
    with open(os.path.join(runs_root, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    write_csv(summaries, os.path.join(runs_root, "results.csv"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def summarize(summaries: list[dict], name: str) -> dict:
    if name == "negative":
        met = sum(1 for s in summaries if s["expected_met"])
        return {
            "set": name, "n": len(summaries), "expected_met": met,
            "ratio": round(met / max(1, len(summaries)), 3),
            "note": "all negative cases must terminate/clarify within budget; no illegal action executed",
        }
    n = len(summaries)
    k = sum(1 for s in summaries if s["independent_complete_success"])
    lo, hi = wilson_interval(k, n)
    by_count: dict[int, dict] = {}
    for s in summaries:
        key = s["objects_total"]
        d = by_count.setdefault(key, {"n": 0, "k": 0})
        d["n"] += 1
        d["k"] += int(s["independent_complete_success"])
    return {
        "set": name,
        "n": n,
        "successes": k,
        "success_rate": round(k / max(1, n), 3),
        "wilson95": [round(lo, 3), round(hi, 3)],
        "by_object_count": {str(kk): {"n": d["n"], "k": d["k"], "rate": round(d["k"] / d["n"], 3)} for kk, d in by_count.items()},
    }


def write_csv(summaries: list[dict], path: str):
    if not summaries:
        return
    keys = ["episode_id", "task_id", "set", "planner", "recovery", "terminal_status",
            "independent_complete_success", "objects_completed", "objects_total",
            "expected_met", "failure_type", "recovery_events", "replan_events",
            "model_requests", "sim_time_s", "wall_time_s"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(summaries)


def trajectory_replay(run_dir: str, out_dir: str | None = None):
    """Replay an episode's recorded plan in a fresh same-seed scene (spec 14:
    shows an existing execution; does not re-solve the task online)."""
    with open(os.path.join(run_dir, "config.json"), encoding="utf-8") as f:
        config = json.load(f)
    events_path = os.path.join(run_dir, "events.jsonl")
    steps = []
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e.get("event") == "plan_generated":
                steps = e["plan"]["steps"]  # last plan before end
    replay_dir = out_dir or os.path.join(run_dir, "replay")
    os.makedirs(replay_dir, exist_ok=True)
    scene = PhysicsScene(seed=config["seed"], object_layout=config["objects"])
    executor = SkillExecutor(scene, world_provider=lambda: None)
    from ..core.contracts import Plan, PlanStep, SkillCall

    plan = Plan(plan_id="replay", based_on_state_version=0, goal_ref="replay",
                steps=[PlanStep.model_validate(s) for s in steps])
    log = []
    for step in plan.steps:
        call = SkillCall(plan_id=plan.plan_id, step_id=step.id, skill=step.skill, args=dict(step.args))
        r = executor.execute(call)
        log.append({"step": step.model_dump(mode="json"), "status": r.status.value, "failure": r.failure_code})
    from .evaluator import IndependentEvaluator
    from ..core.contracts import EvalSpec

    spec = EvalSpec.model_validate(config["eval_spec"])
    score = IndependentEvaluator(scene, spec).score()
    scene.close()
    out = {"replay_of": run_dir, "steps": log, "score": score}
    with open(os.path.join(replay_dir, "replay_summary.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return out


def model_response_replay(run_dir: str, out_dir: str | None = None):
    """Model-response replay (spec 14): re-parse recorded model outputs through
    the runtime validator WITHOUT any network call — isolates planning changes
    from execution. Complements trajectory_replay, neither pretends to be a
    fresh online episode."""
    with open(os.path.join(run_dir, "config.json"), encoding="utf-8") as f:
        config = json.load(f)
    responses = []
    with open(os.path.join(run_dir, "events.jsonl"), encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e.get("event") == "model_response":
                responses.append(e["parsed"])
    if not responses:
        print("no model_response events in", run_dir)
        return None
    from ..core.contracts import GoalSpec, Plan, WorldState
    results = []
    for i, raw in enumerate(responses):
        verdict = {"response_index": i}
        try:
            goal = GoalSpec.model_validate(raw.get("goal_spec", {}))
            plan = Plan.model_validate(raw.get("plan", {}))
            verdict["goal_parses"] = True
            verdict["plan_parses"] = True
            verdict["plan_steps"] = [(s.skill, s.args) for s in plan.steps]
            verdict["ambiguous"] = goal.ambiguous
        except Exception as e:  # noqa: BLE001
            verdict["goal_parses"] = False
            verdict["error"] = str(e)[:200]
        results.append(verdict)
    out = {"replay_of": run_dir, "provider": config.get("planner"), "responses": results}
    out_dir = out_dir or os.path.join(run_dir, "replay")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "model_replay.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return out


def llm_transcript(run_dir: str) -> list[dict]:
    """Pretty-print every LLM request/response recorded in an episode."""
    events_path = os.path.join(run_dir, "events.jsonl")
    out = []
    if not os.path.exists(events_path):
        print(f"no events.jsonl in {run_dir}")
        return out
    for line in open(events_path, encoding="utf-8"):
        e = json.loads(line)
        if e.get("type") != "model_response":
            continue
        p = e["payload"]
        parsed = p.get("parsed", {})
        entry = {
            "replan": bool(p.get("replan") or e.get("replan")),
            "provider": parsed.get("provider"), "model": parsed.get("model"),
            "usage": parsed.get("usage"),
            "plan": [(s["skill"], s["args"]) for s in parsed.get("plan", {}).get("steps", [])],
            "goal_assignments": [
                (a["entity"].get("entity_id") or a["entity"].get("attributes"), a["target_id"])
                for a in parsed.get("goal_spec", {}).get("assignments", [])
            ],
            "ambiguous": parsed.get("goal_spec", {}).get("ambiguous"),
            "clarification": parsed.get("goal_spec", {}).get("clarification_request"),
            "raw_response": parsed.get("raw", "")[:500],
        }
        out.append(entry)
    for i, entry in enumerate(out, 1):
        kind = "重规划" if entry["replan"] else "初始规划"
        print(f"── LLM 请求 {i}({kind},{entry['provider']}/{entry['model']})──")
        print(f"  goal: {entry['goal_assignments']}")
        if entry["ambiguous"]:
            print(f"  需要澄清: {entry['clarification']}")
        for skill, args in entry["plan"]:
            print(f"    {skill:6s} {args}")
        print(f"  tokens: {entry['usage']}")
    if not out:
        print("该 episode 没有 LLM 交互(rule 规划器或未运行)")
    return out


def paired_fault_analysis(on_dir: str, off_dir: str) -> dict:
    import glob as _glob
    """A3 paired analysis: per-config win/loss/tie, cluster (config) level
    bootstrap of the success-rate difference — repeated runs of the same
    config are deterministic here, so the config is the independent unit."""
    import random

    def outcomes(d):
        out = {}
        for s in _glob.glob(os.path.join(d, "*", "summary.json")):
            summ = json.load(open(s))
            cid = summ["task_id"]  # task_id is the frozen fault config id
            out.setdefault(cid, []).append(summ["independent_complete_success"])
        return out

    on, off = outcomes(on_dir), outcomes(off_dir)
    configs = sorted(set(on) & set(off))
    pairs = [(sum(on[c]) / len(on[c]), sum(off[c]) / len(off[c])) for c in configs]
    wins = sum(1 for a, b in pairs if a > b)
    losses = sum(1 for a, b in pairs if a < b)
    ties = len(pairs) - wins - losses
    diffs = [a - b for a, b in pairs]
    rng = random.Random(0)
    boots = []
    for _ in range(10000):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        boots.append(sum(sample) / len(sample))
    boots.sort()
    lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
    return {
        "n_configs": len(configs),
        "recovery_on_rate": round(sum(a for a, _ in pairs) / len(pairs), 3),
        "recovery_off_rate": round(sum(b for _, b in pairs) / len(pairs), 3),
        "mean_gain": round(sum(diffs) / len(diffs), 3),
        "gain_bootstrap95": [round(lo, 3), round(hi, 3)],
        "config_wins": wins, "config_losses": losses, "config_ties": ties,
        "note": "exact paired sign test: P(wins>=observed | tie-free) = 2^-wins when losses=0",
    }


def demo(out_root: str = "runs"):
    """A5 evidence: one multi-object episode, one recovery replay, one real
    failure replay + per-event pointers (spec 12.2 A5)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(out_root, f"demo_{stamp}")
    os.makedirs(out, exist_ok=True)
    # 1) clean multi-object episode with key frames
    case = [c for c in smoke_set() if c.task_id == "smoke_5obj_s13"][0]
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    ex = SkillExecutor(scene, lambda: None)
    episode_dir = os.path.join(out, "episode_5obj")
    rt = Runtime(scene, ex, episode_dir, case.eval_spec.budgets, f"demo_{stamp}")
    world = rt.observe()
    task = TaskInput(task_id=case.task_id, utterance=case.utterance)
    goal = interpret(task, world)
    result = rt.run_episode(task, goal, case.eval_spec, RulePlanner(scene), planner_kind="rule")
    score = IndependentEvaluator(scene, case.eval_spec).score()
    scene.render(os.path.join(episode_dir, "final.png"))
    scene.close()
    with open(os.path.join(episode_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"episode_id": f"demo_{stamp}", "task_id": case.task_id, "set": "smoke",
                   "utterance": case.utterance, "seed": case.seed, "planner": "rule",
                   "recovery": True, "fault": None, "objects": case.objects,
                   "eval_spec": case.eval_spec.model_dump(mode="json")}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(episode_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"episode_id": f"demo_{stamp}", "task_id": case.task_id, "set": "smoke",
                   "planner": "rule", "recovery": True,
                   "terminal_status": result.terminal_status.value,
                   "independent_complete_success": score["complete_success"],
                   "objects_completed": score["objects_completed"],
                   "objects_total": score["objects_total"], "score": score},
                  f, ensure_ascii=False, indent=2, default=str)
    # 2) fault case with recovery: capture mid-recovery + final frames
    fcase = [c for c in smoke_set() if c.fault][0]
    fdir = os.path.join(out, "recovery_demo")
    scene = PhysicsScene(seed=fcase.seed, object_layout=fcase.objects)
    ex = SkillExecutor(scene, lambda: None)
    rt = Runtime(scene, ex, fdir, fcase.eval_spec.budgets, f"demo_rec_{stamp}")
    orig_exec = ex.execute
    frames = {"n": 0}
    def frame_cap(call):
        r = orig_exec(call)
        frames["n"] += 1
        scene.render(os.path.join(fdir, f"after_skill_{frames['n']:02d}_{call.skill}.png"))
        return r
    ex.execute = frame_cap
    world = rt.observe()
    task = TaskInput(task_id=fcase.task_id, utterance=fcase.utterance)
    goal = interpret(task, world)
    fresult = rt.run_episode(task, goal, fcase.eval_spec, RulePlanner(scene),
                             recovery_enabled=True, fault=(fcase.fault.type, tuple(fcase.fault.offset)))
    scene.render(os.path.join(fdir, "final.png"))
    fscore = IndependentEvaluator(scene, fcase.eval_spec).score()
    scene.close()
    with open(os.path.join(fdir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"episode_id": f"demo_rec_{stamp}", "task_id": fcase.task_id, "set": "smoke",
                   "utterance": fcase.utterance, "seed": fcase.seed, "planner": "rule",
                   "recovery": True,
                   "fault": {"type": fcase.fault.type, "offset": list(fcase.fault.offset)},
                   "objects": fcase.objects, "eval_spec": fcase.eval_spec.model_dump(mode="json")},
                  f, ensure_ascii=False, indent=2)
    with open(os.path.join(fdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"episode_id": f"demo_rec_{stamp}", "task_id": fcase.task_id, "set": "smoke",
                   "planner": "rule", "recovery": True,
                   "terminal_status": fresult.terminal_status.value,
                   "independent_complete_success": fscore["complete_success"],
                   "objects_completed": fscore["objects_completed"],
                   "objects_total": fscore["objects_total"],
                   "recovery_events": fresult.recovery_events, "score": fscore},
                  f, ensure_ascii=False, indent=2, default=str)
    demo = {
        "demo_id": stamp,
        "episode": {"task": case.task_id, "utterance": case.utterance,
                    "terminal": result.terminal_status.value, "score": score,
                    "dir": episode_dir, "events": os.path.join(episode_dir, "events.jsonl")},
        "recovery": {"task": fcase.task_id, "fault": {"type": fcase.fault.type, "offset": list(fcase.fault.offset)},
                     "terminal": fresult.terminal_status.value,
                     "recovery_events": fresult.recovery_events,
                     "dir": fdir, "events": os.path.join(fdir, "events.jsonl")},
    }
    with open(os.path.join(out, "demo.json"), "w", encoding="utf-8") as f:
        json.dump(demo, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(demo, ensure_ascii=False, indent=2, default=str))
    return demo


def main():
    ap = argparse.ArgumentParser("TabletopOrganize-v1 runner")
    ap.add_argument("command", choices=["run", "evaluate", "replay", "model-replay", "demo", "a3"])
    ap.add_argument("--set", dest="set_name", default="smoke")
    ap.add_argument("--task", dest="task_id", default=None, help="single task id within the set")
    ap.add_argument("--planner", dest="planner", default="rule", choices=["rule", "fixture", "deepseek"])
    ap.add_argument("--recovery", dest="recovery", default="on", choices=["on", "off"])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", dest="out_root", default="runs")
    ap.add_argument("--run-dir", dest="run_dir", default=None)
    args = ap.parse_args()

    if args.command == "replay":
        assert args.run_dir, "--run-dir required"
        trajectory_replay(args.run_dir)
        return
    if args.command == "model-replay":
        assert args.run_dir, "--run-dir required"
        model_response_replay(args.run_dir)
        return
    if args.command == "demo":
        demo(args.out_root)
        return
    if args.command == "a3":
        import glob as _glob
        assert args.run_dir, "--run-dir <recovery_on_dir> and --out-root <recovery_off_dir>"
        on_d, off_d = args.run_dir, args.out_root
        on_d = sorted(_glob.glob(on_d))[-1] if "*" in on_d else on_d
        off_d = sorted(_glob.glob(off_d))[-1] if "*" in off_d else off_d
        print(json.dumps(paired_fault_analysis(on_d, off_d), ensure_ascii=False, indent=2))
        return
    if args.command == "run":
        cases = [c for c in build_set(args.set_name) if args.task_id in (None, c.task_id)]
        if args.task_id and not cases:
            raise SystemExit(f"task {args.task_id} not in set {args.set_name}")
        cases = cases[: max(1, args.runs)]
        llm = None
        if args.planner == "deepseek":
            from ..adapters.deepseek import DeepSeekPlanner

            llm = DeepSeekPlanner.from_env()
        for case in cases:
            s = run_episode(case, args.planner, args.recovery == "on", args.out_root, llm=llm)
            print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
        return
    evaluate_set(args.set_name, args.planner, args.recovery == "on", args.out_root, args.limit,
                 args.runs)


if __name__ == "__main__":
    main()
