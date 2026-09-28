"""P2-d dev probe #3: which occupant layout makes a tray infeasible *and* stays graspable?

The two preceding probes fixed the ends of the question. `p2d_pick_from_tray.py` measured that a
body seated inside a tray can be lifted out — every graspable size, in two different trays, at the
centre and off-centre — and that the failure is a width limit, not a region limit (a 100 mm body
misses the grasp anywhere; an 80 mm one misses it on the table but is held inside a tray, where the
floor seats it 8 mm higher). `p2d_reach.py` measured the other end: a *single* occupant blocks the
middle tray for a small victim only at half-extents >= 0.05 m, which is precisely the size that
cannot be grasped. So neither end can carry §8 alone, and the answer has to be found between them:
several graspable occupants, whose hand-sweep envelopes together leave no clear slot.

Phase 1 is feasibility arithmetic only — `PlacementPlanner.generate` + `recheck`, the same calls the
planner makes before it claims a region is blocked (`planning/subgoals.py:245`), no action stepped.
It reports every layout where the victim currently has zero clear slots, which is the necessary
condition for a `recover` obligation to be authored at all.

Phase 2 takes those layouts and *does* the sequence with the production executor: lift one occupant
out into another region, place the victim, and try to bring the occupant back. Both directions have
to work for the task to be reachable, and only the second one is surprising — an occupant that
cannot return to a tray it started in is not a §8 case, it is a task the hardware cannot finish, and
authoring it would measure the author rather than the agent.

Read-only against the repository, writes nothing, and nothing here feeds a prompt or a
DecisionContext.
"""
import itertools
import json
import sys

from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.tasks import DIMS, PALETTE, TRAYS
from embodied_agent.planning.subgoals import Instruments

TRAY_XY = {"tray_left": (0.66, -0.32), "tray_middle": (0.66, 0.0), "tray_right": (0.66, 0.32)}
# every occupant size here was measured graspable inside a tray by probe #2
OCCUPANT = {"cube21": ("cube", DIMS["cube"]), "cuboid": ("cuboid", DIMS["cuboid"]),
            "cyl": ("cylinder", DIMS["cylinder"]), "wide40": ("cuboid", [0.040, 0.040, 0.020])}
VICTIM = {"cube21": ("cube", DIMS["cube"]), "cuboid": ("cuboid", DIMS["cuboid"]),
          "cyl": ("cylinder", DIMS["cylinder"]), "wide40": ("cuboid", [0.040, 0.040, 0.020])}
TABLE = [(0.38, -0.16), (0.38, 0.0), (0.38, 0.16), (0.445, -0.16), (0.445, 0.0)]
# offsets from the tray centre, along y: the region is 0.135 m across, so two bodies at +/-0.045
# are as far apart as the walls allow and a third has to sit between them
OFFSETS = [0.0, 0.045, -0.045, 0.0225, -0.0225]
COLOURS = ["red", "green", "blue", "yellow", "purple"]


def say(row):
    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)


def bodies(pattern, victim_shape):
    """pattern: list of (occupant_size, offset). The victim is authored on the table."""
    out = []
    for i, (size, off) in enumerate(pattern):
        shape, dims = OCCUPANT[size]
        out.append({"entity_id": f"obj_{COLOURS[i]}_{i + 1}", "shape": shape, "dims": list(dims),
                    "xy": [TRAY_XY["tray_middle"][0], TRAY_XY["tray_middle"][1] + off],
                    "color": PALETTE[COLOURS[i]],
                    "attributes": {"color": COLOURS[i], "shape": shape}})
    shape, dims = VICTIM[victim_shape]
    out.append({"entity_id": "obj_victim", "shape": shape, "dims": list(dims),
                "xy": list(TABLE[0]), "color": PALETTE["purple"],
                "attributes": {"color": "purple", "shape": shape}})
    return out


def open_scene(layout):
    """A scene whose world is re-measured on every request.

    The first version of this probe handed the executor a captured v1 snapshot, and that silently
    changed the question: `place` re-checks its candidate against `world_provider()`, so a stale
    world makes the runtime's own feasibility arithmetic blind to whatever the previous action just
    did. The provider below is live for the same reason the production runtime's is.
    """
    scene = PhysicsScene(seed=7, object_layout=layout, gui=False)
    box = {"n": 1}

    def provider():
        return build_world_state(scene, box["n"], f"obs_{box['n']:04d}")

    executor = SkillExecutor(scene, world_provider=provider)
    ins = Instruments(executor.placement_planner, provider())
    return scene, executor, ins, box


def refresh(scene, ins, box, n):
    """Advance the measurement clock and re-read the world into the planner's view."""
    box["n"] = n
    ins.world = build_world_state(scene, n, f"obs_{n:04d}")
    return ins.world


def blocked_layouts():
    """Phase 1: every (occupant pattern, victim) pair whose region has no clear slot.

    Deliberately small. Two occupants is the largest set worth sweeping because a third makes the
    region's own capacity the obstacle rather than its geometry (`MAX_OBJECTS_PER_REGION = 3` counts
    the task objects that must end there), and the sizes are the three that a tray can actually
    receive. `wide40` alone is kept as the control that probe #1 already measured at 4 clear slots.
    """
    sizes = ("cuboid", "cyl", "wide40")
    patterns = [[(s, 0.0)] for s in sizes]
    patterns += [[(a, o1), (b, o2)] for a in sizes for b in sizes
                 for o1, o2 in ((0.045, -0.045), (0.0, -0.045), (0.0, 0.0225), (-0.045, 0.045))]
    found = []
    for pattern in patterns:
        for victim in VICTIM:
            scene, _, ins, _box = open_scene(bodies(pattern, victim))
            try:
                f = ins.feasible("obj_victim", "tray_middle")
                row = {"pattern": [[s, round(o, 4)] for s, o in pattern], "victim": victim,
                       "clear": len(f.ok), "offered": f.offered, "feasible": f.feasible,
                       "reason": (f.first_reason or "")[:110]}
            except Exception as e:
                row = {"pattern": [[s, round(o, 4)] for s, o in pattern], "victim": victim,
                       "error": f"{type(e).__name__}: {e}"}
            finally:
                scene.close()
            say(row)
            if not row.get("feasible", True) and not row.get("error"):
                found.append((pattern, victim))
    return found


def run_sequence(pattern, victim):
    """Phase 2: lift one occupant out, seat the victim, bring the occupant back.

    Statuses are read off `.value`, not `str(...)`: `SkillStatus` subclasses `str`, but `str()` of a
    member renders the *qualified* name, so the first draft of this probe compared
    `"SkillStatus.completed" != "completed"` and returned after the very first pick on the layouts
    that had actually succeeded. That is a measurement bug worth its own line in the phase log.
    """
    names = [f"obj_{COLOURS[i]}_{i + 1}" for i in range(len(pattern))]
    scene, executor, ins, box = open_scene(bodies(pattern, victim))
    row = {"pattern": [[s, round(o, 4)] for s, o in pattern], "victim": victim,
           "moved": names[0]}
    try:
        # the occupant leaves for another region; the destination is chosen by the same rule the
        # executor uses when a decision names no slot, so no geometry is invented here
        pick = executor.execute(SkillCall(plan_id="p", step_id="1", skill="pick",
                                         args={"object_id": names[0]}))
        row["lift_out"] = pick.status.value
        if pick.status.value != "completed":
            return row
        refresh(scene, ins, box, 2)
        scan = {t: len(ins.feasible(names[0], t).ok) for t in ("tray_left", "tray_right")}
        dest = next((t for t in ("tray_left", "tray_right")
                     if ins.feasible(names[0], t).feasible), None)
        row["temp_destination"] = dest
        row["temp_scan_while_held"] = scan
        if dest is None:
            return row
        place = executor.execute(SkillCall(plan_id="p", step_id="2", skill="place",
                                          args={"object_id": names[0], "target_id": dest}))
        row["place_temp"] = place.status.value
        row["place_temp_code"] = place.failure_code
        if place.status.value != "completed":
            return row
        after = ins.feasible("obj_victim", "tray_middle")
        row["victim_after_lift"] = {"feasible": after.feasible, "clear": len(after.ok)}
        if not after.feasible:
            return row
        vp = executor.execute(SkillCall(plan_id="p", step_id="3", skill="pick",
                                        args={"object_id": "obj_victim"}))
        row["victim_pick"] = vp.status.value
        if vp.status.value != "completed":
            return row
        refresh(scene, ins, box, 3)
        seated = executor.execute(SkillCall(plan_id="p", step_id="4", skill="place",
                                           args={"object_id": "obj_victim",
                                                 "target_id": "tray_middle"}))
        row["victim_seated"] = seated.status.value
        row["victim_seated_code"] = seated.failure_code
        if seated.status.value != "completed":
            return row
        refresh(scene, ins, box, 4)
        back = ins.feasible(names[0], "tray_middle")
        row["restore_feasible"] = {"feasible": back.feasible, "clear": len(back.ok),
                                   "reason": (back.first_reason or "")[:110]}
        if not back.feasible:
            return row
        retreat = executor.execute(SkillCall(plan_id="p", step_id="5", skill="safe_retreat",
                                            args={}))
        row["retreat"] = retreat.status.value
        p2 = executor.execute(SkillCall(plan_id="p", step_id="6", skill="pick",
                                        args={"object_id": names[0]}))
        row["restore_pick"] = p2.status.value
        if p2.status.value != "completed":
            return row
        refresh(scene, ins, box, 5)
        r2 = executor.execute(SkillCall(plan_id="p", step_id="7", skill="place",
                                       args={"object_id": names[0], "target_id": "tray_middle"}))
        row["restore_place"] = r2.status.value
        row["restore_place_code"] = r2.failure_code
        world = refresh(scene, ins, box, 6)
        row["final_in_tray_middle"] = sorted(o.entity_id for o in world.occupancy
                                             if o.target_id == "tray_middle")
        row["reachable"] = (r2.status.value == "completed"
                            and set(row["final_in_tray_middle"]) == {"obj_victim", names[0]})
        return row
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
        return row
    finally:
        scene.close()


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "1"
    if stage == "1":
        found = blocked_layouts()
        print(f"\n== {len(found)} layouts block the middle tray ==")
        for pattern, victim in found[:40]:
            print(f"  {victim:8} <- blocked by {pattern}")
        with open("/tmp/qoder-remote-worker-nSv81L/p2d_blocked_layouts.json", "w") as fh:
            json.dump([{"pattern": p, "victim": v} for p, v in found], fh, indent=1)
    else:
        with open("/tmp/qoder-remote-worker-nSv81L/p2d_blocked_layouts.json") as fh:
            found = json.load(fh)
        # a `wide40` victim is dropped without measuring: probe #2 found an 80 mm body misses its
        # grasp on the table, so such a task could never be completed whatever the tray does
        found = [c for c in found if c["victim"] in ("cube21", "cuboid", "cyl")]
        print(f"== {len(found)} blocked layouts whose victim is graspable at all ==")
        rows = [run_sequence([tuple(x) for x in c["pattern"]], c["victim"]) for c in found]
        print("\n== reachable end to end ==")
        for r in rows:
            print(f"  {r.get('victim')} {r['pattern']} lift={r.get('lift_out')} "
                  f"moved={r.get('moved')} seat={r.get('victim_seated')} "
                  f"restore={r.get('restore_feasible', {}).get('feasible')} "
                  f"back={r.get('restore_pick')}/{r.get('restore_place')} "
                  f"reachable={r.get('reachable')} err={r.get('error')}")
        with open("/tmp/qoder-remote-worker-nSv81L/p2d_sequences.json", "w") as fh:
            json.dump(rows, fh, indent=1, default=str)
