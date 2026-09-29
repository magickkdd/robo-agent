"""E2's four infrastructure errors, named. The v0.2 report's rule is that a crash is the
channel's event, not an outcome the agent earned, and the only honest thing to do with one is
say what it was — so this prints the error sentence per cell rather than a count."""
import glob
import json
import os

ROOT = "/home/cxz"
ROOT = "/home/cxz".replace("cxz", "czx")
for cell in ("long_horizon_vlm", "long_horizon_stub"):
    print(f"\n========== {cell}")
    for path in sorted(glob.glob(f"{ROOT}/embodied-agent-batches/v03/e2/{cell}/**/errors.jsonl",
                                 recursive=True)):
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            rec = json.loads(line)
            print(f"  where={rec.get('where')}")
            print(f"  error={str(rec.get('error'))[:300]}")
            tail = str(rec.get("traceback_tail") or "")
            frames = [ln.strip() for ln in tail.splitlines() if "File \"" in ln]
            for fr in frames[-6:]:
                print("    ", fr[:150])
