#!/usr/bin/env bash
# Commit the P0' gate fix. Preconditions are re-measured here rather than assumed:
# the frozen contract must be byte-identical (a product change that moved the
# schema would be a re-freeze decision, and the SPEC forbids one this phase),
# and the identity fields must say what the commit message will say.
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python

echo "=== frozen contract ==="
$P work/v03/p0_fingerprint.py | head -8
$P -m embodied_agent.cli freeze --check | tail -1; echo "freeze_rc=$?"
$P -m embodied_agent.cli prereg --check | tail -1; echo "prereg_rc=$?"
$P -m embodied_agent.cli em-pairs --check | tail -1; echo "em_rc=$?"

echo
echo "=== the change ==="
git --no-pager diff --stat

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
fix(P0'): 主 CLI 的 --perceive vlm 此前结构性不可达，两道门都没问它真正的问题

SPEC-v0.3 §6 E2 要"主 CLI 上 --perceive vlm 首次产出 perception 记录"。P0' 预检
实测：该命令无论是否传 --model-config 都以 rc=3 被拒，拒绝句**逐字相同**，而那句
话让操作者去传 --model-config。

根因是一处缺陷、两处调用点：
- cli.py:327   `channel_readiness(args.perceive)`      —— adapter 恒为 None
- run.py       `channel_readiness(perceive)`          —— 同上，第二道门
对照两处本来就对的：perception/arm.py:515 与 benchmark_mujoco/perceive.py:2509
都传了 adapter。所以不是"没配视觉模型"，是这条路上根本没有 adapter 可传：
run_group 无 adapter 形参，run_one_episode(adapter=None) 从未被填。

修法（SPEC §12 允许：不新增模块、不新增事件类型）
- adapters/deepseek.py
  * `DeepSeekAdapter.from_config`：把"一份 yaml 变成一个 adapter"收成一处，
    从 `DeepSeekPlanner.from_env` 原样拆出（含 DEEPSEEK_MODEL/DEEPSEEK_BASE_URL
    覆盖与 .env 回退，行为不变）。原先只有造 planner 才能拿到 adapter，于是
    `--perceive vlm` 只能与 `--planner deepseek` 同时出现——最便宜的那个对照
    （只换感知通道、决策仍走 rule）连问都问不出来。
  * `vision_capability` 从 benchmark_mujoco/runner.py 移到这里，bench 侧改为
    再导出（tests/contract/test_mujoco_channel.py 的 import 不变）。"这个配置
    能不能看图"只该有一个答案；主 CLI 正是因为没有才自己长了一套更弱的问法。
- perception/grounding.py: `channel_readiness(channel, adapter=None, config_path=None)`。
  给了 config_path 就问配置（读 `capabilities:`，而不是问 adapter——`chat_vision`
  是每个端点都有的方法，callable(adapter.chat_vision) 对纯文本模型同样为真）。
  不给 config_path 的调用方（相机侧两个 builder）保留原句与原强度。
- evaluation/run.py
  * `run_group(..., adapter=None)`，经 `_camera_adapter()` 构造并交给
    `run_one_episode(adapter=...)`；优先级上先过门再构造 adapter，因为门只需
    读 yaml，构造还需要能装载 key——"配置声明了 vision 但背后没有 key"是另一个
    失败、另一句话，两者混成一句就等于谁也修不好。
  * 缺 key 时抛 InfraError 并**点名 api_key_env 那个变量名**（不点名就没法修；
    值从不读取，SPEC 7）。
  * 相机通道的默认臂由 `wo_vlm` 改为 `full`，与 cli._perception_kwargs 一致：
    原先 `run_group(perceive="vlm")` 不带臂会被下一行以
    "channel 'vlm' cannot carry arm 'wo_vlm'" 拒绝——那句话描述的是代码的默认值，
    不是操作者的命令。
- tests/contract/test_v03_vlm_channel_reachability.py（新，7 条）

已知未修（登记，不静默改）：manifest 的 `offline` 字段只看 planner
（run.py:972/1033 `planner_kind != "deepseek"`），所以 `--planner rule
--perceive vlm` 这一批明明花了钱，manifest 却写 `offline: true`。这条要单独修，
因为 Cost 组要读的正是这个字段旁边的列。

验证
- PYTHONPATH=. python -m pytest tests/ -q -p no:randomly → 1180 passed, 23 skipped
- schema_fingerprint() 与 configs/experiment/v02_schema_freeze.json **逐项相等**
  （schema_version 4 / 15 事件类型 / 7 消融条件未动；module_sha256 只哈希
  core/v02.py，本次未改）
- freeze --check / prereg --check / em-pairs --check 三条退出码 0
- 实测（1 集，计费 1 次请求）：`--perceive vlm --model-config
  configs/models/sensenova-vision-4k.yaml` 越过两道门，manifest 记下
  `perception {channel: vlm, arm: full}`，渲染出 look_0001_main.png
MSG

echo
echo "--- committed ---"
git rev-parse HEAD
git status --porcelain | wc -l
