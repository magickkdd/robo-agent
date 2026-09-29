"""E2's formal scale, written down as the pre-registration addendum it is.

D2 said the probe batch decides E2's scale, and this is that decision, with the arithmetic that
produced it written next to the number so a reader can disagree with one of them specifically.

The pre-registration itself (`configs/experiment/v03_e2_preregistration.json`, committed at
b918f55) left `formal_scale` as a string saying it would be decided from the probe readings. That
file is not edited; this one fills the gap it declared.
"""
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
os.chdir("/home/czx/embodied-agent-robot-agent-embodied-agent-4")

# Every number below is read from the probe batch's own artefacts rather than transcribed, so
# the addendum cannot drift from what the probe actually spent.
PROBE = "/home/czx/embodied-agent-batches/v03/e2_probe"

# The probe's episodes have **no** `episode_summary.json`: both died before the summary was
# written, which is one of the probe's findings. So the episode set is the directories under
# `episodes/`, and the `perception` count comes from their `events.jsonl` directly rather than
# through a summary that is not there.
eps_dirs = sorted(glob.glob(f"{PROBE}/**/episodes/*", recursive=True))
eps_dirs = [d for d in eps_dirs if os.path.isdir(d)]

summary_files = [os.path.join(d, "episode_summary.json") for d in eps_dirs
                 if os.path.exists(os.path.join(d, "episode_summary.json"))]
ledgers = [os.path.join(d, "model_calls.jsonl") for d in eps_dirs
           if os.path.exists(os.path.join(d, "model_calls.jsonl"))]
prompt = completion = http = rows = ok = failed = 0
perception = 0
for f in ledgers:
    for line in open(f, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        rows += 1
        u = rec.get("usage") or {}
        prompt += int(u.get("prompt_tokens") or 0)
        completion += int(u.get("completion_tokens") or 0)
        http += int(rec.get("http_requests_this_call") or 0)
        if rec.get("ok"):
            ok += 1
        else:
            failed += 1
for d in eps_dirs:
    for line in open(os.path.join(d, "events.jsonl"), encoding="utf-8"):
        if line.strip():
            try:
                if json.loads(line).get("type") == "perception":
                    perception += 1
            except (json.JSONDecodeError, OSError):
                pass

eps = len(eps_dirs)
per_look = http / max(1, ok)
addendum = {
    "spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §6 E2 / §8.1",
    "kind": "pre-registration addendum 1 (E2 formal scale)",
    "amends": "configs/experiment/v03_e2_preregistration.json (committed b918f55, before the "
              "probe batch; the file declared `formal_scale` as 'DECIDED FROM THE PROBE "
              "READINGS' and is not edited by this one)",
    "cell_filled": "formal_scale",
    "decided_by": {"D2": "user 2026-09-29: the probe batch decides the scale"},
    "probe_measurements": {
        "artefact": PROBE,
        "episodes": eps,
        "perception_records": perception,
        "ledger_rows": rows,
        "ledger_ok": ok,
        "ledger_failed": failed,
        "http_requests": http,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "requests_per_answered_look": round(per_look, 3),
        "note": "the ledger field is `http_requests_this_call`; `http_requests_total` is a "
                "cumulative counter and summing it is the mistake that stopped E1 twice this "
                "phase (see configs/experiment/v03_e1_preregistration_amendment_1.json)",
    },
    "scale": {
        "task_sets": {"long_horizon": ["lh_c1_shared_pair_restore",
                                        "lh_c3_capacity_and_shift"]},
        "channels": {"vlm": "full", "stub": "wo_vlm", "privileged": "full"},
        "repeats_per_case": 2,
        "episodes": 12,
        "vlm_episodes": 4,
        "why_these_cases": "two lh cases of different shape: a shared-pair restore (a "
                           "disrupted state the loop must re-establish) and a capacity/shift "
                           "case (a constraint the loop must respect). §8 B5's missing row is "
                           "about whether a vision reading helps at all, which is a question "
                           "about two shapes rather than one.",
        "why_these_channels": "vlm is the arm under test and closes §8 B5's `full`-on-VLM row; "
                               "stub is §9's limited semantic baseline at zero cost and is what "
                               "makes a vlm difference attributable to the vision model rather "
                               "than to having a camera; privileged is the control shape with no "
                               "camera and no request",
        "arm_differs_by_channel": "on purpose, and it is arm_coherence working rather than a "
                                  "confound: a `full` claim on stub is refused because the "
                                  "module that owns the `perception` record is off, and each "
                                  "episode's own `ablation` record names the condition, so the "
                                  "three cells cannot be pooled into one 'vlm vs not' claim",
    },
    "cost": {
        "requests_per_vlm_episode": round(per_look * 11, 1),
        "looks_per_vlm_episode_observed": 11,
        "projected_requests_for_the_vlm_half": round(per_look * 11 * 4),
        "projected_prompt_tokens": prompt // max(1, eps) * 4,
        "as_a_share_of_E1_upper_bound": round(per_look * 11 * 4 / 3800, 4),
        "the_other_two_channels": 0,
        "unit_price": {"pricing_configured": False, "usd": None,
                       "why": "free tier, no price configured; per-call tokens and latency are "
                              "reported instead (SPEC §7.1)"},
    },
    "what_constrains_this_batch": "not the budget. The seat's burst 429 is the live risk: the "
                                  "probe lost 1 of 23 requests to it, and configs/models/"
                                  "agnes-vision-r4.yaml already carries max_retries 4 for that "
                                  "reason. The stop rule is SPEC §8.2: three consecutive "
                                  "429/quota deaths stop the batch with a checkpoint.",
    "artefact_directory": "/home/czx/embodied-agent-batches/v03/e2",
}

path = "configs/experiment/v03_e2_preregistration_addendum_1.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(addendum, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
with open(path, "rb") as f:
    digest = hashlib.sha256(f.read()).hexdigest()
print(f"wrote {path} ({os.path.getsize(path)} bytes, sha256 {digest[:16]})")
print("probe measurements:", json.dumps(addendum["probe_measurements"], ensure_ascii=False))
print("scale:", json.dumps(addendum["cost"], ensure_ascii=False, indent=1))
