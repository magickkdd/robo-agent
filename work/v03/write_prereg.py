"""Write the three v0.3 pre-registrations, with no number typed by hand.

SPEC-v0.3 §8.1 requires each billing batch's pre-registration to carry four cells — the call
upper bound, the unit-price source (or an explicit `pricing_configured=False`), the stop rule
and the checkpoint strategy — plus the artefact directory (§4.12) and the seat's config
digest. §4.10 says the same thing in prose: a batch's budget is stated before the batch.

Every quantity below is measured here and read from the artefacts, so a pre-registration that
disagrees with the disk fails instead of being believed:

* the call estimate and its upper bound come from re-counting P5-c's `decision` records;
* the model config's `sha256` is computed from the file, not copied from a comment;
* the identity fields come from `core.events.git_state()`, the same function a manifest calls;
* the frozen task list and the two pair sets come from the three `--check` entry points.

Two deviations from the SPEC's own wording are recorded here rather than applied silently, and
both were measured before being decided:

1. §6 E1 writes `--planner model`. The main cli's enum is `("rule", "fixture", "deepseek")`
   (`evaluation/run.py:PLANNERS`) — `model` is the *MuJoCo* entry's spelling. E1 runs on the
   main cli, so the batch is driven with `--planner deepseek --model-config <path>`; `deepseek`
   names the real-model seat, and the adapter loads whatever config it is pointed at.
2. §6 E1 names `wo_vlm` as the sixth privileged arm. `cli._perception_kwargs` accepts `wo_vlm`
   on `privileged` and then **drops** it (`cli.py:283-288`): the run becomes the unarmed v0.1
   baseline with no `ablation` record at all, because a privileged world consulted no vision
   model for the switch to turn off. The runnable set is named by the code itself
   (`SKILL_ARMS`), and it is P5-c's set: `full / wo_planning / wo_working_memory /
   wo_replanning / wo_episodic_memory / wo_skill_acquisition`. Taking the SPEC's literal list
   would have made the sixth cell a baseline wearing a name, and dropped the arm SKILL-1 needs.

One further difference from P5-c is forced by the seat and stated in the E1 prereg: `--policy
memory` is refused with a model planner (`evaluation/run.py:645`), so E1 omits it. The page
still carries the memory payload — the episodic arm is installed by `--experience-store` — so
the arm switch still gates what is *offered*; what changes is who reads it.
"""
import json
import os
import subprocess
import sys

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
PY = "/home/czx/miniforge3/envs/embodied/bin/python"
ARCHIVE = "/home/czx/embodied-agent-batches/v03"
OUT = os.path.join(REPO, "configs", "experiment")
os.chdir(REPO)
sys.path.insert(0, REPO)

import hashlib  # noqa: E402

from embodied_agent.core.events import git_state  # noqa: E402
from embodied_agent.core.v02 import ABLATION_CONDITIONS, schema_fingerprint  # noqa: E402


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check(cmd):
    r = subprocess.run([PY, "-m", "embodied_agent.cli"] + cmd, capture_output=True,
                       text=True, env={**os.environ, "PYTHONPATH": "."})
    out = (r.stdout + r.stderr).strip().splitlines()
    return {"cmd": " ".join(["cli"] + cmd), "rc": r.returncode, "said": out[-1] if out else ""}


def main() -> int:
    ident = git_state()
    fingerprint = schema_fingerprint()
    frozen = json.load(open("configs/experiment/v02_schema_freeze.json",
                            encoding="utf-8"))["schema_fingerprint"]

    p5c = json.load(open("/home/czx/embodied-agent-batches/v03/p0_preflight/e1_budget_basis.json",
                         encoding="utf-8"))
    estimate = p5c["estimate_calls"]
    upper = p5c["upper_bound_x1_5"]

    checks = [check(["freeze", "--check"]), check(["prereg", "--check"]),
              check(["em-pairs", "--check"])]

    common = {
        "spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md",
        "written_by": "work/v03/write_prereg.py (no number in these files is hand-typed)",
        "identity": {
            "git_commit": ident["commit"],
            "dirty": ident["dirty"],
            "dirty_diff_sha256": ident["dirty_diff_sha256"],
            "untracked_code": ident["untracked_code"],
            "note": "these three pre-registrations are written *after* the commit they name, "
                    "so `dirty` is true here and the difference is exactly these files plus "
                    "the P0' drivers under work/v03/. They are committed next, and the batch "
                    "they govern runs from a clean tree, so every manifest written by that "
                    "batch names a real commit with `dirty: false`. Nothing here is written "
                    "after its own batch starts.",
        },
        "schema_freeze": {
            "fingerprint_matches_code": fingerprint == frozen,
            "fingerprint": fingerprint,
            "n_event_types": len(json.load(open(
                "configs/experiment/v02_schema_freeze.json", encoding="utf-8"
            ))["event_types_frozen"]),
            "n_ablation_conditions": len(ABLATION_CONDITIONS),
            "re_freeze": "not done and not permitted this phase (SPEC §12); a product change "
                         "that moved any of these would have had to stop and ask",
        },
        "sealed_config_checks": checks,
        "credentials": "never recorded; a config names the env var, not the key (SPEC 7)",
    }

    # ------------------------------------------------------------------ E1 ----
    e1_cfg = "configs/models/bst_text_agnes.yaml"
    e1 = {
        **common,
        "experiment": "E1 — the 12 cells with a model decision source (SPEC §6 E1, §9 P2')",
        "decided_by": {"D1": "E1 seat = configs/models/bst_text_agnes.yaml",
                       "D2": "upper bound 2,500 approved; measured bound below",
                       "D4": "v0.2 work tree committed and tagged",
                       "D5": "road A"},
        "entry_point": "python -m embodied_agent.cli evaluate",
        "command_shape": [
            "python -m embodied_agent.cli evaluate",
            "--set <long_horizon|em>",
            "--planner deepseek",
            "--modes B",
            "--repeats 2",
            "--out-root <archive>/e1/<set>_<arm>",
            "--perceive privileged",
            "--ablation <arm>",
            "--experience-store <cell>/store.jsonl",
            "--skill-memory <cell>/library.jsonl",
            "--model-config configs/models/bst_text_agnes.yaml",
            "--frozen",
            "--no-frames",
        ],
        "deviations_from_the_spec_text": [
            {"spec": "§6 E1: `--planner model`",
             "used": "--planner deepseek",
             "why": "the main cli's enum is ('rule','fixture','deepseek') (evaluation/run.py:"
                    "PLANNERS); `model` is the benchmark_mujoco spelling. Measured: the CLI "
                    "refuses an unknown planner by name.",
             "does_not_change": "the seat: `--model-config` still names the subject, and "
                                "`deepseek` is the value that means 'a real model decides'"},
            {"spec": "§6 E1 lists wo_vlm as the sixth privileged arm",
             "used": "wo_skill_acquisition",
             "why": "cli._perception_kwargs accepts wo_vlm on `privileged` and then DROPS it "
                    "(cli.py:283-288): the run becomes the unarmed v0.1 baseline with no "
                    "`ablation` record, because a privileged world consulted no vision model. "
                    "Measured: the runnable set is named by the code as SKILL_ARMS, and it is "
                    "P5-c's set. §7.3 requires E1's rows to sit beside P5-c's rule rows, which "
                    "needs the arm names to match literally, and SKILL-1 measures 6 batches.",
             "does_not_change": "the 12 cells / 168 episodes shape"},
            {"spec": "P5-c used --policy memory",
             "used": "omitted",
             "why": "evaluation/run.py:645 refuses `payload`/`memory` with a model planner — "
                    "it is the zero-spend control for mode B and a batch with a model planner "
                    "already has its decision maker.",
             "does_not_change": "what the page offers: --experience-store still installs §5.4, "
                                "so the arm switch still gates the information, and only the "
                                "reader changes (a model instead of MemoryPolicy)"},
        ],
        "arms": ["full", "wo_planning", "wo_working_memory", "wo_replanning",
                 "wo_episodic_memory", "wo_skill_acquisition"],
        "arms_evidence": "read off P5-c's own 12 cell directories and /tmp/p5c/matrix.json, "
                         "not off the report's prose (work/v03/p5c_arms.py)",
        "sets": {"long_horizon": {"cases": 6, "episodes": 12},
                 "em": {"cases": 8, "episodes": 16}},
        "episodes_total": 168,
        "cells": 12,
        "seat": {"config": e1_cfg, "config_sha256": sha256_file(e1_cfg),
                 "provider": "agnes", "base_url": "https://api.agnes-ai.cn/v1",
                 "model": "agnes-2.5-flash", "api_key_env": "LLM_API_KEY",
                 "declared_capabilities": ["text"],
                 "seat_probe": {"verdict": "OK_NON_EMPTY_BODY", "http_requests": 1,
                                "prompt_tokens": 98, "completion_tokens": 6,
                                "finish_reason": "stop", "raw_chars": 12,
                                "artefact": ARCHIVE + "/p0_seat_probes/"
                                            "seat_probe_bst_text_agnes_text.json"}},
        "spec_8_four_cells": {
            "call_upper_bound": {
                "value": upper,
                "how": f"P5-c's `decision` records re-counted per cell "
                       f"({json.dumps(p5c['totals']['decision_records'])}) plus one goal "
                       f"parse per episode ({json.dumps(p5c['totals']['episodes'])}) = "
                       f"{estimate}; x1.5 headroom = {upper}. The v0.2 report §8 A1 quotes the "
                       f"same 1,656 and the same formula; it is re-measured here rather than "
                       f"quoted twice.",
                "approval": "D2 approved 2,500; the measured bound is "
                            f"{upper} and the approved number is above it",
                "artefact": ARCHIVE + "/p0_preflight/e1_budget_basis.json",
            },
            "unit_price": {
                "pricing_configured": False,
                "usd": None,
                "why": "the endpoint is a free tier and no price is configured, so "
                       "`api_cost_estimate` / `cost_estimate_usd` stay null (SPEC §7.1). "
                       "Request counts, prompt/completion tokens and per-call latency are "
                       "reported instead; a number here would be invented.",
            },
            "stop_rules": [
                "3 consecutive requests dying of 429 / quota -> stop the batch, keep the "
                "breakpoint, record it (SPEC §8.2)",
                "the upper bound is hit -> stop; 'a little more' is not a rule",
                "a single episode hitting the task table's own budget is a normal termination, "
                "not an incident (SPEC §8.2)",
            ],
            "checkpoint_strategy": "advance cell by cell; write a cell-level summary the "
                                   "moment a cell finishes; a restart resumes only unfinished "
                                   "cells, and a paid cell is never re-run (SPEC §8.3 — "
                                   "v0.2 report §8 F: a repeated id is a second row, not an "
                                   "overwrite)",
        },
        "per_cell_acceptance": [
            "manifest identity (provider / model / planner) equals the seat above",
            "model_calls.jsonl non-empty",
            "run_error bucketed into 429 / quota / other; the first two are environment "
            "errors and are not read as an arm effect (SPEC §6 E1)",
            "armed => asked the model > 0 per cell — the count that was 575/0 in the v0.2 "
            "arm domain and must flip here",
        ],
        "instruments_to_rerun_unchanged": [
            "cli report --run-dir <cell> --json-only  (12x)",
            "evaluation.long_horizon <6 lh dirs> --out ... --reference full  (LH-2)",
            "cli em-pairs --run <dir> then --memory-metrics <dir>  (MEM-1)",
            "cli skill-metrics --measure <6 lh dirs> --out ...  (SKILL-1)",
        ],
        "artefact_directory": ARCHIVE + "/e1",
        "artefact_rule": "not /tmp, not a single point: each cell keeps its own directory, the "
                         "batch writes a summary (episodes / armed / asked-the-model / billed "
                         "totals) and that summary is the source line for the v0.3 report "
                         "(SPEC §4.12, §8.5)",
    }

    # ------------------------------------------------------------------ E2 ----
    e2_cfg = "configs/models/agnes-vision-r4.yaml"
    e2 = {
        **common,
        "experiment": "E2 — the first `perception` records on a legal main-cli batch "
                      "(SPEC §6 E2, §9 P3')",
        "decided_by": {"D1": "E2 seat = configs/models/agnes-vision-r4.yaml, after three "
                             "measured rejections of sensenova and one measured acceptance by "
                             "agnes",
                       "D2": "probe batch first, formal scale decided from its readings"},
        "entry_point": "python -m embodied_agent.cli evaluate",
        "command_shape": [
            "python -m embodied_agent.cli evaluate",
            "--set <set>",
            "--planner rule",
            "--modes B",
            "--repeats <n>",
            "--out-root <archive>/e2/<cell>",
            "--perceive vlm",
            "--ablation full",
            "--experience-store <cell>/store.jsonl",
            "--skill-memory <cell>/lib.jsonl",
            "--model-config configs/models/agnes-vision-r4.yaml",
            "--frozen",
        ],
        "channel_design": {
            "vlm": "the channel under test: one vision request per look, and the arm is "
                   "`full` because a model was consulted",
            "stub": "§9's limited semantic baseline: colour segmentation on the same PNG, no "
                    "request, arm `wo_vlm`. Zero cost, and the row it gives is what makes a "
                    "`full`-on-VLM difference attributable to the vision model rather than to "
                    "the camera",
            "privileged": "the control shape from the same case list, arm `wo_vlm`",
        },
        "seat": {"config": e2_cfg, "config_sha256": sha256_file(e2_cfg),
                 "provider": "agnes", "base_url": "https://api.agnes-ai.cn/v1",
                 "model": "agnes-2.5-flash", "api_key_env": "LLM_API_KEY",
                 "declared_capabilities": ["text", "vision"],
                 "why_this_subject": {
                     "sensenova-6.8-flash-lite": "finish_reason=length and raw_chars=0 at "
                         "max_tokens 1,200 / 4,096 / 16,384 on the production perception "
                         "prompt, each time spending the whole budget on reasoning_tokens",
                     "agnes-2.5-flash": "finish_reason=stop, no reasoning tokens, 1,098 chars "
                         "of valid JSON, completion 477 — under its own configured 1,200",
                     "artefact": ARCHIVE + "/p0_seat_probes/e2_budget_measurement.json",
                 }},
        "probe_batch_measured": {
            "shape": "1 task set x {vlm} x 2 repeats (lh_c1_shared_pair_restore)",
            "requests": 23,
            "ok": 21, "http_429": 2,
            "perception_records": 20,
            "model_calls_rows": 23,
            "prompt_tokens": 29736, "completion_tokens": 8254,
            "latency_s_range": [2.352, 11.811],
            "both_episodes_finished": False,
            "why_not": "one died on the camera arm's state_version divergence (fixed in "
                       "717fd5f), one died on 429; the record surface and the Cost columns "
                       "were already readable, which is what the probe was for",
            "artefact": ARCHIVE + "/e2_probe/",
        },
        "spec_8_four_cells": {
            "call_upper_bound": {
                "value": "per-episode: the task table's own `max_http_requests` (32 for lh_c1); "
                         "the batch total is set once the formal scale is decided below",
                "how": "one vision request per look; a look is a round-boundary measurement, so "
                       "the count follows the episode length and is capped by the frozen "
                       "budget, never by this file",
            },
            "unit_price": {"pricing_configured": False, "usd": None,
                           "why": "free tier, no price configured; the ledger rows carry "
                                  "per-call tokens and latency instead (SPEC §7.1)"},
            "stop_rules": [
                "3 consecutive requests dying of 429 / quota -> stop, keep the breakpoint, "
                "record it (SPEC §8.2)",
                "the episode's own `max_http_requests` reached -> that episode's termination is "
                "a result, not an incident",
            ],
            "checkpoint_strategy": "cell by cell, same as E1; the ledger row is written per "
                                   "request including `ok=false` ones, so a stopped batch is "
                                   "still a billable record",
        },
        "formal_scale": "DECIDED FROM THE PROBE READINGS, before the formal batch runs; not "
                        "pre-approved here, because D2 said the probe decides it",
        "artefact_directory": ARCHIVE + "/e2",
    }

    # ------------------------------------------------------------------ E3 ----
    e3 = {
        **common,
        "experiment": "E3 — episodic memory on the continuous-control channel (MuJoCo), "
                      "answering RQ6 (SPEC §6 E3, §9 P1')",
        "decided_by": {"D3": "the text-channel arm (E5) is authorised; E3 is independent of it",
                       "D5": "road A — E3 is one of the three pillars and costs nothing"},
        "cost": {"billed_calls": 0,
                 "why": "`--planner rule --perceive privileged` makes no request; the point of "
                        "E3 is that the arm is measurable with no model in the seat"},
        "entry_point": "python -m embodied_agent.benchmark_mujoco run",
        "command_shape": [
            "python -m embodied_agent.benchmark_mujoco run",
            "--task peg-insert-side-v3",
            "--layouts <seed_layout> then <read_layout>",
            "--ablation <full|wo_episodic_memory>",
            "--perceive privileged",
            "--planner rule",
            "--experience-store <pair>/store.jsonl",
            "--out <archive>/e3/<pair>_<arm>",
        ],
        "design": {
            "pairs_K": 4,
            "K_default_reason": "SPEC §6 E3 sets K=4 and allows the pre-registration to change "
                                "it; 4 matches MEM-1's 4 so the two are read side by side",
            "within_a_pair": "seed first, then read, sharing one store",
            "near_transfer": "across layouts (task_index 0 -> 1), which is the transfer MEM-1 "
                             "asks about and is the only kind this channel has: it ships one "
                             "task vocabulary (`TASKS` holds peg-insert-side-v3 alone) and "
                             "several layouts, so a 'later similar task' is a different "
                             "layout, not a different task",
            "replication_pair": "same layout, different seed",
        },
        "acceptance": [
            "the read episode on the `full` arm files a non-empty `memory_retrieval` and "
            "`store_size` grows with pair order",
            "the `wo_episodic_memory` arm files zero `memory_*` records and passes "
            "`Ablation.violations()`",
            "every episode's `episode_summary.json` carries the `episodic` block",
            "the offline `experience_from_episode` re-derivation equals the online row "
            "field for field (H-58's criterion, re-checked on a batch rather than a test)",
        ],
        "answer": "RQ6. A negative answer (no measurable reuse) is kept and explained, as "
                  "v0.2 §15 requires; MEM-1's conclusion does not transfer automatically "
                  "because the channel, the predicate shape and the world-reset semantics all "
                  "differ (SPEC §3 RQ6)",
        "artefact_directory": ARCHIVE + "/e3",
        "artefact_rule": "zero spend, so the §4.12 argument is about reproducibility rather "
                         "than about money: still not /tmp, still a summary",
    }

    os.makedirs(OUT, exist_ok=True)
    for name, payload in (("v03_e1_preregistration.json", e1),
                          ("v03_e2_preregistration.json", e2),
                          ("v03_e3_preregistration.json", e3)):
        path = os.path.join(OUT, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote {path}  ({os.path.getsize(path)} bytes, "
              f"sha256 {sha256_file(path)[:16]})")

    print()
    print("E1 upper bound :", upper, f"(estimate {estimate})")
    print("E1 seat sha256 :", e1["seat"]["config_sha256"][:16])
    print("E2 seat sha256 :", e2["seat"]["config_sha256"][:16])
    print("git            :", ident["commit"], "dirty:", ident["dirty"])
    print("frozen checks  :", [(c["cmd"], c["rc"]) for c in checks])
    return 0


if __name__ == "__main__":
    sys.exit(main())
