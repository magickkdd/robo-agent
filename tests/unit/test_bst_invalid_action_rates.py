"""The three invalid-action rates must count the events they claim to count.

Two defects this pins down, both found in the 268-episode batch:

* a proposal `render` refused writes no `skill_call` event, so a numerator built as
  "skill calls minus the executed ones" is 0 no matter how many refusals happened — the
  batch published `syntax_invalid_per_execute_proposal: 0/611` while ten episodes carried
  a binder refusal;
* such a round is in `decisions_by_action` (its payload survived the schema, so a
  `decision` event exists) *and* in `rejected_decisions`, which is why the round identity
  failed for exactly ten episodes by exactly one round each.

The published `episodes_where_identity_fails` list stays: it is the alarm. What is new is
that the aggregation now names the set the gap is made of, so a reader can tell "a round
was billed and never recorded" from "a round was recorded twice".
"""
import json
import os

from embodied_agent.benchmark.aggregate import recompute_episode

REFUSAL = ("skill 'alfred_go' does not map to one environment command: "
           "args['target_id']='garbagecan' is not an instance reference of the form "
           "'<kind> <number>' as the observation wrote it")


def _ev(type_, payload):
    return json.dumps({"type": type_, "payload": payload})


def _episode(tmp_path, binder_refused: bool):
    d = os.path.join(str(tmp_path), "episodes", "slot000_x")
    os.makedirs(d)
    ctx = lambda r, cid: _ev("decision_context", {"round_index": r, "context_id": cid})
    events = [
        _ev("episode_start", {"episode_id": "slot000_x", "task_id": "pick-1"}),
        ctx(1, "ctx_1"),
        _ev("decision", {"decision_id": "d_1", "context_id": "ctx_1", "action": "execute"}),
        _ev("skill_call", {"context_id": "ctx_1",
                           "call": {"call_id": "c_1", "skill": "alfred_go",
                                    "args": {"target_id": "desk 1"}}}),
        _ev("execution_feedback", {"feedback": {
            "feedback_id": "f_1", "decision_id": "d_1", "context_id": "ctx_1",
            "status": "completed", "executed": True, "environment_text": "Nothing happens."}}),
    ]
    if binder_refused:
        events.append(ctx(2, "ctx_2"))
        events.append(_ev("decision", {"decision_id": "d_2", "context_id": "ctx_2",
                                       "action": "execute"}))
        events.append(_ev("execution_feedback", {"feedback": {
            "feedback_id": "f_2", "decision_id": "d_2", "context_id": "ctx_2",
            "status": "rejected", "executed": False, "rejection_reasons": [REFUSAL]}}))
    events += [
        _ev("termination", {"reason": "BUDGET_EXHAUSTED"}),
        _ev("episode_end", {"result": {"decision_rounds": 2 if binder_refused else 1,
                                       "env_actions_used": 1, "http_requests": 2}}),
    ]
    with open(os.path.join(d, "events.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(events) + "\n")
    with open(os.path.join(d, "evaluation.jsonl"), "w", encoding="utf-8") as f:
        f.write(_ev("evaluation", {"episode_id": "slot000_x", "official_won": False,
                                   "env_done": False, "termination_reason": "AGENT_BLOCKED",
                                   "false_finish": False, "task_type": "pick_and_place",
                                   "repeat": 0, "slot_index": 0, "env_steps": 1}) + "\n")
    return d


def test_a_binder_refusal_is_counted_even_though_it_never_became_a_skill_call(tmp_path):
    rec = recompute_episode(_episode(tmp_path, binder_refused=True))
    assert rec["binder_refusals"] == 1
    assert rec["rounds_counted_twice"] == 1
    # the proposal never reached the executor, so the executed-command count must not grow
    assert rec["commands_issued"] == 1


def test_an_ordinary_rejected_payload_is_not_a_binder_refusal(tmp_path):
    """A payload the *schema* refused writes no `decision` event either, but it is not a
    round that was counted twice — the distinction is what the identity note rests on."""
    d = _episode(tmp_path, binder_refused=False)
    with open(os.path.join(d, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write("\n" + _ev("execution_feedback", {"feedback": {
            "feedback_id": "f_9", "context_id": "ctx_1", "status": "rejected",
            "executed": False, "rejection_reasons": [
                "subgoal: Extra inputs are not permitted"]}}) + "\n")
    rec = recompute_episode(d)
    assert rec["binder_refusals"] == 0
    assert rec["rounds_counted_twice"] == 0
    assert rec["rejected_decisions"] == 1
