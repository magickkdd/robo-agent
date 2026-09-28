"""The online model path, driven twice (SPEC 12.1.4: 在线测试 opt-in，且无静默 rule fallback).

The offline twin is the reason this file can be trusted: a test that only runs
with a live key is a standing invitation to rot — it keeps calling a method that
has since been renamed, and "passing without running" is indistinguishable from
"never having been right". So one `_drive` function holds the production wiring
(`evaluation.run.resolve_goal` + `run_one_episode`, real scene, real runtime, real
evaluator), and it runs on every ordinary pytest invocation against a scripted
transport. `RUN_ONLINE=1` with a key set runs the *same* code against the API.

The two variants assert different things, deliberately. The twin may assert what
the model did, because its answers are known. The online variant asserts only the
protocol — that the decisions came from the model, that nothing substituted them,
and that the artifacts prove it afterwards — because whether a real model finished
the task is a measurement, not an expectation (SPEC 12.2).

The machinery those protocol claims ride on is guarded offline where it belongs:
an injected disturbance reaching the round after it is
`tests/unit/test_state_fork.py` and
`tests/integration/test_episodes.py::test_every_smoke_case_reaches_its_declared_terminal_state`.
What the perturbed online test below adds is only that the same happens with a
real provider in the decision seat, which no scripted transport can show.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from embodied_agent.adapters.deepseek import DeepSeekAdapter, DeepSeekPlanner
from embodied_agent.core.runtime import HISTORY_CAP
from embodied_agent.evaluation import run as evaluation_run
from embodied_agent.evaluation.tasks import find_case

CASE = "smoke_clean"
FAKE_KEY = "sk-" + "0123456789abcdef" * 3
ONLINE = os.environ.get("RUN_ONLINE") == "1" and bool(os.environ.get("DEEPSEEK_API_KEY"))
GOAL = [{"entity": {"entity_id": "obj_green_1"}, "target_id": "tray_left"},
        {"entity": {"entity_id": "obj_blue_2"}, "target_id": "tray_right"},
        {"entity": {"entity_id": "obj_yellow_3"}, "target_id": "tray_left"}]


def _execute(skill, **args):
    return json.dumps({"action": "execute", "execute": {"skill": skill, "args": args}})


def _provider_replies(contents: list[str]) -> list[dict]:
    return [{"choices": [{"message": {"content": r}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 300, "completion_tokens": 40},
             "model": "deepseek-fake-echo", "id": f"chatcmpl-fake-{i}"}
            for i, r in enumerate(contents)]


def _model_does_the_task():
    """A model that reads the task and works through it: parse, pick/place each
    object, then finish on its own conviction that the scene is done."""
    contents = [json.dumps({"assignments": GOAL})]
    for a in GOAL:
        contents.append(_execute("pick", object_id=a["entity"]["entity_id"]))
        contents.append(_execute("place", object_id=a["entity"]["entity_id"],
                                 target_id=a["target_id"]))
    contents.append(json.dumps({"action": "finish"}))
    return _provider_replies(contents)


class _Opener:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def open(self, req, timeout=None):
        self.calls.append({"url": req.full_url, "headers": dict(req.header_items()),
                           "body": json.loads(req.data.decode()), "timeout": timeout})
        if not self.replies:
            raise AssertionError("the loop re-asked after the script ran out: the model was "
                                 "asked more times than its own plan needed")
        payload = json.dumps(self.replies.pop(0)).encode()

        class _Response:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return _Response()


def _drive(planner, root, case_id=CASE):
    """One model-driven episode through the production entry points — no test-local
    runtime, so what passes here is what a batch does."""
    case = find_case(case_id)
    resolution = evaluation_run.resolve_goal(case, 0, planner, root)
    assert resolution.error is None, resolution.error
    summary = evaluation_run.run_one_episode(case, 0, "B", resolution, planner, root,
                                            case.subset, frames=False)
    return case, resolution, summary


def _events_of(summary):
    with open(os.path.join(summary["artifacts"]["episode_dir"], "events.jsonl"),
              encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _assert_nothing_substituted_the_model(summary):
    """Protocol claims, true of any online run whatever its outcome.

    The check is that every decision in the ledger has a provider call behind it:
    a rule standing in for the model would leave a decision with no request, and
    SPEC 12.1.4 forbids exactly that. Nothing here assumes the model succeeded."""
    result = summary["result"]
    assert result["mode"] == "B"
    assert result["http_requests"] > 0
    assert result["prompt_tokens"] > 0 and result["completion_tokens"] > 0
    assert summary["goal_resolution"]["planner"] == "deepseek", "the goal was not the model's"
    counters = result["provider_counters"]
    assert set(counters) == {"api_errors", "transport_retries", "format_repairs"}, counters
    events = _events_of(summary)
    asked = [e for e in events if e["type"] == "decision_context"]
    decided = [e for e in events if e["type"] == "decision"]
    assert len(asked) == result["decision_rounds"], "one context per round the runtime counted"
    assert len(decided) <= result["decision_rounds"], "more decisions than rounds"
    for e in decided:
        assert e["payload"]["context_id"] and e["payload"]["based_on_state_version"] is not None
    for fb in [e["payload"]["feedback"] for e in events
               if e["type"] == "execution_feedback" and e["payload"]["feedback"]["executed"]]:
        assert fb["post_observation_ref"], "an action was taken without a measured result"
        assert fb["post_state_version"] >= fb["pre_state_version"]
    calls = _model_calls(summary)
    # one provider call per round, whatever the round's outcome: this is the
    # arithmetic a silent rule fallback cannot survive
    assert len([c for c in calls if c["kind"] == "decision"]) == result["decision_rounds"], calls
    answered = {c["context_id"] for c in calls
                if c["kind"] == "decision" and not c.get("error")}
    assert {e["payload"]["context_id"] for e in decided} <= answered, \
        "a decision was issued for a context the model was never shown"
    assert {c["provider"] for c in calls} == {"deepseek"}, calls
    return events


def _payload_sent(call):
    """The context a decision request carried: the user message is a lead-in
    sentence followed by the context JSON, so the JSON starts at the first brace."""
    content = call["body"]["messages"][1]["content"]
    return json.loads(content[content.index("{"):])


def _model_calls(summary):
    path = os.path.join(summary["artifacts"]["episode_dir"], "model_calls.jsonl")
    if not os.path.exists(path):
        pytest.fail("the model was asked nothing: no provider call log for this episode")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ------------------------------------------------------------------ offline ----


def test_the_offline_twin_exercises_the_real_online_path(tmp_path):
    adapter = DeepSeekAdapter(api_key=FAKE_KEY, model="deepseek-fake-echo", timeout_s=7.0)
    opener = _Opener(_model_does_the_task())
    adapter.opener = opener
    planner = DeepSeekPlanner(adapter)
    case, resolution, summary = _drive(planner, str(tmp_path))

    assert resolution.planner == "deepseek" and resolution.well_formed
    assert [a[1] for a in resolution.assignments] == [g["target_id"] for g in GOAL]
    # the shared goal parse is a model exchange too, so it leaves the same evidence
    # as a decision: which model answered, and the text it answered with
    assert resolution.model == "deepseek-fake-echo", resolution.provider_meta
    assert resolution.provider_meta["requested_model"] == "deepseek-fake-echo"
    assert resolution.provider_meta["returned_model"] == "deepseek-fake-echo"
    assert resolution.provider_meta["completion_id"], resolution.provider_meta
    assert json.loads(resolution.provider_meta["raw_response"])["assignments"][0]["target_id"] \
        == GOAL[0]["target_id"]
    with open(resolution.artifact, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["provider_meta"]["raw_response"] == resolution.provider_meta["raw_response"], \
        "the evidence was gathered but never written down"
    assert summary["result"]["terminal_status"] == "success"
    assert summary["score"]["complete_success"] is True, summary["score"]["details"]
    assert summary["probe"]["met"] is True, summary["probe"]
    # the answers were known, so what the model did is assertable
    assert summary["result"]["decision_rounds"] == 7 and summary["result"]["skill_calls"] == 6
    assert summary["result"]["http_requests"] == 8, "7 decisions + the shared goal parse"
    assert summary["result"]["prompt_tokens"] == 300 * 8
    assert summary["result"]["rejected_decisions"] == 0
    assert summary["result"]["model_errors"] == 0 and summary["result"]["semantic_repairs"] == 0
    # this model never names a slot, so the shared rule resolved all three
    # placements — recorded as a number, not as prose a reader has to trust
    assert summary["result"]["slot_resolution_fallbacks"] == 3
    assert summary["result"]["provider_counters"] == {"api_errors": 0, "transport_retries": 0,
                                                      "format_repairs": 0}
    events = _assert_nothing_substituted_the_model(summary)
    start = next(e for e in events if e["type"] == "episode_start")
    assert start["payload"]["prologue"]["http_requests"] == 1, "the goal parse is billed into the episode"
    assert start["payload"]["planner"] == "deepseek"
    # what the transport actually saw
    assert len(opener.calls) == 8
    assert all(c["url"] == "https://api.deepseek.com/chat/completions" for c in opener.calls)
    assert all(c["headers"]["Authorization"] == f"Bearer {FAKE_KEY}" for c in opener.calls)
    assert all(c["body"]["response_format"] == {"type": "json_object"} for c in opener.calls)
    assert all(c["body"]["model"] == "deepseek-fake-echo" for c in opener.calls)
    assert opener.calls[0]["timeout"] == 7.0
    # the sampling block a run manifest records is the request that went out, not a
    # paraphrase of the config file — otherwise a frozen batch states numbers nobody sent
    sent = opener.calls[0]["body"]
    assert adapter.sampling == {"model": "deepseek-fake-echo",
                                "base_url": "https://api.deepseek.com",
                                "temperature": adapter.temperature,
                                "max_tokens": adapter.max_tokens,
                                "response_format": "json_object", "stream": False,
                                "timeout_s": 7.0, "max_retries": adapter.max_retries,
                                # unset here, and `null` in the manifest is the truthful record:
                                # a default written into the body would be this process choosing
                                # the endpoint's behaviour under a claim that it recorded one
                                "reasoning_effort": None,
                                "proxy": "direct"}, adapter.sampling
    assert (sent["temperature"], sent["max_tokens"], sent["stream"]) == \
        (adapter.sampling["temperature"], adapter.sampling["max_tokens"],
         adapter.sampling["stream"]), "the manifest would record a different run than this one"
    # 有界历史, read off the wire rather than off the dataclass: the runtime kept
    # every feedback of the episode, the model was shown `feedback_depth` of them
    asked = [_payload_sent(c) for c in opener.calls[1:]]
    assert all(len(a["recent_feedbacks"]) <= planner.feedback_depth for a in asked)
    assert len(asked[-1]["recent_feedbacks"]) == planner.feedback_depth, \
        "the bound never bound anything: the episode was too short to test it"
    kept = [e["payload"]["feedbacks_included"] for e in events
            if e["type"] == "decision_context"]
    assert kept[-1] > planner.feedback_depth, kept
    assert all(len(a["attempts"]) <= HISTORY_CAP for a in asked), \
        "the attempt history a round is shown stopped being bounded"
    # a credential reaches the transport and nothing else
    for dirpath, _d, files in os.walk(str(tmp_path)):
        for fn in files:
            with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                text = f.read()
            assert FAKE_KEY not in text, fn
            assert "Authorization" not in text, fn


def test_a_refused_or_unparseable_answer_is_the_model_s_to_fix(tmp_path):
    """The same transport, one bad answer: the runtime re-asks with the reasons and
    does not repair the payload itself (SPEC 6.1). The scripted model then does the
    task, so the episode still completes — the repair is the model's, not ours."""
    contents = [json.dumps({"assignments": GOAL}),
                json.dumps({"action": "execute", "execute": {"skill": "place",
                                                             "args": {"object_id": None}}})]
    good = []
    for a in GOAL:
        good.append(_execute("pick", object_id=a["entity"]["entity_id"]))
        good.append(_execute("place", object_id=a["entity"]["entity_id"], target_id=a["target_id"]))
    good.append(json.dumps({"action": "finish"}))
    adapter = DeepSeekAdapter(api_key=FAKE_KEY, model="deepseek-fake-echo", timeout_s=7.0)
    opener = _Opener(_provider_replies(contents + good))
    adapter.opener = opener
    _case, _res, summary = _drive(DeepSeekPlanner(adapter), str(tmp_path))
    result = summary["result"]
    assert result["rejected_decisions"] == 1 and result["model_errors"] == 0
    assert result["semantic_repairs"] == 1
    assert result["terminal_status"] == "success", "one rejection is not a reason to lose the task"
    rejected = [e for e in _events_of(summary)
                if e["type"] == "execution_feedback" and e["payload"]["feedback"]["status"] == "rejected"]
    assert any(any("args.object_id" in r for r in fb["rejection_reasons"]) for fb in
               [e["payload"]["feedback"] for e in rejected]), "the model was not told what was wrong"


# ------------------------------------------------------------------- online ----


@pytest.mark.skipif(not ONLINE, reason="opt-in: RUN_ONLINE=1 with DEEPSEEK_API_KEY set")
def test_the_online_model_is_the_only_decision_maker():
    planner = DeepSeekPlanner.from_env()
    root = os.environ.get("ONLINE_OUT_DIR") or tempfile.mkdtemp(prefix="embodied_online_")
    os.makedirs(root, exist_ok=True)
    _case, resolution, summary = _drive(planner, root)
    _assert_nothing_substituted_the_model(summary)
    assert resolution.model == planner.model
    print(json.dumps({
        "episode_dir": summary["artifacts"]["episode_dir"],
        "terminal": summary["result"]["terminal_status"],
        "failure_type": summary["result"]["failure_type"],
        "objects": f'{summary["score"]["objects_completed"]}/{summary["score"]["objects_total"]}',
        "complete_success": summary["score"]["complete_success"],
        "decision_rounds": summary["result"]["decision_rounds"],
        "http_requests": summary["result"]["http_requests"],
        "tokens": [summary["result"]["prompt_tokens"], summary["result"]["completion_tokens"]],
        "cost_estimate_usd": planner.cost_estimate(),
        "unfired_declared_events": summary["environment_events"]["unfired"],
        "protocol_notes": summary["score"]["protocol_notes"],
    }, ensure_ascii=False, indent=2))


@pytest.mark.skipif(not ONLINE, reason="opt-in: RUN_ONLINE=1 with DEEPSEEK_API_KEY set")
def test_a_real_disturbance_reaches_the_real_model_through_the_same_interface(tmp_path):
    """The perturbed half of the SPEC 9 P2 exit condition.

    Asserted: the environment really moved something after the model had put it
    there, and every decision afterwards still came from the model with that
    feedback in front of it. Whether the model then finished is a measurement —
    printed, never asserted (SPEC 12.2), because an expected success would make
    this a check of the model rather than of the harness."""
    planner = DeepSeekPlanner.from_env()
    case, _resolution, summary = _drive(planner, str(tmp_path), case_id="smoke_state")
    events = _assert_nothing_substituted_the_model(summary)
    fired = summary["environment_events"]["fired"]
    assert fired, "the scene declared a disturbance and injected none: nothing was perturbed"
    assert summary["environment_events"]["unfired"] == []
    # the disturbance is a fact the model is shown, not one the runtime absorbs:
    # the round after the event is asked about a state that moved, with the
    # feedback that recorded the move in front of it
    at_event = next(i for i, e in enumerate(events) if e["type"] == "environment_event")
    before = [e["payload"] for e in events[:at_event] if e["type"] == "decision_context"]
    after = [e["payload"] for e in events[at_event:] if e["type"] == "decision_context"]
    assert before and after, "the model was not asked on both sides of the disturbance"
    assert after[-1]["state_version"] > before[-1]["state_version"], \
        "the round after the disturbance was shown a state that had not moved"
    assert after[-1]["feedbacks_included"] >= 1, "the change reached the log but not the context"
    print(json.dumps({
        "case": case.task_id, "episode_dir": summary["artifacts"]["episode_dir"],
        "disturbance": fired, "terminal": summary["result"]["terminal_status"],
        "objects": f'{summary["score"]["objects_completed"]}/{summary["score"]["objects_total"]}',
        "complete_success": summary["score"]["complete_success"],
        "decision_rounds": summary["result"]["decision_rounds"],
        "http_requests": summary["result"]["http_requests"],
    }, ensure_ascii=False, indent=2))
