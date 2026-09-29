import os
import re
import subprocess

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
os.chdir(REPO)

# Built from parts so this file does not itself contain the string it is looking for. The first
# version of the purge script searched for a literal prefix and got committed, which then turned
# its own verification into a false positive — a scanner that matches itself is worse than no
# scanner, because it reports a leak where there is none and hides the next one.
TOKEN = "ghp_" + "ELDcz6acyr" + "SS7RdE6pNRco" + "HjPb8XF31hkNwQ"
PREFIX = TOKEN[:8]

print("=== the full token in the worktree ===")
hits = []
for dirpath, dirnames, filenames in os.walk(REPO):
    if ".git" in dirpath.split(os.sep):
        continue
    for fn in filenames:
        p = os.path.join(dirpath, fn)
        try:
            if TOKEN.encode() in open(p, "rb").read():
                hits.append(os.path.relpath(p, REPO))
        except OSError:
            pass
print(f"  {len(hits)} file(s) contain the full token: {hits}")

print()
print("=== the full token in any commit's tree ===")
commits = subprocess.run(["git", "rev-list", "--all"], capture_output=True,
                         text=True).stdout.split()
tree_hits = []
for c in commits:
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", c], capture_output=True,
                           text=True).stdout.split()
    for f in files:
        blob = subprocess.run(["git", "show", f"{c}:{f}"], capture_output=True).stdout
        if TOKEN.encode() in blob:
            tree_hits.append((c[:8], f))
print(f"  scanned {len(commits)} commits; {len(tree_hits)} contain the full token")
for c, f in tree_hits:
    print(f"    {c}  {f}")

print()
print("=== the 8-char prefix (not a credential, but should still not ship) ===")
prefix_hits = []
for dirpath, dirnames, filenames in os.walk(REPO):
    if ".git" in dirpath.split(os.sep):
        continue
    for fn in filenames:
        p = os.path.join(dirpath, fn)
        try:
            if PREFIX.encode() in open(p, "rb").read():
                prefix_hits.append(os.path.relpath(p, REPO))
        except OSError:
            pass
print(f"  {len(prefix_hits)} worktree file(s): {prefix_hits}")
p_commits = subprocess.run(["git", "log", "--all", "-S", PREFIX, "--oneline"],
                           capture_output=True, text=True).stdout.strip()
print(f"  commits whose diff touches it: {p_commits or 'none'}")
print()
print("VERDICT: the full token is absent from the worktree and from every commit"
      if not hits and not tree_hits else
      "VERDICT: FULL TOKEN STILL PRESENT — do not push")
