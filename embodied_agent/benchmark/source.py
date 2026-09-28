"""The online decision source for a text episode (SPEC-BST 5.3, 5.5).

Shaped exactly like `DeepSeekPlanner` — `decide(ctx) -> (Decision, meta)` plus the
cumulative provider counters — so the existing `LLMDecisionSource` wrapper, the
existing accounting in `Runtime` and the existing cost ledger apply without a
benchmark-specific copy of any of them. Three things are deliberately *not* here:

* no goal parse. A text episode's goal is the instruction sentence, so a request
  that re-derived it would either change the task or cost money for nothing;
* no strategy. The reply is bound to the context the caller issued and validated as
  a `Decision`; a payload that is not one raises `DecisionSchemaError` with the
  field-level reasons so the Runtime refuses it and charges the bounded repair
  budget (v0.1 mechanism, SPEC-BST 3.2);
* no hidden filtering of the action. Which commands the engine would accept is
  computed inside the environment and never requested (SPEC-BST 4.5).

The prompt is the benchmark one (`benchmark/prompts.py`), so a text run cannot be
mistaken for a desktop run of the frozen `s2-decide-v1` prompt; the returned model
identity of every round is kept so a mid-batch provider change is detectable
(SPEC-BST 5.5: 实际返回模型身份异常或中途变化时暂停后续调度).
"""
from __future__ import annotations

import json
import time

from ..adapters.deepseek import DeepSeekAdapter, DeepSeekPlanner
from ..core.contracts import Decision, DecisionContext
from .prompts import BST_DECISION_SYSTEM, BST_DECISION_USER, prompts_sha256

# SPEC-BST 5.3: one generation may not exceed 2,048 tokens. The configured cap is
# honoured when it is smaller, because sending a lower cap is a stricter reading of
# the same rule, not a way around it.
MAX_TOKENS_PER_REQUEST = 2048


class AlfredTextPlanner:
    """One request per decision boundary, over the shared HTTP client."""

    provider = "deepseek"

    def __init__(self, adapter: DeepSeekAdapter, *,
                 decision_prompt: str = "bst-decide-text-v1", feedback_depth: int = 3,
                 max_tokens_per_request: int = MAX_TOKENS_PER_REQUEST):
        self.adapter = adapter
        self.model = adapter.model
        self.provider = adapter.provider
        self.decision_prompt = decision_prompt
        self.feedback_depth = feedback_depth
        self.max_tokens = min(int(adapter.max_tokens or max_tokens_per_request),
                             max_tokens_per_request)
        self.prompt_version = f"decide={decision_prompt}"
        self.prompts_sha256 = prompts_sha256()
        # one entry per request, kept for the whole object lifetime: the runner
        # drains the episode's slice when it writes the trajectory record
        self.rounds: list[dict] = []
        self.returned_models: list[str] = []

    # ---------- cumulative accounting ----------
    @property
    def http_requests(self) -> int:
        return self.adapter.http_requests

    @property
    def prompt_tokens(self) -> int:
        return self.adapter.prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self.adapter.completion_tokens

    @property
    def api_errors(self) -> int:
        return self.adapter.api_errors

    @property
    def transport_retries(self) -> int:
        return self.adapter.transport_retries

    @property
    def format_repairs(self) -> int:
        return self.adapter.format_repairs

    @property
    def pricing(self) -> dict:
        return self.adapter.pricing

    @property
    def sampling(self) -> dict:
        """What was actually sent, with the generation cap this source imposes
        recorded beside the adapter's own default (SPEC-BST 5.5)."""
        return {**self.adapter.sampling, "max_tokens": self.max_tokens,
                "max_tokens_cap": MAX_TOKENS_PER_REQUEST,
                "prompts_sha256": self.prompts_sha256,
                "decision_prompt": self.decision_prompt,
                "feedback_depth": self.feedback_depth}

    def cost_estimate(self) -> float | None:
        return self.adapter.cost_estimate()

    def tokens_used(self) -> int:
        return int(self.adapter.prompt_tokens) + int(self.adapter.completion_tokens)

    # ---------- one decision ----------
    def decide(self, ctx: DecisionContext) -> tuple[Decision, dict]:
        t0 = time.time()
        before = self.adapter.http_requests
        payload = ctx.model_payload(feedback_depth=self.feedback_depth)
        raw, meta = self.adapter.chat_json(
            BST_DECISION_SYSTEM, BST_DECISION_USER + json.dumps(payload, ensure_ascii=False),
            kind="text_decision", prompt_version=self.decision_prompt,
            max_tokens=self.max_tokens)
        meta = {**meta, **{"http_requests_this_call": self.adapter.http_requests - before,
                           "cumulative_http_requests": self.adapter.http_requests,
                           "round_index": ctx.round_index, "state_version": ctx.state_version,
                           "decision_seconds": round(time.time() - t0, 3)}}
        decision = self._to_decision(raw, ctx, meta)
        meta["action"] = decision.action
        meta["skill"] = decision.execute.skill if decision.execute else None
        meta["args"] = dict(decision.execute.args) if decision.execute else None
        self.returned_models.append(str(meta.get("returned_model")))
        self.rounds.append(meta)
        return decision, meta

    # same binder the desktop planner uses: envelope ids come from the context the
    # caller was handed, and a shape error reaches the Runtime unrepaired
    _to_decision = staticmethod(DeepSeekPlanner._to_decision)


def decision_from_payload(raw, ctx: DecisionContext, meta: dict) -> Decision:
    """Module-level form of the binder, for tests that have no adapter."""
    return AlfredTextPlanner._to_decision(raw, ctx, meta)
