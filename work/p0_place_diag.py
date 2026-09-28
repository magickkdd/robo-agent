import os, sys, json
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
rt = Runtime(scene, ex, "/tmp/p0diag", case.budgets, "diag", config=case.verify)
ex.world_provider = rt.observe
task = TaskInput(task_id=case.task_id, utterance=case.utterance)
goal = interpret(task, rt.observe())

def show(tag):
    w = rt.observe()
    v = RuntimeVerifier(w, case.verify)
    for eid, tid in [("obj_green_1", "tray_left"), ("obj_blue_2", "tray_right"), ("obj_yellow_3", "tray_left")]:
        r = v.verify_placement(eid, tid)
        e = r.model_dump(mode="json")
        print(f"  [{tag}] {eid}->{tid}: {r.value} {json.dumps(e['evidence'], ensure_ascii=False)} unm={e.get('unmeasured')}")
    ent = w.entity("obj_green_1")
    print(f"  [{tag}] green pose={ [round(x,4) for x in [ent.pose.position.x, ent.pose.position.y, ent.pose.position.z]] } held={ent.held}")
    print(f"  [{tag}] held_object={w.held_object}")

def call(skill, **args):
    c = SkillCall(call_id="c1", plan_id="p", step_id="s", skill=skill, args=args)
    r = ex.execute(c)
    print(f"  call {skill} {args} -> {r.status.value} {r.failure_code}")
    return r

show("t0")
call("pick", object_id="obj_green_1")
show("after-pick")
call("place", object_id="obj_green_1", target_id="tray_left")
show("after-place")
print("contacts green:", sorted(scene.contact_partners("obj_green_1")))
w = rt.observe()
print("tray_left region:", [t.model_dump(mode="json") for t in w.targets if t.target_id == "tray_left"])
scene.close()
