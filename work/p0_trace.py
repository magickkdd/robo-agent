"""P0 diagnosis: trace which place transfer move fails and why."""
import numpy as np
import pybullet as p
from embodied_agent.core.scene import PhysicsScene, ARM_JOINTS
from embodied_agent.core.skills import SkillExecutor, _down_orn
from embodied_agent.core.contracts import SkillCall, WorldState
from embodied_agent.core.verify import build_world_state

LAYOUT = [
    {"entity_id": "obj_red_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.44, 0.0],
     "attributes": {"color": "red", "shape": "cube"}},
]
sc = PhysicsScene(seed=1, object_layout=LAYOUT)
ex = SkillExecutor(sc, lambda: build_world_state(sc, 0, "o"))

r = ex.execute(SkillCall(plan_id="p", step_id="s", skill="pick", args={"object_id": "obj_red_1"}))
print("pick:", r.status.value, r.failure_code)
cur, _ = sc.ee_pose()
print("post-pick ee:", np.round(cur, 3), "joints:", np.round([p.getJointState(sc.robot, j)[0] for j in ARM_JOINTS], 2))
orn = _down_orn()

for label, tgt, to in (("lift", [cur[0], cur[1], 0.78], 2.0), ("travel", [0.66, -0.32, 0.78], 3.0)):
    sol = sc.ik(tgt, orn)
    ok_j = sc.move_joints(sol, timeout_s=to, tol=8e-3)
    pos, _ = sc.ee_pose()
    err = float(np.linalg.norm(pos - np.array(tgt)))
    print(f"{label}: joints_converged={ok_j} tcp_err={err*1000:.1f}mm target={np.round(tgt,3)} reached={np.round(pos,3)}")
