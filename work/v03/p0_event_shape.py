import json
import os

ep = "/tmp/p5c/long_horizon/full"
rundir = None
for name in os.listdir(ep):
    p = os.path.join(ep, name)
    if os.path.isdir(os.path.join(p, "episodes")):
        rundir = p
print("run dir:", rundir)
eps = sorted(os.listdir(os.path.join(rundir, "episodes")))
f = os.path.join(rundir, "episodes", eps[0], "events.jsonl")
print("episode:", eps[0])
types = {}
first_decision = None
with open(f, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        t = rec.get("type")
        types[t] = types.get(t, 0) + 1
        if t == "decision" and first_decision is None:
            first_decision = rec
print("event type counts:", json.dumps(types, ensure_ascii=False, sort_keys=True))
print("first decision record keys:", sorted(first_decision) if first_decision else None)
print(json.dumps(first_decision, ensure_ascii=False)[:900])
