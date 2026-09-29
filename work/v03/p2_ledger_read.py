"""What did that one cell actually spend, and why is it 5x the pre-registered bound?

Two readings, both from the ledger a Cost group would read:
  1. the row shapes — `kind` and the attempt/token field names, since my first reader looked
     for `prompt_tokens`/`completion_tokens` and found 0, which means the rows spell them
     another way (the perception rows use `*_this_call`);
  2. the per-episode distribution, because "821 requests for an episode with ~12 decision
     rounds" is not a retry story — a retry multiplies a request, and this needs an
     explanation with a number attached.

Nothing here re-runs anything. It reads /home/czx/embodied-agent-batches/v03/e1/em_full.
"""
import collections
import glob
import json
import os

CELL = "/home/czx/embodied-agent-batches/v03/e1/em_full"
ledgers = sorted(glob.glob(os.path.join(CELL, "**", "model_calls.jsonl"), recursive=True))
print("ledgers:", len(ledgers))

kinds = collections.Counter()
fields = collections.Counter()
per_ep = []
tot = collections.Counter()

for path in ledgers:
    ep = os.path.basename(os.path.dirname(path))
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    ep_kinds = collections.Counter()
    for r in rows:
        kinds[r.get("kind")] += 1
        ep_kinds[r.get("kind")] += 1
        fields.update(r.keys())
        for k in ("prompt_tokens", "completion_tokens", "http_requests",
                  "prompt_tokens_this_call", "completion_tokens_this_call",
                  "http_requests_this_call", "transport_attempts",
                  "http_requests_total", "usage"):
            v = r.get(k)
            if isinstance(v, (int, float)):
                tot[k] += v
    per_ep.append((ep, len(rows), dict(ep_kinds)))

print("\n=== row kinds across the cell ===")
for k, n in kinds.most_common():
    print(f"  {k}: {n}")
print("\n=== per-episode rows and kinds ===")
for ep, n, ek in per_ep[:6]:
    print(f"  {ep}: {n} rows  {json.dumps(ek)}")
print(f"  ... {len(per_ep)} episodes total")
print("\n=== numeric fields summed over the whole cell ===")
for k, v in sorted(tot.items()):
    print(f"  {k}: {v}")
print("\n=== every key that appears on any row ===")
print(sorted(fields))

# one row in full, so the field names are read, not inferred
if ledgers:
    rows = [json.loads(l) for l in open(ledgers[0], encoding="utf-8") if l.strip()]
    if rows:
        print("\n=== first row of", os.path.basename(os.path.dirname(ledgers[0])), "===")
        print(json.dumps(rows[0], ensure_ascii=False, indent=1)[:2500])
