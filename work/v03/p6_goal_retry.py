"""Why is the ledger 75 requests above the instrument's count, when the tokens agree exactly?

Tokens agreeing to the last unit says the two ledgers are the right *set* of rows. So the
disagreement is not about which rows — it is about how many requests each row is worth. The
hypothesis: the instrument counted goal-parse *rows* while the ledger's
`http_requests_this_call` counts *requests*, and the difference is the retried ones.
"""
import json
import os
from collections import Counter

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"

rows = []
for dirpath, _dirnames, filenames in os.walk(ARCHIVE):
    if "goal_calls.jsonl" in filenames:
        for line in open(os.path.join(dirpath, "goal_calls.jsonl"), encoding="utf-8"):
            if line.strip():
                rows.append(json.loads(line))

print(f"goal-parse rows: {len(rows)}")
this_call = sum(int(r.get("http_requests_this_call") or 0) for r in rows)
print(f"sum of http_requests_this_call : {this_call}")
print(f"rows where it is 1             : {sum(1 for r in rows if r.get('http_requests_this_call') == 1)}")
print(f"rows where it is > 1           : {sum(1 for r in rows if (r.get('http_requests_this_call') or 0) > 1)}")
print()
print("distribution of http_requests_this_call:", dict(sorted(Counter(
    int(r.get("http_requests_this_call") or 0) for r in rows).items())))
print("distribution of transport_attempts  :", dict(sorted(Counter(
    int(r.get("transport_attempts") or 0) for r in rows).items())))
print("rows carrying an error              :", sum(1 for r in rows if r.get("error")))
print()

retried = [r for r in rows if (r.get("http_requests_this_call") or 0) > 1]
print(f"the {len(retried)} rows worth more than one request, and what they cost:")
for r in retried:
    print(f"    case={r.get('case_id'):>28}  repeat={r.get('repeat')}  "
          f"attempts={r.get('transport_attempts')}  this_call={r.get('http_requests_this_call')}  "
          f"err={str(r.get('error'))[:40]}")
extra = this_call - len(rows)
print()
print(f"requests above rows: {extra}")
print(f"per-episode 2936 + goal rows {len(rows)}        = {2936 + len(rows)}")
print(f"per-episode 2936 + goal requests {this_call}   = {2936 + this_call}")
print(f"instrument said                                 = 3217")
print()
if 2936 + len(rows) == 3217:
    print("=> the instrument counted goal-parse ROWS, not requests. It undercounts retries.")
else:
    print("=> the row hypothesis does not hold; the gap is elsewhere.")
