"""P1-f contract tests: the arithmetic of §11's VLM metrics, and none of their values.

`evaluation/vlm_contrast.py` exists to fix a 口径 before a number is computed, so what a test
may pin here is the *relationship between a number and its definition* — that every counted id
is defined before it is counted, that a row carries its own numerator and denominator, that two
accounts which must not be summed are not summed, that a rate's denominator is the questions
this module actually asked. What no test in this file pins is a *value*: not the over-claim
rate, not the grounding accuracy, not a millimetre. Those are §11's findings, and a threshold
written here would turn the one number that has to stay honest into a fixture — the same rule
P1-d wrote for `verify_percept` and the same rule `work/p1_verify_check.py` follows.

Everything runs on real rendered frames of two frozen cases (`dev_c2`, three bodies, and
`dev_c5`, five), through the same `build_scene`/`capture`/`world_state_from_percept` path the
sensing arm uses, so a failure names a measurement rather than a mock.
"""
from __future__ import annotations

import ast
import json
import math
import os
import re

import pytest

from embodied_agent.core.contracts import Source
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.verify import build_world_state
from embodied_agent.evaluation import vlm_contrast as vc
from embodied_agent.evaluation.tasks import find_case

ID_RE = re.compile(r"^[VXC]\d?\d?\.[a-z_0-9]+$")
DEFINITION_KEYS = ("question", "unit", "numerator", "denominator", "truth", "forbidden")
#: every id the batteries can accumulate. `X1.contrast` is a definition of a *pairing*, so it
# carries no fraction and is excluded from the arithmetic checks below.
COUNTED = {k for k in vc.METRIC_DEFINITIONS if not k.startswith("X1")}
BARE_RATIO = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
POINTER = re.compile(r"^as (above|V\d|the)", re.I)


# ------------------------------------------------------------------------- fixture ----


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    """The whole measurement path over two cases — the smallest set that can show pooling."""
    out = str(tmp_path_factory.mktemp("p1f_contrast"))
    return vc.measure_set("all", case_ids=["dev_c2", "dev_c5"], out_dir=out, verbose=False)


@pytest.fixture(scope="module")
def cases(result):
    return result["cases"]


@pytest.fixture(scope="module")
def metrics(result):
    return result["metrics"]


def all_rows(cases, block: str):
    return [r for c in cases for r in c[block]["rows"]]


# --------------------------------------------------------------- the 口径 as data ----


def test_every_id_counted_by_a_battery_is_defined_before_it_is_counted():
    """A count nobody defined is a number that will be misread, so the accumulator refuses it.

    Collected from the module's own AST rather than from this file's opinion about which ids
    matter: `METRIC_DEFINITIONS` is a module-level assignment, so any id-shaped string inside a
    *function body* is a count the code takes."""
    tree = ast.parse(open(os.path.abspath(vc.__file__)).read())
    counted = {n.value for node in tree.body if isinstance(node, ast.FunctionDef)
               for n in ast.walk(node)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and ID_RE.match(n.value)}
    assert counted, "the scan found no metric ids: the pattern, not the module, is broken"
    undefined = sorted(counted - set(vc.METRIC_DEFINITIONS))
    assert not undefined, f"counted without a definition: {undefined}"
    orphan = sorted(COUNTED - counted)
    assert not orphan, f"defined but never counted, so the definition is a decoy: {orphan}"
    with pytest.raises(KeyError):
        vc._step({}, "V9.nobody_wrote_this_down", 1, 1)


def test_a_definition_without_its_own_arms_is_not_a_metric():
    for mid, d in vc.METRIC_DEFINITIONS.items():
        assert set(d) == set(DEFINITION_KEYS), mid
        for field in DEFINITION_KEYS:
            assert len(str(d[field]).strip()) > 10, f"{mid}.{field} is a placeholder"
            # self-containment: a definition is shipped inside every result file so that one
            # row of JSON can be audited alone, so none of its fields may be a pointer
            assert not POINTER.match(str(d[field]).strip()), \
                f"{mid}.{field} points at another definition instead of naming its own"
        for field in ("numerator", "denominator"):
            # a rule, not a result: no definition may state the fraction it is meant to yield
            assert not BARE_RATIO.match(str(d[field])), f"{mid}.{field} is a number"


def test_the_primary_and_control_split_is_written_into_both_definitions():
    """The 口径 decision P1-d left open is only resolved if a reader of the JSON can see which
    questions a rate counted, so the sentence and not just the code carries it."""
    primary = vc.METRIC_DEFINITIONS["V3.over_claim_rate"]
    control = vc.METRIC_DEFINITIONS["V3.control_over_claim"]
    assert "primary" in primary["denominator"]
    assert "control_over_claim" in primary["forbidden"]
    assert "never summed" in control["forbidden"]
    assert control["numerator"] == primary["numerator"], "the same class, a different set"


# ------------------------------------------------------------------ rate arithmetic ----


def test_every_rate_row_carries_its_own_numerator_and_denominator(cases, metrics):
    rows = list(metrics.values()) + [r for c in cases for r in c["metrics"].values()]
    assert rows
    for r in rows:
        d = vc.METRIC_DEFINITIONS[r["definition_id"]]
        assert r["numerator"] >= 0 and r["denominator"] >= 0, r
        assert r["value"] == (round(r["numerator"] / r["denominator"], 4)
                              if r["denominator"] else None), r
        assert r["question"] == d["question"] and r["unit"] == d["unit"], r
        assert r["as_computed"] == f"{d['numerator']} / {d['denominator']}", r
        assert r["denominator_too_small_for_a_rate"] == (0 < r["denominator"] < 10), r
        assert r["interval_kind"] == ("ratio_of_counts"
                                      if r["definition_id"] in vc.RATIO_METRICS
                                      else "proportion"), r
        if r["denominator"] and r["interval_kind"] == "proportion":
            assert r["numerator"] <= r["denominator"], r
            lo, hi = r["wilson_95"]
            # the shared Wilson statistic (v0.1 §11.4, `report.wilson_interval`) lands a
            # hair above 0 for a 0/n rate, which is float noise, not a bracket failure
            assert lo - 1e-9 <= r["value"] <= hi + 1e-9, r
            assert 0.0 <= lo and hi <= 1.0, r
        else:
            assert r["wilson_95"] is None, r


def test_pooling_sums_integers_rather_than_averaging_fractions(result, cases):
    """A three-body case and a five-body case are not two observations of equal weight. A
    mean-of-rates implementation passes on one case and fails on two."""
    assert len(cases) > 1, "pooling cannot be pinned on a single case"
    for mid, pooled in result["metrics"].items():
        n = sum(c["metrics"][mid]["numerator"] for c in cases)
        d = sum(c["metrics"][mid]["denominator"] for c in cases)
        assert (pooled["numerator"], pooled["denominator"]) == (n, d), mid
        assert pooled["pooled_over_cases"] == len(cases), mid


def test_the_shape_table_splits_by_view_without_moving_a_number(result, cases):
    """P1-c residual 6 asked for the VLM-side shape table; the pooled one was already there and
    already hid the answer, because `cylinder 0/55` is a fact about one camera and not about the
    channel. A per-view breakdown is only evidence if it is the *same* rows re-added: every cell
    here must sum back to the shipped pooled counts, or the split is a second opinion."""
    pooled, by_view = result["shape_vocabulary"], result["shape_vocabulary_by_view"]
    assert set(by_view) == set(vc.VIEWS)
    for shape, v in pooled.items():
        cells = [by_view[view][shape] for view in by_view if shape in by_view[view]]
        assert len(cells) == len(vc.VIEWS), f"{shape}: a view with no rows is a missing view"
        assert sum(c["declared"] for c in cells) == v["declared"], shape
        assert sum(c["named_correctly"] for c in cells) == v["named_correctly"], shape
        merged: dict[str, int] = {}
        for c in cells:
            for named, n in c["named_as"].items():
                merged[named] = merged.get(named, 0) + n
        assert merged == v["named_as"], shape
    assert sum(v["declared"] for v in pooled.values()) == \
        sum(1 for c in cases for r in c["claims"]["rows"] if r["shape_truth"])
    assert "P1-c residual 6" in vc.render_markdown(result), "the split must reach the .md too"


def test_the_rendered_table_shows_the_arithmetic_beside_the_number(result):
    text = vc.render_markdown(result)
    assert vc.DEFINITION_VERSION in text
    for mid, r in result["metrics"].items():
        assert f"`{mid}`" in text, mid
        assert f"{r['numerator']}/{r['denominator']}" in text, mid
    assert "control over-claims" in text, "the control set must be visible, not implied"


def test_no_metric_is_reported_without_its_coverage(result):
    coverage = result["coverage"]
    assert coverage["cases_measured"] == len(result["cases"])
    assert coverage["cases_planned"] == len(coverage["case_ids"])
    assert set(coverage["views"]) == set(vc.VIEWS)
    assert coverage["model_money_spent"] == 0, "this run consulted no vision model"


# ------------------------------------------------------- the two change accounts ----


def test_the_ledger_and_the_audits_stay_two_accounts(cases, metrics):
    blocks = [c["change"]["tracker"]["ledger_vs_audits"] for c in cases]
    assert all(b["merged"] is False for b in blocks)
    assert all("ledger" in b["why_not_merged"] for b in blocks)
    assert {"V2.change_detection", "V2.audit_differences",
            "V2.cross_view_recovery"} <= set(metrics)
    # the anti-laundering check proper: no emitted denominator is the two accounts added up
    joined = (sum(b["ledger_rows"] for b in blocks)
              + sum(b["audit_difference_entries"] for b in blocks))
    assert joined not in {r["denominator"] for r in metrics.values()}, \
        "the ledger and the audits were summed into one number"


def test_a_placement_made_while_the_camera_looked_away_is_not_a_change_detection(cases):
    """P1-c's finding, kept as a structural fact: the audit recovers it, the ledger stays
    empty, and §11's detection number does not grow."""
    for case in cases:
        tracker = case["change"]["tracker"]
        blind = tracker["blind_spot"]
        assert blind["cross_view_disagreements"], case["case_id"]
        assert blind["ledger_rows_for_that_look"] == [], case["case_id"]
        assert blind["where_the_key_says"] == vc.PLACEMENT_TARGET
        # the trials feeding the rate are the commanded displacements plus the still-world
        # control, never the tracker cycle that produced the row above
        assert (case["metrics"]["V2.change_detection"]["denominator"]
                + case["metrics"]["V2.change_false_positive"]["denominator"]
                <= len(vc.DISPLACEMENTS_MM) + 1), case["case_id"]


def test_a_cross_view_leg_never_reports_a_change(cases):
    for case in cases:
        for leg in case["change"]["tracker"]["legs"]:
            if leg["kind"] == "cross_view":
                assert leg["ledger_rows"] == 0, (case["case_id"], leg)


# ------------------------------------------------- what a verdict class may mean ----


def test_every_row_is_in_exactly_one_class_and_the_class_follows_the_table(cases):
    rows = all_rows(cases, "verification")
    assert rows
    for r in rows:
        present, sensor, key = r["present"], r["sensor_value"], r["key_value"]
        expected = ("no_answer" if not present else
                    "confirmed" if (sensor == "true" and key == "true") else
                    "dangerous_true" if sensor == "true" else
                    "contrary_false" if (sensor == "false" and key == "true") else
                    "correct_negative" if sensor == "false" else
                    "honest_decline" if key == "true" else "declined_negative")
        assert r["class"] == expected, r
        assert r["class"] in vc.VERDICT_CLASSES, r
        assert r["seating_height_evidence_keys"] == [], r


def test_primary_and_control_questions_partition_the_rows(cases, metrics):
    families = 1 + len(vc.CONFIGURATIONS)
    for case in cases:
        v = case["verification"]
        primary = [r for r in v["rows"] if r["role"] == "primary"]
        control = [r for r in v["rows"] if r["role"] == "control"]
        assert len(primary) + len(control) == len(v["rows"])
        assert v["questions"] == len(primary)
        assert v["questions"] == case["bodies"] * len(vc.VIEWS) * families
        assert v["control_questions"] == len(control)
        for family in ("alone", "wall"):
            rounds = sorted({r["config"] for r in v["rows"]
                             if r["config"].startswith(family + ":")})
            # a per-body family is one round per body, and each round is asked of every body
            # in the frame: the body it seated as the primary question, the rest as control
            assert len(rounds) == case["bodies"], case["case_id"]
            for one in rounds:
                mine = [r for r in v["rows"] if r["config"] == one]
                assert len(mine) == case["bodies"] * len(vc.VIEWS), one
                assert sum(1 for r in mine if r["role"] == "primary") == len(vc.VIEWS), one
                assert sum(1 for r in mine if r["role"] == "control") == \
                    (case["bodies"] - 1) * len(vc.VIEWS), one
        for whole in ("untouched", "crowded"):
            assert all(r["role"] == "primary" for r in v["rows"] if r["config"] == whole)
        assert v["control_questions"] == (2 * case["bodies"] * (case["bodies"] - 1)
                                          * len(vc.VIEWS))
    over = metrics["V3.over_claim_rate"]["denominator"]
    assert over == sum(c["verification"]["questions"] for c in cases)
    assert metrics["V3.control_over_claim"]["denominator"] <= \
        sum(c["verification"]["control_questions"] for c in cases)
    # neither rate's denominator is the union of the two sets
    assert over != metrics["V3.control_over_claim"]["denominator"] + over


# --------------------------------------------------- the refusals this channel owes ----


def test_the_seating_height_comparison_produces_no_number_anywhere(cases):
    """The keys below exist only in the privileged verifier's evidence. If `core/verify.py`
    renamed one, this file's guard list would go stale and the check would pass on nothing — so
    the names are re-checked against the source that produces them before their absence is
    allowed to mean anything."""
    verify_src = open(os.path.join(os.path.dirname(os.path.dirname(vc.__file__)),
                                   "core", "verify.py")).read()
    for key in vc.SEATING_HEIGHT_EVIDENCE_KEYS:
        assert f'"{key}"' in verify_src, f"{key} is no longer a privileged evidence key"
    for case in cases:
        assert case["verification"]["seating_height_compared_anywhere"] == [], case["case_id"]
        for r in case["verification"]["rows"]:
            if r["sensor_value"] == "unknown":
                assert set(vc.DECLINED_FACTS) <= set(r["unmeasured"]), r


def test_a_privileged_snapshot_is_refused_as_sensing_evidence():
    case = find_case("dev_c2")
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    try:
        privileged = build_world_state(scene, 1, "obs_key")
        assert privileged.source is not Source.sensor
        with pytest.raises(AssertionError):
            vc._guard_sensor(privileged, "test")
    finally:
        scene.close()


def test_a_real_sensor_snapshot_passes_the_same_guard(tmp_path):
    """The guard is only worth running on every row if a row can pass it."""
    case = find_case("dev_c2")
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    try:
        catalog = vc.cell_catalog(scene.trays.values())
        assembler = vc.PerceptionAssembler(catalog)
        obs = vc._look(scene, catalog, vc.StubReader(), assembler, str(tmp_path),
                       "guard_probe", vc.VIEWS[0])
        state = vc.world_state_from_percept(obs, catalog, state_version=1)
        vc._guard_sensor(state, "test")
        assert state.source is Source.sensor
        assert all("obj_" not in str(e.entity_id) for e in state.entities)
    finally:
        scene.close()


def test_only_the_fields_with_no_pixel_channel_are_left_silent(cases):
    for case in cases:
        claims = case["claims"]
        seen = {f for r in claims["rows"] for f in r["channel_silent_fields"]}
        assert seen == set(vc.CHANNEL_SILENT_FIELDS), case["case_id"]
        assert claims["bound_not_invariant"] == [], \
            "a percept footprint bound must be valid at any heading"
        assert claims["widening_mm_max"] is None or claims["widening_mm_max"] >= 0.0, \
            "the declared bound may over-claim extent, never under-claim it"


# ---------------------------------------------------------------------------- cost ----


def _episode(case_id: str, rounds: int, *, looks: int = 0, http: int = 0,
             tokens: dict | None = None, assignments: str = "same",
             loop_objects: int | None = None) -> dict:
    """A row in the shape `load_rows` actually returns.

    `episodes.csv` is all strings, and the reader converts the numeric columns to ints and floats
    and the two verdict columns to **Python bools** — which is the part this fixture used to get
    wrong by writing `1`/`0`. Those are numbers, so `_number` averaged them and the tests passed
    while the real contrast put both headline columns in its `not_a_quantity_on_every_pair` list.

    The flat `objects_completed` column and the `score` block are one instrument (layer 3:
    `_csv_row` reads the column *from* the score), so the fixture derives them together instead of
    letting them drift. `loop_objects` is the other instrument — the loop's own counter in
    `result` — and is what makes an over-claim row."""
    key_objects = 2
    return {"case_id": case_id, "mode": "B", "repeat": 1,
            "decision_rounds": rounds, "http_requests": 3, "prompt_tokens": 400,
            "completion_tokens": 90, "wall_time_s": 4.0, "sim_time_s": 12.0,
            "outcome": "success", "failure_type": None,
            "independent_complete_success": True, "objects_completed": key_objects,
            "objects_total": 2, "agent_claims_success": True, "skill_calls": 2,
            "false_finish_attempts": 0, "identical_invalid_attempts": 0,
            "rejected_decisions": 0, "model_errors": 0,
            "_summary": {"goal_resolution": {"assignments": assignments},
                         "result": {"objects_completed": key_objects if loop_objects is None
                                    else loop_objects, "objects_total": 2},
                         "score": {"objects_completed": key_objects, "objects_total": 2},
                         "perception": {"channel": "stub", "looks": looks,
                                        "look_http_requests": http,
                                        "look_tokens": tokens or {}}}}


def test_a_cost_zero_says_which_kind_of_zero_it_is():
    """`0` on the stub arm means *no vision model was consulted*; no looks at all means *this
    arm has no camera*. Both land as zeros in a table, so the ids and their definitions are the
    only thing keeping the two sentences apart.

    The sensing row is the P1-e `costed_stub` measurement — 40 looks over 9 rounds — because
    that is the shape that broke: `looks/round` is a ratio of counts, and feeding it to Wilson
    as if it were a proportion made `p*(1-p)` negative and raised `math domain error` out of the
    production `vlm-contrast --contrast` path."""
    block = vc.cost_block({
        "privileged": [_episode("dev_c1", 4)],
        "sensing": [_episode("dev_c1", 9, looks=40), _episode("dev_c2", 2, looks=3)]})
    assert block["privileged"]["looks_total"] == 0
    assert block["sensing"]["looks_total"] == 43
    assert block["sensing"]["looks_per_round"]["numerator"] == 43
    assert block["sensing"]["looks_per_round"]["denominator"] == 11
    assert block["sensing"]["looks_per_round"]["numerator"] > \
        block["sensing"]["looks_per_round"]["denominator"], "the shape that crashed"
    assert block["privileged"]["looks_per_round"]["numerator"] == 0
    assert block["sensing"]["look_http_requests_total"] == 0
    assert block["sensing"]["look_tokens_total"] == 0
    assert block["sensing"]["model_calls_per_episode"]["value"] == 0.0
    for arm, b in block.items():
        assert isinstance(b["skill_generation_cost"], str), arm
        assert b["skill_generation_cost"].startswith("not_measured"), arm
        for key in ("looks_per_round", "model_calls_per_episode", "tokens_per_episode"):
            row = b[key]
            assert row["interval_kind"] == "ratio_of_counts", (arm, key)
            assert row["wilson_95"] is None, (arm, key)
            assert row["value"] == (round(row["numerator"] / row["denominator"], 4)
                                    if row["denominator"] else None), (arm, key)
    forbidden = vc.METRIC_DEFINITIONS["C1.model_calls"]["forbidden"]
    assert "stub" in forbidden and "not a cheap channel" in forbidden


def test_a_ratio_never_borrows_a_proportion_interval():
    """Which ids are ratios is a declared fact, and the two kinds of row are mutually exclusive:
    a ratio publishes no interval, a proportion that exceeds its denominator is a broken count
    and refuses to render at all."""
    assert vc.RATIO_METRICS <= set(vc.METRIC_DEFINITIONS), "a ratio nobody defined"
    for mid in vc.RATIO_METRICS:
        row = vc._rate(mid, 40, 9)
        assert row["wilson_95"] is None and row["interval_kind"] == "ratio_of_counts", mid
        assert row["value"] == 4.4444, mid
    for mid in set(vc.METRIC_DEFINITIONS) - set(vc.RATIO_METRICS):
        row = vc._rate(mid, 9, 40)
        assert row["interval_kind"] == "proportion", mid
        assert row["wilson_95"] is not None and row["wilson_95"][1] <= 1.0, mid
        with pytest.raises(AssertionError) as e:
            vc._rate(mid, 41, 40)
        assert "counting site" in str(e.value), mid


def test_look_accounting_is_read_from_the_episode_summary_not_the_flat_csv():
    """`episodes.csv` carries no `perception` column; reading one off a csv row would answer 0
    looks, which is the difference between a free channel and a broken instrument."""
    row = _episode("dev_c1", 3, looks=7)
    assert vc._summary_of(row)["perception"]["looks"] == 7
    assert vc._summary_of({"case_id": "dev_c1"}) == {}
    assert "perception" not in {k: v for k, v in row.items() if k != "_summary"}


# ------------------------------------------------------------------- the contrast ----


def test_pairing_keeps_every_row_it_excludes_and_excludes_what_it_cannot_pair():
    by_arm = {
        "privileged": [_episode("dev_c1", 4), _episode("dev_c2", 5), _episode("dev_c9", 6)],
        "sensing": [_episode("dev_c1", 7),
                    _episode("dev_c2", 5, assignments="different")]}
    by_arm["sensing"][0]["sim_time_s"] = ""          # a cell this run did not write
    out = vc.pair_rows(by_arm)
    assert out["paired_cases"] == 2
    assert out["unpaired"]["privileged_only"] == ["dev_c9|B|1"]
    assert out["unpaired"]["sensing_only"] == []
    assert out["not_comparable"] == ["dev_c2|B|1"]
    assert len(out["pairs"]) == 2, "a non-comparable pair is reported, never deleted"
    rounds = out["paired_diff_sensing_minus_privileged"]["decision_rounds"]
    assert rounds["n"] == 1, "only the comparable pair may enter a difference"
    assert rounds["mean"] == rounds["max"] == 3.0
    assert rounds["excluded_pairs"] == [], "a pair that is not comparable is not a hole in a " \
                                           "column; `not_comparable` already says that"
    assert "sim_time_s" not in out["paired_diff_sensing_minus_privileged"]
    assert out["not_a_quantity_on_every_pair"] == {"sim_time_s": ["dev_c1|B|1"]}, \
        "the one exclusion this column has is named, not silently dropped from n"
    assert [p["goal_identical"] for p in out["pairs"]] == [True, False]
    assert set(out["categories"]) == set(vc.CONTRAST_CATEGORIES)
    assert "failure_type" not in out["paired_diff_sensing_minus_privileged"], \
        "a kind is cross-tabulated, never averaged"


def test_an_absent_value_is_never_coerced_into_a_measured_zero():
    """The dev contrast's own first run is the regression: two sensing episodes ended `failed`
    with no `failure_type`, `float("" or 0)` called that 0.0, and the table printed
    `failure_type mean Δ 0.0, n=2` over the two rows that were the actual finding."""
    assert vc._number("") is None and vc._number(None) is None
    assert vc._number("none") is None and vc._number("8") == 8.0
    assert vc._number("BUDGET_EXHAUSTED") is None and vc._number(4) == 4.0
    assert vc._category("") == vc.ABSENT_CATEGORY == vc._category(None)
    assert vc._category("REPEATED_INVALID") == "REPEATED_INVALID"

    priv, sens = _episode("dev_c1", 7), _episode("dev_c1", 5)
    priv["outcome"] = sens["outcome"] = ""
    out = vc.pair_rows({"privileged": [priv], "sensing": [sens]})
    kinds = out["categories"]["failure_type"]
    assert kinds["privileged"] == {vc.ABSENT_CATEGORY: 1}
    assert kinds["sensing"] == {vc.ABSENT_CATEGORY: 1}
    assert kinds["transitions"] == {f"{vc.ABSENT_CATEGORY} -> {vc.ABSENT_CATEGORY}": 1}
    # a column that is not a quantity on any pair produces no row at all, with the reason kept
    assert "outcome" not in out["paired_diff_sensing_minus_privileged"]
    assert out["not_a_quantity_on_every_pair"] == {}, "a kind is not an exclusion, it is a kind"


def test_a_boolean_verdict_is_a_kind_and_never_a_quiet_exclusion():
    """The p1f-5 dev artifact is the regression, and it is the subtle kind: nothing crashed and
    nothing lied. `load_rows` parses the two verdict columns into Python bools, `_number` refuses
    a bool (correctly — `True` is not a 1 that may be averaged), and so the two columns the
    contrast exists to read appeared only in `not_a_quantity_on_every_pair`, all eight pairs.
    A table of kinds with the headline in its footnotes is how a 0 %-claim result goes missing."""
    assert {"agent_claims_success", "independent_complete_success"} <= set(vc.CONTRAST_CATEGORIES)
    assert vc._number(True) is None and vc._number(False) is None
    assert vc._category(True) == "true" and vc._category("False") == "false", \
        "one verdict, two producers (`str(bool)` and a JSON dump), one bucket"
    assert vc._category("BUDGET_EXHAUSTED") == "BUDGET_EXHAUSTED", "an enum name is not renamed"

    priv, sens = _episode("dev_c1", 4), _episode("dev_c1", 9)
    sens.update(outcome="failed", agent_claims_success=False,
                independent_complete_success=False)
    by_arm = {"privileged": [priv], "sensing": [sens]}
    out = vc.pair_rows(by_arm)
    assert out["not_a_quantity_on_every_pair"] == {}, "no headline column may be excluded"
    for field in ("agent_claims_success", "independent_complete_success"):
        kinds = out["categories"][field]
        assert kinds["privileged"] == {"true": 1} and kinds["sensing"] == {"false": 1}, field
        assert kinds["transitions"] == {"true -> false": 1}, field
        assert field not in out["paired_diff_sensing_minus_privileged"], field
    table = vc.render_markdown(
        {"definition_version": vc.DEFINITION_VERSION, "set": "dev", "modes": ["B"],
         "cases": ["dev_c1"],
         "arms": {a: {"channel": a, "run_root": "/tmp/x", "episodes": 1}
                  for a in ("privileged", "sensing")},
         **out, "arm_asymmetric_fields": vc.ARM_ASYMMETRIC_FIELDS,
         "cost": vc.cost_block(by_arm)})
    for field in vc.CONTRAST_CATEGORIES:
        assert f"| `{field}` |" in table, "a kind that never reaches the table is not reported"


def test_the_contrast_is_the_mode_b_pair_over_named_fields():
    assert set(vc.CONTRAST_FIELDS) >= {"outcome", "decision_rounds", "http_requests",
                                       "prompt_tokens", "wall_time_s", "sim_time_s",
                                       "independent_complete_success",
                                       "agent_claims_success"}
    assert vc.DEFAULT_CONTRAST_MODES == ("B",), "the episode contrast is mode B, both arms"
    forbidden = vc.METRIC_DEFINITIONS["X1.contrast"]["forbidden"]
    assert "unpaired" in forbidden and "not_comparable" in forbidden


def test_the_fields_whose_units_change_with_the_channel_are_named():
    """P1-e residual 3: `identical_repeats_total` is not comparable across arms until the guard's
    keying is written down, and `objects_completed` is the loop's own instrument. The claim is
    only real if the reason is *in* the artifact — a list of field names in a docstring is a
    comment, and a reader of the JSON never sees it."""
    assert set(vc.ARM_ASYMMETRIC_FIELDS) <= set(vc.CONTRAST_FIELDS), "a field nobody reports"
    for f, why in vc.ARM_ASYMMETRIC_FIELDS.items():
        assert len(why) > 60, f
        assert "P1-e" in why, f"{f}: the asymmetry must name the measurement that found it"
    assert "independent_complete_success" not in vc.ARM_ASYMMETRIC_FIELDS, \
        "layer 3 grades from the simulator on both arms; that one is the comparable field"

    result = {"definition_version": vc.DEFINITION_VERSION, "set": "dev", "modes": ["B"],
              "cases": ["dev_c1"],
              "arms": {a: {"channel": a, "run_root": "/tmp/x", "episodes": 1}
                       for a in ("privileged", "sensing")},
              **vc.pair_rows({"privileged": [_episode("dev_c1", 4)],
                              "sensing": [_episode("dev_c1", 9, looks=40)]}),
              "arm_asymmetric_fields": vc.ARM_ASYMMETRIC_FIELDS,
              "budget_censored_fields": vc.BUDGET_CENSORED_FIELDS,
              "cost": vc.cost_block({"privileged": [_episode("dev_c1", 4)],
                                     "sensing": [_episode("dev_c1", 9, looks=40)]})}
    table = vc.render_markdown(result)
    marked, censored = set(), set()
    for line in table.splitlines():
        if line.startswith("| "):
            cell = line.split("|")[1].strip()
            if cell.endswith("†"):
                marked.add(cell[:-1].strip())
            elif cell.endswith("‡"):
                censored.add(cell[:-1].strip())
    assert marked == set(vc.ARM_ASYMMETRIC_FIELDS), "a difference printed without its dagger"
    assert censored == set(vc.BUDGET_CENSORED_FIELDS), "a censored mean printed without its mark"
    assert marked & censored == set(), "an instrument swap and a truncated column are not the " \
                                       "same caveat, so no field may carry both"
    assert "§6.2" in table, "the reason has to travel with the table"


def test_the_two_progress_instruments_are_printed_side_by_side_and_never_differenced():
    """`objects_completed` is layer 3's count (`_csv_row` reads it out of `score`), so it *is*
    comparable across arms and carries no mark. The loop's own counter is `result.objects_completed`
    — a different instrument, which on a sensing arm can disagree with the key. Differencing one
    against the other would be the very swap `†` exists to catch, so each arm gets its own
    loop-vs-key line instead."""
    assert "objects_completed" not in vc.ARM_ASYMMETRIC_FIELDS, \
        "the column is the key's count on both arms; calling it the loop's was the p1f-5 error"
    assert set(vc.BUDGET_CENSORED_FIELDS) <= set(vc.CONTRAST_FIELDS)

    priv, sens = _episode("dev_c1", 4), _episode("dev_c1", 24, loop_objects=3)
    out = vc.pair_rows({"privileged": [priv], "sensing": [sens]})
    agree, claim = out["progress_self_report_vs_key"]["privileged"], \
        out["progress_self_report_vs_key"]["sensing"]
    assert agree["episodes"] == claim["episodes"] == 1
    assert agree["over_claim_pairs"] == [], \
        "an agreeing pair is a zero on the record, not a line that goes missing"
    assert claim["over_claim_pairs"] == ["dev_c1|B|1"] and claim["under_report_pairs"] == []
    row = out["pairs"][0]["progress"]["sensing"]
    assert row == {"loop_self_report": 3, "independent_key": 2, "objects_total": 2,
                   "over_claim": True, "under_report": False}
    assert out["paired_diff_sensing_minus_privileged"]["objects_completed"]["mean"] == 0.0, \
        "the key's count still enters the difference table; only the loop's is kept out of it"

    table = vc.render_markdown(
        {"definition_version": vc.DEFINITION_VERSION, "set": "dev", "modes": ["B"],
         "cases": ["dev_c1"],
         "arms": {a: {"channel": a, "run_root": "/tmp/x", "episodes": 1}
                  for a in ("privileged", "sensing")},
         **out, "arm_asymmetric_fields": vc.ARM_ASYMMETRIC_FIELDS,
         "budget_censored_fields": vc.BUDGET_CENSORED_FIELDS,
         "cost": vc.cost_block({"privileged": [priv], "sensing": [sens]})})
    assert "dev_c1|B|1" in table and "more completed objects than the key" in table, \
        "an over-claim that reaches the JSON but not the readable table is not reported"


def test_the_definitions_a_run_ships_with_are_the_ones_it_rendered_from(result):
    assert result["definition_version"] == vc.DEFINITION_VERSION
    assert set(result["definitions"]) == {r["definition_id"] for r in result["metrics"].values()}
    for mid, d in result["definitions"].items():
        assert d == vc.METRIC_DEFINITIONS[mid], mid


def test_a_missing_measurement_is_absent_rather_than_zero(result, cases):
    """A 0/0 rate has no value; a measured zero does. Collapsing the two is how an unrun
    battery reads as a clean result."""
    for mid, r in result["metrics"].items():
        if r["denominator"] == 0:
            assert r["value"] is None and r["wilson_95"] is None, mid
    for case in cases:
        for mid, r in case["metrics"].items():
            assert mid in COUNTED, f"{mid} escaped the definition scan"
            if r["denominator"]:
                assert isinstance(r["value"], float), mid
                if r["numerator"] == 0:
                    assert math.isclose(r["value"], 0.0), mid


# ------------------------------------------------------------------- the production entry ----


def test_the_口径_is_printable_without_computing_anything(capsys, tmp_path):
    """`--definitions` before any number is the order this file was written in, so it must be
    an exit code and not a side effect: no frames, no json, nothing under `--out`."""
    from embodied_agent import cli

    assert cli.main(["vlm-contrast", "--definitions", "--out", str(tmp_path)]) == cli.EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    assert printed["version"] == vc.DEFINITION_VERSION
    assert set(printed["definitions"]) == set(vc.METRIC_DEFINITIONS)
    for mid, d in printed["definitions"].items():
        assert set(d) == set(DEFINITION_KEYS), mid
    assert os.listdir(tmp_path) == []


def test_a_measurement_that_cannot_be_taken_refuses_before_a_scene_opens(capsys, tmp_path):
    """Both entry paths — the batteries and the episode pair — are checked against the frozen
    case list *before* anything is rendered or run, so a typo cannot leave a half-batch behind.
    An id from another set is the interesting case: it exists, and it is still not in scope."""
    from embodied_agent import cli

    for extra in (["--set", "dev", "--cases", "nope"],
                  ["--contrast", "--contrast-set", "dev", "--cases", "smoke_clean"]):
        assert cli.main(["vlm-contrast", *extra, "--out", str(tmp_path / "x")]) \
            == cli.EXIT_CONFIG_ERROR, extra
        assert not os.path.exists(str(tmp_path / "x")), extra
    err = capsys.readouterr().err
    assert "no cases of set" in err and "no case of set" in err


def test_the_cli_writes_the_artifact_pair_the_module_describes(tmp_path):
    """One case, both files, through `cli.main` — the JSON is the thing a reader compares across
    versions, so its version field must be the one the module is running."""
    from embodied_agent import cli

    out = str(tmp_path / "contrast")
    assert cli.main(["vlm-contrast", "--set", "smoke", "--cases", "smoke_clean",
                     "--out", out]) == cli.EXIT_OK
    written_here = sorted(os.listdir(out))
    assert {"contrast_smoke.json", "contrast_smoke.md"} <= set(written_here), written_here[:4]
    assert any(f.endswith(".png") for f in written_here), "the frames are the measurement"
    with open(os.path.join(out, "contrast_smoke.json"), encoding="utf-8") as f:
        written = json.load(f)
    assert written["definition_version"] == vc.DEFINITION_VERSION
    assert written["coverage"]["case_ids"] == ["smoke_clean"]
    with open(os.path.join(out, "contrast_smoke.md"), encoding="utf-8") as f:
        table = f.read()
    assert vc.DEFINITION_VERSION in table
