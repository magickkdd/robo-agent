"""The camera channel of the MuJoCo bench: pixels in, a world state the loop can act on.

SPEC-v0.2 §5.1 asks the sensing arm to build its world from a frame, and §10 forbids the
adapter from handing it anything else. Everything in the rest of this package reads
`backend.measured()` — real metres, off the simulator, no model in the loop — which is the
*control* arm. This file is the other arm: one RGB image plus one depth image per look,
plus the scene file's own static geometry, and a vision model that is asked where things
are in pixels.

Why the depth has to be converted before it is used
---------------------------------------------------
`perception/geometry.py` reads a depth array whose values are OpenGL **window** depth in
[0,1] (`camera.meters_to_depth` is the description of that axis, and `w >= 0.9999` is its
word for "no surface"). MuJoCo's `Renderer.enable_depth_rendering()` returns **metres along
the view axis**, and its far plane is not this cell's `far_m`. So `MuJoCoRig.render` is the
one place the two conventions meet, and three things it does are load-bearing:

* metres are clipped into the declared `[near_m, far_m]` before conversion — an
  un-clipped `meters_to_depth(112.897, 0.05, 3.0)` returns a number above 1 and the
  background test silently passes on a *wall*;
* **NaN becomes background.** Measured in this session: MuJoCo's depth buffer carries NaN
  pixels. Every comparison in `geometry.py` is a `<`/`>=` against a float, and NaN fails all
  of them *without* raising — `patch.min()` over a box containing one NaN is NaN, the
  consistency test at `geometry.py:210` then rejects an estimate that was perfectly good.
  A NaN is "no surface was hit", which is exactly what `1.0` already means here;
* `0.0` (the near plane, hit by geometry the camera is inside) is kept, because
  `depth_to_meters(0.0)` already returns `near_m` and a clipped reading is a fact about a
  camera that is too close, not a missing one.

What the target is, and why one request buys the whole episode
--------------------------------------------------------------
The socket on this bench is a hole through the *side* of a fixed box — a 60 mm x 60 mm
aperture left between four wall blocks, not a circle, and not a tray — so the declared
`RegionDecl` (xy-only, `kind="tray"` downstream) can only express it as a point plus a
half-size. What it can express is enough, and the reason is a measurement taken from the box's
own model arrays:

    /tmp/mw_wall_frames.py, layouts L0-L4 of `peg-insert-side-v3`:
      the box body has **no joints** and the same modelled quaternion everywhere
        ([0.707388, 0, 0, 0.706825]: a 90 deg turn about z), so a local offset can be
        rotated into world axes once and stays true for every episode;
      its geoms live on an **unnamed child body**, which is why a declaration that matches
        `geom_bodyid == box_bid` measures nothing;
      visual AABB (groups 0-1) centre [0, 0, 0.0995], half [0.1, 0.1, 0.1005] in the box's
        frame; collision hull (group 4) centre [0, 0, 0.1], half [0.1, 0.1, 0.1];
      the `hole` site is at [0, -0.096, 0.13] there, identically on all five layouts.

So the hole's position relative to the box is *the scene file*, not the episode, and
`wall_declaration()` reads it from `model.geom_pos/geom_quat/geom_aabb/body_quat/site_pos` of
the box's own subtree — model arrays, never `data`. What changes between episodes is where the
**box** is. So the channel asks one question once per episode: *where in this picture is the
box with a hole in its side?* — and the answer's pixels are then handed to `fit_aperture`, which
finds the hole in the depth image as the one place the box's own face plane lets a ray through.
Every later round's region is that one measurement. The survey's box is used as an *address* for
that search, not as its result: sliding it 15 px moves the answer by about as much as not sliding
it, which is the measured difference between this and the outline fit it replaced.

The target point itself is chosen, not borrowed. The benchmark's `goal` site is attached to the
**world** body and MetaWorld rewrites its position per layout, so `model.site_pos[goal]` is
*this episode's answer in metres* — reading it to calibrate a camera channel would be §10's
forbidden injection wearing the word "static", and an earlier draft of `wall_declaration` did
exactly that. The channel instead declares the point where the hole's axis crosses the box's
central plane. That is a legitimate substitution because of what the verifier measures: the
**perpendicular** distance from the target point to the peg's own two ends, so any point on the
hole's axis answers the same question — and the depth chosen is *stricter* than the scorer's,
96 mm inside the mouth against the 66 mm the `goal` site happens to sit at (measured, by a
script that is not part of the channel, and reported here rather than used).

The survey is a **vision request**, not a percept, and that is a forced choice rather than a
tidiness preference. Three colour-based surveys and three geometric ones were tried first
(`/tmp/mw_hole_probe.py`, `/tmp/mw_hue_probe.py`, `/tmp/mw_colour_words.py`) and all of them
fail on the same two facts, measured:

* the box's rendered colour `rgb_mean=(0.610, 0.300, 0.203)` sits ~5° of hue from the work
  surface it stands on, and `colour_mask` cannot separate them at a usable tolerance; at
  `hue_tol=12°, min_sat=0.5` it recovers 894 of the box's 2159 pixels in `corner2` and 115
  (of 99 real) in `gripperPOV`, with `rows_split=56` — a fragmented outline, because the
  arm is standing in front of it;
* the *word* is the problem too: the robot is red `(0.501, 0.130, 0.130)` and the box is
  orange-brown, and a model asked to describe this picture calls both of them roughly the
  same thing, so the palette below gives the peg the one word with no competition — the peg
  is green at hue 120.0° exactly, and `green` selects 444 px in `corner2` and 9482 px in
  `gripperPOV` with 97-100% of them on the peg itself.

A percept cannot carry the survey's answer, because `world_state_from_percept` refuses a
catalog whose digest differs from the one the percept was assembled against — the guard that
killed the "two catalogs, one episode" design. So the survey runs *before* the catalog is
built, its evidence is returned to the runner and filed in the run manifest (none of the 15
frozen v0.2 record types is "a calibration act", and a `perception` record for it would be a
`wo_vlm` arm producing the record the vlm module owns), and the catalog it produces is the
one every later look is assembled against. One episode, one calibration, one digest.

What this channel still cannot see, and where that is written down
------------------------------------------------------------------
`held` arrives from the gripper (`grounding.py`'s argument, `executor.held_state()`) wherever
the gripper can answer, and this bench's cannot: its aperture ends were never measured, so
layer 1 reports `"unknown"` for every motion. `MujocoPerceiver.held_for_frame` is what answers
in its place, and it answers from *this channel's own pixels* — the rod's measured lowest end
against the two heights in the cell a rod could rest on — never from a simulator handle
(`MujocoSkillExecutor._held_now` is that instrument, and it belongs to the control arm). A rod
in mid-motion, leaning, or part-way into the hole is in neither band and stays `"unknown"`,
which is the same answer the channel gave before the instrument existed. The one place the
upright estimator used to fail outright — a *carried* peg has no support beside it, so "table
height plus a radius" is the answer for a body 0.2 m in the air — is what `measured_axis`
exists to replace, and both readings of the same frame stay in the snapshot
(`attributes["support_estimate_xyz"]` beside the measured midpoint). What remains genuinely
unseen is the heading of a body whose colour band is broken by the arm standing in front of
it, and the depth of the hole's bottom: both are answered `unknown` by
`MujocoPerceptVerifier` rather than estimated, and named in the phase log. See
`SnapshotGoals`.
"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Any, Callable, Optional, Sequence

import numpy as np

from ..core.contracts import (
    GeometrySpec,
    Pose,
    PredicateReport,
    PredicateVerdict,
    SkillStatus,
    Source,
    StrictModel,
    TextFact,
    Vec3,
    WorldState,
)
from ..core.runtime import CALLED_COUNTERS
from ..episodic.arm import EpisodicMixin
from ..episodic.retrieval import DEFAULT_LIMIT
from ..episodic.store import ExperienceStore
from ..perception.arm import (
    Perceiver,
    PerceptRuntime,
    PerceptionUnavailable,
    arm_coherence,
    arm_for_channel,
    channel_readiness,
)
from ..perception.camera import CameraSpec
from ..perception.catalog import PerceptionCatalog, RegionDecl
from ..perception.frames import save_frame
from ..perception.grounding import HELD_SOURCE, GroundingMap
from ..perception.observe import StubReader, TaskContext, VLMReader
from ..perception.segment import colour_bboxes, colour_mask
from ..perception.world_state import ASSUMED_UPRIGHT
from ..planning.arm import RECALL_BUDGET_CHARS, RENDER_BUDGET_CHARS
from ..planning.task_planner import TaskPlanner
from .calls_log import file_call
from .planned_runtime import (
    MW_ARM_MODULES,
    MujocoPlannedRuntime,
    V02MujocoDecisionContext,
    mw_arm_coherence,
)
from .runtime import MujocoRuntime, mw_budgets
from .state import MujocoVerifier, ends_of

# ---------------------------------------------------------------- calibration --
#: this bench's cameras, read out of the model file by `MuJoCoRig.views`. `corner2` is the
#: only one of the two that sees the whole cell; `gripperPOV` is a wrist camera whose frame
#: is 69% red robot, which is why it is a *second* view and not the default.
DEFAULT_VIEWS = ("corner2", "gripperPOV")
SURVEY_VIEW = "corner2"
NEAR_M, FAR_M = 0.05, 3.0
WIDTH = HEIGHT = 480

#: the work surface. `data.geom("floor")`-adjacent bodies put the peg's centre at z=0.02 and
#: its support at z=0.005; both numbers are in `tests/contract/test_mujoco_channel.py`.
TABLE_TOP_Z_M = 0.005
#: heights at which a surface *of this cell* can exist at all: the work plane, up to the top
#: of the box. Anything the support sampler finds outside this band is off the bench and is
#: discarded by `support_surface_z` rather than averaged in.
SUPPORT_Z_BOUNDS = (-0.015, 0.225)

#: the peg's declared extent. `geometry_for` reads a box's three dims as
#: `[half_x, half_y, half_z]` and its `half_h_vertical` — the one number the position
#: estimator adds to a measured support — is **`half_z`**. Measured on this bench, the peg
#: lies flat: its site sits at z≈0.02 on a surface at z=0.005. So the declared vertical
#: half-extent is the peg's *radius*, 0.015 m, and the long 0.12 m is spent along x.
#:
#: That is a deliberate inversion of the object's real proportions, and the reason is the
#: one hard lesson of P1-c: `estimate_centre` solves
#: `measured support z + declared upright half-height`, so a declaration that is honest
#: about an *upright* peg (`cylinder: [0.015, 0.12]`, or the box order
#: `[0.12, 0.015, 0.12]`) places this peg's centre 0.12 m in the air — six times its own
#: radius above where it is — and every grasp and every placement verdict that follows is
#: about a body that is not there. `cylinder` is therefore declared with
#: `[radius, half_h] = [0.015, 0.015]`: the same claim as the rod words, written the way a
#: model is most likely to say it.
#:
#: What the upright assumption cannot give this object — its heading, and so its two ends —
#: is measured instead, from the body's own pixels, by `measured_axis`.
PEG_WORD = "green"
PEG_DIMS_M = [0.12, 0.015, 0.015]
PEG_RADIUS_M = 0.015
PEG_SHAPE_WORDS = ("rod", "bar", "stick", "peg")
PEG_CYLINDER_DIMS_M = [PEG_RADIUS_M, PEG_RADIUS_M]
#: the box the hole is in. Same trick, no collision: its half-extents are *derived* from the
#: model file by `wall_declaration`, and the word is what the survey prompt describes.
WALL_WORD = "brown"
WALL_SHAPE_WORD = "block"
#: hue tolerances for the two colour bands this cell declares, re-derived on the rig that renders
#: today — sites off, the live `MjModel` — over all 35 layouts x both cameras by
#: `/tmp/mw_hue_recheck.py`, with the segmentation buffer of the same renderer used to say which
#: pixels really belong to which body (read by probes only, by no percept).
#:
#: The peg's pixels are green at hue 120.0° at their *median* in all 70 frames, and that is the
#: whole of what survives of the previous comment, which read "hue 120.0° exactly with nothing else
#: on the bench that hue". Both halves were wrong. The per-frame minimum hue over the peg's own
#: pixels reaches 77.8° in `corner2` and 74.4° in `gripperPOV` — the shaded and antialiased edge
#: pixels of the very body being looked for — and other bodies do land in the band: every false
#: positive in those 70 frames belongs to `tablelink` geom 1, 8-21 px per `corner2` frame and
#: 8-238 px per `gripperPOV` frame. What saves the fit is proportion — dozens of pixels against
#: thousands, so the principal axis of a few hundred green pixels is not pulled by a flange.
#:
#: The tolerance is that trade measured, at the worst layout of each cell. Recall over the peg's
#: own pixels: 12° -> 95.2% (`corner2`), 16° -> 98.6%, 22° -> 99.8%; 28° buys nothing further and
#: 34° costs precision (95.4% -> 93.6%). At 22° the band claims 413-531 px against 395-511 real in
#: `corner2` and 7,991-13,259 against 7,919-13,246 in `gripperPOV` — recall >= 98.1% and precision
#: >= 95.1% in every frame of both views.
#:
#: The saturation floor is not the inert number it appears to be. The peg's own pixels go as low as
#: saturation 0.30 (`gripperPOV`; 0.36 in `corner2`), so 0.32 keeps 100% recall there while a floor
#: of 0.45 collapses `gripperPOV` to 57.5%: on the wrist camera the rod is lit to near-white
#: (value p50 1.00) and desaturated, and a floor chosen against the survey camera's darker, more
#: saturated rendering (p50 0.65) would delete two fifths of the body from its own mask. Between
#: 0.15 and 0.32 the floor changes the peg's count by 4 px in 2,923, so the value below is a
#: measured plateau's upper edge, not a guess.
PEG_HUE_TOL_DEG = 22.0
PEG_MIN_SATURATION = 0.32
#: The box band is, by contrast, a documented refusal to calibrate. Its hue histogram is bimodal —
#: p05 0.0°, p50 25.4-25.8°, p95 26.8-26.9° across the 35 `corner2` frames — and
#: `hue_of(WALL_RGB)` is 14.3°, between the two modes, so no tolerance centred on it catches both:
#: at 12° the band claims 33.7-42.8% of the box's pixels at 90.5-94.4% precision, and at 16° it
#: buys 97.2% recall by also claiming the table (precision 11.5%, 89,352 px over six frames that
#: contain at most 3,034 box pixels). A 0.6 floor takes it back down to 2.7-33.1% recall.
#:
#: Two things follow and this cell acts on both. 12°/0.5 is kept because it is the only corner of
#: that sweep that is *precise* rather than merely large, and a wrong pixel is worse than a missing
#: one for the control that reads it. And in `gripperPOV` the same band falls below
#: `AXIS_MIN_PIXELS` in 20 of 35 layouts (0, 2, 4, 5, 6, 10, 11, 15, 17, 19, 20, 21, 23, 25, 26,
#: 28, 29, 31, 33, 34), where the box is dark (value p50 0.31) and its median hue sits 12° away
#: from the declared centre. So `WALL_*` is consulted only by the `wo_vlm` control's own wall
#: lookup and never by an axis fit: colour is what the peg reliably has here, and a hue band is not
#: what finds a box.
WALL_HUE_TOL_DEG = 12.0
WALL_MIN_SATURATION = 0.5
#: the smallest blob `measured_axis` will fit an axis to, bounded from both sides by the same run:
#: 3.4x below the smallest true sighting of the peg on this bench (413 px in `corner2`; 7,991 on the
#: wrist camera), and forty times above what the `hole` site marker contributes when it is drawn at
#: all (2 px at layout 0, 3 at layout 17, 0 in the other ten pairs measured).
#: `measured_axis` refused in 0 of 70 frames at this floor, and every one of those frames' fits
#: came from a blob of at least 413 px, so the floor has never yet discarded an answer.
#:
#: What it does *not* do is exclude the largest false blob in the table: a `tablelink` sighting of
#: 238 px would clear it. That is the honest limit of a size floor on a colour band — it refuses to
#: fit an axis to noise, and it relies on the peg's pixels outnumbering the bench's by ~20:1 in
#: `corner2` for the fit to be about the peg.
AXIS_MIN_PIXELS = 120
#: the declared length of the body an axis fit is about: `PEG_DIMS_M`'s long half-extent,
#: doubled. Not a measurement of where anything is — the same kind of model-file number as the
#: half-extents `build_catalog` has always published, and the only answer to "how long is the
#: thing that colour is supposed to be".
PEG_ROD_LENGTH_M = 2.0 * float(PEG_DIMS_M[0])
#: the shortest span `measured_axis` will report, at four fifths of the declared rod. Bounded
#: on both sides by measurement, never by a guess about what a rod "should" look like:
#:
#: * 42 looks of the real-camera episodes (`/tmp/mw_hold_lift2.py`, the fits over the frames as
#:   the model was shown them) span 0.2339-0.2407 m, in the hand and out of it;
#: * 6 looks rendered one-layout-per-process (`/tmp/mw_axis_one.py`, layouts 0/1/3 x both
#:   cameras, the frames a started cell really has) span 0.2342-0.2391 m.
#:
#: Nothing that healthy is refused: the floor sits 42 mm below the shortest real reading. What it
#: does refuse is a *dead depth image* — this renderer's second `MuJoCoRig` inside one process,
#: the failure `survey` already names `renderer_suspect` (13-19k surface pixels against 109k) —
#: whose rod fits came back between 0.0053 and 0.0849 m over two runs of the same three layouts
#: (`/tmp/mw_axis_health.py`; the dead fit is not even reproducible, which is the point), 2.3x to
#: 36x below the floor, because most of the band's pixels then carry a depth that belongs to
#: nothing. No other number in the row sees the difference; the pixel count does not (11,532 of
#: them on a dead wrist camera frame, more than the 9,508 of a live one).
AXIS_MIN_SPAN_M = round(0.8 * PEG_ROD_LENGTH_M, 4)
#: the band an axis *end* may lie in, which is a different question from the band a *surface*
#: may lie in and must not be answered by `SUPPORT_Z_BOUNDS`: a rod in the fingers is above every
#: surface it has ever rested on, and the first version of this gate made exactly that mistake
#: and refused all 20 carried looks of the 42, leaving the channel unable to confirm a grasp —
#: the one thing the hold instrument below exists for. The floor is the underside of the work
#: plane; the ceiling is the highest rest surface this cell declares (the box top, 0.200 m by
#: `hold_rest_surfaces`) plus one declared rod length — a hand cannot lift a rod higher above the
#: tallest thing it reaches over than the rod is long. Measured: the highest rod end in the 42
#: archived looks is 0.2403 m (`/tmp/mw_axis_zmax.py`), which sits inside with 200 mm to spare,
#: and the ends of a dead `corner2` frame sit at 1.068-1.072 m, outside it by 630 mm.
#:
#: The two gates answer for different frames, which is why there are two. A dead `corner2` band
#: is refused by this one — its ends sit 630 mm above the ceiling — and would have been refused
#: by span as well, at 5 mm. A dead `gripperPOV` band sits at 0.168-0.203 m, which is a height a
#: carried rod really is measured at, so this gate passes it and span is the only refusal it has.
AXIS_END_Z_BOUNDS = (SUPPORT_Z_BOUNDS[0], 0.44)

#: the line this channel has measured between a whole depth frame and a dead one, in pixels of
#: the survey frame that carry a surface at all. Measured by /tmp/mw_depth_check.py and
#: /tmp/mw_survey_multilayout.py on the same five layouts: the frame rendered by a process
#: holding one `MuJoCoRig` holds 109,232 surface pixels of 230,400 — and so does each of those
#: five layouts when its process holds one *layout* — while the second through fifth rig built
#: inside a single process holds 13,255-19,521. Fivefold gap, nothing in it.
#:
#: It is a reading aid and deliberately not a gate, because the phenomenon is not understood:
#: production's first five-layout batch measured a *healthy* 98,782-pixel surface count on its
#: fifth episode, while three shapes of a five-layout probe that changes nothing but renderer
#: lifetime measured dead buffers on four of five. A threshold that fires on one of those two
#: shapes and not the other would be a number deciding verdicts on an unexplained mechanism,
#: which is what this SPEC refuses. What the field does instead is make the ambiguity
#: unreadable-as-fact: a "there is no hole here" refusal that arrives with
#: `frame_surface_px: 47` is about the renderer, and the two populations are far enough apart
#: that no second threshold is needed to tell which sentence was measured.
SURVEY_FRAME_SURFACE_FLOOR = 50_000

#: how the two cameras' images actually look, measured by /tmp/mw_rgb_mean.py over the segmentation
#: buffer of the same renderer (mean RGB of the geom pixels that belong to each body), 35 layouts x
#: 2 views, sites off.
#:
#: `PEG_RGB` is the `corner2` mean, and on that camera it barely drifts: R 0.314-0.320,
#: G 0.937-0.952, B 0.309-0.314 over all 35 frames, `hue_of(mean)` 119.33-119.62°. The same body on
#: the wrist camera is a different picture — R 0.397-0.534, G 0.898-0.995, B 0.395-0.534, value p50
#: 1.00 — and what survives that is only the hue, 119.33-120.00° in both views. That asymmetry is
#: the reason these two lines exist: the tuple below is not a template to be matched, it is where a
#: hue band is centred, and only `hue_of()` of it is load-bearing.
#:
#: `WALL_RGB` is the same instrument pointed at the wrong thing. It is honest for `corner2`
#: (R 0.541-0.618, G 0.243-0.323, B 0.164-0.220; `hue_of(mean)` 12.34-16.35° against the declared
#: 14.30°) and useless for the wrist camera, where the frames that see a thousand pixels of the box
#: report `hue_of(mean)` 0.59-5.32° at value p50 0.31 — the far mode of a histogram whose mean sits
#: between two populations, which is the least trustworthy number this file declares.
PEG_RGB = (0.315, 0.942, 0.31)
WALL_RGB = (0.610, 0.300, 0.203)

OBJECT_ID = "peg 1"
TARGET_ID = "socket 1"

SURVEY_SYSTEM = """你是一台机械臂相机的标定器。图片里有一个固定的箱体，它的某个侧面上有一个穿墙的孔。
你唯一的任务是找出**这个箱体**，并给出它在图中的外接矩形。这不是识别任务，不是规划任务。

只输出一个 JSON 对象，顶层键只能是 box / notes：
{"box":{"found":true,"bbox":[x0,y0,x1,y1],"confidence":0.6},"notes":""}

规则：
- bbox 是像素坐标，原点在图片左上角，顺序 [x0,y0,x1,y1]，必须 0<=x0<x1<width、
  0<=y0<y1<height；
- box 指的是**带孔的那个箱体整体**：它可见轮廓的最左/最上/最右/最下四条边。不是那个孔，
  不是桌面，不是机械臂，不是桌上任何别的物体；
- 看不清或图里没有带孔的箱体就 found=false、bbox 填 [0,0,0,0]。宁可 found=false，
  不要给一个猜出来的框；
- 不要输出米制坐标、实体名、动作或计划。"""

SURVEY_USER = """图片如下（记作 image[0]），尺寸 {w}x{h}。请按 system 的结构只输出一个 JSON 对象。
"""


def survey_prompt_sha256() -> str:
    """Filed beside the decision prompt's digest, never merged into it.

    `PROMPT_VERSION`/`prompts_sha256()` in `prompts.py` identify the question this bench's
    25 archived episodes were already asked. Folding a new prompt into that pair would make
    two different experiments share one number; a separate digest makes the difference
    readable in the manifest."""
    return hashlib.sha256(f"mw-survey-v1|{SURVEY_SYSTEM}".encode("utf-8")).hexdigest()


# ------------------------------------------------------- declared wall geometry --


def _quat_mat(quat) -> np.ndarray:
    """Rotation matrix of a MuJoCo `wxyz` quaternion.

    `mju_quat2Mat` writes into a **flat 9-vector**; a `(3, 3)` buffer raises `TypeError`.
    The binding's shape is not guessable, and this one hid behind another bug: the first
    version of `wall_declaration` never reached the conversion, because it found no geom to
    convert and raised its own message first.
    """
    import mujoco

    flat = np.zeros(9)
    mujoco.mju_quat2Mat(flat, np.asarray(quat, dtype=float))
    return flat.reshape(3, 3)


def _body_subtree(m, root: int) -> list[int]:
    return [root] + [i for i in range(int(m.nbody)) if int(m.body_parentid[i]) == root]


def _to_root_frame(m, bid: int, root: int, xyz) -> np.ndarray:
    """A point expressed in body `bid`'s frame, carried into `root`'s frame.

    MetaWorld hangs an object's geoms and its `hole` site on an **unnamed child body** of the
    one `body_name` names (`box` -> body 35), so a declaration matching
    `geom_bodyid == box_bid` measures nothing: the extent it wants is one transform away, and
    a transform skipped silently is an extent read in the wrong place.
    """
    chain: list[int] = []
    b = int(bid)
    while b != root and b != 0:
        chain.append(b)
        b = int(m.body_parentid[b])
    p = np.asarray(xyz, dtype=float).copy()
    for c in reversed(chain):
        p = np.asarray(m.body_pos[c], dtype=float) + _quat_mat(m.body_quat[c]) @ p
    return p


def _aabb_in_root(m, bodies, root: int, groups) -> tuple[np.ndarray, np.ndarray, list[int]]:
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    geoms: list[int] = []
    for g in range(m.ngeom):
        bid = int(m.geom_bodyid[g])
        if bid not in bodies or int(m.geom_group[g]) not in groups:
            continue
        geoms.append(g)
        centre = np.asarray(m.geom_aabb[g][:3], dtype=float)
        half = np.asarray(m.geom_aabb[g][3:], dtype=float)
        rot = _quat_mat(m.geom_quat[g])
        pos = np.asarray(m.geom_pos[g], dtype=float)
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    corner = _to_root_frame(m, bid, root,
                                           pos + rot @ (centre + half * np.array(
                                               [sx, sy, sz], dtype=float)))
                    lo, hi = np.minimum(lo, corner), np.maximum(hi, corner)
    return lo, hi, geoms


#: `m.geom_group` of the geoms a camera can see, and of the ones it cannot. Measured on this
# bench: the box's two rendered meshes are group 1 and its seven wall blocks are group 4.
VISUAL_GEOM_GROUPS = (0, 1)
COLLISION_GEOM_GROUPS = (3, 4)
#: the site this channel declares its target from, named apart from `goal` because that one
# is the evaluator's and is not read here at all
HOLE_SITE = "hole"


def wall_declaration(backend: Any) -> dict[str, Any]:
    """The box's own extent, and the one point on its hole's axis this channel aims at.

    Every number below comes out of `model.geom_pos/geom_quat/geom_aabb/body_quat/site_pos`
    **of the box's own body subtree** — the scene file, identical in all 35 layouts — and
    never out of `data`. Three measurements are what the function is built on
    (`/tmp/mw_wall_frames.py`, layouts L0-L4 of `peg-insert-side-v3`):

    * the box body has no joints and the same modelled quaternion in every layout
      (`[0.707388, 0, 0, 0.706825]`: a 90° turn about z), so a local offset can be rotated
      into world axes once, from the model, and stay true for every episode;
    * the rendered extent is the AABB of the visual geoms — centre `[0, 0, 0.0995]`, half
      `[0.1, 0.1, 0.1005]` in the box's own frame — while the collision hull is
      centre `[0, 0, 0.1]`, half `[0.1, 0.1, 0.1]`. A camera sees the first and the survey's
      box is fitted to it, so the declaration is stated in the first;
    * the `hole` site sits at `[0, -0.096, 0.13]` in that frame.

    The benchmark's `goal` site is deliberately **not** read. It is attached to the *world*
    body and MetaWorld rewrites its position per layout, so `model.site_pos[goal]` is this
    episode's answer in metres and using it to calibrate a camera channel would be §10's
    forbidden injection dressed up as a static constant. (Measured, for the record and by a
    script that is not part of the channel: `goal` falls at `[0, -0.03, 0.13]` in the box's
    frame on all five layouts — on the hole's axis to 2.4e-5 m, 66 mm deeper than the mouth.)

    What this channel declares instead is the point where the hole's axis crosses the box's
    **central plane**: the components of `hole - visual_centre` perpendicular to the face
    normal, with the along-axis component zeroed. `distance_m` measures the perpendicular
    distance from the target point to the peg's measured axis, so every point on that axis
    answers the same question; the depth chosen makes the test *stricter* rather than looser —
    96 mm inside the mouth against the scorer's 66 mm — so a peg resting against the box
    cannot satisfy it and one pushed through to the middle of the cavity can. No threshold was
    adjusted after a result was seen; this sentence was written before the first frame of the
    first episode.

    The offset comes back in **world axes**, which is what `region_from_wall_centre` adds to a
    perceived centre. An earlier version returned it in the box's frame; for this model's 90°
    heading that swapped the two horizontal axes. The swap is invisible in the final numbers
    for exactly one reason — the chosen point has zero offset along both of them — which is
    precisely the kind of bug a "the readout looked right" check lets through.
    """
    import mujoco

    m = backend.env.model
    box_name = backend.OBJECT_BODIES["socket 1"]
    box_bid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, box_name)
    assert box_bid >= 0, f"no body {box_name!r} in the model"
    hole_sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, HOLE_SITE)
    assert hole_sid >= 0, (
        f"this bench has no {HOLE_SITE!r} site, and this channel declares its target from "
        f"the hole's own geometry rather than falling back to the benchmark's goal site")

    bodies = _body_subtree(m, box_bid)
    lo_v, hi_v, vis = _aabb_in_root(m, bodies, box_bid, VISUAL_GEOM_GROUPS)
    lo_c, hi_c, col = _aabb_in_root(m, bodies, box_bid, COLLISION_GEOM_GROUPS)
    if not vis:
        raise ValueError(
            f"{box_name} and its child bodies have no geom in groups {VISUAL_GEOM_GROUPS} "
            f"to measure in the model file")
    centre = (lo_v + hi_v) / 2.0
    half = (hi_v - lo_v) / 2.0
    hole_local = _to_root_frame(m, int(m.site_bodyid[hole_sid]), box_bid,
                                m.site_pos[hole_sid])
    offset = hole_local - centre
    #: the face the hole is in is the one whose normal carries the site farthest from the
    # centre: the mouth is *on* a wall, so that axis is the wall's, and the two remaining
    # components say where in that wall it sits.
    axis = int(np.argmax(np.abs(offset)))
    depth = float(offset[axis])
    perpendicular = offset.copy()
    perpendicular[axis] = 0.0
    rot = _quat_mat(m.body_quat[box_bid])
    unit = np.zeros(3)
    unit[axis] = float(np.sign(depth))

    def rr(v, n=4) -> list[float]:
        return [round(float(x), n) for x in v]

    return {
        "visual_geoms": vis, "collision_geoms": col,
        "half_extents": rr(half),
        "visual_centre_local": rr(centre),
        "collision_centre_local": rr((lo_c + hi_c) / 2.0),
        "collision_half_extents": rr((hi_c - lo_c) / 2.0),
        "hole_local": rr(hole_local),
        "hole_depth_from_centre_m": round(abs(depth), 4),
        "hole_offset_in_wall": rr(rot @ perpendicular),
        "hole_axis_world": rr(rot @ unit),
        "hole_radius_m": round(float(max(np.asarray(m.site_size[hole_sid],
                                                   dtype=float)[:2])) or 0.03, 4),
        "body_quat_wxyz": rr(np.asarray(m.body_quat[box_bid], dtype=float), 6),
    }


def region_from_wall_centre(decl: dict[str, Any], wall_xyz) -> RegionDecl:
    """The target region, from one perceived box centre and the declared offset.

    `wall_xyz` must come from pixels — the box centre that `fit_aperture`'s mouth implies, or
    nothing.
    `hole_offset_in_wall` is the point where the hole's axis crosses the box's central plane,
    already expressed in world axes by `wall_declaration`; adding it to a perceived centre is
    therefore exact while the box keeps its modelled heading, which is the assumption the
    contract test checks against every layout actually run.
    `floor_top_z` carries that point's height because `as_region()` puts it in the region's
    centre z, which is the point the placement distance is measured to; `wall_top_z` is the
    same number, and is inert on this channel: `MujocoVerifier` reads `center` and
    `inner_half` only, and the privileged arm leaves the field at its default.

    `inner_half` is the `hole` site's own modelled half-size — the same kind of number the
    privileged arm takes from `goal`, and from the same array family. It makes this channel's
    placement tolerance `0.005 + 0.015 = 0.020 m`, i.e. **tighter** than the privileged arm's
    0.025 and far tighter than the scorer's 0.07, which is the honest consequence of declaring
    a point a camera could justify rather than borrowing the evaluator's.
    """
    dx, dy, dz = decl["hole_offset_in_wall"]
    x, y, z = (float(v) for v in wall_xyz)
    return RegionDecl(
        target_id=TARGET_ID, label="the hole in the side of the box",
        center_xy=(round(x + dx, 4), round(y + dy, 4)),
        inner_half=float(decl["hole_radius_m"]),
        floor_top_z=round(z + dz, 4), wall_top_z=round(z + dz, 4),
        capacity=1, accepts=[OBJECT_ID])


# --------------------------------------------- from a depth anomaly back to metres --

#: Two estimators were measured before this one, and each failed on a different axis, which is
#: the only reason a third exists:
#:
#: * the outline fit (`/tmp/mw_fit_sensitivity.py`, `SURVEY_FIT_*` of an earlier revision)
#:   converted **one pixel** of reported-bbox error into **14.8 mm** of placement error at this
#:   camera's magnification, so its accuracy simply *was* the vision model's accuracy;
#: * a depth vote over candidate placements (`/tmp/mw_depth_diag.py`, `/tmp/mw_mouth.py`) reaches
#:   5.7-11.2 mm, but only while the candidate box stays within ~10 px of the true outline —
#:   unconstrained it slides forward onto the robot and lands 86-220 mm away.
#:
#: What follows reads the hole itself instead of the box around it, and that is what the numbers
#: below are sensitive to: sliding the region of interest 15 px off the box's true outline moves
#: the answer by about as much as not sliding it at all (layout 0: 1.2 mm → 1.2 mm, layout 21:
#: 0.3 mm → 0.2 mm). The whole frame, used as the region instead, finds the hole on **11** of the
#: 35 layouts rather than 33 — the box's own face is only the *second* plane mode in a frame that
#: also contains the robot's pedestal at the first — so the surveyed box is still required, as an
#: address rather than as a measurement.
APERTURE_PLANE_TOL_M = 0.004
#: how many times the plane's own pixel mean is taken before the candidate is called a wall. The
# loop stops by itself when a step moves the plane by less than a tenth of a millimetre, so this is
# only the ceiling. Measured over the 35 layouts by `/tmp/mw_port_check.py`, which prints the steps
# each wall took: the most any plane needed was five, the ceiling was never reached, and the second
# plane in every frame — whatever surface it is — settled in two.
APERTURE_PLANE_REFINE_ITERS = 6
#: the smallest number of pixels a plane has to own before it is a wall, and the largest number
# of candidate planes examined per frame. Both come from one measured pair: in `corner2` the
# pedestal's vertical face — which carries the *same declared normal* as the box's wall, because
# the normal is what the scene file says a "side" is — is the global mode at `p . n = +699.3 mm`
# on 6,740 px, while the box's own face is the second mode at −212.7 mm on 3,183 px. A plane is
# therefore not chosen by being the most common surface in the picture, and 6 candidates with a
# 400 px floor is what a face of ~3,200 px inside a region that also holds the pedestal needs.
APERTURE_MIN_MODE_PX = 400
APERTURE_MAX_MODES = 6
#: what this channel is looking for, in pixels. Measured over the 35 layouts by
# `/tmp/mw_port_check.py`: an unoccluded hole gives an enclosed island of 113-139 px spanning
# 76.9-80.0 mm on a side at this camera's ~3.4 mm/px. The opening itself is a 60 x 60 mm square,
# which is in the scene file rather than declared here: the box's wall is built from blocks whose
# `geom_pos/geom_size` leave x in [-0.03, 0.03] and z in [0.10, 0.16] between them, around the
# `hole` site at [0, -0.096, 0.13]. The island is wider than the opening because it is the bore's
# *interior* seen through the mouth, projected back onto the wall's plane — a ray that grazes the
# near rim reaches the far wall outside the rim's own outline. The floor rejects specks (a rim
# reflection, a one-pixel seam between two collision blocks); the ceiling rejects what is not a
# hole but a gap at the box's edge — the table and the background, which an unenclosed island of
# 1,000+ px looks exactly like if enclosure is not demanded. The declared `hole` site is far
# smaller than any of these numbers (`hole_radius_m` = 0.005, the site's own modelled half-size,
# from `model.site_size`), and is the number the *verifier's* tolerance comes from, not this one.
APERTURE_MIN_PX = 30
APERTURE_MAX_PX = 1200
APERTURE_RING_PX = 3
#: how far the island is grown before its centre is taken, because the *edge* of an aperture is
# decided by a 4 mm threshold on a rim that curves and a threshold on a rim should not be allowed
# to move the answer. Two alternatives were measured against it over all 35 layouts
# (`/tmp/mw_anomaly.py`): a recess-*weighted* centroid over the same grown region is worse
# (8.2-9.4 mm against 0.6-1.3 mm) because the weight is a monotone function of how much of the
# bore's far wall each pixel sees, which is not the same as where the axis is; and masking the
# grown set to exclude pixels in front of the wall — which the occluder's rays *are*, and which
# looks physically correct — is also worse (layout 0: 1.2 → 3.7 mm, 14: 0.8 → 5.0, 18: 0.4 → 3.8,
# 27: 5.9 → 10.1), because those rays meet the plane inside the part of the hole the occluder
# hides and stand in for it. The plain mean of the unmasked grown set is the measured choice.
APERTURE_GROW_PX = 2
#: how far outside the surveyed box the plane search looks. 25 px is not a tuned accuracy
# parameter — it is the margin that leaves room for a model to describe the box loosely without
# cutting the wall out of the region, and the 15 px shift above is what says the answer does not
# ride on it.
APERTURE_ROI_MARGIN_PX = 25
#: the largest per-pixel change of the plane coordinate that still counts as "this surface is
#: parallel to the declared face", in metres per pixel. A face parallel to the declared one keeps
#: `p . n` inside a 4 mm band as it walks across the image; a floor cut through by that plane loses
#: tens of millimetres per pixel (`/tmp/mw_wall_plane_mask.py`: the declared band on layout 0 broke
#: into 81 connected pieces, the largest a 1,548 px patch of *floor* under the table). So this is a
#: parallelism filter, not a proximity filter, and it changes which planes get asked about rather
#: than which answer is chosen among them.
#:
#: The value is a plateau, not a tuned point. Nine values were swept over all 35 layouts from
#: `corner2` with the whole frame as the region — the worst box a model ever reported here
#: (`/tmp/mw_flat_surface_filter.py`): the declared face is among the six candidate planes on 14/35
#: frames unfiltered, 35/35 at 0.1 and 0.2 mm/px, 34 at 0.5, 30 at 1.0, 13 at 10; median pixels
#: kept run 14,354 at 0.1 to 18,009 at 1.0. 1.0 mm/px is the value whose *end-to-end answer* was
#: measured through this function on three cameras (`/tmp/mw_flat_region_answer.py`,
#: `/tmp/mw_flat_region_view.py`, and the shipped mask in `/tmp/mw_aperture_region_ship.py`): on
#: `corner2` the answer is taken on the declared face in 30 of 35 layouts against 14 unfiltered, its
#: lateral error is 0.7 mm median against 472.8 mm, and 29 answers land within 2 mm against 11 — with
#: 18 layouts moving from a 400-600 mm answer to a sub-2 mm one and **no layout refused that the
#: unfiltered reading had got right**. The independent second camera (`corner3`) puts the answer on
#: the declared face in 27 of 35 against 20, at a 3.4 mm median lateral. What the rule does not buy is
#: named in the same table: six `corner2` layouts stay ~600 mm wide, because there the *plane* is
#: right and the enclosed island chosen is a speck somewhere else on it (which is #100's question,
#: not this one). And it says nothing about *which* of the box's two parallel faces is being looked
#: at: on `corner`, a camera standing behind the wall, the unfiltered reading answered the box's own
#: far face on 35 of 35 layouts, 195.3-196.2 mm along the normal and inside 3.9 mm laterally
#: (`/tmp/mw_flat_region_view.py`, `/tmp/mw_face_identity_legal.py`). The shipped rule refuses all 35
#: of those — the gate below, and `corner`'s row in the table above.
APERTURE_FLAT_GRADIENT_M_PER_PX = 0.001
#: the front-facing plane gate in `fit_aperture`. It has no constant of its own because it has no
#: threshold: a candidate plane is a wall this camera can be looking at only if it stands on the
#: eye's side of the declared normal, and `h = a + z*b` with `z > 0` makes `a - h > 0` the whole
#: test. Parallelism does not say that — the plane 196 mm behind the
#: declared face is parallel to it, belongs to the *same box* (`/tmp/mw_body35_chain.py`: its two
#: geoms live in body #35, whose parent is the declared `box`), and it is where 35 confident
#: millimetre-precise answers came from on `corner`. Measured over the 105 readings of
#: `/tmp/mw_face_facing_sign.py`: `corner2` keeps 25 of 25 at +1455.6..+1550.7 mm, `corner3` keeps
#: 16 of 16 at +1055.7..+1150.6 mm, `corner` keeps **0 of 35**, all at −749.5..−654.2 mm — no
#: reading came near the line, which is why this one needs no tuned threshold: it is a sign.
#: Necessary and not sufficient (a table or a background board is front-facing too — refusing those
#: is the region's job and #100's), and it is a refusal with a count in the evidence, never a
#: re-ranking of the candidates that do answer. Being a sign, it has no threshold to set: the test
#: in `fit_aperture` is `a - h_face > 0` and nothing else.


def wall_rotation(decl: dict[str, Any]) -> np.ndarray:
    """The box's modelled attitude as a rotation matrix, from the declared quaternion."""
    return _quat_mat(decl["body_quat_wxyz"])


def wall_silhouette(camera: CameraSpec, centre, *, rot: np.ndarray,
                    half) -> Optional[list[float]]:
    """The eight corners of the declared box at this centre, as a pixel outline.

    `None` when any corner is behind the camera: an outline that cannot be drawn is not an
    outline with a large error, and mixing the two would put a geometry failure in the same
    number as a placement failure.
    """
    centre = np.asarray(centre, dtype=float)
    signs = np.indices((2, 2, 2)).reshape(3, -1).T * 2.0 - 1.0
    # `rot @ local` for a stack of locals is `local @ rot.T`; the other way round asks numpy
    # to multiply a (3, 3) by an (8, 3) and it is right to refuse
    offsets = (signs * np.asarray(half, dtype=float)) @ rot.T
    xs, ys = [], []
    for off in offsets:
        try:
            px, py, _d = camera.project(centre + off)
        except ValueError:
            return None
        xs.append(px)
        ys.append(py)
    return [min(xs), min(ys), max(xs), max(ys)]


def aperture_frame(decl: dict[str, Any],
                   *, rot: Optional[np.ndarray] = None) -> dict[str, Any]:
    """The face the declared hole is bored through, as a plane.

    Three scene-file numbers make a depth image readable as a picture of a hole: which local axis
    the hole's offset is largest along (so which face it is), how far that face stands out from the
    box's central plane, and where in the face the axis runs (`perp`, the same world vector
    `wall_declaration` publishes as `hole_offset_in_wall` and `region_from_wall_centre` adds to a
    perceived centre). The assert at the end is the reason to derive them here rather than read
    that field back: `perp + depth_along * normal == mouth_offset_world`, and if the two routes to
    the declared mouth ever disagree, one of them is being trusted wrongly.

    Measured over all 35 layouts of `peg-insert-side-v3`: the outward normal is
    `[1.0, -0.0008, 0]` — the declared `hole_axis_world` to four decimals — `a1` is world y and
    `a2` world z, `span_half` is `[0.1, 0.1005]`, and the face plane sits 100 mm out from a
    central plane whose hole is 96 mm deep. So a point on the hole's axis can be wrong by
    centimetres *along* it and still be the target this verifier accepts.
    """
    rot = wall_rotation(decl) if rot is None else rot
    centre_local = np.asarray(decl["visual_centre_local"], dtype=float)
    site_local = np.asarray(decl["hole_local"], dtype=float)
    half = np.asarray(decl["half_extents"], dtype=float)
    offset = site_local - centre_local
    axis = int(np.argmax(np.abs(offset)))
    sign = float(np.sign(offset[axis]))
    span = [i for i in (0, 1, 2) if i != axis]
    mouth_local = site_local.copy()
    mouth_local[axis] = sign * half[axis]
    depth_along = float((mouth_local[axis] - centre_local[axis]) * sign)
    perp_local = site_local - centre_local
    perp_local[axis] = 0.0
    perp = rot @ perp_local
    mouth = rot @ (mouth_local - centre_local)
    normal = rot[:, axis] * sign
    assert float(np.max(np.abs(perp + depth_along * normal - mouth))) < 1e-9, (
        f"the face decomposition {(perp + depth_along * normal).tolist()} != {mouth.tolist()}: "
        f"the two routes to the declared mouth disagree")
    assert float(np.max(np.abs(normal - np.asarray(decl["hole_axis_world"], dtype=float)))) < 1e-3, (
        f"the face normal {normal.tolist()} is not the declared hole axis "
        f"{decl['hole_axis_world']}: the depth test below would have the wrong sign")
    return {"axis": axis, "sign": sign, "half_extents": half, "normal": normal,
            "a1": rot[:, span[0]], "a2": rot[:, span[1]],
            "span_half": [float(half[span[0]]), float(half[span[1]])],
            "perp": perp, "depth_along": depth_along, "mouth_offset_world": mouth}


def _camera_rays(camera: CameraSpec) -> tuple[np.ndarray, np.ndarray]:
    """`eye`, and the per-pixel world directions `d` whose view-axis component is 1 — so the
    pixel (u, v) at metric view-axis distance z is the point `eye + z * d[:, v, u]`.

    The same algebra `CameraSpec.unproject` applies one pixel at a time, run over the frame at
    once: this estimator needs the plane intersection of every pixel in a region, which is
    ~230,000 `unproject` calls or one `einsum`.
    """
    view = camera.view_matrix
    rot = view[:3, :3]
    eye = np.linalg.inv(rot) @ (-view[:3, 3])
    uu, vv = np.meshgrid(np.arange(camera.width, dtype=float),
                         np.arange(camera.height, dtype=float))
    ex = (uu - camera.cx) / camera.fx
    ey = -(vv - camera.cy) / camera.fy
    ez = -np.ones_like(ex)
    d = np.stack([rot[0, 0] * ex + rot[1, 0] * ey + rot[2, 0] * ez,
                  rot[0, 1] * ex + rot[1, 1] * ey + rot[2, 1] * ez,
                  rot[0, 2] * ex + rot[1, 2] * ey + rot[2, 2] * ez])
    return eye, d


def _frame_metres(window, camera: CameraSpec) -> np.ndarray:
    """`perception.camera.depth_to_meters` over a whole frame.

    Identical arithmetic to the scalar function the rest of the channel uses, without its
    branches: the clip on `z_ndc` is what makes the endpoints agree, since
    `depth_to_meters(0.0)` is `near_m` and `depth_to_meters(1.0)` is `far_m` and this returns the
    same two numbers. `MuJoCoRig.render` stores "no surface" as 1.0, so a background pixel reads
    as the far plane here and is excluded as `~live` below.
    """
    d = np.asarray(window, dtype=float)
    near, far = camera.near_m, camera.far_m
    z_ndc = np.clip(2.0 * d - 1.0, -1.0, 1.0)
    return (2.0 * near * far) / ((far + near) - z_ndc * (far - near))


def _plane_field(camera: CameraSpec, depth_window, *,
                 decl: dict[str, Any]) -> dict[str, Any]:
    """The plane coordinate every aperture reading is taken in, and which pixels own a surface.

    `h = a + z*b` is where each pixel's surface stands along the declared face normal. `fit_aperture`
    and `flat_surface_region` both need it, and two copies of the arithmetic would be two things that
    can disagree about what a plane is — which is the whole content of the parallelism rule.
    """
    rot = wall_rotation(decl)
    face = aperture_frame(decl, rot=rot)
    eye, d = _camera_rays(camera)
    z = _frame_metres(depth_window, camera)
    b = np.einsum("k,kij->ij", face["normal"], d)
    a = float(eye @ face["normal"])
    live = np.isfinite(z) & (z > camera.near_m * 1.001) & (z < camera.far_m * 0.9999)
    return {"rot": rot, "face": face, "eye": eye, "d": d, "z": z, "a": a, "b": b,
            "h": a + z * b, "live": live}


def surface_gradient(h, live) -> np.ndarray:
    """How far the plane coordinate moves per pixel, over pixels that carry a surface.

    Along each axis a pixel keeps the *smallest* step to a live neighbour (a surface one pixel wide
    next to a steep one is still a surface, and the wall's own rim must not be able to mark the wall
    unflat); the two axes are then combined as a 2-norm. A pixel with no live neighbour in either
    direction gets `inf`, so it is never flat: the frame's own border is a step, not a wall.
    """
    h = np.asarray(h, dtype=float)
    live = np.asarray(live, dtype=bool)
    inf = float("inf")

    def along_axis(step: np.ndarray, ok: np.ndarray, axis: int) -> np.ndarray:
        """`step` is |dh| across each neighbouring pair along `axis`; a pixel takes the smaller of
        its two sides, and `inf` where a side has no live neighbour."""
        m = np.where(ok, step, inf)
        low, high = np.full(h.shape, inf), np.full(h.shape, inf)
        if axis == 1:
            low[:, :-1], high[:, 1:] = m, m
        else:
            low[:-1, :], high[1:, :] = m, m
        return np.minimum(low, high)

    du = along_axis(np.abs(h[:, 1:] - h[:, :-1]), live[:, 1:] & live[:, :-1], 1)
    dv = along_axis(np.abs(h[1:, :] - h[:-1, :]), live[1:, :] & live[:-1, :], 0)
    return np.hypot(du, dv)


def flat_surface_region(camera: CameraSpec, depth_window, *, decl: dict[str, Any],
                        g: float = APERTURE_FLAT_GRADIENT_M_PER_PX,
                        ) -> tuple[np.ndarray, dict[str, Any]]:
    """The pixels that carry a surface *parallel to* the declared face, and what that cost.

    Pass the mask to `fit_aperture` as `region`. It decides *which pixels may be this wall*: pixels
    outside it are kept out of the plane histogram, so a slanted floor cannot be a mode, and they
    cannot be counted as wall. What they are not is an opening — whether a pixel closes a hole or
    stands inside one is a depth question, and `fit_aperture` answers it from `h` alone, because a
    bore's rim and the arm crossing the mouth are surfaces, just not parallel ones. The evidence
    files how many pixels survived, because a survey that filtered the wall away and a survey that
    found no hole look identical in the answer.
    """
    f = _plane_field(camera, depth_window, decl=decl)
    grad = surface_gradient(f["h"], f["live"])
    mask = f["live"] & (grad < float(g))
    return mask, {"gradient_m_per_px": round(float(g), 6),
                  "kept_px": int(mask.sum()),
                  "frame_surface_px": int(f["live"].sum())}


def fit_aperture(camera: CameraSpec, depth_window, bbox, *, decl: dict[str, Any],
                 region: Optional[np.ndarray] = None
                 ) -> tuple[Optional[list[float]], dict[str, Any]]:
    """The hole in the side of the surveyed box, measured from the depth image.

    Returns `(mouth point in world metres, evidence)`, or `(None, evidence)` when nothing in the
    region is a hole — and the evidence says *which* of the two ways that happens, because they
    are different failures: no plane in the region is a wall, or a wall with nothing let through.

    Two facts make this a measurement rather than a guess. The face the hole is in has a
    **declared** normal, and MuJoCo's depth is noiseless, so the histogram of `p . n` over the
    region collapses to one spike per planar surface: measured on real renders, the fitted span of
    the box's own face is 206.2 x 200.3 mm against the declared 200 x 201. A hole is then the only
    place on that face where a ray goes *past* the plane, so the aperture is exactly the set of
    pixels whose `p . n` falls short of the face's — and "falls short" is measured against the
    **outward** normal, because the wall faces the survey camera and receding along it means
    coming nearer. The first run of this estimator had that test the other way round, and the
    island it called a hole was 2,977 px over `u[216,295] v[196,276]` on layout 0: the robot's own
    arm and pedestal standing *in front* of the box (`/tmp/mw_scan.py`).

    Being recessed is necessary and not sufficient. The table behind the box, the background
    beside it and the box's own far edge are all recessed, and each of them touches wall pixels
    where the silhouette is one pixel thick. What is sufficient is the statement a person applies
    when they see a hole — the wall surrounds it — and it is topological, so it has no threshold
    to set. Take the wall together with everything standing between the wall and the camera (the
    arm, which occludes but is not a gap in the wall), fill the regions that set encloses, and
    keep what falls inside them and is not the set. `binary_fill_holes` cannot fill anything
    connected to the edge of the array, so an island that is really a gap at the box's boundary —
    the table a metre away, recessed by 100-200 mm because it is a metre away rather than a bore
    deep — is never a hole however much of its border is wall.

    What that buys, measured over all 35 layouts of `peg-insert-side-v3` from `corner2` by
    `/tmp/mw_anomaly.py` and re-measured through this function by `/tmp/mw_port_check.py`: an
    enclosed island is found on **33**, and the lateral error of its centre against the declared
    mouth has **median 0.6 mm**, maximum **1.1 mm** over the 29 layouts whose hole is not occluded,
    where the island is 113-139 px spanning 76.9-80.0 mm. The four exceptions are recorded rather
    than gated away: layouts 5, 9 and 23 find a crescent (14.5, 18.3 and 18.3 mm, from islands of
    50, 42 and 39 px), layout 27 finds 6.2 mm from 94 px, and layouts 24 and 31 find nothing,
    because the arm crosses the mouth *and* the box's edge, so the hole is no longer enclosed and
    the honest reading of that frame is *the hole is not visible from here*. The along-axis error
    is −1.1 to −1.3 mm on all 33 and is never compared to a threshold, for the reason two
    paragraphs down.

    Candidates are ranked by `ring_wall` — the fraction of a 3 px border that is wall — and then
    by area, and never by area alone. That order is a measurement: enclosure is true of the hole
    and of several gaps in a frame, and on layouts 4, 8, 10, 11, 28 and 30 the largest enclosed
    island is a gap, not the hole, 150-230 mm away from it. `ring_wall` is not a gate: nothing here
    refuses a number for being below a line — the lowest fraction this accepted is 0.625 (layout 9,
    the worst measurement in the table) and 0.696 belongs to layout 14, accurate to 0.8 mm — the
    runner-up islands are reported with their own area and border fraction, and the ranking is the
    only use made of it.

    The along-axis coordinate is free, as the assert in `aperture_frame` says: the verifier
    measures the perpendicular distance from the target point to the peg's axis, every point on
    the hole's axis answers the same question, and the plane the answer is taken on comes from the
    wall itself. Only the two in-plane coordinates depend on pixels, which is why a 4 mm plane
    tolerance can afford to be loose.

    `region` narrows *which pixels may be a wall*, and it exists because a rectangle is not a plane.
    A survey box the height of the frame also contains the floor, and the floor meets the declared
    normal's plane along a line, so `h` over it runs from one side of the tolerance to the other and
    the wall becomes the *second* plane in its own picture: with the whole frame as the region, the
    declared face is among the six candidates on 14 of 35 layouts, and the answers fly 472 mm wide
    (median). Where `region` comes from `flat_surface_region` — pixels whose `h` does not move — the
    same frames give their planes to this function in the declared face's order instead: 30 of 35
    answers are then taken *on* that face, at 0.7 mm median lateral error against 472.8, and 29 of
    them land within 2 mm (`/tmp/mw_flat_region_answer.py`, `/tmp/mw_aperture_region_ship.py`;
    `docs/continuous-decision-v0.2-phase-log.md` H-15, H-19). The mask
    stops there. It does not decide what a hole is: a pixel it rejects is a surface that is not this
    wall, and standing *in front* of the wall is the commonest reason for being rejected, so reading
    a rejection as "the ray went through" both breaks the ring that encloses the real opening and
    makes `recess_mm` stop being a depth in millimetres — which is the whole of why #98 cannot be
    paid for with this mask, and is pinned by
    `tests/contract/test_mujoco_perception.py::test_a_surface_rejected_for_not_being_parallel_still_closes_a_hole`.

    The second narrowing is not a region and has no parameter: a candidate plane that stands on the
    far side of the declared normal from this camera is refused, counted in
    `planes_refused_back_facing` and mentioned in `why`. Parallelism selects an *orientation*, not an
    identity — measured on `corner`, a camera behind the box, the flat-region rule works exactly as
    well and answers the box's own far face on **35 of 35** layouts, 195.3-196.2 mm along the normal,
    lateral error inside 3.9 mm (`/tmp/mw_flat_region_view.py`, `/tmp/mw_corner_plane_identity.py`).
    The sign test refuses all 35 and none of the 41 correct answers on the two front-facing cameras
    (`/tmp/mw_face_facing_sign.py`). It is necessary and not sufficient, and nothing about it is
    tuned.
    """
    from scipy.ndimage import binary_dilation, binary_fill_holes, label

    f = _plane_field(camera, depth_window, decl=decl)
    rot, face = f["rot"], f["face"]
    normal, a1, a2 = face["normal"], face["a1"], face["a2"]
    eye, d, z, b, a, h, live = f["eye"], f["d"], f["z"], f["b"], f["a"], f["h"], f["live"]
    margin = int(APERTURE_ROI_MARGIN_PX)
    x0 = max(0, int(np.floor(float(bbox[0]))) - margin)
    y0 = max(0, int(np.floor(float(bbox[1]))) - margin)
    x1 = min(camera.width - 1, int(np.ceil(float(bbox[2]))) + margin)
    y1 = min(camera.height - 1, int(np.ceil(float(bbox[3]))) + margin)
    sub = np.zeros(z.shape, dtype=bool)
    sub[y0:y1 + 1, x0:x1 + 1] = True
    filtered = region is not None
    # "no region" and "every pixel is in the region" are the same measurement, and writing the
    # second as the first keeps one set of mask arithmetic instead of two that can drift apart
    region = np.ones(live.shape, dtype=bool) if region is None else np.asarray(region, dtype=bool)
    inside = live & sub & region
    declared_span = [round(2000.0 * v, 1) for v in face["span_half"]]
    evidence: dict[str, Any] = {
        "method": "enclosed_depth_anomaly_on_declared_face_plane",
        "camera": camera.camera_id, "roi_px": [x0, y0, x1, y1], "roi_margin_px": margin,
        "surface_px": int(inside.sum()), "plane_tol_m": APERTURE_PLANE_TOL_M,
        "region_filtered": filtered, "region_px": int(region.sum()) if filtered else None,
        "face": {"normal": [round(float(v), 4) for v in normal],
                 "a1": [round(float(v), 4) for v in a1],
                 "a2": [round(float(v), 4) for v in a2],
                 "span_half_mm": [round(1000.0 * v, 1) for v in face["span_half"]],
                 "depth_along_mm": round(1000.0 * face["depth_along"], 1),
                 "mouth_offset_world_m": [round(float(v), 4)
                                          for v in face["mouth_offset_world"]]},
        "hole_site_marker_diameter_mm": round(2000.0 * float(decl["hole_radius_m"]), 1),
        "planes": [], "candidates": []}

    def uv(mask, h_face):
        """The coordinates, in the face plane's own axes, of where each pixel of `mask` meets it.
        Everything this function reports about *where in the wall* the hole is comes from these."""
        dv, du = np.nonzero(mask)
        t = (h_face - a) / b[dv, du]
        pts = eye[:, None] + d[:, dv, du] * t[None, :]
        return pts.T @ a1, pts.T @ a2

    if int(inside.sum()) < APERTURE_MIN_MODE_PX:
        evidence["why"] = (f"only {int(inside.sum())} pixels of the surveyed region carry a "
                           f"surface, and a wall needs {APERTURE_MIN_MODE_PX}")
        return None, evidence

    hv = h[inside]
    bins = np.arange(float(hv.min()) - APERTURE_PLANE_TOL_M,
                     float(hv.max()) + APERTURE_PLANE_TOL_M + 1e-9, APERTURE_PLANE_TOL_M)
    counts, edges = np.histogram(hv, bins=bins)
    modes = [int(k) for k in np.argsort(counts)[::-1]
             if counts[k] >= APERTURE_MIN_MODE_PX][:APERTURE_MAX_MODES]
    candidates: list[dict[str, Any]] = []
    faces: list[float] = []
    dups = 0
    back_facing = 0
    for k in modes:
        coarse = float((edges[k] + edges[k + 1]) / 2.0)
        band = (np.abs(h - coarse) <= APERTURE_PLANE_TOL_M) & inside
        if int(band.sum()) < APERTURE_MIN_MODE_PX:
            continue
        # the bin centre is a 4 mm guess and the surface is not: the mean of the plane's own pixels
        # moves the candidate onto the wall it came from, and taking it to a fixed point is what
        # makes that value a property of the wall rather than of which bin it fell in. Refusing a
        # duplicate bin *without* converging moved layouts 17 and 19 of the sweep by 0.5 mm and the
        # median from 0.6 to 0.7; converging put both back (0.6 and 0.8 mm, median 0.6) with one
        # plane per wall. `/tmp/mw_port_check.py` is the table.
        h_face, iters = coarse, 0
        while iters < APERTURE_PLANE_REFINE_ITERS:
            band = (np.abs(h - h_face) <= APERTURE_PLANE_TOL_M) & inside
            if int(band.sum()) < APERTURE_MIN_MODE_PX:
                break
            nxt = float(np.mean(h[band]))
            iters += 1
            settled = abs(nxt - h_face) < 1e-4
            h_face = nxt
            if settled:
                break
        # ...and a fixed point is what makes two bins of one wall the same wall. A noiseless surface
        # is a delta in this histogram and a delta can sit on a bin edge: on the frame
        # `tests/contract/test_mujoco_perception.py::
        # test_a_wall_that_straddles_a_histogram_bin_is_one_wall` draws, one wall of 12,987 px
        # arrived as two modes, 8,042 and 4,945 px, whose bands were *identical* sets and whose
        # refined offsets were the same number. Refusing the duplicate is what keeps `planes` a
        # count of walls and the candidate list a list of different islands.
        if any(abs(h_face - got) < APERTURE_PLANE_TOL_M for got in faces):
            dups += 1
            continue
        # ...and a plane the eye is standing in front of along the declared normal is not a wall this
        # camera is looking at. `h = a + z*b` with `z > 0`, so `a - h_face <= 0` says every ray that
        # met this surface travelled *away* from the normal: the surface faces elsewhere. The box has
        # two such planes and they are strictly parallel, so nothing above this line can tell them
        # apart — see the constant comment on APERTURE_FLAT_GRADIENT_M_PER_PX for the measurement.
        if a - h_face <= 0.0:
            back_facing += 1
            continue
        faces.append(h_face)
        wall = (np.abs(h - h_face) <= APERTURE_PLANE_TOL_M) & inside
        # What closes a hole is every pixel the depth frame puts *at or in front of* this plane, and
        # what opens one is only what stands behind it or returns no surface at all. Neither of those
        # asks the region anything, because the region is a statement about *parallelism*: a pixel it
        # rejected is a surface that is not this wall — a bore's rim, the arm crossing the mouth, a
        # table edge — and every one of those is solid. Reading a rejection as "the ray went through"
        # is the mistake that made this mask refuse frames it should have answered: on the production
        # camera's own layout 0, 34 of the mouth island's 144 enclosing ring pixels are rejected by
        # the region — 29 of them standing *in front* of the plane, 5 in the plane's own band on a
        # surface too steep to be parallel — so under that reading the ring opened 34 gaps,
        # `binary_fill_holes` no longer enclosed the opening, and the survey refused a hole this code
        # now answers 0.04 mm from its own no-region reading on the same frame
        # (`/tmp/mw_ship_frame_probe.py`). The second reading of the same mistake is #98's: a hole
        # assembled out of rejected pixels has a `recess_mm` of 0.0 by construction, because the
        # pixels standing *in front* of the plane are the ones the clip sends there.
        blocker = live & sub & (h - h_face >= -APERTURE_PLANE_TOL_M)
        recessed = sub & ~blocker
        holes = binary_fill_holes(blocker) & ~blocker
        lab, n = label(recessed & holes)
        fv, fu = np.nonzero(wall)
        pts = eye[:, None] + d[:, fv, fu] * z[None, fv, fu]
        evidence["planes"].append({
            "plane_mm": round(1000.0 * h_face, 2), "plane_bin_mm": round(1000.0 * coarse, 1),
            "refine_mm": round(1000.0 * (h_face - coarse), 2), "refine_iters": iters,
            "wall_px": int(wall.sum()),
            "wall_span_mm": [round(1000.0 * float((pts.T @ a1).max() - (pts.T @ a1).min()), 1),
                             round(1000.0 * float((pts.T @ a2).max() - (pts.T @ a2).min()), 1)],
            "declared_wall_span_mm": declared_span, "enclosed_recessed_islands": int(n)})
        for i in range(1, n + 1):
            isl = lab == i
            size = int(isl.sum())
            if not APERTURE_MIN_PX <= size <= APERTURE_MAX_PX:
                continue
            ring = binary_dilation(isl, iterations=APERTURE_RING_PX) & ~isl
            grown = binary_dilation(isl, iterations=APERTURE_GROW_PX)
            gu1, gu2 = uv(grown, h_face)
            if gu1.size == 0:
                continue
            dv, du = np.nonzero(isl)
            point = h_face * normal + float(gu1.mean()) * a1 + float(gu2.mean()) * a2
            candidates.append({
                "plane_mm": round(1000.0 * h_face, 2), "px": size,
                "grown_px": int(grown.sum()),
                "ring_wall": round(float(wall[ring].sum()) / max(int(ring.sum()), 1), 3),
                "recess_mm": round(1000.0 * float(
                    np.median(np.clip(h_face - h[isl], 0.0, None))), 1),
                "bbox_px": [int(du.min()), int(dv.min()), int(du.max()), int(dv.max())],
                "centroid_px": [round(float(du.mean()), 1), round(float(dv.mean()), 1)],
                "span_mm": [round(1000.0 * float(gu1.max() - gu1.min()), 1),
                            round(1000.0 * float(gu2.max() - gu2.min()), 1)],
                "mouth_m": [round(float(v), 5) for v in point]})
    evidence["modes"] = len(modes)
    evidence["modes_refused_duplicate"] = dups
    evidence["planes_refused_back_facing"] = back_facing
    evidence["max_refine_iters"] = max((p["refine_iters"] for p in evidence["planes"]), default=0)
    evidence["candidates"] = sorted(candidates,
                                    key=lambda c: (-c["ring_wall"], -c["px"]))[:6]
    if not candidates:
        evidence["why"] = (f"{len(evidence['planes'])} wall-sized plane modes were tried in this "
                           f"region and none of them carries a recessed island of "
                           f"{APERTURE_MIN_PX}-{APERTURE_MAX_PX} px that its own wall surrounds"
                           + (f". {back_facing} further planes were the same size and were refused "
                              f"for standing on the far side of the declared normal from this "
                              f"camera, which is a different answer: there is a wall-sized surface "
                              f"in this picture, and it is not one this camera can see the face of"
                              if back_facing else ""))
        return None, evidence
    best = evidence["candidates"][0]
    mouth = np.asarray(best["mouth_m"], dtype=float)
    centre = mouth - face["mouth_offset_world"]
    evidence["chosen"] = best
    evidence["centre_m"] = [round(float(v), 4) for v in centre]
    outline = wall_silhouette(camera, centre, rot=rot, half=face["half_extents"])
    if outline is None:
        evidence["outline_note"] = ("the implied box has a corner behind this camera, so the "
                                    "aperture's answer cannot be compared against the surveyed "
                                    "outline. The aperture itself was measured, not inferred")
    else:
        res = [round(float(outline[i]) - float(bbox[i]), 1) for i in range(4)]
        evidence["outline_of_implied_centre_px"] = [round(float(v), 1) for v in outline]
        evidence["outline_residual_px"] = res
        evidence["max_outline_residual_px"] = round(max(abs(r) for r in res), 1)
    return [float(v) for v in mouth], evidence


# ------------------------------------------------------------------- the rig ----


def _survey_scene_option():
    """MuJoCo's own view options with one field changed: every site group is switched off.

    This is not a styling choice, it is the removal of an answer key. `mujoco.MjvOption`
    renders sites by default (`sitegroup == [1,1,1,0,0,0]`, measured) and this bench's `hole`
    site declares `rgba == [0.0, 0.8, 0.0, 1.0]`, so the survey picture a vision model is
    asked to read contained a bright green disc painted *at the target*: RGB (31,252,31) at
    layout 0's projected mouth and (34,247,34) at layout 11's, against the box face's
    (165,3,3) / (161,0,0) once the marker is off (`/tmp/mw_sites_off.py`). Reading that frame
    would have been §10's forbidden injection delivered through pixels instead of numbers, and
    a channel that scored well on it would have measured the benchmark's plotting choices.

    The depth channel was contaminated by the same object: at the mouth pixel the window depth
    moved 1.978 -> 2.046 m on layout 0 and 1.930 -> 1.996 m on layout 11 when the marker went
    away — a 66-68 mm shift, the same order as the site's own declared size — because the
    marker, floating inside the aperture, was the nearest surface the ray met. With sites off
    the pixel reports what the hole actually shows, which is the beyond-the-wall anomaly the
    mouth estimator is built on.

    Everything else is left exactly as MuJoCo declares it, so this option is a strict
    subtraction: `geomgroup == [1,1,1,0,0,0]` is unchanged, which is also what keeps the
    rendered envelope the *visual* one (`model.geom_group` puts the collision wall blocks in
    group 4, invisible to a player and to this rig alike).

    A third effect was assumed here before it was measured, and the measurement was smaller than
    the assumption. The marker is green at the peg's own hue, so it also falls inside
    `PEG_RGB`'s band and reaches `measured_axis`. Drawing the same twelve frames with the default
    option instead leaves that band 2 px larger (layout 0, `corner2`), 3 px larger (layout 17,
    `corner2`) and exactly the same size in the other ten, moving one fitted end by 4.8 / 3.9 /
    2.1 mm and a fitted length from 0.2356 to 0.2390 m (`/tmp/mw_hue_recheck.py`). The disc is
    therefore a real contaminant of the axis and an irrelevant one — the answer key and the depth
    occlusion above are what it was really costing. It stays switched off.
    """
    import mujoco

    option = mujoco.MjvOption()
    mujoco.mjv_defaultOption(option)
    option.sitegroup[:] = 0
    return option


class MuJoCoRig:
    """One renderer, two conventions: the picture a model can look at, and the depth array
    `geometry.py` can read. Nothing here asks the simulator where anything *is*."""

    def __init__(self, backend: Any, out_dir: str, *, views: Sequence[str] = DEFAULT_VIEWS,
                 width: int = WIDTH, height: int = HEIGHT):
        self.backend = backend
        self.out_dir = out_dir
        self.width, self.height = int(width), int(height)
        self.views = tuple(views)
        self.model = None
        self._rgb = None
        self._depth = None
        self._option = None
        self._bind()
        os.makedirs(out_dir, exist_ok=True)

    def _bind(self) -> None:
        """(Re)build the renderers from the model the backend is running *right now*.

        A `mujoco.Renderer` owns the `MjModel` it was constructed with: it reads geoms,
        materials and the camera table out of that struct on every `update_scene`. MetaWorld's
        task randomisation edits `model.body_pos` — a *model* array, measured: the `box` body
        sits at `[-0.31166, 0.63752, 0.00006]` in layout 0's model and `[-0.28294, 0.46311,
        -0.00074]` in layout 3's, in two different MjModel structs — so every layout of this
        bench is a different model, and `MuJoCoBackend.start()` builds a new one each call. A
        rig that captured `backend.env.model` once and later fed it `backend.env.data` from a
        restarted episode therefore asked one model to draw a scene described by another. It
        does not raise: it renders the *previous* layout's objects and looks entirely plausible.
        That is what `/tmp/mw_depth_layouts.py` was measuring when it reported 35-78 mm survey
        errors on four layouts whose boxes were, on screen, somewhere else.

        Rebinding on model identity is cheap (two renderers per episode, and only when the
        episode actually changed; `env.model` is the same object across steps, measured) and
        makes the stale case impossible instead of merely detectable. What this is *not* is a
        fix for a GL-context problem: `/tmp/mw_depth_modes.py` measured an RGB Renderer and a
        depth Renderer alternating on the process-wide default osmesa context against a single
        toggling Renderer and a depth-only one across five layouts x three repeats, and all
        three returned identical depth (230,400/230,400 finite pixels, the box's silhouette
        centre 126 mm nearer than the eye-to-centre distance, deterministic to the last digit).
        The earlier reading of "only ~12k of 230k pixels carry a surface" as corruption was
        wrong twice over: the count was of *window* depths under 0.9999, which is this
        contract's declared "no surface hit" marker, and a 480x480 frame of this scene
        genuinely holds ~4k pixels of box.
        """
        import mujoco

        model = self.backend.env.model
        if self.model is model and self._rgb is not None:
            return
        self.close()
        self.model = model
        self._rgb = mujoco.Renderer(model, self.height, self.width)
        self._depth = mujoco.Renderer(model, self.height, self.width)
        self._depth.enable_depth_rendering()
        self._option = _survey_scene_option()

    def close(self) -> None:
        """Release the GL buffers. A rig that outlives its episode holds a model alive."""
        for renderer in (self._rgb, self._depth):
            if renderer is not None:
                try:
                    renderer.close()
                except Exception:  # noqa: BLE001 - a leaked buffer is not worth a traceback
                    pass
        self._rgb = self._depth = None
        self._option = None
        self.model = None

    @property
    def data(self):
        return self.backend.env.data

    def camera(self, view: str) -> CameraSpec:
        """`CameraSpec` for one named camera *of this model file*.

        `model.cam_intrinsic` is MuJoCo's placeholder (0.01, 0.01, 0, 0), which is not a
        focal length in pixels; treating `intrinsic[0] > 0` as "the model declared
        intrinsics" is the bug that made an earlier probe project everything to (0, 0). The
        real declaration is `cam_fovy`, and the aperture identity for square pixels with the
        principal point at the centre is what `CameraSpec` already encodes — its `fx` is
        exactly `(height/2)/tan(fov/2)`. `look_at` reproduces `data.cam_xmat` element for
        element (checked by `test_the_declared_camera_is_the_rendered_camera`), which is the
        claim that lets a run directory be re-derived without the simulator.
        """
        import mujoco

        self._bind()
        cid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, view)
        if cid < 0:
            raise PerceptionUnavailable(f"this bench has no camera {view!r}")
        fovy = float(np.asarray(self.model.cam_fovy[cid]).reshape(-1)[0])
        pos = np.asarray(self.data.cam_xpos[cid], dtype=float)
        rot = np.asarray(self.data.cam_xmat[cid], dtype=float).reshape(3, 3)
        forward = -rot[:, 2]
        return CameraSpec(
            camera_id=view,
            eye=_vec(pos), target=_vec(pos + forward), up=_vec(rot[:, 1]),
            fov_deg=fovy, width=self.width, height=self.height,
            near_m=NEAR_M, far_m=FAR_M)

    def render(self, camera: CameraSpec, name: str, *, sim_time: float = 0.0,
               keep_depth: bool = True):
        """(SensorFrame, rgb uint8, depth window-float). Raises when the frame cannot be made.

        The GUI's `backend.capture()` swallows a render failure because a missing thumbnail
        must not end an episode. A *percept* cannot: a look that produced no pixels has no
        reading, and reporting the empty table instead would be the invention this whole
        package is built to refuse.
        """
        import mujoco

        self._bind()
        try:
            self._rgb.update_scene(self.data, camera=camera.camera_id,
                                   scene_option=self._option)
            rgb = np.asarray(self._rgb.render(), dtype=np.uint8)
            self._depth.update_scene(self.data, camera=camera.camera_id,
                                     scene_option=self._option)
            depth = _window_depth(self._depth.render(), camera)
        except Exception as e:  # noqa: BLE001 - an unrenderable frame is a refusal, not a crash
            raise PerceptionUnavailable(
                f"camera {camera.camera_id!r} produced no frame: {type(e).__name__}: {e}") from e
        frame = save_frame(self.out_dir, camera, rgb, depth, name, sim_time=sim_time,
                           keep_depth=keep_depth)
        return frame.model_copy(update={
            "renderer": "mujoco.Renderer(OpenGL/osmesa)",
            "limitations": [
                "software OpenGL with the model's own materials and a headlight: shading and "
                "specular cues are present, which is why a colour word can name a body here",
                "the depth channel is MuJoCo's orthogonal view-axis distance, clipped to this "
                "cell's declared [near_m, far_m] and mapped into the window convention by "
                "`MuJoCoRig.render`; its NaN pixels (no surface hit) are stored as background",
                "the segmentation buffer of the same renderer carries a body id per pixel and "
                "is read by no percept in this package — tests and probes only",
                "no site is drawn: `_survey_scene_option` zeroes every site group, because this "
                "bench's `hole` site renders as a green disc at the target and a frame carrying "
                "it would be an answer key, not a picture",
                "intrinsics are derived from `model.cam_fovy`; `model.cam_intrinsic` is "
                "MuJoCo's placeholder and is not used"],
        }), rgb, depth


def _vec(a) -> Any:
    return Vec3(x=float(a[0]), y=float(a[1]), z=float(a[2]))


def _window_depth(raw, camera: CameraSpec) -> np.ndarray:
    """MuJoCo metres -> the window axis `perception/geometry.py` reads. See the module
    docstring for why the three branches are each other's failure mode.

    Written out elementwise rather than by calling `camera.meters_to_depth`, which is a
    *scalar* function (`float(z_m)` on a 480x480 array raises): this is the same three lines
    of arithmetic, and `work/mw_percept_check.py` asserts they agree with it at sampled
    heights rather than trusting the transcription.
    """
    z = np.asarray(raw, dtype=float)
    metres = np.where(np.isfinite(z), z, camera.far_m)
    metres = np.clip(metres, camera.near_m, camera.far_m)
    near, far = camera.near_m, camera.far_m
    z_ndc = (metres * (far + near) - 2.0 * near * far) / (metres * (far - near))
    window = (z_ndc + 1.0) / 2.0
    return np.where(np.isfinite(z), window, 1.0).astype(np.float32)


# ------------------------------------------------------------------ the catalog --


class MuJoCoCellCatalog(PerceptionCatalog):
    """The same manual, with this bench's sentences.

    A subclass rather than a new field, because `PerceptionCatalog.sha256()` is the digest of
    `model_dump_json()`: adding anything to the base class would move the digest of all 47
    archived desktop cells and make their percepts unreadable to the tracker that stored
    them. What differs here is only what is *said* about a cell that has no trays, no
    `桌面`-height assumption worth trusting, and one fixed box."""

    def prompt_context(self) -> str:
        colours = "、".join(f"{w}" for w in sorted(self.colours))
        shapes = "、".join(f"{w}(半尺寸 {self.shapes[w]} m)" for w in sorted(self.shapes))
        regions = "、".join(f"{r.target_id}(称呼「{r.label}」，中心 x={r.center_xy[0]:.3f} "
                          f"y={r.center_xy[1]:.3f}，半径 {r.inner_half:.3f} m，高 z="
                          f"{r.floor_top_z:.3f} m)" for r in self.regions)
        return (f"这是一个机械臂操作台：水平台面（z≈{self.table_top_z:.3f} m）上放着一个带圆孔的"
                f"固定箱体，还有一根长的绿色杆状物。\n"
                f"color 取值：{colours}（图中机械臂本身是红色的，不要把它当成桌上的物体）。\n"
                f"shape 取值：{shapes}。\n区域（id=称呼）：{regions}。\n"
                f"这个单元格没有托盘：唯一的区域就是那个孔所在的箱体，capacity "
                f"{self.regions[0].capacity or '若干'}。")


def build_catalog(backend: Any, region: RegionDecl) -> MuJoCoCellCatalog:
    """The manual a look is assembled against, with the surveyed hole written into it.

    The wall's own extents are the declared shape `block`; a cell whose survey failed never
    reaches here (`PerceptionUnavailable`), so `regions` is never empty and the
    `PerceptionCatalog` validator that refuses a catalog with nowhere to put anything is
    satisfied by an act of perception rather than by a placeholder.

    Every word in here is one a model has been measured using for one of these two bodies
    (`work/mw_percept_check.py`), and the *reason* the peg gets six of them is the one the
    desktop cell never had to face: `attributes_for_word` returns `{}` for a word outside the
    vocabulary, and a detection with no shape gets no declared half-height, so
    `estimate_centre` cannot place it and the arm is blind to the only object it may move.
    `cylinder` is in here for that reason, with the flattened `[radius, half_h]` pair
    `PEG_DIMS_M` encodes — see the note above that constant.
    """
    decl = wall_declaration(backend)
    shapes = {word: list(PEG_DIMS_M) for word in PEG_SHAPE_WORDS}
    shapes["cylinder"] = list(PEG_CYLINDER_DIMS_M)
    for word in (WALL_SHAPE_WORD, "box"):
        shapes[word] = list(decl["half_extents"])
    return MuJoCoCellCatalog(
        version=f"mw-calib-v1+{decl['hole_radius_m']}",
        colours={PEG_WORD: PEG_RGB, WALL_WORD: WALL_RGB},
        shapes=shapes, regions=[region],
        colour_words={PEG_WORD: "绿色", WALL_WORD: "棕色"},
        shape_words={**{w: "杆" for w in PEG_SHAPE_WORDS}, "cylinder": "圆柱",
                     WALL_SHAPE_WORD: "箱体", "box": "箱体"},
        table_top_z=TABLE_TOP_Z_M, support_z_bounds=SUPPORT_Z_BOUNDS)


def grounding_table() -> GroundingMap:
    """`green` is the one colour word that identifies a body here, and it is the whole map.

    The box keeps its word (`brown block` is a legal detection and is filed as an ungrounded
    body, which is what §11's grounding metric needs the channel to be able to say) because
    the *target* of the task is not a body: it is a region, and regions enter the snapshot
    from the catalog, not from a detection. Two entities could not share a word on this bench
    anyway — the arm is red and red is not declared at all, which is the reason the survey is
    a vision request: `colour_mask` cannot tell a brown box from a brown table, and a model
    that is asked one question about one object can.
    """
    return GroundingMap({PEG_WORD: OBJECT_ID}, source="mujoco cell declaration")


# --------------------------------------------------------------------- survey --


def _request_errors(adapter: Any) -> tuple[type, ...]:
    """What asking this adapter can fail with — found on the adapter's own module.

    The camera channel has no business knowing which provider it is talking to, and an earlier
    revision of `survey` imported one provider's `LLMError` by name. That cost two things: the
    perception path named a model vendor, and the `except` did not match the error class of any
    *other* adapter, so a transport failure on a different endpoint would have escaped as an
    unexplained exception rather than arriving as a `PerceptionUnavailable` with a reason. The
    module a client class is defined in is where its failure type actually lives, whichever module
    that turns out to be. `OSError` is in the tuple for a client that lets a socket error through
    — `urllib.error.HTTPError` is an `OSError` — and the tuple is never empty.
    """
    module = sys.modules.get(type(adapter).__module__)
    declared = getattr(module, "LLMError", None)
    out = [declared] if isinstance(declared, type) and issubclass(declared, BaseException) else []
    out.append(OSError)
    return tuple(out)


class SurveyAnswer(StrictModel):
    """The survey's own shape: a box, or the admission that there is no box.

    `notes` is the key the prompt asks for, spelled as the prompt spells it. A field the
    model was never offered is a validation error under `StrictModel`, and refusing a paid
    request over a spare sentence in an unread field is not what a closed schema is for."""

    found: bool = False
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 0.0
    notes: str = ""


def _refused(message: str, *, reasons: Sequence[str] = (),
             evidence: Optional[dict[str, Any]] = None) -> PerceptionUnavailable:
    """A refused survey, with what it cost attached.

    Every refusal below happens *after* the request was billed, and the survey is one
    question per episode: if the exception carries only a sentence, the artifact records a
    spend with nothing to show for it — no token count, no raw answer, no confidence. The
    answer stays refused (the run still ends here, and nothing is repaired toward validity);
    only its evidence travels."""
    error = PerceptionUnavailable(message, reasons=list(reasons))
    error.evidence = dict(evidence or {})
    return error


def survey(backend: Any, rig: MuJoCoRig, *, adapter: Any, reader_kind: str = "vlm",
           task: Optional[TaskContext] = None,
           on_call: Optional[Callable[[dict], None]] = None) -> tuple[RegionDecl, dict[str, Any]]:
    """One question, one frame, one region. Returns (region, evidence) or raises.

    The question is asked of a picture and the answer is a *place in the picture*: the model is
    only ever asked which box the hole is in, and the metres come from `fit_aperture` on the
    depth image of that box's own region. Nothing in the chain between the two is a second
    opinion about where the target is — the bbox selects pixels, it does not select a position.

    Because it selects pixels, the box is used as the region it selects *inside this frame*: an
    answer whose edges overhang the picture contributes the part that is in it, and the overhang is
    filed as `out_of_frame_px` beside the box as answered. What still refuses is a box with no
    part in the frame, an answer that is not a box, and a frame this process did not really draw
    (`frame_surface_px`).

    `on_call` is the episode's request ledger (`calls_log.file_call` bound to its directory):
    the survey is a billed HTTP request that happens *before* the loop exists, so until #109
    it was the one request on this channel with neither a row nor — before #108 — a ledger
    entry. `None` keeps the offline callers, which make no request, unchanged.

    `reader_kind="segmentation"` is the `wo_vlm` control: the same geometry, driven by the
    declared colour band instead of a model, and it is *expected to be worse* — measured
    above, the band recovers 41% of the box's pixels with a split outline in `corner2` and
    almost nothing in `gripperPOV`. It exists so the effect being reported in the batch is
    "a vision model was consulted", with the number that claim is worth attached.
    """
    decl = wall_declaration(backend)
    camera = rig.camera(SURVEY_VIEW)
    frame, rgb, depth = rig.render(camera, "survey_box", sim_time=backend.sim_time,
                                   keep_depth=True)
    # `_window_depth`'s "no surface hit" marker is a window depth of 1.0, so the count below is
    # the frame's own health: how much of it the renderer actually drew.
    surface_px = int(((np.asarray(depth, dtype=np.float64) > 0.0)
                      & (np.asarray(depth, dtype=np.float64) < 0.999)).sum())
    evidence: dict[str, Any] = {
        "method": f"{reader_kind}_bbox_then_enclosed_depth_anomaly",
        "view": camera.camera_id, "frame_ref": frame.frame_id,
        "image_sha256": frame.image_sha256, "depth_sha256": frame.depth_sha256,
        "prompt_version": "mw-survey-v1",
        "prompt_sha256": survey_prompt_sha256() if reader_kind == "vlm" else None,
        "http_requests_this_call": 0, "counters_this_call": {}, "tokens": {},
        # The instrument's own health, filed before anyone reads what the instrument says.
        "frame_surface_px": surface_px,
        "frame_px": int(camera.width) * int(camera.height)}
    if reader_kind == "vlm":
        from pydantic import ValidationError

        user = SURVEY_USER.format(w=camera.width, h=camera.height)
        # A delta, not the adapter's own number: `http_requests_total` in the metadata is
        # cumulative for the object, and one adapter serves a whole sweep. Measured on the
        # first five-layout batch, whose fifth layout filed `http_requests_this_call: 5`
        # for a survey that asks exactly one question — the four before it were other
        # layouts'. `VLMReader.read` does the same subtraction for the same reason.
        req_before = int(getattr(adapter, "http_requests", 0) or 0)
        opened = {k: int(getattr(adapter, k, 0) or 0) for k in CALLED_COUNTERS}

        def _spent() -> int:
            return max(0, int(getattr(adapter, "http_requests", 0) or 0) - req_before)

        def _this_call() -> dict[str, int]:
            """Every counter this question moved, as deltas — the same dict the row files.

            `survey_prologue` bills these five numbers into the episode ledger, so the two
            accountings of one survey cannot disagree only if they are one measurement.
            """
            return {f"{k}_this_call": int(getattr(adapter, k, 0) or 0) - v
                    for k, v in opened.items()}

        def _row(meta: Optional[dict] = None, *, ok: bool, **extra) -> None:
            """This question's one row in the episode's request ledger, answered or not.

            One row for the logical survey, carrying every request it opened
            (`http_requests_this_call` is 2 when the format repair re-asked the same frame),
            in the shape `calls_log.file_call` gives a decision row — same `ok`, same
            `raw_chars`, same bounded preview for a refused answer. #109: without it the
            first request of the episode was the one billed request with no row behind it.
            """
            if on_call is None:
                return
            on_call({"kind": "survey", "prompt_version": "mw-survey-v1",
                     "provider": getattr(adapter, "provider", "unknown-provider"),
                     "model": getattr(adapter, "model", "unknown-model"),
                     "frame_ref": frame.frame_id, "image_sha256": frame.image_sha256,
                     "modality": frame.modality,
                     **_this_call(),
                     **(meta or {}), **extra, "ok": ok})

        # `chat_vision_json`, not `chat_vision`: the survey is one question per episode and a
        # refused survey ends the episode at step 0, so this was the only VLM call site on the
        # channel with no format repair -- a look has one (`perception/observe.py:304`). The
        # second endpoint produced the defect: a complete answer followed by a stray `}`
        # (`/tmp/mw_sensenova_smoke`: `survey_raw` 109 chars, 2 opens / 3 closes, last byte `}`),
        # which neither `json.loads` nor `_loads_lenient` takes, because the lenient loader slices
        # the first `{` to the last `}` and so keeps the junk inside the slice. Nothing is
        # repaired toward validity here: the same frame is asked a second time and the re-ask is
        # charged as the request it is, which `http_requests_this_call` now files as 2. Zero of the
        # 58 archived episodes died this way (`/tmp/mw117/survey_refusal_census.py`), so no reading
        # already on disk can move; six further asks of that same frame all came back parseable
        # (`/tmp/mw117/sn_repair_n6_saved.out`), so this is a floor under a rare shape rather than
        # a routine second request per episode.
        try:
            parsed, meta = adapter.chat_vision_json(SURVEY_SYSTEM, user, [frame.image_ref],
                                                    kind="survey",
                                                    prompt_version="mw-survey-v1",
                                                    modality=frame.modality)
        except _request_errors(adapter) as e:
            spent = _spent()
            emeta = getattr(e, "meta", None) or {}
            first_err = str(emeta.get("first_parse_error") or "")
            if emeta:
                # A reply that came back has a shape to blame; a request that never returned files
                # no `survey_raw` rather than an empty one, and no `answered_kind` at all.
                evidence["survey_raw"] = str(emeta.get("raw_response") or "")[:400]
                evidence["answered_kind"] = emeta.get("kind")
            evidence["http_requests_this_call"] = spent
            evidence["counters_this_call"] = _this_call()
            # The row is filed before the refusal is raised. A survey that died here is the
            # episode's first billed request and, until #109, the one request that left no row
            # at all: `run_error` said the episode refused, and nothing said whether a reply
            # came back. A request that never reached the endpoint files no preview — there is
            # no answer to bound — while one that came back unparseable files its digest and
            # its bounded text, exactly as a rejected decision row does.
            _row({**emeta, "requests_made": int(getattr(e, "requests_made", 0) or 0)},
                 ok=False, error=f"{type(e).__name__}: {e}",
                 **({"schema_errors": [first_err or str(e)]} if emeta else {}))
            if first_err:
                # Asked and answered twice, and neither answer was readable. That is a different
                # defect from never reaching the endpoint, and the archive's censuses key on the
                # two sentences separately, so they stay two sentences.
                raise _refused(
                    f"the target survey answered in a shape that is not an answer: {e}",
                    reasons=[f"first_parse_error={first_err}",
                             f"http_requests_spent={spent}", str(e)],
                    evidence=evidence) from e
            raise _refused(
                f"the target survey could not be asked: {e}",
                reasons=[f"http_requests_spent={int(getattr(e, 'requests_made', 0) or 0)}",
                         str(e)], evidence=evidence) from e
        evidence["http_requests_this_call"] = _spent()
        evidence["counters_this_call"] = _this_call()
        evidence["tokens"] = dict(meta.get("usage") or {})
        evidence["model_return_id"] = meta.get("completion_id")
        evidence["survey_raw"] = str(meta.get("raw_response") or "")[:400]
        # Which call produced the answer above. `prompt_sha256` is the sha of the survey as it is
        # first asked, and a repaired answer is produced by the shipped repair prompt instead, so
        # the prompt fields cannot tell the two apart and this one has to.
        evidence["answered_kind"] = meta.get("kind")
        # `ok: true` here means *answered*, not accepted (#114): the shape check that follows
        # can still refuse this answer and end the episode, and it does — that refusal is
        # `perception.survey` and `refusal_reasons`, while this row is the request's record.
        _row(meta, ok=True)
        if meta.get("first_parse_error"):
            # The answer that failed is not the answer filed, so the failed one gets named here
            # rather than lost: `survey_raw` above is the reply the channel acted on.
            evidence["survey_format_repair"] = {
                "first_parse_error": str(meta["first_parse_error"])[:200],
                "requests": evidence["http_requests_this_call"]}
        try:
            box = (parsed or {}).get("box") or {}
            said = (parsed or {}).get("notes")
            if isinstance(box, dict) and isinstance(said, str) and said and not box.get("notes"):
                # The prompt asks for `notes` as a SIBLING of `box` (`SURVEY_SYSTEM` line 344), so
                # validating the box alone left the one key the model was offered at its default --
                # /tmp/mw_vlm_camera_batch11_retry filed "" against a reply that carried a sentence.
                # A box-level `notes` still wins because that is the shape `SurveyAnswer` declares,
                # and the text is capped like `survey_raw` above.
                box = {**box, "notes": said[:400]}
            answer = SurveyAnswer.model_validate(box)
        except (ValidationError, TypeError, AttributeError) as e:
            raise _refused(
                f"the target survey answered in a shape that is not an answer: {e}",
                reasons=[str(e)], evidence=evidence) from e
        evidence["bbox"] = [round(float(v), 1) for v in answer.bbox]
        evidence["reported_confidence"] = float(answer.confidence)
        evidence["notes"] = answer.notes
        found = bool(answer.found)
    else:
        rows = colour_bboxes(rgb, {WALL_WORD: WALL_RGB}, min_pixels=AXIS_MIN_PIXELS,
                             hue_tol_deg=WALL_HUE_TOL_DEG,
                             min_saturation=WALL_MIN_SATURATION)
        row = next((r for r in rows if r["color"] == WALL_WORD), None)
        evidence["segmentation"] = row
        found = bool(row)
        evidence["bbox"] = [float(v) for v in (row or {}).get("bbox") or ()]
    if not found:
        raise _refused(
            f"the survey could not find the box the hole is in from {SURVEY_VIEW}",
            reasons=[f"bbox={evidence.get('bbox')} notes={evidence.get('notes') or ''}"],
            evidence=evidence)
    bbox = tuple(float(v) for v in evidence["bbox"])
    if len(bbox) != 4 or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise _refused(f"the survey's box is not a box: {bbox}",
                       reasons=[f"bbox={list(bbox)}", f"raw={evidence.get('survey_raw') or ''}"],
                       evidence=evidence)
    # What the answer is *for* is a region of interest, and the image is the whole domain a
    # region of interest can have: pixels outside the frame select nothing, so the reported box
    # is intersected with the frame and both numbers are filed. This is not a repair of the
    # answer — `bbox` above stays exactly what was said, `out_of_frame_px` says by how far it was
    # outside, and a box wholly outside the picture still refuses the episode below. It is the
    # distinction the first two batches could not make. Six answers were filed by those two
    # batches, since a refused survey used to discard its own evidence; /tmp/mw_survey_hole.py
    # projects the `hole` site into each of the six frames and all six boxes contain it, while
    # five of the six were refused because an edge of the box was outside the picture — by +1 px
    # in one case, +226 px in the worst. And the stage that consumes the box is not sensitive to
    # how big it is: on layout 0 the pilot's near-whole-frame answer [0,0,469,397], the box's own
    # rendered silhouette and the whole frame itself measure the same aperture centre to within
    # 0.45 mm (/tmp/mw_survey_roi_dry.py).
    used = (max(0.0, bbox[0]), max(0.0, bbox[1]),
            min(float(camera.width - 1), bbox[2]), min(float(camera.height - 1), bbox[3]))
    evidence["bbox_used"] = [round(v, 1) for v in used]
    evidence["out_of_frame_px"] = [
        round(v, 1) for v in (max(0.0, -bbox[0]), max(0.0, -bbox[1]),
                              max(0.0, bbox[2] - (camera.width - 1)),
                              max(0.0, bbox[3] - (camera.height - 1)))]
    if used[0] >= used[2] or used[1] >= used[3]:
        raise _refused(
            f"the survey's box is outside this frame: {bbox} against "
            f"{camera.width}x{camera.height}",
            reasons=[f"bbox={list(bbox)}", f"frame={camera.width}x{camera.height}",
                     f"out_of_frame_px={evidence['out_of_frame_px']}",
                     f"reported_confidence={evidence.get('reported_confidence')}",
                     f"notes={evidence.get('notes') or ''}"],
            evidence=evidence)
    # The surveyed box is an address, not a measurement: everything inside it is a candidate plane,
    # and in this scene the frame also holds a floor and a pedestal that meet the declared normal's
    # plane along a line. `region` asks only the pixels whose plane coordinate does not move, which
    # is what "parallel to the declared face" means in a noiseless depth image; the constant carries
    # the sweep, and `fit_aperture`'s docstring carries the two readings it was measured on. The
    # filter's own yield is filed with the answer, because a survey that filtered the wall away and
    # a survey that found no hole say the same thing in `surface_px` and different things in the world.
    flat_region, flat = flat_surface_region(camera, depth, decl=decl)
    evidence["flat_region"] = flat
    mouth, aperture = fit_aperture(camera, depth, used, decl=decl, region=flat_region)
    evidence["aperture"] = aperture
    if mouth is None:
        # The health count travels with the refusal, because the sentence above has two readings
        # and only this number tells them apart: the wall has no hole in these pixels, or these
        # pixels are not a picture of this layout. Both are refusals; only one is about the world.
        dead_frame = surface_px < SURVEY_FRAME_SURFACE_FLOOR
        raise _refused(
            ("this frame is not a picture of this layout: " if dead_frame else
             "the surveyed box has no hole this camera can measure: ")
            + f"{aperture.get('why')} (frame_surface_px={surface_px} of "
            + f"{camera.width}x{camera.height})",
            reasons=["aperture_unmeasured",
                     f"plane_modes_tried={len(aperture.get('planes') or [])}",
                     f"planes_refused_back_facing={aperture.get('planes_refused_back_facing')}",
                     f"region_px={aperture.get('surface_px')}",
                     f"flat_region_px={flat['kept_px']}",
                     f"frame_surface_px={surface_px}",
                     f"frame_surface_floor={SURVEY_FRAME_SURFACE_FLOOR}",
                     "renderer_suspect" if dead_frame else "frame_whole"],
            evidence=evidence)
    centre = [float(v) for v in aperture["centre_m"]]
    region = region_from_wall_centre(decl, centre)
    evidence["wall_centre_m"] = centre
    evidence["mouth_m"] = [round(float(v), 4) for v in mouth]
    evidence["region"] = region.model_dump(mode="json")
    return region, evidence


def survey_prologue(evidence: Optional[dict[str, Any]], *, wall_start: float
                    ) -> dict[str, Any]:
    """The calibration request's own billing, in the units `run_episode(prologue=)` takes.

    #108. The keys are the ledger's, not this file's: `http_requests` is charged against the
    ceiling before the first round is asked (`core/runtime.py:312`) and the five provider
    counters become this episode's billed totals there (`:314`, `Runtime._billed`) — while
    `wall_start` moves the episode's clock back to before the frame was rendered, which is
    the rule `evaluation/run.py` already applies to a shared goal parse. The numbers are the
    deltas `survey()` measured off the shared adapter for the same request's log row, so the
    ledger and that row cannot disagree about what one question cost.

    Empty when the survey opened no request. On the `wo_vlm` control the calibration is
    segmentation, and a prologue of zeros would file a pre-loop spend that never happened and
    move that arm's wall clock for nothing.
    """
    req = int((evidence or {}).get("http_requests_this_call") or 0)
    if not req:
        return {}
    spent = dict((evidence or {}).get("counters_this_call") or {})
    # the survey's own `http_requests_this_call` is the older of the two measurements and is
    # what `perception.survey` on disk already shows, so it wins if the two were ever written
    # by different hands
    spent.setdefault("http_requests_this_call", req)
    out: dict[str, Any] = {"wall_start": float(wall_start)}
    for k in CALLED_COUNTERS:
        out[k] = int(spent.get(f"{k}_this_call") or 0)
    return out


# ------------------------------------------------------------- measured extent --


def measured_axis(camera: CameraSpec, rgb: np.ndarray, depth: np.ndarray, *,
                  colour_rgb, hue_tol_deg: float = PEG_HUE_TOL_DEG,
                  min_saturation: float = PEG_MIN_SATURATION,
                  min_pixels: int = AXIS_MIN_PIXELS,
                  end_fraction: float = 0.02,
                  min_span_m: float = AXIS_MIN_SPAN_M,
                  end_z_bounds: Sequence[float] = AXIS_END_Z_BOUNDS) -> dict[str, Any]:
    """The two ends of a long body, from the pixels that are it.

    `estimate_centre` can only answer "where is the middle of an upright box": it
    intersects the ray through a blob with a *horizontal* plane one declared half-height
    above a measured support. That is the right instrument for a cube on a table and the
    wrong one for this peg, which lies along x, is 0.24 m long, and is grasped 0.13 m from
    the end that goes in. Two things follow, and both are measured rather than argued:

    * the verifier's `placed` question is about the object's **axis**
      (`state.distance_m` uses the point-segment test exactly when two `extent` facts are
      present), so a channel that reports only a centre can never call a fully seated peg
      seated — the point it watches sits outside the hole by half the rod's length;
    * a *carried* peg has no support beside it, so the same estimator puts its z at
      "table height + radius" while the peg is 0.2 m above the table, and
      `MAX_CARRY_OFFSET_M` then refuses every place as "not in the hand".

    So this function reads the body's own outline: a hue band selects the pixels, the
    principal axis of that band in the image gives its heading, and the two robust ends are
    the mean of the world points the end pixels unproject to — each at **its own** measured
    depth, so no plane is assumed anywhere. The result is the same kind of claim
    `env.leading_tip` makes for the privileged channel, arrived at by a caliper-free route.

    Returns `{}` with a `why` when there is no band to fit, or when the band that fitted is not
    a body this bench can contain — see `AXIS_MIN_SPAN_M` and `AXIS_END_Z_BOUNDS` for the two
    tests on that, and for the readings each one refuses. A body that cannot be measured this
    way keeps the upright estimate it had and says so in its facts; nothing here invents an
    axis to make a verdict reachable.
    """
    mask = colour_mask(rgb, colour_rgb, hue_tol_deg=hue_tol_deg,
                       min_saturation=min_saturation)
    ys, xs = np.nonzero(mask)
    n = int(xs.size)
    if n < int(min_pixels):
        return {"ends": [], "pixels": n,
                "why": f"only {n} pixels of this colour band are in the frame, below the "
                       f"{int(min_pixels)} an axis fit needs: the principal axis of a few "
                       f"anti-aliased pixels is a coin flip"}
    ui, vi = xs, ys                                   # integer pixel indices, for `depth`
    u, v = xs.astype(float), ys.astype(float)         # and the same pixels, for the covariance
    cov = np.cov(np.vstack([u - u.mean(), v - v.mean()]))
    _, evecs = np.linalg.eigh(cov)
    direction = evecs[:, -1]                            # image-space major axis
    proj = (u - u.mean()) * direction[0] + (v - v.mean()) * direction[1]
    order = np.argsort(proj)
    k = max(3, int(round(float(end_fraction) * n)))

    def end(index: np.ndarray) -> Optional[dict[str, Any]]:
        pix = depth[vi[index], ui[index]]
        # `< 0.999` is this contract's own "a surface was drawn here" test — the same line
        # `survey` counts frame health with and `fit_aperture` selects its planes with. The
        # window is not linear near its far end, so 0.999 is already ~2.6 m and the rod this
        # fits an axis to has never been nearer to the camera than 0.8 m.
        keep = np.isfinite(pix) & (pix < 0.999)
        if not bool(keep.any()):
            return None
        sel = index[keep]
        # `unproject` answers a `Vec3` and a median wants numbers: a structured point cannot
        # be compared, which `np.median` discovers only with a `TypeError` about `<` between
        # two `Vec3`. So each end pixel is unprojected at **its own** measured depth and
        # flattened here — no plane is assumed anywhere in this function.
        pts = [[(w := camera.unproject(float(a), float(b), float(depth[int(b), int(a)]))).x,
                w.y, w.z] for a, b in zip(ui[sel], vi[sel])]
        return {"point": [round(float(x), 4) for x in np.median(pts, axis=0)],
                "pixels": int(keep.sum()),
                "end_pixels_with_no_surface": int((~keep).sum())}

    # `order[0]` is the pixel with the smallest projection along the band, `order[-1]` the
    # largest; the two `end()` calls therefore return opposite tips of the same rod, in an
    # order that means nothing to the caller — `executor._leading_end` chooses between them
    # by asking which is farther from where it is going.
    near, far = end(order[:k]), end(order[-k:])
    if near is None or far is None:
        return {"ends": [], "pixels": n,
                "why": "the end pixels of this band hit no surface in the depth image"}
    span = [far["point"][i] - near["point"][i] for i in range(3)]
    length = float(sum(s * s for s in span) ** 0.5)
    # A fitted axis is a *claim* about a body, and two of its consequences are read by things
    # that cannot check it: `hold_instrument` calls a rod above both rest surfaces "carried",
    # and the extent restatement below publishes `length_m / 2` as the body's half-length. A
    # depth image that was never drawn — this renderer's second rig in a process, the failure
    # `survey` calls `renderer_suspect` — produces exactly such a claim out of two pixels that
    # unproject into empty space, so the fit is refused rather than interpreted. Both numbers
    # the refusal turns on are calibration (`PEG_ROD_LENGTH_M` from the declared geometry,
    # `AXIS_END_Z_BOUNDS` from the model's own work plane and the highest carried end this cell
    # has measured), and the rejected reading
    # is kept in the row instead of dropped, so a reviewer can see what was refused.
    rejected = {"ends": [], "pixels": n,
                "rejected_length_m": round(length, 4),
                "rejected_end_z_m": [near["point"][2], far["point"][2]]}
    low, high = end_z_bounds
    afloat = [p[2] for p in (near["point"], far["point"]) if not low <= p[2] <= high]
    if afloat:
        far_off = min(min(abs(z - low), abs(z - high)) for z in afloat)
        return {**rejected,
                "why": f"its end pixels unproject to z = "
                       f"{' and '.join(f'{z:.4f}' for z in afloat)} m, outside the band "
                       f"[{low:.3f}, {high:.3f}] m in which a body of this bench can be at all: "
                       f"a {(length * 1000):.0f} mm body cannot be {(far_off * 1000):.0f} mm "
                       f"outside it, so those pixels belong to whatever the camera looks *past* "
                       f"the body rather than to the body"}
    if length < min_span_m:
        return {**rejected,
                "why": f"the band spans {length:.4f} m against the {PEG_ROD_LENGTH_M:.3f} m the "
                       f"declared body is long; nothing this cell has ever rendered healthily "
                       f"fitted less than 0.2339 m, and the floor below which a fit is not "
                       f"believed is {min_span_m:.3f} m. A fifth of the rod is not the rod seen "
                       f"end-on: it is a fragment of a depth image that has no rod in it"}
    unit = [s / length for s in span] if length > 0 else [0.0, 0.0, 0.0]
    return {"ends": [tuple(near["point"]), tuple(far["point"])],
            "center": tuple(round(sum(p[i] for p in (near["point"], far["point"])) / 2.0, 4)
                            for i in range(3)),
            "axis": [round(x, 4) for x in unit], "length_m": round(length, 4),
            "pixels": n, "end_pixels": [near["pixels"], far["pixels"]],
            "band": {"hue_tol_deg": float(hue_tol_deg),
                     "min_saturation": float(min_saturation)},
            "method": "image_pca_of_colour_band_end_pixels_unprojected_at_own_depth",
            "assumes": "the body is the only thing in the frame with this colour band, and "
                       "the end pixels are on its visible surface (a depth camera measures a "
                       "front face, so the reported axis is one radius nearer the camera "
                       "than the rod's centre line)"}


# -------------------------------------------------------------------- arm glue --


#: The largest vertical error this cell's axis instrument has shown on a body that is *resting*.
#: Measured over the 21 resting looks of the 42 archived looks of the real-camera episodes
#: (`/tmp/mw_hold_lift2.py`, the fits over the frames as the model was shown them): a rod lying on
#: the table had its lowest end read between −5.3 mm and +1.4 mm off the table top, which is the
#: one-radius bias `measured_axis` documents (a depth camera measures a front face) plus
#: antialiasing. Doubled and rounded up, so that no hold claim is ever made within one measured
#: bias of a surface a rod could be resting on.
HOLD_REST_MARGIN_M = 0.010


def hold_rest_surfaces(backend: Any) -> list[float]:
    """Every height a rod of this cell can come to rest at, in world metres.

    Two, and only two: the work plane, and the top of the socket box's declared AABB
    (`wall_declaration`'s centre z plus its vertical half-extent — the same model-file read that
    already produces the catalog's `block` extents, taken once per episode). Neither is a query
    about where anything *currently* is: this is calibration, the same kind of number as the
    declared half-heights `PerceptionCatalog` has always carried, and the same kind the desktop
    cell reads off its tray inventory.

    The box top is what makes an affirmative claim possible at all. Without it the only question
    the frame could answer would be "is it still on the table", and a rod a `place` dropped on
    top of the box — 195 mm higher, and the failure mode of the one motion that just failed —
    would answer it as *not held* while the model was told the opposite by the next look. With
    it, a lowest measured end above both surfaces is a rod standing on nothing.
    """
    decl = wall_declaration(backend)
    box_top = float(decl["visual_centre_local"][2]) + float(decl["half_extents"][2])
    return sorted({round(TABLE_TOP_Z_M, 4), round(box_top, 4)})


class MujocoPerceiver(Perceiver):
    """The desktop `Perceiver` with its two renderer seams replaced, and one measurement added.

    `camera_for_view`/`capture_view` are the whole structural difference: the sequence after
    them — reading, assembly, tracking, grounding — is the claim of this package, and it is
    inherited line for line rather than reimplemented for a second benchmark.

    What is added is `measured_axis` on the frame the look already has. That is not an extra
    opinion about the scene: it is the second half of what §5.1 asks a perceiver to report
    (where a body *is* and how far it *reaches*), and on a rod-shaped object the first half
    alone makes three things in this loop unreachable — the axis form of the placement
    verdict, the carry check on a lifted peg, and the leading-end aim of `place`. See
    `measured_axis` for the two measurements behind it.

    The second of those three is `held_for_frame`, and it is the reason this channel has a
    grasp report at all: `MujocoSkillExecutor.held_state()` answers `"unknown"` on the camera
    arm — the aperture ends this bench never measured are §5.8's layer 1 with nothing to say —
    and while `held_object` is unknown `PlanValidator` refuses both `pick` and `place`, which
    is where the first two real-camera episodes ended (one asking for clarification after three
    re-picks, one spending its repair budget on four). Nothing closes that with a simulator
    read. What closes it is the rod's own measured lowest end against the two heights in this
    cell it could be resting on, which is a *restatement of a measurement this channel already
    makes* — see `hold_rest_surfaces` and `HOLD_REST_MARGIN_M` for where its numbers come from.
    """

    def __init__(self, backend: Any, catalog, gmap: GroundingMap, *, rig: MuJoCoRig,
                 out_dir: str, reader=None, arm_channel: str = "vlm",
                 task: Optional[TaskContext] = None, episode_id: str = "",
                 views: Sequence[str] = DEFAULT_VIEWS,
                 default_view: str = SURVEY_VIEW, keep_depth: bool = True,
                 thresholds: Optional[dict] = None,
                 rest_surfaces: Sequence[float] = ()):
        self.rig = rig
        #: the model file's own camera names, so `declared_views`/`alt_views` check against
        # this bench's rig rather than the desktop's three
        scene = MujocoSceneFor(backend)
        #: `PerceptionAssembler` refuses an `alt_views` entry that names no camera, and its
        # desktop default names this cell's `overhead`/`front_high`. The advice has to be
        # followable here too: `_locate`'s "look from somewhere else" can only point at a
        # camera `MuJoCoRig` can actually render.
        merged = {"alt_views": tuple(v for v in rig.views if v != default_view)}
        merged.update(dict(thresholds or {}))
        super().__init__(scene, catalog, gmap, out_dir=out_dir, reader=reader,
                         arm_channel=arm_channel, task=task, episode_id=episode_id,
                         default_view=default_view, views=tuple(views),
                         keep_depth=keep_depth, thresholds=merged,
                         known_views=rig.views)
        #: the pixels the last look was measured from, kept so `look` can fit an axis to
        # the same frame the reading was assembled against — never re-rendered afterwards,
        # because a body may have moved in between
        self._last_pixels: Optional[tuple[CameraSpec, np.ndarray, np.ndarray]] = None
        #: one entry per look, oldest first: what the axis instrument answered and why
        self.axes: list[dict[str, Any]] = []
        #: the heights a rod of this cell could be resting at, as declared calibration. Empty
        # means nobody declared them, and then `held_for_frame` asks its question of nothing:
        # the instrument's claims are only as good as the surfaces it may compare against.
        self.hold_rest_surfaces = tuple(float(s) for s in rest_surfaces)
        #: the axis fit of the frame currently being looked at, and what the hold instrument
        # read out of it. Both are per-capture: `_last_pixels` replaces them on every render.
        self._axis_fit: Optional[dict[str, Any]] = None
        self._hold_row: Optional[dict[str, Any]] = None
        #: one entry per look, oldest first: what the grasp question was answered with
        self.holds: list[dict[str, Any]] = []

    def camera_for_view(self, view: str) -> CameraSpec:
        return self.rig.camera(view)

    def capture_view(self, camera: CameraSpec, name: str):
        frame, rgb, depth = self.rig.render(camera, name, sim_time=self.scene.sim_time,
                                            keep_depth=self.keep_depth)
        self._last_pixels = (camera, rgb, depth)
        self._axis_fit = None
        self._hold_row = None
        return frame, rgb, depth

    def axis_of_current_frame(self) -> dict[str, Any]:
        """`measured_axis` on the frame this look rendered, fitted once and kept.

        The hold instrument runs inside `Perceiver.look`, before the extent restatement that
        has always done the fitting, so without this the same 480x480 band would be solved
        twice per look — and, more to the point, the two answers would be able to disagree
        about a frame they are both supposed to be reading.

        The memo is checked before the refusal, and for that same per-capture reason: a fit is
        kept only by `capture_view` clearing it on every render, so a non-empty `_axis_fit` is
        always a fit to the frame this look is on — even in a process where the test that set it
        never rendered at all.
        """
        if self._axis_fit is not None:
            return self._axis_fit
        if self._last_pixels is None:
            return {"ends": [], "why": "this look rendered no frame to fit an axis to"}
        camera, rgb, depth = self._last_pixels
        self._axis_fit = measured_axis(camera, rgb, depth, colour_rgb=PEG_RGB)
        return self._axis_fit

    def hold_instrument(self, axis: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """The grasp question, asked of the rod's own measured lowest end.

        `axis` is the fit to ask about, and defaults to `axis_of_current_frame()`; passing one
        exists for the tests that probe the bands without a renderer, and is the same dict a
        look files.

        Three outcomes, and the middle one is the common case:

        * **resting** — the lowest measured end is within `HOLD_REST_MARGIN_M` of one of the
          declared rest surfaces, so the rod is lying on something and no hand is holding it:
          `held=None`, which is the contract's own report of an empty gripper;
        * **carried** — it is above *every* surface this cell has, by more than that margin, so
          the rod is standing on nothing: `held=OBJECT_ID`;
        * **silent** — anything between, including a rod in mid-motion, one leaning against the
          box, one part-way into the hole, and every frame whose colour band the arm occludes.
          `held` stays `"unknown"`, which is what this channel said before the instrument
          existed; a refusal costs a decision round and buys nothing false.

        The two claims are geometry read off one frame against one calibration number; the
        silence is the honest remainder. What is *not* here is any opinion about where the
        gripper is, which is the point — the instrument that would settle this question from a
        simulator pose is `MujocoSkillExecutor._held_now`, and it belongs to the control arm.
        """
        axis = self.axis_of_current_frame() if axis is None else axis
        ends = [tuple(e) for e in axis.get("ends") or ()]
        row: dict[str, Any] = {
            "claim": "silent", "held": "unknown", "authority": "actuator", "used": False,
            "margin_m": HOLD_REST_MARGIN_M,
            "rest_surfaces_m": list(self.hold_rest_surfaces),
            "axis_pixels": int(axis.get("pixels") or 0),
            "why": str(axis.get("why") or "")}
        if len(ends) != 2:
            row["why"] = row["why"] or "no two-ended axis was fitted to this frame"
            return row
        if not self.hold_rest_surfaces:
            row["why"] = "no rest surfaces were declared for this cell"
            return row
        z_low = min(float(e[2]) for e in ends)
        row["lowest_end_z_m"] = round(z_low, 4)
        row["ends_m"] = [[round(float(v), 4) for v in e] for e in ends]
        for surface in self.hold_rest_surfaces:
            if abs(z_low - surface) <= HOLD_REST_MARGIN_M:
                row.update(claim="resting", held=None, authority="frame", surface_m=surface,
                           resting_on_m=surface,
                           distance_to_surface_m=round(z_low - surface, 4),
                           sentence=(f"{OBJECT_ID} is resting on the surface at "
                                     f"{surface:.3f} m: its lowest measured end is "
                                     f"{z_low:.4f} m, {abs(z_low - surface) * 1000:.1f} mm off "
                                     f"it, which is inside the {HOLD_REST_MARGIN_M * 1000:.0f} "
                                     f"mm this instrument measures a resting rod to, so nothing "
                                     f"is holding it"))
                return row
        highest = max(self.hold_rest_surfaces)
        if z_low > highest + HOLD_REST_MARGIN_M:
            row.update(claim="carried", held=OBJECT_ID, authority="frame", surface_m=highest,
                       above_surface_m=highest,
                       distance_to_surface_m=round(z_low - highest, 4),
                       sentence=(f"{OBJECT_ID} is carried: its lowest measured end is "
                                 f"{z_low:.4f} m, which is {(z_low - highest) * 1000:.1f} mm "
                                 f"above {highest:.3f} m — the top of the highest surface this "
                                 f"cell has for a rod to rest on — so nothing beneath it is "
                                 f"supporting it"))
            return row
        row["why"] = (f"the lowest measured end is {z_low:.4f} m, which is "
                      f"{z_low - highest:+.4f} m from the highest surface this cell can rest a "
                      f"rod on ({highest:.4f} m) and inside none of them by "
                      f"{HOLD_REST_MARGIN_M:.3f} m: neither resting nor standing on nothing")
        return row

    def held_for_frame(self, held: Optional[str],
                       state: WorldState) -> tuple[Optional[str], str]:
        """`Perceiver.held_for_frame`, answered from the rod's measured position.

        Ordered by what may and may not happen: a hand that *did* report is never overruled
        (§5.8's layer 1 outvotes a guess from below it, and a disagreement between the two is
        a calibration defect to investigate, not a licence); a frame that found no axis, or an
        axis in the neither-nor band, says nothing at all; and when both instruments are
        silent the snapshot keeps `held_object: "unknown"` rather than a word for it. Every
        one of those three leaves `held` exactly as it arrived, and the row filed beside the
        look says which of them it was.
        """
        row = self.hold_instrument()
        if held == "unknown" and row["claim"] != "silent":
            row["used"] = True
        self._hold_row = {**row, "actuator_said": held,
                          "observation_ref": state.observation_ref}
        return (row["held"], "frame") if row["used"] else (held, "actuator")

    def look(self, view: str, *, held: Optional[str] = "unknown") -> WorldState:
        """One frame, and the snapshot the loop may act on: metres, extent, grasp.

        The edits made below are all restatements of what this channel already
        measured, not new information:

        * the extent facts `state.ends_of` reads, so `placed` is asked of the rod's axis;
        * the entity's own pose moved to the midpoint of those ends, because the upright
          estimator's answer for a body in the fingers is "table height plus a radius" —
          a number that is true of the *surface under* the peg and false of the peg. The
          estimate it replaces is kept beside it under `support_estimate_xyz`, so a
          reviewer can see both readings of the same frame and the substitution is
          auditable rather than invisible;
        * the grasp claim `held_for_frame` made before grounding, restated here with the
          three numbers it turned on (`hold_lowest_end_z_m`, `hold_surface_z_m`,
          `hold_margin_m`) beside the sentence it filed, so the snapshot that carries the
          claim also carries the comparison that would refute it.
        """
        state = super().look(view, held=held)
        if self._last_pixels is None:                       # a look that rendered nothing
            return state
        if self._hold_row is not None:
            self.holds.append({**self._hold_row, "view": view})
        axis = self.axis_of_current_frame()
        axis = {**axis, "view": view, "observation_ref": state.observation_ref}
        self.axes.append(axis)
        ends = [tuple(e) for e in axis.get("ends") or ()]
        if len(ends) != 2 or not state.has_entity(OBJECT_ID):
            if ends or axis.get("why"):
                # the refusal is filed either way: an arm that measured nothing and an arm
                # that measured something and dropped it look identical in the metrics
                state = state.model_copy(update={"unparsed": list(state.unparsed) + [
                    f"extent of {OBJECT_ID}: "
                    + str(axis.get("why") or f"{len(ends)} end(s) measured, which is not an "
                                             f"axis")]})
            return state
        (x0, y0, z0), (x1, y1, z1) = ends
        ref = state.observation_ref
        row = self._hold_row or {}
        facts = [
            TextFact(kind="extent", subject=OBJECT_ID, related="end",
                     state=f"({x0:.3f}, {y0:.3f}, {z0:.3f})", observation_ref=ref,
                     text=f"{OBJECT_ID} is measured to reach ({x0:.3f}, {y0:.3f}, {z0:.3f})"),
            TextFact(kind="extent", subject=OBJECT_ID, related="end",
                     state=f"({x1:.3f}, {y1:.3f}, {z1:.3f})", observation_ref=ref,
                     text=f"{OBJECT_ID} is measured to reach ({x1:.3f}, {y1:.3f}, {z1:.3f})"),
            TextFact(kind="extent_axis", subject=OBJECT_ID, related="world",
                     state=f"({axis['center'][0]:.3f}, {axis['center'][1]:.3f}, "
                           f"{axis['center'][2]:.3f})",
                     observation_ref=ref,
                     text=f"{OBJECT_ID} is a rod of measured length {axis['length_m']:.3f} m; "
                          f"its two ends are the two `extent` facts of this snapshot, and the "
                          f"point named here is their midpoint")]
        if row.get("sentence"):
            facts.append(TextFact(kind="hold_measurement", subject=OBJECT_ID,
                                  related=str(row.get("surface_m", "")),
                                  state=str(row.get("claim", "")), observation_ref=ref,
                                  text=row["sentence"]))
        facts = list(state.facts) + facts
        entities = list(state.entities)
        for n, e in enumerate(entities):
            if e.entity_id != OBJECT_ID:
                continue
            mx, my, mz = axis["center"]
            # `box` with `[half_length, radius, radius]`, which is the shape of the claim the
            # privileged channel files for the same body (`state.mw_world_state`): one fewer
            # difference between the two arms, and the tolerance the verifier adds to it reads
            # the peg's radius either way.
            update: dict[str, Any] = {
                "pose": Pose(position=Vec3(x=mx, y=my, z=mz),
                             quaternion_xyzw=(e.pose.quaternion_xyzw if e.pose
                                              else ASSUMED_UPRIGHT),
                             frame_id="world"),
                "attributes": {
                    **e.attributes,
                    "position_source": "measured_axis_midpoint",
                    "measured_length_m": f"{axis['length_m']:.4f}",
                    "support_estimate_xyz": ("" if e.pose is None else
                                             f"({e.pose.position.x:.3f}, "
                                             f"{e.pose.position.y:.3f}, "
                                             f"{e.pose.position.z:.3f})")},
                "geometry": GeometrySpec(shape="box", half_extents=Vec3(
                    x=round(axis["length_m"] / 2.0, 4), y=PEG_RADIUS_M, z=PEG_RADIUS_M))}
            if row.get("used"):
                # the hold instrument's numbers travel with the claim they made, so a reader
                # of the snapshot alone can re-do the comparison; `ground_state` has already
                # set `held=True` and `held_source` for a *carried* claim, and for a resting
                # one `held` is the field this step has to move — an object measured lying on
                # the table is an object the hand is not carrying, which is the answer the
                # `pick` postcondition asks for
                update["attributes"] = {
                    **update["attributes"],
                    "hold_claim": str(row["claim"]),
                    "hold_lowest_end_z_m": f"{row['lowest_end_z_m']:.4f}",
                    "hold_surface_z_m": f"{row['surface_m']:.4f}",
                    "hold_margin_m": f"{HOLD_REST_MARGIN_M:.4f}"}
                if row["claim"] == "resting":
                    update.update({"held": False,
                                   "attributes": {**update["attributes"],
                                                  HELD_SOURCE: "frame"}})
            entities[n] = e.model_copy(update=update)
            break
        return state.model_copy(update={"facts": facts, "entities": entities})


class MujocoSceneFor:
    """The one thing `Perceiver` still asks a scene for: `sim_time`.

    `MujocoScene` is the v0.1 `PhysicsScene` the privileged loop builds, and the perceiver
    is not allowed to reach through it to the simulator — the rig renders, and the timestamp
    is the only number that has to travel with the frame."""

    def __init__(self, backend: Any):
        self.backend = backend

    @property
    def sim_time(self) -> float:
        return float(self.backend.sim_time)


class SnapshotGoals:
    """Where a motion is aimed, read out of the agent's own snapshot.

    The installed alternative to `backend.measured()["sites"]` inside
    `MujocoSkillExecutor`. The split it draws is the one a real robot has: *what to move
    toward* is a perception, and *where the arm currently is* is a joint encoder.
    `MujocoRuntime`'s privileged arm keeps reading simulator sites — that is what makes it
    the control — and this object is what makes the other arm's success or failure depend on
    what it saw.

    It carries a refusal as loudly as a number: `position()` returns None when this
    snapshot has no located body by that name, and the primitive then reports
    `target_lookup` instead of moving. Nothing here guesses, and nothing here reaches past
    the snapshot for the truth.
    """

    def __init__(self, runtime: "MujocoPerceptRuntime"):
        self.runtime = runtime
        self.world: Optional[WorldState] = None

    def adopt(self, world: WorldState) -> None:
        self.world = world

    def position(self, entity_id: str) -> Optional[tuple[float, float, float]]:
        w = self.world
        if w is None:
            return None
        e = w.entity(entity_id) if w.has_entity(entity_id) else None
        if e is not None and e.pose is not None:
            return (round(float(e.pose.position.x), 4), round(float(e.pose.position.y), 4),
                    round(float(e.pose.position.z), 4))
        t = w.target(entity_id)
        if t is not None:
            return (round(float(t.center.x), 4), round(float(t.center.y), 4),
                    round(float(t.center.z), 4))
        return None

    def extent(self, entity_id: str) -> list[tuple[float, float, float]]:
        """The two measured ends of a body, or `[]`. The rod-shaped half of the snapshot.

        `executor._leading_end` asks this before it aims a `place`, because the end of an
        object is the part that has to arrive and the named point of a 0.24 m rod sits
        0.13 m outside the hole even when the rod is fully seated. On this channel the only
        account of where the ends are is the `extent` facts `MujocoPerceiver.look` filed
        from the same frame that gave the position, so an empty list here means this
        snapshot did not measure an axis — which the primitive reports as a refusal rather
        than resolving to a zero-length body.
        """
        if self.world is None:
            return []
        return ends_of(self.world, entity_id)

    def source_name(self) -> str:
        return f"snapshot v{getattr(self.world, 'state_version', '?')} " \
               f"({self.world.source.value if self.world else 'no world yet'})"


class MujocoPerceptVerifier(MujocoVerifier):
    """MuJoCo's geometry, answered by a camera: same arithmetic, different evidence.

    Subclassing the *MuJoCo* verifier rather than wrapping the desktop
    `PerceptVerifier` is the point — the two channels must differ only in what stands behind
    the number, and the number is a distance between a perceived body and a surveyed region
    against a modelled radius. What is added here is the provenance of the answer
    (`Source.sensor`, never `privileged`), the `uncertain` outcome status
    (`SkillStatus.uncertain` on a completed action whose every report is `unknown`), and
    `would_resolve`, which names the camera that could answer instead of pretending this one
    can."""

    def __init__(self, world: WorldState, config: Any = None, *, view: str = "",
                 alt_views: Sequence[str] = ()):
        super().__init__(world, config)
        self.view = view
        self.alt_views = [v for v in alt_views if v and v != view]

    DECLINED = ("orientation_measured", "at_rest", "contact_or_seating_depth",
                "benchmark_score")

    def would_resolve(self, reason: str) -> list[str]:
        return [f"re_observe:{v}" for v in self.alt_views]

    def outcome_status(self, result, reports: list[PredicateReport]):
        if not reports or result is None or result.status != SkillStatus.completed:
            return None
        if all(r.value == PredicateVerdict.unknown for r in reports):
            return SkillStatus.uncertain
        return None

    def _report(self, pid: str, description: str, value: PredicateVerdict, *,
                evidence: Optional[dict[str, float]] = None,
                unmeasured: Optional[list[str]] = None) -> PredicateReport:
        return super()._report(
            pid, self._say(description, value), value, evidence=evidence,
            unmeasured=list(unmeasured or []) + [f for f in self.DECLINED
                                                 if f not in (unmeasured or [])]
        ).model_copy(update={"source": Source.sensor})

    def _say(self, description: str, value: PredicateVerdict) -> str:
        """The refusal in the same sentence as the verdict, because the description is what
        the model reads back and an `unknown` that does not say which camera declined is
        advice nobody can act on."""
        if value != PredicateVerdict.unknown:
            return description
        look = (f" This is a camera answer: {self.view or 'this view'} measured the geometry "
                f"and no frame measures the rest."
                + (f" A further look from {self.alt_views[0]!r} could still resolve the "
                   f"position-dependent half of it." if self.alt_views else ""))
        return description + look

    def verify_goals(self, goal: Any):
        report = super().verify_goals(goal)
        return report.model_copy(update={"source": Source.sensor})


#: what a camera episode can be asked to switch off. The privileged loop's three gates, plus
#: the reading module itself — and the `+ ("vlm",)` is not a widening for convenience.
#: `mw_arm_coherence` runs before `arm_coherence` in this class's `__init__`, and on the
#: privileged channel it is the correct answer: this loop has no instrument for `vlm`, so a
#: `wo_vlm` row would be a `full` row filed under a second word. A camera episode *does* have
#: one (the reader, which is what §9's limited-semantic baseline switches off), so leaving the
#: list as it was refused `--perceive stub --ablation wo_vlm` — the only `wo_vlm` row this
#: bench can produce that still has a camera in it — and did so before the gate whose job is
#: exactly that judgement (`perception/grounding.py:arm_coherence`) was reached.
MW_PERCEPT_ARM_MODULES = MW_ARM_MODULES + ("vlm",)


class MujocoPerceptRuntime(PerceptRuntime, MujocoPlannedRuntime):
    """The MuJoCo loop whose world is a camera, with the v0.2 planning arm on top.

    Two arms, one episode: `PerceptRuntime` moves *where the numbers come from* (a frame
    through `Perceiver.look`) and `PlanningMixin` moves *what is done with them* (a rolling
    plan, a working-memory ledger, §9's gates). Stacking them is the point of §12.1's
    "same loop, different arms" — but the stack cannot be built with `super()`, and that is
    not a style complaint about this file:

    `PerceptRuntime.__init__` takes `(scene, executor, ...)` and `MujocoRuntime.__init__`
    takes `(backend, ...)` and *makes* its own scene and executor from it. A cooperative
    chain would hand the v0.1 `MujocoScene` to the argument named `backend` and build a
    second executor out of it — a loop whose motions and whose snapshot address different
    objects. So `MujocoRuntime.__init__` is called directly, and the state each of the other
    two would have installed is installed here, attribute by attribute, with the same values
    those `__init__`s give. `test_the_stacked_runtime_installs_both_arms` pins the set.

    Nothing in this class chooses an action, and nothing in it reads a simulator pose: the
    two things it does that the privileged loop does not are aim at the snapshot
    (`SnapshotGoals`) and answer predicates from pixels (`MujocoPerceptVerifier`).
    """

    context_class = V02MujocoDecisionContext
    #: `PerceptRuntime._begin_episode` files the richer claim — channel, views and the
    # grounding digest — so the planning arm must not file a second one for the same
    # episode. Same resolution as the desktop `PlannedPerceptRuntime`.
    files_ablation_claim = False
    #: this class's own gate: the planned loop's three plus the reading module. Read off
    # `type(self)` in `__init__`, so the episodic subclass below widens exactly its own
    # answer and a `wo_episodic_memory` row on *this* class is still refused.
    installed_modules = MW_PERCEPT_ARM_MODULES

    def __init__(self, backend, run_dir: str, budgets, episode_id: str, *,
                 perceiver: MujocoPerceiver, gmap: GroundingMap, ablation=None,
                 task_planner: Optional[TaskPlanner] = None,
                 config: Any = None, store: Any = None,
                 frames_dir: Optional[str] = None):
        clash = mw_arm_coherence(ablation, installed=type(self).installed_modules)
        if clash:
            raise ValueError("; ".join(clash))
        MujocoRuntime.__init__(self, backend, run_dir, budgets, episode_id, config=config,
                               store=store, frames_dir=frames_dir)
        # ---- `PerceptRuntime.__init__`'s state, installed rather than inherited ----
        self.perceiver = perceiver
        self.gmap = gmap
        self.ablation = ablation
        self._requested_view: Optional[str] = None
        self._last_view = perceiver.default_view
        self._looks_unbilled = 0
        if ablation is not None:
            bad = arm_coherence(ablation, perceiver.channel)
            if bad:
                raise ValueError(f"arm {ablation.condition!r} cannot run on channel "
                                 f"{perceiver.channel!r}: " + "; ".join(bad))
        # ---- `PlanningMixin.__init__`'s state, and its post-super line ----
        self._task_planner = task_planner
        self.memory_render_budget = RENDER_BUDGET_CHARS
        self.memory_recall_budget = RECALL_BUDGET_CHARS
        derivation = getattr(type(self), "task_planner_derivation", None)
        self.planner_engine = task_planner or TaskPlanner(config=self.config,
                                                          derivation=derivation)
        # ---- the seam the motions read through ----
        self.goals = SnapshotGoals(self)
        self.executor.goal_source = self.goals

    # ---------- both arms own this hook, and neither reaches the other ----------
    def _begin_episode(self) -> None:
        PerceptRuntime._begin_episode(self)
        MujocoPlannedRuntime._begin_episode(self)

    # ---------- the snapshot, and what it now points at ----------
    def _capture_world(self, state_version: int, observation_ref: str) -> WorldState:
        """The camera's answer, then the aim moves to it.

        Adopting happens after the look returns, so a snapshot that raised never becomes the
        one the next motion aims at: the alternative is a `place` whose target vanished
        because a frame could not be parsed, and a refused motion is at least legible.
        """
        state = PerceptRuntime._capture_world(self, state_version, observation_ref)
        self.goals.adopt(state)
        return state

    def _verifier(self, world: WorldState):
        view = self.perceiver.view_of(world.observation_ref) or self._last_view
        return MujocoPerceptVerifier(world, self.config, view=view,
                                     alt_views=[v for v in self.perceiver.views if v != view])

    def _ablation_note(self) -> str:
        return (f"perception channel {self.perceiver.channel} on the MuJoCo benchmark: the "
                f"snapshot is a camera reading grounded by "
                f"{self.gmap.sha256()[:12]}, every motion is aimed at a point that reading "
                f"reported, and the hand's own pose and aperture come from the joint "
                f"encoders. Modules off {list(self.ablation.modules_off) if self.ablation else 'none'}")


class MujocoExperiencedPerceptRuntime(EpisodicMixin, MujocoPerceptRuntime):
    """The camera loop with the §5.4 episodic arm installed, same stack as the desktop
    `ExperiencedPerceptRuntime`: the memory mixin first in the MRO, the camera planned
    loop unchanged under it. No `__init__` of its own beyond the store attributes —
    `MujocoPerceptRuntime.__init__` already runs both coherence gates (this class's own
    reads its widened `installed_modules`, so a `wo_episodic_memory` row is accepted here
    and still refused on the bare camera loop) and installs the planning state the recall
    rides on.

    The channel still decides the reading arm (`arm_for_channel` in the builder), so a
    `wo_episodic_memory` episode on `stub` is §9's limited-semantic baseline *with* the
    memory contrast — the same two experiments under one word the gate refuses to run.
    """

    installed_modules = MW_PERCEPT_ARM_MODULES + ("episodic_memory",)

    def __init__(self, backend, run_dir: str, budgets, episode_id: str, *,
                 perceiver: MujocoPerceiver, gmap: GroundingMap, ablation=None,
                 task_planner: Optional[TaskPlanner] = None,
                 config: Any = None, store: Any = None,
                 frames_dir: Optional[str] = None,
                 experience_store: Optional[ExperienceStore] = None,
                 memory_task_kind: str = "", memory_limit: int = DEFAULT_LIMIT):
        super().__init__(backend, run_dir, budgets, episode_id, perceiver=perceiver,
                         gmap=gmap, ablation=ablation, task_planner=task_planner,
                         config=config, store=store, frames_dir=frames_dir)
        self.experience_store = experience_store
        self.memory_task_kind = memory_task_kind
        self.memory_limit = int(memory_limit)

    # no `_ablation_note` override, matching the desktop `ExperiencedPerceptRuntime`: the
    # claim this stack files is `PerceptRuntime._begin_episode`'s own (channel, views,
    # grounding digest), and the store's facts are carried by the `memory_*` records it
    # governs and by `episode_summary.json`'s `episodic` block, not by the claim.


def build_mujoco_percept_arm(*, backend, run_dir: str, budgets, episode_id: str,
                             store=None, perceive: str = "vlm", adapter=None,
                             ablation=None, task_planner=None,
                             experience_store: Optional[ExperienceStore] = None,
                             memory_task_kind: str = "",
                             frames_dir: Optional[str] = None,
                             out_dir: Optional[str] = None,
                             views: Sequence[str] = DEFAULT_VIEWS,
                             survey_reader_kind: Optional[str] = None,
                             task: Optional[TaskContext] = None
                             ) -> tuple[MujocoPerceptRuntime, dict[str, Any]]:
    """Assemble one camera episode of this bench, and hand back the survey it started with.

    The order is forced and is the reason this is a builder rather than a constructor call in
    the runner: `world_state_from_percept` refuses a percept whose catalog digest differs
    from the tracker's, so an episode can hold exactly one catalog, and the survey has to
    have already happened for that catalog to contain the hole. One calibration, one digest,
    one episode.

    `experience_store` is accepted and passed through for the reason `build_episodic_arm`
    states on the desktop channel: an empty store is a legitimate cold start, a *missing*
    one just means the episodic module is not installed — and then the bare
    `MujocoPerceptRuntime` is built, whose own gate refuses a memory-arm claim. The arm
    that *claims* the module therefore cannot be assembled without something to switch off.

    `(region, evidence)` of the survey is returned rather than filed as an event: the 15 v0.2
    record types are frozen and none of them is "a calibration act", so the evidence goes into
    the run manifest, where a reviewer can see the box the model was asked to find, the pixels
    it named, and the metres those pixels solved to. The alternative — writing a `perception`
    record for it — would put a survey's payload in the same counter as the percepts §11
    counts, and on the `wo_vlm` arm would claim the module it switched off.
    """
    channel_refusal = channel_readiness(perceive, adapter)
    if channel_refusal:
        raise ValueError("; ".join(channel_refusal))
    if perceive == "privileged":
        raise ValueError("a privileged world has no camera to build: `perceive='privileged'` "
                         "means this bench reads simulator sites, which is what "
                         "`MujocoPlannedRuntime` already does. This function is the other arm")
    #: §9's *limited semantic baseline*: a reading that consults no vision model may not
    # file the record the vlm module owns, so the channel decides the arm's name when
    # nobody declared one. `arm_for_channel` is that rule, shared with the desktop arm; a
    # declared arm is passed through untouched.
    arm = arm_for_channel(perceive, ablation)
    frames = os.path.join(run_dir, "perception")
    # One sink, two producers: the calibration question and every look of the episode it
    # built. Both are billed HTTP requests and both land in the episode's
    # `model_calls.jsonl`, the file the batch's spend is read from (`calls_log.file_call`).
    def _on_call(meta: dict[str, Any]) -> None:
        file_call(run_dir, meta)
    rig = MuJoCoRig(backend, out_dir or frames, views=views)
    kind = survey_reader_kind or ("segmentation" if perceive == "stub" else "vlm")
    survey_adapter = None if kind == "segmentation" else _survey_adapter(adapter)
    region, survey_evidence = survey(backend, rig, adapter=survey_adapter, reader_kind=kind,
                                     task=task, on_call=_on_call)
    catalog = build_catalog(backend, region)
    gmap = grounding_table()
    reader = StubReader() if perceive == "stub" else VLMReader(adapter, on_call=_on_call)
    surfaces = hold_rest_surfaces(backend)
    perceiver = MujocoPerceiver(backend, catalog, gmap, rig=rig,
                                out_dir=out_dir or frames, reader=reader,
                                arm_channel=perceive, task=task, episode_id=episode_id,
                                views=views, rest_surfaces=surfaces)
    # the store decides the class, and with it the loop's own coherence answer: with one
    # connected, `wo_episodic_memory` is a module switched off; without one, this builder
    # hands back the bare camera loop, whose gate refuses the claim.
    runtime_cls = MujocoPerceptRuntime
    memory_kw: dict[str, Any] = {}
    if experience_store is not None:
        runtime_cls = MujocoExperiencedPerceptRuntime
        memory_kw = {"experience_store": experience_store,
                     "memory_task_kind": memory_task_kind}
    runtime = runtime_cls(backend, run_dir, budgets, episode_id,
                          perceiver=perceiver, gmap=gmap, ablation=arm,
                          task_planner=task_planner, store=store,
                          frames_dir=frames_dir, **memory_kw)
    return runtime, {"survey": survey_evidence,
                     "catalog_sha256": catalog.sha256(),
                     "grounding_map": gmap.summary(),
                     # the calibration the grasp claim is measured against, filed beside the
                     # calibration the target claim was surveyed from: both are numbers a
                     # report has to be able to quote for its own verdict to be checkable
                     "hold_rest_surfaces_m": surfaces,
                     "hold_margin_m": HOLD_REST_MARGIN_M,
                     "views": list(perceiver.views),
                     "channel": perceive}


def _survey_adapter(adapter):
    """The adapter is used as a *vision* client here, and `chat_vision` is the request.

    Checked rather than assumed: an endpoint configured for text only passes
    `channel_readiness` (which asks whether a model is configured at all, not what it can
    see) and then fails on the first frame, mid-episode, after the decision budget has been
    spent. Naming it here costs nothing and says which capability was missing.
    """
    if not callable(getattr(adapter, "chat_vision", None)):
        raise PerceptionUnavailable(
            f"the survey needs a vision request and {type(adapter).__name__} has no "
            f"`chat_vision`: this endpoint can be asked about text only",
            reasons=["no vision capability"])
    return adapter


MW_PERCEPT_HTTP_REQUESTS = 128


def mw_percept_budgets(**kw):
    """`mw_budgets` with the ceiling a camera actually spends.

    The arithmetic, from this file's own loop: one look per decision round (≤32), two per
    skill call — the world before and the world after (≤2·24), one initial and one terminal
    snapshot, one decision request per round, and one survey per episode. That is ≤123, so
    128 is the smallest power-of-two ceiling that cannot end a well-behaved episode before
    its own skill or round budget does. The privileged arm's declared 40 does the opposite:
    it would stop a camera episode at 40 requests, in the middle of a plan, and the row
    would be a measurement of the budget rather than of the arm (§12.1).
    """
    kw.setdefault("http_requests", MW_PERCEPT_HTTP_REQUESTS)
    return mw_budgets(**kw)


__all__ = ["MuJoCoRig", "MuJoCoCellCatalog", "MujocoPerceiver", "MujocoPerceptVerifier",
           "MujocoPerceptRuntime", "MujocoExperiencedPerceptRuntime", "SnapshotGoals",
           "survey", "survey_prologue",
           "build_catalog",
           "build_mujoco_percept_arm", "grounding_table", "measured_axis",
           "wall_declaration", "region_from_wall_centre", "survey_prompt_sha256",
           "aperture_frame", "fit_aperture", "mw_percept_budgets",
           "MW_PERCEPT_HTTP_REQUESTS", "MW_PERCEPT_ARM_MODULES",
           "hold_rest_surfaces", "HOLD_REST_MARGIN_M",
           "PEG_ROD_LENGTH_M", "AXIS_MIN_SPAN_M", "AXIS_END_Z_BOUNDS",
           "DEFAULT_VIEWS", "SURVEY_VIEW", "TABLE_TOP_Z_M", "SUPPORT_Z_BOUNDS",
           "PEG_SHAPE_WORDS", "PEG_WORD", "WALL_WORD", "WALL_SHAPE_WORD", "NEAR_M", "FAR_M"]
