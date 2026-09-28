"""Watch one desktop-stack episode in a PyBullet window (dev viewer, not a measurement).

Why this file exists: `embodied_agent/cli.py run` has no `--gui` flag. The production
path calls `build_scene(case)` with its default everywhere, so graded episodes are
rendered by the tiny renderer into `frame_*.png` next to `events.jsonl`, never into a
window. This script is the smallest thing that shows the *same* production episode
live: it calls the same `resolve_goal` / `run_one_episode`, with the module-level
`build_scene` rebound to `gui=True` and the executor subclassed to print each skill
call. Nothing is re-implemented, so what moves is the real arm under the real
verifier — but a window is a viewer, not evidence; the log stays the record.

The benchmark path (`embodied_agent/benchmark/cli.py`) has no GUI at all: ALFWorld /
TextWorld is text, `no ai2thor, no torch, no CUDA` (its own environment_check.json).

Run:
  DISPLAY=:0 PYTHONPATH=. python work/gui_agent.py --task smoke:smoke_clean --pause 1
  DISPLAY=:0 PYTHONPATH=. python work/gui_agent.py --task dev:dev_c1 --mode B \
      --planner deepseek --model-config configs/models/agnes.yaml

`--planner rule` (the default) asks for no HTTP requests at all; the two model
planners do, and the goal parse alone is one billed request before the window opens.

pybullet's GUI example browser segfaults during teardown on this build (the same
reason `cli.py sim-smoke --gui` calls `os._exit`), so this ends with `os._exit(0)`
after `--stay` seconds of a still window. Close the window early with Ctrl-C.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

os.environ.setdefault("DISPLAY", ":0")

from embodied_agent.cli import _planner_for                      # noqa: E402
from embodied_agent.core.scene import PhysicsScene               # noqa: E402
from embodied_agent.evaluation import run as run_mod             # noqa: E402
from embodied_agent.evaluation.tasks import SET_NAMES, build_set  # noqa: E402

EXIT_CONFIG_ERROR = 3
EXIT_INFRA_ERROR = 4


def main() -> int:
    ap = argparse.ArgumentParser("gui viewer for one desktop episode")
    ap.add_argument("--task", required=True, metavar="SET[:CASE_ID]",
                    help=f"a set name {SET_NAMES}, optionally narrowed to one case id")
    ap.add_argument("--mode", default="B", choices=["A", "B", "C"])
    ap.add_argument("--planner", default="rule", choices=["rule", "fixture", "deepseek"],
                    help="rule is offline and asks for nothing; deepseek is the online subject")
    ap.add_argument("--model-config", default=None)
    ap.add_argument("--repeat", type=int, default=0)
    ap.add_argument("--out", default="runs/gui")
    ap.add_argument("--stay", type=float, default=20.0,
                    help="seconds the finished scene stays on screen")
    ap.add_argument("--pause", type=float, default=0.0,
                    help="seconds to hold after each skill boundary; the offline rule "
                         "plan renders its whole episode in ~5 s, which is a blur")
    a = ap.parse_args()

    suite, _, case_id = a.task.partition(":")
    if suite not in SET_NAMES:
        print(f"config error: unknown set {suite!r}; choose from {SET_NAMES}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    cases = [c for c in build_set(suite) if not case_id or c.task_id == case_id]
    if len(cases) != 1:
        print(f"config error: {a.task!r} selects {len(cases)} cases; the viewer takes one",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR
    case = cases[0]
    print(f"DISPLAY={os.environ['DISPLAY']}  task={case.task_id}  seed={case.seed}  "
          f"mode={a.mode}  planner={a.planner}", flush=True)
    print(f"utterance: {case.utterance}", flush=True)

    try:
        planner = _planner_for(a.planner, a.model_config)
    except Exception as e:  # noqa: BLE001 - a missing key is a config problem
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # goal resolution stays headless on purpose: it builds and closes its own scene,
    # and a window that opens and vanishes before the episode starts is only noise
    resolution = run_mod.resolve_goal(case, a.repeat, planner, a.out,
                                      log_call=run_mod.goal_logger(a.out))
    if resolution.error is not None or resolution.goal is None:
        print(f"goal resolution failed: {resolution.error}", file=sys.stderr)
        return EXIT_INFRA_ERROR

    orig_build_scene = run_mod.build_scene
    run_mod.build_scene = lambda c, gui=False: orig_build_scene(c, gui=True)
    PhysicsScene.close = lambda self: None            # the window must outlive the episode

    class WatchingExecutor(run_mod._FramedExecutor):
        def execute(self, call):
            print(f"\n[{self._frames + 1}] {call.skill} "
                  f"{json.dumps(call.args, ensure_ascii=False)}", flush=True)
            res = super().execute(call)
            print(f"     -> {res.status}"
                  f"{' / ' + str(res.failure_code) if res.failure_code else ''}", flush=True)
            if a.pause:
                time.sleep(a.pause)
            return res

    run_mod._FramedExecutor = WatchingExecutor

    try:
        summary = run_mod.run_one_episode(case, a.repeat, a.mode, resolution, planner,
                                          a.out, suite, frames=True)
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR

    r = summary["result"]
    print("\n" + json.dumps({"episode_id": summary["episode_id"],
                             "dir": (summary["artifacts"] or {}).get("episode_dir"),
                             "frames_written": (summary["artifacts"] or {}).get("frames"),
                             "terminal_status": r["terminal_status"],
                             "failure_type": r["failure_type"],
                             "objects_completed": f'{r["objects_completed"]}/{r["objects_total"]}',
                             "decision_rounds": r["decision_rounds"],
                             "skill_calls": r["skill_calls"],
                             "http_requests": r["http_requests"],
                             "wall_time_s": summary["wall_time_s"]},
                            ensure_ascii=False, indent=1), flush=True)
    print(f"window stays up {a.stay:g}s (Ctrl-C to end now)", flush=True)
    sys.stdout.flush()
    time.sleep(a.stay)
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
