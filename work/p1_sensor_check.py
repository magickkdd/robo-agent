"""P1-a sensor check: pick camera poses and measure the RGB-D geometry error.

Zero model cost. Three questions, answered with numbers instead of a plausible
looking picture:

1. Does this camera model agree with the renderer's own matrices? (project a body's
   true centre, compare against the depth image at that pixel)
2. How large is a 50 mm object in each candidate view, and is it in frame at all?
3. How many millimetres of error does back-projection through the depth image
   carry, per view, when the object is visible? — that number is what decides
   whether a VLM percept may be trusted with "in the tray" or only with "which
   object is where, roughly".

The privileged reads (`get_link_state`, the segmentation index map) appear **only**
in this file, only to grade the sensor model, and never in `embodied_agent/perception`
where a percept is built. That is the SPEC-v0.2 §3.3 split, checked by the file's
own import list rather than by trust.
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pybullet as p

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.scene import TABLE_TOP_Z, PhysicsScene          # noqa: E402
from embodied_agent.evaluation.tasks import build_case, find_case        # noqa: E402
from embodied_agent.perception.camera import column_major, depth_to_meters  # noqa: E402
from embodied_agent.perception.frames import capture, view_spec             # noqa: E402

OUT = "/tmp/v02_p1_sensor"

CANDIDATES = {
    # id: (eye, target, fov, up, size)  — all aimed at the working area between the
    # spawn slots (x~0.38-0.46) and the trays (x=0.66), from the +x side so the arm,
    # which lives at x<=0.3, cannot sit in front of the objects. Range and fov are
    # what decide whether a 50 mm body is 40 px or 90 px tall, and that is the
    # difference between a legible picture and a decorative one.
    "close_right": ((1.02, -0.34, 0.86), (0.52, 0.02, 0.62), 40.0, None, (800, 600)),
    "front_high":  ((1.18, 0.00, 0.95), (0.54, 0.00, 0.62), 40.0, None, (800, 600)),
    "front_low":   ((1.20, 0.00, 0.76), (0.54, 0.00, 0.63), 40.0, None, (800, 600)),
    "overhead":    ((0.58, 0.00, 1.30), (0.58, 0.00, 0.62), 45.0, (0.0, 1.0, 0.0), (800, 600)),
    "high_angle":  ((1.06, -0.30, 1.24), (0.54, 0.00, 0.62), 40.0, None, (800, 600)),
    "legacy":      None,   # the v0.1 hard-coded pose, for the before/after number
}


def body_corners(eid, geom, pos, orn):
    """The 8 (box) or 12 (cylinder) world extremes of a body's collision shape."""
    from embodied_agent.core.contracts import footprint_half_xy_from_quat
    hx, hy = footprint_half_xy_from_quat(geom, orn)
    hz = geom.half_h_vertical
    pts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                pts.append([pos[0] + sx * hx, pos[1] + sy * hy, pos[2] + sz * hz])
    return np.array(pts)


def analyze(scene: PhysicsScene, camera, rgb, depth, label):
    rows = []
    for eid, d in scene.objects.items():
        pos, orn = scene.object_pose(eid)
        geom = d["geometry"]
        try:
            px, py, dw = camera.project(pos)
        except ValueError as e:
            rows.append({"entity_id": eid, "in_frame": False, "why": str(e)})
            continue
        corners = body_corners(eid, geom, pos, orn)
        proj = np.array([camera.project(c)[:2] for c in corners])
        w_px = float(proj[:, 0].max() - proj[:, 0].min())
        h_px = float(proj[:, 1].max() - proj[:, 1].min())
        in_frame = bool(0 <= px < camera.width and 0 <= py < camera.height)
        row = {"entity_id": eid, "attributes": dict(d["attributes"]),
               "true_xyz_mm": [round(1000 * float(v), 1) for v in pos],
               "pixel": [round(float(px), 1), round(float(py), 1)],
               "in_frame": in_frame, "size_px": [round(w_px, 1), round(h_px, 1)],
               "true_range_m": round(float(np.linalg.norm(np.asarray(pos) - np.array(
                   [camera.eye.x, camera.eye.y, camera.eye.z]))), 4)}
        if in_frame:
            xi, yi = int(round(px)), int(round(py))
            z_read = depth_to_meters(float(depth[yi, xi]), camera.near_m, camera.far_m)
            p_read = camera.unproject(xi, yi, float(depth[yi, xi]))
            err = np.array([p_read.x - pos[0], p_read.y - pos[1], p_read.z - pos[2]])
            row["depth_read_m"] = round(z_read, 4)
            row["backproj_err_mm"] = [round(1000 * float(v), 1) for v in err]
            row["backproj_err_norm_mm"] = round(1000 * float(np.linalg.norm(err)), 1)
            # The decisive test of the camera model: if the projected pixel really is
            # the object, the *nearest* depth within a box the size of the object must
            # be its front surface, i.e. a little nearer than the centre. A mirrored or
            # mis-scaled projection puts that window on the table instead, and the
            # number says so in millimetres.
            r = max(3, int(round(max(w_px, h_px) / 2.0)))
            win = depth[max(0, yi - r):min(camera.height, yi + r + 1),
                        max(0, xi - r):min(camera.width, xi + r + 1)]
            dmin = float(win.min())
            row["window_min_range_m"] = round(depth_to_meters(
                dmin, camera.near_m, camera.far_m), 4)
            row["front_vs_centre_err_mm"] = round(1000 * float(
                row["true_range_m"] - row["window_min_range_m"]), 1)
            # `backproj_err_mm` is *not* an error of the camera model: the body centre is
            # never the surface the ray hits, so a few millimetres of z difference is the
            # expected answer here and only its *size* relative to the body is informative.
        rows.append(row)
    return {"view": label, "camera_id": camera.camera_id,
            "fov_deg": camera.fov_deg, "size": [camera.width, camera.height],
            "intrinsics": camera.intrinsics(), "objects": rows,
            "n_in_frame": sum(1 for r in rows if r.get("in_frame")),
            "n_front_surface_measured": sum(1 for r in rows if "front_vs_centre_err_mm" in r),
            "median_front_vs_centre_err_mm": (round(float(np.median(
                [abs(r["front_vs_centre_err_mm"]) for r in rows
                 if "front_vs_centre_err_mm" in r])), 1)
                if any("front_vs_centre_err_mm" in r for r in rows) else None)}


def legacy_camera(size=(640, 480)):
    """Reproduce `PhysicsScene.render`'s hard-coded pose as a CameraSpec by solving
    yaw/pitch/distance into an eye position, so the legacy view is compared on the
    same terms instead of waved at."""
    target = np.array([0.6, 0.0, 0.5])
    distance, yaw, pitch = 1.5, 55.0, -38.0
    y, pt = math.radians(yaw), math.radians(pitch)
    direction = np.array([math.cos(pt) * math.cos(y), math.cos(pt) * math.sin(y), math.sin(pt)])
    eye = target - distance * direction
    return view_spec("legacy", eye, target, fov_deg=55.0, width=size[0], height=size[1],
                     near_m=0.02, far_m=5.0)


def estimator_check(scene: PhysicsScene, camera, depth):
    """How good can the RGB-D centre estimate be when the *box* is perfect?

    The box here comes from the privileged corner projection, i.e. this is the
    upper bound of the geometry channel with the grounding error factored out. If
    this number is already too large to place an object, no VLM can rescue it, and
    the honest conclusion is that the VLM arm must verify semantically instead of
    geometrically — which is what P1-d does."""
    from embodied_agent.core.contracts import footprint_half_xy_from_quat
    from embodied_agent.perception.geometry import estimate_centre
    rows = []
    for eid, d in scene.objects.items():
        pos, orn = scene.object_pose(eid)
        geom = d["geometry"]
        hx, hy = footprint_half_xy_from_quat(geom, orn)
        hz = geom.half_h_vertical
        pts = np.array([[pos[0] + sx * hx, pos[1] + sy * hy, pos[2] + sz * hz]
                        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
        proj = np.array([camera.project(p) for p in pts])
        bbox = (float(proj[:, 0].min()), float(proj[:, 1].min()),
                float(proj[:, 0].max()), float(proj[:, 1].max()))
        try:
            centre, ev = estimate_centre(camera, depth, bbox, half_height_m=hz,
                                         z_bounds=(TABLE_TOP_Z - 0.06, TABLE_TOP_Z + 0.12))
        except ValueError as e:
            rows.append({"entity_id": eid, "error": str(e)})
            continue
        if centre is None:
            rows.append({"entity_id": eid, "support": ev.get("support_surface"),
                         "refused": ev.get("rejected") or ev.get("why")})
            continue
        err = np.array([centre.x - pos[0], centre.y - pos[1], centre.z - pos[2]])
        rows.append({"entity_id": eid, "bbox": [round(v, 1) for v in bbox],
                     "centre_mm": [round(1000 * float(v), 1) for v in
                                   (centre.x, centre.y, centre.z)],
                     "err_xy_mm": round(1000 * float(np.hypot(err[0], err[1])), 1),
                     "err_z_mm": round(1000 * float(err[2]), 1),
                     "err_norm_mm": round(1000 * float(np.linalg.norm(err)), 1),
                     "support_z_m": ev.get("support_z_m"),
                     "support": ev.get("support_surface")})
    ok = [r for r in rows if "err_norm_mm" in r]
    return {"rows": rows, "n_estimated": len(ok),
            "n_refused": sum(1 for r in rows if "refused" in r),
            "n_error": sum(1 for r in rows if "error" in r),
            "median_err_xy_mm": round(float(np.median([r["err_xy_mm"] for r in ok])), 1) if ok else None,
            "max_err_xy_mm": round(float(np.max([r["err_xy_mm"] for r in ok])), 1) if ok else None,
            "median_err_z_mm": round(float(np.median([r["err_z_mm"] for r in ok])), 1) if ok else None,
            "n_ambiguous_support": sum(1 for r in rows
                                       if (r.get("support") or {}).get("ambiguous"))}


def make_camera(label, spec):
    if spec is None:
        return legacy_camera()
    eye, target, fov, up, size = spec
    return view_spec(label, eye, target, fov_deg=fov, width=size[0], height=size[1],
                     **({} if up is None else {"up": up}))


def main():
    os.makedirs(OUT, exist_ok=True)
    case = find_case("dev_c5")
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    print("case", case.task_id, "objects", list(scene.objects), flush=True)
    report = {"case": case.task_id, "seed": case.seed, "views": []}

    # the two renderers must agree on the same geometry: mine and pybullet's
    cam = make_camera("front_high", CANDIDATES["front_high"])
    mine = (column_major(cam.view_matrix), column_major(cam.projection_matrix))
    theirs = (p.computeViewMatrix(cameraEyePosition=list(cam.eye.as_list()),
                                  cameraTargetPosition=list(cam.target.as_list()),
                                  cameraUpVector=list(cam.up.as_list()), physicsClientId=scene.cid),
              p.computeProjectionMatrixFOV(fov=cam.fov_deg, aspect=cam.aspect,
                                           nearVal=cam.near_m, farVal=cam.far_m,
                                           physicsClientId=scene.cid))
    agree = {"view_max_abs_diff": round(float(np.abs(np.array(mine[0]) - np.array(theirs[0])).max()), 9),
             "proj_max_abs_diff": round(float(np.abs(np.array(mine[1]) - np.array(theirs[1])).max()), 9)}
    report["matrix_agreement_with_pybullet"] = agree
    print("matrices", agree, flush=True)

    for label, spec in CANDIDATES.items():
        camera = make_camera(label, spec)
        frame, rgb, depth = capture(scene, OUT, camera, f"{label}", sim_time=scene.sim_time)
        one = analyze(scene, camera, rgb, depth, label)
        one["frame"] = frame.to_payload()
        one["estimator"] = estimator_check(scene, camera, depth)
        report["views"].append(one)
        print(f"--- {label}: in_frame {one['n_in_frame']}/{len(scene.objects)} "
              f"median_front_vs_centre_err {one['median_front_vs_centre_err_mm']} mm "
              f"| estimator n {one['estimator']['n_estimated']} refused {one['estimator']['n_refused']} "
              f"median_err_xy {one['estimator']['median_err_xy_mm']} mm "
              f"max {one['estimator']['max_err_xy_mm']} mm "
              f"median_err_z {one['estimator']['median_err_z_mm']} mm "
              f"ambiguous_support {one['estimator']['n_ambiguous_support']}", flush=True)
        for r in one["objects"]:
            print("   ", json.dumps(r, ensure_ascii=False), flush=True)
        for r in one["estimator"]["rows"]:
            print("    est", json.dumps({k: v for k, v in r.items() if k != "support"},
                                        ensure_ascii=False), flush=True)
    with open(os.path.join(OUT, "p1_sensor_check.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("wrote", os.path.join(OUT, "p1_sensor_check.json"))
    scene.close()


if __name__ == "__main__":
    main()
