"""P4-e contract tests: the arithmetic of §11's Skill Acquisition rows, and none of their values.

`evaluation/skill_metrics.py` fixes a 口径 before any of it is read off a batch, so what may be pinned
here is the *relationship between a number and its definition* — the same five things every §11 group is
tested for (LH-2 in `test_v02_long_horizon_metrics.py`, MEM-1 in
`test_v02_episodic_memory_metrics.py`):

* every id the scorer counts is defined before it is counted, at exactly one site, and §11's five rows
  plus the Cost group cover the id set exactly once — with the Cost group's fifth row (`tokens`)
  accounted for by name and a sentence saying who measures it, checked against the SPEC's own bullets
  rather than against a copy of them;
* a denominator of zero is not a rate of zero, a proportion cannot have a numerator larger than its
  denominator, and the two families that are not proportions (counts per unit, rows defined against the
  batch) keep the properties that make them different;
* the row envelope is key-for-key MEM-1's and LH-2's, so P5's report can print §11's three behaviour
  groups with one reader — with the one difference this module has to have, which is that a Cost row
  counts *seconds* and must not be truncated to an int;
* a pooled figure is a sum of integers and never a mean of fractions, and the two rows whose own
  `forbidden` cells refuse pooling stay unmeasured in the pooled table while still being published per
  batch;
* the gates are published apart rather than merged: the rule frozen in `core/v02.py` and §5.6's strict
  sentence, a proposal that cannot run and a candidate the budget declined to buy, a report that stopped
  at two of three axes and a matrix that named the axis it could not vary.

What no test here pins is a *measured* value. There is no completion rate, no refusal count and no
millisecond in this file — those are §11's findings, and a threshold written into a test turns the one
number that has to stay honest into a fixture.

The fixtures are synthetic directories on disk laid out as `evaluation/run.py` lays a run out:
`manifest.json`, `episodes/<id>/episode_summary.json` carrying the arm's own `skill_acquisition` block,
`events.jsonl`, and `validation/<candidate_id>.json`. They are copied field-for-field from the P4-e
batches, including the traps: `status` as the string `sandbox`, `not_validated` drawn *from* the
executable list rather than beside it, `refusals_in_store` a live store's running total rather than a
per-episode count, and `counts_as_reuse` withheld on a batch whose decision maker cannot choose. The
delivered `/tmp/p4e` artifact is re-scored at the end and has to reproduce its own numbers.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import re

import pytest

from embodied_agent.core.v02 import SCHEMA_VERSION, V02_EVENT_TYPES
from embodied_agent.evaluation import episodic_metrics as em
from embodied_agent.evaluation import long_horizon as lh
from embodied_agent.evaluation import report
from embodied_agent.evaluation import run
from embodied_agent.evaluation import skill_metrics as sk
from embodied_agent.evaluation.report import wilson_interval

ID_RE = re.compile(r"^[SC]\d\.[a-z][a-z_0-9]*$")
DEFINITION_KEYS = ("question", "unit", "numerator", "denominator", "truth", "forbidden")
#: §11's five Skill Acquisition rows, in the SPEC's own order.
SPEC_ROW_NAMES = ("candidate generation rate", "validation success", "cross-instance transfer",
                  "skill reuse success", "unsafe/invalid skill rate")
#: the four §11 Cost rows this group publishes, named as `SPEC_ROWS` names them.
COST_ROW_NAMES = ("model calls", "simulator time", "latency", "skill-generation cost")
POINTER = re.compile(r"^as (above|the|[SC]\d)", re.I)
BARE_RATIO = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
ROOT = "/tmp/p4e"
#: marker on the fixture builders: "compute this from what the fixture wrote", not "absent"
AUTO = object()

_PIPELINE = ("gap_detection", "proposal", "sandbox_and_verifier", "cross_instance_validation",
             "skill_memory")


# ------------------------------------------------------------- the fixture builders ----
def _gap(gap_id: str = "gap_1", *, covered=(), **extra) -> dict:
    g = {"gap_id": gap_id, "round_index": 13, "subgoal_id": "sub_a",
         "desired_effect": "grasp:obj_green_1", "missing_because": "no_termination", "attempts": 3,
         "considered": ["pick"], "covered": list(covered),
         "rule": "repeat_without_new_measurement", "event_ids": ["evt_1"]}
    g.update(extra)
    return g


def _candidate(candidate_id: str, *, status: str = "sandbox", gap_id: str = "gap_1") -> dict:
    return {"candidate_id": candidate_id, "gap_id": gap_id, "skill_name": "pick_up",
            "status": status, "proposed_by": "rule:propose_v2", "path": "rule",
            "parameters": ["object"], "steps": 1, "expected_effects": ["grasp:{object}"],
            "reasons": [] if status == "sandbox" else ["no primitive sequence"]}


def _withheld(candidate_id: str) -> dict:
    return {"candidate_id": candidate_id, "skill_name": "pick_up", "gap_id": "gap_1",
            "why": f"the per-episode validation budget of 1 candidate(s) is spent; {candidate_id} "
                   f"was never put in a sandbox"}


def _validation(candidate_id: str, *, fingerprint: str | None = None, frozen: bool = True,
                strict: bool = True, instances: int = 4, ran: int = 4, measured_true: int = 4,
                kinds=("object", "layout"), axes=("object", "layout"), unavailable=None,
                artifact="auto", cost: dict | None = None) -> dict:
    """One row of the report's `validations` list — the shape `arm.py` files, not a summary of it.

    `frozen` and `strict` are independent on purpose: P4-c measured a candidate that passed the rule
    frozen in `core/v02.py` and failed §5.6's sentence read literally, and the two shares are one of
    this module's reasons to exist.
    """
    return {"candidate_id": candidate_id, "report_id": "val_" + candidate_id,
            "skill_name": "pick_up", "passed_frozen_rule": frozen,
            "passed_strict_reading": strict,
            "frozen_reasons": [] if frozen else ["fewer than min_instances ran"],
            "strict_findings": [] if strict else ["§5.6 axes not varied on a run instance"],
            "instances": instances, "instances_ran": ran, "fully_measured_true": measured_true,
            "kinds_covered": list(kinds), "axes_covered": list(axes),
            "axes_unavailable": dict(unavailable or {}),
            "matrix_fingerprint": ("fp-" + candidate_id if fingerprint is None else fingerprint),
            "cost": dict({"wall_s": 1.0, "sim_s": 2.0, "calls": ran, "model_calls": 0,
                          "sandbox_runs": ran}, **(cost or {})),
            "artifact": (f"validation/{candidate_id}.json" if artifact == "auto" else artifact)}


def _refusal(candidate_id: str, *, frozen: bool = False,
             reasons=("the strict reading did not pass",) ) -> dict:
    return {"candidate_id": candidate_id, "skill_name": "pick_up", "reasons": list(reasons),
            "passed_frozen_rule": frozen}


def _admission(candidate_id: str, *, frozen: bool = True, strict_findings=()) -> dict:
    return {"candidate_id": candidate_id, "skill_name": "pick_up",
            "admission": {"passed_frozen_rule": frozen, "strict_findings": list(strict_findings),
                          "level": "procedural", "validation_report_id": "val_" + candidate_id}}


def _reuse(*, offered: bool = True, matched: bool = True, binding: bool = True,
           effect: bool = True, counts: bool = False, named: bool = False,
           reason: str = sk.REUSE_NAMING_REASON) -> dict:
    """One row of `reuse`: the arm's behavioural measurement of a program that was installed."""
    return {"skill_name": "move_object", "skill_id": "skill_a", "library_offered": offered,
            "reuse_named": named, "procedure_matched": matched, "binding_complete": binding,
            "bound_parameters": ({"object": "obj_green_1"} if binding else {}),
            "matched_action_event_ids": (["evt_13", "evt_23"] if matched else []),
            "unbound_parameters": ([] if binding else ["object"]),
            "why_not_matched": "" if matched else "no ordered match in the executed actions",
            "effects": [{"declared": "grasp:{object}", "bound": "grasp:obj_green_1",
                         "unbound_parameters": [], "measured": "true" if effect else "false",
                         "true": effect}],
            "reuse_effect_achieved": effect, "counts_as_reuse": counts,
            "not_measured_reason": ("" if counts else reason)}


def _cost(**extra) -> dict:
    c = {"wall_s": 1.0, "sim_s": 2.0, "calls": 4, "model_calls": 0, "sandbox_runs": 4,
         "validated_candidates": 1, "gap_detection_wall_s": 0.0}
    c.update(extra)
    return c


def _report(*, arm: str = "full", armed: bool = True, gaps=(), candidates=(),
            executable: int | None = None, withheld=(), validations=(), refusals=(),
            admissions=(), reuse=(), cost=None, offer_rounds: int = 0, library_at_start=(),
            offer=None, validation_budget: int = 1, events_filed=None,
            refusals_in_store=None) -> dict:
    block = {"arm": arm, "armed": armed, "module_off": not armed, "pipeline": list(_PIPELINE),
             "proposer": "rule", "proposer_version": "propose_v2", "detector": "gap_detect_v1",
             "library_at_start": list(library_at_start), "offer": dict(offer or {}),
             "offer_rounds": offer_rounds, "offer_read_by": [], "stage_failures": [],
             "gaps": list(gaps), "candidates": list(candidates),
             "candidates_executable": (sum(1 for c in candidates if c["status"] == "sandbox")
                                       if executable is None else executable),
             "not_validated": [_withheld(c) if isinstance(c, str) else c for c in withheld],
             "validations": list(validations), "refusals": list(refusals),
             "admissions": list(admissions), "reuse": list(reuse),
             "reuse_not_measured_reason": sk.REUSE_NAMING_REASON,
             "validation_budget": validation_budget,
             "cost": cost if cost is not None else _cost(
                 validated_candidates=len(validations))}
    if events_filed is not None:
        block["events_filed"] = dict(events_filed)
    if refusals_in_store is not None:
        block["refusals_in_store"] = refusals_in_store
    return block


def _matrix_of(report_row: dict) -> list:
    """The instance rows of the validation artifact belonging to one report row."""
    return [{"instance_kind": kind, "ran": i < report_row["instances_ran"], "unsafe": []}
            for i, kind in enumerate(report_row["kinds_covered"] or ["task"])]


def _episode(episode_id: str, *, decision_rounds: int = 0, skill_events=(), artifacts=AUTO,
             wall_time_s: float = 10.0, claims=AUTO, **report_kw) -> dict:
    block = _report(**report_kw)
    types = ["decision_context"] * decision_rounds + list(skill_events)
    block["events_filed"] = dict(
        ({t: sum(1 for x in types if x == t) for t in sk.OWNED_EVENT_TYPES} if claims is AUTO
         else claims))
    if artifacts is AUTO:
        artifacts = {row["candidate_id"]: (_matrix_of(row) if row["artifact"] else None)
                     for row in block["validations"]}
    return {"id": episode_id, "block": block, "types": types, "artifacts": dict(artifacts),
            "wall_time_s": wall_time_s}


def _instance(kind: str = "object", *, ran: bool = True, unsafe=()) -> dict:
    return {"instance_kind": kind, "ran": ran, "unsafe": list(unsafe),
            "verdicts": {"grasp:obj_green_1": True}}


def _batch(tmp_path, name: str, *episodes, arm: str = "full",
           set_name: str = "p4e_contract") -> str:
    """One run directory, laid out the way `evaluation/run.py` lays one out."""
    root = tmp_path / name
    (root / "episodes").mkdir(parents=True, exist_ok=True)
    manifest = {"run_id": name, "set": set_name, "planner": "rule", "modes": ["B"],
                "repeats": len(episodes), "perception": "privileged", "offline": True,
                "skill_acquisition": {"arms": [arm, "wo_skill_acquisition"],
                                      "pipeline": list(_PIPELINE), "proposer": "rule",
                                      "validation_budget": 1}}
    with open(root / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    for ep in episodes:
        directory = root / "episodes" / ep["id"]
        (directory / "validation").mkdir(parents=True, exist_ok=True)
        with open(directory / "events.jsonl", "w", encoding="utf-8") as f:
            for i, type_ in enumerate(ep["types"]):
                f.write(json.dumps({"sequence": i + 1, "type": type_, "schema_version": SCHEMA_VERSION,
                                    "payload": {"round_index": i + 1}}) + "\n")
        summary = {"episode_id": ep["id"], "set": set_name, "case_id": ep["id"].rsplit(".", 1)[0],
                   "mode": "B", "repeat": 0, "wall_time_s": ep["wall_time_s"],
                   "result": {"completed": True, "success": True},
                   "skill_acquisition": ep["block"]}
        with open(directory / "episode_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False)
        for candidate_id, instances in ep["artifacts"].items():
            if instances is None:
                continue  # the artifact is missing on purpose: the row must lose its denominator
            with open(directory / "validation" / f"{candidate_id}.json", "w",
                      encoding="utf-8") as f:
                json.dump({"candidate_id": candidate_id, "report": {"instances": instances}}, f)
    return str(root)


# ---------------------------------------------------------------- the 口径 table ----
def test_every_id_the_scorer_counts_is_defined_before_it_is_counted():
    tree = ast.parse(open(os.path.abspath(sk.__file__), encoding="utf-8").read())
    counted = {sub.value for node in tree.body if isinstance(node, ast.FunctionDef)
               for sub in ast.walk(node)
               if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
               and ID_RE.match(sub.value)}
    assert counted, "the scan found no metric ids: the pattern, not the module, is broken"
    assert not counted - set(sk.METRIC_DEFINITIONS), \
        f"counted without a definition: {sorted(counted - set(sk.METRIC_DEFINITIONS))}"
    assert not set(sk.METRIC_DEFINITIONS) - counted, \
        f"defined but never counted: {sorted(set(sk.METRIC_DEFINITIONS) - counted)}"
    with pytest.raises(KeyError):
        sk._count({}, "S9.nobody_wrote_this_down", 1, 1)


def test_the_arithmetic_lives_in_one_function_and_nowhere_else():
    """A second counting site would double count; a copied *sentence* is the one exception.

    `measure_roots` names `S4.counted_as_reuse` where it replaces that row's reason with the sentence
    the arm filed on its own reuse row — a copy of a string, not a second number. Anything else outside
    `score_rounds` means two places can disagree about one id.
    """
    tree = ast.parse(open(os.path.abspath(sk.__file__), encoding="utf-8").read())
    functions = {node.name: {sub.value for sub in ast.walk(node)
                             if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                             and ID_RE.match(sub.value)}
                 for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert functions["score_rounds"] == set(sk.METRIC_DEFINITIONS), \
        "an id with no counting site prints `not measured` forever"
    elsewhere = {name: ids for name, ids in functions.items() if name != "score_rounds" and ids}
    assert elsewhere == {"measure_roots": {"S4.counted_as_reuse"}}, elsewhere


def test_every_definition_is_six_cells_and_says_what_it_refuses_to_be_read_as():
    assert sk.METRIC_DEFINITIONS, "the 口径 is the deliverable; an empty table guards nothing"
    for metric, definition in sk.METRIC_DEFINITIONS.items():
        assert set(definition) - set(DEFINITION_KEYS) <= {"id_note"}, \
            f"{metric}: unexpected cells {sorted(set(definition) - set(DEFINITION_KEYS))}"
        for key in DEFINITION_KEYS:
            cell = definition[key]
            assert isinstance(cell, str) and cell.strip(), f"{metric}.{key} is empty"
            assert "*" not in cell, f"{metric}.{key} carries a markdown emphasis marker"
            assert not POINTER.match(cell), f"{metric}.{key} points somewhere else: {cell!r}"
            assert not BARE_RATIO.match(cell), f"{metric}.{key} is a bare ratio: {cell!r}"
        assert definition["question"].endswith("?"), metric
        assert definition["forbidden"].strip() != definition["truth"].strip(), \
            f"{metric}: the cell that protects the row cannot be the row's own content"


def test_the_five_spec_rows_and_the_four_cost_rows_cover_every_id_exactly_once():
    labels = [label for label, _ in sk.SPEC_ROWS]
    assert labels[:5] == list(SPEC_ROW_NAMES), labels[:5]
    assert sorted(labels[5:]) == sorted(COST_ROW_NAMES), labels[5:]
    seen: list[str] = []
    for label, ids in sk.SPEC_ROWS:
        for metric in ids:
            assert metric in sk.METRIC_DEFINITIONS, f"{label} claims {metric}, which is undefined"
            seen.append(metric)
    assert len(seen) == len(set(seen)), \
        f"an id answered two §11 rows: {[m for m in seen if seen.count(m) > 1]}"
    assert set(seen) == set(sk.METRIC_DEFINITIONS), \
        f"no row claims: {sorted(set(sk.METRIC_DEFINITIONS) - set(seen))}"
    for label, ids in sk.SPEC_ROWS:
        for metric in ids:
            assert sk._rate(metric, 1, 2)["spec_row"] == label


def _spec_bullets(heading: str) -> list[str]:
    """§11's own bullet list under one of its group headings, read off the SPEC file.

    The row names in this file are a copy, and a copy can be edited to match a mistake. Reading the
    SPEC's words here means the claim "these ids 铺满 §11's rows" is checked against the thing it is
    about rather than against a restatement of it.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(sk.__file__)), os.pardir, os.pardir,
                        "Continuous-Decision-Embodied-Agent-SPEC-v0.2.md")
    lines = open(path, encoding="utf-8").read().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"### {heading}")
    out: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("##") or line.startswith("###"):
            break
        if line.startswith("- "):
            out.append(line[2:].strip())
    return out


def test_every_row_11_asks_for_is_either_an_id_here_or_declared_to_be_measured_elsewhere():
    skill_rows = _spec_bullets("Skill Acquisition")
    cost_rows = _spec_bullets("Cost")
    assert skill_rows == list(sk.SKILL_GROUP_ROWS), "the five rows this 口径 is written against changed"
    assert cost_rows == list(sk.COST_GROUP_ROWS)
    labels = {label for label, _ in sk.SPEC_ROWS}
    assert labels - set(cost_rows) == set(sk.SKILL_GROUP_ROWS)
    for name in cost_rows:
        if name in labels:
            continue
        # a row with no id is allowed only with a sentence saying who measures it, because the
        # alternative is a group that reads as four rows when §11 wrote five
        assert name in sk.ROWS_WITHOUT_AN_ID, f"§11's Cost row {name!r} has no id and no reason"
        assert len(sk.ROWS_WITHOUT_AN_ID[name].split()) > 15
    assert set(sk.ROWS_WITHOUT_AN_ID) <= set(cost_rows), "a declared row §11 never asked for"


def test_the_declared_row_travels_with_the_definitions_the_artifact_and_the_printed_table(tmp_path):
    measured = sk.measure_roots([_batch(tmp_path, "row_declared", _episode("c1.B.r0"))])
    assert measured["rows_without_an_id"] == sk.ROWS_WITHOUT_AN_ID
    assert sk._definitions_payload()["rows_without_an_id"] == sk.ROWS_WITHOUT_AN_ID
    text = sk.render_definitions() + "\n" + sk.render_markdown(measured)
    for name in sk.ROWS_WITHOUT_AN_ID:
        assert f"`{name}`" in text, f"{name} is declared in a constant and printed nowhere"
    # the declaration's own promise: the row is answered by the episode ledger, so the two names it
    # cites have to be counters that file actually reads
    assert {"prompt_tokens", "completion_tokens"} <= set(run.PROLOGUE_COUNTERS)
    assert "prompt_tokens_total" in inspect.getsource(report) and "completion_tokens_total" in \
        inspect.getsource(report), "the Costs table this 口径 points at no longer exists"


def test_the_two_families_that_are_not_proportions_are_named_and_do_not_overlap():
    definitions = set(sk.METRIC_DEFINITIONS)
    assert sk.RATIO_METRICS < definitions, "a ratio id nobody defines"
    assert sk.PER_BATCH_METRICS < definitions
    assert not (sk.RATIO_METRICS & sk.PER_BATCH_METRICS), \
        "one row cannot both refuse pooling and be published per unit"
    assert sk.PER_BATCH_METRICS == {"S3.distinct_matrices", "S3.transfer_claim_repeats"}, \
        "the per-batch family is exactly the rows whose numerator is defined against the batch"
    assert {"S1.candidates_per_gap", "S4.rounds_offering_a_program"} <= sk.RATIO_METRICS
    cost_ids = {m for label, ids in sk.SPEC_ROWS if label in COST_ROW_NAMES for m in ids}
    assert cost_ids <= sk.RATIO_METRICS, "a Cost row that got a Wilson interval is a fake precision"
    assert sk.RATIO_METRICS - cost_ids == {"S1.candidates_per_gap",
                                           "S4.rounds_offering_a_program"}


def test_the_row_envelope_is_the_same_object_shape_mem_1_and_lh_2_publish():
    """P5's report reads §11's three behaviour groups with one reader, so the keys cannot fork.

    The one intended difference is asserted beside it: this module's Cost rows count seconds, and an
    int cast would report 2.889 s per sandbox run as 2.
    """
    mine = sk._rate("S1.gap_episodes", 1, 2)
    assert mine["interval_kind"] == "proportion"
    for other in (em._rate(next(iter(em.METRIC_DEFINITIONS)), 1, 2),
                  lh._rate(next(iter(lh.METRIC_DEFINITIONS)), 1, 2)):
        assert set(other) == set(mine), sorted(set(other) ^ set(mine))
    seconds = sk._rate("C2.simulator_seconds_per_sandbox_run", 40.452, 14)
    assert seconds["numerator"] == 40.452 and seconds["denominator"] == 14
    assert seconds["interval_kind"] == "count_per_unit" and seconds["wilson_95"] is None
    assert sk._rate("S1.gap_episodes", 1, 2)["interval_kind"] == "proportion"


def test_a_denominator_of_zero_is_never_a_rate_of_zero():
    for metric in sorted(sk.METRIC_DEFINITIONS):
        row = sk._rate(metric, 0, 0)
        assert row["measured"] is False and row["value"] is None and row["wilson_95"] is None
        assert row["not_measured_reason"] == sk._NOT_MEASURED[metric]
        assert row["denominator_too_small_for_a_rate"] is False
    assert set(sk._NOT_MEASURED) == set(sk.METRIC_DEFINITIONS), \
        "every id needs its own sentence for the case with no denominator"
    zero = sk._rate("S1.gap_episodes", 0, 6)
    assert zero["measured"] is True and zero["value"] == 0.0
    assert zero["not_measured_reason"] is None, "a clean zero and an absent row are different facts"


def test_a_proportion_refuses_a_numerator_larger_than_its_denominator():
    with pytest.raises(AssertionError):
        sk._rate("S2.frozen_rule_share", 3, 2)
    assert sk._rate("S1.candidates_per_gap", 3, 1)["value"] == 3.0
    assert sk._rate("C2.sandbox_runs_per_candidate", 14, 4)["value"] == 3.5
    assert sk._rate("C2.generation_share_of_episode_wall", 5.854, 15.5)["value"] == 0.3777
    assert sk._rate("S3.axes_at_least_two", 1, 2)["denominator_too_small_for_a_rate"] is True


def test_every_row_carries_its_own_arithmetic_as_printed():
    metric = "S2.measured_true_per_ran_instance"
    row, definition = sk._rate(metric, 8, 14), sk.METRIC_DEFINITIONS[metric]
    assert row["as_computed"] == f"{definition['numerator']} / {definition['denominator']}"
    assert row["question"] == definition["question"] and row["unit"] == definition["unit"]
    assert (row["numerator"], row["denominator"], row["value"]) == (8, 14, round(8 / 14, 4))
    assert row["wilson_95"] == list(wilson_interval(8, 14))


# --------------------------------------------------------- reading a run directory ----
def test_a_directory_that_is_not_a_batch_is_refused_by_name(tmp_path):
    with pytest.raises(ValueError, match="not a run directory"):
        sk.read_batch(str(tmp_path / "typo"))
    empty = tmp_path / "empty"
    (empty / "episodes").mkdir(parents=True)
    with open(empty / "manifest.json", "w", encoding="utf-8") as f:
        json.dump({"run_id": "empty"}, f)
    with pytest.raises(ValueError, match="no episodes"):
        sk.read_batch(str(empty))


def test_an_episode_with_no_skill_acquisition_block_is_a_different_arm_and_not_scoreable(tmp_path):
    """§5.1: absence is a name. "the loop ran with no library installed" is not "the module was off".

    Averaging the two would let a batch that never wired `--skill-memory` lower §11's candidate
    generation rate with a clean zero.
    """
    root = _batch(tmp_path, "one", _episode("c.B.r0"))
    path = os.path.join(root, "episodes", "c.B.r0", "episode_summary.json")
    with open(path, encoding="utf-8") as f:
        summary = json.load(f)
    del summary["skill_acquisition"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f)
    with pytest.raises(ValueError, match="skill_acquisition"):
        sk.read_batch(root)


def test_the_manifest_arm_and_the_summary_clock_travel_with_the_batch(tmp_path):
    batch = sk.read_batch(_batch(tmp_path, "manifested", _episode("c.B.r0", wall_time_s=4.25)))
    assert batch["run_id"] == "manifested" and batch["set"] == "p4e_contract"
    assert batch["manifest_acquisition"]["pipeline"] == list(_PIPELINE)
    ep = batch["episodes"][0]
    assert ep["wall_time_s"] == 4.25 and ep["repeat"] == 0
    assert ep["result"]["completed"] is True


# --------------------------------------------------------------- S1: generation ----
def test_the_gap_denominator_is_the_episodes_that_ran_the_detector(tmp_path):
    """Pooling the arms would report the module as roughly half as good as it is.

    The disarmed episodes below carry a gap row each — impossible for the real arm, which is exactly
    why it is in the fixture: a counting site may not lean on the arm's honesty to keep its denominator.
    """
    measured = sk.measure_roots([_batch(
        tmp_path, "arms",
        _episode("a.B.r0", gaps=[_gap()]), _episode("a.B.r1"),
        _episode("a.B.r2", arm="wo_skill_acquisition", armed=False, gaps=[_gap()]),
        _episode("a.B.r3", arm="wo_skill_acquisition", armed=False, gaps=[_gap()]))])
    rows = measured["metrics"]
    assert (rows["S1.gap_episodes"]["numerator"], rows["S1.gap_episodes"]["denominator"]) == (1, 2)
    assert rows["S1.candidates_per_gap"]["denominator"] == 1, \
        "the disarmed gaps belong to no row's denominator"


def test_a_proposal_that_cannot_run_and_one_the_budget_declined_are_two_rows(tmp_path):
    """Three zeros with opposite fixes, kept apart (decision 5, and §5.1)."""
    measured = sk.measure_roots([_batch(
        tmp_path, "declined",
        _episode("b.B.r0", gaps=[_gap()],
                 candidates=[_candidate("c1"), _candidate("c2"), _candidate("c3", status="rejected")],
                 withheld=["c2"], validations=[_validation("c1", ran=3, measured_true=3)],
                 cost=_cost(wall_s=1.0, sim_s=2.0, calls=3, model_calls=0, sandbox_runs=3,
                            validated_candidates=1)))])
    rows = measured["metrics"]
    refused = rows["S1.not_executable"]
    declined = rows["S1.budget_left_unvalidated"]
    assert (refused["numerator"], refused["denominator"]) == (1, 3), "one proposal was not a program"
    assert (declined["numerator"], declined["denominator"]) == (1, 2), \
        "the executable population is two, and the withheld one is drawn from it, not beside it"
    assert refused["as_computed"] != declined["as_computed"]
    assert rows["S2.frozen_rule_share"]["denominator"] == 1, \
        "a candidate the budget declined never reaches a gate, so it cannot dilute S2"


def test_the_withheld_share_is_counted_over_the_candidates_that_could_have_run(tmp_path):
    """A budget of 0 declines *everything*: the row has to be able to say 1.0.

    A denominator built as `executable + withheld` adds the numerator to its own divisor and would
    read 0.5 on a batch that bought none of its two runnable candidates — a rate that moves when the
    shape of the question moves rather than when the batch does.
    """
    measured = sk.measure_roots([_batch(
        tmp_path, "zero_budget",
        _episode("b.B.r0", gaps=[_gap()], candidates=[_candidate("c1"), _candidate("c2")],
                 withheld=["c1", "c2"], validations=[], validation_budget=0))])
    rows = measured["metrics"]
    assert (rows["S1.budget_left_unvalidated"]["numerator"],
            rows["S1.budget_left_unvalidated"]["denominator"],
            rows["S1.budget_left_unvalidated"]["value"]) == (2, 2, 1.0)
    assert rows["S2.frozen_rule_share"]["measured"] is False
    assert rows["S1.not_executable"]["value"] == 0.0, \
        "both candidates were programs; the batch just declined to buy the sandboxes"


def test_a_gap_the_library_already_covered_stays_in_the_proposers_denominator(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "covered",
        _episode("b.B.r0", gaps=[_gap("gap_1", covered=["pick_up"]), _gap("gap_2")],
                 candidates=[_candidate("c1", gap_id="gap_2")]))])
    rows = measured["metrics"]
    assert (rows["S1.covered_by_the_library"]["numerator"],
            rows["S1.covered_by_the_library"]["denominator"]) == (1, 2)
    assert rows["S1.candidates_per_gap"]["denominator"] == 2, \
        "the detector reported the covered gap; dropping it hides the shape of the library"
    assert rows["S1.candidates_per_gap"]["numerator"] == 1, "proposing is gated on `uncovered`"


def test_a_count_per_gap_above_one_is_a_number_and_not_an_error(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "many",
        _episode("b.B.r0", gaps=[_gap()], candidates=[_candidate(f"c{i}") for i in range(3)]))])
    row = measured["metrics"]["S1.candidates_per_gap"]
    assert (row["numerator"], row["denominator"], row["value"]) == (3, 1, 3.0)
    assert row["wilson_95"] is None and row["interval_kind"] == "count_per_unit"


# ------------------------------------------------- S2/S3: the two gate readings ----
def test_the_frozen_rule_and_the_strict_sentence_are_two_rows_and_never_one(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "gates",
        _episode("b.B.r0", gaps=[_gap()], candidates=[_candidate("c1"), _candidate("c2")],
                 validations=[_validation("c1"),
                              _validation("c2", frozen=True, strict=False,
                                          kinds=("object", "task"), axes=("object",))]))])
    rows = measured["metrics"]
    assert (rows["S2.frozen_rule_share"]["numerator"],
            rows["S2.frozen_rule_share"]["denominator"]) == (2, 2)
    assert (rows["S2.strict_reading_share"]["numerator"],
            rows["S2.strict_reading_share"]["denominator"]) == (1, 2)
    assert rows["S2.frozen_rule_share"]["value"] != rows["S2.strict_reading_share"]["value"], \
        "the difference between the two readings is the finding; merging it deletes the finding"
    assert "frozen" in rows["S2.frozen_rule_share"]["as_computed"]
    assert "strict" in rows["S2.strict_reading_share"]["as_computed"]
    assert "gate_readings" in measured["notes"] and "admission_gate" in measured["notes"]


def test_an_instance_that_never_ran_is_neither_a_pass_nor_a_failure(tmp_path):
    """§12.4: 没跑的不能计入闸门."""
    measured = sk.measure_roots([_batch(
        tmp_path, "unrun",
        _episode("b.B.r0", gaps=[_gap()], candidates=[_candidate("c1")],
                 validations=[_validation("c1", instances=4, ran=3, measured_true=3)],
                 artifacts={"c1": [_instance("object"), _instance("layout"), _instance("parameters"),
                                   _instance("repeat", ran=False, unsafe=["contact"])]}))])
    rows = measured["metrics"]
    assert (rows["S2.instances_ran"]["numerator"], rows["S2.instances_ran"]["denominator"]) == (3, 4)
    assert rows["S2.measured_true_per_ran_instance"]["value"] == 1.0
    assert (rows["S5.unsafe_findings"]["numerator"],
            rows["S5.unsafe_findings"]["denominator"]) == (0, 1), \
        "an unrun instance cannot carry an unsafe verdict into a report's count"


def test_two_of_three_axes_is_a_frozen_pass_and_a_stop_at_two_at_the_same_time(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "axes",
        _episode("b.B.r0", validations=[_validation(
            "c1", frozen=True, strict=False, kinds=("object", "layout"), axes=("object", "layout"),
            unavailable={"parameters": ["pick_up declares no target parameter"]})]))])
    rows = measured["metrics"]
    assert (rows["S2.all_three_axes"]["numerator"], rows["S2.all_three_axes"]["denominator"]) == (0, 1)
    assert (rows["S3.axes_at_least_two"]["numerator"],
            rows["S3.axes_at_least_two"]["denominator"]) == (1, 1)
    assert (rows["S3.axes_left_unvaried"]["numerator"],
            rows["S3.axes_left_unvaried"]["denominator"]) == (1, 1)
    assert (rows["S3.axes_named_unavailable"]["numerator"],
            rows["S3.axes_named_unavailable"]["denominator"]) == (1, 1)
    assert rows["S3.axes_at_least_two"]["value"] == 1.0, \
        "the frozen rule is satisfied by exactly the pair that leaves §5.6's parameter axis untested"
    assert not any(r["numerator"] == 2 and r["denominator"] == 1 for r in rows.values()), \
        "adding `left unvaried` to `named unavailable` invents a report"


def test_a_matrix_that_varied_nothing_is_not_counted_as_a_partial_cover(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "nocover",
        _episode("b.B.r0", validations=[_validation("c1", axes=(), kinds=(), frozen=False)]))])
    rows = measured["metrics"]
    assert (rows["S3.axes_left_unvaried"]["numerator"],
            rows["S3.axes_left_unvaried"]["denominator"]) == (0, 1), \
        "the complement is defined among reports that varied something"
    assert rows["S2.all_three_axes"]["numerator"] == 0
    assert rows["S3.axes_at_least_two"]["numerator"] == 0
    assert "varied anything" in sk.METRIC_DEFINITIONS["S3.axes_left_unvaried"]["truth"]


def test_a_missing_axis_somebody_named_is_the_honest_row(tmp_path):
    two = _episode("b.B.r0", validations=[
        _validation("c1", axes=("object",), unavailable={"parameters": ["no value to bind"]}),
        _validation("c2", axes=("object",), unavailable={})])
    measured = sk.measure_roots([_batch(tmp_path, "named", two)])
    rows = measured["metrics"]
    assert (rows["S3.axes_left_unvaried"]["numerator"],
            rows["S3.axes_left_unvaried"]["denominator"]) == (2, 2)
    assert (rows["S3.axes_named_unavailable"]["numerator"],
            rows["S3.axes_named_unavailable"]["denominator"]) == (1, 2)
    assert sk.METRIC_DEFINITIONS["S3.axes_named_unavailable"]["forbidden"].startswith(
        "reading a high value as bad news")


def test_a_fingerprint_is_unique_within_a_batch_and_says_so(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "fps",
        _episode("b.B.r0", validations=[_validation("c1", fingerprint="fp-a"),
                                         _validation("c2", fingerprint="fp-a"),
                                         _validation("c3", fingerprint="fp-b"),
                                         _validation("c4", fingerprint="")]))])
    rows = measured["metrics"]
    assert (rows["S3.distinct_matrices"]["numerator"],
            rows["S3.distinct_matrices"]["denominator"]) == (2, 4)
    assert (rows["S3.transfer_claim_repeats"]["numerator"],
            rows["S3.transfer_claim_repeats"]["denominator"]) == (1, 4)
    assert rows["S3.distinct_matrices"]["measured"] is True, "one batch is a batch"


# ------------------------------------------------------------------- S4: the two arms ----
def test_the_reuse_rows_are_counted_into_one_id_per_arm(tmp_path):
    """Decision 1: §9's row is a claim about the offer, so a sum of the two arms has no arm in it."""
    measured = sk.measure_roots([_batch(
        tmp_path, "twoarms",
        _episode("c.B.r0", library_at_start=["move_object"],
                 offer={"offered": ["move_object"]}, reuse=[_reuse(), _reuse(effect=False,
                                                                            matched=False)]),
        _episode("c.B.r1", arm="wo_skill_acquisition", armed=False,
                 library_at_start=["move_object"], reuse=[_reuse(offered=False),
                                                          _reuse(offered=False)]))])
    rows = measured["metrics"]
    assert (rows["S4.procedure_recurred"]["numerator"],
            rows["S4.procedure_recurred"]["denominator"]) == (1, 2)
    assert (rows["S4.recurrence_with_effect_measured"]["numerator"],
            rows["S4.recurrence_with_effect_measured"]["denominator"]) == (1, 2)
    assert (rows["S4.withheld_and_recurred"]["numerator"],
            rows["S4.withheld_and_recurred"]["denominator"]) == (2, 2)
    for row in rows.values():
        assert not (row["numerator"] == 3 and row["denominator"] == 4), \
            "the two arms were pooled into one reuse figure"
    assert not (rows["S4.procedure_recurred"]["numerator"]
                + rows["S4.withheld_and_recurred"]["numerator"]
                == rows["S4.recurrence_with_effect_measured"]["numerator"])


def test_the_withheld_row_needs_a_program_installed_when_the_episode_began(tmp_path):
    """The control of §9's contrast is a warm library handed to the arm that does not read it.

    A `wo_skill_acquisition` episode with nothing installed has no denominator here, and the reason
    says so — an empty control row is not a 0.0 finding about the offer.
    """
    cold = sk.measure_roots([_batch(
        tmp_path, "cold", _episode("c.B.r0", arm="wo_skill_acquisition", armed=False))])
    row = cold["metrics"]["S4.withheld_and_recurred"]
    assert row["measured"] is False and row["denominator"] == 0
    assert "warm library" in row["not_measured_reason"]
    warm = sk.measure_roots([_batch(
        tmp_path, "warm", _episode("c.B.r0", arm="wo_skill_acquisition", armed=False,
                                   library_at_start=["move_object"],
                                   reuse=[_reuse(offered=False)]))])
    held = warm["metrics"]["S4.withheld_and_recurred"]
    assert (held["numerator"], held["denominator"]) == (1, 1)
    assert held["measured"] is True


def test_a_cold_library_contributes_no_rounds_to_the_offer_denominator(tmp_path):
    """Nothing could have been offered on those rounds, so they belong to no denominator."""
    measured = sk.measure_roots([_batch(
        tmp_path, "rounds",
        _episode("c.B.r0", decision_rounds=13, library_at_start=[]),
        _episode("c.B.r1", decision_rounds=7, library_at_start=["move_object"],
                 offer={"offered": ["move_object"]}, offer_rounds=7))])
    row = measured["metrics"]["S4.rounds_offering_a_program"]
    assert (row["numerator"], row["denominator"], row["value"]) == (7, 7, 1.0)
    assert row["interval_kind"] == "count_per_unit"
    per_episode = {e["episode_id"]: e for b in measured["batches"] for e in b["per_episode"]}
    assert per_episode["c.B.r0"]["decision_rounds"] == 13, "the raw counts stay visible"
    assert per_episode["c.B.r1"]["library_at_start"] == ["move_object"]


def test_an_arm_that_offered_on_only_some_rounds_moves_the_row_down(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "partial",
        _episode("c.B.r0", decision_rounds=8, library_at_start=["move_object"], offer_rounds=2))])
    row = measured["metrics"]["S4.rounds_offering_a_program"]
    assert (row["numerator"], row["denominator"], row["value"]) == (2, 8, 0.25)


def test_the_literal_reading_of_the_reuse_row_is_not_measured_and_says_whose_sentence_it_is(tmp_path):
    """The definition promises the arm's own sentence, copied rather than re-derived."""
    arm_reason = ("a decision cannot name an acquired program: core/contracts.py:SkillName is a "
                  "frozen Literal of the shipped primitives")
    measured = sk.measure_roots([_batch(
        tmp_path, "withheld",
        _episode("c.B.r0", library_at_start=["move_object"],
                 reuse=[_reuse(counts=False, named=True, reason=arm_reason)]))])
    row = measured["metrics"]["S4.counted_as_reuse"]
    assert row["measured"] is False and row["denominator"] == 0
    assert row["not_measured_reason"] == arm_reason, "the artifact must carry the arm's words"
    assert measured["batches"][0]["reuse_naming_reason"] == arm_reason
    bare = sk.measure_roots([_batch(tmp_path, "bare", _episode("c.B.r0"))])
    assert bare["metrics"]["S4.counted_as_reuse"]["not_measured_reason"] == sk.REUSE_NAMING_REASON, \
        "this module's own sentence is only the fallback for a batch with no reuse row to copy"


def test_the_self_report_is_published_and_counts_toward_nothing(tmp_path):
    """P3-d measured `memory_followed` true on two arms whose trajectories were identical.

    The same shape here is `reuse_named`: a batch whose policy cannot name a program would be lying to
    set it, so the field is shown per episode and is a numerator nowhere.
    """
    measured = sk.measure_roots([_batch(
        tmp_path, "selfreport",
        _episode("c.B.r0", library_at_start=["move_object"],
                 reuse=[_reuse(named=True), _reuse(named=True, effect=False, matched=False)]))])
    episode = measured["batches"][0]["per_episode"][0]
    assert (episode["reuse_named"], episode["counts_as_reuse"]) == (2, 0)
    assert episode["recurred"] == 1 and episode["effect_measured"] == 1
    assert measured["metrics"]["S4.counted_as_reuse"]["numerator"] == 0
    assert "self_report" in measured["notes"]["reuse_row"]


# --------------------------------------------------------- S5: unsafe / invalid ----
def test_the_refusals_that_count_are_the_ones_the_episode_filed(tmp_path):
    """`refusals_in_store` is the live store's running total, so summing it double counts.

    Two episodes that each refused one candidate leave the store at 1 and then 2. The numerator of
    §11's row is 2; the store column is an audit of it, not a second measurement.
    """
    measured = sk.measure_roots([_batch(
        tmp_path, "refusals",
        _episode("d.B.r0", gaps=[_gap()], candidates=[_candidate("c1")],
                 validations=[_validation("c1")], refusals=[_refusal("c1")], refusals_in_store=1),
        _episode("d.B.r1", gaps=[_gap("gap_2")], candidates=[_candidate("c2", gap_id="gap_2")],
                 validations=[_validation("c2")], refusals=[_refusal("c2")], refusals_in_store=2))])
    row = measured["metrics"]["S5.refused_after_validation"]
    assert (row["numerator"], row["denominator"]) == (2, 2)
    in_store = [e["refusals_in_store"] for b in measured["batches"] for e in b["per_episode"]]
    assert in_store == [1, 2] and sum(in_store) == 3
    assert row["numerator"] != sum(in_store)
    assert "running total" in measured["notes"]["refusals_in_store"]


def test_a_refusal_stands_whether_or_not_it_names_the_gate_that_stopped_it(tmp_path):
    """A refusal row that also says `passed_frozen_rule` true is the strict reading stopping a program
    the frozen gate would have admitted — the only place §12.2's frozen rule gets priced."""
    measured = sk.measure_roots([_batch(
        tmp_path, "priced",
        _episode("e.B.r0", gaps=[_gap()], candidates=[_candidate("c1")],
                 validations=[_validation("c1", frozen=True, strict=False)],
                 refusals=[_refusal("c1", frozen=True)]))])
    row = measured["metrics"]["S5.refused_after_validation"]
    assert (row["numerator"], row["denominator"]) == (1, 1)
    episode = measured["batches"][0]["per_episode"][0]
    assert (episode["frozen_passes"], episode["strict_passes"]) == (1, 0)
    assert "conjunction" in measured["notes"]["admission_gate"]


def test_a_missing_validation_artifact_leaves_the_unsafe_denominator_rather_than_clean(tmp_path):
    """`0` with a denominator means "looked, nothing"; an artifact that is not there means nobody looked.

    This is the one row whose evidence lives outside the episode summary, so it is the one place the
    distinction can be lost by a reader that trusted the report's instance counts.
    """
    half = sk.measure_roots([_batch(
        tmp_path, "half",
        _episode("f.B.r0", validations=[_validation("c1"), _validation("c2", artifact=None)]))])
    row = half["metrics"]["S5.unsafe_findings"]
    assert (row["numerator"], row["denominator"], row["measured"]) == (0, 1, True)
    blind = sk.measure_roots([_batch(
        tmp_path, "blind",
        _episode("f.B.r1", validations=[_validation("c3", artifact=None)], artifacts={}))])
    assert blind["metrics"]["S5.unsafe_findings"]["measured"] is False
    found = sk.measure_roots([_batch(
        tmp_path, "unsafe",
        _episode("f.B.r2", validations=[_validation("c4")],
                 artifacts={"c4": [_instance(unsafe=["contact with the fixture"])]}))])
    assert (found["metrics"]["S5.unsafe_findings"]["numerator"],
            found["metrics"]["S5.unsafe_findings"]["denominator"]) == (1, 1)


def test_a_zero_denominator_on_the_admission_row_is_not_a_pass(tmp_path):
    empty = sk.measure_roots([_batch(tmp_path, "noadm", _episode("g.B.r0"))])
    row = empty["metrics"]["S5.invalid_skill_admitted"]
    assert row["measured"] is False and row["value"] is None
    assert "nothing was admitted" in row["not_measured_reason"]
    clean = sk.measure_roots([_batch(tmp_path, "clean",
                                     _episode("g.B.r0", admissions=[_admission("c1")]))])
    assert (clean["metrics"]["S5.invalid_skill_admitted"]["numerator"],
            clean["metrics"]["S5.invalid_skill_admitted"]["denominator"]) == (0, 1)
    against = sk.measure_roots([_batch(
        tmp_path, "against",
        _episode("g.B.r0", admissions=[_admission("c1"), _admission("c2", frozen=False),
                                        _admission("c3", strict_findings=["axis never varied"])]))])
    assert (against["metrics"]["S5.invalid_skill_admitted"]["numerator"],
            against["metrics"]["S5.invalid_skill_admitted"]["denominator"]) == (2, 3)


def test_the_librarys_door_is_the_conjunction_of_both_readings():
    """`memory.admission_findings` refuses on the frozen rule *and* on the strict reading.

    So `S2.frozen_rule_share` is not an admission rate, and the row that asks "did anything get in
    against its gate" has to read the provenance the admission itself carries.
    """
    assert sk._admitted_against_its_gate(_admission("c1")) is False
    assert sk._admitted_against_its_gate(_admission("c1", frozen=False)) is True
    assert sk._admitted_against_its_gate(_admission("c1", strict_findings=["x"])) is True
    assert sk._admitted_against_its_gate({"admission": {}}) is False, \
        "an admission that says nothing about the gate is not evidence against it"
    numerator = sk.METRIC_DEFINITIONS["S5.invalid_skill_admitted"]["numerator"]
    assert "`passed_frozen_rule` false" in numerator and "`strict_findings`" in numerator
    assert " or " in numerator, "the row counts against the conjunction, not one reading"


# ----------------------------------------------------------------------- pooling ----
def test_pooling_sums_integers_and_not_the_means_of_two_fractions(tmp_path):
    small = _episode("h.B.r0", gaps=[_gap()], candidates=[_candidate(f"c{i}") for i in range(4)],
                     validations=[_validation(f"c{i}") for i in range(4)],
                     refusals=[_refusal(f"c{i}") for i in range(2)])
    large = _episode("h.B.r1", gaps=[_gap()], candidates=[_candidate(f"d{i}") for i in range(10)],
                     validations=[_validation(f"d{i}") for i in range(10)],
                     refusals=[_refusal(f"d{i}") for i in range(6)])
    first = sk.measure_roots([_batch(tmp_path, "pa", small)])
    second = sk.measure_roots([_batch(tmp_path, "pb", large)])
    assert (first["metrics"]["S5.refused_after_validation"]["value"],
            second["metrics"]["S5.refused_after_validation"]["value"]) == (0.5, 0.6)
    pooled = sk.measure_roots([_batch(tmp_path, "pa", small), _batch(tmp_path, "pb", large)])
    row = pooled["metrics"]["S5.refused_after_validation"]
    assert (row["numerator"], row["denominator"]) == (8, 14)
    assert row["value"] == round(8 / 14, 4)
    assert row["value"] != (0.5 + 0.6) / 2
    assert row["wilson_95"] == list(wilson_interval(8, 14))
    assert pooled["roots"] == [os.path.abspath(str(tmp_path / "pa")),
                               os.path.abspath(str(tmp_path / "pb"))]


def test_the_two_rows_that_refuse_pooling_are_published_per_batch_instead(tmp_path):
    def doubled(index: int) -> dict:
        return _episode(f"k.B.r{index}",
                        validations=[_validation(f"c{i}", fingerprint="same") for i in range(2)])

    pooled = sk.measure_roots([_batch(tmp_path, "ka", doubled(0)), _batch(tmp_path, "kb",
                                                                          doubled(1))])
    for metric in sorted(sk.PER_BATCH_METRICS):
        row = pooled["metrics"][metric]
        assert row["measured"] is False
        assert (row["numerator"], row["denominator"]) == (0, 0), "a refused pool keeps no residue"
        assert "unique within one batch" in row["not_measured_reason"]
        assert "batches[].metrics" in row["not_measured_reason"]
    for batch in pooled["batches"]:
        assert (batch["metrics"]["S3.distinct_matrices"]["numerator"],
                batch["metrics"]["S3.distinct_matrices"]["denominator"]) == (1, 2)
        assert (batch["metrics"]["S3.transfer_claim_repeats"]["numerator"],
                batch["metrics"]["S3.transfer_claim_repeats"]["denominator"]) == (1, 2)
        for metric in sorted(sk.PER_BATCH_METRICS):
            assert batch["metrics"][metric]["measured"] is True, f"{metric} per batch"
    assert pooled["per_batch_metrics"] == sorted(sk.PER_BATCH_METRICS)
    single = sk.measure_roots([_batch(tmp_path, "only", doubled(0))])
    for metric in sorted(sk.PER_BATCH_METRICS):
        assert single["metrics"][metric]["measured"] is True


def test_a_pooled_batch_keeps_its_own_rows_beside_the_pool(tmp_path):
    """One pooled figure across a cold-library and a warm-library batch is the merge P2-e and P3-e
    both refused: different denominators by design, and the difference is the experiment."""
    cold = _episode("m.B.r0", library_at_start=[], validations=[_validation("c1")])
    warm = _episode("m.B.r1", library_at_start=["move_object"], offer={"offered": ["move_object"]},
                    offer_rounds=4, decision_rounds=4)
    pooled = sk.measure_roots([_batch(tmp_path, "mixed", cold), _batch(tmp_path, "mixed2", warm)])
    assert len(pooled["batches"]) == 2 and len(pooled["roots"]) == 2
    for batch in pooled["batches"]:
        assert set(batch["metrics"]) == set(sk.METRIC_DEFINITIONS)
        assert batch["per_episode"] and batch["manifest_acquisition"]["proposer"] == "rule"
    assert pooled["metrics"]["S4.rounds_offering_a_program"]["denominator"] == 4
    assert pooled["pooled_by"].startswith("summing integers")
    assert pooled["kind"] == "skill_acquisition_metrics"
    assert pooled["offline"] is True and pooled["cost_estimate_usd"] is None


def test_every_defined_row_appears_whether_or_not_a_counting_site_reached_it(tmp_path):
    """An absent row is read as "not part of the 口径", which is how a denominator goes missing.

    A single-arm batch has no disarmed episode, so `S4.withheld_and_recurred` gets no accumulation slot
    at all; the table still has to carry it, with its own reason.
    """
    measured = sk.measure_roots([_batch(tmp_path, "onearm", _episode("n.B.r0"))])
    assert set(measured["metrics"]) == set(sk.METRIC_DEFINITIONS)
    row = measured["metrics"]["S4.withheld_and_recurred"]
    assert row["measured"] is False and (row["numerator"], row["denominator"]) == (0, 0)
    assert row["not_measured_reason"] == sk._NOT_MEASURED["S4.withheld_and_recurred"]
    assert row["spec_row"] == "skill reuse success"


# ------------------------------------------------------- cost (§11's last four rows) ----
def test_the_cost_group_reads_the_arms_own_counters_off_the_same_artifact(tmp_path):
    on = _episode("o.B.r0", gaps=[_gap()], candidates=[_candidate("c1"), _candidate("c2")],
                  validations=[_validation("c1", ran=4, cost={"wall_s": 1.5, "sim_s": 4.0,
                                                               "calls": 4, "model_calls": 0,
                                                               "sandbox_runs": 4}),
                               _validation("c2", ran=2, measured_true=2,
                                           cost={"wall_s": 0.5, "sim_s": 2.0,
                                                 "calls": 2, "model_calls": 0,
                                                 "sandbox_runs": 2})],
                  cost=_cost(wall_s=2.0, sim_s=6.0, calls=6, model_calls=0, sandbox_runs=6,
                             validated_candidates=2))
    off = _episode("o.B.r1", arm="wo_skill_acquisition", armed=False, wall_time_s=50.0,
                   cost=_cost(wall_s=0.0, sim_s=0.0, calls=0, model_calls=0, sandbox_runs=0,
                              validated_candidates=0))
    rows = sk.measure_roots([_batch(tmp_path, "cost", on, off)])["metrics"]
    assert (rows["C2.simulator_seconds_per_sandbox_run"]["numerator"],
            rows["C2.simulator_seconds_per_sandbox_run"]["denominator"]) == (6.0, 6)
    assert rows["C2.sandbox_runs_per_candidate"]["value"] == 3.0
    assert rows["C2.generation_wall_seconds_per_candidate"]["value"] == 1.0
    assert rows["C2.model_calls"]["numerator"] == 0 and rows["C2.model_calls"]["measured"] is True, \
        "a zero spend is a number, not an absence"
    share = rows["C2.generation_share_of_episode_wall"]
    assert (share["numerator"], share["denominator"]) == (2.0, 10.0), \
        "the disarmed episode spends 0.0 by construction and is not in this denominator"
    assert share["value"] == 0.2


# ------------------------------------------------------------------ the arm gate ----
def test_the_disarmed_arm_may_not_file_a_single_record_of_the_modules_four(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "lying",
        _episode("p.B.r0", arm="wo_skill_acquisition", armed=False, skill_events=["skill_gap"]))])
    problems = sk.check_arms(measured)
    assert len(problems) == 1 and "p.B.r0" in problems[0]
    assert "switches the module off" in problems[0]


def test_a_report_that_claims_records_its_log_does_not_hold_is_a_problem(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "mismatch",
        _episode("q.B.r0", gaps=[_gap()], skill_events=["skill_gap"],
                 claims={"skill_gap": 3, "skill_candidate": 1, "skill_validation": 0,
                         "skill_library": 0}))])
    problems = sk.check_arms(measured)
    assert len(problems) == 1 and "report claims" in problems[0] and "its own log holds" in problems[0]


def test_a_gap_in_the_report_with_no_record_in_the_log_is_a_problem(tmp_path):
    """The report that would assert the stage is the thing under test, so the log decides."""
    measured = sk.measure_roots([_batch(
        tmp_path, "silent", _episode("r.B.r0", gaps=[_gap()], skill_events=[]))])
    problems = sk.check_arms(measured)
    assert len(problems) == 1 and "never filed anything" in problems[0]


def test_an_armed_episode_with_a_genuine_gapless_log_is_not_a_problem(tmp_path):
    """The gate has to be able to say nothing, or every clean batch becomes a finding."""
    measured = sk.measure_roots([_batch(
        tmp_path, "quiet",
        _episode("s.B.r0", decision_rounds=7, library_at_start=["move_object"],
                 offer={"offered": ["move_object"]}, offer_rounds=7, reuse=[_reuse()]))])
    assert sk.check_arms(measured) == []


def test_the_event_types_this_module_counts_are_the_ones_the_frozen_registry_gives_it():
    assert set(sk.OWNED_EVENT_TYPES) == {t for t in V02_EVENT_TYPES if t.startswith("skill_")}
    assert sk.ROUND_EVENT not in V02_EVENT_TYPES, \
        "the v0.2 list is frozen at fifteen; a new kind there is a re-freeze, not an edit"


def test_the_offer_denominator_counts_a_record_the_runtime_actually_files():
    """`decision_context` is v0.1's per-round record (`core/runtime.py` logs it), not a v0.2 kind.

    §11's offer row divides by it, so the name has to be checked against the code that writes it: a
    runtime that stopped filing it would otherwise leave the denominator shrinking in silence.
    """
    runtime = os.path.join(os.path.dirname(os.path.abspath(sk.__file__)), os.pardir, "core",
                           "runtime.py")
    with open(runtime, encoding="utf-8") as f:
        source = f.read()
    assert source.count(f'self.store.log("{sk.ROUND_EVENT}"') == 2, \
        "the runtime files this record from two places; the row's denominator is both of them"
    assert sk.ROUND_EVENT == "decision_context"


# --------------------------------------------------------- printing, writing, entering ----
def test_the_rendered_table_prints_every_row_once_and_the_pipes_line_up(tmp_path):
    measured = sk.measure_roots([_batch(
        tmp_path, "render",
        _episode("t.B.r0", gaps=[_gap()], candidates=[_candidate("c1")],
                 validations=[_validation("c1")]))])
    lines = sk.render_table(measured).splitlines()
    assert lines[0].startswith("§11 Skill Acquisition — SKILL-1（pooled over 1 batch(es): render")
    for metric in sk.METRIC_DEFINITIONS:
        assert sum(1 for line in lines if f"| `{metric}` |" in line) == 1, metric
    rows = [line for line in lines if line.startswith("| ") and "`" in line.split("|")[2]]
    assert all(line.count("|") == 8 for line in rows), \
        "a cell containing a pipe or a stray marker changes the column count"
    assert "*" not in "\n".join(lines)
    for metric, row in measured["metrics"].items():
        if not row["measured"]:
            assert f"- `{metric}` 未测量：{row['not_measured_reason']}" in "\n".join(lines)


def test_the_markdown_block_carries_the_rows_the_counts_the_notes_and_the_audit(tmp_path):
    measured = sk.measure_roots([_batch(tmp_path, "block",
                                        _episode("u.B.r0", validations=[_validation("c1")]))])
    measured["arm_audit"] = {"ok": False, "problems": ["u.B.r0: made up"]}
    text = sk.render_markdown(measured)
    assert text.startswith("## §11 Skill Acquisition — SKILL-1")
    assert "arm enforcement: FAILED — 1 batch(es)" in text
    assert "- ARM PROBLEM: u.B.r0: made up" in text
    assert "| episode | arm | gaps |" in text and "### notes" in text
    for key in measured["notes"]:
        assert f"- `{key}`" in text, key
    assert "*" not in text, "an asterisk in a table cell is read as emphasis and eats the digits"


def test_writing_then_reading_back_reproduces_every_row_and_carries_the_definitions(tmp_path):
    measured = sk.measure_roots([_batch(tmp_path, "disk",
                                        _episode("v.B.r0", validations=[_validation("c1")]))])
    measured["arm_audit"] = {"ok": True, "problems": []}
    out = str(tmp_path / "out")
    path = sk.write(measured, out)
    assert os.path.basename(path) == "skill_acquisition_SKILL_1.json"
    with open(path, encoding="utf-8") as f:
        back = json.load(f)
    assert back["kind"] == "skill_acquisition_metrics"
    assert back["metric_version"] == sk.METRIC_VERSION == "SKILL-1"
    assert back["offline"] is True and back["cost_estimate_usd"] is None
    assert back["definitions"] == sk.METRIC_DEFINITIONS
    assert back["spec_rows"] == {label: list(ids) for label, ids in sk.SPEC_ROWS}
    assert back["not_measured"] == {k: sk._NOT_MEASURED[k] for k in sorted(sk._NOT_MEASURED)}
    assert back["ratio_metrics"] == sorted(sk.RATIO_METRICS)
    assert back["per_batch_metrics"] == sorted(sk.PER_BATCH_METRICS)
    assert back["metrics"] == json.loads(json.dumps(measured["metrics"])), \
        "an artifact that cannot be read back is not a deliverable"
    for key in ("measured", "not_measured_reason", "denominator_too_small_for_a_rate",
                "interval_kind", "wilson_95", "spec_row", "question", "unit", "as_computed"):
        assert key in back["metric_envelope"], key
    with open(os.path.join(out, "skill_acquisition.md"), encoding="utf-8") as f:
        assert f.read() == sk.render_markdown(back)


def test_the_definitions_are_one_object_across_the_three_publications(capsys):
    assert sk.main(["--definitions"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == sk._definitions_payload()
    assert printed["definitions"] == sk.METRIC_DEFINITIONS
    assert printed["spec_rows"] == {label: list(ids) for label, ids in sk.SPEC_ROWS}
    text = sk.render_definitions()
    for label, ids in sk.SPEC_ROWS:
        assert f"### §11 行: {label}" in text
        for metric in ids:
            assert f"`{metric}` — {sk.METRIC_DEFINITIONS[metric]['question']}" in text
    assert "interval: none (count per unit)" in text and "interval: Wilson 95%" in text


def test_the_scorers_own_entry_codes_name_three_different_facts(tmp_path, capsys):
    """2 usage, 3 not a scoreable run directory, 4 an artifact whose arms contradict its own logs.

    Collapsing them into one config error is how a broken measurement leaves a build with the same code
    as a typo.
    """
    assert sk.main([]) == 2
    assert sk.main(["--out", str(tmp_path / "never")]) == 2
    assert sk.main(["--definitions", "--out", str(tmp_path / "never")]) == 2
    assert sk.main(["--measure"]) == 2
    assert sk.main(["--measure", str(tmp_path / "x"), "--wat"]) == 2
    assert sk.main(["--measure", str(tmp_path / "x"), "--out"]) == 2
    assert not (tmp_path / "never").exists(), "a usage error may not create an artifact"
    assert "usage:" in capsys.readouterr().out

    assert sk.main(["--measure", str(tmp_path / "typo")]) == 3
    assert "not a run directory" in capsys.readouterr().out
    assert not (tmp_path / "typo").exists()

    root = _batch(tmp_path, "good", _episode("w.B.r0", gaps=[_gap()], skill_events=["skill_gap"],
                                             candidates=[_candidate("c1")]))
    out = str(tmp_path / "written")
    assert sk.main(["--definitions", "--measure", root, "--out", out]) == 0
    printed = capsys.readouterr().out
    assert '"metric_version": "SKILL-1"' not in printed, "combined prints the 口径 as prose"
    assert "### §11 行: candidate generation rate" in printed
    assert "arm enforcement: OK — 1 batch(es), 1 episode(s)" in printed
    assert f"wrote {os.path.join(out, 'skill_acquisition_SKILL_1.json')}" in printed
    assert os.path.exists(os.path.join(out, "skill_acquisition.md"))

    bad = _episode("w.B.r1", arm="wo_skill_acquisition", armed=False, skill_events=["skill_gap"])
    refused = str(tmp_path / "refused")
    assert sk.main(["--measure", _batch(tmp_path, "bad", bad), "--out", refused]) == 4
    printed = capsys.readouterr().out
    assert "arm enforcement FAILED: nothing was written" in printed and "ARM PROBLEM" in printed
    assert not os.path.exists(refused), "4 is a refusal to publish, not a table with a caveat"


def test_the_production_cli_reaches_the_scorer_rather_than_a_copy_of_it(tmp_path, capsys,
                                                                      monkeypatch):
    """A flag the handler never reads parses, prints nothing and exits 0 — what a dead instrument
    looks like from outside. `skill-metrics` delegates, so no second list of definitions can exist."""
    from embodied_agent import cli

    assert cli.main(["skill-metrics", "--definitions"]) == cli.EXIT_OK
    printed = json.loads(capsys.readouterr().out)
    assert printed == sk._definitions_payload()
    assert printed["definitions"] == sk.METRIC_DEFINITIONS

    assert cli.main(["skill-metrics"]) == cli.EXIT_CONFIG_ERROR
    assert "--definitions or --measure" in capsys.readouterr().err
    assert cli.main(["skill-metrics", "--out", str(tmp_path / "never")]) == cli.EXIT_CONFIG_ERROR
    assert "needs --measure" in capsys.readouterr().err
    assert not (tmp_path / "never").exists()

    seen: list = []

    def fake_main(argv):
        seen.append(list(argv))
        return 4

    monkeypatch.setattr(sk, "main", fake_main)
    # the scorer's 4 is a refusal to publish, not the caller's config error: it travels through
    assert cli.main(["skill-metrics", "--measure", "x", "y", "--out", "o"]) == 4
    assert seen == [["--measure", "x", "y", "--out", "o"]]
    assert cli.main(["skill-metrics", "--definitions"]) == 4, "delegation means no filtering"

    monkeypatch.setattr(sk, "main", lambda argv: 7)
    assert cli.main(["skill-metrics", "--definitions"]) == cli.EXIT_INFRA_ERROR


def test_the_cli_flag_and_the_modules_own_entry_cannot_disagree_about_a_directory(tmp_path,
                                                                                 capsys):
    """Both routes must land in the same arithmetic, or the CLI is a second instrument."""
    from embodied_agent import cli

    root = _batch(tmp_path, "through_cli", _episode("z.B.r0", gaps=[_gap()], skill_events=[],
                                                    candidates=[_candidate("c1")]))
    assert cli.main(["skill-metrics", "--measure", root, "--out", str(tmp_path / "cli_out")]) == 4
    printed = capsys.readouterr().out
    assert "never filed anything" in printed, "the gate runs on the CLI route too"
    lines = [line for line in printed.splitlines() if "| `S1.gap_episodes` |" in line]
    assert len(lines) == 1
    cells = [c.strip() for c in lines[0].split("|")]
    assert cells[5] == "1 / 1", cells
    assert cells[7] == "yes", cells
    direct = sk.measure_roots([root])["metrics"]["S1.gap_episodes"]
    assert cells[4] == direct["as_computed"], "the CLI printed a different arithmetic than the scorer"
    assert not (tmp_path / "cli_out").exists(), "the CLI route refuses to publish too"


# ------------------------------------------------------------- the delivered artifact ----
def _delivered() -> dict:
    path = os.path.join(ROOT, "skill_acquisition_SKILL_1.json")
    if not os.path.isfile(path):
        pytest.skip(f"{path} is the delivered P4-e batch; it is not on this machine")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_the_delivered_artifact_reproduces_its_own_numbers():
    """Re-scoring the roots that produced it must be a no-op on every row of the table.

    A rate that moves when no counting rule changed is a rate that was never stable, and this artifact
    is what a reader of §11 will check P5's report against.
    """
    delivered = _delivered()
    again = sk.measure_roots(delivered["roots"])
    assert again["metric_version"] == delivered["metric_version"] == sk.METRIC_VERSION
    assert again["roots"] == delivered["roots"]
    assert again["definitions"] == delivered["definitions"] == sk.METRIC_DEFINITIONS
    assert again["notes"] == delivered["notes"]
    assert set(again["metrics"]) == set(delivered["metrics"])
    for metric, row in delivered["metrics"].items():
        fresh = again["metrics"][metric]
        for key in ("numerator", "denominator", "value", "measured", "not_measured_reason",
                    "spec_row", "as_computed", "interval_kind", "wilson_95",
                    "denominator_too_small_for_a_rate"):
            assert fresh[key] == row[key], f"{metric}.{key}: {fresh[key]!r} != {row[key]!r}"
    assert len(again["batches"]) == len(delivered["batches"])
    for was, now in zip(delivered["batches"], again["batches"]):
        assert now["run_id"] == was["run_id"] and now["set"] == was["set"]
        assert now["per_episode"] == was["per_episode"], was["run_id"]
        assert now["reuse_naming_reason"] == was["reuse_naming_reason"]
        for metric, row in was["metrics"].items():
            fresh = now["metrics"][metric]
            assert (fresh["numerator"], fresh["denominator"], fresh["measured"]) == \
                (row["numerator"], row["denominator"], row["measured"]), f"{was['run_id']}.{metric}"


def test_the_delivered_artifact_passes_its_own_gate_and_renders_cleanly():
    delivered = _delivered()
    assert delivered["arm_audit"] == {"ok": True, "problems": []}
    assert sk.check_arms({k: v for k, v in delivered.items() if k != "arm_audit"}) == []
    assert set(delivered["metrics"]) == set(sk.METRIC_DEFINITIONS)
    assert len(delivered["batches"]) == len(delivered["roots"])
    assert all(b["per_episode"] for b in delivered["batches"])
    markdown = os.path.join(ROOT, "skill_acquisition.md")
    if os.path.isfile(markdown):
        with open(markdown, encoding="utf-8") as f:
            text = f.read()
        assert "*" not in text
        rows = [line for line in text.splitlines() if line.startswith("| ")
                and "`" in line.split("|")[2]]
        assert rows and all(line.count("|") == 8 for line in rows)
        assert text == sk.render_markdown(delivered)
