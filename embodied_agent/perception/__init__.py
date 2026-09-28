"""Perception package (SPEC-v0.2 §5.1): from rendered pixels to a semantic percept.

Three layers, one direction of dependency:

* `camera` — the pinhole model, and the only place window depth becomes metres;
* `frames` — what a camera produced: an RGB image, a depth image, and the exact
  `CameraSpec` that made them (`VIEWS` declares the poses a run may use);
* `geometry` / `segment` / `observe` — from a box the *model* reported to a metric
  point with a name, a confidence and an `unknown` when the picture does not support
  one.

Nothing in this package reads simulator state. That is not a convention: it is
asserted against the source text by
`tests/contract/test_v02_perception_sensor.py::test_the_perception_package_reads_no_privileged_state`,
and the one call that touches the renderer asks for images and nothing else.
"""
from .camera import CameraSpec, depth_to_meters, look_at, meters_to_depth, perspective
from .catalog import PerceptionCatalog, RegionDecl
from .frames import (VIEWS, SensorFrame, camera_for, capture, load_depth, load_rgb,
                     render_rgb_depth, save_frame, view_spec)
from .geometry import (AMBIGUOUS_SUPPORT_M, best_fitting_shape, bbox_depth_stats,
                       estimate_centre, support_surface_z)
from .observe import (PROMPT_VERSION, PerceptionAssembler, ReadAbsent, ReadObject,
                      ReadRegion, StubReader, TaskContext, VLMReader, VlmReading,
                      percept_id, prompt_sha256, user_prompt)
from .segment import colour_bboxes, colour_mask, mask_summary

__all__ = [
    "AMBIGUOUS_SUPPORT_M", "CameraSpec", "PerceptionAssembler", "PerceptionCatalog",
    "PROMPT_VERSION", "ReadAbsent", "ReadObject", "ReadRegion", "RegionDecl",
    "SensorFrame", "StubReader", "TaskContext", "VIEWS", "VLMReader", "VlmReading",
    "best_fitting_shape", "bbox_depth_stats", "camera_for", "capture", "colour_bboxes",
    "colour_mask", "depth_to_meters", "estimate_centre", "load_depth", "load_rgb",
    "look_at", "mask_summary", "meters_to_depth", "percept_id", "perspective",
    "prompt_sha256", "render_rgb_depth", "save_frame", "support_surface_z",
    "user_prompt", "view_spec",
]
