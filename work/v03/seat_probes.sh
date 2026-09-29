#!/usr/bin/env bash
# P0' seat probes: E1 (decision seat) and E2 (vision seat). Declared cost: 2 billed calls.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
ARCHIVE=/home/czx/embodied-agent-batches/v03
mkdir -p "$ARCHIVE/p0_seat_probes"

FRAME=runs/gui/episodes/smoke_clean.B.r0/frame_001_pick.png
echo "vision probe frame sha256: $(sha256sum "$FRAME" | cut -c1-16)  ($FRAME)"
echo

echo "############ E1 seat: configs/models/bst_text_agnes.yaml (text) ############"
$P work/v03/seat_probe.py --config configs/models/bst_text_agnes.yaml --mode text \
   --out "$ARCHIVE/p0_seat_probes"
echo "E1_PROBE_RC=$?"
echo

echo "############ E2 seat: configs/models/sensenova-vision.yaml (vision) ############"
$P work/v03/seat_probe.py --config configs/models/sensenova-vision.yaml --mode vision \
   --image "$FRAME" --out "$ARCHIVE/p0_seat_probes"
echo "E2_PROBE_RC=$?"
