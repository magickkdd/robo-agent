import json
from embodied_agent.core import v02

live = v02.schema_fingerprint()
frozen = json.load(open("configs/experiment/v02_schema_freeze.json"))["schema_fingerprint"]
print("equal:", live == frozen)
for k in sorted(set(live) | set(frozen)):
    a, b = live.get(k), frozen.get(k)
    print(("OK  " if a == b else "DIFF"), k, a, b)
print("n_event_types:", len(v02.EVENT_MODULE), "n_ablations:", len(v02.ABLATION_CONDITIONS))
print("event_types:", sorted(v02.EVENT_MODULE))
print("ablation conditions:", sorted(v02.ABLATION_CONDITIONS))
