"""v0.4 P2 seat probes: qualification for the free-tier seats the next batches will use.

Order of operations is the v0.4 discipline (SPEC §4 principle 15): the criteria live in
the phase log BEFORE the first request; this script only measures. Per seat:

  1. config check (zero spend): readable, says what it claims;
  2. key load (zero spend; the value is never printed — only the name and its length);
  3. one minimal request (one billed call): `raw_chars` and `finish_reason` are measured,
     not "did it raise" — the known free-tier death is finish_reason=length with a
     zero-character body (P0' phase log);
  4. decision-grade probe (`--grade`): a prompt of realistic decision size (~4k tokens)
     at graded max_tokens ceilings. A seat qualifies for E2'/E1' decision work only if
     some tier returns a non-empty, parseable JSON body — a trivial prompt underestimates
     the budget need by an order of magnitude (the P0' lesson this probe exists for).
  5. rate window (`--window N`): N minimal requests back to back; refusals (429/5xx) and
     their spacing are recorded as measurements, not failures — the window feeds the
     batch bound and the stop rule.

The key never enters this file, its output, or any exception message.
"""
import argparse
import hashlib
import json
import os
import sys
import time

TEXT_SYSTEM = "You are a connectivity probe. Reply with one JSON object and nothing else."
TEXT_USER = 'Reply with exactly {"ok": true}.'

# ~4k tokens of decision-shaped filler: the model must hold a task block, an inventory
# and a feedback history, then answer in JSON — the shape of a decide-round payload,
# not the shape of a connectivity ping.
GRADE_BLOCK = (
    '{"task": "restore the table", "inventory": ["obj_red_1 cube", "obj_blue_2 cuboid", '
    '"obj_green_3 cylinder"], "feedback": "pick obj_red_1 -> placed in tray_left", '
    '"constraint": "one short skill call per round, JSON schema must be respected"}'
)
GRADE_USER = ("Below are 40 rounds of a hypothetical episode log. Read them, then reply "
              "with exactly {\"ok\": true, \"rounds_seen\": 40} and nothing else.\n\n"
              + "\n".join(f"round {i:02d}: {GRADE_BLOCK}" for i in range(40)))


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
        "declared": {
            "vision": "vision" in (cfg.get("capabilities") or []),
            "pricing_configured": bool(cfg.get("pricing_usd_per_mtok")),
        },
    }


def _adapter(config_path: str):
    from embodied_agent.adapters.deepseek import DeepSeekPlanner

    return DeepSeekPlanner.from_env(config_path).adapter


def probe_key(config_path: str) -> dict:
    ad = _adapter(config_path)
    key = ad.api_key
    return {"api_key_env": probe_config(config_path)["api_key_env"],
            "loaded": bool(key), "value_length": len(key or ""),
            "note": "length only; the value itself is never printed or stored"}


def probe_minimal(config_path: str) -> dict:
    ad = _adapter(config_path)
    t0 = time.time()
    try:
        raw, meta = ad.chat(TEXT_SYSTEM, TEXT_USER)
        return {"ok": True, "raw_chars": len(raw or ""), "finish_reason": meta.get("finish_reason"),
                "reasoning_chars": meta.get("reasoning_chars"),
                "wall_s": round(time.time() - t0, 2), "raw_preview": (raw or "")[:120]}
    except Exception as e:  # noqa: BLE001 - the exception shape IS the reading
        return {"ok": False, "raw_chars": 0, "wall_s": round(time.time() - t0, 2),
                "error_type": type(e).__name__, "error": str(e)[:200]}


def probe_grade(config_path: str, ceilings: list[int], target_tokens: int = 0) -> dict:
    ad = _adapter(config_path)
    rounds = 40
    if target_tokens:
        # the P0' lesson scaled up: a trivial prompt underestimates by an order of
        # magnitude, so the retest sizes the payload from a MEASURED per-round prompt
        # (45,855 tokens, the em-family decide round on the model seat) instead of
        # guessing. GRADE_USER at 40 rounds measured 2,975 prompt tokens => ~74/round.
        rounds = max(40, round(target_tokens / 74))
    user = (
        f"Below are {rounds} rounds of a hypothetical episode log. Read them, then reply "
        f'with exactly {{"ok": true, "rounds_seen": {rounds}}} and nothing else.\n\n'
        + "\n".join(f"round {i:05d}: {GRADE_BLOCK}" for i in range(rounds)))
    tiers = []
    for ceiling in ceilings:
        t0 = time.time()
        try:
            raw, meta = ad.chat(TEXT_SYSTEM, user, max_tokens=ceiling)
            tiers.append({"max_tokens": ceiling, "ok": len(raw or "") > 0,
                          "raw_chars": len(raw or ""),
                          "finish_reason": meta.get("finish_reason"),
                          "reasoning_chars": meta.get("reasoning_chars"),
                          "usage": meta.get("usage"),
                          "wall_s": round(time.time() - t0, 2),
                          "raw_preview": (raw or "")[:120]})
        except Exception as e:  # noqa: BLE001 - the death mode is the reading
            tiers.append({"max_tokens": ceiling, "ok": False, "raw_chars": 0,
                          "wall_s": round(time.time() - t0, 2),
                          "error_type": type(e).__name__, "error": str(e)[:200]})
        time.sleep(2.0)
    qualified = any(t["ok"] and t.get("finish_reason") == "stop" for t in tiers)
    return {"qualified": qualified, "target_prompt_tokens": target_tokens or 2975,
            "payload_rounds": rounds, "tiers": tiers,
            "criterion": "qualified = some tier returns a NON-EMPTY body with "
                         "finish_reason=stop on the target-sized decision prompt "
                         "(v0.4 phase log, D6' 重测判据)"}


def probe_window(config_path: str, n: int) -> dict:
    ad = _adapter(config_path)
    rows = []
    for i in range(n):
        t0 = time.time()
        try:
            raw, meta = ad.chat(TEXT_SYSTEM, TEXT_USER)
            rows.append({"i": i, "ok": True, "raw_chars": len(raw or ""),
                         "finish_reason": meta.get("finish_reason"),
                         "wall_s": round(time.time() - t0, 2)})
        except Exception as e:  # noqa: BLE001
            rows.append({"i": i, "ok": False, "wall_s": round(time.time() - t0, 2),
                         "error_type": type(e).__name__, "error": str(e)[:120]})
        time.sleep(1.0)
    refused = [r for r in rows if not r["ok"]]
    return {"n": n, "ok": sum(1 for r in rows if r["ok"]), "refused": len(refused),
            "refusals": refused[:5],
            "note": "a measurement for the bound and the stop rule, not a pass/fail"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--grade", action="store_true",
                    help="decision-grade prompt at graded max_tokens ceilings")
    ap.add_argument("--target-tokens", type=int, default=0,
                    help="size the grade payload to ~this many prompt tokens "
                         "(measured-basis sizing, not guesswork)")
    ap.add_argument("--ceilings", default="1200,4096,16384")
    ap.add_argument("--window", type=int, default=0, help="N rapid minimal requests")
    ap.add_argument("--out", default=None, help="write the JSON reading here")
    args = ap.parse_args()

    reading = {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "config": probe_config(args.config),
               "key": probe_key(args.config),
               "minimal": probe_minimal(args.config)}
    if args.grade:
        reading["grade"] = probe_grade(args.config,
                                       [int(x) for x in args.ceilings.split(",")],
                                       target_tokens=args.target_tokens)
    if args.window:
        reading["window"] = probe_window(args.config, args.window)
    blob = json.dumps(reading, ensure_ascii=False, indent=1)
    print(blob)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(blob + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
