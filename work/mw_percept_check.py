#!/usr/bin/env python
"""Zero-cost check of the MuJoCo camera channel, before one paid request is spent.

Run it with the MuJoCo interpreter (it needs `metaworld`, `mujoco` and osmesa):

    MUJOCO_GL=osmesa /home/czx/mwvenv/bin/python work/mw_percept_check.py

Every question below is one the percept arm has to answer about *its own pixels*, and none
of them needs a vision model: what the two cameras actually look like, whether the declared
`CameraSpec` reproduces the rendered one, where the colour band that names the peg lands,
how far the measured axis is from the caliper's, and what the gripper's own aperture reads
at the ends of its travel. The last group is the one whose numbers go back into
`executor.APERTURE_OPEN_M` / `APERTURE_CLOSED_M`, which start at `None` precisely so that an
uncalibrated hand reports `unknown` instead of a guess.

Read-only against the repo. Writes only under the directory named on the command line
(default `/tmp/mw_percept_check`).
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("MUJOCO_GL", "osmesa")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402

from embodied_agent.benchmark_mujoco.env import (  # noqa: E402
    GRASP_EFFORT, MuJoCoBackend, OPEN_EFFORT)
from embodied_agent.benchmark_mujoco.perceive import (  # noqa: E402
    AXIS_MIN_PIXELS, DEFAULT_VIEWS, PEG_MIN_SATURATION, PEG_RADIUS_M, PEG_RGB,
    SUPPORT_Z_BOUNDS, SURVEY_VIEW, TABLE_TOP_Z_M, WALL_HUE_TOL_DEG, WALL_MIN_SATURATION,
    WALL_RGB, MujocoPerceiver, MuJoCoRig, build_catalog, grounding_table, measured_axis,
    region_from_wall_centre, wall_declaration)
from embodied_agent.benchmark_mujoco.state import distance_m  # noqa: E402
from embodied_agent.perception.camera import look_at  # noqa: E402
from embodied_agent.perception.geometry import estimate_centre  # noqa: E402
from embodied_agent.perception.observe import StubReader, TaskContext  # noqa: E402
from embodied_agent.perception.segment import colour_bboxes, colour_mask  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mw_percept_check"
LAYOUTS = (0, 1, 2, 3, 4)
#: the identity check's tolerance, in metres: a perceived body centre inside this of the
# simulator's own is a channel that can be trusted to aim at. Stated before the run, and
# the arithmetic below that turns a grasp into a placement needs it to be honest.
AXIS_TOLERANCE_M = 0.02


def mm(a, b) -> float:
    return round(1000.0 * float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float))), 1)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


# ------------------------------------------------------------------ 1. the camera --
def check_camera(backend: MuJoCoBackend, rig: MuJoCoRig) -> list[dict]:
    """The declared `CameraSpec` must be the camera that was rendered.

    Everything downstream — every metre in this file — is `camera.unproject(pixel, depth)`.
    If the declared optics are not the optics the renderer used, the numbers are not wrong
    by a little; they are about a camera that does not exist.
    """
    import mujoco

    rows = []
    for view in rig.views:
        camera = rig.camera(view)
        cid = mujoco.mj_name2id(rig.model, mujoco.mjtObj.mjOBJ_CAMERA, view)
        want = np.asarray(backend.env.data.cam_xmat[cid], float).reshape(3, 3)
        got = look_at(camera.eye.as_list(), camera.target.as_list(),
                      camera.up.as_list())[:3, :3]
        # `look_at` is camera-from-world; `cam_xmat` is world-from-camera, hence the `.T`
        frame, rgb, depth = rig.render(camera, f"camera_{view}", sim_time=backend.sim_time)
        band = colour_mask(rgb, PEG_RGB, hue_tol_deg=22.0, min_saturation=PEG_MIN_SATURATION)
        ys, xs = np.nonzero(band)
        px, py, _w = camera.project(backend.site("pegGrasp"))
        rows.append({
            "view": view,
            "matrix_max_abs_error": round(float(np.abs(got.T - want).max()), 9),
            "fovy_deg": camera.fov_deg, "eye": camera.eye.as_list(),
            "fx_px": round(camera.fx, 3),
            "model_cam_intrinsic": [float(v) for v in np.asarray(rig.model.cam_intrinsic[cid])],
            "frame": frame.frame_id, "rgb_shape": list(rgb.shape),
            "depth_min": round(float(depth.min()), 6),
            "depth_max": round(float(depth.max()), 6),
            "background_px": int((depth >= 0.9999).sum()),
            "near_plane_px": int((depth <= 0.0001).sum()),
            "peg_site_projects_to": [round(float(px), 1), round(float(py), 1)],
            "green_band_bbox": ([int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
                                if xs.size else None),
            # ±12 px of slack: the site is the shaft's named point and the band is the whole
            # body, so a centre that projects just outside a 1-px outline is a rendering
            # fact, not a broken model
            "projection_inside_band": bool(xs.size and xs.min() - 12 <= px <= xs.max() + 12
                                           and ys.min() - 12 <= py <= ys.max() + 12),
            # `_window_depth` transcribes `camera.meters_to_depth` elementwise because that
            # function is scalar; the transcription is checked, not trusted
            "window_depth_matches_scalar_formula": _depth_agreement(camera)})
    return rows


def _depth_agreement(camera) -> dict:
    """Max absolute difference, over sampled metres, between the two conversions.

    Three pixels of the grid are not samples of the round trip: the NaN, the far-plane
    overrun and the near-plane hit are the three behaviours `perceive._window_depth` claims,
    so each gets the value the claim says it must have rather than the one the sample grid
    would imply. An earlier draft of this function compared the clipped overrun against the
    *unclipped* sample and reported a 0.78 disagreement that was the probe's own bug — the
    reason the check exists is to be read, so it has to fail only for the right reason.
    """
    from embodied_agent.benchmark_mujoco.perceive import _window_depth
    from embodied_agent.perception.camera import meters_to_depth

    samples = np.linspace(camera.near_m, camera.far_m, 211)
    grid = np.broadcast_to(samples.reshape(-1, 1), (samples.size, 4)).copy()
    want = np.broadcast_to(np.array(
        [meters_to_depth(float(z), camera.near_m, camera.far_m) for z in samples]
    ).reshape(-1, 1), (samples.size, 4)).copy()
    grid[0, 0] = np.nan                      # no surface hit -> background
    want[0, 0] = 1.0
    grid[1, 0] = camera.far_m * 40.0         # a wall beyond the declared far plane -> clipped
    want[1, 0] = 1.0
    grid[2, 0] = 0.0                         # the near plane, hit by geometry inside the arm
    want[2, 0] = meters_to_depth(camera.near_m, camera.near_m, camera.far_m)
    window = _window_depth(grid, camera)
    finite = np.isfinite(grid)
    return {"max_abs_difference": round(float(np.abs(window[finite] - want[finite]).max()), 12),
            "nan_pixel_stored_as": round(float(window[0, 0]), 6),
            "far_plane_overrun_stored_as": round(float(window[1, 0]), 6),
            "near_plane_stored_as": round(float(window[2, 0]), 6)}


# ------------------------------------------------------------- 2. colour vocabulary --
def check_colours(backend: MuJoCoBackend, rig: MuJoCoRig) -> list[dict]:
    """What each declared colour band actually selects, per view, in pixels."""
    rows = []
    for view in rig.views:
        camera = rig.camera(view)
        frame, rgb, depth = rig.render(camera, f"colour_{view}", sim_time=backend.sim_time)
        bands = {}
        for word, colour, tol, sat in (("green", PEG_RGB, 22.0, PEG_MIN_SATURATION),
                                       ("brown", WALL_RGB, WALL_HUE_TOL_DEG,
                                        WALL_MIN_SATURATION)):
            mask = colour_mask(rgb, colour, hue_tol_deg=tol, min_saturation=sat)
            found = colour_bboxes(rgb, {word: colour}, min_pixels=1, hue_tol_deg=tol,
                                  min_saturation=sat)
            row = next((r for r in found if r["color"] == word), None)
            bands[word] = {"pixels": int(mask.sum()),
                           "fraction_of_frame": round(float(mask.sum()) / mask.size, 4),
                           "bbox": (row or {}).get("bbox"),
                           "rows_split": (row or {}).get("rows_split"),
                           "fill_ratio": (row or {}).get("fill_ratio")}
        rows.append({"view": view, "bands": bands, "image_sha256": frame.image_sha256[:12]})
    return rows


# --------------------------------------------------------- 3. the wall's declaration --
def check_wall(backend: MuJoCoBackend) -> list[dict]:
    """Is the hole's offset inside the box the scene file, and does the rotation land?

    Two separate questions, and an earlier draft of `wall_declaration` passed the first while
    failing the second. The survey buys one fact per episode — where the box is — and
    `region_from_wall_centre` spends it against an offset read out of the model arrays, which
    is only sound while the offset is the same number in every layout. But *sound* also means
    expressed in the same axes: this box body carries a modelled 90° yaw, so an offset left in
    the box's own frame and added to a world centre points at the wrong wall by 96 mm.

    The two points are compared by splitting their gap **along** the hole's axis from the gap
    **across** it. `distance_m` measures a perpendicular distance to the peg's axis, so a
    target slid along the hole's axis is a different *depth*, not a different *place* — that is
    the substitution this channel makes, and it is deliberate. A gap across the axis is a
    calibration error, and nothing here should produce one.

    `data.xpos` and the `goal` site are read in this function only. They are the validation
    oracle, and the channel reads neither.
    """
    import mujoco

    rows = []
    for index in LAYOUTS:
        backend.task_index = index
        backend.start()
        decl = wall_declaration(backend)
        m = backend.env.model
        box_bid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "box")
        flat = np.zeros(9)
        mujoco.mju_quat2Mat(flat, np.asarray(m.body_quat[box_bid], dtype=float))
        rot = flat.reshape(3, 3)
        centre_world = (np.asarray(backend.body("box"), dtype=float)
                        + rot @ np.asarray(decl["visual_centre_local"], dtype=float))
        region = region_from_wall_centre(decl, centre_world)
        declared = np.array([region.center_xy[0], region.center_xy[1], region.floor_top_z])
        goal = np.asarray(backend.site("goal"), dtype=float)
        axis = np.asarray(decl["hole_axis_world"], dtype=float)
        gap = declared - goal
        off = gap - axis * float(gap @ axis)
        rows.append({
            "layout": index, "declaration": decl,
            "perceived_box_centre_world": [round(float(v), 4) for v in centre_world],
            "declared_target_world": [round(float(v), 4) for v in declared],
            "goal_site_world": [round(float(v), 4) for v in goal],
            "gap_along_hole_axis_m": round(float(gap @ axis), 4),
            "gap_across_hole_axis_m": round(float(np.linalg.norm(off)), 5),
            "same_axis_as_goal": bool(np.linalg.norm(off) <= 1e-3),
            "inner_half_m": region.inner_half,
            "tolerance_this_channel_will_use_m": round(region.inner_half + PEG_RADIUS_M, 4)})
    print("  declaration identical across layouts:",
          len({json.dumps(r["declaration"], sort_keys=True) for r in rows}) == 1)
    return rows


# ------------------------------------------------------------------- 4. the axis ---
def check_axis(backend: MuJoCoBackend, rig: MuJoCoRig) -> list[dict]:
    """The measured ends against the caliper's, in millimetres, on five layouts."""
    rows = []
    for index in LAYOUTS:
        backend.task_index = index
        backend.start()
        hand = backend.hand()
        caliper = backend.leading_tip("peg 1", hand)
        camera = rig.camera(SURVEY_VIEW)
        frame, rgb, depth = rig.render(camera, f"axis_L{index}", sim_time=backend.sim_time)
        axis = measured_axis(camera, rgb, depth, colour_rgb=PEG_RGB)
        row = {"layout": index, "site": list(backend.site("pegGrasp")),
               "caliper_center": list(caliper["center"]),
               "caliper_ends": [list(e) for e in caliper["ends"]],
               "caliper_half_length_m": caliper["half_length_m"],
               "measured": axis}
        if axis.get("ends"):
            want = [np.asarray(e, float) for e in caliper["ends"]]
            got = [np.asarray(e, float) for e in axis["ends"]]
            # the two ends come back in an order that means nothing, so try both pairings
            # and report the better one — a swapped pair is not an error of measurement
            direct = [mm(got[0], want[0]), mm(got[1], want[1])]
            crossed = [mm(got[0], want[1]), mm(got[1], want[0])]
            pair = direct if sum(direct) <= sum(crossed) else crossed
            row["end_errors_mm"] = pair
            row["midpoint_error_mm"] = mm((got[0] + got[1]) / 2.0, caliper["center"])
            row["length_error_mm"] = round(1000.0 * abs(float(axis["length_m"])
                                                        - 2.0 * caliper["half_length_m"]), 1)
            row["passes_at_declared_tolerance"] = bool(
                row["midpoint_error_mm"] <= AXIS_TOLERANCE_M * 1000.0)
        rows.append(row)
    return rows


# --------------------------------------------------------------- 5. the stub look ---
def check_snapshot(backend: MuJoCoBackend, rig: MuJoCoRig) -> list[dict]:
    """The whole `look()` path with a deterministic reader: grounding, extent, verdict.

    `StubReader` is the same colour-segmentation algorithm the `wo_vlm` arm is defined by,
    so what this measures is the assembler's and the axis instrument's arithmetic, not a
    model's opinion. A paid request is then a change of reader and nothing else.
    """
    backend.task_index = 0
    backend.start()
    decl = wall_declaration(backend)
    camera = rig.camera(SURVEY_VIEW)
    frame, rgb, depth = rig.render(camera, "snapshot_survey", sim_time=backend.sim_time)
    # the survey's box, without a model: the declared colour band stands in for the answer
    # the paid channel is asked for, so the geometry below is the same arithmetic
    found = colour_bboxes(rgb, {"brown": WALL_RGB}, min_pixels=AXIS_MIN_PIXELS,
                          hue_tol_deg=WALL_HUE_TOL_DEG, min_saturation=WALL_MIN_SATURATION)
    row = next((r for r in found if r["color"] == "brown"), None)
    if row is None:
        return [{"skipped": "the wall's colour band found nothing, so there is no offline "
                           "region to build a snapshot from"}]
    centre, geo = estimate_centre(camera, depth, row["bbox"],
                                  half_height_m=decl["half_extents"][2],
                                  z_bounds=SUPPORT_Z_BOUNDS)
    if centre is None:
        return [{"skipped": "the surveyed box could not be placed in metres", "geometry": geo}]
    region = region_from_wall_centre(decl, [centre.x, centre.y, centre.z])
    catalog = build_catalog(backend, region)
    gmap = grounding_table()
    perceiver = MujocoPerceiver(backend, catalog, gmap, rig=rig,
                                out_dir=os.path.join(OUT, "frames_stub"), reader=StubReader(),
                                arm_channel="stub",
                                task=TaskContext(utterance="insert the peg into the hole",
                                                 items=["green"], regions=["socket 1"]),
                                episode_id="probe", views=DEFAULT_VIEWS)
    state = perceiver.look(SURVEY_VIEW, held=None)
    goal_site = backend.site("goal")
    tid = state.targets[0].target_id if state.targets else "socket 1"
    return [{
        "catalog_sha256": catalog.sha256()[:12],
        "survey_region_centre": [region.center_xy[0], region.center_xy[1],
                                 region.floor_top_z],
        "hole_true": list(goal_site),
        "survey_error_mm": mm([centre.x, centre.y, centre.z], goal_site),
        "entities": [{"id": e.entity_id, "seen_as": e.attributes.get("seen_as"),
                      "grounding": e.attributes.get("grounding"),
                      "pose": (None if e.pose is None else
                               [e.pose.position.x, e.pose.position.y, e.pose.position.z]),
                      "position_source": e.attributes.get("position_source"),
                      "support_estimate_xyz": e.attributes.get("support_estimate_xyz"),
                      "measured_length_m": e.attributes.get("measured_length_m"),
                      "geometry": (None if e.geometry is None else
                                   e.geometry.model_dump(mode="json")),
                      "held": str(e.held), "color": e.attributes.get("color"),
                      "shape": e.attributes.get("shape")} for e in state.entities],
        "extent_facts": [f.state for f in state.facts if f.kind == "extent"],
        "not_seen": [n.entity_id for n in state.not_seen],
        "occupancy": [o.model_dump(mode="json") for o in state.occupancy],
        "unparsed": list(state.unparsed),
        "axes": perceiver.axes,
        "perceived_distance_to_target_m": distance_m(state, "peg 1", tid),
        "target_tolerance_m": (round(float(state.targets[0].inner_half
                                           + min(state.entity("peg 1").geometry.half_extents.y,
                                                 state.entity("peg 1").geometry.half_extents.z)),
                                     4)
                               if state.has_entity("peg 1") and state.targets and
                               state.entity("peg 1").geometry else None),
        "privilege_free": bool(all(e.source.value == "sensor" for e in state.entities)),
        "_snapshot": state.model_dump(mode="json")}]


# ------------------------------------------------------------ 6. the gripper ---------
def check_aperture(backend: MuJoCoBackend) -> list[dict]:
    """`r_close` and the pad gap at both ends of the fingers' travel.

    These numbers are the calibration `executor.APERTURE_OPEN_M` / `APERTURE_CLOSED_M`
    hold. They are measured here, at the two commands the primitives actually issue
    (`OPEN_EFFORT` and `GRASP_EFFORT`), because a threshold copied from another arm's log
    would let `held_state()` report a confident "empty" on a hand that is half shut.
    """
    backend.task_index = 0
    backend.start()
    rows = []

    def probe(label: str, effort: float, *, squeeze_steps: int) -> None:
        hold = backend.servo(backend.hand(), OPEN_EFFORT, budget=8)
        squeeze = backend.squeeze(effort, steps=squeeze_steps)
        m = backend.measured()
        rows.append({"label": label, "effort": effort,
                     "qpos_r_close_m": backend.finger_qpos_m(),
                     "pad_gap_m": m["gripper_open_m"],
                     "squeeze_steps": squeeze["steps"],
                     "hold_error_m": hold["final_error_m"],
                     "hand": list(m["hand"]), "env_steps": backend.steps})

    probe("commanded open on air", OPEN_EFFORT, squeeze_steps=30)
    probe("squeezed on air", GRASP_EFFORT, squeeze_steps=60)
    peg = backend.site("pegGrasp")
    # now with something between the pads, which is the case the threshold exists for
    backend.servo((peg[0], peg[1], peg[2] + 0.02), OPEN_EFFORT, budget=150)
    probe("squeezed on the peg", GRASP_EFFORT, squeeze_steps=40)
    return rows


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    report: dict = {"out_dir": OUT,
                    "declared": {"table_top_z_m": TABLE_TOP_Z_M,
                                 "support_z_bounds": list(SUPPORT_Z_BOUNDS),
                                 "axis_min_pixels": AXIS_MIN_PIXELS,
                                 "axis_tolerance_m": AXIS_TOLERANCE_M}}
    backend = MuJoCoBackend("peg-insert-side-v3", task_index=0, seed=0, max_env_actions=500,
                            views=DEFAULT_VIEWS)
    backend.start()
    rig = MuJoCoRig(backend, os.path.join(OUT, "frames"), views=DEFAULT_VIEWS)
    report["rig_views"] = list(rig.views)

    sections = (("camera", lambda: check_camera(backend, rig)),
                ("colours", lambda: check_colours(backend, rig)),
                ("wall", lambda: check_wall(backend)),
                ("axis", lambda: check_axis(backend, rig)),
                ("snapshot", lambda: check_snapshot(backend, rig)),
                ("aperture", lambda: check_aperture(backend)))
    failed = None
    for name, run in sections:
        section(name)
        try:
            report[name] = run()
        except Exception as e:  # noqa: BLE001 - the partial answer is still an answer
            report[name] = {"error": f"{type(e).__name__}: {e}"}
            failed = (name, e)
        print(json.dumps(report[name], indent=2, default=str)[:3500], flush=True)
        if failed:
            break
    with open(os.path.join(OUT, "percept_check.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nwrote {os.path.join(OUT, 'percept_check.json')}", flush=True)
    if failed:
        raise SystemExit(f"section {failed[0]!r} raised: {failed[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
