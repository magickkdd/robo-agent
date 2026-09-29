#!/usr/bin/env bash
# P3' E2 formal batch. Detached: the vlm half is four episodes of camera rounds, the other two
# channels cost nothing, and the batch writes its own report when it finishes.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
LOG=/home/czx/embodied-agent-batches/v03/e2/e2_formal.log
mkdir -p "$(dirname "$LOG")"
export PYTHONPATH=.
nohup /home/czx/miniforge3/envs/embodied/bin/python work/v03/e2_formal.py \
  >> "$LOG" 2>&1 &
echo "launched pid $! -> $LOG"
sleep 30
tail -8 "$LOG"
