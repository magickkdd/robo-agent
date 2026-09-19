"""The statistics layer of one batch report (SPEC 11.4).

Pure functions over the flat table plus the per-episode logs, so they are tested
here without physics: a wrong denominator, an interval that widens with more
data, or a paired test that quietly became unpaired would change what a reported
number means, and no episode outcome would notice.

The recurring shape of these tests is *a trap a naive implementation falls into*:
three rollouts of one case are not three tasks, a case with only one arm present
cannot take part in a paired difference, a zero-success rate still has a
non-zero upper bound, and one episode may not pay for the damage another did.
"""
from __future__ import annotations

import csv
import json
import os

import pytest

from embodied_agent.evaluation.report import (
    INFRA_OUTCOMES,
    aggregate_by_case,
    behaviour_metrics,
    build_report,
    cluster_bootstrap_paired_diff,
    cost_metrics,
    failure_attribution,
    load_rows,
    model_latency,
    render_markdown,
    state_utilization,
    wilson_interval,
)
from embodied_agent.evaluation.run import CSV_FIELDS as CSV_COLUMNS


def _row(case_id="c1", mode="B", repeat=0, *, outcome="success", subset="clean",
         objects_total=2, objects_completed=2, independent=None, agent_claims=None,
         decision_rounds=3, skill_calls=3, http_requests=3, prompt_tokens=100,
         completion_tokens=20, sim_time_s=10.0, wall_time_s=5.0, api_errors=0,
         transport_retries=0, format_repairs=0, events=None, **extra) -> dict:
    """One row as `load_rows` hands it over: the CSV columns, coerced, plus the
    episode summary and event log only the per-episode directory can add.

    A row that is not a success is not an independent success unless the test
    says otherwise — the two claims come from different code and coupling them
    here keeps the fixtures honest.
    """
    if independent is None:
        independent = outcome == "success"
    if agent_claims is None:
        agent_claims = outcome == "success"
    r = {"episode_id": f"{case_id}.{mode}.r{repeat}", "case_id": case_id, "set": "formal",
         "subset": subset, "mode": mode, "repeat": repeat, "expected": outcome,
         "outcome": outcome, "failure_type": None if outcome == "success" else outcome,
         "independent_complete_success": independent, "agent_claims_success": agent_claims,
         "objects_completed": objects_completed, "objects_total": objects_total,
         "decision_rounds": decision_rounds, "skill_calls": skill_calls,
         "http_requests": http_requests, "prompt_tokens": prompt_tokens,
         "completion_tokens": completion_tokens, "sim_time_s": sim_time_s,
         "wall_time_s": wall_time_s, "rejected_decisions": 0, "false_finish_attempts": 0,
         "identical_invalid_attempts": 0, "identical_repeats_total": 0,
         "model_errors": 0, "semantic_repairs": 0, "slot_resolution_fallbacks": 0,
         "events_unfired": 0, "protocol_notes": "", "probe": "", "probe_met": "",
         "_events": events or [],
         "_summary": {"result": {"provider_counters": {
             "api_errors": api_errors, "transport_retries": transport_retries,
             "format_repairs": format_repairs}}}}
    r.update(extra)
    return r


def _ctx(*progress):
    """A `decision_context` event: the progress the model was actually shown."""
    return {"type": "decision_context", "sequence": 1, "payload": {
        "progress": [{"entity_id": e, "target_id": t, "value": v} for e, t, v in progress]}}


def _decide(skill, *, obj, target=None, candidate=None, seq=2):
    args = {"object_id": obj}
    if target is not None:
        args["target_id"] = target
    return {"type": "decision", "sequence": seq, "payload": {
        "action": "execute", "execute": {"skill": skill, "args": args, "candidate_id": candidate}}}


def _feedback(*changes, seq=3):
    return {"type": "execution_feedback", "sequence": seq, "payload": {
        "feedback": {"progress_changes": [{"assignment": a, "from": f, "to": t}
                                          for a, f, t in changes]}}}


def _write_run(tmp_path, rows, *, with_dirs=True, manifest=None):
    """A run directory shaped exactly like the one `evaluation.run` leaves behind."""
    with open(tmp_path / "episodes.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r[k]) for k in CSV_COLUMNS})
    for r in rows:
        if not with_dirs:
            continue
        d = os.path.join(str(tmp_path), "episodes", r["episode_id"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "episode_summary.json"), "w", encoding="utf-8") as f:
            json.dump(r["_summary"], f)
        with open(os.path.join(d, "events.jsonl"), "w", encoding="utf-8") as f:
            for e in r["_events"]:
                f.write(json.dumps(e) + "\n")
    if manifest is not None:
        with open(tmp_path / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f)
    return str(tmp_path)


# ------------------------------------------------------------- denominators ----


def test_wilson_interval_describes_uncertainty_rather_than_the_point_estimate():
    lo, hi = wilson_interval(5, 10)
    assert abs((lo + hi) / 2 - 0.5) < 1e-12, "the interval must stay centred on p=0.5"
    assert 0.2 < lo and hi < 0.8
    # the whole reason this interval beats a normal approximation: a sample of
    # zero successes does not license a claim of impossibility
    zero_lo, zero_hi = wilson_interval(0, 10)
    assert zero_lo == 0.0 and zero_hi > 0.2, (zero_lo, zero_hi)
    all_lo, all_hi = wilson_interval(10, 10)
    assert all_hi == 1.0 and all_lo < 0.8
    assert hi - lo > wilson_interval(50, 100)[1] - wilson_interval(50, 100)[0], "more data, tighter"
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_repeats_of_one_case_collapse_into_a_single_configuration():
    """SPEC 11.1/11.4: the comparison unit is the configuration, not the rollout."""
    rows = [_row("c1", "A", r, independent=r < 2) for r in range(3)] \
        + [_row("c1", "B", r) for r in range(3)] \
        + [_row("c2", "A", 0, objects_total=4, objects_completed=1, independent=False)]
    agg = aggregate_by_case(rows)
    assert set(agg) == {("c1", "A"), ("c1", "B"), ("c2", "A")}
    assert agg[("c1", "A")]["repeats"] == 3
    assert agg[("c1", "A")]["rate"] == pytest.approx(2 / 3)
    assert agg[("c1", "B")]["rate"] == 1.0
    # counters are summed over the repeats that incurred them, and the scene the
    # case declared is the denominator, not whatever one run happened to finish
    assert agg[("c1", "A")]["skill_calls"] == 9
    assert agg[("c1", "A")]["prompt_tokens"] == 300
    assert agg[("c1", "A")]["sim_time_s"] == pytest.approx(30.0)
    assert agg[("c2", "A")]["objects_total"] == 4
    assert agg[("c2", "A")]["mean_objects_completed"] == pytest.approx(1.0)
    assert agg[("c1", "A")]["subset"] == "clean"


def test_machinery_failures_are_counted_and_labelled_rather_than_dropped():
    rows = [_row("c1", "B", outcome=o) for o in INFRA_OUTCOMES] + [_row("c1", "B", 1)]
    cell = aggregate_by_case(rows)[("c1", "B")]
    assert cell["repeats"] == 3, "an infrastructure crash must stay in the denominator"
    assert cell["infra_failures"] == 2
    assert cell["rate"] == pytest.approx(1 / 3)


# ------------------------------------------------------- the paired estimate ---


def test_a_paired_difference_is_taken_per_configuration_and_bootstrapped_over_cases():
    agg = aggregate_by_case([_row("c1", "A", independent=False), _row("c1", "B"),
                             _row("c2", "A", independent=False), _row("c2", "B", independent=False),
                             _row("c3", "A"), _row("c3", "B")])
    res = cluster_bootstrap_paired_diff(agg, "A", "B", replicates=500, seed=7)
    assert res["paired_cases"] == 3
    assert res["mean"] == pytest.approx(1 / 3, abs=1e-4)
    assert res["per_case"] == {"c1": 1.0, "c2": 0.0, "c3": 0.0}
    lo, hi = res["ci95"]
    assert lo <= res["mean"] <= hi
    assert res["reads_as"] == "indistinguishable from zero on this sample"
    assert res["comparison"] == "B-A", "the sign convention is documented as b - a"


def test_the_interval_is_a_function_of_the_seed_not_of_the_moment():
    agg = aggregate_by_case([_row("c1", "A", independent=False), _row("c1", "B"),
                             _row("c2", "A"), _row("c2", "B", independent=False)])
    a = cluster_bootstrap_paired_diff(agg, "A", "B", replicates=400, seed=11)
    b = cluster_bootstrap_paired_diff(agg, "A", "B", replicates=400, seed=11)
    assert a == b, "a reported interval that moves between runs cannot be reviewed"
    c = cluster_bootstrap_paired_diff(agg, "A", "B", replicates=400, seed=12)
    assert c["mean"] == a["mean"], "the point estimate is not a resample"
    assert c["seed"] == 12


def test_a_unanimous_improvement_is_the_one_case_where_the_claim_is_directional():
    agg = aggregate_by_case([_row(f"c{i}", "A", independent=False) for i in range(4)]
                            + [_row(f"c{i}", "B") for i in range(4)])
    res = cluster_bootstrap_paired_diff(agg, "A", "B", replicates=300)
    assert res["mean"] == 1.0 and res["ci95"] == [1.0, 1.0]
    assert res["reads_as"].startswith("only positive")
    worse = cluster_bootstrap_paired_diff(agg, "B", "A", replicates=300)
    assert worse["mean"] == -1.0 and worse["reads_as"].startswith("only negative")


def test_a_case_with_one_arm_present_is_excluded_by_name_not_silently():
    """A paired design that becomes unpaired mid-report measures nothing."""
    agg = aggregate_by_case([_row("c1", "A"), _row("c1", "B"), _row("c2", "A"), _row("c3", "B")])
    res = cluster_bootstrap_paired_diff(agg, "A", "B")
    assert res["paired_cases"] == 1 and res["mean"] == 0.0
    assert res["excluded_cases"] == ["c2", "c3"]
    assert res["replicates"] == 2000 and res["seed"] == 20260919, "the shipped defaults"

    alone = cluster_bootstrap_paired_diff(aggregate_by_case([_row("c1", "A")]), "A", "B")
    assert alone["mean"] is None and alone["ci95"] is None and alone["paired_cases"] == 0
    assert "undefined" in alone["note"]
    assert alone["excluded_cases"] == ["c1"]


# ------------------------------------------------------------- behaviour -------


def test_a_disturbed_goal_is_credited_only_to_the_episode_that_repaired_it():
    repaired = _row("c1", "B", 0, events=[
        _ctx(("obj_red_1", "tray_left", "true"), ("obj_blue_2", "tray_right", "false")),
        _decide("pick", obj="obj_blue_2", seq=2),
        _feedback(("obj_red_1->tray_left", "true", "false")),
        _decide("pick", obj="obj_red_1", seq=4),
        _feedback(("obj_red_1->tray_left", "false", "true"), seq=5)])
    abandoned = _row("c1", "B", 1, events=[
        _ctx(("obj_red_1", "tray_left", "true")),
        _decide("place", obj="obj_blue_2", target="tray_right", seq=2),
        _feedback(("obj_red_1->tray_left", "true", "unknown"), seq=3)])
    b = behaviour_metrics([repaired, abandoned])["B"]
    assert b["regressed_goals"] == ["obj_red_1->tray_left"]
    assert b["disturbed_goals_taken_back_into_account"] == ["obj_red_1->tray_left"]
    # two disturbances, one repair: pooled sets would report 1.0 here and read as
    # a mode that always recovers, because the two rollouts damage the same name
    assert b["disturbed_goal_recovery_rate"] == pytest.approx(0.5)


def test_nothing_regressing_reports_no_rate_rather_than_a_perfect_one():
    b = behaviour_metrics([_row("c1", "A", events=[
        _ctx(("obj_red_1", "tray_left", "false")),
        _decide("pick", obj="obj_red_1", seq=2),
        _feedback(("obj_red_1->tray_left", "false", "true"))])])["A"]
    assert b["disturbed_goal_recovery_rate"] is None
    assert b["regressed_goals"] == []


def test_replacing_an_assignment_the_context_already_reported_true_is_counted_each_time():
    row = _row("c1", "C", events=[
        _ctx(("obj_red_1", "tray_left", "true")),
        _decide("place", obj="obj_red_1", target="tray_left", seq=2),
        _ctx(("obj_red_1", "tray_left", "true")),
        _decide("place", obj="obj_red_1", target="tray_left", seq=4)])
    c = behaviour_metrics([row])["C"]
    assert c["redundant_replacements_of_satisfied_goals"] == 2
    assert [e["sequence"] for e in c["needs_blind_review"]] == [2, 4]
    assert c["needs_blind_review"][0]["episode_id"] == "c1.C.r0"
    assert c["needs_blind_review"][0]["assignment"] == "obj_red_1->tray_left"
    assert c["needs_blind_review"][0]["kind"] == "place_of_already_satisfied"
    # a pick of the same object is not a replacement, and a place into a target
    # the context did not report satisfied is not one either
    other = _row("c2", "C", events=[
        _ctx(("obj_red_1", "tray_left", "true")),
        _decide("pick", obj="obj_red_1", seq=2),
        _decide("place", obj="obj_red_1", target="tray_right", seq=3)])
    assert behaviour_metrics([other])["C"]["redundant_replacements_of_satisfied_goals"] == 0


def test_only_back_to_back_identical_decisions_count_as_repetition_without_new_evidence():
    events = [_decide("pick", obj="obj_red_1", candidate="k1", seq=1),
              _decide("pick", obj="obj_red_1", candidate="k1", seq=2),
              _decide("pick", obj="obj_red_1", candidate="k2", seq=3),
              _decide("pick", obj="obj_red_1", candidate="k1", seq=4),
              _decide("pick", obj="obj_red_1", candidate="k1", seq=5),
              _decide("pick", obj="obj_red_1", candidate="k1", seq=6)]
    b = behaviour_metrics([_row("c1", "B", events=events)])["B"]
    # one for 1-2, the differing candidate at 3 breaks the streak, then two for 4-5-6
    assert b["consecutive_identical_actions"] == 3
    # the ledger resets between episodes: the last action of one rollout is not
    # evidence about what the next one repeated
    two = behaviour_metrics([_row("c1", "B", 0, events=events[:2]),
                             _row("c1", "B", 1, events=events[:2])])["B"]
    assert two["consecutive_identical_actions"] == 2
    assert behaviour_metrics([_row("c1", "B", events=events[:1])])["B"][
        "consecutive_identical_actions"] == 0


def test_a_non_execute_decision_neither_repeats_anything_nor_resets_the_streak():
    """A `finish` attempt is not an action, but it must not launder a repeat."""
    events = [_decide("pick", obj="obj_red_1", candidate="k1", seq=1),
              {"type": "decision", "sequence": 2, "payload": {"action": "finish"}},
              _decide("pick", obj="obj_red_1", candidate="k1", seq=3)]
    b = behaviour_metrics([_row("c1", "B", events=events)])["B"]
    assert b["consecutive_identical_actions"] == 1


def test_the_runtime_s_own_charges_are_reported_beside_the_derived_counts():
    """`identical_repeats_total` is what the loop billed across the whole episode
    and `identical_invalid_attempts` the run it exited inside; the derived count is
    what the log shows. Reporting only one of the three hides a mismatch."""
    row = _row("c1", "B", outcome="repeated_invalid", identical_invalid_attempts=4,
               identical_repeats_total=3,
               rejected_decisions=2, false_finish_attempts=1, model_errors=1, semantic_repairs=2,
               events=[_decide("place", obj="obj_red_1", target="tray_left", seq=1),
                       _decide("place", obj="obj_red_1", target="tray_left", seq=2)])
    b = behaviour_metrics([row, _row("c1", "B", 1)])["B"]
    assert b["consecutive_identical_actions"] == 1
    assert b["identical_repeats_charged"] == 3
    assert b["longest_identical_run_at_exit"] == 4
    assert b["rejected_decisions"] == 2 and b["false_finish_attempts"] == 1
    assert b["model_errors"] == 1 and b["semantic_repairs"] == 2
    assert b["agent_claims_vs_independent"] == {"agent_successes": 1, "independent_successes": 1}
    assert b["episodes"] == 2


def test_a_clean_episode_is_not_billed_for_the_counter_that_ignited_it():
    """The budget counter is a run length, so an episode whose last action was new
    reads 1. Summing it — which is what an obvious implementation does — makes a
    rule policy that never repeated look like it repeated once per episode."""
    clean = [_row(f"c{i}", "A", identical_invalid_attempts=1,
                  events=[_decide("pick", obj=f"obj_{i}", seq=1)])
             for i in range(3)]
    a = behaviour_metrics(clean)["A"]
    assert a["identical_repeats_charged"] == 0
    assert a["longest_identical_run_at_exit"] == 1
    assert a["consecutive_identical_actions"] == 0


def test_every_reported_behaviour_metric_that_needs_a_definition_has_one():
    b = behaviour_metrics([_row("c1", "A")])
    definitions = b["_definitions"]
    assert set(definitions) <= set(b["A"]), sorted(set(definitions) - set(b["A"]))
    assert all(v.strip() for v in definitions.values())
    # SPEC 11.4: the judgement no predicate can make is listed for blind review
    # and is never scored by the model itself
    assert "blind review" in definitions["needs_blind_review"].lower()
    assert "per episode" in definitions["disturbed_goal_recovery_rate"]
    assert definitions["needs_blind_review"].count("never") == 1


def test_an_automatic_slot_choice_is_a_number_the_report_can_compare():
    """SPEC 5.5: an automatic resolution has to be recorded, and the arms are
    comparable only if a reader can see how much of the geometry each mode decided
    for itself."""
    rows = [_row("c1", "A", slot_resolution_fallbacks=3),
            _row("c2", "A", slot_resolution_fallbacks=1),
            _row("c1", "B", slot_resolution_fallbacks=0)]
    b = behaviour_metrics(rows)
    assert b["A"]["slot_resolution_fallbacks"] == 4 and b["B"]["slot_resolution_fallbacks"] == 0
    assert "no candidate" in b["_definitions"]["slot_resolution_fallbacks"]


# ----------------------------------------------------------------- cost --------


def test_costs_are_billed_to_the_mode_that_incurred_them():
    rows = [_row("c1", "A", skill_calls=4, http_requests=5, prompt_tokens=200,
                 completion_tokens=30, wall_time_s=8.0, sim_time_s=20.0,
                 api_errors=1, transport_retries=2, format_repairs=1),
            _row("c1", "A", 1, skill_calls=2, http_requests=3, prompt_tokens=100,
                 completion_tokens=10, wall_time_s=4.0, sim_time_s=10.0),
            _row("c1", "B", decision_rounds=9, http_requests=1)]
    c = cost_metrics(rows)
    assert set(c) == {"A", "B"}
    assert c["A"]["episodes"] == 2
    assert c["A"]["skill_calls_total"] == 6 and c["A"]["http_requests_total"] == 8
    assert c["A"]["prompt_tokens_total"] == 300 and c["A"]["completion_tokens_total"] == 40
    assert c["A"]["http_requests_per_episode"] == pytest.approx(4.0)
    assert c["A"]["wall_time_s_per_episode"] == pytest.approx(6.0)
    assert c["A"]["sim_time_s_total"] == pytest.approx(30.0)
    assert c["A"]["api_errors"] == 1 and c["A"]["transport_retries"] == 2
    assert c["A"]["format_repairs"] == 1
    assert c["B"]["episodes"] == 1 and c["B"]["http_requests_total"] == 1
    assert c["B"]["decision_rounds_total"] == 9
    # counters the provider never reported are 0, not absent, so a reader never
    # mistakes "no cost recorded" for "no cost incurred"
    assert c["B"]["api_errors"] == 0 and c["B"]["format_repairs"] == 0


def test_the_empty_mode_shape_does_not_divide_by_zero():
    """`--modes A,B,C` trimmed mid-batch leaves a mode with rows only in the
    manifest; the per-episode means must not raise."""
    rows = [_row("c1", "A", wall_time_s=None)]
    c = cost_metrics(rows)
    assert c["A"]["episodes"] == 1
    assert c["A"]["wall_time_s_total"] == 0.0 and c["A"]["wall_time_s_per_episode"] == 0.0


def _model_calls(path, *calls):
    """A `model_calls.jsonl` exactly where production leaves one: in the episode
    directory for a run, at the output root for the state-fork diagnostic."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps(c) + "\n")


def test_latency_is_read_off_the_calls_rather_than_a_mode_being_credited_for_none(tmp_path):
    """SPEC 11.4 lists 延迟 beside the token counts, and a mean alone would hide the
    one request that took a minute. The ledger is the source: the csv column counts
    requests, only the per-call records say how long each took."""
    rows = [_row("c1", "B", 0, http_requests=3), _row("c1", "B", 1, http_requests=2),
            _row("c1", "A", 0, http_requests=3)]
    run_dir = _write_run(tmp_path, rows)
    _model_calls(os.path.join(run_dir, "episodes", "c1.B.r0", "model_calls.jsonl"),
                 *[{"kind": "decision", "latency_s": v} for v in (1.0, 1.2, 50.0)])
    # a call that recorded no latency is counted as missing, never as a fast one,
    # and A's empty ledger is the shape of an episode that died before asking
    _model_calls(os.path.join(run_dir, "episodes", "c1.B.r1", "model_calls.jsonl"),
                 {"kind": "decision", "latency_s": 2.0}, {"kind": "decision"})
    _model_calls(os.path.join(run_dir, "episodes", "c1.A.r0", "model_calls.jsonl"))
    _model_calls(os.path.join(run_dir, "goal_resolutions", "goal_calls.jsonl"),
                 {"kind": "goal_parse", "latency_s": 1.093})

    lat = model_latency(run_dir, rows)
    b = lat["B"]
    assert b["calls"] == 4 and b["total_s"] == pytest.approx(54.2)
    assert b["mean_s"] == pytest.approx(13.55)
    assert (b["p50_s"], b["p95_s"], b["max_s"]) == (1.2, 50.0, 50.0), "the tail survives the mean"
    assert b["calls_without_a_recorded_latency"] == 1 and b["episodes_without_a_model_call"] == 0
    assert b["by_kind"]["decision"]["calls"] == 4
    a = lat["A"]
    assert a["calls"] == 0 and a["mean_s"] is None and a["total_s"] is None, \
        "a rule control is not a free perfect latency, it made no measurement"
    assert a["episodes_without_a_model_call"] == 1
    # the shared goal parse stays out of the decision numbers: it is billed once per
    # case and every mode reads it
    assert lat["goal_parse"]["calls"] == 1 and lat["goal_parse"]["max_s"] == 1.093


def test_the_diagnostic_reports_its_request_latency_and_says_so_when_it_made_none(tmp_path):
    summary = {"policy_label": "deepseek", "policy_provider": "deepseek",
               "action_fit_rate": 0.5, "contexts_requested": 2, "contexts_fitted": 1,
               "contexts_answered": 2, "action_fit_rate_of_answers": 0.5,
               "misses_by_kind": {"wrong_choice": 1}, "pairs_assessed": 1,
               "pairs_with_both_arms_fitting": 0, "pairs_where_state_changed_behaviour": 0,
               "pairs_requiring_a_difference": 1, "pairs_that_did_not_use_the_distinction": [],
               "contexts_naming_a_candidate": 0,
               "pairs_where_candidate_choice_went_untested": [], "synthetic_arms": 0,
               "provider_counters": {"http_requests": 2, "prompt_tokens": 900,
                                     "completion_tokens": 60, "api_errors": 0,
                                     "transport_retries": 1, "format_repairs": 0},
               "unreachable_pairs": [], "diagnostic_only_no_physics_executed": True,
               "code": {}, "definitions": {}}
    with open(tmp_path / "state_utilization.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "contexts": []}, f)
    _model_calls(str(tmp_path / "model_calls.jsonl"),
                 *({"kind": "decision", "latency_s": v} for v in (1.4, 46.0)))

    su = state_utilization(str(tmp_path))
    assert su["provider_latency"]["calls"] == 2 and su["provider_latency"]["max_s"] == 46.0
    assert su["provider_latency"]["total_s"] == pytest.approx(47.4)
    text = render_markdown({"run_id": "unit", "planned_runs": 0, "cases": 0, "modes": [],
                            "state_utilization": su})
    assert "wall time per request" in text and "46.0 s" in text

    # the rule control left no ledger: the report must not leave the reader to
    # infer that a zero means "instant" rather than "no request was made"
    (tmp_path / "model_calls.jsonl").unlink()
    summary["provider_counters"] = {k: 0 for k in summary["provider_counters"]}
    with open(tmp_path / "state_utilization.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "contexts": []}, f)
    control = state_utilization(str(tmp_path))
    assert control["provider_latency"]["calls"] == 0
    text = render_markdown({"run_id": "unit", "planned_runs": 0, "cases": 0, "modes": [],
                            "state_utilization": control})
    assert "no request was made" in text


def test_failures_are_attributed_to_the_code_the_episode_reported_not_to_a_guess():
    rows = [_row("c1", "B"),
            _row("c2", "B", outcome="budget_exceeded", failure_type="wall_clock_exceeded"),
            _row("c3", "B", outcome="failed", failure_type="grasp_miss"),
            _row("c4", "B", outcome="failed", failure_type="grasp_miss"),
            _row("c5", "B", outcome="infrastructure_error", events_unfired=1),
            _row("c6", "B", outcome="blocked", failure_type=None)]
    f = failure_attribution(rows)["B"]
    assert f["episodes"] == 6, "the successes are in the denominator of the mode too"
    causes = f["not_success_by_reported_cause"]
    assert causes == {"grasp_miss": 2, "wall_clock_exceeded": 1, "infrastructure_error": 1,
                      "blocked": 1}
    assert list(causes)[0] == "grasp_miss", "the table is ordered by how often it happened"
    assert set(causes) == {"grasp_miss", "wall_clock_exceeded", "infrastructure_error", "blocked"}
    assert f["infrastructure"] == 1
    assert f["event_not_fired"] == 1


def test_a_failure_with_no_reported_cause_is_labelled_unspecified():
    f = failure_attribution([_row("c1", "A", outcome="", failure_type=None)])["A"]
    assert f["not_success_by_reported_cause"] == {"unspecified": 1}


def test_a_success_is_never_attributed_even_when_its_cause_field_is_filled():
    """An episode that hit a false finish and then genuinely completed is a
    success; re-classifying it by a stale code would inflate a failure bucket."""
    rows = [_row("c1", "B", outcome="success", failure_type="grasp_miss", false_finish_attempts=1)]
    f = failure_attribution(rows)["B"]
    assert f["not_success_by_reported_cause"] == {}
    assert f["episodes"] == 1


# --------------------------------------------------------------- the table -----


def test_load_rows_coerces_the_csv_back_into_typed_values(tmp_path):
    src = _row("c1", "B", 1, outcome="repeated_invalid", identical_invalid_attempts=2,
               events_unfired=1, agent_claims=True,
               events=[_ctx(("obj_red_1", "tray_left", "false"))])
    rows = load_rows(_write_run(tmp_path, [src]))
    assert len(rows) == 1
    r = rows[0]
    assert r["independent_complete_success"] is False and r["agent_claims_success"] is True
    assert r["identical_invalid_attempts"] == 2.0 and r["objects_total"] == 2
    assert r["sim_time_s"] == 10.0
    assert r["_events"][0]["type"] == "decision_context"
    assert r["_summary"]["result"]["provider_counters"]["api_errors"] == 0
    assert behaviour_metrics(rows)["B"]["agent_claims_vs_independent"] == {
        "agent_successes": 1, "independent_successes": 0}


def test_a_row_whose_episode_directory_is_missing_is_still_a_row(tmp_path):
    """The missing directory is usually the crash that broke the run; dropping the
    row would censor exactly the sample a reader needs to see."""
    rows = load_rows(_write_run(tmp_path, [_row("c1", "B", outcome="infrastructure_error")],
                               with_dirs=False))
    assert len(rows) == 1
    assert rows[0]["_events"] == [] and rows[0]["_summary"] == {}
    assert failure_attribution(rows)["B"]["infrastructure"] == 1
    assert behaviour_metrics(rows)["B"]["needs_blind_review"] == []


def test_a_run_directory_with_no_table_is_an_error_rather_than_an_empty_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_rows(str(tmp_path))


def test_absent_numbers_read_as_none_rather_than_a_free_zero(tmp_path):
    """`0` would claim a cost was measured and came out free, so the measured
    columns read as None. The object counters are the deliberate exception: they
    are integers the aggregation adds and compares, where a missing cell is the
    same row as no objects done."""
    rows = load_rows(_write_run(tmp_path, [_row("c1", "B")]))
    assert rows[0]["wall_time_s"] == 5.0 and rows[0]["prompt_tokens"] == 100
    partial = dict(rows[0], episode_id="c1.B.r9", repeat=9, prompt_tokens="",
                   sim_time_s=None, wall_time_s=None, objects_completed="", objects_total="")
    merged = load_rows(_write_run(tmp_path, [partial]))
    assert merged[0]["prompt_tokens"] is None and merged[0]["sim_time_s"] is None
    assert merged[0]["objects_completed"] == 0 and merged[0]["objects_total"] == 0
    # a row whose times are unknown must not poison a mode's total
    assert cost_metrics(merged)["B"]["wall_time_s_total"] == 0.0


def test_build_report_comparisons_run_on_case_rates_not_episode_counts(tmp_path):
    manifest = {"run_id": "synthetic_run", "planner": "rule", "offline": True,
                "schema_version": "3", "frozen": {"matches": True, "enforced": True,
                                                  "detail": "abc123def456"},
                "code": {"commit": "0123456789abcdef0123456789abcdef", "dirty": True,
                         "dirty_diff_sha256": "ffff000011112222"},
                "model": {"provider": "rule", "model": "rule-interpreter"}}
    rows = [_row("c1", "A", r, independent=r == 0) for r in range(3)] \
        + [_row("c1", "B", r) for r in range(3)] \
        + [_row("c2", "A", independent=False), _row("c2", "B", independent=False)]
    run_dir = _write_run(tmp_path, rows, manifest=manifest)
    report = build_report(run_dir)

    assert report["planned_runs"] == 8 and report["cases"] == 2
    assert report["modes"] == ["A", "B"]
    assert report["run_id"] == "synthetic_run" and report["offline"] is True
    assert report["code"] == {"commit": manifest["code"]["commit"], "dirty": True,
                              "dirty_diff_sha256": "ffff000011112222"}
    assert report["frozen"]["detail"] == "abc123def456"
    # per case, not per episode: 4 of 8 episodes succeeded, which is one case up
    # and one unchanged, not a 50% improvement
    assert report["success_rate_by_case"] == {"c1|A": pytest.approx(1 / 3), "c1|B": 1.0,
                                              "c2|A": 0.0, "c2|B": 0.0}
    paired = report["paired_comparisons"]["B-A"]
    assert paired["paired_cases"] == 2 and paired["excluded_cases"] == []
    assert paired["mean"] == pytest.approx(1 / 3, abs=1e-4)
    assert paired["reads_as"] == "indistinguishable from zero on this sample"
    assert report["by_subset"]["clean"]["A"]["episodes"] == 4
    assert report["by_subset"]["clean"]["A"]["independent_successes"] == 1
    assert report["by_object_count"]["2"]["B"]["episodes"] == 4
    assert report["event_coverage"]["episodes_with_declared_events"] == 0
    assert report["event_coverage"]["episodes_where_a_declared_event_never_fired"] == 0
    assert "planned_runs is the denominator" in report["reliability_denominator_note"]
    assert "0 of them are machinery failures" in report["reliability_denominator_note"]


def test_render_markdown_refuses_to_hide_an_undefined_comparison():
    report = {"run_id": "unit_test_run", "run_dir": "/tmp/unit", "planner": "rule",
              "offline": True, "code": {"commit": "deadbeef" * 3, "dirty": True},
              "frozen": {"detail": "abc123abc123", "matches": True, "enforced": True},
              "planned_runs": 4, "cases": 2, "modes": ["A", "B"],
              "by_subset": {"clean": {"A": {"episodes": 2, "independent_successes": 1,
                                            "wilson95_descriptive": [0.1, 0.9]}}},
              "paired_comparisons": {"B-A": {"paired_cases": 0, "excluded_cases": ["c1", "c2"],
                                             "mean": None, "ci95": None,
                                             "note": "no configuration has both arms",
                                             "comparison": "B-A"}},
              "cost": {"A": {"skill_calls_total": 6, "decision_rounds_total": 5,
                             "http_requests_total": 8, "prompt_tokens_total": 300,
                             "completion_tokens_total": 40, "wall_time_s_total": 12.0,
                             "sim_time_s_total": 30.0, "api_errors": 1}},
              "failure_attribution": {"A": {"episodes": 2, "infrastructure": 1,
                                            "event_not_fired": 1,
                                            "not_success_by_reported_cause": {"grasp_miss": 1}}},
              "behaviour": {"_definitions": {}, "A": {
                  "regressed_goals": ["obj_red_1->tray_left"],
                  "disturbed_goals_taken_back_into_account": [],
                  "disturbed_goal_recovery_rate": 0.0,
                  "redundant_replacements_of_satisfied_goals": 0,
                  "consecutive_identical_actions": 1, "identical_repeats_charged": 3,
                  "longest_identical_run_at_exit": 4,
                  "rejected_decisions": 2, "false_finish_attempts": 1, "model_errors": 0,
                  "agent_claims_vs_independent": {"agent_successes": 2,
                                                  "independent_successes": 1}}}}
    text = render_markdown(report)
    assert "planned runs `4`" in text
    assert "undefined" in text and "c1,c2" in text, "the row must appear and say why it cannot"
    assert "dirty=True" in text and "abc123abc123" in text
    assert "offline: not a model result" in text, "a rule harness must not read as a model score"
    assert "[0.100, 0.900]" in text and "Wilson (descriptive only)" in text
    assert "runtime total 3, longest run at exit 4" in text
    assert "machinery failures counted as failures, not removed: 1" in text
    assert "declared event never fired in 1 episode(s) (kept)" in text
    assert "agent claims 2 vs independent 1" in text, "the gap is itself a reported number"
