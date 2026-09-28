"""SPEC-v0.2 §5.6 step 1 contract tests for Skill Gap Detection: when a failure is evidence about
the *library*, and when saying so would be a lie about the plan.

The detector is a record reader, so the tests are mostly about the boundary between "what the
ledger says" and "what I would like it to mean". Four groups.

1. What it may read. Every field name must be a field `ExecutionFeedback` actually declares (a
   rename would not raise — it would report zero gaps forever), and the answer key must be
   unreachable: §12.6 forbids the hidden evaluator from feeding the agent, so a summary carrying an
   `expected` block has to produce the same rows as one that does not.
2. The classification table, one test per rule in the module docstring — including the rule that
   *fires on nothing*: a single refusal is the primitive behaving as §5.2 declares, and a detector
   that called it a gap would inflate the denominator of §11's "candidate generation rate" and hand
   a module credit for a bug it did not fix. `precondition_unmet` therefore never appears, and that
   is asserted rather than implied.
3. What a row carries: the evidence ids in record order, the round from `decision_context`, the
   effect in the verifier's own vocabulary, the subgoal join, and the library entries considered and
   not accepted. Plus determinism modulo freshly drawn ids, because P4-e freezes these rows.
4. The file and run layer: an unreadable path is an error, not an empty list, and the report's
   arithmetic identities hold — `cells_not_reported` included, because a census that only publishes
   positives cannot distinguish "no gaps here" from "nobody looked".

The events are hand-built dicts, not episodes: `test_the_fixture_records_are_valid_feedback_records`
validates every one against the production model, so a fixture that drifted from the contract fails
here instead of quietly becoming a fiction the assertions then confirm.
"""
from __future__ import annotations

import inspect
import json
import os
from typing import get_args

import pytest

from embodied_agent.acquisition import gap, program
from embodied_agent.core.contracts import ExecutionFeedback, SkillStatus
from embodied_agent.core.v02 import (EVENT_MODULE, RECORD_FIELDS, RECORD_NAMES, V02_EVENT_TYPES,
                                    SkillGap, SkillSpec, record_classes)


# ------------------------------------------------------------------ fixtures ----
def _ctx(seq, round_index, context=None):
    return {"type": "decision_context", "event_id": f"evt_ep_{seq}",
            "payload": {"context_id": context or f"ctx_{seq}", "round_index": round_index}}


def _fb(seq, *, skill="pick", entity="obj_green_1", target=None, status="failed",
        code="GRASP_MISS", measured=True, pre_v=1, post_v=4, context=None, reasons=(),
        progress=(), obs="obs_0004"):
    return {"type": "execution_feedback", "event_id": f"evt_ep_{seq}",
            "payload": {"feedback": {
                "skill": skill, "entity_id": entity, "target_id": target, "status": status,
                "failure_code": code, "new_measurement": measured,
                "pre_state_version": pre_v, "post_state_version": post_v,
                "context_id": context or f"ctx_{seq}", "post_observation_ref": obs,
                "rejection_reasons": list(reasons), "progress_changes": list(progress)}}}


def _refusal(seq, **kw):
    kwargs = dict(status="rejected", code="INVALID_DECISION", measured=False, pre_v=9, post_v=9,
                  obs="obs_0009", reasons=["step d0: pick while the hold state is unknown"])
    kwargs.update(kw)
    return _fb(seq, **kwargs)


def _plan(seq, rows):
    return {"type": "plan", "event_id": f"evt_ep_{seq}", "payload": {"view": {"rows": list(rows)}}}


def _ablation(seq, condition):
    return {"type": "ablation", "event_id": f"evt_ep_{seq}", "payload": {"condition": condition}}


def _row(subgoal_id, predicate, entities=()):
    return {"subgoal_id": subgoal_id, "predicate_id": predicate, "kind": "goal",
            "status": "active", "satisfied": "false", "target_entity_ids": list(entities)}


def _library_spec(name, effects):
    return program.program(name, f"the {name} program", [program.param("object")],
                           [program.step("pick", {"object_id": "{object}"})],
                           preconditions=["the object is in the observation"],
                           expected_effects=effects,
                           termination_conditions=["one grasp attempt is measured"],
                           applicability=["a measured grasp miss"])


# --------------------------------------------------------- 1. what it may read ----
def test_every_field_the_detector_reads_is_a_field_the_contract_declares():
    declared = set(ExecutionFeedback.model_fields)
    assert set(gap.READ_FIELDS) <= declared, sorted(set(gap.READ_FIELDS) - declared)
    # and the two sets the rules turn on are the record's own words, not invented ones
    assert set(gap.NON_COMPLETION_STATUSES) <= {s.value for s in SkillStatus}
    assert gap.LOOP_GUARD_CODE == "REPEATED_INVALID"


def test_the_fixture_records_are_valid_feedback_records():
    events = [_ctx(1, 1), _fb(2), _refusal(3),
              _fb(4, skill="place", target="tray_middle", code="PLACE_UNSTABLE")]
    for event in events:
        if event["type"] != "execution_feedback":
            continue
        ExecutionFeedback.model_validate(event["payload"]["feedback"])


def test_an_answer_key_in_the_summary_changes_nothing():
    """What the finding is, versus what the row says about the run.

    `detect_gaps` does read the summary — `episode_id`, `result.task_id`, `result.terminal_status` —
    and those appear in provenance. It must not read `expected`, the third-layer verdict §12.6 keeps
    away from the agent. So the two calls below differ *only* by an `expected` block, and the whole
    row has to come out identical, every provenance field included.

    A substring check on the serialized row is not this test: the module's own `derived_from` string
    says "no expected block read", so the word appears there to deny exactly what a grep would
    have "found".
    """
    events = [_ctx(1, 1), _fb(2), _fb(3, pre_v=4, post_v=7)]
    summary = {"episode_id": "ep", "result": {"task_id": "t", "terminal_status": "failed"}}
    without = gap.detect_gaps(events, summary=summary)
    with_key = gap.detect_gaps(events, summary={**summary,
                                                "expected": {"objects_total": 6, "success": True}})
    assert len(without) == len(with_key) == 1
    # `gap_id` and the two envelope timestamps are the frozen `Record` base stamping the object at
    # construction; every declared field that carries information about the episode must match.
    stamped = {"gap_id", "created_at", "updated_at"}
    assert set(SkillGap.model_fields) - stamped == set(without[0].model_dump()) - stamped
    assert {k: v for k, v in without[0].model_dump().items() if k not in stamped} \
        == {k: v for k, v in with_key[0].model_dump().items() if k not in stamped}
    # ...and the answer key is not merely unused by this call: the module never names it
    source = inspect.getsource(gap)
    assert '["expected"]' not in source and '.get("expected")' not in source


def test_the_detector_emits_no_event_type_and_adds_no_record_type():
    # §5.6's four types are already declared and still have no producer: P4-a writes records that
    # the ledger will carry later, it does not extend the frozen list to make room for itself.
    assert len(V02_EVENT_TYPES) == 15
    assert all(EVENT_MODULE[t] == "skill_acquisition"
               for t in ("skill_gap", "skill_candidate", "skill_validation", "skill_library"))
    assert sum(1 for t in V02_EVENT_TYPES
               if EVENT_MODULE[t] == "skill_acquisition") == 4
    # `SkillGap` is a real record — a `Record` subclass, so `all_records_sha256` already hashes its
    # field surface — even though §6's `RECORD_NAMES` lists only the other three §5.6 types. The
    # detector's output is therefore schema-fingerprinted, not a dict shaped by convention.
    assert "SkillGap" not in RECORD_NAMES
    assert "SkillSpec" in RECORD_NAMES and "SkillCandidate" in RECORD_NAMES
    assert record_classes()["SkillGap"] is SkillGap


# ------------------------------------------------- 2. the classification table ----
def test_one_executed_failure_is_the_world_being_hard_not_a_library_hole():
    assert gap.detect_gaps([_ctx(1, 1), _fb(2)]) == []


def test_a_single_precondition_refusal_is_not_reported_as_a_skill_gap():
    """The rule that never fires on the current artifacts, kept because it is the boundary.

    Measured on the P2+P3 batches: all 12 `rejected/INVALID_DECISION` records sit inside cells that
    repeat, so `single_attempt_refusal` is 0 there — this fixture is the only place the rule is
    exercised, which is itself the honest statement that the rule costs nothing and buys the
    `precondition_unmet` zero.
    """
    gaps = gap.detect_gaps([_ctx(1, 1), _refusal(2)])
    assert gaps == []
    cell = gap._cells(gap._attempts([_ctx(1, 1), _refusal(2)]))[0]
    assert gap.classify(cell) is None
    assert len(cell.attempts) == 1 and cell.attempts[0].status == "rejected"


def test_two_attempts_that_each_remeasured_the_world_are_a_repeated_failure():
    gaps = gap.detect_gaps([_ctx(1, 1), _fb(2), _ctx(3, 2), _fb(4, pre_v=5, post_v=8)])
    assert len(gaps) == 1
    assert gaps[0].missing_because == "repeated_failure"
    assert gaps[0].provenance["rule"] == "repeat_after_remeasurement"
    assert gaps[0].provenance["measured_any"] is True


def test_two_identical_refusals_that_measured_nothing_are_a_missing_termination():
    events = [_ctx(1, 1), _refusal(2), _ctx(3, 2), _refusal(4, pre_v=9, post_v=9)]
    gaps = gap.detect_gaps(events)
    assert [g.missing_because for g in gaps] == ["no_termination"]
    assert gaps[0].provenance["rule"] == "repeat_without_new_measurement"
    assert gaps[0].provenance["measured_any"] is False
    assert len(gaps[0].evidence_refs) == 2


@pytest.mark.parametrize("measured,pre_v,post_v,want", [
    (False, 1, 1, "no_termination"),      # neither re-measured nor advanced: nothing changed
    (False, 1, 2, "repeated_failure"),    # the state moved: the loop did learn, and failed again
    (True, 5, 5, "repeated_failure"),     # a fresh measurement with no change is still information
])
def test_what_separates_the_two_repetition_rules_is_information(measured, pre_v, post_v, want):
    second = _fb(3, measured=measured, pre_v=pre_v, post_v=post_v)
    gaps = gap.detect_gaps([_ctx(1, 1), _fb(2, measured=measured, pre_v=pre_v, post_v=post_v),
                            _ctx(4, 2), second])
    assert [g.missing_because for g in gaps] == [want]


def test_a_cell_that_learnt_once_and_stalled_still_counts_as_learning():
    events = [_ctx(1, 1), _fb(2), _ctx(3, 2),
              _fb(4, measured=False, pre_v=8, post_v=8)]
    gaps = gap.detect_gaps(events)
    assert gaps[0].missing_because == "repeated_failure"


@pytest.mark.parametrize("reason", [
    "step d0: unknown skill open_drawer",
    "unknown skill drawer_open",
])
def test_an_action_outside_the_catalogue_is_a_gap_after_one_occurrence(reason):
    events = [_ctx(1, 1), _refusal(2, reasons=[reason])]
    gaps = gap.detect_gaps(events)
    assert [g.missing_because for g in gaps] == ["no_skill"]
    assert gaps[0].provenance["rule"] == "unknown_skill"


def test_the_unknown_skill_rule_outranks_the_recurrence_rules():
    events = [_ctx(1, 1), _refusal(2, reasons=["step d0: unknown skill kick"]),
              _ctx(3, 2), _refusal(4, reasons=["step d0: unknown skill kick"], pre_v=9, post_v=9)]
    gaps = gap.detect_gaps(events)
    assert len(gaps) == 1 and gaps[0].missing_because == "no_skill"
    assert len(gaps[0].evidence_refs) == 2


def test_the_runtime_loop_guard_makes_a_single_record_a_gap():
    """`REPEATED_INVALID` *is* the runtime saying "this could not end"; one record is the episode."""
    events = [_ctx(1, 1), _refusal(2, code=gap.LOOP_GUARD_CODE,
                                  reasons=["identical invalid decision repeated to the budget"])]
    gaps = gap.detect_gaps(events)
    assert [g.missing_because for g in gaps] == ["no_termination"]
    assert gaps[0].provenance["rule"] == "repeated_invalid_guard"


@pytest.mark.parametrize("status", ["completed", "finish_accepted", "cancelled", "uncertain"])
def test_only_the_statuses_that_mean_did_not_happen_are_attempts(status):
    events = [_ctx(1, 1), _fb(2, status=status), _ctx(3, 2), _fb(4, status=status)]
    assert gap.detect_gaps(events) == []
    assert gap._attempts(events) == []


def test_a_cell_is_keyed_on_what_the_record_names_and_gaps_are_per_episode():
    events = [_ctx(1, 1),
              _fb(2, entity="obj_green_1"), _fb(3, entity="obj_green_1"),
              _fb(4, entity="obj_red_5", code="IK_OR_PATH_UNREACHABLE"),
              _fb(5, entity="obj_red_5", code="IK_OR_PATH_UNREACHABLE"),
              _fb(6, skill="place", entity="obj_red_5", target="tray_left",
                  code="PLACE_UNSTABLE"),
              _fb(7, skill="place", entity="obj_red_5", target="tray_left",
                  code="PLACE_UNSTABLE")]
    gaps = gap.detect_gaps(events)
    assert len(gaps) == 3
    assert len({g.provenance["cell"] for g in gaps}) == 3
    # the same cell in two episodes is two gaps: a library hole found twice is two findings about
    # the world, and §11's rate is per gap the loop actually met
    one = gap.detect_gaps([_ctx(1, 1), _fb(2), _fb(3)])
    assert len(one) == 1 and len(gap.detect_gaps([_ctx(1, 1), _fb(2), _fb(3)])) == 1


def test_the_detector_emits_only_values_the_frozen_enum_has():
    events = [_ctx(1, 1), _fb(2), _fb(3), _ctx(4, 2),
              _refusal(5, reasons=["step d0: unknown skill flip"]), _refusal(6,
                                                                            reasons=[
                                                                                "step d0: unknown skill flip"])]
    gaps = gap.detect_gaps(events)
    allowed = set(get_args(SkillGap.model_fields["missing_because"].annotation))
    assert {g.missing_because for g in gaps} <= allowed
    assert "precondition_unmet" not in {g.missing_because for g in gaps}
    assert allowed == {"no_skill", "precondition_unmet", "repeated_failure", "no_termination",
                       "other"}


# ------------------------------------------------------------ 3. what a row says ----
def test_the_evidence_is_the_event_ids_in_the_order_the_loop_ran():
    events = [_ctx(1, 1), _fb(2), _ctx(3, 2), _fb(4), _ctx(5, 3), _fb(6)]
    gaps = gap.detect_gaps(events)
    assert gaps[0].evidence_refs == ["evt_ep_2", "evt_ep_4", "evt_ep_6"]
    assert gaps[0].provenance["attempt_count"] == 3
    assert all(ref.startswith("evt_") for ref in gaps[0].evidence_refs)


@pytest.mark.parametrize("skill,entity,target,effect,source", [
    ("pick", "obj_green_1", None, "grasp:obj_green_1", "verifier_predicate_id"),
    ("place", "obj_green_1", "tray_left", "placed:obj_green_1:tray_left", "verifier_predicate_id"),
    ("observe", None, None, "a new WorldState snapshot with a fresh observation_ref",
     "catalogue_post"),
    ("pick", None, None, "object lifted above its support and confirmed held", "catalogue_post"),
])
def test_the_desired_effect_is_said_in_the_loop_vocabulary_and_its_origin_is_recorded(
        skill, entity, target, effect, source):
    assert gap.desired_effect(skill, entity, target) == (effect, source)


def test_a_place_gap_names_the_predicate_the_verifier_would_measure():
    events = [_ctx(1, 1),
              _fb(2, skill="place", entity="obj_green_1", target="tray_left",
                  code="PLACE_UNSTABLE"),
              _ctx(3, 2),
              _fb(4, skill="place", entity="obj_green_1", target="tray_left",
                  code="PLACE_UNSTABLE", pre_v=5, post_v=8)]
    gaps = gap.detect_gaps(events)
    assert gaps[0].desired_effect == "placed:obj_green_1:tray_left"
    assert gaps[0].provenance["effect_source"] == "verifier_predicate_id"


def test_the_round_index_comes_from_the_ledger_and_says_how_it_was_found():
    exact = gap.detect_gaps([_ctx(1, 7, context="ctx_a"),
                             _fb(2, context="ctx_a"), _fb(3, context="ctx_a")])
    assert exact[0].round_index == 7
    assert exact[0].provenance["round_index_source"] == "decision_context"

    nearest = gap.detect_gaps([_ctx(1, 4, context="ctx_a"),
                               _fb(2, context="ctx_z"), _fb(3, context="ctx_z")])
    assert nearest[0].round_index == 4
    assert nearest[0].provenance["round_index_source"] == "nearest_preceding_decision_context"

    blind = gap.detect_gaps([_fb(1, context="ctx_z"), _fb(2, context="ctx_z")])
    assert blind[0].round_index == 0
    assert blind[0].provenance["round_index_source"] == "unresolved"


def test_the_subgoal_join_prefers_the_predicate_and_falls_back_to_the_entity():
    by_predicate = [_plan(1, [_row("sub_a", "grasp:obj_green_1")]), _ctx(2, 1), _fb(3), _fb(4)]
    gaps = gap.detect_gaps(by_predicate)
    assert gaps[0].subgoal_id == "sub_a"
    assert gaps[0].provenance["subgoal_id_source"] == "predicate_id"

    by_entity = [_plan(1, [_row("sub_b", "placed:obj_green_1:tray_left", ["obj_green_1"])]),
                 _ctx(2, 1), _fb(3), _fb(4)]
    gaps = gap.detect_gaps(by_entity)
    assert gaps[0].subgoal_id == "sub_b"
    assert gaps[0].provenance["subgoal_id_source"] == "target_entity_id"

    ambiguous = [_plan(1, [_row("sub_b", "placed:obj_green_1:tray_left", ["obj_green_1"]),
                           _row("sub_c", "placed:obj_green_1:tray_right", ["obj_green_1"])]),
                 _ctx(2, 1), _fb(3), _fb(4)]
    gaps = gap.detect_gaps(ambiguous)
    assert gaps[0].subgoal_id is None
    assert gaps[0].provenance["subgoal_id_source"] == "ambiguous_across_rows"

    none = [_plan(1, [_row("sub_d", "placed:obj_blue_9:tray_left", ["obj_blue_9"])]),
            _ctx(2, 1), _fb(3), _fb(4)]
    gaps = gap.detect_gaps(none)
    assert gaps[0].subgoal_id is None
    assert gaps[0].provenance["subgoal_id_source"] == "unresolved"

    # the *last* published view wins, because that is the work graph the episode ended with
    revised = [_plan(1, [_row("sub_old", "grasp:obj_green_1")]),
               {"type": "plan_revision", "event_id": "evt_ep_9",
                "payload": {"view": {"rows": [_row("sub_new", "grasp:obj_green_1")]}}},
               _ctx(2, 1), _fb(3), _fb(4)]
    assert gap.detect_gaps(revised)[0].subgoal_id == "sub_new"


def test_the_candidates_considered_name_what_was_looked_at_and_rejected():
    events = [_ctx(1, 1), _fb(2), _fb(3)]
    assert gap.detect_gaps(events)[0].existing_candidates_considered == ["pick"]

    covering = _library_spec("grasp_after_clear_look", ["grasp:{object}"])
    gaps = gap.detect_gaps(events, library=[covering])
    assert gaps[0].existing_candidates_considered == ["pick", "grasp_after_clear_look"]
    # a library entry about a different effect is not considered, or every entry would be listed
    # in every row and the field would say nothing
    unrelated = _library_spec("place_gently", ["placed:{object}:tray_left"])
    assert gap.detect_gaps(events, library=[unrelated])[0].existing_candidates_considered == ["pick"]


def test_a_gap_the_library_already_covers_is_separated_from_one_it_does_not():
    """`covered` is the transfer-failure row, and the failing primitive is never in it.

    Without this split, "the library had nothing" and "the library had something that did not
    transfer" both read as one gap count, and §11's two rows would be computed from each other.
    """
    assert gap.covers_effect(_library_spec("grasp_after_clear_look", ["grasp:{object}"]),
                             "grasp:obj_green_1") is True
    # a template and a concrete predicate share a stem, which is the whole point of the match
    assert gap.covers_effect(_library_spec("place_gently", ["placed:{object}:tray_left"]),
                             "grasp:obj_green_1") is False
    assert gap.covers_effect(_library_spec("place_gently", ["placed:{object}:{target}"]),
                             "placed:obj_green_1:tray_middle") is True

    events = [_ctx(1, 1), _fb(2), _fb(3)]
    assert gap.detect_gaps(events)[0].provenance["covered"] == []
    [one] = gap.detect_gaps(events, library=[_library_spec("grasp_after_clear_look",
                                                           ["grasp:{object}"])])
    assert one.provenance["covered"] == ["grasp_after_clear_look"]
    # `pick` is in `existing_candidates_considered` but is not "coverage": it is the thing that failed
    assert "pick" in one.existing_candidates_considered
    assert "pick" not in one.provenance["covered"]


def test_the_provenance_carries_everything_needed_to_re_derive_the_row():
    events = [_ablation(1, "full"), _ctx(2, 3), _refusal(3), _refusal(4)]
    [one] = gap.detect_gaps(events, summary={"episode_id": "ep",
                                             "result": {"task_id": "lh_c6",
                                                        "terminal_status": "failed"}})
    prov = one.provenance
    assert prov["detector"] == gap.DETECTOR_VERSION
    assert prov["cell"] == "pick|obj_green_1|None|INVALID_DECISION"
    assert prov["statuses"] == ["rejected"]
    assert prov["rejection_reasons"] == ["step d0: pick while the hold state is unknown"]
    assert prov["condition"] == "full"
    assert prov["task_id"] == "lh_c6" and prov["terminal_status"] == "failed"
    assert prov["derived_from"].startswith("events.jsonl")
    assert one.episode_id == "ep" and one.schema_version == "4"
    assert one.gap_id.startswith("gap_")
    assert one.based_on_state_version == 9 and one.based_on_observation_ref == "obs_0009"
    assert set(RECORD_FIELDS) <= set(SkillGap.model_fields)


def test_the_progress_change_that_names_the_goal_assignment_is_kept_not_guessed():
    events = [_ctx(1, 1),
              _fb(2, progress=[{"assignment": "obj_green_1->tray_middle", "from": "true",
                                "to": "unknown"}]),
              _fb(3, progress=[{"assignment": "obj_green_1->tray_middle", "from": "unknown",
                                "to": "false"}]),
              _ctx(4, 2)]
    [one] = gap.detect_gaps(events)
    assert one.provenance["goal_assignment"] == "obj_green_1->tray_middle"
    # an assignment for a different entity is not this gap's
    other = gap.detect_gaps([_ctx(1, 1),
                             _fb(2, progress=[{"assignment": "obj_blue_9->tray_left",
                                               "from": "true", "to": "unknown"}]), _fb(3)])
    assert other[0].provenance["goal_assignment"] is None


def test_running_the_detector_twice_gives_the_same_rows_apart_from_their_ids():
    events = [_ablation(1, "full"), _ctx(2, 1), _fb(3), _fb(4), _ctx(5, 2), _refusal(6),
              _refusal(7)]
    first = gap.detect_gaps(events)
    second = gap.detect_gaps(events)
    strip = lambda rows: [r.model_dump(exclude={"gap_id", "created_at", "updated_at"})
                          for r in rows]
    assert strip(first) == strip(second)
    assert len({r.gap_id for r in first + second}) == len(first) + len(second)


# ------------------------------------------------------- 4. files, runs, census ----
def _write_episode(root, episode_id, events, summary=None):
    os.makedirs(os.path.join(root, "episodes", episode_id), exist_ok=True)
    with open(os.path.join(root, "episodes", episode_id, "events.jsonl"), "w",
              encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    with open(os.path.join(root, "episodes", episode_id, "episode_summary.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary or {"episode_id": episode_id,
                              "result": {"task_id": episode_id, "terminal_status": "failed"}}, fh)
    return root


def test_an_episode_directory_is_read_as_its_own(tmp_path):
    run = _write_episode(str(tmp_path / "run"), "case_a.B.r0",
                         [_ctx(1, 1), _fb(2), _fb(3)])
    gaps = gap.gaps_for_episode(os.path.join(run, "episodes", "case_a.B.r0"))
    assert [g.missing_because for g in gaps] == ["repeated_failure"]
    assert gaps[0].episode_id == "case_a.B.r0"


def test_a_directory_with_no_ledger_is_an_error_rather_than_an_empty_list(tmp_path):
    empty = tmp_path / "not_an_episode"
    empty.mkdir()
    with pytest.raises(FileNotFoundError) as exc:
        gap.gaps_for_episode(str(empty))
    assert str(empty) in str(exc.value) and "events.jsonl" in str(exc.value)
    # the distinction matters: [] would read as "this episode found no gap", which is a finding
    assert gap.gaps_for_run(_write_episode(str(tmp_path / "clean"), "case_b.B.r0",
                                           [_ctx(1, 1), _fb(2, status="completed")])) \
        == {"case_b.B.r0": []}


def test_a_run_directory_without_episodes_is_an_error(tmp_path):
    (tmp_path / "half_a_run").mkdir()
    with pytest.raises(FileNotFoundError) as exc:
        gap.gaps_for_run(str(tmp_path / "half_a_run"))
    assert "episodes/" in str(exc.value)


def test_the_census_counts_per_episode_directory_and_reports_its_own_arithmetic(tmp_path):
    runs = [_write_episode(str(tmp_path / "arm_full"), "lh_c4.B.r0",
                           [_ctx(1, 1), _fb(2), _fb(3), _refusal(4), _refusal(5)]),
            _write_episode(str(tmp_path / "arm_wo"), "lh_c4.B.r0",
                           [_ctx(1, 1), _fb(2), _ctx(3, 2), _fb(4)])]
    report = gap.detector_report(runs)
    assert report["detector"] == gap.DETECTOR_VERSION
    assert report["repeat_threshold"] == gap.REPEAT_THRESHOLD
    assert report["episodes_scanned"] == 2
    assert report["episodes_with_a_gap"] == 2          # one id, two directories
    assert report["episodes_with_a_gap_by_id"] == ["lh_c4.B.r0"]
    assert report["gaps"] == 3
    assert report["non_completion_cells"] == 3
    assert report["cells_not_reported"] == report["non_completion_cells"] - report["gaps"] == 0
    assert sum(report["not_reported_because"].values()) == report["cells_not_reported"]
    assert sum(report["by_missing_because"].values()) == report["gaps"]
    assert sum(report["by_rule"].values()) == report["gaps"]
    assert report["cells_by_primitive"] == ["pick"]
    assert set(report["by_missing_because"]) == {"no_skill", "precondition_unmet",
                                                 "repeated_failure", "no_termination", "other"}
    assert report["by_missing_because"]["precondition_unmet"] == 0
    assert report["gaps_already_covered_by_the_library"] == 0


def test_the_census_tells_an_absent_capability_from_a_failed_transfer(tmp_path):
    """Same ledger, same gap count, two different findings.

    A gap whose effect some other library entry declares is a transfer failure, and the fix is not
    "write a program" — so the census has to be able to say which of the two it is looking at rather
    than only how many gaps there were.
    """
    run = _write_episode(str(tmp_path / "arm"), "lh_c4.B.r0", [_ctx(1, 1), _fb(2), _fb(3)])
    bare = gap.detector_report([run])
    assert bare["gaps"] == 1 and bare["gaps_already_covered_by_the_library"] == 0
    covered = gap.detector_report([run], library=[_library_spec("grasp_after_clear_look",
                                                               ["grasp:{object}"])])
    assert covered["gaps"] == bare["gaps"]
    assert covered["gaps_already_covered_by_the_library"] == 1
    # the census is a count of the per-episode rows, not an independent second detector
    assert covered["gaps"] == sum(len(v) for v in gap.gaps_for_run(run).values())


def test_a_clean_batch_reports_zero_because_it_was_scanned(tmp_path):
    run = _write_episode(str(tmp_path / "clean_arm"), "em_p1.B.r0",
                         [_ctx(1, 1), _fb(2, status="completed"), _fb(3, status="finish_accepted")])
    report = gap.detector_report([run])
    assert report["episodes_scanned"] == 1
    assert report["gaps"] == 0 and report["non_completion_cells"] == 0
    assert report["episodes_with_a_gap"] == 0
