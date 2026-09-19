# 持续决策实验底座 · 阶段执行记录

唯一有效规格：`Continuous-Decision-Embodied-Agent-SPEC-v0.1.md`。本文件按 P0 → P1 → P2 → P3
顺序记录每个阶段的**修改内容 / 测试结果 / 与 SPEC 对应关系 / 剩余问题**（执行要求第 6 条）。
历史 `S1/…`、`Embodied-Agent-Framework-Spec-v1.0.md`、`Next-Stage-…` 只作为背景，不作为口径来源；
本轮之前的分数一律不沿用（SPEC 9 P0：不沿用旧分数冒充修复后分数）。

工具链（全部结果均在此环境下产生）：

```bash
PYTHONPATH=. /home/czx/miniforge3/envs/embodied/bin/python -m pytest tests/ -q
PYTHONPATH=. /home/czx/miniforge3/envs/embodied/bin/python -m embodied_agent.cli --help
```

Python 3.11.16 / pybullet 3.2.7 / pydantic 2.13.5 / numpy 2.4.6，无 scipy，HTTP 仅用标准库 `urllib`。
基线 commit `a69ffcd`；P0/P1/P2 全部改动尚未提交，工作树为脏，运行清单以 `dirty_diff_sha256` 标识该状态。

---

## P0：可信、共同的实验底座 —— 已完成

### 1. 修改内容

**契约与状态（`core/contracts.py`、`core/verify.py`、`core/scene.py`）**

- 一套共享契约：`ObservationRecord → WorldState → DecisionContext → Decision → SkillCall →
  ExecutionFeedback → EpisodeResult → EvalSpec/TerminalSnapshot`，字段带 `unknown` 三态。
- `Budgets`：`max_decision_rounds=24 / max_http_requests=32 / max_skill_calls=30 /
  per_skill_sim_timeout_s=30 / wall_clock_s=900 / max_identical_invalid_attempts=3 /
  max_semantic_repairs=2 / per_action_extra_repeats=2`；`max_global_replans` 标为 legacy，闭环不再使用。
- 新增 `EpisodeResult.identical_repeats_total`：整段 episode 中"在无新证据下重复上一动作"的**总次数**
  （SPEC 6.2 的预算量 `identical_invalid_attempts` 是**当前连续 run 的长度**，两者不可互换，见 §4 缺陷 16）。
- 槽位归属改为结构化事实：`SkillResult.candidate_resolution` / `ExecutionFeedback.candidate_resolution`
  ∈ {`model_named_candidate`, `runtime_fallback_rule`}，并由 `EpisodeResult.slot_resolution_fallbacks`
  计数、进入 `episodes.csv` 与 `report.behaviour`（SPEC 5.5：自动选槽位必须"各组一致并明确记录"）。
  同时删除 `EpisodeResult.fallback_used`（名字读起来像 SPEC 6.1 禁止的策略替换，实际只是槽位规则）与
  `model_requests`（与 `http_requests` 恒等的重复字段，见 §4 缺陷 19）。
- `PhysicsScene` 每实例新建 pybullet client，所有 `get*State`/`reset*` 显式带 `physicsClientId`；
  托盘几何、可达域、抓取包络按实测校准常量化，并由 `run.py::_declared_constants` 写进运行清单。

**Runtime（`core/runtime.py`）**

- 单循环 decide → validate → control → execute → observe → feedback，**不做任何策略选择**：
  删除 `RecoveryPolicy`（自动换槽位/自动调整 place/自动恢复），Runtime 只拒绝、只计费、只报告。
- 拒绝与预算全部走 `BudgetLedger` 并进入 `EpisodeResult`；`finish` 需独立 `finish_check` 通过，
  假完成上限 `MAX_FALSE_FINISHES=3`；模型错误上限 3。
- `_build_feedback` 的 `pre/post_observation_ref` 与 `pre/post_state_version` 改为**同一来源**（见 §4 缺陷 14）。

**记录与信息边界（`core/events.py`、`evaluation/evaluator.py`）**

- `EpisodeStore`：单 episode 一条 JSONL 追加流，`_redact`/`_is_secret` 覆盖嵌套键与顶层关键字，
  numpy 标量转原生值；`write_manifest` 记录 commit/dirty/diff-hash/schema/依赖/模型元数据，
  `environment_variables` 字段固定写 `"not recorded (SPEC 7: no credentials or full env)"`。
- `IndependentEvaluator` 只读冻结 `TerminalSnapshot` + `EvalSpec`：不读 agent 的 WorldState、验证报告或计划，
  不推进物理；`goals_complete_success`（几何）与 `protocol_ok`（终止/预算/干预/mode）分开，
  `complete_success` 需两者同时成立；空 `EvalSpec` 不可能得满分。

**评测底座（`evaluation/tasks.py`、`protocol.py`、`sources.py`、`run.py`、`report.py`、`cli.py`、`adapters/deepseek.py`）**

- `tasks.py`：3 套正式场景 + smoke + protocol 共 **24 formal / 11 protocol / 8 dev / 4 smoke**，
  `frozen_manifest()` → `configs/experiment/frozen_tasks_v1.json`，`check_frozen()` 双检（文件自哈希 + 与代码现状比对）。
  `PROTOCOL_HARNESS` 冻结 11 条模型载荷边界，`STATE_PAIR_SPECS` 冻结 12 对状态上下文（P1 实现其打分器）。
- `sources.py`：`RulePolicySource / AblatedFeedbackSource(C) / OneShotPlanSource(A) / LLMDecisionSource`；
  mode 只是 source 的标签，执行、校验、预算、候选注册、评测、settle 全部共用。
- `run.py`：一条代码路径跑三臂；**(case, repeat) 共享一次 goal 解析**并按 prologue 计费到每一臂（含 wall clock 回拨）；
  每 episode 新场景、必关；崩溃/API 错误/被拒决策都成行；`--frozen` 为拒绝开关、规范文件为记录；
  `recorded_replay`（读回，不碰物理）与 `plan_rerun`（只重放最后一个 plan，并自报不是轨迹复现）分开命名。
- `report.py`：每 (case, mode) 先聚合成一个配置，再做逐配置配对差与**按配置聚类**的 bootstrap；
  Wilson 只作描述；`behaviour_metrics` 从事件流恢复"被扰动目标/是否重新计入/冗余重放/连续同动作"；
  判不了的行为列入 `needs_blind_review` 而**不由模型自评**；全部预定运行进入分母。
- `adapters/deepseek.py`：`.opener` 可注入；计数器 `http_requests/prompt_tokens/completion_tokens/
  transport_retries/format_repairs/api_errors`；退避 `1.5 * attempts`；401/403 不重试；
  HTTP 错误**不读 body**；`chat_json` 只做一次格式修复；未配置单价时 `cost_estimate()` 返回 `None`。
- `cli.py`：`doctor / sim-smoke / run / evaluate / report / replay / freeze / llm`。

**测试（`tests/`，共 160 项）**

- `tests/conftest.py`：episode 夹具走真实 Runtime + 真实评测器，允许注入自建 source（`source_kind` 显式标注）。
- 新增 `test_state_fork`、`test_decision_ownership`、`test_event_log_safety`、`test_frozen_tasks`、
  `test_protocol_harness`（11 条 SPEC 11.1 协议边界 × 真实 Runtime）、
  `test_run_artifacts`（真实离线批次产物）、`test_report_stats`（统计层）；
  重写 `test_episodes`（原文件在 P0 之前连 import 都不能通过）；
  重写 `tests/online/test_deepseek_online.py`：**"离线孪生"**——同一 `_drive()` 走生产入口
  （`evaluation.run.resolve_goal` + `run_one_episode`）+ 真实 `DeepSeekAdapter` + 可注入 transport，
  每次普通 pytest 都执行，只有带真实 key 的那一条 `skipif`（见 §4 缺陷 18）。
- 删除 `test_replan_budget.py / test_w2_4_contracts.py / test_w2_5_benchmark.py`：三者测试的正是被移除的
  Runtime 侧自动恢复（`RecoveryPolicy`、`can_change_slot`、`can_adjust_place`）与旧 `configs/tasks/v2_*.yaml`
  读盘逻辑；其预算账本断言迁移到 `test_contracts.py`/`test_decision_ownership.py`，
  v2 套件按 SPEC 2.1 保留在树内但由 `test_frozen_tasks` 断言"产品代码不再读取"。

### 2. 测试结果

```
PYTHONPATH=. python -m pytest tests/ -q
159 passed, 1 skipped in 65.87s        # skip = 唯一需要真实 key 的在线条目（RUN_ONLINE=1）
```

| 测试文件 | 数量 | 守住的口径 |
|---|---:|---|
| `tests/unit/`：`test_contracts` 7、`test_decision_ownership` 6、`test_event_log_safety` 15、`test_frozen_tasks` 11、`test_information_isolation` 8、`test_place_robust` 13、`test_placement_planner` 14、`test_report_stats` 27、`test_state_fork` 5 | 106 | 契约/预算、决策归属、日志脱敏、冻结表与 12 对探针的形制、信息隔离、放置几何与槽位归属记录、状态分叉浅拷贝陷阱、统计层 |
| `tests/integration/test_protocol_harness.py` | 13 | SPEC 11.1 的 11 条模型载荷边界（含 5 项 provider 计数与脱敏） |
| `tests/integration/test_episodes.py` | 18 | 规则策略整 episode、反馈账本可追溯（含每个未点名槽位的归属记录）、单一样本判分、解释器、评测器 12 项自检 |
| `tests/integration/test_run_artifacts.py` | 14 | 真实批次产物：清单、冻结表、共享 goal、CSV 分母、三臂同一槽位解析记录、`--frozen` 拒绝、replay 两种命名 |
| `tests/contract/test_pybullet_world.py` + `tests/test_mvp.py` | 6 | 环境契约与保留的二维替身不回归 |
| `tests/online/test_deepseek_online.py` | 3（其中 1 skip） | 2 条**离线孪生**每次运行：真实 adapter 计数/计费/产物 + 一次被拒答复由模型自己修；1 条 `RUN_ONLINE=1` + 真实 key 才跑，且只断言协议、不断言能力 |

原始失败处置（SPEC 9 P0 退出条件要求逐条给结论）：`tests/integration/test_episodes.py` 在 P0 之前**连 import
都不能通过**（引用旧的 `Runtime(scene, executor, …)`/`interpret(task, world)` 接口），其名义上的"集成测试"
不构成任何证据 → 整体重写为真实路径测试；`tests/test_mvp.py` 改为只测按 SPEC 2.1 保留的二维替身；
三个失败/无意义的旧单元文件（见 §1 测试条目）随 `RecoveryPolicy` 的删除一并重新界定，其仍成立的预算断言
迁移到 `test_contracts.py` 与 `test_decision_ownership.py`。**没有任何一项靠放宽容差变绿**：容差与冲量的变化
均由实测标定产生，并作为 `frozen_manifest()["constants"]` 与 `verify.tolerance_version` 被冻结、由清单随行记录。

### 3. 与 SPEC 的对应关系

| SPEC | 落点 |
|---|---|
| 4 信息边界 | `verify.build_world_state` 只取观测；`test_information_isolation.py` + `evaluator.py` 不读 agent 状态；改 `EvalSpec` 不改变 `DecisionContext`（`test_episodes` 协议分档断言） |
| 5.1–5.5 契约 | `contracts.py`；候选 `summary()` 只暴露可公开字段；5.5 的"自动选槽位必须明确记录"= `candidate_resolution` 字段 + `slot_resolution_fallbacks` 计数进表 |
| 6.1 一轮调度 | `runtime.run_episode` 单循环；无自动换方案/自动恢复 |
| 6.2 预算 | `BudgetLedger` + `remaining_payload` 进入上下文；请求预算只保留 SPEC 表中的那一个 `max_http_requests`（删除与之恒等的 `max_model_requests` 别名和 `EpisodeResult.model_requests`）；`test_protocol_harness` 逐条耗尽测试 |
| 7 验证/评测/记录 | 单一 settle 协议产生 `TerminalSnapshot`；`events.py` 清单与脱敏；`recorded_replay` vs `plan_rerun` |
| 8 模块改动映射 | 上述文件即映射表本体 |
| 9 P0 退出条件 | 单技能+clean 可复现（`test_run_artifacts` 整批 success）；无已知假成功（§4 缺陷逐条关闭）；隔离/预算/状态分叉测试通过 |
| 10.1/10.2 主 demo 与配对案例 | `pr_all_releases_*`、`pr_hold_*` 场景 + `_displaceable_in` 可实现性校验 |
| 10.3 环境事件 | `fault_injection.EnvironmentController` 每 episode 重建（`case.fresh_events()`），`events_unfired` 只报告不过滤 |
| 11.1 冻结规模 | 24 formal + 11 protocol + 11 protocol-harness 边界 + 12 状态对 ≥ 12 |
| 11.2 三臂对照 | `run.py::make_source` 唯一差异点 + 共享 goal 计费，`test_run_artifacts` 断言三臂同一 `goal_resolutions/*.json` |
| 11.3 状态利用诊断 | 12 对已冻结并做形制校验；打分器 `evaluation/state_utilization.py` **未实现** → P1 |
| 11.4 指标与统计 | `report.py`：分母=预定运行、按配置聚类配对 bootstrap、Wilson 仅描述、盲审清单、成本与失败归因全出表 |
| 12.1.1–12.1.6 工程验收 | 12.1.1/2/3/5/6 已闭环；12.1.4 的"无静默 rule fallback"已由**离线孪生**每次运行守住（每轮一次 provider 调用、决策事件的 `context_id` 必须是模型被问过的上下文）；真实 key 的在线执行与 12.1.7 的"至少一组在线 demo"**属 P2** |
| 12.4 评测器自检 | `test_episodes.py` 的 12 项错误判定（错绑目标、仍在夹爪、被撞离、悬空高度、空 spec 等） |

### 4. 缺陷（本轮发现并修复；每条由具体测试守住）

1. 跨场景 `physicsClientId` 缺省 → 后建场景改先到场景的对象（`test_pybullet_world`）。
2. `frozen` 门禁结果被丢弃 → 清单同时记录 `matches` 与 `enforced`（`test_run_artifacts::manifest`）。
3. 树外运行目录让 git 溯源谎报 clean → `git_state()` 以仓库根为准并带 diff 哈希。
4. 顶层关键字（如 `api_key=`）绕过 `_is_secret` → `log()` 自查顶层键名。
5. numpy 标量被 `default=str` 记成 `"3"` → `_redact` 先 `.item()`。
6. 同 episode 目录复用导致事件流串写 → 目录含 `episode_id` 且夹具带序号。
7. provider 双缺 `cost_estimate()` → 未配单价时统一 `None`，不臆造价格。
8. `+y` 冲量把物体顶到不可解 → 冲量常量按实测重标定并冻结。
9. cuboid 尺寸非单调 → 放置候选生成改为按实测包络。
10. `pr_all_releases_deviate` 原不可实现 → 目标区域与事件实体联合可解后才冻结。
11. `wall_clock` 在慢机上是首个失败原因 → 预算改由清单记录、协议测试只看有界退出。
12. 陈旧的预算/容差期望（沿用 S1 数字）→ 全部改为从契约与标定常量读取。
13. "不可能失败"的 mock 测试 → 关键断言改到真实 Runtime/Executor 路径上。
14. **反馈里的 `pre/post_observation_ref` 取自技能内部快照，而版本号取自闭环观测**：一次成功的 pick
    会对模型说 `post_observation_ref=null, new_measurement=false`，而它自己报告的状态版本已经移动
    （`core/runtime.py::_build_feedback`；`test_episodes::test_the_feedback_ledger_records_what_actually_happened`）。
15. **报表"被扰动目标恢复率"跨 episode 记账**：同一 case 两次 rollout 扰动同一目标时，修复一次即可把
    未修复的一次算作已恢复（该指标会报 1.0）。改为按 episode 实例计分（`report.behaviour_metrics`；
    `test_report_stats::test_a_disturbed_goal_is_credited_only_to_the_episode_that_repaired_it`）。
16. **`identical_invalid_attempts` 被当作可求和的重复次数**：它是当前连续 run 长度，一次干净的
    episode 也报 1，三臂求和就等于给不重复的策略记 3 次重复。新增 `identical_repeats_total` 承担"总计"，
    报表同时给出"总重复次数"与"退出时最长 run"（`contracts/runtime/report/run.py` CSV 列；
    `test_report_stats::test_a_clean_episode_is_not_billed_for_the_counter_that_ignited_it`、
    `test_run_artifacts::test_every_planned_run_is_a_row…`、`test_protocol_harness` 两计数一致性断言）。
17. **共享 goal 解析失败时那三行没进 `episodes.csv`**：`_write_rows` 只在逐模式循环里被调用，
    `goal_resolution_error` 分支 `continue` 后表格漏行，整批失败时甚至不产生表（报告于是从"幸存者"起算）。
    两个失败分支各自补写表（`test_run_artifacts::test_a_goal_parse_that_crashes_costs_the_whole_trio_not_one_arm`）。
18. **唯一的"在线"测试早已腐烂且从不运行**：它调用 `DeepSeekPlanner.plan()`，而该方法在 P0 重构中已不存在
    ——只要没人设 `RUN_ONLINE=1`，它就永远"通过"，与"从来没对过"不可区分。改为 `_drive()` 复用生产入口
    （`resolve_goal` + `run_one_episode`）+ 真实 adapter + 可注入 transport 的**离线孪生**（每次普通 pytest 都跑，
    断言已知答复下模型做了什么：7 轮、6 次技能、8 次请求、2400 prompt tokens、答复被拒后由模型自己修），
    真实 key 那条只断言协议不断言能力（`tests/online/test_deepseek_online.py`）。
19. **两个谎报身份的字段**：`EpisodeResult.fallback_used` 实际只记录"place 的槽位由共用执行期规则选定"
    （SPEC 5.5 允许且各组一致），但名字读起来就是 SPEC 6.1 禁止的"Runtime 替模型选策略"——孪生测试第一次
    按字面理解它就把一条成功的模型 episode 判成了"被接管"。`EpisodeResult.model_requests` 与
    `Budgets.max_model_requests` 则与 `http_requests`/`max_http_requests` 恒等（`runtime.py` 直接同值赋两字段），
    于是"`model_requests == http_requests`"这条断言永真、不提供任何证据。删除三者，"无静默 rule fallback"
    改由可失败的算术守住：每一决策轮必须恰有一条 `model_calls.jsonl` 记录，且 `decision` 事件的
    `context_id` 必须是模型真被问过的上下文（缺陷 18 的孪生 `_assert_nothing_substituted_the_model`）。
20. **槽位归属只存在于散文 note 里**：`skills.py` 把 `candidate_resolution=...` 写进 `SkillResult.notes`，
    Runtime 靠字符串匹配计数，而失败分支会整体覆盖 `notes`（放置失败时该记录消失），事件流里也没有任何
    结构可查。改为 `SkillResult/ExecutionFeedback.candidate_resolution` 结构化字段（在结果分支**之前**写入），
    `model_named_candidate` 与 `runtime_fallback_rule` 两条路径都由真实物理测试各自证明非默认值
    （`test_place_robust::test_who_chose_the_slot_is_recorded_either_way`），episode 计数进 CSV 与报表
    （`test_episodes`、`test_run_artifacts`、`test_report_stats`）。

### 5. 剩余问题

- **P0 未提交**：HEAD 仍是 `a69ffcd`，P0 全部改动在工作树。任何对外报数前需一次提交，使
  `manifest.code.dirty=false` 且 `commit` 指向被测代码；本轮所有离线批次的清单都如实写着 `dirty=true`。
- **11.3 状态利用诊断的打分器未实现**（`evaluation/state_utilization.py`）：12 对上下文已冻结，P1 第一优先。
- **真实 key 的能力仍未测**：在线代码路径（`DeepSeekAdapter` + `DeepSeekPlanner` + 生产 `resolve_goal`/
  `run_one_episode`）现在每次普通 pytest 都由"离线孪生"真跑一遍（含计数、计费、产物与脱敏），
  但这只证明**接得上**，不证明模型会做任务；真实 key 下的 clean + 扰动各一组 demo（SPEC 12.1.7）留待 P2。
- **盲审表尚未填写**：`needs_blind_review` 只产出证据指针（episode_id/sequence/kind/assignment），
  评分准则需在正式测试前预先注册（11.4）。
- 12.2 的能力门槛是**未测**状态：当前只有规则策略与离线替身，不声称任何能力增益。
- `runs/` 下遗留两个 P0 之前的批次目录（`smoke_3obj_s11_20260918_*`），其口径不属本轮；新产物一律写到树外
  （`.gitignore` 只含 `__pycache__/`、`*.pyc`、`.env`，写进树内会污染工作树）。

---

## P1：离线可验证的信息闭环 —— 已完成

P0 的"剩余问题"里排第一的 11.3 打分器在本阶段实现；本阶段新增的是**闭环的证据**（每个动作可追溯、
最新反馈进入下一轮、无回流无替代）与**诊断的可运行性**（`cli state-util` + 报表章节），
没有新增任何策略逻辑。

### 1. 修改内容

**`evaluation/state_utilization.py`（新增，SPEC 11.3 打分器）**

- 上下文获取顺序是本模块的核心设计：先用 `walk_case()` 用真实场景 + 真实 `Runtime` + 规则策略走完
  该对冻结的 `base_case`，**逐轮捕获 `DecisionContext`**，再从这些真实回合里选两臂；只有在一个 episode
  内确实取不到的状态才用**标记的变换**造（`synthetic=True` + `provenance="transform:..."`）。
  24 臂中 **18 臂是真实回合，6 臂是标记变换**，六种变换分别是
  `candidate_occupancy_failed / candidate_list_reversed / entity_sent_back_to_parse_time_pose /
  held_object_set_unknown / region_occupants_sent_back / two_pending_progress_entries_swapped`。
- 变换**改事实、不改结论**：涉及位姿的用 `_sent_back()` 一次改整束（`pose`+`support`+`supported_by`+
  `at_rest`，并丢弃失效的 `occupancy` 记录），随后一律调用生产同一个
  `RuntimeVerifier(world, case.verify).progress(goal)` 重推 `progress`；唯一直接写结论的是**候选预检**
  （它是对未来动作的报价而非当前快照的读数），且每个被改的候选都带 `synthetic:` note。
  `tests/integration/test_state_utilization.py::test_every_arm_reports_the_progress_its_own_world_implies`
  对 24 臂逐一验证"报告的进度 = 它自己世界推出的进度"。
- `classify()` 是全部测量本身：可接受集合是**标签谓词**表 `LABELS`（21 个），每个谓词只读该策略自己被
  展示过的事实（`world.held_object`、`progress`、`candidates` 的判定、`last_feedback`），
  不读隐藏 `EvalSpec`、不读事件配置、不含任何答案字符串。
- `aggregate()` 把限定条件与分数一起产出，不做解释：`action_fit_rate` 带分母、
  `a_difference_is_required`（`DIFFERENCE_KINDS` 三分类：状态分叉 / 呈现控制 /
  "换了瞄准点但动作仍合法"）、`behaviour_differs`（SPEC 11.3"检验实际行为差异，不把生成理由当作证明"）、
  `candidate_choice_not_tested`（答案从不命名槽位时，命名槽位类的对**自我声明其判别力未使用**）、
  `unreachable_pairs`（不可达是响亮的结果，不是跳过）。
- 成本入账：每个上下文新建一个 source 以保证独立，因此 `provider_counters` 是 24 次请求的**求和**
  （离线策略为 0，并如实写 0，而非缺字段）。产物 `state_utilization.json` 恒带
  `diagnostic_only_no_physics_executed: true` 与 `code`（commit + dirty diff hash）。
- 驱动 `run_state_utilization(...)` 支持 `forks=`（同一批臂打分第二个策略，两臂必须是同一批上下文）并
  返回与落盘**同形**的 `{"summary","contexts"}`。

**`evaluation/tasks.py`（冻结 12 对的可达性）**

- `sp03/sp04/sp10` 换 `base_case`（`sc_c1→sc_c3`、`sc_c2→dev_c2`、`ed_c3→ed_c1`）：**可接受/拒绝标签一字未改**，
  只让两臂真的出现在同一 episode 里；原因见 §4 缺陷 27 与 §2 的实测。
- `_displace_region_event(..., on_own_placement=True)`：`displace_placed` 事件现在用
  `when_entity_id=受害者`，即"它自己被放进托盘"才触发，而不是"任何一次进该托盘的手递交"。
  这是一次语义收紧（精度），实测**不是** sp03/sp04 不可达的原因（见缺陷 27）。
- 结果是一次注册在案的预修订：`configs/experiment/frozen_tasks_v1.json`
  `ecc1b5d257e0 → 2f1c74f52e91`。逐字段 diff 只有两处：5 个 `displace_placed` 事件的
  `when_entity_id`，3 对状态对的 `base_case`+`note`。场景几何、预算、`hidden_eval_spec` 均未变。

**`cli.py`**

- 新子命令 `state-util`：`--planner rule|fixture|deepseek`（rule=离线控制策略；模型臂每上下文一次独立请求，
  并写与 episode 同格式的 `model_calls.jsonl`，P2 的成本账本因此已就位）、`--out`、`--only`。
  退出码区分**结果差**（0）与**测不出**（不可达对 → `EXIT_INFRA_ERROR`，并明确"上面这个数不是 12 对诊断"）。
- `freeze` 不再能静默覆盖已漂移的预注册文件：需 `--amend`，并打印它替换掉的 hash（`amended_from`）。
- `llm` 增加 `--file`，可读诊断自身的请求账本。

**`evaluation/report.py`（11.4 行为指标进报表）**

- 新 `state_utilization(run_dir)` + `STATE_UTIL_FIELDS`：把 11.4 的"状态分叉动作适配率"连同分母与
  限定读进 `report.json`；**未运行返回 `{"ran": false, note: 如何运行}`，不当 0 报**。
  `report.md` 增"State-fork action fit（未执行任何物理）"一节，并把"未使用区分度的对 /
  未命名槽位的候选对 / 不可达对"三条限定直接印在数后面。

**测试（新增 30 项，全部每次普通 pytest 都跑）**

- `tests/integration/test_state_utilization.py`（13 项）：覆盖与不静默丢弃、每个冻结标签都存在谓词、
  `DIFFERENCE_KINDS` 与差异轴一致、控制策略恰好漏哪两臂（点名而非计数）、全 `finish` 对照只在 sp12 得分、
  未命名槽位不被指控命名错、呈现控制对"顺序无关策略"与"只取第一项策略"分别给结论、
  变换不污染被走的 episode（按 round 对齐一次全新 episode，比较整个 `world` 的 dump，只排除 `wall_time`）、
  返回==写盘、产物无凭证。
- `tests/integration/test_p1_exit_conditions.py`（5 项）：见下 §2 的退出条件三项。
- `tests/unit/test_frozen_tasks.py`（+1 项）：`freeze` 拒绝对漂移清单覆盖、`--amend` 打印被替换 hash。

### 2. 测试结果

```bash
PYTHONPATH=. /home/czx/miniforge3/envs/embodied/bin/python -m pytest tests/ -q
# 179 passed, 1 skipped in 93.50s        （P0 结束时为 159 passed, 1 skipped in 65.87s）
PYTHONPATH=. ... -m embodied_agent.cli state-util --out /tmp/state_util_rule   # 树外，沿用 P0 约定
```

规则控制策略（`rule_policy`，非模型结果）在 12 对上的实测，产物
`/tmp/state_util_rule/state_utilization.json`（commit `a69ffcd`，dirty，`dirty_diff_sha256=7016cc658bf09681`）：

- **24/24 上下文全部到达，`unreachable_pairs=[]`**；`action_fit_rate = 0.9167`（22/24），
  两臂同时适配 **10/12** 对；要求行为差异的 9 对里 7 对确实用上了该区分。
- 两次未命中被点名而非计数：`sp08/full`（该区域每个被报价的槽位都已预检失败，仍请求 `place`，
  `accepted_because=[]`）与 `sp09/unknown`（夹爪持有不可辨识时选择释放，而该臂只接受
  `observe/safe_retreat`）。这正是该诊断存在的目的：**这两条是策略事实，不是机器故障**。
- 未使用区分度的两对：`sp05`（free/occupied 两臂画出同一动作）、`sp08`（full/free 两臂都 `place`）。
- 如实报告的自身局限：`contexts_naming_a_candidate = 0` → `sp05/sp06/sp08` 三对
  `candidate_choice_not_tested`。该控制策略从不命名槽位（`slot_resolution_fallbacks=3` 的同一事实），
  所以这三对的"选哪个槽位"判别力**本轮没有动用**，留给 P2 的真实模型臂；分数不因此被调整。
- 全 fork 构建 15.6 s / 177 MB，作为 module 级 fixture 常跑，未新增 slow 层。

P1 退出条件（SPEC 9）三项，各由命名测试守住：

| 退出条件 | 测试 |
|---|---|
| 每个动作都能追溯到上下文和决策 | `test_every_action_traces_back_to_the_context_and_decision_that_asked_for_it`（按事件序检查 三段链：skill_call→decision→decision_context 的先后与同一 context 归属；且策略被递给的对象序列 == 日志记录的序列）、`test_a_decision_about_another_rounds_context_is_refused_and_never_executed`（反向：不能凭空指定上下文，拒绝且不动物理） |
| 最新反馈进入下一轮 | `test_the_newest_feedback_is_what_the_next_round_is_shown`（一条含 `rejected`/`model_error`/`finish_rejected` 三分支的真实 episode：每轮恰一条反馈、各自指名来源轮次，下一轮的 `last_feedback` 与它逐字段一致）、`test_a_round_that_changed_the_world_reports_the_measurement_it_changed_it_with`（改过世界的动作必须带新读数与独立核验） |
| 没有 EvalSpec 回流或隐藏策略替代 | 回流由 `tests/unit/test_information_isolation.py`（AST 扫描 + 分级配对不外泄）守住；替代由 `test_the_loop_ran_exactly_what_was_asked_for_in_both_planning_modes` 守住：B/C 两模式每个 `skill_call` 的 skill/args 与决策逐字相同，命名过的 `candidate_id` 不被改，唯一允许差异是"未命名时由共用执行期规则选槽"且 `candidate_resolution` 与 `slot_resolution_fallbacks` 计数互相印证 |

P1 要求覆盖的分支（成功/失败/unknown/无效动作/finish 被拒/模型错误/预算耗尽）仍由
`tests/integration/test_protocol_harness.py` 的 11 个 `h_*` 用例逐条给出，本阶段未重复实现。

### 3. 与 SPEC 的对应关系

| SPEC | 本阶段落点 |
|---|---|
| 9 P1 退出条件 | 上表三项测试；两种规划模式共用 `_validate_decision` + `SkillExecutor` + `RuntimeVerifier`（`make_source` 仍是唯一差异点，未改） |
| 5.2 信息边界 | `LABELS` 每个谓词只用策略自己被展示过的事实；`classify()` 不接触 `EvalSpec`/事件配置/验证器 |
| 6.1 拒绝即反馈 | 交叉 context 的决策被拒后**不执行**且成为下一轮可读的反馈；标签谓词不吞异常（缺陷 25），"测不了"与"不适用"不同码 |
| 7 记录 | `state_utilization.json` 带 `code`（commit+dirty diff）与 `diagnostic_only_no_physics_executed`；模型臂写 `model_calls.jsonl`；产物无凭证由测试断言 |
| 11.3 状态利用诊断 | 12 对 × 每上下文 1 次 = 24 请求；真实回合优先、伪造状态全部标记且只用于离线；可接受集合是标签集不是答案串；候选顺序置换单列 `sp06` 为呈现控制并验证其确实能抓"只取第一项"；未声称识别内部推理（`a_difference_is_required`/`behaviour_differs` 只报行为） |
| 11.4 指标与统计 | 状态分叉动作适配率进 `report.json/report.md` 并带分母与限定；诊断自身成本进 `provider_counters`；"未运行"不等于 0 |
| 2.1 保留工作树 | 未删除任何 legacy 文件；新增产物写到树外 |

### 4. 缺陷（本阶段发现并修复；每条由具体测试守住）

21. **`None == None` 把每次未命名槽位的放置判成"命名了被占用的那个"**：`place_with_occupied_candidate`
    最初写成 `a.candidate_id == fork.focus_candidate`，未命名时两边都是 `None` → sp05 两臂同时被指控违规。
    加 `bool(a.candidate_id)` 前置，并把该反例固化为
    `test_a_policy_that_never_names_a_slot_is_not_accused_of_naming_the_wrong_one`。
22. **`advance_to_next_pending` 用 `focus_entity` 定义，两处反向出错**：sp08/full 里"所有槽位都已失败还
    请求 place"被记成"推进了下一个待办"（虚假得分），而 sp10 里刚 pick 完成、正当去 place 的动作被取消
    资格。改为对可读事实判定：不是重复上一条已完成的反馈、不是所有被报价槽位都失败的 place、
    且该对象进度不为 `true`/缺失。
23. **sp07 两臂不只位姿不同**：最初拿 episode 的两个 round 当一对，于是夹爪状态与待办对象也一并不同，
    该对测的就不再是"抓取点移动"。改为取同一个 round 的孪生（`_sent_back` 把该物体放回目标解析时的
    实测位姿并整束重推事实），`test_every_arm_reports_the_progress_its_own_world_implies` 保住派生一致性。
24. **`grasp_point_moved` 被误归为呈现控制**：它是状态分叉，只是两臂的可接受动作相同（重新瞄准同一个
    pick）。`DIFFERENCE_KINDS` 因此是三分类并显式带 `requires_behaviour_difference` 位，避免
    "两臂动作相同"被当成该对的失败。
25. **标签求值里的静默 `except Exception`**：会把"谓词在真实上下文上算不出来"读成"该标签不适用"，
    于是坏测量看起来像干净的未命中。`classify()` 不再吞异常，由驱动按上下文记 `error` 字段。
26. **三处对契约的猜测**：`WorldState.entity()` 对未知 id 抛 `KeyError`（改 `_entity()` 显式容错）、
    `isinstance(v, Literal["unknown"])` 直接 `TypeError`（改值比较 `== "unknown"`）、
    `EntityState` 没有 `position_xy`/`in_target` 字段（改读 `pose.position.x/.y` 与 `world.occupancy`）。
27. **`displace_placed` 在 `sc_c1/sc_c2/sc_c5/sc_c8` 不使关系失效**（本轮实测）：事件把已放置的方块沿
    托盘内方向钉到靠墙处，下一轮 `placed` 仍读 `true`，于是这些 case 里不存在"`true→false` 的真实翻转"，
    `satisfied_vs_displaced` 两对不可达。**没有靠放宽容差或改判分让测试变绿**：改 `base_case` 到
    确实会翻转的 `sc_c3/dev_c2`（缺陷之外的可达性核查），并在此记录该机制问题为剩余问题（§5 第 2 条）。
    **P3 冻结前订正（见 P3 §0，全部为实测）**：该结论读错了物体——受害者是 `_displaceable_in` 点名的
    **方块**，而当时打印的是同托盘里**第一个**被放置的圆柱。事件确实触发、`placed` 确实读 `false`；
    真正成立的那半是"受害者从未被观察到 `true`"，因此 `sp03/sp04` 换 `base_case` 依旧正确，
    但"机制坏掉"这一条不成立，`expected` 与物理都不动。
28. **驱动只返回 summary**：拿同一批臂比较第二个策略时需要逐上下文行，只能回读文件（把返回值挂在写盘
    成功上）。改为返回与落盘同形的 payload，并加断言"返回 == 写盘"。
29. **`--only` 的部分运行把未要求的 10 对报成 unreachable**：分母没错但读者会以为诊断自身失败。
    现在 `only` 决定"被问过却没到达"的集合，命令也据此决定退出码。
30. **`freeze` 能静默覆盖已漂移的预注册清单**：旧批次的清单会因此指向从未跑过的任务表。改为拒绝并要求
    `--amend`，打印 `amended_from`（`test_refreezing_a_list_the_code_has_drifted_from_is_an_explicit_act`）。

### 5. 剩余问题

- **本轮的 0.9167 不是能力分数**：它是规则控制策略在 12 对上的行为读数，作用是证明打分器会说"不"
  并且不吞限定。`sp05/sp06/sp08` 三对的槽位判别力尚未动用（`contexts_naming_a_candidate=0`），
  这一条已在产物里自报，留给 P2 的真实模型臂使用；不因此调整任何分数。
- **缺陷 27 的机制问题未在本阶段动**：`sc_c1/sc_c2/sc_c5/sc_c8` 声明了 `displace_placed` 却测不出关系失效。
  两种处置（改事件方向 vs 承认这些场景的扰动不改变放置结论并改 `expected`）必须在 P3 冻结前二选一并记录；
  本阶段只把它写成事实，不改物理以适配期望。
  **（P3 §0 已裁定：两种处置的前提都不成立——事件确实触发且使关系失效，见缺陷 27 的订正。）**
- 真实 key 的在线 decide、候选引用与 clean/扰动 demo 仍未测（P2）；`state-util --planner deepseek` 的
  请求与账本路径已接好但未运行（`DEEPSEEK_API_KEY` 未设，且不会写进任何配置或日志）。
- 盲审表仍未填写（11.4）；`needs_blind_review` 只产出证据指针。
- P0/P1 全部改动仍未提交，HEAD 仍是 `a69ffcd`；本轮产物清单如实写 `dirty=true`。

---

## P2：在线模型决策与候选引用 —— 已完成

P1 的"剩余问题"第一条（真实 key 的在线 decide / 候选引用 / clean+扰动 demo）在本阶段做完。
新增的是**在线链路的证据与计量**，没有任何新的策略逻辑：Runtime 依旧只做合法性检查、执行与反馈，
不替模型换方案、不替它重试、不替换它的动作（执行要求第 5 条）。全部在线数字由真实 key 经生产入口
（`cli run` / `cli evaluate` / `cli state-util` / `tests/online`）产生，产物一律写到树外 `/tmp/p2_online/`。

### 1. 修改内容

**在线 decide 接通（`adapters/deepseek.py`）**

- 三类请求共用同一条 transport 与同一份证据字段：`goal_parse` / `decision` / `one_shot_plan`，
  提示版本 `goal=s2-goal-v1;decide=s2-decide-v1;plan=s2-plan-v1`；每条请求写一行
  `model_calls.jsonl`，字段为 `requested_model / returned_model / completion_id / created /
  finish_reason / usage / transport_attempts / transport_retries / prompt_chars / raw_chars /
  raw_response / latency_s / http_requests_this_call / prompt_version / context_id / decision_id`
  ——即"谁答的、答了什么原文、答了多久"三件事在同一行里可对上。
- 重试与错误语义：401/403 与不可重试的 4xx 立即失败，408/429/5xx/`URLError`/超时/JSON 解析失败
  退避 `1.5·n` 秒重试；**响应体永不读入异常消息**（一律 `HTTP <code> (body not read)`），
  密钥只在 `Authorization` 头里出现，由 `core/events.py` 的 `_redact/_is_secret` 与
  `"environment_variables": "not recorded (SPEC 7)"` 挡在日志外。
- 候选引用规则写在 `DECISION_SYSTEM`：`place` 可命名 `context.candidates` 里的 `candidate_id`，
  也可只给 `target_id` 交给执行期共用规则；引用不存在或已失效的候选会被拒绝。
- 新增 `sampling` 属性（缺陷 37）：清单记的是**实际发出**的参数
  （model/base_url/temperature/max_tokens/response_format/stream/timeout_s/max_retries/proxy 种类），
  proxy 只记种类不记 URL（代理地址可能含凭证）。
- 计价未配置：`api_cost_estimate` 诚实为 `null`（11.4 要的是请求数/token/延迟，不编造美元）。

**有界历史（`core/runtime.py` + 在线断言）**

- `HISTORY_CAP = 12`：Runtime 侧对 `feedback_history` 与 `attempts` 截断（SPEC 5.2 有界历史）；
  展示深度 `feedback_depth=3` 在 `model_payload()` 再薄一层，两者是**不同的界**，各自被测。
- 在线孪生测试从**线上载荷**读这两个界，而不是从 dataclass：`recent_feedbacks` 长度 ≤3 且末轮恰为 3、
  `attempts` ≤ `HISTORY_CAP`、同时事件里 `feedbacks_included > 3`（证明薄的是展示而非记账）。

**目标解析的证据与账本（`evaluation/run.py`）**

- `GoalResolution` 新增 `provider_meta`（`CALL_EVIDENCE` 白名单），成功与异常两条分支都填；
  新 `goal_logger(root)` 让 `cmd_run` 与 `run_group` 写同一条 `goal_resolutions/goal_calls.jsonl`。
- `model` 字段改为 `meta.requested_model` 兜底（此前把 provider 名当模型名，见缺陷 33）。

**诊断的在线臂与计量（`evaluation/sources.py`、`evaluation/state_utilization.py`）**

- `LLMDecisionSource`：每个上下文新建一个 source（独立性），`provider_counters` 按**增量**入账（缺陷 31）。
- `miss_kind` 三分：`wrong_choice` / `schema_invalid` / `request_failed`，并新增
  `contexts_answered` 与 `action_fit_rate_of_answers`；主率分母仍是"到达的每一个上下文"（SPEC 11.5：
  机器故障留在分母里但要能点名）。

**报表（`evaluation/report.py`）**

- 新 `model_latency(run_dir, rows)`：逐调用延迟 p50/p95/max/total + `by_kind`，
  并分别计数"没有账本的 episode"与"未记延迟的调用"；`goal_parse` 单列（每 case 一次、三模式共用，
  折进决策延迟会让 A/B/C 不可比）。`state_utilization()` 从诊断自己的账本补 `provider_latency`。
- 两条"没有请求"的分支都明写"这不是 0 延迟"（缺陷 35）。

**任务集与 CLI（`evaluation/tasks.py`、`cli.py`）**

- `_REGISTERED_SETS = (smoke, dev, formal, protocol)` + `all`（47 例去重拼接），`SET_NAMES` 成为
  `--set` / `--task` 的唯一来源（缺陷 34）；`PLANNER_KINDS` 同理，`--planner gpt` 在 argparse 与
  `_planner_for()` 两处都拒。

**候选引用规范化（`core/contracts.py`，缺陷 36）**

- `Decision` 增校验器：`execute.args.candidate_id` 有值而规范字段为空时**提升**为同一引用；
  两者给出**不同** id 时拒绝（那是同时声称两个几何，只有模型能定，Runtime 不选）。

**测试（新增 8 项，普通 pytest 每次全跑；另有 1 项在线 opt-in）**

- `tests/online/test_deepseek_online.py`（现 4 项：2 项孪生每次离线跑，2 项真实请求需
  `RUN_ONLINE=1` + key；本阶段新增真实扰动那 1 项）：
  离线孪生（同一 `_drive`，脚本化 transport，可断言模型"做了什么"）、拒绝/不可解析由模型自己改
  （限次修复计数，不替换动作）、**在线模型是唯一决策者**（真实 key 走完 episode，逐
  `skill_call` 与决策逐字比对）、**真实扰动经真实接口抵达模型**（`smoke_state`：扰动事件 fired、
  其后一轮看到新的 `state_version` 且 `feedbacks_included ≥ 1`；只断言协议与证据，不断言成功）。
- `tests/integration/test_state_utilization.py`（+3：增量计费、未答上下文分类、args 命名的候选引用）、
  `tests/unit/test_report_stats.py`（+2：逐调用延迟、诊断账本延迟与"无请求"分支）、
  `tests/unit/test_frozen_tasks.py`（+2：`SET_NAMES` 单源与 CLI 拒绝）、
  `tests/unit/test_contracts.py`（+1：候选引用提升与分歧拒绝）。

### 2. 测试结果

```bash
PYTHONPATH=. /home/czx/miniforge3/envs/embodied/bin/python -m pytest tests/ -q
# 187 passed, 2 skipped in 95.17s        （P1 结束时 179 passed, 1 skipped）
set -a; . ./.env; set +a
RUN_ONLINE=1 ONLINE_OUT_DIR=/tmp/p2_online/online_final PYTHONPATH=. ... -m pytest tests/online -q
# 4 passed in 33.38s                      （2 项孪生 + 2 项真实请求）
```

在线 episode（真实 key，`cli run --planner deepseek`，产物 `/tmp/p2_online/*`，commit `a69ffcd` dirty）：

| case | 模式 | 结果 | 轮次 | 请求 | tokens(p/c) | 墙钟 | 声明事件 | 独立判定 |
|---|---|---|---|---|---|---|---|---|
| `clean_c1` | B | success | 7 | 8 | 36227/1082 | 10.5 s | 无声明 | success=True |
| `sc_c1`（扰动） | B | success | 9 | 10 | 48374/1453 | 12.9 s | `sc_c1-displace-tray_right-obj_blue_2-m1` fired，unfired=[] | success=True |
| `ed_c1`（执行偏差） | B | success | 8 | 9 | 41131/1206 | 11.7 s | `ed_c1-grasp_calibration-obj_green_1-m1` fired | success=True |
| `clean_c1` A/B/C | 三臂 | 各 success | 7/7/7 | 1/8/8 | 448/82 · 36274/1107 · 24660/1059 | 1.9/11.2/10.0 s | — | 三臂均 independent success，与 agent 自述一致 |

12 对状态分叉诊断（每上下文 1 次请求，不执行任何物理），同一批 24 臂分别打两个策略：

| | 在线模型臂 | 规则控制臂 |
|---|---|---|
| `action_fit_rate` | **0.9167**（22/24） | **0.9167**（22/24） |
| 有答案的上下文 / 未命中分类 | 24/24，`wrong_choice×2` | 24/24，`wrong_choice×2` |
| 两臂同时适配 | 10/12 | 10/12 |
| 要求区分的 9 对中真用了区分 | 6 | 7 |
| 命名槽位的上下文 | **11/24** → `pairs_where_candidate_choice_went_untested = []` | 0/24 → `sp05/sp06/sp08` 三对的几何判别力未动用 |
| 未使用区分的对 | `sp05, sp08, sp09` | `sp05, sp08` |
| 成本 | 24 请求，124,168+3,804 tokens，p50 1.442 s / p95 1.947 s / max 2.057 s，累计等待 34.9 s | 0 请求，0 tokens，无延迟可测 |

**这张表的诚实读法（不挑收益说）**：在本诊断上在线模型**没有**在总分上超过规则控制——两者同为 0.9167。
模型的净收益是第一次真正动用槽位判别（11 个上下文命名候选，规则臂 0 个）；净损失是 `sp09/unknown`
在持有物不可辨识时仍选择释放，被该臂的拒绝标签 `place` 抓到（规则臂这一对反而答对）。
两次未命中的具体读法：`sp08/full`＝每个被报价槽位都已预检失败仍请求 `place`（`accepted_because=[]`）；
`sp09/unknown`＝`missing_information` 为空而世界说 held 不可辨。两条都是**策略事实**，
不是机器故障，且都没有靠放宽容差或改判分口径消化掉。

P2 退出条件（SPEC 9）逐项：

| 退出条件 | 证据 |
|---|---|
| 在线模型独立走完至少一组 clean 与一组扰动，证据链完整 | 上表 `clean_c1` 与 `sc_c1/ed_c1`：每轮 `decision_context ↔ decision ↔ skill_call ↔ feedback` 同 `context_id/state_version` 配对，`model_calls.jsonl` 存原文与 `completion_id`，扰动轮次有 `fired` 记录与 `state_version` 前进；`test_the_online_model_is_the_only_decision_maker` 逐字比对每个执行调用与决策 |
| 相似失败能被真实决策接口表达并校验 | `test_a_refused_or_unparseable_answer_is_the_model_s_to_fix`：同一失败重复时精简拒绝反馈进入下一轮、限次修复计数走、Runtime 不替换动作；在线臂 `ed_c1`（`GRASP_MISS` 校准事件）实测 8 轮 0 次拒绝、0 次 `model_errors` |
| 不同 held 状态能被真实决策接口表达并校验 | `sp01/sp02`（held vs released）与 `sp09`（known vs unknown hold）四臂全部由真实模型作答，前一对 `behaviour_differs=True`，后一对被记为未用区分（如实扣分而非剔除） |
| 候选引用 | 在线诊断 11/24 命名候选且 `accepted_because` 命中 `place_with_named_candidate/place_with_other_candidate`；本阶段 8 个在线 episode（含同一 case 的重复运行）共 25 次放置：20 次在规范字段命名槽位、2 次只写进 args（缺陷 36 的来源）、3 次属 A 臂一次性规划本就不命名（`slot_resolution_fallbacks=3`） |

### 3. 与 SPEC 的对应关系

| SPEC | 本阶段落点 |
|---|---|
| 9 P2 | 接通在线 decide（`s2-decide-v1`）、候选引用（诊断 11/24 + 孪生逐字比对）、有界历史（`HISTORY_CAP`/`feedback_depth` 双界）、clean+扰动 demo、保留不合理选择并记录拒绝（`sp08/sp09` 不修正）、不人工替换动作 |
| 5.2 有界历史 | 界从**线上载荷**断言，并证明它确实绑定过（末轮恰 3 条、事件里 `feedbacks_included > 3`） |
| 6.1/6.2 拒绝即反馈、预算 | 拒绝计数与限次修复走生产 `_validate_decision`；`max_http_requests=32/episode` 未被任何一次在线运行触及（最大 10） |
| 7 记录 | 清单 `model` 块补齐实际发出的采样参数（`sampling`）；逐请求账本含 `returned_model/completion_id/latency_s`；无凭证入日志/产物由测试断言（含 `Authorization` 字样不出现） |
| 11.3 诊断 | 模型臂与规则臂打同一批 24 上下文；伪造臂仍全部标 `synthetic`；结果并列呈现含负向 |
| 11.4 指标 | 技能数/决策数/HTTP 请求/token/**延迟**/墙钟/仿真时长/失败数全部进 `report.json/report.md`；缺测（无账本、无延迟字段）显式计数 |
| 11.5 分母 | `misses_by_kind` + `contexts_answered` + `action_fit_rate_of_answers`：机器故障留在分母但能点名 |
| 12.1.4 在线测试 opt-in | 无 `RUN_ONLINE` 时整档 skip，离线孪生每次全跑，故在线断言不会静默腐烂 |
| 执行要求 5 | 本阶段未新增任何替模型决策的代码路径；Runtime 的槽位解析只在模型**未命名**时按共用规则进行，且单独计数（`slot_resolution_fallbacks`：A 臂 3，B/C 臂 0） |

### 4. 缺陷（本阶段发现并修复；每条由具体测试守住）

31. **诊断把适配器累计计数当本轮成本**：首版 `provider_counters` 取每个 source 结束时的累计值求和，
    24 个上下文报成 **600 请求 / 2,979,600 prompt tokens**；对照账本实为 25 请求 / 119,987 tokens。
    改为按上下文**增量**入账（与 Runtime 的 episode delta 计费同一道理，SPEC 6.2/11.4），
    修后在线臂报 24 请求 / 124,168 tokens，与 `model_calls.jsonl` 逐条求和一致。
    `test_the_diagnostic_bills_each_context_once_not_every_context_ever`。
32. **"没答上来"被读成"选得不好"**：schema 违规或请求失败与策略选错共用一次未命中。
    加 `miss_kind` 三分与两个率；`test_a_context_that_was_never_answered_is_not_read_as_a_bad_choice`。
    （真实触发：首轮在线诊断 `sp10/failed` 因 `grasp_candidate_index` 越界成为 `schema_invalid`。）
33. **目标解析证据把 provider 名当模型名**：`meta.get("model", kind)` 使 `resolution.model` 恒为
    `deepseek`。改 `requested_model` 兜底并由孪生测试断言
    `resolution.model == "deepseek-fake-echo"`。
34. **`--set clean` 被 argparse 拒、`--set all` KeyError**：CLI 的 choices 来自另一张旧表，
    而子集构建器另有其名。统一为 `SET_NAMES`/`_SET_BUILDERS` 单一来源并补 `all`；
    验证注册集与冻结清单 hash 一字未变（`2f1c74f52e91`）。
35. **报表没有延迟**（11.4 明确列了）：逐调用 `latency_s` 一直在账本里，但没有任何地方聚合。
    新 `model_latency()`；两个"没有请求"的分支明写"无测量，不是 0"。
36. **参数包里的 `candidate_id` 与规范字段分歧**（真实在线证据）：`skills.py` 读
    `args["candidate_id"] or call.candidate_id`，而清单/反馈/11.3 标签只读规范字段——
    在线 8 个 episode 里有 **2 轮**把 id 只写进 args（`sc_c1`、`ed_c1` 各一）。物理按模型自己的槽位执行了，
    记录却是"没命名"，于是 `place_with_occupied_candidate` 这类**拒绝**标签抓不到它，该臂会被记成适配。
    改为在校验器里提升（同一引用的两种写法）并在两种写法**互不一致**时拒绝（不替模型选）。
    核对：在线诊断 24 上下文里 args-only 为 0 例 ⇒ **本阶段已报的分数一字不变**；
    `test_a_slot_named_only_in_the_arguments_is_recorded_as_named`（契约）+
    `test_a_slot_named_only_in_the_arguments_is_still_scored_as_the_slot_named`（打分器）。
37. **清单 `model.sampling` 恒为 `null`，而配置文件里两个键根本没人读**：
    `configs/models/deepseek.yaml` 的 `prompt_version: s1-plan-v1` 与 `json_mode: true` 是死键
    （清单实记 `s2-*`，`response_format` 硬编码），冻结批次会据此核对到错的口径。
    适配器加 `sampling` 属性（从实际发送处取），配置文件只留被读取的键并把 prompt 版本写成
    `prompts:{goal,decision,plan}` 三键；孪生测试断言 `sampling == 请求体`。

### 5. 剩余问题

- **请求名与返回名不一致**：24/24 请求 `deepseek-chat`，返回元数据一律 `deepseek-flash`。
  P3 只能固定"请求名 + 端点 + 提示版本"，**服务侧模型名以逐请求 `returned_model` 记录并如实公布**，
  不得写成"实验在 `deepseek-chat` 上完成"。
- **缺陷 27 的机制问题仍未动**（`sc_c1/sc_c2/sc_c5/sc_c8` 声明 `displace_placed` 却测不出关系失效）：
  P3 冻结前必须二选一（改事件方向 vs 承认这些场景的扰动不改变放置结论并改 `expected`）并记录。
  **（下一条即 P3 §0：该表述本身是错的，两种处置都不需要执行。）**
- **盲审准则仍未填写**（11.4）：`needs_blind_review` 只产出证据指针；P3 跑全量前必须给出 rubric，
  否则"哪个下一步是合理的"这一类只能留白，不能靠模型自评。
- 定价仍为 `null`（不编造）；P3 报告的成本口径是请求数/token/延迟。
- **P3 规模预算（本阶段实测外推）**：诊断 24 请求 / 124k prompt tokens / 35 s 等待；单 episode
  8–10 请求 / 36–48k prompt tokens。formal 24 例 × 3 模式 × 3 重复 = 216 episodes（+ 72 次目标解析）
  ≈ **1,400–1,600 请求 / 6–7 M prompt tokens / 顺序调用 1.5–2.5 小时**；本阶段观测到一次约 50 s 的慢请求
  与一次退避重试，故 P3 必须容忍超时并按 `max_http_requests=32/episode` 的预算执行，
  失败样本留在分母里点名（SPEC 11.5）。
- P0/P1/P2 全部改动仍未提交，HEAD 仍是 `a69ffcd`；本轮产物清单如实写 `dirty=true`。

---

## P3：冻结实验与对照报告 —— 进行中

### 0. 冻结前裁定：缺陷 27（`displace_placed` 机制）

P1/P2 两节的"剩余问题"都要求 P3 冻结前在两种处置里二选一（改事件方向 vs 承认扰动不改变放置结论并改
`expected`）。**逐案实测之后，两种处置的前提都不成立，因此一个都不执行**：把扰动"改对"会把物理调到
期望上，把 `expected` 改低会抹掉一条本来就在起作用的判别力。裁定按证据下，不按记录下的旧推断下。

复测方式：走真实 `Runtime`/真实技能的正式集 episode（`cli run --mode A|B --planner rule`，产物在
**树外** `/tmp/p3_probe/`），逐轮读 `progress` 谓词、事件注入日志与 place 反馈的两份快照读数。
`allow_x = 0.126 m`，被推物体的 `footprint_half ≈ 0.023 m`，故 `|dx| > 0.103 m` 即判 `false`。

| case | 机制 | 受害者（方块） | 触发 | 位移 m | 释放点 x → 事件后 x | 核验 `xy_err_x` | Skill 自身 `inside_target` | 同区其他完成项 | mode A | mode B |
|---|---|---|---|---|---|---|---|---|---|---|
| sc_c1 | displace_placed | obj_blue_2 | 1 次 | 0.1501 | 0.7003 → 0.5503 | 0.1097 → `false` | 1.0 | obj_green_1 自 r3 起恒 `true` | failed 2/3（3 次假 finish） | **success 3/3** |
| sc_c2 | displace_placed | obj_yellow_2 | 1 次 | 0.0891 | 0.6410 → 0.5521 | 0.1079 → `false` | 1.0 | obj_blue_1 恒 `true` | failed 2/3 | — |
| sc_c5 | displace_placed | obj_green_2 | 1 次 | 0.1494 | 0.7000 → 0.5508 | 0.1092 → `false` | 1.0 | 其余 4 项最终全部 `true` | failed 4/5 | — |
| sc_c8 | displace_placed | obj_purple_2 | 1 次 | 0.0297 | 0.5801 → 0.5505 | 0.1095 → `false` | 1.0 | obj_yellow_1 恒 `true` | failed 2/3 | **success 3/3** |
| sc_c3 | displace_second（对照） | obj_yellow_1 | 1 次 | 0.1093 | 0.6599 → 0.5506 | r3–r4 `true` → r5 起 `false` | 1.0 | — | failed 3/4 | — |

四条事实，逐条对应旧记录的哪里错了：

1. **事件每次都触发，且真的使关系失效**：四例 `after_xy` 的 x 全部落在 0.5503–0.5521 m（靠墙位），
   `placed` 谓词在事件后的快照上读 `false`。旧记录说"下一轮仍读 `true`"，是因为它打印的是同托盘里
   **第一个**被放置的物体（sc_c1 的 obj_green_1，圆柱），而 `_displaceable_in` 点名的受害者是**方块**。
   那个恒 `true` 恰好是 SPEC 10.1 "保留其他仍有效的已完成结果" 的**通过**证据，不是机制失效证据。
2. **两份读数没有被混同**：place 的 `measurements.inside_target == 1.0`（释放确实落在有效区内）与
   `verification.reports[0].value == "false"`（事件后的独立快照）同属一条反馈，各自指名
   `pre/post_observation_ref`。SPEC 10.3 禁止篡改 `SkillResult`，这里正是"不篡改"应有的形状。
3. **`expected: success` 站得住**：被钉住的方块仍可抓取——mode B 的连续决策把它重新抓起并落座
   （sc_c1 `xy_err_x` 0.110 → 0.041；sc_c8 → 0.079），3/3 完成；同一条 one-shot 计划（mode A）不会回头，
   于是 2/3 且三次假 finish。**"扰动使任务失败"与"策略不回头使任务失败"被这两个臂分开，正是
   state_change 子集存在的理由**；改 `expected` 等于自废这条判别力。
4. **旧记录里唯一成立的那半**：因为事件与释放在同一轮，受害者的关系**从未被观察到** `true`，
   episode 里没有 `true→false` 翻转（sc_c1 的 obj_blue_2 全程 false→false，只是几何从 0.229 变 0.110）。
   于是 `_pair_satisfied_vs_displaced` 需要 t2f 翻转，在这四例上返回不可达——**P1 把 `sp03/sp04` 的
   `base_case` 换到 sc_c3/dev_c2 是对的**（sc_c3 表里可见 r4 `true` → r5 `false`），
   但它当时给的理由（"机制坏掉"）是错的。

改动（**行为为零**：物理、事件参数、`expected`、预算、几何一字未动）：

- `evaluation/tasks.py`：`_displace_region_event` docstring 补上"这四例实测做了什么、以及不测什么"；
  `state_change` 子集抬头改掉"valid progress later invalidated"这句会被读成 t2f 的说法；
  五处 `notes` 写明实测（含 sc_c2 一条**旧注错误**：共享区实测在 `tray_left` 而非 `tray_right`）。
- 新测试 `test_the_declared_impulse_undoes_the_placement_it_names_and_no_other`
  （`tests/integration/test_episodes.py`，2.1 s，真实物理）：五条断言 = 触发一次且只一次、
  两份读数并存且互不掩盖、其他完成项保持 `true`、事件后只有被策略点名的那一次重抓
  （Runtime 不自动补 pick）、受害者谓词只出现 `false→true` 而不出现 `true→false`（即第 4 条局限被钉住）。
- `notes`/docstring 不进冻结哈希：`cli freeze --check` 仍为 **`2f1c74f52e91`**，预注册清单未被这次裁定改动。

### 1. SPEC 11.4 盲审：准则预注册、机械执行、结果按两个比率公布

P2 §5 的欠账是"`needs_blind_review` 只产出证据指针，没有 rubric，于是'哪个下一步是合理的'这类
读数只能留白"。本节把留白变成**可执行但不由模型自评**的口径。**至今为止没有任何一条人审记录**，
所以本节交付的是机制与实证到的样本量，不是一个 adverse 比率。

**准则文件**：`configs/experiment/blind_review_rubric_v1.json`（`rubric_id: blind-review-v1`，
`registered_before_any_p3_run: true`，sha256 **`95811983da05…`**）。两条 item：
`place_of_already_satisfied`（scale `reasonable|redundant|cannot_tell`，adverse `redundant`）与
`fork_answer_outside_both_label_sets`（scale `reasonable|unreasonable|cannot_tell`，adverse
`unreasonable`）；`sampling` 为 **100%**（指针点名多少审多少，不是抽样）。四条 `prohibitions` 把
"不得自评、不得事后改判据/分母、不得折中分歧、不得把未审当 0"写成文本，`rate_definition` 写明
两个比率各自的分母。
本节内对该文件做过一次**措辞澄清**（`blinding.withheld[0]` 原写"mode / 臂"，会被读成状态分叉的
`arm` 也要掩掉；现明确：实验臂 = A|B|C 掩掉，分叉那一臂连同 `pair_id/difference/provenance/synthetic`
保留，因为它就是被展示的那个世界的形状），并补写 `blinding.kept` 与子串检查的存在。**sha 因此从
`e8a118c60103…` 变为 `95811983da05…`；此刻零条 verdict 存在，故澄清仍在预注册之内**——换版判据从此
只能以新 `rubric_id` + 重跑的形式发生，改字不重跑会让每一条已入库 verdict 自动拒收（下面第 2 条）。

**三条性质被写成代码，不是写成声明**（`evaluation/blind_review.py`）：

1. **盲**：`item_key = sha256(rubric_id|kind|source|locator)[:12]`；`_scrub` 递归丢掉
   `episode_id/case_id/mode/planner/model/provider/policy_label/subset/expected/repeat`（载荷里的同名
   字段一并丢），kind 2 额外丢掉标签集与自动结论本身（那是问题的答案）；`build_sheet` 出表前对每条
   证据做子串断言，命中 `episode_id`/`"mode"`/`policy_label` 就**整表报错**而不是静默出表。
2. **准则冻结**：表上带 rubric 字节的 sha256，每条 verdict 必须回带同一个 sha；不符逐行拒收。
   测试直接演这一幕：把 `adverse_value` 换一个词重建表，旧 verdict 全部拒收（`reviewer_kind != human`、
   表外 key、超出 scale、重复 `(item, reviewer)` 同样逐行拒收并给理由）。
3. **分歧存活**：两名审阅人齐了才算 settled；一致 adverse 才记 adverse；分裂记 `divided` 并原样列出
   两人判定，不平均、不请第三人推翻；任一人 `cannot_tell` 则移出分母单独计数。公布
   `adverse_rate_strict` 与 `adverse_rate_counting_divided_as_adverse` **两个数**，两者之差就是这条读数
   对"分歧怎么处理"的敏感度假如多大；`unreviewed` 点名列出，空分母的比率是 `null` 而不是 0。

**证据补齐**：kind 2 要"看该回答当场被给出的那份载荷"，而诊断记录里原先只有转述字段。
`score_record_shell` 新增 `shown_to_the_policy`（按被试自己的 `feedback_depth` 现算的模型可见载荷）。
实测：重跑 rule 诊断，**已公布的数字一字未动**（`action_fit_rate 0.9167`、`22/24`、
`10 of 12`、`unreachable_pairs []`），行级唯一差异是随机的 `context_id/decision_id`；产物 48 KB → 593 KB。

**本节测出来的缺陷（四条）**：都是"机制看着在跑、其实一直空转"的形状，靠新测试才现形。

- `behaviour_items` 把 episode 日志读成 `run_dir/episodes/events.jsonl`，少了一层 episode 子目录
  （`EpisodeStore` 的第一个参数是**该 episode 自己的目录**，`evaluation/run.py` 就是这么建的）——
  后果是每一条 kind 1 指针都静默落到 `not_reviewable`，表看着建成功了但条目永远是 0。
- "最近的先前 `decision_context`" 实际取的是**第一个**（升序 `next`）：会把第 1 轮的世界拿去给审阅人
  判第 7 轮的决策。现改为取序列上真正最近的前一个，并断言证据里的 `context_id` 等于该决策自己的
  `context_id`。
- 报告侧同形状的一处：`behaviour["_definitions"]["needs_blind_review"]` 是**散文**，旧代码按列表
  迭代，于是 P2 报告里那个"162 条待审条目"其实是 162 个字符。已在 `report.py` 排除 `"_definitions"`，
  并由 CLI 测试钉住（构造一份只含散文定义的 report，断言表上是 1 条而不是几百条）。
  另修 `render_markdown`：`blind_review` 块缺 `rubric` 时不再 KeyError 拖垮整份报告（两个既有报告测试抓到）。

**入口**：`cli blind-review --run-dir D [--sheet P] [--template P] [--records P]`。
退出码分工：0 = 测到了（包括"审出来 adverse"和"没有可审条目"）；3 = 输入不可用（目录/`episodes.csv`
缺失、`--records` 指空、有 verdict 行被拒收）；4 = 表根本建不出来（产物坏了）。
**故意没有 `--rubric` 开关**：准则能从命令行换，就等于事后挪门柱。`--template` 写出的空表每条 verdict
为 `null`，会被逐行拒收——把表单交回来不等于交出结果。

**实测的诚实结论（这条决定 P3-d 的读法）**：现有全部批次上 **0 条待审条目**。
`/tmp/p2_online/report_rule`、`report_deepseek` 的 `needs_blind_review` 指针各 0 条；两份
`state_utilization.json` 各 24 行里 `labels == []` 的 0 行；全仓 4 份 `report.json` 的
`redundant_replacements_of_satisfied_goals` 总和为 0。也就是说：kind 1 需要策略真的去重放一个已
`true` 的关系，kind 2 需要回答落在冻结标签集之外——两者都还没在实测里发生过。因此
**P3-d 的正式批次若仍为 0，报告就照实写 0 条待审、盲审为空集**，不得为了让盲审有数可报去放宽自动
口径的判定线；若出现条目，则两名审阅人的 verdict 必须先入库，报告才允许引用 adverse 比率。

**测试**：新增 `tests/unit/test_blind_review.py`（20 条：表 6 / verdict 6 / 注册文件 1 / 报告渲染 1 /
CLI 4 / 其余 2），证据侧用真实 episode 的 `events.jsonl` 铺成 run 目录，准则读的是注册文件本身而不是
fixture 副本。全量离线套件 **206 passed in 129.09 s**（上一基线 188+2，其中 2 条是 online 跳过项）。
