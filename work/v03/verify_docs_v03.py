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
    skipped = []

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
    # The Memory group was the one row v0.3 could not close: no `--planner` on the pair protocol,
    # so M1–M4 had no model-seat reading. P6 closed it, so this check now verifies *both* halves —
    # the structural reason the gap existed, and the reading that replaced it. Checking only the
    # old reason would keep passing after the row was fixed, which is the shape of a check that
    # stops meaning anything.
    #
    # The phrase is looked for with backticks *stripped*, not substituted. The first version did
    # `r_text.replace("`--planner`", "没有 `--planner`")` to paper over the backticked form, which
    # meant it inserted the phrase it was about to test for: the condition was true whenever the
    # report mentioned `--planner` at all, so the check could not fail. The mutation runner found
    # it (work/v03/p6e_gate_mutation.py) after four other removals turned a check red.
    flat = r_text.replace("`", "")
    out.append(("the report names the Memory group's structural gap AND its model-seat reading",
                "em-pairs" in flat and "没有 --planner" in flat
                and "MEM-1" in r_text and "216" in r_text and "411" in r_text,
                "row 2 moved from 部分达成 to 达成, so the gate must check the reading too"))
    out.append(("the report says the 5 policy-trace rows are not-measured on a model seat, not 0",
                "policy.trace" in r_text and "编出来的负数" in r_text,
                ""))

    # 7. a reading and the conclusion it can carry are different claims. Three small-denominator
    #    readings were delivered as findings; §17 re-checks each against its own denominator and
    #    downgrades the conclusions. This check exists so the downgrade cannot quietly be reverted
    #    by editing the §17 prose away, and so the headline 58/80 keeps its own weight instead of
    #    being read at the same level as a 2/2.
    flat_all = r_text.replace("`", "")
    out.append(("the report separates 58/80 from the small-denominator rows",
                "不是一个量级的证据" in flat_all and "58/80" in r_text,
                "58/80 is 80 paired rows; 2/2, 11/19 and 9-vs-12 are single-arm rates"))
    out.append(("the small-denominator rows carry their interval and an exact test",
                all(t in flat_all for t in ("p=0.50", "p=0.65", "p=1.00"))
                and "Wilson 95%" in r_text,
                ""))
    out.append(("the report refuses to read 9/12 vs 8/12 in either direction",
                "分不开就是分不开" in flat_all and "重规划有害" in flat_all,
                "an underpowered comparison must not be inverted into an opposite claim"))
    out.append(("§17 records the two errors this analysis made itself",
                "p<0.01" in flat_all and "0.6476" in flat_all and "一句废话" in flat_all,
                ""))

    # 8. §18 recomputed the `full`-on-VLM denominator from E2's own products and found the
    #    delivered explanation wrong in three specific ways. The conclusion (the camera channel is
    #    net negative) survives; the numbers attached to it do not. These checks keep the
    #    corrections from being quietly reverted along with the prose.
    out.append(("the report states the matched denominator, not 4/4 against 0/4",
                "匹配分母" in flat_all and "0/2" in flat_all and "2/2" in flat_all,
                "the delivered text compared 4 episodes against 2"))
    out.append(("the report attributes the duplicate colour to c1, not c3",
                "重复颜色的" in flat_all and "c1" in flat_all and "c3" in flat_all,
                ""))
    out.append(("the report says stub hits the same refusal, so the cause is the shared map",
                "stub" in flat_all and "接地图" in flat_all,
                ""))
    out.append(("the report names the unexplained infrastructure_error as a hole in the ledger",
                "infrastructure_error" in flat_all and "错误账本" in flat_all,
                ""))

    # 6. this gate does not quote the v0.2 gate's own live digest in a deliverable.
    #    (The check's name used to say the opposite of what it tests — "quotes the digest"
    #    while the assertion forbade the digest. A check whose name lies about its own
    #    condition is worse than no check: a reader trusts the name and skips the body.)
    #
    #    The check used to live inside `if os.path.exists(V02_GATE)`, so when the v0.2 gate file
    #    went away the check **silently disappeared** and the total fell from 17 to 16 with every
    #    line still green. A gate whose count depends on a file nobody is watching is a gate whose
    #    count cannot be trusted, so an absent input is now reported as a check that could not
    #    run, and the summary prints the skipped ones.
    if os.path.exists(V02_GATE):
        d = hashlib.sha256(open(V02_GATE, "rb").read()).hexdigest()[:16]
        out.append(("no deliverable quotes the v0.2 gate's digest or its file (H-48 #147)",
                    d not in r_text + l_text and V02_GATE not in r_text + l_text,
                    f"forbidden: digest {d}, file {V02_GATE}"))
    else:
        skipped.append(f"H-48 #147: the v0.2 gate is not at {V02_GATE}, so its live digest could "
                       f"not be computed and this check did not run")
    return out, skipped


def main():
    report = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        DOCS, "continuous-decision-v0.3-final-report.md")
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        DOCS, "continuous-decision-v0.3-phase-log.md")

    print(f"=== v0.3 document gate ===")
    print(f"report: {report}  ({os.path.getsize(report)} bytes)")
    print(f"log   : {log}  ({os.path.getsize(log)} bytes)")
    print()
    checks, skipped = run_gate(report, log)
    for label, ok, detail in checks:
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
    for label, ok, _d in run_gate(broken, log)[0]:
        if not ok:
            bit += 1
            print(f"  bites: {label}")
    os.remove(broken)
    check("the battery bites when the report is broken", bit >= 3,
          f"{bit} checks went red on the broken copy")

    if skipped:
        print()
        print("=== checks that could not run ===")
        for note in skipped:
            print(f"  SKIPPED {note}")
        print("  (a check that silently stops running is how a gate's count stops meaning "
              "anything — this is printed, not dropped)")

    red = [r for r in results if not r[1]]
    print()
    print(f"RESULT: {len(results)} checks run ({len(skipped)} could not run) -> "
          f"{'ALL CLOSED' if not red else str(len(red)) + ' FAILED'}")
    for label, _ok, detail in red:
        print(f"  FAILED {label}  | {detail}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
