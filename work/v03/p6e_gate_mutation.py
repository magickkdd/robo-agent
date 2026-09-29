#!/usr/bin/env python3
"""Non-vacuity for the two checks the Memory row's new verdict added.

The gate's non-vacuity pass only breaks `58`, `153/153` and the freeze needle, so a check keyed on
new material (`MEM-1`, `216`, `411`, `policy.trace`) could pass by never being exercised. Each
below removes one of those and confirms the named check goes red.
"""
import os
import subprocess
import sys
import tempfile

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
GATE = os.path.join(REPO, "work", "v03", "verify_docs_v03.py")
REPORT = os.path.join(REPO, "docs", "continuous-decision-v0.3-final-report.md")
LOG = os.path.join(REPO, "docs", "continuous-decision-v0.3-phase-log.md")
PY = "/home/czx/miniforge3/envs/embodied/bin/python"

BREAKS = [
    ("the model-seat cost", "216", "0"),
    ("the declared bound", "411", "999"),
    ("the experiment name", "MEM-1", "MEM-9"),
    ("the policy-trace reason", "编出来的负数", "一个普通的零"),
    ("the structural gap's cause", "没有 `--planner`", "不知道什么原因"),
    ("the gate's own self-repairing phrase", "em-pairs", "em_pairs"),
]


def run(path):
    env = dict(os.environ, PYTHONPATH=".")
    p = subprocess.run([PY, GATE, path, LOG], cwd=REPO, env=env, capture_output=True,
                       text=True, timeout=600)
    return p.stdout, p.returncode


def red_checks(out):
    return {line.split("|")[0].strip()[5:].strip() for line in out.splitlines()
            if line.startswith("FAIL")}


base_out, base_rc = run(REPORT)
base_red = red_checks(base_out)
print(f"baseline rc={base_rc}, red={sorted(base_red) or 'none'}")
problems = []

text = open(REPORT, encoding="utf-8").read()
for name, needle, replacement in BREAKS:
    if needle not in text:
        print(f"  ERROR  {name}: {needle!r} not in the report — the check cannot be exercised")
        problems.append(f"needle absent: {name}")
        continue
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text.replace(needle, replacement))
        tmp = f.name
    try:
        out, _rc = run(tmp)
        # newly red = red(mutated) minus red(baseline). The first version of this computed it the
        # other way round, and since a green baseline has an empty red set the difference was
        # always empty — so all five removals were reported VACUOUS while the checks were in fact
        # biting. A harness that cannot see a failure it is looking for is the same defect as a
        # check that cannot fail.
        newly = red_checks(out) - base_red
        if newly:
            print(f"  BITES  {name}  ({needle!r} -> {replacement!r})")
            for g in sorted(newly):
                print(f"          went red: {g}")
        else:
            print(f"  VACUOUS {name}  <-- removing {needle!r} changed nothing")
            problems.append(f"vacuous: {name}")
    finally:
        os.unlink(tmp)

print()
if problems:
    print(f"RESULT: {len(problems)} problem(s)")
    for p in problems:
        print(f"  - {p}")
    raise SystemExit(1)
print(f"RESULT: all {len(BREAKS)} removals turned a check red")
