#!/usr/bin/env bash
# The text channel's backend is a separate interpreter (v0.2 report §10: `<BST>` =
# /home/czx/bstvenv/bin/python for textworld/alfworld). Same trap as metaworld: the venv's
# bin/python is a symlink, and the venv's site-packages is found from the *invoked* path.
set -u
BST=/home/czx/bstvenv/bin/python
echo "### is the venv there"
ls -la "$BST" 2>&1 | head -2
echo
echo "### what it can import"
"$BST" -c 'import sys
print("exe:", sys.executable)
for m in ("textworld", "alfworld", "gym"):
    try:
        __import__(m); print("  OK  ", m)
    except Exception as e:
        print("  fail", m, type(e).__name__)
' 2>&1 | tail -8
echo
echo "### the text channel env-check"
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
PYTHONPATH=. "$BST" -m embodied_agent.benchmark.cli env-check 2>&1 | tail -18
