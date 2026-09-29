import glob
import json
import sys

for path in sys.argv[1:] or glob.glob("/tmp/v03_gate2/**/errors.jsonl", recursive=True):
    print("===", path)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            print(json.dumps(rec, ensure_ascii=False, indent=1)[:1200])
