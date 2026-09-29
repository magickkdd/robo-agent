"""E2 预算的一次测量：真 survey 提示下，这个模型的推理到底多长。

P0' 的席位探针用的是一句 trivial 提示，在 4096 下只要 199–1071 个推理 token；真
`PERCEPTION_SYSTEM` + `user_prompt` 在 4096 下推理跑满、正文 0 字符。所以探针低估了
一个数量级，不能拿它定配置。

这一次按用户 D2 修正裁定发**一次**计费请求。提示与帧不是重写的近似，而是用生产代码
自己走一遍得到的：先用一个只记录不请求的假适配器装配 `build_perceiver`，让它渲染出
那一帧并把**逐字**的 `(system, user, images)` 交出来；再拿这份字节去拨真端点一次。
这样"量的是真提示"这句话不靠声称。

判据只有一件事：这一次的推理落在 16384 以下还是撞顶。撞顶说明"再调大 max_tokens"
这条路本身走不通（那不是配置改得动的量）；落回则说明可以据此定配置。
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())

from embodied_agent.adapters.deepseek import DeepSeekPlanner  # noqa: E402
from embodied_agent.evaluation.calibration import cell_catalog  # noqa: E402
from embodied_agent.evaluation.run import build_scene  # noqa: E402
from embodied_agent.evaluation.tasks import build_set  # noqa: E402
from embodied_agent.perception.arm import build_perceiver  # noqa: E402
from embodied_agent.perception.observe import prompt_sha256  # noqa: E402


class _CapturingAdapter:
    """Records the request the production code would send, and sends nothing."""

    def __init__(self):
        self.captured = None
        self.http_requests = self.prompt_tokens = self.completion_tokens = 0
        self.transport_retries = self.api_errors = self.format_repairs = 0
        self.model = self.provider = "capture-only"
        self.max_tokens = 0
        self.sampling = {"model": "capture-only"}

    def chat_vision_json(self, system, user, images, *, kind, prompt_version,
                         max_tokens=None, modality="rgb"):
        self.captured = {"system": system, "user": user, "images": list(images),
                         "kind": kind, "prompt_version": prompt_version,
                         "max_tokens": max_tokens, "modality": modality}
        raise _Captured()


class _Captured(Exception):
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--case", default="lh_c1_shared_pair_restore")
    ap.add_argument("--view", default="main")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=16384)
    args = ap.parse_args()

    case = [c for c in build_set("long_horizon") if c.task_id == args.case][0]
    cap = _CapturingAdapter()
    scene = build_scene(case)
    try:
        perceiver, _gmap = build_perceiver(
            case=case, scene=scene, run_dir="/tmp/v03_e2_budget", episode_id="probe",
            perceive="vlm", adapter=cap,
            catalog=cell_catalog(scene.trays.values()), default_view=args.view)
        try:
            perceiver.look(args.view)
        except _Captured:
            pass
    finally:
        scene.close()
    if cap.captured is None:
        print("no request was captured; the perceiver did not look", file=sys.stderr)
        return 2

    c = cap.captured
    image = c["images"][0]
    with open(image, "rb") as f:
        blob = f.read()
    out = {
        "spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §6 E2 / §8",
        "phase": "P3'-e2-budget-measurement",
        "declared_billed_calls": 1,
        "prompt_is_the_production_one": {
            "prompt_sha256": prompt_sha256(),
            "system_chars": len(c["system"]),
            "user_chars": len(c["user"]),
            "kind": c["kind"],
            "prompt_version": c["prompt_version"],
            "perceiver_max_tokens": c["max_tokens"],
        },
        "frame": {"ref": image, "sha256": hashlib.sha256(blob).hexdigest(),
                  "bytes": len(blob)},
        "config": os.path.abspath(args.config),
        "requested_max_tokens": args.max_tokens,
        "call": None,
    }

    planner = DeepSeekPlanner.from_env(args.config)
    ad = planner.adapter
    before = (ad.http_requests, ad.prompt_tokens, ad.completion_tokens)
    t0 = time.time()
    try:
        raw, meta = ad.chat_vision(c["system"], c["user"], c["images"],
                                   max_tokens=args.max_tokens, modality=c["modality"])
        err = None
    except Exception as e:  # noqa: BLE001
        raw, meta, err = "", {}, f"{type(e).__name__}: {e}"
    out["call"] = {
        "error": err,
        "raw_chars": len(raw or ""),
        "raw_preview": (raw or "")[:300],
        "finish_reason": meta.get("finish_reason"),
        "content_field_present": meta.get("content_field_present"),
        "reasoning_field_present": meta.get("reasoning_field_present"),
        "reasoning_chars": meta.get("reasoning_chars"),
        "usage": meta.get("usage"),
        "returned_model": meta.get("returned_model"),
        "wall_s": round(time.time() - t0, 3),
        "ledger_delta": {
            "http_requests": ad.http_requests - before[0],
            "prompt_tokens": ad.prompt_tokens - before[1],
            "completion_tokens": ad.completion_tokens - before[2],
        },
    }
    usage = meta.get("usage") or {}
    out["verdict"] = {
        "answered": bool(raw) and meta.get("content_field_present") is not False,
        "hit_the_ceiling": meta.get("finish_reason") == "length",
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens"),
    }

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "e2_budget_measurement.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    p = out["prompt_is_the_production_one"]
    print(f"production prompt sha256 : {p['prompt_sha256'][:16]}  "
          f"system {p['system_chars']} chars, user {p['user_chars']} chars")
    print(f"frame                    : {out['frame']['sha256'][:16]}  "
          f"({out['frame']['bytes']} B)")
    print(f"requested max_tokens     : {args.max_tokens}")
    print(f"finish_reason            : {out['call']['finish_reason']}")
    print(f"raw_chars                : {out['call']['raw_chars']}")
    print(f"reasoning_tokens         : {out['verdict']['reasoning_tokens']}")
    print(f"usage                    : {usage}")
    print(f"verdict                  : {out['verdict']}")
    print(f"ledger_delta             : {out['call']['ledger_delta']}  "
          f"wall {out['call']['wall_s']}s")
    if err:
        print(f"error                    : {err}")
    if raw:
        print(f"body                     : {(raw or '')[:200]}")
    print("artifact:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
