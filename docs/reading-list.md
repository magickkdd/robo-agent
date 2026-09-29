# Reading List — 具身智能 / 持续决策 / Agent

项目关键词对应的三条学术脉络(决策经典、LLM Agent、具身 Agent)的书单,
带 arXiv 链接与**读后钩子**——每篇读时带着的问题,以及读完该回头对照本项目的什么。

用法:★ = 必读;§0 是时间紧张时的十篇顺序;附录把项目每个模块的学术出处排成一张表,
用于回答"你这个东西前人做到哪了"。

---

## 0. 只读十篇的顺序

1. ★ ReAct — agent 循环的通用范式
2. ★ Reflexion — 语言反思代替梯度,自进化线的思想源头
3. ★ Voyager — 开放世界技能库(ASPIRE 技能库的思想前身)
4. ★ SayCan — LLM 决策接地上机器人
5. ★ Code as Policies — ASPIRE 的 CaP-X 直系祖先,复现前必读
6. RT-2 或 OpenVLA(二选一)— VLA 概念与开源标准
7. ★ LIBERO — 长时程终身学习基准(ASPIRE 的 LIBERO-Pro 底稿)
8. ★ Kaelbling 1998 — POMDP:看不全状态还必须连续决策
9. ★ Sutton & Barto(工具书,选读)— 决策词汇的来源
10. ★ ASPIRE 全文(重读)— 复现对象

---

## 一、决策与持续性的经典地基

### ★ Sutton & Barto,《Reinforcement Learning: An Introduction》(2nd ed.)
MIT Press 2018。免费全文:[incompleteideas.net/book](http://incompleteideas.net/book/the-book-2nd.html)
- 为什么读:预算、回报、策略、探索-利用——运行时里"预算与停止规则"的词汇全从这里来。
- 读后钩子:我们的 `run_episode` 预算参数(mode、轮数、耗时)对应书里哪几节的形式化?

### ★ Kaelbling, Littman & Cassandra,"Planning and Acting in Partially Observable Stochastic Domains"
AIJ 1998(POMDP)。无 arXiv,[DOI](https://doi.org/10.1016/S0004-3702(98)00023-1)
- 为什么读:agent 看不全状态还必须连续决策,就是我们每天处理的问题的正式定义。
- 读后钩子:义务账本里"相信什么"的更新,对应 belief-state 更新的哪一种?我们的 `perception`
  事件是观测,`working_memory` 事件是 belief——缺的是哪一块?

### ★ Sutton, Precup & Singh,"Between MDPs and semi-MDPs: The Options Framework"
AIJ 1999。无 arXiv,[DOI](https://doi.org/10.1016/S0004-3702(99)00052-1)
- 为什么读:"技能 = 可复用的带终止条件的动作序列"这个概念的根。
- 读后钩子:我们的 `SkillSpec`(primitive/composite/acquired 三级)与 option 的半马尔可夫性
  差在哪?准入门要求"跨实例泛化"对应 option 的什么性质?

### Haarnoja et al.,"Soft Actor-Critic"
ICML 2018,[arXiv:1801.01290](https://arxiv.org/abs/1801.01290)
- 为什么读:连续控制的标准算法,MetaWorld 的基线;复现 ASPIRE 时会反复遇到。
- 读后钩子:我们的 MuJoCo 通道是决策层(技能选择)而非力矩层——它和 SAC 的控制层怎么分工?

### Silver et al.,"AlphaZero" / Schrittwieser et al.,"MuZero"
Science 2018 / Nature 2020,[arXiv:1712.01815](https://arxiv.org/abs/1712.01815) / [arXiv:1911.08265](https://arxiv.org/abs/1911.08265)
- 为什么读:"规划即决策"的极致参照,读方法框架即可。
- 读后钩子:MuZero 用学到的模型前瞻;我们的事件日志(schema v4)能否支撑同类"回放式前瞻"?

### ★ Kirkpatrick et al.,"Overcoming Catastrophic Forgetting in Neural Networks"(EWC)
PNAS 2017,[arXiv:1612.00796](https://arxiv.org/abs/1612.00796)
- 为什么读:"持续"的代价——灾难性遗忘;经验库为什么需要准入和生命周期管理,根在这。
- 读后钩子:我们的库是 append-only(无淘汰)。EWC 的视角下,append-only 的库会以什么方式"遗忘"?

### Khetarpal et al.,"Towards Continual Reinforcement Learning: A Review and Perspectives"
JMLR 2022,[arXiv:2007.04954](https://arxiv.org/abs/2007.04954)
- 为什么读:持续 RL 的正式框架:任务序列、迁移、遗忘的分类学,"持续决策"的学术坐标。
- 读后钩子:我们的三通道任务序列属于综述里的哪一类设定?"经验层负迁移"落在它的哪一格里?

---

## 二、LLM Agent 经典(2022–2023)

### ★ Wei et al.,"Chain-of-Thought Prompting Elicits Reasoning in Large Language Models"
NeurIPS 2022,[arXiv:2201.11903](https://arxiv.org/abs/2201.11903)
- 为什么读:一切的起点:让模型先想后答。
- 读后钩子:我们的 `decision` 事件存 rationale——它等于 CoT 吗?(答:CoT 是自由文本,
  我们的 rationale 事后要被 `mark_used` 拿来和执行动作对账。)

### ★ Yao et al.,"ReAct: Synergizing Reasoning and Acting in Language Models"
ICLR 2023,[arXiv:2210.03629](https://arxiv.org/abs/2210.03629)
- 为什么读:想→动→看→再想的循环范式,我们运行时循环的直系祖先。
- 读后钩子:ReAct 的循环里没有"验证"这一步——我们的循环在 ReAct 之上多出的每一环
  (验证、义务、事件)分别堵的什么洞?

### ★ Shinn et al.,"Reflexion: Language Agents with Verbal Reinforcement Learning"
NeurIPS 2023,[arXiv:2303.11366](https://arxiv.org/abs/2303.11366)
- 为什么读:用语言反思代替梯度更新——EmbodiSkill 的反思机制是它的后代。
- 读后钩子:Reflexion 把反思存进 episodic memory,但**怎么防坏反思污染后续**?对照我们
  `memory_write` 的字段(矛盾守卫在检索时跑,准入在写入时跑)。

### Yao et al.,"Tree of Thoughts: Deliberate Problem Solving with Large Language Models"
NeurIPS 2023,[arXiv:2305.10601](https://arxiv.org/abs/2305.10601)
- 为什么读:把搜索引入 LLM 决策;我们规划器的多候选结构可对标。
- 读后钩子:ToT 的候选由 LLM 自评;我们的候选由验证器评——评估者的差别意味着什么?

### ★ Park et al.,"Generative Agents: Interactive Simulacra of Human Behavior"
UIST 2023,[arXiv:2304.03442](https://arxiv.org/abs/2304.03442)
- 为什么读:记忆流(存储-检索-反思-计划)的原始设计;我们经验库的存储-检索结构可对照。
- 读后钩子:它的检索按 recency×importance×relevance 打分;我们的 `retrieve` 按谓词匹配分
  (`episodic/retrieval.py`)——各自会在什么任务上占优?

### ★ Wang et al.,"Voyager: An Open-Ended Embodied Agent with Large Language Models"
TMLR 2023,[arXiv:2305.16291](https://arxiv.org/abs/2305.16291)
- 为什么读:开放世界里的技能库:自动写技能代码、入库、复用。ASPIRE 技能库的思想前身。
- 读后钩子:Voyager 的入库标准是"程序可执行+自我验证通过"——**没有跨实例门**。
  把我们 0/20 的准入门装到 Voyager 上,Minecraft 里它还会涨吗?

### Schick et al.,"Toolformer: Language Models Can Teach Themselves to Use Tools"
NeurIPS 2023,[arXiv:2302.04761](https://arxiv.org/abs/2302.04761)
- 为什么读:模型自学会调工具;agent 与环境交互的另一条根。
- 读后钩子:Toolformer 的 API 调用是无状态文本;我们的 `SkillCall` 带物理参数与验证——
  从文本 API 到物理动作,多了哪些失败模式?

---

## 三、具身 Agent 经典(ASPIRE 的直系谱系)

### ★ Ahn et al.,"Do As I Can, Not As I Say"(SayCan)
CoRL 2022,[arXiv:2204.01691](https://arxiv.org/abs/2204.01691)
- 为什么读:第一次把 LLM 决策"接地"到机器人可供性上,具身 agent 开山。
- 读后钩子:SayCan 用 value function 给 LLM 的提议打"可行性分";我们的三层验证是
  事后判——事前打分与事后验证各挡住什么、漏掉什么?

### Huang et al.,"Inner Monologue: Embodied Reasoning through Planning with Language Models"
CoRL 2022,[arXiv:2207.05608](https://arxiv.org/abs/2207.05608)
- 为什么读:闭环反馈进 LLM 规划;我们"感知→验证→重规划"回路的直系祖先。
- 读后钩子:它的反馈是成功/失败检测器的字符串;我们的反馈是 15 类事件——
  把检测器输出升级为结构化日志,换来了什么可测性?

### ★ Liang et al.,"Code as Policies: Language Model Programs for Robot Control"
ICRA 2023,[arXiv:2209.07753](https://arxiv.org/abs/2209.07753)
- 为什么读:让 LLM 写策略代码。**ASPIRE 的 CaP-X 就是这条线的直系后代,复现前必读。**
- 读后钩子:CaP 生成的代码错了怎么办?(答:它没有闭环验证。)对照 ASPIRE 加上的
  诊断→修复→验证循环,记住"祖先缺什么"。

### Zeng et al.,"VoxPoser: Composable 3D Value Maps for Robotic Manipulation"
CoRL 2023,[arXiv:2301.12411](https://arxiv.org/abs/2301.12411)
- 为什么读:LLM 组合 3D 值地图做操作,code-as-policy 的另一代表作。
- 读后钩子:VoxPoser 的产物是值地图(连续量),CaP 的产物是代码(符号量)——
  我们的 `SkillSpec` 是参数化程序,介于两者之间的哪一点?

### Brohan et al.,"RT-2: Vision-Language-Action Models"
CoRL 2023,[arXiv:2307.15818](https://arxiv.org/abs/2307.15818)
- 为什么读:VLA 概念的确立:视觉-语言-动作一个模型。
- 读后钩子:RT-2 端到端出动作;我们是 VLM 出决策、执行器出动作——两种架构的
  "决策可追溯性"差多少?(我们每轮有 `decision` 事件,RT-2 的决策藏在权重里。)

### Driess et al.,"PaLM-E: An Embodied Multimodal Language Model"
ICML 2023,[arXiv:2303.03378](https://arxiv.org/abs/2303.03378)
- 为什么读:多模态大模型做具身推理的第一篇重磅。
- 读后钩子:PaLM-E 把观测直接塞进上下文;我们把观测写成 `perception` 事件再进上下文——
  哪种在长 episode 里先爆上下文?

### Open X-Embodiment Collaboration,"Open X-Embodiment: Robotic Learning Datasets and RT-X Models"
ICRA 2024,[arXiv:2310.08864](https://arxiv.org/abs/2310.08864)
- 为什么读:"跨 embodiment"这个说法的出处;ASPIRE 声称技能跨本体持久的对照系。
- 读后钩子:数据层面的跨本体迁移 vs 我们关心的经验层面的跨任务迁移——
  迁移的单位不同(数据 vs 技能条目),负迁移的表现也不同。

### ★ Kim et al.,"OpenVLA: An Open-Source Vision-Language-Action Model"
CoRL 2024,[arXiv:2406.09246](https://arxiv.org/abs/2406.09246)
- 为什么读:开源 VLA 事实标准——ASPIRE/Zetta 语境里的"冻结 VLA 底座"多指这类。
- 读后钩子:本地 4GB 显存跑不动 7B VLA——复现 Zetta 路线时的算力墙在哪,替代路径是什么?

### Black et al.,"π0: A Vision-Language-Action Flow Model for General Robot Control"
2024(Physical Intelligence),[arXiv:2410.24164](https://arxiv.org/abs/2410.24164)
- 为什么读:流匹配 VLA,当前机器人底座的风向标。
- 读后钩子:π0 的动作块是 50Hz 连续段;我们的技能是离散调用——两种时间粒度的
  "决策轮"定义差在哪?事件日志各自怎么记?

### 前沿工业:Figure "Helix" / NVIDIA "GR00T N1"
2025。[Helix(技术报告,无 arXiv)](https://www.figure.ai/news/helix) /
[GR00T N1, arXiv:2503.14734](https://arxiv.org/abs/2503.14734)
- 为什么读:人形机器人 VLA 的工业前沿,了解双系统(快动作+慢推理)走向即可。
- 读后钩子:双系统架构里"慢系统"是规划器——和我们的 VLM 决策层同构吗?

---

## 四、基准线(在用的与长时程相关的)

### ★ Yu et al.,"Meta-World: A Benchmark and Evaluation for Multi-Task and Meta-RL"
CoRL 2019,[arXiv:1910.11697](https://arxiv.org/abs/1910.11697)
- 为什么读:我们 MuJoCo 通道的基准本体。
- 读后钩子:它的 50 个任务按任务族分组——我们的消融臂读数若按任务族切,
  会在哪一族上看到记忆机制的最大差异?(这是经验层负迁移实验的任务表来源。)

### ★ Shridhar et al.,"ALFWorld: Aligning Text and Embodied Environments for Interactive Learning"
ICLR 2021,[arXiv:2010.03768](https://arxiv.org/abs/2010.03768)
- 为什么读:我们文本通道的基准本体;文本-具身对齐的代表设计。
- 读后钩子:EmbodiSkill 在 ALFWorld 上报 ~93%——我们文本通道的 §5.4 条件接线(E5)
  完成后,同场对照是最便宜的第一个实验。

### Mees et al.,"CALVIN: A Benchmark for Language-Conditioned Policy Learning for Long-Horizon Robot Manipulation"
RA-L 2022,[arXiv:2112.03227](https://arxiv.org/abs/2112.03227)
- 为什么读:长时程语言条件操作的标杆,链式任务的评测协议(A-B-C-D 连锁)值得抄。
- 读后钩子:CALVIN 的连锁评测口径(连续完成 5 段的概率)能否借来评我们的长 episode?

### ★ Liu et al.,"LIBERO: Benchmarking Knowledge Transfer for Lifelong Robot Learning"
NeurIPS 2023,[arXiv:2306.03310](https://arxiv.org/abs/2306.03310)
- 为什么读:终身学习操作基准;ASPIRE 用的 LIBERO-Pro 是它的加强版,复现时必读。
- 读后钩子:LIBERO 的四套件(Object/Spatial/Goal/100)按知识类型切——
  这正是"哪个记忆组件对哪类知识起作用"实验的现成任务表。

### Nasiriany et al.,"RoboCasa: Large-Scale Simulation of Everyday Tasks for Generalist Robots"
RSS 2024,[arXiv:2406.02523](https://arxiv.org/abs/2406.02523)
- 为什么读:大规模日常任务仿真,Zetta 的验证场。
- 读后钩子:RoboCasa 的场景多样性和渲染负担——本地 4GB 显存能跑它的哪个子集?

### Li et al.,"BEHAVIOR-1K: A Benchmark for Embodied AI with 1,000 Practical Daily Tasks"
arXiv 2023 / NeurIPS 2024,[arXiv:2306.13378](https://arxiv.org/abs/2306.13378)
- 为什么读:千级日常任务基准,ASPIRE 的第三验证场(OmniGibson/Isaac 栈,本地不可行,知道即可)。

### Yang et al.,"EmbodiedBench: Comprehensive Benchmarking MLLMs as Embodied Agents"
ICML 2025 Oral,[arXiv:2502.09530](https://arxiv.org/abs/2502.09530),
[GitHub](https://github.com/EmbodiedBench/EmbodiedBench)
- 为什么读:多模态具身 agent 基准(1,128 任务 × 4 环境),EmbodiSkill 的第二验证场。
- 读后钩子:它的 EB-ALFRED 子集是文本的——我们文本通道的结果可直接与它对表。

---

## 五、前沿 2025–2026(自进化线 + 相邻前沿)

### ★ ASPIRE: Agentic Skills Discovery for Robotics
NVIDIA 等,2026-07,[arXiv:2607.00272](https://arxiv.org/abs/2607.00272),
[项目页](https://research.nvidia.com/labs/gear/aspire/),[GitHub: NVlabs/ASPIRE](https://github.com/NVlabs/ASPIRE)
- 状态:**已精读,复现对象。** 技能=代码(CaP-X/MuJoCo Playground 底座),闭环执行引擎
  带诊断→修复→验证,进化搜索发现新技能。
- 读后钩子(精读后已确认):官方限制第 4 条自认"长期记忆管理不完整(条目陈旧/过特化/
  冗余/误导)"——这正是我们准入精度与库生命周期仪器的落点;其 debug 循环 token 密集
  (真机部分百万级),复现必须缩规模、换端点。

### Recuris: Recursive Experiential–Working Memory Evolution
2026-08,[arXiv:2608.24876](https://arxiv.org/abs/2608.24876),[GitHub: Gen-Verse/Recuris](https://github.com/Gen-Verse/Recuris)
- 状态:已精读(数字域对照系)。EM-WM 耦合 + 四组件(技能/状态规范/调用策略/校验器)
  归因 + 门控进化;35/37 对提升;EM-only≈0、WM-only +23.9 的双分离。
- 读后钩子:它自己承认"校验器组件需要一个校验真正起约束作用的领域才能测"——
  具身闭环就是那个领域(物理验证是常态)。组件归因双分离实验没人在具身域做过。

### MUSE: Learning on the Job
2025-10,[arXiv:2510.08002](https://arxiv.org/abs/2510.08002)
- 状态:已精读。Plan-Execute-Reflect-Memorize,分层记忆(战略/程序/工具)。
- 读后钩子:只从成功写 SOP、单次成功即入库——与我们 0/20 的"从不准入"是**同一根
  准入轴的两个极端**;失败侧记忆(负知识)完全空白。

### SHAPER: Self-Evolving Embodied Agents via Skill-Harness Evolution
微软研究院,2026-08,[arXiv:2608.11350](https://arxiv.org/abs/2608.11350)
- 状态:已读摘要。免训练,冻结底座,进化"技能库 + 上下文代码 harness"两样非参数资产。
- 读后钩子:闭源(Request Code);双资产拆分暗含"收益来自技能还是脚手架"的归因问题——
  Recuris 式双分离它没做。

### Zetta ζ: An Efficient Closed-Loop Embodied Harness for Self-Evolving Physical Intelligence
2026-08,[arXiv:2608.16590](https://arxiv.org/abs/2608.16590),
[GitHub: air-embodied-brain/Zetta-Embodiment](https://github.com/air-embodied-brain)
- 状态:已读摘要。冻结 VLA 底座,在线进化"代码批判器 + 恢复技能",三时间尺度闭环。
- 读后钩子:critic+recovery 分工 = 承认"失败是常态,主要工作是恢复"——与我们
  caught-error 台账哲学同源;它的批判器误报/漏报率没人测。

### EmbodiSkill: Skill-Aware Reflection for Self-Evolving Embodied Agents
2026-05,[arXiv:2605.10332](https://arxiv.org/abs/2605.10332)
- 状态:已读摘要。区分"失败证明技能坏"与"执行疏漏没照技能做",免训练。
- 读后钩子:"执行疏漏 vs 技能内容"与我们 `mark_used`(`used_by_model`,按执行动作判定)
  **概念同源**;差别在:它当反思决策规则用,我们当每次落账的被测仪器用。

### World Action Models: A Survey
2026,[arXiv:2605.12090](https://arxiv.org/abs/2605.12090)
- 读后钩子:VLA 之后的"世界动作模型"走向——前瞻与世界模型进动作环,关注其对
  验证层的影响。

### RD-VLA: Reasoning VLA(测试时算力进动作模型)
2026,[arXiv:2602.07845](https://arxiv.org/abs/2602.07845)
- 读后钩子:推理型 VLA 把"多想再动"搬进底层——与我们决策层的"多想"如何分工,
  会不会重复计费同一个思考?

### 数据负迁移元分析(1,228 篇 VLA)
2026,[arXiv:2602.09722](https://arxiv.org/abs/2602.09722)
- 读后钩子:数据层面的负迁移已有元分析;**经验层面**(库里的条目何时变负)无人测——
  这是我们"经验级负迁移可观测性"的立项对照。

### Self-Improvements in Modern Agentic Systems: A Survey
2026-07,[arXiv:2607.13104](https://arxiv.org/abs/2607.13104)
- 读后钩子:定位用——把我们的"测量层"放进自改进全景的分类格里,写 related work 的骨架。

---

## 六、思想经典(写引言时引)

- Brooks,"Intelligence Without Representation",AIJ 1991,
  [DOI](https://doi.org/10.1016/0004-3702(91)90053-7) — 表征不是智能的全部,行为与身体是。
- Pfeifer & Bongard,《How the Body Shapes the Way We Think》,MIT Press 2006 —
  具身智能的思想框架:身体即计算的一部分。

---

## 附录:项目模块 → 学术出处对照

| 项目模块(代码) | 直接出处 | 同源概念 |
|---|---|---|
| 运行时循环(`core/runtime.py`) | ReAct;Inner Monologue | agent loop / sense-think-act |
| 义务账本(`WorkingMemoryState`,v0.2 §5.3) | — | BDI 的 intention;规划里的 open precondition;多 agent 的 commitment |
| 经验库(`embodied_agent/episodic/`) | Generative Agents 记忆流 | Voyager 技能库(存储侧) |
| 检索矛盾守卫(`episodic/retrieval.py:contradicted`) | — | (无直接对应;世界重置对记忆谓词的反驳) |
| 动作级使用判定(`episodic/retrieval.py:mark_used`) | EmbodiSkill(概念同源) | — |
| 技能=参数化程序(`SkillSpec`) | Code as Policies → CaP-X → ASPIRE | Options 框架的半马尔可夫动作 |
| 技能准入门(`SkillValidationReport`:≥3 实例 × ≥2 类别) | — | SOAR chunking(无门对照:Voyager) |
| 消融七臂 + 假臂结构性拒绝(`EVENT_MODULE`) | ML ablation study | Recuris 的四组件归因(数字域) |
| 15 类事件 + schema v4 + 哈希冻结 | — | Generative Agents 的 trace 结构(非强制) |
| 三层验证(v0.2 §5.8) | SayCan(事前可供性)、Inner Monologue(事后反馈) | Recuris 的 checkers(数字域) |
| caught-error 台账(210 条) | — | (工程纪律;文献无对应物) |
| 三通道(PyBullet/MetaWorld/ALFWorld) | MetaWorld;ALFWorld | CALVIN/LIBERO/RoboCasa(同代基准) |
