"""Measured (not IK-only) envelope: can the arm actually be held at a point?"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pybullet as p
from embodied_agent.core.scene import PhysicsScene

LAYOUT = [
    {"entity_id": "obj_green_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.38, -0.16],
     "attributes": {"color": "green", "shape": "cube"}},
    {"entity_id": "obj_yellow_3", "shape": "cylinder", "dims": [0.023, 0.042], "xy": [0.38, 0.16],
     "attributes": {"color": "yellow", "shape": "cylinder"}},
]
sc = PhysicsScene(seed=1, object_layout=LAYOUT)
orn = p.getQuaternionFromEuler([math.pi, 0, 0])
print("base", getattr(sc, "base_pos", None))
for tid in ("tray_left", "tray_middle", "tray_right"):
    t = sc.trays[tid]
    cx, cy = t["center"]
    for dx in (-0.08, -0.04, 0.0, 0.04, 0.08):
        for z in (0.80,):
            sc.reset_arm()
            ok = sc.move_ee([cx + dx, cy, z], orn, timeout_s=3.0)
            pos, _ = sc.ee_pose()
            err = float(np.linalg.norm(pos - np.array([cx + dx, cy, z])))
            print(f"{tid} x={cx+dx:.3f} y={cy:+.2f} z={z}: moved={ok} err={err*1000:.1f}mm "
                  f"ik_only={sc.reachable([cx+dx, cy, z], orn)}")
sc.close()
