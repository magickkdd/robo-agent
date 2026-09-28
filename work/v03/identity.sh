#!/usr/bin/env bash
# v0.3 P0' identity check (zero spend). Prints only identities, never secrets.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python

echo "=== git ==="
git rev-parse HEAD
git status --porcelain | wc -l

echo "=== freeze --check ==="
$P -m embodied_agent.cli freeze --check; echo "rc=$?"

echo "=== prereg --check ==="
$P -m embodied_agent.cli prereg --check; echo "rc=$?"

echo "=== em-pairs --check ==="
$P -m embodied_agent.cli em-pairs --check; echo "rc=$?"

echo "=== schema fingerprint vs freeze file ==="
$P - <<'PY'
import json
from embodied_agent.core.v02 import schema_fingerprint
from embodied_agent.core.v02 import ABLATION_CONDITIONS, EVENT_TYPES
live = schema_fingerprint()
frozen = json.load(open("configs/experiment/v02_schema_freeze.json"))
print("live :", json.dumps(live, sort_keys=True))
print("frozen:", json.dumps({k: v for k, v in frozen.items() if k in live}, sort_keys=True))
print("equal:", live == {k: frozen.get(k) for k in live})
print("n_event_types:", len(EVENT_TYPES), "n_ablations:", len(ABLATION_CONDITIONS))
PY
