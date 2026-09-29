"""What did the MEM-1 model-seat batch actually spend?

The batch's own stop rule printed `spent 0, completed within bound`. That is not a small number
in the reassuring direction: the check globbed `.../<role>/episodes/*/model_calls.jsonl` while
`run_group` writes `.../<role>/<run_id>/episodes/*/...`, so it read an empty tree and reported a
clean pass. A bound check that measures nothing and says "within bound" is the most dangerous
shape this project has produced, and it is the *same* class of error as the one P6-a fixed — a
reader pointed at the wrong place. The difference is that this one reported success.

So: measure the real spend at the depth the ledger is actually at, and report it.
"""
import json
import os
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
from embodied_agent.evaluation.run import batch_spend

ROOT = "/home/czx/embodied-agent-batches/v03/mem1_model"
BOUND = 411

ledger = json.load(open(os.path.join(ROOT, "pairs_run.json"), encoding="utf-8"))
print("=== what the run recorded ===")
print(f"  planner={ledger['planner']} policy={ledger['policy']} "
      f"model_config={ledger['model_config']}")
print(f"  pairs_ran={ledger.get('pairs_ran')} declared={ledger.get('pairs_declared')}")
print(f"  stop rule recorded: {ledger.get('stop_rule')}")
print()

# the run root each batch reported is authoritative — run_group names a timestamped subdir
roots = [(r["pair_id"], r["arm"], role, b["root"])
         for r in ledger["batches"] for role, b in r["batches"].items()]
print(f"=== {len(roots)} episode batches; the depth that actually holds ledgers ===")
tot = {"http_requests": 0, "http_requests_successful": 0, "errored_http_requests": 0,
       "errored_rows": 0, "prompt_tokens": 0, "completion_tokens": 0}
rows = 0
for pair_id, arm, role, root in roots:
    s = batch_spend(root)
    eps = s["per_episode_ledgers"]["files"]
    rows += s["per_episode_ledgers"]["rows"]
    for k in tot:
        tot[k] += s[k]
    print(f"  {pair_id:6s} {arm:20s} {role:7s} eps={eps} rows={s['per_episode_ledgers']['rows']:>3d} "
          f"req={s['http_requests']:>3d} prompt={s['prompt_tokens']:>7,d} "
          f"goal_rows={s['goal_parse_ledger']['rows']}")

print()
print("=== the batch, measured ===")
print(f"  episode ledgers : {len(roots)} files, {rows} rows")
print(f"  requests made   : {tot['http_requests']}")
print(f"  requests returned: {tot['http_requests_successful']}")
print(f"  returned nothing : {tot['errored_http_requests']} across {tot['errored_rows']} rows")
print(f"  prompt tokens   : {tot['prompt_tokens']:,}")
print(f"  completion      : {tot['completion_tokens']:,}")
print()
print(f"=== against the declared bound of {BOUND} ===")
print(f"  spent {tot['http_requests']} / {BOUND} -> "
      f"{'WITHIN' if tot['http_requests'] < BOUND else 'OVER'} bound, "
      f"margin {BOUND - tot['http_requests']}")
print(f"  the pre-registration's mean projection was 237, worst-cell 274")
print(f"  actual / mean   = {tot['http_requests'] / 237:.1%}")
print()
print("  the batch is within its bound, so the numbers stand — but the stop rule that was")
print("  supposed to enforce it did not fire because it read an empty tree. Recorded, not")
print("  glossed: the bound held by luck of the projection, not because it was checked.")
