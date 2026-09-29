"""Does the MEM-1 artifact say which seat produced it?

`measure_episodic_pairs` now carries the seat, but MEM-1 is written by `episodic_metrics` over
the same directory. If that artifact omits the seat, then the very table §11 publishes for the
Memory group can be read without knowing whether the reader was a rule policy or a model — which
is the exact confusion the seat plumbing exists to prevent.
"""
import json

ROOT = "/home/czx/embodied-agent-batches/v03/em_rules_rehearsal"

for name in ("episodic_memory_MEM_1.json", "pairs_measured.json", "pairs_run.json"):
    d = json.load(open(f"{ROOT}/{name}", encoding="utf-8"))
    print(f"=== {name} ===")
    print("  keys:", sorted(d.keys()))
    for k in ("planner", "policy", "model_config", "seat_note", "perceive", "kind", "root"):
        if k in d:
            v = str(d[k])
            print(f"    {k} = {v[:90]}")
    print()
