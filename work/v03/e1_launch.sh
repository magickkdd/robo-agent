#!/usr/bin/env bash
# P2' E1, the remaining cells. Run detached because the batch is hours of wall time and the
# SPEC's discipline is a checkpoint per cell, not a babysitter: the driver writes a cell-level
# summary the moment a cell finishes, so this can be killed and resumed without re-paying.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
LOG=/home/czx/embodied-agent-batches/v03/e1/e1_batch.log
mkdir -p "$(dirname "$LOG")"
export PYTHONPATH=.
nohup /home/czx/miniforge3/envs/embodied/bin/python work/v03/e1_batch.py \
  >> "$LOG" 2>&1 &
echo "launched pid $! -> $LOG"
sleep 20
tail -5 "$LOG"
