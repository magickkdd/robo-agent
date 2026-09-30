"""P1-e contract tests: the seams a sensing arm may move, and the ones it may not.

`perception/arm.py` switches the loop's world from simulator state to a camera. The SPEC
questions that change answers are, in §13's order for P1: does the loop still *choose*
anything it should not (`Runtime` must not do the model's strategy work), does the arm still
have to *earn* a finish (§5.8's layer 2 and §7's exit protocol), and can a batch say which
arm it ran in (§9, §12.1). Each group below pins one of those as a structural fact about the
production objects — a real `PerceptRuntime`, real renders of `dev_c5`, one real actuation
where a verdict needs one — and none of them pins a rate. §11's VLM metrics are P1-f's job,
and a threshold tuned until an arm-shaped test passes would be exactly the number the
benchmark exists to measure.

Seven things are asserted:

* **the seams move as a pair** — a sensor snapshot never meets the privileged verifier, and
  the ten overrides (`_capture_world`, `_verifier`, `_state_diff`, `_begin_episode`,
  `_preflight_trip`, `_requests_used`, `_finalize`, `_held_report`, `_before_execute`,
  `_validate_execution`) are the whole of what the arm adds to `Runtime`;
* **the arm picks no viewpoint** — a default is configuration, `observe`'s argument is the
  model's choice, an undeclared name is a rejection it must repair, and the terminal look
  reuses the camera the loop last stood at;
* **no threshold is relaxed** — `_finish_check` is not overridden, the verifier is handed the
  case's own `VerifyConfig`, a finish asked for over a table nobody can see into is refused,
  and the answer key is read out of a different instrument than the claim;
* **a missing measurement stays a named gap** — a body the frame saw without metres is
  refused by every downstream that would otherwise subtract coordinates, and the list of what
  this frame cannot answer is in the payload the model is asked to act on;
* **the hold state arrives as a report** — `None` means "empty", not "unknown", the ambiguous
  gripper means "unknown" and no further look settles it, the lift comparison the frame would
  fabricate is never even computed, and the report names which of its two possible authors gave
  it — the hook that lets a frame answer is inert on this bench;
* **an arm and a channel that contradict each other are refused before the first round** —
  `wo_vlm` files no `perception` record, `full` requires a channel that consulted a vision
  model, and `vlm` requires a vision model that exists;
* **a look spends the same budget as a decision** — §6.2's HTTP ceiling covers both and can
  end the episode that reaches it, or a VLM episode is reported as free.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import time
import types

import pytest

from embodied_agent import cli
from embodied_agent.core.contracts import (
    Budgets,
    Decision,
    DecisionExecute,
    FailureCode,
    PredicateVerdict,
    SkillCall,
    Source,
    TerminalStatus,
    WorldState,
)
from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import BudgetLedger, Runtime
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.v02 import ABLATION_CONDITIONS, EVENT_MODULE
from embodied_agent.core.v02 import ablation as ablation_record
from embodied_agent.core.verify import RuntimeVerifier, build_world_state
from embodied_agent.evaluation.calibration import cell_catalog
from embodied_agent.evaluation.evaluator import IndependentEvaluator
from embodied_agent.evaluation.run import (
    InfraError,
    build_scene,
    make_source,
    run_group,
    run_one_episode,
    task_input,
)
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.perception.arm import (
    GRASP_UNMEASURED,
    Perceiver,
    PerceptRuntime,
    SensingVerifier,
    build_arm,
)
from embodied_agent.perception.grounding import (
    GROUNDING,
    GROUNDED,
    HELD_SOURCE,
    SEEN_AS,
    GroundingMap,
    arm_coherence,
    channel_readiness,
    declared_views,
    ground_state,
)
from embodied_agent.perception.observe import StubReader, VlmReading
from embodied_agent.perception.verify_percept import DECLINED_FACTS

MAIN, OVERHEAD, FRONT_HIGH = "main", "overhead", "front_high"
VIEWS = (MAIN, OVERHEAD, FRONT_HIGH)
TARGET = "tray_middle"

#: the keys only the `lift_gain_m` comparison produces. None of them may appear in a grasp
#: report: both z's would be `measured support + declared upright half-height`, so a body
#: lifted off the table would be put back at the height it started at and the verdict would be
#: a confident `false` about a grasp the gripper is reporting.
LIFT_KEYS = ("lift_gain_m", "lift_gain_min_m", "z_after", "z_before", "z_delta_m")

#: what two looks of the same static table may legitimately differ by, before anything is
#: concluded about the difference
VOLATILE = {"observation_ref", "state_version", "sim_time", "wall_time"}


def bare(state) -> dict:
    return {k: v for k, v in state.model_dump(mode="json").items() if k not in VOLATILE}


# --------------------------------------------------------------------- fixtures ----


@pytest.fixture(scope="module")
def case():
    return find_case("dev_c5")


@pytest.fixture(scope="module")
def gmap(case):
    return GroundingMap.from_objects(case.objects)


@pytest.fixture()
def scene(case):
    """A fresh scene per test: this file seats bodies and empties grippers."""
    s = build_scene(case)
    yield s
    s.close()


@pytest.fixture()
def goal(scene, case):
    """The goal the runner would hand any arm: parsed from the *initial* scene, once."""
    world = build_world_state(scene, 1, "obs_0001", case.verify)
    return interpret(task_input(case), world)


def make_arm(case, scene, gmap, run_dir, *, perceive="stub", budgets=None, ablation=None,
             adapter=None, views=None, default_view=MAIN, episode_id="p1e-arm"):
    """The arm exactly as `run_one_episode` assembles it, from the one production builder."""
    os.makedirs(run_dir, exist_ok=True)
    episode_id = f"{episode_id}.{perceive}"
    store = EpisodeStore(run_dir, episode_id)
    executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
    rt = build_arm(case=case, scene=scene, executor=executor, store=store, run_dir=run_dir,
                   episode_id=episode_id, perceive=perceive,
                   catalog=cell_catalog(scene.trays.values()), gmap=gmap,
                   budgets=budgets or case.budgets,
                   environment=EnvironmentController(scene, case.fresh_events()),
                   ablation=ablation, adapter=adapter, views=views,
                   default_view=default_view)
    executor.world_provider = rt.observe
    return rt


@pytest.fixture()
def arm(case, scene, gmap, tmp_path):
    return make_arm(case, scene, gmap, str(tmp_path / "arm"))


@pytest.fixture()
def privileged(case, scene, gmap, tmp_path):
    return make_arm(case, scene, gmap, str(tmp_path / "priv"), perceive="privileged")


class _CostedReader:
    """A stub read that says what it paid, so the billing path is exercised on real frames.

    Only `meta["http_requests_this_call"]` and `tokens` differ from `StubReader` — the
    objects, the bboxes and the pixels are the deterministic read P1-b measured. This is the
    cheapest way to stand where `VLMReader` stands in a checkout that has no vision model."""

    channel = "vlm"

    def __init__(self, per_call: int = 2, tokens: int = 50):
        self.inner = StubReader()
        self.per_call = per_call
        self.tokens = tokens
        self.calls = 0

    def read(self, frame, **kw) -> VlmReading:
        self.calls += 1
        reading = self.inner.read(frame, **kw)
        return reading.model_copy(update={
            "meta": {**(reading.meta or {}), "http_requests_this_call": self.per_call},
            "tokens": {"prompt_tokens": self.tokens, "completion_tokens": 0}})


# ------------------------------------------------ 1. the seams move as a pair ----


def test_the_arm_overrides_ten_seams_and_nothing_else(privileged, arm):
    """The whole of what a sensing arm adds to `Runtime`, as a set — the evidence for "no
    strategy lives here" rather than the assertion of it."""
    moved = {n for n in dir(PerceptRuntime)
             if getattr(PerceptRuntime, n, None) is not getattr(Runtime, n, None)
             and not n.startswith("__")}
    assert moved == {
        "_capture_world",     # where a snapshot comes from
        "_verifier",          # who answers a postcondition about it
        "_state_diff",        # what may be concluded from two of them
        "_begin_episode",     # which arm the episode is about to run in
        "_preflight_trip",    # the ceiling its own looks are spending
        "_requests_used",     # looks and decisions on one counter
        "_finalize",          # the terminal look inside that counter
        "_held_report",       # §5.8's layer 1, read when the snapshot is taken
        "_before_execute",    # the camera the model asked for
        "_validate_execution",  # one extra refusal: a camera this arm does not have
    }
    # what it must not quietly re-implement: candidate offer, validation envelope, feedback,
    # the finish protocol, the ledger and the episode loop itself
    for seam in ("_finish_check", "_build_context", "_context_candidates", "_validate_envelope",
                 "_build_feedback", "_record_feedback", "_skill_catalogue", "_terminal_progress",
                 "_episode_success", "_verification_reports", "_outcome_status", "run_episode",
                 "_repeated_without_new_evidence", "_map_failure", "observe", "_billed",
                 "_accumulate_usage", "_capture_terminal_snapshot"):
        assert seam not in moved, f"the sensing arm overrode {seam}"


def test_the_privileged_channel_returns_the_v01_object_untouched(privileged, case):
    """P1-e must add nothing to the sealed path: `build_arm('privileged')` hands back a
    `Runtime`, not a subclass with the seams pre-wired to a camera."""
    assert type(privileged) is Runtime
    assert privileged.config == case.verify
    assert not hasattr(privileged, "perceiver")


def test_a_sensor_snapshot_never_meets_the_privileged_verifier(arm, case):
    """The pair rule. `_capture_world` and `_verifier` are overridden together because
    `Runtime._build_feedback` and `_finish_check` both call `self._verifier(world)`: a sensor
    snapshot handed to the privileged verifier is signed `Source.privileged` and trusted."""
    world = arm.observe()
    verifier = arm._verifier(world)
    assert isinstance(verifier, SensingVerifier) and verifier.source is Source.sensor
    eid = sorted(e.entity_id for e in world.entities)[0]
    assert verifier.verify_placement(eid, TARGET).source is Source.sensor
    # the control: the same snapshot, the verifier this file did not install
    assert RuntimeVerifier(world, case.verify).verify_placement(eid, TARGET).source \
        is Source.privileged


def test_the_verifier_is_handed_the_case_s_own_thresholds(arm, case):
    """No margin is widened on the way in: the bar P1-d measured is the declared one."""
    verifier = arm._verifier(arm.observe())
    assert verifier.config is case.verify
    assert verifier.config.footprint_margin_m == case.verify.footprint_margin_m


# ------------------------------------------------------- 2. no viewpoint is chosen ----


def test_the_loop_inherits_a_camera_and_the_model_asks_for_one(arm):
    """`_capture_world` uses the view it was *asked* for, and otherwise the camera the loop
    last stood at. Both are recorded; neither is picked for the model."""
    assert arm._requested_view is None
    first = arm.observe()
    assert arm.perceiver.view_of(first.observation_ref) == MAIN
    arm._before_execute(SkillCall(plan_id="decision", step_id="d0", skill="observe",
                                 args={"view": OVERHEAD}))
    second = arm.observe()
    assert arm.perceiver.view_of(second.observation_ref) == OVERHEAD
    # and the next look reuses it rather than reaching for a better one
    third = arm.observe()
    assert arm.perceiver.view_of(third.observation_ref) == OVERHEAD
    assert arm._requested_view is None


def test_an_undeclared_view_is_rejected_and_no_look_is_taken(arm, goal):
    """A substitution here would be the runtime choosing where to look: `front_low` is not a
    camera this arm has, so the decision fails and the model has to repair it."""
    decision = Decision(context_id="c0", based_on_state_version=1, goal_ref=goal.goal_id,
                        action="execute",
                        execute=DecisionExecute(skill="observe", args={"view": "front_low"}))
    world = arm.observe()
    before = len(arm.perceiver.percepts)
    ok, errors = arm._validate_execution(decision, None, world)
    assert ok is False and any("front_low" in e for e in errors)
    assert sorted(arm.perceiver.views) == sorted(VIEWS)
    assert len(arm.perceiver.percepts) == before
    # the same decision naming a declared camera is not refused for its view
    ok2, errors2 = arm._validate_execution(
        decision.model_copy(update={"execute": DecisionExecute(
            skill="observe", args={"view": FRONT_HIGH})}), None, world)
    assert ok2 is True and not any("camera this arm has" in e for e in errors2)


def test_an_arm_cannot_be_built_with_an_undeclared_camera(case, scene, gmap, tmp_path):
    with pytest.raises(ValueError, match="undeclared view"):
        make_arm(case, scene, gmap, str(tmp_path / "a"), views=(MAIN, "side"))
    with pytest.raises(ValueError, match="is not among this arm"):
        make_arm(case, scene, gmap, str(tmp_path / "b"), views=(MAIN,), default_view=OVERHEAD)
    assert declared_views((MAIN,)) == (MAIN,)


def test_an_unset_views_flag_is_every_camera_and_a_manifest_empty_list_is_not_a_restriction(
        case, scene, gmap, tmp_path):
    """P5-c read a manifest's `views: []` as "this arm was given one camera" and was corrected.

    The two renderings of one unset flag genuinely differ: `run_group` writes what the operator
    declared (`list(views or ())`, `evaluation/run.py:833`) while `declared_views` turns that same
    `None` into every camera (`perception/grounding.py:267`). So what is pinned is the behaviour
    the correction rests on — an arm nobody restricted can be pointed at each of the three, an arm
    somebody restricted cannot — never the two spellings of the field.
    """
    assert sorted(declared_views(None)) == sorted(VIEWS)
    assert sorted(declared_views(())) == sorted(VIEWS)
    unrestricted = make_arm(case, scene, gmap, str(tmp_path / "unset"))
    assert sorted(unrestricted.perceiver.views) == sorted(VIEWS)
    restricted = make_arm(case, scene, gmap, str(tmp_path / "one"), views=(MAIN,))
    assert sorted(restricted.perceiver.views) == [MAIN]


def test_a_difference_between_two_cameras_is_reported_as_not_comparable(arm):
    """P1-c measured one static table reading tens of millimetres apart across two views, so
    a cross-view diff would manufacture events out of a change of viewpoint."""
    from_above = arm.perceiver.look(OVERHEAD, held=arm._held_report())
    from_front = arm.perceiver.look(FRONT_HIGH, held=arm._held_report())
    diff = arm._state_diff(from_above, from_front)
    assert "not_comparable" in diff
    assert OVERHEAD in diff["not_comparable"] and FRONT_HIGH in diff["not_comparable"]
    assert diff["moved"] == [] and diff["state_changed"] == []
    same_view = arm._state_diff(from_front, arm.perceiver.look(FRONT_HIGH, held=None))
    assert "not_comparable" not in same_view


def test_the_fingerprint_ignores_a_second_look_and_notices_a_second_camera(arm):
    """§6.2's identical-attempt guard reads `_fingerprint`, and on this channel it is alive:
    the tuple is the gripper's report plus millimetre-rounded positions, so it excludes
    `state_version` and `observation_ref` — the two fields that change on every look whatever
    the world does. A second look at an untouched table therefore buys nothing, which is what
    the guard is for.

    The other half is the finding, measured by `work/p1_e_check.py`: a change of *viewpoint*
    does move it, because the same unmoved body reads up to 15 mm elsewhere from another
    camera. A repeated failing action thus counts as having new evidence whenever the model
    also changed camera — the guard honouring `re_observe:` as a refusal's stated resolution,
    at the cost of a round. That trade is §11's to count, not this test's to bless."""
    a = arm.perceiver.look(MAIN, held=None)
    b = arm.perceiver.look(MAIN, held=None)
    assert str(a.observation_ref) != str(b.observation_ref)
    assert a.state_version != b.state_version
    assert arm._fingerprint(a) == arm._fingerprint(b)
    from_above = arm.perceiver.look(OVERHEAD, held=None)
    assert arm._fingerprint(b) != arm._fingerprint(from_above)


# ------------------------------------------------- 3. no threshold is relaxed ----


def test_a_finish_over_a_table_the_camera_cannot_see_into_is_refused(arm, goal):
    """`_finish_check` still demands `overall == true`. Nothing here widens a margin or takes
    `unknown` for an answer: the bodies are on the table, so a finish is not reachable from
    this channel until something is carried — and the cost of finding that out is in looks."""
    assert PerceptRuntime._finish_check is Runtime._finish_check
    ok, report, snapshot = arm._finish_check(goal)
    assert ok is False
    assert report.overall is not PredicateVerdict.true
    assert report.reports and all(r.source is Source.sensor for r in report.reports)
    assert arm.terminal_world.observation_ref == snapshot.observation_ref


def test_the_answer_key_is_not_read_out_of_the_camera(arm, goal, case):
    """§5.8's third layer must not inherit the instrument the arm is being measured on, and
    this is the one record in an episode summary the arm could otherwise have quietly moved:
    `score.scored_observation_ref` is the *loop's* terminal ref — §7 pins that identity ("one
    sample, both judges", held by `test_episodes.py:113`) — which on this channel names a
    rendered frame, while every geometry row the evaluator judged came out of the simulator by
    `TerminalSnapshot.capture`. So the record now says which is which, and a batch of two arms
    can be read as having been scored against a key that did not move with the channel (§12.1).
    """
    ok, report, snapshot = arm._finish_check(goal)
    score = IndependentEvaluator(snapshot, case.eval_spec).score()
    assert str(score["scored_observation_ref"]).startswith("per_"), "this arm's terminal ref is a look"
    assert score["scored_observation_ref"] == arm.terminal_world.observation_ref
    assert arm.terminal_world.source is Source.sensor
    assert score["scored_entities_from"] == "simulator (TerminalSnapshot.capture)"
    # the judged rows are the simulator's own readings, to the last bit, and every one of them
    # belongs to a body this channel has no handle on — the two instruments are structurally
    # distinguishable in the record, which is what §12.1's shared key has to rest on
    for eid, row in snapshot.entities.items():
        assert row["pos"] == [float(v) for v in arm.scene.object_pose(eid)[0][:3]]
        assert row["contacts"] == arm.scene.contact_partners(eid)
    assert snapshot.entities and all(e.body_id == -1 for e in arm.terminal_world.entities)


def test_the_declined_facts_stay_declined_through_the_arm(arm):
    """The arm must not become a producer for the facts P1-d named unmeasurable: seating
    height, hold state and rest are still absent from every placement report it files."""
    world = arm.observe()
    verifier = arm._verifier(world)
    assert world.entities, "the camera reported nothing"
    for entity in world.entities:
        report = verifier.verify_placement(entity.entity_id, TARGET)
        assert set(report.unmeasured) >= set(DECLINED_FACTS)
        for key in ("rest_z", "rest_z_expected", "rest_z_measured", "rest_z_nominal",
                    "height_err_m"):
            assert key not in report.evidence


def test_a_body_the_frame_cannot_metricize_is_answered_by_every_downstream(arm):
    """P1-d residual 1, closed: three consumers read `entity.pose`, and a sensor snapshot is
    the first object in this repository that may legitimately contain a body with none —
    §5.1's "seen, no metres". `dev_c5`'s deterministic reader metricizes everything it sees,
    so the row is *forced* here (one body's pose removed from an otherwise real snapshot, and
    its support moved into the tray the reader would have had to see it in). What is pinned is
    the answer each consumer gives once the metres are gone, and none of the four may raise.

    The shared shape of all four: a missing measurement is a **named gap**, never free space.
    The identical-attempt fingerprint keeps the body and drops the number, so a frame that
    still cannot locate it repeats rather than counting as new evidence; the diff says
    `position_unmeasured` instead of adding it to `moved`; the placement verdict is `unknown`;
    and a tray slot with such a body resting in it *fails* — "somewhere in there, unmeasured"
    is not the same claim as "clear".
    """
    world = arm.observe()
    holder = next(e for e in world.entities if e.attributes.get(SEEN_AS) == "seen:green")
    red = next(e.entity_id for e in world.entities if e.attributes.get(SEEN_AS) == "seen:red")
    gid = holder.entity_id
    unlocated = world.model_copy(update={"entities": [
        e.model_copy(update={"pose": None, "supported_by": TARGET})
        if e.entity_id == gid else e for e in world.entities]})
    assert unlocated.entity(gid).pose is None and unlocated.entity(gid).geometry is not None

    fp = arm._fingerprint(unlocated)
    row = next(r for r in fp[1] if r[0] == gid)
    assert row[1:4] == (None, None, None) and gid in [r[0] for r in fp[1]]
    assert arm._fingerprint(unlocated.model_copy()) == fp

    diff = arm._state_diff(world, unlocated)
    assert gid in diff["position_unmeasured"]
    assert gid not in [m["entity_id"] for m in diff["moved"]]

    report = arm._verifier(unlocated).verify_placement(gid, TARGET)
    assert report.value is PredicateVerdict.unknown
    assert report.unmeasured[0] == "position_unmeasured" and report.evidence == {"measurable": 0.0}

    planner = arm.executor.placement_planner
    candidates = planner.generate(TARGET, red, unlocated)
    assert candidates, "a tray the snapshot names must still offer slots"
    checks = [planner.recheck(c, unlocated) for c in candidates]
    assert all(not c.ok for c in checks)
    assert any("no measured position this frame" in r for c in checks for r in c.reasons)


def test_what_this_frame_cannot_answer_is_visible_to_the_decider(arm, goal, case):
    """P1-d residual 5: §5.1's uncertain marking is only one of the six perception
    capabilities if the thing being tested on it can *see* it. `DecisionContext.model_payload`
    already publishes `progress[].unmeasured`, and the arm adds no field to make that true —
    the pair of moved seams is what puts a camera's list of refusals in front of the model,
    which is the whole reason `would_resolve: re_observe:<view>` is a usable suggestion rather
    than a note in a log.
    """
    world = arm.observe()
    progress = arm._verifier(world).progress(goal)
    assert progress, "the goal parsed to nothing"
    ctx = arm._build_context(task_input(case), goal, world, progress, BudgetLedger(case.budgets))
    rows = ctx.model_payload()["progress"]
    assert len(rows) == len(progress)
    for row in rows:
        assert set(row["unmeasured"]) >= set(DECLINED_FACTS)
        assert row["value"] in ("false", "unknown")


# ------------------------------------------------------------ 4. the hold report ----


def test_an_empty_gripper_is_a_report_and_not_an_unknown(arm):
    """`None` is the contract's own value for "holding nothing". Reading it as `unknown` is
    what made the arm structurally unable to act: `PlanValidator` refuses every pick while the
    hold state is unknown, so the refusal becomes permanent."""
    assert arm.executor.held_state() is None
    world = arm.observe()
    assert world.held_object is None
    rows = [f for f in world.facts if f.kind == "hold_report"]
    assert len(rows) == 1 and rows[0].state == "empty" and rows[0].subject is None
    eid = sorted(e.entity_id for e in world.entities)[0]
    report = arm._verifier(world).verify_grasp(eid)
    assert report.value is PredicateVerdict.false
    assert report.evidence["gripper_reports_empty"] == 1.0


def test_the_only_fact_in_the_snapshot_that_did_not_come_from_the_frame_is_the_hold(case, scene,
                                                                                   gmap, tmp_path):
    """The auditable form of "`held` is supplied from outside": two looks at one static table
    differing only in what the hand says, and the diff names nothing else."""
    arm_a = make_arm(case, scene, gmap, str(tmp_path / "none"))
    held = sorted(e.entity_id for e in arm_a.perceiver.look(MAIN, held=None).entities)[0]
    a = bare(arm_a.perceiver.look(MAIN, held=None))
    b = bare(arm_a.perceiver.look(MAIN, held=held))
    changed = {k for k in a if json.dumps(a[k], sort_keys=True)
               != json.dumps(b[k], sort_keys=True)}
    assert changed == {"held_object", "facts", "entities"}
    assert b["held_object"] == held
    for x, y in zip(a["entities"], b["entities"]):
        diff = {k for k in x if json.dumps(x[k], sort_keys=True)
                != json.dumps(y[k], sort_keys=True)}
        assert diff <= {"held", "attributes"}
        if y["entity_id"] == held:
            assert y["held"] is True and y["attributes"][HELD_SOURCE] == "actuator"
    # what the frame *did* measure is identical: same bodies, same names, same metres
    assert [e["entity_id"] for e in a["entities"]] == [e["entity_id"] for e in b["entities"]]
    assert [e["pose"] for e in a["entities"]] == [e["pose"] for e in b["entities"]]


def test_an_ambiguous_gripper_is_unknown_and_no_further_look_settles_it(arm):
    """`"unknown"` passes through untouched. Keeping it unknown matters because the
    instrument that could settle it is not a camera: `would_resolve` answers empty."""
    world = arm.perceiver.look(MAIN, held="unknown")
    assert world.held_object == "unknown"
    assert [f for f in world.facts if f.kind == "hold_report"] == []
    verifier = arm._verifier(world)
    eid = sorted(e.entity_id for e in world.entities)[0]
    report = verifier.verify_grasp(eid)
    assert report.value is PredicateVerdict.unknown
    assert report.evidence["gripper_report_is_ambiguous"] == 1.0
    for reason in GRASP_UNMEASURED:
        assert verifier.would_resolve(reason) == []
    # a frame-side reason still names the two cameras that were not the one asked
    assert verifier.would_resolve("not_in_this_frame") == [f"re_observe:{v}"
                                                           for v in verifier.alt_views]
    assert verifier.alt_views and MAIN not in verifier.alt_views


def test_the_lift_comparison_is_never_even_computed(arm):
    """Four grasp answers, one rule: the keys only the `lift_gain_m` subtraction produces do
    not appear in any of them, in either direction of the verdict."""
    empty = arm.perceiver.look(MAIN, held=None)
    ambiguous = arm.perceiver.look(MAIN, held="unknown")
    named = arm.perceiver.look(MAIN, held=sorted(e.entity_id for e in empty.entities)[0])
    first = sorted(e.entity_id for e in empty.entities)[0]
    other = sorted(e.entity_id for e in empty.entities)[1]
    reports = [arm._verifier(empty).verify_grasp(first),
               arm._verifier(ambiguous).verify_grasp(first),
               arm._verifier(ambiguous).verify_grasp("obj_not_in_this_frame"),
               arm._verifier(named).verify_grasp(first),
               arm._verifier(named).verify_grasp(other)]
    assert [r.value for r in reports] == [PredicateVerdict.false, PredicateVerdict.unknown,
                                          PredicateVerdict.unknown, PredicateVerdict.true,
                                          PredicateVerdict.false]
    keys = {"gripper_named_the_entity", "gripper_reports_empty", "gripper_report_is_ambiguous",
            "reported_by_this_view", "held_flag_is_true"}
    for report in reports:
        assert set(report.evidence) == keys          # an `unknown` says which channel was mute
        assert not (set(report.evidence) & set(LIFT_KEYS))
        assert set(report.unmeasured) >= set(GRASP_UNMEASURED)
        assert report.source is Source.sensor


def test_the_snapshot_the_loop_acts_on_is_still_a_sensor_snapshot(arm, case):
    """Grounding a name transfers a name and nothing else: no physics handle, no measured
    orientation, and the frame's own label kept beside the task's."""
    world = arm.observe()
    declared = {o["entity_id"] for o in case.objects}
    assert world.entities, "the camera reported nothing"
    for entity in world.entities:
        assert entity.body_id == -1
        assert entity.orientation_measured is False
        assert str(entity.attributes[SEEN_AS]).startswith("seen:")
        if entity.entity_id in declared:
            assert entity.attributes[GROUNDING] == GROUNDED
    # a held body is the exception that proves the rule: named by the actuator, labelled so
    held = world.entities[0].entity_id
    again = arm.perceiver.look(MAIN, held=held)
    row = next(e for e in again.entities if e.entity_id == held)
    assert row.held is True and row.attributes[HELD_SOURCE] == "actuator"
    assert row.body_id == -1 and row.orientation_measured is False


def test_the_hold_report_says_which_instrument_answered(gmap):
    """`held_from` is not decoration, and this is the whole of its contract.

    `held_object` is one field whichever instrument filled it: the gripper's `held_state()` and a
    channel that settled the question from measured geometry both answer `None`, `"obj 1"` or
    `"unknown"`. A snapshot that credits the hand for an answer the pixels gave would collapse
    §5.8's two layers into one number and make the disagreement unfindable, so the report names
    its author — and the default names the gripper, because that is what has always answered.
    """
    empty = WorldState(state_version=1, sim_time=0.0, wall_time=0.0, entities=[], targets=[],
                       observation_ref="obs_authority")

    def said(state) -> str:
        rows = [f for f in state.facts if f.kind == "hold_report"]
        assert len(rows) == 1, [f.kind for f in state.facts]
        return rows[0].text

    by_actuator = ground_state(empty, gmap, held="mug 1")
    assert said(by_actuator) == "the gripper reports holding mug 1"
    assert by_actuator.held_object == "mug 1"
    # the same answer, from below: an entity-less snapshot keeps the naming identical
    by_frame = ground_state(empty, gmap, held="mug 1", held_from="frame")
    assert said(by_frame) == "the frame reports holding mug 1"
    assert by_frame.held_object == by_actuator.held_object
    assert said(ground_state(empty, gmap, held=None, held_from="frame")) \
        == "the frame reports holding nothing"
    # an unknown is not an answer, so it gets no sentence and no author
    mute = ground_state(empty, gmap, held="unknown", held_from="frame")
    assert [f for f in mute.facts if f.kind == "hold_report"] == []
    assert mute.held_object == "unknown"


def test_the_hook_that_lets_a_frame_answer_is_inert_here(arm):
    """`Perceiver.held_for_frame` exists to be overridden, and this bench does not override it.

    The seam is in `look` before grounding because `held_object` is part of the snapshot the
    model reads, and an instrument that answered afterwards would have its own look called by
    someone else's name. What it must do *here* is nothing: the desktop gripper reports empty,
    ambiguous and named answers with real fingers, and a perceiver that has a camera and no
    fingers has no standing to contradict them. The channel that does answer it is
    `MujocoPerceiver`, and its tests are in `test_mujoco_perception.py`.
    """
    perceiver = arm.perceiver
    state = perceiver.look(MAIN, held=None)
    named = sorted(e.entity_id for e in state.entities)[0]
    assert type(perceiver) is Perceiver, "this file's perceiver must not be the overriding one"
    for said in (None, "unknown", named):
        assert perceiver.held_for_frame(said, state) == (said, "actuator")


# -------------------------------------------------------- 5. the ablation gate ----


@pytest.mark.parametrize("condition, channel, expect_refusal", [
    ("full", "vlm", False),
    ("full", "stub", True),
    ("wo_vlm", "stub", False),
    ("wo_vlm", "privileged", False),
    ("wo_vlm", "vlm", True),
    # every other condition leaves the vlm module *on*, so a channel that never consults one
    # cannot be run under it: `wo_planning` is not a perception arm with a camera in it
    ("wo_planning", "stub", True),
    ("wo_planning", "vlm", False),
    ("wo_working_memory", "vlm", False),
], ids=lambda v: str(v))
def test_an_arm_claim_and_a_channel_are_checked_against_each_other(condition, channel,
                                                                   expect_refusal):
    reasons = arm_coherence(ablation_record(condition), channel)
    assert bool(reasons) is expect_refusal, (condition, channel, reasons)


def test_the_vlm_channel_refuses_to_silently_become_a_stub(case, scene, gmap, tmp_path):
    """The one substitution this gate exists to prevent: `vlm` is a coherent `full` arm, and
    this checkout has no vision model behind it. Reporting a stub episode under the name
    `full` is what the readiness refusal blocks, and it blocks it with the budget sentence."""
    assert channel_readiness("stub") == [] and channel_readiness("privileged") == []
    with pytest.raises(ValueError, match="no vision model is configured"):
        make_arm(case, scene, gmap, str(tmp_path), perceive="vlm")
    with pytest.raises(ValueError, match="budget stated"):
        build_arm(case=case, scene=scene, executor=None, store=None, run_dir=str(tmp_path),
                  episode_id="x", perceive="vlm", catalog=[], gmap=gmap, budgets=case.budgets)


def test_a_contradicting_arm_is_refused_before_the_first_round(case, scene, gmap, tmp_path):
    with pytest.raises(ValueError, match="cannot run on channel"):
        make_arm(case, scene, gmap, str(tmp_path / "a"), perceive="stub",
                 ablation=ablation_record("full"))
    with pytest.raises(ValueError, match="not the full system"):
        make_arm(case, scene, gmap, str(tmp_path / "b"), perceive="privileged",
                 ablation=ablation_record("full"))
    with pytest.raises(ValueError, match="unknown perception channel"):
        make_arm(case, scene, gmap, str(tmp_path / "c"), perceive="lidar")


def test_a_stub_episode_files_one_ablation_record_and_no_perception_record(case, scene, gmap,
                                                                          tmp_path, goal):
    """§9's *w/o VLM (privileged or limited semantic baseline)* read as the arm it is: the
    module that owns the `perception` record is off, so the episode produces none — and the arm
    it ran in is on the log before anything that claim governs."""
    arm = make_arm(case, scene, gmap, str(tmp_path / "ep"),
                   budgets=Budgets(max_decision_rounds=3, max_skill_calls=2))
    source = make_source("B", case, None, arm, goal)
    result = arm.run_episode(task_input(case), goal, source, mode="B",
                             prologue={"wall_start": time.time(), "http_requests": 0})
    events = arm.store.read_all()
    seen = [e["type"] for e in events]
    assert seen.count("perception") == 0
    ablation_events = [e for e in events if e["type"] == "ablation"]
    assert len(ablation_events) == 1
    payload = ablation_events[0]["payload"]
    # `episode_id` is a reserved envelope key, so the record's copy is captured, not nested
    assert ablation_events[0]["episode_id"] == arm.episode_id == payload.get("episode_id",
                                                                            arm.episode_id)
    assert payload["condition"] == "wo_vlm"
    assert payload["modules_off"] == ["vlm"]
    assert payload["registry_sha256"]
    assert "channel stub" in payload["notes"]
    assert "grounding map" in payload["notes"]
    assert arm.ablation.violations([t for t in seen if t in EVENT_MODULE]) == []
    # the claim precedes the evidence it governs: an arm recorded after the looks it licenses
    # is a label, not a gate
    assert (min(e["sequence"] for e in events if e["type"] == "ablation")
            < min(e["sequence"] for e in events if e["type"] == "observation"))
    assert result.decision_rounds <= 3


def test_the_privileged_arm_records_no_ablation_at_all(privileged, goal, case):
    """The v0.1 log stays the v0.1 log: no record type this phase invented appears in a
    privileged episode, so a baseline diff is a diff of episodes, not of schema."""
    source = make_source("B", case, None, privileged, goal)
    privileged.run_episode(task_input(case), goal, source, mode="B",
                           prologue={"wall_start": time.time(), "http_requests": 0})
    seen = [e["type"] for e in privileged.store.read_all()]
    assert "ablation" not in seen and "perception" not in seen


# ------------------------------------------- 6. a look spends the decision budget ----


def test_a_look_is_charged_against_the_http_ceiling(arm):
    """SPEC 6.2 bills model requests, and a vision request is one. An arm that counted only
    decisions would report a VLM episode as free."""
    arm.perceiver.reader = _CostedReader(per_call=2)
    assert arm._looks_unbilled == 0
    arm.observe()
    assert arm._looks_unbilled == 2
    assert arm._requests_used(types.SimpleNamespace(http_requests=5), 1) == 2 + 4
    assert arm._looks_unbilled == 0
    arm.perceiver.reader = StubReader()
    assert arm._requests_used(types.SimpleNamespace(http_requests=3), 3) == 0


def test_look_cost_reaches_the_ledger_and_the_row(case, scene, gmap, tmp_path, goal):
    """The cost is not only computed, it is billed: with a rule policy — which spends no HTTP
    at all — every request in the ledger came from the camera."""
    arm = make_arm(case, scene, gmap, str(tmp_path / "costed"),
                   budgets=Budgets(max_decision_rounds=8, max_skill_calls=20,
                                   max_http_requests=8))
    arm.perceiver.reader = _CostedReader(per_call=2, tokens=50)
    source = make_source("B", case, None, arm, goal)
    result = arm.run_episode(task_input(case), goal, source, mode="B",
                             prologue={"wall_start": time.time(), "http_requests": 0})
    # Nothing is lost between a look and the ledger, and nothing is billed twice: the total is
    # exactly the looks this episode took, at what each one said it cost.
    assert result.http_requests > 0
    assert result.http_requests == arm.perceiver.http_requests
    assert arm.perceiver.http_requests == len(arm.perceiver.percepts) * 2
    assert arm._looks_unbilled == 0
    assert arm.perceiver.usage().get("prompt_tokens", 0) > 0
    assert result.decision_rounds < 8


def test_the_http_ceiling_ends_the_episode_that_spends_it(case, scene, gmap, tmp_path, goal):
    """§6.2's request cap is a budget, not a caption. On the privileged path the v0.1 loop only
    reports the pair (a rule policy spends 0, and the archived baseline's peak is 3 of 32); the
    arm that bills several looks per round is where the number becomes reachable, so that is
    where the ceiling has to end the episode — before the round, like every other budget."""
    budgets = Budgets(max_decision_rounds=12, max_skill_calls=20, max_http_requests=8)
    arm = make_arm(case, scene, gmap, str(tmp_path / "ceiling"), budgets=budgets)
    # the bound is the arm's: the base loop's own seam stays silent for the same ledger
    base = Runtime(scene, arm.executor, str(tmp_path / "base"), case.budgets, "p1e-base",
                   config=case.verify)
    assert base._preflight_trip(types.SimpleNamespace(http_requests=999)) is None

    trip = arm._preflight_trip(types.SimpleNamespace(http_requests=8))
    assert trip is not None
    status, code, note = trip
    assert status is TerminalStatus.failed and code is FailureCode.BUDGET_EXHAUSTED
    assert "http request budget 8" in note and "looks billed" in note
    # under the ceiling the seam says nothing: it is a bound, not an early exit
    assert arm._preflight_trip(types.SimpleNamespace(http_requests=4)) is None

    arm.perceiver.reader = _CostedReader(per_call=4)
    source = make_source("B", case, None, arm, goal)
    result = arm.run_episode(task_input(case), goal, source, mode="B",
                             prologue={"wall_start": time.time(), "http_requests": 0})
    assert result.failure_type == "BUDGET_EXHAUSTED"
    assert "http request budget 8" in (result.artifacts.get("note") or "")
    assert result.http_requests >= 8
    # the episode stops at the next pre-flight, so the crossing is one round's traffic and the
    # round count never reaches the 12 a privileged arm would have used: this is what the cost
    # of looking *is*, and §11 reports it rather than rounding it away
    assert result.decision_rounds <= 3


# ------------------------------------------------------------------- 7. the gates ----


def test_every_new_knob_defaults_to_the_v01_path():
    """P1-e is opt-in. A call written before this phase must still mean the same experiment:
    `perceive='privileged'`, no ablation record, no views."""
    episode, group = inspect.signature(run_one_episode).parameters, \
        inspect.signature(run_group).parameters
    assert episode["perceive"].default == group["perceive"].default == "privileged"
    for kw in ("ablation", "adapter", "views"):
        assert episode[kw].default is None
    assert episode["default_view"].default == MAIN
    assert group["ablation"].default is None and group["views"].default is None
    assert group["default_view"].default == MAIN


def _argns(**kw) -> types.SimpleNamespace:
    base = dict(perceive="privileged", ablation=None, view=MAIN, views=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_the_cli_refuses_every_incoherent_pair_before_a_scene_opens():
    kw, refusals = cli._perception_kwargs(_argns(perceive="stub"))
    assert refusals == [] and kw["perceive"] == "stub"
    assert kw["ablation"].condition == "wo_vlm"

    kw, refusals = cli._perception_kwargs(_argns(perceive="privileged", ablation="wo_vlm"))
    assert refusals == [] and "ablation" not in kw

    for args, needle in (
            (_argns(perceive="vlm"), "no vision model is configured"),
            (_argns(perceive="stub", ablation="full"), "never consults a vision model"),
            (_argns(perceive="stub", views="main,side"), "undeclared view"),
            (_argns(perceive="stub", view="front_low"), "is not a camera this arm has"),
            (_argns(perceive="stub", ablation="wo_nonsense"), "unknown ablation condition"),
            (_argns(perceive="privileged", ablation="wo_nonsense"), "unknown ablation condition"),
    ):
        bad_kw, bad = cli._perception_kwargs(args)
        assert bad_kw == {} and any(needle in r for r in bad), (args, bad)


def test_the_privileged_channel_carries_the_planning_arms_and_refuses_the_rest(tmp_path):
    """P2-e moved 9's planning contrast onto the zero-spend channel.

    `arm_coherence` is a check about the `perception` record, and no planning arm turns the vision
    model off, so a gate that ran it here would refuse the whole of 9's planning table while a
    privileged world kept doing exactly what it always did. What this channel cannot carry is the
    camera arm in its armed form: on a privileged world nothing is switched off, so `wo_vlm` is
    accepted and reported as the v0.1 baseline *without* an `ablation` record at all.

    The two module arms are the two that changed after P2, and they changed the same way — P3-c moved
    `wo_episodic_memory` from "no module to switch off" to "carried, given a store", P4-d moved
    `wo_skill_acquisition` from "no module to switch off" to "carried, given `--skill-memory`". Both
    are still refused when the installed thing they switch off is missing, and that reason is now about
    the arm rather than about the module existing: a loop constructed with no store takes a code path
    that merely *behaves* like the ablated arm, and an ablation whose control is reached by accident is
    not a control. So each condition is accepted and refused by this one test, on one flag's presence.
    The asymmetry the gate keeps between the two is that §5.6's module has nothing to do with §5.4's —
    `wo_skill_acquisition` is not a member of `EPISODIC_ARMS`, so the store rule is deliberately not
    applied to it.
    """
    for condition in ("full", "wo_planning", "wo_working_memory", "wo_replanning"):
        kw, refusals = cli._perception_kwargs(_argns(perceive="privileged", ablation=condition))
        assert refusals == [], refusals
        assert kw["ablation"].condition == condition
        assert kw["ablation"].modules_off == list(ABLATION_CONDITIONS[condition])

    for condition, needle in (("wo_episodic_memory", "needs --experience-store"),
                              ("wo_skill_acquisition", "needs --skill-memory")):
        kw, refusals = cli._perception_kwargs(_argns(perceive="privileged", ablation=condition))
        assert kw == {} and any(needle in r for r in refusals), (condition, refusals)

    kw, refusals = cli._perception_kwargs(_argns(
        perceive="privileged", ablation="wo_episodic_memory",
        experience_store=str(tmp_path / "store.jsonl")))
    assert refusals == [], refusals
    assert kw["ablation"].condition == "wo_episodic_memory"
    assert kw["ablation"].modules_off == list(ABLATION_CONDITIONS["wo_episodic_memory"])
    # a store that does not exist yet is a cold start, not an error, and must not have been created
    assert not os.path.exists(tmp_path / "store.jsonl")

    kw, refusals = cli._perception_kwargs(_argns(
        perceive="privileged", ablation="wo_skill_acquisition",
        skill_memory=str(tmp_path / "library.jsonl")))
    assert refusals == [], refusals
    assert kw["ablation"].condition == "wo_skill_acquisition"
    assert kw["ablation"].modules_off == list(ABLATION_CONDITIONS["wo_skill_acquisition"])
    assert kw["skill_memory"].path == str(tmp_path / "library.jsonl")
    assert not os.path.exists(tmp_path / "library.jsonl")

    kw, refusals = cli._perception_kwargs(_argns(perceive="privileged", ablation="wo_vlm"))
    assert refusals == [] and "ablation" not in kw


def test_both_cli_entries_carry_the_same_gate():
    """`run` and `evaluate` must not be able to disagree about what an arm means.

    The two checks live in two places on purpose. The channel name is closed by argparse
    *and* by every downstream builder, because an unknown channel has no object graph at all;
    the condition and the view are left to the gate, which can hand an operator every reason
    at once instead of argparse's first-complained-about-one."""
    for command in (cli.cmd_run, cli.cmd_evaluate):
        assert "_perception_kwargs(args)" in inspect.getsource(command)
    parser = argparse.ArgumentParser()
    cli._add_perception_flags(parser)
    parsed = parser.parse_args(["--perceive", "stub", "--ablation", "wo_vlm",
                                "--views", "main,overhead", "--view", "overhead"])
    assert (parsed.perceive, parsed.ablation, parsed.view) == ("stub", "wo_vlm", "overhead")
    assert parsed.views == "main,overhead"
    assert parser.parse_args(["--ablation", "wo_nonsense"]).ablation == "wo_nonsense"
    assert parser.parse_args(["--view", "front_low"]).view == "front_low"
    assert cli._perception_kwargs(_argns(perceive="stub", ablation="wo_nonsense"))[1]
    assert cli._perception_kwargs(_argns(perceive="stub", view="front_low"))[1]
    with pytest.raises(SystemExit):
        parser.parse_args(["--perceive", "lidar"])


def test_the_runner_refuses_an_unrunnable_arm_without_leaving_a_batch_behind(tmp_path):
    """A refusal must not spend a request or write a run directory: an undeployable channel,
    an unknown channel name and a contradicting pair are all settled before `run_group` opens
    `out_root`."""
    out_root = str(tmp_path / "runs")
    os.makedirs(out_root, exist_ok=True)
    for kwargs in ({"perceive": "vlm"}, {"perceive": "lidar"},
                   {"perceive": "stub", "ablation": ablation_record("full")}):
        with pytest.raises(InfraError):
            run_group("dev", modes=("B",), repeats=1, out_root=out_root, limit=1,
                      case_ids=["dev_c5"], frames=False, **kwargs)
        assert os.listdir(out_root) == []


def test_the_grounding_map_is_a_declared_function_of_the_case(case, gmap):
    """The bridge is the public half of the case, and it is hashable: a re-labelled
    declaration is a different experiment even when the pictures are identical."""
    by_colour = {str(o["attributes"]["color"]): o["entity_id"] for o in case.objects}
    assert gmap.by_colour == by_colour
    assert set(gmap.by_colour.values()) == {o["entity_id"] for o in case.objects}
    assert gmap.sha256() == GroundingMap.from_objects(case.objects).sha256()
    assert gmap.sha256() != GroundingMap(dict(by_colour, teal="obj_teal_1")).sha256()
    # v0.4 R3 narrowed the duplicate-colour refusal: with no declared shapes to split
    # the colour the bridge still refuses, under the new sentence (evolution recorded
    # in the v0.4 phase log; the old wording was pinned here since the c3-era refusal)
    with pytest.raises(ValueError, match="cannot be grounded"):
        GroundingMap.from_objects([{"entity_id": "a_1", "attributes": {"color": "red"}},
                                   {"entity_id": "b_1", "attributes": {"color": "RED"}}])


def test_a_grounding_map_that_is_not_a_function_is_refused():
    with pytest.raises(ValueError, match="not a function"):
        GroundingMap({"red": "obj_red_1", "crimson": "obj_red_1"})
