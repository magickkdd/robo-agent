#!/usr/bin/env bash
# Non-vacuity: reintroduce each bug the spend contract guards, and confirm the tests go red.
# A contract that cannot fail is a comment. Each mutation below is one of the four mistakes
# that actually shipped, applied to the production function, then reverted.
set -u
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
export PYTHONPATH=.
P=/home/czx/miniforge3/envs/embodied/bin/python
SRC=embodied_agent/evaluation/run.py
cp "$SRC" /tmp/run.py.bak

restore() { cp /tmp/run.py.bak "$SRC"; }
trap restore EXIT

run_contract() { $P -m pytest tests/contract/test_v03_spend_accounting.py -q 2>&1 | tail -1; }

echo "baseline (unmutated):"
echo "  $(run_contract)"

mutate() {  # $1 = description, $2 = python that rewrites the function
  restore
  $P - "$2" <<'PY'
import re, sys
p = "embodied_agent/evaluation/run.py"
src = open(p, encoding="utf-8").read()
old, new = eval(sys.argv[1])
assert old in src, "mutation target not found"
open(p, "w", encoding="utf-8").write(src.replace(old, new, 1))
PY
  local out
  out=$(run_contract)
  if echo "$out" | grep -qE 'failed|error'; then
    echo "  BITES  $1"
    echo "         $out"
  else
    echo "  VACUOUS $1  <-- the contract did not notice"
  fi
}

echo
echo "mutation 1: read only the per-episode ledgers (the original 2,936)"
mutate 'read only the per-episode ledgers, drop the goal ledger' \
  '("goal = _tally(_rows(os.path.join(run_root, GOAL_LEDGER_NAME)))",
   "goal = {\"rows\": 0, \"http_requests\": 0, \"prompt_tokens\": 0, \"completion_tokens\": 0, \"errored_rows\": 0, \"errored_http_requests\": 0, \"http_requests_successful\": 0, \"latency_s_min\": None, \"latency_s_max\": None}")'

echo
echo "mutation 2: sum the cumulative counter instead of the per-call one"
mutate 'sum http_requests_total (the cumulative counter) instead of this_call' \
  '("            this_call = int(r.get(\"http_requests_this_call\") or 0)",
   "            this_call = int(r.get(\"http_requests_total\") or 0)")'

echo
echo "mutation 3: drop rows that ended in an error"
mutate 'skip errored rows entirely' \
  '("            if r.get(\"error\"):", "            if False:")'

echo
echo "mutation 4: read tokens off the top level instead of usage"
mutate 'read prompt_tokens off the row instead of under usage' \
  '("            prompt += int(usage.get(\"prompt_tokens\") or 0)",
   "            prompt += int(r.get(\"prompt_tokens\") or 0)")'

echo
echo "mutation 5: report the successful count as the spend (the v0.3 report's mistake)"
mutate 'report http_requests_successful as the spend' \
  '("        \"http_requests\": _sum(\"http_requests\"),",
   "        \"http_requests\": _sum(\"http_requests_successful\"),")'

restore
echo
echo "restored; re-running to confirm the file is clean:"
echo "  $(run_contract)"
