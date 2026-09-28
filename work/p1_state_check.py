"""P1-c world-state check: what a percept-derived `WorldState` may claim, measured.

Zero model cost. `StubReader` + `PerceptionAssembler` produce the percepts,
`embodied_agent/perception/world_state.py` turns them into v0.1 `WorldState` snapshots,
and the *privileged* channel appears only in two roles: as the answer key a claim is
graded against, and as the hand that moves a body when a measurement needs a change to
have really happened. Every number reported below is read back out of the snapshot the
pixels produced.

Six questions, in the order the phase log answers them:

1. **Claim boundary.** Field by field, what does the percept leave `unknown` where the
   privileged snapshot answers, and how much wider does the orientation-invariant
   footprint have to be than the upright one? A widening large enough to flip
   `fully_inside` would mean a percept may never claim a placement by footprint.
2. **Grounding parity.** Do the words this case's own utterance uses bind to the same
   body on both channels? `only_percept` is the dangerous direction — a binding to
   something the answer key denies — so it is counted separately from a miss.
3. **Change ledger.** Command a displacement from a fixed base pose and ask whether the
   percept's `ChangeRecord`s and a diff of the two snapshots say the same thing.
   `unreported_differences` is §11's state-change metric and must stay 0.
4. **View cycle.** Three views of a table that did not move, then a placement made while
   the agent was looking elsewhere. What does the change channel report, and what does
   the one comparison it cannot decline recover?
5. **Placement parity.** Seat each body in a tray and compare the two channels'
   `supported_by` and `fully_inside`, plus the millimetres between their rest points. This
   is the number P1-d's visual verification has to be built on.
6. **Bulk grounding.** The same parity test as (2) over a whole frozen case set, so the
   claim is about the task distribution and not about one fixture.

Run:  PYTHONPATH=. python work/p1_state_check.py [case_id] [--sweep smoke|dev|formal|all]
"""
from __future__ import annotations

import ast
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pybullet as p                                                  # noqa: E402

from embodied_agent.core.contracts import (                             # noqa: E402
    TaskInput,
    WorldState,
    footprint_half_xy_from_quat,
    orientation_invariant_footprint_half_xy,
)
from embodied_agent.core.interpreter import interpret                   # noqa: E402
from embodied_agent.core.scene import PhysicsScene                        # noqa: E402
from embodied_agent.core.verify import (                                # noqa: E402
    build_world_state,
    entity_footprint_half_xy,
    entity_position,
)
from embodied_agent.evaluation.calibration import (                     # noqa: E402
    cell_catalog,
    task_context_of,
)
from embodied_agent.evaluation.tasks import build_set, find_case        # noqa: E402
from embodied_agent.perception.frames import camera_for, capture        # noqa: E402
from embodied_agent.perception.observe import (                         # noqa: E402
    PerceptionAssembler,
    StubReader,
    TaskContext,
)
from embodied_agent.perception.world_state import (                     # noqa: E402
    ASSUMED_UPRIGHT,
    UNCLAIMED_ATTRIBUTES,
    PerceptWorldStateTracker,
    cross_view_check,
    reconcile,
    state_differences,
    world_state_from_percept,
)

OUT = "/tmp/v02_p1_state"
VIEWS = ("main", "front_high", "overhead")
DISPLACEMENTS_MM = (5, 10, 15, 20, 25, 30, 40, 60)
UNANSWERABLE = ("held", "at_rest", "linear_speed_mps", "angular_speed_rps")


# ------------------------------------------------------------------ helpers ----


def truth(scene: PhysicsScene) -> dict[str, dict]:
    """The privileged answer key, for grading only: which body is which colour, what
    shape it really is, and where it really stands."""
    rows = {}
    for eid, d in scene.objects.items():
        pos, _orn = scene.object_pose(eid)
        rows[str(d["attributes"]["color"])] = {
            "entity_id": eid, "shape": str(d["attributes"]["shape"]),
            "xy": [pos[0], pos[1]]}
    return rows


def look(scene, catalog, reader, assembler, name: str, view: str, *, task=None,
         previous=None, index: int = 0):
    """A frame, a reading of it, and the percept it assembles into. Pixels only."""
    frame, _, _ = capture(scene, OUT, camera_for(view), name, sim_time=scene.sim_time)
    reading = reader.read(frame, catalog=catalog, task=task)
    return frame, reading, assembler.assemble(frame, reading, previous=previous,
                                              frame_index=index)


def mm(pair) -> list[float]:
    return [round(float(v) * 1000, 1) for v in pair]


def bound_pairs(goal, world) -> set:
    """`(colour, target_id)` for every assignment, resolved against this snapshot.

    An ambiguous binding is reported as its own pair value rather than dropped, because
    "bound to 2 entities" and "bound to none" are different failures of the state layer
    and the report should be able to tell them apart."""
    out = set()
    for a in goal.assignments:
        hits = [e for e in world.entities
                if a.entity.entity_id == e.entity_id
                or (a.entity.attributes and all(
                    e.attributes.get(k) == v for k, v in a.entity.attributes.items()))]
        if len(hits) != 1:
            out.add((f"<{len(hits)} hits {a.entity.attributes or a.entity.entity_id}>",
                     a.target_id))
            continue
        out.add((str(hits[0].attributes.get("color")), a.target_id))
    return out


def seat(scene, entity_id: str, target_id: str, dx: float, dy: float) -> dict:
    """Put a body in a tray with the simulator's own hand, and report where it settled."""
    d = scene.objects[entity_id]
    tray = scene.trays[target_id]
    z = float(tray["floor_top"]) + float(d["geometry"].half_h_vertical)
    xy = [float(tray["center"][0]) + dx, float(tray["center"][1]) + dy]
    p.resetBasePositionAndOrientation(d["body"], [xy[0], xy[1], z],
                                      p.getQuaternionFromEuler([0, 0, 0.05],
                                                               physicsClientId=scene.cid),
                                      physicsClientId=scene.cid)
    for _ in range(40):
        p.stepSimulation(physicsClientId=scene.cid)
    scene.sim_time += 40 * 0.01
    pos, _orn = scene.object_pose(entity_id)
    return {"entity_id": entity_id, "target_id": target_id,
            "colour": str(d["attributes"]["color"]),
            "rest_xy": [round(pos[0], 4), round(pos[1], 4)]}


def rest_in(scene: PhysicsScene, xy) -> str:
    """Which declared region this ground point really sits inside (grading only)."""
    for tid, t in scene.trays.items():
        if (abs(xy[0] - t["center"][0]) <= t["inner_half"]
                and abs(xy[1] - t["center"][1]) <= t["inner_half"]):
            return tid
    return "table"


# ------------------------------------------------------------ 1. claim boundary ----


def claim_boundary(scene, states: dict[str, WorldState], key: dict) -> dict:
    """Per body: what the picture answered, what it refused, and what it had to assume."""
    privileged = build_world_state(scene, 1, "obs_static")
    by_colour = {str(e.attributes.get("color")): e for e in privileged.entities}
    rows, widening = [], []
    for view, state in states.items():
        for e in state.entities:
            colour = str(e.attributes.get("color"))
            pri = by_colour.get(colour)
            carried = None if e.geometry is None else entity_footprint_half_xy(e)
            assumed = None if e.geometry is None else footprint_half_xy_from_quat(
                e.geometry, ASSUMED_UPRIGHT)
            invariant = None if e.geometry is None else \
                orientation_invariant_footprint_half_xy(e.geometry)
            if carried is not None:
                widening.append({
                    "view": view, "entity_id": e.entity_id,
                    "shape": e.attributes.get("shape"),
                    "carried_half_mm": mm(carried),
                    "upright_assumption_half_mm": mm(assumed),
                    "widening_mm": round((carried[0] - assumed[0]) * 1000, 1),
                    "invariant_bound_is_the_carried_one": carried == invariant,
                    "truth_half_mm": (None if pri is None else
                                      mm(entity_footprint_half_xy(pri))),
                })
            positioned = e.pose is not None
            rows.append({
                "view": view,
                "entity_id": e.entity_id,
                "colour": colour,
                "shape_seen": e.attributes.get("shape"),
                "shape_truth": (key.get(colour) or {}).get("shape"),
                "position_error_mm": (None if not positioned or pri is None else round(
                    math.hypot(entity_position(e)[0] - pri.pose.position.x,
                               entity_position(e)[1] - pri.pose.position.y) * 1000, 2)),
                "percept_leaves_unknown": sorted(
                    f for f in UNANSWERABLE if getattr(e, f) in ("unknown", None)),
                "privileged_answers": sorted(
                    f for f in UNANSWERABLE if pri is not None
                    and getattr(pri, f) not in ("unknown", None)),
                "orientation_measured": e.orientation_measured,
                "supported_by_percept": e.supported_by,
                "supported_by_truth": None if pri is None else pri.supported_by,
                "rest_in_truth": (None if pri is None
                                  else rest_in(scene, entity_position(pri)[:2])),
            })
    errs = sorted(r["position_error_mm"] for r in rows if r["position_error_mm"] is not None)
    return {
        "bodies": rows,
        "footprint_widening": widening,
        "widening_mm_max": max((w["widening_mm"] for w in widening), default=None),
        "widening_mm_median": (round(float(np.median([w["widening_mm"] for w in widening])), 1)
                               if widening else None),
        "carried_bound_is_not_the_invariant_one": [
            w["entity_id"] for w in widening if not w["invariant_bound_is_the_carried_one"]],
        "percept_relations_disagreeing_with_truth": [
            [r["view"], r["entity_id"], r["supported_by_percept"], r["rest_in_truth"]]
            for r in rows if r["supported_by_percept"] != r["rest_in_truth"]],
        "position_error_mm": {"n": len(errs),
                              "median": round(float(np.median(errs)), 2) if errs else None,
                              "max": errs[-1] if errs else None,
                              "all": errs},
        "fields_no_channel_but_privileged_answers": sorted({
            f for r in rows for f in r["percept_leaves_unknown"]}),
        "fields_privileged_always_answers": sorted(set(UNANSWERABLE) & {
            f for r in rows for f in r["privileged_answers"]}),
    }


# -------------------------------------------------------------- 2. grounding ----


def grounding_probes(case, catalog, states: dict[str, WorldState], privileged) -> dict:
    """Every phrase this case could be asked with, through the real interpreter.

    The utterance is the one the episode actually used. The colour and colour+shape
    phrases are probes: §11's grounding accuracy is about whether an attribute reference
    finds its body, and a sentence that names colours only would leave the shape half of
    the vocabulary unmeasured.
    """
    colours = sorted({str(d["attributes"]["color"]) for d in case.objects})
    shapes = {str(d["attributes"]["color"]): str(d["attributes"]["shape"])
              for d in case.objects}
    phrases = [case.utterance] + [catalog.word_for_attributes({"color": c}) for c in colours]
    phrases += [catalog.word_for_attributes({"color": c, "shape": shapes[c]}) for c in colours]
    probes, tally = [], {}
    for phrase in dict.fromkeys(phrases):
        utterance = phrase if phrase == case.utterance else f"把{phrase}放入中间盘"
        priv_goal = interpret(TaskInput(task_id=case.task_id, utterance=utterance), privileged)
        priv_pairs = sorted(bound_pairs(priv_goal, privileged))
        row = {"utterance": utterance, "privileged_pairs": priv_pairs,
               "privileged_ambiguous": priv_goal.ambiguous, "views": {}}
        for view, state in states.items():
            goal = interpret(TaskInput(task_id=case.task_id, utterance=utterance), state)
            pairs = sorted(bound_pairs(goal, state))
            agreement = ("both" if pairs and pairs == priv_pairs else
                         "neither" if not pairs and not priv_pairs else
                         "only_percept" if pairs and not priv_pairs else "only_privileged")
            row["views"][view] = {"pairs": pairs, "ambiguous": goal.ambiguous,
                                  "clarification": goal.clarification_request,
                                  "agreement": agreement}
            tally[agreement] = tally.get(agreement, 0) + 1
        probes.append(row)
    return {"n_probes": len(probes), "tally": tally, "probes": probes,
            "simulator_id_in_percept_json": {
                view: "obj_" in state.model_dump_json() for view, state in states.items()}}


# ------------------------------------------------------------- 3. change ledger ----


def change_ledger(scene, catalog, reader, assembler, task, key) -> dict:
    """Command a displacement from a fixed base pose and diff the two snapshots.

    The base percept stays the baseline for every trial, so each row measures one move
    against one memory of the world — which is what `reconcile` is for. The trials below
    the threshold are the false-positive half of the same metric, and the repeated
    baseline is the control: a table that did not move must produce no difference at all.
    """
    colour, t = max(key.items(), key=lambda kv: abs(kv[1]["xy"][1]))
    entity_id, base_xy = t["entity_id"], list(t["xy"])
    rest_z = float(scene.objects[entity_id]["rest_z_table"])

    def place_and_perceive(xy, name, previous):
        d = scene.objects[entity_id]
        p.resetBasePositionAndOrientation(d["body"], [xy[0], xy[1], rest_z],
                                          p.getQuaternionFromEuler([0, 0, 0.05],
                                                                   physicsClientId=scene.cid),
                                          physicsClientId=scene.cid)
        for _ in range(40):
            p.stepSimulation(physicsClientId=scene.cid)
        scene.sim_time += 0.4
        pos, _orn = scene.object_pose(entity_id)
        _frame, _reading, obs = look(scene, catalog, reader, assembler, name, "main",
                                     task=task, previous=previous)
        return pos, obs

    base_pos, base_obs = place_and_perceive(base_xy, "chg_base", None)
    base_state = world_state_from_percept(base_obs, catalog, state_version=1)
    rows = []
    for i, delta_mm in enumerate(DISPLACEMENTS_MM, start=2):
        pos, obs = place_and_perceive([base_xy[0], base_xy[1] - delta_mm / 1000.0],
                                      f"chg_{delta_mm}", base_obs)
        state = world_state_from_percept(obs, catalog, state_version=i)
        audit = reconcile(base_state, state, obs)
        move_rows = [row for row in obs.changes
                     if row.subject_id == f"seen:{colour}" and row.attribute == "position"]
        reported = None
        if move_rows:
            before = np.array(ast.literal_eval(move_rows[0].before), dtype=float)
            after = np.array(ast.literal_eval(move_rows[0].after), dtype=float)
            reported = round(float(math.hypot(*(after[:2] - before[:2])) * 1000.0), 2)
        rows.append({
            "commanded_mm": delta_mm,
            "true_mm": round(math.hypot(pos[0] - base_pos[0], pos[1] - base_pos[1]) * 1000, 2),
            "state_differences": audit["state_differences"],
            "reported_changes": audit["reported_changes"],
            "position_rows_not_carried_by_the_state":
                audit["position_rows_not_carried_by_the_state"],
            "unreported_differences": audit["unreported_differences"],
            "rows_without_a_state_difference": audit["rows_without_a_state_difference"],
            "unclaimed_attribute_differences": audit["unclaimed_attribute_differences"],
            "reported_delta_mm": reported,
            "delta_error_mm": None if reported is None else round(reported - delta_mm, 2),
        })
    control = reconcile(base_state, base_state, base_obs)
    threshold_mm = assembler.move_threshold_m * 1000
    under = [r for r in rows if r["commanded_mm"] < threshold_mm]
    over = [r for r in rows if r["commanded_mm"] >= threshold_mm]
    return {
        "colour": colour, "entity_id": entity_id, "move_threshold_m": assembler.move_threshold_m,
        "still_world_control": {k: control[k] for k in
                                ("state_differences", "unreported_differences",
                                 "rows_without_a_state_difference")},
        "rows": rows,
        "unreported_total": sum(len(r["unreported_differences"]) for r in rows),
        "spurious_rows_total": sum(len(r["rows_without_a_state_difference"]) for r in rows),
        "unclaimed_differences_total": sum(len(r["unclaimed_attribute_differences"])
                                           for r in rows),
        "missed_position_rows_over_threshold": sum(
            1 for r in over
            if not any(tag.endswith(":position")
                       for tag in r["position_rows_not_carried_by_the_state"])),
        "position_rows_while_under_threshold": sum(
            1 for r in under
            if any(tag.endswith(":position")
                   for tag in r["position_rows_not_carried_by_the_state"])),
        "median_delta_error_mm": (round(float(np.median(
            [r["delta_error_mm"] for r in over if r["delta_error_mm"] is not None])), 2)
            if any(r["delta_error_mm"] is not None for r in over) else None),
    }


# --------------------------------------------------------------- 4. view cycle ----


def view_cycle(scene, catalog, reader, assembler, task, key) -> dict:
    """Every view looked at twice — then a placement made while the agent looked away.

    Two looks per view are what make the still-table half of this mean something: the
    second one is a same-view repeat, so its `ChangeRecord`s are the noise floor of the
    change channel and its audit is a `reconcile`. The legs between views are cross-view,
    where the assembler reports nothing by design.

    The last three steps are P1-b's residual 5 turned into a measurement. `main` → move →
    `overhead` is a real event the change channel is blind to, and the question is whether
    the one comparison that may cross viewpoints recovers it.
    """
    tracker = PerceptWorldStateTracker(catalog, episode_id="p1c_cycle")
    versions, legs = [], []

    def step(view, name):
        frame, reading, _ = look(scene, catalog, reader, assembler, name, view, task=task)
        percept, state = tracker.perceive(assembler, frame, reading)
        versions.append(state.state_version)
        audit = tracker.audits[-1] if tracker.audits else None
        if audit is None:
            return None
        cross = "fields_declined" in audit
        was = str(audit.get("previous_camera_id") or view) if cross else view
        return {"leg": f"{was}->{view}", "kind": "cross_view" if cross else "same_view",
                "reported_changes": audit["reported_changes"],
                "unreported_differences": audit["unreported_differences"],
                "cross_view_disagreements": audit["cross_view_disagreements"],
                "fields_declined": sorted(audit.get("fields_declined", {})),
                "bodies_compared": audit.get("bodies_compared")}

    for view in VIEWS:
        for repeat in range(2):
            row = step(view, f"cycle_{view}_{repeat}")
            if row:
                legs.append(row)
    still_legs = list(legs)
    # everything above this line is a still table: the sums below must not be charged to
    # the placement made underneath them, or the control measures the treatment
    n_still_audits = len(tracker.audits)

    cubes = [c for c, t in key.items() if t["shape"] == "cube"]
    colour = cubes[0] if cubes else sorted(key)[0]
    step("main", "blind_from_main")
    before = tracker.state
    seat(scene, key[colour]["entity_id"], "tray_middle", 0.0, 0.0)
    step("overhead", "blind_after")
    after = tracker.state
    recorded = tracker.audits[-1]
    direct = cross_view_check(before, after, view="overhead", previous_view="main")
    compared = ("cross_view_disagreements", "fields_compared", "bodies_compared")
    still = tracker.audits[:n_still_audits]
    return {
        "versions": versions,
        "versions_monotonic": versions == sorted(set(versions)),
        "n_audits": len(tracker.audits),
        "n_still_audits": n_still_audits,
        "still_table_unreported": sum(len(a["unreported_differences"]) for a in still),
        "still_table_disagreements": sum(len(a["cross_view_disagreements"]) for a in still),
        "still_table_reported_changes": sum(len(a["reported_changes"]) for a in still),
        "legs": still_legs,
        "blind_spot": {
            "colour": colour, "entity_id": key[colour]["entity_id"],
            "where_it_really_went": "tray_middle",
            "where_main_had_left_it": before.entity(f"seen:{colour}").supported_by,
            "where_overhead_says": after.entity(f"seen:{colour}").supported_by,
            "change_rows_the_assembler_reported": recorded["reported_changes"],
            "cross_view_disagreements": recorded["cross_view_disagreements"],
            "fields_declined": sorted(recorded.get("fields_declined", {})),
            "tracker_row_matches_the_direct_call": all(
                recorded.get(k) == direct[k] for k in compared),
            "naive_diff_would_have_claimed": [list(s) for s in
                                              state_differences(before, after)],
            "unseen_rows_from_overhead": [list(u) for u in tracker.unseen("overhead")],
        },
        "ledger_rows": len(tracker.ledger),
        "audits_with_unreported": [a["observation_id"] for a in tracker.unreported()],
        "audits_with_disagreements": [a["observation_id"] for a in tracker.disagreements()],
    }


# ----------------------------------------------------------- 5. placement parity ----


def placement_parity(scene, catalog, reader, assembler, task) -> dict:
    """Seat every body in a tray and ask both channels the same two questions.

    `supported_by` is the relation claim; `fully_inside` is the geometric one, and it is
    the one the upright assumption can reach. Both are reported per view, because a
    placement only one camera confirms is a different finding from one none of them sees.
    """
    rows = []
    for i, eid in enumerate(sorted(scene.objects)):
        info = seat(scene, eid, "tray_middle", -0.075 + 0.05 * i, 0.0)
        privileged = build_world_state(scene, 1, f"obs_seat_{i}")
        pri = privileged.entity(eid)
        pri_rec = next((o for o in privileged.occupancy if o.entity_id == eid), None)
        per_view = {}
        for view in VIEWS:
            _frame, _reading, obs = look(scene, catalog, reader, assembler,
                                         f"seat_{i}_{view}", view, task=task)
            state = world_state_from_percept(obs, catalog, state_version=1)
            eid_seen = f"seen:{info['colour']}"
            ent = state.entity(eid_seen) if state.has_entity(eid_seen) else None
            rec = next((o for o in state.occupancy if o.entity_id == eid_seen), None)
            per_view[view] = {
                "seen": ent is not None,
                "supported_by": None if ent is None else ent.supported_by,
                "relation_right": bool(ent is not None and ent.supported_by == "tray_middle"),
                "fully_inside": None if rec is None else rec.fully_inside,
                "occupancy_matches_privileged": (rec is not None and pri_rec is not None
                                                 and rec.fully_inside == pri_rec.fully_inside),
                # the only three outcomes that matter, and they are not symmetric: a
                # `declined` or `conservative` miss costs a planned move, a `dangerous`
                # one tells the agent a placement succeeded when it did not.
                "verdict": ("no_entity" if ent is None else
                            "declined" if rec is None or ent.supported_by != "tray_middle"
                            else "dangerous" if (pri_rec and rec.fully_inside
                                                 and not pri_rec.fully_inside)
                            else "conservative" if (pri_rec and pri_rec.fully_inside
                                                     and not rec.fully_inside)
                            else "agrees"),
                "rest_xy_gap_mm": (None if ent is None or ent.pose is None else round(
                    math.hypot(entity_position(ent)[0] - pri.pose.position.x,
                               entity_position(ent)[1] - pri.pose.position.y) * 1000, 2)),
                "carried_half_mm": (None if ent is None
                                    else mm(entity_footprint_half_xy(ent))),
                "privileged_half_mm": mm(entity_footprint_half_xy(pri)),
                "upright_half_mm": mm(footprint_half_xy_from_quat(pri.geometry, ASSUMED_UPRIGHT)),
            }
        rows.append({"colour": info["colour"], "entity_id": eid,
                     "truth_relation": pri.supported_by,
                     "truth_contacts": pri.support.contact_count,
                     "truth_fully_inside": None if pri_rec is None else pri_rec.fully_inside,
                     "views": per_view})
    return {"rows": rows,
            "per_view": {view: {
                "n": len(rows),
                "relations_right": sum(1 for r in rows if r["views"][view]["relation_right"]),
                "occupancy_matches": sum(1 for r in rows
                                         if r["views"][view]["occupancy_matches_privileged"]),
                "fully_inside_true": sum(1 for r in rows
                                         if r["views"][view]["fully_inside"] is True),
                "verdicts": {v: sum(1 for r in rows if r["views"][view]["verdict"] == v)
                             for v in sorted({r["views"][view]["verdict"] for r in rows})},
                "gaps_mm": sorted(g for g in (r["views"][view]["rest_xy_gap_mm"] for r in rows)
                                  if g is not None),
            } for view in VIEWS},
            "dangerous_verdicts": [[r["colour"], view] for r in rows for view in VIEWS
                                   if r["views"][view]["verdict"] == "dangerous"],
            "widening_cost_mm_per_tray": {
                "tray_inner_half_m": round(
                    float(next(t.inner_half for t in build_world_state(
                        scene, 1, "obs_geom").targets)), 4),
                "max_carried_half_mm": max(
                    max(v["carried_half_mm"][0] for v in r["views"].values())
                    for r in rows),
            }}


# ----------------------------------------------------------- 6. bulk grounding ----


def bulk_grounding(set_name, reader, assembler) -> dict:
    """The parity test of (2) over a whole frozen set, one view per case.

    Only `main` here on purpose: this block asks whether the task distribution is
    groundable from pixels, and a per-view answer belongs to (2) and (5), not to a sweep
    whose cost is one rendered scene per case."""
    rows = []
    for case in build_set(set_name):
        scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
        try:
            catalog = cell_catalog(scene.trays.values())
            task = TaskContext(**task_context_of(case))
            frame, _reading, obs = look(scene, catalog, reader, assembler,
                                        f"bulk_{case.task_id}", "main", task=task)
            state = world_state_from_percept(obs, catalog, state_version=1)
            privileged = build_world_state(scene, 1, f"obs_{case.task_id}")
            priv_goal = interpret(TaskInput(task_id=case.task_id, utterance=case.utterance),
                                  privileged)
            per_goal = interpret(TaskInput(task_id=case.task_id, utterance=case.utterance),
                                 state)
            priv_pairs = bound_pairs(priv_goal, privileged)
            per_pairs = bound_pairs(per_goal, state)
            truth_shape = {str(d["attributes"]["color"]): str(d["attributes"]["shape"])
                           for d in case.objects}
            seen_shape = {str(e.attributes.get("color")): str(e.attributes.get("shape"))
                          for e in state.entities}
            confusion = {f"{seen_shape[c]}_named_as_{truth_shape[c]}"
                         for c in truth_shape
                         if c in seen_shape and seen_shape[c] != truth_shape[c]}
            rows.append({
                "task_id": case.task_id, "n_objects": len(case.objects),
                "n_detected": len(obs.detections), "utterance": case.utterance,
                "privileged_ambiguous": priv_goal.ambiguous,
                "percept_ambiguous": per_goal.ambiguous,
                "pairs_match": priv_pairs == per_pairs,
                "n_pairs_privileged": len(priv_pairs), "n_pairs_percept": len(per_pairs),
                "privileged_pairs": sorted(map(list, priv_pairs)),
                "percept_pairs": sorted(map(list, per_pairs)),
                "percept_clarification": per_goal.clarification_request,
                "shapes_seen": seen_shape, "shapes_truth": truth_shape,
                "shape_confusion": sorted(confusion),
                "bodies_the_frame_did_not_see": sorted(
                    set(truth_shape) - set(seen_shape)),
                "no_simulator_id_in_snapshot": "obj_" not in state.model_dump_json(),
            })
        finally:
            scene.close()
    confusion_tally: dict[str, int] = {}
    for r in rows:
        for c in r["shape_confusion"]:
            confusion_tally[c] = confusion_tally.get(c, 0) + 1
    return {
        "set": set_name, "n_cases": len(rows),
        "pairs_match": sum(1 for r in rows if r["pairs_match"]),
        "both_ambiguous": sum(1 for r in rows
                              if r["percept_ambiguous"] and r["privileged_ambiguous"]),
        # `only_percept_ambiguous` is the safe direction — the picture asked a question
        # the answer key did not need. `only_privileged_ambiguous` would be the reverse,
        # and a non-zero count there means the state let a phrase bind to a body the
        # scene denies, which no amount of clarification can undo.
        "only_percept_ambiguous": [r["task_id"] for r in rows
                                   if r["percept_ambiguous"] and not r["privileged_ambiguous"]],
        "only_privileged_ambiguous": [r["task_id"] for r in rows
                                      if r["privileged_ambiguous"] and not r["percept_ambiguous"]],
        "shape_confusion": confusion_tally,
        "cases_with_an_unseen_body": [[r["task_id"], r["bodies_the_frame_did_not_see"]]
                                      for r in rows if r["bodies_the_frame_did_not_see"]],
        "bodies_per_case_detected": [r["n_detected"] for r in rows],
        "bodies_per_case_declared": [r["n_objects"] for r in rows],
        "ids_never_leak": all(r["no_simulator_id_in_snapshot"] for r in rows),
        "rows": rows,
    }


# ------------------------------------------------------------------------ main ----


def main() -> None:
    args = sys.argv[1:]
    sweep = "none"
    if "--sweep" in args:
        i = args.index("--sweep")
        sweep = args[i + 1] if i + 1 < len(args) else "dev"
        args = args[:i] + args[i + 2:]
    case_id = args[0] if args else "dev_c5"
    os.makedirs(OUT, exist_ok=True)

    case = find_case(case_id)
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    catalog = cell_catalog(scene.trays.values())
    assembler = PerceptionAssembler(catalog)
    reader = StubReader()
    task = TaskContext(**task_context_of(case))
    key = truth(scene)

    report = {
        "case": case_id, "seed": case.seed, "n_objects": len(key),
        "catalog": {"version": catalog.version, "sha256": catalog.sha256(),
                    "table_top_z": catalog.table_top_z,
                    "support_z_tol_m": catalog.support_z_tol_m},
        "assembler_thresholds": assembler.thresholds(),
        "unclaimed_attributes": list(UNCLAIMED_ATTRIBUTES),
        "views": {},
    }

    states: dict[str, WorldState] = {}
    for i, view in enumerate(VIEWS):
        _frame, _reading, obs = look(scene, catalog, reader, assembler, f"static_{view}",
                                     view, task=task, index=i)
        state = world_state_from_percept(obs, catalog, state_version=i + 1)
        states[view] = state
        report["views"][view] = {
            "frame_id": _frame.frame_id,
            "n_detections": len(obs.detections),
            "n_entities": len(state.entities),
            "n_located": sum(1 for e in state.entities if e.pose is not None),
            "occupancy": [[o.target_id, o.entity_id, o.fully_inside] for o in state.occupancy],
            "simulator_ids_leaked": "obj_" in state.model_dump_json(),
            "round_trip_lossless": state.model_dump() == WorldState.model_validate_json(
                state.model_dump_json()).model_dump(),
            "held_object": state.held_object,
            "sources": sorted({e.source.value for e in state.entities}),
            "body_ids": sorted({e.body_id for e in state.entities}),
            "change_rows": [[c.subject_id, c.attribute, c.modality] for c in obs.changes],
        }
    report["claim_boundary"] = claim_boundary(scene, states, key)
    report["grounding"] = grounding_probes(case, catalog, states,
                                           build_world_state(scene, 1, "obs_ground"))
    report["change_ledger"] = change_ledger(scene, catalog, reader, assembler, task, key)
    report["view_cycle"] = view_cycle(scene, catalog, reader, assembler, task, key)
    report["placement_parity"] = placement_parity(scene, catalog, reader, assembler, task)
    scene.close()
    if sweep != "none":
        report["bulk_grounding"] = bulk_grounding(sweep, reader, assembler)

    with open(os.path.join(OUT, "p1_state_check.json"), "w") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False, default=str)
    print_report(report)
    print(f"\nwrote {os.path.join(OUT, 'p1_state_check.json')}")


def print_report(report: dict) -> None:
    cb = report["claim_boundary"]
    print(f"case {report['case']}  {report['n_objects']} bodies  "
          f"catalog {report['catalog']['sha256'][:12]}")
    for view, v in report["views"].items():
        print(f"  [{view:10s}] detections {v['n_detections']}  entities {v['n_entities']}  "
              f"located {v['n_located']}  ids_leaked={v['simulator_ids_leaked']}  "
              f"round_trip={v['round_trip_lossless']}  occupancy {v['occupancy']}")
    print(f"\nclaim boundary: position error mm median "
          f"{cb['position_error_mm']['median']} max {cb['position_error_mm']['max']} "
          f"(n={cb['position_error_mm']['n']})")
    print(f"  always unknown to a camera: {cb['fields_no_channel_but_privileged_answers']}")
    print(f"  answered by the privileged read: {cb['fields_privileged_always_answers']}")
    print(f"  footprint widening from the upright assumption: median "
          f"{cb['widening_mm_median']} mm, max {cb['widening_mm_max']} mm; carried bound "
          f"not the invariant one: {cb['carried_bound_is_not_the_invariant_one']}")
    print(f"  relations disagreeing with the answer key: "
          f"{cb['percept_relations_disagreeing_with_truth']}")
    for row in cb["bodies"][:len(VIEWS)]:
        print(f"    {row['view']:10s} {row['entity_id']:11s} "
              f"unknown={','.join(row['percept_leaves_unknown']) or '-':34s} "
              f"err={str(row['position_error_mm']):>6s}mm orient_measured="
              f"{row['orientation_measured']} support={row['supported_by_percept']}"
              f"/{row['rest_in_truth']}")
    g = report["grounding"]
    print(f"\ngrounding: {g['n_probes']} probes, agreement tally {g['tally']}")
    print(f"  simulator ids in a percept snapshot: {g['simulator_id_in_percept_json']}")
    for row in g["probes"]:
        agree = {view: v["agreement"] for view, v in row["views"].items()}
        if set(agree.values()) != {"both"}:
            clar = {view: v["clarification"] for view, v in row["views"].items()
                    if v["clarification"]}
            print(f"    {row['utterance']}\n      privileged={row['privileged_pairs']} "
                  f"agree={agree}\n      clarifications={clar}")
    cl = report["change_ledger"]
    print(f"\nchange ledger on {cl['colour']} (threshold {cl['move_threshold_m'] * 1000:.0f} mm):"
          f" unreported {cl['unreported_total']}, spurious rows {cl['spurious_rows_total']},"
          f" unclaimed diffs {cl['unclaimed_differences_total']}, missed over threshold "
          f"{cl['missed_position_rows_over_threshold']}, position rows under threshold "
          f"{cl['position_rows_while_under_threshold']}, median delta error "
          f"{cl['median_delta_error_mm']} mm")
    print(f"  still-world control: {cl['still_world_control']}")
    for r in cl["rows"]:
        print(f"    {r['commanded_mm']:>3}mm (true {r['true_mm']:>5}) reported_delta="
              f"{str(r['reported_delta_mm']):>6}mm carried={r['state_differences']} "
              f"not_carried={r['position_rows_not_carried_by_the_state']} unreported="
              f"{r['unreported_differences']}")
    vc = report["view_cycle"]
    print(f"\nview cycle: {vc['n_audits']} audits ({vc['n_still_audits']} on a still table), "
          f"versions {vc['versions']} monotonic={vc['versions_monotonic']}, still-table "
          f"change rows {vc['still_table_reported_changes']}, still-table unreported "
          f"{vc['still_table_unreported']}, still-table disagreements "
          f"{vc['still_table_disagreements']}, ledger rows {vc['ledger_rows']}")
    for leg in vc["legs"]:
        print(f"    {leg['leg']:22s} {leg['kind']:11s} reported={leg['reported_changes']} "
              f"unreported={leg['unreported_differences']} disagreements="
              f"{leg['cross_view_disagreements']} declined={leg['fields_declined']}")
    b = vc["blind_spot"]
    print(f"  placement between views: {b['colour']} really in {b['where_it_really_went']}, "
          f"overhead says {b['where_overhead_says']} (main had left it "
          f"{b['where_main_had_left_it']}); change rows reported: "
          f"{b['change_rows_the_assembler_reported']}")
    print(f"    recovered across the one view-invariant field: "
          f"{b['cross_view_disagreements']}")
    print(f"    a naive cross-view diff would have claimed: "
          f"{b['naive_diff_would_have_claimed']}")
    print(f"    fields declined: {b['fields_declined']}  unseen rows: "
          f"{b['unseen_rows_from_overhead']}")
    pp = report["placement_parity"]
    print(f"\nplacement parity (dangerous verdicts — a claim of placed the answer key "
          f"denies — {pp['dangerous_verdicts']}); widest carried footprint "
          f"{pp['widening_cost_mm_per_tray']['max_carried_half_mm']} mm against a tray "
          f"inner half of "
          f"{pp['widening_cost_mm_per_tray']['tray_inner_half_m'] * 1000:.1f} mm")
    for view, a in pp["per_view"].items():
        print(f"  [{view:10s}] relations {a['relations_right']}/{a['n']}  occupancy matches "
              f"{a['occupancy_matches']}/{a['n']}  fully_inside true "
              f"{a['fully_inside_true']}  verdicts {a['verdicts']}  gaps mm {a['gaps_mm']}")
    if "bulk_grounding" in report:
        bg = report["bulk_grounding"]
        print(f"\nbulk grounding over {bg['set']}: pairs match {bg['pairs_match']}/"
              f"{bg['n_cases']}, both ambiguous {bg['both_ambiguous']}, only-percept-ambiguous "
              f"{len(bg['only_percept_ambiguous'])}, only-privileged-ambiguous "
              f"{bg['only_privileged_ambiguous']}, shape confusion {bg['shape_confusion']}, "
              f"cases with a body the frame did not see {bg['cases_with_an_unseen_body']}, "
              f"ids never leak {bg['ids_never_leak']}")
        for r in bg["rows"]:
            if not r["pairs_match"]:
                print(f"    {r['task_id']:24s} priv({r['n_pairs_privileged']})="
                      f"{r['privileged_pairs']}\n      percept({r['n_pairs_percept']})="
                      f"{r['percept_pairs']}\n      {r['percept_clarification']}")


if __name__ == "__main__":
    main()
