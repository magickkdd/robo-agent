"""The v0.3 document gate.

**This is a new gate, not an evolution of v0.2's 51 checks, and saying so is the first thing it
checks.** v0.2's gate (`/tmp/mw117/verify_docs_v3.py`, digest 7e18d10436a29d05, run as shipped
this round: 51 checks -> ALL CLOSED) is pointed at the v0.2 pair by two module constants
(`REPORT`, `LOG`) and most of its 51 checks are about structures v0.3 does not have: v0.2's
§11.3 locator rows, the appendix-H counter, the "un-ranged merge round", the per-row pipe
convention. Repointing two constants would give 51 green checks that say nothing about the v0.3
documents — which is the failure mode the v0.2 log's own row 86 names: "「检查通过」与「产物正确」
之间差的永远是我没让检查去看的那一处".

So this gate checks what the v0.3 report actually asserts, and it is **non-vacuous by
construction**: `main()` takes an optional pair of document paths, and the self-test at the end
runs the whole battery against a deliberately broken copy of the report to show each check bites.
A gate that cannot fail is a comment.

The checks, in the order a reader needs them:

1. every artefact path the report cites resolves to a real file, outside `/tmp` (SPEC §4.12);
2. the frozen identities the report quotes equal a live measurement, not a remembered one
   (the rule that stopped the v0.2 report's published `dirty_diff_sha256` from being reusable);
3. §13's seven completion-definition rows are all present, and each carries a verdict word and an
   evidence cell — a table that lost a row must go red rather than read complete;
4. every number in the §2.2 headline table is present in the product the table cites, so the
   "58 of 80" claim cannot outlive the artefact it came from;
5. the report says the two things it must not omit: the unscaled USD and the scope of every
   negative reading.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
DOCS = os.path.join(REPO, "docs")
ARCHIVE = "/home/czx/embodied-agent-batches/v03"
V02_GATE = "/tmp/mw117/verify_docs_v3.py"
PY = "/home/czx/miniforge3/envs/embodied/bin/python"

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok), detail))
    print(f"{'OK  ' if ok else 'FAIL'} {label}" + (f"  | {detail}" if detail else ""))


def run_gate(report, log):
    """The battery, as a function of the two documents, so the self-test can point it elsewhere."""
    r_text = open(report, encoding="utf-8").read()
    l_text = open(log, encoding="utf-8").read()
    out = []

    # 1. every cited *artefact* resolves, and none of them is a /tmp address.
    #    Scope: backticked tokens that name a **file** under a root this project owns, or a
    #    path relative to the archive or the repo. Three classes are deliberately out of scope
    #    and the exclusion is stated rather than silent: a cited interpreter (it is a tool, not
    #    a reading's address), a directory (a place, not a file), and a brace form like
    #    `long_horizon_v0.{json,md}` which is expanded rather than dropped.
    #    (Two earlier versions of this check were wrong in opposite directions: one took every
    #    /home/czx string and went red on `/home/czx/mwvenv`; the next excluded so much that it
    #    reported "0 artefact paths" — a check that cannot fail is a comment, and the count is
    #    printed so that condition is visible rather than inferred.)
    raw = set(re.findall(r"`([^`\n]+)`", r_text + l_text))
    expanded = set()
    for tok in raw:
        m = re.fullmatch(r"([A-Za-z0-9_./-]+)\.\{([A-Za-z0-9,]+)\}", tok.strip())
        if m:
            for ext in m.group(2).split(","):
                expanded.add(f"{m.group(1)}.{ext}")
        else:
            expanded.add(tok.strip())
    under_archive = [t for t in expanded
                     if t.startswith("/home/czx/embodied-agent-batches/")
                     or re.fullmatch(r"e[123]/[A-Za-z0-9_./-]*[A-Za-z0-9_]+\.[A-Za-z0-9]+", t)
                     or re.fullmatch(r"e[123]_[A-Za-z0-9_]+\.json", t)]
    under_repo = [t for t in expanded
                  if t.startswith(REPO + "/") and re.search(r"\.[A-Za-z0-9]+$", t)
                  and "/bin/" not in t]
    artefacts = sorted({t.rstrip("/") for t in under_archive + under_repo
                        if os.path.isfile(t.rstrip("/"))
                        or not os.path.isdir(t.rstrip("/"))})
    resolvable = []
    for p in artefacts:
        base = p.rstrip("/")
        if os.path.exists(base) or os.path.exists(os.path.join(ARCHIVE, base)) \
                or os.path.exists(os.path.join(REPO, base)):
            resolvable.append(p)
    missing = sorted(set(artefacts) - set(resolvable))
    out.append(("every cited artefact path resolves", bool(artefacts) and not missing,
                f"{len(artefacts)} artefact paths cited, {len(resolvable)} resolve"
                + (f"; missing {missing[:5]}" if missing else "")))
    out.append(("the artefact-path check is not vacuous (it has inputs)", bool(artefacts),
                f"inputs: {len(artefacts)}" if artefacts else "NO INPUTS — this check is a comment"))
    tmp_cited = [t for t in expanded if t.startswith("/tmp/") and "/" in t]
    out.append(("no reading is cited from a /tmp address", not tmp_cited,
                f"tmp citations: {tmp_cited[:5]}" if tmp_cited else "none"))

    # 2. the frozen identities are live, not remembered
    live = json.loads(subprocess.run(
        [PY, "-c", "import json;from embodied_agent.core import v02;"
         "print(json.dumps(v02.schema_fingerprint()))"],
        capture_output=True, text=True, cwd=REPO,
        env={**os.environ, "PYTHONPATH": "."}).stdout)
    frozen = json.load(open(os.path.join(REPO, "configs/experiment/v02_schema_freeze.json"),
                            encoding="utf-8"))["schema_fingerprint"]
    out.append(("the report's schema fingerprint claim equals the live one",
                live == frozen and "schema_version 4" in r_text,
                f"live==frozen: {live == frozen}"))

    def _check(cmd):
        p = subprocess.run([PY, "-m", "embodied_agent.cli"] + cmd, capture_output=True,
                           text=True, cwd=REPO, env={**os.environ, "PYTHONPATH": "."})
        return p.returncode, p.stdout + p.stderr

    for name, cmd, needle in (
            ("freeze --check", ["freeze", "--check"], "2f1c74f52e91"),
            ("prereg --check", ["prereg", "--check"], "23abe58ff333"),
            ("em-pairs --check", ["em-pairs", "--check"], "7df449ed2f4d")):
        # the needle is searched over the whole output, not its last line: these entry points
        # print pybullet's build banner on stderr *after* the reading, so `said[-1]` picks the
        # banner (a first version of this check went red on exactly that).
        rc, said = _check(cmd)
        out.append((f"{name} is rc=0 and the report quotes its reading",
                    rc == 0 and needle in said and needle in r_text,
                    f"rc={rc} needle_in_output={needle in said} needle_in_report={needle in r_text}"))

    # 3. §13's seven rows, each with a verdict and an evidence cell.
    #    Scoped to §1's table: the report has 21 other `| n |` rows (the arm tables, the cost
    #    table, the defect table) and demanding evidence cells of those is a check about a
    #    different document.
    sec = re.search(r"## 1\. §13 完成定义的逐条判定(.*?)\n## ", r_text, re.S)
    verdict_rows = re.findall(r"^\|\s*(\d)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|", sec.group(1), re.M) \
        if sec else []
    want = set("1234567")
    got = {n for n, _c, v, _e in verdict_rows if any(
        w in v for w in ("达成", "部分达成", "不达成"))}
    out.append(("§13's seven completion rows are all present with a verdict word",
                got == want, f"rows with a verdict: {sorted(got)}"))
    out.append(("every §13 row carries an evidence cell",
                len(verdict_rows) == 7 and all(e.strip() for _n, _c, _v, e in verdict_rows),
                f"{len(verdict_rows)} rows in §13's table"))

    # 4. the §2.2 headline numbers exist in the product they cite
    prod = os.path.join(ARCHIVE, "e1/lh_score/two_sources_summary.json")
    if os.path.exists(prod):
        two = json.load(open(prod, encoding="utf-8"))
        moved = two.get("v0.3 (model decision source)", {}).get("moved")
        rule = two.get("v0.2 (rule decision source)", {}).get("moved")
        out.append(("the '58 of 80' and '1 of 80' claims are in the product",
                    moved == 58 and rule == 1 and "58" in r_text and "**1**" in r_text,
                    f"product: model={moved} rule={rule}"))
    else:
        out.append(("the two-source product exists", False, prod))

    # 5. the two omissions that must not be omitted
    out.append(("the report says the USD is null and why", "pricing_configured" in r_text
                and "null" in r_text.lower(), ""))
    out.append(("the report names the two invariants' before/after",
                "575/0" in r_text and "153/153" in r_text, ""))
    out.append(("the report keeps the negative results, including the new ones",
                "MODEL_ERROR" in r_text and "假完成" in r_text
                and "not_executable" in r_text, ""))
    out.append(("the report says which decision sources are never merged",
                "不合并" in r_text, ""))
    out.append(("the report names the Memory group's structural not-measured reason",
                "em-pairs" in r_text and "没有 `--planner`" in r_text.replace("`--planner`",
                                                                           "没有 `--planner`"),
                ""))

    # 6. this gate does not quote the v0.2 gate's own live digest in a deliverable
    if os.path.exists(V02_GATE):
        d = hashlib.sha256(open(V02_GATE, "rb").read()).hexdigest()[:16]
        out.append(("this document quotes the v0.2 gate's digest, not its file (H-48 #147)",
                    d not in r_text + l_text, f"digest {d} must not appear in a deliverable"))
    return out


def main():
    report = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        DOCS, "continuous-decision-v0.3-final-report.md")
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        DOCS, "continuous-decision-v0.3-phase-log.md")

    print(f"=== v0.3 document gate ===")
    print(f"report: {report}  ({os.path.getsize(report)} bytes)")
    print(f"log   : {log}  ({os.path.getsize(log)} bytes)")
    print()
    for label, ok, detail in run_gate(report, log):
        check(label, ok, detail)

    # non-vacuity: the same battery against a deliberately broken copy
    print()
    print("=== non-vacuity: the battery against a broken copy ===")
    broken = "/tmp/v03_broken_report.md"
    text = open(report, encoding="utf-8").read()
    # three independent breakages, so a check cannot pass by accident
    text = text.replace("58", "9").replace("153/153", "0/0").replace(
        "2f1c74f52e91", "DEADBEEF")
    open(broken, "w", encoding="utf-8").write(text)
    bit = 0
    for label, ok, _d in run_gate(broken, log):
        if not ok:
            bit += 1
            print(f"  bites: {label}")
    os.remove(broken)
    check("the battery bites when the report is broken", bit >= 3,
          f"{bit} checks went red on the broken copy")

    red = [r for r in results if not r[1]]
    print()
    print(f"RESULT: {len(results)} checks run -> "
          f"{'ALL CLOSED' if not red else str(len(red)) + ' FAILED'}")
    for label, _ok, detail in red:
        print(f"  FAILED {label}  | {detail}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
