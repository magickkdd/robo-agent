"""Batch-level control: the run-order accounting that has to survive a re-run.

Three things are checked here, all of them offline and all of them about the *ledger*
rather than the model: the identity guard (§5.5), one-directory-per-attempt (§6.4/§8.1)
and the two independent recomputation routes for token cost (§13-9).
"""
from __future__ import annotations

import json
import os

import pytest

# `runner.py` imports the ALFWorld backend, so this file can only be collected where
# the batch itself can run. The skip is named rather than worked around: a stub of the
# backend would test the stub.
pytest.importorskip("textworld", reason="the batch runner imports the text backend")

from embodied_agent.benchmark.aggregate import _series_check
from embodied_agent.benchmark.cli import _slots_for_segment
from embodied_agent.benchmark.runner import _isolate_prior_attempt, completed_slot_index
from embodied_agent.benchmark.runner import identity_drift


def _episode(slot_index: int, cumulative, sealed_total) -> dict:
    return {"episode_id": f"slot{slot_index:03d}", "slot_index": slot_index,
            "sealed_tokens": {"prompt": sealed_total, "completion": 0,
                              "total": sealed_total, "provider_cumulative_total": cumulative}}


def test_a_different_respondent_is_named_and_a_match_is_not():
    summary = {"model_identity": {"returned_models": ["agnes-2.5-flash", "other-variant"]}}
    offenders = identity_drift(summary, "agnes-2.5-flash")
    assert offenders == ["other-variant"], "the guard must name what it saw, not just fail"
    assert identity_drift({"model_identity": {"returned_models": ["agnes-2.5-flash"]}},
                          "agnes-2.5-flash") == []


def test_silence_is_not_reported_as_a_match():
    """An episode with no successful request carries no returned model. Reading that
    as agreement would let the guard pass on the absence of evidence."""
    assert identity_drift({}, "agnes-2.5-flash") == []
    assert identity_drift({"model_identity": {"returned_models": []}}, "agnes-2.5-flash") == []
    assert identity_drift({"model_identity": {"returned_models": [None, ""]}}, "x") == []


def test_a_second_attempt_moves_the_first_out_of_episodes(tmp_path):
    run_root = str(tmp_path)
    ep_dir = os.path.join(run_root, "episodes", "slot007_x")
    os.makedirs(ep_dir)
    with open(os.path.join(ep_dir, "events.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "episode_start"}) + "\n")
    _isolate_prior_attempt(run_root, ep_dir)
    assert not os.path.exists(ep_dir), "the name must be free for the new attempt"
    kept = os.path.join(run_root, "superseded", "slot007_x.attempt1")
    assert os.path.isfile(os.path.join(kept, "events.jsonl")), "a measurement was deleted"

    os.makedirs(ep_dir)
    with open(os.path.join(ep_dir, "events.jsonl"), "w", encoding="utf-8") as f:
        f.write("{}\n")
    _isolate_prior_attempt(run_root, ep_dir)
    assert os.path.isdir(os.path.join(run_root, "superseded", "slot007_x.attempt2"))


def test_an_empty_directory_is_not_a_prior_attempt(tmp_path):
    """A slot that started, crashed before any event, and is being re-run must not
    have its own empty directory moved away and counted as an attempt."""
    run_root = str(tmp_path)
    ep_dir = os.path.join(run_root, "episodes", "slot000_y")
    os.makedirs(ep_dir)
    open(os.path.join(ep_dir, "events.jsonl"), "w").close()
    _isolate_prior_attempt(run_root, ep_dir)
    assert os.path.isdir(ep_dir)
    assert not os.path.isdir(os.path.join(run_root, "superseded"))


def test_the_provider_series_is_the_route_that_survives_a_usageless_row():
    eps = [_episode(0, 100, 100), _episode(1, 250, 150)]
    out = _series_check(eps)
    assert out["available"] is True and out["episodes_agreeing"] == 2
    assert out["process_counter_at_batch_end"] == 250

    broken = _series_check([eps[0], _episode(1, 400, 150)])
    assert broken["episodes_agreeing"] == 1, "a 300-token gap must not read as a 150 episode"
    assert [r["agrees"] for r in broken["per_episode"]] == [True, False]


def test_the_series_refuses_to_guess_across_a_resume_or_a_missing_counter():
    dup = _series_check([_episode(0, 10, 10), _episode(0, 30, 20)])
    assert dup["available"] is False and "repeated" in dup["why"]
    no_counter = _series_check([_episode(0, None, 10)])
    assert no_counter["available"] is False
    assert no_counter["episodes"] == ["slot000"], "the episode that cannot be checked is named"


def test_only_a_resumable_complete_slot_is_skipped_on_a_rerun():
    progress = {"slots": [{"slot_index": 0, "resumable_complete": True},
                          {"slot_index": 1, "resumable_complete": False},
                          {"slot_index": 2}]}
    assert completed_slot_index(progress) == {0}


# ------------------------------------------------------- the slot plan ---------


def _planned(index: int, task: str) -> dict:
    return {"slot_index": index, "task_id": task, "task_type": "pick_and_place_simple",
            "repeat": 0, "run_seed": "20260920"}


def _manifest() -> dict:
    """Two blocks, each numbered from zero — the shape the task manifest actually has."""
    return {"order": {"screening_slots": [dict(_planned(0, "a"), slot_index=0),
                                          dict(_planned(1, "b"), slot_index=1)],
                      "remainder_slots": [dict(_planned(0, "c"), slot_index=0),
                                          dict(_planned(1, "d"), slot_index=1)]}}


def test_a_segment_is_numbered_over_the_whole_plan_not_over_each_of_its_lists():
    """The full segment is two manifest lists; `slot_index` is what a resume and the
    episode directory are both keyed on, so a plan that repeats a number is a plan that
    loses the episodes that collide with it."""
    chain = ("order", "screening_slots", "order", "remainder_slots")
    full = _slots_for_segment(_manifest(), chain)
    assert [s["slot_index"] for s in full] == [0, 1, 2, 3]
    assert [s["task_id"] for s in full] == ["a", "b", "c", "d"]
    assert [s["manifest_slot_index"] for s in full] == [0, 1, 0, 1]


def test_continuing_a_batch_does_not_renumber_the_slots_already_run():
    full = _slots_for_segment(_manifest(), ("order", "screening_slots",
                                            "order", "remainder_slots"))
    assert full[:2] == _slots_for_segment(_manifest(), ("order", "screening_slots"))


def _ledger(tmp_path, row):
    json.dump({"segment": "full", "token_cap": 40_000_000, "slots": [row], "halted": None},
              open(os.path.join(str(tmp_path), "progress.json"), "w"))


def test_a_carried_slot_that_matches_the_plan_is_skipped_without_being_run(tmp_path, monkeypatch):
    from embodied_agent.benchmark import runner as R
    _ledger(tmp_path, dict(_planned(0, "a"), resumable_complete=True))
    ran = []
    monkeypatch.setattr(R, "run_slot",
                        lambda slot, **kw: ran.append(slot["slot_index"]) or
                        {"slot_index": slot["slot_index"], "task_id": slot["task_id"]})
    out = R.run_batch([_planned(0, "a"), _planned(1, "b")], planner=None,
                      identity={"model": "m"}, run_root=str(tmp_path), data_dir=str(tmp_path),
                      segment="full", token_cap=40_000_000, quiet=True)
    assert ran == [1] and out["halted"] is None


def test_a_slot_index_whose_ledger_row_names_another_task_halts_before_any_run(tmp_path,
                                                                               monkeypatch):
    """The alternative is silent: slot 0 is never executed, slot 0's borrowed verdict is
    counted for a task that was never seen, and the numbers still add up."""
    from embodied_agent.benchmark import runner as R
    _ledger(tmp_path, dict(_planned(0, "someone-else"), resumable_complete=True))
    ran = []
    monkeypatch.setattr(R, "run_slot", lambda slot, **kw: ran.append(slot) or {})
    with pytest.raises(R.BatchHalt) as err:
        R.run_batch([_planned(0, "a"), _planned(1, "b")], planner=None, identity={"model": "m"},
                    run_root=str(tmp_path), data_dir=str(tmp_path), segment="full",
                    token_cap=40_000_000, quiet=True)
    halt = json.loads(err.value.args[0])
    assert halt["reason"] == "SLOT_IDENTITY_MISMATCH" and halt["at_slot"] == 0
    assert ran == [], "the plan and the ledger are reconciled before the environment is opened"
    assert halt["slots"][0]["planned"]["task_id"] == "a"
    assert halt["slots"][0]["ledger"][0]["task_id"] == "someone-else"
    assert json.load(open(os.path.join(str(tmp_path), "progress.json")))["halted"]["reason"] == \
        "SLOT_IDENTITY_MISMATCH"


def test_an_incomplete_row_at_an_index_does_not_commit_the_batch_to_a_task(tmp_path, monkeypatch):
    """A slot that was started and died is not evidence about what that number *means*;
    only a finished episode is, and the guard must not read identity into a half-row."""
    from embodied_agent.benchmark import runner as R
    _ledger(tmp_path, dict(_planned(0, "someone-else"), resumable_complete=False))
    ran = []
    monkeypatch.setattr(R, "run_slot",
                        lambda slot, **kw: ran.append(slot["slot_index"]) or
                        {"slot_index": slot["slot_index"]})
    R.run_batch([_planned(0, "a")], planner=None, identity={"model": "m"},
                run_root=str(tmp_path), data_dir=str(tmp_path), segment="full",
                token_cap=40_000_000, quiet=True)
    assert ran == [0]
