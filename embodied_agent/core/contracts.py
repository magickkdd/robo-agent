"""Core data contracts (spec section 8).

SI units: metres, radians, seconds. Quaternions are xyzw. Every pose carries an
explicit frame_id. Implemented with Pydantic per spec section 8 preamble.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Literal, Optional, Union

import numpy as np
from pydantic import BaseModel, Field


def now_wall() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Vec3(BaseModel):
    x: float
    y: float
    z: float


class Pose(BaseModel):
    position: Vec3
    quaternion_xyzw: tuple[float, float, float, float]
    frame_id: str = "world"


class Source(str, Enum):
    privileged = "privileged"
    sensor = "sensor"


class EntityState(BaseModel):
    entity_id: str
    pose: Pose
    held: Union[bool, Literal["unknown"]] = "unknown"
    supported_by: Optional[str] = None
    visible: bool = True
    confidence: Optional[float] = None  # uncalibrated when from VLM
    attributes: dict[str, str] = Field(default_factory=dict)
    source: Source = Source.privileged


class TargetRegion(BaseModel):
    target_id: str
    label: str  # tray_left / tray_middle / tray_right
    center: Vec3
    inner_half: float
    floor_top_z: float
    frame_id: str = "world"


class WorldState(BaseModel):
    state_version: int
    sim_time: float
    wall_time: float
    entities: list[EntityState]
    targets: list[TargetRegion]
    held_object: Union[str, None, Literal["unknown"]] = None
    observation_ref: Optional[str] = None

    def entity(self, entity_id: str) -> EntityState:
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        raise KeyError(entity_id)


# ---------- task / goal / eval ----------


class TaskInput(BaseModel):
    task_id: str
    utterance: str
    language: str = "zh"
    declared_constraints: list[str] = Field(default_factory=list)


class EntityRef(BaseModel):
    """Reference used by the interpreter: either a stable logical id or an
    attribute selector (attribute bindings resolved against the world state)."""

    entity_id: Optional[str] = None
    attributes: dict[str, str] = Field(default_factory=dict)
    selector: Optional[Literal["rest"]] = None  # "剩下的/其余"


class GoalAssignment(BaseModel):
    entity: EntityRef
    target_id: str


class GoalSpec(BaseModel):
    goal_id: str = Field(default_factory=lambda: new_id("g"))
    assignments: list[GoalAssignment]
    ambiguous: bool = False
    clarification_request: Optional[str] = None
    prohibitions: list[str] = Field(default_factory=list)

    @property
    def well_formed(self) -> bool:
        return not self.ambiguous and len(self.assignments) > 0


class Assignment(BaseModel):
    """Ground-truth expectation authored independently of any model output."""

    entity_id: str
    target_id: str


class EvalSpec(BaseModel):
    eval_id: str
    assignments: list[Assignment]
    tolerance_version: str = "v1"
    settle_time_s: float = 0.5
    speed_tol_mps: float = 0.02
    ang_speed_tol_rps: float = 0.2
    support_height_tol_m: float = 0.01
    footprint_margin_m: float = 0.005
    lift_gain_min_m: float = 0.03
    budgets: "Budgets" = Field(default_factory=lambda: Budgets())


class Budgets(BaseModel):
    per_object_extra_attempts: int = 2
    max_global_replans: int = 3
    max_skill_calls: int = 30
    max_model_requests: int = 10
    wall_clock_s: float = 900.0
    per_skill_sim_timeout_s: float = 30.0
    max_consecutive_no_progress: int = 3


# ---------- plan / skills ----------

SkillName = Literal["observe", "pick", "place", "safe_retreat"]


class PlanStep(BaseModel):
    id: str
    skill: SkillName
    args: dict[str, str] = Field(default_factory=dict)


class Plan(BaseModel):
    schema_version: str = "1"
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


class SkillStatus(str, Enum):
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
    uncertain = "uncertain"
    cancelled = "cancelled"


class SkillResult(BaseModel):
    call_id: str
    status: SkillStatus
    failure_code: Optional[str] = None
    t_start: float
    t_end: float
    stages_executed: list[str] = Field(default_factory=list)
    pre_observation_ref: Optional[str] = None
    post_observation_ref: Optional[str] = None
    held_state: Union[str, None, Literal["unknown"]] = None
    measurements: dict[str, float] = Field(default_factory=dict)
    remaining_action_hint: Optional[str] = None


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


# ---------- verification ----------


class PredicateVerdict(str, Enum):
    true = "true"
    false = "false"
    unknown = "unknown"


class PredicateReport(BaseModel):
    predicate_id: str
    description: str
    value: PredicateVerdict
    evidence: dict[str, float] = Field(default_factory=dict)
    source: Source = Source.privileged


class VerificationReport(BaseModel):
    reports: list[PredicateReport]
    source: Source
    tolerance_version: str = "v1"

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


# ---------- episode ----------


class TerminalStatus(str, Enum):
    success = "success"
    failed = "failed"
    needs_clarification = "needs_clarification"
    cancelled = "cancelled"


class EpisodeResult(BaseModel):
    episode_id: str
    task_id: str
    terminal_status: TerminalStatus
    score_complete_success: bool = False
    objects_total: int = 0
    objects_completed: int = 0
    failure_type: Optional[FailureCode] = None
    recovery_events: int = 0
    retry_events: int = 0
    replan_events: int = 0
    model_requests: int = 0
    api_cost_estimate: Optional[float] = None
    sim_time_s: float = 0.0
    wall_time_s: float = 0.0
    fallback_used: bool = False
    human_intervention: bool = False
    config_ref: Optional[str] = None
    artifacts: dict[str, str] = Field(default_factory=dict)


EvalSpec.model_rebuild()
