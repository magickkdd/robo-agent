"""SPEC-BST Phase 2/3: run one slot of the frozen schedule, and freeze what it left.

This module schedules and records. It never chooses an action: the decision comes
from `AlfredTextPlanner` over the real HTTP client, the world comes from
`AlfredTextBackend`, and everything in between is `AlfredRuntime` — the frozen v0.1
loop with the seams of `compatibility_delta.md` D13 attached.

Three responsibilities live here because no existing runner covers them:

* **the run root of §10.4** — `episodes/<id>/{events,model_calls,evaluation}.jsonl`
  plus `episode_summary.json`, and the sealed headers (`manifest.json`,
  `frozen_config.json`, `task_manifest.json`, `compatibility_delta.md`,
  `environment_check.json`);
* **the resume ledger** — a completed slot is never re-run, and "completed" means a
  summary that parses and carries a termination reason, not that a directory exists
  (§6.4: an infrastructure failure may be replaced once, so its row must be re-runnable);
* **the two caps and the one guard** — batch token cap, per-episode token cap
  (already enforced inside the runtime, D17), and the model-identity check that stops
  scheduling when the endpoint starts answering as someone else (§5.5).

`evaluation.jsonl` is written *after* the loop stopped. It is the only place the
official verdict is stored, and no object the model can read is built from it.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from ..adapters.deepseek import DeepSeekPlanner
from ..core.contracts import TaskInput, VerifyConfig
from ..core.events import EpisodeStore, git_state, write_manifest
from ..evaluation.sources import LLMDecisionSource
from .backend import AlfredTextBackend, BackendError
from .native_actions import catalogue_sha256
from .parser import parse
from .prompts import prompts_sha256
from .runtime_alfred import (BST_COMMAND_TIMEOUT_S, BST_DECISION_ROUNDS, BST_ENV_ACTIONS,
                             BST_EPISODE_TOKENS, BST_EPISODE_WALL_S, BST_HTTP_REQUESTS,
                             BST_HTTP_TIMEOUT_S, AlfredRuntime, bst_budgets)
from .source import MAX_TOKENS_PER_REQUEST, AlfredTextPlanner
from .state import goal_from_instruction

BST_SPEC = "BST-1.0"
# SPEC-BST 5.3: the two batch-level token ceilings, by segment size.
SCREENING_TOKEN_CAP = 8_000_000
FULL_TOKEN_CAP = 40_000_000
REPLACEMENTS_PER_SLOT = 1


# ------------------------------------------------------------- model config ----


def load_model_config(path: str) -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def planner_from_config(path: str) -> tuple[AlfredTextPlanner, dict]:
    """The benchmark decision source over the endpoint a config names.

    `DeepSeekPlanner.from_env` is used for key resolution rather than copied: it is
    the code that turns `api_key_env` into a key without ever writing the value
    down. The returned desktop planner is discarded — its prompts, goal parse and
    plan method have no part here; only its adapter, which owns the HTTP client and
    the counters, is kept."""
    cfg = load_model_config(path)
    desktop = DeepSeekPlanner.from_env(path)
    planner = AlfredTextPlanner(
        desktop.adapter,
        decision_prompt=f"bst-decide-text-v1",
        feedback_depth=int(cfg.get("feedback_depth", 3)))
    identity = {"provider": desktop.adapter.provider, "model": desktop.adapter.model,
                "base_url_host": desktop.adapter.base_url.split("//")[-1].split("/")[0],
                "temperature": desktop.adapter.temperature,
                "max_tokens": planner.max_tokens,
                "max_tokens_cap": MAX_TOKENS_PER_REQUEST,
                "timeout_s": desktop.adapter.timeout_s,
                "max_retries": desktop.adapter.max_retries,
                "pricing_usd_per_mtok": desktop.adapter.pricing or None,
                "config_path": os.path.abspath(path),
                "config_sha256": _sha_file(os.path.abspath(path)),
                "decision_prompt": planner.decision_prompt,
                "prompts_sha256": planner.prompts_sha256,
                "feedback_depth": planner.feedback_depth,
                "api_key_recorded": False}
    return planner, identity


# ------------------------------------------------------------------ one slot ---


def episode_id_for(slot: dict) -> str:
    """Readable, unique, and reversible to its task: the slot index pins the position
    in the frozen order, the tail digest pins which game, without a path separator.

    A replacement attempt (§6.4) gets its own id: the failed slot's artifacts are the
    evidence that the infrastructure error happened, and appending a second episode
    into the same `events.jsonl` would destroy the audit trail it is there to keep."""
    tail = slot["task_id"].replace("/", "__")
    suffix = ".retry1" if slot.get("_replacement_of") is not None else ""
    return (f"slot{slot['slot_index']:03d}_{slot['task_type']}_r{slot['repeat']}"
            f"_{tail[-40:]}{suffix}")


def run_slot(slot: dict, *, planner: AlfredTextPlanner, run_root: str, data_dir: str,
             budgets=None, max_episode_tokens: int = BST_EPISODE_TOKENS,
             quiet: bool = False) -> dict:
    """One (task, repeat) slot, end to end, artifacts sealed on every exit path."""
    episode_id = episode_id_for(slot)
    ep_dir = os.path.join(run_root, "episodes", episode_id)
    _isolate_prior_attempt(run_root, ep_dir)
    os.makedirs(ep_dir, exist_ok=True)
    budgets = budgets or bst_budgets()
    started = time.time()
    backend = AlfredTextBackend(slot["gamefile"], max_env_actions=int(budgets.max_skill_calls),
                                command_timeout_s=BST_COMMAND_TIMEOUT_S, data_dir=data_dir)
    calls_path = os.path.join(ep_dir, "model_calls.jsonl")

    def log_call(payload, latency_s):
        _append_jsonl(calls_path, {**payload, "latency_s": round(latency_s, 3),
                                   "episode_id": episode_id, "task_id": slot["task_id"],
                                   "repeat": slot["repeat"], "recorded_at": time.time()})

    store = EpisodeStore(ep_dir, episode_id)
    summary: dict[str, Any]
    where = "backend_start"
    # the provider's counters are cumulative over the process, so an episode's own
    # spend — on the success path and on the error path — is a delta against this
    before = (int(planner.prompt_tokens), int(planner.completion_tokens),
              int(planner.http_requests), len(planner.returned_models))
    try:
        raw = backend.start()
        where = "task_instruction"
        instruction = parse(raw).instruction
        if not instruction:
            # the printed instruction is this backend's only goal channel (§3.4); an
            # initial observation without one is an environment fault, not a failure
            # to solve a task
            raise BackendError(f"no task instruction in the initial observation: {raw[:200]!r}")
        goal = goal_from_instruction(instruction, state_version=0)
        task = TaskInput(task_id=slot["task_id"], utterance=instruction, language="en",
                         declared_constraints=[])
        runtime = AlfredRuntime(backend, ep_dir, budgets, episode_id,
                                config=VerifyConfig(), store=store,
                                max_episode_tokens=max_episode_tokens)
        source = LLMDecisionSource(planner, log=log_call)
        where = "episode"
        result = runtime.run_episode(task, goal, source, mode="BST")
        official = backend.eval_view()
        evaluation = {
            "episode_id": episode_id, "task_id": slot["task_id"], "repeat": slot["repeat"],
            "task_type": slot["task_type"], "run_seed": slot["run_seed"],
            "game_tw_pddl_sha256": slot["game_tw_pddl_sha256"],
            "official_won": bool(official.get("official_won")),
            "env_done": bool(official.get("env_done")),
            "env_steps": int(official.get("env_steps")),
            "max_env_actions": int(official.get("max_env_actions")),
            "reward": official.get("reward"),
            "score_complete_success": bool(official.get("official_won"))
            and bool(official.get("env_done")),
            "termination_reason": result.termination_reason,
            "false_finish": (result.termination_reason == "AGENT_FINISH"
                             and not bool(official.get("official_won"))),
            "backend_error": official.get("error"),
            "written_at": time.time(),
            "note": "read from the environment's own lifecycle fields after the loop "
                    "stopped; no decision context was built from this row (SPEC-BST 4.5, 6.1)"}
        _write_json(os.path.join(ep_dir, "evaluation.jsonl"), [evaluation], jsonl=True)
        summary = {
            "bst_spec": BST_SPEC, "episode_id": episode_id,
            "slot_index": slot["slot_index"], "task_id": slot["task_id"],
            "task_type": slot["task_type"], "type_id": slot["type_id"],
            "repeat": slot["repeat"], "run_seed": slot["run_seed"],
            "gamefile": os.path.relpath(slot["gamefile"], data_dir),
            "game_tw_pddl_sha256": slot["game_tw_pddl_sha256"],
            "instruction": instruction,
            "instruction_sha256": _sha_text(instruction),
            "reset_seconds": backend.reset_seconds,
            "result": result.model_dump(mode="json"),
            "evaluation": evaluation,
            "model_identity": {"provider": planner.provider, "model": planner.model,
                               "returned_models": sorted(set(
                                   planner.returned_models[before[3]:])),
                               "decision_prompt": planner.decision_prompt,
                               "prompts_sha256": planner.prompts_sha256},
            # the episode's own ledger, not the adapter's cumulative counters, which
            # would charge every earlier episode again to this batch's cap.
            "tokens": {"prompt": int(result.prompt_tokens),
                       "completion": int(result.completion_tokens),
                       "total": int(result.prompt_tokens) + int(result.completion_tokens),
                       "http_requests": int(result.http_requests),
                       "decision_rounds": int(result.decision_rounds),
                       "api_cost_estimate": result.api_cost_estimate,
                       "provider_cumulative_total": planner.tokens_used()},
            "artifacts": {"episode_dir": ep_dir, "events": store.path,
                          "model_calls": calls_path if os.path.exists(calls_path) else None,
                          "evaluation": os.path.join(ep_dir, "evaluation.jsonl")},
            "wall_time_s": round(time.time() - started, 2),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    except BackendError as e:
        # an environment that will not start, or will not state a task, is
        # infrastructure: not a model failure and not a task outcome. The slot stays
        # re-runnable, once (§6.4), because no `termination_reason` is recorded.
        summary = _infra_summary(slot, episode_id, where, e, backend, started, planner, before)
        _write_json(os.path.join(ep_dir, "evaluation.jsonl"),
                    [{**summary, "official_won": None, "scoreable": False,
                      "note": "an infrastructure error row, not an episode verdict: no "
                              "decision was scored from it (SPEC-BST 6.4)"}], jsonl=True)
    except Exception as e:  # noqa: BLE001 - a code fault is recorded, then allowed to stop the batch
        # §6 records this as an infrastructure error so the artifacts of the slot that
        # hit it survive, but it is not replaced and not skipped: a fault in this
        # process will recur on every remaining slot, and continuing would spend the
        # token cap to produce 268 copies of one traceback.
        summary = _infra_summary(slot, episode_id, where, e, backend, started, planner, before)
        _write_json(os.path.join(ep_dir, "episode_summary.json"), summary)
        raise
    finally:
        backend.close()

    _write_json(os.path.join(ep_dir, "episode_summary.json"), summary)
    if not quiet:
        e = summary.get("evaluation") or {}
        print(f"[{summary.get('slot_index')}] {summary.get('task_type')} r{summary.get('repeat')} "
              f"-> {e.get('termination_reason') or summary.get('outcome')} "
              f"official_won={e.get('official_won')} env_steps={e.get('env_steps')} "
              f"rounds={(summary.get('result') or {}).get('decision_rounds')} "
              f"tokens={summary.get('tokens', {}).get('total')} "
              f"{summary.get('wall_time_s')}s", flush=True)
    return summary


def _infra_summary(slot: dict, episode_id: str, where: str, error: Exception,
                   backend: AlfredTextBackend, started: float,
                   planner: AlfredTextPlanner, before: tuple[int, int, int, int]) -> dict:
    """The row an episode that never ran leaves behind: enough to identify the slot
    and reproduce the fault, and nothing that could be read as a task outcome.

    What was spent is the delta of the provider's counters across this call, not zero:
    a slot that died mid-episode really did consume requests, and the batch cap has to
    be charged for them. A slot that died at reset legitimately measures 0."""
    prompt = int(planner.prompt_tokens) - before[0]
    completion = int(planner.completion_tokens) - before[1]
    requests = int(planner.http_requests) - before[2]
    return {"bst_spec": BST_SPEC, "episode_id": episode_id,
            "slot_index": slot["slot_index"], "task_id": slot["task_id"],
            "task_type": slot["task_type"], "type_id": slot.get("type_id"),
            "repeat": slot["repeat"], "run_seed": slot["run_seed"],
            "gamefile": slot["gamefile"],
            "game_tw_pddl_sha256": slot.get("game_tw_pddl_sha256"),
            "replacement_of": slot.get("_replacement_of"),
            "outcome": "infrastructure_error",
            "infrastructure_error": {"where": where,
                                     "error": f"{type(error).__name__}: {error}"[:1000],
                                     "backend_error": backend.error,
                                     "env_steps": int(backend.env_steps),
                                     "returned_models": sorted(set(
                                         planner.returned_models[before[3]:])),
                                     "traceback_tail": traceback.format_exc()[-1500:]},
            "tokens": {"prompt": prompt, "completion": completion,
                       "total": prompt + completion, "http_requests": requests},
            "wall_time_s": round(time.time() - started, 2),
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


# ----------------------------------------------------------------- the batch ---


class BatchHalt(Exception):
    """Scheduling stopped for a reason that must reach the operator: a cap, or the
    model identity guard. Neither is an episode outcome."""


def completed_slot_index(progress: dict) -> set[int]:
    return {int(r["slot_index"]) for r in progress.get("slots", [])
            if r.get("resumable_complete")}


SLOT_IDENTITY_FIELDS = ("task_id", "task_type", "repeat", "run_seed")


def slot_identity_mismatches(slots: list[dict], progress: dict) -> list[dict]:
    """Where a carried ledger row and the plan disagree about what that slot *is*.

    A resume matches them by `slot_index` alone, because that is the name the episode's
    artifacts are stored under. The task manifest numbers its screening block and its
    remainder block from zero separately, so the two collide on 48 indices: matched
    blindly, the first 48 never-run slots would read as already complete and borrow the
    screening episodes' verdicts. Renumbering the plan is what makes the match sound;
    this is the check that it stayed sound, run before anything touches the environment.
    """
    carried: dict[int, list[dict]] = defaultdict(list)
    for r in progress.get("slots") or []:
        if r.get("resumable_complete") and r.get("slot_index") is not None:
            carried[int(r["slot_index"])].append(r)
    out: list[dict] = []
    for slot in slots:
        rows = carried.get(int(slot["slot_index"]))
        if not rows:
            continue
        if not any(all(r.get(k) == slot.get(k) for k in SLOT_IDENTITY_FIELDS) for r in rows):
            out.append({"slot_index": int(slot["slot_index"]),
                        "planned": {k: slot.get(k) for k in SLOT_IDENTITY_FIELDS},
                        "ledger": [{k: r.get(k) for k in SLOT_IDENTITY_FIELDS} for r in rows]})
    return out


def identity_drift(summary: dict, configured_model: str) -> list[str]:
    """The names this episode's responses actually carried that are not the one asked
    for. Empty means the respondent matched the configuration for the whole episode.

    A missing name is not a drift: an episode that made no successful request records
    no returned model, and inventing a match for it would let the guard pass on
    silence. That case is visible in the episode's own `model_calls.jsonl`."""
    return sorted({m for m in ((summary.get("model_identity") or {}).get("returned_models")
                               or []) if m and m != configured_model})


def run_batch(slots: list[dict], *, planner: AlfredTextPlanner, identity: dict,
              run_root: str, data_dir: str, segment: str, token_cap: int,
              budgets=None, max_episode_tokens: int = BST_EPISODE_TOKENS,
              resume: bool = True, quiet: bool = False) -> dict:
    """The frozen order, one process, one slot at a time (SPEC-BST 5.5: 并发 1)."""
    os.makedirs(run_root, exist_ok=True)
    progress_path = os.path.join(run_root, "progress.json")
    progress = _read_json(progress_path) if os.path.exists(progress_path) else {
        "bst_spec": BST_SPEC, "segment": segment, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "token_cap": token_cap, "slots": [], "halted": None}
    progress.setdefault("token_cap", token_cap)
    carried = [r for r in progress.get("slots") or [] if r.get("resumable_complete")]
    carried_tokens = sum(int((r.get("tokens") or {}).get("total") or 0) for r in carried)
    if progress["segment"] != segment or progress["token_cap"] != token_cap:
        # the 48 screening slots are part of the 268 (SPEC-BST 4.3), so a full-segment
        # run legitimately inherits a screening ledger. What it must not inherit is the
        # screening *cap*: 8 M belongs to the smaller segment and would stop the larger
        # one early. The handover is written down instead of being implied by the fact
        # that the numbers happen to line up.
        progress.setdefault("inherited_ledgers", []).append({
            "from_segment": progress["segment"], "from_token_cap": progress["token_cap"],
            "to_segment": segment, "to_token_cap": token_cap,
            "slots_carried": len(carried), "tokens_carried": carried_tokens,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "note": "same config, same code, same slot plan: the carried slots are the "
                    "screening episodes counted into the 268, not re-run and not "
                    "re-picked (SPEC-BST 4.3, 12)"})
    progress["segment"] = segment
    progress["token_cap"] = token_cap
    done = completed_slot_index(progress) if resume else set()
    tokens_so_far = sum(int((r.get("tokens") or {}).get("total") or 0) for r in progress["slots"])
    # SPEC-BST 6.4: at most one replacement per slot. A slot whose replacement also
    # failed stays *open* — recorded, counted, and never run a third time — rather
    # than becoming an endless supply of fresh attempts on every resume.
    spent = {int(r["slot_index"]) for r in progress["slots"]
             if r.get("replacement_of") is not None
             and r.get("outcome") == "infrastructure_error"}
    replacements = {r["slot_index"] for r in progress["slots"] if r.get("replacement_of")}
    halted: Optional[dict] = None

    mismatched = slot_identity_mismatches(slots, progress)
    if mismatched:
        # Nothing has been run yet and nothing will be: continuing would overwrite
        # another slot's identity onto an episode, which no later analysis could undo.
        halted = {"reason": "SLOT_IDENTITY_MISMATCH", "count": len(mismatched),
                  "slots": mismatched[:10], "at_slot": mismatched[0]["slot_index"],
                  "note": "the ledger and the slot plan disagree about which task a "
                          "slot_index refers to; check that the plan was numbered over "
                          "the whole segment before any slot is skipped as complete"}
        progress["halted"] = halted
        _write_json(progress_path, progress)
        raise BatchHalt(json.dumps(halted, ensure_ascii=False))

    for slot in slots:
        if slot["slot_index"] in done:
            if not quiet:
                print(f"[{slot['slot_index']}] already complete, not re-run", flush=True)
            continue
        if slot["slot_index"] in spent:
            if not quiet:
                print(f"[{slot['slot_index']}] both attempts were infrastructure errors; "
                      f"the slot is left open, not re-run", flush=True)
            continue
        if tokens_so_far >= token_cap:
            halted = {"reason": "TOKEN_CAP", "at_slot": slot["slot_index"],
                      "tokens_used": tokens_so_far, "cap": token_cap,
                      "note": "the cap is checked against the actual billed total before a "
                              "slot starts, so the overage it reports is real, not estimated"}
            break
        summary = run_slot(slot, planner=planner, run_root=run_root, data_dir=data_dir,
                           budgets=budgets, max_episode_tokens=max_episode_tokens, quiet=quiet)
        if summary.get("outcome") == "infrastructure_error" and slot["slot_index"] not in replacements:
            # one replacement for an infrastructure failure, recorded as a replacement
            # rather than as a fresh sample (§6.4). A second failure leaves the slot open.
            retry = dict(slot, _replacement_of=slot["slot_index"])
            summary = run_slot(retry, planner=planner, run_root=run_root, data_dir=data_dir,
                               budgets=budgets, max_episode_tokens=max_episode_tokens,
                               quiet=quiet)
            summary["replacement_of"] = slot["slot_index"]
        summary["resumable_complete"] = bool((summary.get("evaluation") or {}).get("termination_reason"))
        row = {k: summary.get(k) for k in ("slot_index", "task_id", "task_type", "repeat",
                                           "run_seed", "episode_id", "resumable_complete",
                                           "outcome", "replacement_of")}
        row["tokens"] = summary.get("tokens") or {}
        row["termination_reason"] = ((summary.get("evaluation") or {})
                                     .get("termination_reason"))
        row["official_won"] = (summary.get("evaluation") or {}).get("official_won")
        progress["slots"].append(row)
        progress["slots"].sort(key=lambda r: r["slot_index"])
        tokens_so_far += int(row["tokens"].get("total") or 0)
        progress["tokens_used"] = tokens_so_far
        progress["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        offenders = identity_drift(summary, identity["model"])
        if offenders:
            # §5.5: an unexpected or mid-batch change of the returned model identity
            # pauses scheduling. The episodes already written stay; nothing is rescored.
            halted = {"reason": "MODEL_IDENTITY_DRIFT", "at_slot": slot["slot_index"],
                      "configured_model": identity["model"], "returned_models": offenders,
                      "note": "scheduling paused at the next boundary; the batch is not "
                              "continued under a different respondent"}
        _write_json(progress_path, progress)
        if halted:
            break

    progress["halted"] = halted
    progress["tokens_used"] = tokens_so_far
    progress["open_slot_index"] = sorted({int(r["slot_index"]) for r in progress["slots"]}
                                         - completed_slot_index(progress))
    progress["not_attempted_slot_index"] = sorted(
        {int(s["slot_index"]) for s in slots}
        - {int(r["slot_index"]) for r in progress["slots"]})
    progress["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _write_json(progress_path, progress)
    if halted:
        raise BatchHalt(json.dumps(halted, ensure_ascii=False))
    return progress


# ------------------------------------------------------------- run headers -----


def freeze_run_root(run_root: str, *, identity: dict, task_manifest_path: str,
                    environment_check_path: str, compatibility_delta_path: str,
                    segment: str, slots: list[dict], budgets=None,
                    extra: dict | None = None) -> dict:
    """Write the sealed headers §10.4 lists, and copy the three inputs that must be
    readable from inside the run root without reaching back into the repository."""
    os.makedirs(run_root, exist_ok=True)
    budgets = budgets or bst_budgets()
    manifest_path = write_manifest(
        run_root,
        bst_spec=BST_SPEC, segment=segment,
        frozen_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        code=git_state(),
        rules_sha256=_live_rules_sha256(),
        catalogue_sha256=catalogue_sha256(),
        prompts_sha256=prompts_sha256(),
        model=identity,
        budgets=budgets.model_dump(mode="json"),
        wall_clock_limits={"http_timeout_s": BST_HTTP_TIMEOUT_S,
                           "command_timeout_s": BST_COMMAND_TIMEOUT_S,
                           "episode_wall_s": BST_EPISODE_WALL_S},
        schedule={"n_slots": len(slots),
                  "first_order_key": slots[0]["order_key"] if slots else None,
                  "last_order_key": slots[-1]["order_key"] if slots else None,
                  "selection_seed": _manifest_field(task_manifest_path, "selection_seed"),
                  "order_seed": _manifest_field(task_manifest_path, "order_seed"),
                  "run_seeds": _manifest_field(task_manifest_path, "run_seeds"),
                  "repeats": _manifest_field(task_manifest_path, "repeats"),
                  "order_rule": _manifest_field(task_manifest_path, "path", ["order", "rule"])},
        inputs={"task_manifest_sha256": _sha_file(task_manifest_path),
                "environment_check_sha256": _sha_file(environment_check_path),
                "compatibility_delta_sha256": _sha_file(compatibility_delta_path)},
        credentials="not recorded (SPEC 7: no credentials or full env)",
        **(extra or {}))
    frozen = {
        "bst_spec": BST_SPEC, "segment": segment,
        "budgets": budgets.model_dump(mode="json"),
        "token_caps": {"per_episode": BST_EPISODE_TOKENS, "screening": SCREENING_TOKEN_CAP,
                       "full": FULL_TOKEN_CAP},
        "model": identity,
        "catalogue_sha256": catalogue_sha256(), "prompts_sha256": prompts_sha256(),
        "rules_sha256": _live_rules_sha256(),
        "note": "what this file pins is what a rerun must reproduce; a difference in any "
                "of these is a different experiment, not a longer run of the same one"}
    _write_json(os.path.join(run_root, "frozen_config.json"), frozen)
    _copy_if_present(task_manifest_path, os.path.join(run_root, "task_manifest.json"))
    _copy_if_present(environment_check_path, os.path.join(run_root, "environment_check.json"))
    _copy_if_present(compatibility_delta_path, os.path.join(run_root, "compatibility_delta.md"))
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def slot_summary_counts(progress: dict) -> dict:
    rows = progress.get("slots", [])
    return {"slots_recorded": len(rows),
            "complete": sum(1 for r in rows if r.get("resumable_complete")),
            "infrastructure": sum(1 for r in rows if r.get("outcome") == "infrastructure_error"),
            "official_won": sum(1 for r in rows if r.get("official_won")),
            "tokens_used": progress.get("tokens_used")}


# ------------------------------------------------------------------- plumbing --


def _isolate_prior_attempt(run_root: str, ep_dir: str) -> None:
    """Move a finished attempt's directory aside before a second attempt takes its name.

    `events.jsonl` is appended to, so a re-run into the same directory would write a
    single stream containing two episodes — and a reader could not tell that the file
    it is aggregating holds two runs. The old attempt is kept (deleting a measurement
    is not how an overage gets reported) but kept *outside* `episodes/`, so one
    directory under `episodes/` means one attempt, always."""
    prior = os.path.join(ep_dir, "events.jsonl")
    if not (os.path.isfile(prior) and os.path.getsize(prior)):
        return
    superseded = os.path.join(run_root, "superseded")
    os.makedirs(superseded, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(superseded, f"{os.path.basename(ep_dir)}.attempt{n}")):
        n += 1
    shutil.move(ep_dir, os.path.join(superseded, f"{os.path.basename(ep_dir)}.attempt{n}"))


def _append_jsonl(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _write_json(path: str, content, jsonl: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if jsonl:
            for row in content:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        else:
            json.dump(content, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _copy_if_present(src: str, dst: str) -> None:
    if src and os.path.isfile(src):
        shutil.copyfile(src, dst)


def _sha_file(path: str) -> Optional[str]:
    if not path or not os.path.isfile(path):
        return None
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_text(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _manifest_field(path: str, key: str, chain=None):
    try:
        doc = _read_json(path)
    except Exception:  # noqa: BLE001 - a missing draft manifest is recorded as null
        return None
    if chain:
        for part in chain:
            if not isinstance(doc, dict):
                return None
            doc = doc.get(part)
        return doc
    return doc.get(key)


def _live_rules_sha256() -> Optional[str]:
    """The v0.1 decision-mechanism hash, read from the code that computes it.

    Recorded so a text batch can always be shown to have run the frozen loop: if this
    ever differs from `configs/experiment/p3_preregistration_v1.json`, the claim
    "v0.1 决策机制经环境适配后的表现" is false and the batch must not be graded."""
    try:
        from ..evaluation.preregistration import preregistration
        return preregistration()["rules_sha256"]
    except Exception:  # noqa: BLE001 - a missing baseline is reported, never guessed
        return None
