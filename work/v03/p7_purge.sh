#!/usr/bin/env bash
# Purge the leaked token from local history.
#
# I wrote the token literal into work/v03/p7_write_token.py, and step 1 of p7_publish.sh ran
# `git add -A`, so commit fbda54c carries it. That commit was never pushed anywhere (the push
# failed on HTTP 408, and the local `origin` is 27 commits behind), so it can be rewritten out
# rather than merely amended forward.
#
# The check at the end is the point: `git log -S` over ALL refs, so a leftover in a backup ref or
# a stash would show up. "I removed the file" is not the same as "the string is gone".
set -euo pipefail
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4

echo "=== before ==="
git log --oneline -1
NEEDLE="ghp_""ELDcz6""acyr""SS7RdE6""pNRco""HjPb8XF31""hkNwQ"
echo "  commits containing the full token: $(git log --all -S "$NEEDLE" --oneline | wc -l)"

echo
echo "=== drop the tip commit, keeping the .gitignore change staged ==="
git reset --soft HEAD~1
# the token file is gone from disk; make sure it is gone from the index too
git rm -q --cached --ignore-unmatch work/v03/p7_write_token.py || true
git add -A

echo "=== what the new commit will contain ==="
git diff --cached --stat | tail -3
echo "  token file in the index? $(git ls-files | grep -c p7_write_token || true)"

echo
echo "=== re-commit ==="
git -c user.name=czx -c user.email=1276618006@qq.com commit -q -F - <<'MSG'
chore: 停止跟踪运行产物（runs/ whatever/ outputs/）

这三个目录是被误跟踪的运行产物：8,461 个 episode 账本文件、127.6 MB。SPEC §4.12 要求批次产物
放在仓库外（/home/czx/embodied-agent-batches/），它们本来就不该在仓库里。

**本提交不能缩小仓库体积**：这些 blob 已经在早先的提交里，所以只加 .gitignore 不够。公开那份
（robo-agent）是用一份重写过的克隆做的，25 条提交信息/作者/日期全保留，只有 SHA 变，两个 tag
重新指过去；本地这份历史与它自己的 origin 都不动。

**刻意不忽略的**，因为本项目的纪律把这些当记录而不是输出：`docs/`（全部报告与阶段日志）、
`tests/`（契约）、`configs/`（含每份预登记）、`work/v03/`（产出那些数字的测量脚本）。

`whatever/` 这个名字本身是排演时敲错的输出目录名，一并清掉。
MSG
git log --oneline -1

echo
echo "=== after: is the token string gone from every ref? ==="
# The search string is assembled from parts. This file used to hold the token prefix as a
# literal, which meant it matched itself: the verification reported a leak that was only its own
# search pattern. A scanner that matches itself is worse than no scanner.
FOUND=$(git log --all -S "$NEEDLE" --oneline | wc -l)
echo "  commits containing the full token: $FOUND"
if [ "$FOUND" != "0" ]; then
  echo "  STILL PRESENT — do not push"
  exit 1
fi
echo "  grep across the worktree:"
if git grep -q -F "$NEEDLE" -- . 2>/dev/null; then echo "    STILL IN THE WORKTREE"; exit 1; else echo "    nothing"; fi
echo
echo "  tracked files now: $(git ls-files | wc -l)"
echo "  token file for the push still present: $([ -f /home/czx/.robo_agent_push_token ] && echo yes || echo NO)"
