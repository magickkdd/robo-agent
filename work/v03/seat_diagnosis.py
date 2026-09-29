"""Diagnose the E2 seat's zero-body answer instead of re-probing until it goes green.

The seat probe returned `finish_reason=length`, `raw_chars=0`,
`content_field_present=False`, with all 1200 completion tokens booked as
`reasoning_tokens`. Two candidate causes that need different fixes:

  A. the endpoint can serve this id on this host, but this model's reasoning
     does not fit in the config's 1200-token budget  -> fix is a config change
     (max_tokens), which is a D1 amendment;
  B. the endpoint cannot serve this id on this host    -> fix is a different
     config, also a D1 amendment.

So the two discriminating calls are:
  (a) a TEXT request to the same id: if text answers and vision does not, the
      failure is in the image path, not in the seat;
  (b) a VISION request with a larger max_tokens on the same frame and prompt:
      if the body appears, the zero body was a budget-vs-reasoning interaction.

Both are billed. Both are declared. Neither is allowed to change a config.
"""
import argparse
import json
import os
import sys
import time

from embodied_agent.adapters.deepseek import DeepSeekPlanner

TEXT_SYSTEM = "You are a connectivity probe. Reply with one JSON object and nothing else."
TEXT_USER = 'Reply with exactly {"ok": true}.'
VISION_SYSTEM = "You are a connectivity probe for a robot camera. Reply with one JSON object."
VISION_USER = ('Look at image[0]. Reply with exactly {"shape": "<the main object shape>", '
               '"ok": true} and nothing else.')


def call(ad, mode, image, max_tokens):
    t0 = time.time()
    before = (ad.http_requests, ad.prompt_tokens, ad.completion_tokens)
    try:
        if mode == "vision":
            raw, meta = ad.chat_vision(VISION_SYSTEM, VISION_USER, [image], max_tokens=max_tokens)
        else:
            raw, meta = ad.chat(TEXT_SYSTEM, TEXT_USER, max_tokens=max_tokens)
        err = None
    except Exception as e:  # noqa: BLE001
        raw, meta, err = "", {}, f"{type(e).__name__}: {e}"
    return {
        "mode": mode,
        "requested_max_tokens": max_tokens,
        "error": err,
        "raw_chars": len(raw or ""),
        "raw_response": (raw or "")[:400],
        "finish_reason": meta.get("finish_reason"),
        "content_field_present": meta.get("content_field_present"),
        "reasoning_chars": meta.get("reasoning_chars"),
        "usage": meta.get("usage"),
        "wall_s": round(time.time() - t0, 3),
        "ledger_delta": {
            "http_requests": ad.http_requests - before[0],
            "prompt_tokens": ad.prompt_tokens - before[1],
            "completion_tokens": ad.completion_tokens - before[2],
        },
        "verdict": ("REFUSED_NO_ANSWER" if err else
                    "DEAD_ZERO_BODY" if (len(raw or "") == 0
                                         or meta.get("content_field_present") is False) else
                    "OK_NON_EMPTY_BODY"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--big-max-tokens", type=int, default=4096)
    args = ap.parse_args()

    planner = DeepSeekPlanner.from_env(args.config)
    ad = planner.adapter
    out = {
        "spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §9 P0'",
        "phase": "P0'-seat-diagnosis",
        "config": os.path.abspath(args.config),
        "image": os.path.abspath(args.image),
        "declared_billed_calls": 2,
        "config_max_tokens_left_unchanged": ad.max_tokens,
        "calls": [],
    }
    out["calls"].append(call(ad, "text", None, ad.max_tokens))
    out["calls"].append(call(ad, "vision", args.image, args.big_max_tokens))

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.config))[0]
    path = os.path.join(args.out, f"seat_diagnosis_{stem}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    for c in out["calls"]:
        print(f"--- {c['mode']} @ max_tokens={c['requested_max_tokens']}")
        print("   verdict:", c["verdict"], "| finish_reason:", c["finish_reason"],
              "| raw_chars:", c["raw_chars"],
              "| content_field_present:", c["content_field_present"],
              "| reasoning_chars:", c["reasoning_chars"])
        print("   usage:", c["usage"])
        print("   ledger_delta:", c["ledger_delta"], "| wall_s:", c["wall_s"])
        if c["error"]:
            print("   error:", c["error"])
        if c["raw_response"]:
            print("   body:", c["raw_response"][:200])
    print("artifact:", path)

    text_ok = out["calls"][0]["verdict"] == "OK_NON_EMPTY_BODY"
    vision_big_ok = out["calls"][1]["verdict"] == "OK_NON_EMPTY_BODY"
    if text_ok and vision_big_ok:
        print("DIAGNOSIS: A - the endpoint serves this id; the zero body was a "
              "budget-vs-reasoning interaction. Fix is a max_tokens change in the config (D1).")
    elif text_ok and not vision_big_ok:
        print("DIAGNOSIS: B - text answers, vision does not even with 4x the budget. "
              "Fix is a different vision config (D1).")
    else:
        print("DIAGNOSIS: C - the seat does not answer text today either; the failure is "
              "not in the image path. Fix is a different seat (D1).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
