"""SPEC 11.4 blind review: what the sheet may show, and what a verdict must survive.

The rubric is a pre-registration, so these tests read the registered file rather
than a fixture copy of it — a test that invented its own criteria would prove the
machinery works and say nothing about the criteria the experiment is bound to. A
synthetic rubric appears only where the point is the binding (a verdict written
against other criteria must be refused).

The evidence side is real: kind 1 items are built from an episode that actually
ran, copied into a run-directory layout, so the scrubbing is checked against the
payload shapes the runtime writes rather than against a guess at them.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

from embodied_agent.evaluation.blind_review import (RECORDS_NAME, RUBRIC_PATH, SHEET_NAME,
                                                    aggregate, build_sheet, fork_items, item_key,
                                                    load_rubric, read_verdicts, rubric_sha256,
                                                    scan, validate_verdicts, write_template)
from embodied_agent.evaluation.report import render_markdown

KIND1 = "place_of_already_satisfied"
KIND2 = "fork_answer_outside_both_label_sets"


def _write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def batch(run_case, tmp_path):
    """One finished episode laid out as a run directory, plus a pointer to a real
    `place` decision inside it — the unit the review is asked about."""
    ep = run_case("smoke_clean")
    episode_id = ep.events[0]["episode_id"]
    run_dir = str(tmp_path / "batch")
    dest = os.path.join(run_dir, "episodes", episode_id)
    os.makedirs(dest, exist_ok=True)
    shutil.copy(os.path.join(tmp_path, episode_id, "events.jsonl"),
                os.path.join(dest, "events.jsonl"))
    _write(os.path.join(run_dir, "episodes.csv"), "episode_id,mode,case_id\n")
    places = [e for e in ep.events if e["type"] == "decision"
              and (e["payload"].get("execute") or {}).get("skill") == "place"]
    assert places, "the episode placed nothing, so the pointer below proves nothing"
    args = (places[0]["payload"].get("execute") or {}).get("args") or {}
    pointer = {"episode_id": episode_id, "sequence": places[0]["sequence"], "kind": KIND1,
               "assignment": f'{args.get("object_id")}->{args.get("target_id")}'}
    return {"run_dir": run_dir, "episode_id": episode_id, "case_id": ep.case.task_id,
            "mode": ep.mode, "pointer": pointer, "events": ep.events}


def _fork_row(**kw) -> dict:
    row = {"pair_id": "sp01", "arm": "held", "case_id": "clean_c2", "difference":
           "held_vs_released", "provenance": "real_round:r2", "synthetic": False, "note": "",
           "context_id": "ctx_1c5f3dff", "state_version": 5, "observation_ref": "obs_0005",
           "focus": {"entity": "obj_blue_1", "target": "tray_right", "candidate": None},
           "asked": {"action": "execute", "execute": {"skill": "place"}},
           "labels": [], "acceptable_labels": ["place"], "rejected_labels": ["finish"],
           "accepted_because": [], "rejected_because": [], "miss_kind": "wrong_choice",
           "error": None, "fit": False, "policy_label": "deepseek", "provider_cost": {"n": 1},
           "shown_to_the_policy": {"round_index": 2, "progress": [{"entity_id": "obj_blue_1",
                                                                   "value": "false"}]}}
    return {**row, **kw}


def _sheet(tmp_path, batch, rows=None, rubric=None):
    rubric = rubric or load_rubric()
    paths = []
    if rows is not None:
        p = str(tmp_path / "state_utilization.json")
        _write(p, json.dumps({"contexts": rows}))
        paths.append(p)
    return build_sheet(rubric, batch["run_dir"], [batch["pointer"]], paths)


def _verdict(item_key_value, reviewer, verdict, sha=None, kind="human", **kw):
    return {"item_key": item_key_value, "reviewer_id": reviewer, "reviewer_kind": kind,
            "rubric_sha256": sha or rubric_sha256(), "verdict": verdict, **kw}


# ------------------------------------------------------------- the sheet ------


def test_the_sheet_shows_the_round_and_none_of_the_identity(batch, tmp_path):
    sheet = _sheet(tmp_path, batch)
    assert len(sheet["items"]) == 1
    item = sheet["items"][0]
    assert item["kind"] == KIND1 and item["scale"] == ["reasonable", "redundant", "cannot_tell"]
    # the reviewer is handed the records, not a summary of them
    assert item["evidence"]["context_at_the_decision"]["round_index"] >= 1
    assert item["evidence"]["decision"]["execute"]["skill"] == "place"
    assert "progress" in item["evidence"]["context_at_the_decision"]
    # the context *of that round*: round 1's view would answer a question nobody asked
    assert (item["evidence"]["context_at_the_decision"]["context_id"]
            == item["evidence"]["decision"]["context_id"])
    assert item["evidence"]["feedback_that_followed"]["feedback"]["skill"] == "place"
    blob = json.dumps(sheet, ensure_ascii=False)
    # which system acted is not a fact a reviewer may weigh, and the mode letter is
    # written into the episode id, so the id cannot appear either
    assert batch["episode_id"] not in blob
    assert batch["case_id"] not in blob
    for leak in ("episode_id", '"mode"', '"planner"', "policy_label", '"case_id"', '"subset"'):
        assert leak not in blob, leak
    assert item["item_key"] == item_key(sheet["rubric_id"], KIND1, batch["episode_id"],
                                        batch["pointer"]["sequence"])
    assert sheet["rubric_sha256"] == rubric_sha256(RUBRIC_PATH)


def test_a_payload_that_names_an_identity_in_a_value_is_refused_not_printed(batch, tmp_path):
    # `_scrub` drops keys; a rationale that *quotes* the identity gets past it, and
    # the substring guard is what stops that from reaching a reviewer
    path = os.path.join(batch["run_dir"], "episodes", batch["episode_id"], "events.jsonl")
    with open(path, encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    for rec in records:
        if rec["type"] == "decision":
            rec["payload"]["rationale"] = f"per {batch['episode_id']} episode_id"
    _write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    with pytest.raises(ValueError, match="episode_id"):
        _sheet(tmp_path, batch)


def test_fork_items_keep_the_state_arm_and_drop_the_verdicts(batch, tmp_path):
    rows = [_fork_row(), _fork_row(arm="released", context_id="ctx_99", fit=True),
            _fork_row(labels=["place"], accepted_because=["place in labels"], fit=True),
            _fork_row(error="http 500")]
    rubric = load_rubric()
    items = fork_items(rows, rubric)
    assert len(items) == 1, "only the answer that matched no label is contestable"
    ev = items[0]["evidence"]
    assert ev["arm"] == "held" and ev["difference"] == "held_vs_released"
    assert ev["shown_to_the_policy"]["round_index"] == 2, "the model-visible payload is the evidence"
    for gone in ("labels", "acceptable_labels", "rejected_labels", "accepted_because",
                 "rejected_because", "fit", "miss_kind", "case_id", "policy_label"):
        assert gone not in ev, gone
    assert items[0]["item_key"] == item_key(rubric["rubric_id"], KIND2, "sp01:held", "ctx_1c5f3dff")
    assert items[0]["item_key"] != item_key(rubric["rubric_id"], KIND2, "sp01:released", "ctx_99")


def test_two_items_cannot_share_one_blind_key(batch, tmp_path):
    # a collision would let one reviewer's sentence stand in for two actions
    rows = [_fork_row(), _fork_row(context_id="ctx_1c5f3dff")]
    with pytest.raises(ValueError, match="collapsed onto one blind key"):
        _sheet(tmp_path, batch, rows=rows)


def test_a_pointer_with_no_records_around_it_is_reported_not_dropped(batch, tmp_path):
    missing = dict(batch["pointer"], sequence=999999)
    rubric = load_rubric()
    sheet = build_sheet(rubric, batch["run_dir"], [missing], [])
    assert sheet["items"] == []
    assert len(sheet["not_reviewable"]) == 1
    assert "no decision_context/decision pair" in sheet["not_reviewable"][0]["reason"]


# ------------------------------------------------------------ the verdicts -----


def test_no_verdicts_is_a_state_of_the_experiment_not_a_clean_record(batch, tmp_path):
    state = scan(batch["run_dir"], [batch["pointer"]], rubric=load_rubric())
    assert state["ran"] is False and state["items"] == 1
    assert "unreviewed, which is not the same as reviewed-and-clear" in state["note"]
    assert state["verdict_file"].endswith(RECORDS_NAME)


def test_unreviewed_items_are_named_rather_than_counted_as_reasonable(batch, tmp_path):
    rubric, sheet = load_rubric(), _sheet(tmp_path, batch, rows=[_fork_row()])
    out = aggregate(rubric, sheet, [])
    assert out["items_settled"] == 0
    assert out["adverse_rate_strict"] is None, "an empty denominator may not read as 0 or 1"
    assert len(out["unreviewed"]) == len(sheet["items"]) == 2
    assert out["by_status"]["reasonable"] == 0
    assert set(out["items_by_kind"]) == {KIND1, KIND2}


def test_a_divided_item_stays_divided_and_changes_the_headline(batch, tmp_path):
    rubric, sheet = load_rubric(), _sheet(tmp_path, batch)
    key = sheet["items"][0]["item_key"]
    accepted = validate_verdicts(rubric, sheet, [_verdict(key, "r1", "redundant"),
                                                 _verdict(key, "r2", "reasonable")])["accepted"]
    out = aggregate(rubric, sheet, accepted)
    row = out["rows"][0]
    assert row["status"] == "divided"
    assert sorted(row["verdicts"]) == ["reasonable", "redundant"], "both readings survive verbatim"
    assert out["adverse_rate_strict"] == 0.0
    assert out["adverse_rate_counting_divided_as_adverse"] == 1.0, ("the gap between the two "
                                                                   "published rates *is* the "
                                                                   "sensitivity of this reading")
    # one reviewer alone settles nothing: the item is partial, not adverse
    solo = aggregate(rubric, sheet, accepted[:1])
    assert solo["by_status"]["partial"] == 1 and solo["items_settled"] == 0


def test_only_unanimous_adverse_counts_as_adverse(batch, tmp_path):
    rubric, sheet = load_rubric(), _sheet(tmp_path, batch)
    key = sheet["items"][0]["item_key"]
    out = aggregate(rubric, sheet, validate_verdicts(rubric, sheet, [
        _verdict(key, "r1", "redundant"), _verdict(key, "r2", "redundant")])["accepted"])
    assert out["rows"][0]["status"] == "adverse" and out["adverse_rate_strict"] == 1.0
    # either reviewer saying cannot_tell removes the item from the denominator
    told = aggregate(rubric, sheet, validate_verdicts(rubric, sheet, [
        _verdict(key, "r1", "redundant"), _verdict(key, "r2", "cannot_tell")])["accepted"])
    assert told["rows"][0]["status"] == "cannot_tell" and told["items_settled"] == 0


def test_a_model_may_not_grade_a_model(batch, tmp_path):
    rubric, sheet = load_rubric(), _sheet(tmp_path, batch)
    key = sheet["items"][0]["item_key"]
    checked = validate_verdicts(rubric, sheet, [
        _verdict(key, "llm-judge", "redundant", kind="model"),
        _verdict(key, "r1", "redundant")])
    assert len(checked["refused"]) == 1 and len(checked["accepted"]) == 1
    assert "may not grade itself" in checked["refused"][0]["reason"]
    assert checked["refused"][0]["row"]["reviewer_id"] == "llm-judge", "the refusal names its row"


def test_a_verdict_written_against_other_criteria_is_refused(batch, tmp_path):
    rubric, sheet = load_rubric(), _sheet(tmp_path, batch)
    key = sheet["items"][0]["item_key"]
    checked = validate_verdicts(rubric, sheet, [
        _verdict(key, "r0", "redundant", sha="0" * 64),
        _verdict(key, "r1", "redundant"),
        _verdict("deadbeefcafe", "r2", "redundant"),
        _verdict(key, "r3", "definitely_bad"),
        _verdict(key, "r1", "reasonable")])
    reasons = " | ".join(c["reason"] for c in checked["refused"])
    assert len(checked["refused"]) == 4, reasons
    assert "rubric_sha256" in reasons and "not on the sheet" in reasons
    assert "scale" in reasons and "duplicate" in reasons
    assert len(checked["accepted"]) == 1, ("one row per (item, reviewer): the first answer "
                                           "stands, the second is refused rather than silently "
                                           "replacing it")


def test_an_unfilled_template_cannot_be_handed_in_as_a_result(batch, tmp_path):
    sheet = _sheet(tmp_path, batch)
    path = str(tmp_path / "form.jsonl")
    assert write_template(sheet, path) == 2, "one row per item per reviewer"
    rows = read_verdicts(path)
    checked = validate_verdicts(load_rubric(), sheet, rows)
    assert checked["accepted"] == []
    assert all("is not in the" in c["reason"] for c in checked["refused"])
    assert all(r["reviewer_kind"] == "human" and r["rubric_sha256"] == sheet["rubric_sha256"]
               for r in rows), "the form carries everything but the judgement"


def test_editing_a_criterion_afterwards_invalidates_every_verdict_written_before_it(
        batch, tmp_path):
    # the registered file is left alone: the edited criteria are a *second* file, which
    # is also what a future `blind-review-v2` would be
    with open(RUBRIC_PATH, encoding="utf-8") as f:
        text = f.read()
    sheet = _sheet(tmp_path, batch)
    verdict = _verdict(sheet["items"][0]["item_key"], "r1", "redundant")
    assert validate_verdicts(load_rubric(), sheet, [verdict])["accepted"], "bound, so accepted"
    edited = str(tmp_path / "rubric_v2.json")
    # same rubric_id, same scale: only the meaning of one word moved
    _write(edited, text.replace('"adverse_value": "redundant"', '"adverse_value": "cannot_tell"'))
    rebuilt = build_sheet(load_rubric(edited), batch["run_dir"], [batch["pointer"]], [],
                          rubric_path=edited)
    assert rebuilt["rubric_sha256"] != sheet["rubric_sha256"]
    checked = validate_verdicts(load_rubric(edited), rebuilt, [verdict])
    assert checked["accepted"] == [] and len(checked["refused"]) == 1
    assert "rubric_sha256" in checked["refused"][0]["reason"]


# ------------------------------------------------------- the registered file ---


def test_the_registered_criteria_answer_to_the_prose_that_registered_them():
    rubric = load_rubric()
    assert rubric["registered_before_any_p3_run"] is True
    assert {it["kind"] for it in rubric["items"]} == {KIND1, KIND2}
    for it in rubric["items"]:
        assert it["adverse_value"] in it["scale"]
        # the rate definition must name the value it counts, or the two files disagree
        assert it["adverse_value"] in rubric["rate_definition"]["adverse"]
        assert it["emitted_by"].split(" -> ")[0].split()[0].endswith(".py")
    emitter = {it["kind"]: it["emitted_by"] for it in rubric["items"]}
    assert emitter[KIND1].startswith("evaluation/report.py")
    assert emitter[KIND2].startswith("evaluation/state_utilization.py")
    assert rubric["reviewers"]["count"] == 2
    assert "100%" in rubric["sampling"]
    assert any("自评" in p for p in rubric["prohibitions"])
    assert any("未审" in p for p in rubric["prohibitions"])


def test_the_report_says_unreviewed_rather_than_scoring_it(batch, tmp_path):
    state = scan(batch["run_dir"], [batch["pointer"]])
    md = render_markdown({"run_dir": batch["run_dir"], "planned_runs": 1, "cases": 1,
                          "modes": ["B"], "behaviour": {}, "blind_review": state})
    assert "## Blind review" in md
    assert "1 contested items" in md and "unreviewed, which is not the same" in md
    assert "adverse rate" not in md, "no rate exists yet, so none may be printed"


def test_scan_reads_verdicts_from_the_run_directory(batch, tmp_path):
    rubric = load_rubric()
    sheet = _sheet(tmp_path, batch)
    key = sheet["items"][0]["item_key"]
    _write(os.path.join(batch["run_dir"], RECORDS_NAME),
           "".join(json.dumps(v) + "\n" for v in [_verdict(key, "r1", "reasonable"),
                                                  _verdict(key, "r2", "redundant")]))
    state = scan(batch["run_dir"], [batch["pointer"]])
    assert state["ran"] is True and state["items_settled"] == 1
    assert state["by_status"]["divided"] == 1


# ----------------------------------------------------------------- the cli ----


def _report_with(batch, pointers):
    """The batch's own published pointers — the report is where they come from."""
    _write(os.path.join(batch["run_dir"], "report.json"),
           json.dumps({"behaviour": {"A": {"episodes": 1, "needs_blind_review": pointers},
                                     "_definitions": {"needs_blind_review":
                                                      "a prose definition, not a list"}}}))


def test_the_cli_reads_the_batch_pointers_and_writes_a_blind_sheet(batch, capsys):
    from embodied_agent import cli

    _report_with(batch, [batch["pointer"]])
    rc = cli.main(["blind-review", "--run-dir", batch["run_dir"], "--sheet", batch["run_dir"]])
    printed = capsys.readouterr().out
    assert rc == cli.EXIT_OK
    assert "sheet ->" in printed
    # a definition string is prose; iterating it used to yield 162 "items" of one
    # character each, which read as a large contested sample
    with open(os.path.join(batch["run_dir"], SHEET_NAME), encoding="utf-8") as f:
        sheet = json.load(f)
    assert [it["item_key"] for it in sheet["items"]] == [
        item_key(sheet["rubric_id"], KIND1, batch["episode_id"], batch["pointer"]["sequence"])]
    assert batch["episode_id"] not in json.dumps(sheet, ensure_ascii=False)
    assert '"ran": false' in printed, "nothing has been reviewed yet, and the sheet says so"


def test_an_unfilled_form_exits_nonzero_and_a_filled_one_aggregates(batch, capsys):
    from embodied_agent import cli

    _report_with(batch, [batch["pointer"]])
    form = os.path.join(batch["run_dir"], "blind_review", "form.jsonl")
    assert cli.main(["blind-review", "--run-dir", batch["run_dir"], "--template", form]) \
        == cli.EXIT_OK
    assert "blank rows: 1 items x 2 reviewers" in capsys.readouterr().out
    assert cli.main(["blind-review", "--run-dir", batch["run_dir"], "--records", form]) \
        == cli.EXIT_CONFIG_ERROR
    assert "refused" in capsys.readouterr().err
    with open(form, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    for row, verdict in zip(rows, ["redundant", "redundant"]):
        row["verdict"], row["reviewer_id"] = verdict, f"human-{row['reviewer_id']}"
    _write(form, "".join(json.dumps(r) + "\n" for r in rows))
    assert cli.main(["blind-review", "--run-dir", batch["run_dir"], "--records", form]) \
        == cli.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["adverse_rate_strict"] == 1.0 and out["items_settled"] == 1
    assert out["by_status"]["adverse"] == 1 and out["refused"] == []


def test_the_cli_names_the_missing_input_rather_than_printing_zero(capsys, tmp_path):
    from embodied_agent import cli

    assert cli.main(["blind-review", "--run-dir", str(tmp_path / "nowhere")]) \
        == cli.EXIT_CONFIG_ERROR
    assert "no run directory" in capsys.readouterr().err
    empty = tmp_path / "empty"
    _write(str(empty / "episodes.csv"), "episode_id,mode,case_id\n")
    assert cli.main(["blind-review", "--run-dir", str(empty),
                     "--records", str(empty / "absent.jsonl")]) == cli.EXIT_CONFIG_ERROR
    assert "--template" in capsys.readouterr().err
    # a run with no episodes is a run with nothing contested, and exits 0 saying so
    assert cli.main(["blind-review", "--run-dir", str(empty)]) == cli.EXIT_OK
    assert '"items": 0' in capsys.readouterr().out


def test_a_duplicated_pointer_is_an_error_not_a_smaller_sample(batch, capsys):
    from embodied_agent import cli

    _report_with(batch, [batch["pointer"], dict(batch["pointer"])])
    assert cli.main(["blind-review", "--run-dir", batch["run_dir"]]) == cli.EXIT_INFRA_ERROR
    assert "could not be built" in capsys.readouterr().err
