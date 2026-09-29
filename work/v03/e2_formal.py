"""P3' E2's formal batch: the first `full`-on-VLM row on the main CLI.

Scale, derived from the probe batch's own ledger rather than chosen for neatness (D2: "the
probe decides the scale"):

  the probe ran 2 episodes and spent 23 requests — 22 answered, 1 refused by a 429 — for
  20 `perception` records, prompt 29,736 and completion 8,254. That is ~1.1 requests per look
  and ~11 looks per episode, so one `vlm` episode costs about 12 requests and 18k prompt
  tokens. The SPEC's suggested ceiling (2 task sets x 3 channels x 4 episodes = 24) therefore
  costs about 110 requests in its `vlm` half and nothing at all in the other two, which is
  0.03 of E1's bound — the scale is not what constrains this batch, the seat's burst 429 is.

Three channels, and the arm differs between them on purpose:

  vlm         arm `full`      a vision model was consulted, so the system is the full one and
                                this is §8 B5's missing `full`-on-VLM row;
  stub        arm `wo_vlm`    §9's limited semantic baseline: colour segmentation on the same
                                PNG, no request, and the module that owns the `perception`
                                record is off — so it is not allowed to claim `full`;
  privileged  arm `full`      the control shape: the same cases with the loop reading
                                simulator state, no camera, no request.

The arm changing with the channel is not a confound, it is `arm_coherence` working: each
episode's own `ablation` record names the condition, so no later reader can pool the three into
one "vlm vs not" claim. The thing the contrast buys is that `full`-on-VLM and `wo_vlm`-on-stub
run the SAME cases, the SAME seeds and the SAME task list, and differ only in what read the
frame.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
PY = "/home/czx/miniforge3/envs/embodied/bin/python"
ARCHIVE = "/home/czx/embodied-agent-batches/v03/e2"
SEAT = "configs/models/agnes-vision-r4.yaml"

CHANNELS = [("vlm", "full"), ("stub", "wo_vlm"), ("privileged", "full")]
SETS = [("long_horizon", ["lh_c1_shared_pair_restore", "lh_c3_capacity_and_shift"])]


def run_cell(*, set_name, cases, channel, arm, repeats, out_root, store, lib):
    cmd = [PY, "-m", "embodied_agent.cli", "evaluate",
           "--set", set_name,
           "--planner", "rule",
           "--modes", "B",
           "--repeats", str(repeats),
           "--out-root", out_root,
           "--perceive", channel,
           "--ablation", arm,
           "--experience-store", store,
           "--skill-memory", lib,
           "--cases", ",".join(cases),
           "--model-config", SEAT,
           "--frozen",
           "--no-frames"]
    env = {**os.environ, "PYTHONPATH": "."}
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, env=env)
    tail = [ln for ln in (r.stdout + r.stderr).splitlines() if "pybullet" not in ln]
    return {"cmd": " ".join(cmd[2:]), "rc": r.returncode,
            "wall_s": round(time.time() - t0, 2), "said": tail[-4:]}


def read_cell(out_root):
    run_dirs = [p for p in sorted(glob.glob(os.path.join(out_root, "*")))
                if os.path.isdir(os.path.join(p, "episodes"))]
    if not run_dirs:
        return {"episodes": 0, "why": "no run directory", "out_root": out_root}
    rd = run_dirs[-1]
    eps = sorted(glob.glob(os.path.join(rd, "episodes", "*")))
    perception = armed = asked = 0
    prompt = completion = http = 0
    per_ep = []
    for ep in eps:
        types = {}
        ev = os.path.join(ep, "events.jsonl")
        if os.path.exists(ev):
            for line in open(ev, encoding="utf-8"):
                if not line.strip():
                    continue
                try:
                    types[json.loads(line).get("type")] = types.get(
                        json.loads(line).get("type"), 0) + 1
                except json.JSONDecodeError:
                    pass
        p_rows = 0
        ep_prompt = ep_completion = ep_http = 0
        ok = failed = 0
        mc = os.path.join(ep, "model_calls.jsonl")
        if os.path.exists(mc):
            for line in open(mc, encoding="utf-8"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                p_rows += 1
                usage = rec.get("usage") or {}
                ep_prompt += int(usage.get("prompt_tokens") or 0)
                ep_completion += int(usage.get("completion_tokens") or 0)
                ep_http += int(rec.get("http_requests_this_call") or 0)
                if rec.get("ok"):
                    ok += 1
                else:
                    failed += 1
        perception += int(types.get("perception", 0))
        armed += int(types.get("ablation", 0) > 0)
        asked += int(p_rows > 0)
        prompt += ep_prompt
        completion += ep_completion
        http += ep_http
        per_ep.append({"episode_id": os.path.basename(ep), "perception": types.get("perception", 0),
                       "ablation": types.get("ablation", 0), "ledger_rows": p_rows,
                       "ledger_ok": ok, "ledger_failed": failed,
                       "prompt_tokens": ep_prompt, "completion_tokens": ep_completion,
                       "http_requests": ep_http})
    outcomes = {}
    sp = os.path.join(rd, "run_summary.json")
    if os.path.exists(sp):
        outcomes = json.load(open(sp, encoding="utf-8")).get("outcomes", {})
    manifest = {}
    mp = os.path.join(rd, "manifest.json")
    if os.path.exists(mp):
        m = json.load(open(mp, encoding="utf-8"))
        manifest = {"perception": m.get("perception"), "offline": m.get("offline"),
                    "model": m.get("model"), "frozen": m.get("frozen"),
                    "code_commit": (m.get("code") or {}).get("commit"),
                    "code_dirty": (m.get("code") or {}).get("dirty")}
    return {"run_dir": rd, "episodes": len(eps), "perception_records": perception,
            "armed": armed, "asked_the_model": asked, "prompt_tokens": prompt,
            "completion_tokens": completion, "http_requests": http,
            "outcomes": outcomes, "manifest": manifest, "per_episode": per_ep}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root", default=ARCHIVE)
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args()

    os.makedirs(args.out_root, exist_ok=True)
    report = {"spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §6 E2 / §9 P3'",
              "phase": "P3'-E2-formal", "seat": SEAT,
              "scale_derived_from": "the probe batch's ledger (23 requests / 2 episodes)",
              "sets": {s: c for s, c in SETS}, "repeats": args.repeats,
              "channels": {c: a for c, a in CHANNELS}, "cells": []}

    total_req = 0
    for set_name, cases in SETS:
        for channel, arm in CHANNELS:
            key = f"{set_name}_{channel}"
            cell = os.path.join(args.out_root, key)
            os.makedirs(cell, exist_ok=True)
            store = os.path.join(cell, "store.jsonl")
            lib = os.path.join(cell, "lib.jsonl")
            open(store, "a").close()
            open(lib, "a").close()
            print(f"[E2] === {key} (arm {arm}) ===", flush=True)
            run = run_cell(set_name=set_name, cases=cases, channel=channel, arm=arm,
                           repeats=args.repeats, out_root=cell, store=store, lib=lib)
            summary = read_cell(cell)
            report["cells"].append({"key": key, "set": set_name, "channel": channel,
                                    "arm": arm, "cases": cases, "run": run,
                                    "summary": summary})
            total_req += summary.get("http_requests", 0)
            s = summary
            print(f"      rc={run['rc']} {run['wall_s']}s  eps={s.get('episodes')} "
                  f"perception={s.get('perception_records')} armed={s.get('armed')} "
                  f"asked={s.get('asked_the_model')} http={s.get('http_requests')} "
                  f"prompt={s.get('prompt_tokens')} completion={s.get('completion_tokens')}",
                  flush=True)
            print(f"      outcomes={json.dumps(s.get('outcomes'))} "
                  f"manifest.offline={s.get('manifest', {}).get('offline')}", flush=True)
            if run["rc"] != 0:
                print("      said:", json.dumps(run["said"], ensure_ascii=False)[:300], flush=True)

    path = os.path.join(args.out_root, "e2_formal_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print()
    print("=== E2 formal ===")
    for c in report["cells"]:
        s = c["summary"]
        print(f"  {c['key']:28s} eps={s.get('episodes')} "
              f"perception={s.get('perception_records')} armed={s.get('armed')} "
              f"asked={s.get('asked_the_model')} http={s.get('http_requests')} "
              f"outcomes={json.dumps(s.get('outcomes'))}")
    print("total http_requests:", total_req)
    print("report:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
