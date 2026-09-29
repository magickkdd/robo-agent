#!/usr/bin/env python3
"""Non-vacuity for the seat contract: break each guarantee, confirm red, restore.

Written in Python rather than bash-with-eval because two of the earlier shell attempts failed
*silently as scripts* — an unterminated string inside `eval()` left the file unmutated, and the
harness reported "VACUOUS" for a mutation it had never applied. A non-vacuity check that can
report a false negative is worse than none, because it looks like evidence.

So every mutation here asserts it actually changed the file before running the tests, and a
mutation that fails to apply is reported as an ERROR, never as a pass.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
PY = "/home/czx/miniforge3/envs/embodied/bin/python"
CONTRACT = "tests/contract/test_v03_episodic_seat.py"
RUNS = "embodied_agent/evaluation/episodic_runs.py"
CLI = "embodied_agent/cli.py"
MEM = "embodied_agent/evaluation/episodic_metrics.py"

MUTATIONS = [
    ("keep policy=memory on a model seat (two decision makers)",
     RUNS,
     '    policy = "memory" if planner_kind == "rule" else None',
     '    policy = "memory"'),
    ("drop the store on a model seat (the object of the measurement)",
     RUNS,
     "experience_store=store)",
     "experience_store=None)"),
    ("accept a model seat with no named seat",
     RUNS,
     '    if planner_kind != "rule" and not model_config:',
     "    if False:"),
    ("accept the test-double planner",
     RUNS,
     '    if planner_kind == "fixture":',
     "    if False:"),
    ("record the seat as rule whatever ran (the artifact lies)",
     RUNS,
     '"policy": policy, "planner": planner_kind,',
     '"policy": policy, "planner": "rule",'),
    ("drop the seat note, so a model seat reads like a rule seat",
     RUNS,
     '"planner": ledger.get("planner"), "policy": ledger.get("policy"),',
     '"planner": None, "policy": None,'),
    ("let the CLI take a --policy after all",
     CLI,
     '    em.add_argument("--planner", default="rule", choices=["rule", "deepseek"],',
     '    em.add_argument("--policy", default=None)\n'
     '    em.add_argument("--planner", default="rule", choices=["rule", "deepseek"],'),
    ("let the CLI accept the test-double planner",
     CLI,
     'choices=["rule", "deepseek"],',
     'choices=["rule", "deepseek", "fixture"],'),
    ("stop naming the seat in the CLI output",
     CLI,
     '    print(f"seat: planner={artifact[\'planner\']} policy={artifact[\'policy\']} "',
     '    print(f"seat: (unreported) "'),
    ("drop the seat from MEM-1, the table §11 actually publishes",
     MEM,
     '              "planner": _seat(root).get("planner"), "policy": _seat(root).get("policy"),',
     '              "planner": None, "policy": None,'),
    ("make MEM-1's seat note the rule one whatever ran",
     MEM,
     '    return (f"{planner} seat: the reader is the model, so the two arms differ in memory AND in "',
     '    return ("rule seat: the reader is MemoryPolicy, so the arms differ only in memory"\n'
     '            if True else (f"{planner} seat: the reader is the model, so the two arms differ in memory AND in "'),
    ("let MEM-1 guess the seat when the ledger does not record one",
     MEM,
     '        return ("seat not recorded in this directory\'s pairs_run.json; it predates the seat "',
     '        return ("rule seat: guessed"  # noqa\n'
     '            "seat not recorded in this directory\'s pairs_run.json; it predates the seat "'),
]


def run_contract() -> str:
    env = dict(os.environ, PYTHONPATH=".")
    p = subprocess.run([PY, "-m", "pytest", CONTRACT, "-q"],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    return (p.stdout.strip().splitlines() or ["<no output>"])[-1]


def main() -> int:
    os.chdir(REPO)
    backup = tempfile.mkdtemp(prefix="seatmut-")
    for f in (RUNS, CLI, MEM):
        shutil.copy(f, os.path.join(backup, os.path.basename(f)))

    def restore():
        for f in (RUNS, CLI, MEM):
            shutil.copy(os.path.join(backup, os.path.basename(f)), f)

    problems = []
    try:
        base = run_contract()
        print(f"baseline: {base}")
        if "failed" in base or "error" in base:
            problems.append(f"baseline is not green: {base}")

        print()
        for desc, path, old, new in MUTATIONS:
            restore()
            src = open(path, encoding="utf-8").read()
            if old not in src:
                print(f"  ERROR   {desc}")
                print(f"          target not found in {path}: {old[:70]!r}")
                problems.append(f"mutation never applied: {desc}")
                continue
            open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
            # prove the mutation landed before believing anything about the tests
            if open(path, encoding="utf-8").read() == src:
                print(f"  ERROR   {desc} (file unchanged)")
                problems.append(f"mutation did not change the file: {desc}")
                continue
            out = run_contract()
            if re.search(r"failed|error", out):
                print(f"  BITES   {desc}")
                print(f"          {out}")
            else:
                print(f"  VACUOUS {desc}  <-- the contract did not notice")
                problems.append(f"vacuous: {desc}")
    finally:
        restore()

    final = run_contract()
    print(f"\nrestored: {final}")
    if "failed" in final or "error" in final:
        problems.append(f"not clean after restore: {final}")
    shutil.rmtree(backup, ignore_errors=True)

    print()
    if problems:
        print(f"RESULT: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"RESULT: all {len(MUTATIONS)} mutations bit and the tree restored clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
