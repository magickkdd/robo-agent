"""From a frame and a reader to a `PerceptionObservation` (SPEC-v0.2 §5.1).

One boundary runs through this file, and every choice below sits on one side of it:

* a **reader** answers *where in the picture* and *what it is* — the only two things a
  vision model is for. `VLMReader` asks a model; `StubReader` runs a deterministic
  colour segmenter over the same PNG. Both produce the same structure, so the §9
  ablation switch replaces one with the other without anything else noticing;
* the **assembler** answers *where in the cell*, *what is beside it*, *whether the
  tray is free* and *what changed* — computed from the depth image, the declared
  catalogue and the previous percept. No model output reaches a metric number
  directly, and no simulator state reaches either.

A third rule is what makes the whole thing measurable rather than decorative: a
percept that cannot answer one of the six required questions must **say so in
`uncertainties`**, with the reason and what would resolve it. Leaving a group empty is
not an answer — the `PerceptionObservation` contract says so, and
`_mark_empty_groups` is what enforces it here.

Both readers fill the same closed vocabulary the model is *shown*
(`PerceptionCatalog` is the only source of words), so an out-of-vocabulary attribute is
a fact to report, never a thing to guess at.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Callable, Literal, Optional, Union

import numpy as np
from pydantic import Field, ValidationError

from ..core.contracts import Source, StrictModel, Unknown, Vec3
from ..core.runtime import CALLED_COUNTERS
from ..core.v02 import (ChangeRecord, Detection, PerceptionObservation, RegionRecord,
                        SpatialRelation, UncertaintyItem, VisibilityRecord)
from .catalog import PerceptionCatalog
from .frames import VIEWS, SensorFrame, load_depth, load_rgb
from .geometry import best_fitting_shape, estimate_centre, support_surface_z
from .segment import colour_bboxes

PROMPT_VERSION = "s2-perceive-v1"

# The perception prompt, written to be checkable rather than helpful: a closed word
# list, pixel units only, an explicit way to say "I can't tell", and an instruction
# not to volunteer what another module computes better. Anything the model adds
# outside these keys is a validation error, so a plausible-sounding extra field cannot
# become an unstated input to a decision.
PERCEPTION_SYSTEM = """你是桌面整理机器人的视觉感知器。你会看到一张本单元格相机拍摄的图片，
以及这个单元格的静态说明（可用的属性词与区域）和本次任务提到的对象。你只做视觉判断：
图里有什么、在哪里、能不能看见、和谁相邻。不要规划动作，不要编造米制坐标。

只输出一个 JSON 对象，顶层键只能是 objects / absent / regions / notes：
{"objects":[{"ref":"红色方块","color":"red","shape":"cube","bbox":[x0,y0,x1,y1],
             "confidence":0.5,"occluded":false,"on_or_in":"tray_left"}],
 "absent":[{"ref":"绿色圆柱","because":"not_in_image","confidence":0.5,
            "hidden_behind":""}],
 "regions":[{"target_id":"tray_left","occupied":true,"confidence":0.5}],
 "notes":""}

规则：
- bbox 是像素坐标，原点在图片左上角，顺序 [x0,y0,x1,y1]，必须 0<=x0<x1<width、
  0<=y0<y1<height；
- color 与 shape 只能取说明里列出的英文小写词。看不出形状就把 shape 设为 ""，
  认不出颜色就把 color 设为 ""，不要为了填满字段而猜一个词；
- confidence 是你对该项的把握，0 到 1 之间的实数；看不清就给低值，不要一律给 1.0；
- occluded 只有在该物体被别的物体挡住、或部分超出画面边界时才为 true；
- on_or_in 只填说明中的区域 id 或 "table"，判断不了就填 ""；
- absent 只用于「任务提到的对象」在这张图里的情况：
  not_in_image=整张图里没看到；hidden=知道它在但被挡住看不到；
  任务没提到的对象不要写进 absent；
- regions 只写你从图上看出来的「这个区域里有没有东西」。几何归属由别的模块算，
  你说的和它不一致会被记成冲突证据，所以不要迎合；
- 不要输出 entity_id、米制坐标、动作、计划或任何图片里没有的信息。"""

PERCEPTION_USER = """图片如下（记作 image[0]）。请按 system 的结构只输出一个 JSON 对象。

单元格静态说明：
"""


def prompt_sha256() -> str:
    """Digest of the prompt pair this module ships (§7: 提示版本入清单).

    The catalog text is injected per call and is hashed separately
    (`PerceptionCatalog.sha256`), so a calibration edit and a wording edit move
    different digests and can be told apart afterwards."""
    return hashlib.sha256(f"{PROMPT_VERSION}|{PERCEPTION_SYSTEM}|{PERCEPTION_USER}"
                          .encode("utf-8")).hexdigest()


def percept_id(colour: str, shape: str = "") -> str:
    """A detection's identity, from the one attribute that identifies a *body*.

    `seen:red`, and deliberately not `seen:red:cube`. An identity is the thing that
    stays the same while its attributes are re-measured, and shape is the least
    stable thing in a percept: `work/p1_perceive_check.py` measures the silhouette fit
    at 4/5 correct in `main` and 3/5 in `overhead`, so a key that contains the shape
    turns "I looked again and now think it is a cylinder" into one object vanishing and
    another appearing. Measured that way, two of the five bodies changed identity
    between two views of an unmoved table.

    Colour is enough because colours never repeat within a frozen case (verified over
    all 47), and an entity id cannot be used instead: it is a name the *environment*
    chose, and a percept that produced one would be reading it from somewhere the
    contract forbids. The consequence is the useful one — a shape change becomes a
    `ChangeRecord` about one continuing body, which is what §5.1 asks the change
    channel to report.

    A detection with no colour at all keeps a shape-only key, and one with neither is
    `seen:unknown`: both are honest labels for a box the picture could not name, and a
    second of either is reported as `conflicting_evidence`, never merged."""
    if colour:
        return f"seen:{colour}"
    if shape:
        return f"seen:shape:{shape}"
    return "seen:unknown"


def _weaker(a: float, b: float) -> float:
    """A derived claim is never more confident than the weakest thing it rests on."""
    return round(min(float(a), float(b)), 3)


class ReadingError(ValueError):
    """A reader ran and produced something that is not a reading.

    `reasons` carries field-level text the way `DecisionSchemaError` does, so a
    runtime can hand the model its own error next round instead of repairing it here
    (SPEC 6.1: the framework rejects, it does not interpret on the model's behalf)."""

    def __init__(self, message: str, *, reasons: list[str] | None = None,
                 meta: dict | None = None):
        super().__init__(message)
        self.reasons = reasons or [message]
        self.meta = meta or {}


# --------------------------------------------------------------- the reading -----


class ReadObject(StrictModel):
    """One thing a reader says it sees, in pixels and words only.

    `bbox` is required and `confidence` defaults to 0.0: a detection that reports no
    confidence is treated as no confidence at all, not as a confident one. `ref` is
    the reader's own words for the thing, which is what P1-c grounds a referring
    expression against."""

    ref: str = ""
    color: str = ""
    shape: str = ""
    bbox: tuple[float, float, float, float]
    confidence: float = 0.0
    occluded: Union[bool, Unknown] = "unknown"
    hidden_behind: str = ""
    on_or_in: str = ""


class ReadAbsent(StrictModel):
    ref: str = ""
    because: Literal["not_in_image", "hidden", "other"] = "not_in_image"
    hidden_behind: str = ""
    confidence: float = 0.0
    note: str = ""


class ReadRegion(StrictModel):
    """A reader's *semantic* claim about a region, kept apart from the measured one.

    The two are stored side by side on purpose: agreement between a model's glance and
    a depth-derived count is evidence, and disagreement is the more interesting result.
    Either way it must not be possible to tell them apart only by guessing which one a
    merged field came from."""

    target_id: str = ""
    occupied: Union[bool, Unknown] = "unknown"
    confidence: float = 0.0
    note: str = ""


class VlmReading(StrictModel):
    """What a reader said, plus who said it. No metric number is allowed in here."""

    objects: list[ReadObject] = Field(default_factory=list)
    absent: list[ReadAbsent] = Field(default_factory=list)
    regions: list[ReadRegion] = Field(default_factory=list)
    notes: str = ""
    reader: str = ""
    channel: Literal["vlm", "sensor"] = "vlm"
    prompt_version: str = PROMPT_VERSION
    prompt_sha256: str = ""
    catalog_sha256: str = ""
    raw_text: str = ""
    model_return_id: Optional[str] = None
    latency_s: float = 0.0
    tokens: dict[str, int] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class TaskContext(StrictModel):
    """The visual-question half of the task: what a person asked about.

    An agent told "put the red cube in the left tray" can then be asked what it thinks
    about *the green cylinder it cannot see*. Without the mentioned items, `absent` has
    no meaning and §11's unknown-handling metric has nothing to score."""

    utterance: str = ""
    items: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ readers -----


def user_prompt(catalog: PerceptionCatalog, frame: SensorFrame,
                task: Optional[TaskContext] = None) -> str:
    """The text half of a vision request: vocabulary, frame size, mentioned items.

    Deliberately missing: entity ids, positions, the goal assignment, anything about
    what has already been achieved. A percept has to produce those; handing them over
    would make the grounding metric measure copying."""
    t = task or TaskContext()
    payload = {
        "image": "image[0]",
        "width": frame.camera.width,
        "height": frame.camera.height,
        "camera_id": frame.camera.camera_id,
        "modality": frame.modality,
        "mentioned_items": list(t.items),
        "mentioned_regions": list(t.regions),
    }
    return (f"{PERCEPTION_USER}{catalog.prompt_context()}\n\n"
            f"本次参数（JSON）：\n{json.dumps(payload, ensure_ascii=False)}\n")


def _schema_repair_prompt(user: str, raw: str, reasons: list[str]) -> str:
    """The re-ask: same picture, same question, plus the validator's own complaint.

    The allowed values are not spelled out here because the reasons already carry them —
    pydantic writes "Input should be 'not_in_image', 'hidden' or 'other'", and quoting it
    from a second place would let the two drift apart from the schema they describe."""
    return (f"{user}\n\n上一次输出不符合该结构：" + "；".join(reasons) +
            "。请就同一张图片重新只输出一个符合 system 结构的合法 JSON 对象，"
            "只修正上面点名的字段，不要添加解释、Markdown 代码块或注释。"
            f"\n需要修正的输出如下：\n{raw[:4000]}")


#: Every column a reading row of `model_calls.jsonl` carries, answered or not.
#:
#: 任务 `#138` measured the absence: over the 44 camera run roots that file reading rows, the
#: 12 `ok: true` ones had 30 keys and the 4 `ok: false` ones had 18, the same 14 short on every
#: one of them — so the log a batch is *billed from* was ragged exactly where it matters, and
#: `aggregate.py`'s `refused_round_tokens` could only read 0 for a refused look, because a
#: refusal filed no `usage` at all. Applied once, in `_file_row`, to both paths, so no call site
#: can forget it; `None` is the explicit "only an answer can say", never 0 and never "".
#: `raw_sha256`/`raw_preview`/`raw_truncated` are deliberately absent: those are `file_call`'s
#: own policy, keyed on a rejected answer existing at all, not a column this reader decides.
READING_ROW_COLUMNS = dict.fromkeys((
    "ok", "kind", "prompt_version", "provider", "model", "frame_ref", "image_sha256",
    "modality", "images", "requested_model", "returned_model", "completion_id", "created",
    "finish_reason", "usage", "temperature", "max_tokens", "prompt_chars", "raw_chars",
    "latency_s", "http_requests_total", "transport_attempts", "transport_retries",
    "http_requests_this_call", "api_errors_this_call", "transport_retries_this_call",
    "prompt_tokens_this_call", "completion_tokens_this_call", "format_repairs_this_call",
    "schema_repairs", "schema_errors", "error", "requests_made",
    # 任务 `#155`: the three keys that tell "the provider billed a generation and never opened
    # it for reading" apart from "the model answered with nothing". They come from the adapter's
    # `_answer_meta`, so a transport death leaves them null and a content-absent 200 fills them
    # False/False/8571 — which is the reading this log was previously unable to make.
    "content_field_present", "reasoning_field_present", "reasoning_chars",
))


class VLMReader:
    """Ask a vision model about one frame.

    Holds no state between frames and no opinion about what to do next: one question,
    one reading — or one *bounded* re-ask of that same question, described below. It also
    never edits a payload toward validity: every value in the returned reading is a value
    the model sent, and a structure error that survives the re-ask comes back as
    `ReadingError` with the field reasons (SPEC 6.1).

    `schema_repairs=1` is that re-ask, and it is the half of SPEC 6.1 this class used to
    leave unimplemented. `ReadingError`'s own docstring says the field-level reasons exist
    "so a runtime can hand the model its own error next round instead of repairing it
    here" — but on this channel there is no next round to hand it to, because the frame is
    measured once and a refusal there ends the episode. The cost of that reading was
    measured on the first billed camera episode that got past the survey
    (`/tmp/mw_vlm_camera_batch3`, layout 0): one vision request answered
    `absent.0.because = "hidden_behind"`, which is not one of the three values the schema
    allows, and the episode ended at `env_steps=0` with `decisions=0` — the survey had
    already produced a usable region, so what killed it was a *word* in a field that
    nothing in the loop needed. The prompt cannot be fixed for this: `PERCEPTION_SYSTEM`
    is frozen and hashed, and it is also what caused the slip — its example writes
    `"because":"not_in_image"` and `"hidden_behind":""` side by side, and the model put
    the name of the second key into the value of the first. So the framework hands the
    complaint back, once, in the user turn, and asks about the same picture. It chooses
    nothing: no field is dropped, no value is mapped, and the second answer is a fresh
    sentence from the model rather than an edited one. A payload whose *JSON* does not
    parse already gets exactly this treatment one level down, in
    `adapters/deepseek.py:chat_vision_json`, which is the precedent for the bound and for
    charging the re-ask as the request it is."""

    provider = "unknown-provider"
    model = "unknown-model"
    channel = "vlm"

    def __init__(self, adapter, *, prompt_version: str = PROMPT_VERSION,
                 kind: str = "perceive", max_tokens: Optional[int] = None,
                 schema_repairs: int = 1,
                 on_call: Optional[Callable[[dict], None]] = None):
        self.adapter = adapter
        self.model = getattr(adapter, "model", self.model)
        self.provider = getattr(adapter, "provider", self.provider)
        self.prompt_version = prompt_version
        self.kind = kind
        self.max_tokens = max_tokens
        #: re-asks allowed per frame, counted and filed; 0 restores the refuse-on-first-error
        self.schema_repairs = max(0, int(schema_repairs))
        #: a caller that keeps a request ledger (`benchmark_mujoco/calls_log.py`) hands this
        #: reader the sink; `None` keeps a reader that files nothing, which is what every
        #: offline caller and the desktop arm still do
        self.on_call = on_call

    def _file_row(self, frame: SensorFrame, meta: dict, *, ok: bool) -> None:
        """One row per *reading*, on the returning path and the refusing one alike.

        The shape is the decision arm's (`model_policy.MujocoModelPolicy._file_call` writes
        the same keys through the same function), because the artifact this lands in is the
        one a batch is billed from and #102's remaining half was that a non-decision request
        filed nothing at all: on the camera arm a look's tokens were in `model_usage` with no
        row behind them, and a request that died in transport left no trace of having been
        opened. `http_requests_this_call` is a delta off the shared adapter for the same
        reason `survey()` subtracts it — one adapter serves a whole sweep.

        The frame is named by id and digest, never by pixels: `file_call` strips
        `raw_response` down to `raw_chars`, so no base64 and no full answer reaches the log
        except the bounded preview a rejected answer is entitled to."""
        if self.on_call is None:
            return
        self.on_call({**READING_ROW_COLUMNS,
                      "frame_ref": frame.frame_id, "image_sha256": frame.image_sha256,
                      "modality": frame.modality, "provider": self.provider,
                      "model": self.model, **meta, "ok": ok})

    def read(self, frame: SensorFrame, *, catalog: PerceptionCatalog,
             task: Optional[TaskContext] = None) -> VlmReading:
        before = getattr(self.adapter, "http_requests", 0)
        opened = {k: int(getattr(self.adapter, k, 0) or 0) for k in CALLED_COUNTERS}

        def _this_call() -> dict[str, int]:
            """What the requests of this one reading cost, as deltas (see `_file_row`)."""
            return {f"{k}_this_call": int(getattr(self.adapter, k, 0) or 0) - v
                    for k, v in opened.items()}

        user = user_prompt(catalog, frame, task)
        reasons: list[str] = []
        spent: dict[str, int] = {}
        asked_from = ""
        for attempt in range(self.schema_repairs + 1):
            kind = self.kind if attempt == 0 else f"{self.kind}_schema_repair"
            try:
                parsed, meta = self.adapter.chat_vision_json(
                    PERCEPTION_SYSTEM, user, [frame.image_ref],
                    kind=kind,
                    prompt_version=self.prompt_version,
                    max_tokens=self.max_tokens, modality=frame.modality)
            except Exception as e:  # noqa: BLE001 - the look fails, the request still happened
                # The adapter's own retry loop and its one format repair both end here on a
                # dead request, so this is the row that carries them. Nothing is caught: the
                # caller's `PerceptionUnavailable` is still raised one level up.
                #
                # `e.meta` is the request side of 任务 `#138`'s fourteen — what the adapter can
                # say about a call it sent and got no answer from — and `perceive.survey` one
                # file over already spreads it into its refusal row. `kind`/`prompt_version` are
                # dropped from that merge because they name *this* ask: on a format repair the
                # copy riding in `meta` says `_format_repair`, and this reader's own attempt
                # counter is the one that agrees with the `schema_repairs` column.
                emeta = {k: v for k, v in (getattr(e, "meta", None) or {}).items()
                         if k not in ("kind", "prompt_version")}
                self._file_row(frame, {**_this_call(), **emeta,
                                       "kind": kind, "prompt_version": self.prompt_version,
                                       "error": f"{type(e).__name__}: {e}",
                                       "requests_made":
                                           int(getattr(e, "requests_made", 0) or 0),
                                       "schema_repairs": attempt}, ok=False)
                raise
            meta = dict(meta or {})
            raw = str(meta.pop("raw_response", ""))
            # tokens are summed across the attempts of this one question: a reading that
            # cost two requests has to report both, or the batch's spend is understated
            for k, v in (meta.get("usage") or {}).items():
                if isinstance(v, (int, float)):
                    spent[k] = spent.get(k, 0) + int(v)
            try:
                reading = VlmReading.model_validate(parsed or {})
                break
            except ValidationError as e:
                # The offending value goes into the reason because `run_error` in a batch table
                # quotes only this message, and "Input should be 'not_in_image', 'hidden' or
                # 'other'" without the word that arrived cannot tell a reader which of the two
                # things happened: the model invented a word, or the caller shipped the wrong
                # schema. Truncated and repr'd: this is model-produced text. Nothing here is
                # widened to make the reading valid — the rejection stands (SPEC 6.1), and the
                # model gets one chance to say otherwise about the same picture.
                new = []
                for err in e.errors():
                    loc = ".".join(str(p) for p in err["loc"]) or "$"
                    got = repr(err["input"])[:60] if "input" in err else ""
                    new.append(f"{loc}: {err['msg']}" + (f" (got {got})" if got else ""))
                if reasons or attempt >= self.schema_repairs:
                    all_reasons = reasons + new
                    err_meta = {**meta, "raw_response": raw,
                                "usage": spent,
                                "schema_repairs": attempt,
                                "http_requests_this_call":
                                    int(getattr(self.adapter, "http_requests", 0)) - before,
                                **({"repaired_from": asked_from} if attempt else {})}
                    # The row is filed *before* the raise, and with the reasons attached: a
                    # look that died here is the round's whole record of what the model said,
                    # and #102's unbilled half was that it filed nothing. The exception's meta
                    # is the same dict it always carried — `schema_errors` is added for the
                    # row alone, so no consumer of `ReadingError.meta` sees a new key.
                    self._file_row(frame, {**_this_call(), **err_meta,
                                           "schema_errors": all_reasons,
                                           "error": f"vision reading does not match the "
                                                    f"perception schema: {all_reasons[-1]}"},
                                   ok=False)
                    raise ReadingError(
                        f"vision reading does not match the perception schema: "
                        f"{all_reasons[-1]}",
                        reasons=all_reasons,
                        meta=err_meta) from e
                reasons = new
                asked_from = raw
                user = _schema_repair_prompt(user, raw, new)
        meta["schema_repairs"] = int(attempt)
        if attempt:
            meta["usage"] = spent
            meta["first_schema_reasons"] = list(reasons)
            meta["repaired_from"] = asked_from
        meta["http_requests_this_call"] = (
            int(getattr(self.adapter, "http_requests", 0)) - before)
        self._file_row(frame, {**_this_call(), **meta, "raw_response": raw}, ok=True)
        # provenance is stamped, never accepted: a model that volunteered `reader` or
        # `prompt_sha256` in its JSON gets those overwritten with what actually happened
        return reading.model_copy(update={
            "reader": self.model, "channel": "vlm",
            "prompt_version": self.prompt_version, "prompt_sha256": prompt_sha256(),
            "catalog_sha256": catalog.sha256(), "raw_text": raw,
            "model_return_id": meta.get("completion_id"),
            "latency_s": float(meta.get("latency_s") or 0.0),
            "tokens": {k: int(v) for k, v in (meta.get("usage") or {}).items()
                       if isinstance(v, (int, float))},
            "meta": meta})


class StubReader:
    """Deterministic offline reader: colour segmentation plus silhouette fit.

    This is not a stand-in for a camera — it *is* a camera algorithm, run on the same
    PNG a model is shown. Two reasons it exists:

    * §9's `wo_vlm` arm must still perceive, and switching a module off cannot mean
      switching sight off, or the arm measures blindness rather than that module;
    * every offline test needs a reader that costs no money and reproduces to the byte,
      so a change in the assembler's arithmetic can be attributed to the assembler.

    Its limits are the instructive part: it cannot name a shape without measuring the
    surface under it, it says nothing about an object that is not colour-coded, and its
    "confidence" is a silhouette-fit score rather than a probability — which is why a
    stub detection's `how_identified` says `segmented` and never `labelled`."""

    provider = "numpy-segmentation"
    model = "stub-colour-v1"
    channel = "sensor"
    prompt_version = "no-prompt-deterministic"
    # A hue threshold is a hard decision, not a probability: a reader that measures no
    # calibration cannot report one, so absence gets the contract's neutral value
    # instead of an invented 0.9. Silhouette *fit* scores are different — those are a
    # measured fraction of explained pixels, and are reported as what they are.
    absent_confidence = 0.5

    def __init__(self, *, min_pixels: int = 120, hue_tol_deg: float = 22.0):
        self.min_pixels = int(min_pixels)
        self.hue_tol_deg = float(hue_tol_deg)

    def read(self, frame: SensorFrame, *, catalog: PerceptionCatalog,
             task: Optional[TaskContext] = None) -> VlmReading:
        t0 = time.time()
        rgb = load_rgb(frame)
        blobs = colour_bboxes(rgb, catalog.colours, min_pixels=self.min_pixels,
                              hue_tol_deg=self.hue_tol_deg)
        depth = load_depth(frame) if frame.depth_ref else None
        objects: list[ReadObject] = []
        fits: dict[str, dict] = {}
        for blob in blobs:
            bbox = tuple(float(v) for v in blob["bbox"])
            support_z: Optional[float] = None
            fit: dict = {"ranked": []}
            if depth is not None:
                # the same declared-height gate the estimator uses, and applied here for
                # the same measured reason: an unsupported sample moves *both* the shape
                # fit (which is placed on the support) and the position solve
                support_z, _sev = support_surface_z(frame.camera, depth, bbox,
                                                    z_bounds=catalog.support_z_bounds)
                if support_z is not None:
                    fit = best_fitting_shape(frame.camera, depth, bbox,
                                             shapes=catalog.shapes, support_z=support_z)
            shape = str(fit["ranked"][0]) if support_z is not None and fit["ranked"] else ""
            iou = float((fit.get("scores") or {}).get(shape, {}).get("iou", 0.0)) if shape else 0.0
            objects.append(ReadObject(
                ref=catalog.word_for_attributes({"color": blob["color"], "shape": shape}),
                color=blob["color"], shape=shape, bbox=bbox,      # type: ignore[arg-type]
                confidence=round(iou, 3),
                occluded=bool(blob["rows_split"] or blob["touches_frame"]),
                on_or_in=""))
            # the ranking travels with the reading so a shape claim stays auditable: this
            # channel's numbers are measurements, and a reader that reports only its
            # answer leaves a later reader no way to tell a fit from a guess
            fits[percept_id(blob["color"], shape)] = {
                "iou": iou, "margin": fit.get("margin"), "ambiguous": fit.get("ambiguous"),
                "ranked": list(fit.get("ranked") or []), "scores": fit.get("scores") or {},
                "support_z_m": fit.get("support_z_m"), "assumes": fit.get("assumes"),
                "segmentation": {"pixels": blob["pixels"], "fill_ratio": blob["fill_ratio"],
                                 "rows_split": blob["rows_split"],
                                 "touches_frame": list(blob["touches_frame"])}}
        seen_colours = {o.color for o in objects}
        absent: list[ReadAbsent] = []
        for item in (task.items if task else []):
            colour = catalog.attributes_for_word(item).get("color")
            if colour and colour not in seen_colours:
                absent.append(ReadAbsent(ref=item, because="not_in_image",
                                         confidence=self.absent_confidence))
        return VlmReading(
            objects=objects, absent=absent, notes="deterministic colour segmentation",
            reader=self.model, channel="sensor",
            prompt_version=self.prompt_version, prompt_sha256="",
            catalog_sha256=catalog.sha256(),
            raw_text=json.dumps({"segmentation": blobs, "fit": [o.model_dump() for o in objects],
                                 "min_pixels": self.min_pixels,
                                 "hue_tol_deg": self.hue_tol_deg}, ensure_ascii=False),
            latency_s=round(time.time() - t0, 3), tokens={},
            meta={"kind": "perceive_stub", "provider": self.provider, "model": self.model,
                  "view": frame.camera.camera_id, "frame_id": frame.frame_id,
                  "silhouette_fit": fits,
                  "http_requests_this_call": 0, "cost_estimate_usd": None,
                  "image_sha256": frame.image_sha256, "depth_sha256": frame.depth_sha256})


# ---------------------------------------------------------------- the assembler ----


class PerceptionAssembler:
    """Turn one reading of one frame into the six field groups §5.1 asks for.

    Stateless across calls except for the thresholds below, all of them declared
    rather than discovered: a percept's numbers must be attributable to a named
    parameter that a freeze file can record.

    `previous` is the only way the `changes` group can be filled — a change is a
    comparison, and the comparison is against the agent's own previous percept, never
    against a simulator snapshot."""

    def __init__(self, catalog: PerceptionCatalog, *,
                 min_confidence: float = 0.25,
                 min_bbox_px: int = 8,
                 move_threshold_m: float = 0.025,
                 neighbour_gap_m: float = 0.02,
                 alt_views: tuple[str, ...] = ("overhead", "front_high"),
                 region_empty_confidence: float = 0.5,
                 frame_edge_margin_px: int = 2,
                 known_views: Optional[tuple[str, ...]] = None):
        self.catalog = catalog
        self.min_confidence = float(min_confidence)
        self.min_bbox_px = int(min_bbox_px)
        self.move_threshold_m = float(move_threshold_m)
        self.neighbour_gap_m = float(neighbour_gap_m)
        self.region_empty_confidence = float(region_empty_confidence)
        self.frame_edge_margin_px = int(frame_edge_margin_px)
        # `known_views` is the rig of the cell being looked at, which is not always
        # `frames.VIEWS`: a MuJoCo bench has `corner2`/`gripperPOV` in its own model file.
        # The check itself is universal — an `alt_views` entry naming no camera makes every
        # "look from somewhere else" advice in this file unfollowable — so a new cell
        # declares its cameras, it does not skip the declaration.
        allowed = set(VIEWS) if known_views is None else set(known_views)
        undeclared = [v for v in alt_views if v not in allowed]
        if undeclared:
            raise ValueError(f"alt_views must be declared in frames.VIEWS or the caller's "
                             f"known_views; undeclared: "
                             f"{undeclared} (a percept cannot resolve itself by asking "
                             f"for a view that does not exist)")
        self.alt_views = tuple(alt_views)

    def thresholds(self) -> dict[str, Any]:
        return {"min_confidence": self.min_confidence, "min_bbox_px": self.min_bbox_px,
                "move_threshold_m": self.move_threshold_m,
                "neighbour_gap_m": self.neighbour_gap_m,
                "support_z_tol_m": self.catalog.support_z_tol_m,
                "support_z_bounds": list(self.catalog.support_z_bounds),
                "region_empty_confidence": self.region_empty_confidence,
                "frame_edge_margin_px": self.frame_edge_margin_px,
                "alt_views": list(self.alt_views)}

    def _other_views(self, camera_id: str) -> tuple[str, ...]:
        """The declared alt views, minus the one being asked to re-read itself.

        Every `would_resolve` in this file names a viewpoint, so a suggestion that
        returns the current camera is advice nobody can follow — and `overhead` is
        `alt_views[0]`, which is exactly the view whose refusals this module records.
        An empty result falls back to the full list: a cell with one camera genuinely
        has nowhere else to look, and saying so is better than inventing a view."""
        others = tuple(v for v in self.alt_views if v != camera_id)
        return others or self.alt_views

    # ---------- the one entry point ----------
    def assemble(self, frame: SensorFrame, reading: VlmReading, *,
                 previous: Optional[PerceptionObservation] = None,
                 frame_index: int = 0,
                 episode_id: Optional[str] = None) -> PerceptionObservation:
        depth = load_depth(frame) if frame.depth_ref else None
        uncertainties: list[UncertaintyItem] = []
        claims: dict[str, dict[str, Any]] = {}
        for o in reading.objects:
            key = percept_id(o.color.strip().lower(), o.shape.strip().lower())
            claims[key] = {"ref": o.ref, "on_or_in": o.on_or_in, "occluded": o.occluded,
                           "hidden_behind": o.hidden_behind,
                           "bbox_px": [round(float(v), 1) for v in o.bbox]}
        evidence: dict[str, Any] = {
            "reader": {"model": reading.reader, "channel": reading.channel,
                       "prompt_version": reading.prompt_version,
                       "model_return_id": reading.model_return_id,
                       "absent": [a.model_dump() for a in reading.absent]},
            "geometry": {}, "thresholds": self.thresholds(), "relation_claims": claims,
            # what the *shape* claim rests on. The sensor channel measures a silhouette
            # fit and can show its ranking; a vision model's shape word rests on its own
            # judgement, and recording that difference is the point — the two channels
            # must not be able to claim the same kind of evidence.
            "silhouette_fit": dict((reading.meta or {}).get("silhouette_fit") or {})}

        detections, positioned = self._detections(frame, depth, reading, uncertainties,
                                                  evidence)
        relations, relation_of, blocked_by = self._relations(detections, positioned,
                                                            frame, uncertainties)
        visibility = self._visibility(frame, detections, reading, blocked_by, uncertainties)
        regions = self._regions(frame, detections, positioned, reading, uncertainties)
        changes = self._changes(frame, detections, previous, reading.channel, relation_of,
                                blocked_by, uncertainties)
        obs = PerceptionObservation(
            modality=frame.modality,                                    # type: ignore[arg-type]
            frame_index=int(frame_index),
            image_ref=frame.image_ref, depth_ref=frame.depth_ref,
            camera_id=frame.camera.camera_id,
            timestamp_sim=frame.sim_time, timestamp_wall=frame.wall_time,
            detections=detections, relations=relations, visibility=visibility,
            regions=regions, changes=changes, uncertainties=uncertainties,
            raw_text=reading.raw_text, model=reading.reader,
            model_return_id=reading.model_return_id,
            prompt_sha256=reading.prompt_sha256 or prompt_sha256(),
            latency_s=reading.latency_s, tokens=dict(reading.tokens),
            source=Source.sensor, episode_id=episode_id,
            based_on_observation_ref=frame.frame_id,
            provenance={**evidence,
                        "catalog_version": self.catalog.version,
                        "catalog_sha256": reading.catalog_sha256 or self.catalog.sha256(),
                        "previous_observation_id": previous.observation_id if previous else None,
                        "reading_notes": reading.notes,
                        "frame": {"image_sha256": frame.image_sha256,
                                  "depth_sha256": frame.depth_sha256,
                                  "depth_range_m": frame.depth_range_m,
                                  "limitations": list(frame.limitations),
                                  "intrinsics": frame.camera.intrinsics()},
                        "http_requests_this_call": int(
                            (reading.meta or {}).get("http_requests_this_call") or 0),
                        "cost_estimate_usd": (reading.meta or {}).get("cost_estimate_usd")})
        self._mark_empty_groups(obs, frame, previous=previous)
        return obs

    # ---------- 1. objects and attributes ----------
    def _detections(self, frame: SensorFrame, depth, reading: VlmReading,
                    uncertainties: list[UncertaintyItem],
                    evidence: dict[str, Any]) -> tuple[list[Detection], list[Detection]]:
        """Objects, attributes, and the metric position a depth image supports.

        Returns (every detection, the ones that got a position). The second list is
        kept separate because each later group depends on it: a relation, an occupancy
        count and a movement claim all need a place, and a colour the picture cannot
        locate is still a colour the picture saw."""
        camera = frame.camera
        other = self._other_views(camera.camera_id)
        detections: list[Detection] = []
        positioned: list[Detection] = []
        claimed: set[str] = set()
        for row in reading.objects:
            colour = row.color.strip().lower()
            shape = row.shape.strip().lower()
            if colour and colour not in self.catalog.colours:
                uncertainties.append(UncertaintyItem(
                    what=f"a box was reported with color {row.color!r}, which this cell's "
                         f"catalog does not contain",
                    why="conflicting_evidence",
                    would_resolve="a frame the model re-reads using only the declared "
                                  "colour words: an invented word is a reporting failure, "
                                  "not a new kind of object",
                    evidence_refs=[frame.frame_id]))
                continue
            if shape and shape not in self.catalog.shapes:
                uncertainties.append(UncertaintyItem(
                    what=f"a box was reported with shape {row.shape!r}, which this cell's "
                         f"catalog does not contain",
                    why="conflicting_evidence",
                    would_resolve="the colour alone is kept for this box; a second view "
                                  "may name the shape",
                    evidence_refs=[frame.frame_id]))
                shape = ""
            why_box = self._box_problem(row.bbox, camera)
            if why_box:
                uncertainties.append(UncertaintyItem(
                    what=f"a box for {colour or 'unknown'} is not usable: {why_box}",
                    why="conflicting_evidence",
                    would_resolve="a re-read with pixel coordinates in [0, width) x "
                                  "[0, height): this run does not rescale a model's "
                                  "numbers into the frame, because that would be this "
                                  "module inventing the geometry it is supposed to measure",
                    evidence_refs=[frame.frame_id]))
                continue
            eid = percept_id(colour, shape)
            if eid in claimed:
                # colours never repeat within a case, so a duplicate can only be two
                # boxes for one body or a false positive; merging them silently is how
                # a percept starts believing in objects that are not there
                uncertainties.append(UncertaintyItem(
                    what=f"two boxes were reported for {eid}",
                    why="conflicting_evidence",
                    would_resolve="the overlap between the two boxes, or a different "
                                  "viewpoint that separates them",
                    evidence_refs=[frame.frame_id]))
                continue
            claimed.add(eid)
            det = Detection(
                entity_id=eid, kind=shape or "unknown",
                state_attributes={
                    "color": colour or "unknown", "shape": shape or "unknown",
                    "orientation_measured": False,
                    "shape_source": "silhouette_fit" if reading.channel == "sensor"
                    else "reader_word",
                    "bbox_px": [round(float(v), 1) for v in row.bbox],
                    "partly_hidden": row.occluded,
                    "claimed_on_or_in": row.on_or_in.strip()},
                extent=self.catalog.geometry(shape) if shape else None,
                confidence=float(row.confidence), evidence_ref=frame.frame_id,
                how_identified="labelled" if reading.channel == "vlm" else "segmented")
            detections.append(det)
            if row.confidence < self.min_confidence:
                uncertainties.append(UncertaintyItem(
                    what=f"{eid} was reported at confidence {round(float(row.confidence), 3)}, "
                         f"below this run's floor of {self.min_confidence}",
                    why="low_confidence",
                    would_resolve=f"capture from {other[0]!r} and read it again",
                    evidence_refs=[frame.frame_id]))
            fit = (evidence.get("silhouette_fit") or {}).get(eid) or {}
            if fit.get("ambiguous"):
                # the fit ranks two declared shapes within its own noise of each other.
                # Keeping the top answer and flagging it is the useful pair: a plan needs
                # a half-height to work with, and a reviewer needs to know it was a
                # coin flip (`front_high` measures 0.0443 on the cylinder/cuboid pair,
                # `overhead` 0.049 on the cuboid/cylinder pair — both wrong-way risks)
                second = other[1] if len(other) > 1 else other[0]
                ranked = fit.get("ranked") or ["?"]
                rival = ranked[1] if len(ranked) > 1 else "the others"
                uncertainties.append(UncertaintyItem(
                    what=f"{eid}'s silhouette fits {ranked[0]} only {fit.get('margin')} IoU "
                         f"ahead of {rival}",
                    why="low_confidence",
                    would_resolve=f"a view where the two candidates' outlines differ, e.g. "
                                  f"{second!r}",
                    evidence_refs=[frame.frame_id]))
            if self._locate(det, frame, depth, shape, row, uncertainties, evidence):
                positioned.append(det)
        return detections, positioned

    def _box_problem(self, bbox, camera) -> str:
        """Why a reported box cannot be used, or "" when it can.

        Checking the range instead of clipping is the point: a model that answers in
        0-1000 normalised coordinates has described a *different image*, and quietly
        rescaling it would attribute this frame's numbers to a box that was never in
        this frame."""
        x0, y0, x1, y1 = (float(v) for v in bbox)
        if x1 <= x0 or y1 <= y0:
            return f"the box is empty or inverted ({[x0, y0, x1, y1]})"
        if x0 < 0 or y0 < 0 or x1 > camera.width - 1 or y1 > camera.height - 1:
            return (f"it leaves the frame bounds: {[x0, y0, x1, y1]} against "
                    f"{camera.width}x{camera.height}")
        if min(x1 - x0, y1 - y0) < self.min_bbox_px:
            return f"its smallest side is {round(min(x1 - x0, y1 - y0), 1)} px, under the " \
                   f"{self.min_bbox_px} px floor below which a box is an edge artefact"
        return ""

    def _locate(self, det: Detection, frame: SensorFrame, depth, shape: str,
                row: ReadObject, uncertainties: list[UncertaintyItem],
                evidence: dict[str, Any]) -> bool:
        """Back-project one box into a body centre, or record why no number exists."""
        eid, camera = det.entity_id, frame.camera
        other = self._other_views(camera.camera_id)
        if depth is None:
            uncertainties.append(UncertaintyItem(
                what=f"{eid} cannot be located: this frame kept no depth channel",
                why="not_measured", would_resolve="capture with keep_depth=True",
                evidence_refs=[frame.frame_id]))
            return False
        if not shape:
            uncertainties.append(UncertaintyItem(
                what=f"{eid}: no shape, so no declared half-height exists to turn the "
                     f"surface the camera saw into a body centre",
                why="not_measured",
                would_resolve=f"a clearer silhouette, e.g. from {other[0]!r}",
                evidence_refs=[frame.frame_id]))
            return False
        bbox = tuple(float(v) for v in row.bbox)
        margin = self.frame_edge_margin_px
        if (bbox[0] < margin or bbox[1] < margin
                or bbox[2] > camera.width - 1 - margin
                or bbox[3] > camera.height - 1 - margin):
            uncertainties.append(UncertaintyItem(
                what=f"{eid} touches the frame edge, so its contact line may be cut off "
                     f"and one support sample is missing",
                why="not_measured",
                would_resolve=f"a re-centred or wider view: {other[0]!r}",
                evidence_refs=[frame.frame_id]))
        centre, ev = estimate_centre(camera, depth, bbox,
                                     half_height_m=self.catalog.half_height(shape),
                                     z_bounds=self.catalog.support_z_bounds)
        evidence["geometry"][eid] = ev
        if centre is None:
            reason = str(ev.get("rejected") or ev.get("why")
                         or "the depth image did not support an estimate")
            uncertainties.append(UncertaintyItem(
                what=f"{eid}: position not measured ({reason})", why="not_measured",
                would_resolve=self._resolve_for(ev, camera.camera_id),
                evidence_refs=[frame.frame_id]))
            return False
        det.state_attributes["position_xyz_m"] = [round(float(v), 4) for v in
                                                  (centre.x, centre.y, centre.z)]
        det.state_attributes["support_z_m"] = ev.get("support_z_m")
        if ev.get("ambiguous_support"):
            # Two samples of one floor gave two answers, and both are real surfaces: on
            # `dev_c5` the disagreements are 26-44 mm, which is a neighbouring body seen
            # beside the box — its top face (a cube's sits 42 mm above the table, and
            # `seen:red`'s right sample measures 0.6627 m) or its flank. The lower
            # sample is the support — measured to within 0.8 mm of the declared table top
            # on all five bodies — so the answer is kept and its rival is recorded. This
            # is `conflicting_evidence`, not `not_measured`: a number exists, and what is
            # missing is a reason to prefer one of two readings of it.
            uncertainties.append(UncertaintyItem(
                what=f"{eid}'s two support samples disagree by "
                     f"{ev.get('support_sample_spread_m')} m: something else stands beside it",
                why="conflicting_evidence",
                would_resolve=self._resolve_for(ev, camera.camera_id),
                evidence_refs=[frame.frame_id]))
        if row.occluded is True:
            # The estimator's input is the box's *bottom row*, and on an occluded body
            # that row is where the occluder ends, not where the object meets the floor.
            # Measured on `dev_c5` from `main`: `seen:red`, the one body whose lower edge
            # is hidden, lands 13.95 mm off; the three unhidden bodies whose shape was
            # also right land 0.77-2.17 mm off. (The fourth, `seen:blue`, is 12.33 mm off
            # for a different reason — a wrong shape buys a wrong half-height, and its z
            # error is exactly the 7 mm between a cuboid's and a cylinder's.) The number
            # stays — a plan needs it — but it must not read as accurate as those three.
            resolves = " or ".join(repr(v) for v in other[:2])
            uncertainties.append(UncertaintyItem(
                what=f"{eid} is partly hidden, so its box's bottom edge is the occluder's "
                     f"edge and its measured position is biased by up to the hidden height",
                why="low_confidence",
                would_resolve=f"a view from which nothing stands between the camera and "
                              f"{eid}: {resolves}",
                evidence_refs=[frame.frame_id]))
        return True

    def _resolve_for(self, ev: dict, camera_id: str = "") -> str:
        """What would actually fix *this* refusal, in one sentence.

        §5.1's uncertainty marking is only useful if it names a resolution: `unknown`
        without an action is a shrug, and the decision loop cannot act on a shrug.
        Ordered most specific first: the off-table message contains the word
        "background" too, and answering it with "nothing is under its feet" would
        describe the wrong problem. `camera_id` is excluded from the suggestions — a
        refusal recorded *in* `overhead` cannot be resolved by looking in `overhead`
        again, which is the advice this parameter used to give, and the list is joined
        as a set because `alt_views` holds two entries and a cell may only offer one
        other viewpoint."""
        reason = str(ev.get("rejected") or ev.get("why") or "")
        other = self._other_views(camera_id)
        views = " or ".join(repr(v) for v in dict.fromkeys(other))
        first = other[0]
        if "outside every height" in reason or "a height this cell declares" in reason:
            return ("the surface beside this box is not one this cell has: a support "
                    f"sample left the table. Re-measure from {views}"
                    ", which keep a body at this spot inside the table's bounds")
        if "background" in reason:
            return (f"nothing visible under its feet in this view: capture from "
                    f"{first!r}")
        if "depth span" in reason:
            return (f"the box and the measured floor disagree about how far away this is: "
                    f"re-read with a tighter box, or capture from {views}")
        if "declared scene heights" in reason:
            return ("the measured support is outside every height this cell declares: "
                    "check the catalog version, then capture from another view")
        return f"capture from {views} and read it again"

    # ---------- 2. spatial relations ----------
    def _relations(self, detections: list[Detection], positioned: list[Detection],
                   frame: SensorFrame, uncertainties: list[UncertaintyItem]
                   ) -> tuple[list[SpatialRelation], dict[str, tuple[str, str]],
                              dict[str, str]]:
        """Support and neighbour relations, from measured geometry.

        Returns the rows, plus `{entity_id: (predicate, related_id)}` for the change
        diff and `{hidden_id: blocker_id}` for the visibility group. Everything here
        is derived from two numbers the depth image supports — where the body is, and
        how high its floor is — which is what makes a disagreement with a reader's own
        claim (`on_or_in`) worth recording instead of resolving quietly."""
        out: list[SpatialRelation] = []
        relation_of: dict[str, tuple[str, str]] = {}
        tol = self.catalog.support_z_tol_m
        for det in detections:
            pos = det.state_attributes.get("position_xyz_m")
            ref = det.evidence_ref or frame.frame_id
            if pos is None:
                out.append(SpatialRelation(subject_id=det.entity_id, predicate="unknown",
                                           related_id="unknown", confidence=0.0,
                                           evidence_ref=ref))
                relation_of[det.entity_id] = ("unknown", "unknown")
                continue
            x, y = float(pos[0]), float(pos[1])
            support_z = det.state_attributes.get("support_z_m")
            region = self.catalog.region_containing(x, y)
            predicate, related = "unknown", "unknown"
            if support_z is not None:
                if region and abs(float(support_z) - region.floor_top_z) <= tol:
                    predicate, related = "in", region.target_id
                elif abs(float(support_z) - self.catalog.table_top_z) <= tol:
                    predicate, related = "on", "table"
            relation_of[det.entity_id] = (predicate, related)
            out.append(SpatialRelation(
                subject_id=det.entity_id, predicate=predicate,   # type: ignore[arg-type]
                related_id=related,
                confidence=0.0 if predicate == "unknown" else float(det.confidence),
                evidence_ref=ref))
            claim = str(det.state_attributes.get("claimed_on_or_in") or "")
            if predicate == "unknown":
                uncertainties.append(UncertaintyItem(
                    what=f"{det.entity_id} is located but its support is not: cannot say "
                         f"what it rests on",
                    why="not_measured",
                    would_resolve="a frame whose depth reaches under this object",
                    evidence_refs=[ref]))
            elif claim and claim != "table" and claim != related:
                uncertainties.append(UncertaintyItem(
                    what=f"a reader said {det.entity_id} is in {claim!r}; the measured "
                         f"support puts it {predicate} {related!r}",
                    why="conflicting_evidence",
                    would_resolve="a second frame. If it repeats, this reader's region "
                                  "judgement is unreliable from this view and only the "
                                  "geometric one should be quoted to the model",
                    evidence_refs=[ref]))
        for i, a in enumerate(positioned):
            for b in positioned[i + 1:]:
                gap = self._footprint_gap(a, b)
                if gap is None or gap > self.neighbour_gap_m:
                    continue
                out.append(SpatialRelation(
                    subject_id=a.entity_id, predicate="next_to", related_id=b.entity_id,
                    confidence=_weaker(a.confidence, b.confidence),
                    evidence_ref=a.evidence_ref or frame.frame_id))
        blocked_by: dict[str, str] = {}
        for det in detections:
            if det.state_attributes.get("partly_hidden") is not True:
                continue
            blocker = self._blocker_of(det, positioned, frame)
            if blocker is None:
                continue
            blocked_by[det.entity_id] = blocker.entity_id
            out.append(SpatialRelation(
                subject_id=blocker.entity_id, predicate="blocks",
                related_id=det.entity_id,
                confidence=_weaker(blocker.confidence, det.confidence),
                evidence_ref=det.evidence_ref or frame.frame_id))
        return out, relation_of, blocked_by

    def _footprint_gap(self, a: Detection, b: Detection) -> Optional[float]:
        """Clear distance between two bodies' footprints, using the declared sizes.

        This is the yaw-only radius, not `orientation_invariant_footprint_half_xy`'s
        all-orientation bound: a body standing on a corner is wider than the circle used
        here, so a `next_to` can be missed as well as wrongly claimed. That trade is
        deliberate — a missed neighbour costs one extra planned move, and the wider bound
        would buy it at the price of the false `fully_inside` a placement verdict rests on.
        See `core.contracts.orientation_invariant_footprint_half_xy`."""
        pa = a.state_attributes.get("position_xyz_m")
        pb = b.state_attributes.get("position_xyz_m")
        if pa is None or pb is None:
            return None
        dist = math.hypot(float(pa[0]) - float(pb[0]), float(pa[1]) - float(pb[1]))
        return dist - self._radius(a) - self._radius(b)

    @staticmethod
    def _radius(det: Detection) -> float:
        geom = det.extent
        if geom is None:
            return 0.0
        hx, hy = geom.footprint_half_xy_at_zero_yaw
        return float(math.hypot(hx, hy))

    @staticmethod
    def _range_from(camera, xyz) -> float:
        eye = np.array([camera.eye.x, camera.eye.y, camera.eye.z])
        return float(np.linalg.norm(np.asarray(xyz, dtype=float) - eye))

    def _blocker_of(self, hidden: Detection, positioned: list[Detection],
                    frame: SensorFrame) -> Optional[Detection]:
        """The located body that is both overlapping this box and nearer than it.

        "Something is in front" is a pixel fact (`partly_hidden`); *which* body is a
        geometric one. If nothing passes both tests the answer stays unknown rather
        than becoming whichever object happens to be nearby."""
        box = hidden.state_attributes.get("bbox_px")
        pos = hidden.state_attributes.get("position_xyz_m")
        if not box:
            return None
        camera = frame.camera
        behind_range = (self._range_from(camera, pos) if pos is not None else None)
        best: Optional[tuple[float, Detection]] = None
        for other in positioned:
            if other.entity_id == hidden.entity_id:
                continue
            ob = other.state_attributes.get("bbox_px")
            op = other.state_attributes.get("position_xyz_m")
            if not ob or op is None:
                continue
            if min(float(box[2]), float(ob[2])) <= max(float(box[0]), float(ob[0])):
                continue                          # boxes do not overlap horizontally
            r = self._range_from(camera, op)
            if behind_range is not None and r >= behind_range:
                continue                          # not in front after all
            if best is None or r < best[0]:
                best = (r, other)
        return best[1] if best else None

    # ---------- 3. visibility ----------
    def _visibility(self, frame: SensorFrame, detections: list[Detection],
                    reading: VlmReading, blocked_by: dict[str, str],
                    uncertainties: list[UncertaintyItem]) -> list[VisibilityRecord]:
        """Seen, not seen in this frame, and hidden behind something.

        `visible=False` is a claim about *this frame*, made when a reader looked for
        something named in the task and reported its absence. A catalogue colour nobody
        mentioned and no pixel showed produces no row at all: the frame cannot say
        whether such an object exists anywhere, and pretending otherwise would turn a
        blind spot into a finding."""
        view = frame.camera.camera_id
        out = [VisibilityRecord(entity_id=d.entity_id, visible=True, view=view,
                                occluded_by=blocked_by.get(d.entity_id),
                                confidence=float(d.confidence),
                                evidence_ref=d.evidence_ref or frame.frame_id)
               for d in detections]
        reported = {d.entity_id for d in detections}
        for row in reading.absent:
            attributes = self.catalog.attributes_for_word(row.ref)
            if not attributes:
                uncertainties.append(UncertaintyItem(
                    what=f"absence of {row.ref!r} was reported, and that expression does "
                         f"not resolve in this cell's vocabulary",
                    why="conflicting_evidence",
                    would_resolve="a referring expression built from the declared colour "
                                  "and shape words",
                    evidence_refs=[frame.frame_id]))
                continue
            eid = percept_id(attributes.get("color", ""), attributes.get("shape", ""))
            behind = self.catalog.attributes_for_word(row.hidden_behind) if row.hidden_behind else {}
            occluder = percept_id(behind.get("color", ""), behind.get("shape", "")) if behind else None
            out.append(VisibilityRecord(entity_id=eid, visible=False, occluded_by=occluder,
                                        view=view, confidence=float(row.confidence),
                                        evidence_ref=frame.frame_id))
            if row.because == "hidden" and not occluder:
                uncertainties.append(UncertaintyItem(
                    what=f"{eid} is hidden and what hides it was not named",
                    why="not_visible",
                    would_resolve=f"capture from "
                                  f"{self._other_views(view)[0]!r}, which sees the table "
                                  f"from the other side",
                    evidence_refs=[frame.frame_id]))
            elif occluder and occluder not in reported:
                uncertainties.append(UncertaintyItem(
                    what=f"{eid} was reported hidden behind {occluder}, which this frame "
                         f"does not report seeing",
                    why="conflicting_evidence",
                    would_resolve="a second frame naming both objects, or a view from "
                                  "which neither blocks the other",
                    evidence_refs=[frame.frame_id]))
        return out

    # ---------- 4. regions and occupancy ----------
    def _regions(self, frame: SensorFrame, detections: list[Detection],
                 positioned: list[Detection], reading: VlmReading,
                 uncertainties: list[UncertaintyItem]) -> list[RegionRecord]:
        """Which declared region holds what, and whether there is room left.

        `occupants` are counted from measured positions only. A reader's claim to see
        something in a region *without* a measurable position keeps `free` at
        `unknown` — that is the difference between "there is room" and "I did not find
        out", and a plan that puts four objects in a three-slot tray is billed to
        whichever field let it through."""
        out: list[RegionRecord] = []
        claims = {c.target_id: c for c in reading.regions if c.target_id}
        for decl in self.catalog.regions:
            inside = [d.entity_id for d in positioned
                      if decl.contains_xy(*[float(v) for v in
                                            d.state_attributes["position_xyz_m"][:2]])]
            claimed_here = [d.entity_id for d in detections
                            if str(d.state_attributes.get("claimed_on_or_in") or "")
                            == decl.target_id]
            unmeasured = [d.entity_id for d in detections
                          if "position_xyz_m" not in d.state_attributes
                          and str(d.state_attributes.get("claimed_on_or_in") or "")
                          == decl.target_id]
            members = sorted(set(inside) | set(claimed_here))
            note = decl.capacity_note()
            if decl.capacity and len(members) > decl.capacity:
                over = len(members) - decl.capacity
                note += f"; OVER the declared capacity by {over}"
                uncertainties.append(UncertaintyItem(
                    what=f"{decl.target_id} shows {len(members)} objects against a "
                         f"declared capacity of {decl.capacity}: either one detection is "
                         f"false or the region is no longer usable",
                    why="conflicting_evidence",
                    would_resolve="measure each member's footprint against the walls, "
                                  "then re-plan around one of them",
                    evidence_refs=list(members)))
            if unmeasured:
                free: Union[bool, Unknown] = "unknown"
                note += (f"; free is unknown because {unmeasured} were claimed to be here "
                         f"but could not be located")
            elif decl.capacity:
                free = bool(len(members) < decl.capacity)
            else:
                free = "unknown"
                note += "; free is unknown because this cell declares no capacity"
            out.append(RegionRecord(
                region_id=decl.target_id, kind="tray",
                center=Vec3(x=decl.center_xy[0], y=decl.center_xy[1], z=decl.floor_top_z),
                half_xy=Vec3(x=decl.inner_half, y=decl.inner_half, z=0.0),
                free=free, occupants=members, capacity_note=note,
                confidence=self._region_confidence(members, detections, free),
                evidence_ref=frame.frame_id))
            claim = claims.get(decl.target_id)
            if claim is not None and claim.occupied != "unknown":
                if bool(claim.occupied) != bool(members):
                    uncertainties.append(UncertaintyItem(
                        what=f"a reader said {decl.target_id} is "
                             f"{'occupied' if claim.occupied else 'empty'}; the measured "
                             f"occupants are {members or 'none'}",
                        why="conflicting_evidence",
                        would_resolve=f"a closer look at {decl.target_id} from "
                                      f"{self._other_views(frame.camera.camera_id)[0]!r}",
                        evidence_refs=[frame.frame_id, decl.target_id]))
        return out

    def _region_confidence(self, members: list[str], detections: list[Detection],
                           free: Union[bool, Unknown]) -> float:
        if free == "unknown":
            return 0.0
        if not members:
            # absence over a floor nothing contradicts is a weaker claim than a
            # detection: the picture showed no object there, which is all it rests on
            return float(self.region_empty_confidence)
        return min((float(d.confidence) for d in detections if d.entity_id in members),
                   default=0.0)

    # ---------- 5. state changes ----------
    def _changes(self, frame: SensorFrame, detections: list[Detection],
                 previous: Optional[PerceptionObservation], channel: str,
                 relation_of: dict[str, tuple[str, str]],
                 blocked_by: dict[str, str],
                 uncertainties: list[UncertaintyItem]) -> list[ChangeRecord]:
        """Diff against the agent's own previous percept, not against the simulator.

        Every number compared here is an estimate from its own frame, which is why the
        movement threshold sits above the estimator's measured error: `work/
        p1_perceive_check.py` puts the cross-view noise floor at 18.13 mm worst-case
        over 15 body/view pairs (median 10.37 mm) and reports 0 false moves over a
        commanded sweep from 5 to 60 mm, so 25 mm is the smallest of the declared
        thresholds that is not a coin flip (see below).

        It also refuses to compare two *views*. Measured on an unmoved table, comparing
        `main` against `front_high` produced four position changes and two identity
        changes, all of them the camera moving, not the world. A change channel that
        cannot tell those apart reports a disturbance that never happened, which is the
        failure mode §5.1's change detection exists to avoid.

        Only like is compared with like. `relation_of` holds each body's *support*
        relation, so the previous frame's is rebuilt from the same kind of row: a
        `blocks` row has the blocker as its subject, and taking the last non-`next_to`
        row per subject once measured a spurious `blocks:seen:red -> on:table` "change"
        between two renders of the same still scene. Occlusion is still diffed — as its
        own `occluded_by` attribute, keyed by the hidden body, which is the subject a
        replanner cares about.
        """
        if previous is None:
            return []
        if previous.camera_id != frame.camera.camera_id:
            uncertainties.append(UncertaintyItem(
                what=f"no change is reported: this frame is from {frame.camera.camera_id!r} "
                     f"and the previous percept is from {previous.camera_id!r}, so every "
                     f"difference between them is partly the viewpoint",
                why="not_measured",
                would_resolve=f"another frame from {previous.camera_id!r}, or an explicit "
                              f"cross-view check that re-measures both boxes before "
                              f"comparing them",
                evidence_refs=[frame.frame_id, previous.based_on_observation_ref or
                               str(previous.observation_id)]))
            return []
        before = {d.entity_id: d for d in previous.detections}
        # A body has exactly one support row (`unknown` included); pairwise rows are not
        # per-subject claims at all and cannot be compared as if they were.
        before_relation = {r.subject_id: (str(r.predicate), str(r.related_id))
                           for r in previous.relations
                           if r.predicate not in ("next_to", "blocks", "unknown")}
        before_hidden_by = {r.related_id: r.subject_id for r in previous.relations
                            if r.predicate == "blocks"}
        now = {d.entity_id: d for d in detections}
        out: list[ChangeRecord] = []
        for eid, det in now.items():
            old = before.get(eid)
            ref = det.evidence_ref or frame.frame_id
            if old is None:
                out.append(ChangeRecord(subject_id=eid, attribute="presence",
                                        before="not_reported", after="seen",
                                        confidence=_weaker(0.5, det.confidence),
                                        modality=channel, evidence_ref=ref))
                continue
            if str(old.kind) != str(det.kind):
                # the body is the same one; what changed is what the picture now says it
                # *is*. Under a colour-only identity this is a report rather than a pair
                # of appearance/disappearance rows, which is the point of the identity.
                out.append(ChangeRecord(
                    subject_id=eid, attribute="shape", before=str(old.kind),
                    after=str(det.kind),
                    confidence=_weaker(old.confidence, det.confidence),
                    modality=channel, evidence_ref=ref))
            pa = old.state_attributes.get("position_xyz_m")
            pb = det.state_attributes.get("position_xyz_m")
            if pa and pb:
                moved = math.hypot(float(pb[0]) - float(pa[0]), float(pb[1]) - float(pa[1]))
                dz = float(pb[2]) - float(pa[2])
                if moved > self.move_threshold_m or abs(dz) > self.move_threshold_m:
                    out.append(ChangeRecord(subject_id=eid, attribute="position",
                                            before=str([round(float(v), 4) for v in pa]),
                                            after=str([round(float(v), 4) for v in pb]),
                                            confidence=_weaker(old.confidence, det.confidence),
                                            modality=channel, evidence_ref=ref))
                elif abs(moved) > 0.5 * self.move_threshold_m:
                    # under the threshold is not the same as nothing: it is a near miss
                    # that the percept should be able to say it declined to report
                    out.append(ChangeRecord(subject_id=eid, attribute="position_below_threshold",
                                            before=str([round(float(v), 4) for v in pa]),
                                            after=f"moved {round(moved * 1000, 1)} mm, under "
                                                 f"{round(self.move_threshold_m * 1000)} mm",
                                            confidence=_weaker(old.confidence, det.confidence),
                                            modality=channel, evidence_ref=ref))
            ra = before_relation.get(eid)
            rb = relation_of.get(eid)
            if ra and rb and ra != rb and "unknown" not in ra + rb:
                out.append(ChangeRecord(subject_id=eid, attribute="relation",
                                        before=f"{ra[0]}:{ra[1]}", after=f"{rb[0]}:{rb[1]}",
                                        confidence=_weaker(old.confidence, det.confidence),
                                        modality=channel, evidence_ref=ref))
            elif ra and rb and "unknown" in rb and "unknown" not in ra:
                out.append(ChangeRecord(subject_id=eid, attribute="relation",
                                        before=f"{ra[0]}:{ra[1]}", after="unknown",
                                        confidence=0.0, modality=channel, evidence_ref=ref))
            was_hidden_by, is_hidden_by = before_hidden_by.get(eid), blocked_by.get(eid)
            if was_hidden_by != is_hidden_by and (was_hidden_by or is_hidden_by):
                out.append(ChangeRecord(
                    subject_id=eid, attribute="occluded_by",
                    before=was_hidden_by or "not_hidden", after=is_hidden_by or "not_hidden",
                    confidence=_weaker(old.confidence, det.confidence),
                    modality=channel, evidence_ref=ref))
        for eid, old in before.items():
            if eid not in now:
                out.append(ChangeRecord(
                    subject_id=eid, attribute="visibility", before="seen",
                    after="not_reported",
                    confidence=_weaker(old.confidence, 0.5),
                    modality=channel, evidence_ref=frame.frame_id))
        return out

    # ---------- 6. the group that must never be silent ----------
    def _mark_empty_groups(self, obs: PerceptionObservation, frame: SensorFrame, *,
                           previous: Optional[PerceptionObservation]) -> None:
        """An empty group must never be read as "nothing there".

        If one came out empty, the percept says which of the two it means: no evidence
        at all, or evidence that there is nothing to report. That difference is the
        whole content of §11's unknown-handling metric."""
        def add(what: str, why: str, resolve: str) -> None:
            obs.uncertainties.append(UncertaintyItem(
                what=what, why=why, would_resolve=resolve,          # type: ignore[arg-type]
                evidence_refs=[frame.frame_id]))

        if not obs.detections:
            # nothing was reported for a frame that was read: the honest `why` is that
            # this view showed none of it, not "low confidence" about objects that were
            # seen, and not "not measured" about a question nobody answered
            add(f"no object was detected in {frame.frame_id}", "not_visible",
                f"capture from {self._other_views(frame.camera.camera_id)[0]!r}, or check "
                f"that a body is in frame at all")
        if not obs.regions:
            add("no declared region could be described: the catalog and this cell "
                "disagree", "not_measured",
                "rebuild the catalog from the scene declaration")
        if previous is None:
            add("no change is reported because this is the first percept: there is "
                "nothing to compare against yet", "not_measured",
                "assemble the next percept with this one as `previous`")
        elif previous.camera_id != frame.camera.camera_id:
            # `_changes` already said why in its own item; this says that the empty
            # group is a declined comparison, not a finding of no movement
            add("the change group is empty because the two percepts are from different "
                "views: nothing here says the scene did not change", "not_measured",
                f"a frame from {previous.camera_id!r}")
        elif not obs.changes:
            add("no change reported: every object still matches the previous percept "
                "within the declared thresholds", "not_measured",
                "nothing — this is a report that nothing moved, not a missing answer")
