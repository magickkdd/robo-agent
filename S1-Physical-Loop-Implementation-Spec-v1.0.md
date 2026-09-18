# Embodied Agent Framework S1：物理闭环 MVP 实施规格

版本：v1.0  
日期：2026-09-18  
状态：可进入实施；本文描述计划和验收要求，不代表功能已经实现  
上位文档：[Embodied Agent Framework：下一阶段实现规格与长期演进边界](./Embodied-Agent-Framework-Spec-v1.0.md)

## 1. 本规格解决什么问题

S0 已经证明最小软件控制流可以串起来，但它没有机械臂、动力学、真实抓取、在线 LLM 规划和独立评测。S1 要跨过这条界线：在轻量物理仿真中，让固定机械臂依靠夹爪接触完成多物体整理，并让 Agent 根据执行后的真实状态继续、恢复或退出。

S1 的最终可见行为是：

> 用户用中文下达一个 3～5 物体整理任务。DeepSeek 根据当前结构化场景生成合法计划。机械臂逐个抓取和放置，每个关键技能后自动验证。第一次指定抓取受到未知偏移而抓空时，Agent 能发现失败、安全撤退、重新观察并再次完成。最终结果由独立评测器判定，并留下可回放证据。

本轮只实现这一个垂直切片。Memory 只记录 episode，不参与规划；RGB-D/VLM、工具使用、复杂遮挡、第二仿真器、ROS2、MoveIt 和自动技能生成均不进入 S1。

## 2. S1 完成定义

只有以下条件同时成立，S1 才算完成：

1. 使用 PyBullet 中的固定 Panda 或经过记录的等价单臂模型。
2. 抓取依赖夹爪与物体的物理接触和摩擦；执行期间不瞬移物体、不用隐藏固定约束吸附物体。
3. 单个任务包含 3～5 个物体，机械臂逐个归位。
4. 同一 Runtime 可以切换 `rule` 和 `deepseek` planner，不修改技能或仿真代码。
5. DeepSeek 输出结构化 Goal/Plan，客户端完成 schema、实体、技能和前置条件校验。
6. Runtime 在每次 `pick`、`place` 后强制重新观察和验证，不依赖模型主动请求。
7. 一次物理抓空可被检测并在预算内恢复；其他已知异常至少能分类并有界退出。
8. 独立 Evaluator 使用任务真值判分，Agent 无法读取隐藏目标答案或故障注入状态。
9. 固定任务集、无恢复对照、成功率统计、事件日志、成功/失败视频和复现说明齐全。
10. 规则规划物理基线与在线 DeepSeek 端到端在 `S1-clean` 上的完整任务成功率点估计均达到 80% 以上；恢复在配对故障集上显示稳定正向增益。

第 10 条是工程验收目标。未达到时必须报告真实数据和主要错误分布，不能用精选演示替代。

## 3. 冻结的设计决策

### 3.1 技术基线

| 项目 | S1 决策 | 说明 |
|---|---|---|
| 语言 | Python 3.11 优先 | 建立独立项目环境；不依赖 Codex 内置 Python 路径 |
| 主仿真器 | PyBullet 3.2.7 候选锁定 | 官方 PyPI 当前稳定发布为 3.2.7；Windows 原生安装必须先实测 |
| 机器人 | `pybullet_data` 的 Franka Panda 候选 | W0 核对 link/joint、夹爪耦合、许可和模型行为 |
| 物理模式 | 固定步长、显式 `stepSimulation` | 不使用 realtime 模式作为评测时钟 |
| LLM API | DeepSeek，OpenAI-compatible 客户端 | 模型名和 endpoint 从配置读取，不写死到 Runtime |
| LLM 输出 | Chat Completions JSON mode + 本地 schema 校验 | S1 不让模型直接连续调用工具；strict tool call 暂不需要 |
| 数据模型 | Pydantic v2 或等价成熟 schema 库 | 统一验证与 JSON 序列化，避免散落字典 |
| 日志 | JSONL 事件流 + JSON summary + CSV 聚合 | 不引入数据库服务 |
| 测试 | pytest | 在线 API 测试 opt-in，默认测试完全离线 |
| 配置 | YAML 或 TOML 二选一并冻结 | 禁止同一配置同时存在多种格式；实现时优先 YAML |

PyBullet 官方说明其 Python API支持 URDF、正逆运动学、动力学、碰撞查询和渲染，覆盖 S1 所需底层能力：[PyBullet Quickstart](https://github.com/bulletphysics/bullet3/blob/master/docs/pybullet_quickstart_guide/PyBulletQuickstartGuide.md.html)。DeepSeek 当前 Chat Completions API 支持 JSON mode；官方仍明确要求客户端处理空内容、截断，并验证工具参数：[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

### 3.2 为什么 S1 不采用 LLM 连续 Tool Calling

模型一次输出有类型的剩余计划，Runtime 逐步执行。发生状态变化后，Runtime 决定是否重规划。这样可以：

- 把机器人控制频率与云端 API 延迟隔离；
- 在执行前检查完整计划；
- 明确区分 LLM 错误、运动规划错误和物理执行错误；
- 让 rule planner 与 DeepSeek planner 使用同一输出契约；
- 对同一计划做 recovery 开关的配对实验。

后续若连续 tool calling 能解决已观察到的问题，再作为独立实验加入；S1 不预先引入。

### 3.3 真实抓取边界

允许：Panda 夹爪自身的关节/齿轮耦合约束、关节控制、接触力、摩擦参数、IK 与运动规划。

禁止：抓取时创建物体到夹爪的固定约束、直接设置物体位姿、把物体运动学地挂在末端、执行后直接把任务状态写成成功。

可单独保留 `assisted_grasp` 调试模式，但它的运行不得计入 S1 正式成功率。

## 4. 场景与任务域

### 4.1 场景组成

- 固定单臂机械臂和平行夹爪；基座、桌面、相机位置固定。
- 3 个目标区域，以桌面彩色平面区域或低边浅托盘实现。先避免高壁容器造成额外碰撞。
- 3～5 个简单刚体。第一批以方块和长方体为主；直立圆柱在基本抓取稳定后加入。
- 物体初始位置由固定 seed 生成，位于经预检得到的安全可达工作区，互不相交。
- 目标区域足够容纳同类多物体；S1 不堆叠。
- 背景、光照和纹理固定，因为 S1 使用特权状态，不评测视觉泛化。

初始建议范围仅用于 W0 校准：物体宽度 3～5 cm、质量 20～100 g、物体间距至少 5 cm。最终数值由单物体真实夹取实验决定，并在正式测试前冻结。

### 4.2 任务类型

S1 支持两类任务：

1. 显式指派：`把 red_cube 放到 left_tray，把 blue_block 放到 right_tray。`
2. 属性分类：`红色物体放左边，蓝色物体放右边，其余放中间。`

任务涉及：颜色、形状、对象 ID、左/中/右目标和“其余”集合。空间词以固定相机视角定义，并在 prompt 中明确。S1 不处理比较级尺寸、材质推理、常识用途或含糊代词链。

无法唯一解析时返回 `NEEDS_CLARIFICATION`；基准批量评测中将其记为未完成，不向脚本虚构用户回答。交互演示允许用户补充一次。

### 4.3 成功谓词

对每个目标对象，以下条件必须同时满足：

- 对象水平 footprint 位于目标有效区域内；
- 对象受桌面或目标区域支撑；
- 对象未被夹爪持有；
- 线速度与角速度低于冻结阈值，并持续稳定窗口；
- 对象没有与禁止物体发生持续穿透或异常接触；
- 映射到的目标与隐藏 `EvalSpec` 一致。

所有对象谓词成立且预算未异常终止，episode 才成功。任务开始时已经满足的对象不需要重新搬动，但必须由当前几何状态确认。

## 5. 软件结构

当前扁平原型将在 W2 中渐进迁移。规划后的目标结构是：

```text
embodied_agent/
  core/
    contracts.py          # 纯数据契约和枚举
    runtime.py            # 有界状态机
    events.py             # 事件类型与写入接口
  planning/
    base.py               # Planner protocol
    rule.py               # oracle GoalSpec 的规则基线
    deepseek.py           # DeepSeek adapter
    prompts.py            # 版本化 prompt
    validation.py         # 计划语法/语义校验
  skills/
    registry.py
    pick.py
    place.py
    observe.py
    retreat.py
  simulation/
    base.py               # RobotWorld protocol
    symbolic.py           # 迁入现有 test double
    pybullet_world.py
    panda_controller.py
    scene_loader.py
    fault_injection.py
  verification/
    runtime.py
    predicates.py
  recovery/
    policy.py
  evaluation/
    task_loader.py
    evaluator.py
    runner.py
    metrics.py
  cli.py
configs/
  runtime/
  scenes/
  tasks/
  models/
tests/
  unit/
  integration/
  contract/
  online/
```

这是一组职责边界，不要求第一天创建所有空目录。`symbolic.py` 只用于快速测试；PyBullet 和 symbolic backend 必须满足相同的观察/机器人接口，但不强迫它们模拟相同物理细节。

## 6. 核心协议

### 6.1 类型约定

- 长度使用米，角度使用弧度，时间使用秒。
- 位姿必须带 `frame_id`，四元数固定 `[x, y, z, w]`。
- 所有核心对象带 `schema_version`；所有 episode/plan/step/skill/observation 具有唯一 ID。
- 仿真 body ID 仅存在于 PyBullet adapter 内，不进入 LLM prompt 或通用协议。
- `success` 只保留在 episode 级；技能使用 `status`，谓词使用三值 `TRUE/FALSE/UNKNOWN`。

### 6.2 关键数据模型

```python
class TaskInput(BaseModel):
    task_id: str
    instruction: str
    language: Literal["zh-CN", "en"]

class GoalAssignment(BaseModel):
    object_id: str
    target_id: str

class GoalSpec(BaseModel):
    schema_version: Literal["1"]
    task_id: str
    assignments: list[GoalAssignment]
    hard_constraints: list[str] = []
    clarification_required: bool = False

class PlanStep(BaseModel):
    step_id: str
    skill: Literal["pick", "place"]
    args: dict[str, str]

class Plan(BaseModel):
    schema_version: Literal["1"]
    plan_id: str
    based_on_state_version: int
    steps: list[PlanStep]

class SkillResult(BaseModel):
    call_id: str
    status: Literal["completed", "failed", "timeout", "uncertain", "cancelled"]
    failure_code: str | None
    before_observation_id: str
    after_observation_id: str | None
    completed_phase: str | None
    evidence: dict

class VerificationReport(BaseModel):
    report_id: str
    goal_ref: str
    state_version: int
    predicates: list[dict]
    overall: Literal["true", "false", "unknown"]
```

具体实现可以增加字段，但不得删除来源、状态版本和证据引用。

### 6.3 Planner 协议

```python
class Planner(Protocol):
    def create_goal_and_plan(
        self,
        task: TaskInput,
        world: WorldState,
        skills: SkillCatalog,
    ) -> PlanningResult: ...

    def replan(
        self,
        task: TaskInput,
        goal: GoalSpec,
        world: WorldState,
        failure: FailureContext,
        remaining_budget: Budget,
    ) -> PlanningResult: ...
```

rule planner 接收基准系统提供的结构化 GoalSpec，仅作为执行能力上限；它不伪装成通用自然语言解析器。DeepSeek planner 同时完成自然语言实体绑定和动作排序，端到端错误必须计入成绩。

### 6.4 RobotWorld 协议

```python
class RobotWorld(Protocol):
    def reset(self, scene: SceneSpec, seed: int) -> Observation: ...
    def observe(self, channel: ObservationChannel) -> Observation: ...
    def robot_state(self) -> RobotState: ...
    def command_joint_path(self, path: JointPath, timeout_s: float) -> MotionResult: ...
    def command_gripper(self, width_m: float, force_n: float, timeout_s: float) -> MotionResult: ...
    def contacts(self) -> list[Contact]: ...
    def render(self, camera_id: str) -> FrameRef: ...
    def close(self) -> None: ...
```

语义级 `pick/place` 不属于 RobotWorld。它们由 Skill 层组合 IK、路径、夹爪、物理步进和验证。

### 6.5 Agent 与 Evaluator 的信息隔离

场景加载后创建两种只读视图：

- `AgentObservationProvider`：S1 可暴露声明过的对象类别、位姿、目标位置和机器人状态；不暴露正确 assignment、故障开关或最终分数。
- `EvaluationTruthProvider`：允许 Evaluator 读取真实 body pose、速度、接触和隐藏 EvalSpec；对象不能被 Planner、Skill 或 Recovery 引用。

用依赖注入限制通道，并写反向测试：Agent 组件构造函数中不存在 truth provider。

## 7. Runtime 状态机

```text
RECEIVE_TASK
  -> OBSERVE
  -> PLAN
  -> VALIDATE_PLAN
  -> PRECHECK_STEP
  -> EXECUTE_SKILL
  -> OBSERVE_AFTER_SKILL
  -> VERIFY_STEP
       -> NEXT_STEP
       -> LOCAL_RECOVERY
       -> REPLAN
       -> FAILED
  -> FINAL_VERIFY
       -> SUCCEEDED
       -> REPLAN or FAILED
```

### 7.1 状态转换规则

1. 每个计划绑定 `state_version`。执行步骤前如果世界状态已变化，必须重新检查当前步骤。
2. 每次 `pick/place` 后强制 `observe -> verify`；LLM 无法关闭这一行为。
3. 已满足的 assignment 根据当前状态删除，不依赖历史 `placed=True`。
4. 技能超时或结果未知时先观察，不能直接重复同一动作。
5. 局部抓空恢复由确定性 policy 处理；目标映射、顺序或可行性改变时才调用 replan。
6. 所有循环共享统一 Budget，不允许 final verification 分支绕过计数。
7. 终止状态只有 `SUCCEEDED`、`FAILED`、`NEEDS_CLARIFICATION`、`CANCELLED`。

### 7.2 初始预算

| 预算 | 初始值 | 超限行为 |
|---|---:|---|
| 物理 skill call | 30 | 安全停稳后失败 |
| 单对象额外 pick 尝试 | 2 | 进入 replan 或失败 |
| 全局 replan | 3 | 失败 |
| 模型请求，含格式修复 | 10 | 失败 |
| 连续无进展动作 | 3 | 失败 |
| 单 skill 仿真时长 | 30 秒 | 停稳、观察、分类 |
| episode 墙钟 | 15 分钟 | 安全停稳后失败 |

预算在 dev 集校准后冻结。Runtime 每次循环必须证明：剩余目标数减少、状态更接近目标，或消耗了有限恢复预算。

## 8. 物理控制与技能实现

### 8.1 控制分层

```text
pick(object)
  -> select_grasp_pose
  -> solve_ik(pregrasp, grasp, lift)
  -> validate_joint_limits_and_collisions
  -> execute_pregrasp
  -> open_gripper
  -> descend
  -> close_gripper
  -> lift
  -> verify_grasp
```

```text
place(object, target)
  -> select_free_place_pose
  -> solve_ik(preplace, place, retreat)
  -> validate_joint_limits_and_collisions
  -> transfer
  -> descend
  -> open_gripper
  -> retreat
  -> wait_for_settle
  -> verify_place
```

### 8.2 运动规划策略

S1 首先使用受限顶抓：固定末端向下姿态、预抓取/预放置高度、离散关节轨迹与逐点碰撞检查。IK、关节控制和碰撞查询直接使用 PyBullet 成熟接口；不自行编写 IK 或通用采样规划器。

如果在合法随机场景中，简单路径因桌面/机械臂自碰撞导致 rule baseline 无法达到 80%，则通过 `MotionPlanner` 适配器接入成熟的 RRT-Connect/现成规划组件。触发前保留失败统计，避免在问题尚未出现时提前扩大依赖。

轨迹接受条件：

- 所有关节在限制内；
- 末端目标误差在冻结阈值内；
- 规划态和执行态分别检查碰撞；
- 抓取后把物体外形纳入 carried-object 碰撞检查；
- 允许接触对明确列出，不能把所有接触都当碰撞，也不能全局关闭碰撞。

### 8.3 抓取成功证据

抓取后不能仅凭双指有接触就返回 completed。至少联合检查：

- 夹爪闭合宽度与目标尺寸相符；
- 抬升后对象底部离开桌面达到冻结高度；
- 对象在稳定窗口内随末端移动，相对位姿变化受限；
- 对象没有快速下落或只被单指偶然碰触。

place 在末端撤退后再次检查；夹爪托着物体经过目标区域不能算成功。

## 9. DeepSeek 规划规格

### 9.1 请求内容

system prompt 固定包含：任务角色、可见信息边界、技能目录、禁止输出未注册技能、只输出 JSON、歧义处理、完整示例和 schema version。

user payload 只包含：

- 原始任务文本；
- 当前实体及属性；
- 当前目标区域及标签；
- 已满足/未满足目标；
- 当前持有状态；
- 可用技能的参数 schema；
- replan 时的结构化失败摘要和剩余预算。

不发送隐藏 EvalSpec、故障配置、仿真 body ID、API key、整段无关历史日志或模型自己的旧长文本。

### 9.2 响应策略

使用 `response_format={"type":"json_object"}`，并在 prompt 明确要求 JSON。返回后执行：

1. 检查 HTTP、finish reason 与空内容；
2. JSON parse；
3. Pydantic schema 校验；
4. assignment 的对象/目标存在性和唯一性校验；
5. skill 名称和参数校验；
6. 符号级前置条件模拟；
7. 当前第一步的几何可行性预检查。

JSON mode 保证 JSON 语法，不保证本项目语义正确。任何未知字段、幻觉对象、遗漏目标和危险动作都不能直接执行。

格式或 schema 错误允许一次带错误摘要的修复请求。网络临时错误最多两次指数退避。空响应按官方文档描述视为可重试模型错误。认证/余额/配置错误立即终止在线模式。

### 9.3 Planner 可比性

- rule 与 DeepSeek 输出相同 `PlanningResult`。
- 在线评测记录 provider、实际返回 model、system fingerprint、prompt version、token usage、延迟和是否缓存命中。
- `temperature` 或等价采样参数在正式测试前冻结；不假设设置为 0 就完全确定。
- API 不可用时可以显式运行 rule 模式，但不得静默 fallback 后计入 DeepSeek 成绩。
- 可保存已校验模型响应用于离线回归；重放成绩必须标记为 replay，不计在线可靠性。

## 10. 故障注入与恢复

### 10.1 S1 故障

`GRASP_POSE_OFFSET_ONCE` 在指定对象的第一次实际 grasp approach 上叠加固定横向偏移，使双指无法形成有效抓取。偏移发生在执行输入，不直接篡改技能返回值。

故障配置包含：`fault_id`、目标对象、触发 attempt、偏移向量、seed。Agent 只能看到最终观察和 SkillResult，不能看到 fault 配置。

### 10.2 检测与恢复流程

```text
close gripper
  -> lift
  -> grasp verification = false
  -> SkillResult(failed, GRASP_MISS, evidence)
  -> safe retreat
  -> ensure gripper open
  -> observe new object pose
  -> recompute nominal grasp
  -> retry within per-object budget
  -> normal place and verification
```

RecoveryPolicy 不能假设第二次必成功。第二次仍失败时继续消耗预算，达到上限后受控失败并保存回放。

### 10.3 S1 其他错误的最低行为

| 错误 | S1 最低行为 |
|---|---|
| IK 无解 | `IK_UNREACHABLE`，不发送关节命令 |
| 路径碰撞 | `PATH_COLLISION`，不执行非法路径 |
| place 目标无空间 | `TARGET_FULL`，请求 replan 或退出 |
| 状态版本过期 | 重新观察和 precheck |
| LLM 非法计划 | `INVALID_PLAN`，有限修复，禁止执行 |
| 技能超时 | 停止下发、读取状态、标记 uncertain |
| 异常物体掉落 | 重新观察；S1 可受控退出，不要求自动捡回 |

只有 `GRASP_MISS` 必须在 S1 自动恢复。其他类型的“正确识别并安全终止”也属于有效工程行为，但任务记为失败。

## 11. 日志与回放

每个 episode 创建独立目录：

```text
runs/<run_id>/<episode_id>/
  config.snapshot.yaml
  task.json
  events.jsonl
  summary.json
  model_calls.jsonl
  frames/
  video.mp4
  diagnostics/
```

事件使用统一 envelope：

```json
{
  "schema_version": "1",
  "event_id": "evt_...",
  "episode_id": "ep_...",
  "sequence": 17,
  "sim_time": 4.25,
  "wall_time": "ISO-8601",
  "type": "SKILL_FINISHED",
  "payload": {}
}
```

必须记录：reset、观察、目标/计划版本、计划拒绝原因、skill 开始/阶段/结束、验证、恢复、replan、预算变化、终态、Evaluator 结果和异常。API key 永不写日志；prompt 可保存去密版本。

默认只保存关键帧和正式/失败 episode 视频，避免 RGB-D 数据无界增长。录像失败不能改变任务评分，但必须记为 artifact error。

## 12. 测试策略

### 12.1 Unit

- schema 接受/拒绝正确；单位、frame 与四元数校验。
- GoalSpec 不允许重复/不存在 assignment。
- PlanValidator 拒绝未知 skill、未知对象、错误参数和符号前置条件冲突。
- Runtime 所有循环都消耗预算；final verification 不会无限循环。
- “已放错目标”的对象仍是未完成，不能由永久 placed 标志跳过。
- UNKNOWN 不会被空列表或 truthy 对象误判为成功。
- 失败时不会无条件松开未知持有物。

### 12.2 Contract

同一组 backend contract tests 运行在 symbolic 和 PyBullet：reset、observe、robot state、seed、close、多实例隔离和错误传播。只测试共同契约，不要求 symbolic 模拟接触动力学。

### 12.3 Integration

- DIRECT 模式加载场景并稳定 1000 步。
- Panda 回 home、到达 pregrasp、夹爪开合。
- 单物体真实抓取、抬升、放置、撤退。
- 三物体 rule plan 完整闭环。
- 抓空注入产生物理失败并被检测。
- recovery 开启后重试，关闭后同场景失败。
- reset 后对象/机器人状态在容差内复现。
- Evaluator 能发现“程序声称成功但物体错误”的假成功。

### 12.4 LLM

- fixture 覆盖合法、截断、空内容、非 JSON、幻觉实体和遗漏目标。
- mock HTTP 覆盖 timeout、429、5xx、401 和重试预算。
- 在线 smoke 使用真实 DeepSeek，默认不随本地 pytest 运行。
- 保存一组经过校验的响应做 deterministic regression，但明确标记 replay。

## 13. 评测协议

### 13.1 任务集

| Suite | 配置 | 目的 |
|---|---:|---|
| `smoke` | 5 | 每次变更快速检查 |
| `dev` | 20 | 调物理、容差和 prompt；结果不作为最终成绩 |
| `s1-clean` | 3/4/5 物体各 10 配置，每配置 3 次 | 正式物理与端到端成功率，共 90 episodes/模式 |
| `s1-fault` | 30 个固定故障配置，每配置 3 次 | recovery on/off 配对比较，各 90 episodes |
| `negative` | 至少 12 个 | 歧义、非法实体、目标无空间、预算耗尽、空任务等 |

任务配置和 seed 在正式测试前冻结。针对正式集调整控制或 prompt 后，需要生成新的正式运行批次并记录迭代，不能覆盖旧结果。

### 13.2 运行矩阵

| 模式 | Planner | Recovery | 作用 |
|---|---|---|---|
| B0 | rule | off | 纯控制下限 |
| B1 | rule | on | 隔离恢复收益 |
| E0 | DeepSeek | off | 端到端无恢复基线 |
| E1 | DeepSeek | on | S1 最终系统 |

`s1-clean` 重点运行 B1/E1；`s1-fault` 四组都保留，至少 B0/B1 使用相同已验证初始计划以隔离 recovery。

### 13.3 指标

主指标：完整任务成功率，报告成功数/总数和 95% Wilson 区间。

恢复主指标：同一故障配置的成功率差，按配置做聚类 bootstrap 95% 区间。初步验收目标为 recovery on 相对 off 提升至少 20 个百分点且区间下界大于 0。

附加指标：每对象成功率、首次抓取率、目标解析准确率、计划有效率、故障检测准确率、恢复成功率、动作/replan/API 次数、执行时长、token、延迟、费用、人工干预、仿真崩溃数。

### 13.4 计分规则

- LLM/API timeout、仿真崩溃、预算耗尽和未处理异常都计端到端失败，并单列原因。
- 人工移动物体、改状态、跳过失败步骤或重启当前 episode 均计失败。
- GUI/headless 可以分别诊断；正式统计固定一种执行模式。
- `assisted_grasp`、fixture plan、response replay 单独报告，不得混入正式成绩。
- 场景生成失败只有在预先定义的合法性检查不通过时才可排除，并必须补足样本。

## 14. 实施里程碑

### M0：环境预检，预计 0.5～1.5 天

工作：创建 Python 3.11 环境；试装 PyBullet、schema/测试依赖；验证 GUI/DIRECT、Panda、IK、关节、夹爪、碰撞、相机和 1000 步稳定性。

退出条件：生成 `doctor` 报告和一个 smoke 视频/截图；确认 Windows 原生是否可用。若 PyBullet 官方包安装失败或关键行为不可用，转 WSL2 做同一预检；仍失败才评估 MuJoCo。

### M1：单物体真实抓放，预计 2～4 天

工作：场景、PandaController、顶抓 pose、IK、碰撞预检、真实夹爪接触、放置与几何验证。

退出条件：固定场景连续 20 次至少 18 次成功；无瞬移/隐藏吸附；保存失败样例。未达标则不开发 LLM。

### M2：三至五物体规则闭环，预计 2～3 天

工作：多物体空位分配、rule planner、逐 skill 观察/验证、当前状态重新计算未完成目标。

退出条件：`dev` 中三物体任务点估计达到 80%；五物体至少跑通；放错目标能被独立判失败。

### M3：协议、Runtime 和评测，预计 2～3 天

工作：迁移数据契约、有界状态机、事件日志、Evaluator、task suites、planned CLI。

退出条件：unit/contract/integration tests 通过；无无限循环；一次 episode 可重放决策和观看视频；B0/B1 可批量运行。

### M4：DeepSeek 结构化规划，预计 1～2 天

工作：模型适配器、prompt、JSON mode、schema/语义校验、有限修复、在线 smoke、成本日志。

退出条件：20 个 `dev` 指令的目标解析和计划有效率达到可定位执行问题的水平；E1 能完成三物体在线任务；fallback 状态可见。

### M5：物理抓空与恢复，预计 1～3 天

工作：故障注入、抓取证据、`GRASP_MISS`、安全撤退、有界重试和 recovery on/off。

退出条件：同一 seed 下关闭恢复会失败，开启后能从真实抓空完成；Agent 不读取 fault 配置。

### M6：稳定化与正式验收，预计 2～4 天

工作：冻结配置、运行全部 suite、错误归因、修复 P0/P1 问题、生成表格和视频、复现文档。

退出条件：满足第 2 节全部条件，或产出明确的未达标报告和下一步决策。预计总周期 10～17 个全职工作日；M1 完成后依据真实接触调试重新估算。

## 15. 计划中的 CLI

这些命令是实施契约，当前尚不存在：

```powershell
python -m embodied_agent.cli doctor --backend pybullet
python -m embodied_agent.cli sim-smoke --gui
python -m embodied_agent.cli run --task configs/tasks/demo.yaml --planner rule --gui
python -m embodied_agent.cli run --task configs/tasks/demo.yaml --planner deepseek --gui
python -m embodied_agent.cli evaluate --suite s1-clean --planner rule --headless
python -m embodied_agent.cli evaluate --suite s1-fault --planner deepseek --recovery on --headless
python -m embodied_agent.cli report --run-id <run_id>
python -m embodied_agent.cli replay --episode <episode_dir>
```

CLI 退出码区分：成功、任务失败、需要澄清、配置错误、基础设施错误。批量 runner 捕获单个 episode 异常后继续剩余任务，并保留原始异常。

## 16. 配置示例

```yaml
schema_version: "1"
task_id: organize_003
seed: 31003
instruction: "把红色物体放到左边区域，蓝色物体放到右边，其余放到中间。"
scene:
  scene_id: tabletop_s1
  object_count: 3
  objects:
    - {id: red_cube, shape: box, color: red}
    - {id: blue_block, shape: box, color: blue}
    - {id: green_cube, shape: box, color: green}
evaluation:
  assignments:
    red_cube: tray_left
    blue_block: tray_right
    green_cube: tray_center
  stable_time_s: 0.5
faults: []
```

在线模型配置只引用环境变量名：

```yaml
provider: deepseek
api_key_env: DEEPSEEK_API_KEY
base_url: https://api.deepseek.com
model: <validated-at-M4>
prompt_version: s1-plan-v1
timeout_s: 60
max_retries: 2
json_mode: true
```

API key 不进入仓库、配置快照、prompt、模型调用日志或视频元数据。

## 17. 风险、触发条件与处理

| 风险 | 触发条件 | 处理 |
|---|---|---|
| Windows/Python 安装阻塞 | M0 超过 1.5 天仍不能稳定运行 | 转 WSL2/Python 3.11；保留诊断记录 |
| 真实夹爪成功率低 | M1 低于 90%/20 次 | 先校准几何、摩擦、控制与抓取证据；缩小对象域 |
| 简单路径频繁碰撞 | 合法 dev 场景因路径失败导致低于 80% | 接入成熟运动规划器适配层 |
| DeepSeek JSON/语义不稳 | dev 计划有效率低或频繁空响应 | 改 prompt/示例、有限修复；不放宽验证器 |
| API 当前模型变化 | 配置模型不可用或能力变化 | M4 读取官方可用模型并冻结运行元数据 |
| 故障注入过强或过弱 | off/on 两组都总成功或总失败 | 只在 dev 校准物理偏移，test 使用冻结值 |
| 评测与 Agent 状态泄漏 | Planner/Skill 可访问 EvalSpec/truth provider | 阻止发布，增加构造与依赖检查 |
| 日志体积过大 | 单次批量运行超出预设空间预算 | 仅保存关键帧，保留全部结构化事件 |

不得用以下方式“解决”风险：隐藏吸附、降低判定标准直到通过、删除失败样本、让 LLM 读取正确答案、在正式集反复调参后仍称未见测试。

## 18. 开工顺序与第一个代码任务

实施严格按 `M0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6` 推进。M0/M1 先证明物理底座可靠，M2/M3 再固定上层契约，M4 才接云端规划，M5 用真实失败检验闭环。

第一个代码任务应仅包含：

1. 建立独立 Python 3.11 环境与依赖声明；
2. 实现 `doctor`/smoke 脚本；
3. 在 GUI 和 DIRECT 中加载 Panda、桌面、一个方块；
4. 查询关节、末端、夹爪和碰撞信息；
5. 执行一次 IK 到 pregrasp 并回 home；
6. 生成环境报告和截图。

这个任务不接 DeepSeek、不重构全部原型、不实现多物体 Agent。它的输出决定 Windows 原生路径是否成立，并为 M1 提供经过验证的机器人索引、坐标系和控制参数。

## 19. S1 之后的唯一直接出口

S1 验收后先分析错误分布，再选择 S2：

- 若主要问题是抓取/碰撞，先扩展操作技能和运动规划；
- 若主要问题是目标解析或计划，扩展任务组合与 planner 评测；
- 若特权状态与可用演示差距最大，进入 RGB-D 感知；
- 若长任务中局部失败是瓶颈，扩展 recovery/replanning；
- 只有积累足够 episode 后，才开始 Memory 检索和跨任务增益实验。

S1 结束时不自动进入 Self-Evolving、VLA 训练或大规模 benchmark。下一阶段由真实失败数据决定。
