"""Two of the sixteen MEM-1 episodes wrote no ledger. Find out why, from the run's own records.

`em_p1/wo_episodic_memory/reader` and `em_p2/wo_episodic_memory/reader` each spent 5 requests and
produced no episode ledger. Five requests is about one goal parse plus retries, so the guess is
the goal parse failing — but the guess is the thing to test, not to report. Both arms matter:
they are the *control* arm's reader, and a control arm that cannot run is not a control.

Everything read here is what the run already wrote: the manifest, the goal ledger, the errors file.
"""
import json
import os

ROOT = "/home/czx/embodied-agent-batches/v03/mem1_model"
TARGETS = [("em_p1", "wo_episodic_memory"), ("em_p2", "wo_episodic_memory")]

for pair_id, arm in TARGETS:
    base = os.path.join(ROOT, pair_id, arm, "reader")
    print(f"=== {pair_id} / {arm} / reader ===")
    for dirpath, dirnames, filenames in os.walk(base):
        rel = os.path.relpath(dirpath, base)
        for fn in sorted(filenames):
            print(f"  {os.path.join(rel, fn)}")
        if rel == ".":
            continue
    # the goal ledger under the timestamped run root
    for dirpath, _dn, filenames in os.walk(base):
        if "goal_calls.jsonl" in filenames:
            print(f"  goal ledger at {os.path.relpath(dirpath, base)}")
            for line in open(os.path.join(dirpath, "goal_calls.jsonl"), encoding="utf-8"):
                if not line.strip():
                    continue
                d = json.loads(line)
                print(f"    kind={d.get('kind')} case={d.get('case_id')} repeat={d.get('repeat')}")
                print(f"    http_this_call={d.get('http_requests_this_call')} "
                      f"attempts={d.get('transport_attempts')} "
                      f"retries={d.get('transport_retries')}")
                print(f"    error={d.get('error')}")
                print(f"    goal_semantic_problems={d.get('goal_semantic_problems')}")
                print(f"    assignments={d.get('assignments')}")
        if "errors.jsonl" in filenames:
            print(f"  errors.jsonl at {os.path.relpath(dirpath, base)}")
            for line in open(os.path.join(dirpath, "errors.jsonl"), encoding="utf-8"):
                if line.strip():
                    print(f"    {line.strip()[:300]}")
        if "episode_summary.json" in filenames:
            s = json.load(open(os.path.join(dirpath, "episode_summary.json"), encoding="utf-8"))
            print(f"  episode_summary at {os.path.relpath(dirpath, base)}: "
                  f"outcome={s.get('outcome')} rounds={s.get('decision_rounds')}")
    print()

print("=== for contrast, the same arm/role on em_p3 (which did run) ===")
base = os.path.join(ROOT, "em_p3", "wo_episodic_memory", "reader")
for dirpath, _dn, filenames in os.walk(base):
    if "episode_summary.json" in filenames:
        s = json.load(open(os.path.join(dirpath, "episode_summary.json"), encoding="utf-8"))
        print(f"  outcome={s.get('outcome')} rounds={s.get('decision_rounds')} "
              f"memory_records={len(s.get('memory_records') or [])}")
