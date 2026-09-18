"""S1 CLI contract (spec section 15).

Commands:
  python -m embodied_agent.cli doctor --backend pybullet
  python -m embodied_agent.cli sim-smoke [--gui]
  python -m embodied_agent.cli run --task <suite:task_id|yaml> --planner rule|deepseek [--gui]
  python -m embodied_agent.cli evaluate --suite smoke|dev|s1-clean|s1-fault|negative
        --planner rule|deepseek [--recovery on|off] [--headless] [--limit N]
  python -m embodied_agent.cli report --run-id <run_dir>
  python -m embodied_agent.cli replay --episode <episode_dir>

Exit codes (implementation contract):
  0 success | 1 task failure | 2 needs clarification | 3 config error | 4 infrastructure error
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

EXIT_OK = 0
EXIT_TASK_FAILED = 1
EXIT_NEEDS_CLARIFICATION = 2
EXIT_CONFIG_ERROR = 3
EXIT_INFRA_ERROR = 4


def _load_dotenv():
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def cmd_doctor(args) -> int:
    """M0 environment report (PyBullet backend): reuse the W0 precheck."""
    script = os.path.join(os.path.dirname(__file__), "..", "work", "w0_precheck.py")
    if args.backend != "pybullet":
        print(f"unknown backend {args.backend}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    rc = os.system(f"{sys.executable} {script} > /dev/null 2>&1")
    report = os.path.join(os.path.dirname(__file__), "..", "outputs", "w0_precheck", "precheck.json")
    with open(report, encoding="utf-8") as f:
        data = json.load(f)
    for name, check in data["checks"].items():
        print(f"[{'PASS' if check['ok'] else 'FAIL'}] {name}: {check['detail']}")
    print("doctor:", "OK" if data["all_ok"] else "FAILED")
    return EXIT_OK if data["all_ok"] and rc == 0 else EXIT_INFRA_ERROR


def cmd_sim_smoke(args) -> int:
    """GUI/DIRECT scene load, 1000 stable steps, one screenshot."""
    try:
        from .core.scene import PhysicsScene

        scene = PhysicsScene(seed=0, gui=args.gui)
        scene.settle(1.0)
        out = os.path.join("outputs", "sim_smoke.png")
        scene.render(out)
        scene.close()
        print(f"sim-smoke OK, frame -> {out}")
        if args.gui:
            sys.stdout.flush(); sys.stderr.flush()
            os._exit(EXIT_OK)
        return EXIT_OK
    except Exception as e:  # noqa: BLE001
        print(f"sim-smoke infrastructure error: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR


def cmd_run(args) -> int:
    from .evaluation import run as _run_mod
    _run_mod.set_verbose(args.verbose)
    from .evaluation.run import run_episode
    from .evaluation.tasks import build_set

    _load_dotenv()
    suite, _, task_id = args.task.partition(":")
    if task_id:
        cases = [c for c in build_set(suite) if c.task_id == task_id]
    elif args.task.endswith(".yaml"):
        from .evaluation.tasks import task_case_from_yaml

        cases = [task_case_from_yaml(args.task)]
    else:
        cases = build_set(suite)
    if not cases:
        print("config error: task not found", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    llm = None
    if args.planner == "deepseek":
        from .adapters.deepseek import DeepSeekPlanner

        try:
            llm = DeepSeekPlanner.from_env()
        except Exception as e:  # noqa: BLE001
            print(f"config error: {e}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
    s = run_episode(cases[0], args.planner, args.recovery == "on", args.out, llm=llm, gui=args.gui)
    print(json.dumps({k: s[k] for k in ("task_id", "terminal_status", "independent_complete_success",
                                        "objects_completed", "objects_total")}, ensure_ascii=False))
    code = EXIT_OK
    if s["terminal_status"] == "needs_clarification":
        code = EXIT_NEEDS_CLARIFICATION
    elif s["terminal_status"] != "success":
        code = EXIT_TASK_FAILED
    if args.gui:
        # pybullet's GUI example browser segfaults during interpreter teardown
        # (after all work is done) — flush and hard-exit to avoid it
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(code)
    return code


def cmd_evaluate(args) -> int:
    from .evaluation import run as _run_mod
    _run_mod.set_verbose(args.verbose)
    from .evaluation.run import evaluate_set

    _load_dotenv()
    suite = args.suite.replace("_", "-")
    if suite not in ("smoke", "dev", "s1-clean", "s1-fault", "negative"):
        print(f"config error: unknown suite {suite}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        report = evaluate_set(suite, args.planner, args.recovery == "on", args.out, args.limit)
    except KeyError as e:
        print(f"config error: missing {e} (e.g. API key)", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    if report.get("set") == "negative":
        return EXIT_OK if report["expected_met"] == report["n"] else EXIT_TASK_FAILED
    rate = report.get("success_rate", 0.0)
    return EXIT_OK if rate >= 0.8 else EXIT_TASK_FAILED


def cmd_llm(args) -> int:
    from .evaluation.run import llm_transcript

    if not os.path.isdir(args.episode):
        print(f"config error: episode dir {args.episode} not found", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    llm_transcript(args.episode)
    return EXIT_OK


def cmd_report(args) -> int:
    run_dir = args.run_id
    report = os.path.join(run_dir, "report.json")
    if os.path.exists(report):
        print(open(report, encoding="utf-8").read())
        return EXIT_OK
    summary = os.path.join(run_dir, "summary.json")
    if os.path.exists(summary):
        print(open(summary, encoding="utf-8").read())
        return EXIT_OK
    print(f"config error: no report in {run_dir}", file=sys.stderr)
    return EXIT_CONFIG_ERROR


def cmd_replay(args) -> int:
    from .evaluation.run import model_response_replay, trajectory_replay

    ep = args.episode
    if not os.path.isdir(ep):
        print(f"config error: episode dir {ep} not found", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    trajectory_replay(ep)
    model_response_replay(ep)
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser("embodied_agent S1 CLI")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor")
    d.add_argument("--backend", default="pybullet")
    d.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("sim-smoke")
    s.add_argument("--gui", action="store_true")
    s.set_defaults(fn=cmd_sim_smoke)

    r = sub.add_parser("run")
    r.add_argument("--task", required=True, help="suite[:task_id] or task YAML")
    r.add_argument("--planner", default="rule", choices=["rule", "deepseek", "fixture"])
    r.add_argument("--recovery", default="on", choices=["on", "off"])
    r.add_argument("--gui", action="store_true")
    r.add_argument("--verbose", action="store_true", help="print LLM planning results in real time")
    r.add_argument("--out", default="runs")
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("evaluate")
    e.add_argument("--suite", required=True)
    e.add_argument("--planner", default="rule", choices=["rule", "deepseek", "fixture"])
    e.add_argument("--recovery", default="on", choices=["on", "off"])
    e.add_argument("--headless", action="store_true", default=True)
    e.add_argument("--verbose", action="store_true", help="print LLM planning results in real time")
    e.add_argument("--limit", type=int, default=None)
    e.add_argument("--out", default="runs")
    e.set_defaults(fn=cmd_evaluate)

    p = sub.add_parser("report")
    p.add_argument("--run-id", required=True)
    p.set_defaults(fn=cmd_report)

    pl = sub.add_parser("replay")
    pl.add_argument("--episode", required=True)
    pl.set_defaults(fn=cmd_replay)

    l = sub.add_parser("llm")
    l.add_argument("--episode", required=True, help="episode dir; prints all LLM exchanges")
    l.set_defaults(fn=cmd_llm)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return EXIT_INFRA_ERROR


if __name__ == "__main__":
    sys.exit(main())
