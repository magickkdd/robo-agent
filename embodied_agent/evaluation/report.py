"""Statistics and attribution for one batch (SPEC 11.4).

The reading rules this file exists to enforce:

* **Every planned run is in the denominator.** An infrastructure crash, an API
  error or a goal parse that failed lowers the rate instead of vanishing from it
  (SPEC 11.4: 所有预定运行均进入可靠性分母). Nothing here drops a sample because
  the task was hard or because a declared event never fired; both are reported.
* **Repeats are aggregated per configuration first**, then differences are taken
  per configuration, and the interval is bootstrapped over *configurations* — the
  three rollouts of one case are not three tasks (SPEC 11.1, 11.4).
* **The verdict is the independent evaluator's.** `agent_claims_success` is
  reported beside it, and the gap between the two is its own metric.
* **Wilson intervals are a description, not the test.** The claim-supporting
  number is the paired, config-clustered bootstrap interval; if it crosses zero,
  the wording this file emits is "preliminary", never "stable".

Metrics that need a judgement no automatic predicate can make — "was this the
reasonable next step, given several were legal" — are not scored here. They are
listed in `needs_blind_review` with the evidence pointer, for the pre-registered
blind review (SPEC 11.4: 以预先制定准则盲审并保留分歧，不能靠模型自评分).
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import statistics
from collections import defaultdict

from .blind_review import scan as scan_blind_review

SUBSETS = ("clean", "state_change", "execution_deviation", "protocol", "smoke", "dev")
# `outcome` values that mean "the experiment machinery failed", not "the agent
# failed". They are counted, and they are counted separately.
INFRA_OUTCOMES = ("infrastructure_error", "goal_resolution_error")
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20260919


# ------------------------------------------------------------------ input -----


def load_rows(run_dir: str) -> list[dict]:
    """The flat table plus whatever the per-episode summaries can add.

    A row without an episode directory is still a row: that is usually the case
    that broke, and dropping it would silently censor the failure."""
    path = os.path.join(run_dir, "episodes.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no episodes.csv in {run_dir}")
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["objects_completed"] = _int(r.get("objects_completed"))
            r["objects_total"] = _int(r.get("objects_total"))
            r["independent_complete_success"] = _bool(r.get("independent_complete_success"))
            r["agent_claims_success"] = _bool(r.get("agent_claims_success"))
            for k in ("decision_rounds", "skill_calls", "http_requests", "prompt_tokens",
                      "completion_tokens", "sim_time_s", "wall_time_s", "rejected_decisions",
                      "false_finish_attempts", "identical_invalid_attempts",
                      "identical_repeats_total", "model_errors", "slot_resolution_fallbacks",
                      "semantic_repairs", "events_unfired"):
                r[k] = _num(r.get(k))
            summary = os.path.join(run_dir, "episodes", r["episode_id"], "episode_summary.json")
            r["_events"] = _load_events(run_dir, r["episode_id"]) if os.path.exists(summary) else []
            r["_summary"] = json.load(open(summary, encoding="utf-8")) if os.path.exists(summary) else {}
            rows.append(r)
    return rows


def _jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_events(run_dir: str, episode_id: str) -> list[dict]:
    return _jsonl(os.path.join(run_dir, "episodes", episode_id, "events.jsonl"))


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(v):
    return str(v).strip().lower() in ("true", "1", "yes")


# ------------------------------------------------------------------ basics ----


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Descriptive only (SPEC 11.4: Wilson 区间可作补充描述)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def aggregate_by_case(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """One entry per (case, mode): the repeats collapsed into a fraction.

    This is the unit the comparison is run on, so a case that was lucky three
    times counts once."""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        buckets[(r["case_id"], r["mode"])].append(r)
    out = {}
    for (case_id, mode), group in buckets.items():
        n = len(group)
        ok = sum(1 for r in group if r["independent_complete_success"])
        infra = sum(1 for r in group if r["outcome"] in INFRA_OUTCOMES)
        api = sum(_int((r["_summary"].get("result") or {}).get("provider_counters", {})
                       .get("api_errors")) for r in group)
        out[(case_id, mode)] = {
            "case_id": case_id, "mode": mode, "subset": group[0]["subset"],
            "repeats": n, "successes": ok, "rate": ok / n if n else 0.0,
            "infra_failures": infra, "api_errors": api,
            "objects_total": max(r["objects_total"] for r in group),
            "mean_objects_completed": statistics.fmean(r["objects_completed"] for r in group),
            "decision_rounds": sum(_int(r["decision_rounds"]) for r in group),
            "skill_calls": sum(_int(r["skill_calls"]) for r in group),
            "http_requests": sum(_int(r["http_requests"]) for r in group),
            "prompt_tokens": sum(_int(r["prompt_tokens"]) for r in group),
            "completion_tokens": sum(_int(r["completion_tokens"]) for r in group),
            "sim_time_s": round(sum(_num(r["sim_time_s"]) or 0.0 for r in group), 2),
            "wall_time_s": round(sum(_num(r["wall_time_s"]) or 0.0 for r in group), 2),
        }
    return out


def cluster_bootstrap_paired_diff(agg: dict, a: str, b: str,
                                  replicates: int = BOOTSTRAP_REPLICATES,
                                  seed: int = BOOTSTRAP_SEED) -> dict:
    """Paired difference of per-case success rates, resampled over cases.

    `b - a`, so a positive mean favours `b`. Cases with only one arm present are
    excluded from *this* statistic and named, because a paired design cannot
    silently become an unpaired one."""
    cases = sorted({c for c, _ in agg})
    paired = [c for c in cases if (c, a) in agg and (c, b) in agg]
    missing = [c for c in cases if c not in paired]
    if not paired:
        return {"comparison": f"{b}-{a}", "paired_cases": 0, "excluded_cases": missing,
                "mean": None, "ci95": None,
                "note": "no configuration has both arms; a paired estimate is undefined"}
    diffs = [agg[(c, b)]["rate"] - agg[(c, a)]["rate"] for c in paired]
    rng = random.Random(seed)
    means = []
    for _ in range(replicates):
        sample = [rng.choice(diffs) for _ in diffs]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int(0.025 * (replicates - 1))]
    hi = means[min(replicates - 1, int(0.975 * (replicates - 1)))]
    point = statistics.fmean(diffs)
    return {"comparison": f"{b}-{a}", "paired_cases": len(paired), "excluded_cases": missing,
            "mean": round(point, 4), "ci95": [round(lo, 4), round(hi, 4)],
            "per_case": {c: round(diffs[i], 4) for i, c in enumerate(paired)},
            "replicates": replicates, "seed": seed,
            "reads_as": ("only positive (95% lower bound > 0)" if lo > 0 else
                         "only negative (95% upper bound < 0)" if hi < 0 else
                         "indistinguishable from zero on this sample")}


# --------------------------------------------------------------- behaviour ----


def behaviour_metrics(rows: list[dict]) -> dict:
    """What the episode logs can actually show, per mode.

    Every definition is stated in `definitions` so a reader can disagree with the
    definition instead of being misled by the number.
    """
    per_mode: dict[str, dict] = {}
    for mode in sorted({r["mode"] for r in rows}):
        group = [r for r in rows if r["mode"] == mode]
        regressed: set[str] = set()
        recovered: set[str] = set()
        disturbed = repaired = 0
        redundant = 0
        repeats_without_new_evidence = 0
        blind_review: list[dict] = []
        for r in group:
            episode_regressed: set[str] = set()
            episode_recovered: set[str] = set()
            satisfied_before: dict[str, str] = {}
            last_sig = None
            for e in r["_events"]:
                p = e.get("payload") or {}
                if e["type"] == "decision_context":
                    satisfied_before = {f"{x['entity_id']}->{x['target_id']}": x["value"]
                                        for x in (p.get("progress") or [])}
                if e["type"] == "decision" and p.get("action") == "execute":
                    ex = p.get("execute") or {}
                    args = ex.get("args") or {}
                    key = f"{args.get('object_id')}->{args.get('target_id')}"
                    if ex.get("skill") == "place" and satisfied_before.get(key) == "true":
                        redundant += 1
                        blind_review.append({"episode_id": r["episode_id"], "sequence": e["sequence"],
                                             "kind": "place_of_already_satisfied",
                                             "assignment": key})
                    sig = (ex.get("skill"), tuple(sorted(args.items())), ex.get("candidate_id"))
                    if sig == last_sig:
                        repeats_without_new_evidence += 1
                    last_sig = sig
                if e["type"] == "execution_feedback":
                    fb = p.get("feedback") or {}
                    for ch in fb.get("progress_changes") or []:
                        if ch["from"] == "true" and ch["to"] != "true":
                            episode_regressed.add(ch["assignment"])
                        elif (ch["from"] != "true" and ch["to"] == "true"
                              and ch["assignment"] in episode_regressed):
                            episode_recovered.add(ch["assignment"])
            # Credit is earned inside the episode that lost the goal. Repeats of
            # one case disturb the same assignment, so a pooled set would let a
            # rollout that repaired the damage pay for one that left it broken,
            # and the rate would stop describing anything that happened.
            disturbed += len(episode_regressed)
            repaired += len(episode_recovered)
            regressed |= episode_regressed
            recovered |= episode_recovered
        per_mode[mode] = {
            "episodes": len(group),
            "regressed_goals": sorted(regressed),
            "disturbed_goals_taken_back_into_account": sorted(recovered),
            "disturbed_goal_recovery_rate": round(repaired / disturbed, 4) if disturbed else None,
            "redundant_replacements_of_satisfied_goals": redundant,
            "consecutive_identical_actions": repeats_without_new_evidence,
            "identical_repeats_charged": sum(_int(r["identical_repeats_total"]) for r in group),
            "longest_identical_run_at_exit": max(
                (_int(r["identical_invalid_attempts"]) for r in group), default=0),
            "rejected_decisions": sum(_int(r["rejected_decisions"]) for r in group),
            "slot_resolution_fallbacks": sum(_int(r["slot_resolution_fallbacks"]) for r in group),
            "false_finish_attempts": sum(_int(r["false_finish_attempts"]) for r in group),
            "model_errors": sum(_int(r["model_errors"]) for r in group),
            "semantic_repairs": sum(_int(r["semantic_repairs"]) for r in group),
            "agent_claims_vs_independent": {
                "agent_successes": sum(1 for r in group if r["agent_claims_success"]),
                "independent_successes": sum(1 for r in group if r["independent_complete_success"])},
            "needs_blind_review": blind_review,
        }
    per_mode["_definitions"] = {
        "regressed_goals": "a goal predicate that read true and later read not-true in the "
                           "same episode (the environment invalidated a completed relation)",
        "disturbed_goal_recovery_rate": "share of the disturbance instances a mode created "
                                        "that were made true again before the same episode ended; "
                                        "counted per episode, so one rollout cannot repair another's "
                                        "damage; None when nothing regressed",
        "redundant_replacements_of_satisfied_goals": "executed place whose object->target was "
                                                    "already true in the decision's own context",
        "consecutive_identical_actions": "execute decisions repeating the previous skill, "
                                         "arguments and candidate back to back",
        "identical_repeats_charged": "the runtime's own total of attempts that repeated the "
                                     "previous action under an unchanged world (SPEC 6.2) — the "
                                     "budgeted version of the metric above, and the number the "
                                     "loop would have exited on",
        "longest_identical_run_at_exit": "the longest identical-attempt run any episode of this "
                                         "mode ended inside; this is the value the budget is "
                                         "compared against, so it is 1 for an episode whose last "
                                         "action was new, whatever happened earlier",
        "needs_blind_review": "episodes where several next steps were legal or the automatic "
                              "definition is contestable; scored by the pre-registered blind "
                              "review, never by the model (SPEC 11.4)",
        "slot_resolution_fallbacks": "executed places whose slot the shared execution-time rule "
                                     "chose because the decision named no candidate (SPEC 5.5). "
                                     "A policy fact, not a machinery one: it says how much of the "
                                     "geometry each mode decided for itself",
    }
    return per_mode


# ----------------------------------------------------------------- cost -------


def cost_metrics(rows: list[dict]) -> dict:
    per_mode: dict[str, dict] = {}
    for mode in sorted({r["mode"] for r in rows}):
        group = [r for r in rows if r["mode"] == mode]
        n = len(group) or 1
        per_mode[mode] = {
            "episodes": len(group),
            "skill_calls_total": sum(_int(r["skill_calls"]) for r in group),
            "decision_rounds_total": sum(_int(r["decision_rounds"]) for r in group),
            "http_requests_total": sum(_int(r["http_requests"]) for r in group),
            "prompt_tokens_total": sum(_int(r["prompt_tokens"]) for r in group),
            "completion_tokens_total": sum(_int(r["completion_tokens"]) for r in group),
            "wall_time_s_total": round(sum(_num(r["wall_time_s"]) or 0.0 for r in group), 1),
            "sim_time_s_total": round(sum(_num(r["sim_time_s"]) or 0.0 for r in group), 1),
            "http_requests_per_episode": round(sum(_int(r["http_requests"]) for r in group) / n, 2),
            "wall_time_s_per_episode": round(sum(_num(r["wall_time_s"]) or 0.0 for r in group) / n, 1),
            "api_errors": sum(_int((r["_summary"].get("result") or {}).get("provider_counters", {})
                                   .get("api_errors")) for r in group),
            "transport_retries": sum(_int((r["_summary"].get("result") or {}).get("provider_counters", {})
                                          .get("transport_retries")) for r in group),
            "format_repairs": sum(_int((r["_summary"].get("result") or {}).get("provider_counters", {})
                                       .get("format_repairs")) for r in group),
        }
    return per_mode


def _pctl(vals: list[float], q: float) -> float | None:
    """Nearest-rank percentile — with a dozen calls in a mode, interpolating
    between two measured numbers would report a latency nobody measured."""
    if not vals:
        return None
    ordered = sorted(vals)
    rank = min(len(ordered) - 1, max(0, math.ceil(q / 100 * len(ordered)) - 1))
    return round(ordered[rank], 3)


def _latency_shape(vals: list[float]) -> dict:
    return {"calls": len(vals),
            "mean_s": round(statistics.fmean(vals), 3) if vals else None,
            "p50_s": _pctl(vals, 50), "p95_s": _pctl(vals, 95), "max_s": _pctl(vals, 100),
            "total_s": round(sum(vals), 1) if vals else None}


def _ledger_latency(path: str) -> dict:
    """The same shape read straight off one `model_calls.jsonl`."""
    calls = _jsonl(path)
    vals = [float(c["latency_s"]) for c in calls if isinstance(c.get("latency_s"), (int, float))]
    return {**_latency_shape(vals), "calls_without_a_recorded_latency": len(calls) - len(vals)}


def model_latency(run_dir: str, rows: list[dict]) -> dict:
    """Wall time spent waiting on the provider, per call (SPEC 11.4: 延迟).

    The per-call ledger is the only source, because a per-episode total cannot show
    the tail one slow request burns; the calls inside an episode are sequential, so
    `total_s` is the share of that episode's wall time the provider is answerable
    for. Two absences are reported rather than absorbed: an episode whose ledger is
    missing (it died before asking, which is why its mode may look fast), and a mode
    with no calls at all — a rule planner gets no measurements, not a latency of 0.
    The goal parse is kept in its own block: it is billed once per case and shared
    by every mode, so folding it in would move the decision numbers without the
    decisions changing.
    """
    out: dict[str, dict] = {}
    for mode in sorted({r["mode"] for r in rows}):
        group = [r for r in rows if r["mode"] == mode]
        vals: list[float] = []
        kinds: dict[str, list[float]] = defaultdict(list)
        missing_ledger, unrecorded = 0, 0
        for r in group:
            path = os.path.join(run_dir, "episodes", r["episode_id"], "model_calls.jsonl")
            if not os.path.exists(path):
                missing_ledger += 1
                continue
            calls = 0
            for row in _jsonl(path):
                v = row.get("latency_s")
                if not isinstance(v, (int, float)):
                    unrecorded += 1
                    continue
                calls += 1
                vals.append(float(v))
                kinds[str(row.get("kind") or "unknown")].append(float(v))
            if not calls:
                missing_ledger += 1
        out[mode] = {**_latency_shape(vals),
                     "episodes_without_a_model_call": missing_ledger,
                     "calls_without_a_recorded_latency": unrecorded,
                     "by_kind": {k: _latency_shape(v) for k, v in sorted(kinds.items())},
                     "kinds_compared": "only a mean over the same kind is comparable: "
                                       "mode A asks once per episode, modes B and C once "
                                       "per round"}
    out["goal_parse"] = {**_ledger_latency(os.path.join(run_dir, "goal_resolutions",
                                                        "goal_calls.jsonl")),
                         "note": "one shared parse per case, from goal_resolutions/goal_calls.jsonl"}
    return out


def failure_attribution(rows: list[dict]) -> dict:
    """Every non-success explained by the code the episode itself reported."""
    out: dict[str, dict] = {}
    for mode in sorted({r["mode"] for r in rows}):
        group = [r for r in rows if r["mode"] == mode]
        codes: dict[str, int] = defaultdict(int)
        for r in group:
            if r["independent_complete_success"]:
                continue
            key = r["failure_type"] or r["outcome"] or "unspecified"
            codes[str(key)] += 1
        out[mode] = {
            "episodes": len(group),
            "not_success_by_reported_cause": dict(sorted(codes.items(), key=lambda kv: -kv[1])),
            "infrastructure": sum(1 for r in group if r["outcome"] in INFRA_OUTCOMES),
            "event_not_fired": sum(1 for r in group if _int(r["events_unfired"])),
        }
    return out


# ----------------------------------------------------------------- report -----


# What the report carries out of the diagnostic's own artifact: the headline and
# its denominators, plus everything that qualifies how the rate should be read.
# The per-pair detail stays in `state_utilization.json`, next to the contexts it
# was measured on.
STATE_UTIL_FIELDS = (
    "policy_label", "policy_provider", "action_fit_rate", "contexts_requested",
    "contexts_fitted", "pairs_assessed", "pairs_with_both_arms_fitting",
    "pairs_where_state_changed_behaviour", "pairs_requiring_a_difference",
    "pairs_that_did_not_use_the_distinction", "contexts_naming_a_candidate",
    "pairs_where_candidate_choice_went_untested", "synthetic_arms",
    "misses_by_kind", "contexts_answered", "action_fit_rate_of_answers",
    "provider_counters", "unreachable_pairs", "diagnostic_only_no_physics_executed",
    "code", "definitions",
)


def state_utilization(run_dir: str) -> dict:
    """SPEC 11.4 状态分叉动作适配率, read back from `state-util`'s artifact.

    Not run is a different claim from run and scored zero, so the absence is the
    result here; and a fit rate is a behavioural number measured on contexts without
    executing a single one of them, which is why it sits beside the behaviour metrics
    and never inside a success rate.
    """
    path = os.path.join(run_dir, "state_utilization.json")
    if not os.path.exists(path):
        return {"ran": False,
                "note": "no state_utilization.json in this run directory: run "
                        "`state-util --out <this directory>` to measure the frozen "
                        "state pairs (SPEC 11.3)"}
    with open(path, encoding="utf-8") as f:
        summary = json.load(f)["summary"]
    out = {k: summary.get(k) for k in STATE_UTIL_FIELDS}
    out["ran"] = True
    out["artifact"] = path
    # the diagnostic's own artifact counts requests and tokens; latency lives only in
    # the ledger it left beside it, and SPEC 11.4 asks for all three together
    out["provider_latency"] = _ledger_latency(os.path.join(run_dir, "model_calls.jsonl"))
    return out


def _blind_review(run_dir: str, contested: list[dict]) -> dict:
    """The pre-registered blind review's state for this run (SPEC 11.4).

    A sheet that cannot be built is named inside the report instead of taking the
    report down with it: the reader still needs the success rates, and `error` is
    the difference between "nothing to review" and "the review machinery is broken"."""
    try:
        return scan_blind_review(run_dir, contested)
    except Exception as e:  # noqa: BLE001 - a broken sheet is a finding, not a missing report
        return {"ran": False, "error": f"{type(e).__name__}: {e}", "items": len(contested),
                "note": "the blind-review sheet could not be built; the contested items are "
                        "still listed per mode under `behaviour`"}


def build_report(run_dir: str) -> dict:
    rows = load_rows(run_dir)
    agg = aggregate_by_case(rows)
    modes = sorted({r["mode"] for r in rows})
    cases = sorted({r["case_id"] for r in rows})
    per_subset: dict[str, dict] = {}
    for subset in sorted({r["subset"] for r in rows}):
        in_sub = [r for r in rows if r["subset"] == subset]
        per_subset[subset] = {
            mode: {
                "episodes": sum(1 for r in in_sub if r["mode"] == mode),
                "independent_successes": sum(1 for r in in_sub
                                             if r["mode"] == mode and r["independent_complete_success"]),
                "wilson95_descriptive": [round(v, 4) for v in wilson_interval(
                    sum(1 for r in in_sub if r["mode"] == mode and r["independent_complete_success"]),
                    sum(1 for r in in_sub if r["mode"] == mode))],
            } for mode in modes}
    by_objects: dict[str, dict] = {}
    for n_obj in sorted({r["objects_total"] for r in rows}):
        in_n = [r for r in rows if r["objects_total"] == n_obj]
        by_objects[str(n_obj)] = {
            mode: {"episodes": sum(1 for r in in_n if r["mode"] == mode),
                   "independent_successes": sum(1 for r in in_n
                                                 if r["mode"] == mode and r["independent_complete_success"])}
            for mode in modes}

    manifest_path = os.path.join(run_dir, "manifest.json")
    manifest = json.load(open(manifest_path, encoding="utf-8")) if os.path.exists(manifest_path) else {}
    behaviour = behaviour_metrics(rows)
    # the pointers are the whole of what the automatic reading could not settle;
    # the blind review turns them into items and reports whichever verdicts exist
    contested = [p for arm_id, arm in behaviour.items()
                 if arm_id != "_definitions" and isinstance(arm, dict)
                 for p in (arm.get("needs_blind_review") or [])]
    report = {
        "run_dir": os.path.abspath(run_dir),
        "run_id": manifest.get("run_id"),
        "planner": manifest.get("planner"),
        "offline": manifest.get("offline"),
        "schema_version": manifest.get("schema_version"),
        "code": {"commit": (manifest.get("code") or {}).get("commit"),
                 "dirty": (manifest.get("code") or {}).get("dirty"),
                 "dirty_diff_sha256": (manifest.get("code") or {}).get("dirty_diff_sha256")},
        # a number is only comparable to another number if both were measured
        # against the same task list (SPEC 11.1)
        "frozen": manifest.get("frozen"),
        "model": manifest.get("model"),
        "planned_runs": len(rows),
        "cases": len(cases),
        "modes": modes,
        "primary_metric": "independent complete-task success, aggregated per case then "
                          "compared per case with a configuration-clustered bootstrap",
        "success_rate_by_case": {f"{c}|{m}": v["rate"] for (c, m), v in sorted(agg.items())},
        "by_subset": per_subset,
        "by_object_count": by_objects,
        "paired_comparisons": {f"{b}-{a}": cluster_bootstrap_paired_diff(agg, a, b)
                               for a, b in [(x, y) for x in modes for y in modes if x < y]},
        "behaviour": behaviour,
        "state_utilization": state_utilization(run_dir),
        # SPEC 11.4: the readings that need a judgement about what a *reasonable*
        # next step was are reviewed by humans against pre-registered criteria. The
        # report says how many items there are and which verdicts exist; a broken
        # sheet is named inside the report rather than allowed to eat the report.
        "blind_review": _blind_review(run_dir, contested),
        "cost": cost_metrics(rows),
        "model_latency": model_latency(run_dir, rows),
        "failure_attribution": failure_attribution(rows),
        "event_coverage": {
            "episodes_with_declared_events": sum(
                1 for r in rows if (r["_summary"].get("environment_events") or {}).get("declared")),
            "episodes_where_a_declared_event_never_fired": sum(1 for r in rows if _int(r["events_unfired"])),
            "note": "episodes are kept whatever happened (SPEC 10.3); coverage is reported, "
                    "not filtered on",
        },
        "reliability_denominator_note": "planned_runs is the denominator for every rate; "
                                        f"{sum(1 for r in rows if r['outcome'] in INFRA_OUTCOMES)} "
                                        "of them are machinery failures and are listed separately",
    }
    return report


def write_report(run_dir: str, report: dict | None = None) -> dict:
    report = report or build_report(run_dir)
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    text = render_markdown(report)
    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return report


def render_markdown(report: dict) -> str:
    code = report.get("code") or {}
    dirty = code.get("dirty")
    frozen = report.get("frozen") or {}
    L = [f"# {report.get('run_id') or report['run_dir']}",
         "",
         f"- planner `{report.get('planner')}`"
         f"{' (offline: not a model result)' if report.get('offline') else ''}",
         f"- code `{str(code.get('commit') or '')[:12]}` "
         f"dirty={'unverifiable' if dirty is None else dirty}",
         f"- frozen task list `{str(frozen.get('detail') or 'not checked')[:12]}` "
         f"matches={frozen.get('matches')} refusal={frozen.get('enforced')}",
         f"- planned runs `{report['planned_runs']}` across "
         f"`{report['cases']}` cases x modes {''.join(report['modes'])}",
         "",
         "## Primary metric — independent complete-task success",
         "",
         "| subset | mode | episodes | successes | Wilson (descriptive only) |",
         "|---|---|---:|---:|---:|"]
    for subset, modes in (report.get("by_subset") or {}).items():
        for mode, v in modes.items():
            L.append(f"| {subset} | {mode} | {v['episodes']} | {v['independent_successes']} | "
                     f"[{v['wilson95_descriptive'][0]:.3f}, {v['wilson95_descriptive'][1]:.3f}] |")
    L += ["", "## Paired comparisons (per-case, bootstrap over configurations)", ""]
    for name, c in (report.get("paired_comparisons") or {}).items():
        if c.get("mean") is None:
            L.append(f"- `{name}`: undefined — {c.get('note')}"
                     f"{' excluded ' + ','.join(c['excluded_cases']) if c.get('excluded_cases') else ''}")
            continue
        lo, hi = c["ci95"]
        L.append(f"- `{name}`: mean {c['mean']:+.3f}, 95% CI [{lo:+.3f}, {hi:+.3f}] over "
                 f"{c['paired_cases']} cases ({c['replicates']} replicates, seed {c['seed']}) — "
                 f"{c['reads_as']}")
    L += ["", "## Costs", "", "| mode | skills | decisions | http | tokens(p/c) | wall s | sim s | api errors |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for mode, v in (report.get("cost") or {}).items():
        L.append(f"| {mode} | {v['skill_calls_total']} | {v['decision_rounds_total']} | "
                 f"{v['http_requests_total']} | {v['prompt_tokens_total']}/{v['completion_tokens_total']} | "
                 f"{v['wall_time_s_total']} | {v['sim_time_s_total']} | {v['api_errors']} |")
    lat = report.get("model_latency") or {}
    for mode, v in lat.items():
        if mode == "goal_parse":
            continue
        if not v["calls"]:
            L.append(f"- **{mode}**: no model call was made, so no latency is measured — "
                     f"this is not a latency of 0")
            continue
        kinds = ", ".join(f"{k} x{c['calls']}" for k, c in v["by_kind"].items())
        L.append(f"- **{mode}** latency per call ({kinds}): p50 {v['p50_s']} s, p95 {v['p95_s']} s, "
                 f"max {v['max_s']} s; {v['total_s']} s of waiting on the provider in total")
        if v["episodes_without_a_model_call"]:
            L.append(f"  - {v['episodes_without_a_model_call']} episode(s) of this mode never reached "
                     f"a model call and contribute nothing above")
        if v["calls_without_a_recorded_latency"]:
            L.append(f"  - {v['calls_without_a_recorded_latency']} call(s) recorded no latency and are "
                     f"absent from the percentiles")
    g = lat.get("goal_parse") or {}
    if g.get("calls"):
        L.append(f"- goal parse, shared across modes at one call per case: p50 {g['p50_s']} s, "
                 f"max {g['max_s']} s over {g['calls']} cases")
    L += ["", "## Failure attribution", ""]
    for mode, v in (report.get("failure_attribution") or {}).items():
        L.append(f"- **{mode}** ({v['episodes']} episodes): "
                 + ", ".join(f"{k} x{n}" for k, n in v["not_success_by_reported_cause"].items()) or "-")
        if v["infrastructure"]:
            L.append(f"  - machinery failures counted as failures, not removed: {v['infrastructure']}")
        if v["event_not_fired"]:
            L.append(f"  - declared event never fired in {v['event_not_fired']} episode(s) (kept)")
    L += ["", "## Behaviour", ""]
    for mode, v in (report.get("behaviour") or {}).items():
        if mode.startswith("_"):
            continue
        L.append(f"- **{mode}**: disturbed goals {len(v['regressed_goals'])}, taken back into account "
                 f"{len(v['disturbed_goals_taken_back_into_account'])} "
                 f"(rate {v['disturbed_goal_recovery_rate']}); redundant re-placements "
                 f"{v['redundant_replacements_of_satisfied_goals']}; identical repeats "
                 f"{v['consecutive_identical_actions']} (runtime total "
                 f"{v['identical_repeats_charged']}, longest run at exit "
                 f"{v['longest_identical_run_at_exit']}); "
                 f"rejected decisions {v['rejected_decisions']}; false finishes "
                 f"{v['false_finish_attempts']}; agent claims {v['agent_claims_vs_independent']['agent_successes']} "
                 f"vs independent {v['agent_claims_vs_independent']['independent_successes']}")
    su = report.get("state_utilization") or {}
    L += ["", "## State-fork action fit (SPEC 11.3 diagnostic — nothing executed)", ""]
    if not su.get("ran"):
        L.append(f"- not run: {su.get('note')}")
    else:
        L.append(f"- `{su['policy_label']}` ({su['policy_provider']}): "
                 f"**{su['action_fit_rate']}** ({su['contexts_fitted']}/{su['contexts_requested']} "
                 f"contexts), both arms fitting in {su['pairs_with_both_arms_fitting']}/"
                 f"{su['pairs_assessed']} pairs; synthetic arms {su['synthetic_arms']}, "
                 f"provider requests {su['provider_counters']['http_requests']}, "
                 f"tokens {su['provider_counters']['prompt_tokens']}/"
                 f"{su['provider_counters']['completion_tokens']}")
        if su.get("misses_by_kind"):
            kinds = ", ".join(f"{k} x{n}" for k, n in su["misses_by_kind"].items())
            L.append(f"  - misses by kind: {kinds}; of the {su['contexts_answered']} contexts that "
                     f"produced a decision at all the rate is "
                     f"{su['action_fit_rate_of_answers']} — the un-answered ones stay in the "
                     f"rate above, they are not excluded")
        v = su.get("provider_latency") or {}
        if v.get("calls"):
            L.append(f"  - wall time per request: p50 {v['p50_s']} s, p95 {v['p95_s']} s, "
                     f"max {v['max_s']} s over {v['calls']} calls, {v['total_s']} s waiting "
                     f"in a diagnostic that executed no physics")
        elif not su["provider_counters"]["http_requests"]:
            L.append("  - no request was made, so there is no latency to compare: a rule control "
                     "is free and instant by construction, which is the baseline to beat")
        if su["pairs_that_did_not_use_the_distinction"]:
            L.append(f"  - pairs whose two arms drew the same action, though each arm froze a "
                     f"different one: {', '.join(su['pairs_that_did_not_use_the_distinction'])}")
        if su["pairs_where_candidate_choice_went_untested"]:
            L.append(f"  - candidate-choice pairs answered without naming a slot, so their fit "
                     f"tested no geometry: {', '.join(su['pairs_where_candidate_choice_went_untested'])}")
        if su["unreachable_pairs"]:
            L.append(f"  - unreachable pairs (rate is not the 12-pair diagnostic): "
                     f"{', '.join(str(p.get('pair_id')) for p in su['unreachable_pairs'])}")
    br = report.get("blind_review") or {}
    L += ["", "## Blind review (SPEC 11.4 — criteria pre-registered, never model self-scored)", ""]
    if br.get("error"):
        L.append(f"- the sheet could not be built: {br['error']} — {br['items']} contested items "
                 "remain unreviewed and are still listed under `behaviour`")
    elif not br.get("ran"):
        kinds = ", ".join(f"{k} x{n}" for k, n in (br.get("items_by_kind") or {}).items()) or "none"
        rub = br.get("rubric")
        registered = (f"criteria `{rub['rubric_id']}` (sha256 {str(rub['sha256'])[:12]}) are "
                      f"registered" if rub else "no criteria block was recorded for this run")
        L.append(f"- **{br.get('items', 0)} contested items** ({kinds}); {registered}, and no "
                 f"human verdict file exists: {br.get('note')}")
    else:
        b = br["by_status"]
        L.append(f"- {br['items']} items, {br['items_settled']} settled by all "
                 f"{br['reviewers_required']} reviewers: adverse {b['adverse']}, reasonable "
                 f"{b['reasonable']}, **divided {b['divided']}**, cannot-tell {b['cannot_tell']}, "
                 f"partial {b['partial']}; unreviewed {len(br['unreviewed'])}")
        L.append(f"- adverse rate strict {br['adverse_rate_strict']} vs counting each split as "
                 f"adverse {br['adverse_rate_counting_divided_as_adverse']} — the gap is how much "
                 "of this reading depends on how disagreement is handled, so both are published")
        if br.get("refused"):
            L.append(f"- {len(br['refused'])} verdict rows refused (reasons in `report.json`)")
        if br.get("not_reviewable"):
            L.append(f"- {len(br['not_reviewable'])} items have no evidence to review and are "
                     "named rather than dropped")
    L += ["", "Definitions and the blind-review list are in `report.json`.", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="embodied_agent.evaluation.report")
    p.add_argument("run_dir")
    p.add_argument("--json-only", action="store_true")
    a = p.parse_args(argv)
    report = write_report(a.run_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if a.json_only
                  else render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
