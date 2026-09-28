"""RGB / RGB-D frame capture for the perception channel (SPEC-v0.2 §5.1, §13-P1).

One rule decides every design choice here: **what the agent may use is what a camera
could tell it.** The frame is therefore written to the run directory together with
the exact `CameraSpec` that produced it, and the only numbers taken from the
simulator are the rendered colour image and the rendered depth image. Object
identities, poses and contacts are *not* read here — the simulator's own
segmentation index map and `get body pose` calls are privileged channels, used in
this phase only by the evaluation-side contrast (P1-f) and never by a percept.

The depth map is useless without the matrices that made it, so a `SensorFrame`
carries them and refuses to describe a frame whose depth was rendered by a
different camera. That is the asymmetry the v0.1 renderer left behind: the old
`render()` returned a tuple whose depth nobody unpacked and no matrix described.
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

import numpy as np
from pydantic import Field

from ..core.contracts import StrictModel, Vec3
from .camera import CameraSpec, column_major, depth_to_meters


class SensorFrame(StrictModel):
    """One captured frame and everything needed to re-derive its geometry."""

    frame_id: str = ""
    camera_id: str = "main"
    camera: CameraSpec
    modality: str = "rgbd"          # "rgb" when no depth was kept
    sim_time: float = 0.0
    wall_time: float = Field(default_factory=time.time)
    image_ref: str = ""
    image_sha256: str = ""
    image_bytes: int = 0
    depth_ref: Optional[str] = None
    depth_sha256: Optional[str] = None
    depth_shape: Optional[list[int]] = None
    depth_range_m: Optional[list[float]] = None
    renderer: str = "ER_TINY_RENDERER"
    # What a flat-shaded software renderer cannot deliver, said out loud rather than
    # discovered later by a reader who trusted a `rgbd` label.
    limitations: list[str] = Field(default_factory=list)

    def to_payload(self) -> dict:
        """The dict that is safe to log: refs and digests, never pixels."""
        d = self.model_dump(mode="json")
        return d


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ------------------------------------------------------------------ views -----

# Declared as data, in one place, with the reason each one exists. Chosen by
# `work/p1_sensor_check.py`, which measures how many pixels of each body actually
# land in each view instead of assuming a plausible-sounding pose.
def view_spec(camera_id: str, eye, target, *, fov_deg: float = 40.0,
              width: int = 640, height: int = 480, near_m: float = 0.05,
              far_m: float = 3.0, up=(0.0, 0.0, 1.0)) -> CameraSpec:
    return CameraSpec(camera_id=camera_id, eye=Vec3(x=float(eye[0]), y=float(eye[1]), z=float(eye[2])),
                      target=Vec3(x=float(target[0]), y=float(target[1]), z=float(target[2])),
                      up=Vec3(x=float(up[0]), y=float(up[1]), z=float(up[2])),
                      fov_deg=fov_deg, width=width, height=height, near_m=near_m, far_m=far_m)


# The poses the agent may look through, frozen here so a run cannot quietly re-aim
# the camera. Each was selected by `work/p1_sensor_check.py` on one measured number:
# the error of `estimate_centre` when the *box is perfect* (privileged corner
# projection), i.e. the ceiling on what any percept's metric geometry can be worth.
# All rows are the same scene — `dev_c5`, 5 objects, `seed 105` — and every number is
# over the 5 bodies. From `/tmp/v02_p1_sensor/p1_sensor_check.json`.
#
# These are re-measured numbers. The first version of this table was taken while
# `CameraSpec.fx` was wrong by the aspect ratio (1.333x on every projected x), which
# inflated the errors *and* changed which views looked usable — it claimed `overhead`
# refused 4 of 5 bodies, where the corrected model refuses none. Nothing but
# re-running the measurement caught that, which is why the table carries its source
# file and every claim below is a number from it rather than a reasonableness judge.
#
#   view          n  refused  amb  med_xy  max_xy  med_z  width_px  verdict
#   high_angle    5     0      3     1.1     1.7    0.8    57-65    chosen as `main`
#   overhead      5     0      0     1.9     3.2    0.0    60-90    xy is fine; z is not
#                                                                   measured at all
#   front_high    5     0      1     4.4    74.5    0.9    45-60    one body's flank
#                                                                   filled both samples
#   close_right   5     0      4     6.9    65.2    0.8    66-88    biggest blobs, most
#                                                                   clutter at the feet
#   legacy 640    5     0      2     4.9    19.4    2.1    24-28    the v0.1 pose
#   front_low     5     0      1   157.9   189.5   22.1    46-61    grazing angle
#
# `high_angle` wins: lowest median *and* lowest worst-case error, and it is the only
# view whose z error is a measurement rather than an assumption. `overhead`'s 0.0 mm z
# error is the reason it is not `main`, not a virtue — looking straight down, every
# support sample lands on the table at the same height, so the centre's z is whatever
# the declared half-height says it is and the picture contributes nothing. Its
# metric xy is otherwise excellent, and `work/p1_perceive_check.py` adds the rest of
# the case against it as a primary: from pixels its shape fit is the worst of the three
# kept views (3/5 against `main`'s 4/5 and `front_high`'s 5/5), and it is the view that
# puts a support sample off the table edge for a body standing 5 cm from it.
# `close_right` and `front_low` were rejected and are not declared: four of five bodies
# have a neighbour at their feet in the former, and 158 mm of median error is not a
# sensor reading. `legacy` is kept out of `VIEWS` too — v0.1's 640x480 pose measures
# bodies at 24-28 px, a fifth of the pixel budget, and its worst case is 11x `main`'s.
VIEWS: dict[str, dict] = {
    "main":       dict(eye=(1.06, -0.30, 1.24), target=(0.54, 0.00, 0.62), fov_deg=40.0,
                       width=800, height=600),
    "overhead":   dict(eye=(0.58, 0.00, 1.30), target=(0.58, 0.00, 0.62), fov_deg=45.0,
                       width=800, height=600, up=(0.0, 1.0, 0.0)),
    "front_high": dict(eye=(1.18, 0.00, 0.95), target=(0.54, 0.00, 0.62), fov_deg=40.0,
                       width=800, height=600),
}


def camera_for(view_id: str = "main", *, near_m: float = 0.05, far_m: float = 3.0) -> CameraSpec:
    """The declared `CameraSpec` behind one of the named views."""
    if view_id not in VIEWS:
        raise ValueError(f"undeclared view {view_id!r}; declared: {sorted(VIEWS)}")
    spec = VIEWS[view_id]
    return view_spec(view_id, spec["eye"], spec["target"], fov_deg=spec["fov_deg"],
                     width=spec["width"], height=spec["height"], near_m=near_m, far_m=far_m,
                     **({} if "up" not in spec else {"up": spec["up"]}))


# ---------------------------------------------------------------- capture -----


def render_rgb_depth(scene, camera: CameraSpec) -> tuple[np.ndarray, np.ndarray]:
    """(H,W,3) uint8 RGB and (H,W) float window depth from the physics scene.

    `scene` is anything exposing `cid` — this is the only line in the perception
    package that touches the simulator, and it asks for images, not state."""
    import pybullet as p

    w, h = camera.width, camera.height
    img = p.getCameraImage(
        w, h,
        viewMatrix=column_major(camera.view_matrix),
        projectionMatrix=column_major(camera.projection_matrix),
        renderer=p.ER_TINY_RENDERER, physicsClientId=scene.cid)
    rgba = np.reshape(img[2], (h, w, 4))
    depth = np.reshape(np.asarray(img[3], dtype=np.float64), (h, w))
    return rgba[:, :, :3].copy(), depth


def save_frame(out_dir: str, camera: CameraSpec, rgb: np.ndarray, depth: np.ndarray,
               name: str, *, sim_time: float = 0.0, keep_depth: bool = True,
               frame_id: str = "") -> SensorFrame:
    """Write `name.png` (and `name.depth.npy`) and describe what was written."""
    os.makedirs(out_dir, exist_ok=True)
    from PIL import Image

    img_path = os.path.join(out_dir, f"{name}.png")
    Image.fromarray(rgb.astype(np.uint8)).save(img_path)
    h, w = depth.shape
    if list(depth.shape) != [camera.height, camera.width]:
        raise ValueError(f"depth {depth.shape} does not match camera {camera.width}x{camera.height}")
    zmin = depth_to_meters(float(depth.min()), camera.near_m, camera.far_m)
    zmax = depth_to_meters(float(depth.max()), camera.near_m, camera.far_m)
    limitations = [
        "flat shading: the tiny software renderer adds no specular or shadow cues, "
        "so shape must be read from silhouette and context, not from shading",
    ]
    depth_ref = None
    depth_sha = None
    if keep_depth:
        depth_ref = os.path.join(out_dir, f"{name}.depth.npy")
        np.save(depth_ref, depth.astype(np.float32))
        depth_sha = _sha(depth_ref)
    else:
        limitations.append("no depth channel kept: metric geometry is unavailable to this frame")
    return SensorFrame(
        frame_id=frame_id or name, camera_id=camera.camera_id, camera=camera,
        modality="rgbd" if keep_depth else "rgb", sim_time=sim_time,
        image_ref=img_path, image_sha256=_sha(img_path),
        image_bytes=os.path.getsize(img_path),
        depth_ref=depth_ref, depth_sha256=depth_sha,
        depth_shape=[int(h), int(w)] if keep_depth else None,
        depth_range_m=[round(zmin, 4), round(zmax, 4)] if keep_depth else None,
        limitations=limitations)


def capture(scene, out_dir: str, camera: CameraSpec, name: str, *, sim_time: float = 0.0,
            keep_depth: bool = True, frame_id: str = "") -> tuple[SensorFrame, np.ndarray, np.ndarray]:
    rgb, depth = render_rgb_depth(scene, camera)
    frame = save_frame(out_dir, camera, rgb, depth, name, sim_time=sim_time,
                       keep_depth=keep_depth, frame_id=frame_id)
    return frame, rgb, depth


def load_depth(frame: SensorFrame) -> np.ndarray:
    """The depth channel of a logged frame, from the ref the frame names.

    A `SensorFrame` carries its own `CameraSpec`, so a run directory is
    self-sufficient: its geometry can be re-derived from the files, with no
    simulator connected and nothing privileged to read."""
    if not frame.depth_ref:
        raise ValueError(f"frame {frame.frame_id} kept no depth channel")
    return np.load(frame.depth_ref)


def load_rgb(frame: SensorFrame) -> np.ndarray:
    """The colour channel of a logged frame, checked against its own digest.

    The digest check is the point of this function rather than an `Image.open` call:
    a reader that segments bytes different from the ones the frame describes produces
    a percept whose evidence trail is a fiction, and this is the cheap place to
    notice. It also means a reader of a run directory needs no simulator to see what
    the agent saw."""
    from PIL import Image

    if not frame.image_ref:
        raise ValueError(f"frame {frame.frame_id} names no image")
    if frame.image_sha256 and _sha(frame.image_ref) != frame.image_sha256:
        raise ValueError(f"frame {frame.frame_id}: {frame.image_ref} does not match the "
                         f"recorded image digest")
    rgb = np.asarray(Image.open(frame.image_ref).convert("RGB"), dtype=np.uint8)
    if list(rgb.shape[:2]) != [frame.camera.height, frame.camera.width]:
        raise ValueError(f"frame {frame.frame_id}: image {rgb.shape} does not match camera "
                         f"{frame.camera.width}x{frame.camera.height}")
    return rgb
