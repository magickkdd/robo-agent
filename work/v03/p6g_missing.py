"""What happened to `lh_c3` in the vlm and stub arms?

The picture so far contradicts the delivered residual in three ways:

1. the duplicated colour is in **`lh_c1_shared_pair_restore`**, not `lh_c3_capacity_and_shift`;
2. the `stub` channel hits it too (`looks: 26`, `look_http_requests: 0`) — so the cause is the
   **camera arm's grounding table**, which `privileged` bypasses (`looks: 0`, `views: null`), not
   "the camera" in some general sense;
3. the episodes that hit it **ran anyway** — `status: failed` at 6 decision rounds, with the
   refusal recorded in `errors.jsonl`. So it did not make the case unrunnable.

Which leaves `lh_c3` in those two arms: **no episode summary and no error either**. Four were
planned per arm, two summaries exist, and only two errors are recorded. A planned episode that
leaves neither a summary nor an error is the more serious of the two problems, so it is worth
naming exactly.
"""
import json
import os

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e2"

for arm in ("long_horizon_vlm", "long_horizon_stub", "long_horizon_privileged"):
    label = arm.replace("long_horizon_", "")
    print("=" * 78)
    print(f"  {label}")
    print("=" * 78)
    base = os.path.join(ARCHIVE, arm)
    for run in sorted(os.listdir(base)):
        rp = os.path.join(base, run)
        if not os.path.isdir(rp):
            continue
        print(f"  run: {run}")
        for name in ("manifest.json", "run_summary.json", "task_manifest.json", "episodes.csv"):
            p = os.path.join(rp, name)
            if not os.path.exists(p):
                continue
            if name == "episodes.csv":
                print(f"    episodes.csv:")
                for line in open(p, encoding="utf-8").read().splitlines():
                    print(f"      {line[:150]}")
                continue
            d = json.load(open(p, encoding="utf-8"))
            keys = ("planned", "rows", "episodes", "cases", "case_ids", "outcomes",
                    "perceive", "planner", "offline", "run_id", "errors", "error_counts",
                    "episode_errors", "status_counts", "task_ids")
            bits = {k: d[k] for k in keys if k in d}
            print(f"    {name}: {json.dumps(bits, ensure_ascii=False)[:420]}")
            if name == "manifest.json":
                for k in sorted(d):
                    if k not in bits:
                        v = json.dumps(d[k], ensure_ascii=False)
                        if len(v) < 200:
                            print(f"      {k} = {v}")
        print()

print("=" * 78)
print("the formal batch's own report, on the two that did not appear")
print("=" * 78)
rep = os.path.join(ARCHIVE, "e2_formal_report.json")
if os.path.exists(rep):
    d = json.load(open(rep, encoding="utf-8"))
    for k in sorted(d):
        v = json.dumps(d[k], ensure_ascii=False)
        if len(v) < 600:
            print(f"  {k} = {v}")
