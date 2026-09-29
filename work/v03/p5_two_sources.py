"""The one number this whole phase turns on, computed the same way for both decision sources.

`L6.recorded_vs_inferred` was the only contrast row whose value moved under a rule decision
source (v0.2 report §2: "全批 80 条对照行里唯一一条值变了的行"). The claim to check is what the
*same* instrument says after a model is put in the seat — so both products are read with one
predicate, applied to both:

    a row counts as moved when the arm's value differs from the `full` reference AND the
    instrument marked the pair comparable (i.e. neither side is n/m)

Nothing is averaged across decision sources, and the two tables are printed side by side rather
than merged, because SPEC §7.3 forbids the merged reading: a rule cell and a model cell are
two experiments, not one run twice.
"""
import json
import os

PRODUCTS = {
    "v0.2 (rule decision source)": "/tmp/p5c/lh_score/long_horizon_v0.json",
    "v0.3 (model decision source)": "/home/czx/embodied-agent-batches/v03/e1/lh_score/long_horizon_v0.json",
}

summary = {}
for label, path in PRODUCTS.items():
    if not os.path.exists(path):
        print(f"{label}: product absent at {path}")
        continue
    d = json.load(open(path, encoding="utf-8"))
    diffs = d["contrast"].get("differences") or []
    cells = d["contrast"]["cells"]
    arms = {k.split("::")[-1] for k in cells}
    metric_ids = {mid for v in cells.values() for mid in v["metrics"]}
    moved, not_comparable, same = [], [], []
    for x in diffs:
        if not x.get("comparable"):
            not_comparable.append(x)
        elif x.get("reference") is None or x.get("value") is None:
            not_comparable.append(x)
        elif x["reference"] != x["value"]:
            moved.append(x)
        else:
            same.append(x)
    # which metric ids were identical across every arm?
    per_metric = {}
    for mid in sorted(metric_ids):
        vals = {}
        for k, v in cells.items():
            m = v["metrics"].get(mid, {})
            vals[k.split("::")[-1]] = (m.get("value"), m.get("not_measured_reason"))
        measured = {a: t[0] for a, t in vals.items() if t[0] is not None}
        per_metric[mid] = {
            "arms_with_a_reading": len(measured),
            "distinct_values": len(set(measured.values())),
            "identical_across_arms": len(measured) > 1 and len(set(measured.values())) == 1,
        }
    summary[label] = {
        "product": path, "runs": d["n_runs"], "episodes": d["n_episodes"],
        "contrast_rows": len(diffs),
        "moved": len(moved), "identical": len(same), "not_comparable": len(not_comparable),
        "metrics_identical_across_all_arms": sum(
            1 for m in per_metric.values() if m["identical_across_arms"]),
        "metrics_total": len(metric_ids),
        "per_metric": per_metric,
    }
    print(f"\n=== {label} ===")
    print(f"  runs={d['n_runs']} episodes={d['n_episodes']} contrast_rows={len(diffs)}")
    print(f"  moved={len(moved)}  identical={len(same)}  not_comparable={len(not_comparable)}")
    print(f"  metrics identical across every arm: "
          f"{summary[label]['metrics_identical_across_all_arms']}/{len(metric_ids)}")
    for x in moved[:8]:
        print(f"    moved: {x['arm']:22s} {x['metric']:32s} {x['reference']} -> {x['value']}")
    if len(moved) > 8:
        print(f"    ... {len(moved) - 8} more")

print()
print("=== the two decision sources, side by side (never merged) ===")
hdr = f"{'':46s}{'rule':>14s}{'model':>14s}"
print(hdr)
rows = [
    ("contrast rows", "contrast_rows"),
    ("rows whose value moved", "moved"),
    ("rows identical", "identical"),
    ("rows not comparable (n/m on a side)", "not_comparable"),
    ("metrics identical across arms", "metrics_identical_across_all_arms"),
    ("metrics total", "metrics_total"),
]
for name, key in rows:
    a = summary.get("v0.2 (rule decision source)", {}).get(key)
    b = summary.get("v0.3 (model decision source)", {}).get(key)
    print(f"  {name:44s}{str(a):>14s}{str(b):>14s}")

out = "/home/czx/embodied-agent-batches/v03/e1/lh_score/two_sources_summary.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
print("\nartefact:", out)
