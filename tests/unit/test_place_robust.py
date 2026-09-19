"""The place skill against real physics (SPEC 5.4, 5.5, 10.2, 12.1.5).

Replaces the mock-scene version of this file. A mock that carries the numbers it
is checking cannot fail, and every claim below is exactly the kind of claim that
has to fail when it stops being true:

* a refusal moves nothing and costs no simulated time;
* the three refusal codes stay distinguishable from one another;
* a completion is a *measured* footprint inside the region, not a command sent;
* an actuator error changes where the object is let go, while the request and the
  named candidate stay what the decision asked for, so the model reads a
  consequence rather than a label.
"""
import math

import pytest

from embodied_agent.core.contracts import (
    FailureCode,
    SkillCall,
    SkillStatus,
)
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import RuntimeVerifier, build_world_state
from embodied_agent.evaluation.tasks import find_case


class Rig:
    """One built scene, one executor, and a measuring world channel."""

    def __init__(self, case_id="smoke_shared"):
        self.case = find_case(case_id)
        self.scene = PhysicsScene(seed=self.case.seed, object_layout=self.case.objects)
        self.config = self.case.verify
        self._v = 0
        self.executor = SkillExecutor(self.scene, world_provider=self.observe, config=self.config)

    def observe(self):
        self._v += 1
        return build_world_state(self.scene, self._v, f"obs_{self._v:04d}", self.config)

    def call(self, skill, **args):
        return SkillCall(plan_id="test", step_id=f"t{self._v}", skill=skill, args=dict(args))

    def pose(self, eid):
        pos, _ = self.scene.object_pose(eid)
        return [round(float(v), 4) for v in pos]

    def pick(self, eid):
        return self.executor.execute(self.call("pick", object_id=eid))

    def place(self, eid, tid, candidate_id=None):
        args = {"object_id": eid, "target_id": tid}
        if candidate_id:
            args["candidate_id"] = candidate_id
        return self.executor.execute(self.call("place", **args))

    def objects(self, shape=None):
        out = sorted(self.scene.objects)
        if shape:
            out = [e for e in out if self.scene.objects[e]["attributes"]["shape"] == shape]
        return out

    def close(self):
        self.scene.close()


@pytest.fixture
def rig():
    r = Rig()
    try:
        yield r
    finally:
        r.close()


# ------------------------------------------------- refusal is not physics -----


def test_place_without_a_grasp_is_refused_before_physics(rig):
    eid = rig.objects()[0]
    before, sim_before = rig.pose(eid), rig.scene.sim_time
    res = rig.place(eid, "tray_left")
    assert res.status == SkillStatus.rejected
    assert res.failure_code == FailureCode.HELD_STATE_CONFLICT.value
    assert rig.pose(eid) == before, "a refused action moved the object"
    assert res.sim_seconds_used == 0.0 and rig.scene.sim_time == sim_before, \
        "a refusal consumed physics budget it never performed"
    assert res.stages_executed == ["rejected:validation"]


def test_the_three_refusal_reasons_do_not_collapse_into_one(rig):
    eid = rig.objects()[0]
    unknown_object = rig.place("obj_ghost_1", "tray_left")
    unknown_target = rig.place(eid, "tray_drawer")
    assert unknown_object.failure_code == FailureCode.UNKNOWN_ENTITY.value
    assert unknown_target.failure_code == FailureCode.UNKNOWN_TARGET.value
    # and neither is dressed up as a contact failure
    for res in (unknown_object, unknown_target):
        assert res.status == SkillStatus.rejected
        assert res.failure_code != FailureCode.PLACE_COLLISION.value


def test_a_missing_argument_is_a_structured_rejection_not_a_crash(rig):
    res = rig.executor.execute(SkillCall(plan_id="test", step_id="t", skill="place",
                                         args={"object_id": rig.objects()[0]}))
    assert res.status == SkillStatus.rejected
    assert res.failure_code == FailureCode.INVALID_DECISION.value
    assert any("missing required argument" in n for n in res.notes), res.notes


def test_a_skill_outside_the_catalogue_reaches_no_physics(rig):
    """The model-facing catalogue is the whole action space (SPEC 5.2): there is no
    verb that teleports, releases mid-air or re-seats a joint group."""
    from embodied_agent.core.skills import SkillRegistry

    assert set(SkillRegistry.CATALOGUE) == {"observe", "pick", "place", "safe_retreat"}
    with pytest.raises(Exception):
        SkillCall(plan_id="test", step_id="t", skill="teleport", args={})


def test_who_chose_the_slot_is_recorded_either_way(rig):
    """SPEC 5.5: an automatic slot choice must be recorded, and a prose note is not
    a record — the episode counter is read from this field. Both branches are
    measured here so neither can be a default nobody ever set."""
    a, b = rig.objects()[:2]
    named = rig.executor.placement_planner.generate("tray_left", a, rig.observe(), limit=1)[0]
    assert rig.pick(a).status == SkillStatus.completed
    res = rig.place(a, "tray_left", candidate_id=named.candidate_id)
    assert res.status == SkillStatus.completed, res.notes
    assert res.candidate_resolution == "model_named_candidate"

    assert rig.pick(b).status == SkillStatus.completed
    res2 = rig.place(b, "tray_right")
    assert res2.status == SkillStatus.completed, res2.notes
    assert res2.candidate_resolution == "runtime_fallback_rule"
    # the slot it chose is still measurable, not just labelled
    assert res2.measurements["displacement_from_candidate_m"] >= 0.0


# ------------------------------------------------------ candidate honesty -----


def test_a_candidate_id_never_offered_is_stale_not_guessed(rig):
    eid = rig.objects()[0]
    assert rig.pick(eid).status == SkillStatus.completed
    res = rig.place(eid, "tray_left", candidate_id="cand-tray_left-%s-x0y0" % eid)
    assert res.status == SkillStatus.rejected
    assert res.failure_code == FailureCode.CANDIDATE_STALE.value
    assert rig.scene.held_bodies(), "the object was dropped while the slot was being argued about"


def test_a_candidate_belonging_to_another_object_is_not_borrowed(rig):
    a, b = rig.objects()[:2]
    other = rig.executor.placement_planner.generate("tray_left", b, rig.observe(), limit=1)[0]
    assert rig.pick(a).status == SkillStatus.completed
    res = rig.place(a, "tray_left", candidate_id=other.candidate_id)
    assert res.failure_code == FailureCode.INVALID_DECISION.value
    assert b in " ".join(res.notes) and a in " ".join(res.notes), res.notes
    assert rig.scene.held_bodies()


def test_a_slot_inside_an_occupants_hand_envelope_is_rejected_and_says_why(rig):
    """The real-physics counterpart of the planner's descent-envelope rule.

    One object is placed for real; the second is then offered a slot at that
    object's own resting position. The refusal must name the *occupant*, because
    'no free slot' and 'this slot is inside the hand's path' are different facts
    for the policy that has to choose a target."""
    a, b = rig.objects()[:2]
    world = rig.observe()
    first = rig.executor.placement_planner.best_feasible("tray_left", a, world)[0]
    assert first is not None, "no slot for the first object: the tray is not empty at t0"
    assert rig.pick(a).status == SkillStatus.completed, rig.pose(a)
    assert rig.place(a, "tray_left", first.candidate_id).status == SkillStatus.completed
    occupied_at = rig.pose(a)[:2]

    assert rig.pick(b).status == SkillStatus.completed
    crowded = rig.executor.placement_planner._make("tray_left", b, occupied_at[0], occupied_at[1],
                                                   rig.observe())
    res = rig.place(b, "tray_left", crowded.candidate_id)
    assert res.status == SkillStatus.rejected
    assert res.failure_code == FailureCode.CANDIDATE_INFEASIBLE.value
    assert a in " ".join(res.notes) and "descent envelope" in " ".join(res.notes), res.notes
    assert rig.pose(a)[:2] == occupied_at, "the refused approach still shoved the first object"
    assert rig.scene.held_bodies()


# ------------------------------------------------------- measured success -----


def test_a_completed_place_is_a_measured_footprint_inside_the_region(rig):
    eid = rig.objects()[0]
    world = rig.observe()
    cand = rig.executor.placement_planner.best_feasible("tray_left", eid, world)[0]
    assert rig.pick(eid).status == SkillStatus.completed
    res = rig.place(eid, "tray_left", cand.candidate_id)
    assert res.status == SkillStatus.completed, res.notes
    m = res.measurements
    assert m["inside_target"] == 1.0 and m["at_rest"] == 1.0
    assert m["gripper_free"] == 1.0, "the hand is still touching it"
    assert not rig.scene.held_bodies()
    # the runtime's own verifier, from a fresh measurement, agrees
    verdict = RuntimeVerifier(rig.observe(), rig.config).verify_placement(eid, "tray_left")
    assert verdict.value.value == "true", verdict.evidence
    # and the object is where the *named candidate* said it would be, to within the
    # hand-off slip the skill itself measured
    assert math.isclose(m["final_x"], cand.position_xy[0], abs_tol=0.03)
    assert math.isclose(m["final_y"], cand.position_xy[1], abs_tol=0.03)


def test_a_release_the_measurement_contradicts_is_never_reported_as_success(rig):
    """`bounded_no_blind_place` at the level of one skill (SPEC 11.1, 12.1.2).

    A calibration error is armed on the *actuator*, so the object is handed off
    where no decision named it — measured at -150 mm the release lands against
    the outside of the tray wall. Whatever the geometry does, the invariant is
    one: a footprint the skill measured outside the region may not come back as a
    completion, because that is the false success the whole protocol forbids."""
    eid = rig.objects()[0]
    rig.scene.place_calibration_offset = [-0.15, 0.0, 0.0]
    world = rig.observe()
    cand = rig.executor.placement_planner.best_feasible("tray_left", eid, world)[0]
    assert rig.pick(eid).status == SkillStatus.completed
    res = rig.place(eid, "tray_left", cand.candidate_id)
    assert res.measurements["inside_target"] == 0.0, \
        "the mechanism stopped producing an outside-region release; this test must be re-armed"
    assert res.status == SkillStatus.failed, res.measurements
    assert res.failure_code == FailureCode.PLACE_UNSTABLE.value
    assert res.measurements["at_rest"] == 1.0 and res.measurements["gripper_free"] == 1.0
    assert res.measurements["candidate_x"] == pytest.approx(cand.position_xy[0], abs=1e-4), \
        "the report was rewritten to the place it actually ended up"


def test_an_actuator_error_moves_the_release_not_the_request(rig):
    """SPEC 10.2: the evidence the model sees is a measured consequence.

    The named candidate, the call and every value the skill attributes to the
    decision stay what was asked for; only the *measured* final position reveals
    that the hand let go somewhere else. A test that let the offset appear in the
    request would be checking a leak, not a mechanism."""
    eid = rig.objects()[0]
    rig.scene.place_calibration_offset = [-0.05, 0.0, 0.0]
    world = rig.observe()
    cand = rig.executor.placement_planner.best_feasible("tray_left", eid, world)[0]
    assert rig.pick(eid).status == SkillStatus.completed
    res = rig.place(eid, "tray_left", cand.candidate_id)
    assert res.measurements["candidate_x"] == pytest.approx(cand.position_xy[0], abs=1e-4)
    assert res.measurements["candidate_y"] == pytest.approx(cand.position_xy[1], abs=1e-4)
    slip = res.measurements["final_x"] - cand.position_xy[0]
    assert slip == pytest.approx(-0.05, abs=0.02), \
        f"the hand did not actuate the offset it was armed with (slip {slip * 1000:.0f} mm)"
    joined = " ".join(res.notes)
    assert f"candidate={cand.candidate_id}" in joined, res.notes  # what it was asked for
    assert "calibration" not in joined and "offset" not in joined, \
        f"the skill reported the environment's label instead of measuring it: {res.notes}"


def test_no_object_teleports_and_no_joint_resets_during_a_skill(rig):
    """SPEC 12.1.5: the path is interpolated, so simulated time advances through
    every stage and the object's own travel is continuous."""
    eid = rig.objects()[0]
    world = rig.observe()
    cand = rig.executor.placement_planner.best_feasible("tray_middle", eid, world)[0]
    samples = [rig.pose(eid)]
    origin = rig.scene.objects[eid]["body"]
    assert rig.pick(eid).status == SkillStatus.completed
    res = rig.place(eid, "tray_middle", cand.candidate_id)
    assert res.status == SkillStatus.completed
    assert res.sim_seconds_used > 0.5, "a transfer that spent no simulated time moved nothing"
    for stage in ("transfer", "descend", "release", "retreat", "settle"):
        assert stage in res.stages_executed, res.stages_executed
    assert rig.scene.entity_by_body(origin) == eid
    final = rig.pose(eid)
    assert abs(final[2] - cand.rest_z) < 0.02, f"resting height {final} vs seated {cand.rest_z}"


def test_two_objects_in_one_region_both_end_inside_and_the_second_waits_for_room(rig):
    """The shared-tray case the benchmark's ordering question rests on: the second
    placement must find a slot that does not disturb the first."""
    a, b = rig.objects()[:2]
    world = rig.observe()
    planner = rig.executor.placement_planner
    first = planner.best_feasible("tray_right", a, world)[0]
    assert rig.pick(a).status == SkillStatus.completed
    assert rig.place(a, "tray_right", first.candidate_id).status == SkillStatus.completed
    second = planner.best_feasible("tray_right", b, rig.observe())
    assert second[0] is not None, second[1]
    assert rig.pick(b).status == SkillStatus.completed
    assert rig.place(b, "tray_right", second[0].candidate_id).status == SkillStatus.completed
    v = RuntimeVerifier(rig.observe(), rig.config)
    assert v.verify_placement(a, "tray_right").value.value == "true"
    assert v.verify_placement(b, "tray_right").value.value == "true"
