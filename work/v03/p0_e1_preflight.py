"""Two pre-flight facts for the E1 pre-registration, both zero spend.

1. Is SPEC-v0.3 §6 E1's literal arm list runnable?  It names `wo_vlm` as the
   sixth privileged arm, where P5-c ran `wo_skill_acquisition`.  Ask the code
   rather than the report: `run_group` resolves the arm before a run directory
   exists, so a refusal costs nothing and prints the reason.

2. What is the call estimate made of?  The v0.2 report §8 A1 quotes
   6 x (148 + 100) rounds + 168 goal parses = ~1,656.  Those round counts are
   re-measured here from P5-c's own `events.jsonl`, so the E1 budget rests on a
   reading this round can produce rather than on a number quoted twice.

The round count is the number of `decision` records in an episode: one per
round, with the round's `context_id` inside `payload` (the previous attempt at
this key read the envelope and collapsed every episode to 1).
"""
import collections
import json
import os
import shutil
import sys

sys.path.insert(0, os.getcwd())

SCRATCH = "/tmp/v03_preflight_refusals"

print("=== 1. does the code accept the SPEC's literal arm list on this channel? ===")
print("    (through the real CLI, not a hand-built call, and one zero-spend episode each)")
import subprocess  # noqa: E402

SCRATCH = "/tmp/v03_preflight_cli"
shutil.rmtree(SCRATCH, ignore_errors=True)
os.makedirs(SCRATCH, exist_ok=True)
PY = "/home/czx/miniforge3/envs/embodied/bin/python"
for arm in ("wo_vlm", "full"):
    store = os.path.join(SCRATCH, f"{arm}_store.jsonl")
    lib = os.path.join(SCRATCH, f"{arm}_lib.jsonl")
    open(store, "w").close()
    open(lib, "w").close()
    cmd = [PY, "-m", "embodied_agent.cli", "evaluate", "--set", "long_horizon",
           "--planner", "rule", "--modes", "B", "--repeats", "1",
           "--out-root", os.path.join(SCRATCH, arm),
           "--perceive", "privileged", "--ablation", arm,
           "--experience-store", store, "--skill-memory", lib,
           "--cases", "lh_c1_shared_pair_restore", "--no-frames"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd(),
                       env={**os.environ, "PYTHONPATH": "."})
    tail = (r.stderr.strip().splitlines() or [""])[-1]
    print(f"  {arm}: rc={r.returncode} | {tail[:220]}")

print()
print("=== 2. P5-c rounds per cell, re-measured from events.jsonl ===")
total_rounds = collections.Counter()
total_eps = collections.Counter()
per_cell = {}
root = "/tmp/p5c"
for sub in ("long_horizon", "em"):
    for arm in sorted(os.listdir(os.path.join(root, sub))):
        cellroot = os.path.join(root, sub, arm)
        if not os.path.isdir(cellroot):
            continue
        rundir = None
        for name in os.listdir(cellroot):
            p = os.path.join(cellroot, name)
            if os.path.isdir(os.path.join(p, "episodes")):
                rundir = p
                break
        if rundir is None:
            continue
        eps = os.path.join(rundir, "episodes")
        n_eps = rounds = 0
        for ep in sorted(os.listdir(eps)):
            f = os.path.join(eps, ep, "events.jsonl")
            if not os.path.exists(f):
                continue
            n_eps += 1
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "decision":
                        rounds += 1
        per_cell[f"{sub}/{arm}"] = {"episodes": n_eps, "decision_records": rounds,
                                    "run_dir": rundir}
        total_eps[sub] += n_eps
        total_rounds[sub] += rounds
        print(f"  {sub}/{arm}: {n_eps} episodes, {rounds} decision records")

print()
print("totals per set:", dict(total_eps), dict(total_rounds))
r = sum(total_rounds.values())
e = sum(total_eps.values())
est = r + e
print(f"E1 estimate basis: sum(decision records)={r} + sum(episodes)={e} = {est}")
print("v0.2 report §8 A1 quoted: 6 x (148 + 100) + 168 = 1656")
print(f"upper bound at x1.5      : {int(est * 1.5)}")
out = {"per_cell": per_cell, "totals": {"episodes": dict(total_eps),
                                        "decision_records": dict(total_rounds)},
       "estimate_calls": est, "upper_bound_x1_5": int(est * 1.5)}
os.makedirs("/home/czx/embodied-agent-batches/v03/p0_preflight", exist_ok=True)
with open("/home/czx/embodied-agent-batches/v03/p0_preflight/e1_budget_basis.json",
          "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2, sort_keys=True)
    fh.write("\n")
print("artifact: /home/czx/embodied-agent-batches/v03/p0_preflight/e1_budget_basis.json")
