"""Contract: the episodic pair protocol can put a model on the reader seat.

SPEC-v0.3 §11's Memory group (M1–M4) is defined by MEM-1 over the pair protocol: a writer
episode leaves residue, a reader episode queries it, and the two arms differ in whether
retrieval happens. The protocol's only entry point hard-coded `planner_kind="rule"`, so M1–M4
had **no reading on a model decision source at all** — a structural gap, not a measurement
that was skipped. The v0.3 report's §13.2 row 2 is partially met for exactly this reason.

The awkward part is that `MemoryPolicy` is itself a decision maker: it answers from
`ctx.model_payload()` and is only a *control* while the rule planner is the decision source.
That is why `run_group` refuses `policy="memory"` alongside a model planner. So the fix is not
to add a `--policy` flag; it is to **derive** the policy from the seat, so the incoherent batch
is unrepresentable rather than documented as unsupported.

Three things are asserted here, and the third is the one that would be easy to get wrong:

1. the rule seat keeps `policy="memory"` and costs nothing;
2. a model seat drops the policy, keeps the store and the arm, and needs a named seat;
3. a model seat is **not** interchangeable with a rule seat, and `measure_episodic_pairs`
   carries the seat into the result so a comparison cannot silently attribute the model's own
   randomness to memory.

Nothing here dials a provider: the model seat is checked for refusal and wiring, not run.
"""
from __future__ import annotations

import json
import os

import pytest

from embodied_agent.evaluation import episodic_runs
from embodied_agent.evaluation.episodic_runs import (EM_ARMS, measure_episodic_pairs,
                                                     run_episodic_pairs)


# ------------------------------------------------------------- the policy derivation ----
def _captured(tmp_path, monkeypatch, **kwargs):
    """Run the driver with `run_group` stubbed, and return (calls, artifact).

    `run_group` is the only seam that would dial a provider, so the seat is checked by looking
    at what the driver hands it. Stubbing it here is also what makes the wiring testable at all:
    asserting on the arguments is asserting on the thing that matters, not on a log line.
    """
    calls = []

    def fake_run_group(set_name, **kw):
        calls.append(dict(kw, set_name=set_name))
        root = os.path.join(kw["out_root"], "ep", f"r{len(calls)}")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "episode_summary.json"), "w", encoding="utf-8") as f:
            json.dump({"episode_id": f"e{len(calls)}"}, f)
        with open(os.path.join(root, "events.jsonl"), "w", encoding="utf-8") as f:
            f.write("")
        return {"root": root, "run_id": f"r{len(calls)}", "rows": 0}

    monkeypatch.setattr(episodic_runs, "run_group", fake_run_group)
    pairs = [{"pair_id": "p1", "condition": "c", "writer": "w", "reader": "r"}]
    out = os.path.join(str(tmp_path), "pairs")
    artifact = run_episodic_pairs(out, pairs=pairs, arms=("full",), **kwargs)
    return calls, artifact, out


def test_the_rule_seat_keeps_the_memory_policy(tmp_path, monkeypatch):
    calls, artifact, _ = _captured(tmp_path, monkeypatch)
    assert artifact["planner"] == "rule"
    assert artifact["policy"] == "memory"
    # MemoryPolicy is the reader, so it must be handed a store or it answers from an empty one
    assert all(c["experience_store"] is not None for c in calls)


def test_the_rule_seat_is_the_default_so_nothing_changes_for_existing_callers(tmp_path, monkeypatch):
    calls, artifact, _ = _captured(tmp_path, monkeypatch)
    assert all(c["planner_kind"] == "rule" for c in calls)
    assert all(c["model_config"] is None for c in calls)


# ------------------------------------------------------------------- the model seat ----
def test_a_model_seat_drops_the_policy_and_keeps_the_store(tmp_path, monkeypatch):
    calls, artifact, _ = _captured(tmp_path, monkeypatch, planner_kind="deepseek",
                                   model_config="configs/models/bst_text_agnes.yaml")
    assert artifact["planner"] == "deepseek"
    assert artifact["policy"] is None
    assert artifact["model_config"] == "configs/models/bst_text_agnes.yaml"
    # the store is the object of the measurement; a model seat must not lose it
    assert all(c["experience_store"] is not None for c in calls)
    assert all(c["planner_kind"] == "deepseek" for c in calls)


def test_a_model_seat_still_applies_the_arm(tmp_path, monkeypatch):
    _calls, artifact, _ = _captured(tmp_path, monkeypatch, planner_kind="deepseek",
                                     model_config="cfg.yaml")
    # one arm was asked for, and the ablation record travels with each episode
    assert [r["arm"] for r in artifact["batches"]] == ["full"]
    assert all(r["batches"]["writer"] and r["batches"]["reader"] for r in artifact["batches"])


def test_a_model_seat_without_a_named_seat_is_refused(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="model-config"):
        _captured(tmp_path, monkeypatch, planner_kind="deepseek")


def test_a_test_double_planner_is_refused(tmp_path, monkeypatch):
    # a pairs_run.json naming 'fixture' could not be told apart from one naming a real seat
    with pytest.raises(ValueError, match="test double"):
        _captured(tmp_path, monkeypatch, planner_kind="fixture")


def test_the_incoherent_batch_is_unrepresentable_not_merely_documented(tmp_path, monkeypatch):
    # There is no parameter through which a caller could ask for a model seat AND a policy that
    # is itself a decision maker. If one is ever added, this test is the thing that should fail
    # loudly rather than the other way round.
    import inspect
    sig = inspect.signature(run_episodic_pairs)
    assert "policy" not in sig.parameters


def test_the_refusal_happens_before_any_store_is_written(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(episodic_runs, "run_group",
                        lambda *a, **k: calls.append(k) or {"root": "", "run_id": "", "rows": 0})
    with pytest.raises(ValueError):
        run_episodic_pairs(os.path.join(str(tmp_path), "p"), planner_kind="deepseek")
    assert calls == [], "a refused seat must not have started a single episode"


# --------------------------------------------------- the seat is part of the result ----
def _fake_ledger(root, planner, policy, model_config=None):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "pairs_run.json"), "w", encoding="utf-8") as f:
        json.dump({"kind": "episodic_pairs_run", "root": root, "planner": planner,
                   "policy": policy, "model_config": model_config, "batches": []}, f)
    return root


def test_measure_carries_the_seat(tmp_path, monkeypatch):
    root = _fake_ledger(os.path.join(str(tmp_path), "rule"), "rule", "memory")
    monkeypatch.setattr(episodic_runs, "EM_PAIRS", [])
    out = measure_episodic_pairs(root)
    assert out["planner"] == "rule"
    assert out["policy"] == "memory"
    assert "differ only in memory" in out["seat_note"]


def test_measure_warns_that_a_model_seat_is_not_a_rule_seat(tmp_path, monkeypatch):
    # the confound: on a model seat the two arms differ in memory AND in the draw, so a
    # within-pair difference cannot be attributed to memory alone
    root = _fake_ledger(os.path.join(str(tmp_path), "model"), "deepseek", None, "cfg.yaml")
    monkeypatch.setattr(episodic_runs, "EM_PAIRS", [])
    out = measure_episodic_pairs(root)
    assert out["planner"] == "deepseek"
    assert out["model_config"] == "cfg.yaml"
    assert "AND in the draw" in out["seat_note"]
    assert "not attributable to memory alone" in out["seat_note"]


def test_measure_on_an_older_rule_ledger_still_reads(tmp_path, monkeypatch):
    # a v0.2/v0.3 ledger written before this change has planner="rule" and no model_config;
    # measuring it must not raise, and must report the rule seat
    root = os.path.join(str(tmp_path), "old")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "pairs_run.json"), "w", encoding="utf-8") as f:
        json.dump({"kind": "episodic_pairs_run", "root": root, "planner": "rule",
                   "policy": "memory", "batches": []}, f)
    monkeypatch.setattr(episodic_runs, "EM_PAIRS", [])
    out = measure_episodic_pairs(root)
    assert out["planner"] == "rule"
    assert out["model_config"] is None


# ------------------------------------------------ MEM-1 itself has to name the seat ----
# This hop was missed the first time. `pairs_run.json` and `pairs_measured.json` both carried the
# seat, and `episodic_memory_MEM_1.json` — the artifact §11 actually publishes for the Memory
# group, the one a reader would quote — did not. Two tables, identical headers, no provenance.
def _mem1(root, monkeypatch):
    from embodied_agent.evaluation import episodic_metrics
    monkeypatch.setattr(episodic_metrics, "EM_PAIRS", [])
    return episodic_metrics.measure(root)


def test_mem1_names_the_seat(tmp_path, monkeypatch):
    from embodied_agent.evaluation import episodic_metrics
    root = _fake_ledger(os.path.join(str(tmp_path), "model"), "deepseek", None, "cfg.yaml")
    out = _mem1(root, monkeypatch)
    assert out["planner"] == "deepseek"
    assert out["model_config"] == "cfg.yaml"
    assert "AND in the draw" in out["seat_note"]
    assert "not comparable with a rule-seat table" in out["seat_note"]


def test_mem1_on_the_rule_seat_says_the_arms_differ_only_in_memory(tmp_path, monkeypatch):
    root = _fake_ledger(os.path.join(str(tmp_path), "rule"), "rule", "memory")
    out = _mem1(root, monkeypatch)
    assert out["planner"] == "rule"
    assert "differ only in memory" in out["seat_note"]


def test_mem1_on_a_ledger_with_no_seat_recorded_says_so(tmp_path, monkeypatch):
    # "not recorded" rather than a guess: an older batch was a rule reader, but saying so
    # without the ledger's word for it would be asserting something the artifact does not say.
    #
    # The assertions are anchored (`startswith`, and an explicit `not in`) on purpose. A mutation
    # check caught the weaker form: prepending a wrong claim to the note left
    # `"not recorded" in note` still true, so the test passed over a note that now asserts a
    # rule seat it has no evidence for. A substring test that survives a wrong prefix is not a
    # test.
    root = os.path.join(str(tmp_path), "nostseat")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "pairs_run.json"), "w", encoding="utf-8") as f:
        json.dump({"kind": "episodic_pairs_run", "root": root, "batches": []}, f)
    out = _mem1(root, monkeypatch)
    assert out["planner"] is None
    note = out["seat_note"]
    assert note.startswith("seat not recorded"), note
    assert "rule seat" not in note, note
    assert "differ only in memory" not in note, note


def test_mem1_refuses_a_directory_that_was_never_run(tmp_path, monkeypatch):
    # Not "degrades gracefully": `measure` reads the ledger for the batch's `task_kind` and so
    # cannot score a directory with no ledger. That refusal is the right shape — a MEM-1 table
    # over a directory nobody ran would look exactly like a table over a small run, and the
    # difference is invisible in the output. Pinned so the graceful path is not added later.
    from embodied_agent.evaluation import episodic_metrics
    root = os.path.join(str(tmp_path), "bare")
    os.makedirs(root, exist_ok=True)
    monkeypatch.setattr(episodic_metrics, "EM_PAIRS", [])
    with pytest.raises(FileNotFoundError):
        episodic_metrics.measure(root)


# ------------------------------------------------------------------- the CLI surface ----
# The parser is built inline in `cli.main`, so these are behavioural rather than a grep of the
# source: a grep would pass on a help string that no argument actually reads. What matters is
# what the CLI *accepts*.
PAIR = {"pair_id": "p1", "condition": "c", "writer": "w", "reader": "r"}


def _stub_episodes(monkeypatch):
    calls = []

    def fake_run_group(set_name, **kw):
        calls.append(kw)
        root = os.path.join(kw["out_root"], "ep", f"r{len(calls)}")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "episode_summary.json"), "w", encoding="utf-8") as f:
            json.dump({"episode_id": f"e{len(calls)}"}, f)
        open(os.path.join(root, "events.jsonl"), "w", encoding="utf-8").close()
        return {"root": root, "run_id": f"r{len(calls)}", "rows": 0}

    from embodied_agent.evaluation import episodic_tasks
    monkeypatch.setattr(episodic_runs, "run_group", fake_run_group)
    monkeypatch.setattr(episodic_runs, "EM_PAIRS", [PAIR])
    monkeypatch.setattr(episodic_runs, "print_pairs", lambda root: None)
    monkeypatch.setattr(episodic_tasks, "EM_PAIRS", [PAIR])
    return calls


def test_the_cli_accepts_the_seat_and_names_it_in_its_output(tmp_path, capsys, monkeypatch):
    from embodied_agent import cli
    calls = _stub_episodes(monkeypatch)
    rc = cli.main(["em-pairs", "--run", os.path.join(str(tmp_path), "out"),
                   "--pairs", "p1", "--arms", "full", "--planner", "deepseek",
                   "--model-config", "cfg.yaml"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "planner=deepseek" in out
    assert "policy=None" in out
    assert all(c["planner_kind"] == "deepseek" for c in calls)
    assert all(c["model_config"] == "cfg.yaml" for c in calls)


def test_the_cli_refuses_a_model_seat_without_a_config(tmp_path, capsys, monkeypatch):
    from embodied_agent import cli
    monkeypatch.setattr(episodic_runs, "run_group",
                        lambda *a, **k: pytest.fail("must not start an episode"))
    rc = cli.main(["em-pairs", "--run", os.path.join(str(tmp_path), "out"),
                   "--planner", "deepseek"])
    err = capsys.readouterr().err
    assert rc == cli.EXIT_CONFIG_ERROR
    assert "model-config" in err
    assert not os.path.exists(os.path.join(str(tmp_path), "out", "pairs_run.json"))


def test_the_cli_has_no_policy_flag_to_confuse_a_seat_with(tmp_path, monkeypatch):
    # the incoherent batch must be unreachable from the command line too.
    # `run_group` is stubbed to fail loudly: if `--policy` ever comes back, argparse will accept
    # it and the command would otherwise start a *real* batch — so a regression here would dial a
    # provider and take 40 seconds to report itself. It should fail in milliseconds.
    monkeypatch.setattr(episodic_runs, "run_group",
                        lambda *a, **k: pytest.fail("--policy would have started a real batch"))
    from embodied_agent import cli
    with pytest.raises(SystemExit) as exc:
        cli.main(["em-pairs", "--run", os.path.join(str(tmp_path), "never"),
                  "--policy", "memory"])
    assert exc.value.code == 2, "argparse should reject an unknown flag"
    assert not os.path.exists(os.path.join(str(tmp_path), "never"))


def test_the_cli_will_not_accept_the_test_double_planner(tmp_path, monkeypatch):
    monkeypatch.setattr(episodic_runs, "run_group",
                        lambda *a, **k: pytest.fail("fixture would have started a real batch"))
    from embodied_agent import cli
    with pytest.raises(SystemExit) as exc:
        cli.main(["em-pairs", "--run", os.path.join(str(tmp_path), "never"),
                  "--planner", "fixture"])
    assert exc.value.code == 2, "argparse should reject a planner outside the choices"
    assert not os.path.exists(os.path.join(str(tmp_path), "never"))


def test_the_arms_still_default_to_both():
    assert set(EM_ARMS) == {"full", "wo_episodic_memory"}


# ------------------------------------------------------- the bound is enforced here ----
# A pre-registration's bound is only real if the thing that spends enforces it. The check lives
# in `run_episodic_pairs` rather than in a driver, and it is read with the two-ledger reader:
# on E1, reading only the per-episode ledgers undercounted the batch by 356 requests, so a bound
# checked against the smaller number is a bound checked against the wrong number.
PAIR_A = {"pair_id": "p1", "condition": "c", "writer": "w", "reader": "r"}
PAIR_B = {"pair_id": "p2", "condition": "c", "writer": "w", "reader": "r"}


def _budgeted_ledger(out_root, per_episode_requests):
    """A stub that writes ledgers whose cost the reader can actually see."""
    def fake_run_group(set_name, **kw):
        root = os.path.join(kw["out_root"], "episodes", "e1")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "model_calls.jsonl"), "w", encoding="utf-8") as f:
            for _ in range(per_episode_requests):
                f.write(json.dumps({"http_requests_this_call": 1, "latency_s": 0.1,
                                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}) + "\n")
        g = os.path.join(kw["out_root"], "goal_resolutions")
        os.makedirs(g, exist_ok=True)
        with open(os.path.join(g, "goal_calls.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"http_requests_this_call": 1, "latency_s": 0.1,
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}) + "\n")
        with open(os.path.join(root, "episode_summary.json"), "w", encoding="utf-8") as f:
            json.dump({"episode_id": "e1"}, f)
        open(os.path.join(root, "events.jsonl"), "w", encoding="utf-8").close()
        return {"root": os.path.dirname(root), "run_id": "r", "rows": 1}
    return fake_run_group


def test_the_bound_stops_the_batch_at_a_pair_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(episodic_runs, "run_group", _budgeted_ledger(str(tmp_path), 3))
    out = os.path.join(str(tmp_path), "pairs")
    # 1 pair-arm = 2 roles x (3 decision + 1 goal) = 8 requests. The check is at a pair
    # boundary, so a bound of 8 halts before p2 while a bound of 10 does not: after p1 spent is
    # 8, and 8 < 10 admits p2. The pair is never abandoned half-run — a half pair is a writer
    # with no reader, and the reader is the object of the measurement.
    artifact = run_episodic_pairs(out, pairs=[PAIR_A, PAIR_B], arms=("full",),
                                  stop_at_requests=8)
    assert artifact["pairs_ran"] == ["p1"]
    assert artifact["pairs_declared"] == ["p1", "p2"]
    assert artifact["stop_rule"]["halted_before_pair"] == "p2"
    assert artifact["stop_rule"]["bound_requests"] == 8
    assert artifact["stop_rule"]["spent_requests_when_stopped"] == 8


def test_a_halt_names_what_did_not_run(tmp_path, monkeypatch):
    # a partial batch that looks like a complete small one is the failure this prevents
    monkeypatch.setattr(episodic_runs, "run_group", _budgeted_ledger(str(tmp_path), 3))
    out = os.path.join(str(tmp_path), "pairs")
    artifact = run_episodic_pairs(out, pairs=[PAIR_A, PAIR_B], arms=("full",),
                                  stop_at_requests=8)
    assert artifact["pairs_ran"] != artifact["pairs_declared"]
    assert artifact["stop_rule"]["halted_before_pair"] is not None
    with open(os.path.join(out, "pairs_run.json"), encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["stop_rule"]["halted_before_pair"] == "p2"


def test_the_bound_counts_the_goal_ledger_too(tmp_path, monkeypatch):
    # the reader must be the two-ledger one: goal parses live outside any episode directory, so
    # a per-episode-only count would let the batch run past the bound by exactly the amount that
    # hid on E1
    monkeypatch.setattr(episodic_runs, "run_group", _budgeted_ledger(str(tmp_path), 0))
    out = os.path.join(str(tmp_path), "pairs")
    artifact = run_episodic_pairs(out, pairs=[PAIR_A, PAIR_B], arms=("full",),
                                  stop_at_requests=2)
    # each pair-arm costs 2 requests, all of them goal parses
    assert artifact["stop_rule"]["spent_requests_when_stopped"] == 2
    assert artifact["pairs_ran"] == ["p1"]


def test_no_bound_means_the_batch_runs_to_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(episodic_runs, "run_group", _budgeted_ledger(str(tmp_path), 1))
    out = os.path.join(str(tmp_path), "pairs")
    artifact = run_episodic_pairs(out, pairs=[PAIR_A, PAIR_B], arms=("full",))
    assert artifact["pairs_ran"] == ["p1", "p2"]
    assert artifact["stop_rule"]["bound_requests"] is None
    assert artifact["stop_rule"]["halted_before_pair"] is None


def test_the_cli_takes_the_bound_and_reports_the_verdict(tmp_path, capsys, monkeypatch):
    from embodied_agent import cli
    monkeypatch.setattr(episodic_runs, "run_group", _budgeted_ledger(str(tmp_path), 3))
    monkeypatch.setattr(episodic_runs, "EM_PAIRS", [PAIR_A, PAIR_B])
    monkeypatch.setattr(episodic_runs, "print_pairs", lambda root: None)
    from embodied_agent.evaluation import episodic_tasks
    monkeypatch.setattr(episodic_tasks, "EM_PAIRS", [PAIR_A, PAIR_B])
    rc = cli.main(["em-pairs", "--run", os.path.join(str(tmp_path), "out"),
                   "--arms", "full", "--stop-at-requests", "8"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bound 8 requests" in out
    assert "HALTED BEFORE p2" in out
    assert "pairs ran" in out and "pairs declared" in out


def test_a_batch_inside_its_bound_says_so(tmp_path, capsys, monkeypatch):
    from embodied_agent import cli
    monkeypatch.setattr(episodic_runs, "run_group", _budgeted_ledger(str(tmp_path), 1))
    monkeypatch.setattr(episodic_runs, "EM_PAIRS", [PAIR_A])
    monkeypatch.setattr(episodic_runs, "print_pairs", lambda root: None)
    from embodied_agent.evaluation import episodic_tasks
    monkeypatch.setattr(episodic_tasks, "EM_PAIRS", [PAIR_A])
    rc = cli.main(["em-pairs", "--run", os.path.join(str(tmp_path), "out"),
                   "--arms", "full", "--stop-at-requests", "9999"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "completed within bound" in out
    assert "HALTED" not in out
