#!/usr/bin/env bash
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
$P -m pytest tests/ -q -p no:randomly 2>&1 | tail -3

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
chore(P5'): E1 全表 + §11 三个组重取 + 两条不变量已可判

E1 跑完：12 格 / 153 集 / 2,936 次请求（预登记上界 3,800，余量 864），**零 429、零 run error**。
格级摘要按 SPEC §8.3 一格一写，所以两次误停与一次续跑都没有重付一格。

§13.3 第一条不变量：**12/12 格 asked == armed 且 > 0**，v0.2 臂根的 575/0 现在是 153/153。
168 计划集里 15 集死在模型的目标解析上（无集目录、无账），差额留在分母里、不重跑——
按 SPEC §11.5"它们留在分母里"。

Cost 组第一次有真数（三列同源，都从每集的 model_calls.jsonl 逐行求和）：
  prompt 11,593,585 / completion 192,449 / http 2,936 / latency 0.66–84.1 s
  USD 保持 null，`pricing_configured=False`（免费端点、无单价，编数不合法）

§11 三组已重取（同仪器、同冻结配置引用）：
1. **Long-horizon 6 行**（LH-2，6 格 63 集）：**80 条对照行里 58 条值变了**，
   v0.2 同一台仪器是 **1/80**（唯一那条是 wo_replanning 的 L6.recorded_vs_inferred）。
   六臂之间逐值相同的指标 16 个里只剩 3 个（v0.2 是 14/16）。
   两条 v0.2 属于结构性 n/m 的行现在有数了：
     `L2.dependency_edge_violation` 六格全 n/m -> `full` **1.0 (2/2)**（模型写下依赖边并全违反）
     `L6.replanning_effectiveness` 0.0 (0/4) -> `full` **0.5789 (11/19)**
   两张表并排打印、**不合并**（§7.3 禁止平均）："两源对照汇总"落盘
   e1/lh_score/two_sources_summary.json，连 per_metric 明细。
2. **Skill Acquisition 5 行**（SKILL-1，`arm_audit ok=True`，6 批 63 集逐集对账）：
   `S1.not_executable` 0.0 (0/20) -> **0.3 (3/10)**；`S1.gap_episodes` 0.3333 (20/60) ->
   0.1731 (9/52)；`S2.frozen_rule_share 1.0` 对 `S2.strict_reading_share 0.0` 这对
   相反读数**原样保留**（v0.2 最不愿印又最该印的那一行）；`S4` 五行仍全 n/m 并各自带理由；
   `S5.refused_after_validation 1.0 (7/7)`、入库仍为 0，所以 `S5.invalid_skill_admitted`
   仍然"入库 0 次 ⇒ 没有分母可说谎"。
3. **VLM 3 行**：重跑通过（`paired_cases: 47`、`not_comparable: []`）。这一组是 stub 确定性分割，
   所以它是对仪器的**对照**而不是对能力的读数——v0.2 的 V1/V2/V3 全部来自 StubReader，
   这一点没有变，`full`-on-VLM 的那一行由 E2 交付而不是由这一组。

**Memory 组（4 行）本轮未重取，理由是结构性的不是"没跑"**：`cli em-pairs` 没有 `--planner`，
所以把模型放进 pair set 的决策席需要新增一条路径——那是产品改动，本阶段未获授权。
按 §13.2，not-measured 只允许来自"该臂该通道的结构性不可达"；这里诚实的字眼是
**这条路径不存在**，属结构那一类，已具名。
MSG

echo
git rev-parse HEAD
git status --porcelain | wc -l
