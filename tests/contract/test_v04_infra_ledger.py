"""v0.4 R1/R2 contract tests: the errors ledger covers every non-success path, and the
batch manifest names the perception channel.

The findings these hold, after the v0.4 P1 re-judgment (v0.4 phase log, "三处盘上重判"):

* R1, corrected motivation. The v0.3 report's residual 18 said c3's two infrastructure
  errors had no cause in the ledger — the archive says they did (both `errors.jsonl`
  rows name `lh_c3_*`, written at batch time). The real gap is coverage asymmetry:
  `_infra_row` is assembled on three paths but only the in-episode crash path appends to
  `errors.jsonl`. E1's sixteen `goal_resolution_error` rows live in no ledger at all.
  "Why did this episode not produce a result" must not depend on which path it died on,
  so every path appends, and `goal_resolution_error` rows do too.
* R1, the other side of the contract: a clean batch must not grow a ledger. A book that
  exists only when something went wrong is the only kind that can be read as a signal.
* R2, pinned property, zero product change. The manifest has carried
  `perception.channel` since `run.py` entered the repo (`230f4dc`); the v0.3 analysis
  instrument looked for a key named `perceive` and blinded itself. No alias key is added
  — one identity under two names is two places to disagree — the existing key is pinned
  instead.

No test here spends a request: the crashing paths are monkeypatched at the seams
(`resolve_goal`, `run_one_episode`), and the clean-batch test runs the offline rule
planner the rest of the suite already exercises.
"""
from __future__ import annotations

import glob
import json
import os

import pytest

from embodied_agent.evaluation.run import GoalResolution, run_group


def _ledger(out_root: str) -> list[dict]:
    """The batch-root errors ledger, wherever the single run directory is."""
    files = glob.glob(os.path.join(out_root, "*", "errors.jsonl"))
    assert len(files) <= 1
    if not files:
        return []
    with open(files[0], encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _sole_run_dir(out_root: str) -> str:
    (root,) = os.listdir(out_root)
    return os.path.join(out_root, root)


def _failed_resolution(case_id: str) -> GoalResolution:
    return GoalResolution(case_id=case_id, repeat=0, planner="rule", goal=None,
                          well_formed=False, error="simulated: goal parse refused",
                          counters={}, wall_s=0.0, model="rule-interpreter",
                          prompt_version="no-prompt")


def test_a_goal_resolution_crash_leaves_a_named_cause(tmp_path, monkeypatch):
    """Path 1 of 3: `resolve_goal` raising. This is the hole the re-judgment found in
    code (E2's c3 dodged it by dying one path later): the row and the outcome existed
    and the ledger did not."""
    def boom(*a, **kw):
        raise ValueError("simulated: bridge refused at declaration time")
    monkeypatch.setattr("embodied_agent.evaluation.run.resolve_goal", boom)
    out_root = str(tmp_path / "runs")
    stats = run_group("dev", modes=("B",), repeats=1, out_root=out_root, limit=1,
                      case_ids=["dev_c5"], frames=False)
    rows = _ledger(out_root)
    assert len(rows) == 1, "the crash produced exactly one planned row and one ledger row"
    row = rows[0]
    for key in ("episode_id", "case_id", "mode", "repeat", "where", "error", "outcome"):
        assert key in row, f"an unanswerable ledger row is missing {key}"
    assert row["outcome"] == "infrastructure_error"
    assert "simulated" in row["error"]
    # the accounting did not move: the dead episode stays in the denominator it was
    # planned into, exactly as §7's rule requires
    assert stats["planned"] == stats["rows"] == 1
    assert stats["outcomes"] == {"infrastructure_error": 1}


def test_a_refused_goal_parse_leaves_a_named_cause(tmp_path, monkeypatch):
    """Path 2 of 3: `resolve_goal` returning a failed resolution. E1's sixteen
    `goal_resolution_error` rows all live on this path, in no ledger."""
    monkeypatch.setattr("embodied_agent.evaluation.run.resolve_goal",
                        lambda *a, **kw: _failed_resolution("dev_c5"))
    out_root = str(tmp_path / "runs")
    run_group("dev", modes=("B",), repeats=1, out_root=out_root, limit=1,
              case_ids=["dev_c5"], frames=False)
    rows = _ledger(out_root)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "goal_resolution_error"
    assert rows[0]["error"] == "simulated: goal parse refused"
    for key in ("episode_id", "case_id", "mode", "repeat", "where"):
        assert key in rows[0]


def test_an_in_episode_crash_still_leaves_a_named_cause(tmp_path, monkeypatch):
    """Path 3 of 3: the path E2's c3 actually died on, which already wrote the ledger.
    Pinned so the unification cannot quietly drop the one path the archive vouches for."""
    def boom(*a, **kw):
        raise RuntimeError("simulated: episode died mid-round")
    monkeypatch.setattr("embodied_agent.evaluation.run.run_one_episode", boom)
    out_root = str(tmp_path / "runs")
    run_group("dev", modes=("B",), repeats=1, out_root=out_root, limit=1,
              case_ids=["dev_c5"], frames=False)
    rows = _ledger(out_root)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "infrastructure_error"
    assert "simulated" in rows[0]["error"]
    assert "traceback_tail" in rows[0], "a crash keeps its traceback in the ledger"


def test_a_clean_batch_grows_no_ledger(tmp_path):
    """The other half of the contract: nothing went wrong, so no book exists. A ledger
    that records health would be unreadable as a record of failures."""
    out_root = str(tmp_path / "runs")
    run_group("dev", modes=("B",), repeats=1, out_root=out_root, limit=1,
              case_ids=["dev_c5"], frames=False)
    assert _ledger(out_root) == []
    assert not glob.glob(os.path.join(out_root, "*", "errors.jsonl"))


@pytest.mark.parametrize("perceive", ["privileged", "stub"])
def test_the_manifest_names_the_perception_channel(tmp_path, monkeypatch, perceive):
    """R2's pinned property, under the key that has always carried it (`perception`,
    not the `perceive` the v0.3 instrument blinded itself with). Both cells run without
    a model: the episode itself is cut short at a controlled seam, because what is under
    test is the manifest, which is written before the loop opens."""
    def boom(*a, **kw):
        raise RuntimeError("simulated: the manifest is already on disk by now")
    monkeypatch.setattr("embodied_agent.evaluation.run.run_one_episode", boom)
    out_root = str(tmp_path / "runs")
    run_group("dev", modes=("B",), repeats=1, out_root=out_root, limit=1,
              case_ids=["dev_c5"], frames=False, perceive=perceive)
    with open(os.path.join(_sole_run_dir(out_root), "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["perception"]["channel"] == perceive
    # the stub channel renders a frame and consults a deterministic reader: it never
    # asks a model, so a stub batch must not masquerade as one that could spend
    assert manifest["offline"] is (perceive != "vlm")
