"""Contracts on the production path: schema accept/reject, three-value verdicts,
and the SPEC 6.2 budget table the ledger actually enforces.

These are deliberately not restatements of the field list. Each one names a claim
the experiment rests on — that the two judges share a tolerance, that a rejection
is not a success, that a budget is charged before the action — and would fail if
the code quietly changed its meaning.
"""
import inspect

import pytest
from pydantic import ValidationError

from embodied_agent.core.contracts import (
    Budgets,
    Decision,
    EvalSpec,
    Plan,
    PlanStep,
    PredicateReport,
    PredicateVerdict,
    Source,
    VerificationReport,
    VerifyConfig,
    WorldState,
)


def _decision_payload(candidate=None, args_candidate=None):
    args = {"object_id": "obj_red_1", "target_id": "tray_left"}
    if args_candidate is not None:
        args["candidate_id"] = args_candidate
    return {"context_id": "ctx_1", "based_on_state_version": 3, "goal_ref": "g_1",
            "action": "execute",
            "execute": {"skill": "place", "args": args, "candidate_id": candidate}}


def test_a_slot_named_only_in_the_arguments_is_recorded_as_named():
    """The canonical reference and the argument are one claim, not two chances to
    lose it (SPEC 5.3: 模型引用候选 id).

    `place` documents a `candidate_id` argument while `execute.candidate_id` is the
    field the skill call, the feedback and the state-pair labels read, so a model that
    names its geometry in the argument bag was honoured by the executor and recorded as
    naming nothing — including for the labels that *reject* a named slot. Two real
    online rounds did exactly that.
    """
    lifted = Decision.model_validate(_decision_payload(None, "cand-a"))
    assert lifted.execute.candidate_id == "cand-a"
    assert lifted.skill_call().candidate_id == "cand-a", \
        "the call that reaches the executor must carry the same reference it acted on"
    assert Decision.model_validate(_decision_payload("cand-a", "cand-a")).execute.candidate_id \
        == "cand-a"
    # naming two geometries at once is a claim only the model can resolve, so it is
    # refused rather than chosen between by the runtime
    with pytest.raises(ValidationError, match="name different candidates"):
        Decision.model_validate(_decision_payload("cand-a", "cand-b"))
    # and the lift must not manufacture a reference the model never gave
    assert Decision.model_validate(_decision_payload(None)).execute.candidate_id is None
    assert "candidate_id" not in Decision.model_validate(
        _decision_payload(None)).skill_call().args


def test_plan_schema_rejects_place_before_pick():
    p = Plan(plan_id="p", based_on_state_version=0, goal_ref="g", steps=[
        PlanStep(id="s1", skill="place", args={"object_id": "a", "target_id": "tray_left"})])
    assert p.validate_schema() == []
    from embodied_agent.core.planner import PlanValidator

    w = WorldState(state_version=0, sim_time=0, wall_time=0, entities=[], targets=[])
    ok, errs = PlanValidator(w).validate(p)
    assert not ok and errs  # plan before any pick / unknown object / empty first action


def test_verification_report_empty_is_unknown_not_success():
    r = VerificationReport(reports=[], source=Source.privileged)
    assert r.overall == PredicateVerdict.unknown


def test_verification_report_unknown_propagates():
    r = VerificationReport(reports=[
        PredicateReport(predicate_id="a", description="", value=PredicateVerdict.true,
                        source=Source.privileged),
        PredicateReport(predicate_id="b", description="", value=PredicateVerdict.unknown,
                        source=Source.privileged),
    ], source=Source.privileged)
    assert r.overall == PredicateVerdict.unknown


def test_public_and_hidden_tolerance_parameters_agree():
    """The two judges may not hold different numbers.

    SPEC 7 keeps the runtime's predicate parameters public and the scoring truth
    hidden, but both read the same physical claim off the *same* terminal
    snapshot, so drift between the two defaults would let the independent score
    contradict the execution feedback on a tolerance nobody chose."""
    public = VerifyConfig().model_dump()
    hidden = EvalSpec(eval_id="e", assignments=[]).model_dump()
    for key in ("tolerance_version", "speed_tol_mps", "ang_speed_tol_rps",
                "support_height_tol_m", "footprint_margin_m", "lift_gain_min_m"):
        assert public[key] == hidden[key], f"{key} differs between the two judges"
    # the end-of-episode settle is a shared procedure, not scoring truth
    assert "settle_time_s" in public and "settle_time_s" not in hidden


def test_budget_defaults_match_spec_6_2():
    """The six rows of the SPEC 6.2 table, in the SPEC's own units.

    24 decision rounds (finish, observe and rejected decisions included), 32 HTTP
    requests, 30 physical skill calls, 30 s per skill, 900 s wall clock, 3
    identical invalid attempts."""
    b = Budgets()
    assert (b.max_decision_rounds, b.max_http_requests, b.max_skill_calls) == (24, 32, 30)
    assert (b.per_skill_sim_timeout_s, b.wall_clock_s) == (30.0, 900.0)
    assert b.max_identical_invalid_attempts == 3


def test_decision_loop_does_not_charge_replans():
    """SPEC 6.2: 决策轮数不再复用 max_global_replans.

    The field survives only on the legacy full-plan reference path; if the ledger
    read it, an episode would stop for a reason its own published budget never
    claimed to contain."""
    from embodied_agent.core.runtime import BudgetLedger

    assert "max_global_replans" not in inspect.getsource(BudgetLedger)
    payload = BudgetLedger(Budgets()).remaining_payload()
    assert set(payload) >= {"decision_rounds_used", "http_requests_used", "skill_calls_used",
                            "per_skill_sim_timeout_s", "wall_clock_remaining_s"}
    assert not any("replan" in k for k in payload), payload


def test_budget_ledger_counts_are_not_interchangeable():
    """A skill call is not a decision round is not an HTTP request (SPEC 6.2).

    `can_*` answers "may I start one more", so N permits exactly N uses."""
    from embodied_agent.core.runtime import BudgetLedger

    led = BudgetLedger(Budgets(max_decision_rounds=2, max_http_requests=3, max_skill_calls=1))
    assert led.can_decide() and led.can_call_skill()
    led.charge_skill("pick", 1.25)
    assert not led.can_call_skill(), "one skill call was the whole budget"
    assert led.sim_seconds_by_skill == {"pick": 1.25}
    led.charge_decision()
    led.charge_requests(3)
    assert led.decision_rounds == 1 and led.http_requests == 3
    assert led.can_decide(), "one of two rounds spent"
    led.charge_decision()
    assert not led.can_decide()
    # a negative charge can never buy back budget
    led.charge_requests(-5)
    assert led.http_requests == 3
