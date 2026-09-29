#!/usr/bin/env bash
# Publish this project to https://github.com/magickkdd/robo-agent.git, source only.
#
# Three things make this more than `git push`:
#
# 1. **The artefacts are already in the history.** `runs/` (127.6 MB, 8,461 files),
#    `whatever/` and `outputs/` were tracked by mistake in the earlier commits, so adding them to
#    `.gitignore` and `git rm --cached` stops them coming back but does NOT shrink what a clone
#    downloads — the blobs are in commits 1..25. The public copy is therefore built as a separate
#    rewritten clone, and the local repository keeps its history and its own `origin` untouched.
#    All 25 commit messages, authors and dates survive; only the SHAs change, and the two tags
#    are re-created onto the rewritten commits.
#
# 2. **The token must not go to a proxy.** `/home/czx/.gitconfig` has
#    `url.https://ghfast.top/https://github.com/.insteadOf = https://github.com/`, so an ordinary
#    push to a github.com URL is silently rewritten through a third-party mirror — which would
#    hand it the `Authorization` header. Every push here runs with `GIT_CONFIG_GLOBAL=/dev/null`
#    and the identity passed with `-c`, so the URL goes straight to github.com.
#
# 3. **The token is not persisted.** It is read from a file, used as a one-shot
#    `http.extraHeader` for the push command only, and the file is removed. It is never written to
#    `.git/config`, `~/.git-credentials`, the repository, or a log line in this script.
set -euo pipefail

REPO=/home/czx/embodied-agent-robot-agent-embodied-agent-4
TARGET=https://github.com/magickkdd/robo-agent.git
NAME=czx
EMAIL=1276618006@qq.com
TOKEN_FILE=/home/czx/.robo_agent_push_token
WORK=/tmp/robo_publish
KEEP_HISTORY_STRIP="runs whatever outputs"

cd "$REPO"

echo "=== 1. commit the ignore, locally ==="
git add -A
git -c user.name="$NAME" -c user.email="$EMAIL" commit -q \
  -F - <<'MSG'
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
echo "=== 2. build a clean copy for the public repo ==="
rm -rf "$WORK"
mkdir -p "$WORK"
git clone --no-hardlinks --quiet "$REPO" "$WORK/repo"
cd "$WORK/repo"
echo "  cloned: $(git log --oneline | wc -l) commits"

echo
echo "=== 3. strip the artefacts from every commit ==="
FILTER="git rm -r --cached --ignore-unmatch -q $KEEP_HISTORY_STRIP"
git -c user.name="$NAME" -c user.email="$EMAIL" filter-branch -f --index-filter \
  "$FILTER" --prune-empty --tag-name-filter cat -- --all 2>&1 | tail -3

echo
echo "=== 4. drop filter-branch's backup refs, or the push sends the old blobs ==="
# `git filter-branch` keeps every pre-rewrite commit alive under `refs/original/`. Without
# dropping them the pack still contains all 127.6 MB of run artefacts, and the first push attempt
# died on `HTTP 408 curl 22` — a server-side timeout while receiving a pack that should have been
# a few megabytes. This is the step whose absence makes the rewrite look like it worked: the file
# count said 402 while `.git` was still 43 MB.
for ref in $(git for-each-ref --format='%(refname)' refs/original/); do
  git update-ref -d "$ref"
done
rm -rf .git/refs/original
git reflog expire --expire=now --all
git gc --prune=now --quiet
echo "  refs/original removed, reflog expired, objects pruned"

echo
echo "=== 5. what the public copy now weighs ==="
echo "  tracked files : $(git ls-files | wc -l)"
BYTES=$(git ls-files -z | xargs -0 du -cb 2>/dev/null | tail -1 | cut -f1)
echo "  tracked bytes : ${BYTES:-?}"
echo "  .git size     : $(du -sh .git | cut -f1)"
echo "  commits       : $(git log --oneline | wc -l)"
echo "  tags          : $(git tag | tr '\n' ' ')"
echo "  artefact paths anywhere in history: $(git log --all --name-only --pretty=format: \
  | grep -cE '^(runs|whatever|outputs)/')"


echo
echo "=== 6. re-create the two tags on the rewritten commits ==="
# tag-name-filter cat already carried them across; this reports rather than re-creates
for t in v0.2 continuous-decision-v0.2; do
  if git rev-parse -q --verify "refs/tags/$t" >/dev/null; then
    echo "  $t -> $(git rev-parse --short "refs/tags/$t")"
  else
    echo "  $t MISSING"
  fi
done

echo
echo "=== 7. secret re-scan on exactly what will be pushed ==="
cd "$WORK/repo"
python3 - <<'PY'
import re, subprocess
PATS = [("openai", rb"sk-[A-Za-z0-9]{20,}"), ("bearer", rb"Bearer [A-Za-z0-9._\-]{20,}"),
        ("github", rb"gh[pousr]_[A-Za-z0-9]{30,}"), ("aws", rb"AKIA[0-9A-Z]{16}"),
        ("privkey", rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")]
files = [f.decode() for f in subprocess.run(["git", "ls-files", "-z"],
         capture_output=True).stdout.split(b"\0") if f]
hits = []
for rel in files:
    try:
        blob = open(rel, "rb").read()
    except OSError:
        continue
    for name, pat in PATS:
        for m in re.finditer(pat, blob):
            hits.append((rel, blob[:m.start()].count(b"\n") + 1, name))
print(f"  files to be pushed: {len(files)}")
if hits:
    print(f"  CREDENTIAL-SHAPED MATCHES: {len(hits)} (values not printed)")
    for h in hits[:20]:
        print(f"    {h[0]}:{h[1]}  {h[2]}")
else:
    print("  no credential-shaped string in anything to be pushed")
PY

echo
echo "=== 8. push, straight to github.com (no proxy), token for this command only ==="
if [ ! -f "$TOKEN_FILE" ]; then
  echo "  no token file at $TOKEN_FILE — stopping before the push"
  exit 1
fi
chmod 600 "$TOKEN_FILE"
TOKEN=$(cat "$TOKEN_FILE")
AUTH=$(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)
unset TOKEN
cd "$WORK/repo"
set +e
GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
  git -c user.name="$NAME" -c user.email="$EMAIL" \
      -c "http.extraHeader=Authorization: Basic $AUTH" \
      push "$TARGET" "HEAD:refs/heads/main" 2>&1 | tail -8
rc=${PIPESTATUS[0]}
unset AUTH
set -e
echo "  push rc=$rc"

if [ "$rc" != "0" ]; then
  echo "  push FAILED — the token file is kept so the push can be retried"
  echo "  (it is 0600, outside the repository, and must be removed once this lands)"
  exit 1
fi

echo
echo "=== 9. push the two tags ==="
# still inside the window where the token file exists; removing it before this would leave the
# tags unpushed with no credential to push them with
GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
  git -c "http.extraHeader=Authorization: Basic $AUTH" \
      push "$TARGET" "refs/tags/v0.2" "refs/tags/continuous-decision-v0.2" 2>&1 | tail -4
unset AUTH
rm -f "$TOKEN_FILE"
echo "  token file removed: $([ -f "$TOKEN_FILE" ] && echo NO || echo yes)"

echo
echo "=== 10. verify what landed ==="
GIT_CONFIG_GLOBAL=/dev/null git ls-remote --heads --tags "$TARGET" 2>&1 | head -6
echo "  local repo untouched? origin = $(cd "$REPO" && git remote get-url origin)"
echo "  local HEAD = $(cd "$REPO" && git rev-parse --short HEAD)  ($(cd "$REPO" && git status --porcelain | wc -l) uncommitted changes)"
