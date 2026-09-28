"""SPEC-v0.2 §6 contract freeze tests (P0).

These assert the *shape* of the v0.2 layer, not any task behaviour: every new record
exists and carries the five things §6 requires, and the three invariants the phase
depends on are enforced by the types rather than by convention —

* an obligation cannot disappear by being unanswered (`unknown` counts as owed);
* a skill cannot enter the library without a cross-instance validation report;
* a record cannot claim the v0.1 schema version and be pooled with v0.1 statistics.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from embodied_agent.core import v02
from embodied_agent.core.contracts import SCHEMA_VERSION as V01_SCHEMA, EvalSpec, Source


def test_every_spec_record_exists():
    missing = [name for name in v02.RECORD_NAMES if not hasattr(v02, name)]
    assert missing == []


def test_records_carry_the_five_required_fields():
    for name in v02.RECORD_NAMES:
        cls = getattr(v02, name)
        fields = set(cls.model_fields)
        assert {"schema_version", "episode_id", "created_at", "source"} <= fields, name
    # `Dependency` and the rest descend from Record, so the state reference is available
    # to all of them; the record that is *about* a state sets it.
    assert "based_on_state_version" in v02.TaskPlan.model_fields


def test_json_round_trip_is_lossless():
    plan = v02.TaskPlan(
        episode_id="ep1", task_utterance="clear the mat, keep the cup reachable",
        subgoals=[v02.Subgoal(subgoal_id="s1", statement="lift the mat"),
                  v02.Subgoal(subgoal_id="s2", statement="set the mat back",
                              kind="recover", depends_on=["s1"])],
        dependencies=[v02.Dependency(from_subgoal_id="s2", on_subgoal_id="s1",
                                     kind=v02.DependencyKind.recovery)])
    back = v02.TaskPlan.model_validate_json(plan.model_dump_json())
    assert back.model_dump() == plan.model_dump()
    assert back.schema_version == v02.SCHEMA_VERSION == "4"


def test_a_v02_record_may_not_claim_the_v01_schema():
    assert V01_SCHEMA == "3"
    with pytest.raises(ValidationError, match="schema 3"):
        v02.TaskPlan(schema_version=V01_SCHEMA)


def test_unknown_is_the_default_everywhere_it_matters():
    assert v02.Unknownable().value == "unknown"
    assert v02.Subgoal().satisfied.value == "unknown"
    assert v02.SuspendedRelation().restored.value == "unknown"
    assert v02.Dependency().violated.value == "unknown"


def test_an_unanswered_obligation_still_counts_as_owed():
    wm = v02.WorkingMemoryState(
        episode_id="ep1",
        commitments=[v02.Commitment(text="put the mat back"),
                     v02.Commitment(text="restore the chair",
                                    discharged=v02.Unknownable(value=True))],
        suspended_relations=[v02.SuspendedRelation(subject_id="mat", predicate="on",
                                                   was="true", now="false")])
    assert wm.unresolved_obligations() == 2       # 1 unknown commitment + 1 unrestored
    wm.commitments[0].discharged.value = True
    assert wm.unresolved_obligations() == 1


def test_plan_rejects_dangling_and_duplicate_references():
    with pytest.raises(ValidationError, match="unknown ids"):
        v02.TaskPlan(subgoals=[v02.Subgoal(subgoal_id="a", depends_on=["ghost"])])
    with pytest.raises(ValidationError, match="duplicate"):
        v02.TaskPlan(subgoals=[v02.Subgoal(subgoal_id="a"), v02.Subgoal(subgoal_id="a")])


def test_procedure_binding_substitutes_and_refuses_holes():
    spec = v02.SkillSpec(
        name="move_to", description="pick then place",
        parameters=[v02.SkillParameter(name="obj"), v02.SkillParameter(name="dest")],
        procedure=[v02.ProcedureStep(intent=v02.ProcedureIntent(
                       skill="pick", args={"object_id": "{obj}"})),
                   v02.ProcedureStep(intent=v02.ProcedureIntent(
                       skill="place",
                       args={"object_id": "{obj}", "target_id": "{dest}"}))])
    calls = spec.bind("plan_x", {"obj": "cup 1", "dest": "mat 1"})
    assert [c.skill for c in calls] == ["pick", "place"]
    assert calls[1].args == {"object_id": "cup 1", "target_id": "mat 1"}
    with pytest.raises(KeyError, match="dest"):
        spec.bind("plan_x", {"obj": "cup 1"})
    with pytest.raises(ValidationError):          # not a primitive the runtime has
        v02.SkillSpec(name="bad", procedure=[v02.ProcedureStep(
            intent=v02.ProcedureIntent(skill="teleport", args={}))]).bind("p", {})
    undeclared = v02.SkillSpec(
        name="x", parameters=[v02.SkillParameter(name="obj")],
        procedure=[v02.ProcedureStep(step_id="s9", intent=v02.ProcedureIntent(
            skill="pick", args={"object_id": "{ghost}"}))])
    assert undeclared.unresolved_parameters() == ["s9:ghost"]


def test_cross_instance_gate_is_a_rule_not_a_mood():
    ok = lambda kind: v02.ValidationInstance(instance_kind=kind, ran=True,
                                             success=v02.Unknownable(value=True))
    one_kind = v02.SkillValidationReport(candidate_id="c", skill_name="n",
                                         instances=[ok("object")] * 3)
    assert one_kind.passed is False
    assert any("cross-instance" in r for r in one_kind.reasons)
    two_kinds = v02.SkillValidationReport(candidate_id="c", skill_name="n",
                                          instances=[ok("object"), ok("layout"), ok("object")])
    assert two_kinds.passed is True
    too_few = v02.SkillValidationReport(candidate_id="c", skill_name="n",
                                        instances=[ok("object"), ok("layout")])
    assert too_few.passed is False
    unsafe = v02.SkillValidationReport(candidate_id="c", skill_name="n", instances=[
        ok("object"), ok("layout"),
        v02.ValidationInstance(instance_kind="parameters", ran=True,
                               success=v02.Unknownable(value=True),
                               unsafe=["moved a fixture without checking clearance"])])
    assert unsafe.passed is False and any("unsafe" in r for r in unsafe.reasons)
    unrun = v02.SkillValidationReport(candidate_id="c", skill_name="n", instances=[
        v02.ValidationInstance(instance_kind="object", ran=False)] * 4)
    assert unrun.passed is False


def test_library_status_requires_a_report():
    spec = v02.SkillSpec(name="n")
    with pytest.raises(ValidationError, match="validation report"):
        v02.SkillCandidate(spec=spec, status="library")
    with pytest.raises(ValidationError, match="validation report"):
        v02.SkillCandidate(spec=spec, status="validated")
    assert v02.SkillCandidate(spec=spec, status="proposed").status == "proposed"


def test_perception_covers_the_six_required_capabilities():
    fields = set(v02.PerceptionObservation.model_fields)
    assert {"detections", "relations", "visibility", "regions", "changes",
            "uncertainties"} <= fields
    percept = v02.PerceptionObservation(
        episode_id="ep1", modality="rgb",
        detections=[v02.Detection(entity_id="cup 1", kind="cup", confidence=0.62,
                                  how_identified="segmented")],
        relations=[v02.SpatialRelation(subject_id="cup 1", predicate="on",
                                       related_id="mat 1", confidence=0.4)],
        uncertainties=[v02.UncertaintyItem(what="is the drawer open",
                                           why="not_visible", would_resolve="observe")])
    assert percept.source == Source.sensor
    assert json.loads(percept.model_dump_json())["detections"][0]["confidence"] == 0.62


def test_change_records_label_who_saw_the_change():
    fields = set(v02.ChangeRecord.model_fields)
    assert "modality" in fields                       # vlm | privileged | text
    assert v02.ChangeRecord().modality == "vlm"


def test_recalled_experience_is_reference_only():
    wm = v02.WorkingMemoryState(recalled=[v02.RecalledExperience(
        experience_id="exp1", relevance=0.31, match_terms=["mat"],
        injected_into_round=3)])
    assert wm.recalled[0].used_by_model.value == "unknown"
    # A recall row is a pointer plus an audit trail. It holds no world facts of its own, so
    # retrieval can be logged and ablated without ever entering WorldState (§5.4).
    assert set(v02.RecalledExperience.model_fields) <= {
        "recall_id", "experience_id", "relevance", "match_terms", "injected_into_round",
        "used_by_model", "contradicted_current_state", *v02.RECORD_FIELDS}
    exp = v02.EpisodicExperience(task_context="clear the desk",
                                 outcome=v02.OutcomeLabel.failure,
                                 failure_reason="lost the mat",
                                 transfer_caution="one layout only")
    assert "caution" in exp.as_reference() and "failure" in exp.as_reference()


def test_no_hidden_truth_reaches_the_v02_layer():
    """The separation must be structural, not a word list: the v0.2 layer may import the
    v0.1 data model but must not import or define the scoring contract (§3.2, §3.5)."""
    import ast
    import inspect

    import embodied_agent.core.v02 as module

    tree = ast.parse(inspect.getsource(module))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported += [node.module or ""] + [a.name for a in node.names]
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    banned = ("EvalSpec", "evaluator", "eval", "blind", "score", "ground_truth")
    for name in imported:
        assert not any(b in name.lower() for b in banned), name
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert not any(b in d.lower() for d in defined for b in banned), defined
    field_names = {f for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
                   for stmt in cls.body if isinstance(stmt, ast.AnnAssign)
                   for f in [getattr(stmt.target, "id", "")]}
    # §3.5 separates world facts / model intent / history / eval truth. The v0.2 layer may
    # carry model intent (SkillSpec.expected_effects, §5.5) but never the reference answer.
    assert not any("reference" in f or "oracle" in f for f in field_names), field_names
    assert "expected_effects" in {f for f in field_names}
    assert not issubclass(v02.TaskPlan, EvalSpec)


def test_event_vocabulary_is_frozen_and_unique():
    assert len(set(v02.V02_EVENT_TYPES)) == len(v02.V02_EVENT_TYPES)
    for needed in ("plan", "working_memory", "memory_retrieval", "skill_gap",
                   "skill_validation", "perception", "ablation"):
        assert needed in v02.V02_EVENT_TYPES


def test_fingerprint_is_deterministic_and_sensitive():
    a, b = v02.schema_fingerprint(), v02.schema_fingerprint()
    assert a == b
    assert a["schema_version"] == "4" and a["v01_schema_version"] == "3"
    assert len(a["module_sha256"]) == 64
    changed = dict(a, records_sha256="0" * 64)
    assert changed != a

def test_ablation_registry_matches_spec_section_9():
    # §9's main system names seven capabilities; its core ablation list names seven arms.
    assert len(v02.MODULE_NAMES) == 7
    assert set(v02.ABLATION_CONDITIONS) == {
        "full", "wo_vlm", "wo_planning", "wo_working_memory", "wo_episodic_memory",
        "wo_replanning", "wo_skill_acquisition"}
    assert v02.ABLATION_CONDITIONS["full"] == ()
    for condition, off in v02.ABLATION_CONDITIONS.items():
        assert set(off) <= set(v02.MODULE_NAMES), condition
        assert len(off) <= 1, f"{condition} is a combined arm, §9 lists single-module arms"


def test_an_arm_record_cannot_disagree_with_the_registry():
    with pytest.raises(ValidationError):
        v02.Ablation(condition="wo_vlm", modules_off=[])          # claims less than it turns off
    with pytest.raises(ValidationError):
        v02.Ablation(condition="wo_vlm", modules_off=["vlm", "planning"])
    with pytest.raises(ValidationError):
        v02.Ablation(condition="wo_perception", modules_off=[])   # not a registered arm
    arm = v02.ablation("wo_episodic_memory")
    assert arm.enabled("planning") and not arm.enabled("episodic_memory")


def test_a_module_cannot_produce_records_while_switched_off():
    arm = v02.ablation("wo_vlm")
    # The audit is over record types, so "the module was off" is checkable per episode
    # rather than asserted by the runner (§3.7, §12.3).
    assert arm.violations(["perception", "working_memory"]) == ["perception"]
    assert arm.violations(["working_memory", "plan", "ablation"]) == []
    assert v02.ablation("full").violations(list(v02.V02_EVENT_TYPES)) == []
    # Every event type is attributable, and every ablatable module owns at least one.
    assert set(v02.EVENT_MODULE) == set(v02.V02_EVENT_TYPES)
    owned = {m for m in v02.EVENT_MODULE.values() if m}
    assert owned <= set(v02.MODULE_NAMES)
    assert set(v02.MODULE_NAMES) - owned == {"skill_retrieval"}, (
        "retrieval is in §9's main system but has no dedicated core arm and no record of "
        "its own yet; the gap is recorded, not papered over")


FREEZE_PATH = "configs/experiment/v02_schema_freeze.json"


def test_freeze_file_matches_the_code():
    """P0's "固化 v0.2 schema": the contract layer's identity is on disk, and a later edit
    that moves it fails here instead of silently re-labelling old records."""
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), FREEZE_PATH)
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    assert doc["schema_fingerprint"] == v02.schema_fingerprint(), (
        "the v0.2 contract layer has moved since the freeze; re-take it and record the "
        "delta (history entry + a compatibility_delta line), do not edit this file's hash")
    assert sorted(doc["records_frozen"]) == sorted(v02.RECORD_NAMES)
    assert list(doc["event_types_frozen"]) == list(v02.V02_EVENT_TYPES)
    assert doc["ablation_registry"]["conditions"].keys() == v02.ABLATION_CONDITIONS.keys()
    for condition, off in doc["ablation_registry"]["conditions"].items():
        assert list(off) == list(v02.ABLATION_CONDITIONS[condition]), condition
    assert doc["ablation_registry"]["modules"] == list(v02.MODULE_NAMES)


def test_the_freeprint_covers_every_record_class_not_just_the_spec_nine():
    nine = set(v02.RECORD_NAMES)
    allrecords = set(v02.record_classes())
    assert nine <= allrecords
    # The sub-records are where a field quietly appears, so they are in the fingerprint.
    assert {"Subgoal", "PlanRevision", "Commitment", "FailedAttempt", "Assumption",
            "RecalledExperience", "SkillGap", "Ablation", "TaskUnderstanding"} <= allrecords
    fp = v02.schema_fingerprint()
    assert fp["all_records_sha256"] != fp["records_sha256"]
    assert len(fp["all_records_sha256"]) == 64


def test_an_unrun_validation_instance_cannot_carry_a_verdict():
    from pydantic import ValidationError as VE

    with pytest.raises(VE):
        v02.ValidationInstance(ran=False, success=v02.Unknownable(value=True))
    with pytest.raises(VE):
        v02.ValidationInstance(ran=False, effects_observed=["mat moved"])
    ok = v02.ValidationInstance(ran=True, success=v02.Unknownable(value=True),
                                effects_observed=["mat moved"])
    assert ok.ran


def test_an_unknown_event_type_cannot_slip_past_the_arm_audit():
    arm = v02.ablation("wo_vlm")
    with pytest.raises(ValueError):
        arm.violations(["vlm_said_so_far"])


def test_the_event_store_writes_two_contract_generations_without_pooling_them(tmp_path):
    """P0's version gate: v0.2 records share the log with v0.1 records, so the envelope has
    to say which generation a line belongs to and refuse a relabelled record (§6, §12.1).
    A v0.1 record must be filed exactly as it was before the gate existed."""
    from embodied_agent.core.events import EpisodeStore

    plan = v02.TaskPlan(episode_id="ep-v2", task_utterance="clear the mat")
    store = EpisodeStore(tmp_path / "run", episode_id="ep-v2")
    v01_shape = {"round_index": 1, "schema_version": "3"}
    first = store.log("decision", **v01_shape)          # what runtime.py:355 does
    second = store.log_record("plan", plan)
    with pytest.raises(ValueError):
        store.log("plan", contract_version="4", **v01_shape)   # contradicts the record
    assert [e["schema_version"] for e in store.read_all()] == ["3", "4"]
    assert [e["type"] for e in store.read_all("4")] == ["plan"]
    assert store.read_all("3")[0]["payload"]["round_index"] == 1
    # the v0.1 line is untouched by the gate: same envelope version, and the record's own
    # declaration still lands in the reserved-key witness rather than disappearing
    assert first["payload"]["_clobbered_reserved_keys"] == {"schema_version": "3"}
    assert second["payload"]["_clobbered_reserved_keys"] == {"episode_id": "ep-v2",
                                                             "schema_version": "4"}
    # a record cannot be filed into another episode's log
    with pytest.raises(ValueError):
        store.log_record("plan", v02.TaskPlan(episode_id="someone-else"))


def test_the_two_skill_arity_registries_still_agree():
    """A P0 residue, not a v0.2 design: the enforced arity lives in `core/planner.py`
    (`REQUIRED_ARGS`/`OPTIONAL_ARGS`) while the text the model sees lives in
    `core/skills.py` (`CATALOGUE[...]["args"]`). v0.2 adds composite and acquired skills
    on top of both, so the drift between them has to fail a test rather than quietly
    teach the model an argument the validator rejects."""
    from embodied_agent.core.planner import OPTIONAL_ARGS, REQUIRED_ARGS
    from embodied_agent.core.skills import SkillRegistry

    catalogue = SkillRegistry.CATALOGUE
    assert set(catalogue) == set(REQUIRED_ARGS) == set(OPTIONAL_ARGS)
    for skill, spec in catalogue.items():
        shown = spec["args"]
        required = {k for k, v in shown.items() if "required" in v}
        optional = {k for k, v in shown.items() if "optional" in v}
        assert required == REQUIRED_ARGS[skill], skill
        assert optional == OPTIONAL_ARGS[skill], skill
        assert set(shown) == required | optional, f"{skill} has an arg labelled neither"
