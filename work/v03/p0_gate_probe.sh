#!/usr/bin/env bash
# P0' gate probe for E2: is `--perceive vlm` reachable from the MAIN CLI once a
# vision-capable config is passed?  `channel_readiness(channel, adapter=None)`
# refuses `vlm` when no adapter is handed in, and `_perception_kwargs` calls it
# with one argument.  Ask the entry point rather than reading the call: a refusal
# here costs no money because it happens before the first request.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
S=/tmp/v03_gate_probe
rm -rf "$S"; mkdir -p "$S"
: > "$S/store.jsonl"; : > "$S/lib.jsonl"

run () {
  echo "--- $*"
  $P -m embodied_agent.cli evaluate --set long_horizon --planner "${PLANNER:-rule}" \
     --modes B --repeats 1 --out-root "$S/out" --perceive "$1" --ablation "$2" \
     --experience-store "$S/store.jsonl" --skill-memory "$S/lib.jsonl" \
     --cases lh_c1_shared_pair_restore --no-frames ${EXTRA:-} 2>&1 \
     | grep -v 'pybullet build time' | tail -6
  echo "   rc=${PIPESTATUS[0]}"
}

echo "############ privileged + full (the E1 shape), zero spend ############"
run privileged full

echo
echo "############ vlm + full, with a vision-capable --model-config ############"
EXTRA="--model-config configs/models/sensenova-vision-4k.yaml" run vlm full

echo
echo "############ vlm + full, WITHOUT --model-config ############"
EXTRA="" run vlm full

echo
echo "############ stub + wo_vlm (the zero-spend limited-semantic baseline) ############"
EXTRA="" run stub wo_vlm
