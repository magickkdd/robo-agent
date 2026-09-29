"""Contract: what a run's spend is, and the three ways this project has got it wrong.

Each of these is a real error that shipped, not a hypothetical:

1. Reading only the per-episode ledgers. The goal parses live in a run-level ledger because §7
   bills them once per `(case, repeat)`, shared across modes, before any episode directory
   exists — so a driver that walks `episodes/` cannot see them at all. It understated the E1
   batch by 356 requests, 10.8%.

2. Summing `http_requests_total`. That field is cumulative per episode. Summed across rows on
   the real E1 archive it gives 187,279 against a true 2,936 — 64x.

3. Dropping rows that ended in an error. 15 of E1's 262 goal parses exhausted five attempts on
   HTTP 429. They carry no usage, so **tokens** show no gap at all; but they made 75 requests
   and consumed the rate-limit quota that throttles this channel. Counting only token-bearing
   rows is right about money and wrong about pressure.

A fourth error — reporting one of the two request counts as "the" spend, and deriving the
hidden gap by subtracting the instrument's total instead of measuring both ledgers — is what
the v0.3 report did. It printed 281 where the measured gap is 356, because the instrument's
3,217 is itself a hybrid: per-episode requests *including* its errors, plus goal requests
*excluding* its own. `test_the_instruments_own_number_is_not_reproducible` pins that so the
next reader does not try to reconcile against it and conclude the ledgers are wrong.

The unit tests build their own ledgers and so run anywhere. The archive tests are skipped when
the batch is not present, which is the honest outcome: they are evidence about a real run, and
a synthetic fixture cannot stand in for it.
"""
from __future__ import annotations

import json
import os

import pytest

from embodied_agent.evaluation.run import batch_spend

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"
needs_archive = pytest.mark.skipif(not os.path.isdir(ARCHIVE),
                                   reason="the v0.3 E1 batch is not on this machine")


# --------------------------------------------------------------------- helpers ----
def _row(this_call=1, total=None, prompt=10, completion=2, error=None, latency=1.0):
    r = {"kind": "decision", "http_requests_this_call": this_call, "latency_s": latency,
         "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}
    if total is not None:
        r["http_requests_total"] = total
    if error:
        r["error"] = error
    return r


def _write(root, episodes, goal):
    os.makedirs(root, exist_ok=True)
    for ep, rows in episodes.items():
        d = os.path.join(root, "episodes", ep)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "model_calls.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    g = os.path.join(root, "goal_resolutions")
    os.makedirs(g, exist_ok=True)
    with open(os.path.join(g, "goal_calls.jsonl"), "w", encoding="utf-8") as f:
        for r in goal:
            f.write(json.dumps(r) + "\n")
    return root


# --------------------------------------------------------------- trap 1: two ledgers ----
def test_the_goal_ledger_is_not_under_episodes(tmp_path):
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row()]}, [_row()])
    assert not os.path.exists(os.path.join(root, "episodes", "ep.r0", "goal_resolutions"))
    s = batch_spend(root)
    assert s["per_episode_ledgers"]["http_requests"] == 1
    assert s["goal_parse_ledger"]["http_requests"] == 1
    assert s["http_requests"] == 2


def test_tokens_are_summed_from_both_ledgers(tmp_path):
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row(prompt=100, completion=10)]},
                  [_row(prompt=7, completion=1)])
    s = batch_spend(root)
    assert s["prompt_tokens"] == 107
    assert s["completion_tokens"] == 11


def test_tokens_are_read_from_usage_not_the_top_level(tmp_path):
    # the mistake this guards: reading `prompt_tokens` off the row, where it does not exist
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row(prompt=100, completion=10)]}, [])
    s = batch_spend(root)
    assert s["prompt_tokens"] == 100
    assert s["completion_tokens"] == 10


def test_a_batch_with_no_goal_ledger_still_reads(tmp_path):
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row()]}, [])
    s = batch_spend(root)
    assert s["http_requests"] == 1
    assert s["goal_parse_ledger"]["rows"] == 0


# ---------------------------------------------------- trap 2: the cumulative counter ----
def test_the_cumulative_counter_is_not_the_spend(tmp_path):
    # one episode, three rows, `http_requests_total` walking 1..3, one request each
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row(total=1), _row(total=2),
                                                      _row(total=3)]}, [])
    s = batch_spend(root)
    assert s["http_requests"] == 3
    # and the way to get it wrong is visible, not merely asserted against
    naive = sum(_row(total=t)["http_requests_total"] for t in (1, 2, 3))
    assert naive == 6


def test_a_missing_counter_does_not_inflate_the_total(tmp_path):
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row(), _row(), _row()]}, [])
    assert batch_spend(root)["http_requests"] == 3


# --------------------------------------------------------- trap 3: the errored rows ----
def test_an_errored_row_still_made_its_requests(tmp_path):
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row()]},
                  [_row(this_call=5, prompt=0, completion=0, error="HTTP Error 429")])
    s = batch_spend(root)
    assert s["http_requests"] == 6
    assert s["errored_http_requests"] == 5
    assert s["http_requests_successful"] == 1
    assert s["errored_rows"] == 1


def test_a_failed_request_contributes_no_tokens(tmp_path):
    # the reason this trap can hide: a 429 returns nothing, so the token columns agree with an
    # instrument that drops failed rows, and only the request count reveals the difference
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row(prompt=100, completion=10)]},
                  [_row(this_call=5, prompt=0, completion=0, error="HTTP Error 429")])
    s = batch_spend(root)
    assert s["prompt_tokens"] == 100
    assert s["completion_tokens"] == 10
    assert s["http_requests"] > s["http_requests_successful"]


def test_a_schema_error_counts_as_an_errored_row_too(tmp_path):
    # 17 of E1's 153 errored rows were DecisionSchemaError, not 429: a different failure with
    # the same accounting consequence, so the split must not be hard-wired to 429s
    root = _write(str(tmp_path / "batch"), {"ep.r0": [_row(error="DecisionSchemaError: bad")]}, [])
    s = batch_spend(root)
    assert s["errored_rows"] == 1
    assert s["errored_http_requests"] == 1
    assert s["http_requests_successful"] == 0


# ------------------------------------------------------------ the real E1 batch ----
def _archive_totals():
    batch_roots = sorted(dp for dp, dn, _fn in os.walk(ARCHIVE) if "episodes" in dn)
    keys = ("http_requests", "http_requests_successful", "errored_http_requests",
            "errored_rows", "prompt_tokens", "completion_tokens")
    tot = {k: 0 for k in keys}
    pe = {k: 0 for k in keys}
    gp = {k: 0 for k in keys}
    for r in batch_roots:
        s = batch_spend(r)
        for k in keys:
            tot[k] += s[k]
            pe[k] += s["per_episode_ledgers"].get(k, 0)
            gp[k] += s["goal_parse_ledger"].get(k, 0)
    return tot, pe, gp


@needs_archive
def test_the_real_batch_decomposes_into_its_two_ledgers():
    tot, pe, gp = _archive_totals()
    assert pe["http_requests"] == 2936
    assert gp["http_requests"] == 356
    assert tot["http_requests"] == 3292
    assert tot["http_requests_successful"] == 2585
    assert tot["errored_http_requests"] == 707
    assert tot["errored_rows"] == 153


@needs_archive
def test_the_real_batch_tokens_match_the_instrument_exactly():
    # exact agreement is the evidence that the two ledgers are the right *set* of rows
    tot, _pe, _gp = _archive_totals()
    assert tot["prompt_tokens"] == 11668006
    assert tot["completion_tokens"] == 209285


@needs_archive
def test_the_hidden_gap_is_measured_not_subtracted():
    tot, pe, _gp = _archive_totals()
    assert tot["http_requests"] - pe["http_requests"] == 356
    # the v0.3 report printed 281. Pinned so the subtraction is never taken for the measurement.
    assert tot["http_requests"] - pe["http_requests"] != 281


@needs_archive
def test_the_instruments_own_number_is_not_reproducible():
    tot, pe, gp = _archive_totals()
    hybrid = pe["http_requests"] + gp["http_requests_successful"]
    assert hybrid == 3217
    assert hybrid != tot["http_requests"]
    assert hybrid != tot["http_requests_successful"]


@needs_archive
def test_the_batch_stayed_under_its_bound_on_both_readings():
    tot, _pe, _gp = _archive_totals()
    assert tot["http_requests"] == 3292 < 3800
    assert tot["http_requests_successful"] == 2585 < 3800
