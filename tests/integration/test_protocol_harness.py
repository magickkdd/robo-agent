"""The SPEC 11.1 boundary cases that a scene cannot express: bad model payloads.

`PROTOCOL_CONFIGS` covers the bullets an episode can freeze (an unresolvable
reference, a full tray, each budget). The remaining eleven bullets are about
what comes back from the *decision source* — a held-state conflict, a stale or
infeasible candidate id, a missing argument, an empty or schema-invalid or
absent provider response. They cannot be frozen as scenes, so `tasks.PROTOCOL_HARNESS`
lists them by name and this file owns each one with a test of the same name.

The rule for every test here is the same: drive the **real** `Runtime` (through
`run_case`, i.e. the pieces `evaluation.run` uses) and substitute only the thing
the boundary is about — a scripted `Decision`, or a real `DeepSeekAdapter` whose
*transport* is fake. `http_requests`, `transport_retries`, `format_repairs` and
`api_errors` are then the adapter's own counters and the ledger's own deltas, so
an accounting bug cannot hide behind a mock.

`test_protocol_harness_names_are_exactly_the_ones_owned_here` is what keeps the
list and this file from drifting apart in either direction.
"""
from __future__ import annotations

import json
import sys
import urllib.error

from embodied_agent.adapters.deepseek import DeepSeekAdapter, DeepSeekPlanner
from embodied_agent.core.contracts import (
    Decision,
    DecisionContext,
    DecisionExecute,
    FailureCode,
    GoalAssignment,
    GoalSpec,
    PredicateVerdict,
    SkillCall,
)
from embodied_agent.core.runtime import MAX_MODEL_ERRORS
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation.sources import LLMDecisionSource
from embodied_agent.evaluation.tasks import ALL_SETS, PROTOCOL_HARNESS, build_set, find_case

# a credential-shaped constant on purpose: the redaction guard is what makes it
# safe to hold, and the tests below check it never reaches an artifact
FAKE_KEY = "sk-" + "0123456789abcdef" * 3


# ------------------------------------------------------ the fake transport --


class _Reply:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Only `urlopen` is replaced. Everything above it — request building, the
    retry loop, the JSON parse, the counters, the pydantic validation — is the
    production adapter."""

    def __init__(self, replies, *, raises: Exception | None = None):
        self.replies = list(replies)
        self.raises = raises
        self.calls: list[dict] = []

    def open(self, req, timeout=None):
        self.calls.append({"url": req.full_url,
                           "headers": dict(req.header_items()),
                           "body": json.loads(req.data.decode("utf-8")),
                           "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return _Reply(json.dumps(item).encode("utf-8"))


def provider_payload(content: str, *, usage: dict | None = None) -> dict:
    return {"id": "chatcmpl-fake", "model": "deepseek-fake-echo",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 25}}


def model_source(replies, *, raises=None, max_retries: int = 0):
    """The production mode-B source over a fake transport."""
    adapter = DeepSeekAdapter(api_key=FAKE_KEY, model="deepseek-fake-echo",
                              max_retries=max_retries, timeout_s=7.0)
    opener = _FakeOpener(replies, raises=raises)
    adapter.opener = opener
    return LLMDecisionSource(DeepSeekPlanner(adapter)), opener


def _poses(world) -> dict[str, tuple[float, float, float]]:
    return {e.entity_id: (e.pose.position.x, e.pose.position.y, e.pose.position.z)
            for e in world.entities}


def ends_with_note(ep) -> str:
    ends = ep.payloads("episode_end")
    assert ends, "the episode wrote no terminal record"
    return str(ends[-1].get("note") or "")


def feedbacks_of(ep, code: str) -> list[dict]:
    return [fb for fb in ep.feedbacks() if fb.get("failure_code") == code]


def _assert_no_credential_in_artifacts(ep):
    raw = open(ep.runtime.store.path, encoding="utf-8").read()
    assert FAKE_KEY not in raw and "Authorization" not in raw
    assert FAKE_KEY not in ep.agent_visible(), "the key reached the prompt surface"


# ------------------------------------------------ 1. held-state conflict ----


def test_h_hold_state_conflict(run_case):
    """A place for an object the gripper is not holding never reaches physics."""
    ep = run_case("smoke_clean", steps=[
        ("execute", ("pick", {"object_id": "obj_green_1"})),
        ("execute", ("place", {"object_id": "obj_blue_2", "target_id": "tray_left"})),
    ])
    rejects = [fb for fb in ep.feedbacks() if fb.get("status") == "rejected"]
    assert rejects, "the conflicting place was not rejected"
    reason = " ".join(rejects[0]["rejection_reasons"])
    assert "gripper holds" in reason and "obj_green_1" in reason, reason
    assert rejects[0]["executed"] is False
    assert rejects[0]["held_object_after"] == "obj_green_1", "the guard must not release"
    # "before physics" is checkable: a rejected decision is never a skill call
    assert [c["call"]["skill"] for c in ep.payloads("skill_call")] == ["pick"]

    # and the executor's own guard says the same thing when the decision-level
    # validator is bypassed — defence in depth is a measured claim, not a comment
    ex = SkillExecutor(ep.scene, world_provider=ep.runtime.observe, config=ep.case.verify)
    before = _poses(ep.runtime.observe())
    res = ex.execute(SkillCall(plan_id="direct", step_id="d0", skill="place",
                               args={"object_id": "obj_blue_2", "target_id": "tray_left"}))
    assert res.status.value == "rejected"
    assert res.failure_code == FailureCode.HELD_STATE_CONFLICT.value, res.failure_code
    assert "obj_green_1" in " ".join(res.notes)
    after = _poses(ep.runtime.observe())
    assert after.keys() == before.keys()
    for eid, pos in before.items():
        assert all(abs(a - b) < 1e-6 for a, b in zip(pos, after[eid])), eid
    assert ep.runtime.observe().held_object == "obj_green_1"


# --------------------------------------------------- 2. missing argument ----


def test_h_missing_argument(run_case):
    """A decision with no `object_id` is a structured rejection, not a guess."""
    ep = run_case("smoke_clean", steps=[("execute", ("pick", {}))])
    rejects = [fb for fb in ep.feedbacks() if fb.get("status") == "rejected"]
    limit = ep.case.budgets.max_semantic_repairs
    assert len(rejects) == limit + 1, [r["rejection_reasons"] for r in rejects]
    reasons = " ".join(rejects[0]["rejection_reasons"])
    assert "object_id" in reasons and "pick" in reasons, reasons
    assert rejects[0]["failure_code"] == "INVALID_DECISION"
    assert rejects[0]["skill"] == "pick" and rejects[0]["entity_id"] is None
    assert not ep.payloads("skill_call"), "an incomplete action was executed anyway"
    assert ep.terminal_status == "failed" and ep.failure_type == "INVALID_DECISION"
    assert ep.result.prompt_tokens == 0 and ep.result.http_requests == limit + 1


# ---------------------------------------------------- 3. stale candidate ----


def test_h_stale_candidate(run_case):
    """An id that was never offered is CANDIDATE_STALE, and the loop stays bounded."""
    stale = "cand-never-offered"
    place = ("execute", ("place", {"object_id": "obj_green_1", "target_id": "tray_left"}, stale))
    ep = run_case("smoke_clean", steps=[
        ("execute", ("pick", {"object_id": "obj_green_1"})), place, place, place, place])
    hits = feedbacks_of(ep, "CANDIDATE_STALE")
    assert len(hits) == 4, [fb["failure_code"] for fb in ep.feedbacks()]
    for fb in hits:
        assert fb["candidate_id"] == stale
        assert fb["executed"] is False
        assert fb["held_object_after"] == "obj_green_1"
        assert "never offered" in " ".join(fb["rejection_reasons"])
    offered = json.dumps([p["candidates"] for p in ep.payloads("decision_context")])
    assert stale not in offered, "that id was in fact offered"
    # repeating it under an unchanged world is what ends the episode
    assert ep.terminal_status == "failed" and ep.failure_type == "REPEATED_INVALID"
    assert "no new evidence" in ends_with_note(ep)
    assert ep.result.identical_invalid_attempts > ep.case.budgets.max_identical_invalid_attempts
    # the two counters answer different questions and must stay consistent: the
    # budget bound is a *run length*, so the number of attempts that were wasted
    # on repeats is that run minus the one attempt that was not a repeat
    assert ep.result.identical_repeats_total == \
        ep.result.identical_invalid_attempts - 1 == \
        ep.case.budgets.max_identical_invalid_attempts
    assert ep.result.decision_rounds <= ep.case.budgets.max_decision_rounds
    # a refused call is still a call: it is billed, and it is not a silent repair
    assert ep.result.skill_calls == 5 and ep.result.semantic_repairs == 0


# ---------------------------------------------- 4. infeasible candidate ----


class _NameBlockedCandidate:
    """A policy that reads the payload and names a slot the payload itself marks
    as failing a check. It is a bad *choice*, not a fabricated id: the id, the
    geometry and the object/target binding all come from `ctx.candidates`."""

    provider = "script"
    http_requests = 0

    def __init__(self):
        self.seen: list[DecisionContext] = []
        self.named: str | None = None

    def decide(self, ctx: DecisionContext) -> Decision:
        self.seen.append(ctx)
        held = ctx.world.held_object
        base = {"context_id": ctx.context_id, "based_on_state_version": ctx.state_version,
                "goal_ref": ctx.goal.goal_id}
        if held and self.named is None:
            mine = [c for c in ctx.candidates if c.entity_id == held]
            blocked = [c for c in mine if "fail" in c.summary()["checks"].values()]
            assert blocked, "no offered candidate was marked infeasible; the test proves nothing"
            cand = blocked[0]
            self.named = cand.candidate_id
            return Decision(**base, action="execute",
                            execute=DecisionExecute(skill="place",
                                                    args={"object_id": held,
                                                          "target_id": cand.target_id},
                                                    candidate_id=cand.candidate_id))
        if held:
            return Decision(**base, action="blocked",
                            missing_information="every named slot failed its re-check")
        pending = [p for p in ctx.progress if p.value != PredicateVerdict.true]
        return Decision(**base, action="execute",
                        execute=DecisionExecute(skill="pick", args={"object_id": pending[0].entity_id}))


def test_h_infeasible_candidate(run_case):
    """A named candidate that fails its execution-time re-check is refused while
    the object stays held — the geometry, not the id, is what is checked."""
    src = _NameBlockedCandidate()
    ep = run_case("pr_target_full", source=src, source_kind="script+named_candidate")
    hits = feedbacks_of(ep, "CANDIDATE_INFEASIBLE")
    assert hits, [fb["failure_code"] for fb in ep.feedbacks()]
    fb = hits[0]
    assert fb["candidate_id"] == src.named
    assert fb["executed"] is False and fb["held_object_after"] == "obj_red_1"
    offered = [c for p in ep.payloads("decision_context") for c in p["candidates"]
               if c["candidate_id"] == src.named]
    assert offered and "fail" in offered[0]["checks"].values(), offered
    assert "re-check failed" in " ".join(fb["rejection_reasons"])
    # the runtime did not quietly substitute its own slot
    assert ep.runtime.slot_resolution_fallbacks == 0
    assert all(c["call"]["args"].get("candidate_id") for c in ep.payloads("skill_call")
               if c["call"]["skill"] == "place")
    # and the policy's own exit is a bounded failure, not a blind placement
    assert ep.terminal_status == "failed" and ep.failure_type is None
    assert not [p for p in ep.feedbacks() if p["skill"] == "place" and p["executed"]]


# ------------------------------------------------- 5. unknown predicates ----


def test_h_unknown_predicate(world_for):
    """Absent measurement propagates as `unknown` everywhere it is reported."""
    case = find_case("smoke_clean")
    scene, world = world_for("smoke_clean")
    eid = world.entities[0].entity_id
    v = RuntimeVerifier(world, case.verify)

    lift = v.verify_grasp(eid)
    assert lift.value == PredicateVerdict.unknown
    assert "lift_gain" in lift.unmeasured and "unmeasurable" in lift.description

    absent = v.verify_placement("obj_ghost_1", "tray_left")
    assert absent.value == PredicateVerdict.unknown and absent.unmeasured == ["entity"]
    no_target = v.verify_placement(eid, "tray_drawer")
    assert no_target.value == PredicateVerdict.unknown and no_target.unmeasured == ["target"]

    # a support / rest / hold sub-fact that was not measured is `unknown` with
    # the sub-fact named, never `true`. `model_copy(deep=True)` because a shallow
    # copy would edit the snapshot the assertions above were made on
    partial = world.model_copy(deep=True)
    target = next(e for e in partial.entities if e.entity_id == eid)
    assert target is not next(e for e in world.entities if e.entity_id == eid)
    target.supported_by = "unknown"
    target.at_rest = "unknown"
    target.held = "unknown"
    rep = RuntimeVerifier(partial, case.verify).verify_placement(eid, "tray_left")
    assert rep.value == PredicateVerdict.unknown
    assert {"supported", "at_rest", "not_held"} <= set(rep.unmeasured), rep.unmeasured
    assert rep.evidence["measurable"] == 0.0, "unknown was encoded as a measured 1.0/0.0"
    assert RuntimeVerifier(world, case.verify).verify_placement(
        eid, "tray_left").value != PredicateVerdict.unknown, "the measured world is not complete"

    # an unbindable reference is unknown too, and is reported that way downstream.
    # A named-but-absent id and an attribute set matching nothing are different
    # failures, and each names its own missing measurement
    goal = GoalSpec(assignments=[
        GoalAssignment(entity={"entity_id": eid}, target_id="tray_left"),
        GoalAssignment(entity={"entity_id": "obj_ghost_1"}, target_id="tray_left"),
        GoalAssignment(entity={"attributes": {"color": "chartreuse"}}, target_id="tray_left")])
    items = v.progress(goal)
    assert items[1].value == PredicateVerdict.unknown
    assert items[1].unmeasured == ["entity"], items[1].unmeasured
    assert items[2].value == PredicateVerdict.unknown
    assert items[2].unmeasured == ["entity_binding"], items[2].unmeasured
    assert v.verify_goals(goal).overall is not PredicateVerdict.true, \
        "an unknown goal bought a success claim"
    dumped = [i.model_dump(mode="json") for i in items]
    assert [d["value"] for d in dumped] == ["false", "unknown", "unknown"], dumped
    assert all(d["unmeasured"] for d in dumped[1:]), dumped[1:]


# ------------------------------------------------------ 6. false finish ----


def test_h_false_finish(run_case):
    """`finish` is a claim the verifier answers, and the claim is bounded."""
    ep = run_case("smoke_clean", steps=[("finish", None)])
    rejects = feedbacks_of(ep, "FINISH_REJECTED")
    assert len(rejects) == 3, [fb["status"] for fb in ep.feedbacks()]
    assert all(fb["status"] == "finish_rejected" and fb["executed"] is False for fb in rejects)
    checks = ep.payloads("finish_check")
    assert len(checks) == 3 and all(c["accepted"] is False for c in checks)
    for c in checks:
        assert c["reports"] and all(r["value"] != "true" for r in c["reports"]), c["reports"]
    assert ep.result.false_finish_attempts == 3
    assert ep.result.semantic_repairs == 0, "a refused claim is not a schema repair"
    assert ep.terminal_status == "failed" and ep.failure_type == "FINISH_REJECTED"
    assert "repeated false finish" in ends_with_note(ep)
    assert not ep.payloads("skill_call"), "finishing early must not act"
    assert ep.score["complete_success"] is False


# -------------------------------------------------- 7. repeated invalid ----


def test_h_repeated_invalid(run_case):
    """The identical action under an unchanged world ends the episode, and the
    counter is about *evidence*: the executed pick before it is not a repeat."""
    place = ("execute", ("place", {"object_id": "obj_red_1", "target_id": "tray_middle"}))
    ep = run_case("pr_target_full", steps=[
        ("execute", ("pick", {"object_id": "obj_red_1"})), place, place, place, place])
    codes = [fb["failure_code"] for fb in ep.feedbacks()]
    assert codes[0] is None, codes
    assert all(c in (FailureCode.TARGET_FULL.value, FailureCode.NO_FEASIBLE_CANDIDATE.value)
               for c in codes[1:]), codes
    for fb in ep.feedbacks()[1:]:
        assert fb["executed"] is False and fb["held_object_after"] == "obj_red_1"
    assert ep.terminal_status == "failed" and ep.failure_type == "REPEATED_INVALID"
    assert "no new evidence" in ends_with_note(ep)
    assert ep.result.identical_invalid_attempts == \
        ep.case.budgets.max_identical_invalid_attempts + 1
    assert ep.result.semantic_repairs == 0, "an executed refusal is not a repair"
    assert ep.result.decision_rounds <= ep.case.budgets.max_decision_rounds
    assert not [fb for fb in ep.feedbacks() if fb["skill"] == "place" and fb["executed"]], \
        "the tray was filled by a placement the environment contradicted"


# -------------------------------------------- 8. semantic repair limit ----


def test_h_semantic_repair_limit(run_case):
    """Rejections stop at `max_semantic_repairs + 1` instead of looping."""
    limit = find_case("smoke_clean").budgets.max_semantic_repairs
    ep = run_case("smoke_clean", steps=[("execute", ("pick", {}))] * (limit + 6))
    assert ep.result.semantic_repairs == limit + 1
    assert ep.result.rejected_decisions == limit + 1
    assert ep.result.decision_rounds == limit + 1, "the round budget was reached instead"
    assert ep.result.decision_rounds < ep.case.budgets.max_decision_rounds
    assert ep.result.model_errors == 0, "a rejection is not a transport failure"
    assert "semantic repair budget" in ends_with_note(ep)
    assert ep.terminal_status == "failed" and ep.failure_type == "INVALID_DECISION"
    assert not ep.payloads("skill_call")
    # every rejection was fed back with its reasons, so the model could have repaired
    rejected = [fb for fb in ep.feedbacks() if fb["status"] == "rejected"]
    assert len(rejected) == limit + 1 and all(fb["rejection_reasons"] for fb in rejected)


# ----------------------------------------------- 9. empty model response ----


def test_h_empty_model_response(run_case, recorder):
    """An empty completion is a bounded MODEL_ERROR outcome, not a crash."""
    replies = [provider_payload("", usage={"prompt_tokens": 100, "completion_tokens": 0})]
    src, opener = model_source(replies)
    ep = run_case("smoke_clean", source=src, source_factory=recorder().wrap,
                  source_kind="model+fake_transport")
    assert ep.terminal_status == "failed" and ep.failure_type == "MODEL_ERROR"
    assert "repeated model errors" in ends_with_note(ep)
    assert ep.result.model_errors == MAX_MODEL_ERRORS
    errs = feedbacks_of(ep, "MODEL_ERROR")
    assert len(errs) == MAX_MODEL_ERRORS
    for fb in errs:
        assert fb["executed"] is False
        assert "unparseable JSON after 1 format repair" in " ".join(fb["rejection_reasons"])
        assert "no action was executed" in fb["rejection_reasons"]
    # the format repair is a real second request and the ledger sees it
    assert len(opener.calls) == 2 * MAX_MODEL_ERRORS
    assert ep.result.http_requests == 2 * MAX_MODEL_ERRORS
    assert ep.source.format_repairs == MAX_MODEL_ERRORS
    assert ep.result.provider_counters["format_repairs"] == MAX_MODEL_ERRORS
    assert ep.result.prompt_tokens == 200 * MAX_MODEL_ERRORS
    assert not ep.payloads("skill_call"), "nothing was executed on an empty answer"
    # the model is still asked, and told, in a well-formed context
    assert len(ep.contexts) == MAX_MODEL_ERRORS
    assert ep.contexts[0].model_payload()["budget"]["http_requests_total"] == 32
    _assert_no_credential_in_artifacts(ep)


# --------------------------------------------------- 10. schema error ----


def test_h_schema_error(run_case, recorder):
    """A payload that parses but is not a `Decision` is rejected with the
    field-level reasons — the adapter does not repair it into validity."""
    replies = [provider_payload(json.dumps(
        {"action": "execute",
         "execute": {"skill": "place", "args": {"object_id": None, "target_id": "tray_left"}}}))]
    src, opener = model_source(replies)
    ep = run_case("smoke_clean", source=src, source_factory=recorder().wrap,
                  source_kind="model+fake_transport")
    rejects = [fb for fb in ep.feedbacks() if fb.get("status") == "rejected"]
    assert len(rejects) == 3, [fb["status"] for fb in ep.feedbacks()]
    reasons = rejects[0]["rejection_reasons"]
    assert any("args.object_id" in r for r in reasons), reasons
    assert all(":" in r for r in reasons), "reasons must be field-qualified, not prose"
    assert "missing required argument" not in " ".join(reasons), \
        "the payload reached semantic validation instead of failing schema"
    assert rejects[0]["failure_code"] == "INVALID_DECISION"
    # a schema error is the model's to repair, so it is not billed as a model error
    assert ep.result.model_errors == 0
    assert ep.result.semantic_repairs == 3 and ep.failure_type == "INVALID_DECISION"
    assert "semantic repair budget" in ends_with_note(ep)
    assert len(opener.calls) == 3, "a schema rejection re-asks, it does not re-parse"
    assert ep.result.http_requests == 3
    assert not ep.payloads("skill_call")
    # the raw text survives into the provider meta untouched, and the model is
    # told in its next context exactly what was wrong
    tail = ep.contexts[-1].recent_feedbacks[-1]
    assert list(tail.rejection_reasons) == reasons, (tail.rejection_reasons, reasons)
    _assert_no_credential_in_artifacts(ep)


# ---------------------------------------------------- 11. api timeout ----


def test_h_api_timeout(run_case, recorder, monkeypatch):
    """Every opened request is billed, retries included, and the episode ends."""
    backoffs: list[float] = []
    # the adapter's own backoff is `time.sleep(1.5 * attempts)`; recording it keeps
    # the retry loop intact and the test off the wall clock
    monkeypatch.setattr("time.sleep", lambda s: backoffs.append(s))
    retries = 2
    src, opener = model_source([provider_payload("{}")],
                               raises=urllib.error.URLError("timed out"), max_retries=retries)
    ep = run_case("smoke_clean", source=src, source_factory=recorder().wrap,
                  source_kind="model+fake_transport")
    attempts = 1 + retries
    assert len(opener.calls) == attempts * MAX_MODEL_ERRORS
    assert ep.result.http_requests == attempts * MAX_MODEL_ERRORS, "a retry hid from the ledger"
    assert ep.result.http_requests <= ep.case.budgets.max_http_requests
    assert ep.source.transport_retries == retries * MAX_MODEL_ERRORS
    assert ep.result.provider_counters["transport_retries"] == retries * MAX_MODEL_ERRORS
    assert ep.source.api_errors == MAX_MODEL_ERRORS
    assert backoffs == [1.5 * n for n in range(1, attempts)] * MAX_MODEL_ERRORS, backoffs
    errs = feedbacks_of(ep, "MODEL_ERROR")
    assert len(errs) == MAX_MODEL_ERRORS
    assert all("timed out" in " ".join(fb["rejection_reasons"]) for fb in errs)
    assert all("URLError" in fb["rejection_reasons"][0] for fb in errs)
    assert ep.terminal_status == "failed" and ep.failure_type == "MODEL_ERROR"
    assert not ep.payloads("skill_call")
    # the Authorization header was really sent, and really stayed out of the log
    assert opener.calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert opener.calls[0]["url"].endswith("/chat/completions")
    assert opener.calls[0]["timeout"] == 7.0
    _assert_no_credential_in_artifacts(ep)


# ------------------------------------------------------ list ownership ----


def test_protocol_harness_names_are_exactly_the_ones_owned_here():
    module = sys.modules[__name__]
    owned = {n[len("test_h_"):]: fn for n, fn in vars(module).items()
             if n.startswith("test_h_") and callable(fn)}
    listed = {h["name"][len("h_"):]: h for h in PROTOCOL_HARNESS}
    assert set(owned) == set(listed), (sorted(set(owned) ^ set(listed)),
                                       "a SPEC 11.1 boundary is listed but untested, or "
                                       "tested but not in PROTOCOL_HARNESS")
    for name, spec in listed.items():
        assert callable(owned[name]), name
        assert spec["asserts"], name
        assert owned[name].__doc__, f"test_h_{name} states nothing about its boundary"


def test_the_harness_cases_stay_out_of_the_score_denominator():
    """`PROTOCOL_HARNESS` is a test list, not a task set, so it must not be
    counted as cases; together they are the SPEC 11.1 boundary total."""
    names = {h["name"] for h in PROTOCOL_HARNESS}
    total = 0
    for set_name in ALL_SETS:
        cases = build_set(set_name)
        total += len(cases) if set_name == "protocol" else 0
        assert not (names & {c.task_id for c in cases}), set_name
    assert len(names) == len(PROTOCOL_HARNESS) == 11
    assert total + len(PROTOCOL_HARNESS) >= 12
