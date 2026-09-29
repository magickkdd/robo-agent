"""P4' E5's acceptance batch: does §5.4 show on the ALFWorld text channel?

Zero spend by construction: the decision source is the text backend's own deterministic policy,
so this measures whether the *mechanism* installs and records on a third channel. Whether a
model benefits from the memory is E1's question on the desktop; this is the question SPEC §3 RQ7
asks — does the same mechanism behave consistently across the three channels (desktop
privileged, desktop camera, text)?

Shape, from `configs/experiment/v03_e3_preregistration.json`'s E5 counterpart: K=2 pairs
(seed then read, one store per pair) x {full, wo_episodic_memory}. The text channel's "later
similar task" is another game of the same task family, which is what the `task_kind` term makes
shared; two different families would be a different kind and would deliberately not share.

The interpreter matters and is named in every row: the ALFWorld backend needs textworld and
alfworld, which live in `/home/czx/bstvenv` — a venv whose `bin/python` is a symlink to the
desktop conda interpreter, and whose site-packages is found from the *invoked* path. Driving
this channel with the desktop interpreter fails with `BackendError: textworld/alfworld ...`,
which is why the v0.2 report §10 called it `<BST>`.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
BST = "/home/czx/bstvenv/bin/python"
ARCHIVE = "/home/czx/embodied-agent-batches/v03/e5"
MODEL_CONFIG = "configs/models/bst_text_agnes.yaml"


def run_slot_cli(*, manifest, run_root, slot, ablation, store, data_dir, limit_env_check=True):
    cmd = [BST, "-m", "embodied_agent.benchmark.cli", "run",
           "--segment", "dev",
           "--task-manifest", manifest,
           "--model-config", MODEL_CONFIG,
           "--run-root", run_root,
           "--single", str(slot),
           "--fresh"]
    if data_dir:
        cmd += ["--data-dir", data_dir]
    if ablation:
        cmd += ["--ablation", ablation]
    if store:
        cmd += ["--experience-store", store]
    env = {**os.environ, "PYTHONPATH": "."}
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, env=env)
    tail = [ln for ln in (r.stdout + r.stderr).splitlines()
            if "pybullet" not in ln and ln.strip()]
    return {"cmd": " ".join(cmd[1:]), "rc": r.returncode,
            "wall_s": round(time.time() - t0, 2), "said": tail[-6:]}


def read_episode(run_root):
    eps = sorted(glob.glob(os.path.join(run_root, "episodes", "*")))
    if not eps:
        return {"present": False, "run_root": run_root}
    ep = eps[0]
    path = os.path.join(ep, "episode_summary.json")
    if not os.path.exists(path):
        return {"present": False, "run_root": run_root, "episode_dir": ep}
    summary = json.load(open(path, encoding="utf-8"))
    types = {}
    if os.path.exists(os.path.join(ep, "events.jsonl")):
        for line in open(os.path.join(ep, "events.jsonl"), encoding="utf-8"):
            if line.strip():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                types[rec.get("type")] = types.get(rec.get("type"), 0) + 1
    return {"present": True, "episode_dir": ep,
            "episode_id": summary.get("episode_id"),
            "task_type": summary.get("task_type"),
            "memory_event_counts": {k: v for k, v in types.items()
                                    if str(k).startswith("memory_")},
            "all_event_types": types,
            "episodic_block": summary.get("episodic"),
            "official_won": (summary.get("evaluation") or {}).get("official_won"),
            "tokens": summary.get("tokens"),
            "outcome": summary.get("outcome")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="/tmp/bst_v01/task_manifest.json")
    ap.add_argument("--out-root", default=ARCHIVE)
    ap.add_argument("--pairs", type=int, default=2)
    ap.add_argument("--seed-slot", type=int, default=0)
    ap.add_argument("--read-slot", type=int, default=1)
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args()

    if not os.path.exists(args.manifest):
        print(f"no task manifest at {args.manifest}; run "
              f"`bst env-check` + `bst tasks` first (zero spend)", file=sys.stderr)
        return 2

    import shutil
    if os.path.exists(args.out_root):
        shutil.rmtree(args.out_root)
    os.makedirs(args.out_root, exist_ok=True)

    report = {"spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §6 E5 / §3 RQ7",
              "phase": "P4'-E5", "billed_calls": 0, "interpreter": BST,
              "pairs": args.pairs, "arms": ["full", "wo_episodic_memory"],
              "episodes": [], "criteria": {}}
    print(f"manifest: {args.manifest}", flush=True)

    for p in range(args.pairs):
        for arm in ("full", "wo_episodic_memory"):
            cell = os.path.join(args.out_root, f"p{p}_{arm}")
            os.makedirs(cell, exist_ok=True)
            store = os.path.join(cell, "store.jsonl")
            open(store, "w").close()
            for role, slot in (("seed", args.seed_slot + 2 * p),
                               ("read", args.read_slot + 2 * p)):
                print(f"[E5] p{p} {arm} {role}: slot {slot} ...", flush=True)
                run = run_slot_cli(manifest=args.manifest, run_root=cell, slot=slot,
                                   ablation=arm, store=store, data_dir=args.data_dir)
                seen = read_episode(cell)
                report["episodes"].append({"pair": p, "arm": arm, "role": role, "slot": slot,
                                           "run": run, "seen": seen})
                print(f"      rc={run['rc']} {run['wall_s']}s  "
                      f"memory={json.dumps(seen.get('memory_event_counts', {}))}  "
                      f"episodic={'yes' if seen.get('episodic_block') else 'NO'}",
                      flush=True)
                if run["rc"] != 0:
                    print("      said:", json.dumps(run["said"], ensure_ascii=False)[:400],
                          flush=True)

    # ------------------------------------------------------------- criteria
    c1 = {}
    for e in report["episodes"]:
        if e["arm"] == "full" and e["role"] == "read" and e["seen"].get("present"):
            c1[f"p{e['pair']}"] = {
                "task_type": e["seen"].get("task_type"),
                "memory_event_counts": e["seen"].get("memory_event_counts"),
                "rows_retrieved": (e["seen"].get("episodic_block") or {}).get("rows_retrieved"),
                "retrievals": (e["seen"].get("episodic_block") or {}).get("retrievals"),
                "store_size_before": (e["seen"].get("episodic_block") or {}).get(
                    "store_size_before"),
            }
    report["criteria"]["1_full_arm_read_retrieves"] = c1

    c2 = {}
    for e in report["episodes"]:
        if e["arm"] == "wo_episodic_memory" and e["seen"].get("present"):
            c2[f"p{e['pair']}/{e['role']}"] = e["seen"].get("memory_event_counts")
    report["criteria"]["2_control_arm_memory_is_silent"] = c2

    c3 = {f"p{e['pair']}/{e['arm']}/{e['role']}": bool(e["seen"].get("episodic_block"))
          for e in report["episodes"] if e["seen"].get("present")}
    report["criteria"]["3_episodic_block_present"] = c3

    path = os.path.join(args.out_root, "e5_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print("\n=== E5 acceptance ===")
    print("1. full arm read episodes:", json.dumps(c1, ensure_ascii=False))
    print("2. control arm memory_* (must be empty):", json.dumps(c2, ensure_ascii=False))
    print(f"3. episodic block present: {sum(1 for v in c3.values() if v)}/{len(c3)}")
    print("report:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
