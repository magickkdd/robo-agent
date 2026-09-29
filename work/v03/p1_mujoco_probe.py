import os

os.environ.setdefault("MUJOCO_GL", "osmesa")

from embodied_agent.benchmark_mujoco import env as mwenv

print("module names:", [n for n in dir(mwenv) if not n.startswith("_")][:40])
for name in ("TASKS", "DEFAULT_WORKSPACE"):
    print(name, "=", getattr(mwenv, name, None))
for fn in ("make_env", "build_env", "make_rig", "MuJoCoRig", "Rig"):
    print(fn, "->", getattr(mwenv, fn, None))
