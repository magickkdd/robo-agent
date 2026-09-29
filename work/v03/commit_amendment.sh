#!/usr/bin/env bash
set -eu
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python

# Clear the stop that the false reading put in the state. The cell itself is kept: it was
# paid for and summarised, and SPEC §8.3 says a paid cell is never re-run.
$P - <<'PY'
import json
p = "/home/czx/embodied-agent-batches/v03/e1/e1_state.json"
s = json.load(open(p, encoding="utf-8"))
s["stopped"] = None
s["stopped_because"] = None
s["consecutive_quota_deaths"] = 0
s["upper_bound"] = 3800
s["upper_bound_amendment"] = {
    "from": 2484, "to": 3800, "file": "configs/experiment/v03_e1_preregistration_amendment_1.json",
    "why": "the batch stopped on the first cell because the reader summed a cumulative counter; "
           "the cell spent 225 requests, and the measured 2.027 requests per decision row puts "
           "the 12 cells at 3,016, x1.25 = 3,800",
}
s["re_derive_spend_from_ledger"] = True
json.dump(s, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2, sort_keys=True)
open(p, "a", encoding="utf-8").write("\n")
print("state reset: upper_bound", s["upper_bound"], "| cells recorded",
      len(s["cells"]), "| paid http_requests",
      sum((c.get("summary") or {}).get("http_requests", 0) for c in s["cells"]))
PY

git add -A
git -c user.name="czx" -c user.email="czx@localhost" commit -F - <<'MSG'
chore(P2'): E1 预登记增补 1 —— 上界 2,484 -> 3,800，并记一次我自己读错的账

第一格 em_full 触发停止规则时，报上来的请求数是 12,320，上界是 2,484。那个 12,320 是
**本脚本自己读错的**：账本每一行都带 `http_requests_total`，而那是适配器的**累计**计数器，
逐行相加等于把每一次请求在它之后的每一行各数一遍。逐次的字段是 `http_requests_this_call`，
该格真实花了 **225**。同一个读法还把 token 读成了 0，因为两列嵌在 `usage` 里而不是顶层
——该格真实花了 prompt 793,882 / completion 14,174。两个字段名现在在 e1_batch.py:read_cell
里按读到的样子写死，被取代的读法保留在 em_full_rederived.json。

所以：停止在一个**错的**上界面前是对的，而那个上界之所以错，是有可测理由的——估算式按
每轮 1 次请求算，实测 2.027 次，因为 chat_json 允许一次 JSON 格式修复，而
bst_text_agnes.yaml 允许 4 次传输尝试（max_retries: 4）。v0.2 不可能知道这件事：那一轮
全部 --planner rule、一发请求都不发，"每轮几次请求"在模型上座之前**结构上不可测**——
这正是本阶段要关的那个缺口，从成本这一侧看见的样子。

按实测 2.027 外推 12 格 = 3,016（em 6×203 + lh 6×300），×1.25 = 3,800；余量 784。
那个 1.25 是为一个**写明了的未知**付的：lh 的页面更大，页面越大越容易被截断，越可能触发
格式修复，所以 lh 未必与实测那格同率。这是余量，不是预报。

增补另立文件、不改原预登记：原文件在第一次请求**之前**于 b918f55 提交，一份跑完还能改的
预登记不是预登记。谁定的、改哪一格、从多少到多少、依据哪次测量，都写在
configs/experiment/v03_e1_preregistration_amendment_1.json。
没有变的：席位与配置哈希、臂表、sets、repeats、mode、每格 --frozen、--prereg 仍然故意不给
（P3 那份冻结的是另一个矩阵，matrix_mismatch 会以正确的理由拒 E1）、停止规则本身
（"撞顶即停，不允许再加一点"）、断点策略——断点策略正是这次**一分钱没多花**的原因：
em_full 已经写好格级摘要，所以增补的代价是零请求。
MSG

echo
git rev-parse HEAD
git status --porcelain | wc -l
