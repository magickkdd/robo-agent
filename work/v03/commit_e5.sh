#!/usr/bin/env bash
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
$P work/v03/p0_fingerprint.py | sed -n '1,2p'
$P -m embodied_agent.cli freeze --check 2>/dev/null | tail -1
$P -m embodied_agent.cli prereg --check 2>/dev/null | tail -1
$P -m embodied_agent.cli em-pairs --check 2>/dev/null | tail -1

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
feat(P4'): E5 —— 文本通道接上 §5.4，RQ7 有了三个通道的读数

改动形状与 H-58 同族（SPEC §6 E5 授权，用户 2026-09-29 裁定"接"）：
- benchmark/planned_runtime.py
  * `TEXT_ARM_MODULES` 仍是**裸**回路能装的东西；`AlfredPlannedRuntime.installed_modules`
    把它作为类属性，门口读 `type(self).installed_modules` 而不是模块常量——否则一个把门
    自己放宽的回路仍会被一个它已无法覆盖的常量检查（H-58 当初在 MuJoCo 侧改的就是这一行）
  * 新增 `TEXT_EXPERIENCED_ARM_MODULES = TEXT_ARM_MODULES + ("episodic_memory",)` 与
    `AlfredExperiencedRuntime(EpisodicMixin, AlfredPlannedRuntime)`：mixin 在前，它的
    `_offer_memories` / `_record_feedback` / `_begin_episode` 就是回路调用的那三个实现；
    记忆缝以下的回路逐字不变，所以这一通道上的 full vs wo_episodic_memory 之差可归给模块
  * `_ablation_note` 说出库的状态，并照旧说明"这个文本后端不是 §9 的 full 系统"
- benchmark/runner.py：`run_slot` / `run_batch` 加 `--ablation` / `--experience-store` /
  `memory_task_kind`，默认全空即冻结的 v0.1 回路（所以不点名的批跑的还是它一直跑的那个）；
  集末经**同一个** `write_experience` 写残渣，summary 里的 `episodic` 块与桌面/MuJoCo 同构；
  slot 行上记 `arm`，因为一个说不清哪个回路产出了它的 run root，它的 memory_* 行没有意义
- benchmark/cli.py：`_arm_kwargs` 在封存 run root **之前**解析臂，一次报出全部理由；
  新增三个 flag
- tests/contract/test_v03_text_channel_memory.py（新，8 条），其中"裸回路仍然拒绝记忆臂"
  单列一条——那半边不能动，v0.2 §8 B20 正是据此把文本通道的 Memory 行记成 not measured，
  一次只放宽门而忘了拒绝，会把那句话变成假的

顺带修一个**被咬出来的**真缺陷：`cli.py:cmd_run` 里的 `if args.single:` 把 `--single 0`
（第一个 slot，是个真 slot）当成"没给"，于是跑了**整个** dev_train 段。E5 的探针本意跑两格，
实际跑了整个 12 格段两遍。改成 `is not None`，并用 AST 断言那一条（不用子串匹配：解释它为什么
重要的注释在源码里，子串测试会匹配到自己的注释）。

RQ7（同一套机制在三个通道上行为是否一致）的读数，来自那次跑歪的批——歪得有用：
- 记录面**一致**：三个通道都检索、都在集末写、都过同一个 `experience_from_episode`；
  库的 `store_size_before` 在文本通道上按集序 0→11 递增，一集一行
- 对照臂**真的关掉了**：12/12 集的 memory_* 全 0，库末态 0 行
- 产出**不一致**，而这正是 RQ7 问的那件事：文本通道 12 集里只有 **2 集**检索到相关行
  （slot003 检索 7 用 2；slot010 检索 2 用 0），而 MuJoCo 通道是 4/4 读集都检索到。
  同一套机制、同一道门、同一台抽取器，在两个通道上产出的相关率差一个量级

两处**花钱/事实**的更正，不静默改：
- E5 的设计写着"零花费（rule 规划器）"，那是**错的**：文本通道的 run_slot 只有模型决策源
  （`LLMDecisionSource`），没有规则策略这一路。所以这两批各 12 集跑在**模型席**上，
  full 398,278 tokens、对照 342,112 tokens，合计约 740k。端点是免费层所以没有钱，但
  "零花费"这句作为预登记里的断言是错的，改正记在这里。
- E5 的读格脚本按目录排序取第一个 episode，于是"读集"那一格读的是种子集（slot000 排在
  slot001 前），而真正跑了 rc=1 的读集根本没被量到。已改为按 slot 读；上面那两个检索数
  是从 progress.json 按 slot 读出来的，不是从那个错脚本。

验证
- 桌面解释器：PYTHONPATH=. python -m pytest tests/ -q -p no:randomly -> 1200 passed, 24 skipped
  （+1 skipped 是那条需要 textworld 的 runner 拒绝测试，按解释器跳过，断言本身没削弱）
- 文本解释器 /home/czx/bstvenv/bin/python：本文件 + test_bst_text_backend.py -> 39 passed
- schema_fingerprint() 与 v02_schema_freeze.json 逐项相等；freeze / prereg / em-pairs 退出码 0
MSG

echo
git rev-parse HEAD
git status --porcelain | wc -l
