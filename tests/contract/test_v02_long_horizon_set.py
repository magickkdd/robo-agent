"""SPEC-v0.2 §8 contract tests for the `long_horizon` set (P2-d).

The set is the first thing in the project whose *purpose* is a long horizon, so two failures are
available and only one of them is obvious:

* the cases could be impossible, which is why `_respect_region_capacity` exists for the frozen 47 and
  why `preflight` re-asks the question for these six against the real placement instrument; and
* the cases could be reachable but *shallow* — six placements in a row is not §8's "complex task". So
  the complexity claims are checked as measurements: that each case's shared region is a resource the
  planner sees shrink when its own object is seated in it, and that every disturbance is armed
  inside that region, so "which row does this invalidate" has an answer.

One claim that was here before is gone, and its absence is the finding. An earlier version asserted
that `derive` finds a dependency edge once one object of a shared pair is measured seated. Measured
on all six cases it authores none — 47, 48 or 81 clear slots stay 13 to 36 after one body is seated,
which is still more space than the two or three objects the region owes, so the deriver is right to
report no constraint. The number is now a test of the *rule* (an edge appears exactly when a region
has fewer clear slots than objects still owed there) rather than of an outcome that this scene's
geometry does not produce.

The second thing under test is the separation. These cases are authored after the frozen 47 exist,
so `long_horizon` must be resolvable by name (the runner has to be able to drive it) and invisible to
`frozen_manifest`, `build_set("all")` and `configs/experiment/frozen_tasks_v1.json` — a set that
moved a published hash would retroactively invalidate every number in §11's first tables.

No episode is run here, and no model is called: preflight reads the scene through
`PlacementPlanner`, which is the same call the runtime makes before it claims a slot is free.
"""
from __future__ import annotations

import contextlib
import json
from collections import Counter
from pathlib import Path

import pybullet as p
from embodied_agent.core.contracts import TaskInput
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.placement_planner import PlacementPlanner
from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation import long_horizon_tasks as lh
from embodied_agent.evaluation.tasks import (ALL_SETS, FROZEN_PATH, SET_NAMES, TaskCase, build_case,
                                            build_set, check_frozen, find_case, frozen_manifest)

lh_root = Path(__file__).resolve().parents[2]

import pytest


@pytest.fixture(scope="module")
def cases():
    return lh.build_long_horizon_set()


@pytest.fixture(scope="module")
def on_disk():
    """The frozen file, as any reader of the deliverable sees it (no scene built to get here)."""
    with open(lh.LH_FROZEN_PATH, encoding="utf-8") as f:
        return json.load(f)


@contextlib.contextmanager
def seated(case: TaskCase, entity_id: str, region: str):
    """The case's v1 scene with one of its own bodies measured inside `region`.

    The body is teleported and settled — no arm, no policy — and the world is re-read from the
    scene. A hand-written `OccupancyRecord` is not a substitute: the placement instrument takes a
    neighbour's pose from the *entity*, so a record whose entity is still on the table removes no
    slot at all, which is the wrong answer the first version of this file was about to freeze.
    """
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    try:
        tray, body = scene.trays[region], scene.objects[entity_id]
        p.resetBasePositionAndOrientation(
            body["body"], [tray["center"][0], tray["center"][1],
                           tray["floor_top"] + body["half_h"] + 0.01], [0, 0, 0, 1],
            physicsClientId=scene.cid)
        p.resetBaseVelocity(body["body"], [0, 0, 0], [0, 0, 0], physicsClientId=scene.cid)
        scene.settle(1.0)
        world = build_world_state(scene, 2, "obs_0002", case.verify)
        yield scene, world, PlacementPlanner(scene, config=case.verify)
    finally:
        scene.close()


def _shared(case) -> tuple[str, list[str]]:
    """(region, its co-owned entities) from the hidden truth, not from prose."""
    load: dict[str, list[str]] = {}
    for a in case.eval_spec.assignments:
        load.setdefault(a.target_id, []).append(a.entity_id)
    region = max(load, key=lambda t: (len(load[t]), t))
    return region, sorted(load[region])


# ------------------------------------------------------- §8's own requirements ----
def test_every_case_carries_the_bullets_eight_asks_for(cases, on_disk):
    """3-8 objects, several subgoals, a resource constraint, a disturbance, budget as declared.

    Each bullet becomes a check on a field rather than a sentence in a docstring, because a task set
    whose complexity claims are prose is a task set that quietly stops being complex.
    """
    assert [c.task_id for c in cases] == [c["task_id"] for c in on_disk["cases"]]
    for case in cases:
        assert 3 <= case.n_objects <= 8, f"{case.task_id}: §8 asks for 3-8 objects"
        assert len(case.eval_spec.assignments) >= 3, "several subgoals means several relations"
        assert case.events, f"{case.task_id}: §8 asks for at least one state change or failure"
        load = Counter(a.target_id for a in case.eval_spec.assignments)
        assert max(load.values()) >= 2, f"{case.task_id}: no finite-space constraint in the truth"
        assert max(load.values()) <= lh.MAX_OBJECTS_PER_REGION, "authored past measured capacity"
        got = case.budgets.model_dump(mode="json")
        assert {k: got[k] for k in lh.LH_BUDGET} == lh.LH_BUDGET, \
            f"{case.task_id}: the budget is the one the manifest declares, per case"


def test_every_case_is_physically_open_before_it_is_frozen(cases, on_disk):
    """`preflight` ran on the real scene; the manifest carries its numbers, so the claim is auditable."""
    recorded = {c["task_id"]: c for c in on_disk["cases"]}
    for case in cases:
        entry = recorded[case.task_id]
        assert entry["preflight"]["ok"] is True, \
            f"{case.task_id}: {json.dumps(entry['preflight']['per_object'])}"
        for eid, v in entry["preflight"]["per_object"].items():
            assert v["grasp_candidates"] >= 1, f"{case.task_id}/{eid}: nothing to grasp"
            assert min(v["clear_slots"].values()) >= 1, \
                f"{case.task_id}/{eid}: the truth asks for a region with no slot"


def test_the_shared_region_is_a_resource_the_planner_measures(cases, on_disk):
    """§8's "finite space", as a number rather than as an adjective.

    Seat one of the region's own objects and ask the same instrument how many slots the next one
    has. The frozen file carries both counts, so the claim is auditable without re-running a scene;
    the loop re-derives the pair for every case so a manifest that had been edited by hand cannot be
    the only source.
    """
    recorded = {c["task_id"]: c for c in on_disk["cases"]}
    for case in cases:
        region, members = _shared(case)
        assert len(members) >= 2, f"{case.task_id}: {region} is not shared, so §8's bullet is unclaimed"
        probe = recorded[case.task_id]["preflight"]["shared_region"]
        assert probe["ok"] is True, json.dumps(probe)
        assert probe["region"] == region and probe["seated"] == members[0]
        assert probe["measured_inside"] == [members[0]], \
            f"{case.task_id}: the body the probe moved is not the one the world measures inside"
        for eid, after in probe["clear_after"].items():
            assert after < probe["v1_clear"][eid], (
                f"{case.task_id}/{region}: seating {probe['seated']} left {after} slots for {eid}, "
                f"the same as the {probe['v1_clear'][eid]} an empty region offers — the region is "
                f"not a constraint in this case, so the claim above would be empty too")


def test_the_deriver_reports_a_dependency_only_where_the_geometry_owes_one(cases):
    """The rule, tested in both directions, on each case's own mid-episode snapshot.

    `_capacity_dependencies` accepts one condition: a region has fewer clear slots than it has
    objects still owed there. On these six scenes that never happens after a single hand-off (13 to
    36 slots remain for 2 or 3 objects), so `derive` correctly authors no edge — and the test that
    used to assert an edge here was measuring a wish. What is checked instead is that the edge set
    and the slot shortage agree, per case, on a snapshot the physics server produced.

    Two things measured here are worth recording because they are not what a reader of §5.2 would
    assume. A row `derive` has just authored is `Status.pending` with `satisfied=unknown` even when
    the verifier reports its predicate true: authoring a plan and claiming a fact are separate
    permissions, and only the runtime's evidence loop may do the second. And a region that has lost
    a third of its slots gets no `precondition` on its rows — the deriver does not describe pressure
    as an obstacle.
    """
    from embodied_agent.planning.subgoals import Instruments, derive

    def is_true(item) -> bool:
        return str(getattr(item.value, "value", item.value)) == "true"

    for case in cases:
        region, members = _shared(case)
        with seated(case, members[0], region) as (scene, world, planner):
            goal = interpret(TaskInput(task_id=case.task_id, utterance=case.utterance), world)
            prog = RuntimeVerifier(world, case.verify).progress(goal)
            d = derive(goal, world, planner=planner, progress=prog)

            # the plan is the task: one `achieve` row per (object, region) the truth asks for,
            # nothing invented and nothing dropped because it was already finished
            pairs = {(s.target_entity_ids[0], s.target_region_id) for s in d.subgoals
                     if s.kind == "achieve" and s.target_region_id}
            truth = {(a.entity_id, a.target_id) for a in case.eval_spec.assignments}
            assert pairs == truth, f"{case.task_id}: the plan is not the task ({pairs ^ truth})"
            assert any(is_true(x) and x.entity_id == members[0] and x.target_id == region
                       for x in prog), (
                f"{case.task_id}: {members[0]} was moved to the centre of {region} and the verifier "
                f"still does not report the relation — the snapshot, not the plan, is wrong")
            assert all(not s.preconditions for s in d.subgoals), (
                f"{case.task_id}: a row carries an obstacle the slot counts do not support: "
                f"{[(s.target_entity_ids, s.preconditions) for s in d.subgoals if s.preconditions]}")

            ins = Instruments(planner, world)
            owed: dict[str, list[str]] = {}
            for eid, tid in sorted(truth):
                if not any(is_true(x) and x.entity_id == eid and x.target_id == tid for x in prog):
                    owed.setdefault(tid, []).append(eid)
            short = {tid: (min(len(ins.feasible(e, tid).ok) for e in rows), len(rows))
                     for tid, rows in owed.items() if len(rows) >= 2}
            shortages = {t: v for t, v in short.items() if v[0] < v[1]}
            edges = [e for e in d.dependencies if str(e.kind) == "DependencyKind.resource"]
            assert bool(edges) == bool(shortages), (
                f"{case.task_id}: {len(edges)} resource edges against shortages {short}: the "
                f"dependency graph and the slot counts disagree about what constrains this task")


def test_every_disturbance_is_armed_inside_the_shared_region(cases):
    """§8's dependency, in the form this set can actually deliver.

    A disturbance that lands on an unrelated object invalidates a row nobody was depending on, and
    the episode degenerates into "do one more placement". Here every event either names a body the
    shared region owes, or names no body and is armed on that region itself — so the relation it can
    flip is one of the coupled ones, which is what makes "which row does this invalidate?" a
    question with a non-trivial answer.
    """
    for case in cases:
        region, members = _shared(case)
        assert case.events, f"{case.task_id}: §8 asks for a state change or a failure"
        for spec in case.event_specs():
            lands = (spec["entity_id"] in members) if spec["entity_id"] else \
                spec["when_target_id"] == region
            assert lands, (f"{case.task_id}: {spec['event_id']} is armed on "
                           f"{spec['entity_id'] or spec['when_target_id']}, outside the shared "
                           f"{region} ({members})")


def test_preflight_refuses_a_task_the_utterance_cannot_say(cases):
    """The other half of the negative control, and the bug that produced it.

    `make_objects` cycles five colours, so at 6 objects two bodies share one; `class_utterance` names
    a colour and nothing else, and the interpreter therefore resolves 绿色物体 to two entities and
    marks the goal ambiguous. That is the reason `lh_c6` is authored with explicit mentions, and this
    test is the reason the reason is true: the same seed, the same six bodies, the class phrasing, and
    `preflight` says no while the geometry says yes.
    """
    six = next(c for c in cases if c.task_id == "lh_c6_two_disruptions")
    cfg = next(c for c in lh.LH_CONFIGS if c["name"] == "lh_c6_two_disruptions")
    ambiguous = build_case({**cfg, "style": "class"})
    assert [o["attributes"]["color"] for o in ambiguous.objects].count(
        ambiguous.objects[0]["attributes"]["color"]) == 2, \
        "the collision this test is about is that two objects share a colour"
    verdict = lh.preflight(ambiguous)
    assert verdict["ok"] is False
    assert verdict["understanding"]["ok"] is False and "matches 2 entities" in (
        verdict["understanding"]["clarification"] or "")
    assert verdict["shared_region"]["ok"] is True, \
        "the geometry is fine; only the utterance is not — that is the half under test"
    assert lh.preflight(six)["ok"] is True, "and the case as authored does not have the problem"


def test_preflight_refuses_a_case_the_scene_cannot_open():
    """The negative control: `pr_target_full` is authored so that its only region is occupied.

    Without this, "preflight passed" could be a check that always passes — the protocol case whose
    blocker fills the tray is the one place in the frozen list where the same question has a known
    answer of *no*. Which of the three refusals fires is part of the claim: here the utterance is
    sayable and the region is not shared, so it is the slot count that must refuse.
    """
    blocked = find_case("pr_target_full")
    verdict = lh.preflight(blocked)
    assert verdict["ok"] is False, json.dumps(verdict, default=str)
    assert verdict["understanding"]["ok"] is True, "a protocol case's phrasing is not the defect"
    assert verdict["shared_region"]["ok"] is False
    empty = [e for e, v in verdict["per_object"].items()
             if min(v["clear_slots"].values()) < 1]
    assert empty, f"expected a region with no slot; got {json.dumps(verdict['per_object'])}"


# ------------------------------------------------------------- the separation ----
def test_the_new_set_is_reachable_by_name_and_absent_from_the_frozen_47(cases):
    assert "long_horizon" in SET_NAMES, "the runner must be able to ask for the set"
    assert build_set("long_horizon") and len(build_set("long_horizon")) == len(cases) == 6
    assert lh.LH_SET_NAME not in ALL_SETS, "registered means frozen-47 membership"
    assert len(build_set("all")) == 47, "the long-horizon batch must not enlarge `all`"
    ids = {c.task_id for c in cases}
    assert ids.isdisjoint({c.task_id for c in build_set("all")}), "a case in both sets is two truths"
    assert ids.isdisjoint({t["task_id"] for s in frozen_manifest()["sets"].values() for t in s})
    ok, msg = check_frozen(FROZEN_PATH)
    assert ok, f"the published 47-case hash moved: {msg}"


def test_the_manifest_freezes_to_its_own_file_and_agrees_with_the_code():
    ok, msg = lh.check_lh_frozen()
    assert ok, msg
    assert lh.LH_FROZEN_PATH != FROZEN_PATH
    recorded = json.load(open(lh.LH_FROZEN_PATH, encoding="utf-8"))
    assert recorded["set"] == lh.LH_SET_NAME
    assert json.load(open(FROZEN_PATH, encoding="utf-8"))["sets"].keys() != recorded["cases"]


def test_a_hand_edited_scenario_fails_its_own_hash_before_it_fails_the_code(tmp_path, on_disk):
    """Two claims are checked separately, and the first one has to catch a quiet edit.

    The hash is a field *of the file it describes*, so an edited scenario that left the field alone
    would be believed by anything that only compared it to the code.
    """
    edited = json.loads(json.dumps(on_disk))
    edited["cases"][0]["utterance"] = "把一切都放到中间托盘。"
    path = str(tmp_path / "lh.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(edited, f, ensure_ascii=False)
    ok, msg = lh.check_lh_frozen(path)
    assert not ok and "does not hash to its own" in msg


def test_the_set_builds_to_the_same_bytes_twice(cases):
    """Nothing in the authoring reads a clock, a random draw or a model."""
    again = lh.build_long_horizon_set()
    assert [c.task_id for c in again] == [c.task_id for c in cases]
    for a, b in zip(cases, again):
        assert a.objects == b.objects and a.utterance == b.utterance
        assert a.eval_spec == b.eval_spec and a.event_specs() == b.event_specs()
        assert a.budgets == b.budgets


def test_the_shape_2_verdict_points_at_scripts_that_can_be_re_run(on_disk):
    """The set omits §8's hardest case, so the omission carries its evidence and its provenance.

    `SHAPE_2_MEASURED` is the difference between "we did not do that" and "we measured that it
    cannot be done, here is the number and here is the file that printed it". The scripts are checked
    to exist rather than to reproduce: they are dev probes, and a probe that stops running is a
    finding, not a failure of this test.
    """
    m = on_disk["spec8_shape_2_reachability"]
    assert m["verdict"] and m["consequence"]
    assert m["probes"], "a measurement with no script behind it is an assertion"
    for rel in m["probes"]:
        assert (lh_root / rel).exists(), f"{rel} is cited as the source of a number and is not there"
    executed = m["f6_that_layout_executed"]
    assert executed["victim_placements_attempted"] >= 1
    assert executed["victim_placements_completed"] == 0, \
        "the recorded verdict is 'unreachable'; a completion would move it"
    assert m["f3_real_sequence_over_9_such_layouts"]["completed_temp_move_but_restore_infeasible"] == 1


def test_two_disturbances_cannot_collapse_into_one_round(cases):
    """`lh_c6` is the only case with two events, and the second one is composed on purpose."""
    six = next(c for c in cases if c.task_id == "lh_c6_two_disruptions")
    assert len(six.events) == 2
    specs = six.event_specs()
    assert len({s["event_id"] for s in specs}) == 2
    assert len({s["action"] for s in specs}) == 2, "two events of one action are one disturbance"
    named = [s["entity_id"] for s in specs if s["entity_id"]]
    assert len(named) == 2 and len(set(named)) == 2, \
        f"both events would fire on the same body: {specs}"
    assert named[1] == six.targets[1], "the second event is armed on the second target"
    for other in [c for c in cases if c.task_id != six.task_id]:
        assert len(other.events) == 1
