#!/usr/bin/env bash
# LH-2 on E1's six long_horizon cells, unchanged (SPEC §6 E1: the same scorer, the same frozen
# config reference). The v0.2 command shape is reused verbatim; only the run directories differ.
# Zero cost: the instrument reads event logs and executes nothing.
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
OUT=/home/czx/embodied-agent-batches/v03/e1/lh_score
mkdir -p "$OUT"

DIRS=""
for arm in full wo_planning wo_working_memory wo_replanning wo_episodic_memory wo_skill_acquisition; do
  d=$(ls -d /home/czx/embodied-agent-batches/v03/e1/long_horizon_$arm/*/ 2>/dev/null \
      | while read x; do [ -d "$x/episodes" ] && echo "$x"; done | head -1)
  d=${d%/}
  [ -z "$d" ] && { echo "missing cell long_horizon_$arm"; exit 1; }
  echo "  $arm -> $d"
  DIRS="$DIRS $d"
done

echo
$P -m embodied_agent.evaluation.long_horizon $DIRS --out "$OUT" --reference full 2>&1 \
  | grep -v 'pybullet build time' | tail -25
echo "LH2_RC=$?"
ls -la "$OUT"
