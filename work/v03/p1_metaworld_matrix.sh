#!/usr/bin/env bash
# `import metaworld` works in this interpreter, but not with PYTHONPATH=. set from the repo
# directory. Isolate which of the two it is, because the backend's refusal names the wrong
# thing entirely (it tells the operator to use a different interpreter, and
# /home/czx/mwvenv/bin/python is a symlink to this one).
set -u
PY=/home/czx/miniforge3/envs/embodied/bin/python
REPO=/home/czx/embodied-agent-robot-agent-embodied-agent-4
PROBE='
import os, sys
print("  cwd:", os.getcwd(), "| PYTHONPATH:", os.environ.get("PYTHONPATH"))
print("  sys.path[:4]:", sys.path[:4])
try:
    import metaworld
    print("  metaworld OK ->", metaworld.__file__)
except Exception as e:
    print("  metaworld FAILED:", type(e).__name__, e)
    for p in sys.path:
        if os.path.isdir(p) and "metaworld" in os.listdir(p):
            print("    ^ found in:", p, "->", os.listdir(p)[:8])
'

echo "### A: cwd=repo, PYTHONPATH unset"
(cd "$REPO" && env -u PYTHONPATH $PY -c "$PROBE")

echo
echo "### B: cwd=repo, PYTHONPATH=."
(cd "$REPO" && PYTHONPATH=. $PY -c "$PROBE")

echo
echo "### C: cwd=/tmp, PYTHONPATH=."
(cd /tmp && PYTHONPATH=$REPO $PY -c "$PROBE")

echo
echo "### D: where does metaworld live?"
$PY -c "import sys; print([p for p in sys.path])"
