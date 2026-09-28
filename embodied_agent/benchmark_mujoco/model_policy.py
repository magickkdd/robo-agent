"""A model in the MuJoCo decision seat (SPEC-v0.2 §3.4, §6.1, §10).

The counterpart to `policy.py:MujocoPlanPolicy`. Same seat, same one-page-per-round
contract, one difference that matters: every round's action comes back from an HTTP
request instead of from a rule written here.

What is *not* in this module is the point. No fallback to the rule when the model is
slow, no retry that edits a payload toward validity, no default skill when the JSON is
thin — a decision source that quietly repairs a model's answer is the runtime choosing
the action (§10). The binding of envelope ids and the field-level rejection live in
`adapters/deepseek.py:DeepSeekPlanner._to_decision`, which is reused rather than
re-implemented so that "rejection reasons go to the model, unedited" means one thing on
every channel.

The provider is whatever `configs/models/*.yaml` names: the adapter class carries the
v0.1 name, and `provider`/`model`/`base_url` are read off the config, so the label this
module files is the endpoint it actually talked to.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..adapters.deepseek import DeepSeekPlanner
from ..core.contracts import Decision, DecisionContext
from .calls_log import REJECTED_RAW_PREVIEW_CHARS, file_call
from .prompts import PROMPT_VERSION, MW_DECISION_SYSTEM, MW_DECISION_USER, prompts_sha256


class MujocoModelPolicy:
    """One request per round; the answer is the round's action, unedited."""

    #: the ledger reads these off the source, and they are the spend being audited
    kind = "model"

    def __init__(self, adapter, *, ep_dir: Optional[str] = None, feedback_depth: int = 3,
                 max_tokens: Optional[int] = None):
        self.adapter = adapter
        self.provider = getattr(adapter, "provider", "unknown-provider")
        self.model = getattr(adapter, "model", "unknown-model")
        self.ep_dir = ep_dir
        self.feedback_depth = int(feedback_depth)
        self.max_tokens = max_tokens
        self.decisions = 0
        self.trace: list[dict[str, Any]] = []
        self.schema_rejections = 0
        self.transport_errors = 0
        self._bind = DeepSeekPlanner(adapter)

    # ---------- cumulative accounting (the loop charges deltas of these) ----------
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

    def _file_call(self, meta: dict[str, Any]) -> None:
        """One row for this round's request, in the shape the camera's rows use too.

        The writing itself is `calls_log.file_call`, shared with `perceive.survey` and
        `VLMReader`: `model_calls.jsonl` is what a batch is billed from, and a decision row
        and a look row that disagreed about `ok`, `raw_chars` or the rejected-answer preview
        would make that document two ledgers. Its docstring carries the rules.
        """
        file_call(self.ep_dir, meta)

    # ------------------------------------------------------------------ one round ----
    def decide(self, ctx: DecisionContext) -> Decision:
        payload = ctx.model_payload(feedback_depth=self.feedback_depth)
        user = MW_DECISION_USER + json.dumps(payload, ensure_ascii=False)
        # One adapter serves a whole sweep, so every counter on it is cumulative and a row
        # is only billable as a delta. This matters for retries in particular: a retried
        # attempt raises `http_requests` without raising the decision count, so a log row
        # that carries only the adapter's absolute totals cannot show where the extra
        # request went (`benchmark/source.py` files the same two fields for this reason).
        req_before = self.adapter.http_requests
        pin_before, pout_before = self.adapter.prompt_tokens, self.adapter.completion_tokens
        err_before, retry_before = self.adapter.api_errors, self.adapter.transport_retries
        try:
            raw, meta = self.adapter.chat_json(MW_DECISION_SYSTEM, user, kind="decision",
                                              prompt_version=PROMPT_VERSION,
                                              max_tokens=self.max_tokens)
        except Exception as e:  # noqa: BLE001 - the round fails, the request still happened
            self._file_call({
                "kind": "decision", "prompt_version": PROMPT_VERSION,
                "provider": self.provider, "model": self.model,
                "ok": False, "error": f"{type(e).__name__}: {e}",
                "requests_made": int(getattr(e, "requests_made", 0) or 0),
                "http_requests_this_call": self.adapter.http_requests - req_before,
                "api_errors_this_call": self.adapter.api_errors - err_before,
                "transport_retries_this_call": self.adapter.transport_retries - retry_before,
                "prompt_tokens_this_call": self.adapter.prompt_tokens - pin_before,
                "completion_tokens_this_call": self.adapter.completion_tokens - pout_before})
            raise
        meta = {**meta,
                "http_requests_this_call": self.adapter.http_requests - req_before,
                "cumulative_http_requests": self.adapter.http_requests,
                "prompt_tokens_this_call": self.adapter.prompt_tokens - pin_before,
                "completion_tokens_this_call": self.adapter.completion_tokens - pout_before}
        try:
            decision = self._bind._to_decision(raw, ctx, meta)
        finally:
            # Filed after the binding because `_to_decision` writes its rejection reasons
            # into this same dict — before it, a rejected round left a row that said only
            # "something came back and was 589 chars". `finally` rather than an `except`
            # arm keeps the invariant this module was written with: one row per logical
            # request, on the path that returns and the path that raises alike.
            self._file_call(meta)

        ex = decision.execute
        self.decisions += 1
        self.trace.append({"round_index": payload["round_index"],
                           "state_version": payload["state_version"],
                           "views": [k for k in ("plan", "working_memory") if k in payload],
                           "action": decision.action,
                           "skill": ex.skill if ex else None,
                           "args": dict(ex.args or {}) if ex else {},
                           "row": (ex.subgoal if ex else None),
                           "steps_left": int(payload.get("budget", {}).get("env_steps_total", 0))
                                         - int(payload.get("budget", {}).get("env_steps_used", 0)),
                           "rationale": decision.rationale,
                           "http_requests": self.adapter.http_requests})
        return decision


__all__ = ["MujocoModelPolicy", "prompts_sha256", "REJECTED_RAW_PREVIEW_CHARS"]
