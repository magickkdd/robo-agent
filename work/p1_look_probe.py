"""Why does a completed place read as `false` on the next look?

P1-e smoke found: pick -> place -> `placed:obj_red_1:tray_right` reported `true` by the
post-action snapshot, and the very next round's `progress()` says `false`. This probe runs the
two acts through the real executor and then prints what the camera says, per view, so the
disagreement can be attributed to a field rather than guessed at.
"""
import json

from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.verify import build_world_state
from embodied_agent.evaluation.calibration import cell_catalog
from embodied_agent.evaluation.run import build_scene, task_input
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.perception.arm import build_arm
from embodied_agent.perception.grounding import GroundingMap

case = find_case("dev_c5")
scene = build_scene(case)
goal = interpret(task_input(case), build_world_state(scene, 1, "obs_0001", case.verify))
print("assignments:", [(a.entity.model_dump(), a.target_id) for a in goal.assignments][:2])
catalog = cell_catalog(scene.trays.values())
gmap = GroundingMap.from_objects(case.objects)
import tempfile, os
root = tempfile.mkdtemp(prefix="p1_look_")
executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
store = EpisodeStore(root, "probe")
rt = build_arm(case=case, scene=scene, executor=executor, store=store, run_dir=root,
               episode_id="probe", perceive="stub", catalog=catalog, gmap=gmap,
               budgets=case.budgets, environment=EnvironmentController(scene, case.fresh_events()))
executor.world_provider = rt.observe
p = rt.perceiver


def dump(view):
    w = p.look(view, held=executor.held_state())
    print(f"  view={view} held={w.held_object!r}")
    for e in w.entities:
        print(f"    {e.entity_id:14s} pose={'None' if e.pose is None else tuple(round(v,3) for v in (e.pose.position.x,e.pose.position.y,e.pose.position.z))} "
              f"support={e.supported_by!r} grounding={e.attributes.get('grounding')}")
    print("    occupancy:", [(o.entity_id, o.target_id, o.fully_inside) for o in w.occupancy])
    print("    not_seen :", [(n.entity_id, n.occluded_by) for n in w.not_seen])
    prog = rt._verifier(w).progress(goal)
    print("    progress :", [(x.entity_id, x.target_id, x.value.value) for x in prog])


for view in ("main", "overhead", "front_high"):
    print(f"before any action, {view}:")
    dump(view)

print("pick obj_red_1")
r = executor.execute(SkillCall(call_id="c1", plan_id="probe", step_id="s1", skill="pick",
                              args={"object_id": "obj_red_1"}, timeout_s=8.0))
print("  ", r.status, r.failure_code)
print("place obj_red_1 -> tray_right")
r = executor.execute(SkillCall(call_id="c2", plan_id="probe", step_id="s2", skill="place",
                              args={"object_id": "obj_red_1", "target_id": "tray_right"},
                              timeout_s=8.0))
print("  ", r.status, r.failure_code)
print("true positions:", scene.object_pose("obj_red_1")[0],
      {t: scene.trays[t].get("position") for t in scene.trays})
for view in ("main", "overhead", "front_high"):
    print(f"after the place, {view}:")
    dump(view)

priv = build_world_state(scene, 999, "priv", case.verify)
print("privileged snapshot:", [(e.entity_id, e.supported_by, e.held) for e in priv.entities])
print("privileged occupancy:", [(o.entity_id, o.target_id, o.fully_inside) for o in priv.occupancy])
# the *privileged* verifier, because a privileged snapshot is what this comparison is about:
# `rt._verifier` would hand back the sensor one, which has no business reading this world
from embodied_agent.core.verify import RuntimeVerifier
print("privileged progress:", [(x.entity_id, x.target_id, x.value.value)
                               for x in RuntimeVerifier(priv, case.verify).progress(goal)])
json.dump({"dir": root}, open(os.path.join(root, "probe.json"), "w"))
print("artifacts:", root)
