#!/usr/bin/env bash
# Can the §11 instrument chain read an E1 cell? Everything here is an offline reader, so
# this costs nothing, and the answer decides whether the v0.3 report is a re-run of known
# instruments or a build of new ones.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
CELL=/home/czx/embodied-agent-batches/v03/e1/em_full
RUNDIR=$(ls -d "$CELL"/*/ 2>/dev/null | while read d; do [ -d "$d/episodes" ] && echo "$d"; done | head -1)
RUNDIR=${RUNDIR%/}
echo "E1 cell run dir: $RUNDIR"
echo "episodes: $(ls "$RUNDIR/episodes" 2>/dev/null | wc -l)"
echo
echo "### which artefacts does a v0.2-era cell have that these instruments need?"
for f in run_summary.json manifest.json errors.jsonl; do
  [ -e "$RUNDIR/$f" ] && echo "  present: $f" || echo "  ABSENT : $f"
done
echo "  episodes.json present: $([ -e "$CELL/episodes.json" ] && echo yes || echo 'no (v0.2 had one at the cell root)')"
echo
echo "### 1. cli report (Task group rows + cost)"
$P -m embodied_agent.cli report --run-dir "$RUNDIR" --json-only 2>&1 | head -20
echo
echo "### 2. LH-2 needs 6 lh run dirs; is the lh_full cell present yet?"
ls -d /home/czx/embodied-agent-batches/v03/e1/long_horizon_*/ 2>/dev/null | head -8
echo
echo "### 3. the instruments' own entry points"
for m in evaluation.long_horizon evaluation.episodic_metrics evaluation.skill_metrics evaluation.vlm_contrast; do
  $P -m embodied_agent.$m --help >/dev/null 2>&1 && echo "  OK   embodied_agent.$m" || echo "  ?    embodied_agent.$m (needs args)"
done
$P -m embodied_agent.cli em-pairs --help >/dev/null 2>&1 && echo "  OK   cli em-pairs" || echo "  ?    cli em-pairs"
$P -m embodied_agent.cli skill-metrics --help >/dev/null 2>&1 && echo "  OK   cli skill-metrics" || echo "  ?    cli skill-metrics"
$P -m embodied_agent.cli vlm-contrast --help >/dev/null 2>&1 && echo "  OK   cli vlm-contrast" || echo "  ?    cli vlm-contrast"
