"""Does a single long move_ee make the held object slip in the grip?"""
import sys, os, math, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pybullet as p
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor, _down_orn
from embodied_agent.core.runtime import Runtime
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[0]


def offset(scene, oid="obj_yellow_3"):
    ee, _ = scene.ee_pose()
    pos, _ = scene.object_pose(oid)
    return np.asarray(pos[:2]) - np.asarray(ee[:2])


def run(interpolate):
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    ex = SkillExecutor(scene, world_provider=lambda: None)
    rt = Runtime(scene, ex, tempfile.mkdtemp(), case.budgets, "transfer", config=case.verify)
    ex.world_provider = rt.observe
    r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="pick",
                             args={"object_id": "obj_yellow_3"}))
    d0 = offset(scene, "obj_yellow_3")
    scene.start_action(30.0, "diag")
    scene.begin_execution()
    orn = _down_orn()
    cur, _ = scene.ee_pose()
    scene.move_ee([cur[0], cur[1], 0.80], orn, timeout_s=2.0)
    target = (0.64, -0.38)
    if interpolate:
        steps = max(1, int(math.hypot(target[0] - cur[0], target[1] - cur[1]) / 0.04) + 1)
        for i in range(1, steps + 1):
            f = i / steps
            ok = scene.move_ee([cur[0] + f * (target[0] - cur[0]),
                                cur[1] + f * (target[1] - cur[1]), 0.80], orn, timeout_s=1.0)
            if not ok:
                break
    else:
        ok = scene.move_ee([target[0], target[1], 0.80], orn, timeout_s=3.0)
    d1 = offset(scene, "obj_yellow_3")
    sim_s = scene.sim_time
    scene.end_action()
    print(f"interpolate={interpolate} ok={ok} off_after_pick=({d0[0]*1000:+5.1f},{d0[1]*1000:+5.1f}) "
          f"off_after_transfer=({d1[0]*1000:+5.1f},{d1[1]*1000:+5.1f}) |off|={np.linalg.norm(d1)*1000:5.1f}mm "
          f"sim_s={sim_s:.2f}")
    scene.close()


run(False)
run(True)
