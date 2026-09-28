#!/usr/bin/env bash
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
echo "tracked under runs/   : $(git ls-files runs | wc -l)"
echo "tracked under work/   : $(git ls-files work | wc -l)"
echo "tracked under outputs/: $(git ls-files outputs | wc -l)"
echo "tracked under configs/: $(git ls-files configs | wc -l)"
echo "tracked under docs/   : $(git ls-files docs | wc -l)"
echo "tracked total         : $(git ls-files | wc -l)"
echo
echo "--- untracked .py under embodied_agent/ (these are what untracked_code_state hashes) ---"
git ls-files --others --exclude-standard -- embodied_agent | wc -l
echo
echo "--- last commit subject lines style ---"
git log --format='%s' -6
