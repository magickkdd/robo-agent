# Embodied Agent Framework v0.2
## Long-Horizon VLM/LLM Continuous Decision Agent

版本：v0.2 讨论/实施基线
状态：进入设计与实施阶段

## 1. 阶段目标

v0.2 不再把 Benchmark Stress Test 作为决定“做不做某个模块”的闸门。Benchmark 用于验证、对照和消融；本阶段直接构建完整的模型驱动 Long-Horizon Embodied Agent。

核心目标：

> 让 LLM/VLM 在真实视觉/环境反馈、持续变化和多阶段任务约束下，维护全局任务目标，管理子目标，利用当前任务与历史经验，根据反馈持续决策、恢复和重新规划，并在现有技能不足时提出、验证和保存可复用的程序技能。

核心闭环：

```text
User Task
   ↓
VLM Perception / Observation
   ↓
WorldState
   ↓
Task Planner
   ↓
Working Memory + Episodic Memory Retrieval
   ↓
Decision Context
   ↓
LLM/VLM Decision
   ↓
Skill Retrieval / Existing Skill / Skill Acquisition
   ↓
Skill Execution
   ↓
Verification + New Observation
   ↓
Execution Feedback
   ↓
Working Memory Update / Episode Record
   ↓
Replanning / Next Decision
   └──────────────────────────────→
```

## 2. 研究问题

RQ1：VLM/LLM 是否能够在视觉与状态反馈闭环中完成明显更长、更复杂的多阶段任务？

RQ2：任务分解、子目标依赖和 Working Memory 是否能提升长程任务的全局连贯性？

RQ3：Episodic Memory 是否能使 Agent 在后续相似任务中复用历史经验？

RQ4：当现有技能不足时，Agent 能否生成经过验证、可参数化、可迁移的程序技能？

RQ5：上述机制组合是否比单纯持续决策更能提升复杂任务成功率，同时在调用成本和失败率上保持可接受水平？

## 3. 核心原则

1. LLM/VLM 是高层认知与策略核心；Framework 提供状态、工具、反馈、验证和执行边界。
2. 不向 Agent 暴露 hidden evaluator truth、fault injection 标签、参考答案或专家计划。
3. VLM 主路径逐步替代 privileged state；privileged state 仅作为 debug / oracle baseline。
4. Runtime 不静默替模型选择策略。
5. 世界事实、模型意图、历史经验和评测真值严格分离。
6. Skill Acquisition 先做程序技能，不直接依赖大规模 VLA/RL 训练。
7. 所有新增能力必须可通过 ablation 单独关闭并测量。
8. Benchmark 是验证平台，不是研究方向的决定器。

## 4. 架构

```text
                           User Instruction
                                  │
                                  ▼
                       ┌──────────────────┐
                       │ Task Understanding│
                       └────────┬─────────┘
                                ▼
                       ┌──────────────────┐
                       │ Global Task Plan │
                       │ Goals/Subgoals   │
                       │ Dependencies     │
                       └────────┬─────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
 VLM Perception          Working Memory         Episodic Memory
        │                       │                        │
        └──────────────┬────────┴────────────────────────┘
                       ▼
                   WorldState
                       ▼
                DecisionContext
                       ▼
                LLM/VLM Decision
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
       Existing     Retrieve    Acquire New
         Skill        Skill        Skill
            └──────────┼──────────┘
                       ▼
                 Skill Executor
                       ▼
               Robot / Simulator
                       ▼
              Verification + VLM
                       ▼
                ExecutionFeedback
                       ▼
       Working Memory / Episodic Record
                       ▼
              Replan / Next Decision
```

## 5. 模块定义

### 5.1 VLM Perception

输入：RGB/RGB-D、任务相关视觉上下文。

输出：带来源、置信度和时间戳的语义 Observation / WorldState 更新。

至少支持：
- 物体识别与属性
- 空间关系
- 可见/不可见与遮挡
- 目标区域与占用关系
- 状态变化检测
- 不确定信息标记

禁止直接使用 simulator hidden state 代替 VLM 主路径。

### 5.2 Task Planner

负责：
- 任务理解
- Goal / Subgoal 生成
- 前置条件与依赖
- 长程计划摘要
- 根据反馈修订剩余计划

计划是可更新工作状态，不是一次生成后强制执行的脚本。

### 5.3 Working Memory

保存当前任务的：
- 当前目标与子目标
- 已完成/未完成/未知目标
- 当前承诺与暂存关系
- 恢复义务
- 已失效假设
- 关键失败尝试
- 当前计划摘要

它必须在线参与决策。

### 5.4 Episodic Memory

保存跨 episode 的结构化经验：
- 任务上下文
- 状态模式
- 行动/技能序列
- 结果
- 失败原因
- 适用条件
- 经验来源

检索结果作为模型参考，不直接改写 WorldState。

### 5.5 Skill Library

保留现有 primitive skills，并允许组合技能。

Skill 最小描述：
- name
- description
- parameters
- preconditions
- procedure
- expected effects
- termination conditions
- recovery hints
- applicability
- provenance

### 5.6 Skill Acquisition

第一阶段限定为程序技能生成：

```text
Task / Experience
      ↓
Skill Gap Detection
      ↓
LLM proposes parameterized skill/program
      ↓
Sandbox / Simulator execution
      ↓
Verifier
      ↓
Cross-instance validation
      ↓
Skill Memory
```

只有通过未见对象/布局/参数的验证，才能进入可复用 Skill Library。

不得通过修改 evaluator、teleport、hidden state 或直接写入世界完成技能验证。

### 5.7 Recovery / Replanning

Recovery 与 Replanning 是决策能力，而不是固定 Runtime 分支。

模型可决定：
- retry
- change candidate
- re-observe
- abandon local strategy
- revise subgoal
- replan remaining task
- request clarification

底层组件负责执行安全和约束检查。

### 5.8 Verification

分三层：
1. SkillResult：发生了什么；
2. Runtime Verification：当前证据是否支持技能后置条件/任务状态；
3. Independent Evaluator：独立任务评分。

三者信息来源必须隔离。

## 6. 数据契约

v0.1 继续复用：
- WorldState
- DecisionContext
- Decision
- SkillCall
- SkillResult
- ExecutionFeedback
- EpisodeResult

新增/扩展：
- TaskPlan
- Subgoal
- Dependency
- WorkingMemoryState
- EpisodicExperience
- SkillSpec
- SkillCandidate
- SkillValidationReport
- PerceptionObservation

所有记录都应带 schema_version、episode_id、状态引用、来源和时间。

## 7. 运行模式

每轮：

1. VLM/Observation 更新 WorldState；
2. Working Memory 更新当前任务状态；
3. Episodic Memory 可选检索相关经验；
4. Task Planner 更新子目标/依赖/短期计划；
5. 构造 DecisionContext；
6. LLM/VLM 选择下一项 Skill、观察或任务控制动作；
7. 校验参数与前置条件；
8. 执行一个短技能片段；
9. Verification + VLM 产生新证据；
10. 更新 Working Memory 与 Episode Record；
11. 必要时 Replanning；
12. 继续直到完成、澄清或有证据的受阻终止。

每次只执行一个短技能片段是 v0.2 的工程实现选择，不定义为永久架构限制。

## 8. 任务范围

第一批复杂任务优先使用桌面/房间整理与重排类任务，要求出现：
- 3–8 个物体
- 多个子目标
- 至少一个显式或隐式依赖
- 有限空间/资源约束
- 可能的临时状态
- 至少一次状态变化或执行失败
- 最终需要验证多个关系

之后通过公开 benchmark 验证迁移。

### 任务示例

> “把桌面整理成适合学习的状态，保留常用物品，清除阻挡区域，把物品按照用途放到合适位置；如果需要临时移动物品，任务结束前恢复必须保持的关系。”

## 9. 研究实验与消融

主系统：Full v0.2

至少包含：
- VLM
- Task Planning/Subgoals
- Working Memory
- Episodic Memory
- Replanning/Recovery
- Skill Retrieval
- Skill Acquisition（在适合的任务集启用）

核心消融：
- Full
- w/o VLM（privileged or limited semantic baseline）
- w/o Planning/Subgoals
- w/o Working Memory
- w/o Episodic Memory
- w/o Replanning
- w/o Skill Acquisition

新增能力必须比较：
- 成功率
- 长程任务完成率
- 子目标/义务遗漏
- 无效重复
- 约束违反
- 重新规划次数
- LLM/VLM 调用
- token
- wall-clock
- skill generation / validation 成本
- 新任务泛化

## 10. Benchmark

Benchmark 用作外部验证，不作为模块选择的门槛。

第一优先级：ALFWorld Text-only，验证高层任务管理/持续决策迁移。

物理/视觉验证继续保留 PyBullet，并逐步切换到 RGB/RGB-D VLM 输入。

后续根据需要加入第二 benchmark，不要求 v0.2 初期同时覆盖多个环境。

所有 benchmark adapter 不得替 Agent：
- 自动规划
- 自动 retry
- 自动恢复
- 自动组合多步动作
- 注入 hidden state / expert answer

## 11. 评测协议

必须同时报告：

### Task
- complete success
- partial progress
- constraint violations
- invalid completion

### Long-horizon behavior
- subgoal completion
- dependency violations
- progress regressions
- unnecessary rework
- repeated failures
- recovery/replanning effectiveness

### VLM
- semantic grounding accuracy
- state change detection
- unknown / uncertainty handling

### Memory
- retrieval relevance
- successful reuse
- negative transfer
- stale memory usage

### Skill Acquisition
- candidate generation rate
- validation success
- cross-instance transfer
- skill reuse success
- unsafe/invalid skill rate

### Cost
- model calls
- tokens
- latency
- simulator time
- skill-generation cost

## 12. 关键实验设计原则

1. 同一模型、同一任务、同一环境，对各消融保持一致。
2. 公开 benchmark 与自定义任务分别报告，不混合成功率。
3. 不把模型解释文字当成推理证据；以实际动作和最终状态为准。
4. 不把一次成功技能候选称为“已学习能力”；必须验证迁移。
5. 不把更高 token 消耗自动解释成更强能力。
6. Benchmark 的 hidden evaluator 只能评分，不能反馈给 Agent。

## 13. 实施阶段

### P0：v0.1 清理与契约升级
- 保留现有持续决策闭环
- 清理残留/不对称接口
- 固化 v0.2 schema
- 保证 v0.1 回归测试

### P1：VLM + Semantic WorldState
- RGB/RGB-D 输入
- VLM observation adapter
- 视觉验证
- privileged vs VLM 对照

### P2：Task Planning + Working Memory
- Goal/Subgoal
- dependency
- current task commitments
- long-horizon rolling plan

### P3：Episodic Memory
- experience schema
- retrieval
- relevance filtering
- negative transfer safeguards

### P4：Skill Acquisition
- skill gap detection
- program skill generation
- sandbox verification
- cross-instance validation
- skill memory

### P5：Full Agent + Benchmark Evaluation
- 全模块闭环
- Benchmark 迁移
- 消融
- 成本/收益分析

## 14. 非目标

本阶段暂不：
- 大规模训练 VLA
- 大规模 RL skill learning
- 真实机器人控制部署
- 多 Agent
- 通用 PDDL/HTN 求解器
- 完全开放世界技能生成
- 让 LLM 直接控制关节/电机

## 15. 阶段完成定义

v0.2 完成的最低条件不是“所有模块都有代码”，而是证明形成了完整的模型驱动长时程闭环：

```text
视觉/环境理解
→ 全局任务与子目标
→ Working Memory
→ Episodic Memory
→ LLM/VLM Decision
→ Skill/Skill Acquisition
→ Robot Execution
→ Verification
→ Replanning
→ 任务完成
```

并通过消融证明每个主要模块是否在其目标任务中产生可测量贡献；如果某模块没有收益，仍应保留负结果并分析原因。
