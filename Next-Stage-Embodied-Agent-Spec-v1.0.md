# Embodied Agent Framework 下一阶段规格：操作鲁棒性 → RGB-D → 经验复用

版本：v1.0  
日期：2026-09-18  
状态：基于 S1 项目总结的下一阶段设计规格  
适用环境：WSL 项目工作区；Windows 文件夹不作为运行状态来源

## 1. 规格依据与现状

本规格以 `Project_summary.md` 中的项目总结和已有 S1 规格为状态依据。总结描述的是已经完成的实现和实验结果，不是需要照抄的架构指令；本规格只提取其中已验证的事实，再据此安排下一阶段。

S1 已具备一个可运行的固定机械臂 Agent：DeepSeek/规则 planner、PyBullet Panda、真实夹爪接触、逐步验证、抓空恢复、独立 Evaluator、事件日志和回放。固定 90 集验收中，规则基线为 81/90，在线 DeepSeek 为 80/90；故障配对中 recovery 为 19/30，no-recovery 为 0/30。分层结果为 3 物体 96.7%、4 物体 90%、5 物体 80%。

这些结果说明：S1 的主闭环已经成立，下一阶段不应再把“接入 LLM”“做一个抓方块 Demo”作为主要目标。当前最有价值的问题是多物体长任务中，已经成功放置的物体会影响后续放置，目标区域的槽位和容量没有被规划器充分建模，操作技能的落座和干扰处理仍有提升空间。

## 2. 下一阶段总决策

采用以下顺序：

```text
S2 操作可靠性与目标区域约束
  -> S3 RGB-D 几何感知
  -> S4 VLM 语义 grounding 与开放语言
  -> S5 长程任务、Memory 与跨任务经验
```

### 2.1 为什么先做 S2

如果现在直接接 RGB-D/VLM，5 物体失败会同时混入感知误差、抓取误差、槽位规划误差和验证误差，无法判断系统为什么变差。S2 先保持特权状态，让物理执行、目标容量、位置分配和恢复策略在稳定接口上收敛；这样 S3 引入视觉后，oracle 与传感器模式之间的差异可以被测量，而不是被操作底层问题掩盖。

### 2.2 这不是最终方向的锁死

S2 是基于目前错误分布的优先级判断。若 S2 的物理实验显示主要瓶颈其实是目标识别或状态观测，而不是放置与干扰，再提前进入 S3。是否调整顺序由固定实验数据决定，不由单次演示决定。

## 3. S2：操作可靠性与目标区域约束

### 3.1 S2 目标

在固定 Panda、PyBullet 和特权状态下，把“多物体分别归位”从基本顺序执行升级为能够处理共享目标区域、目标容量、放置顺序、已放物体干扰和局部失败恢复的操作系统。

S2 仍然是同一条黄金任务主线，不新增移动底盘、第二机械臂、VLM、复杂工具或大模型训练。

### 3.2 新增任务因素

1. **同类多物体进入同一目标区域**：例如 3 个红色物体共同进入一个托盘。
2. **目标容量约束**：目标区域有最大数量、面积或可用槽位。
3. **槽位分配**：每次放置前根据已占用物体、目标边界和物体 footprint 计算候选位置。
4. **放置顺序**：先放大物体、先放靠里位置或先放会阻挡其他物体的目标。
5. **物体间干扰**：已放物体可能遮挡、碰撞或使原本可用的落点不可用。
6. **临时区**：当目标暂时无合适槽位时，允许放入声明过的安全临时区。
7. **局部重新规划**：一个物体失败时保留已经确认完成的对象，只规划剩余对象。
8. **多类型故障诊断**：在原有抓空基础上，增加目标满、放置不稳、路径碰撞、状态未知等故障的分类和有界退出。

S2 不要求 Agent 能解决任意堆叠或密集装箱装载。目标区域先使用平面托盘或低壁区域，几何规划只在声明的桌面工作区内有效。

## 4. S2 的核心模型变化

### 4.1 从二值 placed 到关系状态

S1 的对象状态不能只用 `placed=True/False`。S2 采用显式关系：

```text
object(obj)                 # 对象存在
category(obj, red)          # 属性
at(obj, pose)               # 当前位姿
inside(obj, target)         # 几何上位于目标有效区域
supported_by(obj, surface)  # 支撑关系
held_by(obj, gripper)       # 夹持关系
stable(obj)                 # 稳定窗口成立
slot_reserved(obj, slot)    # 规划暂时占用
goal_satisfied(obj, target) # 任务谓词成立
```

`goal_satisfied` 必须由当前 observation 和 verifier 重新计算。历史完成记录只能用于日志和提示，不能跳过当前几何验证。

### 4.2 目标区域模型

每个目标区域增加：

- `target_id`、接受类别集合；
- 可用 footprint、多边形或栅格表示；
- 高度、边界和允许接触；
- `capacity` 与当前占用对象；
- 候选放置槽位生成器；
- 槽位评分：边界余量、与已放物距离、后续可达性、稳定性和遮挡风险。

槽位是当前 episode 的规划实体，不是永久记忆中的绝对坐标。已放置对象变化后，剩余槽位必须重新计算。

### 4.3 Planner 的新职责

Planner 仍只输出 typed plan，但 plan step 必须允许引用目标和放置策略：

```json
{
  "step_id": "s4",
  "skill": "place",
  "args": {
    "object_id": "red_cube_2",
    "target_id": "red_tray",
    "placement_policy": "nearest_free_slot"
  },
  "preconditions": [
    "held(red_cube_2)",
    "free_slot(red_tray)"
  ]
}
```

具体槽位坐标由确定性 `PlacementPlanner` 根据当前状态生成；DeepSeek 不输出精确米制坐标。模型可选择顺序和策略，不能越过几何和碰撞验证。

## 5. S2 架构和职责

```text
Task / EvalSpec
      |
Task Interpreter -> GoalSpec
      |
Planner(rule / DeepSeek) -> typed remaining plan
      |
PlanValidator -> symbol + entity + budget checks
      |
Runtime -> SkillExecutor
      |                 |
PlacementPlanner       Motion / Gripper Controller
      |                 |
WorldState <----- PyBullet Observation
      |
RuntimeVerifier -> RecoveryPolicy / Replan
      |
Independent Evaluator + Event Trace
```

新增模块的边界：

| 模块 | 负责 | 不负责 |
|---|---|---|
| `PlacementPlanner` | 生成/排序当前目标的可用槽位，计算 footprint 与余量 | 不决定自然语言目标，不控制机械臂 |
| `ManipulationSkill` | 执行带 pregrasp、落座、撤退和稳定检查的 pick/place | 不判断任务整体是否完成 |
| `WorldState` | 保存当前关系、来源、置信度和状态版本 | 不保存隐藏评测答案 |
| `RuntimeVerifier` | 判断当前 assignment/slot 是否成立 | 不把 skill 返回码当物理证据 |
| `RecoveryPolicy` | 按 failure code 选择观察、换槽位、重试或退出 | 不无限尝试，不重置整个世界 |
| `Evaluator` | 用独立 EvalSpec 判定最终得分 | 不向 Agent 暴露正确 assignment |

S2 可以继续沿用当前目录并逐步迁移，不要求为每个模块创建空目录。只有当 placement、recovery 和 verification 的测试边界稳定后再拆包。

## 6. S2 技能规格

### 6.1 `pick(object_id)`

前置：对象可见/可定位、夹爪空闲、对象未满足目标、抓取候选通过可达性检查。

执行：选择顶抓候选，预抓取上方接近，下降，夹爪闭合，抬升，撤离干扰区。

成功证据：夹爪状态、抬升高度、对象与末端相对运动、接触摘要和稳定窗口共同成立。

失败：返回 `GRASP_MISS`、`IK_UNREACHABLE`、`PATH_COLLISION` 或 `STATE_UNCERTAIN`；不得仅返回自然语言 message。

### 6.2 `place(object_id, target_id, placement_policy)`

前置：确认持有对象、目标存在、目标接受对象类别、当前有候选槽位。

执行：从 `PlacementPlanner` 取槽位，预放置，下降到落座高度，检查支撑和边界，释放，撤退，等待稳定。

成功证据：撤退后对象 footprint 在目标有效区域内、对象已支撑、目标未溢出、速度稳定、夹爪不再承载对象。

失败：`TARGET_FULL`、`PLACE_UNSTABLE`、`PLACE_COLLISION`、`OBJECT_DROPPED`、`STATE_UNCERTAIN`。

### 6.3 `inspect(target_id)`

强制产生新的观察和目标区域报告。报告必须包括占用对象、剩余槽位、边界余量、静止情况和冲突对象。S2 Runtime 在每个 place 后自动触发，不依赖 Planner 输出 inspect。

### 6.4 `safe_retreat()`

在夹持状态已知时执行保守撤离；状态未知时先停稳和观察。不得把 `release` 当作通用恢复动作。

## 7. S2 恢复与重规划

### 7.1 故障处理矩阵

| failure code | 首选动作 | 是否必须自动恢复 |
|---|---|---|
| `GRASP_MISS` | 撤离、重观察、换候选/重试 | 是，保留现有能力 |
| `TARGET_FULL` | 重新计算槽位；无槽位则换目标顺序或临时区 | 是，限预算 |
| `PLACE_UNSTABLE` | 观察对象姿态；调整高度/槽位后重试 | 是，限预算 |
| `PLACE_COLLISION` | 放弃当前槽位，重新规划顺序/槽位 | 是，限预算 |
| `PATH_COLLISION` | 不执行；换路径或报告不可达 | 至少正确报告 |
| `OBJECT_DROPPED` | 重新观察；若可定位则生成新 pick，否则失败退出 | 可选 |
| `STATE_UNCERTAIN` | 停稳、重新观察、禁止盲重试 | 是，至少有界处理 |
| `INVALID_PLAN` | 修复或重新请求结构化计划 | 是，有限次数 |

### 7.2 局部 replan

每次验证后构造 `RemainingGoal`：

```text
RemainingGoal = 所有 EvalSpec assignment
                - 当前 observation 已确认满足的 assignment
                + 当前未解决的容量/顺序/槽位约束
```

replan 不重做已验证完成的对象，除非它后来被碰撞移出目标区域。已经完成的对象仍然会作为障碍和容量约束输入。

### 7.3 预算

S2 初始预算保持 S1 的上限，再增加：

- 每个目标区域最多 3 次换槽位；
- 每个对象最多 2 次 place 调整；
- 单 episode 最多 5 次局部 replan；
- 连续 3 次 replan 没有减少未完成目标时终止。

预算计入日志，并覆盖 `VERIFY_FAILED`、`TARGET_FULL` 和 `STATE_UNCERTAIN` 分支，禁止通过不同错误码绕过上限。

## 8. S2 任务集与实验

建立 `TabletopOrganize-v2`，保留 S1 的 `v1` 不变作为回归基线。

| 子集 | 规模 | 变化 | 目的 |
|---|---:|---|---|
| `v2-clean` | 90 episodes | 3/4/5 物体；同类多物共享目标；固定 seed | 测多物体操作稳定性 |
| `v2-capacity` | 45 episodes | 目标容量小于对象数，必须换序/临时区/报告失败 | 测容量与约束处理 |
| `v2-interference` | 45 episodes | 已放对象会阻挡部分槽位或路径 | 测顺序和局部重规划 |
| `v2-fault` | 90 episodes | 抓空、目标满、放置不稳、状态不确定 | 测故障分类与恢复 |
| `v2-negative` | ≥15 cases | 非法目标、不可达槽位、无唯一解、预算耗尽 | 测有界失败 |

正式集的场景、EvalSpec、fault 配置和 seed 在运行前冻结。所有新任务必须保存目标区域几何和槽位生成参数。

### 8.1 S2 运行矩阵

| 模式 | 目标/计划 | Placement | Recovery | 用途 |
|---|---|---|---|---|
| S1-B1 | rule + 正确 GoalSpec | 固定单槽 | on | 旧基线 |
| S2-B0 | rule + 正确 GoalSpec | 新槽位 planner | off | 物理/槽位下限 |
| S2-B1 | rule + 正确 GoalSpec | 新槽位 planner | on | 隔离 recovery 与 placement |
| S2-E0 | DeepSeek | 新槽位 planner | off | 在线无恢复基线 |
| S2-E1 | DeepSeek | 新槽位 planner | on | 目标系统 |

`v2-fault` 中 B0/B1 使用相同初始状态和计划；E0/E1 另报告完整端到端结果。模型响应 replay 只能作诊断，不能替代在线成绩。

### 8.2 S2 验收门槛

1. `v2-clean` 规则基线完整任务成功率 ≥90%，且 5 物体层不低于 90% 点估计；3 物体层不低于 90%。
2. `v2-clean` 在线 DeepSeek 完整任务成功率 ≥85%，或相对 S1 同域结果不下降超过 5 个百分点；目标解析错误单独报告。
3. `v2-capacity` 中无法满足容量的任务不会非法执行，必须换序、使用临时区或有界失败。
4. `v2-interference` 中放置顺序和槽位重算能减少因已放物体造成的失败；成功率和失败类别均要对照 S1 单槽版本。
5. `v2-fault` 每种故障都有结构化 code；可恢复故障的 recovery on 相对 off 有稳定提升，不可恢复故障能安全终止。
6. 无隐藏吸附、物体瞬移、删除困难样本或静默 rule fallback。
7. 90% 目标是阶段验收目标，不是总体能力保证；报告成功数/总数和 95% 区间。

若规则基线不能先达到门槛，不接入更多 LLM 能力；先修低层控制、几何槽位或判分器。

## 9. S2 实施工作包

### W2.1：复现与错误归因，预计 1～2 个工作日

复现总结中的 90/90、5 物体 80% 分层结果；将失败事件按 `grasp`、`place geometry`、`capacity`、`interference`、`verification`、`planner` 分类；确认当前 5 物体失败是否确实集中于共享目标区域。

产物：`s1_reproduction.md`、错误分布 CSV、失败视频索引。若复现不一致，先解释环境/模型/随机性差异。

### W2.2：目标区域与槽位规划，预计 2～4 个工作日

引入容量、footprint、占用关系、候选槽位和确定性评分；先在 symbolic backend 测试边界，再接 PyBullet。

产物：PlacementPlanner、槽位可视化、目标区域 unit/integration tests。

### W2.3：稳健 place skill，预计 2～4 个工作日

将落座、支撑、释放高度、自适应等待和撤退整合为可测阶段；增加放置不稳和目标满故障注入。

产物：SkillResult failure taxonomy、关键帧/视频、同一槽位重复测试报告。

### W2.4：局部 replan 与有界 Runtime，预计 2～3 个工作日

保留已验证完成对象，动态重算剩余目标、槽位和障碍；所有 replan 分支消耗统一预算。

产物：状态机回归测试、故障矩阵测试、局部 replan trace。

### W2.5：v2 benchmark 与验收，预计 2～4 个工作日

冻结任务集，运行 B0/B1/E0/E1，生成统计报告和精选/失败回放。

产物：`TabletopOrganize-v2` 配置、summary CSV/JSON、验收报告。预计 S2 总周期 9～17 个全职工作日，W2.1 后根据真实错误比例重新估算。

## 10. S3：RGB-D 几何感知

S2 达标后再进入 S3。S3 先替换“物体位姿和类别来自特权状态”的一部分，不先让 VLM 负责精确控制。

### 10.1 分阶段替换

```text
Oracle state
  -> RGB-D + 已知实例 mask（只调通坐标链路）
  -> RGB-D + 颜色/形状候选分割
  -> RGB-D + 轻量检测/几何拟合
  -> RGB-D + VLM 语义候选
```

第一阶段保留已知 mask 是接口调试模式，不能计入最终视觉成绩。最终输入只允许 RGB/RGB-D、语言、机器人状态和标定；控制器不得偷读隐藏物体位姿。

### 10.2 S3 主要模块

- 相机内外参和坐标变换；
- depth 去噪、桌面平面估计、物体点云/footprint；
- 实例 ID 跨帧跟踪；
- `WorldState` 中的 visibility、confidence 和 evidence；
- 视觉观察失败与物理执行失败的区分；
- 多视角/重新拍摄策略；
- oracle/sensor 双通道对照。

RGB-D 负责空间几何，VLM 后续负责开放类别、语义关系和自然语言绑定。不要让模型根据图片直接编造高精度米制坐标。

### 10.3 S3 验收门槛

- 同一 `v2-clean` 任务集同时跑 oracle、RGB-D transition、纯传感器模式；
- 报告目标绑定、位姿误差、槽位选择和完整任务成功率的分项损失；
- 纯传感器模式在声明的 3～5 物体任务域达到 ≥70% 点估计；
- 视觉 unknown 能触发重新观察或安全失败，不能伪装成控制成功；
- oracle 分数不因视觉模块接入而变化。

## 11. S4：VLM、长程和跨任务经验

S4 不是 S2 的并行工作。它依赖 S2 的槽位/放置能力和 S3 的 WorldState 来源标记已经稳定。

### 11.1 VLM 角色

VLM 只负责：开放物体类别候选、目标语义对齐、图像区域证据、自然语言中的关系和约束解析。所有结果必须进入 schema、置信度和当前 observation 验证。

### 11.2 长程任务

增加：5～10 个对象、多个目标区域、多子目标依赖、临时区、先后约束、部分完成后继续执行。单纯把互不相关的对象数量增加，不足以称为长程；必须包含中间状态和失败后的持续执行。

### 11.3 Memory 顺序

1. S1/S2：持久化 episode、失败 code、技能序列和证据。
2. S3：按场景/技能/失败类型结构化检索，不影响决策。
3. S4：检索相似任务中的有效技能组合、规划顺序和恢复案例。
4. 评估 Memory on/off 与 Recovery on/off 的 2×2 消融。

Memory 先用标签过滤和文本相似度；只有数据量证明需要时才加 embedding/vector DB。对象绝对坐标和旧仿真 body ID 不得直接跨场景复用。经验必须带适用条件、版本和验证结果；错误经验保留但不能自动晋升为正式策略。

### 11.4 S4 验收目标

- 未见过的物体组合、顺序和语言表达达到 70%～80% 完整任务成功率区间；
- Memory/recovery 相对无 Memory/no recovery baseline 有稳定、可重复的改善；
- 经验不会造成负迁移超过预先设置的容忍范围；
- 新技能组合必须先通过离线验证和小规模 sandbox，再进入正式库。

## 12. 评测和统计的共同要求

所有阶段必须保留：

- 固定任务配置和 seed；
- 成功数/总数、分层成功率和 95% 区间；
- 规则、在线模型、recovery、memory 的对照组；
- 逐事件日志、模型调用元数据、失败视频和回放；
- 仿真崩溃、API 错误、人工干预、fallback、assisted grasp 的单独统计。

正式 test 集冻结后，修改控制参数、prompt 或判分器必须生成新批次；不能覆盖旧结果后仍称测试未见。重复运行在同一场景配置上需要按配置聚类处理，避免把重复 rollout 当完全独立样本。

主指标始终是独立 Evaluator 判定的完整任务成功率；技能成功率、LLM 计划有效率、恢复成功率和视觉检测准确率作为诊断指标，不能互相替代。

## 13. 当前暂缓项

以下内容继续放在 backlog：

- 工具使用；
- 第二仿真器或公共 benchmark 适配；
- ROS2/MoveIt 体系迁移；
- Docker/图形环境封装；
- 向量数据库和复杂知识图谱；
- 自动技能生成、自我修改和 Self-Evolving；
- VLA/大模型训练；
- 移动机械臂、双臂、人形和真实硬件部署。

触发条件是：当前主线任务已经达到对应阶段门槛，或者现有系统的错误分布证明它是主要瓶颈。技术栈丰富应体现在真实接口、实验和回放上，不以增加依赖数量作为目标。

## 14. 下一次开工的具体任务

下一次 coding agent 任务只做 W2.1：

1. 在 WSL 工作区确认当前 S1 代码、配置和运行入口；
2. 找到 90 集 clean 与 30 集故障配对实验的真实产物；
3. 用统一脚本重算总成功率、分层成功率、故障差异和错误类型；
4. 随机抽取至少 5 个 5 物体失败 episode，观看轨迹/读取事件，确认失败确实属于共享目标区域干扰、槽位不足或相关验证问题；
5. 不修改 planner、skill 或物理参数；
6. 输出错误分布报告和 S2 优先级建议。

W2.1 没有完成前，不开始 RGB-D、不接 VLM、不引入 Memory 数据库，也不根据一个失败样例改槽位算法。它的作用是把“5 物体性能下降的原因”从合理猜测变成可核对的工程事实。

## 15. 需要用户确认的默认假设

本规格暂按以下默认继续推进：

- S2 仍使用固定机械臂和 PyBullet；
- 优先深化整理归位，不加入工具使用；
- S3 按 RGB-D 几何到 VLM 语义的顺序进行；
- DeepSeek 继续作为可替换 planner provider；
- 运行和代码以 WSL 工作区为唯一事实来源；
- S1 总结中的结果作为已完成状态，但正式复现实验仍需保留原始产物并允许核查。

若这些默认没有变化，下一步就直接执行 W2.1；若有变化，只需调整相应阶段，不需要重写整个 Framework 规格。
