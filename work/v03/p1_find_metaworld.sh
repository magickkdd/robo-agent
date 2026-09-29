#!/usr/bin/env bash
# The MuJoCo backend needs `metaworld`, and this interpreter cannot import it. The appendix H
# camera batches ran on this same channel, so it was importable in this environment at some
# point. Find where it lives before concluding it is gone.
set -u
echo "### conda envs"
ls -d /home/czx/miniforge3/envs/* 2>/dev/null
echo
echo "### any metaworld on disk (site-packages or a source checkout)"
find /home/czx -maxdepth 6 -name 'metaworld*' -not -path '*/node_modules/*' 2>/dev/null | head -20
echo
echo "### which interpreters can import it"
for PY in /home/czx/miniforge3/envs/*/bin/python /home/czx/bstvenv/bin/python /usr/bin/python3; do
  [ -x "$PY" ] || continue
  if "$PY" -c "import metaworld" >/dev/null 2>&1; then
    echo "  OK   $PY  ($("$PY" -V 2>&1))"
  else
    echo "  no   $PY  ($("$PY" -V 2>&1))"
  fi
done
echo
echo "### pip metadata in the embodied env"
/home/czx/miniforge3/envs/embodied/bin/python -m pip list 2>/dev/null | grep -iE 'metaworld|mujoco|gym|glfw' || echo "  (pip list unavailable or no match)"
