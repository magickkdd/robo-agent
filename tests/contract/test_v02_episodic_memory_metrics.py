"""SPEC-v0.2 §11 *Memory* — the contract tests for the 口径 that scored the first `em` artifact.

Four things are under test, in this order, because each is a different way a memory metric can be
wrong while printing a plausible number:

* **a row with no definition is a row that will be misread.** The ids `score_pair` counts are
  collected from the module's own AST, so a metric added at a counting site and not to
  `METRIC_DEFINITIONS` fails here rather than in P5's report, and a definition nobody counts is a
  decoy that makes the table look broader than the arithmetic.
* **a denominator of zero is not a rate of zero.** §11's negative-transfer row is the one that would
  most happily report 0.0 for the arm whose retrieval never runs.
* **the self-report must not be able to manufacture reuse.** P3-d measured `memory_followed` true on
  6 of 6 rounds of `em_p4` with the two arms' trajectories identical, so every behavioural row here
  is computed from the two action sequences or from the retrieval page. Two tests below flip the
  flags while holding the trajectories fixed, and prove which rows may move and which may not.
* **the two shapes of a stale memory are not one number.** `M4.refuted_and_still_governing` and
  `M4.uncheckable_and_governing` need different fixes — a guard that lost an argument it could hear
  versus a guard with no vocabulary to hear it in — so the second id may exist only for a pair whose
  own case arithmetic declares its claims uncheckable.

The fixtures are synthetic and well-formed: `read_pair` is replaced by a table of arm profiles, so
`score_pair`, `measure`, `render_markdown`, `write` and `main` are exercised end to end without a
scene, a store or a model call. The delivered `/tmp/em_full` artifact is re-scored at the end and has
to reproduce its own numbers.
"""
from __future__ import annotations

import ast
import json
import os
import re

import pytest
from embodied_agent.evaluation import episodic_metrics as em
from embodied_agent.evaluation.episodic_tasks import (EM_CONTROL_ARM, EM_PAIRS, EM_TREATMENT_ARM,
                                                      pair_claims)
from embodied_agent.evaluation.report import wilson_interval

ID_RE = re.compile(r"^M\d\.[a-z][a-z_0-9]*$")
DEFINITION_KEYS = ("question", "unit", "numerator", "denominator", "truth", "forbidden")
#: §11's four Memory rows, in the SPEC's own order.
SPEC_ROW_NAMES = ("retrieval relevance", "successful reuse", "negative transfer",
                  "stale memory usage")
POINTER = re.compile(r"^as (above|the|M\d)", re.I)
BARE_RATIO = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
ROOT = "/tmp/em_full"


def _interval(num: int, den: int) -> str:
    """The cell the report will print, derived rather than transcribed."""
    lo, hi = wilson_interval(num, den)
    return f"{lo:.3f}–{hi:.3f}"


# --------------------------------------------------------------- the 口径 table ----
def test_every_id_the_scorer_counts_is_defined_before_it_is_counted():
    tree = ast.parse(open(os.path.abspath(em.__file__), encoding="utf-8").read())
    counted = {sub.value for node in tree.body if isinstance(node, ast.FunctionDef)
               for sub in ast.walk(node)
               if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
               and ID_RE.match(sub.value)}
    assert counted, "the scan found no metric ids: the pattern, not the module, is broken"
    assert not counted - set(em.METRIC_DEFINITIONS), \
        f"counted without a definition: {sorted(counted - set(em.METRIC_DEFINITIONS))}"
    assert not set(em.METRIC_DEFINITIONS) - counted, \
        f"defined but never counted: {sorted(set(em.METRIC_DEFINITIONS) - counted)}"
    with pytest.raises(KeyError):
        em._count({}, "M9.nobody_wrote_this_down", 1, 1)


def test_the_four_spec_rows_cover_every_defined_id_exactly_once():
    assert [row for row, _ in em.SPEC_ROWS] == list(SPEC_ROW_NAMES)
    flat = [metric for _, ids in em.SPEC_ROWS for metric in ids]
    assert len(flat) == len(set(flat)), "an id in two rows is a number counted twice"
    assert set(flat) == set(em.METRIC_DEFINITIONS), set(flat) ^ set(em.METRIC_DEFINITIONS)


def test_a_definition_without_its_own_arms_is_not_a_metric():
    for mid, d in em.METRIC_DEFINITIONS.items():
        assert set(d) == set(DEFINITION_KEYS), mid
        for field in DEFINITION_KEYS:
            text = str(d[field])
            assert len(text.strip()) > 10, f"{mid}.{field} is a placeholder"
            assert not POINTER.match(text.strip()), f"{mid}.{field} points at a neighbour"
            assert not BARE_RATIO.match(text), f"{mid}.{field} is a number, not a rule"
            # the definitions ship inside every artifact and `numerator` is printed in a markdown
            # table cell, so emphasis markup in them renders as a stray character
            assert "*" not in text, f"{mid}.{field} carries markup that breaks the table"


def test_the_two_stale_shapes_and_the_two_blindings_are_named_where_they_are_defined():
    """MEM-1's decisions that a later reader would otherwise take on trust.

    `forbidden` is where a counting rule says what it is *not*: LH-2 put the superseded rule in the
    definition it replaced, and this table does the same for the three errors this group is close
    enough to make by itself.
    """
    reuse = em.METRIC_DEFINITIONS["M2.trajectory_changed_and_completed"]
    assert "memory_followed" in reuse["forbidden"] and "p4" in reuse["forbidden"]
    stale = em.METRIC_DEFINITIONS["M4.refuted_and_still_governing"]
    assert "pooling" in stale["forbidden"] and "different fixes" in stale["forbidden"]
    uncheckable = em.METRIC_DEFINITIONS["M4.uncheckable_and_governing"]
    assert "not that they are false" in uncheckable["forbidden"], \
        "the second shape is 'never tested', not 'wrong', and only the definition says so"
    assert "_memory_query" in uncheckable["truth"], "the mechanism has to be cited, not the vibe"
    assert "relevance_within_a_batch" in \
        em.METRIC_DEFINITIONS["M1.matched_beyond_the_batch_name"]["forbidden"], \
        "the frozen set's own claim about the batch-name term is what this row tests"
    assert "wo_episodic_memory" in em.METRIC_DEFINITIONS["M1.rows_per_round"]["forbidden"]


# ---------------------------------------------------------------- rate arithmetic ----
def test_every_rate_row_carries_its_own_arithmetic_and_no_borrowed_interval():
    for mid in em.METRIC_DEFINITIONS:
        row = em._rate(mid, 3, 7)
        d = em.METRIC_DEFINITIONS[mid]
        assert (row["numerator"], row["denominator"]) == (3, 7), mid
        assert row["value"] == round(3 / 7, 4), mid
        assert row["measured"] is True and row["not_measured_reason"] is None, mid
        assert row["question"] == d["question"] and row["unit"] == d["unit"], mid
        assert row["as_computed"] == f"{d['numerator']} / {d['denominator']}", mid
        assert row["spec_row"] == next(r for r, ids in em.SPEC_ROWS if mid in ids), mid
        assert row["denominator_too_small_for_a_rate"] is True, mid
        if mid in em.RATIO_METRICS:
            assert row["interval_kind"] == "count_per_unit" and row["wilson_95"] is None, mid
        else:
            assert row["interval_kind"] == "proportion", mid
            lo, hi = row["wilson_95"]
            assert lo <= row["value"] <= hi and 0.0 <= lo and hi <= 1.0, mid
    assert em.RATIO_METRICS <= set(em.METRIC_DEFINITIONS), "a ratio nobody defined"


def test_a_count_per_unit_is_not_clipped_and_a_proportion_over_one_is_a_broken_count():
    """Three rows on one round, two objects declined in one round: both legitimate readings.

    Every other id is a share, and a share above 1.0 can only come from a counting site that mixed
    two populations — which is what the assertion is there to catch.
    """
    assert em._rate("M1.rows_per_round", 3, 1)["value"] == 3.0
    assert em._rate("M3.declines_per_refuted_round", 2, 1)["value"] == 2.0
    for mid in sorted(set(em.METRIC_DEFINITIONS) - em.RATIO_METRICS):
        with pytest.raises(AssertionError):
            em._rate(mid, 5, 4)


def test_an_empty_denominator_is_not_measured_rather_than_zero():
    for mid in em.METRIC_DEFINITIONS:
        row = em._rate(mid, 0, 0)
        assert row["measured"] is False and row["value"] is None, mid
        assert row["wilson_95"] is None, mid
        assert row["not_measured_reason"] == em._NOT_MEASURED.get(
            mid, "no unit fell in this cell's denominator"), mid
        assert len(row["not_measured_reason"]) > 20, mid
    zero = em._rate("M3.declines_per_refuted_round", 0, 5)
    assert zero["measured"] is True and zero["value"] == 0.0
    assert zero["not_measured_reason"] is None, "a guard that saw refuted rows and declined nothing"


def test_a_not_measured_reason_does_not_bake_in_a_number_it_did_not_measure():
    """The one way a static string in this file could become false: quoting a result.

    `_NOT_MEASURED` is printed whenever a cell is empty, on any root, so a reason that names a
    fraction measured on one artifact would be a wrong statement on the next.
    """
    for mid, why in em._NOT_MEASURED.items():
        assert mid in em.METRIC_DEFINITIONS, "a reason for an undefined id"
        assert not re.search(r"\b\d+\s*/\s*\d+\b", why), f"{mid}'s reason cites {why}"


def test_pooling_sums_integers_rather_than_averaging_fractions():
    """A pair with one round and a pair with eleven are not two equal measurements.

    A mean-of-per-pair-rates implementation passes on a single pair and fails here.
    """
    counts: dict[str, list[int]] = {}
    em._merge(counts, {"M2.followed_round_share": [1, 1]})
    em._merge(counts, {"M2.followed_round_share": [0, 20]})
    row = em._render(counts)["M2.followed_round_share"]
    assert (row["numerator"], row["denominator"]) == (1, 21)
    assert row["value"] == round(1 / 21, 4)
    assert row["value"] != round((1.0 + 0.0) / 2, 4), "the arithmetic that would flatter a cell"


def test_a_row_matched_only_by_the_batch_name_is_the_harness_selecting_it():
    assert em._terms_are_only_the_batch_name(["task_kind:em"], "em") is True
    assert em._terms_are_only_the_batch_name(["task_kind:em", "region:tray_a"], "em") is False
    assert em._terms_are_only_the_batch_name(["region:tray_a"], "em") is False
    # a row with no recorded terms has no recorded reason, which is not evidence of another one
    assert em._terms_are_only_the_batch_name([], "em") is False


# ------------------------------------------------------------------ one pair ----
def _page(index, ids, *, store_size, contradicted=None, terms=None):
    contradicted = contradicted or [False] * len(ids)
    terms = terms if terms is not None else [["task_kind:em", "predicate:placed:obj_a:tray_a"]
                                             for _ in ids]
    return {"round_index": index, "store_size": store_size, "rows": len(ids),
            "experience_ids": list(ids), "relevance": [13.0] * len(ids),
            "match_terms": [list(t) for t in terms], "contradicted": list(contradicted),
            "clash_counts": [0] * len(ids)}


def _recall(*pages):
    return {"rounds": list(pages),
            "rounds_with_a_row": sum(1 for p in pages if p["rows"]),
            "rows_offered": sum(p["rows"] for p in pages),
            "rows_refuted": sum(1 for p in pages for flag in p["contradicted"] if flag),
            "first_round_rows": pages[0]["rows"] if pages else 0,
            "first_round_refuted": bool(pages and pages[0]["contradicted"]
                                        and pages[0]["contradicted"][0])}


def _episode(*, objects, order=(), followed=(), declined=(), page=None, trajectory=None,
             complete=True, rounds=None, calls=None, outcome="success"):
    """A reader reduced to the fields `score_pair` reads, with the policy's own trace beside it.

    `rounds` and `calls` default to what the trajectory implies, so a fixture that only says "the
    treatment took one more round" is stating the cost rather than typing two numbers that agree.
    """
    trace = [{"round_index": i, "args": {"object_id": obj},
              "memory_order": list(order[i]) if i < len(order) else [],
              "memory_followed": i in followed,
              "memory_declined": dict(declined[i]) if i < len(declined) else {}}
             for i, obj in enumerate(objects)]
    return {"trajectory": list(trajectory or [f"pick {o}" for o in objects]),
            "complete_success": complete, "outcome": outcome,
            "decision_rounds": len(objects) if rounds is None else rounds,
            "skill_calls": 2 * len(objects) if calls is None else calls,
            "declines": [d for d in declined if d], "trace": trace,
            "recall": page if page is not None else _recall()}


def _arms(**by_pair):
    """Replace `read_pair` with a table, so no artifact has to exist to be scored."""
    def fake(root, pair, arm):
        return {"pair_id": pair["pair_id"], "condition": pair["condition"], "arm": arm,
                "reader": by_pair[pair["pair_id"]][arm]}
    return fake


def _row(pair_id):
    return next(p for p in EM_PAIRS if p["pair_id"] == pair_id)


def _claim(pair_id, **over):
    claim = dict(next(c for c in pair_claims() if c["pair_id"] == pair_id))
    claim["reader_task_kind"] = "em"
    claim.update(over)
    return claim


def _score(monkeypatch, pair_id, treatment, control):
    monkeypatch.setattr(em, "read_pair", _arms(**{
        pair_id: {EM_TREATMENT_ARM: treatment, EM_CONTROL_ARM: control}}))
    return em.score_pair("/ignored", _row(pair_id), _claim(pair_id))


def _count(row, metric):
    return row["counts"].get(metric, [0, 0])


def _ledger(tmp_path, pair_ids, *, root_name="em_root", on_disk=False):
    """A `pairs_run.json` naming the pairs that ran, in both arms.

    `on_disk=True` points the batch records at a real (empty) directory, which the production
    `read_pair` refuses; the default points at a path that does not exist, which only a patched
    reader is allowed to ignore.
    """
    root = tmp_path / root_name
    (root / "b").mkdir(parents=True, exist_ok=True)
    ledger = {"set": "em", "batches": [
        {"pair_id": p["pair_id"], "condition": p["condition"], "arm": arm,
         "batches": {role: {"case_id": p[role], "root": str(root / "b")}
                     for role in ("writer", "reader")}}
        for p in EM_PAIRS if p["pair_id"] in pair_ids for arm in em.EM_ARMS]}
    if not on_disk:
        for batch in ledger["batches"]:
            batch["batches"] = {role: dict(entry, root=str(tmp_path / "nothing"))
                                for role, entry in batch["batches"].items()}
    with open(root / "pairs_run.json", "w", encoding="utf-8") as f:
        json.dump(ledger, f)
    return str(root)


# --------------------------------------------------------- the two stale shapes ----
def test_refuted_and_governing_is_recomputed_from_the_page_and_the_choice(monkeypatch):
    """`memory_followed` says the policy ranked the row it picked; `M4` asks whether a refuted row
    still chose the action. A round can be one and not the other, and the instrument's own claim is
    not the truth source for a row about the instrument."""
    page = _recall(_page(0, ["e1"], store_size=1),
                   _page(1, ["e1"], store_size=1, contradicted=[True]),
                   _page(2, ["e1"], store_size=1, contradicted=[True]))
    silent = _episode(objects=["a", "b", "c"], order=[[], ["b"], ["c"]], page=page)
    loud = _episode(objects=["a", "b", "c"], order=[[], ["b"], ["c"]], page=page, followed={1, 2})
    control = _episode(objects=["a", "b", "c"])
    off = _score(monkeypatch, "em_p1", silent, control)
    on = _score(monkeypatch, "em_p1", loud, control)
    assert _count(off, "M4.refuted_and_still_governing") == [2, 2], \
        "the guard heard the refutation, ranked the refuted object and chose it"
    assert _count(on, "M4.refuted_and_still_governing") == [2, 2], "and the flag changes nothing"
    assert _count(off, "M2.followed_round_share") == [0, 3]
    assert _count(on, "M2.followed_round_share") == [2, 3]


def test_only_a_pair_that_declares_its_claims_uncheckable_produces_the_second_stale_row(
        monkeypatch):
    """`M4.uncheckable_and_governing` has no flag inside the loop that could report it, so the row
    exists only where the *case arithmetic* says the plan has no row that could test the memory."""
    page = _recall(_page(0, ["e"], store_size=1), _page(1, ["e"], store_size=1))
    treated = _episode(objects=["a", "b"], order=[["a", "b"], ["a", "b"]], page=page)
    control = _episode(objects=["a", "b"])
    declared = _score(monkeypatch, "em_p2", treated, control)
    assert declared["uncheckable_claims_declared"] is True
    assert _count(declared, "M4.uncheckable_and_governing") == [2, 2]
    assert declared["metrics"]["M4.uncheckable_and_governing"]["uncheckable_claims_declared"] is True
    checkable = _score(monkeypatch, "em_p1", treated, control)
    assert "M4.uncheckable_and_governing" not in checkable["counts"], \
        "a pair whose claims are checkable contributes rounds to the other row, not to this one"
    assert _count(checkable, "M4.refuted_and_still_governing") == [0, 0], \
        "and a page nobody refuted is neither shape of stale: it is memory saying nothing yet"


def test_an_uncheckable_claim_about_a_region_the_plan_names_is_not_the_second_shape(monkeypatch):
    """The declaration, not the arithmetic, is what selects the row — so the test has to move the
    declaration and show the row follows it, or `score_pair` could be reading its own assumption."""
    page = _recall(_page(0, ["e"], store_size=1))
    treated = _episode(objects=["a"], order=[["a"]], page=page)
    control = _episode(objects=["a"])
    monkeypatch.setattr(em, "read_pair", _arms(em_p1={EM_TREATMENT_ARM: treated,
                                                      EM_CONTROL_ARM: control}))
    claim = _claim("em_p1", claims_the_reader_can_check=[])
    row = em.score_pair("/x", _row("em_p1"), claim)
    assert _count(row, "M4.uncheckable_and_governing") == [1, 1], \
        "a pair the set declares uncheckable is counted as the second shape whatever its id"
    empty_remembered = em.score_pair("/x", _row("em_p1"),
                                     _claim("em_p1", claims_the_reader_can_check=[],
                                            remembered_order={}))
    assert "M4.uncheckable_and_governing" not in empty_remembered["counts"], \
        "and a pair that remembered nothing has no memory governing, checkable or not"


# ------------------------------------------------------------------ reuse rows ----
def test_an_agreeing_memory_is_not_reuse_and_a_moving_one_is_not_harm(monkeypatch):
    """`em_p4` and its mirror, in one test: the flag and the behaviour point opposite ways.

    Held fixed: the treatment's own trace, which says "I followed the memory" on every round.
    What changes is the control arm's action sequence.
    """
    page = _recall(_page(0, ["e"], store_size=1), _page(1, ["e"], store_size=1))
    agrees = _episode(objects=["a", "b"], order=[["a"], ["b"]], followed={0, 1}, page=page)
    same = _episode(objects=["a", "b"], trajectory=["pick a", "pick b"])
    row = _score(monkeypatch, "em_p4", agrees, same)
    assert _count(row, "M2.followed_round_share") == [2, 2], "the self-report is at its maximum"
    assert _count(row, "M2.trajectory_changed_and_completed") == [0, 1], "and reuse is nil"
    assert _count(row, "M2.followed_where_control_chose_the_same") == [2, 2], \
        "which is exactly what the coincidence rate is there to say"
    assert row["trajectories"]["diverged"] is False

    differs = _episode(objects=["b", "a"], order=[["b"], ["a"]], followed={0, 1}, page=page)
    moved = _score(monkeypatch, "em_p4", differs, same)
    assert _count(moved, "M2.trajectory_changed_and_completed") == [1, 1]
    assert _count(moved, "M2.followed_round_share") == [2, 2], "the flag did not move, the path did"
    assert moved["trajectories"]["first_difference"] == 0


def test_a_divergent_pair_that_did_not_finish_is_not_counted_as_reuse(monkeypatch):
    """Decision 3 of the module's docstring: reuse is a change *and* a completion, because a
    divergence into a failure is M3's row, and one number may not be both."""
    page = _recall(_page(0, ["e"], store_size=1))
    treatment = _episode(objects=["a", "b"], order=[["a"]], page=page, complete=False,
                         outcome="partial_success",
                         trajectory=["pick a", "pick b"])
    control = _episode(objects=["a", "b"], trajectory=["pick b", "pick a"])
    row = _score(monkeypatch, "em_p1", treatment, control)
    assert _count(row, "M2.trajectory_changed_and_completed") == [0, 1]
    assert _count(row, "M2.outcome_improvement") == [0, 1]
    assert _count(row, "M3.treatment_worse_or_costlier") == [1, 1]


def test_only_a_treatment_that_finished_alone_counts_as_an_improvement(monkeypatch):
    page = _recall(_page(0, ["e"], store_size=1))
    finished = _episode(objects=["a", "b"], page=page)
    stuck = _episode(objects=["a", "b"], complete=False, outcome="budget_exhausted")
    assert _count(_score(monkeypatch, "em_p1", finished, stuck), "M2.outcome_improvement") == [1, 1]
    assert _count(_score(monkeypatch, "em_p1", stuck, finished), "M2.outcome_improvement") == [0, 1]
    assert _count(_score(monkeypatch, "em_p1", stuck, stuck), "M2.outcome_improvement") == [0, 1]


def test_a_memory_that_costs_rounds_or_calls_is_charged_even_when_both_arms_finish(monkeypatch):
    """§11's negative transfer, defined as an event rather than as a flag: a failed episode, an
    extra round or an extra call each charge the pair, and finishing at the same cost does not."""
    control = _episode(objects=["a", "b"], calls=4)
    same = _score(monkeypatch, "em_p1", _episode(objects=["a", "b"], calls=4), control)
    assert _count(same, "M3.treatment_worse_or_costlier") == [0, 1]
    longer = _score(monkeypatch, "em_p1",
                    _episode(objects=["a", "b"], calls=4, rounds=3,
                             trajectory=["pick a", "wait", "pick b"]), control)
    assert _count(longer, "M3.treatment_worse_or_costlier") == [1, 1], "one round more is a cost"
    extra_calls = _score(monkeypatch, "em_p1", _episode(objects=["a", "b"], calls=6), control)
    assert _count(extra_calls, "M3.treatment_worse_or_costlier") == [1, 1], "so is one call more"
    cheaper = _score(monkeypatch, "em_p1", _episode(objects=["a", "b"], calls=3), control)
    assert _count(cheaper, "M3.treatment_worse_or_costlier") == [0, 1], "and a saving is not harm"


# --------------------------------------------------------- page and store rows ----
def test_a_decline_is_counted_against_the_rounds_the_guard_could_act_on(monkeypatch):
    page = _recall(_page(0, ["e"], store_size=1, contradicted=[True]),
                   _page(1, ["e"], store_size=1, contradicted=[True]),
                   _page(2, ["e"], store_size=1))
    treated = _episode(objects=["a", "b", "c"], page=page, declined=[{}, {"obj_z": "refuted"}, {}])
    row = _score(monkeypatch, "em_p1", treated, _episode(objects=["a", "b", "c"]))
    assert _count(row, "M3.declines_per_refuted_round") == [1, 2], \
        "one object passed over, on one of the two rounds whose page was refuted"
    assert row["recall"]["refuted_rounds"] == [0, 1]
    assert row["self_report"]["declines"] == [{"obj_z": "refuted"}]
    assert row["metrics"]["M3.declines_per_refuted_round"]["measured"] is True


def test_a_round_that_was_never_refuted_is_not_in_the_decline_denominator(monkeypatch):
    page = _recall(_page(0, ["e"], store_size=1), _page(1, ["e"], store_size=1))
    treated = _episode(objects=["a", "b"], page=page, declined=[{}, {"obj_z": "refuted"}])
    row = _score(monkeypatch, "em_p1", treated, _episode(objects=["a", "b"]))
    assert _count(row, "M3.declines_per_refuted_round") == [1, 0], \
        "a decline with no refuted round is a numerator with no denominator: the row refuses it"
    assert row["metrics"]["M3.declines_per_refuted_round"]["measured"] is False
    assert "refuted" in row["metrics"]["M3.declines_per_refuted_round"]["not_measured_reason"]


def test_the_store_denominator_is_the_last_round_and_a_repeated_row_is_not_reached_twice(
        monkeypatch):
    page = _recall(_page(0, ["e1", "e1"], store_size=6), _page(1, ["e1"], store_size=4))
    treated = _episode(objects=["a", "b"], page=page)
    row = _score(monkeypatch, "em_p1", treated, _episode(objects=["a", "b"]))
    assert _count(row, "M1.distinct_rows_vs_store") == [1, 4], \
        "one distinct id against the store the reader last saw, not three pages against six"
    assert _count(row, "M1.rows_per_round") == [3, 2]
    assert row["recall"]["store_rows_at_last_round"] == 4
    assert row["recall"]["distinct_experience_ids"] == ["e1"]


def test_the_control_arms_page_cannot_touch_a_relevance_row(monkeypatch):
    """Decision 1: every `M1.*` is a treatment-arm quantity.

    The control's retrieval page is set first to nothing at all — what `wo_episodic_memory` files —
    and then to a store nine times the size, and no M1 number moves. A pooling bug that read both
    arms would turn the switch itself into the largest denominator in the table.
    """
    treated = _episode(objects=["a", "b"], page=_recall(_page(0, ["e1"], store_size=1),
                                                        _page(1, ["e1"], store_size=1)))
    empty = _episode(objects=["a", "b"])
    crowded = _episode(objects=["a", "b"], page=_recall(
        *[_page(i, [f"x{i}a", f"x{i}b", f"x{i}c", f"x{i}d"], store_size=9) for i in range(4)]))
    off = _score(monkeypatch, "em_p1", treated, empty)
    on = _score(monkeypatch, "em_p1", treated, crowded)
    for mid in sorted(m for m in em.METRIC_DEFINITIONS if m.startswith("M1.")):
        assert off["counts"][mid] == on["counts"][mid], f"{mid} read the control's page"


# ------------------------------------------------------------------- measure ----
def _four_pair_fixture():
    """A synthetic root whose pooled table has hand-computed values, asserted in the next test.

    The four pairs stand in for the four shapes P3-d measured: a memory that moved the first hand
    (`em_p1`), a memory that could not be tested at all (`em_p2`), a page that said nothing
    (`em_p3`) and a memory that agreed with the plan (`em_p4`).
    """
    p1_t = _episode(objects=["a", "b", "c"], order=[[], ["b"], ["c"]], followed={1, 2},
                    page=_recall(_page(0, ["e1"], store_size=1),
                                 _page(1, ["e1"], store_size=1, contradicted=[True]),
                                 _page(2, ["e1"], store_size=1, contradicted=[True])),
                    trajectory=["pick a", "pick b", "pick c"])
    p1_c = _episode(objects=["a", "c", "b"], trajectory=["pick a", "pick c", "pick b"])
    # `em_p2`'s page is never contradicted — that is the pair's defect: nothing on the plan could
    # check it, so the guard is silent for all seven rounds and the memory sets the order anyway.
    p2_t = _episode(objects=["x", "y", "z"], order=[["x", "y", "z"], ["x", "y", "z"],
                                                    ["x", "y", "z"]],
                    page=_recall(*[_page(i, ["e2"], store_size=1) for i in range(3)]))
    p2_c = _episode(objects=["x", "y", "z"])
    p3_t = _episode(objects=["a", "b", "c"],
                    page=_recall(_page(0, [], store_size=0), _page(1, [], store_size=0),
                                 _page(2, [], store_size=0)))
    p3_c = _episode(objects=["a", "b", "c"])
    p4_t = _episode(objects=["a", "a"], order=[["a"], ["a"]], followed={0, 1},
                    page=_recall(_page(0, ["e4"], store_size=1, terms=[["task_kind:em"]]),
                                 _page(1, ["e4"], store_size=1, terms=[["task_kind:em"]])))
    p4_c = _episode(objects=["a", "a"])
    return {"em_p1": {EM_TREATMENT_ARM: p1_t, EM_CONTROL_ARM: p1_c},
            "em_p2": {EM_TREATMENT_ARM: p2_t, EM_CONTROL_ARM: p2_c},
            "em_p3": {EM_TREATMENT_ARM: p3_t, EM_CONTROL_ARM: p3_c},
            "em_p4": {EM_TREATMENT_ARM: p4_t, EM_CONTROL_ARM: p4_c}}


def test_measure_produces_the_pooled_arithmetic_the_fixture_was_built_to_imply(tmp_path,
                                                                               monkeypatch):
    """Every numerator and denominator at once. This is the test that catches a contribution moved
    from one `_count` call to another, which no single-row test can see."""
    fixture = _four_pair_fixture()
    monkeypatch.setattr(em, "read_pair", _arms(**fixture))
    pooled = em.measure(_ledger(tmp_path, [p["pair_id"] for p in em.EM_PAIRS]))["pooled"]
    expected = {"M1.rows_per_round": (8, 11), "M1.matched_beyond_the_batch_name": (6, 8),
                "M1.rows_refuted": (2, 8), "M1.distinct_rows_vs_store": (3, 3),
                "M2.trajectory_changed_and_completed": (1, 4), "M2.outcome_improvement": (0, 4),
                "M2.followed_round_share": (4, 11),
                "M2.followed_where_control_chose_the_same": (2, 4),
                "M3.treatment_worse_or_costlier": (0, 4), "M3.declines_per_refuted_round": (0, 2),
                "M4.refuted_and_still_governing": (2, 2), "M4.uncheckable_and_governing": (3, 3)}
    for mid, (num, den) in expected.items():
        got = (pooled[mid]["numerator"], pooled[mid]["denominator"])
        assert got == (num, den), f"{mid}: expected {num}/{den}, the fixture says {got}"
    reachable = sum(int(any(v == "false" for v in c["recorded_truths"].values()))
                    for c in pair_claims())
    branch = pooled["M3.decline_branch_reachable"]
    assert (branch["numerator"], branch["denominator"]) == (reachable, 4)
    assert branch["measured"] is True, "0/4 reachable is a measurement, not an absence"


def test_measure_scores_only_the_pairs_the_ledger_says_ran_and_names_the_rest(tmp_path,
                                                                             monkeypatch):
    """The ledger, not this file, decides the denominator.

    Dropping a pair from a batch is therefore visible as an `absent` row and a smaller denominator,
    never as a silent 0.0 — and an id only `em_p2` can produce is absent from the pooled table
    rather than reported as zero when `em_p2` did not run.
    """
    fixture = _four_pair_fixture()
    monkeypatch.setattr(em, "read_pair", _arms(**fixture))
    result = em.measure(_ledger(tmp_path, ["em_p1", "em_p3"]))
    assert result["pairs_ran"] == ["em_p1", "em_p3"]
    assert [a["pair_id"] for a in result["absent"]] == ["em_p2", "em_p4"]
    pooled = result["pooled"]
    assert (pooled["M1.rows_per_round"]["numerator"],
            pooled["M1.rows_per_round"]["denominator"]) == (3, 6), "3 rows over 3 rounds, then none"
    assert pooled["M1.matched_beyond_the_batch_name"]["denominator"] == 3
    assert (pooled["M4.refuted_and_still_governing"]["numerator"],
            pooled["M4.refuted_and_still_governing"]["denominator"]) == (2, 2)
    assert "M4.uncheckable_and_governing" not in pooled
    p3 = result["by_pair"]["em_p3"]["metrics"]
    assert p3["M1.rows_per_round"]["measured"] is True and p3["M1.rows_per_round"]["value"] == 0.0
    for mid in ("M1.matched_beyond_the_batch_name", "M1.rows_refuted", "M1.distinct_rows_vs_store",
                "M4.refuted_and_still_governing"):
        assert p3[mid]["measured"] is False and p3[mid]["value"] is None, mid


def test_a_ledger_that_names_an_episode_that_is_not_on_disk_fails_rather_than_scoring_nothing(
        tmp_path):
    """`absent` is a claim about the ledger, so the ledger and the disk have to be reconciled by a
    refusal, not by a rate computed over the pairs that happened to survive."""
    with pytest.raises((ValueError, FileNotFoundError)):
        em.measure(_ledger(tmp_path, ["em_p1"], on_disk=True))


# ---------------------------------------------------------------- the artifact ----
def _result(tmp_path, monkeypatch):
    fixture = _four_pair_fixture()
    monkeypatch.setattr(em, "read_pair", _arms(**fixture))
    return em.measure(_ledger(tmp_path, [p["pair_id"] for p in em.EM_PAIRS]))


def test_the_markdown_prints_every_defined_row_once_in_the_specs_order(tmp_path, monkeypatch):
    text = em.render_markdown(_result(tmp_path, monkeypatch))
    for label in SPEC_ROW_NAMES:
        assert f"### {label}" in text
    for mid in em.METRIC_DEFINITIONS:
        cell = f"| `{mid}` |"
        assert text.count(cell) == 1, f"{mid} printed {text.count(cell)} times"
    assert [line.split("`")[1] for line in text.splitlines() if line.startswith("| `M")] == \
        [metric for _, ids in em.SPEC_ROWS for metric in ids]
    assert "| 6 / 8 | " + _interval(6, 8) + " |" in text, "a proportion carries its own interval"
    assert "| 8 / 11 | — |" in text, "a count per unit carries none"


def test_a_row_no_pair_could_produce_is_named_as_not_produced_rather_than_left_out(
        tmp_path, monkeypatch):
    """`M4.uncheckable_and_governing` on a root without `em_p2` is an absence with a reason.

    The same cell filled with 0.0 would read as "no stale memory governed", which is the sentence
    this group exists to refuse.
    """
    monkeypatch.setattr(em, "read_pair", _arms(**_four_pair_fixture()))
    result = em.measure(_ledger(tmp_path, ["em_p1", "em_p3", "em_p4"]))
    assert "M4.uncheckable_and_governing" not in result["pooled"]
    text = em.render_markdown(result)
    line = next(line for line in text.splitlines()
                if line.startswith("| `M4.uncheckable_and_governing`"))
    assert "not produced by any pair that ran" in line and "| | | |" in line
    assert "### Not run" in text and "em_p2" in text


def test_the_written_artifact_is_the_table_and_the_definitions_travel_with_it(tmp_path,
                                                                             monkeypatch):
    result = _result(tmp_path, monkeypatch)
    out = str(tmp_path / "out")
    path = em.write(result, out)
    assert path.endswith(f"episodic_memory_{em.METRIC_VERSION.replace('-', '_')}.json")
    with open(path, encoding="utf-8") as f:
        back = json.load(f)
    assert back["definitions"] == em.METRIC_DEFINITIONS
    assert back["spec_rows"] == {label: list(ids) for label, ids in em.SPEC_ROWS}
    assert back["pooled"] == json.loads(json.dumps(result["pooled"]))
    assert back["ratio_metrics"] == sorted(em.RATIO_METRICS)
    assert back["cost_estimate_usd"] is None
    assert "interval" in back["interval_note"] and "unit_note" in back
    with open(os.path.join(out, "episodic_memory.md"), encoding="utf-8") as f:
        assert f.read() == em.render_markdown(result)


def test_main_refuses_a_flag_combination_that_would_name_a_directory_and_write_nothing(
        tmp_path, monkeypatch, capsys):
    assert em.main(["--definitions"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["metric_version"] == em.METRIC_VERSION
    assert printed["definitions"] == em.METRIC_DEFINITIONS
    assert printed["spec_rows"] == {label: list(ids) for label, ids in em.SPEC_ROWS}

    assert em.main(["--out", str(tmp_path / "never")]) == 2
    assert em.main(["--definitions", "--out", str(tmp_path / "never")]) == 2
    assert not os.path.exists(tmp_path / "never"), "a usage error may not create an artifact"
    assert "usage:" in capsys.readouterr().out

    result = _result(tmp_path, monkeypatch)
    monkeypatch.setattr(em, "measure", lambda root: result)
    # a real run root: `main` refuses a path with no `pairs_run.json` before it reaches `measure`,
    # so the fake root below has to be a directory a `--run` could have written
    root = tmp_path / "run"
    root.mkdir()
    (root / "pairs_run.json").write_text(json.dumps({"set": "em"}), encoding="utf-8")
    assert em.main(["--definitions", "--measure", str(root), "--out",
                    str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert '"metric_version": "MEM-1"' in out and "### stale memory usage" in out
    assert os.path.exists(os.path.join(str(tmp_path / "out"),
                                       f"episodic_memory_{em.METRIC_VERSION.replace('-', '_')}.json"))


def test_a_path_that_is_not_a_run_directory_is_a_usage_error_rather_than_a_traceback(
        tmp_path, capsys):
    """The one boundary `main` cannot delegate to `measure`: a typo'd directory is user input, and
    the scorer's own `open()` calls would otherwise report it as a crash inside the instrument."""
    nothing = tmp_path / "typo"
    assert em.main(["--measure", str(nothing)]) == 2
    err = capsys.readouterr().err
    assert str(nothing) in err and "pairs_run.json" in err, err
    assert not nothing.exists(), "a refused path may not be created on the way to refusing it"
    half = tmp_path / "halfway"
    (half / "episodes").mkdir(parents=True)
    assert em.main(["--measure", str(half)]) == 2
    assert "pairs_run.json" in capsys.readouterr().err


def test_the_delivered_artifact_reproduces_its_own_numbers():
    """Re-scoring the root that produced `/tmp/em_full/episodic_memory_MEM_1.json` must be a no-op.

    A rate that moves when no counting rule changed is a rate that was never stable, and the
    artifact is the deliverable a reader of §11 will check the report against.
    """
    if not os.path.isfile(os.path.join(ROOT, "episodic_memory_MEM_1.json")):
        pytest.skip(f"{ROOT} is the delivered batch; it is not on this machine")
    with open(os.path.join(ROOT, "episodic_memory_MEM_1.json"), encoding="utf-8") as f:
        delivered = json.load(f)
    again = em.measure(ROOT)
    assert again["metric_version"] == delivered["metric_version"] == em.METRIC_VERSION
    assert again["pairs_ran"] == delivered["pairs_ran"] and again["absent"] == delivered["absent"]
    assert set(again["pooled"]) == set(delivered["pooled"])
    for mid, row in delivered["pooled"].items():
        fresh = again["pooled"][mid]
        assert (fresh["numerator"], fresh["denominator"], fresh["value"]) == \
            (row["numerator"], row["denominator"], row["value"]), mid
    for pair_id, pair in delivered["by_pair"].items():
        for mid, counts in pair["counts"].items():
            assert again["by_pair"][pair_id]["counts"][mid] == counts, f"{pair_id}.{mid}"


def test_the_delivered_table_has_no_asterisk_that_would_escape_a_markdown_cell():
    """The rendering defect P2-e hit at report time, refused at test time instead.

    Every string `render_markdown` puts inside a `| … |` cell comes from `METRIC_DEFINITIONS` or
    `_NOT_MEASURED`, so those are the two places to check.
    """
    for mid, d in em.METRIC_DEFINITIONS.items():
        assert all("*" not in str(v) for v in d.values()), mid
    assert all("*" not in v for v in em._NOT_MEASURED.values())
    if os.path.isfile(os.path.join(ROOT, "episodic_memory.md")):
        with open(os.path.join(ROOT, "episodic_memory.md"), encoding="utf-8") as f:
            table = [line for line in f if line.startswith("| `M")]
        assert table and all(line.count("|") == 6 for line in table), \
            "a cell containing a pipe or a stray character changes the column count"


# ---------------------------------------------------------------- the CLI entry ----
def test_the_production_cli_reaches_the_scorer_rather_than_a_copy_of_it(tmp_path, capsys):
    """A flag the handler never reads still parses, prints nothing and exits 0, which is exactly how
    a dead instrument looks alive. `em-pairs` delegates to the module's own `main`, so the 口径 the
    CLI prints is the one its arithmetic used and no second list of definitions can exist."""
    from embodied_agent import cli

    assert cli.main(["em-pairs", "--memory-definitions"]) == cli.EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    assert printed["metric_version"] == em.METRIC_VERSION
    assert printed["definitions"] == em.METRIC_DEFINITIONS

    assert cli.main(["em-pairs", "--memory-metrics", str(tmp_path / "absent")]) \
        == cli.EXIT_CONFIG_ERROR
    assert "pairs_run.json" in capsys.readouterr().err
    assert not (tmp_path / "absent").exists()

    # one action per invocation: the gate that kept `--run` and `--claims` apart names these two
    assert cli.main(["em-pairs", "--memory-definitions", "--claims"]) == cli.EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "--memory-metrics" in err and "--memory-definitions" in err, err
