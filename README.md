# Embodied Agent Framework — S1 物理闭环 MVP

自然语言输入 → 场景感知 → 技能组合 → 多步骤桌面整理 → 失败检测与恢复 → 独立评分。
本仓库实现规格文档 `Embodied-Agent-Framework-Spec-v1.0.md` 的 **S1 阶段**：固定 Franka Panda 在
PyBullet 桌面场景中通过真实夹爪接触完成 3～5 个物体的整理归位，DeepSeek 输出结构化计划，
Runtime 逐步验证并处理可复现的抓空故障。旧二维接口原型保留为测试替身（`embodied_agent` 顶层模块）。

> **当前有效规格是 `Continuous-Decision-Embodied-Agent-SPEC-v0.1.md`**（P0–P3 已执行完毕，逐阶段
> 记录见 `docs/continuous-decision-phase-log.md`）。上面这段与本文后半部分的 S1 措辞是历史背景：
> 其中"Runtime 处理抓空故障"等说法已被现版本取代（Runtime 不再替模型选策略），运行命令与产物名
> 已按现 CLI 更正如下。

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

# 离线：全部必需测试（无需网络、无需 key；在线用例无 RUN_ONLINE=1 时整档 skip）
PYTHONPATH=. python -m pytest -q

# 环境与依赖自检
PYTHONPATH=. python -m embodied_agent.cli doctor

# 一条 episode，一个臂（规则策略，离线、零成本；产物写进 /tmp 以免污染仓库）
PYTHONPATH=. python -m embodied_agent.cli run --task clean:clean_c1 --mode B --planner rule \
  --out /tmp/single

# 一批（三臂 A/B/C 同一底座、同一 goal 解析）；--set 取 smoke / dev / clean /
# state_change / execution_deviation / formal / protocol / all
PYTHONPATH=. python -m embodied_agent.cli evaluate --set clean --modes A,B,C --planner rule \
  --out-root /tmp/batch
PYTHONPATH=. python -m embodied_agent.cli report --run-dir /tmp/batch/<run_id>
PYTHONPATH=. python -m embodied_agent.cli replay --run-dir /tmp/batch/<run_id> --recorded

# 在线模型：先配 key（值只放环境变量或仓库内 .env；SPEC 7：不入配置/日志/提示词）
#   默认被试 = configs/models/deepseek.yaml（v1 预注册的那一个）
#   免费档被试 = configs/models/agnes.yaml（agnes-2.5-flash，api_key_env: LLM_API_KEY）
set -a; . ./.env; set +a          # 或直接 export LLM_API_KEY=…
PYTHONPATH=. python -m embodied_agent.cli run --task clean:clean_c1 --mode B \
  --planner deepseek --model-config configs/models/agnes.yaml --out /tmp/online

# 冻结自查：任务清单 / 预注册（阈值、矩阵、prompt、预算、被试）
PYTHONPATH=. python -m embodied_agent.cli freeze --check
PYTHONPATH=. python -m embodied_agent.cli prereg --check
```

无 key 时 `--planner deepseek` **显式报错并拒绝开跑**，不会静默换成规则策略。带 `--prereg` 的批次会
把实际 `base_url`/`model`/矩阵与冻结值逐条比对，不符即拒绝且不产生任何目录——所以换 provider 的批次
不能冒充 v1，除非另立一份 v2 预注册（`configs/models/deepseek.yaml` 的字节被哈希进 `rules_sha256`，
因此换被试只能新增配置文件）。

## 产物

批次目录（`evaluate`）：`manifest.json`（代码 commit + dirty diff 哈希 + 依赖 + 被试与采样 + 冻结清单
与预注册状态；**不记凭证、不记环境变量**，代理只记 kind）、`episodes.csv`、`run_summary.json`、
`task_manifest.json`、`goal_resolutions/`（每 case 一次共享解析及其账本）、
`episodes/<episode_id>/{events.jsonl, episode_summary.json, model_calls.jsonl, frame_NNN_<skill>.png}`、
`report.json` / `report.md`；跑过 `state-util` 后另有 `state_utilization.json`。
`run` 只产生该 episode 的目录与 `goal_resolutions/`，不写批次级清单与表。
成功率、Wilson 区间、按 case 聚类的配对 bootstrap、行为与成本指标、失败归因都在 `report.md` 开头。

## 成功率分母与统计口径

主指标 = 独立判分的完整任务成功 episodes / **预定运行的全部 episode**（失败、超时、被拒、澄清、
基础设施错误都留在分母里，不事后剔除）。
- 配对增益按 case 聚类 bootstrap（次数与 seed 都在冻结文件里），Wilson 区间只作描述。
- S1 时期的 A1–A4 目标已由 `configs/experiment/p3_preregistration_v1.json` 的 G1–G5 取代；
  v1 批次结果：G1–G4 达标、G5（状态对 10/12）未达 ⇒ 门槛组整体 `not met`，逐条见
  `docs/continuous-decision-phase-log.md` 的 P3 §5 与末尾审计。
- 评测集为自建工程集合（TabletopOrganize-v1），不是公共 benchmark。

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
- 在线被试由 `configs/models/*.yaml` 指定：默认 `deepseek.yaml`（v1 批次的那个），`--model-config
  configs/models/agnes.yaml` 走免费档 `agnes-2.5-flash`。密钥只从环境变量或仓库内 `.env` 读，
  配置里只出现变量名。仓库内的 LLM 指标不再只有 fixture：`/tmp/p3d_formal/…` 是 216 episode 的
  真实在线批次，其结论只属于当时那个被试。

## 成本

请求数、prompt/completion tokens、逐调用延迟与墙钟占比全部**实测**并出表（`report.md` 的 Costs 与
`model_latency`）；金额只在配置了 `pricing_usd_per_mtok` 时才出现，否则 `cost_estimate_usd` 保持
`null`（SPEC 11.4：不臆造价格）。v1 那一批为 1,631 请求 / 7.19 M prompt tokens / 47.3 min，其中
B 相对 A 多出的 11 条成功花了 +706 请求与 +4.17 M tokens——增益与代价必须同框读。
