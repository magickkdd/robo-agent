# Benchmark-based Stress Testing of v0.1 Agent

版本：BST-1.0  
日期：2026-09-19  
状态：面向后续实现的规格；本次交付仅文档，不表示环境已经安装或实验已经运行。  
阶段性质：Diagnosis phase；不是 Continuous Decision v0.2，也不是新 Agent 开发。  
实现目标仓库：`/home/czx/embodied-agent-robot-agent-embodied-agent-4`。  
本地交付目录：`C:/Users/czx/Documents/Codex/2026-09-18/wsl-embodied-agent-framework-spec-spec-2/outputs`。

## 1. 项目定位

### 1.1 研究目标

在冻结 v0.1 核心持续决策机制的前提下，将其接入一个成熟、公开的任务环境，采集完整执行证据，回答：

> 当前 Agent 在公开多步骤任务中何时开始失败，首先在哪个环节偏离，以及哪些因素使局部错误扩大为整集失败？

本阶段优先识别以下边界：任务要求理解、前置条件与依赖处理、当前状态使用、已有进展保持、失败后的行动调整、循环与停止行为、动作接口覆盖。

研究问题分为：

1. RQ1：不同原生任务类型的成功率、成本与失败模式有何差异？
2. RQ2：必要信息未被观察、被观察但已离开上下文、仍在上下文却未被使用，分别出现多频繁？
3. RQ3：失败后，Agent 是否根据可见反馈改变行动，还是在缺少新证据时重复尝试？
4. RQ4：哪些失败来自 Agent 决策，哪些来自适配、接口覆盖、环境或基础设施？
5. RQ5：现有证据足以支持哪些 v0.2 候选研究问题，哪些解释仍需对照实验区分？

这是描述性、诊断性研究。任务间比较不自动构成因果结论；本阶段不证明持续决策相对 one-shot 的普遍优势。

### 1.2 不解决的问题

- 不实现 Working Memory、Episodic Memory、技能发现、技能生成、跨集学习。
- 不实现子目标管理器、任务图、全局重规划、自动恢复策略或新的反思调用。
- 不训练或微调 LLM、VLM、VLA、RL 策略，不建设视觉识别管线。
- 不设计新 benchmark、不生成新任务、不向官方任务注入自定义扰动。
- 不追求排行榜领先，不以提高成功率作为本阶段实现验收条件。
- 不同时接入多个 benchmark，不进行多模型竞赛。
- 不以文本环境结果推断真实机器人的抓取、接触控制或视觉能力。

适配函数、诊断记录和评测脚本是本阶段必要工程；它们不得成为新的 Agent 决策模块。

### 1.3 为什么现在不直接做 v0.2

依据用户提供的 v0.1 完成报告：

- state_change 子集，one-shot 为 9/24，持续决策为 20/24；其他两类子集均达到 24/24。
- 整体预注册 claim 未达；状态对诊断为 9/12，低于要求的 10/12。
- 已出现同一不可达对象连续抓取 11 次的轨迹，但这不能单独证明必须增加 Memory 或新技能。
- 子目标字段没有实际写入；全局重规划机制未实现；EpisodeStore 是日志而非跨任务学习。
- 实际返回模型为 deepseek-flash，请求名为 deepseek-chat；后续 Agnes 只有单集运行，不能视为同一已验证被试。
- 当前观测为特权状态，动作、目标和验证仍有桌面整理语义。

因此，当前证据不足以在 Memory、Planning、Skill Acquisition 等方向中作出可靠选择。应先在公开任务上获得可归因的瓶颈证据。

上述是完成报告中的事实摘要，不是本规格编写时重新运行的实验。Phase 0 必须核对真实完成版代码及配置。已有本地 v0.1 设计稿记载的是更早开发状态，不能据此覆盖完成报告或实际仓库。

### 1.4 预期产出

最终交付是：**Failure Analysis + Bottleneck Map + v0.2 Decision Evidence**。

具体包括：

1. 一个 ALFWorld 文本后端适配，以及职责与信息边界检查。
2. 冻结的代码、数据、模型、提示词、预算、任务清单与运行清单。
3. 可复核的逐轮输入、输出、行动、反馈和终止轨迹。
4. 筛查结果、完整评估结果或明确标识的不完整批次。
5. 自动统计、人工归因记录及 Bottleneck Report。
6. 基于证据的 v0.2 候选方向比较；不自动生成或实施 v0.2 SPEC。

低分、零增益、没有发现稳定瓶颈，均是允许的研究结果。

## 2. Benchmark 选择

### 2.1 约束与比较

目标资源为 RTX 3050 4GB、约 16GB 主机内存、WSL/Linux、API LLM。首选单进程 CPU 环境，不下载不必要的训练集图像、模型权重或启动 GPU 渲染。

以下为官方资料核查后的接入判断，不是本机性能实测。代码开放与底层模拟器、数据资产许可分开核查。

| 候选 | 任务与动作 | 对当前研究的价值 | 不作为第一选择的原因／接入成本 |
|---|---|---|---|
| **ALFWorld TextWorld 后端** | 家庭搜索、处理、放置；文本语义动作 | 原生多步任务、反馈、公开划分与成功判定；容易记录全过程 | **首选**；CPU 可运行，仍需改接任务、状态、动作与终止边界 |
| ScienceWorld | 文本科学实验、工具使用、状态演化 | 流程依赖和信息使用；CPU + Java | 科学知识与特殊动作语义增加归因变量；作为未来备选，不并行接入 |
| VirtualHome Evolving Graph | 家务程序与状态图，纯 Python 图模拟 | 易表达关系与组合动作 | 平台不等于统一评测协议；还需选择已有任务、目标与评分规范，可能重新引入自建评测成本 |
| ALFRED | AI2-THOR 中的视觉导航与操作 | 更接近具身执行，有长序列与不可逆变化 | 感知、导航、交互掩码、渲染与旧依赖成本高；官方评测输入限制与特权状态方案不同 |
| CALVIN | 连续控制、连续语言操作链 | 适合技能衔接与控制可靠性 | 需要已有低层策略；标准任务链提供连续指令，不能直接等同自主高层分解 |
| ManiSkill | SAPIEN 操作任务、连续控制 | 后续控制／技能边界研究 | 语义技能封装成本较高；官方支持表列 WSL 可 CPU 仿真，但不支持 GPU 仿真与渲染 |
| RLBench | CoppeliaSim/PyRep 多操作任务 | 物理操作与多步执行 | 需控制技能、对象落地与模拟器依赖；容易首先测到控制接入问题 |
| Habitat Rearrangement | 移动操作与室内重排 | 空间、导航、操作组合 | 需指定任务套件、资产及执行技能栈；成本高于当前诊断需求 |
| BEHAVIOR-100 / BEHAVIOR-1K | 丰富家务、物体状态和约束 | 长期很相关 | 前者为旧版；1K 当前官方要求 32GB+ RAM、8GB+ VRAM，超出本机配置 |
| MineDojo | Minecraft 探索、合成与科技树 | 可作未来技能积累研究参考 | 原生复合动作不是现成程序技能库；额外游戏技能封装会改变被试能力 |

### 2.2 决定

本规格只实施 **ALFWorld 的 AlfredTWEnv 文本后端**，不安装 THOR 视觉后端作为主实验依赖。

采用官方原生任务、动作规则与成功判定。不改变任务定义、不重写奖励，不自建任务生成器。

测试对象是：在 ALFWorld 动作抽象和局部文本观测下，v0.1 持续决策机制如何表现。

**该结果不代表真实机器人能力，也不直接验证 VLM、运动控制、新物理技能学习或外部随机扰动恢复。** 标准文本任务中的状态变化主要来自环境动作语义；动作拒绝后的调整不等于真实抓取恢复。

首轮若全部任务接近满分，应报告当前 benchmark 对该被试存在诊断天花板，不能自行增加扰动或扩展任务。是否引入第二 benchmark 是下一次研究决策。

## 3. 接入架构与冻结边界

### 3.1 运行结构

```text
ALFWorld reset / step 的公开观察与反馈
                    |
          Environment Adapter
                    |
       Observation + WorldState
                    |
       现有 DecisionContext 构造
                    |
        冻结的 LLM 单步 Decision
                    |
       现有校验与预算／终止守卫
                    |
             Action Adapter
                    |
       一个 ALFWorld 原生命令
                    |
          环境反馈 -> 下一轮

环境的 reward / won / done 等评估信息
          -> 隔离的评估与生命周期通道
          -> episode 终止后的统计与诊断
```

图中 Adapter 是职责边界，不要求创建独立服务或通用插件框架。优先扩展现有环境入口与日志；不得另写一个功能相似的新 Agent 绕过原有决策路径。

### 3.2 冻结的核心机制

以 Phase 0 确认的 v0.1 持续决策 B 路径为基线：

- 同一模型端点配置、请求参数、实际模型身份要求。
- 同一单步决策方式：一次最多一项技能调用；保留 execute / finish / blocked / clarify 控制类型。
- 同一通用决策要求、返回结构、依据与预期效果字段。
- 同一近期反馈深度：报告显示为 3；若源码不同，以核查结果记录并冻结。
- 同一已有 AttemptRecord 构造、容量和裁剪规则；不得新增历史摘要或恢复建议。
- 不启用当前未写入的 subgoal、todo 或长期意图维护字段。
- 同一 schema 错误、语义拒绝和网络错误处理方式；所有调用均计费入账。
- 同一无效重复守卫算法及阈值；不得为提高分数修订。
- 同一预算检查和控制终止机制；数值按本规格统一设置。

冻结是“核查后的具体实现”，不是按旧设计稿补齐本来不存在的功能。

### 3.3 允许的改动

- 环境初始化、reset、step、关闭、数据定位与版本记录。
- 公开观察的确定性解析；原始文本透传。
- 环境对象名称、动作语法与 SkillCall 参数的机械转换。
- 新环境必要的状态属性与可空值；去除桌面坐标、槽位、IK 的强制假设。
- 环境反馈到现有反馈结构的映射。
- 官方评估结果的隔离读取、轨迹与统计字段。
- 单纯环境说明与动作目录的提示词替换。
- 将场景专用调用移到可替换后端；不得顺便重构规划或记忆系统。

所有变化必须列入 `compatibility_delta.md`，逐项记录：旧行为、新行为、必要性、可能影响、验证证据。

### 3.4 必须披露的兼容性变化

不能同时要求完全保留桌面版输入、评分和技能实现，又要求原生 ALFWorld 信息条件。本规格明确允许以下变化，但不得声称“原程序零改动”：

1. **任务入口**：原始 benchmark 指令每轮保留在 GoalSpec/Task Context 中。桌面颜色／托盘 assignment 专用目标解析不适用于该环境。主实验使用原始指令透传，不另加模型目标解析调用或 PDDL 目标编译器；记录为任务入口差异。任务理解仍在每轮 Decision 中发生。本阶段不评价旧桌面目标解析器。
2. **观测来源**：从特权全局几何改为原生局部文本；来源必须不同，未观察事实为 unknown。
3. **动作后端**：用 ALFWorld 原生命令替代桌面运动技能实现。保留 SkillCall 层，不把已有物理控制器说成已迁移。
4. **进展**：没有可见证据的目标进展为 unknown，不接入官方隐藏子目标完成度。
5. **完成语义**：原桌面 finish 真值拒绝机制不能照搬为可反复查询的评分 oracle。终止细则见第 6 节。

最终结论必须使用“v0.1 决策机制经环境适配后的表现”。不得把跨环境分数差直接解释为架构性能变化。

### 3.5 Adapter 不得执行的行为

- 选择目标对象、替换对象 ID、模糊匹配到另一实例。
- 自动导航、开门、找物、放下物体以满足模型动作前提。
- 合成多个原生命令来实现一条模型调用。
- 自动重试、换策略、换动作参数或修复模型语义错误。
- 调用 LLM/VLM 解析观察、总结历史、选择下一步。
- 将专家路径、隐藏合法动作、剩余目标或建议策略写入反馈。
- 将生成失败的调用静默改成 look、inventory 或其他安全动作。

每个合法 execute 必须对应且仅对应一次原生 env.step。benchmark 自身原生命令可能是高层抽象；记录这种抽象，不额外拆装。无效 schema 可以在本地拒绝，计一次决策但零次环境 step。

### 3.6 动作与本地校验的接入要求

动作目录从固定版本原生语法建立，至少核对移动到容器、取出、放置、打开、关闭、使用设备、清洁、加热、冷却、look、inventory。仅暴露该版本实际存在的命令，不自行创造技能。目录须保存：原生命令模板、参数名、对象引用格式、公开前提说明及映射例子；例子只能解释语法，不提供完整任务解法。

同一对象引用必须原样传给环境。若使用内部稳定 ID，只允许与原生公开名称一一对应的可逆编码，不从隐藏对象表预建实例目录。对象未在当前观察中出现但模型从现有历史引用它时，不得仅因局部 WorldState 缺失就拒绝；由环境判定当前调用是否成立。

桌面专属的槽位、IK、几何可达性和“held 为 unknown 就拒绝 pick/place”等守卫不能直接作用于 ALFWorld 命令。本后端本地校验只验证冻结 schema、参数结构与公开命令语法；环境负责动作语义前提。这一校验职责变化必须进入 compatibility_delta，不能藏在通用 Runtime 分支中。

预算、错误计数、单步调度和终止守卫仍沿用原核心机制；不借移除桌面校验之机增加自动行动选择。

## 4. Observation / WorldState / Feedback / Evaluation 契约

### 4.1 四类信息

| 类型 | 定义 | 模型是否可见 |
|---|---|---|
| Observation | reset/step 返回的公开文本、观察引用与接收时间 | 是；保留原文 |
| WorldState | 从本次公开观察直接抽取的事实视图，不是全局真值 | 是 |
| Feedback | 上次命令、是否发出、公开返回消息、本地语法错误与有证据的结果 | 是，沿用现有历史窗口 |
| Evaluation | 官方分数、成功、内部任务进度、诊断标签与运行结果统计 | 否；生命周期只接收终止信号 |

所有事实必须能够回溯到公开原文片段或现有上下文来源。source 使用独立文本来源值；不能继续标记为 privileged。

### 4.2 WorldState 最小内容

在现有 contracts 扩展必要字段或后端变体，不新建知识图谱服务：

- state_version、observation_ref、source、raw_observation。
- 本次文本明确报告的位置／容器、对象标识、包含或支撑关系。
- 本次文本明确报告的 open/closed、on/off、clean/heated/cooled 等状态。
- 本次文本明确报告的 held/inventory 信息。
- 每个事实的证据引用；没有证据的字段为 unknown/null/not_applicable，三者语义必须区分。

无坐标时不填零向量；看不到物体不等于物体不存在；没有持有报告不等于空手；命令发出不等于效果已实现。

本次返回若只有“无效果”或其他局部消息，WorldState 仅含能从该消息确认的事实，不自动沿用上一轮为“当前事实”。先前事实仍可按 v0.1 已有近期反馈窗口出现，必须有历史来源。不得在 Adapter 内另存持久对象地图、访问集合、物品位置或完成清单。

### 4.3 无额外观察动作

Runtime 在技能前后获取状态时，只读取本次 reset/step 已返回的观察缓存，不额外执行 look 或 inventory。

- 模型显式请求 look/inventory 时，映射成一个环境动作并消耗 action budget。
- 反复读取同一缓存使用同一 observation_ref，不伪造新测量。
- 新 step 即使文本相同，也可有新观察版本；版本增加不证明获得了相关新信息。
- Adapter 可以缓存最近一次响应以实现接口读取，但不得积累更早事实；reset 清空所有 episode 局部状态。

### 4.4 解析规则

先完成原文透传，再实现必要确定性抽取。无法匹配的文本仍完整进入 Observation，并记录 parse_status=unparsed；不得丢弃、猜测补全或偷偷调用模型。

Action Adapter 使用 exact object reference，允许可逆的空白与格式规范化，不允许语义纠错。不能为了让模型通过而把数字自动换成另一个实例；参数字段沿用已冻结字符串类型时，在环境边界作明确字符串序列化。

### 4.5 信息白名单与禁止项

主实验只从 reset/step 的公开观察文本及必要技术标识生成模型输入。下列项目不进入 DecisionContext、GoalSpec、Feedback 或失败恢复分支：

- PDDL 初始完整状态、内部 facts、隐藏对象位置、目标谓词真值。
- expert_plan、expert trajectory、task solution、high-level demonstrations。
- reward、score、won、隐藏 subgoal completion、剩余目标答案。
- admissible_commands 或通过内部状态计算的动态合法动作列表。
- 任务类型标签、难度标签和供分析使用的隐藏元数据。

模型可见静态动作说明，包括普通使用前提，但不提供任务专用解法或专家例子。主实验不提供动态候选列表；记录为相对桌面版候选机制的差异。

评估进程可以读取官方评分及必要内部证据，用于终止后的事实核查；这些数据不得进入在线策略链。专家轨迹即使存在，也不用于本阶段模型提示、任务分解或难度分层。

### 4.6 Goal 与 Verification

GoalSpec 至少保留 benchmark 原始任务指令；桌面 assignments 等字段在本后端标为不适用，不用评分目标填充。

Verification 只转述公开执行证据及已有合法性检查：确认成功、确认失败或 unknown。不从一句笼统消息生成精细失败原因，不把 lack-of-evidence 判成成功。

当前缺少通用自然语言目标验证器是已知事实。本阶段不为 ALFWorld 新建此类推理模块，不以隐藏奖励填补空缺。

## 5. Task Protocol、数据与预算

### 5.1 数据使用

只使用固定版本官方 ALFWorld 游戏：

- train：开发与校验；默认每类 2 个，共 12 个不同任务，用于接口测试与预算预检。不用于训练模型。
- valid_unseen：主诊断集合。官方说明为 134 个游戏；Phase 0 实际枚举、校验数量与文件哈希。
- valid_seen：本阶段不运行，以避免扩大矩阵；不混入 unseen 分母。
- 不使用 ALFRED 私有测试服务器，也不把本地诊断称为官方排行榜成绩。

若固定版本的数量、类型或下载数据与文档不同，在任何在线筛查前修订并冻结任务清单；禁止默默补齐或删除失败任务。

使用官方原生 templated goal 模式，`goal_desc_human_anns_prob=0.0`、`domain_randomization=false`；确认固定版本支持这些配置。不在同一主批次混用语言来源。

### 5.2 任务选择、seed 与 repeat

- 选择种子：20260919；运行种子：repeat 0 为 20260919，repeat 1 为 20260920。
- 对每类任务按 SHA-256(`selection_seed|task_id`) 排序，从 valid_unseen 各取前 4 个作为筛查，共 24 个任务。
- 每个任务独立 reset，运行 2 次：**第一轮筛查共 48 episodes**。
- 正式全量：实际完整 valid_unseen 集合 × 2 次；若为 134 个，合计 **268 episodes**。
- 在配置与代码完全相同的情况下，48 个筛查 episode 计入 268 总数，不重跑后挑选较好结果；后续新增 220 集。
- 任一类型不足 4 个时，冻结前声明实际数，不从其他类替补。运行时随机数种子不改写官方任务初始状态；记录实际 task_id。
- 支持 seed 的 Python、NumPy、环境和模型接口分别设置并记录。模型不支持 seed 时明确写 unsupported，不声称 API 输出逐字可复现。
- 按 task_type、repeat 交错执行，固定洗牌顺序，降低时间漂移集中到某一类的风险。并发默认 1。

完整任务顺序在筛查前生成：前缀为 48 个筛查槽位，其后为剩余槽位；在两个区段内部按上述规则交错。进入全量时从既定清单续跑，不根据已见失败类型重新排序。

### 5.3 统一 episode 预算

以下为 BST-1.0 默认值，Phase 2 只能依据 train 预检调整一次并发布冻结配置；进入筛查后不得按结果改动。

| 项目 | 默认值 | 口径 |
|---|---:|---|
| 原生环境动作上限 | 60 | 每次 env.step，包括无效语义动作、look、inventory |
| 决策轮数上限 | 80 | 每次模型决策，包括 schema 无效、finish、blocked、clarify |
| HTTP 请求上限 | 90 | 全部模型请求、现有允许的修复与网络重试 |
| 单次生成 token 上限 | 2,048 | 使用 provider 对应参数；含可计量 reasoning token 的规则需记录 |
| 单 episode 输入＋输出 token 上限 | 600,000 | 重试、拒绝和目标相关请求均入账，不只统计成功调用 |
| 单 HTTP 超时 | 60 秒 | 继承现有有限重试机制；重试也消耗总预算 |
| 单环境命令超时 | 10 秒 | 超时后不可再次发出同一动作以猜测成功 |
| episode 墙钟上限 | 900 秒 | reset 前开始，包含模型等待及日志时间 |
| 环境启动／数据初始化 | 180 秒 | 单列 setup，不伪装为 Agent task failure |

若官方环境自带较小步数截断，必须在冻结前明确调整为一致上限，或报告实际较小上限并采用其口径；不得同时存在隐藏的 50 步与公开的 60 步预算。

不新增裁剪算法；沿用已有上下文构造。若 payload 超模型上下文限制，记录 CONTEXT_LIMIT 并终止，不删掉失败记录后继续。提供商 token 使用缺失时保存估计值与估算器版本；硬限制使用发起请求前的保守估计，并报告实际超额。

重复守卫阈值从完成版配置继承，必须写入冻结文件；若无法确定其实际行为，Phase 0 不通过。离线重复诊断不得使用 state_version 变化自动抵消无新证据的重复；若已有在线守卫存在该局限，应如实冻结并报告，不在本阶段修复决策收敛策略。

### 5.4 批次成本限制

预先记录模型输入／输出／缓存单价来源与日期；unknown 时不生成虚构美元费用。

固定实验配置必须有 `screening_token_cap`、`full_token_cap` 和可用时的 `cost_cap_usd`。默认 token 上限：筛查 8,000,000，全量累计 40,000,000。Phase 2 按 train 消耗预估是否可完成；不足时必须在筛查前缩小预声明样本或修订预算，不边跑边挑任务。

账本支持暂停与续跑。达到批次成本上限后在下一请求前停止，已完成数据保留，报告为 partial batch；不能把未运行的条目算成功、算 Agent 失败或从清单删除。

### 5.5 Frozen configuration 与可复现性

`manifest.json` 至少记录：

- 本规格版本、run_id、开始时间、任务顺序、task_id、repeat、所有 seed。
- 基线代码 commit、实际源码完整归档的 SHA-256、未提交及必要未跟踪文件；仅 dirty diff 哈希不够。
- 适配代码 commit/hash、完整 compatibility_delta、依赖锁定清单、Python/Java/OS 版本。
- ALFWorld/TextWorld 版本与源码 hash、游戏数据版本与文件 manifest。
- 请求 provider/model、实际返回 model/fingerprint（若可得）、temperature、top_p、生成限制、endpoint 标识；不保存密钥。
- 核心 prompt、环境说明、动作目录、schema、观察 parser 的原文与 hash。
- 反馈深度、attempt 规则、所有动作与预算参数、环境停止方式。
- 信息暴露白名单、admissible_commands=false、特权观察=false、跨 episode 记忆=false。
- 评估、分类标注规范、任务采样规则版本。

模型与 temperature 等优先继承完成版实际配置，缺失则 Phase 0 必须补明，不能猜测 temperature。若旧模型无法获得，必须标为“新被试上的 v0.1 架构诊断”，建立新 manifest；不沿用旧模型结论。

实际返回模型身份异常或中途变化时，暂停后续调度并单列已受影响数据；未返回身份信息时记录不可核验。不能根据请求别名断言底层模型相同。

公开任务可能被基础模型训练见过；seen/unseen 是 benchmark 场景划分，不证明模型训练未见。报告中必须说明这一限制。

## 6. Episode 生命周期与终止语义

### 6.1 一轮执行

1. 从 reset 或上个 step 取得原生公开响应，构造 Observation 和局部 WorldState。
2. 保存完整 DecisionContext，包括原始任务、静态技能目录、现有近期反馈与 attempts。
3. 请求一个 Decision，执行现有 schema 校验和预算检查。
4. execute 合法则发出一个原生命令；语义前提不满足通常由环境返回，不由隐藏合法动作筛选器代答。
5. 保存公开响应，确定性映射 Verification/Feedback；模型下一轮可以继续选择。
6. 同步保存独立评估信息，但不写入上下文或在线恢复分支。
7. 按下述固定终止规则结束，封存 episode。

不在模型调用期间推进环境，不插入隐式 look；不得通过清空状态或自动 reset 从失败中恢复。

### 6.2 finish、环境 done 与预算

终止优先记录真实来源：ENV_TERMINATED、AGENT_FINISH、AGENT_BLOCKED、NEEDS_CLARIFICATION、REPEAT_GUARD、BUDGET_EXHAUSTED、MODEL_ERROR、ENVIRONMENT_ERROR、ADAPTER_ERROR。

- 环境原生 done：生命周期调度器结束 episode。done 可能表示成功或截断，由独立评估分类；done 不作为下一轮输入。
- Agent finish：停止发送动作，封存后读取官方成功标识。若公开观察未证明成功，Agent 侧验证为 unknown，不提前伪造 accepted=true。
- 本后端**不将官方失败评分转换为 FINISH_REJECTED 再让模型继续**。这会把评分变成策略反馈 oracle。
- blocked/clarify：主实验无交互式人类解答，立即封存并按官方结果评估；blocked 不等于已证明任务不可满足。
- 预算／重复守卫：按冻结机制终止；同时记录底层触发原因和当时官方结果。
- 报告独立的 `official_success`、`termination_reason`、`false_finish`。只有 Agent 主动 finish 且官方不成功，才记 false_finish；环境 done 不是模型声称完成。

这是环境终止与验证接口的明确适配例外。不得声称桌面版 finish 拒绝回路逐字保持不变；也不得在主实验混用两种终止协议。

### 6.3 错误与续跑

模型 schema 错误是有效被试行为，保留并按原有机制反馈；不算基础设施故障。现有网络重试允许，但请求次数、消耗和失败均入账。

环境动作超时且无法判断是否执行时，终止为 infrastructure/indeterminate，不重放该动作。需要重跑时从同一任务 reset 建立新 attempt_id，保留原尝试和完整成本；不选择分数更高的尝试。

一次 infrastructure 失败最多允许一次替补运行，规则在冻结时固定。仅环境／网络／进程故障可替补；模型无效决策、blocked 和预算耗尽不得替补。

## 7. 第一轮实验矩阵

### 7.1 主矩阵

| 分层 | 官方 task type | 筛查独立任务 | repeat | 主要观察点 |
|---|---|---:|---:|---|
| 环境内参考组 | Pick & Place | 4 | 2 | 搜索、对象落地、基本搬运链 |
| 状态／设备条件 | Examine in Light | 4 | 2 | 物体持有、设备状态和前置条件 |
| 处理后放置 | Clean & Place | 4 | 2 | 清洁与放置顺序、效果证据 |
| 处理后放置 | Heat & Place | 4 | 2 | 设备使用与处理状态 |
| 处理后放置 | Cool & Place | 4 | 2 | 同类依赖在不同操作上的表现 |
| 数量与进展 | Pick Two & Place | 4 | 2 | 两个实例、数量要求、已完成项保持 |
| 合计 | 6 类 | 24 | 2 | 48 episodes |

“处理后放置”等只是分析分层，不给模型增加步骤说明。Pick & Place 也可能因搜索较难而比其他任务更长，因此不把类型排列成客观难度等级。

初始公开文本中的容器数量等可作为探索性协变量，必须记录定义。实际执行步数是结果，不用它事后定义“高复杂度组”再证明该组更差。没有可靠独立复杂度标签时，只报告任务类型与行为负担，不声称获得长程能力随复杂度的因果曲线。

### 7.2 与历史基线的关系

v0.1 原有桌面结果作为背景，保留旧产物引用，不与 ALFWorld 混算成功率。迁移后的 Pick & Place 是本环境内参考组，不是与旧桌面任务等价的 baseline。

不新增 one-shot、Memory、合法动作列表或其他模型实验臂。若后续归因需要干预对照，写入 v0.2 候选实验，不悄悄加入本批。

### 7.3 筛查到全量

筛查后允许修基础设施和适配错误，但修后需发布新版本并重跑全部 48 筛查条目；旧批次不得与新版本混合。

禁止根据验证失败改 prompt 策略、加入特例或修复 Agent 推理。发现此类需要时记录到报告，原冻结版本继续评价。

进入全量的条件是接口、账本、信息隔离和预算可用，不是成功率超过阈值。若主要问题是适配错误、模型身份异常或成本不足，停止扩展并输出不完整诊断说明。

## 8. Evaluation Design

### 8.1 结果与分母

必须区分：planned、started、benchmark-valid、infrastructure-failed、not-run、replaced attempts。

主报告同时给出：

1. **任务成功率**：官方成功数 / benchmark-valid episodes；模型错误、预算耗尽、blocked、clarify 均保留在此分母。
2. **运行可靠性**：基础设施成功完成记录的比例、替补次数及其成本。
3. **保守交付成功率**：成功数 / 已启动的预定 episode 槽位；不可恢复基础设施故障记为未交付成功，明确其不等于 Agent 推理失败。
4. **覆盖率**：已完成预定槽位 / 全部计划槽位；未运行项不混入 Agent 失败率。

每个预定槽位最多一个正式成绩；原始基础设施尝试保留在可靠性与成本账本中。不得将全部尝试当成独立任务样本。

### 8.2 指标

| 维度 | 指标与定义 |
|---|---|
| Task performance | overall/type-wise official success；两次重复都成功、都失败、分歧的任务比例 |
| Partial completion | 仅当固定环境提供明确可解释的独立部分进度时记录并说明语义；否则 null/not_supported。禁止将二值 reward、模型自述或通过动作数当作完成比例 |
| Constraint violation | 官方任务约束违反（有对应证据时）；无独立约束信号则 not_supported。动作前提拒绝另列，不能冒充最终约束违反 |
| Efficiency | env.step 次数、有效执行／被环境拒绝次数、decision rounds、全部 HTTP 请求、输入/输出/缓存 token、墙钟、模型等待时间、环境执行时间 |
| Repetition | 相同命令的连续失败长度、无新相关证据的重复候选数、最长循环长度、涉及重复的 episode 比例 |
| Invalid action | schema-invalid / 决策数；syntax-invalid / execute 提议数；环境前置条件拒绝 / 已发出命令数，分开报告 |
| Progress retention | 有明确证据的已满足条件被破坏、遗漏剩余数量／条件；可靠自动标签缺失时由人工标注，不捏造连续进度曲线 |
| Adaptation | 失败后是否改变对象／动作／参数或主动取得新信息；改变是否合理需证据复核，不把任何动作变化都算恢复 |
| Termination | false_finish、blocked、clarify、预算类型、重复守卫、基础设施中止；环境完成与 Agent 自述分开 |

成本与耗时报告均值、中位数、p95，并分成功／失败和 task type 展示。成功任务成本不能代表整体成本。

不使用当前恒为零的 replan_events 评价重规划。程序未显式生成计划时，使用“行动调整”而非“计划修订次数”。

### 8.3 重复与信息使用判据

自动统计的相同命令使用精确动作与对象参数，不只比较技能名。连续失败只有在公开反馈表明失败或无效果时计数；不能因文字类似就确定世界未变。

自动规则只标记“疑似无进展重复”。人工结合公开前后证据确认是否有相关新信息；仅 state_version 或时间变化不算新证据。排除任务合理要求的重复操作，不仅凭命令相同判错。

信息使用必须区分：

- I0：从未提供／没有观察到。
- I1：完整轨迹曾提供，但当前输入已因冻结窗口不再包含。
- I2：当前 DecisionContext 仍包含明确证据。
- I3：当前包含冲突或陈旧信息，无法确定正确事实。

这四类是人工归因证据，不是提供给 Agent 的新增记忆。

### 8.4 不确定性与统计

筛查每类仅 4 个独立任务，主要用于发现案例，不作强显著性声明。

全量报告以 task_id 为聚类单位，先对每个任务的两次 repeat 求平均，再做 2,000 次 cluster bootstrap，seed=20260919，给出 95% 区间。总体微平均和六类宏平均分开；不得把 268 episodes 视为 268 个独立任务。

失败类型比较是探索性分析。若只标注样本，报告样本分母与选择规则，不外推“全部失败中有 X% 是 Memory 问题”。

## 9. Failure Taxonomy 与判定规则

### 9.1 三层记录

每个失败记录：

1. outcome：官方未成功、模型错误、预算耗尽等结果。
2. observed_pattern：具体可观察行为，允许多个标签。
3. possible_cause：有证据支持的解释，confidence 为 confirmed / supported / uncertain；unknown 允许存在。

标记首次偏离的轮次、后续放大因素；不强迫所有失败只有一个原因。模型 rationale 只是辅助证据，不单独证明理解、遗忘或因果机制。

### 9.2 分类表

| 标签 | 判定依据 | 不得据此推断 |
|---|---|---|
| TASK_UNDERSTANDING | 原始指令明确的对象、数量或属性被持续误用；保留指令与动作对照 | 一次选错对象不一定是理解问题，也可能是 grounding |
| PLANNING_ORDER | 信息与动作足够，但行动顺序阻碍目标，存在可核查的合法替代顺序 | 与专家路径不同不等于错 |
| DEPENDENCY | 已可知的必要前置条件未满足，继续发出依赖动作；可与 planning 共标 | 环境未透露前提时不能默认模型已经知道 |
| STATE_USAGE | I2 情况下，动作与明确当前事实冲突 | 不直接归因视觉感知或需要 VLM |
| HISTORY_UNAVAILABLE | I1 情况下重犯错误或丢失任务进展 | 只能提出历史保留假设，不能证明新增 Memory 有效 |
| STATE_AMBIGUOUS | I3，证据过期或冲突，行动缺少必要确认 | 不把 Adapter 错误更新算模型遗忘 |
| REPEATED_FAILURE | 在无相关新证据时重复同一失败方案，经轨迹复核 | 不等于跨 episode 学习不足 |
| INVALID_ACTION | schema、动作名、参数或对象引用不符合公开接口 | 与正确调用后的执行失败分开 |
| PROGRESS_LOSS | 明确完成条件被后续行动破坏，未恢复或造成任务失败 | 必要中间退让不能自动算无效返工 |
| ACTION_COVERAGE_GAP | 原生环境支持所需动作，但 Adapter 未暴露或错误限制 | 首先是接入缺陷，不是 Agent 自主技能学习需求 |
| SKILL_LIMITATION | 已完整对接原生动作，但某必要行为确实不能表达；需独立证据 | 标准 ALFWorld 通常已有可解动作，不能仅凭低分认定 |
| OBSERVATION_LIMITATION | 必要事实 I0，需搜索或显式观察才能获得 | 不能直接推出应上 VLM；可能是信息获取策略问题 |
| EXECUTION_FAILURE | 参数与公开前提正确、动作已发出，但环境未产生预期效果 | 文本前置条件拒绝不是机器人控制失败 |
| BUDGET_LIMIT | 轮数／动作／token／时间截断，记录此前行为 | 是终止结果，不能代替根因 |
| ADAPTER_ERROR | 翻译、解析、缓存、参数或信息传递错误，有原始 I/O 证据 | 不计为模型规划短板 |
| INFRASTRUCTURE | API 不可用、环境崩溃、不可判定超时或数据损坏 | 不通过删除记录提高成功率 |
| UNRESOLVED | 证据不足，多个解释无法区分 | 不强行归为 Memory / Planning |

### 9.3 人工判定最低证据

每条归因必须包含 episode_id、decision_id、原始指令、相关上下文引用、实际命令、原始响应、判定理由、反例或替代解释。

若声称存在更好的合法行动，必须给出接口规则或公开状态依据；评审员的常识猜测不是可执行性证明。可使用冻结环境的离线重放核查，但这种接管轨迹单独标为 diagnostic，不计入主成绩、不得回流改变主实验策略。

## 10. Failure Analysis Pipeline 与产物

### 10.1 完整流程

```text
预声明任务清单
   -> 执行 episode，保存逐轮原始证据
   -> 校验账本与官方结果一致性
   -> 自动统计与候选事件标记
   -> 人工查看关键决策上下文
   -> taxonomy 标签与证据等级
   -> 第二次复核／分歧记录
   -> Bottleneck Report
   -> v0.2 候选研究问题与所需对照
```

复用 EpisodeStore/events/model_calls 链路，必要时补字段；不得把离线分析结果注回 Agent。

### 10.2 轨迹最小字段

- episode_id、task_id、task_type（仅评估）、repeat、attempt_id、seed。
- instruction、observation 原文及引用、解析状态与解析后事实。
- 发给模型的完整脱敏 DecisionContext、prompt/schema hash。
- 模型原始输出、解析后 Decision、拒绝原因、真实返回模型、usage、latency。
- 提议动作、实际环境命令、是否发送、发送一次性的标识。
- 公开环境响应、Feedback、已有 attempts 与预算前后值。
- 独立保存的官方 score/won/done/termination/truncation 信息及来源。
- episode summary、覆盖与成本账本、人工标签和引用。

不记录 API key、认证头或完整环境变量。完整模型请求与响应应可用于离线核查，不以日志摘要替代。

### 10.3 人工分析规模

- 筛查：查看全部未成功 episode；另外每个类型按固定哈希顺序抽至少 1 个成功 episode（存在时）。
- 全量：对每个失败 task_id 至少查看一条失败轨迹；两次结果分歧时，成对查看成功与失败轨迹。全部 false_finish、适配错误、最长重复案例必须查看。
- 其余同任务重复失败可只自动标记，但标注覆盖率必须报告。没有逐集复核的标签不得伪装成全量人工比例。
- 关键结论所引用案例和至少 20% 的人工标注记录进行第二次复核。优先不同评审者；只有一位评审者时，做间隔复核并明确不能估计评审者间一致性。
- 保存分歧及最终裁定，不让自动 LLM 分类成为唯一真值。

### 10.4 建议产物布局

以下是待实现的输出契约，不表示当前已存在命令或文件：

```text
<run_root>/
  manifest.json
  frozen_config.json
  task_manifest.json
  compatibility_delta.md
  environment_check.json
  episodes/<episode_id>/
    events.jsonl
    model_calls.jsonl
    evaluation.jsonl
    episode_summary.json
  metrics.json
  metrics_by_task_type.csv
  failure_candidates.jsonl
  annotations.jsonl
  annotation_guidelines.md
  Bottleneck-Report.md
  v0.2-Decision-Evidence.md
```

日志中保留现有格式即可，不要求重命名所有旧产物。报告必须能由封存日志重算，不依赖再调用模型或再运行环境。

### 10.5 Bottleneck Report 必须包含

1. 实际被试、数据、预算、信息与动作条件，所有偏离及未完成项。
2. 成功、部分进度可用性、成本、终止、覆盖和基础设施可靠性。
3. 分任务类型结果与不确定性，不把固定类型称为严格难度等级。
4. Failure Pattern -> Evidence -> Possible Cause -> Competing Explanation。
5. 首次偏离与后续放大因素；按独立 task_id 统计的频率。
6. 代表性成功、失败、重复分歧和无法归因案例。
7. 高频问题、昂贵问题、严重但罕见问题分别排序，不合成未经验证的总分。
8. 未测试能力：真实视觉、运动控制、外部扰动恢复、跨任务学习等。
9. v0.2 候选问题、必要对照、预期验证方式及暂不选择的方向。

## 11. v0.2 决策规则

### 11.1 证据映射

| 观察到的模式 | 可以考虑的方向 | 进入该方向前需要排除／验证 |
|---|---|---|
| 当前输入已有明确事实，仍反复忽略 | State representation / reasoning | Parser 丢失、冲突事实、错误动作命名；不是先增加历史长度 |
| 历史事实曾可见，离开窗口后出现错误 | Task Working Memory | 与同类 I2 错误区分；后续需“同信息、不同保留方式”对照 |
| 前置条件或顺序反复处理错误 | Task decomposition / dependency management | 任务规则已提供、动作接口完整；不能仅以偏离示范判错 |
| 当轮失败证据与 attempts 已存在，仍重复 | Failure feedback use / decision convergence | 相关新证据是否真的缺失；不直接归为跨集记忆 |
| 相似任务不断从头摸索 | Experience Memory 的候选实验 | 本阶段没有记忆对照；不能声称经验存储必然改善 |
| 所需原生命令未被暴露 | Skill interface / adapter completeness | 先修迁移缺陷并重新诊断，不叫 Skill Acquisition |
| 完整接口仍不能表达必要行为 | Skill expansion / acquisition | 独立证明能力缺口；标准文本任务不能证明新运动策略需求 |
| 需要信息但没有主动获取 | Observation policy / active information gathering | look/inventory/search 是否已开放，观察是否被错误裁剪 |
| 主要失败在物理控制 | Skill / Controller | ALFWorld 文本主实验不能提供该结论；需未来物理环境证据 |
| 主要成本来自反复决策而任务成功 | 决策粒度、程序组合等候选 | 先量化成本与成功收益，不能预先实现宏动作 |

### 11.2 选择规则

v0.2 候选优先级依据：跨独立任务重复性、影响的失败／成本份额、归因证据强度、是否属于 Agent 层、在现有资源下能否用最小对照检验。

某模式只有在至少 3 个独立 task_id 有 supported/confirmed 证据时，才称为“重复出现的瓶颈”；低于该数量作为案例或风险，不忽略但不宣称普遍性。跨两个任务类型出现增强其广度证据，但特定类型瓶颈也可成立。

研究选择不自动化：报告最多提出 2 个优先候选和 1 个保留候选，每个写清证据、竞争解释、最小后续实验。若高频问题主要来自 Adapter 或基础设施，结论应是接入诊断尚不充分，而不是强行进入 v0.2。

没有 Memory 对照就不能宣布 Memory 有效；没有动作覆盖证据就不能宣布必须技能学习；没有视觉实验就不能宣布必须引入 VLM。

## 12. Implementation Plan

### Phase 0：调研核验与环境安装

- **目标**：确定真实完成版被试、环境可运行性和资源成本。
- **输入**：v0.1 完成代码与冻结产物、完成报告、本规格、官方仓库。
- **工作**：核查实际 Runtime/contract/provider；归档包含必要未提交文件的源码；建立隔离 Python 环境；固定 ALFWorld/TextWorld 与数据版本，只安装文本所需依赖；核验六类与划分；运行官方文本示例。
- **输出**：环境版本清单、task manifest 草案、baseline inventory、compatibility_delta 草案、资源与许可证说明。
- **完成标准**：CPU 下连续 reset/step/close 无基础设施错误；六类可枚举；实际模型配置与旧代码行为可追溯。无法确认完成版代码或模型身份时，不进入冻结实验。

### Phase 1：Adapter prototype

- **目标**：接通同一决策链，不增加解决任务的策略。
- **输入**：Phase 0 环境与契约清单。
- **工作**：原文透传、必要结构化事实、静态动作目录、单命令映射、原生终止、评估隔离与日志；处理第 3.4 节明确差异。
- **输出**：最小适配实现、白名单、兼容性差异、确定性接口测试。
- **完成标准**：一个合法 execute 恰好产生一次 step；无效输出不产生替代动作；不存在额外 look、历史地图、专家信息或评分回流。

### Phase 2：Single-task validation 与六类开发校验

- **目标**：先证明一条真实调用链，再确认各动作类型没有接入盲区。
- **输入**：Adapter、train 12 个固定任务、真实 API 配置。
- **工作**：先在一个 train Pick & Place 任务验证完整闭环；再在每类两个 train 任务检查输入、命令、反馈、停止和预算。成功与否不作为适配正确性的唯一标准。人工合法命令序列仅用于环境检查，存为 dev-only，不进入 prompt。
- **输出**：开发轨迹、预算成本估计、终止测试结果、最终 compatibility_delta、冻结 manifest 与配置。
- **完成标准**：六类接口语义和评估一致；至少一个正常成功终止与一个失败／截断终止可核验；隐藏信息隔离测试通过。只允许修接入错误，禁止写任务特例以凑成功率。

### Phase 3：Benchmark evaluation

- **目标**：先低成本定位失败区域，再完成单 benchmark 全量评价。
- **输入**：冻结配置、任务清单、成本上限。
- **工作**：先运行 48 集筛查，审查运行质量；满足第 7.3 节条件后扩展到全量 268 集（实际数量以清单为准）。复用相同配置已完成槽位。中途只允许暂停／续跑与预定义 infrastructure 替补。
- **输出**：封存轨迹、完整账本、机器统计、筛查／全量覆盖报告。
- **完成标准**：正式清单每个槽位有结果或明确未运行原因；无静默漏项、模型混用或评分泄漏；全量未完成时必须标 partial，不能称为 full evaluation。

### Phase 4：Failure analysis

- **目标**：将失败现象转成有证据、有不确定性的瓶颈描述。
- **输入**：封存日志、官方结果、taxonomy。
- **工作**：自动候选标记、按第 10.3 节人工查看、复核、首次偏离与放大因素分析、task_id 聚类统计。
- **输出**：annotations、Bottleneck Report、覆盖率与未解决归因列表。
- **完成标准**：主要结论都有可定位的上下文／动作／反馈证据；适配失败与 Agent 失败分开；评审覆盖率、分歧及未知均公开。无需任何成功率门槛。

### Phase 5：v0.2 proposal

- **目标**：用实验结果提出下一阶段待检验问题。
- **输入**：Bottleneck Report 与资源条件。
- **工作**：按第 11 节比较候选方向，提出最小对照实验及不选其他方向的原因。
- **输出**：`v0.2-Decision-Evidence.md`，不自动输出或实现 v0.2 SPEC。
- **完成标准**：每个建议对应证据、替代解释和验证路径；由后续研究讨论确定方向。证据不足时明确建议补诊断，而不是默认建设 Memory。

### 12.1 当前代码接续位置

以下是完成报告中已有的文件职责与预计接续点，路径相对于首页目标仓库；Phase 0 核查实际文件后更新映射。它们不是本次已经完成的改动。

| 已有位置 | 本阶段允许的工作 |
|---|---|
| `embodied_agent/core/contracts.py` | 原始任务文本、局部文本 WorldState 及不适用字段的最小兼容；保留 Decision 主结构 |
| `embodied_agent/core/runtime.py` | 接入后端的观察缓存、执行与终止；保留现有决策调用和历史窗口，记录环境例外 |
| `embodied_agent/core/skills.py` | 保留旧桌面执行；在环境边界选择静态原生命令目录和一对一映射，不增加恢复策略 |
| `embodied_agent/adapters/deepseek.py` | 复用现有网络与 Decision 请求；只替换必需的环境说明，记录真实模型身份 |
| `embodied_agent/core/events.py` | 复用事件和模型调用账本；补公开观察、原生命令、独立评估引用 |
| `embodied_agent/evaluation/run.py` | 数据划分、task_id/repeat 调度、暂停续跑与预算；不另建智能 Agent |
| `embodied_agent/evaluation/evaluator.py`、`report.py` | 对接官方结果、分母、成本与 taxonomy 候选统计；不回流策略链 |
| `embodied_agent/cli.py` | 在现有 CLI 上提供 benchmark 运行、续跑、离线聚合、校验冻结配置的入口 |

具体函数名与 CLI 命令以实现时现有接口为准；本规格不冒称已经存在 ALFWorld 命令。实现交付的运行说明必须给出真实可执行命令：环境自检、12 集开发校验、48 集筛查、全量续跑、离线聚合，各命令明确配置文件和输出目录。

## 13. 最小验证与阶段验收

仅为关键语义与信息边界编写必要测试，不要求为每个报告字段创建镜像测试。

实现必须验证：

1. **动作不被代做**：开柜、导航等前提缺失时，Adapter 不自动补齐；未知对象不被替换。
2. **一步一动作**：调用计数覆盖成功、语义失败、look/inventory；读取观测缓存不触发 step。
3. **信息隔离**：向 evaluation-only 字段加入可识别标记，构造的模型输入、Feedback、重试分支不得包含该标记。
4. **无自动 Memory**：对“看见对象 -> 离开 -> 只得到局部消息”序列，Adapter 不把旧位置当当前事实；既有窗口以外不保留新建的实体地图。
5. **unknown 保真**：未观测、空、false、not_applicable 不混用；未解析文本完整保留。
6. **终止不泄漏**：Agent finish 后只判分不再引导下一步；官方 done 与模型 finish 分开记录。
7. **账本完整**：schema 错误、网络重试、环境失败、超时及重复守卫均有记录；预算边界无漏计。
8. **隔离回归**：原桌面后端的必要契约与核心决策回归检查仍通过；不因新后端改变旧实验语义。
9. **离线可重算**：不重新请求模型或环境，即可从封存产物重算主要指标和分母。

阶段验收以诊断可信度为核心：实现可追溯、公开任务未被改写、被试机制冻结、信息边界明确、归因有证据。成功率低本身不构成验收失败。

## 14. 文献与官方实现依据

以下资料于 2026-09-19 的设计讨论中核查；实现时记录所采用的具体版本，不能把网页最新状态当成环境锁文件。

1. ALFWorld 官方代码、文本／视觉安装区别与接口：https://github.com/alfworld/alfworld
2. ALFWorld 数据与划分：https://github.com/alfworld/alfworld/tree/master/alfworld/data
3. ALFWorld 六类任务与默认配置：https://github.com/alfworld/alfworld/blob/master/configs/base_config.yaml
4. ScienceWorld 官方代码、Java/Python 依赖与任务：https://github.com/allenai/ScienceWorld
5. VirtualHome 官方代码与 Evolving Graph：https://github.com/xavierpuigf/virtualhome
6. ALFRED 官方代码、输入限制与评测规则：https://github.com/askforalfred/alfred
7. CALVIN 动作空间与评测：https://github.com/mees/calvin
8. ManiSkill 官方系统支持表：https://github.com/haosulab/ManiSkill
9. RLBench 官方接口：https://github.com/stepjam/RLBench
10. Habitat-Lab：https://github.com/facebookresearch/habitat-lab
11. BEHAVIOR-100 版本说明：https://behavior.stanford.edu/behavior_100/overview.html
12. BEHAVIOR-1K 安装与硬件要求：https://behavior.stanford.edu/getting_started/installation.html
13. MineDojo 官方动作与任务说明：https://github.com/MineDojo/MineDojo

项目证据：用户提供的《v0.1 完成情况报告（用于讨论 v0.2）》及其指向的冻结产物。实际实现以 Phase 0 核查后的完整源码快照为准。

**执行原则：先测清楚 Agent 为什么失败，再决定 Agent 如何进化。**
