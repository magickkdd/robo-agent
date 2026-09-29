"""Build one episode of the MuJoCo benchmark and run it through the production loop.

Nothing here is a second agent: it constructs a backend, a goal, a runtime and a
decision source, calls `run_episode`, and files the result. The one thing worth naming
is `backend.init_z` — the height each object was measured at on the first snapshot,
which is what `carried()` compares against. It is a *measurement taken at reset*, not
a task fact: it says where the peg started, which any camera could see, and never where
it was supposed to end up.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from ..core.contracts import TaskInput
from ..core.events import EpisodeStore, write_manifest
from ..core.v02 import Ablation
from .env import TASKS, MuJoCoBackend
from .gui import GuiSink
from .policy import MujocoPlanPolicy
from .runtime import MujocoRuntime, mw_budgets
from .state import goal_from_assignment

OBJECT_ID = "peg 1"
TARGET_ID = "socket 1"


def run_one_episode(*, out_root: str, env_name: str = "peg-insert-side-v3", task_index: int = 0,
                    seed: int = 0, horizon: int = 500, ablation: Optional[Ablation] = None,
                    armed: bool = True, views: tuple[str, ...] = ("corner2", "gripperPOV"),
                    skill_calls: int = 24, decision_rounds: int = 32,
                    gui: Optional[str] = None, gui_every: int = 10,
                    planner: str = "rule", model_config: Optional[str] = None,
                    adapter: Any = None, perceive: str = "privileged",
                    experience_store: Any = None,
                    episode_id: Optional[str] = None, record: Any = None) -> dict[str, Any]:
    if perceive != "privileged" and not armed:
        # Raised before a directory exists. `build_mujoco_percept_arm` returns a
        # `MujocoPerceptRuntime`, which is the planning stack with a perceiver under it:
        # this bench has no unarmed camera loop to fall back to, so accepting the pair
        # would file `condition: unarmed` — the id's own word for "no arm" — about an armed
        # episode. `cli.py` refuses it too, and this is the check the CLI is not the only
        # door through which this function gets called.
        raise ValueError(f"--perceive {perceive!r} needs the arm: the camera runtime on this "
                         f"channel is `MujocoPerceptRuntime`, a `MujocoPlannedRuntime` with a "
                         f"perceiver under it, so it cannot be filed as unarmed")
    if armed and ablation is not None and ablation.condition == "wo_episodic_memory" \
            and experience_store is None:
        # Same door, same reasoning as `evaluation/run.py:installed_module_refusals`: the
        # contrast §9 asks for is the memory module switched off *on a loop that has a
        # store*. A loop built without one takes a different code path that merely happens
        # to file none of the `memory_*` records, and a row read off it would be a cold
        # start reported as an ablation.
        raise ValueError("arm 'wo_episodic_memory' needs --experience-store: the contrast "
                         "is the module switched off on a loop that has a store, not a loop "
                         "built without one, or the two arms differ by more than the gate")
    if not armed and experience_store is not None:
        # The v0.1 loop has no memory module, so a store handed to it would sit unread —
        # an episode that files as `unarmed` while holding a store is two experiments
        # waiting to be read as one.
        raise ValueError("--experience-store needs the arm: the unarmed v0.1 loop has no "
                         "episodic module to connect it to, so the store would be a silent "
                         "no-op. Drop one of the two.")
    episode_id = episode_id or f"{env_name}__L{task_index}__s{seed}__{ablation.condition if ablation else 'unarmed'}"
    if planner != "rule":
        # A model episode and a rule episode on the same layout are two records, not two
        # names for one file: the first run of this path overwrote nothing only because
        # nobody had asked for both under one id.
        episode_id = f"{episode_id}__{planner}"
    if perceive != "privileged":
        # and the same argument in the other direction: a camera episode and a site-reading
        # one on one layout are two experiments. `privileged` is left out of the name so the
        # 25 archived episodes keep the id their manifests already carry.
        episode_id = f"{episode_id}__perceive-{perceive}"
    if experience_store is not None:
        # ... and the memory arm is the third axis with the same argument: a `full` episode
        # with a store connected and one without are two different loops (the episodic
        # module is installed in exactly one of them), so the id says which ran. Suffix
        # order matches the desktop run name (`...+memstore`), and every archived id —
        # which none of them carries — is untouched.
        episode_id = f"{episode_id}__memstore"
    ep_dir = os.path.join(out_root, "episodes", episode_id)
    os.makedirs(ep_dir, exist_ok=True)
    sink = GuiSink(os.path.join(ep_dir, gui or "gui"), every_steps=gui_every,
                   view=views[0]) if (gui or "").lower() not in ("", "none", "off") else None

    backend = MuJoCoBackend(env_name, task_index=task_index, seed=seed, max_env_actions=horizon,
                            views=views, gui_sink=sink)
    backend.start()
    measured = backend.measured()
    backend.init_z = {eid: float(xyz[2]) for eid, xyz in measured["sites"].items()}
    instruction = TASKS[env_name]

    store = EpisodeStore(ep_dir, episode_id)
    budgets = mw_budgets(skill_calls=skill_calls, decision_rounds=decision_rounds)
    if perceive != "privileged":
        # imported inside the branch rather than at the top: `perceive.py` pulls in the
        # renderer, the vision reader and the segmentation code, and the site-reading
        # control arm of this bench has to stay runnable where none of that is installed.
        # It is also the arm's *budget*, so the ceiling and the channel arrive together.
        from .perceive import mw_percept_budgets
        budgets = mw_percept_budgets(skill_calls=skill_calls, decision_rounds=decision_rounds)
    runtime_cls = MujocoRuntime
    memory_kw: dict[str, Any] = {}
    if armed:
        from .planned_runtime import MujocoPlannedRuntime
        runtime_cls = MujocoPlannedRuntime
        if experience_store is not None:
            # §5.4 on this channel: the store decides the class, exactly as on the camera
            # side (`build_mujoco_percept_arm`) — and the chosen class's own coherence gate
            # is the widened one, so a `wo_episodic_memory` row is a module switched off,
            # not a refusal.
            from .planned_runtime import MujocoExperiencedRuntime
            runtime_cls = MujocoExperiencedRuntime
            # The kind term is this bench's own set fact: the environment an episode ran,
            # which is what `experience_from_episode` files as the row's `task_kind` —
            # the two must be one word or retrieval would query a kind the store never wrote.
            memory_kw = {"experience_store": experience_store, "memory_task_kind": env_name}
    policy, identity = _decision_source(planner, model_config, adapter, ep_dir)
    perception: dict[str, Any] = (
        {"channel": "privileged",
         "note": "every object position in this episode is a simulator site pose; no "
                 "camera was consulted"}
        if perceive == "privileged" else
        # Pre-filled with the channel that was *asked for*, because the alternative is the
        # sentence above appearing on an episode whose survey raised: `runtime` is None
        # until the builder returns, and an artifact that files "no camera was consulted"
        # about a camera episode that died at calibration would be the one line in the
        # record nobody could check. Overwritten wholesale by `_percept_arm` on success.
        {"channel": perceive, "survey": None, "catalog_sha256": None,
         "note": "the arm was never assembled, so no survey evidence exists; see "
                 "`run_error`"})
    runtime = None
    if perceive == "privileged":
        runtime = runtime_cls(backend, ep_dir, budgets, episode_id, store=store,
                              frames_dir=os.path.join(ep_dir, "frames"),
                              **({"ablation": ablation} if armed else {}), **memory_kw)
    if sink is not None:
        # a closure over the name, not the value: on a camera arm `runtime` is still bound
        # below, and `_live_state` reads what is there when the viewer asks.
        sink.live = lambda: _live_state(runtime, policy, ep_dir)

    task = TaskInput(task_id=f"{env_name}/L{task_index}", utterance=instruction, language="en",
                     # These three are §10 disclosures, and the derivation turns every
                     # declared constraint into a `maintain` row. So on this channel an
                     # episode's `unresolved_obligations` has a floor of 3 that is a fact
                     # about how the experiment is run, not a count of work the agent
                     # owed and did not do — read the `achieve` rows for that. Measured on
                     # the 5-layout batch: every one of the 25 episodes finishes with exactly
                     # those three rows still owed, all `satisfied: unknown`, and therefore
                     # with `invalid_completion: true` even when the benchmark scored it a
                     # success, because that flag is "claimed complete while anything is
                     # owed" (`planning/working_memory.py:obligation_check`). The rows are
                     # kept because the contract is public; the flag is reported with this
                     # sentence attached rather than quietly recomputed.
                     declared_constraints=[
                         "one skill per round; the system will not compose, retry or recover "
                         "on the agent's behalf (SPEC-v0.2 §10)",
                         "arguments are entity names, never coordinates",
                         "the benchmark's score is not an observation"])
    goal = goal_from_assignment(OBJECT_ID, TARGET_ID, instruction, state_version=0)

    t0 = time.time()
    prologue: dict[str, Any] = {}
    try:
        if perceive != "privileged":
            # built inside the try, not beside it: a survey that cannot find the hole is
            # this arm's most likely first failure, and it is an episode's outcome — a
            # refused one — not a stack trace that takes the other 24 layouts with it.
            # Imported here rather than at the top: `perceive.py` pulls MuJoCo and the
            # perception stack in, and the privileged arm does not need either. Above the
            # call, so the `except` arm below can bill the same way.
            from .perceive import survey_prologue
            runtime, perception = _percept_arm(
                perceive=perceive, backend=backend, ep_dir=ep_dir, budgets=budgets,
                episode_id=episode_id, store=store, ablation=ablation, adapter=adapter,
                views=views, instruction=instruction,
                experience_store=experience_store, memory_task_kind=env_name,
                frames_dir=os.path.join(ep_dir, "frames"))
            # #108: the survey is a billed request that happens *before* the loop, and until
            # now the loop's baseline was taken after it, so the episode filed a ledger that
            # was short by exactly that request. Measured over the camera archive
            # (`/tmp/mw117/h43_prologue_wall.py` and `h42_survey_values.py`, 61
            # `episode_summary.json` read): the survey's archived request count is the value
            # SET {1, 5} against a ceiling of 128 — an earlier draft of this line said
            # "1, or 2 on a format repair", which read a sliced printout as a range (#122) —
            # and 558/594 prompt against 27..527 completion (38 distinct values) against
            # batch13's 19,516/1,575 episode total. Those archived figures are not the
            # quantity `survey_prologue` reads: none of the 57 archived surveys carries
            # `counters_this_call`, so only batches from this change forward are comparable
            # (#122 again). `wall_clock_s - episode_result.wall_time_s` —
            # the duration this moves inside the billed window — is 2.782 / 7.772 / 23.947 s
            # (min / median / max over the 25 episodes that reached a result) against a
            # 900 s wall budget. `run_episode(prologue=)` is the channel the v0.1 runner
            # already uses for a shared goal parse (`evaluation/run.py:462`), and the wall
            # clock starting at `t0` is its rule, not mine: no episode gets a cheaper budget
            # than the work that physically preceded it.
            prologue = survey_prologue(perception.get("survey"), wall_start=t0)
        result = runtime.run_episode(task, goal, policy, mode="B", prologue=prologue)
        error = None
    except Exception as e:  # noqa: BLE001 - a crashed episode is still an episode
        result, error = None, f"{type(e).__name__}: {e}"
        if runtime is not None:
            # The bill survives the failure. A request that died inside `observe()` never
            # reached the round's `_accumulate_usage`, so on this path the episode used to
            # file `api_errors: 0` beside a fatal HTTP error: measured across the 51
            # archived camera episodes, 9 end in a fatal `LLMError` and 5 of those filed 0
            # (phase-log H-23). Settle before `model_usage` is read below.
            runtime.settle_usage(policy)
        reasons = [str(r) for r in (getattr(e, "reasons", None) or [])]
        lost = getattr(e, "evidence", None)
        if isinstance(lost, dict) and lost:
            # A survey that was asked and refused is a billed request with an answer this
            # run would not accept. `run_error` says which check refused it; without this
            # the artifact carries the tokens in `model_usage` and nothing beside them.
            perception = {**perception, "survey": lost,
                          "note": "the survey asked its question, this run refused the "
                                  "answer and the episode ended there; the request, its "
                                  "raw reply and the refusal reasons are filed under "
                                  "`survey` and `refusal_reasons`"}
        if reasons:
            # the field-level complaints of a refused look or survey — what the model
            # actually said that the schema would not take
            perception = {**perception, "refusal_reasons": reasons}
        if perceive != "privileged" and not prologue:
            from .perceive import survey_prologue
            # A survey that was asked and refused has paid for its request, and this arm's
            # most likely first failure is exactly that episode (#108). There is no ledger to
            # charge on this path — `runtime` is None, so `model_usage` files {} — but
            # `model_usage_billed` is the figure a bill is compared against, and without this
            # line it would read zero for an episode the endpoint answered.
            prologue = survey_prologue(perception.get("survey"), wall_start=t0)
    wall = round(time.time() - t0, 3)

    env_view = backend.eval_view()
    skills_executed = int(getattr(getattr(runtime, "executor", None), "calls", 0))
    # The motion log the primitives keep and the two per-look logs the perception arm keeps. All
    # three are filed here rather than as events, because the 15 record types are frozen and none
    # of them is "what a motor primitive measured". They are also the only readings that can tell
    # a bad aim from a bad measurement, which is the distinction both batch 4 and batch 5 needed
    # and could not make from an artifact (phase-log H-9 item 5).
    perceiver = getattr(runtime, "perceiver", None)
    perception = {**perception,
                  "motions": list(getattr(getattr(runtime, "executor", None),
                                          "motions", ()) or ()),
                  **({"axis_fits": list(perceiver.axes), "hold_claims": list(perceiver.holds)}
                     if perceiver is not None else {})}
    if sink is not None:
        sink.publish(backend, extra={**_live_state(runtime, policy, ep_dir),
                                     "official_success": bool(env_view.get("official_success"))})
    usage = dict(getattr(runtime, "usage", {}) or {})
    # #108. The two are filed side by side and read differently on purpose. `usage` is the
    # delta of the shared adapter's counters since the baseline `run_episode` took, which is
    # the meaning every archived camera episode's `model_usage` carries and the one H-41 §D's
    # account was closed against. `prologue` is what this episode had already spent before
    # that baseline — the survey, its one request and its tokens, or nothing at all on an arm
    # that asked no question — and `billed` is the sum, which is what `episode_result`
    # reports as `prompt_tokens` / `completion_tokens` / `provider_counters` and what a bill
    # has to be computed from; on `usage` alone the survey's tokens would be missing from the
    # money figure while sitting in the endpoint's invoice. The union of keys, not `usage`'s:
    # on the arm where `_percept_arm` raised there is no ledger and `usage` is `{}`, and a
    # comprehension over it would file a zero bill for an episode the endpoint answered.
    billed = {k: int(usage.get(k) or 0) + int(prologue.get(k) or 0)
              for k in set(usage) | (set(prologue) - {"wall_start"})}
    summary = {
        "episode_id": episode_id, "env_name": env_name, "task_index": task_index,
        "seed": seed, "ablation": (ablation.condition if ablation else None),
        "armed": bool(armed), "run_dir": ep_dir,
        "official_success": bool(env_view.get("official_success")),
        "env_steps": int(env_view["env_steps"]), "horizon": int(horizon),
        "skill_calls_executed": skills_executed,
        "reward_total": env_view.get("reward_total"),
        "obj_to_target_m": env_view.get("obj_to_target_m"),
        "backend_error": env_view.get("error"), "run_error": error,
        "frame_capture_error": backend.capture_error,
        "wall_clock_s": wall, "decisions": len(policy.trace),
        "decision_source": identity,
        # The spend, per episode, from the loop's own ledger — not re-read off the
        # adapter, whose counter is cumulative across a batch. The three keys here, and the
        # `usage` / `billed` split above, say which of them means what.
        "model_usage": usage,
        "prologue": {k: v for k, v in prologue.items() if k != "wall_start"},
        "model_usage_billed": billed,
        "api_cost_estimate_usd": _cost_estimate(billed, identity.get("pricing_usd_per_mtok")),
        "model_calls_log": (os.path.join(ep_dir, "model_calls.jsonl")
                            if os.path.exists(os.path.join(ep_dir, "model_calls.jsonl"))
                            else None),
        "decision_trace": policy.trace,
        "termination_reason": getattr(runtime, "termination_reason", None),
        "episode_result": result.model_dump(mode="json") if result is not None else None,
        # What the camera was asked and what it answered, on the camera arm; on the
        # privileged arm this says so in one sentence. The survey is *not* filed as an
        # event (the 15 record types are frozen and none is a calibration act), so this is
        # the only place a reviewer can see the box the model was pointed at, the pixels it
        # named, and the metres those pixels solved to.
        "perception": perception,
    }
    # §5.4's write, filed through the same `write_experience` the desktop runner calls, so
    # the online row and an offline re-read of this archive come from one extractor on one
    # summary. Only an episode with a terminal record contributes a row: `outcome_of`
    # collapses an empty `result` into `failure`, and a crash is the channel's event, not
    # an outcome the agent earned — writing that row would be a claim no record supports.
    writer = getattr(runtime, "write_experience", None)
    if writer is not None and result is not None:
        before = list(experience_store.ids()) if experience_store is not None else []
        experience = writer(summary, run_ref=ep_dir)
        summary["episodic"] = {
            "arm": (ablation.condition if ablation else None),
            "store_path": getattr(experience_store, "path", None),
            "store_size_before": len(before),
            "experiences_before": before,
            "retrievals": getattr(runtime, "retrievals", 0),
            "rows_retrieved": getattr(runtime, "retrieved_rows", 0),
            "rows_used": getattr(runtime, "used_rows", 0),
            "written_experience_id": (experience.experience_id if experience else None),
            "store_size_after": (len(experience_store.ids()) if experience_store else 0),
            "store_fingerprint": (experience_store.fingerprint() if experience_store else None),
            "memory_events": {key: sum(1 for e in store.read_all() if e["type"] == key)
                              for key in ("memory_retrieval", "memory_use", "memory_write")},
        }
    # One manifest per episode, filed beside the records it describes. `write_manifest``
    # takes a directory and a run may hold several layouts, so a run-root copy could name
    # exactly one `task_index` and was measured doing so — the last episode of a
    # `--layouts 0-1` run, describing the whole run as layout 1.
    write_manifest(ep_dir, run_id=os.path.basename(os.path.normpath(out_root)),
                   episode_id=episode_id,
                   benchmark="metaworld", environment=env_name, task_index=task_index,
                   seed=seed, ablation=(ablation.condition if ablation else "unarmed"),
                   # Filed from the source that actually ran, not from a string here: a
                   # manifest that says `model: rule` about an episode whose actions came
                   # back over HTTP would be the artifact to disbelieve later.
                   planner=identity["planner"], provider=identity["provider"],
                   model=identity["model"], prompt_version=identity["prompt_version"],
                   rule_interpreter=("rule_mujoco_plan" if identity["planner"] == "rule"
                                     else "none: the round's action was the model's"),
                   model_config=identity["model_config"], config_sha256=identity["config_sha256"],
                   prompts_sha256=identity["prompts_sha256"],
                   base_url_host=identity.get("base_url_host"),
                   # `_redact` matches this key on its name, so the artifact shows
                   # `***redacted***` rather than this sentence. Kept anyway: the same
                   # field is what `benchmark/runner.py` passes, and the two channels'
                   # manifests should differ only in what they measure. The claim itself
                   # survives under `environment_variables`, which `write_manifest` writes
                   # unconditionally and which preregistration claim I3 points at.
                   credentials="not recorded (SPEC 7: no credentials or full env)",
                   packages=_versions(), catalogue_sha256=_catalogue_sha(),
                   # The three lines that let a manifest be read as a claim about a channel:
                   # which sensor arm ran, which vocabulary of entity ids its percepts were
                   # grounded against (`catalog_sha256` is the digest every percept is
                   # checked against at `perceive.py:world_state_from_percept`), and which
                   # bytes the one calibration question was asked in. The survey's *answer*
                   # is too big for a manifest and lives in `episode_summary.json`.
                   perceive=perceive,
                   perception_channel=perception.get("channel"),
                   grounding_map=perception.get("grounding_map"),
                   catalog_sha256=perception.get("catalog_sha256"),
                   survey_prompt_sha256=perception.get("survey_prompt_sha256"),
                   instruction=instruction,
                   prohibitions="no auto-plan / auto-retry / auto-recovery / action "
                                "composition / hidden-state injection (SPEC-v0.2 §10)")
    with open(os.path.join(ep_dir, "episode_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    return summary


#: The only two seats this channel can fill. `rule` is the zero-spend control that keeps
#: the instruments readable; `model` puts an endpoint in the decision seat and is where an
#: arm effect can actually be measured. There is deliberately no "fall back to rule if the
#: request fails" option: that would be a model run whose failures are answered by a rule,
#: and every number downstream of it would be a mixture of two agents.
PLANNERS = ("rule", "model")


def _sha_file(path: str) -> str:
    import hashlib
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _cost_estimate(usage: dict[str, Any], pricing: Optional[dict[str, Any]]) -> Optional[float]:
    """Money only when a price is configured. `None` is the answer that is not invented
    (SPEC 11.4), and this free endpoint has no price in `configs/models/agnes.yaml`."""
    if not pricing:
        return None
    pin = float(pricing.get("input", 0.0) or 0.0)
    pout = float(pricing.get("output", 0.0) or 0.0)
    return round(int(usage.get("prompt_tokens", 0) or 0) / 1e6 * pin
                 + int(usage.get("completion_tokens", 0) or 0) / 1e6 * pout, 6)


def _decision_source(planner: str, model_config: Optional[str], adapter: Any,
                     ep_dir: str) -> tuple[Any, dict[str, Any]]:
    """One decision source, and the identity a manifest files about it.

    `adapter` is accepted so a batch can hold one HTTP client across episodes — the
    accounting in `core/runtime.py:_accumulate_usage` subtracts a per-episode baseline,
    which only means anything if the counter it subtracts is the same object's."""
    if planner not in PLANNERS:
        raise ValueError(f"--planner must be one of {PLANNERS}, got {planner!r}")
    if planner == "rule":
        return MujocoPlanPolicy(), {"planner": "rule", "provider": "rule_mujoco_plan",
                                    "model": "rule", "prompt_version": "no-prompt",
                                    "model_config": None, "config_sha256": None,
                                    "prompts_sha256": None}
    from ..adapters.deepseek import DeepSeekPlanner
    from .model_policy import MujocoModelPolicy
    from .prompts import PROMPT_VERSION, prompts_sha256

    path = model_config or os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "configs", "models", "agnes.yaml"))
    if adapter is None:
        # `from_env` is the code that turns a config's `api_key_env` into a key without
        # ever writing the value down, and it is the only reason this function can name a
        # provider without naming a secret.
        adapter = DeepSeekPlanner.from_env(path).adapter
    identity = {"planner": planner, "provider": adapter.provider, "model": adapter.model,
                "prompt_version": PROMPT_VERSION, "model_config": os.path.abspath(path),
                "config_sha256": _sha_file(os.path.abspath(path)),
                "prompts_sha256": prompts_sha256(),
                "base_url_host": adapter.base_url.split("//")[-1].split("/")[0],
                "pricing_usd_per_mtok": adapter.pricing or None}
    return MujocoModelPolicy(adapter, ep_dir=ep_dir), identity


#: the sensor channels this bench can build, kept in sync with
#: `perception/grounding.py:PERCEIVE_CHANNELS` by the contract test rather than by an
#: import: `privileged` is the control arm, `stub` is §9's `wo_vlm` baseline, `vlm` is §5.1.
PERCEIVE_CHANNELS = ("privileged", "stub", "vlm")


def vision_capability(config_path: Optional[str]) -> tuple[bool, str]:
    """This bench's name for the question, now answered in one place.

    The body moved to `adapters.deepseek.vision_capability` when the main CLI had to ask it
    too: `cli.py` and `run.py` each called `channel_readiness` with no adapter, so
    `--perceive vlm` was refused whatever `--model-config` said, while the refusal sentence
    named `--model-config` as the fix. Re-exported rather than kept as a second copy so the
    bench and the desktop still have exactly one answer to "can this config see", and so
    `tests/contract/test_mujoco_channel.py` keeps importing the name it was written against.

    Its reasoning, unchanged: read off the raw YAML rather than off an adapter, because
    `chat_vision` is a method every endpoint in this repo has structurally.
    """
    from ..adapters.deepseek import vision_capability as _impl

    return _impl(config_path)


def _percept_arm(*, perceive: str, backend: Any, ep_dir: str, budgets: Any,
                 episode_id: str, store: Any, ablation: Optional[Ablation], adapter: Any,
                 views: tuple[str, ...], instruction: str,
                 experience_store: Any = None, memory_task_kind: str = "",
                 frames_dir: str) -> tuple[Any, dict[str, Any]]:
    """Assemble the camera arm, and hand back the calibration evidence to file.

    A thin call on `build_mujoco_percept_arm`, and deliberately no logic in it. The builder
    owns the order that order matters in — survey, then catalog, then perceiver, because a
    percept whose catalog digest differs from the tracker's is refused — and a wrapper that
    rearranged any of that would be a second, worse version of the same channel.

    What it does add is the two things only this function can know: which frames directory
    the episode's own images belong in, and the fact that the episode has an id and a store
    to file into. And `survey_prompt_sha256()`, because the survey is one question asked
    once per episode and the bytes of that question are the difference between a
    calibration and a coin flip. The episodic store and its kind term pass through
    untouched: which class the builder returns is the builder's own decision.
    """
    from .perceive import build_mujoco_percept_arm, survey_prompt_sha256

    runtime, evidence = build_mujoco_percept_arm(
        backend=backend, run_dir=ep_dir, budgets=budgets, episode_id=episode_id,
        store=store, perceive=perceive, adapter=adapter, ablation=ablation,
        experience_store=experience_store, memory_task_kind=memory_task_kind,
        frames_dir=frames_dir, views=views, task=_task_context(instruction))
    evidence["survey_prompt_sha256"] = survey_prompt_sha256()
    return runtime, evidence


def _task_context(instruction: str):
    """The visual half of the task: the words a frame is being asked to read itself against.

    Not derived from the utterance the way the desktop channel derives its own
    (`calibration.py:task_context_of` picks the colour words that appear in the sentence —
    this bench's sentence contains none of them, it says "the peg"). These two words are
    stated, and what each one buys is different:

    * `["green"]` is the identifying attribute `grounding_table()` keys the peg's entity id
      on, so a detection reported in any other word cannot be aimed at. It discloses
      nothing the request does not already contain: `MuJoCoCellCatalog.prompt_context()`
      opens with 还有一根长的绿色杆状物 and then lists `green`、`brown` as the legal
      `color` values, so the mention points at a word already printed above it.
    * `["socket 1"]` names a region by the id the same prompt prints, with its centre and
      radius — a pointer to a declaration, not a position handed over.

    What stays out is the per-layout truth: the benchmark's `goal` site pose, which
    MetaWorld rewrites every layout and which `wall_declaration()` therefore never reads, and
    any statement of what would count as done.
    """
    from ..perception.observe import TaskContext

    return TaskContext(utterance=instruction, items=["green"], regions=["socket 1"])


def _live_state(runtime: Any, policy: Any, ep_dir: str) -> dict[str, Any]:
    """What the viewer shows while an episode is still running: the agent's own page.

    Decisions it published, the plan rows the arm is holding, the event tail. `eval_view`
    is not called here at all — a live scoreboard on the same wall would be an
    observation the loop never sanctioned (SPEC-v0.2 §10), and the run has to look the
    same whether or not someone is watching it.
    """
    trace = list(policy.trace)
    last = trace[-1] if trace else {}
    state: dict[str, Any] = {
        "round": len(trace),
        "skill_calls": int(getattr(getattr(runtime, "executor", None), "calls", 0)),
        "action": last.get("action"), "skill": last.get("skill"), "args": last.get("args"),
        "decisions": [f"r{t.get('round_index')} {t.get('action')} {t.get('skill') or ''} "
                      f"{json.dumps(t.get('args') or {}, ensure_ascii=False)} "
                      f"[row {t.get('row')}] -> {t.get('rationale') or ''}" for t in trace],
        "events": _tail_events(ep_dir)}
    plan = getattr(runtime, "plan", None)
    if plan is not None:
        state["plan"] = {
            "version": plan.version, "authored_by": plan.authored_by,
            "open_obligation_count": plan.open_obligation_count, "summary": plan.summary,
            "subgoals": [{"id": s.subgoal_id, "kind": s.kind,
                          "status": str(getattr(s.status, "value", s.status)),
                          "attempts": s.attempts, "statement": s.statement}
                         for s in plan.subgoals],
            "revisions": [{"trigger": r.trigger, "from": r.from_version, "to": r.to_version,
                           "rationale": (r.rationale or "")[:180]}
                          for r in plan.revisions[-3:]]}
    return state


def _tail_events(ep_dir: str, n: int = 14) -> list[str]:
    path = os.path.join(ep_dir, "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    out = []
    for ln in lines[-n:]:
        try:
            e = json.loads(ln)
            rest = {k: v for k, v in e.items() if k not in ("t", "type")}
            out.append(f"{e.get('t', '')} {e.get('type')} {json.dumps(rest, ensure_ascii=False)}"
                       [:150])
        except Exception:  # noqa: BLE001
            out.append(ln[:150])
    return out


def _versions() -> dict[str, Any]:
    import importlib.metadata as md
    import sys
    out = {"python": sys.version.split()[0], "executable": sys.executable}
    for name in ("metaworld", "mujoco", "gymnasium", "numpy", "scipy", "imageio"):
        try:
            out[name] = md.version(name)
        except Exception:  # noqa: BLE001
            out[name] = None
    return out


def _catalogue_sha() -> str:
    from .skills import catalogue_sha256
    return catalogue_sha256()


__all__ = ["run_one_episode", "OBJECT_ID", "TARGET_ID"]
