"""Verify the batch-wide numbers the v0.3 report quotes, and flag any that were read at a
narrower scope than the sentence claims.

This exists because the report's own §0 rule is that a reading carries the scope it was taken
at, and two of the figures in the draft were read off `long_horizon_full` alone while the
sentence said "the batch". Those are the ones this recomputes from all twelve cells.
"""
import json
import os

REP = "/home/cxz/embodied-agent-batches/v03/e1/reports"
REP = "/home/czx/embodied-agent-batches/v03/e1/reports"
CELLS = [("long_horizon", a) for a in ("full", "wo_planning", "wo_working_memory",
                                       "wo_replanning", "wo_episodic_memory",
                                       "wo_skill_acquisition")] + \
        [("em", a) for a in ("full", "wo_planning", "wo_working_memory",
                             "wo_replanning", "wo_episodic_memory", "wo_skill_acquisition")]

lat = []
cost = {"http_requests_total": 0, "prompt_tokens_total": 0, "completion_tokens_total": 0,
        "sim_time_s_total": 0.0, "wall_time_s_total": 0.0, "api_errors": 0,
        "transport_retries": 0, "format_repairs": 0}
planned = success = 0
ff = 0
per_cell_latency = {}
for set_name, arm in CELLS:
    p = os.path.join(REP, f"{set_name}_{arm}.json")
    if not os.path.exists(p):
        continue
    r = json.load(open(p, encoding="utf-8"))
    ml = (r.get("model_latency") or {}).get("B") or {}
    per_cell_latency[f"{set_name}_{arm}"] = {k: ml.get(k) for k in
                                             ("calls", "mean_s", "p50_s", "p95_s", "max_s")}
    for k in cost:
        b = (r.get("cost") or {}).get("B") or {}
        if k in b:
            cost[k] += b[k]
    planned += int(r.get("planned_runs") or 0)
    succ = sum((v.get("independent_successes") or 0) for v in
               (r.get("by_object_count") or {}).values())
    success += succ
    fb = (r.get("behaviour") or {}).get("B") or {}
    ff += int(fb.get("false_finish_attempts") or 0)
    for kind, d in (ml.get("by_kind") or {}).items():
        lat.append((f"{set_name}_{arm}/{kind}", d))

print("=== E1, all twelve cells ===")
print(f"planned_runs (the denominator) : {planned}")
print(f"independent successes summed  : {success}")
print(f"false_finish_attempts summed   : {ff}")
print("cost.B summed over the cells  :")
for k, v in cost.items():
    print(f"  {k:26s} {v}")
print()
print("=== latency, per cell and per kind (not pooled across kinds) ===")
for k, d in per_cell_latency.items():
    if d.get("calls"):
        print(f"  {k:34s} calls={d['calls']:4d} mean={d['mean_s']:8.3f} "
              f"p50={d['p50_s']:8.3f} p95={d['p95_s']:8.3f} max={d['max_s']:8.3f}")
print()
mx = max((d.get("max_s") or 0) for _k, d in lat)
mn = min((d.get("p50_s") or 0) for _k, d in lat if d.get("p50_s") is not None)
print(f"  slowest single call in the batch : {mx} s")
print(f"  smallest per-cell p50            : {mn} s")
print("  NOTE: the report must quote a per-cell p50/p95 or a pooled figure, and say which;")
print("        a mean over cells is not the same object as a mean over calls.")

# E2's own latency, from its ledger
e2 = "/home/cxz/embodied-agent-batches/v03/e2"
e2 = "/home/czx/embodied-agent-batches/v03/e2"
vals = []
for f in glob.glob(f"{e2}/**/model_calls.jsonl", recursive=True) if (glob := __import__("glob")) else []:
    for line in open(f, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("latency_s") is not None:
                vals.append(float(r["latency_s"]))
if vals:
    vals.sort()
    print()
    print("=== E2 formal batch, per-call latency ===")
    print(f"  calls={len(vals)} min={vals[0]} p50={vals[len(vals)//2]} "
          f"max={vals[-1]}")
print()
print("=== identity, from the same reports ===")
p = os.path.join(REP, "long_horizon_full.json")
r = json.load(open(p, encoding="utf-8"))
print("  planner:", r.get("planner"), "| offline:", r.get("offline"))
print("  model:", json.dumps(r.get("model"), ensure_ascii=False)[:200])
print("  identities:", json.dumps((r.get("model_identities_observed") or {}).get("identities"),
                                  ensure_ascii=False),
      "| one_identity_throughout:",
      (r.get("model_identities_observed") or {}).get("one_identity_throughout"),
      "| calls_without_an_answered_identifier:",
      (r.get("model_identities_observed") or {}).get("calls_without_an_answered_identifier"))
print("  event_coverage:", json.dumps(r.get("event_coverage"), ensure_ascii=False)[:220])
