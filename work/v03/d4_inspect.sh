#!/usr/bin/env bash
# D4 pre-commit inspection: what is about to be committed, and is anything in it
# a secret, a build artefact, or a batch output that does not belong in the tree?
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4

echo "=== 1. tracked modifications ==="
git diff --stat HEAD | tail -3

echo
echo "=== 2. untracked (respecting .gitignore) ==="
git ls-files --others --exclude-standard | wc -l
git ls-files --others --exclude-standard | sed -n '1,200p'

echo
echo "=== 3. untracked files larger than 200 kB ==="
git ls-files --others --exclude-standard | while IFS= read -r f; do
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  if [ "$sz" -gt 200000 ]; then echo "$sz $f"; fi
done

echo
echo "=== 4. is .env ignored? ==="
git check-ignore -v .env || echo "!!! .env NOT ignored"

echo
echo "=== 5. secret scan over everything that would be added ==="
# pattern class only; matched file+line, never the value
git ls-files --others --exclude-standard > /tmp/v03_untracked.txt
git diff HEAD --name-only >> /tmp/v03_untracked.txt
hits=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in *.py|*.json|*.md|*.yaml|*.yml|*.txt|*.sh) ;; *) continue ;; esac
  n=$(grep -cE 'sk-[A-Za-z0-9]{8,}|Bearer [A-Za-z0-9._-]{10,}|API_KEY *= *["'"'"'][^"'"'"']{8,}' "$f" 2>/dev/null || true)
  if [ "${n:-0}" -gt 0 ]; then echo "MATCH($n) $f"; hits=$((hits+1)); fi
done < /tmp/v03_untracked.txt
echo "files with secret-shaped matches: $hits"
