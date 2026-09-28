"""P1-d contract tests: the four rules a camera channel may be held to, and nothing else.

§5.8 separates three verification layers by their *information sources*, and P1-d puts a
camera in the middle one: the runtime layer now answers a postcondition from a
percept-derived snapshot (`perception/verify_percept.py`). What a test may therefore pin is
which claims that channel is allowed to make — never how often it succeeds. The rates are
measured by `work/p1_verify_check.py` and counted by §11 (P1-f); a threshold tuned to make a
test here pass would turn the one number that must stay honest into a fixture.

Four rules, in the order the module states them:

* **the seating-height comparison is declined, not passed** — the keys only that comparison
  produces never appear in a sensor report, while the privileged control over the same body
  at the same instant does produce them;
* **absence is `unknown`, never `false`** — a body the frame did not report, or reported
  without metres, gets no verdict about where it rests, and no number that could be read as
  one;
* **`false` needs contrary evidence** — a measured support surface that is not the target,
  named in the description and agreed with by the answer key;
* **occupancy is not placement** — a body blocking a tray is not a body placed in it, and the
  two questions are published as two fields with two answers.

Plus the legality of the word `uncertain` itself: which layer is allowed to say it, on what
evidence, and what the loop is still told by layer 1 after it has been said. Everything runs
on real rendered frames of `dev_c5` and, for the loop tests, one real actuation of the
pick skill — so a failure names a measurement.
"""
from __future__ import annotations

import types

import pybullet as p
import pytest

from embodied_agent.core.contracts import (
    Decision,
    DecisionContext,
    DecisionExecute,
    FailureCode,
    PredicateReport,
    PredicateVerdict,
    SkillCall,
    SkillStatus,
    Source,
    TaskInput,
)
from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import RuntimeVerifier, build_world_state
from embodied_agent.evaluation.calibration import cell_catalog, task_context_of
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.perception.frames import camera_for, capture
from embodied_agent.perception.observe import (
    PerceptionAssembler,
    ReadAbsent,
    ReadObject,
    StubReader,
    TaskContext,
    VlmReading,
)
from embodied_agent.perception.verify_percept import (
    DECLINED_FACTS,
    UNANSWERABLE,
    PerceptVerifier,
)
from embodied_agent.perception.world_state import world_state_from_percept

MAIN, OVERHEAD, FRONT_HIGH = "main", "overhead", "front_high"
VIEWS = (MAIN, OVERHEAD, FRONT_HIGH)
COLOURS = ("red", "green", "blue", "yellow", "purple")
TARGET = "tray_middle"
TABLE_BOX = (300, 200, 340, 240)
# above and left of the table, where `main`'s depth reads a surface at no height this cell
# declares: the estimator's own refusal is what leaves the body seen but without metres
SKY_BOX = (60, 100, 96, 132)
# the keys *only* the seating-height comparison produces. If any of them appears in a sensor
# report, the check that would confirm the snapshot's own upright assumption came back.
HEIGHT_KEYS = ("height_err_m", "rest_z", "rest_z_expected", "rest_z_measured", "rest_z_nominal")


def eid_of(scene, colour: str) -> str:
    return next(e for e, d in scene.objects.items()
                if str(d["attributes"]["color"]) == colour)


def seat(scene, entity_id: str, target_id: str, dx: float = 0.0, dy: float = 0.0,
         yaw: float = 0.05) -> str:
    """Put a body in a tray with the simulator's own hand, and return its colour.

    The privileged channel appears as the thing that *makes* a placement happen, never as
    the evidence a verdict is graded from — that is the §5.8 split this phase is about."""
    d = scene.objects[entity_id]
    tray = scene.trays[target_id]
    p.resetBasePositionAndOrientation(
        d["body"],
        [float(tray["center"][0]) + dx, float(tray["center"][1]) + dy,
         float(tray["floor_top"]) + float(d["geometry"].half_h_vertical)],
        list(p.getQuaternionFromEuler([0, 0, yaw], physicsClientId=scene.cid)),
        physicsClientId=scene.cid)
    for _ in range(40):
        p.stepSimulation(physicsClientId=scene.cid)
    scene.sim_time += 0.4
    return str(d["attributes"]["color"])


def upright_half_max(scene, entity_id: str) -> float:
    geom = scene.objects[entity_id]["geometry"]
    pair = ((geom.half_extents.x, geom.half_extents.y) if geom.half_extents is not None
            else (geom.radius, geom.radius))
    return max(float(v) for v in pair)


def reading_for(**colour_shape_box) -> VlmReading:
    """A sensor-channel reading from keyword colour → (shape, bbox) pairs."""
    rows = []
    for colour, (shape, bbox) in colour_shape_box.items():
        rows.append(ReadObject(ref=f"{colour} {shape}", color=colour, shape=shape,
                              bbox=bbox, confidence=0.9, occluded=False, on_or_in=""))
    return VlmReading(objects=rows, reader="unit-test", channel="sensor",
                      prompt_version="unit", catalog_sha256="", raw_text="hand-built")


def verifier_for(state, view: str = MAIN, config=None) -> PerceptVerifier:
    """The channel answering *for* one view, with the other two as further looks."""
    return PerceptVerifier(state, config, view=view,
                           alt_views=[v for v in VIEWS if v != view])


# --------------------------------------------------------------------- fixtures ----


#: The poses the case was built with, taken before any test can move a body. A module-level
#: row rather than a fixture body because the first test in this file already seats something:
#: a snapshot of "the table as it was" has to predate every mutation, not every request.
START: dict = {}


@pytest.fixture(scope="module")
def case():
    return find_case("dev_c5")


@pytest.fixture(scope="module")
def scene(case):
    s = PhysicsScene(seed=case.seed, object_layout=case.objects)
    START.update({eid: s.object_pose(eid) for eid in s.objects})
    yield s
    s.close()


@pytest.fixture(scope="module")
def catalog(scene):
    return cell_catalog(scene.trays.values())


@pytest.fixture(scope="module")
def task(case):
    return TaskContext(**task_context_of(case))


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("p1d_verify"))


@pytest.fixture(scope="module")
def start():
    return dict(START)


@pytest.fixture(scope="module")
def pixels(scene, catalog, task, out_dir):
    """One way to see: render a real frame, read it, and adopt the percept as a snapshot."""
    reader, assembler = StubReader(), PerceptionAssembler(catalog)

    def see(view: str = MAIN, name: str = "see", state_version: int = 0):
        frame = capture(scene, out_dir, camera_for(view), f"{name}.{view}",
                        sim_time=scene.sim_time)[0]
        percept = assembler.assemble(frame, reader.read(frame, catalog=catalog, task=task))
        return world_state_from_percept(percept, catalog, state_version=state_version)

    def see_reading(reading: VlmReading, view: str = MAIN, name: str = "hand",
                    state_version: int = 0):
        frame = capture(scene, out_dir, camera_for(view), f"{name}.{view}",
                        sim_time=scene.sim_time)[0]
        return world_state_from_percept(assembler.assemble(frame, reading), catalog,
                                        state_version=state_version)

    return types.SimpleNamespace(see=see, see_reading=see_reading)


@pytest.fixture()
def on_the_table(scene, start):
    """Every body back where the case put it, whatever an earlier test seated."""
    for eid, (pos, orn) in start.items():
        p.resetBasePositionAndOrientation(scene.objects[eid]["body"], list(pos), list(orn),
                                         physicsClientId=scene.cid)
    for _ in range(40):
        p.stepSimulation(physicsClientId=scene.cid)
    scene.sim_time += 0.4
    return scene


@pytest.fixture()
def loop(scene, case, on_the_table, tmp_path):
    """A real `Runtime` over the real scene, with no episode scripted.

    The seams are what is under test here, so nothing needs a policy: a `SkillResult` comes
    from `SkillExecutor.execute` and the feedback record is built by `_build_feedback`, both
    production objects."""
    executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
    runtime = Runtime(scene, executor, str(tmp_path), case.budgets, "p1d-loop",
                      config=case.verify,
                      environment=EnvironmentController(scene, case.fresh_events()),
                      store=EpisodeStore(str(tmp_path), "p1d-loop"))
    executor.world_provider = runtime.observe
    return runtime


# ------------------------------- 1. the declined comparison is never even computed ----


def test_the_seating_height_comparison_never_appears_in_a_sensor_report(scene, pixels, case):
    """The first refusal, pinned by its *only possible fingerprint*.

    `state_supported_on` compares a body's z against `seated_rest_z(..., entity.pose
    .quaternion)`, and for a percept entity both sides come from the same upright assumption
    P1-c recorded — so the difference is ~0 by construction. The way to refuse a check that
    cannot fail is to never run it, which is observable: the keys are absent."""
    red = eid_of(scene, "red")
    seat(scene, red, TARGET)
    state = pixels.see(MAIN, "seated")
    report = verifier_for(state, config=case.verify).verify_placement("seen:red", TARGET)
    # the certification is what makes the refusal mean something: this is a report that
    # *could* have carried a height number and deliberately does not
    assert report.value is PredicateVerdict.true, report.description
    assert not [k for k in HEIGHT_KEYS if k in report.evidence], \
        f"a sensor report computed {sorted(set(report.evidence) & set(HEIGHT_KEYS))}"
    assert set(report.unmeasured) >= set(DECLINED_FACTS)

    # ... and the same question about the same body, on the same table, at the same instant,
    # asked of the channel that measures orientation and height directly
    privileged = build_world_state(scene, 1, "obs_0001", case.verify)
    control = RuntimeVerifier(privileged, case.verify).verify_placement(red, TARGET)
    assert control.value is PredicateVerdict.true
    assert {k for k in HEIGHT_KEYS if k in control.evidence} == set(HEIGHT_KEYS)
    assert control.unmeasured == []


def test_a_camera_that_cannot_see_a_gripper_reports_no_contact_either(pixels, case,
                                                                     on_the_table):
    """`in_contact` is a claim about the actuators, so it stays out of a frame's answer."""
    state = pixels.see(MAIN, "grasp")
    report = verifier_for(state, config=case.verify).verify_grasp("seen:green", before=state)
    assert report.value is PredicateVerdict.unknown
    assert report.unmeasured == ["hold_state"]
    assert "in_contact" not in report.evidence and "held_object_is_entity" not in report.evidence


def test_every_sensor_placement_report_names_the_three_facts_a_frame_has_no_instrument_for(
        scene, pixels, case, on_the_table):
    """45 tray questions on an untouched table, plus a seated body seen from three views."""
    trays = list(scene.trays)
    rows = []
    red = eid_of(scene, "red")
    for view in VIEWS:
        state = pixels.see(view, "decline_sweep")
        verifier = verifier_for(state, view, case.verify)
        rows += [verifier.verify_placement(f"seen:{colour}", tray)
                 for colour in COLOURS for tray in trays]
    seat(scene, red, TARGET)
    for view in VIEWS:
        seated = pixels.see(view, "decline_seated")
        rows.append(verifier_for(seated, view, case.verify).verify_placement("seen:red", TARGET))
    assert len(rows) == 48
    for report in rows:
        assert set(report.unmeasured) >= set(DECLINED_FACTS), report.predicate_id
        # the same three names in the same report as any verdict, true or false alike
        assert report.source is Source.sensor


# ------------------------------------------------- 2. absence is unknown, never false ----


def test_a_body_this_frame_did_not_report_is_unknown_and_carries_no_number(pixels, case):
    reading = reading_for(red=("cube", TABLE_BOX))
    reading.absent.append(ReadAbsent(ref="绿色圆柱", because="not_in_image", confidence=0.5))
    state = pixels.see_reading(reading, MAIN, "absent")
    assert not state.has_entity("seen:green")
    report = verifier_for(state, config=case.verify).verify_placement("seen:green", TARGET)
    assert report.value is PredicateVerdict.unknown
    assert report.unmeasured[0] == "not_in_this_frame"
    # §5.1: an evidence field either holds a real measurement or does not appear, so `0.0`
    # can never be read here as "looked, and it was not there"
    assert report.evidence == {"measurable": 0.0}


def test_a_body_seen_without_metres_is_unknown_rather_than_outside(pixels, case):
    reading = reading_for(red=("cube", TABLE_BOX), green=("cuboid", SKY_BOX))
    state = pixels.see_reading(reading, MAIN, "unlocated")
    green = next(d for d in state.entities if d.entity_id == "seen:green")
    assert green.pose is None and green.visible is True
    report = verifier_for(state, config=case.verify).verify_placement("seen:green", TARGET)
    assert report.value is PredicateVerdict.unknown
    assert report.unmeasured[0] == "position_unmeasured"
    assert report.evidence == {"measurable": 0.0}
    # and the red body the same frame did locate is still answered: this is a per-body
    # refusal, not the snapshot being disabled
    assert verifier_for(state, config=case.verify).verify_placement(
        "seen:red", TARGET).value is not PredicateVerdict.unknown


# ------------------------------------- 3. `false` needs a measured contrary surface ----


def test_a_false_names_the_surface_that_was_measured_and_the_answer_key_agrees(
        scene, pixels, case, on_the_table):
    privileged = build_world_state(scene, 1, "obs_0001", case.verify)
    key = RuntimeVerifier(privileged, case.verify)
    state = pixels.see(MAIN, "negatives")
    verifier = verifier_for(state, config=case.verify)
    rows = [(colour, tray, verifier.verify_placement(f"seen:{colour}", tray))
            for colour in COLOURS for tray in scene.trays]
    assert len(rows) == 15
    for colour, tray, report in rows:
        assert report.value is PredicateVerdict.false, (colour, tray, report.description)
        assert report.evidence["relation_matches_target"] == 0.0
        assert report.unmeasured[0] == "relation_other_surface"
        # the description quotes the measurement that contradicts the claim, not an absence
        measured = str(state.entity(f"seen:{colour}").supported_by)
        assert measured in report.description and tray in report.description
        # the sound direction: the channel that reads physics says `false` too, so no
        # placement that has happened is being undone by a look
        assert key.verify_placement(eid_of(scene, colour), tray).value is PredicateVerdict.false


def test_a_placement_that_really_happened_is_certified_by_the_look_that_saw_it(
        scene, pixels, case):
    """The positive half of the same rule: `true` is reachable, and only with the relation
    measured. `main` and `overhead` read this body's clearance the same way; the numbers a
    view disagrees on are §11's to count, not a threshold's to hide."""
    red = eid_of(scene, "red")
    seat(scene, red, TARGET)
    for view in (MAIN, OVERHEAD):
        state = pixels.see(view, "placed")
        report = verifier_for(state, view, case.verify).verify_placement("seen:red", TARGET)
        assert report.value is PredicateVerdict.true, (view, report.description)
        assert report.evidence["relation_matches_target"] == 1.0
        assert report.evidence["wall_margin_m"] > 0
        # the other two trays stay false for a body the frame put on this one
        other = verifier_for(state, view, case.verify).verify_placement("seen:red", "tray_left")
        assert other.value is PredicateVerdict.false
        assert "tray_middle" in other.description


# ------------------------------------ 4. occupancy is not placement, and every `true`
# ------------------------------------ buys with a clearance it publishes ----


def test_a_true_claim_always_publishes_the_clearance_it_bet_on(scene, pixels, case,
                                                              on_the_table):
    """`wall_margin_m` is in the evidence so a `true` won on a thin margin is auditable.

    The rule is arithmetic, not a rate: `inside` is `|dx| + half_x <= inner_half - margin`,
    so a certified body cannot be crossing the wall, and a body the channel declined *because
    of the bound* cannot be clearing it either."""
    red = eid_of(scene, "red")
    rows = []
    state = pixels.see(MAIN, "margin_table")
    verifier = verifier_for(state, config=case.verify)
    rows += [verifier.verify_placement(f"seen:{c}", t)
             for c in COLOURS for t in scene.trays]
    for view in VIEWS:
        seated = pixels.see(view, "margin_seated")
        rows.append(verifier_for(seated, view, case.verify).verify_placement("seen:red", TARGET))
    dx = float(scene.trays[TARGET]["inner_half"]) - upright_half_max(scene, red) + 0.004
    seat(scene, red, TARGET, dx=dx)
    for view in VIEWS:
        at_wall = pixels.see(view, "margin_wall")
        rows.append(verifier_for(at_wall, view, case.verify).verify_placement("seen:red", TARGET))
    for report in rows:
        assert "wall_margin_m" in report.evidence, report.predicate_id
        if report.value is PredicateVerdict.true:
            assert report.evidence["wall_margin_m"] >= -1e-4, report.evidence
        elif report.evidence.get("relation_matches_target") == 1.0:
            # the decline is *about* the bound, and it cannot be true and over the bound
            assert report.value is PredicateVerdict.unknown
            assert report.evidence["wall_margin_m"] <= 0, report.evidence
    # the sweep has a declined row in it, or this test would be asserting nothing
    assert any(r.value is PredicateVerdict.unknown for r in rows)


def test_occupying_a_tray_and_being_placed_in_it_are_two_fields_with_two_answers(
        scene, pixels, case, on_the_table):
    """The row that made the first draft of `verify_percept` wrong, run on purpose.

    A body parked with its footprint 4 mm across the inner wall blocks the tray — occupancy
    is measured with the margin *relaxed* precisely so that stays true — and has not been
    put in it. The sensor channel must answer those two questions in two fields, and the
    placement one must follow the answer key rather than the blocking one."""
    red = eid_of(scene, "red")
    dx = float(scene.trays[TARGET]["inner_half"]) - upright_half_max(scene, red) + 0.004
    seat(scene, red, TARGET, dx=dx)
    privileged = build_world_state(scene, 1, "obs_0001", case.verify)
    assert RuntimeVerifier(privileged, case.verify).verify_placement(
        red, TARGET).value is PredicateVerdict.false, "the answer key must put this body outside"
    blocking = []
    for view in VIEWS:
        state = pixels.see(view, "wall")
        report = verifier_for(state, view, case.verify).verify_placement("seen:red", TARGET)
        occupies = report.evidence["occupies_target"]
        margin = report.evidence["wall_margin_m"]
        assert occupies is not None and margin is not None
        if occupies == 1.0 and margin < 0:
            # the two fields disagree, and the verdict follows the tightened bound: this is
            # the row the first draft graded on occupancy and got wrong
            blocking.append(view)
            assert report.value is not PredicateVerdict.true, \
                f"{view} certified a placement on a body its own margin puts over the wall"
            assert "inside_unproven_at_invariant_bound" in report.unmeasured
    assert blocking, ("no view still calls this body a blocker of the tray while placing it "
                      "outside, so the occupancy-vs-placement distinction this test exists for "
                      "has gone away and the test must be re-aimed, not left asserting nothing")


# -------------------------------------------------- 5. who is allowed to say `uncertain` ----


def _report(value: PredicateVerdict) -> PredicateReport:
    return PredicateReport(predicate_id="placed:seen:x:tray_middle", description="built by hand",
                           value=value, evidence={}, source=Source.sensor)


def _result(status: SkillStatus):
    return types.SimpleNamespace(status=status, failure_code=None)


CASES = [
    # (channel, layer-1 status, report values, expected override)
    ("sensor", SkillStatus.completed, [_report(PredicateVerdict.unknown)], SkillStatus.uncertain),
    ("sensor", SkillStatus.completed, [_report(PredicateVerdict.unknown)] * 3,
     SkillStatus.uncertain),
    ("sensor", SkillStatus.completed, [_report(PredicateVerdict.true),
                                       _report(PredicateVerdict.unknown)], None),
    ("sensor", SkillStatus.completed, [_report(PredicateVerdict.true)], None),
    ("sensor", SkillStatus.completed, [_report(PredicateVerdict.false)], None),
    ("sensor", SkillStatus.completed, [], None),
    ("sensor", SkillStatus.failed, [_report(PredicateVerdict.unknown)], None),
    ("sensor", SkillStatus.rejected, [_report(PredicateVerdict.unknown)], None),
    ("sensor", SkillStatus.timeout, [_report(PredicateVerdict.unknown)], None),
    ("sensor", SkillStatus.uncertain, [_report(PredicateVerdict.unknown)], None),
    ("privileged", SkillStatus.completed, [_report(PredicateVerdict.unknown)], None),
    ("privileged", SkillStatus.completed, [], None),
]


@pytest.mark.parametrize("channel, layer1, reports, expected", CASES,
                         ids=[f"{c}-{s.value}-{len(r)}" for c, s, r, _ in CASES])
def test_uncertain_is_produced_only_by_the_channel_that_measured_and_could_not_answer(
        scene, pixels, case, channel, layer1, reports, expected, on_the_table):
    """The whole legality of the new word, in one table.

    `uncertain` may only come from a layer that looked for the postcondition and could not
    answer it — never from the actuator's own report (layer 1, which says `completed`) and
    never from the privileged channel, whose snapshot measures the predicate directly. P0 §3
    kept the enum member alive for exactly this producer."""
    state = pixels.see(MAIN, "outcome")
    verifier = (verifier_for(state, config=case.verify) if channel == "sensor"
                else RuntimeVerifier(build_world_state(scene, 1, "obs_0001", case.verify),
                                     case.verify))
    assert verifier.outcome_status(_result(layer1), reports) is expected


def test_uncertain_is_not_reachable_without_reports_to_back_it(scene, pixels, case,
                                                              on_the_table):
    state = pixels.see(MAIN, "empty")
    verifier = verifier_for(state, config=case.verify)
    assert verifier.outcome_status(_result(SkillStatus.completed), []) is None
    assert verifier.outcome_status(None, [_report(PredicateVerdict.unknown)]) is None
    # the sealed vocabulary itself is unchanged: `uncertain` is the member P0 kept, not a
    # seventh status added to make room for this phase
    assert [s.value for s in SkillStatus] == ["completed", "failed", "timeout", "uncertain",
                                              "cancelled", "rejected"]


# ------------------------------------------------------- 6. the loop seam ----------


def test_the_loop_records_the_channel_s_word_without_editing_the_actuator_s(
        scene, pixels, case, loop):
    """One real pick: the actuators finished, the frame cannot say the object is held."""
    green = eid_of(scene, "green")
    pre = pixels.see(MAIN, "loop_pre", state_version=1)
    call = SkillCall(plan_id="p", step_id="s", skill="pick", args={"object_id": green})
    result = loop.executor.execute(call)
    assert result.status is SkillStatus.completed
    post = pixels.see(MAIN, "loop_post", state_version=2)
    verifier = verifier_for(post, config=case.verify)
    reports = [verifier.verify_grasp("seen:green", before=pre)]
    assert [r.value for r in reports] == [PredicateVerdict.unknown]
    assert reports[0].unmeasured == ["hold_state"]

    assert loop._outcome_status(result, reports, verifier) == (
        SkillStatus.uncertain, FailureCode.STATE_UNCERTAIN.value)
    # layer 1 survives the call: the seam records a reading, it does not rewrite a result
    assert result.status is SkillStatus.completed and result.failure_code is None

    privileged = RuntimeVerifier(build_world_state(scene, 2, "obs_0002", case.verify),
                                case.verify)
    assert loop._outcome_status(result, reports, privileged) == (SkillStatus.completed, None)

    class _TextVerifier:
        """What a text backend offers the loop: `progress` and `verify_goals`, nothing else."""

        def progress(self, goal):
            return []

    assert loop._outcome_status(result, reports, _TextVerifier()) == (SkillStatus.completed,
                                                                     None)


def test_a_feedback_record_from_a_sensor_round_says_uncertain_and_still_says_executed(
        scene, pixels, case, loop):
    """The seam inside `_build_feedback`, on a real record.

    What the loop is told changed; what it *did* did not. `executed` and the verification
    report still come from layer 1 and layer 2's own evidence, and the failure code is the
    one that means "the state is uncertain", not a fabricated actuator error."""
    green = eid_of(scene, "green")
    pre = pixels.see(MAIN, "fb_pre", state_version=1)
    result = loop.executor.execute(SkillCall(plan_id="p", step_id="s", skill="pick",
                                             args={"object_id": green}))
    assert result.status is SkillStatus.completed
    post = pixels.see(MAIN, "fb_post", state_version=2)
    task_in = TaskInput(task_id=case.task_id, utterance=case.utterance)
    goal = interpret(task_in, pre)
    ctx = DecisionContext(episode_id="p1d", state_version=pre.state_version, task=task_in,
                         goal=goal, world=pre, observation_ref=pre.observation_ref)
    # the same body, named the way the frame names it: a percept snapshot has no simulator
    # id in it (P1-c), so that is the only binding the sensor channel can be asked about
    seen_call = SkillCall(plan_id="p", step_id="s", skill="pick",
                          args={"object_id": "seen:green"}, call_id=result.call_id)
    decision = Decision(context_id=ctx.context_id, based_on_state_version=pre.state_version,
                        goal_ref=goal.goal_id, action="execute",
                        execute=DecisionExecute(skill="pick", args={"object_id": "seen:green"}))
    verifier = verifier_for(post, config=case.verify)
    # the seam P1-e's arm will override for real; here it is the instance attribute, which
    # is exactly what `_build_feedback` calls
    loop._verifier = lambda world: verifier
    fb = loop._build_feedback(decision, ctx, seen_call, result, pre, post, goal)
    assert (fb.status, fb.failure_code) == (SkillStatus.uncertain.value,
                                           FailureCode.STATE_UNCERTAIN.value)
    assert fb.executed is True, "the loop stopped recording that the action ran"
    assert fb.stages_executed == result.stages_executed
    assert fb.verification.overall is PredicateVerdict.unknown
    assert [r.source for r in fb.verification.reports] == [Source.sensor]
    assert fb.post_observation_ref == post.observation_ref and fb.new_measurement

    del loop._verifier
    control = loop._build_feedback(decision, ctx, seen_call, result, pre, post, goal)
    assert control.status == SkillStatus.completed.value and control.failure_code is None
    assert [r.source for r in control.verification.reports] == [Source.privileged]
    # the control is also the reason both seams move together: hand the default verifier a
    # frame and it labels the report privileged and trusts the claim. `_capture_world` and
    # `_verifier` are one switch, which is what P1-e's arm has to get right
    assert control.verification.reports[0].evidence.get("in_contact") is None


# ------------------------------------------- 7. provenance, and naming a better look ----


def test_each_channel_signs_its_own_reports(scene, pixels, case, on_the_table):
    state = pixels.see(MAIN, "source")
    sensor = verifier_for(state, config=case.verify).verify_placement("seen:red", TARGET)
    assert sensor.source is Source.sensor
    assert sensor.evidence_refs == [state.observation_ref] and state.observation_ref
    privileged_state = build_world_state(scene, 1, "obs_0001", case.verify)
    control = RuntimeVerifier(privileged_state, case.verify).verify_placement(
        eid_of(scene, "red"), TARGET)
    assert control.source is Source.privileged
    assert control.evidence_refs == ["obs_0001"]
    # `source` is a constructor argument now, so the base class answers for whichever channel
    # handed it a snapshot — and a report that says otherwise is the mistake §5.8 exists for
    assert RuntimeVerifier(privileged_state, case.verify, source=Source.sensor
                           ).verify_placement(eid_of(scene, "red"), TARGET).source is Source.sensor


def test_a_refusal_names_a_different_look_and_never_the_one_that_just_failed(pixels, case,
                                                                            on_the_table):
    state = pixels.see(MAIN, "resolve")
    verifier = verifier_for(state, MAIN, case.verify)
    for reason in UNANSWERABLE:
        assert verifier.would_resolve(reason) == [f"re_observe:{OVERHEAD}",
                                                 f"re_observe:{FRONT_HIGH}"]
        assert "re_observe:main" not in verifier.would_resolve(reason)
    for fact in DECLINED_FACTS:
        # no camera settles these; saying so is why the two lists are kept apart
        assert verifier.would_resolve(fact) == []
    # the asked-for view is filtered out at construction, not at the call site
    assert PerceptVerifier(state, case.verify, view=MAIN,
                           alt_views=(MAIN, OVERHEAD, None)).alt_views == [OVERHEAD]


def test_goal_progress_asked_of_a_frame_binds_refuses_and_signs_in_one_pass(scene, pixels, case,
                                                                          on_the_table):
    """§7 step 9's actual entry point: the loop asks a whole goal of one snapshot."""
    task_in = TaskInput(task_id=case.task_id, utterance=case.utterance)
    state = pixels.see(MAIN, "goals")
    goal = interpret(task_in, state)
    assert not goal.ambiguous and len(goal.assignments) == len(COLOURS)
    verifier = verifier_for(state, config=case.verify)
    report = verifier.verify_goals(goal)
    assert report.source is Source.sensor
    assert report.world_observation_ref == state.observation_ref
    assert report.state_version == state.state_version
    assert {r.source for r in report.reports} == {Source.sensor}
    assert all(set(r.unmeasured) >= set(DECLINED_FACTS) for r in report.reports)
    # every assignment was answerable from the frame, and none of them is a hold claim
    assert len(report.reports) == len(goal.assignments)
    assert report.overall is PredicateVerdict.false, "nothing has been placed on this table"
