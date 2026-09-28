"""The camera channel's own contracts: what a frame may say, and what it may not.

`embodied_agent/benchmark_mujoco/perceive.py` builds the target region of this bench from
pixels, and the claim it makes is a narrow one — *the survey's box selects the search, the depth
image finds the hole*. This file is where that claim is attacked, test by test:

* the estimator's arithmetic, on a synthetic depth frame where the right answer is known because
  it was drawn there (no renderer, no episode, no `data` — the simulator enters only through the
  quaternion conversion at the top of `aperture_frame`);
* the sign and the topology, by building the three frames that look like a hole and are not one:
  a bump in front of the wall, a gap at the wall's edge, and a bigger recess whose border is an
  occluder rather than the wall;
* the survey's own §10 exposure, on the real renderer: no site marker in the picture, no `data`
  read by the declaration, and the declared camera the camera that rendered the frame.
* the rod and the grasp question, on bands `_synthetic_rod` draws and on canned axis fits: what a
  `measured_axis` reading has to look like before it may be a metre claim at all, and what the
  picture-only hold instrument says when it is one — three claims (`resting`, `carried`, and the
  silence that keeps `"unknown"` in the snapshot) and two refusals the real camera actually
  produced: a fragment of a depth image that was never drawn, and end pixels past the window's
  own "nothing here" line.
* the aim seam, on a rod a test holds and on a rod the real arm has just lifted: which of an
  object's two measured ends counts as the one that has to arrive, and the proof that the
  perceived arm and the simulator's caliper answer that with the *same rule*. They did not until
  batch 4; `test_the_perceived_aim_and_the_caliper_pick_the_same_end_of_a_carried_rod` is the
  regression, and the pose it is checked at is the pose where the two rules disagree — at every
  layout's rest pose they happen to agree, which is why this seam survived 35 layouts unnoticed.

The synthetic face is described by `FACE_DECL` below, which is a *transcription* of what
`wall_declaration` returns for `peg-insert-side-v3`. That is a risk — a transcription can drift —
so `test_the_synthetic_face_is_the_scene_file` compares the two on a real backend and fails if any
field moved. The numbers in it were measured, not chosen: they are the model arrays of the box's
own body subtree, identical in all 35 layouts.

Skipped, not failed, on an interpreter without metaworld: the simulator half needs
`/home/czx/mwvenv/bin/python`, and the desktop suite must stay runnable without it.
"""
from __future__ import annotations

import importlib.util
import json
import os
from typing import Any, Optional

os.environ.setdefault("MUJOCO_GL", "osmesa")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

#: The estimator is simulator-free in every other sense: no renderer, no `data`, no episode. It is
#: not *package*-free, because `aperture_frame` turns the box's declared quaternion into a rotation
#: matrix with MuJoCo's own `mju_quat2Mat`, so the whole file needs `/home/czx/mwvenv`.
pytest.importorskip("mujoco", reason="`_quat_mat` is MuJoCo's quaternion conversion")

from embodied_agent.benchmark_mujoco.perceive import (  # noqa: E402
    APERTURE_FLAT_GRADIENT_M_PER_PX, APERTURE_MIN_MODE_PX, APERTURE_PLANE_TOL_M,
    AXIS_END_Z_BOUNDS, AXIS_MIN_SPAN_M, FAR_M, HELD_SOURCE, HOLD_REST_MARGIN_M, NEAR_M,
    OBJECT_ID, PEG_RGB, PEG_ROD_LENGTH_M, TABLE_TOP_Z_M, MuJoCoRig, MujocoPerceiver,
    _camera_rays, _frame_metres, _request_errors, aperture_frame, build_catalog, fit_aperture,
    flat_surface_region, grounding_table, hold_rest_surfaces, measured_axis,
    region_from_wall_centre, surface_gradient, wall_declaration, wall_rotation, wall_silhouette)
from embodied_agent.benchmark_mujoco.executor import MujocoSkillExecutor, _dist  # noqa: E402
from embodied_agent.perception.camera import CameraSpec  # noqa: E402
from embodied_agent.perception.frames import view_spec  # noqa: E402

HAS_MW = importlib.util.find_spec("metaworld") is not None
needs_mw = pytest.mark.skipif(not HAS_MW,
                              reason="the renderer needs a started MetaWorld instance")

#: the box of `peg-insert-side-v3`, as `wall_declaration` publishes it. `hole_local` is the
#: `hole` site in the box's frame, `visual_centre_local`/`half_extents` the AABB of the *visual*
#: geoms (groups 0-1 — the collision hull is group 4 and no camera sees it), and the quaternion a
#: 90° turn about z, which is the only one the 35 layouts ever use. `hole_offset_in_wall` and
#: `hole_axis_world` are in **world** axes, which is why they look swapped against `hole_local`:
#: the box's local -y face is the world +x one, and that rotation is the fact the whole channel
#: rests on. `hole_radius_m` is the site's own modelled half-size, `model.site_size` = 0.005 — it
#: is the verifier's tolerance and nothing else; the opening a camera sees through the wall is
#: several times wider, which is why `fit_aperture` measures it instead of declaring it.
FACE_DECL: dict[str, Any] = {
    "half_extents": [0.1, 0.1, 0.1005],
    "visual_centre_local": [0.0, 0.0, 0.0995],
    "hole_local": [0.0, -0.096, 0.13],
    "hole_offset_in_wall": [0.0, 0.0, 0.0305],
    "hole_axis_world": [1.0, -0.0008, 0.0],
    "body_quat_wxyz": [0.707388, 0.0, 0.0, 0.706825],
    "hole_radius_m": 0.005,
}

#: a camera on the +x axis looking down it, which is the one arrangement where the declared
# normal (`[1, 0, 0]` for `FACE_DECL`) points *at* the eye: the wall faces the camera, so a hole
# in it is a place a ray goes past the plane. Swap that sign and every test below fails, which is
# the point — the first run of the estimator had it the wrong way round and called the robot's own
# arm a 2,977 px aperture.
def _camera(width: int = 240, height: int = 240, eye=(1.0, 0.0, 0.1)) -> CameraSpec:
    return view_spec("synthetic", [float(v) for v in eye], [0.0, 0.0, 0.1], up=[0.0, 0.0, 1.0],
                     fov_deg=45.0, width=width, height=height, near_m=NEAR_M, far_m=FAR_M)


def _synthetic_face(camera: CameraSpec, *, recesses=(), bars=(), wall_extent=0.20,
                    plane_x: float = 0.0, recess_m: float = 0.020,
                    bar_m: float = 0.050) -> np.ndarray:
    """A depth image of a flat wall with holes in it, generated rather than rendered.

    Each `recesses` entry is `((y, z), radius)` — a circular patch of the wall plane that is
    `recess_m` *past* it, which is what a bore looks like from the outside. Each `bars` entry is
    `((y, z), (half_y, half_z))` — a rectangle `bar_m` *in front* of the wall, which is what the
    robot's arm looks like. Pixels whose ray leaves the `wall_extent`-half-square of the plane get
    no surface at all, so the wall has an edge and the edge can be tested against.

    The returned array is in the window convention `MuJoCoRig.render` declares, built with the
    same three lines of arithmetic, so what these tests measure is the estimator and not a
    private idea of what a depth value means.
    """
    eye, d = _camera_rays(camera)
    b = d[0]                                    # n = [1, 0, 0], so b is the ray's x component
    a = float(eye[0])
    t = (plane_x - a) / b                        # view-axis distance to the wall plane
    pts = eye[:, None, None] + d * t[None, :, :]
    yy, zz = pts[1], pts[2]

    surface = t.copy()
    wall = (np.abs(yy) <= wall_extent) & (np.abs(zz - 0.1) <= wall_extent)
    for (cy, cz), r in recesses:
        inside = ((yy - cy) ** 2 + (zz - cz) ** 2) <= r * r
        surface = np.where(inside, (plane_x - recess_m - a) / b, surface)
    for (cy, cz), (hy, hz) in bars:
        inside = (np.abs(yy - cy) <= hy) & (np.abs(zz - cz) <= hz)
        surface = np.where(inside, (plane_x + bar_m - a) / b, surface)
    hit = wall & (surface > camera.near_m) & (surface < camera.far_m)
    return _window(camera, surface, hit)


def _window(camera: CameraSpec, metres, hit) -> np.ndarray:
    """View-axis metres and a hit mask -> the window convention the estimators are called with.

    The one place this conversion is written, so a frame generated here and a frame rendered by
    `MuJoCoRig` cannot mean different things at the same number.
    """
    near, far = camera.near_m, camera.far_m
    z = np.clip(metres, near, far)
    z_ndc = (z * (far + near) - 2.0 * near * far) / (z * (far - near))
    return np.where(hit, (z_ndc + 1.0) / 2.0, 1.0).astype(np.float32)


def _synthetic_slant(camera: CameraSpec, *, from_row: int, slope_m_per_px: float,
                     plane_x: float = 0.0) -> np.ndarray:
    """A depth image of a surface that is *not* parallel to the declared face.

    Its coordinate along the declared normal (`[1, 0, 0]` for `FACE_DECL`, as in `_synthetic_face`)
    moves `slope_m_per_px` with the image row from `from_row` down: a floor slanting away from the
    wall, which is the thing the surveyed box actually stands on. Every pixel of it is a different
    plane, and no camera looking at a wall has any use for it — except that the plane histogram
    takes any surface wide enough, and a slant crossing the frame is wide enough.
    """
    eye, d = _camera_rays(camera)
    rows = np.arange(camera.height, dtype=float)[:, None] * np.ones((1, camera.width))
    h = plane_x + slope_m_per_px * np.maximum(rows - from_row, 0.0)
    metres = (h - float(eye[0])) / d[0]
    return _window(camera, metres, np.isfinite(metres) & (metres > camera.near_m)
                   & (metres < camera.far_m))


def _whole(camera: CameraSpec) -> list[float]:
    return [0.0, 0.0, float(camera.width - 1), float(camera.height - 1)]


# ------------------------------------------------------- the face, from the file --


def test_the_face_decomposition_agrees_with_itself():
    """`perp + depth_along * normal == mouth_offset_world`, and the normal is the hole's axis.

    Two independent routes to the declared mouth — one along the face axes, one out through the
    wall — that disagree would mean the depth test below has the wrong sign. `aperture_frame`
    asserts both, so calling it is the test; what this adds is the numbers, because a passing
    assert that never fires is not the same as an assert that cannot fire.
    """
    face = aperture_frame(FACE_DECL)
    assert face["axis"] == 1 and face["sign"] == -1.0        # the hole is in the -y face
    assert np.allclose(face["normal"], [1.0, 0.0, 0.0], atol=1e-3)
    # 1e-3, not 1e-6: the modelled quaternion is a 90° turn to six decimals
    # (`[0.707388, 0, 0, 0.706825]`), so the face axes are world y and world z to 8e-4, and an
    # exact-zero expectation would be a claim about the scene file that the scene file does not make
    assert np.allclose(face["a1"], [0.0, 1.0, 0.0], atol=1e-3)
    assert np.allclose(face["a2"], [0.0, 0.0, 1.0], atol=1e-3)
    assert face["span_half"] == [0.1, 0.1005]
    assert face["depth_along"] == pytest.approx(0.1, abs=1e-9)
    assert np.allclose(face["mouth_offset_world"], [0.1, -0.0001, 0.0305], atol=1e-4)


def test_the_region_is_tighter_than_the_scorers_and_comes_from_the_hole():
    """`inner_half` is the `hole` site's own modelled half-size, so the tolerance is 20 mm."""
    region = region_from_wall_centre(FACE_DECL, [-0.30, 0.50, 0.0])
    assert region.inner_half == FACE_DECL["hole_radius_m"]
    assert region.center_xy == pytest.approx((-0.30, 0.50), abs=1e-4)
    assert region.floor_top_z == pytest.approx(0.0305, abs=1e-4)
    # `MujocoVerifier._tolerance = inner_half + min(half_y, half_z)`; the peg's own half-sizes are
    # 0.015, so this channel's verdict is 20 mm wide against the benchmark's 70 mm.
    assert region.inner_half + 0.015 == pytest.approx(0.020)


# ------------------------------------------------------------ the estimator, drawn --


def test_a_recessed_disc_is_found_where_it_was_drawn():
    camera = _camera()
    centre = (0.02, 0.12)
    depth = _synthetic_face(camera, recesses=[(centre, 0.03)])
    mouth, ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    assert mouth is not None, ev.get("why")
    assert np.allclose(mouth, [0.0, centre[0], centre[1]], atol=1e-3), (mouth, ev["chosen"])
    assert ev["chosen"]["ring_wall"] == pytest.approx(1.0, abs=1e-6)
    assert ev["chosen"]["recess_mm"] == pytest.approx(20.0, abs=1.0)
    # the along-axis coordinate is free, and this says so with a number: the point is on the
    # *fitted wall plane*, not on the bore's far wall 20 mm behind it
    assert abs(mouth[0]) < 1e-4


def test_a_bump_in_front_of_the_wall_is_not_a_hole():
    """The sign test, as a picture. The same disc, nearer instead of recessed, is the mistake the
    first run of this estimator made — and it made it on 2,977 px of robot arm."""
    camera = _camera()
    depth = _synthetic_face(camera, recesses=[((0.02, 0.12), 0.03)], recess_m=-0.020)
    mouth, ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    assert mouth is None
    assert "none of them carries" in ev["why"]


def test_a_gap_at_the_walls_edge_is_not_a_hole():
    """Everything beyond the box is recessed — the table by 100 mm, the far wall by a metre — and
    a rule that only asks "is it behind the plane" answers with whichever of them is biggest. The
    enclosure test is what separates a hole from an edge, and `binary_fill_holes` cannot fill a
    region connected to the border of the array."""
    camera = _camera()
    depth = _synthetic_face(camera, wall_extent=0.08)          # a small wall, open all round
    mouth, ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    assert mouth is None
    assert "none of them carries" in ev["why"]


def test_an_occluder_across_the_hole_still_finds_the_hole_and_says_what_it_cost():
    """The arm crossing the mouth is the common case, not the rare one: it is what layouts 5, 9,
    23 and 27 of the real bench look like, and what layouts 24 and 31 look like when the arm also
    reaches the box's edge.

    Enclosure survives it — the occluder is part of the blocker, so the visible part of the hole
    is still surrounded — and the *centre* does not: the mean of a half-disc sits 4r/3π ≈ 12.7 mm
    off the axis. Both halves are asserted, because the one that is reassuring is the one people
    forget to check.
    """
    camera = _camera()
    centre = (0.02, 0.12)
    depth = _synthetic_face(camera, recesses=[(centre, 0.03)],
                            bars=[((centre[0] - 0.05, 0.12), (0.05, 0.06))])
    mouth, ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    assert mouth is not None, ev.get("why")
    chosen = ev["chosen"]
    assert chosen["ring_wall"] < 1.0, "an occluder in the border must show up in the border"
    shift = float(np.hypot(mouth[1] - centre[0], mouth[2] - centre[1]))
    assert 0.004 < shift < 0.020, (shift, chosen)
    assert chosen["px"] < 300, "the island should be the visible part of the disc, not all of it"


def test_the_surrounded_recess_wins_over_the_bigger_one():
    """Ranking, measured rather than assumed.

    Two fully recessed discs, the *small* one surrounded by wall and the large one with an
    occluder across its near half. The visible part of the large one is a semicircle of ~330 px —
    bigger than the small disc's ~210 — and most of its border is the occluder rather than the
    wall. Selecting by area — the rule an earlier revision used, and the one that puts the answer
    150-230 mm away on layouts 4, 8, 10, 11, 28 and 30 of the real bench — takes the semicircle.
    Selecting by border fraction first takes the small disc, which is the hole.
    """
    camera = _camera()
    depth = _synthetic_face(camera, recesses=[((-0.06, 0.12), 0.05), ((0.06, 0.12), 0.028)],
                            bars=[((-0.11, 0.12), (0.05, 0.06))])
    mouth, ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    assert mouth is not None, ev.get("why")
    assert np.allclose(mouth, [0.0, 0.06, 0.12], atol=2e-3), (mouth, ev["candidates"])
    chosen = ev["chosen"]
    assert chosen["px"] < max(c["px"] for c in ev["candidates"]), (
        "the answer is the biggest enclosed island, and on the real bench the biggest enclosed "
        "island is a gap at the box's edge, not the hole")
    assert any(c["px"] > chosen["px"] and c["ring_wall"] < chosen["ring_wall"]
               for c in ev["candidates"][1:]), (
        "no rival is both larger and less surrounded, so this frame does not test the order the "
        "sort claims: " + json.dumps(ev["candidates"]))


def test_a_wall_that_straddles_a_histogram_bin_is_one_wall():
    """The plane search is a histogram, and a noiseless surface is a delta function that can land
    exactly on a bin edge — so one wall can supply two modes, each of whose ±4 mm band holds the
    whole of it.

    That is not a hypothetical: before the refined offsets were compared, the frame below reported
    two planes and two candidates, identical to the pixel, and the candidate list that is supposed
    to show the runner-up islands showed the winner twice. A wall is now counted once, and the
    islands on it are the ones that get the list.
    """
    camera = _camera()
    depth = _synthetic_face(camera, recesses=[((0.02, 0.12), 0.03)])
    mouth, ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    assert mouth is not None, ev.get("why")
    assert len(ev["planes"]) == 1, ev["planes"]
    assert len({tuple(c["mouth_m"]) for c in ev["candidates"]}) == len(ev["candidates"]), \
        ev["candidates"]


def test_a_surface_that_is_not_parallel_to_the_declared_face_is_not_a_wall():
    """The frame a surveyed box actually stands on, drawn instead of rendered.

    A wall with a bore and a floor slanting away from it at 1.2 mm per pixel over the bottom 70 rows
    of a 160x160 frame. `flat_surface_region` keeps 3,773 of the 14,896 pixels carrying a surface,
    and all but 77 of those are wall. The rest of this test is what that filter is worth: without it
    the histogram reports **five** wall-sized plane modes on this frame, four of which are the floor
    — a 1.2 mm/px slant fills an 8 mm-wide ±4 mm band with ~1,100 pixels, which is nearly three
    times `APERTURE_MIN_MODE_PX`, so every few rows of the floor qualifies as a wall in its own
    right. And the slant moves the value the wall is reported at: the pixels of it that happen to
    fall within 4 mm of x = 0 join the wall's band, so the declared face comes back at 0.27 mm
    instead of 0.00 mm and its `wall_px` is 4,232 where the wall is 3,669.
    """
    camera = _camera(160, 160)
    wall = _synthetic_face(camera, recesses=[((0.02, 0.12), 0.03)])
    rows = np.arange(camera.height)[:, None] >= 90
    depth = np.where(rows, _synthetic_slant(camera, from_row=90, slope_m_per_px=1.2e-3), wall)

    eye, d = _camera_rays(camera)
    metres = _frame_metres(depth, camera)
    h = float(eye[0]) + metres * d[0]
    live = np.isfinite(metres) & (metres > camera.near_m * 1.001) & (metres < camera.far_m * 0.9999)
    grad = surface_gradient(h, live)
    assert round(float(np.median(grad[rows & live])) * 1000, 2) == 1.2, "the slant, as drawn"
    assert float(np.median(grad[~rows & live])) == 0.0, "the wall is flat, by construction"

    region, flat = flat_surface_region(camera, depth, decl=FACE_DECL)
    assert flat == {"gradient_m_per_px": APERTURE_FLAT_GRADIENT_M_PER_PX, "kept_px": 3773,
                    "frame_surface_px": 14896}, flat
    assert int((region & rows).sum()) == 77, "the filter keeps the wall and almost none of the floor"

    loose, loose_ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    floors = sorted(p["plane_mm"] for p in loose_ev["planes"][1:])
    assert floors == [26.27, 38.28, 50.38, 74.38], loose_ev["planes"]
    assert all(p["wall_px"] >= APERTURE_MIN_MODE_PX for p in loose_ev["planes"][1:]), \
        "these are accepted as walls, not merely listed: " + str(loose_ev["planes"])
    assert loose_ev["planes"][0]["plane_mm"] == 0.27, "the slant drags the declared face with it"

    tight, tight_ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL, region=region)
    assert len(tight_ev["planes"]) == 1, tight_ev["planes"]
    assert tight_ev["planes"][0]["plane_mm"] == 0.0
    assert tight_ev["planes"][0]["wall_px"] == 3669
    assert tight_ev["surface_px"] == 3773 == tight_ev["region_px"]
    assert tight is not None and np.allclose(tight, [0.0, 0.02, 0.12], atol=2e-3), (tight, loose)
    assert loose is not None and np.allclose(tight, loose, atol=1e-3), (
        "the region has to survive the frame it improves on: the hole is the same hole either way, "
        "and a filter that only works by changing the answer is not a filter")


def test_a_region_of_every_pixel_is_the_measurement_that_no_region_is():
    """`region=None` is an implementation branch, not a second estimator, so the branch has to be
    invisible: the same answer, the same `surface_px`, and a difference confined to the two fields
    that exist to say which branch ran.
    """
    camera = _camera()
    depth = _synthetic_face(camera, recesses=[((0.02, 0.12), 0.03)])
    plain_mouth, plain = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    all_mouth, every = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL,
                                    region=np.ones(depth.shape, dtype=bool))
    assert plain_mouth == all_mouth
    differs = {k for k in set(plain) | set(every) if plain.get(k) != every.get(k)}
    assert differs == {"region_filtered", "region_px"}, differs
    assert plain["surface_px"] == every["surface_px"] == 13225
    assert (plain["region_px"], every["region_px"]) == (None, 57600)
    # and the other end of the same argument is not the same measurement: an empty region leaves no
    # wall at all, which is the failure the survey's refusal has to be able to tell apart from "no
    # hole in this picture" -- see `SURVEY_FRAME_SURFACE_FLOOR` at the call site.
    none_mouth, none_ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL,
                                       region=np.zeros(depth.shape, dtype=bool))
    assert none_mouth is None and none_ev["surface_px"] == 0
    assert "carry a surface" in none_ev["why"], none_ev["why"]


def test_a_camera_on_the_far_side_of_the_declared_face_refuses_instead_of_answering():
    """发现十六's gate, on the frame that shows what it is for.

    The same generated wall, seen from x = -1 m instead of x = +1 m. The picture is a wall-sized flat
    surface with a recessed disc in it — every field the estimator used to rank candidates on is
    satisfied — and it is the *back* of the wall: `h = a + z*b` with `a = eye·n = -1` and the surface
    at `h = 0`, so every ray that met it travelled away from the declared normal. Two planes of that
    shape are refused and nothing is answered. Before this gate the same frame returned a confident
    mouth, and on the real `corner` camera that is what put the aim 196 mm inside a box: the two
    strictly parallel faces of the box are indistinguishable to a plane histogram, and so is a wall
    seen from behind.
    """
    behind = _camera(eye=(-1.0, 0.0, 0.1))
    depth = _synthetic_face(behind, recesses=[((0.02, 0.12), 0.03)])
    mouth, ev = fit_aperture(behind, depth, _whole(behind), decl=FACE_DECL)
    assert mouth is None, "the back of a wall is not a hole in its face: " + str(ev["candidates"])
    assert ev["planes"] == [] and ev["modes"] == 2, ev["planes"]
    assert ev["planes_refused_back_facing"] == 2, ev
    assert "far side of the declared normal" in ev["why"], ev["why"]
    # the region rule cannot rescue it, because the wall really is flat: every pixel survives G
    region, flat = flat_surface_region(behind, depth, decl=FACE_DECL)
    assert flat == {"gradient_m_per_px": APERTURE_FLAT_GRADIENT_M_PER_PX, "kept_px": 13225,
                    "frame_surface_px": 13225}
    second, second_ev = fit_aperture(behind, depth, _whole(behind), decl=FACE_DECL, region=region)
    assert second is None and second_ev["planes_refused_back_facing"] == 2
    # the same wall from the front: the gate is present, counted, and does not fire
    front = _camera()
    front_depth = _synthetic_face(front, recesses=[((0.02, 0.12), 0.03)])
    third, third_ev = fit_aperture(front, front_depth, _whole(front), decl=FACE_DECL)
    assert third is not None and third_ev["planes_refused_back_facing"] == 0, third_ev


def test_a_surface_rejected_for_not_being_parallel_still_closes_a_hole():
    """The region answers "which pixels may be this wall". It does not answer "what is a hole".

    The first version of this mask shipped with the second meaning attached: a pixel outside it was
    read as a pixel with *no surface* there, so it joined the recessed set and became part of a hole.
    Measured on the production camera's own layout 0, 34 of the mouth island's 144 enclosing ring
    pixels are rejected by the region — 29 standing in front of the wall, 5 in its own band on a
    surface too steep to be parallel — so the ring gained 34 gaps,
    `binary_fill_holes` stopped enclosing the opening, and a hole this channel measures on every
    other frame was refused (`/tmp/mw_ship_frame_probe.py`). The other reading of the
    same mistake is the one 发现十一 was going to be paid with: `recess_mm` is `h_face - h` clipped at
    zero, so a hole assembled out of pixels standing *in front* of the plane measures 0.0 mm deep by
    construction, and every health count in the candidate gets **better** while the answer moves.

    So the arm crossing the mouth is the test object. It is solid, it is not parallel, and rejecting
    it may remove its plane from the histogram and nothing else. The last block is the same boundary
    from the other side: pixels *behind* the wall's plane, rejected for curving, must not move the
    answer either — what that one does move is `ring_wall`, which is a report about the wall.
    """
    camera = _camera()
    centre = (0.02, 0.12)
    depth = _synthetic_face(camera, recesses=[(centre, 0.03)],
                            bars=[((centre[0] - 0.05, 0.12), (0.05, 0.06))])
    eye, d = _camera_rays(camera)
    h = float(eye[0]) + _frame_metres(depth, camera) * d[0]
    in_front = h > APERTURE_PLANE_TOL_M

    loose, loose_ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL)
    kept, kept_ev = fit_aperture(camera, depth, _whole(camera), decl=FACE_DECL, region=~in_front)
    assert int(in_front.sum()) == 57600 - 56585, int(in_front.sum())
    assert len(loose_ev["planes"]) == 2, "the arm is its own wall plane when nothing rejects it"
    assert kept_ev["planes"][0]["plane_mm"] == 0.0 and len(kept_ev["planes"]) == 1, \
        "dropping the arm's plane out of the candidates is the mask's whole job"

    a, b = loose_ev["chosen"], kept_ev["chosen"]
    assert (a["px"], a["recess_mm"], a["ring_wall"]) == (124, 20.0, 0.596), a
    assert (b["px"], b["recess_mm"], b["ring_wall"]) == (124, 20.0, 0.596), b
    assert kept == pytest.approx(loose, abs=1e-9), \
        "the bore behind the arm is the same hole whether or not the arm is a candidate wall"

    # ...and the pixels the shipped filter really does drop: the bore's own curved rim, which stands
    # *behind* the wall's plane. Rejecting them leaves the island where it is and lowers how much of
    # its ring counts as wall.
    clean = _synthetic_face(camera, recesses=[(centre, 0.03)])
    plain, plain_ev = fit_aperture(camera, clean, _whole(camera), decl=FACE_DECL)
    _, dd = _camera_rays(camera)
    t_wall = (0.0 - float(eye[0])) / dd[0]
    pts = np.asarray(eye)[:, None, None] + dd * t_wall[None, :, :]
    disc = ((pts[1] - centre[0]) ** 2 + (pts[2] - centre[1]) ** 2) <= 0.035 ** 2
    rim, rim_ev = fit_aperture(camera, clean, _whole(camera), decl=FACE_DECL, region=~disc)
    c = rim_ev["chosen"]
    assert int(disc.sum()) == 322, int(disc.sum())
    assert (c["px"], c["recess_mm"]) == (238, 20.0), c
    assert (plain_ev["chosen"]["px"], plain_ev["chosen"]["ring_wall"]) == (238, 1.0)
    assert rim == pytest.approx(plain, abs=1e-9), (rim, plain)
    assert c["ring_wall"] == pytest.approx(0.491), \
        "a rim the mask rejects is no longer wall, and the health count is allowed to say so"


def test_the_answer_does_not_move_when_the_region_does():
    """The whole reason this estimator replaced the outline fit.

    The fit converted 1 px of reported-bbox error into 14.8 mm of placement error, because the
    bbox *was* the measurement. Here the bbox only selects pixels, so a region slid 10 px — 22 mm
    at the survey camera's magnification — has to leave the answer where it is, and does, on a
    frame where the wall is large enough that sliding does not cut it off.
    """
    camera = _camera()
    centre = (0.02, 0.12)
    depth = _synthetic_face(camera, recesses=[(centre, 0.03)])
    got = []
    for dx in (0, 10, -10):
        bbox = [20.0 + dx, 20.0, 200.0 + dx, 200.0]
        mouth, ev = fit_aperture(camera, depth, bbox, decl=FACE_DECL)
        assert mouth is not None, (dx, ev.get("why"))
        got.append(mouth)
    assert max(float(np.linalg.norm(np.asarray(a) - np.asarray(b)))
               for a in got for b in got) < 2e-3


def test_the_window_convention_is_the_scalar_functions_own():
    """`_frame_metres` is `depth_to_meters` over a frame. If the two ever disagree, every number
    in this file is measured against a depth image no percept would have recognised."""
    camera = _camera()
    window = np.linspace(0.0, 1.0, 257)
    from embodied_agent.perception.camera import depth_to_meters
    assert np.allclose(_frame_metres(window, camera),
                       [depth_to_meters(float(w), camera.near_m, camera.far_m) for w in window])
    # and the two endpoints, which are the branches the scalar has and this one does not: the
    # vector form reaches `near_m` by arithmetic, and arrives at 0.05000000000000001, so the
    # agreement being tested is float agreement rather than identity
    assert _frame_metres(np.array([0.0, 1.0]), camera) == pytest.approx(
        [camera.near_m, camera.far_m], rel=1e-15)


# ------------------------------------------------------------- the survey's shape --


def test_the_survey_names_no_provider_in_the_perception_path():
    """`_request_errors` finds the adapter's own failure type instead of importing a vendor's.

    Two things are asserted and one deliberately is not. A socket error must be caught whatever
    client raised it, and the failure type a client's own module declares must be found there.
    What is *not* claimed is that an arbitrary exception from an adapter with no declared
    `LLMError` gets converted: it does not, and pretending otherwise would hide a misconfigured
    endpoint behind a perception refusal.
    """
    class Client:
        pass

    errors = _request_errors(Client())
    assert errors == (OSError,), errors              # nothing declared on that module to find
    with pytest.raises(errors):
        raise OSError("connection reset by peer")

    from embodied_agent.adapters.deepseek import LLMError

    Client.__module__ = LLMError.__module__          # what `_request_errors` reads
    assert LLMError in _request_errors(Client())


# ---------------------------------------------------------------- with a renderer --


@pytest.fixture(scope="module")
def mw_backend():
    if not HAS_MW:
        pytest.skip("metaworld")
    from embodied_agent.benchmark_mujoco.env import MuJoCoBackend

    b = MuJoCoBackend("peg-insert-side-v3", task_index=0, seed=0)
    b.start()
    yield b
    b.close()


@pytest.fixture()
def survey_rig(mw_backend, tmp_path):
    rig = MuJoCoRig(mw_backend, str(tmp_path / "perception"), views=("corner2",))
    yield rig
    rig.close()


@needs_mw
def test_the_synthetic_face_is_the_scene_file(mw_backend):
    """`FACE_DECL` above is a transcription, and a transcription that drifts turns the tests on
    this file into a test of a box that does not exist."""
    decl = wall_declaration(mw_backend)
    for key, value in FACE_DECL.items():
        got = decl[key]
        assert np.allclose(np.asarray(got, dtype=float), np.asarray(value, dtype=float),
                           atol=1e-4), (key, got, value)


@needs_mw
def test_the_declaration_reads_the_model_file_and_never_the_running_state(mw_backend):
    """§10, as a tripwire: touch `data` anywhere in the declaration and this raises.

    `wall_declaration` is the channel's only source of "where the hole is relative to the box",
    and the honest version of that sentence is a fact about the scene file. A backend whose every
    `data` attribute access raises is the strongest check of that this file can write — it does
    not ask whether the numbers look model-shaped, it asks whether the code can reach a number
    that is not.
    """
    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError(f"the declaration read data.{name}: that is this episode's "
                                 f"state, and §10 forbids it in a camera channel")

    class ModelOnly:
        OBJECT_BODIES = mw_backend.OBJECT_BODIES

        def __init__(self):
            self.env = type("E", (), {"model": mw_backend.env.model})()
            self.data = Forbidden()

    decl = wall_declaration(ModelOnly())
    assert np.allclose(decl["hole_local"], [0.0, -0.096, 0.13], atol=1e-4)
    assert np.allclose(aperture_frame(decl)["normal"], [1.0, 0.0, 0.0], atol=1e-3)


@needs_mw
def test_the_survey_frame_carries_no_answer_key(mw_backend, survey_rig):
    """The `hole` site renders as a green disc *at the target*, and it did.

    Measured before the fix (`/tmp/mw_sites_off.py`): RGB (31,252,31) at layout 0's projected
    mouth and (34,247,34) at layout 11's against the box face's (165,3,3) once the marker is off,
    and the depth at that pixel moved 1.978 -> 2.046 m — the marker, floating inside the
    aperture, was the nearest surface the ray met. So this test has two halves: the rig's own
    option must paint nothing green there, and MuJoCo's default option must still paint it, which
    is what says the first half is a claim about the renderer rather than about the picture
    happening to be brown today.
    """
    import mujoco

    decl = wall_declaration(mw_backend)
    rot = wall_rotation(decl)
    centre = (np.asarray(mw_backend.body("box"), float)
              + rot @ np.asarray(decl["visual_centre_local"], float))
    mouth = centre + aperture_frame(decl, rot=rot)["mouth_offset_world"]
    camera = survey_rig.camera("corner2")
    px, py, _d = camera.project(mouth)
    u, v = int(round(px)), int(round(py))

    def green_near(rgb):
        patch = rgb[max(0, v - 4):v + 5, max(0, u - 4):u + 5].astype(int)
        return int(((patch[:, :, 1] > 150) & (patch[:, :, 1] > patch[:, :, 0] + 60)
                    & (patch[:, :, 1] > patch[:, :, 2] + 60)).sum())

    _frame, rgb, _depth = survey_rig.render(camera, "no_site", sim_time=mw_backend.sim_time)
    assert green_near(rgb) == 0, "a green disc at the declared mouth is the answer, in pixels"

    renderer = mujoco.Renderer(mw_backend.env.model, camera.height, camera.width)
    try:
        renderer.update_scene(mw_backend.env.data, camera=camera.camera_id)
        with_sites = np.asarray(renderer.render(), dtype=np.uint8)
    finally:
        renderer.close()
    assert green_near(with_sites) > 0, (
        "MuJoCo's default option no longer paints the hole site, so the half of this test that "
        "matters has stopped being a test")


@needs_mw
def test_the_declared_camera_is_the_rendered_camera(mw_backend, survey_rig):
    """`CameraSpec` is built from `model.cam_fovy` and `data.cam_xmat`, and the frame it predicts
    has to be the frame that came back — otherwise a run directory holds a picture and a camera
    that did not take it.

    The check is the depth at the pixel the declaration points at: project the box's visual centre
    with the declared intrinsics, convert that pixel's window depth with the declared near/far, and
    ask where the ray landed. Two things must be true of the answer, and they are the two the
    estimator depends on: the surface there is the *declared wall* — 100 mm out along the declared
    normal from the box's own central plane — and the point is inside the declared face rectangle.

    What is deliberately *not* asserted is that the point coincides with the projected centre. It
    cannot: `corner2` looks at the wall at an angle, so the ray through the centre's projection
    meets the plane well off it. The first version of this test asserted lateral agreement to 5 mm
    and measured 77.5 mm of disagreement — an error in the test, found by the renderer.
    """
    decl = wall_declaration(mw_backend)
    rot = wall_rotation(decl)
    face = aperture_frame(decl, rot=rot)
    centre = (np.asarray(mw_backend.body("box"), float)
              + rot @ np.asarray(decl["visual_centre_local"], float))
    camera = survey_rig.camera("corner2")
    assert camera.camera_id == "corner2"
    px, py, _window = camera.project(centre)
    u, v = int(round(px)), int(round(py))
    assert 0 <= u < camera.width and 0 <= v < camera.height, (px, py)
    _frame, _rgb, depth = survey_rig.render(camera, "camera_check",
                                            sim_time=mw_backend.sim_time, keep_depth=True)
    got = camera.unproject(float(u), float(v), float(depth[v, u]))
    seen = np.array([got.x, got.y, got.z])
    off = seen - centre
    axis = np.asarray(decl["hole_axis_world"], float)
    depth_along = float(off @ axis)
    assert abs(depth_along - face["depth_along"]) < 0.005, (
        f"the pixel the declaration points at is {1000 * depth_along:.1f} mm along the hole's axis "
        f"from the box's central plane, and the scene file says its wall is at "
        f"{1000 * face['depth_along']:.1f}")
    in_plane = [float(off @ face[key]) for key in ("a1", "a2")]
    assert all(abs(c) <= half for c, half in zip(in_plane, face["span_half"])), (
        f"the ray meets the wall's own plane {[round(1000 * c, 1) for c in in_plane]} mm from the "
        f"box's centre, outside the declared "
        f"{[round(2000 * h, 1) for h in face['span_half']]} mm rectangle: the intrinsics and the "
        f"picture are of different cameras")


@needs_mw
def test_the_hole_is_measured_on_a_real_frame(mw_backend, survey_rig):
    """The end-to-end anchor: the ported estimator, on the production rig, on layout 0, with the
    true outline handed to it as the region.

    `/tmp/mw_port_check.py` runs this over all 35 layouts and reports median 0.6 mm of lateral
    error, 33 layouts finding an island, and layouts 5/9/23/27 degraded by the arm and 24/31
    refusing. One number belongs in the suite: if the channel ever answers layout 0 to centimetre
    accuracy, the batch's table is the thing to read, not the code.
    """
    decl = wall_declaration(mw_backend)
    rot = wall_rotation(decl)
    face = aperture_frame(decl, rot=rot)
    centre = (np.asarray(mw_backend.body("box"), float)
              + rot @ np.asarray(decl["visual_centre_local"], float))
    mouth_true = centre + face["mouth_offset_world"]
    camera = survey_rig.camera("corner2")
    outline = wall_silhouette(camera, centre, rot=rot, half=face["half_extents"])
    _frame, _rgb, depth = survey_rig.render(camera, "aperture", sim_time=mw_backend.sim_time,
                                            keep_depth=True)
    mouth, ev = fit_aperture(camera, depth, outline, decl=decl)
    assert mouth is not None, ev.get("why")
    err = np.asarray(mouth) - mouth_true
    lateral = err - float(err @ face["normal"]) * face["normal"]
    assert float(np.linalg.norm(lateral)) < 0.005, (mouth, mouth_true, ev["chosen"])
    # the implied box outline must agree with the region the estimator was given, or the two
    # channels — where the model says the box is, and where the hole says it is — have parted
    assert ev["max_outline_residual_px"] < 1.0


# ------------------------------------------------- the rod, and what a grasp is --


def _synthetic_rod(camera: CameraSpec, *, length_m: float, centre_x: float = 0.6,
                   centre_y: float = 0.0, centre_z: float = 0.1, stripe_m: float = 0.02):
    """An RGB frame and a window depth for a straight colour band lying along y.

    The band is drawn on the plane `x = centre_x` and spans `length_m` **in world metres**, so
    the body a fit returns has the length the test chose rather than the length a scene file
    happens to model. Each pixel carries the depth of the plane it was drawn on, by the same
    three lines of arithmetic `_synthetic_face` uses, so `measured_axis` is given a front face at
    its own measured distance and no plane is assumed anywhere. `stripe_m` is the band's
    thickness across that plane, and is what keeps a band drawn metres away above
    `AXIS_MIN_PIXELS` — a 5 mm stripe 2.9 m from the eye is 69 pixels and is refused for the
    wrong reason.

    Returns `(rgb, depth)` in the dtypes `colour_mask` and `measured_axis` read.
    """
    eye, d = _camera_rays(camera)
    t = (centre_x - eye[0]) / d[0]
    pts = eye[:, None, None] + d * t[None, :, :]
    on = ((np.abs(pts[1] - centre_y) <= length_m / 2.0)
          & (np.abs(pts[2] - centre_z) <= stripe_m))
    rgb = np.zeros((camera.height, camera.width, 3), dtype=float)
    rgb[on] = PEG_RGB
    metres = np.clip(t, camera.near_m, camera.far_m)
    near, far = camera.near_m, camera.far_m
    z_ndc = (metres * (far + near) - 2.0 * near * far) / (metres * (far - near))
    window = (z_ndc + 1.0) / 2.0
    return rgb, np.where(on, window, 1.0).astype(np.float32)


def _metres_at(camera: CameraSpec, window: float) -> float:
    """How far away a window value says a surface is: `_synthetic_rod`'s three lines inverted.

    Written as the inverse rather than as a table of numbers so a test that places a band at a
    window value cannot drift away from the arithmetic that draws it.
    """
    z = 2.0 * float(window) - 1.0
    near, far = camera.near_m, camera.far_m
    return 2.0 * near * far / ((far + near) - z * (far - near))


def _rod_axis(z_low: float, z_high: Optional[float] = None) -> dict[str, Any]:
    """A fitted rod whose two ends sit at these heights, lying along x at the origin."""
    high = z_low + 0.01 if z_high is None else z_high
    return {"ends": [[0.0, 0.0, z_low], [0.2339, 0.0, high]],
            "center": [0.1170, 0.0, round((z_low + high) / 2.0, 4)],
            "axis": [1.0, 0.0, 0.0], "length_m": 0.2339, "pixels": 442}


class _NoRenderer:
    """A `MuJoCoRig` that fails loudly if a test asks it for a frame.

    `MujocoPerceiver` reads `rig.views` once at construction and nothing else, so the grasp
    instrument's arithmetic can be driven entirely by canned axes. That is worth the fake: an
    instrument that needed a renderer to be tested is an instrument nobody tests when the
    renderer is the thing that broke.
    """

    views = ("corner2", "gripperPOV")

    def camera(self, view: str):
        raise AssertionError("the hold instrument asked for a camera")

    def render(self, *args, **kwargs):
        raise AssertionError("the hold instrument asked to render")

    def close(self) -> None:
        pass


@pytest.fixture()
def hold(mw_backend, tmp_path):
    """A perceiver that has never rendered, holding this cell's declared rest surfaces."""
    decl = wall_declaration(mw_backend)
    # the region below is the model's own hole point, which is *not* the production route (a
    # look fills it from pixels, three tests above). It is legitimate here only because nothing
    # the hold instrument reads consults a region: `hold_rest_surfaces` comes from the same
    # declaration's box AABB, and the claim the instrument makes is about the rod.
    region = region_from_wall_centre(decl, [0.0, 0.0, 0.13])
    return MujocoPerceiver(mw_backend, build_catalog(mw_backend, region), grounding_table(),
                           rig=_NoRenderer(), out_dir=str(tmp_path / "hold"),
                           arm_channel="stub", rest_surfaces=hold_rest_surfaces(mw_backend))


@needs_mw
def test_a_rod_of_the_declared_length_is_a_rod_and_a_fragment_is_not(hold):
    """The two gates on a fit, each fired by the number it exists for.

    `measured_axis` answers about a body, and two things downstream read it as one: the extent
    restatement publishes `length_m / 2` as the body's half-length, and `hold_instrument` calls a
    lowest end above every rest surface *carried*. A depth image that was never drawn — this
    renderer's second rig in a process, the failure `survey` names `renderer_suspect` — has
    produced both kinds of nonsense: 5 mm spans whose pixels unproject to z 1.07 m, and 55 mm
    spans at the height a hand really carries a rod at. Neither is refused by the pixel floor
    (`AXIS_MIN_PIXELS`), and both are refused below, by name.

    The gate values are calibration and are not re-tuned here: the drawn rod is the declared
    0.24 m, and each refusal moves only the threshold, never the picture.
    """
    camera = _camera()
    rgb, depth = _synthetic_rod(camera, length_m=0.24)
    fit = measured_axis(camera, rgb, depth, colour_rgb=PEG_RGB)
    assert len(fit["ends"]) == 2, fit.get("why")
    assert abs(fit["length_m"] - PEG_ROD_LENGTH_M) < 0.01, fit["length_m"]
    assert all(AXIS_END_Z_BOUNDS[0] <= e[2] <= AXIS_END_Z_BOUNDS[1] for e in fit["ends"])

    # the same picture, a floor raised past it: refused, and the refused numbers kept
    short = measured_axis(camera, rgb, depth, colour_rgb=PEG_RGB, min_span_m=0.25)
    assert short["ends"] == []
    assert abs(short["rejected_length_m"] - fit["length_m"]) < 1e-4
    assert "0.250" in short["why"] and "no rod in it" in short["why"], short["why"]

    high = measured_axis(camera, rgb, depth, colour_rgb=PEG_RGB,
                         end_z_bounds=(-0.5, fit["ends"][0][2] - 0.02))
    assert high["ends"] == []
    assert len(high["rejected_end_z_m"]) == 2
    assert "outside the band" in high["why"], high["why"]

    # and a genuinely short band, with the pixel floor taken out of the way so the refusal is
    # the span's and not the count's
    tiny_rgb, tiny_depth = _synthetic_rod(camera, length_m=0.05)
    tiny = measured_axis(camera, tiny_rgb, tiny_depth, colour_rgb=PEG_RGB, min_pixels=20)
    assert tiny["ends"] == []
    assert tiny["rejected_length_m"] < AXIS_MIN_SPAN_M, tiny
    assert "no rod in it" in tiny["why"], tiny["why"]
    # a 5 mm band is not a grasp answer either: the instrument says nothing rather than
    # inventing a body to hold
    assert hold.hold_instrument(tiny)["claim"] == "silent"
    assert hold.hold_instrument(tiny)["why"] == tiny["why"]


@needs_mw
def test_the_far_end_of_the_window_is_not_a_surface():
    """0.999 is this contract's own "nothing was drawn here" line, and `measured_axis` took 0.9999.

    The window is not linear at its far end, and the numbers below are the arithmetic of
    `_synthetic_rod` inverted: `0.9985` is 2.756 m from the eye and `0.9995` is 2.914 m, so one
    tenth of the last thousandth of the window spans 160 mm where the first tenth of the whole
    window spans 50 mm. The looser cut therefore admitted pixels the renderer had given up on —
    which is how a dead frame's rod came to stand a metre in the air — and both halves of this
    test are placed by *window value* rather than by distance so the line itself is what is pinned.

    The refusal is the counted one: every pixel of both ends is past the line, so an end has no
    pixels left and the fit says so instead of averaging the void.
    """
    camera = _camera()
    past, near = _metres_at(camera, 0.9995), _metres_at(camera, 0.9985)

    rgb, depth = _synthetic_rod(camera, length_m=0.24, stripe_m=0.06,
                                centre_x=float(camera.eye.x) - past)
    fit = measured_axis(camera, rgb, depth, colour_rgb=PEG_RGB)
    assert fit["ends"] == []
    assert "hit no surface in the depth image" in fit["why"], fit["why"]

    # the same rod 160 mm nearer the eye — and so on the near side of the line — is a body
    # again: the refusal is about the line, not about the picture or about the distance
    live_rgb, live_depth = _synthetic_rod(camera, length_m=0.24, stripe_m=0.06,
                                         centre_x=float(camera.eye.x) - near)
    live = measured_axis(camera, live_rgb, live_depth, colour_rgb=PEG_RGB)
    assert len(live["ends"]) == 2, live.get("why")
    assert abs(live["length_m"] - PEG_ROD_LENGTH_M) < 0.02, live["length_m"]
    assert live["pixels"] > fit["pixels"], (live["pixels"], fit["pixels"])


@pytest.mark.parametrize("z_low, claim, held, surface", [
    (0.0051, "resting", None, 0.005),        # the table, read 0.1 mm high
    (-0.0040, "resting", None, 0.005),       # 9 mm below it, inside the measured margin
    (0.0150, "resting", None, 0.005),        # exactly on the margin: inclusive
    (0.2000, "resting", None, 0.2),          # standing on the box top
    (0.2095, "resting", None, 0.2),          # 9.5 mm above it, still a rest
    (0.2105, "carried", "peg 1", 0.2),       # 10.5 mm up: standing on nothing
    (0.2138, "carried", "peg 1", 0.2),       # the height the archive really measured
    (0.0349, "silent", "unknown", None),     # the wrist camera's own resting reading
    (0.1000, "silent", "unknown", None),     # mid-motion
], ids=lambda v: str(v))
@needs_mw
def test_the_grasp_claim_is_the_measured_number(hold, z_low, claim, held, surface):
    """Three answers, and the arithmetic is on the page with them.

    The bands come from `HOLD_REST_MARGIN_M` (the largest vertical error this cell's axis
    instrument has shown on a resting rod, measured over the 21 resting looks of 42) and
    `hold_rest_surfaces` (the work plane and the top of the box, both from the model file). What
    is *not* in either band is silent, and silence is a real outcome of this design: a rod in
    motion, leaning, or part-way into the hole has no answer from two pixels, and the snapshot
    keeps `"unknown"` rather than a word for it.
    """
    row = hold.hold_instrument(_rod_axis(z_low))
    assert row["claim"] == claim, row
    assert row["held"] == held, row
    assert row["authority"] == ("frame" if claim != "silent" else "actuator")
    assert row["used"] is False                      # the instrument never promotes itself
    assert row["lowest_end_z_m"] == round(z_low, 4)
    assert row["rest_surfaces_m"] == [0.005, 0.2]
    assert row["margin_m"] == HOLD_REST_MARGIN_M
    if claim == "silent":
        assert "sentence" not in row and row["why"], row
        assert "neither resting nor standing on nothing" in row["why"]
        return
    assert row["surface_m"] == surface
    distance = row["distance_to_surface_m"]
    if claim == "resting":
        assert abs(distance) <= HOLD_REST_MARGIN_M + 1e-9, row
    else:
        assert distance > HOLD_REST_MARGIN_M, row
    sentence = row["sentence"]
    # the claim travels with the comparison that would refute it, in the snapshot's own units
    assert f"{z_low:.4f}" in sentence, sentence
    assert (f"{surface:.3f}" in sentence) and ("holding nothing" not in sentence)
    assert sentence.startswith("peg 1 is resting" if claim == "resting"
                              else "peg 1 is carried:")


@needs_mw
def test_a_frame_with_nothing_to_compare_says_nothing(hold):
    """Two refusals, both numeric, neither of them a claim.

    `held` arrives as `"unknown"` on this channel and the instrument's default is to leave it
    there: with no axis to read — the look rendered nothing, or the band was too small, or the
    fit was refused by the gates above — and with no rest surfaces declared, the only correct
    sentence is the one about what is missing.
    """
    hold._last_pixels = None
    hold._axis_fit = None
    refused = hold.hold_instrument()
    assert refused["claim"] == "silent" and refused["held"] == "unknown"
    assert "no frame to fit an axis to" in refused["why"], refused["why"]

    occluded = hold.hold_instrument({"ends": [], "pixels": 7, "why": "band occluded"})
    assert occluded["claim"] == "silent" and occluded["why"] == "band occluded"
    assert occluded["axis_pixels"] == 7

    one_end = hold.hold_instrument({"ends": [[0.0, 0.0, 0.21]], "pixels": 442})
    assert one_end["claim"] == "silent"
    assert "no two-ended axis" in one_end["why"], one_end["why"]

    saved = hold.hold_rest_surfaces
    hold.hold_rest_surfaces = ()
    mute = hold.hold_instrument(_rod_axis(0.2138))
    assert mute["claim"] == "silent" and "no rest surfaces" in mute["why"], mute["why"]
    hold.hold_rest_surfaces = saved


@needs_mw
def test_the_frame_never_overrules_a_gripper_that_answered(hold):
    """§5.8's two layers, in the order that decides a snapshot.

    The actuator's word outscores the pixels in both directions: a hand that reported `peg 1`
    keeps it against a frame measuring the rod on the table, and a hand that reported empty keeps
    it against a frame measuring the rod in the air. Either disagreement is a calibration defect
    to be read out of the filed row, not a licence for the later instrument to overwrite the
    earlier one — and the row records what each said, including the case where the actuator said
    nothing at all and the frame's answer is the only one there is.
    """
    from embodied_agent.core.contracts import WorldState

    state = WorldState(state_version=1, sim_time=0.0, wall_time=0.0, entities=[], targets=[],
                       observation_ref="obs_hold")
    carried = _rod_axis(0.2138)
    resting = _rod_axis(0.0051)
    for axis in (carried, resting):
        hold._axis_fit = dict(axis)
        for said in ("peg 1", None):
            held, authority = hold.held_for_frame(said, state)
            assert (held, authority) == (said, "actuator"), (axis["ends"][0][2], said)
            assert hold._hold_row["used"] is False
            assert hold._hold_row["actuator_said"] == said
            assert hold._hold_row["claim"] == ("carried" if axis is carried else "resting")

    # the frame answers only when the hand is mute, and then it says who answered
    hold._axis_fit = dict(carried)
    assert hold.held_for_frame("unknown", state) == ("peg 1", "frame")
    assert hold._hold_row["used"] is True and hold._hold_row["authority"] == "frame"
    hold._axis_fit = {"ends": [], "why": "band occluded"}
    assert hold.held_for_frame("unknown", state) == ("unknown", "actuator")
    assert hold._hold_row["used"] is False


@needs_mw
def test_a_real_frame_of_a_resting_rod_is_neither_refusal(mw_backend, survey_rig):
    """The calibration anchor: what the gates do to the frame this bench actually has.

    Layout 0's `corner2` shows the rod lying on the work plane, which is the reading the 42
    archived looks measured between −5.3 mm and +1.4 mm off `TABLE_TOP_Z_M`. If a future version
    of this file refuses *that*, the instrument has been gated out of existence, and the failure
    is a metre of `why` rather than a silent drop in a success rate.
    """
    camera = survey_rig.camera("corner2")
    _frame, rgb, depth = survey_rig.render(camera, "axis_resting", sim_time=mw_backend.sim_time,
                                           keep_depth=True)
    fit = measured_axis(camera, rgb, depth, colour_rgb=PEG_RGB)
    assert len(fit["ends"]) == 2, fit.get("why")
    assert fit["length_m"] >= AXIS_MIN_SPAN_M, fit
    assert all(AXIS_END_Z_BOUNDS[0] <= e[2] <= AXIS_END_Z_BOUNDS[1] for e in fit["ends"]), fit
    assert abs(min(e[2] for e in fit["ends"]) - TABLE_TOP_Z_M) <= HOLD_REST_MARGIN_M, fit


@needs_mw
def test_a_look_reports_the_grasp_it_measured_and_never_asks_the_simulator(hold, survey_rig):
    """The end-to-end form of the same claim: a snapshot, its authority field, and no `data`.

    What is being pinned is not the verdict — layout 0's rod is on the table, and the answer
    below is the one the fingers would give too — it is *who said it*. `held_object` is `None`,
    the `hold_report` fact names the frame, the entity's `held_source` says `frame`, and the
    numbers the claim turned on are filed beside it. Every one of those is a sentence a reviewer
    can refute from the snapshot alone; the instrument that would have answered them from
    `data` is `MujocoSkillExecutor._held_now`, and it is not reachable from here.
    """
    hold.rig = survey_rig
    try:
        state = hold.look("corner2", held="unknown")
    finally:
        hold.rig = _NoRenderer()

    row = [f for f in state.facts if f.kind == "hold_report"]
    assert len(row) == 1, [f.kind for f in state.facts]
    assert row[0].state == "empty" and state.held_object is None
    assert row[0].text == "the frame reports holding nothing", row[0].text
    entity = next(e for e in state.entities if e.entity_id == OBJECT_ID)
    assert entity.held is False
    assert entity.attributes[HELD_SOURCE] == "frame"
    assert entity.attributes["hold_claim"] == "resting"
    measurement = [f for f in state.facts if f.kind == "hold_measurement"]
    assert len(measurement) == 1 and measurement[0].state == "resting"
    assert not any(u.startswith("extent of") for u in state.unparsed), state.unparsed
    # and the row that decided it, filed in the order the looks were taken
    filed = hold.holds[-1]
    assert filed["used"] is True and filed["actuator_said"] == "unknown"
    assert filed["view"] == "corner2"
    assert abs(filed["lowest_end_z_m"] - TABLE_TOP_Z_M) <= HOLD_REST_MARGIN_M


# ------------------------------------------------------------------- the aim seam --


class _NoActuator:
    """A backend that fails loudly if an aimed motion reaches for the simulator.

    `_leading_end`'s perceived arm is the half of `MujocoSkillExecutor` a camera is allowed to
    own; `hand()`, `leading_tip()` and `measured()` are the half it is not. A test that drives the
    first with this in place cannot pass by silently falling back to the second.
    """

    def hand(self):
        raise AssertionError("the perceived aim asked the simulator where the hand is")

    def leading_tip(self, *args, **kwargs):
        raise AssertionError("the perceived aim asked the simulator's caliper")

    def measured(self):
        raise AssertionError("the perceived aim asked the simulator's site poses")


class _Aimed:
    """A `SnapshotGoals` that answers with the ends and the centre a test supplies.

    The three methods `_leading_end` calls, with the real object's meanings kept: `extent()` is
    `[]` when this snapshot measured no axis, and `position()` is None when it located no body.
    """

    def __init__(self, *, ends, centre, name: str = "snapshot v1 (test)"):
        self._ends = [tuple(float(v) for v in p) for p in ends]
        self._centre = None if centre is None else tuple(float(v) for v in centre)
        self._name = name

    def position(self, entity_id: str):
        return self._centre

    def extent(self, entity_id: str):
        return self._ends

    def source_name(self) -> str:
        return self._name


#: the carried rod the tests below use: a 0.24 m body along x, held just off its middle on the
#: side away from the hole, aimed at a hole on the far side of -x. Chosen because it is
#: *discriminating in the direction the fix has to go*: the end farthest from the hand is x=0.24,
#: the end nearest where the rod is going is x=0, so the two candidate rules cannot both pass.
#: This is batch 5b's layout 0 in its shape, not an invention: its filed axis ends were
#: (-0.0307, +0.2071) in x, the carrying hand at x=0.0919 against a centre of 0.0882, and the hole
#: it was steered to at x=-0.3124 — a 0.4 mm hand-distance margin, a 227 mm target-distance one,
#: and the shipped rule took the end away from the hole (`/tmp/mw_end_rule5b.py`). The fixture
#: widens the hand margin to 20 mm because this test is about *which side* the rule looks at, not
#: about how close the call is.
_CARRIED_ENDS = [(0.0, 0.0, 0.1), (0.24, 0.0, 0.1)]
_CARRIED_CENTRE = (0.12, 0.0, 0.1)
_CARRIED_HAND = (0.11, 0.0, 0.1)
_CARRIED_HOLE = (-0.4, 0.0, 0.12)


def test_the_end_that_has_to_arrive_is_the_end_nearest_where_it_is_going():
    """The rule, on a rod that has no simulator in it.

    Bringing the *near* end of a rod to a hole drives its far end straight through the wall, so
    the end that has to arrive is the one already closest to the hole. Two rules have been shipped
    against that geometry and neither measured it: `-_dist(p, target)` ("farthest from where it is
    going", retired in batch 4) and `-_dist(p, hand)` ("farthest from the carrying hand", Fix A).
    The first costs a rod length of commanded travel by construction. The second agrees with the
    correct answer only when the hand sits far enough to one side — and on a centre grasp the two
    ends are 0.4 to 4.2 mm apart in hand distance, so the choice is noise. Batch 5b measured that
    noise going the same way in five of five episodes: every live `place` led with the end away
    from the hole (phase-log H-11), and the two layouts whose hole was measured well (0.0436 m and
    0.1106 m off) released 0.2312 m and 0.2522 m short, while replaying *those same archived ends*
    with the near end leading put both in the hole at 186 and 187 steps (`/tmp/mw_fixb_replay.py`).
    """
    ex = MujocoSkillExecutor(_NoActuator())
    ex.goal_source = _Aimed(ends=_CARRIED_ENDS, centre=_CARRIED_CENTRE)

    row = ex._leading_end(OBJECT_ID, _CARRIED_HOLE, _CARRIED_HAND)

    assert row["tip"] == (0.0, 0.0, 0.1), row
    assert row["center"] == (0.12, 0.0, 0.1) and row["half_length_m"] == 0.12, row
    # the two rules that have been shipped, spelled out so this geometry cannot stop
    # discriminating silently: neither one's answer is the end that arrives.
    assert max(_CARRIED_ENDS, key=lambda p: _dist(p, _CARRIED_HOLE)) != row["tip"], row
    assert max(_CARRIED_ENDS, key=lambda p: _dist(p, _CARRIED_HAND)) != row["tip"], row
    # ... and the two margins differ by an order of magnitude, which is the reason for the change:
    # the target separates the ends by 240 mm, the hand by 20 mm, and in a real grasp the hand's
    # number is what collapses into a tie.
    assert _dist(row["tip"], _CARRIED_HOLE) < _dist(_CARRIED_ENDS[1], _CARRIED_HOLE)
    assert abs(_dist(row["tip"], _CARRIED_HAND)
               - _dist(_CARRIED_ENDS[1], _CARRIED_HAND)) < \
        abs(_dist(row["tip"], _CARRIED_HOLE) - _dist(_CARRIED_ENDS[1], _CARRIED_HOLE))


def test_the_perceived_axis_points_the_same_way_as_the_calipers():
    """`axis` is centre->tip on both arms, because one reader of it cannot be two directions.

    `_do_place` falls back on `-tip["axis"]` when the centre and the tip it was given coincide,
    which is the one place this field is read. `env.leading_tip` publishes centre->tip; the
    perceived read published tip->centre, so the same fallback pulled the stand-off to opposite
    sides of the wall depending on whether a snapshot had been installed.
    """
    ex = MujocoSkillExecutor(_NoActuator())
    ex.goal_source = _Aimed(ends=_CARRIED_ENDS, centre=_CARRIED_CENTRE)
    row = ex._leading_end(OBJECT_ID, _CARRIED_HOLE, _CARRIED_HAND)

    axis, tip, centre = row["axis"], row["tip"], row["center"]
    n = _dist(axis, (0.0, 0.0, 0.0))
    assert n == pytest.approx(1.0, abs=1e-3), row
    # the statement of the convention, not a literal: `axis` points from the centre *toward* the
    # end that has to arrive. Written as a dot product so that this test cannot be cancelled out
    # by the two arms' errors agreeing — a literal here passed with both the retired rule and the
    # retired sign installed, which is how it was caught.
    outward = [float(t) - float(c) for t, c in zip(tip, centre)]
    dot = sum(a * o for a, o in zip(axis, outward))
    assert dot > 0.0, (row, dot)
    assert _dist(centre, tip) == pytest.approx(row["half_length_m"], abs=1e-3), row


def test_an_aim_with_no_measured_axis_refuses_rather_than_guessing_one():
    """A snapshot that saw no rod-shaped body gets a refusal, not a zero-length body.

    The `None` is what turns into the primitive's `extent_measurement` note; a default of
    `centre` here would move the arm and report success.
    """
    ex = MujocoSkillExecutor(_NoActuator())
    ex.goal_source = _Aimed(ends=[], centre=_CARRIED_CENTRE)
    assert ex._leading_end(OBJECT_ID, _CARRIED_HOLE, _CARRIED_HAND) is None


class _AskedButUnstepped:
    """A backend that answers the two numbers `execute` reads and refuses to actuate.

    `_NoActuator` cannot stand in for it: `execute` asks `steps` before and after the motion, so
    a filing test written against that fixture would die in the accounting rather than in the
    motion. The pose below is a *read*, which a privileged-arm refusal legitimately needs; the
    three `AssertionError`s are the test's proof that nothing was moved, which is the property
    the filing has to follow.
    """

    env = object()
    steps = 0
    done = False
    sim_time = 0.0

    def measured(self):
        return {"hand": _CARRIED_HAND,
                "sites": {OBJECT_ID: _CARRIED_CENTRE, "socket 1": _CARRIED_HOLE}}

    def servo(self, *args, **kwargs):
        raise AssertionError("a declined motion actuated")

    def squeeze(self, *args, **kwargs):
        raise AssertionError("a declined motion closed the fingers")

    def hold(self, *args, **kwargs):
        raise AssertionError("a declined motion held a position")


#: which snapshot the case is aimed with. `privileged` leaves `goal_source` alone, so `_aim` reads
#: the stub's site poses and a name that is not in them is the refusal.
_SOURCES = {
    "percept-no-body": lambda: _Aimed(ends=[], centre=None),
    "percept-no-axis": lambda: _Aimed(ends=[], centre=_CARRIED_CENTRE),
    "privileged": lambda: None,
}


@pytest.mark.parametrize("stage,skill,args,source,needle", [
    # the shape the archived batches actually contain: a `pick` with nothing measured to aim at
    ("target_lookup", "pick", {"object_id": OBJECT_ID}, "percept-no-body", OBJECT_ID),
    # the same refusal on the privileged arm, reached by naming a site this world does not have
    ("target_lookup", "place", {"object_id": OBJECT_ID, "target_id": "socket 9"},
     "privileged", "socket 9"),
    # the second refusal inside `place`: a target it can see, a body it cannot measure an axis of
    ("extent_measurement", "place", {"object_id": OBJECT_ID, "target_id": "socket 1"},
     "percept-no-axis", OBJECT_ID),
])
def test_a_motion_that_refused_to_move_is_not_filed_as_one_that_ran(stage, skill, args, source,
                                                                    needle):
    """`completed` for a motion that moved nothing is a false positive in two readers at once.

    The status is not decoration on this channel. `core/runtime.py:703` forwards a skill's notes
    as `rejection_reasons` **only** when the status is `rejected`, so filing a refusal as a
    success threw away the one sentence that explains the miss *and* told the model the step
    worked; and `runner.py`'s `skill_calls_executed` reads `executor.calls`, so the same filing
    counted a motion that consumed no environment step. Both halves are pinned below.

    Measured before this was written, not after: `/tmp/mw102/reconcile107.py` reads both records
    the channel keeps — 4 of the 97 `execution_feedback` motions are `pick` rows at
    `stages: ["target_lookup"]` with `env_steps_used: 0.0`. Those 4 are published as
    `uncertain`/`STATE_UNCERTAIN`, because layer 2 narrows a `completed` whose postcondition it
    cannot measure (`perceive.py:2163`) — the misfiling pinned here happens one layer earlier, in
    `calls` and in the reason that never reaches the page. The two other shapes this set names
    have not occurred in an archived batch and are not driven here: the predicate is a set
    membership, so the constant itself is asserted rather than a case faked to reach it.
    """
    from embodied_agent.benchmark_mujoco.executor import DECLINATION_STAGES
    from embodied_agent.core.contracts import SkillCall, SkillStatus

    assert DECLINATION_STAGES == frozenset({"target_lookup", "extent_measurement",
                                           "carry_check"}), \
        "a refusal stage left out of this set keeps being filed as a skill that ran"

    ex = MujocoSkillExecutor(_AskedButUnstepped())
    src = _SOURCES[source]()
    if src is not None:
        ex.goal_source = src
    r = ex.execute(SkillCall(plan_id="p", step_id="s", skill=skill, args=args))

    assert r.status is SkillStatus.rejected, (r.status, r.notes)
    assert r.failure_code == "AIM_UNMEASURED", r.failure_code
    assert r.stages_executed == [stage], "the refusal names the stage it stopped at"
    assert r.measurements["env_steps_used"] == 0.0
    assert r.held_state == "unknown", "a motion that refused reports no grip"
    assert ex.calls == 0, "a motion that refused is not one this episode executed"
    assert ex.env_steps_total == 0
    assert ex.motions and ex.motions[-1]["status"] == "rejected", \
        "the channel's own log keeps the refusal, notes and all"
    assert any(needle in n for n in ex.motions[-1]["notes"]), \
        f"the note has to say what could not be measured: {ex.motions[-1]['notes']}"


@needs_mw
def test_the_perceived_aim_and_the_caliper_pick_the_same_end_of_a_carried_rod():
    """The seam's whole claim in one line: same rule, different source.

    The two arms are meant to differ only in *where* the number comes from; a different choice of
    end between them would hide a policy difference inside a motor primitive, and the ablation
    would then be measuring that instead of perception. So the perceived read is fed the very
    ends the simulator's caliper reports — which makes this a test of the rule alone, and nothing
    here claims the ends themselves are accurate (that is `/tmp/mw_aim_replay.py`'s 5 mm).

    What the rule is, is the other test's business; what is pinned here is that *both arms of the
    seam* answer it the same way. That is not the same statement as "the seam agrees with
    `leading_tip`": the caliper keys its sign on the holder (`env.py`, "the choice is a convention,
    not knowledge"), a 0.24 m rod gripped at its middle gives that convention a sub-5 mm margin
    (batch 5b: 0.4-4.2 mm on five of five episodes), and `state.py` reads that convention for the
    geometry it publishes. So the caliper still answers its own question, the aim still answers the
    target's, and the two arms of the aim must not disagree with each other.

    The pose matters. At every layout's rest pose the two candidate rules select the same end
    (`/tmp/mw_tip_rule_probe.py`: 7 of 7 layouts, agree=True), so a test written against a resting
    peg would have passed on any rule and pinned nothing. This drives the real `pick` and compares
    at the pose a `place` is actually called from — and then separates the aim from the caliper by
    moving only the question, because whether the carried rod happens to hang with its
    holder-keyed end on the hole's side is a fact about one pose, not about a rule.

    Own backend instance rather than the module fixture: this moves the arm, and three tests above
    render the resting rod.
    """
    from embodied_agent.benchmark_mujoco.env import MuJoCoBackend
    from embodied_agent.core.contracts import SkillCall

    b = MuJoCoBackend("peg-insert-side-v3", task_index=0, seed=0)
    b.start()
    try:
        ex = MujocoSkillExecutor(b)
        ex.execute(SkillCall(plan_id="p", step_id="s", skill="pick",
                             args={"object_id": OBJECT_ID}))
        m = b.measured()
        hand, hole = m["hand"], m["sites"]["socket 1"]
        ref = b.leading_tip(OBJECT_ID, hand)
        privileged = ex._leading_end(OBJECT_ID, hole, hand)

        perceived = MujocoSkillExecutor(_NoActuator())
        perceived.goal_source = _Aimed(ends=ref["ends"], centre=ref["center"])
        row = perceived._leading_end(OBJECT_ID, hole, hand)

        assert row["tip"] == privileged["tip"], (row, privileged)
        assert row["axis"] == pytest.approx(tuple(privileged["axis"]), abs=2e-4), (row, privileged)
        assert row["half_length_m"] == pytest.approx(ref["half_length_m"], abs=1e-4)
        # the end both arms name is the one already closest to the hole ...
        assert row["tip"] == min(ref["ends"], key=lambda p: _dist(p, hole)), (row, ref)
        # ... and here the caliper's own convention agrees with that answer, which is a fact about
        # this pose. So the agreement above is tested where the two cannot agree by coincidence:
        # leave the rod, the hand and the caliper's report exactly as they are and ask the same
        # seam about a point as far outside the rod on the *other* side. A rule that looked at the
        # holder instead of the target has no way to follow it.
        mirrored = (2 * float(ref["center"][0]) - float(hole[0]), float(hole[1]), float(hole[2]))
        assert _dist(mirrored, hole) > 0.2, (mirrored, hole)
        priv_m = ex._leading_end(OBJECT_ID, mirrored, hand)
        row_m = perceived._leading_end(OBJECT_ID, mirrored, hand)

        assert row_m["tip"] == priv_m["tip"], (row_m, priv_m)
        assert row_m["axis"] == pytest.approx(tuple(priv_m["axis"]), abs=2e-4), (row_m, priv_m)
        assert row_m["center"] == priv_m["center"], (row_m, priv_m)
        # it moved, to the other of the same two ends, while the caliper's answer did not move
        assert row_m["tip"] != row["tip"], (row_m, row)
        assert row_m["tip"] == max(ref["ends"], key=lambda p: _dist(p, hole)), (row_m, ref)
        assert row_m["tip"] != ref["tip"], (row_m, ref)
    finally:
        b.close()


# ------------------------------------------------------------ #103: the three ways a
# `placed:` row can be unmeasurable, named apart
#
# `distance_m` (`benchmark_mujoco/state.py:283-300`) returns None when the region is absent, when
# the body is absent, or when the body is present with no measured position. Until this section
# existed, `MujocoVerifier.progress` and `verify_action` answered all three with one sentence
# claiming a *name* was missing, and `MujocoPerceptVerifier` (`perceive.py:2139`) inherits both
# methods untouched, so that is what the camera channel told the model at the end of every
# successful insertion. The archived reading, before the fix: 9 of 35 camera episodes are scored
# `official_success`, all 9 file `placed:peg 1:socket 1 = unknown` on the `place` row with
# `unmeasured: ["one of the two names is not in this snapshot"]` (`/tmp/mw102/check103.py` re-runs
# one of them against the shipped code), while the same predicate read `false` in rounds 1-2 of
# those very episodes — so the agent's world got *less* able to answer at the moment the task got
# done, which is why an arm re-issued `pick` after a scored insertion (#92).
#
# `/tmp/mw102/pos112.py` decides which of the three it was from the archived payloads and the
# `episode_summary` axis fits, without rebuilding a state. A camera look carries a position for the
# rod through exactly two doors: `state_attributes.position_xyz_m` on the detection, which
# `perception/world_state.py:149,191` turns into `EntityState.pose`, and the fitted-axis midpoint
# that `benchmark_mujoco/perceive.py:2022-2044` then overwrites it with. Over the 64 looks of the
# 10 camera episodes that have scored: the rod blob is present **64/64** (`green -> peg 1`, bound in
# every look), `socket 1` is a declared region **64/64** — so branch 1 and branch 2 are out on the
# artifact alone — and **34** of the 64 give the rod no position by either door, including
# **10/10** of the looks immediately after a `place`, which is why all 10 of those places are filed
# `uncertain`. Cross-check on the rounds themselves: the 20 that published `placed: = false` all sit
# on looks that do carry a position and the 15 that published `unknown` all sit on looks that carry
# none, so no round ever contradicted the snapshot it cited.
#
# Two earlier readings of mine disagreed with each other, and both were instruments rather than
# facts: `probe103b.py` rebuilt states with `entities_from_percept` + `ground_state` and so missed
# the second door (it counted 56 unmeasurable / 3 located), and `branch103.py` then asked
# `detections[].pose`, a key this pipeline never reads and which is null in every camera detection
# ever filed (0/59). `detections[].pose` is not `distance_m`'s third branch; the two doors above
# are. Phase-log #39 and #40.
#
# So the sentence was not vague, it was false, and it was the advice attached to the `unknown`:
# "reconcile the names" is one act, "look from a side that can see under it" is another.


def _camera_snapshot(*, with_body: bool = True, with_region: bool = True,
                     located: bool = False):
    """A sensor snapshot in the shape the archive shows: `peg 1` named, `socket 1` declared, and
    no metres on the body unless `located`."""
    from embodied_agent.core.contracts import (
        EntityState, Pose, Source, TargetRegion, Vec3, WorldState)
    rod = EntityState(entity_id="peg 1", body_id=-1,
                      pose=(Pose(position=Vec3(x=0.0, y=0.0, z=0.0),
                                 quaternion_xyzw=(0.0, 0.0, 0.0, 1.0)) if located else None),
                      orientation_measured=False, geometry=None, held="unknown",
                      supported_by="unknown", at_rest="unknown", visible=True,
                      attributes={"color": "green", "shape": "bar"}, source=Source.sensor)
    region = TargetRegion(target_id="socket 1", label="the surveyed hole",
                          center=Vec3(x=-0.31, y=0.21, z=-0.23), inner_half=0.005,
                          floor_top_z=-0.23, frame_id="world")
    return WorldState(state_version=1, sim_time=0.0, wall_time=0.0,
                      entities=([rod] if with_body else []),
                      targets=([region] if with_region else []), held_object="unknown",
                      observation_ref="per_probe", source=Source.sensor)


def _place_verifier(world) -> "MujocoPerceptVerifier":
    from embodied_agent.benchmark_mujoco.perceive import MujocoPerceptVerifier
    return MujocoPerceptVerifier(world, None, view="corner2", alt_views=["gripperPOV"])


def test_an_unmeasurable_place_says_which_of_the_three_tests_failed():
    """The camera channel's `unknown` must not claim a missing name when the name is there."""
    from embodied_agent.core.contracts import (
        EntityRef, GoalAssignment, GoalSpec, PredicateVerdict, Source)
    why_unlocated = _place_verifier(_camera_snapshot()).verify_action(
        "place", {"object_id": "peg 1", "target_id": "socket 1"})[0]
    assert why_unlocated.value is PredicateVerdict.unknown
    assert why_unlocated.source is Source.sensor, "this is a camera answer, not a simulator one"
    text = why_unlocated.unmeasured[0]
    assert "both in this snapshot" in text and "no distance to socket 1" in text, text
    assert "not in this snapshot" not in text, \
        ("the collapsed sentence asserts an absence the snapshot does not have: 0 of the 59 "
         "archived looks of a scored episode lost a name")
    # the same row `progress` publishes on the decision page says the same thing
    goal = GoalSpec(assignments=[GoalAssignment(entity=EntityRef(entity_id="peg 1"),
                                                target_id="socket 1")])
    assert _place_verifier(_camera_snapshot()).progress(goal)[0].unmeasured == [text]

    # the other two branches get their own words, and no two of the three are the same sentence
    absent_body = _place_verifier(_camera_snapshot(with_body=False)).verify_action(
        "place", {"object_id": "peg 1", "target_id": "socket 1"})[0]
    absent_region = _place_verifier(_camera_snapshot(with_region=False)).verify_action(
        "place", {"object_id": "peg 1", "target_id": "socket 1"})[0]
    assert absent_body.unmeasured[0] == "peg 1 is not a body in this snapshot"
    assert absent_region.unmeasured[0] == "socket 1 is not a region in this snapshot"
    three = {text, absent_body.unmeasured[0], absent_region.unmeasured[0]}
    assert len(three) == 3, "a re-collapse into one sentence is the defect this pins shut"

    # ...and none of this moved a verdict: the fix is to the reason, not to the answer
    assert {r.value for r in (why_unlocated, absent_body, absent_region)} \
        == {PredicateVerdict.unknown}


def test_the_reason_reaches_the_page_the_model_reads_next_to_the_null_pose():
    """The `unmeasured` list is rendered onto the decision page verbatim (`prompts.py:95`), and so
    is the entity's `pose` (`:108`). Those two rows are the same fact seen from two ends — a body
    with `pose: null` and a reason that says *this snapshot has no metres* — and a page where they
    disagree teaches the model to go looking for a naming bug. The verdict stays `unknown` either
    way; what the fix changes is which act the next round can take.
    """
    from embodied_agent.benchmark_mujoco.prompts import MujocoDecisionContext
    from embodied_agent.core.contracts import (
        EntityRef, GoalAssignment, GoalProgressItem, GoalSpec, PredicateVerdict, TaskInput)

    world = _camera_snapshot()
    goal = GoalSpec(assignments=[GoalAssignment(entity=EntityRef(entity_id=OBJECT_ID),
                                                target_id="socket 1")])
    verifier = _place_verifier(world)
    page = MujocoDecisionContext(
        episode_id="probe", task=TaskInput(task_id="t", utterance="把杆插进洞里"), goal=goal,
        state_version=world.state_version, observation_ref=world.observation_ref,
        world=world, progress=verifier.progress(goal))
    rendered = page.model_payload()
    row = rendered["progress"][0]
    assert row["value"] == "unknown", "the fix moved no verdict"
    assert row["predicate_id"] == f"placed:{OBJECT_ID}:socket 1"
    assert row["unmeasured"][0].startswith(f"{OBJECT_ID} and socket 1 are both in this snapshot"), \
        row["unmeasured"]
    body = next(e for e in rendered["world"]["entities"] if e["entity_id"] == OBJECT_ID)
    assert body["pose"] is None, "the page shows the missing metre beside the sentence about it"
    assert [t["target_id"] for t in rendered["world"]["targets"]] == ["socket 1"]
    assert isinstance(page.progress[0], GoalProgressItem)
    assert page.progress[0].value is PredicateVerdict.unknown


def test_an_untested_seating_row_says_the_test_was_not_run():
    """`verify_goals` hands the model a `description`, not a verdict, and the two `_seated`
    branches used to share one lead clause: an `unknown` row read "peg 1 is seated in socket 1:
    the measured point ... lies inside the body of peg 1 to within 0.005 m" next to
    `value: unknown` and a null pose, which is a claim about a test that returned no number. The
    archived post-place snapshots of all 9 scored episodes are in exactly that shape
    (`/tmp/mw102/check103.py` reads them through `verify_goals`), so the sentence the model was
    told at the moment of success was the opposite of the verdict in the same row.

    Pinning both branches, and that the untested one quotes no tolerance: `self._tolerance`
    answers `inf` whenever no region carries the name, and `inf:.3f` is "inf m" — a number the
    camera never measured.
    """
    from embodied_agent.core.contracts import EntityRef, GoalAssignment, GoalSpec, PredicateVerdict
    goal = GoalSpec(assignments=[GoalAssignment(entity=EntityRef(entity_id="peg 1"),
                                                target_id="socket 1")])
    untested = _place_verifier(_camera_snapshot()).verify_goals(goal).reports[0]
    assert untested.value is PredicateVerdict.unknown
    assert untested.description.startswith(
        "whether peg 1 is seated in socket 1 was not tested here"), untested.description
    assert "to within" not in untested.description.split(" A further look")[0], \
        untested.description
    assert "inf" not in untested.description, untested.description
    assert untested.evidence == {}, "no metres were measured, so none may be quoted"

    measured = _place_verifier(_camera_snapshot(located=True)).verify_goals(goal).reports[0]
    assert measured.value is PredicateVerdict.false
    assert measured.description.startswith("peg 1 is seated in socket 1: the measured point")
    assert f"(measured {measured.evidence['distance_m']:.4f} m)" in measured.description, \
        measured.description
    assert untested.description != measured.description

    # the same fact reached from the other door: `verify_action` publishes onto
    # `execution_feedback`, and its unmeasurable branch used to lead with the bare fragment
    # "peg 1 seated in socket 1" — the same assertion-shaped clause, one method away.
    via_action = _place_verifier(_camera_snapshot()).verify_action(
        "place", {"object_id": "peg 1", "target_id": "socket 1"})[0]
    assert via_action.description == untested.description, \
        (via_action.description, untested.description)
