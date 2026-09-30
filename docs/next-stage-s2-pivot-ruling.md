# Next-Stage 规格（S2 正式验收）—— 转向裁定

日期：2026-09-30 ｜ 裁定人：用户（D 级决定） ｜ 性质：归档裁定，不产生任何新读数

## 1. 事实（全部可回产物核对）

- `Next-Stage-Embodied-Agent-Spec-v1.0.md` 的 S2 部分执行到 **W2.5**：W0/W1/W2.1 产物在
  `outputs/{w0_precheck, w1_baseline, s2_w2_1}`（W2.1 是只读复现与错误归因，报告实测与官方一致）；
  W2.2–W2.5 已提交（`e5f9914` → `927b818`，修 `a69ffcd`）。W2.5 交付了
  `configs/tasks/v2_suite_config.yaml`、`configs/tasks/v2_clean.yaml` 与
  `tests/unit/test_w2_5_benchmark.py`（判据按 §8.2 写进测试）。
- **§8.2 的正式验收批（TabletopOrganize-v2，90 集 × 判据）从未运行**：`runs/` 全部 88 个批次
  均为 2026-09-18 的 s1/smoke 时期产物，无任何 v2 批次。
- 次日（2026-09-19）`Continuous-Decision-Embodied-Agent-SPEC-v0.1.md` 立项，其阶段日志 P0 开篇
  声明：「唯一有效规格：v0.1 …… 历史 Next-Stage-… 只作为背景，不作为口径来源」。
  此后四轮（v0.1 → v0.2 → v0.3 → BST v0.1）均在持续决策与压力测试两条线上，Next-Stage 未再推进。

## 2. 裁定

**正式归档，不补跑。** Next-Stage 规格整份降为历史背景（把 v0.1 阶段日志里那句话升格为
独立可引用的裁定）；S2 正式验收批不再执行。

**后果与复活条件**：

- `v2_suite_config.yaml`、`v2_clean.yaml` 与 `test_w2_5_benchmark.py` **留在库内**，作为
  "判据已冻结、批次未运行" 的证物；`v2_clean.yaml` 的 90 集规划不构成任何已承诺的分母。
- 若未来任何阶段需要 TabletopOrganize-v2 的读数，须**另立预登记**重新定规模与被试，
  并遵守既有纪律：不沿用旧分数冒充修复后分数（v0.1 SPEC §9 P0）、判据先进测试后开跑。
- 本裁定不追溯影响已封存的任何报告；`outputs/s2_w2_1/` 的复现与归因读数保持有效——
  它们量的是 S1 时期的批次，与是否补跑 S2 验收无关。
