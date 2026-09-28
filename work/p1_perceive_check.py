"""P1-b perception check: does a percept assembled from pixels match the truth?

Zero model cost. `StubReader` (numpy colour segmentation + silhouette fit) runs on the
same PNG a VLM would be shown, `PerceptionAssembler` turns its boxes into a
`PerceptionObservation`, and the *privileged* channel appears only in the grading
lines below — never in what is being measured. Four questions, each answered with a
number rather than a good-looking picture:

1. **Attributes.** Does the colour word match, and does a silhouette fit alone name
   the right shape? `cube` vs `cuboid` is the hard pair: they are one collision shape
   and two appearances, and a wrong answer here propagates into `extent`, so into the
   half-height and every millimetre downstream.
2. **Metric position.** Same measurement as P1-a, but now with a *seen* box instead of
   the privileged corner projection, so the error includes the segmentation. This is
   the number that decides whether "in the tray" (a 131 mm interior) is a claim this
   percept is allowed to make.
3. **Cross-view agreement.** The table does not move between frames, so the
   disagreement between two views of it is the percept's own noise floor — and
   `move_threshold_m = 25 mm` is only legal if that floor sits under it. Anything
   above it would make §5.1's change detection fire on a camera move.
4. **Support surface.** How far a measured `support_z` lands from the declared table
   top, and from a tray floor when a body stands in a tray. This is what
   `support_z_tol_m = 12 mm` has to cover.

Run:  PYTHONPATH=. python work/p1_perceive_check.py [case_id]
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.contracts import footprint_half_xy_from_quat          # noqa: E402
from embodied_agent.core.scene import TABLE_TOP_Z, PhysicsScene                # noqa: E402
from embodied_agent.evaluation.calibration import cell_catalog, task_context_of  # noqa: E402
from embodied_agent.evaluation.tasks import find_case                          # noqa: E402
from embodied_agent.perception.frames import camera_for, capture               # noqa: E402
from embodied_agent.perception.observe import (PerceptionAssembler,            # noqa: E402
                                               StubReader, TaskContext)

OUT = "/tmp/v02_p1_perceive"
VIEWS_TO_TRY = ("main", "front_high", "overhead")


def truth(scene: PhysicsScene) -> dict[str, dict]:
    """The privileged answer key, for grading only."""
    rows = {}
    for eid, d in scene.objects.items():
        pos, orn = scene.object_pose(eid)
        attrs = d.get("attributes") or {}
        hx, hy = footprint_half_xy_from_quat(d["geometry"], orn)
        rows[str(attrs.get("color"))] = {
            "entity_id": eid,
            "color": str(attrs.get("color")),
            "shape": str(attrs.get("shape")),
            "xy": [pos[0], pos[1]],
            "z": pos[2],
            "half_h": d["geometry"].half_h_vertical,
            "footprint_hx_hy": [hx, hy],
            "footprint_radius": math.hypot(hx, hy),
        }
    return rows


def expected_floor_z(scene: PhysicsScene, x: float, y: float) -> float:
    """Which surface a body at this ground point really rests on (grading only)."""
    for t in scene.trays.values():
        if (abs(x - t["center"][0]) <= t["inner_half"]
                and abs(y - t["center"][1]) <= t["inner_half"]):
            return float(t["floor_top"])
    return TABLE_TOP_Z


def grade_percept(obs, catalog, key, scene) -> list[dict]:
    rows = []
    for det in obs.detections:
        colour = str(det.state_attributes.get("color"))
        t = key.get(colour)
        relation = next((r for r in obs.relations
                         if r.subject_id == det.entity_id and r.predicate != "next_to"), None)
        out = {
            "percept_id": det.entity_id,
            "color": colour,
            "shape_seen": str(det.state_attributes.get("shape")),
            "shape_truth": t["shape"] if t else None,
            "shape_correct": bool(t and t["shape"] == str(det.state_attributes.get("shape"))),
            "confidence": det.confidence,
            "how_identified": det.how_identified,
            "pose_is_none": det.pose is None,
            "orientation_measured": det.state_attributes.get("orientation_measured"),
            "extent_matches_catalog": bool(det.extent is not None and det.kind in catalog.shapes
                                           and catalog.geometry(det.kind) == det.extent),
            "bbox_px": det.state_attributes.get("bbox_px"),
            "relation": None if relation is None else f"{relation.predicate}:{relation.related_id}",
            "truth_relation": (None if t is None else
                               f"on:{expected_region(scene, *t['xy'])}"),
            "uncertainties": [],
            "not_located": True,
        }
        pos = det.state_attributes.get("position_xyz_m")
        if pos is not None:
            out["not_located"] = False
            out["position_xyz_m"] = pos
            if t:
                out["position_error_mm"] = round(math.hypot(pos[0] - t["xy"][0],
                                                            pos[1] - t["xy"][1]) * 1000, 2)
                out["z_error_mm"] = round((pos[2] - t["z"]) * 1000, 2)
                out["centre_xy_truth"] = [round(t["xy"][0], 4), round(t["xy"][1], 4)]
            sz = det.state_attributes.get("support_z_m")
            if sz is not None and t:
                floor = expected_floor_z(scene, t["xy"][0], t["xy"][1])
                out["support_z_m"] = round(float(sz), 4)
                out["support_expected_floor_z"] = round(float(floor), 4)
                out["support_error_mm"] = round((float(sz) - float(floor)) * 1000, 2)
                out["within_support_tol"] = bool(abs(float(sz) - float(floor))
                                                 <= catalog.support_z_tol_m)
        for u in obs.uncertainties:
            if det.entity_id in u.what:
                out["uncertainties"].append({"why": u.why, "what": u.what[:110]})
        rows.append(out)
    return rows


def expected_region(scene: PhysicsScene, x: float, y: float) -> str:
    for tid, t in scene.trays.items():
        if (abs(x - t["center"][0]) <= t["inner_half"]
                and abs(y - t["center"][1]) <= t["inner_half"]):
            return tid
    return "table"


def silhouette_attribution(scene, camera, key, reading) -> list[dict]:
    """Blob size versus the *true* projected silhouette, per reported box.

    `best_fitting_shape` can only rank the declared candidates against the box it is
    given, so when a shape is wrong the fit and the mask are two different suspects and
    the IoU cannot tell them apart. This does: it projects the eight corners of the
    body's own declared box, at its measured pose, and compares that rectangle with the
    blob the segmenter reported. A blob narrower than the body's true outline is a mask
    problem (the hue threshold lost a dim flank), and no shape vocabulary can fix that;
    a blob that matches its outline while the fit still chooses wrongly is a fit
    problem. Grading only — the percept never sees any of it."""
    rows = []
    for o in reading.objects:
        t = key.get(str(o.color))
        if not t:
            continue
        hx, hy = t["footprint_hx_hy"]
        hz = t["half_h"]
        corners = np.array([[sx * hx, sy * hy, sz * hz]
                            for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
        pts = corners + np.array([t["xy"][0], t["xy"][1], t["z"]])
        proj = np.array([camera.project(pt) for pt in pts])
        tx0, tx1 = float(proj[:, 0].min()), float(proj[:, 0].max())
        ty0, ty1 = float(proj[:, 1].min()), float(proj[:, 1].max())
        ox0, oy0, ox1, oy1 = (float(v) for v in o.bbox)
        rows.append({
            "color": str(o.color), "shape_truth": t["shape"], "shape_seen": o.shape,
            "true_px": [round(tx1 - tx0 + 1, 1), round(ty1 - ty0 + 1, 1)],
            "blob_px": [round(ox1 - ox0 + 1, 1), round(oy1 - oy0 + 1, 1)],
            "true_bbox_px": [round(tx0, 1), round(ty0, 1), round(tx1, 1), round(ty1, 1)],
            "width_deficit_px": round((tx1 - tx0 + 1) - (ox1 - ox0 + 1), 1),
            "height_deficit_px": round((ty1 - ty0 + 1) - (oy1 - oy0 + 1), 1),
            "centre_offset_px": [round(((ox0 + ox1) - (tx0 + tx1)) / 2.0, 1),
                                 round(((oy0 + oy1) - (ty0 + ty1)) / 2.0, 1)],
        })
    return rows


def displacement_sweep(scene, catalog, reader, assembler, task, key) -> dict:
    """Command a known displacement, then ask only the picture what changed.

    The privileged write exists to *create* a state worth looking at; every reported
    number still comes from pixels. Each trial starts from the same base pose, so a
    change row has exactly one possible cause, and the commanded size is swept across
    the assembler's own threshold from both sides — 5 mm, which must not be reported as
    a move, through 60 mm, which must be. What makes this worth running is the second
    column: the millimetres the percept *says* it saw, which is the only number a
    downstream replanner will act on.
    """
    import ast

    import pybullet as p

    colour, t = max(key.items(), key=lambda kv: abs(kv[1]["xy"][1]))
    base_xy, entity_id = list(t["xy"]), t["entity_id"]
    d = scene.objects[entity_id]
    rest_z = float(d["rest_z_table"])
    camera = camera_for("main")

    def place_and_perceive(xy, name, previous):
        p.resetBasePositionAndOrientation(d["body"], [xy[0], xy[1], rest_z],
                                          p.getQuaternionFromEuler([0, 0, 0.05],
                                                                   physicsClientId=scene.cid),
                                          physicsClientId=scene.cid)
        for _ in range(40):
            p.stepSimulation(physicsClientId=scene.cid)
        scene.sim_time += 0.4
        pos, _orn = scene.object_pose(entity_id)
        frame, _, _ = capture(scene, OUT, camera, name, sim_time=scene.sim_time)
        reading = reader.read(frame, catalog=catalog, task=task)
        obs = assembler.assemble(frame, reading, previous=previous, frame_index=1)
        return pos, obs

    # the reference percept: the body at its base pose, nothing else moved
    base_pos, base_obs = place_and_perceive(base_xy, "disp_base", None)
    rows = []
    for delta_mm in (5, 10, 15, 20, 25, 30, 40, 60):
        target = [base_xy[0], base_xy[1] - delta_mm / 1000.0]
        pos, obs = place_and_perceive(target, f"disp_{delta_mm}", base_obs)
        moved = [c for c in obs.changes
                 if c.subject_id == f"seen:{colour}" and c.attribute == "position"]
        near = [c for c in obs.changes
                if c.subject_id == f"seen:{colour}"
                and c.attribute == "position_below_threshold"]
        reported = None
        if moved:
            before = np.array(ast.literal_eval(moved[0].before), dtype=float)
            after = np.array(ast.literal_eval(moved[0].after), dtype=float)
            reported = round(float(math.hypot(*(after[:2] - before[:2])) * 1000.0), 2)
        det = next((x for x in obs.detections if x.state_attributes.get("color") == colour),
                   None)
        seen = det.state_attributes.get("position_xyz_m") if det else None
        rows.append({
            "commanded_mm": delta_mm,
            "true_mm": round(math.hypot(pos[0] - base_pos[0], pos[1] - base_pos[1]) * 1000, 2),
            "reported_position_change": bool(moved),
            "reported_below_threshold": bool(near),
            "reported_delta_mm": reported,
            "delta_error_mm": (None if reported is None else round(reported - delta_mm, 2)),
            "position_error_mm": (None if seen is None else
                                  round(math.hypot(seen[0] - pos[0], seen[1] - pos[1]) * 1000, 2)),
            "other_changes": sorted({c.attribute for c in obs.changes}),
            "n_changes": len(obs.changes),
        })
    below = [r for r in rows if r["commanded_mm"] < assembler.move_threshold_m * 1000]
    above = [r for r in rows if r["commanded_mm"] >= assembler.move_threshold_m * 1000]
    return {
        "colour": colour, "entity_id": entity_id,
        "move_threshold_m": assembler.move_threshold_m,
        "rows": rows,
        "reported_while_under_threshold": sum(1 for r in below if r["reported_position_change"]),
        "missed_over_threshold": sum(1 for r in above if not r["reported_position_change"]),
        "median_delta_error_mm": (round(float(np.median(
            [r["delta_error_mm"] for r in above if r["delta_error_mm"] is not None])), 2)
            if any(r["delta_error_mm"] is not None for r in above) else None),
    }


def main() -> None:
    case_id = sys.argv[1] if len(sys.argv) > 1 else "dev_c5"
    os.makedirs(OUT, exist_ok=True)
    case = find_case(case_id)
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    catalog = cell_catalog(scene.trays.values())
    task = TaskContext(**task_context_of(case))
    reader = StubReader()
    assembler = PerceptionAssembler(catalog)
    key = truth(scene)

    report = {
        "case": case_id, "seed": case.seed, "n_objects": len(key),
        "truth": {c: {"xy": [round(v["xy"][0], 4), round(v["xy"][1], 4)], "shape": v["shape"],
                      "z": round(v["z"], 4), "region": expected_region(scene, *v["xy"])}
                  for c, v in key.items()},
        "catalog": {"version": catalog.version, "sha256": catalog.sha256(),
                    "colours": sorted(catalog.colours), "shapes": sorted(catalog.shapes),
                    "table_top_z": catalog.table_top_z,
                    "support_z_tol_m": catalog.support_z_tol_m,
                    "support_z_bounds": list(catalog.support_z_bounds),
                    "regions": [{"target_id": r.target_id, "label": r.label,
                                 "center_xy": [round(c, 4) for c in r.center_xy],
                                 "inner_half": round(r.inner_half, 4),
                                 "floor_top_z": r.floor_top_z, "capacity": r.capacity}
                                for r in catalog.regions]},
        "thresholds": assembler.thresholds(),
        "task_context": task.model_dump(),
        "reader": {"model": reader.model, "channel": reader.channel},
        "views": {},
    }

    percepts: dict[str, object] = {}
    positions: dict[str, dict[str, list[float]]] = {}
    for view in VIEWS_TO_TRY:
        camera = camera_for(view)
        frame, _, _ = capture(scene, OUT, camera, view, sim_time=scene.sim_time)
        reading = reader.read(frame, catalog=catalog, task=task)
        obs = assembler.assemble(frame, reading, frame_index=0)
        percepts[view] = obs
        graded = grade_percept(obs, catalog, key, scene)
        errs = [g["position_error_mm"] for g in graded if "position_error_mm" in g]
        sup = [g["support_error_mm"] for g in graded if "support_error_mm" in g]
        positions[view] = {g["color"]: g["position_xyz_m"] for g in graded
                           if g.get("position_xyz_m") is not None}
        report["views"][view] = {
            "frame": frame.to_payload(),
            "reading_objects": [o.model_dump() for o in reading.objects],
            "detections": graded,
            "n_detected": len(obs.detections),
            "n_truth": len(key),
            "n_located": sum(1 for g in graded if not g["not_located"]),
            "n_shape_correct": sum(1 for g in graded if g["shape_correct"]),
            "relations_right": sum(1 for g in graded
                                   if g["relation"] == g["truth_relation"]),
            "position_error_mm": {"n": len(errs),
                                  "median": round(float(np.median(errs)), 2) if errs else None,
                                  "max": round(max(errs), 2) if errs else None},
            "support_error_mm": {"median": round(float(np.median(sup)), 2) if sup else None,
                                 "max_abs": round(max(abs(s) for s in sup), 2) if sup else None,
                                 "all_within_tol": bool(all(
                                     abs(s) <= catalog.support_z_tol_m * 1000 for s in sup))},
            "relations": [r.model_dump() for r in obs.relations],
            "visibility": [v.model_dump() for v in obs.visibility],
            "regions": [r.model_dump() for r in obs.regions],
            "uncertainties": [{"why": u.why, "what": u.what} for u in obs.uncertainties],
            "why_counts": {w: sum(1 for u in obs.uncertainties if u.why == w)
                           for w in sorted({u.why for u in obs.uncertainties})},
            "provenance_keys": sorted(obs.provenance),
            # the two audit trails a claim rests on: which shapes were scored against
            # this box and where each was projected, and how the metric solve treated the
            # depth it was given, including every refusal. A number in the phase log has
            # to be checkable against these without re-running the segmenter.
            "silhouette_fit": (obs.provenance or {}).get("silhouette_fit"),
            "geometry_evidence": (obs.provenance or {}).get("geometry"),
            "silhouette_attribution": silhouette_attribution(scene, camera, key, reading),
            "http_requests_this_call": obs.provenance.get("http_requests_this_call"),
            "cost_estimate_usd": obs.provenance.get("cost_estimate_usd"),
            "round_trip_lossless": bool(type(obs).model_validate_json(
                obs.model_dump_json()).model_dump() == obs.model_dump()),
        }

    # ---- cross-view agreement: the table did not move, so this is the noise floor ----
    pairs = []
    for colour in sorted({c for row in positions.values() for c in row}):
        seen = {v: row[colour] for v, row in positions.items() if colour in row}
        names = sorted(seen)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pa, pb = seen[a], seen[b]
                pairs.append({"color": colour, "views": [a, b],
                              "gap_xy_mm": round(math.hypot(pa[0] - pb[0],
                                                            pa[1] - pb[1]) * 1000, 2),
                              "gap_z_mm": round((pa[2] - pb[2]) * 1000, 2)})
    gaps = [p["gap_xy_mm"] for p in pairs]
    report["cross_view_noise"] = {
        "pairs": pairs,
        "median_gap_xy_mm": round(float(np.median(gaps)), 2) if gaps else None,
        "max_gap_xy_mm": round(max(gaps), 2) if gaps else None,
        "move_threshold_m": assembler.move_threshold_m,
    }

    # ---- same-view repeat: the deterministic floor under any change claim ----
    same_view = {}
    for view in VIEWS_TO_TRY:
        camera = camera_for(view)
        frame, _, _ = capture(scene, OUT, camera, f"{view}_again", sim_time=scene.sim_time)
        reading = reader.read(frame, catalog=catalog, task=task)
        obs = assembler.assemble(frame, reading, previous=percepts[view], frame_index=1)
        same_view[view] = {
            "n_changes": len(obs.changes),
            "rows": [c.model_dump() for c in obs.changes],
            "camera_guard_fired": any("different views" in u.what for u in obs.uncertainties),
        }
    report["change_detection_same_view_repeat"] = same_view

    # ---- cross-view comparison: must now be refused, not reported ----
    changes = {}
    for view in VIEWS_TO_TRY[1:]:
        camera = camera_for(view)
        frame, _, _ = capture(scene, OUT, camera, f"{view}_vs_main", sim_time=scene.sim_time)
        reading = reader.read(frame, catalog=catalog, task=task)
        obs = assembler.assemble(frame, reading, previous=percepts["main"], frame_index=1)
        attrs = sorted({c.attribute for c in obs.changes})
        changes[view] = {
            "n_changes": len(obs.changes),
            "attributes": {a: sum(1 for c in obs.changes if c.attribute == a) for a in attrs},
            "rows": [c.model_dump() for c in obs.changes],
            "modalities": sorted({c.modality for c in obs.changes}),
            "false_position_changes": sum(1 for c in obs.changes if c.attribute == "position"),
            "refusal_recorded": any("different views" in u.what for u in obs.uncertainties),
        }
    report["change_detection_cross_view"] = changes
    report["cross_view_noise"]["false_position_changes_after_guard"] = sum(
        c["false_position_changes"] for c in changes.values())

    # ---- a commanded displacement: what does the change channel report, and how
    # many millimetres does it get right? This is where `move_threshold_m` earns its
    # value or does not.
    report["displacement_sweep"] = displacement_sweep(scene, catalog, reader, assembler,
                                                      task, key)

    # ---- a body seated in a tray: the region claim, measured ----
    # The privileged write here exists only to *create a state worth looking at*; the
    # percept still reads nothing but pixels. Grading reads poses after the fact.
    import pybullet as p

    def seat_in_tray(entity_id: str, target_id: str, dx: float, dy: float) -> dict:
        d = scene.objects[entity_id]
        tray = scene.trays[target_id]
        z = float(tray["floor_top"]) + float(d["geometry"].half_h_vertical)
        xy = [float(tray["center"][0]) + dx, float(tray["center"][1]) + dy]
        p.resetBasePositionAndOrientation(d["body"], [xy[0], xy[1], z],
                                          p.getQuaternionFromEuler([0, 0, 0.05],
                                                                   physicsClientId=scene.cid),
                                          physicsClientId=scene.cid)
        for _ in range(40):
            p.stepSimulation(physicsClientId=scene.cid)
        scene.sim_time += 40 * 0.01
        pos, _orn = scene.object_pose(entity_id)
        return {"entity_id": entity_id, "tray": target_id,
                "rest_xy": [round(pos[0], 4), round(pos[1], 4)],
                "rest_z": round(pos[2], 4), "floor_top": round(float(tray["floor_top"]), 4),
                "colour": str(d["attributes"]["color"])}

    tray_rows = []
    for i, eid in enumerate(sorted(scene.objects)):
        info = seat_in_tray(eid, "tray_middle", -0.075 + 0.05 * i, 0.0)
        camera = camera_for("main")
        frame, _, _ = capture(scene, OUT, camera, f"tray_{i}", sim_time=scene.sim_time)
        reading = reader.read(frame, catalog=catalog, task=task)
        obs = assembler.assemble(frame, reading, frame_index=2 + i)
        colour = info["colour"]
        det = next((d for d in obs.detections
                    if d.state_attributes.get("color") == colour), None)
        rel = next((r for r in obs.relations if det and r.subject_id == det.entity_id
                    and r.predicate != "next_to"), None)
        reg = next((r for r in obs.regions if r.region_id == "tray_middle"), None)
        pos = det.state_attributes.get("position_xyz_m") if det else None
        sz = det.state_attributes.get("support_z_m") if det else None
        tray_rows.append({
            **info,
            "detected": det is not None,
            "relation": None if rel is None else f"{rel.predicate}:{rel.related_id}",
            "relation_correct": bool(rel is not None and rel.predicate == "in"
                                     and rel.related_id == "tray_middle"),
            "position_error_mm": (None if pos is None else
                                  round(math.hypot(pos[0] - info["rest_xy"][0],
                                                   pos[1] - info["rest_xy"][1]) * 1000, 2)),
            "support_error_mm": (None if sz is None else
                                 round((float(sz) - info["floor_top"]) * 1000, 2)),
            "within_support_tol": (None if sz is None else bool(
                abs(float(sz) - info["floor_top"]) <= catalog.support_z_tol_m)),
            "tray_occupants": None if reg is None else list(reg.occupants),
            "tray_free": None if reg is None else str(reg.free),
            "tray_capacity_note": None if reg is None else reg.capacity_note,
            "uncertainties": [{"why": u.why, "what": u.what[:120]} for u in obs.uncertainties],
        })
    report["in_tray"] = {
        "n_seated": len(tray_rows),
        "n_relation_correct": sum(1 for r in tray_rows if r["relation_correct"]),
        "capacity": catalog.regions[0].capacity,
        "rows": tray_rows,
    }

    with open(os.path.join(OUT, "p1_perceive_check.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"case {case_id} seed {case.seed}: {len(key)} bodies, "
          f"catalog {catalog.version} {catalog.sha256()[:12]}")
    print(f"truth: " + ", ".join(f"{c}/{v['shape']}@({v['xy'][0]:.3f},{v['xy'][1]:.3f})"
                                 for c, v in sorted(key.items())))
    print(f"task context items={task.items} regions={task.regions}")
    for view in VIEWS_TO_TRY:
        r = report["views"][view]
        print(f"\n[{view}] detected {r['n_detected']}/{r['n_truth']}, located {r['n_located']}, "
              f"shapes right {r['n_shape_correct']}, relations right {r['relations_right']}, "
              f"round-trip lossless {r['round_trip_lossless']}")
        print(f"  position mm  median {r['position_error_mm']['median']}  "
              f"max {r['position_error_mm']['max']}   "
              f"support mm median {r['support_error_mm']['median']}  "
              f"max|e| {r['support_error_mm']['max_abs']}  within tol {r['support_error_mm']['all_within_tol']}")
        print(f"  relations {[(x['subject_id'][5:], x['predicate'], x['related_id']) for x in r['relations']]}")
        print(f"  regions   {[(x['region_id'], x['free'], len(x['occupants']), round(x['confidence'], 2)) for x in r['regions']]}")
        print(f"  visibility {[(x['entity_id'][5:], x['visible'], x['occluded_by']) for x in r['visibility']]}")
        print(f"  uncertainties {r['why_counts']}")
        for g in r["detections"]:
            print(f"    {g['color']:7s} seen={g['shape_seen']:7s} truth={str(g['shape_truth']):7s} "
                  f"xy_err={str(g.get('position_error_mm')):>6s}mm z_err={str(g.get('z_error_mm')):>6s}mm "
                  f"sup_err={str(g.get('support_error_mm')):>6s}mm conf={g['confidence']} "
                  f"box={g['bbox_px']}")
        for a in r["silhouette_attribution"]:
            print(f"      attr {a['color']:7s} true={str(a['true_px']):11s} blob={str(a['blob_px']):11s} "
                  f"Δw={a['width_deficit_px']:>6} Δh={a['height_deficit_px']:>6} "
                  f"centre_off={a['centre_offset_px']}  {a['shape_seen']}/{a['shape_truth']}")
    n = report["cross_view_noise"]
    print(f"\ncross-view noise: median {n['median_gap_xy_mm']} mm, max {n['max_gap_xy_mm']} mm "
          f"against a move threshold of {assembler.move_threshold_m * 1000:.0f} mm; "
          f"position changes reported across views {n['false_position_changes_after_guard']}")
    for view, c in changes.items():
        print(f"  [{view}] {c['n_changes']} changes {c['attributes']} "
              f"modalities={c['modalities']} refusal_recorded={c['refusal_recorded']}")
    for view, s in same_view.items():
        print(f"  same-view repeat [{view}]: {s['n_changes']} changes, "
              f"camera_guard_fired={s['camera_guard_fired']}")
    d = report["displacement_sweep"]
    print(f"\ndisplacement sweep on {d['colour']} "
          f"(threshold {d['move_threshold_m'] * 1000:.0f} mm): reported while under "
          f"{d['reported_while_under_threshold']}, missed over threshold "
          f"{d['missed_over_threshold']}, median delta error {d['median_delta_error_mm']} mm")
    for r in d["rows"]:
        print(f"    commanded {r['commanded_mm']:>3}mm  true {r['true_mm']:>5}mm  "
              f"reported={str(r['reported_position_change']):5s} below-threshold-row="
              f"{str(r['reported_below_threshold']):5s}  delta={str(r['reported_delta_mm']):>7s}mm "
              f"err={str(r['delta_error_mm']):>7s}mm  pos_err={r['position_error_mm']}mm")
    t = report["in_tray"]
    print(f"\nin_tray: {t['n_relation_correct']}/{t['n_seated']} reported in:tray_middle "
          f"against a declared capacity of {t['capacity']}")
    for r in t["rows"]:
        print(f"    {r['colour']:7s} rest=({r['rest_xy'][0]:.3f},{r['rest_xy'][1]:.3f}) "
              f"rel={str(r['relation']):18s} pos_err={str(r['position_error_mm']):>6s}mm "
              f"sup_err={str(r['support_error_mm']):>5s}mm within_tol={r['within_support_tol']} "
              f"occupants={r['tray_occupants']} free={r['tray_free']}")
    scene.close()
    print(f"\nwrote {os.path.join(OUT, 'p1_perceive_check.json')}")


if __name__ == "__main__":
    main()
