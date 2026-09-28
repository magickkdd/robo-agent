"""Why does a placed object fail its own region predicate? (dev_c3, dev_c6)

Runs the production episode wiring (same scene builder, controller, executor and
rule policy as `evaluation.run`) on the two dev cases that fail in every mode,
then prints the *pure* predicate evidence against the terminal snapshot:
measured pose, measured footprint half-extents, the region's allowed extent and
the support verdict. Numbers, not guesses.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.contracts import TaskInput
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import (entity_footprint_half_xy, footprint_inside_region,
                                        object_in_region, object_supported, seated_rest_z)
from embodied_agent.evaluation.evaluator import IndependentEvaluator
from embodied_agent.evaluation.run import build_scene
from embodied_agent.evaluation.sources import RulePolicySource
from embodied_agent.evaluation.tasks import build_set


def _v3(v):
    return [round(float(v.x), 4), round(float(v.y), 4), round(float(v.z), 4)]


def show(case):
    scene = build_scene(case)
    try:
        controller = EnvironmentController(scene, case.fresh_events())
        ex = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        rt = Runtime(scene, ex, "/tmp/diag_dev", case.budgets, f"diag-{case.task_id}",
                     config=case.verify, environment=controller)
        ex.world_provider = rt.observe
        task = TaskInput(task_id=case.task_id, utterance=case.utterance)
        goal = interpret(task, rt.observe())
        class Traced(SkillExecutor):
            def execute(self, call):
                r = super().execute(call)
                print(f"  [{call.skill} {call.args}] status={r.status.value} code={r.failure_code}")
                for eid in sorted(scene.objects):
                    pos, orn = scene.object_pose(eid)
                    g = scene.geometry(eid)
                    tr = scene.trays.get(next((t for t in scene.trays
                                               if scene.contact_partners(eid) and t in scene.contact_partners(eid)), ""), None)
                    part = sorted(scene.contact_partners(eid))
                    print(f"      {eid:14} pos=({pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f}) "
                          f"contacts={part}")
                return r

        ex2 = Traced(scene, world_provider=rt.observe, config=case.verify)
        rt.executor = ex2
        res = rt.run_episode(task, goal, RulePolicySource(case.verify), mode="B")
        print(f"\n=== {case.task_id} terminal={res.terminal_status.value}/{res.failure_type} "
              f"objs={res.objects_completed}/{res.objects_total} ===")
        world = rt.terminal_world
        regions = {t.target_id: t for t in world.targets}
        by_id = {e.entity_id: e for e in world.entities}
        report = RuntimeVerifier(world, case.verify).verify_goals(goal)
        for pr in report.reports:
            print(f"  verdict {pr.predicate_id:34} {pr.description[:20]:22} "
                  f"={pr.value.value} ev={json.dumps(pr.evidence, default=str)[:200]}")
        for a in goal.assignments:
            eid, tid = a.entity.entity_id, a.target_id
            e, reg = by_id.get(eid), regions.get(tid)
            if e is None or reg is None:
                print(f"  {eid} -> {tid}: no snapshot record")
                continue
            half = entity_footprint_half_xy(e)
            inside, ev = footprint_inside_region(
                [e.pose.position.x, e.pose.position.y], half, reg,
                case.verify.footprint_margin_m)
            live_in, _ = object_in_region(scene, eid, reg, case.verify.footprint_margin_m)
            live_sup, sup = object_supported(scene, eid, reg, case.verify.support_height_tol_m)
            rest = seated_rest_z(e.geometry, reg, e.pose.quaternion_xyzw)
            g = e.geometry
            print(f"  {eid:15} -> {tid:12} inside={inside} live={live_in} support={live_sup}")
            print(f"    pos={_v3(e.pose.position)} "
                  f"half_xy={[round(h,4) for h in half]} rest_z={rest:.4f} {json.dumps(ev)}")
            print(f"    geom={g.shape} r={g.radius} half_h={g.half_h} "
                  f"he={_v3(g.half_extents) if g.half_extents else None} "
                  f"support_ev={json.dumps({k: round(v, 4) for k, v in sup.items()})}")
        sc = IndependentEvaluator(rt.terminal_snapshot, case.eval_spec).score(res)
        print(f"  independent: {sc['objects_completed']}/{sc['objects_total']} "
              f"complete={sc['complete_success']} notes={sc['protocol_notes']}")
    finally:
        scene.close()


def find(name):
    for st in ("smoke", "dev", "formal", "protocol"):
        for c in build_set(st):
            if c.task_id == name:
                return c
    raise KeyError(name)


for name in sys.argv[1:] or ["dev_c3", "dev_c6"]:
    show(find(name))
