"""v0.4 E1' driver: 60 model-seat episodes in 5 checkpointed blocks (prereg
`configs/experiment/v04_e1_preregistration.json`).

Inherited from `work/v03/e1_batch.py`: spend counted from the artefacts (never guessed),
429/quota deaths bucketed as environment errors, per-block state written the moment a
block finishes. New in v0.4:

* the bound is checked against **both ledgers** (`batch_spend`, the P6-a instrument) —
  the v0.3 E1 batch understated its own spend by 356 goal-parse requests when only the
  per-episode ledger was read;
* the seat is a per-block fact: the primary seat runs every block unless a quota-stop
  armed the failover seat (`sensenova-text.yaml`, block-boundary only), and whichever
  seat ran is recorded per block so the reading can stratify;
* blocks, not cells, are the checkpoint unit: a kill loses at most 12 episodes (~370
  requests), and a restart re-runs no paid block.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
from embodied_agent.evaluation.run import batch_spend  # noqa: E402

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
PY = "/home/czx/miniforge3/envs/embodied/bin/python"
ARCHIVE = "/home/czx/embodied-agent-batches/v04/e1"
CELL = "long_horizon_full"
PRIMARY = "configs/models/bst_text_agnes.yaml"
FAILOVER = "configs/models/sensenova-text.yaml"
BOUND = 2340  # v04_e1_preregistration.json spend_bound.requests
CONSECUTIVE_QUOTA_STOPS = 3  # v0.3 SPEC §8.2, honoured at block granularity here


def run_block(block_index: int, seat: str, *, repeats: int, out_root: str,
              store: str, lib: str) -> dict:
    cmd = [PY, "-m", "embodied_agent.cli", "evaluate",
           "--set", "long_horizon",
           "--planner", "deepseek",
           "--modes", "B",
           "--repeats", str(repeats),
           "--out-root", out_root,
           "--perceive", "privileged",
           "--ablation", "full",
           "--experience-store", store,
           "--skill-memory", lib,
           "--model-config", seat,
           "--frozen",
           "--no-frames"]
    env = {**os.environ, "PYTHONPATH": "."}
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, env=env)
    tail = [ln for ln in (r.stdout + r.stderr).splitlines() if "pybullet" not in ln]
    return {"cmd": " ".join(cmd[2:]), "rc": r.returncode,
            "wall_s": round(time.time() - t0, 2), "said": tail[-4:]}


def read_latest_run(out_root: str) -> dict:
    run_dirs = [p for p in sorted(glob.glob(os.path.join(out_root, "*")))
                if os.path.isdir(os.path.join(p, "episodes"))]
    if not run_dirs:
        return {"episodes": 0, "outcomes": {}, "quota_deaths": 0}
    rd = run_dirs[-1]
    outcomes = {}
    sp = os.path.join(rd, "run_summary.json")
    if os.path.exists(sp):
        outcomes = json.load(open(sp, encoding="utf-8")).get("outcomes", {})
    quota_deaths = 0
    for mc in glob.glob(os.path.join(rd, "episodes", "*", "model_calls.jsonl")):
        for line in open(mc, encoding="utf-8"):
            if not line.strip():
                continue
            rec = json.loads(line)
            err = str(rec.get("error") or "")
            if not rec.get("ok") and ("429" in err or "quota" in err.lower()):
                quota_deaths += 1
    for row in glob.glob(os.path.join(rd, "episodes.csv")):
        pass
    goal_errs = 0
    gc = os.path.join(rd, "goal_resolutions", "goal_calls.jsonl")
    if os.path.exists(gc):
        for line in open(gc, encoding="utf-8"):
            if line.strip() and "429" in line:
                goal_errs += 1
    eps = len(glob.glob(os.path.join(rd, "episodes", "*", "episode_summary.json")))
    return {"run_dir": rd, "episodes": eps, "outcomes": outcomes,
            "quota_deaths": quota_deaths, "goal_429_rows": goal_errs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bound", type=int, default=BOUND)
    ap.add_argument("--repeats", type=int, default=2,
                    help="repeats per block; 5 blocks x this = the episode total")
    args = ap.parse_args()

    cell = os.path.join(ARCHIVE, CELL)
    os.makedirs(cell, exist_ok=True)
    store = os.path.join(cell, "store.jsonl")
    lib = os.path.join(cell, "library.jsonl")
    open(store, "a").close()
    open(lib, "a").close()

    state_path = os.path.join(ARCHIVE, "e1_state.json")
    if os.path.exists(state_path):
        state = json.load(open(state_path, encoding="utf-8"))
    else:
        state = {"spec": "v04_e1_preregistration.json", "bound": args.bound,
                 "blocks": [], "spent_requests": 0, "failover_armed": False,
                 "failover_used": False, "stopped": None, "stopped_because": None}

    for block_index in range(1, 6):
        if state.get("stopped"):
            break
        if any(b["block"] == block_index for b in state["blocks"]):
            continue  # checkpoint: this block already ran
        seat = FAILOVER if (state.get("failover_armed") and not state.get("failover_used")) else PRIMARY
        print(f"[E1'] === block {block_index}/5 seat {os.path.basename(seat)} ===", flush=True)
        run = run_block(block_index, seat, repeats=args.repeats, out_root=cell,
                        store=store, lib=lib)
        summary = read_latest_run(cell)
        spend = batch_spend(cell)
        state["blocks"].append({
            "block": block_index, "seat": seat, "run": run, "summary": summary,
            "cumulative_requests": spend["http_requests"],
            "cumulative_prompt_tokens": spend["prompt_tokens"],
            "cumulative_completion_tokens": spend["completion_tokens"]})
        state["spent_requests"] = spend["http_requests"]
        if seat == FAILOVER:
            state["failover_used"] = True
        quota = summary.get("quota_deaths", 0) + summary.get("goal_429_rows", 0)
        print(f"      rc={run['rc']} {run['wall_s']}s eps={summary.get('episodes')} "
              f"outcomes={summary.get('outcomes')} quota_deaths={quota} "
              f"cumulative={spend['http_requests']}/{args.bound}", flush=True)
        if quota >= CONSECUTIVE_QUOTA_STOPS:
            if not state.get("failover_used") and seat == PRIMARY:
                state["failover_armed"] = True
                state["stopped_because"] = (f"{quota} quota deaths in block {block_index}; "
                                            f"failover armed for the next block")
            else:
                state["stopped"] = block_index
                state["stopped_because"] = (f"{quota} quota deaths in block {block_index} "
                                            f"on the {'failover' if seat == FAILOVER else 'primary'} "
                                            f"seat; no seat left to switch to")
        elif spend["http_requests"] >= args.bound:
            state["stopped"] = block_index
            state["stopped_because"] = (f"bound {args.bound} reached "
                                        f"(SPEC §8.1: hit means stop, not 'a little more')")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

    print(f"[E1'] spent {state['spent_requests']}/{args.bound}; "
          f"stopped={state.get('stopped')} {state.get('stopped_because') or ''}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
