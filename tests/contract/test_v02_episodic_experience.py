"""SPEC-v0.2 §5.4 contract tests: what an experience may contain, and where it may come from.

The question this file asks is narrow: **is every field of an `EpisodicExperience` something a
record already said?** §5.4 lists seven things a cross-episode experience must carry, and the
research value of carrying them is that a later episode can act on them — which makes an invented
field worse than a missing one, because an invented field is a claim about a past run that no past
run made. So the tests here pin provenance rather than values:

* the seven §5.4 fields each have a named source in the episode's own two artifacts
  (`episode_summary.json`, `events.jsonl`), and a missing source reads as empty, not as a guess;
* `outcome` (the loop's terminal reading) and `official_success` (the third-layer verdict) are
  never merged — the disagreement between them is a finding, and P1 measured one;
* the state pattern is read from whichever instrument actually measured, and a *work* status
  (`pending`) is never promoted into a world verdict (`false`);
* 经验来源 is mandatory: an untraceable row cannot be stored at all;
* the store is append-only, refuses a second row for one episode, refuses a foreign
  `schema_version` at the file boundary, and derives its ids rather than drawing them — a random id
  would let the same episode in twice and make a frozen seed file impossible to re-derive.

Fixture shapes were copied field-for-field from the real P2-e batch under /tmp, traps included: the
event envelope says `schema_version: "3"` while the payload rows are schema 4, `satisfied` arrives
as the strings `'True'`/`'unknown'` from `str(bool)`, `status` as a `SkillStatus` (`completed`)
rather than a `Status`, and the terminal `plan` event of five of six episodes is the *round-1* view.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os

import pytest

from embodied_agent.core.v02 import SCHEMA_VERSION, EpisodicExperience, OutcomeLabel, Source, Status
from embodied_agent.episodic import ExperienceStore, experience_from_episode, experience_id_for
from embodied_agent.episodic.experience import (TRUNCATING_FAILURE_CODES, _truth, final_plan_rows,
                                                last_working_memory_status, outcome_of,
                                                read_episode_dir, state_pattern_of, steps_of)

EPISODE = "lh_c3_capacity_and_shift.B.r0"
RUN_DIR = "/tmp/frozen_batch/runs/long_horizon_rule_full_20260101_000000"
EPISODE_DIR = f"{RUN_DIR}/episodes/{EPISODE}"
PREDICATES = ["placed:obj_blue_5:tray_middle", "placed:obj_yellow_1:tray_middle",
              "placed:obj_green_4:tray_left"]


def _plan_row(predicate, *, satisfied="unknown", history=()):
    """One row of a published plan view. `subgoal_id` is derived, not `hash()`-drawn: P2 found a
    salted hash giving two trajectories from one payload, and a fixture that reused the trap would
    hide it rather than test around it."""
    return {"subgoal_id": "sub_" + hashlib.sha1(predicate.encode()).hexdigest()[:8],
            "row_key": predicate, "kind": "achieve", "status": "pending",
            "statement": f"{predicate.split(':')[1]} is seated in {predicate.split(':')[2]}",
            "predicate_id": predicate, "satisfied": satisfied, "unmeasured": [],
            "completion_evidence": ["obs_0019"] if satisfied != "unknown" else [],
            "depends_on": [], "attempts": 0, "target_entity_ids": [predicate.split(":")[1]],
            "target_region_id": predicate.split(":")[2], "open_questions": [],
            "history": list(history)}


def _plan_event(rows, *, version=1, sequence=30):
    return {"schema_version": "4", "event_id": f"evt_{sequence}", "episode_id": EPISODE,
            "sequence": sequence, "type": "plan",
            "payload": {"arm": "full", "view": {"version": version, "rows": list(rows)}}}


def _feedback(skill, entity, target=None, *, status="completed", code=None, executed=True,
              sequence=13, post="obs_0004"):
    return {"schema_version": "3", "event_id": f"evt_{sequence}", "episode_id": EPISODE,
            "sequence": sequence, "type": "execution_feedback",
            "payload": {"feedback": {
                "feedback_id": f"fb_{sequence}", "decision_id": f"d_{sequence}",
                "call_id": f"c_{sequence}", "context_id": f"ctx_{sequence}",
                "skill": skill, "entity_id": entity, "target_id": target, "candidate_id": None,
                "executed": executed, "stages_executed": ["precondition", "grasp_check"],
                "status": status, "failure_code": code, "pre_observation_ref": "obs_0001",
                "post_observation_ref": post, "new_measurement": True,
                "pre_state_version": 1, "post_state_version": 4,
                "verification": {"reports": []}}}}


def _decision(sequence=12):
    return {"schema_version": "4", "event_id": f"evt_{sequence}", "episode_id": EPISODE,
            "sequence": sequence, "type": "decision",
            "payload": {"arm": "full", "round_index": 1, "action": {"skill": "pick"}}}


def _working_memory(subgoal_status, *, observation_ref="obs_0055", round_index=13, sequence=120):
    return {"schema_version": "4", "event_id": f"evt_{sequence}", "episode_id": EPISODE,
            "sequence": sequence, "type": "working_memory",
            "payload": {"arm": "full", "state_version": 55, "observation_ref": observation_ref,
                        "round_index": round_index, "plan_version": 1,
                        "subgoal_status": dict(subgoal_status), "commitments": [],
                        "suspended_relations": [], "failed_attempts": [], "assumptions": [],
                        "unknown_facts": [], "open_questions": [], "recalled": []}}


def _summary(*, episode=EPISODE, terminal="success", failure_type=None, completed=6, total=6,
             complete_success=True, channel="privileged", reason=None, details=True):
    task = episode.split(".")[0]
    score = {"eval_id": f"eval-{task}", "scored_observation_ref": "obs_0056",
             "scored_state_version": 56, "objects_total": total,
             "objects_completed": completed, "complete_success": complete_success,
             "details": ({f"obj_{i}": {"target": "tray_middle", "satisfied": True}
                          for i in range(completed)} if details else {})}
    return {
        "episode_id": episode, "case_id": task, "set": "long_horizon", "mode": "B", "repeat": "0",
        "expected": "success", "score": score,
        "perception": {"channel": channel, "arm": "full", "looks": 0},
        "decision_source": {"mode": "B", "policy": "rule", "provider": "rule_policy"},
        "goal_resolution": {"planner": "rule",
                            "assignments": [[{"shape": "cube", "color": "yellow"}, "tray_middle"],
                                            [{"shape": "cylinder", "color": "green"},
                                             "tray_left"]]},
        "result": {"episode_id": episode, "task_id": task, "terminal_status": terminal, "mode": "B",
                   "decision_rounds": 13, "skill_calls": 12, "http_requests": 0,
                   "objects_total": total, "objects_completed": completed,
                   "failure_type": failure_type, "recovery_events": 0, "retry_events": 0,
                   "replan_events": 0, "rejected_decisions": 0, "false_finish_attempts": 0,
                   "identical_repeats_total": 0, "sim_time_s": 35.05, "wall_time_s": 2.94,
                   "termination_reason": reason, "api_cost_estimate": None},
        "artifacts": {"episode_dir": f"{RUN_DIR}/episodes/{episode}", "terminal_snapshot":
                      "obs_0056", "frames": 0},
    }


def _events(status=("done", "done", "pending"), *, rows=None, observation_ref="obs_0055"):
    """The five rows a `full`-arm episode files, in file order: one decision, three executed
    feedbacks (the third a grasp miss), the round-1 plan view, and the last round's memory."""
    plan_rows = rows if rows is not None else [_plan_row(p) for p in PREDICATES]
    return [_decision(),
            _feedback("pick", "obj_yellow_1", sequence=13),
            _feedback("place", "obj_yellow_1", "tray_middle", sequence=17, post="obs_0008"),
            _feedback("pick", "obj_blue_5", sequence=21, status="failed", code="GRASP_MISS",
                      post="obs_0012"),
            _plan_event(plan_rows),
            _working_memory(dict(zip(PREDICATES, status)), observation_ref=observation_ref)]


def _row(**kwargs):
    return experience_from_episode(_summary(**kwargs), _events())


# ------------------------------------------------------------------ §5.4 field provenance ----

def test_six_of_the_seven_fields_are_populated_from_this_episodes_own_records():
    """§5.4's 失败原因 is the exception that proves the rule: a successful episode has no reason
    to report, and the field is left empty rather than filled with a sentence about nothing. The
    near-miss is not lost — it is a property of the step that made it."""
    exp = _row()
    assert exp.task_context.startswith("long_horizon/lh_c3_capacity_and_shift")
    assert exp.state_pattern and exp.steps and exp.applicability and exp.run_ref == EPISODE_DIR
    assert exp.outcome is OutcomeLabel.success
    assert exp.failure_reason == ""
    assert exp.steps[2].effect == "failed:GRASP_MISS"
    assert exp.task_kind == "long_horizon"
    assert "task_kind:long_horizon" in exp.applicability


#: what each module of the episodic package may reach for. The two record modules and the store
#: carry §3's original claim: an experience that asked the simulator or a reader a question would
#: stop being a residue of a finished episode and become a second, later-dated source of world
#: facts. `arm` and `policy` are P3-c modules and get the mirror-image rule: they are allowed to
#: touch the loop precisely because their job is to be in it, so what must be kept out of them is
#: the *measuring* layer — an arm that could read `evaluation` could read the third-layer verdict
#: too, which is §3.2's forbidden edge, and a policy that could import a store would stop being a
#: reader of the payload and become a reader of the past through a side channel no view declares.
_IMPORT_BOUNDARIES: dict[str, set[str]] = {
    "experience": {"scene", "runtime", "verify", "observe", "deepseek", "world_state",
                   "frames", "camera", "run", "report", "evaluator", "cli", "arm", "policy"},
    "retrieval": {"scene", "runtime", "verify", "observe", "deepseek", "world_state",
                  "frames", "camera", "run", "report", "evaluator", "cli", "arm", "policy"},
    "store": {"scene", "runtime", "verify", "observe", "deepseek", "world_state",
              "frames", "camera", "run", "report", "evaluator", "cli", "arm", "policy",
              "experience", "retrieval"},
    "arm": {"run", "report", "evaluator", "protocol", "tasks", "long_horizon", "sources",
            "state_utilization", "vlm_contrast", "blind_review", "preregistration",
            "calibration", "benchmark", "deepseek", "policy"},
    "policy": {"scene", "runtime", "verify", "observe", "deepseek", "world_state", "frames",
               "camera", "run", "report", "evaluator", "cli", "arm", "store", "experience",
               "retrieval", "benchmark"},
}


def _imported_names(module) -> set[str]:
    """The tail of every import this module's own source names, relative imports included.

    Tail, not full dotted path, because that is what a violation looks like in practice: a module
    written as `from ..core.runtime import Runtime` in one file and
    `from embodied_agent.core import runtime` in another is the same edge.
    """
    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[-1] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[-1])
            # `from . import store` names the module only in the alias, so take those too
            imported |= {a.name for a in node.names if not node.module}
    return imported


def test_the_extractor_opens_two_files_and_reaches_for_nothing_else():
    """§3's separation, checked as an import graph, one boundary per module rather than one rule
    for the whole package: P3-c added two modules that legitimately import the loop, and widening
    the old assertion to cover them would have meant weakening it."""
    import importlib

    import embodied_agent.episodic

    for name, forbidden in _IMPORT_BOUNDARIES.items():
        module = importlib.import_module(f"embodied_agent.episodic.{name}")
        imported = _imported_names(module)
        assert not forbidden & imported, (module.__name__, sorted(forbidden & imported))
        if name in ("experience", "retrieval", "store"):
            assert "v02" in imported or "contracts" in imported, module.__name__

    # The package's own top level may not undo all of that by importing the two heavy modules
    # eagerly: `from embodied_agent.episodic import ExperienceStore` is how callers reach the
    # record modules, and an eager `from .arm import ...` would put a simulator behind it.
    tree = ast.parse(open(embodied_agent.episodic.__file__, encoding="utf-8").read())
    eager = {node.module.split(".")[-1] for node in tree.body
             if isinstance(node, ast.ImportFrom) and node.module}
    assert not {"arm", "policy"} & eager, sorted(eager)
    assert set(embodied_agent.episodic._LAZY.values()) == {"arm", "policy"}


def test_importing_the_store_does_not_load_a_simulator():
    """The consequence, not just the graph. A fresh interpreter does what the P3-b retrieval code
    needs — open the package and use `ExperienceStore` — and reports whether either the simulator
    or the runtime is loaded as a result.

    Two names, not one, because they are two different claims and only one of them is about weight:
    `pybullet` is the third-party dependency, and `core.runtime` is §3's forbidden edge itself —
    and the runtime reaches pybullet through `core.scene`, so the second is what makes the first
    appear. Checking only `pybullet` would pass on a build that imported the runtime and had not
    yet touched a scene, which is exactly the half-loaded state a boundary test should not accept.
    The static check above says the edge is absent from this package's source; this says nothing
    else about the package re-opens it at import time.
    """
    import subprocess
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    code = ("import sys, embodied_agent.episodic as ep; _ = ep.ExperienceStore; "
            "print('pybullet=%s runtime=%s' % ('pybullet' in sys.modules, "
            "'embodied_agent.core.runtime' in sys.modules))")
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": root})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "pybullet=False runtime=False", done.stdout


def test_a_field_with_no_record_stays_empty_instead_of_becoming_a_guess():
    exp = experience_from_episode(_summary(details=False), [])
    assert exp.state_pattern == []
    assert exp.provenance["state_pattern_from"] == "no_record"
    assert exp.steps == [] and exp.counter_examples == []


def test_outcome_and_official_success_are_not_merged_into_one_label():
    """§12.3: 以实际动作和最终状态为准 — and the loop's own word is not the final state. P1
    measured a sensing arm self-reporting more completed objects than the key gave it, so the
    disagreement has to survive into the row that a later episode reads."""
    claimed = experience_from_episode(
        _summary(terminal="success", completed=2, total=6, complete_success=False), _events())
    assert claimed.outcome is OutcomeLabel.success
    assert claimed.official_success is False


@pytest.mark.parametrize("terminal, failure_type, completed, total, expected", [
    ("success", None, 6, 6, OutcomeLabel.success),
    ("cancelled", None, 6, 6, OutcomeLabel.error),
    ("failed", "BUDGET_EXHAUSTED", 3, 6, OutcomeLabel.truncated),
    ("failed", "TIMEOUT", 3, 6, OutcomeLabel.truncated),
    ("failed", "GRASP_MISS", 3, 6, OutcomeLabel.partial),
    ("failed", "GRASP_MISS", 0, 6, OutcomeLabel.failure),
    ("needs_clarification", None, 6, 6, OutcomeLabel.failure),
])
def test_the_collapse_from_four_terminal_statuses_to_five_labels_is_the_written_one(
        terminal, failure_type, completed, total, expected):
    got = outcome_of({"terminal_status": terminal, "failure_type": failure_type},
                     {"objects_completed": completed, "objects_total": total})
    assert got is expected


def test_an_episode_that_stopped_by_asking_still_says_so_in_the_reason():
    """`needs_clarification` has no label of its own in the frozen enum. Folding it into `failure`
    must not lose the fact that the loop stopped by asking a question."""
    exp = experience_from_episode(
        _summary(terminal="needs_clarification", completed=6, total=6, complete_success=True,
                 reason="which of the two green cubes?"), _events())
    assert exp.outcome is OutcomeLabel.failure
    assert "needs_clarification" in exp.failure_reason
    assert "which of the two green cubes?" in exp.failure_reason


def test_only_the_two_codes_the_budget_actually_stops_on_read_as_truncation():
    assert TRUNCATING_FAILURE_CODES == frozenset({"BUDGET_EXHAUSTED", "TIMEOUT"})


# ------------------------------------------------------------------------ the action ledger ----

def test_a_step_is_one_executed_feedback_and_its_effect_is_the_verdict_not_the_hope():
    steps = steps_of(_events())
    assert [s.skill for s in steps] == ["pick", "place", "pick"]
    assert steps[0].effect == "completed"
    assert steps[2].effect == "failed:GRASP_MISS"
    assert steps[0].round_index == 1
    assert steps[0].observation_ref == "obs_0004"
    assert steps[1].args == {"object_id": "obj_yellow_1", "target_id": "tray_middle"}


def test_an_unexecuted_feedback_is_not_a_step():
    events = [_decision(), _feedback("place", "obj_ghost", "tray_left", executed=False),
              _feedback("pick", "obj_blue_5")]
    assert [s.skill for s in steps_of(events)] == ["pick"]


def test_failure_reason_names_the_terminal_code_then_the_last_step_that_did_not_complete():
    exp = experience_from_episode(_summary(terminal="failed", failure_type="INVALID_DECISION",
                                           completed=3, total=6, complete_success=False),
                                  _events())
    assert exp.failure_reason.startswith("INVALID_DECISION")
    assert "last uncompleted step: pick obj_blue_5" in exp.failure_reason
    assert "GRASP_MISS" in exp.failure_reason


def test_a_counter_example_is_a_measured_flip_and_not_a_retail_of_speculation():
    flipped = [_plan_row(PREDICATES[0], satisfied="False",
                         history=["was done and is False on obs_0019: the obligation returned"]),
               _plan_row(PREDICATES[1], satisfied="True"),
               _plan_row(PREDICATES[2], satisfied="False")]
    exp = experience_from_episode(_summary(), _events(rows=flipped))
    assert len(exp.counter_examples) == 1
    assert "was done and is False" in exp.counter_examples[0]


def test_one_episode_is_carried_as_a_caution_about_itself():
    exp = _row()
    assert "a single layout is not a rule" in exp.transfer_caution
    assert f"({EPISODE})" in exp.transfer_caution
    assert "outcome=success" in exp.transfer_caution


# ------------------------------------------------------------- the state pattern's provenance ----

def test_the_last_filed_plan_view_is_not_the_terminal_one_and_is_not_trusted_as_such():
    """Five of six episodes on the real P2-e batch filed exactly one `plan` event — the round-1
    view, whose rows are unmeasured. Trusting it as the episode's ending would have written three
    satisfied goals into memory as unknowns, on an episode the key scored 6/6."""
    events = _events(status=("done", "done", "done"))
    assert len([e for e in events if e["type"] in ("plan", "plan_revision")]) == 1
    pattern, source = state_pattern_of(final_plan_rows(events), None,
                                       last_working_memory_status(events)[0])
    assert source == "terminal_plan_view+working_memory"
    assert all(line.endswith("=true") for line in pattern), pattern


@pytest.mark.parametrize("live, filed, expected", [
    ("done", "unknown", "true"),
    ("done", "False", "true"),
    ("pending", "False", "false"),
    ("pending", "unknown", "unknown"),
    ("failed", "unknown", "unknown"),
    ("unknown", "True", "true"),
])
def test_only_a_measurement_may_answer_the_pattern_and_a_work_status_never_fakes_one(
        live, filed, expected):
    rows = [_plan_row(PREDICATES[0], satisfied=filed)]
    pattern, _ = state_pattern_of(rows, _summary()["score"], {PREDICATES[0]: live})
    assert pattern == [f"{PREDICATES[0]}={expected}"]


def test_the_pooled_pattern_keeps_the_open_questions_visible():
    """The `=unknown` count is a finding, not a formatting accident: on the real batch the six
    predicates of the one truncated episode are all unknown to the loop while the key scored two
    of them satisfied. A pattern that reported `false` instead would have hidden the gap."""
    pattern = _row().state_pattern
    assert sorted(line.rsplit("=", 1)[1] for line in pattern) == ["true", "true", "unknown"]


def test_pending_is_not_unknown_because_the_frozen_enum_says_so():
    """`Status` documents `pending` as "the work has not started" and `unknown` as "nobody has
    looked"; a reader of a stored pattern must still be able to tell those apart two episodes
    later."""
    for work_status in ("pending", "active", "failed", "abandoned", Status.unknown.value):
        assert _truth(work_status) == "unknown", work_status
    assert _truth("completed") == "unknown"
    assert _truth(False) == "false" and _truth("False") == "false"
    assert _truth(True) == "true" and _truth("True") == "true"
    assert _truth(None) == "unknown"


def test_the_pattern_falls_back_to_the_independent_score_only_when_the_loop_said_nothing():
    """A different instrument, so the artifact has to name which one answered — including when the
    planning module was ablated and no view exists at all."""
    pattern, source = state_pattern_of([], {"details": {"obj_a": {"target": "tray_left",
                                                                 "satisfied": True}}}, None)
    assert source == "independent_score"
    assert pattern == ["placed:obj_a:tray_left=true"]


def test_the_observation_the_pattern_was_read_from_is_recorded_with_it():
    exp = experience_from_episode(_summary(), _events(observation_ref="obs_0077"))
    assert exp.provenance["state_pattern_ref"] == "obs_0077"
    assert exp.based_on_observation_ref == "obs_0056"


def test_which_channel_saw_the_episode_decides_what_kind_of_claim_the_row_is():
    sensor = _row(channel="rgbd")
    privileged = _row(channel="privileged")
    assert sensor.source is Source.sensor and sensor.provenance["channel"] == "rgbd"
    assert privileged.source is Source.privileged


# --------------------------------------------------------------------------------- identity ----

def test_an_experience_id_is_derived_from_where_it_came_from_not_drawn():
    """A random id lets one episode be stored twice, and a frozen seed file could not be
    re-derived from its runs. `hash()` is not a substitute either: it is salted per process."""
    stable = experience_id_for(EPISODE, EPISODE_DIR)
    assert stable == experience_id_for(EPISODE, EPISODE_DIR)
    assert stable == "exp_%s_%s" % (EPISODE, hashlib.sha1(
        f"{EPISODE}\n{EPISODE_DIR}".encode("utf-8")).hexdigest()[:8])
    assert stable != experience_id_for(EPISODE, f"{RUN_DIR}/episodes/another")
    assert stable != experience_id_for("lh_c4_failed_grasp_recovery.B.r0", EPISODE_DIR)


def test_re_extracting_the_same_episode_reproduces_the_same_row():
    events = _events()
    first = experience_from_episode(_summary(), events)
    second = experience_from_episode(_summary(), events)
    assert first.experience_id == second.experience_id
    def comparable(record):
        stamp = {"created_at", "updated_at"}
        return {k: v for k, v in record.model_dump().items() if k not in stamp}

    assert comparable(first) == comparable(second)


def test_an_experience_with_no_经验来源_cannot_be_built_at_all():
    summary = _summary()
    summary["artifacts"] = {}
    with pytest.raises(ValueError, match="经验来源"):
        experience_from_episode(summary, _events(), run_ref="")


def test_an_experience_that_names_no_episode_cannot_be_stored():
    summary = _summary()
    summary["result"]["episode_id"] = ""
    with pytest.raises(ValueError, match="names no episode"):
        experience_from_episode(summary, _events())


# ------------------------------------------------------------------------------------ store ----

def test_a_cold_start_is_a_measured_condition_not_an_error(tmp_path):
    store = ExperienceStore.load(str(tmp_path / "absent.jsonl"))
    assert len(store) == 0 and store.fingerprint() is None


def test_the_store_is_append_only_because_a_retrieval_row_cites_an_id(tmp_path):
    one = _row()
    store = ExperienceStore(str(tmp_path / "experiences.jsonl"), [one])
    with pytest.raises(ValueError, match="append-only"):
        store.append(_row())
    assert store.ids() == [one.experience_id]


def test_a_row_written_by_one_process_is_visible_to_the_next(tmp_path):
    path = str(tmp_path / "experiences.jsonl")
    one = _row()
    ExperienceStore(path, [one]).flush()
    reloaded = ExperienceStore.load(path)
    assert reloaded.ids() == [one.experience_id]
    assert reloaded.get(one.experience_id).model_dump() == one.model_dump()
    assert reloaded.fingerprint() == ExperienceStore(path).fingerprint()


def test_flushing_never_rewrites_a_line_that_is_already_on_disk(tmp_path):
    """An appended row is the only change a flush may make: a rewritten row would silently change
    what an earlier round was shown, and those rounds cite by id."""
    path = str(tmp_path / "experiences.jsonl")
    one = _row()
    ExperienceStore(path, [one]).flush()
    before = open(path, encoding="utf-8").read().splitlines()
    second = _row(episode="lh_c4_failed_grasp_recovery.B.r0")
    ExperienceStore(path, [second]).flush()
    after = open(path, encoding="utf-8").read().splitlines()
    assert after[0] == before[0]
    assert len(after) == 2 and json.loads(after[1])["episode_id"].startswith("lh_c4")


@pytest.mark.parametrize("version", ["3", "", None])
def test_a_foreign_record_generation_is_refused_at_the_file_boundary(tmp_path, version):
    """Schema 4 is frozen by fingerprint; a store that mixed generations would be a report unable
    to say which rules produced which row."""
    path = tmp_path / "mixed.jsonl"
    payload = json.loads(json.dumps(_row().model_dump(mode="json")))
    payload["schema_version"] = version
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        ExperienceStore.load(str(path))


def test_the_stored_bytes_are_hashable_so_a_manifest_can_name_this_store(tmp_path):
    path = str(tmp_path / "experiences.jsonl")
    one = _row()
    ExperienceStore(path, [one]).flush()
    first = ExperienceStore.load(path).fingerprint()
    assert first == hashlib.sha256(open(path, "rb").read()).hexdigest()
    ExperienceStore(path, [_row(episode="lh_c5_release_deviation.B.r0")]).flush()
    second = ExperienceStore.load(path).fingerprint()
    assert first != second and len(second) == 64


def test_a_store_with_no_file_cannot_be_flushed_but_can_still_answer_a_query():
    one = _row()
    store = ExperienceStore(None, [one])
    with pytest.raises(ValueError, match="in-memory"):
        store.flush()
    assert store.fingerprint() is None
    assert [r.experience_id for r in store.all()] == [one.experience_id]
    assert iter(store) is not None


def test_a_row_is_findable_by_the_two_things_a_reader_would_ask_about():
    one = _row()
    store = ExperienceStore(None, [one])
    assert store.find(task_kind="long_horizon") == [one]
    assert store.find(outcome="success") == [one]
    assert store.find(task_kind="other_set", outcome="success") == []
    assert store.get("exp_missing") is None


def test_the_record_model_still_refuses_the_v01_envelope():
    """The store's rows are frozen records: if a validation loophole let a schema-3 object into an
    `ExperienceStore`, the fingerprint discipline would be decorating the wrong file."""
    with pytest.raises(ValueError, match="schema_version"):
        EpisodicExperience.model_validate({"schema_version": "3",
                                           "experience_id": "exp_x", "episode_id": EPISODE,
                                           "run_ref": EPISODE_DIR})
    assert SCHEMA_VERSION == "4"


def test_the_working_memory_status_read_is_the_last_rounds_not_the_first():
    events = [_working_memory({PREDICATES[0]: "pending"}, observation_ref="obs_0010"),
              _working_memory({PREDICATES[0]: "done"}, observation_ref="obs_0020")]
    status, ref = last_working_memory_status(events)
    assert status == {PREDICATES[0]: "done"} and ref == "obs_0020"


def test_an_episode_can_be_read_from_its_directory_without_writing_anything(tmp_path):
    """And its 经验来源 is the path the artifact itself claimed, not wherever the directory happens
    to have been copied to — a row that pointed at the copy would outlive its own evidence."""
    episode_dir = tmp_path / EPISODE
    episode_dir.mkdir()
    events = _events()
    summary = _summary()
    (episode_dir / "episode_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with (episode_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    read_summary, read = read_episode_dir(str(episode_dir))
    assert read == events and read_summary["set"] == "long_horizon"
    exp = experience_from_episode(read_summary, read)
    assert exp.steps[0].skill == "pick"
    assert exp.run_ref == summary["artifacts"]["episode_dir"] != str(episode_dir)
    assert exp.experience_id == experience_id_for(EPISODE, summary["artifacts"]["episode_dir"])
    assert sorted(os.listdir(episode_dir)) == ["episode_summary.json", "events.jsonl"]
