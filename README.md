# Embodied Agent Framework — S1 物理闭环 MVP

自然语言输入 → 场景感知 → 技能组合 → 多步骤桌面整理 → 失败检测与恢复 → 独立评分。
本仓库实现规格文档 `Embodied-Agent-Framework-Spec-v1.0.md` 的 **S1 阶段**：固定 Franka Panda 在
PyBullet 桌面场景中通过真实夹爪接触完成 3～5 个物体的整理归位，DeepSeek 输出结构化计划，
Runtime 逐步验证并处理可复现的抓空故障。旧二维接口原型保留为测试替身（`embodied_agent` 顶层模块）。

## 支持范围（S1 边界）

- 场景：固定 Panda（PyBullet `franka_panda/panda.urdf`，基座抬高 0.30 m）、桌面、3 个浅托盘
  （tray_left / tray_middle / tray_right）。
- 物体：方块、长方体、直立圆柱（3～5 个），属性（颜色/形状）来自特权状态 —— **文档与演示明确标注**。
- 语言：显式物体归位、按属性分类、"剩下的/其余"、全部归入一类；歧义时返回
  `needs_clarification`，不猜测。
- 技能目录（LLM 可见）：`pick / place / observe / safe_retreat`；`verify` 由 Runtime 强制触发。
- 恢复：仅 GRASP_MISS（抓空）可自主恢复（重新观察 → 安全撤退 → 换抓取候选 → 有界重试）；
  其余失败类型正确报告并有界终止。

## 安装与运行

```bash
# Python 3.11（conda env `embodied`，依赖锁定见 requirements.txt）
pip install -r requirements.txt

# 冒烟（5 用例：正常、属性分类、5 物体、一次抓空故障、非法目标）
python -m embodied_agent.evaluation.run evaluate --set smoke --planner rule --out runs

# 负例（10 用例：歧义、不存在实体、未知目标、空目标……）
python -m embodied_agent.evaluation.run evaluate --set negative --planner rule --out runs

# 固定验收集（90 episodes）与故障配对实验
python -m embodied_agent.evaluation.run evaluate --set s1_clean  --planner rule --out runs
python -m embodied_agent.evaluation.run evaluate --set s1_fault  --planner rule --recovery on  --out runs
python -m embodied_agent.evaluation.run evaluate --set s1_fault  --planner rule --recovery off --out runs

# 单集运行
python -m embodied_agent.evaluation.run run --set smoke --planner rule --out runs

# 在线 DeepSeek（需真实 key；无 key 时会显式报错，不会静默回退到规则规划器）
export DEEPSEEK_API_KEY=sk-...
python -m embodied_agent.evaluation.run run --set smoke --planner deepseek --out runs

# 轨迹回放（重放已记录计划，展示既有执行，不冒充重新在线完成任务）
python -m embodied_agent.evaluation.run replay --run-dir runs/<episode_dir>
```

## 产物

每次运行目录包含：`config.json`（场景/任务/预算/故障配置快照）、`events.jsonl`
（episode/plan/skill/observation ID 串联的逐事件日志）、`summary.json`、`final_frame.png`、
错误诊断。集合级产物：`report.json`（成功率 + Wilson 95% 区间 + 按物体数分层）、`results.csv`。

## 成功率分母与统计口径

主指标 = 完整任务成功 episodes / 全部有效正式试验（所有约束满足、无人工干预）。
- A1 规则基线：S1-clean ≥ 80%。
- A2 在线 DeepSeek：同一域 ≥ 80%（真实物理夹取、无隐式回退）。
- A3 恢复增益：S1-fault 中 recovery 组相对 no-recovery 组正向增益，配对差异 95% 区间下界 > 0。
- A4 全部 negative 用例预算内终止/澄清，非法动作不执行。
- 评测集均为自建工程集合（TabletopOrganize-v1），不是公共 benchmark。

## 物理真实性边界

所有抓取/放置通过物理接触与夹爪约束力完成；禁止执行途中瞬移物体或吸附。
仿真 reset 仅用于规划影子状态，不污染执行世界。故障注入器只在第一次抓取的下降目标中
引入冻结偏移（幅度在 dev 集标定后冻结），Agent 无法读取"这是注入失败"。

## 已知限制

- 特权状态模式：S1 的 WorldState 来自仿真真值通道（`source=privileged`）；S3 起替换为允许的
  传感器通道，评分器真值永不回流。
- 抓取候选为几何启发式（物体顶面中心 + 少量横向偏移），非学习型。
- 转移采用开阔场景的"抬升—平移—下降"栅栏策略，不声称通用避障。
- 平台：WSL2/Ubuntu（Windows 原生因规划依赖未验证而未采用，见 `outputs/w0_precheck/`）。
- 在线 DeepSeek 指标（A2）需要 `DEEPSEEK_API_KEY`；当前仓库内的 LLM 指标均来自 fixture
  模式（仅离线测试用，不冒充在线结果）。

## 成本

暂无人民币预算目标。小规模调用的 token/延迟记录在 episode 的 `model_response` 事件中
（provider/model/usage），批量费用按当时实际单价估算。
