"""Entry point: `python -m embodied_agent.benchmark_mujoco.cli <env-check|run|gui>`.

A separate entry point on purpose. `embodied_agent/cli.py:run` is the desktop PyBullet
bench and its frozen flag set; a second environment that shares that parser would put
a benchmark install behind a flag on the production CLI. Run this one with the
interpreter that has mujoco (`/home/czx/mwvenv/bin/python`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

from ..core.v02 import ABLATION_CONDITIONS, Ablation
from ..core.v02 import ablation as make_ablation
from .env import TASKS
from .runner import PERCEIVE_CHANNELS, PLANNERS

#: Absolute, because the run may be launched from any directory and a relative
#: `configs/models/...` would resolve against that cwd and read a different file — or
#: none, which `from_env` answers by falling back to `deepseek.yaml`. On this channel a
#: silent fallback to a provider nobody asked for is the failure to design out.
DEFAULT_MODEL_CONFIG = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "configs", "models", "agnes.yaml"))


def cmd_env_check(argv: list[str]) -> int:
    """What this machine can actually run, measured rather than assumed."""
    import platform
    out: dict[str, Any] = {"checked_at": _now(), "host": platform.platform(),
                           "python": sys.version.split()[0], "executable": sys.executable,
                           "mujoco_gl": os.environ.get("MUJOCO_GL", "(unset -> osmesa)")}
    errors: list[str] = []
    try:
        import importlib.metadata as md
        out["packages"] = {name: _ver(md, name) for name in
                           ("metaworld", "mujoco", "gymnasium", "numpy", "scipy", "imageio")}
    except Exception as e:  # noqa: BLE001
        errors.append(f"packages: {type(e).__name__}: {e}")
        out["packages"] = {}
    try:
        import metaworld
        names = sorted(metaworld.ALL_V3_ENVIRONMENTS)
        out["benchmark_tasks_total"] = len(names)
        out["tasks_offered"] = {k: TASKS[k] for k in sorted(TASKS)}
        out["tasks_present"] = {k: (k in names) for k in sorted(TASKS)}
        missing = [k for k, ok in out["tasks_present"].items() if not ok]
        if missing:
            errors.append(f"installed metaworld does not expose {missing}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"metaworld import: {type(e).__name__}: {e}")
    try:
        from .env import MuJoCoBackend
        b = MuJoCoBackend("peg-insert-side-v3", task_index=0, seed=0)
        b.start()
        out["start_ok"] = True
        out["measured_at_reset"] = b.measured()
        frame = b.capture("corner2")
        out["render_ok"] = bool(frame is not None and hasattr(frame, "shape")
                                and frame.shape[-1] == 3)
        out["render_shape"] = list(getattr(frame, "shape", []))
        out["eval_view_at_reset"] = b.eval_view()
        b.close()
    except Exception as e:  # noqa: BLE001
        out["start_ok"] = False
        errors.append(f"backend start/render: {type(e).__name__}: {e}")
    out["ablation_conditions"] = sorted(ABLATION_CONDITIONS)
    out["perceive_channels"] = list(PERCEIVE_CHANNELS)
    # Which of the model configs on disk may drive a camera, read off each file's
    # `capabilities` line and nothing else — no key, no env var, no request. A rotation
    # between two endpoints starts by knowing which of the two can be shown a frame.
    from .runner import vision_capability
    out["model_configs"] = {}
    cfg_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                           "configs", "models"))
    if os.path.isdir(cfg_dir):
        for name in sorted(os.listdir(cfg_dir)):
            if name.endswith((".yaml", ".yml")):
                able, why = vision_capability(os.path.join(cfg_dir, name))
                out["model_configs"][name] = {"vision": able, "why": why}
    out["errors"] = errors
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def cmd_run(argv: list[str]) -> int:
    from .runner import run_one_episode
    unarmed = bool(argv.unarmed)
    ablation: Optional[Ablation] = None
    if argv.ablation == "none":
        # `none` has always meant "the v0.1 loop, no arm"; it is not a condition, and an
        # armed runtime without a condition would file an id saying `unarmed` while
        # running the full system.
        unarmed = True
    elif unarmed and argv.perceive != "privileged":
        # There is no unarmed camera loop on this bench: `build_mujoco_percept_arm` returns
        # a `MujocoPerceptRuntime`, which is the planning-arm stack with a perceiver under
        # it. Accepting the pair would file `condition: unarmed` about an armed episode, so
        # the refusal belongs here rather than in the records that would follow it.
        print(f"--unarmed runs the v0.1 site-reading loop, which has no camera: a "
              f"--perceive {argv.perceive!r} episode is built by "
              f"`build_mujoco_percept_arm` and is armed whatever it is called. Drop one of "
              f"the two.", file=sys.stderr)
        return 2
    elif unarmed and argv.ablation != "full":
        print(f"--unarmed runs the v0.1 loop with no decision arm at all, so it cannot "
              f"also name a condition (--ablation {argv.ablation!r}). Drop one of the two.",
              file=sys.stderr)
        return 2
    elif argv.ablation is None and not unarmed:
        # Nobody named an arm. `arm_for_channel` is the rule the builder itself applies in
        # that case — a stub reading consults no vision model and so may not file the
        # `full` claim, a vlm reading may not file `wo_vlm` — and calling it here is what
        # keeps `--perceive stub` from being 25 episodes that each refuse with the same
        # correct sentence.
        from ..perception.arm import arm_for_channel
        ablation = arm_for_channel(argv.perceive, None) or make_ablation("full")
        print(f"--ablation not given: the {argv.perceive} channel names its own arm, "
              f"{ablation.condition!r}")
    elif not unarmed:
        try:
            # The factory, not the constructor: `Ablation` validates `modules_off`
            # against the registry, so naming a condition without its modules is rejected
            # and every arm except `full` was refused here with a message about
            # registration. `ablation()` reads those modules off the same registry.
            ablation = make_ablation(argv.ablation)
        except Exception as e:  # noqa: BLE001 - the registry is the authority
            print(f"--ablation {argv.ablation!r} is not a registered arm: {e}", file=sys.stderr)
            return 2
    views = tuple(v.strip() for v in argv.views.split(",") if v.strip())
    experience_store = None
    if getattr(argv, "experience_store", None):
        from ..episodic.store import ExperienceStore

        try:
            # `load` on a missing file is an empty store with this path attached, which is
            # §5.4's cold start; a file that exists and does not parse, or that carries
            # another schema generation, raises here rather than half-way through a batch.
            experience_store = ExperienceStore.load(argv.experience_store)
        except ValueError as e:
            print(f"--experience-store {argv.experience_store!r} cannot be read: {e}",
                  file=sys.stderr)
            return 2
        if unarmed:
            # Refused before the loop, for the reason `run_one_episode` states at its own
            # door: the v0.1 loop has no episodic module, so the store would be a silent
            # no-op on an episode filed as `unarmed`.
            print("--experience-store needs the arm: the unarmed v0.1 loop has no episodic "
                  "module to connect it to, so the store would be a silent no-op. Drop one "
                  "of the two.", file=sys.stderr)
            return 2
        print(f"episodic memory: store={argv.experience_store} "
              f"rows_at_start={len(experience_store)} "
              f"fingerprint={experience_store.fingerprint() or '(empty)'}")
    if ablation is not None and ablation.condition == "wo_episodic_memory" \
            and experience_store is None:
        # The runner raises the same sentence; refusing here keeps a `--layouts` sweep from
        # building one refused episode per layout with the simulator already up.
        print("arm 'wo_episodic_memory' needs --experience-store: the contrast is the "
              "module switched off on a loop that has a store, not a loop built without "
              "one, or the two arms differ by more than the gate.", file=sys.stderr)
        return 2
    adapter = None
    config_path = argv.model_config
    if argv.planner == "model":
        from ..adapters.deepseek import DeepSeekPlanner
        # One HTTP client for the whole sweep. `core/runtime.py:_accumulate_usage` bills an
        # episode by subtracting the source's cumulative counter at episode start, so that
        # subtraction is only this episode's spend if the counter belongs to one object —
        # a fresh adapter per layout would report every episode from zero and a batch total
        # nobody could tie back to a request log.
        adapter = DeepSeekPlanner.from_env(config_path).adapter
        print(f"decision source: provider={adapter.provider} model={adapter.model} "
              f"host={adapter.base_url.split('//')[-1].split('/')[0]} "
              f"config={os.path.basename(config_path)} — requests are billed to the free "
              f"quota. Where they are filed: every request the episode opens writes one row to "
              f"its `model_calls.jsonl`, answered or not (`ok` says which) — decision rounds, "
              f"camera looks, and the survey that is asked before the first round — and the "
              f"rows' `http_requests_this_call` deltas sum to the request count the budget "
              f"ledger reports. A look is also filed as the episode's `perception` record "
              f"(tokens, latency, raw text) and is in `model_usage`, because the baseline was "
              f"taken before it; the survey is asked before that baseline, so it is filed in "
              f"`prologue` and the episode's bill is `model_usage_billed` = `model_usage` + "
              f"`prologue`, which is the figure a cost is compared against (H-23, #108, #109).")
    if argv.perceive == "vlm":
        # Two refusals, both before the first request.
        #
        # `--planner rule` is refused because the loop meters HTTP off the *decision
        # source* (`core/runtime.py:_requests_used`, `_accumulate_usage`), and a rule policy
        # reports no counter: the camera would then spend against a 128-request ceiling
        # nothing was reading, and the episode would file `model_usage` zeros next to a bill.
        # The arm a caller probably means by "rule decisions, camera perception" already
        # exists and costs nothing — it is `--perceive stub`, §9's `wo_vlm` baseline, whose
        # reader is the segmentation code rather than a model.
        #
        # A config that does not declare `vision` is refused because the alternative is the
        # endpoint deciding: the survey is the first request of the episode, so an
        # unsupported image part surfaces after the backend is up and the store is open,
        # once per layout.
        if argv.planner != "model":
            print(f"--perceive vlm needs --planner model: the request ledger is read off the "
                  f"decision source, so with --planner {argv.planner!r} the camera's HTTP "
                  f"spend would be unbilled and unbounded. Use --perceive stub for a "
                  f"zero-spend camera.", file=sys.stderr)
            return 2
        from .runner import vision_capability
        able, why = vision_capability(config_path)
        if not able:
            print(f"--perceive vlm refused: {why}", file=sys.stderr)
            return 2
        print(f"vision capability: {why}")
    if argv.perceive != "privileged":
        # The spend, declared before it happens. A camera episode asks at least one question
        # per round and two per skill call, so the ceiling is the number that decides when
        # the episode ends — filing it after the fact would let a budget-exhaustion row read
        # as an arm effect (G-6's "先申报预算" requirement).
        from .perceive import MW_PERCEPT_HTTP_REQUESTS
        print(f"perception: channel={argv.perceive} views={list(views)} "
              f"ceilings: http_requests<={MW_PERCEPT_HTTP_REQUESTS} per episode, "
              f"decision_rounds<={argv.decision_rounds}, skill_calls<={argv.skill_calls}; "
              f"the survey is one question per episode and its evidence is filed in "
              f"`episode_summary.json` under `perception`")
    summaries = []
    # Provenance, declared before the first request. This package is untracked, so the diff hash
    # a manifest used to carry says nothing about an edit to it (phase-log H-15 读数三: two
    # batches, different code, one identity); printing all three lets a caller notice a resume
    # that picked up an edited tree *before* paying for it.
    from ..core.events import git_state
    code = git_state()
    print(f"code identity: commit={code['commit'][:12]} "
          f"dirty_diff_sha256={code['dirty_diff_sha256']} "
          f"untracked_code={code['untracked_code'] or 'unreadable'} "
          f"(the same fields are filed in every episode manifest)")
    for layout in _expand(argv.layouts):
        summaries.append(run_one_episode(
            out_root=argv.out, env_name=argv.task, task_index=layout, seed=argv.seed + layout,
            horizon=argv.horizon, ablation=ablation, armed=not unarmed, views=views,
            skill_calls=argv.skill_calls, decision_rounds=argv.decision_rounds,
            gui=argv.gui, gui_every=argv.gui_every,
            planner=argv.planner, model_config=config_path, adapter=adapter,
            perceive=argv.perceive, experience_store=experience_store))
    for s in summaries:
        print(json.dumps({k: s[k] for k in
                          ("episode_id", "official_success", "env_steps", "skill_calls_executed",
                           "decisions", "termination_reason", "wall_clock_s", "run_error",
                           "obj_to_target_m", "model_usage")}, ensure_ascii=False))
    print(f"\nwrote {argv.out}  ({len(summaries)} episode(s); "
          f"official_success {sum(1 for x in summaries if x['official_success'])}"
          f"/{len(summaries)})")
    print(f"GUI: python -m embodied_agent.benchmark_mujoco.cli gui --dir "
          f"{os.path.join(argv.out, 'episodes', summaries[0]['episode_id'], 'gui')}")
    return 0 if all(not s["run_error"] for s in summaries) else 1


def cmd_gui(argv: list[str]) -> int:
    from .gui import serve
    return serve(argv.dir, argv.port)


def _expand(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",") if x.strip() != ""]


def _ver(md, name: str) -> Optional[str]:
    try:
        return md.version(name)
    except Exception:  # noqa: BLE001
        return None


def _now() -> str:
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="benchmark_mujoco", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("env-check", help="measure what this machine can run")
    e.set_defaults(fn=cmd_env_check)
    r = sub.add_parser("run", help="run one episode through the production loop")
    r.add_argument("--task", default="peg-insert-side-v3", choices=sorted(TASKS))
    r.add_argument("--layouts", default="0", help="layout index, e.g. 0 or 0-3 or 0,2,5")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--horizon", type=int, default=500, help="simulator steps per episode")
    r.add_argument("--ablation", default=None,
                   help="a registered condition; 'none' (or --unarmed) runs the v0.1 loop "
                        "with no arm and therefore no condition, and cannot name one. Left "
                        "out, the --perceive channel names the arm (a stub reading is "
                        "'wo_vlm', a vlm reading is 'full'); on the privileged channel the "
                        "default is 'full'")
    r.add_argument("--unarmed", action="store_true",
                   help="run MujocoRuntime without the arm (files as condition 'unarmed')")
    r.add_argument("--planner", default="rule", choices=PLANNERS,
                   help="'rule' is the zero-spend control; 'model' puts the endpoint named by "
                        "--model-config in the decision seat, one request per round")
    r.add_argument("--perceive", default="privileged", choices=PERCEIVE_CHANNELS,
                   help="what the world state is built from: 'privileged' reads simulator "
                        "site poses (the control arm, and the only one the archived 25 "
                        "episodes used), 'stub' is colour segmentation on a rendered frame "
                        "(§9's wo_vlm baseline, no HTTP), 'vlm' is one vision request per "
                        "look and needs --planner model plus a config that declares "
                        "capabilities: [.., vision]")
    r.add_argument("--model-config", dest="model_config", default=DEFAULT_MODEL_CONFIG,
                   help="configs/models/*.yaml: provider, model, base_url and the NAME of the "
                        "env var holding the key (the key itself is never read into a record)")
    r.add_argument("--experience-store", dest="experience_store", default=None,
                   help="path to an episodic experience store (§5.4): connects the memory "
                        "module to the round and makes 'wo_episodic_memory' a runnable arm "
                        "instead of a refusal; a missing file is an empty store — the cold "
                        "start — and needs the arm (refused with --unarmed)")
    r.add_argument("--views", default="corner2,gripperPOV")
    r.add_argument("--skill-calls", dest="skill_calls", type=int, default=24)
    r.add_argument("--decision-rounds", dest="decision_rounds", type=int, default=32)
    r.add_argument("--gui", default="gui", help="'none' to publish no frames")
    r.add_argument("--gui-every", dest="gui_every", type=int, default=10)
    r.add_argument("--out", required=True)
    r.set_defaults(fn=cmd_run)
    g = sub.add_parser("gui", help="serve a run directory in a browser")
    g.add_argument("--dir", required=True)
    g.add_argument("--port", type=int, default=8093)
    g.set_defaults(fn=cmd_gui)
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    os.environ.setdefault("MUJOCO_GL", "osmesa")
    return int(ns.fn(ns))


if __name__ == "__main__":
    raise SystemExit(main())
