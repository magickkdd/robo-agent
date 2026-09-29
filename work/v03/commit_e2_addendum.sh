#!/usr/bin/env bash
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
chore(P3'): E2 预登记增补 1 —— 正式规模由探针账本导出

D2 原话是"探针批先跑，再回来定规模"。这就是那次回来：原预登记里 formal_scale 那一格当初
写的是 "DECIDED FROM THE PROBE READINGS"——原文件不动（b918f55 提交，在探针批之前），
这一份填上它。

每个数都是从探针批**自己的产物**读出来的，不是转述的：2 集、20 条 perception 记录、
23 行账（21 行 ok、2 行 429 死亡）、34 次请求、prompt 29,736 / completion 8,254、
每次被回答的 look 1.619 次请求。
（读法本身记在旁边：探针那两集**没有** episode_summary.json——两集都是在写摘要之前死的，
这本身就是探针的一条发现——所以集数与 perception 计数直接来自 episodes/ 目录与它们的
events.jsonl，不经过那份不存在的摘要。）

规模：1 个任务集 × 3 通道 {vlm, stub, privileged} × 2 集 × 2 repeats = 12 集，
其中 vlm 4 集、其余两通道 0 花费。外推 vlm 那一半约 **71 次请求**、prompt 约 59k，
占 E1 上界的 **1.9%**。⇒ 这批的约束不是预算，是席位的突发 429：探针 23 次里丢了 1 次，
agnes-vision-r4.yaml 已经为此把 max_retries 提到 4；停止规则照 SPEC §8.2
（连续 3 次 429/额度类即停批、存断点、落账）。

选的两个 case 是形状不同的两个 lh 集（lh_c1 共享对恢复 = 一个被扰动的状态；
lh_c3 容量/位移 = 一个必须守住的约束），因为 §8 B5 缺的那一行问的是"一次视觉读数有没有
用"，那是关于两种形状的问题而不是一种。

臂按通道不同是**故意的**，也正是 arm_coherence 在工作：stub 上不能 claim `full`
（拥有 perception 记录的那个模块是关的），而每集自己的 ablation 记录写着条件，
所以三格不能被并成一句"vlm vs 非 vlm"。这一条写进增补，因为它是最容易被读成混淆的地方。

读这两个数时踩过的坑也记在旁边：账本字段是 `http_requests_this_call`，
`http_requests_total` 是累计计数器——本阶段它已经让 E1 停了两次，见
configs/experiment/v03_e1_preregistration_amendment_1.json。
MSG

echo
git rev-parse HEAD
git status --porcelain | wc -l
