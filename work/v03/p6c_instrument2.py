"""Confirm the read path: `M2.followed_round_share` reads `episode_summary.policy.trace`.

The metric's own definition says the truth source is "`episode_summary.policy.trace`, filed by
`evaluation/run.py` from the policy's own per-round record", and that the row is `forbidden` from
being reported as reuse — it is the self-report, published *beside* the behavioural row. On a
model seat `policy=None`, so no policy ran and there is no trace to read. If so, 0/15 is an
instrument that was never installed, and the row belongs in `not_measured` with that reason —
exactly as `M4.uncheckable_and_governing` already does when no pair produces it.
"""
import glob
import json
import os

SEATS = [("/home/czx/embodied-agent-batches/v03/em_rules_rehearsal", "rule seat"),
         ("/home/czx/embodied-agent-batches/v03/mem1_model", "model seat")]

for root, label in SEATS:
    have = 0
    without = 0
    traces = 0
    entries = 0
    followed_true = 0
    for path in glob.glob(os.path.join(root, "**", "episode_summary.json"), recursive=True):
        s = json.load(open(path, encoding="utf-8"))
        pol = s.get("policy")
        if pol is None:
            without += 1
            continue
        have += 1
        tr = pol.get("trace") or []
        traces += 1
        entries += len(tr)
        followed_true += sum(1 for e in tr if e.get("memory_followed"))
    print(f"=== {label} ===")
    print(f"  episode summaries        : {have + without}")
    print(f"  with a `policy` block    : {have}")
    print(f"  with NO `policy` block   : {without}")
    print(f"  with a policy trace      : {traces}")
    print(f"  trace entries            : {entries}")
    print(f"  entries with memory_followed=true : {followed_true}")
    print()

print("=== the verdict ===")
print("  rule seat : the policy wrote the trace, so the self-report row has a source")
print("  model seat: no policy ran, so `memory_followed` has no writer. The row is")
print("             NOT-MEASURED on a model seat, and reporting 0/15 as a share would")
print("             be a fabricated negative — the strongest-sounding wrong number here,")
print("             because a 0 reads as evidence while an absent instrument does not.")
print()
print("  the row that IS seat-agnostic is the behavioural one the definition prefers:")
print("  M2.trajectory_changed_and_completed, which reads executed actions and final state")
print("  and exists whoever decided. It reads 0/2 on the model seat.")
