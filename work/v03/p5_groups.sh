#!/usr/bin/env bash
# The two §11 groups whose inputs are already complete, run with the same entry points and the
# same frozen config references v0.2 used. Zero cost: both are offline readers over artefacts
# that exist (SKILL-1 over the six finished lh cells' event logs; the VLM group over a
# deterministic StubReader).
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
OUT=/home/czx/embodied-agent-batches/v03/e1

DIRS=""
for arm in full wo_planning wo_working_memory wo_replanning wo_episodic_memory wo_skill_acquisition; do
  d=$(ls -d $OUT/long_horizon_$arm/*/ 2>/dev/null | while read x; do [ -d "$x/episodes" ] && echo "$x"; done | head -1)
  d=${d%/}
  [ -n "$d" ] && DIRS="$DIRS $d"
done

echo "### SKILL-1 (5 rows) over the six lh cells"
$P -m embodied_agent.cli skill-metrics --measure $DIRS --out $OUT/skill_score 2>&1 \
  | grep -v 'pybullet build time' | tail -12
echo "SKILL1_RC=$?"
ls -la $OUT/skill_score 2>/dev/null

echo
echo "### V1/V2/V3 (3 rows) — the stub-based 47-case contrast, re-measured on this code"
$P -m embodied_agent.cli vlm-contrast --contrast --contrast-set all --set all --out $OUT/vlm_contrast 2>&1 \
  | grep -v 'pybullet build time' | tail -12
echo "VLM_RC=$?"
ls -la $OUT/vlm_contrast 2>/dev/null

echo
echo "### MEM-1 (4 rows): does em-pairs take a model decision source?"
$P -m embodied_agent.cli em-pairs --help 2>&1 | grep -E 'planner|policy|ablation|perceive' || \
  echo "  (no planner flag on em-pairs -- a model-planner MEM-1 would need a new path)"
