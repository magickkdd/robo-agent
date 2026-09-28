"""P2-e contract tests: the arithmetic of §11's Long-horizon rows, and none of their values.

`evaluation/long_horizon.py` fixes a 口径 before the four arms are measured, so what may be pinned
here is the *relationship between a number and its definition*: that every counted id is defined
before it is counted, that §11's six rows cover the id set exactly once, that a rate carries its
own numerator and denominator and refuses an interval where none is defined, that an empty
denominator reads as `not_measured` with a reason rather than as a clean zero, and that the two
counting rules LH-2 replaced stay replaced — a claim audited against its own round rather than
against the episode's last verdict, and a loss measured from two populations rather than from
verdict flips alone. What no test here pins is a *measured* value: no completion rate, no
regression count, no millimetre. Those are §11's findings, and a threshold written into a test
turns the one number that has to stay honest into a fixture (the same rule
`tests/contract/test_v02_vlm_contrast.py` follows).

Two families of fixture live here. `_rec`/`_ctx`/`_plan`/`_fb` write `events.jsonl` rows, so the
end-to-end tests exercise `read_episode`'s own parsing; `_page`/`_plan_event` hand back the shapes
`read_episode` *produces*, so the direct-function tests can audit one rule without re-running the
loop. Both were copied field-for-field from the real P2-e batches under /tmp, including the traps:
`type` rather than `event_type`, `satisfied` as the strings `'True'`/`'unknown'` from `str(bool)`,
`status` as `completed` or `SkillStatus.completed`, and the schema-4 subset that carries the arm's
own claim.
"""
from __future__ import annotations

import ast
import json
import math
import os
import re

import pytest

from embodied_agent.core.v02 import ABLATION_CONDITIONS, SCHEMA_VERSION, V02_EVENT_TYPES
from embodied_agent.evaluation import long_horizon as lh

ID_RE = re.compile(r"^L\d\.[a-z]+_[a-z_0-9]+$")
DEFINITION_KEYS = ("question", "unit", "numerator", "denominator", "truth", "forbidden")
#: §11's rows in the SPEC's own order (SPEC-v0.2 §11, "Long-horizon behavior").
SPEC_ROW_NAMES = ("subgoal completion", "dependency violations", "progress regressions",
                  "unnecessary rework", "repeated failures", "recovery/replanning effectiveness")
#: `L5.failure_codes` defines a cross-tabulation, not a fraction, so no counting site takes it —
#: the same exemption `vlm_contrast` grants its `X1.contrast` pairing definition.
CROSS_TAB = {"L5.failure_codes"}
COUNTED = set(lh.METRIC_DEFINITIONS) - CROSS_TAB
BARE_RATIO = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
POINTER = re.compile(r"^as (above|L\d|the)", re.I)


# --------------------------------------------------------------- log shapes ----


def _rec(seq: int, type_: str, payload: dict, *, v02: bool = False) -> dict:
    """One `events.jsonl` row. A v0.2 record carries `schema_version` "4"; the v0.1 record kinds
    keep "3", because `score_episode` re-checks the arm's claim against the "4" subset only, and a
    fixture that marked everything "4" would invent unattributed-type findings."""
    return {"sequence": seq, "type": type_,
            "schema_version": SCHEMA_VERSION if v02 else "3", "payload": payload}


def _item(pid: str, value, unmeasured=()) -> dict:
    return {"predicate_id": pid, "value": value, "evidence": "probe",
            "evidence_refs": ["obs_x"], "unmeasured": list(unmeasured)}


def _page(seq: int, round_index: int, *items: dict) -> dict:
    """The parsed `decision_context` sample `read_episode` puts on the timeline."""
    return {"kind": "round", "sequence": seq, "round_index": round_index,
            "state_version": round_index, "observation_ref": f"obs_{seq}",
            "verdicts": {str(i["predicate_id"]): lh._verdict(i) for i in items}}


def _ctx(seq: int, round_index: int, *items: dict) -> dict:
    return _rec(seq, "decision_context", {"round_index": round_index,
                                          "state_version": round_index,
                                          "observation_ref": f"obs_{seq}",
                                          "progress": list(items)})


def _row(key: str, pid: str, satisfied, kind: str = "achieve") -> dict:
    return {"subgoal_id": f"sg_{key}", "row_key": key, "kind": kind, "statement": f"{key} done",
            "predicate_id": pid, "satisfied": satisfied, "unmeasured": [],
            "completion_evidence": None, "depends_on": [], "attempts": 0,
            "target_entity_ids": [], "target_region_id": None, "open_questions": [],
            "history": []}


def _plan_event(seq: int, rows, *, version: int = 1, deps=(), violated=(),
                type_: str = "plan", changed=None, trigger: str = "initial") -> dict:
    """The parsed plan event — the shape every L1/L2/L6 rule actually reads."""
    return {"type": type_, "sequence": seq, "trigger": trigger, "version": version,
            "rows": list(rows), "dependencies": list(deps), "violated": list(violated),
            "changed": changed or {}}


def _plan(seq: int, rows, **kw) -> dict:
    event = _plan_event(seq, rows, **kw)
    payload = {"arm": "full", "trigger": event["trigger"],
               "view": {"version": event["version"], "rows": event["rows"],
                        "dependencies": event["dependencies"], "violated": event["violated"],
                        "changed": event["changed"], "summary": "probe view"}}
    if event["type"] == "plan_revision":
        payload.update({"from_version": event["version"] - 1, "to_version": event["version"],
                        "rationale": "probe", "replanning_enabled": True})
    return _rec(seq, event["type"], payload, v02=True)


def _feedback(seq: int, skill: str, entity: str, target: str = "", *,
              status: str = "completed", executed: bool = True, code: str | None = None,
              moved=(), state_changed=(), affected=()) -> dict:
    return dict({"status": status, "executed": executed, "skill": skill, "entity_id": entity,
                 "target_id": target, "decision_id": f"d{seq}", "failure_code": code,
                 "affected_entities": list(affected), "round_index": 1,
                 "state_diff": {"moved": [{"entity_id": m} for m in moved],
                                "state_changed": [{"entity_id": s} for s in state_changed]}},
                sequence=seq)


def _fb(seq: int, skill: str, entity: str, target: str = "", **kw) -> dict:
    return _rec(seq, "execution_feedback", {"feedback": _feedback(seq, skill, entity, target, **kw)})


def _inj(seq: int, entity: str) -> dict:
    return dict({"entity_id": entity, "kind": "displace"}, sequence=seq)


def _injection(seq: int, entity: str) -> dict:
    return _rec(seq, "environment_event", {"phase": "disturbance",
                                           "injection": _inj(seq, entity)})


def _recovery(seq: int, entity: str, choice: str = "restore") -> dict:
    return _rec(seq, "recovery_action", {"choice": choice, "args": {"object_id": entity}},
                v02=True)


def _ablation(condition: str) -> dict:
    return _rec(2, "ablation", {"condition": condition,
                                "modules_off": list(ABLATION_CONDITIONS[condition])}, v02=True)


def _episode(tmp_path, name: str, records, *, arm: str = "full", policy: str = "rule",
             condition: str = "full", task_id: str = "lh_probe", result=None,
             score=None) -> str:
    """Write one episode directory — `events.jsonl` plus `episode_summary.json` — and return its
    path. `episode_start`/`episode_end` are appended rather than passed in, because `read_episode`
    takes `case_id`/`mode` from the first and every loop counter from the second."""
    d = os.path.join(str(tmp_path), name)
    os.makedirs(d, exist_ok=True)
    body = [_rec(1, "episode_start", {"task_id": task_id, "mode": "B", "episode_id": name}),
            _ablation(condition)]
    body += [r for r in records if r["type"] != "ablation"]
    body.append(_rec(9999, "episode_end", {"episode_id": name, "result": dict(
        {"decision_rounds": 3, "terminal_status": "success", "objects_completed": 1,
         "objects_total": 1, "recovery_events": 0, "replan_events": 0, "retry_events": 0,
         "rejected_decisions": 0, "false_finish_attempts": 0}, **(result or {}))}))
    with open(os.path.join(d, "events.jsonl"), "w", encoding="utf-8") as fh:
        for r in sorted(body, key=lambda x: x["sequence"]):
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(d, "episode_summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"perception": {"arm": arm, "channel": "stub"},
                   "decision_source": {"policy": policy}, "score": score or {}}, fh)
    return d


def _run(tmp_path, name: str, episode_dirs, *, arm="full", policy="rule") -> str:
    """A batch directory in the shape `score_run` reads: `manifest.json` plus `episodes/*`."""
    root = os.path.join(str(tmp_path), name)
    os.makedirs(os.path.join(root, "episodes"), exist_ok=True)
    for d in episode_dirs:
        target = os.path.join(root, "episodes", os.path.basename(d))
        if not os.path.exists(target):
            os.rename(d, target)
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"run_id": name, "perception": {"arm": arm, "channel": "stub"},
                   "decision_source": {"policy": policy}, "modes": ["B"],
                   "cases": [{"task_id": "lh_probe"}], "code": {"dirty_diff_sha256": "x"}}, fh)
    return root


# --------------------------------------------------------------- the 口径 as data ----


def test_every_id_the_scorer_counts_is_defined_before_it_is_counted():
    """Collected from the module's own AST rather than from this file's opinion about which ids
    matter: `METRIC_DEFINITIONS` is a module-level assignment, so an id-shaped string inside a
    *function body* is a count the code takes. The pattern requires an underscore after the row
    prefix, because `arm_contrast` tests `metric.startswith(("L1.row", "L6.recorded"))` and those
    prefixes are not ids."""
    tree = ast.parse(open(os.path.abspath(lh.__file__)).read())
    counted = {n.value for node in tree.body if isinstance(node, ast.FunctionDef)
               for n in ast.walk(node)
               if isinstance(n, ast.Constant) and isinstance(n.value, str) and ID_RE.match(n.value)}
    assert counted, "the scan found no metric ids: the pattern, not the module, is broken"
    undefined = sorted(counted - set(lh.METRIC_DEFINITIONS))
    assert not undefined, f"counted without a definition: {undefined}"
    orphan = sorted(COUNTED - counted)
    assert not orphan, f"defined but never counted, so the definition is a decoy: {orphan}"
    with pytest.raises(KeyError):
        lh._count({}, "L9.nobody_wrote_this_down", 1, 1)


def test_the_six_spec_rows_cover_every_defined_id_exactly_once():
    """§11 lists six rows; the report's table may not quietly add a column, nor split one row into
    three easier ones."""
    assert [row for row, _ in lh.SPEC_ROWS] == list(SPEC_ROW_NAMES)
    flat = [m for _, ids in lh.SPEC_ROWS for m in ids]
    assert len(flat) == len(set(flat)), "an id in two rows is a number counted twice"
    assert set(flat) == set(lh.METRIC_DEFINITIONS), set(flat) ^ set(lh.METRIC_DEFINITIONS)


def test_a_definition_without_its_own_arms_is_not_a_metric():
    for mid, d in lh.METRIC_DEFINITIONS.items():
        assert set(d) == set(DEFINITION_KEYS), mid
        for field in DEFINITION_KEYS:
            assert len(str(d[field]).strip()) > 10, f"{mid}.{field} is a placeholder"
            # each definition ships inside every artifact so that one row of JSON can be audited
            # alone, so none of its fields may be a pointer at a neighbour
            assert not POINTER.match(str(d[field]).strip()), \
                f"{mid}.{field} points at another definition instead of naming its own"
        for field in ("numerator", "denominator"):
            assert not BARE_RATIO.match(str(d[field])), f"{mid}.{field} is a number, not a rule"


def test_a_reason_that_is_only_true_on_one_arm_is_not_written_into_the_cell_of_another():
    """`_NOT_MEASURED` is mechanical: what an empty denominator says about the *counting*, never
    what it says about a comparison. The arm-specific reading belongs to `arm_contrast`, and the
    first version of this table put "the switch on `wo_planning`" into every cell's reason, which
    was a false statement in the `full` cell of the same table."""
    assert set(lh._NOT_MEASURED) <= set(lh.METRIC_DEFINITIONS), "a reason for an undefined id"
    for mid, why in lh._NOT_MEASURED.items():
        assert len(why) > 40, mid
        assert not BARE_RATIO.match(why), mid
        assert "wo_planning" not in why or "switch" in why, mid


def test_the_two_lh_2_corrections_are_written_into_the_definitions_they_replaced():
    """A counting rule that changed is only a change if the reason travelled with it: LH-1 left no
    artifact, so what a later reader compares against is the definition text itself."""
    claim = lh.METRIC_DEFINITIONS["L1.row_claim_vs_key"]
    assert "final" in claim["forbidden"] and "3/4" in claim["forbidden"], \
        "the staleness-read-as-a-lie error must be named where its fix is defined"
    assert "same round" in claim["unit"]
    recovery = lh.METRIC_DEFINITIONS["L6.recovery_effectiveness"]
    assert "plus" in recovery["denominator"] and "never true" in recovery["denominator"], \
        "the second loss population has to be in the denominator's own sentence"
    assert "wo_replanning" in recovery["forbidden"]
    assert lh.METRIC_VERSION == "LH-2"


# ------------------------------------------------------------------ rate arithmetic ----


def test_every_rate_row_carries_its_own_arithmetic_and_no_borrowed_interval():
    for mid in lh.METRIC_DEFINITIONS:
        row = lh._rate(mid, 3, 7)
        d = lh.METRIC_DEFINITIONS[mid]
        assert (row["numerator"], row["denominator"]) == (3, 7), mid
        assert row["value"] == round(3 / 7, 4), mid
        assert row["measured"] is True and row["not_measured_reason"] is None, mid
        assert row["question"] == d["question"] and row["unit"] == d["unit"], mid
        assert row["as_computed"] == f"{d['numerator']} / {d['denominator']}", mid
        assert row["spec_row"] == next(r for r, ids in lh.SPEC_ROWS if mid in ids), mid
        assert row["denominator_too_small_for_a_rate"] is True, mid
        if mid in lh.RATIO_METRICS:
            assert row["interval_kind"] == "count_per_episode" and row["wilson_95"] is None, mid
        else:
            assert row["interval_kind"] == "proportion", mid
            lo, hi = row["wilson_95"]
            assert lo <= row["value"] <= hi and 0.0 <= lo and hi <= 1.0, mid
    assert lh.RATIO_METRICS <= set(lh.METRIC_DEFINITIONS), "a ratio nobody defined"


def test_a_proportion_that_outruns_its_denominator_is_a_broken_count_not_a_big_number():
    with pytest.raises(AssertionError) as e:
        lh._rate("L3.regression_rate", 5, 4)
    assert "counting site" in str(e.value)
    # a count per episode may legitimately exceed its episode count, and must not be clipped
    assert lh._rate("L2.violations_per_episode", 9, 2)["value"] == 4.5


def test_an_empty_denominator_is_not_measured_rather_than_zero():
    """The one rule the four-arm table depends on: `wo_planning` authors no rows, so a 0.0 in its
    subgoal column would credit the arm that cannot state a subgoal with perfect completion."""
    for mid in lh.METRIC_DEFINITIONS:
        row = lh._rate(mid, 0, 0)
        assert row["measured"] is False and row["value"] is None, mid
        assert row["wilson_95"] is None, mid
        assert row["not_measured_reason"] == lh._NOT_MEASURED.get(
            mid, "no unit fell in this cell's denominator"), mid
        assert len(row["not_measured_reason"]) > 10, mid
    # a measured zero is a different sentence, and has to read as one
    zero = lh._rate("L3.unrecovered_regression", 0, 6)
    assert zero["measured"] is True and math.isclose(zero["value"], 0.0)
    assert zero["not_measured_reason"] is None


def test_pooling_sums_integers_rather_than_averaging_fractions():
    """Four episodes of a 4-object task and one of an 8-object task are not five equal
    measurements of completion. A mean-of-rates implementation passes on one cell and fails here."""
    counts: dict[str, list[int]] = {}
    lh._merge(counts, {"L1.assignment_completion": [1, 1]})
    lh._merge(counts, {"L1.assignment_completion": [0, 20]})
    row = lh._render(counts)["L1.assignment_completion"]
    assert (row["numerator"], row["denominator"]) == (1, 21)
    assert row["value"] == round(1 / 21, 4)
    assert row["value"] != round((1.0 + 0.0) / 2, 4), "the arithmetic that would flatter a cell"


def test_counting_an_episode_twice_shows_in_the_denominator_and_not_in_a_label():
    counts: dict[str, list[int]] = {}
    for _ in range(3):
        lh._count(counts, "L2.violations_per_episode", 2, 1)
    assert counts["L2.violations_per_episode"] == [6, 3]
    assert lh._render(counts)["L2.violations_per_episode"]["value"] == 2.0


# ----------------------------------------------------- one verdict, three producers ----


def test_a_verdict_has_exactly_three_values_and_an_unmeasured_one_is_not_a_failure():
    assert lh._verdict({"value": "true"}) == "true"
    assert lh._verdict({"value": "False"}) == "false", "a bool dumped to a string is one verdict"
    assert lh._verdict({"value": True}) == "true", "the `str(bool)` producer"
    assert lh._verdict({"value": "Verdict.true"}) == "true", "the enum producer"
    assert lh._verdict({"value": "", "unmeasured": ["no_channel"]}) == "unknown"
    assert lh._verdict({"value": "true", "unmeasured": ["no_channel"]}) == "unknown", \
        "a channel that cannot answer is not the agent finishing"
    assert lh._verdict({"value": "maybe"}) == "unknown"


def test_a_completed_status_matches_whatever_the_enum_was_stringified_as():
    assert lh._completed({"status": "completed"})
    assert lh._completed({"status": "SkillStatus.completed"}), "the trap P2 measured once already"
    assert not lh._completed({"status": "failed"})
    assert not lh._completed({}), "an absent status is not a completion"


def test_a_rows_own_claim_survives_both_serialisers():
    """`satisfied` reaches the log as the strings `'True'`/`'False'`/`'unknown'` from `str(bool)`,
    and a plain `or`-default would turn every unmet row into a non-claim, because `False or ""` is
    `""` — which is how a whole metric silently empties its own denominator."""
    for value, expected in ((True, "true"), (False, "false"), ("True", "true"),
                            ("False", "false"), ("unknown", "unknown"), (None, "unknown"),
                            ("", "unknown"), ("Status.false", "false")):
        assert lh._satisfied({"satisfied": value}) == expected, value


def test_transitions_pair_definite_readings_and_skip_the_holes():
    assert lh._transitions([(0, "true"), (1, "unknown"), (2, "false")]) == [(0, 2)], \
        "a sensor that could not answer between two readings did not observe a flip"
    assert lh._transitions([(0, "false"), (1, "true")]) == [], "a gain is not a regression"
    assert lh._transitions([(0, "true"), (1, "false"), (2, "true"), (3, "false")]) == [(0, 1), (2, 3)]
    assert lh._transitions([(0, "unknown")]) == []
    assert lh._split("placed:obj_a:region_1") == ("obj_a", "region_1")
    assert lh._split("clear:region_1") == ("", ""), "only `placed:` has a verdict timeline"


# ------------------------------------------------------- the same-round pairing (L1) ----


def test_the_page_a_claim_is_audited_against_is_the_first_context_at_or_after_it():
    """Measured on the real batches: a `plan_revision` at seq 45 is followed by the round-5
    `decision_context` at seq 47, so the pairing is an instant and not a lag."""
    pages = [_page(7, 1, _item("placed:a:r", "true")),
             _page(47, 5, _item("placed:a:r", "false"))]
    assert lh._page_after(pages, 45)["round_index"] == 5
    assert lh._page_after(pages, 5)["round_index"] == 1
    assert lh._page_after(pages, 48) is None, "a view with no round after it is not a lie"


def test_a_claim_correct_at_its_own_instant_is_not_flagged_because_the_world_changed_after():
    """The LH-1 regression, kept as a structural fact: the plan and the verifier read one world
    state in one round, so a later disturbance is L3's business, not a false self-report."""
    events = [_plan_event(5, [_row("k1", "placed:obj_a:region_1", "True")])]
    pages = [_page(7, 1, _item("placed:obj_a:region_1", "true")),
             _page(9, 2, _item("placed:obj_a:region_1", "false"))]
    claims = lh._row_claims(events, pages)
    assert len(claims) == 1 and claims[0]["disagree"] is False, claims
    assert claims[0]["round_index"] == 1 and claims[0]["claim"] == "true"
    # and the disagreement this metric does exist to catch: one round, two contradictory records
    against = lh._row_claims([_plan_event(5, [_row("k1", "placed:obj_a:region_1", "True")])],
                             [_page(7, 1, _item("placed:obj_a:region_1", "false"))])
    assert against[0]["disagree"] is True and against[0]["verdict"] == "false"


def test_an_unknown_claim_contributes_no_unit_rather_than_cleaning_the_cell():
    assert lh._row_claims([_plan_event(5, [_row("k1", "placed:obj_a:region_1", "unknown")])],
                          [_page(7, 1, _item("placed:obj_a:region_1", "true"))]) == []
    assert lh._row_claims([_plan_event(5, [_row("k1", "placed:obj_z:region_9", "False")])],
                          [_page(7, 1, _item("placed:obj_a:region_1", "true"))]) == [], \
        "a predicate the round's page does not list cannot be disagreed with"


# ------------------------------------------------------------ loss populations (L6) ----


def _handoff_pages():
    """Three rounds: obj_a true then false, obj_c false throughout."""
    return [_page(7, 1, _item("placed:obj_a:region_1", "true"),
                  _item("placed:obj_c:region_2", "false")),
            _page(21, 2, _item("placed:obj_c:region_2", "false")),
            _page(31, 3, _item("placed:obj_a:region_1", "false"))]


def test_a_handoff_lost_before_its_relation_was_ever_true_is_a_loss_event():
    """The mirror of LH-1's error: a denominator of verdict flips alone sat at 0/0 on
    `full/payload` while the arm re-ran a handoff it had just been told completed."""
    pages = _handoff_pages()
    feedbacks = [_feedback(20, "place", "obj_c", "region_2"),
                 _feedback(30, "place", "obj_a", "region_1")]
    losses = lh._loss_events({"feedbacks": feedbacks, "injections": []}, pages,
                             lh._series(pages), [])
    kinds = {loss["predicate_id"]: loss["kind"] for loss in losses}
    assert kinds == {"placed:obj_c:region_2": "unverified_handoff"}, \
        "obj_c's pair was never true, so the flip rule alone saw nothing"
    assert "placed:obj_a:region_1" not in kinds, "obj_a was true before this place moved it: a " \
                                                "relocation, which L3 owns"


def test_a_regression_enters_the_same_denominator_and_the_list_is_in_log_order():
    pages = _handoff_pages()
    feedbacks = [_feedback(20, "place", "obj_c", "region_2"),
                 _feedback(30, "place", "obj_a", "region_1")]
    transition = {"predicate_id": "placed:obj_a:region_1", "entity_id": "obj_a",
                  "target_id": "region_1", "to_index": 2, "environment": False,
                  "ambiguous": False}
    losses = lh._loss_events({"feedbacks": feedbacks, "injections": []}, pages,
                             lh._series(pages), [transition])
    assert [loss["kind"] for loss in losses] == ["unverified_handoff", "regression"], \
        "sorted by the sequence the log carries, not by which rule produced the row"
    assert losses[1]["at_sequence"] == pages[2]["sequence"]


def test_an_injection_before_a_silent_handoff_is_recorded_as_the_environments_own_doing():
    pages = _handoff_pages()
    feedbacks = [_feedback(20, "place", "obj_c", "region_2")]
    losses = lh._loss_events({"feedbacks": feedbacks, "injections": [_inj(19, "obj_c")]},
                             pages, lh._series(pages), [])
    assert [loss["environment"] for loss in losses] == [True]
    clean = lh._loss_events({"feedbacks": feedbacks, "injections": []}, pages,
                            lh._series(pages), [])
    assert [loss["environment"] for loss in clean] == [False]


def test_an_open_ended_cause_window_runs_to_the_end_of_the_log():
    """`hi=None` is a different question from `hi=len(samples)`, and the second one is an
    IndexError: the first episode that needed "has anything explained this since?" was an L4
    re-place, and the batches scored before it had not reached that branch."""
    pages = [_page(7, 1, _item("placed:obj_a:region_1", "true")),
             _page(9, 2, _item("placed:obj_a:region_1", "false"))]
    ep = {"feedbacks": [_feedback(11, "pick", "obj_a")], "injections": []}
    assert lh._cause_window(ep, pages, 0, None, "obj_a")["agent_direct"] is True
    with pytest.raises(IndexError):
        lh._cause_window(ep, pages, 0, len(pages), "obj_a")
    assert lh._cause_window(ep, pages, 0, 1, "obj_a") == \
        {"agent_direct": False, "agent_knock": False, "environment": False}, \
        "a bounded window stops at the reading, which is what makes the two calls different"


def test_a_knock_on_from_another_bodys_feedback_is_not_the_same_as_a_direct_action():
    pages = [_page(7, 1, _item("placed:obj_a:region_1", "true")),
             _page(13, 2, _item("placed:obj_a:region_1", "false"))]
    knock = _feedback(11, "place", "obj_b", "region_1", moved=["obj_a"])
    direct = _feedback(11, "pick", "obj_a")
    both = lh._cause_window({"feedbacks": [knock], "injections": [_inj(12, "obj_a")]},
                            pages, 0, 1, "obj_a")
    assert (both["agent_knock"], both["agent_direct"], both["environment"]) == (True, False, True), \
        "an interval with both signals is `ambiguous`, and is never split between two causes"
    assert lh._cause_window({"feedbacks": [direct], "injections": []}, pages, 0, 1,
                            "obj_a")["agent_direct"] is True


# -------------------------------------------------------------- one episode ----


@pytest.fixture(scope="module")
def full_episode(tmp_path_factory):
    """One episode carrying every population at once: an authored edge, a definite claim, a
    displacement-caused regression that the agent then repaired, and two clean attempts."""
    d = tmp_path_factory.mktemp("lh_episode")
    edge = {"from_subgoal_id": "sg_k1", "on_subgoal_id": "sg_k2", "kind": "resource",
            "violated": False}
    records = [
        _plan(5, [_row("k1", "placed:obj_a:region_1", "True"),
                  _row("k2", "placed:obj_b:region_2", "unknown")], deps=[edge]),
        _ctx(7, 1, _item("placed:obj_a:region_1", "true"), _item("placed:obj_b:region_2", "false")),
        _injection(10, "obj_a"),
        _ctx(12, 2, _item("placed:obj_a:region_1", "false"),
             _item("placed:obj_b:region_2", "false")),
        _fb(14, "pick", "obj_a"),
        _fb(16, "place", "obj_a", "region_1"),
        _ctx(18, 3, _item("placed:obj_a:region_1", "true"),
             _item("placed:obj_b:region_2", "false")),
    ]
    return lh.score_episode(_episode(d, "lh_probe_case", records, task_id="lh_probe_case"))


def test_one_scored_episode_publishes_every_row_with_its_own_counts(full_episode):
    assert full_episode["metric_version"] == lh.METRIC_VERSION
    assert set(full_episode["counts"]) <= set(lh.METRIC_DEFINITIONS)
    for mid, (n, dn) in full_episode["counts"].items():
        row = full_episode["metrics"][mid]
        assert [row["numerator"], row["denominator"]] == [n, dn], mid
        assert row["measured"] == bool(dn), mid
    assert full_episode["counts"]["L1.assignment_completion"] == [1, 2]
    assert full_episode["counts"]["L1.plan_row_completion"] == [1, 2]
    assert full_episode["counts"]["L1.row_claim_vs_key"] == [0, 1], \
        "one definite claim, agreeing with its own round's page"
    assert full_episode["counts"]["L2.dependency_edge_violation"] == [0, 1]
    assert full_episode["counts"]["L3.regression_rate"] == [1, 1]
    assert full_episode["counts"]["L3.environment_caused_regression"] == [1, 1]
    assert full_episode["counts"]["L3.agent_caused_regression"] == [0, 1]
    assert full_episode["counts"]["L3.ambiguous_regression"] == [0, 1]
    assert full_episode["counts"]["L3.unrecovered_regression"] == [0, 1]
    assert full_episode["counts"]["L4.unnecessary_rework"] == [0, 1], \
        "the re-place is explained by the displacement and the pick, so it is L6's recovery"
    assert full_episode["counts"]["L4.plan_asked_move"] == [0, 1]
    assert full_episode["counts"]["L5.repeated_failure_share"] == [0, 2]
    assert full_episode["counts"]["L6.recovery_effectiveness"] == [1, 1]
    assert full_episode["counts"]["L6.recorded_vs_inferred"] == [0, 1], \
        "the timeline shows the recovery and no record files it: that is the instrument's audit"
    assert full_episode["instrument"]["loss_kinds"] == ["regression"]
    assert full_episode["instrument"]["definite_row_claims"] == 1
    assert full_episode["instrument"]["ablation_record"] is True
    assert full_episode["instrument"]["arm_claim_violations"] == []


def test_a_cell_that_filed_no_revision_reports_its_own_empty_denominator(full_episode):
    """`L6.replanning_effectiveness` is counted on an episode that never revised: the row must
    exist with its mechanical reason attached, not go missing from the table."""
    row = full_episode["metrics"]["L6.replanning_effectiveness"]
    assert row["measured"] is False and row["value"] is None
    assert row["not_measured_reason"] == lh._NOT_MEASURED["L6.replanning_effectiveness"]
    assert full_episode["counts"]["L6.replanning_effectiveness"] == [0, 0]


def test_a_plan_that_said_nothing_definite_is_an_instrument_gap_not_a_clean_sheet(tmp_path):
    """Every `satisfied` still `unknown` means the metric has no unit at all, and the artifact has
    to say which of the two it found."""
    records = [_plan(5, [_row("k1", "placed:obj_a:region_1", "unknown")]),
               _ctx(7, 1, _item("placed:obj_a:region_1", "true"))]
    scored = lh.score_episode(_episode(tmp_path, "silent_plan", records))
    assert scored["counts"]["L1.row_claim_vs_key"] == [0, 0]
    assert scored["metrics"]["L1.row_claim_vs_key"]["measured"] is False
    assert any("instrument gap" in f for f in scored["findings"]), scored["findings"]
    assert scored["instrument"]["definite_row_claims"] == 0


def test_a_recovery_record_with_no_loss_to_attach_to_is_a_finding_not_a_silent_zero(tmp_path):
    """One of the two instruments is lying, and L6's denominator is where it would show."""
    records = [_ctx(7, 1, _item("placed:obj_a:region_1", "true")), _recovery(9, "obj_a")]
    scored = lh.score_episode(_episode(tmp_path, "rec_only", records))
    assert any("no loss event" in f for f in scored["findings"]), scored["findings"]
    assert scored["counts"].get("L6.recovery_effectiveness", [0, 0]) == [0, 0]
    assert scored["instrument"]["loss_events"] == 0 and scored["instrument"]["recovery_records"] == 1


def test_a_repair_after_a_silent_handoff_is_counted_as_a_recovery_even_with_no_flip(tmp_path):
    """LH-1 scored 0/0 on exactly this shape, because no predicate ever flipped true→false."""
    records = [_ctx(7, 1, _item("placed:obj_c:region_2", "false")),
               _fb(20, "place", "obj_c", "region_2"),
               _ctx(21, 2, _item("placed:obj_c:region_2", "false")),
               _recovery(22, "obj_c"),
               _fb(23, "pick", "obj_c"),
               _fb(25, "place", "obj_c", "region_2"),
               _ctx(27, 3, _item("placed:obj_c:region_2", "true"))]
    scored = lh.score_episode(_episode(tmp_path, "handoff", records))
    assert scored["instrument"]["loss_kinds"] == ["unverified_handoff"]
    assert scored["detail"]["transitions"] == [], "nothing flipped true→false in this episode"
    assert scored["counts"]["L3.regression_rate"] == [0, 1], \
        "the pair is on §11's regression timeline only once the repair made it true, and it is a " \
        "zero there — the loss lives in L6's denominator, which is the point of the second rule"
    assert scored["counts"]["L6.recovery_effectiveness"] == [1, 1]
    assert scored["counts"]["L6.recorded_vs_inferred"] == [1, 1], \
        "the record window is loss sequence < record <= first action taken about it"


def test_the_arm_claim_is_re_checked_against_the_records_it_produced(tmp_path):
    """An arm that names itself `wo_planning` and publishes a plan view is a broken experiment, and
    the scorer notices without being told which arm it is reading."""
    rows = [_row("k1", "placed:obj_a:region_1", "unknown")]
    claimed = lh.score_episode(_episode(tmp_path, "liar", [_plan(5, rows)],
                                         arm="wo_planning", condition="wo_planning"))
    assert claimed["instrument"]["arm_claim_violations"] == ["plan"]
    assert any("produced records owned by them" in f for f in claimed["findings"])
    honest = lh.score_episode(_episode(tmp_path, "honest",
                                        [_ctx(7, 1, _item("placed:obj_a:region_1", "true"))],
                                        arm="wo_planning", condition="wo_planning"))
    assert honest["instrument"]["arm_claim_violations"] == [], \
        "the violation is the record, not the label"
    assert honest["counts"]["L1.plan_row_completion"] == [0, 0], \
        "the counting site is reached on every arm; what the switch removes is the units, and " \
        "`not_measured` is what keeps that apart from a perfect score"


def test_a_v02_record_the_registry_does_not_know_is_an_error_rather_than_a_pass(tmp_path):
    scored = lh.score_episode(_episode(
        tmp_path, "unknown_type", [_rec(6, "not_in_the_vocabulary", {}, v02=True)]))
    assert any("unattributed v0.2 record type" in f for f in scored["findings"]), scored["findings"]
    assert set(scored["instrument"]["v02_record_types"]) - set(V02_EVENT_TYPES) == \
        {"not_in_the_vocabulary"}


# ------------------------------------------------------------------ runs ----


def test_the_two_control_policies_are_never_pooled_into_one_cell(tmp_path):
    """`policy=rule` reads pending goals and never a plan page, which is why obligation-shaped
    numbers are comparable across arms only within one policy."""
    shared = [_ctx(7, 1, _item("placed:obj_a:region_1", "true"))]
    a = _episode(tmp_path, "a", shared, arm="full", policy="rule")
    b = _episode(tmp_path, "b", shared, arm="full", policy="payload")
    run = lh.score_run(_run(tmp_path, "one_batch", [a, b]))
    assert sorted(run["pools"]) == ["full/payload", "full/rule"]
    for cell, block in run["pools"].items():
        assert block["n_episodes"] == 1, cell
        assert block["policy"] == cell.rsplit("/", 1)[1], cell
        assert block["metrics"]["L1.assignment_completion"]["denominator"] == 1, \
            "a pooled cell that quietly absorbed the other policy's units"


def test_rows_are_counted_per_episode_and_edges_are_counted_per_edge(tmp_path):
    """`refresh` re-publishes an unresolved violation every round, so counting per round would
    multiply one defect into ten: the edge set is keyed by (from, on, kind)."""
    edge = {"from_subgoal_id": "sg_k1", "on_subgoal_id": "sg_k2", "kind": "resource",
            "violated": True}
    rows = [_row("k1", "placed:obj_a:region_1", "True")]
    records = [_plan(5, rows, deps=[edge]),
               _plan(8, rows, deps=[edge], version=2, type_="plan_revision"),
               _plan(11, rows, deps=[edge], version=3, type_="plan_revision")]
    scored = lh.score_episode(_episode(tmp_path, "republished", records))
    assert scored["counts"]["L2.dependency_edge_violation"] == [1, 1], "three views, one edge"
    assert scored["counts"]["L2.violations_per_episode"] == [1, 1]
    assert scored["detail"]["dependency_edges"] == ["sg_k1->sg_k2:resource"]
    assert scored["counts"]["L6.replanning_effectiveness"] == [0, 2], \
        "a spinning planner stays in the denominator"


def test_a_dropped_row_that_named_a_goal_is_lost_work_and_a_dropped_clear_row_is_not(tmp_path):
    """§5.2's work object cannot shrink by forgetting — but a row the verifier has no verdict for
    is a statement about the sensor, so the two are separated instead of both counted."""
    records = [_plan(5, [_row("k1", "placed:obj_a:region_1", "unknown"),
                         _row("k2", "clear:region_2", "unknown", kind="clear")]),
               _ctx(7, 1, _item("placed:obj_a:region_1", "false")),
               _plan(9, [], version=2, type_="plan_revision")]
    scored = lh.score_episode(_episode(tmp_path, "dropped", records))
    lost = [f for f in scored["findings"] if "left the plan while their predicate" in f]
    unjudgeable = [f for f in scored["findings"] if "naming no goal predicate" in f]
    assert len(lost) == 1 and "k1" in lost[0], scored["findings"]
    assert len(unjudgeable) == 1 and "k2" in unjudgeable[0], scored["findings"]
    assert scored["counts"]["L1.plan_row_completion"] == [0, 1], \
        "only k1 names a predicate the verifier listed; k2 has no verdict to be scored against"
    assert scored["detail"]["row_kinds"] == {"achieve": 1, "clear": 1}


def test_a_run_pool_is_the_sum_of_its_episodes_and_not_their_mean(tmp_path):
    big = _episode(tmp_path, "big", [_ctx(7, 1, *[
        _item(f"placed:obj_{i}:region_1", "true" if i else "false") for i in range(5)])])
    small = _episode(tmp_path, "small", [_ctx(7, 1, _item("placed:obj_a:region_1", "true"))])
    run = lh.score_run(_run(tmp_path, "pool", [big, small]))
    row = run["pools"]["full/rule"]["metrics"]["L1.assignment_completion"]
    assert (row["numerator"], row["denominator"]) == (5, 6), row
    assert row["value"] == round(5 / 6, 4)
    assert row["value"] != round((0.8 + 1.0) / 2, 4), "the mean of two unequal cells"
    # the loop's own v0.1 counters travel beside the rates, never inside them
    assert [e["loop_counters"]["decision_rounds"] for e in run["episodes"]] == [3, 3]
    assert run["pools"]["full/rule"]["failure_codes"] == {}, "no attempt failed in this batch"


def test_an_arm_that_authors_no_rows_shows_an_empty_cell_and_says_why(tmp_path):
    """The §9 switch, measured: `wo_planning` publishes no view, so its subgoal-completion row has
    no denominator, while `L1.assignment_completion` — graded from the verifier's list on every
    arm — still does. The two must not be allowed to look like the same kind of zero."""
    d = _episode(tmp_path, "wo", [_ctx(7, 1, _item("placed:obj_a:region_1", "true"))],
                 arm="wo_planning", condition="wo_planning")
    run = lh.score_run(_run(tmp_path, "wo_batch", [d]))
    metrics = run["pools"]["wo_planning/rule"]["metrics"]
    assert metrics["L1.plan_row_completion"]["measured"] is False
    assert metrics["L1.row_claim_vs_key"]["measured"] is False
    assert metrics["L2.dependency_edge_violation"]["measured"] is False
    assert metrics["L1.assignment_completion"]["measured"] is True
    assert run["pools"]["wo_planning/rule"]["row_kinds"] == {}, \
        "the row population is published so L2's empty denominator can be checked against it"


def test_the_dependency_row_reports_the_population_it_could_not_ask_about(tmp_path):
    """A cell that published rows and no edge has no denominator for §11's dependency row; that is
    reported, and is not the same sentence as "no violation happened"."""
    records = [_plan(5, [_row("k1", "placed:obj_a:region_1", "True")]),
               _ctx(7, 1, _item("placed:obj_a:region_1", "true"))]
    scored = lh.score_episode(_episode(tmp_path, "no_edges", records))
    finding = [f for f in scored["findings"] if "no dependency edge" in f]
    assert len(finding) == 1, scored["findings"]
    assert "not measurable here, not as zero violations" in finding[0]
    assert scored["detail"]["dependency_edges"] == []
    assert scored["detail"]["row_kinds"] == {"achieve": 1}
    # and a cell with an edge gets no such finding
    edge = {"from_subgoal_id": "sg_k1", "on_subgoal_id": "sg_k2", "kind": "resource",
            "violated": False}
    with_edge = lh.score_episode(_episode(
        tmp_path, "with_edge",
        [_plan(5, [_row("k1", "placed:obj_a:region_1", "True")], deps=[edge]),
         _ctx(7, 1, _item("placed:obj_a:region_1", "true"))]))
    assert not [f for f in with_edge["findings"] if "no dependency edge" in f]


def test_the_contrast_names_a_switch_instead_of_reporting_a_difference_of_zero():
    def scored(arm, counts):
        metrics = lh._render(counts)
        return {"run_dir": f"/tmp/{arm}", "perception": {"arm": arm},
                "pools": {f"{arm}/rule": {"arm": arm, "policy": "rule", "n_episodes": 3,
                                          "metrics": metrics}}}

    out = lh.arm_contrast([
        scored("full", {"L6.recorded_vs_inferred": [3, 3], "L1.assignment_completion": [4, 6]}),
        scored("wo_replanning", {"L6.recorded_vs_inferred": [0, 0],
                                 "L1.assignment_completion": [2, 6]})])
    rows = {d["metric"]: d for d in out["differences"]}
    assert out["reference"] == "full"
    switch = rows["L6.recorded_vs_inferred"]
    assert switch["comparable"] is False and switch["delta"] is None
    assert switch["not_measured_on"] == ["wo_replanning"], "the switch, not a blindness"
    assert switch["asymmetric"] is True
    kept = rows["L1.assignment_completion"]
    assert kept["comparable"] is True
    assert kept["delta"] == round(kept["value"] - kept["reference"], 4), \
        "the difference is taken between the two published values, the two the reader sees"
    assert kept["not_measured_on"] is None
    assert set(out["cells"]) == {"full::full/rule", "wo_replanning::wo_replanning/rule"}


def test_the_artifact_ships_its_definitions_and_names_the_shape_it_could_not_author(tmp_path):
    """§8's second shape is not a rate of 0.0, and the JSON has to say so by itself: a reader of
    one file must not need the .md to tell "no violation happened" from "no episode in which one
    could happen was authored"."""
    d = _episode(tmp_path, "one", [_ctx(7, 1, _item("placed:obj_a:region_1", "true"))])
    result = lh.measure([_run(tmp_path, "batch", [d])])
    assert result["metric_version"] == lh.METRIC_VERSION
    assert result["definitions"] == lh.METRIC_DEFINITIONS
    assert result["spec_rows"] == {row: list(ids) for row, ids in lh.SPEC_ROWS}
    assert result["spec8_shape_2"]["status"] == "not_applicable"
    assert result["spec8_shape_2"]["probes"], "a not-applicable verdict must name its measurement"
    assert "not authorable" in result["spec8_shape_2"]["measured_as"]
    assert set(result["asymmetric_fields"]) == set(lh.ARM_ASYMMETRIC_FIELDS)
    assert result["n_episodes"] == 1 and result["n_runs"] == 1
    # the loop's own counters are pooled only at `measure`, beside the rates
    assert result["runs"][0]["pools"]["full/rule"]["loop_summary"]["decision_rounds"] == [3]
    table = lh.render_markdown(result)
    assert lh.METRIC_VERSION in table
    assert "§8 shape 2" in table and "not_applicable" in table
    assert "plan rows summed over episodes" in table, \
        "the population behind an empty L2 denominator has to be in the readable table too"
    for mid in result["runs"][0]["pools"]["full/rule"]["metrics"]:
        assert f"`{mid}`" in table, f"{mid} is in the JSON and not in the table"
