"""Check the two-ledger reader against the instrument on the real E1 archive.

The claim under test: a run's spend is the sum of the per-episode ledgers AND the run-level
goal-parse ledger, and the sum equals the instrument's own count. If `batch_spend` returned
2,936 — the per-episode figure — the gap would still be there and the function would be
wording the problem instead of closing it.

The falsifiable part: 2,936 + 281 must come out to 3,217, the number the instrument reported
for this batch. Agreement is not a formality — it is the only evidence available that the
281 are the goal parses and not some other 281.
"""
import json
import os
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
from embodied_agent.evaluation.run import batch_spend

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"

# what the instrument said, transcribed from the batch's own report rather than recomputed
INSTRUMENT = {"http_requests": 3217, "prompt_tokens": 11668006, "completion_tokens": 209285}

# what the per-episode ledgers alone said, which is the figure the stop rule was reading
PER_EPISODE_ONLY = {"http_requests": 2936, "prompt_tokens": 11593585, "completion_tokens": 192449}

fail = []


def check(name, got, want):
    ok = got == want
    print(f"{'OK  ' if ok else 'FAIL'} {name} | got {got!r} want {want!r}")
    if not ok:
        fail.append(name)


print("=== the batch roots, taken from the episodes/ directories ===")
# A batch root is the parent of an `episodes/` directory. Deriving it this way — rather than
# from the ledger files — is what lets the goal ledgers be *counted* below: a driver that
# starts from the per-episode ledgers cannot see a batch that wrote goal parses and then died
# before its first episode, which is precisely how the 281 stayed hidden for a whole batch.
batch_roots = set()
for dirpath, dirnames, _filenames in os.walk(ARCHIVE):
    if "episodes" in dirnames:
        batch_roots.add(dirpath)
batch_roots = sorted(batch_roots)
print(f"batch roots: {len(batch_roots)}")

# Independently: every goal ledger's batch root, found by walking the goal files.
goal_roots = set()
for dirpath, _dirnames, filenames in os.walk(ARCHIVE):
    if "goal_calls.jsonl" in filenames:
        goal_roots.add(os.path.dirname(dirpath))
orphans = sorted(goal_roots - set(batch_roots))
print(f"batch roots carrying a goal-parse ledger: {len(goal_roots)}")
print(f"goal ledgers under a root with no episodes/: {len(orphans)}")
for o in orphans[:5]:
    print(f"    ORPHAN {os.path.relpath(o, ARCHIVE)}")
check("no goal-parse ledger is invisible to an episode-driven walk", orphans, [])

totals = {"http_requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
per_only = {"http_requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
goal_only = {"http_requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
for r in batch_roots:
    s = batch_spend(r)
    pe = s["per_episode_ledgers"]
    g = s["goal_parse_ledger"]
    for k in totals:
        totals[k] += s[k]
        per_only[k] += pe[k]
        goal_only[k] += g[k]

print()
print("=== the two readings, side by side ===")
for k in ("http_requests", "prompt_tokens", "completion_tokens"):
    print(f"  {k:>18}:  per-episode only {per_only[k]:>10,}   + goal parses {goal_only[k]:>9,}"
          f"   = {totals[k]:>10,}")
print()
print(f"  the difference the per-episode-only reading hides: "
      f"{totals['http_requests'] - per_only['http_requests']} requests "
      f"({100.0 * (totals['http_requests'] - per_only['http_requests']) / totals['http_requests']:.1f}%)")
print()

print("=== the cumulative counter, on the real rows ===")
# If a reader summed `http_requests_total` it would get a number far above the truth, because
# that field is cumulative per episode. Showing both makes the trap concrete rather than
# described: the same rows read two ways.
cum = 0
for r in batch_roots:
    import glob
    for p in glob.glob(os.path.join(r, "episodes", "*", "model_calls.jsonl")):
        for line in open(p, encoding="utf-8"):
            if line.strip():
                cum += int(json.loads(line).get("http_requests_total") or 0)
print(f"  summing http_requests_total (wrong) : {cum:,}")
print(f"  summing http_requests_this_call     : {per_only['http_requests']:,}")
check("the cumulative counter would overstate by more than 3x", cum > 3 * per_only["http_requests"], True)

print()
print("=== against the instrument ===")
for k, want in INSTRUMENT.items():
    check(f"instrument agrees on {k}", totals[k], want)
print()
print("=== and against what the old reader believed ===")
for k, want in PER_EPISODE_ONLY.items():
    check(f"per-episode-only reading reproduced ({k})", per_only[k], want)

print()
if fail:
    print(f"RESULT: {len(fail)} FAILED -> {fail}")
    raise SystemExit(1)
print("RESULT: the two-ledger reading equals the instrument; the gap is exactly the goal parses")
