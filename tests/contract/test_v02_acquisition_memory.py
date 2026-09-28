"""Contract tests for SPEC-v0.2 §5.6's last box: the skill memory, and the gate in its door.

The module's claim is narrow and absolute: nothing enters the reusable library without a validation
report that varied an unseen object, layout and parameter and measured true. So the tests are arranged
as attempts on a door, and only two kinds of attempt are allowed to succeed.

One admission runs real physics, because the evidence a skill is admitted on has to be a real
`SkillValidationReport` from `validate_candidate` — that pairing is what §15's demonstrable loop
turns on. Every refusal is exercised on a hand-assembled report: mutating the record a gate reads is
the standard way to ask whether the gate reads it at all, and no measurement is invented here — the
instances below carry `scene_id="hand-built"` and say so in their own refs.
"""
from __future__ import annotations

import inspect
import json
import os
import re

import pytest
from pydantic import ValidationError

from embodied_agent.acquisition import memory as memory_module
from embodied_agent.acquisition import program, sandbox, validation as V
from embodied_agent.acquisition.memory import (ADMISSION_KEY, MemoryEntry, SkillMemory,
                                               SkillMemoryError, admission_findings, promote)
from embodied_agent.core.v02 import (SCHEMA_VERSION, ProcedureIntent, ProcedureStep, SkillCandidate,
                                     SkillParameter, SkillSpec, SkillValidationReport, Unknownable,
                                     ValidationInstance)

CASE = "smoke_clean"
ENTITY = "obj_green_1"
TARGET = "tray_left"
FINGERPRINT = "f" * 64


def _spec(name="move_object", effects=("placed:{object}:{target}",)) -> SkillSpec:
    return program.program(
        name, "pick the object, then place it in the named tray",
        [program.param("object", "entity", description="the object to move"),
         program.param("target", "region", description="the tray to put it in")],
        [program.step("pick", {"object_id": "{object}"}, step_id="s0", on_failure="retry_once"),
         program.step("place", {"object_id": "{object}", "target_id": "{target}"}, step_id="s1")],
        preconditions=["the gripper is empty and the object is reachable"],
        expected_effects=list(effects),
        termination_conditions=["the placement predicate measures true, or the budget is spent"],
        applicability=["one object, one tray"])


def _candidate(spec: SkillSpec, candidate_id: str = "", status: str = "sandbox") -> SkillCandidate:
    return SkillCandidate(spec=spec, proposed_by="test-fixture", status=status,
                          candidate_id=candidate_id or f"cand_{spec.name}")


def _report(spec: SkillSpec, *, kinds=("object", "layout", "parameters"), verdict=True,
            candidate_id: str = "", violations: list[str] | None = None,
            fingerprint: str = FINGERPRINT, axes_covered=None,
            extra_cost: dict | None = None) -> SkillValidationReport:
    """A hand-assembled report, for the gate's own logic.

    `verdict=False` and `violations=[...]` are the two shapes the frozen `_derive_passed` does not
    look at the way §5.6's sentence does, so they are the shapes a gate that only asks for `passed`
    would let through. Nothing here is a measurement of a skill.
    """
    instances = [ValidationInstance(
        instance_kind=kind, scene_id="hand-built", seed=51, args={"object": ENTITY,
                                                                 "target": TARGET},
        ran=True, success=Unknownable(value=verdict) if verdict is not None
        else Unknownable(value="unknown", unmeasured=[f"{kind}:not_measured"]),
        effects_observed=[f"placed:{ENTITY}:{TARGET}"] if verdict else [],
        violations=list(violations or []), evidence_refs=[f"hand:{kind}"])
        for kind in kinds]
    cost = {"matrix_fingerprint": fingerprint,
            "axes_covered": list(axes_covered if axes_covered is not None else kinds),
            "axes_unavailable": {}, "proposal": None, "wall_s": 1.0, "sim_s": 2.0, "calls": 6,
            "model_calls": 0, "sandbox_runs": len(instances), "fully_measured_true":
            sum(1 for i in instances if i.success.value is True)}
    return SkillValidationReport(candidate_id=candidate_id or f"cand_{spec.name}",
                                 skill_name=spec.name, instances=instances,
                                 cost={**cost, **(extra_cost or {})})


def _positional_spec_takers(cls) -> list[str]:
    """Public callables of `cls` that can be handed a spec as an argument.

    A helper so the filter can be turned on a class that *does* have such a method: an assertion that
    a list is empty is worth nothing until something shows the list would not be empty otherwise.
    """
    return [name for name in dir(cls) if not name.startswith("_")
            and callable(getattr(cls, name))
            and any(p.name == "spec" and p.kind is not p.KEYWORD_ONLY
                    for p in inspect.signature(getattr(cls, name)).parameters.values())]


# ------------------------------------------------------------------ the door ----
def test_no_public_call_can_be_asked_to_store_a_bare_spec():
    """There is no `add(spec)`. Admission is a relation between a candidate and a report.

    The one place the word `spec` appears in a public signature is `refuse`, where it is keyword-only,
    optional and used to *label* a row — so the honest claim is about positionals, and the rest of this
    test is the proof that the exception is an exception for the label only.
    """
    class WouldBeWrong:                                # the filter's own negative case
        def add(self, spec): ...
    assert _positional_spec_takers(WouldBeWrong) == ["add"]

    assert _positional_spec_takers(SkillMemory) == []
    assert list(inspect.signature(SkillMemory.admit).parameters) == ["self", "candidate", "report"]
    assert SkillMemory().specs() == []
    assert not any("add" in name or "store" in name for name in dir(SkillMemory)
                   if not name.startswith("_")), [n for n in dir(SkillMemory)
                                                  if not n.startswith("_")]

    refuse = inspect.signature(SkillMemory.refuse).parameters
    assert refuse["spec"].kind is inspect.Parameter.KEYWORD_ONLY
    assert refuse["spec"].default is None
    memory = SkillMemory()
    spec = _spec()
    memory.refuse(["the representation gate said no"], spec=spec, candidate_id="cand_x")
    assert memory.specs() == [] and len(memory.refusals) == 1
    assert memory.refusals[0]["skill_name"] == spec.name


def test_a_skill_validated_by_the_real_pipeline_is_admitted(tmp_path):
    spec = _spec()
    scene = sandbox.SandboxScene.from_task(CASE)
    proposal = V.Proposal(scene=scene, params={"object": ENTITY, "target": TARGET})
    matrix = V.default_matrix(spec, proposal)
    validation = V.validate_candidate(spec, proposal, matrix, candidate_id=f"cand_{spec.name}")
    assert validation.passed_strict, validation.strict
    memory = SkillMemory(str(tmp_path / "skill_memory.jsonl"))
    entry = memory.admit(_candidate(spec), validation.report)
    assert entry.skill_name == spec.name and entry.spec.skill_id == spec.skill_id
    admission = entry.admission
    assert admission["passed_frozen_rule"] is True and admission["strict_findings"] == []
    # stored in §5.6's own order (对象/布局/参数), because a row that lists the axes in the order the
    # spec names them can be read against the sentence; the audit's union is the sorted one
    assert admission["axes_covered"] == list(V.UNSEEN_KINDS)
    assert admission["matrix_fingerprint"] == matrix.fingerprint()
    assert admission["seeds"] == [scene.seed, scene.seed + 1]
    assert admission["cost"]["model_calls"] == 0 and admission["cost"]["calls"] == 8
    assert admission["cost"]["sim_s"] > 0.0
    # the stored program is the program that ran, not a re-encoding of it
    assert program.render(entry.spec) == program.render(spec)
    assert entry.spec.provenance[ADMISSION_KEY]["report_id"] == validation.report.report_id
    assert memory.audit()["admitted"] == 1
    # and the bytes on disk re-read as the same thing
    again = SkillMemory.load(memory.path)
    assert again.names() == [spec.name]
    assert again.entries[0].admission["matrix_fingerprint"] == matrix.fingerprint()


# ------------------------------------------------------------------ refusals ----
def test_a_report_for_another_candidate_opens_nothing():
    spec = _spec()
    memory = SkillMemory()
    with pytest.raises(SkillMemoryError) as exc:
        memory.admit(_candidate(spec, "cand_mine"), _report(spec, candidate_id="cand_theirs"))
    assert any("not 'cand_theirs'" in r or "somebody else" in r for r in exc.value.reasons), \
        exc.value.reasons
    assert memory.specs() == []


def test_a_report_that_only_satisfied_the_frozen_rule_is_refused():
    """`unknown` is not a failure to `_derive_passed`, so a gate that stops at `passed` is a sieve."""
    spec = _spec()
    report = _report(spec, verdict=None)
    assert report.passed and report.reasons == []
    assert admission_findings(_candidate(spec), report)
    with pytest.raises(SkillMemoryError) as exc:
        SkillMemory().admit(_candidate(spec), report)
    assert any("no measured verdict" in r for r in exc.value.reasons)


def test_a_run_with_an_audit_violation_is_refused_even_though_the_frozen_rule_ignores_it():
    """The second hole: `violations` is not consulted by `_derive_passed`.

    A `violations` row means the sandbox reached outside its authority — §5.6's 不得…直接写入世界 — so
    the numbers in that instance describe a world somebody edited. Counting it as a validation would
    turn the prohibition into paperwork.
    """
    spec = _spec()
    report = _report(spec, violations=["the sandbox wrote scene attribute held_bodies from x"])
    assert report.passed, report.reasons
    with pytest.raises(SkillMemoryError) as exc:
        SkillMemory().admit(_candidate(spec), report)
    assert any("audit violations" in r for r in exc.value.reasons)


def test_a_report_that_cannot_name_the_axes_it_varied_is_refused():
    spec = _spec()
    memory = SkillMemory()
    with pytest.raises(SkillMemoryError) as exc:
        memory.admit(_candidate(spec), _report(spec, fingerprint=""))
    assert any("matrix fingerprint" in r for r in exc.value.reasons)
    with pytest.raises(SkillMemoryError) as exc2:
        memory.admit(_candidate(spec), _report(spec, axes_covered=["object", "layout"]))
    assert any("axes never varied" in r and "parameters" in r for r in exc2.value.reasons)
    assert memory.specs() == []


def test_a_measured_failure_is_refused_and_says_which_instance():
    spec = _spec()
    report = _report(spec, verdict=False)
    assert not report.passed
    with pytest.raises(SkillMemoryError) as exc:
        SkillMemory().admit(_candidate(spec), report)
    assert any("the frozen gate did not pass" in r for r in exc.value.reasons)
    assert any("world disagreed" in r for r in exc.value.reasons)


def test_only_an_acquired_skill_can_be_stored():
    primitive = _spec().model_copy(update={"level": "primitive"})
    with pytest.raises(SkillMemoryError) as exc:
        SkillMemory().admit(_candidate(primitive), _report(_spec()))
    assert any("not an acquired skill" in r for r in exc.value.reasons)
    composite = _spec().model_copy(update={"level": "composite"})
    assert any("not an acquired skill" in r
               for r in admission_findings(_candidate(composite), _report(_spec())))


def test_a_rejected_candidate_is_not_let_back_in_by_attaching_a_report():
    spec = _spec()
    with pytest.raises(SkillMemoryError) as exc:
        SkillMemory().admit(_candidate(spec, status="rejected"), _report(spec))
    assert any("already rejected" in r for r in exc.value.reasons)


def test_a_name_is_not_overwritten():
    """Append-only, because a skill that changed under an earlier episode would move the ground."""
    spec = _spec()
    memory = SkillMemory()
    memory.admit(_candidate(spec), _report(spec))
    with pytest.raises(SkillMemoryError) as exc:
        memory.admit(_candidate(_spec()), _report(_spec()))
    assert any("already in the library" in r for r in exc.value.reasons)
    assert memory.audit()["admitted"] == 1
    # a different program with the same name is refused too, and says which one it is
    renamed = _spec().model_copy(update={"skill_id": "skill_other"})
    with pytest.raises(SkillMemoryError):
        memory.admit(_candidate(renamed), _report(_spec()))


def test_a_program_that_is_not_executable_is_refused_before_it_is_stored():
    broken = SkillSpec(name="teleport_thing", description="d", level="acquired",
                       parameters=[SkillParameter(name="object", type="entity")],
                       preconditions=["p"], expected_effects=["placed:x:y"],
                       termination_conditions=["t"], applicability=["a"],
                       procedure=[ProcedureStep(step_id="s0",
                                                intent=ProcedureIntent(skill="teleport",
                                                                       args={}))])
    reasons = admission_findings(_candidate(broken), _report(broken))
    assert any("not executable as proposed" in r for r in reasons), reasons
    # the same gate the sandbox applies, applied one box later: a stored skill must be a program
    assert any("teleport" in r for r in reasons)


# ---------------------------------------------------------------- the file ----
def _stored(tmp_path, spec=None):
    spec = spec or _spec()
    memory = SkillMemory(str(tmp_path / "skill_memory.jsonl"))
    memory.admit(_candidate(spec), _report(spec))
    memory.flush()
    return memory


def test_a_missing_file_is_a_cold_start(tmp_path):
    memory = SkillMemory.load(str(tmp_path / "nothing_here.jsonl"))
    assert memory.entries == [] and memory.audit()["admitted"] == 0


def test_a_line_written_by_hand_is_refused_at_load(tmp_path):
    memory = _stored(tmp_path)
    with open(memory.path, encoding="utf-8") as fh:
        doc = json.load(fh)
    stripped = {**doc, "provenance": {}}
    path = str(tmp_path / "hand.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(stripped) + "\n")
    with pytest.raises(SkillMemoryError) as exc:
        SkillMemory.load(path)
    assert any("no admission block" in r for r in exc.value.reasons)
    # and an admission that says the gate did not pass is refused too, not repaired
    lying = {**doc, "provenance": {ADMISSION_KEY: {**doc["provenance"][ADMISSION_KEY],
                                                   "passed_frozen_rule": False}}}
    path2 = str(tmp_path / "lying.jsonl")
    with open(path2, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(lying) + "\n")
    with pytest.raises(SkillMemoryError) as exc2:
        SkillMemory.load(path2)
    assert any("did not pass" in r for r in exc2.value.reasons)


def test_a_line_from_another_schema_version_is_refused(tmp_path):
    """The library is read back months later, by a different build of the schema.

    A line whose `schema_version` is not the one the reader speaks is not repaired and not skipped: it
    is refused, because a silently-*adapted* old record is how a stored skill ends up meaning something
    its validation report never checked.
    """
    memory = _stored(tmp_path)
    with open(memory.path, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["schema_version"] == SCHEMA_VERSION
    path = str(tmp_path / "old.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({**doc, "schema_version": "3"}) + "\n")
    with pytest.raises(SkillMemoryError) as exc:
        SkillMemory.load(path)
    assert any("schema" in r for r in exc.value.reasons)
    # the refusal names the version it wanted and the one it found, so a reader can tell which side moved
    line = exc.value.reasons[0]
    assert SCHEMA_VERSION in line and "3" in line
    # and dropping the version entirely is the same class of problem, not a default
    path2 = str(tmp_path / "versionless.jsonl")
    with open(path2, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({k: v for k, v in doc.items() if k != "schema_version"}) + "\n")
    with pytest.raises(SkillMemoryError):
        SkillMemory.load(path2)


def test_flush_and_load_round_trip_keeps_every_field_the_gate_read(tmp_path):
    memory = _stored(tmp_path)
    again = SkillMemory.load(memory.path)
    assert again.entries[0].admission == memory.entries[0].admission
    assert again.entries[0].spec.model_dump() == memory.entries[0].spec.model_dump()
    assert os.path.exists(memory.path)


# ------------------------------------------------------------------ reading ----
def test_applicable_offers_every_covering_skill_and_never_picks_one():
    """§9: choosing among them is the loop's decision, so the store must not make it quietly."""
    first, second = _spec("move_left"), _spec("move_right")
    memory = SkillMemory()
    memory.admit(_candidate(first), _report(first))
    memory.admit(_candidate(second), _report(second))
    offered = memory.applicable(f"placed:{ENTITY}:{TARGET}")
    assert [s.name for s in offered] == ["move_left", "move_right"]
    assert all(isinstance(s, SkillSpec) for s in offered)
    assert memory.applicable("stacked:whatever") == []
    assert memory.get("move_left") is first or memory.get("move_left").skill_id == first.skill_id
    # a disabled entry is not offered, and is still in the store
    memory.entries[0].spec.enabled = False
    assert [s.name for s in memory.applicable(f"placed:{ENTITY}:{TARGET}")] == ["move_right"]
    assert memory.get("move_left") is None
    assert memory.audit()["admitted"] == 2


def test_the_matching_relation_is_the_ones_gap_detection_used():
    """`applicable` and "the library already covers this" must not drift apart mid-experiment."""
    from embodied_agent.acquisition.gap import covers_effect
    spec = _spec()
    memory = SkillMemory()
    memory.admit(_candidate(spec), _report(spec))
    effect = f"placed:{ENTITY}:{TARGET}"
    assert memory.applicable(effect) and covers_effect(spec, effect)
    assert not memory.applicable("grasp:" + ENTITY) and not covers_effect(spec, "grasp:" + ENTITY)


def test_refusals_are_counted_as_rows_as_well_as_raised(tmp_path):
    """§11's unsafe/invalid-skill rate needs the attempts, and an exception alone loses them."""
    memory = SkillMemory(str(tmp_path / "skill_memory.jsonl"))
    spec = _spec()
    for _ in range(2):
        with pytest.raises(SkillMemoryError):
            memory.admit(_candidate(spec, "cand_mine"), _report(spec, candidate_id="cand_other"))
    assert memory.audit()["refused"] == 2 and memory.audit()["admitted"] == 0
    assert memory.audit()["refusal_reasons"]
    assert os.path.exists(memory._refusal_path)
    with open(memory._refusal_path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    assert len(rows) == 2 and rows[0]["candidate_id"] == "cand_mine"
    assert all(r["report_id"] for r in rows)
    memory.refuse(["the representation gate said no"], spec=spec, candidate_id="cand_x")
    assert len(memory.refusals) == 3


def test_promotion_never_forgets_the_report_that_earned_it():
    spec, report = _spec(), _report(_spec())
    promoted = promote(_candidate(spec), report)
    assert promoted.status == "library" and promoted.validation_report_id == report.report_id
    with pytest.raises(ValidationError) as exc:
        SkillCandidate(spec=spec, status="library")
    assert "requires a validation report id" in str(exc.value)


def test_the_memory_holds_records_and_touches_no_world():
    """The store is the far end of the pipeline: no executor, no scene, no verifier, no answer key.

    Asserted against the module's own text rather than by calling something, because the property that
    matters is that there is *nothing to call* — a store that could run a skill would be able to
    re-validate a skill on its own authority, which is the §5.6 gate wearing a filename.
    """
    source = inspect.getsource(memory_module)
    for forbidden in ("SkillExecutor", "PhysicsScene", "pybullet", "build_world_state",
                      "eval_spec", "RuntimeVerifier", "EpisodeStore", "SkillCall",
                      "from ..core.skills", "from ..core.scene", "from ..core.verify",
                      "from ..core.runtime", "from ..evaluation"):
        assert forbidden not in source, forbidden
    # and the vocabulary that *is* here is the record vocabulary, with the gate's own dependency
    assert "SkillValidationReport" in source and "strict_findings" in source
    assert "program.check" in source and "covers_effect" in source
    # no import of a world at any indentation, lazily or otherwise
    assert not [ln for ln in source.splitlines()
                if re.search(r"^\s*(from|import)\s+\.", ln)
                and re.search(r"\.(scene|skills|verify|runtime|evaluation)\b", ln)], source[:200]


def test_the_audit_sums_what_validation_cost():
    memory = SkillMemory()
    for name in ("alpha", "beta"):
        spec = _spec(name)
        memory.admit(_candidate(spec), _report(spec))
    audit = memory.audit()
    assert audit["admitted"] == 2 and audit["names"] == ["alpha", "beta"]
    assert audit["model_calls"] == 0 and audit["validation_wall_s"] == 2.0
    assert audit["validation_calls"] == 12
    assert audit["axes_covered"] == sorted(V.UNSEEN_KINDS) and audit["axes_missing"] == []
    assert audit["levels"] == ["acquired"] and audit["min_instances"] == [3]
    assert isinstance(MemoryEntry.from_dict(_spec().model_dump(mode="json")).spec, SkillSpec)
