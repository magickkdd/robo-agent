#!/usr/bin/env bash
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python

echo "=== the v0.2 gate, run as shipped (its pair is untouched this round) ==="
cd /tmp/mw117 && $P /tmp/mw117/verify_docs_v3.py 2>&1 | tail -2; echo "v02_GATE_RC=${PIPESTATUS[0]}"
cd /home/cxz 2>/dev/null || cd /home/czx/embodied-agent-robot-agent-embodied-agent-4

echo
echo "=== the v0.3 gate ==="
$P work/v03/verify_docs_v03.py 2>&1 | tail -3
echo "v03_GATE_RC=${PIPESTATUS[0]}"

echo
echo "=== frozen contract, one more time before the commit ==="
$P work/v03/p0_fingerprint.py | sed -n '1,2p'
$P -m embodied_agent.cli freeze --check 2>/dev/null | tail -1
$P -m embodied_agent.cli prereg --check 2>/dev/null | tail -1
$P -m embodied_agent.cli em-pairs --check 2>/dev/null | tail -1

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
docs(P5'): v0.3 最终报告与阶段日志；§15 第一半达成（路 A），Memory 组 4 行结构性未重取

两份文档：`docs/continuous-decision-v0.3-final-report.md`（读数）与
`docs/continuous-decision-v0.3-phase-log.md`（过程、失败与更正）。分工与 v0.2 相同。

§13 七条完成定义：六条达成，一条**部分达成**——24 行有读数，Memory 组 4 行
（M1–M4）未重取，理由是 `cli em-pairs` 没有 `--planner`，**这条路径不存在**，属 §13.2
允许的结构性 not-measured，不是"没跑"。§15 第一半因此按路 A 达成，边界写清：
`full`-on-VLM 交付的是负数，VLM 三行仍是 stub 读数。

本轮的核心读数（全部带论域与产物地址）：
- **§11 Long-horizon 6 行**：同一台仪器、同一份冻结配置，只换决策源 ⇒
  **80 条对照行里 58 条值变了**（v0.2 是 **1/80**），六臂之间逐值相同的指标 16 个里只剩 3 个
  （v0.2 是 14/16）。两条 v0.2 属结构性 n/m 的行现在有数：`L2.dependency_edge_violation`
  六格全 n/m -> `full` **1.0 (2/2)**（模型写下依赖边并全违反）；`L6.replanning_effectiveness`
  0.0 (0/4) -> `full` **0.5789 (11/19)**。两张表并排不合并。
- **§11 Skill 5 行**：`arm_audit ok=true`、6 批 63 集逐集对账。`S1.not_executable`
  0.0 (0/20) -> **0.3 (3/10)**；`S2` 那对相反读数（1.0 对 0.0）原样保留。
- **§11 VLM 3 行**：重跑通过，但这一组是**对仪器的对照**（V1/V2/V3 全部来自确定性
  `StubReader`）——v0.2 说的这一点没变，不写成变了。
- **§11 Task 4 行 / §9 七臂**：模型决策源下臂差显形（`full` 8/12 vs `wo_working_memory`
  2/12 vs `wo_replanning` 9/12），且 `MODEL_ERROR` 是 v0.2 不可能出现的失败形状。
- **§13.3 两条不变量**：E1 **12/12 格** `asked == armed > 0`（575/0 -> 153/153）；
  E2 `perception` 记录 **51** 条（v0.2 臂根 0 条）。
- **Cost 5 行**：第一次有真数。**两把尺并排印**：逐集账本 2,936 请求 / 仪器 `cost.B`
  3,217，差 281 是**目标解析**的请求（记在 `goal_resolutions/goal_calls.jsonl`，不在逐集
  `model_calls.jsonl` 里）⇒ 我的预算记账比仪器少算约 8%，登记为新残留第 13 条。
  `api_errors 121` / `transport_retries 1,605` / `format_repairs 0`；延迟按格给、不池化。
  USD 保持 `null`、`pricing_configured: false`。

文档门（§13.7）：v0.2 的门 `/tmp/mw117/verify_docs_v3.py` 本轮**按原样**跑过
（51 checks -> ALL CLOSED，rc=0）；v0.3 的门是**新写的** `work/v03/verify_docs_v03.py`
（17 checks -> ALL CLOSED，含非虚度：把 `58`/`153/153`/`2f1c74f52e91` 同时改坏，
4 条转红）。**它不是那 51 条的演进**——那 51 条里大多数查的是 v0.3 没有的结构，
只改两个路径常量会得到 51 条"绿"而没有一条在说 v0.3 的文档。这条理由本身也进了门
（`docs/…` 里的两份文档都不许引用该门自己的实时摘要，H-48 #147 的规矩）。

新写的门自己抓出了 5 处，其中 4 处是我第一版检查写得不对（把解释器路径当产物、把 stderr 的
pybullet 横幅当最后一行、范围外的表也算进 §13 的行、过滤得太狠导致"0 artefact paths"——
最后一条是**空检查**，所以门自己印了输入条数），1 处是报告真的把 4 个产物写成裸文件名而它们
分属不同子目录。前四处改门，第五处改报告。

写报告时对自己做了一次范围核对，抓到两处引用的数取了比句子更窄的论域（延迟的 p50/p95 用了
一格、最慢调用用了全批；E2 延迟引的是探针批而正文说的是正式批），两处都改正为逐格/逐批标注。
MSG

echo
git rev-parse HEAD
git status --porcelain | wc -l
