"""Print one sealed benchmark episode as the round-by-round text the model saw.

Why this file exists: the graded benchmark has no GUI — ALFWorld/TextWorld is text,
`no ai2thor, no torch, no CUDA` (its own environment_check.json) — and `cli.py run`
echoes one line per *episode*, not per round. The rounds are all in `events.jsonl`,
so this reads them back: the command the executor sent, the environment's own reply,
and the verdict. It opens the logs read-only and computes nothing, so it cannot
change a sealed result and is not a second measurement.

Run:
  PYTHONPATH=. python work/text_trace.py /tmp/bst_v01/full/episodes/slot123_*
  PYTHONPATH=. python work/text_trace.py --commands-only <episode_dir>
"""
from __future__ import annotations

import argparse
import glob
import json
import sys


def _summary(episode_dir: str) -> dict:
    """The episode's own header (task type, instruction). Absent for a slot still
    in flight, which is why the trace prints without it rather than failing."""
    stem = episode_dir.rstrip("/") if episode_dir.endswith("events.jsonl") else episode_dir
    try:
        with open(stem + "/episode_summary.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _rows(path: str, width: int, commands_only: bool) -> list[str]:
    """The printable form of one episode's event log, in event order."""
    out = []
    for event in (json.loads(l) for l in open(path, encoding="utf-8")):
        kind = event["type"]
        p = event.get("payload") or {}
        if kind == "episode_start":
            out.append("  planner=%s mode=%s" % (p.get("planner"), p.get("mode")))
        elif kind == "skill_call":
            call = p["call"]
            args = json.dumps(call["args"], ensure_ascii=False)
            out.append("  > %s %s" % (call["skill"], args))
        elif kind == "execution_feedback" and not commands_only:
            fb = p["feedback"]
            reply = fb.get("environment_text") or fb.get("failure_code") or ""
            out.append("    < [%s] %s" % (fb["status"], str(reply)[:width]))
        elif kind == "termination":
            out.append("  == %s env_done=%s official_won=%s steps=%s"
                       % (p.get("reason"), p.get("env_done"), p.get("official_won"),
                          p.get("env_steps")))
        elif kind == "episode_official":
            out.append("  == official_success=%s false_finish=%s"
                       % (p.get("official_success"), p.get("false_finish")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser("read a benchmark episode back as text")
    ap.add_argument("episode", help="an episode dir, or a glob of them")
    ap.add_argument("--commands-only", action="store_true",
                    help="skip the environment's replies")
    ap.add_argument("--width", type=int, default=120)
    a = ap.parse_args()

    for d in sorted(glob.glob(a.episode)) or [a.episode]:
        leaf = d.rstrip("/").split("/")[-1]
        path = d if d.endswith("events.jsonl") else d.rstrip("/") + "/events.jsonl"
        try:
            rows = _rows(path, a.width, a.commands_only)
        except OSError as e:
            print("%s: %s" % (d, e), file=sys.stderr)
            continue
        print("\n=== " + leaf)
        summary = _summary(d)
        if summary:
            print("  %s | %s" % (summary.get("task_type"), str(summary.get("instruction"))[:a.width]))
        print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
