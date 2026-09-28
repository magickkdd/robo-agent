# compatibility_delta — ALFWorld 文本后端相对 v0.1 桌面实现的改动清单

规格：`Benchmark-Stress-Test-v0.1-Agent-SPEC.md`（下文简称 SPEC-BST）§3.3「所有变化必须列入
`compatibility_delta.md`，逐项记录：旧行为、新行为、必要性、可能影响、验证证据」。
本文件是该要求的唯一落点：凡是被 SPEC-BST §3.4／§3.6 点名"必须披露"的变化，都在这里占一个
编号条目，不存在只写在代码注释里的披露。

状态：**Phase 0／1／2 已完成部分的清单**（Phase 2 退出即筛查发车时随 `frozen_config.json` 一并
复制进 run root，见第 8 节）。Phase 3–5 若再引入兼容性变化，必须新增条目而不是修改旧条目。

代码身份（本清单所描述的树）：

| 项 | 值 |
|---|---|
| commit | `4e960a2d2d189cb6d20e19910f83c6f76361f906`（工作树为脏，改动未提交；"脏"的证据就是下面两行非空的 diff 摘要与未跟踪清单） |
| 跟踪文件 diff 摘要 | `tracked_diff_sha256 = 86732095450ea0c38b90262a6580ddecdc4579ec11b87c9f16123c1949b473e5`（27 个文件；Phase 0 定稿时是 `247d3630…` / 26 个，本清单 D23–D29 描述的 `runner.py`/`aggregate.py`/`cli.py`/`tasks.py`/`sources.py` 改动推到现在的值） |
| 未跟踪文件 | 85 个，逐个记录 `sha256`＋字节数（不记录内容），见 `environment_check.json → repo.untracked`（快照 `checked_at=2026-09-20T03:21:34+0800`）。**本文件不可能出现在那个快照里**（自指），它的权威摘要是 run root 的 `manifest.json → inputs.compatibility_delta_sha256`，由 `freeze_run_root` 在复制副本当时算出 |
| 决策机制哈希 | `rules_sha256 = 23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3`（与 v0.1 冻结预注册文件逐项相等；D24 改 `evaluation/sources.py` 前后各测一次，均为该值） |
| 文本后端提示词 | 标签 `bst-decide-text-v1`，摘要 `prompts_sha256 = 25502db7697ffd8e179ade40facbcd327a74134d014fb28516b60bcb1a520512`（dev2 每集 `model_identity.prompts_sha256` 与之相等） |
| 原生命令目录摘要 | `catalogue_sha256 = f5044d67c0bb696ad40f01f9310c7e1bebb704f7d3aa0d9b217292abda8917de` |
| 被试 | `provider=agnes`、`model=agnes-2.5-flash`、`temperature 0.2`、`max_tokens 2048`、`timeout 60 s`、`max_retries 4`；配置文件 `configs/models/bst_text_agnes.yaml`（只记 `api_key_env` 的变量名，无凭证、无完整环境变量） |

---

## 0. 一句话结论

v0.1 的**决策机制**（每轮一次决策、一次最多一个技能、四种控制动作、反馈窗口 3、AttemptRecord
构造与 12 条上限、schema/语义/网络错误的计费与修复预算、无效重复守卫算法与阈值、预算检查与
控制终止）在这棵树上**没有被改**：`rules_sha256` 与冻结预注册逐字节相等，桌面回归
`356 passed, 20 skipped`。被改的是**这个机制所接触的世界**：观测从特权几何换成了环境原话，
动作后端从 PyBullet 控制器换成了 12 条原生命令，目标从 assignment 列表换成了逐字保留的指令句子，
进展判断换成显式 `unknown`，完成语义从"真值拒绝 finish"换成"agent 自己的终止声明"。
因此最终结论只能写成 **"v0.1 决策机制经环境适配后的表现"**（SPEC-BST §3.4 末段），
桌面分数与文本分数之差不可解释为架构性能变化。

---

## 1. SPEC-BST §3.4 五项强制披露（D1–D5）

### D1 任务入口：原始 benchmark 指令逐字保留，桌面目标解析器不参与

| 字段 | 内容 |
|---|---|
| 旧行为 | 桌面 episode 的目标是 `GoalSpec.assignments`：一串 `(entity_id → target_id)` 配对。任务文本先经目标解析（`DeepSeekPlanner` 的 `goal` 提示，标签 `s2-goal-v1`）得到配对列表，`GoalSpec.well_formed` 由 assignments 是否解出决定，`Runtime` 每轮对它调 `verifier.progress(goal)` 得到**逐目标**的 true/false/unknown 行。 |
| 新行为 | `TextGoalSpec`（`embodied_agent/benchmark/state.py`）携带 `instruction: str` = reset 原话逐字，`assignments = []`，并有 `assignments_not_applicable = "no desktop entity/target mapping exists for a text game"`；`well_formed` 改为"指令非空且不含歧义标记"。指令同时出现在每轮 payload 的 `task.utterance` 与 `goal.instruction` 两处（`benchmark/prompts.py:100-107`）。**没有**新增一次模型目标解析调用，**没有**引入 PDDL 目标编译器，**没有**把指令拆成子目标。 |
| 必要性 | 文本游戏的goal 是一句自然语言（例：`put some saltshaker on drawer`），不存在可解析的 entity/target 配对表；为其增加一次模型解析调用会改变每轮决策次数与计费口径（违反 §3.2"同一单步决策方式"），把指令编译成 PDDL 目标则会读入隐藏事实（违反 §4.5）。 |
| 可能影响 | 目标形式化质量与桌面不可比：桌面有显式子目标行可判断"完成了几何条件"，这里没有。桌面目标解析器在本阶段**未被评价**，其缺陷/优点都不能从文本结果推断。任务理解仍然每轮发生在 `Decision.rationale` 里。 |
| 验证证据 | `tests/unit/test_bst_parser.py::test_the_instruction_is_kept_word_for_word`；`tests/integration/test_bst_alfworld_live.py::test_a_real_reset_is_the_official_observation_and_nothing_more`（实测指令字符串 `put some saltshaker on drawer`，且 payload 无 `Available commands` 段）；`tests/contract/test_bst_text_backend.py::test_the_verifier_of_a_text_task_always_answers_unknown`。 |

### D2 观测来源：特权全局几何 → 原生局部文本

| 字段 | 内容 |
|---|---|
| 旧行为 | `WorldState.source = Source.privileged`：`build_world_state(scene, …)` 从持久仿真真值读出全部物体的 `pose`/`geometry`/`held`/`support`/`occupancy`。 |
| 新行为 | 新增 `Source.local_text`；`text_world_state()` 只把**这段文本明确陈述**的事实放进快照：`facts: list[TextFact]`（每条带 `observation_ref` 与字符 `span`）、`location`、`held_object`、`entities[*].attributes`；`pose=None`、`occupancy=[]`、`targets=[]`、`supported_by="unknown"`、`at_rest="unknown"`。原文完整保留在 `raw_observation`，读不懂的句子逐字留在 `unparsed`，并由 `parse_status ∈ {parsed, partial, unparsed}` 计数（SPEC-BST §4.4）。 |
| 必要性 | 该环境不提供几何真值给 agent 侧；把上一轮读过的内容当成"当前仍然如此"会凭空制造未观察事实（§4.2 明确禁止"自动沿用上一轮为当前事实"）。 |
| 可能影响 | 未观察事实一律 `unknown`，因此模型的搜索行为、失败归因、回看请求都与桌面不同分布；`parse_status` 成为桌面没有的新失败面（解析覆盖不到 ≠ 环境没说）。 |
| 验证证据 | `test_bst_text_backend.py::test_a_response_no_rule_reads_is_unparsed_not_empty`、`::test_the_snapshot_carries_no_privileged_and_no_geometry_field`、`::test_no_holding_report_is_not_an_empty_hand`、`::test_a_fact_stops_being_current_when_the_text_stops_stating_it`；`test_bst_parser.py::test_no_geometry_position_or_support_is_invented_for_a_text_thing`、`::test_the_whole_of_an_unreadable_text_is_unreadable_rather_than_empty`；live `::test_a_real_payload_carries_no_privileged_and_no_geometry_key`。 |

### D3 动作后端：桌面运动技能 → ALFWorld 原生命令（保留 SkillCall 层）

| 字段 | 内容 |
|---|---|
| 旧行为 | `SkillRegistry.CATALOGUE` 四个技能 `observe / pick / place / safe_retreat`，技能体做槽位解析、IK、PyBullet 力控与 settle。 |
| 新行为 | `benchmark/native_actions.py::CATALOGUE`（`native_actions.py:43`，12 项：`alfred_go/take/move/open/close/use/clean/heat/cool/examine/inventory` + 复用 `observe`）每项对应**一条**命令模板；`SkillName` 联合类型（`core/contracts.py:397-403`）增加这 11 个名字，删除路径为 0——四个桌面名字原样保留。渲染只做字符串模板填充，`object_id`/`target_id` 必须是环境写过的 `<name> <n>` 实例引用（`INSTANCE_RE`）。 |
| 必要性 | 桌面运动技能在文本环境里没有对应物；不替换就无法把一次 `SkillCall` 变成一个 `env.step`。保留 SkillCall 层是 §3.4-3 的显式要求：不能说"物理控制器已迁移"。 |
| 可能影响 | 命令本身是基准的高层抽象（`go to cabinet 5` 内部含导航；`clean mug 2` 内部含开水龙头），这层抽象属于环境而非本 Agent；`env_actions` 与"物理动作步数"不可跨环境比较（§3.5 末段：记录这种抽象，不额外拆装）。 |
| 验证证据 | `test_bst_native_actions.py::test_every_catalogued_skill_renders_exactly_one_command`、`::test_no_skill_body_is_issued_and_the_refusal_says_which_backend_it_belongs_to`（对 `pick/place/safe_retreat` 明确拒绝并说明属于桌面后端）、`::test_the_environment_offers_no_put_verb_and_none_is_invented`；live `::test_one_legal_skill_call_is_exactly_one_real_environment_step`（`env_steps == skill_calls == 3`，`decision_rounds == 4`）。 |

### D4 进展：没有可见证据的目标进展是 unknown，不接官方隐藏子目标

| 字段 | 内容 |
|---|---|
| 旧行为 | `RuntimeVerifier.progress(goal)` 按几何谓词逐条给 true/false，`_episode_success` = 全部 true。 |
| 新行为 | `TextVerifier.progress()` 恒返回**一行** `unknown`：`entity_id="task"`，`predicate_id="alfworld_task_instruction_satisfied"`，`unmeasured=["no public text states the task instruction is satisfied", "the official success flag is not an observation"]`。`AlfredRuntime._episode_success` 因此**不看** progress 行，只看官方 `won`（在密封之后读，见 D5）。 |
| 必要性 | 该基准的公开观察里没有任何句子声明"任务已完成"；要给出 true/false 只能去读隐藏的 `won`/`reward`/PDDL 子目标完成度，那是把评分器当观测（§4.5、§4.6 禁止）。 |
| 可能影响 | 模型看不到"还剩几个子目标"，也就没有任何进度信号可依赖；桌面那套 progress→决策 的反馈链路在此**必然**变弱。`EpisodeResult.artifacts["unknown_goals"]` 在文本后端恒 ≥1。 |
| 验证证据 | `test_bst_text_backend.py::test_the_verifier_of_a_text_task_always_answers_unknown`；`test_bst_parser.py::test_the_end_words_are_lifecycle_not_a_grade`（`you did it`/`you failed` 只作为生命周期事实，不生成进度）。 |

### D5 完成语义：finish 不再被真值拒绝，而是 agent 自己的终止声明

| 字段 | 内容 |
|---|---|
| 旧行为 | `Runtime._finish_check` 用几何验证器判定 `report.overall == true` 才接受 finish；不接受时 `FailureCode.FINISH_REJECTED`、`false_finish_attempts += 1`，最多 `MAX_FALSE_FINISHES=3` 次后终止。 |
| 新行为 | `AlfredRuntime._finish_check`（`runtime_alfred.py:303`）**总是接受**并记一次 `finish_check` 事件（含当时的 `official_won` 引用），episode 立刻结束、不再向模型要第二次反馈；`FINISH_REJECTED` 在本后端**从不产生**（§3.4-5、§6.2）。官方 `won` 只在 `_finalize` 密封时刻读取，用于 `AGENT_FINISH` 时把 `terminal_status` 改判为 success/failed 并记 `terminal_status_restated` 事件（`runtime_alfred.py:393-406`）；`false_finish` 作为独立字段报告。 |
| 必要性 | 评分 oracle 不能变成可反复查询的反馈源，否则 Agent 会用 finish 试探评分器（§3.4-5）；同时官方 done 绝不能回流进决策链（§6.1）。 |
| 可能影响 | finish 的代价结构变了：桌面是"错三次才出局"，文本是"一次定局"。因此文本批次的 `false_finish` 不能与桌面的 `false_finish_attempts` 直接对比。 |
| 验证证据 | `test_bst_text_backend.py::test_an_agent_finish_ends_the_episode_without_another_request`、`::test_the_environment_done_and_the_agent_finish_are_different_records`、`::test_a_finished_episode_the_environment_did_not_win_is_a_false_finish`；live `::test_an_agent_finish_on_a_real_game_is_the_agent_s_own_word`、`::test_the_engine_truncates_at_its_own_action_budget`（60 步耗尽 ⇒ `ENV_TERMINATED`，`false_finish=False`）。 |

---

## 2. SPEC-BST §3.6 必须披露的校验职责变化（D6）

### D6 本地校验只验 schema／参数形状／公开命令语法；语义前提交还环境

| 字段 | 内容 |
|---|---|
| 旧行为 | `Runtime._validate_decision()` = 信封校验 + `PlanValidator(world, config).validate(PlanStep)`：几何可达性、槽位可行性、IK 可解性、`held` 为 unknown 即拒绝 pick/place、候选 `candidate_id` 必须在列。被本地拒绝的调用**零**次环境交互。 |
| 新行为 | 拆成两个显式钩子：`Runtime._validate_envelope()`（`core/runtime.py:545`：`context_id`/`goal_ref`/`based_on_state_version` 等对"本轮"的声明，任何后端都跑，`AlfredRuntime` 不覆写）与 `Runtime._validate_execution()`（`core/runtime.py:567`：桌面几何前置）。`AlfredRuntime._validate_execution`（`runtime_alfred.py:290`）只验三件事：冻结 schema 已过（`Decision` 构造）、参数键与模板一致（`native_actions.validate_call`）、命令能渲染成一条合法原生命令；并对非空 `candidate_id` 直接拒绝。槽位／IK／几何可达／"held unknown ⇒ 拒"这些桌面守卫在文本后端**不生效**，且删除它们的分支**不在** `Runtime` 的通用路径里——`Runtime._validate_execution` 仍是原样几何校验，桌面行为逐字节未变。 |
| 必要性 | §3.6 原文："本后端本地校验只验证冻结 schema、参数结构与公开命令语法；环境负责动作语义前提。这一校验职责变化必须进入 compatibility_delta，不能藏在通用 Runtime 分支中。" 若在通用分支里加"文本世界跳过几何"判断，等于让 Adapter 替模型判断可行性，违反 §3.5。 |
| 可能影响 | 计数分布变了：一次语义不成立的命令（对象不在这里、容器关着）在桌面是"本地拒绝、0 步、计入 `rejected_decisions` 与语义修复预算"，在文本后端是"发出去、环境回答 `Nothing happens.`、计 1 次 skill_call + 1 次 env step + 1 次反馈"。因此 `rejected_decisions`、`skill_calls`、`env_actions` 三个计数器在两个环境里的语义不同，跨环境比较只能比"预算内做完没做完"，不能比计数值。 |
| 验证证据 | `test_bst_text_backend.py::test_a_name_the_model_invented_is_answered_by_the_engine_not_repaired`（模型发明的 `mug 9` 被原样送进环境，不模糊匹配成 `mug 2`）、`::test_a_semantic_failure_still_costs_exactly_one_step`；live `::test_a_refusal_sends_nothing_and_costs_no_environment_step`（仅**本地 schema** 拒绝 ⇒ 1 次决策、0 次 step）；`test_bst_native_actions.py` 全部 55 项（缺参/多参/非字符串/非实例引用/多余空白各自的拒绝形状）。 |

---

## 3. 其余允许的改动（SPEC-BST §3.3 分类）

### D7 数据定位与版本记录（环境初始化）

- **旧**：无 ALFWorld 相关记录。
- **新**：`benchmark/envcheck.py` 产出 `environment_check.json`：host/python、`pip_freeze_sha256`、包版本（alfworld 0.4.2 / textworld 1.6.2 / tatsu 5.8.3 / jericho 3.3.1 / fast_downward_textworld 20.6.4 / numpy 2.4.6 / spacy 3.8.16 / pytest 9.1.1）、`data.archive_manifest`（两个 zip 的 url+sha256+bytes：`json_2.1.1_json.zip` `25171f16…`、`json_2.1.2_tw-pddl.zip` `eea90499…`）、`data.config_used`（AlfredTWEnv 实际读的 key/value）、`filesystem.*`（逐 game 的 `game_tw_pddl_sha256`/`traj_data_sha256`）、`official_collection.*`（3553/140/134 与六类分布）、`playthrough`（真实 reset/step，`initial_observation_sha256`，`reward_was_none`）、`repeat_stability`（3 次 reset 文本同摘要 ⇒ 确定性）、`licenses`、`errors: []`、`exit_criteria` 四项全 true。
- **必要性**：§5.5 要求 run 记录能定位数据与版本；本树工作区是脏的，只记 commit 无法复现。
- **可能影响**：无运行时影响；只增加产物体积（未跟踪文件仅记摘要，不记内容，凭证不可能进入）。
- **证据**：`checked_at=2026-09-20T00:58` 那一版之后重跑（新增 `repo.tracked_diff_sha256`、`repo.untracked`、`licenses`、`packages.pytest`），`errors` 仍为 `[]`。

### D8 反馈映射：环境原话进 feedback，state_diff 改为"陈述之差"

- **旧**：`ExecutionFeedback.state_diff` 来自 `diff_states()`（位移、支撑变化、接触数）；`rejection_reasons` 只在 `rejected` 时携带 notes；无原文字段。
- **新**：`core/contracts.py` 给 `ExecutionFeedback` 增加 `environment_text: Optional[str]`（文本后端的原话；桌面无此文本，恒 None），`short()` 只在非 None 时输出该键；`Runtime._build_feedback` 统一填 `environment_text=post_world.raw_observation`（桌面为 None，因此 payload 不变）；`text_state_diff()` 返回同名字典键（`moved` / `state_changed` / `held_*` / `location_*` / `same_response` / `note`），其中 `moved` 表示"被陈述的持有者换了"，并显式声明"这里没有测量任何位置"。
- **必要性**：§3.3 允许"环境反馈到现有反馈结构的映射"；沿用同名键让冻结循环、冻结 `short()`、冻结窗口深度都不必改。
- **可能影响**：`state_diff["moved"]` 在两个环境里的含义不同（事实层 vs 几何层），报告里凡引用它都必须带环境标注。
- **证据**：`test_bst_text_backend.py::test_unread_text_is_kept_verbatim_and_labelled`、`::test_a_repeated_sentence_is_a_new_measurement_but_not_new_facts`；`test_bst_parser.py::test_a_refusal_is_the_environment_s_own_fact_and_is_not_widened`。

### D9 观测缓存与 observation_ref：同一次公开响应 = 同一个 ref

- **旧**：每次 `observe()` 都 `self._obs_seq += 1` 并生成新 `obs_%04d`，因为读一次持久真值确实是新测量。
- **新**：`AlfredRuntime.observe()`（`runtime_alfred.py:115`）以 `(raw_text, env_steps)` 为缓存键：同一条响应被读第二次时复用 `observation_ref`（不凭空造第二个测量），而 once a step lands, 即使新文本与旧文本逐字相同也**是**新 measurement——`_record_feedback`（`runtime_alfred.py:342`）据此纠正 `new_measurement`，并保证"重复的句子有新事实"与"新轮次无新事实"两种情形不被混淆。`text_fingerprint()` 刻意剔除 `observation_ref`，所以 §3.2 的重复守卫仍然只看"有没有新证据"，不看版本号。
- **必要性**：§4.3「同一条缓存响应不得被当成两次测量」＋§5.4「新 step 可以重复文本仍是新版本」。桌面两条规则都不触发，因为没有"重发同一段文本"这个通道。
- **可能影响**：`state_version` 与 `observation_ref` 不再是简单递增对应；重复守卫的输入换成了文本指纹（见 D18）。
- **证据**：`test_bst_text_backend.py::test_reading_the_cached_response_takes_no_step_and_invents_no_ref`、`::test_a_repeated_sentence_is_a_new_measurement_but_not_new_facts`。

### D10 不生成候选列表（`_context_candidates → ([], 0)`）

- **旧**：`_build_context` 对前 4 个 pending 目标各生成最多 `PER_GOAL_CANDIDATES=3` 个几何候选，`candidates_truncated` 记录被截断数。
- **新**：`AlfredRuntime._context_candidates` 返回 `([], 0)`；payload 里 `candidates=[]` + `candidates_note="no dynamic candidate list is offered by this backend"`（`prompts.py:139-141`）；模型若给 `candidate_id` 非 null，本地拒绝（D6）。
- **必要性**：§4.5 禁止"动态候选列表"（那等于把合法动作算好递给模型）；文本后端没有可枚举的槽位几何。
- **可能影响**：桌面模式 B 的"候选选择"能力在此**没有对应物**，两环境可比的部分只剩"每轮自主决定一条命令"。
- **证据**：`test_bst_text_backend.py::test_no_candidate_list_is_invented`。

### D11 提示词替换（§3.3"单纯环境说明与动作目录的提示词替换"）

- **旧**：`s2-decide-v1` 系统提示（`adapters/deepseek.py` 三个常量，被 I1 不变式按 needle 保护）描述桌面几何/槽位/候选。
- **新**：`benchmark/prompts.py::BST_DECISION_SYSTEM/USER`，标签 `bst-decide-text-v1`，摘要 `25502db7…`；内容只讲这个世界的事实：一条技能=一条命令、名字必须逐字引用环境写过的、`Nothing happens.` 的含义、每轮 world 只含本段文本陈述、progress 恒 unknown、finish/blocked/clarify 立即结束、没有坐标/候选/合法动作/奖励。**不含**任何示例计划、admissible commands、任务类型标签。桌面三个常量一字未动。
- **必要性**：§3.3；把桌面提示原样发给文本任务会要求模型输出它无法成立的空间参数。
- **可能影响**：提示不同是跨环境分数差的直接来源之一，必须在结论里并列声明；提示摘要随每集 `model_calls.jsonl` 记录，可离线区分。
- **证据**：`rules_sha256` 未漂移（`_prompts()` 在哈希内，桌面提示任何改动都会漂移）；`test_bst_text_backend.py::test_evaluation_only_values_never_reach_the_decision_chain` 对真实 payload 断言 `BANNED_IN_PAYLOAD` 九个键（`admissible_commands/expert_plan/task_type/reward/won/score/split/solution`）不出现。

### D12 `TextDecisionContext` 只替换渲染

- **旧**：`DecisionContext.model_payload()` 一份，含几何。
- **新**：`Runtime.context_class = DecisionContext`（`core/runtime.py:481`），`_build_context` 用它构造；`AlfredRuntime.context_class = TextDecisionContext` 仅覆写 `model_payload`。记账（`store.log("decision_context", …)`）、校验、预算扣减全部走原路径。
- **必要性**：§3.2 冻结的是机制；模型看到的 payload 属于机制的输入端，只能换渲染，不能换循环。
- **可能影响**：文本 payload 字段更多（`facts/unparsed/parse_status`），prompt token 结构与桌面不同。
- **证据**：`_build_context` 未被 `AlfredRuntime` 覆写逻辑之外使用（`runtime_alfred.py:257` 仅补 `budget` 视图）；live `::test_a_real_payload_carries_no_privileged_and_no_geometry_key`。

### D13 后端生命周期接缝（11 个钩子）

- **旧**：`Runtime` 直接调用 `build_world_state / RuntimeVerifier / SkillRegistry.CATALOGUE / diff_states / TerminalSnapshot.capture / _world_fingerprint / PlanValidator`。
- **新**：新增 `_capture_world / _verifier / _verification_reports / _skill_catalogue / _fingerprint / _state_diff / _capture_terminal_snapshot / _terminal_progress / _backend_termination / _episode_success / _preflight_trip`（`core/runtime.py:185-235, 489`），桌面默认实现就是原来的直接调用；`AlfredRuntime` 覆写其中 11 个。**没有**新增第二套决策循环，`Runtime.run_episode` 的轮次结构、请求点、计费点未变。
- **必要性**：§12.1 明确允许"接入后端的观察缓存、执行与终止；保留现有决策调用和历史窗口"；不另写一个功能相似的新 Agent 是 §3.1 的硬要求。
- **可能影响**：钩子是新的抽象面，若将来在钩子里做策略就违反 §3.5；因此每个钩子的 docstring 都写明"只测不选"。
- **证据**：`git diff HEAD -- embodied_agent/core/runtime.py` 全部为"抽出调用点"（除 `environment_text`/`termination_reason`/`_preflight_trip` 三个新增点）；桌面回归 `356 passed, 20 skipped`；`rules_sha256` 相等；I4 不变式（`eval_spec` 不得出现在 `core/runtime.py`）仍通过。

### D14 `_backend_termination()`：世界自己结束 episode

- **旧**：episode 只能被 Agent 的控制动作、守卫或预算终止。
- **新**：`Runtime` 在动作落地并记账后调用一次 `_backend_termination()`（`core/runtime.py:455`），返回 `(status, failure_code, note)` 即结束。`AlfredRuntime` 用它读两件事：后端的 `error`（⇒ `ENVIRONMENT_ERROR`，见 D16）与 `env_done`（⇒ `ENV_TERMINATED`）。返回值**从不**写进 `DecisionContext`。
- **必要性**：§6.1/§6.2 要求 `ENV_TERMINATED` 成为独立终止原因，同时官方 done 不得回流反馈。
- **可能影响**：文本环境会在 60 步处自己收摊（`ENV_TERMINATED` + `score_complete_success=False`），与桌面"预算耗尽 ⇒ BUDGET_EXHAUSTED"不同名，报告须分开统计。
- **证据**：live `::test_the_engine_truncates_at_its_own_action_budget`（只有一条 `termination` 事件，reason=ENV_TERMINATED）；`::test_the_environment_done_and_the_agent_finish_are_different_records`。

### D15 `EpisodeResult.termination_reason` + 十个终止名

- **旧**：只有 `terminal_status` + `failure_type`。
- **新**：`core/contracts.py` 增加 `termination_reason: Optional[str]`；`AlfredRuntime._termination_of()`（`runtime_alfred.py:359-386`）按优先级产出 §6.2 的十个名字：`ENV_TERMINATED / AGENT_FINISH / AGENT_BLOCKED / NEEDS_CLARIFICATION / REPEAT_GUARD / BUDGET_EXHAUSTED / MODEL_ERROR / ENVIRONMENT_ERROR / ADAPTER_ERROR`（`INVALID_DECISION` 依 `note` 分流到 `BUDGET_EXHAUSTED` 或 `MODEL_ERROR`，两个 §6.2 名字不被合并）。桌面运行该字段为 `null`。
- **必要性**：§6.2「必须能说出是谁结束了这一集」；为生命周期事件新造物理失败码会污染桌面 taxonomy。
- **可能影响**：新增契约字段（向后兼容：可选、默认 None，桌面产物不写它）。
- **证据**：`test_bst_text_backend.py::test_no_substitution`（⇒ `BUDGET_EXHAUSTED` + note `"semantic repair budget"`）、`::test_control_outcomes_seal_the_episode_at_once`、`::test_an_environment_fault_ends_the_episode_without_replaying_the_command`、`::test_every_cost_counter_survives_into_the_result`。

### D16 环境故障不重放（§6.3）

- **旧**：executor 在技能内做有界重试/恢复。
- **新**：`benchmark/backend.py` 捕获引擎异常时把 `{phase, type, message}` 记进 `eval_view()["error"]`；`AlfredRuntime._backend_termination` **先于** `env_done` 检查该键（一个故障和一个生命周期事件可能同到一个响应里，只有一个是世界的测量），终止语明确写"命令是否到达引擎不可知，因此不重放试探，本轮按基础设施而非模型失败计分"。冻结循环把 `failed` 结果的 notes 丢出模型可见反馈，因此 `runtime_alfred._build_feedback`（`runtime_alfred.py:325`）另记一条 `backend_fault_detail` 事件到集旁（模型可见行**不改**）。
- **必要性**：§6.3；静默重试=Adapter 替模型行动（§3.5），删除 notes 又会让"不可重放"这条要求事后无法审计。
- **可能影响**：`ENVIRONMENT_ERROR` 的集要按 §6.4 判基础设施并允许至多一次替换。
- **证据**：`test_bst_text_backend.py::test_an_environment_fault_ends_the_episode_without_replaying_the_command`（断言 `backend_fault_detail.notes` 与 `"never replayed"` 出现在 result note，且 env_steps 未增加）。

### D17 预算来源与新增上限（§5.3）

- **旧**：`Budgets` 八项来自 v0.1 配置（`max_decision_rounds=24 / max_http_requests=32 / max_skill_calls=30 …`）。
- **新**：`benchmark/runtime_alfred.py:71-93` 定义 §5.3 的统一数值 `BST_ENV_ACTIONS=60, BST_DECISION_ROUNDS=80, BST_HTTP_REQUESTS=90, BST_EPISODE_TOKENS=600_000, BST_HTTP_TIMEOUT_S=60.0, BST_COMMAND_TIMEOUT_S=10.0, BST_EPISODE_WALL_S=900.0, BST_SETTLE_S=0.0`；`bst_budgets()` 把可表达的四项装进**未改一字**的 `Budgets`（env actions ⇒ `max_skill_calls`，decisions ⇒ `max_decision_rounds`，HTTP ⇒ `max_http_requests`，wall ⇒ `max_wall_time_s`）。**`Budgets` 字段集合冻结未动**：给不可变契约加字段会让 v0.1 批次的 `rules_sha256` 漂移、批次不可判分。
- **必要性**：§5.3 要求两个环境用同一组数值，而 §3.2 要求核心机制与契约不修订。
- **可能影响**：`env actions 60` 恰好等于文本引擎自身的 step cap（见 D19）；`decision_rounds 80 / HTTP 90` 都大于旧值，因此文本批次单集上限比 v0.1 高，跨环境比较只能比成功率与成本，不能比"是否撞同一堵墙"。
- **证据**：`test_bst_text_backend.py::test_the_environment_action_cap_is_the_skill_call_cap`、`::test_the_decision_round_cap_is_the_frozen_one`、`::test_the_http_cap_binds_before_the_request_that_would_pass_it`（实测 cap=6 时 `http_requests==10`、calls=2 ⇒ 保守预检确实提前刹车）、`::test_the_episode_token_cap_uses_a_pre_request_estimate`。

### D18 重复守卫的输入换成文本指纹（算法与阈值未动）

- **旧**：`_world_fingerprint(world)` 用位姿、支撑、槽位、held 几何量。
- **新**：守卫 `_repeated_without_new_evidence()`、`identical_invalid`/`identical_repeats_total` 计数、阈值全部是原代码；`AlfredRuntime._fingerprint` 换成 `text_state_fingerprint()` =（location、held_object、实体属性集、事实键多重集），其中 `fact_key` **不含** `observation_ref`。
- **必要性**：§3.2「同一无效重复守卫算法及阈值，不得为提高分数修订」＋§5.4「不得用 state_version 抵消无新证据的重复」。文本世界没有几何可哈希。
- **可能影响**：判"无新证据"的标准变成"这段话陈述的事实集合没变"，环境重发同一段文本 ⇒ 守卫判为重复（这是 §4.3 想要的效果）。
- **证据**：`test_bst_text_backend.py::test_a_repeated_sentence_is_a_new_measurement_but_not_new_facts`；`test_bst_parser.py::test_facts_are_named_by_kind_and_never_depend_on_a_snapshot_that_changed`；阈值来源仍是 `rules_sha256` 覆盖的同一份代码。

### D19 记录：官方参考解本身超过 60 步

- **旧**：不适用。
- **新**：Phase 0 用 `handcoded` 官方参考解跑通 12 个 dev game，采到 9 条在 60 步内到达 `done` 的获胜轨迹（已固化为 `tests/fixtures/bst_alfworld_expert_replay.json`，共 104 条动作，轨迹内 `help` 查询 0 次），另有 **3 条记到 60 步仍未 `done`** 而被排除、只登记不运行。
- **必要性**：§13 的"每集 60 环境动作"必须有人先量过它在参照解下的紧度，否则筛查分数会被误读成架构上限。
- **可能影响**：任何以这 3 个 game 做上限讨论的结论都必须带"参照解亦未在此预算内完成"这一句。
- **证据**：fixture 的 `skipped` 段与 `env_actions_to_done` 字段；`test_bst_alfworld_live.py::test_the_expert_traces_that_did_not_finish_in_the_budget_are_recorded_not_run`；9 条重放 ⇒ `decision_rounds == len(skills)`、`rejected_decisions == 0`、`ENV_TERMINATED`、成功（`::test_an_official_expert_win_replays_through_the_loop`）。

### D20 终端快照：文本世界没有 settle 几何

- **旧**：`TerminalSnapshot.capture(scene, settle_time_s, …)` 让物理稳定后读一次真值。
- **新**：`AlfredRuntime._capture_terminal_snapshot` 返回一个不带几何的快照（`settle_s=0.0`），`_finish_check` 随后用 `observe()` 的真实 `observation_ref/state_version` 覆写它，使两个判据（Agent 判分与官方判分）来自**同一份**样本（§7 的单快照要求）。
- **必要性**：文本世界"再 settle 一会儿"不会改变任何句子；空等只浪费时间预算。
- **可能影响**：`terminal_snapshot` 里没有位姿；任何要求位姿证据的下游分析在文本批次为 not_applicable。
- **证据**：`test_bst_text_backend.py::test_the_snapshot_carries_no_privileged_and_no_geometry_field`。

### D21 契约枚举只做加法

- **旧**：`Source ∈ {privileged, sensor}`；`FailureCode` 无环境侧成员；`SkillName` 四个。
- **新**：`Source.local_text`、`FailureCode.{ENV_TERMINATED, ENVIRONMENT_ERROR, ADAPTER_ERROR}`、`SkillName` + 11 个 `alfred_*`。**没有任何既取值被改名或删除**，桌面代码路径不会构造新值。
- **必要性**：§3.3 允许新环境必要的状态属性与可空值；改名/删除会破坏 v0.1 产物的可读性。
- **可能影响**：下游按枚举穷举分支需处理新成员（本仓库内的分支点已全部补齐并回归通过）。
- **证据**：桌面回归 `356 passed, 20 skipped`；`test_bst_native_actions.py::test_the_catalogue_documents_every_skill_and_only_those_skills`。

### D22 端点身份记账（provider 标签）

- **旧**：`DeepSeekAdapter.provider` 硬编码 `"deepseek"`；`DeepSeekPlanner` 不暴露 provider。
- **新**：`provider` 成为构造参数（默认仍是 `deepseek`），从配置的 `provider:` 读取；`DeepSeekPlanner` 把 `adapter.provider` 挂到自己身上，供 manifest 与每集账本记录"这个 adapter 实际在跟哪个端点说话"。附带 `strip_env_quotes()`：`. ./.env` 由 shell 去引号，自行解析 `.env` 时留着引号会发出带字面引号的 bearer token 并收到无法说明原因的 401。
- **必要性**：§5.5 要求记录真实模型身份；v0.1 的密钥配置字节被冻结预注册哈希锁住，换端点只能是新文件（`configs/models/agnes.yaml`，不在任何 `rules_sha256` 内）。
- **可能影响**：桌面默认行为不变（不传 `provider` ⇒ 仍是 `deepseek`）；带 `--prereg` 强制判分的批次会因为模型/base_url 非冻结值而被 `matrix_mismatch` 拒绝——这是**期望**行为，不是兼容性回归。
- **证据**：`tests/unit/test_preregistration.py`、`tests/test_mvp.py` 等桌面回归全绿；`configs/models/agnes.yaml` 只记 `api_key_env` 变量名，无凭证。

### D23 Phase 2 运行入口：`benchmark/cli.py`（§12 五条命令的落点）

- **旧**：只有 `embodied_agent/cli.py`（桌面实验）与直接 `import` 调用 `runner.run_slot/run_batch`；§12 要求的"环境自检 / 12 集开发校验 / 48 集筛查 / 全量续跑 / 离线聚合"没有可执行入口。
- **新**：`embodied_agent/benchmark/cli.py` 提供 `env-check`、`tasks`、`run --segment dev|screening|full`、`aggregate` 四个子命令，每条都写明 `--config` 与 `--out/--run-root`。段与 token 硬顶写死为 `SEGMENTS`：`dev_train` 无上限、`screening` 8,000,000、`full` 40,000,000（§5.3）。入口是 `python -m embodied_agent.benchmark.cli`，**没有** `__main__.py`，因此 `python -m embodied_agent.benchmark` 会失败——这是替代而非补齐，写进第 6 节。退出码 0/3/4 分别表示"批次跑完（哪怕全错）/配置错/基础设施工位未闭合"；一个每集都失败的批次仍然返回 0，因为低成功率是被测对象而不是命令错误（§13-8）。
- **必要性**：§12 交付要求给出真实可执行命令；把段与硬顶放在代码常量里，是为了让"发一次筛查"不可能误带上全量上限。
- **可能影响**：无运行时影响；`envcheck.main(argv)` 现在可被复用（原来只有 `if __name__`）。
- **证据**：dev 段真实跑通（`/tmp/bst_v01/dev2`，12/12 写入 §10.4 四类产物）；`aggregate` 产出 `metrics.json`/`metrics_by_task_type.csv`/`failure_candidates.jsonl`。

### D24 拒绝的决策仍然花钱：错误路径的 `model_calls.jsonl` 行补上 `usage`

- **旧**：`evaluation/sources.py::LLMDecisionSource.decide` 的异常分支只写 `http_requests_this_call`、`error`、`error_reasons`、`raw_response`，**没有** `usage`。而 runtime 计费读的是 provider 累计计数器（`core/runtime.py:595-600`），所以一次"HTTP 成功但 payload 被拒"的往返**已被计入本集 token**，却在 `model_calls.jsonl` 里查不到数额。
- **新**：错误行附带 `usage = {prompt_tokens, completion_tokens}`，取自该次调用前后 provider 计数器的差，并标注 `"source": "provider counter delta across the failed call"`。成功行的形状一字未改。
- **必要性**：§13-9 要求报告能只由密封日志重算。缺了这个字段，离线重算的 token 永远**低于**封存值，第 9 项验证会退化成为差异编造解释。
- **可能影响**：只增加日志字段；不改变任何决策、计费、终止或阈值（该文件不在 `rules_sha256` 的哈希集合内，改动前后 `23abe58f…aae7b3` 逐字节不变，已实测）。D24 之后的第一批（dev2）是在修复**之前**跑的，因此它的 6 集差异被保留并在 `metrics.json → token_authority` 里点名，而不是重跑掩盖。
- **证据**：`/tmp/bst_v01/dev2/metrics.json`：`sealed_tokens_total = 276,434 = ledger_tokens_used`，而 `recomputed_tokens_total = 163,399`，且差异恰好只出现在 `billed_calls_without_usage > 0` 的 6 个 episode；修复机制由一次性脚本验证（错误行的 `usage` 与该次调用的计数器增量相等）。

### D25 每集 token 记账改为"本集增量"，基础设施行同样记账

- **旧**：`runner.run_slot` 的 `tokens` 块里混用了 adapter 的**进程累计**计数器；一个复用 adapter 的批次会把之前所有集的开销再记一遍到本集的账上。
- **新**：`tokens.prompt/completion/total/http_requests/decision_rounds` 全部取自 `EpisodeResult`（runtime 的每集账本），进程累计量单独命名为 `provider_cumulative_total` 并只用于交叉核对；`_infra_summary()` 用 `计数器 - 进入本 slot 前的快照` 记录"这一工位真的花掉了多少"，因此一集死在半路不会记成 0，也不会记成整批。
- **必要性**：§5.3 的 600,000/集与 8 M/40 M/批是两套分母，混用会把批次上限算错一个数量级。
- **可能影响**：历史产物（D25 之前的 `dev` run root）里 `tokens` 字段含义与新批次不同；新批次逐集总量与进程累计序列差分 12/12 相符。
- **证据**：`metrics.json → cross_check_against_ledger.provider_cumulative_series`：`available=true, episodes_agreeing=12/12, process_counter_at_batch_end=276,434`。

### D26 一次尝试一个目录：`superseded/` 布局

- **旧**：`run_slot` 直接往 `episodes/<id>/` 追加，重跑同一 slot 会把两次尝试的 `events.jsonl` 拼进同一文件（实测发生过：slot007 的目录里同时有 `BUDGET_EXHAUSTED` 与 `NEEDS_CLARIFICATION`，聚合器把它读成一集）。
- **新**：`_isolate_prior_attempt()` 在开工前检查该目录是否已有非空 `events.jsonl`，若有则整体 `shutil.move` 到 `<run_root>/superseded/<episode_id>.attemptN`；被取代的产物不删除、不进入分母（§6.4 的替换 attempt 另有 `.retry1` episode id）。
- **必要性**：§8.1 的分母按 attempt 计数，一个目录必须恰好对应一次尝试，否则"重跑"会伪装成"一集跑了很久"。
- **可能影响**：run root 里出现 `superseded/` 这个 §10.4 未列出的目录；它不含任何被判分的行。
- **证据**：`/tmp/bst_v01/dev/episodes/slot007…`（污染的那份）已按此路径保全；新批次 `dev2` 的 `episode_directories = 12 = planned_slots`，`replaced_attempts = 0`，无 `termination_reason` 差异行。

### D27 文本环境补 pyyaml；train 开发段每类 1 次重复

- **旧**：`/home/czx/bstvenv` 无 `pyyaml` ⇒ `configs/models/*.yaml` 根本读不进来；开发段的重复数未定。
- **新**：安装 `pyyaml 6.0.3`（与桌面环境同版本，避免"两个解释器读到不同版本的同一份配置"）；`tasks.py` 的 `DEV_REPEATS = 1`，即 dev 段是 **12 个不同 train 任务各 1 次**，而不是 6 任务×2。
- **必要性**：§5.2 把 train 12 个 dev 任务定义为"接口测试与预算预检"，预检要的是**六类都过一遍**和每集成本分布，重复两次只会把同一件事的花费记两遍；全量批次的 2 次重复由 `screening`/`full` 段承担。
- **可能影响**：dev 段的 `repeats` 与正式段不同，因此 dev 的 0 胜率不可被引用为"每任务两次都失败"；已在 `task_manifest.json → segments` 与 `phase-log.md` 写明。
- **证据**：bstvenv 下 `pytest tests/unit tests/contract tests/integration`（不含 live 文件）= **350 passed, 1 deselected**，被 deselect 的那项是桌面环境的依赖断言（它要求 `Pillow` 在场，文本栈不需要也没有）；同一选集在桌面解释器 = **351 passed**（`/tmp/bst_v01/bst_offline_dev2.log`、`/tmp/bst_v01/desktop_suite_after_sources.log`）。

### D28 Phase 2 唯一一次预检调整：`max_retries 2 → 4`（不改分数门槛）

- **旧**：沿用 `deepseek.yaml` 的 `max_retries: 2`（最多 3 次尝试）。
- **新**：`configs/models/bst_text_agnes.yaml` 写 `max_retries: 4`（最多 5 次尝试，退避 1.5/3/4.5/6 s）。**所有尝试都计入 `http_requests`**，每集 90 次硬上限一字未改（§6.2：transport retry 不得藏在账本外）。
- **必要性**：实测该免费端点在 20 次探测请求里返回 5 次 HTTP 429、另一组 20 次为 0 次，且与本地发送间隔无关（1.0 s 间隔那组更差）⇒ 限速是端点侧突发。dev2 真实批次里 `transport_retries` 合计 7、`api_errors` 1（全部集中在 slot005：它一集发了 12 个请求，超过 6 轮决策），说明 3 次尝试不足以把端点抖动与能力分开。§5.2 只允许 Phase 2 依据 train 预检调整**一次**，本次调整的理由是基础设施事实，**不是**任何分数。
- **可能影响**：单集 `wall_time_s` 与 `http_requests` 上界变大（退避最坏 15 s）；若端点持续 429，一集可能因 90 次请求上限而 `BUDGET_EXHAUSTED`，这是把外部抖动记在预算里的**期望**行为，不会被记成"重放"（§6.4 不重放未知执行的超时）。
- **证据**：dev2 以 `max_retries: 4` 跑满 12 集，`infrastructure_failed = 0`、`running_reliability = 1.0`；对比 `max_retries: 2` 的第一次 dev 批次（同样 12 集、`/tmp/bst_v01/dev`）。

### D29 每请求生成上限按 §5.3 发放：`max_tokens: 2048`

- **旧**：`configs/models/agnes.yaml` 的 `max_tokens: 1200`（`benchmark/source.py` 取 `min(配置值, 2048)`，属于"更严"而非违规）。
- **新**：新被试配置 `configs/models/bst_text_agnes.yaml` 显式 `max_tokens: 2048`，并声明该文件的 `prompts:` 段对文本批次**不生效**（决策 prompt 由 `benchmark/prompts.py` 提供，以 `prompts_sha256` 记账）。
- **必要性**：§5.3 把单次生成上限定为 2,048；被截断的决策 JSON 会制造与能力无关的格式错误，污染 `schema_invalid` 这一项归因。
- **可能影响**：单集 completion tokens 上界变大；dev2 实测每集 completion 均值 364.8、最大 589（46 次请求合计 4,377，即每请求均值 95.1），距 2,048 很远。
- **证据**：dev2 的 46 次请求里 0 次因截断产生的格式修复（`format_repairs = 0`）；配置摘要在冻结时由 `manifest.json → model.config_sha256` 记录。

### D30 新增离线标注工具 `benchmark/annotate.py`（Phase 3–4，不产生任何 episode）

- **旧**：没有"把 §9 归因落到证据上"的载体；Phase 2 结尾只有 `failure_candidates.jsonl` 的自动标记。
- **新**：`annotate worklist`（按 §10.3 的纳入规则挑必读 episode，写出每台一份证据包）与
  `annotate validate`（§9.3 的最小证据门：缺 `labels`/`confidence`/`reason`/引用不到环境原话/
  `reason` 只是模型自评 ⇒ 拒绝）。标签本身由仓库外的脚本
  `/tmp/bst_v01/scratch/make_annotations.py` 从事件里逐项抄写，判据在
  `docs/bst/annotation_guidelines.md`。
- **必要性**：§9 要求"主要结论都有可定位的上下文／动作／反馈证据"，§12 Phase 4 的产出就是
  annotations；没有 `validate` 的话这条要求只能靠自觉。
- **可能影响**：**只读已落盘的产物**，不写任何 episode 目录、不调模型、不动环境；
  `rules_sha256` 不在其影响范围内（新文件，不在哈希集合内）。它**不能**替代人工：
  `annotations.jsonl` 头部 `annotated_by` 明写"single reviewer (agent-authored, human-read
  traces)"，§10.3 的"没有逐集复核的标签不得伪装成全量人工比例"按这个事实引用。
- **证据**：`tests/unit/test_bst_annotate.py`（纳入规则的强制项、`validate` 的每种拒绝路径）；
  筛查段 101 行经 `validate` 通过（`unlabeled: []`、`invalid: []`，退出码 0）。

### D31 `aggregate.py` 的三个无效动作率之一此前结构性为 0（Phase 4 实测后修正）

- **旧**：`syntax_invalid_per_execute_proposal = (len(commands) - commands_issued) / len(commands)`，
  而 `commands` 是把 `render` 离线重放到 **`skill_call` 事件** 上得到的。被 `render` 拒绝的提案
  **根本不会写出 `skill_call` 事件**，于是这条率恒等于 0——全量批次实际有 **10** 次绑定器拒绝
  （`args['target_id']='garbagecan'` 这类"只有类别名、没有实例号"的引用），发布的却是 0/611。
- **新**：每集新增 `binder_refusals`（从 `rejection_reasons` 里含
  `does not map to one environment command` 的拒绝行读出）与 `rounds_counted_twice`
  （带着 `decision_id` 的被拒反馈 = schema 通过、绑定器拒出的那一轮）；该率的分子改用
  `binder_refusals`、分母改为"到达绑定器的提案数 = `skill_call` 数 + 被拒数"。
  `rounds_decomposition` 增加 `rounds_counted_twice` 与 `identity_note`。
- **必要性**：§8.2 要求把"未修复的 schema 失误 / 适配器拒建命令 / 环境答'什么都没发生'"三件事
  分开报，其中第二件在修正前**永远报 0**，等于用一条空率掩盖一个真实失败面。同时这 10 轮正是
  10 个 `episodes_where_identity_fails` 的原因（每集恰好差 1）：不点名这个重叠，读者只能把它
  读成"有一轮被计费却没有记录"。
- **可能影响**：**离线统计**，不改任何 episode、任何决策、任何阈值。筛查段的数字**逐字节不变**
  （筛查段 0 次绑定器拒绝 ⇒ 分子 0、分母 114 与旧式相同；实测重跑
  `metrics.json`：`47/215`、`0/114`、`0/114`、`15/114`、identity_fails 0 全部与修正前相同），
  因此筛查段那 48 集的标注与哈希不需要重做，§7.3 的"改适配 ⇒ 全部 48 集重跑"不被触发。
  全量段变为 `10/621 = 1.61%`。
- **证据**：新增 `tests/unit/test_bst_invalid_action_rates.py`（2 例：绑定器拒绝被计到、
  schema 级拒绝不算绑定器拒绝）；`rounds_counted_twice == len(episodes_where_identity_fails)
  == 10` 在全量批次成立（逐集 gap 都是 1，脚本核对）；改动后 `rules_sha256` 复测仍为
  `23abe58f…aae7b3`。

### D32 标注与探针产物放在 run root 之外（§10.4 布局的一处偏离）

- **旧**：§10.4 把 `annotations/` 画在 `<run_root>/` 下面。
- **新**：本批次的 `annotations/`（worklist、annotations.jsonl、二次复核表、
  `guidelines_provenance.json`）与 `scratch/`（三个 dev-only 探针与两个生成脚本）都在
  `/tmp/bst_v01/` 下，与两个 run root 平级。
- **必要性**：run root 在冻结那一刻由 `freeze_run_root` 写完自己的
  `manifest.json`/`frozen_config.json`/`compatibility_delta.md` 副本，此后任何写入都会让
  "目录内容 = 冻结时快照"这条断言失效；标注是**事后**产物，写进去就分不出哪些文件属于那次
  冻结。运行产物留在仓库外另有一条独立理由（凭证与工作区隔离）。
- **可能影响**：只有路径约定；`metrics.json` 的 `run_root` 仍指运行目录，
  `annotations.jsonl` 头部 `run_root` 同一路径，读者可以按 `episode_id` 一一对回去。
- **证据**：两个 run root 里**没有** `annotations/`（`ls /tmp/bst_v01/{screen,full}`），
  标注侧的 `run_root`/`segment`/`guidelines.sha256` 字段在各自头部可见。

### D33 bootstrap 说明句里的episode/task 数是写死的（改为按参数算）

- **旧**：`_cluster_bootstrap()` 的 `note` 字面写着 "268 episodes of 134 tasks are not 268
  independent samples"，无论它在哪个批次上被调用。筛查段的 `metrics.json` 因此带着"268 集 /
  134 任务"的句子描述 48 集 / 24 任务的那一批。
- **新**：同一条 `note` 的两个数字由传入的 `per_task` 算出（集数 = 各任务重复数之和，任务数 =
  聚类数）。
- **必要性**：这句话的功能是提醒读者"区间不是按集算的"，它自己说错了数就把这个提醒变成了
  误导；与 D31 同一类问题（说明文字描述的是写下它的那一批，而不是正在被描述的那一批）。
- **可能影响**：纯文字；`point_estimate`、`ci95`、`n_clusters`、`draws`、`seed` 一字未动
  （重跑后筛查 `[0.0, 0.0]`、全量 `[0.0038, 0.0492]` 与修正前相同）。
- **证据**：`metrics.json → overall.cluster_bootstrap_success.note` 两段现各说自己的数
  （筛查 "48 episodes of 24 tasks"，全量 "268 episodes of 132 tasks"）。


### D34 凭证遮蔽把任务 id 和一条 rationale 吃掉了（`core/events.py` 的 `sk-` 判据收窄）

- **旧**：`_redact()` 对字符串的规则是"长度 > 24 且含子串 `sk-` / `Bearer ` / `api_key`"就截到
  前 12 字符 + `***redacted***`。子串 `sk-` 在 ALFWorld 的任务 id 里天天出现：
  `pick_and_place_simple-Mug-None-Desk-308/trial_…` 里的 `sk-` 来自 `De**sk-**308`。
- **新**：`sk-` 必须**起始一个 token**（前面不是字母或数字）才算凭证形状，`Bearer ` 与 `api_key`
  两条不变，长度下限 24 不变。真实 key 总是出现在值首、`=` 之后或空格之后，因此旧规则抓到的
  每一种形状新规则照样抓到。
- **实测影响面（可复算，逐字段统计两个 run root 的全部事件）**：`***redacted***` 一共出现在
  2,897 个字段值里，其中 2,894 个是**有意为之**的
  `decision_context.budget.episode_tokens_used/total`（各 1,447），**误伤** 6 个
  `episode_start.task_id`、6 个 `episode_end.result.task_id`（同一批 6 集：slot131/157/178/180/
  232/256，全部 `…-Desk-308` 任务）、以及 1 条 `decision.rationale`（slot177 某轮 clarify）。
  `model_calls.jsonl` 里 0 处。
- **为什么这不是"§7.3 的适配改动 ⇒ 48 集重跑"**：被改的文件只写日志，不产生任何模型可见内容——
  prompt、catalogue、决策、终止、评分、预算都不读 `events.jsonl`。改完后
  `rules_sha256` 复测仍为 `23abe58f…aae7b3`（逐字节相等），且**没有任何一集被重跑**。
- **已丢的证据不假装找回**：那 6 个 task_id 与 1 条 rationale 在既有产物里就是被截断的；
  D35 修的是"聚合器因此算错"，rationale 本身无从恢复，按缺失记录。
- **证据**：`tests/unit/test_event_log_safety.py::test_an_identifier_that_happens_to_contain_sk_survives`
  （任务 id 与含 "risk-averse"/"desk-1" 的 rationale 原样落盘，同时
  `api_key=sk-…` 仍被遮蔽）+ 该文件其余 15 例全绿。

### D35 聚合器按被截断的 task_id 聚类，导致 3 个任务并成 1 个（改为优先未遮蔽字段）

- **旧**：`recompute_episode()` 的 `task_id` 取
  `episode_start → episode_summary → evaluation` 的第一个非空值。6 集的 `episode_start.task_id`
  被 D34 吃掉之后仍是"非空"，于是这 6 集在按任务聚类时共用同一个残缺键。
- **新**：`_first_unredacted()` 跳过含 `redacted` 的候选（`episode_summary.json` 由另一条路径
  写出，从未被遮蔽），其余优先级不变。
- **必要性**：§8.4 的 bootstrap 以任务为聚类单位。旧写法下全量段发布的是
  **132 个聚类（其中一个装了 6 集）**，`tasks_with_two_verdicts` 也从 134 少成 132；聚类边界
  错了，区间就不是它声称的那个统计量。
- **可能影响**：只改离线统计。筛查段数字不变（24 个聚类、CI `[0.0, 0.0]`）；全量段的
  任务级 CI 由 `[0.0038, 0.0492]`（132 聚类，D34/D35 修正前发布过）变为
  **`[0.0037, 0.0485]`（134 聚类）**，点估计 0.0227 → 0.0224，`both_failure` 128 → 130。
  成功率、成本、终止分布等逐集统计一字未动。两个数字都留在报告里，不以新值覆盖旧记录。
- **证据**：`metrics.json → overall.cluster_bootstrap_success.n_clusters == 134` 与
  `overall.repeats.tasks_with_two_verdicts == 134`；逐目录核对
  `start.task_id != summary.task_id` 的 episode 恰为 6/268。

### D36 标注生成器（`scratch/make_annotations.py`，产物在仓库外）的五处"句子与产物不符"

这一类不是判据问题，是**行内证据句子写错**：标签可能对，但句子引用不了它声称引用的东西。五处全部
在收口前修掉，每一处的爆炸半径都用"逐键 diff 存档"量出来，而不是用"应该只影响几行"说过。

| # | 缺陷 | 修好之后 | 受影响行（实测） |
|---|---|---|---|
| 1 | `schema_refusal` 行的 key 计数把 `execute` 内的 6 次折进顶层的 400 次 | 按位置分层：406 总 = 顶层 400（`candidate_id` 196 + `subgoal` 204）+ `execute` 内 6 | 148 行 `reason` |
| 2 | `truncation` 行只说"被截断"，不引该轮实际回复与锚句 | 回复原话 + 触发截断的锚句进句子 | 41 行 `reason` |
| 3 | `final_decision` 的 UNRESOLVED 行引用了一个空的 `rationale`（`''`） | 无措辞时改为引 payload/终止字段，不引用不存在的引文 | 17 行 `reason` |
| 4 | `unpublic_reference` 的重复行把"两轮之间"算进了别的轮 | 只数两次同命令之间的轮，并逐轮引环境原话 | 2 行 `reason` |
| 5 | `no_effect_round` 的 `world_said` 子句被拼在句号之后，首字母小写 | 两个句号后的分支改用 `world_cap` | 10 行 `reason` |

- **可复算的核对方式**：三次重跑（06:49 / 07:28 / 08:24 三份文件）逐键
  （`episode_id|round_index|claim`，530 行 = 527 键，3 集各有 2 条 `final_decision`）比较。
  07:28 → 08:24 的差是 208 键，正好等于表中 1–4 类之和（148+41+17+2），且 **0 行标签变化、
  0 个其他字段变化**；第 5 类的差是 10 键，改前改后各重跑一次两段得到
  "全量 10 行 / 筛查 0 行、标签 0 变、其他字段 0 变"。
- **同一段里唯一一次判据形状变化**（不是排版）：06:49 → 07:28 只有 14 行 `no_effect_round` 变了，
  其中 10 行标签由 `OBSERVATION_LIMITATION` 变为 `OBSERVATION_LIMITATION,STATE_USAGE`、
  `competing_explanation` 同步、8 行 `confidence` 变。原因是 6.4-D 的 `else_`/`at`/`rep` 三个测试
  这时才进生成器。**这是收紧，不是放宽**：没有任何一行的标签被减少。
- **必要性**：§9.3 要求行内每个计数都能回到产物；一个折错的分组数会让"这套接口在多大比例上把
  模型教成违规"这个结论失去可核对性，而这正是那 148 行存在的唯一理由。
- **可能影响**：只改标注文本与离线聚合的标签分布（见上一条的 0 标签变化）；`rules_sha256`、
  `prompts_sha256`、`catalogue_sha256`、任何一集的 `events.jsonl` / `episode_summary.json` /
  `model_calls.jsonl` 一字未动，没有重跑任何一集。
- **证据**：`docs/bst/annotation_guidelines.md` §6.6 第 5 条；
  `/tmp/bst_v01/annotations/annotations_full_rev_0649.jsonl`、`…_rev_0728.jsonl`、
  `…_stamped_0819.jsonl` 三份存档与 `scratch/ann_full_try3..try11.jsonl`。

### D37 二次复核新增第四种判决 `wording_corrected`（§10.3 的一个空档）

- **旧**：判决只有 `held` / `changed` / `row_added_by_the_reread` 三种。标签在重读后仍然成立、但
  首遍那句证据陈述被产物推翻的行，没有可写的地方——记 `held` 等于把句子和标签一起保住（假），
  记 `changed` 等于说标签动了（也假）。
- **新**：加 `wording_corrected`，`stamp` 与 `merge_verdicts.py` 都硬性要求
  `labels_agree == (result in {held, wording_corrected})`，且 note 非空（"没有 note 的判决是盖章"）。
- **实测**：全量段 16 行落入这一类 —— 12 行 `schema_refusal`（分组计数折错那一族）、2 行
  `truncation`（回复与锚句）、slot174 第 7 轮（"两轮之间没有新证据"的算法）、slot260 第 1 轮等；
  筛查段 0 行，因为那 24 行的判决写于这些模板修好之前，其中 3 行是
  `changed`/`row_added_by_the_reread` 而不是"措辞"。
- **同时纠正一处会让 held 被高估的记录方式**：与两份存档逐键比对量出 **101 行 held 之下有 28 行**
  （对 06:49 是 29 行，另 2 行因标签已变在旧存档无同键）的句子在判决之后被规则重写过。报告引用
  held 率时必须并列这两个数：**held 计的是标签与判据，不是句子**。
- **必要性**：§10.3 要的是"留住分歧"，而句子级错误如果并入手感相同的 held，二次复核就变成了给
  自己盖章。
- **可能影响**：只影响复核字段与头部计数（`second_review_wording_corrected`）；标签分布不变。
- **证据**：`/tmp/bst_v01/annotations/second_review_verdicts_full.jsonl`（122 键，
  101 held / 4 changed / 1 added / 16 wording_corrected）；
  `scratch/make_second_review.py` 的 `stamp`、`scratch/merge_verdicts.py` 的校验分支。

### D38 复核记账工具的三处静默失败改成硬失败

- **`verify_absence.py` 的空转**：它从 `episode_start.payload.task_id` 解析任务类别来构造"应当
  缺席的类"，而 D34 记录了 6/268 集的 `task_id` 被 `_redact` 屏蔽过——那 6 集类清单为空，于是
  "这个类整集从未出现"的断言**空转通过**。改为优先读 `episode_summary.json` 的 `task_id`，解析
  失败显式返回 `UNPARSED_TASK_ID` 而不是空清单。slot180 的 held 判决差点落在空转输出上。
- **未判决行的口径**：临时脚本按 episode 判断"这集判过没有"，把 122 行的缺口读成 41；`merge_verdicts.py`
  现在每次合并后打印 `still_unjudged_count`（按 `episode|round|labels` 全键），当前为 0。
- **stray 键**：短柄判决文件（`slot005 | 9 | …`）不经 `verdict_batch.py` 解析就直接 merge，会带进
  20 个永不落到行上的键并把缺口从 41 抬到 52；merge 现在遇到任何不在抽样键集合里的判决直接失败。
- **必要性**：这三个都是"检查通过但其实没检查"的形状，对一个以可核对性为卖点的批次是最贵的一类
  bug；它们都不是模型或环境问题，是复核工具自己的问题。
- **可能影响**：无。三个脚本都不写 run root，只读；标注产物与 episode 产物不变。
- **证据**：`scratch/{verify_absence,merge_verdicts,verdict_batch}.py`；
  `docs/bst/annotation_guidelines.md` §6.6 第 5 条第 2–4 点；`docs/bst/commands.md` 的新命令段。

### D39 判据文件重哈希后，worklist 的载荷逐字节不变（§10.4 哈希链路的一次自证）

- **做法**：追加 §6.6 之后，用 `annotate worklist` 重建两段 worklist（cap 4、同 run root、同
  segment），与新存档逐行比较：48/49 与 265/266 行的**载荷行 JSON 集合完全相等**，头部只有
  `written_at` 与 `guidelines.sha256` 两个字段变。然后才重跑标注器与 `stamp`。
- **为什么记一条**：判据文件的哈希进了标注文件的头部，任何"改了判据"都必须能被证明只改了判据
  文字而没有改证据包。这次核对给出的是：证据包逐字节相同，标签分布与之前一致（530/106 行、
  标签计数不变），改动只有 D36 第 5 类的 10 行句子。
- **新的判据哈希**：`11899e9604ac07326f284a2f9e7c8aef3028da86620a1937734a3a945479cff6`
  （两段 `annotations*.jsonl` 头部一致；旧值 筛查 `18da301c…`、全量 `0f986e5e…` 留在存档文件里）。
- **可能影响**：`rules_sha256 = 23abe58f…`、`prompts_sha256 = 25502db7…`、
  `catalogue_sha256 = f5044d67…` 复测不变；冻结批次仍可判读。
- **证据**：`/tmp/bst_v01/scratch/worklist_{full,screen}_rebuild.jsonl` 与
  `/tmp/bst_v01/annotations/worklist_full_0525.jsonl`、`worklist_screening_0403.jsonl` 的 diff。

### D40 标注器的形类计数把"本地拒收"算成了"引擎答了"（`batch_command_form_outcomes`）

- **缺陷**：该函数只按 `if skill:` 收 `execution_feedback`，再用"回话不以 `Nothing happens.` 开头"
  定义 `succeeded`。一条 `executed=False`、`status=rejected`、`failure_code=INVALID_DECISION`、
  `environment_text` 为空的反馈是**本地校验器**的拒收记录（命令没进引擎），却落进了成功那一侧。
- **修法与影响面**：门改为 `if skill and fb.get("executed") and text:`。全量段逐集核出**恰好 10 条**
  这种反馈（`go` 7：slot094/144/153/161/164/168/182；`take` 1：slot113；`examine` 1：slot135；
  `use` 1：slot239），计数随之 `go 301→294、take 76→75、use 17→16、examine 4→3`，四个
  `nothing_happens` 计数不变（43/14/4/10）；筛查段 0 条，该段逐字节不变。改前改后各重跑两段做
  逐键 diff：**16 行 `reason` 变化（全是 `no_effect_round`）、0 行标签变化、0 个其他字段变化**，
  行数仍 530 / 106，抽样键集合不变，因此 122 + 24 份判决原样重盖。
- **顺带纠正一条记错的开放事项**：修正后的 `examine` 是 3 次真回话，逐条读为
  `'The cabinet 1 is closed.'`（slot016）/ contents 报告（slot045）/ `"There's nothing special
  about pencil 3."`（slot174 第 8 轮）——描述性句式**有一个真实样本**，三种句式都被解析器接住
  （`receptacle_state` / `contents` / `no_new_information`，`unparsed` 为 0）。§8 里那条"仍无样本、
  须标为未测面"按此改写。
- **留下的过期数字**：4 行已盖章判决的 note 引的是修正前的 301/43 与 4/10（slot144 r3、slot250 r4、
  slot174 r2、slot025 r4）。note 是那次重读当时能看到的证据的记录，**不改写**；更正只进本节、判据
  §6.7 与文件头部的 `command_form_outcomes_in_segment`。
- **独立核对**：这 10 集与 `metrics.json → invalid_action.rounds_decomposition.
  episodes_where_identity_fails` **逐集相同**（`aggregate.py` 那条"决策轮恒等式"的告警名单）。
  两条互不相干的检查看见的是同一批事件——本地 binder 拒收让一轮既进 `decisions_by_action`
  又进 `rejected_decisions`（该文件的 `identity_note` 已把这解释为"一轮被计两次"，不是"被计费却没
  记录"），而标注器把同一批拒收当成了"引擎回了话"。因此本条不是新事实，是同一事实的两个探针；
  报告引用时应并列给出，以说明拒收在两套统计里的不同去向。
- **可能影响**：不改任何 episode 产物、不改判据 §1–§5 的任何标签定义，因此不改分数。它改的是报告
  可以引用的一个统计量的口径，所以规则是：**报告不得把形类计数当作命令合法性的独立证据**，引用它
  时必须给出 `succeeded` 在这里的定义。`rules_sha256 / prompts_sha256 / catalogue_sha256` 与本缺陷
  无关（标注器在仓库外，不进任何冻结哈希）。
- **证据**：`/tmp/bst_v01/scratch/{ann_full_try11,ann_full_try12,ann_screen_try7,ann_screen_try8}.jsonl`
  的逐键 diff；`docs/bst/annotation_guidelines.md` §6.7。


---

## 4. 明确未变的机制（§3.2 逐条核对）

| 冻结项 | 源码位置 | 核查结果 |
|---|---|---|
| 一轮最多一个技能；execute/finish/blocked/clarify | `core/contracts.py::Decision`、`Runtime` 分支 | 未改；文本后端每轮仍只可能一次 `SkillCall`（live 重放 9 条轨迹 ⇒ `decision_rounds == len(skills)`，含 1 轮 finish 时 +1） |
| 通用决策要求/返回结构/依据与预期字段 | `Decision.rationale/expected_effect/evidence_refs` | 未改；仅提示词文本换成 `bst-decide-text-v1`（D11） |
| 近期反馈深度 3 | `adapters/deepseek.py:340`、`benchmark/source.py:43`、`configs/models/agnes.yaml:19` | 三处一致为 3（SPEC 报告值与源码一致，无需按 §3.2 例外记录） |
| AttemptRecord 构造、`HISTORY_CAP=12`、裁剪 | `core/runtime.py:59, 651, 667` | 未改；`test_bst_text_backend.py::test_the_only_history_is_the_frozen_window` |
| 不启用 subgoal/todo/长期意图维护 | payload `current_subgoal`/`todo_summary` 默认 None | 未启用；文本后端不写这两个字段的新生产者 |
| schema 错误/语义拒绝/网络错误处理与计费 | `Runtime` 的 `DecisionSchemaError`、`semantic_repairs`、`MAX_MODEL_ERRORS=3`、`MAX_FALSE_FINISHES=3` | 未改；文本后端只改**本地拒绝的判据**（D6），不改拒绝后的计费形状。三条测试：`::test_a_schema_error_costs_a_decision_and_no_environment_step`、`::test_a_transport_error_is_a_model_error_and_is_billed`、`::test_a_rejected_call_is_bounded_by_the_repair_budget`（`max_semantic_repairs` 实测默认 2） |
| 无效重复守卫算法与阈值 | `_repeated_without_new_evidence` | 算法/阈值未改，只换指纹输入（D18） |
| 预算检查与控制终止机制 | `BudgetLedger.can_decide()`、`ledger.wall_remaining()` | 未改。注意实测事实：`can_decide()` **不**看 `http_requests`，所以 HTTP 上限只能由 `_preflight_trip` 约束（D17、§5.3 的保守预检） |
| 决策机制哈希 | `configs/experiment/p3_preregistration_v1.json` | `23abe58f…aae7b3`，与当前代码 `rules_sha256(_rules())` 逐字节相等（两次核查：加入 AlfredRuntime 前后） |

---

## 5. 本轮发现并修掉的两个真实缺陷（不是"预防性改动"）

1. **`false_finish` 归账错误**。`Runtime._finalize` 在**每一条**退出路径上，若 `terminal_world is None` 都会调 `self._finish_check(self.goal)`（`core/runtime.py:691-692`）。最初 `AlfredRuntime` 在 `_finish_check` 里置 `_agent_finished = True`，于是"被截断/被 blocked 的集"也被标成 `AGENT_FINISH` 并可能算 `false_finish`。修法：把置位移到 `_validate_decision`——只有**校验通过的** finish 才置位（`runtime_alfred.py:279-288`）。测试：`::test_a_finished_episode_the_environment_did_not_win_is_a_false_finish`、`::test_the_environment_done_and_the_agent_finish_are_different_records`、live `::test_the_engine_truncates_at_its_own_action_budget`（`false_finish is False`）。
2. **解析器锚点顺序缺陷**。`contents_in`（"In it, you see …"）排在 `opened`（"You open the X. Inside you see …"）之后，导致**这个环境里最常见的一种回复形状**产出的 contents 事实把 `subject` 写成 `unknown` 而不是那个刚被打开的容器。Phase 0 的"0 句未被读"覆盖率指标看不见它——句子被读了，只是读成了错的绑定。修法：`parser.RULES` 重排为"先建立锚点的规则，再放读锚点的指代规则"，导出 `ANCHOR_RULES/ANAPHORA_RULES`，并加一条顺序守卫测试 `test_no_rule_that_only_sets_an_anchor_is_listed_after_one_that_reads_it`（`tests/unit/test_bst_parser.py`）。重排后离线/在线全部重跑：113 passed（当时离线 BST 三文件）、18 passed（live）、7 场景 runtime smoke `FAILS: none`、9 条官方获胜轨迹重放 `mismatches: none`。本清单定稿时离线为 **114**：多出的 1 项是随后为"引擎自带 `help` 会打印合法动作全集"这一入口新增的 `test_the_environments_own_help_command_cannot_be_asked_for`。

---

## 6. 采用的替代方案与原因（§"必要时采用合理替代方案并记录原因"）

| 替代 | 原方案 | 为什么换 | 影响 |
|---|---|---|---|
| 机器产物写到 `/tmp/bst_v01/<run>/…`，人类交付物在 `docs/bst/` 并在冻结时复制进 run root | 全部落在 `<run_root>` | 单集 `events.jsonl`/`model_calls.jsonl` 体量大，不进 git 树；§10.4 要求的完整目录在冻结/聚合时于 run root 内成立 | 交付目录树在 run root 内逐项齐全；仓库里保留可复现命令与摘要 |
| 双解释器：`/home/czx/bstvenv`（ALFWorld 栈 + pytest 9.1.1）跑文本相关，`/home/czx/miniforge3/envs/embodied` 跑桌面回归 | 单环境同时装两套 | 文本栈不要 torch/ai2thor/CUDA；桌面栈的 pybullet 与文本依赖混装会让版本记录失真 | 只有 bstvenv 能跑 live 测试；桌面解释器无 textworld ⇒ 该文件**命名原因 skip**（不计为通过） |
| 用免费 Agnes 端点 `configs/models/agnes.yaml`（`provider: agnes`、`model: agnes-2.5-flash`） | 沿用 `configs/models/deepseek.yaml` | 用户明确要求换免费端点；旧文件字节被冻结预注册哈希锁住，改它会使 v0.1 批次不可判分 | 新批次不与 v0.1 冻结批次同被试；`--prereg` 强制判分路径会以 `matrix_mismatch` 拒绝（预期）。旧配置未改一字 |
| Phase 2 的 `max_tokens` 需按 §5.3 设为 2,048 | 沿用 `agnes.yaml` 的 `max_tokens: 1200` | §5.3 规定单次生成上限 2,048 tokens；`benchmark/source.py:50` 取 `min(配置值, 2048)`，因此 1200 是"更严"的读法而非绕过 | **已落地**：新文件 `configs/models/bst_text_agnes.yaml` 显式 2048（D29），`agnes.yaml` 一字未改 |
| 文本批次的入口是 `python -m embodied_agent.benchmark.cli` | §12 那种"一个 `benchmark` 包名就是命令"的写法 | 包里刻意不放 `__main__.py`：子命令挂在 `cli.py` 上，多一个入口就多一处会与它漂移的地方 | `python -m embodied_agent.benchmark` 会以 "no module named ...__main__" 失败；文档与脚本一律写 `.cli`（D23） |
| 桌面环境的依赖断言在 bstvenv 里 **显式 deselect** | 在两个解释器里都跑同一选集，或改小那条断言 | 该测试断言的是桌面物理栈的依赖集合（要求 `Pillow` 在场）；文本栈不需要它，装它反而会污染 `environment_check.json → packages` 的语义 | 选集差异写进本条与 `phase-log.md`，不静默跳过：bstvenv `350 passed, 1 deselected`；桌面 `351 passed` |

---

## 7. 更新后的 §12.1 文件映射（Phase 0 核查后的实际职责）

| 位置 | 实际职责 | 状态 |
|---|---|---|
| `embodied_agent/core/contracts.py` | `Source.local_text`、`TextFact`、`WorldState` 文本通道 4 字段、`SkillName` + 11、`FailureCode` + 3、`ExecutionFeedback.environment_text`、`EpisodeResult.termination_reason` | D1/D2/D5/D15/D21 |
| `embodied_agent/core/runtime.py` | 11 个后端接缝 + `context_class` + `_validate_envelope/_validate_execution` 拆分 + `_preflight_trip` + `termination_reason` 透传 | D13/D6/D12/D15 |
| `embodied_agent/core/skills.py` | **未改**；桌面执行原样保留 | §3.2 |
| `embodied_agent/benchmark/native_actions.py` | 冻结版本的原生命令目录、模板渲染、参数形状校验、`catalogue_sha256` | D3/D6 |
| `embodied_agent/benchmark/parser.py` | 公开文本 → `TextFact`；锚点/指代分层的 `RULES`；`unparsed` 保真 | D2、第 5 节缺陷 2 |
| `embodied_agent/benchmark/state.py` | `text_world_state`、`text_state_fingerprint/diff`、`TextGoalSpec`、`TextVerifier` | D1/D2/D4/D8/D9/D18 |
| `embodied_agent/benchmark/backend.py` | reset/step/关闭、命令超时、`eval_view()` 隔离读官方结果与 error | D7/D14/D16 |
| `embodied_agent/benchmark/executor.py` | 一条 SkillCall ⇒ 恰好一次 `env.step`；`_map_failure` 前置 | D3、§13-2 |
| `embodied_agent/benchmark/prompts.py` | 文本后端提示词与 `TextDecisionContext.model_payload` | D11/D12 |
| `embodied_agent/benchmark/source.py` | 复用共享 HTTP client 的决策源、`bst-decide-text-v1` 记账、2048 cap | D11/D17/D22 |
| `embodied_agent/benchmark/runtime_alfred.py` | AlfredRuntime：缓存观测、后端终止、finish/done 协议、预算视图、终止名、密封记账 | D5/D9/D14–D17/D20 |
| `embodied_agent/benchmark/tasks.py` | 划分选择（train 12 dev、valid_unseen 134×2、`selection_seed 20260919`、SHA-256 top-4/类型 ⇒ 24×2=48）、固定交错顺序 | Phase 2/3 入口 |
| `embodied_agent/benchmark/envcheck.py`／`probe.py` | 环境自检与探针（§12 的"环境自检"命令） | D7 |
| `embodied_agent/adapters/deepseek.py` | `provider` 参数化 + `.env` 去引号；三个 prompt 常量与请求体未改 | D22、I1/I2 |
| `embodied_agent/core/events.py` | **未改**；复用事件与模型调用账本，`_redact/_is_secret` 继续守凭证 | §3.2 |
| `embodied_agent/evaluation/run.py`、`report.py` | 调度/分母/成本/taxonomy 统计的接入点 | Phase 3–5（未开始） |
| `embodied_agent/cli.py` | benchmark 运行/续跑/离线聚合/校验冻结配置入口 | Phase 2 起补 |
| `tests/contract/test_bst_text_backend.py`(31) / `tests/unit/test_bst_native_actions.py`(55) / `tests/unit/test_bst_parser.py`(28) / `tests/integration/test_bst_alfworld_live.py`(18) / `tests/fixtures/bst_alfworld_expert_replay.json` | §13 的最小验证证据（桌面回归第 8 项、离线重算第 9 项见下） | 已跑绿 |

---

## 8. 尚未落地的兼容性事项（Phase 3–5，届时必须补条目）

已在 Phase 2 闭合的（保留编号，避免"消失"）：

1. ~~§13-9 离线重算无实现~~ → `benchmark/aggregate.py`（D24）；两条独立重算路（行求和 / provider
   累计序列差分），且差异本身进入 `metrics.json` 而被点名。
2. ~~模型身份守卫未实现~~ → `runner.identity_drift` + `MODEL_IDENTITY_DRIFT` + `BatchHalt`（有测试
   `tests/unit/test_bst_batch_control.py`：漂移被指名、**沉默不算相符**）。
3. ~~暂停/续跑账本与 8 M/40 M cap~~ → `run_batch` 的 `resumable_complete` 跳过、开工前按实际计费量
   判 cap、基础设施失败最多一次替换（同文件有 2 项测试）。
4. ~~预算数值调整~~ → 只用掉 §5.2 允许的那一次：`max_retries 2 → 4`（D28）。此后进入筛查，
   **不再按结果改动任何预算、阈值或提示词**。

仍然开放的：

1. §10.5 九节报告（`docs/bst/Bottleneck-Report.md`）与 §11 映射（`docs/bst/v0.2-Decision-Evidence.md`）
   本身。`termination_reason` / `false_finish` 的**叙述性**口径在其中落实：聚合器已经把它们按 episode、
   按类型、按分母算好（`metrics.json → termination`：`AGENT_BLOCKED 110 / NEEDS_CLARIFICATION 94 /
   BUDGET_EXHAUSTED 41 / AGENT_FINISH 17 / ENV_TERMINATED 6`，`false_finish 17`，
   `env_done_without_agent_finish 6`，`agent_finish_without_env_done 17`），缺的是报告。
2. 报告引用 `command_form_outcomes_in_segment` 的每一处都必须并列给出 `succeeded` 的定义（D40）：
   "命令真的下发、引擎真的回了话、且回话不以 `Nothing happens.` 开头"。它测的是本段内部的应答分布，
   不能当命令合法性的独立证据。
3. `EXECUTION_FAILURE` 在本批两段都是 **0 行**（slot025 第 4 轮改判之后，全段 12 行 / 筛查 1 行的该
   标签撤下）。报告必须写成"§9.2 的这一行在文本批次未被使用"，不能写成"未发生执行失败"——这两句话在
   一个 41 轮预算截断、17 次假 finish 的批次里含义完全不同。
4. 盲评仍是单一标注者（`blind: false`）；§10.3 的二次复核给的是判据随时间的稳定性，不是 inter-rater
   agreement。要估 IRR 需要第二个 reviewer，v0.2 的事。

已在本节追加期内闭合的（保留原编号，避免"消失"）：

- ~~失败归因标注（§9.2 taxonomy）需要预注册判据 + 盲评，Phase 4 交付~~ →
  `docs/bst/annotation_guidelines.md`（§0 冻结时间戳早于抽样）+ 两段 `annotations*.jsonl`（530 / 106 行）
  + `annotate validate` 全绿 + 两段各一次 §10.3 二次复核（全量 122 行判决、筛查 24 行判决，
  `still_unjudged_count 0`）；**盲评一项按实际做到的记录**：`blind: false`，判据文件 §6.2 把遮蔽范围
  写死到字段名，而本批的标注者是写这批产物的同一个 reviewer。
- ~~Phase 3 若出现"一个 episode 目录 ≠ 一次尝试"或"行求和 > 封存值"，那是缺陷而不是解释对象~~ →
  两种形状都没出现，而且是**测出来**的：`metrics.json → denominators` 给
  `planned_slots = episode_directories = started = benchmark_valid = 268`、`replaced_attempts 0`、
  `not_run_slots 0`；`recompute_mismatch` 为空，行求和 = 封存值 = 账本值 = 6,036,680 tokens。
  provider 累计序列这条独立路更强：**筛查段 48/48 集全符**（进程计数末值 1,162,821 = 该段封存值），
  **全量段 267/268 集全符**，唯一不符的是续跑接缝 `slot048`，差值 `-1,149,098` = 13,723（该集自身开销）
  − 1,162,821（前一进程的计数末值），即 `aggregate.py::_series_check.assumes` 预告的"续跑批的第一行
  会差掉之前那次的花费"这一形状；新进程末值 4,873,859 减 slot048 的 13,723 恰等于 slot049…267 的行求和
  4,860,136，两段相加 1,162,821 + 4,873,859 = 6,036,680 与封存值闭合。接缝被点名而不是被吸收。
- ~~`alfred_examine` 的描述性回复句式仍无真实样本，须标为未测面~~ → **这一条原本就是错的，按 D40 撤销**：
  修正后的形类计数是 `examine` 3 次真回话 / 10 次 `Nothing happens.`，其中
  `"There's nothing special about pencil 3."`（slot174 第 8 轮）就是描述性句式，另两次是
  `'The cabinet 1 is closed.'`（slot016）与一次 contents 报告（slot045）；此前把它记成"无样本"的原因是
  D40 那个计数缺陷把 4 当成成功数，而第 4 次其实是 slot135 的一条本地拒收。三种句式都被解析器接住
  （`unparsed` 0）。同集第 2 轮 `examine pencil 3` 回 `Nothing happens.`、第 8 轮回描述句，中间只有
  第 3 轮 `You pick up the pencil 3 from the desk 2.` 改变了局面——这条对照进报告的证据质量一节。
  旧文案里"4 次带状态变化里包含 `use desklamp` 那类点亮灯的回复"一句不成立：`alfred_use` 的 16 次
  真回话全是 "You turn on the desklamp 1." 形状，与该 census 无关（两条命令按 `skill` 分开计数）。


### D41 Phase 5 的报告与证据工具：三处自我修正 + 三个只读脚本

- **性质**：不改任何 episode、不改任何决策机制。全部产物在 `docs/bst/`、`work/`、
  `/tmp/bst_v01/scratch/` 三处，被测包 `embodied_agent/` 一个字节没动 ——
  改后复测 `rules_sha256` 仍为 `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3`
  （`preregistration()` 与 `_live_rules_sha256()` 双路实测相等），冻结批次仍可判读。
- **新增脚本（全部只读，不参与评分，不进入任何 prompt / Feedback / DecisionContext）**：
  `work/text_trace.py`（按轮读回封存 episode 的文本）、`work/gui_agent.py`（dev-only 3D 观察，
  属于 v0.1 桌面栈，与文本批次无关）、`/tmp/bst_v01/scratch/report_stats.py`（标注统计）、
  `/tmp/bst_v01/scratch/funnel_and_held.py`（命令漏斗与轮间持物回落）。
- **修掉的三处报告口径错误**（写完后逐数回查产物发现）：
  (1) "每轮约 20k prompt token" ⇒ 实测均值 5,342 / 中位 4,360 / p95 9,499 / 最大 10,136
  （1,108 条带 `usage` 的请求）；
  (2) 把 `executed=True` 的 `move` 读成"真正放下过" ⇒ 拆成两列：发出 9 集 / 引擎报出效果 4 集，
  `heat/cool/clean` 同样从"21 集做过状态改变"改为"发出 21 集 / 有效果 4 集"；
  (3) 漏斗与 56 集 / 58 轮曾记在 `metrics.json` 名下 ⇒ `metrics.json` 无这些字段，改标为
  `funnel_and_held.py` 对 `events.jsonl` 的直测。
- **顺带钉死的一处分母口径**：`invalid_action.schema_invalid_per_decision = 230 / 1,133` 与
  `rounds_decomposition.refused_before_execution = 276` 差 46 轮，原因是前者的理由模式串
  （`not permitted` / `action:` / `field required`）不含 32 个 "`action=clarify` 必须写出缺什么"
  与 4 个其它 schema 错误；276 = 266 未过冻结 schema + 10 binder 形类拒收。逐轮可复现。
- **为什么记一条**：本 SPEC 的结论必须能由封存日志重算（§10.4）。报告第一版里有三个数来自
  记忆而不是产物，若不写下这条修正，读者会以为它们和其余数字同样直接来自 `metrics.json`。

## D42 —— v0.2 P0 动了共享底座：契约代闸门与两处删除（2026-09-20）

- **改了什么**：`embodied_agent/core/events.py` 的 `EpisodeStore.log()` 新增关键字形参
  `contract_version`（缺省 = 跟随记录自己声明的 `schema_version`，再缺省 = v0.1 常量 "3"），
  显式版本与记录声明冲突 ⇒ `ValueError`，记录携带的 `episode_id` 与 store 不同 ⇒ `ValueError`；
  新增 `log_record(event_type, record)`；`read_all(schema_version=None)` 可按契约代过滤。
  新增 `embodied_agent/core/v02.py`（schema "4" 契约层 + §9 消融登记表）。
  删除 `embodied_agent/core/recovery_stub.py`（0 字节、无引用）与 `embodied_agent/llm.py`
  （第二个 `DeepSeekAdapter`，无引用，绕过 `response_format` / 重试计数 / "(body not read)"）。
- **对封存批次的影响**：**零**。`/tmp/bst_v01/full/` 的 268 集产物不被这些代码回写；
  新写入路径对 v0.1 记录保持字节口径一致（同一信封值、同一
  `_clobbered_reserved_keys` 见证），由
  `tests/contract/test_v02_contracts.py::test_the_event_store_writes_two_contract_generations_without_pooling_them`
  钉住。`cli prereg --check configs/experiment/p3_preregistration_v1.json` 在本轮改动前后都输出
  `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3` ⇒ 冻结规则身份未移动
  （`rules_sha256` 由 `inspect.signature` 采样与统计定义组成，不含文件字节，见
  `evaluation/preregistration.py:71,286`）。
- **回归**：全量 `406 passed / 21 skipped（151.76s）`；对照
  `pytest -q tests --ignore=tests/contract/test_v02_contracts.py` →
  `381 passed / 21 skipped（142.33s）`，差值 25 与新增契约文件的
  `25 tests collected` 逐条相等 ⇒ v0.1 那套测试一条没红、一条没改。最终数以
  `docs/continuous-decision-v0.2-phase-log.md` §2.1 为准。
- **为什么要记一条**：`events.py` 是 v0.1 与 v0.2 共用的唯一写入路径。若不在 P0 就把契约代
  钉进信封，P1–P4 写出的 schema-4 记录会以 `schema_version: "3"` 落盘，而所有下游读者
  （`evaluation/run.py:290,589-594`、`report.py:72-80`、`blind_review.py:101`、
  `benchmark/aggregate.py:44-52`、`annotate.py:62-75`）只按 `type` 过滤 ⇒ 两代契约会被并成一份
  统计，且事后无法分开。这是 §12.1"同一底座可比"与 §3.5"四类信息严格分离"的共同前提。

## D43 —— v0.2 P1-c 动了共享底座：`EntityState` 多了一个"这个姿态是不是量出来的"标志（2026-09-20）

- **改了什么**（三处，全部加法，无一处删除或改签名）：
  1. `embodied_agent/core/contracts.py`：`EntityState` 新增
     `orientation_measured: bool = True`；新增纯函数
     `orientation_invariant_footprint_half_xy(geom)`（任意姿态都成立的水平界：盒子取
     半空间对角线 `hypot(hx,hy,hz)`，圆柱取轮缘半径 `hypot(radius, half_h)`）；
     `DecisionContext.model_payload()` 的实体行两处 `None`-ing ——
     `position` 在 `pose is None` 时为 `None`，`yaw_rad` 在此之上还要求
     `orientation_measured` 才给数（`contracts.py:817-824`）。
  2. `embodied_agent/core/verify.py`：两个**已存在**的取值口各加一条分支/守卫——
     `entity_footprint_half_xy(entity)`（:102）原先无条件
     `footprint_half_xy_from_quat(geometry, pose.quaternion_xyzw)`，现在未测朝向 →
     返回不变界、声称测了却无 pose → `ValueError`；`entity_position(entity)`（:120）
     原先直接解引用 `pose.position`（`pose=None` 是 `AttributeError`），现在是带名字的
     `ValueError`。签名与调用点（`measure_occupancy` :247-248、`state_inside` :264）
     **未动**；**`state_supported_on` 一行未动**。
  3. `embodied_agent/perception/observe.py`：`PerceptionAssembler._footprint_gap` 的
     docstring 收窄（原来声称"用半对角所以只会误报相邻、不会漏报"，实际用的是 yaw-only
     半径，顶翻的体会被漏判）。**仅文档：一条断言、一个数字都没动，P1-b 的表仍然有效。**
- **对封存批次的影响**：**零，但不是"逐字节零"**。默认值 `True` 让每一个 v0.1 特权快照的
  `model_payload()`（模型真正看到的那个投影）**逐键不变**——`orientation_measured` 不在
  实体行的键里，`yaw_rad` 仍照旧算出。序列化侧确实多了一个键：
  `EntityState.model_dump()` 现在带 `orientation_measured: true`（实测键集 15 个），
  而**读回旧记录无损**：缺该键的 dump 仍然校验通过并取默认 `True`
  （`EntityState.model_validate(去掉该键的 dump).orientation_measured is True`）。
  `/tmp/bst_v01/full/` 的 268 集产物不被回写。钉在
  `test_a_privileged_snapshot_is_untouched_by_the_new_field`：所有实体
  `orientation_measured is True` 且 `entity_footprint_half_xy` 仍等于
  `footprint_half_xy_from_quat(geometry, pose.quaternion_xyzw)`。v0.2 的
  `schema_fingerprint()` 七个键与 `configs/experiment/v02_schema_freeze.json` 仍逐项相等
  （指纹只覆盖 `core/v02.py` 文本），
  `cli prereg --check configs/experiment/p3_preregistration_v1.json` 仍输出
  `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3` ⇒ 不重冻结。
- **回归**：全量 `492 passed / 21 skipped（180.74 s）`；对照
  `pytest -q tests --ignore=tests/contract/test_v02_perception_world_state.py` →
  `473 passed / 21 skipped（186.58 s）`，差值 19 与新增文件的 collected 数逐条相等
  ⇒ v0.1 那套测试一条没红、一条没改。
- **实测的下游代价（不记就是隐瞒）**：加宽界让传感通道的"完全在内"判定变严。同一批
  5 个坐进 `tray_middle` 的物体上，占用判定与特权快照一致的比例从 `main` 5/5、
  `front_high` 4/5 变为 **`main` 4/5、`front_high` 3/5**；错误判决的分布是
  `conservative` 1→**3**（说"没放进去"而真值放了）、`dangerous` **保持 0**（加宽界在
  几何上**不可能**造出这一类，剩余风险只在位置估计）。footprint 被加宽的量：中位
  21.1 mm、最大 24.9 mm（托盘内壁半宽 131.0 mm）。数字来自
  `work/p1_state_check.py` 的 `placement_parity` 块。
- **为什么要记一条**：`EntityState` 是 v0.1 与 v0.2 **共用**的快照类型，也是
  `verify`/`measure_occupancy`/`model_payload` 三条链路的唯一输入。本轮之前的代码里，
  一个"从深度图反投影、姿态其实是按直立假设写出来的"实体与一个" simulator 直接给的"
  实体在类型上**无法区分**，于是它们在同一张占用表里共享 `footprint_half_xy` ——
  这就是 §5.1"禁止用 hidden state 代替主路径"最想防的那类静默乐观。加一个带默认值的
  标志位是把决定权交还给几何代码而不是收紧某个阈值；若不记账，P5 的比较会看到两条通道
  的 `fully_inside` 定义在 2026-09-20 当天**变了**，而两边的分数都还没重测。

## D44 —— v0.2 P1-d 动了共享底座：验证器可以是"用一帧回答的"，循环多了一个记账 seam（2026-09-20）

- **改了什么**（两个文件，全部是"加一个默认不变的分支"，无删除、无签名收紧）：
  1. `embodied_agent/core/verify.py`：
     - `RuntimeVerifier.__init__` 新增关键字参数
       `source: Source = Source.privileged`（:318-322），类内 **6 处**报告构造点从字面量
       `Source.privileged` 改带 `self.source`（:330, :376, :383, :416, :454, :457）。
       **`build_world_state` 里的两处（:213, :226）仍是 `Source.privileged`** —— 那个函数
       本身就是特权测量，不是通道。
     - 新增 `RuntimeVerifier.outcome_status(result, reports)`（:333-343），**返回 `None`**：
       基类拒绝替第 1 层做决定，v0.1 封存的状态词表因此**一处未改**。唯一的覆写者是
       `perception/verify_percept.PerceptVerifier`（v0.2 新文件，不在此账内）。
     - 三条 `pose is None` 守卫：`verify_grasp`（:352-354，`verify_placement` 同理
       :395-399）、`before` 快照的取值（:357-358）。特权快照永不产生 `pose=None`
       （`build_world_state` 每行都写 pose），所以这三条**在 v0.1 路径上不可达**；它们把
       "传感状态里有一个看见了但没量出米的 body"从 `AttributeError` 变成一条 `unknown` 报告。
     - **未动**：`footprint_inside_region`（:69）、`state_inside`（:263）、
       `state_supported_on`（:267）、`measure_occupancy`（:230）、`VerifyConfig` 的任何阈值。
       传感通道判"在里面"用的就是特权验证器一直在用的那同一个函数、同一条
       `margin`（`+5 mm`）；`git diff -U0` 里这些函数零行变化。
  2. `embodied_agent/core/runtime.py`：
     - 新增后端接缝 `Runtime._outcome_status(result, reports, verifier)`（:204-221），与
       `_verification_reports`（:194）并列。它用 `getattr(verifier, "outcome_status", None)`
       **查**方法而不是调：`_verifier` 是鸭子类型的（文本后端只给 `progress`/`verify_goals`），
       查不到就原样返回 `(result.status, result.failure_code)`。
     - `_build_feedback`：`status=result.status.value, failure_code=result.failure_code`
       → `status=outcome.value, failure_code=outcome_failure`（:632 调用、:650 落字段）。
       **`executed=result.status != SkillStatus.rejected`、`stages_executed`、`_map_failure(result)`
       （:469 归账）与 `_finish_check`（:700-710）全部仍读第 1 层。**
- **对封存批次的影响**：**行为零变化，且这次是量出来的而不是推论**：
  `pytest -q tests --ignore=tests/contract/test_v02_percept_verification.py` →
  **492 passed / 21 skipped（253.52 s）**，与 P1-c 记的那一条逐字相同；全量
  **519 passed / 21 skipped（176.65 s）**，差值 27 与新文件 collected 数相等。
  本轮**没有**动 `core/contracts.py`/`core/v02.py`（那是 D43），`schema_fingerprint()`
  七个键与 `configs/experiment/v02_schema_freeze.json` 逐项相等
  （`module_sha256 65f2b2402c192a1b…`、`all_records_sha256 ac2f73562cfb4e6a…`）⇒ 不重冻结。
  预注册对 `core/runtime.py` 的两根针仍成立：I4（`eval_spec` 必须**缺席**）、
  I7（`Runtime.run_episode` 里的 `prologue.get("http_requests")`），
  `cli prereg --check configs/experiment/p3_preregistration_v1.json` 仍输出
  `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3`。
- **实测的新行为（不记就是隐瞒）**：`execution_feedback.status` 自本轮起**可以是**
  `"uncertain"` 并带 `failure_code="STATE_UNCERTAIN"` —— `SkillStatus.uncertain` 是
  P0 §3 记在"有声明、无生产者"清单上的那一个，本轮是它的第一次也是唯一一次产出。
  可达条件是三者的合取：`_verifier` 返回 `PerceptVerifier`、第 1 层是 `completed`、
  且**每条**报告都 `unknown`。v0.1 的 268 集与本轮之前的任何路径都不满足第一条，
  且这条不靠断言保证：参数化表里 `privileged-completed-1` 那一行**显式**断言基类对
  同样一批全 `unknown` 报告返回 `None`。**旧记录读回不受影响**：`status` 一直是字符串字段，
  `ExecutionFeedback` 的键集未变，事件信封仍按 `schema_version` 分代（D42）。
- **为什么要记一条**：`core/runtime.py` 与 `core/verify.py` 是 v0.1 封存批次与 v0.2 全部
  实验**共用**的那条循环-验证脊柱。本轮之后，"执行反馈里的状态词"与"验证报告里的
  `source`"都不再是一个常量而是一个 seam 的返回值；若 P5 有人拿 2026-09-20 之前落盘的
  `execution_feedback` 批次和之后的传感臂批次并表统计，他会看到 `uncertain` 这个值
  在两边的**可达性**不同，而两边的规则哈希都一样。

## D45 —— v0.2 P1-e 换了一条观察通道：三个入口各多一组开关，一处评分记录多写仪器名（2026-09-20）

- **改了什么**（`core/*` 本轮**一行未动**；改的是三个生产入口 + 一个新文件）：
  1. `embodied_agent/cli.py`：`_add_perception_flags`（:116-…）在 `run` 与 `evaluate` 两个
     入口都挂 `--perceive/--ablation/--view/--views`，默认 `privileged` / `None` / `main` /
     `None` ⇒ **封存的 v0.1 调用串一个字符都不用改**。`_perception_kwargs`（:149-186）把
     "通道 × 臂"的拒绝做成**一次说完的列表**而不是异常。
     - 本轮抓到并修掉的**真 bug（新代码里的，不是 v0.1 缺陷）**：:177 把
       `ABLATION_CONDITIONS` 写成 `ABULATION_CONDITIONS` ⇒ "未知 ablation 条件"这条**拒绝**
       本身抛 `NameError`。判据
       `test_the_cli_refuses_every_incoherent_pair_before_a_scene_opens` 逼出来；全仓仅此一处。
  2. `embodied_agent/evaluation/run.py`：`run_one_episode`（:258-261）与 `run_group`
     （:393-398）各多 `perceive/ablation/adapter/views`（+ `default_view`）关键字，
     **默认值全等于 v0.1 路径**（逐参数断言：`test_every_new_knob_defaults_to_the_v01_path`）；
     批次预检（:431-449）在**开任何场景、建任何目录之前**抛 `InfraError`（判据断言
     `os.listdir(out_root) == []`）；run id 仅在非默认通道时带 channel（:474-475）⇒
     默认批次的目录名不变；每集摘要多一个 `perception` 块（:319-330：通道 / 臂 / 视角集 /
     look 数 / token / look-http / `grounding_map_sha256` / 两类事件计数）——默认通道下它是
     "全零 + `arm: "unset"`"，**不是缺席**。
  3. `embodied_agent/evaluation/evaluator.py`：`score()` 的返回 dict 多一个纯 provenance 键
     `scored_entities_from`（:96）。**不改任何判据、不改 `scored_observation_ref`**
     （§7"一个样本两个判决"那条同一性仍由 `test_episodes.py:113` 持有）。
  4. 新文件 `embodied_agent/perception/arm.py`（490 行）：`Perceiver` + `SensingVerifier` +
     `PerceptRuntime`（十个 seam）+ `build_arm`。`privileged` 通道返回的仍是 v0.1 那个
     `Runtime` 对象本体（`type(privileged) is Runtime`）。
- **对封存批次的影响**：全量 **559 passed / 21 skipped（214.79 s）**。本轮没动
  `core/contracts.py`/`core/v02.py` ⇒ `schema_fingerprint()` 七个键与
  `configs/experiment/v02_schema_freeze.json` 逐项相等（`module_sha256 65f2b2402c192a1b…`、
  `all_records_sha256 ac2f73562cfb4e6a…`），`cli prereg --check` 仍输出
  `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3` ⇒ **不重冻结**。
  `episode_summary.json` 的两处新增都是**加键**：按名取值的 `report.py`/`probe` 与旧批次读回
  不受影响。
- **实测的新行为（不记就是隐瞒）**：
  1. **`max_http_requests` 第一次在 pybullet 路上能结束一集**，且**只在传感臂上**
     （`PerceptRuntime._preflight_trip`，:413-…）。量测：`costed_stub` 一集 9 轮 / 40 look，
     note 原文 `http request budget 32 spent before the next round (39 looks billed to
     36 requests)`。v0.1 特权环仍**只报告不执法**；归档 `runs/*/results.csv` 34 批 904 行的
     `model_requests` 峰值 **3**（分布 {0:659, 1:226, 2:10, 3:9}、均值 0.302）vs 声明 32
     ⇒ 对既有 904 行是空操作。环级武装留给冻结前的决定（phase log P1-e 残留 2）。
  2. **`ablation` 记录第一次由生产路径落盘**，且先于该臂任何受它管辖的记录：
     `condition` / `modules_off=["vlm"]` / `registry_sha256` / `notes`（通道、视角集、
     `grounding_map sha256[:12]`）。v0.1 归档里 `ablation` 与 `perception` 两种类型一条都没有；
     两个类型名在 D42 的 v02 注册表里早已冻结 ⇒ 加一条臂不动规则哈希。
  3. **同一批字段换了仪器**：传感臂的快照里 `entity.body_id == -1`、
     `orientation_measured == False`、位姿是反投影估计、`held_object` 带
     `held_source: actuator`；两个不同相机的 diff 回 `not_comparable` 而**不是**空 diff。
     评分那侧不变：判据逐位核对 `snapshot.entities[eid]["pos"] == scene.object_pose(eid)`，
     所以表 11 里"循环自报 1/5 vs 独立评分 0/5"是**两个仪器的两个答案**，不是同一个答案被抄了两遍。
  4. **§6.2 的"相同无效尝试"守卫在两臂上语义不同（量出来的）**：同一相机连拍两帧指纹**相等**
     （`observation_ref`/`state_version` 本就不在指纹里），跨视角**不等**——`dev_c5` 一张没动过
     的桌子：`main`↔`overhead` **15.0 mm**、`front_high`↔`main` **18.0 mm**、
     `front_high`↔`overhead` **13.0 mm** ⇒ 换相机会重置计数。P5 并表时
     `identical_repeats_total` 在两臂间不可比，除非先按 phase log P1-e 残留 3 写口径。
- **为什么要记一条**：这一轮第一次让"同任务、同 seed、同预算"的两个批次**读不同的世界**。
  把 2026-09-20 之前的 privileged 批次与之后的 stub/vlm 批次并表的人会看到四种此前不可能出现的
  形状：一集可以被 HTTP 上限结束、`ablation` 记录存在与否、快照位姿来自像素、以及自报完成数与
  独立评分系统性分开。规则哈希、契约代、评分 spec 全都没变——**变的只是那一帧是谁拍的**。

## D46 —— v0.2 P1-f 只加了一个读侧工具：24 条 §11 口径随产物发布，两臂对照的差值表**只从 CSV 读回**（2026-09-21）

- **改了什么**（`core/*` 本轮**一行未动**，写侧**一个字段都没加**）：
  1. 新文件 `embodied_agent/evaluation/vlm_contrast.py`（1800 行）：24 条 `METRIC_DEFINITIONS`
     （V1 5 / V2 4 / V3 11 / C1 3 / X1 1，:238-511）+ 四组感知电池（:745/:807/:888/:1053，
     每用例开**一个** `PhysicsScene`）+ 两臂 episode 对照（:1424-1605）+ 渲染与落盘
     （:1641/:1759）。`DEFINITION_VERSION`（:203）= `v0.2-p1f-6`。
  2. 新判据 `tests/contract/test_v02_vlm_contrast.py`（679 行 / 31 条）。
  3. `embodied_agent/cli.py` 多一个子命令 `vlm-contrast`（:454 `cmd_vlm_contrast`、
     :706-737 挂载）。相对 v0.1 冻结提交 `4e960a2` 的累计 diff 是 +255/−7，其中**本轮**只
     贡献这一个子命令：v0.2 的 P0/P1-e 改动（`prereg`、`--perceive`）也在这 255 行里。
  4. **纯读侧**这一条是可核对的：`episode_contrast` 把两臂各跑一次 `run_group`，然后所有
     进差值表的数字**只从 `report.load_rows`（`report.py:56-65`）读回的 `results.csv` 来**；
     `progress_self_report_vs_key` 读的是 `episode_summary.json` 里**已存在**的
     `result`/`score` 两块。⇒ `run.py` 的 `CSV_FIELDS`、`_csv_row`、第 3 层评分器一行未改。
- **对封存批次的影响**：全量 **590 passed / 21 skipped（281.84 s）**（同一批代码此前一次是
  336.58 s，差在机器负载 —— 见下第 4 条）。本轮没动 `core/contracts.py` / `core/v02.py` /
  `core/runtime.py` ⇒ `schema_fingerprint()` 与 `configs/experiment/v02_schema_freeze.json`
  逐项相等，`cli prereg --check` 仍输出
  `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3` ⇒ **不重冻结**；BST v0.1
  的 34 批 / 904 行 `runs/*/results.csv` 与 Phase 3–5 的哈希链路不受影响（写侧未动，
  且新工具**不读**那些旧批次的任何列）。
- **实测的新行为（不记就是隐瞒）**：
  1. **口径成了一个有版本的数据对象**：每个产物带 `definition_version` 与整份
     `definitions`，判据 `test_the_definitions_a_run_shipped_with_are_the_ones_it_rendered_from`
     要求产物里的口径与**当前代码**逐条相等 ⇒ "改了口径文字没重跑产物"和"重跑产物没改口径"
     都当场红。这带来一条新纪律：**任何一次修订都要重跑它描述的那个产物**（`v0.2-p1f-1 … -6`
     之间五次修订里，最后两次分别重跑了 dev 两臂批量与 47 用例批量）。
  2. **两臂对照表里有两列此前被静默排除**（`v0.2-p1f-5` 产物）：`load_rows` 把
     `independent_complete_success` / `agent_claims_success` 解析成 **Python `bool`**，而
     `_number` 拒绝 bool ⇒ 它们没有进差值表，只出现在 `not_a_quantity_on_every_pair`。
     修法是声明成"类"（交叉表 + 配对迁移，`CONTRAST_CATEGORIES` :540），不是让数值函数接受
     bool。⇒ 任何**下游**把这两列当数值平均的脚本都要按这个形状写；旧产物里它们本来就缺席。
  3. **`objects_completed` 的仪器被本轮改判**：它是**第 3 层从模拟器读的键**（`_csv_row` 从
     `score` 取），不是循环自报 —— 此前被 `ARM_ASYMMETRIC_FIELDS` 标成了后者。循环自己的计数
     现在并排发布在 `progress_self_report_vs_key` 里，实测：`sensing` 8 集里 **4** 集自报多于
     键（`dev_c3/c4/c5/c6`），`privileged` **0** 集。⇒ P5 并表时"自报 vs 独立评分"这一对
     在 v0.1 归档里**没有**对应列，只有 `model_requests`/`objects_completed` 那些单边数。
  4. **`wall_time_s` 不可复现，量出来了**：同一个 8 集 dev 批量在代码只差文字的情况下四次得到
     474.09 / 573.66 / 627.16 / 614.74 s 的 sensing wall 总和，而 `sim_time_s` 恒 **246.59 s**
     （一集接一集跑，`run.py` 里没有任何进程池）⇒ 本轮把它列进 `†`（仪器/单位随通道变）并把
     观测写进产物文本，而不是删掉那一列。全量回归 281.84 s 对 336.58 s 是同一件事的复现。
  5. **`‡` 是一类新的"看起来能减其实不能减"**：`decision_rounds` / `skill_calls` 单位随两臂
     一致，但 8 对里 5 对被 24 轮上限截断 ⇒ 均值差 **+8.125 / +8.875 是下界**。此前 D45 只记了
     仪器不对称那一类。
  6. **成本形状先于成本数字**：`C1` 三条（`looks_per_round` / `model_calls` / `tokens`）是
     计数之比，发布 `interval_kind: "ratio_of_counts"` 且**不给区间**（Wilson 需要 0 ≤ k ≤ n）；
     实测 `looks_per_round` = 596/138 = **4.3188**，`model_calls`/`tokens` 分子 0
     （`StubReader`，`coverage.model_money_spent: 0`，`api_cost_estimate`/`cost_estimate_usd`
     仍 null）。⇒ "每轮 4.32 次观察"现在是**批量数**而不是 P1-e 的单集数（105/24 = 4.375）。
- **为什么要记一条**：从这一轮起，v0.2 的 §11 数字有了**版本化的口径**，因而"这个数是按哪套
  规则算的"变成产物里可核对的一项，而不是散在注释里的说法。要把 2026-09-21 之前的 v0.2 产物
  （`v0.2-p1f-1 … -5`）与之后的并表的人会遇到三处形状变化：两列判决从"缺席/被当数值"变成
  "交叉表"、`objects_completed` 的仪器换了名字、以及两列被 `‡` 标成截断。评分规则、契约代、
  事件类型、写侧 schema 全都没动 —— **变的只是怎么读，不是怎么算**。

## D47 —— v0.2 P2 把"计划 + 工作记忆"接进闭环：6 个 P0 已声明的事件类型第一次有生产者，`long_horizon` 集合**不进** `_REGISTERED_SETS`，写侧 schema 不重冻结（2026-09-21）

- **改了什么**（本轮新增，`git diff` 切不出"P2 单独一行"，理由见下第 4 条）：
  1. 新包 **`embodied_agent/planning/`（8 文件 / 3003 行）**：`understanding.py`(100) /
     `subgoals.py`(406) / `task_planner.py`(600) / `working_memory.py`(631) / `view.py`(342) /
     `arm.py`(583, `PlanningRuntime`，六个 seam) / `policy.py`(332, 只读 payload 的规则策略) /
     `__init__.py`(9)。
  2. 新文件 **`evaluation/long_horizon_tasks.py`(397)** + 独立 manifest
     **`configs/experiment/frozen_long_horizon_v1.json`**（6 用例、seed 301-306、30 轮 / 40 次
     技能调用，四臂同一份，先申报后跑）+ **`evaluation/long_horizon.py`(1249)**
     （`METRIC_VERSION="LH-2"`，18 条 `METRIC_DEFINITIONS`：L1 4 / L2 2 / L3 5 / L4 2 / L5 2 / L6 3）。
  3. 新判据 **5 个文件 / 3642 行 / 131 条**（`test_v02_task_planner.py` 1120/41、
     `test_v02_working_memory.py` 864/18、`test_v02_planning_arm.py` 553/22、
     `test_v02_long_horizon_set.py` 351/13、`test_v02_long_horizon_metrics.py` 754/37）。
  4. **`--set` 的集合表多一个可解析的名字，但哈希集合不变**：`long_horizon` 进 `_SET_BUILDERS`
     （`tasks.py:762`）而**刻意不进 `_REGISTERED_SETS`**（:720 仍是
     `smoke/dev/formal/protocol`），于是 `_all_cases()`（:723）与
     `tests/unit/test_frozen_tasks.py:241` 那条"47 个用例、按 id 去重"的等式**一处未动**；
     `cli evaluate --set long_horizon` 走的是同一个 `run_group`，`--ablation` 在 `privileged` 通道
     上接受四个 planning 臂（`cli.py:205-212`），并新增 `--policy {rule,payload}`（:143）。
- **对封存批次的影响**：全量 **722 passed / 21 skipped（526.18 s）**（P1-f 时是 590+21 ⇒ 净增 132
  条，21 条 skip 全是需要在线模型/外部后端的 skipif 门 ⇒ 本轮零花费）。
  `schema_fingerprint()` 与 `configs/experiment/v02_schema_freeze.json` **逐项相等**
  （`schema_version "4"`、`module_sha256 65f2b240…`、`records_sha256 112b2df8…`、
  `all_records_sha256 ac2f7356…`、`event_types_sha256 58f691b8…`、
  `ablation_registry_sha256 db486f63…`），`cli prereg --check` 仍输出
  `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3` ⇒ **不重冻结**。
  BST v0.1 的 34 批 / 904 行 `runs/*/results.csv`、Phase 3-5 的哈希链路本轮**没被读也没被写**：
  P2 的全部产物在仓库外（`/tmp/p2e_full/**`），dev 探针在 `work/` 且对仓库只读。
  **但 v0.2 至今一行未提交**（`HEAD` 仍是 v0.1 冻结提交 `4e960a2`）⇒ "P2 相对 P0/P1 的增量"只能靠
  文件清单说，不能靠 git 说；本轮能核对的是指纹、prereg、封存目录未被触碰与 v0.1 回归全绿。
- **实测的新行为（不记就是隐瞒）**：
  1. **六个 P0 声明过却没人生产的事件类型，从本轮起真的在盘上**：单集 `events.jsonl` 的实测组成是
     `episode_start 1 / ablation 1 / observation 47 / task_understanding 1 / plan 1 /
     working_memory 11 / decision_context 11 / decision 11 / skill_call 10 /
     execution_feedback 11 / environment_event 1 / plan_revision 1 / recovery_action 2 /
     finish_check 1 / obligation_check 1 / episode_end 1`。⇒ **`episode_summary.json` 的键没变**
     （`artifacts/case_id/decision_source/environment_events/episode_id/expected/goal_resolution/
     mode/perception/probe/repeat/result/score/set/subset/wall_time_s`），新东西全在 `events.jsonl`
     与 payload 的两段里 ⇒ 下游"读 summary 就够"的脚本会**看不见 P2 的全部内容**。
  2. **`invalid_completion` 是一个新列的形状，且它不是独立评分的假阳性**：它是循环**自己承认**的
     "欠着还说完成"（`obligation_check`），v0.1 归档里**没有**对应列。实测（48 集，每集一个值）：
     `full/rule` 未了债务 `10,12,12,9,10,10` + `invalid_completion` **True×5**，
     `full/payload` `0,0,0,4,0,6` + **全 False**，`wo_planning/payload` `10,12,12,11,10,15` + True×4。
     ⇒ "计划有没有用"这条结论只能写在 `payload` 侧（`rule` 侧 `wo_planning/rule ≡ full/rule` 逐集恒等：
     该控制组根本不读计划，所以它对消融无感）。
  3. **四个消融开关里只有一个推动了 §11 的一行**：`arm_contrast` 96 行对照里非零 delta 只有两条，
     都是 `L6.recorded_vs_inferred` 1.0 → **0.0**（`wo_replanning`×`rule` 2/2→0/2、×`payload`
     3/3→0/3）。`wo_planning` 的效果全是"三行变成没有分母"（开关，不是成绩），
     `wo_working_memory` **一行都不动**且 `obligation_check` 整块缺席（`arm.py:25-34` 的门控设计）。
     ⇒ 早期一次读数把 payload/rule 之差**误报成** working-memory 臂的效果，已撤回并写进 P2-f。
  4. **§11 的 dependency 行这一批是空集，而且空集有正面证据**：`L2.dependency_edge_violation`
     8 格全部 not measured，理由文本随产物发布；新发布的每格 `row_kinds` 是
     `{'achieve': 30}`（6 格）或 `{}`（两个 `wo_planning` 格）⇒ **一条依赖边都没写出来**。
     同批 `spec8_shape_2.status = "not_applicable"`（"不可写"，实测于 `work/p2d_*.py` 六条探针，
     f6 那一次：临时槽 47 个可选、受害者空闲槽 6、尝试 6、**完成 0**，首次失败
     `lift to transfer height, tcp_err_m=0.018`）。⇒ 把 L2 的 not measured 读成"零违规"是错的。
  5. **`MAX_OBJECTS_PER_REGION = 3`（`tasks.py:50`）是任务作者的声明，不是世界的行为**：
     读它的是 `_respect_region_capacity`、`calibration.py`、`long_horizon_tasks.py` 与判据，
     **`planning/` 一行都不读**（那里走 `subgoals._capacity_dependencies` :365 + 生成器在当前快照上
     的重检）。⇒ 任何"超过 3 就该判阻塞"的下游预期都不成立。
  6. **一个 v0.1 就存在的仪器宽判，本轮量出来了没改**：`PlacementPlanner._reachability`
     （`core/placement_planner.py:241`）只测**空手** IK 在 `(x,y,0.80)` 与 `(x,y,release_z)` 两点的
     残差（`scene.reachable`，`core/scene.py:363`），下探包络 `HAND_SWEEP_RADIUS_M=0.050` 只用于
     **邻居**（:224）⇒ 携带体几何既不在解算里也不在包络里，于是 6 个通过 `recheck` 的槽位 0 个可执行
     （差 17–18 mm）。本轮**没动它**：改它会让 v0.1 那 34 批的候选可行性口径与 P2 之后不一致。
  7. **两套账不一致（v0.1 计数器 vs v0.2 事件流）**：48 集上 `retry_events`/`recovery_events`/
     `replan_events` 求和恒 **0**，而同一批事件流里有 `recovery_action` 与 `plan_revision`
     （L6 的分子正是从事件读出来的：`full/rule` 2/2、`full/payload` 3/3）。另
     `slot_resolution_fallbacks` 每格 **29-30**（几乎每次放置都走确定性回退）⇒
     `L4.plan_asked_move` 的 0/30 只说明"决策没点名候选"，不说明"槽位选得好"。
  8. **两个形状陷阱**：(a) 同一个"真"在 `SatisfiedRecord.satisfied` 是 `str(bool)` 的 `"True"`、
     在 `Subgoal.satisfied` 是 `str(SkillStatus)` 的 `"completed"` ⇒ 任何 `x or default` 读法在其中一个
     上静默走错（`False or ""` 是 `""`）；(b) 计划行序改用行自己的 `row_key`
     （`policy.py:25`，`subgoals.row_key`）—— 此前用 `hash()` 派生 ⇒ **同一份 payload 在两个进程里
     给出两条不同轨迹**，重放旧 P2 payload 的脚本必须按新形状写。
  9. **口径修订不改变数**：LH-1→LH-2 五处修正（同轮 page 配对、损失/回归分两本账、
     `_cause_window(hi=None)` 越界、`cause["agent"]` KeyError、缺 `Counter` import）之后，产物
     **重跑三次，池化指标 0 处不同**；本轮还把 `spec8_shape_2` 与 `row_kinds` 从"只在 .md 的散文"
     变成 JSON 字段，并把两处 31-40 字符的 not-measured 理由改成自足整句（**没有下调那条 40 字符
     阈值**来让旧文本过关）。产物 `definitions` 与代码 `METRIC_DEFINITIONS` 18 条逐字段相等
     （程序核对：diff 键 0）。
- **为什么要记一条**：P2 是 v0.2 第一次**关掉一个模块就会改变产物形状**的阶段 —— 之前 v0.1 的消融
  只动 prompt 的措辞。于是并表时会遇到五类新形状：新事件类型只在 `events.jsonl`、`invalid_completion`
  这类"循环自认"的列 v0.1 没有、§11 的一行必须读成"没有分母"而不是 0、两套完成/恢复计数器并存、
  以及行序从 `hash()` 变成 `row_key`。而写侧契约、47 用例集合、评分规则、封存批次哈希**一处未动**。

## D48 —— v0.2 P3 把"经验 → 检索 → 参考 → 残渣"接进闭环：3 个 P0 声明过的事件类型有生产者了，`em` 集合**不进** `_REGISTERED_SETS`，记忆段自占一个字符池，写侧 schema 不重冻结（2026-09-21）

- **改了什么**（本轮新增；v0.2 一行未提交，`git diff` 切不出"P3 单独一行"）：
  1. 新包 **`embodied_agent/episodic/`（6 文件 / 1438 行）**：`experience.py`(332, §5.4 七字段的**映射**，
     只读 `episode_summary.json` + `events.jsonl`) / `store.py`(136, 只追加 JSONL) /
     `retrieval.py`(313, 声明式相关性 + 确定性排序 + `contradicted_current_state` + `mark_used`) /
     `arm.py`(394, 三个 seam + `EPISODIC_ARMS`) / `policy.py`(206, `MemoryPolicy`，`provider =
     "rule_memory"`) / `__init__.py`(57)。
  2. 新文件 **`evaluation/episodic_tasks.py`(574)** + 独立 manifest
     **`configs/experiment/frozen_episodic_v1.json`**（8 用例 / 4 对 / 16 集 / 两臂同一份预算，
     `sha256 7df449ed2f4d6f0cc04555029df6e6ee998d485f90ef87075d6737d554ae0084`，先申报后跑）+
     **`evaluation/episodic_runs.py`(424)**（每 (pair, arm, role) 调一次生产批入口 `run_group`，
     **一对一个 store**）+ **`evaluation/episodic_metrics.py`(585)**（`METRIC_VERSION="MEM-1"`，
     13 条 `METRIC_DEFINITIONS` 铺满 §11 Memory 四行）。
  3. 新判据 **5 个文件 / 2903 行 / `def test` 131 条 / 按 pytest 收集 162 条**
     （`test_v02_episodic_experience.py` 574/35/48、`test_v02_episodic_retrieval.py` 429/27/38、
     `test_v02_episodic_arm.py` 710/18/18、`test_v02_episodic_set.py` 481/19/26、
     `test_v02_episodic_memory_metrics.py` 709/32/32）。
  4. **接线只占三个既有槽，且不动注册表**：`planning/arm.py:203` 调 `self._offer_memories(...)`，
     默认实现 `:295` 返回 `None`（⇒ 不装记忆的臂**逐字节还是 P2 那一页**）；`run.py` 的 `policy`
     多一个合法值 `"memory"`（:231-254 单集 / :541-552 批，且要求 `experience_store` 已装否则拒绝跑）；
     `wo_episodic_memory` 自 P0 就在 `core/v02.py:731` 的 `ABLATION_CONDITIONS` 里 ⇒
     `ablation_registry_sha256` **没动**。`em` 进 `_SET_BUILDERS`（`tasks.py:772`）而**刻意不进**
     `_REGISTERED_SETS`（:720 仍是 `smoke/dev/formal/protocol`）⇒ `_all_cases()` 与
     `tests/unit/test_frozen_tasks.py:241` 的"47 用例、按 id 去重"等式一处未动，
     `frozen_tasks_v1.json` 字节不变。
  5. **CLI 新增一个子命令与两个旗标**：`em-pairs --claims | --run | --measure | --dump | --check |
     --memory-metrics DIR | --memory-definitions`（后两个委托给 `episodic_metrics.main`，
     互斥门覆盖它们；指向非 run 目录 ⇒ 退出 3 的一句人话，不再是 traceback）。
- **对封存批次的影响**：全量 **885 passed / 21 skipped（559.02 s）**（P2-f 时 722+21 ⇒ 净增 163 条，
  其中 162 条来自上面那 5 个新文件）。`schema_fingerprint()` 与
  `configs/experiment/v02_schema_freeze.json` **逐项相等**（`schema_version "4"`、
  `module_sha256 65f2b240…`、`records_sha256 112b2df8…`、`all_records_sha256 ac2f7356…`、
  `event_types_sha256 58f691b8…`、`ablation_registry_sha256 db486f63…`、`v01_schema_version 3`），
  `cli prereg --check` 仍输出 `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3`，
  `cli em-pairs --check` 输出 `em set matches: 7df449ed2f4d` ⇒ **不重冻结**。
  BST v0.1 的 34 批 / 904 行 `runs/*/results.csv` 与 Phase 3-5 哈希链路本轮**没被读也没被写**：
  P3 全部产物在仓库外（`/tmp/em_full/**`），探针在 `work/` 且对仓库只读；整棵 `/tmp/em_full` 里
  `model_calls.jsonl` **0 个文件** ⇒ 本轮零花费。
- **实测的新行为（不记就是隐瞒）**：
  1. **三个 P0 声明过却无人生产的事件类型，从本轮起真的在盘上**：`memory_retrieval` / `memory_use` /
     `memory_write`。`full` 臂 reader 实测 `memory_retrieval 7 / memory_use 7 / memory_write 1`；
     `wo_episodic_memory` 臂**这三条完全缺席**，其余 13 类**逐键同数**（`decision 7 /
     decision_context 7 / observation 29 / skill_call 6 / working_memory 7 …`）⇒ 开关切的是模块，
     不是措辞。控制臂目录里**连 `store.jsonl` 都不生成**。`memory_write` 落在 `episode_end` **之后**，
     所以数记录的读者必须停在 `episode_end` 那个序号。
  2. **§11 的 reuse 行只能从两条轨迹读**：控制策略自报 `memory_followed` 覆盖 **14/28** 个 reader 轮次，
     其中 **6/14** 轮控制臂在同一轮选了同一个对象（全在 em_p4，`followed=[1..6]` 而轨迹全等）；
     真正**换路径且走到底**的是 **2/4 对**，终局判定改善 **0/4**（16 集全部 `success`）。
     MEM-1 因此在 `forbidden` 格里写死"自报不得被当作 reuse 报出"。
  3. **负迁移这一行在本批是地板，而且分两层**：guard 看得见 **8** 个被否证的 reader 轮次却
     **0 次 decline**（`M3.declines_per_refuted_round` 0/8 是量出来的 0），而
     `M3.decline_branch_reachable` 0/4 是**机制行**（这四对的存储里没有一条被记成 `false` 的谓词）。
     造得出该形状的探针（`work/p3d_decline_reachability.py`：6 个 `lh` 集、唯一那个 `partial` 写出
     **2 条 false 声明**）重跑 12 集仍是 **0 declines、0 分歧用例** —— 否证一条声明的只有量到 `true`
     的 reader，即做完了 writer 没做完的活的 reader，而一个确定性策略会重复另一个的失败。
     另：`work/p3d_order_cost_scan.py` 走完 15 个 em 形状任务的**全部 78 个放置顺序 ⇒ 78/78 完成、
     0 次被拒**（`_respect_region_capacity` + `MAX_OBJECTS_PER_REGION = 3` 封住了唯一物理通道）。
  4. **两种 stale 形状是两个数，不是一个**：`M4.refuted_and_still_governing` **8/8**（p1 的 2-4 轮 +
     p4 的 2-6 轮：被否证仍在支配 —— **等一轮就可能愈**的时间盲），`M4.uncheckable_and_governing`
     **4/7**（p2：本集计划不点名该谓词 ⇒ 永不查、永不否证 —— **等多久都不愈**的词表盲）。
     根因是 `arm.py::_memory_query` 用**本集自己的计划行**构造 `measured`，所以 guard 是**下限**。
  5. **否证判据从"按行 skip"改成"按对象 decline"，是量出来的不是设计的**：世界复位 ⇒ 成功 writer
     结尾每条 `placed:X:R` 在新 reader 上都量成 false，行级 veto 会否证 100% 的回忆、让
     `MemoryPolicy` 每轮退化成 `PlanPolicy`（一个被检索、被显示、按构造**无法改变任何东西**的记忆）。
     `dev_c1` 探针逐轮读数：第 1 轮三条 `unknown`、第 2 轮三条 `false`、第 3-4 轮两条、第 5-6 轮一条、
     第 7 轮三条又 `true` ⇒ guard 在 7 轮里开火 5 轮。代价同时记下：**关于"本集还欠着的活"的错误
     记忆会被照做**，因为分不出"过期"与"你说得对但我还没干完"。
  6. **相关性在批内只排序、不排除**：`RELEVANCE_WEIGHTS["task_kind"] = 3.0 ≥ MIN_RELEVANCE = 1.0`
     且运行时把查询的 `task_kind` 取成**批次集合名** ⇒ 同一 `em` 批里没有任何行会被这条公式滤掉；
     实测 `M1.matched_beyond_the_batch_name` 28/28（每行确实还有批名之外的匹配理由）但**这不代表能排除**。
     该性质随冻结清单的 `relevance_within_a_batch` 发布。
  7. **两个字符池是分开的**：`RECALL_BUDGET_CHARS = 2400`（`working_memory.py:88`）与债务块的
     `RENDER_BUDGET_CHARS = 900`（:70）⇒ 一条长记忆饿不死"本集欠着什么"。三处渲染上限
     `STEP_CAP 8 / MATCH_TERM_CAP 3 / CLASH_CAP 3` 裁的是页面，指针留在 `provenance.*`；
     记账字段 `recalled_json_chars` 明确声明"这一节的价钱不止 `recalled_chars`"。
  8. **`view_sha256` 因渲染器源码变化而变，且对两臂同值**：`planning/view.py:195` 哈希的是
     `plan_view` + `memory_view` + `V02DecisionContext.model_payload` + `WorkingMemory.render`
     四段**源码文本** ⇒ P3 改了 `WorkingMemory.render` 之后，产物里的哈希从 P2 的 `19e37182…`
     变成 `6b18efbd…`，而 `em_p1` 两臂两角色**逐轮同值**。它是"哪个视图机制在跑"的身份，
     不是"这一页内容"的身份 ⇒ **P2 与 P3 的视图哈希不可比**。
  9. **只追加的存储逼出一个批处理约束**：`--run` 进一个已有 `pairs_run.json` 的目录会被拒绝
     （reader 要测的是**一个** writer 的残渣，第二遍会拿两行的存储比一行的期望），`--force` 才放行；
     批级 `run_group` 的单存储被显式换成"一对一个 store"。
  10. **口径升级可以不改数，并且证明了**：MEM-1 首版发布之后把行格式对齐 LH-2（补
     `interval_kind` / `denominator_too_small_for_a_rate` / `not_measured_reason` 等 13 键，
     两条 `RATIO_METRICS` 明确不给 Wilson 区间），重跑 `measure` 与首版产物
     **池化 13 行 + 4 对 per-pair counts 逐格相同**（程序核对，drift = none）。
- **为什么要记一条**：P3 第一次让"关掉一个模块"同时改变**盘上文件集合**（`store.jsonl` 有无）、
  **事件类型集合**（三条 `memory_*`）与**决策依据**（同一 payload 在两臂下走两条不同轨迹：
  p1/p2 首手 `obj_blue_3`→`obj_green_2`）。并表时会遇到四类新形状：一行的单位是**对**而不是集
  （`M2`/`M3` 的分母 4 是"对"，`M1` 的分母 28 是"轮"）、一个 0 可能是"量到的 0"也可能是"机制上
  不可能非 0"、自报与行为两条账必须并排而不能互相代替、以及 §11 的一行必须按 Wilson 区间之外还有
  `interval_kind` 来读。而写侧契约、47 用例集合、评分规则、消融注册表、封存批次哈希**一处未动**。

## D49 —— v0.2 P4 把"缺口 → 程序 → 沙箱 → 跨实例 → 库"接成一条**有门**的管道：4 个 P0 声明过的事件类型有生产者了，`acquisition/` 全程零花费却把代价量了出来，`view_sha256` 头一回**不变**（2026-09-22）

- **改了什么**（本轮新增；v0.2 一行未提交，`git diff` 切不出"P4 单独一行"）：
  1. 新包 **`embodied_agent/acquisition/`（8 文件 / 3684 行）**：`program.py`(391，§5.5 的参数化程序
     表示 + `check` 的八条表示规则，**明确不判断会不会成功**) / `gap.py`(485，cell =
     (skill, entity_id, target_id, failure_code)，阈值 2) / `propose.py`(343，`propose_v2`，
     规则路与模型路两条出口) / `sandbox.py`(779，四条禁令各是一种**结构**) /
     `validation.py`(405，"unseen" 是**关系**、`repeat` 自成一类且**不贡献轴**) /
     `memory.py`(344，`admit` 是合取、`applicable()` 返回每个覆盖者而**不排序**) /
     `arm.py`(905，四个 seam + `build_skill_arm`) / `__init__.py`(32)。
  2. 新文件 **`evaluation/skill_metrics.py`(1099)**：`METRIC_VERSION = "SKILL-1"`，**28 条
     `METRIC_DEFINITIONS`** 铺满 §11 Skill Acquisition 五行 + Cost 组四行，另有
     `rows_without_an_id`（声明 §11 的第五行 `tokens` 在本口径**不设 id**、在别处回答）。
  3. 新判据 **7 个文件 / 4383 行 / `def test` 204 条 / 按 pytest 收集 222 条**
     （`..._gap.py` 528/32/41、`..._program.py` 341/25/34、`..._sandbox.py` 432/27/27、
     `..._validation.py` 387/19/19、`..._memory.py` 420/21/21、`..._arm.py` 1059/24/24、
     `..._metrics.py` 1216/56/56）。
  4. **接线只占既有槽，且不动注册表**：`run.py` 单集 `:300/:330-343`、批 `:570/:667-695`；
     `manifest` 多 `skill_acquisition` 块（`:806/:869`，阶段列表**取模块自己的 `PIPELINE`**）、
     run_id 打 `skillmem` 标签（`:783`）；`cli.py` 多 `--skill-memory PATH`（`:164/:229/:256`）。
     `wo_skill_acquisition` 自 P0 就在 `core/v02.py` 的 `ABLATION_CONDITIONS`（7 条）里 ⇒
     `ablation_registry_sha256` **没动**；四个 `skill_*` 类型自 P0 就在 `V02_EVENT_TYPES`（15 条）里 ⇒
     **写侧一字未改**。v0.1 的槽 `core/runtime.py:244` 的 `_skill_catalogue()` 被
     `acquisition/arm.py:454` 覆盖，而它在**库为空或模块被关**时原样 `return base` ⇒
     不装库的臂那一页与 P3 **逐字节相同**（只有真存在程序时才多一行）。
  5. **五道新的"拒跑"**（都是 `InfraError`，不是降级）：`--ablation wo_skill_acquisition` 却没装库；
     一个臂一批混两个 mode；`perceive != privileged` 且装了库（单集 `:370-373` / 批 `:727-738`，
     理由写明"记忆与相机两条通道还没交汇"）；`--propose` 取值不在 `{rule, model}`；
     `--propose model` 而没有 caller-supplied asker（`run.py:616-625`，"没有内建 provider、
     也没有离线回退"）。
  6. **CLI 新增一个子命令**：`skill-metrics --definitions | --measure DIR [DIR …] [--out DIR]`，
     **委托给模块自己的 `main`** ⇒ 打印的口径与算数的口径不可能分叉；退出码沿用 `run.py` 那一套
     （用法 2 / 不是 run 目录 3 / **臂被自己的日志推翻 4 且拒绝落盘**）。
- **对封存批次的影响**：全量 **1107 passed / 21 skipped（664.89 s）**（P3-e 时 885+21、P4-d 时
  1051+21 ⇒ 本轮净增 **56** 条，全部来自 `test_v02_acquisition_metrics.py`；21 条 skip 的**数目与
  P2-f/P3-e 相同**）。`schema_fingerprint()` 与 `configs/experiment/v02_schema_freeze.json` 的
  `schema_fingerprint` 键**逐项相等**（实测 `True`：`schema_version "4"`、`module_sha256 65f2b240…`、
  `records_sha256 112b2df8…`、`all_records_sha256 ac2f7356…`、`event_types_sha256 58f691b8…`、
  `ablation_registry_sha256 db486f63…`、`v01_schema_version 3`），`test_freeze_file_matches_the_code`
  通过，`cli prereg --check` 仍输出
  `23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3`，
  `cli em-pairs --check` 输出 `em set matches: 7df449ed2f4d` ⇒ **不重冻结**。
  BST v0.1 的 34 批 / 904 行 `runs/*/results.csv` 与 Phase 3-5 哈希链路本轮**没被读也没被写**；
  P4 全部产物在仓库外（`/tmp/p4e/**`），普查探针在 `work/` 且对仓库只读；整棵 `/tmp/p4e` 里
  `model_calls.jsonl` **0 个文件** ⇒ 本轮零花费。
- **实测的新行为（不记就是隐瞒）**：
  1. **四个 P0 声明过却一直无人生产的事件类型，从本轮起真的在盘上**：`skill_gap` / `skill_candidate` /
     `skill_validation` / `skill_library`。`lh_c6` 一臂实测四条各 **1**（其中 `skill_library` 那行是
     **拒绝**记录不是入库），`wo_skill_acquisition` 臂**四条全缺席**且其余记录逐键同数；
     `skill_candidate` **只在提议时记一次** ⇒ "候选数"就是一个候选一行的计数。
  2. **库是门造出来的，不是 episode 长出来的**：12 集真批次 `admissions` **全为 0**，
     `/tmp/p4d_warm` 那次冷→暖尝试跑完盘上**没有库文件可拷**（只有 `lib.jsonl.refusals.jsonl`）。
     于是 §11 reuse 行的分母来自一个用**生产门**（`validate_candidate` 在真物理上验 +
     `SkillMemory.admit` 收，没有一行手写）造出的种子库，这件事随产物发布（`notes.admission_gate`）
     —— 读 §11 那两行时必须记住分子里没有一次"真 episode 自己学到的技能"。
  3. **同一批上两根门给出两个不同的数，而差值本身是结论**：`S2.frozen_rule_share 2/4` 对
     `S2.strict_reading_share 0/4`。过冻结规则的两份是 `lh_c6` 的 `pick_up`
     （`frozen_reasons: []`，但只动 `['layout','object']` ⇒ 严格读数点名
     `"§5.6 axes not varied on a run instance: ['parameters']"`）；没过冻结规则的两份是
     `pr_target_full` 的 `pick_and_place`（**三根轴齐**却 `frozen_reasons: ["an instance failed the
     effect check"]`）。⇒ 这是 §12.2"门在正式使用前冻结、之后不放宽"在真物理上的**代价**，
     两种读法因此**并排印**而**不合并**。
  4. **没有任何已装好的控制策略读那一节**：`core/contracts.py:SkillName` 是冻结 `Literal`、
     `core/planner.py` 拒绝动作空间之外的名字 ⇒ 一个决定说不出"我用的是 `move_object`"，
     臂因此**主动扣住** `counts_as_reuse`（SKILL-1 里那格是 not measured 且带着臂自己的理由）。
     实测两臂 reuse 行**逐键相同**只差 `library_offered`，且 `offer_rounds == decision_rounds`
     （14/14）—— 程序一旦装上，**每一轮**都在页上而**没有一轮**被指名使用。
  5. **解除武装的臂照样返回一行**（`"module_off": true`、五阶段 `not_attempted`、reuse 行仍算并带
     `library_offered: false`）—— §5.1 缺席是一个名字；而 `Ablation.violations()` 查的是**日志**
     不是文件形状 ⇒ 该臂上多一条 `skill_*` 记录是**一集失败**，不是一处可隐藏的缺陷。
  6. **`view_sha256` 在页面多出一节的情况下不变**（P3→P4 仍是 `6b18efbd79eb`，6 批 100 条
     `working_memory` 记录实测同一值，而 `dev` 的 `full` 臂每轮页面上多一个程序）。这是 D48
     第 8 条的**镜像**：`planning/view.py:195` 哈希的是四个渲染函数的**源码文本**，P4 一个字没改
     它们 ⇒ 视图哈希既不能证明两臂内容相同、也不能证明不同；要证"看到的东西不同"只能看
     `offer.chars` 与 `acquired_programs` 的有无。
  7. **零花费不等于零代价**：4 个候选 `5.854 s` 墙钟（**1.4635 s/候选**）、14 次 sandbox 运行
     `40.452 s` 仿真（**2.8894 s/运行**、**3.5 运行/候选**）、占当集墙钟 **0.3777**；
     `C2.model_calls 0/6` 是被**测**出来的（0 个 `model_calls.jsonl`），不是被声称的。
  8. **两条缺陷是契约测试抓出来的，且都在发布之前**（记下来是因为它们都朝"数字变好看"的方向）：
     `S1.budget_left_unvalidated` 的分母原本写"提议数" ⇒ 一行可以把自己的分子算进别人的分母，
     已改为**可执行数**；`main` 在 rc 4（臂与自己的日志矛盾）时原本仍会**落盘** ⇒ 现在什么都不写，
     表留在 stdout（一份叫 `skill_acquisition_SKILL_1.json` 的文件会在"FAILED"滚过去很久之后
     仍被当作交付物读）。
  9. **口径升级可以不改数，并且证明了**：`tokens` 那一格（含它引用的 `run.py:PROLOGUE_COUNTERS`
     两个计数器名与 `report.py` 的两处 `_total`）是在首版产物发布**之后**补的；重写产物后程序比对
     ⇒ **28 行 metric 行逐键相同、`batches[]`（含 `per_episode`）相同、`notes` 相同、
     `definitions` 28 条一字未改**，唯一变化是新增顶层键 `rows_without_an_id`。
  10. **检测器的沉默是可断言的**：`missing_because` 的合法枚举值 `precondition_unmet` 与 `other`
      在本实现里**永不发出**（一次前置条件拒绝是 §5.2 在正常工作，缺的是一个**决定**）；
      动手前对 **24 个 run 目录 / 64 集**的普查给出 32 个 cell ⇒ **12 报 / 20 不报**，
      不报的逐个核对全为 `single_attempt_failure`，`gaps_already_covered_by_the_library` 当时为 0。
  11. **一条完整性事件**：核对脚本里把 `glob` 模式写成 `{long_horizon,protocol}/…`（python 的
      `glob` 不支持花括号展开）⇒ **静默返回空**，看起来像"产物里没有 reuse 行"。改成两次显式
      glob 后 4 行全在。任何"检查通过"的结论都要能指出那个空列表**意味着**什么。
- **为什么要记一条**：P4 第一次让一个模块的开关同时改变**事件类型集合**、**页面上有一节的有无**与
  **一份独立产物（库文件 + `.refusals.jsonl` 边车）的有无**，并给并表带来五类新形状：①一行可以是
  **两个权威的两个数**（冻结门 vs 严格读法），印成一个就把 §12.2 抹平了；②"not measured" 现在有
  **四种不同理由**（单批不池化 / 契约层没有名字 / 0 分母 / 门没机会被抓到），**都不许印成 0**；
  ③§11 的一行可以在**这里没有 id** 的情况下被回答（`tokens`），产物那格点名真正回答它的地方；
  ④reuse 行的分母可能来自**门造出来的种子**而不是任何真 episode；⑤哈希**不变**而内容变（与 P3
  的方向相反）。而写侧契约（15 个事件类型、`ABLATION_CONDITIONS`、`SkillName`）、47 用例集合、
  评分规则、消融注册表、封存批次哈希与 prereg/em 两份 `--check` 哈希**一处未动**。

## D50 —— v0.2 P5 把闭环搬到相机通道与一个不是自己写的环境上：**删掉四条拒跑**、两个"字段名没变而它数什么变了"的账本缺陷、§9 七臂批次与 §11 六组首次并表（2026-09-22）

- **改了什么**（P5-a…P5-e 累计。v0.2 一行未提交 ⇒ `git diff` 切不出"某个子阶段"，下面的行数与收集数是本轮 `wc -l` / `--collect-only` 实测）：
  1. 新文件 **3 个，全在文本通道**：`benchmark/planned_runtime.py`(182，把 v0.2 的决策页挂到 v0.1 的
     `AlfredRuntime` 上，不另起一个 runtime 类) / `benchmark/obligations.py`(377，文本 goal → 逐义务，
     `obligations_sha256()` 本轮算得 `e32d3eb161ee5ac4…`) / `benchmark/policy.py`(1079，零花费的规则
     读页者；"`look` 在这台引擎上不陈述任何 contents"是它 docstring 里的一条 finding 而不是猜测)。
  2. **接缝公开**：`perception/arm.py`(549) 的 `build_perceiver`(:447) 从 `build_arm` 里搬出来成公开函数，
     返回 `(Perceiver, GroundingMap)`；`arm_for_channel`(:497) 只在**没人声明臂**时按通道补臂名。
     `perception/grounding.py`(279) 的 `declared_views`(:267)、`arm_coherence`(:216)、
     `channel_readiness`(:245) 三条门控在这里；`acquisition/arm.py`(947) 的 `build_skill_arm` 三分支
     （privileged→计划臂 / 缺 `perceiver`|`gmap`→`ValueError` / 其余→`AcquiredPerceptRuntime`）；
     `episodic/arm.py`(394) 的拒跑文案点名去哪儿拿 perceiver。装配只加类：
     MRO 实测 `AcquiredPerceptRuntime → AcquisitionMixin → ExperiencedPerceptRuntime → EpisodicMixin →
     PlanningMixin → PerceptRuntime → Runtime`，每层仍只往页面上加一节。
  3. `evaluation/run.py`(1162)：相机分支一次装好 `(perceiver, gmap)` 再交给两个臂；共享门控
     `installed_module_refusals`(:205) 由私有改公开（一条规则被 CLI 与 runner 共用）；manifest 多批次级
     `episodic` / `skill_acquisition` 两块。**`cli.py`(1097) 删掉四条拒跑**（P1-e 立的"相机上不许召回 /
     不许习得 / 臂不能叠"，理由是"没有公开的 perceiver 构造口"）⇒ `--perceive stub --experience-store …
     --skill-memory …` 从**退出 3** 变成能跑。路径→对象仍只有一处（`cli.py:226` 的
     `ExperienceStore.load`），`--propose model` 仍在入口被拒（`cli.py:232-241`：asker 是 caller-supplied
     callable，这个入口什么都不花，而一次模型提议本身就是一个决定）。
  4. `core/runtime.py`(807) `_finalize`(:735)：`EpisodeResult` 声明的 `recovery_events` / `replan_events`
     两格改从臂自己的计数读（`getattr(self, "recovery_events", 0)` / `revision_events`，在 `episode_end`
     落盘**之前**读，事件日志与结果因此是同一本账）；`retry_events` **仍按声明留 0** —— 一次重试是
     `recovery_action` 记录的 `choice` 字段，不是它自己的事件类型。
  5. `planning/working_memory.py`(739)：白名单 `_OK`(:99) 把契约自己的成功值 `SkillStatus.completed.value`
     打头（原三条 `success/ok/succeeded` **循环从不发出**），并给"控制结局"而非"一步动作"的
     `finish_accepted` 单列一条豁免 `_NOT_A_MOVE`(:105)；`finish_rejected` **不**豁免。两处都挂在
     `_record_attempt`(:432/:434) 上。
  6. 判据：本轮实测各文件收集数 `test_bst_text_policy.py` **18** / `test_bst_text_obligations.py` **29** /
     `test_bst_text_backend.py` **31** / `test_v02_perception_arm.py` **42** / `test_v02_episodic_arm.py`
     **18** / `test_v02_acquisition_arm.py` **28** / `test_v02_planning_arm.py` **23** /
     `test_v02_working_memory.py` **20**。阶梯 1107(P4-e) → **1111**(P5-a) → **1161**(P5-e 全量)，净增 54 条。
     **这 54 条不逐子阶段分配**：唯一能事后区分"新文件 vs 就地补钉"的证据只有 mtime 与
     `git ls-files`（本轮实测 `tests/` 下 19 tracked / 35 untracked，v0.1 之后的东西两边都有），拿它当账目分是不诚实的。
- **对封存批次的影响**：全量 **`1161 passed, 21 skipped in 924.40s (0:15:24)`**（`/tmp/p5e_regression.log`，
  零 FAILED / 零 ERROR），同一份代码第二次跑 **1161 / 21 in 879.56s** —— **条数同、秒数不同**，这个差就是成本表
  墙钟列标 `†` 的原委，不是哪条测试不稳定。21 条 skip 逐条来自 `-rs`：18 ×
  `tests/integration/test_bst_alfworld_live.py`（桌面解释器无 `textworld`）+ 1 ×
  `tests/unit/test_bst_batch_control.py:17`（模块级 `pytest.importorskip`）+ 2 ×
  `tests/online/test_deepseek_online.py:272/:295`；顺带解释一个表面矛盾 —— `--collect-only` 报 **1181**
  而不是 1161+21=1182，少的那一条正是那条模块级 skip（collect 阶段不贡献项、run 阶段贡献 1 个 skipped）。
  **数目与 P4-e 相同 ⇒ 本轮没有新增 skip，也没有一条是代码坏掉。**
  不重冻结，本轮逐项实测：`V02_EVENT_TYPES` **15**、`ABLATION_CONDITIONS` **7**、`schema_fingerprint()` 与
  `configs/experiment/v02_schema_freeze.json` 的七个键**全等**（`all_equal True`；`schema_version 4` /
  `module_sha256 65f2b240…` / `records_sha256 112b2df8…` / `all_records_sha256 ac2f7356…` /
  `event_types_sha256 58f691b8…` / `ablation_registry_sha256 db486f63…` / `v01_schema_version 3`），
  `cli prereg --check` 仍 `23abe58ff333…aae7b3`、`cli em-pairs --check` 仍 `em set matches: 7df449ed2f4d`。
  BST v0.1 的 34 批 / 904 行 `runs/*/results.csv` 与 Phase 3-5 哈希链路本轮**没被读也没被写**
  （`git status --porcelain runs/` 只有三条既存未跟踪目录，无任何 tracked 修改）。P5 全部产物在仓库外
  （`/tmp/p5a_*` `/tmp/p5b_*` `/tmp/p5c` `/tmp/p5d`，普查探针在 `work/` 且对仓库只读）；模型侧零花费是**逐集量出**的
  （12 格 / 168 集 `http_requests / prompt_tokens / completion_tokens` 全 0、`cost_estimate_usd: null`）。
  **一条产物体积的账**：`/tmp/p5c` 实测 **1.8 GB / 2400 张 png**，因为十二格的命令形状里没带 `--no-frames`
  —— 那个开关一直存在（`cli.py:910/:932/:1011`），是驱动脚本没传它，不是入口缺它。
- **实测的新行为（不记就是隐瞒）**：
  1. **一个缺陷此前被当成了行为读——但它的射程要在这一条里划清**：`_OK` 不含循环真正发出的状态 ⇒
     判定"这步成功了所以不记失败行"永不成立。修前文本批 12 集 / **334** 条 `working_memory` 行里共 file 了
     **6,712** 行 `failed_attempts`（`pick_cool…__full` 最后一行 19、`look…__wo_planning` 27），修后
     **同一批同一形状**是 **0**（本轮从 `matrixC` / `matrixE` 的 `events.jsonl` 重算）；P5-a 第 5 节里
     "`failed_attempts` 随轮上数（1→2→3→4→5）"那句把这个**账本**读成了行为，已在 P5-b 第 4 节更正。
     **被污染的是账本与页面，不是那台仪器**（本轮读代码划的界）：`evaluation/long_horizon.py` 的 L5/L6
     从 `execution_feedback` 事件取数（`:462-465`，L5 自建 `attempts` 在 `:860`）并有 `_completed`(:395)
     自己那道防线，所以 §11 的 `repeated failures` 与 `recovery/replanning effectiveness` **从未**读过这本账
     —— 那句"两格在每个通道都读错"的旧断言有一个来源：生产代码的注释 `planning/working_memory.py:378-379`
     自称"§11 的两格都从这个字段读"，代码否证了它，本轮**记录而不修改**（处置同第 11 条那条过期指针）。
     账本的消费者只有两处，且两处本轮都量了：`episodic/arm.py:190` 取 `failure_code` 拼检索 query，
     而一次干净成功的行没有 code（被同一行的 `if str(f.failure_code or "")` 滤掉）⇒ 查询不受影响；
     另一处是 §11 `obligation_check` 行里的账本派生字段 `failures_never_answered`
     （`working_memory.py:671` 按 `followed_by == "nothing"` 过滤、`:704` 落盘、`long_horizon.py:995` 抄入）
     —— 修前批与修后批各 12 条 `obligation_check` 记录，两侧该字段合计**都是 0** ⇒ 它在数据路径上，
     但没有改动任何已发布数字。
     **但中性只在测过的地方成立**：文本批没装记忆臂、用的是零花费规则读页者，所以"页面变了而行为没变"
     是**那一批**的测量；一个带 `--policy memory` 的修前批次不存在，故这种批上这一页的变化**没有**
     前/后对照可引。
  2. **行为中性是被量出来的，不是声称**：`matrixC`（修前）与 `matrixE`（修后）的 12 个 run 行，在**递归**剥掉
     派生 id（`decision_id / wm_id / observation_ref / created_at / wall_time …`）与墙钟之后
     **0 行不同**（本轮程序核对：`rows differing after recursive id+wall strip: 0`）；`event_kinds`、
     `subgoal_status` 轨迹、`official_won` 逐行相同；三条哈希随之不变
     （`view_sha256 6b18efbd79eb…` 直接从 `matrixE` 的事件里读回、`obligations_sha256 e32d3eb1…` 本轮算得、
     `policy_sha256 264e188b…` 在 12/12 行上）。
  3. **而页面文本在 12/12 集上都变了**：`lines_dropped` 合计 **11,341 → 4,819**（−57.5%）、
     渲染字符合计 **272,010 → 259,217**（−4.70%）、`wm_rows` 334 → 334。⇒ 那些 phantom 行**在占页预算**：
     它们被 file 进记录、挤掉别的行，却因超预算而被丢弃。**第三次"内容变而 `view_sha256` 不变"**
     （它哈希的是四个渲染对象的源码文本，本处改的是 `_record_attempt` 与模块常量）—— 要证"看到的东西变了"
     只能看 `page_chars` / `lines_dropped`，哈希两头都证明不了。
  4. **`EpisodeResult` 两个字段同名换义**：修前恒 0（一集报了 5 条 `recovery_action` 也报 0），修后是臂的计数；
     `full` 一集 5 条恢复 + 1 条修订与 `episode_end` 同数，`wo_replanning` 的 0/1 是合法读数。
     **已发布产物不回头改** ⇒ 新旧产物放在一起比较时，这一列的差是**账本定义**的差，不是行为的差。
  5. **一条 exit 3 变成能跑，四类拒跑留在原处**：入口仍一次给出全部理由（`_perception_kwargs` 返回 list 而非
     异常）——① `arm_coherence`：`--perceive stub --ablation full` 被拒（`stub` 从不问视觉模型，而 `full`
     声称 vlm 开着）；② `channel_readiness`：`--perceive vlm` 而无 adapter 被拒，文案写明"退回确定性 stub
     会把同一集报成 `full`，这正是这道门存在的理由"；③④ 门在没装的模块上（`wo_episodic_memory` 缺 store /
     `wo_skill_acquisition` 缺 library）。**privileged 分支上 `arm_coherence` 故意不参与**（`cli.py:313-317`
     把理由写在注释里：两条规则都是关于 `perception` 记录的，套在这里会把这个开关要的每一臂都拒掉），
     而 `--perceive privileged --ablation wo_vlm` 是**接受后丢弃** —— 那本来就是封存的 v0.1 基线，
     透传只会把基线改名。读 P1-P4 日志里那几个退出码的读者会落空，所以这条要记。
  6. **产物里 `views: []` 不是"这一臂只有一台相机"**：`declared_views(())` 与 `declared_views(None)` 都返回
     **全部三台**（`grounding.py:267`），而 `run.py:833` 往 manifest 写的是 `list(views or ())` ⇒ 同一个未设置
     的开关有两种拼法。P5-c 把 manifest 的 `[]` 读成"单视角"并**当场更正**（two_views 负控制），钉住的判据是
     行为而不是拼法：未限制的臂可指向三台中任一台、被限制的臂不可（`test_v02_perception_arm.py:300-312`）。
  7. **相机 × 记忆 × 习得第一次挂在同一集**：15 类事件里 **13 类在盘**（`working_memory 94`、`memory_use 87`、
     `skill_* 各 12`、`plan_revision 2`、`observation 298`），缺的两类各有名字 —— `perception` 为 0 **正是**
     `wo_vlm` 的要求（coherence 保证），`recovery_action` 为 0 是没触发。outcome **2/16** 对 privileged 同形状
     **16/16**，`S1.gap_episodes 12/16` 对 `0/16` ⇒ **习得的扳机是失败压力，不是模块在不在**；
     两批 `http_requests` 合计 0。
  8. **"模型到底决定过没有"第一次成为一个可重跑的普查对象**：带 v0.2 臂记录的 **753** 个 episode 里，
     同目录 `model_calls.jsonl` 非空的是 **0** 个；`perception` 记录在 **3,552** 个 episode 日志里出现 **0** 次，
     其产生条件恰是 ⑤ 里那两道门共同拒绝的那个条件。口径（`events.jsonl` 有 `ablation` 记录 ⇔ 挂臂）
     **必须随数字一起写下**：本节初稿曾发布过一个没留口径的"991"，重测得 753/0。**而"有口径"还不等于
     "可重跑"** —— 交付后按同一条 glob（`/tmp/**/episodes/*/events.jsonl`）再跑一次得 **3,278 / 739 / 0 / 0**
     （日志 / 带臂 / 带臂且问过模型 / `perception` 记录），总数少 274 个日志、14 个带臂集，因为这条公式扫的
     `/tmp` 不是封存目录。⇒ 判定重下在**不变量**上：三种枚举（`episodes/*` 深度、任意 `events.jsonl`、
     再加 `runs`+`work`+`outputs` 共 6,681 个文件 / 948 个带臂）在"带臂 ⇒ 问过模型"上都给 **0**。
     上面那两个总数保留为**当时的快照**（报告 §0 就地标注，§11.3 第 14 条记录这条规矩）。
  9. **外部环境的成功名与自定义任务集的成功名从此是两条列**：文本通道 `full 6/6` → `wo_planning 2/6`，代价是
     墙钟 28.76 → 43.99 s 而花费 0；333 条命令的 `status` 只有 `completed` 一个值。ALFWorld 的
     `official_won / reward / env_done` 与 §8 自定义集的部分完成度**分列报**（§12.2）—— 而且在文本通道上
     它们**不是两个独立测量**：`benchmark/runtime_alfred.py:187 _episode_success` 就读 `official_won`
     （SPEC-BST 4.6 之下谓词行只能是 `unknown`，再"与"一次会得到一个假的失败率），三处一致**是定义如此**；
     谓词式 `objects_completed / objects_total` 仍**只**属于 lh/em 那两条列。
  10. **开局句不可从产物重放**：`episode_start` 之后的 `observation` 从**第一条回话**开始，唯一列出这局房间的
      是游戏第一句 ⇒ 只看产物的重放必然从错页开始，把每一行报成 unactuated 然后交 `blocked`。修法是 fixture
      显式带上 opening，并加一条"每局的开局句必须陈述它自己的任务"的钉子
      （`test_bst_text_policy.py:389`）——粘贴错的开局页仍是一页合法文本、一个错房间。
  11. **一处过期指针，本轮量出来并留在这里**：`planning/working_memory.py:97` 与阶段日志都写
      `evaluation/long_horizon.py:388 _completed`，本轮实测 `def _completed` 在 **:395**（P5-d 给那台仪器补
      `missing_because` 解释段之后漂了 7 行）。指针漂了、事实没错；**不改生产代码** —— 1161 这条回归线描述的
      就是它存在的那份代码，为一条注释重跑不划算，为重跑改代码更不划算。
  12. **十二格矩阵与它的读数以"不动"为主**：12/12 格 `git_commit 4e960a2d…` + `dirty_diff_sha256 b43d31f369f688d3`
      逐格相同、全部 `rc=0`；LH-2 产物本轮重算得 80 行对照 ⇒ 每臂 16 指标、6 臂 96 读数，其中
      **`value != reference` 只有 1 行**（`wo_replanning·L6.recorded_vs_inferred` 1.0 → 0.0，含义只是"这条信息
      还在不在页面上"）、`comparable: false` **8** 行、asymmetric **10** 行。⇒ "关掉模块会让能力掉"这句话
      在这个任务集上今天**没有对应的数**；唯一有臂差的行为读数在文本通道（6/6 → 2/6）。
- **为什么要记一条**：P5 是第一个改动主要**不是新模块**的阶段 —— 删四条拒跑、修两个"字段名没变而它数什么变了"
  的账本状态、把整套闭环搬到一个不是自己写的环境上。它给并表带来六类新形状：①同一个字段跨新旧产物**换义**
  （`recovery_events`），比新增字段更危险；②"not measured" 多了一种成因 —— **仪器读自己的 manifest 读错**；
  ③外部 benchmark 的成功与自定义集的部分完成度必须**分列**，且在文本通道上那个"成功"就是引擎的 `done`
  （三处一致是定义，不是两个独立测量互相印证）；
  ④哈希不变而内容变（第三次，方向与 P3 相反、与 P4 同类）；⑤"模型问过没有"成为口径的一部分，且**口径没写下就
  不可重跑**（991 → 753 的差正是这个）；⑥产物体积是**命令形状**的性质，不是仪器的性质。而写侧契约
  （15 个事件类型、`ABLATION_CONDITIONS` 7 条、`SkillName`）、47 用例集合、评分规则、消融注册表、封存批次哈希
  与 prereg / em 两份 `--check` 哈希**一处未动**。关闭 §15 第一半所需的 ≈1,656 次计费调用与"先报预算再花"
  这条规矩都不在这里，在 `docs/continuous-decision-v0.2-final-report.md` §12 等一个决定。



## D51 —— 附录 H（相机工程 H-1…H-22）落在契约面上的改动：manifest 的 `code` 多一个键、一条测试门换判据、`skill_calls_executed` 数到一步没走的那一次（2026-09-24）

- **范围先说清**：D50 之后到 H-22 为止，改在**被 git 跟踪的 v0.1 契约文件**上的只有一处 —— `core/events.py`。
  其余全在未跟踪的 `embodied_agent/benchmark_mujoco/`（适配层、`perceive.py`）、`perception/` 包与 `tests/` 里，
  本轮实测这些目录下 **76 个 `.py` 未跟踪**（`git ls-files --others --exclude-standard --full-name`，前缀
  `embodied_agent/`）。**这条范围本身就是本条 delta 的理由**：跨版本读产物的人会以为"看不见 = 没改"，而
  附录 H 的全部修复都发生在看不见的那一侧。

- **改了什么**（四条，每条给当场可复现的读数）：
  1. **`core/events.py`：`git_state()` 的返回值多一个键 `untracked_code`**（新函数
     `untracked_code_state()`、常量 `UNTRACKED_CODE_PREFIXES = ("embodied_agent/",)`、`_git()` 帮助函数）。
     ⇒ 从此每一份 `manifest.json` 的 `code` 块是 **8 键而不是 7 键**；`_provenance()` 里也是同一支函数，
     所以重跑 `dump_prereg` 的 `provenance.code` 同样会多这一格 —— 但 `rules_sha256` 只哈希 `rules`
     （`preregistration.py:459,468`，文件头第 14 行写着"hash the rules and nothing else"），
     **两份 `--check` 哈希一处未动**。本仓当前读数：`files = 76`、`sha256 = 868569a6794dc564`、
     `commit = 4e960a2d2d18`、`changed_files` 169 项；一次 `git_state()` **0.145 s**（函数本身），
     端到端含 import **0.443 s**。
  2. **`dirty_diff_sha256` 一字未动，但它的语义现在有了已量的边界**：它是 `git diff --binary` 的哈希 ⇒
     对未跟踪文件恒为不变。D50 里那 12 格矩阵靠它论证"同一份代码"，本条给那句话补上它当时不知道的洞：
     相机批次 batch5 / 5b / 6 的 **15 份 manifest 全部 `commit 4e960a2d2d18` + `dirty_diff_sha256 a821c9769c1664e4`
     + `changed_files` 同一 169 项**（`glob('**/manifest.json')` 实测；batch6 的 9 份含 1 份 429 partial），
     横跨 8 h 43 min，而 Fix B 的编辑正夹在 5b 与 6 之间。**新字段不追认历史**：并列这两批时必须写
     "身份字段相同，且该字段对未跟踪代码是盲的（H-22）"。
  3. **`tests/contract/test_mujoco_channel.py`：一条门的判据从"这一跑有没有报错"换成"仪器自报的健康"**，
     净效果是**放宽**（`frame_surface_px` 低于地板而 `run_error is None` 的进程，过去挂、现在跳），
     阈值 `SURVEY_FRAME_SURFACE_FLOOR = 50000` 未动。回归数（选择集都是 `tests/contract tests/unit`）：
     改后跑 A `1159 passed, 1 skipped in 543.72s`，跑 B `1 failed, 1159 passed, 2 skipped in 673.32s`。
     **一条新的跨版本陷阱，量出来只为不让它被引用错**：今天 `-m pytest tests/contract tests/unit
     --collect-only` = **1161 项**，与 D50 §10.3 那句"**`1161 passed`**"（选择集是 `tests/`，今天收
     **1257 项**：unit 320 + contract 841 + integration 89 + `tests/online` 4 + `tests/test_mvp.py` 3）
     **数字相同、选择集不同**，纯属撞号。A/B 两跑的账本轮按收集数闭合：A 收集 1159 项 ⇒
     1159 通过 + 1 条模块级 skip 的 **outcome** = 1160；B 收集 1161 项（`test_event_log_safety.py`
     本轮 16 → 18 条）⇒ 1 failed + 1159 passed + 1 项内 skip + 1 模块级 outcome = 1162。
     "outcome 数 = 收集数 + 1" 这个偏移 D50 已记过，本轮第一次用它把两跑对平。
  4. **相机产物多了三格、其中两格是"字段名没变而它数什么变了"的新实例**：`episode_summary.json` 的
     `perception.motions`（`runner.py:173-177`，随 `axis_fits` / `hold_claims` 一起）、`perception.survey.frame_surface_px`、
     以及 **`skill_calls_executed`（= `runner.py:161` 读的 `executor.calls`）现在会把一步没走的 motion 计进去** ——
     `executor.py:333-334` 除了 `"horizon"` 一律记 `completed`，实测已计费 batch6 的 30 条 motion 里
     **2 条是 0 步"完成"**（两集 `official_success` 仍 True、成功不来自它们）。这条与 D50 的 `recovery_events`
     换义同族：**读旧产物时"字段在、数没变"不代表数的是同一件事**。
     **【D53 的勘误，2026-09-24】** 这一格里 **数字对、口径标签错**：那 **30** 条 motion 不是 batch6 的，
     而是带 `perception.motions` 字段的**两个已计费批次之和**（batch5b 12 + batch6 18 = 30，13 个集目录）；
     **2 条 0 步"完成"** 确实都在 batch6（`/tmp/mw102/reconcile107.py` 一次只读命令重测，两处的
     `official_success` 也都仍为 True）。这半个错标签的代价是**读数被当成了范围**：H-23 之后我按
     "batch6 30 条"去找 30 条，`glob` 回来的是 13 个目录而不是 8 个，才对出这个口径差。

- **为什么要记一条**：这一批改动没有新增事件类型、没有动 `SkillName` / `ABLATION_CONDITIONS` / `Budgets` /
  47 用例集合 / 评分规则 / v0.1 基线读数，唯一进入 v0.1 契约文件的是**一个加法字段**。它危险的地方恰好在
  "加法"：`code.untracked_code` 在旧产物里**不存在**，`manifest` 的比较脚本若用 `dict.keys()` 判断版本会把它
  读成"另一种树"；而它给出的答案又依赖一条**申报式作用域**（只覆盖 `embodied_agent/`、只算 `.py`、不含
  `.gitignore` 挡掉的），所以"`untracked_code` 相同"永远不等于"跑的代码相同"，只等于"申报范围内相同"。
  同一条规矩也解释了为什么它值得存在：本节的读数被时间推翻过一次 —— 落盘那句写的是
  `961ee93facd8f69c`，一小时内同轮改 `cli.py` 之后同一命令给 `868569a6794dc564`；**字段没错，是数字要连
  时间一起引**。

## D52 —— v0.1 契约文件第二次被加法改动：`core/runtime.py` 多一个公开方法 `settle_usage`、`model_calls.jsonl` 每行自带 `ok`（于是"行"不再是"轮"的同义词）、survey 的账外支出从暗示改成写明（2026-09-24）

- **范围**：D51 之后到 H-23，改在**被 git 跟踪的 v0.1 契约文件**上的仍然只有一个目录、这次换了一个文件 ——
  `core/runtime.py`。其余四处（`benchmark_mujoco/runner.py`、`model_policy.py`、`cli.py`、
  `tests/contract/test_mujoco_channel.py`）实测**全部未跟踪**（`git ls-files --error-unmatch` 逐个问，
  四个都返回"不匹配"；同一命令下 `core/runtime.py` 与 `core/events.py` 是 TRACKED）。
  **一句话口径**：`git diff --numstat -- embodied_agent/core/runtime.py` 现在是
  **206 added / 30 deleted**，那个数是**整个 v0.2 工程相对 `4e960a2d2d18` 的累计**，不是本轮的 ——
  本轮加的是其中连续的 **11 行**（`runtime.py:647-657`）。引用这条 delta 的人不要把 206 当成本轮的账。

- **改了什么**（四条，每条给当场可复现的读数）：
  1. **`core/runtime.py:647-657` 新增公开方法 `settle_usage(source)`**：`_usage_base` 存在时调一次
     `_accumulate_usage`，返回 `dict(self.usage)`。既有语句**一处未改** —— 这条不是印象，是
     `git diff -U0 -- embodied_agent/core/runtime.py` 里含它的那个 hunk 的头：
     **`@@ -505,0 +647,12 @@`**（旧文件侧计数 0 ⇒ 该 hunk 删 0 行、加 12 行）。
     `SOURCE_COUNTERS` 未动、`_finalize` 未动、状态语义未动。
     ⇒ **跨版本读数真正会变的那一格**：一集死于 `observe()` 内的请求时，
     `episode_summary.json` 的 `model_usage.api_errors` / `.transport_retries` 从 `0 / 0` 变成 `1 / 2`。
     字段名没变、类型没变、键集没变，**数的是同一件事，只是以前漏计了致命那一次**。归档实测：51 集里
     9 集终于致命 `LLMError`，其中 **5 集记 `api_errors: 0`、4 集记 1**（`/tmp/mw102/audit.py` 的口径 +
     本轮重跑的分布 `{1: 4, 0: 5}`）。所以"相机批次的 429 次数"这种跨批次统计**必须以 D52 为界切开**，
     不能把 5 个 0 读成"那 5 集没花请求"。因果线是**反证实验**给的，不是推理给的：把
     `Runtime.settle_usage` 换成 no-op、同一 seed/task 再跑一次，`api_errors 0 → 1`、
     `transport_retries 0 → 2`（`/tmp/mw102/counterfactual.py`）。
  2. **`benchmark_mujoco/model_policy.py`：请求抛出来也先写一行，再 `raise` 原样抛出**。
     每行现在**自带** `ok`（成功路径 `row.setdefault("ok", True)`），失败行另带 `error`、
     `requests_made` 和五个 `*_this_call` 差值。⇒ 对读产物的人，`model_calls.jsonl` 的语义从
     **"一行 = 一轮决策"** 收紧为 **"一行 = 一次被问的决策请求（答没答上都算）"**。
     **旧产物一律没有这个键**：本轮实测归档相机批次 **138 行里带 `"ok"` 的是 0 行**，且 138 行的
     `finish_reason` 全是 `stop`（`Counter` 逐行数）。所以任何"没有 `ok` ⇒ 失败"的读法在 D52 之前的
     产物上是错的 —— 那句话现在由 `ok: false` 明写，不再靠缺键推断。
     既有断言 `test_mujoco_channel.py:740` 的 `[1, 3, 1]` 仍然成立（那是成功路径的每轮一行），
     新增的一条断言 `len(rows) == 被问次数` 钉的是新的这一半。**没有**新增重试、修补或兜底决策（§10）。
  3. **`benchmark_mujoco/cli.py` 花钱前的那段申报文案重写**：原文暗示 `model_usage` 是三个文件的合计，
     实际它只是"臂装配完成之后"的适配器计数器 —— **survey 的 1 个请求不在里面**（它发生在
     `_usage_base` 之前；H-23 里 #108 记的量：batch6 归档 `glob('/tmp/mw_vlm_camera_batch6*/episodes/*/
     episode_summary.json')` 实测 **8 集，每集 `perception.survey.tokens.prompt_tokens` 都是 558**，
     而同集 `model_usage.prompt_tokens` 是 26670 / 12848 / 20774 / 17705 / 19377 / 16682 / 38008 / 17756 ——
     八个数里没有一个含它那 558）。**这一条没改任何数，改的是读者以为数是什么**。
  4. **`tests/contract/test_mujoco_channel.py` 未跟踪，+2 条契约测试**：
     `test_a_fatal_decision_request_is_a_row_and_a_bill`、
     `test_a_look_that_dies_still_bills_the_episode_it_was_asked_for`。两条都是**零 HTTP**（脚本化端点），
     后一条的最后一条断言钉的是**没做的那一半** —— 那集的 `model_calls.jsonl` 是空的（#109）。

- **为什么要记一条**：本轮没有新增事件类型、没有动 `SkillName` / `ABLATION_CONDITIONS` / `Budgets` /
  47 用例集合 / 评分规则，进入 v0.1 契约文件的还是一个**加法方法**。危险的地方仍然在"加法"两个字上，
  但方向与 D51 相反：D51 的 `code.untracked_code` 是**产物多一格**，旧产物里不存在；D52 是
  **产物一格不少、值却变了**，而变的那一格正是"这次花钱失败了没有"。一个用
  `model_usage.api_errors == 0` 做筛选的脚本，在 D52 之前会把 5 集真死在 429 上的相机集读成
  "干净地结束了"，在之后不会 —— 两句都对，因为它们各自对的是自己那一版的代码。这正是 D50/D51 记过两次的
  "字段名没变而它数什么变了"的第三个实例，也是它第一次出现在**被跟踪的文件**上。

- **回归**：目标集（相机通道 + 相机感知 + `test_run_artifacts` + `test_event_log_safety` + `vlm_contrast`）**139 passed in 177.08 s**。全量 `pytest tests -q -p no:randomly -rs` 同码两跑：第一跑 **1 failed, 1238 passed, 21 skipped in 612.74 s**，第二跑 **1239 passed, 21 skipped in 604.33 s**。红的那一条与账**无关**（它是 H-19 记过的那条相机间歇，本轮量到它挂时 `frame_surface_px=109232` 在地板之上 ⇒ 已记 #110），但**这一红一绿本身就是本条 delta 的一部分**：跨版本比测试计数的人必须知道，这个组成下这条相机测试是 1/2 而不是 2/2，任何把它当回归的读数都会指错代码。详见阶段日志 H-23 末的补记。

## D53 —— #107：`skill_calls_executed` 在同一条 v0.2 线上第二次换义（这次是反方向）、相机通道的 `execution_feedback` 第一次出现 `rejected`、`STATE_UNCERTAIN` 的计数会因此下降（2026-09-24）

- **范围**：本轮被改的 4 个文件 —— `embodied_agent/benchmark_mujoco/executor.py`、
  `embodied_agent/benchmark_mujoco/runner.py`（只改注释里的一个数）、
  `tests/contract/test_mujoco_perception.py`、`tests/contract/test_mujoco_channel.py` ——
  `git ls-files --error-unmatch` 逐个问，**四个全部未跟踪**；被跟踪的 v0.1 契约文件本轮**一处未动**，
  证据是被跟踪那一个（`embodied_agent/core/runtime.py`）的 `git diff --numstat` 仍是 D52 记下的
  **206 / 30**，一字未增。⇒ **本条 delta 完全落在"git 看不见的那一侧"（H-22/#101 那族）**，
  也正因为如此它必须写下来：读产物的人看到的字段名一个都没变，而有三格数的东西变了。

- **① `skill_calls_executed` 的第二次换义，方向与 D51 相反。** D51 第 4 格记的是"这个字段**开始**把一步
  没走的 motion 计进去"；本轮把那句话变成过去式 —— `executor.py:355` 现在对被拒的 motion **不**
  `calls += 1`。同一字段名在同一条线上换义两次，所以任何跨 batch5b 与未来批次的比较都必须写明哪一侧：
  **旧归档的数是"含白计"的数**。重放到归档（`/tmp/mw102/reconcile107.py`，只读）：
  层 1 口径（`perception.motions` 存在的 13 个目录）**30 → 28**；层 2 口径（有 `events.jsonl` 的
  35 个目录，`sum(skill_calls_executed)`）**98 → 94**。两数之差不是矛盾而是覆盖差：
  `perception.motions` 字段是 H-9/3e 才加的，batch4 那两集根本没有层 1 流水。
  **要问"多少条 motion 白计了"只能问层 2。**

- **② `execution_feedback.status` 在相机通道上第一次出现 `rejected`，而 `uncertain` 会少 4。**
  归档那 4 条 0 步 `target_lookup` 的 `pick`，公开状态从 `uncertain`/`STATE_UNCERTAIN` 变成
  `rejected`/`AIM_UNMEASURED`；伴随两件事，都是**语义正确但读数会跳**的：
  `executed` 从 `true` 变 `false`（`core/runtime.py:683` 只认 `rejected`），
  `rejection_reasons` 从 `[]` 变成那句"peg 1 has no position in snapshot …"（`:703` 同一道门）；
  以及 `verification.reports` 从"1 条 `held:peg 1 = unknown`"变成 **`None`** —— 因为
  `runtime.py:198` 的 `verify_grasp` 只对 `status == completed` 的 pick 开门。
  **⇒ 任何按 `STATE_UNCERTAIN` 计数的工作（#103 就是那条）在新产物上会看到计数下降，
  下降的原因是那四条本来不是"测不准"而是"没做"**，不是世界变好了。这条如果没人写，一定会被读错。

- **③ 新 failure_code 字符串 `AIM_UNMEASURED`，且它不在 `FailureCode` 枚举里。** 相机通道此前用过的
  `ADAPTER_ERROR` / `ENVIRONMENT_ERROR` 都是枚举成员，这是第一个纯字符串 ⇒ 按字符串读，别按枚举校验。
  它同时是**层 1 与层 2 之间第一次出现"新码盖掉旧码"**：`_outcome_status`
  （`runtime.py:221`）只在 `result.failure_code` 为空时才补 `STATE_UNCERTAIN`，所以层 1 给了码之后
  层 2 那个码就不会出现 —— 这是上面那条计数下降的机制来源，不是额外规则。

- **④ 一条既有测试的判据被换，方向是"收紧"。** `test_mujoco_channel.py` 里
  `len(motions) == s["skill_calls_executed"]` 改成
  `len(motions) == skill_calls_executed + 被拒 motion 数`。旧断言把"记进流水"与"计进执行数"当成同一件事，
  而 #107 要分的正是这两件事：**流水一条不少，计费少一条**。同文件新增端到端一条
  （`test_a_motion_that_refused_its_own_aim_is_a_refusal_on_the_model_page`），
  `test_mujoco_perception.py` 新增 3 条参数化（层 1 的三种拒绝形状）。

- **边界：什么没动**（这几条是跨版本读者最会误推的）：`SkillStatus` 枚举没加成员（`rejected` 是 v0.1
  就有的，`contracts.py:493-499`）；15 个事件类型没加；`SOURCE_COUNTERS` 没动；`env_steps` /
  `official_success` / `decisions` / `model_usage` / `budget` 各格读数一字未变（反证里两跑
  `steps [0.0, 116.0]`、`official_success false`、`termination_reason AGENT_FINISH` 完全相同）；
  Runtime 仍然不替模型选动作（§10）—— 动作序列修理前后逐条相同，变的只有报告。

- **已知未量的那一半（#111，写在这里以免被当成已修）**：`benchmark_mujoco/runtime.py:171`
  `_map_failure` 把任何 `rejected` 映射成 `FailureCode.ADAPTER_ERROR`，而
  `_termination_of:252-253` 会把它写成终止理由。本轮新增的 `rejected` 因此**可能**让"以被拒 motion
  收尾直到轮次耗尽"的那一集读成 `ADAPTER_ERROR`（把一次电机拒绝说成基础设施故障 —— 恰是 SPEC-BST 6.3
  要分开的两类）。**这一形状本轮没跑出来**：反证里 agent 自己 `finish`，`last_failure` 没走到
  `_finalize`，两侧终止理由都是 `AGENT_FINISH`；而重复同一被拒动作会先被
  `_repeated_without_new_evidence` 判成 `REPEAT_GUARD`。所以它是**新缺口，不是本条 delta 的读数**。

- **回归**：目标集（相机通道 + 相机感知 + `test_run_artifacts` + `test_event_log_safety` +
  `vlm_contrast`）**143 passed, 1 warning in 175.01 s**（H-23 同集 139，差的 4 条即本轮新增）；
  全量 `pytest tests -q -p no:randomly` **1243 passed, 21 skipped in 662.64 s**，输出里 0 个 `F`；
  `--collect-only` 1263 项 ⇒ outcome 1243+21=1264=收集+1，即 D51 第 3 格记过的那个偏移，本轮再用它对账。
  另：D51 第 4 格里"batch6 的 30 条 motion"已就地加【D53 的勘误】—— 数字对、口径标签错（30 是
  batch5b+batch6 之和）。

## D54 —— #103：相机通道上 `unmeasured[*]` 与 `description` 的**措辞**换了，判定一字未动；跨 batch 做字符串对账的人会看到旧归档里没有的三句话（2026-09-24）

- **范围**：本轮被改的 2 个文件 —— `embodied_agent/benchmark_mujoco/state.py`、
  `tests/contract/test_mujoco_perception.py` —— `git ls-files --error-unmatch` 逐个问，**两个全部未跟踪**；
  被跟踪的 v0.1 契约文件一处未动：`git diff --numstat` 读到 `core/runtime.py` **206/30**（与 D52/D53 一致）、
  `core/contracts.py` 130/5（本轮量）。HEAD 本轮为 `4e960a2`。⇒ 与 D53 同族：**这条 delta 完全落在 git 看不见的那一侧**，
  `git diff` 为空不等于什么都没改（H-22/#101）。

- **① `placed:` 行的 `unmeasured[0]` 从"一句话盖三种缺失"变成三句各自的话。** 旧：无论
  区域缺失 / 身体缺失 / **有身体但没量到米**，都是 `"{eid} or {tid} is not in this snapshot"`；
  新：`"{tid} is not a region in this snapshot"` / `"{eid} is not a body in this snapshot"` /
  `"{eid} and {tid} are both in this snapshot; what is missing is a position: … no distance to {tid} to compare"`。
  **旧串在相机通道上不是"少了"，是"从来没有过对应的事实"**：9 个 `official_success` 集的 59 眼里
  blob 在场 **59/59**、`socket 1` 被声明 **59/59** ⇒ 归档里那句"名字不在快照里"
  （`probe103c.out` 逐字读过：`peg 1 or socket 1 is not in this snapshot`）在**每一次**成功插入上都为假。
  【D55 同轮的更正】这一格原先还写着"第三分支成立是因为 detection 带 pose 的 0/59" —— 那句作废：
  `detections[].pose` 这条通道的代码从不消费（它 412/412 为 null），换成正确的口径后第三分支的成立由
  `/tmp/mw102/pos112.out` 给出：**两扇门**（`position_xyz_m` 与 accepted 轴拟合）**皆空的 34/64 眼**，
  其中 10/10 是 place 之后那一眼。结论（旧句子为假）一字未动，撑它的读数换了。
  **⇒ 任何用 `unmeasured` 文本做归因/聚类的跨版本分析都必须按 batch 分侧：batch4/5/5b/6 是旧句子。**
  另一句"看着像被这条改动波及、其实没有"：`state.py:432` 的 pick 分支仍写
  `"the object is not in this snapshot"` —— 那里 `e is None`，是真缺失，保留。

- **② 两种"读起来像判定"的 description 被换掉，`evidence` 也跟着不再凭空给数。**
  `verify_goals` 与 `verify_action` 的 place 出口在 `d is None` 时原来分别写
  `"peg 1 is seated in socket 1: the measured point … to within 0.020 m"` 与裸片段 `"peg 1 seated in socket 1"`，
  并排着 `value: unknown`；现在两个出口同一句
  `"whether peg 1 is seated in socket 1 was not tested here: the test is that … and this snapshot has no distance to compare"`，
  且只有真量到米时才追加 `(measured {d:.4f} m)`、`evidence` 里才出现 `distance_m`
  （未测那一支 `evidence == {}`，测试已钉）。旧句子在真 artifact 上的形态是被**重打旧句 + 只走线上报告路径**
  量出来的（`/tmp/mw102/seated103.out`：`tol = 0.02` 由仪器给出，`verdicts identical: True`）。
  **⇒ 任何按 `description` 前缀匹配"seated"的判断都会在新产物上改道**；`predicate_id` 一字未变
  （仍是共享的 `placed:<eid>:<tid>`，D44/#92 那条命名收敛不受影响）。

- **③ 判定、枚举、事件、计数：0 处变化。** 三处出口的 verdict 全部沿用 `d is None → unknown`，
  反证里同一快照两版句子的 `value` 完全相同；`STATE_UNCERTAIN` 的计数不因本条改变（D53 第 ② 格里
  "新产物上会下降"那件事是 #107 的账，不是这条）。`SkillStatus` / `FailureCode` 成员没加、15 个事件类型没加、
  `Budgets` 没动、`core/v02.py` 没动、`core/contracts.py:SkillName` 没动。
  **模板级提示哈希也不变**（`GOAL_SYSTEM` / `DECISION_SYSTEM` / `PERCEPTION_SYSTEM` 等一字未动）——
  本轮改的是**被逐字渲染进页面的那句 reason**（`prompts.py:95`），所以按 rendered-prompt 对账的地方会看到差异，
  这不是模板变更。Runtime 仍然不替模型选动作（§10）：动作序列、可选动作集合、`would_resolve` 的建议集合
  （`perceive.py:2160`，本就答 `re_observe:<alt_view>`）都一字未动，变的只有"为什么不知道"怎么说。

- **回归**：全量 `pytest tests -q --no-header -rf` 三跑，读数同一 —— **1246 passed, 21 skipped**，
  分别耗时 616.21 s（`/tmp/mw102/full103b.out`，"第二个出口"之前）、661.84 s（`full103c.out`）、
  **747.60 s（`full103d.out`，出货字节，输出里 `FAILED` 行 0 个）**；上一轮基线
  **1243 passed, 21 skipped in 662.64 s**（D53）⇒ **+3 条测试、0 失败、跳过数一字未动**
  （三跑墙钟差是负载差，不是代码差）。
  三条新契约测试在 `tests/contract/test_mujoco_perception.py:1395 / :1428 / :1460`。
  **目标集这一次不能按"文件对"报**：同一对文件正序 3/3 次让
  `test_mujoco_channel.py::test_a_completed_motion_files_the_aim_it_was_given_where_the_feedback_record_cannot`
  失败（`['rejected','rejected']` vs `['completed','completed']`、`failure_code='AIM_UNMEASURED'`、
  `stages=['target_lookup']`，而同进程 `frame_surface_px = 109232` 远高于地板），反序 **80 passed**、
  单文件各自全绿、把本轮 3 条新测试 `--deselect` 掉仍失败 ⇒ **与本条 delta 无关的顺序依赖**，
  归 #110（其描述已按这个读数改写）。读回归数的人请注意口径：**"目标集"在本项目里不等于"随便两个相机文件并跑"**。

- **已知未量 / 未被本条覆盖的（写下来以免被当成已修）**：
  ① ~~**新缺口 #112**~~ —— **下一轮撤销**（见 D55 上面那格更正）。那 18 个 `false` 轮次从来没有与自己的
  快照矛盾；矛盾在我那个"payload 没有 pose"的字段读法（`detections[].pose` 无人消费）。35 轮对质结果：
  20 个 `false` 全部落在给得出米的看上、15 个 `unknown` 全部落在给不出的看上、自相矛盾的轮次 0。
  ② 模型读了新 reason 之后会不会真的换视角再看一眼：**未量**，需要一次计费批次；本轮全部读数零 HTTP。
  ③ `distance_m` 为什么拿不到米（support-surface 那道门）一字未动 —— 本条改的是**说什么**，不是**能不能测**。


## D55 —— #113：`events.jsonl` 里 `"tokens"` 那个座位从一律 `***redacted***` 变成"映射/数字留下、字符串仍抹"；这是本轮唯一落在 **git 看得见** 的那一侧的改动（2026-09-24）

- **范围**：改的是被跟踪的 v0.1 事件层 `embodied_agent/core/events.py`（`_is_secret` 加一个参数 +
  它的两个调用点），测试落在同样被跟踪的 `tests/unit/test_event_log_safety.py`。
  `git diff --numstat` 读到 `core/events.py` **122/17**、`test_event_log_safety.py` **100/0**（含更早轮次
  对同一文件的累计改动 —— 本轮不假装能从这两个数字里剥出自己那一层），HEAD `4e960a2`。
  **对照 D52/D53/D54**：那三条完全活在 git 的盲侧（相机通道的实现与测试全未跟踪，`git diff` 为空），
  这一条形态相反 —— 它在 diff 里看得见，但**看得见的那一半不解释为什么产物变了**，
  要解释得看 `events.jsonl` 本身。

- **① 产物上会看到什么。** 感知事件 payload 的 `tokens` 座位（`benchmark_mujoco/perceive.py:1487,1511`
  把 provider 的整张 usage 映射存在这个**裸键**下）以前到达文件时是 `"tokens": "***redacted***"`，
  现在是 `{"prompt_tokens": …, "completion_tokens": …, "total_tokens": …}`。
  ⇒ **跨 batch 对账必须分侧**：batch4/5/5b/6/7 的每一个感知事件在那一格都是 `***redacted***`，
  而那不是"没有值"、更不是"那里曾有敏感信息被抹掉"，是键名规则误伤；新批次起才有 per-reading 成本。
  `usage` 这个键名同样豁免。

- **② 为什么这不是放宽安全边界。** 旧规则只看键名（`token` 在子串表里 ⇒ 整个值抹掉），豁免只有
  `*_tokens` 结尾的**计数器**；新规则在**两个调用点**（`_redact` 递归与 `log()` 顶层）都把值一起交给
  `_is_secret`，仅当值**不是字符串**时才放过 `tokens`/`usage` 这两个名字 —— 凭证是字符串，
  计数器是映射或数字。值级防线 `_CREDENTIAL_IN_TEXT`（`sk-` 起头 / `Bearer ` / `api_key`）对字符串照旧跑，
  所以真把 key 写进那个座位仍会被截。新测试一次钉四种座位：映射留下、`token=FAKE_KEY` 抹、
  `access_token=FAKE_KEY` 抹、`tokens=FAKE_KEY`（**同一个键名放字符串**）抹，且整份文件搜不到假 key。

- **③ 一字未动的部分。** 15 个事件类型、`RESERVED_KEYS`、`MAX_INLINE_CHARS`、schema 版本与
  `contract_version` 的拒写规则、manifest 的字段名、`model_calls.jsonl` 的行形状、任何判定与计数
  （`env_steps` / `official_success` / `decisions` / `model_usage` / `budget` 各格读数不受本条影响）。
  **#109 没有被本条修掉**：感知请求仍然开不出账本行（batch7 实测 5 个 `perception` 事件 vs 3 行
  `kind=decision`）；本条只是让成本在**事件流**里可读，而不是在账本里多出行。

- **已知未量 / 未被本条覆盖的（写下来以免被当成已修）**：
  ① **同一规则的另外三个座位本轮没修**，只登记：`manifest.json` 里那句写明"不记凭证"的
  `credentials` 格子本身被抹成 `***redacted***`（同一句话在 `environment_variables` 里活着，信息没丢，
  但读者去查那格时看到的是反的信号）；被 schema 拒掉的决策其原文不落盘（`raw_text` 5/5 在感知事件、
  0/3 在决策账本 ⇒ #114）；survey 仍在集外计费且致命那一集所有账本为 0（#108）。
  ② 历史批次里每一眼读取的成本**已经不可恢复** —— 本条只保护往后的产物。
  ③ 本条改的是"日志里能不能看到一次读取花了多少"，与"模型读了新句子之后会不会真的再看一眼"（CR-1，
  batch7 读数 = not observed）是两件事，别合并成一条结论。

- **回归**：定向 `tests/unit/test_event_log_safety.py` **19 passed in 1.44 s**（18→19，多的就是钉这条的那一回）；
  全量 `pytest tests -q -p no:randomly` → **1247 passed, 21 skipped in 640.23s**
  （`/tmp/mw102/full113.out` 末行；正是期望值：H-25 出货基线 1246/21 加本条新增的 1 条测试）。

## D56 —— #117（动作半）：相机通道上 `observe` 第一次**可以带参数**、决策页的 `skills` 段第一次写进这一 run 真实存在的相机名；提示哈希一字未变（2026-09-24）

- **范围**：三处全在未跟踪的 `embodied_agent/benchmark_mujoco/`（`skills.py:28-48`、`runtime.py:112-121`、
  `runtime.py:215-220`），测试落在未跟踪的 `tests/contract/test_mujoco_channel.py`（新增 3 条 + 4 个 import 行）。
  `git diff` 对本条**读到的仍是空**（与 D53/D54 同侧，与 D55 相反），身份只能由 episode manifest 的
  `code.untracked_code.sha256` 与下面两个哈希来认。

- **① 哈希对（这一条的主证）。** `catalogue_sha256` 从 batch8 归档里的
  `98e00eb536d64030ec3dcc37f6017e6c311d6bc6b27c7134198586c3ff7cb162`
  变为 `076983fb948fa6c7ed131b84e863d35531e0a19558844601921b6717af44fc7a`；
  `prompts_sha256` **保持** `3c39f0f533ace471f8b78ee82d4c07e78e5f7989d6d9c5cbff9d8bb7bdba9cad`
  —— 也就是"**给模型看的东西变了**"只发生在 `skills` 目录那一格与 `observe` 的合法参数集上，
  `MW_DECISION_SYSTEM`/`MW_DECISION_USER` 一字未动，37 集归档的提示身份仍可比。
  跨 batch 对账的人应当用这一对哈希来切线，而不是用"页面里出现了 gripperPOV"（那话从来没进过页面，见 H-28 §E）。

- **② 产物上会看到什么。** (a) `observe` 的合法参数从"空"变为"可含 `view`"，
  于是一次决策可以长成 `{"skill":"observe","args":{"view":"gripperPOV"}}` 而在语法上被接受；
  (b) 相机臂（`self.perceiver` 存在）的页面 `skills.observe.args.view` 现在写明这一 run 声明的相机名
  （`--views`，默认 `corner2,gripperPOV`）—— 该串来自 rig，不是静态表里的常量；
  (c) privileged 臂多出一条**新的拒绝理由**：
  "`observe` with a `view` names a camera, and this arm has none to point…"，
  且其 `observe.args` 与静态 `CATALOGUE` 逐字相同（不给它编造选择）；
  (d) `cleaned`（动作真正转向用的参数对）**不含** `view` —— 视角由
  `perception/arm.py:409-412 _before_execute` 从 call 上读，走的是观察通路的既有三件套
  （`_before_execute` → `_requested_view` → `_capture_world`），执行器与 `pick/place` 的 args 语义一字未动。

- **③ 一字未动的部分。** 15 个事件类型、`core/contracts.py`、`core/v02.py`、`core/runtime.py`、
  `Budgets`、`SkillName`、`ABLATION_CONDITIONS`、`render()` 的取值约定、`catalogue` 之外的任何 `SIGNATURES` 条目、
  以及**规则策略发出的 `observe`**（`benchmark_mujoco/policy.py:134`、`planning/policy.py:128/133/215/293` 仍是 `args={}`）——
  §10 的"Runtime 不替模型做策略选择"没有被本条扩大：被扩的只有模型可说的动作集合。
  `prompts.py:40` 那句"参数只能是**实体名**（形如 "peg 1"、"socket 1"），不能是坐标"也**没动**，
  它对 `pick/place` 仍然对、对 `observe.view` 不再严格成立 —— 这一处不一致**已知、故意留着**：
  改它等于改 `prompts_sha256()`，会让 H-28/H-29 的 CR-1 与 CR-9a 不再是同一把尺子。登记在案，不当已修。

- **已知未量（写下来以免被当成已修）**：本条只买到"**能问**"，没买到"**问了就有**"。
  `gripperPOV` 能否测出 `corner2` 测不出的支撑面，历史读数中样本量为 **0**
  —— 全部 20 个 `/tmp/*batch*` 归档去重后 121 个 `events.jsonl`、33 个相机臂 episode 共 **303 次 look 的 `view` 全是 `corner2`**：腕部帧每次感知都渲染落盘（batch8-L4 每个感知事件一对 `per_*_corner2.png` + `per_*_gripperPOV.png`），但 7 张 `look_*.png`（真正发给模型的那些）全是 `corner2`，腕部视角从未进入任何一次几何判定。口径订正：H-28 §E 记的是 **301**，差的 2 次来自那次扫描按 `task__L#__s#__condition` 的**目录名格式**过滤、漏掉了 `…batch6/episodes/wo_working_memory_L0_attempt1_killed_by_429` 的 2 次 look；结论不变，见 #44。
  验收判据 CR-9a/CR-9b 已写在 H-29 §E，须等额度窗口。**建议半没修**：
  `report.description` 仍被 `Feedback.short()` 丢弃、`model_payload` 仍无 `uncertainties` 键、
  `MujocoPerceptVerifier.would_resolve()` 与 `observe_subgoal()` 生产调用者仍为 0（四条同一缺口，记 **#117b** / task #118）。

- **回归**：定向 `tests/contract/test_mujoco_channel.py -k "observe_may_name or names_the_cameras or no_camera_is_refused"`
  → **3 passed in 0.79 s**；全量 `pytest tests -q -p no:randomly` → **1250 passed, 21 skipped in 637.04s**
  （`/tmp/mw117/full117.out` 末行；正是期望值：D55 的出货基线 1247/21 加本条新增的 3 条，且**无新增 skip**）。


## D57 —— #117 的**第四条测试**与出货基线 1250→1251；以及一条对 D56 自身的口径纠正（"新增 3 条"只对到写完那一刻）——本条**没有改任何产品代码**（2026-09-24）

- **范围**：**只有一条新测试**。`tests/contract/test_mujoco_channel.py` 追加
  `test_the_page_the_model_is_asked_from_carries_those_names_and_no_privileged_page_does`
  （1235 → 1302 行）。`benchmark_mujoco/` 下**一个字节都没动**（D56 之后 `skills.py`/`runtime.py` 的 mtime
  仍是 21:14:26，本条写于 22:00 之后 ⇒ 可从 mtime 直接排除）。
  因此**产品产物上看不到任何变化**，这条存在的理由只有一个：把"给模型看的那句话里到底有没有相机名"
  从一句推理变成一条会红的测试。

- **① 为什么这条纠正必须写在 delta 里（口径，不是代码）**。D56 的回归段落当时写的是
  "新增 3 条 / 期望 1250"，那条**在它写完的那一刻是对的**，`/tmp/mw117/full117.out` 也确实给出
  **1250 passed, 21 skipped in 637.04s**。但同一天的 H-30 把 #117 的测试从 3 条加到 4 条，
  于是"D56 说 3 条"与"仓库里有 4 条"同时成立。**不回改 D56 正文**（它是当时读数的诚实记录），
  改为在此登记：**出货基线 1250/21 → 1251/21**，
  全量 `pytest tests -q -p no:randomly` → **1251 passed, 21 skipped in 682.82s (0:11:22)**
  （`/tmp/mw117/full117b.out`，21:54:54 写完）。计数闭合：
  D55 的 1,247 + #117 的 4 条 = 1,251，无悬空用例。
  ⇒ 给后来人的规则：**同一天的两份文档可以引用同一条测试数的不同版本**，
  所以"多少条测试"这种数**必须连同哪一份文档、哪一次运行一起引用**，否则对账会得出一条假结论。

- **② 代码身份**（跨 batch 对账用）：新增测试**不进**任何产物哈希 ——
  episode manifest 的 `code.untracked_code` 只覆盖 `embodied_agent/` 前缀（batch9 log 原话：
  `'prefixes': ['embodied_agent/'], 'files': 76`），`tests/` 不在其中；
  `catalogue_sha256` 仍是 `076983fb948fa6c7ed131b84e863d35531e0a19558844601921b6717af44fc7a`、
  MuJoCo `prompts_sha256()`（`benchmark_mujoco/prompts.py:71`）仍是
  `3c39f0f533ace471f8b78ee82d4c07e78e5f7989d6d9c5cbff9d8bb7bdba9cad` —— 两者**当场重算过**，未变。
  `git diff` 对本条**读到的仍是空**（与 D53-D56 同侧）：未跟踪文件不出现在 diff 里，身份只能靠
  `untracked_code.sha256` 认。**引用哈希必须连同产出它的函数**：文本通道的
  `benchmark/prompts.py:77 prompts_sha256()` 是**另一个函数**，现在算出 `25502db7…`，
  拿 `3c39f0f5…` 去对文本批次会得出假结论（H-30 §A 记了这条，此处只是把它放进 delta 以便对账的人一定看到）。

- **③ 一字未动**：15 个事件类型、`core/contracts.py`、`core/v02.py`、`core/runtime.py`、`Budgets`、
  `SkillName`、`ABLATION_CONDITIONS`、`render()` 的取值约定、`catalogue` 之外的任何 `SIGNATURES` 条目、
  规则策略发出的 `observe`（仍是 `args={}`）。D56 §③ 登记的那处**已知不一致**
  （`prompts.py:40` "参数只能是实体名"对 `observe.view` 不再严格成立）**照旧留着**，
  改它等于改 `prompts_sha256()` = 换题，H-28/H-29/H-30 的 CR-1 与 CR-9a 就不同尺子了。

- **④ 顺带把 #118 的改动面量窄了（不是修，是登记更准的缺口）**。H-28/H-30 原先的说法是
  "页面没有承载 `uncertainties` 的格子"，那条读数取自 `decision_context` **事件记录** ——
  那是运行时的记账，**不是**模型被问的那段话（H-30 §E 的 #48）。本条把同一件事改问生产装配器
  （零 HTTP、零模拟器）：造一个 world 对象**明确带着** `uncertainties=[{would_resolve: "… capture from 'gripperPOV'"}]`
  的上下文，跑真 `MujocoModelPolicy.decide()`，在适配器记下的 user 串上搜。读数：
  相机名在页面（`skills` 格，True）、诊断句在页面（`progress[].unmeasured`，True）、
  remedy 句**不在**页面（`"would_resolve"` 键本身也不在，False）、`uncertainties` 格子**不在**页面（False）。
  ⇒ 缺口从"页面没有诊断"精确成一句话：**`model_payload()` 的键表里没有一个格子承载 remedy，
  世界对象上有、装配时静默丢掉**。#118 的下一步因此是**一个键**的变更面，不是一段新 payload 结构。
  本条**没有实现它**：它会改页面身份，而 CR-9a/CR-9b 的读数还没跑到一个能看见结果的额度窗口（H-30 §C）。

## D58 —— PR-114 落地：`model_calls.jsonl` 在**被 schema 拒的那一行**上多三格；出货基线 1251→1254；两个页面哈希**逐字节不变**，而这条改动对 manifest 的 dirty 半是隐形的（2026-09-24）

- **范围**：产品文件 **1 个** `embodied_agent/benchmark_mujoco/model_policy.py`（本轮实测
  `wc -l` = **177 行**；一个常数 `REJECTED_RAW_PREVIEW_CHARS = 4096`、`_file_call` 里的三格、
  `decide()` 里落盘时机从 `_to_decision` 之前挪到 `try/finally`）；测试文件 **1 个**
  `tests/contract/test_mujoco_channel.py`（1302 → **1466 行**：1302 是 D57 里已归档的值，
  42 条 `def test_`；本轮加 3 条新测试 + 既有 fatal-row 测试加 1 条断言）。
  `core/contracts.py`、`core/v02.py`、15 类事件、`Budgets`、`prompts.py`、
  `skills.py`、`runtime.py`、任何规则策略**一字未动**。
  这是 batch9 之后第一次有产品代码变化 ⇒ 本条是**读下一批之前必须先看的一条**。
  **改前那一版的行数没写进本条**：这个文件是未跟踪文件，工作树里没有上一版可查，
  而"我记得它当时是 157 行"不是一条能核对的读数 —— 这正是 ④b 那条 #121 的代价，第一次落在自己身上。

- **① 三格是加法，且只加在被拒的那一行上**（跨批可比性的全部主张）。
  新键：`raw_sha256`（对**模型原文那串字节**取摘要）、`raw_preview`（原文前缀，上限 4096）、
  `raw_truncated`（bool）。触发条件是 `meta["schema_errors"]` 存在，而那一格只有
  `_to_decision` 在抛 `DecisionSchemaError` 前会写（`adapters/deepseek.py:528`）。
  盘上实测（`/tmp/mw117/rowshape114.out`，零 HTTP、零模拟器，直接调 `_file_call`）：
  被拒行含三新格，**answered 行与 `ok:false` 行都不含**，且 `raw_response` 仍然没进日志。
  ⇒ batch9 归档那 8 行的键形状**不会被追溯改变**（**6 行 `ok:true`，每行 22 格；
  2 行 `ok:false`，每行 13 格** —— 本轮逐行数过；历史不可恢复，与 #113 同性质）；
  后来人读旧日志必须把这三格当**可选字段**。
  **4096 这个上限的依据本轮重数过，而且是第二次才数对**（`/tmp/mw117/rawchars114b.out`：
  13 个具名根目录下 `kind=="decision"` 且**有答案**的行共 **158 条**，全部带 `raw_chars`
  （不带的是 **0** 条）⇒ **min 140 / median 515.0 / mean 483.1 / max 770**，4096 = **5.32×max**。
  出货注释里那三个数就是这一组。**为什么是第二次**：第一遍我按 `ok is True` 过滤，得到 12 条 /
  max 589 —— 那 12 条不是归档，是**"带 `ok` 这个键的那部分归档**（实测：13 个根里 `kind=="decision"`
  的行共 **160 条**，含 `ok` 的只有 **14** 条 = 12 答 + 2 拒，全落在 batch7/8/9；其余 **146** 条
  根本没有这个键 ⇒ 按 `ok is True` 过滤，量的是**这个键的落盘史**，不是归档）。
  ⇒ 见 H-32 §E 的 #54。）归档里**没有任何一行**带 `raw_sha256`（实测 `False`）—— 三格只在往后的批次里出现。

- **② 仓库里读 `model_calls.jsonl` 的三个消费方，没有一个需要改**（本轮逐个看过，不是推断）：
  `cli.py:871 cmd_llm` 按行原样再打印（它才是这次改动最直接的受益者：`llm --episode …` 现在
  能把被拒那次回答打到终端上）；`evaluation/report.py:337 _ledger_latency` 只取
  `latency_s`；`benchmark/aggregate.py:91 recompute_episode` 只数行数。
  三者都按**字段名**读，而"一行 = 一次逻辑请求"这条不变量被新测试钉住
  （`len(rows) == len(asked) == 4`，落盘时机挪到绑定之后既没多写也没漏写）⇒
  行数、`raw_chars` 语义、延迟统计全部不动。

- **③ 页面身份没变，而且这次是有归档值可对**：实跑
  `benchmark_mujoco/prompts.py:71 prompts_sha256()` =
  `3c39f0f533ace471f8b78ee82d4c07e78e5f7989d6d9c5cbff9d8bb7bdba9cad`、
  `benchmark_mujoco/skills.py:115 catalogue_sha256()` =
  `076983fb948fa6c7ed131b84e863d35531e0a19558844601921b6717af44fc7a`；
  当场 dump batch9 两个 episode 的 `manifest.json`，两格值与今天**完全相同**。
  另加一条运行时不变量（不只是"没改文件"）：被拒轮的 `execution_feedback` 仍带同样的
  `rejection_reasons`（断言是 `fb["rejection_reasons"] == row["schema_errors"]`，逐字未编辑）、
  `decision_id is None`、`executed is False`、`pre_state_version == post_state_version`、
  被拒那轮什么都没执行 ⇒ **Runtime 仍然不替模型修、补、猜那段话**（§10）。
  而 `raw_preview` **不在**下一次请求的页面里：留在日志里的东西是给读者看的证据，
  不是喂回模型的原料 —— 后者才真的会换题。

- **④ 一个只有本条能暴露的断面（#101 的具体代价）**：`model_policy.py` 与测试文件**都是未跟踪
  文件**（`git status --porcelain` 两个都是 `??`）。本轮实测改完之后 `git_state()`：
  commit `4e960a2d…` 同、`changed_files` 仍 **169 条** 同、
  `dirty_diff_sha256 = 5b1f7e036fd5880e`（**与 batch8/9 相同，看不见这条产品改动**），
  **只有** `untracked_code.sha256` 从 batch9 的 `7bb7265030b04caf` 变成
  **`95110c2ad3e46b5d`**（`files` 仍 76）。
  ⇒ 给后来人的读法：**"dirty 格没变"不等于"代码没变"**；本通道的代码身份只能靠
  `untracked_code` + 两个页面哈希三格一起读 —— 前者说"代码换了"，后两者说"换的不是页面"。
  单看 `untracked_code` 变了就判"页面换了"是错的（本轮就是活例：它变了，页面一字未变）。

- **④b 同一格里还有两半方向相反的盲区（新登记 #121，两条都实测）**：
  (a) `changed_files` 里 `embodied_agent/benchmark_mujoco/` 是**一条折叠的目录行**
  （裸 `git status --porcelain` 第 52 行），所以那条产品编辑在 manifest 里**只剩一个十六进制数**，
  说不出是哪个文件变了；(b) 反过来，测试文件是**逐文件列出名字**的，
  但它的内容**不在任何摘要里**（`UNTRACKED_CODE_PREFIXES = ("embodied_agent/",)` 不含 `tests/`，
  未跟踪文件又进不了 `git diff --binary`）⇒ **钉住本条 ①②③ 的那 3 条新测试，
  从一份运行 manifest 看是不可证明的**。两种修法都不在本条里：加宽 prefix 会把这个
  **跨批比较用的字段本身**重定义一遍（所有归档的两值作废），属另一次预登记的量级。

- **⑤ 回归与基线（五遍，只有一遍是出货数）**：定向 6 条 `6 passed in 23.53s`。
  `full114.out` = `1254 passed, 21 skipped in 609.94s`（②里那条 fatal-row 断言未在）；
  `full114b.out` = `1254 / 21 / 654.56s`（断言已在）；
  `full114c.out` = `1254 / 21 / 880.70s`（注释第一版订正，`model_policy.py` 跑前后同哈希 `5da4b1be…`）；
  `full114d.out` **跑到 45% 停掉、不报数** —— 因为 ① 里那次**总体重数**已经判定它覆盖的树不是出货树；
  **出货遍 `full114e.out` = `1254 passed, 21 skipped in 621.36s`，exit 0**，树 = `sha256 0ba7d8bd5c3a6516…`
  （注释第二版）：该文件最后写入 23:34:29，这一遍 23:35:06 起、23:45:28 止（由 621.36s 反推），
  跑完再哈希仍是 `0ba7d8bd…` ⇒ 这一遍覆盖的就是出货树。
  计数闭合：1251（D57）+ 本轮 3 条 = **1254**，skip 仍 21（无新增 skip）；
  并且**当场点名重跑**确认那 +3 是本轮新写的 3 条、都不是 skip
  （`test_a_schema_rejected_answer_…` / `test_an_answer_longer_than_the_preview_…` /
  `test_filing_the_answer_changes_nothing_…` 三条 `PASSED`，加被加长的邻居共 `4 passed, 38 deselected`）
  —— 只做"1251+3=1254"的加法不能证明这 3 条真的在集合里。
  **用时只在空载下可比**：①里那几个探针与回归并发跑过，多出的秒数是负载，不是回归变慢；
  五遍里只有**计数**可比，所以每个数都必须带上它跑在哪一棵树上。
  **下一个计费窗口的基线是 1254/21**，不是 D57 的 1251/21（同一天里基线动过两次；
  引用测试数必须连同哪一份文档、哪一次运行一起引用 —— 规则随 D57 那条再来一遍）。

- **⑥ 判据不动**：CR-9a / CR-9b / CR-9c / CR-3 的预登记文本（`/tmp/mw_vlm_camera9.sh:22-42`）
  本条一字未改，也没有因为"现在看得见被拒回答了"就去改分子定义。
  #118 仍**未落地**：本轮只动日志，没动 `model_payload()`。

- **⑦ 本条自己的错误账（三条，全在 H-32 §E，这里只点名不重述）**：**#52** 我第一版归档普查用
  递归 glob 扫 `/tmp`，把 2,154 个 pytest 产物当总体（⇒ 反例规则 #17：普查按名字列举根、
  并打印被排除数）；**#53** §H 第一版写了"递归走一遍"而探针实际只走顶层（⇒ 规则 #18：
  正文每个动词都要在脚本里有实现）；**#54（最重）** ①里那个"12 条"的过滤总体——
  `ok is True` 把 146 条不含该键的行静默排除，于是 4096 的依据一路从预登记 `pr114.md:19-20`
  错到出货注释里（"订正一次"仍然是错的 ⇒ 规则 #20）。
  三条都在**出货之前**抓到并改正，代价是回归多跑两遍（第 4 遍因此中途停、不报数）。
