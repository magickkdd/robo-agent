"""P1-e dev probe: run one episode with the world coming out of a camera.

Read-only against the repository, writes only under /tmp. Nothing here feeds a prompt or a
DecisionContext; it exists to answer one question before any test is written — does the
sensing arm complete an episode at all, and what does its event log look like when it does.
"""
import json
import os
import sys
import tempfile

from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import build_world_state
from embodied_agent.evaluation.calibration import cell_catalog
from embodied_agent.evaluation.run import build_scene, task_input
from embodied_agent.evaluation.sources import RulePolicySource
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.perception.arm import PerceptionUnavailable, build_arm
from embodied_agent.perception.grounding import GroundingMap

ROOT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="p1_arm_")


def run(case_id, perceive, *, default_view="main", views=None, ablation=None):
    case = find_case(case_id)
    scene = build_scene(case)
    try:
        goal_world = build_world_state(scene, 1, "obs_0001", case.verify)
        task = task_input(case)
        goal = interpret(task, goal_world)
        catalog = cell_catalog(scene.trays.values())
        gmap = GroundingMap.from_objects(case.objects)
        episode_id = f"{case_id}.{perceive}"
        ep_dir = os.path.join(ROOT, episode_id)
        os.makedirs(ep_dir, exist_ok=True)
        controller = EnvironmentController(scene, case.fresh_events())
        executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        store = EpisodeStore(ep_dir, episode_id)
        rt = build_arm(case=case, scene=scene, executor=executor, store=store, run_dir=ep_dir,
                       episode_id=episode_id, perceive=perceive, catalog=catalog, gmap=gmap,
                       budgets=case.budgets, environment=controller, ablation=ablation,
                       default_view=default_view, views=views)
        executor.world_provider = rt.observe
        result = rt.run_episode(task, goal, RulePolicySource(case.verify), mode="B")
        events = store.read_all()
        s4 = store.read_all("4")
        kinds = {}
        for e in events:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        return {
            "case": case_id, "perceive": perceive, "status": str(result.terminal_status),
            "failure": str(result.failure_type), "rounds": result.decision_rounds,
            "skill_calls": result.skill_calls, "http": result.http_requests,
            "completed": f"{result.objects_completed}/{result.objects_total}",
            "events": kinds, "schema4_types": sorted({e["type"] for e in s4}),
            "looks": len(rt.perceiver.percepts) if hasattr(rt, "perceiver") else 0,
            "usage_looks": rt.perceiver.usage() if hasattr(rt, "perceiver") else {},
            "note": result.artifacts.get("note"),
            "dir": ep_dir,
        }
    finally:
        scene.close()


if __name__ == "__main__":
    print("root:", ROOT)
    rows = []
    for perceive in ("privileged", "stub"):
        try:
            rows.append(run("dev_c5", perceive))
        except PerceptionUnavailable as e:
            rows.append({"perceive": perceive, "unavailable": str(e), "reasons": e.reasons})
    for r in rows:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
