"""The P3 pre-registration: derived from the code, hash-bound, and enforceable.

SPEC 9 P3 freezes 代码、任务、seed、事件、prompt、预算、模型配置和统计口径 before the
comparative batch runs, and SPEC 12.2 forbids lowering a threshold once results
exist. Both are claims about a *file*, so these tests check the file against the
code rather than checking that a file exists:

* every frozen value is read out of the object that will act on it — mutating a
  `Budgets` default, a prompt string or a report constant moves the rules hash,
  which is the only way to know the freeze was derived and not retyped;
* `provenance` (commit, dirty diff, dependencies) sits outside the hash, so a
  docs-only commit cannot invalidate the rules while every run still pairs its
  rules hash with the code that executed it;
* a hand-edited file is refused for being self-inconsistent, drift is named field
  by field, and an invariant that stops holding fails the check even though no
  frozen *value* moved;
* `matrix_mismatch` refuses a batch that is not the pre-registered one, and the
  report grades a batch only against the thresholds its own manifest names.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os

import pytest

from embodied_agent.adapters.deepseek import DeepSeekAdapter
from embodied_agent.core.contracts import Budgets
from embodied_agent.evaluation import preregistration as P
from embodied_agent.evaluation import report as R
from embodied_agent.evaluation.blind_review import load_rubric, rubric_sha256
from embodied_agent.evaluation.preregistration import (PREREG_PATH, check_invariants,
                                                       check_prereg, dump_prereg,
                                                       load_prereg, matrix_mismatch,
                                                       preregistration, rules_sha256)
from embodied_agent.evaluation.tasks import build_set, frozen_manifest


@pytest.fixture(scope="module")
def live():
    return preregistration()


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(doc: dict, path) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
    return str(path)


def _refrozen(mutate) -> dict:
    """A copy of the live document with `mutate(rules)` applied and the hash fixed,
    i.e. a file that is self-consistent but no longer what the code freezes to."""
    doc = copy.deepcopy(preregistration())
    mutate(doc["rules"])
    doc["rules_sha256"] = rules_sha256(doc["rules"])
    return doc


# --------------------------------------------- every value comes from the code --


def test_budgets_are_the_contract_defaults_not_a_retyped_copy(live, monkeypatch):
    fields = {k: f.default for k, f in Budgets.model_fields.items()}
    assert live["rules"]["budgets"]["defaults"] == fields
    formal = build_set("formal")
    # the cap a run is held to is the widest of the *per-case* budgets, which ride on
    # the task-list hash, not the class default restated here
    assert live["rules"]["run_matrix"]["per_episode_http_cap"] == \
        max(c.budgets.max_http_requests for c in formal)
    assert live["rules"]["budgets"]["protections"]["per_action_extra_repeats_mode_A"] == \
        formal[0].budgets.per_action_extra_repeats
    monkeypatch.setattr(Budgets.model_fields["max_http_requests"], "default", 99)
    moved = preregistration()
    assert moved["rules"]["budgets"]["defaults"]["max_http_requests"] == 99
    assert moved["rules_sha256"] != live["rules_sha256"], \
        "a budget the code changed must move the freeze, or the freeze is decoration"


def test_prompt_identity_is_the_text_that_gets_sent(live, monkeypatch):
    from embodied_agent.adapters import deepseek as DS

    def sha(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    prompts = live["rules"]["prompts"]
    assert prompts["sha256_by_kind"]["decision"] == sha(DS.DECISION_SYSTEM + DS.DECISION_USER)
    assert prompts["sha256_by_kind"]["goal"] == sha(DS.GOAL_SYSTEM + DS.GOAL_USER)
    assert prompts["sha256_by_kind"]["plan"] == sha(DS.PLAN_SYSTEM + DS.PLAN_USER)
    assert prompts["versions"] == {"goal_prompt": "s2-goal-v1",
                                   "decision_prompt": "s2-decide-v1",
                                   "plan_prompt": "s2-plan-v1"}
    # a prompt edited without a version bump still changes the hash: the version is
    # a label, and the label alone cannot be what a frozen experiment trusts
    monkeypatch.setattr(P, "DECISION_SYSTEM", DS.DECISION_SYSTEM + "\n额外一句")
    assert preregistration()["rules_sha256"] != live["rules_sha256"]


def test_the_statistical_constants_are_the_ones_report_computes_with(live, monkeypatch):
    stats = live["rules"]["statistics"]
    assert stats["bootstrap"]["replicates"] == R.BOOTSTRAP_REPLICATES
    assert stats["bootstrap"]["seed"] == R.BOOTSTRAP_SEED
    assert stats["infra_outcomes_stay_in_the_denominator"] == list(R.INFRA_OUTCOMES)
    monkeypatch.setattr(R, "BOOTSTRAP_SEED", 1)
    moved = preregistration()
    assert moved["rules"]["statistics"]["bootstrap"]["seed"] == 1
    assert moved["rules_sha256"] != live["rules_sha256"]


def test_the_run_matrix_is_the_frozen_task_list_itself(live):
    m = live["rules"]["run_matrix"]
    formal = build_set("formal")
    assert m["case_ids"] == [c.task_id for c in formal]
    assert m["seeds"] == {c.task_id: c.seed for c in formal}
    assert m["subset_counts"] == {"clean": 8, "execution_deviation": 8, "state_change": 8}
    assert m["cases"] == 24 and m["repeats"] == 3
    assert m["episodes"] == m["cases"] * len(m["modes"]) * m["repeats"] == 216
    assert m["shared_goal_parses"] == m["cases"] * m["repeats"] == 72


def test_the_task_list_and_rubric_hashes_are_the_ones_on_disk(live):
    assert live["rules"]["task_list"]["sha256"] == frozen_manifest()["sha256"]
    assert live["rules"]["blind_review"]["sha256"] == rubric_sha256()
    rubric = load_rubric()
    assert live["rules"]["blind_review"]["rubric_id"] == rubric["rubric_id"]
    assert live["rules"]["blind_review"]["reviewers"] == rubric["reviewers"]["count"]
    assert live["rules"]["blind_review"]["prohibitions"] == rubric["prohibitions"]
    # the frozen event specification is inside the task-list hash: a perturbation
    # changed after a result was produced is drift, not tuning (SPEC 10.3)
    assert live["rules"]["task_list"]["events_note"]


def test_the_diagnostics_are_counted_and_kept_out_of_the_success_denominator(live):
    d = live["rules"]["diagnostics"]
    assert d["state_utilization"]["pairs"] == 12
    assert d["state_utilization"]["excluded_from_success_denominator"] is True
    assert d["protocol_cases_in_the_scene"]["cases"] == len(build_set("protocol")) == 11
    assert d["protocol_cases_owned_by_tests"]["harness_entries"] == 11
    assert d["boundary_cases_total"] == 22, "SPEC 11.1 asks for at least 12"


def test_sampling_names_the_adapter_it_was_read_from(live):
    s = live["rules"]["sampling"]
    assert set(s["defaults_in_code"]) == {"model", "base_url", "temperature", "max_tokens",
                                          "timeout_s", "max_retries"}
    # the keys a live adapter reports must be a superset of the frozen ones, or the
    # runner's equality check could never fail on a field this block does not name.
    # Constructing it sends nothing: `sampling` is read off the object's own fields.
    probe = DeepSeekAdapter(api_key="probe-value-never-sent")
    assert set(s["defaults_in_code"]) <= set(probe.sampling)
    assert {k: probe.sampling[k] for k in s["defaults_in_code"]} == s["defaults_in_code"]
    assert s["credential_policy"].startswith("api_key_env names the variable")
    assert "api_key" not in json.dumps(s).replace(s["credential_policy"], "")
    assert s["pricing_configured"] is False and s["api_cost_estimate"] is None


# ------------------------------------------------------- the hash and the checks --


def test_provenance_is_recorded_outside_the_rules_hash(live, monkeypatch):
    other = copy.deepcopy(live)
    other["provenance"]["code"]["commit"] = "f" * 40
    other["provenance"]["dependencies"]["pybullet"] = "9.9.9"
    assert rules_sha256(other["rules"]) == live["rules_sha256"], \
        "a commit that only touches docs must not invalidate the rules"
    code = live["provenance"]["code"]
    assert len(code["commit"]) == 40 and isinstance(code["dirty"], bool)
    assert code["dirty_diff_sha256"] if code["dirty"] else True
    assert live["provenance"]["environment_variables"].startswith("not recorded")


def test_dump_then_check_passes_against_the_code(tmp_path):
    path = dump_prereg(str(tmp_path / "prereg.json"))
    ok, msg = check_prereg(path)
    assert ok, msg
    assert msg == load_prereg(path)["rules_sha256"]


def test_the_canonical_file_is_in_step_with_the_code():
    ok, msg = check_prereg(PREREG_PATH)
    assert ok, msg
    assert not check_invariants()


def test_a_hand_edit_to_a_gate_is_not_believed(tmp_path):
    doc = preregistration()
    doc["rules"]["gates"]["items"][0]["threshold"] = 0.0   # no recorded hash change
    path = _write(doc, tmp_path / "prereg.json")
    ok, msg = check_prereg(path)
    assert not ok and "self-inconsistent" in msg


def test_drift_names_the_field_that_moved(tmp_path, monkeypatch):
    path = dump_prereg(str(tmp_path / "prereg.json"))
    monkeypatch.setattr(Budgets.model_fields["wall_clock_s"], "default", 1234.5)
    ok, msg = check_prereg(path)
    assert not ok
    assert "budgets.defaults.wall_clock_s" in msg, msg
    assert "1234.5" in msg and "900.0" in msg
    assert "the code has drifted" in msg


def test_an_invariant_that_stops_holding_fails_before_any_hash_is_compared(tmp_path):
    """A claim about the code can break without a frozen value moving, so the hash
    alone would keep saying 'frozen, all right'."""
    def break_it(rules):
        rules["code_invariants"].append({"id": "I99", "claim": "a thing that is not true",
                                         "file": "embodied_agent/core/contracts.py",
                                         "check": {"needle": "the runtime chooses its own "
                                                            "strategy"}})
    path = _write(_refrozen(break_it), tmp_path / "prereg.json")
    ok, msg = check_prereg(path)
    assert not ok and "I99 no longer holds" in msg, msg


def test_an_invariant_needle_that_should_be_absent_is_checked_too():
    problems = check_invariants({"code_invariants": [
        {"id": "IX", "claim": "c", "file": "embodied_agent/core/runtime.py",
         "check": {"needle": "def run_episode", "absent": True}}]})
    assert problems and "IX no longer holds" in problems[0]
    missing_fn = check_invariants({"code_invariants": [
        {"id": "IY", "claim": "c", "file": "embodied_agent/core/runtime.py",
         "check": {"function": "Runtime.not_a_method", "needle": "x"}}]})
    assert "IY: Runtime.not_a_method not found" in missing_fn[0]


# ------------------------------------------------------------- the matrix gate --


def _sampling_for(rules):
    return {**rules["sampling"]["defaults_in_code"], "response_format": "json_object",
            "stream": False, "proxy": "direct"}


def test_the_pre_registered_matrix_itself_is_not_refused(live):
    rules = live["rules"]
    assert matrix_mismatch(rules, set_name="formal", modes=tuple(rules["run_matrix"]["modes"]),
                           repeats=3, planner_kind="deepseek", case_ids=None, limit=None,
                           sampling=_sampling_for(rules)) == []


def test_a_batch_that_is_not_the_pre_registered_one_is_refused_for_each_reason(live):
    rules = live["rules"]
    reasons = matrix_mismatch(rules, set_name="smoke", modes=("A", "B"), repeats=1,
                              planner_kind="rule", case_ids=["clean_c1"], limit=3,
                              sampling=_sampling_for(rules))
    assert len(reasons) == 5, reasons
    assert any("pre-registered set is 'formal'" in r for r in reasons)
    assert any("arms are" in r for r in reasons)
    assert any("repeats are 3" in r for r in reasons)
    assert any("planner is 'deepseek'" in r for r in reasons)
    assert any("--cases/--limit would change the denominator" in r for r in reasons)


def test_a_sampling_edit_stops_the_batch_naming_the_field(live):
    rules = live["rules"]
    sampling = _sampling_for(rules)
    sampling["temperature"] = 0.9
    reasons = matrix_mismatch(rules, set_name="formal", modes=tuple(rules["run_matrix"]["modes"]),
                              repeats=3, planner_kind="deepseek", case_ids=None, limit=None,
                              sampling=sampling)
    assert len(reasons) == 1
    assert "temperature: frozen 0.2 != live 0.9" in reasons[0], reasons[0]


def test_an_offline_planner_cannot_stand_in_for_the_pre_registered_model(live):
    rules = live["rules"]
    reasons = matrix_mismatch(rules, set_name="formal", modes=tuple(rules["run_matrix"]["modes"]),
                              repeats=3, planner_kind="fixture", case_ids=None, limit=None,
                              sampling=None)
    assert any("planner is 'deepseek', got 'fixture'" in r for r in reasons)


# ------------------------------------------------------------------ the gates ---


def test_the_gates_are_the_spec_12_2_numbers_as_data(live):
    g = live["rules"]["gates"]
    assert [(i["id"], i["op"], i["threshold"]) for i in g["items"]] == [
        ("G1", ">=", 0.8), ("G2", "<=", 0.05), ("G3", ">=", 0.7), ("G4", ">=", 0.15),
        ("G5", ">=", 10)]
    assert g["may_not_be_relaxed_after_results"] is True
    assert "阶段目标" in g["status"]
    assert live["rules"]["statistics"]["perturbed_subset_for_gate_G3_G4"] == \
        ["state_change", "execution_deviation"]


def _agg(clean_a=1.0, clean_b=1.0, pert_a=0.0, pert_b=0.0, per_case_repeats=3):
    """A synthetic per-(case, mode) aggregation: arithmetic for the gate code,
    never a result. Only the frozen file decides whether these numbers pass.

    `pert_b` may be a list, one rate per perturbed case, which is how a batch with
    a wide interval is distinguished from a batch that merely looks good. Rates are
    multiples of 1/3 so `successes` stays the integer a real row carries."""
    agg = {}
    for i in range(8):
        for mode, rate in (("A", clean_a), ("B", clean_b)):
            agg[(f"clean_c{i + 1}", mode)] = {"subset": "clean", "rate": rate,
                                              "successes": int(rate * per_case_repeats),
                                              "repeats": per_case_repeats}
    for i in range(16):
        subset = "state_change" if i < 8 else "execution_deviation"
        rate_b = pert_b[i] if isinstance(pert_b, list) else pert_b
        for mode, rate in (("A", pert_a), ("B", rate_b)):
            agg[(f"pert_{i}", mode)] = {"subset": subset, "rate": rate,
                                        "successes": int(rate * per_case_repeats),
                                        "repeats": per_case_repeats}
    return agg


def _run_dir_with(tmp_path, prereg_path, rules_hash, **kw):
    root = tmp_path / "run"
    root.mkdir(exist_ok=True)
    pre = {"prereg_path": str(prereg_path), "rules_sha256": rules_hash,
           "enforced": kw.pop("enforced", True), "matches": True}
    with open(root / "manifest.json", "w", encoding="utf-8") as f:
        json.dump({"pre_registration": pre}, f)
    return str(root)


def _grade(root, agg, state_pairs=11, planned=216, cases=24):
    return R.preregistered_gates(root, agg, {"ran": True,
                                             "pairs_with_both_arms_fitting": state_pairs},
                                 planned, cases, ["A", "B", "C"], repeats=3,
                                 planner="deepseek")


def test_a_run_recorded_under_other_rules_is_not_graded_at_all(tmp_path):
    path = _write(preregistration(), tmp_path / "prereg.json")
    root = _run_dir_with(tmp_path, path, "0" * 64)
    out = _grade(root, _agg())
    assert out["refused"] is True and "gates" not in out
    assert "not the ones this batch was pre-registered under" in out["note"]
    text = "\n".join(R._render_gates(out))
    assert "nothing is graded" in text and "000000000000" in text


def test_the_thresholds_come_from_the_file_and_not_from_the_report(tmp_path):
    """Re-freezing with a lower bar is what SPEC 12.2 forbids; this shows the report
    would obey it mechanically *unless* the run's recorded hash catches it — which
    the test above covers. The pair of tests is the point."""
    loose = _refrozen(lambda r: r["gates"]["items"].__setitem__(
        slice(None), [dict(i, threshold=0.0) for i in r["gates"]["items"]]))
    path = _write(loose, tmp_path / "loose.json")
    root = _run_dir_with(tmp_path, path, loose["rules_sha256"])
    # one perturbed case of sixteen succeeds: every threshold is 0.0, so all five
    # gates are "met" while the interval spans zero — the wording rule, not the
    # gate list, is what stops this from being read as a gain
    out = _grade(root, _agg(pert_b=[1.0] + [0.0] * 15))
    assert [g["status"] for g in out["gates"]] == ["met"] * 5
    assert out["paired_on_perturbed"]["ci95"][0] == 0.0
    assert out["claim"].startswith("preliminary"), out["claim"]

    strict = _refrozen(lambda r: r["gates"]["items"].__setitem__(
        slice(None), [dict(i, threshold=0.99) for i in r["gates"]["items"]]))
    path2 = _write(strict, tmp_path / "strict.json")
    root2 = _run_dir_with(tmp_path, path2, strict["rules_sha256"])
    out2 = _grade(root2, _agg())
    # G2 is `<=` and G5 counts pairs, so the same 0.99 catches three of five and
    # misses two: the operator is part of the frozen record, not a report choice
    assert [g["status"] for g in out2["gates"]] == ["met", "met", "not_met", "not_met",
                                                    "met"]
    assert out2["claim"] == "not met"


def test_a_sample_that_is_not_the_frozen_one_can_neither_pass_nor_fail(tmp_path):
    doc = _refrozen(lambda r: None)
    path = _write(doc, tmp_path / "prereg.json")
    root = _run_dir_with(tmp_path, path, doc["rules_sha256"])
    out = _grade(root, _agg(), planned=3, cases=1)
    assert out["sample_matches_preregistration"] is False
    assert any("3 runs measured, 216 pre-registered" in m for m in out["sample_mismatches"])
    assert all(g["status"] == "not_evaluable" for g in out["gates"])
    assert out["claim"] == "not_evaluable"
    assert "not the pre-registered sample" in "\n".join(R._render_gates(out))


def test_gates_without_a_pre_registration_say_so(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    out = R.preregistered_gates(str(root), _agg(), {"ran": False}, 3, 1, ["A", "B", "C"])
    assert out["registered"] is False
    assert "measures the harness" in out["note"]
    assert "measures the harness" in "\n".join(R._render_gates(out))


def test_a_missing_gate_metric_is_no_measurement_not_zero(tmp_path):
    doc = _refrozen(lambda r: None)
    path = _write(doc, tmp_path / "prereg.json")
    root = _run_dir_with(tmp_path, path, doc["rules_sha256"])
    out = R.preregistered_gates(root, _agg(), {"ran": False}, 216, 24, ["A", "B", "C"],
                                repeats=3, planner="deepseek")
    g5 = next(g for g in out["gates"] if g["id"] == "G5")
    assert g5["status"] == "no_measurement" and g5["value"] is None, \
        "an unrun diagnostic is not a zero on the gate sheet"
    assert out["claim"] == "not met"


def test_the_paired_statistic_is_restricted_to_the_frozen_perturbed_subsets(tmp_path):
    doc = _refrozen(lambda r: None)
    path = _write(doc, tmp_path / "prereg.json")
    root = _run_dir_with(tmp_path, path, doc["rules_sha256"])
    out = _grade(root, _agg(pert_a=0.0, pert_b=1.0))
    p = out["paired_on_perturbed"]
    assert p["paired_cases"] == 16, "clean cases must not enter a perturbed-subset estimate"
    assert p["mean"] == pytest.approx(1.0) and p["seed"] == R.BOOTSTRAP_SEED
    assert p["replicates"] == R.BOOTSTRAP_REPLICATES
    assert p["ci95"][0] > 0 and out["claim"].startswith("supported")
    assert out["denominators"]["perturbed"]["B"] == 48


# ------------------------------------------------------------------------ cli ---


def test_cli_check_of_the_canonical_file_passes(capsys):
    from embodied_agent.cli import main

    assert main(["prereg", "--check"]) == 0
    assert "pre-registration matches the code" in capsys.readouterr().out


def test_cli_check_of_a_tampered_file_is_a_config_error(tmp_path, capsys):
    from embodied_agent.cli import main

    doc = preregistration()
    doc["rules"]["prompts"]["versions"]["decision_prompt"] = "s9-decide-v9"
    path = _write(doc, tmp_path / "tampered.json")
    assert main(["prereg", "--check", path]) == 3
    assert "MISMATCH" in capsys.readouterr().out


def test_cli_refuses_to_overwrite_a_file_it_disagrees_with_and_amends_on_demand(tmp_path,
                                                                                capsys):
    from embodied_agent.cli import main

    path = tmp_path / "prereg.json"
    assert main(["prereg", "--out", str(path)]) == 0
    first = json.loads(_read(path))
    assert first["rules_sha256"] == preregistration()["rules_sha256"]
    # make the code disagree with the file, then ask for the file to be rewritten
    original = Budgets.model_fields["max_skill_calls"].default
    Budgets.model_fields["max_skill_calls"].default = original + 1
    try:
        assert main(["prereg", "--out", str(path)]) == 3
        out = capsys.readouterr().out
        assert "refusing to overwrite" in out and "--amend" in out
        assert json.loads(_read(path))["rules_sha256"] == first["rules_sha256"], \
            "a refusal must not touch the file"
        moved = preregistration()["rules_sha256"]
        assert main(["prereg", "--out", str(path), "--amend"]) == 0
        amended = json.loads(_read(path))
    finally:
        Budgets.model_fields["max_skill_calls"].default = original
    assert amended["rules_sha256"] == moved
    assert amended["rules_sha256"] != first["rules_sha256"]
    assert amended["provenance"]["amended_from"] == first["rules_sha256"], \
        "the lineage of an amendment belongs in the artifact, not only in a terminal"
    printed = capsys.readouterr().out
    assert f'"amended_from": "{first["rules_sha256"]}"' in printed
    assert "drift" in printed, \
        "an amendment must print what it replaced *and* why the code disagreed"
