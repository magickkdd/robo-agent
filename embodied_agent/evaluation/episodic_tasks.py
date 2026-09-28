"""The `em` task set: pairs of episodes where one agent has already lived through part of the other (SPEC-v0.2 §5.4, §11 Memory, §13 P3).

Why a separate set
------------------
§13's RQ3 asks whether episodic memory lets the agent *reuse* history on a later similar task, and
§11's Memory rows (retrieval relevance / successful reuse / negative transfer / stale memory usage)
are all claims about a **second** episode reading what a **first** one wrote. None of the existing
sets contains such a relation: `frozen`, `dev`, `formal` and `long_horizon` give every case its own
cold store, so every recall in those runs is empty and the memory arm measures an absence. So this
set lives beside them, is *not* in `tasks._REGISTERED_SETS`, leaves `frozen_tasks_v1.json`
byte-identical, and freezes to its own file with its own hash — the separation
`long_horizon_tasks` established.

Why the unit is a pair and not a case
-------------------------------------
A `TaskCase` is one episode; a memory measurement is two — a writer that leaves a residue and a
reader that reads it, in one store, in a declared order. `EM_PAIRS` is that declaration and
`episodic_runs.py` drives it, one store per pair, because the batch-level store `run_group` installs
cannot isolate a pair from every other pair in the batch.

What had to be measured before this set could be authored
---------------------------------------------------------
A sweep over the 38 zero-spend episodes that already exist (`work/p3d_order_sweep.py`, then
`work/p3d_lever_scan.py` on the same artifacts) found the order lever alive and dead at once:

* **254 of 370 rounds had more than one ready row** — a real ordering choice, up to six admissible
  rows at once (`lh_c3` round 1);
* **0 of 370 rounds took a row other than the first one `plan_view` publishes**: `PlanPolicy`'s
  choice *is* `row_key` order, every time;
* **0 of 154 rows in those episodes was anything but `placed:`** — no `clear:`/`recover` row ever
  fired, so nothing in the existing sets forces a non-alphabetical order;
* therefore **0 of 38 produced a remembered order differing from the plan order it would be read
  against.**

The reason is arithmetic, not luck, and naming it is what made the set authorable: a successful
writer's executed order is `row_key` order over its own ready set, and a subset of an alphabetical
order is alphabetical, so writer and reader necessarily agree about every object they share. A recall
can only move a trajectory when the reader has an object **the writer never acted on** whose row
sorts **before every remembered row**. p1 and p2 are seeded for exactly that; p3 and p4 are seeded
for its two failure modes.

The four pairs, and the one condition each isolates
---------------------------------------------------
`preflight` refuses a pair whose case data does not say what its `expectation` claims. The
expectations were written from that arithmetic before any em episode ran.

* **p1 promotion** (seeds 405/405) — the reader's `row_key`-first object `obj_blue_3` is one the
  writer never touched, and the memory's first step is `obj_green_2`, whose claim the reader *also*
  owes (same seed ⇒ same region). Positive transfer: the path changes in the remembered direction
  and the verdict does not.
* **p2 uncheckable claims govern** (seeds 415/415) — the same object structure, and the remembered
  claims name regions this reader does not want. It was filed as *the* negative-transfer pair, on
  the reasoning that a claim about `obj_green_2` in a tray this plan never targets would be measured
  `false` and then declined; the first run said no, and `EM_PAIRS[1]["preregistration"]` keeps the
  record. The reason is a second blindness in §5.4's guard, one not fixable by waiting a round:
  `arm.py::_memory_query` builds `measured` from *this episode's own plan rows*, so a predicate the
  plan does not name is never looked up and never refuted. p2 measured `contradicted=false` on 7 of
  7 rounds — its whole recall is untestable — and the recall still moved the first hand from
  `obj_blue_3` to `obj_green_2`. Contrast p1, whose regions do agree: there the row is refuted at
  rounds 2-4 and governs anyway, because a claim refuted while the work is still owed is what
  `policy.py` calls positive transfer.
* **p3 retrieved, not attributable** (seeds 406/407) — no entity id in common, so the reader is
  shown a row whose steps name objects this plan has no row for. Prediction: recalled non-empty,
  `memory_order` empty, trajectory equal to the control's. This is the row that stops "the memory
  was on the page" being read as "the memory was used". Measured: the two writer objects *were*
  ranked (`memory_order=['obj_blue_2','obj_green_1']`) and nothing followed, because the ranking is
  over entities no ready row names — the flag, not the page, is what keeps this pair honest.
* **p4 duplicate** (seeds 408/408) — the same task twice. Every remembered object is owed, the
  remembered order *is* the plan's order, so the arm reports the memory followed and moves nothing.
  The noise floor: an arm that diverged here would be diverging on nothing. Measured: followed on
  6 of 6 decision rounds, all 6 of them rounds where the no-memory arm picked the same object,
  trajectory identical. That is the price of reading §11's reuse row off a self-report, and
  `EM_PAIRS[3]["preregistration"]` records the check that assumed otherwise.

Nothing here costs an outcome. `work/p3d_order_cost_scan.py` walked every placement order of 15
em-shaped cases — 78 orders — and all 78 completed with no refused call, because
`_respect_region_capacity` and `MAX_OBJECTS_PER_REGION = 3` forbid the one physical channel through
which an order could cost anything. `work/p3d_decline_reachability.py` looks for the store shape that
could still reach the decline branch. Both numbers are in `episodic_manifest()`.

One property of the frozen relevance formula, recorded rather than fixed
------------------------------------------------------------------------
`RELEVANCE_WEIGHTS["task_kind"]` is 3.0 and `MIN_RELEVANCE` is 1.0, and the runtime takes the query's
`task_kind` from the **batch's set name** — so every stored row of an `em` batch matches every `em`
query on that single term, and no row in this set can be filtered out as irrelevant. Inside a batch
relevance *orders* and does not *exclude*; the filter that bites is §5.4's page budget. That is a
fact about the shipped formula and this harness together, it is why p3 exists, and it is repeated in
the manifest so no reader mistakes an empty `recalled` list for a relevance decision.

Nothing here is model-billed: both arms run `policy="memory"` on the rule planner, the utterances are
frozen strings authored without a model, and the hidden truth stays in `eval_spec`.
"""
from __future__ import annotations

import hashlib
import json
import os

from ..core.scene import PhysicsScene
from ..core.skills import SkillExecutor
from .long_horizon_tasks import _understanding
from .tasks import FROZEN_PATH, TaskCase, _cfg, build_case

EM_SET_NAME = "em"
EM_FROZEN_PATH = os.path.join(os.path.dirname(FROZEN_PATH), "frozen_episodic_v1.json")

#: The two arms of every pair, named the way §9 names them. Both run `policy="memory"`; the gate is
#: the only difference, and `wo_episodic_memory` still has a store installed, because the contrast is
#: the module switched off on a loop that has one, not a loop built without one.
EM_TREATMENT_ARM = "full"
EM_CONTROL_ARM = "wo_episodic_memory"
EM_ARMS = (EM_TREATMENT_ARM, EM_CONTROL_ARM)

#: One budget for both arms of every pair, declared before any em episode ran. The longest case here
#: is 3 objects = 6 skill calls and 7 rounds of nominal work, against the 9-13 rounds
#: `work/p3d_lever_scan.py` measured for the 3-object episodes that exist, so 14 rounds / 20 calls is
#: about 2x nominal and no pair can be censored by its own allowance. Identical across arms for the
#: reason §8's is: arms that differ in budget measure the budget.
EM_BUDGET = {"max_decision_rounds": 14, "max_skill_calls": 20}

# ------------------------------------------------------------------ configs --
EM_CONFIGS = [
    _cfg("em_p1_write_two", "episodic", 2, 405, budget=EM_BUDGET,
         notes="p1's writer. Green sorts after blue in row_key order, so this episode's first hand "
               "is green's, and that is the sequence the reader will be shown"),
    _cfg("em_p1_read_three", "episodic", 3, 405, budget=EM_BUDGET,
         notes="p1's reader: the same scene plus obj_blue_3, the body no experience has acted on. "
               "Its row sorts first, so this is the pair where a recall has somewhere to push"),
    _cfg("em_p2_write_two", "episodic", 2, 415, budget=EM_BUDGET,
         notes="p2's writer: red to tray_middle, green to tray_left"),
    _cfg("em_p2_read_three", "episodic", 3, 415, shared=True, budget=EM_BUDGET,
         notes="p2's reader: the same three bodies, but the hidden truth wants red AND green in "
               "tray_right. Every remembered claim is then refuted on a region this plan does not "
               "owe, which is what a misleading experience has to be to count as one"),
    _cfg("em_p3_write_two", "episodic", 2, 406, budget=EM_BUDGET,
         notes="p3's writer: obj_green_1 and obj_blue_2"),
    _cfg("em_p3_read_three", "episodic", 3, 407, budget=EM_BUDGET,
         notes="p3's reader: obj_blue_1, obj_yellow_2, obj_purple_3 — no id in common with the "
               "writer, so the recalled steps name entities this plan has no row for"),
    _cfg("em_p4_write_three", "episodic", 3, 408, budget=EM_BUDGET,
         notes="p4's writer: the whole task, done once"),
    _cfg("em_p4_read_three", "episodic", 3, 408, budget=EM_BUDGET,
         notes="p4's reader: the same task again — a memory of exactly this episode, which the arm "
               "must be able to follow without moving anything"),
]

#: The experiment's unit. `expectation` is pre-registered: every value is either an object id frozen
#: here (and `preflight` refuses the pair if the case arithmetic disagrees) or `None`, which means
#: "whatever the computed control value is" and is how a pair whose answer is *equality* says so
#: without me naming an id I have not seen. `round1` is the pick made while the guard has nothing
#: measured to check; `after_guard` is the first pick made once the snapshot has measured the
#: writer's claims false.
EM_PAIRS: list[dict] = [
    {"pair_id": "em_p1", "condition": "promotion",
     "writer": "em_p1_write_two", "reader": "em_p1_read_three",
     "expectation": {
         "rows_per_round": 1, "declined_when_measured": [],
         "stale_memory_governs": True,
         "control_first_pick": "obj_blue_3",
         "treatment_first_pick_round1": "obj_green_2",
         "treatment_first_pick_after_guard": "obj_green_2",
         "trajectory_changes": True}},
    {"pair_id": "em_p2", "condition": "uncheckable_claims_govern",
     "writer": "em_p2_write_two", "reader": "em_p2_read_three",
     "expectation": {
         "rows_per_round": 1, "declined_when_measured": [],
         "stale_memory_governs": False,
         "control_first_pick": "obj_blue_3",
         "treatment_first_pick_round1": "obj_green_2",
         "treatment_first_pick_after_guard": "obj_green_2",
         "trajectory_changes": True},
     "preregistration": {
         "filed": "before any em episode ran",
         "condition": "negative_transfer",
         "expectation": {"declined_when_measured": ["obj_green_2", "obj_red_1"],
                         "treatment_first_pick_after_guard": "obj_blue_3"},
         "falsified_by": ["/tmp/em_full/pairs_measured.json", "work/p3d_order_cost_scan.py"],
         "reason": (
             "three errors, all on my side of the instrument, and the pair that survived them is a "
             "different pair. (1) The expected decline assumed a claim about `obj_green_2` in the "
             "writer's region would be *measured* false here. `arm.py::_memory_query` builds "
             "`measured` from this episode's own plan rows, so `placed:X:R_w` with "
             "`R_w != R_r` is never looked up — and this pair is built so that every one of its "
             "claims misses: the writer is `shared=False` and the reader `shared=True`, so the two "
             "objects land in `tray_left`/`tray_middle` for the writer and both in `tray_right` for "
             "the reader. `claims_the_reader_can_check` is empty and the measurement agrees: "
             "`contradicted=false` on 7 of 7 rounds, while the recall still set the first pick. The "
             "guard is blind to this memory not for one round, as in p1, but for the whole "
             "episode — §5.4's check is a floor over the plan's own vocabulary, and this is the "
             "pair that measures where that floor stops. Where the regions *do* agree the claim is "
             "refuted only while the row is owed, which `policy.py` defines as positive transfer and "
             "promotes; the decline branch needs a recorded `false`, i.e. a failed writer. (2) The "
             "name was wrong even had the decline fired: a costed transfer needs an order that fails "
             "where another succeeds, and `work/p3d_order_cost_scan.py` walked every placement order "
             "of 15 em-shaped cases (all 8 cases of this set plus 7 shared-region seeds) — 78 of 78 "
             "orders completed with 0 refused calls. The family has no order-dependent cost channel: "
             "`_respect_region_capacity` and `MAX_OBJECTS_PER_REGION = 3` forbid the one physical "
             "route to it. (3) So a memory that overrides the plan order here is measured as free, "
             "and `negative_transfer` as a §11 row cannot be produced by this set at all. What "
             "p2 produces instead is the undetectable-staleness case above, which is the stronger "
             "of the two findings and is what this pair now pre-registers.")}},
    {"pair_id": "em_p3", "condition": "retrieved_not_attributable",
     "writer": "em_p3_write_two", "reader": "em_p3_read_three",
     "expectation": {
         "rows_per_round": 1, "declined_when_measured": [],
         "stale_memory_governs": False,
         "control_first_pick": None, "treatment_first_pick_round1": None,
         "treatment_first_pick_after_guard": None, "trajectory_changes": False}},
    {"pair_id": "em_p4", "condition": "duplicate_agrees",
     "writer": "em_p4_write_three", "reader": "em_p4_read_three",
     "expectation": {
         "rows_per_round": 1, "declined_when_measured": [],
         "stale_memory_governs": True,
         "control_first_pick": None, "treatment_first_pick_round1": None,
         "treatment_first_pick_after_guard": None, "trajectory_changes": False},
     "preregistration": {
         "filed": "before any em episode ran",
         "expectation": {"memory_followed": False},
         "falsified_by": ["/tmp/em_full/pairs_measured.json"],
         "reason": (
             "the check read `expected: trajectory_changes`, i.e. I asserted that a memory is only "
             "'followed' when it moves the trajectory. `policy.py` sets the flag from a different "
             "rule — the row that won was one the page ranked — so an agreeing memory reports "
             "followed on 6 of 6 decision rounds while `trajectory_changes` stays False. The "
             "measurement is right and my check was wrong, and the disagreement is the point: "
             "`memory_followed` counts *rankings that agree*, not reuse, so §11's successful-reuse "
             "row must be read off the two-arm trajectory split and never off this flag. The flag "
             "is still tested, against the ranking it claims to reflect "
             "(`memory_followed_is_the_ranking`).")}},
]


def build_episodic_set() -> list[TaskCase]:
    """The frozen em cases, in pair order: every writer before its reader."""
    return [build_case(cfg) for cfg in EM_CONFIGS]


# ------------------------------------------------------- the authored claims --
def _truth(case: TaskCase) -> dict[str, str]:
    return {a.entity_id: a.target_id for a in case.eval_spec.assignments}


def _row_key(entity_id: str, region: str) -> str:
    """The row key `plan_view` publishes and `PlanPolicy` visits in.

    Not a restatement of a name: `planning.subgoals.row_key` *is* `predicate_id` for an `achieve`
    placement row, and `work/p3d_lever_scan.py` measured the first published row taken in 370 of 370
    rounds. So this is the order the control arm will follow, and every expectation above is a claim
    about what a memory has to beat.
    """
    return f"placed:{entity_id}:{region}"


def _declined(claims: dict[str, str], recorded: dict[str, str], satisfied: set[str]) -> list[str]:
    """`MemoryPolicy._remembered_order`'s decline rule, restated over case arithmetic.

    `claims[e]` is the predicate the memory asserts about `e`; `recorded[e]` is the truth value it
    asserts (`"true"` for every row of a writer frozen `expected="success"`); `satisfied` is the set
    of the reader's predicates it has already made true, which is also the only place its guard can
    look a predicate up (`arm.py::_memory_query` builds `measured` from `plan.subgoals`).

    Read the two conditions together and the rule's reach collapses: a `"true"` claim is refuted
    while its row is *unsatisfied* — which is when it is owed, so promoted — and once the row is
    satisfied the claim is confirmed rather than refuted. `"true" and claim in satisfied` never
    holds. What does hold it is a `"false"` claim whose row the reader has since filled: a failed
    writer's residue, revisited. Hand this function that shape and it returns it — the emptiness
    below is a property of a success-only store, not a constant typed in its place.
    """
    return sorted(e for e, claim in claims.items()
                  if claim in satisfied and recorded.get(e) == "false")


def _pair_claim(writer: TaskCase, reader: TaskCase) -> dict:
    """What the two frozen cases say about the memory the reader will be shown.

    Case arithmetic only: no scene, no policy, no run. The writer is frozen as `expected="success"`,
    so its residue's `state_pattern` asserts `placed:X:R_w=true` for each of its objects and its
    executed order is `row_key` order over its own rows — which is what makes `remembered_order` a
    derived field and not a guess. The reader owes `placed:X:R_r`.

    Which claims the reader can *check* is not the same question as which claims differ from its
    plan, and the first version of this function confused them. §5.4's guard compares a recall
    against `measured`, and `episodic/arm.py::_memory_query` builds `measured` from
    `predicate_of(s) for s in plan.subgoals` — this episode's own row vocabulary, nothing else. So
    `placed:X:R_w` with `R_w != R_r` is never looked up, never refuted, and the object is promoted
    on the strength of a claim nobody could test. The cross-region conflict this set was authored to
    decline on is invisible to the guard that was supposed to decline it.

    For a claim the reader *can* check, `MemoryPolicy._remembered_order` (`episodic/policy.py`)
    declines only when the refuted predicate is absent from `owed` = the plan's unsatisfied rows.
    Walk the two states that predicate can be in: unsatisfied ⇒ measured `false` against a stored
    `true` ⇒ refuted **and owed** ⇒ promoted (the docstring's own words: "that is positive
    transfer"); satisfied ⇒ measured `true` ⇒ agrees with the stored `true` ⇒ not refuted. So on a
    store of successful episodes **no claim can ever reach the decline branch**, and
    `refuted_and_not_owed` is empty by construction rather than by measurement. The branch needs a
    memory that recorded a predicate *false* which the reader then made true — a failed writer — and
    then it fires on work the reader has already finished, where there is no later pick left to
    change. `decline_branch_reachable` carries that reading out of the arithmetic.
    """
    w_truth, r_truth = _truth(writer), _truth(reader)
    shared = sorted(e for e in w_truth if e in r_truth)
    remembered = sorted(shared, key=lambda e: _row_key(e, w_truth[e]))
    # One set, two names for the two roles it plays at the first decision: the rows the reader owes,
    # and the whole vocabulary its guard can look a predicate up in. They coincide before any work
    # is done, which is exactly why an unsatisfied claim is always refuted-*and*-owed there.
    owed = {_row_key(e, region) for e, region in r_truth.items()}
    claims = {e: _row_key(e, w_truth[e]) for e in remembered}
    checkable = {e: c for e, c in claims.items() if c in owed}
    recorded = {e: "true" for e in remembered}
    satisfied: set[str] = set()
    declined = _declined(claims, recorded, satisfied)
    promotable = [e for e in remembered if e not in declined]
    plan_first = min(r_truth, key=lambda e: _row_key(e, r_truth[e]))
    return {"pair": f"{writer.task_id}→{reader.task_id}",
            "writer": writer.task_id, "reader": reader.task_id,
            "writer_objects": sorted(w_truth), "reader_objects": sorted(r_truth),
            "shared_objects": shared, "remembered_order": remembered,
            "writer_claims": claims, "reader_owed": sorted(owed),
            "claims_the_reader_can_check": checkable,
            "claims_the_reader_cannot_check": {e: c for e, c in claims.items() if c not in owed},
            "recorded_truths": recorded,
            "reader_rows_satisfied_at_first_decision": sorted(satisfied),
            "refuted_and_not_owed": declined,
            "decline_branch_reachable": any(v == "false" for v in recorded.values()),
            "decline_branch_reason": (
                "every claim on this page is a `true` assertion by a successful writer, and a "
                "`true` assertion is either refuted-while-owed (promoted) or confirmed-when-done "
                "(not refuted); the guard's decline branch needs a recorded `false`, which a "
                "success-only store cannot contain"),
            "reader_plan_first": plan_first,
            "first_unremembered": plan_first not in remembered,
            "predicted_first_pick_round1": remembered[0] if remembered else plan_first,
            "predicted_first_pick_after_guard": promotable[0] if promotable else plan_first,
            "trajectory_will_change": bool(remembered) and remembered[0] != plan_first}


def pair_claims() -> list[dict]:
    """`_pair_claim` for every declared pair, in `EM_PAIRS` order and keyed by its `pair_id`.

    The declared expectation and any `preregistration` amendment travel with the arithmetic, so the
    frozen manifest carries the claim and the record of what it used to say in one place.
    """
    cases = {c.task_id: c for c in build_episodic_set()}
    return [dict(_pair_claim(cases[p["writer"]], cases[p["reader"]]),
                 pair_id=p["pair_id"], condition=p["condition"], expectation=p["expectation"],
                 preregistration=p.get("preregistration") or {})
            for p in EM_PAIRS]


# --------------------------------------------------------------- preflight ----
def _geometry(planner, case: TaskCase, world, grasp_count) -> dict:
    """Is every relation the hidden truth asks for physically open in the *initial* scene?

    The same two geometric claims `long_horizon_tasks.preflight` makes — each (object, region) pair
    has a candidate that clears the production re-check, and each object has a grasp candidate —
    without its shared-region probe, which is not a claim this set makes. Plus the one world fact the
    pair arithmetic leans on: no target object starts inside a region, so a memory claim about a
    placement here is a claim about a layout the scene does not match — whether the reader can *see*
    that mismatch is `claims_the_reader_can_check`, and p2 is the pair where it cannot.
    """
    want: dict[str, set[str]] = {}
    for a in case.eval_spec.assignments:
        want.setdefault(a.entity_id, set()).add(a.target_id)
    inside = sorted({o.entity_id for o in world.occupancy if o.entity_id in want})
    per_object = {eid: {"clear_slots": {t: len([c for c in planner.generate(t, eid, world, limit=None)
                                                if planner.recheck(c, world).ok])
                                        for t in sorted(want[eid])},
                        "grasp_candidates": len(grasp_count(eid))}
                  for eid in sorted(want)}
    return {"ok": bool(per_object) and not inside and all(
                min(v["clear_slots"].values()) >= 1 and v["grasp_candidates"] >= 1
                for v in per_object.values()),
            "targets_already_in_a_region": inside, "per_object": per_object}


def preflight(case: TaskCase) -> dict:
    """Is this case speakable, physically open, and does its pair still say what it was frozen to say?

    Three independent refusals, the third of which is what makes this a *set* and not a list: a pair
    whose claims no longer match its `EM_PAIRS` expectation measures something nobody
    pre-registered, and it is refused here, at build time, for both of its cases.
    """
    from ..core.runtime import build_world_state

    scene = PhysicsScene(seed=case.seed, object_layout=case.objects, gui=False)
    try:
        executor = SkillExecutor(scene, world_provider=lambda: None)
        world = build_world_state(scene, 1, "obs_0001")
        executor.world_provider = lambda: world
        understood = _understanding(case, world)
        geometry = _geometry(executor.placement_planner, case, world, scene.grasp_candidates)
    finally:
        scene.close()
    pair = _pair_check(case)
    return {"ok": bool(understood["ok"] and geometry["ok"] and (not pair or pair["consistent"])),
            "understanding": understood, "geometry": geometry, "pair": pair}


def _pair_check(case: TaskCase) -> dict:
    """`_pair_claim` for this case's pair, against the expectation frozen in `EM_PAIRS`."""
    mine = next((p for p in EM_PAIRS if case.task_id in (p["writer"], p["reader"])), None)
    if mine is None:
        return {}
    cases = {c.task_id: c for c in build_episodic_set()}
    claim = _pair_claim(cases[mine["writer"]], cases[mine["reader"]])
    expected = mine["expectation"]
    control = expected["control_first_pick"] or claim["reader_plan_first"]
    round1 = expected["treatment_first_pick_round1"] or claim["predicted_first_pick_round1"]
    after = expected["treatment_first_pick_after_guard"] or claim["predicted_first_pick_after_guard"]
    return {**claim, "pair_id": mine["pair_id"], "condition": mine["condition"],
            "expectation": expected,
            "preregistration": mine.get("preregistration") or {},
            "consistent": bool(control == claim["reader_plan_first"]
                               and round1 == claim["predicted_first_pick_round1"]
                               and after == claim["predicted_first_pick_after_guard"]
                               and sorted(expected["declined_when_measured"])
                               == claim["refuted_and_not_owed"]
                               and expected["stale_memory_governs"]
                               == bool(claim["claims_the_reader_can_check"])
                               and expected["trajectory_changes"]
                               == claim["trajectory_will_change"])}


# ------------------------------------------------------------------ manifest ---
def episodic_manifest() -> dict:
    """The em set, hash-addressed the way `frozen_manifest` does for the frozen 47."""
    from ..core.v02 import ABLATION_CONDITIONS
    from ..episodic.retrieval import (CLASH_CAP, DEFAULT_LIMIT, MATCH_TERM_CAP, MIN_RELEVANCE,
                                      RELEVANCE_WEIGHTS, STEP_CAP)
    from ..planning.working_memory import RECALL_BUDGET_CHARS, RENDER_BUDGET_CHARS

    cases = build_episodic_set()
    payload = {
        "schema_version": "1",
        "spec": "v0.2 §5.4 / §11 Memory / §13 P3",
        "set": EM_SET_NAME,
        "arms": {"treatment": "ablation=full + policy=memory",
                 "control": "ablation=wo_episodic_memory + policy=memory",
                 "note": ("the same policy, the same store, the same budget, one module switch: "
                          "with the gate off §5.4's retrieval never runs, so `working_memory."
                          "recalled` is empty and `MemoryPolicy` is `PlanPolicy` line for line")},
        "ablation_registered": sorted(ABLATION_CONDITIONS),
        "budget_rationale": ("declared before any em run: 6 nominal skill calls and 7 rounds for "
                             "the longest case here, against 9-13 rounds measured for the 3-object "
                             "episodes that exist; 14 rounds / 20 calls is ~2x that and identical "
                             "in both arms"),
        "budgets": EM_BUDGET,
        "page_constants": {"RECALL_BUDGET_CHARS": RECALL_BUDGET_CHARS,
                           "RENDER_BUDGET_CHARS": RENDER_BUDGET_CHARS,
                           "DEFAULT_LIMIT": DEFAULT_LIMIT, "STEP_CAP": STEP_CAP,
                           "MATCH_TERM_CAP": MATCH_TERM_CAP, "CLASH_CAP": CLASH_CAP},
        "relevance_formula": {"weights": RELEVANCE_WEIGHTS, "MIN_RELEVANCE": MIN_RELEVANCE},
        "relevance_within_a_batch": ("no em row can be filtered out as irrelevant: the query's "
                                     "task_kind term is the batch's set name, every stored row of "
                                     "the batch carries it, and that one term is worth 3.0 against "
                                     "MIN_RELEVANCE=1.0. Relevance orders rows; §5.4's page budget "
                                     "trims them"),
        "order_lever_measurement": {
            "source": ["work/p3d_order_sweep.py", "work/p3d_lever_scan.py"],
            "episodes": 38, "rounds": 370, "rounds_with_more_than_one_ready_row": 254,
            "rounds_that_took_a_row_other_than_the_first_published": 0,
            "rows_that_were_not_placed_predicates": 0,
            "episodes_whose_remembered_order_differed_from_the_plan_order": 0,
            "consequence": ("a subset of an alphabetical order is alphabetical, so a writer and a "
                            "reader can only disagree via an object the reader has and the writer "
                            "never acted on; that is the structure p1 and p2 are seeded for")},
        "guard_timing": ("two blindings, measured on different pairs. (a) In time: the refutation "
                         "test needs a verdict on both sides and a freshly derived plan row reads "
                         "`unknown`, so on every pair the reader's FIRST pick is made with the guard "
                         "blind — p1 and p4 show a refuted row governing from round 2 onward "
                         "(rounds [2,3,4] and [2,3,4,5,6]). (b) In vocabulary: "
                         "`arm.py::_memory_query` builds `measured` from this episode's own plan "
                         "rows, so a claim about a region this plan does not name is never looked up "
                         "and never refuted at any round — p2 is built so that all of its claims "
                         "miss, and it measured `contradicted=false` on 7 of 7 rounds while still "
                         "setting the order. (a) is a one-round gap; (b) is permanent, and the set "
                         "carries a pair for each because they need different fixes"),
        "decline_branch": {
            "rule": ("`policy.py::_remembered_order` declines an object when a predicate the recall "
                     "asserted is refuted here *and* absent from the plan's unsatisfied rows"),
            "reachable_on_this_store": {p["pair_id"]: p["decline_branch_reachable"]
                                        for p in pair_claims()},
            "reason": (pair_claims()[0]["decline_branch_reason"]),
            "measured": {
                "em_pairs": 4, "em_arms": 2, "em_declines": 0,
                "lh_repeat_probe": "work/p3d_decline_reachability.py",
                "lh_pass1_experiences": 6, "lh_pass1_stored_false_claims": 2,
                "lh_pass1_false_claim_rows": ["placed:obj_green_3:tray_right=false",
                                              "placed:obj_red_2:tray_right=false"],
                "lh_pass2_episodes": 12, "lh_pass2_declines": 0, "lh_pass2_divergent_cases": 0},
            "why_the_failed_writer_still_does_not_reach_it": (
                 "the two `false` claims exist only in `lh_c4_failed_grasp_recovery`'s residue, whose "
                 "outcome is `partial`, and a claim of `false` is refuted by a snapshot that measures "
                 "`true` — i.e. only by a reader that finishes the work the writer could not. The "
                 "same deterministic policy repeats the same failure: pass 2 measured `failed` for "
                 "that case in both arms, 11 rounds in both, no divergence. The branch needs a "
                 "capable reader and an incapable writer, which one policy cannot supply"),
            "consequence": ("§11's negative-transfer *detection* has no reachable input on a store "
                            "this environment writes: the decline needs a recorded `false`, and a "
                            "`false` is only refuted by a reader that succeeds where the writer "
                            "failed. Where the branch can be reached the reader has by construction "
                            "already completed that object, so no later pick is left to change — the "
                            "flag is a record, not a brake. It is exercised in "
                            "`tests/contract/test_v02_episodic_arm.py` on a hand-built recall, which "
                            "is the only place its input can be made")},
        "negative_transfer_constructibility": {
            "asked": ("can any em-shaped case make one placement order cost more than another, which "
                      "is what a measured negative transfer needs"),
            "probe": "work/p3d_order_cost_scan.py",
            "cases": 15, "orders_tested": 78, "orders_completed": 78, "refused_calls": 0,
            "coverage": ("every permutation of every assignment in all 8 cases of this set plus 7 "
                         "`shared=True` seeds, driven through `SkillExecutor` on a real scene"),
            "why": ("`_respect_region_capacity` and `MAX_OBJECTS_PER_REGION = 3` refuse to author a "
                    "region the hardware cannot hold, which removes the only physical channel "
                    "through which an order could cost anything in a flat place-each-object task"),
            "consequence": ("a memory that overrides the plan order in this family is measured as "
                            "free: same rounds, same outcome, both arms complete. §11's negative "
                            "transfer row is therefore reported as an absence with a mechanism, and "
                            "the costed version belongs to a set with dependency-bearing work — the "
                            "`lh` family, whose rows fail verification when the order is wrong")},
        "pairs": pair_claims(),
        "cases": [
            {"task_id": c.task_id, "subset": c.subset, "seed": c.seed, "n_objects": c.n_objects,
             "utterance": c.utterance, "objects": c.objects, "targets": c.targets,
             "budgets": c.budgets.model_dump(mode="json"),
             "verify": c.verify.model_dump(mode="json"), "events": c.event_specs(),
             "expected": c.expected, "notes": c.notes,
             "hidden_eval_spec": c.eval_spec.model_dump(mode="json"),
             "preflight": preflight(c)}
            for c in cases],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    payload["sha256"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return payload


def dump_em_frozen(path: str = EM_FROZEN_PATH) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(episodic_manifest(), f, ensure_ascii=False, indent=1, sort_keys=True)
    return path


def check_em_frozen(path: str = EM_FROZEN_PATH) -> tuple[bool, str]:
    """Self-consistency first, then agreement with the code."""
    if not os.path.exists(path):
        return False, f"no episodic manifest at {path}"
    with open(path, encoding="utf-8") as f:
        recorded = json.load(f)
    embedded = str(recorded.pop("sha256", ""))
    blob = json.dumps(recorded, ensure_ascii=False, sort_keys=True, indent=1)
    live = episodic_manifest()
    if hashlib.sha256(blob.encode("utf-8")).hexdigest() != embedded:
        return False, "the episodic file does not hash to its own recorded value"
    if embedded == live["sha256"]:
        return True, live["sha256"][:12]
    return False, (f"em set drifted: frozen {embedded[:12]} != live {live['sha256'][:12]}")


if __name__ == "__main__":
    import sys

    if "--check" in sys.argv:
        ok, msg = check_em_frozen()
        print(("em frozen OK " if ok else "em frozen MISMATCH ") + msg)
        raise SystemExit(0 if ok else 1)
    if "--dump" in sys.argv:
        print(dump_em_frozen())
        raise SystemExit(0)
    for claim in pair_claims():
        print(json.dumps(claim, ensure_ascii=False))
    for c in build_episodic_set():
        print(json.dumps({"task_id": c.task_id, "n": c.n_objects, "seed": c.seed,
                          "ok": preflight(c)["ok"]}, ensure_ascii=False))
