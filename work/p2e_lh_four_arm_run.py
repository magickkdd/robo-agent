"""P2-e: run §9's four planning arms over the six `lh_*` cases, twice each.

Two policies per arm, because P2-e's claim is about what the *page* carries: the same decision
maker (`planning.policy.PlanPolicy`) answers on both, and the v0.1 control answers from the rule
path. Eight batches x six cases = 48 episodes, all zero-spend (`planner_kind="rule"`, no camera,
`privileged` world), so the artifact can be re-run by anyone without a bill.

Writes only under /tmp. `run_group` prints its own summary; the line printed here is the
arm/policy/outcome triple, so a log reader can tell which batch produced which row.
"""
import json
import sys

from embodied_agent.core.v02 import ablation as ablation_record
from embodied_agent.evaluation.run import run_group
from embodied_agent.planning.arm import PLANNING_ARMS

CASE_IDS = ["lh_c1_shared_pair_restore", "lh_c2_displaced_completion",
            "lh_c3_capacity_and_shift", "lh_c4_failed_grasp_recovery",
            "lh_c5_release_deviation", "lh_c6_two_disruptions"]
OUT_ROOT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/p2e_full/runs"

for policy in (None, "payload"):
    for arm in PLANNING_ARMS:
        stats = run_group("long_horizon", modes=("B",), planner_kind="rule", repeats=1,
                          out_root=OUT_ROOT, case_ids=list(CASE_IDS), frames=False,
                          perceive="privileged", ablation=ablation_record(arm), policy=policy)
        print(json.dumps({"arm": arm, "policy": policy or "rule", "run_id": stats["run_id"],
                          "root": stats["root"], "episodes": stats["rows"],
                          "outcomes": stats["outcomes"]}), flush=True)
print("ALL BATCHES DONE", flush=True)
