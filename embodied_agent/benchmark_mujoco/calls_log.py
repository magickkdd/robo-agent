"""The one writer of `model_calls.jsonl` for this benchmark, shared by both request arms.

Two producers, one row shape. `model_policy.MujocoModelPolicy` files a row per decision
request and the camera files one per look and per survey (`perceive.survey`,
`perception.observe.VLMReader`), so the log a batch is *billed from* is the same document
whatever the request was about. That is the whole reason this module exists: the arithmetic
H-41 §C did for batch13 — decision rows plus look rows against the episode's `model_usage`
— had to be done by reading two different artifacts, because the second one had no rows.

The row is one *logical* request, written whether the answer came back or not; retries and
format repairs inside that request are filed as `http_requests_this_call`, not as extra
rows. See `file_call` for what that means for each key.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

#: How much of a schema-rejected answer is filed with the request that produced it.
#: 4096 chars is 5.3x the largest answered decision in the camera archive
#: (158 rows with `kind: decision` that carry an answer, over the 13 explicitly named
#: camera-programme roots: min 140, median 515.0, max 770 — counted in phase log H-32 §E)
#: and still a quarter of one decision prompt (`prompt_chars` 15,566 in batch8), so a
#: rejection row can never outweigh the page it was answered from. PR-114 in `docs/`, and
#: it bounds only the preview: the digest below always covers the whole answer.
REJECTED_RAW_PREVIEW_CHARS = 4096

#: The log's filename, so a reader of either arm and the writer agree on one string.
CALLS_LOG_NAME = "model_calls.jsonl"


def file_call(ep_dir: Optional[str], meta: dict[str, Any]) -> None:
    """Append one row for one opened request, retries included (SPEC 6.2).

    Refs and digests only: `chat_vision` keeps the frame path, byte count and sha256 in its
    metadata and never the base64, so what is written here cannot carry pixels.

    A row is written whether the answer came back or not: `ok` is explicit on both arms
    (`decide` files the failing one before it re-raises), because a log whose gaps are silent
    is a log whose totals are wrong. `http_requests_this_call` on an `ok: false` row is the
    attempts the adapter opened for the request that died, so the retries are attributable on
    that path too.

    The answer itself stays out of the log — `raw_chars` sizes it and nothing more, because a
    page is 15k chars and twenty rows of it would bury the ledger. The one exception is a row
    the schema rejected: that answer is the only record of what the model *meant* to do in a
    round whose decision never existed, and #114 turned its absence into a criterion-reading
    loss. So those three keys are filed, preview bounded, digest over the whole string so a
    truncated preview can still be proved against a later full-text copy.
    """
    if not ep_dir:
        return
    row = {k: v for k, v in meta.items() if k != "raw_response"}
    row.setdefault("ok", True)
    raw = str(meta.get("raw_response", ""))
    row["raw_chars"] = len(raw)
    if meta.get("schema_errors"):
        row["raw_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        row["raw_preview"] = raw[:REJECTED_RAW_PREVIEW_CHARS]
        row["raw_truncated"] = len(raw) > REJECTED_RAW_PREVIEW_CHARS
    with open(os.path.join(ep_dir, CALLS_LOG_NAME), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


__all__ = ["file_call", "REJECTED_RAW_PREVIEW_CHARS", "CALLS_LOG_NAME"]
