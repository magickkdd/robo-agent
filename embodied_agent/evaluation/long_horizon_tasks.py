"""The `long_horizon` task set (SPEC v0.2 §8) and its own frozen manifest.

Why a separate set
------------------
§8 asks for a *first batch of complex tasks* with, in every case: 3-8 objects, several subgoals, at
least one explicit or implicit dependency, a finite space/resource constraint, a possible temporary
state, at least one state change or execution failure, and a final state that verifies several
relations at once. None of the frozen sets does that on purpose: `dev` calibrates physics and
prompts, `formal` isolates one mechanism per case, and `protocol` is boundary behaviour. Adding
long-horizon cases to any of them would silently re-freeze a set whose results already exist, so
this set lives beside them, is *not* in `tasks._REGISTERED_SETS`, and `frozen_tasks_v1.json` stays
byte-identical. It has its own file, `configs/experiment/frozen_long_horizon_v1.json`, and its own
hash check.

What is reachable, measured
---------------------------
§8's example utterance names the hard case: *if an object has to be moved out of the way, restore
the relations that must hold before the task ends*. `planning/subgoals.py` authors that as a
`recover` row in two shapes, and only one of them survives measurement here:

* **shape 1 — a satisfied relation is destroyed after the fact** (the environment shoves an object
  that was already handed off, pinning it against the tray wall). Reachable: the disturbance and its
  recovery were calibrated on 92/92 trials across all three trays, every offered slot and four seeds
  (`work/p0_impulse*.py`, recorded in `tasks.IMPULSE_PIN_TO_WALL`), and the pinned cube stays
  graspable. Every `displace_*` case below is this shape.
* **shape 2 — a foreign body occupies the region and must be displaced, then brought back**. Not
  reachable at this scene's object scale, measured in six steps (`work/p2d_reach.py`,
  `work/p2d_pick_from_tray.py`, `work/p2d_block_matrix.py`, `work/p2d_temp_dest.py`,
  `work/p2d_slot_sweep.py`, `work/p2d_shape2.py`; the numbers are in `SHAPE_2_MEASURED` below). The
  short version: blocking a region takes ~160 mm of seated bodies in a 262 mm interior, bodies that
  wide are at the edge of the gripper, and the one arrangement whose slot arithmetic closes the
  return leg fails at execution because the hand cannot carry an 80 mm body up to the 0.80 m
  transfer height — it stops 17-18 mm short. Authoring it anyway would produce an episode that
  measures the author, not the agent, which is the same reason `_respect_region_capacity` refuses
  over-subscribed regions.

The defect that measurement exposed is worth naming for §13 P5: `PlacementPlanner.recheck` scores a
slot by footprint and descent envelope, with no term for the *transfer path with the body in hand*.
So a placement can be reported feasible by the very instrument the planner uses to decide that a
region is blocked, and still be unexecutable. The runtime is not fooled — the skill fails, the
verifier reports it, and the round is charged — but a `recover` row authored on that instrument
would be a promise the hardware cannot keep. That is recorded, not papered over: fixing the
instrument is a decision for the ablation phase, where its cost can be measured.

Where the dependency is, measured
---------------------------------
§8's other bullet is "at least one explicit or implicit dependency", and a test that first asserted it
the obvious way — seat one object of a shared pair, expect `derive` to emit an edge — came out false on
all six cases. Seating a body at a region's centre does take away most of its room (47 clear slots →
13, 48 → 19, 81 → 36, all through `Instruments`, all recorded per case by `preflight`), but what is
left is still more space than the two or three objects that region owes, so `_capacity_dependencies`
is right to report no constraint and `_blocker_dependency` has nothing to displace. The dependency in
this set is therefore the one the *events* carry: every disturbance is armed inside the shared region
(on one of its owed bodies, or on the region itself), so the relation it can flip is a relation two
placements compete over. `preflight` refuses on three grounds — an utterance that does not bind to the
truth, a region with no slot, and a shared region whose slot count does not fall when its own object
is seated — and the third is what keeps "finite space" from being an adjective.

The first version of that probe hand-wrote an `OccupancyRecord` at the tray centre and measured
*no* loss at all (47/48/81 unchanged), which is the same class of error as shape 2's: a record is not
a body. `PlacementPlanner.recheck` reads a neighbour's pose from the entity, so the entity has to be
where the record says it is. `preflight` moves the real body, settles it and re-reads the world.

Determinism
-----------
Same rule as `tasks.py`: objects, the utterance, the disturbance policy and the hidden truth all
come from one frozen seed, and no model is in the loop when they are authored. One caveat that rule
does not cover: `tasks.make_objects` cycles five colours and its docstring claims colours therefore
never repeat, which is true up to 5 objects and false at 6 — two bodies of lh_c3 and lh_c6 share a
colour. Explicit mentions (colour *and* shape) are unaffected, and `class_utterance` is not: that is
why lh_c6 is not class-styled, and `preflight`'s understanding half refuses the variant (measured:
"'绿色物体': attribute {'color': 'green'} matches 2 entities"). The only class-styled case in the
frozen 47 has 5 objects, so no published number moves.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os

from ..core.runtime import build_world_state
from ..core.scene import PhysicsScene
from ..core.skills import SkillExecutor
from .tasks import (FROZEN_PATH, MAX_OBJECTS_PER_REGION, TRAYS, TaskCase, _cfg, build_case)

LH_SET_NAME = "long_horizon"
LH_FROZEN_PATH = os.path.join(os.path.dirname(FROZEN_PATH), "frozen_long_horizon_v1.json")

# One budget for every case and every arm. `max_decision_rounds=24` (the v0.1 default) censored 5 of
# the 8 dev pairs before the work was done, and a long-horizon set that cannot be finished inside its
# own budget would measure the budget. The number below is arithmetic, fixed before any result: the
# longest case has 6 objects (12 skill calls), one disruption to undo (2 more), one armed actuator
# deviation that costs a retry (`per_action_extra_repeats=2`, so 4 more) and observations between
# them — 20 nominal, 30 with slack, 40 skill calls. Identical across arms so the ablation differs in
# modules and not in allowance.
LH_BUDGET = {"max_decision_rounds": 30, "max_skill_calls": 40}

# ------------------------------------------------------- §8 shape 2, measured --
SHAPE_2_MEASURED = {
    "verdict": "not authorable in S1 at this object scale",
    "probes": ["work/p2d_reach.py", "work/p2d_pick_from_tray.py", "work/p2d_block_matrix.py",
               "work/p2d_temp_dest.py", "work/p2d_slot_sweep.py", "work/p2d_shape2.py"],
    "tray_interior_half_m": 0.131,
    "candidate_lattice_step_m": 0.020,
    "hand_descent_envelope_radius_m": 0.050,
    "transfer_z_m": 0.80,
    "f1_single_occupant_blocks_only_at_half_extent_ge": 0.05,
    "f2_lift_out_of_tray_works_for": ["cube 0.021", "cuboid 0.027x0.019x0.035", "cylinder r0.023",
                                      "cuboid 0.040x0.040x0.020 (in a tray, not on the table)"],
    "f2_never_graspable": ["cuboid 0.050x0.050x0.020", "cuboid 0.060 and wider"],
    "f3_two_80mm_occupants_at_pm45mm_leave_clear_slots_for_victim": 0,
    "f3_real_sequence_over_9_such_layouts": {
        "lift_failed": 6, "release_unconfirmed": 2,
        "completed_temp_move_but_restore_infeasible": 1},
    "f5_arrangements_scanned": 8,
    "f5_arrangements_whose_return_leg_closes": 1,
    "f6_that_layout_executed": {
        "blocker_lift": "completed", "temporary_slots_in_other_tray": 47,
        "temp_place": "completed", "victim_clear_slots_at_execution": 6,
        "victim_placements_attempted": 6, "victim_placements_completed": 0,
        "first_failure": "lift to transfer height, tcp_err_m=0.018"},
    "consequence": "the `recover` row is exercised through shape 1 (displacement) in this set; "
                   "shape 2 stays authored-by-the-planner and measured-unreachable, so the metric "
                   "for it is reported as not applicable rather than as a zero",
}

# ------------------------------------------------------------------- configs --
# `shapes` is never decoration: the disturbance mechanisms are only reproducible on the body whose
# slide against the tray wall is deterministic (see `tasks._displaceable_in`), so a case that asks
# for `displace_*` without a cube in the shared region is refused at build time, not silently
# realized as an episode where nothing happens.
LH_CONFIGS = [
    _cfg("lh_c1_shared_pair_restore", "long_horizon", 4, 301, shared=True,
         budget=LH_BUDGET, mechanism="displace_second", shapes=["cube", "cuboid", "cube", "cylinder"],
         notes="§8: 4 objects / 3 regions, a shared region (dependency between two placements), a "
               "finite region capacity, and a completion that the *second* handoff undoes — the "
               "agent has to notice a satisfied relation went false and restore it. This is the "
               "only form of the mechanism with an observed true->false flip"),
    _cfg("lh_c2_displaced_completion", "long_horizon", 5, 302, shared=True,
         budget=LH_BUDGET, mechanism="displace_placed", shapes=["cube", "cuboid", "cube", "cylinder", "cube"],
         notes="§8 at 5 objects: the disturbance lands on the object's own handoff, so the plan is "
               "valid when written and wrong on the next round; three completions must survive one "
               "invalidated completion"),
    _cfg("lh_c3_capacity_and_shift", "long_horizon", 6, 303, shared=True,
         budget=LH_BUDGET, mechanism="occupancy_shift", shapes=["cube", "cube", "cuboid", "cylinder", "cube", "cuboid"],
         notes="§8's resource constraint taken seriously: 6 objects over 3 regions is at "
               f"MAX_OBJECTS_PER_REGION={MAX_OBJECTS_PER_REGION} per region, and the shift leaves "
               "every completed relation true while the slots the plan assumed are no longer the "
               "same geometry — rework pressure without a violation"),
    _cfg("lh_c4_failed_grasp_recovery", "long_horizon", 4, 304, shared=True,
         budget=LH_BUDGET, mechanism="grasp_offset",
         shapes=["cube", "cuboid", "cylinder", "cube"],
         notes="§8's execution failure: the first grasp is actuated beside the object, so the round "
               "ends in a measured failure and the decision of what to do about it is the subject "
               "(retry, change candidate, or re-observe are all legitimate; ignoring it is not)"),
    _cfg("lh_c5_release_deviation", "long_horizon", 5, 305, shared=True, budget=LH_BUDGET, mechanism="release_high",
         shapes=["cube", "cylinder", "cube", "cuboid", "cube"],
         notes="the hand-off happens and the object is not where the candidate promised: layer 1 of "
               "§5.8 must catch it before the relation is believed"),
    _cfg("lh_c6_two_disruptions", "long_horizon", 6, 306, shared=True,
         budget=LH_BUDGET, mechanism="impulse_unplaced",
         shapes=["cube", "cube", "cuboid", "cube", "cylinder", "cuboid"],
         notes="two independent disturbances on a 6-object horizon: a still-pending object slides "
               "across the table when it is first picked at, and an actuator error costs a grasp "
               "later. The rolling plan is the point — a flat progress list cannot say which of "
               "its rows each one invalidates. `style` is left at the default explicit mention "
               "(colour *and* shape) rather than the class phrasing: `make_objects` cycles five "
               "colours, so at 6 objects two share one, and a colour-only utterance names that "
               "colour twice — the interpreter marks the goal ambiguous and no plan can be derived "
               "at all. `preflight` now refuses that, which is how the collision was found"),
]


def build_long_horizon_set() -> list[TaskCase]:
    """The frozen lh cases. `lh_c6`'s second event is composed here and nowhere else."""
    out = []
    for cfg in LH_CONFIGS:
        case = build_case(cfg)
        if cfg["name"] == "lh_c6_two_disruptions":
            out.append(dataclasses.replace(case, events=case.events + [_lh_grasp_event(case)]))
        else:
            out.append(case)
    return out


def _lh_grasp_event(case: TaskCase):
    """The armed-calibration half of `lh_c6`, built by the same helper `tasks` uses.

    Named on the second target rather than the first, so the two disturbances cannot collapse into
    one round: the impulse fires on the first `pick`, this one on the first `pick` of another entity.
    """
    from ..core.fault_injection import EventAction
    from .tasks import GRASP_OFFSET, _calibration_event

    return _calibration_event(case.task_id, EventAction.GRASP_CALIBRATION, "pick", GRASP_OFFSET,
                              entity=case.targets[1])


def _understanding(case: TaskCase, world) -> dict:
    """Does the frozen utterance actually say what the hidden truth means?

    Geometry alone cannot tell that a case was never speakable. `lh_c6` is the example: authored at 6
    objects with the class phrasing, its utterance names a colour that `make_objects` hands to two
    bodies (five colours cycle over six objects), the rule interpreter resolves that mention to two
    entities, marks the goal ambiguous, and `derive` has no rows to plan at all. The episode would
    then fail for a reason that has nothing to do with any agent.

    The claim checked here is the narrow one: the bindings the interpreter makes — resolved through
    the same `entity_id or bind_attributes` rule the verifier and the subgoal deriver use — are
    exactly the (object, region) pairs the hidden truth asks for. Anything else is refused.
    """
    from ..core.contracts import TaskInput
    from ..core.interpreter import interpret
    from ..core.verify import bind_attributes

    goal = interpret(TaskInput(task_id=case.task_id, utterance=case.utterance), world)
    bound: dict[str | None, set[str]] = {}
    for a in goal.assignments:
        eid = a.entity.entity_id or bind_attributes(dict(a.entity.attributes or {}), world)
        bound.setdefault(eid, set()).add(a.target_id)
    truth: dict[str, set[str]] = {}
    for a in case.eval_spec.assignments:
        truth.setdefault(a.entity_id, set()).add(a.target_id)
    return {"well_formed": goal.well_formed,
            "clarification": goal.clarification_request,
            "bound_pairs": sum(len(v) for v in bound.values()),
            "truth_pairs": sum(len(v) for v in truth.values()),
            "matches_truth": bound == truth,
            "ok": bool(goal.well_formed and bound == truth)}


def _shared_region(case: TaskCase) -> tuple[str, list[str]]:
    """The region the hidden truth owes the most objects to, and those objects.

    `§8`'s "finite space" bullet has to be a claim about one specific region or it is nothing; the
    tie-break is the region name, so the choice is the same in every process.
    """
    load: dict[str, list[str]] = {}
    for a in case.eval_spec.assignments:
        load.setdefault(a.target_id, []).append(a.entity_id)
    region = max(load, key=lambda t: (len(load[t]), t))
    return region, sorted(load[region])


def _resource_probe(scene: PhysicsScene, case: TaskCase, planner,
                    v1_clear: dict[str, int]) -> dict:
    """Seat one of the shared region's own objects and count what is left for the next.

    This is the §8 finite-space constraint as a measurement instead of a sentence. The body is moved
    by teleporting it to the region centre and settling — no arm, no policy, no invented geometry —
    and the world is re-read from the scene. That matters: the first version of this probe hand-wrote
    an `OccupancyRecord` at the tray centre and reported that seating a body removed *no* slots
    (47/48/81 unchanged), because `PlacementPlanner.recheck` takes a neighbour's pose from the entity
    and the entity was still measured on the table. A record is not a body.
    """
    import pybullet as p

    region, members = _shared_region(case)
    if len(members) < 2:
        return {"region": region, "ok": False,
                "note": "the truth shares no region, so there is no finite-space claim to check"}
    blocker = members[0]
    tray, body = scene.trays[region], scene.objects[blocker]
    p.resetBasePositionAndOrientation(
        body["body"], [tray["center"][0], tray["center"][1],
                       tray["floor_top"] + body["half_h"] + 0.01], [0, 0, 0, 1],
        physicsClientId=scene.cid)
    p.resetBaseVelocity(body["body"], [0, 0, 0], [0, 0, 0], physicsClientId=scene.cid)
    scene.settle(1.0)
    seated = build_world_state(scene, 2, "obs_0002")
    inside = sorted(o.entity_id for o in seated.occupancy if o.target_id == region)
    clear = {e: len([c for c in planner.generate(region, e, seated, limit=None)
                     if planner.recheck(c, seated).ok]) for e in members[1:]}
    return {"region": region, "seated": blocker, "measured_inside": inside,
            "v1_clear": dict(v1_clear), "clear_after": clear,
            "ok": bool(inside == [blocker] and all(clear[e] < v1_clear[e] for e in clear))}


# --------------------------------------------------------------- preflight ----
def preflight(case: TaskCase) -> dict:
    """Is the authored task physically open, sayable and actually coupled at v1? (SPEC 11.1.)

    Three independent refusals. `understanding` is `_understanding`'s claim that the utterance binds
    to exactly the hidden truth. The geometric half is that every target/region pair the truth asks
    for must have at least one placement candidate that clears the production re-check in the
    *initial* scene, and every object must have at least one grasp candidate. `shared_region` is
    `_resource_probe`'s claim that the case's own shared region gets measurably tighter as its own
    objects are seated — without that, "several objects compete for one region" is a description of
    an empty constraint.

    This is not a promise that the episode can be planned: a case may still fail because of the
    dependency between its own placements. It is the check that rules out a case that was never
    possible. It reads the scene through `SkillExecutor`'s own planner, so the numbers are the ones
    the runtime would cite.
    """
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects, gui=False)
    try:
        executor = SkillExecutor(scene, world_provider=lambda: None)
        world = build_world_state(scene, 1, "obs_0001")
        executor.world_provider = lambda: world
        planner = executor.placement_planner
        want: dict[str, set[str]] = {}
        for a in case.eval_spec.assignments:
            want.setdefault(a.entity_id, set()).add(a.target_id)
        per_object = {}
        for eid in sorted(want):
            slots = {t: len([c for c in planner.generate(t, eid, world, limit=None)
                             if planner.recheck(c, world).ok]) for t in sorted(want[eid])}
            per_object[eid] = {"clear_slots": slots,
                               "grasp_candidates": len(scene.grasp_candidates(eid))}
        understood = _understanding(case, world)
        region, members = _shared_region(case)
        probe = _resource_probe(scene, case, planner,
                                {e: per_object[e]["clear_slots"][region] for e in members
                                 if e in per_object})
        return {"ok": (understood["ok"] and probe["ok"]
                       and all(min(v["clear_slots"].values()) >= 1 and v["grasp_candidates"] >= 1
                               for v in per_object.values())),
                "understanding": understood,
                "shared_region": probe,
                "per_object": per_object,
                "occupancy_v1": {t: sorted(o.entity_id for o in world.occupancy
                                           if o.target_id == t) for t in TRAYS}}
    finally:
        scene.close()


# ------------------------------------------------------------------ manifest ---
def lh_manifest() -> dict:
    """The lh set, hash-addressed, exactly the way `frozen_manifest` does for the frozen 47.

    Deliberately a *different* file with a *different* hash: the long-horizon set is authored after
    the frozen results exist, and folding it into the same manifest would move a hash that published
    numbers already depend on (SPEC 12.1).
    """
    cases = build_long_horizon_set()
    payload = {
        "schema_version": "1",
        "spec": "v0.2 §8",
        "set": LH_SET_NAME,
        "budget_rationale": ("declared before any run: 20 nominal skill calls for the longest case "
                             "(12 placements + 2 restore + 4 retry), 30 rounds / 40 calls with "
                             "slack, identical for every arm"),
        "budgets": LH_BUDGET,
        "spec8_shape_2_reachability": SHAPE_2_MEASURED,
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


def dump_lh_frozen(path: str = LH_FROZEN_PATH) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lh_manifest(), f, ensure_ascii=False, indent=1, sort_keys=True)
    return path


def check_lh_frozen(path: str = LH_FROZEN_PATH) -> tuple[bool, str]:
    """Self-consistency first, then agreement with the code (same two claims as `check_frozen`)."""
    if not os.path.exists(path):
        return False, f"no long-horizon manifest at {path}"
    with open(path, encoding="utf-8") as f:
        recorded = json.load(f)
    embedded = str(recorded.pop("sha256", ""))
    blob = json.dumps(recorded, ensure_ascii=False, sort_keys=True, indent=1)
    live = lh_manifest()
    if hashlib.sha256(blob.encode("utf-8")).hexdigest() != embedded:
        return False, "the long-horizon file does not hash to its own recorded value"
    if embedded == live["sha256"]:
        return True, live["sha256"][:12]
    return False, (f"long-horizon set drifted: frozen {embedded[:12]} != live {live['sha256'][:12]}")


if __name__ == "__main__":
    import sys

    if "--check" in sys.argv:
        ok, msg = check_lh_frozen()
        print(("lh frozen OK " if ok else "lh frozen MISMATCH ") + msg)
        raise SystemExit(0 if ok else 1)
    if "--dump" in sys.argv:
        print(dump_lh_frozen())
        raise SystemExit(0)
    for c in build_long_horizon_set():
        print(json.dumps({"task_id": c.task_id, "n": c.n_objects, "events": len(c.events),
                          "preflight": preflight(c)["ok"]}, ensure_ascii=False))
