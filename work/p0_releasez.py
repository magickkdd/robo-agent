"""How many tray slots stay servable when the hand-off pose drops to touchdown?

The current reachability claim is checked at floor_top + half_h + 22 mm. If the
release must instead reach the object's own seated height, the claim has to be
made at that lower z, and the reachable slot set changes.
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pybullet as p
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.evaluation.tasks import build_set, TRAYS, _object, SLOTS

orn = p.getQuaternionFromEuler([math.pi, 0, 0])
LAYOUT = [_object("obj_cyl", "cylinder", "yellow", SLOTS[0]),
          _object("obj_cube", "cube", "green", SLOTS[4]),
          _object("obj_cub", "cuboid", "blue", SLOTS[2])]
sc = PhysicsScene(seed=51, object_layout=LAYOUT)
sc.start_action(120.0, "probe")
sc.begin_execution()

for eid, half_h in (("obj_cyl", 0.042), ("obj_cube", 0.021), ("obj_cub", 0.035)):
    g = sc.geometry(eid)
    rad = g.radius if g.shape == "cylinder" else math.hypot(g.half_extents.x, g.half_extents.y)
    print(f"== {eid} shape={sc.geometry(eid).shape} r/half={rad:.3f} half_h={half_h}")
    for tid, t in sc.trays.items():
        cx, cy = t["center"]
        room = t["inner_half"] - 0.01 - rad - 0.010
        n = int(room // 0.02)
        pts = [(cx + i * 0.02, cy + j * 0.02) for i in range(-n, n + 1) for j in range(-n, n + 1)]
        for lift in (0.022, 0.004, 0.000):
            z = t["floor_top"] + half_h + lift
            ok = sum(1 for x, y in pts if sc.reachable([x, y, z], orn))
            print(f"   {tid:11s} lift={lift:5.3f} z={z:.3f} reachable {ok:3d}/{len(pts)}")
sc.end_action()
sc.close()
