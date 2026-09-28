"""Does the second placement fail because a cylinder can lie on its side?"""
import sys, os, math, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation.tasks import build_set


def rot_matrix(qx, qy, qz, qw):
    n = qx * qx + qy * qy + qz * qz + qw * qw
    if n == 0:
        return np.eye(3)
    qx, qy, qz, qw = (v * math.sqrt(2.0 / n) for v in (qx, qy, qz, qw))
    return np.array([
        [1 - (qy * qy + qz * qz), qx * qy - qz * qw, qx * qz + qy * qw],
        [qx * qy + qz * qw, 1 - (qx * qx + qz * qz), qy * qz - qx * qw],
        [qx * qz - qy * qw, qy * qz + qx * qw, 1 - (qx * qx + qy * qy)],
    ])


def extent_along_world_z(geom, R):
    """Half-extent of the body measured along the world z axis, any orientation."""
    row = R[2]
    if geom.shape == "cylinder":
        axis_tilt = abs(float(row[2]))
        return geom.half_h_vertical * axis_tilt + geom.radius * math.sqrt(max(0.0, 1 - axis_tilt ** 2))
    return (abs(float(row[0])) * geom.half_extents.x + abs(float(row[1])) * geom.half_extents.y
            + abs(float(row[2])) * geom.half_extents.z)


case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
rt = Runtime(scene, ex, tempfile.mkdtemp(), case.budgets, "sitdiag", config=case.verify)
ex.world_provider = rt.observe


def call(skill, **args):
    r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill=skill, args=args))
    c = ex.last_candidate
    print(f"  {skill} {args} -> {r.status.value} {r.failure_code} cand={c.candidate_id if c else None}")
    if r.measurements:
        print("     meas:", json.dumps({k: round(v, 4) for k, v in r.measurements.items()}))
    return r


call("pick", object_id="obj_green_1")
call("place", object_id="obj_green_1", target_id="tray_left")
call("pick", object_id="obj_yellow_3")
call("place", object_id="obj_yellow_3", target_id="tray_left")

w = rt.observe()
v = RuntimeVerifier(w, case.verify)
FLOOR = 0.628
for eid in ("obj_green_1", "obj_yellow_3"):
    e = w.entity(eid)
    q = e.pose.quaternion_xyzw
    R = rot_matrix(*q)
    g = e.geometry
    true_rest = FLOOR + extent_along_world_z(g, R)
    print(f"{eid}: xy=({e.pose.position.x:.4f},{e.pose.position.y:.4f}) z={e.pose.position.z:.4f} "
          f"sup={e.supported_by} rest={e.at_rest}")
    print(f"   quat={np.round(q, 3)} diag_z={np.round(np.diag(R), 3)} shape={g.shape} "
          f"r={g.radius} hh={g.half_h_vertical}")
    print(f"   nominal_rest={FLOOR + g.half_h_vertical:.4f} measured_seated_rest={true_rest:.4f} "
          f"actual_z={e.pose.position.z:.4f}")
    rep = v.verify_placement(eid, "tray_left").model_dump(mode="json")
    print(f"   placement -> {rep['value']} unm={rep['unmeasured']} "
          f"evidence={json.dumps({k: round(x, 4) for k, x in rep['evidence'].items()})}")
print("occupancy:", [(o.entity_id, o.target_id, o.fully_inside) for o in w.occupancy])
scene.close()
