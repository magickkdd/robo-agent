#!/usr/bin/env bash
# Re-run the published v0.2 command in this round (phase-log rule: a cited
# negative reading fails by "the world grew something new", and the new thing
# never announces itself), then add the one ignore line for a stray runtime log.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4

echo "=== dirty_diff_sha256 as published by v0.2 (expect prefix b43d31f369f688d3) ==="
git diff --binary | sha256sum | cut -c1-16
echo "HEAD: $(git rev-parse HEAD)"

if ! grep -qx 'MUJOCO_LOG.TXT' .gitignore; then
  printf '\n# a GLFW initialisation log from a failed GUI attempt; a runtime artefact, not evidence\nMUJOCO_LOG.TXT\n' >> .gitignore
  echo "appended MUJOCO_LOG.TXT to .gitignore"
fi
echo "--- .gitignore now ---"
cat .gitignore
