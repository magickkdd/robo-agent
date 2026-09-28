"""What a batch leaves on disk, and what those files are allowed to claim
(SPEC 7, 11.1, 11.2, 12.1.6).

One real offline run goes through the production entry point — `evaluation.run.main`,
the same argument parsing the CLI hands the operator — and then every promise the
artifacts make is checked against the files themselves:

* the manifest records the code, the schema, the scoring version, the dependency
  versions and the frozen task list, and records *no* environment variables, so a
  credential cannot be proven present by the run that was supposed to hide it;
* the three arms of a case share one goal parse and each of them pays for it, so
  a difference between A, B and C cannot be an artefact of three parses (SPEC 11.2);
* every planned run is a row in `episodes.csv`, including the rows a crash
  produced, because a report built from the survivors is a censored report;
* `--frozen` refuses before a single byte of a run is written, so a drifted task
  list cannot leave behind numbers that look like they were measured on the
  frozen one.
"""
from __future__ import annotations

import csv
import json
import os

import pytest

from embodied_agent.core.events import SCHEMA_VERSION
from embodied_agent.evaluation import report as R
from embodied_agent.evaluation import run as RUN
from embodied_agent.evaluation.tasks import FROZEN_PATH, build_set, frozen_manifest

CASE = "smoke_clean"
MODES = ("A", "B", "C")
# a credential-shaped string that no episode would produce on its own
SECRET = "sk-" + "0123456789abcdef" * 4


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _run_batch(out_root: str, *extra: str) -> str:
    """`evaluation.run`'s own CLI, with the environment holding a key it must not
    record. The rule planner never opens a connection; the key is here to be
    looked for afterwards."""
    prev = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = SECRET
    try:
        assert RUN.main(["run", "--set", "smoke", "--cases", CASE, "--modes", ",".join(MODES),
                         "--out-root", out_root, "--no-frames", *extra]) == 0
    finally:
        if prev is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = prev
    children = [d for d in os.listdir(out_root) if os.path.isdir(os.path.join(out_root, d))]
    assert len(children) == 1, children
    return os.path.join(out_root, children[0])


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return _run_batch(str(tmp_path_factory.mktemp("batch")))


@pytest.fixture(scope="module")
def manifest(root):
    return json.loads(_read(os.path.join(root, "manifest.json")))


@pytest.fixture(scope="module")
def summaries(root):
    out = {}
    for mode in MODES:
        d = os.path.join(root, "episodes", f"{CASE}.{mode}.r0")
        with open(os.path.join(d, "episode_summary.json"), encoding="utf-8") as f:
            out[mode] = (d, json.load(f))
    return out


@pytest.fixture(scope="module")
def rows(root):
    with open(os.path.join(root, "episodes.csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------- provenance ------


def test_the_manifest_names_the_code_that_produced_the_numbers(manifest):
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["planner"] == "rule"
    assert manifest["offline"] is True, "an offline batch must never be readable as a model result"
    code = manifest["code"]
    assert len(code["commit"]) == 40 and all(c in "0123456789abcdef" for c in code["commit"])
    assert code["commit"] != "unknown", "provenance that says 'unknown' proves nothing"
    assert isinstance(code["dirty"], bool)
    if code["dirty"]:
        assert code["dirty_diff_sha256"], "a dirty tree must be identified by its diff hash"
        assert code["changed_files"]
    else:
        assert code["dirty_diff_sha256"] is None
    deps = manifest["dependencies"]
    assert deps["pybullet"] not in ("absent", "unknown"), "physics version is part of the result"
    assert deps["pydantic"] and "urllib" in deps["http_transport"]
    assert manifest["scoring_version"] == "IndependentEvaluator/EvalSpec.tolerance_version"
    assert manifest["goal_resolution"]["shared_per"] == "(case, repeat)"
    assert manifest["goal_resolution"]["billed_to_modes"] == list(MODES)
    # the declared constants the hand-off geometry depends on are part of the run
    assert manifest["scene"]["declared_constants"]["GRID_STEP_M"] > 0
    assert manifest["model"] == {"provider": "rule", "model": "rule-interpreter",
                                 "prompt_version": "no-prompt", "sampling": None,
                                 "pricing_configured": False}
    assert set(manifest["budget_profile"]) == {CASE}


def test_the_manifest_records_the_frozen_list_and_says_whether_it_was_enforced(manifest):
    """The canonical file is checked and recorded even when nobody asked for a
    refusal; `enforced` is what distinguishes the two."""
    frozen = manifest["frozen"]
    assert frozen["frozen_path"] == FROZEN_PATH
    assert frozen["matches"] is True, frozen["detail"]
    assert frozen["enforced"] is False, "no --frozen was passed, so this records, it does not refuse"
    assert frozen["detail"] == frozen_manifest()["sha256"][:12]


def test_no_artifact_in_the_run_directory_can_carry_the_key(root):
    """SPEC 7: provenance without credentials or the environment."""
    scanned = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            text = _read(os.path.join(dirpath, fn))
            scanned += 1
            assert SECRET not in text, f"{fn} leaked the credential"
            assert "DEEPSEEK_API_KEY" not in text, f"{fn} dumped the environment"
            assert "Authorization" not in text, f"{fn} logged a request header"
    assert scanned >= 8, "the scan was vacuously small: nothing like a run was written"
    manifest = json.loads(_read(os.path.join(root, "manifest.json")))
    assert manifest["environment_variables"] == "not recorded (SPEC 7: no credentials or full env)"


def test_the_directory_carries_the_task_list_the_result_was_measured_against(root):
    with open(os.path.join(root, "task_manifest.json"), encoding="utf-8") as f:
        tm = json.load(f)
    assert tm["sha256"] == frozen_manifest()["sha256"]
    assert len(tm["sets"]["smoke"]) == len(build_set("smoke"))
    assert len(tm["protocol_harness"]) == 11 and len(tm["state_pair_specs"]) == 12
    ids = {c["task_id"] for c in tm["sets"]["smoke"]}
    assert CASE in ids
    # the hidden truth is in the frozen list, so a re-score is possible without
    # the code that produced it, and the run summary cannot disagree with it
    case = next(c for c in tm["sets"]["smoke"] if c["task_id"] == CASE)
    assert {a["entity_id"] for a in case["hidden_eval_spec"]["assignments"]} == set(case["targets"])


# ------------------------------------------------- the shared goal, billed twice


def test_the_three_arms_share_one_goal_parse_and_each_pays_for_it(summaries, root):
    resolution = os.path.join(root, "goal_resolutions", f"{CASE}__r0.json")
    assert os.path.exists(resolution)
    with open(resolution, encoding="utf-8") as f:
        rec = json.load(f)
    assert rec["shared_by_modes"] == list(MODES)
    assert rec["planner"] == "rule" and rec["well_formed"] is True and rec["error"] is None
    assert rec["counters"] == {"http_requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                               "api_errors": 0, "transport_retries": 0, "format_repairs": 0}, \
        "a rule harness must not claim model spend"
    assert rec["prologue"]["shared_goal"] == f"{CASE}__r0"
    assert len(rec["assignments"]) == 3

    for mode, (_d, s) in summaries.items():
        assert s["goal_resolution"]["artifact"] == resolution, "one parse, three billing records"
        assert s["goal_resolution"]["shared"] is True
    for mode in MODES:
        d, s = summaries[mode]
        with open(os.path.join(d, "events.jsonl"), encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        start_at = next(i for i, e in enumerate(events) if e["type"] == "episode_start")
        before = [e["type"] for e in events[:start_at]]
        assert set(before) <= {"observation", "decision_context"}, \
            "only the mode-A one-shot request may precede the episode's own start record"
        assert (start_at > 0) == (mode == "A"), \
            "the baseline is drawn from the same t0 context a B round gets, and it is logged"
        start = events[start_at]
        prologue = start["payload"]["prologue"]
        # the prologue is billed *into* the episode, not beside it, and the clock
        # it shifts is applied rather than shown: no arm gets a cheaper budget
        assert prologue["shared_goal"] == f"{CASE}__r0"
        assert "wall_start" not in prologue
        assert prologue["shared_goal_artifact"] == resolution
        assert s["result"]["http_requests"] == 0
    assert len({s["result"]["decision_rounds"] for _d, s in summaries.values()}) == 1, \
        "the same policy on the same scene must take the same number of rounds"


def test_a_frame_free_run_writes_no_images_but_still_writes_the_ledger(summaries):
    for mode, (d, s) in summaries.items():
        assert s["artifacts"]["frames"] == 0
        assert not [f for f in os.listdir(d) if f.endswith(".png")], os.listdir(d)
        assert s["artifacts"]["events"].endswith("events.jsonl")
        assert os.path.exists(s["artifacts"]["events"])
    assert not any("model_calls.jsonl" in os.listdir(d) for d, _s in summaries.values()), \
        "an offline run made no model call; a log claiming otherwise would be believed"


def test_the_event_log_is_one_ordered_stream_that_traces_every_claim(root, summaries):
    d, s = summaries["B"]
    with open(os.path.join(d, "events.jsonl"), encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    seq = [e["sequence"] for e in events]
    assert seq == sorted(seq) and len(set(seq)) == len(seq), "the ledger is append-only and ordered"
    assert all(e["episode_id"] == f"{CASE}.B.r0" for e in events)
    assert all(e["schema_version"] == SCHEMA_VERSION for e in events)
    kinds = {e["type"] for e in events}
    assert {"episode_start", "observation", "decision_context", "decision", "skill_call",
            "execution_feedback", "finish_check", "episode_end"} <= kinds
    called = {(e["payload"].get("call") or {}).get("call_id") for e in events
              if e["type"] == "skill_call"}
    for e in events:
        if e["type"] != "execution_feedback":
            continue
        fb = e["payload"]["feedback"]
        if fb["executed"]:
            assert fb["call_id"] in called, "an action with no request of record"
        assert fb["post_observation_ref"] and fb["post_state_version"] >= fb["pre_state_version"]
    end = events[-1]
    assert end["payload"]["result"]["objects_completed"] == 3
    assert s["score"]["scored_observation_ref"], "the verdict names the sample it judged"


# ------------------------------------------------------------ the flat table ---


def test_every_planned_run_is_a_row_and_the_report_reads_the_same_table(rows, root):
    assert [r["episode_id"] for r in rows] == [f"{CASE}.{m}.r0" for m in MODES]
    for r in rows:
        assert r["outcome"] == "success" and r["independent_complete_success"] == "True"
        assert r["agent_claims_success"] == "True", "both claims are reported, not just one"
        assert r["objects_completed"] == r["objects_total"] == "3"
        assert r["probe_met"] == "True"
        assert r["failure_type"] == "" and r["protocol_notes"] == ""
        # the budget counter is a run length, so 1 here is correct; the total of
        # wasted repeats is the new column, and a clean episode has none
        assert r["identical_invalid_attempts"] == "1"
        assert r["identical_repeats_total"] == "0"
        # SPEC 5.5 and 11.2 together: the slot-resolution rule is shared machinery,
        # so every arm records the same amount of it — three places, none of them
        # named by the policy. A difference here would be a difference in the arms.
        assert r["slot_resolution_fallbacks"] == "3", r["episode_id"]
    report = R.build_report(root)
    assert {report["behaviour"][m]["slot_resolution_fallbacks"] for m in MODES} == {3}
    assert report["planned_runs"] == 3 and report["cases"] == 1
    assert report["modes"] == list(MODES)
    assert report["success_rate_by_case"] == {f"{CASE}|{m}": 1.0 for m in MODES}
    assert report["by_subset"]["smoke"]["A"]["episodes"] == 1
    assert report["by_object_count"]["3"]["C"]["independent_successes"] == 1
    for name, cmp in report["paired_comparisons"].items():
        assert cmp["paired_cases"] == 1 and cmp["mean"] == 0.0, name
        assert cmp["reads_as"] == "indistinguishable from zero on this sample", name
    assert report["failure_attribution"]["A"]["not_success_by_reported_cause"] == {}
    assert report["behaviour"]["A"]["identical_repeats_charged"] == 0
    assert report["behaviour"]["A"]["needs_blind_review"] == []
    assert report["cost"]["B"]["skill_calls_total"] == 6
    assert report["event_coverage"]["episodes_with_declared_events"] == 0, \
        "this case declares no disturbance, and coverage is reported rather than filtered on"


def test_the_written_report_says_it_came_from_an_offline_harness(root, capsys):
    written = R.write_report(root)
    md = _read(os.path.join(root, "report.md"))
    js = json.loads(_read(os.path.join(root, "report.json")))
    assert js["planned_runs"] == written["planned_runs"] == 3
    assert md.startswith(f"# {written['run_id']}"), md[:40]
    assert "planner `rule` (offline: not a model result)" in md
    assert f"frozen task list `{written['frozen']['detail']}` matches=True refusal=False" in md
    assert "Wilson (descriptive only)" in md
    assert "indistinguishable from zero" in md
    assert "## Behaviour" in md and "## Costs" in md and "## Failure attribution" in md
    assert "runtime total 0" in md, "the honest repeat total, not the run-length counter"


# ------------------------------------------------------------------- refusal ---


def test_a_drifted_frozen_list_stops_the_run_before_it_writes_anything(tmp_path, capsys):
    """The gate has to fire before the artifacts exist: a directory holding a
    manifest and no episodes is already a number someone could quote."""
    path = tmp_path / "frozen.json"
    with open(FROZEN_PATH, encoding="utf-8") as f:
        payload = json.load(f)
    victim = next(c for c in payload["sets"]["smoke"] if c["task_id"] == CASE)
    victim["objects"][0]["xy"] = [0.4, 0.06]          # move the scene under the task
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)

    out = str(tmp_path / "out")
    os.makedirs(out, exist_ok=True)
    with pytest.raises(RUN.InfraError) as exc:
        _run_batch(out, "--frozen", str(path))
    assert "drifted task list" in str(exc.value) or "self-inconsistent" in str(exc.value)
    assert "self-inconsistent" in str(exc.value), "an edited file must not be believed"
    roots = [d for d in os.listdir(out)]
    assert len(roots) == 1
    assert os.listdir(os.path.join(out, roots[0])) == [], "a refused run leaves no artifacts"
    assert not os.path.exists(os.path.join(out, roots[0], "episodes.csv"))


def test_an_explicit_matching_frozen_list_is_recorded_as_enforced(tmp_path):
    out = str(tmp_path / "out")
    os.makedirs(out, exist_ok=True)
    prev = os.environ.get("DEEPSEEK_API_KEY")
    os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        children = []
        RUN.main(["run", "--set", "smoke", "--cases", CASE, "--modes", "B", "--out-root", out,
                  "--no-frames", "--frozen", FROZEN_PATH])
        children = [d for d in os.listdir(out)]
        with open(os.path.join(out, children[0], "manifest.json"), encoding="utf-8") as f:
            frozen = json.load(f)["frozen"]
    finally:
        if prev is not None:
            os.environ["DEEPSEEK_API_KEY"] = prev
    assert frozen == {"frozen_path": FROZEN_PATH, "matches": True, "enforced": True,
                      "detail": frozen_manifest()["sha256"][:12]}


# ----------------------------------------------------- the trio fails together -


def test_a_goal_parse_that_crashes_costs_the_whole_trio_not_one_arm(tmp_path, monkeypatch, capsys):
    """SPEC 11.2/11.4: a shared parse that fails fails every arm, and every one of
    those runs is still a row. Dropping the trio to keep the average clean is the
    failure this test exists to catch."""
    def boom(*a, **kw):
        raise RuntimeError("interpreter exploded")

    monkeypatch.setattr(RUN, "interpret", boom)
    out = str(tmp_path / "out")
    os.makedirs(out, exist_ok=True)
    assert RUN.main(["run", "--set", "smoke", "--cases", CASE, "--modes", ",".join(MODES),
                     "--out-root", out, "--no-frames"]) == 0
    run_dir = os.path.join(out, next(iter(os.listdir(out))))
    with open(os.path.join(run_dir, "episodes.csv"), encoding="utf-8") as f:
        table = list(csv.DictReader(f))
    assert [r["mode"] for r in table] == list(MODES), "one arm is not allowed to survive alone"
    assert {r["outcome"] for r in table} == {"goal_resolution_error"}
    for r in table:
        assert r["independent_complete_success"] != "True"
        assert "interpreter exploded" in json.dumps(json.loads(
            _read(os.path.join(run_dir, "goal_resolutions", f"{CASE}__r0.json"))))
    with open(os.path.join(run_dir, "run_summary.json"), encoding="utf-8") as f:
        stats = json.load(f)
    assert stats["planned"] == stats["rows"] == 3
    assert stats["outcomes"] == {"goal_resolution_error": 3}
    assert stats["cost_estimate_usd"] is None, "an offline batch has no API cost to report"

    report = R.build_report(run_dir)
    assert report["planned_runs"] == 3, "the crashed runs stay in the denominator"
    assert all(v == 0.0 for v in report["success_rate_by_case"].values())
    assert report["failure_attribution"]["A"]["not_success_by_reported_cause"] == \
        {"goal_resolution_error": 1}
    assert report["failure_attribution"]["A"]["infrastructure"] == 1


# --------------------------------------------------------------------- replay --


def test_recorded_replay_reads_back_the_trajectory_without_touching_physics(summaries, capsys):
    d, s = summaries["B"]
    out = RUN.recorded_replay(d)
    assert out["kind"] == "recorded_replay"
    assert out["events"] == len(_read(os.path.join(d, "events.jsonl")).strip().splitlines())
    assert out["sim_time_final"] == pytest.approx(s["result"]["sim_time_s"], abs=0.01)
    skills = [e["skill"] for e in out["sequence"] if e["type"] == "skill_call"]
    assert skills == ["pick", "place"] * 3, "the log is replayed in the order it happened"
    assert [e["skill"] for e in out["sequence"]
            if e["type"] == "execution_feedback" and e["skill"]] == skills, \
        "every request of record has its result beside it"


def test_plan_rerun_replays_the_last_plan_in_a_fresh_scene_and_says_what_it_is(summaries, tmp_path):
    """`recorded_replay` reads; `plan_rerun` re-executes only the final plan, so it
    is evidence about the plan and the physics and never contradicts a mid-episode
    decision. The naming is the product's claim, so the claim is tested."""
    d, _s = summaries["B"]
    out = RUN.plan_rerun(d, "smoke")
    assert out["kind"] == "plan_rerun" and out["not_a_trajectory_reproduction"] is True
    assert out["steps"] == 6
    assert [o["skill"] for o in out["outcomes"]] == ["pick", "place"] * 3
    assert [o["status"] for o in out["outcomes"]] == ["completed"] * 6, out["outcomes"]
    assert all(o["failure_code"] is None for o in out["outcomes"])


# --------------------------------------------------- the pre-registration -----


def test_the_manifest_records_the_pre_registration_and_says_whether_it_was_enforced(manifest):
    """Like the task list, the rules are recorded on every run and only *enforced*
    when the operator asked for the refusal. `matches: False` on a run is therefore a
    finding about the code, not a missing field."""
    from embodied_agent.evaluation.preregistration import PREREG_PATH, preregistration

    pre = manifest["pre_registration"]
    assert pre["prereg_path"] == PREREG_PATH
    assert pre["matches"] is True, pre["detail"]
    assert pre["enforced"] is False, "no --prereg was passed, so this records, it does not refuse"
    assert pre["rules_sha256"] == pre["detail"] == preregistration()["rules_sha256"]


def test_an_offline_batch_is_not_graded_against_the_frozen_gates(root):
    """SPEC 12.2's thresholds were written for the 216-run online matrix. A 3-run
    offline harness must not be able to pass them, fail them, or leave the reader to
    work out which one happened."""
    from embodied_agent.evaluation.preregistration import preregistration

    report = R.build_report(root)
    pg = report["preregistered_gates"]
    assert pg["registered"] is True and pg["enforced"] is False
    assert pg["sample_matches_preregistration"] is False
    assert any("planner 'rule'" in m for m in pg["sample_mismatches"]), pg["sample_mismatches"]
    assert any("3 runs measured, 216 pre-registered" in m for m in pg["sample_mismatches"])
    assert [g["status"] for g in pg["gates"]] == ["not_enforced"] * 5
    assert pg["claim"] == "not_evaluable"
    assert pg["rules_sha256"] == preregistration()["rules_sha256"]
    # every number the gates would have read is null rather than a convenient 0
    assert set(pg["numbers"].values()) == {None}
    R.write_report(root)
    with open(os.path.join(root, "report.md"), encoding="utf-8") as f:
        md = f.read()
    assert "## Pre-registered gates (SPEC 12.2)" in md
    assert "not the pre-registered sample" in md
    assert f"rules `{pg['rules_sha256'][:12]}`" in md
    assert f"pre-registered rules `{pg['rules_sha256'][:12]}` matches=True refusal=False" in md


def test_prereg_refuses_before_a_run_directory_exists(tmp_path):
    """The refusal is cheaper than the artifacts: an empty directory full of nothing
    is still a directory someone could point at."""
    from embodied_agent.evaluation.preregistration import PREREG_PATH

    out = str(tmp_path / "out")
    os.makedirs(out, exist_ok=True)
    with pytest.raises(RUN.InfraError) as exc:
        RUN.main(["run", "--set", "smoke", "--cases", CASE, "--modes", "B",
                  "--out-root", out, "--no-frames", "--prereg", PREREG_PATH])
    assert "not the pre-registered one" in str(exc.value)
    assert "the pre-registered set is 'formal'" in str(exc.value)
    assert os.listdir(out) == [], "a refused batch leaves no run behind to be quoted"
