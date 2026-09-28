# Benchmark-Stress-Test v0.1 Agent · 阶段执行记录

规格：`Benchmark-Stress-Test-v0.1-Agent-SPEC.md`（SPEC-BST）。本文件按 §12 的 Phase 0 → Phase 5
顺序记录每阶段的**修改内容 / 测试结果 / 与 SPEC 的对应 / 剩余问题**。兼容性改动本身不写在这里，
只在 `compatibility_delta.md` 落编号条目（SPEC-BST §3.3 把它定为唯一落点）。

运行环境（两个解释器，职责不同，见 `compatibility_delta.md` 第 6 节）：

```bash
# 文本后端：跑 live 测试、真实 env 探针、实验
PYTHONPATH=. /home/czx/bstvenv/bin/python -m pytest tests/integration/test_bst_alfworld_live.py -q

# 桌面回归：跑 v0.1 契约与决策回归（无 textworld，live 文件按名 skip）
PYTHONPATH=. /home/czx/miniforge3/envs/embodied/bin/python -m pytest tests/ -q
```

代码身份：commit `4e960a2`＋脏工作树，`rules_sha256 = 23abe58f…aae7b3`（与 v0.1 冻结预注册相等）。

---

## Phase 0：基准接入与可复现性 —— 已完成

### 1. 修改内容

- 新增 `embodied_agent/benchmark/`（12 个模块，全部在桌面路径之外）：`envcheck.py`、`probe.py`、
  `tasks.py`、`backend.py`、`native_actions.py`、`parser.py`、`state.py`、`prompts.py`、`source.py`、
  `executor.py`、`runtime_alfred.py`、`__init__.py`。
- 核心侧只做接缝与可选字段，不改机制：条目见 `compatibility_delta.md` D6/D8/D9/D10/D12/D13/D14/D15/D17/D20。
- 安装一个不含 torch/ai2thor/CUDA 的独立环境 `/home/czx/bstvenv`（Python 3.11.16），
  并把 ALFWorld 数据下到 `~/.cache/alfworld`（两个官方 zip，摘要见 `environment_check.json → data.archive_manifest`）。
- 记录用脚本全部留在仓库外（`/tmp/bst_v01/`）：`baseline_inventory.py`、`fetch_alfworld_data.py`、
  `expert_trace.py`、`full_obs_probe.py`、`refusal_probe.py`、`parser_coverage.py`、
  `make_expert_fixture.py`、`runtime_smoke.py`、`win_smoke.py`。它们产生证据，不参与运行时。

### 2. 测试结果（全部为实测，无一项外推）

**(a) 依赖与主机**（`environment_check.json`）：alfworld 0.4.2 / textworld 1.6.2 / tatsu 5.8.3 /
jericho 3.3.1 / fast_downward_textworld 20.6.4 / numpy 2.4.6 / spacy 3.8.16 / pytest 9.1.1 /
pydantic 2.13.5；`pip_freeze` 57 行，摘要 `8d3d5c5c…`；java 未安装（文本后端不需要）；`gpu_used=false`。

**(b) 许可证记录**（同一文件的 `licenses`）：alfworld MIT、textworld MIT、spacy MIT、
tatsu BSD、jericho GPLv2+、fast_downward_textworld GPLv3（求解器二进制，仅本地执行）、
numpy/pydantic/pytest（元数据未声明 classifier）、pybullet zlib/libpng（仅桌面后端使用）。
游戏数据两个 archive 按 url+sha256+bytes 定位，条款以上游该 release 声明为准。

**(c) 集合交叉核查**：官方 collector 与磁盘枚举一致——
train 3553、valid_seen(eval_in_distribution) 140、valid_unseen(eval_out_of_distribution) 134；
六类分布 `pick_and_place_simple 24 / look_at_obj_in_light 18 / pick_clean 31 / pick_heat 23 /
pick_cool 21 / pick_two 17`（valid_unseen）；`ids_match=true`，两侧独有集合均为空。
每个 game 记 `game_tw_pddl_sha256` 与 `traj_data_sha256`。

**(d) 真实 playthrough 与确定性**：reset 1.95 s；初始观察摘要 `5f3670ed…`；同一 game 三次 reset
文本摘要相同（`deterministic_reset_text=true`）、`hard_errors=0`、`reward_was_none=true`
（引擎不在公开通道给奖励）；reset 时 admissible commands 28 条（只用于普查，从不进 prompt）。

**(e) 命令与消息形状普查**（`env_probe.json`，dev-only，永不进 prompt/Feedback/DecisionContext）：
72 个 game、13,865 条 admissible 命令、989 s、`engine_errors=0`、随机策略 4 胜。
出现的动词框架：`go to / open / close / take … from … / move … to … / use / heat·cool·clean … with …
/ examine / look / inventory`，**没有** `put … in/on …`——这决定了放置技能叫 `alfred_move`（D3）。
高频回复形状含 `You open the <cabinet>. The <cabinet> is open. In it, you see nothing.`（961 次）。
样本里唯一的 `slice <bread> with <butterknife>`（1 次）不进目录，理由写在 `native_actions.py` 模块头。

**(f) 引擎拒绝词汇探针**（`refusal_probe.json`，6 条记录）：非法前提得到的是
`Nothing happens.`；空手时 `You are not carrying anything.`；空容器 `On the cabinet 1, you see nothing.`。
这三句是 D6"语义前提交还环境"能成立的前提：环境确实会用可解析的原话回答不成立的命令。

**(g) 官方参考解预算紧度**（`expert_trace.py` → fixture）：`handcoded` 参考解跑 12 个 dev game，
9 条在 60 步内到达 `done`（0 条内部 `help` 查询，共 104 条动作），**3 条记到 60 步仍未 `done`**。
前者固化为 `tests/fixtures/bst_alfworld_expert_replay.json`（逐 game 记 `game_tw_pddl_sha256`），
后者只登记不运行（D19）。

**(h) 解析器覆盖率**（`parser_coverage.py`）：1,202 份完整文本（专家轨迹 296 / 全量观察探针 852 /
引擎拒绝 54；43 条被截断的探针样本跳过），**含未读句子的文档数 0，未读句子去重后 0**。
注意：该指标只看"有没有句子没被任何规则匹配"，看不见"匹配了但绑定错"——后者由第 5 节的缺陷 2 暴露。

**(i) 任务清单草案**（`task_manifest_draft.json`）：`selection_seed=order_seed=20260919`，
run seeds `20260919 / 20260920`，repeats 2；task_id = split 下 game 目录相对路径；
dev_train 12 任务 / 24 slot / 六类齐全；筛查按 SHA-256 每类 top-4 ⇒ 24 任务 / 48 slot，`shortfall={}`；
全量 134 × 2 = 268 slot，顺序规则"SHA-256(seed|order|task_id|repeat) 升序，筛查段在前且不重跑"。
数据漂移处理：live 测试对逐 game 摘要做 `_game()` 校验，不一致时**按名 skip**（基础设施原因），不判红。

**(j) 桌面回归与基线**（§13-8）：`355 passed, 20 skipped in 132.81s`（20 个 skip 全部来自 live 文件在
无 textworld 解释器里的按名跳过）。基线清点（`baseline_inventory.json`）：8,578 个源文件归档、
`git_tree_object=cfa532913ebd1d7be62071a64093b3bb615223ec`、`n_skipped=0`。

### 3. 与 SPEC 的对应

§12 Phase 0 的退出条件：环境可安装并自检、数据可定位且版本被记录、六类任务与 valid_unseen 划分被枚举、
公开/评估通道分清、真实样例可跑通、兼容性变化有清单——逐项对应上面 (a)(c)(d)(e)(g)(i) 与
`compatibility_delta.md` D1–D5。`environment_check.json → exit_criteria` 四项均为 true，`errors=[]`。

### 4. 剩余问题

1. 数据许可的**再分发**范围只按上游 release 声明记录，未做人工法务判断；run root 只发布摘要与
   game 路径，不发布 game 文件本身。
2. 参考解在 3/12 dev game 上超出 60 步 ⇒ 该预算对某些任务确实偏紧，Phase 2 若调整只能调一次并公示。
3. 解析器规则表来自 72 个 dev game 的观察；`valid_unseen` 上若出现新句式，`parse_status=unparsed`
   会先于任何崩溃暴露它（设计上可接受，但需在 Phase 3 统计 unparsed 比例）。

---

## Phase 1：Agent 与环境对接 + 最小验证 —— 已完成

### 1. 修改内容

- **1a 后端**：`backend.py`（reset/step/关闭/命令超时/`eval_view()` 隔离读官方结果与故障）、
  `native_actions.py`（12 条命令目录 + `catalogue_sha256`）、`parser.py`（文本 → `TextFact`，
  锚点/指代分层）、`state.py`（快照、指纹、diff、`TextGoalSpec`、`TextVerifier`）、
  `prompts.py`（`bst-decide-text-v1` 与 `TextDecisionContext`）、`source.py`（复用共享 HTTP client
  的决策源，`max_tokens=min(配置, 2048)`）、`executor.py`（一条 SkillCall ⇒ 恰好一次 `env.step`）。
- **1b 接入**：`runtime_alfred.py` 覆写 11 个接缝，实现 finish/done 双协议、终止名、保守预检、
  密封记账（`termination`、`finish_check`、`terminal_status_restated`、`backend_fault_detail`、
  `episode_official`）。**没有**第二套决策循环、**没有** Adapter 侧策略。
- **1c 验证**：4 个测试文件 + 1 个 fixture。

### 2. 测试结果

| 套件 | 解释器 | 结果 |
|---|---|---|
| `tests/contract/test_bst_text_backend.py` | bstvenv | 31 passed |
| `tests/unit/test_bst_native_actions.py` | bstvenv | 55 passed |
| `tests/unit/test_bst_parser.py` | bstvenv | 28 passed |
| 三文件合计 | bstvenv | **114 passed in 0.32s**（解析器重排当次的实测是 113；第 31 项 contract 测试是本记录写完后针对"剩余问题 7"补的） |
| `tests/integration/test_bst_alfworld_live.py`（真实 env） | bstvenv | **18 passed in 44.75s** |
| 同一 live 文件 | embodied（无 textworld） | 按名 skip，不计通过 |
| 全仓回归 | embodied | **356 passed, 20 skipped in 131.22s**（含新增的 1 项 contract 测试；解析器重排前为 355/20） |
| 7 场景 runtime smoke（真实 game） | bstvenv | `FAILS: none` |
| 9 条官方获胜轨迹重放 | bstvenv | `mismatches: none` |

SPEC-BST §13 九项验证的落点：

| # | 要求 | 证据 |
|---|---|---|
| 1 | 动作不被代做 | contract `test_the_adapter_sends_nothing_the_model_did_not_name`、`test_a_name_the_model_invented_is_answered_by_the_engine_not_repaired`、`test_a_desktop_verb_has_no_text_equivalent` |
| 2 | 一步一动作 | contract `test_one_legal_execute_is_exactly_one_environment_step`（内含 `alfred_go`、`alfred_inventory`、`alfred_move` 三条 ⇒ 三条命令、`env_steps==skill_calls==3`、`decision_rounds==4`）、`test_a_semantic_failure_still_costs_exactly_one_step`、`test_reading_the_cached_response_takes_no_step_and_invents_no_ref`；live `test_one_legal_skill_call_is_exactly_one_real_environment_step`。9 条重放共 104 次调用，覆盖 12 个技能中的 10 个（`alfred_go` 63、`observe` 12、`alfred_take` 9、`alfred_move` 7、`alfred_open` 4、`alfred_use`/`alfred_close`/`alfred_clean`/`alfred_cool` 各 2、`alfred_heat` 1）。`alfred_examine` **没有**任何 step 级证据（见"剩余问题 6"），只有 `test_every_catalogued_skill_renders_exactly_one_command` 的"一条技能恰好一条命令" |
| 3 | 信息隔离（带标记） | contract `test_evaluation_only_values_never_reach_the_decision_chain`（`MARKER` 注入 `eval_extra`，断言 payload / feedback / 模型可见事件均不含，只允许出现在终止记录）；live `test_a_real_payload_carries_no_privileged_and_no_geometry_key` |
| 4 | 无自动 Memory | contract `test_the_only_history_is_the_frozen_window`、`test_a_fact_stops_being_current_when_the_text_stops_stating_it`（看见→离开→局部消息序列）；parser `test_a_second_snapshot_remembers_nothing_from_the_first`、`test_every_fact_carries_the_ref_of_the_snapshot_that_stated_it` |
| 5 | unknown 保真 | contract `test_no_holding_report_is_not_an_empty_hand`、`test_unread_text_is_kept_verbatim_and_labelled`、`test_a_response_no_rule_reads_is_unparsed_not_empty`；parser `test_an_anaphora_with_no_anchor_stays_unknown_rather_than_guessing` |
| 6 | 终止不泄漏 | contract `test_the_environment_done_and_the_agent_finish_are_different_records`、`test_an_agent_finish_ends_the_episode_without_another_request`；live `test_an_agent_finish_on_a_real_game_is_the_agent_s_own_word` |
| 7 | 账本完整 | contract `test_every_cost_counter_survives_into_the_result`、`test_a_schema_error_costs_a_decision_and_no_environment_step`、`test_a_transport_error_is_a_model_error_and_is_billed`、`test_the_http_cap_binds_before_the_request_that_would_pass_it`、`test_the_episode_token_cap_uses_a_pre_request_estimate`、`test_an_environment_fault_ends_the_episode_without_replaying_the_command` |
| 8 | 隔离回归 | 桌面 `356 passed, 20 skipped`；`rules_sha256` 与 v0.1 冻结文件逐字节相等；I1/I2/I3/I4 不变式通过 |
| 9 | 离线可重算 | **未开始**——聚合器属 Phase 4 工具，见"剩余问题" |

### 3. 本轮发现并修掉的两个真实缺陷

1. `false_finish` 归账错误（截断/ blocked 集被标成 agent finish）。修法与测试见 D5 与 `compatibility_delta.md` 第 5 节。
2. 解析器锚点顺序缺陷：`contents_in` 排在建立锚点的规则之前，使本环境最高频的回复形状把容器绑定成
   `unknown`。覆盖率指标看不见它（句子"被读了"），是新增的顺序守卫测试
   `test_no_rule_that_only_sets_an_anchor_is_listed_after_one_that_reads_it` 把它钉住的。
   重排后离线 114、live 18、两个 smoke、覆盖率 1,202/0 全部重跑通过。

### 4. 与 SPEC 的对应

§12 Phase 1 的退出条件："公开观察→WorldState→Feedback 闭环可用；一条动作恰好一步；信息隔离与终止
协议有测试"——上表 1–7、9(除重算) 即其证据。§3.5 的禁止清单由 contract 测试逐条对着实现：
不替换对象、不补动作、不合成命令、不自动重试、不读 LLM 于 Adapter、不写专家/隐藏信息、不静默改写。

### 5. 剩余问题（Phase 2 起点）

1. **§13-9 离线重算**尚无实现：`aggregate.py` 需在 Phase 4 交付，并证明从密封日志重算的
   `metrics.json` 与集内 `episode_summary.json` 一致。
2. `cli.py` 的 benchmark 入口（环境自检 / 12 集开发校验 / 48 集筛查 / 全量续跑 / 离线聚合）未落地，
   §12 要求每条命令写明配置文件与输出目录 ⇒ Phase 2 第一件事。
3. Phase 2 配置文件需显式 `max_tokens: 2048`（现 `configs/models/agnes.yaml` 为 1200，取 min 后偏严）。
4. 模型身份守卫（`returned_models` 异常或中途变化 ⇒ 暂停调度）与暂停/续跑账本在 Phase 3。
5. run root 目录树（§10.4）目前只在替代布局下成立（机器产物 `/tmp/bst_v01/`，人类交付物 `docs/bst/`，
   冻结时复制进 run root）；`manifest.json`/`frozen_config.json` 待 Phase 2 生成。
6. `alfred_examine` 只有渲染层证据：Phase 0 的 13,865 条 admissible 命令里 `examine …` 存在，
   但随机策略从未发出它，因此**没有采到它的回复句式**。没有句式的后果是不写测试桩（不猜测环境文本），
   该技能若被真实批次使用，其解析结果属于未测面——`parse_status` 与 `unparsed` 会在产物里暴露它，
   而不是被静默当成"没有信息"。
7. 引擎自带 `help` 命令会打印该 game 的合法动作全集（探针实测：`Available commands: look … inventory …
   go to (receptacle) …`）。这是本后端唯一已知的"公开通道里出现隐藏信息"的入口，处置方式有两条：
   `help` **不在**目录内（模型无法把它作为技能发出：`execute.skill` 不在冻结 `SkillName` 里，拒绝理由逐条列出目录中的合法名字），并且
   `parser.RULES` 的 `command_list` 规则把这类自描述文本归成单条 `self_description` 事实、
   不生成实体（`test_the_engine_describing_itself_is_not_a_fact_about_the_world`）。
   本条记录后补了一条直接针对该入口的测试
   `test_the_environments_own_help_command_cannot_be_asked_for`：以 `skill="help"` 走真实 binder
   ⇒ `DecisionSchemaError`（`execute.skill` 不在冻结 `SkillName` 里），episode 未发出任何命令，
   payload 中不含 `Available commands`。
   Phase 3 仍需统计真实批次里 `self_description` 的出现次数，确认它没有被任何路径产生。

---

## Phase 2：单任务校验 + 六类 train 开发校验 + 预算预检 —— 已完成

### 1. 修改内容

兼容性条目一律只写进 `compatibility_delta.md`（括号里是它的编号），本节只记"做了什么、测到什么"。

- **运行入口** `benchmark/cli.py`：`env-check` / `tasks` / `run --segment dev|screening|full` /
  `aggregate`，段与 token 硬顶是代码常量（`screening` 8,000,000、`full` 40,000,000）。§12 要求的
  五条命令从此有真实可执行形式（D23）。
- **批次层** `benchmark/runner.py`：`run_batch` 的顺序 = 冻结顺序、并发 1、断点续跑账本
  （`resumable_complete` 才跳过）、token 硬顶在**开工前**用实际计费量判定、模型身份守卫
  （`identity_drift` ⇒ `MODEL_IDENTITY_DRIFT` + `BatchHalt`）、基础设施失败的**一次**替换、
  `_infra_summary` 用计数器增量记账（D25）、`_isolate_prior_attempt` 保证"一个目录 = 一次尝试"（D26）。
- **离线聚合** `benchmark/aggregate.py`：§8.1 五个分母 + 四个头条率、§8.2 指标族、§8.3 精确重复与
  信息类别、§8.4 按 task_id 的 cluster bootstrap（2,000 次、种子 20260919）、micro/macro 分开、
  `not_supported` 显式列出（partial completion / constraint violation / progress retention /
  `replan_events`）。§13-9 从此有了实现（D24）。
- **任务清单** `benchmark/tasks.py`：三段 slot 全量展开并封进 `task_manifest.json`；
  `DEV_REPEATS = 1` ⇒ dev 段是 12 个不同 train 任务各 1 次（六类各 2 个），不是 6 任务×2（D27）。
- **被试配置** `configs/models/bst_text_agnes.yaml`：`max_tokens: 2048`（D29）、
  `max_retries: 4`（D28 = §5.2 允许的唯一一次预检调整，理由是可测的端点 429 而不是任何分数）。
- **一处日志形状** `evaluation/sources.py`：被拒决策的行补上 `usage`（D24）。不在 `rules_sha256`
  哈希集合内，改前改后实测 `23abe58f…aae7b3` 逐字节不变。

### 2. 测试结果

套件（D24/D25/D26 之后重跑）：

| 套件 | 解释器 | 结果 |
|---|---|---|
| `tests/unit tests/contract tests/integration`（不含 live） | embodied 桌面 | **351 passed in 138.71s** |
| 同一选集 | bstvenv | **350 passed, 1 deselected in 137.67s**（被 deselect 的是桌面依赖断言，要求 `Pillow`；见 D27） |
| `tests/integration/test_bst_alfworld_live.py` | bstvenv | **18 passed in 44.65s** |
| 新增 `tests/unit/test_bst_batch_control.py` | bstvenv / 桌面 | **7 passed** / 1 skipped（`runner` 需要 textworld） |

dev 段真实批次跑了两轮，第二轮是干净的那轮：

| 批次 | `max_retries` | 集数 | benchmark-valid | 官方胜 | 基础设施失败 | token | 请求 | 决策轮 | env 步 |
|---|---|---|---|---|---|---|---|---|---|
| `/tmp/bst_v01/dev` | 2 | 12 | 12 | 0 | 0 | 276,614 | — | — | — |
| `/tmp/bst_v01/dev2` | 4 | 12 | 12 | 0 | 0 | **276,434** | **46** | **39** | **17** |

`dev2` 的 `metrics.json`（全部可从密封日志重算）：

- 分母（§8.1）：planned 12 / started 12 / benchmark-valid 12 / infrastructure-failed 0 /
  not-run 0 / replaced 0；四个头条率 = task success **0.0**、running reliability **1.0**、
  conservative delivery **0.0**、coverage **1.0**。
- 终止分布：`NEEDS_CLARIFICATION` 6、`BUDGET_EXHAUSTED` 3、`AGENT_BLOCKED` 3；
  `false_finish` **0**、`env_done_without_agent_finish` 0、`agent_finish_without_env_done` 0。
- 效率：env 步 mean 1.42（max 3）、决策轮 mean 3.25（max 6）、请求 mean 3.83（max 12）、
  被拒决策合计 12、`env_no_effect` 2、`distinct_commands` 与 `longest_stuck_repeat` 均 <3。
- 成本（以封存账本为准，见 D24/D25）：prompt 272,057 + completion 4,377 = **276,434**；
  每集 total mean **23,036**、median 19,105、p95 34,018、max **42,611**、min 11,382。
- 端点侧计数：`transport_retries` 7、`api_errors` 1、`model_errors` 1（全部集中在 slot005：
  6 轮决策发了 12 个请求）、`format_repairs` 0、`semantic_repairs` 12（=被拒决策数）。
- 墙钟：`wall_time_s` mean 8.9 s、max 38.5 s（`metrics.wall_clock_s` 是事件日志跨度，mean 7.29 s，
  比它小是正常的：前者含后端 reset 与解析）。
- 交叉核对：`sealed_tokens_total = 276,434 = ledger_tokens_used = process_counter_at_batch_end`，
  逐集 provider 累计序列差分 **12/12 相符**；行求和 163,399 是**下界**，差异只出现在 6 个
  "被计费但当时未记 `usage`" 的 episode（D24 修复前的形状，保留不掩盖）。

### 3. 预算预检结论（§5.3）

以 dev2 的每集分布外推，只用于安排，不进入任何分母：

| 段 | 集数 | 按 mean 23,036 | 按 max 42,611 | 硬顶 | 结论 |
|---|---|---|---|---|---|
| screening | 48 | ≈ 1.11 M | ≈ 2.05 M | 8 M | 最坏情形约 25% 占用，不需要抽稀样本 |
| full | 268 | ≈ 6.17 M | ≈ 11.4 M | 40 M | 最坏情形约 29% 占用 |

每集层面：最大观测 42,611 tokens / 12 请求 / 6 决策轮，对上 600,000 / 90 / 80 的上限，
余量分别约 14×、7.5×、13×。**真正会先绑住的仍是每集 90 次请求**（429 突发时退避会吃掉它），
所以 §5.3 的保守预检按请求逐次判，不按批均值判。超额只报告，不扩预算。

### 4. 本轮发现并修掉的三个真实缺陷

1. **被计费的请求在日志里没有数额**（D24）：错误路径的 `model_calls.jsonl` 行没有 `usage`，
   使 §13-9 的离线重算**永远低于**封存值 41%（163,399 / 276,434）。修法有两半：给错误行补
   `usage`（此后两路应当相等）；同时增加一条不依赖行的重算路（provider 累计序列差分），
   它当场给出 12/12 相符，因此"哪个数字是权威"不是选择而是被证明。
2. **成本统计取错了基**（同一轮的自查）：`by_task_type`/`overall` 的 mean/median/p95 曾按行求和
   计算，于是 `pick_cool` 的均值报成 7,487 而实测 32,590，两集报成 0。改指 `cost_tokens`
   后全表重算，`sum` 与批次账本相等。
3. **一次重跑把两次尝试拼进一个 `events.jsonl`**（D26）：`--single 7` 之后再跑批次，slot007
   的目录里出现两个 `termination_reason`，聚合器把它读成一集。结构性修法 + 7 项新测试；
   被污染的那份保全在 `superseded/`，dev 段整个重跑成 `dev2`。

### 5. 与 SPEC 的对应

§12 Phase 2 的退出条件是"单任务闭环 + 六类各若干集真实跑通 + 预算预检 + 发布冻结配置"：
单任务闭环在 Phase 1 的 live 与 1 集真实端点闭环上已成立；六类各 2 个 train 任务在 `dev2` 各跑 1 次
（`by_task_type` 六类 `episodes=2`）；预算预检即第 3 节；冻结配置在筛查发车前 `freeze_run_root`
生成 `manifest.json`/`frozen_config.json`/`compatibility_delta.md` 副本。§5.2 的"只能调整一次"
由 D28 兑现且只用掉一次；§8.1 五分母与 §13-9 双路重算在 `metrics.json` 里落地。

### 6. 剩余问题（Phase 3 起点）

1. **0 胜率不做归因**（归因是 Phase 4 的 `annotate.py` + 盲评）。但分布已知：3 个
   `BUDGET_EXHAUSTED` 全部是 `semantic_repairs` 用尽，模型把 `candidate_id`/`subgoal` 写在
   决策**顶层**而不是 `execute` 内。**提示词与阈值都不改**——那是"看到结果后改题目"；
   这批 episode 作为 TASK_UNDERSTANDING / 格式类候选进入标注。
2. `dev2` 的 6 集 token 下界差异按 D24 原样保留；筛查批次起，"行求和 ≠ 封存值"应当只发生在
   没有摘要的 episode，若否则按缺陷处理而不是找解释。
3. `/tmp/bst_v01/dev`（第一轮，`max_retries=2`）字段含义与新批次不同（D25 之前），只作对照，
   不并入任何分母。
4. `alfred_examine` 在 dev2 里被真实发出了 **1 次**（slot001，`target_id="newspaper 1"`，位置
   `sidetable 1`），引擎回的是 **"Nothing happens."** ⇒ 这条技能第一次有了真实 step 级证据，
   但仍然**没有**出现过一次"描述性"回复，所以它的句式解析面依旧是未测的（Phase 1 剩余问题 6
   的同一件事，只是现在有了证据说明"发得出、引擎会答"）。筛查/全量批次会把它的次数与
   回复形状一并统计出来。
5. 17 步的实际技能分布：`alfred_go` 9、`alfred_open` 3、`observe` 2、`alfred_take` 1、
   `alfred_use` 1、`alfred_examine` 1 —— 12 个技能里被真实使用 6 个。`alfred_close`、
   `alfred_clean`、`alfred_cool`、`alfred_heat`、`alfred_put`、`alfred_inventory` 在本段
   没有出现（多数 episode 在 2–3 轮内就自行 clarify/blocked，走不到那些动作）。
6. 筛查/全量发车前还需一次 `env-check` 重生成 `environment_check.json`（`packages` 里现在
   含 pyyaml 6.0.3，旧副本记的是没有它的那次）。→ **已做**：`checked_at = 2026-09-20T03:21:34+0800`
   那一次，`packages` 含 `yaml 6.0.3`、`errors: []`；同一份按 run root 各存一份
   （`screen/environment_check.json`、`full/environment_check.json`，两份**逐字节相同**）。

---

## Phase 3：冻结配置 → 筛查批次 → 全量批次 —— 已完成

### 1. 修改内容

- 冻结入口 `benchmark/cli.py run --prereg …`：发车前生成 `<run_root>/{manifest.json,
  frozen_config.json, environment_check.json, task_manifest.json, compatibility_delta.md}`，
  其中 `manifest.json` 记 commit、脏 diff、`rules/catalogue/prompts` 三个哈希、依赖版本、预算、
  时间表与 `credentials: "not recorded (SPEC 7: no credentials or full env)"`。
- 新增 `benchmark/runner.py` 的批次控制面：`resumable_complete` 跳过、开工前按实际计费量判
  8 M / 40 M cap、基础设施失败最多一次替换、`identity_drift` → `MODEL_IDENTITY_DRIFT` → `BatchHalt`。
- 新增 `benchmark/aggregate.py`：§8.1 五分母、§8.2 终止/成本分离、§8.3 重复定义、§8.4 任务级
  bootstrap、§13-9 双路重算（行求和 + provider 累计序列差分）。
- 文本批次的命令入口统一为 `python -m embodied_agent.benchmark.cli`（D23：包里刻意没有
  `__main__.py`）。模型端点换 `configs/models/bst_text_agnes.yaml`（`provider: agnes`、
  `agnes-2.5-flash`、`max_tokens: 2048`，D29/D17）；旧 `deepseek.yaml`、`agnes.yaml` 一字未改。
- §5.2 允许的"只调一次"用掉在 `max_retries 2 → 4`（D28），此后不再按结果改动任何预算、阈值或提示词。

### 2. 测试结果（筛查 48 集 → 全量 268 集，全部实测）

| 项 | 筛查 `screen/` | 全量 `full/` |
|---|---|---|
| slots / episode 目录 / benchmark-valid | 48 / 48 / 48 | 268 / 268 / 268 |
| 官方成功 | **0** | **6**（`task_success_rate 0.0224`，任务级 95% CI `[0.0037, 0.0485]`，134 簇 bootstrap、seed 20260919） |
| 终止分布 | BLOCKED 17 / CLARIFY 20 / FINISH 2 / BUDGET 9 | BLOCKED 110 / CLARIFY 94 / BUDGET 41 / FINISH 17 / ENV_TERMINATED 6 |
| `false_finish` | 2 | **17**（且 `agent_finish_without_env_done = 17`、`env_done_without_agent_finish = 6`） |
| 决策轮 / HTTP / 环境步 / 下发命令 | 215 / 312 / 114 / 114 | 1,133 / 1,673 / 611 / 611 |
| 引擎因语法拒绝的命令 | 0 / 114 | **0 / 611**（`env_rejected_per_command 0.0` ⇒ §9.2 的 `EXECUTION_FAILURE` 在本批无来源） |
| 缓存观测读数 | 263 | 1,401 |
| 本地拒收轮 | 53 | 276（原因实例：`subgoal` 204、`candidate_id` 196、`$` 32、`action` 10、binder 7…） |
| 模型错误（传输失败） | 9 条 `LLMError: request failed after 5 attempts` | 25 条同（两段各自等于标注里 `INFRASTRUCTURE` 的行数 9 / 25，且**全部留在分母**） |
| tokens（封存 = 行求和 = 账本） | 1,162,821 | 6,036,680（cap 40,000,000 的 15.1%） |
| 集内墙钟合计 / 其中等模型 | 572.07 s / 564.77 s = 98.7% | **3,119.51 s / 3,081.88 s = 98.8%** |
| provider 累计序列核对 | **48/48 集全符** | **267/268 集全符**，唯一不符是续跑接缝 slot048（差 −1,149,098 = 13,723 − 1,162,821，正是 `_series_check.assumes` 预告的形状） |
| `infrastructure.episodes` | `[]` | `[]`（0 集被剔除；`replaced_attempts 0`） |
| 模型身份 | 返回名只有 `agnes-2.5-flash`；2 集没有返回名（该集没有成功请求） | 同；8 集没有返回名，`MODEL_IDENTITY_DRIFT` 未触发 |

按任务类型（全量，micro 0.0224 / macro 0.0266）：

| 类型 | 集 | 赢 | 成功率 |
|---|---|---|---|
| `look_at_obj_in_light` | 36 | **5** | 0.1389 |
| `pick_and_place_simple` | 48 | 1 | 0.0208 |
| `pick_clean_then_place_in_recep` | 62 | 0 | 0.0 |
| `pick_cool_then_place_in_recep` | 42 | 0 | 0.0 |
| `pick_heat_then_place_in_recep` | 46 | 0 | 0.0 |
| `pick_two_obj_and_place` | 34 | 0 | 0.0 |

六类里只有两类产生过官方成功；`pick_cool` 的 BLOCKED 比例最高（28/42）。Phase 2 剩余问题 4
里"`examine` 的句式解析面未测"在全量批次后有了答案，见 §Phase 4 第 4 节与 D40。

### 3. 与 SPEC 的对应

§5.2（一次预算调整）、§5.3（2,048 单次生成上限 + 保守预检）、§5.5（模型身份守卫）、
§7（manifest 记哪些、不记凭证）、§8.1–8.4（分母、成本分离、重复定义、任务级 CI）、§10.4
（哈希链路）、§12 的命令面、§13-1/-2/-9。逐条落点在 `compatibility_delta.md` 的 D23–D29、D24、
D26、D28、D29 与第 4 节"明确未变的机制"表。

### 4. 剩余问题（Phase 4 起点）

1. 6 个赢全部是"环境自己判定完成、agent 没有 finish"（`ENV_TERMINATED 6` 与
   `env_done_without_agent_finish 6` 同集），17 个 finish 全部是假的 ⇒ 完成率与"知道自己完成"
   是两件事，报告必须分开写。
2. `episode_id` 进了模型载荷（`prompts.py:95` + `runner.py:99-109` 的 `tail[-40:]`），全量批次的
   60 次不公开引用里 **18 次**正好等于该 id 的 `Class-N` 片段 ⇒ 这是可修的自身缺陷，不是模型问题。
3. 每轮 world 只持久化计数（`n_entities` / `held_object`），prompt 不落盘（只存
   `raw_response`），`_redact` 在 6 集屏蔽了 `task_id` ⇒ §10.2 的三条最小 trace 有缺口，报告要点名。
4. 筛查 0 赢、全量 6 赢 ⇒ 阈值不改（判据已冻结），筛查的作用只到"暴露形状"，不能当效果。

---

## Phase 3c / 3d：判据追加、筛查段标注与二次复核 —— 已完成

### 1. 修改内容

- `docs/bst/annotation_guidelines.md` 追加 §6.1–§6.5（纳入规则逐字对齐、遮蔽范围写到字段名、
  §3 表未盖住的三条判据、两条需补证方式的判据 + 两个只读探针、二次复核做了什么没做什么）。
  追加期内没有任何一条改动放宽标签；§6.1 是把 §10.3 的纳入规则**收紧**（只增不减）。
- 新增 `embodied_agent/benchmark/annotate.py`（`worklist` / `validate` 两个子命令，§9.3 最小证据
  是硬门槛）与仓库外标注器 `scratch/make_annotations.py`。
- 筛查段 48 集人工标注：106 行、6 类 claim、`annotate validate` 的 `invalid` 与 `unlabeled` 均空。
- §10.3 二次复核（筛查段）：seed 20260919、按 episode 抽 10/48 = 20.8%，24 行判决
  （19 held / 2 changed / 3 行是重读新加的）。

### 2. 测试结果

`scratch/{verify_absence,probe_near_miss,probe_no_effect_causes,probe_already_there,
probe_stop_structural}.py` 的探针结论全部落到具体行上；`reset` 只读探针覆盖 265 集，
且**没有**回流进任何 prompt / Feedback / DecisionContext（SPEC §4.5）。

### 3. 与 SPEC 的对应

§9.1（模型措辞只作辅助）、§9.2（标签表逐条具体化）、§9.3（最小证据 → `validate` 拒写）、
§10.3（≥20% 复核，单位是 episode）、§4.5（探针只读）。

### 4. 剩余问题

单标注者 ⇒ IRR 不可估计；这一条从筛查段一直带到全量段，见下。

---

## Phase 4：全量批次的聚合与 530 行标注 + 二次复核 —— 已完成

### 1. 修改内容

- 全量聚合：`metrics.json` / `metrics_by_task_type.csv` / `failure_candidates.jsonl` 由
  `aggregate.py` 从**封存日志**离线重算（不重跑任何模型或环境）。
- 全量标注：264 集 / 530 行（10 个标签、6 类 claim），停机轮的行改由**结构规则**生成
  （`BST_TERMINAL_RULE=1`：手工表盖不住 264 集），并把 `OBSERVATION_LIMITATION` 的 I0 断言
  改成可核对的"目标类在本集持久句子与 reset 里都不出现"。
- §10.3 全量二次复核：按 episode 抽 53/264 = 20.1%，122 行判决分 11 批读、
  `merge_verdicts.py` 合并（`still_unjudged_count 0`、stray 键致命）。新增第四种判决
  `wording_corrected`（D37）。
- 复核工具三处"通过但没检查"改成硬失败（D38）。
- 判据文件追加 §6.6、§6.7，两次重哈希并各跑一遍完整闭环（D39、D40）。

### 2. 测试结果

| 项 | 筛查段 | 全量段 |
|---|---|---|
| 标注行 / 集 | 106 / 48 | **530 / 264**（population 268；`episodes_sampled_without_a_row = [slot064]`） |
| claim 分布 | — | schema_refusal 148、final_decision 198、no_effect_round 71、unpublic_reference 47、truncation 41、transport_error 25 |
| 行标签计数 | OBS 42 / INVALID 32 / BUDGET 9 / INFRA 9 / AMBIG 6 / STATE_USAGE 3 / ADAPTER 3 / HISTORY 3 / UNRESOLVED 2 / REPEAT 1 | OBS 224 / INVALID 167 / BUDGET 41 / AMBIG 31 / INFRA 25 / ADAPTER 18 / UNRESOLVED 17 / STATE_USAGE 15 / HISTORY 7 / REPEAT 4；**`EXECUTION_FAILURE` 两段均为 0 行** |
| 二次复核 | 24 行判决：19 held / 2 changed / 3 added | **122 行判决：101 held / 4 changed / 1 added / 16 wording_corrected** |
| `annotate validate` | `invalid []`、`unlabeled []` | 同 |
| 判据哈希（头部一致） | `15e359d0…`（两段同一值） | 同 |
| 载荷可证性 | 建包时刻的判据**字节副本**留存：`annotations/guidelines_bytes_at_15e359d04a1f.md`，`sha256sum` 等于头部值 | 同 |
| 重哈希后的载荷核对 | 48/48 行集合相等 | 265/265 行集合相等 |

改判的 4 行（全部留住首遍标签）：slot009 r2 `HISTORY_UNAVAILABLE→INVALID_ACTION`、
slot025 r4 `[OBS,EXECUTION_FAILURE]→OBS`、slot032 r4 `OBS→STATE_AMBIGUOUS`、
slot067 r3 `OBS→[OBS,STATE_USAGE]`；新加 1 行 slot032 r4 `INVALID_ACTION`。

本轮在标注产物里查出并修掉的缺陷类（详见判据 §6.6 第 5 条与 D36、D40）：模板句号后小写拼接
（10 行 `reason`、0 标签）；形类计数把本地拒收当成引擎回话（10 条反馈、16 行 `reason`、0 标签，
且与 `metrics.json` 的 `episodes_where_identity_fails` **逐集相同**）；`verify_absence.py` 在 6 个被
屏蔽的 `task_id` 上空转；未判决行按 episode 计数把 122 读成 41；短柄判决文件的 20 个 stray 键。
**held 计数对句子层面的重写是盲的**：101 行 held 里有 28 行的证据句子被规则换过（29 行改前、2 行
另动），因此报告引用 held 率必须并列给出这两个数。

### 3. 与 SPEC 的对应

§9.2 标签表逐行落到 claim；§9.3 每行都有 `evidence_refs`；§10.2 的 trace 缺口以"报告点名 + §11
进 v0.2"的方式兑现（没有静默补数据）；§10.3 复核比例、单位、留分歧；§10.4 哈希链路两次自证；
§13-3/-4/-5/-10 由已跑绿的测试与本文的可核对数字共同覆盖。

### 4. 剩余问题（Phase 5 起点）

1. 报告本体（§10.5 九节）与 v0.2 映射（§11）还没写 ⇒ 任务 #32 / #33。
2. `EXECUTION_FAILURE` 在文本批次 0 行：要么给出能证伪"公开前提正确"的接口（→ 引擎回话带原因码），
   要么在报告里明写这一行未被使用。
3. 形类计数只能描述本段内部应答分布，报告引用时必须给出 `succeeded` 的定义（D40）。
4. 4 行已盖章判决的 note 引的是修正前的 301/43 与 4/10，**不改写**；引用它们必须并列修正值。

---

## Phase 5a：Bottleneck Report（§10.5 九节）—— 已完成

### 1. 修改内容

- 新增 `docs/bst/Bottleneck-Report.md`（603 行 / 40,270 字节），§10.5 要求的九节逐节落位，
  并在开头给出"重算入口"、结尾给出"数字出处"附录：文中每个数都能由 `metrics.json`、
  `annotations_full.jsonl`、`manifest.json` 或 `scratch/report_stats.py` 重新得到，
  **没有一个是再调用模型或再跑环境得到的**（§10.4 末句）。
- 新增 `work/text_trace.py`（dev-only，只读）：把一条封存 episode 按轮打印回"发出去的命令 +
  环境原话 + 判定"，报告 §6 的每条代表轨迹都注明了用它复现的命令行。
- 新增 `/tmp/bst_v01/scratch/report_stats.py`（证据工具，在 run root 内、仓库外）：从标注文件
  重算标签交叉表、claim 分布、confidence、evidence_basis、二次复核统计、首次偏离分布、
  按标签的 token 份额。它只读日志、不写日志。
- 报告按 §10.4 复制到 run root：`/tmp/bst_v01/full/Bottleneck-Report.md`。
- `.gitignore` 未改；`rules_sha256 = 23abe58f…aae7b3` 在新增上述脚本**之后**重新实测仍等于冻结值
  （新文件都在 `work/`、`docs/`、`/tmp` 下，不进入被测包），所以冻结批次依旧可判读。

### 2. 报告里给出的主要实测数字（全部来自封存产物）

| 项 | 值 | 出处 |
|---|---|---|
| 官方成功率（268 槽位） | **0.0224**，95% CI [0.0037, 0.0485]（6/268） | `metrics.json` |
| 六类型宏平均 | 0.0266 | `metrics_by_task_type.csv` |
| 累计 token | 6,036,680（两段进程续跑：seam −1,149,098；1,162,821 + 4,873,859） | `manifest.json` / 账本 |
| 轮预算利用 | 1.96% | `metrics.json` |
| HTTP/轮 | 1,673 / 1,133 = 1.48；模型等待占墙钟 98.79% | `metrics.json` |
| 终止方式 | **正确 finish 0/17**；6/6 成功集由环境自行终止判定 | `metrics.json::termination` |
| 命令漏斗 | `take` 发出 80 集（引擎确认 75）→ `move` 发出 9 集（引擎确认 **4**）；71 集拿了没发出 move；21 集试过 heat/cool/clean 但只有 4 集被引擎报出效果 | `scratch/funnel_and_held.py`（`metrics.json` 无漏斗字段） |
| `held_object` 跨轮回读 | 268 集中 **56 集（58 轮）**拿起后下一轮读回 `unknown` | 报告 §4 P2 的测量 |
| 标注覆盖 | 530 行 / 264 集（population 268） | `annotations_full.jsonl` |
| 二次复核 | 122 行 = **23.02% 行覆盖**、53 集 = 20.1% episode 覆盖（§10.3 单位是 episode） | 同上 + verdicts |
| §10.2 trace 缺口 | 4 项，报告 §2.6 明列（含第 1 轮请求文本未持久化） | 报告 §2.6 |

### 3. 与 SPEC 的对应

§10.5 第 1…9 条 ↔ 报告 §1…§9 一一对应（第 3 节明写"固定类型不是难度等级"；第 7 节三个排序
分开给、不合成总分；第 8 节列出未测试能力以标出结论边界）。
§10.3 的"关键结论所引用案例可定位"由 §6 的 episode 目录名 + `text_trace.py` 命令兑现。
§11.2 的三条禁令在报告里被当作硬约束执行：`EXECUTION_FAILURE` 0 行 ⇒ 不宣称引擎归因；
无 Memory 对照 ⇒ 不宣称记忆有效；文本批次 ⇒ 不出物理控制结论。

### 4. 剩余问题（Phase 5b 起点）

1. §11 证据映射文档（`v0.2-Decision-Evidence.md`）未写 ⇒ 任务 #33。
2. 报告 §9.0 的"先修接入缺陷"清单要落到 §11 的映射表里逐条给出验证路径，不能停在建议。
3. `EXECUTION_FAILURE` 需要接口层证据（引擎回话带原因码）才能被证伪，本轮仍为 0 行 ⇒ 进 v0.2 前置项。

### 5. 第二遍核对后的自我修正（写完后逐数回查产物，改了三处）

| 报告原写法 | 实测 | 处理 |
|---|---|---|
| "每轮约 20k prompt token"（§9.2） | `model_calls.jsonl` 的 1,108 条带 `usage` 记录：均值 **5,342**、中位 4,360、p95 9,499、最大 10,136 | 已替换为实测四个数并给出来源 |
| §2.2 "≥1 次成功 move ⇒ 真正放下过目标物" | `move` 发出 9 集，但只有 **4 集**回话不是 `Nothing happens.`；`heat/cool/clean` 21 集发出 / **4 集**有效果 | 表格拆成"发出过 / 引擎报出效果"两列；§4 P2、§9.1 同步改写 |
| 漏斗与 56 集 / 58 轮记在 `metrics.json` 名下 | `metrics.json` **没有**漏斗字段；这两个数是对 `events.jsonl` 的直测 | 新增 `scratch/funnel_and_held.py`（只读），并在报告头部"重算入口"与出处附录点名 |

另外核到一处口径必须写死的数：`schema_invalid_per_decision = 230 / 1,133`（全批）与
`refused_before_execution = 276` 不是一回事 —— 聚合器按理由模式串
（`not permitted` / `action:` / `field required`）计数，276 里有 46 轮不匹配该串
（10 binder 形类 + 32 "`clarify` 必须写出缺什么" + 4 其它 schema 错误）。逐轮实测可复现。

5a 最终哈希（run root 内副本与仓库内副本逐字节相同）：
`Bottleneck-Report.md = ce682366b5d03491f2f2a4fac19180cebaf835ffc2558233e734a27ad9cb02b2`。

---

## Phase 5b：v0.2 决策证据映射（§11）—— 已完成

### 1. 修改内容

- 新增 `docs/bst/v0.2-Decision-Evidence.md`（171 行，sha256
  `7428460959151d52c8449d5f8f00297b32d49fda0d913971e851cd0bde6b98be`；已复制到
  `/tmp/bst_v01/full/`）。结构：§0 一句话结论 → §1 §11.1 表十行逐行核对（每行给出
  "是否观察到 / 定量证据 / 进入前需排除项的实际状态 / 裁定"）+ 本批次自发现的第 11 行
  （载荷字段泄漏）→ §2 §11.2 五依据打分与门槛裁决 → §3 四项前置修复（含可核对的基线值与
  复测口径）→ §4 两个优先候选 + 一个保留候选 → §5 暂不选择方向（每条指回具体条款）→
  §6 预登记的证伪条件（什么结果会推翻本文件每条结论）。
- **没有输出 v0.2 SPEC、没有实现任何 v0.2 机制**（Phase 5 的"不自动输出"约束）。
  本文件只把证据、排除状态与验证路径写清，方向留给后续研究讨论。

### 2. 核对结果（本轮实测，不是转述）

| 核对项 | 结果 |
|---|---|
| `episode_id` 是否真在发给模型的载荷顶层 | **是**：`benchmark/prompts.py:95`（`model_payload` 第 2 个键）；1,133 / 1,133 轮 |
| `held_object` 是否真的逐轮重算 | **是**：`benchmark/state.py:60-80`，`known = p.held_said` 只取当轮原话；无跨轮携带路径 |
| 反馈窗口是否真的裁掉观察 | 当轮 `observation.text` = `world.raw_observation` **整句透传**（`prompts.py:115`）；只有 `recent_feedbacks[-3:]`（`prompts.py:143`）按深度裁剪；`Feedback.short()` 含 `environment_text` 全文（`core/contracts.py:708-709`） |
| 观察类技能是否已开放（§11.1 第 8 行的前置） | **已开放**：`observe→look`、`alfred_inventory`、`alfred_examine`（`native_actions.py:39-41`） |
| §11.2 的 3-task 门槛 | 过门槛 9 个标签；`REPEATED_FAILURE` 恰好 3 个 ⇒ 记为案例；`OBSERVATION_LIMITATION` / `INVALID_ACTION` 各 47 个独立 task_id |
| 独立 task_id 分母 | `task_manifest.json`：134 个 `valid_unseen` game × repeats 2 = 268 槽位（`cross_check`：官方 134 = 磁盘可解 134，id 一致） |

### 3. 与 SPEC 的对应

§11.1 的 10 行全部落到表里，且每行都写了"需排除项当前是已排除 / 未排除 / 无法排除"；
§11.2 的选择依据五条逐项打分；"最多 2 优先 + 1 保留"未超；
高频问题主要来自 Adapter ⇒ 结论写成**"接入诊断尚不充分"**（§0 与 §3），
而不是强行进入 v0.2；无 Memory 对照 / 无动作覆盖证据 / 无视觉实验三条禁令逐条落到 §5 表。

### 4. SPEC-BST 的收尾状态

Phase 0–5 全部完成。§10.4 产物清单里除 `annotations.jsonl` / `annotation_guidelines.md`
（在 run root 的 `annotations/` 子目录，D32 记录的已知偏离）外，其余文件均在
`/tmp/bst_v01/full/`。未提交、未推送；`rules_sha256` 复测仍为 `23abe58f…aae7b3`。

