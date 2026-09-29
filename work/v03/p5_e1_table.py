"""E1's twelve cells, read from the ledger and the run summaries, with every denominator shown.

The reading that matters is per cell: armed episodes, episodes that asked the model, requests,
tokens and the outcome split. The invariant §13.3 asks for is "armed => asked the model > 0 per
cell", and the cost group's first three columns come from the same rows rather than from a
separate tally, so a cell cannot be right on one and wrong on the other.
"""
import json
import os
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
os.chdir("/home/czx/embodied-agent-robot-agent-embodied-agent-4")

from work.v03.e1_batch import ARMS, read_cell  # noqa: E402

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"
state = json.load(open(os.path.join(ARCHIVE, "e1_state.json"), encoding="utf-8"))

rows = []
for set_name, arm in [(s["set"], s["arm"]) for s in state["cells"]]:
    cell_root = os.path.join(ARCHIVE, f"{set_name}_{arm}")
    cd = None
    if os.path.isdir(cell_root):
        for p in sorted(os.listdir(cell_root)):
            if os.path.isdir(os.path.join(cell_root, p, "episodes")):
                cd = p
                break
    cell = os.path.join(cell_root, cd) if cd else None
    s = read_cell(cell) if cell else {}
    s.pop("per_episode", None)
    outcomes = s.get("outcomes") or {}
    rows.append((set_name, arm, s, outcomes))

hdr = (f"{'cell':30s} {'eps':>4s} {'armed':>6s} {'asked':>6s} {'rows':>5s} {'http':>6s} "
       f"{'prompt':>10s} {'compl':>8s} {'ok':>4s} {'gre':>4s} {'nfail':>5s} {'nclar':>5s}")
print(hdr)
print("-" * len(hdr))
tp = tc = th = 0
for set_name, arm, s, o in rows:
    tp += s.get("prompt_tokens", 0)
    tc += s.get("completion_tokens", 0)
    th += s.get("http_requests", 0)
    print(f"{set_name + '_' + arm:30s} {s.get('episodes', 0):4d} {s.get('armed', 0):6d} "
          f"{s.get('asked_the_model', 0):6d} {s.get('model_calls_rows', 0):5d} "
          f"{s.get('http_requests', 0):6d} {s.get('prompt_tokens', 0):10d} "
          f"{s.get('completion_tokens', 0):8d} {o.get('success', 0):4d} "
          f"{o.get('goal_resolution_error', 0):4d} {o.get('failed', 0):5d} "
          f"{o.get('needs_clarification', 0):5d}")
print("-" * len(hdr))
print(f"{'TOTAL':30s} {sum(r[2].get('episodes', 0) for r in rows):4d} "
      f"{sum(r[2].get('armed', 0) for r in rows):6d} "
      f"{sum(r[2].get('asked_the_model', 0) for r in rows):6d} "
      f"{sum(r[2].get('model_calls_rows', 0) for r in rows):5d} {th:6d} {tp:10d} {tc:8d}")
print()
print(f"upper bound {state['upper_bound']} | spent {state['spent_calls']} | "
      f"stopped: {state.get('stopped')}")
print()
print("=== §13.3 invariant 1: armed => asked the model, per cell ===")
bad = [f"{a}_{b}" for _s, a, b, _o in rows
       if b.get("asked_the_model", 0) == 0 or b.get("asked_the_model", 0) != b.get("armed", 0)]
print("  cells where asked == armed and > 0:",
      sum(1 for _s, _a, b, _o in rows
          if b.get("asked_the_model", 0) == b.get("armed", 0) and b.get("armed", 0) > 0),
      f"/ {len(rows)}")
print("  violations:", bad or "none")
print()
print("=== run errors, bucketed (429/quota are environment errors, never arm effects) ===")
for set_name, arm, s, _o in rows:
    b = s.get("run_error_buckets") or {}
    print(f"  {set_name}_{arm}: quota_or_429={b.get('quota_or_429')} other={b.get('other')} "
          f"run_errors={s.get('run_errors')}")
print()
print("=== latency, from the same ledger rows ===")
lats = [s.get("latency_s_min") for s in (_r[2] for _r in rows) if s.get("latency_s_min")]
latx = [s.get("latency_s_max") for s in (_r[2] for _r in rows) if s.get("latency_s_max")]
print(f"  per-cell min {min(lats)} s .. max {max(latx)} s")
print(f"  pricing_configured: False  ->  api_cost_estimate / cost_estimate_usd stay null (SPEC 7.1)")

out = {"cells": [{"set": s_, "arm": a, "summary": b, "outcomes": o} for s_, a, b, o in rows],
       "totals": {"episodes": sum(r[2].get("episodes", 0) for r in rows),
                  "armed": sum(r[2].get("armed", 0) for r in rows),
                  "asked_the_model": sum(r[2].get("asked_the_model", 0) for r in rows),
                  "model_calls_rows": sum(r[2].get("model_calls_rows", 0) for r in rows),
                  "http_requests": th, "prompt_tokens": tp, "completion_tokens": tc},
       "upper_bound": state["upper_bound"], "spent": state["spent_calls"]}
path = os.path.join(ARCHIVE, "e1_table.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
print("\nartefact:", path)
