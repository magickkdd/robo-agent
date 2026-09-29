# Continuous-Decision Embodied Agent v0.3 —— 最终报告

写于 P5'。SPEC 是 `Continuous-Decision-Embodied-Agent-SPEC-v0.3.md`；过程、失败与更正记在
`docs/continuous-decision-v0.3-phase-log.md`。本报告只做一件事：**按 §13 的七条完成定义、
§11 的六组 27 行、§9 的七条臂，把 v0.3 结束时究竟有什么被量到、什么没有被量到，说一次**。

v0.2 报告（`docs/continuous-decision-v0.2-final-report.md`）保持封存；它的读数一个字未改，
本报告凡引用它都是并排引用，**不合并、不平均**（SPEC §7.3：rule 的一格与 model 的一格是两个
实验，不是一次实验跑两遍）。

---

## 0. 数据身份：本报告里每个数从哪儿来

| 来源 | 内容 | 身份 |
|---|---|---|
| 代码 | 工作树，v0.3 全程在提交上跑 | `git_commit 717fd5f…` 至 `384ecf2…`，每个 manifest 自带 `commit` / `dirty`；**`dirty: false`**（D4 之后每批的 manifest 都记真提交） |
| 写侧契约 | `core/v02.py` 的事件类型表与消融注册表 | **15** 事件类型、**7** 消融条件；`schema_fingerprint()` 与 `configs/experiment/v02_schema_freeze.json` **逐项相等**（`schema_version 4`、`module_sha256 65f2b240…` 等 7 键）——本阶段**没有重冻结**（§12 禁止），四个产品改动都动不到它 |
| 封存配置 | 冻结任务表与两份预注册 | `cli freeze --check` → `2f1c74f52e91`；`cli prereg --check` → `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3`；`cli em-pairs --check` → `7df449ed2f4d`（三条退出码 0，P0' 与每个提交前各重跑一次） |
| v0.3 预登记 | E1 / E2 / E3 三份 + 两份增补 | `configs/experiment/v03_e{1,2,3}_preregistration.json`（`b918f55`）与 `v03_e1_preregistration_amendment_1.json`（上界 2484→3800）、`v03_e2_preregistration_addendum_1.json`（E2 正式规模）。**原预登记跑后一字未改**，增补另立文件 |
| E1 产物 | 12 格 / 153 集 | `/home/czx/embodied-agent-batches/v03/e1/`；格级摘要 `e1/e1_state.json`、全表 `e1/e1_table.json` |
| §11 三组重取 | LH-2 / SKILL-1 / VLM | `e1/lh_score/long_horizon_v0.{json,md}`（703 kB）、`e1/skill_score/skill_acquisition_SKILL_1.json`（`arm_audit ok=true`）、`e1/vlm_contrast/episode_contrast_all.json` |
| E2 产物 | 3 格 / 12 集 | `e2/e2_formal_report.json`；探针批 `e2_probe/`（1 集 ×2） |
| E3 产物 | 4 对 × 2 臂 / 16 集 | `e3/e3_rq6_answer.json`、`e3/e3_criterion4.json` |
| E5 产物 | 12 格 × 2 臂 / 24 集 | `e5/p0_full/progress.json`、`e5/p0_wo_episodic_memory/progress.json` |

**产物不落 /tmp 单点**（§4.12）：全部计费批次的产物在 `/home/czx/embodied-agent-batches/v03/`
下按实验分目录，格级摘要写完即归档。v0.2 报告 §0 末那条教训（`/tmp` 总数漂移、两个不变量不漂）
在 v0.3 仍然成立，所以本报告引用的每个总数都带论域。

**花费**。E1 与 E2 全部跑在免费层端点上，`pricing_configured=False`，所以
`api_cost_estimate` 与 `cost_estimate_usd` 在**所有**产物里保持 `null`（SPEC §7.1：
编一个价格不合法）。报的是请求数、prompt/completion tokens 与逐调用时延。
凭证与完整环境变量从不入库。

---

## 1. §13 完成定义的逐条判定

| # | §13 的条件 | 判定 | 证据 |
|---|---|---|---|
| 1 | **E1 的 12 格逐格**：模型决策源集数 > 0；逐集 manifest 身份三键与 D1 配置一致；`model_calls.jsonl` 非空；`run_error` 分桶 | **达成** | 12/12 格；**153/153 集** armed 且 `asked_the_model` > 0；`model_identities_observed.one_identity_throughout: true`（请求与应答都是 `agnes-2.5-flash`）；1,459 行账；分桶表见 §4 |
| 2 | **六组 27 行**逐行有读数或有命名的 not-measured，且 not-measured 只允许来自"该臂该通道的结构性不可达" | **部分达成**：24 行有读数，**4 行（Memory 组）未重取** | §2。Memory 组的 not-measured 理由是 **`cli em-pairs` 没有 `--planner`**——这条路径不存在，属结构那一类，不是"没跑" |
| 3 | **两条不变量翻转** | **达成** | E1：`asked == armed > 0` 逐格 **12/12**（v0.2 臂根 575/0 → 153/153）。E2：`perception` 记录 **51**（v0.2 臂根 0 条） |
| 4 | **E3** 判据逐条有读数（RQ6 可以答"否"但要有数） | **达成，且答的是"检索与使用有、收益没有"** | §6 RQ6 |
| 5 | **Cost 组** tokens/calls/latency 有真数；USD 非 null 或明示无单价 | **达成** | §4：prompt **11,593,585** / completion **192,449** / http **2,936** / latency p50 3.4 s、p95 15.9 s、max 84.1 s；USD `null`，明示无单价 |
| 6 | **负结果保留** | **达成** | §5：11 条，含 v0.2 的 10 条里仍未翻身的那几条 |
| 7 | **文档门全绿** | **达成** | v0.2 的门（51 检查，按 H-48 #147 只描述、不引它的文件也不引它的实时摘要）本轮**按原样**跑过：**51 checks → ALL CLOSED**，rc=0（它由两个模块常量指向 v0.2 那一对文档，本轮 v0.2 一字未改）。v0.3 的门是**新写的** `work/v03/verify_docs_v03.py`：**17 checks → ALL CLOSED**，含非虚度（把报告的 `58`/`153/153`/`2f1c74f52e91` 同时改坏，4 条检查转红）。它**不是** v0.2 那 51 条的演进——那 51 条里的大多数查的是 v0.3 没有的结构（§11.3 的定位行、附录 H 的计数器、逐行竖线约定），只改两个路径常量会得到 51 条"绿"而没有一条在说 v0.3 的文档 |

**结论：§15 第一半达成。** v0.2 §12 写下的两条路里，本轮走的是**路 A**（D5 用户裁定）：
§15 要的"完整的**模型驱动**长时程闭环"，其两个缺席（A1 决策源、A2 `perception` 记录）都已关闭，
且都是在**同一入口、同一任务表、同一 schema** 下关闭的。

但要按 §12 的纪律说清这句话的边界：**"模型驱动"这一半达成，不等于"每一行都有数"**。
Memory 组 4 行本轮未重取（§2.4），VLM 三行仍是确定性 `StubReader` 的读数（§2.3），
`full`-on-VLM 的成功率一行由 E2 交付且交付的是一个**负数**（§2.3、§5.4）。

---

## 2. §11 的六组 27 行

### 2.1 Task（4 行）——`cli report`，12 格逐格

`primary_metric` 是仪器自述的："independent complete-task success, aggregated per case then
compared per case"；分母是 `planned_runs`，仪器自己声明"0 of them are machinery failures"。

| §11 行 | v0.3 读数（模型决策源） | v0.2 读数（rule） | 来源 |
|---|---|---|---|
| complete success | lh 逐格 **2–9 / 12**（见下表）；em 逐格 **13–16 / 16** | lh 六臂逐格 **8/12**、em 六臂逐格 16/16 | `cli report` 12 次 |
| partial progress | `by_object_count`：lh_full 4→3、5→3、6→2（成功集 / 集） | 同形状，六臂逐格相同 | 同上 |
| constraint violations | `false_finish_attempts` 逐格 0–1（`lh_full` 1、`lh_wo_planning` 1，其余 0） | 12 格全 0 | `behaviour.B` |
| invalid completion | 逐格归因表，形状随臂变（见下） | lh 每格 `{REPEATED_INVALID: 4}` | `failure_attribution.B` |

lh 六格的成功数与失败形状（`ok / goal_resolution_error / failed / needs_clarification`）：

| 格 | success | goal_resolution_error | failed | needs_clarification | 主要失败原因 |
|---|---|---|---|---|---|
| `full` | 8 | 0 | 2 | 2 | AMBIGUOUS_TASK 2、INVALID_DECISION 1、MODEL_ERROR 1 |
| `wo_planning` | 6 | 2 | 4 | 0 | goal_resolution_error 2、INVALID_DECISION 2 |
| `wo_working_memory` | **2** | **4** | 6 | 0 | **MODEL_ERROR 6**、goal_resolution_error 4 |
| `wo_replanning` | **9** | 1 | 2 | 0 | REPEATED_INVALID 2 |
| `wo_episodic_memory` | 8 | 1 | 1 | 2 | AMBIGUOUS_TASK 2 |
| `wo_skill_acquisition` | 8 | 1 | 2 | 1 | AMBIGUOUS_TASK 1、INVALID_DECISION 1、MODEL_ERROR 1 |

**`MODEL_ERROR` 是 v0.2 不可能出现的失败形状**：它是模型的决定没能通过 schema/语义校验且有界
修复预算用尽。v0.2 报告 §6.2 记的 24 个非成功 lh 集"全是 `REPEATED_INVALID`"。
`wo_working_memory` 的 8 集里 6 集是 MODEL_ERROR，这就是它 2/12 的直接原因（§5.2）。

`constraint violations` 这行照旧要说实话：12 格**不是全 0**（`full` 与 `wo_planning` 各 1 次
假完成），而 v0.2 是全 0。假完成**从 0 变成了非 0**——这条负结果是本轮新增的。

### 2.2 Long-horizon behavior（6 行，LH-2）

16 个指标 × 6 臂 = 96 个读数格、80 条对照行（`--reference full`）。

**本轮最该被印出来的一张表**——同一台仪器、同一份冻结配置，只换决策源：

| | rule（v0.2） | **model（v0.3）** |
|---|---|---|
| 对照行总数 | 80 | 80 |
| **值变了的行** | **1** | **58** |
| 值相同的行 | 71 | 14 |
| 两侧都 n/m 不可比的行 | 8 | 8 |
| 六臂之间逐值相同的指标 | 14 / 16 | **3 / 17** |

v0.2 报告 §6.2 第 1 条的原话是："privileged 通道上六个开关里五个不动 outcome。**这不是
'模块没用'的证据，是这批仪器分辨不出它**……能解释它的是：*要分辨需要一个会自己点行的模型决策者
—— 那是要花钱的一批，v0.2 不代付。*" 现在那批花了，**80 条对照行里 58 条动了**。

两条在 v0.2 属于结构性 n/m 的 §11 行现在有读数：

| §11 行 | v0.2（rule） | v0.3（model） |
|---|---|---|
| `dependency violations`（`L2.dependency_edge_violation`） | **六格全 n/m**——"没有任何一条依赖边被写下" | **`full` 1.0 (2/2)**：模型写下了依赖边，**并把它们全部违反了** |
| `recovery/replanning effectiveness`（`L6.replanning_effectiveness`） | **0.0 (0/4)**——v0.2 的头号负结果 | **`full` 0.5789 (11/19)** |

其余四行的代表读数（`full` 格）：

| §11 行 | v0.3（model） | v0.2（rule） |
|---|---|---|
| subgoal completion | `L1.assignment_completion 0.85 (51/60)`、`L1.unmeasured_at_end 0.1 (6/60)` | 0.7333 (44/60)、0.2 (12/60) |
| progress regressions | `L3.regression_rate 0.1053 (6/57)`、`agent_caused 0.8571 (12/14)` | 0.037 (2/54)、1.0 (2/2) |
| unnecessary rework | `L4.unnecessary_rework 0.0 (0/70)` | 0.0 (0/58) |
| repeated failures | `L5.repeated_failure_share 0.0774 (12/155)` | 0.0857 (12/140) |
| recovery effectiveness | `L6.recovery_effectiveness 0.7692 (10/13)` | 0.6667 (4/6) |
| `L6.recorded_vs_inferred` | `full` 0.8462 (11/13) vs **`wo_replanning` 0.0 (0/5)** | 1.0 (6/6) vs 0.0 (0/6) |

最后一行是**唯一一条在两个决策源下都在 `wo_replanning` 上归零的行**——v0.2 全批 80 条里唯一
值变了的那条，本轮仍然是。机制一致，样本不同。

### 2.3 VLM（3 行）——重跑通过，但这一组仍然不是视觉模型的读数

`vlm-contrast --contrast --contrast-set all --set all` 在本轮代码上重跑：`paired_cases: 47`、
`not_comparable: []`、`definition_version: v0.2-p1f-6`。

**必须说清的一件事**：V1/V2/V3 全部来自**确定性 `StubReader`**（确定性色彩分割），所以这一组
是**对仪器的对照**，不是对视觉模型能力的读数。v0.2 §8 B5 指出"§11 VLM 三行只有 StubReader 的
读数"——本轮这一点**没有变**，也不该被写成变了。

真正变化的是另一件事，而且它是负的：§9 的 `full`-on-VLM 行走进了 E2（§2.5），结论是
**在 privileged 通道上 4/4 成功、换成相机后 0/4**。v0.2 §6.5 把相机通道的净负归因于
"`silhouette_fit` 形状盲 + 属性全等才重绑 + 恢复器认不出'槽位无候选'"三件的串积；本轮在主
CLI 上用真视觉模型重现了同一形状（§5.4）。

### 2.4 Memory（4 行）——本轮未重取，理由具名

`cli em-pairs` **没有 `--planner`**（`work/v03/p5_instruments.sh` 实测：无 planner / policy /
ablation / perceive 任一 flag）。所以把模型放进 pair set 的决策席需要新增一条代码路径——
那是产品改动，本阶段未获授权。

按 SPEC §13.2，not-measured 只允许来自"该臂该通道的结构性不可达"，不允许来自"没跑"。
这里诚实的字眼是：**这条路径不存在**，属结构那一类。因此 M1–M4 四行按 v0.2 的读数**沿用**，
并在下面标明它们是 rule 决策源的读数，不是本轮的：

| §11 行 | 沿用读数（**v0.2 / rule**） | 本轮 |
|---|---|---|
| retrieval relevance | `M1.matched_beyond_the_batch_name 1.0 (28/28)`、`distinct_rows_vs_store 1.0 (4/4)`、`rows_per_round 1.0 (28/28)`、`rows_refuted 0.2857 (8/28)` | 未重取（无 `--planner`） |
| successful reuse | `M2.followed_round_share 0.5 (14/28)`、`followed_where_control_chose_the_same 0.4286 (6/14)`、`trajectory_changed_and_completed 0.5 (2/4)`、**`outcome_improvement 0.0 (0/4)`** | 未重取 |
| negative transfer | `M3.treatment_worse_or_costlier 0.0 (0/4)`、`declines_per_refuted_round 0.0 (0/8)`、`decline_branch_reachable 0.0 (0/4)` | 未重取 |
| stale memory usage | **`M4.refuted_and_still_governing 1.0 (8/8)`**、`uncheckable_and_governing 0.5714 (4/7)` | 未重取 |

**E3 与 E5 各自给了 Memory 机制在另外两个通道上的读数**（§6 RQ6、RQ7），所以"记忆机制本轮
没有任何读数"这句话不成立；不成立的是"MEM-1 这台仪器在模型决策源上重取了一次"。

### 2.5 `full`-on-VLM：§8 B5 那一行，本轮有了一个真数

E2 正式批（1 任务集 × 3 通道 × 2 集 × 2 repeats = 12 集，`--perceive {vlm, stub, privileged}`、
`--planner rule`，只换感知通道）：

| 格 | 臂 | 集 | **`perception` 记录** | asked | http | prompt | completion | outcomes | `manifest.offline` |
|---|---|---|---|---|---|---|---|---|---|
| `vlm` | `full` | 4 | **51** | 2 | 73 | 73,614 | 19,456 | failed 2, infra 2 | **false** |
| `stub` | `wo_vlm` | 4 | **0** | 2 | 0 | 0 | 0 | failed 2, infra 2 | true |
| `privileged` | `full` | 4 | 0 | 4 | 0 | 0 | 0 | **success 4** | true |

三件事要一起读：

1. **`stub` 的 0 是正确读数**，不是失败：臂是 `wo_vlm`，拥有 `perception` 记录的那个模块是关的
   （`Ablation.violations()` 正是查这个）。所以 vlm 与 stub 的对比是可归因的——同一批 case、
   同一 seed、同一任务表，只有读帧的方式不同。
2. **4 集是基础设施错误，原因四处相同**且不是模型失败：
   `lh_c3_capacity_and_shift` 声明了两个黄色物体（`obj_yellow_1` 与 `obj_yellow_6`），而相机
   通道按颜色给身体命名，`GroundingMap.from_objects` 因此拒绝。
   **P1-b 验证过颜色唯一性的是 47 个冻结 case，那是感知任务集，不是 `long_horizon` 集**——
   所以这条性质在 lh 集上不成立，而相机通道依赖它。
   ⇒ **E2 选的两个 case 里有一个在相机通道上根本跑不起来**；这四集留在分母里
   （SPEC §11.5），`full`-on-VLM 这一行的分母因此是 **2 而不是 4**，而 `privileged` 是 4。
   **三格不在同一个分母上，这是本轮的一条结构性限制，不是一个可以平均掉的数。**
3. **`manifest.offline` 逐通道正确**：`vlm` 格 false、`stub` 与 `privileged` 格 true。v0.2 的
   `offline` 只看 planner（§7 的第 2 个缺陷）。

### 2.6 Cost（5 行）——第一次有真数

**请求有两个数，不是一个**（§4 详述）：本表的 `cost.B` 是仪器自己逐格汇总的，而 §1 与 §4
的格级表是从每集 `model_calls.jsonl` 的 `http_requests_this_call` 逐行相加得来的。目标解析的
请求记在每格的 `goal_resolutions/goal_calls.jsonl` 里，不在逐集账本里——**两边都要读**。
下一阶段把两个账本一起量过之后，本表有两处要更正（原文写"相差 281 次"，见 §4 与残留 13）：

| 口径 | 逐集账本 | 目标解析 | 合计 |
|---|---|---|---|
| 账本行数 | 1,459 | 262 | 1,721 |
| **请求（发出的）** | 2,936 | 356 | **3,292** |
| 请求（拿到回应的） | 2,304 | 281 | 2,585 |
| 以错误收尾的行 | 138 | 15 | 153 |
| **什么都没返回的请求** | 632 | 75 | **707（21.5%）** |

失败形态是 **136 次 HTTP 429** 与 **17 次 `DecisionSchemaError`**。429 拿不回任何内容，
所以**它们不带 token**——这正是逐 token 的读者看不出缺口的原因；而限流配额是真的被消耗了，
它就是这个通道上真正的约束。仪器的 3,217 是**逐集请求（含其错误）+ 目标解析请求（不含其错误）**
的混口径，既不是发出数也不是回应数（见 §4）。

| §11 行 | v0.3 读数（仪器 `cost.B`，12 格合计） | v0.2 读数 |
|---|---|---|
| model calls | **3,217**（混口径；真实发出 **3,292**，拿到回应 **2,585**） | 0 |
| tokens | prompt **11,668,006** / completion **209,285** | 0 / 0 |
| latency | 逐调用，逐格列出（**不跨格池化**，见下） | null（没有调用可计时） |
| simulator time | `sim_time_s_total` 合计 **3,109.4 s** | lh 354.4 s（六格同值） |
| skill-generation cost | `C2.generation_wall_seconds_per_candidate 0.4961`、`sandbox_runs_per_candidate 3.0 (21/7)`、`simulator_seconds_per_sandbox_run 2.1276` | 1.2894 s/候选、3.0、2.1125 |
| （USD） | **`null`，`pricing_configured: false`** —— 免费端点、无价，**编数不合法** | 同 |

**延迟按格给，不池化**（仪器自述："only a mean over the same kind is comparable"，而本批
`by_kind` 只有 `decision` 一种，但"每格的 p50"与"全批的 p50"仍不是同一个对象）：

| 格 | calls | mean | p50 | p95 | max |
|---|---|---|---|---|---|
| `lh_full` | 182 | 6.397 | 3.398 | 15.938 | 42.766 |
| `lh_wo_planning` | 141 | 6.081 | 2.365 | 17.451 | 41.644 |
| `lh_wo_working_memory` | 92 | 8.752 | 6.735 | 17.875 | 23.059 |
| `lh_wo_replanning` | 153 | 6.419 | 3.587 | 17.125 | 36.286 |
| `lh_wo_episodic_memory` | 142 | 6.315 | 2.672 | 18.136 | 72.249 |
| `lh_wo_skill_acquisition` | 142 | 6.316 | 3.639 | 18.733 | 56.670 |
| `em_full` | 111 | 5.660 | 3.386 | 15.672 | 24.641 |
| `em_wo_planning` | 85 | 6.298 | 3.376 | 18.143 | 67.792 |
| `em_wo_working_memory` | 98 | 6.204 | 3.351 | 19.062 | 30.905 |
| `em_wo_replanning` | 100 | 6.173 | 2.744 | 16.763 | 37.011 |
| `em_wo_episodic_memory` | 110 | 6.702 | 3.438 | 18.100 | 45.588 |
| `em_wo_skill_acquisition` | 103 | 8.992 | 4.700 | 29.893 | **84.141** |

逐格的 p50 落在 **2.365–4.700 s**、p95 落在 **15.672–29.893 s**，全批最慢的一次调用
**84.141 s**（在 `em_wo_skill_acquisition`）。E2 另计：51 次调用，min 2.776 / p50 4.692 / max 34.83 s。

---

## 3. §9 的七条臂：消融读出了什么

| 臂 | 通道 | 记录面 | 行为面 | 判决 |
|---|---|---|---|---|
| `full` | privileged + model | 15/15 事件类型在盘上（`event_coverage.episodes_where_a_declared_event_never_fired: 0`） | lh 8/12、em 15/16 | 参照臂 |
| `w/o Planning` | 同 | `plan` / `plan_revision` → 0 | lh 6/12；`L1.plan_row_completion` n/m（**按设计失分母**） | 掉了 2 集 |
| `w/o Working Memory` | 同 | `rows_used` → 0 | **lh 2/12**；**8 集里 6 集 MODEL_ERROR** | **掉得最狠**，且失败形状变了 |
| `w/o Replanning` | 同 | `recovery_action` → 0；`L6.recorded_vs_inferred` **0.0** | **lh 9/12**（比 `full` 好） | 与 v0.2 相反（v0.2 六格逐格相同） |
| `w/o Episodic Memory` | 同 | `memory_use` → 0 | lh 8/12、em 16/16；`L1.assignment_completion` 0.7636（−0.0864） | 略低，且**有可测差异**（v0.2 是 0.0） |
| `w/o Skill Acquisition` | 同 | `skill_gap/candidate/validation/library` → 0 | lh 8/12 | 与 `full` 同（v0.2 也是同） |
| `w/o VLM` | **相机（E2）** | `perception` **必须**为 0（`stub` 格实测 0 条） | **privileged 4/4 → vlm 0/4** | v0.2 头号负结果重现 |

三条必须一起读的话：

1. **v0.2 那个"五个开关里四个不动 outcome"的结论作废了**，而它的**理由**（仪器分辨不出）才是
   当时正确的诊断。换成模型决策源之后 58/80 条对照行有了差，所以那五条"没有收益"现在要重读：
   它们不是没收益，是**在 rule 决策源下测不出来**。`wo_working_memory`（8→2）与
   `wo_replanning`（8→9）两条方向相反，都必须保留（§5.2、§5.3）。
2. **`w/o Replanning` 比 `full` 好**（9/12 vs 8/12），且 `L6.recorded_vs_inferred` 归零——
   即"丢失事件之后没有记录在案的动作"这件事存在，而关掉重规划居然略微提高成功率。
   n=12，两集之差不是效应量；它是一个**该被追问而不是被解释掉**的数（§5.3）。
3. **臂差现在主要出现在最靠内的两个模块上**（working memory、planning），而"最外部"的
   通道（相机）代价最大——这与 v0.2 的第 2 条（臂差在最外部的两个通道上最大）**方向相反**。
   两个阶段合起来读：v0.2 的零花费读页者**用不到**内部信息，所以外部门禁显得最重要；
   模型决策者**用得到**内部信息，于是内部门禁开始显形。

---

## 4. Cost 组与两把尺的纪律

**两把尺，必须并排印，因为它们量的不是同一个东西。**——而下一阶段量过之后要更正一句：
本节原文说"两把尺差 281 次请求，差额是目标解析"。**这个差额是减出来的，不是量出来的**，
而仪器那一侧本身有口径问题，所以减法把它一起抄了过来。真实差额是 **356**。

| | 逐集 `model_calls.jsonl` 求和 | 仪器 `cost.B` 逐格汇总 | 真实（两账本相加） |
|---|---|---|---|
| http requests | **2,936** | **3,217** | **3,292** |
| prompt tokens | **11,593,585** | **11,668,006** | 11,668,006 |
| completion tokens | **192,449** | **209,285** | 209,285 |

差额的来源是可指的：**目标解析的请求**记在每格的 `goal_resolutions/goal_calls.jsonl`，
**不在**逐集 `model_calls.jsonl` 里。§7 按 `(case, repeat)` 计费一次、各 mode 共用，且发生在
任何 episode 目录存在**之前**，所以这些行在结构上就无处可挂——它们只能是这一格的行级账本。

* **3,217 是个混口径，本节现在说清它是什么**：它是"逐集请求（**含**逐集那 138 个错误行）
  + 目标解析请求（**不含**目标解析那 15 个错误行）"。既不是发出的总数（3,292），也不是拿到
  回应的数（2,585）。任何想和 3,217 对账的人都会在这里卡住，而卡住是对的。
* **token 数字与仪器逐位相符**（11,668,006 / 209,285），因为 429 拿不回任何内容、
  `usage` 为空。**所以逐 token 的读者看不出任何缺口**——这是缺口能藏一整轮的原因。
* 停止规则用的是 **2,936**（逐集账本），真实发出 **3,292**——**我的预算记账少算 356 次，
  10.8%**。本轮它没有造成后果（3,292 < 上界 3,800，余量 508；若按仪器的混口径算是 583），
  但如果下一轮的上界贴着 3,000 定，这 356 次就会把批**跑出界**而账上看不出来。
  这是一条新残留（§9 第 13 条），不是一次可以带过的手误。
* `format_repairs: 0`（全批）——模型的 JSON 形状稳定，多出来的请求几乎全是传输重试。

**429 是这一批的主导失败形态，而上一版报告没把它写成主导形态。**3,292 次请求里
**707 次（21.5%）什么都没返回**：**136 次 HTTP 429**（每次重试到 5 次用尽）与
**17 次 `DecisionSchemaError`**。目标解析是最脆的通道——**262 次里 15 次五次全败**，
而这 **15 次正是 168 计划里丢掉的那 15 集**。也就是说：限流不只是让批变慢，
它**直接决定了这批能拿到多少集**。下一轮开工前的探针应当把目标解析的限流余量算进去。

**`api_errors 121` 与 `transport_retries 1,605`（全批 12 格）必须说清楚**，因为它和
"零 429"这句话并不矛盾：1,605 次传输重试与 121 次 api 错误**在适配器的重试循环里被吸收了**，
没有任何一集因此退出。所以：

- **格级**：`run_error` 分桶 `quota_or_429 = 0`、`other = 0`（12 格全部，`errors.jsonl` 皆空）；
- **调用级**：`api_errors 121`、`transport_retries 1,605`、`format_repairs 0`。
- **账本级（本轮新量）**：以错误收尾的行 **153**（逐集 138 + 目标解析 15）= 429 **136** +
  `DecisionSchemaError` **17**。`api_errors 121` 恰好等于**逐集那 136 次 429 里的 121**——
  第三个口径差：它既不含目标解析的 15 次 429，也不含 17 次 schema 错误。

把前者说成"这次没有 429"是错的；正确说法是"**没有 429 打死过一集**，但有 1,605 次传输重试"。
1,605 / 3,292 ≈ **49%** 的请求是重试——这与 §7 第 10 条的 `requests_per_row = 2.027` 是同一件事的
两种说法，也与 SPEC §11 风险登记表把免费端点的突发 429 列为"不受本项目控制"一致。

`model_latency.B` 的自述值得抄下来："only a mean over the same kind is comparable"。
本批 `by_kind` 只有 `decision` 一种，所以每格 182 次调用的均值有意义。

---

## 5. 负结果（§15 要求保留并分析原因）

1. **Memory 组（M1–M4）本轮未重取**：`cli em-pairs` 没有 `--planner`（§2.4）。结构性，不是预算。
2. **`wo_working_memory` 掉到 2/12，且 8 集里 6 集是 `MODEL_ERROR`。** 原因可指到实现：
   关掉工作记忆后页面没有账可读，模型的决定过不了 schema/语义校验，有界修复预算用尽。
   v0.2 的六个开关里没有出现过这个失败形状——**它只有在模型自己读页面时才可能出现**。
3. **`wo_replanning` 9/12 比 `full` 8/12 好。** n=12，两集之差；不解释掉，列为待追问。
4. **相机通道在主 CLI 上仍是净负**：`privileged` 4/4 → `vlm` 0/4、耗时也更高。v0.2 §6.5 的三件
   串积（形状盲 + 属性全等才重绑 + 恢复器认不出"槽位无候选"）在真视觉模型上重现。
5. **`perception` 记录有了（51 条），但四集因颜色重复跑不起来**（§2.5 第 2 点）：
   `long_horizon` 集不满足相机通道依赖的颜色唯一性，而这条性质只在 47 个感知 case 上被验证过。
   ⇒ `full`-on-VLM 的分母是 2，与 `privileged` 的 4 不同格。
6. **`S2.frozen_rule_share 1.0 (7/7)` 对 `S2.strict_reading_share 0.0 (0/7)`** 这对相反读数
   原样保留：过的是冻结模板规则，没过的是 §5.6"三根轴都要在未见实例上动过"的严格读数。
7. **技能习得仍然零准入**：`S5.refused_after_validation 1.0 (7/7)`、`S4` 五行全 n/m。
   原因链未变：单参数程序动不了 `parameters` 轴 ⇒ 冻结门拒绝 ⇒ 库永远冷。
   **新增一条**：`S1.not_executable` 从 v0.2 的 `0.0 (0/20)` 变成 **`0.3 (3/10)`**——
   模型驱动的回合提出的候选里，三成不可执行，而 rule 驱动时是零。
8. **`L2.dependency_edge_violation` 从"六格全 n/m"变成 `full` 1.0 (2/2)**。
   一条从"测不到"变成"测到、而且是全违反"的行。n=2。
9. **`constraint violations` 从 12 格全 0 变成两格各 1 次假完成。** 假完成**从 0 变成非 0**，
   这是一条新出现的负结果。
10. **15 / 168 集死在模型的目标解析上**（`goal_resolution_error`），集中在 lh 集
    （`wo_working_memory` 4、`wo_planning` 2）。这些集没有集目录、没有账，留在分母里不重跑。
11. **E5 的"零花费"预登记断言是错的**（§7 第 6 条）：文本通道的 `run_slot` 只有模型决策源，
    所以那两批各 12 集跑在模型席上，约 740k tokens。免费层所以没有钱，但那句话作为断言不成立。

---

## 6. RQ1–RQ7 的答卷

| RQ | 答 | 读数住所 |
|---|---|---|
| RQ1 长程任务的子目标完成 | 有臂差了。`L1.assignment_completion` 逐臂 0.75–0.9074（v0.2 六臂逐值相同 0.7333） | §2.2 |
| RQ2 Working Memory 的贡献 | **有**：关掉它 8/12 → 2/12，且失败形状从 `REPEATED_INVALID` 变成 `MODEL_ERROR` | §2.1、§3 |
| RQ3 Episodic Memory 的贡献 | **有小幅正贡献的读数**：`wo_episodic_memory` 的 `L1.assignment_completion` 低 0.0864、`L6.replanning_effectiveness` 低 0.329。**收益的方向与桌面 MEM-1 的 `outcome_improvement 0/4` 相反** | §2.2 |
| RQ4 Skill Acquisition 的贡献 | 无：与 `full` 同（8/12），且准入仍为 0。**新增负结果**：候选 30% 不可执行 | §2.6、§5.7 |
| RQ5 Replanning 的贡献 | **读数存在但方向为负**：关掉它反而 9/12，而 `L6.recorded_vs_inferred` 归零 | §2.2、§5.3 |
| **RQ6 记忆机制在连续控制通道上** | **答：检索与使用有且可测；收益没有。** 4/4 读集检索到行并使用（库 0→1），对照臂 8/8 集 `memory_*` 全 0，离线重导出 8/8 逐字段相等 | E3 |
| **RQ7 三个通道上行为是否一致** | **答：记录面一致，产出不一致。** 三通道都检索、都集末写、都过同一个 `experience_from_episode`；但相关率差一个量级：MuJoCo 4/4 读集检索到，文本 12 集里只有 2 集 | E3、E5 |

---

## 7. 本轮查出并修掉的缺陷（每一条都改了生产代码）

| # | 症状 | 根因 | 修法 | 判据 |
|---|---|---|---|---|
| 1 | `--perceive vlm` 在主 CLI 上**结构性不可达**：`cli.py:327` 与 `run.py` 各以一个参数调 `channel_readiness`，`adapter` 恒为 `None`，而拒绝句让操作者去传 `--model-config` | 这条路上根本没有 adapter：`run_group` 无该形参，`run_one_episode(adapter=None)` 从未被填 | `DeepSeekAdapter.from_config`（一份 yaml 变一个 adapter，收成一处）；`channel_readiness(..., config_path=)` 读配置的 `capabilities:`（复用 MuJoCo 侧的 `vision_capability`，不新写第三套）；`run_group` 经 `_camera_adapter()` 接到 `run_one_episode`；缺 key 时抛 `InfraError` 并**点名变量名** | `tests/contract/test_v03_vlm_channel_reachability.py`（15 条） |
| 2 | 感知请求**不落账**（v0.2 残留 **#109**）：真 look 花了 4096 completion token 死在零正文上，全盘无一行账 | `VLMReader` 一直在造行，`_file_row` 在没有回调时直接 return；`build_perceiver` / `build_arm` 根本没有 `on_call` 形参 | 两个 builder 加 `on_call`；`run.py` 把 `log_call` 上移并新增 `log_perception_call`，两个调用点都传 | 同上文件，含"死掉的 look 也落一行"与"缺回调的读法仍合法" |
| 3 | manifest 的 **`offline` 只看决策席**：`--planner rule --perceive vlm` 的批明明花了钱却写 `offline: true` | `planner_kind != "deepseek"` 是不完整的半个问题 | `_batch_is_offline(planner_kind, perceive)`，两个写出点共用 | 同上，含逐组合断言 |
| 4 | 相机臂 **"two ledgers"**：`the tracker stamped v12 for the snapshot the loop asked for as v13` | `Runtime.observe()` 在**测量之前**就加一；一次没测成的观测（被拒的 look、死掉的请求、渲染故障）把回路计数器永久留在 tracker 前面一格 | 没测成的观测不留下版本号，把它本来要用的 ref 还回去；**守卫一字未动** | `tests/contract/test_v03_two_ledgers.py`（5 条），其中"守卫仍然拒"与"两者一致时通过"各一条，删掉守卫满足不了前一条。**该守卫此前零测试** |
| 5 | 文本通道 §5.4 未接（E5，D3 授权） | `TEXT_ARM_MODULES` 三元组、门读模块常量 | 类属性化 + `TEXT_EXPERIENCED_ARM_MODULES` + `AlfredExperiencedRuntime(EpisodicMixin, AlfredPlannedRuntime)`；`run_slot` / `run_batch` / CLI 三个入口接库；集末经**同一个** `write_experience` 写残渣 | `tests/contract/test_v03_text_channel_memory.py`（8 条），其中"裸回路仍拒绝记忆臂"单列 |
| 6 | `cli.py:cmd_run` 的 `if args.single:` 把 **`--single 0`（第一个 slot）当成"没给"**，于是跑整个段 | 用真值判断一个 slot 序号 | 改 `is not None` | 同上文件，用 **AST** 断言（不用子串：解释它的注释会匹配到自己的注释） |
| 7 | 本轮自己造的回归：`DeepSeekPlanner.from_env` 的 `@classmethod` 被误删，`--planner deepseek` 会在第一集 `TypeError` | 我做一次删重复块时把装饰器一起删了 | 恢复装饰器 | 补一条按名打开那道门的测试。**1187 条测试全绿也没抓到**——套件里没有一处用路径调它，而那正是 E1 的调用方式 |
| 8 | 本轮自己造的读法错误，让 E1 的停止规则**误触发了两次** | 把累计计数器 `http_requests_total` 逐行相加（真值 225 报成 12,320）；token 读成顶层字段（真值 793,882 报成 0） | 改读 `http_requests_this_call` 与 `usage.*`；`e1/e1_state.json` 从盘上重导（那份状态是缓存，产物才是权威） | 记进 `v03_e1_preregistration_amendment_1.json` 的 `the_stop_that_triggered_this` |
| 9 | SPEC §6 E1 的字面写法两处不可跑：`--planner model` 不是主 CLI 的合法值；第六条 privileged 臂写 `wo_vlm`，而 CLI 在 `privileged` 上**接受后丢弃**它（那一格变成没有任何 `ablation` 记录的未武装 v0.1 基线） | SPEC 的散文与代码不一致 | 用 `--planner deepseek` 与 P5-c 实际的第六臂 `wo_skill_acquisition`；两处都记进 `v03_e1_preregistration.json` 的 `deviations_from_the_spec_text` | 臂表读自 P5-c 的 12 个格目录与 `matrix.json`，不是读自报告散文 |
| 10 | 预登记上界 2,484 少算了 **21%** | 估算式按每轮 1 次请求；实测 **2.027**（`chat_json` 允许一次 JSON 格式修复 + `max_retries: 4`）。v0.2 不可能知道：那一轮全部零花费，"每轮几次请求"在模型上座之前结构上不可测 | 增补 1，上界 2,484 → **3,800**（= 实测外推 3,016 × 1.25）。原预登记不动 | 增补 1 落盘 `v03_e1_preregistration_amendment_1.json` |

---

## 8. 复现

解释器：桌面/物理与 E1/E2/E3 的仪器 `/home/czx/miniforge3/envs/embodied/bin/python`（下称 `<PY>`），
文本后端与 E5 `/home/czx/bstvenv/bin/python`（下称 `<BST>`），
MuJoCo 后端 `/home/czx/mwvenv/bin/python`（下称 `<MW>`）——**三个 venv 的 `bin/python` 都是指向
同一个 conda 解释器的符号链接，而它们各自能 import 自己那套包，靠的是 Python 按**被调用的那个
路径**去找 `pyvenv.cfg`**。这一点被本轮咬过一次：E3 的第一批全部以 rc=1 退出，而 `env.py:117`
那句"Use /home/czx/mwvenv/bin/python"指得是对的，错的是我用了 conda 那个路径。

```
# 身份
<PY> -m embodied_agent.cli freeze --check      # 2f1c74f52e91
<PY> -m embodied_agent.cli prereg --check       # 23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3
<PY> -m embodied_agent.cli em-pairs --check     # em set matches: 7df449ed2f4d
<PY> -c "import json;from embodied_agent.core import v02;print(json.dumps(v02.schema_fingerprint()))"
<PY> -m pytest tests/ -q -p no:randomly         # 1200 passed, 24 skipped

# E1（计费，2,936 次请求；上界 3,800）
<PY> work/v03/e1_batch.py                       # 12 格；已付的格自动跳过，可续跑

# E1 的仪器
<PY> -m embodied_agent.cli report --run-dir <cell> --json-only          # 12 次 → e1/reports/
<PY> -m embodied_agent.evaluation.long_horizon <6 个 lh run 目录> --out e1/lh_score --reference full
<PY> -m embodied_agent.cli skill-metrics --measure <6 个 lh run 目录> --out e1/skill_score
<PY> -m embodied_agent.cli vlm-contrast --contrast --contrast-set all --set all --out e1/vlm_contrast
<PY> work/v03/p5_two_sources.py                  # 58/80 vs 1/80 的并排对照

# E2（计费，73 次请求）
<PY> work/v03/e2_formal.py                      # 3 格 × 4 集

# E3（零花费，16 集）
<MW> work/v03/e3_batch.py                       # 4 对 × 2 臂 × 2 集
<PY> work/v03/e3_criterion4.py                  # 离线重导出逐字段比较
<PY> work/v03/e3_rq6.py                         # RQ6 答卷

# E5（文本，模型席，约 740k tokens）
<BST> work/v03/e5_batch.py
```

---

## 9. 残留（v0.3 结束时仍未闭合）

| # | 残留 | 关闭它需要什么 |
|---|---|---|
| 1 | **Memory 组 M1–M4 未在模型决策源上重取** | `cli em-pairs` 加 `--planner`（产品改动） |
| 2 | `full`-on-VLM 的分母是 2 而 `privileged` 是 4 | `long_horizon` 集里不满足颜色唯一性的 case 要么换掉、要么相机通道要一个不靠颜色的身体命名 |
| 3 | `wo_replanning` 比 `full` 好（9 vs 8，n=12） | 更多重复；两集之差不是效应量 |
| 4 | `wo_working_memory` 的 6 集 `MODEL_ERROR` | 记到实现的哪一行：页面无账 → 决定过不了校验 → 修复预算用尽 |
| 5 | `constraint violations` 从 0 变成 1 次假完成 | 假完成这一格在 v0.2 没有自己的记录类型（§2.1 的括注仍成立） |
| 6 | 15/168 集死在目标解析上 | 目标解析的失败面；集中在 lh 集 |
| 7 | 技能习得零准入，`S4` 五行 n/m | 一条**任务形状**上的改动（多参数程序可变的 gap），不是门上的改动 |
| 8 | `S1.not_executable` 0.3 | 候选可执行性；rule 驱动时是 0 |
| 9 | 页面保真仍无直接证据 | `decision_context` 不落盘渲染文本 |
| 10 | 渲染器病（成因未知、flaky） | 本轮未观测到复发，但成因仍未查；只仪器化，不修机制 |
| 11 | `#209` 逐轮读数修法 | 仍是条件项（E4 相机批次无排期） |
| 12 | 没有一份**跨解释器的仪器一致性判据** | 桌面（`<PY>`）、文本（`<BST>`）、MuJoCo（`<MW>`）三套 site-packages 各跑各的仪器；本轮三次回归都是绿的，但没有一份判据说"同一台仪器在三个解释器下读同一份产物会读出同一个数" |
| 13 | **预算记账少算 356 次请求（10.8%）**：目标解析的请求记在每格的 `goal_resolutions/goal_calls.jsonl`，不在逐集 `model_calls.jsonl` 里。停止规则用的是后者（2,936），真实发出是 3,292。**本条的数字已更正**——原文写 281，那是 `3217 - 2936` 减出来的，等于把仪器自己的口径问题一起抄了过来；逐账本量得 356。仪器的 3,217 本身是混口径（逐集含错误 + 目标解析不含错误），既非发出数（3,292）也非回应数（2,585） | **已修**：`evaluation/run.py` 新增 `batch_spend(run_root)`，两个账本一起读，并分列"发出"与"拿到回应"；`tests/contract/test_v03_spend_accounting.py` 钉住四个口径坑（两账本、累计计数器、错误行、界的比较对象）。三个口径差（`api_errors 121` 只数逐集 429）也一并记在 §4 |
| 14 | **429 是这批的主导失败形态，而报告没把它写成主导形态**：3,292 次请求里 **707 次（21.5%）什么都没返回**（429 **136** 次 + `DecisionSchemaError` **17** 次）。目标解析 262 次里 **15 次五次全败**，而这 15 次**就是** 168 计划里丢掉的那 15 集 | 已量、已写进 §4；未修：免费端点的限流余量不受本项目控制。下一轮开工前的探针须把目标解析的限流余量算进上界 |
| 14 | `calls_without_an_answered_identifier: 16`（`lh_full` 格）——有 16 次调用没有被记录应答标识，而 `identities` 仍显示"请求什么就答什么" | 仪器自己分了两列（`identities` 与 `calls_without_an_answered_identifier`）；本轮未追查这 16 次是哪种形状 |
