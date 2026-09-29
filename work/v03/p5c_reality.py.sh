#!/usr/bin/env bash
# What did P5-c actually run? The arm list is settled by the directory names and
# matrix.json on disk, not by the report's prose.
set -u
ls -d /tmp/p5c/*/ 2>/dev/null | sed 's|/$||' | head -40
echo "--- matrix.json ---"
if [ -f /tmp/p5c/matrix.json ]; then
  /home/czx/miniforge3/envs/embodied/bin/python - <<'PY'
import json
d = json.load(open("/tmp/p5c/matrix.json"))
print("top keys:", sorted(d) if isinstance(d, dict) else type(d))
if isinstance(d, dict):
    for k, v in d.items():
        s = json.dumps(v, ensure_ascii=False)
        print(f"{k}: {s[:600]}")
PY
else
  echo "absent"
fi
echo "--- run directories (the 12 cells) ---"
find /tmp/p5c -maxdepth 3 -name manifest.json 2>/dev/null | head -20
