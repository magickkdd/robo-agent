import inspect
import os

os.environ.setdefault("MUJOCO_GL", "osmesa")

from embodied_agent.benchmark_mujoco.env import MuJoCoBackend

print("MuJoCoBackend.__init__:", inspect.signature(MuJoCoBackend.__init__))
src = inspect.getsource(MuJoCoBackend)
for i, line in enumerate(src.splitlines(), 1):
    if "layout" in line.lower():
        print(f"{i:4d}: {line}")
