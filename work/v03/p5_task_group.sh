#!/usr/bin/env bash
# §11's Task group: four rows per cell, from `cli report`, which is the same instrument and the
# same frozen-config reference v0.2 used. The cells' own reports are the source; this only
# collects the four rows and prints them beside v0.2's, because §7.3 forbids merging the two
# decision sources into one number.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
OUT=/home/cxz
OUT=/home/czx/embodied-agent-batches/v03/e1
mkdir -p "$OUT/reports"
for set in long_horizon em; do
  for arm in full wo_planning wo_working_memory wo_replanning wo_episodic_memory wo_skill_acquisition; do
    d=$(ls -d $OUT/${set}_$arm/*/ 2>/dev/null | while read x; do [ -d "$x/episodes" ] && echo "$x"; done | head -1)
    d=${d%/}
    [ -z "$d" ] && continue
    $P -m embodied_agent.cli report --run-dir "$d" --json-only > "$OUT/reports/${set}_${arm}.json" 2>/dev/null
    echo "  ${set}_${arm} -> $(wc -c < "$OUT/reports/${set}_${arm}.json") bytes"
  done
done
