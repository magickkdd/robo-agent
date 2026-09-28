"""Why can a recall never fit the default 900-char page on dev_c1? Print the arithmetic.

Zero spend, /tmp artifacts only. One seed episode to get a real stored row, then one read episode
against it, dumping every round's render counts and the two quantities the budget competes over:
what the block costs before anything is admitted, and how long one recall line actually is.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.events import EpisodeStore                      # noqa: E402
from embodied_agent.core.fault_injection import EnvironmentController    # noqa: E402
from embodied_agent.core.interpreter import interpret                    # noqa: E402
from embodied_agent.core.runtime import build_world_state                # noqa: E402
from embodied_agent.core.skills import SkillExecutor                     # noqa: E402
from embodied_agent.core.v02 import ablation                             # noqa: E402
from embodied_agent.episodic.policy import MemoryPolicy                  # noqa: E402
from embodied_agent.episodic.store import ExperienceStore                # noqa: E402
from embodied_agent.evaluation.run import (build_scene, resolve_goal, run_one_episode,   # noqa: E402
                                           task_input)
from embodied_agent.evaluation.tasks import find_case                    # noqa: E402
from embodied_agent.planning.working_memory import RENDER_BUDGET_CHARS   # noqa: E402

CASE, KIND = "dev_c1", "dev"
root = tempfile.mkdtemp(prefix="p3c_render_")
print("root", root, "RENDER_BUDGET_CHARS", RENDER_BUDGET_CHARS)

store = ExperienceStore.load(os.path.join(root, "store.jsonl"))
case = find_case(CASE)
resolution = resolve_goal(case, 0, None, root)
run_one_episode(case, 0, "B", resolution, None, root, KIND, frames=False, perceive="privileged",
                ablation=ablation("full"), experience_store=store, policy="memory")
print("stored rows:", [r.experience_id for r in store.all()])
for record in store.all():
    line = record.as_reference()
    print(f"  as_reference len={len(line)}")

from embodied_agent.episodic.retrieval import query_from, render_rows, retrieve  # noqa: E402

query = query_from(task_kind=KIND, regions=["tray_a", "tray_b"], kinds=[], predicates=[],
                   failure_codes=[])
rows = retrieve(query, store.all(), measured={}, limit=3, round_index=1)
for line in render_rows(rows, {r.experience_id: r for r in store.all()}):
    print(f"  rendered line len={len(line)}")
    print("   ", line.replace("\n", "\n    "))

from embodied_agent.episodic.arm import build_episodic_arm                # noqa: E402

scene = build_scene(case)
try:
    world = build_world_state(scene, 1, "obs_0001", case.verify)
    task = task_input(case)
    goal = interpret(task, world)
    ep_dir = os.path.join(root, "read")
    os.makedirs(ep_dir, exist_ok=True)
    episode_id = "probe.read"
    executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
    events = EpisodeStore(ep_dir, episode_id)
    runtime = build_episodic_arm(case=case, scene=scene, executor=executor, store=events,
                                 run_dir=ep_dir, episode_id=episode_id, budgets=case.budgets,
                                 perceive="privileged", ablation=ablation("full"),
                                 environment=EnvironmentController(scene, case.fresh_events()),
                                 experience_store=store, task_kind=KIND)
    executor.world_provider = runtime.observe
    policy = MemoryPolicy()
    result = runtime.run_episode(task, goal, policy, mode="B")
    print("terminal", result.terminal_status, "rounds", result.decision_rounds)
    for entry in policy.trace:
        print(f"  policy round {entry.get('round_index')}: subgoal={entry.get('subgoal')} "
              f"order={entry.get('memory_order')} declined={entry.get('memory_declined')} "
              f"followed={entry.get('memory_followed')} recalled={entry.get('recalled')}")
    for e in events.read_all():
        if e["type"] == "memory_retrieval":
            for row in e["payload"]["retrieved"]:
                clashes = row["provenance"].get("contradictions") or []
                if clashes:
                    print(f"round {e['payload']['round_index']}: contradicted "
                          f"{row['experience_id']}: {clashes}")
    for e in events.read_all():
        if e["type"] != "working_memory":
            continue
        payload = e["payload"]
        render = payload.get("render") or {}
        print(f"round {payload.get('round_index')}: budget={render.get('budget')} "
              f"recall_budget={render.get('recall_budget')} "
              f"uncuttable={render.get('uncuttable_chars')} "
              f"debt_chars={render.get('debt_chars')} "
              f"recalled_chars={render.get('recalled_chars')} "
              f"page_chars={render.get('page_chars')} "
              f"offered={render.get('recalled_offered')} shown={render.get('recalled_shown')} "
              f"dropped={render.get('recalled_dropped')} "
              f"lines_dropped={payload.get('lines_dropped')} "
              f"over={render.get('over_budget')} page_over={render.get('page_over_budget')}")
finally:
    scene.close()
