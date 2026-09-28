"""P0 diagnosis: instrument every move_ee during pick+place in the real skill path."""
import numpy as np
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.verify import build_world_state

LAYOUT = [
    {"entity_id": "obj_red_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.44, 0.0],
     "attributes": {"color": "red", "shape": "cube"}},
    {"entity_id": "obj_blue_2", "shape": "cylinder", "dims": [0.023, 0.042], "xy": [0.68, -0.16],
     "attributes": {"color": "blue", "shape": "cylinder"}},
]
sc = PhysicsScene(seed=1, object_layout=LAYOUT)
_orig = sc.move_ee
cnt = {"n": 0}


def traced(xyz, orn, **kw):
    cnt["n"] += 1
    ok = _orig(xyz, orn, **kw)
    print(f"  move#{cnt['n']:2d} -> {np.round(xyz,3).tolist()} tol={kw.get('tol')} "
          f"to={kw.get('timeout_s')} ok={ok} tcp_err={getattr(sc,'_last_tcp_err',0)*1000:.1f}mm")
    return ok


sc.move_ee = traced
ex = SkillExecutor(sc, lambda: build_world_state(sc, 0, "o"))
print("PICK")
r = ex.execute(SkillCall(plan_id="p", step_id="s", skill="pick", args={"object_id": "obj_red_1"}))
print("pick:", r.status.value, r.failure_code, "stages", r.stages_executed)
print("PLACE")
r = ex.execute(SkillCall(plan_id="p", step_id="s2", skill="place",
                         args={"object_id": "obj_red_1", "target_id": "tray_left"}))
print("place:", r.status.value, r.failure_code, "stages", r.stages_executed)
print("  meas", {k: round(v, 4) for k, v in r.measurements.items()})
print("final obj pose", np.round(sc.object_pose("obj_red_1")[0], 3))
sc.close()
