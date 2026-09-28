"""SPEC-v0.2 §5.5/§5.6 contract tests for the program-skill representation.

The claim under test is the one §5.6 turns on: **an acquired skill is a program over the primitives
that already exist**, so it inherits every rule the frozen action space has and adds no new one.
Four groups, in the order a reader should meet them.

1. The capability set (`PRIMITIVES`, the arg tables). If these drift from `SkillRegistry.CATALOGUE`
   and `core/planner.py`'s `REQUIRED_ARGS`/`OPTIONAL_ARGS`, everything else in the module is
   decorating a private definition of the action space.
2. The gate (`check`, and `program` raising it). Each refusal is a defect the *executor* would
   otherwise report as an ordinary world-shaped failure — an unknown name comes back as
   `rejected/'unknown skill'`, an unbindable placeholder as `KeyError`, an extra argument as the
   primitive silently not reading it. Refusing at representation time is what keeps a broken
   candidate out of the evidence for "the agent acquired a skill".
3. Binding (`used_parameters`, `bind_calls`, `as_plan`, `max_calls`): a template becomes calls, and
   both that nothing new appears and that nothing the runtime does not implement is smuggled in.
4. The view (`render`), bounded for the same reason `episodic/retrieval.py`'s is: a description that
   reaches a prompt uncapped is a budget leak, and one truncated silently is a different program.

One split is asserted explicitly rather than left implicit, because it is the easiest thing to get
wrong by optimism: `check` judges *representability*, not success. A program that puts `place`
before `pick` passes `check` and is refused by `PlanValidator`, the production validator — see
`test_the_representation_gate_does_not_secretly_become_a_planner`. A representation module that also
planned would be a second source of strategic choice, which §3.4 and §5.7 put in the model.
"""
from __future__ import annotations

import json
from typing import get_args

import pytest

from embodied_agent.acquisition import program
from embodied_agent.core.contracts import PlanStep, SkillCall, SkillName, WorldState
from embodied_agent.core.planner import OPTIONAL_ARGS, REQUIRED_ARGS, PlanValidator
from embodied_agent.core.skills import SkillRegistry
from embodied_agent.core.v02 import ProcedureStep, SkillSpec


# ------------------------------------------------------------------ fixtures ----
def _grasp_program(**overrides):
    """The program the `lh_c4` gap family would propose: look, retreat, then try the next grasp."""
    kwargs = dict(
        name="grasp_after_clear_look",
        description="re-measure, retreat, then grasp at a named candidate index",
        parameters=[program.param("object"),
                    program.param("index", "number", example=1)],
        procedure=[program.step("observe", {}),
                   program.step("safe_retreat", {}),
                   program.step("pick", {"object_id": "{object}",
                                         "grasp_candidate_index": "{index}"},
                                on_failure="retry_once", step_id="grasp")],
        preconditions=["the object is in the current observation"],
        expected_effects=["grasp:{object}"],
        termination_conditions=["the grasp predicate is measured true",
                                "the second grasp attempt is measured"],
        applicability=["a pick that failed once with a measured grasp miss"],
    )
    kwargs.update(overrides)
    return program.program(**kwargs)


def _one_step(skill="pick", args=None, *, name="one_step", **step_kwargs):
    return _grasp_program(name=name, description="d", parameters=[],
                          procedure=[program.step(skill, args or {}, **step_kwargs)])


def _shape(calls):
    """A call list without its freshly drawn ids: `call_id` is a `new_id`, so two bindings of the
    same template differ there and nowhere else."""
    return [(c.step_id, c.skill, dict(c.args), c.timeout_s) for c in calls]


# ------------------------------------------------------------- 1. capability set ----
def test_a_program_may_name_exactly_the_shipped_primitives():
    assert program.PRIMITIVES == tuple(sorted(SkillRegistry.CATALOGUE))
    assert set(program.PRIMITIVES) == {"observe", "pick", "place", "safe_retreat"}
    # The frozen `SkillName` Literal is wider than this executor (the text backend's verbs live
    # there too), and it is the *catalogue* that bounds a program. Asserted as a strict subset so
    # the difference stays a decision a reader can see rather than an oversight.
    assert set(program.PRIMITIVES) < set(get_args(SkillName))
    assert "teleport" not in program.PRIMITIVES
    assert "teleport" not in get_args(SkillName)


def test_the_arg_tables_come_from_the_validator_and_agree_with_the_catalogue():
    for skill in program.PRIMITIVES:
        required, optional = program.declared_args(skill)
        assert (required, optional) == (frozenset(REQUIRED_ARGS[skill]),
                                        frozenset(OPTIONAL_ARGS[skill]))
        assert required | optional == set(SkillRegistry.CATALOGUE[skill]["args"]), skill
        # `required` is written down only in the catalogue's prose; read it back, or the two
        # tables are free to drift apart while `check` keeps reporting the old one
        for arg in sorted(required):
            assert "required" in program.arg_type_text(skill, arg), (skill, arg)
    assert program.declared_args("teleport") == (frozenset(), frozenset())


# ------------------------------------------------------------ 2. the gate: accept ----
def test_a_program_is_an_acquired_skill_spec_with_the_described_fields():
    spec = _grasp_program()
    assert isinstance(spec, SkillSpec)
    assert spec.level == "acquired" and spec.enabled and spec.validation_report_id is None
    assert program.check(spec) == []
    for field in ("name", "description", "parameters", "preconditions", "procedure",
                  "expected_effects", "termination_conditions", "recovery_hints",
                  "applicability", "provenance"):
        assert field in SkillSpec.model_fields, field          # §5.5's ten, `provenance` inherited
    assert spec.parameter_names() == ["object", "index"]


def test_a_bound_program_is_ordinary_calls_and_an_ordinary_plan():
    spec = _grasp_program()
    params = {"object": "obj_green_3", "index": 1}
    calls = program.bind_calls(spec, "prog_1", params)
    assert all(isinstance(c, SkillCall) for c in calls)
    assert [c.skill for c in calls] == ["observe", "safe_retreat", "pick"]
    assert all(c.skill in program.PRIMITIVES for c in calls)
    assert all(c.plan_id == "prog_1" for c in calls)
    assert calls[-1].args == {"object_id": "obj_green_3", "grasp_candidate_index": "1"}
    assert _shape(program.bind_calls(spec, "prog_1", params)) == _shape(calls)

    plan = program.as_plan(spec, "prog_1", params, based_on_state_version=7, goal_ref="g_x")
    assert all(isinstance(s, PlanStep) for s in plan.steps)
    assert [(s.id, s.skill, s.args) for s in plan.steps] == [
        (c.step_id, c.skill, dict(c.args)) for c in calls]
    assert plan.validate_schema() == []
    assert plan.based_on_state_version == 7 and plan.goal_ref == "g_x"


def test_the_representation_gate_does_not_secretly_become_a_planner():
    """`place` before `pick` is representable and not legal — and only one gate may say so.

    `check` answers "is this a program?"; `PlanValidator` answers "can this run against this
    world?". If the first started answering the second there would be two planners with different
    rules, and §5.7 files that question under replanning, in the model.
    """
    spec = _grasp_program(name="place_first",
                          parameters=[program.param("object"), program.param("target")],
                          procedure=[program.step("place", {"object_id": "{object}",
                                                            "target_id": "{target}"})],
                          expected_effects=["placed:{object}:{target}"])
    assert program.check(spec) == []
    world = WorldState(state_version=0, sim_time=0, wall_time=0, entities=[], targets=[])
    plan = program.as_plan(spec, "prog_1", {"object": "obj_blue_1", "target": "tray_left"},
                           based_on_state_version=0, goal_ref="g")
    ok, errs = PlanValidator(world).validate(plan)
    assert not ok
    assert any("hold state" in e or "before it is held" in e for e in errs), errs


# ------------------------------------------------------------- 2. the gate: refuse ----
@pytest.mark.parametrize("skill", ["teleport", "alfred_open", "retract_and_peek", ""])
def test_a_step_naming_anything_the_executor_does_not_implement_is_refused(skill):
    with pytest.raises(program.ProgramError) as exc:
        _one_step(skill, {"object_id": "a"})
    assert any("not in the frozen catalogue" in r for r in exc.value.reasons)
    assert repr(skill) in " ".join(exc.value.reasons)


def test_an_unbound_placeholder_is_refused_by_two_checks_that_could_each_catch_it():
    with pytest.raises(program.ProgramError) as exc:
        _one_step("pick", {"object_id": "{nope}"})
    reasons = exc.value.reasons
    assert any("names no declared parameter" in r for r in reasons)
    assert any(r.startswith("unbound placeholder") for r in reasons)


def test_a_step_missing_a_required_argument_is_refused():
    with pytest.raises(program.ProgramError) as exc:
        _one_step("pick", {})
    assert any("pick requires 'object_id'" in r for r in exc.value.reasons)


def test_an_argument_the_primitive_never_reads_is_refused():
    with pytest.raises(program.ProgramError) as exc:
        _grasp_program(parameters=[program.param("object")],
                       procedure=[program.step("pick", {"object_id": "{object}",
                                                        "zone": "tray_left"})])
    assert any("not 'zone'" in r for r in exc.value.reasons)


@pytest.mark.parametrize("index,parameter_type,expectation", [
    ("left", "number", "is not one and not a"),
    ("{object}", "entity", "must bind a 'number' parameter"),
])
def test_an_integer_argument_cannot_be_filled_with_a_non_integer(index, parameter_type,
                                                                 expectation):
    with pytest.raises(program.ProgramError) as exc:
        _grasp_program(
            parameters=[program.param("object"),
                        program.param("index", parameter_type, example=index)],
            procedure=[program.step("pick", {"object_id": "{object}",
                                             "grasp_candidate_index": index})])
    assert any(expectation in r for r in exc.value.reasons), exc.value.reasons


@pytest.mark.parametrize("on_failure", ["try_next_candidate", "re_observe", "ask_model"])
def test_a_step_that_hands_control_to_the_decision_layer_is_refused(on_failure):
    with pytest.raises(program.ProgramError) as exc:
        _one_step("pick", {"object_id": "a"}, on_failure=on_failure)
    assert any("not implemented by SkillExecutor" in r for r in exc.value.reasons)
    assert on_failure not in program.ON_FAILURE_EXECUTABLE


def test_a_conditional_step_is_refused_because_nothing_evaluates_the_condition():
    spec = _grasp_program()
    conditional = ProcedureStep(intent=spec.procedure[-1].intent, on_failure="abort",
                                when="the hold state is known", expect="grasp confirmed")
    hollow = spec.model_copy(update={"procedure": [spec.procedure[0], conditional]})
    reasons = program.check(hollow)
    assert reasons and program.check(hollow) == reasons
    assert any("when/expect" in r for r in reasons)


@pytest.mark.parametrize("field", ["preconditions", "expected_effects",
                                   "termination_conditions", "applicability"])
def test_a_field_that_makes_a_program_checkable_may_not_be_empty(field):
    with pytest.raises(program.ProgramError) as exc:
        _grasp_program(**{field: []})
    assert any(f"{field} is empty" in r for r in exc.value.reasons)


def test_a_blank_name_is_refused_because_it_is_a_join_key():
    with pytest.raises(program.ProgramError) as exc:
        _grasp_program(name="   ")
    assert any("name is empty" in r for r in exc.value.reasons)


def test_a_program_may_neither_shadow_nor_claim_to_be_a_primitive():
    with pytest.raises(program.ProgramError) as exc:
        _one_step("pick", {"object_id": "a"}, name="pick")
    assert any("shadows a primitive" in r for r in exc.value.reasons)

    shipped = SkillSpec(name="grasp_after_clear_look", level="primitive",
                        preconditions=["p"], expected_effects=["e"],
                        termination_conditions=["t"], applicability=["a"],
                        procedure=[program.step("pick", {"object_id": "a"})])
    assert any("level 'primitive' is shipped" in r for r in program.check(shipped))


def test_a_procedure_may_not_be_empty_or_hollow():
    with pytest.raises(program.ProgramError) as exc:
        _grasp_program(procedure=[])
    assert any("procedure is empty" in r for r in exc.value.reasons)

    spec = _grasp_program()
    hollow = spec.model_copy(update={"procedure": [ProcedureStep(), spec.procedure[0]]})
    assert any("no intent" in r for r in program.check(hollow))


def test_identifiers_are_refused_before_they_become_ambiguous():
    with pytest.raises(program.ProgramError) as exc:
        _grasp_program(parameters=[program.param("object"), program.param("object")])
    assert any("duplicate parameter names" in r for r in exc.value.reasons)

    with pytest.raises(program.ProgramError) as exc:
        _grasp_program(procedure=[program.step("observe", {}, step_id="s"),
                                  program.step("pick", {"object_id": "{object}"}, step_id="s")])
    assert any("duplicate step_id" in r for r in exc.value.reasons)


def test_a_non_positive_timeout_is_refused_where_the_intent_lives():
    with pytest.raises(program.ProgramError) as exc:
        _one_step("pick", {"object_id": "a"}, timeout_s=0)
    assert any("timeout_s must be positive" in r for r in exc.value.reasons)


def test_program_raises_exactly_what_check_returns_and_check_is_pure():
    procedure = [program.step("teleport", {"object_id": "a"}), program.step("pick", {})]
    spec = SkillSpec(name="half_broken", description="d", level="acquired",
                     procedure=procedure)
    first = program.check(spec)
    assert first and program.check(spec) == first        # pure: no accumulation, no order effect
    with pytest.raises(program.ProgramError) as exc:
        program.program("half_broken", "d", [], procedure)
    assert len(exc.value.reasons) >= 4     # both steps *and* all four empty description fields,
    assert json.dumps(exc.value.reasons)   # reported together rather than one at a time


# ------------------------------------------------------------------ 3. binding ----
def test_only_the_parameters_the_procedure_interpolates_must_be_supplied():
    spec = _grasp_program(parameters=[program.param("object"),
                                      program.param("index", "number"),
                                      program.param("unused_note", "text", required=False)])
    assert program.used_parameters(spec) == {"object", "index"}
    calls = program.bind_calls(spec, "p", {"object": "obj_blue_4", "index": 0})
    assert len(calls) == 3


def test_an_unsupplied_interpolated_parameter_is_a_program_error_not_a_keyerror():
    """The *type* is the assertion: a `KeyError` escaping into a sandbox is logged as an execution
    failure, which is the exact confusion this module exists to prevent."""
    spec = _grasp_program()
    with pytest.raises(program.ProgramError) as exc:
        program.bind_calls(spec, "p", {"object": "obj_blue_4"})
    assert "parameters the procedure interpolates" in " ".join(exc.value.reasons)
    assert not isinstance(exc.value, KeyError)


def test_a_bound_call_whose_integer_argument_will_not_parse_is_refused():
    spec = _grasp_program(parameters=[program.param("object"),
                                      program.param("index", "number", required=False)])
    with pytest.raises(program.ProgramError) as exc:
        program.bind_calls(spec, "p", {"object": "obj_blue_4", "index": "second"})
    assert any("bound to a non-integer" in r for r in exc.value.reasons)


def test_a_retry_once_step_adds_exactly_one_call_and_no_program_can_express_a_loop():
    spec = _grasp_program()
    assert len(spec.procedure) == 3
    assert program.max_calls(spec) == 4                      # three steps + one retry
    assert program.max_calls(_grasp_program(
        procedure=[s.model_copy(update={"on_failure": "abort"}) for s in spec.procedure])) == 3
    # finite procedure x at most one retry per step: the expansion is bounded, so a program cannot
    # spend a round budget in a way `max_calls` does not already show
    assert program.max_calls(spec) <= 2 * len(spec.procedure)


# -------------------------------------------------------------------- 4. view ----
def test_render_states_the_program_within_a_budget_and_says_what_it_dropped():
    spec = _grasp_program()
    text = program.render(spec)
    assert "grasp_after_clear_look" in text
    assert "pick(grasp_candidate_index={index}" in text
    assert "stops when:" in text                 # termination is shown: it is the §5.6 question
    assert len(text) <= program.RENDER_BUDGET_CHARS

    long_spec = _grasp_program(procedure=[program.step("observe", {}) for _ in range(30)])
    rendered = program.render(long_spec)
    assert f"{program.STEP_LINE_CAP}. observe" in rendered
    assert "not shown" in rendered and len(rendered) <= program.RENDER_BUDGET_CHARS


def test_reading_the_programs_did_not_widen_the_action_space():
    assert len(SkillRegistry.CATALOGUE) == 4
    # compared as sets: the two tables were written in different orders, and an order assertion
    # here would fail on a cosmetic edit to a file this module does not own
    assert set(REQUIRED_ARGS) == set(SkillRegistry.CATALOGUE)
    assert set(OPTIONAL_ARGS) == set(SkillRegistry.CATALOGUE)
    assert program.PRIMITIVES == tuple(sorted(SkillRegistry.CATALOGUE))
