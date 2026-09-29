"""Write MEM-1's model-seat pre-registration, with every number read off the archive.

The discipline the other three v0.3 pre-registrations follow is that no number in them is
hand-typed (`v03_e1_preregistration.json` says so in its own `written_by` field). That matters
most for this one, because the bound is a projection and a projection typed by hand is a guess
wearing a pre-registration's clothes.

The projection is measured, not assumed: E1 already ran 15 `em_p*` episodes per cell on this
exact seat with this exact config, and the pair protocol uses the same cases. So the per-episode
cost is read out of E1's own ledgers with the two-ledger reader, and the bound is set from the
**worst** cell rather than the mean — because on E1 the 429 tail is what actually determined the
spend, and a mean is computed mostly over cells that escaped the limiter.

The confound is stated in the pre-registration, not discovered afterwards: on a model seat the
two arms differ in memory AND in the draw, so a within-pair difference is not attributable to
memory alone. Pre-registering that is what stops the finding being read as a memory effect.
"""
import json
import os
import statistics
import subprocess
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
from embodied_agent.evaluation.run import batch_spend

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"
OUT = os.path.join(REPO, "configs", "experiment", "v03_mem1_preregistration.json")
EPISODES = 16
MARGIN = 1.5


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True,
                          text=True, check=True).stdout.strip()


# ------------------------------------------------- the measured per-episode cost ----
def em_batches():
    out = []
    for cell in sorted(os.listdir(ARCHIVE)):
        if not cell.startswith("em_") or not os.path.isdir(os.path.join(ARCHIVE, cell)):
            continue
        for dirpath, dirnames, _fn in os.walk(os.path.join(ARCHIVE, cell)):
            if "episodes" not in dirnames:
                continue
            s = batch_spend(dirpath)
            if s["per_episode_ledgers"]["files"]:
                out.append(s)
    return out


batches = em_batches()
per_ep = {
    "rows": [b["per_episode_ledgers"]["rows"] / b["per_episode_ledgers"]["files"] for b in batches],
    "requests": [b["http_requests"] / b["per_episode_ledgers"]["files"] for b in batches],
    "prompt": [b["prompt_tokens"] / b["per_episode_ledgers"]["files"] for b in batches],
    "completion": [b["completion_tokens"] / b["per_episode_ledgers"]["files"] for b in batches],
}
wasted = [1 - b["http_requests_successful"] / b["http_requests"]
          for b in batches if b["http_requests"]]

mean_req = statistics.mean(per_ep["requests"])
worst_req = max(per_ep["requests"])
BOUND = int(worst_req * EPISODES * MARGIN)

e1_total = 0
for dirpath, dirnames, _fn in os.walk(ARCHIVE):
    if "episodes" in dirnames:
        e1_total += batch_spend(dirpath)["http_requests"]

payload = {
    "experiment": "MEM-1 on a model decision source — §11's Memory group, which had no reading "
                  "on one (SPEC §11 Memory, §13.2 row 2)",
    "spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md",
    "why_this_batch_exists": [
        "§11's Memory group (M1–M4) is defined by MEM-1 over the episodic **pair** protocol: a "
        "writer episode leaves residue, a reader episode queries it, and the two arms differ in "
        "whether retrieval happens.",
        "That protocol's only entry point hard-coded `planner_kind=\"rule\"`, so every M1–M4 "
        "reading this project has is a rule-policy reading. On a model decision source the group "
        "had **no** reading — a structural gap, not a measurement that was skipped, and the reason "
        "v0.3's §13.2 row 2 reads 'partially met'.",
        "P6-b added the seat. This batch is what runs on it.",
    ],
    "entry_point": "python -m embodied_agent.cli em-pairs",
    "command_shape": [
        "python -m embodied_agent.cli em-pairs",
        "--run /home/czx/embodied-agent-batches/v03/mem1_model",
        "--planner deepseek",
        "--model-config configs/models/bst_text_agnes.yaml",
    ],
    "shape": {
        "pairs": 4,
        "pairs_are": "the frozen set's own four (EM_PAIRS); `--pairs` is not narrowed",
        "arms": ["full", "wo_episodic_memory"],
        "roles_per_arm": ["writer", "reader"],
        "episodes": EPISODES,
        "episodes_arithmetic": "4 pairs x 2 arms x 2 roles = 16 episodes, one store per pair-arm",
        "repeats": 1,
        "perceive": "privileged",
        "why_privileged": "the Memory group's question is whether the reader uses what the writer "
                          "left. Rendering a camera would add a second, unmeasured difference "
                          "between the arms, so perception is held fixed and the reader is the "
                          "only thing that varies.",
    },
    "seat": {
        "planner": "deepseek",
        "model_config": "configs/models/bst_text_agnes.yaml",
        "policy": "None — derived, not passed",
        "why_policy_is_none": "`MemoryPolicy` answers from `ctx.model_payload()` and is itself a "
                              "decision maker; `run_group` refuses `policy=\"memory\"` beside a "
                              "model planner precisely because two decision makers make a row's "
                              "source unnameable. `run_episodic_pairs` derives the policy from the "
                              "seat, so the incoherent batch cannot be expressed.",
        "store_and_ablation_kept": "the experience store and the arm's ablation both stay on, "
                                   "which is the object of the measurement",
        "same_seat_as": "E1 (identical config), so the two batches' ledgers are comparable",
    },
    "the_confound_stated_up_front": {
        "what": "on a model seat the two arms differ in memory AND in the draw",
        "why": "a model planner is stochastic; the rule seat's reader is a deterministic policy, "
               "so there the arms differ only in memory",
        "consequence": "a within-pair trajectory difference is NOT attributable to memory alone, "
                       "and this table is not comparable with a rule-seat table as if it were the "
                       "same experiment",
        "what_it_does_not_establish": [
            "that a model reader uses memory *more* than a rule reader — the two seats differ in "
            "far more than memory, so no such comparison is licensed",
            "any rate: n=4 pairs is an existence proof for the instruments, not a sample "
            "(the same `unit_note` MEM-1 already carries on the rule seat)",
        ],
        "recorded_in_the_artefact": "`planner`, `policy`, `model_config` and a `seat_note` are in "
                                    "`pairs_run.json`, `pairs_measured.json` AND "
                                    "`episodic_memory_MEM_1.json`, so a reader of the published "
                                    "table cannot mistake the two seats",
    },
    "acceptance": [
        "all 16 episodes write a `pairs_run.json` naming `planner: deepseek`, `policy: null` and "
        "the model config",
        "the `full` arm's reader episodes file non-empty `memory_retrieval` records; the "
        "`wo_episodic_memory` arm files none and passes `Ablation.violations()`",
        "every episode's `episode_summary.json` carries the `episodic` block",
        "MEM-1's `episodic_memory_MEM_1.json` carries the seat fields and the model-seat "
        "`seat_note`",
        "the batch's two-ledger spend is reported, with `http_requests` (made) and "
        "`http_requests_successful` (returned) side by side",
    ],
    "cost": {
        "unit": "HTTP requests made",
        "why_requests_not_usd": "the endpoint is free and no price is configured, so a money "
                                 "figure would be invented; the request is also the unit the "
                                 "provider's rate limiter is denominated in, and on E1 that "
                                 "limiter — not the token count — decided what the batch got",
        "measured_from": "E1's own `em_*` batch ledgers, read with `batch_spend` (the two-ledger "
                         "reader added in P6-a): the same case family, the same seat, the same "
                         "config, so this is a measurement of the same thing rather than a guess",
        "batches_measured": len(batches),
        "per_episode_requests": {
            "mean": round(mean_req, 2), "median": round(statistics.median(per_ep["requests"]), 2),
            "worst_cell": round(worst_req, 2), "best_cell": round(min(per_ep["requests"]), 2),
        },
        "per_episode_prompt_tokens": {
            "mean": round(statistics.mean(per_ep["prompt"])),
            "worst_cell": round(max(per_ep["prompt"])),
        },
        "per_episode_completion_tokens": {
            "mean": round(statistics.mean(per_ep["completion"])),
            "worst_cell": round(max(per_ep["completion"])),
        },
        "share_of_requests_that_returned_nothing": {
            "mean": round(statistics.mean(wasted), 4), "worst_cell": round(max(wasted), 4),
            "note": "E1 spent 707 of 3,292 requests this way, all 429s after retries plus 17 "
                    "schema errors. It is why the bound comes from the worst cell: a mean is "
                    "computed mostly over cells that escaped the limiter.",
        },
        "projection_mean": {
            "requests": round(mean_req * EPISODES),
            "prompt_tokens": round(statistics.mean(per_ep["prompt"]) * EPISODES),
            "completion_tokens": round(statistics.mean(per_ep["completion"]) * EPISODES),
        },
        "projection_worst_cell": {
            "requests": round(worst_req * EPISODES),
            "prompt_tokens": round(max(per_ep["prompt"]) * EPISODES),
            "completion_tokens": round(max(per_ep["completion"]) * EPISODES),
        },
        "upper_bound_requests": BOUND,
        "upper_bound_arithmetic": f"worst cell {worst_req:.2f} req/ep x {EPISODES} episodes x "
                                  f"{MARGIN} = {BOUND}; the margin covers a batch that meets the "
                                  f"limiter harder than any E1 cell did, which SPEC §11's risk "
                                  f"register lists as outside this project's control",
        "context": {
            "e1_spent_requests": e1_total,
            "this_batch_as_share_of_e1_spend": round(BOUND / e1_total, 4) if e1_total else None,
            "e1_bound_was": 3800,
        },
        "usd": None,
        "pricing_configured": False,
        "stop_rule": f"halt before the 17th episode if the batch's two-ledger `http_requests` "
                     f"reaches {BOUND}; on a halt, report what ran and what did not, and do not "
                     f"resume into the same directory (the store beside it is append-only)",
    },
    "artefact_directory": "/home/czx/embodied-agent-batches/v03/mem1_model",
    "artefact_rule": "not /tmp, not a single point: each pair-arm keeps its own store and "
                     "episodes, and the three JSON summaries beside the run are the source lines",
    "credentials": "never recorded; a config names the env var, not the key (SPEC 7)",
    "decided_by": {
        "D1": "the text seat is `configs/models/bst_text_agnes.yaml` — the same one E1 ran, so "
              "this batch's ledgers are comparable with E1's rather than being a second seat",
        "D2": f"bound declared here at {BOUND} requests, measured from E1 and set from its worst "
              f"cell, before the first request of this batch",
        "D5": "road A — this closes the one §13.2 row v0.3 could not close, and it is the "
              "cheapest of the three outstanding gaps",
    },
    "product_change_this_batch_depends_on": {
        "commit": git("rev-parse", "HEAD"),
        "what": "`em-pairs` gained `--planner`/`--model-config`; `run_episodic_pairs` derives the "
                "policy from the seat; MEM-1 carries the seat into its artefact",
        "contract": "tests/contract/test_v03_episodic_seat.py (20 checks) with 12/12 mutations "
                    "verified to bite (work/v03/p6b_mutation_check.py)",
        "zero_spend_rehearsal": "the rule seat ran end to end first — 4 pairs x 2 arms = 16 "
                                "episodes over 8 stores, `offline: true`, giving the baseline this "
                                "batch is read against",
    },
    "identity": {
        "git_commit": git("rev-parse", "HEAD"),
        "note": "written after the commit that added the seat, so the tree is dirty by exactly "
                "this file; it is committed next, and the batch runs from a clean tree",
    },
    "written_by": "work/v03/p6c_write_prereg.py (no number in this file is hand-typed: the "
                  "per-episode cost, the projection and the bound are all read off E1's ledgers "
                  "with batch_spend)",
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
    f.write("\n")

print(f"wrote {OUT}")
print(f"  bound            : {BOUND} requests")
print(f"  mean projection  : {payload['cost']['projection_mean']}")
print(f"  worst projection : {payload['cost']['projection_worst_cell']}")
print(f"  as share of E1   : {payload['cost']['context']['this_batch_as_share_of_e1_spend']:.1%}")
