"""SPEC-BST 10.4/13.9: recompute every reported number from the sealed logs.

The claim this module exists to support is narrow and checkable: `metrics.json` is
derived from `episodes/*/events.jsonl`, `model_calls.jsonl` and `evaluation.jsonl`,
and nothing in it needs the model, the environment or `progress.json`. Reading the
scheduler's ledger is allowed only as a *cross-check*, and a difference between the
two is reported as `recompute_mismatch` rather than resolved by picking whichever
was easier — a metric that can only be produced while the run is live is not a
metric a reader can verify.

Denominators follow SPEC-BST 8.1 (planned / started / benchmark-valid /
infrastructure-failed / not-run / replaced attempts), so a coverage problem can
never be read as a capability result. Partial completion and constraint violation
report `not_supported` because the text backend has no third-party signal for
either (SPEC-BST 4.6, 8.2); the alternative — a model-written or reward-derived
"percentage" — is the thing the spec forbids.

Run:
  /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.cli aggregate \\
      --run-root /tmp/bst_v01/bst1
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
import time
from collections import Counter, defaultdict

from .native_actions import render

BST_SPEC = "BST-1.0"
SCHEMA_VERSION = "1"
# SPEC-BST 8.4: the statistics of the full batch are pre-declared, including the seed.
BOOTSTRAP_DRAWS = 2_000
BOOTSTRAP_SEED = "20260919"


# ------------------------------------------------------------------ readers ----


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _num(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _first_unredacted(*candidates):
    """The first candidate the credential redactor did not eat.

    `_redact` truncates any string containing `sk-` (a key-prefix heuristic), and ALFWorld
    task ids contain it routinely — `pick_and_place_simple-Mug-None-Desk-308/trial_…`
    carries `sk-` inside `Desk`. Six episodes in the full batch had `episode_start.task_id`
    and `episode_end.result.task_id` truncated to 12 characters, and grouping by that field
    collapsed three different trials into one cluster of six, which silently changed the
    per-task bootstrap. `episode_summary.json` is written by another path and survives, so
    this is a precedence fix over fields already on disk — it repairs no log and re-measures
    nothing the episodes did not already say."""
    for candidate in candidates:
        if candidate and "redacted" not in str(candidate):
            return candidate
    return next((c for c in candidates if c), None)


def episode_dirs(run_root: str) -> list[str]:
    root = os.path.join(run_root, "episodes")
    return sorted(os.path.join(root, d) for d in os.listdir(root)) if os.path.isdir(root) else []


# ------------------------------------------------------- one episode, rebuilt --


def recompute_episode(ep_dir: str) -> dict:
    """Everything measurable about one episode, read out of its own log files."""
    events = _read_jsonl(os.path.join(ep_dir, "events.jsonl"))
    calls = _read_jsonl(os.path.join(ep_dir, "model_calls.jsonl"))
    evals = [e for e in _read_jsonl(os.path.join(ep_dir, "evaluation.jsonl"))
             if "scoreable" not in e or e.get("scoreable")]
    start = next((e["payload"] for e in events if e["type"] == "episode_start"), {})
    end = next((e["payload"] for e in events if e["type"] == "episode_end"), {})
    term = next((e["payload"] for e in events if e["type"] == "termination"), {})
    result = end.get("result") or {}
    evaluation = evals[-1] if evals else {}

    decisions = [e["payload"] for e in events if e["type"] == "decision"]
    skill_calls = [e["payload"]["call"] for e in events if e["type"] == "skill_call"]
    feedback = [e["payload"]["feedback"] for e in events if e["type"] == "execution_feedback"]
    cached_reads = sum(1 for e in events if e["type"] == "observation_cached")

    # the command each skill call actually sent: `render` is the same public,
    # deterministic mapping the executor used, so an exact-command comparison is
    # possible offline without storing anything the model was not already shown
    commands = [render(c.get("skill"), dict(c.get("args") or {}))[0] for c in skill_calls]

    rejected = [f for f in feedback if f.get("status") == "rejected"]
    schema_invalid = sum(1 for f in rejected
                         if any("not permitted" in r or r.startswith("action:")
                                or "field required" in r for r in (f.get("rejection_reasons") or [])))
    no_effect = sum(1 for f in feedback if f.get("status") == "completed"
                    and "Nothing happens" in str(f.get("environment_text") or ""))
    failed = sum(1 for f in feedback if f.get("status") == "failed")
    model_errors = sum(1 for f in feedback if f.get("status") == "model_error")

    # longest run of one exact command whose own feedback said it did nothing
    longest_stuck = run = 0
    prev = None
    for cmd, fb in zip(commands, [f for f in feedback if f.get("executed")]):
        stuck = (cmd == prev and fb.get("status") == "completed"
                 and "Nothing happens" in str(fb.get("environment_text") or ""))
        run = run + 1 if stuck and prev is not None else (1 if cmd == prev else 0)
        longest_stuck = max(longest_stuck, run)
        prev = cmd

    tokens = {"prompt": sum(_num((c.get("usage") or {}).get("prompt_tokens")) for c in calls),
              "completion": sum(_num((c.get("usage") or {}).get("completion_tokens")) for c in calls)}
    # a call that was billed but logged without its `usage` is the one reason a
    # recomputed sum can sit below the sealed number: the provider counter moved,
    # the row says nothing about how far. Named here so the difference is a
    # diagnosis rather than an unexplained gap.
    calls_without_usage = sum(1 for c in calls if not c.get("usage")
                              and _num(c.get("http_requests_this_call")))
    # what a payload that never became a decision cost. SPEC-BST 5.1 bills the round
    # either way, so this is money spent and information not used: the share of a batch
    # it accounts for is the cheapest bottleneck to find and the most expensive to ignore
    errored = [c for c in calls if c.get("error")]
    refused_round_tokens = sum(_num((c.get("usage") or {}).get("prompt_tokens"))
                               + _num((c.get("usage") or {}).get("completion_tokens"))
                               for c in errored)
    refused_round_kinds = dict(Counter(str(c.get("error")).split(":")[0] for c in errored))
    summary = {}
    sp = os.path.join(ep_dir, "episode_summary.json")
    if os.path.isfile(sp):
        with open(sp, encoding="utf-8") as f:
            summary = json.load(f)

    mismatch = {}
    st = (summary.get("tokens") or {})
    for key, live in (("prompt", tokens["prompt"]), ("completion", tokens["completion"])):
        if st and _num(st.get(key)) != live:
            mismatch[f"tokens_{key}"] = {"sealed": _num(st.get(key)), "recomputed": live}
    if mismatch and calls_without_usage:
        mismatch["billed_calls_without_usage"] = calls_without_usage
        mismatch["reading"] = ("a row counted in the sealed figure logged no `usage`, so the "
                               "recomputed sum is a lower bound; see `token_authority`")
    if summary.get("evaluation") and result.get("termination_reason") \
            and summary["evaluation"].get("termination_reason") != result["termination_reason"]:
        mismatch["termination_reason"] = {"sealed": summary["evaluation"].get("termination_reason"),
                                          "recomputed": result["termination_reason"]}

    wall = [e.get("wall_time") for e in events if isinstance(e.get("wall_time"), float)]
    first_deviation = None
    for e in events:
        if e["type"] == "execution_feedback" and (e["payload"].get("feedback") or {}).get(
                "status") in ("rejected", "model_error"):
            first_deviation = e["payload"]["feedback"].get("context_id")
            break
    return {
        "episode_id": summary.get("episode_id") or os.path.basename(ep_dir),
        "task_id": _first_unredacted(start.get("task_id"), summary.get("task_id"),
                                     evaluation.get("task_id")),
        "task_type": summary.get("task_type") or evaluation.get("task_type"),
        "repeat": summary.get("repeat", evaluation.get("repeat")),
        "slot_index": summary.get("slot_index"),
        "replacement_of": summary.get("replacement_of"),
        "outcome": summary.get("outcome") or "ran",
        "started": bool(events),
        "benchmark_valid": bool(evaluation.get("termination_reason")),
        "official_won": evaluation.get("official_won"),
        "env_done": evaluation.get("env_done"),
        "env_steps": _num(evaluation.get("env_steps")),
        "termination_reason": result.get("termination_reason") or evaluation.get("termination_reason"),
        "failure_type": result.get("failure_type"),
        "false_finish": evaluation.get("false_finish"),
        "decision_rounds": _num(result.get("decision_rounds")),
        "decisions_by_action": dict(Counter(d.get("action") for d in decisions)),
        "rejected_decisions": len(rejected),
        "rejection_reasons": dict(Counter(r for f in rejected
                                         for r in (f.get("rejection_reasons") or []))),
        "schema_invalid": schema_invalid,
        # A proposal `render` refused never becomes a `skill_call` event, so a numerator
        # built from those events is structurally blind to it: the count has to come from
        # the rejection reason. Without this the batch publishes "0 of N proposals were
        # syntactically invalid" while ten of them carry a binder refusal.
        "binder_refusals": sum(1 for f in rejected
                               if any("does not map to one environment command" in r
                                      for r in (f.get("rejection_reasons") or []))),
        # A round whose payload survived the schema and was then refused by the binder
        # appears in `decisions_by_action` *and* in `rejected_decisions`, so the round is
        # counted twice. Naming the overlap turns `episodes_where_identity_fails` from an
        # alarm into a fact: the gap is these rounds, not rounds that were billed and
        # never recorded.
        "rounds_counted_twice": sum(1 for f in rejected if f.get("decision_id")),
        "model_errors": model_errors,
        "semantic_repairs": _num(result.get("semantic_repairs")),
        "identical_repeats_total": _num(result.get("identical_repeats_total")),
        "longest_stuck_repeat": longest_stuck,
        "commands": commands,
        "distinct_commands": len({c for c in commands if c}),
        "env_rejections": failed,
        "env_no_effect": no_effect,
        "commands_issued": len([c for c in commands if c]),
        "cached_observation_reads": cached_reads,
        "http_requests": _num(result.get("http_requests")) or
                         sum(_num(c.get("http_requests_this_call")) for c in calls),
        "tokens": {**tokens, "total": tokens["prompt"] + tokens["completion"]},
        "sealed_tokens": {"prompt": _num(st.get("prompt")),
                          "completion": _num(st.get("completion")),
                          "total": _num(st.get("total")),
                          "provider_cumulative_total": st.get("provider_cumulative_total")},
        # what every cost statistic is computed from. The sealed total is the delta of
        # the provider's own counters — what the runtime billed and what the batch cap
        # charges — and it is corroborated offline by `provider_cumulative_series`. The
        # row-derived sum above is a recomputation of it, and a recomputation that is
        # missing a row must not quietly become the measured cost.
        "cost_tokens": ({"prompt": _num(st.get("prompt")),
                         "completion": _num(st.get("completion")),
                         "total": _num(st.get("total")),
                         "source": "sealed episode ledger (provider counter delta)"}
                        if st else
                        {"prompt": tokens["prompt"], "completion": tokens["completion"],
                         "total": tokens["prompt"] + tokens["completion"],
                         "source": "model_calls rows: no sealed summary to prefer"}),
        "billed_calls_without_usage": calls_without_usage,
        "refused_round_tokens": refused_round_tokens,
        "refused_round_kinds": refused_round_kinds,
        # §5.5: the guard halts on a name that is not the configured one. It cannot
        # halt on the absence of a name, so the absence is counted here instead —
        # silence must be visible in the report, not merely tolerated by the guard.
        "configured_model": (summary.get("model_identity") or {}).get("model"),
        "returned_models": (summary.get("model_identity") or {}).get("returned_models") or [],
        "provider_counters": result.get("provider_counters") or {},
        "model_wait_s": round(sum(float(c.get("latency_s") or 0) for c in calls), 2),
        "wall_log_span_s": round(max(wall) - min(wall), 2) if len(wall) > 1 else None,
        "requests_recorded": len(calls),
        "first_deviation_context": first_deviation,
        "instruction": summary.get("instruction"),
        "infrastructure_error": summary.get("infrastructure_error"),
        "recompute_mismatch": mismatch,
    }


# ------------------------------------------------------------- the statistics --


def _series_check(episodes: list) -> dict:
    """Recompute each episode's token spend a second way: the provider's cumulative
    counter is sealed into every summary, so consecutive differences give the same
    number the runtime billed, derived from the log alone.

    This is the check that survives an error row with no `usage` in it — it counts
    the whole process, not the rows. It needs the batch to have run in one process
    and every slot to appear exactly once; either way it says so instead of
    printing a number it cannot support (SPEC-BST 13-9)."""
    if any(e["slot_index"] is None for e in episodes) or \
            len({e["slot_index"] for e in episodes}) != len(episodes):
        return {"available": False,
                "why": "a slot index is missing or repeated, so the run order cannot be "
                       "rebuilt from the summaries; the series is not computed"}
    ordered = sorted(episodes, key=lambda e: e["slot_index"])
    missing = [e["episode_id"] for e in ordered
               if not isinstance(e["sealed_tokens"]["provider_cumulative_total"], int)]
    if missing:
        return {"available": False,
                "why": "these episodes sealed no provider cumulative counter (an "
                       "infrastructure row records its own delta, not the process "
                       "counter)",
                "episodes": missing}
    rows, prev, agree = [], 0, 0
    for e in ordered:
        cum = e["sealed_tokens"]["provider_cumulative_total"]
        delta = cum - prev
        prev = cum
        same = delta == e["sealed_tokens"]["total"]
        agree += same
        rows.append({"slot_index": e["slot_index"], "episode_id": e["episode_id"],
                     "sealed_total": e["sealed_tokens"]["total"],
                     "series_delta": delta, "agrees": same})
    return {"available": True, "episodes_agreeing": agree, "episodes": len(ordered),
            "process_counter_at_batch_end": prev,
            "per_episode": rows,
            "assumes": "one process for the whole batch, with the provider counter "
                       "starting at 0. A resumed batch shows up as the first row "
                       "disagreeing by exactly the earlier spend; it is reported, not "
                       "absorbed",
            "note": "a disagreement here is a real accounting defect: the same provider "
                    "counter is what the runtime billed and what the cap is charged"}


def _pct(xs: list, q: float) -> float | None:
    """Nearest-rank percentile; `xs` need not be sorted. Returns None for an empty
    list rather than 0, because 0 is a measurement and None is the absence of one."""
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def _stats(xs: list) -> dict:
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 2),
            "median": round(statistics.median(xs), 2), "p95": round(_pct(xs, 0.95), 2),
            "min": round(min(xs), 2), "max": round(max(xs), 2), "sum": round(sum(xs), 2)}


def _rate(num: int, den: int) -> dict:
    return {"numerator": num, "denominator": den,
            "value": round(num / den, 4) if den else None,
            "note": None if den else "empty denominator: no rate is reported"}


def _merge_counters(counters) -> dict:
    """Additive merge of per-episode reason counters, largest first. Additive because a
    reason is a count, not a mean: averaging them would hide that one behaviour accounts
    for most of the batch."""
    total: Counter = Counter()
    for c in counters:
        total.update(c or {})
    return dict(total.most_common())


def _cluster_bootstrap(per_task: list[tuple[str, list[int]]]) -> dict:
    """SPEC-BST 8.4: average each task's repeats first, cluster by task_id, 2,000
    draws, seed 20260919, 95% interval. The task, not the episode, is the unit."""
    if not per_task:
        return {"applicable": False, "note": "no task with a recorded verdict"}
    means = [statistics.fmean(w) for _, w in per_task]
    if len(per_task) < 2:
        return {"applicable": False, "n_clusters": len(per_task),
                "point_estimate": round(statistics.fmean(means), 4),
                "note": "fewer than two tasks: resampling one cluster cannot bound "
                        "anything, so no interval is claimed"}
    keys = sorted({t for t, _ in per_task})
    draws = []
    state = int(hashlib.sha256(f"{BOOTSTRAP_SEED}|cluster".encode()).hexdigest(), 16)
    for _ in range(BOOTSTRAP_DRAWS):
        total, n = 0.0, 0
        for _k in keys:
            state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
            total += means[state % len(means)]
            n += 1
        draws.append(total / n)
    draws.sort()
    return {"applicable": True, "n_clusters": len(per_task),
            "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED,
            "point_estimate": round(statistics.fmean(means), 4),
            "ci95": [round(draws[int(0.025 * len(draws))], 4),
                     round(draws[min(len(draws) - 1, int(0.975 * len(draws)))], 4)],
            # spelled out of the argument, not of the batch this note was first written
            # for: the screening metrics carried "268 episodes of 134 tasks" while the
            # segment it described had 48 of 24
            "note": f"the interval is over tasks, not episodes: {sum(len(o) for _, o in per_task)} "
                    f"episodes of {len(per_task)} tasks are not {sum(len(o) for _, o in per_task)} "
                    "independent samples (SPEC-BST 8.4)"}


def _type_block(rows: list[dict]) -> dict:
    wins = [r for r in rows if r.get("official_won")]
    valid = [r for r in rows if r["benchmark_valid"]]
    per_task: dict[str, list[int]] = defaultdict(list)
    for r in valid:
        per_task[r["task_id"]].append(1 if r.get("official_won") else 0)
    both = [t for t, w in per_task.items() if len(w) > 1]
    return {
        "episodes": len(rows), "benchmark_valid": len(valid),
        "official_successes": len(wins),
        "task_success_rate": _rate(len(wins), len(valid)),
        "repeats": {"tasks_with_two_verdicts": len(both),
                    "both_success": sum(1 for t in both if all(per_task[t])),
                    "both_failure": sum(1 for t in both if not any(per_task[t])),
                    "disagree": sum(1 for t in both if 0 < sum(per_task[t]) < len(per_task[t]))},
        "termination_reasons": dict(Counter(str(r.get("termination_reason")) for r in rows)),
        "efficiency": {k: _stats([r[k] for r in rows]) for k in
                       ("env_steps", "decision_rounds", "http_requests", "commands_issued",
                        "rejected_decisions", "env_rejections", "env_no_effect",
                        "distinct_commands", "longest_stuck_repeat", "cached_observation_reads")},
        "tokens": {"prompt": _stats([r["cost_tokens"]["prompt"] for r in rows]),
                   "completion": _stats([r["cost_tokens"]["completion"] for r in rows]),
                   "total": _stats([r["cost_tokens"]["total"] for r in rows]),
                   "basis": "sealed per-episode provider counter delta; the row-derived "
                            "sum is kept in each episode as `tokens` and differs only "
                            "where a billed request logged no `usage`"},
        "wall_clock_s": _stats([r["wall_log_span_s"] for r in rows]),
        "model_wait_s": _stats([r["model_wait_s"] for r in rows]),
        "success_vs_failure_cost": {
            "succeeded_total_tokens": _stats([r["cost_tokens"]["total"] for r in rows
                                              if r.get("official_won")]),
            "failed_total_tokens": _stats([r["cost_tokens"]["total"] for r in rows
                                           if r["benchmark_valid"] and not r.get("official_won")]),
            "note": "reported apart on purpose: the cost of the tasks that were solved "
                    "is not the cost of the batch (SPEC-BST 8.2)"},
        "cluster_bootstrap_success": _cluster_bootstrap([(t, w) for t, w in per_task.items()]),
    }


# ------------------------------------------------------------------ the run ----


def aggregate_run_root(run_root: str, task_manifest: str | None = None) -> dict:
    episodes = [recompute_episode(d) for d in episode_dirs(run_root)]
    progress = {}
    pp = os.path.join(run_root, "progress.json")
    if os.path.isfile(pp):
        with open(pp, encoding="utf-8") as f:
            progress = json.load(f)

    planned = None
    # the run root carries its own copy of the manifest (§10.4), so an aggregation
    # that was not told the path can still answer "how many slots were planned"
    if not task_manifest:
        task_manifest = os.path.join(run_root, "task_manifest.json")
    if task_manifest and os.path.isfile(task_manifest):
        with open(task_manifest, encoding="utf-8") as f:
            man = json.load(f)
        segment = progress.get("segment")
        chain = {"dev_train": ("dev_train", "slots"),
                 "screening": ("order", "screening_slots"),
                 "full": None}.get(segment)
        if chain:
            planned = len(man[chain[0]][chain[1]])
        elif segment == "full":
            planned = len(man["order"]["screening_slots"]) + len(man["order"]["remainder_slots"])

    valid = [e for e in episodes if e["benchmark_valid"]]
    infra = [e for e in episodes if e["outcome"] == "infrastructure_error"]
    started = [e for e in episodes if e["started"]]
    wins = [e for e in valid if e.get("official_won")]
    slots = {e["slot_index"] for e in episodes if e.get("slot_index") is not None}
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in episodes:
        by_type[str(e.get("task_type"))].append(e)
    blocks = {t: _type_block(rows) for t, rows in sorted(by_type.items())}
    macro = [b["task_success_rate"]["value"] for b in blocks.values()
             if b["task_success_rate"]["value"] is not None]

    refused_tokens = sum(e["refused_round_tokens"] for e in valid)
    batch_tokens = sum(e["cost_tokens"]["total"] for e in valid)
    metrics = {
        "bst_spec": BST_SPEC, "schema_version": SCHEMA_VERSION,
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "run_root": os.path.abspath(run_root),
        "recomputed_from": ["episodes/*/events.jsonl", "episodes/*/model_calls.jsonl",
                            "episodes/*/evaluation.jsonl"],
        "progress_ledger": "read for the planned-slot count and the cross-check only; no "
                           "metric above is taken from it (SPEC-BST 13.9)",
        "denominators": {
            "planned_slots": planned,
            "episode_directories": len(episodes),
            "started": len(started),
            "benchmark_valid": len(valid),
            "infrastructure_failed": len(infra),
            "replaced_attempts": sum(1 for e in episodes if e.get("replacement_of") is not None),
            "not_run_slots": (planned - len(slots)) if planned is not None else None,
            "slot_index_present": len(slots),
        },
        "headline": {
            "task_success_rate": {**_rate(len(wins), len(valid)),
                                  "definition": "official successes / benchmark-valid "
                                                "episodes; model errors, budget exhaustion, "
                                                "blocked and clarify all stay in this "
                                                "denominator (SPEC-BST 8.1)"},
            "running_reliability": _rate(len(valid), len(episodes)),
            "conservative_delivery_rate": {
                **_rate(len(wins), len(started)),
                "definition": "successes / started planned slots; an unrecoverable "
                              "infrastructure failure is an undelivered success here, "
                              "never an agent reasoning failure (SPEC-BST 8.1)"},
            "coverage": _rate(len(slots), planned) if planned else {"value": None,
                                                                   "note": "planned unknown"},
        },
        "termination": {
            "reasons": dict(Counter(str(e.get("termination_reason")) for e in episodes)),
            "false_finish": sum(1 for e in episodes if e.get("false_finish")),
            "env_done_without_agent_finish": sum(
                1 for e in episodes if e.get("env_done") and
                e.get("termination_reason") != "AGENT_FINISH"),
            "agent_finish_without_env_done": sum(
                1 for e in episodes if not e.get("env_done") and
                e.get("termination_reason") == "AGENT_FINISH"),
            "note": "the environment's own termination and the agent's stop are counted "
                    "separately and never merged (SPEC-BST 6.2, 8.2)"},
        "repetition": {
            "episodes_with_any_exact_repeat": sum(
                1 for e in valid if e["commands_issued"] > e["distinct_commands"]),
            "episodes_with_stuck_repeat_ge3": sum(1 for e in valid if e["longest_stuck_repeat"] >= 3),
            "max_stuck_repeat": max((e["longest_stuck_repeat"] for e in valid), default=0),
            "definition": "identical rendered command *and* arguments; counted as a loop "
                          "only where the public feedback said the command did nothing, "
                          "and a human still has to confirm there was no relevant new "
                          "information (SPEC-BST 8.3)"},
        "invalid_action": {
            "schema_invalid_per_decision": _rate(sum(e["schema_invalid"] for e in valid),
                                                 sum(e["decision_rounds"] for e in valid)),
            "syntax_invalid_per_execute_proposal": _rate(
                sum(e["binder_refusals"] for e in valid),
                sum(len(e["commands"]) + e["binder_refusals"] for e in valid)),
            "env_rejected_per_command": _rate(sum(e["env_rejections"] for e in valid),
                                              sum(e["commands_issued"] for e in valid)),
            "env_no_effect_per_command": _rate(sum(e["env_no_effect"] for e in valid),
                                               sum(e["commands_issued"] for e in valid)),
            "rounds_decomposition": {
                "decision_rounds": sum(e["decision_rounds"] for e in valid),
                "accepted_decisions": sum(sum(e["decisions_by_action"].values())
                                          for e in valid),
                "refused_before_execution": sum(e["rejected_decisions"] for e in valid),
                "model_errors": sum(e["model_errors"] for e in valid),
                # checked episode by episode: a batch total that happens to add up could
                # hide two episodes that cancel, and this identity is what a reader uses
                # to see whether a round was billed but never recorded
                "episodes_where_identity_fails": [
                    e["episode_id"] for e in valid
                    if sum(e["decisions_by_action"].values()) + e["rejected_decisions"]
                    + e["model_errors"] != e["decision_rounds"]],
                "rounds_counted_twice": sum(e["rounds_counted_twice"] for e in valid),
                "identity_note": "where an episode fails the identity, the gap is a round "
                    "counted twice, not a round billed and never recorded: the payload "
                    "survived the schema (so it is a `decision` event) and was then refused "
                    "by the binder (so it is also a rejected feedback). "
                    "`rounds_counted_twice` is that set, and it equals the number of "
                    "episodes where the identity fails only when every gap is exactly 1, "
                    "which is checked per batch rather than assumed"},
            "denominator_definitions": {
                "schema_invalid_per_decision": "rounds billed by SPEC-BST 5.1 (a refused "
                    "payload is still a decision round), so the refused rounds are in the "
                    "denominator: accepted + refused + model_error must equal decision_rounds",
                "syntax_invalid_per_execute_proposal": "proposals that reached the binder = "
                    "the `skill_call` events plus the proposals it refused; the numerator is "
                    "the refusals. A binder refusal writes no `skill_call`, so counting "
                    "skill calls and subtracting the executed ones — an earlier version of "
                    "this line — is structurally 0 however many refusals happened",
                "env_rejected_per_command / env_no_effect_per_command": "commands the engine "
                    "actually stepped on"},
            "refusal_reasons": _merge_counters(e["rejection_reasons"] for e in valid),
            "tokens_spent_on_refused_rounds": {
                "tokens": refused_tokens,
                "batch_tokens": batch_tokens,
                "share": round(refused_tokens / batch_tokens, 4) if batch_tokens else None,
                "meaning": "tokens billed for a round that produced no decision at all "
                           "(SPEC-BST 5.1 bills it either way). this is not a savings "
                           "estimate: it is what the batch spent on payloads the frozen "
                           "schema refused"},
            "refused_round_kinds": _merge_counters(e["refused_round_kinds"] for e in valid),
            "episodes_touched_by_a_refused_round": sum(1 for e in valid
                                                       if e["refused_round_tokens"]),
            "note": "the three are reported apart: an unrepaired schema slip, a command "
                    "the adapter refused to build and a command the engine answered "
                    "'Nothing happens.' are different events (SPEC-BST 8.2). a refused "
                    "payload never became an execute proposal, so it is not subtracted "
                    "from the proposal count anywhere: the two denominators are counted "
                    "from different event types"},
        "not_supported": {
            "partial_completion": "no third-party per-condition signal exists for a text "
                                  "instruction (SPEC-BST 4.6); a binary reward, a model "
                                  "claim or an action count is not a progress percentage",
            "constraint_violation": "no independent constraint signal in this backend; "
                                    "precondition rejections are counted separately as "
                                    "adapter/environment events, not as final violations",
            "progress_retention": "requires the per-condition evidence a human reads; "
                                  "candidate rows are emitted in failure_candidates.jsonl",
            "replan_events": "the program never produced a plan to revise; the label is "
                             "'action adjustment', not 'replan count' (SPEC-BST 8.2)"},
        "overall": _type_block(episodes),
        "by_task_type": blocks,
        "macro_vs_micro": {
            "micro_success_rate": _rate(len(wins), len(valid))["value"],
            "macro_success_rate": round(statistics.fmean(macro), 4) if macro else None,
            "macro_note": "the average of the six type rates, so a type with 4 tasks "
                          "weighs as much as one with 60; both are reported, neither is "
                          "a difficulty ordering (SPEC-BST 8.4, 10.5)"},
        "infrastructure": {
            "episodes": [{"episode_id": e["episode_id"], "task_id": e["task_id"],
                          "error": e.get("infrastructure_error")} for e in infra],
            "tokens_spent_on_failed_attempts": sum(e["cost_tokens"]["total"] for e in infra),
            "note": "kept in the report, not deleted: an infrastructure failure lowers "
                    "coverage, and coverage is reported apart from success "
                    "(SPEC-BST 8.1, 9.2 INFRASTRUCTURE)"},
        "recompute_mismatch": [{"episode_id": e["episode_id"], **e["recompute_mismatch"]}
                               for e in episodes if e["recompute_mismatch"]],
        "cross_check_against_ledger": {
            "ledger_slots_recorded": len(progress.get("slots") or []),
            "ledger_tokens_used": progress.get("tokens_used"),
            "recomputed_tokens_total": sum(e["tokens"]["total"] for e in episodes),
            "sealed_tokens_total": sum(e["sealed_tokens"]["total"] for e in episodes),
            "ledger_official_won": sum(1 for r in (progress.get("slots") or [])
                                       if r.get("official_won")),
            "recomputed_official_won": len(wins),
            "provider_cumulative_series": _series_check(episodes)},
        "model_identity": {
            "configured": sorted({e["configured_model"] for e in episodes if e["configured_model"]}),
            "returned_names": sorted({m for e in episodes for m in e["returned_models"]}),
            "episodes_with_no_returned_name": [e["episode_id"] for e in episodes
                                               if not e["returned_models"]],
            "note": "the runner halts the batch the moment a response carries a name that is "
                    "not the configured one (§5.5); an episode that made no successful "
                    "request carries no name at all, and is listed here rather than passed "
                    "as a match"},
        "token_authority": {
            "used_for_the_batch_cap": "the sealed per-episode total, i.e. the delta of the "
                                      "provider's own cumulative counters across the episode",
            "why": "the runtime bills what the provider reported, and the same number is "
                   "independently corroborated offline by differencing each episode's "
                   "`provider_cumulative_total` against the previous one — no model call and "
                   "no environment run is needed to check it (SPEC-BST 10.4, 13-9)",
            "how_to_read_a_tokens_mismatch": "recomputed < sealed means some requests were "
                                             "billed and logged without their `usage`: a "
                                             "rejected payload still spent the round. Count "
                                             "the episode's `billed_calls_without_usage`. "
                                             "recomputed > sealed has no accepted explanation "
                                             "and is treated as a defect, not a rounding "
                                             "difference",
            "diagnostic_for_this_run": {
                "episodes_where_recompute_is_a_lower_bound":
                    [e["episode_id"] for e in episodes if e["billed_calls_without_usage"]],
                "note": "an error-path row written before `usage` was attached to it; the "
                        "sealed figure it disagrees with is the one carried forward into "
                        "the cap, and the disagreement is left visible rather than "
                        "quietly reconciled"},
        },
    }
    os.makedirs(run_root, exist_ok=True)
    with open(os.path.join(run_root, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2, default=str)
    _write_type_csv(run_root, metrics, by_type)
    _write_failure_candidates(run_root, episodes)
    return {"metrics_path": os.path.join(run_root, "metrics.json"),
            "episodes": len(episodes), "benchmark_valid": len(valid),
            "official_won": len(wins),
            "recompute_mismatch": len(metrics["recompute_mismatch"]),
            "failure_candidates": sum(1 for e in episodes
                                      if e["benchmark_valid"] and not e.get("official_won"))}


def _write_type_csv(run_root: str, metrics: dict, by_type: dict) -> None:
    path = os.path.join(run_root, "metrics_by_task_type.csv")
    blocks = metrics.get("by_task_type") or {}
    cols = ("task_type", "episodes", "benchmark_valid", "official_successes",
            "task_success_rate", "mean_env_steps", "mean_decision_rounds",
            "mean_http_requests", "mean_total_tokens", "median_total_tokens",
            "p95_total_tokens", "false_finish", "schema_invalid",
            "episodes_with_stuck_repeat_ge3", "ci95_low", "ci95_high")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for t, b in sorted(blocks.items()):
            rows = by_type.get(t) or []
            eff, tok = b["efficiency"], b["tokens"]["total"]
            ci = (b["cluster_bootstrap_success"] or {}).get("ci95") or [None, None]
            w.writerow([t, b["episodes"], b["benchmark_valid"], b["official_successes"],
                        b["task_success_rate"]["value"],
                        eff["env_steps"].get("mean"), eff["decision_rounds"].get("mean"),
                        eff["http_requests"].get("mean"), tok.get("mean"), tok.get("median"),
                        tok.get("p95"),
                        sum(1 for e in rows if e.get("false_finish")),
                        sum(e["schema_invalid"] for e in rows),
                        sum(1 for e in rows if e["longest_stuck_repeat"] >= 3),
                        ci[0], ci[1]])


def _write_failure_candidates(run_root: str, episodes: list[dict]) -> None:
    """Every benchmark-valid episode that did not succeed, and every attempt that never
    produced a verdict. The selection rule is declared here and not adjusted afterwards
    to make a nicer sample (SPEC-BST 8.4)."""
    path = os.path.join(run_root, "failure_candidates.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for e in episodes:
            if e.get("official_won"):
                continue
            row = {
                "episode_id": e["episode_id"], "task_id": e["task_id"],
                "task_type": e["task_type"], "repeat": e["repeat"],
                "slot_index": e["slot_index"],
                "selection_rule": "all non-successful benchmark-valid episodes plus all "
                                  "attempts without a verdict (declared before any "
                                  "annotation)",
                "outcome": {"termination_reason": e.get("termination_reason"),
                            "failure_type": e.get("failure_type"),
                            "official_won": e.get("official_won"),
                            "false_finish": e.get("false_finish"),
                            "benchmark_valid": e["benchmark_valid"]},
                "automatic_markers": {
                    "first_rejected_or_error_context": e["first_deviation_context"],
                    "decision_rounds": e["decision_rounds"],
                    "rejected_decisions": e["rejected_decisions"],
                    "schema_invalid": e["schema_invalid"],
                    "model_errors": e["model_errors"],
                    "semantic_repairs": e["semantic_repairs"],
                    "longest_stuck_repeat": e["longest_stuck_repeat"],
                    "distinct_commands_of_issued": [e["distinct_commands"],
                                                    e["commands_issued"]],
                    "env_no_effect": e["env_no_effect"],
                    "refusal_reasons": e["rejection_reasons"],
                    "unrendered_skill_calls": len(e["commands"]) - e["commands_issued"],
                    "note": "markers narrow where a human should look; none of them is a "
                            "verdict, and an identical command can be a reasonable action "
                            "(SPEC-BST 8.3)"},
                "evidence": {"instruction": e.get("instruction"),
                             "events": f"episodes/{e['episode_id']}/events.jsonl",
                             "model_calls": f"episodes/{e['episode_id']}/model_calls.jsonl",
                             "commands": e["commands"]},
                "annotation": {"observed_pattern": None, "possible_cause": None,
                               "confidence": None, "evidence_refs": None,
                               "competing_explanation": None}}
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


__all__ = ["aggregate_run_root", "recompute_episode", "episode_dirs"]
