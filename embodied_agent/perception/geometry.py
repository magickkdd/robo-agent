"""From a model's box to a metric point: the RGB-D geometry estimator.

The split this file exists to keep straight (SPEC-v0.2 §3.3, §5.1):

* the **model** decides *where in the picture* something is and *what it is* — that
  is the only thing a VLM is good at, and it is the only semantic input here;
* the **depth image** turns that box into metres — no simulator state is read;
* the **declared scene layout** (table height, tray rectangles, the object
  catalogue's dimensions) supplies the static calibration a real cell has: where
  its surfaces are and how big its parts are. It contains no per-episode truth:
  which object moved where, what is held, and what is occluded all come from the
  percept or stay `unknown`.

A camera sees a *surface*, not a centre. What it can measure honestly is the
height of the surface an object stands on (`support_surface_z`, sampled at the
bottom row of the reported box), and the object's own declared half-height turns
that into a centre — a derivation with a name, an error you can measure
(`work/p1_sensor_check.py` reports it in millimetres) and a refusal when the
surface is not there.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..core.contracts import Vec3
from .camera import CameraSpec, depth_to_meters

# Two side samples of one floor that disagree by more than this are not one floor:
# something else stands beside the object, and the lower surface may still be the
# right support while the percept should say so. 12 mm is roughly the half-height of
# the shortest object in the catalogue, so a spread below it cannot hide a wrong
# support choice, and a spread above it is worth an UncertaintyItem (§5.1 不确定信息标记).
AMBIGUOUS_SUPPORT_M = 0.012


def _clip_bbox(bbox, shape) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    x0, y0, x1, y1 = (int(round(float(v))) for v in bbox)
    x0, x1 = max(0, min(w - 1, x0)), max(0, min(w - 1, x1))
    y0, y1 = max(0, min(h - 1, y0)), max(0, min(h - 1, y1))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"bbox {bbox} collapses outside a {w}x{h} frame")
    return x0, y0, x1, y1


def bbox_depth_stats(depth: np.ndarray, bbox) -> dict:
    """Range statistics of the surface a box covers, in metres.

    `near_m` is the front face; `far_m` is whatever the box also caught — the table
    behind a small object, or the tray floor under a seated one. Both are reported
    because the *gap* between them is what makes "this box contains a solid object
    standing on a surface" checkable instead of assumed."""
    x0, y0, x1, y1 = _clip_bbox(bbox, depth.shape)
    patch = depth[y0:y1 + 1, x0:x1 + 1]
    return {"pixels": int(patch.size),
            "near_window": float(patch.min()), "far_window": float(patch.max()),
            "median_window": float(np.median(patch))}


def support_surface_z(camera: CameraSpec, depth: np.ndarray, bbox,
                      *, stand_off_px: int = 3,
                      z_bounds: Optional[tuple[float, float]] = None
                      ) -> tuple[Optional[float], dict]:
    """Height of the surface just outside the box's contact line.

    Sampled left and right of the object at its own bottom row: that is the floor
    the object is sitting *on*, measured, not the nominal table height copied from
    the scene file. The **lower** sample wins, because a floor is below whatever
    stands beside it — the mean would let a neighbouring body's flank lift the
    estimate and invent a floating object. When the two samples disagree by more
    than `AMBIGUOUS_SUPPORT_M` the scene is cluttered at this object's feet, and
    that is recorded as a fact instead of being averaged away.

    `z_bounds` — the heights this cell declares, table top and tray floors with a
    tolerance — gates which samples are *candidates* at all, and the gate is applied
    before the minimum is taken rather than after. That ordering was forced by a
    measurement: in the `overhead` view the green body stands 5 cm from the table's
    edge, its left sample misses the table entirely and lands 48 cm below the top,
    and "lowest" then chose a surface nothing in this cell can stand on. Because
    both the position solve and the silhouette fit are placed *on the support*, that
    one bad sample destroyed both: the body reported no position, and every candidate
    shape was projected 48 cm too far away and scored ~0.3 IoU against each other.
    A sample outside the declared heights is recorded as discarded, never averaged
    in, and a body whose only samples are out of bounds stays `unknown` — the same
    refusal as before, reached only after the plausible evidence was used.

    `None` when both samples are background: the percept then says it could not
    find the support, which is an answer (§5.1 不确定信息标记)."""
    x0, y0, x1, y1 = _clip_bbox(bbox, depth.shape)
    # one pixel above the box's last row: the bottom edge of a rendered silhouette is
    # an anti-aliased blend of object and floor, and its depth belongs to neither
    row = max(y0, y1 - 1)
    samples: list[dict] = []
    discarded: list[dict] = []
    for side, col in (("left", x0 - stand_off_px), ("right", x1 + stand_off_px)):
        base = {"side": side, "col": col}
        if not 0 <= col < depth.shape[1]:
            discarded.append({**base, "why": "outside the frame"})
            continue
        w = float(depth[row, col])
        if w >= 0.9999:
            discarded.append({**base, "why": "background"})
            continue
        p = camera.unproject(float(col), float(row), w)
        s = {**base, "z_m": round(p.z, 4),
             "range_m": round(depth_to_meters(w, camera.near_m, camera.far_m), 4)}
        if z_bounds is not None and not float(z_bounds[0]) <= p.z <= float(z_bounds[1]):
            s["why"] = (f"a surface at z={s['z_m']} m is outside every height this cell "
                        f"declares [{round(float(z_bounds[0]), 3)}, "
                        f"{round(float(z_bounds[1]), 3)}] m, so nothing stands on it")
            discarded.append(s)
        else:
            samples.append(s)
    if not samples:
        off_table = any("z_m" in d for d in discarded)
        return None, {"samples": [], "discarded": discarded,
                      "why": ("no sample sits at a height this cell declares: the only "
                              "surfaces beside this box are background, off the table, "
                              "or both" if off_table
                              else "both side samples are background")}
    zs = [float(s["z_m"]) for s in samples]
    z = float(min(zs))
    spread = float(max(zs) - min(zs)) if len(zs) > 1 else 0.0
    return z, {"samples": samples, "discarded": discarded,
               "z_m": round(z, 4), "chosen": "lowest",
               "sample_spread_m": round(spread, 4),
               "ambiguous": bool(spread > AMBIGUOUS_SUPPORT_M)}


def estimate_centre(camera: CameraSpec, depth: np.ndarray, bbox, *,
                    half_height_m: float, support_z: Optional[float] = None,
                    stand_off_px: int = 3,
                    z_bounds: Optional[tuple[float, float]] = None,
                    pixel: Optional[tuple[float, float]] = None,
                    ) -> tuple[Optional[Vec3], dict]:
    """Body centre, from the box the model reported and the surface it stands on.

    The ray through the box's centre pixel is intersected with the horizontal plane
    one declared half-height above the *measured* support surface. That ordering is
    the whole point: a camera cannot see a body's centre, so the estimator takes the
    two things it can see — which pixel the object covers, and how high the surface
    under it is — and solves for the centre instead of pretending to measure it.

    Returns `None` with the reason when the support surface is not measurable or
    falls outside the scene's declared heights: an unlocatable detection is a real
    outcome, and `unknown` is what the contract has for it (§5.1 不确定信息标记)."""
    x0, y0, x1, y1 = _clip_bbox(bbox, depth.shape)
    px, py = pixel if pixel is not None else ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    ev: dict = {"method": "ray_through_box_centre_at_support_z",
                "pixel": [round(float(px), 2), round(float(py), 2)],
                "bbox": [x0, y0, x1, y1], "half_height_m": round(float(half_height_m), 4)}
    if support_z is None:
        support_z, sev = support_surface_z(camera, depth, bbox, stand_off_px=stand_off_px,
                                           z_bounds=z_bounds)
        ev["support_surface"] = sev
        # the ambiguity travels with the answer: a centre solved beside a neighbouring
        # body is still the best available estimate, and the percept must say so
        # rather than quietly report a coordinate that looks as certain as any other
        ev["ambiguous_support"] = bool(sev.get("ambiguous", False))
        ev["support_sample_spread_m"] = sev.get("sample_spread_m")
    if support_z is not None and z_bounds is not None:
        lo, hi = float(z_bounds[0]), float(z_bounds[1])
        if not lo <= float(support_z) <= hi:
            ev["rejected"] = (f"measured support z {round(float(support_z), 4)} is outside the "
                              f"declared scene heights [{lo}, {hi}]")
            support_z = None
    if support_z is None:
        # the sub-reason, not a paraphrase of it: `_resolve_for` picks what to suggest
        # from which of the two happened (no floor there at all, or a floor this cell
        # cannot have), and a caller that lost that distinction cannot say either
        ev["why"] = ("support surface unmeasurable: "
                     + str((ev.get("support_surface") or {}).get("why")
                           or "the centre height is unknown"))
        return None, ev
    z_target = float(support_z) + float(half_height_m)
    eye = np.array([camera.eye.x, camera.eye.y, camera.eye.z])
    # two samples of the same ray, at different window depths, give its direction
    # without depending on either depth reading being right
    a = camera.unproject(float(px), float(py), 0.25)
    b = camera.unproject(float(px), float(py), 0.75)
    direction = np.array([b.x - a.x, b.y - a.y, b.z - a.z])
    direction = direction / float(np.linalg.norm(direction))
    dz = float(direction[2])
    if abs(dz) < 1e-9:
        ev["why"] = "the ray through this pixel is horizontal: it never meets the plane"
        return None, ev
    t = (z_target - float(eye[2])) / dz
    if t <= 0.0:
        ev["why"] = f"the plane at z={round(z_target, 4)} is behind the camera for this pixel"
        return None, ev
    centre = eye + t * direction
    ev["support_z_m"] = round(float(support_z), 4)
    ev["centre_z_m"] = round(float(centre[2]), 4)
    ev["range_m"] = round(float(t), 4)          # along the ray
    # The consistency check has to compare like with like: the box's depth numbers
    # are distances along the view axis, so the solved centre is re-projected and
    # read the same way instead of being compared against its ray parameter.
    solved_window = camera.project([float(v) for v in centre])[2]
    ev["centre_view_range_m"] = round(depth_to_meters(solved_window,
                                                     camera.near_m, camera.far_m), 4)
    near = bbox_depth_stats(depth, bbox)
    ev["box_near_m"] = round(depth_to_meters(near["near_window"], camera.near_m, camera.far_m), 4)
    ev["box_far_m"] = round(depth_to_meters(near["far_window"], camera.near_m, camera.far_m), 4)
    # Consistency: the solved centre must sit between the nearest and farthest
    # surface the box covers, otherwise the box and the support disagree and the
    # number is a fiction. This check is what turns a bad estimate into an
    # `unknown` instead of a confident wrong coordinate.
    if not (ev["box_near_m"] - 0.05 <= ev["centre_view_range_m"] <= ev["box_far_m"] + 0.05):
        ev["rejected"] = (f"solved centre at {ev['centre_view_range_m']} m is outside the box's own "
                          f"depth span [{ev['box_near_m']}, {ev['box_far_m']}]")
        return None, ev
    return Vec3(x=float(centre[0]), y=float(centre[1]), z=float(centre[2])), ev


def _ray_to_plane(camera: CameraSpec, pixel, z_target: float) -> Optional[np.ndarray]:
    """World point where the ray through `pixel` meets the horizontal plane at
    `z_target`, or None when that plane is behind the camera for this pixel."""
    eye = np.array([camera.eye.x, camera.eye.y, camera.eye.z])
    a = camera.unproject(float(pixel[0]), float(pixel[1]), 0.25)
    b = camera.unproject(float(pixel[0]), float(pixel[1]), 0.75)
    direction = np.array([b.x - a.x, b.y - a.y, b.z - a.z])
    n = float(np.linalg.norm(direction))
    if n == 0.0 or abs(float(direction[2]) / n) < 1e-9:
        return None
    direction /= n
    t = (float(z_target) - float(eye[2])) / float(direction[2])
    if t <= 0.0:
        return None
    return eye + t * direction


def best_fitting_shape(camera: CameraSpec, depth: np.ndarray, bbox, *,
                       shapes: dict[str, list[float]], support_z: float,
                       top_k: int = 3) -> dict:
    """Which declared shape could cast this silhouette from this camera?

    Only the parts of the picture that are not secret are used: the catalogue's own
    dimensions, the camera, and the height of the surface the blob stands on. Each
    candidate is placed at the observed support on the ray through the blob's centre
    pixel, and scored by how well its projected rectangle covers the observed one.

    This is *not* a classifier and does not pretend to be one: `cube` and `cuboid`
    differ by 12 mm of width, so at 0.9 m their silhouettes overlap heavily. What the
    caller gets is the ranking with its margin, and a margin small enough to be a coin
    flip is reported as one (`"ambiguous": true`) so the percept can say so instead of
    guessing silently.
    """
    x0, y0, x1, y1 = _clip_bbox(bbox, depth.shape)
    pixel = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    scores: dict[str, dict] = {}
    for word, dims in shapes.items():
        if word == "cylinder" and len(dims) != 2:
            scores[word] = {"why": "cylinder needs [radius, half_h]"}
            continue
        if word != "cylinder" and len(dims) != 3:
            scores[word] = {"why": "a box needs three half-extents"}
            continue
        if word == "cylinder":
            hx = hy = float(dims[0])
            hz = float(dims[1])
        else:
            hx, hy, hz = float(dims[0]), float(dims[1]), float(dims[2])
        centre = _ray_to_plane(camera, pixel, float(support_z) + hz)
        if centre is None:
            scores[word] = {"iou": 0.0, "why": "no point on this ray sits at that height"}
            continue
        # `centre` is the body's middle, one half-height above the measured support, so
        # the corners straddle it: `- [0,0,hz]` here would sink every candidate by half
        # its own height, and the fit would then always pick the smallest declared shape
        # because that is the one whose low-slung box still touches the silhouette.
        corners = np.array([[cx * hx, cy * hy, cz * hz]
                            for cx in (-1, 1) for cy in (-1, 1) for cz in (-1, 1)])
        pts = corners + centre
        try:
            proj = np.array([camera.project(pt) for pt in pts])
        except ValueError as e:
            scores[word] = {"iou": 0.0, "why": f"candidate does not project: {e}"}
            continue
        px0, px1 = float(proj[:, 0].min()), float(proj[:, 0].max())
        py0, py1 = float(proj[:, 1].min()), float(proj[:, 1].max())
        inter = max(0.0, min(x1, px1) - max(x0, px0) + 1.0) * \
            max(0.0, min(y1, py1) - max(y0, py0) + 1.0)
        observed_area = float((x1 - x0 + 1) * (y1 - y0 + 1))
        projected_area = (px1 - px0 + 1.0) * (py1 - py0 + 1.0)
        union = observed_area + projected_area - inter
        scores[word] = {"iou": round(float(inter / union), 4) if union > 0 else 0.0,
                        "projected_px": [round(px1 - px0 + 1.0, 1), round(py1 - py0 + 1.0, 1)],
                        "projected_bbox_px": [round(px0, 1), round(py0, 1), round(px1, 1),
                                              round(py1, 1)],
                        "observed_px": [x1 - x0 + 1, y1 - y0 + 1]}
    ranked = sorted((w for w in scores if "iou" in scores[w]), key=lambda w: -scores[w]["iou"])
    out = {"method": "silhouette_fit_against_declared_shapes", "scores": scores,
           "ranked": ranked[:top_k], "observed_bbox": [x0, y0, x1, y1],
           # the candidate stands upright with no yaw: with nothing in a single silhouette
           # that reliably fixes a rotation (and no measured quaternion, §5.1), the fit is
           # stated against the one pose a plan will assume anyway
           "assumes": "upright, zero yaw",
           "support_z_m": round(float(support_z), 4)}
    if len(ranked) >= 2:
        margin = scores[ranked[0]]["iou"] - scores[ranked[1]]["iou"]
        out["margin"] = round(float(margin), 4)
        out["ambiguous"] = bool(margin < 0.05)
    elif ranked:
        out["margin"] = None
        out["ambiguous"] = False
    else:
        out["margin"] = None
        out["ambiguous"] = True
        out["why"] = "no declared shape could be scored against this box"
    return out
