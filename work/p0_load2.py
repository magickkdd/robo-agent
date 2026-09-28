"""Empty vs loaded TCP residual at the same waypoints, same order (calibration)."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pybullet as p
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor

LAYOUT = [{"entity_id": "obj_green_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.38, -0.16],
           "attributes": {"color": "green", "shape": "cube"}},
          {"entity_id": "obj_yellow_3", "shape": "cylinder", "dims": [0.023, 0.042],
           "xy": [0.38, 0.16], "attributes": {"color": "yellow", "shape": "cylinder"}}]
sc = PhysicsScene(seed=1, object_layout=LAYOUT)
from embodied_agent.core.verify import build_world_state

_ctr = [0]
ex = SkillExecutor(sc, world_provider=lambda: build_world_state(
    sc, _ctr.__setitem__(0, _ctr[0] + 1) or _ctr[0], f"obs_{_ctr[0]}", None))
orn = p.getQuaternionFromEuler([math.pi, 0, 0])
cx, cy = sc.trays["tray_left"]["center"]
pts = [[cx, cy, 0.80], [cx + 0.02, cy + 0.04, 0.80], [cx + 0.04, cy + 0.08, 0.80],
       [cx + 0.06, cy + 0.08, 0.80], [cx + 0.02, cy + 0.02, 0.692], [cx + 0.08, cy + 0.08, 0.80]]


def residuals(tag):
    out = []
    for pt in pts:
        sc.move_ee(pt, orn, timeout_s=2.5)
        sc.settle(0.3)
        pos, _ = sc.ee_pose()
        out.append(float(np.linalg.norm(pos - np.asarray(pt))) * 1000)
    print(tag, " ".join(f"{v:6.2f}" for v in out))
    return out


def pts_str():
    print("pts ", " ".join(f"({x-cx:+.2f},{y-cy:+.2f},{z:.3f})" for x, y, z in pts))


pts_str()
a = residuals("empty ")
sc.begin_execution()
r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="pick",
                         args={"object_id": "obj_green_1"}))
print("pick green:", r.status.value, r.failure_code, (r.notes or [])[:1])
b = residuals("loaded")
sc.end_action()
print("delta mm:", " ".join(f"{y - x:+6.2f}" for x, y in zip(a, b)))
sc.close()
