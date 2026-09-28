"""P2-d dev probe #5: does *any* §8 shape-2 layout survive its return leg?

Probes #1-#4 reduced §8's temporary-move-and-restore to one open question. A region goes infeasible
for a victim only when the occupants cover every candidate slot, and that was measured at two 80 mm
bodies — the forbid radius is the hand's 50 mm descent envelope plus the other body's footprint, so
one graspable body still leaves 15-17 clear slots. But an 80 mm body is at the edge of the actuator:
in the real sequence (probe #3, `p2d_sequences.json`) the lift failed on two of three arrangements,
and where it succeeded the release into a side tray came back `OBJECT_RELEASE_UNCONFIRMED`.

This probe spends no arm motion at all and adds the leg probe #3 never reached: after the victim is
seated in slot S, is there still a clear slot for the removed occupant to return to? A layout where
the answer is no is not a §8 task, it is a trap, and authoring it would measure the author rather
than the agent (`_respect_region_capacity` refuses the same kind of authoring error).

Bodies are moved with `resetBasePositionAndOrientation` + `settle`, i.e. allowed to come to rest
under the same gravity the skills act in, and every count below comes from the production
`PlacementPlanner.generate` + `recheck` reading measured poses. Nothing here is shown to a model and
nothing is written into the repository.
"""
import json

import pybullet as p

from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.tasks import DIMS, PALETTE

TRAY_X = 0.66
MIDDLE_Y = 0.0
TABLE_SLOTS = [[0.38, -0.16], [0.38, 0.0], [0.38, 0.16], [0.445, 0.16], [0.445, 0.0]]
SIZES = {
    "cube21": ("cube", [0.021, 0.021, 0.021]),
    "cuboid": ("cuboid", [0.027, 0.019, 0.035]),
    "cyl": ("cylinder", DIMS["cylinder"]),
    "wide40": ("cuboid", [0.040, 0.040, 0.020]),
}
COLOURS = ["red", "green", "blue", "yellow", "purple"]
FLOOR_Z = [None]  # read off the scene, never assumed


def say(row):
    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)


def put(scene, eid, xy):
    """Rest a body at `xy` in its own support plane, and let the solver settle it."""
    o = scene.objects[eid]
    p.resetBasePositionAndOrientation(
        o["body"], [xy[0], xy[1], FLOOR_Z[0] + o["half_h"] + 0.02], [0, 0, 0, 1],
        physicsClientId=scene.cid)
    p.resetBaseVelocity(o["body"], [0, 0, 0], [0, 0, 0], physicsClientId=scene.cid)
    scene.settle(1.0)


def body(eid, size, xy, colour):
    shape, dims = SIZES[size]
    return {"entity_id": eid, "shape": shape, "dims": list(dims), "xy": list(xy),
            "color": PALETTE[colour], "attributes": {"color": colour, "shape": shape}}


def scan(pattern, victim):
    """pattern: list of (size, dx, dy). Returns None unless the victim is blocked at v1.

    For each occupant that could be removed, the scan walks *every* victim slot that becomes clear
    and asks how many slots would still be clear for that occupant to come back to. `return_max` is
    the answer for the best victim slot, so a zero means no ordering of the real actions can close
    the loop and the layout is not authorable.
    """
    layout = [body(f"obj_p{i + 1}", s, [TRAY_X + dx, MIDDLE_Y + dy], COLOURS[i])
              for i, (s, dx, dy) in enumerate(pattern)]
    layout.append(body("obj_victim", victim, TABLE_SLOTS[0], "purple"))
    scene = PhysicsScene(seed=7, object_layout=layout, gui=False)
    try:
        FLOOR_Z[0] = scene.trays["tray_middle"]["floor_top"]
        executor = SkillExecutor(scene, world_provider=lambda: None)
        planner = executor.placement_planner
        world = build_world_state(scene, 1, "obs_0001")
        n1 = len([c for c in planner.generate("tray_middle", "obj_victim", world, limit=None)
                  if planner.recheck(c, world).ok])
        if n1:
            return None
        legs = []
        for i, (size, dx, dy) in enumerate(pattern):
            eid, home = f"obj_p{i + 1}", [TRAY_X + dx, MIDDLE_Y + dy]
            put(scene, eid, TABLE_SLOTS[4])
            w2 = build_world_state(scene, 2, "obs_0002")
            slots = [c for c in planner.generate("tray_middle", "obj_victim", w2, limit=None)
                     if planner.recheck(c, w2).ok]
            by_slot = {}
            for c in slots:
                put(scene, "obj_victim", [c.position_xy[0], c.position_xy[1]])
                w3 = build_world_state(scene, 3, "obs_0003")
                by_slot[c.candidate_id] = len(
                    [x for x in planner.generate("tray_middle", eid, w3, limit=None)
                     if planner.recheck(x, w3).ok])
                put(scene, "obj_victim", TABLE_SLOTS[0])
            put(scene, eid, home)
            legs.append({"removed": eid, "size": size, "victim_slots": len(slots),
                         "return_max": max(by_slot.values()) if by_slot else 0,
                         "return_by_slot": by_slot})
        return {"pattern": [[s, dx, dy] for s, dx, dy in pattern], "victim": victim,
                "v1_clear": 0, "legs": legs}
    finally:
        scene.close()


# The patterns below are the ones geometry can plausibly block with: spread across the region along
# y, and then pushed into the lattice corners, with the three sizes that probe #2 measured as
# liftable out of a tray. `wide40` is kept only as the arrangement probe #3 already failed.
PATTERNS = [
    [("cuboid", 0.0, -0.045), ("cuboid", 0.0, 0.0), ("cuboid", 0.0, 0.045)],
    [("cyl", 0.0, -0.045), ("cyl", 0.0, 0.0), ("cyl", 0.0, 0.045)],
    [("cuboid", 0.0, -0.045), ("cyl", 0.0, 0.0), ("cuboid", 0.0, 0.045)],
    [("cuboid", -0.02, -0.045), ("cyl", 0.02, -0.005), ("cuboid", -0.02, 0.035)],
    [("cyl", -0.025, -0.04), ("cyl", 0.025, -0.01), ("cyl", -0.025, 0.02), ("cyl", 0.025, 0.05)],
    [("cuboid", -0.025, -0.04), ("cuboid", 0.025, -0.01), ("cuboid", -0.025, 0.02)],
    [("wide40", 0.0, -0.045), ("wide40", 0.0, 0.045)],
    [("wide40", -0.02, -0.045), ("wide40", 0.02, 0.045)],
]

if __name__ == "__main__":
    probe = PhysicsScene(seed=1, gui=False)
    FLOOR_Z[0] = probe.trays["tray_middle"]["floor_top"]
    probe.close()
    print(f"tray floor_top={FLOOR_Z[0]}", flush=True)
    found = []
    for pattern in PATTERNS:
        for victim in ("cube21", "cuboid", "cyl", "wide40"):
            try:
                r = scan(pattern, victim)
            except Exception as e:
                say({"pattern": pattern, "victim": victim, "error": f"{type(e).__name__}: {e}"})
                continue
            if r is None:
                continue
            closable = [g for g in r["legs"] if g["return_max"] > 0]
            say({"pattern": r["pattern"], "victim": victim,
                 "legs": [(g["removed"], g["size"], g["victim_slots"], g["return_max"])
                          for g in r["legs"]], "closable": bool(closable)})
            if closable:
                found.append({**r, "legs": closable})
    print(f"\n== {len(found)} blocked layouts that also close the return leg ==")
    for f in found:
        print(f"  {f['victim']} {f['pattern']} "
              f"{[(g['removed'], g['victim_slots'], g['return_max']) for g in f['legs']]}")
    with open("/tmp/qoder-remote-worker-nSv81L/p2d_slot_sweep.json", "w") as fh:
        json.dump(found, fh, indent=1)
