"""Contract tests for SPEC-v0.2 §5.6 steps 2-4: sandbox execution and the verifier gate.

Three kinds of claim, and they need different evidence:

1. *Structure* — the prohibitions are properties of the module's own text and signatures, so they are
   checked without running anything, and the check is on both sides ("absent here" plus "present in
   the file that does use it", because a token that exists nowhere is a free pass).
2. *The audit* — `audit_findings` is exercised on synthetic logs, including the exact row a
   teleport would leave, since a gate that has never caught anything has not been tested.
3. *Real physics* — a handful of programs run through the shipped `SkillExecutor` on scenes built
   from frozen cases, and the verdict comes from `core/verify.py`, not from this test file. The
   scenes are the slow part, so every run is module-scoped and reused by the assertions about it.

Offline, deterministic, no model call: `model_calls == 0` is asserted, because a "validated" skill
that quietly spent tokens in its sandbox would make §11's cost rows meaningless.
"""
from __future__ import annotations

import inspect
import json
import os
from typing import get_args

import pytest
from pydantic import ValidationError

from embodied_agent.acquisition import program, sandbox
from embodied_agent.core.contracts import SkillStatus
from embodied_agent.core.v02 import EVENT_MODULE, SkillSpec, ValidationInstance

CASE = "smoke_clean"
ENTITY = "obj_green_1"
ENTITY_B = "obj_blue_2"
TARGET = "tray_left"


# ------------------------------------------------------------------ fixtures ----
@pytest.fixture(scope="module")
def scene():
    return sandbox.SandboxScene.from_task(CASE)


def _spec(name="place_straight", effects=("placed:{object}:{target}",), steps=None) -> SkillSpec:
    return program.program(
        name, "pick the object, then place it in the named tray",
        [program.param("object", "entity", description="the object to move"),
         program.param("target", "region", description="the tray to put it in")],
        steps or [program.step("pick", {"object_id": "{object}"}, step_id="s0",
                               on_failure="retry_once"),
                  program.step("place", {"object_id": "{object}", "target_id": "{target}"},
                               step_id="s1")],
        preconditions=["the gripper is empty and the object is reachable"],
        expected_effects=list(effects),
        termination_conditions=["the placement predicate measures true, or the budget is spent"],
        applicability=["one object, one tray"])


@pytest.fixture(scope="module")
def good_run(scene):
    """The one expensive run everything else about success is read from."""
    return sandbox.run_program(_spec(), {"object": ENTITY, "target": TARGET}, scene)


@pytest.fixture(scope="module")
def refused_run(scene):
    ghost = _spec("pick_a_ghost", effects=["placed:obj_not_here:{target}"], steps=[
        program.step("pick", {"object_id": "obj_not_here"}, step_id="s0"),
        program.step("place", {"object_id": "obj_not_here", "target_id": "{target}"},
                     step_id="s1")])
    return sandbox.run_program(ghost, {"object": "obj_not_here", "target": TARGET}, scene)


# ------------------------------------------------- 1. the gate as structure ----
def test_the_prohibited_names_are_absent_here_and_present_somewhere():
    gates = sandbox.source_gates()
    assert gates["absent_from_scanned_code"] == list(sandbox.FORBIDDEN_IN_SANDBOX)
    assert gates["present_in_scanned_code"] == []
    # "absent" is only a fact if the token is a real thing that lives elsewhere; a list of strings
    # nobody ever wrote would pass this test forever and prove nothing
    assert all(v.startswith("present in ") for v in gates["origins"].values()), gates["origins"]
    assert len(gates["origins"]) == len(sandbox.FORBIDDEN_IN_SANDBOX)


def test_the_scanned_region_is_the_code_and_not_the_declaration_of_the_marker():
    code = sandbox.scanned_code()
    # the region must begin after the marker line, so the constants that *name* the prohibitions are
    # not inside the text being searched for them
    assert "FORBIDDEN_IN_SANDBOX" not in code
    assert "def run_program" in code and "def source_gates" not in code
    assert code.startswith("\n") or code.lstrip().startswith("#")


def test_the_end_of_the_scanned_region_cannot_be_used_to_hide_an_actuator():
    """The two gate functions must sit after the end marker — and the tail is checked too.

    Without this half, moving `run_program` below the end marker would shrink the scanned region until
    it contained nothing and still print a clean report. The tail may name the prohibitions; it may
    not hold a handle on a world.
    """
    tail = sandbox.unscanned_tail()
    gates = sandbox.source_gates()
    assert gates["present_in_unscanned_tail"] == []
    assert gates["absent_from_unscanned_tail"] == list(sandbox.TAIL_MUST_NOT_NAME)
    assert "def scanned_code" in tail and "def source_gates" in tail
    # every world-touching import in the module is inside the region, so the region is most of it
    assert gates["scanned_chars"] > len(tail)
    assert "from ..core.scene import PhysicsScene" in sandbox.scanned_code()


def test_no_scene_writer_is_called_from_this_module():
    assert sandbox.source_gates()["scene_writers_called_here"] == []
    # and the list of writers is not empty decoration: every one of them is a real scene method
    from embodied_agent.core.scene import PhysicsScene
    for writer in sandbox.SCENE_WRITERS:
        assert callable(getattr(PhysicsScene, writer, None)), writer


def test_no_parameter_of_the_runner_can_carry_a_live_world():
    gates = sandbox.source_gates()
    assert gates["run_program_parameters"] == ["spec", "params", "scene", "plan_id", "call_cap"]
    # the world the sandbox runs in is described by value: nothing in the description can hold a
    # scene object, which is why "it cannot write the episode's world" needs no runtime enforcement
    assert set(gates["sandbox_scene_fields"]) == {"label", "seed", "objects", "verify", "origin"}


def test_the_forbidden_token_list_does_not_include_itself_in_the_scanned_text():
    # a scan of the whole module would find its own vocabulary; the marker is what keeps that honest
    source = sandbox.scanned_code() + sandbox.SCAN_FROM
    assert sandbox.SCAN_FROM in source
    assert "import pybullet" not in sandbox.scanned_code()


# ------------------------------------------------------------- 2. the audit ----
def test_a_writer_reached_from_the_skills_is_clean_and_from_anywhere_else_is_not():
    assert sandbox.audit_findings([{"attr": "get:move_ee", "site": "skills.py:_do_pick"}]) == []
    assert sandbox.audit_findings(
        [{"attr": "get:move_ee", "site": "skills.py:_do_pick"},
         {"attr": "get:reset_arm", "site": "sandbox.py:run_program"}]) == [
        "scene writer reset_arm reached from sandbox.py:run_program, outside "
        f"{list(sandbox.WRITER_SITES)}"]
    # a placement plan reaching the same writer is still the shipped code, not the sandbox
    assert sandbox.audit_findings(
        [{"attr": "get:object_pose", "site": "placement_planner.py:plan"}]) == []


def test_the_teleport_that_never_happened_would_still_be_caught():
    """The gate's own negative case: the row a `resetBasePositionAndOrientation` leaves is a violation.

    Asserted against the audit function rather than by attempting it, because attempting it would mean
    writing the one piece of code this module exists to not have.
    """
    found = sandbox.audit_findings([{"attr": "get:settle", "site": "sandbox.py:do_attempt"}])
    assert len(found) == 1 and "settle" in found[0]
    # one place in the module makes a world, which is why "the sandbox cannot touch the episode's
    # scene" is a statement about a call site rather than about discipline
    assert sandbox.scanned_code().count("PhysicsScene(") == 1
    assert "def open(self)" in sandbox.scanned_code()
    assert "set:held_bodies" in json.dumps(
        sandbox.audit_findings([{"attr": "set:held_bodies", "site": "skills.py:_do_pick"}]))


def test_a_scene_attribute_write_is_a_violation_even_from_the_shipped_files():
    # the executor reads the scene and never assigns to it; a `set:` row means someone bypassed it
    assert sandbox.audit_findings([{"attr": "set:sim_time", "site": "skills.py:_do_place"}]) != []


def test_effect_templates_bind_and_say_which_parameter_refused_to_bind():
    assert sandbox.bind_effect("placed:{object}:{target}",
                               {"object": ENTITY, "target": TARGET}) \
        == (f"placed:{ENTITY}:{TARGET}", [])
    bound, unresolved = sandbox.bind_effect("placed:{object}:{target}", {"object": ENTITY})
    assert bound is None and unresolved == ["target"]
    assert sandbox.bind_effect("already concrete", {}) == ("already concrete", [])
    assert sandbox._effect_entity(f"placed:{ENTITY}:{TARGET}") == ENTITY


# ------------------------------------------------------------ 3. real physics ----
def test_a_honest_program_runs_and_the_verifier_is_the_one_that_says_so(good_run):
    run = good_run
    assert run.accepted and run.ran and not run.refusals
    assert run.terminated_by == "procedure_complete"
    assert [s.status for s in run.steps] == ["completed", "completed"]
    assert run.measured() == [f"placed:{ENTITY}:{TARGET}"]
    assert run.outcome()[0] is True
    # the numbers behind the verdict came from the verifier, not from this test
    effect = run.effects[0]
    assert effect["source"].endswith("privileged")
    assert {"xy_err_x", "tray_contacts", "speed_mps"} <= set(effect["evidence"])
    assert effect["evidence_refs"] and effect["evidence_refs"][0].startswith(sandbox.SANDBOX_OBS_PREFIX)


def test_each_step_is_measured_against_the_snapshot_around_it(good_run, scene):
    pick, place = good_run.steps
    assert pick.effect["predicate_id"] == f"grasp:{ENTITY}"
    assert pick.effect["value"] == "true"
    # lift gain is a comparison: the pre-pick snapshot is what makes this measurable at all
    assert pick.effect["evidence"]["lift_gain_m"] > pick.effect["evidence"]["lift_gain_min_m"]
    assert place.effect["predicate_id"] == f"placed:{ENTITY}:{TARGET}"
    assert place.effect["value"] == "true"
    # the four sub-facts `verify_placement` composes, as the numbers it reported
    assert place.effect["evidence"]["in_gripper_contact"] == 0.0
    assert place.effect["evidence"]["tray_contacts"] >= 1.0
    assert place.effect["evidence"]["speed_mps"] < scene.verify.speed_tol_mps
    assert abs(place.effect["evidence"]["rest_z_measured"]
               - place.effect["evidence"]["rest_z_expected"]) < scene.verify.support_height_tol_m
    # the claim is answered on a snapshot of this run's own world, by ref
    assert place.effect["evidence_refs"][0].startswith(sandbox.SANDBOX_OBS_PREFIX)
    assert place.effect["unmeasured"] == []


def test_a_program_that_declares_an_intermediate_state_as_its_end_state_fails(scene):
    """`false` with numbers, not `unknown`: the claim was checked and the world disagreed.

    This is the sharp edge of §12.3. After a successful place the object is no longer grasped, so a
    spec that lists `grasp:{object}` among its *expected effects* is a badly written candidate, and
    the sandbox has to be able to say so rather than reporting an absence of measurement.
    """
    run = sandbox.run_program(_spec("claims_the_grasp_too",
                                    effects=["grasp:{object}", "placed:{object}:{target}"]),
                              {"object": ENTITY_B, "target": "tray_right"}, scene)
    assert run.terminated_by == "procedure_complete"
    assert [e["value"] for e in run.effects] == ["false", "true"]
    assert run.outcome()[0] is False
    assert any(e["predicate_id"] == f"grasp:{ENTITY_B}" for e in run.effects)
    grasp = next(e for e in run.effects if e["predicate_id"] == f"grasp:{ENTITY_B}")
    assert grasp["unmeasured"] == [] and grasp["evidence"]["lift_gain_m"] < 0.03


def test_a_claim_no_verifier_predicate_answers_is_recorded_as_unmeasurable(scene):
    run = sandbox.run_program(_spec("claims_stacking", effects=["stacked:{object}"]),
                              {"object": ENTITY, "target": TARGET}, scene)
    assert run.outcome()[0] is None
    assert run.effects[0]["value"] == "unknown"
    assert run.effects[0]["unmeasured"] == ["unanswerable_stem:stacked"]
    assert run.unmeasured() == [f"stacked:{ENTITY}:unanswerable_stem:stacked"]
    # the steps still ran; an unverifiable *claim* is not a failed *act*, and conflating them is how
    # a metric ends up scoring the vocabulary a candidate happened to use
    assert [s.status for s in run.steps] == ["completed", "completed"]


def test_an_object_the_layout_never_offered_is_refused_before_anything_moves(refused_run):
    run = refused_run
    assert not run.accepted and not run.ran
    assert run.terminated_by == "plan_validator"
    assert any("is not in observation" in r for r in run.refusals)
    assert run.steps == [] and run.effects == []
    # the production validator's words, not a paraphrase of them
    assert "step s0:" in run.refusals[0]


def test_the_sandbox_sees_the_scene_the_scene_has(scene):
    run = sandbox.run_program(_spec(), {"object": ENTITY, "target": TARGET}, scene)
    assert run.entities_seen == ["obj_blue_2", "obj_green_1", "obj_yellow_3"]
    assert run.targets_seen == ["tray_left", "tray_middle", "tray_right"]
    # a target outside that list is the validator's business, and it says so
    bad = sandbox.run_program(_spec("wrong_tray"), {"object": ENTITY, "target": "tray_nowhere"},
                              scene)
    assert bad.terminated_by == "plan_validator"
    assert not bad.ran


def test_a_parameter_the_caller_withholds_is_a_refusal_not_a_blind_call(scene):
    run = sandbox.run_program(_spec(), {"object": ENTITY}, scene)
    assert run.terminated_by == "binding_error" and not run.ran
    assert "target" in run.refusals[0]
    # the failure mode this forecloses: `pick` running with no object and reading back as physics
    assert run.steps == []


def test_a_variant_layout_is_a_different_world_with_its_own_provenance(scene):
    variant = scene.variant(seed=scene.seed + 1)
    assert variant.seed == scene.seed + 1 and variant.origin.startswith("variant of")
    assert "seed" in variant.origin
    assert variant.objects == scene.objects and variant.objects is not scene.objects
    run = sandbox.run_program(_spec(), {"object": ENTITY_B, "target": TARGET}, variant)
    assert run.scene_seed == scene.seed + 1 and run.scene_origin == variant.origin
    assert run.outcome()[0] is True
    # editing the description must not be able to edit the case: the case's own layout is untouched
    assert scene.variant(seed=7).seed == 7 and scene.seed != 7


def test_the_budget_is_a_cap_and_the_scene_is_always_closed(good_run, refused_run, scene):
    assert good_run.calls_used <= good_run.call_cap
    assert good_run.call_cap == program.max_calls(_spec())
    tight = sandbox.run_program(_spec("one_attempt", steps=[
        program.step("pick", {"object_id": "{object}"}, step_id="s0", on_failure="retry_once"),
        program.step("place", {"object_id": "{object}", "target_id": "{target}"}, step_id="s1")]),
        {"object": ENTITY, "target": TARGET}, scene, call_cap=1)
    assert tight.calls_used == 1 and tight.terminated_by == "call_cap"
    assert tight.outcome()[0] is False       # stopped early, whatever the steps did
    assert all(r.closed for r in (good_run, refused_run, tight))
    assert good_run.sim_s > 0.0              # real seconds of physics, not a stub


def test_no_model_spend_and_no_event_type_invented(good_run):
    instance = sandbox.validation_instance(good_run, "object")
    assert instance.model_calls == 0
    # the zero is structural, not a counter: nothing in the scanned region imports a model channel,
    # and nothing in it writes an episode record either — P4-b returns a run, P4-d files the event
    gates = sandbox.source_gates()
    assert gates["model_channels_imported"] == []
    assert gates["event_writes_here"] == []
    assert set(sandbox.SANDBOX_EVENT_TYPES) <= set(EVENT_MODULE)
    assert all(EVENT_MODULE[t] == "skill_acquisition" for t in sandbox.SANDBOX_EVENT_TYPES)
    # and the run's own audit has no write row in it
    assert not [a for a in good_run.audit if a["attr"].startswith("set:")]


def test_the_same_program_on_the_same_world_says_the_same_twice(scene):
    first = sandbox.run_program(_spec(), {"object": ENTITY, "target": TARGET}, scene)
    second = sandbox.run_program(_spec(), {"object": ENTITY, "target": TARGET}, scene)
    strip = lambda r: (r.terminated_by, [s.status for s in r.steps],
                       [(e["predicate_id"], e["value"]) for e in r.effects], r.calls_used,
                       r.outcome()[0], r.violations, r.unsafe)
    assert strip(first) == strip(second)
    assert first.plan_id != second.plan_id or first.skill_id != second.skill_id


# ------------------------------------------------- 4. the frozen record glue ----
def test_a_run_becomes_the_object_the_gate_reads(good_run, refused_run):
    ok = sandbox.validation_instance(good_run, "object")
    assert ok.ran and ok.success.value is True
    assert ok.effects_observed == [f"placed:{ENTITY}:{TARGET}"]
    assert ok.violations == [] and ok.unsafe == [] and ok.seed == good_run.scene_seed
    # the kind is one of the frozen vocabulary of `ValidationInstance`, not a free string
    assert ok.instance_kind in get_args(ValidationInstance.model_fields["instance_kind"].annotation)
    refused = sandbox.validation_instance(refused_run, "layout")
    assert refused.ran is False and refused.success.value == "unknown"
    assert refused.effects_observed == [] and refused.violations == []
    # the refusal is still readable, which is the whole point of §12.4 rather than a silence
    assert any("plan_validator" in r for r in refused.evidence_refs)


def test_an_unrun_instance_cannot_be_forced_to_carry_a_verdict(refused_run):
    """The frozen guard, exercised from the sandbox side.

    `validation_instance` gets this right by construction; the test is that the *model* also refuses,
    so a future caller that assembles the fields by hand cannot pass an instance that never ran.
    """
    refused = sandbox.validation_instance(refused_run, "object")
    base = refused.model_dump()
    with pytest.raises(ValidationError) as exc:
        ValidationInstance(**{**base, "success": {"value": True}})
    assert "did not run cannot report a verdict" in str(exc.value)
    with pytest.raises(ValidationError):
        ValidationInstance(**{**base, "effects_observed": [f"placed:{ENTITY}:{TARGET}"]})


def test_the_unsafe_list_is_the_executors_physical_codes_not_any_failure(scene):
    """A skill that does not work and a skill that is dangerous are different rows.

    §11's unsafe-invalid-skill rate is an integrity claim, so pooling it with ordinary failure would
    let a hard scene read as a dangerous skill. The instance asserted here is the honest, boring kind
    of failure: a program that ran both of its calls exactly as asked and whose declared terminal
    claim is about the wrong tray. Nothing was disturbed, so `unsafe` stays empty while the run is
    still a `False`. The physical codes cannot be conjured without a rigged scene, so what is asserted
    about them is the vocabulary, and that no real run in this file reports one.
    """
    from embodied_agent.core.contracts import FailureCode
    codes = {c.value: c.name for c in FailureCode}
    assert set(sandbox.UNSAFE_CODES) <= set(codes)
    # the vocabulary is the executor's own, not a list invented here: each of the three is a code the
    # shipped skill layer can actually return
    source = inspect.getsource(__import__("embodied_agent.core.skills", fromlist=["SkillExecutor"]))
    for value in sandbox.UNSAFE_CODES:
        assert value in codes
        assert f"FailureCode.{codes[value]}" in source, value
    failed = sandbox.run_program(
        _spec("places_in_the_wrong_tray", effects=["placed:{object}:tray_middle"]),
        {"object": ENTITY, "target": TARGET}, scene)
    assert failed.ran and failed.terminated_by == "procedure_complete"
    assert [s.status for s in failed.steps] == ["completed", "completed"]
    assert all(s.failure_code is None for s in failed.steps)
    assert failed.unsafe == [] and failed.outcome()[0] is False
    assert failed.measured() == []            # the claim that failed is the one it made
    assert set(sandbox.COMPLETE_STATUSES) <= {s.value for s in SkillStatus}


def test_a_real_run_leaves_an_audit_that_names_the_skills_and_nothing_else(good_run, scene):
    """The audit's positive case, on real physics rather than a synthetic log.

    Every writer row comes from one of the three shipped implementation files, and the four scene
    handles a disruption would need — the arm reset, a joint teleport, an impulse, a held-position
    write — are never asked for at all. That is measured, not asserted from the module's own text.
    """
    assert good_run.violations == []
    writers = {a["attr"]: a["site"] for a in good_run.audit
               if a["attr"].split(":")[-1] in sandbox.SCENE_WRITERS}
    assert writers, "a run that actuated nothing would make this test vacuous"
    for attr, site in writers.items():
        assert site.split(":")[0] in sandbox.WRITER_SITES, (attr, site)
    for absent in ("get:reset_arm", "get:move_joints", "get:apply_environment_impulse",
                   "get:hold_position"):
        assert absent not in {a["attr"] for a in good_run.audit}
    assert not [a for a in good_run.audit if a["attr"].startswith("set:")]
    # and the same gate, read the other way: it does have teeth on a log that names a writer
    assert sandbox.audit_findings([{"attr": key, "site": "sandbox.py:run_program"}
                                  for key in writers])


def test_a_refusal_is_not_a_failure_and_both_are_counted_apart(scene, tmp_path):
    runs = [sandbox.run_program(_spec(), {"object": ENTITY, "target": TARGET}, scene),
            sandbox.run_program(_spec("ghost", effects=["placed:obj_nope:{target}"], steps=[
                program.step("pick", {"object_id": "obj_nope"}, step_id="s0")]),
                {"object": "obj_nope", "target": TARGET}, scene),
            sandbox.run_program(_spec("claims_stacking_again", effects=["stacked:{object}"]),
                                {"object": ENTITY, "target": TARGET}, scene)]
    census = sandbox.run_census(runs)
    assert census["runs"] == 3 and census["executed"] == 2
    assert census["refused_before_execution"] == 1
    assert census["success"] == 1 and census["unknown"] == 1 and census["failure"] == 0
    assert sum(census["terminated_by"].values()) == census["runs"]
    assert census["runs_with_violations"] == 0
    assert census["scene_closed_on_every_run"] and census["max_call_cap_respected"]
    # two declared effects, not three: the refused run's template never reached a verifier, because it
    # never reached a world. Counting it as declared would credit a broken candidate with a measurement
    assert census["effects_verified_true"] == 1 and census["effects_declared"] == 2
    assert census["effects_unmeasurable"] == 1          # the stacking claim, and only it
    assert census["runs_with_unsafe_findings"] == 0
    assert census["terminated_by"] == {"procedure_complete": 2, "plan_validator": 1}


def test_a_run_can_be_handed_to_a_reader_as_one_file(tmp_path, good_run):
    path = sandbox.write_run(good_run, str(tmp_path))
    assert os.path.exists(path) and path.endswith(f"{good_run.plan_id}.json")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["outcome"] == "success" and doc["ran"] is True
    assert [s["step_id"] for s in doc["steps"]] == ["s0", "s1"]
    assert doc["outcome_reasons"] == [] and doc["violations"] == []
    # every effect row carries the numbers behind its verdict, or the name of what was missing
    assert all({"value", "description"} <= set(e) for e in doc["effects"])
