"""SPEC-v0.2 §5.6 step 1: Skill Gap Detection — "the library ran out", as data.

§5.6's pipeline starts at `Task / Experience → Skill Gap Detection`, and `core/v02.py:SkillGap`
says why that row has to exist: without it, "the agent acquired a skill" and "the framework
patched a hole" are the same line in the log. So this module's whole job is to decide, from
records alone, when a repeated non-completion is evidence about the *library* rather than about the
world or the plan — and to say nothing when it is not.

Three constraints, in order of how easy each is to violate:

* **Records only, and only the loop's own.** The input is one episode's `events.jsonl` (plus its
  `episode_summary.json` for provenance): `execution_feedback` payloads carry `status`,
  `failure_code`, `rejection_reasons`, `new_measurement`, `pre/post_state_version` and
  `progress_changes`, and `decision_context` carries the round index. Nothing here asks the
  simulator anything, opens a scene, or reads an `expected` block — §12.6 keeps the hidden
  evaluator out of the agent, and a gap derived from the answer key would be exactly the leak the
  ablation is supposed to be immune to.
* **A refusal is not a gap.** A single `rejected` whose reason names a precondition ("pick while the
  hold state is unknown") is the primitive behaving exactly as §5.2 declares, and what is missing
  is a *decision* — §5.7 files recovery and replanning under the model, not under the library. A
  detector that counted those would inflate the denominator of §11's "candidate generation rate"
  and let a module take credit for a bug it did not fix. So `missing_because="precondition_unmet"`
  is deliberately never emitted here, even though the frozen enum has the value; that is asserted
  as a rule, not left as an absence (see
  `test_a_single_precondition_refusal_is_not_reported_as_a_skill_gap`).
* **Recurrence is the signal, and it has two kinds that the records distinguish.** What separates
  "the program is wrong" from "nothing stopped" is whether the loop *learned anything between the
  attempts*: an attempt that produced a new measurement and a later state version means the world
  was re-observed and the same call failed again — a physical defeat a different program might
  answer. An attempt that produced neither means the identical call was issued against identical
  information, and no program in the library could end that except one with an explicit stop.

| `missing_because` | fires when (all fields from records) | rule id |
|---|---|---|
| `no_skill` | a rejection reason says `unknown skill` — the action itself is absent, once is enough | `unknown_skill` |
| `no_termination` | the runtime's own loop guard fired (`REPEATED_INVALID`), or ≥2 attempts of one cell of which **none** measured anything new | `repeated_invalid_guard`, `repeat_without_new_measurement` |
| `repeated_failure` | ≥2 attempts of one cell, at least one of which re-measured and moved the state version | `repeat_after_remeasurement` |
| `precondition_unmet` | never emitted by this detector — see the second bullet | — |
| `other` | never emitted | — |

One row per **cell**, where a cell is `(skill, entity_id, target_id, failure_code)` inside one
episode: that is the finest grouping that is still about one piece of missing capability, and it is
what makes "12 gaps" and "120 failing calls" different claims that a reader can tell apart. The
cell, the attempt count and the rule are all written into `provenance`, so a later reader can
re-derive the row from the artifact without re-running the detector.

§11 rows fed: this module is the **denominator** of "candidate generation rate" (candidates per
gap, and episodes with at least one gap per episodes run) and the reason that rate means anything:
without a gap record, "we generated 3 candidates" has no "out of how many". `evidence_refs`,
`round_index` and `desired_effect` are the join keys for "skill reuse success" (a reuse is only a
reuse if a gap was open at that round) and `existing_candidates_considered` is what distinguishes a
gap the library already claims to cover — the `cross-instance transfer` failure case — from one
nothing ever offered.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from ..core.skills import SkillRegistry
from ..core.v02 import SkillGap, SkillSpec
from ..episodic.experience import final_plan_rows, read_episode_dir

#: Attempts of one cell before the repetition is evidence about the library. Two, not three: one
# failure is the world being hard, and the first repetition is the point at which the same
# behaviour was chosen again with the failure already measured.
REPEAT_THRESHOLD = 2

#: The statuses that mean "this call did not do what it said". `completed` is the one `SkillStatus`
# member that says it did; `uncertain` is deliberately *not* listed, because §5.8's layer-2 "cannot
# verify" is not a defeat the library should be credited with. `finish_accepted` and
# `finish_rejected` also appear in the ledger — `core/runtime.py:442` emits them for a finish
# decision, and the measured P2 batch has 5 of the former — but they are feedback statuses, not
# members of `SkillStatus`, so they fall outside this tuple by construction. A contract test asserts
# every value here is a real `SkillStatus`, because the failure mode of a record reader is a silently
# empty census.
NON_COMPLETION_STATUSES = ("failed", "rejected", "timeout")

#: The loop-guard code: `Budgets.max_identical_invalid_attempts` spent. One of these is already a
# whole episode of not terminating, so it does not need a second occurrence.
LOOP_GUARD_CODE = "REPEATED_INVALID"

#: Wording shared by `SkillExecutor._rejected` and `PlanValidator.validate` for a name outside the
# catalogue. Matched on the phrase rather than the code because both paths report
# `INVALID_DECISION`, and the argument-free and unknown-skill cases are different gaps.
UNKNOWN_SKILL_MARKER = "unknown skill"

DETECTOR_VERSION = "gap_detect_v1"

#: Every `ExecutionFeedback` field this detector reads, as data.
# A contract test asserts each name is still a declared field of the model, because the failure
# mode of a record reader is not a traceback — it is `fb.get("status")` returning None forever and
# the census quietly reporting zero gaps on a renamed field.
READ_FIELDS = ("skill", "entity_id", "target_id", "status", "failure_code", "new_measurement",
               "pre_state_version", "post_state_version", "rejection_reasons",
               "progress_changes", "context_id", "post_observation_ref")

# `pick`'s and `place`'s effects are measurable predicates, and `core/verify.py` already names them
# `grasp:<eid>` and `placed:<eid>:<target>`; the verifier writes those same ids into
# `verification.reports[].predicate_id`, so this is the loop's vocabulary rather than a new one.
EFFECT_PREDICATE = {"pick": "grasp", "place": "placed"}

FAILURE_STATUSES = NON_COMPLETION_STATUSES


@dataclass
class _Attempt:
    """One non-completion, as the record states it."""
    event_id: str
    skill: str
    entity_id: Optional[str]
    target_id: Optional[str]
    code: Optional[str]
    status: str
    measured: bool
    state_moved: bool
    round_index: Optional[int]
    round_resolved: bool
    post_observation_ref: Optional[str]
    post_state_version: Optional[int]
    reasons: tuple[str, ...] = ()
    goal_assignment: Optional[str] = None


@dataclass
class _Cell:
    key: tuple[Any, ...]
    attempts: list[_Attempt] = field(default_factory=list)

    @property
    def last(self) -> _Attempt:
        return self.attempts[-1]


# ------------------------------------------------------------------ the ledger ----
def _rounds_by_context(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in events:
        if e.get("type") != "decision_context":
            continue
        payload = e.get("payload") or {}
        cid, index = payload.get("context_id"), payload.get("round_index")
        if cid is not None and isinstance(index, int):
            out[str(cid)] = index
    return out


def _subgoal_joins(events: Iterable[dict[str, Any]]
                  ) -> tuple[dict[str, str], dict[str, list[str]]]:
    """(`predicate_id` -> subgoal, `entity_id` -> subgoals) from the last published plan view.

    Reuses `episodic.experience.final_plan_rows`, which already documents why "last" is file order
    and not version order. The predicate join is the exact one; the entity join exists because a
    gap's `desired_effect` is the *primitive's* predicate (`grasp:obj_green_1`) while a plan row
    carries the goal's (`placed:obj_green_1:tray_middle`), and the second is the one the work graph
    can point at. An entity named by more than one row resolves to nothing rather than to a guess.
    """
    by_predicate: dict[str, str] = {}
    by_entity: dict[str, list[str]] = {}
    for row in final_plan_rows(events):
        sid = row.get("subgoal_id")
        if not sid:
            continue
        predicate = row.get("predicate_id")
        if predicate:
            by_predicate[str(predicate)] = str(sid)
        for entity in row.get("target_entity_ids") or []:
            by_entity.setdefault(str(entity), []).append(str(sid))
    return by_predicate, by_entity


def _subgoal_for(effect: str, entity_id: Optional[str], by_predicate: dict[str, str],
                 by_entity: dict[str, list[str]]) -> tuple[Optional[str], str]:
    """The row this gap is work for, and how it was found."""
    if effect in by_predicate:
        return by_predicate[effect], "predicate_id"
    owners = sorted(set(by_entity.get(entity_id or "", [])))
    if len(owners) == 1:
        return owners[0], "target_entity_id"
    return None, ("ambiguous_across_rows" if owners else "unresolved")


def _attempts(events: Iterable[dict[str, Any]]) -> list[_Attempt]:
    """Every non-completion in record order, with the round it happened in.

    File order is kept rather than a sort: `sequence` in the ledger already *is* loop order, and
    re-sorting by a timestamp would reorder events the loop filed in one round.
    """
    events = list(events)
    rounds = _rounds_by_context(events)
    attempts: list[_Attempt] = []
    last_seen_round: Optional[int] = None
    for e in events:
        etype = e.get("type")
        if etype == "decision_context":
            index = rounds.get(str((e.get("payload") or {}).get("context_id")))
            last_seen_round = index if index is not None else last_seen_round
            continue
        if etype != "execution_feedback":
            continue
        fb = (e.get("payload") or {}).get("feedback") or {}
        if str(fb.get("status") or "") not in NON_COMPLETION_STATUSES:
            continue
        cid = str(fb.get("context_id") or "")
        resolved = cid in rounds
        round_index = rounds.get(cid, last_seen_round)
        goal_assignment = None
        for change in fb.get("progress_changes") or []:
            if isinstance(change, dict) and fb.get("entity_id") \
                    and str(change.get("assignment") or "").startswith(f"{fb['entity_id']}->"):
                goal_assignment = str(change.get("assignment"))
                break
        reasons = tuple(str(r) for r in (fb.get("rejection_reasons") or ()))
        attempts.append(_Attempt(
            event_id=str(e.get("event_id") or ""),
            skill=str(fb.get("skill") or ""),
            entity_id=fb.get("entity_id"),
            target_id=fb.get("target_id"),
            code=fb.get("failure_code"),
            status=str(fb.get("status")),
            # `new_measurement` is the record's own word for "the agent learned something from this",
            # and the state version is the corroboration: a rejection that neither measured nor
            # advanced changed nothing at all.
            measured=bool(fb.get("new_measurement")),
            state_moved=(fb.get("post_state_version") is not None
                         and fb.get("post_state_version") != fb.get("pre_state_version")),
            round_index=round_index,
            round_resolved=resolved,
            post_observation_ref=fb.get("post_observation_ref"),
            post_state_version=fb.get("post_state_version"),
            reasons=reasons,
            goal_assignment=goal_assignment,
        ))
    return attempts


def _cells(attempts: Sequence[_Attempt]) -> list[_Cell]:
    """Group by (skill, entity, target, failure code), first-seen order preserved."""
    order: dict[tuple[Any, ...], _Cell] = {}
    for attempt in attempts:
        key = (attempt.skill, attempt.entity_id, attempt.target_id, attempt.code)
        cell = order.get(key)
        if cell is None:
            cell = _Cell(key=key)
            order[key] = cell
        cell.attempts.append(attempt)
    return list(order.values())


# ------------------------------------------------------------------ classification ----
def classify(cell: _Cell) -> Optional[tuple[str, str]]:
    """`(missing_because, rule_id)` for one cell, or None when it is not evidence about the library."""
    attempts = cell.attempts
    if any(UNKNOWN_SKILL_MARKER in reason for a in attempts for reason in a.reasons):
        return "no_skill", "unknown_skill"
    if any(a.code == LOOP_GUARD_CODE for a in attempts):
        return "no_termination", "repeated_invalid_guard"
    if len(attempts) < REPEAT_THRESHOLD:
        return None
    if not any(a.measured or a.state_moved for a in attempts):
        return "no_termination", "repeat_without_new_measurement"
    return "repeated_failure", "repeat_after_remeasurement"


def desired_effect(skill: Optional[str], entity_id: Optional[str],
                   target_id: Optional[str]) -> tuple[str, str]:
    """The effect the failing call was about, and where the words for it came from.

    Falls back to the catalogue's own `post` text when no predicate applies (`observe`,
    `safe_retreat`, or an entity the record never named), so every row is traceable to a
    declaration that already exists rather than to a phrase invented here.
    """
    stem = EFFECT_PREDICATE.get(skill or "")
    if stem and entity_id:
        return (f"{stem}:{entity_id}" if stem == "grasp"
                else f"{stem}:{entity_id}:{target_id or '?'}"), "verifier_predicate_id"
    posts = (SkillRegistry.CATALOGUE.get(skill or "") or {}).get("post") or []
    if posts:
        return str(posts[0]), "catalogue_post"
    return f"{skill or 'unnamed_skill'}:{entity_id or '?'}", "no_declared_effect"


def _effect_stem(effect: str) -> str:
    """`grasp:obj_red_2` and `grasp:{object}` share the stem `grasp`.

    Matching on stems is what lets a *template* in the library cover a *concrete* predicate from the
    log; matching on full strings would compare a placeholder to an entity id and always miss.
    """
    return str(effect).split(":", 1)[0].strip()


def _primitive_declares(skill: str, effect: str) -> bool:
    """Does this shipped primitive claim, in its own catalogue entry, to produce this effect?"""
    stem = _effect_stem(effect)
    entry = SkillRegistry.CATALOGUE.get(skill) or {}
    return (EFFECT_PREDICATE.get(skill) == stem
            or any(_effect_stem(str(p)) == stem for p in (entry.get("post") or [])))


def covers_effect(spec: SkillSpec, effect: str) -> bool:
    """Does a library entry declare this effect?

    Public because the *same* question is a §11 row: a gap whose effect the library already claims
    to cover is not a missing capability, it is a failed transfer of a capability that exists — and
    counting those together with the absent ones would make "the library grew" look like "the
    library did not grow".
    """
    return any(_effect_stem(declared) == _effect_stem(effect)
               for declared in spec.expected_effects)


def _considered(effect: str, skill: Optional[str],
                library: Sequence[SkillSpec]) -> list[str]:
    """Library entries the detector consulted for this effect and did not accept as an answer.

    Includes the primitive itself when the effect is one it declares: that is literally the row a
    reader needs, because "the library already had `pick` and `pick` failed four times" is a
    different finding from "nothing in the library could have done this".
    """
    out: list[str] = []
    if skill and skill in SkillRegistry.CATALOGUE and _primitive_declares(skill, effect):
        out.append(skill)
    for spec in library:
        if spec.name in out:
            continue
        if covers_effect(spec, effect):
            out.append(spec.name)
    return out


# ------------------------------------------------------------------ the detector ----
def detect_gaps(events: Iterable[dict[str, Any]], *,
                summary: Optional[dict[str, Any]] = None,
                library: Sequence[SkillSpec] = ()) -> list[SkillGap]:
    """The gaps one episode's records show, in the order the loop ran into them."""
    events = list(events)
    attempts = _attempts(events)
    by_predicate, by_entity = _subgoal_joins(events)
    episode_id = str((summary or {}).get("episode_id") or "") or None
    condition = None
    for e in events:
        if e.get("type") == "ablation":
            condition = str((e.get("payload") or {}).get("condition") or "") or None
            break
    result = (summary or {}).get("result") or {}

    gaps: list[SkillGap] = []
    for cell in _cells(attempts):
        verdict = classify(cell)
        if verdict is None:
            continue
        missing_because, rule = verdict
        skill, entity_id, target_id, code = cell.key
        effect, effect_source = desired_effect(skill, entity_id, target_id)
        subgoal_id, subgoal_source = _subgoal_for(effect, entity_id, by_predicate, by_entity)
        considered = _considered(effect, skill, library)
        last = cell.last
        gaps.append(SkillGap(
            episode_id=episode_id,
            round_index=int(last.round_index or 0),
            subgoal_id=subgoal_id,
            desired_effect=effect,
            missing_because=missing_because,
            # the event ids are the handle: `evt_<episode>_<sequence>`, so a reader can open
            # `events.jsonl` and see the attempts without trusting this row.
            evidence_refs=[a.event_id for a in cell.attempts],
            existing_candidates_considered=considered,
            provenance={
                "detector": DETECTOR_VERSION,
                "rule": rule,
                "cell": f"{skill}|{entity_id}|{target_id}|{code}",
                "attempt_count": len(cell.attempts),
                "measured_any": bool(any(a.measured or a.state_moved for a in cell.attempts)),
                "statuses": sorted({a.status for a in cell.attempts}),
                "rejection_reasons": sorted({r for a in cell.attempts for r in a.reasons}),
                "goal_assignment": last.goal_assignment,
                "effect_source": effect_source,
                # the library entries (never the failing primitive) that claim this effect already.
                # Non-empty means this gap is a transfer failure, not an absent capability.
                "covered": [name for name in considered if name != skill],
                "subgoal_id_source": subgoal_source,
                "round_index_source": ("decision_context" if last.round_resolved
                                       else "nearest_preceding_decision_context"
                                       if last.round_index is not None else "unresolved"),
                "condition": condition,
                "task_id": result.get("task_id"),
                "terminal_status": result.get("terminal_status"),
                "derived_from": "events.jsonl (no simulator question asked, no expected block read)",
            },
            based_on_state_version=last.post_state_version,
            based_on_observation_ref=last.post_observation_ref,
        ))
    return gaps


def gaps_for_episode(episode_dir: str, *,
                     library: Sequence[SkillSpec] = ()) -> list[SkillGap]:
    """One episode directory (`events.jsonl` + `episode_summary.json`) → its gaps.

    Raises `FileNotFoundError` rather than returning `[]` for a directory with no ledger: an empty
    list here would mean "this episode found no gap", which is a finding, and a typo'd path must
    not be able to produce one.
    """
    if not os.path.exists(os.path.join(episode_dir, "events.jsonl")):
        raise FileNotFoundError(f"{episode_dir} has no events.jsonl; it is not an episode directory")
    summary, events = read_episode_dir(episode_dir)
    return detect_gaps(events, summary=summary, library=library)


def gaps_for_run(run_dir: str, *, library: Sequence[SkillSpec] = ()) -> dict[str, list[SkillGap]]:
    """Every episode of one run directory, keyed by episode id, in the order on disk."""
    root = os.path.join(run_dir, "episodes")
    if not os.path.isdir(root):
        raise FileNotFoundError(f"{run_dir} has no episodes/ directory; it is not a run directory")
    out: dict[str, list[SkillGap]] = {}
    for name in sorted(os.listdir(root)):
        if not os.path.exists(os.path.join(root, name, "events.jsonl")):
            continue
        gaps = gaps_for_episode(os.path.join(root, name), library=library)
        out[name] = gaps
    return out


def detector_report(run_dirs: Sequence[str], *,
                    library: Sequence[SkillSpec] = ()) -> dict[str, Any]:
    """What the detector saw over a set of runs — including what it refused to call a gap.

    The negatives are in here on purpose. A gap detector that reported only its positives would
    make "0 gaps in the memory set" indistinguishable from "the detector never ran there", and
    those two mean opposite things about an experiment; and `not_reported_because` is the number
    that decides whether `REPEAT_THRESHOLD = 2` is doing any work at all.

    Counted per **episode directory**, not per episode id: the same id runs in every arm, so
    keying by id would collapse four arms' evidence into one row and understate both denominators.
    """
    gaps: list[SkillGap] = []
    episodes = 0
    episodes_with_gaps = 0
    cells = 0
    not_reported: dict[str, int] = {}
    rules: dict[str, int] = {}
    for run_dir in run_dirs:
        for episode_id, episode_gaps in gaps_for_run(run_dir, library=library).items():
            episodes += 1
            episodes_with_gaps += 1 if episode_gaps else 0
            _, events = read_episode_dir(os.path.join(run_dir, "episodes", episode_id))
            for cell in _cells(_attempts(events)):
                cells += 1
                if classify(cell) is not None:
                    continue
                status = cell.last.status
                reason = ("single_attempt_refusal" if status == "rejected"
                          else "single_attempt_failure" if status == "failed"
                          else "single_attempt_timeout")
                not_reported[reason] = not_reported.get(reason, 0) + 1
            for gap in episode_gaps:
                rule = str(gap.provenance.get("rule"))
                rules[rule] = rules.get(rule, 0) + 1
            gaps.extend(episode_gaps)
    return {
        "detector": DETECTOR_VERSION,
        "repeat_threshold": REPEAT_THRESHOLD,
        "run_dirs": list(run_dirs),
        "episodes_scanned": episodes,
        "episodes_with_a_gap": episodes_with_gaps,
        "non_completion_cells": cells,
        "gaps": len(gaps),
        "cells_not_reported": cells - len(gaps),
        "not_reported_because": dict(sorted(not_reported.items())),
        "by_missing_because": {
            kind: sum(1 for g in gaps if g.missing_because == kind)
            for kind in ("no_skill", "precondition_unmet", "repeated_failure",
                         "no_termination", "other")},
        "by_rule": dict(sorted(rules.items())),
        # A gap whose effect some *other* library entry already declares is not a missing
        # capability: the capability exists and did not transfer. Separated because §11's
        # "cross-instance transfer" row would otherwise be counted by this detector's own
        # denominator, and the two findings imply opposite fixes (write a program vs fix one).
        "gaps_already_covered_by_the_library": sum(
            1 for g in gaps if g.provenance.get("covered")),
        "cells_by_primitive": sorted({str(g.provenance.get("cell", "").split("|")[0])
                                      for g in gaps if g.provenance.get("cell")}),
        "episodes_with_a_gap_by_id": sorted({str(g.episode_id) for g in gaps if g.episode_id}),
    }
