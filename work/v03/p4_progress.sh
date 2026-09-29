#!/usr/bin/env bash
set -u
P=/home/czx/miniforge3/envs/embodied/bin/python
R=/home/czx/embodied-agent-robot-agent-embodied-agent-4/work/v03/p4_progress.py
for c in p0_full p0_wo_episodic_memory; do
  echo "########## $c"
  $P "$R" "/home/czx/embodied-agent-batches/v03/e5/$c" 2>&1 | head -24
done
