"""The closed loop, end to end, and the judge that must not agree with it.

Two things are owned here and nowhere else:

* **the loop closes on the production path** (`evaluation.run`'s own pieces, a rule
  decision source): every frozen smoke case runs Observation -> WorldState ->
  DecisionContext -> Decision -> Skill -> Feedback -> terminal check, and what is
  asserted is the *ledger* of that loop — one feedback per executed skill, each
  one pointing at observation refs that were really taken, with the state version
  stepped (SPEC 5.1, 6, 12.1.1).
* **the independent evaluator is not self-confirming** (SPEC 12.4): starting from
  one real terminal snapshot of one real success, each adversarial variant — the
  wrong target in the truth, an object still in the gripper, a footprint that
  crosses the region edge while its centre does not, a placement knocked away
  afterwards, an object still moving — must be refused, and the unedited snapshot
  must still pass. Without that control the test would only prove that the
  evaluator fails on anything.
"""
from __future__ import annotations

import copy

import pytest

from embodied_agent.core.contracts import EvalSpec, TaskInput, TerminalStatus
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation.evaluator import IndependentEvaluator
from embodied_agent.evaluation.tasks import build_set, find_case

SMOKE = [c.task_id for c in build_set("smoke")]
ACTIVE = [c for c in SMOKE if c != "smoke_clarify"]


# ------------------------------------------------------- the loop closes ----


@pytest.mark.parametrize("case_id", SMOKE)
def test_every_smoke_case_reaches_its_declared_terminal_state(run_case, case_id):
    case = find_case(case_id)
    ep = run_case(case_id)
    assert ep.source_kind == "rule", "a script measures the harness, not the loop"
    assert ep.terminal_status == case.expected, (ep.terminal_status, case.expected)
    assert ep.probe["met"] is True, ep.probe
    assert ep.result.decision_rounds <= case.budgets.max_decision_rounds
    assert ep.result.skill_calls <= case.budgets.max_skill_calls
    if case.expected == "success":
        assert ep.score["complete_success"] is True, ep.score
        assert ep.result.failure_type is None
        assert ep.result.rejected_decisions == 0
        assert ep.result.decision_rounds >= 1
    else:
        assert not ep.payloads("skill_call"), "a question is not an action"
        assert ep.score["complete_success"] is False
        # the goal was unresolvable before any decision was asked for, so no
        # policy — good or bad — got the chance to act on a guessed binding
        assert ep.result.decision_rounds == 0, ep.result
        assert ep.payloads("needs_clarification"), ep.payloads("decision")


@pytest.mark.parametrize("case_id", ACTIVE)
def test_the_feedback_ledger_records_what_actually_happened(run_case, case_id):
    """One feedback per executed skill, and every ref in it is a measurement."""
    ep = run_case(case_id)
    calls = ep.payloads("skill_call")
    # the terminal `finish` also reports itself through the same channel, without
    # a call behind it; the pairing below is only about executed skills
    fbs = [fb for fb in ep.feedbacks() if fb.get("call_id")]
    unpaired = [fb for fb in ep.feedbacks() if not fb.get("call_id")]
    assert calls and len(fbs) == len(calls), (len(calls), len(fbs))
    assert all(fb["executed"] is False for fb in unpaired), unpaired
    assert [fb["call_id"] for fb in fbs] == [c["call"]["call_id"] for c in calls]
    observed = {o["observation"]["observation_ref"]: o["observation"]["state_version"]
                for o in ep.payloads("observation")}
    for call, fb in zip(calls, fbs):
        c = call["call"]
        assert fb["skill"] == c["skill"]
        assert fb["executed"] is True and fb["status"] == "completed", fb
        assert fb["pre_observation_ref"] in observed and fb["post_observation_ref"] in observed
        # a ref and a version in the same record must name one measurement, or the
        # trace from claim back to evidence is broken (SPEC 5.1)
        assert observed[fb["pre_observation_ref"]] == fb["pre_state_version"]
        assert observed[fb["post_observation_ref"]] == fb["post_state_version"]
        assert fb["post_state_version"] > fb["pre_state_version"], fb
        assert fb["new_measurement"] is True
        # a claim of completion is a measured claim, with the numbers attached
        assert fb["verification"]["reports"] and fb["measurements"], fb
        if c["skill"] == "place":
            assert fb["state_diff"], "a placement changed nothing it could be seen to change"
            assert fb["target_id"] == c["args"]["target_id"]
            # this policy never names a slot, so the shared execution-time rule
            # resolved one — that must be recorded, not silently applied (SPEC 5.5),
            # and the slot it chose is in the measurements the model sees
            assert fb["candidate_id"] is None
            assert fb["candidate_resolution"] == "runtime_fallback_rule", fb
            assert fb["measurements"]["displacement_from_candidate_m"] >= 0.0
    assert ep.result.slot_resolution_fallbacks == sum(
        1 for c in calls if c["call"]["skill"] == "place"), "every un-named slot counted once"
    assert ep.result.skill_calls == len(calls)


def test_a_finished_episode_is_judged_from_one_frozen_sample(run_case):
    """SPEC 7: the settle protocol runs once, and both judges read that sample."""
    ep = run_case("smoke_clean")
    snapshot = ep.runtime.terminal_snapshot
    assert snapshot is not None
    ends = ep.payloads("episode_end")
    assert len(ends) == 1, "an episode may not conclude twice"
    assert ends[0]["result"]["terminal_status"] == "success"
    finish = ep.payloads("finish_check")[-1]
    assert finish["accepted"] is True
    assert {r["value"] for r in finish["reports"]} == {"true"}, finish["reports"]
    assert ep.score["scored_observation_ref"] == ep.runtime.terminal_world.observation_ref
    assert snapshot.settle_seconds > 0
    # re-scoring the same sample is idempotent: judging cannot go looking for a
    # more favourable instant
    again = IndependentEvaluator(snapshot, ep.case.eval_spec).score(ep.result)
    assert again["details"] == ep.score["details"]


# ---------------------------- the SPEC 10.1 disturbance, as measured ------


def test_the_declared_impulse_undoes_the_placement_it_names_and_no_other(run_case, recorder):
    """The main demo of SPEC 10.1 read off one real episode, not off the event config.

    Five separate claims, because the case's `expected: success` depends on all of
    them and a freeze must not rest on a mechanism assumed to work:

    1. the declared event fires exactly once, on the body the case names;
    2. the skill's own measurement still says the release landed inside (SPEC 10.3
       forbids tampering with `SkillResult`), while the loop's independent
       verification of the post-event snapshot says the predicate is false — the
       two records describe two snapshots, and the feedback carries both;
    3. the other handoff into the same region keeps its completed result;
    4. the object is still graspable where the impulse left it, so a policy that
       re-picks it wins — which is what makes `success` an honest expectation;
    5. the limit of this mechanism: because the impulse lands in the same round as
       the release, the victim's relation is never *observed* true. These cases
       therefore measure "the placement you just executed is verified false", not
       "a completed relation was later undone" — that fork is carried by the
       `displace_second` cases (`sc_c3`/`dev_c2`, pair `sp03`/`sp04`), where the
       flip is seen. Recorded here so nobody reads the two as one measurement.
    """
    victim, other = "obj_purple_2", "obj_yellow_1"
    ep = run_case("smoke_state", source_factory=recorder().wrap)

    inj = ep.payloads("environment_event")
    assert len(inj) == 1, inj
    assert inj[0]["phase"] == "after_skill"
    record = inj[0]["injection"]
    assert (record["action"], record["entity_id"]) == ("impulse", victim)
    assert record["displacement_m"] > 0.02, record
    assert record["after_xy"][0] < record["before_xy"][0], "shoved toward the wall"

    places = [f for f in ep.feedbacks() if f["skill"] == "place" and f["entity_id"] == victim]
    assert len(places) == 2, "the re-place is the policy's decision, not a second record"
    first = places[0]
    assert first["status"] == "completed"
    assert first["measurements"]["inside_target"] == 1.0, \
        "the skill's own reading was edited to agree with the event"
    reports = first["verification"]["reports"]
    assert [(r["predicate_id"], r["value"]) for r in reports] == \
        [(f"placed:{victim}:tray_left", "false")], reports
    evidence = reports[0]["evidence"]
    assert evidence["xy_err_x"] + evidence["footprint_half_x"] > evidence["allow_x"], evidence

    def read(rounds):
        return [{p.entity_id: str(p.value.value) for p in c.progress} for c in rounds]

    seq = read(ep.contexts)
    event_round = next(c.round_index for c in ep.contexts if c.context_id == first["context_id"])
    later = [s for c, s in zip(ep.contexts, seq) if c.round_index > event_round]
    assert later and later[0][victim] == "false", \
        "the event's effect was not in the observation of the next round"
    assert all(s[other] == "true" for s in later), \
        "SPEC 10.1: an untouched completion must survive the disturbance"
    values = [s[victim] for s in seq]
    transitions = [(a, b) for a, b in zip(values, values[1:]) if a != b]
    assert transitions == [("false", "true")], (
        f"claim 5: the victim was never observed satisfied before it was knocked out, so "
        f"this episode shows one false->true and no true->false flip: {transitions}")

    event_at = next(i for i, e in enumerate(ep.events) if e["type"] == "environment_event")
    repicked = [e for e in ep.events[event_at:]
                if e["type"] == "skill_call" and e["payload"]["call"]["skill"] == "pick"
                and e["payload"]["call"]["args"].get("object_id") == victim]
    assert len(repicked) == 1, "the Runtime must not add the recovery pick itself"
    assert ep.terminal_status == "success" and ep.score["complete_success"] is True


# ----------------------------------------------- language to bindings ------


def test_interpreter_binds_attributes_and_never_a_position_guess(world_for):
    _, world = world_for("smoke_clean")
    case = find_case("smoke_clean")
    bind = RuntimeVerifier(world, case.verify).bind
    goal = interpret(TaskInput(task_id="t",
                               utterance="把绿色的方块放到左边托盘，把蓝色的长方体放到右边托盘。"),
                     world)
    assert goal.well_formed and not goal.ambiguous, goal.clarification_request
    assert {bind(a): a.target_id for a in goal.assignments} == {
        "obj_green_1": "tray_left", "obj_blue_2": "tray_right"}
    # every reference is an attribute claim, and the entity it resolves to really
    # has those attributes in this snapshot — not "the leftmost object on the table"
    assert all(a.entity.attributes for a in goal.assignments)
    for a in goal.assignments:
        measured = world.entity(bind(a)).attributes
        assert measured == a.entity.attributes, (measured, a.entity.attributes)


def test_interpreter_rest_clause_covers_every_remaining_object(world_for):
    _, world = world_for("smoke_clean")
    case = find_case("smoke_clean")
    goal = interpret(TaskInput(task_id="t", utterance="把绿色的方块放到左边托盘，其余放到中间托盘。"),
                     world)
    assert goal.well_formed, goal.clarification_request
    # a binding may be by attribute rather than by id, so resolve it the way the
    # verifier does before comparing
    bind = RuntimeVerifier(world, case.verify).bind
    targets = {bind(a): a.target_id for a in goal.assignments}
    assert targets == {"obj_green_1": "tray_left", "obj_blue_2": "tray_middle",
                       "obj_yellow_3": "tray_middle"}
    assert next(a for a in goal.assignments if bind(a) == "obj_green_1").entity.attributes


def test_interpreter_leaves_an_unresolvable_reference_as_a_question(world_for):
    _, world = world_for("smoke_clean")
    for utterance in ("把那个东西收好。", "把红色的方块放到抽屉里。",
                      "把绿色的方块叠在蓝色的长方体上面。", "把 obj_ghost_1 放到左边托盘。"):
        goal = interpret(TaskInput(task_id="t", utterance=utterance), world)
        assert not goal.well_formed, (utterance, goal.assignments)
        assert goal.clarification_request, utterance


# --------------------------------------- the judge must not be bought ------


@pytest.fixture
def scored(run_case):
    """A real terminal snapshot of a real success, with its hidden truth."""
    ep = run_case("smoke_clean")
    assert ep.score["complete_success"] is True, ep.score
    return ep.runtime.terminal_snapshot, ep.case.eval_spec, ep.result


def _score(snapshot, spec, episode=None):
    return IndependentEvaluator(snapshot, spec).score(episode)


def test_the_control_snapshot_scores_success(scored):
    snapshot, spec, result = scored
    out = _score(snapshot, spec, result)
    assert out["complete_success"] is True and out["protocol_ok"] is True
    assert out["objects_completed"] == out["objects_total"] == len(spec.assignments)
    assert all(d["satisfied"] for d in out["details"].values())


def test_a_mis_binding_that_executed_perfectly_scores_zero(scored):
    """The agent's own view is not an input: re-point the hidden truth and the
    score follows the truth, not the episode."""
    snapshot, spec, result = scored
    first = spec.assignments[0]
    other = next(t for t in snapshot.targets if t != first.target_id)
    wrong = spec.model_copy(update={"assignments": [
        type(first)(entity_id=first.entity_id, target_id=other), *spec.assignments[1:]]})
    out = _score(snapshot, wrong, result)
    assert out["goals_complete_success"] is False
    assert out["complete_success"] is False
    detail = out["details"][first.entity_id]
    assert detail["satisfied"] is False
    # the target it reports is the truth's, not the one the agent was told
    assert detail["target"] == other


def test_still_held_and_absent_entities_are_not_placed(scored):
    snapshot, spec, _ = scored
    eid = spec.assignments[0].entity_id
    held = snapshot.model_copy(deep=True)
    held.entities[eid]["in_gripper"] = 1.0
    out = _score(held, spec)
    assert out["details"][eid]["satisfied"] is False
    assert out["details"][eid]["evidence"]["in_gripper"] == 1.0

    missing = snapshot.model_copy(deep=True)
    del missing.entities[eid]
    out = _score(missing, spec)
    assert out["details"][eid]["satisfied"] is False
    assert "absent" in out["details"][eid]["evidence"]["reason"]

    no_region = snapshot.model_copy(deep=True)
    del no_region.targets[spec.assignments[0].target_id]
    assert _score(no_region, spec)["complete_success"] is False


def test_centre_inside_but_footprint_crossing_is_refused(scored):
    """`inside` is a whole-footprint claim. Sit the footprint exactly on the
    clearance limit and then one centimetre past it: the first still counts, the
    second must not — otherwise the boundary is never exercised and a helper bug
    would let both variants pass."""
    snapshot, spec, _ = scored
    a = spec.assignments[0]
    ent, region = snapshot.entities[a.entity_id], snapshot.targets[a.target_id]
    limit = region["inner_half"] - spec.footprint_margin_m - ent["footprint_half_xy"][0]
    assert limit > 0.02, "the tray is too small for this object to exercise a boundary"

    at_edge = copy.deepcopy(snapshot)
    _move(at_edge, a.entity_id, region["center"][0] - limit + 1e-4)
    ok = _score(at_edge, spec)["details"][a.entity_id]
    assert ok["satisfied"] is True, ok["evidence"]
    assert ok["evidence"]["xy_err_x"] == pytest.approx(limit, abs=1e-3)

    past = copy.deepcopy(snapshot)
    _move(past, a.entity_id, region["center"][0] - limit - 1e-2)
    out = _score(past, spec)["details"][a.entity_id]
    assert out["satisfied"] is False
    assert out["evidence"]["xy_err_x"] > out["evidence"]["allow_x"] - \
        out["evidence"]["footprint_half_x"], out["evidence"]


def _move(snapshot, eid, x):
    pos = snapshot.entities[eid]["pos"]
    pos[0] = x


def test_a_placement_knocked_away_afterwards_is_not_a_success(scored):
    """The dev set's `displace_placed` mechanism is exactly this: an object the
    environment pushed out after a verified placement must not be counted."""
    snapshot, spec, _ = scored
    a = spec.assignments[0]
    moved = copy.deepcopy(snapshot)
    moved.entities[a.entity_id]["pos"] = [0.40, moved.entities[a.entity_id]["pos"][1],
                                          moved.entities[a.entity_id]["pos"][2]]
    moved.entities[a.entity_id]["contacts"] = ["table_surface"]
    out = _score(moved, spec)["details"][a.entity_id]
    assert out["satisfied"] is False
    assert out["evidence"]["tray_contacts"] == 0.0

    sinking = copy.deepcopy(snapshot)
    sinking.entities[a.entity_id]["lin_speed"] = spec.speed_tol_mps * 3
    assert _score(sinking, spec)["details"][a.entity_id]["satisfied"] is False, \
        "a moving object was counted as at rest"

    tipped = copy.deepcopy(snapshot)
    tipped.entities[a.entity_id]["pos"][2] += spec.support_height_tol_m * 3
    assert _score(tipped, spec)["details"][a.entity_id]["satisfied"] is False, \
        "an object floating above the tray floor was counted as supported"


def test_geometry_and_protocol_are_separate_verdicts(scored):
    """A run that ended badly cannot be scored on geometry alone, and the geometry
    is still reported so the two failures can be told apart later."""
    snapshot, spec, result = scored
    intervened = _score(snapshot, spec, result.model_copy(update={"human_intervention": True}))
    assert intervened["goals_complete_success"] is True
    assert intervened["protocol_ok"] is False and intervened["complete_success"] is False
    assert "human intervention occurred" in intervened["protocol_notes"]

    failed = _score(snapshot, spec, result.model_copy(update={"terminal_status":
                                                              TerminalStatus.failed}))
    assert failed["goals_complete_success"] is True and failed["complete_success"] is False
    assert failed["protocol_notes"][0].startswith("terminal_status=failed")

    unknown_mode = _score(snapshot, spec, result.model_copy(update={"mode": "D"}))
    assert unknown_mode["protocol_ok"] is False
    assert "mode D is not a scored mode" in unknown_mode["protocol_notes"]

    # and no episode at all is still a geometry report, not an implicit pass
    assert _score(snapshot, spec)["protocol_ok"] is True


def test_an_empty_truth_is_not_a_perfect_score(scored):
    snapshot, _, result = scored
    empty = EvalSpec(eval_id="empty", assignments=[])
    out = _score(snapshot, empty, result)
    assert out["objects_total"] == 0
    assert out["goals_complete_success"] is False, "success by universal quantification"
    assert out["complete_success"] is False
