#!/usr/bin/env bash
# Commit the three v0.3 pre-registrations. They are committed *before* any batch runs,
# which is the whole point of writing them: a pre-registration that can still be edited
# after the first request is not a pre-registration.
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python

$P work/v03/write_prereg.py | tail -6

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
chore(v0.3 P0'): 三份预登记（E1 计费 2,484 / E2 待探针批定规模 / E3 零花费）

SPEC-v0.3 §8.1 要求每个计费批次的预登记载明四格：调用数上界、单价来源（或明示
pricing_configured=False）、停止规则、断点策略；§4.12 另外要求产物目录不落 /tmp 单点。
三份都在 configs/experiment/v03_*_preregistration.json，由 work/v03/write_prereg.py 生成，
**没有一个数是手打的**：

E1  调用数上界 2,484 = 1,656 x 1.5
    1,656 是当场重数 P5-c 每格的 `decision` 记录（lh 888 + em 600 = 1,488）加每集一次
    目标解析（168）得来的，与 v0.2 报告 §8 A1 引的同一个数和同一条公式一致——重数而不是
    转引，是为了让"上界"这件事在跑之前就能被盘上产物证伪。产物
    /home/czx/embodied-agent-batches/v03/p0_preflight/e1_budget_basis.json
    单价：pricing_configured=False，USD=null（免费端点、无价；编数不合法）
    停止规则：连续 3 次 429/额度类即停批存断点；上界撞顶即停；单集触顶属正常终止
    断点：以格为单位推进，跑完即写格级摘要，重启只续未完成格，已付费的格不重跑
E2  上界按每集冻结的 max_http_requests（lh_c1 是 32），正式规模**留待探针批读数再定**
    —— D2 的原话就是"先跑探针批再回来定规模"，所以这里不预先批一个规模上去
E3  零花费（--planner rule --perceive privileged 不发请求），四格如实写零

三处与 SPEC 字面不同、且都是先量后定的，记进 E1 预登记的 deviations_from_the_spec_text：
1. §6 E1 写 `--planner model`，主 CLI 的枚举是 ('rule','fixture','deepseek')
   （evaluation/run.py:PLANNERS），`model` 是 benchmark_mujoco 入口的拼法。E1 落在主 CLI，
   所以用 `--planner deepseek --model-config <path>`；席位没变（`deepseek` 这个取值就是
   "由真模型决定"的意思，适配器按 --model-config 加载配置）。
2. §6 E1 把第六条 privileged 臂写成 `wo_vlm`。cli._perception_kwargs 在 `privileged` 上
   接受 `wo_vlm` 然后**把它丢掉**（cli.py:283-288）：那一格变成没有任何 `ablation` 记录的
   未武装 v0.1 基线，因为特权世界本来就没咨询过视觉模型，没有东西可关。可跑的那组名字由
   代码自己给出（SKILL_ARMS），正是 P5-c 那一组。§7.3 要求 E1 的行与 P5-c 的 rule 行并排，
   臂名必须逐字相同才有并排资格；而 SKILL-1 量 6 批。
3. P5-c 用了 `--policy memory`，E1 不能用：evaluation/run.py:645 明确拒它与模型规划器
   同时出现（它是 mode B 的零花费对照，而带模型规划器的批次已经有了自己的决策者）。
   页面仍然带记忆——`--experience-store` 照样装上 §5.4——所以臂开关仍然门控"提供什么"，
   变的只是"谁在读"。

身份：commit 717fd5f，dirty=true 的差别**就是这三份文件加 work/v03/ 下的 P0' 驱动**，
这一点写进文件里而不是留给读者猜。下一批跑动时工作树是干净的，manifest 会记真提交。
MSG

echo
git rev-parse HEAD
git status --porcelain | wc -l
