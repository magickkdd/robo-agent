import subprocess
import sys

PY = "/home/czx/mwvenv/bin/python"
print(subprocess.run(["ls", "-la", PY], capture_output=True, text=True).stdout)
print(subprocess.run([PY, "-V"], capture_output=True, text=True).stdout)
mods = "import metaworld, mujoco, pydantic, numpy; print('metaworld/mujoco/pydantic/numpy ok')"
r = subprocess.run([PY, "-c", mods], capture_output=True, text=True)
print(r.stdout, r.stderr[-600:])
