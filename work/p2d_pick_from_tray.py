"""P2-d dev probe #2: can the hand lift *any* body out of a tray, or is that a class of action S1
cannot perform?

`work/p2d_reach.py` found the pair of facts that decide whether §8's temporary-move-and-restore task
can be authored at all: a seated body only makes a region infeasible for another object at
half-extents >= 0.05 m (a 0.04 body still leaves 4 clear slots in the middle tray), and a body of
that size authored at a tray centre fails `pick` with GRASP_MISS. Neither fact separates "the hand
cannot reach into a tray" from "this body is too wide to be grasped" — and the two have opposite
consequences for the task set. So the sweep is over *size* and *position within the region*, with the
table as the control:

* if every seated body fails to leave, whatever its size and wherever in the region it sits, then a
  lift-out is not an action this environment supports and §8 cannot be a physical task here; the
  honest P2-d deliverable is the dependency-ordered set plus a recorded reason for the omission.
* if small bodies leave and only wide ones do not, the question becomes whether a *graspable* body
  can block a region — the second sweep in `p2d_block_matrix` answers that by pairing occupant size
  against victim size.

Production instruments only: `SkillExecutor.execute` for the action, `PhysicsScene.reachable` and
`grasp_candidates` for what the hand claims it can do, `build_world_state` for the facts. Nothing
here feeds a prompt or a DecisionContext.
"""
import json
import sys

from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.tasks import DIMS, PALETTE, TRAYS

TRAY_Y = {"tray_left": -0.32, "tray_middle": 0.0, "tray_right": 0.32}
BODIES = {
    "cube21": ("cube", [0.021, 0.021, 0.021]),
    "cuboid": ("cuboid", [0.027, 0.019, 0.035]),
    "cyl": ("cylinder", DIMS["cylinder"]),
    "wide40": ("cuboid", [0.040, 0.040, 0.020]),
    "wide50": ("cuboid", [0.050, 0.050, 0.020]),
}
COLOURS = ["red", "green", "blue", "yellow", "purple"]


def say(row):
    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)


def attempt(tray, offsets, where):
    """Pick one body out of the position it was authored at, in `tray` (or on the table)."""
    out = []
    for (shape, dims), (name, _) in zip(BODIES.values(), BODIES.items()):
        xy = [0.66, TRAY_Y[tray] + offsets[0]] if where == "tray" else [0.40, -0.16]
        layout = [{"entity_id": "obj_x", "shape": shape, "dims": list(dims), "xy": list(xy),
                   "color": PALETTE["red"], "attributes": {"color": "red", "shape": shape}}]
        scene = PhysicsScene(seed=7, object_layout=layout, gui=False)
        try:
            executor = SkillExecutor(scene, world_provider=lambda: None)
            world = build_world_state(scene, 1, "obs_0001")
            executor.world_provider = lambda: world
            pos, orn = scene.object_pose("obj_x")
            cands = scene.grasp_candidates("obj_x")
            reach = [bool(scene.reachable(list(c), list(orn))) for c in cands]
            r = executor.execute(SkillCall(plan_id="p", step_id="s", skill="pick",
                                           args={"object_id": "obj_x"}))
            row = {"where": where, "tray": tray if where == "tray" else "table", "body": name,
                   "authored_xy": [round(float(v), 4) for v in xy],
                   "seated_z": round(float(pos[2]), 4),
                   "in_occupancy": [o.entity_id for o in world.occupancy
                                    if o.entity_id == "obj_x"],
                   "grasp_candidates": len(cands),
                   "grasp_reachable": reach,
                   "pick": str(r.status), "code": r.failure_code}
            scene.close()
        except Exception as e:
            row = {"where": where, "tray": tray, "body": name,
                   "error": f"{type(e).__name__}: {e}"}
        out.append(row)
        say(row)
    return out


if __name__ == "__main__":
    rows = []
    rows += attempt("tray_middle", [0.0], "tray")           # dead centre of the region
    rows += attempt("tray_middle", [0.045], "tray")         # off-centre, still fully inside
    rows += attempt("tray_left", [0.0], "tray")             # a different tray, same question
    rows += attempt("tray_middle", [0.0], "table")          # control: the same bodies on the table
    print("\n== can it leave? ==")
    for where in ("tray", "table"):
        for name in BODIES:
            r = [x for x in rows if x["where"] == where and x.get("body") == name]
            if r:
                r = r[0]
                print(f"{where:6} {name:8} pick={r.get('pick'):22} code={r.get('code')} "
                      f"cands={r.get('grasp_candidates')} reach={r.get('grasp_reachable')} "
                      f"occupant={bool(r.get('in_occupancy'))}")
    sys.exit(0)
