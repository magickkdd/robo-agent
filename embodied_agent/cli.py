"""Reproducible entry points (SPEC 8 file map: cli.py 暴露规划模式与实验入口).

The commands are the ones the experiment is actually run with, and each one is a
thin shell over a module that could be called from a test:

  doctor       environment precheck (PyBullet backend)
  sim-smoke    scene load, 1000 stable steps, one frame
  run          one episode, one arm: --mode A|B|C
  evaluate     a frozen set x arms x repeats through the shared base
  report       statistics and attribution for a finished run directory
  replay       the two *different* things replay can mean (SPEC 7 naming):
                 --recorded  read back the logged trajectory, no physics
                 --plan      re-execute the episode's last plan in a fresh scene
  freeze       write or check the task-list hash a run must match
  blind-review the SPEC 11.4 sheet of contested items, and the human verdicts on it
  llm          the model exchanges recorded for one episode

There is deliberately no `--recovery` switch. Whether to retry, re-plan or stop
is the model's decision; a CLI flag that made that choice for it would be the
runtime taking over the thing under measurement (SPEC 2, 11.2).

Exit codes (implementation contract):
  0 success | 1 task failure | 2 needs clarification | 3 config error | 4 infrastructure error
"""
from __future__ import annotations

import argparse
import json
import os
import sys

EXIT_OK = 0
EXIT_TASK_FAILED = 1
EXIT_NEEDS_CLARIFICATION = 2
EXIT_CONFIG_ERROR = 3
EXIT_INFRA_ERROR = 4

# What `--planner` accepts, in one place, and checked by `_planner_for`: the three
# arms differ only in who decides, so an unrecognized kind must be refused rather
# than fall through to the online planner and spend requests nobody asked for.
PLANNER_KINDS = ("rule", "fixture", "deepseek")


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
    """Environment report; reuses the W0 precheck rather than restating it."""
    script = os.path.join(os.path.dirname(__file__), "..", "work", "w0_precheck.py")
    if args.backend != "pybullet":
        print(f"unknown backend {args.backend}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    rc = os.system(f"{sys.executable} {script} > /dev/null 2>&1")
    report = os.path.join(os.path.dirname(__file__), "..", "outputs", "w0_precheck", "precheck.json")
    if not os.path.exists(report):
        print("infrastructure error: precheck produced no report", file=sys.stderr)
        return EXIT_INFRA_ERROR
    with open(report, encoding="utf-8") as f:
        data = json.load(f)
    for name, check in data["checks"].items():
        print(f"[{'PASS' if check['ok'] else 'FAIL'}] {name}: {check['detail']}")
    print("doctor:", "OK" if data["all_ok"] else "FAILED")
    return EXIT_OK if data["all_ok"] and rc == 0 else EXIT_INFRA_ERROR


def cmd_sim_smoke(args) -> int:
    try:
        from .core.scene import PhysicsScene

        scene = PhysicsScene(seed=0, gui=args.gui)
        scene.settle(1.0)
        out = os.path.join("outputs", "sim_smoke.png")
        scene.render(out)
        scene.close()
        print(f"sim-smoke OK, frame -> {out}")
        if args.gui:
            # pybullet's GUI example browser segfaults during interpreter
            # teardown, after all work is done
            sys.stdout.flush(); sys.stderr.flush()
            os._exit(EXIT_OK)
        return EXIT_OK
    except Exception as e:  # noqa: BLE001
        print(f"sim-smoke infrastructure error: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR


def _planner_for(kind: str):
    """None is the deterministic rule interpreter, and is labelled as such —
    it is never reported as a model result (SPEC 11.3)."""
    if kind not in PLANNER_KINDS:
        raise ValueError(f"unknown planner kind {kind!r}; choose from {PLANNER_KINDS}")
    if kind == "rule":
        return None
    _load_dotenv()
    if kind == "fixture":
        from .adapters.deepseek import FixturePlanner

        return FixturePlanner()
    from .adapters.deepseek import DeepSeekPlanner

    return DeepSeekPlanner.from_env()


def cmd_run(args) -> int:
    from .evaluation.run import goal_logger, resolve_goal, run_one_episode
    from .evaluation.tasks import SET_NAMES, build_set

    suite, _, case_id = args.task.partition(":")
    if suite not in SET_NAMES:
        print(f"config error: unknown set {suite!r} in --task {args.task!r}; "
              f"choose from {SET_NAMES}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    cases = [c for c in build_set(suite) if not case_id or c.task_id == case_id]
    if len(cases) != 1:
        print(f"config error: {args.task!r} selects {len(cases)} cases; run takes exactly one",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        planner = _planner_for(args.planner)
    except Exception as e:  # noqa: BLE001 - a missing key is a config problem
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    case = cases[0]
    try:
        resolution = resolve_goal(case, args.repeat, planner, args.out,
                                  log_call=goal_logger(args.out))
        if resolution.error is not None or resolution.goal is None:
            print(f"goal resolution failed: {resolution.error}", file=sys.stderr)
            return EXIT_INFRA_ERROR
        summary = run_one_episode(case, args.repeat, args.mode, resolution, planner, args.out,
                                  suite, frames=not args.no_frames)
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    r = summary["result"]
    print(json.dumps({"episode_id": summary["episode_id"],
                      "dir": os.path.join(args.out, "episodes", summary["episode_id"]),
                      "terminal_status": r["terminal_status"],
                      "failure_type": r["failure_type"],
                      "independent_complete_success": summary["score"]["complete_success"],
                      "objects_completed": r["objects_completed"],
                      "objects_total": r["objects_total"],
                      "decision_rounds": r["decision_rounds"],
                      "skill_calls": r["skill_calls"],
                      "http_requests": r["http_requests"]}, ensure_ascii=False, indent=1))
    if r["terminal_status"] == "needs_clarification":
        return EXIT_NEEDS_CLARIFICATION
    return EXIT_OK if r["terminal_status"] == "success" else EXIT_TASK_FAILED


def cmd_evaluate(args) -> int:
    from .evaluation.run import InfraError, run_group
    from .evaluation.tasks import SET_NAMES

    if args.set not in SET_NAMES:
        print(f"config error: unknown set {args.set!r}; choose from {SET_NAMES}",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR
    modes = tuple(m.strip().upper() for m in args.modes.split(",") if m.strip())
    try:
        out = run_group(args.set, modes=modes, planner_kind=args.planner, repeats=args.repeats,
                        out_root=args.out_root,
                        case_ids=[c for c in args.cases.split(",") if c.strip()] or None,
                        limit=args.limit, frames=not args.no_frames,
                        model_config=args.model_config, frozen_path=args.frozen)
    except InfraError as e:
        # a pre-flight refusal: nothing was measured, and the reason is stated
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    # `run_group` prints the run summary itself; the exit code only says whether
    # the measurement happened, not what it showed (SPEC 11.5).
    infra = out["outcomes"].get("infrastructure_error", 0) + \
        out["outcomes"].get("goal_resolution_error", 0)
    if infra:
        print(f"{infra} runs could not be measured at all (see errors.jsonl); "
              f"they stay in the denominator (SPEC 11.5)", file=sys.stderr)
        return EXIT_INFRA_ERROR
    return EXIT_OK


def cmd_report(args) -> int:
    from .evaluation.report import render_markdown, write_report

    if not os.path.isdir(args.run_dir):
        print(f"config error: no run directory at {args.run_dir}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        report = write_report(args.run_dir)
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json_only
                  else render_markdown(report))
    return EXIT_OK


def cmd_replay(args) -> int:
    from .evaluation.run import InfraError, plan_rerun, recorded_replay

    try:
        if args.recorded:
            recorded_replay(args.run_dir)
        else:
            plan_rerun(args.run_dir, args.set)
    except (InfraError, FileNotFoundError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    return EXIT_OK


def cmd_freeze(args) -> int:
    from .evaluation.tasks import check_frozen, dump_frozen, frozen_manifest

    if args.check:
        ok, msg = check_frozen(args.check)
        print(("frozen list matches: " if ok else "MISMATCH: ") + msg)
        return EXIT_OK if ok else EXIT_CONFIG_ERROR
    live = frozen_manifest()
    previous = None
    if os.path.exists(args.out):
        ok, msg = check_frozen(args.out)
        if not ok:
            # This file is the pre-registration: writing over a list that no longer
            # matches the code is how a result stops being traceable to the tasks it
            # was produced on. Say what changed and require the amendment by name.
            with open(args.out, encoding="utf-8") as f:
                previous = str(json.load(f).get("sha256", ""))
            if not args.amend:
                print(f"refusing to overwrite {args.out}: {msg}\n"
                      f"live list hashes to {live['sha256']}. Re-freeze with --amend "
                      f"only as a recorded change to the pre-registered task set.")
                return EXIT_CONFIG_ERROR
    path = dump_frozen(args.out)
    out = {"written": path, "sha256": live["sha256"],
           "cases": {k: len(v) for k, v in live["sets"].items()}}
    if previous:
        out["amended_from"] = previous
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_state_util(args) -> int:
    """SPEC 11.3/11.4 状态分叉动作适配率: one request per forked context, scored.

    Nothing is executed here — the contexts come from real episodes, the answers
    are only *read*. A low rate is a result, so it exits 0; an unreachable pair is
    a broken measurement, so it does not.
    """
    from .evaluation.sources import LLMDecisionSource, RulePolicySource
    from .evaluation.state_utilization import build_forks, run_state_utilization

    try:
        planner = _planner_for(args.planner)
    except Exception as e:  # noqa: BLE001 - a missing key is a config problem
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    only = [p for p in (args.only or []) if p.strip()] or None
    forks, _ = build_forks(only=only)
    if not forks:
        print("config error: no fork reached any pair", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    out_root = args.out or os.path.join("runs", f"state_util_{args.planner}")
    call_log = os.path.join(out_root, "model_calls.jsonl")
    # a source per context keeps the forks independent (SPEC 11.3), so the planner
    # is captured once and each answer is a fresh request; the same adapter log an
    # episode run writes, so the diagnostic's HTTP cost is auditable in the same way
    def log_call(payload, latency):
        with open(call_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({**payload, "latency_s": round(latency, 3)},
                               ensure_ascii=False, default=str) + "\n")

    def source_factory():
        if planner is None:
            return RulePolicySource()
        return LLMDecisionSource(planner, log=log_call)

    payload = run_state_utilization(source_factory, out_root=out_root, label=args.planner,
                                    only=only, forks=forks)
    s = payload["summary"]
    print(json.dumps({"written": os.path.join(out_root, "state_utilization.json"),
                      "policy": s["policy_label"], "provider": s["policy_provider"],
                      "action_fit_rate": s["action_fit_rate"],
                      "contexts": f'{s["contexts_fitted"]}/{s["contexts_requested"]}',
                      "pairs_with_both_arms_fitting":
                          f'{s["pairs_with_both_arms_fitting"]}/{s["pairs_assessed"]}',
                      "pairs_that_did_not_use_the_distinction":
                          s["pairs_that_did_not_use_the_distinction"],
                      "candidate_choice_not_tested_in":
                          s["pairs_where_candidate_choice_went_untested"],
                      "synthetic_arms": s["synthetic_arms"],
                      "provider_counters": s["provider_counters"],
                      "unreachable_pairs": s["unreachable_pairs"]},
                     ensure_ascii=False, indent=1))
    if s["unreachable_pairs"]:
        print(f"incomplete: {len(s['unreachable_pairs'])} frozen pair(s) reached no context; "
              "the rate above is not the 12-pair diagnostic", file=sys.stderr)
        return EXIT_INFRA_ERROR
    return EXIT_OK


def _contested_pointers(run_dir: str) -> list[dict]:
    """The `needs_blind_review` pointers of a finished batch, read from the report
    if one was written and re-derived from the logs otherwise. The two are the same
    deterministic reading; the pointer set must not be a third opinion."""
    path = os.path.join(run_dir, "report.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            behaviour = json.load(f).get("behaviour") or {}
    else:
        from .evaluation.report import behaviour_metrics, load_rows

        behaviour = behaviour_metrics(load_rows(run_dir))
    return [p for arm, v in behaviour.items()
            if arm != "_definitions" and isinstance(v, dict)
            for p in (v.get("needs_blind_review") or [])]


def cmd_blind_review(args) -> int:
    """SPEC 11.4 blind review: print the contested items, or judge them.

    The criteria live in `configs/experiment/` and are *not* selectable here: an
    operator who could pass a different rubric on the command line could move the
    goalposts after the verdicts arrived, which is the one thing the rubric's own
    prohibitions forbid. What is selectable is the human input (`--records`) and
    where the derived artifacts go.

    Exit codes keep the three states a reader must not confuse apart: 0 = measured
    (including "adverse", and including "nothing was contested"), 3 = the criteria or
    the verdict file is not usable as input, 4 = the sheet cannot be built from this
    run directory at all.
    """
    from .evaluation.blind_review import (SHEET_NAME, review_state, write_template)

    if not os.path.isdir(args.run_dir):
        print(f"config error: no run directory at {args.run_dir}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if not os.path.exists(os.path.join(args.run_dir, "episodes.csv")):
        print(f"config error: {args.run_dir} holds no episodes.csv — blind review judges a "
              "finished batch, and an empty run has nothing contested rather than a clean "
              "record", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if args.records and not os.path.exists(args.records):
        print(f"config error: no verdict file at {args.records} (write one from "
              f"'--template <path>')", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        pointers = _contested_pointers(args.run_dir)
    except Exception as e:  # noqa: BLE001 - unreadable artifacts are this command's input
        print(f"config error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        sheet, state = review_state(args.run_dir, pointers, records_path=args.records)
    except Exception as e:  # noqa: BLE001 - a broken sheet is a finding, not a verdict
        print(f"infrastructure error: the blind sheet could not be built from "
              f"{args.run_dir}: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    rc = EXIT_OK
    if args.sheet:
        out = args.sheet if args.sheet.endswith(".json") else os.path.join(args.sheet, SHEET_NAME)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(sheet, f, ensure_ascii=False, indent=2)
        print(f"sheet -> {out}")
    if args.template:
        os.makedirs(os.path.dirname(os.path.abspath(args.template)), exist_ok=True)
        n = write_template(sheet, args.template)
        print(f"template -> {args.template} ({n} blank rows: {len(sheet['items'])} items x "
              f"{sheet['reviewers']['count']} reviewers; a blank verdict is refused, so "
              f"handing in the form cannot be mistaken for a result)")
        return rc
    print(json.dumps({k: v for k, v in state.items() if k != "prohibitions"},
                     ensure_ascii=False, indent=1, default=str))
    if state.get("refused"):
        print(f"{len(state['refused'])} verdict row(s) refused; they are excluded from the "
              "rates above and the reasons are printed with them — fix the file, do not "
              "resubmit it as is (SPEC 11.4)", file=sys.stderr)
        rc = EXIT_CONFIG_ERROR
    return rc


def cmd_llm(args) -> int:
    """Every model exchange recorded for one episode — or for one diagnostic run.

    Both write the same `model_calls.jsonl` shape, so the cost of 24 fork requests
    is readable the same way as the cost of an episode (SPEC 7, 11.4). An offline
    run has none, and says so rather than printing an empty block."""
    if not args.file and not args.episode:
        print("config error: llm needs --episode or --file", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    path = args.file or os.path.join(args.episode, "model_calls.jsonl")
    if not os.path.exists(path):
        print(f"no model calls recorded for {path} (an offline run has none)")
        return EXIT_OK
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            print(json.dumps(rec, ensure_ascii=False, default=str))
    return EXIT_OK


def main(argv=None):
    # imported here rather than at module scope: the parser needs the canonical
    # frozen path and the set names as defaults, and neither is a second source for
    # a fact the task list already owns (`SET_NAMES` *is* what `build_set` resolves)
    from .evaluation.tasks import FROZEN_PATH, SET_NAMES

    ap = argparse.ArgumentParser("embodied_agent CLI (continuous-decision experiment)")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="environment precheck")
    d.add_argument("--backend", default="pybullet")
    d.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("sim-smoke", help="scene loads and renders")
    s.add_argument("--gui", action="store_true")
    s.set_defaults(fn=cmd_sim_smoke)

    r = sub.add_parser("run", help="one episode, one arm")
    r.add_argument("--task", required=True, metavar="SET[:CASE_ID]",
                   help=f"a set name {SET_NAMES}, optionally narrowed to one case id")
    r.add_argument("--mode", default="B", choices=["A", "B", "C", "a", "b", "c"])
    r.add_argument("--planner", default="rule", choices=PLANNER_KINDS)
    r.add_argument("--repeat", type=int, default=0)
    r.add_argument("--out", default="runs/single")
    r.add_argument("--no-frames", action="store_true")
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("evaluate", help="a set through the shared base")
    e.add_argument("--set", required=True, choices=SET_NAMES)
    e.add_argument("--planner", default="rule", choices=PLANNER_KINDS)
    e.add_argument("--modes", default="A,B,C")
    e.add_argument("--repeats", type=int, default=1)
    e.add_argument("--cases", default="")
    e.add_argument("--limit", type=int)
    e.add_argument("--out-root", default="runs")
    e.add_argument("--model-config")
    e.add_argument("--frozen", nargs="?", const=FROZEN_PATH, default=None,
                   metavar="PATH",
                   help=f"refuse to run unless the task list matches this file "
                        f"(default when given without a value: {FROZEN_PATH})")
    e.add_argument("--no-frames", action="store_true")
    e.set_defaults(fn=cmd_evaluate)

    p = sub.add_parser("report", help="statistics and attribution for a run directory")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--json-only", action="store_true")
    p.set_defaults(fn=cmd_report)

    rp = sub.add_parser("replay", help="read a trajectory back, or re-run a plan")
    rp.add_argument("--run-dir", required=True)
    rp.add_argument("--recorded", action="store_true",
                    help="print the logged event sequence; advances no physics")
    rp.add_argument("--set", help="set the case belongs to (plan replay)")
    rp.set_defaults(fn=cmd_replay)

    fz = sub.add_parser("freeze", help="write or check the frozen task list")
    fz.add_argument("--out", default=FROZEN_PATH)
    fz.add_argument("--amend", action="store_true",
                    help="allow re-freezing over a list the code has drifted from, and "
                         "print the hash it replaced")
    fz.add_argument("--check", nargs="?", const=FROZEN_PATH, default=None, metavar="PATH",
                    help=f"compare the live task list against this file "
                         f"(default when given without a value: {FROZEN_PATH})")
    fz.set_defaults(fn=cmd_freeze)

    su = sub.add_parser("state-util", help="SPEC 11.3 state-fork action-fit diagnostic "
                                          "(reads contexts, executes nothing)")
    su.add_argument("--planner", choices=PLANNER_KINDS, default="rule",
                    help="rule is the offline control policy; a model planner asks one "
                         "independent request per forked context")
    su.add_argument("--out", default=None, help="where to write state_utilization.json "
                                                "(default: runs/state_util_<planner>)")
    su.add_argument("--only", nargs="*", default=None, metavar="PAIR_ID",
                    help="score a subset of the 12 frozen pairs (a partial run says so)")
    su.set_defaults(fn=cmd_state_util)

    bz = sub.add_parser("blind-review", help="SPEC 11.4: the contested items of a finished "
                                            "batch, and the human verdicts on them")
    bz.add_argument("--run-dir", required=True)
    bz.add_argument("--sheet", default=None, metavar="PATH",
                    help="write the blind sheet here (a directory gets /blind_review/sheet.json); "
                         "no identity field is on it, by construction")
    bz.add_argument("--template", default=None, metavar="PATH",
                    help="write a blank verdict file (one row per item per reviewer) and stop")
    bz.add_argument("--records", default=None, metavar="PATH",
                    help="a filled verdict file to validate and aggregate "
                         "(default: <run-dir>/blind_review/records.jsonl)")
    bz.set_defaults(fn=cmd_blind_review)

    ll = sub.add_parser("llm", help="model exchanges recorded for one episode or one "
                                    "diagnostic run")
    ll.add_argument("--episode", default=None)
    ll.add_argument("--file", default=None, help="a model_calls.jsonl to read directly")
    ll.set_defaults(fn=cmd_llm)

    args = ap.parse_args(argv)
    if getattr(args, "mode", None) is not None:
        args.mode = args.mode.upper()
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return EXIT_INFRA_ERROR


if __name__ == "__main__":
    sys.exit(main())
