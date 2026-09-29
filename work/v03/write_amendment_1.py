"""Write E1's first pre-registration amendment as its own dated record.

`configs/experiment/v03_e1_preregistration.json` was committed at b918f55, before the first
request, and it is not edited now: a pre-registration that can be rewritten after the first
request is not a pre-registration. This file amends one cell of it and says which, why, on
whose decision, and on what measurement.

The trigger is worth stating exactly, because the batch stopped for a reason that turned out
to be the reader's and not the batch's:

  * the pre-registered call upper bound was 2,484, and the batch stopped on the FIRST cell
    reporting 12,320 requests;
  * 12,320 was this script's own reading error. `http_requests_total` is the adapter's
    **cumulative** counter and is carried on every ledger row, so summing it across 111 rows
    counted each request once per row after it. The per-call field is
    `http_requests_this_call`, and the cell spent **225**;
  * the same reader looked for `prompt_tokens`/`completion_tokens` at the top level and found
    0, because the rows nest tokens under `usage`. The cell actually spent prompt 793,882 /
    completion 14,174.

So the stop was right under a bound that was wrong, and the bound is wrong for a measurable
reason: the estimate counted **one** request per decision round, and a round costs 2.03 on this
seat, because `chat_json` allows one JSON format repair and the config allows four transport
attempts. Nothing in v0.2 could have told us that: every v0.2 arm batch was rule-driven and
made no request at all, so "requests per round" was structurally unmeasurable before a model
was in the seat — which is the same gap this whole phase exists to close, seen from the cost
side.
"""
import json
import os
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
os.chdir("/home/czx/embodied-agent-robot-agent-embodied-agent-4")

from work.v03.e1_batch import read_cell  # noqa: E402

CELL = "/home/czx/embodied-agent-batches/v03/e1/em_full"
run_dir = next(os.path.join(CELL, p) for p in sorted(os.listdir(CELL))
               if os.path.isdir(os.path.join(CELL, p, "episodes")))
measured = read_cell(run_dir)
measured.pop("per_episode")
rederived = json.load(open("/home/czx/embodied-agent-batches/v03/e1/em_full_rederived.json",
                           encoding="utf-8"))

amendment = {
    "spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §8.1 / §8.2",
    "kind": "pre-registration amendment 1",
    "amends": "configs/experiment/v03_e1_preregistration.json (committed at b918f55, before "
              "the first request; not edited by this file, and this file is why)",
    "cell_changed": "spec_8_four_cells.call_upper_bound",
    "from": 2484,
    "to": 3800,
    "decided_by": {"D2": "user 2026-09-29: raise the upper bound to 3,800 = 3,016 x 1.25"},
    "why_the_original_bound_was_wrong": {
        "the_estimate": "P5-c's decision records re-counted (1,488) plus one goal parse per "
                        "episode (168) = 1,656, x1.5 = 2,484. Both terms are right; the "
                        "missing term is that a round costs more than one request.",
        "measured_requests_per_decision_row": round(rederived["requests_per_decision_row"], 3),
        "mechanism": "adapters.deepseek.chat_json allows one JSON format repair for a "
                     "well-formed-but-unparseable answer, and configs/models/"
                     "bst_text_agnes.yaml allows four transport attempts (max_retries: 4). "
                     "A round therefore costs 1 request plus whatever those two paths add.",
        "why_v0.2_could_not_have_said": "every v0.2 arm batch ran `--planner rule` and made no "
                                        "request, so requests-per-round was structurally "
                                        "unmeasurable before a model was in the seat",
    },
    "projection_from_the_measured_cell": {
        "requests_per_decision_row_used": round(rederived["requests_per_decision_row"], 3),
        "rounds_per_cell": {"em": 100, "long_horizon": 148},
        "requests_per_cell": {"em": 203, "long_horizon": 300},
        "projected_12_cells": rederived["projected_total"],
        "headroom_in_the_new_bound": 3800 - rederived["projected_total"],
        "assumption_named": "the long_horizon cells have the same requests-per-row as the em "
                            "cell that was measured. They do not obviously: an lh page is "
                            "larger, and a larger page is where a truncated answer (and so a "
                            "format repair) is likelier. The 1.25 headroom is the declared "
                            "payment for that unknown, not a forecast.",
    },
    "the_stop_that_triggered_this": {
        "cell": "em_full",
        "bound_in_force_at_the_time": 2484,
        "requests_the_reader_reported": 12320,
        "requests_actually_spent": measured["http_requests"],
        "why": "the reader summed `http_requests_total` (cumulative, per row) instead of "
               "`http_requests_this_call` (per call), and read tokens at the top level "
               "instead of under `usage`. Both field names are now read in "
               "work/v03/e1_batch.py:read_cell, and the superseded reading is kept in "
               "/home/czx/embodied-agent-batches/v03/e1/em_full_rederived.json",
        "action_taken": "the batch stopped, the cell was left in place and NOT re-run "
                        "(SPEC §8.3: a paid cell is never re-run), and the bound was put to "
                        "the user as a D2 amendment rather than raised by the agent",
    },
    "what_did_not_change": [
        "the seat (configs/models/bst_text_agnes.yaml, sha256 unchanged)",
        "the arm list, the sets, the repeats, the mode",
        "--frozen on every cell, --prereg still deliberately absent (the P3 pre-registration "
        "freezes a different matrix and matrix_mismatch would refuse E1 for the right reason)",
        "the stop rules, including 'the bound is hit means stop, not a little more'",
        "the checkpoint strategy, which is what kept this from costing anything: em_full was "
        "already summarised, so the amendment cost zero further requests",
    ],
    "measurements_now_available_for_the_cost_group": {
        "note": "the first batch on this project with a real token ledger; the Cost group of "
                "§11 has been 0/0/null in every prior reading",
        "em_full": measured,
    },
    "artefacts": {
        "cell": CELL,
        "rederived": "/home/czx/embodied-agent-batches/v03/e1/em_full_rederived.json",
        "state": "/home/czx/embodied-agent-batches/v03/e1/e1_state.json",
    },
}

path = "configs/experiment/v03_e1_preregistration_amendment_1.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(amendment, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
import hashlib
digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
print(f"wrote {path} ({os.path.getsize(path)} bytes, sha256 {digest[:16]})")
print(json.dumps({k: amendment[k] for k in
                  ("cell_changed", "from", "to", "decided_by")}, ensure_ascii=False, indent=1))
print("projected 12 cells:", amendment["projection_from_the_measured_cell"]["projected_12_cells"])
print("headroom in the new bound:",
      amendment["projection_from_the_measured_cell"]["headroom_in_the_new_bound"])
