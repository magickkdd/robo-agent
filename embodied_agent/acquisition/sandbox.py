"""SPEC-v0.2 §5.6 steps 2-4: run the proposed program, and let the *verifier* say whether it worked.

§5.6's middle of the pipeline is `parameterized program → sandbox / simulator execution → verifier`.
The reason it is written as three boxes rather than one is that a skill author can always make a
program *look* successful, and the only remedy is that the thing which decides is a different piece
of code with a different authority. So this module's job is narrower than "run a skill": it runs a
program on a world it built itself, asks the shipped `RuntimeVerifier` the question the program's
own `expected_effects` field asks, and reports what came back — including when what came back is
"this never should have been allowed to be tried".

Four prohibitions, each implemented as a structure rather than as a check that runs after the fact:

* **No teleport.** The only handle on physics in this module is `SkillExecutor.execute(call)`, and
  the only skills a program may name are the four shipped ones (`acquisition.program.check` refuses
  every other name, including the ALFWorld text verbs). Direct actuation is a `pybullet` call, and
  this module does not import `pybullet` — so there is no call it could make. `core/scene.py` does,
  and the presence of the forbidden names *there* is asserted in the contract tests, so the claim
  cannot be satisfied by a list of tokens that exist nowhere.
* **No writing the episode's world.** `run_program` takes a `SandboxScene`, which is a *description*
  of a world (seed, layout, verify config) and not a world. The scene it opens is built here and
  closed here; nothing in this module can be handed a live `PhysicsScene` because no parameter of
  `run_program` accepts one. That is the difference between "we asked nicely" and "the argument does
  not exist".
* **No reading hidden state, and no editing the evaluator.** The snapshot comes from
  `build_world_state` — the same channel the loop itself uses — and the answer key is unreachable by
  name: `SandboxScene.from_task` reads `seed`, `objects` and `verify` off a frozen case and nothing
  else, so `eval_spec`, `expected` and `events` never enter. The module also imports no `Runtime`,
  no `EpisodeStore` and no ledger reader, so a sandbox run cannot write an episode record either.
* **No unattributed scene mutation.** Every attribute the executor reaches on the scene goes through
  `_AuditedScene`, which records *which file and function asked*. Mutating a body from
  `acquisition/sandbox.py` is therefore not a rule that can be broken quietly — it is a row in
  `ProgramRun.audit`, and `audit_findings` turns any writer reached from outside the three shipped
  implementation files into a violation that invalidates the run.

What counts as a result (§12.3: 以实际动作与终态为准):

| field | means |
|---|---|
| `refusals` | the representation gate's or `PlanValidator`'s own reasons; nothing executed, so the run is `ran=False` and §12.4 forbids it carrying a verdict |
| `terminated_by` | `procedure_complete`, `step_abort`, `retry_spent`, `call_cap`, `representation_gate`, `plan_validator`, `binding_error` |
| `effects` | one row per declared `expected_effect`, bound and asked of `RuntimeVerifier`; `true` / `false` / `unknown` with the report's own evidence numbers |
| `violations` | audit findings — evidence that the run left its authority, not that the skill was hard |
| `unsafe` | the executor's own physical-disturbance codes: `ANOMALOUS_CONTACT`, `OBJECT_DROPPED`, `OBJECT_RELEASE_UNCONFIRMED` |

§11 rows fed: `validation success` (instances whose every declared effect verified `true`), `cross-
instance transfer` (via `validation_instance`, which is what `SkillValidationReport` counts), and
`unsafe-invalid skill rate` (the `unsafe`/`violations` lists, kept apart because a collision is a
physical finding and an audit violation is an integrity finding — pooling them would let a framework
bug read as a hard scene).
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional, Sequence

from ..core.contracts import (PredicateVerdict, SkillCall, SkillResult, SkillStatus, Source,
                              VerifyConfig)
from ..core.planner import PlanValidator
from ..core.skills import SkillExecutor
from ..core.v02 import Unknownable, ValidationInstance, SkillSpec
from . import program
from .gap import EFFECT_PREDICATE

# ------------------------------------------------------------------ the gate ----
#: Scene attributes that move or release something. `SkillExecutor`'s `_do_pick`/`_do_place` and
# `PlacementPlanner` are the shipped code that legitimately uses them; a call from anywhere else is
# not "running a skill", it is the sandbox reaching past one.
SCENE_WRITERS = ("move_ee", "move_ee_straight", "move_joints", "reset_arm", "open_gripper",
                 "close_gripper", "hold_position", "apply_environment_impulse", "settle",
                 "start_action", "end_action", "begin_execution", "end_execution")

#: The files whose functions may reach a writer. `scene.py` is in the list because its own internal
# calls (`_tick` → `move_ee`) arrive from inside the object being audited.
WRITER_SITES = ("skills.py", "placement_planner.py", "scene.py")

#: Names whose presence in *this module's code* would mean one of §5.6's prohibitions was
# implemented rather than merely described. Each has a known home in `FORBIDDEN_ORIGINS`, and the
# contract tests assert both halves — "absent here" only means something if "used there" does too.
#
# `source_gates` scans the text between the two markers below, because a token listed here is by
# definition present in this file: a scan of the whole module would report its own vocabulary as a
# violation, and the honest fix is to name the region that is being held to the standard.
FORBIDDEN_IN_SANDBOX = ("import pybullet", "resetBasePositionAndOrientation", "changeDynamics",
                        "eval_spec", "episode_summary", "events.jsonl", "EpisodeStore",
                        "run_episode(")

#: Where the held region ends. The two gate functions must live after it — they name the forbidden
# tokens, so they cannot also be inside the text searched for them — and the end marker is not an
# escape hatch: `TAIL_MUST_NOT_NAME` is asserted against the tail, so nothing that can touch a world
# may be moved past it.
SCAN_TO = "# end-scanned-code"

#: Names whose appearance in the *unscanned* tail would mean the gate's own region was shrunk to hide
# an actuator. Everything here is a handle on a world, and every handle is in the scanned region.
TAIL_MUST_NOT_NAME = ("SkillExecutor", "PhysicsScene", "build_world_state", "_AuditedScene(",
                      "scene.open(")

FORBIDDEN_ORIGINS = {"resetBasePositionAndOrientation": "evaluation/long_horizon_tasks.py",
                     "changeDynamics": "core/scene.py",
                     "import pybullet": "core/scene.py",
                     "eval_spec": "evaluation/tasks.py",
                     "episode_summary": "evaluation/run.py",
                     "events.jsonl": "core/events.py",
                     "EpisodeStore": "core/events.py",
                     "run_episode(": "core/runtime.py"}

SCAN_FROM = "# scanned-code"

#: The executor's own codes for "the world was disturbed in a way nobody asked for". These are
# `unsafe` rather than `failed`: §11's unsafe-invalid-skill row is about the skill being dangerous,
# and a `GRASP_MISS` is not dangerous, it is just not working.
UNSAFE_CODES = ("ANOMALOUS_CONTACT", "OBJECT_DROPPED", "OBJECT_RELEASE_UNCONFIRMED")

#: `SkillStatus` values that mean the actuation finished as asked. Only one: `uncertain` is §5.8's
# honest "it moved, the postcondition was not measured", and the sandbox settles that question with
# its own effect check rather than by counting it as progress. (`finish_accepted` is a *feedback*
# status the runtime emits for an accepted finish — `core/runtime.py:442` — not a member of this
# enum, which is why it is absent here.)
COMPLETE_STATUSES = (SkillStatus.completed.value,)

SANDBOX_OBS_PREFIX = "sandbox_obs_"

#: Nothing in §5.6's middle three boxes is a model call: the program is already written, the world is
# a frozen case, and the verdict comes from the verifier. So `model_calls == 0` is a structural fact
# about the imports rather than a counter a run could get wrong, and these are the channel names whose
# absence makes it one.
MODEL_CHANNELS = ("adapters", "openai", "httpx", "requests", "anthropic", "vertexai", "gemini")

#: The four §5.6 event types, owned by `skill_acquisition` in `core/v02.py:EVENT_MODULE`. This module
# files none of them: it returns a `ProgramRun`, and the runtime owner writes the record. `P4-d` is
# where that owner appears, and the event vocabulary stays exactly the frozen four until it does.
SANDBOX_EVENT_TYPES = ("skill_gap", "skill_candidate", "skill_validation", "skill_library")

#: Names that would mean this module wrote an episode record. It has no `Runtime`, no store and no
# event channel, so a hit here is someone wiring the sandbox to the archive directly.
EVENT_WRITERS = ("_file(", "emit(", "write_event(", "append_event(", "Event(")


class SandboxError(RuntimeError):
    """A sandbox that could not be built. Reported, never half-run."""

# scanned-code


# ------------------------------------------------------------------ the world ----
@dataclass(frozen=True)
class SandboxScene:
    """A *description* of a world to build: seed, layout, verification config.

    Frozen, and holding no scene. This is what makes "the acquired skill was tested on an unseen
    object" a statement about data (`variant(...)`) rather than about an object that two callers could
    share and one of them could move a body in.
    """

    label: str
    seed: int
    objects: tuple[dict[str, Any], ...]
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    origin: str = "declared layout"

    @classmethod
    def from_task(cls, task_id: str) -> "SandboxScene":
        """The frozen case's *public* scene: seed, object layout, verify config.

        Importantly not the declared disruptions (those are episode state), not `budgets` (the
        runtime's concern) and not the case's evaluation spec or its `expected` label — the third
        layer, which §12.6 keeps away from the agent. The layout is copied, because `PhysicsScene`
        reads it once at construction and a caller must not be able to edit a case's objects through
        a sandbox run.
        """
        from ..evaluation.tasks import SET_NAMES, find_case
        # `find_case`'s own default is the *pre-registered* sets, which are v0.1's four. A sandbox
        # that could only reproduce those could not reproduce the tasks this phase is run on — P2's
        # `long_horizon` and P3's `em` sets came after the freeze and have their own manifests — so
        # the search is over every declared set. The cost is a list walk over in-memory case
        # builders; the alternative is a pipeline that refuses to sandbox the episode it just ran.
        case = find_case(task_id, sets=SET_NAMES)
        return cls(label=task_id, seed=int(case.seed),
                   objects=tuple(dict(o) for o in case.objects),
                   verify=case.verify, origin=f"frozen case {task_id}")

    def variant(self, *, label: Optional[str] = None, seed: Optional[int] = None,
                objects: Optional[Sequence[dict[str, Any]]] = None) -> "SandboxScene":
        """A derived layout, with its own `origin` string saying what changed.

        P4-c's cross-instance gate turns on this: "unseen object" and "unseen layout" have to be
        different rows, and a row that cannot be traced back to which field was changed is not
        evidence about either.
        """
        changed = []
        if seed is not None:
            changed.append(f"seed={seed}")
        if objects is not None:
            changed.append(f"{len(objects)} objects")
        # The layout is copied again, not passed through: a variant that shared its parent's dicts
        # could be edited into a different world through either handle, and "unseen layout" would stop
        # being a statement about data.
        layout = self.objects if objects is None else objects
        return replace(self, label=label or f"{self.label}#{'+'.join(changed) or 'copy'}",
                       seed=self.seed if seed is None else seed,
                       objects=tuple(dict(o) for o in layout),
                       origin=f"variant of {self.origin}: {', '.join(changed) or 'nothing'}")

    def entity_ids(self) -> list[str]:
        """The movable bodies the layout declares. Trays are not here: `PhysicsScene` builds those
        itself, which is why a run records `targets_seen` from its first snapshot rather than trusting
        a description that does not mention them."""
        return [str(o.get("entity_id")) for o in self.objects if o.get("entity_id")]

    def open(self):
        """Build the scene. Private in spirit: callers get a run, never this object."""
        from ..core.scene import PhysicsScene  # lazy, and the only place a scene is made here
        return PhysicsScene(seed=self.seed, object_layout=[dict(o) for o in self.objects],
                            gui=False)


class _AuditedScene:
    """A forwarding proxy that records every attribute the executor asked for, and from where.

    Attribute *writes* are logged the same way; the sandbox has no reason to set a scene attribute,
    and `setattr` lines in the log are read as violations by `audit_findings`.
    """

    def __init__(self, scene: Any, log: list[dict[str, str]]):
        object.__setattr__(self, "_scene", scene)
        object.__setattr__(self, "_log", log)

    def _record(self, name: str, kind: str) -> None:
        frame = sys._getframe(2)
        self._log.append({"attr": f"{kind}:{name}", "site": f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_code.co_name}"})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name in ("_scene", "_log"):
            return getattr(object.__getattribute__(self, "_scene"), name)
        self._record(name, "get")
        return getattr(object.__getattribute__(self, "_scene"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        self._record(name, "set")
        setattr(object.__getattribute__(self, "_scene"), name, value)


def audit_findings(log: Sequence[dict[str, str]]) -> list[str]:
    """What the audit says is out of authority — empty for a run that only ever asked the skills.

    Rows are stored qualified (`get:move_ee`, `set:sim_time`), so the kind is split off before the
    name is compared against `SCENE_WRITERS`. That split is the whole mechanism: a check that matched
    the qualified string against a bare attribute name would find nothing on any log, including the
    one where the sandbox teleports a body.
    """
    out: list[str] = []
    for entry in log:
        kind, _, name = str(entry["attr"]).partition(":")
        if kind == "set":
            out.append(f"the sandbox wrote scene attribute {name} from {entry['site']} "
                       f"(row {entry['attr']!r})")
            continue
        if kind != "get" or name not in SCENE_WRITERS:
            continue
        if entry["site"].split(":")[0] not in WRITER_SITES:
            out.append(f"scene writer {name} reached from {entry['site']}, outside "
                       f"{list(WRITER_SITES)}")
    return sorted(set(out))


# ------------------------------------------------------------------ the run ----
@dataclass
class StepRecord:
    """One executed call, as the executor reported it."""

    step_id: str
    skill: str
    args: dict[str, str]
    attempt: int
    status: str
    failure_code: Optional[str]
    sim_seconds_used: float
    stages_executed: list[str]
    notes: list[str]
    pre_observation_ref: Optional[str]
    post_observation_ref: Optional[str]
    held_state: Any = None
    #: The verifier's answer about *this step's* effect, on the snapshot taken right after it, with
    # the snapshot from right before it as the comparison. §12.3 asks for the actual action and the
    # terminal state; this is the actual-action half, and `ProgramRun.effects` is the terminal half.
    effect: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ProgramRun:
    """Everything one sandbox execution of one program produced, including its refusal."""

    spec_name: str
    skill_id: str
    level: str
    plan_id: str
    params: dict[str, Any]
    scene_label: str
    scene_seed: int
    scene_origin: str
    accepted: bool = False
    terminated_by: str = "not_started"
    refusals: list[str] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    effects: list[dict[str, Any]] = field(default_factory=list)
    calls_used: int = 0
    call_cap: int = 0
    audit: list[dict[str, str]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)
    wall_s: float = 0.0
    sim_s: float = 0.0
    state_versions: list[int] = field(default_factory=list)
    #: What the first snapshot of this world actually contained. Recorded because P4-c's claim is
    # "an unseen object", and an object the layout never offered is not unseen, it is absent.
    entities_seen: list[str] = field(default_factory=list)
    targets_seen: list[str] = field(default_factory=list)
    closed: bool = False

    @property
    def ran(self) -> bool:
        """Whether a call actually reached the executor.

        §12.4 makes this the load-bearing property: `SkillValidationReport` may not count an
        instance that never executed, and an instance that never executed may not carry effects — so
        a refusal stays a refusal all the way into the gate instead of becoming a soft "failure".
        """
        return bool(self.steps)

    def measured(self, value: str = PredicateVerdict.true.value) -> list[str]:
        return [str(e["predicate_id"]) for e in self.effects if e["value"] == value]

    def unmeasured(self) -> list[str]:
        """The declared claims this run could not measure, each one qualified by *why*.

        One row per unmeasured claim rather than two, so §11's "how much of the gate was actually
        measured" has a denominator that is a count of claims and not a count of strings. A claim the
        verifier answered no reason for still appears, bare — an absence with no explanation is itself
        the finding.
        """
        out: list[str] = []
        for e in self.effects:
            if e["value"] != "unknown":
                continue
            reasons = [str(u) for u in (e.get("unmeasured") or [])]
            out.extend([f"{e['predicate_id']}:{r}" for r in reasons] or [str(e["predicate_id"])])
        return sorted(set(out))

    def outcome(self) -> tuple[Any, list[str]]:
        """`(True | False | None, reasons)` — the effect check, and nothing else. `None` is unknown.

        Deliberately not "did the steps succeed": a program that ran two clean `pick`s and whose
        declared effect is `placed:x:y` did not do what it claims. And the two negatives are kept
        apart because they mean different things to §11 — `false` is a measurement that disagrees
        (a failure), `unknown` is a measurement that did not happen (§5.1: absence is a name, never a
        placeholder verdict). An audit violation is neither: it says this run's measurements are not
        about a skill at all.
        """
        if not self.ran:
            return None, [f"nothing executed ({self.terminated_by})"]
        if self.violations:
            return None, list(self.violations)
        reasons = list(self.unsafe)
        if not self.effects:
            return None, ["the spec declares no expected_effect, so nothing was checked"]
        false = [e for e in self.effects if e["value"] == PredicateVerdict.false.value]
        unknown = [e for e in self.effects if e["value"] == "unknown"]
        reasons += [f"{e['predicate_id']}: measured {e['description'].lower()}" for e in false]
        reasons += [f"{e['predicate_id']}: unmeasurable ({'; '.join(e['unmeasured']) or 'no verdict'})"
                    for e in unknown]
        if self.terminated_by != "procedure_complete":
            reasons.append(f"the procedure stopped early ({self.terminated_by})")
        if false or self.unsafe or self.terminated_by != "procedure_complete":
            return False, reasons
        return (None, reasons) if unknown else (True, reasons)

    def as_dict(self) -> dict[str, Any]:
        value, reasons = self.outcome()
        return {k: v for k, v in self.__dict__.items()} | {
            "ran": self.ran,
            "outcome": "unknown" if value is None else ("success" if value else "failure"),
            "outcome_reasons": reasons,
            "steps": [s.as_dict() for s in self.steps],
        }


# ---------------------------------------------------------------- verification ----
def bind_effect(template: str, params: Mapping[str, Any]) -> tuple[Optional[str], list[str]]:
    """`placed:{object}:{target}` + {object: obj_a, target: tray_b} → the concrete predicate.

    Whole-token substitution, the same rule `SkillSpec.bind` uses for call arguments, so a template
    cannot bind one half of a name and produce a predicate no verifier will ever be asked about.
    """
    unresolved = [m.group(1) for m in re.finditer(r"\{(\w+)\}", str(template))
                  if params.get(m.group(1)) in (None, "")]
    if unresolved:
        return None, sorted(set(unresolved))
    return re.sub(r"\{(\w+)\}", lambda m: str(params[m.group(1)]), str(template)), []


def check_effects(spec: SkillSpec, params: Mapping[str, Any], world: Any,
                  config: VerifyConfig,
                  before_by_entity: Optional[Mapping[str, Any]] = None) -> list[dict[str, Any]]:
    """Ask the shipped verifier each effect the spec claims, on the final snapshot.

    Three cases, and only the first can produce `true`: an id the verifier answers (`grasp:…`,
    `placed:…:…`), an id whose parameter never bound, and a claim in a vocabulary no measurement
    answers. `before_by_entity` carries the snapshot from before each entity's first pick, because
    lift gain is a *comparison* and `verify_grasp` says `unknown` without one — a terminal `grasp`
    claim after a successful place measures `false` with numbers, which is the correct answer to a
    program that declared an intermediate state as its end state.
    """
    verifier = RuntimeVerifierOf(world, config)
    out: list[dict[str, Any]] = []
    for template in spec.expected_effects:
        effect, unresolved = bind_effect(template, params)
        if effect is None:
            out.append({"predicate_id": str(template), "value": "unknown",
                        "description": "template parameter never bound",
                        "evidence": {}, "evidence_refs": [], "source": str(verifier.source),
                        "unmeasured": [f"unbound_parameter:{u}" for u in unresolved]})
            continue
        out.append(verifier.answer(effect,
                                   before=(before_by_entity or {}).get(_effect_entity(effect))))
    return out


def _effect_entity(effect: str) -> str:
    parts = str(effect).split(":")
    return parts[1] if len(parts) > 1 else ""


class RuntimeVerifierOf:
    """One place where a predicate id becomes a question to `core/verify.py`.

    Wrapped rather than called directly because the id has to be *parsed* to know which method to
    ask, and that parsing is the step that could quietly start answering a claim the verifier was
    never asked about. `answer` refuses to guess: an unknown stem reports `unknown`.
    """

    def __init__(self, world: Any, config: VerifyConfig):
        from ..core.verify import RuntimeVerifier
        self._verifier = RuntimeVerifier(world, config, source=Source.privileged)
        self.source = Source.privileged

    def answer(self, predicate_id: str, before: Any = None) -> dict[str, Any]:
        stem = str(predicate_id).split(":", 1)[0]
        parts = str(predicate_id).split(":")
        report = None
        if stem == EFFECT_PREDICATE["pick"] and len(parts) == 2:
            report = self._verifier.verify_grasp(parts[1], before=before)
        elif stem == EFFECT_PREDICATE["place"] and len(parts) == 3:
            report = self._verifier.verify_placement(parts[1], parts[2])
        if report is None:
            return {"predicate_id": predicate_id, "value": "unknown",
                    "description": f"no verifier predicate answers the stem {stem!r}",
                    "evidence": {}, "evidence_refs": [], "source": str(self.source),
                    "unmeasured": [f"unanswerable_stem:{stem}"]}
        return {"predicate_id": report.predicate_id, "value": str(report.value.value),
                "description": report.description,
                "evidence": {k: float(v) for k, v in (report.evidence or {}).items()},
                "evidence_refs": list(report.evidence_refs or []), "source": str(report.source),
                "unmeasured": list(report.unmeasured or [])}


def step_effect(call: SkillCall, before: Any, after: Any,
                config: VerifyConfig) -> dict[str, Any]:
    """The verifier's word on one executed step, measured between the two snapshots around it."""
    stem = EFFECT_PREDICATE.get(str(call.skill))
    entity = call.args.get("object_id")
    if stem is None or entity is None:
        return {"predicate_id": f"{call.skill}:{entity or '?'}", "value": "unknown",
                "description": f"{call.skill} declares no predicate the verifier answers",
                "evidence": {}, "evidence_refs": [], "source": str(Source.privileged),
                "unmeasured": ["no_verifier_predicate"]}
    effect = (f"{stem}:{entity}" if stem == EFFECT_PREDICATE["pick"]
              else f"{stem}:{entity}:{call.args.get('target_id') or '?'}")
    return RuntimeVerifierOf(after, config).answer(effect, before=before)


# ------------------------------------------------------------------ the runner ----
def run_program(spec: SkillSpec, params: Mapping[str, Any], scene: SandboxScene, *,
                plan_id: Optional[str] = None,
                call_cap: Optional[int] = None) -> ProgramRun:
    """Execute one bound program on a world built from `scene`, then verify what it claims.

    Nothing here decides *what to try*: the procedure's order, its `on_failure` values and its
    argument placeholders were all written in the candidate, and the only choice made during a run
    is whether the executor's own budget was spent. That is the §9 constraint, held even in the
    module whose subject is skill creation.
    """
    plan_id = plan_id or f"sandbox_{spec.name}_{scene.label}"
    run = ProgramRun(spec_name=spec.name, skill_id=spec.skill_id, level=spec.level,
                     plan_id=plan_id, params=dict(params), scene_label=scene.label,
                     scene_seed=scene.seed, scene_origin=scene.origin,
                     call_cap=int(call_cap if call_cap is not None else program.max_calls(spec)))

    gate = program.check(spec)
    if gate:
        run.refusals, run.terminated_by = list(gate), "representation_gate"
        return run
    try:
        calls = program.bind_calls(spec, plan_id, params)
        plan = program.as_plan(spec, plan_id, params, based_on_state_version=1,
                              goal_ref=f"sandbox:{scene.label}")
    except program.ProgramError as e:
        run.refusals, run.terminated_by = list(e.reasons), "binding_error"
        return run

    t0 = time.time()
    raw = None
    log: list[dict[str, str]] = []
    try:
        raw = scene.open()
    except Exception as e:  # noqa: BLE001 - an unbuildable world is reported, not run around
        raise SandboxError(f"{scene.label}: sandbox scene would not build: {type(e).__name__}: {e}") from e
    try:
        audited = _AuditedScene(raw, log)
        from ..core.verify import build_world_state
        version = [0]

        def observe():
            version[0] += 1
            return build_world_state(audited, version[0],
                                     f"{SANDBOX_OBS_PREFIX}{version[0]:04d}", scene.verify)

        executor = SkillExecutor(audited, world_provider=observe, config=scene.verify)
        world = observe()
        run.entities_seen = sorted(e.entity_id for e in world.entities)
        run.targets_seen = sorted(t.target_id for t in world.targets)
        ok, reasons = PlanValidator(world, scene.verify).validate(plan)
        if not ok:
            # The production validator's own words, in the run, and nothing executed. A program the
            # validator rejects is a broken candidate, not a failed skill — the distinction §12.4.
            run.refusals, run.terminated_by = list(reasons), "plan_validator"
            run.state_versions.append(world.state_version)
            return run
        run.accepted = True
        run.state_versions.append(world.state_version)

        steps = [s for s in spec.procedure if s.intent is not None]
        #: entity -> the snapshot taken before its first `pick`. Lift gain is a comparison, so this is
        # what makes a grasp claim measurable at all rather than `unknown` (§5.1: absence is a name).
        before_by_entity: dict[str, Any] = {}

        def do_attempt(call: SkillCall, which: int) -> StepRecord:
            before = observe()
            entity = call.args.get("object_id")
            if str(call.skill) == "pick" and entity and entity not in before_by_entity:
                before_by_entity[entity] = before
            result = _execute(executor, call)
            record = _record(call, result, attempt=which,
                             effect=step_effect(call, before, observe(), scene.verify))
            run.calls_used += 1
            run.steps.append(record)
            if record.failure_code in UNSAFE_CODES:
                run.unsafe.append(f"{record.step_id}: {record.failure_code}"
                                  + (" (after retry)" if which > 1 else ""))
            return record

        index = 0
        terminated = "procedure_complete"
        while index < len(steps):
            step, call = steps[index], calls[index]
            if run.calls_used >= run.call_cap:
                terminated = "call_cap"
                break
            if do_attempt(call, 1).status in COMPLETE_STATUSES:
                index += 1
                continue
            if step.on_failure == "retry_once" and run.calls_used < run.call_cap:
                if do_attempt(call, 2).status in COMPLETE_STATUSES:
                    index += 1
                    continue
                terminated = "retry_spent"
                break
            terminated = "step_abort"
            break
        run.terminated_by = terminated
        final = observe()
        run.state_versions.append(final.state_version)
        run.effects = check_effects(spec, params, final, scene.verify, before_by_entity)
        run.sim_s = float(getattr(raw, "sim_time", 0.0) or 0.0)
    finally:
        run.wall_s = round(time.time() - t0, 3)
        run.violations = audit_findings(log)
        run.audit = _compact_audit(log)
        if raw is not None:
            raw.close()
            run.closed = True
    return run


def _execute(executor: SkillExecutor, call: SkillCall) -> SkillResult:
    """One call, one status. Exceptions are the executor's to classify, and it does (§5.8 layer 1)."""
    return executor.execute(call)


def _record(call: SkillCall, result: SkillResult, *, attempt: int,
            effect: dict[str, Any]) -> StepRecord:
    return StepRecord(
        step_id=call.step_id, skill=str(call.skill), args=dict(call.args), attempt=attempt,
        status=str(result.status.value if hasattr(result.status, "value") else result.status),
        failure_code=result.failure_code,
        sim_seconds_used=float(result.sim_seconds_used or 0.0),
        stages_executed=list(result.stages_executed or []), notes=list(result.notes or []),
        pre_observation_ref=result.pre_observation_ref,
        post_observation_ref=result.post_observation_ref, held_state=result.held_state,
        effect=effect)


def _compact_audit(log: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    """`(attr, site) -> count`, sorted. The full log would be thousands of identical getter rows."""
    counts: dict[tuple[str, str], int] = {}
    for entry in log:
        key = (entry["attr"], entry["site"])
        counts[key] = counts.get(key, 0) + 1
    return [{"attr": a, "site": s, "count": n} for (a, s), n in sorted(counts.items())]


# ------------------------------------------------------- the frozen record glue ----
def validation_instance(run: ProgramRun, kind: str, *,
                        evidence_prefix: str = "sandbox") -> ValidationInstance:
    """The run as the one object §5.6's gate reads: a `ValidationInstance`.

    `ran` comes from whether a call reached the executor, so a refusal cannot smuggle a verdict into
    the report; and the reasons for a refusal go in `evidence_refs` rather than `violations`, which
    the frozen model only allows on a run that ran.
    """
    value, _reasons = run.outcome()
    refs = [f"{evidence_prefix}:{run.plan_id}:{s.step_id}#{s.attempt}" for s in run.steps]
    if not run.ran:
        refs = [f"{evidence_prefix}:{run.plan_id}:refused:{run.terminated_by}", *run.refusals]
        # An unrun instance may carry no effects, violations or unsafe findings — enforced by
        # `ValidationInstance._unrun_cannot_have_result`. The refusals are readable in `evidence_refs`.
        return ValidationInstance(instance_kind=kind, scene_id=run.scene_label, seed=run.scene_seed,
                                  args=dict(run.params), ran=False,
                                  success=Unknownable(value="unknown", evidence_refs=refs,
                                                      unmeasured=[run.terminated_by]),
                                  evidence_refs=refs[:4], wall_s=run.wall_s, model_calls=0)
    measured_true = run.measured(PredicateVerdict.true.value)
    return ValidationInstance(
        instance_kind=kind, scene_id=run.scene_label, seed=run.scene_seed, args=dict(run.params),
        ran=True,
        success=Unknownable(value="unknown" if value is None else bool(value),
                            evidence_refs=refs,
                            # only what the verifier could not measure; a `false` is a measurement, so
                            # the failure reasons stay in `violations`-free `run.outcome()` instead
                            unmeasured=run.unmeasured()),
        effects_observed=measured_true, violations=list(run.violations),
        unsafe=list(run.unsafe), evidence_refs=refs[:8], wall_s=run.wall_s, model_calls=0)


def write_run(run: ProgramRun, root: str) -> str:
    """One run as JSON under `root`, for the artifact tree a report can cite.

    The path is returned rather than assumed; §15 asks for a demonstrable loop, and a cited artifact
    that was never written is worse than none.
    """
    os.makedirs(os.path.join(root, "sandbox"), exist_ok=True)
    path = os.path.join(root, "sandbox", f"{run.plan_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run.as_dict(), fh, indent=1, sort_keys=True, default=str)
    return path


def run_census(runs: Sequence[ProgramRun]) -> dict[str, Any]:
    """What a batch of sandbox runs adds up to, with the refusals counted separately from failures."""
    executed = [r for r in runs if r.ran]
    outcomes = [r.outcome() for r in executed]
    return {
        "runs": len(runs),
        "executed": len(executed),
        "refused_before_execution": sum(1 for r in runs if not r.ran),
        "terminated_by": {kind: sum(1 for r in runs if r.terminated_by == kind)
                          for kind in sorted({r.terminated_by for r in runs})},
        "success": sum(1 for v, _ in outcomes if v is True),
        "failure": sum(1 for v, _ in outcomes if v is False),
        "unknown": sum(1 for v, _ in outcomes if v is None),
        "runs_with_violations": sum(1 for r in runs if r.violations),
        "runs_with_unsafe_findings": sum(1 for r in runs if r.unsafe),
        "effects_declared": sum(len(r.effects) for r in runs),
        "effects_verified_true": sum(len(r.measured(PredicateVerdict.true.value)) for r in runs),
        "effects_unmeasurable": sum(len(r.unmeasured()) for r in runs),
        "calls_used": sum(r.calls_used for r in runs),
        "scene_closed_on_every_run": all(r.closed for r in runs),
        "max_call_cap_respected": all(r.calls_used <= r.call_cap for r in runs),
    }

# end-scanned-code


def _marker_lines(marker: str, lines: Sequence[str]) -> list[int]:
    hits = [i for i, line in enumerate(lines) if line.strip() == marker]
    if len(hits) != 1:
        raise SandboxError(f"the scan marker {marker!r} occurs {len(hits)} times, not once; "
                           "the source gate cannot say which region it cleared")
    return hits


def scanned_code() -> str:
    """This module's own executable text, between the two markers — the region held to the gate.

    Markers are matched as whole lines and each must occur exactly once. A substring match would find
    the line that *declares* a marker and quietly move the region, which in a check whose whole job is
    to say "this text is absent" is the difference between a gate and a decoration.
    """
    lines = inspect.getsource(sys.modules[__name__]).splitlines(True)
    start, end = _marker_lines(SCAN_FROM, lines)[0], _marker_lines(SCAN_TO, lines)[0]
    if end <= start:
        raise SandboxError("the scan region is empty: its end marker precedes its start")
    return "".join(lines[start + 1:end])


def unscanned_tail() -> str:
    """Everything after the end marker — the two gate functions, and nothing that touches a world."""
    lines = inspect.getsource(sys.modules[__name__]).splitlines(True)
    return "".join(lines[_marker_lines(SCAN_TO, lines)[0]:])


def _imported_tops(code: str) -> set[str]:
    """The top-level names this region imports, relative-prefix stripped.

    Read from the text rather than from `sys.modules`, because the question is what *this module*
    reaches for; an interpreter that has already loaded a package for somebody else answers a
    different question, and a wrong "no" from that is a wrong "it never spends model calls" from here.
    """
    tops = set()
    for name in re.findall(r"^\s*(?:from|import)\s+([.\w]+)", code, re.MULTILINE):
        top = name.lstrip(".").split(".")[0]
        if top:
            tops.add(top)
    return tops


def source_gates() -> dict[str, Any]:
    """The prohibitions as measured text, so the claim can be re-checked without trusting a test.

    Four parts, because "we did not do it" needs a different kind of support: what was scanned, which
    forbidden names are absent from it *and present in the file that does use them*, which scene
    writers this module never calls, and what the region's own end marker cannot be used to hide.
    """
    code = scanned_code()
    tail = unscanned_tail()
    lives: dict[str, str] = {}
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for token, path in FORBIDDEN_ORIGINS.items():
        try:
            with open(os.path.join(root, "embodied_agent", path), encoding="utf-8") as fh:
                lives[token] = ("present in " if token in fh.read() else "ABSENT from ") + path
        except OSError as e:
            lives[token] = f"unreadable: {e}"
    return {
        "this_module": __name__,
        "scanned_chars": len(code),
        "absent_from_scanned_code": [t for t in FORBIDDEN_IN_SANDBOX if t not in code],
        "present_in_scanned_code": [t for t in FORBIDDEN_IN_SANDBOX if t in code],
        "absent_from_unscanned_tail": [t for t in TAIL_MUST_NOT_NAME if t not in tail],
        "present_in_unscanned_tail": [t for t in TAIL_MUST_NOT_NAME if t in tail],
        "origins": lives,
        "scene_writers_called_here": [w for w in SCENE_WRITERS if f".{w}(" in code],
        "model_channels_imported": sorted({top for top in _imported_tops(code)
                                           if top in MODEL_CHANNELS}),
        "event_writes_here": sorted({w for w in EVENT_WRITERS
                                     if re.search(r"^\s*(?:self\.)?" + re.escape(w), code,
                                                  re.MULTILINE)}),
        "run_program_parameters": list(inspect.signature(run_program).parameters),
        "sandbox_scene_fields": list(SandboxScene.__dataclass_fields__),
        "scene_writers_tracked": list(SCENE_WRITERS),
    }
