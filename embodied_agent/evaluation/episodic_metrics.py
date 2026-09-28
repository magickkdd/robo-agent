"""§11's Memory group — 口径 written before the numbers (SPEC-v0.2 §11, §13 P3-e).

SPEC-v0.2 §11 lists four rows under *Memory*: retrieval relevance, successful reuse, negative transfer,
stale memory usage. Every one of them is a claim about a **second** episode reading what a **first**
one wrote, which is why they are computed here over pairs of runs (`evaluation/episodic_runs.py`) and
not over single episodes: on the frozen 47, on `dev`, on `formal` and on `long_horizon`, every store is
cold at every episode's start, so all four rows would be a division by zero dressed as a measurement.

The house rule is P1-f's and P2-e's (`evaluation/vlm_contrast.py`, `evaluation/long_horizon.py`): every
rate is rendered from `METRIC_DEFINITIONS` below, carries its numerator and denominator in the output,
and is pooled by summing integers rather than by averaging per-pair fractions. What follows are the five
decisions this module exists to make.

**1. Only the reader is scored, and the control's silence is `not_measured`, never a zero.**

The writer's episode is the *cause* of the memory, not a subject of these rows. And on the
`wo_episodic_memory` arm `episodic/arm.py` never calls `retrieve`, so a retrieval-shaped rate there has
no denominator at all; publishing 0.0 would credit the arm with the gate removed having "no irrelevant
memories", which is the unfalsifiable switch P2-e named, one layer up. Every `M1.*` is therefore a
treatment-arm quantity, and the control appears only where the comparison needs two arms (M2, M3).

**2. `retrieval relevance` is three numbers because one cannot say what relevance means here.**

The relevance filter is applied *before* the record is filed, so "share of retrieved rows that were
relevant" is 1.0 by construction — a metric that cannot fail is not a metric. The frozen set
(`episodic_tasks.episodic_manifest()["relevance_within_a_batch"]`) records why the filter is even
weaker than that inside a batch: the query's `task_kind` term is the batch's own set name, worth 3.0
against `MIN_RELEVANCE` 1.0, so no `em` row can be excluded. So `M1` reports the page (rows per
round), the *term mix* of each match (`M1.matched_beyond_the_batch_name`: a row whose only matched term
is `task_kind:em` was selected by the harness, not by the task), and what the guard thought of it
(`M1.rows_refuted`). Those three together are the honest content of §11's first Memory row.

**3. `successful reuse` is read off the two trajectories, never off `memory_followed`.**

P3-d's p4 measured the flag true on 6 of 6 decision rounds with the two arms' trajectories identical —
`episodic/policy.py` sets it when the row that won is one the page ranked, and an *agreeing* memory
ranks the same object the plan already put first. So the headline is behavioural (§12.3: 以实际动作和最
终状态为准): a pair counts as reuse when the reader's executed action sequence differs from the
control's *and both arms finish*. The self-report is published beside it with its coincidence rate
(`M2.followed_where_control_chose_the_same`), because the gap between the two is a finding about the
instrument, and this module is where that gap is priced rather than smoothed over.

**4. `negative transfer` gets a cost definition and a guard definition, and the two are not summed.**

A memory does harm measurable as harm when the arm that had it ends up worse off: lower outcome, or the
same outcome at more rounds or more skill calls (`M3.treatment_worse_or_costlier`). That is the row
§11 asks for and it needs no internal flag. Separately, the guard's own brake — `declines` — is counted
against the rounds where the page was actually refuted (`M3.declines_per_refuted_round`), and reported
as `not_measured` when the store shape cannot reach the branch at all. P3-d measured exactly that
shape: a `true` claim by a successful writer is refuted only while its row is unsatisfied, i.e. only
when it is *owed*, and owed is when `policy.py` promotes it — so on a success-only store the brake has
no input (`episodic_tasks._declined`, `frozen_episodic_v1.json["decline_branch"]`). Reporting 0.0 for
that without the mechanism would read as "no negative transfer observed", which is the opposite of what
was measured.

**5. `stale memory usage` has two shapes, and they need different fixes.**

The one the design anticipated: the world contradicted the recalled claim, and the policy ranked its
next action by it anyway (`M4.refuted_and_still_governing`). The one the first run found: the claim was
never contradicted *because the plan has no row that could check it*, since
`arm.py::_memory_query` builds `measured` from this episode's own `plan.subgoals`
(`M4.uncheckable_and_governing`, declared per pair by the set's own arithmetic). Both are a stale
memory governing; neither is a superset of the other; §11's row is reported twice rather than pooled.
The second shape is the larger defect — it lasts the whole episode rather than the round before the
first measurement — and it is invisible to the module's own flag, so it can only be counted from
outside, which is what this file is for.

Zero spend: everything here is read from artifacts a rule-policy, privileged-perception batch wrote.
No scene is opened, no model is called, no store is mutated.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .episodic_runs import read_pair
from .episodic_tasks import EM_ARMS, EM_CONTROL_ARM, EM_PAIRS, EM_TREATMENT_ARM, pair_claims
from .report import wilson_interval

#: MEM-1 is the first publication of §11's Memory group, so no earlier version is being replaced.
#: If a definition changes after an artifact is delivered, the version changes with it and the old
#: artifact is named rather than re-scored (SPEC §12.2: 门槛正式前冻结, 事后不调低).
METRIC_VERSION = "MEM-1"

METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "M1.rows_per_round": {
        "question": "how much memory is a reader actually shown per decision?",
        "unit": "one decision round of a treatment-arm reader x one pair",
        "numerator": "rows the retrieval record filed for that round, summed",
        "denominator": "reader rounds that carry a `memory_retrieval` record (the arm files one per "
                       "decision it makes while the gate is on, including a zero-row round)",
        "truth": "`memory_retrieval.payload.retrieved`, the record of what was put on the page",
        "forbidden": "the control arm's rounds: `wo_episodic_memory` files no retrieval record by "
                     "construction, so a rate over its rounds divides nothing and a 0.0 would read as "
                     "a perfect memory module",
    },
    "M1.matched_beyond_the_batch_name": {
        "question": "of the rows that were selected, how many were selected for a reason other than "
                    "belonging to this batch?",
        "unit": "one retrieved row",
        "numerator": "rows with at least one `match_terms` entry that does not start `task_kind:`",
        "denominator": "rows on the page",
        "truth": "the row's own `match_terms`, which `retrieval.score` files beside the score it came "
                 "from",
        "forbidden": "reading the complement as 'irrelevant': `task_kind` is the batch's set name and "
                     "every row in the batch carries it, so a row matched only by the batch name was "
                     "not excluded for any reason the task supplied — that is the property the frozen "
                     "set declares in `relevance_within_a_batch`, and this is the number that shows "
                     "whether it bites on these pairs",
    },
    "M1.rows_refuted": {
        "question": "how often did the snapshot contradict a row that was on the page?",
        "unit": "one retrieved row",
        "numerator": "rows filed with `contradicted_current_state` true",
        "denominator": "rows on the page",
        "truth": "`retrieval.contradicted`, which needs a measured verdict on both sides before it "
                 "says anything",
        "forbidden": "reading a low value as agreement between memory and world: `unknown` on either "
                     "side ends the question rather than answering it, which is why M4 counts the "
                     "rounds where nothing could be looked up",
    },
    "M1.distinct_rows_vs_store": {
        "question": "of the residue the writer left, how much of it reached a page?",
        "unit": "one pair, against the store that pair's own writer left",
        "numerator": "distinct `experience_id`s the reader was shown across all its rounds",
        "denominator": "rows in the store when the reader's last round retrieved (the writer's own "
                       "count, never the batch's)",
        "truth": "`memory_retrieval.payload.store_size` and the rows' `experience_id`s",
        "forbidden": "pooling across pairs: every pair has its own store, and a denominator summed "
                     "over pairs would let one pair's unread row be cancelled by another's",
    },
    "M2.trajectory_changed_and_completed": {
        "question": "did the memory change what the reader did, and did the reader still finish?",
        "unit": "one pair (both arms)",
        "numerator": "pairs whose treatment reader's executed action sequence differs from the control "
                     "reader's and both arms report `complete_success`",
        "denominator": "pairs that ran in both arms",
        "truth": "the two `decision` streams, compared element by element — §12.3's 以实际动作和最终状态"
                 "为准, applied to a memory",
        "forbidden": "`memory_followed`, the round's own flag: p4 measured it true on 6 of 6 rounds "
                     "with no divergence at all, and a reuse row read off a self-report would be a "
                     "claim about the policy's bookkeeping, not about transfer",
    },
    "M2.outcome_improvement": {
        "question": "did any pair finish because it had the memory, and not otherwise?",
        "unit": "one pair (both arms)",
        "numerator": "pairs where the treatment reader completed and the control reader did not",
        "denominator": "pairs that ran in both arms",
        "truth": "`episode_summary.result.complete_success` in each arm",
        "forbidden": "counting a pair where both arms completed as an improvement: on this set that is "
                     "the expected case, and the interesting quantity is the difference in the path, "
                     "which M2.trajectory_changed_and_completed carries",
    },
    "M2.followed_round_share": {
        "question": "what share of the reader's decisions does the policy record as memory-shaped?",
        "unit": "one decision round of a treatment-arm reader x one pair",
        "numerator": "rounds whose policy trace entry has `memory_followed` true",
        "denominator": "the same reader's decision rounds",
        "truth": "`episode_summary.policy.trace`, filed by `evaluation/run.py` from the policy's own "
                 "per-round record",
        "forbidden": "being reported as reuse; it is the self-report, published beside M2."
                     "trajectory_changed_and_completed so the two can be compared, never instead of it",
    },
    "M2.followed_where_control_chose_the_same": {
        "question": "how much of that self-report is a memory agreeing with the plan?",
        "unit": "one followed round",
        "numerator": "followed rounds where the control arm's round of the same index chose the same "
                     "object",
        "denominator": "followed rounds",
        "truth": "both arms' traces at one round index",
        "forbidden": "subtracting this from M2.followed_round_share to get a 'real' reuse rate: the "
                     "difference is not reuse either, it is divergence, and the behavioural row above "
                     "is the one that decides whether divergence helped",
    },
    "M3.treatment_worse_or_costlier": {
        "question": "did the memory cost the reader anything it could be charged for?",
        "unit": "one pair (both arms)",
        "numerator": "pairs where the treatment reader failed where the control completed, or used "
                     "more decision rounds or more skill calls than the control",
        "denominator": "pairs that ran in both arms",
        "truth": "`episode_summary.result` in each arm: outcome, rounds, calls",
        "forbidden": "inferring the absence of a cost from the absence of a decline: §11's negative "
                     "transfer is about what happened, and the guard's bookkeeping is M3."
                     "declines_per_refuted_round",
    },
    "M3.declines_per_refuted_round": {
        "question": "when the guard saw a refuted memory, how often did it pass an object over?",
        "unit": "one decision round whose page carried a refuted row x one pair",
        "numerator": "objects named in that round's `memory_declined`, summed",
        "denominator": "reader rounds whose retrieval record filed a refuted row",
        "truth": "`episode_summary.policy.trace`'s `memory_declined`, and the retrieval record it was "
                 "computed against",
        "forbidden": "reporting 0.0 where the denominator is empty: `not_measured` plus the store-shape "
                     "finding below is the answer, because the decline branch has no reachable input "
                     "on a success-only store (see `M3.decline_branch_reachable`)",
    },
    "M3.decline_branch_reachable": {
        "question": "could the guard's brake fire at all on the store this set writes?",
        "unit": "one pair, from the set's own arithmetic",
        "numerator": "pairs whose remembered claims include a predicate recorded `false`",
        "denominator": "pairs declared in `EM_PAIRS`",
        "truth": "`episodic_tasks._declined`, which restates `policy.py::_remembered_order`'s two "
                 "conditions over case data — this is a property of the store shape, so it is decided "
                 "without running anything",
        "forbidden": "being read as a clean result: 0/4 means the detection of §11's negative "
                     "transfer was never exercised by these episodes, and the row that says so is "
                     "this one, not M3.declines_per_refuted_round's zero",
    },
    "M4.refuted_and_still_governing": {
        "question": "when the world had contradicted the memory, did the memory still choose the next "
                    "action?",
        "unit": "one decision round whose page carried a refuted row x one pair",
        "numerator": "such rounds whose chosen object is in that round's `memory_order`",
        "denominator": "such rounds",
        "truth": "the retrieval record for the refutation and the policy trace for the choice",
        "forbidden": "pooling with M4.uncheckable_and_governing: one is a guard that lost an argument "
                     "it could hear, the other is a guard that could not hear it at all, and they need "
                     "different fixes",
    },
    "M4.uncheckable_and_governing": {
        "question": "when the plan had no row that could test the memory, did the memory still choose?",
        "unit": "one decision round of a reader whose pair declares all its remembered claims "
                "uncheckable x one pair",
        "numerator": "rounds whose chosen object is in `memory_order`",
        "denominator": "that reader's decision rounds",
        "truth": "the pair's declared `claims_the_reader_can_check` (empty) from the frozen set, and "
                 "the trace for the choice; the blindness itself is `arm.py::_memory_query`, which "
                 "builds `measured` out of this episode's own plan rows",
        "forbidden": "reading the value as 'stale': the set says these claims cannot be tested here, "
                     "not that they are false. The claim being made is that an untestable memory "
                     "governs for the whole episode — p2 measured it on every round it had — and that "
                     "no flag in the loop reports it",
    },
}

#: §11's four Memory rows in the SPEC's own order, so the table cannot quietly add a column.
SPEC_ROWS = (
    ("retrieval relevance", ("M1.rows_per_round", "M1.matched_beyond_the_batch_name",
                             "M1.rows_refuted", "M1.distinct_rows_vs_store")),
    ("successful reuse", ("M2.trajectory_changed_and_completed", "M2.outcome_improvement",
                          "M2.followed_round_share",
                          "M2.followed_where_control_chose_the_same")),
    ("negative transfer", ("M3.treatment_worse_or_costlier", "M3.declines_per_refuted_round",
                           "M3.decline_branch_reachable")),
    ("stale memory usage", ("M4.refuted_and_still_governing", "M4.uncheckable_and_governing")),
)

#: A count per unit divided by a count of units is not a proportion and gets no Wilson interval:
#: `M1.rows_per_round` reaches 3.0 on a full page, and one round can pass over two objects.
RATIO_METRICS = {"M1.rows_per_round", "M3.declines_per_refuted_round"}

_NOT_MEASURED = {
    "M3.declines_per_refuted_round": (
        "no round of any pair carried a refuted row, so the guard had nothing to decline on: read "
        "this cell beside `M3.decline_branch_reachable`, which says whether the store shape could "
        "reach the branch at all, and beside the pair-level `refuted_rounds` list. A zero here with a "
        "nonzero denominator is a different sentence — the guard saw refuted rows and passed no "
        "object over — and is published as a measured zero"),
}


# ------------------------------------------------------------------ counting ----
def _count(counts: dict[str, list[int]], metric: str, num: int = 0, den: int = 0) -> None:
    """Accumulate one contribution, and refuse an id nobody defined: a number that appears in a
    table with no 口径 next to it is a number that will be misread."""
    if metric not in METRIC_DEFINITIONS:
        raise KeyError(f"{metric!r} has no entry in METRIC_DEFINITIONS")
    slot = counts.setdefault(metric, [0, 0])
    slot[0] += int(num)
    slot[1] += int(den)


def _rate(metric: str, numerator: int, denominator: int, **extra) -> dict[str, Any]:
    """One published fraction, carrying the definition it was counted under.

    LH-2's envelope (`long_horizon._rate`) so that §11's two behaviour groups render the same columns
    in P5's report. Two things travel with every row: its own `question`/`unit`/`as_computed`, so a
    single JSON object can be audited without the module open, and `measured` — a denominator of zero
    is never a rate of zero anywhere in this file.
    """
    d = METRIC_DEFINITIONS[metric]
    n, dn = int(numerator), int(denominator)
    if metric in RATIO_METRICS:
        interval, kind = None, "count_per_unit"
    else:
        if n > dn:
            raise AssertionError(f"{metric}: a proportion cannot have numerator {n} > denominator "
                                 f"{dn}; the counting site is wrong")
        interval = list(wilson_interval(n, dn)) if dn else None
        kind = "proportion"
    return {"metric": metric, "numerator": n, "denominator": dn,
            "value": (round(n / dn, 4) if dn else None),
            "wilson_95": interval, "interval_kind": kind,
            "measured": bool(dn),
            "not_measured_reason": (None if dn else _NOT_MEASURED.get(
                metric, "no unit fell in this cell's denominator")),
            "denominator_too_small_for_a_rate": 0 < dn < 10,
            "spec_row": next((row for row, ids in SPEC_ROWS if metric in ids), None),
            "question": d["question"], "unit": d["unit"],
            "as_computed": f"{d['numerator']} / {d['denominator']}", **extra}


def _render(counts: dict[str, list[int]]) -> dict[str, dict[str, Any]]:
    return {metric: _rate(metric, *counts[metric]) for metric in sorted(counts)}


def _merge(target: dict[str, list[int]], counts: dict[str, list[int]]) -> None:
    for metric, (num, den) in counts.items():
        _count(target, metric, num, den)


def _terms_are_only_the_batch_name(terms: list[str], task_kind: str) -> bool:
    return bool(terms) and all(t == f"task_kind:{task_kind}" for t in terms)


# ----------------------------------------------------------------- one pair ----
def score_pair(root: str, pair: dict, claim: dict) -> dict:
    """The three §11 rows a pair can answer, from both arms' artifacts.

    `claim` is the pair's own arithmetic (`episodic_tasks.pair_claims()`), and it is used for exactly
    one thing: whether this pair's remembered claims are ones its reader could ever check. That is a
    property of the two task definitions, not of a run, and the loop does not record it — which is the
    whole reason `M4.uncheckable_and_governing` needs an external declaration to be countable.
    """
    treated = read_pair(root, pair, EM_TREATMENT_ARM)
    control = read_pair(root, pair, EM_CONTROL_ARM)
    reader, control_reader = treated["reader"], control["reader"]
    recall, trace = reader["recall"], reader["trace"]
    uncheckable = bool(claim["remembered_order"]) and not claim["claims_the_reader_can_check"]

    counts: dict[str, list[int]] = {}
    refuted_rounds = {int(r["round_index"]) for r in recall["rounds"] if any(r["contradicted"])}
    chosen_by_round = {int(e["round_index"]): str((e.get("args") or {}).get("object_id") or "")
                       for e in trace if e.get("round_index") is not None}
    decision_rounds = len([r for r in recall["rounds"] if r["round_index"] is not None])

    # --- M1: what was on the page, and why it was chosen
    offered = recall["rows_offered"]
    _count(counts, "M1.rows_per_round", offered, decision_rounds)
    beyond = sum(1 for r in recall["rounds"] for terms in r.get("match_terms") or []
                 if not _terms_are_only_the_batch_name(list(terms), claim["reader_task_kind"]))
    _count(counts, "M1.matched_beyond_the_batch_name", beyond, offered)
    _count(counts, "M1.rows_refuted", recall["rows_refuted"], offered)
    distinct = sorted({e for r in recall["rounds"] for e in r["experience_ids"]})
    store_rows = int(recall["rounds"][-1]["store_size"] or 0) if recall["rounds"] else 0
    _count(counts, "M1.distinct_rows_vs_store", len(distinct), store_rows)

    # --- M2: behaviour first, self-report beside it
    diverged = reader["trajectory"] != control_reader["trajectory"]
    both_complete = bool(reader["complete_success"]) and bool(control_reader["complete_success"])
    _count(counts, "M2.trajectory_changed_and_completed", int(diverged and both_complete), 1)
    _count(counts, "M2.outcome_improvement",
           int(bool(reader["complete_success"]) and not bool(control_reader["complete_success"])), 1)
    followed = sorted(int(e["round_index"]) for e in trace
                      if e.get("memory_followed") and e.get("round_index") is not None)
    _count(counts, "M2.followed_round_share", len(followed), decision_rounds)
    control_choice = {int(e["round_index"]): str((e.get("args") or {}).get("object_id") or "")
                      for e in control["reader"]["trace"] if e.get("round_index") is not None}
    same = [r for r in followed if r in control_choice
            and control_choice[r] == chosen_by_round.get(r)]
    _count(counts, "M2.followed_where_control_chose_the_same", len(same), len(followed))

    # --- M3: a cost, or the guard's own bookkeeping
    worse = ((bool(control_reader["complete_success"]) and not bool(reader["complete_success"]))
             or int(reader["decision_rounds"] or 0) > int(control_reader["decision_rounds"] or 0)
             or int(reader["skill_calls"] or 0) > int(control_reader["skill_calls"] or 0))
    _count(counts, "M3.treatment_worse_or_costlier", int(worse), 1)
    declines = sum(len(e.get("memory_declined") or {}) for e in trace)
    _count(counts, "M3.declines_per_refuted_round", declines, len(refuted_rounds))
    _count(counts, "M3.decline_branch_reachable",
           int(any(v == "false" for v in claim["recorded_truths"].values())), 1)

    # --- M4: the two shapes of a memory that governed anyway
    # Recomputed from the round's two recorded fields rather than read off `memory_followed`: the
    # flag is the instrument's own claim, and one of the two quantities below is about the instrument.
    governing = sorted(int(e["round_index"]) for e in trace
                       if e.get("round_index") is not None
                       and chosen_by_round.get(int(e["round_index"]))
                       and chosen_by_round[int(e["round_index"])]
                       in (e.get("memory_order") or []))
    _count(counts, "M4.refuted_and_still_governing",
           len(set(governing) & refuted_rounds), len(refuted_rounds))
    if uncheckable:
        _count(counts, "M4.uncheckable_and_governing", len(governing), decision_rounds)

    rendered = _render(counts)
    if "M4.uncheckable_and_governing" in rendered:
        # The id is only counted by a pair that declared its remembered claims uncheckable, so the
        # row's existence is the declaration; the field says it again because `measure` publishes one
        # merged row for all four pairs and a reader of the pooled table cannot see which pairs
        # contributed to its denominator.
        rendered["M4.uncheckable_and_governing"].update({"uncheckable_claims_declared": True})
    return {"pair_id": pair["pair_id"], "condition": pair["condition"],
            "uncheckable_claims_declared": uncheckable,
            "counts": counts, "metrics": rendered,
            "trajectories": {"treatment": reader["trajectory"],
                             "control": control_reader["trajectory"],
                             "diverged": diverged, "first_difference": next(
                                 (i for i, (a, b) in enumerate(zip(reader["trajectory"],
                                                                   control_reader["trajectory"]))
                                  if a != b), None)},
            "cost": {"treatment_rounds": reader["decision_rounds"],
                     "control_rounds": control_reader["decision_rounds"],
                     "treatment_calls": reader["skill_calls"],
                     "control_calls": control_reader["skill_calls"],
                     "treatment_outcome": reader["outcome"],
                     "control_outcome": control_reader["outcome"]},
            "recall": {"decision_rounds": decision_rounds, "rows_offered": offered,
                       "rows_refuted": recall["rows_refuted"], "distinct_experience_ids": distinct,
                       "store_rows_at_last_round": store_rows, "refuted_rounds": sorted(refuted_rounds)},
            "self_report": {"followed_rounds": followed,
                            "followed_where_control_chose_the_same": same,
                            "declines": reader["declines"]}}


# ------------------------------------------------------------------- measure ----
def measure(root: str) -> dict:
    """Every declared pair that ran in both arms, pooled and per pair, plus what did not run."""
    claims = {c["pair_id"]: dict(c, reader_task_kind=_task_kind_of(root)) for c in pair_claims()}
    with open(os.path.join(root, "pairs_run.json"), encoding="utf-8") as f:
        ledger = json.load(f)
    ran = {(row["pair_id"], row["arm"]) for row in ledger["batches"]}
    scored, absent = [], []
    pooled: dict[str, list[int]] = {}
    for pair in EM_PAIRS:
        missing = [arm for arm in EM_ARMS if (pair["pair_id"], arm) not in ran]
        if missing:
            absent.append({"pair_id": pair["pair_id"], "arms_missing": missing})
            continue
        row = score_pair(root, pair, claims[pair["pair_id"]])
        _merge(pooled, row["counts"])
        scored.append(row)
    result = {"kind": "episodic_memory_metrics", "metric_version": METRIC_VERSION,
              "root": root, "set": "em", "arms": list(EM_ARMS),
              "reference_arm_note": ("every M1/M2/M4 quantity is a treatment-arm reader; the control "
                                     "arm appears only as the other side of a two-arm difference"),
              "unit_note": ("the pair is the denominator of every M2/M3 cross-arm row: n="
                            f"{len(scored)}. A four-pair set is an existence proof for the "
                            "instruments, not a sample (§13's RQ3 asks whether the mechanism can be "
                            "measured at all; a rate over four pairs estimates nothing)"),
              "pairs_ran": [row["pair_id"] for row in scored], "absent": absent,
              "pooled": _render(pooled), "by_pair": {row["pair_id"]: row for row in scored},
              "definitions": METRIC_DEFINITIONS,
              "spec_rows": {label: list(ids) for label, ids in SPEC_ROWS},
              "not_measured": {k: _NOT_MEASURED[k] for k in sorted(_NOT_MEASURED)},
              "metric_envelope": ("every row carries `measured`, `not_measured_reason`, "
                                  "`denominator_too_small_for_a_rate`, `interval_kind`, `wilson_95`, "
                                  "`spec_row`, `question`, `unit`, `as_computed` — LH-2's shape, so "
                                  "§11's two behaviour groups are read the same way"),
              "interval_note": ("a Wilson interval here is over the units named in the row's own "
                                "`unit` field, and units inside one pair are not independent draws: "
                                "28 reader rounds are 4 pairs x 7 rounds of one deterministic policy "
                                "each, so read the interval as the width of a count, never as the "
                                "uncertainty of an effect. The pair-level rows (M2, M3) are the ones "
                                "whose denominator is a real sample, and n=4 is below any threshold "
                                "for a rate"),
              "ratio_metrics": sorted(RATIO_METRICS),
              "cost_estimate_usd": None}
    return result


def _task_kind_of(root: str) -> str:
    """The batch name the query's `task_kind` term carries, read off the ledger that ran."""
    with open(os.path.join(root, "pairs_run.json"), encoding="utf-8") as f:
        return str(json.load(f).get("set") or "em")


# ------------------------------------------------------------------- report ----
def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def render_markdown(result: dict) -> str:
    """§11's four rows, each with its numerator/denominator and the arm it was computed on.

    One row per metric id, in `SPEC_ROWS` order. An interval is printed for a proportion and never
    for a count per unit, and a `not_measured_reason` is printed in place of the numerator
    description wherever one exists, because the reason a row was not measured is the content of
    that cell.
    """
    lines = [f"## §11 Memory ({result['metric_version']}) — {result['set']} pairs, "
             f"{len(result['pairs_ran'])} ran",
             "",
             f"- root: `{result['root']}`",
             f"- arms: {', '.join(result['arms'])} (treatment first; the control is the same policy "
             "with `wo_episodic_memory`)",
             f"- {result['unit_note']}",
             f"- {result.get('interval_note') or ''}",
             f"- {result.get('metric_envelope') or ''}",
             ""]
    for label, ids in SPEC_ROWS:
        lines += [f"### {label}", "", "| metric | value | num / den | 95% Wilson | note |",
                  "|---|---|---|---|---|"]
        for metric in ids:
            row = result["pooled"].get(metric)
            if row is None:
                lines.append(f"| `{metric}` | | | | not produced by any pair that ran |")
                continue
            interval = ("—" if not row.get("wilson_95")
                        else f"{row['wilson_95'][0]:.3f}–{row['wilson_95'][1]:.3f}")
            note = row.get("not_measured_reason") or METRIC_DEFINITIONS[metric]["numerator"]
            lines.append(f"| `{metric}` | {_fmt(row['value'])} | {row['numerator']} / "
                         f"{row['denominator']} | {interval} | {note} |")
        lines.append("")
    lines += ["### Per pair", ""]
    for pair_id, row in result["by_pair"].items():
        lines.append(f"- **{pair_id}** ({row['condition']}) — divergence at index "
                     f"{row['trajectories']['first_difference']}, rounds "
                     f"{row['cost']['control_rounds']}/{row['cost']['treatment_rounds']} "
                     f"(control/treatment), outcomes {row['cost']['control_outcome']}/"
                     f"{row['cost']['treatment_outcome']}, uncheckable claims declared: "
                     f"{_fmt(row['uncheckable_claims_declared'])}")
    if result["absent"]:
        lines += ["", "### Not run", ""]
        for absent in result["absent"]:
            lines.append(f"- {absent['pair_id']}: arms missing {absent['arms_missing']}")
    return "\n".join(lines) + "\n"


def write(result: dict, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"episodic_memory_{result['metric_version'].replace('-', '_')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    with open(os.path.join(out_dir, "episodic_memory.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(result))
    return path


def _definitions_payload() -> dict:
    """The 口径 as one object, so `--definitions`, the artifact and the table cannot drift apart."""
    return {"metric_version": METRIC_VERSION,
            "spec_rows": {label: list(ids) for label, ids in SPEC_ROWS},
            "definitions": METRIC_DEFINITIONS}


def main(argv=None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    # `--out` names a directory for a measurement to be written into, so without `--measure` it is a
    # request that cannot be honoured — and refusing it is the difference between a usage error and
    # an export that silently did not happen. Checked before `--definitions` for the same reason:
    # `--definitions --out DIR` would otherwise print the 口径, exit 0 and write nothing.
    if "--out" in argv and "--measure" not in argv:
        print("usage: python -m embodied_agent.evaluation.episodic_metrics "
              "--definitions | --measure RUN_DIR [--out DIR]")
        print("`--out` is only meaningful with `--measure`; nothing was written.")
        return 2
    if "--definitions" in argv and "--measure" not in argv:
        print(json.dumps(_definitions_payload(), ensure_ascii=False, indent=1, sort_keys=True))
        return 0
    # `--definitions --measure DIR` asks for both: the definitions are the 口径, and printing them
    # beside the table is how a reader of the deliverable checks the table against them.
    if "--measure" not in argv or argv.index("--measure") + 1 >= len(argv):
        print(__doc__ if "--definitions" not in argv else __doc__.split("Zero spend:")[0])
        print("usage: python -m embodied_agent.evaluation.episodic_metrics "
              "--definitions | --measure RUN_DIR [--out DIR]")
        return 2
    root = argv[argv.index("--measure") + 1]
    out = argv[argv.index("--out") + 1] if "--out" in argv else root
    # `pairs_run.json` is what a run directory *is*; without it every later lookup in `measure`
    # raises, and a traceback from a typo'd path reads as a broken scorer rather than a typo.
    if not os.path.isfile(os.path.join(root, "pairs_run.json")):
        print(f"{root} is not an episodic pair-run directory: no pairs_run.json in it. Point "
              f"--measure at a directory an earlier --run wrote.", file=sys.stderr)
        return 2
    result = measure(root)
    path = write(result, out)
    if "--definitions" in argv:
        print(json.dumps(_definitions_payload(), ensure_ascii=False, indent=1, sort_keys=True))
    print(render_markdown(result))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
