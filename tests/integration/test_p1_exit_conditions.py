"""SPEC 9 P1's exit conditions, written as tests that run against the real loop.

The three clauses are 每个动作都能追溯到上下文和决策；最新反馈进入下一轮；没有 EvalSpec
回流或隐藏策略替代. This file does not re-prove what the suites named below already
carry — an audit that duplicates its neighbours reads as coverage while testing
nothing new:

* information isolation (no `EvalSpec` in the decision chain) is
  `tests/unit/test_information_isolation.py`, structurally (an AST scan of every
  runtime module) and empirically (the graded pairing never appears as a pair a
  policy can read);
* the same round seen by mode C, with only its feedback channel thinned, is
  `tests/unit/test_state_fork.py::test_the_ablated_view_of_one_round_changes_only_the_feedback_channel`;
* the two planning modes sharing one goal parse and one disturbance is
  `test_the_three_arms_share_one_goal_parse_and_each_pays_for_it` and
  `test_the_disturbance_is_the_same_one_whatever_order_the_modes_choose`;
* the branch responses P1 asks for (成功/失败/unknown/无效动作/finish 被拒/模型错误/预算耗尽)
  are the `h_*` cases of `tests/integration/test_protocol_harness.py`.

What is added here is the *whole-episode* version of the three clauses: the
traceability chain read out of the event stream in log order, the feedback record
delivered to the next round across every branch of one episode, and "the loop ran
what was asked" checked on every action of both modes rather than on one call.
Episodes are driven by `tests/conftest.py::run_case` — real scene, real `Runtime`,
real skills, real `EpisodeStore` — with only the decision source substituted, which
is what 用 fixture 响应走真实 Runtime means.
"""
from __future__ import annotations

from embodied_agent.core.contracts import Decision, DecisionExecute
from embodied_agent.evaluation.sources import RulePolicySource, ScriptedSource

CASE = "smoke_clean"

# A rejection, a model error, and a premature finish, then the deterministic policy
# finishes the task. The three noise rounds are the point: each writes a feedback
# record while executing nothing, so the only way the loop can carry on sensibly is
# by delivering that record to the next round.
NOISE = [
    ("execute", ("place", {"object_id": "obj_blue_2", "target_id": "tray_right"})),
    RuntimeError("transport died between two rounds"),
    ("finish", None),
]


def ep_verify(case_id: str):
    from embodied_agent.evaluation.tasks import find_case

    return find_case(case_id).verify


class ScriptThenRule:
    """Play a script, then hand the episode to the deterministic control policy.

    `ScriptedSource` repeats its last step when the list runs out, which would make
    an episode of twelve scripted rounds a test of the script's own bookkeeping.
    Handing over to `RulePolicySource` keeps the rest of the episode a real policy's
    doing, so the claims below hold over rounds nobody wrote by hand.
    """

    provider = "script_then_rule"
    prompt_tokens = 0
    completion_tokens = 0

    def __init__(self, steps: list, verify):
        self.script = ScriptedSource(steps)
        self.rule = RulePolicySource(verify)

    @property
    def http_requests(self) -> int:
        return self.script.http_requests

    def decide(self, ctx):
        if self.script.calls < len(self.script.steps):
            return self.script.decide(ctx)
        return self.rule.decide(ctx)


# --------------------------------------------------- 1. traceable to context --


def test_every_action_traces_back_to_the_context_and_decision_that_asked_for_it(run_case,
                                                                                recorder):
    """每个动作都能追溯到上下文和决策, read from the log rather than from memory.

    Three links, each checked in sequence order so the claim is causal and not set
    membership: every skill call names a decision logged before it, every decision
    names a context logged before that, and the context objects the policy was
    handed are the ones the log recorded. `test_the_event_log_is_one_ordered_stream_that_traces_every_claim`
    walks the same chain across a three-arm run; this is the single-episode version
    with the contexts themselves retained.
    """
    ep = run_case(CASE, source_factory=recorder().wrap)
    contexts = {p["context_id"]: p for p in ep.payloads("decision_context")}
    decisions = {p["decision_id"]: p for p in ep.payloads("decision")}
    assert ep.payloads("skill_call"), "a rule episode that executed nothing proves nothing"

    seen_contexts: list[str] = []
    seen_decisions: list[str] = []
    for e in ep.events:
        p = e["payload"]
        if e["type"] == "decision_context":
            seen_contexts.append(p["context_id"])
        elif e["type"] == "decision":
            seen_decisions.append(p["decision_id"])
            assert p["context_id"] in seen_contexts, \
                f"decision {p['decision_id']} answers a context logged after it"
            assert p["context_id"] in contexts, "a decision names a context never recorded"
        elif e["type"] == "skill_call":
            assert p["decision_id"] in seen_decisions, \
                f"call {p['call']['call_id']} executes a decision nobody has asked for"
            assert p["context_id"] == decisions[p["decision_id"]]["context_id"], \
                "the executed action and the decision behind it name different contexts"
        elif e["type"] == "execution_feedback":
            fb = p["feedback"]
            assert fb["context_id"] in seen_contexts, "feedback answers a future round"
            if fb["decision_id"]:
                assert fb["decision_id"] in seen_decisions
    assert len(seen_contexts) == len(set(seen_contexts)), "two rounds shared one context id"
    assert [c.context_id for c in ep.contexts] == seen_contexts, \
        "the contexts the policy was handed are not the contexts the log recorded"


def test_a_decision_about_another_rounds_context_is_refused_and_never_executed(run_case):
    """The other half of traceability: a decision cannot invent its context.

    `_validate_decision` refuses a mismatched `context_id`, so a crossed or stale
    answer cannot be charged to a round that never produced it — and the refusal is
    feedback (SPEC 6.1), not a silent correction.
    """
    asked: list = []

    class _Crossed:
        provider = "fixture"
        http_requests = 0
        prompt_tokens = 0
        completion_tokens = 0

        def decide(self, ctx):
            asked.append(ctx)
            if len(asked) == 1:
                return Decision(context_id="ctx_somewhere_else",
                                based_on_state_version=ctx.state_version,
                                goal_ref=ctx.goal.goal_id, action="execute",
                                execute=DecisionExecute(skill="pick",
                                                        args={"object_id": "obj_green_1"}))
            return ScriptedSource([("finish", None)]).decide(ctx)

    ep = run_case(CASE, source=_Crossed(), source_kind="crossed-context")
    rejects = [fb for fb in ep.feedbacks() if fb["status"] == "rejected"]
    assert rejects, "a decision naming a foreign context was accepted"
    reason = " ".join(rejects[0]["rejection_reasons"])
    assert "ctx_somewhere_else" in reason and asked[0].context_id in reason, reason
    assert rejects[0]["executed"] is False and rejects[0]["decision_id"]
    assert not ep.payloads("skill_call"), "the loop executed the crossed decision's skill"


# ------------------------------------------------------ 2. feedback arrives ---


def test_the_newest_feedback_is_what_the_next_round_is_shown(run_case, recorder):
    """最新反馈进入下一轮, across the branches that produce no skill result.

    Asserted round by round: one feedback per round, each naming the round that
    produced it, and every context after the first shown that record as its
    `last_feedback` — same origin context, same status, same failure code, same
    executed flag. A dropped or doubled record breaks the pairing; a next round
    shown an older one fails on the status.
    """
    ep = run_case(CASE, source=ScriptThenRule(NOISE, ep_verify(CASE)),
                  source_factory=recorder().wrap)
    feedbacks = ep.feedbacks()
    assert [f["status"] for f in feedbacks[:3]] == ["rejected", "model_error", "finish_rejected"], \
        [f["status"] for f in feedbacks[:3]]
    assert ep.result.terminal_status == "success", \
        f"the episode did not recover and finish: {ep.failure_type}"
    contexts = ep.contexts
    # one feedback per round, each naming the round that produced it: the terminal
    # `finish_accepted` is itself a feedback, so the two lists run side by side
    assert len(feedbacks) == len(contexts), \
        "a round produced no feedback, or one feedback was billed to two rounds"
    for i, produced in enumerate(feedbacks):
        assert produced["context_id"] == contexts[i].context_id, \
            f"feedback {i} names {produced['context_id']}, not round {i}"
    for i in range(len(contexts) - 1):
        shown, produced = contexts[i + 1].last_feedback, feedbacks[i]
        assert shown is not None, f"round {i + 1} was handed no last_feedback"
        assert (shown.context_id, shown.status, shown.failure_code) == \
            (contexts[i].context_id, produced["status"], produced["failure_code"]), \
            f"round {i + 1} sees {shown.status} from {shown.context_id}, not round {i}'s " \
            f"{produced['status']} on {contexts[i].context_id}"
        assert shown.model_dump(mode="json")["executed"] == produced["executed"]
    assert "gripper holds" in " ".join(feedbacks[0]["rejection_reasons"]), \
        "the rejection the next round reads says nothing about why it was refused"


def test_a_round_that_changed_the_world_reports_the_measurement_it_changed_it_with(
        run_case, recorder):
    """The other half of 最新反馈进入下一轮: an executed skill's feedback must be a new
    reading, not a restatement of the request.

    SPEC 5.1. If a completed pick could be reported against the pre-action snapshot,
    the next round's `last_feedback` would read as a promise rather than as evidence,
    and a policy acting on it would be re-planning from its own intention.
    `test_the_feedback_ledger_records_what_actually_happened` checks the same refs
    against the physics from the evaluator's side.
    """
    ep = run_case(CASE, source=ScriptThenRule(NOISE, ep_verify(CASE)),
                  source_factory=recorder().wrap)
    executed = [fb for fb in ep.feedbacks() if fb["executed"]]
    assert executed, "no skill ran, so nothing here was measured"
    for fb in executed:
        assert fb["post_observation_ref"] != fb["pre_observation_ref"], fb["skill"]
        assert fb["post_state_version"] > fb["pre_state_version"], fb["skill"]
        assert fb["new_measurement"] is True
        assert fb["verification"] is not None, \
            f"{fb['skill']} was reported to the next round with no independent check"
    # the record the next round reads is the one that was written, not a summary of it
    for fb in executed:
        round_index = next(j for j, c in enumerate(ep.contexts)
                           if c.context_id == fb["context_id"])
        assert round_index + 1 < len(ep.contexts), \
            "the last thing that moved the world was an action with no round after it"
        following = ep.contexts[round_index + 1].last_feedback.model_dump(mode="json")
        assert following["post_observation_ref"] == fb["post_observation_ref"]
        assert following["measurements"] == fb["measurements"]


# --------------------------------------------- 3. no substitution in the loop --


def test_the_loop_ran_exactly_what_was_asked_for_in_both_planning_modes(run_case, recorder):
    """没有隐藏策略替代, over every action of a real episode rather than one call.

    Each decision is compared with the call that came out of the loop: same skill,
    same arguments, same named slot. The one permitted difference is *which slot*,
    and only when the decision named none — SPEC 5.5 hands that geometry to a shared
    execution-time rule, and the feedback says so in `candidate_resolution`.
    `test_who_chose_the_slot_is_recorded_either_way` pins that down for a single
    call; reading it across a whole episode is the difference between "a fallback
    exists" and "the fallback never became a substitution".
    """
    for mode in ("B", "C"):
        ep = run_case(CASE, mode=mode, source_factory=recorder().wrap)
        decisions = {p["decision_id"]: p for p in ep.payloads("decision")}
        assert ep.payloads("skill_call"), f"mode {mode} executed nothing"
        for p in ep.payloads("skill_call"):
            asked, call = decisions[p["decision_id"]]["execute"], p["call"]
            assert asked["skill"] == call["skill"], f"mode {mode}: the skill was changed"
            assert dict(asked["args"]) == dict(call["args"]), \
                f"mode {mode}: the arguments were changed"
            if asked.get("candidate_id"):
                assert call.get("candidate_id") == asked["candidate_id"], \
                    f"mode {mode}: a named slot was replaced"
        places = [fb for fb in ep.feedbacks() if fb["skill"] == "place" and fb["executed"]]
        assert places, f"mode {mode} placed nothing, so the slot claim is untested"
        for fb in places:
            if fb.get("candidate_resolution") == "runtime_fallback_rule":
                assert not decisions[fb["decision_id"]]["execute"].get("candidate_id"), \
                    "the runtime chose over a slot the policy named"
        assert ep.runtime.slot_resolution_fallbacks == sum(
            1 for fb in places if fb.get("candidate_resolution") == "runtime_fallback_rule"), \
            f"mode {mode}: who chose the slot is counted inconsistently with how it is recorded"
