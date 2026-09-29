"""Re-derive every recorded cell's summary from disk, then clear the stop.

The batch stopped a second time on a number this round's own reader produced. The first stop
was `read_cell` summing `http_requests_total` (cumulative, per row); that was fixed. The second
stop is the same wrong number surviving in `e1_state.json`, because `spent_calls` sums the
*stored* summaries and the stored `em_full` summary was written by the buggy reader before the
fix. So the batch did not overspend — it read its own history wrong.

The state file is a cache of what the artefacts say, and the artefacts are the authority. This
recomputes every recorded cell with the corrected reader and rewrites the state. It re-runs
nothing and spends nothing: it reads `model_calls.jsonl`, which is already on disk.

It also prints, per cell, the real numbers, because two cells' worth of readings is the first
place this project has a token ledger and it should be visible rather than summarised.
"""
import json
import os
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
os.chdir("/home/czx/embodied-agent-robot-agent-embodied-agent-4")

from work.v03.e1_batch import find_cell_dir, read_cell  # noqa: E402

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"
state_path = os.path.join(ARCHIVE, "e1_state.json")
state = json.load(open(state_path, encoding="utf-8"))

before = state.get("spent_calls")
total = 0
print("cell                 eps armed asked  rows    http  prompt     completion  latency")
for cell in state["cells"]:
    key = f"{cell['set']}_{cell['arm']}"
    cd = cell.get("cell_dir") or find_cell_dir(os.path.join(ARCHIVE, key))
    s = read_cell(cd) if cd else {}
    per = s.pop("per_episode", None)
    cell["cell_dir"] = cd
    cell["summary"] = s
    total += s.get("http_requests", 0)
    old = (cell.get("_summary_before_rederive") or {}).get("http_requests")
    cell["_summary_before_rederive"] = {"http_requests": old} if old is not None else None
    print(f"{key:20s} {s.get('episodes', 0):3d} {s.get('armed', 0):5d} "
          f"{s.get('asked_the_model', 0):5d} {s.get('model_calls_rows', 0):5d} "
          f"{s.get('http_requests', 0):6d} {s.get('prompt_tokens', 0):9d} "
          f"{s.get('completion_tokens', 0):11d}  "
          f"{s.get('latency_s_min')}-{s.get('latency_s_max')}"
          + (f"   [was {old}]" if old is not None else ""))

state["spent_calls"] = total
state["stopped"] = None
state["stopped_because"] = None
state["consecutive_quota_deaths"] = 0
state["re_derived_from_disk"] = {
    "when": "after the second false stop",
    "why": "spent_calls summed the stored summaries, and the stored em_full summary still "
           "carried the pre-fix reading (http_requests_total summed across rows). The "
           "artefacts are the authority; the state is a cache of them.",
    "spent_calls_before": before,
    "spent_calls_after": total,
}
with open(state_path, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
print()
print(f"spent_calls: {before} (as the state had it) -> {total} (from the ledger)")
print(f"upper bound : {state['upper_bound']}   headroom: {state['upper_bound'] - total}")
print("cells paid for:", [f"{c['set']}_{c['arm']}" for c in state["cells"]])
print("state:", state_path)
