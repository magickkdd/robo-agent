"""v0.4 E2' formal batch: the first `full`-on-VLM row on a bridge that can ground the case.

Everything about the shape is inherited from `work/v03/e2_formal.py` (same three channels,
same arm-per-channel coherence, same cases); three things are new, and each is v0.4's:

  * the bridge now carries the (colour, shape) key, so `lh_c3` — the case whose duplicate
    yellow (a cube and a cuboid) killed four non-privileged episodes in v0.3 — is expected
    to RUN on every channel. The prereg's matched denominator is 4 per cell;
  * every non-success row must land in the batch's errors ledger with a named cause
    (v0.4 R1) — the driver checks this live instead of trusting the run;
  * the spend bound from the prereg (400 requests) is enforced between cells, not
    discovered afterwards (probe E's lesson: a bound checked only at the end cannot stop
    anything).

Cell order puts the two zero-cost channels first: if the seat's burst 429 kills the `vlm`
cell, the free half of the batch is already on disk (checkpoint discipline, v0.3 SPEC §8.3).
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
PY = "/home/czx/miniforge3/envs/embodied/bin/python"
ARCHIVE = "/home/czx/embodied-agent-batches/v04/e2"
SEAT = "configs/models/agnes-vision-r4.yaml"
BOUND = 400  # configs/experiment/v04_e2_preregistration.json spend_bound.requests

CHANNELS = [("privileged", "full"), ("stub", "wo_vlm"), ("vlm", "full")]
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
    perception = grounded = ungrounded = 0
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
                    t = json.loads(line).get("type")
                    types[t] = types.get(t, 0) + 1
                except json.JSONDecodeError:
                    pass
        p_rows = ep_prompt = ep_completion = ep_http = 0
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
        perception += int(types.get("perception", 0))
        prompt += ep_prompt
        completion += ep_completion
        http += ep_http
        per_ep.append({"episode_id": os.path.basename(ep),
                       "perception": types.get("perception", 0),
                       "ledger_rows": p_rows, "http_requests": ep_http,
                       "prompt_tokens": ep_prompt, "completion_tokens": ep_completion})
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
    # R1 live: a non-success outcome with no ledger row is the defect this batch exists
    # to have made impossible — measure it here, not in the report
    ledger = []
    lp = os.path.join(rd, "errors.jsonl")
    if os.path.exists(lp):
        ledger = [json.loads(l) for l in open(lp, encoding="utf-8") if l.strip()]
    unexplained = [{"outcome": o, "where": (r.get("infrastructure_error") or {}).get("where")}
                   for o in outcomes for r in []]
    infra_outcomes = {k: v for k, v in outcomes.items()
                      if k in ("infrastructure_error", "goal_resolution_error")}
    ledger_outcomes = {}
    for r in ledger:
        ledger_outcomes[r.get("outcome")] = ledger_outcomes.get(r.get("outcome"), 0) + 1
    r1_ok = sum(infra_outcomes.values()) == sum(ledger_outcomes.get(k, 0)
                                                for k in infra_outcomes)
    return {"run_dir": rd, "episodes": len(eps), "perception_records": perception,
            "prompt_tokens": prompt, "completion_tokens": completion,
            "http_requests": http, "outcomes": outcomes, "manifest": manifest,
            "per_episode": per_ep,
            "r1_ledger": {"infra_outcomes": infra_outcomes,
                          "ledger_rows": ledger_outcomes, "covered": r1_ok}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root", default=ARCHIVE)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--bound", type=int, default=BOUND)
    args = ap.parse_args()

    os.makedirs(args.out_root, exist_ok=True)
    report = {"spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.4.md §7 E2'",
              "prereg": "configs/experiment/v04_e2_preregistration.json",
              "phase": "P4-E2'-formal", "seat": SEAT, "bound": args.bound,
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
            print(f"[E2'] === {key} (arm {arm}) ===", flush=True)
            run = run_cell(set_name=set_name, cases=cases, channel=channel, arm=arm,
                           repeats=args.repeats, out_root=cell, store=store, lib=lib)
            summary = read_cell(cell)
            report["cells"].append({"key": key, "set": set_name, "channel": channel,
                                    "arm": arm, "cases": cases, "run": run,
                                    "summary": summary})
            total_req += summary.get("http_requests", 0)
            s = summary
            print(f"      rc={run['rc']} {run['wall_s']}s  eps={s.get('episodes')} "
                  f"outcomes={s.get('outcomes')} perception={s.get('perception_records')} "
                  f"http={s.get('http_requests')} r1_covered={s.get('r1_ledger', {}).get('covered')}",
                  flush=True)
            with open(os.path.join(args.out_root, "e2_prime_report.json"), "w",
                      encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
            if total_req > args.bound:
                print(f"[E2'] bound {args.bound} hit at {total_req} requests; "
                      f"stopping before the next cell (prereg stop rule)", flush=True)
                break
        if total_req > args.bound:
            break
    print(f"[E2'] total requests {total_req} / bound {args.bound}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
