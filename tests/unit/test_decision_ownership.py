"""Who owns a decision (SPEC 5.4, 6.2, 11.2).

The standing rule of this experiment is that the Runtime never chooses a strategy
for the model: not a recovery, not a substitute target, not an extra action. It
executes what it was told, measures what happened, and asks again. What replaces
the old `RecoveryPolicy` is therefore tested from both sides:

* a scripted episode takes **exactly** as many decisions as the script has
  entries — an invented call makes the script raise;
* the identical-invalid rule *ends* the episode where a recovery policy used to
  quietly continue it;
* mode A's one-shot baseline has exactly one permitted reaction to a failure, the
  frozen repeat, and no other.
"""
from __future__ import annotations

import pytest

from embodied_agent.core.contracts import (
    DecisionContext,
    ExecutionFeedback,
    FailureCode,
)
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.planner import OneShotPlanSource, RulePlanner
from embodied_agent.evaluation.run import task_input
from embodied_agent.evaluation.sources import ScriptedSource
from embodied_agent.evaluation.tasks import find_case


def _script(case, repeats: int = 0) -> list:
    """The full pick/place script for a case, in the hidden-truth order — which is
    also a legal order, since the assignment was authored to be one."""
    steps: list = []
    for a in case.eval_spec.assignments:
        steps.append(("execute", ("pick", {"object_id": a.entity_id})))
        if repeats:
            steps += [("execute", ("pick", {"object_id": a.entity_id}))] * repeats
        steps.append(("execute", ("place", {"object_id": a.entity_id,
                                            "target_id": a.target_id})))
    steps.append(("finish", None))
    return steps


# ------------------------------------------------ the loop runs the script -----


def test_the_loop_asks_exactly_once_per_action_and_terminates_when_the_script_does(run_case):
    case = find_case("smoke_clean")
    steps = _script(case)
    source = ScriptedSource(steps, on_exhausted="raise")
    ep = run_case("smoke_clean", mode="B", steps=source)

    assert ep.terminal_status == "success", (ep.failure_type, ep.last_feedback())
    calls = [p["call"] for p in ep.payloads("skill_call")]
    decisions = ep.payloads("decision")
    assert len(calls) == len(steps) - 1 and len(decisions) == len(steps), \
        f"the runtime spent {len(decisions)} decisions on a {len(steps)}-step script"
    # one decision, one call, same arguments: nothing added, nothing edited
    for d, c in zip(decisions, calls):
        cand = (d["execute"] or {}).get("candidate_id")
        assert d["execute"]["skill"] == c["skill"]
        assert c["args"] == {**d["execute"]["args"],
                             **({"candidate_id": cand} if cand else {})}, \
            "the call that ran is not the call that was decided"
        assert c["candidate_id"] == cand


def test_a_decision_the_runtime_cannot_execute_is_bounded_not_repaired(run_case):
    """The failure a recovery policy used to hide: an action that cannot start is
    refused a bounded number of times and then stops the episode.

    Two bounds apply to an unexecutable action and only the tighter one fires:
    `max_semantic_repairs` for a decision the runtime's own validation refuses,
    `max_identical_invalid_attempts` for one that reached the executor (that path
    is `h_repeated_invalid`). Which is which is worth a test, because a runtime
    that repaired the action instead of counting it would show up as extra
    attempts of a *different* action."""
    case = find_case("smoke_clean")
    eid = case.targets[0]
    ep = run_case("smoke_clean", mode="B",
                  steps=ScriptedSource([("execute", ("place", {"object_id": eid,
                                                               "target_id": "tray_left"}))]))
    assert ep.terminal_status == "failed"
    assert ep.failure_type == FailureCode.INVALID_DECISION.value
    assert ep.result.decision_rounds == case.budgets.max_semantic_repairs + 1
    assert not ep.payloads("skill_call"), \
        "a decision the runtime refused still reached the executor: the refusal was a formality"
    reasons = " ".join(" ".join(fb["rejection_reasons"]) for fb in ep.feedbacks())
    assert eid in reasons and "held" in reasons.lower(), reasons


def test_a_failure_is_returned_to_the_source_untouched(run_case):
    """SPEC 10.2 on the ownership side: the environment made the first grasp miss,
    and the *retry* is the script's own second entry — the same request, because
    nothing else was allowed to issue one."""
    case = find_case("dev_c3")
    steps = _script(case, repeats=1)          # the first pick is attempted twice
    source = ScriptedSource(steps, on_exhausted="raise")
    ep = run_case("dev_c3", mode="B", steps=source)

    calls = [p["call"] for p in ep.payloads("skill_call")]
    assert calls[0]["args"] == calls[1]["args"], "the retry was not the same request"
    statuses = [fb["status"] for fb in ep.feedbacks()]
    assert statuses[0] == "failed" and statuses[1] == "completed", statuses
    assert ep.failure_type is None or ep.terminal_status == "success", ep.failure_type
    assert len(calls) == len(steps) - 1, \
        "the loop added a call of its own after the grasp recovered"


# ------------------------------------------- mode A: one frozen reaction -------


def _plan_and_context(case_id: str, world_for):
    case = find_case(case_id)
    scene, world = world_for(case_id)
    task = task_input(case)
    goal = interpret(task, world)
    plan = RulePlanner(case.verify).plan(goal, world, plan_id="p_unit")
    ctx = DecisionContext(episode_id="unit", state_version=world.state_version, task=task,
                          goal=goal, world=world, observation_ref=world.observation_ref)
    return case, plan, ctx


def _fb(status: str, skill: str = "pick", entity: str = "x") -> ExecutionFeedback:
    return ExecutionFeedback(skill=skill, entity_id=entity, status=status, executed=True)


def test_the_one_shot_baseline_may_only_repeat_what_it_already_tried(world_for):
    case, plan, ctx = _plan_and_context("smoke_clean", world_for)
    assert [s.skill for s in plan.steps] == ["pick", "place"] * case.n_objects
    source = OneShotPlanSource(plan, per_action_extra_repeats=2)
    a, b = plan.steps[0].args["object_id"], plan.steps[2].args["object_id"]

    # each entry is the feedback the *previous* attempt came back with
    outcomes = [None, "failed", "failed", "failed", "completed"]
    emitted = []
    for status in outcomes:
        step_ctx = ctx if status is None else ctx.model_copy(
            update={"last_feedback": _fb(status, entity=a)})
        d = source.decide(step_ctx)
        assert d.action == "execute"
        emitted.append((d.execute.skill, dict(d.execute.args)))

    assert emitted[:3] == [("pick", {"object_id": a})] * 3, \
        f"one attempt plus two repeats is the whole allowance: {emitted}"
    assert emitted[3] == ("place", {"object_id": a, "target_id": plan.steps[1].args["target_id"]}), \
        f"after the repeats ran out the plan should move on, not substitute: {emitted}"
    assert emitted[4][0] == "pick" and emitted[4][1]["object_id"] == b, emitted[4]
    # the baseline never reaches outside its own plan
    planned = {(s.skill, tuple(sorted(s.args.items()))) for s in plan.steps}
    assert {(s, tuple(sorted(a2.items()))) for s, a2 in emitted} <= planned


def test_a_refused_step_is_not_argued_with(world_for):
    _, plan, ctx = _plan_and_context("smoke_clean", world_for)
    source = OneShotPlanSource(plan, per_action_extra_repeats=2)
    source.decide(ctx)
    after_refusal = source.decide(ctx.model_copy(update={"last_feedback": _fb("rejected")}))
    assert after_refusal.execute.skill == "place", \
        "a guard the executor refused was retried instead of passed over"


def test_an_exhausted_plan_asks_to_finish_and_the_verifier_decides(run_case):
    """SPEC 11.2: mode A is the one-shot baseline, so on a case whose plan is
    undone by the environment it must end by *asking*, and the shared finish
    protocol — not the plan — is what rejects it. This is the measured A-vs-B/C
    gap the report attributes, here under test."""
    a = run_case("dev_c2", mode="A")
    b = run_case("dev_c2", mode="B")
    assert b.terminal_status == "success"
    assert a.terminal_status == "failed"
    assert a.failure_type == FailureCode.FINISH_REJECTED.value, (a.failure_type, a.probe)
    assert a.result.decision_rounds <= a.case.budgets.max_decision_rounds
    assert a.score["objects_completed"] < a.score["objects_total"], \
        "mode A finished clean: the state-change mechanism stopped being applied"
