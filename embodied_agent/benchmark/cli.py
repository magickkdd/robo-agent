"""SPEC-BST 12: the commands a BST run is actually executed with.

A separate entry point from `embodied_agent.cli` for one practical reason and one
substantive one. Practically, `embodied_agent.cli` is the desktop (physics)
experiment: importing its parser is harmless, but the two batches run in different
interpreters with different packages, and one command namespace that only half
worked in each would invite running the wrong experiment. Substantively, a BST run
is sealed in a *run root* (§10.4) rather than a `runs/<set>` directory, and every
command here names its config file and its output directory on the command line so
the sealed headers, the schedule and the ledger can be pointed at a path outside
the repository.

  python -m embodied_agent.benchmark.cli env-check   --data-dir ~/.cache/alfworld \\
      --out /tmp/bst_v01/environment_check.json
  python -m embodied_agent.benchmark.cli tasks       --data-dir ~/.cache/alfworld \\
      --out /tmp/bst_v01/task_manifest.json
  python -m embodied_agent.benchmark.cli run         --segment dev --run-root /tmp/bst_v01/dev \\
      --task-manifest /tmp/bst_v01/task_manifest.json \\
      --model-config configs/models/bst_text_agnes.yaml
  python -m embodied_agent.benchmark.cli run         --segment screening --run-root /tmp/bst_v01/bst1
  python -m embodied_agent.benchmark.cli run         --segment full --run-root /tmp/bst_v01/bst1   # 续跑
  python -m embodied_agent.benchmark.cli aggregate    --run-root /tmp/bst_v01/bst1

Exit codes describe the *command*, not the measurements: a batch in which every
episode fails still exits 0, because a low success rate is the thing under
measurement and not a command error (SPEC-BST 12: 成功率低本身不构成验收失败).
  0 command completed | 3 config error | 4 halted (cap, identity guard) or infrastructure error
"""
from __future__ import annotations

import argparse
import json
import os
import sys

EXIT_OK = 0
EXIT_CONFIG_ERROR = 3
EXIT_INFRA_ERROR = 4

DEFAULT_DATA_DIR = os.environ.get("ALFWORLD_DATA") or os.path.expanduser("~/.cache/alfworld")
DEFAULT_OUT = "/tmp/bst_v01"

# segment -> (where its slots live in the task manifest, SPEC-BST 5.3 token cap).
# `dev_train` has no batch cap: 12 slots x the 600,000 per-episode cap already bound
# it, and a second number invented here would be a second budget to honour.
SEGMENTS = {
    "dev_train": (("dev_train", "slots"), None),
    "screening": (("order", "screening_slots"), 8_000_000),
    "full": (("order", "screening_slots", "order", "remainder_slots"), 40_000_000),
}
ALIASES = {"dev": "dev_train", "screen": "screening", "train": "dev_train"}


def _slots_for_segment(manifest: dict, chain) -> list[dict]:
    """The frozen order for one segment. `full` is the screening slots followed by the
    remainder, in the order the manifest already fixed — the 48 are not re-numbered or
    re-drawn when the batch continues (SPEC-BST 5.2).

    The manifest numbers each list from zero, so `full` is two source lists whose
    `slot_index` values overlap on 48 of them. Resumption keys on `slot_index` and so
    does the episode directory name, so taking those numbers as given would mean the
    first 48 unrun slots read as already complete and the rest would be written into
    the screening episodes' own directories. `slot_index` is therefore renumbered to
    one sequence over the concatenated order, and the manifest's own number is kept
    beside it as `manifest_slot_index`. The screening block is first and already runs
    0..47, so the 48 episodes that have been run keep exactly the numbers they had."""
    groups: list[list[dict]] = []
    for i in range(0, len(chain), 2):
        node = manifest
        for part in chain[i:i + 2]:
            node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, list):
            raise SystemExit(f"config error: task manifest has no segment at "
                             f"{'.'.join(chain[i:i+2])}")
        groups.append(node)
    out: list[dict] = []
    for group in groups:
        for slot in sorted(group, key=lambda s: (int(s.get("slot_index", 0)))):
            out.append(dict(slot, manifest_slot_index=int(slot.get("slot_index", 0)),
                            slot_index=len(out)))
    if len({s["slot_index"] for s in out}) != len(out):
        raise SystemExit("config error: the slot plan is not uniquely numbered")
    return out


def cmd_env_check(args) -> int:
    from .envcheck import main as envcheck_main
    print("environment check ->", args.out)
    return envcheck_main(["--data-dir", args.data_dir, "--repo", args.repo,
                          "--out", args.out, "--max-steps", str(args.max_steps)]) or EXIT_OK


def cmd_tasks(args) -> int:
    from .tasks import build
    build(args.data_dir, args.out, args.max_steps)
    return EXIT_OK


def _arm_kwargs(args):
    """`(--ablation, --experience-store)` for the text channel, or every refusal at once.

    A CLI answer has to name every reason together: an operator who fixes the first and re-runs
    into the second learns nothing about how the gate works. The unknown-condition check comes
    from the same registry the desktop entry uses, so "registered here" and "registered there"
    cannot drift.
    """
    from ..core.v02 import ABLATION_CONDITIONS, ablation as ablation_record
    from ..episodic.store import ExperienceStore
    from .planned_runtime import TEXT_EXPERIENCED_ARM_MODULES, text_arm_coherence

    arm_name = getattr(args, "ablation", None)
    store_path = getattr(args, "experience_store", None) or None
    if not arm_name and not store_path:
        return None, None

    problems = []
    if arm_name and arm_name not in ABLATION_CONDITIONS:
        problems.append(f"unknown ablation condition {arm_name!r}; registered: "
                        f"{sorted(ABLATION_CONDITIONS)}")
    elif arm_name:
        off = list(ABLATION_CONDITIONS[arm_name])
        if not store_path and "episodic_memory" in off:
            problems.append(
                f"--ablation {arm_name} needs --experience-store: the arm switches §5.4's module "
                f"off *an installed arm*, and a loop built with no store at all takes a different "
                f"code path that merely happens to behave the same")
        problems += text_arm_coherence(ablation_record(arm_name),
                                       installed=TEXT_EXPERIENCED_ARM_MODULES)
    if problems:
        raise SystemExit("config error:\n  " + "\n  ".join(problems))
    arm = ablation_record(arm_name) if arm_name else None
    store = None
    if store_path:
        try:
            store = ExperienceStore.load(store_path)
        except ValueError as e:
            raise SystemExit(f"config error:\n  --experience-store {store_path!r} cannot be "
                             f"read: {e}")
    return arm, store


def cmd_run(args) -> int:
    from .runner import (BST_SPEC, BatchHalt, freeze_run_root, load_model_config,
                         planner_from_config, run_batch, run_slot, slot_summary_counts)
    from .runtime_alfred import bst_budgets

    if not os.path.isfile(args.task_manifest):
        print(f"config error: no task manifest at {args.task_manifest} "
              f"(run `tasks` first)", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    segment = ALIASES.get(args.segment, args.segment)
    if segment not in SEGMENTS:
        print(f"config error: --segment must be one of {sorted(SEGMENTS)}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    manifest = json.load(open(args.task_manifest, encoding="utf-8"))
    chain, cap = SEGMENTS[segment]
    slots = _slots_for_segment(manifest, chain)
    if args.limit:
        slots = slots[:args.limit]
    token_cap = args.token_cap if args.token_cap is not None else cap
    data_dir = args.data_dir or manifest.get("data_dir") or DEFAULT_DATA_DIR
    budgets = bst_budgets()

    planner, identity = planner_from_config(args.model_config)
    cfg = load_model_config(args.model_config)

    # §5.4's arm door (SPEC-v0.3 §6 E5). Resolved here, before a run root is sealed, so a
    # refusal leaves nothing behind and a batch cannot be half-armed: the store is loaded by
    # the same reader the desktop and MuJoCo entry points use, and a memory arm without one is
    # refused here rather than at the first episode.
    ablation, experience_store = _arm_kwargs(args)

    print(json.dumps({"bst_spec": BST_SPEC, "segment": segment, "slots": len(slots),
                      "token_cap": token_cap, "run_root": os.path.abspath(args.run_root),
                      "data_dir": data_dir, "budgets": budgets.model_dump(mode="json"),
                      "arm": {"condition": (getattr(ablation, "condition", None)
                                            if ablation is not None else None),
                              "experience_store": getattr(experience_store, "path", None),
                              "memory_task_kind": args.memory_task_kind or "(the slot's task_type)"},
                      "model": {k: identity[k] for k in
                                ("provider", "model", "base_url_host", "temperature",
                                 "max_tokens", "max_tokens_cap", "timeout_s", "max_retries",
                                 "decision_prompt", "config_sha256")},
                      "feedback_depth_cfg": cfg.get("feedback_depth")},
                     indent=1), flush=True)

    headers = os.path.join(args.run_root, "manifest.json")
    if not os.path.exists(headers) or args.refreeze:
        frozen = freeze_run_root(
            args.run_root, identity=identity, task_manifest_path=args.task_manifest,
            environment_check_path=args.environment_check,
            compatibility_delta_path=args.compatibility_delta, segment=segment, slots=slots,
            budgets=budgets, extra={"schedule_segment": segment,
                                    "n_slots_this_command": len(slots)})
        print("sealed run headers; git commit:",
              (frozen.get("code") or {}).get("commit", "")[:12],
              "dirty:", (frozen.get("code") or {}).get("dirty"), flush=True)
    else:
        print(f"{headers} exists: provenance not rewritten (pass --refreeze to do so)",
              flush=True)

    if args.single is not None:
        # `is not None`, not truthiness: `--single 0` is a real slot (the first one) and
        # `if args.single:` treated it as "no slot given" and ran the WHOLE segment. Measured
        # by being bitten: an E5 probe meant to run two slots ran all twelve of `dev_train`
        # twice, on the model seat, before anyone noticed. A flag whose value 0 means "off" is
        # a flag that silently does something else.
        one = [s for s in slots if int(s["slot_index"]) == args.single]
        if not one:
            print(f"config error: --single {args.single} is not a slot of {segment}",
                  file=sys.stderr)
            return EXIT_CONFIG_ERROR
        summary = run_slot(one[0], planner=planner, run_root=args.run_root, data_dir=data_dir,
                           budgets=budgets, max_episode_tokens=args.max_episode_tokens,
                           ablation=ablation, experience_store=experience_store,
                           memory_task_kind=args.memory_task_kind)
        print(json.dumps({k: summary.get(k) for k in
                          ("episode_id", "instruction", "tokens", "wall_time_s",
                           "evaluation", "outcome", "episodic")}, indent=1, default=str))
        return EXIT_OK if summary.get("outcome") != "infrastructure_error" else EXIT_INFRA_ERROR

    try:
        progress = run_batch(slots, planner=planner, identity=identity, run_root=args.run_root,
                             data_dir=data_dir, segment=segment, token_cap=token_cap or 1 << 62,
                             budgets=budgets, max_episode_tokens=args.max_episode_tokens,
                             resume=not args.fresh, quiet=args.quiet,
                             ablation=ablation, experience_store=experience_store,
                             memory_task_kind=args.memory_task_kind)
    except BatchHalt as e:
        print(f"halted: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    print(json.dumps(slot_summary_counts(progress), indent=1))
    return EXIT_OK


def cmd_aggregate(args) -> int:
    from .aggregate import aggregate_run_root
    out = aggregate_run_root(args.run_root, task_manifest=args.task_manifest)
    print(json.dumps(out, indent=1, default=str))
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser("embodied_agent.benchmark (BST-1.0 text batch)")
    sub = ap.add_subparsers(dest="command", required=True)

    ec = sub.add_parser("env-check", help="SPEC-BST 12 Phase 0 环境自检")
    ec.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    ec.add_argument("--repo", default=".")
    ec.add_argument("--out", default=os.path.join(DEFAULT_OUT, "environment_check.json"))
    ec.add_argument("--max-steps", type=int, default=60)
    ec.set_defaults(fn=cmd_env_check)

    tk = sub.add_parser("tasks", help="enumerate the pinned data and freeze the run order")
    tk.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    tk.add_argument("--out", default=os.path.join(DEFAULT_OUT, "task_manifest.json"))
    tk.add_argument("--max-steps", type=int, default=60)
    tk.set_defaults(fn=cmd_tasks)

    rn = sub.add_parser("run", help="run one segment of the frozen schedule")
    rn.add_argument("--segment", required=True, help="|".join(sorted(SEGMENTS))
                    + " (dev/screen/train are accepted aliases)")
    rn.add_argument("--task-manifest", required=True, metavar="PATH")
    rn.add_argument("--model-config", required=True, metavar="PATH",
                    help="a configs/models/*.yaml; only its api_key_env, model, base_url, "
                         "sampling and pricing fields are read, and no key is ever written")
    rn.add_argument("--run-root", required=True, metavar="DIR")
    rn.add_argument("--data-dir", default=None)
    rn.add_argument("--environment-check",
                    default=os.path.join(DEFAULT_OUT, "environment_check.json"))
    rn.add_argument("--compatibility-delta", default="docs/bst/compatibility_delta.md")
    rn.add_argument("--token-cap", type=int, default=None,
                    help="override the segment cap for a precheck; a scored batch must not")
    rn.add_argument("--max-episode-tokens", type=int, default=600_000,
                    help="the SPEC-BST 5.3 per-episode cap; passed to every episode")
    rn.add_argument("--limit", type=int, default=None, help="first N slots of the segment")
    rn.add_argument("--single", type=int, default=None, metavar="SLOT_INDEX",
                    help="run exactly one slot and print it: the Phase 2 closed-loop check")
    rn.add_argument("--fresh", action="store_true",
                    help="ignore progress.json and start the segment over")
    rn.add_argument("--refreeze", action="store_true",
                    help="rewrite the sealed headers (a new provenance record, not an edit)")
    rn.add_argument("--ablation", default=None, metavar="CONDITION",
                    help="a §9 condition this text loop can actually show (SPEC-v0.3 §6 E5). "
                         "Absent means the frozen v0.1 loop, which installs no episodic module "
                         "and files no memory_* record")
    rn.add_argument("--experience-store", default=None, metavar="PATH",
                    help="the JSONL file §5.4's memory reads and appends. A missing file is a "
                         "cold start, which is a measured condition; a memory arm without one "
                         "is refused here rather than measured and reported as a memory")
    rn.add_argument("--memory-task-kind", default="", metavar="KIND",
                    help="§5.4's task-kind term. Left empty it is each slot's own task_type "
                         "from the frozen manifest, which is the text backend's task family")
    rn.add_argument("--quiet", action="store_true")
    rn.set_defaults(fn=cmd_run)

    ag = sub.add_parser("aggregate", help="recompute every metric from the sealed logs")
    ag.add_argument("--run-root", required=True, metavar="DIR")
    ag.add_argument("--task-manifest", default=None, metavar="PATH")
    ag.set_defaults(fn=cmd_aggregate)

    args = ap.parse_args(argv)
    from ..adapters.deepseek import strip_env_quotes  # same reader the adapter uses
    env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), strip_env_quotes(v.strip()))
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
