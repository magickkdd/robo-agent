import glob
import json
import os
import sys

roots = sys.argv[1:] or sorted(glob.glob("/tmp/v03_gate2/*/"))
for root in roots:
    print("===", root)
    for name in ("manifest.json", "run_manifest.json"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            m = json.load(open(p, encoding="utf-8"))
            for k in sorted(m):
                if k in ("git", "dependency_versions", "python", "platform", "recorded_at"):
                    continue
                print(f"  {k}: {json.dumps(m[k], ensure_ascii=False)[:400]}")
    for f in glob.glob(os.path.join(root, "**", "model_calls.jsonl"), recursive=True):
        rows = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        print(f"  model_calls.jsonl ({f}): {len(rows)} rows")
        for r in rows:
            print("   ", json.dumps({k: r.get(k) for k in
                                     ("kind", "provider", "requested_model", "returned_model",
                                      "finish_reason", "raw_chars", "content_field_present",
                                      "reasoning_chars", "http_requests_this_call",
                                      "prompt_tokens", "completion_tokens", "latency_s",
                                      "image_ref")}, ensure_ascii=False)[:500])
