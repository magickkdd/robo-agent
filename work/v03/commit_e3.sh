#!/usr/bin/env bash
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
$P -m pytest tests/contract/test_v03_two_ledgers.py tests/contract/test_v03_vlm_channel_reachability.py -q -p no:randomly 2>&1 | tail -3

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
feat(P1'): E3 批 —— MuJoCo 通道的情景记忆对照，RQ6 有答案了（零花费）

4 对 x {full, wo_episodic_memory} x 2 集 = 16 集，全部 rc=0，--planner rule
--perceive privileged 一分钱不发。产物 /home/czx/embodied-agent-batches/v03/e3/。

四条判据逐条有读数（configs/experiment/v03_e3_preregistration.json 预登记在 b918f55）：
1. read 集（full 臂）出现非空 memory_retrieval 且库在种子集与读出集之间从 0 长到 1
   —— 4/4 对，种子集的检索记录 store_size=0 且 retrieved=[]（冷启动读数本身），
   读出集 3 次检索全部 retrieved 非空
2. 对照臂 8 集 memory_retrieval / memory_use / memory_write **全 0**，库末态 0 行
3. 16/16 集的 episode_summary.json 都带 episodic 块
4. 离线 experience_from_episode 重导出与在线写入行 **8/8 逐字段相等**（21 个字段，
   experience_id 也相等）——H-58 的判据在批上复核通过

RQ6 的答案分两半写，因为两半的证据强度不同：
- 检索与使用：**有，且可测**。读出集每集 3 次 memory_use，retrieved/shown/used 三列同名，
  并且每一轮都落下判决——两轮 "True"（证据 `pick peg 1 -> ` 与 `place peg 1 -> socket 1`），
  一轮 "unknown" 且 unmeasured 写着"这一轮没有可比较的已执行技能"。机制在无法比较时
  拒绝下判决，这是正确的形状。
- 收益：**没有**。读出集执行的两个技能与关掉记忆时本来就会执行的两个相同，所以
  full 与 wo_episodic_memory 的差别在"页面说了什么"，不在"回路做了什么"。这与桌面
  MEM-1 的 `followed_where_control_chose_the_same 0.4286` / `outcome_improvement 0/4`
  是同一个限制从另一条路走到，SPEC §3 RQ6 不让桌面结论自动迁移那句话由这批作证。
- K=4、n=4：这是机制在这个通道上的**存在性证明**，不是一个率。
- 这一条通道只有一套任务词表（TASKS 里只有 peg-insert-side-v3）但有 35 个 layout，
  所以"一个之后才出现的相似任务"在这里只能表达成换 layout——比 MEM-1 的 4 个不同
  任务对窄。限制写进产物，不留给读者猜。

判据本身更正一处（就地记录）：预登记写的是"store_size 随对序递增"，那假设了一份
**共享**的库；而每对一个隔离库才使"对"成其为对。递增的真实读法是**对内**的
（种子集首次 look 时 0 → 读出集首次 look 时 1，每对末态 2 行），已写进
e3_rq6_answer.json 的 design.store_isolation，而不是留成一条读不出来的判据。

判据 4 还有一次**读错**的过程也留在产物里（e3_criterion4.json 的 method.supersedes）：
第一版把合成的一条事件喂给提取器、并且把 created_at/updated_at 也算进比较，报 0/8
相等。那是仪器错不是批次错——提取器按自己文档的两种输入形状读日志末的 plan view，
合成体会丢掉它据以派生内容的那些字段，而那两个时间戳是记录信封的构造戳、不是经验内容
（H-58 的契约测试正是只剥这两个）。改正后 8/8。

本轮驱动自身的三处错也记下来：
- 入口是 `-m embodied_agent.benchmark_mujoco.cli`，不是 `-m embodied_agent.benchmark_mujoco`
  （后者是包、退出码 1、0.04 s）
- MuJoCo 后端要 `metaworld`，它装在 /home/czx/mwvenv 这个**独立 venv** 里；那个
  venv 的 bin/python 是指向桌面 conda 解释器的**符号链接**，而这恰恰是它能工作的原因——
  Python 按**被调用的那个路径**去找 pyvenv.cfg。后端自己的拒绝句（env.py:117）指的就是
  这个解释器，指得对；我一开始读成"它指错了"，那是我的读法错。
- 复现对的定义按**参数**写而不是按集种子写：集种子 = `--seed + task_index`
  （benchmark_mujoco/cli.py:238），所以 p3（同 layout、换种子）必须是 `--layouts 0
  --seed 0` 接 `--layouts 0 --seed 1`；按集种子写会把同一集跑两遍。
MSG

echo
git rev-parse HEAD
git status --porcelain | wc -l
