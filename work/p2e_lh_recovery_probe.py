"""The feedback stream next to the verdict timeline, so the missing recovery population can be
named from the record rather than guessed.

`full/payload` filed two `recovery_action` records while the verdict timeline yielded zero
true->false transitions, which left L6 at 0/0 on an episode that visibly did recovery work.
Either the records are noise or the denominator is wrong; this dump decides which.
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/embodied-agent-robot-agent-embodied-agent-4"))
from embodied_agent.evaluation.long_horizon import read_episode, _episode_dirs, _completed  # noqa: E402

LETTERS = {"true": "T", "false": "F", "unknown": "?"}

for run_dir in sys.argv[1:]:
    for d in _episode_dirs(run_dir):
        ep = read_episode(d)
        samples = ep["samples"]
        print(f"\n=== {os.path.basename(run_dir)} / {os.path.basename(d)}")
        for pid in sorted({p for s in samples for p in s["verdicts"]}):
            print(f"  {pid:46s} " + "".join(LETTERS[s["verdicts"][pid]]
                                            for s in samples if pid in s["verdicts"]))
        print("  seq  round skill   entity/target                 completed code       "
              "cand_res            held_after   verdict_now")
        for fb in ep["feedbacks"]:
            seq = fb["sequence"]
            skill = str(fb.get("skill") or "")
            ent = str(fb.get("entity_id") or "")
            tgt = str(fb.get("target_id") or "")
            pid = f"placed:{ent}:{tgt}" if skill == "place" else f"held:{ent}"
            now = "n/a"
            for s in reversed([x for x in samples if x["sequence"] <= seq]):
                if pid in s["verdicts"]:
                    now = LETTERS[s["verdicts"][pid]]
                    break
            print(f"  {seq:4d} {str(fb.get('round_index') or ''):>5} {skill:7s} "
                  f"{(ent + '/' + tgt)[:28]:28s} "
                  f"{str(_completed(fb)):9s} {str(fb.get('failure_code'))[:11]:11s} "
                  f"{str(fb.get('candidate_resolution'))[:17]:17s} "
                  f"{str(fb.get('held_object_after'))[:12]:12s} {now:4s} "
                  f"exec={fb.get('executed')} rej={str(fb.get('rejection_reasons'))[:24]}")
        for rec in ep["recoveries"]:
            print(f"  recovery seq={rec['sequence']} round={rec.get('round_index')} "
                  f"kind={rec.get('recovery_kind')} row={rec.get('row_key')!r} "
                  f"skill={rec.get('skill')} args={rec.get('args')}")
        for inj in ep["injections"]:
            print(f"  injection seq={inj['sequence']} action={inj.get('action')} "
                  f"entity={inj.get('entity_id')} disp={inj.get('displacement_m')} "
                  f"offset={inj.get('offset')}")
