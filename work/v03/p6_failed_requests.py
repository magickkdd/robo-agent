"""Confirm: the instrument counted goal requests but not the failed ones.

356 goal requests. 281 + 75 = 356, and the 15 errored rows each cost 5 attempts, so 15 x 5 = 75
is the whole residual. The question that decides whether this is a real undercount or a
correct reading: did a failed request consume anything billable or quota-limited? Two things
settle it — whether the failed rows carry token usage (if they do, the instrument counted
their tokens while dropping their requests, which is internally inconsistent), and whether the
attempts are real HTTP round-trips.
"""
import json
import os

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"

rows = []
for dirpath, _dirnames, filenames in os.walk(ARCHIVE):
    if "goal_calls.jsonl" in filenames:
        for line in open(os.path.join(dirpath, "goal_calls.jsonl"), encoding="utf-8"):
            if line.strip():
                rows.append(json.loads(line))

ok = [r for r in rows if not r.get("error")]
bad = [r for r in rows if r.get("error")]

req_ok = sum(int(r.get("http_requests_this_call") or 0) for r in ok)
req_bad = sum(int(r.get("http_requests_this_call") or 0) for r in bad)
print(f"goal rows: {len(rows)}   successful: {len(ok)}   errored: {len(bad)}")
print(f"requests in successful rows: {req_ok}")
print(f"requests in errored rows   : {req_bad}")
print()
print(f"per-episode 2936 + successful goal requests {req_ok} = {2936 + req_ok}   "
      f"(instrument said 3217)")
print(f"per-episode 2936 + ALL goal requests        {req_ok + req_bad} = "
      f"{2936 + req_ok + req_bad}   (the real spend)")
print()

print("=== do the failed rows carry token usage? ===")
tok_bad_p = sum(int((r.get("usage") or {}).get("prompt_tokens") or 0) for r in bad)
tok_bad_c = sum(int((r.get("usage") or {}).get("completion_tokens") or 0) for r in bad)
tok_ok_p = sum(int((r.get("usage") or {}).get("prompt_tokens") or 0) for r in ok)
print(f"prompt tokens on errored rows   : {tok_bad_p:,}")
print(f"completion tokens on errored rows: {tok_bad_c:,}")
print(f"prompt tokens on successful rows: {tok_ok_p:,}")
print()
if tok_bad_p or tok_bad_c:
    print("=> the instrument summed these rows' TOKENS but dropped their REQUESTS.")
    print("   A row cannot be worth its tokens and worth none of its requests: whichever way")
    print("   the failure landed, the request was made and consumed provider quota.")
else:
    print("=> the failed rows carry no usage, so the two ledgers are consistent: the")
    print("   instrument's request count and token count agree, and the only disagreement")
    print("   is whether a request that failed five times counts as a request. It does.")

print()
print("=== what an errored row looks like in full ===")
if bad:
    r = bad[0]
    for k in sorted(r.keys()):
        v = r[k]
        s = str(v)
        print(f"  {k} = {s[:100]}{'...' if len(s) > 100 else ''}")

print()
print("=== the margin this changes ===")
for label, total in (("as the instrument counted it", 3217), ("as it was actually spent", 3292)):
    print(f"  {label:<28} {total:,} / 3,800 bound -> margin {3800 - total:,}")
