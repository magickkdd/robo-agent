"""What will MEM-1 on the model seat cost? Measure the per-episode cost, do not guess it.

MEM-1 is 4 pairs x 2 arms x 2 roles = 16 episodes on the `bst_text_agnes` seat. The honest way to
bound that is the E1 batch, which already ran 15 `em_p*` episodes per cell on that exact seat with
that exact config: same case family, same reader, same protocol. So this measures E1's `em_*` cells
with the two-ledger reader and projects.

The number that decides the bound is not the mean. It is the tail: on E1, 707 of 3,292 requests
(21.5%) returned nothing, all of them 429s after retries, and a 429 costs a request and no tokens.
A bound set at the mean would be under the mean, because the mean is computed over cells that
mostly escaped the limiter. So the spread across cells is reported, and the bound is set from the
worst cell rather than the average.
"""
import json
import os
import statistics
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
from embodied_agent.evaluation.run import batch_spend

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"
EPISODES = 16          # 4 pairs x 2 arms x 2 roles
REHEARSAL = "/home/czx/embodied-agent-batches/v03/em_rules_rehearsal"

# which E1 cells are the em_* family (the same cases the pair protocol uses)
cells = sorted(d for d in os.listdir(ARCHIVE) if d.startswith("em_"))
print(f"=== E1 cells on the same case family: {cells} ===")
print()

rows = []
for cell in cells:
    cell_root = os.path.join(ARCHIVE, cell)
    for dirpath, dirnames, _fn in os.walk(cell_root):
        if "episodes" not in dirnames:
            continue
        s = batch_spend(dirpath)
        pe = s["per_episode_ledgers"]
        n_ep = pe["files"]
        if not n_ep:
            continue
        g = s["goal_parse_ledger"]
        rows.append({
            "cell": cell, "batch": os.path.relpath(dirpath, ARCHIVE),
            "episodes": n_ep,
            "rows": pe["rows"], "requests": s["http_requests"],
            "req_ok": s["http_requests_successful"],
            "prompt": s["prompt_tokens"], "completion": s["completion_tokens"],
            "goal_requests": g["http_requests"], "goal_rows": g["rows"],
            "errored_rows": s["errored_rows"],
        })

print(f"{'cell':26s} {'eps':>4s} {'rows':>5s} {'req':>5s} {'req/ep':>7s} "
      f"{'prompt/ep':>10s} {'compl/ep':>9s} {'goal':>5s} {'err':>4s}")
for r in rows:
    print(f"{r['cell'][:26]:26s} {r['episodes']:>4d} {r['rows']:>5d} {r['requests']:>5d} "
          f"{r['requests'] / r['episodes']:>7.2f} {r['prompt'] / r['episodes']:>10,.0f} "
          f"{r['completion'] / r['episodes']:>9,.0f} {r['goal_requests']:>5d} {r['errored_rows']:>4d}")

print()
per_ep_req = [r["requests"] / r["episodes"] for r in rows]
per_ep_prompt = [r["prompt"] / r["episodes"] for r in rows]
per_ep_compl = [r["completion"] / r["episodes"] for r in rows]
per_ep_rows = [r["rows"] / r["episodes"] for r in rows]
wasted_frac = [1 - r["req_ok"] / r["requests"] for r in rows if r["requests"]]

print("=== per-episode, across the em_* batches ===")
for name, vals in (("rows", per_ep_rows), ("requests", per_ep_req),
                   ("prompt tokens", per_ep_prompt), ("completion tokens", per_ep_compl)):
    print(f"  {name:>18}: mean {statistics.mean(vals):>9,.1f}  median {statistics.median(vals):>9,.1f}"
          f"  worst {max(vals):>9,.1f}  best {min(vals):>9,.1f}")
print(f"  {'share wasted':>18}: mean {statistics.mean(wasted_frac):>9.1%}"
      f"  worst {max(wasted_frac):>9.1%}")

print()
print("=== the rehearsal's round count, to confirm 16 episodes is the right shape ===")
n_eps = 0
for dirpath, dirnames, _fn in os.walk(REHEARSAL):
    if "episodes" in dirnames:
        n_eps += 1
print(f"  rule-seat rehearsal: {n_eps} episodes (expected 16)")
print(f"  model-seat MEM-1   : {EPISODES} episodes (4 pairs x 2 arms x 2 roles)")

print()
print("=== projection for the model seat, and the bound ===")
mean_req = statistics.mean(per_ep_req)
worst_req = max(per_ep_req)
print(f"  mean projection     : {mean_req * EPISODES:>9,.0f} requests, "
      f"{statistics.mean(per_ep_prompt) * EPISODES:>11,.0f} prompt, "
      f"{statistics.mean(per_ep_compl) * EPISODES:>9,.0f} completion")
print(f"  worst-cell projection: {worst_req * EPISODES:>9,.0f} requests, "
      f"{max(per_ep_prompt) * EPISODES:>11,.0f} prompt, "
      f"{max(per_ep_compl) * EPISODES:>9,.0f} completion")
# A bound has to cover a batch that hits the limiter harder than any E1 cell did, because
# E1's limiter behaviour is explicitly recorded as outside this project's control. 1.5x on the
# worst cell is the smallest margin that still means something, and it is declared here rather
# than discovered afterwards.
BOUND = int(worst_req * EPISODES * 1.5)
print(f"  declared bound      : {BOUND:>9,d} requests  "
      f"(worst cell x 1.5 = {worst_req * EPISODES:,.0f} x 1.5)")
print()
print(f"  ratio to E1's whole batch: {BOUND / 3292:.1%} of the 3,292 E1 spent, "
      f"and {BOUND / 3800:.1%} of E1's 3,800 bound")
print()
print("  USD: null — the endpoint is free and no price is configured, so a money figure")
print("  would be invented. The bound is denominated in requests, which is what the")
print("  limiter and the quota are actually denominated in.")
