"""Contract tests for SPEC-v0.2 §5.6 step 5: cross-instance validation on unseen object/layout/parameters.

Two halves, because the module has two jobs and they need different evidence:

1. *The matrix* — which instances exist, which axis could not be built, and what the fingerprint
   commits to. None of this needs a world, and a claim about the *set* a validation intended to run
   should not depend on physics.
2. *The runs* — a small number of complete validations on real scenes, chosen so that each of the
   three verdicts the gate can meet appears at least once: measured true, measured false, and
   measured-not-at-all. The third one is the interesting case: the frozen
   `SkillValidationReport._derive_passed` accepts it, which is why `strict_findings` exists and why
   the tests report the two readings apart instead of picking a favourite.

Offline and unbilled: `model_calls == 0` on every instance and in the cost block. Scenes cost about a
third of a second each, so every complete validation is a module-scoped fixture and the assertions
about it read from that one run.
"""
from __future__ import annotations

import inspect
import json
import os
from typing import get_args

import pytest

from embodied_agent.acquisition import memory, program, sandbox, validation as V
from embodied_agent.core.v02 import (ProcedureIntent, ProcedureStep, Record, SkillParameter,
                                     SkillSpec, SkillValidationReport, ValidationInstance)

CASE = "smoke_clean"
ENTITY = "obj_green_1"
ENTITY_B = "obj_blue_2"
TARGET = "tray_left"


# ------------------------------------------------------------------ fixtures ----
def _spec(name="move_object", effects=("placed:{object}:{target}",), parameters=None,
          steps=None, description=None) -> SkillSpec:
    return program.program(
        name, description or "pick the object, then place it in the named tray",
        parameters if parameters is not None else
        [program.param("object", "entity", description="the object to move"),
         program.param("target", "region", description="the tray to put it in")],
        steps if steps is not None else
        [program.step("pick", {"object_id": "{object}"}, step_id="s0", on_failure="retry_once"),
         program.step("place", {"object_id": "{object}", "target_id": "{target}"}, step_id="s1")],
        preconditions=["the gripper is empty and the object is reachable"],
        expected_effects=list(effects),
        termination_conditions=["the placement predicate measures true, or the budget is spent"],
        applicability=["one object, one tray"])


@pytest.fixture(scope="module")
def scene():
    return sandbox.SandboxScene.from_task(CASE)


@pytest.fixture(scope="module")
def proposal(scene):
    return V.Proposal(scene=scene, params={"object": ENTITY, "target": TARGET})


def _validated(spec, proposal, **kwargs) -> V.Validation:
    matrix = V.default_matrix(spec, proposal, **{k: v for k, v in kwargs.items()
                                                 if k in ("vocabulary", "kinds", "object_param",
                                                          "target_param", "include_repeat")})
    return V.validate_candidate(spec, proposal, matrix, candidate_id=f"cand_{spec.name}",
                                **{k: v for k, v in kwargs.items()
                                   if k in ("min_instances", "required_axes")})


@pytest.fixture(scope="module")
def good(proposal):
    spec = _spec()
    return _validated(spec, proposal)


@pytest.fixture(scope="module")
def unmeasurable(proposal):
    """Every instance ran, and no instance was measured: the case the frozen rule waves through."""
    spec = _spec("stacker", effects=("stacked:{object}",))
    return _validated(spec, proposal)


@pytest.fixture(scope="module")
def measured_false(proposal):
    """The parameter axis binds a different tray than the claim allows."""
    spec = _spec("always_left", effects=("placed:{object}:tray_left",))
    return _validated(spec, proposal)


# ----------------------------------------------------------------- 1. the axes ----
def test_the_axes_are_the_three_the_sentence_names(proposal):
    assert V.UNSEEN_KINDS == ("object", "layout", "parameters")
    assert set(V.AUXILIARY_KINDS) <= set(get_args(
        ValidationInstance.model_fields["instance_kind"].annotation))
    with pytest.raises(V.ValidationError_, match="not §5.6 axes"):
        V.default_matrix(_spec(), proposal, kinds=("object", "vibes"))


def test_an_unseen_object_comes_from_the_layout_and_not_from_the_test_file(scene, proposal):
    matrix = V.default_matrix(_spec(), proposal, kinds=("object",), include_repeat=False)
    instance = matrix.instances[0]
    assert instance.kind == "object"
    assert instance.params["object"] in scene.entity_ids()
    assert instance.params["object"] != proposal.params["object"]
    assert instance.differs == {"object": f"{ENTITY} -> {ENTITY_B}"}
    # the same world, so the only thing varied is the argument
    assert instance.scene.seed == scene.seed and instance.scene.label == scene.label


def test_the_layout_axis_varies_the_seed_and_records_it_in_the_origin(proposal):
    matrix = V.default_matrix(_spec(), proposal, kinds=("layout",), include_repeat=False)
    instance = matrix.instances[0]
    assert instance.params == dict(proposal.params)
    assert instance.scene.seed == proposal.scene.seed + 1
    assert instance.scene.origin.startswith("variant of frozen case")
    assert instance.differs == {"seed": f"{proposal.scene.seed} -> {proposal.scene.seed + 1}"}


def test_the_repeat_instance_is_identical_on_purpose_and_the_axes_are_not(proposal):
    matrix = V.default_matrix(_spec(), proposal)
    by_kind = {i.kind: i for i in matrix.instances}
    assert by_kind["repeat"].differs == {}
    assert all(by_kind[k].differs for k in V.UNSEEN_KINDS)
    assert matrix.axes_covered() == sorted(V.UNSEEN_KINDS)
    assert matrix.kinds() == sorted(["object", "layout", "parameters", "repeat"])


def test_an_axis_with_nothing_to_vary_is_reported_rather_than_faked(proposal):
    # one object and one region in the vocabulary: the remaining "unseen" instances would be
    # measurements of things that do not exist, which the PlanValidator refuses before anything moves
    matrix = V.default_matrix(_spec(), proposal,
                              vocabulary={"entities": [ENTITY], "targets": [TARGET]})
    assert [i.kind for i in matrix.instances] == ["layout", "repeat"]
    assert set(matrix.unavailable) == {"object", "parameters"}
    assert "unseen but absent" in matrix.unavailable["object"][0]
    assert "different world, which is the layout axis" in matrix.unavailable["parameters"][0]
    # the layout axis needs nothing from the vocabulary, so it survives on its own
    assert V.default_matrix(_spec(), proposal, kinds=("layout",),
                            vocabulary={"entities": [], "targets": []}).axes_covered() == ["layout"]


def test_a_program_with_no_region_parameter_is_visibly_not_tested_on_parameters(proposal):
    only_object = _spec("grab", effects=("grasp:{object}",),
                        description="pick the object and hold it",
                        parameters=[program.param("object", "entity", description="the object")],
                        steps=[program.step("pick", {"object_id": "{object}"}, step_id="s0",
                                            on_failure="retry_once")])
    matrix = V.default_matrix(only_object,
                              V.Proposal(scene=proposal.scene, params={"object": ENTITY}))
    assert matrix.axes_covered() == sorted(["layout", "object"])
    assert "declares no 'target' parameter" in matrix.unavailable["parameters"][0]
    # and an axis is never silently dropped: the claim and the difference are both recorded
    assert all(i.claim and (i.differs or i.kind == "repeat") for i in matrix.instances)


def test_the_fingerprint_commits_to_the_matrix_and_moves_only_with_it(proposal):
    spec = _spec()
    first = V.default_matrix(spec, proposal)
    same = V.default_matrix(_spec(), proposal)
    assert first.fingerprint() == same.fingerprint()
    other_target = V.default_matrix(spec, V.Proposal(scene=proposal.scene,
                                                    params={"object": ENTITY,
                                                            "target": "tray_middle"}))
    assert other_target.fingerprint() != first.fingerprint()
    fewer = V.default_matrix(spec, proposal, kinds=("object", "layout"))
    assert fewer.fingerprint() != first.fingerprint()
    assert len(first.fingerprint()) == 64


# -------------------------------------------------------------- 2. the vocabulary ----
def test_the_vocabulary_is_read_from_a_real_snapshot(scene):
    vocab = V.scene_vocabulary(scene)
    assert vocab["entities"] == sorted([ENTITY, ENTITY_B, "obj_yellow_3"])
    assert vocab["targets"] == sorted(["tray_left", "tray_middle", "tray_right"])
    # the trays are the scene's own: nothing in the layout mentions them, so this list cannot have
    # been copied out of a description
    assert not any("tray" in str(o.get("entity_id")) for o in scene.objects)


# ------------------------------------------------------------------- 3. the runs ----
def test_a_validated_program_varies_every_axis_and_measures_it(good):
    report, validation = good.report, good
    assert validation.passed_frozen and validation.passed_strict
    assert report.reasons == [] and validation.strict == []
    kinds = [i.instance_kind for i in report.instances]
    assert sorted(kinds) == ["layout", "object", "parameters", "repeat"]
    assert all(i.ran and i.success.value is True for i in report.instances)
    assert report.kinds_covered() == sorted(kinds)
    assert report.cost["axes_covered"] == list(V.UNSEEN_KINDS)
    assert report.cost["axes_unavailable"] == {}
    assert report.cost["fully_measured_true"] == len(report.instances)
    assert report.cost["model_calls"] == 0 and report.cost["sandbox_runs"] == 4
    assert report.cost["sim_s"] > 0.0 and report.cost["calls"] == 8


def test_the_object_axis_actuates_on_a_body_the_program_was_not_proposed_for(good):
    instance = next(i for i in good.instances if i.instance_kind == "object")
    assert instance.args["object"] == ENTITY_B
    assert instance.effects_observed == [f"placed:{ENTITY_B}:{TARGET}"]
    assert any(ref.startswith("differs:object=") for ref in instance.evidence_refs)
    assert instance.evidence_refs[0].startswith("claim:")
    # a real run, with its own observation refs, not a summary of one
    run = next(r for r in good.runs if r.params["object"] == ENTITY_B)
    assert run.accepted and run.terminated_by == "procedure_complete"
    assert all(ref.startswith(sandbox.SANDBOX_OBS_PREFIX) for ref in run.effects[0]["evidence_refs"])


def test_the_layout_axis_actuates_on_a_world_the_program_never_saw(good, proposal):
    instance = next(i for i in good.matrix.instances if i.kind == "layout")
    run = next(r for r in good.runs if r.scene_label == instance.scene.label)
    assert instance.scene.seed == proposal.scene.seed + 1
    assert run.scene_seed == proposal.scene.seed + 1
    assert run.scene_origin.startswith("variant of frozen case")
    # the same three bodies, in a world that placed them differently
    assert run.entities_seen == sorted([ENTITY, ENTITY_B, "obj_yellow_3"])
    assert run.outcome()[0] is True
    recorded = next(i for i in good.instances if i.instance_kind == "layout")
    assert recorded.seed == proposal.scene.seed + 1
    assert recorded.scene_id == instance.scene.label
    assert recorded.effects_observed == [f"placed:{ENTITY}:{TARGET}"]


def test_an_axis_measured_false_fails_both_readings(measured_false):
    report = measured_false.report
    assert not report.passed and report.reasons == ["an instance failed the effect check"]
    assert not measured_false.passed_strict
    assert any("were measured and the world disagreed" in f for f in measured_false.strict)
    failed = [i for i in report.instances if i.success.value is False]
    assert [i.instance_kind for i in failed] == ["parameters"]
    assert report.cost["fully_measured_true"] == 3


def test_the_unmeasurable_candidate_passes_the_frozen_rule_and_nothing_else(unmeasurable):
    """The hole, measured rather than described.

    Four instances ran, every one of them clean of `unsafe` and of `violations`, and none of them
    measured anything: `stacked:` is a claim no shipped predicate answers, so `success.value` is
    `"unknown"`, which `_derive_passed` does not treat as a failure. The frozen `passed` is therefore
    `True` on a skill that was never verified — and the strict reading, `memory.admission_findings`
    and §11's stricter count are all of the ways that gets caught.
    """
    report = unmeasurable.report
    assert report.passed and report.reasons == []
    assert not unmeasurable.passed_strict
    assert any("have no measured verdict" in f for f in unmeasurable.strict)
    assert report.cost["fully_measured_true"] == 0
    assert all(str(i.success.value) == "unknown" for i in report.instances)
    assert all("unanswerable_stem:stacked" in u for i in report.instances for u in i.success.unmeasured)
    stacker = _spec("stacker", effects=("stacked:{object}",))
    findings = memory.admission_findings(_candidate(stacker), report)
    assert any("strict reading did not pass" in f for f in findings)


def test_a_refused_program_stays_a_refusal_inside_the_report(scene):
    """§12.4 through the gate: an instance that never ran cannot be counted, listed or not.

    The configuration is built by hand here because no axis constructor would invent an object the
    layout does not offer — that refusal is the point of `default_matrix`, and this is the same rule
    seen from the other side.
    """
    spec = _spec()
    proposal = V.Proposal(scene=scene, params={"object": ENTITY, "target": TARGET})
    matrix = V.Matrix(proposal=proposal, instances=[
        V.Instance(kind=kind, scene=scene, params={"object": "obj_nope", "target": TARGET},
                   claim=f"hand-built {kind} instance against an absent object",
                   differs={"object": f"{ENTITY} -> obj_nope"})
        for kind in V.UNSEEN_KINDS])
    result = V.validate_candidate(spec, proposal, matrix, candidate_id="cand_absent")
    assert all(not i.ran for i in result.instances)
    assert all(i.success.value == "unknown" for i in result.instances)
    assert all(i.effects_observed == [] and i.violations == [] for i in result.instances)
    assert not result.report.passed
    assert result.report.reasons == ["only 0 of 3 required instances ran",
                                     "ran on no instance kind; cross-instance validation needs >= 2"]
    assert any("listed instances never ran" in f for f in result.strict)
    assert any("axes not varied" in f and "object" in f for f in result.strict)
    # the unmeasured-verdict finding is about instances that ran and could not be answered; a refusal
    # is reported by its own line, and the two are not pooled
    assert not any("have no measured verdict" in f for f in result.strict)
    assert result.census()["executed"] == 0
    assert result.census()["refused_before_execution"] == 3
    # the refusal's own words are readable in the record, not merely its absence of a verdict
    assert any("plan_validator" in ref for i in result.instances for ref in i.evidence_refs)
    assert any("is not in observation" in ref for i in result.instances for ref in i.evidence_refs)


def test_the_cost_block_carries_the_fingerprint_the_matrix_committed_to(good):
    """`SkillValidationReport` is frozen, so the extras go in the field it declares for them.

    Asserted as a set difference as well: if a future measurement ever needs its own field, that is a
    re-freeze of `core/v02.py` and this test is what makes it a decision rather than an accident.
    """
    report = good.report
    own = set(SkillValidationReport.model_fields) - set(Record.model_fields)
    assert own == {"report_id", "candidate_id", "skill_name", "instances", "min_instances",
                   "passed", "reasons", "cost"}
    assert report.cost["matrix_fingerprint"] == good.matrix.fingerprint()
    assert report.cost["proposal"]["params"] == dict(good.matrix.proposal.params)
    assert report.cost["proposal"]["seed"] == good.matrix.proposal.scene.seed
    assert report.cost["proposal"]["origin"] == good.matrix.proposal.scene.origin
    for key in ("wall_s", "sim_s", "calls", "model_calls", "sandbox_runs", "instances_listed",
                "instances_ran", "kinds_covered", "axes_required", "axes_covered",
                "axes_unavailable", "fully_measured_true", "unmeasurable_claims",
                "runs_with_violations", "runs_with_unsafe_findings", "matrix_fingerprint"):
        assert key in report.cost, key


def test_validation_refuses_to_run_a_program_that_is_not_a_program(proposal):
    """The representation gate is re-checked here rather than trusted from the caller."""
    broken = SkillSpec(name="teleport_thing", description="d", level="acquired",
                       parameters=[SkillParameter(name="object", type="entity")],
                       preconditions=["p"], expected_effects=["placed:{object}:tray_left"],
                       termination_conditions=["t"], applicability=["a"],
                       procedure=[ProcedureStep(step_id="s0",
                                                intent=ProcedureIntent(skill="teleport",
                                                                       args={"object_id": "{object}"}))])
    matrix = V.Matrix(proposal=proposal, instances=V.default_matrix(
        _spec(), proposal).instances)
    with pytest.raises(V.ValidationError_, match="not an executable program"):
        V.validate_candidate(broken, proposal, matrix, candidate_id="cand_broken")


def test_two_axes_out_of_three_pass_the_frozen_rule_and_fail_this_one(proposal):
    """The frozen gate asks for 2 kinds; §5.6's sentence asks for 3 axes. Both numbers are reported.

    Object and layout varied, every instance measured true, nothing unsafe and nothing violated: the
    shipped `_derive_passed` is satisfied and prints no reasons at all. The parameters axis was never
    touched — which is exactly the row §11's `cross-instance transfer` has to be able to see, and the
    reason `passed` alone is not what `SkillMemory.admit` asks for.
    """
    spec = _spec()
    matrix = V.default_matrix(spec, proposal, kinds=("object", "layout"))
    result = V.validate_candidate(spec, proposal, matrix, candidate_id="cand_two_axes")
    assert [i.instance_kind for i in result.instances] == ["object", "layout", "repeat"]
    assert result.report.passed and result.report.reasons == []
    assert all(i.success.value is True for i in result.instances)
    assert not result.passed_strict
    assert any("axes not varied" in f and "parameters" in f for f in result.strict)
    assert result.report.cost["axes_covered"] == ["object", "layout"]
    assert result.report.min_instances == 3
    findings = memory.admission_findings(_candidate(spec, "cand_two_axes"), result.report)
    assert any("axes never varied" in f and "parameters" in f for f in findings)


# ------------------------------------------------------------------ 4. artifacts ----
def test_write_validation_leaves_one_file_per_run_and_one_for_the_report(good, tmp_path):
    path = V.write_validation(good, str(tmp_path))
    assert os.path.exists(path) and path.endswith("cand_move_object.json")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["passed_frozen_rule"] is True and doc["passed_strict_reading"] is True
    assert len(doc["runs"]) == doc["cost"]["sandbox_runs"] == 4
    assert doc["matrix"]["fingerprint"] == good.matrix.fingerprint()
    assert sorted(os.listdir(os.path.join(str(tmp_path), "sandbox"))) == sorted(
        f"{r.plan_id}.json" for r in good.runs)
    assert sorted(os.listdir(os.path.join(str(tmp_path), "validation"))) == [
        "cand_move_object.json"]
    # the report on disk re-reads as the same object the gate returned
    assert SkillValidationReport.model_validate(doc["report"]).passed is True


def test_the_validation_layer_imports_no_answer_key(tmp_path):
    """The same prohibition as the sandbox, one box later in the pipeline.

    Checked by module text rather than by argument list, because by this point the runs are already
    made and what could leak is the *selection* of instances or the reading of a verdict.
    """
    source = inspect.getsource(V)
    for forbidden in ("from ..evaluation", "import evaluation", "eval_spec", "expected[",
                      "episode_summary"):
        assert forbidden not in source, forbidden
    assert "from ..core.verify import build_world_state" in source


# ---------------------------------------------------------------- helpers ----
def _candidate(spec: SkillSpec, candidate_id: str = ""):
    """The candidate row a validation is keyed to.

    Hand-assembled, because P4-c's subject is the gate and not the proposal step: `proposed_by` says
    which, and no `SkillCandidate` written here ever claims a model produced it.
    """
    from embodied_agent.core.v02 import SkillCandidate
    return SkillCandidate(spec=spec, proposed_by="test-fixture", status="sandbox",
                          candidate_id=candidate_id or f"cand_{spec.name}")
