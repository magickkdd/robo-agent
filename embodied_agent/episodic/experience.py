"""SPEC-v0.2 §5.4: one episode's usable residue, assembled only from records the loop filed.

§5.4 lists seven things an experience must carry — 任务上下文 / 状态模式 / 行动序列 / 结果 /
失败原因 / 适用条件 / 经验来源 — and `core/v02.py:EpisodicExperience` (frozen at schema 4) names a
field for each. This module's whole job is the mapping, and the mapping is constrained in three
ways that are easy to violate by accident:

* **No new facts.** Every field is read out of an `episode_summary.json` and its `events.jsonl`
  — the two artifacts `evaluation/run.py` already writes for every episode. Nothing here asks the
  simulator a question, opens a scene, or calls a model. An experience that contained a claim no
  record supports would be a *second* source of world facts, which §3's separation forbids.
* **The loop's word and the key's word stay apart.** `outcome` is the loop's own terminal reading;
  `official_success` is the independent third-layer verdict. They can disagree, and that
  disagreement is the finding (P1 measured a self-reported progress count exceeding the key), so
  the two are never merged into one label.
* **Provenance is mandatory.** An experience with no `run_ref` cannot be traced back when it turns
  out to be badly labelled, so `experience_from_episode` refuses to return one — and
  `store.ExperienceStore.append` refuses to save it either.

`counter_examples` is the field most likely to be faked, so it is worth stating what fills it: a
terminal plan row whose `history` says the predicate *was* measured `done` and later measured
`False` is a counter-example this episode actually observed. That is why the field is not always
empty and never a guess.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable, Optional

from ..core.contracts import Source
from ..core.v02 import EpisodicExperience, ExperienceStep, OutcomeLabel, Status

# §5.4's 结果 is one of five labels; the loop terminates with four statuses plus a failure code.
# The collapse is written out rather than left to an `else`, and `needs_clarification` is the one
# label the frozen enum has no word for: it maps to `failure` and the distinction lives in
# `failure_reason`, where a reader can still see that the episode stopped by asking.
TRUNCATING_FAILURE_CODES = frozenset({"BUDGET_EXHAUSTED", "TIMEOUT"})
PLAN_EVENT_TYPES = ("plan", "plan_revision")


def experience_id_for(episode_id: str, run_ref: str) -> str:
    """A row's address, derived from where it came from rather than drawn at random.

    `EpisodicExperience.experience_id` has a `new_id` default, and retrieval rows cite that id. A
    random id makes the same episode produce a *new* experience every time the extractor is run, so
    an append-only store silently accumulates near-duplicates and a frozen seed file cannot be
    re-derived from its runs. sha1 over (episode, run) is stable across processes — unlike `hash()`,
    which P2 found was giving two trajectories from one payload — and two batches of the same task
    still get two rows, because they are two different pieces of evidence.
    """
    digest = hashlib.sha1(f"{episode_id}\n{run_ref}".encode("utf-8")).hexdigest()[:8]
    return f"exp_{episode_id}_{digest}"


def outcome_of(result: dict[str, Any], score: Optional[dict[str, Any]]) -> OutcomeLabel:
    status = str(result.get("terminal_status") or "")
    code = str(result.get("failure_type") or "")
    if status == "success":
        return OutcomeLabel.success
    if status == "cancelled":
        return OutcomeLabel.error
    if code in TRUNCATING_FAILURE_CODES:
        return OutcomeLabel.truncated
    done, total = (score or {}).get("objects_completed"), (score or {}).get("objects_total")
    if isinstance(done, int) and isinstance(total, int) and 0 < done < total:
        return OutcomeLabel.partial
    return OutcomeLabel.failure


def read_episode_dir(episode_dir: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The two artifacts of one episode, as written — no re-derivation, no repair."""
    with open(os.path.join(episode_dir, "episode_summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    events: list[dict[str, Any]] = []
    path = os.path.join(episode_dir, "events.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return summary, events


def _by_type(events: Iterable[dict[str, Any]], *types: str) -> list[dict[str, Any]]:
    return [e for e in events if str(e.get("type") or "") in types]


def _view_of(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    view = (event.get("payload") or {}).get("view")
    return view if isinstance(view, dict) else None


def final_plan_rows(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The last published plan view's rows — the terminal reading of the work graph.

    "Last" is by file order, which *is* the record order (`sequence` increases as the loop runs),
    not by version number: a re-filed view of the same version is still later evidence.
    """
    rows: list[dict[str, Any]] = []
    for event in _by_type(events, *PLAN_EVENT_TYPES):
        view = _view_of(event)
        if view and isinstance(view.get("rows"), list):
            rows = [r for r in view["rows"] if isinstance(r, dict)]
    return rows


def last_working_memory_status(events: Iterable[dict[str, Any]]) -> tuple[dict[str, str], str]:
    """The loop's own terminal reading of its goal predicates, and the observation it came from.

    This exists because a `plan` / `plan_revision` event is filed when the work graph *changes*,
    not every round (§9 attributes revisions to `planning`). On the P2 `full` batch five of six
    episodes filed exactly one plan event — the round-1 view, whose rows are all unmeasured — so
    the last filed view is the last *published* one rather than the terminal one. The
    `working_memory` event is filed every round and carries `subgoal_status`, so its last row is
    current — but it is a *work* status, and `Status` documents `pending` as "the work has not
    started", not as "nobody looked", so it may confirm a satisfied predicate (`done`) and may never
    refute one. What it says and which instrument said it are both recorded.
    """
    latest: dict[str, str] = {}
    ref = ""
    for event in _by_type(events, "working_memory"):
        payload = event.get("payload") or {}
        status = payload.get("subgoal_status")
        if isinstance(status, dict) and status:
            latest = {str(k): str(v) for k, v in status.items()}
            ref = str(payload.get("observation_ref") or "")
    return latest, ref


def state_pattern_of(rows: list[dict[str, Any]], score: Optional[dict[str, Any]],
                     wm_status: Optional[dict[str, str]] = None) -> tuple[list[str], str]:
    """`placed:<entity>:<region>=true|false|unknown`, one per named predicate.

    Identity of the predicates comes from the plan's filed view; the verdict comes from whichever
    instrument actually measured. The two disagree because a `plan` event is published only when the
    work graph changes, so on the P2 `full` batch five of six episodes filed the round-1 rows and
    nothing after, while the last round's `subgoal_status` is always current but is a **work** status
    — and `core/v02.py:Status` says `pending` is not `unknown`: it means the work has not finished,
    which does not tell a reader whether anyone looked. So a row the loop last verified `done` is
    `true`, a verdict the filed view measured is kept, and everything else stays `unknown` rather
    than becoming a guess. Which instruments answered is the second return value and lands in
    `provenance`.
    """
    measured: dict[str, str] = {}
    for row in rows:
        predicate = str(row.get("predicate_id") or row.get("row_key") or "")
        if predicate:
            measured[predicate] = _truth(row.get("satisfied"))
    live = {str(k): str(v) for k, v in (wm_status or {}).items()}
    if measured or live:
        pattern: list[str] = []
        for predicate in sorted(set(measured) | set(live)):
            if live.get(predicate) == Status.done.value:
                truth = "true"
            elif measured.get(predicate, "unknown") != "unknown":
                truth = measured[predicate]
            else:
                truth = "unknown"
            pattern.append(f"{predicate}={truth}")
        source = ("terminal_plan_view+working_memory" if measured and live
                  else "terminal_plan_view" if measured else "working_memory")
        return pattern, source
    details = (score or {}).get("details")
    if isinstance(details, dict) and details:
        pattern = [f"placed:{entity}:{row.get('target')}={_truth(row.get('satisfied'))}"
                   for entity, row in sorted(details.items()) if isinstance(row, dict)]
        return sorted(p for p in pattern if "None" not in p), "independent_score"
    return [], "no_record"


def _truth(value: Any) -> str:
    """`true` / `false` / `unknown` from the shapes a *measured* verdict reaches disk in.

    The same "yes" arrives as a JSON bool and as `str(bool)`, so a plain truthiness test would call
    `"False"` true. `Status` names are deliberately not understood here: `pending` and `active` are
    work states rather than measurements, and reading them as `false` is the conflation `Status`'
    own docstring forbids. Anything unrecognised is `unknown`, never `false` — an unanswered
    question is not an answer.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value or "").strip().lower()
    if text in ("true", "1"):
        return "true"
    if text in ("false", "0"):
        return "false"
    return "unknown"


def steps_of(events: Iterable[dict[str, Any]]) -> list[ExperienceStep]:
    """The action sequence, one step per *executed* feedback row.

    `effect` is the verdict the environment returned (status plus failure code), not the
    agent's expectation of it, and `observation_ref` is the post-state the verdict was read
    from — those two together are what makes a step citable instead of anecdotal.
    """
    steps: list[ExperienceStep] = []
    round_index = 0
    for event in events:
        etype = str(event.get("type") or "")
        payload = event.get("payload") or {}
        if etype == "decision":
            round_index += 1
            continue
        if etype != "execution_feedback":
            continue
        fb = payload.get("feedback") or {}
        if not fb.get("executed"):
            continue
        skill = str(fb.get("skill") or "")
        if not skill:
            continue
        args = {k: v for k, v in (("object_id", fb.get("entity_id")),
                                  ("target_id", fb.get("target_id"))) if v}
        code = fb.get("failure_code")
        status = str(fb.get("status") or "")
        steps.append(ExperienceStep(
            skill=skill, args=args, round_index=round_index,
            observation_ref=(str(fb.get("post_observation_ref") or None)),
            effect=status if not code else f"{status}:{code}"))
    return steps


def _kinds_of(assignments: Any) -> list[str]:
    out = set()
    for pair in assignments or []:
        spec = pair[0] if isinstance(pair, (list, tuple)) and pair else None
        if isinstance(spec, dict) and spec.get("shape"):
            out.add(f"{spec['shape']}:{spec.get('color', '')}")
    return sorted(out)


def _regions_of(steps: list[ExperienceStep], rows: list[dict[str, Any]]) -> list[str]:
    regions = {str(r.get("target_region_id")) for r in rows if r.get("target_region_id")}
    regions |= {str(s.args.get("target_id")) for s in steps if s.args.get("target_id")}
    return sorted(r for r in regions if r and r != "None")


def failure_reason_of(result: dict[str, Any], steps: list[ExperienceStep]) -> str:
    """Why it stopped, in the words the records already used: the terminal code, then the last step
    that did not complete (the one a later reader would try to avoid repeating).

    On a successful episode there is nothing to explain, and the mid-episode miss is already carried
    by `steps[].effect` — leaving it here too would make §5.4's 失败原因 read like a verdict on the
    run rather than the reason it ended."""
    code = str(result.get("failure_type") or "")
    status = str(result.get("terminal_status") or "")
    reason = result.get("termination_reason") or ""
    parts = [p for p in (code or ("" if status == "success" else status), str(reason or "")) if p]
    if status != "success":
        for step in reversed(steps):
            if not step.effect.startswith("completed"):
                parts.append(f"last uncompleted step: {step.skill} "
                             f"{step.args.get('object_id', '')} -> {step.args.get('target_id', '')} "
                             f"[{step.effect}] round {step.round_index}")
                break
    return "; ".join(dict.fromkeys(parts))


def experience_from_episode(summary: dict[str, Any], events: list[dict[str, Any]], *,
                            run_ref: str = "") -> EpisodicExperience:
    """The §5.4 record for one finished episode. Reads; never computes a new claim.

    The summary reaches this function in one of two shapes, and both are read, never
    normalized into a third: the desktop bench files the terminal record under `result`,
    the task-set name under `set` and the independent score under `score`
    (`evaluation/run.py`), while the MuJoCo bench files the same terminal record under
    `episode_result`, the environment name under `env_name` and its third-layer verdict
    as the top-level `official_success` flag (`benchmark_mujoco/runner.py`). Every alias
    below is `summary.get(a) or summary.get(b)` over keys the two runners already write —
    which is what keeps `write_experience`'s contract honest: the online write and an
    offline reader re-running this function over the archive call it with the same bytes,
    so a row a batch acted on cannot differ from a row somebody rebuilds afterwards. A
    shape that stopped carrying a key reads as absent, not as a default."""
    result = dict(summary.get("result") or summary.get("episode_result") or {})
    score = dict(summary.get("score") or {})
    perception = dict(summary.get("perception") or {})
    decision = dict(summary.get("decision_source") or {})
    goal = dict(summary.get("goal_resolution") or {})
    artifacts = dict(summary.get("artifacts") or {})
    set_name = str(summary.get("set") or summary.get("env_name") or "")
    # the id is read from the terminal record on both shapes and nothing else: the
    # top-level `episode_id` the desktop summary also carries is the envelope's word, and
    # letting it answer for an empty `result.episode_id` would silently repair exactly the
    # broken provenance the refusal below exists to catch.
    episode_id = str(result.get("episode_id") or "")
    steps = steps_of(events)
    rows = final_plan_rows(events)
    wm_status, wm_ref = last_working_memory_status(events)
    pattern, pattern_from = state_pattern_of(rows, score, wm_status)
    regions = _regions_of(steps, rows)
    kinds = _kinds_of(goal.get("assignments"))
    counter_examples = sorted({str(h) for r in rows for h in (r.get("history") or [])
                               if "was done" in str(h) or "is False" in str(h)})
    outcome = outcome_of(result, score)
    # where_from's third fallback is the MuJoCo shape: its summary names the episode
    # directory `run_dir` (there is no `artifacts` block to nest it in), and an offline
    # reader rebuilding rows from that archive must not need to know to pass `run_ref`.
    where_from = run_ref or str(artifacts.get("episode_dir") or "") \
        or str(summary.get("run_dir") or "")
    provenance = {
        "state_pattern_from": pattern_from,
        "state_pattern_ref": wm_ref,
        # `arm` and `policy` are filed under different keys on the two shapes: the desktop
        # nests them in `perception`/`decision_source.policy`, the MuJoCo bench carries the
        # condition at the top level and names the policy word `planner`. Neither fallback
        # exists on the other shape, so a desktop row reads exactly as it always did.
        "arm": str(perception.get("arm") or summary.get("ablation") or ""),
        "channel": str(perception.get("channel") or ""),
        "policy": str(decision.get("policy") or decision.get("planner") or ""),
        "planner": str(decision.get("provider") or ""),
        "goal_planner": str(goal.get("planner") or ""),
        "mode": str(result.get("mode") or ""),
        "expected": str(summary.get("expected") or ""),
    }
    experience = EpisodicExperience(
        experience_id=experience_id_for(episode_id, where_from),
        episode_id=episode_id,
        task_context=f"{set_name}/{result.get('task_id', '')} "
                     f"({len(pattern)} named predicate(s), {len(steps)} executed step(s))",
        task_kind=set_name,
        scene_id=str(result.get("task_id") or "") or None,
        object_kinds=kinds,
        state_pattern=pattern,
        steps=steps,
        outcome=outcome,
        # The third layer, under whichever name the channel files it: the desktop reads the
        # independent score's flag, the MuJoCo bench reads its own top-level field — the
        # benchmark's verdict in both cases, never the loop's `outcome` above.
        official_success=(bool(score.get("complete_success")) if score
                          else (bool(summary["official_success"])
                                if "official_success" in summary else None)),
        failure_reason=failure_reason_of(result, steps),
        applicability=[f"task_kind:{set_name}",
                       f"regions:{'|'.join(regions) or '(none named)'}",
                       f"kinds:{'|'.join(kinds) or '(none named)'}",
                       f"objects:{score.get('objects_total', 0)}"],
        counter_examples=counter_examples,
        transfer_caution=(
            f"one episode ({result.get('episode_id')}), arm={provenance['arm']}, "
            f"policy={provenance['policy']}, outcome={outcome.value}, "
            f"{len(counter_examples)} measured predicate flip(s); a single layout is not a rule"),
        cost={k: result.get(k) for k in
              ("decision_rounds", "skill_calls", "http_requests", "objects_completed",
               "objects_total", "sim_time_s", "wall_time_s", "identical_repeats_total",
               "rejected_decisions", "recovery_events", "retry_events", "replan_events")},
        run_ref=where_from,
        provenance=provenance,
        source=(Source.sensor if str(perception.get("channel") or "") in
                # the desktop camera words, then the MuJoCo bench's own two: `stub` reads a
                # rendered frame through segmentation and `vlm` through a vision model —
                # both sensor readings, neither a simulator pose
                ("rgb", "rgbd", "percept", "sensing", "stub", "vlm") else Source.privileged),
        based_on_state_version=score.get("scored_state_version"),
        based_on_observation_ref=score.get("scored_observation_ref"),
    )
    if not experience.run_ref:
        raise ValueError("an experience with no 经验来源 cannot be stored: it could not be "
                         "traced back if it turns out to be mislabelled (§5.4, §3 separation)")
    if not experience.episode_id:
        raise ValueError("an experience that names no episode cannot be stored: its id would "
                         "address nothing, and a retrieval row cites by that id")
    return experience
