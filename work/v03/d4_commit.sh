#!/usr/bin/env bash
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
feat(v0.2): 持续决策 v0.2 交付落盘 —— 九模块闭环、消融读数与两个未闭合的席位

把 v0.2 全程未提交的工作树封存成一个 commit，使产物身份从"工作树快照哈希"
换成真提交（SPEC-v0.3 §9 P0' D4，用户 2026-09-28 授权"提交，并打 tag"）。

交付文档
- docs/continuous-decision-v0.2-final-report.md（§15 判定：第二半达成、第一半不达成）
- docs/continuous-decision-v0.2-phase-log.md（P0…P5 与附录 H 的执行纪律）
- docs/bst/（Benchmark 压力测试 SPEC 的阶段日志、兼容性裁定、标注准则）
- Continuous-Decision-Embodied-Agent-SPEC-v0.2.md / -v0.3.md / Benchmark-Stress-Test-v0.1-Agent-SPEC.md

代码（此前 77 个 .py 完全未被 git 看见，untracked_code_state 只能给一个内容哈希）
- embodied_agent/{planning,perception,episodic,acquisition}/ 九个模块
- embodied_agent/{benchmark,benchmark_mujoco}/ 文本与 MuJoCo 两条外部通道
- embodied_agent/core/v02.py 15 类事件 + 7 条消融条件的 schema-4 契约层
- embodied_agent/evaluation/ LH-2 / MEM-1 / SKILL-1 / V1-V3 / 预注册六组仪器
- tests/contract/ 31 个契约测试文件（含 H-58 的 MuJoCo 侧 §5.4 接线四判据）

封存配置
- configs/experiment/{v02_schema_freeze,frozen_long_horizon_v1,frozen_episodic_v1,
  p3_preregistration_v1}.json
- configs/models/{agnes,agnes-vision,bst_text_agnes,sensenova-vision,deepseek}.yaml
  （只记 api_key_env 的变量名，键值从不入库；.env 已被 .gitignore 排除）

身份对照（提交前实测，两处都与已发布读数不同，登记而不静默改）
- git_commit             4e960a2d2d189cb6d20e19910f83c6f76361f906 -> 本提交
- dirty                  True -> False
- dirty_diff_sha256      报告 §0 发布 b43d31f369f688d3；本轮按 manifest 用的
                         core.events.git_state() 重跑得 1eada71e66594cc2
                         （同一条 git diff --binary 直接管道给 sha256sum 得
                         235be136787d6727，差在尾部换行的处理；两者都不复现）
- untracked_code.sha256  625f7652a4918ef2（77 个 .py）-> 本提交后 files=0
  原因：dirty_diff_sha256 只哈希**被跟踪**文件，而 v0.2 期间被改动的被跟踪文件
  晚于报告快照才落定；真正随 H-58 移动的是 untracked_code 那一列。

回归
- PYTHONPATH=. python -m pytest tests/ -q -p no:randomly
  → 1173 passed, 23 skipped（0:07:59），rc=0

卫生
- .gitignore 增加一行 MUJOCO_LOG.TXT：一次失败 GUI 尝试留下的 GLFW 初始化日志，
  是运行时产物不是证据（同类内容在阶段日志里有）。
MSG

echo "--- committed ---"
git rev-parse HEAD
git status --porcelain | wc -l

git tag -a v0.2 -m "Continuous-Decision v0.2: architecture complete, measurement incomplete (see docs/continuous-decision-v0.2-final-report.md §12)"
git tag -a continuous-decision-v0.2 -m "same commit as tag v0.2; the SPEC file names this one"
echo "--- tags ---"
git tag -l -n1
