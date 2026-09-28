"""Build the evidence a failure label has to stand on, and refuse labels without it.

SPEC-BST §9.1–§9.3. Two halves, deliberately kept apart:

* `build_worklist` reads only sealed artifacts and produces one row per *deviation*
  (not per episode), each already carrying the §9.3 minimum evidence fields with the
  labels left empty. It is the thing that makes "go and look at the failures" a
  command rather than a project.
* `validate_rows` is the gate on the other side: a row without an evidence reference,
  without a competing explanation, or with a label outside the frozen taxonomy is not
  an annotation. Nothing here assigns a label — a model cannot blind-review itself,
  and an auto-labeled taxonomy would be exactly the self-scoring §9 forbids.

Selection follows the pre-registered rule in `docs/bst/annotation_guidelines.md`
§4, and the rule itself is written into the worklist header so a reader can check
that the sample was chosen before it was read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from collections import Counter, defaultdict
from typing import Any

TAXONOMY = (
    "TASK_UNDERSTANDING", "PLANNING_ORDER", "DEPENDENCY", "STATE_USAGE",
    "HISTORY_UNAVAILABLE", "STATE_AMBIGUOUS", "REPEATED_FAILURE", "INVALID_ACTION",
    "PROGRESS_LOSS", "ACTION_COVERAGE_GAP", "SKILL_LIMITATION", "OBSERVATION_LIMITATION",
    "EXECUTION_FAILURE", "BUDGET_LIMIT", "ADAPTER_ERROR", "INFRASTRUCTURE", "UNRESOLVED",
)
CONFIDENCE = ("confirmed", "supported", "uncertain")
# a deviation is a round whose own feedback reported a problem. a round the engine
# answered "Nothing happens." is `completed` and still counts: a null answer is new
# information about the attempt, and it is the case the repeat guard exists for.
DEVIATING_STATUSES = ("rejected", "failed", "model_error")
# SPEC-BST 8.3: three consecutive identical commands that the public feedback said did
# nothing is the pre-declared loop threshold. It is a threshold for *looking*, not a
# verdict, and §10.3 requires the cases that reach it to be examined.
LOOP_THRESHOLD = 3

GUIDELINES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "docs", "bst", "annotation_guidelines.md")
SELECTION_SEED = 20260919
SELECTION_RULE = (
    "screening (SPEC-BST 10.3): every benchmark-valid episode without an official win, "
    "plus one winning episode per task type by SHA-256(seed|task_id) order when a win "
    "exists; "
    "full: the same forced inclusions (every false_finish, every episode with a model "
    "error, every episode whose longest stuck repeat reaches the 8.3 loop threshold of "
    "3, one trace for every failing task_id, and both sides of any task whose two "
    "repeats disagree) on top of a stratified draw of min(8, failures) per task type. "
    "Seed 20260919 throughout. Denominator is always episodes, never label rows, and "
    "coverage of the sample is reported apart from the labels it carries.")


# --------------------------------------------------------------- artifacts ------

def _read_jsonl(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def episode_artifacts(ep_dir: str) -> dict[str, Any]:
    events = _read_jsonl(os.path.join(ep_dir, "events.jsonl"))
    summary = {}
    sp = os.path.join(ep_dir, "episode_summary.json")
    if os.path.isfile(sp):
        with open(sp, encoding="utf-8") as f:
            summary = json.load(f)
    return {"dir": ep_dir, "events": events, "summary": summary,
            "evaluation": (summary.get("evaluation") or {})}


def _commands_by_context(events: list[dict]) -> dict[str, str]:
    """context_id -> the native command that round's call rendered into. `render` is
    the same public mapping the executor used, so no extra trust is required."""
    from .native_actions import render
    out = {}
    for e in events:
        if e["type"] != "skill_call":
            continue
        call = e["payload"].get("call") or {}
        cid = call.get("context_id") or e["payload"].get("context_id")
        try:
            cmd = render(call.get("skill"), dict(call.get("args") or {}))[0]
        except Exception:  # noqa: BLE001 - an unrenderable call is itself the evidence
            cmd = f"<unrenderable: {call.get('skill')} {call.get('args')}>"
        if cid:
            out[cid] = cmd
    return out


def _rounds_by_context(events: list[dict]) -> dict[str, int]:
    """context_id -> round_index. The number lives on the `decision_context` event, not
    on the decision the model returned, so reading it from the decision payload would
    silently produce `null` in every row."""
    return {e["payload"].get("context_id"): e["payload"].get("round_index")
            for e in events if e["type"] == "decision_context"
            and e["payload"].get("context_id")}


def deviations(arts: dict) -> list[dict]:
    """Every round whose feedback reported a problem, in order, with its evidence."""
    events = arts["events"]
    cmds = _commands_by_context(events)
    rounds = _rounds_by_context(events)
    decisions = {d.get("context_id"): d
                 for e in events if e["type"] == "decision"
                 for d in [e["payload"]] if d.get("context_id")}
    rows = []
    for e in events:
        if e["type"] != "execution_feedback":
            continue
        fb = (e["payload"].get("feedback") or {})
        status = fb.get("status")
        text = str(fb.get("environment_text") or "")
        null_answer = status == "completed" and "Nothing happens" in text
        if status not in DEVIATING_STATUSES and not null_answer:
            continue
        cid = fb.get("context_id")
        dec = decisions.get(cid) or {}
        execute = dec.get("execute") or {}
        rows.append({
            "context_id": cid,
            "decision_id": dec.get("decision_id"),
            "round_index": rounds.get(cid, fb.get("pre_state_version")),
            "decided_action": dec.get("action"),
            "skill": execute.get("skill"),
            "args": execute.get("args"),
            "command_sent": cmds.get(cid) or "none",
            "environment_response": text or None,
            "status": status,
            "rejection_reasons": list(fb.get("rejection_reasons") or []),
            "observed_before": {"held": fb.get("held_object_before"),
                                "state_version": fb.get("pre_state_version")},
            "observed_after": {"held": fb.get("held_object_after"),
                               "state_version": fb.get("post_state_version")},
            "evidence_refs": list(dec.get("evidence_refs") or []),
            "model_rationale": dec.get("rationale"),
            "is_first": not rows})
    return rows


def _last_decision(events: list[dict], cmds: dict[str, str],
                   rounds: dict[str, int]) -> dict | None:
    """The decision that ended the episode. When no command ever failed, this is the
    evidence: a clarify/blocked stop is a behaviour with a reason attached, and
    §9.1 asks for the reason of the *failure*, not only of the failed rounds."""
    decs = [e["payload"] for e in events if e["type"] == "decision"]
    if not decs:
        return None
    d = decs[-1]
    ex = d.get("execute") or {}
    return {"context_id": d.get("context_id"), "decision_id": d.get("decision_id"),
            "round_index": rounds.get(d.get("context_id")),
            "action": d.get("action"),
            "skill": ex.get("skill"), "args": ex.get("args"),
            "command_sent": cmds.get(d.get("context_id")) or "none",
            "missing_information": d.get("missing_information"),
            "expected_effect": d.get("expected_effect"),
            "evidence_refs": list(d.get("evidence_refs") or []),
            "model_rationale": d.get("rationale")}


def _termination(events: list[dict]) -> dict:
    for e in reversed(events):
        if e["type"] == "termination":
            return {k: v for k, v in (e["payload"] or {}).items()
                    if k in ("reason", "terminal_status", "failure_code", "note")}
    return {}


def episode_row(arts: dict, cap: int = 4, segment: str | None = None,
                markers: dict | None = None) -> dict | None:
    """One candidate episode: outcome facts plus the evidence rows to be judged.

    Emitted for every benchmark-valid episode, won or not, with or without a failed
    command. An episode that ended in `clarify` after three rounds that each worked is
    still a failure that needs a label, and dropping it because nothing reported an
    error is how a batch ends up annotating only the failures that were loud."""
    s, ev = arts["summary"], arts["evaluation"]
    # the same validity test the aggregator uses: a slot with no `termination_reason`
    # is an infrastructure row, and no decision — including no label — is scored from it
    if not s or ev.get("scoreable") is False or not ev.get("termination_reason"):
        return None
    events = arts["events"]
    cmds = _commands_by_context(events)
    rounds = _rounds_by_context(events)
    devs = deviations(arts)
    last = _last_decision(events, cmds, rounds)
    return {
        "episode_id": s.get("episode_id"), "task_id": s.get("task_id"),
        "task_type": s.get("task_type"), "repeat": s.get("repeat"),
        "slot_index": s.get("slot_index"), "segment": segment,
        "instruction_verbatim": s.get("instruction"),
        "instruction_sha256": s.get("instruction_sha256"),
        "outcome": {"benchmark_valid": True,
                    "official_won": ev.get("official_won"),
                    "env_done": ev.get("env_done"),
                    "env_steps": ev.get("env_steps"),
                    "termination_reason": ev.get("termination_reason"),
                    "false_finish": ev.get("false_finish"),
                    "decision_rounds": (s.get("result") or {}).get("decision_rounds"),
                    "semantic_repairs": (s.get("result") or {}).get("semantic_repairs"),
                    "model_errors": (s.get("result") or {}).get("model_errors"),
                    "http_requests": (s.get("tokens") or {}).get("http_requests"),
                    "tokens_total": (s.get("tokens") or {}).get("total")},
        "deviations": devs[:cap],
        "deviations_total": len(devs),
        "final_decision": last,
        "termination": _termination(events),
        "evidence_basis": "deviations" if devs else "stop_without_failed_command",
        "observed_from": {"events": len(events),
                          "observation_refs": sum(1 for e in events
                                                  if e["type"] == "observation")},
        "run_markers": {k: (markers or {}).get(k) for k in
                        ("longest_stuck_repeat", "schema_invalid", "semantic_repairs",
                         "refusal_reasons")} if markers else {},
        "labels": [],
        "note": "labels are assigned by a human reading this evidence against "
                "docs/bst/annotation_guidelines.md; this file carries none of them"}


# ------------------------------------------------------------- the sampling -----

def _episode_dirs(run_root: str) -> list[str]:
    root = os.path.join(run_root, "episodes")
    return sorted(os.path.join(root, d) for d in os.listdir(root)) if os.path.isdir(root) else []


def _task_order_key(task_id: str, seed: int = SELECTION_SEED) -> str:
    """SHA-256(seed|task_id), the same ordering key §4.2 uses to pick and interleave
    tasks. Reusing it here means a success is chosen by the task's place in a list that
    was fixed before the batch ran, not by which one looks interesting."""
    return hashlib.sha256(f"{seed}|{task_id}".encode()).hexdigest()


def _markers(run_root: str) -> dict[str, dict]:
    """episode_id -> the automatic markers `aggregate` already published. Read from the
    aggregator's own output rather than recomputed here: the loop threshold is defined
    once (SPEC-BST 8.3), and a second implementation of it would be a second opinion
    about what counts as a repeat."""
    out = {}
    for row in _read_jsonl(os.path.join(run_root, "failure_candidates.jsonl")):
        if row.get("episode_id"):
            out[row["episode_id"]] = row.get("automatic_markers") or {}
    return out


def select(rows: list[dict], segment: str, seed: int = SELECTION_SEED) -> dict:
    """The pre-registered rule of §10.3, applied to every benchmark-valid episode.

    Returns the chosen rows plus what the choice was made of, because the report has to
    say how much of the batch a set of labels actually covers."""
    failures = [r for r in rows if not r["outcome"].get("official_won")]
    wins = [r for r in rows if r["outcome"].get("official_won")]
    reasons: dict[str, list[str]] = defaultdict(list)
    for r in failures:
        reasons[r["episode_id"]].append("failure")
    for t in sorted({r["task_type"] for r in wins}):
        best = min((r for r in wins if r["task_type"] == t),
                   key=lambda r: _task_order_key(str(r["task_id"]), seed))
        reasons[best["episode_id"]].append("success_reference")

    if segment != "full":
        chosen = [r for r in rows if r["episode_id"] in reasons]
        return {"rows": sorted(chosen, key=lambda r: (str(r["task_type"]),
                                                       str(r["slot_index"]))),
                "reasons": reasons, "failures": len(failures), "wins": len(wins),
                "stratified_draw": None}

    # every case §10.3 names outright, before any sampling happens
    for r in failures:
        m = r.get("run_markers") or {}
        if r["outcome"].get("false_finish"):
            reasons[r["episode_id"]].append("false_finish")
        if (r["outcome"].get("model_errors") or 0) > 0:
            reasons[r["episode_id"]].append("model_error")
        if (m.get("longest_stuck_repeat") or 0) >= LOOP_THRESHOLD:
            reasons[r["episode_id"]].append("loop_threshold")
    # one trace per failing task_id, and both sides when the two repeats disagree
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[str(r["task_id"])].append(r)
    for tid, group in by_task.items():
        failed = [r for r in group if not r["outcome"].get("official_won")]
        if not failed:
            continue
        if len({bool(r["outcome"].get("official_won")) for r in group}) > 1:
            for r in group:
                reasons[r["episode_id"]].append("repeat_disagreement_pair")
        else:
            best = min(failed, key=lambda r: (_task_order_key(tid, seed), str(r["repeat"])))
            reasons[best["episode_id"]].append("one_per_failing_task")
    rng = random.Random(seed)
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in failures:
        by_type[r["task_type"]].append(r)
    drawn = [rng.sample(v, min(8, len(v))) for v in
             (by_type[t] for t in sorted(by_type))]
    draw = [r["episode_id"] for group in drawn for r in group]
    for eid in draw:
        reasons[eid].append("stratified_draw")
    chosen = [r for r in rows if r["episode_id"] in reasons]
    return {"rows": sorted(chosen, key=lambda r: (str(r["task_type"]), str(r["slot_index"]))),
            "reasons": reasons, "failures": len(failures), "wins": len(wins),
            "stratified_draw": {"seed": seed, "total_drawn": len(draw),
                                "cap": "min(8, failures of that type)"}}


def _run_segment(run_root: str) -> str | None:
    """The segment the run root itself says it is. A row's `segment` is copied from
    here, never from the `--segment` argument: the two can differ, and when they do
    the reader should see the run's own claim."""
    mp = os.path.join(run_root, "manifest.json")
    if not os.path.isfile(mp):
        return None
    with open(mp, encoding="utf-8") as f:
        return (json.load(f) or {}).get("segment")


def build_worklist(run_root: str, segment: str, out_path: str,
                   cap: int = 4, blind: bool = False) -> dict:
    run_segment = _run_segment(run_root)
    markers = _markers(run_root)
    rows, valid, infra = [], 0, 0
    for d in _episode_dirs(run_root):
        arts = episode_artifacts(d)
        if not arts["summary"]:
            continue                      # a slot still in flight, not a result
        ev = arts["evaluation"]
        if ev.get("scoreable") is False or not ev.get("termination_reason"):
            infra += 1
            continue                      # an infrastructure row: no verdict, no label
        valid += 1
        eid = arts["summary"].get("episode_id")
        r = episode_row(arts, cap=cap, segment=run_segment, markers=markers.get(eid))
        if r:
            rows.append(r)
    quiet = sorted(r["episode_id"] for r in rows
                   if r["evidence_basis"] == "stop_without_failed_command")
    # counted here, before anything is stripped: the header describes the population,
    # and `blind` removes outcome fields from the rows a reviewer reads, not the
    # aggregate facts about how that population was assembled
    won_in_population = sum(1 for r in rows if r["outcome"].get("official_won"))
    picked = select(rows, segment)
    chosen = picked["rows"]
    if blind:
        # §5 of the guidelines: judge the pattern before the outcome is visible.
        # What is removed is every field that carries a verdict — the grader's
        # (`outcome`) and the runtime's (`termination`, including its `failure_code`
        # and the `note` that repeats the model's own excuse). What stays is behaviour:
        # the commands, the replies, and the last decision with its evidence refs.
        # `success_reference` is a reason that *says* the episode won, so it is not
        # written into a blind row either; the header keeps the aggregate count.
        for r in chosen:
            for verdict in ("outcome", "termination"):
                r.pop(verdict, None)
            r["selection_reason"] = [x for x in picked["reasons"][r["episode_id"]]
                                     if x != "success_reference"]
    else:
        for r in chosen:
            r["selection_reason"] = picked["reasons"][r["episode_id"]]
    coverage = {"sampled": len(chosen), "benchmark_valid": valid,
                "sampled_over_benchmark_valid": round(len(chosen) / valid, 4) if valid else None,
                "failures_in_sample": sum(1 for r in chosen
                                          if "failure" in r["selection_reason"]),
                "wins_in_sample": sum(1 for r in chosen
                                      if "success_reference"
                                      in picked["reasons"][r["episode_id"]])}
    header = {"bst_spec": "BST-1.0", "kind": "annotation_worklist", "segment": segment,
              "run_root": os.path.abspath(run_root), "run_segment": run_segment,
              "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "benchmark_valid_episodes": valid, "infrastructure_rows_excluded": infra,
              "episodes_with_failed_commands": len(rows) - len(quiet),
              "episodes_stopping_without_failed_command": len(quiet),
              "episodes_stopping_without_failed_command_ids": quiet,
              "won_episodes_in_population": won_in_population,
              "sampled_episodes": len(chosen),
              "sampled_deviation_rows": sum(len(r["deviations"]) for r in chosen),
              "selection_coverage": coverage,
              "selection_reason_counts": dict(Counter(
                  x for v in picked["reasons"].values() for x in v).most_common()),
              "stratified_draw": picked["stratified_draw"],
              "selection_rule": SELECTION_RULE, "blind": blind,
              **({"blind_scope": "hidden: `outcome` (the grader's verdict) and "
                                 "`termination` (the runtime's reason, terminal_status, "
                                 "failure_code and note). kept: the commands, the "
                                 "environment's replies, and the final decision with its "
                                 "evidence refs — those are the behaviour being judged, "
                                 "not a verdict on it"} if blind else {}),
              "deviation_cap_per_episode": cap,
              "guidelines": {"path": GUIDELINES_PATH,
                             "sha256": _sha_file(GUIDELINES_PATH)},
              "taxonomy": list(TAXONOMY),
              "note": "the sample is fixed by the rule above before any label is written. "
                      "Every valid episode is a candidate: one that never got an error "
                      "reply is listed in "
                      "episodes_stopping_without_failed_command_ids and annotated from its "
                      "final decision, not skipped silently (SPEC-BST 9.3)"}
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for r in chosen:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"path": out_path, "benchmark_valid": valid, "population": len(rows),
            "sampled": len(chosen),
            "sampled_deviation_rows": sum(len(r["deviations"]) for r in chosen),
            "stopping_without_failed_command": len(quiet),
            "coverage": coverage, "selection_reason_counts": header["selection_reason_counts"]}


# ---------------------------------------------------------------- the gate ------

def validate_rows(path: str) -> dict:
    """Every annotation row, and what is missing from it. Read-only: the fix is to
    go and look, never to delete the row.

    Two buckets, because they are two different failures. A row with no label at all
    is *unlabeled* — the work is not done yet, which is not a §9.3 violation. A row
    with a label but no evidence, or a label outside the frozen taxonomy, is an
    *invalid annotation*: it would have counted as a finding it cannot support.
    Pointing this at a worklist is allowed and produces one unlabeled row per episode,
    which is the correct reading of a file that carries no labels by construction."""
    rows = _read_jsonl(path)
    header = rows[0] if rows and rows[0].get("kind") == "annotation_worklist" else None
    unlabeled, problems = [], []
    for i, row in enumerate(rows, start=1):
        if row is header:
            continue
        labels = row.get("labels") or []
        annotated = bool(labels or row.get("reason") or row.get("annotated"))
        if not annotated:
            unlabeled.append({"line": i, "episode_id": row.get("episode_id"),
                              "reason": "no labels, no reason: this row is still a "
                                        "worklist entry, not an annotation"})
            continue
        bad = []
        for field in ("episode_id", "task_id", "instruction_verbatim", "command_sent",
                      "reason", "competing_explanation"):
            if not row.get(field):
                bad.append(field)
        # §2 binds a row to `decision_id` *or* `context_id`, and the distinction is not
        # cosmetic: the refused rounds that dominate this batch produced no Decision at
        # all, so requiring a decision id would make the single most common failure
        # impossible to annotate honestly.
        if not row.get("decision_id") and not row.get("context_id"):
            bad.append("decision_id|context_id")
        if (not row.get("evidence_refs") and not row.get("environment_response")
                and not row.get("rejection_reasons")):
            # a refused payload has no environment reply to cite — the validator's own
            # reasons are what the round produced, so they count as its response
            bad.append("evidence_refs|environment_response|rejection_reasons")
        if not labels:
            bad.append("labels")
        unknown = [l for l in labels if l not in TAXONOMY]
        if unknown:
            bad.append(f"labels_not_in_taxonomy:{unknown}")
        conf = row.get("confidence")
        if conf not in CONFIDENCE:
            bad.append(f"confidence:{conf}")
        if row.get("claims_better_action") and not row.get("counterfactual_basis"):
            bad.append("counterfactual_basis")
        # §9.1: the model's own words are auxiliary evidence, never the sole support
        if row.get("reason") and row.get("reason") == row.get("model_rationale"):
            bad.append("reason_is_only_model_rationale")
        if bad:
            problems.append({"line": i, "episode_id": row.get("episode_id"),
                             "decision_id": row.get("decision_id"), "missing": bad})
    return {"annotations": path, "rows": len(rows) - (1 if header else 0),
            "worklist_header_seen": bool(header), "guidelines_sha256_in_header":
            ((header or {}).get("guidelines") or {}).get("sha256"),
            "unlabeled": unlabeled, "invalid": problems}


def _sha_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser("embodied_agent.benchmark.annotate (SPEC-BST 9)")
    sub = ap.add_subparsers(dest="command", required=True)
    w = sub.add_parser("worklist", help="the evidence packets for the sampled failures")
    w.add_argument("--run-root", required=True)
    w.add_argument("--segment", default="screening", choices=("screening", "full"))
    w.add_argument("--out", required=True)
    w.add_argument("--cap", type=int, default=4, help="max deviation rows per episode")
    w.add_argument("--blind", action="store_true",
                   help="strip outcome fields: judge the pattern before the result")
    v = sub.add_parser("validate", help="check an annotations.jsonl against the 9.3 minimum")
    v.add_argument("--annotations", required=True)
    args = ap.parse_args(argv)

    if args.command == "worklist":
        out = build_worklist(args.run_root, args.segment, args.out, cap=args.cap,
                             blind=args.blind)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    report = validate_rows(args.annotations)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not report["invalid"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
