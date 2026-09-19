"""The SPEC 11.3 state-utilization scoring, on contexts real episodes passed through.

What each test here guards, and why it is not tautological:

* The 12 frozen pairs must each *reach* two arms, and every arm name in a pair spec
  must be one a selector produces. A pair whose base case never shows the declared
  difference scores nothing and reports nothing, and a spec naming an arm nobody
  builds makes its acceptable labels unreachable — either way the rate falls with
  nothing in any episode changing.
* Every acceptable-action label must name a predicate that exists, for the same
  reason.
* The two arms of a presentation control must be scored from the *same* decision
  shape: an order-independent policy is not flagged, a first-entry policy is. That
  pair exists only to catch a policy reading the list instead of the checks, so a
  scorer that cannot tell those two apart has no teeth.
* A marked transform must not reach back into the walked episode. `WorldState`
  containers are shared by reference, and a transform that edited the source
  context would leave the pair's real arm describing a world that never happened.
* The offline control policy's two misses are named, not counted away: continuing
  to place where every offered slot has already failed, and releasing a hold it can
  no longer name, are the things this diagnostic exists to see.
* A policy that names no slot is not accused of naming the wrong one. The occupied
  id and an absent id compared equal once, which would have made every unnamed
  placement a violation of the pair it was answering. And the mirror of that: a slot
  named only inside the skill's argument bag is the same claim as the canonical
  field, so the pair's rejection still fires — the executor honoured both spellings
  while the scorer read one, which would have scored a violation as a fit.
* The rate reaches the report with its denominators and its qualifications, and a
  diagnostic that was not run reads as "not run" rather than as a zero. The command
  line is exercised as the way this is actually produced, including that a partial
  run does not report the pairs it skipped as unreachable.
* The cost line bills each context the request it made, and a context that produced
  no answer at all is kept in the denominator but named apart from one a policy
  answered badly — otherwise a flaky transport reads as a weak policy.

Nothing here executes a fork: `diagnostic_only_no_physics_executed` is asserted in
the artifact, and no episode outcome depends on any of it.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation.sources import RulePolicySource, ScriptedSource
from embodied_agent.evaluation.state_utilization import (
    DIFFERENCE_KINDS,
    LABELS,
    _score_error,
    aggregate,
    build_forks,
    run_state_utilization,
    score_fork,
    walk_case,
)
from embodied_agent.evaluation.tasks import STATE_PAIR_SPECS, find_case

# The two arms the deterministic control policy is expected to miss. Named rather
# than counted because a rate that moved on its own would not say which
# measurement changed.
CONTROL_MISSES = {("sp08", "full"), ("sp09", "unknown")}


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    """The whole frozen fork set, scored once against the control policy and once
    against a policy that answers every context the same way."""
    forks, missing = build_forks()
    out = str(tmp_path_factory.mktemp("state_util"))
    blind_out = str(tmp_path_factory.mktemp("state_util_blind"))
    payload = run_state_utilization(lambda: RulePolicySource(), out_root=out,
                                    label="rule_control", forks=forks)
    with open(os.path.join(out, "state_utilization.json"), encoding="utf-8") as f:
        artifact = json.load(f)
    assert payload == artifact, "what the driver returned is not what it wrote down"
    blind = run_state_utilization(lambda: ScriptedSource([("finish", None)]),
                                  out_root=blind_out, label="always_finish", forks=forks)
    return {"forks": {f.pair_id: {} for f in forks}, "by_arm": {(f.pair_id, f.arm): f
                                                                for f in forks},
            "missing": missing, "summary": payload["summary"], "artifact": artifact,
            "out": out, "blind": blind}


@pytest.fixture(scope="module")
def sp06():
    """The candidate-order control pair on its own: one walked episode, two arms."""
    return {f.arm: f for f in build_forks(only=["sp06"])[0]}


def _by_round(case_id):
    """A fresh walk of a frozen case, keyed by the round each context was captured on."""
    return {c.round_index: c for c in walk_case(case_id)}


def _place(ctx, object_id, target_id, candidate_id=None):
    return ScriptedSource([("execute", ("place", {"object_id": object_id,
                                                 "target_id": target_id}, candidate_id))]).decide(ctx)


# ------------------------------------------------------------- coverage --------


def test_every_frozen_pair_reaches_both_arms_and_nothing_is_silently_dropped(scored):
    summary, artifact = scored["summary"], scored["artifact"]
    assert summary["unreachable_pairs"] == [] and scored["missing"] == []
    assert summary["contexts_requested"] == 2 * len(STATE_PAIR_SPECS)
    assert len(artifact["contexts"]) == summary["contexts_requested"]
    arms = {}
    for r in artifact["contexts"]:
        arms.setdefault(r["pair_id"], set()).add(r["arm"])
    assert sorted(arms) == [s["pair_id"] for s in STATE_PAIR_SPECS]
    for spec in STATE_PAIR_SPECS:
        named = (set(spec["accept"]) | set(spec["reject"])) - {"any"}
        # every arm a spec names must exist (an arm nobody builds makes its frozen
        # labels unreachable), and a pair must be a pair: exactly two arms. The
        # rest of the names come from the selectors, and a spec is free to label
        # only the side it needs a rejection for.
        assert named <= arms[spec["pair_id"]], f"{spec['pair_id']}: a spec arm no selector builds"
        assert len(arms[spec["pair_id"]]) == 2, f"{spec['pair_id']}: not a two-arm pair"


def test_every_frozen_label_names_a_predicate_that_exists():
    for spec in STATE_PAIR_SPECS:
        for group in ("accept", "reject"):
            for arm, labels in spec[group].items():
                for label in labels:
                    assert label in LABELS, f"{spec['pair_id']}/{arm}: unknown label {label}"


def test_every_difference_is_classified_for_whether_it_requires_a_change():
    assert set(DIFFERENCE_KINDS) == {s["difference"] for s in STATE_PAIR_SPECS}


# ------------------------------------------------------------------- fit -------


def test_the_control_policy_misses_exactly_the_two_forks_it_should(scored):
    summary, artifact = scored["summary"], scored["artifact"]
    missed = {(r["pair_id"], r["arm"]) for r in artifact["contexts"] if not r["fit"]}
    assert missed == CONTROL_MISSES
    total = len(artifact["contexts"])
    assert summary["action_fit_rate"] == round((total - len(CONTROL_MISSES)) / total, 4)
    # and each miss carries no accepted label at all, rather than an unreadable
    # answer that happened to be blamed by a comparison nobody intended
    for pair_id, arm in CONTROL_MISSES:
        row = next(r for r in artifact["contexts"]
                   if r["pair_id"] == pair_id and r["arm"] == arm)
        assert row["accepted_because"] == [] and row["error"] is None
    full = scored["by_arm"][("sp08", "full")]
    assert all(c.occupancy_ok.value == "fail" for c in full.context.candidates)


def test_a_fork_blind_policy_is_not_credited_for_the_forks(scored):
    """Same contexts, same scorer, one answer for all of them.

    Without this, a high fit rate could be read as the scorer accepting anything:
    the frozen acceptable sets are wide, so the control that shows the scorer can
    say *no* is part of the measurement, not an afterthought.
    """
    blind = scored["blind"]
    assert blind["summary"]["contexts_requested"] == scored["summary"]["contexts_requested"]
    assert {(r["pair_id"], r["arm"]) for r in blind["contexts"] if r["fit"]} == \
        {("sp12", "all_satisfied")}


def test_a_policy_that_never_names_a_slot_is_not_accused_of_naming_the_wrong_one(scored):
    for r in scored["artifact"]["contexts"]:
        assert r["named_a_candidate"] is False, r["pair_id"]
        # an absent id and the occupied id compared equal once, which would have
        # made every unnamed placement a violation of the pair it was answering
        assert "place_with_occupied_candidate" not in r["labels"], r["pair_id"]
    # the three pairs that are about *which slot* say so rather than reporting a
    # fit that tested no geometry choice
    assert scored["summary"]["pairs_where_candidate_choice_went_untested"] == ["sp05", "sp06",
                                                                              "sp08"]


def test_a_slot_named_only_in_the_arguments_is_still_scored_as_the_slot_named(scored):
    """The scorer reads the canonical field while the executor honoured either one.

    A model that put the occupied id in the argument bag was recorded as naming
    nothing, so the rejection this pair froze could not fire and the arm scored a fit
    while violating it — measured online, where two rounds named it that way.
    """
    fork = scored["by_arm"][("sp05", "occupied")]
    ctx, held = fork.context, fork.context.world.held_object
    args_only = ScriptedSource([("execute", ("place", {"object_id": held,
                                                      "target_id": fork.focus_target,
                                                      "candidate_id": fork.focus_candidate},
                                            None))]).decide(ctx)
    record = score_fork(fork, args_only)
    assert record["named_a_candidate"] is True
    assert record["rejected_because"] == ["place_with_occupied_candidate"], \
        "the pair's own rejection must be reachable from either spelling"
    assert record["fit"] is False
    # and the guard the previous test fixes still holds: naming nothing is not naming
    # the occupied slot
    unnamed = score_fork(fork, _place(ctx, held, fork.focus_target))
    assert unnamed["named_a_candidate"] is False
    assert "place_with_occupied_candidate" not in unnamed["labels"]


# --------------------------------------------------------- presentation --------


def _feasible_for(ctx, entity):
    return next(c for c in ctx.candidates if c.entity_id == entity
                and all(getattr(c, f).value == "ok" for f in
                        ("params_ok", "boundary_ok", "occupancy_ok", "reachability_ok")))


def test_an_order_independent_policy_is_not_flagged_by_the_reordering_control(sp06):
    as_offered, reversed_ = sp06["order_as_offered"], sp06["order_reversed"]
    held = as_offered.context.world.held_object
    stable = _feasible_for(as_offered.context, held)
    rows = [score_fork(f, _place(f.context, held, stable.target_id, stable.candidate_id))
            for f in (as_offered, reversed_)]
    assert [r["fit"] for r in rows] == [True, True]
    assert aggregate(rows, [as_offered, reversed_])["pairs"]["sp06"][
        "changed_under_reordering"] is False


def test_a_first_entry_policy_is_flagged_by_the_reordering_control(sp06):
    as_offered, reversed_ = sp06["order_as_offered"], sp06["order_reversed"]
    held = as_offered.context.world.held_object
    rows = [score_fork(f, _place(f.context, held, f.context.candidates[0].target_id,
                                 f.context.candidates[0].candidate_id))
            for f in (as_offered, reversed_)]
    assert rows[0]["asked"]["execute"]["candidate_id"] != \
        rows[1]["asked"]["execute"]["candidate_id"]
    assert aggregate(rows, [as_offered, reversed_])["pairs"]["sp06"][
        "changed_under_reordering"] is True


# ------------------------------------------------------ transforms are copies ---


def test_a_transform_leaves_the_walked_episode_it_came_from_alone(sp06):
    as_offered, reversed_ = sp06["order_as_offered"], sp06["order_reversed"]
    real_order = [c.candidate_id for c in as_offered.context.candidates]
    assert [c.candidate_id for c in reversed_.context.candidates] == list(reversed(real_order))
    # a fresh episode of the same frozen case is the untouched reference: had the
    # transform edited shared containers, the real arm would have drifted as well
    # (`context_id` carries a random suffix, so the round is what identifies a
    # position in an episode across two walks of one case)
    reference = _by_round(as_offered.case_id)[as_offered.context.round_index]
    assert [c.candidate_id for c in reference.candidates] == real_order
    # the whole reading of the world, not just its order: `wall_time` is the one
    # field two walks of one frozen case cannot share
    assert reference.world.model_dump(exclude={"wall_time"}) == \
        as_offered.context.world.model_dump(exclude={"wall_time"})


def test_a_presentation_transform_changes_no_reading_of_the_state(sp06):
    as_offered, reversed_ = sp06["order_as_offered"], sp06["order_reversed"]
    assert reversed_.synthetic is True and reversed_.provenance.startswith("transform:")
    assert (reversed_.context.state_version, reversed_.context.observation_ref) == \
        (as_offered.context.state_version, as_offered.context.observation_ref)
    assert [(p.entity_id, p.value.value) for p in reversed_.context.progress] == \
        [(p.entity_id, p.value.value) for p in as_offered.context.progress]


def test_every_arm_reports_the_progress_its_own_world_implies(scored):
    """Each of the 24 contexts is internally consistent with its own snapshot.

    A transform that moved a body changes what `placed` can report, so the derived
    verdicts have to follow it; a transform that hand-edited `progress` (or left
    the old verdicts standing) fails here, on the synthetic arms as much as the
    real ones. Compared as a multiset because two of the pairs are *about* the
    order entries are listed in: their verdicts must stay put, their positions
    must not.
    """
    for (pair_id, arm), fork in scored["by_arm"].items():
        derived = RuntimeVerifier(fork.context.world,
                                  find_case(fork.case_id).verify).progress(fork.context.goal)
        assert sorted((p.entity_id, p.target_id, p.value.value)
                      for p in fork.context.progress) == sorted(
            (p.entity_id, p.target_id, p.value.value) for p in derived), f"{pair_id}/{arm}"


# ------------------------------------------------------------------ artifact ---


def test_the_report_surfaces_the_rate_or_says_it_was_not_measured(scored, tmp_path):
    """11.4's 状态分叉动作适配率 has to reach the report, and its absence must not
    read as a zero.

    A rate copied into a report without its denominators is the shape of a
    misleading number: the reader cannot see that 24 contexts, 6 marked synthetic
    arms and 3 untested slot-choice pairs are inside it.
    """
    from embodied_agent.evaluation import report as R

    absent = R.state_utilization(str(tmp_path / "no_run_yet"))
    assert absent["ran"] is False and "state-util" in absent["note"]

    run_dir = str(tmp_path / "run")
    os.makedirs(run_dir, exist_ok=True)
    shutil.copy(os.path.join(scored["out"], "state_utilization.json"), run_dir)
    su = R.state_utilization(run_dir)
    summary = scored["summary"]
    assert su["ran"] is True and su["diagnostic_only_no_physics_executed"] is True
    for field in R.STATE_UTIL_FIELDS:
        assert su[field] == summary[field], field
    text = R.render_markdown({"run_dir": run_dir, "planned_runs": 0, "cases": 0,
                              "modes": [], "state_utilization": su})
    assert "State-fork action fit" in text and "nothing executed" in text
    assert f"{summary['action_fit_rate']}" in text
    for pair_id in summary["pairs_where_candidate_choice_went_untested"]:
        assert pair_id in text, "the qualification is missing from the readable report"


def test_the_cli_entry_point_measures_the_pairs_it_was_asked_about(tmp_path):
    """`state-util` is how the diagnostic is actually run, so it is tested as run."""
    from embodied_agent import cli
    from embodied_agent.evaluation import report as R

    out = str(tmp_path / "diag")
    assert cli.main(["state-util", "--only", "sp01", "--out", out]) == cli.EXIT_OK
    with open(os.path.join(out, "state_utilization.json"), encoding="utf-8") as f:
        summary = json.load(f)["summary"]
    # a partial run reports the subset it measured, and does not list the eleven
    # pairs it was never asked for as unreachable
    assert summary["contexts_requested"] == 2 and summary["unreachable_pairs"] == []
    assert summary["policy_provider"] == "rule_policy"
    assert summary["provider_counters"]["http_requests"] == 0, \
        "the offline control asked no provider"
    assert R.state_utilization(out)["ran"] is True


def test_the_artifact_says_what_it_measured_and_that_it_measured_no_physics(scored):
    summary, artifact, out = scored["summary"], scored["artifact"], scored["out"]
    assert summary["diagnostic_only_no_physics_executed"] is True
    assert summary["policy_provider"] == "rule_policy"
    assert summary["code"]["commit"], "a diagnostic without a commit cannot be re-run"
    assert artifact["summary"]["action_fit_rate"] == summary["action_fit_rate"]
    for r in artifact["contexts"]:
        assert {"pair_id", "arm", "provenance", "synthetic", "context_id", "observation_ref",
                "state_version", "focus", "labels", "acceptable_labels", "rejected_labels",
                "accepted_because", "rejected_because", "fit", "error"} <= set(r)
        assert r["context_id"].startswith("ctx"), "each verdict names the context it judged"
    assert os.path.exists(os.path.join(out, "state_utilization.json"))
    text = json.dumps(artifact)
    for secret in ("DEEPSEEK_API_KEY", "api_key", "Authorization", "sk-"):
        assert secret not in text, secret


def test_the_diagnostic_bills_each_context_once_not_every_context_ever(scored, tmp_path):
    """24 sources over one shared adapter: what a provider reports is cumulative, so
    summing the lifetime counters charges each context for all the traffic before it
    — the number that hides a retry (SPEC 6.2) reported as a cost."""
    pair = [("sp01", "held"), ("sp01", "released")]
    forks = [scored["by_arm"][key] for key in pair]
    policies = {f.case_id: RulePolicySource(find_case(f.case_id).verify) for f in forks}

    class SharedAdapterSource:
        """A fresh source per context, all reading one growing counter."""

        provider = "shared_counter_control"
        total = 0

        def __init__(self):
            pass

        @property
        def http_requests(self) -> int:
            return SharedAdapterSource.total

        @property
        def prompt_tokens(self) -> int:
            return 100 * SharedAdapterSource.total

        def decide(self, ctx):
            SharedAdapterSource.total += 1
            return policies[ctx.task.task_id].decide(ctx)

    payload = run_state_utilization(SharedAdapterSource, out_root=str(tmp_path),
                                    label="shared", forks=forks)
    counters = payload["summary"]["provider_counters"]
    # 2 requests, 2x100 tokens: adding what each source reports at the end would
    # have said 3 requests and 300 tokens, since those counters are lifetime totals
    assert counters["http_requests"] == 2, counters
    assert counters["prompt_tokens"] == 200, counters
    assert [r["provider_cost"]["http_requests"] for r in payload["contexts"]] == [1, 1], \
        "one request per context is the whole claim SPEC 11.3 makes about cost"


def test_a_context_that_was_never_answered_is_not_read_as_a_bad_choice(scored):
    """The rate's denominator is the 24 contexts (SPEC 11.5: nothing is quietly
    excluded), but *why* a context is missing has to be separable by a reader."""
    fork = scored["by_arm"][("sp01", "held")]
    timeout = _score_error(fork, "LLMError: request failed after 3 attempts: "
                                 "TimeoutError: timed out")
    schema = _score_error(fork, "DecisionSchemaError: execute.args.grasp_candidate_index: "
                               "Input should be a valid string")
    other = _score_error(fork, "AttributeError: nothing")
    assert timeout["miss_kind"] == "request_failed", timeout
    assert schema["miss_kind"] == "schema_invalid", schema
    assert other["miss_kind"] == "source_error", other

    answered = score_fork(fork, RulePolicySource(find_case(fork.case_id).verify)
                          .decide(fork.context))
    assert answered["fit"] is True and answered["miss_kind"] is None

    agg = aggregate([answered, timeout, schema], [fork])
    assert agg["misses_by_kind"] == {"request_failed": 1, "schema_invalid": 1}, agg
    assert agg["contexts_requested"] == 3 and agg["contexts_answered"] == 1
    assert agg["action_fit_rate"] == round(1 / 3, 4), "the two un-answered stay in the rate"
    assert agg["action_fit_rate_of_answers"] == 1.0
    # a wrong choice is not machinery: the rate and the rate-of-answers move together
    wrong = dict(answered, fit=False, miss_kind="wrong_choice")
    agg2 = aggregate([answered, wrong], [fork])
    assert agg2["misses_by_kind"] == {"wrong_choice": 1}
    assert agg2["action_fit_rate"] == agg2["action_fit_rate_of_answers"] == 0.5
