"""Look at the E1 archive's real ledger layout and row shape before fixing the reader."""
import json
import os

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"

print("=== where the ledgers sit ===")
for name in ("model_calls.jsonl", "goal_calls.jsonl"):
    hits = []
    for dirpath, _dirnames, filenames in os.walk(ARCHIVE):
        if name in filenames:
            hits.append(os.path.join(dirpath, name))
    hits.sort()
    print(f"{name}: {len(hits)} files")
    for h in hits[:3]:
        print(f"    {os.path.relpath(h, ARCHIVE)}")

print()
print("=== one per-episode row, verbatim ===")
h = None
for dirpath, _dirnames, filenames in os.walk(ARCHIVE):
    if "model_calls.jsonl" in filenames:
        h = os.path.join(dirpath, "model_calls.jsonl")
        break
if h:
    print(f"file: {os.path.relpath(h, ARCHIVE)}")
    lines = [ln for ln in open(h, encoding="utf-8") if ln.strip()]
    print(f"lines: {len(lines)}")
    if lines:
        d = json.loads(lines[0])
        print("keys:", sorted(d.keys()))
        for k in ("http_requests_this_call", "http_requests_total", "usage", "latency_s"):
            print(f"  {k} = {d.get(k)!r}")

print()
print("=== one goal-parse row, verbatim ===")
g = None
for dirpath, _dirnames, filenames in os.walk(ARCHIVE):
    if "goal_calls.jsonl" in filenames:
        g = os.path.join(dirpath, "goal_calls.jsonl")
        break
if g:
    print(f"file: {os.path.relpath(g, ARCHIVE)}")
    lines = [ln for ln in open(g, encoding="utf-8") if ln.strip()]
    print(f"lines: {len(lines)}")
    if lines:
        d = json.loads(lines[0])
        print("keys:", sorted(d.keys()))
        for k in ("http_requests_this_call", "http_requests_total", "usage", "latency_s"):
            print(f"  {k} = {d.get(k)!r}")
else:
    print("NO goal_calls.jsonl anywhere under", ARCHIVE)
