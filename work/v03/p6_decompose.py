"""Decompose the E1 batch's requests properly, and find out what 3,217 actually was.

The v0.3 report said the per-episode reading hid 281 requests and attributed all of it to the
goal parses. The goal parses are worth 356, of which 75 returned nothing. So either the
report's 281 was a subset of the goal cost, or it was something else entirely. This resolves
which, by decomposing both ledgers into rows and requests, returned and not.
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
from embodied_agent.evaluation.run import batch_spend

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"

batch_roots = sorted(dp for dp, dn, _fn in os.walk(ARCHIVE) if "episodes" in dn)

agg = Counter()
for r in batch_roots:
    s = batch_spend(r)
    pe = s["per_episode_ledgers"]
    g = s["goal_parse_ledger"]
    agg["pe_rows"] += pe["rows"]
    agg["pe_req"] += pe["http_requests"]
    agg["pe_req_ok"] += pe["http_requests_successful"]
    agg["pe_err_rows"] += pe["errored_rows"]
    agg["pe_err_req"] += pe["errored_http_requests"]
    agg["pe_tokens"] += pe["prompt_tokens"]
    agg["g_rows"] += g["rows"]
    agg["g_req"] += g["http_requests"]
    agg["g_req_ok"] += g["http_requests_successful"]
    agg["g_err_rows"] += g["errored_rows"]
    agg["g_err_req"] += g["errored_http_requests"]

print("=== the two ledgers, decomposed ===")
print(f"{'':>28} {'per-episode':>13} {'goal parses':>13} {'total':>9}")
for label, pk, gk in (
    ("rows", "pe_rows", "g_rows"),
    ("requests made", "pe_req", "g_req"),
    ("requests that returned", "pe_req_ok", "g_req_ok"),
    ("rows that ended in error", "pe_err_rows", "g_err_rows"),
    ("requests that returned nothing", "pe_err_req", "g_err_req"),
):
    print(f"{label:>28} {agg[pk]:>13,} {agg[gk]:>13,} {agg[pk] + agg[gk]:>9,}")
print(f"{'prompt tokens':>28} {agg['pe_tokens']:>13,} {'':>13} {agg['pe_tokens']:>9,}")

print()
print("=== so what was 3,217? ===")
cands = {
    "per-episode requests + successful goal requests": agg["pe_req"] + agg["g_req_ok"],
    "per-episode requests + ALL goal requests": agg["pe_req"] + agg["g_req"],
    "requests that returned, both ledgers": agg["pe_req_ok"] + agg["g_req_ok"],
    "every request made": agg["pe_req"] + agg["g_req"],
    "per-episode requests + goal rows": agg["pe_req"] + agg["g_rows"],
    "per-episode requests + goal rows + per-episode errors": agg["pe_req"] + agg["g_rows"] + agg["pe_err_req"],
}
for name, v in cands.items():
    mark = "  <-- the instrument" if v == 3217 else ""
    print(f"  {v:>7,}  {name}{mark}")

print()
print("=== the error rows, by what went wrong ===")
msgs = Counter()
for r in batch_roots:
    for name in ("model_calls.jsonl",):
        import glob
        for p in glob.glob(os.path.join(r, "episodes", "*", name)):
            for line in open(p, encoding="utf-8"):
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("error"):
                    msgs[str(d["error"])[:70]] += 1
    g = os.path.join(r, "goal_resolutions", "goal_calls.jsonl")
    if os.path.exists(g):
        for line in open(g, encoding="utf-8"):
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("error"):
                msgs[str(d["error"])[:70]] += 1
for m, n in msgs.most_common():
    print(f"  {n:>4}  {m}")
print(f"  total errored rows: {sum(msgs.values())}")
