"""How long does a released cylinder keep spinning, and does it ever satisfy at_rest?"""
import sys, os, math, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
rt = Runtime(scene, ex, tempfile.mkdtemp(), case.budgets, "spin", config=case.verify)
ex.world_provider = rt.observe


def call(skill, **args):
    r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill=skill, args=args))
    print(f"  {skill} {args} -> {r.status.value} {r.failure_code}")
    return r


call("pick", object_id="obj_green_1")
call("place", object_id="obj_green_1", target_id="tray_left")
call("pick", object_id="obj_blue_2")
call("place", object_id="obj_blue_2", target_id="tray_right")
call("pick", object_id="obj_yellow_3")
call("place", object_id="obj_yellow_3", target_id="tray_left")

for i in range(40):
    pos, orn = scene.object_pose("obj_yellow_3")
    lin, ang = scene.object_velocity("obj_yellow_3")
    ax = np.array(ang, dtype=float)
    n = np.linalg.norm(ax)
    axis = ax / n if n > 1e-9 else ax
    print(f"t={i * 0.25:5.2f}s pos=({pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f}) "
          f"|v|={np.linalg.norm(lin):.4f} |w|={n:.4f} axis=({axis[0]:.2f},{axis[1]:.2f},{axis[2]:.2f})",
          flush=True)
    if n < case.verify.ang_speed_tol_rps and np.linalg.norm(lin) < case.verify.speed_tol_mps:
        print("  -> at rest")
        break
    scene.settle(0.25)
print("budget consumed sim s:", scene.sim_time, "action steps left:", scene._action_steps_left)
scene.close()
