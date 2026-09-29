"""Scan every TRACKED file for credentials, without printing any value.

Pushing to a public GitHub repository publishes whatever is tracked, so this runs over
`git ls-files` rather than over the worktree — a file that is tracked is a file that ships, and
`runs/` alone is 8,461 of them. Values are never printed: a scanner that echoes the secret it
found has turned a check into a second copy of the secret.

The comparison against `.env` is by hash and by substring, both without printing either side.
"""
import hashlib
import os
import re
import subprocess

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
os.chdir(REPO)

PATTERNS = [
    ("openai-style key", re.compile(rb"sk-[A-Za-z0-9]{20,}")),
    ("bearer token", re.compile(rb"Bearer [A-Za-z0-9._\-]{20,}")),
    ("github token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws access key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("slack token", re.compile(rb"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private key block", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("assigned api key", re.compile(
        rb"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']")),
    ("bearer in a url", re.compile(rb"(?i)https?://[^/\s:@]+:[^/\s:@]+@")),
]

tracked = [f for f in subprocess.run(["git", "ls-files", "-z"], capture_output=True
                                     ).stdout.split(b"\0") if f]
tracked = [f.decode() for f in tracked]
print(f"scanning {len(tracked)} tracked files...")

hits = []
for rel in tracked:
    try:
        with open(rel, "rb") as f:
            blob = f.read()
    except OSError:
        continue
    if not blob:
        continue
    for name, pat in PATTERNS:
        for m in pat.finditer(blob):
            line = blob[:m.start()].count(b"\n") + 1
            hits.append((rel, line, name))

print()
if hits:
    print(f"POTENTIAL CREDENTIALS: {len(hits)} match(es) — file, line, kind; NO VALUES PRINTED")
    for rel, line, name in hits[:60]:
        print(f"  {rel}:{line}  {name}")
else:
    print("no credential-shaped string in any tracked file")

print()
print("=== the .env value, compared without printing it ===")
env_path = os.path.join(REPO, ".env")
if not os.path.exists(env_path):
    print("  no .env on this machine")
else:
    values = []
    for line in open(env_path, encoding="utf-8"):
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            v = v.strip().strip("\"'")
            if len(v) >= 8:
                values.append((k.strip(), v))
    print(f"  .env defines {len(values)} value(s) of length >= 8: "
          f"{[k for k, _ in values]}")
    for k, v in values:
        digest = hashlib.sha256(v.encode()).hexdigest()[:12]
        verbatim = []
        for rel in tracked:
            try:
                if v.encode() in open(rel, "rb").read():
                    verbatim.append(rel)
            except OSError:
                continue
        print(f"  {k}: sha256[:12]={digest}  appears verbatim in {len(verbatim)} "
              f"tracked file(s) {verbatim[:5]}")
    print("  (a config that names the env var is fine; the value itself appearing in a")
    print("   tracked file is not)")

print()
print("=== what the configs actually record about credentials ===")
for rel in sorted(f for f in tracked if f.startswith("configs/models/")):
    text = open(rel, encoding="utf-8", errors="replace").read()
    envs = re.findall(r"api_key_env:\s*(\S+)", text)
    literals = re.findall(r"api_key:\s*(\S+)", text)
    print(f"  {rel}: api_key_env={envs} api_key_literals={len(literals)}")
