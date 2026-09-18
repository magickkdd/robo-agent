"""Unit tests: schema accept/reject, three-value verification, budget logic."""
import pytest
from embodied_agent.core.contracts import (
    EvalSpec, Plan, PlanStep, VerificationReport, PredicateReport,
    PredicateVerdict, Source, Budgets,
)


def test_plan_schema_rejects_place_before_pick():
    p = Plan(plan_id="p", based_on_state_version=0, goal_ref="g", steps=[
        PlanStep(id="s1", skill="place", args={"object_id": "a", "target_id": "tray_left"})])
    assert p.validate_schema() == []
    from embodied_agent.core.planner import PlanValidator
    from embodied_agent.core.contracts import WorldState as WS
    w = WS(state_version=0, sim_time=0, wall_time=0, entities=[], targets=[])
    ok, errs = PlanValidator(w).validate(p)
    assert not ok and errs  # plan before any pick / unknown object / empty first action


def test_verification_report_empty_is_unknown_not_success():
    r = VerificationReport(reports=[], source=Source.privileged)
    assert r.overall == PredicateVerdict.unknown


def test_verification_report_unknown_propagates():
    r = VerificationReport(reports=[
        PredicateReport(predicate_id="a", description="", value=PredicateVerdict.true, source=Source.privileged),
        PredicateReport(predicate_id="b", description="", value=PredicateVerdict.unknown, source=Source.privileged),
    ], source=Source.privileged)
    assert r.overall == PredicateVerdict.unknown


def test_eval_spec_frozen_tolerances_present():
    spec = EvalSpec(eval_id="e", assignments=[])
    assert spec.support_height_tol_m == 0.01 and spec.footprint_margin_m == 0.005


def test_budget_defaults_match_spec_7_2():
    b = Budgets()
    assert b.max_skill_calls == 30 and b.per_object_extra_attempts == 2
    assert b.max_global_replans == 3 and b.max_model_requests == 10
    assert b.max_consecutive_no_progress == 3
    assert b.per_skill_sim_timeout_s == 30.0 and b.wall_clock_s == 900.0
