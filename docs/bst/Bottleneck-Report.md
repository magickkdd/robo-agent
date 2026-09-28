# Benchmark-Stress-Test v0.1 · Bottleneck Report

规格：`Benchmark-Stress-Test-v0.1-Agent-SPEC.md`（SPEC-BST）§10.5 九节。
批次：`/tmp/bst_v01/full`（268 集）与 `/tmp/bst_v01/screen`（48 集，筛查段）。
本文件**每个数字都可由封存日志重算**，不依赖再调用模型或再运行环境（SPEC-BST §10.4）。
重算入口：

```bash
# §2–§3 的全部率、成本、CI、分类型表
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.cli aggregate \
  --run-root /tmp/bst_v01/full --task-manifest /tmp/bst_v01/task_manifest.json
# §4–§7 的标签交叉表、首次偏离、按标签成本
PYTHONPATH=. /home/czx/bstvenv/bin/python /tmp/bst_v01/scratch/report_stats.py \
  /tmp/bst_v01/full /tmp/bst_v01/annotations/annotations_full.jsonl
# §2.2 的漏斗两列与 §4 P2 的 56 集 / 58 轮（只读遍历封存事件）
/home/czx/bstvenv/bin/python /tmp/bst_v01/scratch/funnel_and_held.py /tmp/bst_v01/full
# §6 的逐轮文本（读回，不重跑）
PYTHONPATH=. /home/czx/bstvenv/bin/python work/text_trace.py /tmp/bst_v01/full/episodes/slot236_*
```

`metrics.json = 2026-09-20T05:35:05+0800` 那次聚合的产物；标注文件与判据哈希链路见
`docs/bst/compatibility_delta.md` D39 与 `/tmp/bst_v01/annotations/guidelines_provenance.json`。

---

## 1. 实际被试、数据、预算、信息与动作条件，以及所有偏离与未完成项

### 1.1 被试（SPEC-BST §5.5 的身份核对）

| 项 | 值 | 来源 |
|---|---|---|
| provider / model | `agnes` / `agnes-2.5-flash` | `manifest.json::model` |
| base_url host | `api.agnes-ai.cn`（`api_key_env: LLM_API_KEY`，密钥本身不记录） | 同上 + `configs/models/bst_text_agnes.yaml` |
| temperature / 每请求 max_tokens / timeout / max_retries | 0.2 / 2048 / 60 s / 4 | `manifest.json::model`（D28、D29 记录了 2→4 与 2048 的来历） |
| 决策提示词版本 | `bst-decide-text-v1`，`prompts_sha256 = 25502db7…` | `manifest.json` |
| 机制哈希 | `rules_sha256 = 23abe58f…aae7b3`、`catalogue_sha256 = f5044d67…` | `manifest.json`；复测仍相等（`commands.md` §7） |
| 代码身份 | commit `4e960a2d2d189cb6d20e19910f83c6f76361f906` + 脏工作树（`changed_files` 逐项列出） | `manifest.json::code` |
| 返回模型名 | 配置 `{agnes-2.5-flash}` == 返回 `{agnes-2.5-flash}`，**没有一次身份违规** | `metrics.json::model_identity` |
| 无返回名的集 | 8 集（这些集一次成功请求都没发生），逐条列 id，不当成匹配 | 同上 |
| 运行时 | Python 3.11.16 / alfworld 0.4.2 / textworld 1.6.2 / pydantic 2.13.5 / numpy 2.4.6 / Pillow absent / stdlib urllib | `manifest.json::python,dependencies` |
| 平台 | Linux 6.18.33.2-microsoft-standard-WSL2 x86_64 glibc2.35 | `manifest.json::platform` |

### 1.2 数据与调度

`json_2.1.1/valid_unseen`，6 个任务类型，134 个 task_id × 2 次重复 = **268 槽位**，
顺序由 `first_order_key…last_order_key` 冻结（`manifest.json::schedule`），每集携带自己的
`gamefile` 与 `game_tw_pddl_sha256`。划分只有 `valid_unseen` 一种：本批次**没有** train/val
对照，因此不存在"未见泛化"的测量（见 §8）。

### 1.3 预算（`manifest.json::budgets`，全批次同一套）

`max_decision_rounds 80 · max_http_requests 90 · max_skill_calls 60 ·
per_skill_sim_timeout_s 10 · wall_clock_s 900 · max_identical_invalid_attempts 3 ·
max_semantic_repairs 2 · per_action_extra_repeats 2 · max_global_replans 3`；
段级 token 硬顶 40,000,000，每集 600,000。实际花费 6,036,680 = 硬顶的 **15.1%**，
最大单集 76,949（每集上限的 12.8%）：**没有任何一集是被预算掐死的**（§4 的 P4 依赖这一点）。

### 1.4 模型每轮看到什么、能做什么

给出的（`TextDecisionContext.model_payload`，`prompts.py:93-140`）：`schema_version`、
`episode_id`、`context_id`、`round_index`、`state_version`、`observation_ref`、
`task.utterance`（英文原句逐字）、`goal.instruction`、`world{observation.text =
本轮环境原话, location, held_object, facts[], targets}`、`progress`（恒 unknown）、
`candidates`、`attempts`、`budget`、`recent_feedbacks`（深度 3，带各自 observation_ref）。

明确不存在的（`prompts.py:69-70` `BANNED_IN_PAYLOAD`）：`admissible_commands`、
`expert_plan`、`task_type`、`reward`、`won`、`score`、`split`、`solution`。

两条实测的接口事实，后面反复用到：

- `candidates` 在**全部 1,133 轮里都是空表**（长度分布 `{0: 1133}`，`candidates_truncated` 恒 0）。
  冻结契约里的"候选方案"机制在文本后端没有内容可放——规范禁止可执行动作列表。
- `world.targets` 恒为 `[]`（`benchmark/state.py:83`）。文本世界的目标容器只在句子里，
  没有结构化的 target；提示词里用一句 `targets_note` 说明了这件事（`prompts.py:128`）。

动作面 = 16 个技能（`observe`、`pick`、`place`、`safe_retreat` 4 个通用名 +
`alfred_go/take/move/open/close/use/clean/heat/cool/examine/inventory` 12 个原生名），
一轮最多一个 `execute`；`finish/blocked/clarify` 各自立即结束本集。

### 1.5 偏离与未完成项（全部在 `compatibility_delta.md` 有编号）

批次内实质影响解释的偏离：

| 编号 | 一句话 | 对本报告的影响 |
|---|---|---|
| D28 | `max_retries 2 → 4`（Phase 2 唯一一次预检调整，不动分数门槛） | §2 的 1.48 请求/轮来自这里 |
| D29 | 每请求生成上限按 §5.3 发放 `max_tokens 2048` | 成本口径 |
| D31 | 三个无效动作率之一此前结构性为 0 | §4 P1 的分母定义 |
| D32 | 标注与探针产物放在 run root 之外 | §10.4 布局的一处已知偏离 |
| D34 | 凭证遮蔽把 6 个 task_id 和 1 条 rationale 截到 12 字符 | §10.2 证据缺口，见 §2.6 |
| D35 | 聚合器曾按被截断的 task_id 聚类（3 个任务并成 1 个） | §5 的独立 task_id 计数依赖修好后的口径 |
| D36–D38 | 标注/复核工具自身的记账与静默失败修正 | 只影响工具，不影响 episode |
| D39 | 判据重哈希后 worklist 载荷逐字节不变的自证 + 字节副本机制 | 哈希链路 |
| D40 | 标注器形类计数把"本地拒收"算成"引擎答了"（10 集） | §4 的命令形类表用修正后的数 |

批次结构上的一处事实：全量段是**两个进程**跑完的（slot000–047 与 slot048–267），
接缝处 provider 累计计数器回退 −1,149,098 = 13,723 − 1,162,821，账本按 §13-9 闭合：
`1,162,821 + 4,873,859 = 6,036,680 = 封存值 = 行求和 = 账本`
（`metrics.json::cross_check_against_ledger`，267/268 集逐集一致，唯一不一致的就是接缝）。

未完成项：SPEC-BST §10.5 要求的本报告与 §11 的 `v0.2-Decision-Evidence.md`（本文件之后立刻做）；
§13 的"全部现有测试通过"在 bstvenv 下需要显式 `--deselect` 那条桌面依赖断言（`commands.md` §7），
这是环境差异不是回归。

---

## 2. 成功、部分进度可用性、成本、终止、覆盖与基础设施可靠性

### 2.1 成功率与不确定性

| 指标 | 值 | 口径 |
|---|---|---|
| task_success_rate | **6 / 268 = 0.0224** | 官方成功 / benchmark-valid；模型错误、预算耗尽、blocked、clarify 全部留在分母（§8.1） |
| cluster bootstrap 95% CI | **[0.0037, 0.0485]** | 134 个 task 为簇，2,000 次重抽，seed 20260919；**不是** 268 个独立样本 |
| conservative_delivery_rate | 6 / 268 = 0.0224 | 无基础设施失败，故与上式相同 |
| macro（六类型率均值） | 0.0266 | 4 题的类型与 60 题的类型同权 |
| running_reliability | 268 / 268 = 1.0 | 覆盖率与成功率分开报（§8.1） |

筛查段 48 集 **0 成功**。两段在机制上不是巧合而是同一件事：schema 拒收率
21.86%（screen）vs 20.30%（full），拒收轮 token 占比 26.06% vs 23.54%。

### 2.2 部分进度：**没有**可用的第三方形信号

`metrics.json::not_supported` 四条原样保留：文本指令没有逐条件的第三方评分器，
二值奖励、模型自述或动作计数都不是进度百分比；没有独立约束信号；progress_retention
需要人读的证据（候选行在 `failure_candidates.jsonl`）；程序从未产出过可修订的计划，
所以那个标签是"动作调整"而不是"重规划次数"。

因此下面这张表**只能当命令漏斗读，不能读成进度百分比**（由 `execution_feedback` 中
`executed=True 且 status=completed` 的事件计数，268 集）。两列口径必须分开读：
**"发出过"= 命令被 binder 接受并 step 到引擎**（本地拒收的轮不在内）；
**"引擎报出效果"= 回话不是 `Nothing happens.`**（`metrics.json` 的
`env_no_effect_per_command = 96 / 611` 就是这些）：

| 达成过 | 发出过该命令的集 | 其中引擎报出效果的集 | 说明 |
|---|---|---|---|
| `alfred_take` | 80 | 75 | 拿到过东西 |
| `alfred_open` | 82 | 80 | 开过容器 |
| `heat / cool / clean` | 21 | **4** | 状态改变动词**大多回 "Nothing happens."**（17 集试了但没成） |
| `alfred_use` | 18 | 16 | 开过灯 |
| `alfred_move` | **9** | **4** | 放下动作只被引擎确认过 4 集 |
| 拿到过但从没发出 `move` | **71** | — | 其中 62 集属于"必须放下"的 5 个类型 |
| 官方成功 | 6 | — | 9 次 `move` 里 8 次落在**未成功**的集（放下之后还差条件） |

### 2.3 成本

| 项 | 值 |
|---|---|
| token 合计 | 6,036,680（prompt 5,918,546 / completion 118,134 → **完成部分只占 1.96%**） |
| 每集 token | 均值 22,525，中位 21,418，p95 37,767，最大 76,949 |
| HTTP 请求 | 1,673 次 / 1,133 轮 = **1.48**；242 轮（21.4%）用了不止一次，67 轮把 5 次额度用满 |
| 墙钟 | 3,119.51 s，其中**等模型 3,081.88 s = 98.79%** |
| 单次延迟 | 中位 1.10 s，p95 15.64 s，最大 29.26 s（n=1,133） |
| 环境侧 | 611 条命令、1,133 轮决策、585 个不同命令（重复命令极少，见 §4 P6） |
| 成本估计 | `api_cost_estimate` / `cost_estimate_usd` 保持 null：未配置单价，不臆造（SPEC §7） |

**成功与失败的成本分开报**（§8.2）：6 个成功集共 108,089 token（均值 18,015），
262 个失败集共 5,928,591 token（均值 22,628）。成功比失败便宜 20%，
但样本太小，这个差值不构成任何结论。

### 2.4 终止结构（`metrics.json::termination`，268 集）

| 终止原因 | 集数 | 占比 |
|---|---|---|
| AGENT_BLOCKED | 110 | 41.0% |
| NEEDS_CLARIFICATION | 94 | 35.1% |
| BUDGET_EXHAUSTED | 41 | 15.3% |
| AGENT_FINISH | 17 | 6.3% |
| ENV_TERMINATED | 6 | 2.2% |
| 官方成功 | 6 | 2.24% |

两条必须分开、且都不许合并读的事实：

- `false_finish = 17`、`agent_finish_without_env_done = 17`：**Agent 主动宣布完成的 17 次，
  全部是错的**。反过来，6 个成功集**全部**是环境自己判 done 而结束的
  （`env_done_without_agent_finish = 6`）。也就是说：**这批数据里没有一个实例是
  Agent 自己认出"任务已完成"的。** 成功完全来自环境的终止信号先于 Agent 的判断到达。
- 204 / 268 = **76.1%** 的集以 Agent 主动放弃（blocked + clarify）结束，此时平均只走了
  2.28 条环境命令、4.23 轮决策——预算是 80 轮 / 60 命令。放弃发生在预算的 5% 处。

### 2.5 覆盖与人工复核规模

| 项 | 值 | 口径 |
|---|---|---|
| 标注覆盖 | 264 / 268 集、530 行 | 每集最多 4 条偏离行（`deviation_cap_per_episode 4`） |
| 抽样覆盖 | 265 / 268 = 98.88% | 唯一未成行的抽样集是 slot064（成功集，ENV_TERMINATED，无偏离可读） |
| 判据版本 | `annotation_guidelines.md` sha256 `15e359d04a1f…` | 四个文件头一致；字节副本已归档（D39） |
| 二次复核 | 122 行 / 53 集 = **23.02%** 行覆盖 | §10.3 要求 ≥20%，单位是 episode |
| 复核判决 | held 101、wording_corrected 16、changed 4、重读新增 1 | 标签一致 117 / 不一致 5；**分歧保留**，不静默改标 |
| 评审者 | 单人（同一次会话内间隔复核） | 因此**不给出评审者间一致性估计**（§10.3 明令） |
| validate | `invalid []`、`unlabeled []`（两段各自） | `commands.md` §9 |

### 2.6 基础设施可靠性与证据缺口

- 基础设施失败行 **0**（`denominators.infrastructure_failed = 0`），被替换的尝试 0，
  未跑的槽位 0；`recompute_mismatch = []`。
- 25 轮以 `LLMError` 结束（25 / 1,133 = 2.2%），全部是重试 5 次仍失败的传输错误：
  `model_calls.jsonl` 里 `raw_response = null` 的行数正好等于 `rounds_decomposition.model_errors`。
  其余 1,108 轮有完整原始返回。**1,133 行全部带 `usage`**（D24 补的），
  所以"被拒收的轮仍然花钱"这件事是可逐轮核算的：拒收轮合计 **1,421,317 token = 全批 23.54%**。

§10.2 轨迹最小字段的实测缺口（这些是**结论的边界**，不是排版问题）：

1. **发给模型的请求原文没有持久化。** `model_calls.jsonl` 只记 `prompt_version`、
   `raw_response`、`usage`、`latency`；`events.jsonl` 的 `decision_context` 记的是
   运行时构造的上下文（含 `observation_ref`），但**不含环境原话文本**。
   实测：slot123 的四个产物文件里字符串 `"You are in"` 出现 **0 次**——开局那段房间描述
   （模型第 1 轮唯一能依据的空间信息）在封存日志里不存在，只有 `episode_start.prologue = {}`。
   第 2 轮起环境原话保存在 `feedback.environment_text`，所以缺口是**每集第 1 轮**。
   后果：§4 的 OBSERVATION_LIMITATION 判读只能依据"指令 + 第 2 轮起的原话"，
   不能声称复核了模型第 1 轮看到的完整文本。SPEC-BST §10.2 要求"完整模型请求与响应
   可用于离线核查，不以日志摘要替代"——**响应满足，请求不满足**。
2. **每集 token 计数器在上下文中被遮蔽**：`budget.episode_tokens_used` 与
   `episode_tokens_total` 在 **1,133 / 1,133** 个 `decision_context` 事件里都是
   `***redacted***`。原因是 `_is_secret()` 只放过以 `_tokens` 结尾的键，而这两个键以
   `_used` / `_total` 结尾。成本证据本身在 `model_calls.jsonl::usage` 与
   `episode_summary.json::tokens` 里完整，缺的是"模型当时被展示了什么"这一层。
3. **6 集的 `task_id` 与 1 条 `decision.rationale` 被截到 12 字符**（D34）。
   完整值在 `episode_summary.json` 里（另一条写入路径不过 `_redact`），
   所以证据没有丢，丢的是"事件流自解释"的性质；聚合器曾因同一原因把 3 个任务并成 1 个（D35）。
4. 被遮蔽字段之外，`evidence_refs` 指向的 `obs_NNNN` 只有引用号，没有文本；
   文本要靠同一轮的 `feedback` 对齐。

---

## 3. 分任务类型结果与不确定性

| 类型 | 集 | 成功 | 率 | cluster bootstrap 95% CI | 簇 | 终止结构（blocked/clarify/budget/finish/env） |
|---|---|---|---|---|---|---|
| look_at_obj_in_light | 36 | 5 | 0.1389 | [0.0000, 0.3056] | 18 | 14 / 7 / 6 / 4 / 5 |
| pick_and_place_simple | 48 | 1 | 0.0208 | [0.0000, 0.0625] | 24 | 17 / 17 / 8 / 5 / 1 |
| pick_clean_then_place_in_recep | 62 | 0 | 0.0000 | [0, 0] | 31 | 26 / 22 / 9 / 5 / 0 |
| pick_cool_then_place_in_recep | 42 | 0 | 0.0000 | [0, 0] | 21 | 17 / 15 / 6 / 4 / 0 |
| pick_heat_then_place_in_recep | 46 | 0 | 0.0000 | [0, 0] | 23 | 19 / 16 / 8 / 3 / 0 |
| pick_two_obj_and_place | 34 | 0 | 0.0000 | [0, 0] | 17 | 17 / 17 / 4 / 0 / 0 |

读法（SPEC-BST §8.4 明确要求把这件事说死）：

- 这六列是**固定的任务族**，不是难度等级。`look_at_obj_in_light` 的 CI 下界是 0，
  与四个 0 成功类型的 CI 区间**重叠**，所以"它更容易"在本数据里不成立；
  它 5/6 的成功集中在这里，但 36 集的样本量支撑不了排序。
- 四个 0 成功类型的 CI 是退化的 `[0, 0]`：簇内没有任何成功，重抽不出非零值。
  这是"没观测到"，不是"概率为 0"。
- 唯一能确定的排序来自 §2.2 的漏斗：**所有需要 `move` 的类型都是 0 或 1 个成功**，
  唯一不要求 `move` 的类型（开灯即可）拿了 5 个。这条区分是机制性的，
  不依赖 CI 是否重叠。

---

## 4. Failure Pattern → Evidence → Possible Cause → Competing Explanation

标签计数来自 `annotations_full.jsonl`（530 行 / 264 集，判据 `15e359d0…`）。
"独立 task_id"按 §11.2 的口径数（同一 task_id 的两次重复算一个）。

### P1 顶层键与冻结 schema 不一致 → 整轮白花钱（最高频、最便宜可修）

- **Pattern**：模型把 `subgoal` / `candidate_id` 放在决策对象**顶层**，而 schema 只允许它们出现在
  `execute` 里；本地校验器拒收整轮，环境一步没走。
- **Evidence**：拒收原因计数 `subgoal: Extra inputs are not permitted` **204** 次、
  `candidate_id: …` **196** 次；键位普查 **顶层 400 次 vs `execute` 内 6 次**
  （`refused_key_placement_top_level / _inside_execute`）。全部拒收 276 轮
  （= 266 schema 拒收 + 10 命令形类拒收），触及 **147 / 268 集**，
  烧掉 **1,421,317 token = 全批 23.54%**。人工标签 `INVALID_ACTION`：
  167 行 / **154 集 / 47 个独立 task_id**（远超 §11.2 的 3 条门槛）。
- **Possible Cause（我们的接口）**：`BST_DECISION_SYSTEM` 的输出结构示例把
  `candidate_id` 和 `subgoal` 画在 `execute` 内，但紧接着的第 61 行单独用一条 bullet
  讲 `candidate_id 一律为 null`——一个**脱离嵌套结构**的提法。模型学到的是"这两个键要填"，
  位置学错了。
- **Competing Explanation**：(a) 模型能力不足、不会嵌套 JSON——但同一批模型在同一批次里
  正确嵌套了 6 次，且 `execute.skill/args` 几乎从不错位（`execute.skill` 取值错误仅 2 次），
  所以"不会嵌套"不如"位置提示冲突"经济；(b) temperature 0.2 下的采样噪声——筛查段
  48 集独立复现了同一比率（21.86% vs 20.30%），不是单批抖动；(c) 提示词没问题、
  是 JSON schema 太严——`subgoal` 在顶层确实是**未被要求**的字段，放宽它等于改动冻结机制。
- **不能从本条得出的结论**：不能说"模型不行"。这条的份额（23.54% 成本）首先是**接入缺陷**。

### P2 拿到东西之后不再放下（漏斗断在最后一跳）

- **Pattern**：成功 `take` 之后，`move` 几乎不出现。
- **Evidence**：80 集发出过 `take`（75 集被引擎确认拿到了），**9 集**发出过 `move`（只有 **4 集**
  被引擎确认放下），71 集拿了却没发出 `move`（其中 62 集属于
  必须放的 5 个类型）；`alfred_move` 全批 9 次执行里 4 次有效果、5 次 "Nothing happens"
  （D40 修正后的形类表）。人工侧 `holding_window_scan`：72 集曾被文本告知"你拿着 X"，
  其中 **21 集**的这句话已经滑出深度 3 的反馈窗口，17 条决策提到了所持物的类别，
  但在报告之后做过 put/drop/move 的只有 **5** 次。标签 `STATE_USAGE` 15 行 / 15 集 /
  13 个独立 task_id。
- **Possible Cause**：`world.held_object` 是**逐轮从环境原话重算**的
  （`benchmark/state.py:66-80`：只有那句话说了手上有东西才算已知）。
  于是"上一轮我成功拿起了 mug 3"这一事实在下一轮的 `world` 里读作 `unknown`——
  实测 **56 / 268 集、58 轮**出现"上一轮是具名物体、这一轮变 unknown"且中间没有任何放下动作。
  模型要在 `move` 里逐字复述那个名字，而当前结构化状态字段与它矛盾。
- **Competing Explanation**：(a) 模型不知道 `move` 才是完成放置的命令（知识问题，不是状态问题）——
  与 (b) 状态字段矛盾 一样能解释数据，区分需要一次"把 `held_object` 携带下去、其余不变"的
  最小对照（§9 候选 A）；(c) 提示词已经明说"每轮 world 只含本轮文本明确报告的事实，
  需要时自己去确认"（`prompts.py:51-53`），即接口是**诚实**的，那么这就是
  Agent 层的"未使用已声明的获取手段"——但全批 `alfred_inventory` 只被调用 **4 次 / 3 集**，
  一个 3/268 的使用率更像是接口没有把"什么时候该查"变成可学的信号。
- **不能从本条得出的结论**：不能宣布"需要 Memory"。这里没有任何跨集信息，
  全部是**集内轮间**的事实保留，而 §11.2 明令：没有 Memory 对照就不能说 Memory 有效。

### P3 过早放弃，且放弃时预算几乎没动

- **Pattern**：Agent 大量以 `blocked` / `clarify` 结束。
- **Evidence**：204 / 268 集（76.1%）；这些集平均只走 2.28 条命令、4.23 轮；
  首次标注偏离出现在第 1 轮的有 **66 集**、第 2 轮 103 集、第 3 轮 73 集（共 262 集有标注偏离）。
  `clarify` 的失败还有一层机械原因：`action=clarify must name the missing or conflicting
  information` 单独拒收 **32 次**——模型想澄清却没按格式给出缺什么。
- **Possible Cause**：文本后端的 `progress` 恒为 unknown（没有第三方验证器），
  模型没有任何"你还差几步"的信号；同时 §2.4 显示它从未在正确时机 `finish`，
  说明"完成判据"只能从环境原话里读，而它读不出来。
- **Competing Explanation**：(a) 任务本身对 `agnes-2.5-flash` 太难（能力上限）——
  本批次没有第二个被试、没有 oracle 动作序列，**无法排除**；
  (b) 放弃是合理行为：这些集里 47 集的证据基础是"停止时没有失败命令"
  （`episodes_stopping_without_failed_command = 83`，其中 47 集引用了未公开引用），
  即环境从未给过否定反馈，模型在没有信号的情况下选择停手是可理解的策略。
- **不能从本条得出的结论**：不能说"给更多预算就会成功"。BUDGET_EXHAUSTED 的 41 集
  是另一群（它们一直在试），两群不能混。

### P4 完成判据错位：17 次假完成，0 次真完成

- **Evidence**：`finish_scan`：17 集以 `finish` 结束，**17 集**的 goal 值仍是 unknown；
  rationale 里 11 条是放弃措辞、5 条是"我做完了"的措辞，其中 **3 条点名的物体类别
  在整个 episode 的任何环境原话里都没出现过**；1 条两种都不是。
  反向：6 个成功集全部由 `ENV_TERMINATED` 结束。
- **Possible Cause**：`finish` 的判据被交给模型自己（SPEC-BST §4.5 禁止把官方成功标识放进
  上下文），而环境只在**成功那一刻**才改变文本；模型没有可核对的完成谓词。
- **Competing Explanation**：这是接口设计的必然后果而非缺陷——把完成判据给模型正是本实验要测的
  能力。因此这条的正确读法是：**该能力在本被试上未被观测到具备**（0/17 正确 + 6/6 靠环境），
  而不是"提示词写得不好"。
- **不能从本条得出的结论**：不能引用官方 `won` 反推"模型应该知道自己完成了"——
  那是评测侧信息，SPEC-BST §4.5 禁止进入决策。

### P5 无效果命令的重复（**证伪**了一条常见假设）

- **Evidence**：`episodes_with_any_exact_repeat = 18`，但
  `episodes_with_stuck_repeat_ge3 = 0`、`max_stuck_repeat = 1`；
  611 条命令里 585 个不同命令。形类普查（D40 修正后）：`go` 294 成功 / 43 无效果，
  `take` 75/14，`open` 84/3，`use` 16/4，`examine` 3/10，`inventory` 4/0，`observe` 31/0。
  人工标签 `REPEATED_FAILURE` 只有 4 行 / 4 集 / **3 个独立 task_id**。
- **结论**：**"Agent 卡在死循环里重复同一条命令"在本批次不成立**。
  按 §11.2 的 3-task 门槛，`REPEATED_FAILURE` 刚好踩线（3 个），只能作为案例/风险，
  不称为"重复出现的瓶颈"。
- 同时 `env_rejected_per_command = 0 / 611`：**环境从未拒绝过任何一条走到的命令**——
  命令形类映射是干净的（10 次 binder 拒收全部是"名字没带编号"，见 P1 的邻居）。

### P6 引用了模型没被公开过的标识（18 / 60）

- **Evidence**：`unpublic_reference_episodes = 47`、`occurrences = 60`；
  分类：31 次引用的是**开局 reset 文本**里出现过、但后续轮不再出现的名字；
  29 次引用的名字**从未在任何公开文本里出现过**（其中 7 次落在被 D34 截断的集里，
  无法完全核对）；**18 次**与 `episode_id` 的片段吻合。
- **Possible Cause**：`episode_id` 本身就在发给模型的 payload 顶层（`prompts.py:95`），
  模型把它当成可用 token 抄进 `object_id`。
- **Competing Explanation**：模型从任务指令的英文类别名（"a mug"）自行拼出 "mug 1" 是
  另一种解释，两者都能产生"名字没编号"的拒收（10 次），区分要靠把 `episode_id`
  从载荷里去掉后重测（§9 前置修复项）。
- 标签 `ADAPTER_ERROR` 18 行 / 15 集 / 7 个独立 task_id。

### P7 从未被使用的标签：EXECUTION_FAILURE

9.2 taxonomy 里的 `EXECUTION_FAILURE`（参数与公开前提都正确、执行却失败）在本批次
**0 行**。原因是文本后端没有"执行失败"这一层：命令要么被环境接受并产生文本，
要么报 "Nothing happens."（计入无效果，不是执行失败）。这条要写出来，
否则读者会以为分类器漏了。**主要失败不在物理控制**——本批次也没有物理。

---

## 5. 首次偏离与后续放大；按独立 task_id 的频率

### 5.1 首次标注偏离（262 个失败集，每集取最小 round_index 的那一行）

| 首次偏离的标签 | 集数 |
|---|---|
| INVALID_ACTION | 123 |
| OBSERVATION_LIMITATION | 101 |
| STATE_AMBIGUOUS | 15 |
| INFRASTRUCTURE | 12 |
| STATE_USAGE | 8 |
| BUDGET_LIMIT | 8 |
| UNRESOLVED | 3 |

| 首次偏离出现在第几轮 | 1 | 2 | 3 | 4 | 5 | 7 |
|---|---|---|---|---|---|---|
| 集数 | 66 | 103 | 73 | 11 | 8 | 1 |

**84% 的失败集在第 3 轮及以前就已经出现第一条可读偏离**（242 / 262 集落在 1–3 轮，
平均决策轮数是 4.23）。也就是说：失败不是"后半程走偏"，而是**开局几步内就定了**。

### 5.2 放大机制（成本侧）

1. 顶层键错位 → 整轮被本地拒收（276 轮）→ 该轮照样计费 → **1,421,317 token**；
2. 拒收轮的反馈文本是本地校验器的话，不是环境的话（D40 记的正是这条被误算成引擎回答过）；
3. 模型在下一轮常常换个位置重试同一个意图，于是 `take` 之后没有 `move`（P2）；
4. 没有进展 → 放弃（P3），或错误地宣布完成（P4）。

### 5.3 按独立 task_id 的频率（§11.2 的 3 条门槛）

| 标签 | 行 | 集 | **独立 task_id** | 过门槛？ |
|---|---|---|---|---|
| OBSERVATION_LIMITATION | 224 | 194 | **47** | 是 |
| INVALID_ACTION | 167 | 154 | **47** | 是 |
| BUDGET_LIMIT | 41 | 41 | 28 | 是 |
| STATE_AMBIGUOUS | 31 | 31 | 19 | 是 |
| INFRASTRUCTURE | 25 | 25 | 21 | 是 |
| ADAPTER_ERROR | 18 | 15 | 7 | 是 |
| UNRESOLVED | 17 | 17 | 13 | 是 |
| STATE_USAGE | 15 | 15 | 13 | 是 |
| HISTORY_UNAVAILABLE | 7 | 6 | 6 | 是 |
| REPEATED_FAILURE | 4 | 4 | **3** | 踩线：作为案例，不称普遍瓶颈 |

`OBSERVATION_LIMITATION`（224 行 / 194 集 / 47 task）与 `INVALID_ACTION`
（167 / 154 / 47）是唯二在**行数、集数、独立任务数**三个口径上都占优的模式。
其中 `OBSERVATION_LIMITATION` 的语义要说清：它记的是"这一轮公开文本里没有做出该判断所需的
事实"，**既包括环境真的没说，也包括我们没把已说过的话带过来**——P2 的 56 集正是后一种，
两者在本标签下没有分开计数，这是 §9 候选 A 必须先解决的可分辨性问题。

---

## 6. 代表性案例（全部可用 `work/text_trace.py` 读回）

**成功（环境判 done，非 Agent 判 done）— slot236，`look_at_obj_in_light`，3 步**

```
examine the cd with the desklamp
  > alfred_go    {"target_id":"desk 1"}     You arrive at desk 1. On the desk 1, you see a cd 2, … a desklamp 1.
  > alfred_take  {"object_id":"cd 2", …}    You pick up the cd 2 from the desk 1.
  > alfred_use   {"target_id":"desklamp 1"} You turn on the desklamp 1.
  == ENV_TERMINATED env_done=True official_won=True steps=3
```
第 3 条命令之后环境立即判 done：Agent 没有 `finish`，也没有"我完成了"的判断。
5 个 `look_at_obj_in_light` 成功集全是这个形状（3 步、`use` 收尾）。

**成功（放置类唯一一例）— slot123，`pick_and_place_simple`，4 步**
`go countertop 1 → take soapbottle 1 → go toilet 1 → move soapbottle 1 to toilet 1`，
第 1 轮先被本地拒收一次（顶层 `candidate_id`/`subgoal`），拒收那轮照样花了 5,361 token。

**重复分歧 — 同一 task_id 两种结局（4 个 task_id）**

| task_id | 两次重复的结果 |
|---|---|
| look_at_obj_in_light-CD-None-DeskLamp-308 | 206 赢、236 赢、50/167/250/258 输 |
| look_at_obj_in_light-Pencil-None-DeskLamp-308 | 64 赢、184 赢、4/21/25/36/160/166/174 输 |
| look_at_obj_in_light-Mug-None-DeskLamp-308 | 239 赢，其余 5 次输 |
| pick_and_place_simple-SoapBottle-None-Toilet-424 | 123 赢，114/132/137/164/249 输 |

同一个 gamefile、同一套预算，结局不同——**这是采样方差，不是能力差异**。
它同时给出一件有用的事：赢的路径确实存在且很短（3–4 步），所以失败不是任务不可解。

**P2 的教科书案例 — slot099（`pick_heat_then_place_in_recep`）**

```
  > alfred_go   {"target_id":"countertop 1"}   You arrive at countertop 1. … you see a mug 3.
  > alfred_take {"object_id":"mug 3", …}       You pick up the mug 3 from the countertop 1.   held_after = mug 3
  < [rejected]  INVALID_DECISION                                                          held_after = mug 3
  > alfred_go   {"target_id":"cabinet 1"}      You arrive at cabinet 1. …                    held_after = unknown   ← 手上的杯子消失了
  < [rejected]  INVALID_DECISION                                                          held_after = unknown
  == AGENT_BLOCKED
```
环境从没说过它放下了 mug 3；是我们的 `held_object` 逐轮重算把它读成 `unknown`。
第 3、5 轮的 `go` 之后，模型再没有任何结构化依据去提交 `move mug 3 …`。

**无法归因 — `UNRESOLVED` 17 行 / 17 集 / 13 个独立 task_id**
判据 §9.3 要求"证据等级"，这 17 行是复核后仍只能标 uncertain 的：
环境原话与决策都读到了，但缺少能区分"模型不知道"与"接口没告诉它"的第三条证据。
31 行整体标为 `uncertain`（260 confirmed / 239 supported / 31 uncertain），
二次复核把 4 行改判、16 行只改措辞（`wording_corrected`，D37 新增的第四种判决）。

---

## 7. 三个排序：高频 / 昂贵 / 严重但罕见（不合成总分）

### 7.1 高频（按触及的集数）

| 模式 | 集 | 独立 task_id |
|---|---|---|
| P1 顶层键错位（INVALID_ACTION） | 154 / 268 = 57.5% | 47 |
| OBSERVATION_LIMITATION（含 P2 的状态未携带） | 194 / 268 = 72.4% | 47 |
| P3 放弃（blocked + clarify） | 204 / 268 = 76.1% | — |
| BUDGET_LIMIT | 41 | 28 |
| STATE_AMBIGUOUS | 31 | 19 |

### 7.2 昂贵（"该标签所在集的整集 token"，注意口径是整集，不是该模式的净成本）

| 标签 | 集 | 这些集的 token 合计 |
|---|---|---|
| INVALID_ACTION | 154 | 4,112,340 |
| OBSERVATION_LIMITATION | 194 | 4,023,845 |
| BUDGET_LIMIT | 41 | 1,121,407 |
| STATE_AMBIGUOUS | 31 | 859,112 |
| INFRASTRUCTURE | 25 | 635,000 |
| UNRESOLVED | 17 | 491,842 |
| STATE_USAGE | 15 | 403,999 |
| ADAPTER_ERROR | 15 | 391,935 |
| HISTORY_UNAVAILABLE | 6 | 223,982 |
| REPEATED_FAILURE | 4 | 194,207 |

唯一一处**净**成本（不是整集摊派）：本地拒收轮直接烧掉 **1,421,317 token = 23.54%**，
以及 1,673 − 1,133 = **540 次额外 HTTP 请求**花在重试上。这两个数是 §9 前置修复项的
量化收益上限，且只覆盖"拒收"这一项，不含 P2/P3 的连锁成本。

### 7.3 严重但罕见

| 事实 | 规模 | 为什么严重 |
|---|---|---|
| Agent 主动 `finish` 的正确率 | 0 / 17 | 完成判据从未被自主使用（§2.4） |
| 每集第 1 轮请求原文缺失 | 268 / 268 集 | §10.2 要求可离线核查请求（§2.6-1） |
| `episode_id` 出现在发给模型的载荷里 | 1,133 / 1,133 轮 | 18 / 60 次未公开引用与它吻合（P6） |
| 传输错误耗尽 5 次重试 | 25 轮 / 2.2% | 直接终止 25 集的决策 |
| 无返回模型名的集 | 8 集 | 身份守卫无法在这些集上核对（列名，不算通过） |
| 被 D34 截断的证据 | 6 集 + 1 rationale | 事件流不再自解释 |
| `EXECUTION_FAILURE` 无证据 | 0 行 | 分类器有类无例：说明本后端测不到执行层失败 |

---

## 8. 本批次**没有**测试的能力（结论的边界）

| 未测试 | 为什么没测 | 因此不能说什么 |
|---|---|---|
| 真实视觉 / VLM | 后端是纯文本，`no ai2thor, no torch, no CUDA`（`environment_check.json`）；没有任何像素 | 不能声称需要视觉模型；ALFWorld 文本主实验按 §11.1 明令**不能**支撑"主要失败在感知" |
| 运动控制 / 物理失败 | 文本后端无物理；桌面栈（PyBullet + Panda）有，但本批次不在那里跑 | 不能对控制层下任何结论；`EXECUTION_FAILURE = 0` 是后端的性质，不是 Agent 的性质 |
| 外部扰动与恢复 | 未注入 `fault_injection`；`env_rejections = 0 / 611` | 不能谈鲁棒性、不能谈恢复策略 |
| 跨任务学习 / 经验记忆 | 每集独立上下文，无记忆、无跨集复用，也没有记忆对照组 | **不能宣布 Memory 有效**（§11.2 原话） |
| 技能获取 / 动作扩展 | 16 个技能覆盖了 6 类任务的全部必要动词（成功路径 3–4 步全用现成命令） | 不能宣布"必须做技能学习"；§11.1 要求先有动作覆盖证据 |
| 主动观察策略 | 只有 4 次 `inventory` / 31 次 `observe`，没有为观察设计的对照 | 只能说"几乎没用"，不能说"用了也没用" |
| 泛化划分 | 只有 `valid_unseen` 一个划分，无 train/val 对照 | 不能谈未见泛化差距 |
| 被试间差异 | 只有一个模型，没有第二个被试、没有 oracle 动作序列 | P3 的竞争解释"任务对该模型就是太难"**无法排除**（这是本报告最大的单点局限） |
| 多 Agent、任务图、程序库 | SPEC-BST 明确不在范围内 | 不评 |
| 评审者间一致性 | 单人复核（间隔复核） | 不给 κ 一类指标（§10.3 明令） |

---

## 9. v0.2 候选研究问题、必要对照、预期验证方式，以及暂不选择的方向

按 §11.2：**先说接入诊断不充分的部分，再给 2 个优先候选 + 1 个保留候选。**

### 9.0 前置修复（不是研究方向，是重测的前提）

高频份额主要来自我们自己两处接口缺陷（P1 的键位提示冲突、P6 的 `episode_id` 入载荷），
按 §11.2 的收尾条款，此时的正确结论是**接入诊断尚不充分**，而不是把它们当成 Agent 瓶颈：

1. 把 `subgoal` / `candidate_id` 从提示词里脱离嵌套结构的提法收回 `execute` 内部表述；
   从发给模型的载荷里移除 `episode_id`（`prompts.py:95`，以及 `runner.py:99-109` 的 `tail[-40:]`）。
2. 每集第 1 轮的环境原话落盘（修 §2.6-1 的请求缺口）。
3. 在同一份 48 集筛查清单上重跑一次（改动 1、2 之后），比较
   `schema_invalid_per_decision`（基线 21.86%）与 `unpublic_reference_occurrences`（基线 60）。
   这一步是**判分前提**，不是 v0.2 的贡献。

### 9.1 优先候选 A：已公开事实在轮间的保留方式（State representation，不是 Memory）

- **证据**：56 / 268 集出现"具名持物 → unknown"的无端回落（58 轮，逐轮相邻口径）；
  80 集拿到过东西、只有 9 集发出过放下命令（其中引擎确认放下的 4 集）；
  21 集的持物句已滑出深度 3 窗口；`STATE_USAGE` 15 行 / 13 个独立 task_id。
- **要排除的**：(i) 模型不知道 `move` 是完成动词；(ii) 提示词已经声明"world 只含本轮文本
  报告的事实"，因此这是"未使用已声明的获取手段"而非"状态丢了"。两者都能解释同一批数据。
- **最小对照**（同 task_id、同预算、同提示词，只换保留方式）：
  (a) 现状；(b) `held_object` 跨轮携带直到出现放下句；(c) 携带 + 在 `facts` 里给出该事实的
  历史 observation_ref。主指标：**每集成功 `alfred_move` 数**与"take 之后 3 轮内出现 move 的比例"，
  次指标：到达 `ENV_TERMINATED` 的比例。
- **预期验证方式**：48 集筛查清单里所有"有过成功 take"的集（基线约 80/268 比例），
  三条件各自 2 次重复；若 (b) 显著抬升 move 率而 (a) 不抬升，才允许把这条从
  "接入问题"升级为"状态表示/推理问题"。
- **明确不许做的**：不许在这一步引入任何跨集存储。

### 9.2 优先候选 B：主动信息获取（Observation policy）

- **证据**：`alfred_inventory` 全批 4 次 / 3 集，`observe` 31 次 / 1,133 轮；
  同时 `OBSERVATION_LIMITATION` 覆盖 194 集 / 47 个独立 task_id；
  提示词明确要求"需要时自己去确认"却几乎没被执行。
- **要排除的**：观察并不免费（31 次 `observe` 全部成功、0 次无效果，但每个计费轮的 prompt 均值
  **5,342** token、中位 4,360、p95 9,499、最大 10,136 —— 实测自 `episodes/*/model_calls.jsonl` 的
  1,108 条带 `usage` 记录），所以"没去查"可能是成本理性的；另外 3 轮窗口内常常已经有那句话，查了是浪费。
- **最小对照**：同 task_id 两条件——(a) 现状；(b) 在 `world` 里显式给出
  "本轮缺失的谓词清单 + 哪个技能能补上它"（只给可得性提示，不给答案）。
  主指标：**在真正缺信息时选择观察类技能的比率**（分母 = 缺信息的轮，由 §9.3 的证据等级判定），
  以及该选择之后 3 轮内的命令成功率。
- **预期验证方式**：先从本批次 268 集里离线挑出"缺信息轮"（`OBSERVATION_LIMITATION` 且
  后续轮才出现所需句子），量级约 224 行；只在这些轮上做两条件对照，避免整批重跑。

### 9.3 保留候选：决策粒度与程序组合（成本侧）

- **证据**：completion 只占 token 的 1.96%；每集均值 22,525 token 里 98.79% 的墙钟是等模型；
  成功路径其实只有 3–4 条命令（§6）。若 `take→go→move` 能一次提交，成本结构会变。
- **为什么只是保留**：§11.1 明令"先量化成本与成功收益，不能预先实现宏动作"。
  当前成功率 2.24%，**收益无法测量**——在一个几乎不成功的被试上谈"少花 token 地完成任务"
  没有分母。必须先有 §9.0 与 §9.1 的结果。
- **必要对照**：同一 task_id 下"一步一命令" vs "把环境已保证必然成功的前缀合成一个宏"，
  主指标是**每成功集的 token**，不是每集 token。

### 9.4 暂不选择的方向（以及为什么）

| 方向 | 暂不选择的理由 |
|---|---|
| 引入 VLM / 真实视觉 | 本后端没有任何视觉证据；§11.1 禁止用文本主实验推出该结论 |
| Skill acquisition / 新动词 | 16 技能已覆盖全部成功路径；`env_rejected_per_command = 0 / 611` |
| 跨任务 Memory | 无对照、无跨集证据；§11.2 禁止宣布其有效 |
| 更长历史 / 更大窗口 | P1 的教训正是"先修接口再谈能力"；把窗口从 3 调到 10 会同时改变两个变量 |
| 多 Agent、任务图、显式规划器 | 冻结机制里"一轮一个技能"是被测对象；Runtime 替模型做策略选择违反 v0.1 约束 |
| 降低放弃率（惩罚 blocked/clarify） | 那是改分数门槛；§11.2 与"事后不调低门槛"直接冲突 |

---

### 附：本文件的数字来源清单

| 数字 | 产物 |
|---|---|
| 6 / 268、CI、macro、分类型表、成本、延迟、终止结构 | `/tmp/bst_v01/full/metrics.json` |
| 48 集对照数 | `/tmp/bst_v01/screen/metrics.json` |
| 标签交叉表、首次偏离、按标签成本、二次复核 | `/tmp/bst_v01/annotations/annotations_full.jsonl` + `scratch/report_stats.py` |
| §2.2 命令漏斗的"发出过 / 引擎报出效果"两列、56 集 / 58 轮持物回落 | `scratch/funnel_and_held.py`（只读，遍历 `episodes/*/events.jsonl`；**`metrics.json` 里没有漏斗字段**，这两列是本脚本直测） |
| 键位普查、未公开引用、持物窗口、finish 扫描、形类普查 | 同上文件的 header 字段（`refused_key_placement_*`、`unpublic_*`、`holding_window_scan`、`finish_scan`、`command_form_outcomes_in_segment`） |
| 被试身份、预算、哈希、平台、续跑账本 | `/tmp/bst_v01/full/manifest.json`、`frozen_config.json`、`progress.json` |
| 逐轮文本案例 | `work/text_trace.py` 读 `episodes/<slot>/events.jsonl` |
| 偏离编号 | `docs/bst/compatibility_delta.md` D22–D40 |
