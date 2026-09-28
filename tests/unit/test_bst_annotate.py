"""The annotation layer refuses labels it cannot support (SPEC-BST §9.1–§9.3).

These tests run on synthetic event files, not on a live run root: what is under test is
the contract between an evidence packet and a label, and that contract must hold before
any real batch exists to be judged by it.
"""
import json
import os

import pytest

annotate = pytest.importorskip("embodied_agent.benchmark.annotate",
                              reason="the annotation tool ships with the benchmark package")


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _episode(tmp_path, *, name="slot000", status="completed",
             env_text="Nothing happens.",
             decisions=2, won=False, termination="AGENT_BLOCKED",
             task_type="pick_cool_then_place_in_recep", task_id=None, repeat=0,
             slot_index=0, false_finish=False):
    """One episode directory with the three artifacts `episode_row` reads."""
    d = tmp_path / "episodes" / name
    d.mkdir(parents=True)
    cmds = []
    events = []
    for i in range(1, decisions + 1):
        cid = f"ctx_{i}"
        events.append({"type": "decision_context",
                       "payload": {"context_id": cid, "round_index": i}})
        events.append({"type": "decision", "payload": {
            "decision_id": f"d_{i}", "context_id": cid, "action": "execute",
            "execute": {"skill": "alfred_take",
                        "args": {"object_id": "apple 1", "target_id": "fridge 1"}},
            "evidence_refs": [f"obs_{i:04d}"], "rationale": f"r{i}"}})
        events.append({"type": "skill_call", "payload": {
            "call": {"context_id": cid, "skill": "alfred_take",
                     "args": {"object_id": "apple 1", "target_id": "fridge 1"}}}})
        events.append({"type": "execution_feedback", "payload": {"feedback": {
            "context_id": cid, "decision_id": f"d_{i}", "status": status,
            "environment_text": env_text, "rejection_reasons": [],
            "held_object_before": None, "held_object_after": "unknown",
            "pre_state_version": i, "post_state_version": i + 1}}})
    events.append({"type": "termination", "payload": {
        "reason": termination, "terminal_status": "failed", "official_won": won,
        "env_steps": decisions, "note": "stopped"}})
    _write(d / "events.jsonl", events)
    summary = {"episode_id": name, "task_id": task_id or f"{name}/trial",
               "task_type": task_type,
               "repeat": repeat, "slot_index": slot_index,
               "instruction": "put a cool apple in bin",
               "instruction_sha256": "abc",
               "result": {"decision_rounds": decisions, "semantic_repairs": 0,
                          "model_errors": 0},
               "tokens": {"http_requests": decisions, "total": 1000 * decisions},
               "evaluation": {"official_won": won, "env_done": won, "env_steps": decisions,
                              "termination_reason": termination,
                              "false_finish": false_finish}}
    (d / "episode_summary.json").write_text(json.dumps(summary))
    return str(tmp_path)


# ------------------------------------------------------------------ evidence ----

def test_a_round_that_got_no_answer_is_a_deviation_even_though_it_was_not_an_error(tmp_path):
    """"Nothing happens." is `completed`: the refusal to treat it as success is the
    whole reason the repeat-guard exists, so it must reach the annotator."""
    root = _episode(tmp_path, status="completed", env_text="Nothing happens.")
    arts = annotate.episode_artifacts(os.path.join(root, "episodes", "slot000"))
    devs = annotate.deviations(arts)
    assert len(devs) == 2
    assert [d["round_index"] for d in devs] == [1, 2]      # from decision_context, not guessed
    assert devs[0]["command_sent"] == "take apple 1 from fridge 1"
    assert devs[0]["evidence_refs"] == ["obs_0001"]
    assert devs[0]["is_first"] is True and devs[1]["is_first"] is False


def test_a_round_that_reported_new_information_is_not_a_deviation(tmp_path):
    root = _episode(tmp_path, status="completed", env_text="You open the fridge.")
    arts = annotate.episode_artifacts(os.path.join(root, "episodes", "slot000"))
    assert annotate.deviations(arts) == []


def test_an_episode_that_never_got_an_error_reply_still_gets_an_evidence_packet(tmp_path):
    """The most common failure in this backend is a clarify with no failed command.
    Dropping those rows would make the taxonomy describe only the loud failures."""
    root = _episode(tmp_path, status="completed", env_text="You turn on the desklamp.",
                    termination="NEEDS_CLARIFICATION")
    arts = annotate.episode_artifacts(os.path.join(root, "episodes", "slot000"))
    row = annotate.episode_row(arts, segment="screening")
    assert row["evidence_basis"] == "stop_without_failed_command"
    assert row["deviations"] == [] and row["deviations_total"] == 0
    assert row["final_decision"]["action"] == "execute"
    assert row["termination"]["reason"] == "NEEDS_CLARIFICATION"


def test_an_infrastructure_row_produces_no_annotation_target(tmp_path):
    """A slot that never terminated has no verdict; a label on it would be a label on
    the harness, not on the agent (SPEC-BST §6.4)."""
    root = _episode(tmp_path)
    p = os.path.join(root, "episodes", "slot000", "episode_summary.json")
    s = json.load(open(p))
    s["evaluation"]["termination_reason"] = None
    json.dump(s, open(p, "w"))
    arts = annotate.episode_artifacts(os.path.join(root, "episodes", "slot000"))
    assert annotate.episode_row(arts) is None


def test_the_cap_reports_how_much_was_left_unread(tmp_path):
    root = _episode(tmp_path, decisions=6)
    arts = annotate.episode_artifacts(os.path.join(root, "episodes", "slot000"))
    row = annotate.episode_row(arts, cap=3)
    assert len(row["deviations"]) == 3 and row["deviations_total"] == 6


# ------------------------------------------------------------------ sampling ----

def _rows(root, names, markers=None, **kw):
    """The rows `build_worklist` would see. `markers` is keyed by episode id because the
    loop threshold is published by `aggregate`, and the sampler must judge the same
    number the report prints."""
    return [annotate.episode_row(annotate.episode_artifacts(
                os.path.join(str(root), "episodes", n)),
            markers=(markers or {}).get(n), **kw) for n in names]


def _ids(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return [json.loads(l) for l in lines[1:]]                # line 1 is the header


def test_screening_reads_every_failure_plus_one_success_reference_per_type(tmp_path):
    """SPEC-BST 10.3: the screening sample is all non-successes, and one winning trace
    per type when one exists — a reviewer who only ever sees failures loses the ability
    to tell a hard task from a hard failure."""
    for i in range(3):
        _episode(tmp_path, name=f"f{i:03d}", won=False, task_id=f"task{i}/trial")
    _episode(tmp_path, name="w0", won=True, task_id="task0/trial")
    _episode(tmp_path, name="w1", won=True, task_id="task1/trial",
             task_type="pick_heat_then_place_in_recep")
    _episode(tmp_path, name="w2", won=True, task_id="task2/trial",
             task_type="look_at_obj_in_light")
    picked = annotate.select(_rows(tmp_path, ["f000", "f001", "f002", "w0", "w1", "w2"]),
                             "screening")
    reasons = picked["reasons"]
    assert sum(1 for v in reasons.values() if "failure" in v) == 3
    assert sum(1 for v in reasons.values() if "success_reference" in v) == 3
    assert len(picked["rows"]) == 6


def test_the_screening_sample_reports_nothing_to_reference_when_there_are_no_wins(tmp_path):
    """With 0/48 successes the success-reference branch is empty, and the report has to
    say that rather than look like it sampled and found nothing."""
    _episode(tmp_path, name="f000", won=False)
    picked = annotate.select(_rows(tmp_path, ["f000"]), "screening")
    assert picked["wins"] == 0 and len(picked["rows"]) == 1


def test_every_failing_task_id_gets_a_trace_read(tmp_path):
    """§10.3: one failing trace per task_id, not one per type. A per-type sample can
    cover a type with a single task and call the other five unread."""
    names = []
    for t in range(6):
        for r in range(2):
            n = f"slot{t}{r}"
            _episode(tmp_path, name=n, won=False, task_id=f"task{t}/trial", repeat=r,
                     slot_index=t * 2 + r)
            names.append(n)
    picked = annotate.select(_rows(tmp_path, names), "full")
    covered = {r["task_id"] for r in picked["rows"]}
    assert covered == {f"task{t}/trial" for t in range(6)}
    assert any("one_per_failing_task" in v for v in picked["reasons"].values())


def test_a_task_whose_two_repeats_disagree_is_read_as_a_pair(tmp_path):
    """The contrast is the evidence: one trace won and one did not, same task, same
    seed family. §10.3 requires both to be looked at together."""
    _episode(tmp_path, name="slot0", won=True, task_id="pair/trial", repeat=0)
    _episode(tmp_path, name="slot1", won=False, task_id="pair/trial", repeat=1)
    _episode(tmp_path, name="slot2", won=False, task_id="other/trial", repeat=0)
    picked = annotate.select(_rows(tmp_path, ["slot0", "slot1", "slot2"]), "full")
    pair = [eid for eid, v in picked["reasons"].items() if "repeat_disagreement_pair" in v]
    assert sorted(pair) == ["slot0", "slot1"]
    assert "slot0" in {r["episode_id"] for r in picked["rows"]}


def test_the_forced_cases_are_in_the_sample_and_say_why_they_are_there(tmp_path):
    """false_finish, a model error and a repeat at the 8.3 loop threshold must be read
    whatever the draw says; each row carries the reason it was picked so a reader can
    check that the rule, not the result, chose it."""
    names = []
    for t in range(4):
        for r in range(3):
            n = f"slot{t}{r}"
            _episode(tmp_path, name=n, won=False, task_id=f"task{t}/trial", repeat=r,
                     slot_index=t * 3 + r)
            names.append(n)
    # mark slot32 as a false finish + model error, and slot20 as a loop case
    fp = os.path.join(str(tmp_path), "episodes", "slot32", "episode_summary.json")
    s = json.load(open(fp)); s["evaluation"]["false_finish"] = True
    s["result"]["model_errors"] = 1; json.dump(s, open(fp, "w"))
    rows = _rows(tmp_path, names, markers={
        "slot20": {"longest_stuck_repeat": annotate.LOOP_THRESHOLD},
        "slot32": {"longest_stuck_repeat": 0}})
    picked = annotate.select(rows, "full")
    why = picked["reasons"]
    assert "false_finish" in why["slot32"] and "model_error" in why["slot32"]
    assert "loop_threshold" in why["slot20"]
    assert picked["stratified_draw"]["total_drawn"] == 8      # min(8, 12) of one type
    assert picked["stratified_draw"]["seed"] == annotate.SELECTION_SEED


def test_the_worklist_pulls_the_loop_markers_from_the_aggregators_own_file(tmp_path):
    """`aggregate` defines what a stuck repeat is; `annotate` reads its published number
    instead of computing a second one, and puts it in the packet the reviewer sees."""
    _episode(tmp_path, name="slot0", won=False, task_id="t/trial")
    _write(tmp_path / "failure_candidates.jsonl", [
        {"episode_id": "slot0",
         "automatic_markers": {"longest_stuck_repeat": 4, "schema_invalid": 2,
                               "semantic_repairs": 1,
                               "refusal_reasons": {"subgoal: Extra inputs are not "
                                                   "permitted": 2}}}])
    annotate.build_worklist(str(tmp_path), "screening", str(tmp_path / "wl.jsonl"))
    row = _ids(tmp_path / "wl.jsonl")[0]
    assert row["run_markers"]["longest_stuck_repeat"] == 4
    assert row["run_markers"]["refusal_reasons"] == {
        "subgoal: Extra inputs are not permitted": 2}


def test_the_sample_is_the_same_sample_twice(tmp_path):
    """A selection that moved between two runs of identical artifacts would make the
    pre-registered rule unfalsifiable."""
    names = [f"slot{i}" for i in range(9)]
    for i, n in enumerate(names):
        _episode(tmp_path, name=n, won=False, task_id=f"task{i%3}/trial", repeat=i // 3)
    a = [r["episode_id"] for r in _rows(tmp_path, names)]
    one = annotate.select(_rows(tmp_path, names), "full")
    two = annotate.select(_rows(tmp_path, names), "full")
    assert [r["episode_id"] for r in one["rows"]] == [r["episode_id"] for r in two["rows"]]
    assert a and one["reasons"]


def test_the_worklist_header_carries_the_rule_and_the_criteria_it_was_written_against(tmp_path):
    root = _episode(tmp_path)
    annotate.build_worklist(root, "screening", os.path.join(root, "wl.jsonl"))
    header = json.loads(open(os.path.join(root, "wl.jsonl")).readline())
    assert header["sampled_episodes"] == 1
    assert header["guidelines"]["sha256"] and header["taxonomy"] == list(annotate.TAXONOMY)
    assert header["run_segment"] is None              # no manifest: says so, not a guess
    assert header["blind"] is False
    assert header["selection_coverage"]["sampled_over_benchmark_valid"] == 1.0
    assert header["selection_reason_counts"] == {"failure": 1}


def test_blind_review_cannot_see_the_outcome(tmp_path):
    """§10.3 wants the pattern judged before the verdict is known, so a blind packet
    loses both the grader's field and the runtime's, and the reason that names a winner
    is not written into it either."""
    _episode(tmp_path, name="slot0", won=False)
    _episode(tmp_path, name="slot1", won=True)
    annotate.build_worklist(str(tmp_path), "screening", os.path.join(str(tmp_path),
                                                                     "wl.jsonl"),
                            blind=True)
    rows = _ids(os.path.join(str(tmp_path), "wl.jsonl"))
    by = {r["episode_id"]: r for r in rows}
    assert set(by) == {"slot0", "slot1"}
    for eid, row in by.items():
        assert "outcome" not in row and "termination" not in row
        assert "success_reference" not in row["selection_reason"]
        assert row["deviations"][0]["environment_response"] == "Nothing happens."
        assert row["final_decision"]["action"] == "execute"   # evidence survives
    header = json.loads(open(os.path.join(str(tmp_path), "wl.jsonl")).readline())
    assert header["won_episodes_in_population"] == 1        # aggregate stays visible
    assert header["selection_coverage"]["wins_in_sample"] == 1
    assert "termination" in header["blind_scope"]


# -------------------------------------------------------------------- the gate ----

def _annotation(**over):
    row = {"episode_id": "slot000", "task_id": "slot000/trial", "decision_id": "d_1",
           "instruction_verbatim": "put a cool apple in bin",
           "command_sent": "take apple 1", "environment_response": "Nothing happens.",
           "evidence_refs": ["obs_0001"], "labels": ["REPEATED_FAILURE"],
           "confidence": "confirmed", "reason": "the same command was sent twice and "
           "the reply never changed",
           "competing_explanation": "the object may not be takeable here"}
    row.update(over)
    return row


def _gate(tmp_path, rows):
    p = str(tmp_path / "annotations.jsonl")
    _write(p, rows)
    return annotate.validate_rows(p)


def test_a_label_without_a_competing_explanation_is_not_an_annotation(tmp_path):
    report = _gate(tmp_path, [_annotation(competing_explanation="")])
    assert report["invalid"][0]["missing"] == ["competing_explanation"]


def test_a_label_without_the_task_it_is_about_is_not_an_annotation(tmp_path):
    """§2 lists `task_id` among the minimum evidence, and the gate is what makes that
    list more than prose: a finding that cannot say which task it belongs to cannot be
    read against the per-type tables in the report."""
    report = _gate(tmp_path, [_annotation(task_id="")])
    assert report["invalid"][0]["missing"] == ["task_id"]


def test_a_refused_round_can_be_annotated_at_all(tmp_path):
    """The rounds that dominate this batch produced no `Decision` and no environment
    reply: the payload was refused before either existed. If the gate insisted on a
    decision id or on an environment sentence, the most common failure in the experiment
    would be the one failure that could not be recorded."""
    row = _annotation(decision_id=None, context_id="ctx_1a2b", environment_response=None,
                      evidence_refs=[], rejection_reasons=["subgoal: Extra inputs are not "
                                                           "permitted"], command_sent="none")
    assert _gate(tmp_path, [row])["invalid"] == []
    neither = _gate(tmp_path, [dict(row, context_id=None)])
    assert "decision_id|context_id" in neither["invalid"][0]["missing"]
    no_evidence = _gate(tmp_path, [dict(row, rejection_reasons=[])])
    assert any("rejection_reasons" in m for m in no_evidence["invalid"][0]["missing"])


def test_a_label_outside_the_frozen_taxonomy_is_refused(tmp_path):
    report = _gate(tmp_path, [_annotation(labels=["THE_MODEL_WAS_DUMB"])])
    assert any("labels_not_in_taxonomy" in m for m in report["invalid"][0]["missing"])


def test_the_models_own_words_cannot_be_the_only_support(tmp_path):
    """SPEC-BST §9.1: a rationale is evidence about what the model said, not about why
    the episode failed. Copying it into `reason` must not pass the gate."""
    row = _annotation(model_rationale="the same command was sent twice and the reply "
                                       "never changed")
    report = _gate(tmp_path, [dict(row, model_rationale="something else entirely")])
    assert report["invalid"] == []            # an independent reason is allowed
    report = _gate(tmp_path, [row])           # reason copied verbatim from the model
    assert "reason_is_only_model_rationale" in report["invalid"][0]["missing"]


def test_an_empty_worklist_row_is_unlabeled_not_invalid(tmp_path):
    """Two different failures: work not done yet, and work done without support. Only
    the second one may not be shipped."""
    row = _annotation(labels=[], reason="", competing_explanation="")
    report = _gate(tmp_path, [row])
    assert report["unlabeled"] and not report["invalid"]


def test_a_claim_that_a_better_action_existed_needs_its_basis(tmp_path):
    report = _gate(tmp_path, [_annotation(claims_better_action=True)])
    assert "counterfactual_basis" in report["invalid"][0]["missing"]
    report = _gate(tmp_path, [_annotation(claims_better_action=True,
                                          counterfactual_basis="look first is in the "
                                          "catalogue and obs_0001 listed the cabinet")])
    assert report["invalid"] == []


def test_the_gate_says_nothing_about_whether_the_label_is_right(tmp_path):
    """`validate` checks the shape of evidence, not the judgement. A row that is wrong
    but supported passes, and that is the correct behaviour for a mechanical gate."""
    assert _gate(tmp_path, [_annotation(labels=["UNRESOLVED"])])["invalid"] == []
