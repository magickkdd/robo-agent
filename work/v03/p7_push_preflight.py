"""What is actually tracked, and how big would the push be?

`runs/` is 143M in the worktree and `.gitignore` does not mention it, `whatever/` and `outputs/`
are 5.0M and 1.5M with names that say nothing. Whether those are tracked decides whether this
push is 44M or 200M, and pushing 200M of run artefacts to a public repository is a decision,
not a detail.
"""
import os
import subprocess

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
os.chdir(REPO)


def sh(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


tracked = sh("ls-files", "-z").split("\0")
tracked = [f for f in tracked if f]
print(f"tracked files: {len(tracked)}")

groups = {}
for f in tracked:
    top = f.split("/")[0] if "/" in f else "(top level)"
    try:
        size = os.path.getsize(f)
    except OSError:
        size = 0
    g = groups.setdefault(top, [0, 0])
    g[0] += 1
    g[1] += size

print()
print(f"{'top-level':34s} {'files':>7s} {'bytes':>14s}")
for top, (n, size) in sorted(groups.items(), key=lambda kv: -kv[1][1]):
    print(f"{top:34s} {n:>7d} {size:>14,d}")

total = sum(v[1] for v in groups.values())
print(f"{'TOTAL':34s} {len(tracked):>7d} {total:>14,d}  ({total / 1e6:.1f} MB)")

print()
print("=== the largest tracked files ===")
rows = []
for f in tracked:
    try:
        rows.append((os.path.getsize(f), f))
    except OSError:
        pass
for size, f in sorted(rows, reverse=True)[:15]:
    print(f"  {size / 1e6:>8.2f} MB  {f}")

print()
print("=== untracked-but-present top-level dirs (would NOT be pushed) ===")
for d in ("runs", "whatever", "outputs", ".env"):
    p = os.path.join(REPO, d)
    if os.path.exists(p):
        untracked_here = [f for f in (
            sh("ls-files", "--others", "--exclude-standard", "-z", "--", d).split("\0"))]
        untracked_here = [f for f in untracked_here if f]
        print(f"  {d:12s} present, untracked files: {len(untracked_here)}")
    else:
        print(f"  {d:12s} absent")

print()
print("=== ignored files that exist (must NOT be pushed) ===")
ig = [f for f in sh("ls-files", "--others", "--ignored", "--exclude-standard",
                    "-z").split("\0") if f]
print(f"  {len(ig)} ignored paths, e.g. {ig[:5]}")
print(f"  .env tracked? {'configs/.env' in tracked or '.env' in tracked}")
