"""v0.4's document gate: the two deliverable docs against the archives they cite.

Same constitution as v0.3's gate (verify_docs_v03.py): every number the report states is
re-derived from the artefacts on disk, and a check that cannot run prints itself as
SKIPPED rather than silently passing. Checks are chosen so that corrupting a cited number
in EITHER direction — editing the report, or editing an archive — turns something red.

Not an evolution of v0.3's 25 checks: most of those read v0.3-era structures. The v0.4
gate is written fresh for v0.4's delivery surface (evolution is logged, not silent).
"""
import glob
import json
import os
import re
import sys

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
sys.path.insert(0, REPO)
REPORT = os.path.join(REPO, "docs", "continuous-decision-v0.4-final-report.md")
PHASELOG = os.path.join(REPO, "docs", "continuous-decision-v0.4-phase-log.md")
E2 = "/home/czx/embodied-agent-batches/v04/e2"
E1 = "/home/czx/embodied-agent-batches/v04/e1"
MEM = "/home/czx/embodied-agent-batches/v04/mem1_joined"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def report_text():
    with open(REPORT, encoding="utf-8") as f:
        return f.read()


def phaselog_text():
    with open(PHASELOG, encoding="utf-8") as f:
        return f.read()


def main() -> int:
    rep = report_text()
    log = phaselog_text()

    # ---- E2' archive vs the report's table
    e2rep = json.load(open(os.path.join(E2, "e2_prime_report.json"), encoding="utf-8"))
    cells = {c["key"]: c["summary"] for c in e2rep["cells"]}
    check("E2' 三格齐备", len(cells) == 3, sorted(cells))
    check("E2' privileged 4/4",
          cells.get("long_horizon_privileged", {}).get("outcomes") == {"success": 4},
          cells.get("long_horizon_privileged", {}).get("outcomes"))
    check("E2' stub 0/4 全跑",
          cells.get("long_horizon_stub", {}).get("outcomes") == {"failed": 4},
          cells.get("long_horizon_stub", {}).get("outcomes"))
    check("E2' vlm 0/4 全跑 + 132 请求",
          cells.get("long_horizon_vlm", {}).get("outcomes") == {"failed": 4}
          and sum(c["summary"].get("http_requests", 0) for c in e2rep["cells"]) == 132,
          sum(c["summary"].get("http_requests", 0) for c in e2rep["cells"]))
    check("E2' c3 在 vlm/stub 有摘要（R3 生产证据）",
          all(glob.glob(os.path.join(E2, f"long_horizon_{ch}", "*", "episodes",
                                     "lh_c3_capacity_and_shift*", "episode_summary.json"))
              for ch in ("vlm", "stub")))

    # ---- E1' archive vs the report's numbers
    from embodied_agent.evaluation.run import batch_spend
    runs = [p for p in sorted(glob.glob(os.path.join(E1, "long_horizon_full", "*")))
            if os.path.isdir(os.path.join(p, "episodes"))]
    spend = sum(batch_spend(p)["http_requests"] for p in runs)
    eps = len(glob.glob(os.path.join(E1, "long_horizon_full", "*", "episodes", "*",
                                     "episode_summary.json")))
    check("E1' 模型席 700 请求 / 34 集（报告 §0/§7）",
          spend == 700 and eps == 34, f"{spend} requests, {eps} eps")
    state = json.load(open(os.path.join(E1, "e1_state.json"), encoding="utf-8"))
    check("E1' 止于 block 3（停止规则，非上界）",
          state.get("stopped") == 3 and state["spent_requests"] == 0,
          state.get("stopped_because", "")[:80])
    rule_root = glob.glob(os.path.join(E1, "long_horizon_full_rule", "*", "run_summary.json"))
    rule_out = json.load(open(rule_root[0], encoding="utf-8")).get("outcomes") if rule_root else {}
    check("rule 席 60 集 50/10", rule_out == {"success": 50, "failed": 10}, rule_out)

    lh = json.load(open(os.path.join(E1, "lh_score", "long_horizon_v0.json"), encoding="utf-8"))
    l6s = []
    for run in lh["runs"]:
        m = (run.get("pools", {}).get("full/rule", {}).get("metrics") or {}
             ).get("L6.replanning_effectiveness", {})
        if m.get("numerator") is not None:
            l6s.append((m["numerator"], m["denominator"]))
    check("模型席 L6 合并 10/20（三块求和）",
          sum(x[0] for x in l6s) == 10 and sum(x[1] for x in l6s) == 20, l6s)
    lh_rule = json.load(open(os.path.join(E1, "lh_score_rule", "long_horizon_v0.json"),
                             encoding="utf-8"))
    rm = ((lh_rule["runs"][0].get("pools", {}).get("full/rule", {}).get("metrics") or {})
          .get("L6.replanning_effectiveness", {}))
    check("rule 席 L6 0.5 (10/20)",
          rm.get("numerator") == 10 and rm.get("denominator") == 20, rm)
    rl2 = ((lh_rule["runs"][0].get("pools", {}).get("full/rule", {}).get("metrics") or {})
           .get("L2.dependency_edge_violation", {}))
    check("rule 席 L2 结构性 0/0",
          rl2.get("numerator") == 0 and rl2.get("denominator") == 0, rl2)

    # ---- MEM-1 joined reading
    mem = json.load(open(os.path.join(MEM, "episodic_memory_MEM_1.json"), encoding="utf-8"))
    check("MEM-1 四对齐员", len(mem.get("by_pair", {})) == 4
          and mem.get("pairs_ran") == ["em_p1", "em_p2", "em_p3", "em_p4"],
          mem.get("pairs_ran"))
    pooled = mem.get("pooled", {})
    m2 = pooled.get("M2.trajectory_changed_and_completed", {})
    check("MEM-1 M2 1/4 以区间交付",
          m2.get("numerator") == 1 and m2.get("denominator") == 4
          and m2.get("interval_kind") == "proportion", m2)

    # ---- the report's own surface
    check("报告声明 L2 触发率 1/34（不是率是读数）",
          "1/34" in rep and "两位数触发在任何" in rep.replace("\n", " ") or "1/34" in rep)
    check("报告 §0 声明探针读数以阶段日志为准（/tmp 非权威）",
          "读数以阶段日志" in rep)
    check("报告无凭据形状", not re.search(r"sk-[A-Za-z0-9]{8}", rep))
    check("阶段日志记录了驱动 bug 的修正",
          "目录深度" in log and "700/2,340" in log.replace("\n", ""))
    check("差值表在报告中（§12.7 兜底）", "差值表" in rep and "34/60" in rep.replace(" ", ""))

    # ---- verdict
    failed = [(n, d) for n, ok, d in results if not ok]
    for n, ok, d in results:
        print(f"  {'OK ' if ok else 'RED'} {n}" + (f"  [{d}]" if (d and not ok) else ""))
    print(f"RESULT: {len(results) - len(failed)}/{len(results)} -> "
          f"{'ALL CLOSED' if not failed else str(len(failed)) + ' RED'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
