"""P1-a contract tests: the sensor channel is a camera, not a state reader.

Two kinds of test, and the distinction matters. The ones that call the simulator
check that *this camera model is the renderer's own* — an off-by-sign here mirrors
every percept, and a mirrored percept looks perfectly plausible in a picture. The
rest run on synthetic depth maps that no renderer would produce, because the
behaviour worth pinning down is the refusal: what the geometry channel says when a
surface is not there (§5.1 不确定信息标记).

`test_the_perception_package_reads_no_privileged_state` is the §3.3 line drawn in
code: information flow is a property of the source text, so it is checked as one
rather than promised in a docstring.
"""
from __future__ import annotations

import hashlib
import math
import os

import numpy as np
import pytest

from embodied_agent.core.contracts import footprint_half_xy_from_quat
from embodied_agent.core.scene import TABLE_TOP_Z, PhysicsScene
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.perception import frames as frames_mod
from embodied_agent.perception import geometry as geom_mod
from embodied_agent.perception.camera import (CameraSpec, column_major, depth_to_meters,
                                              look_at, meters_to_depth)
from embodied_agent.perception.frames import (SensorFrame, VIEWS, camera_for, capture,
                                              load_depth, save_frame, view_spec)
from embodied_agent.perception.geometry import (AMBIGUOUS_SUPPORT_M, bbox_depth_stats,
                                                estimate_centre, support_surface_z)

PERCEPTION_DIR = os.path.dirname(frames_mod.__file__)

# the geometry of the pose `work/p1_sensor_check.py` measured 1.1 mm median error on
MAIN = "main"


@pytest.fixture(scope="module")
def scene():
    case = find_case("dev_c5")
    s = PhysicsScene(seed=case.seed, object_layout=case.objects)
    yield s
    s.close()


@pytest.fixture(scope="module")
def main_frame(scene, tmp_path_factory):
    out = tmp_path_factory.mktemp("p1a_frame")
    camera = camera_for(MAIN)
    frame, rgb, depth = capture(scene, str(out), camera, "main", sim_time=scene.sim_time)
    return frame, rgb, depth


def window_for_z(camera: CameraSpec, px: float, py: float, z: float) -> float:
    """Window depth whose back-projection at this pixel lands at height `z`.

    Synthetic depth maps are built with this instead of a renderer so a test can
    describe a floor that a camera could never show: background on one side of an
    object, a neighbouring body on the other."""
    lo, hi = 0.0, 1.0
    flo = camera.unproject(px, py, lo).z - z
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if (camera.unproject(px, py, mid).z - z) * flo > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ------------------------------------------------- the camera model is the renderer's -----


def test_view_matrix_equals_the_simulators_own(scene):
    import pybullet as p

    cam = camera_for(MAIN)
    theirs = p.computeViewMatrix(cameraEyePosition=cam.eye.as_list(),
                                 cameraTargetPosition=cam.target.as_list(),
                                 cameraUpVector=cam.up.as_list(), physicsClientId=scene.cid)
    assert np.allclose(np.array(column_major(cam.view_matrix)), np.array(theirs), atol=1e-6)


def test_projection_matrix_equals_the_simulators_own(scene):
    import pybullet as p

    cam = camera_for(MAIN)
    theirs = p.computeProjectionMatrixFOV(fov=cam.fov_deg, aspect=cam.aspect,
                                          nearVal=cam.near_m, farVal=cam.far_m,
                                          physicsClientId=scene.cid)
    assert np.allclose(np.array(column_major(cam.projection_matrix)), np.array(theirs), atol=1e-6)


@pytest.mark.parametrize("view_id", sorted(VIEWS))
def test_project_then_unproject_round_trips_for_every_declared_view(view_id):
    cam = camera_for(view_id)
    for point in ([0.40, -0.10, 0.66], [0.66, 0.32, 0.63], [0.52, 0.02, 0.72]):
        px, py, d = cam.project(point)
        back = cam.unproject(px, py, d)
        assert np.allclose([back.x, back.y, back.z], point, atol=1e-6)


def test_project_agrees_with_the_matrices_it_declares():
    """`project` must be the analytic form of `projection_matrix @ view_matrix`.

    The two are written independently in this package — one as a pinhole formula, one
    as a matrix builder — and nothing else checks that they say the same thing. A
    divergence survives every other test here: `project`→`unproject` round-trips
    because both halves carry the same mistake, and the matrices still equal the
    simulator's because they are built correctly. `fx` once multiplied `focal_px` by
    the aspect, which the projection matrix had already divided out, and this is the
    test that names it."""
    for view_id in sorted(VIEWS):
        cam = camera_for(view_id)
        for point in ([0.40, -0.10, 0.66], [0.66, 0.32, 0.63], [0.52, 0.02, 0.72]):
            clip = cam.projection_matrix @ (cam.view_matrix @ np.append(point, 1.0))
            ndc_x, ndc_y = float(clip[0] / clip[3]), float(clip[1] / clip[3])
            from_matrix = ((ndc_x + 1.0) / 2.0 * cam.width,
                           (1.0 - ndc_y) / 2.0 * cam.height)   # row 0 is the top edge
            assert (from_matrix[0], from_matrix[1]) == pytest.approx(
                cam.project(point)[:2], abs=1e-6), (view_id, point)
    # square pixels: `fov_deg` is the vertical field, and the matrix already divides x
    # by the aspect, so a second aspect factor here stretches the image horizontally
    cam = camera_for("main")
    assert cam.fx == pytest.approx(cam.fy)
    assert cam.intrinsics()["fx"] == pytest.approx(cam.intrinsics()["fy"])


def test_a_projected_point_lands_where_the_rendered_image_shows_it(scene, main_frame):
    """The check with teeth, against pixels rather than against this file's own maths.

    Every test above compares one part of the model to another part of the model, so a
    mistake written into both passes them all and still describes a stretched picture.
    A colour mask is the only thing in the room whose geometry comes from the renderer:
    the pixels a body paints must lie inside the box this camera predicts for that body,
    and their centre must sit on the centre this camera predicts. Measured on `dev_c5`
    from `main`, the tightest hue threshold still leaves every box inside its predicted
    outline by 1-9 px per edge, and the horizontal centres agree to ~1 px.
    """
    from embodied_agent.evaluation.tasks import PALETTE
    from embodied_agent.perception.segment import colour_mask

    frame, rgb, depth = main_frame
    camera = frame.camera
    rows = []
    for eid, d in scene.objects.items():
        pos, orn = scene.object_pose(eid)
        colour = str(d["attributes"]["color"])
        geom = d["geometry"]
        hx, hy = footprint_half_xy_from_quat(geom, orn)
        hz = geom.half_h_vertical
        pts = np.array([[pos[0] + a * hx, pos[1] + b * hy, pos[2] + e * hz]
                        for a in (-1, 1) for b in (-1, 1) for e in (-1, 1)])
        proj = np.array([camera.project(p) for p in pts])
        predicted = [proj[:, 0].min(), proj[:, 1].min(), proj[:, 0].max(), proj[:, 1].max()]
        mask = colour_mask(rgb, PALETTE[colour][:3])
        ys, xs = np.nonzero(mask)
        assert xs.size > 200, f"{eid}: {xs.size} pixels is not a body"
        seen = [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]
        slack = 3.0                       # antialiasing, one pixel of rounding
        assert seen[0] >= predicted[0] - slack and seen[2] <= predicted[2] + slack, \
            f"{eid}: mask x {seen[0]}..{seen[2]} leaves the predicted x " \
            f"{predicted[0]:.1f}..{predicted[2]:.1f}"
        assert seen[1] >= predicted[1] - slack and seen[3] <= predicted[3] + slack, \
            f"{eid}: mask y {seen[1]}..{seen[3]} leaves the predicted y " \
            f"{predicted[1]:.1f}..{predicted[3]:.1f}"
        # a horizontal scale error moves the centre but keeps the box plausible-looking
        rows.append(abs(0.5 * (seen[0] + seen[2]) - 0.5 * (predicted[0] + predicted[2])))
        # the ray through the body's own centre must hit the body, not the table behind
        px, py, _ = camera.project(pos)
        z = depth_to_meters(float(depth[int(round(py)), int(round(px))]),
                            camera.near_m, camera.far_m)
        centre_range = float(np.linalg.norm(np.array(camera.eye.as_list()) - np.array(pos)))
        # nearer than the centre by less than twice the largest half-diagonal: the near
        # face, not the far side and not the background
        assert 0.01 < centre_range - z < 2.0 * math.hypot(hx, hy, hz), \
            f"{eid}: depth {z:.4f} m against centre range {centre_range:.4f} m"
    assert max(rows) < 8.0, f"worst horizontal centre offset {max(rows):.1f} px"


def test_window_depth_is_monotonic_and_hits_both_planes():
    near, far = 0.05, 3.0
    zs = [depth_to_meters(d, near, far) for d in np.linspace(0.0, 1.0, 21)]
    assert zs == sorted(zs)
    assert zs[0] == pytest.approx(near) and zs[-1] == pytest.approx(far)
    for d in (0.13, 0.5, 0.91):
        assert depth_to_meters(meters_to_depth(zs[7], near, far), near, far) == pytest.approx(zs[7])


def test_look_at_rejects_an_up_parallel_to_the_view_direction():
    with pytest.raises(ValueError, match="parallel to the view direction"):
        look_at([0.5, 0.0, 1.2], [0.5, 0.0, 0.6], [0.0, 0.0, 1.0])


def test_look_at_rejects_a_camera_with_nothing_to_look_at():
    with pytest.raises(ValueError, match="coincide"):
        look_at([0.5, 0.0, 1.2], [0.5, 0.0, 1.2], [0.0, 0.0, 1.0])


def test_project_refuses_a_point_behind_the_camera():
    cam = camera_for(MAIN)
    with pytest.raises(ValueError, match="behind camera"):
        cam.project([2.0, 0.0, 0.6])


def test_camera_for_rejects_an_undeclared_view():
    with pytest.raises(ValueError, match="undeclared view"):
        camera_for("where_is_my_kite")


def test_the_declared_main_view_is_the_measured_pose():
    """The frozen table and the check script must not drift apart silently."""
    cam = camera_for(MAIN)
    assert (cam.eye.x, cam.eye.y, cam.eye.z) == (1.06, -0.30, 1.24)
    assert (cam.width, cam.height, cam.fov_deg) == (800, 600, 40.0)


# ---------------------------------------------------------------- the frame record -----


def test_a_frame_records_the_pixels_it_is_claiming(main_frame):
    frame, rgb, depth = main_frame
    with open(frame.image_ref, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == frame.image_sha256
    assert frame.image_bytes == os.path.getsize(frame.image_ref)
    assert frame.depth_shape == [depth.shape[0], depth.shape[1]] == [frame.camera.height,
                                                                     frame.camera.width]
    assert frame.camera == camera_for(MAIN)
    assert frame.modality == "rgbd" and frame.renderer == "ER_TINY_RENDERER"
    assert frame.depth_range_m[0] < frame.depth_range_m[1]


def test_a_logged_frame_reproduces_its_own_metric_geometry(main_frame):
    """No simulator, no privileged channel: the run directory alone re-derives points.

    `SensorFrame` carries the `CameraSpec` that made it, so a reader of a logged
    episode can turn any pixel and depth back into metres exactly as the agent did —
    which is what makes a VLM pose auditable after the fact instead of a number to
    be taken on trust."""
    frame, _, depth = main_frame
    again = SensorFrame.model_validate_json(frame.model_dump_json())
    reloaded = load_depth(again)
    # the channel is stored at float32, half the bytes for a window depth whose
    # useful precision is far coarser than that
    assert reloaded.dtype == np.float32 and reloaded.shape == depth.shape
    assert np.allclose(reloaded, depth, atol=1e-7)
    samples = [(300.0, 220.0, 0.5), (frame.camera.cx, frame.camera.cy, float(depth[300, 400])),
               (10.0, 590.0, float(depth[590, 10]))]
    for px, py, d in samples:
        a = again.camera.unproject(px, py, d)
        b = frame.camera.unproject(px, py, d)
        assert (a.x, a.y, a.z) == pytest.approx((b.x, b.y, b.z), abs=1e-12)
    payload = frame.to_payload()
    assert "pixels" not in payload and payload["image_sha256"] == frame.image_sha256
    # the logged dict is enough to rebuild the camera exactly: refs and numbers, no
    # simulator state and no pixel arrays
    assert CameraSpec.model_validate(payload["camera"]) == frame.camera


def test_save_frame_refuses_depth_that_another_camera_made(scene, tmp_path):
    cam = camera_for(MAIN)
    with pytest.raises(ValueError, match="does not match camera"):
        save_frame(str(tmp_path), cam, np.zeros((cam.height, cam.width, 3), np.uint8),
                   np.zeros((cam.height - 2, cam.width), float), "bad")


def test_an_rgb_only_frame_says_it_has_no_metric_channel(scene, tmp_path):
    cam = camera_for(MAIN)
    frame = save_frame(str(tmp_path), cam, np.zeros((cam.height, cam.width, 3), np.uint8),
                       np.full((cam.height, cam.width), 0.5), "rgb_only", keep_depth=False)
    assert frame.modality == "rgb" and frame.depth_ref is None
    assert any("no depth channel" in s for s in frame.limitations)
    assert any("flat shading" in s for s in frame.limitations)
    with pytest.raises(ValueError, match="kept no depth channel"):
        load_depth(frame)


# ------------------------------------------------------------ the geometry estimator -----


def _background(cam):
    """A depth map that sees nothing: every pixel at the far plane."""
    return np.full((cam.height, cam.width), 1.0)


def _surface_at(cam, depth, col, row, z):
    """Write the one depth value that back-projects this pixel to height `z`."""
    depth[int(row), int(col)] = window_for_z(cam, float(col), float(row), z)
    return float(depth[int(row), int(col)])


# The one box every synthetic case below shares, and where `support_surface_z` looks
# when it is given that box: one pixel above the last row (`y1 - 1`), `stand_off_px`
# outside each flank. Writing the samples anywhere else tests a pixel nobody reads.
BOX = (300, 200, 340, 240)
SAMPLE_ROW = BOX[3] - 1
LEFT_COL, RIGHT_COL = BOX[0] - 3, BOX[2] + 3


def test_bbox_depth_stats_reports_the_gap_that_proves_a_solid_object():
    cam = camera_for(MAIN)
    floor = window_for_z(cam, 320.0, 220.0, TABLE_TOP_Z)
    depth = np.full((cam.height, cam.width), floor)
    # a box a model reports for an object on a table holds *both* surfaces: the
    # object's own near face and the floor behind it. The gap is the evidence that
    # the box contains a solid body rather than a patch of empty table.
    depth[200:220, 300:340] = window_for_z(cam, 320.0, 210.0, TABLE_TOP_Z + 0.10)
    st = bbox_depth_stats(depth, (300, 200, 339, 239))
    assert st["pixels"] == 40 * 40
    assert st["near_window"] < st["median_window"] < st["far_window"]


def test_support_surface_z_is_unknown_when_both_sides_are_background():
    cam = camera_for(MAIN)
    z, ev = support_surface_z(cam, _background(cam), BOX)
    assert z is None
    assert ev["samples"] == [] and "background" in ev["why"]


def test_the_lower_sample_wins_and_a_disagreement_is_flagged_not_averaged():
    cam = camera_for(MAIN)
    depth = _background(cam)
    _surface_at(cam, depth, LEFT_COL, SAMPLE_ROW, TABLE_TOP_Z)
    _surface_at(cam, depth, RIGHT_COL, SAMPLE_ROW, TABLE_TOP_Z + 0.04)   # a neighbour's flank
    z, ev = support_surface_z(cam, depth, BOX)
    assert z == pytest.approx(TABLE_TOP_Z, abs=1e-4)           # the mean would say +20 mm
    assert ev["chosen"] == "lowest"
    assert ev["ambiguous"] is True
    assert ev["sample_spread_m"] > AMBIGUOUS_SUPPORT_M
    assert [s["side"] for s in ev["samples"]] == ["left", "right"]


def test_a_single_side_sample_is_recorded_as_one_sample():
    cam = camera_for(MAIN)
    depth = _background(cam)
    _surface_at(cam, depth, 43, SAMPLE_ROW, TABLE_TOP_Z)
    # a box whose left edge is the frame edge: only one flank is inside the picture
    z, ev = support_surface_z(cam, depth, (0, 200, 40, 240), stand_off_px=3)
    assert z == pytest.approx(TABLE_TOP_Z, abs=1e-4)
    assert [s["side"] for s in ev["samples"]] == ["right"]
    assert ev["ambiguous"] is False


def test_estimate_centre_solves_a_centre_no_surface_was_seen_at():
    """The estimator's claim, on a depth map no renderer produced.

    The box's own patch is filled with the depth of the *centre plane*, which is not
    a surface a camera can show — so the only way this can return a point at
    `TABLE_TOP_Z + half_height` is by solving the ray against the measured support."""
    cam = camera_for(MAIN)
    depth = _background(cam)
    _surface_at(cam, depth, LEFT_COL, SAMPLE_ROW, TABLE_TOP_Z)
    _surface_at(cam, depth, RIGHT_COL, SAMPLE_ROW, TABLE_TOP_Z)
    depth[BOX[1]:BOX[3], BOX[0]:BOX[2] + 1] = window_for_z(cam, 320.0, 219.5, TABLE_TOP_Z + 0.05)
    centre, ev = estimate_centre(cam, depth, BOX, half_height_m=0.05)
    assert centre is not None, ev
    assert centre.z == pytest.approx(TABLE_TOP_Z + 0.05, abs=1e-3)
    assert 0.30 < centre.x < 0.80 and abs(centre.y) < 0.45
    assert ev["support_z_m"] == pytest.approx(TABLE_TOP_Z, abs=1e-3)
    assert ev["ambiguous_support"] is False
    assert ev["box_near_m"] == pytest.approx(ev["centre_view_range_m"], abs=0.02)


def test_estimate_centre_refuses_when_no_sample_sits_at_a_declared_height():
    cam = camera_for(MAIN)
    depth = _background(cam)
    for col in (LEFT_COL, RIGHT_COL):                 # a hole where the table should be
        _surface_at(cam, depth, col, SAMPLE_ROW, 0.10)
    bounds = (TABLE_TOP_Z - 0.06, TABLE_TOP_Z + 0.12)
    centre, ev = estimate_centre(cam, depth, BOX, half_height_m=0.05, z_bounds=bounds)
    assert centre is None
    assert "a height this cell declares" in ev["why"]
    # the refusal is auditable rather than terse: what the picture actually showed, and
    # the declared range that excludes it, are both in the evidence
    assert [round(float(d["z_m"]), 2) for d in ev["support_surface"]["discarded"]] \
        == [0.10, 0.10]


def test_a_sample_off_the_table_cannot_veto_the_sample_that_is_on_it():
    """The measured failure this gate exists for.

    `work/p1_perceive_check.py` found this on the `overhead` view: the green body
    stands 5 cm from the table's edge, its left support sample misses the table and
    lands 48 cm lower, and taking the *lowest* sample — the rule that correctly refuses
    a neighbour's flank — chose that surface. Because the position solve and the
    silhouette fit are both placed on the support, the one bad pixel cost the body its
    position *and* its shape (every candidate scored ~0.3 IoU against every other).
    Gating candidates by the declared heights, before the minimum, keeps the neighbour
    rule and returns the estimate the other sample supported."""
    cam = camera_for(MAIN)
    depth = _background(cam)
    _surface_at(cam, depth, LEFT_COL, SAMPLE_ROW, 0.10)        # off the table edge
    _surface_at(cam, depth, RIGHT_COL, SAMPLE_ROW, TABLE_TOP_Z)  # the table itself
    bounds = (TABLE_TOP_Z - 0.06, TABLE_TOP_Z + 0.12)
    z, ev = support_surface_z(cam, depth, BOX, z_bounds=bounds)
    assert z == pytest.approx(TABLE_TOP_Z, abs=1e-4)
    assert [s["side"] for s in ev["samples"]] == ["right"]
    assert ev["discarded"][0]["side"] == "left"
    assert "nothing stands on it" in ev["discarded"][0]["why"]
    assert ev["ambiguous"] is False          # the rival was never a candidate
    # the box's own patch holds a surface too, so the consistency check has something
    # to agree with: this test is about the support gate, not about an empty box
    depth[BOX[1]:BOX[3], BOX[0]:BOX[2] + 1] = window_for_z(cam, 320.0, 219.5, TABLE_TOP_Z + 0.05)
    centre, cev = estimate_centre(cam, depth, BOX, half_height_m=0.05, z_bounds=bounds)
    # and the same two pixels now support a centre instead of destroying one
    assert centre is not None, cev
    assert centre.z == pytest.approx(TABLE_TOP_Z + 0.05, abs=1e-3)


def test_an_explicit_support_is_still_checked_against_the_declared_heights():
    """The caller-supplied path keeps its own refusal.

    `support_surface_z` cannot gate what it was never asked to sample: when a caller
    hands in a height, out-of-bounds is a contradiction with the scene declaration and
    says so as `rejected`, in a different field from the sampling refusal above."""
    cam = camera_for(MAIN)
    depth = _background(cam)
    _surface_at(cam, depth, LEFT_COL, SAMPLE_ROW, TABLE_TOP_Z)
    _surface_at(cam, depth, RIGHT_COL, SAMPLE_ROW, TABLE_TOP_Z)
    centre, ev = estimate_centre(cam, depth, BOX, half_height_m=0.05, support_z=0.10,
                                 z_bounds=(TABLE_TOP_Z - 0.06, TABLE_TOP_Z + 0.12))
    assert centre is None
    assert "outside the declared scene heights" in ev["rejected"]
    assert "support surface unmeasurable" in ev["why"]


def test_estimate_centre_refuses_when_the_answer_and_the_box_disagree():
    cam = camera_for(MAIN)
    depth = _background(cam)
    _surface_at(cam, depth, LEFT_COL, SAMPLE_ROW, TABLE_TOP_Z)
    _surface_at(cam, depth, RIGHT_COL, SAMPLE_ROW, TABLE_TOP_Z)
    depth[BOX[1]:BOX[3] + 1, BOX[0]:BOX[2] + 1] = 0.9999    # the box says: nothing but far plane
    centre, ev = estimate_centre(cam, depth, BOX, half_height_m=0.05, support_z=TABLE_TOP_Z)
    assert centre is None
    assert "outside the box's own depth span" in ev["rejected"]


def test_estimate_centre_returns_the_body_centre_not_the_seen_surface(scene, main_frame):
    """The claim the whole perception phase rests on, on a real rendered frame.

    The box is the *privileged* corner projection — the best box any VLM could ever
    report — so the number below is the ceiling on metric accuracy for this view, not
    an estimate of it. Everything the percept then says about position is derived from
    the picture, and this test says how far that derivation is from the truth."""
    frame, _, depth = main_frame
    from embodied_agent.core.contracts import footprint_half_xy_from_quat

    camera = frame.camera
    errors, supports = [], []
    for eid, d in scene.objects.items():
        pos, orn = scene.object_pose(eid)
        hx, hy = footprint_half_xy_from_quat(d["geometry"], orn)
        hz = d["geometry"].half_h_vertical
        pts = np.array([[pos[0] + sx * hx, pos[1] + sy * hy, pos[2] + sz * hz]
                        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
        proj = np.array([camera.project(pt) for pt in pts])
        bbox = (proj[:, 0].min(), proj[:, 1].min(), proj[:, 0].max(), proj[:, 1].max())
        centre, ev = estimate_centre(camera, depth, bbox, half_height_m=hz,
                                     z_bounds=(TABLE_TOP_Z - 0.06, TABLE_TOP_Z + 0.12))
        assert centre is not None, (eid, ev)
        errors.append(float(np.hypot(centre.x - pos[0], centre.y - pos[1])))
        supports.append(ev["support_z_m"])
    median_mm = float(np.median(errors)) * 1000.0
    assert median_mm < 5.0, f"median xy error {median_mm:.1f} mm"
    assert max(errors) * 1000.0 < 25.0
    # every support was found on the table, not on a neighbour or the far plane
    assert all(abs(s - TABLE_TOP_Z) < 0.005 for s in supports)


# ------------------------------------------------------------- information flow -----


def test_the_perception_package_reads_no_privileged_state():
    forbidden = ("getBasePositionAndOrientation", "getLinkState", "getClosestPoints",
                 "getContactPoints", "getOverlappingObjects", "getDynamics",
                 "getJointState", "getVisualShapeData", "getCollisionShapeData",
                 "getAABB", "queryObject", "img[4]", "object_pose", "build_world_state",
                 "scene.objects", "scene.trays", "held_bodies", "contact_partners",
                 "eval_spec", "ground_truth")
    sources = {name: open(os.path.join(PERCEPTION_DIR, name)).read()
               for name in sorted(os.listdir(PERCEPTION_DIR)) if name.endswith(".py")}
    assert set(sources) >= {"camera.py", "frames.py", "geometry.py"}
    for name, text in sources.items():
        for token in forbidden:
            assert token not in text, f"{name} reads privileged state: {token}"
        for line in text.splitlines():
            if "import pybullet" in line:
                assert line.startswith(" "), f"{name}: pybullet must stay a lazy import"
            if "p.get" in line and "def " not in line:
                assert "getCameraImage" in line, f"{name}: non-image simulator call: {line}"


def test_the_only_simulator_call_asks_for_an_image(scene):
    """`render_rgb_depth` takes pixels and nothing else: same camera, same frame,
    and the returned arrays are exactly what a JPEG of the scene would show."""
    camera = camera_for(MAIN)
    frame, rgb, depth = capture(scene, "/tmp/v02_p1a_selftest", camera, "probe",
                                sim_time=scene.sim_time)
    assert rgb.dtype == np.uint8 and rgb.shape == (camera.height, camera.width, 3)
    assert depth.shape == (camera.height, camera.width)
    assert depth.min() >= 0.0 and depth.max() <= 1.0
    assert frame.image_ref.endswith("probe.png")
