"""Is `M2.followed_round_share = 0/15` a measurement, or an instrument that was never installed?

The field is `memory_followed`, written by `episodic/policy.py` — the `MemoryPolicy`, which is the
reader **only on the rule seat**. On a model seat the driver sets `policy=None` because the model
planner is the decision maker, so `policy.py` never runs. A metric that reads a field nothing
writes returns 0, and 0 reads as "the model followed memory 0% of the time" when the truth is
"nothing was in a position to record it".

The two are told apart by looking at what is actually in the model-seat episodes: are there any
policy trace entries at all, and is `memory_followed` among their keys?
"""
import glob
import json
import os
from collections import Counter

RULE = "/home/czx/embodied-agent-batches/v03/em_rules_rehearsal"
MODEL = "/home/czx/embodied-agent-batches/v03/mem1_model"


def survey(root, label):
    print(f"=== {label} ===")
    trace_keys = Counter()
    events = Counter()
    n_files = 0
    memory_related = 0
    for path in glob.glob(os.path.join(root, "**", "events.jsonl"), recursive=True):
        n_files += 1
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            d = json.loads(line)
            events[d.get("kind") or d.get("event") or d.get("type")] += 1
            # the policy trace is where `memory_followed` would live
            for key in d:
                if key in ("memory_followed", "memory_order", "memory_declined"):
                    trace_keys[key] += 1
            if any(k.startswith("memory") for k in d):
                memory_related += 1
    print(f"  event files: {n_files}")
    print(f"  event kinds: {dict(events.most_common(12))}")
    print(f"  events carrying a `memory*` field: {memory_related}")
    print(f"  memory_* fields seen: {dict(trace_keys)}")
    print()
    return trace_keys, events


r_keys, r_events = survey(RULE, "rule seat (MemoryPolicy is the reader)")
m_keys, m_events = survey(MODEL, "model seat (the model planner is the reader)")

print("=== the difference that decides the question ===")
print(f"  rule seat  has `memory_order` entries : {r_keys.get('memory_order', 0)}")
print(f"  model seat has `memory_order` entries : {m_keys.get('memory_order', 0)}")
print()
if r_keys.get("memory_followed", 0) and not m_keys.get("memory_followed", 0):
    print("  => `memory_followed` exists on the rule seat and is absent on the model seat.")
    print("     So M2.followed_round_share on a model seat is an INSTRUMENT THAT WAS NEVER")
    print("     INSTALLED, not a zero. Reporting 0/15 as 'the model followed memory 0% of the")
    print("     time' would be a fabricated negative.")
    print()
    print("  the rows that ARE seat-agnostic are the behavioural ones — they read executed")
    print("  actions and final state, which exist whoever decided:")
    print("     M2.trajectory_changed_and_completed, M2.outcome_improvement,")
    print("     M3.treatment_worse_or_costlier")
else:
    print("  => both seats carry the field; a zero would be a real measurement.")
