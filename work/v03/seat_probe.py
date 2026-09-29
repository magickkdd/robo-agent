"""P0' seat probe: three checks per decision seat, in the order the SPEC names them.

  1. the config is readable and says what it claims (zero spend)
  2. the key named by `api_key_env` loads (zero spend; the value is never printed)
  3. one minimal request returns a non-empty body (ONE billed call per seat)

Probe 3 exists because the known death mode of a free multimodal endpoint is a
`finish_reason=length` answer with zero characters of body -- the request is
billed and nothing arrives, which is a different failure from a refused request.
So the probe measures `raw_chars` and `content_field_present` rather than
"did it raise".

The key never enters this file, its output, or any exception message: only the
variable's NAME and the loaded value's length are recorded.
"""
import argparse
import hashlib
import json
import os
import sys
import time

TEXT_SYSTEM = "You are a connectivity probe. Reply with one JSON object and nothing else."
TEXT_USER = 'Reply with exactly {"ok": true}.'

VISION_SYSTEM = "You are a connectivity probe for a robot camera. Reply with one JSON object."
VISION_USER = ('Look at image[0]. Reply with exactly {"shape": "<the main object shape>", '
               '"ok": true} and nothing else.')


def probe_config(path: str) -> dict:
    import yaml

    with open(path, "rb") as f:
        blob = f.read()
    cfg = yaml.safe_load(blob) or {}
    return {
        "path": os.path.abspath(path),
        "config_sha256": hashlib.sha256(blob).hexdigest(),
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "api_key_env": cfg.get("api_key_env"),
        "capabilities": cfg.get("capabilities"),
        "max_tokens": cfg.get("max_tokens"),
        "max_retries": cfg.get("max_retries"),
        "prompts": cfg.get("prompts"),
        "pricing_usd_per_mtok": cfg.get("pricing_usd_per_mtok"),
        "declared": {
            "vision": "vision" in (cfg.get("capabilities") or []),
            "pricing_configured": bool(cfg.get("pricing_usd_per_mtok")),
        },
    }


def probe_key(config_path: str) -> dict:
    from embodied_agent.adapters.deepseek import DeepSeekPlanner

    planner = DeepSeekPlanner.from_env(config_path)
    key = planner.adapter.api_key
    return {
        "api_key_env": probe_config(config_path)["api_key_env"],
        "loaded": bool(key),
        "value_length": len(key or ""),
        "value_prefix_sha256": hashlib.sha256((key or "").encode()).hexdigest()[:16] if key else None,
        "note": "length + digest of the value; the value itself is never printed or stored",
        "sampling": planner.adapter.sampling,
        "prompt_versions": {"goal": planner.goal_prompt, "decision": planner.decision_prompt,
                            "plan": planner.plan_prompt},
    }


def probe_request(config_path: str, mode: str, image: str | None) -> dict:
    from embodied_agent.adapters.deepseek import DeepSeekPlanner

    planner = DeepSeekPlanner.from_env(config_path)
    ad = planner.adapter
    before = {"http_requests": ad.http_requests, "prompt_tokens": ad.prompt_tokens,
              "completion_tokens": ad.completion_tokens}
    t0 = time.time()
    images = []
    if mode == "vision":
        if not image:
            raise SystemExit("--mode vision needs --image")
        images = [image]
    try:
        if mode == "vision":
            raw, meta = ad.chat_vision(VISION_SYSTEM, VISION_USER, images)
        else:
            raw, meta = ad.chat(TEXT_SYSTEM, TEXT_USER)
        err = None
    except Exception as e:  # noqa: BLE001 - the refusal sentence is the reading
        raw, meta, err = "", {}, f"{type(e).__name__}: {e}"
    wall = time.time() - t0
    raw_chars = len(raw or "")
    finish = meta.get("finish_reason")
    present = meta.get("content_field_present")
    return {
        "mode": mode,
        "image_ref": os.path.abspath(image) if image else None,
        "error": err,
        "raw_chars": raw_chars,
        "raw_response": (raw or "")[:400],
        "finish_reason": finish,
        "content_field_present": present,
        "reasoning_chars": meta.get("reasoning_chars"),
        "returned_model": meta.get("returned_model"),
        "usage": meta.get("usage"),
        "wall_s": round(wall, 3),
        "ledger_delta": {
            "http_requests": ad.http_requests - before["http_requests"],
            "prompt_tokens": ad.prompt_tokens - before["prompt_tokens"],
            "completion_tokens": ad.completion_tokens - before["completion_tokens"],
        },
        "verdict": (
            "REFUSED_NO_ANSWER" if err else
            "DEAD_ZERO_BODY" if (raw_chars == 0 or present is False) else
            "OK_NON_EMPTY_BODY"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--mode", choices=("text", "vision"), default="text")
    ap.add_argument("--image")
    ap.add_argument("--out", required=True, help="archive directory for the probe artifact")
    args = ap.parse_args()

    out = {"spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §9 P0'",
           "phase": "P0'-seat-probe", "probes": {}, "decided_by": "SPEC §14 D1",
           "billed_calls_declared": 1}
    out["probes"]["1_config_readable"] = probe_config(args.config)
    out["probes"]["2_key_loadable"] = probe_key(args.config)
    out["probes"]["3_minimal_request"] = probe_request(args.config, args.mode, args.image)

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.config))[0]
    path = os.path.join(args.out, f"seat_probe_{stem}_{args.mode}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    for name, p in out["probes"].items():
        print(f"--- {name}")
        if name == "1_config_readable":
            print("   provider/base_url/model:", p["provider"], p["base_url"], p["model"])
            print("   api_key_env:", p["api_key_env"], "capabilities:", p["capabilities"],
                  "max_tokens:", p["max_tokens"], "max_retries:", p["max_retries"])
            print("   declared:", p["declared"], "config_sha256:", p["config_sha256"][:16])
        elif name == "2_key_loadable":
            print("   loaded:", p["loaded"], "value_length:", p["value_length"])
            print("   sampling:", p["sampling"])
        else:
            print("   verdict:", p["verdict"], "| finish_reason:", p["finish_reason"],
                  "| raw_chars:", p["raw_chars"],
                  "| content_field_present:", p["content_field_present"])
            print("   returned_model:", p["returned_model"], "| usage:", p["usage"])
            print("   ledger_delta:", p["ledger_delta"], "| wall_s:", p["wall_s"])
            if p["error"]:
                print("   error:", p["error"])
    print("artifact:", path)
    return 0 if out["probes"]["3_minimal_request"]["verdict"] == "OK_NON_EMPTY_BODY" else 2


if __name__ == "__main__":
    sys.exit(main())
