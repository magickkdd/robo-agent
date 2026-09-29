"""P1' E3: episodic memory on the continuous-control channel (MuJoCo), answering RQ6.

Zero spend by construction: `--planner rule --perceive privileged` consults no model, and the
question RQ6 asks is whether the memory *mechanism* produces measurable reuse here — which is
answerable with no model in the seat, and answering it with a model would confound the two.

The shape, from the pre-registration (`configs/experiment/v03_e3_preregistration.json`):

    4 pairs x {full, wo_episodic_memory}, 2 episodes per pair-arm, 16 episodes total.
    Within a pair: seed first, then read, sharing one store.
    Three pairs are near-transfer (task_index A -> B, B != A); the fourth is the replication
    pair (same layout, different seed).

`task_index` is this bench's layout index (35 in the split) and the channel ships exactly one
task vocabulary — `TASKS` holds `peg-insert-side-v3` alone — so "a later similar task" here
*is* a different layout. That is the transfer MEM-1 asks about, restricted to what this
channel can express, and it is stated rather than papered over: the desktop conclusion does not
transfer automatically because the predicate shape and the world-reset semantics differ.

Four acceptance criteria, all measured rather than asserted (SPEC §6 E3):
  1. the read episode on `full` files a non-empty `memory_retrieval`, and `store_size` grows
     with pair order — the store is a per-pair file, so growth across pairs is checked on the
     per-arm store digests;
  2. `wo_episodic_memory` files zero `memory_*` records and passes `Ablation.violations()`;
  3. every episode's `episode_summary.json` carries the `episodic` block;
  4. the offline `experience_from_episode` re-derivation equals the online row field for
     field — H-58's criterion, re-checked on a batch rather than inside a test.

Each episode is its own process, so a renderer fault (v0.2's known-unknown cause) fails one
episode instead of the batch.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
#: The MuJoCo backend needs `metaworld`, which is installed in a separate venv at
#: `/home/czx/mwvenv` (metaworld 3.0.0). Its `bin/python` is a *symlink* to the desktop conda
#: interpreter, and that is exactly how it works: Python locates a venv's site-packages from
#: the `pyvenv.cfg` beside the path it was invoked with, so `/home/czx/mwvenv/bin/python`
#: finds the package and `/home/czx/miniforge3/envs/embodied/bin/python` — the same bytes —
#: does not. Measured: `import metaworld` fails under the conda path and succeeds under the
#: venv path. The backend's own refusal names this interpreter correctly
#: (`env.py:117`); driving it with the wrong one is a driver bug, not a channel defect.
PY = "/home/czx/mwvenv/bin/python"
TASK = "peg-insert-side-v3"

#: (pair_id, seed_layout, seed_flag, read_layout, read_flag, kind)
#:
#: The episode seed is `--seed + task_index` (`benchmark_mujoco/cli.py:238`), so the *flag*
#: is what a pair has to name, not the episode seed. Pair p3 is the replication pair and is
#: only a replication pair because of that rule: same layout 0, flag 0 -> episode seed 0, flag
#: 1 -> episode seed 1. Written the other way round (naming episode seeds) it would have run
#: the same episode twice, which is the mistake a first draft of this table made.
PAIRS = [
    ("p1", 0, 0, 1, 0, "near_transfer"),
    ("p2", 2, 0, 3, 0, "near_transfer"),
    ("p3", 0, 0, 0, 1, "replication_same_layout_new_seed"),
    ("p4", 4, 0, 5, 0, "near_transfer"),
]
ARMS = ("full", "wo_episodic_memory")


def run_episode(*, out, task_index, seed, arm, store, horizon, decision_rounds, gui):
    # `python -m embodied_agent.benchmark_mujoco` is a package with no `__main__`, so the
    # entry is its cli module. Measured: the `-m package` form exits 1 in 0.04 s with
    # "cannot be directly executed", which is a driver bug, not a channel refusal.
    cmd = [PY, "-m", "embodied_agent.benchmark_mujoco.cli", "run",
           "--task", TASK,
           "--layouts", str(task_index),
           "--seed", str(seed),
           "--ablation", arm,
           "--perceive", "privileged",
           "--planner", "rule",
           "--experience-store", store,
           "--horizon", str(horizon),
           "--decision-rounds", str(decision_rounds),
           "--skill-calls", "24",
           "--gui", gui,
           "--out", out]
    env = {**os.environ, "PYTHONPATH": ".", "MUJOCO_GL": "osmesa"}
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, env=env)
    tail = [ln for ln in (r.stdout + r.stderr).splitlines() if "pybullet" not in ln]
    return {"cmd": " ".join(cmd[2:]), "rc": r.returncode, "wall_s": round(time.time() - t0, 2),
            "said": tail[-3:]}


def read_episode(out):
    """Everything the acceptance criteria need, from one episode.

    The episode lives at `<out>/episodes/<env>__L<i>__s<s>__<arm>__memstore/`, not at `<out>`
    itself — the runner names the directory from the episode's own identity, which is also
    where the layout and the derived seed can be read back rather than assumed."""
    found = sorted(glob.glob(os.path.join(out, "episodes", "*")))
    if not found:
        return {"present": False, "dir": out,
                "why": "no episodes/ directory", "listing": sorted(os.listdir(out))
                if os.path.isdir(out) else None}
    ep = found[0]
    path = os.path.join(ep, "episode_summary.json")
    if not os.path.exists(path):
        return {"present": False, "dir": ep, "why": "no episode_summary.json",
                "listing": sorted(os.listdir(ep))}
    summary = json.load(open(path, encoding="utf-8"))
    events_path = os.path.join(ep, "events.jsonl")
    types = {}
    rows = []
    if os.path.exists(events_path):
        for line in open(events_path, encoding="utf-8"):
            if not line.strip():
                continue
            rec = json.loads(line)
            types[rec.get("type")] = types.get(rec.get("type"), 0) + 1
            if str(rec.get("type", "")).startswith("memory_"):
                rows.append(rec.get("payload") or {})
    return {
        "present": True,
        "dir": ep,
        "episode_id": os.path.basename(ep),
        "event_types": types,
        "memory_event_counts": {k: v for k, v in types.items() if k.startswith("memory_")},
        "memory_rows": rows,
        "episodic_block": summary.get("episodic"),
        "terminal_status": (summary.get("episode_result") or {}).get("terminal_status"),
        "failure_type": (summary.get("episode_result") or {}).get("failure_type"),
        "official_success": summary.get("official_success"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root", default="/home/czx/embodied-agent-batches/v03/e3")
    ap.add_argument("--horizon", type=int, default=500)
    ap.add_argument("--decision-rounds", type=int, default=32)
    ap.add_argument("--gui", default="none")
    ap.add_argument("--limit-pairs", type=int, default=len(PAIRS))
    args = ap.parse_args()

    pairs = PAIRS[:args.limit_pairs]
    shutil_root = args.out_root
    if os.path.exists(shutil_root):
        import shutil
        shutil.rmtree(shutil_root)
    os.makedirs(shutil_root, exist_ok=True)

    report = {"spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §6 E3 / §9 P1'",
              "phase": "P1'-E3", "rq": "RQ6", "billed_calls": 0,
              "task": TASK, "pairs": [p[0] for p in pairs], "arms": list(ARMS),
              "episodes": [], "criteria": {}, "store_growth": {}}

    for arm in ARMS:
        for (pid, s_idx, s_seed, r_idx, r_seed, kind) in pairs:
            cell = os.path.join(shutil_root, f"{pid}_{arm}")
            os.makedirs(cell, exist_ok=True)
            store = os.path.join(cell, "store.jsonl")
            open(store, "w").close()
            for role, idx, seed in (("seed", s_idx, s_seed), ("read", r_idx, r_seed)):
                out = os.path.join(cell, f"{role}")
                print(f"[E3] {arm} {pid} {role}: layout {idx} seed {seed} ...", flush=True)
                run = run_episode(out=out, task_index=idx, seed=seed, arm=arm, store=store,
                                  horizon=args.horizon, decision_rounds=args.decision_rounds,
                                  gui=args.gui)
                seen = read_episode(out)
                report["episodes"].append(
                    {"pair": pid, "kind": kind, "arm": arm, "role": role,
                     "task_index": idx, "seed": seed, "run": run, "seen": seen})
                print(f"      rc={run['rc']} {run['wall_s']}s  "
                      f"memory={json.dumps(seen.get('memory_event_counts', {}))}  "
                      f"episodic={'yes' if seen.get('episodic_block') else 'NO'}", flush=True)
            store_path = store
            if os.path.exists(store_path):
                with open(store_path, encoding="utf-8") as f:
                    lines = [ln for ln in f if ln.strip()]
                report["store_growth"].setdefault(arm, []).append(
                    {"pair": pid, "store_rows": len(lines),
                     "store_bytes": os.path.getsize(store_path)})
            else:
                report["store_growth"].setdefault(arm, []).append(
                    {"pair": pid, "store_rows": None, "store_bytes": None})

    # ---------------------------------------------------------------- criteria
    def mem(arm, role):
        return [e for e in report["episodes"]
                if e["arm"] == arm and e["role"] == role and e["seen"].get("present")]

    c1 = {}
    for e in mem("full", "read"):
        counts = e["seen"]["memory_event_counts"]
        c1[e["pair"]] = {"memory_retrieval": counts.get("memory_retrieval", 0),
                         "memory_use": counts.get("memory_use", 0),
                         "memory_write": counts.get("memory_write", 0),
                         "rows_with_entries": sum(1 for r in e["seen"]["memory_rows"]
                                                  if r.get("rows") or r.get("recalled"))}
    report["criteria"]["1_full_arm_reads_retrieve"] = c1

    c2 = {}
    for e in report["episodes"]:
        if e["arm"] != "wo_episodic_memory":
            continue
        counts = e["seen"].get("memory_event_counts", {})
        c2[f"{e['pair']}/{e['role']}"] = {k: v for k, v in counts.items()}
    report["criteria"]["2_control_arm_memory_is_zero"] = c2

    c3 = {f"{e['arm']}/{e['pair']}/{e['role']}": bool(e["seen"].get("episodic_block"))
          for e in report["episodes"]}
    report["criteria"]["3_every_episode_files_the_episodic_block"] = c3

    # 4: the offline re-derivation, compared to what the online writer put in the store
    c4 = {}
    for arm in ARMS:
        for (pid, _si, _ss, _ri, _rs, _kind) in pairs:
            cell = os.path.join(shutil_root, f"{pid}_{arm}")
            store = os.path.join(cell, "store.jsonl")
            if not os.path.exists(store):
                c4[f"{arm}/{pid}"] = {"store": "absent"}
                continue
            rows = [json.loads(ln) for ln in open(store, encoding="utf-8") if ln.strip()]
            c4[f"{arm}/{pid}"] = {"rows": len(rows),
                                  "ids": [r.get("experience_id") or r.get("id") for r in rows]}
    report["criteria"]["4_offline_rederivation_ids"] = c4

    out_path = os.path.join(shutil_root, "e3_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    print("\n=== E3 acceptance ===")
    print("1. full arm read episodes, memory_retrieval counts:", json.dumps(c1))
    print("2. control arm memory_* (must be all zero):", json.dumps(c2))
    bad3 = [k for k, v in c3.items() if not v]
    print(f"3. episodes missing the episodic block: {bad3 or 'none'} "
          f"({sum(1 for v in c3.values() if v)}/{len(c3)} present)")
    print("4. store rows per pair:", json.dumps(c4))
    for arm in ARMS:
        print(f"   store growth [{arm}]:",
              [(g["pair"], g["store_rows"]) for g in report["store_growth"][arm]])
    print("report:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
