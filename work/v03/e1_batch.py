"""P2' E1: the 12 cells with a model decision source. The main deliverable.

Everything the pre-registration fixed in advance, enforced here rather than left to discipline:

* the arm list is P5-c's, read off its cell directories — not the SPEC's literal list, which
  the CLI drops on `privileged` (see `configs/experiment/v03_e1_preregistration.json`);
* the seat is `configs/models/bst_text_agnes.yaml` and the decision source is
  `--planner deepseek` (the main cli's enum; `model` is the MuJoCo spelling);
* the call upper bound is counted **as the batch runs**, from the same per-episode
  `model_calls.jsonl` a Cost group would read, and the run stops when it is hit;
* 429 / quota deaths are bucketed separately and are environment errors, never arm effects;
* each cell writes its own summary the moment it finishes, so a kill loses at most the cell in
  flight and a restart resumes only unfinished cells (SPEC §8.3);
* the stop rule is SPEC §8.2: three consecutive 429/quota deaths stop the batch.

`--frozen` is passed and `--prereg` deliberately is not: the P3 pre-registration freezes a
*different* matrix (set `formal`, modes A/B/C, repeats 3, planner deepseek, 24 smoke cases),
and `matrix_mismatch` would refuse E1 for the right reason. E1's own matrix is frozen in its
own pre-registration, committed at b918f55, and its task list is checked against
`frozen_tasks_v1.json` on every cell.
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
ARCHIVE = "/home/czx/embodied-agent-batches/v03/e1"
SEAT = "configs/models/bst_text_agnes.yaml"

ARMS = ["full", "wo_planning", "wo_working_memory", "wo_replanning",
        "wo_episodic_memory", "wo_skill_acquisition"]
SETS = {"long_horizon": {"cases": 6, "episodes": 12},
        "em": {"cases": 8, "episodes": 16}}
UPPER_BOUND = 2484          # v03_e1_preregistration.json: 1,656 x 1.5
CONSECUTIVE_QUOTA_STOPS = 3  # SPEC §8.2


def cells():
    for set_name, shape in SETS.items():
        for arm in ARMS:
            yield set_name, arm, shape


def read_cell(cell_dir):
    """One cell's numbers, from the artefacts a Cost group would read.

    Two field names had to be read rather than guessed, and getting them wrong is what made a
    first version of this reader report 12,320 requests for a cell that spent 225:

    * `http_requests_total` is the adapter's **cumulative** counter, carried on every row.
      Summed across rows it counts each request once per subsequent row. The per-call figure
      is `http_requests_this_call`; the running total is kept separately as
      `cumulative_http_requests_last`.
    * tokens are nested under `usage` (`usage.prompt_tokens` / `usage.completion_tokens`), not
      at the top level — the same shape the perception ledger rows use, and the reason a first
      read found 0 tokens in a cell that certainly spent some.
    """
    ep_root = os.path.join(cell_dir, "episodes")
    episodes = sorted(glob.glob(os.path.join(ep_root, "*"))) if os.path.isdir(ep_root) else []
    armed = asked = 0
    prompt = completion = http = 0
    cumulative_last = 0
    latencies = []
    per_episode = []
    for ep in episodes:
        ev = os.path.join(ep, "events.jsonl")
        has_arm = False
        if os.path.exists(ev):
            for line in open(ev, encoding="utf-8"):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "ablation":
                    has_arm = True
        mc = os.path.join(ep, "model_calls.jsonl")
        ep_prompt = ep_completion = ep_http = 0
        ep_rows = 0
        ep_cum = 0
        if os.path.exists(mc):
            for line in open(mc, encoding="utf-8"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                ep_rows += 1
                usage = rec.get("usage") or {}
                ep_prompt += int(usage.get("prompt_tokens") or 0)
                ep_completion += int(usage.get("completion_tokens") or 0)
                ep_http += int(rec.get("http_requests_this_call") or 0)
                ep_cum = max(ep_cum, int(rec.get("http_requests_total") or 0))
                if rec.get("latency_s") is not None:
                    latencies.append(float(rec["latency_s"]))
        armed += int(has_arm)
        asked += int(ep_rows > 0)
        prompt += ep_prompt
        completion += ep_completion
        http += ep_http
        cumulative_last = max(cumulative_last, ep_cum)
        per_episode.append({"episode_id": os.path.basename(ep), "armed": has_arm,
                            "model_calls_rows": ep_rows, "prompt_tokens": ep_prompt,
                            "completion_tokens": ep_completion, "http_requests": ep_http})
    errs = os.path.join(cell_dir, "errors.jsonl")
    errors = []
    if os.path.exists(errs):
        for line in open(errs, encoding="utf-8"):
            if line.strip():
                errors.append(json.loads(line))
    outcomes = {}
    summary_path = os.path.join(cell_dir, "run_summary.json")
    if os.path.exists(summary_path):
        outcomes = json.load(open(summary_path, encoding="utf-8")).get("outcomes", {})
    return {"episodes": len(episodes), "armed": armed, "asked_the_model": asked,
            "model_calls_rows": sum(e["model_calls_rows"] for e in per_episode),
            "prompt_tokens": prompt, "completion_tokens": completion, "http_requests": http,
            "cumulative_http_requests_last": cumulative_last,
            "latency_s_min": round(min(latencies), 3) if latencies else None,
            "latency_s_max": round(max(latencies), 3) if latencies else None,
            "outcomes": outcomes, "run_errors": len(errors),
            "run_error_buckets": bucket_errors(errors),
            "per_episode": per_episode}


def bucket_errors(errors):
    """429 / quota / other. The first two are environment errors and are never read as an arm
    effect (SPEC §6 E1's per-episode acceptance)."""
    b = {"quota_or_429": 0, "other": 0}
    samples = {"quota_or_429": [], "other": []}
    for e in errors:
        text = json.dumps(e, ensure_ascii=False)
        is_env = ("429" in text or "Too Many Requests" in text or "quota" in text.lower()
                  or "attempt" in text and "failed after" in text)
        key = "quota_or_429" if is_env else "other"
        b[key] += 1
        if len(samples[key]) < 2:
            samples[key].append(str(e.get("error"))[:200])
    b["samples"] = samples
    return b


def run_cell(set_name, arm, shape, *, repeats, extra):
    cell = os.path.join(ARCHIVE, f"{set_name}_{arm}")
    os.makedirs(cell, exist_ok=True)
    store = os.path.join(cell, "store.jsonl")
    lib = os.path.join(cell, "library.jsonl")
    open(store, "a").close()
    open(lib, "a").close()
    cmd = [PY, "-m", "embodied_agent.cli", "evaluate",
           "--set", set_name,
           "--planner", "deepseek",
           "--modes", "B",
           "--repeats", str(repeats),
           "--out-root", cell,
           "--perceive", "privileged",
           "--ablation", arm,
           "--experience-store", store,
           "--skill-memory", lib,
           "--model-config", SEAT,
           "--frozen",
           "--no-frames"] + extra
    env = {**os.environ, "PYTHONPATH": "."}
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, env=env)
    tail = [ln for ln in (r.stdout + r.stderr).splitlines() if "pybullet" not in ln]
    return {"cmd": " ".join(cmd[2:]), "rc": r.returncode,
            "wall_s": round(time.time() - t0, 2), "said": tail[-4:]}


def find_cell_dir(cell):
    """The runner appends its own run id, so the cell is `cell/<run_id>`."""
    for p in sorted(glob.glob(os.path.join(cell, "*"))):
        if os.path.isdir(os.path.join(p, "episodes")):
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--upper-bound", type=int, default=UPPER_BOUND)
    ap.add_argument("--only-cell", default=None, help="e.g. long_horizon_full")
    ap.add_argument("--stop-rule", type=int, default=CONSECUTIVE_QUOTA_STOPS)
    args = ap.parse_args()

    os.makedirs(ARCHIVE, exist_ok=True)
    state_path = os.path.join(ARCHIVE, "e1_state.json")
    state = {"spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §6 E1 / §9 P2'",
             "phase": "P2'-E1", "seat": SEAT, "seat_sha256": None,
             "arms": ARMS, "sets": SETS, "repeats": args.repeats,
             "upper_bound": args.upper_bound, "spent_calls": 0, "cells": [],
             "stopped": None, "stopped_because": None, "consecutive_quota_deaths": 0}
    if os.path.exists(state_path):
        state = json.load(open(state_path, encoding="utf-8"))

    import hashlib
    with open(os.path.join(REPO, SEAT), "rb") as f:
        state["seat_sha256"] = hashlib.sha256(f.read()).hexdigest()

    done = {f"{c['set']}_{c['arm']}" for c in state["cells"] if c.get("summary")}

    for set_name, arm, shape in cells():
        key = f"{set_name}_{arm}"
        if args.only_cell and key != args.only_cell:
            continue
        if key in done:
            print(f"[E1] {key}: already paid for and summarised, skipping", flush=True)
            continue
        print(f"[E1] === {key} ===", flush=True)
        run = run_cell(set_name, arm, shape, repeats=args.repeats, extra=[])
        cell_dir = find_cell_dir(os.path.join(ARCHIVE, key))
        summary = read_cell(cell_dir) if cell_dir else {"episodes": 0}
        entry = {"set": set_name, "arm": arm, "run": run, "cell_dir": cell_dir,
                 "summary": summary}
        state["cells"] = [c for c in state["cells"] if f"{c['set']}_{c['arm']}" != key] + [entry]
        state["spent_calls"] = sum((c.get("summary") or {}).get("http_requests", 0)
                                   for c in state["cells"])
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")

        s = summary
        print(f"      rc={run['rc']} {run['wall_s']}s  episodes={s.get('episodes')} "
              f"armed={s.get('armed')} asked={s.get('asked_the_model')} "
              f"http={s.get('http_requests')} prompt={s.get('prompt_tokens')} "
              f"completion={s.get('completion_tokens')}", flush=True)
        print(f"      outcomes={json.dumps(s.get('outcomes'))} "
              f"run_errors={s.get('run_errors')} "
              f"buckets={json.dumps(s.get('run_error_buckets', {}).get('quota_or_429'))} "
              f"quota / {s.get('run_error_buckets', {}).get('other')} other", flush=True)
        print(f"      cumulative http_requests={state['spent_calls']} "
              f"of upper bound {state['upper_bound']}", flush=True)

        buckets = (s.get("run_error_buckets") or {})
        quota = buckets.get("quota_or_429", 0)
        state["consecutive_quota_deaths"] = (
            state["consecutive_quota_deaths"] + 1 if quota else 0)
        if state["consecutive_quota_deaths"] >= args.stop_rule:
            state["stopped"] = key
            state["stopped_because"] = (f"{args.stop_rule} consecutive cells with 429/quota "
                                        f"deaths (SPEC §8.2); the breakpoint is this cell and "
                                        f"the cells after it are unpaid")
            break
        if state["spent_calls"] >= state["upper_bound"]:
            state["stopped"] = key
            state["stopped_because"] = (f"upper bound {state['upper_bound']} reached "
                                        f"(SPEC §8.1: hit means stop, not 'a little more')")
            break

    print()
    print("spent http_requests:", state["spent_calls"], "of", state["upper_bound"])
    if state["stopped"]:
        print("stopped at:", state["stopped"], "-", state["stopped_because"])
    print("state:", state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
