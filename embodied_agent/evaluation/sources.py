"""Decision sources for the three comparison modes (SPEC 11.2).

One shape only: `decide(context) -> Decision`. The Runtime never knows who
answered, and no source can reach physics, the hidden `EvalSpec` or the event
configuration — a source sees exactly `DecisionContext.model_payload()` fields.

* `RulePolicySource`     Mode B with a deterministic policy (offline control)
* `OneShotPlanSource`    Mode A (core.planner): plan once, fixed repeat rule
* `LLMDecisionSource`    Mode B with an online model
* `LLMOneShotSource`     Mode A with an online model (one plan request at t0)
* `AblatedFeedbackSource` Mode C: same loop, model-visible feedback removed
* `ScriptedSource`       fixture-driven decisions for offline protocol tests

Everything that talks to a provider reports cumulative `http_requests`,
`prompt_tokens` and `completion_tokens`, so transport retries cannot hide inside
an adapter (SPEC 6.2: 全部 HTTP 请求进入成本账本).
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from ..core.contracts import (
    Decision,
    DecisionContext,
    DecisionExecute,
    Plan,
    PredicateVerdict,
    VerifyConfig,
)
from ..core.planner import OneShotPlanSource, RulePlanner


class RulePolicySource:
    """Mode B control policy: every round, recompute pending goals and act on the
    first one. It has no memory of its own rounds beyond the context it is
    handed, which is the point — the *state*, not the policy, decides.

    The policy itself lives in `core.planner.RulePlanner.decide` so that the
    offline fixture planner and this source cannot drift apart."""

    provider = "rule_policy"
    http_requests = 0
    prompt_tokens = 0
    completion_tokens = 0

    def __init__(self, config: VerifyConfig | None = None, prefer: Optional[str] = None):
        self.planner = RulePlanner(config)
        self.prefer = prefer  # deterministic order override for tests
        self.decisions = 0

    def decide(self, ctx: DecisionContext) -> Decision:
        self.decisions += 1
        return self.planner.decide(ctx, prefer=self.prefer)


class _PlannerBacked:
    """Forward a planner's cumulative provider counters through the source.

    The runtime bills an episode the *delta* of these, so transport retries and
    format repairs an adapter performed are visible in the cost ledger no matter
    which mode asked for them (SPEC 6.2, 11.4)."""

    @property
    def http_requests(self) -> int:
        return int(getattr(self.planner, "http_requests", 0))

    @property
    def prompt_tokens(self) -> int:
        return int(getattr(self.planner, "prompt_tokens", 0))

    @property
    def completion_tokens(self) -> int:
        return int(getattr(self.planner, "completion_tokens", 0))

    @property
    def api_errors(self) -> int:
        return int(getattr(self.planner, "api_errors", 0))

    @property
    def transport_retries(self) -> int:
        return int(getattr(self.planner, "transport_retries", 0))

    @property
    def format_repairs(self) -> int:
        return int(getattr(self.planner, "format_repairs", 0))

    @property
    def pricing(self) -> dict:
        return getattr(self.planner, "pricing", {}) or {}


class LLMDecisionSource(_PlannerBacked):
    """Mode B with an online model: one request per decision boundary."""

    provider = "deepseek"

    def __init__(self, planner, log: Callable[[dict, float], None] | None = None):
        self.planner = planner
        self.log = log
        self.provider = getattr(planner, "provider", "llm")
        self.model = getattr(planner, "model", "unknown")
        self.prompt_version = getattr(planner, "prompt_version", "unversioned")

    def decide(self, ctx: DecisionContext) -> Decision:
        t0 = time.time()
        before = self.http_requests
        tok_before = (self.prompt_tokens, self.completion_tokens)
        try:
            decision, meta = self.planner.decide(ctx)
        except Exception as e:  # noqa: BLE001 - the call still belongs in the cost ledger
            if self.log:
                # the counters travel with the row even when the payload was
                # rejected: a schema refusal still spent the request it was billed
                # for, and an offline recomputation must not have to trust a number
                # it cannot see (SPEC-BST 10.4, 13-9)
                ta, ca = self.prompt_tokens, self.completion_tokens
                self.log({"provider": self.provider, "model": self.model,
                          "prompt_version": self.prompt_version, "kind": "decision",
                          "round_index": ctx.round_index, "context_id": ctx.context_id,
                          "http_requests_this_call": self.http_requests - before,
                          "usage": {"prompt_tokens": ta - tok_before[0],
                                    "completion_tokens": ca - tok_before[1],
                                    "source": "provider counter delta across the failed call"},
                          "error": f"{type(e).__name__}: {e}",
                          "error_reasons": list(getattr(e, "reasons", [])),
                          "raw_response": getattr(e, "meta", {}).get("raw_response")},
                         time.time() - t0)
            raise
        if self.log:
            self.log({**meta, "kind": "decision", "round_index": ctx.round_index,
                      "context_id": ctx.context_id,
                      "http_requests_this_call": self.http_requests - before},
                     time.time() - t0)
        return decision


class LLMOneShotSource(_PlannerBacked, OneShotPlanSource):
    """Mode A with an online model: exactly one planning request, then the
    frozen one-shot rule from `core.planner` (no revision, bounded identical
    repeats). No further request is made, so the accounting it must report is the
    planner's cumulative counter, which already includes that one request."""

    def __init__(self, planner, plan: Plan, per_action_extra_repeats: int = 2):
        OneShotPlanSource.__init__(self, plan,
                                   per_action_extra_repeats=per_action_extra_repeats)
        self.planner = planner
        self.provider = getattr(planner, "provider", "llm")
        self.model = getattr(planner, "model", "unknown")
        self.prompt_version = getattr(planner, "prompt_version", "unversioned")


def llm_one_shot_plan(planner, ctx: DecisionContext, per_action_extra_repeats: int,
                      log: Callable[[dict, float], None] | None = None) -> LLMOneShotSource:
    """Mode A's single planning request. A failure here is the baseline's own
    outcome and propagates: the caller records the episode as failed rather than
    silently falling back to the rule plan (SPEC 11.2)."""
    t0 = time.time()
    before = int(getattr(planner, "http_requests", 0))
    try:
        plan, meta = planner.plan_from_context(ctx)
    except Exception as e:  # noqa: BLE001
        if log:
            log({"provider": getattr(planner, "provider", "llm"),
                 "model": getattr(planner, "model", "unknown"),
                 "prompt_version": getattr(planner, "prompt_version", "unversioned"),
                 "kind": "one_shot_plan", "context_id": ctx.context_id,
                 "http_requests_this_call": int(getattr(planner, "http_requests", 0)) - before,
                 "error": f"{type(e).__name__}: {e}",
                 "error_reasons": list(getattr(e, "reasons", [])),
                 "raw_response": getattr(e, "meta", {}).get("raw_response")},
                time.time() - t0)
        raise
    if log:
        log({**meta, "kind": "one_shot_plan", "context_id": ctx.context_id,
             "http_requests_this_call": int(getattr(planner, "http_requests", 0)) - before},
            time.time() - t0)
    return LLMOneShotSource(planner, plan,
                            per_action_extra_repeats=per_action_extra_repeats)


class AblatedFeedbackSource:
    """Mode C: continuous decisions on the *newest* WorldState, with the model's
    own feedback channel removed — no verification report, no measurements, no
    stage list, no attempt history (SPEC 11.2). It is not "no feedback": the
    state itself still shows what happened, which is exactly the ablation's
    limit and must be stated when reading its results."""

    def __init__(self, inner):
        self.inner = inner
        self.provider = f"{getattr(inner, 'provider', type(inner).__name__)}_ablated"

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def decide(self, ctx: DecisionContext) -> Decision:
        stripped = _strip_feedback(ctx)
        decision = self.inner.decide(stripped)
        # the inner source echoed the ablated context id; the runtime validates
        # against the context it built, so rebind the identifiers it issued
        return decision.model_copy(update={"context_id": ctx.context_id,
                                          "based_on_state_version": ctx.state_version})


def _strip_feedback(ctx: DecisionContext) -> DecisionContext:
    def thin(fb):
        if fb is None:
            return None
        return fb.model_copy(update={"verification": None, "measurements": {}, "state_diff": {},
                                     "stages_executed": [], "progress_changes": [],
                                     "affected_entities": []})
    return ctx.model_copy(update={
        "last_feedback": thin(ctx.last_feedback),
        "recent_feedbacks": [f for f in (thin(x) for x in ctx.recent_feedbacks) if f is not None],
        "attempts": [],
        "candidates_truncated": ctx.candidates_truncated,
    })


class ScriptedSource:
    """Fixture decisions for offline protocol and closed-loop tests.

    `steps` is a list of `Decision`, `(action, execute_or_None)` tuples, or
    `Exception` instances (which stand in for a model/transport error at that
    round). When the script runs out, `on_exhausted` decides what happens: the
    default repeats the last entry so a short script can drive a long episode,
    and "raise" makes an under-specified fixture fail loudly instead of quietly
    succeeding.
    """

    provider = "fixture"
    prompt_tokens = 0
    completion_tokens = 0

    def __init__(self, steps: list, *, per_action_extra_repeats: int = 0,
                 on_exhausted: str = "repeat_last", http_requests_per_call: int = 1):
        self.steps = list(steps)
        self.on_exhausted = on_exhausted
        self.http_requests_per_call = http_requests_per_call
        self.http_requests = 0
        self.calls = 0
        self.seen: list[DecisionContext] = []

    def decide(self, ctx: DecisionContext) -> Decision:
        self.calls += 1
        self.http_requests += self.http_requests_per_call
        self.seen.append(ctx)
        idx = self.calls - 1
        if idx >= len(self.steps):
            if self.on_exhausted == "raise":
                raise AssertionError(f"script exhausted after {len(self.steps)} decisions")
            item = self.steps[-1]
        else:
            item = self.steps[idx]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Decision):
            return item
        action, payload = item
        if action == "execute":
            skill, args, candidate = payload[0], payload[1], (payload[2] if len(payload) > 2 else None)
            return Decision(context_id=ctx.context_id, based_on_state_version=ctx.state_version,
                            goal_ref=ctx.goal.goal_id, action="execute",
                            execute=DecisionExecute(skill=skill, args=dict(args), candidate_id=candidate))
        return Decision(context_id=ctx.context_id, based_on_state_version=ctx.state_version,
                        goal_ref=ctx.goal.goal_id, action=action,
                        missing_information=payload if action == "clarify" else None,
                        rationale="scripted")


def pending_pairs(ctx: DecisionContext) -> list[tuple[str, str]]:
    return [(p.entity_id, p.target_id) for p in ctx.progress
            if p.value != PredicateVerdict.true]
