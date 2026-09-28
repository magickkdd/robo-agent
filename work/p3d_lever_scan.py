"""Which decision levers does a zero-spend episode actually present? Read-only.

The order sweep that came before this answered "does a remembered order ever differ from the plan
order it is read against" (it does not, in 38 episodes) but not the prior question: **is there an
order to differ over?** A recall can only change a trajectory at a round where the plan offers more
than one admissible next action. So this walks the events a previous run wrote and reports, per
round, how many rows were ready, whether the row taken was the first one `plan_view` publishes,
and what predicate shapes occur at all.

Nothing here re-simulates: the ready set is read out of each round's `plan_summary` text and mapped
back to predicate ids through the `plan` event's own rows, and the row taken is the decision's own
`execute.subgoal` mapped the same way. Zero spend, no pybullet, no writes.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import sys

ROOT = sys.argv[1]

READY = "ready (a set, not an order): "


def _statements(summary: str, marker: str) -> list[str]:
    for line in summary.split("\n"):
        if marker in line:
            return [s.strip() for s in line.split(marker, 1)[1].split(";") if s.strip()]
    return []


def scan(episode_dir: str) -> dict:
    events = [json.loads(l) for l in
              open(os.path.join(episode_dir, "events.jsonl"), encoding="utf-8") if l.strip()]
    by_type: dict[str, list[dict]] = collections.defaultdict(list)
    for e in events:
        by_type[e["type"]].append(e)

    key_of_sub: dict[str, str] = {}
    statement_to_key: dict[str, str] = {}
    kinds: collections.Counter = collections.Counter()
    for plan in by_type["plan"]:
        for row in (plan["payload"].get("view") or {}).get("rows") or []:
            statement_to_key[str(row.get("statement"))] = str(row.get("predicate_id"))
            key_of_sub[str(row.get("subgoal_id"))] = str(row.get("predicate_id"))
            kinds[str(row.get("predicate_id")).split(":")[0]] += 1

    wm = {int(w["payload"]["round_index"]): w["payload"] for w in by_type["working_memory"]}
    round_of_ctx = {str(d["payload"]["context_id"]): int(d["payload"]["round_index"])
                    for d in by_type["decision_context"]}
    choose_rounds = 0          # rounds where more than one row was admissible
    off_first = []             # ...and the row taken was not the first published
    for dec in by_type["decision"]:
        p = dec["payload"]
        if p["action"] != "execute":
            continue
        n = round_of_ctx.get(str(p.get("context_id") or ""))
        if n is None:
            off_first.append(("no-context", str(p.get("decision_id")), "?"))
            continue
        view = wm.get(n, {})
        ready = sorted(statement_to_key.get(s, "?")
                       for s in _statements(str(view.get("plan_summary") or ""), READY))
        taken = key_of_sub.get(str((p.get("execute") or {}).get("subgoal") or ""), "?")
        if len(ready) > 1:
            choose_rounds += 1
            if taken != ready[0]:
                off_first.append((n, taken, ready[0]))
    return {"episode": os.path.relpath(episode_dir, ROOT),
            "predicate_kinds": dict(kinds),
            "rounds": len(by_type["decision"]),
            "choose_rounds": choose_rounds,
            "max_ready": max([len(_statements(str(v.get("plan_summary") or ""), READY))
                              for v in wm.values()] or [0]),
            "off_first": off_first,
            "outcome": next((str(e["payload"].get("outcome") or e["payload"].get("status"))
                             for e in by_type["episode_end"]), "?"),
            "retrievals": len(by_type["memory_retrieval"]),
            "writes": len(by_type["memory_write"])}


def main() -> None:
    rows = []
    for case in sorted(os.listdir(ROOT)):
        for ep in sorted(glob.glob(os.path.join(ROOT, case, "episodes", "*"))):
            rows.append(scan(ep))
    head = (f"{'episode':50s} {'rnd':>3s} {'pick2+':>6s} {'maxR':>4s} "
            f"{'notFirst':>8s} {'out':>9s}  kinds")
    print(head)
    print("-" * len(head))
    tot: collections.Counter = collections.Counter()
    for r in rows:
        print(f"{r['episode']:50s} {r['rounds']:3d} {r['choose_rounds']:6d} {r['max_ready']:4d} "
              f"{len(r['off_first']):8d} {r['outcome']:>9s}  {r['predicate_kinds']}")
        tot.update({"episodes": 1, "rounds": r["rounds"], "choose2+": r["choose_rounds"]})
        for k, v in r["predicate_kinds"].items():
            tot[f"kind:{k}"] += v
    print("\ncensus:", dict(sorted(tot.items())))
    print("episodes that ever took a non-first ready row:",
          [(r["episode"], r["off_first"]) for r in rows if r["off_first"]])
    print("episodes with any non-`placed` row:",
          [r["episode"] for r in rows if set(r["predicate_kinds"]) - {"placed"}])


if __name__ == "__main__":
    main()
