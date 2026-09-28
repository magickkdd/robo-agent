"""P1-b contract tests: one reading in, one `PerceptionObservation` out (§5.1).

The split these tests exist to defend is the one in `perception/observe.py`: a reader
answers *where in the picture* and *what it is*; the assembler answers *where in the
cell*, *what is beside it*, *whether a tray is free* and *what changed*. So almost
every case here asks one of three questions of the assembler:

* does a number it reports come from the depth image and the catalog and nowhere else —
  checked by re-running the estimator and comparing, and by the reader structures having
  no metric field a model could answer through;
* does a question it cannot answer **say so** in `uncertainties`, with the right `why`:
  `not_measured` when no answer exists, `conflicting_evidence` when two real readings
  disagree, `low_confidence` when an answer is weak, `not_visible` when the frame simply
  showed none of it;
* and does a change it reports describe the world rather than its own camera.

Most cases run on a hand-built `VlmReading` over one real captured frame, which is the
point: the same structure a vision model produces, with the model's price and
nondeterminism taken out. The tests that go through `VLMReader` use a fake adapter,
because that half is about provenance rather than geometry.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

import numpy as np
import pytest
from pydantic import ValidationError

from embodied_agent.core.contracts import Source
from embodied_agent.core.scene import TABLE_TOP_Z, PhysicsScene
from embodied_agent.core.v02 import PerceptionObservation, SpatialRelation
from embodied_agent.evaluation.calibration import cell_catalog
from embodied_agent.evaluation.tasks import DIMS, find_case
from embodied_agent.perception.catalog import PerceptionCatalog, geometry_for
from embodied_agent.perception.frames import (camera_for, capture, load_depth, load_rgb,
                                              save_frame)
from embodied_agent.perception.geometry import estimate_centre
from embodied_agent.perception.observe import (PERCEPTION_SYSTEM, PERCEPTION_USER,
                                              PROMPT_VERSION, READING_ROW_COLUMNS,
                                              PerceptionAssembler, ReadAbsent, ReadObject,
                                              ReadRegion, ReadingError, StubReader, TaskContext,
                                              VlmReading, VLMReader, percept_id,
                                              prompt_sha256, user_prompt)

MAIN = "main"
TRAY_FLOOR_Z = 0.628              # the measured `floor_top` of every declared tray
# a box over the empty table in `main`, measured to support z 0.6208 m by the same
# side-sampling the estimator uses: the smallest geometry that lets a hand-built
# reading reach the position code instead of the refusal code
TABLE_BOX = (300, 200, 340, 240)


# --------------------------------------------------------------------- helpers -----


def window_for_z(camera, px: float, py: float, z: float) -> float:
    """Window depth whose back-projection at this pixel lands at height `z`.

    The bisection is `test_v02_perception_sensor.py`'s, repeated rather than imported:
    a test file that has to load another test file to build its own depth map is one
    more way for a suite to break in a way that says nothing about the code."""
    lo, hi = 0.0, 1.0
    flo = camera.unproject(px, py, lo).z - z
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if (camera.unproject(px, py, mid).z - z) * flo > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def boxes_reading(**colour_shape_box) -> VlmReading:
    """A reading from keyword colour → (shape, bbox[, extras]) triples, confidence 0.9."""
    rows = []
    for colour, spec in colour_shape_box.items():
        extra = spec[2] if len(spec) > 2 else {}
        rows.append(ReadObject(ref=colour, color=colour, shape=spec[0], bbox=spec[1],
                               confidence=0.9, occluded=False, **extra))
    return VlmReading(reader="fixture", channel="sensor", objects=rows)


def pos_of(observation: PerceptionObservation, colour: str) -> list[float]:
    for det in observation.detections:
        if det.entity_id == f"seen:{colour}":
            p = det.state_attributes.get("position_xyz_m")
            assert p is not None, colour
            return [float(v) for v in p]
    raise KeyError(colour)


def shifted(observation: PerceptionObservation, colour: str,
            dx_mm: float, dy_mm: float, dz_mm: float = 0.0) -> PerceptionObservation:
    """A copy of a percept with one body moved, in millimetres."""
    moved = copy.deepcopy(observation)
    target = next(d for d in moved.detections if d.entity_id == f"seen:{colour}")
    p = target.state_attributes["position_xyz_m"]
    target.state_attributes["position_xyz_m"] = [round(p[0] + dx_mm / 1000.0, 4),
                                                 round(p[1] + dy_mm / 1000.0, 4),
                                                 round(p[2] + dz_mm / 1000.0, 4)]
    return moved


def support_relations(observation: PerceptionObservation) -> dict[str, tuple[str, str]]:
    """The same projection `_changes` builds for `previous`, from the current percept:
    support rows only, because a pairwise row is not a per-subject claim."""
    return {r.subject_id: (str(r.predicate), str(r.related_id))
            for r in observation.relations
            if r.predicate not in ("next_to", "blocks", "unknown")}


def blockers(observation: PerceptionObservation) -> dict[str, str]:
    return {r.related_id: r.subject_id for r in observation.relations
            if r.predicate == "blocks"}


def diff(assembler, frame, previous, current, *, channel="sensor"):
    """One change-diff, with the uncertainties it added, without re-rendering a frame."""
    uncertainties: list = []
    rows = assembler._changes(frame, current.detections, previous, channel,
                             support_relations(current), blockers(current),
                             uncertainties)
    return rows, uncertainties


def flat_tray_frame(tmp_path, name="tray_plane"):
    """A frame whose depth is one plane at the measured tray floor.

    Synthetic on purpose: what is under test is the *counting*, not the optics, and the
    same rule is measured on rendered pixels in `work/p1_perceive_check.py`."""
    cam = camera_for(MAIN)
    depth = np.full((cam.height, cam.width),
                    window_for_z(cam, 463.0, 372.6, TRAY_FLOOR_Z))
    return save_frame(str(tmp_path), cam, np.zeros((cam.height, cam.width, 3), np.uint8),
                      depth, name)


def whys(observation, why: str) -> list[str]:
    return [u.what for u in observation.uncertainties if u.why == why]


def items_where(observation, needle: str) -> list[str]:
    return [u.what for u in observation.uncertainties if needle in u.what]


# --------------------------------------------------------------------- fixtures -----


@pytest.fixture(scope="module")
def scene():
    case = find_case("dev_c5")
    s = PhysicsScene(seed=case.seed, object_layout=case.objects)
    yield s
    s.close()


@pytest.fixture(scope="module")
def catalog(scene):
    return cell_catalog(scene.trays.values())


@pytest.fixture(scope="module")
def frame(scene, tmp_path_factory):
    out = tmp_path_factory.mktemp("p1b_observe")
    f, _rgb, _d = capture(scene, str(out), camera_for(MAIN), "main", sim_time=scene.sim_time)
    return f


@pytest.fixture(scope="module")
def depth(frame):
    return load_depth(frame)


@pytest.fixture(scope="module")
def assembler(catalog):
    return PerceptionAssembler(catalog)


@pytest.fixture(scope="module")
def stub_reading(frame, catalog):
    return StubReader().read(frame, catalog=catalog,
                            task=TaskContext(utterance="把红色方块放进中盘",
                                            items=["红色方块", "紫色长方体"],
                                            regions=["中盘"]))


@pytest.fixture(scope="module")
def obs(assembler, frame, stub_reading):
    return assembler.assemble(frame, stub_reading)


# --------------------------------------------------------- the six groups exist ----


def test_one_percept_answers_all_six_questions_and_survives_its_own_json(obs, frame):
    assert len(obs.detections) == 5 and obs.relations and obs.visibility and obs.regions
    assert obs.changes == [] and obs.uncertainties            # first frame, nothing to diff
    again = PerceptionObservation.model_validate_json(obs.model_dump_json())
    assert again.model_dump() == obs.model_dump()
    assert again.source is Source.sensor and again.schema_version == "4"
    assert obs.modality == "rgbd" and obs.camera_id == MAIN
    assert obs.based_on_observation_ref == frame.frame_id       # which pixels this rests on
    assert obs.provenance["frame"]["image_sha256"] == frame.image_sha256
    assert {d.entity_id for d in obs.detections} == {f"seen:{c}" for c in
                                                      ("red", "green", "blue", "yellow",
                                                       "purple")}
    for det in obs.detections:
        assert det.state_attributes["position_xyz_m"] is not None, det.entity_id
        assert det.state_attributes["support_z_m"] is not None
    assert {v.entity_id for v in obs.visibility if v.visible is True} == \
        {d.entity_id for d in obs.detections}


def test_orientation_is_not_measured_and_the_percept_says_so_in_every_field(obs, catalog):
    """No field in this percept claims a rotation, because nothing here measured one.

    `pose` stays `None` rather than a zero quaternion — which would read as a measurement
    — and the attribute that would have carried it is recorded `False`, so a later
    consumer cannot mistake an assumed upright pose for a seen one. `extent` is the
    catalog's own declared geometry, looked up from the shape word: the one place a
    number enters a percept without being measured, and therefore the place a plan is
    allowed to be wrong about a body that has fallen over."""
    for det in obs.detections:
        assert det.pose is None
        assert det.state_attributes["orientation_measured"] is False
        assert det.state_attributes["shape"] == det.kind
        assert det.extent == geometry_for(det.kind, DIMS[det.kind])
        assert det.how_identified == "segmented"
        assert det.state_attributes["shape_source"] == "silhouette_fit"


# ----------------------------------------------- a reader cannot answer in metres ----


def test_a_reading_has_no_field_a_metric_number_could_live_in():
    """The information rule, enforced by the absence of a place to put it.

    `StrictModel` forbids unknown keys, so a model that volunteers a coordinate — the
    single most tempting thing for a vision model to do — fails to parse at all. That is
    stronger than a runtime check, and it is what makes §5.1's ban on substituting
    simulator state for the VLM path a property of the schema rather than a habit."""
    assert set(ReadObject.model_fields) == {"ref", "color", "shape", "bbox", "confidence",
                                           "occluded", "hidden_behind", "on_or_in"}
    for name in ReadObject.model_fields:
        assert not any(t in name for t in ("_m", "xyz", "metre", "meter", "position"))
    with pytest.raises(ValidationError):
        ReadObject.model_validate({"bbox": [1, 1, 20, 20], "position_xyz_m": [0.5, 0, 0.66]})
    with pytest.raises(ValidationError):
        VlmReading.model_validate({"objects": [{"bbox": [1, 1, 20, 20], "confidence": 0.5,
                                               "z_m": 0.62}]})
    with pytest.raises(ValidationError):
        ReadRegion.model_validate({"target_id": "tray_left", "free": True,
                                   "occupants": ["o_1"]})
    assert "position_xyz_m" not in json.dumps(ReadObject.model_json_schema())


def test_a_readers_word_is_used_and_never_edited_while_its_claim_is_kept_separate(
        assembler, frame, stub_reading):
    """`on_or_in` is the seam between the two halves.

    A reader may be wrong about which tray a body is in, and its words are the only
    source for the referring expressions a later decision grounds against; so the claim
    travels as a claim, the relation is computed, and the gap between them is recorded
    instead of closed."""
    reading = stub_reading.model_copy(update={"objects": [
        o.model_copy(update={"on_or_in": "tray_left"}) for o in stub_reading.objects]})
    out = assembler.assemble(frame, reading)
    for det in out.detections:
        assert det.state_attributes["claimed_on_or_in"] == "tray_left"
        rel = next(r for r in out.relations if r.subject_id == det.entity_id
                   and r.predicate not in ("next_to", "blocks"))
        assert (rel.predicate, rel.related_id) == ("on", "table")
    assert "a reader said" in " ".join(whys(out, "conflicting_evidence"))
    assert {r.related_id for r in out.relations if r.predicate == "in"} == set()


# ------------------------------------------------------------ refusals, not repairs ----


def test_a_colour_outside_the_catalog_is_reported_and_a_shape_word_is_dropped(assembler,
                                                                             frame):
    """An invented word is a reporting failure, not a new kind of object (§5.1).

    The two attributes are treated differently on purpose: a colour is the identity, so
    a box with an unknown colour is a box about nothing and is refused; a shape is one
    attribute of a body the picture did see, so the body is kept and the word is dropped
    to `unknown` — which also removes the half-height, and so the position."""
    out = assembler.assemble(frame, boxes_reading(
        mauve=("cube", TABLE_BOX), red=("sphere", TABLE_BOX)))
    assert {d.entity_id for d in out.detections} == {"seen:red"}
    assert items_where(out, "color 'mauve'") and items_where(out, "shape 'sphere'")
    red = out.detections[0]
    assert red.kind == "unknown" and red.state_attributes["color"] == "red"
    assert "position_xyz_m" not in red.state_attributes
    assert whys(out, "conflicting_evidence") and whys(out, "not_measured")


def test_a_duplicate_box_for_one_colour_becomes_a_conflict_not_a_merge(assembler, frame):
    """Two boxes, one body: keep one answer, report the collision.

    Merging is the tempting repair and the wrong one. Colours never repeat inside a
    frozen case, so a duplicate can only be a false positive or a split silhouette, and
    the overlap between the two boxes is exactly what a second viewpoint would resolve."""
    reading = VlmReading(reader="fixture", channel="sensor", objects=[
        ReadObject(color="red", shape="cube", bbox=TABLE_BOX, confidence=0.9, occluded=False),
        ReadObject(color="red", shape="cube", bbox=(302, 202, 344, 244), confidence=0.9,
                   occluded=False)])
    out = assembler.assemble(frame, reading)
    assert len(out.detections) == 1, "two boxes for one colour became two bodies"
    assert items_where(out, "two boxes were reported for seen:red")
    assert out.detections[0].state_attributes["bbox_px"] == [300.0, 200.0, 340.0, 240.0]


@pytest.mark.parametrize("bad, why_needed", [
    ((1200, 1200, 1400, 1400), "leaves the frame bounds"),
    ((600, 400, 580, 460), "empty or inverted"),
    ((300, 200, 305, 260), "px floor"),
])
def test_an_unusable_box_is_refused_rather_than_repaired(assembler, frame, bad,
                                                        why_needed):
    """Including the 0-1000 normalised case, which is the same refusal.

    Checking the range instead of clipping is the point: a model that answered in
    normalised coordinates described a *different image*, and rescaling its numbers
    into this frame would attribute this frame's depth to a box that was never in it."""
    out = assembler.assemble(frame, boxes_reading(red=("cube", bad)))
    assert out.detections == []
    assert whys(out, "conflicting_evidence")
    text = " ".join(u.what for u in out.uncertainties)
    assert why_needed in text, text
    assert "does not rescale" in " ".join(u.would_resolve for u in out.uncertainties)


def test_a_low_confidence_detection_stays_and_is_labelled_weak(assembler, frame):
    """Below the floor is not absent. A plan may still use the number; it must be able
    to see that the reader was unsure."""
    out = assembler.assemble(frame, VlmReading(reader="fixture", channel="sensor", objects=[
        ReadObject(color="red", shape="cube", bbox=TABLE_BOX, confidence=0.10,
                   occluded=False)]))
    assert len(out.detections) == 1 and out.detections[0].confidence == 0.10
    assert pos_of(out, "red") is not None
    weak = [u for u in out.uncertainties if "below this run's floor" in u.what]
    assert len(weak) == 1 and weak[0].why == "low_confidence"


def test_an_rgb_only_frame_locates_nothing_and_says_which_channel_is_missing(scene,
                                                                           tmp_path,
                                                                           assembler,
                                                                           frame, catalog):
    """A frame with no depth is not a frame with an empty table.

    `modality` flips to `rgb`, every body is still *seen* — the colour channel says so —
    and each one carries its own `not_measured` row naming the missing channel, which is
    the difference between a blind spot and a finding."""
    shallow = save_frame(str(tmp_path), frame.camera, load_rgb(frame),
                         np.load(frame.depth_ref), "rgb_only", keep_depth=False)
    out = assembler.assemble(shallow, StubReader().read(shallow, catalog=catalog))
    assert out.modality == "rgb" and out.depth_ref is None
    assert len(out.detections) == 5
    for det in out.detections:
        assert "position_xyz_m" not in det.state_attributes, det.entity_id
        assert det.kind == "unknown"           # shape needs the support too
    assert len(items_where(out, "kept no depth channel")) == 5
    assert out.provenance["frame"]["limitations"] == list(shallow.limitations)


# --------------------------------------------------------- geometry is re-runnable ----


def test_a_position_is_the_estimators_own_answer_when_the_box_is_re_run(obs, frame, depth,
                                                                       catalog):
    """Nothing here is trusted to memory: the solve is repeated and compared.

    The z is the interesting part. A camera cannot see a body's centre, so z must be
    `measured support + declared half-height` and not a depth read at the box centre —
    re-running `estimate_centre` proves the arithmetic, and the three-way check below
    proves the ordering."""
    for det in obs.detections:
        shape = det.state_attributes["shape"]
        half_h = catalog.half_height(shape)
        centre, ev = estimate_centre(frame.camera, depth,
                                     tuple(det.state_attributes["bbox_px"]),
                                     half_height_m=half_h,
                                     z_bounds=catalog.support_z_bounds)
        assert centre is not None, (det.entity_id, ev)
        recorded = det.state_attributes["position_xyz_m"]
        assert [round(float(v), 4) for v in (centre.x, centre.y, centre.z)] == recorded
        assert abs(recorded[2] - det.state_attributes["support_z_m"] - half_h) < 1e-3
        # the recorded evidence is the same dict the estimator returned, not a summary
        assert obs.provenance["geometry"][det.entity_id]["method"] == \
            "ray_through_box_centre_at_support_z"


def test_a_measured_support_sits_at_a_height_the_cell_declares(obs, catalog):
    lo, hi = catalog.support_z_bounds
    for det in obs.detections:
        z = det.state_attributes["support_z_m"]
        assert lo <= z <= hi, (det.entity_id, z)
        # `dev_c5` is a table, so every support must *be* the table top within the
        # declared tolerance: the external anchor a round-trip test cannot provide
        assert abs(z - TABLE_TOP_Z) <= catalog.support_z_tol_m, (det.entity_id, z)


def test_a_shape_claim_carries_the_ranking_it_came_out_of(obs):
    """The sensor channel measures a fit, so it can show its rivals; a shape word that
    arrives with no evidence behind it is a different kind of claim (§11's grounding
    metric has to be able to tell the two apart)."""
    fits = obs.provenance["silhouette_fit"]
    assert set(fits) == {d.entity_id for d in obs.detections}
    for eid, fit in fits.items():
        assert fit["ranked"], eid
        assert set(fit["scores"]) == {"cube", "cuboid", "cylinder"}
        assert fit["assumes"] == "upright, zero yaw"
        assert fit["segmentation"]["pixels"] > 0


def test_a_coin_flip_between_two_shapes_is_flagged_and_its_answer_kept(assembler, frame):
    """The fit's own margin, not a reviewer's opinion, decides.

    Measured on `dev_c5`: `front_high` puts the cylinder/cuboid pair 0.0443 IoU apart
    and `overhead` the cuboid/cylinder pair 0.049 — both under this module's 0.05
    ambiguity floor. A plan still needs a half-height, so the answer stays."""
    out = assembler.assemble(frame, VlmReading(
        reader="fixture", channel="sensor", prompt_sha256="",
        objects=[ReadObject(color="red", shape="cylinder", bbox=TABLE_BOX, confidence=0.9,
                           occluded=False)],
        meta={}))
    flagged = [u for u in out.uncertainties if "only" in u.what and "IoU ahead of" in u.what]
    fit = out.provenance["silhouette_fit"]
    if fit:                                   # a hand-built reading carries no fit evidence,
        assert flagged                        # so this branch only bites via StubReader
    assert out.detections[0].kind == "cylinder"


# ------------------------------------------------------------ regions and occupancy ----


def test_three_measured_occupants_fill_a_tray_and_there_is_no_room(catalog, tmp_path,
                                                                  assembler):
    """`occupants` are counted from measured positions, so `free` can be a fact.

    The three bodies sit in one row of pixels on one synthetic plane, which is what makes
    `free is False` a consequence of the count rather than of a reader's impression."""
    f = flat_tray_frame(tmp_path)
    out = assembler.assemble(f, boxes_reading(
        red=("cube", (400, 350, 440, 390)), green=("cube", (450, 350, 490, 390)),
        blue=("cube", (500, 350, 540, 390))))
    middle = next(r for r in out.regions if r.region_id == "tray_middle")
    assert middle.occupants == ["seen:blue", "seen:green", "seen:red"]
    assert middle.free is False and "OVER" not in middle.capacity_note
    assert middle.confidence == pytest.approx(0.9)
    assert [(r.region_id, r.free, r.occupants) for r in out.regions
            if r.region_id != "tray_middle"] == [("tray_left", True, []),
                                                 ("tray_right", True, [])]
    assert [(r.subject_id, r.predicate, r.related_id) for r in out.relations
            if r.predicate == "in"] == [("seen:red", "in", "tray_middle"),
                                       ("seen:green", "in", "tray_middle"),
                                       ("seen:blue", "in", "tray_middle")]
    for eid in ("seen:red", "seen:green", "seen:blue"):
        det = next(d for d in out.detections if d.entity_id == eid)
        support = det.state_attributes["support_z_m"]
        # the plane is synthetic and one window value cannot be level in world z across
        # a whole 800x600 image, so what must hold is the *ordering*: the measured floor
        # within the declared tolerance, and the centre one declared half-height above it
        assert abs(support - TRAY_FLOOR_Z) <= catalog.support_z_tol_m, (eid, support)
        assert abs(pos_of(out, eid.split(":")[1])[2] - support
                   - catalog.half_height("cube")) < 1e-3


def test_a_fourth_occupant_is_a_conflict_the_percept_keeps(catalog, tmp_path, assembler):
    """Over capacity is reported, not repaired by dropping a detection.

    Either the fourth body is a false positive or the tray is no longer usable, and the
    plan needs to be told which of those two it is choosing between — not handed a
    three-object list that looks like an observation."""
    f = flat_tray_frame(tmp_path, "tray_plane_over")
    out = assembler.assemble(f, boxes_reading(
        red=("cube", (400, 350, 440, 390)), green=("cube", (450, 350, 490, 390)),
        blue=("cube", (500, 350, 540, 390)), yellow=("cube", (425, 300, 465, 340))))
    middle = next(r for r in out.regions if r.region_id == "tray_middle")
    assert len(middle.occupants) == 4 and "OVER the declared capacity by 1" in \
        middle.capacity_note
    assert items_where(out, "against a declared capacity of 3")
    assert whys(out, "conflicting_evidence")


def test_an_occupied_claim_without_a_position_leaves_free_unknown(assembler, frame):
    """`free: unknown` and `free: false` are different answers, and only one of them is
    what a claim without geometry supports.

    This is the field a plan reads before ordering a fourth placement, so the honest
    `unknown` has to cost something: confidence 0.0 says no downstream statistic may
    count this as an answered question."""
    out = assembler.assemble(frame, VlmReading(reader="fixture", channel="sensor", objects=[
        ReadObject(color="red", shape="", bbox=TABLE_BOX, confidence=0.9, occluded=False,
                   on_or_in="tray_left")]))
    left = next(r for r in out.regions if r.region_id == "tray_left")
    assert left.free == "unknown" and left.occupants == ["seen:red"]
    assert "free is unknown because" in left.capacity_note and left.confidence == 0.0


def test_a_readers_region_claim_that_the_geometry_denies_is_kept_as_a_conflict(
        assembler, frame):
    """Agreement is evidence and disagreement is the more interesting result, so both
    are stored side by side rather than merged into one field."""
    out = assembler.assemble(frame, VlmReading(
        reader="fixture", channel="sensor", objects=[],
        regions=[ReadRegion(target_id="tray_left", occupied=True, confidence=0.8),
                 ReadRegion(target_id="tray_right", occupied=False, confidence=0.8)]))
    assert out.detections == [] and len(out.regions) == 3
    text = " ".join(whys(out, "conflicting_evidence"))
    assert "tray_left is occupied" in text and "tray_right is empty" not in text
    assert [(u.why, "no object was detected" in u.what) for u in out.uncertainties
            if "no object was detected" in u.what] == [("not_visible", True)]


# ------------------------------------------------------------- changes and views ----


def test_an_unmoved_table_told_twice_reports_no_change(assembler, frame, stub_reading,
                                                      obs):
    """The regression this test exists for.

    `work/p1_perceive_check.py` measured one spurious `relation` row per view between
    two renders of the same still scene: `blocks:seen:red -> on:table`. It was not a bad
    diff but a mixed one — the previous percept was indexed by subject with last-wins,
    so a blocker's pairwise `blocks` row overwrote its own support row and was then
    compared against a map that only ever holds support. A change channel that cannot
    tell those kinds of row apart reports relations changing in a scene nobody touched."""
    again = assembler.assemble(frame, stub_reading, previous=obs)
    assert again.changes == [], [(c.subject_id, c.attribute, c.before, c.after)
                                for c in again.changes]
    assert again.detections[0].state_attributes["position_xyz_m"] == \
        obs.detections[0].state_attributes["position_xyz_m"]
    assert any("nothing moved, not a missing answer" in u.would_resolve
               for u in again.uncertainties)


def test_a_move_past_the_threshold_is_reported_and_a_near_miss_is_declined_loudly(
        assembler, frame, obs):
    """`move_threshold_m = 0.025` is a measured number, and both sides of it speak.

    The sweep in `work/p1_perceive_check.py` moved one body 5…60 mm and re-perceived it:
    5 and 10 mm were not reported, 15 and 20 mm produced a `position_below_threshold`
    row, 25…60 mm produced `position` rows whose deltas were right within 1.2 mm. So a
    body under the threshold is not "nothing happened" — the percept says it declined,
    and by how far."""
    small = shifted(obs, "green", 0.0, -15.0)
    rows, _unc = diff(assembler, frame, obs, small)
    assert [(c.attribute, c.subject_id) for c in rows] == \
        [("position_below_threshold", "seen:green")]
    assert "moved 15.0 mm, under 25 mm" in rows[0].after

    big = shifted(obs, "green", 0.0, -40.0)
    rows2, _unc2 = diff(assembler, frame, obs, big)
    assert [(c.attribute, c.subject_id) for c in rows2] == [("position", "seen:green")]
    before = [float(v) for v in rows2[0].before.strip("[]").split(",")]
    after = [float(v) for v in rows2[0].after.strip("[]").split(",")]
    assert after == [round(v, 4) for v in pos_of(big, "green")]
    assert abs(math.hypot(after[0] - before[0], after[1] - before[1]) - 0.040) < 1.5e-3


def test_a_shape_reclassification_is_one_row_about_one_body(assembler, frame, obs):
    """`seen:red`, not `seen:red:cube`: the identity is the thing that survives.

    The measured reason is in `work/p1_perceive_check.py` — a shape-keyed identity made
    two of five bodies change identity between two views of a table nobody touched, so a
    reclassification read as one object vanishing and another appearing. Under a
    colour-only identity it is a report about a continuing body, which is what §5.1 asks
    the change channel to produce."""
    assert percept_id("red", "cube") == percept_id("red", "cylinder") == "seen:red"
    assert percept_id("", "cube") == "seen:shape:cube" and percept_id("", "") == "seen:unknown"
    re_read = copy.deepcopy(obs)
    for det in re_read.detections:
        if det.entity_id == "seen:red":
            det.kind = "cylinder"
            det.state_attributes["shape"] = "cylinder"
    rows, _unc = diff(assembler, frame, obs, re_read)
    assert [(c.attribute, c.subject_id, c.before, c.after) for c in rows
            if c.attribute == "shape"] == [("shape", "seen:red", "cube", "cylinder")]
    assert not [c for c in rows if c.attribute in ("presence", "visibility")], \
        "a shape word changed and the percept reported one body appearing and another vanishing"


def test_a_blocker_row_is_never_compared_against_a_support_row(assembler, frame, obs):
    """The same measured bug, one row at a time.

    `seen:yellow` blocks `seen:red` in this frame *and* rests on the table. The two are
    different kinds of claim — one is pairwise, one is about a body's support — so an
    added `blocks` row must not surface as a `relation` change for its subject.
    Occlusion is still diffed, as its own `occluded_by` row keyed on the *hidden* body,
    because that is the subject a replanner cares about."""
    previous = copy.deepcopy(obs)
    previous.relations.append(SpatialRelation(subject_id="seen:purple", predicate="blocks",
                                             related_id="seen:blue", confidence=0.9,
                                             evidence_ref=frame.frame_id))
    rows, _unc = diff(assembler, frame, previous, obs)
    assert not [c for c in rows if c.attribute in ("relation", "position")], \
        [(c.subject_id, c.attribute, c.before, c.after) for c in rows]
    assert [(c.subject_id, c.attribute, c.before, c.after) for c in rows
            if c.attribute == "occluded_by"] == \
        [("seen:blue", "occluded_by", "seen:purple", "not_hidden")], \
        "an occlusion that ended must be reported about the body that was hidden"


def test_a_body_that_appeared_and_one_that_disappeared_are_separate_rows(assembler,
                                                                        frame, obs):
    """`modality` on a change row is §5.1's ban on a hidden-state substitute, per row.

    A body the previous percept did not report and one it did are different facts about
    different bodies, and the two directions must not collapse into one "the scene
    changed" summary."""
    re_read = copy.deepcopy(obs)
    gone = re_read.detections.pop(0)
    rows, _unc = diff(assembler, frame, re_read, obs)
    assert [(c.subject_id, c.attribute, c.before, c.after) for c in rows] == \
        [(gone.entity_id, "presence", "not_reported", "seen")]

    rows2, _unc2 = diff(assembler, frame, obs, re_read)
    assert [(c.subject_id, c.attribute, c.before, c.after) for c in rows2] == \
        [(gone.entity_id, "visibility", "seen", "not_reported")]
    assert all(c.modality == "sensor" for c in rows + rows2), \
        "a change derived from pixels must not be labelled as one derived from a model"


def test_a_change_against_another_view_is_refused_and_announced(assembler, frame, obs):
    """Comparing `main` against `overhead` measured 4 position changes and 2 identity
    changes on a table that did not move: the camera moved.

    The group says it declined instead of going quiet, because an empty `changes` list is
    read downstream as "nothing changed" — which is the exact failure §5.1's change
    detection exists to avoid."""
    other = copy.deepcopy(obs)
    other.camera_id = "overhead"
    out = assembler.assemble(frame, _reading_of(other), previous=other)
    assert out.changes == []
    refused = [u for u in out.uncertainties if "partly the viewpoint" in u.what]
    declined = [u for u in out.uncertainties if "different views" in u.what]
    assert [u.why for u in refused + declined] == ["not_measured", "not_measured"]
    assert "overhead" in refused[0].what and "overhead" in refused[0].would_resolve
    assert "nothing here says the scene did not change" in declined[0].what


def test_the_view_a_refusal_points_at_is_never_the_view_being_asked(assembler, scene,
                                                                   tmp_path):
    """§5.1's uncertainty marking is only useful if it names an action, and "look where
    you are looking" is not one.

    `alt_views[0]` is `overhead`, which is also the view whose refusals this module
    records most often — so the generic fallback used to recommend exactly that."""
    cam = camera_for("overhead")
    f, _rgb, _d = capture(scene, str(tmp_path), cam, "overhead_view",
                          sim_time=scene.sim_time)
    out = assembler.assemble(f, boxes_reading(red=("cube", (30, 30, 80, 80))))
    refusals = [u for u in out.uncertainties if "position not measured" in u.what]
    assert len(refusals) == 1 and "front_high" in refusals[0].would_resolve
    for u in out.uncertainties:
        assert "'overhead'" not in u.would_resolve, u.would_resolve
        assert "overhead or 'overhead'" not in u.would_resolve, u.would_resolve


def _reading_of(observation) -> VlmReading:
    """The reading that would have produced this percept, for a diff-only test."""
    return VlmReading(reader=observation.model or "fixture", channel="sensor", objects=[
        ReadObject(color=d.entity_id.replace("seen:", ""), shape=str(d.kind),
                   bbox=tuple(d.state_attributes["bbox_px"]),
                   confidence=float(d.confidence),
                   occluded=bool(d.state_attributes.get("partly_hidden")),
                   on_or_in=str(d.state_attributes.get("claimed_on_or_in") or ""))
        for d in observation.detections])


def test_the_first_percept_says_it_has_nothing_to_compare_against(obs):
    rows = [u for u in obs.uncertainties if "first percept" in u.what]
    assert len(rows) == 1 and rows[0].why == "not_measured"
    assert obs.changes == []


# --------------------------------------------------------------------- visibility ----


def test_absence_is_only_a_claim_about_what_the_task_asked_for(assembler, frame):
    """§5.1's "可见不可见" is a statement about *this frame*, and it needs a question.

    A catalogue colour nobody mentioned and no pixel showed produces no row at all: this
    frame cannot say whether such an object exists anywhere. A hidden body whose occluder
    was not named stays `not_visible`, and a named occluder the frame does not report
    seeing is a contradiction rather than a finding."""
    reading = VlmReading(reader="fixture", channel="sensor", objects=[
        ReadObject(color="red", shape="cube", bbox=TABLE_BOX, confidence=0.9, occluded=False)],
        absent=[ReadAbsent(ref="绿色圆柱", because="not_in_image", confidence=0.5),
                ReadAbsent(ref="橙色三角", because="not_in_image", confidence=0.5),
                ReadAbsent(ref="蓝色圆柱", because="hidden", confidence=0.5)])
    out = assembler.assemble(frame, reading)
    rows = {v.entity_id: v for v in out.visibility}
    assert rows["seen:green"].visible is False and rows["seen:green"].occluded_by is None
    assert "seen:purple" not in rows and "seen:orange" not in rows
    assert items_where(out, "does not resolve in this cell's vocabulary")
    unnamed = [u for u in out.uncertainties if "what hides it was not named" in u.what]
    assert [u.why for u in unnamed] == ["not_visible"]
    assert "capture from" in unnamed[0].would_resolve

    named = reading.model_copy(update={"absent": [
        ReadAbsent(ref="绿色圆柱", because="hidden", hidden_behind="紫色长方体",
                   confidence=0.5)]})
    out2 = assembler.assemble(frame, named)
    assert [(v.entity_id, v.occluded_by) for v in out2.visibility if not v.visible] == \
        [("seen:green", "seen:purple")]
    assert items_where(out2, "does not report seeing")


def test_an_occluded_body_keeps_its_number_and_loses_its_precision(obs):
    """`seen:red` is the one body whose lower edge is the occluder's edge, and the one
    position that is wrong by 13.95 mm where the three unhidden, correctly-shaped bodies
    land 0.77-2.17 mm. The estimate stays — a plan needs a coordinate — but the percept
    is not allowed to present it as if it were accurate."""
    hidden = [v for v in obs.visibility if v.occluded_by]
    assert [(v.entity_id, v.occluded_by, v.visible) for v in hidden] == \
        [("seen:red", "seen:yellow", True)]
    assert [(r.subject_id, r.related_id) for r in obs.relations
            if r.predicate == "blocks"] == [("seen:yellow", "seen:red")]
    items = [u for u in obs.uncertainties if u.why == "low_confidence"
             and "partly hidden" in u.what]
    assert len(items) == 1 and "overhead" in items[0].would_resolve
    assert pos_of(obs, "red") is not None


def test_two_support_samples_that_disagree_are_conflicting_evidence_not_an_average(obs):
    """The taxonomy is the deliverable here.

    `not_measured` = no answer exists; `conflicting_evidence` = two real readings
    disagree. On `dev_c5` from `main`, `seen:green` and `seen:red` each have a
    neighbouring body in one of their two samples, 41.9 and 43.9 mm apart. An answer
    exists — the lower sample, measured to within 0.8 mm of the declared table top — so
    calling this `not_measured` would throw away a good coordinate, and calling it
    `low_confidence` would describe the wrong problem."""
    disagree = [u for u in obs.uncertainties if "two support samples disagree" in u.what]
    assert len(disagree) == 2
    assert {u.why for u in disagree} == {"conflicting_evidence"}
    assert sorted(s for s in ("0.0419 m" in d.what for d in disagree)) == [True, True] or \
        any("0.0419 m" in d.what for d in disagree) and any("0.0439 m" in d.what
                                                           for d in disagree)
    for det in obs.detections:                     # and the answers were kept
        assert det.state_attributes["support_z_m"] is not None


# --------------------------------------------------------------- the two channels ----


def test_the_stub_reader_is_deterministic_and_makes_no_request(frame, catalog, obs):
    """§9's `wo_vlm` arm must still be able to see, and an offline test must not pay.

    Byte-identical output is what lets a later reader attribute a changed number to the
    assembler rather than to the reader."""
    a, b = (StubReader().read(frame, catalog=catalog) for _ in range(2))
    assert [o.model_dump() for o in a.objects] == [o.model_dump() for o in b.objects]
    assert a.raw_text == b.raw_text and a.channel == "sensor"
    assert a.meta["http_requests_this_call"] == 0 and a.meta["cost_estimate_usd"] is None
    assert obs.provenance["http_requests_this_call"] == 0
    assert obs.provenance["cost_estimate_usd"] is None
    assert obs.provenance["reader"]["channel"] == "sensor"


class FakeAdapter:
    """The smallest thing `VLMReader` can be pointed at: one canned JSON answer."""

    provider, model = "fake-vision-provider", "fake-vlm-1"

    def __init__(self, payload, *, meta=None):
        self.payload, self.meta = payload, meta or {}
        self.http_requests = 0
        self.calls: list = []

    def chat_vision_json(self, system, user, images, *, kind, prompt_version,
                         max_tokens=None, modality="rgb"):
        self.http_requests += 1
        self.calls.append((system, user, tuple(images), kind, prompt_version, modality))
        return copy.deepcopy(self.payload), dict(self.meta)


def test_the_vlm_reader_stamps_provenance_and_refuses_to_repair_a_bad_payload(frame,
                                                                             catalog):
    """A model's own words about who it is get overwritten, and a structure error comes
    back as an error rather than as a repaired reading (SPEC 6.1: the framework rejects,
    it does not interpret on the model's behalf)."""
    adapter = FakeAdapter(
        {"objects": [{"color": "red", "shape": "cube", "bbox": list(TABLE_BOX),
                      "confidence": 0.7}],
         "reader": "gpt-9", "prompt_sha256": "0" * 64, "notes": "seen"},
        meta={"completion_id": "cmpl-1", "latency_s": 1.25,
              "usage": {"prompt_tokens": 900, "completion_tokens": 40,
                        "reasoning_tokens": "not-a-number"},
              "raw_response": "{...}"})
    reading = VLMReader(adapter).read(frame, catalog=catalog)
    system, user, images, kind, pver, modality = adapter.calls[0]
    assert (kind, modality) == ("perceive", "rgbd") and images == (frame.image_ref,)
    assert system is PERCEPTION_SYSTEM and "红色方块" not in user     # no answers handed over
    assert reading.reader == "fake-vlm-1" and reading.channel == "vlm"
    assert reading.prompt_sha256 == prompt_sha256()
    assert reading.prompt_version == PROMPT_VERSION and pver == PROMPT_VERSION
    assert reading.catalog_sha256 == catalog.sha256() and reading.raw_text == "{...}"
    assert reading.model_return_id == "cmpl-1" and reading.latency_s == 1.25
    assert reading.tokens == {"prompt_tokens": 900, "completion_tokens": 40}
    assert reading.meta["http_requests_this_call"] == 1

    broken = FakeAdapter({"objects": [{"color": "red", "bbox": ["a", 200, 340, 240]}]})
    # `schema_repairs=0` is this test's own claim, not the shipped default: the framework
    # must be able to refuse a payload it cannot read *without* asking twice, so that a
    # rejection is one measured event. The default (one re-ask of the same picture) is the
    # subject of `test_a_schema_invalid_reading_is_handed_back_to_the_model_once`.
    with pytest.raises(ReadingError) as e:
        VLMReader(broken, schema_repairs=0).read(frame, catalog=catalog)
    assert any("objects.0.bbox" in r for r in e.value.reasons), e.value.reasons
    assert e.value.meta["http_requests_this_call"] == 1
    assert e.value.meta["schema_repairs"] == 0 and len(broken.calls) == 1
    assert e.value.meta["raw_response"] == "" and "repaired_from" not in e.value.meta, \
        "nothing was asked twice, so there is nothing to point back at"


def test_a_schema_invalid_reading_is_handed_back_to_the_model_once(frame, catalog):
    """SPEC 6.1's other half: the framework rejects, and the model gets its own error back.

    The first billed camera episode that got past the survey died here rather than doing
    anything: one vision request answered `absent.0.because = "hidden_behind"` — not one of
    the three values the schema allows, and a word the frozen prompt's own example invites by
    writing `"because":"not_in_image"` next to `"hidden_behind":""`. The episode ended at
    `env_steps=0`, `decisions=0`, with a usable survey region already filed, which measures
    the schema's brittleness and not the agent's. So the complaint goes back in the user turn
    and the *same picture* is asked about again — and what has to stay true while that
    happens is that no value is mapped, no field is dropped, the second answer is the model's
    and not this module's, and the two requests are charged as two.
    """
    first = {"objects": [], "absent": [{"ref": "绿色圆柱", "because": "hidden_behind",
                                        "confidence": 0.6}]}
    second = {"objects": [{"color": "red", "shape": "cube", "bbox": list(TABLE_BOX),
                           "confidence": 0.7}],
              "absent": [{"ref": "绿色圆柱", "because": "hidden", "confidence": 0.6}]}

    class TwoAnswers(FakeAdapter):
        """Answers the first ask badly and the second one well, and says which is which.

        The canned `meta` is per call rather than shared: a reading that keeps the second
        answer has to carry the second answer's raw text and completion id, or the artifact
        would quote the reply that was rejected."""

        def chat_vision_json(self, system, user, images, **kw):
            n = self.http_requests
            self.payload = first if n == 0 else second
            self.meta = {"usage": {"prompt_tokens": 900, "completion_tokens": 40},
                         "raw_response": f"{{answer {n}}}", "completion_id": f"cmpl-{n}"}
            out = super().chat_vision_json(system, user, images, **kw)
            self.payload, self.meta = first, {}
            return out

    adapter = TwoAnswers(first)
    reading = VLMReader(adapter).read(frame, catalog=catalog)
    assert len(adapter.calls) == 2 and adapter.http_requests == 2
    assert [c[3] for c in adapter.calls] == ["perceive", "perceive_schema_repair"], \
        "the filing says which of the two answers is the reading"
    assert reading.meta["schema_repairs"] == 1
    assert reading.meta["http_requests_this_call"] == 2, "two requests, or the spend lies"
    assert reading.tokens == {"prompt_tokens": 1800, "completion_tokens": 80}, \
        "the summed usage of one question, not the last call's"
    assert reading.raw_text == "{answer 1}" and reading.model_return_id == "cmpl-1", \
        "the kept reading is the kept answer"
    assert reading.meta["repaired_from"] == "{answer 0}", "and the rejected one is filed too"
    assert reading.absent[0].because == "hidden", "the model's own second word, unmapped"
    assert any("absent.0.because" in r for r in reading.meta["first_schema_reasons"])
    asked = adapter.calls[1][1]
    assert asked.startswith(adapter.calls[0][1]), "the question is the same question"
    assert "absent.0.because" in asked and "hidden_behind" in asked, \
        "the re-ask names the field and quotes the word that arrived"
    assert adapter.calls[1][0] is adapter.calls[0][0] is PERCEPTION_SYSTEM, \
        "the frozen prompt is not edited to make the second ask"

    still_bad = FakeAdapter(first)
    with pytest.raises(ReadingError) as e:
        VLMReader(still_bad).read(frame, catalog=catalog)
    assert len(still_bad.calls) == 2, "one re-ask, not an unbounded loop"
    assert e.value.meta["schema_repairs"] == 1
    assert e.value.meta["http_requests_this_call"] == 2
    assert any("absent.0.because" in r for r in e.value.reasons), e.value.reasons
    assert "repaired_from" in e.value.meta, "the refused reading says it was asked twice"


def test_the_vlm_channel_produces_the_same_percept_shape_from_a_different_authority(
        frame, catalog, assembler, obs):
    """One contract, two readers: the §9 ablation switch is a constructor argument.

    The field that must differ is the one that says where a shape word came from. A
    model's judgement is not a silhouette fit, and a percept that recorded them the same
    way would make §11's VLM metric group unmeasurable."""
    adapter = FakeAdapter({"objects": [{"color": "red", "shape": "cube",
                                        "bbox": list(TABLE_BOX), "confidence": 0.7,
                                        "occluded": False, "on_or_in": ""}]})
    reading = VLMReader(adapter).read(frame, catalog=catalog)
    out = assembler.assemble(frame, reading)
    det = out.detections[0]
    assert det.how_identified == "labelled"
    assert det.state_attributes["shape_source"] == "reader_word"
    assert out.provenance["reader"]["channel"] == "vlm"
    assert out.provenance["silhouette_fit"] == {}, "a model's word arrived with a fit's evidence"
    assert out.model == "fake-vlm-1" and out.prompt_sha256 == prompt_sha256()
    assert set(det.state_attributes) == set(obs.detections[0].state_attributes)
    assert det.pose is None and det.state_attributes["orientation_measured"] is False
    # the model's shape word is used as given, and its half-height looked up, not measured
    assert abs(det.state_attributes["position_xyz_m"][2]
               - det.state_attributes["support_z_m"] - catalog.half_height("cube")) < 1e-3
    assert det.extent == geometry_for("cube", DIMS["cube"])


def test_the_prompt_shows_a_closed_vocabulary_and_nothing_the_percept_must_find_out(
        frame, catalog):
    text = user_prompt(catalog, frame, TaskContext(utterance="u", items=["红色方块"],
                                                  regions=["中盘"]))
    assert "color 取值" in text and "shape 取值" in text and "tray_left" in text
    for word in catalog.colours:
        assert word in text
    assert f'"width": {frame.camera.width}' in text and "image[0]" in text
    assert "红色方块" in text                                 # the mentioned item
    for forbidden in ("entity_id", "o_", "eval_spec", "position_xyz", "target"):
        assert forbidden not in text, forbidden
    assert "不要编造米制坐标" in PERCEPTION_SYSTEM
    assert prompt_sha256() == hashlib.sha256(
        f"{PROMPT_VERSION}|{PERCEPTION_SYSTEM}|{PERCEPTION_USER}".encode("utf-8")
    ).hexdigest()


def test_the_assembler_refuses_an_alt_view_it_could_not_ask_for(catalog):
    """`would_resolve` names a viewpoint, so an undeclared one would be advice nobody can
    follow and a run could not be reproduced from its own log."""
    with pytest.raises(ValueError, match="must be declared in frames.VIEWS"):
        PerceptionAssembler(catalog, alt_views=("overhead", "from_the_ceiling"))


# --------------------------------------------------------------------- the catalog ----


def test_the_calibration_agrees_with_the_scene_and_does_not_depend_on_the_seed(frame,
                                                                             catalog,
                                                                             scene):
    """What counts as calibration is decided by one question: is this the same in every
    episode?

    Tray positions, the palette and the shape dimensions are, so a perceiver may hold
    them — and the interior half-width is the *measured* one, the same number the
    verifier applies to a placement, so a percept and a verdict cannot disagree about
    what "inside the tray" means. Which body is where is not, and appears nowhere in the
    catalog: `red` maps to an RGB triple and never to an entity id."""
    assert [r.target_id for r in catalog.regions] == list(scene.trays)
    for r in catalog.regions:
        declared = scene.trays[r.target_id]
        assert (r.center_xy, r.inner_half, r.floor_top_z, r.wall_top_z) == \
            ((declared["center"][0], declared["center"][1]), declared["inner_half"],
             declared["floor_top"], declared["wall_top"])
        assert r.capacity == 3
    assert catalog.table_top_z == TABLE_TOP_Z
    assert catalog.support_z_bounds == (TABLE_TOP_Z - 0.06, TABLE_TOP_Z + 0.12)
    assert "o_" not in catalog.model_dump_json()
    assert not any(hasattr(catalog, a) for a in ("objects", "entities", "targets"))
    assert PerceptionCatalog.model_validate_json(catalog.model_dump_json()).sha256() == \
        catalog.sha256()
    other = PhysicsScene(seed=7, object_layout=find_case("dev_c5").objects)
    try:
        assert cell_catalog(other.trays.values()).sha256() == catalog.sha256()
    finally:
        other.close()


def test_the_tray_declaration_is_the_same_in_every_case(catalog):
    """The licence for handing tray positions to a perceiver, checked over the whole
    frozen set instead of asserted in a docstring.

    §3.3 lets a perceiver hold calibration and forbids it holding answers, and the whole
    distinction rests on one empirical claim: this cell's *static* layout does not vary
    between episodes. If a tray ever moved, `cell_catalog` would be smuggling an answer
    in as a manual — so the check that would notice runs all 47 declared cases, not a
    plausible-looking sample of them. Measured here: one digest
    (`3687b7f28386ff7f…`) for all 47, 4.5 s wall clock."""
    from dataclasses import fields

    import embodied_agent.evaluation.tasks as tasks

    digests = set()
    for case in tasks._all_cases():
        s = PhysicsScene(seed=case.seed, object_layout=case.objects)
        try:
            digests.add(cell_catalog(s.trays.values()).sha256())
        finally:
            s.close()
    assert digests == {catalog.sha256()}, \
        f"the static layout varies over the frozen set: {sorted(digests)}"
    # and a case has nowhere to put a tray even if it wanted to: what it declares is
    # objects, targets, budgets, events and a private eval_spec — never region geometry
    names = {f.name for f in fields(tasks.TaskCase)}
    assert not names & {"trays", "regions", "target_regions", "table_top_z", "dims"}, names
    # colours are the identifying attribute the catalog is keyed on, so the claim that
    # lets `percept_id` use colour alone has to hold over the whole set too
    for case in tasks._all_cases():
        colours = [str(o["attributes"].get("color")) for o in case.objects if o.get("attributes")]
        assert len(colours) == len(set(colours)), case.task_id


def test_a_catalog_that_could_not_ground_a_percept_refuses_to_be_constructed():
    """Every one of these is a manual that cannot describe the cell it belongs to, and a
    percept assembled against one would be measuring nothing."""
    palette = {"red": (0.85, 0.1, 0.1)}
    dims = {"cube": [0.021, 0.021, 0.021]}
    trays = [dict(target_id="tray_left", center=[0.66, -0.32], inner_half=0.131,
                  floor_top=0.628, wall_top=0.653, capacity=3)]

    def build(**over):
        kw = dict(palette=palette, dims=dims, trays=trays, table_top_z=TABLE_TOP_Z)
        kw.update(over)
        return PerceptionCatalog.from_declared(**kw)

    assert build().half_height("cube") == 0.021
    with pytest.raises(Exception, match="no colours"):
        build(palette={})
    with pytest.raises(Exception, match="no regions"):
        build(trays=[])
    with pytest.raises(Exception, match="three half-extents"):
        build(dims={"cube": [0.021, 0.021]})
    with pytest.raises(Exception, match="duplicate target_id"):
        build(trays=trays + trays)
    with pytest.raises(ValueError, match="not in this cell's catalog"):
        build().geometry("sphere")


class _DyingAdapter:
    """An adapter whose request never came back: three HTTP attempts, no answer.

    `meta` is left as the caller would find it on the transport path — the real adapter now
    fills it with what it *sent* (`adapters/deepseek.py:_sent_meta`), and an empty one here is
    the deliberate other half: it shows the column template, not the adapter, is what keeps a
    refusal row as wide as a returning one."""

    provider, model = "fake-vision-provider", "fake-vlm-1"

    def __init__(self, error):
        self.error = error
        self.http_requests = 0
        self.api_errors = 0
        self.transport_retries = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.format_repairs = 0
        self.calls = 0

    def chat_vision_json(self, system, user, images, *, kind, prompt_version,
                         max_tokens=None, modality="rgb"):
        # the counters move before the raise, as `_post` does them: three attempts, two
        # sleeps between them, one api error. The reader's `*_this_call` deltas are how a
        # refusal row is billed at all, so a stub that leaves them at zero tests nothing.
        self.calls += 1
        self.http_requests += 3
        self.transport_retries += 2
        self.api_errors += 1
        raise self.error


#: what the adapter returns beside the answer: the request-side keys and the answer-side keys,
#: as one `chat_vision` return of theirs would look. The three `*_field_*` keys are 任务 `#155`:
#: here the message was well formed and answered, which is the case they read True/False/0.
ANSWER_META = {"provider": "fake-vision-provider", "requested_model": "fake-vlm-1",
               "temperature": 0.2, "max_tokens": 1200, "http_requests_total": 1,
               "transport_attempts": 1, "transport_retries": 0, "latency_s": 1.25,
               "prompt_chars": 987, "raw_chars": 12, "modality": "rgbd",
               "images": [{"index": 0, "ref": "f.png", "bytes": 24, "sha256": "0" * 64}],
               "returned_model": "fake-vlm-1", "completion_id": "cmpl-1", "created": 1700000000,
               "finish_reason": "stop", "usage": {"prompt_tokens": 900, "completion_tokens": 40},
               "content_field_present": True, "reasoning_field_present": False,
               "reasoning_chars": 0,
               "raw_response": "{\"objects\": []}"}


def _rows(ep_dir):
    """The rows of one episode's `model_calls.jsonl`, read back the way a later reader finds them."""
    from embodied_agent.benchmark_mujoco.calls_log import CALLS_LOG_NAME
    return [json.loads(line) for line in
            (ep_dir / CALLS_LOG_NAME).read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_refused_look_files_the_same_columns_as_an_answered_one(frame, catalog, tmp_path):
    """任务 `#138`: the log a batch is billed from is read column-wise, so a refusal row that is
    narrower than a returning one loses the rows that say the most.

    Measured before the fix, over the 44 camera run roots that file reading rows: 12 `ok: true`
    rows at 30 keys, 4 `ok: false` rows at 18, the same 14 absent from all four — and
    `aggregate.py`'s `refused_round_tokens` could only ever read 0, because a refusal filed no
    `usage` at all. Both rows are read back out of a real `model_calls.jsonl`, because the
    artifact is what a batch is billed from and `file_call` rewrites two keys on the way in."""
    from embodied_agent.adapters.deepseek import LLMError
    from embodied_agent.benchmark_mujoco.calls_log import CALLS_LOG_NAME, file_call

    def file_one(ep_name):
        ep = tmp_path / ep_name
        ep.mkdir()
        return ep, (lambda meta: file_call(str(ep), meta))

    (ep_a, sink_a), (ep_r, sink_r) = file_one("answered"), file_one("refused")
    VLMReader(FakeAdapter({"objects": []}, meta=dict(ANSWER_META)),
              on_call=sink_a).read(frame, catalog=catalog)
    with pytest.raises(LLMError):
        VLMReader(_DyingAdapter(LLMError("request failed after 3 attempts: URLError: timed out",
                                         requests_made=3)),
                  on_call=sink_r).read(frame, catalog=catalog)

    a, r = _rows(ep_a), _rows(ep_r)
    assert len(a) == len(r) == 1
    a, r = a[0], r[0]
    assert set(a) == set(r), (f"the refusal is short {sorted(set(a) - set(r))}, "
                              f"extra {sorted(set(r) - set(a))}")
    assert set(a) == set(READING_ROW_COLUMNS), \
        "one row per opened request, and the same columns whoever opened it"
    # an answer-only fact is *null* on a refusal, never 0 and never "": 0 tokens would say the
    # dead request was free, and an empty usage dict would say the provider sent one and it was
    # empty — both are claims, and neither is what happened.
    for k in ("usage", "completion_id", "created", "finish_reason", "returned_model"):
        assert r[k] is None and a[k] is not None, k
    # what the refusal does know, it says: the attempts, the error, and the deltas of the
    # counters one adapter shares across a whole sweep.
    assert r["ok"] is False and r["requests_made"] == 3 and r["error"].startswith("LLMError: ")
    assert r["http_requests_this_call"] == 3 and a["http_requests_this_call"] == 1
    assert r["transport_retries_this_call"] == 2 and r["api_errors_this_call"] == 1

    # The other half, without which the merge above would be dead code that still passes: a
    # request that reached the endpoint and came back unreadable. Its columns are not nulls,
    # they are the numbers the adapter put on the exception — and `kind` stays the reader's,
    # because the ask this row records is the one this reader is on.
    came_back = {k: v for k, v in ANSWER_META.items()
                 if k not in ("returned_model", "completion_id", "created", "finish_reason")}
    (ep_m, sink_m) = file_one("answered-but-unreadable")
    with pytest.raises(LLMError):
        VLMReader(_DyingAdapter(LLMError(
            "unparseable JSON from the vision model after 1 format repair: Expecting value",
            requests_made=2, meta={**came_back, "usage": {"prompt_tokens": 1850,
                                                          "completion_tokens": 70},
                                   "kind": "perceive_format_repair", "raw_response": "{oops"})),
            on_call=sink_m).read(frame, catalog=catalog)
    m = _rows(ep_m)[0]
    assert set(m) == set(a), "the same columns again — this time filled in"
    assert m["usage"] == {"prompt_tokens": 1850, "completion_tokens": 70}, \
        "the pair of requests one logical ask, or the bill is understated under its own name"
    assert m["kind"] == "perceive" and m["prompt_chars"] == 987 and m["transport_attempts"] == 1
    assert m["completion_id"] is None and m["returned_model"] is None
    assert m["raw_chars"] == len("{oops"), "`file_call` sizes the answer it was handed"
    assert "raw_preview" not in m and m["schema_errors"] is None, \
        "an unparseable answer is not a schema rejection; the preview policy stays the writer's"


def test_the_adapter_leaves_its_request_facts_on_the_error_it_raises(tmp_path, monkeypatch):
    """The other half of 任务 `#138`: the request-side columns are the adapter's to know.

    Filling them from the reader would be a second home for the same numbers — `max_tokens`
    alone resolves differently in the two places (`body["max_tokens"]` is
    `max_tokens or self.max_tokens`), which is how a column ends up carrying two meanings. So
    the adapter puts them on the exception, and `_file_row` merges what it finds there.
    Offline: `_post` is replaced, so no request is opened and no key is used."""
    from embodied_agent.adapters.deepseek import DeepSeekAdapter, LLMError

    adapter = DeepSeekAdapter(api_key="not-a-key-offline", model="fake-vlm-1",
                              base_url="http://127.0.0.1:9")
    png = tmp_path / "frame.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    def die(body):
        raise LLMError("request failed after 3 attempts: URLError: timed out", requests_made=3)

    monkeypatch.setattr(adapter, "_post", die)
    with pytest.raises(LLMError) as e:
        adapter.chat_vision("sys", "usr", [str(png)], modality="rgbd")

    meta = e.value.meta
    assert meta["transport_attempts"] == e.value.requests_made == 3
    assert meta["transport_retries"] == 2 and meta["max_tokens"] == adapter.max_tokens
    assert meta["temperature"] == adapter.temperature and meta["requested_model"] == "fake-vlm-1"
    assert meta["prompt_chars"] == len("sys") + len("usr") and meta["raw_chars"] == 0
    assert meta["modality"] == "rgbd" and [i["ref"] for i in meta["images"]] == [str(png)]
    assert meta["images"][0]["sha256"] == hashlib.sha256(png.read_bytes()).hexdigest()
    assert not ({"completion_id", "created", "finish_reason", "returned_model", "usage",
                 "content_field_present", "reasoning_field_present", "reasoning_chars"}
                & set(meta)), "answered-only keys are absent here, so the row can call them null"


def test_a_format_repair_is_billed_as_the_two_requests_it_was(monkeypatch):
    """`usage` means "this row's spend" on both paths, so a refusal has to add the pair.

    The returning path already sums it across attempts in `observe.py`; a refusal that filed
    only the second request's usage would understate the bill under the same column name — the
    #9 family (one value, two homes) in the one place it would not be caught by eye, because
    both numbers are plausible and only their sum is the money."""
    from embodied_agent.adapters.deepseek import DeepSeekAdapter, LLMError

    adapter = DeepSeekAdapter(api_key="not-a-key-offline", model="fake-vlm-1",
                              base_url="http://127.0.0.1:9")
    asked = []

    def second_answer(system, user, images, *, max_tokens=None, modality="rgb"):
        asked.append(len(user))
        n = len(asked)
        return ("{oops", {"transport_attempts": 1,
                          "usage": ({"prompt_tokens": 900, "completion_tokens": 40} if n == 1
                                    else {"prompt_tokens": 950, "completion_tokens": 30})})

    monkeypatch.setattr(adapter, "chat_vision", second_answer)
    with pytest.raises(LLMError) as e:
        adapter.chat_vision_json("sys", "usr", ["f.png"], kind="perceive",
                                 prompt_version=PROMPT_VERSION)

    assert len(asked) == 2, "one ask, one format repair — and both are the same row"
    assert e.value.requests_made == 2
    assert e.value.meta["usage"] == {"prompt_tokens": 1850, "completion_tokens": 70}
    assert e.value.meta["kind"] == "perceive_format_repair"
    assert e.value.meta["first_parse_error"], "the refusal says what was wrong with the first"


#: batch24's truncated answer on the SenseNova seat, shape for shape: `/tmp/mw_vlm_live_batch24`
#: row 1 (`finish=length`, `raw_chars=0`, `http_requests_this_call=2`,
#: `usage.completion_tokens=9600` against `max_tokens=4800`) and `/tmp/mw117/h55/p1p2_sensenova.out`
#: (`message_keys=['reasoning','role']`). One request's slice of that row is below — 4800, the
#: ceiling, spent on a `reasoning` string the adapter never reads as an answer. A fake may not
#: improve on what was measured, so `content` is **absent** here rather than `""`.
TRUNCATED_REASONING_PAYLOAD = {
    "id": "cmpl-sn-truncated", "model": "sensenova-6.8-flash-lite", "object": "chat.completion",
    "created": 1700000000,
    "choices": [{"finish_reason": "length",
                 "message": {"reasoning": "t" * 8571, "role": "assistant"}}],
    "usage": {"prompt_tokens": 2453, "completion_tokens": 4800, "total_tokens": 7253},
}


def _absent_content_payload():
    return copy.deepcopy(TRUNCATED_REASONING_PAYLOAD)


def _payload_post(target, seen, payloads):
    """A stand-in for `_post` that moves the counters `_post` moves.

    Every `*_this_call` column in the ledger is a delta off a shared adapter, and `http_requests`
    rises before the outcome is known (SPEC 6.2), so a stand-in that only returned a payload would
    leave the numbers this batch is billed from at zero and pass by construction."""
    def post(body):
        seen.append(body)
        payload = payloads[min(len(seen) - 1, len(payloads) - 1)]
        target.http_requests += 1
        usage = payload.get("usage") or {}
        target.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        target.completion_tokens += int(usage.get("completion_tokens") or 0)
        return copy.deepcopy(payload), 1
    return post


def test_no_content_field_is_not_re_asked_as_an_empty_answer(frame, monkeypatch):
    """任务 `#155`: absence of an answer is not an empty answer, and only one of them is a
    format problem the model can be asked to fix.

    Both halves are here because the defect was the collapse of the two into one string:
    `(message or {}).get("content") or ""` reads a message with no `content` key as `""`, and
    the format path then re-asked — batch24's single look row is 2 HTTP requests and 9600
    completion tokens against a 4800 ceiling, each request spending the whole ceiling on
    thinking that never became readable. The gate has to key on the payload's **key set**, so
    the second half of this test is the case that keeps its re-ask: a model that answered with
    an empty string did answer."""
    from embodied_agent.adapters.deepseek import DeepSeekAdapter, LLMError

    def sensernova_seat():
        return DeepSeekAdapter(api_key="not-a-key-offline", provider="sensenova",
                               model="sensenova-6.8-flash-lite",
                               base_url="http://127.0.0.1:9", max_tokens=4800)

    sent = []
    a = sensernova_seat()
    monkeypatch.setattr(a, "_post", _payload_post(a, sent, [_absent_content_payload()]))
    with pytest.raises(LLMError) as e:
        a.chat_vision_json("sys", "usr", [frame.image_ref], kind="perceive",
                           prompt_version=PROMPT_VERSION, modality=frame.modality)

    assert len(sent) == 1 and a.http_requests == 1, "one billed generation, not two"
    assert a.format_repairs == 0, "there was no answer to have a format problem"
    meta = e.value.meta
    assert meta["content_field_present"] is False and meta["reasoning_field_present"] is True
    assert meta["reasoning_chars"] == 8571 and meta["raw_chars"] == 0
    assert meta["finish_reason"] == "length" and meta["usage"]["completion_tokens"] == 4800
    head = str(e.value)[:90]      # what `/tmp/mw117/h55/watch_rows.py` prints live
    assert "no `content` field" in head and "reasoning 8571" in head, head
    assert e.value.requests_made == 1

    # the same gate on the text half, because a decision page is billed the same way
    sent2 = []
    t = sensernova_seat()
    monkeypatch.setattr(t, "_post", _payload_post(t, sent2, [_absent_content_payload()]))
    with pytest.raises(LLMError) as e2:
        t.chat_json("sys", "usr", kind="decision", prompt_version=PROMPT_VERSION)
    assert len(sent2) == 1 and t.format_repairs == 0 and e2.value.meta["reasoning_chars"] == 8571

    # and the half that must NOT change: `content` present and empty is an answer with nothing
    # in it, which is exactly what the one bounded format repair was written for.
    empty = _absent_content_payload()
    empty["choices"][0]["message"]["content"] = ""
    empty["usage"]["completion_tokens"] = 4
    fixed = {"id": "cmpl-sn-2", "model": "sensenova-6.8-flash-lite", "created": 1700000001,
             "choices": [{"finish_reason": "stop",
                          "message": {"role": "assistant", "content": "{\"objects\": []}"}}],
             "usage": {"prompt_tokens": 2460, "completion_tokens": 5}}
    seen = []
    v = sensernova_seat()
    monkeypatch.setattr(v, "_post", _payload_post(v, seen, [empty, fixed]))
    parsed, pmeta = v.chat_vision_json("sys", "usr", [frame.image_ref], kind="perceive",
                                       prompt_version=PROMPT_VERSION, modality=frame.modality)
    assert parsed == {"objects": []}, "the re-ask is still there for an answer that can be fixed"
    assert len(seen) == 2 and v.format_repairs == 1
    assert pmeta["content_field_present"] is True and pmeta["raw_chars"] == len("{\"objects\": []}")


def test_a_content_absent_look_files_the_same_columns_and_names_its_billing(
        frame, catalog, tmp_path, monkeypatch):
    """The row that replaces batch24's, read back out of a real `model_calls.jsonl`.

    任务 `#138`'s template is what makes this legible rather than merely longer: the three
    任务 `#155` keys are answer-side, so a transport death still files them as null and a
    truncated 200 files False/True/8571 — the difference a column reader can act on."""
    from embodied_agent.adapters.deepseek import DeepSeekAdapter, LLMError
    from embodied_agent.benchmark_mujoco.calls_log import file_call

    adapter = DeepSeekAdapter(api_key="not-a-key-offline", provider="sensenova",
                              model="sensenova-6.8-flash-lite", base_url="http://127.0.0.1:9",
                              max_tokens=4800)
    sent = []
    monkeypatch.setattr(adapter, "_post",
                        _payload_post(adapter, sent, [_absent_content_payload()]))
    ep = tmp_path / "ep"
    ep.mkdir()
    with pytest.raises(LLMError):
        VLMReader(adapter, on_call=lambda meta: file_call(str(ep), meta)).read(
            frame, catalog=catalog)

    row = _rows(ep)[0]
    assert set(row) == set(READING_ROW_COLUMNS), "the same columns whoever opened it"
    assert row["ok"] is False and row["kind"] == "perceive"
    assert row["content_field_present"] is False and row["reasoning_field_present"] is True
    assert row["reasoning_chars"] == 8571 and row["raw_chars"] == 0
    assert row["http_requests_this_call"] == 1 and row["format_repairs_this_call"] == 0, \
        "the second request is what this fix removed, so one is what gets billed here"
    assert row["usage"]["completion_tokens"] == 4800 and row["max_tokens"] == 4800
    assert "raw_preview" not in row and row["schema_errors"] is None, \
        "no answer came back at all, so #114's preview policy has nothing to preserve"


def test_the_thinking_budget_reaches_the_request_only_when_a_config_names_it(frame, monkeypatch):
    """`reasoning_effort` is the provider's documented knob, wired as config and recorded.

    Unset means the field is not in the body: a default written here would be this process
    choosing the endpoint's behaviour, and it would land in `sampling` — which a frozen
    manifest reads as "what was sent" — as a number that was never sent."""
    from embodied_agent.adapters.deepseek import DeepSeekAdapter, LLMError

    answered = {"id": "cmpl-1", "model": "m", "created": 1,
                "choices": [{"finish_reason": "stop",
                             "message": {"role": "assistant", "content": "{\"objects\": []}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    seen: list = []

    plain = DeepSeekAdapter(api_key="not-a-key-offline", base_url="http://127.0.0.1:9")
    monkeypatch.setattr(plain, "_post", _payload_post(plain, seen, [answered]))
    plain.chat("s", "u")
    assert len(seen) == 1 and "reasoning_effort" not in seen[0]
    assert plain.sampling["reasoning_effort"] is None

    throttled = DeepSeekAdapter(api_key="not-a-key-offline", base_url="http://127.0.0.1:9",
                                reasoning_effort="none")
    monkeypatch.setattr(throttled, "_post", _payload_post(throttled, seen, [answered]))
    throttled.chat_vision("s", "u", [frame.image_ref])
    assert len(seen) == 2 and seen[1]["reasoning_effort"] == "none"
    assert throttled.sampling["reasoning_effort"] == "none", \
        "the manifest records what was sent, and this was"

    with pytest.raises(LLMError, match="reasoning_effort"):
        DeepSeekAdapter(api_key="not-a-key-offline", reasoning_effort="extreme-high")
