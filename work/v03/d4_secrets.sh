#!/usr/bin/env bash
# Classify the 5 secret-shaped matches WITHOUT printing any value: show the
# matched line with anything that looks like a credential masked out.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
for f in docs/bst/Bottleneck-Report.md docs/bst/commands.md embodied_agent/benchmark/cli.py \
         docs/continuous-decision-phase-log.md embodied_agent/evaluation/tasks.py; do
  echo "----- $f"
  grep -nE 'sk-[A-Za-z0-9]{8,}|Bearer [A-Za-z0-9._-]{10,}|API_KEY *= *["'"'"'][^"'"'"']{8,}' "$f" \
    | sed -E 's/sk-[A-Za-z0-9]{4,}/sk-***MASKED***/g; s/Bearer [A-Za-z0-9._-]{4,}/Bearer ***MASKED***/g' \
    | cut -c1-220
done

echo
echo "=== does the repo .env value appear in any tracked-or-untracked candidate? ==="
# compare hashes, never print either side
ENVKEY=$(grep -E '^LLM_API_KEY=' .env | head -1 | cut -d= -f2-)
if [ -z "$ENVKEY" ]; then echo "LLM_API_KEY empty in .env"; fi
found=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  if grep -qF -- "$ENVKEY" "$f" 2>/dev/null; then echo "!!! .env LLM_API_KEY value found verbatim in $f"; found=1; fi
done < /tmp/v03_untracked.txt
[ "$found" -eq 0 ] && echo "no .env value appears verbatim in any candidate file"
