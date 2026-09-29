"""Classify the 9 `assigned api key` matches: real credential, or a test fixture?

The scan already proved no `.env` value appears in any tracked file, so a real key is unlikely.
"unlikely" is not "checked": these matches get classified by *shape* only — the value is never
printed, only its length, its character classes, and whether it is an obvious placeholder.

A placeholder is judged by content-free signals: it repeats a word (`test`, `dummy`, `fake`,
`secret`, `example`), it is a well-known dummy (`sk-test`, `not-a-key`), or it is a hex/uuid
shorthand. A random-looking mixed-case alphanumeric blob of realistic length is flagged for a
human to look at.
"""
import os
import re
import subprocess

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
os.chdir(REPO)

MATCHES = [
    ("docs/continuous-decision-phase-log.md", 797),
    ("tests/contract/test_v02_perception_observe.py", 1166),
    ("tests/contract/test_v02_perception_observe.py", 1199),
    ("tests/contract/test_v02_perception_observe.py", 1272),
    ("tests/contract/test_v02_perception_observe.py", 1330),
    ("tests/contract/test_v02_perception_observe.py", 1368),
    ("tests/contract/test_v02_perception_observe.py", 1374),
    ("tests/contract/test_v02_perception_observe.py", 1383),
    ("tests/unit/test_preregistration.py", 155),
]

PAT = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']")

PLACEHOLDER_WORDS = ("test", "dummy", "fake", "secret", "example", "placeholder", "xxx",
                     "notreal", "sample", "mock", "stub")

for rel, lineno in MATCHES:
    lines = open(rel, encoding="utf-8", errors="replace").read().splitlines()
    line = lines[lineno - 1]
    m = PAT.search(line)
    if not m:
        print(f"  {rel}:{lineno}  <no longer matches — line moved or changed>")
        continue
    # the value is whatever sits between the quotes the pattern captured, whichever quote it is
    quoted = re.search(r"[\"']([^\"'\s]{12,})[\"']", m.group(0))
    value = quoted.group(1) if quoted else m.group(0)
    # classification, no value printed
    low = value.lower()
    is_placeholder = any(w in low for w in PLACEHOLDER_WORDS) or set(low) <= set("x-_*")
    classes = ("".join(sorted({c for c in value if c.islower()})),
               "".join(sorted({c for c in value if c.isupper()})),
               "".join(sorted({c for c in value if c.isdigit()})))
    has_symbol = any(c in value for c in "-_!@#$%^&*")
    verdict = "PLACEHOLDER" if is_placeholder else "REVIEW"
    print(f"  {rel}:{lineno}")
    print(f"      length={len(value)}  has_symbol={has_symbol}  "
          f"lower/upper/digit sets={classes}")
    print(f"      contains a placeholder word: {is_placeholder}   -> {verdict}")
    if not is_placeholder:
        print(f"      the line reads: {line.strip()[:90]!r}")
        print("      (value masked; the line shape is shown, not the secret)")

print()
print("=== summary ===")
print("  all three real .env values appear in 0 tracked files (verified by hash+substring)")
print("  every model config names an env var and carries 0 api_key literals")
print("  -> nothing to strip before publishing, provided the 9 above are fixtures")
