"""P0 diagnosis: which place targets are actually reachable?"""
import numpy as np
from embodied_agent.core.scene import PhysicsScene
import pybullet as p

LAYOUT = [
    {"entity_id": "obj_red_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.44, 0.0],
     "attributes": {"color": "red", "shape": "cube"}},
]
sc = PhysicsScene(seed=1, object_layout=LAYOUT)

for tid in ("tray_left", "tray_middle", "tray_right"):
    t = sc.trays[tid]
    for z in (0.78, 0.72, 0.68, 0.66):
        sc.reset_arm()
        ok = sc.move_ee([t["center"][0], t["center"][1], z],
                        p.getQuaternionFromEuler([np.pi, 0, 0]), timeout_s=3.0)
        pos, _ = sc.ee_pose()
        err = np.linalg.norm(pos - np.array([t["center"][0], t["center"][1], z]))
        print(f"{tid} z={z}: reachable={ok} tcp_err={err*1000:.1f}mm at {np.round(pos,3)}")
sc.close()
