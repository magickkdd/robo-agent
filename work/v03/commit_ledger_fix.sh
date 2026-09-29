#!/usr/bin/env bash
# Commit the two authorised P0' fixes plus the repair of a regression this round
# introduced. Frozen contract re-measured first: a product change that moved the
# schema would be a re-freeze decision, and the SPEC forbids one this phase.
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python

echo "=== frozen contract ==="
$P work/v03/p0_fingerprint.py | sed -n '1,8p'
$P -m embodied_agent.cli freeze --check 2>/dev/null | tail -1
$P -m embodied_agent.cli prereg --check 2>/dev/null | tail -1
$P -m embodied_agent.cli em-pairs --check 2>/dev/null | tail -1

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
fix(P0'): 感知请求的账本行（残留 #109）与 manifest 的 offline 字段

E2 要交付 §11 Cost 组的 tokens / latency 两列，而这两列今天没有任何落盘处可读——不是
缺一列，是缺一个分母。探针批实测：一次真 look 花了 4096 个 completion token 死在零正文
上，全盘没有一行账。

一、#109：感知请求不往 model_calls.jsonl 里写行
  `VLMReader` 一直在为**每一次**视觉请求造一行（含失败的那次，因为被拒的 look 也是计费的
  look），`observe.py:_file_row` 在没拿到回调时直接 return，于是这行被扔在地上。
  根因是 `build_perceiver` / `build_arm` 没有 `on_call` 形参，调用方也就无处可传。
  修：arm.py 两个 builder 加 `on_call=None` 并交给 VLMReader；run.py 把 log_call 上移，
  新增 `log_perception_call`（一行 -> 同一个文件、同样的 episode/mode/case 标注），
  两个 build_perceiver/build_arm 调用点都传下去。

二、manifest 的 `offline` 只看决策席
  `planner_kind != "deepseek"` 是不完整的半个问题。实测 `--planner rule --perceive vlm`
  那一批明明按 look 计费，manifest 写 `offline: true`。Cost 组就读在这个字段旁边的列上，
  所以一个低报花费的标志会污染它所限定的那组读数（§7 不许把 VLM 集报成免费的，同一条
  理由）。修：`_batch_is_offline(planner_kind, perceive)`，两个写出点共用。

三、本轮自己造的一个回归（登记）
  删 `DeepSeekPlanner` 里误插的重复块时，把 `from_env` 上面的 `@classmethod` 一起删掉了，
  于是 `DeepSeekPlanner.from_env(path)` 把 path 当 cls → `TypeError: 'str' object is not
  callable`，每个 `--planner deepseek` 批会在第一集就死。**1187 条测试全绿也没抓到**，
  因为套件里没有任何地方用路径调它——那正是 E1 的调用方式。已修，并补一条按名打开这道门的
  测试（`test_a_config_path_builds_a_planner_and_an_adapter_the_same_way`）。

四、配置的自述被自己的测量推翻（就地改写，不静默留）
  `configs/models/sensenova-vision-4k.yaml` 的头部原来写着"本文件是 E2 的视觉席"。
  三次测量后这句话不成立：同一段生产提示（prompt_sha256 8668a4af2d320e68…）、同一张帧，
    max_tokens 1,200 / 4,096 / 16,384 -> finish_reason 全是 length，reasoning_tokens
    分别吃满 1200 / 4096 / 16384，raw_chars 三次都是 0
  对照一次：`agnes-vision.yaml` -> stop、无推理 token、1098 字符合法 JSON、477 completion。
  ⇒ 把预算调大这条路走不通，问题在这个被试，不在提示、不在主 CLI、不在预算。
  E2 的席改为 `agnes-vision.yaml`（用户 2026-09-29 裁定），该文件因此改写为一份具名负结果，
  两次"trivial 提示下它能答"的测量一并留在上面——它们不错，只是外推不了。

验证
- PYTHONPATH=. python -m pytest tests/ -q -p no:randomly → 1188 passed, 23 skipped
- schema_fingerprint() 与 configs/experiment/v02_schema_freeze.json 逐项相等
- freeze / prereg / em-pairs 三条退出码 0
- 实测（E2 探针批 1 集 x 2 repeats，--perceive vlm，agnes-vision.yaml）：
  * `perception` 记录 **11 + 9 = 20 条**（v0.2 臂根论域：0 条）——A2 的记录面翻转
  * model_calls.jsonl **13 + 10 = 23 行**，含 2 行 `ok=false` 的 429 死亡行
    （p=0 c=0 是对的：429 没有生成发生，计的是 `http_requests_this_call=3` 次尝试）
  * tokens 真数：prompt 29,736 / completion 8,254；latency 逐行 2.4–11.8 s
  * manifest `offline: false`（改前为 true）
  * 两集都没跑完：一集死在 `RuntimeError: two ledgers: the tracker stamped v12 for the
    snapshot the loop asked for as v13`（相机臂的 state_version 记账缺陷，**新发现，未修**），
    一集死在 429。⇒ 记录面与 Cost 组已可读，"完成一集"还不行。
MSG

echo
echo "--- committed ---"
git rev-parse HEAD
git status --porcelain | wc -l
