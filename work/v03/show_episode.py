import glob
import json
import sys

paths = sys.argv[1:] or glob.glob("/tmp/v03_gate2/**/events.jsonl", recursive=True)
for path in paths:
    print("===", path)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            p = r.get("payload") or {}
            keys = ("condition", "provider", "requested_model", "finish_reason", "raw_chars",
                    "http_requests", "prompt_tokens", "completion_tokens", "error",
                    "channel", "modality", "image_ref")
            brief = {k: p[k] for k in keys if k in p}
            print(f"  {r.get('type'):<18} {json.dumps(brief, ensure_ascii=False)[:320]}")
print()
print("=== files in the episode dir ===")
for d in glob.glob("/tmp/v03_gate2/**/episodes/*", recursive=True):
    if not __import__("os").path.isdir(d):
        continue
    for root, dirs, files in __import__("os").walk(d):
        for f in files:
            p = __import__("os").path.join(root, f)
            print(f"  {p}  {__import__('os').path.getsize(p)}B")
