"""Core data contracts (SPEC v0.1 sections 5 and 8).

Schema version 3. Version 2 was the prototype's decision loop; 3 adds what that
loop was missing — `unmeasured` sub-fact names on every predicate answer, the
count of candidates left out of an offer, and a terminal snapshot both judges
share — and drops `Budgets` from `EvalSpec`, which was an information leak rather
than a field. Old schema-2 event logs stay on disk and are never compared against
new scores. Two things are enforced throughout this module:

* Truth separation. `VerifyConfig` carries the *public* predicate parameters the
  agent is allowed to act on; `EvalSpec` carries the *hidden* scoring truth.
  Nothing in the agent decision chain reads an `EvalSpec` (SPEC 4, 7, 12.1.1).
* No silent defaults for unknown facts. `held`, `supported_by` and predicate
  values are three-valued and default to `unknown`, never to the convenient
  answer (SPEC 5.1).

SI units: metres, radians, seconds, kilograms. Quaternions are xyzw. Every pose
carries an explicit `frame_id`. Logical `entity_id` and simulation `body_id` are
separate fields (SPEC 5.1).
"""
from __future__ import annotations

import math
import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional, Union

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "3"

Unknown = Literal["unknown"]


def now_wall() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class StrictModel(BaseModel):
    """Every model-facing structure rejects unexpected fields and unknown enum
    values, so a malformed model output is a validation error, never a silent
    interpretation (SPEC 5 preamble)."""

    model_config = ConfigDict(extra="forbid")


# ---------- geometry ----------


class Vec3(BaseModel):
    x: float
    y: float
    z: float

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z]


class Pose(BaseModel):
    position: Vec3
    quaternion_xyzw: tuple[float, float, float, float]
    frame_id: str = "world"

    def yaw_rad(self) -> float:
        _, _, z, w = self.quaternion_xyzw
        return 2.0 * math.atan2(z, w)


class GeometrySpec(StrictModel):
    """Declared collision geometry in the body frame.

    `footprint_half_xy` is the *horizontal* half-extent pair (hx, hy) that a
    planner must use; it is deliberately not derived from `half_h`, which is
    vertical (SPEC 5.1, 8 placement_planner: use correct horizontal size)."""

    shape: Literal["box", "cylinder"]
    half_extents: Optional[Vec3] = None
    radius: Optional[float] = None
    half_h: Optional[float] = None
    mass_kg: Optional[float] = None

    @model_validator(mode="after")
    def _check_shape_fields(self):
        if self.shape == "box" and self.half_extents is None:
            raise ValueError("box geometry requires half_extents")
        if self.shape == "cylinder" and (self.radius is None or self.half_h is None):
            raise ValueError("cylinder geometry requires radius and half_h")
        return self

    @property
    def half_h_vertical(self) -> float:
        return self.half_h if self.shape == "cylinder" else self.half_extents.z

    @property
    def footprint_half_xy_at_zero_yaw(self) -> tuple[float, float]:
        if self.shape == "cylinder":
            return self.radius, self.radius
        return self.half_extents.x, self.half_extents.y


def oriented_footprint_half_xy(geom: GeometrySpec, yaw_rad: float) -> tuple[float, float]:
    """Axis-aligned horizontal half-extents of an *upright* body after a yaw rotation.

    Cylinders are yaw invariant. Boxes grow towards their diagonal bound as they
    rotate, and the placement and verification code must see that or it will
    accept slots whose true footprint crosses the tray wall (SPEC 8).

    A plan may only claim a yaw, so this is the candidate-generation helper; the
    measured facts of a snapshot must use `footprint_half_xy_from_quat` instead —
    a cylinder released on its side is 2*half_h long, not 2*radius wide.
    """
    if geom.shape == "cylinder":
        r = geom.radius
        return r, r
    hx, hy = geom.half_extents.x, geom.half_extents.y
    c, s = abs(math.cos(yaw_rad)), abs(math.sin(yaw_rad))
    return hx * c + hy * s, hx * s + hy * c


def orientation_invariant_footprint_half_xy(geom: GeometrySpec) -> tuple[float, float]:
    """The square horizontal bound that holds *whatever the body's orientation turns
    out to be* — the only extent a channel that never measures orientation may assert.

    Every corner of a box is further from its centre than any face, so the bound is half
    the space diagonal: taking the two largest half-extents would still be optimistic
    about a body standing on a corner, and the optimistic pair is exactly the mistake
    `vertical_half_extent`'s docstring records. A cylinder's farthest points are its rim,
    at `hypot(radius, half_h)` from the centre, which is the same rule with the third
    term folded in. A bound that can only be wrong in the *safe* direction is what a
    placement verdict needs; `PerceptionAssembler._footprint_gap` deliberately uses the
    narrower yaw-only radius for `next_to`, where a missed neighbour costs one extra
    planned move and a false "fully inside" costs the task.
    """
    if geom.shape == "cylinder":
        r = math.hypot(float(geom.radius), float(geom.half_h))
    else:
        r = math.hypot(*[float(v) for v in geom.half_extents.as_list()])
    return r, r


def rotation_rows_from_quat_xyzw(q) -> tuple[tuple[float, float, float], ...]:
    """Rows of the body->world rotation matrix, without a numpy dependency in the
    contract layer (this module is imported by the log path too)."""
    x, y, z, w = (float(v) for v in q)
    n = x * x + y * y + z * z + w * w
    if n <= 0.0:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    s = math.sqrt(2.0 / n)
    x, y, z, w = x * s, y * s, z * s, w * s
    return (
        (1.0 - (y * y + z * z), x * y - z * w, x * z + y * w),
        (x * y + z * w, 1.0 - (x * x + z * z), y * z - x * w),
        (x * z - y * w, y * z + x * w, 1.0 - (x * x + y * y)),
    )


def vertical_half_extent(geom: GeometrySpec, quaternion_xyzw) -> float:
    """Body half-size along the world z axis in its **measured** orientation: the
    distance from its centre to whatever it rests on.

    Using the declared `half_h` instead silently assumes every object ends
    upright, which made a cylinder lying in a tray read as 19 mm short of its
    seating height and therefore 'not placed' (SPEC 8: geometry truth)."""
    row_z = rotation_rows_from_quat_xyzw(quaternion_xyzw)[2]
    if geom.shape == "cylinder":
        a = abs(row_z[2])
        return geom.half_h * a + geom.radius * math.sqrt(max(0.0, 1.0 - a * a))
    return sum(abs(c) * h for c, h in zip(row_z, geom.half_extents.as_list()))


def footprint_half_xy_from_quat(geom: GeometrySpec,
                                quaternion_xyzw) -> tuple[float, float]:
    """Axis-aligned horizontal half-extents in the measured orientation."""
    rows = rotation_rows_from_quat_xyzw(quaternion_xyzw)
    if geom.shape == "cylinder":
        out = []
        for i in (0, 1):
            a = abs(rows[2][i])
            out.append(geom.half_h * a + geom.radius * math.sqrt(max(0.0, 1.0 - a * a)))
        return out[0], out[1]
    hx, hy = geom.half_extents.x, geom.half_extents.y
    return (sum(abs(rows[0][j]) * h for j, h in enumerate((hx, hy, geom.half_extents.z))),
            sum(abs(rows[1][j]) * h for j, h in enumerate((hx, hy, geom.half_extents.z))))


# ---------- observation / world state ----------


class Source(str, Enum):
    privileged = "privileged"
    sensor = "sensor"
    # a benchmark text backend: what the environment printed to the agent. Never
    # `privileged`, because a sentence says only what this snapshot revealed
    # (SPEC-BST 3.4: observation source relabelled, not upgraded).
    local_text = "local_text"


class TextFact(BaseModel):
    """One fact read out of an observation sentence, with the sentence quoted and
    its character `span` attached, so a parse is checkable without re-running the
    environment. Whatever the parser could not express stays verbatim in
    `WorldState.unparsed` instead of being dropped (SPEC-BST 4: unknown 保真)."""

    fact_id: str = Field(default_factory=lambda: new_id("f"))
    kind: str
    subject: Union[str, None, Unknown] = "unknown"
    related: Union[str, None, Unknown] = "unknown"
    state: Union[str, None, Unknown] = "unknown"
    observation_ref: Optional[str] = None
    span: Optional[tuple[int, int]] = None
    text: str = ""


class SupportEvidence(BaseModel):
    """Where an object rests, derived from measured contact, never assumed.

    `support_id` is a logical id ("table", a target id, another entity id) or
    None when the object is airborne, or "unknown" when contact could not be
    measured this snapshot (SPEC 5.1: no unconditional supported_by=table)."""

    support_id: Union[str, None, Unknown] = "unknown"
    contact_count: int = 0
    source: Source = Source.privileged


class EntityState(BaseModel):
    entity_id: str
    body_id: int = -1
    # None means "this backend does not measure geometry", not "at the origin":
    # a text world names things and never locates them (SPEC-BST 4).
    pose: Optional[Pose] = None
    # An identity quaternion is what a back-projection *assumes*, not what it sees: the
    # centre is solved as `support_z + declared upright half-height`, so a snapshot from
    # pixels has no orientation fact to report. The default keeps every v0.1 privileged
    # snapshot unchanged; a channel that did not measure orientation must set this False,
    # because `footprint_half_xy_from_quat(geom, identity)` returns the upright pair and
    # every geometric check downstream would inherit the assumption as a measurement.
    orientation_measured: bool = True
    geometry: Optional[GeometrySpec] = None
    held: Union[bool, Unknown] = "unknown"
    support: SupportEvidence = Field(default_factory=SupportEvidence)
    supported_by: Union[str, None, Unknown] = "unknown"
    at_rest: Union[bool, Unknown] = "unknown"
    linear_speed_mps: Optional[float] = None
    angular_speed_rps: Optional[float] = None
    visible: bool = True
    confidence: Optional[float] = None
    attributes: dict[str, str] = Field(default_factory=dict)
    source: Source = Source.privileged


class TargetRegion(BaseModel):
    target_id: str
    label: str
    center: Vec3
    inner_half: float
    floor_top_z: float
    wall_top_z: Optional[float] = None
    frame_id: str = "world"


class OccupancyRecord(BaseModel):
    """Which entities are measured to rest inside which target region, with the
    footprint used to decide it. Occupancy is evidence, not a reservation
    (SPEC 5.1: slot reservations are not world facts)."""

    target_id: str
    entity_id: str
    rest_xy: tuple[float, float]
    footprint_half_xy: tuple[float, float]
    fully_inside: bool


class NotSeen(BaseModel):
    """A body this snapshot's frame looked for and did not find, and what the frame can
    say about that (SPEC-v0.2 §5.1: 可见/不可见与遮挡).

    Deliberately *not* an `EntityState`. An entity is something a plan may name, and a
    body the current frame never saw has no measured position, no support and no extent —
    so carrying it as an entity would let an action be validated against an object the
    snapshot only knows the name of. It is carried beside the entities instead, which is
    the difference between "the green one is behind the yellow one" and "there is no green
    one": the first is an answer the model can act on, the second is a claim this frame
    cannot make either way.

    `view` names the camera that did not see it, because not-seen is a property of one
    image and not of the world (SPEC-v0.2 §5.1, and phase log P1-c rule 4)."""

    entity_id: str
    view: str = ""
    occluded_by: Optional[str] = None


class WorldState(BaseModel):
    """One explicitly versioned snapshot of the allowed observation channel."""

    state_version: int
    sim_time: float
    wall_time: float
    entities: list[EntityState]
    targets: list[TargetRegion]
    held_object: Union[str, None, Unknown] = "unknown"
    observation_ref: Optional[str] = None
    occupancy: list[OccupancyRecord] = Field(default_factory=list)
    # What the frame looked for and did not find. Empty means the frame saw everything it
    # was looking for — a privileged snapshot has no such rows, because it inventories the
    # world rather than looking at it.
    not_seen: list[NotSeen] = Field(default_factory=list)
    source: Source = Source.privileged
    schema_version: str = SCHEMA_VERSION
    # ---------- text-backend channel (SPEC-BST 4): what was printed, and what of
    # it could not be read. Both default to "nothing to show", never to a claim.
    raw_observation: Optional[str] = None
    unparsed: list[str] = Field(default_factory=list)
    facts: list[TextFact] = Field(default_factory=list)
    location: Union[str, None, Unknown] = "unknown"
    # SPEC-BST 4.4: the sentences that were not read stay verbatim in `unparsed`;
    # this word is what a report counts, and `partial` is never written as `parsed`.
    parse_status: Literal["parsed", "partial", "unparsed"] = "parsed"

    def entity(self, entity_id: str) -> EntityState:
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        raise KeyError(entity_id)

    def has_entity(self, entity_id: str) -> bool:
        return any(e.entity_id == entity_id for e in self.entities)

    def target(self, target_id: str) -> Optional[TargetRegion]:
        for t in self.targets:
            if t.target_id == target_id:
                return t
        return None

    def occupant_ids(self, target_id: str, fully_inside_only: bool = True) -> list[str]:
        return [
            o.entity_id
            for o in self.occupancy
            if o.target_id == target_id and (o.fully_inside or not fully_inside_only)
        ]


# ---------- task / goal ----------


class TaskInput(BaseModel):
    task_id: str
    utterance: str
    language: str = "zh"
    declared_constraints: list[str] = Field(default_factory=list)


class EntityRef(BaseModel):
    entity_id: Optional[str] = None
    attributes: dict[str, str] = Field(default_factory=dict)
    selector: Optional[Literal["rest"]] = None


class GoalAssignment(BaseModel):
    entity: EntityRef
    target_id: str


class GoalSpec(StrictModel):
    goal_id: str = Field(default_factory=lambda: new_id("g"))
    assignments: list[GoalAssignment]
    ambiguous: bool = False
    clarification_request: Optional[str] = None
    prohibitions: list[str] = Field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    based_on_state_version: Optional[int] = None

    @property
    def well_formed(self) -> bool:
        return not self.ambiguous and len(self.assignments) > 0


# ---------- public verification parameters vs hidden scoring truth ----------


class VerifyConfig(BaseModel):
    """Public predicate parameters (SPEC 7: RuntimeVerifier uses *public*
    parameters; SPEC 4: public execution budget, verification parameters and
    hidden scoring targets are passed separately)."""

    tolerance_version: str = "v2"
    settle_time_s: float = 0.5
    speed_tol_mps: float = 0.02
    ang_speed_tol_rps: float = 0.2
    support_height_tol_m: float = 0.012
    footprint_margin_m: float = 0.005
    lift_gain_min_m: float = 0.03
    grasp_follow_tol_m: float = 0.03


class Assignment(BaseModel):
    """Ground-truth expectation authored independently of any model output."""

    entity_id: str
    target_id: str


class EvalSpec(BaseModel):
    """Hidden scoring truth. Only `IndependentEvaluator` may read it, and only
    to score (SPEC 4, 7, 12.1.1). The end-of-episode settle protocol is *not*
    here: it is the shared public procedure in `VerifyConfig`, so a single
    snapshot serves both judges and the evaluator cannot re-run the world."""

    eval_id: str
    assignments: list[Assignment]
    tolerance_version: str = "v2"
    speed_tol_mps: float = 0.02
    ang_speed_tol_rps: float = 0.2
    support_height_tol_m: float = 0.012
    footprint_margin_m: float = 0.005
    lift_gain_min_m: float = 0.03


class Budgets(StrictModel):
    """Public execution budget (SPEC 6.2).

    `max_decision_rounds` replaces the old practice of charging model decisions
    against `max_global_replans`; the latter survives only for the legacy
    full-plan+replan reference path and is not used by the decision loop."""

    max_decision_rounds: int = 24
    max_http_requests: int = 32
    max_skill_calls: int = 30
    per_skill_sim_timeout_s: float = 30.0
    wall_clock_s: float = 900.0
    max_identical_invalid_attempts: int = 3
    max_semantic_repairs: int = 2
    # mode-A one-shot baseline rule (SPEC 11.2): repeat the identical skill /
    # object / candidate at most this many extra times.
    per_action_extra_repeats: int = 2
    # legacy reference path only
    max_global_replans: int = 3


# ---------- plan (baseline mode A) ----------

SkillName = Literal["observe", "pick", "place", "safe_retreat",
                    # ALFWorld's native verbs: one name here maps to exactly one
                    # environment command, and `observe` is reused for `look`.
                    "alfred_go", "alfred_take", "alfred_move", "alfred_open", "alfred_close",
                    "alfred_use", "alfred_clean", "alfred_heat", "alfred_cool", "alfred_examine",
                    "alfred_inventory"]


class PlanStep(StrictModel):
    id: str
    skill: SkillName
    args: dict[str, str] = Field(default_factory=dict)


class Plan(StrictModel):
    schema_version: str = SCHEMA_VERSION
    plan_id: str = Field(default_factory=lambda: new_id("p"))
    based_on_state_version: int
    goal_ref: str
    steps: list[PlanStep]

    def validate_schema(self) -> list[str]:
        errs = []
        seen = set()
        for s in self.steps:
            if s.id in seen:
                errs.append(f"duplicate step id {s.id}")
            seen.add(s.id)
        return errs


class SkillCall(BaseModel):
    call_id: str = Field(default_factory=lambda: new_id("c"))
    plan_id: str
    step_id: str
    skill: SkillName
    args: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = 30.0
    expected_state_version: Optional[int] = None
    decision_id: Optional[str] = None
    based_on_state_version: Optional[int] = None
    candidate_id: Optional[str] = None


class SkillStatus(str, Enum):
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
    uncertain = "uncertain"
    cancelled = "cancelled"
    rejected = "rejected"  # never executed: validation refused it (SPEC 5.4)


class SkillResult(BaseModel):
    call_id: str
    status: SkillStatus
    failure_code: Optional[str] = None
    t_start: float
    t_end: float
    sim_seconds_used: float = 0.0
    stages_executed: list[str] = Field(default_factory=list)
    pre_observation_ref: Optional[str] = None
    post_observation_ref: Optional[str] = None
    held_state: Union[str, None, Unknown] = "unknown"
    measurements: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    # who chose the slot a `place` used: the decision (model_named_candidate) or the
    # shared execution-time rule (runtime_fallback_rule). SPEC 5.5 requires an
    # automatic slot choice to be recorded, and a prose note cannot be counted.
    candidate_resolution: Optional[str] = None


class FailureCode(str, Enum):
    GRASP_MISS = "GRASP_MISS"
    IK_UNREACHABLE = "IK_OR_PATH_UNREACHABLE"
    PATH_COLLISION = "PATH_COLLISION"
    DROP_UNCONFIRMED = "OBJECT_RELEASE_UNCONFIRMED"
    TARGET_FULL = "TARGET_NO_FREE_SLOT"
    INVALID_PLAN = "INVALID_PLAN"
    VERIFY_FAILED = "VERIFY_FAILED"
    TIMEOUT = "TIMEOUT"
    ANOMALOUS_CONTACT = "ANOMALOUS_CONTACT"
    AMBIGUOUS_TASK = "AMBIGUOUS_TASK"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PLACE_UNSTABLE = "PLACE_UNSTABLE"
    PLACE_COLLISION = "PLACE_COLLISION"
    OBJECT_DROPPED = "OBJECT_DROPPED"
    STATE_UNCERTAIN = "STATE_UNCERTAIN"
    # decision-chain codes (SPEC 5.3, 5.5, 6.1)
    INVALID_DECISION = "INVALID_DECISION"
    UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"
    HELD_STATE_CONFLICT = "HELD_STATE_CONFLICT"
    CANDIDATE_STALE = "CANDIDATE_STALE"
    CANDIDATE_INFEASIBLE = "CANDIDATE_INFEASIBLE"
    CANDIDATE_OCCUPIED = "CANDIDATE_OCCUPIED"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    REACHABILITY_UNPROVEN = "REACHABILITY_UNPROVEN"
    MODEL_ERROR = "MODEL_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NO_FEASIBLE_CANDIDATE = "NO_FEASIBLE_CANDIDATE"
    FINISH_REJECTED = "FINISH_REJECTED"
    REPEATED_INVALID = "REPEATED_INVALID"
    # benchmark-backend codes (SPEC-BST 6): the world ended, the world broke, or
    # the layer between them broke. None of these is a model failure, and an
    # environment's own "you lost" is a lifecycle event rather than a score.
    ENV_TERMINATED = "ENV_TERMINATED"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    ADAPTER_ERROR = "ADAPTER_ERROR"


# ---------- verification ----------


class PredicateVerdict(str, Enum):
    true = "true"
    false = "false"
    unknown = "unknown"


class PredicateReport(BaseModel):
    """One predicate answer plus the numbers that produced it.

    `unmeasured` names the sub-facts this snapshot could not measure. Absence is
    recorded as a name, never as a placeholder number: an evidence field either
    holds a real measurement or does not appear, so `0.0` can never be read as
    "measured and false" by a model or a report (SPEC 5.1)."""

    predicate_id: str
    description: str
    value: PredicateVerdict
    evidence: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    unmeasured: list[str] = Field(default_factory=list)
    source: Source = Source.privileged


class VerificationReport(BaseModel):
    reports: list[PredicateReport]
    source: Source
    tolerance_version: str = "v2"
    world_observation_ref: Optional[str] = None
    state_version: Optional[int] = None

    @property
    def overall(self) -> PredicateVerdict:
        """Any false => false; any unknown (with rest true) => unknown; empty => unknown."""
        if not self.reports:
            return PredicateVerdict.unknown
        if any(r.value == PredicateVerdict.false for r in self.reports):
            return PredicateVerdict.false
        if any(r.value == PredicateVerdict.unknown for r in self.reports):
            return PredicateVerdict.unknown
        return PredicateVerdict.true


# ---------- placement candidates (SPEC 5.5) ----------


class CheckVerdict(str, Enum):
    ok = "ok"
    fail = "fail"
    unchecked = "unchecked"


class PlacementCandidate(StrictModel):
    """A concrete geometric placement option with an identity that survives
    regeneration.

    `candidate_id` names *this* geometry: two generators must never reuse an id
    for a different position (SPEC 5.5). Each check is reported separately and
    `unchecked` is not the same claim as `ok` (SPEC 5.5: 未经检查不能标为已证明可执行)."""

    candidate_id: str
    target_id: str
    entity_id: str
    position_xy: tuple[float, float]
    rest_z: float
    footprint_half_xy: tuple[float, float]
    generated_at_state_version: int
    params_ok: CheckVerdict = CheckVerdict.unchecked
    boundary_ok: CheckVerdict = CheckVerdict.unchecked
    occupancy_ok: CheckVerdict = CheckVerdict.unchecked
    reachability_ok: CheckVerdict = CheckVerdict.unchecked
    checked_at_state_version: Optional[int] = None
    superseded_by: Optional[str] = None
    notes: list[str] = Field(default_factory=list)

    @property
    def is_executable(self) -> bool:
        return self.params_ok == CheckVerdict.ok and self.boundary_ok == CheckVerdict.ok

    @property
    def is_preferred(self) -> bool:
        return self.is_executable and self.occupancy_ok == CheckVerdict.ok and self.reachability_ok == CheckVerdict.ok

    def summary(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "target_id": self.target_id,
            "entity_id": self.entity_id,
            "position_xy": [round(v, 4) for v in self.position_xy],
            "rest_z": round(self.rest_z, 4),
            "footprint_half_xy": [round(v, 4) for v in self.footprint_half_xy],
            "generated_at_state_version": self.generated_at_state_version,
            "checks": {
                "params": self.params_ok.value,
                "boundary": self.boundary_ok.value,
                "occupancy": self.occupancy_ok.value,
                "reachability": self.reachability_ok.value,
            },
            "checked_at_state_version": self.checked_at_state_version,
            "superseded_by": self.superseded_by,
            "notes": list(self.notes),
        }


# ---------- decision chain (SPEC 5.2 - 5.4) ----------


class GoalProgressItem(StrictModel):
    """Per-assignment progress recomputed from current evidence every round.
    There is no permanent "done" flag: a satisfied item that is later disturbed
    returns to `false` (SPEC 5.1, 5.2)."""

    entity_id: str
    target_id: str
    value: PredicateVerdict
    evidence: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    unmeasured: list[str] = Field(default_factory=list)
    predicate_id: str = ""


class AttemptRecord(StrictModel):
    """Bounded history of failed skill/object/candidate combinations."""

    skill: str
    entity_id: Optional[str] = None
    target_id: Optional[str] = None
    candidate_id: Optional[str] = None
    failure_code: Optional[str] = None
    at_state_version: int
    count: int = 1
    last_observation_ref: Optional[str] = None


class ExecutionFeedback(StrictModel):
    """What happened, expressed as facts. It may list candidate facts and failed
    constraints but never "the next step must be X" (SPEC 5.4)."""

    feedback_id: str = Field(default_factory=lambda: new_id("fb"))
    decision_id: Optional[str] = None
    call_id: Optional[str] = None
    context_id: Optional[str] = None
    skill: Optional[str] = None
    entity_id: Optional[str] = None
    target_id: Optional[str] = None
    candidate_id: Optional[str] = None
    # which slot the executor actually used and who picked it: the id the decision
    # named is `candidate_id`; `runtime_fallback_rule` means the shared
    # execution-time rule chose, and `candidate_*` measurements say where (SPEC 5.5)
    candidate_resolution: Optional[str] = None
    executed: bool = False
    stages_executed: list[str] = Field(default_factory=list)
    status: Optional[str] = None
    failure_code: Optional[str] = None
    pre_observation_ref: Optional[str] = None
    post_observation_ref: Optional[str] = None
    new_measurement: bool = False
    pre_state_version: Optional[int] = None
    post_state_version: Optional[int] = None
    verification: Optional[VerificationReport] = None
    held_object_after: Union[str, None, Unknown] = "unknown"
    progress_changes: list[dict[str, str]] = Field(default_factory=list)
    affected_entities: list[str] = Field(default_factory=list)
    measurements: dict[str, float] = Field(default_factory=dict)
    state_diff: dict[str, Any] = Field(default_factory=dict)
    rejection_reasons: list[str] = Field(default_factory=list)
    # what the environment said after this action, verbatim. A text backend's
    # feedback *is* this sentence; a simulator has no such text and leaves it None.
    environment_text: Optional[str] = None
    cause_confirmed: bool = False
    sim_seconds_used: float = 0.0
    schema_version: str = SCHEMA_VERSION

    def short(self) -> dict[str, Any]:
        """Model-facing rendering: facts only, bounded."""
        out: dict[str, Any] = {
            "feedback_id": self.feedback_id,
            "decision_id": self.decision_id,
            "skill": self.skill,
            "entity_id": self.entity_id,
            "target_id": self.target_id,
            "candidate_id": self.candidate_id,
            "executed": self.executed,
            "status": self.status,
            "failure_code": self.failure_code,
            "held_object_after": self.held_object_after,
            "pre_observation_ref": self.pre_observation_ref,
            "post_observation_ref": self.post_observation_ref,
            "new_measurement": self.new_measurement,
        }
        if self.stages_executed:
            out["stages_executed"] = list(self.stages_executed)
        if self.candidate_resolution:
            # a fact about the action taken, not a verification report: mode C keeps it
            out["candidate_resolution"] = self.candidate_resolution
        if self.rejection_reasons:
            out["rejection_reasons"] = list(self.rejection_reasons)
        if self.environment_text is not None:
            out["environment_text"] = self.environment_text
        if self.verification is not None:
            out["verification"] = [
                {"predicate": r.predicate_id, "value": r.value.value,
                 "evidence": {k: round(v, 4) for k, v in r.evidence.items()},
                 **({"unmeasured": list(r.unmeasured)} if r.unmeasured else {})}
                for r in self.verification.reports
            ]
        if self.measurements:
            out["measurements"] = {k: round(v, 4) for k, v in self.measurements.items()}
        if self.progress_changes:
            out["progress_changes"] = list(self.progress_changes)
        if self.affected_entities:
            out["affected_entities"] = list(self.affected_entities)
        if self.state_diff:
            out["state_diff"] = self.state_diff
        if not self.cause_confirmed and self.failure_code:
            out["cause_note"] = "root cause not confirmed by measurement"
        return out


class DecisionContext(StrictModel):
    """Everything the model is allowed to see at one decision boundary
    (SPEC 5.2). Nothing here comes from `EvalSpec` or the fault config."""

    schema_version: str = SCHEMA_VERSION
    context_id: str = Field(default_factory=lambda: new_id("ctx"))
    episode_id: str
    round_index: int = 0
    task: TaskInput
    goal: GoalSpec
    goal_version: int = 1
    public_constraints: list[str] = Field(default_factory=list)
    state_version: int
    observation_ref: str
    sim_time: float = 0.0
    world: WorldState
    progress: list[GoalProgressItem] = Field(default_factory=list)
    last_feedback: Optional[ExecutionFeedback] = None
    recent_feedbacks: list[ExecutionFeedback] = Field(default_factory=list)
    attempts: list[AttemptRecord] = Field(default_factory=list)
    skill_catalogue: dict[str, Any] = Field(default_factory=dict)
    candidates: list[PlacementCandidate] = Field(default_factory=list)
    candidates_truncated: int = 0
    current_subgoal: Optional[str] = None
    todo_summary: Optional[str] = None
    intent_note: str = "current_subgoal/todo_summary are model intent, not world facts"
    budget: dict[str, Any] = Field(default_factory=dict)

    def model_payload(self, feedback_depth: int = 3) -> dict[str, Any]:
        """The bounded prompt payload. All goals and the current state stay in
        full; only feedback detail is truncated, and never by dropping failures
        (SPEC 5.2)."""
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "context_id": self.context_id,
            "round_index": self.round_index,
            "state_version": self.state_version,
            "observation_ref": self.observation_ref,
            "sim_time": round(self.sim_time, 3),
            "task": {"utterance": self.task.utterance,
                     "declared_constraints": list(self.task.declared_constraints)},
            "goal": {"goal_id": self.goal.goal_id,
                     "assignments": [
                         {"entity_id": a.entity.entity_id, "attributes": a.entity.attributes,
                          "target_id": a.target_id} for a in self.goal.assignments]},
            "progress": [
                {"entity_id": p.entity_id, "target_id": p.target_id, "value": p.value.value,
                 "evidence": {k: round(v, 4) for k, v in p.evidence.items()},
                 **({"unmeasured": list(p.unmeasured)} if p.unmeasured else {})}
                for p in self.progress
            ],
            "world": {
                "held_object": self.world.held_object,
                "entities": [
                    {"entity_id": e.entity_id,
                     # a backend that sees colour but not location, or a body whose box
                     # the depth image could not resolve, has a null position — which the
                     # model must be able to tell apart from a position that was measured
                     # and found to be at the origin.
                     "position": (None if e.pose is None else
                                  [round(e.pose.position.x, 4), round(e.pose.position.y, 4),
                                   round(e.pose.position.z, 4)]),
                     # an assumed upright heading is not a measurement of one, so the
                     # number stays out of the payload and the model is left with the
                     # caveat in `attributes` instead of a confident 0.0
                     "yaw_rad": (None if e.pose is None or not e.orientation_measured
                                 else round(e.pose.yaw_rad(), 3)),
                     "geometry": (e.geometry.model_dump(exclude_none=True) if e.geometry else None),
                     "held": e.held,
                     "supported_by": e.supported_by,
                     "at_rest": e.at_rest,
                     "attributes": e.attributes}
                    for e in self.world.entities
                ],
                "targets": [
                    {"target_id": t.target_id,
                     "center": [round(t.center.x, 4), round(t.center.y, 4), round(t.center.z, 4)],
                     "inner_half": round(t.inner_half, 4), "floor_top_z": round(t.floor_top_z, 4)}
                    for t in self.world.targets
                ],
                "occupancy": [
                    {"target_id": o.target_id, "entity_id": o.entity_id,
                     "fully_inside": o.fully_inside}
                    for o in self.world.occupancy
                ],
                # A body the frame did not find is not in `entities` and so would be
                # silently absent from the payload — read by the model as "there is no
                # green one" rather than as "this camera did not see it". §5.1's
                # visibility capability exists to keep those two apart, so the row is
                # published, in the frame's own name for the body and with the blocker the
                # frame named.
                **({"not_seen": [
                        {"entity_id": n.entity_id, "view": n.view,
                         **({"occluded_by": n.occluded_by} if n.occluded_by else {})}
                        for n in self.world.not_seen]}
                   if self.world.not_seen else {}),
            },
            "skills": self.skill_catalogue,
            "candidates": [c.summary() for c in self.candidates],
            "candidates_truncated": self.candidates_truncated,
            "last_feedback": self.last_feedback.short() if self.last_feedback else None,
            "recent_feedbacks": [f.short() for f in self.recent_feedbacks[-feedback_depth:]],
            "attempts": [a.model_dump() for a in self.attempts],
            "current_subgoal": self.current_subgoal,
            "todo_summary": self.todo_summary,
            "budget": dict(self.budget),
        }


class DecisionExecute(StrictModel):
    skill: SkillName
    args: dict[str, str] = Field(default_factory=dict)
    candidate_id: Optional[str] = None
    subgoal: Optional[str] = None


class Decision(StrictModel):
    """One of four mutually exclusive outcomes (SPEC 5.3)."""

    schema_version: str = SCHEMA_VERSION
    decision_id: str = Field(default_factory=lambda: new_id("d"))
    context_id: str
    based_on_state_version: int
    goal_ref: str
    action: Literal["execute", "finish", "blocked", "clarify"]
    execute: Optional[DecisionExecute] = None
    missing_information: Optional[str] = None
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str = ""
    expected_effect: str = ""

    @model_validator(mode="after")
    def _check_action_payload(self):
        if self.action == "execute":
            if self.execute is None:
                raise ValueError("action=execute requires an `execute` payload")
        elif self.execute is not None:
            raise ValueError(f"action={self.action} must not carry an `execute` payload")
        if self.action == "clarify" and not self.missing_information:
            raise ValueError("action=clarify must name the missing or conflicting information")
        return self

    @model_validator(mode="after")
    def _one_canonical_candidate_reference(self):
        """A slot named in the argument bag is the same reference as the canonical one.

        `execute.candidate_id` is the field the skill call, the execution feedback and
        the frozen state-pair labels all read, while `place` also documents a
        `candidate_id` argument and `args` is an open dict — so a model can name its
        geometry in either place. Honouring the argument while the canonical field
        stayed empty would mean the placement used the model's own slot but no record
        said which one it was, including for the labels that *reject* a named slot
        (measured: two real online rounds did exactly this). Two different names is a
        claim of two geometries at once, which only the model can resolve, so that is
        refused rather than chosen between; the runtime never picks a slot here.
        """
        ex = self.execute
        if ex is None:
            return self
        named = ex.args.get("candidate_id")
        if not named:
            return self
        if ex.candidate_id and ex.candidate_id != named:
            raise ValueError("execute.candidate_id and execute.args.candidate_id name "
                             f"different candidates ({ex.candidate_id} vs {named})")
        if not ex.candidate_id:
            self.execute = ex.model_copy(update={"candidate_id": named})
        return self

    def skill_call(self, plan_id: str = "decision", step_id: str = "d") -> SkillCall:
        if self.action != "execute" or self.execute is None:
            raise ValueError("only execute decisions map to a SkillCall")
        args = dict(self.execute.args)
        if self.execute.candidate_id:
            args["candidate_id"] = self.execute.candidate_id
        return SkillCall(
            plan_id=plan_id, step_id=step_id, skill=self.execute.skill, args=args,
            decision_id=self.decision_id, based_on_state_version=self.based_on_state_version,
            candidate_id=self.execute.candidate_id,
        )


class DecisionSchemaError(Exception):
    """A model payload that did not become a `Decision`.

    It exists so a *shape* error reaches the Runtime's bounded-repair path with
    the field-level reasons intact (SPEC 6.1: schema 或语义不合法时返回精简拒绝
    反馈，限次修复). That is not the same event as a transport or empty-response
    failure, which stays `MODEL_ERROR` (SPEC 11.1 lists them separately), and the
    adapter must not paper over either one by inventing a valid-looking action."""

    def __init__(self, reasons: list[str], meta: dict | None = None):
        super().__init__("; ".join(reasons) or "decision payload did not validate")
        self.reasons = list(reasons)
        self.meta = meta or {}


class TerminalStatus(str, Enum):
    success = "success"
    failed = "failed"
    needs_clarification = "needs_clarification"
    cancelled = "cancelled"


class EpisodeResult(BaseModel):
    episode_id: str
    task_id: str
    terminal_status: TerminalStatus
    mode: str = "B"
    decision_rounds: int = 0
    skill_calls: int = 0
    http_requests: int = 0
    score_complete_success: bool = False
    objects_total: int = 0
    objects_completed: int = 0
    failure_type: Optional[FailureCode] = None
    recovery_events: int = 0
    retry_events: int = 0
    replan_events: int = 0
    rejected_decisions: int = 0
    false_finish_attempts: int = 0
    # the length of the identical-attempt run the episode ended inside: this is
    # what the SPEC 6.2 budget bounds, so a clean episode legitimately reports 1
    identical_invalid_attempts: int = 0
    # how many attempts in the whole episode repeated the previous action under
    # an unchanged world — the number a report can add up
    identical_repeats_total: int = 0
    model_errors: int = 0
    semantic_repairs: int = 0
    provider_counters: dict[str, int] = Field(default_factory=dict)
    api_cost_estimate: Optional[float] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    sim_time_s: float = 0.0
    wall_time_s: float = 0.0
    # how many `place` actions let the shared execution-time rule pick the slot
    # because the decision named no candidate — the "explicitly recorded, identical
    # for every mode" record SPEC 5.5 asks for. It is not a strategy substitution:
    # the runtime never chose the action. It is a count, not `fallback_used`,
    # because that name reads as the one thing SPEC 6.1 forbids.
    slot_resolution_fallbacks: int = 0
    human_intervention: bool = False
    config_ref: Optional[str] = None
    # SPEC-BST 6.2: a benchmark episode must be able to say *who* ended it — the
    # world, the agent, a budget or a broken process. `terminal_status` plus
    # `failure_type` cannot name that without inventing a physics failure code for
    # a lifecycle event, so the reason is recorded as its own word. Desktop runs
    # leave it null; nothing about the desktop loop changed.
    termination_reason: Optional[str] = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class ObservationRecord(BaseModel):
    """Append-only ledger entry: which observation ref produced which snapshot
    version, so a claim can always be traced to a real measurement
    (SPEC 5.1, 7)."""

    observation_ref: str
    state_version: int
    sim_time: float
    wall_time: float
    source: Source
    held_object: Union[str, None, Unknown]
    n_entities: int


class TerminalSnapshot(BaseModel):
    """One frozen end-of-episode world sample taken under the shared settle
    protocol. The independent evaluator scores this sample, so judging cannot
    advance physics further and then pick a more favourable instant
    (SPEC 7)."""

    observation_ref: str
    state_version: int
    sim_time: float
    settle_seconds: float
    entities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    targets: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @classmethod
    def capture(cls, scene, settle_seconds: float, ref: str, version: int) -> "TerminalSnapshot":
        """Advance the shared settle window once, then freeze every measured
        fact a judge needs. Nothing downstream steps the simulator again."""
        scene.settle(settle_seconds)
        data = {}
        for eid in scene.objects:
            pos, orn = scene.object_pose(eid)
            lin, ang = scene.object_velocity(eid)
            geom = scene.geometry(eid)
            quat = [float(orn[0]), float(orn[1]), float(orn[2]), float(orn[3])]
            rows = rotation_rows_from_quat_xyzw(quat)
            # heading of the body's own x axis projected on the ground plane
            yaw = math.atan2(rows[1][0], rows[0][0])
            data[eid] = {
                "pos": [float(pos[0]), float(pos[1]), float(pos[2])],
                "quat_xyzw": quat,
                "yaw_rad": yaw,
                "footprint_half_xy": [float(v) for v in footprint_half_xy_from_quat(geom, quat)],
                "rest_extent_z": vertical_half_extent(geom, quat),
                "half_h": geom.half_h_vertical,
                "lin_speed": float(np.linalg.norm(lin)),
                "ang_speed": float(np.linalg.norm(ang)),
                "contacts": scene.contact_partners(eid),
                "in_gripper": float(scene.objects[eid]["body"] in scene.held_bodies()),
            }
        targets = {
            tid: {"center": [float(t["center"][0]), float(t["center"][1])],
                  "inner_half": float(t["inner_half"]), "floor_top_z": float(t["floor_top"])}
            for tid, t in scene.trays.items()
        }
        return cls(observation_ref=ref, state_version=version, sim_time=float(scene.sim_time),
                   settle_seconds=float(settle_seconds), entities=data, targets=targets)
