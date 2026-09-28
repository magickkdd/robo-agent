"""SPEC-v0.2 §5.2/§5.8 contract tests for the P2-a planner (understanding, subgoals, revisions).

What is under test is the *口径*, not the rendering: which row a measurement entitles the plan to
create, when the version may move, which rows may never vanish, and what may be asserted without
an instrument. The four claims a reviewer is most likely to want relaxed — "a round passed so the
plan should be renumbered", "the tray looks full", "that clause probably meant the blue one",
"the subgoal is done because the agent said so" — each have a test that fails if the code starts
agreeing.

Two kinds of fixture, deliberately:

* **real geometry** (`PhysicsScene` + `PlacementPlanner` + `RuntimeVerifier`): every claim about
  what an instrument actually reports. A mock here would prove nothing about the instrument, so
  there is none, and `tests/unit/test_placement_planner.py` owns the numbers themselves.
* **a scripted instrument / scripted verifier**: every claim about what the plan *does* with an
  answer. Deciding "given the instrument refused every slot, does the blocker get a dependency?"
  needs a refusal that can be aimed at one chosen (object, region) pair; the geometry that
  produced a real refusal is checked one file away.
"""
from __future__ import annotations

import hashlib
from collections import namedtuple

import pytest

from embodied_agent.core.contracts import (
    Decision,
    DecisionExecute,
    EntityRef,
    GoalAssignment,
    GoalProgressItem,
    GoalSpec,
    NotSeen,
    OccupancyRecord,
    PredicateReport,
    PredicateVerdict,
    Source,
    TaskInput,
)
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.placement_planner import PlacementPlanner
from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.v02 import (
    EVENT_MODULE,
    DependencyKind,
    Status,
    Subgoal,
    ablation,
)
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.planning.subgoals import (
    Instruments,
    clear_predicate,
    derive,
    occupants,
    placement_predicate,
    row_key,
)
from embodied_agent.planning.task_planner import (
    HISTORY_PREFIX,
    TaskPlanner,
    evidence_for,
    key_of,
    observe_subgoal,
)
from embodied_agent.planning.understanding import understand, interpreter_sha256

# ------------------------------------------------------------------ fixtures ----

Check = namedtuple("Check", "ok candidate reasons")


class StubPlanner:
    """A placement instrument whose refusals a test can aim at one (object, region) pair.

    The unit under test is the graph `derive` builds *from* an instrument answer, so the answer
    is supplied here and the consequence is checked. No physical constant is restated: `slots`
    says how many candidate geometries a pair has, and a candidate fails re-check while any
    other body is recorded in its region — which is the whole interface `Instruments.feasible`
    uses (`generate` then `recheck`, reading `.ok`, `.candidate`, `.reasons`).
    """

    def __init__(self, *, slots: dict[tuple[str, str], int], inside: dict[str, list[str]]):
        self.slots = slots
        self.inside = inside

    def generate(self, target_id, entity_id, world, limit=None):
        n = self.slots.get((entity_id, target_id), 0)
        return [StubCandidate(f"cand_{entity_id}_{target_id}_{i}", target_id, entity_id, i)
                for i in range(n)]

    def recheck(self, cand, world):
        others = [o for o in self.inside.get(cand.target_id, []) if o != cand.entity_id]
        if others:
            return Check(False, cand, [f"slot is inside the hand's descent envelope of "
                                       f"{', '.join(others)}"])
        return Check(True, cand, [])


class StubCandidate(namedtuple("StubCandidate", "candidate_id target_id entity_id index")):
    """Just enough of a `PlacementCandidate` for `Instruments` to report on."""

    __slots__ = ()


class ScriptVerifier:
    """The verification channel, with the answers fixed.

    `evidence_for` asks a verifier exactly one question — `verify_placement` — so this is the
    whole interface, not a partial one. It exists because "a subgoal measured true is now
    measured false" needs two snapshots' answers, and no amount of pybullet buys evidence about
    what the plan does with them.
    """

    def __init__(self, table=None, default=PredicateVerdict.false, source=Source.sensor):
        self.table = dict(table or {})
        self.default = default
        self.source = source
        self.calls: list[str] = []

    def verify_placement(self, eid, target_id):
        pid = placement_predicate(eid, target_id)
        self.calls.append(pid)
        return PredicateReport(predicate_id=pid, description="scripted",
                               value=self.table.get(pid, self.default),
                               evidence_refs=[f"obs_script:{pid}"], source=self.source)

    def flip(self, eid, target_id, value):
        self.table[placement_predicate(eid, target_id)] = value


def goal_for(pairs) -> GoalSpec:
    return GoalSpec(assignments=[GoalAssignment(entity=EntityRef(entity_id=e), target_id=t)
                                 for e, t in pairs])


def progress_for(pairs, value=PredicateVerdict.false) -> list[GoalProgressItem]:
    return [GoalProgressItem(entity_id=e, target_id=t, value=value,
                             predicate_id=placement_predicate(e, t),
                             evidence_refs=["obs_0001"]) for e, t in pairs]


#: the frozen `dev_c1` sentence, taken from the task set rather than invented here: every
#: interpret-based test below therefore binds the same three objects the scene really holds.
DEV_UTTERANCE = find_case("dev_c1").utterance


def task_for(constraints=(), utterance="把方块放进托盘。") -> TaskInput:
    return TaskInput(task_id="t_p2", utterance=utterance,
                     declared_constraints=list(constraints))


def dev_task(constraints=()) -> TaskInput:
    return task_for(constraints, utterance=DEV_UTTERANCE)


def seated_in(world, entity_id, target_id, *, version=None, obs=None):
    """A snapshot that measures `entity_id` resting inside `target_id`.

    This is the state the frozen disturbance suite produces on purpose: an object shoved to the
    tray wall is *occupying volume* (`occupancy`, `IMPULSE_PIN_TO_WALL` in `evaluation/tasks.py`)
    while `placed:` still reads false, because a pinned pose is not a valid placement. The two
    facts come from different fields, and a plan that conflated them would call a blocker free
    space.
    """
    rec = OccupancyRecord(target_id=target_id, entity_id=entity_id, rest_xy=(0.66, -0.32),
                          footprint_half_xy=(0.021, 0.021), fully_inside=True)
    return world.model_copy(update={
        "state_version": version if version is not None else world.state_version + 1,
        "observation_ref": obs or f"obs_{world.state_version + 1:04d}",
        "occupancy": [o for o in world.occupancy if o.entity_id != entity_id] + [rec]})


def vacated(world, entity_id, target_id, *, version=None, obs=None):
    """A snapshot that still measures `entity_id` — just not as an occupant of `target_id`.

    The two fields that make an occupant (`occupancy`, `supported_by`) are both moved, because
    `occupants()` is the union and clearing only one of them is a snapshot that still says the
    body is resting there.
    """
    return world.model_copy(update={
        "state_version": version if version is not None else world.state_version + 1,
        "observation_ref": obs or f"obs_{world.state_version + 1:04d}",
        "entities": [e if e.entity_id != entity_id else
                     e.model_copy(update={"supported_by": "table"}) for e in world.entities],
        "occupancy": [o for o in world.occupancy if not (o.entity_id == entity_id and
                                                         o.target_id == target_id)]})


def unlocated(world, entity_id, *, view="front_left"):
    """The frame looked for `entity_id` in one view and did not find it."""
    return world.model_copy(update={"not_seen": list(world.not_seen) +
                                     [NotSeen(entity_id=entity_id, view=view)]})


@pytest.fixture(scope="module")
def real():
    """One real scene, snapshot, placement instrument and privileged verifier (`dev_c1`)."""
    case = find_case("dev_c1")
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        yield (case, scene, world, PlacementPlanner(scene, config=case.verify),
               RuntimeVerifier(world, case.verify))
    finally:
        scene.close()


@pytest.fixture(scope="module")
def ids(real):
    _, _, world, _, _ = real
    return sorted(e.entity_id for e in world.entities)


@pytest.fixture(scope="module")
def regions(real):
    _, _, world, _, _ = real
    return sorted(t.target_id for t in world.targets)


@pytest.fixture(scope="module")
def blocked_case():
    """The frozen protocol case whose only legal region already has an occupant."""
    case = find_case("pr_target_full")
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        yield case, world, PlacementPlanner(scene, config=case.verify)
    finally:
        scene.close()


def _row(plan, predicate_id):
    rows = [s for s in plan.subgoals if s.predicate_id == predicate_id]
    assert len(rows) == 1, f"{predicate_id}: {[r.kind for r in rows]}"
    return rows[0]


def _sub_of(derivation, predicate_id):
    rows = [s for s in derivation.subgoals if s.predicate_id == predicate_id]
    assert len(rows) == 1, f"{predicate_id}: {len(rows)} rows, expected exactly one"
    return rows[0]


# ------------------------------------------------- task understanding (§5.2) ----


def test_understanding_records_only_what_other_modules_already_saw(real, ids):
    case, _, world, planner, verifier = real
    task = dev_task(["objects not part of the task keep their place"])
    goal = interpret(task, world)
    progress = verifier.progress(goal)
    und = understand(task, goal, world, progress=progress, planner=planner)
    assert und.entities_mentioned == sorted({i.entity_id for i in progress})
    assert und.entities_mentioned == ids, "in this scene every clause bound to exactly one object"
    assert "no binding" not in und.normalized_goal
    assert und.ambiguous_parts == []
    assert und.constraints == task.declared_constraints + goal.prohibitions
    assert und.prompt_sha256 == interpreter_sha256()
    assert und.based_on_state_version == world.state_version
    assert und.model.startswith("rule:")
    assert und.wall_s == 0.0, "no model was called, so no latency may be claimed"


def test_understanding_reports_a_measured_blocker_in_the_instruments_own_words(blocked_case):
    """`implicit_requirements` is quoted evidence, not the plan's opinion about a tray."""
    _, bworld, bplanner = blocked_case
    pairs = [("obj_red_1", "tray_middle")]
    und = understand(task_for(), goal_for(pairs), bworld, progress=progress_for(pairs),
                     planner=bplanner)
    assert len(und.implicit_requirements) == 1
    assert "obj_blocker_1" in und.implicit_requirements[0]
    assert "descent envelope" in und.implicit_requirements[0]


def test_understanding_invents_no_obstacle_on_a_free_region(real, ids):
    case, _, world, planner, _ = real
    clean = understand(task_for(), goal_for([(ids[0], "tray_left")]), world,
                       progress=progress_for([(ids[0], "tray_left")]), planner=planner)
    assert clean.implicit_requirements == []


def test_understanding_keeps_an_unreadable_clause_out_of_the_bound_set(real, ids):
    """An attribute reference that resolves to nothing is a problem, not a guessed subgoal."""
    case, _, world, planner, _ = real
    goal = GoalSpec(assignments=[GoalAssignment(entity=EntityRef(attributes={"color": "pink"}),
                                                target_id="tray_left")],
                    ambiguous=True, clarification_request="no entity matches {'color': 'pink'}")
    und = understand(task_for(), goal, world, progress=[], planner=planner)
    assert und.entities_mentioned == []
    assert any("pink" in p for p in und.ambiguous_parts)
    assert und.model.endswith("(ambiguous)")


# ------------------------------------------------------ subgoal authoring (§5.2) ----


def test_rows_are_keyed_to_the_verifiers_own_predicate(real, ids):
    """`placed:<entity>:<target>` is one question, asked the same way by two modules."""
    case, _, world, planner, verifier = real
    pairs = list(zip(ids, ["tray_left", "tray_middle", "tray_right"]))
    d = derive(goal_for(pairs), world, task_for(), planner=planner, progress=progress_for(pairs))
    assert {s.predicate_id for s in d.subgoals if s.kind == "achieve"} == \
        {placement_predicate(e, t) for e, t in pairs}
    assert verifier.verify_placement(ids[0], "tray_left").predicate_id == \
        placement_predicate(ids[0], "tray_left")


def test_the_plan_is_never_shorter_than_the_task(real, ids):
    """A row comes from the *assignment*, not from whoever happened to measure it.

    `progress` is an input, and an input that is empty (a channel that binders refused, a unit
    call with no verifier) must not be allowed to author a plan that quietly omits an
    obligation. The binding is resolved with `verify.bind_attributes`, the same attribute-unique
    rule the verifier uses, so the two modules cannot disagree about which object "the green
    cylinder" is.
    """
    case, _, world, planner, _ = real
    goal = GoalSpec(assignments=[
        GoalAssignment(entity=EntityRef(attributes={"color": "green", "shape": "cylinder"}),
                       target_id="tray_left")])
    d = derive(goal, world, task_for(), planner=planner, progress=[])
    assert [s.predicate_id for s in d.subgoals if s.kind == "achieve"] == \
        [placement_predicate("obj_green_1", "tray_left")]
    assert any("was never measured" in p for p in d.subgoals[0].preconditions)
    assert d.notes == []


def test_an_ambiguous_reference_authors_no_row_and_says_what_it_refused(real, ids):
    """The v0.1 defect this replaces: guessing the id mapping from co-occurrence.

    Two objects that are both pink is a scene state, not a parse error, so it is fabricated here
    by editing the snapshot's attributes — and the answer under test is that the plan reports
    the missing obligation instead of picking the alphabetically earlier one.
    """
    case, _, world, planner, _ = real
    repainted = [e.model_copy(update={"attributes": {"color": "pink",
                                                     "shape": e.attributes["shape"]}})
                 for e in world.entities[:2]]
    twins = world.model_copy(update={"entities": repainted + list(world.entities[2:])})
    goal = GoalSpec(assignments=[
        GoalAssignment(entity=EntityRef(attributes={"color": "pink"}), target_id="tray_left")])
    d = derive(goal, twins, task_for(), planner=planner, progress=[])
    assert [s for s in d.subgoals if s.kind == "achieve"] == []
    assert any("binds to 2 entities" in n and "never guessed into one" in n for n in d.notes)

    none = derive(GoalSpec(assignments=[
        GoalAssignment(entity=EntityRef(attributes={"color": "mauve"}),
                       target_id="tray_left")]), twins, task_for(), planner=planner, progress=[])
    assert any("binds to 0 entities" in n for n in none.notes)


def test_a_named_object_that_is_not_in_the_snapshot_still_owes_a_row(real, ids):
    """Public text names an obligation the current frame cannot measure: keep the debt."""
    case, _, world, planner, _ = real
    d = derive(goal_for([("obj_ghost_1", "tray_left")]), world, task_for(), planner=planner,
               progress=[])
    sub = [s for s in d.subgoals if s.target_entity_ids == ["obj_ghost_1"]]
    assert len(sub) == 1 and sub[0].satisfied.value == "unknown"
    assert any("not in this snapshot" in p for p in sub[0].preconditions)


def test_a_maintain_row_arrives_with_no_evidence_and_stays_open(real, ids):
    case, _, world, planner, verifier = real
    task = dev_task(["no stacking"])
    goal = interpret(task, world)
    d = derive(goal, world, task, planner=planner, progress=verifier.progress(goal))
    row = [s for s in d.subgoals if s.kind == "maintain"]
    assert len(row) == 1 and row[0].predicate_id is None
    assert row_key(row[0]).startswith("text:")
    assert row[0].satisfied.value == "unknown"


def test_row_key_is_a_hash_not_a_process_salt(real, ids):
    """`hash()` would make one snapshot author two different version histories in two runs."""
    case, _, world, planner, _ = real
    statement = "objects not part of the task keep their place"
    row = [s for s in derive(interpret(dev_task([statement]), world), world,
                             dev_task([statement]), planner=planner, progress=[]).subgoals
           if s.kind == "maintain"][0]
    assert row_key(row) == "text:" + hashlib.sha256(statement.encode()).hexdigest()[:10]
    assert row_key(row) != f"text:{hash(statement) & 0xffffff:06x}"


def test_no_instrument_means_no_dependency_claimed(real, ids):
    """`planner=None` is a real state: the graph reports that it has no blocking evidence."""
    case, _, world, _, _ = real
    pairs = [(e, "tray_left") for e in ids]
    d = derive(goal_for(pairs), world, task_for(), planner=None, progress=progress_for(pairs))
    assert d.dependencies == []
    assert any("no placement instrument" in n for n in d.notes)


def test_a_malformed_goal_authors_no_plan(real, ids):
    case, _, world, planner, _ = real
    goal = GoalSpec(assignments=[], ambiguous=True, clarification_request="nothing to bind")
    d = derive(goal, world, task_for(), planner=planner, progress=[])
    assert d.subgoals == [] and d.dependencies == []
    assert "not well-formed" in d.notes[0]


def test_derivations_are_deterministic_in_one_snapshot(real, ids):
    """Two runs over one world author one graph: `notes` and rows must not accumulate."""
    case, _, world, planner, _ = real
    task = dev_task(["no stacking"])
    pairs = [(e, "tray_left") for e in ids]
    a = derive(goal_for(pairs), world, task, planner=planner, progress=progress_for(pairs))
    b = derive(goal_for(pairs), world, task, planner=planner, progress=progress_for(pairs))
    assert [s.predicate_id for s in a.subgoals] == [s.predicate_id for s in b.subgoals]
    assert a.notes == b.notes


# -------------------------------------------------- dependency shapes (§5.2) ----


def test_shape1_a_blocker_that_already_owes_another_region_needs_no_new_row(real, ids):
    """The work already exists in the plan; only the edge is new."""
    _, _, world, _, _ = real
    a, b = ids[0], ids[1]
    planner = StubPlanner(slots={(a, "tray_left"): 1, (b, "tray_middle"): 1},
                          inside={"tray_left": [b]})
    pairs = [(a, "tray_left"), (b, "tray_middle")]
    d = derive(goal_for(pairs), seated_in(world, b, "tray_left"), task_for(), planner=planner,
               progress=progress_for(pairs))
    assert len([s for s in d.subgoals if s.kind == "achieve"]) == 2
    dep, = d.dependencies
    assert dep.kind is DependencyKind.state
    assert dep.from_subgoal_id == _sub_of(d, placement_predicate(b, "tray_middle")).subgoal_id
    assert dep.on_subgoal_id == _sub_of(d, placement_predicate(a, "tray_left")).subgoal_id
    assert _sub_of(d, placement_predicate(a, "tray_left")).depends_on == [], \
        "the edge is a record of the constraint, not a second queue the plan enforces"


def test_shape2_a_blocker_owed_to_the_same_region_becomes_a_restore_debt(real, ids, regions):
    """The obligation a flat progress list cannot express: leave, let this one in, come back."""
    _, _, world, _, _ = real
    a, b, c = ids[0], ids[1], ids[2]
    left, middle, right = regions[0], regions[1], regions[2]
    planner = StubPlanner(slots={(a, left): 1, (b, left): 1, (b, middle): 1, (c, right): 1},
                          inside={left: [b]})
    pairs = [(a, left), (b, left), (c, right)]
    d = derive(goal_for(pairs), seated_in(world, b, left), task_for(), planner=planner,
               progress=progress_for(pairs))

    back = _sub_of(d, placement_predicate(b, left))
    away = _sub_of(d, placement_predicate(b, middle))
    blocked = _sub_of(d, placement_predicate(a, left))
    assert back.kind == "recover", "one predicate may not carry two rows: the debt is a relabel"
    assert "must come back" in back.statement
    assert blocked.depends_on == [away.subgoal_id]
    recovery = [dep for dep in d.dependencies if dep.kind is DependencyKind.recovery]
    assert len(recovery) == 1
    assert (recovery[0].from_subgoal_id, recovery[0].on_subgoal_id) == \
        (back.subgoal_id, blocked.subgoal_id)
    assert away.preconditions and "temporarily" in away.statement


def test_shape2_offers_no_receiver_that_the_task_still_needs(real, ids, regions):
    """A temp move into a region the task still owes would trade one blocker for another."""
    _, _, world, _, _ = real
    a, b, c = ids[0], ids[1], ids[2]
    left, middle, right = regions[0], regions[1], regions[2]
    # b could sit in `middle`, but c is owed there, so the only receiver is excluded
    planner = StubPlanner(slots={(a, left): 1, (b, left): 1, (b, middle): 1, (c, middle): 2},
                          inside={left: [b]})
    pairs = [(a, left), (b, left), (c, middle)]
    d = derive(goal_for(pairs), seated_in(world, b, left), task_for(), planner=planner,
               progress=progress_for(pairs))
    assert _sub_of(d, placement_predicate(b, left)).kind == "achieve"
    assert [dep.kind for dep in d.dependencies] != [DependencyKind.recovery], \
        "nothing may be promised to come back if nothing was asked to leave"
    assert all(dep.kind is not DependencyKind.state for dep in d.dependencies)
    assert any("unanswerable" in n for n in d.notes), \
        "an obligation nothing can satisfy is reported, never deleted"
    assert any("no region can receive it" in p
               for p in _sub_of(d, placement_predicate(a, left)).preconditions)


def test_shape3_a_stray_occupant_owes_a_clear_row_answered_by_the_negative(blocked_case):
    """Not part of the task, still in the way: `clear:` is a goal the agent can satisfy."""
    _, bworld, bplanner = blocked_case
    pairs = [("obj_red_1", "tray_middle")]
    d = derive(goal_for(pairs), bworld, task_for(), planner=bplanner, progress=progress_for(pairs))
    row = _sub_of(d, clear_predicate("obj_blocker_1", "tray_middle"))
    assert row.target_region_id == "tray_middle"
    dep, = d.dependencies
    assert dep.kind is DependencyKind.state
    assert _sub_of(d, placement_predicate("obj_red_1", "tray_middle")).depends_on == \
        [row.subgoal_id]


def test_a_clear_row_is_answered_from_occupancy_never_from_negated_placement(blocked_case):
    """§5.8 layer 2: the negative of a measurement, never the absence of one — and never the
    negation of a *different* measurement.

    Measured before this test existed: `clear:` was answered as `not placed:`, and the two
    predicates disagree exactly where it matters. `placed:` is false for an object shoved against
    the tray wall (`IMPULSE_PIN_TO_WALL`) as well as for one that left, so negating it reported a
    region as clear while its descent envelope was still occupied — and then marked the blocker's
    prerequisite satisfied.
    """
    _, bworld, bplanner = blocked_case
    eid, tid = "obj_blocker_1", "tray_middle"
    pairs = [("obj_red_1", tid)]
    d = derive(goal_for(pairs), bworld, task_for(), planner=bplanner, progress=progress_for(pairs))
    row = _sub_of(d, clear_predicate(eid, tid))

    pinned = seated_in(bworld, eid, tid)
    verdict, refs, _un = evidence_for(row, ScriptVerifier(
        {placement_predicate(eid, tid): PredicateVerdict.false}), pinned)
    assert verdict is PredicateVerdict.false, "an occupant that is not *placed* has not cleared"
    assert refs == [pinned.observation_ref], "the evidence is the frame that read the occupancy"

    gone = vacated(bworld, eid, tid)
    verdict, refs, _un = evidence_for(row, ScriptVerifier(
        {placement_predicate(eid, tid): PredicateVerdict.true}), gone)
    assert verdict is PredicateVerdict.true, "the region that stopped holding it is measurably clear"
    assert refs == [gone.observation_ref]

    no_pose = gone.model_copy(update={"entities": [
        e.model_copy(update={"pose": None}) if e.entity_id == eid else e
        for e in gone.entities]})
    assert evidence_for(row, ScriptVerifier(), no_pose) is None, \
        "a body with no measured position has not been shown to have moved"
    assert evidence_for(row, ScriptVerifier(), unlocated(gone, eid)) is None, \
        "and neither has one this frame only knows the name of"


def test_capacity_edges_appear_only_when_the_region_is_the_constraint(real, ids, regions):
    """`resource` is the one kind that measures the *tray*, so it needs a slot count."""
    _, _, world, _, _ = real
    a, b, c = ids[0], ids[1], ids[2]
    left, middle = regions[0], regions[1]
    pairs = [(a, left), (b, left), (c, middle)]
    tight = StubPlanner(slots={(a, left): 1, (b, left): 1, (c, middle): 3}, inside={})
    d = derive(goal_for(pairs), world, task_for(), planner=tight, progress=progress_for(pairs))
    edges = [dep for dep in d.dependencies if dep.kind is DependencyKind.resource]
    assert len(edges) == 1, "one edge per contested pair, earlier object to later"
    assert (edges[0].from_subgoal_id, edges[0].on_subgoal_id) == \
        (_sub_of(d, placement_predicate(a, left)).subgoal_id,
         _sub_of(d, placement_predicate(b, left)).subgoal_id)
    assert "1 feasible slot" in edges[0].note
    assert _sub_of(d, placement_predicate(a, left)).depends_on == [], \
        "a capacity edge names a constraint, not an order the plan enforces"

    roomy = StubPlanner(slots={(a, left): 2, (b, left): 2}, inside={})
    loose = derive(goal_for(pairs[:2]), world, task_for(), planner=roomy,
                   progress=progress_for(pairs[:2]))
    assert loose.dependencies == []


def test_capacity_counts_exclude_a_row_that_wants_the_region_emptied(real, ids, regions):
    """A `clear:` row is not an object owed to the region; counting it fabricates competition."""
    _, _, world, _, _ = real
    a, b = ids[0], ids[1]
    left, right, middle = regions[0], regions[1], regions[2]
    planner = StubPlanner(slots={(a, left): 1, (b, right): 1}, inside={middle: [b]})
    pairs = [(a, left), (b, right)]
    d = derive(goal_for(pairs), seated_in(world, b, middle), task_for(), planner=planner,
               progress=progress_for(pairs))
    assert [dep.kind for dep in d.dependencies] == [] or \
        not [dep for dep in d.dependencies if dep.kind is DependencyKind.resource]


def test_occupants_uses_the_rule_the_descent_envelope_uses(real, ids, regions):
    """`occupants()` and `PlacementPlanner.recheck` must disagree about nothing."""
    case, _, world, planner, _ = real
    for target in regions:
        counted = set(occupants(world, target))
        from_records = ({o.entity_id for o in world.occupancy if o.target_id == target} |
                        {e.entity_id for e in world.entities if e.supported_by == target})
        assert counted == from_records
        assert counted <= {e.entity_id for e in world.entities}


# ------------------------------------------------------- revisions (§5.2, §11) ----


def test_an_unchanged_world_never_bumps_the_version(real, ids):
    """The denominator of "replanning effectiveness" must not count rounds.

    Measured before this test existed: three refreshes over one snapshot produced `plan v2`,
    because a `maintain` row keyed itself by the process-salted `hash()` of its own statement
    and so re-appeared as new work every round.
    """
    case, _, world, planner, verifier = real
    task = dev_task(["objects not part of the task keep their place"])
    goal = interpret(task, world)
    tp = TaskPlanner()
    plan = tp.initial(task, goal, world, verifier=verifier, planner=planner,
                      progress=verifier.progress(goal))
    for n in range(4):
        r = tp.refresh(plan, world, verifier=verifier, planner=planner, task=task, goal=goal,
                       progress=verifier.progress(goal))
        assert not r.version_bumped, f"round {n}: {r.added} {r.dropped} {r.relabelled} " \
                                     f"{r.rewired} {r.reopened}"
        plan = r.plan
    assert plan.version == 1
    assert len(plan.revisions) == 1 and plan.revisions[0].trigger == "initial"
    quiet = tp.refresh(plan, world, verifier=verifier, planner=planner)
    assert not quiet.version_bumped and quiet.plan.version == 1


def test_a_new_obligation_bumps_once_and_the_reason_names_it(real, ids, regions):
    case, _, world, _, _ = real
    a, b = ids[0], ids[1]
    left = regions[0]
    tp, ver = TaskPlanner(), ScriptVerifier()
    task = task_for()
    goal = goal_for([(a, left)])
    stub = StubPlanner(slots={(a, left): 1, (b, left): 1, (b, regions[1]): 1},
                       inside={left: [b]})
    world2 = seated_in(world, b, left)
    plan = tp.initial(task, goal, world, verifier=ver, planner=StubPlanner(slots={
        (a, left): 2, (b, left): 1}, inside={}), progress=progress_for([(a, left)]))
    assert plan.version == 1
    r = tp.refresh(plan, world2, verifier=ver, planner=stub, task=task, goal=goal,
                   progress=progress_for([(a, left)]))
    assert r.version_bumped and r.plan.version == 2
    assert clear_predicate(b, left) in r.added
    rev = r.plan.revisions[-1]
    assert rev.trigger == "new_observation"
    assert f"added:{clear_predicate(b, left)}" in rev.rationale
    assert rev.changed_subgoal_ids == sorted({key_of(r.plan.subgoals, clear_predicate(b, left)),
                                              key_of(r.plan.subgoals,
                                                     placement_predicate(a, left))}), \
        "adding a prerequisite changes both ends of the new edge, not just the row that appeared"
    again = tp.refresh(r.plan, world2, verifier=ver, planner=stub, task=task, goal=goal,
                       progress=progress_for([(a, left)]))
    assert not again.version_bumped, "the same measurement read twice is not a new revision"


def test_completion_is_not_a_revision_but_a_return_is(real, ids, regions):
    """`done` refreshes a row in place; a true->false flip *adds* work and moves the version."""
    case, _, world, planner, _ = real
    a, left = ids[0], regions[0]
    tp, ver = TaskPlanner(), ScriptVerifier()
    task, goal = task_for(), goal_for([(a, left)])
    plan = tp.initial(task, goal, world, verifier=ver, planner=planner,
                      progress=progress_for([(a, left)]))
    open_r = tp.refresh(plan, world, verifier=ver, planner=planner, task=task, goal=goal,
                        progress=progress_for([(a, left)]))
    assert not open_r.version_bumped, "reading a false twice is not a revision"
    ver.flip(a, left, PredicateVerdict.true)
    done_r = tp.refresh(open_r.plan, world, verifier=ver, planner=planner, task=task, goal=goal,
                        progress=progress_for([(a, left)], PredicateVerdict.true))
    assert not done_r.version_bumped, "satisfying a subgoal is progress, not a replan"
    key = key_of(done_r.plan.subgoals, placement_predicate(a, left))
    row = done_r.plan.subgoal(key)
    assert row.status is Status.done and row.satisfied.value is True
    assert done_r.plan.open_obligation_count == 0

    ver.flip(a, left, PredicateVerdict.false)
    back_r = tp.refresh(done_r.plan, world, verifier=ver, planner=planner, task=task, goal=goal,
                        progress=progress_for([(a, left)]))
    assert back_r.version_bumped and back_r.reopened == [placement_predicate(a, left)]
    reopened = back_r.plan.subgoal(key)
    assert reopened.status is Status.pending and reopened.satisfied.value is False
    assert back_r.plan.open_obligation_count == 1
    assert any(p.startswith(HISTORY_PREFIX) and "obligation returned" in p
               for p in reopened.preconditions)
    # and a history line is not snapshot data: a later derivation may not erase it
    later = tp.refresh(back_r.plan, world, verifier=ver, planner=planner, task=task, goal=goal,
                       progress=progress_for([(a, left)]))
    assert any(p.startswith(HISTORY_PREFIX) for p in later.plan.subgoal(key).preconditions)


def test_rows_survive_being_inconvenient(real, ids, regions):
    """Rule 3: a `done` row and a persistent row outlive a derivation that forgot them."""
    case, _, world, planner, _ = real
    a, b = ids[0], ids[1]
    left, middle = regions[0], regions[1]
    tp, ver = TaskPlanner(), ScriptVerifier(
        {placement_predicate(a, left): PredicateVerdict.true})
    task = dev_task(["no stacking"])
    pairs = [(a, left), (b, middle)]
    plan = tp.initial(task, goal_for(pairs), world, verifier=ver, planner=planner,
                      progress=progress_for(pairs))
    plan = tp.refresh(plan, world, verifier=ver, planner=planner, task=task, goal=goal_for(pairs),
                      progress=progress_for(pairs)).plan
    assert _row(plan, placement_predicate(a, left)).status is Status.done

    narrowed = tp.refresh(plan, world, verifier=ver, planner=planner, task=task,
                          goal=goal_for([(b, middle)]), progress=progress_for([(b, middle)]))
    assert placement_predicate(a, left) in {s.predicate_id for s in narrowed.plan.subgoals}
    assert [s.kind for s in narrowed.plan.subgoals].count("maintain") == 1, \
        "the constraint row is keyed by its text and must not multiply either"
    assert not narrowed.dropped, narrowed.dropped


def test_a_completion_that_arrives_on_the_retirement_round_keeps_its_evidence(real, ids,
                                                                              regions):
    """`done` is a fact about the world, so it may not depend on which round read it last.

    A row the derivation no longer mentions is retired — unless it is `done`, unless it is
    persistent, and unless *this* snapshot measures it true. Without the third reading, an
    obligation satisfied on the same round its clause was withdrawn would leave no evidence
    behind, and a later re-ask would be counted as new work rather than as rework (§11).
    """
    case, _, world, _, _ = real
    a, b = ids[0], ids[1]
    left, right = regions[0], regions[1]
    pairs = [(a, left), (b, right)]
    stub = StubPlanner(slots={(a, left): 1, (b, right): 1}, inside={})
    tp = TaskPlanner()

    ver = ScriptVerifier({placement_predicate(b, right): PredicateVerdict.true})
    plan = tp.initial(task_for(), goal_for(pairs), world, verifier=ver, planner=stub,
                      progress=progress_for(pairs))
    r = tp.refresh(plan, world, verifier=ver, planner=stub, task=task_for(),
                   goal=goal_for([(a, left)]), progress=progress_for([(a, left)]))
    row = _row(r.plan, placement_predicate(b, right))
    assert row.status is Status.done and row.completion_evidence_refs, \
        "the measurement that arrived on the retirement round is the row's last will"
    assert key_of(r.plan.subgoals, placement_predicate(b, right)) not in r.dropped
    assert placement_predicate(b, right) not in {s.predicate_id for s in tp.obligations(r.plan)}

    blind = ScriptVerifier()
    open_plan = tp.initial(task_for(), goal_for(pairs), world, verifier=blind, planner=stub,
                           progress=progress_for(pairs))
    retired = tp.refresh(open_plan, world, verifier=blind, planner=stub, task=task_for(),
                         goal=goal_for([(a, left)]), progress=progress_for([(a, left)]))
    assert placement_predicate(b, right) in retired.dropped, \
        "and a row nothing measured is retired, not carried forward as a phantom debt"


def test_a_recover_row_is_never_downgraded_by_a_snapshot_that_no_longer_sees_the_block(
        real, ids, regions):
    case, _, world, _, _ = real
    a, b, c = ids[0], ids[1], ids[2]
    left, middle, right = regions
    tp = TaskPlanner()
    task = task_for()
    pairs = [(a, left), (b, left), (c, right)]
    blocked = StubPlanner(slots={(a, left): 1, (b, left): 1, (b, middle): 1, (c, right): 1},
                          inside={left: [b]})
    free = StubPlanner(slots={(a, left): 2, (b, left): 2, (c, right): 2}, inside={})
    ver = ScriptVerifier()
    world2 = seated_in(world, b, left)
    plan = tp.initial(task, goal_for(pairs), world2, verifier=ver, planner=blocked,
                      progress=progress_for(pairs))
    plan = tp.refresh(plan, world2, verifier=ver, planner=blocked, task=task,
                      goal=goal_for(pairs), progress=progress_for(pairs)).plan
    key = placement_predicate(b, left)
    assert _row(plan, key).kind == "recover"
    settled = tp.refresh(plan, world2, verifier=ver, planner=free, task=task,
                         goal=goal_for(pairs), progress=progress_for(pairs))
    assert _row(settled.plan, key).kind == "recover", \
        "the debt was created by a measurement; a later snapshot cancels nothing"
    assert "must come back" in _row(settled.plan, key).statement
    assert not settled.relabelled


def test_a_retired_prerequisite_is_pruned_and_reported_not_left_dangling(real, ids, regions):
    """`TaskPlan` forbids an edge onto a row it does not carry; neither may the planner.

    The case is real because `model_copy` does not re-run validators: a revision can add a row
    conditioned on a prerequisite that a later clause withdrawal removes. The honest repair is to
    cut the edge *and say so* (`pruned-prerequisite:`), because losing a prerequisite is a change
    to the work; leaving the id dangling would be a plan that cannot be re-read, and cutting it
    silently would be a plan that forgot why it was blocked.
    """
    case, _, world, _, _ = real
    a, b = ids[0], ids[1]
    left, right = regions[0], regions[1]
    tp = TaskPlanner()
    task = task_for()
    pairs = [(a, left), (b, right)]
    stub = StubPlanner(slots={(a, left): 1, (b, right): 1}, inside={left: [b]})
    ver = ScriptVerifier()
    world2 = seated_in(world, b, left)
    plan = tp.initial(task, goal_for(pairs), world2, verifier=ver, planner=stub,
                      progress=progress_for(pairs))
    prereq = _row(plan, placement_predicate(b, right))
    blocked = _row(plan, placement_predicate(a, left))
    assert [(d.from_subgoal_id, d.on_subgoal_id) for d in plan.dependencies
            if d.kind is DependencyKind.state] == [(prereq.subgoal_id, blocked.subgoal_id)], \
        "shape 1: the blocker's own goal is the constraint, with no new row invented"

    # A revision adds work that waits on that row, and the same round withdraws the clause the
    # row came from.
    wait = Subgoal(statement=f"{b} must be seated in {right} before anything else is decided "
                             f"about it", kind="control", status=Status.pending,
                   target_entity_ids=[b], target_region_id=right,
                   depends_on=[prereq.subgoal_id],
                   preconditions=["added by a revision, not by the derivation"])
    r = tp.refresh(plan, world2, verifier=ver, planner=stub, task=task,
                   goal=goal_for([(a, left)]), progress=progress_for([(a, left)]),
                   new_obligations=[wait])
    assert placement_predicate(b, right) in r.dropped
    held = [s for s in r.plan.subgoals if s.kind == "control"]
    assert len(held) == 1 and held[0].depends_on == [], \
        "the row survives, the edge to a retired row does not"
    live = {s.subgoal_id for s in r.plan.subgoals}
    assert all(set(s.depends_on) <= live for s in r.plan.subgoals)
    assert [d for d in r.plan.dependencies if prereq.subgoal_id in
            (d.from_subgoal_id, d.on_subgoal_id)] == [], "the graph and the rows agree"
    assert r.version_bumped
    assert row_key(wait) in r.rewired
    assert f"pruned-prerequisite:{row_key(wait)}" in r.events
    rev = r.plan.revisions[-1]
    groups = dict(g.split(":", 1) for g in rev.rationale.split("; "))
    assert row_key(wait) in groups["rewired"].split(","), \
        "the version jump says which row lost its prerequisite"
    assert r.plan.model_validate(r.plan.model_dump()).version == r.plan.version, \
        "the revised record must still be a plan"


# ---------------------------------------------- satisfaction is measured (§5.8) ----


def test_a_row_cannot_be_done_on_better_evidence_than_the_instrument_gives(real, ids, regions):
    case, _, world, planner, _ = real
    a, left = ids[0], regions[0]
    tp = TaskPlanner()
    task, goal = task_for(), goal_for([(a, left)])
    blind = ScriptVerifier(default=PredicateVerdict.unknown)
    plan = tp.initial(task, goal, world, verifier=blind, planner=planner,
                      progress=progress_for([(a, left)]))
    r = tp.refresh(plan, world, verifier=blind, planner=planner, task=task, goal=goal,
                   progress=progress_for([(a, left)]))
    row = _row(r.plan, placement_predicate(a, left))
    assert row.status is Status.pending and row.satisfied.value == "unknown"
    assert r.plan.open_obligation_count == 1
    assert not any(p.startswith(HISTORY_PREFIX) for p in row.preconditions)


def test_evidence_is_bound_to_the_channel_that_read_it(real, ids, regions):
    """Which verifier is passed decides what counts as proof — for the row and for the reader."""
    case, _, world, planner, verifier = real
    a, left = ids[0], regions[0]
    plan = TaskPlanner().initial(task_for(), goal_for([(a, left)]), world, verifier=verifier,
                                 planner=planner, progress=progress_for([(a, left)]))
    sub = _row(plan, placement_predicate(a, left))
    privileged = evidence_for(sub, verifier, world)
    sensing = evidence_for(sub, ScriptVerifier(
        {placement_predicate(a, left): PredicateVerdict.true}), world)
    assert privileged[0] is PredicateVerdict.false and sensing[0] is PredicateVerdict.true
    assert privileged[1] == [world.observation_ref], "a row cites the frame it was read from"
    assert sensing[1] == [f"obs_script:{placement_predicate(a, left)}"]
    assert evidence_for(sub.model_copy(update={"target_entity_ids": ["obj_ghost_1"]}),
                        verifier, world) is None, "an unlocated object answers nothing"
    assert evidence_for(sub.model_copy(update={"predicate_id": None}), verifier, world) is None


# ------------------------------------------------------- the plan as an offer ----


def test_ready_is_ordered_by_row_identity_and_not_by_a_preference(real, ids):
    """`ready()` is a set, published in an order a reader can reproduce — never a ranking.

    Two properties, and the second is the one P2-c bought: the order is `row_key`, not
    `subgoal_id`, because the id carries a random `uuid4` suffix (`core/contracts.py:new_id`). An
    id-ordered offer visits the same rows in a different sequence in the next process, which turns
    every downstream choice that takes "the first ready row" — a rule policy, a test, a second
    arm — into a measurement of the draw rather than of the plan.
    """
    case, _, world, planner, verifier = real
    task = dev_task(["objects not part of the task keep their place"])
    goal = interpret(task, world)
    tp = TaskPlanner()
    plan = tp.initial(task, goal, world, verifier=verifier, planner=planner,
                      progress=verifier.progress(goal))
    first = [row_key(s) for s in tp.ready(plan)]
    assert first == sorted(first)
    assert all(s.kind != "maintain" for s in tp.ready(plan)), \
        "no skill closes a maintain row, so it may never be offered as startable work"
    again = tp.refresh(plan, world, verifier=verifier, planner=planner, task=task, goal=goal,
                       progress=verifier.progress(goal)).plan
    assert [row_key(s) for s in tp.ready(again)] == first, \
        "the offer must not reorder itself between rounds, or the plan implies a queue"
    redrawn = tp.initial(task, goal, world, verifier=verifier, planner=planner,
                         progress=verifier.progress(goal))
    assert [row_key(s) for s in tp.ready(redrawn)] == first, \
        "a fresh authoring draws new subgoal ids; the offer it publishes may not move with them"


def test_ready_respects_prerequisites_but_obligations_do_not_care(blocked_case):
    _, bworld, bplanner = blocked_case
    task, goal = task_for(), goal_for([("obj_red_1", "tray_middle")])
    tp = TaskPlanner()
    pairs = [("obj_red_1", "tray_middle")]
    plan = tp.initial(task, goal, bworld, verifier=ScriptVerifier(), planner=bplanner,
                      progress=progress_for(pairs))
    plan = tp.refresh(plan, bworld, verifier=ScriptVerifier(), planner=bplanner, task=task,
                      goal=goal, progress=progress_for(pairs)).plan
    assert [s.predicate_id for s in tp.ready(plan)] == \
        [clear_predicate("obj_blocker_1", "tray_middle")]
    owed = {s.predicate_id for s in tp.obligations(plan)}
    assert placement_predicate("obj_red_1", "tray_middle") in owed, \
        "blocked work is still owed work"
    assert "blocked (owed, nothing can start it yet)" in plan.summary


def test_an_obligation_no_instrument_can_close_keeps_the_count_above_zero(real, ids, regions):
    """The state a `success` exit must not be able to hide (§11 obligation_check)."""
    case, _, world, planner, verifier = real
    task = dev_task(["objects not part of the task keep their place"])
    goal = interpret(task, world)
    tp = TaskPlanner()
    plan = tp.initial(task, goal, world, verifier=verifier, planner=planner,
                      progress=verifier.progress(goal))
    ver = ScriptVerifier(default=PredicateVerdict.true)
    finished = tp.refresh(plan, world, verifier=ver, planner=planner, task=task, goal=goal,
                          progress=verifier.progress(goal))
    assert [s.status for s in finished.plan.subgoals if s.predicate_id] == \
        [Status.done] * len(ids)
    assert finished.plan.open_obligation_count == 1
    assert [s.kind for s in tp.obligations(finished.plan)] == ["maintain"]


def test_summary_sheds_detail_but_never_the_debt_count(real, ids):
    case, _, world, planner, verifier = real
    task = dev_task(["objects not part of the task keep their place"])
    goal = interpret(task, world)
    tp = TaskPlanner()
    plan = tp.initial(task, goal, world, verifier=verifier, planner=planner,
                      progress=verifier.progress(goal))
    full = tp.summary(plan, budget=4000)
    assert "ready (a set, not an order)" in full and "  maintain:" in full
    assert len(full) <= 4000
    tight = tp.summary(plan, budget=170)
    assert tight.startswith("plan v1")
    assert f"{plan.open_obligation_count} owed" in tight, "the count is not a detail"
    assert "  maintain:" in tight, "a debt line is the first thing kept, not the first cut"
    assert "ready" not in tight
    assert len(tp.summary(plan, budget=1)) <= 1, "a budget cannot raise, only shave, the text"


def test_a_recover_debt_is_the_last_line_a_budget_may_cut(real, ids):
    """§5.3: 恢复义务 must survive being inconvenient, and the summary is where that is decided.

    The P2-e batches made this the sharpest edge of the two text channels: `lh_c2`'s temporary move
    authorizes a `recover` row for the return leg, and the same bounded text is what the decision
    model reads, so a budget that shed the restore line would hide the only debt the scenario was
    built to test. The invariant is not "restore is first" — it is that no budget may keep a later
    line while dropping this one, which holds because lines are appended in priority order against
    a growing length, so if the restore line does not fit, nothing after it can either.
    """
    case, _, world, planner, verifier = real
    task = dev_task(["objects not part of the task keep their place"])
    goal = interpret(task, world)
    tp = TaskPlanner()
    plan = tp.initial(task, goal, world, verifier=verifier, planner=planner,
                      progress=verifier.progress(goal))
    rows = list(plan.subgoals)
    debt = next(s for s in rows if s.kind == "achieve" and s.satisfied.value is not True)
    rows[rows.index(debt)] = debt.model_copy(update={"kind": "recover"})
    plan = plan.model_copy(update={"subgoals": rows})
    owed = tp.obligations(plan)
    assert [s.kind for s in owed if s.kind == "recover"] and len(owed) == \
        plan.open_obligation_count, "the ledger and the count the report reads are one call"
    full = tp.summary(plan, budget=4000)
    assert "  restore: " in full and debt.statement in full, \
        "the line names the debt's own statement, not just that a debt exists"
    later = ("  maintain:", "  look-for-evidence:", "  violated:", "  stalled", "  blocked",
             "  ready", "  unmeasured:", "  last revision")
    for budget in range(1, len(full) + 1):
        text = tp.summary(plan, budget=budget)
        assert len(text) <= budget, budget
        if any(tag in text for tag in later):
            assert "  restore:" in text, f"budget {budget} kept a detail and cut the debt"
    head = full.splitlines()[0]
    assert f"{len(owed)} owed" in head, "the count lives on the line that is never cut"
    for budget in range(len(head), len(full) + 1):
        assert tp.summary(plan, budget=budget).startswith(head), \
            f"budget {budget} cut the head line, which is what a reader adds up"
    assert tp.summary(plan, budget=len(head) - 1).startswith("plan v"), \
        "too tight for the whole head line still truncates it, it does not replace it"


def test_observe_subgoal_is_closed_by_the_measurement_it_asked_for(real, ids, regions):
    """The consumer P1 left unwritten: 558 of 558 withheld verdicts said a further look would
    settle them, and nothing in the loop acted on that."""
    case, _, world, planner, _ = real
    a, b = ids[0], ids[1]
    left = regions[0]
    asked = placement_predicate(b, left)
    row = observe_subgoal(asked, f"look for evidence that {b} is seated in {left}",
                          resolves=["front_left"], entity_id=b, region_id=left)
    assert row.kind == "observe" and "front_left" in row.preconditions[0]
    tp = TaskPlanner()
    plan = tp.initial(task_for(), goal_for([(a, left)]), world, verifier=ScriptVerifier(),
                      planner=planner, progress=progress_for([(a, left)]))
    ver = ScriptVerifier()
    added = tp.refresh(plan, world, verifier=ver, planner=planner, new_obligations=[row])
    assert added.version_bumped and added.added == [asked], \
        "a question the plan was not already asking is new work"
    now = _row(added.plan, asked)
    assert now.kind == "observe" and now.status is Status.pending, \
        "asking for a look is not the look: the row opens on a promise, not an answer"
    dupe = tp.refresh(added.plan, world, verifier=ver, planner=planner,
                      new_obligations=[row.model_copy()])
    assert dupe.added == [] and not dupe.version_bumped, \
        "one predicate is one row, however many looks were asked for"

    ver.flip(b, left, PredicateVerdict.true)
    settled = tp.refresh(dupe.plan, world, verifier=ver, planner=planner,
                         new_obligations=[row.model_copy()])
    closed = _row(settled.plan, asked)
    assert closed.status is Status.done and closed.satisfied.value is True
    assert closed.completion_evidence_refs == [f"obs_script:{asked}"], \
        "the row closes on the instrument's ref, which is the ref the look was supposed to produce"
    assert asked not in {s.predicate_id for s in tp.obligations(settled.plan)}
    assert not settled.version_bumped, "the look arriving is evidence, not a replan"


def test_an_observe_row_with_no_view_left_says_so_and_stays_open(real, ids, regions):
    case, _, world, planner, _ = real
    a, left = ids[0], regions[0]
    row = observe_subgoal(placement_predicate(a, left), f"is {a} in {left}?", resolves=[],
                          entity_id=a, region_id=left)
    assert "no further look" in row.preconditions[0]
    assert row.status is Status.pending and row.satisfied.value == "unknown"


def test_mark_attempt_binds_to_the_row_the_action_names_and_reports_a_miss(real, ids, regions):
    case, _, world, planner, verifier = real
    pairs = list(zip(ids, regions))
    tp = TaskPlanner()
    plan = tp.initial(task_for(), goal_for(pairs), world, verifier=verifier, planner=planner,
                      progress=progress_for(pairs))
    target = _row(plan, placement_predicate(ids[0], regions[0]))
    dec = Decision(context_id="ctx_1", based_on_state_version=world.state_version,
                   goal_ref="g_1", action="execute",
                   execute=DecisionExecute(skill="pick", args={"object_id": ids[0]}))
    marked, hit = tp.mark_attempt(plan, dec)
    assert hit is not None and hit.subgoal_id == target.subgoal_id
    assert _row(marked, placement_predicate(ids[0], regions[0])).attempts == 1
    assert _row(marked, placement_predicate(ids[0], regions[0])).last_decision_id == \
        dec.decision_id
    assert marked is not plan, "the plan handed in is never mutated"
    assert plan.subgoal(target.subgoal_id).attempts == 0

    ghost = Decision(context_id="ctx_1", based_on_state_version=world.state_version,
                     goal_ref="g_1", action="execute",
                     execute=DecisionExecute(skill="pick", args={"object_id": "obj_ghost_1"}))
    unchanged, miss = tp.mark_attempt(marked, ghost)
    assert miss is None and unchanged == marked, "a decision that matches no row is reported"
    finish = Decision(context_id="ctx_1", based_on_state_version=world.state_version,
                      goal_ref="g_1", action="finish", rationale="done")
    assert tp.mark_attempt(marked, finish)[1] is None


def test_a_dependency_is_violated_after_the_fact_and_not_prevented(real, ids, regions):
    """§3.4: the runtime records, it does not block. The plan neither stops the action nor
    edits itself to excuse it."""
    case, _, world, _, _ = real
    a, b = ids[0], ids[1]
    left = regions[0]
    tp = TaskPlanner()
    stub = StubPlanner(slots={(a, left): 1, (b, regions[1]): 1}, inside={left: [b]})
    ver = ScriptVerifier()
    pairs = [(a, left), (b, regions[1])]
    goal = goal_for(pairs)
    world2 = seated_in(world, b, left)
    plan = tp.initial(task_for(), goal, world2, verifier=ver, planner=stub,
                      progress=progress_for(pairs))
    plan = tp.refresh(plan, world2, verifier=ver, planner=stub, task=task_for(), goal=goal,
                      progress=progress_for(pairs)).plan
    blocked = _row(plan, placement_predicate(a, left))
    dec = Decision(context_id="ctx", based_on_state_version=world2.state_version,
                   goal_ref=goal.goal_id, action="execute",
                   execute=DecisionExecute(skill="place",
                                           args={"object_id": a, "target_id": left}))
    attempted = tp.mark_attempt(plan, dec, subgoal=blocked.subgoal_id)[0]
    r = tp.refresh(attempted, world2, verifier=ver, planner=stub, task=task_for(), goal=goal,
                   progress=progress_for(pairs))
    assert r.violations and r.plan.version == attempted.version + 1
    assert r.plan.revisions[-1].trigger == "dependency_violation"
    dep = [d for d in r.plan.dependencies if d.on_subgoal_id == blocked.subgoal_id]
    assert len(dep) == 1 and dep[0].violated.value is True
    assert _row(r.plan, placement_predicate(a, left)).attempts == 1, \
        "the action still happened: a plan that vetoed decisions would be a controller"
    again = tp.refresh(r.plan, world2, verifier=ver, planner=stub, task=task_for(), goal=goal,
                       progress=progress_for(pairs))
    assert again.violations == [], "one violation is reported once, not once per round"


# ------------------------------------------------------------ instruments (§5.5) ----


def test_the_plan_reads_the_full_feasible_set_not_the_offered_sample(real, ids, regions):
    """`Instruments.feasible(limit=None)`: concluding "blocked" from a 3-slot sample would
    fabricate an obstacle out of options the runtime simply did not print."""
    case, _, world, planner, _ = real
    inst = Instruments(planner, world)
    feas = inst.feasible(ids[0], regions[0])
    assert feas.offered == len(planner.generate(regions[0], ids[0], world, limit=None))
    assert feas.offered > 3, "the model is shown a smaller sample than a claim may use"
    assert feas.feasible and feas.ok
    empty = inst.feasible(ids[0], "no_such_region")
    assert not empty.feasible and empty.offered == 0
    assert "generator offered no slot" in empty.first_reason
    assert Instruments(None, world).feasible(ids[0], regions[0]).ok == []
    assert not Instruments(None, world).have_placement


# ------------------------------------------------------------ ablation gate ----


def test_plan_revision_is_attributed_to_planning_not_replanning():
    """The frozen registry says so, and P2-c must therefore check `wo_replanning` by hand.

    This test exists to be read before anyone adds an `if ablation:` branch to the planner: the
    arm is a payload and record difference, and the one place it is *measured* is here — where a
    plan that revised every round still shows no `recovery_action`, so no violation.
    """
    revisions = ["task_understanding", "plan", "plan_revision"]
    assert {EVENT_MODULE[e] for e in revisions} == {"planning"}
    wo_planning = ablation("wo_planning")
    assert wo_planning.violations(revisions) == sorted(revisions)
    wo_replanning = ablation("wo_replanning")
    assert wo_replanning.violations(revisions) == [], \
        "a revised plan is not a violation of wo_replanning; `recovery_action` is"
    assert wo_replanning.violations(["recovery_action"]) == ["recovery_action"]
    assert ablation("full").violations(revisions + ["recovery_action"]) == []
