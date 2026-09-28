import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embodied_agent.core.contracts import SkillCall, TaskInput
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
rt = Runtime(scene, ex, tempfile.mkdtemp(), case.budgets, "shared", config=case.verify)
ex.world_provider = rt.observe
w = rt.observe()

def call(skill, **args):
    r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill=skill, args=args))
    print(f"  {skill} {args} -> {r.status.value} {r.failure_code} notes={ (r.notes or [])[:2] }")
    return r

def status(tag):
    w = rt.observe()
    v = RuntimeVerifier(w, case.verify)
    print(f" [{tag}]")
    for eid in ("obj_green_1", "obj_yellow_3"):
        e = w.entity(eid)
        r = v.verify_placement(eid, "tray_left").model_dump(mode="json")
        ev = r["evidence"]
        print(f"   {eid} xy=({e.pose.position.x:.4f},{e.pose.position.y:.4f}) z={e.pose.position.z:.4f} "
              f"sup={e.supported_by} rest={e.at_rest} held={e.held} -> {r['value']} {json.dumps({k: round(val,4) for k,val in ev.items() if isinstance(val,(int,float))})}")
    print("   occupancy:", [(o.entity_id, o.target_id, o.fully_inside) for o in w.occupancy])

call("pick", object_id="obj_green_1")
call("place", object_id="obj_green_1", target_id="tray_left")
status("after-green")
call("pick", object_id="obj_yellow_3")
status("holding-yellow")
call("place", object_id="obj_yellow_3", target_id="tray_left")
status("after-yellow")
scene.close()
