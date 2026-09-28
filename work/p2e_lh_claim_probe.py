"""What does a plan row actually publish, and when?

`L1.row_claim_vs_key` came out 4/4 on the `full` arm, which is either a real self-report defect
or my comparison being wrong. The two are told apart by reading the log in publication order:
each plan view's raw `satisfied` next to the verdict of the *same* point in the round.
"""
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/embodied-agent-robot-agent-embodied-agent-4"))
from embodied_agent.evaluation.long_horizon import (read_episode, _verdict, _episode_dirs,  # noqa: E402
                                                    _satisfied, _series, _last)

LETTERS = {"true": "T", "false": "F", "unknown": "?"}

if True:
    for run_dir in sys.argv[1:]:
        for d in _episode_dirs(run_dir):
            episode = os.path.basename(d)
            ep = read_episode(d)
            samples = ep["samples"]
            print(f"\n=== {episode}")
            series = _series(samples)
            for pid in sorted({p for s in samples for p in s["verdicts"]}):
                line = "".join(LETTERS[s["verdicts"][pid]] for s in samples if pid in s["verdicts"])
                print(f"  {pid:46s} {line}")
            print("  --- each row's LAST published claim vs the final verdict ---")
            print("  --- same-round pairing: view claim vs the NEXT context page ---")
            pairs = 0
            for event in ep["plan_events"]:
                nxt = [s for s in samples if s["sequence"] >= event["sequence"]
                       and s["kind"] == "round"]
                if not nxt:
                    print(f"    seq={event['sequence']} v{event.get('version')}: no page after it")
                    continue
                page = nxt[0]
                print(f"    seq={event['sequence']} v{event.get('version')} -> ctx round "
                      f"{page['round_index']} sv={page['state_version']}")
                for row in event["rows"]:
                    pid = str(row.get("predicate_id") or "")
                    if pid not in page["verdicts"]:
                        continue
                    claim = _satisfied(row)
                    if claim == "unknown":
                        continue
                    pairs += 1
                    mark = "=" if claim == page["verdicts"][pid] else "X"
                    print(f"      {mark} {str(row.get('row_key'))[:34]:34s} claim={claim:7s} "
                          f"page={page['verdicts'][pid]}")
            print(f"    definite claims in the same round: {pairs}")
            last_seen: dict[str, dict] = {}
            for event in ep["plan_events"]:
                for row in event["rows"]:
                    key = str(row.get("row_key") or "")
                    if key:
                        last_seen[key] = {"row": row, "sequence": event["sequence"],
                                          "version": event.get("version"),
                                          "in_final_view": False}
            final_keys = {str(r.get("row_key")) for r in ep["plan_events"][-1]["rows"]} \
                if ep["plan_events"] else set()
            for key, rec in sorted(last_seen.items()):
                row = rec["row"]
                pid = str(row.get("predicate_id") or "")
                in_final = key in final_keys
                prior = [s for s in samples if s["sequence"] <= rec["sequence"]]
                contemp = "no-sample"
                for s in reversed(prior):
                    if pid in s["verdicts"]:
                        contemp = LETTERS[s["verdicts"][pid]]
                        break
                    contemp = "not-listed"
                print(f"  {key:34s} last@seq={rec['sequence']:4d} v{rec['version']} "
                      f"satisfied={row.get('satisfied')!r:10s}->{_satisfied(row):7s} "
                      f"raw_type={type(row.get('satisfied')).__name__:4s} "
                      f"contemp={contemp} final={LETTERS[_last(series.get(pid) or [])]} "
                      f"in_final_view={in_final}")
            print("  --- recovery records vs the timeline ---")
            for rec in ep["recoveries"]:
                print(f"  seq={rec['sequence']:4d} round={rec.get('round_index')} "
                      f"kind={rec.get('recovery_kind')} choice={rec.get('choice')!r} "
                      f"row={rec.get('row_key')} skill={rec.get('skill')} "
                      f"plan_version={rec.get('plan_version')}")
