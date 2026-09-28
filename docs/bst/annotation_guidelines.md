# 失败归因判据（预注册）· BST-1.0 文本批次

SPEC-BST §9.1–§9.3、§8.4、§10.5 第 4–6 节要求的判据文件。本文件在**写下第一个归因行之前**
定稿，此后只允许追加"执行期发现的分歧"，不允许改动判据本身。

## 0. 定稿时已知与未知（时间戳 2026-09-20T03:23+0800）

| 已知（写下本文件时在手里的东西） | 未知（本文件不得引用） |
|---|---|
| SPEC §9.2 的 17 个标签与其"不得据此推断"列 | 任何筛查/全量 episode 的**文本内容**、动作序列或决策理由 |
| dev 段的**聚合**终止计数：`NEEDS_CLARIFICATION` 6 / `BUDGET_EXHAUSTED` 3 / `AGENT_BLOCKED` 3、`false_finish` 0、被拒决策 12（train 任务，非测量样本） | 上述任何一集的逐轮轨迹 |
| 冻结的 I0–I3 信息类别定义（§8.3）与 `attempts`/`recent_feedbacks` 的窗口形状（深度 3、上限 12） | 筛查批次是否会出现某一类标签 |
| 后端事实：官方 `done/won` 从不进入决策上下文；`progress` 恒为 unknown；无几何、无候选列表、无合法动作列表 | — |

**已知偏差声明**：dev 段的终止分布先于本文件存在，因此"哪些失败值得看"的先验不可能为零。
处置：判据表本身逐字取自 §9.2（不是从 dev 观察中归纳的），且样本选择规则（第 4 节）在任何
标签写下之前固定，不按"看起来更有趣"挑。

## 1. 三层记录（§9.1，一行 = 一个 observed_pattern，不强求唯一原因）

1. **outcome**：直接抄产物字段，不重述为自然语言（`official_won`、`termination_reason`、
   `false_finish`、`model_errors`、`semantic_repairs`）。
2. **observed_pattern**：可观察行为，允许多标签；每条必须绑定一个
   `decision_id`/`context_id`（首次偏离）与它自己的证据引用。
3. **possible_cause**：有证据支持的解释，`confidence ∈ {confirmed, supported, uncertain}`；
   `unknown` 是一个合法值，且**不需要**为它编一个原因。

后续放大因素（同一模式在第几轮之后仍在继续）单独记 `amplified_rounds`，不并进 pattern。

## 2. 一条归因的最小证据（§9.3，缺任一项则 `annotate.py` 拒绝写入）

```
episode_id, task_id, decision_id (或 context_id), instruction_verbatim,
context_refs[]        # 当时上下文里那条事实的 observation_ref
command_sent          # render(skill, args) 的原生命令，或 "none"（被拒/未发出）
environment_response  # 环境原话，逐字；没有则 null 并说明为什么没有
label[]               # §9.2 标签，1..n
reason                # 判定依据，必须引用上面某一项，不得只引用模型 rationale
competing_explanation # 至少一条替代解释，或写明"证据不排除 X"
counterfactual_basis  # 若声称存在更好的合法行动：接口规则或公开状态依据；否则 null
```

`rationale`（模型自己写的一句话）**只能**作为辅助证据：它不能单独证明"理解/遗忘/因果"。
判据：把 `rationale` 划掉之后，剩下的字段是否仍能支撑该标签？不能 ⇒ 降级为 `uncertain`。

## 3. 逐条判据（本批次的具体化，左列是 §9.2 原标签）

| 标签 | 在本文本后端里**怎样被证明**（正面） | 在本批次里**不得**据此判定（反面） |
|---|---|---|
| `TASK_UNDERSTANDING` | 指令里的对象/数量/属性（如 "two mugs"、"cool"）在 ≥2 个不相邻轮次被持续用错，且当轮上下文文本已明确陈述它 | 一次选错对象 ⇒ 可能是 grounding；"没做我预期的顺序" ⇒ 不算 |
| `PLANNING_ORDER` | 信息与动作都够（所需事实是 I2 且已在上下文里），但顺序使目标不可达，且能引用目录规则指出**合法**替代顺序 | 与专家路径不同；轨迹长 |
| `DEPENDENCY` | 前置条件公开可知（例如容器需先打开，环境已用文本说明），仍连续 ≥2 次发出依赖它的动作 | 环境未透露前提时假定模型知道 |
| `STATE_USAGE` | I2 情形：动作与**当轮上下文明确陈述**的事实冲突（引用该 fact 的 `observation_ref`） | 归因给"视觉感知"（本批次无视觉） |
| `HISTORY_UNAVAILABLE` | I1 情形：被引用的事实在上一轮说过、当轮不再陈述，随后重犯 | 证明"加 Memory 会好"；只能提保留假设 |
| `STATE_AMBIGUOUS` | I3 情形：证据过期或互相冲突，动作缺少必要确认 | 把 Adapter 的错误更新算成模型遗忘（那是 `ADAPTER_ERROR`） |
| `REPEATED_FAILURE` | 无相关新证据时重复**完全相同**的 command+args（用 §8.3 的精确重复度量），且重复前刚收到失败/无效果原话 | 跨 episode 学习不足 |
| `INVALID_ACTION` | 决策被本地/引擎拒绝：schema、技能名、参数形状或对象引用不合公开接口（`status=rejected`/`schema_invalid`，附拒绝理由原文） | 与"正确调用但环境说没效果"混为一谈 |
| `PROGRESS_LOSS` | 明确达成过的条件被后续动作破坏且未恢复（用状态序列证明，不靠印象） | 必要的中间退让 |
| `ACTION_COVERAGE_GAP` | 环境支持该原生命令但目录未暴露，或 `render` 无法发出（需 `_map_failure` 或目录证据） | 说成"Agent 不会这个技能" |
| `SKILL_LIMITATION` | 接口完整仍不能表达必要行为，需**独立**证据（如 9 条官方获胜轨迹重放里从未出现某形状） | 仅凭低分认定 |
| `OBSERVATION_LIMITATION` | 所需事实是 I0：不搜索/不显式观察就拿不到 | 推出"应该上 VLM" |
| `EXECUTION_FAILURE` | 参数与公开前提都对、命令已发出，环境回了非预期的东西 | 文本前置条件拒绝（那是 `DEPENDENCY`/`INVALID_ACTION`），机器人控制失败 |
| `BUDGET_LIMIT` | 轮数/动作/token/时间截断（`BUDGET_EXHAUSTED`/`TRUNCATED`），并记录截断前的行为 | 用它替代根因（它是结果不是原因） |
| `ADAPTER_ERROR` | 翻译/解析/缓存/传递错误，**必须**附原始 I/O（原话 vs 解析出的 `TextFact`，或 `unparsed` 非空且该句本应被读） | 记成模型规划短板 |
| `INFRASTRUCTURE` | API 不可用、环境崩溃、不可判定超时、数据损坏（`outcome=infrastructure_error`） | 删除记录以提高成功率 |
| `UNRESOLVED` | 以上都不能区分，且写明缺哪一条证据 | 强行归为 Memory / Planning |

## 4. 样本与分母（§8.4）

> ▶ 本节的纳入范围由 §6.1 追加项**收紧**（分母定义与"不外推"这条不变）。

- **筛查段**：48 集里的**全部**非成功 episode 都做归因（分母 = 该段 `benchmark_valid` 且
  `official_won=false` 的 episode 数，由 `metrics.json` 给出，不按人工判断增减）。
- **全量段**：268 集若全部标注不现实，则用**固定种子**抽：`seed=20260919`，先按 task_type
  分层，每层抽 `min(8, 该层失败数)` 个 episode，另加"每个出现过 ≥1 次 `false_finish` 或
  `model_error` 的类型至少 1 个"的强制包含项。抽样在 `annotations.jsonl` 的头一行里记
  `selection_rule`、`population_n`、`sample_n`，报告不外推为"全部失败中有 X% 是 Memory 问题"。
- **一个 episode 可以有多个标签行**（不同 pattern 不同 `decision_id`），但**分母永远是 episode**：
  标签计数写成"n / N episodes"，不写"label 总数"。

## 5. 盲评纪律

> ▶ 第 1 条里"去掉哪些字段"由 §6.2 追加项**写死到字段名**（范围只扩大，不缩小）。

1. 标注者看到的是去掉 `official_won`/`reward`/`termination_reason` 的轨迹片段
   （`annotate.py --blind` 生成），先给 pattern，再解锁 outcome 看是否改变判断。
2. 两个判读不一致时**保留两行**并标 `disagreement: true`，不投票折中、不事后统一口径。
3. 模型自评（"我认为我失败了是因为……"）不计入任何分母，只作为 `rationale` 字段存在。
4. 任何"接管轨迹"（人工/规则替模型补一步以证明本可以成功）一律标 `diagnostic`，
   不进主成绩，也不得回流改提示词或预算。

## 6. 允许追加、不允许改写的内容

追加区（执行期发现的分歧）：

### 6.1 2026-09-20T04:02+0800 · 纳入规则按 SPEC-BST §10.3 逐字对齐（收紧，只增不减）

**发现方式**：实现 `embodied_agent/benchmark/annotate.py` 的抽样函数时，把第 4 节与 §10.3
逐句对照，发现本文写下的纳入项**少于规格要求的项**（缺"每类一条成功参照"、"每个失败
task_id 至少一条轨迹"、"重复结果不一致的两条全读"、"达到 §8.3 loop 阈值的 episode 强制
纳入"）。这是工具与规格的分歧，与实验结果无关。

**为什么允许此刻改**：① 改动落在"读哪些 episode"，不落在 §1–§3 的判据、§8 的阈值或 §4
的分母定义；② 四项都是**新增必读**，没有一项被删除，标准只会更难达到；③ 写本行时
`annotations.jsonl` 尚不存在，"看过标签之后再挑样本"这条通道是关着的。筛查段的聚合数字
（成功率、终止分布、成本）在此之前已经看过，本行不据此调整任何阈值或门槛——§10.3 的
纳入项是规格先于本实验写下的要求。

**对齐后的规则**（与 `annotate.py` 的 `SELECTION_RULE` / `select()` 逐字一致；reason 代码
原样写进每个 episode 行的 `selection_reason`，抽样覆盖与标签分布分开报告）：

- 两段共同：`benchmark_valid` 且无官方胜利的 episode **全部**纳入（`failure`）；每种任务类型
  若有胜利，按 `SHA-256("20260919|"<task_id>)` 取最小者纳入 1 条成功参照（`success_reference`）
  ——§10.3 要求"每类至少读一条成功轨迹（如果有）"，没有它就无法区分"这类任务普遍失败"与
  "这一条恰好失败"。
- 全量段在此之上先加**强制项**，再做分层抽样：
  `false_finish` 的每一集、`model_errors > 0` 的每一集、`longest_stuck_repeat ≥ 3`（§8.3 的
  loop 阈值，取自聚合器自己发布的 `failure_candidates.jsonl`，不在这里二次实现）的每一集；
  每个出现过失败的 task_id 至少 1 条（`one_per_failing_task`，同一哈希序）；同一 task_id 两次
  重复结论不一致时**两条都读**（`repeat_disagreement_pair`）。
- 然后在每个 task_type 的失败集里按 `seed=20260919` 抽 `min(8, 该层失败数)`
  （`stratified_draw`）。抽到的不足 8 个时**不补成"全读"**：样本量不由它要描述的结果决定。
- 分母永远是 episode 数；一个 episode 多行标签不放大分母；样本结论不外推为"全部失败中
  X% 是 Memory 问题"。

### 6.2 2026-09-20T04:02+0800 · 盲评遮蔽范围写死到字段名

第 5.1 条说"去掉 `official_won`/`reward`/`termination_reason`"。本批次实现为
`annotate.py --blind`，遮蔽**两个字段整体**并记在 worklist 头部的 `blind_scope` 里：

- 隐藏：`outcome`（评分器的一切结论：`official_won`、`env_done`、`false_finish`、
  `termination_reason`…）与 `termination`（运行时自己的判词：`reason`、`terminal_status`、
  `failure_code`、`note` —— `note` 常常就是模型自己的解释，留着等于把答案写在题目上）。
  `reward` 在本批次不存在该字段，遮蔽要求空满足，不谎称遮住了什么。
- 保留：`instruction_verbatim`、每条 `deviations[]`（命令、环境原话、拒绝理由、轮次）、
  `final_decision`（含 `evidence_refs`）。这些是被判读的**行为**，不是对行为的判决。
- 头部仍写总体是怎么组起来的（`won_episodes_in_population` 等）：盲评遮的是单集结论，
  不是样本的构成事实。`success_reference` 这个 reason 会**说出**该集成功了，因此不写进
  盲评行；它仍出现在非盲评行与头部计数里。
- 单人标注：本批次所有标签由同一人写下，**inter-rater agreement 不可估计**。§10.3 要求的
  ≥20% 二次复核由"同一人在时间间隔后重读"完成，它检验的是判据的稳定性而不是两人一致性，
  报告中必须按这个含义引用，不得称 IRR。两次判读不一致时按 §5.2 保留两行。

### 6.3 2026-09-20T05:13+0800 · §3 表格没盖住的三条判据（筛查段标注与二次复核落盘后补写）

**时间顺序先写清楚**：本节写在 `/tmp/bst_v01/annotations/annotations.jsonl`（101 行 / 48 集）
及其 §10.3 二次复核戳（23 行）**之后**。该文件头部的 `guidelines.sha256` 因此仍是
`18da301c…`（不含本节、也不含 6.4/6.5）。我**没有**重跑 `annotate worklist` 把头部哈希换成
含本节的版本——那会让这 101 行看起来是在下面这套判据下写下的，而事实不是。每条规则都写出它
决定了哪些行，只按 §1–§3 重读的读者有权推翻它。**本节不改任何阈值、分母或成功率**（§3 的
`INVALID_ACTION` 行、§8 的门槛、§4 的分母定义原样），它只说明标签是怎么落到具体行的；
受影响的每一行都把被否掉的那个读法留在 `competing_explanation` 里（§5.2 的"保留分歧"）。

**地基测量（全部来自已落盘产物，可复算）**：筛查段 178 条 `execution_feedback` 的
`rejection_reasons` 一共只有 10 种不同字符串，其中 **0 条**与对象引用有关——
`subgoal: Extra inputs are not permitted` 44、`candidate_id:` 同 42、
`action: Input should be 'execute','finish','blocked' or 'clarify'` 3、
`expected_effect: Input should be a valid string` 2、`blocked:` 1、`clarify:` 1、
`missing_information: Input should be a valid string` 1、
`$: action=clarify must name the missing or conflicting information` 5、`LLMError … 429` 9、
`no action was executed` 9。即：本地校验器**会**拒绝内容级问题，却从不拒绝任何 id；
`render` 与解析层只按 `<kind> <number>` 的形状接受，`context.world.entities` 的名字从未进入任何
可核对的校验（§10.2 要求的"发给模型的完整脱敏 DecisionContext"也没落盘：`decision_context`
事件只存 11 个键，world 部分只剩 `n_entities` 一个**计数**）。

- **A｜"引用没有任何公开文本支持、但也没有校验器拒绝"→ 不打 `INVALID_ACTION`。**
  §3 第 `INVALID_ACTION` 行字面上含"对象引用不合公开接口"，但那要求可核对的接口违反；本批次
  对 id 只做形状检查（上面的 0/178），按字面套用会把三种不同起因压成一个标签。改按各自证据
  分派（B、C 与 6.4 的 D），`INVALID_ACTION` 作为备选读法**逐行**写进 `competing_explanation`
  ——筛查段这样做的行有 15 行（`STATE_AMBIGUOUS` 5、`OBSERVATION_LIMITATION` 4+1、
  `ADAPTER_ERROR` 3、`HISTORY_UNAVAILABLE` 2）。
- **B｜引用的索引恰好等于 `episode_id` 尾部的 `Class-N` 片段 → `ADAPTER_ERROR`（`supported`）。**
  证据要件按 §3 的"必须附原始 I/O"来凑齐：该轮 payload 里的 `episode_id` 字段原样、该 gamefile
  reset 的公开清单原话、模型命令原话、环境原话，外加两处代码位置（`prompts.py:95`
  `TextDecisionContext.model_payload` 把 `episode_id` 放进模型据以判读的 JSON；
  `runner.py:106` `episode_id_for` 由 `task_id` 最后 40 字符拼接，`GarbageCan-10` 到模型那里是
  `ageCan-10`，`Drawer-10` 则整段保留）。§9.2 规定 `ADAPTER_ERROR` 不计为模型规划短板，所以这
  3 行（slot003 / slot020 / slot043，全部 `go to drawer 10`，而 reset 只公开 `drawer 1/2/3`）
  不进"决策能力"的账。只到 `supported` 不到 `confirmed`：匹配可能是巧合，10 次里 3 次不足以排除。
- **C｜I0 不成立时的 "Nothing happens." → `STATE_AMBIGUOUS`（5 行）。**
  §3 的 `OBSERVATION_LIMITATION` 要求那条事实是 I0。如果这一轮**自己依据的观察句**或该 gamefile
  的 reset 原话已经把同类实例全部列出（公开 `drawer 1/2/3` 而模型发 `go to drawer 10`），那么
  "drawer 10 在不在"不是 I0；而环境回复不含提示词列出的任何一种原因 ⇒ I3、证据过期或冲突且
  缺少必要确认。这条是 6.5 里唯一一次改判的来源。

### 6.4 2026-09-20T05:13+0800 · 两条 §3 已覆盖、但"怎样被证明"需要补记的判据，以及两个只读探针

- **D｜`STATE_USAGE`（3 行）的表面形式**：`alfred_go` 被回以 "Nothing happens."，而**该轮
  `decision_context` 所依据的那条观察句**已把智能体写在目标处。匹配只允许五种原话
  （`facing the …X` / `arrive at X` / `on the X, you see` / `X is open|closed` / `from the X`），
  且不接受"上一轮说过"当轮已不陈述的情形（那是 `HISTORY_UNAVAILABLE`）。命中：slot036
  `go to desk 1`（"facing the desk 1"）、slot014 `go to cabinet 2`、slot011 `go to countertop 2`
  （"from the countertop 2"，且它刚从那上面把 mug 捡起）。不判 `EXECUTION_FAILURE` 的理由是
  §3 反面栏里那句"文本前置条件拒绝不是机器人控制失败"。本批次 `EXECUTION_FAILURE` 因此只剩
  1 行（slot025 第 4 轮 `examine desklamp 1`），而这一行在 6.5 里被记为有争议。
  泛化边界：`scratch/probe_already_there.py` 在两段产物里只找到 2 次"捡起之后再 `go to` 同一
  receptacle"的调用，两次都是 "Nothing happens."，涉及 1 集——所以 D 只用于当轮文本已经说出
  位置的情形。
- **E｜`REPEATED_FAILURE`（1 行）的取证方式**：判据在 §3 已有，补记的是本批次怎么满足
  "无相关新证据"——同一 `command_sent`、环境**原样**重复同一句回复，且把两次之间每一轮的环境
  原话逐条引在行内（slot035 第 3 与第 5 轮都是 `take apple 1 from desk 1`）。因为"没有新证据"
  是判读而不是可核对的事实，引文必须进 `evidence`，不能只留结论。§9.1 允许一行多标签，所以它
  挂在该轮已有的 claim 上，不另起一行放大行数。
- **P1｜reset 探针**（`scratch/probe_initial_obs.py` → `initial_obs_all.jsonl`）：对每个 episode
  的 gamefile 只重放开局，取那句初始房间描述——它是本批次唯一公开的 receptacle/object 清单。
  它存在的理由是 §10.2 没被产物满足（每轮 world 只存计数）。确定性核对：
  `scratch/probe_targets_and_determinism.py` 对前 6 个 gamefile **各重放 reset 两次**并比较文本
  哈希，6/6 两次一致（产物已存
  `/tmp/bst_v01/scratch/probe_targets_and_determinism.json`，`games_checked: 6,
  all_identical: true`；后端走 `AlfredDemangler(shuffle=False)`，见 `backend.py:60,67`），
  所以"reset 原话就是第 1 轮看到的那句"是被检查过的假设，不是假设——但它只在被抽查的 6 个
  gamefile 上成立，没有对全部 48 个做过。**边界（§4.5）**：探针结果只用于本文件与
  `annotations.jsonl` 的事后核对，一次也没有回流进 prompt、`Feedback` 或 `DecisionContext`，
  没有产生任何新的环境动作，因此不影响任何一集的预算或结果。
- **P2｜`make_annotations.py` 里的三段核对**（`unpublic_refs` / `already_at` / `repeated`）都是
  对已落盘事件的 join，不重跑环境；它们的一个已知弱点是观察文本要靠
  `execution_feedback.feedback.environment_text` 反查 `post_observation_ref`，而"未推进状态"的
  feedback 与"推进了状态"的 feedback 在这张表里必须区别对待（否则会把旧句子覆盖成新句子）——
  这个覆盖 bug 曾让 slot035 的一行消失，修好后本段行数 100→101。

### 6.5 2026-09-20T05:13+0800 · §10.3 二次复核做了什么、没做什么

抽样在标注之前就用固定规则定下并写进
`/tmp/bst_v01/annotations/second_review_sample.json`：
`random.Random(20260919).sample(sorted(episode_ids), ceil(0.2 * 48))` = **10 / 48 集 = 20.8%**，
抽样单位是 **episode**（按行抽会让同一集的系统性错误被计几次还自称独立证据）；这 10 集覆盖
**23 / 101 行 = 22.77%**。重读结果（`stamp` 写回每行的 `second_review`，头部计数同步）：

- **20 行 held**、**1 行 changed**、**2 行是重读新加的**。
- 唯一的改判是 slot003 第 3 轮：首遍 `OBSERVATION_LIMITATION` → `STATE_AMBIGUOUS`，依据就是
  6.3-C（reset 公开了 `drawer 1/2/3`，I0 条件不成立）。**这次改判说明 6.3-C 在首遍并没有被
  一致地执行**——同一个探针、同一条规则，首遍用在了别处而漏了这一轮。报告里按"判据不稳定"
  引用它，不按"复核纠正了错误"这种表扬自己的方式引用。
- 新加的 2 行：slot003 第 3 轮的 `ADAPTER_ERROR`（6.3-B），slot035 第 5 轮的
  `REPEATED_FAILURE`（6.4-E，该轮首遍根本没有行）。
- **1 行 held 但把分歧留住**：slot025 第 4 轮的 `EXECUTION_FAILURE`。`alfred_examine` 在本段
  2 次成功 / 2 次 "Nothing happens."，而 `alfred_use` 4/4 从不空转，所以这更像"动词被拒"，
  那样 6.4-D 引用的排除栏就要接手。定这个分歧需要直接探测引擎，本遍没有做，原话留在行里。
- **它不提供什么**：单一标注者 ⇒ inter-rater agreement 不可估计（§6.2 末条），二次复核检验的是
  判据随时间的稳定性，不是两人一致性，报告不得称 IRR；被复核的 10 集不能代表 48 集的标签分布，
  改判率只按 1 / 23 行（或 1 / 10 集）报告，不外推。

### 6.6 2026-09-20T08:19+0800 · 两段二次复核收口；"标签保住但句子重写过"单独成类；模板证据的缺陷类

本节只追加，不改写 6.1–6.5；与 6.5 冲突之处在下方第 1 条明确写出以哪份产物为准。

**1｜两段最终的判决数（`annotations*.jsonl` 头部与每行 `second_review` 是准）**

| 段 | 抽样 | 行覆盖 | held | changed | 重读新加 | wording_corrected |
|---|---|---|---|---|---|---|
| 筛查 | 10 / 48 集 = 20.8% | 24 / 106 行 = 22.6% | 19 | 2 | 3 | 0 |
| 全量 | 53 / 264 集 = 20.1% | 122 / 530 行 = 23.0% | 101 | 4 | 1 | 16 |

- 全量段标注对象：268 集 benchmark-valid，265 集进入标注抽样（population 里有 6 集官方赢），264
  集有行，共 530 行（`sampled_deviation_rows: 391`，每集偏差行上限 4）；二次复核的抽样基数是
  **有行的 264 集**，`ceil(0.2 * 264) = 53`。
- **6.5 的数被这份表取代**：6.5 记 23 行 / 20 held / 1 changed / 2 added，那时的手工判决表还没有
  slot003 第 4 轮那条结构性 `OBSERVATION_LIMITATION`，也还没有把 slot025 第 4 轮的
  `EXECUTION_FAILURE` 撤下。撤下的理由见下面第 3 条，它同时把 6.5 的"1 行 held 但把分歧留住"变成
  `changed`，所以筛查段是 19 / 2 / 3 而不是 20 / 1 / 2。

**2｜为什么必须有 `wording_corrected` 这一类**

定义：标签在重读后仍然成立（`labels_agree=true` 是硬条件），但首遍那句证据陈述被它所引用的产物
推翻。记 `held` 会把句子和标签一起保住，记 `changed` 会说谎——标签没有动。`stamp` 与
`merge_verdicts.py` 都拒绝 `labels_agree` 与 `result` 不一致的判决。

**held 低估了措辞漂移，这是量出来的**：把最终文件与两份存档逐键
（`episode_id|round_index|claim`，530 行折成 527 键，因为 3 集各有 2 条 `final_decision` 行）比对——

- 对 07:28 存档：208/527 键的 `reason` 不同 ⇒ `schema_refusal` 148、`truncation` 41、
  `final_decision` 17、`unpublic_reference` 2。
- 对 06:49 存档：222/527 键不同，另有 10 行标签由单 `OBSERVATION_LIMITATION` 变成
  `OBSERVATION_LIMITATION,STATE_USAGE`（6.4-D 的 `else_`/`at`/`rep` 三个测试后来才进生成器）。
- 这 208 行里，**101 行 held 判决之下有 28 行的句子被重写过**（对 06:49 存档是 29 行，另有 2 行
  因标签已变而在旧存档里查不到同键）。16 行 `wording_corrected` 是"原句不成立"的那部分，28 行是
  "原句不假、但被规则加强"的那部分。报告引用 held 率时必须并列给出这两个数：**held 计的是标签与
  判据，不是句子**。

16 行的共同错误样式是"分组计数与 token 没有从 packet 重算"：例如 slot260 第 1 轮，本段存储的 raw
response 里 `candidate_id`/`subgoal` 合计 406 次（顶层 400 = {`candidate_id` 196, `subgoal` 204}，
`execute` 内 6），首遍把那 6 次折进了 400。

**3｜改判的 4 行与新加的 1 行（全部留住首遍标签，不覆盖）**

- slot009 第 2 轮：`HISTORY_UNAVAILABLE` → `INVALID_ACTION`。I1 缺口之下要看到"重犯错误或丢失进展"，
  该轮没有；它是一个落在公开接口之外的引用。
- slot025 第 4 轮：`OBSERVATION_LIMITATION + EXECUTION_FAILURE` → `OBSERVATION_LIMITATION`。
  §9.2 的 `EXECUTION_FAILURE` 行首栏是"参数与**公开前提正确**"，而文本环境永远给不出这一半：
  prompt 自己为 "Nothing happens." 列的三个原因（对象不在、容器关着、位置不对）全是前提失败。
  全段 12 行、筛查 1 行的该标签随之撤下。分歧没有抹掉：排除读法留在该轮的 competing 字段里。
- slot032 第 4 轮：`OBSERVATION_LIMITATION` → `STATE_AMBIGUOUS`（reset 公开了那个类，I0 不成立），
  同一轮新加 1 行 `INVALID_ACTION`（`probe_near_miss.py`：被引 base 是 reset 列出某个类名的前缀）。
- slot067 第 3 轮：`OBSERVATION_LIMITATION` → `OBSERVATION_LIMITATION,STATE_USAGE`。该行原本写着一句
  关于"当轮文本没有说过位置"的缺席断言，而它没有对着持久回复核对过；核对后当轮文本确实说了位置。

**4｜停机轮的模型措辞与公开事实冲突（§9.1 是承重的，不是装饰）**

抽样里 5 个停机轮的 rationale 与一条本集持久下来的公开句子矛盾，逐个已核：slot025 第 5 轮（"没有
可见的 mug 或 desklamp"，而第 1 轮回复就在 desk 1 上点名 `a desklamp 1`）、slot050 第 5 轮（"所有
尝试过的命令都回了 Nothing happens"，第 1 轮是到达句）、slot082 第 5 轮（"没有任何 prior text 提到
cabinet 或 cloth"，前半不成立、后半成立）、slot084 第 3 轮（"没有 mug 或 cabinet"，reset 列了
cabinet 1..6）、slot250 第 5 轮（"`go to desk 1` 和 `use desklamp 1` 都没有效果"，第 2 轮的
`use desklamp 1` 回了 "You turn on the desklamp 1."）。另有核对为准确的停机轮（slot176 第 5 轮、
slot266 第 3 轮），所以这不是"模型总在胡说"，是"不能拿它当证据"。**§9.2 没有"误报历史"这一行**，
判据在抽样之前就冻结了，因此这些计数进报告的证据质量一节，不进标签。

**5｜模板证据的缺陷类（本轮修掉的与留下的）**

1. **句号后的小写拼接（10 行）**：`make_annotations.py` 里 `world_said` 这个子句同时被用在句中
   （"while …"/"and …" 之后，小写正确）与句号之后（读起来像断句）。修法是先算 `world_cap`
   （首字母大写）再在两个句号后的分支用它。验证方式本身就是结论：改前改后各重跑一次两段，逐键
   diff = **恰好 10 行 `reason` 变化、0 行标签变化、0 个其他字段变化**，筛查段 **0 行**变化。
   这 10 行全是 `no_effect_round` 的 `OBSERVATION_LIMITATION,STATE_USAGE`，其中 3 行已盖章
   （slot067 第 3 轮、slot250 第 4 轮、slot257 第 3 轮）；slot257 的判决 note 当时写明"记下来但不
   在这个周期里改"，所以是在收口之后才改，判决文字保持原样不改写。
2. **`verify_absence.py` 的空转**：它从 `episode_start.payload.task_id` 解析任务类别，而 `_redact`
   在 268 集里有 6 集把 `task_id` 屏蔽成 `***redacted***`（`episode_tokens_used` 同规则），于是
   类清单为空、"这个类整集从未出现"式断言被**空转通过**。改为优先读 `episode_summary.json` 的
   `task_id`，解析不出来就显式写 `UNPARSED_TASK_ID`。slot180 的 held 判决差点盖在这份空转输出上——
   是这次核对拦住的，不是写判决时想到的。
3. **未判决行的计数口径**：临时脚本按 episode 判断"这集判过没有"，把 122 行的缺口读成 41；按
   `episode|round|labels` 全键比对才是真缺口。`merge_verdicts.py` 每次 merge 后打印
   `still_unjudged_count`，缺口可见而不是静默。
4. **stray 键**：第 1 批用短柄手写的判决文件直接进 merge 会得到 20 个永不落到行上的键，并把
   `still_unjudged` 从 41 抬到 52。现在 merge 遇到任何不在抽样键集合里的判决直接失败；短柄必须经
   `verdict_batch.py` 解析，它拒绝匹配 0 个或 >1 个行的柄。
5. **留下的**：`world.targets == []`、每轮 world 只存计数不存句子、prompt 不落盘（只存
   `raw_response`）——这三条不是标注器能修的，进 §10.2 与 §11。

**6｜仍然不提供什么**：单人复核 ⇒ 不可估计 inter-rater agreement；全量段的改判率只按
4 / 122 行（或对应集数）报告，不外推到 264 集；本批 268 集里有 **6 集官方赢**，其中 2 集进入标注
抽样且带行、4 集未进入抽样，被抽样的 slot064 一行都没有（赢且无可标注偏差）——所以赢的形态在
标签分布里几乎不可见，报告谈覆盖率必须同时给"集"和"行"两个分母（`selection_coverage`、
`episodes_sampled_without_a_row` 就是为这个留在头里的）。

### 6.7 2026-09-20T08:33+0800 · 形类计数的第二处偏差：把"没送到引擎"的反馈算成了"引擎答了"

6.6 第 5 条记的是句子层面的缺陷。这一条是**数字层面**的，形状不同：错的不是某一句断言，而是一段
被许多行引用的统计量的口径。

**缺陷**：`make_annotations.py::batch_command_form_outcomes()` 只按 `if skill:` 收事件，然后用
"回话不是 `Nothing happens.` 开头"来定义 `succeeded`。而一条 `executed=False`、`status=rejected`、
`failure_code=INVALID_DECISION`、`environment_text` 为空的 `execution_feedback` 是**本地校验器的拒收
记录**——命令从未进入引擎，引擎也就没有"答"。它落进了"不是 Nothing happens."的那一侧，于是被当成
一次带状态变化的成功。修法是把门改为 `if skill and fb.get("executed") and text:`（`executed=True` 表示
载荷过了本地校验且命令真的下发；`text` 非空表示引擎真的回了话）。

**逐集核出的 10 条**（全量段，全段仅此 10 条，无其他形状）：`alfred_go` 7 条（slot094、slot144、
slot153、slot161、slot164、slot168、slot182）、`alfred_take` 1 条（slot113）、`alfred_examine` 1 条
（slot135）、`alfred_use` 1 条（slot239）。后果：**go 301→294、take 76→75、use 17→16、examine 4→3**，
四个 `nothing_happens` 计数不变（43 / 14 / 4 / 10）；筛查段一条都没有，因此该段数字与行文本**逐字节不变**。

**改动面（改前改后各重跑两段，逐键 diff）**：全量段 **16 行 `reason` 变化**，全部是
`no_effect_round`（其证据句尾部引用了本段形类计数），**0 行标签变化、0 个其他字段变化**；筛查段
**0 行**。行数仍是 530 / 106，抽样键集合不变，所以 122 + 24 份判决可以原样重盖。

**顺带核上的一件事实**：修正后的 `examine` 计数是 3 次真回话，逐条读出来是
`'The cabinet 1 is closed.'`（slot016）、`'On the desk 1, you see a alarmclock 1, …'`（slot045，
contents 形状）、`"There's nothing special about pencil 3."`（slot174 第 8 轮）。也就是说
`alfred_examine` 的**描述性句式在真实轨迹里有一个样本**，此前记为"仍无样本、须标为未测面"的那条开放
事项不成立，`compatibility_delta.md` §8 已按此改写；三种句式都被解析器接住（`receptacle_state`、
`contents`、`no_new_information`，`unparsed` 为 0）。slot174 还给出这条更值得引用的对照：同一集里
`examine pencil 3` 第 2 轮回 `Nothing happens.`，第 8 轮回描述句，中间唯一改变局面的是第 3 轮的
`You pick up the pencil 3 from the desk 2.`——同一条命令、同一个目标、两种答案，而引擎不说原因。
这是 `OBSERVATION_LIMITATION` 的正例，不是反例。

**留下的 4 行带判决的过期数字**（不重写）：slot144 第 3 轮与 slot250 第 4 轮的 note 引 301/43，
slot174 第 2 轮引 4/10 并写"something else 4 times"，slot025 第 4 轮引 4/10。二次复核的 note 是
**那次重读当时能看到的证据**的记录；把它改成本次才算清的数字，等于用今天的核对去美化昨天的判断。
更正记在本节与标注文件头部的 `command_form_outcomes_in_segment` 里，任何引用这四份 note 的报告必须
并列给出修正后的计数。

**这一类发现的通用形状**（进 §10.3 与报告"证据质量"节）：`reason` 里出现的**每一个数字**都是一条
未标注的断言，它由某个 join 出来，而 join 的口径不在判据表里。标签计数看不见它——16 行改的是句子，
0 个标签动过；`held` 计数也看不见它。因此报告不得把形类计数当作对"命令合法性"的独立证据：它测的是
本段内部的应答分布，且必须写明 `succeeded` 在这里的含义是"引擎回了话且不是 `Nothing happens.`"。

**7｜本轮重哈希链路的实际闭合情况**：追加本节之后重建两段 worklist（cap 4、同 run root、同 segment），
与新存档逐行比较——全量段 265/265、筛查段 48/48 的**载荷行 JSON 集合完全相等**，头部只有 `written_at`
与 `guidelines.sha256` 两项变化。新判据哈希 `15e359d04a1f1a863416baf13e530461c61c443ca4675807d2c5c600ab958b0d`
（两段 `annotations*.jsonl` 与两份 worklist 头部一致；旧值 `11899e96…`、`18da301c…`、`0f986e5e…` 留在
各自存档里）。生成→盖章→校验复跑：530 / 106 行，122（101 held / 4 changed / 1 added / 16 wording）与
24（19 / 2 / 3 / 0）份判决原样重盖，两段 `annotate validate` 的 `invalid` 与 `unlabeled` 均为空。
**并且**：本节这段记录哈希的文字本身会让文件再变一次，哈希因此无法只靠打开这份 md 复算——所以建包
的同一时刻把判据文件按字节存了一份：`/tmp/bst_v01/annotations/guidelines_bytes_at_15e359d04a1f.md`
（`sha256sum` 直接等于头部那个值）。上一节记的 `11899e96…` 没有这样的字节副本，是这次才补上的机制；
它意味着那两个值只能与头部对照、不能与文档正文对照。这是 §10.4 哈希链路的一处真实缺口，不是一句
"可复现"能盖过去的。
