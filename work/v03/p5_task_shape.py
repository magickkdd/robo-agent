"""§11's Task group, four rows, with the instrument's own keys read rather than guessed.

Two passes: the first prints the shape of one report so the collector is not inventing a path,
the second collects. The first report's `failure_attribution.B` already carries a fact worth
stating on its own — `MODEL_ERROR` appears in four long-horizon cells and in `wo_working_memory`
it is 6 of 8 episodes, which is a failure shape a rule decision source cannot produce at all.
"""
import json
import os

OUT = "/home/czx/embodied-agent-batches/v03/e1/reports"
sample = json.load(open(os.path.join(OUT, "long_horizon_full.json"), encoding="utf-8"))
for key in ("primary_metric", "reliability_denominator_note"):
    print(f"{key}: {json.dumps(sample.get(key), ensure_ascii=False)[:400]}")
for key in ("behaviour", "failure_attribution", "cost", "model_latency"):
    b = (sample.get(key) or {}).get("B") or {}
    print(f"\n{key}.B: {json.dumps(b, ensure_ascii=False)[:700]}")
print("\nby_object_count:", json.dumps(sample.get("by_object_count"), ensure_ascii=False)[:300])
print("\nsuccess_rate_by_case:", json.dumps(sample.get("success_rate_by_case"),
                                           ensure_ascii=False)[:400])
print("\npaired_comparisons:", json.dumps(sample.get("paired_comparisons"),
                                          ensure_ascii=False)[:300])
print("\nmodel_identities_observed:", json.dumps(
    sample.get("model_identities_observed"), ensure_ascii=False)[:500])
print("\nevent_coverage:", json.dumps(sample.get("event_coverage"), ensure_ascii=False)[:300])
