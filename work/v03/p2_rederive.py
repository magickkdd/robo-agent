"""Re-derive the first cell with the corrected reader, and re-derive the budget from it.

The stop rule fired on `em_full` because the reader summed `http_requests_total`, a cumulative
counter, across rows. With `http_requests_this_call` the cell spent 225. This script re-reads
that cell, and projects the bound from the two shapes (one `em` cell measured, `lh` projected
from P5-c's round counts and the measured requests-per-round) so the next run of the batch is
governed by a number that came from a ledger.
"""
import json
import os
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
os.chdir("/home/czx/embodied-agent-robot-agent-embodied-agent-4")

from work.v03.e1_batch import read_cell  # noqa: E402

CELL = "/home/czx/embodied-agent-batches/v03/e1/em_full"
run_dir = None
for p in sorted(os.listdir(CELL)):
    if os.path.isdir(os.path.join(CELL, p, "episodes")):
        run_dir = os.path.join(CELL, p)
print("run dir:", run_dir)
s = read_cell(run_dir)
per = s.pop("per_episode")
print(json.dumps(s, ensure_ascii=False, indent=1))
print("\nper-episode:")
for e in per:
    print("  ", json.dumps(e, ensure_ascii=False))

rows = s["model_calls_rows"]
http = s["http_requests"]
eps = s["episodes"]
print()
print("=== the budget, re-derived from this ledger ===")
print(f"measured em cell: {eps} episodes, {rows} decision rows, {http} requests, "
      f"requests/row {http / max(1, rows):.3f}, requests/episode {http / max(1, eps):.1f}")
print(f"prompt tokens {s['prompt_tokens']} / completion {s['completion_tokens']} "
      f"(prompt per row {s['prompt_tokens'] / max(1, rows):.0f})")

# P5-c round counts, re-measured in P0'
p5c = json.load(open("/home/czx/embodied-agent-batches/v03/p0_preflight/e1_budget_basis.json",
                     encoding="utf-8"))
lh_rounds = p5c["totals"]["decision_records"]["long_horizon"] / 6   # per cell
em_rounds = p5c["totals"]["decision_records"]["em"] / 6
rpr = http / max(1, rows)          # requests per decision row, measured on the em cell
project = {}
for set_name, rounds_per_cell, eps_per_cell in (("em", em_rounds, 16),
                                                ("long_horizon", lh_rounds, 12)):
    per_cell = rounds_per_cell * rpr
    project[set_name] = {"rounds_per_cell": round(rounds_per_cell, 1),
                         "episodes": eps_per_cell,
                         "requests_per_cell": round(per_cell),
                         "six_cells": round(per_cell * 6)}
total = sum(v["six_cells"] for v in project.values())
print()
for k, v in project.items():
    print(f"  {k}: {v}")
print(f"  projected total for 12 cells: {total}")
print(f"  pre-registered upper bound   : 2484")
print(f"  ratio                        : {total / 2484:.2f}x")
out = {"measured_em_cell": s, "per_episode": per, "requests_per_decision_row": rpr,
       "projected": project, "projected_total": total, "upper_bound": 2484,
       "ratio_to_upper_bound": round(total / 2484, 2),
       "superseded_reading": {
           "http_requests": 12320,
           "why_wrong": "summed `http_requests_total`, a cumulative counter carried on every "
                        "row, so each request was counted once per row after it",
           "prompt_tokens": 0,
           "why_tokens_zero": "read `prompt_tokens` at the top level; the rows nest tokens "
                              "under `usage`"}}
path = "/home/czx/embodied-agent-batches/v03/e1/em_full_rederived.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
print("artefact:", path)
