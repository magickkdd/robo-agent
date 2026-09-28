"""Pinhole camera model for the RGB-D perception channel (SPEC-v0.2 §5.1).

Why this module exists instead of a call to `pybullet.computeViewMatrix`: the
percept must be *re-derivable* from what was recorded. A run directory that holds a
PNG and a depth map but not the exact extrinsics that produced them cannot be
inspected later — not by a test, not by a reader, not by the contrast harness in
P1-f. So the camera is declared here as data (`CameraSpec`), the two matrices are
computed from that data by this file, and `work/p1_sensor_check.py` proves the
matrices agree with the simulator's own and that a pixel plus its depth lands back
on the body it came from.

The one convention that has to be right is the depth window: `getCameraImage`
returns OpenGL window-space depth in [0, 1] (0 at `near_m`, 1 at `far_m`), not
metres. `depth_to_meters` is the only place that converts it, and `unproject` is the
only way geometry enters a percept.
"""
from __future__ import annotations

import math

import numpy as np

from ..core.contracts import StrictModel, Vec3


class CameraSpec(StrictModel):
    """One fixed camera: where it is, what it looks at, and how it sees.

    `eye/target/up/fov/aspect/near/far` are the whole specification — enough for a
    later reader to reproduce `project`/`unproject` without the simulator running."""

    camera_id: str = "main"
    eye: Vec3
    target: Vec3
    up: Vec3 = Vec3(x=0.0, y=0.0, z=1.0)
    fov_deg: float = 40.0
    width: int = 640
    height: int = 480
    near_m: float = 0.05
    far_m: float = 3.0

    @property
    def aspect(self) -> float:
        return self.width / self.height

    @property
    def view_matrix(self) -> np.ndarray:
        """Camera-from-world, row-major 4x4 (OpenGL: the camera looks down -z)."""
        return look_at(self.eye.as_list(), self.target.as_list(), self.up.as_list())

    @property
    def projection_matrix(self) -> np.ndarray:
        return perspective(self.fov_deg, self.aspect, self.near_m, self.far_m)

    # ---------- intrinsics, derived ----------
    @property
    def focal_px(self) -> float:
        """Focal length in pixels. `fov_deg` is the *vertical* field of view, so this
        is measured against `height`."""
        return self.height / (2.0 * math.tan(math.radians(self.fov_deg) / 2.0))

    @property
    def fx(self) -> float:
        """`focal_px`, not `focal_px * aspect`: the projection matrix already divides
        x by the aspect (`m[0,0] = f/aspect`), and the NDC-to-pixel step multiplies by
        `width/2 = aspect * height/2`, which cancels it. Applying the aspect a second
        time here stretches every projected x by `aspect` about the principal point —
        vertically exact, horizontally wrong, and invisible in a picture. The renderer
        disagrees with it pixel by pixel, which is what
        `tests/contract/test_v02_perception_sensor.py::
        test_a_projected_point_lands_where_the_rendered_image_shows_it` measures."""
        return self.focal_px

    @property
    def fy(self) -> float:
        return self.focal_px

    @property
    def cx(self) -> float:
        return self.width / 2.0

    @property
    def cy(self) -> float:
        return self.height / 2.0

    def intrinsics(self) -> dict:
        return {"model": "pinhole", "width": self.width, "height": self.height,
                "fx": round(self.fx, 4), "fy": round(self.fy, 4),
                "cx": self.cx, "cy": self.cy,
                "fov_deg": self.fov_deg, "aspect": round(self.aspect, 6),
                "near_m": self.near_m, "far_m": self.far_m}

    def extrinsics(self) -> dict:
        """The two matrices as flat column-major lists, the order `getCameraImage`
        wants, so the recorded camera is literally the camera that was used."""
        return {"eye": self.eye.as_list(), "target": self.target.as_list(),
                "up": self.up.as_list(),
                "view_matrix_column_major": [round(float(v), 9) for v in
                                             self.view_matrix.T.reshape(-1)],
                "projection_matrix_column_major": [round(float(v), 9) for v in
                                                   self.projection_matrix.T.reshape(-1)]}

    # ---------- the two directions of the model ----------
    def project(self, world_xyz) -> tuple[float, float, float]:
        """World point -> (pixel x, pixel y, window depth in [0,1])."""
        eye = self.view_matrix @ np.append(np.asarray(world_xyz, dtype=float), 1.0)
        z = -float(eye[2])
        if z <= 0.0:
            raise ValueError(f"point {list(world_xyz)} is behind camera {self.camera_id}")
        px = self.fx * float(eye[0]) / z + self.cx
        py = -self.fy * float(eye[1]) / z + self.cy
        return px, py, meters_to_depth(z, self.near_m, self.far_m)

    def project_meters(self, world_xyz) -> tuple[float, float, float]:
        px, py, d = self.project(world_xyz)
        return px, py, depth_to_meters(d, self.near_m, self.far_m)

    def unproject(self, px: float, py: float, depth_window: float) -> Vec3:
        """Pixel + window depth -> world point. The percept's only source of metric
        geometry, and the reason a depth map has to travel with its own camera."""
        z = depth_to_meters(depth_window, self.near_m, self.far_m)
        x = (px - self.cx) * z / self.fx
        y = -(py - self.cy) * z / self.fy
        eye = np.array([x, y, -z, 1.0])
        world = np.linalg.inv(self.view_matrix) @ eye
        return Vec3(x=float(world[0]), y=float(world[1]), z=float(world[2]))

    def unproject_meters(self, px: float, py: float, depth_m: float) -> Vec3:
        return self.unproject(px, py, meters_to_depth(depth_m, self.near_m, self.far_m))


# ---------- matrix builders (pure numpy, no simulator connection) ----------


def look_at(eye, target, up) -> np.ndarray:
    """Right-handed view matrix in the convention `computeViewMatrix` uses: rows
    are the camera axes expressed in world coordinates, the camera looks down its
    own -z, and +x is `cross(forward, up)` — not the other way round.

    The sign of that cross product is the whole difference between a working
    camera model and one that mirrors every pixel about the image centre, and it is
    not guessable: `work/p1_sensor_check.py` compares this matrix against the
    simulator's own element by element, and compares a projected body centre
    against where the depth image actually shows that body."""
    eye = np.asarray(eye, dtype=float)
    target = np.asarray(target, dtype=float)
    up = np.asarray(up, dtype=float)
    forward = target - eye
    n = np.linalg.norm(forward)
    if n == 0.0:
        raise ValueError("eye and target coincide: no view direction")
    forward /= n
    right = np.cross(forward, up)
    rn = np.linalg.norm(right)
    if rn < 1e-9:
        raise ValueError(f"up {list(up)} is parallel to the view direction of "
                         f"{list(forward)}: choose an up vector that defines a roll")
    right /= rn
    newup = np.cross(-forward, right)
    rot = np.vstack([right, newup, -forward])
    m = np.eye(4)
    m[:3, :3] = rot
    m[:3, 3] = -rot @ eye
    return m


def perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    """Standard OpenGL frustum, matching `computeProjectionMatrixFOV`: fov is the
    *vertical* field of view, and depth is mapped non-linearly into [0, 1]."""
    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2.0 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def depth_to_meters(depth_window: float, near: float, far: float) -> float:
    """Window depth in [0,1] -> distance from the camera plane along -z."""
    d = float(depth_window)
    if d <= 0.0:
        return float(near)
    if d >= 1.0:
        return float(far)
    z_ndc = 2.0 * d - 1.0
    return float((2.0 * near * far) / ((far + near) - z_ndc * (far - near)))


def meters_to_depth(z_m: float, near: float, far: float) -> float:
    """The exact inverse of `depth_to_meters`, kept next to its partner so the round
    trip is checkable rather than assumed.

    NDC space runs from -1 to 1 and the window depth the renderer returns runs from
    0 to 1, so the mapping needs the same `(z_ndc + 1)/2` shift in both directions.
    Dropping it is invisible in a picture and fatal in a coordinate: this function
    is only ever used to *describe* a depth, never to read one back."""
    z = max(float(near), float(z_m))
    z_ndc = (z * (far + near) - 2.0 * near * far) / (z * (far - near))
    return (z_ndc + 1.0) / 2.0


def column_major(m: np.ndarray) -> list[float]:
    """What `getCameraImage(viewMatrix=..., projectionMatrix=...)` expects."""
    return [float(v) for v in m.T.reshape(-1)]
