#!/usr/bin/env bash
# P3' E2 探针批：1 个任务集 x {vlm} x 2 集，决策源仍为 rule（只换感知通道）。
#
# 这是 D2 里"先跑探针批再定规模"的那一批。规模固定为 1 集 x 2 repeats，不多跑一集——
# 它的判据是"perception 记录能不能落盘 + 账本有没有行"，这两个读数第一集就会说话。
#
# 计费：每次 look 一次请求。lh_c1 的预算是 max_http_requests 32，所以两集的请求上界是
# 64 次（预登记里写死的上界，实际按 1 次/look 计）。
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
ARCHIVE=/home/czx/embodied-agent-batches/v03/e2_probe
rm -rf "$ARCHIVE"; mkdir -p "$ARCHIVE"

echo "=== E2 探针批：--perceive vlm / --planner rule / agnes-vision.yaml ==="
echo "=== 预算上界 64 次请求（2 集 x lh_c1 的 max_http_requests 32）；停在撞顶或跑完 ==="
$P -m embodied_agent.cli evaluate \
   --set long_horizon \
   --planner rule \
   --modes B \
   --repeats 2 \
   --out-root "$ARCHIVE/vlm_full" \
   --perceive vlm \
   --ablation full \
   --experience-store "$ARCHIVE/store.jsonl" \
   --skill-memory "$ARCHIVE/lib.jsonl" \
   --cases lh_c1_shared_pair_restore \
   --model-config configs/models/agnes-vision.yaml \
   --frozen \
   2>&1 | grep -v 'pybullet build time' | tail -30
echo "E2_PROBE_RC=${PIPESTATUS[0]}"
