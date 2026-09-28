"""v0.2 data contracts (SPEC-v0.2 §6, §5.2–§5.6).

Why this is a separate module rather than an edit to `core/contracts.py`: the v0.1
frozen batches grade against the *text* of the prompts and the task list, and the
`Source`/`StrictModel` conventions defined there. Nothing in this file changes those
files; it composes them. Schema version 4 is declared here and travels with each
record, so a v0.1 log and a v0.2 log can never be silently pooled into one statistic.

Three invariants carried over from v0.1 and extended:

* **Truth separation.** A `WorkingMemoryState` holds what the agent *believes and owes*,
  never what the evaluator knows. `SkillValidationReport` records evidence the agent
  produced by running things, not a reference answer. Nothing here imports `EvalSpec`,
  and an evaluator must not read a plan to decide a score (SPEC-v0.2 §3.2, §3.5).
* **No silent defaults.** Every status that a runtime cannot measure is `unknown`,
  three-valued booleans included. An unanswered obligation stays visible as unanswered;
  it is never closed by omission (§5.3: 恢复义务 must survive being inconvenient).
* **Provenance on every field group.** A plan step, a memory row and a perception
  detection all say which observation or model call produced them, so a later reader can
  tell "the model said so" from "the environment said so" from "the framework computed
  it" (§5.8's three-layer split).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import Field, model_validator

from .contracts import (
    SCHEMA_VERSION as V01_SCHEMA_VERSION,
    SkillCall,
    Source,
    StrictModel,
    Unknown,
    Vec3,
    new_id,
    now_wall,
)

SCHEMA_VERSION = "4"

# The envelope every v0.2 record carries: its own identity plus the five things
# SPEC-v0.2 §6 demands of all of them — schema version, episode, a state reference, a
# source and a time (`updated_at` is the sixth because a plan is a work object and
# rewrites itself; `provenance` carries where a field came from).
#
# `based_on_state_version` is the WorldState version this record was computed against;
# `None` means the record is not about a state at all (a plan revision, a memory row),
# which is deliberately different from `0`. `based_on_observation_ref` is the percept a
# claim rests on, so "the VLM said it" and "the simulator said it" stay distinguishable.


class Record(StrictModel):
    schema_version: str = SCHEMA_VERSION
    episode_id: Optional[str] = None
    created_at: float = Field(default_factory=now_wall)
    updated_at: float = Field(default_factory=now_wall)
    source: Source = Source.sensor
    provenance: dict[str, Any] = Field(default_factory=dict)
    based_on_state_version: Optional[int] = None
    based_on_observation_ref: Optional[str] = None

    @model_validator(mode="after")
    def _not_v01(self):
        if self.schema_version == V01_SCHEMA_VERSION:
            raise ValueError("v0.2 records may not claim schema 3")
        return self


RECORD_FIELDS = tuple(Record.model_fields)


class Status(str, Enum):
    """The answers a subgoal may carry. `pending` is not `unknown`: the first means the
    work has not started, the second means nobody has looked."""

    pending = "pending"
    active = "active"
    done = "done"
    failed = "failed"
    abandoned = "abandoned"
    unknown = "unknown"


class Unknownable(StrictModel):
    """A value the agent either measured or did not — never guessed."""

    value: Union[bool, Unknown] = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)
    unmeasured: list[str] = Field(default_factory=list)


# --------------------------------------------------------------- 5.2 planning -----


class Subgoal(Record):
    """One unit of the long-horizon task, stated in the agent's own public language.

    `statement` is text the agent could have read or written; it must not contain a
    predicate the environment has never named, because the plan is model-facing and a
    plan that invents facts teaches the model to invent facts."""

    subgoal_id: str = Field(default_factory=lambda: new_id("sub"))
    statement: str = ""
    kind: Literal["achieve", "maintain", "recover", "observe", "control"] = "achieve"
    status: Status = Status.pending
    target_entity_ids: list[str] = Field(default_factory=list)
    target_region_id: Optional[str] = None
    predicate_id: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    completion_evidence_refs: list[str] = Field(default_factory=list)
    attempts: int = 0
    last_decision_id: Optional[str] = None
    # `done` is a claim about the world; it is only allowed to be `done` when something
    # public supports it, so an optimistic model still cannot mark a subgoal finished
    # out of thin air (SPEC-v0.2 §3.4: the runtime checks, it does not choose).
    satisfied: Unknownable = Field(default_factory=Unknownable)


class DependencyKind(str, Enum):
    ordering = "ordering"          # B cannot start before A is done
    state = "state"                # B needs a condition A produces
    resource = "resource"          # both need one hand / one region / one object
    recovery = "recovery"          # B must hold until A restores what A' disturbed


class Dependency(Record):
    dep_id: str = Field(default_factory=lambda: new_id("dep"))
    from_subgoal_id: str = ""
    on_subgoal_id: str = ""
    kind: DependencyKind = DependencyKind.ordering
    satisfied: Unknownable = Field(default_factory=Unknownable)
    violated: Unknownable = Field(default_factory=Unknownable)
    note: str = ""


class PlanRevision(Record):
    """Why the plan changed, in one line, with the evidence that forced it.

    Rolling replans are only scientifically interesting if they are auditable: without
    this row, "the agent replanned 11 times" is a number about nothing (§11:
    recovery/replanning effectiveness needs a denominator)."""

    revision_id: str = Field(default_factory=lambda: new_id("rev"))
    from_version: int = 0
    to_version: int = 0
    trigger: Literal["feedback", "failure", "new_observation", "model_request",
                     "dependency_violation", "initial"] = "initial"
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str = ""
    changed_subgoal_ids: list[str] = Field(default_factory=list)


class TaskPlan(Record):
    """The plan as a work object: versioned, revisable, and never a script.

    `version` increments on every accepted revision. `summary` is the short text handed
    to the decision model — the whole subgoal list can be long, and what the model sees
    each round has to fit the budget without dropping obligations."""

    plan_id: str = Field(default_factory=lambda: new_id("plan"))
    goal_ref: Optional[str] = None
    task_utterance: str = ""
    version: int = 1
    subgoals: list[Subgoal] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    summary: str = ""
    authored_by: Literal["model", "rule", "human_dev_only"] = "model"
    revisions: list[PlanRevision] = Field(default_factory=list)
    open_obligation_count: int = 0

    def subgoal(self, subgoal_id: str) -> Optional[Subgoal]:
        for s in self.subgoals:
            if s.subgoal_id == subgoal_id:
                return s
        return None

    @model_validator(mode="after")
    def _ids_unique(self):
        ids = [s.subgoal_id for s in self.subgoals]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate subgoal_id in one plan")
        known = set(ids)
        for s in self.subgoals:
            missing = [d for d in s.depends_on if d not in known]
            if missing:
                raise ValueError(f"subgoal {s.subgoal_id} depends on unknown ids {missing}")
        return self


# ---------------------------------------------------------- 5.3 working memory ----


class Commitment(Record):
    """Something the agent has said it will do, with the round it said it.

    A commitment outlives the feedback window. That is the whole reason this record
    exists separately from `recent_feedbacks`."""

    commitment_id: str = Field(default_factory=lambda: new_id("cmt"))
    text: str = ""
    subgoal_id: Optional[str] = None
    made_at_round: int = 0
    made_at_decision_id: Optional[str] = None
    discharged: Unknownable = Field(default_factory=Unknownable)
    evidence_refs: list[str] = Field(default_factory=list)


class SuspendedRelation(Record):
    """A relation that is temporarily false and must be restored.

    `restore_by` may be a subgoal or a terminal condition ("before the task ends");
    an empty one is allowed and means the agent never said when, which the audit reports
    as an unresolved obligation rather than dropping the row."""

    relation_id: str = Field(default_factory=lambda: new_id("rel"))
    subject_id: str = ""
    predicate: str = ""
    related_id: Optional[str] = None
    was: str = ""
    now: str = ""
    reason: str = ""
    restore_by: str = ""
    restored: Unknownable = Field(default_factory=Unknownable)
    disturbed_at_round: int = 0


class FailedAttempt(Record):
    attempt_id: str = Field(default_factory=lambda: new_id("att"))
    round_index: int = 0
    skill: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    failure_code: Optional[str] = None
    environment_text: Optional[str] = None
    evidence_refs: list[str] = Field(default_factory=list)
    # What the agent then did about it. Two rows with the same skill and args and a
    # `retry` here are the repeated-failure pattern §11 asks to count.
    followed_by: Literal["retry", "change_candidate", "re_observe", "abandon",
                         "replan", "give_up", "nothing"] = "nothing"


class Assumption(Record):
    """A belief the agent is reasoning on, and whether anything still supports it.

    `invalidated_at_round` is set when an observation contradicts it. Carrying the
    invalidated ones forward is what lets a later round notice it kept acting on them."""

    assumption_id: str = Field(default_factory=lambda: new_id("asm"))
    text: str = ""
    supporting_refs: list[str] = Field(default_factory=list)
    holds: Unknownable = Field(default_factory=Unknownable)
    invalidated_at_round: Optional[int] = None
    invalidated_by_ref: Optional[str] = None


class WorkingMemoryState(Record):
    """The current task's live beliefs and debts (SPEC-v0.2 §5.3).

    This is the record that must participate online: the decision context carries a
    rendering of it, so a fact that entered working memory stays available after it
    leaves the feedback window. Retrieval from episodic memory never writes here
    directly — it arrives as `recalled`, marked with where it came from."""

    wm_id: str = Field(default_factory=lambda: new_id("wm"))
    round_index: int = 0
    plan_version: int = 1
    current_subgoal_id: Optional[str] = None
    subgoal_status: dict[str, Status] = Field(default_factory=dict)
    commitments: list[Commitment] = Field(default_factory=list)
    suspended_relations: list[SuspendedRelation] = Field(default_factory=list)
    failed_attempts: list[FailedAttempt] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    unknown_facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    plan_summary: str = ""
    recalled: list["RecalledExperience"] = Field(default_factory=list)
    # `pending_replan` is a request the model can make and the runtime must honour by
    # re-offering the planner next round; it is not the runtime deciding to replan.
    pending_replan: bool = False
    replan_reason: str = ""

    def unresolved_obligations(self) -> int:
        """Count of things still owed. `unknown` counts as owed: an unanswered
        "did I put it back?" is exactly the failure mode this module exists for."""

        def owed(u: Unknownable) -> bool:
            return u.value is not True

        return (sum(owed(c.discharged) for c in self.commitments)
                + sum(owed(r.restored) for r in self.suspended_relations)
                + sum(1 for a in self.assumptions if a.invalidated_at_round is not None
                      and a.holds.value is not False))


# --------------------------------------------------------- 5.4 episodic memory ----


class OutcomeLabel(str, Enum):
    success = "success"
    partial = "partial"
    failure = "failure"
    truncated = "truncated"
    error = "error"


class ExperienceStep(StrictModel):
    skill: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    round_index: int = 0
    observation_ref: Optional[str] = None
    effect: str = ""


class EpisodicExperience(Record):
    """One episode's usable residue (SPEC-v0.2 §5.4).

    `state_pattern` and `applicability` are what retrieval matches on; `outcome` and
    `failure_reason` are what a reader weighs it by. `transfer_caution` is recorded
    rather than inferred: an experience that succeeded once in one layout is not a rule.
    """

    experience_id: str = Field(default_factory=lambda: new_id("exp"))
    task_context: str = ""
    task_kind: str = ""
    scene_id: Optional[str] = None
    object_kinds: list[str] = Field(default_factory=list)
    state_pattern: list[str] = Field(default_factory=list)
    steps: list[ExperienceStep] = Field(default_factory=list)
    outcome: OutcomeLabel = OutcomeLabel.error
    official_success: Optional[bool] = None
    failure_reason: str = ""
    applicability: list[str] = Field(default_factory=list)
    counter_examples: list[str] = Field(default_factory=list)
    transfer_caution: str = ""
    cost: dict[str, Any] = Field(default_factory=dict)
    # Where it came from, so a stale or badly-labelled row can be traced back. The
    # benchmark's hidden verdict may be stored here for *retrieval-time context only*
    # if and only if the loader is verified never to put it in a prompt (§3.2).
    run_ref: str = ""

    def as_reference(self) -> str:
        """A one-paragraph rendering for a prompt. Text only — it must never be merged
        into a WorldState field, which is why the structured lists stay separate."""
        head = self.task_context or self.task_kind or "an earlier attempt"
        return (f"{head}: {len(self.steps)} steps, outcome={self.outcome.value}"
                f"{' — ' + self.failure_reason if self.failure_reason else ''}"
                f"{' (caution: ' + self.transfer_caution + ')' if self.transfer_caution else ''}")


class RecalledExperience(Record):
    """One retrieved row, with why it was chosen and whether it was used.

    `relevance` is the retrieval score, recorded before the model sees it.
    `used_by_model` is judged afterwards from the model's own decision, not from its
    explanation text (§12.3: 以实际动作和最终状态为准)."""

    recall_id: str = Field(default_factory=lambda: new_id("rec"))
    experience_id: str = ""
    relevance: float = 0.0
    match_terms: list[str] = Field(default_factory=list)
    injected_into_round: Optional[int] = None
    used_by_model: Unknownable = Field(default_factory=Unknownable)
    contradicted_current_state: bool = False


# ------------------------------------------------------------ 5.5/5.6 skills -----


class SkillParameter(StrictModel):
    name: str = ""
    type: Literal["entity", "region", "number", "text", "bool"] = "entity"
    description: str = ""
    required: bool = True
    example: Any = None


class ProcedureIntent(StrictModel):
    """One primitive call inside a procedure, with parameters left symbolic.

    A procedure is a template: `args` values are either literals (`"drawer 1"`) or a
    placeholder naming one of the skill's own parameters (`"{container}"`). Binding is
    what turns it into a `SkillCall`, and it is a testable step rather than a string
    convention — see `SkillSpec.bind`."""

    skill: str = ""
    args: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = 30.0


class ProcedureStep(StrictModel):
    """One step of a composite or acquired skill.

    The intent names a *primitive*, so an acquired program is executable by the same
    executor that runs everything else — the point of §5.6's "程序技能" limit, and the
    reason no new interpreter is needed."""

    step_id: str = Field(default_factory=lambda: new_id("step"))
    intent: Optional[ProcedureIntent] = None
    on_failure: Literal["abort", "retry_once", "try_next_candidate", "re_observe",
                        "ask_model"] = "abort"
    when: str = ""
    expect: str = ""


class SkillSpec(Record):
    """The minimum description SPEC-v0.2 §5.5 asks for: its ten fields, of which
    nine are declared here and `provenance` is inherited from `Record`.

    `level` separates what shipped from what was acquired, because the ablation
    "w/o Skill Acquisition" has to be a switch and not a code search."""

    skill_id: str = Field(default_factory=lambda: new_id("skill"))
    name: str = ""
    description: str = ""
    parameters: list[SkillParameter] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    procedure: list[ProcedureStep] = Field(default_factory=list)
    expected_effects: list[str] = Field(default_factory=list)
    termination_conditions: list[str] = Field(default_factory=list)
    recovery_hints: list[str] = Field(default_factory=list)
    applicability: list[str] = Field(default_factory=list)
    level: Literal["primitive", "composite", "acquired"] = "primitive"
    enabled: bool = True
    validation_report_id: Optional[str] = None

    def parameter_names(self) -> list[str]:
        return [p.name for p in self.parameters]

    def bind(self, plan_id: str, params: dict[str, Any]) -> list[SkillCall]:
        """Substitute this skill's parameters into its procedure, or say exactly what
        is missing.

        An unresolved placeholder is an error, not an empty argument: a program that
        silently ran `pick` with no object would show up in the log as an ordinary
        failure rather than as the broken skill it is."""
        out: list[SkillCall] = []
        for step in self.procedure:
            if step.intent is None:
                continue
            args: dict[str, str] = {}
            for key, value in step.intent.args.items():
                text = str(value)
                if text.startswith("{") and text.endswith("}"):
                    name = text[1:-1]
                    if name not in params or params[name] in (None, ""):
                        raise KeyError(
                            f"{self.name}: step {step.step_id} needs parameter {name!r} "
                            f"and the call did not supply it")
                    args[key] = str(params[name])
                else:
                    args[key] = text
            out.append(SkillCall(plan_id=plan_id, step_id=step.step_id,
                                 skill=step.intent.skill, args=args,
                                 timeout_s=step.intent.timeout_s))
        return out

    def unresolved_parameters(self) -> list[str]:
        """Placeholders the procedure uses that the declared parameter list does not
        declare — a candidate that can never bind, caught before it is ever run."""
        declared = set(self.parameter_names())
        missing: list[str] = []
        for step in self.procedure:
            if step.intent is None:
                continue
            for value in step.intent.args.values():
                text = str(value)
                if text.startswith("{") and text.endswith("}") and text[1:-1] not in declared:
                    missing.append(f"{step.step_id}:{text[1:-1]}")
        return missing


class SkillGap(Record):
    """The moment the library ran out, recorded as data.

    Without this row, "the agent acquired a skill" and "the framework patched a hole"
    look identical in the log — and only the first is a result."""

    gap_id: str = Field(default_factory=lambda: new_id("gap"))
    round_index: int = 0
    subgoal_id: Optional[str] = None
    desired_effect: str = ""
    missing_because: Literal["no_skill", "precondition_unmet", "repeated_failure",
                             "no_termination", "other"] = "no_skill"
    evidence_refs: list[str] = Field(default_factory=list)
    existing_candidates_considered: list[str] = Field(default_factory=list)


class ValidationInstance(StrictModel):
    """One run of one proposed skill on one unseen configuration.

    `instance_kind` is what §5.6's gate turns on: an object, layout and parameter are
    each a separate axis of "unseen", and a skill validated only on a new object has
    not been validated against layouts."""

    instance_kind: Literal["object", "layout", "parameters", "task", "repeat"] = "object"
    scene_id: Optional[str] = None
    seed: Optional[int] = None
    args: dict[str, Any] = Field(default_factory=dict)
    ran: bool = False
    success: Unknownable = Field(default_factory=Unknownable)
    effects_observed: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    unsafe: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    wall_s: float = 0.0
    model_calls: int = 0

    @model_validator(mode="after")
    def _unrun_cannot_have_result(self):
        # §12.4: an instance that never executed cannot count toward the cross-instance
        # gate, and it must not be able to carry a verdict either.
        if not self.ran and self.success.value != "unknown":
            raise ValueError("an instance that did not run cannot report a verdict")
        if not self.ran and (self.effects_observed or self.violations or self.unsafe):
            raise ValueError("an instance that did not run cannot report observations")
        return self


class SkillValidationReport(Record):
    """Sandbox + cross-instance evidence for one candidate.

    `passed` is computed only from the instances below, and the rule is written down
    rather than tuned: it needs at least `min_instances` runs covering at least two
    distinct `instance_kind`s, every run `ran`, and no `unsafe` finding anywhere.
    A teleported or evaluator-edited "run" cannot satisfy it because there is no way to
    express one here that does not go through the executor (§5.6's prohibition)."""

    report_id: str = Field(default_factory=lambda: new_id("val"))
    candidate_id: str = ""
    skill_name: str = ""
    instances: list[ValidationInstance] = Field(default_factory=list)
    min_instances: int = 3
    passed: bool = False
    reasons: list[str] = Field(default_factory=list)
    cost: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _derive_passed(self):
        ran = [i for i in self.instances if i.ran]
        kinds = {i.instance_kind for i in ran}
        reasons: list[str] = []
        if len(ran) < self.min_instances:
            reasons.append(f"only {len(ran)} of {self.min_instances} required instances ran")
        if len(kinds) < 2:
            reasons.append(f"ran on {sorted(kinds) or 'no'} instance kind; "
                           "cross-instance validation needs >= 2")
        if any(i.success.value is False for i in ran):
            reasons.append("an instance failed the effect check")
        if any(i.unsafe for i in ran):
            reasons.append("an instance reported an unsafe finding")
        self.passed = not reasons
        self.reasons = reasons
        return self

    def kinds_covered(self) -> list[str]:
        return sorted({i.instance_kind for i in self.instances if i.ran})


class SkillCandidate(Record):
    """A proposed skill between proposal and library.

    `status` only reaches `library` through a passed `SkillValidationReport`; the
    transition is asserted here so no caller can forget the gate."""

    candidate_id: str = Field(default_factory=lambda: new_id("cand"))
    gap_id: Optional[str] = None
    spec: SkillSpec
    proposed_by: str = "model"
    prompt_sha256: str = ""
    attempts: int = 1
    status: Literal["proposed", "sandbox", "validated", "rejected", "library",
                    "expired"] = "proposed"
    validation_report_id: Optional[str] = None
    rejection_reasons: list[str] = Field(default_factory=list)
    source_text: str = ""

    @model_validator(mode="after")
    def _gate(self):
        if self.status in ("validated", "library") and self.validation_report_id is None:
            raise ValueError(f"status {self.status!r} requires a validation report id")
        return self


# ------------------------------------------------------------ 5.1 perception -----


class Detection(StrictModel):
    entity_id: str = ""
    kind: str = ""
    state_attributes: dict[str, Any] = Field(default_factory=dict)
    pose: Optional[Any] = None
    extent: Optional[Any] = None
    confidence: float = 0.0
    evidence_ref: Optional[str] = None
    # How the box got a name: `segmented | labelled | matched`. Recording this is what
    # makes a mislabel traceable instead of mysterious (§11: semantic grounding accuracy).
    how_identified: str = ""


class SpatialRelation(StrictModel):
    subject_id: str = ""
    predicate: Literal["on", "in", "under", "next_to", "facing", "inside", "supports",
                       "blocks", "unknown"] = "unknown"
    related_id: str = ""
    confidence: float = 0.0
    evidence_ref: Optional[str] = None


class VisibilityRecord(StrictModel):
    entity_id: str = ""
    visible: Union[bool, Unknown] = "unknown"
    occluded_by: Optional[str] = None
    view: str = ""
    confidence: float = 0.0
    evidence_ref: Optional[str] = None


class RegionRecord(StrictModel):
    region_id: str = ""
    kind: str = ""
    center: Optional[Vec3] = None
    half_xy: Optional[Vec3] = None
    free: Union[bool, Unknown] = "unknown"
    occupants: list[str] = Field(default_factory=list)
    capacity_note: str = ""
    confidence: float = 0.0
    evidence_ref: Optional[str] = None


class ChangeRecord(StrictModel):
    """Something the percept says is different from the previous percept.

    `modality` is `vlm | sensor | privileged | text`. The split is the whole content
    of §5.1's "禁止直接使用 simulator hidden state 代替 VLM 主路径": a change only the
    simulator knows about is not a perception result, and labelling it `sensor` (what
    a deterministic pixel algorithm found) or `privileged` (what a state read found)
    is what lets §11's state-change metric count only the reported ones. An
    unlabelled mix of the two would be unmeasurable."""

    subject_id: str = ""
    attribute: str = ""
    before: str = ""
    after: str = ""
    detected_at: float = Field(default_factory=now_wall)
    confidence: float = 0.0
    modality: str = "vlm"
    evidence_ref: Optional[str] = None


class UncertaintyItem(StrictModel):
    what: str = ""
    why: Literal["not_visible", "low_confidence", "conflicting_evidence", "not_measured",
                 "stale"] = "not_measured"
    would_resolve: str = ""      # e.g. an observation skill or a viewpoint
    evidence_refs: list[str] = Field(default_factory=list)


class PerceptionObservation(Record):
    """One percept, with its own identity and timestamps (SPEC-v0.2 §5.1).

    The six required capabilities are the six field groups: `detections` (objects and
    attributes), `relations` (spatial), `visibility` (seen/hidden/occluded), `regions`
    (target areas and occupancy), `changes` (state-change detection) and `uncertainties`
    (unknown marking). A percept that cannot fill a group says so in `uncertainties`;
    it does not leave the group empty as if the answer were "nothing there"."""

    observation_id: str = Field(default_factory=lambda: new_id("per"))
    modality: Literal["rgb", "rgbd", "text", "privileged"] = "rgb"
    frame_index: int = 0
    image_ref: Optional[str] = None
    depth_ref: Optional[str] = None
    camera_id: Optional[str] = None
    timestamp_sim: float = 0.0
    timestamp_wall: float = Field(default_factory=now_wall)
    detections: list[Detection] = Field(default_factory=list)
    relations: list[SpatialRelation] = Field(default_factory=list)
    visibility: list[VisibilityRecord] = Field(default_factory=list)
    regions: list[RegionRecord] = Field(default_factory=list)
    changes: list[ChangeRecord] = Field(default_factory=list)
    uncertainties: list[UncertaintyItem] = Field(default_factory=list)
    raw_text: str = ""
    model: str = ""
    model_return_id: Optional[str] = None
    prompt_sha256: str = ""
    latency_s: float = 0.0
    tokens: dict[str, int] = Field(default_factory=dict)
    source: Source = Source.sensor


# ------------------------------------------------------------------ task input ----


class TaskUnderstanding(Record):
    """The first step of §4's diagram, kept as its own record.

    `ambiguous_parts` is what a clarification request is later checked against: §11 asks
    whether the agent asked about the right thing, which needs the ambiguity on record
    before any model answered."""

    understanding_id: str = Field(default_factory=lambda: new_id("und"))
    task_ref: Optional[str] = None
    utterance: str = ""
    normalized_goal: str = ""
    entities_mentioned: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    implicit_requirements: list[str] = Field(default_factory=list)
    ambiguous_parts: list[str] = Field(default_factory=list)
    success_described_by_agent: list[str] = Field(default_factory=list)
    model: str = ""
    prompt_sha256: str = ""
    wall_s: float = 0.0


# ------------------------------------------------------- ablation registry -----

# SPEC-v0.2 §9 names seven capabilities in the main system and seven arms in its core
# ablation list: `full`, plus one `w/o` arm for each capability except skill retrieval.
# Kept in one place so an arm is a named registry entry rather than an `if` inside the
# loop (principle 3.4: the runtime never silently picks a strategy; principle 3.7: every
# capability must be switchable and measurable).
MODULE_NAMES = (
    "vlm", "planning", "working_memory", "episodic_memory",
    "replanning", "skill_retrieval", "skill_acquisition",
)

ABLATION_CONDITIONS: dict[str, tuple[str, ...]] = {
    "full": (),
    "wo_vlm": ("vlm",),
    "wo_planning": ("planning",),
    "wo_working_memory": ("working_memory",),
    "wo_episodic_memory": ("episodic_memory",),
    "wo_replanning": ("replanning",),
    "wo_skill_acquisition": ("skill_acquisition",),
}

# Which capability produced which record. `ablation` and the skill-execution records are
# owned by no ablatable module, so they appear in every arm.
EVENT_MODULE: dict[str, Optional[str]] = {
    "perception": "vlm",
    "task_understanding": "planning",
    "plan": "planning",
    "plan_revision": "planning",
    "working_memory": "working_memory",
    "obligation_check": "working_memory",
    "memory_write": "episodic_memory",
    "memory_retrieval": "episodic_memory",
    "memory_use": "episodic_memory",
    "recovery_action": "replanning",
    "skill_gap": "skill_acquisition",
    "skill_candidate": "skill_acquisition",
    "skill_validation": "skill_acquisition",
    "skill_library": "skill_acquisition",
    "ablation": None,
}


class Ablation(Record):
    """The arm an episode ran in, as data.

    The pair must match the registry exactly: an arm that claims to be `wo_vlm` while
    also switching something else off is a different experiment and needs its own entry.
    `violations()` then checks the claim against what the episode actually produced."""

    condition: str = "full"
    modules_off: list[str] = Field(default_factory=list)
    registry_sha256: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def _matches_registry(self):
        if self.condition not in ABLATION_CONDITIONS:
            raise ValueError(f"unknown ablation condition {self.condition!r}; "
                             f"add it to ABLATION_CONDITIONS first")
        if self.modules_off != list(ABLATION_CONDITIONS[self.condition]):
            raise ValueError(
                f"condition {self.condition} turns off "
                f"{list(ABLATION_CONDITIONS[self.condition])}, not {self.modules_off}")
        return self

    def enabled(self, module: str) -> bool:
        return module not in self.modules_off

    def violations(self, event_types) -> list[str]:
        """Event types an episode produced while their owning module was switched off.

        Feed it the schema-4 events (`EpisodeStore.read_all(SCHEMA_VERSION)`), not the
        whole log. An event type outside the vocabulary is then an error rather than a
        pass: skipping what the registry does not know would let a new record type escape
        attribution."""
        seen = set()
        for e in event_types:
            name = str(e)
            if name not in EVENT_MODULE:
                raise ValueError(f"{name!r} is not a v0.2 event type; attribute it in "
                                 f"EVENT_MODULE before measuring an arm against it")
            seen.add(name)
        return sorted(e for e in seen if EVENT_MODULE[e] in self.modules_off)


def ablation(condition: str, **kw) -> "Ablation":
    return Ablation(condition=condition,
                    modules_off=list(ABLATION_CONDITIONS.get(condition, ())), **kw)


def registry_sha256() -> str:
    import hashlib

    payload = "|".join(f"{k}={','.join(v)}" for k, v in sorted(ABLATION_CONDITIONS.items()))
    payload += "||" + "|".join(MODULE_NAMES)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ freeze -----

# The event vocabulary v0.2 adds to the v0.1 log. Named here so the loop, the writer
# and the reader agree, and so "the closed loop ran" is checkable as a set of records
# that must appear rather than as a claim (SPEC-v0.2 §15).
V02_EVENT_TYPES = (
    "task_understanding",       # §4 Task Understanding
    "plan",                     # a TaskPlan version, accepted
    "plan_revision",            # why it changed (§5.2: a work object, not a script)
    "perception",               # a PerceptionObservation (§5.1)
    "working_memory",           # a WorkingMemoryState snapshot (§5.3)
    "memory_write",             # an EpisodicExperience stored at episode end (§5.4)
    "memory_retrieval",         # a RecalledExperience offered to the model
    "memory_use",               # judged after the fact: was it used / contradicted
    "skill_gap",                # §5.6's first box: the library ran out, as data
    "skill_candidate",          # a proposed SkillSpec
    "skill_validation",         # a SkillValidationReport, passed or not
    "skill_library",            # a candidate admitted after cross-instance validation
    "recovery_action",          # a model-chosen recovery (§5.7), not a runtime branch
    "obligation_check",         # unresolved obligations at a termination boundary
    "ablation",                 # which modules were off for this episode (§9)
)

RECORD_NAMES = (
    "TaskPlan", "Subgoal", "Dependency", "WorkingMemoryState", "EpisodicExperience",
    "SkillSpec", "SkillCandidate", "SkillValidationReport", "PerceptionObservation",
)


def record_classes() -> dict[str, type]:
    """Every v0.2 record class, including the sub-records §6 does not name."""
    import sys

    namespace = vars(sys.modules[__name__])
    return {name: obj for name, obj in namespace.items()
            if isinstance(obj, type) and obj is not Record and issubclass(obj, Record)}


def schema_fingerprint() -> dict[str, str]:
    """Hash of the contract text and of the record surface, for a freeze file.

    `records_sha256` covers the nine records §6 names; `all_records_sha256` covers those
    plus every sub-record that descends from `Record`, by field name, so adding a field
    to `Subgoal` moves a hash. The plain (envelope-less) helpers such as
    `ValidationInstance` are covered by `module_sha256`, which hashes the source text: a
    later edit cannot be described as "the same schema" — the fingerprint moves, and a
    run recorded against the old one cannot be pooled with the new."""
    import hashlib
    import inspect
    import sys

    source = inspect.getsource(sys.modules[__name__])
    surface = "|".join(f"{name}({','.join(cls.model_fields)})"
                       for name, cls in sorted(record_classes().items()))
    return {
        "schema_version": SCHEMA_VERSION,
        "v01_schema_version": V01_SCHEMA_VERSION,
        "module_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "records_sha256": hashlib.sha256(
            "|".join(RECORD_NAMES).encode("utf-8")).hexdigest(),
        "all_records_sha256": hashlib.sha256(surface.encode("utf-8")).hexdigest(),
        "event_types_sha256": hashlib.sha256(
            "|".join(V02_EVENT_TYPES).encode("utf-8")).hexdigest(),
        "ablation_registry_sha256": registry_sha256(),
    }


__all__ = [
    "SCHEMA_VERSION", "V02_EVENT_TYPES", "RECORD_NAMES", "RECORD_FIELDS",
    "MODULE_NAMES", "ABLATION_CONDITIONS", "EVENT_MODULE", "Ablation", "ablation",
    "registry_sha256", "schema_fingerprint", "record_classes",
    "Record", "Status", "Unknownable", "TaskUnderstanding",
    "Subgoal", "DependencyKind", "Dependency", "PlanRevision", "TaskPlan",
    "Commitment", "SuspendedRelation", "FailedAttempt", "Assumption",
    "WorkingMemoryState",
    "OutcomeLabel", "ExperienceStep", "EpisodicExperience", "RecalledExperience",
    "SkillParameter", "ProcedureIntent", "ProcedureStep", "SkillSpec", "SkillGap",
    "ValidationInstance", "SkillValidationReport", "SkillCandidate",
    "Detection", "SpatialRelation", "VisibilityRecord", "RegionRecord", "ChangeRecord",
    "UncertaintyItem", "PerceptionObservation",
    "Source", "StrictModel", "Unknown", "Vec3",
]
