#!/usr/bin/env bash
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python

echo "=== frozen contract ==="
$P work/v03/p0_fingerprint.py | sed -n '1,2p'
$P -m embodied_agent.cli freeze --check 2>/dev/null | tail -1
$P -m embodied_agent.cli prereg --check 2>/dev/null | tail -1
$P -m embodied_agent.cli em-pairs --check 2>/dev/null | tail -1

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
fix(P0'): 一次没测成的观测不该占一个版本号（相机臂 "two ledgers"）+ 429 退避

一、相机臂的 state_version 记账缺陷（既有缺陷，本轮才变得可达）
  E2 探针批第一集死在
    RuntimeError: two ledgers: the tracker stamped v12 for the snapshot the loop asked for as v13
  这道守卫（perception/arm.py:378）是好的，它要抓的正是"两份账对同一张快照说了不同版本"。
  它当时抓的是别的东西：`Runtime.observe()` 在**测量之前**就把 `_obs_seq` 与
  `state_version` 各加一，而一次没有产出快照的捕获（被拒的 look、死掉的请求、渲染器故障）
  会把回路的计数器永久留在相机 tracker 前面一格，之后每一次成功的 look 都撞这道守卫。
  实测形状：探针批里一个 429 杀掉一次 look，下一次成功的 look 就报 v13/v12。
  privileged 通道读的是模拟器自己的状态，不会这样失败——所以这道守卫在相机通道可达之前
  从来没响过（通道被它自己那道 readiness 门挡着，见 063b527）。

  修法在比较的**上**一侧：没测成的观测不留下版本号，把它本来要用的 ref 也还回去。
  守卫一个字没动，强度不减——真的分叉了照样拒。附 `tests/contract/test_v03_two_ledgers.py`
  （5 条），其中"守卫仍然拒"与"两者一致时通过"各一条，删掉守卫满足不了前一条；
  "privileged 批一次没变"单列一条，把 v0.1 那条回归钉住。
  顺带记一个覆盖洞：这道守卫此前**没有任何测试**，所以它响的时候没人知道它在守什么。

二、429：退避 2 -> 4 次尝试，不换席（用户 2026-09-29 裁定）
  新文件 `configs/models/agnes-vision-r4.yaml`，与 `agnes-vision.yaml` 逐字只差
  `max_retries`（2 -> 4）——那份文件的字节属于 E2 探针批，manifest 记着它的
  `config_sha256`，所以按它自己立下的规矩另立文件。退避 `sleep(1.5*attempt)`
  = 1.5+3+4.5+6 = 15 s 跨 5 次尝试，与 `bst_text_agnes.yaml` 同一处置。
  探针批 23 次请求里 2 次死于 429，两集的 `ok=false` 行都在 `model_calls.jsonl` 里：
  tokens 记 0 是对的（429 没有生成发生），计的是 `http_requests_this_call=3` 次尝试。
  停批规则照 SPEC §8.2：连续 3 次死于 429/额度类即停批、存断点、落账。

验证
- PYTHONPATH=. python -m pytest tests/ -q -p no:randomly -> 1193 passed, 23 skipped
- schema_fingerprint() 与 v02_schema_freeze.json 逐项相等；freeze / prereg / em-pairs 退出码 0
MSG

echo
git rev-parse HEAD
git status --porcelain | wc -l
