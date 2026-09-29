"""Two things to settle before writing anything about gap 3.

1. What is the outcome field called? (`outcome` came back None for every episode.)
2. **The stub arm hit the same colour error as the VLM arm.** The v0.3 residual says the camera
   grounds by colour, so the failure should be camera-specific — but a stub reader never renders.
   Either the residual attributes the cause to the wrong component, or the guard is shared. That
   difference decides the fix: a camera-arm change if it is the camera, a shared-path change if
   not. Worth settling rather than repeating the delivered explanation.
"""
import glob
import json
import os
import re
from collections import Counter

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e2"
REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"

print("=" * 74)
print("1. the outcome field")
print("=" * 74)
s = glob.glob(os.path.join(ARCHIVE, "**", "episode_summary.json"), recursive=True)
print(f"  episode summaries found: {len(s)}")
if s:
    d = json.load(open(s[0], encoding="utf-8"))
    print(f"  top-level keys: {sorted(d.keys())}")
    for k in sorted(d):
        if "result" in k or "outcome" in k or "success" in k:
            print(f"    {k} = {json.dumps(d[k], ensure_ascii=False)[:160]}")

print()
print("=" * 74)
print("2. who raises the colour error, and who can reach it")
print("=" * 74)
# where the message is produced
hits = []
for path in glob.glob(os.path.join(REPO, "embodied_agent", "**", "*.py"), recursive=True):
    text = open(path, encoding="utf-8").read()
    for m in re.finditer(r".*declared for both.*", text):
        hits.append((os.path.relpath(path, REPO), m.group(0).strip()))
for f, line in hits:
    print(f"  {f}")
    print(f"      {line}")

print()
print("  the guard's own module, with its functions:")
DEF = re.compile(r"def ([a-z_]+)\(")
for path in glob.glob(os.path.join(REPO, "embodied_agent", "**", "grounding.py"), recursive=True):
    text = open(path, encoding="utf-8").read()
    names = DEF.findall(text)
    print(f"    {os.path.relpath(path, REPO)}: {names}")

print()
print("=" * 74)
print("3. the actual error, per arm")
print("=" * 74)
for arm in ("long_horizon_vlm", "long_horizon_stub", "long_horizon_privileged"):
    errs = Counter()
    eps = []
    for p in glob.glob(os.path.join(ARCHIVE, arm, "**", "errors.jsonl"), recursive=True):
        for line in open(p, encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                errs[str(d.get("error") or d.get("kind"))[:90]] += 1
    for p in glob.glob(os.path.join(ARCHIVE, arm, "**", "episode_summary.json"), recursive=True):
        d = json.load(open(p, encoding="utf-8"))
        eps.append((d.get("case_id"), (d.get("result") or {}).get("outcome")))
    print(f"  {arm.replace('long_horizon_', ''):12s} episodes={len(eps)}")
    for c, o in sorted(eps):
        print(f"      {str(c):34s} outcome={o}")
    for e, n in errs.items():
        print(f"      error x{n}: {e}")
    print()

print("=" * 74)
print("4. what the manifest says the arm was configured with")
print("=" * 74)
for arm in ("long_horizon_vlm", "long_horizon_stub", "long_horizon_privileged"):
    for p in glob.glob(os.path.join(ARCHIVE, arm, "**", "manifest.json"), recursive=True):
        m = json.load(open(p, encoding="utf-8"))
        print(f"  {arm.replace('long_horizon_', ''):12s} "
              f"perceive={m.get('perceive')} planner={m.get('planner')} "
              f"ablation={m.get('ablation')} offline={m.get('offline')}")
