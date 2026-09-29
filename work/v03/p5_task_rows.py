"""§11's Task group, four rows, from the twelve per-cell reports.

`cli report` is the same instrument and the same frozen-config reference v0.2 used, so these
rows are a re-measurement. The Task group's four rows are: complete success, partial progress
(the third-layer geometric score, not the loop's own counter), constraint violations
(`false_finish_attempts`), and invalid completion (the failure attribution).

v0.2's Task-group reading is quoted beside it and the two are never averaged: SPEC §7.3 — a rule
cell and a model cell are two experiments, not one run twice.
"""
import json
import os

OUT = "/home/cxz"
OUT = "/home/czx/embodied-agent-batches/v03/e1/reports"
CELLS = [("long_horizon", a) for a in ("full", "wo_planning", "wo_working_memory",
                                       "wo_replanning", "wo_episodic_memory",
                                       "wo_skill_acquisition")] + \
        [("em", a) for a in ("full", "wo_planning", "wo_working_memory",
                             "wo_replanning", "wo_episodic_memory", "wo_skill_acquisition")]

rows = {}
for set_name, arm in CELLS:
    p = os.path.join(OUT, f"{set_name}_{arm}.json")
    if not os.path.exists(p):
        continue
    r = json.load(open(p, encoding="utf-8"))
    # find the four rows wherever this instrument keeps them
    beh = r.get("behaviour") or r.get("behavioural") or {}
    score = r.get("score") or {}
    fa = r.get("failure_attribution") or {}
    rows[f"{set_name}_{arm}"] = {
        "complete_success": (r.get("complete_success")
                             or (score.get("complete_success"))
                             or ((r.get("summary") or {}).get("complete_success"))),
        "partial_progress": score.get("objects_completed"),
        "false_finish_attempts": (beh.get("B") or {}).get("false_finish_attempts")
        if isinstance(beh.get("B"), dict) else beh.get("false_finish_attempts"),
        "not_success_by_cause": ((fa.get("B") or {}) if isinstance(fa.get("B"), dict)
                                 else fa).get("not_success_by_reported_cause"),
        "outcomes": (r.get("outcomes") or {}),
        "n_episodes": r.get("n_episodes") or r.get("episodes"),
    }

# the instrument's own key layout, once, so the collector is not guessing silently
sample = json.load(open(os.path.join(OUT, "long_horizon_full.json"), encoding="utf-8"))
print("report top keys:", sorted(sample))
print()
for k in sorted(sample):
    v = sample[k]
    if isinstance(v, dict):
        print(f"  {k}: dict keys={sorted(v)[:10]}")
    elif isinstance(v, list):
        print(f"  {k}: list len={len(v)}")
    else:
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:100]}")
print()
print("=== the four Task rows, per cell ===")
hdr = f"{'cell':32s} {'n':>4s} {'success':>9s} {'objects':>10s} {'false_finish':>13s}  not_success_by_cause"
print(hdr)
for key, v in rows.items():
    nf = v["not_success_by_cause"]
    print(f"{key:32s} {str(v['n_episodes']):>4s} "
          f"{str(v['complete_success']):>9s} "
          f"{json.dumps(v['partial_progress'], ensure_ascii=False)[:10]:>10s} "
          f"{str(v['false_finish_attempts']):>13s}  {json.dumps(nf, ensure_ascii=False)[:70]}")
out = os.path.join(OUT, "..", "task_group.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
print("\nartefact:", out)
