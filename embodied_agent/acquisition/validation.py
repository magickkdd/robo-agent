"""SPEC-v0.2 §5.6 step 5: cross-instance validation — the box between the verifier and the library.

The pipeline says 只有通过未见对象/布局/参数的验证，才能进入可复用 Skill Library. Three things follow
from that sentence, and each is a structure here rather than a policy:

* **A reference point.** "Unseen" is not a property of a scene, it is a relation between a scene and
  the configuration the candidate was proposed *for*. So a validation takes a `Proposal` (world +
  binding) and every instance carries the `differs` row saying which field moved. Without the
  reference, "cross-instance" degrades into "several runs", and several runs of the same thing are
  repetition — which is why `repeat` exists here as its own kind, and contributes nothing to the
  three axes.
* **No invented names.** The unseen object and the alternate parameter value are read off the world
  (`scene_vocabulary`: the entity ids the layout declares and the target ids the first observation
  actually contains). A test that named a fourth tray by hand would be measuring whether the tray
  exists, and an instance built from a body the layout never offered is refused by the production
  `PlanValidator` before anything moves — a refusal, which §12.4 forbids counting as a test.
* **A gate with teeth on the sentence, not just on the count.** The frozen
  `SkillValidationReport._derive_passed` needs ≥ `min_instances` runs, ≥ 2 distinct `instance_kind`s,
  no `success is False` and no `unsafe`. That rule is kept exactly as frozen — but it accepts two
  things §5.6 does not: an *unmeasurable* verdict (a claim no verifier answers never returns `False`,
  so a program that always says `unknown` passes with nothing measured), and an audit **violation**
  (`violations` is not consulted). Both are therefore caught here by `strict_findings`, and
  `SkillMemory.admit` requires both the frozen `passed` and a clean strict reading. §11's
  `validation success` row reports the two counts apart, because the difference between them is the
  size of the hole.

Refusals stay refusals all the way through: an instance whose program never reached the executor is
`ran=False`, carries no effects, and cannot make up the `min_instances` total.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ..core.v02 import SkillSpec, SkillValidationReport, ValidationInstance
from . import program, sandbox
from .sandbox import SANDBOX_OBS_PREFIX, SandboxScene

#: The three axes §5.6 names. An axis that was not varied is not covered by a run of the other two.
UNSEEN_KINDS = ("object", "layout", "parameters")

#: `repeat` answers "was the success a fluke?" and `task` belongs to the runtime's later use of a
# skill; neither is one of the three axes, so neither is counted toward them.
AUXILIARY_KINDS = ("task", "repeat")


class ValidationError_(ValueError):
    """A validation that could not be assembled. Named with the trailing underscore so it is never
    mistaken for a *failed* instance: a broken matrix is not evidence about the skill."""


# ------------------------------------------------------------------ the reference ----
@dataclass(frozen=True)
class Proposal:
    """The one configuration a candidate was proposed for.

    Carried through the whole validation because every "unseen" claim is a comparison against it, and
    a claim with nothing to compare against is not a claim.
    """

    scene: SandboxScene
    params: dict[str, Any]
    gap_id: Optional[str] = None
    subgoal_id: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {"scene": self.scene.label, "seed": self.scene.seed, "origin": self.scene.origin,
                "entities": list(self.scene.entity_ids()), "params": dict(self.params),
                "gap_id": self.gap_id, "subgoal_id": self.subgoal_id}


@dataclass(frozen=True)
class Instance:
    """One configuration to run the program on, and in what words it is unseen."""

    kind: str
    scene: SandboxScene
    params: dict[str, Any]
    claim: str
    #: measured differences from the proposal, e.g. {"object": "obj_green_1 -> obj_blue_2"}
    differs: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "scene": self.scene.label, "seed": self.scene.seed,
                "origin": self.scene.origin, "params": dict(self.params), "claim": self.claim,
                "differs": dict(sorted(self.differs.items()))}


def scene_vocabulary(scene: SandboxScene) -> dict[str, list[str]]:
    """What one world of this description contains, read from an actual snapshot.

    This is the only place in the phase that opens a scene without running a program, and it opens it
    to *look*: `build_world_state`, the same channel the loop uses, then close. It exists because the
    parameter axis needs the target ids, and targets are built by `PhysicsScene` (the trays are not in
    any layout), so the alternative to measuring them is naming them by hand.
    """
    raw = None
    try:
        raw = scene.open()
    except Exception as e:  # noqa: BLE001 - an unbuildable world is reported, not run around
        raise ValidationError_(f"{scene.label}: vocabulary scene would not build: "
                               f"{type(e).__name__}: {e}") from e
    try:
        from ..core.verify import build_world_state
        world = build_world_state(raw, 1, f"{SANDBOX_OBS_PREFIX}vocab", scene.verify)
        return {"entities": sorted(e.entity_id for e in world.entities),
                "targets": sorted(t.target_id for t in world.targets)}
    finally:
        raw.close()


# ------------------------------------------------------------------ the matrix ----
def _first_difference(before: Any, after: Any) -> str:
    return f"{before} -> {after}"


def _differs_from(proposal: Proposal, instance: "Instance") -> dict[str, str]:
    """Which fields of this instance are not the proposal's. Computed, never declared."""
    out: dict[str, str] = {}
    if instance.scene.seed != proposal.scene.seed:
        out["seed"] = _first_difference(proposal.scene.seed, instance.scene.seed)
    if [dict(o) for o in instance.scene.objects] != [dict(o) for o in proposal.scene.objects]:
        out["layout"] = f"{len(proposal.scene.objects)} objects -> {len(instance.scene.objects)}"
    for key, value in sorted(instance.params.items()):
        if key in proposal.params and proposal.params[key] != value:
            out[key] = _first_difference(proposal.params[key], value)
    return out


@dataclass
class Matrix:
    """The pre-registered set of instances, and the axes that could not be built.

    `unavailable` is as load-bearing as `instances`: an axis with nothing to vary is a named absence
    (§5.1), and a report that quietly covered two axes while claiming three is the shape of result
    §12's whole document exists to prevent.
    """

    instances: list[Instance] = field(default_factory=list)
    unavailable: dict[str, list[str]] = field(default_factory=dict)
    proposal: Optional[Proposal] = None

    def kinds(self) -> list[str]:
        return sorted({i.kind for i in self.instances})

    def axes_covered(self) -> list[str]:
        return sorted({i.kind for i in self.instances if i.kind in UNSEEN_KINDS})

    def fingerprint(self) -> str:
        """sha256 over the canonical JSON of the matrix — so "this is the set we meant to run" can be
        checked after the results exist instead of asserted from them."""
        doc = {"proposal": self.proposal.as_dict() if self.proposal else None,
               "instances": [i.as_dict() for i in self.instances]}
        return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {"proposal": self.proposal.as_dict() if self.proposal else None,
                "instances": [i.as_dict() for i in self.instances],
                "unavailable": {k: list(v) for k, v in sorted(self.unavailable.items())},
                "axes_covered": self.axes_covered(), "kinds": self.kinds(),
                "fingerprint": self.fingerprint()}


def default_matrix(spec: SkillSpec, proposal: Proposal, *, object_param: str = "object",
                   target_param: str = "target", kinds: Sequence[str] = UNSEEN_KINDS,
                   include_repeat: bool = True,
                   vocabulary: Optional[Mapping[str, Sequence[str]]] = None) -> Matrix:
    """One instance per §5.6 axis, built from what the proposal's own world offers.

    `object_param` / `target_param` name which of the candidate's parameters the axes vary; they are
    *keys*, and the values come from the world. An axis whose parameter the candidate does not declare
    is reported in `unavailable` rather than skipped, so a program with no region parameter is visibly
    not-tested-on-parameters instead of silently passing that row.
    """
    unknown = [k for k in kinds if k not in UNSEEN_KINDS]
    if unknown:
        raise ValidationError_(f"not §5.6 axes: {unknown}; the sentence names 对象/布局/参数")
    declared = set(spec.parameter_names())
    vocab = dict(vocabulary or scene_vocabulary(proposal.scene))
    matrix = Matrix(proposal=proposal)

    def add(kind: str, scene: SandboxScene, params: dict[str, Any], claim: str) -> None:
        matrix.instances.append(Instance(kind=kind, scene=scene, params=params, claim=claim))

    for kind in kinds:
        if kind == "object":
            if object_param not in declared:
                matrix.unavailable.setdefault("object", []).append(
                    f"{spec.name} declares no {object_param!r} parameter, so no object can be unseen")
                continue
            here = str(proposal.params.get(object_param, ""))
            others = [e for e in vocab.get("entities", []) if e != here]
            if not others:
                matrix.unavailable.setdefault("object", []).append(
                    f"the world offers only {here!r}; a second object is not unseen but absent, and a "
                    f"run on an absent object is refused by PlanValidator before anything moves")
                continue
            add("object", proposal.scene, {**proposal.params, object_param: others[0]},
                f"an object this program was not proposed for, from the same layout: {others[0]}")
        elif kind == "layout":
            variant = proposal.scene.variant(seed=proposal.scene.seed + 1)
            add("layout", variant, dict(proposal.params),
                f"the same objects at a different seed, so the placement is not the one proposed: "
                f"seed {proposal.scene.seed} -> {variant.seed}")
        elif kind == "parameters":
            if target_param not in declared:
                matrix.unavailable.setdefault("parameters", []).append(
                    f"{spec.name} declares no {target_param!r} parameter, so there is no alternative "
                    f"value to bind")
                continue
            here = str(proposal.params.get(target_param, ""))
            others = [t for t in vocab.get("targets", []) if t != here]
            if not others:
                matrix.unavailable.setdefault("parameters", []).append(
                    f"this world contains only the region {here!r}; a different region would be a "
                    f"different world, which is the layout axis")
                continue
            add("parameters", proposal.scene, {**proposal.params, target_param: others[0]},
                f"a different value for the same parameter: {here} -> {others[0]}")
    if include_repeat:
        add("repeat", proposal.scene, dict(proposal.params),
            "the proposal's own configuration again, which tests for luck and for nothing else")
    for instance in matrix.instances:
        instance.differs.update(_differs_from(proposal, instance))
    # A claimed-unseen `repeat` would be a lie, and a claimed-unseen axis with nothing differing is
    # the same lie by accident; both are removed here rather than counted.
    kept: list[Instance] = []
    for instance in matrix.instances:
        if instance.kind in UNSEEN_KINDS and not instance.differs:
            matrix.unavailable.setdefault(instance.kind, []).append(
                f"the {instance.kind} instance turned out identical to the proposal "
                f"({instance.scene.label}); it is not an unseen configuration and is not counted")
            continue
        kept.append(instance)
    matrix.instances = kept
    return matrix


# ---------------------------------------------------------------- the findings ----
def measured_true(instance: ValidationInstance) -> bool:
    """`success.value` is a bool or the string `"unknown"` — `Unknownable` cannot hold a guess."""
    return instance.success.value is True


def no_verdict(instance: ValidationInstance) -> bool:
    return not isinstance(instance.success.value, bool)


def uncovered_axes(instances: Sequence[ValidationInstance]) -> list[str]:
    return [k for k in UNSEEN_KINDS if k not in {i.instance_kind for i in instances if i.ran}]


def strict_findings(instances: Sequence[ValidationInstance], *, min_instances: int = 3,
                    required_axes: Sequence[str] = UNSEEN_KINDS) -> list[str]:
    """The frozen rule's blind spots, read as findings.

    Four questions the shipped `passed` does not ask, in the order their seriousness runs: an
    unmeasured verdict counted as no-failure, an audit violation nobody consults, a section of the
    axis list left uncovered, and an instance that did not run but was still listed.
    """
    ran = [i for i in instances if i.ran]
    out: list[str] = []
    if len(ran) < min_instances:
        out.append(f"only {len(ran)} of {min_instances} required instances reached the executor")
    if len(ran) != len(instances):
        out.append(f"{len(instances) - len(ran)} listed instances never ran and are not counted "
                   f"anywhere (§12.4)")
    unmeasured = [i for i in ran if no_verdict(i)]
    if unmeasured:
        out.append(f"{len(unmeasured)} ran instance(s) have no measured verdict: "
                   + "; ".join(f"{i.scene_id}/{i.instance_kind}:{u}" for i in unmeasured
                              for u in (i.success.unmeasured or ["no verdict"])))
    not_true = [i for i in ran if isinstance(i.success.value, bool) and i.success.value is False]
    if not_true:
        out.append(f"{len(not_true)} ran instance(s) were measured and the world disagreed "
                   + "; ".join(f"{i.scene_id}/{i.instance_kind}" for i in not_true))
    violations = [i for i in ran if i.violations]
    if violations:
        out.append(f"{len(violations)} instance(s) reported audit violations; the frozen gate does "
                   f"not read `violations`, and an out-of-authority run measures nothing: "
                   + "; ".join(f"{i.scene_id}:{v}" for i in violations for v in i.violations))
    if any(i.unsafe for i in ran):
        out.append("an instance reported an unsafe finding")
    missing = [k for k in required_axes if k not in {i.instance_kind for i in ran}]
    if missing:
        out.append(f"§5.6 axes not varied on a run instance: {missing} "
                   f"(the frozen rule would accept any 2 kinds; this one reads the sentence)")
    return out


# ------------------------------------------------------------------ the runner ----
@dataclass
class Validation:
    """One cross-instance validation: the frozen report, the runs behind it, and the strict reading."""

    spec_name: str
    candidate_id: str
    report: SkillValidationReport
    runs: list[sandbox.ProgramRun] = field(default_factory=list)
    matrix: Optional[Matrix] = None
    strict: list[str] = field(default_factory=list)

    @property
    def instances(self) -> list[ValidationInstance]:
        return list(self.report.instances)

    @property
    def passed_frozen(self) -> bool:
        return bool(self.report.passed)

    @property
    def passed_strict(self) -> bool:
        """Admissible by §5.6's sentence, which is what `SkillMemory.admit` asks for."""
        return bool(self.report.passed and not self.strict)

    def census(self) -> dict[str, Any]:
        return sandbox.run_census(self.runs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "spec_name": self.spec_name, "candidate_id": self.candidate_id,
            "report": self.report.model_dump(mode="json"),
            "matrix": self.matrix.as_dict() if self.matrix else None,
            "passed_frozen_rule": self.passed_frozen, "passed_strict_reading": self.passed_strict,
            "strict_findings": list(self.strict), "frozen_reasons": list(self.report.reasons),
            "cost": dict(self.report.cost), "census": self.census(),
            "runs": [r.as_dict() for r in self.runs],
        }


def validate_candidate(spec: SkillSpec, proposal: Proposal, matrix: Matrix, *,
                       candidate_id: str = "", min_instances: int = 3,
                       required_axes: Sequence[str] = UNSEEN_KINDS) -> Validation:
    """Run the program on every instance and let the verifier decide each one.

    Nothing chooses *which* instance to try next while this runs, and nothing stops early on a
    failure: the point of a pre-registered matrix is that the set is fixed before the results exist.
    """
    gate = program.check(spec)
    if gate:
        raise ValidationError_(f"{spec.name}: the candidate is not an executable program, so there "
                               f"is nothing to validate: {gate}")
    instances: list[ValidationInstance] = []
    runs: list[sandbox.ProgramRun] = []
    for index, instance in enumerate(matrix.instances):
        run = sandbox.run_program(spec, instance.params, instance.scene,
                                  plan_id=f"val_{spec.name}_{instance.scene.label}#{index}")
        runs.append(run)
        record = sandbox.validation_instance(run, instance.kind)
        refs = [f"claim:{instance.claim}"] + [f"differs:{k}={v}"
                                              for k, v in sorted(instance.differs.items())]
        # Only `evidence_refs` is rewritten, and with strings that name the configuration: a verdict,
        # an effect or a `ran` flag is never edited here — those are the verifier's and the executor's.
        instances.append(record.model_copy(update={
            "evidence_refs": [*refs, *record.evidence_refs][:12]}))
    report = SkillValidationReport(candidate_id=candidate_id, skill_name=spec.name,
                                   instances=instances, min_instances=min_instances,
                                   cost=_cost(runs, instances, matrix, required_axes))
    return Validation(spec_name=spec.name, candidate_id=candidate_id, report=report, runs=runs,
                      matrix=matrix,
                      strict=strict_findings(instances, min_instances=min_instances,
                                             required_axes=required_axes))


def _cost(runs: Sequence[sandbox.ProgramRun], instances: Sequence[ValidationInstance],
          matrix: Matrix, required_axes: Sequence[str]) -> dict[str, Any]:
    """§11's `skill generation / validation 成本`, as the only field the frozen model allows: `cost`."""
    ran = [i for i in instances if i.ran]
    return {
        "wall_s": round(sum(r.wall_s for r in runs), 3),
        "sim_s": round(sum(r.sim_s for r in runs), 3),
        "calls": sum(r.calls_used for r in runs),
        "model_calls": 0,
        "sandbox_runs": len(runs),
        "instances_listed": len(instances),
        "instances_ran": len(ran),
        "kinds_covered": sorted({i.instance_kind for i in ran}),
        "axes_required": list(required_axes),
        "axes_covered": [k for k in required_axes if k in {i.instance_kind for i in ran}],
        "axes_unavailable": {k: list(v) for k, v in sorted(matrix.unavailable.items())},
        "fully_measured_true": sum(1 for i in ran if measured_true(i)),
        "unmeasurable_claims": sorted({u for i in ran for u in (i.success.unmeasured or [])}),
        "runs_with_violations": sum(1 for r in runs if r.violations),
        "runs_with_unsafe_findings": sum(1 for r in runs if r.unsafe),
        "matrix_fingerprint": matrix.fingerprint(),
        "proposal": matrix.proposal.as_dict() if matrix.proposal else None,
    }


def write_validation(validation: Validation, root: str) -> str:
    """The report, the matrix and every run, as files a §15 deliverable can cite.

    Written here and not summarised, because a validation whose instances cannot be re-read is a
    number with no evidence under it.
    """
    os.makedirs(os.path.join(root, "validation"), exist_ok=True)
    for run in validation.runs:
        sandbox.write_run(run, root)
    path = os.path.join(root, "validation", f"{validation.candidate_id or validation.spec_name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(validation.as_dict(), fh, indent=1, sort_keys=True, default=str)
    return path
