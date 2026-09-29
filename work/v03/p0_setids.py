from embodied_agent.evaluation.tasks import build_set

for name in ("smoke", "long_horizon", "em"):
    print(name, "->", [c.task_id for c in build_set(name)])
