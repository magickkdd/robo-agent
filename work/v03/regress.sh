#!/usr/bin/env bash
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
/home/czx/miniforge3/envs/embodied/bin/python -m pytest tests/ -q -p no:randomly 2>&1 | tail -30
echo "PYTEST_RC=${PIPESTATUS[0]}"
