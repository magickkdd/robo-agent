"""P1-c contract tests: a `WorldState` that a picture is allowed to assert (§5.1, §7-1).

§6 keeps v0.1's `WorldState` and §7 step 1 asks a percept to update it, so the question
every case here answers is the same one: **does this snapshot claim anything the frame
did not measure?** Three families, in that order:

* provenance and honesty — the channel label, the fields a camera cannot answer staying
  `unknown`, and the upright quaternion a back-projection has to assume being marked as
  an assumption where the geometry code will actually honour it;
* grounding — the words a task uses must bind to entities this snapshot names, and a
  body the frame says it did *not* see must not be bindable, or a plan gets an object to
  pick up that the picture denies;
* change — two consecutive snapshots and the percept's own `changes` must say the same
  thing, in both directions, and a viewpoint switch must not be allowed to hide a
  placement that happened in between.

Everything runs on real rendered frames from `dev_c5` through `StubReader` and
`PerceptionAssembler`, so a failure names a measurement and not a fixture. The simulator
appears twice only: as the thing a claim is graded against, and as the thing that moves a
body when a test needs a change to have really happened.
"""
from __future__ import annotations

import ast
import math

import pybullet as p
import pytest

from embodied_agent.core.contracts import (
    DecisionContext,
    EntityState,
    GoalSpec,
    Source,
    TaskInput,
    WorldState,
    footprint_half_xy_from_quat,
    orientation_invariant_footprint_half_xy,
)
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.verify import (build_world_state, entity_footprint_half_xy,
                                        entity_position)
from embodied_agent.evaluation.calibration import cell_catalog
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.perception.frames import camera_for, capture
from embodied_agent.perception.observe import (PerceptionAssembler, ReadAbsent,
                                               ReadObject, StubReader, TaskContext,
                                               VlmReading)
from embodied_agent.perception.world_state import (
    ASSUMED_UPRIGHT,
    PerceptWorldStateTracker,
    cross_view_check,
    entities_from_percept,
    state_differences,
    world_state_from_percept,
)

MAIN, OVERHEAD, FRONT_HIGH = "main", "overhead", "front_high"
COLOURS = ("red", "green", "blue", "yellow", "purple")
# a box over the empty table, as in the P1-b tests: the smallest geometry that lets a
# hand-built reading reach the position code instead of the refusal code
TABLE_BOX = (300, 200, 340, 240)
# above and left of the table, where `main`'s depth reads a surface at no height this
# cell declares: a hand-built reading still claims a shape there, and the estimator's
# own refusal — not the stub's dropped word — is what leaves the body without a position
SKY_BOX = (60, 100, 96, 132)
UTTERANCE = "把红色方块放进中间盘"


def move_body(scene, entity_id: str, xyz, quaternion=(0.0, 0.0, 0.0, 1.0),
              steps: int = 80) -> None:
    """Create a change worth detecting. Grading stays on pixels either way."""
    d = scene.objects[entity_id]
    p.resetBasePositionAndOrientation(d["body"], list(xyz), list(quaternion),
                                      physicsClientId=scene.cid)
    for _ in range(steps):
        p.stepSimulation(physicsClientId=scene.cid)
    scene.sim_time += 0.02 * steps


def look(scene, catalog, out, view: str, name: str):
    """A fresh frame and a fresh reading of it, for the tests that change the world."""
    frame = capture(scene, str(out), camera_for(view), name, sim_time=scene.sim_time)[0]
    task = TaskContext(utterance=UTTERANCE, items=["红色方块"], regions=["中间盘"])
    return frame, StubReader().read(frame, catalog=catalog, task=task)


def reading_for(**colour_shape_box) -> VlmReading:
    """A sensor-channel reading from keyword colour → (shape, bbox[, extras]) triples."""
    rows = []
    for colour, spec in colour_shape_box.items():
        shape, bbox = spec[0], spec[1]
        extra = spec[2] if len(spec) > 2 else {}
        rows.append(ReadObject(ref=f"{colour} {shape}", color=colour, shape=shape,
                               bbox=bbox, confidence=extra.get("confidence", 0.9),
                               occluded=extra.get("occluded", False),
                               on_or_in=extra.get("on_or_in", "")))
    return VlmReading(objects=rows, reader="unit-test", channel="sensor",
                      prompt_version="unit", catalog_sha256="", raw_text="hand-built")


def bound_pairs(goal, world) -> set:
    """`(colour, target_id)` for every assignment, resolved against this snapshot.

    The same helper has to work on both channels, which is the point: the interpreter
    binds by attribute *or* by explicit id, and a percept snapshot has no ids the task
    could have named — so equality here is a statement about the world, not about
    naming."""
    out = set()
    for a in goal.assignments:
        hits = [e for e in world.entities
                if a.entity.entity_id == e.entity_id
                or (a.entity.attributes and all(
                    e.attributes.get(k) == v for k, v in a.entity.attributes.items()))]
        assert len(hits) == 1, f"{a.entity} binds to {len(hits)} entities"
        out.add((hits[0].attributes.get("color"), a.target_id))
    return out


# --------------------------------------------------------------------- fixtures ----


@pytest.fixture(scope="module")
def scene():
    case = find_case("dev_c5")
    s = PhysicsScene(seed=case.seed, object_layout=case.objects)
    yield s
    s.close()


@pytest.fixture(scope="module")
def case():
    return find_case("dev_c5")


@pytest.fixture(scope="module")
def catalog(scene):
    return cell_catalog(scene.trays.values())


@pytest.fixture(scope="module")
def frames(scene, catalog, tmp_path_factory):
    out = tmp_path_factory.mktemp("p1c_world_state")
    return {view: capture(scene, str(out), camera_for(view), view,
                          sim_time=scene.sim_time)[0]
            for view in (MAIN, OVERHEAD, FRONT_HIGH)}


@pytest.fixture(scope="module")
def readings(scene, catalog, frames):
    task = TaskContext(utterance=UTTERANCE, items=["红色方块"], regions=["中间盘"])
    reader = StubReader()
    return {view: reader.read(frame, catalog=catalog, task=task)
            for view, frame in frames.items()}


@pytest.fixture()
def assembler(catalog):
    return PerceptionAssembler(catalog)


@pytest.fixture()
def tracker(catalog):
    return PerceptWorldStateTracker(catalog, episode_id="p1c")


def see(tracker, assembler, frames, readings, view):
    """Assemble one view against the tracker's own memory and adopt the result."""
    return tracker.perceive(assembler, frames[view], readings[view])[1]


# ------------------------------------------------------- 1. who measured what ------


def test_a_percept_snapshot_is_labelled_by_the_channel_that_produced_it(tracker, assembler,
                                                                        frames, readings,
                                                                        scene):
    state = see(tracker, assembler, frames, readings, MAIN)
    percept = tracker.percept
    assert state.source is Source.sensor and state.schema_version == "3"
    assert state.observation_ref == percept.observation_id
    assert state.sim_time == percept.timestamp_sim
    assert state.state_version == 1                      # the tracker's own ledger
    assert {e.source for e in state.entities} == {Source.sensor}
    assert {e.entity_id for e in state.entities} == {f"seen:{c}" for c in COLOURS}
    # the strongest §5.1 check available here: a simulator entity id cannot appear
    # anywhere in a percept-derived snapshot, not even in a field whose format allows it
    assert "obj_" not in state.model_dump_json()
    assert {e.body_id for e in state.entities} == {-1}
    assert not {e.entity_id for e in state.entities} & set(scene.objects)


def test_every_field_the_frame_cannot_answer_is_left_unknown(tracker, assembler, frames,
                                                            readings, scene):
    state = see(tracker, assembler, frames, readings, MAIN)
    privileged = build_world_state(scene, 1, "obs_0001")
    for e in state.entities:
        assert e.held == "unknown" and e.at_rest == "unknown"
        assert e.linear_speed_mps is None and e.angular_speed_rps is None
        assert e.support.contact_count == 0 and e.support.source is Source.sensor
        assert e.attributes["orientation_measured"] == "false"
    assert state.held_object == "unknown"
    # the same questions, answered by the channel that can answer them: this is a
    # difference between sensors, not a field nobody ever fills in
    for e in privileged.entities:
        assert isinstance(e.held, bool) and isinstance(e.at_rest, bool)
        assert e.linear_speed_mps is not None and e.angular_speed_rps is not None
        assert e.source is Source.privileged and e.body_id >= 0
    assert privileged.held_object is None


def test_the_state_survives_its_own_json_and_leaves_the_text_channel_alone(tracker,
                                                                          assembler,
                                                                          frames, readings):
    state = see(tracker, assembler, frames, readings, MAIN)
    again = WorldState.model_validate_json(state.model_dump_json())
    assert again.model_dump() == state.model_dump()
    # `unparsed`/`facts` mean "what the printed sentence did not say". A picture has no
    # sentence, and borrowing the text channel to mean "a question I did not answer"
    # would make one field carry two very different kinds of evidence.
    assert again.facts == [] and again.raw_observation is None and again.unparsed == []


# ------------------------------------------------ 2. orientation is not measured -----


def test_a_percept_pose_is_an_assumption_and_the_geometry_code_acts_as_if_it_is_one(
        tracker, assembler, frames, readings):
    state = see(tracker, assembler, frames, readings, MAIN)
    cube = state.entity("seen:red")
    assert cube.orientation_measured is False
    assert cube.pose is not None and cube.pose.quaternion_xyzw == ASSUMED_UPRIGHT
    assert cube.pose.frame_id == "world"                 # the metres really are world
    hx, hy = entity_footprint_half_xy(cube)
    assert (hx, hy) == orientation_invariant_footprint_half_xy(cube.geometry)
    # what was refused: the upright pair `footprint_half_xy_from_quat` returns for this
    # same identity quaternion, and which one a placement verdict sees is the entire
    # content of the flag
    half = cube.geometry.half_extents.as_list()
    upright = footprint_half_xy_from_quat(cube.geometry, ASSUMED_UPRIGHT)
    assert upright == (half[0], half[1]) and hx > upright[0] and hy > upright[1]
    cylinder = state.entity("seen:blue")
    if cylinder.geometry.shape == "cylinder":
        # a cylinder on its side is the case that makes the difference worth a field:
        # its long axis goes horizontal, and no upright pair describes it
        assert entity_footprint_half_xy(cylinder) == (
            math.hypot(cylinder.geometry.radius, cylinder.geometry.half_h),) * 2


def test_the_assumed_heading_stays_out_of_the_model_payload_and_the_caveats_do_not(
        tracker, assembler, frames, readings, case):
    state = see(tracker, assembler, frames, readings, MAIN)
    task_in = TaskInput(task_id=case.task_id, utterance=case.utterance)
    ctx = DecisionContext(episode_id="p1c", task=task_in, goal=interpret(task_in, state),
                          state_version=state.state_version,
                          observation_ref=state.observation_ref, world=state)
    payload = ctx.model_payload()
    rows = {r["entity_id"]: r for r in payload["world"]["entities"]}
    assert set(rows) == {f"seen:{c}" for c in COLOURS}
    assert all(r["yaw_rad"] is None for r in rows.values())
    assert all(r["attributes"]["orientation_measured"] == "false" for r in rows.values())
    assert all(len(r["position"]) == 3 for r in rows.values())
    assert payload["world"]["held_object"] == "unknown"


def test_a_privileged_snapshot_is_untouched_by_the_new_field(scene):
    privileged = build_world_state(scene, 1, "obs_0001")
    assert all(e.orientation_measured is True for e in privileged.entities)
    assert all(entity_footprint_half_xy(e) ==
               footprint_half_xy_from_quat(e.geometry, e.pose.quaternion_xyzw)
               for e in privileged.entities)
    assert EntityState.model_fields["orientation_measured"].default is True


# ------------------------------------------------------- 3. grounding a request ------


def test_the_frozen_utterance_binds_the_same_world_on_both_channels(tracker, assembler,
                                                                   frames, readings,
                                                                   scene, case):
    state = see(tracker, assembler, frames, readings, MAIN)
    privileged = build_world_state(scene, 1, "obs_0001")
    task_in = TaskInput(task_id=case.task_id, utterance=case.utterance)
    goal = interpret(task_in, state)
    assert isinstance(goal, GoalSpec) and not goal.ambiguous, goal.clarification_request
    assert len(goal.assignments) == len(COLOURS)
    # the claim worth making: not "the percept parses" but "the percept and the state
    # read from physics describe the same five bodies going to the same three trays"
    assert bound_pairs(goal, state) == bound_pairs(interpret(task_in, privileged),
                                                   privileged)
    assert {t.target_id for t in state.targets} == {t.target_id for t in privileged.targets}


def test_a_shape_word_the_fit_got_wrong_makes_the_request_ungroundable_rather_than_wrong(
        tracker, assembler, frames, readings, scene, catalog):
    """§11's grounding metric in one case: a wrong attribute must fail closed.

    `dev_c5`'s own utterance names colours only, so a mis-segmented shape costs nothing
    there — which is why this test builds the shape-naming sentence from whichever body
    the frame got wrong instead of assuming the fixture still reproduces one."""
    state = see(tracker, assembler, frames, readings, MAIN)
    truth = {str(d["attributes"]["color"]): str(d["attributes"]["shape"])
             for d in scene.objects.values()}
    wrong = [c for c in COLOURS
             if state.has_entity(f"seen:{c}")
             and state.entity(f"seen:{c}").attributes["shape"] != truth[c]]
    assert wrong, ("the fit now names every shape in dev_c5 correctly: this test was "
                   "written against P1-b's measured 4/5 in `main` and must be re-aimed, "
                   "not left asserting nothing")
    colour = wrong[0]
    phrase = catalog.word_for_attributes({"color": colour, "shape": truth[colour]})
    utterance = f"把{phrase}放入中间盘"
    privileged = build_world_state(scene, 1, "obs_0001")
    true_goal = interpret(TaskInput(task_id="t", utterance=utterance), privileged)
    percept_goal = interpret(TaskInput(task_id="t", utterance=utterance), state)
    assert not true_goal.ambiguous, true_goal.clarification_request
    assert percept_goal.ambiguous and percept_goal.assignments == []
    # the refusal names the exact pair it looked for, and the clause it looked in it for:
    # a clarification that quoted some other attribute would send the model to the wrong
    # body. Parsed rather than string-matched, because the interpreter builds that dict in
    # its own word-scan order and the test has no business depending on it.
    complaint = percept_goal.clarification_request
    assert phrase in complaint
    named = ast.literal_eval(complaint.split("no entity matches ", 1)[1])
    assert named == {"color": colour, "shape": truth[colour]}


def test_an_unseen_body_is_not_an_entity_because_grounding_would_bind_to_it(assembler,
                                                                           frames,
                                                                           tracker):
    reading = reading_for(red=("cube", TABLE_BOX))
    reading.absent.append(ReadAbsent(ref="绿色圆柱", because="not_in_image", confidence=0.5))
    percept = assembler.assemble(frames[MAIN], reading)
    assert any(v.entity_id == "seen:green" and v.visible is False
               for v in percept.visibility)
    state = tracker.observe(percept)
    assert [e.entity_id for e in state.entities] == ["seen:red"]
    assert tracker.unseen() == [("seen:green", False, None)]
    goal = interpret(TaskInput(task_id="t", utterance="把绿色圆柱放入中间盘"), state)
    assert goal.ambiguous and goal.assignments == []
    # and the body the frame *did* see still grounds, so the refusal is about absence
    assert not interpret(TaskInput(task_id="t", utterance="把红色方块放入中间盘"),
                         state).ambiguous


def test_an_explicit_entity_id_in_the_utterance_cannot_be_grounded_from_pixels(
        tracker, assembler, frames, readings, scene):
    state = see(tracker, assembler, frames, readings, MAIN)
    some_id = next(iter(scene.objects))
    goal = interpret(TaskInput(task_id="t", utterance=f"把{some_id}放入中间盘"), state)
    assert goal.ambiguous and "unknown entity" in goal.clarification_request


# --------------------------------------------------- 4. a body without a position ----


def test_a_body_the_depth_image_could_not_locate_is_an_entity_with_no_position(assembler,
                                                                              frames,
                                                                              tracker):
    percept = assembler.assemble(frames[MAIN],
                                 reading_for(red=("cube", TABLE_BOX),
                                             green=("cuboid", SKY_BOX)))
    green = next(d for d in percept.detections if d.entity_id == "seen:green")
    assert "position_xyz_m" in next(d for d in percept.detections
                                    if d.entity_id == "seen:red").state_attributes, \
        "the locating half of this case has to work for the refusal to mean anything"
    assert "position_xyz_m" not in green.state_attributes, green.state_attributes
    state = tracker.observe(percept)
    located, unlocated = state.entity("seen:red"), state.entity("seen:green")
    assert located.pose is not None and unlocated.pose is None
    assert unlocated.supported_by == "unknown"
    assert unlocated.visible is True            # the frame saw it; only metres are missing
    assert unlocated.geometry is not None       # a declared size needs no depth to exist
    assert "seen:green" not in [o.entity_id for o in state.occupancy]
    with pytest.raises(ValueError, match="no measured position"):
        entity_position(unlocated)


def test_a_snapshot_never_mixes_two_cameras_and_remembers_each_one_separately(tracker,
                                                                             assembler,
                                                                             frames,
                                                                             readings):
    main_state = see(tracker, assembler, frames, readings, MAIN)
    overhead_state = see(tracker, assembler, frames, readings, OVERHEAD)
    audit = tracker.audits[-1]
    assert audit["not_comparable"].startswith("no change is reconciled across 'main'")
    assert audit["unreported_differences"] == []
    assert set(tracker.by_view) == {MAIN, OVERHEAD}
    # the measured reason the rule exists: the two views name different blockers, so a
    # merged relation set would assert a pair of facts no single frame ever showed
    hidden = {e.entity_id: e.attributes.get("hidden_behind") for e in main_state.entities
              if e.attributes.get("hidden_behind")}
    hidden_above = {e.entity_id: e.attributes.get("hidden_behind")
                    for e in overhead_state.entities if e.attributes.get("hidden_behind")}
    assert hidden != hidden_above
    assert {e.attributes["view"] for e in main_state.entities} == {MAIN}
    assert {e.attributes["view"] for e in overhead_state.entities} == {OVERHEAD}
    assert set(audit["fields_declined"]) == {"shape", "next_to", "occluded_by"}
    assert audit["fields_compared"] == ["relation"]


# ------------------------------------------------------------ 5. change detection ----


def test_a_still_table_leaves_the_change_channel_and_the_state_in_agreement(tracker,
                                                                           assembler,
                                                                           frames,
                                                                           readings):
    first = see(tracker, assembler, frames, readings, MAIN)
    second = see(tracker, assembler, frames, readings, MAIN)
    audit = tracker.audits[-1]
    assert tracker.percept.changes == []
    assert audit["state_differences"] == []
    assert audit["unreported_differences"] == []
    assert audit["rows_without_a_state_difference"] == []
    assert audit["unclaimed_attribute_differences"] == []
    volatile = {"state_version", "sim_time", "wall_time", "observation_ref"}
    assert first.model_dump(exclude=volatile) == second.model_dump(exclude=volatile)
    assert tracker.unreported() == []
    assert second.state_version == first.state_version + 1


def test_a_real_move_is_reported_and_the_state_keeps_what_it_cannot_claim(scene, tracker,
                                                                         assembler,
                                                                         frames, readings,
                                                                         catalog,
                                                                         tmp_path):
    see(tracker, assembler, frames, readings, MAIN)
    before = tracker.state.entity("seen:red")
    pos, _orn = scene.object_pose("obj_red_1")
    move_body(scene, "obj_red_1", [pos[0], pos[1] - 0.06, pos[2]], steps=40)
    frames, readings = dict(frames), dict(readings)
    frames[MAIN], readings[MAIN] = look(scene, catalog, tmp_path, MAIN, "main_moved")
    see(tracker, assembler, frames, readings, MAIN)
    audit, percept = tracker.audits[-1], tracker.percept
    moved = [c for c in percept.changes if c.attribute == "position"]
    assert moved and moved[0].subject_id == "seen:red"
    assert "seen:red:position" in audit["position_rows_not_carried_by_the_state"]
    assert audit["unreported_differences"] == []
    assert audit["rows_without_a_state_difference"] == []
    after = tracker.state.entity("seen:red")
    assert math.dist([before.pose.position.x, before.pose.position.y],
                     [after.pose.position.x, after.pose.position.y]) > 0.04
    # the ledger is what §11's change metric counts from, and it holds what was said
    assert len(tracker.ledger) == len(percept.changes) >= 1


def test_a_placement_made_while_the_agent_looks_elsewhere_still_surfaces(scene, tracker,
                                                                        assembler,
                                                                        frames, readings,
                                                                        catalog, tmp_path):
    """The hole P1-b's residual 5 predicted, closed at the layer that can see it.

    The assembler is right to refuse a cross-view comparison, and the consequence is that
    a body moved between two viewpoints produces no change row at all. Only the state
    layer, holding both snapshots, can notice that the one question whose answer does not
    depend on where the camera stands — what is this resting on — now has a different
    answer."""
    see(tracker, assembler, frames, readings, MAIN)
    tray = scene.trays["tray_middle"]
    d = scene.objects["obj_red_1"]
    move_body(scene, "obj_red_1", [tray["center"][0], tray["center"][1],
                                   tray["floor_top"] + d["geometry"].half_h_vertical])
    frames, readings = dict(frames), dict(readings)
    frames[OVERHEAD], readings[OVERHEAD] = look(scene, catalog, tmp_path, OVERHEAD,
                                                "oh_moved")
    state = see(tracker, assembler, frames, readings, OVERHEAD)
    audit = tracker.audits[-1]
    assert tracker.percept.changes == []                  # the change channel declined
    assert audit["reported_changes"] == []
    assert ["seen:red", "relation", "table -> tray_middle"] in \
        audit["cross_view_disagreements"]
    assert [e.entity_id for e in state.entities if e.supported_by == "tray_middle"] == \
        ["seen:red"]
    assert tracker.disagreements() == [audit]
    record = next(o for o in state.occupancy if o.entity_id == "seen:red")
    assert record.target_id == "tray_middle" and record.fully_inside


def test_occupancy_uses_the_same_arithmetic_as_the_privileged_channel(scene, tracker,
                                                                     assembler, frames,
                                                                     readings, catalog,
                                                                     tmp_path):
    tray = scene.trays["tray_middle"]
    d = scene.objects["obj_red_1"]
    move_body(scene, "obj_red_1", [tray["center"][0], tray["center"][1],
                                   tray["floor_top"] + d["geometry"].half_h_vertical])
    frames, readings = dict(frames), dict(readings)
    frames[MAIN], readings[MAIN] = look(scene, catalog, tmp_path, MAIN, "main_in_tray")
    state = see(tracker, assembler, frames, readings, MAIN)
    privileged = build_world_state(scene, 1, "obs_0001")
    per = next(o for o in state.occupancy if o.entity_id == "seen:red")
    pri = next(o for o in privileged.occupancy if o.entity_id == "obj_red_1")
    assert (per.target_id, per.fully_inside) == (pri.target_id, pri.fully_inside)
    # the conservative bound, never the smaller one, and the same measured metres
    assert per.footprint_half_xy[0] >= pri.footprint_half_xy[0]
    assert math.dist(per.rest_xy, pri.rest_xy) < 0.01
    assert [o.target_id for o in state.occupancy] == ["tray_middle"]


# ---------------------------------------------------------------- 6. the audit -------


def test_state_differences_speaks_the_change_channels_vocabulary(catalog, assembler,
                                                                frames, readings,
                                                                tracker):
    """So that "did the world move" and "was a change reported" are one comparison."""
    first = see(tracker, assembler, frames, readings, MAIN)
    moved = WorldState.model_validate_json(first.model_dump_json())
    target = next(e for e in moved.entities if e.supported_by == "table")
    target.supported_by = "tray_middle"
    assert state_differences(first, moved) == [
        (target.entity_id, "relation", "table -> tray_middle")]
    assert state_differences(first, WorldState.model_validate_json(
        first.model_dump_json())) == []
    dropped = WorldState.model_validate_json(first.model_dump_json())
    dropped.entities = [e for e in dropped.entities if e.entity_id != "seen:red"]
    assert state_differences(first, dropped) == [
        ("seen:red", "visibility", "seen -> not_reported")]
    assert state_differences(dropped, first) == [
        ("seen:red", "presence", "not_reported -> seen")]


def test_reconcile_separates_a_declined_comparison_from_a_missed_one(tracker, assembler,
                                                                    frames, readings):
    state_a = see(tracker, assembler, frames, readings, MAIN)
    state_b = see(tracker, assembler, frames, readings, OVERHEAD)
    row = cross_view_check(state_a, state_b, view=OVERHEAD, previous_view=MAIN)
    assert row["cross_view_disagreements"] == []          # this table did not move
    assert row["bodies_compared"] == len(state_a.entities) == len(state_b.entities)
    assert "shape" in row["fields_declined"]
    assert row["unreported_differences"] == []
    assert set(row) - {"observation_id", "previous_observation_id"} <= set(tracker.audits[-1])
    # a moved body is the case the guard is for: the same comparison, with the world
    # actually changed, has nowhere to hide
    a2 = WorldState.model_validate_json(state_a.model_dump_json())
    a2.entities[0].supported_by = "tray_middle"
    assert cross_view_check(a2, state_b, view=MAIN, previous_view=OVERHEAD)[
        "cross_view_disagreements"] == [
            [state_a.entities[0].entity_id, "relation", "tray_middle -> table"]]


def test_a_snapshot_refuses_to_be_grounded_on_another_cells_catalog(tracker, assembler,
                                                                   frames, readings,
                                                                   catalog):
    see(tracker, assembler, frames, readings, MAIN)
    percept = tracker.percept
    other = catalog.model_copy(deep=True)
    other.regions[0].inner_half += 0.01
    assert other.sha256() != catalog.sha256()
    with pytest.raises(ValueError, match="catalog"):
        world_state_from_percept(percept, other, state_version=2)
    # the refusal is about the digest, not the bodies: the same percept still assembles
    # into the cell it was read in
    assert sorted(e.entity_id for e in entities_from_percept(percept)) == \
        sorted(d.entity_id for d in percept.detections)
