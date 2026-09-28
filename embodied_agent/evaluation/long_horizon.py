"""§11's Long-horizon group — definitions written before the numbers.

SPEC-v0.2 §11 lists six rows under *Long-horizon behavior*: subgoal completion, dependency
violations, progress regressions, unnecessary rework, repeated failures, recovery/replanning
effectiveness. P2-c and P2-d built the machinery that produces the evidence for them; nothing in
the codebase counted them, and `EpisodeResult`'s own `recovery_events` / `retry_events` /
`replan_events` were measured on P2-e's first batch to be **0 in all eight episodes of all four
arms** while the log in the same directory held two `recovery_action` records and a `plan_revision`.
That reading was right and its explanation was not: P5-d found the zeros were a wiring gap —
`core/runtime.py::_finalize` never passed those fields, so the arm's tally stopped at the event log.
The loop fills two of them now (`core/runtime.py`, P5-d), and the frozen P5-c batch re-scores
byte for byte unchanged (`sha256 3effe284…`): the 16 §11 rows are recomputed from `events.jsonl`,
and the `loop_counters` block reads the `episode_end` record as the run wrote it, so only episodes
run *after* the fix report a tally. The point that survives the fix is the semantic one.
Those counters answer a v0.1 question (what the
loop itself decided to retry against a failure, and `retry_events` is still unfilled because a retry
is a field of a `recovery_action` record rather than its own event), and §11's rows ask a v0.2 one
(what happened to the *work*). So every number here stays recomputed offline from `events.jsonl`,
and the v0.1 counters are reported beside it as a separate account, never folded in.

The house rule is the one P1-f established (`evaluation/vlm_contrast.py`): every rate is rendered
from `METRIC_DEFINITIONS` below, carries its numerator and denominator in the output, and is pooled
by summing integers rather than by averaging per-episode fractions. What follows are the four
decisions this module exists to make.

**1. A missing record is `not_measured`, never a zero.**

The ablation gate removes records by design — `wo_planning` authors no plan, `wo_working_memory`
files no `obligation_check`, `wo_replanning` files no `recovery_action`. If a plan-shaped metric
reported 0.0 on the arm that has no plan, the table would read "no dependency violations" for the
arm that cannot represent a dependency at all, and the ablation would appear to *improve* the
behaviour it removes. So each of those metrics carries an explicit denominator of the records that
exist, `_rate` publishes `value: null` when it is empty, and the arm block says
`not_measured_reason`. Two quantities that *cannot* be computed that way are computed from the
behavioural timeline instead, and the record count is published beside them as an instrument audit
(`L6.recorded_vs_inferred`): an arm that hides its own recovery must still be measured doing it.

**2. A relation's verdict comes from the verifier's list, and the plan's own claim is audited
against it.**

`decision_context.progress` is the same list v0.1 published — one `{predicate_id, value, evidence,
unmeasured}` per goal conjunction, produced by `RuntimeVerifier` off the simulator — so a timeline
built from it exists on every arm, including the ones with no plan, and it is the only verdict
series an arm difference can be read off. `finish_check.reports` is appended as a final sample when
the episode asked to finish, because that is layer 3 reading the same predicates one last time. The
plan's own `satisfied` field is *not* the truth source: `L1.row_claim_vs_key` audits each definite
claim against the verdict page **of the round that published it** — the view and that round's
`decision_context` read one world state, so a disagreement is the loop contradicting itself at one
instant, which is the row §11's Task group would otherwise hide (a plan that believes its own rows).
The comparison is deliberately *not* against the final verdict: measured on `full`, that pairing
called three correct claims lies, because the world moved after the plan spoke. That is L3's
subject and it is counted there.

**3. A regression is attributed to the experiment or to the agent, and the two are never summed.**

These cases are built to break completions: every `lh_*` episode arms at least one disturbance
*inside the region the plan is contending for* (P2-d's `preflight` verifies exactly that). A
true→false flip caused by an injected impulse is the scenario working as designed and is the thing
a recovery is judged on; a flip caused by the agent's own handoff knocking a neighbour off its tray
is the agent's. So every flip is classified from the log — an `environment_event` naming that entity
in the interval, an executed skill on that entity, or that entity appearing in the feedback's
`state_diff.moved` / `affected_entities` — and the four categories are published separately.
`L3.agent_caused_regression` is the headline; the environment's share sits beside it. The
`both` category is *not* split between them: an interval with two signals is named as ambiguous and
counted once, because choosing a winner per case is the kind of judgement a metric must not make
after the numbers exist.

The same flip does not define *loss*, though. A `place` that reports `completed` while the next
reading of that pair still says `false` lost the handoff too, and a relation that was never true
cannot flip — so on `full/payload` that mechanism produced two `recovery_action` records, a re-run
pick and place, and an L6 denominator of zero. `_loss_events` therefore counts both shapes, labels
each with the kind that produced it, and keeps only the flip in §11's regression rows.

**4. Rework is what the task did not ask for.**

A `place` of a pair that was already measured `true` is a second bite at finished work *unless* the
interval since that verdict names a cause — an injection on that entity, or the agent's own pick of
it, which is what a temporary move and a restore both look like. The plan distinguishes them from
the inside (`recover`-kind rows, `clear:` predicates), and §8's temporary-state bullet is
deliberately a different id (`L4.plan_asked_move`) from §11's "unnecessary rework"
(`L4.unnecessary_rework`), because a designed detour charged as waste would make the capable arm
look worse than the forgetful one.

## What this set cannot yet answer, stated where the number is

§8's shape 2 — reach into a tray past an occupant to lift the blocked object out — is
**not authorable** in this scene at this object scale: six probes and their measurements are in
`long_horizon_tasks.SHAPE_2_MEASURED`, whose `consequence` says the `recover` row is exercised
through shape 1 (displacement) instead. The artifact therefore reports
`spec8_shape_2: not_applicable` with that sentence attached rather than a `L*.restore_*` rate of
0.0, which would read as "no restores happened" and not as "no restore was reachable".

The other hole is named rather than papered over, and it was measured before it was described. A
commitment discharges against a *plan row*, and a decision that names no row can never close one.
`policy=rule` (the v0.1 control) reads pending goals and never a plan, so it names no `subgoal` and
`WorkingMemory` books commitments whose `predicate_id` is empty: on P2-e's first batch the `full`
arm under that control finished 4 objects into 4 regions and still ended with
`unresolved_obligations: 10`. Under `policy=payload` the same arm ends with 0, and `wo_planning` —
which has the rows but no plan to hold them — ends with 10 again. `policy` is therefore an
`ARM_ASYMMETRIC_FIELD`: obligation-shaped numbers are comparable across *arms* only within one
policy, and the artifact prints the two policies apart instead of pooling them.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Any, Optional

from ..core.events import EpisodeStore
from ..core.v02 import SCHEMA_VERSION, V02_EVENT_TYPES, Ablation
from .report import wilson_interval

# ------------------------------------------------------------------ version ----

#: bumped whenever a counting rule below changes meaning. A table whose rows were scored under two
#: versions of one definition is not one table, and the version has to travel with the artifact.
#: LH-2 replaces two LH-1 rules before any LH-1 artifact was delivered: `L1.row_claim_vs_key`
#: audited a claim against the episode's final verdict (staleness read as a false self-report), and
#: `L6.*` inferred loss from verdict flips alone (a handoff lost before its relation was ever true
#: was invisible). The eight LH-1 probe batches under /tmp are re-scored, not carried over.
METRIC_VERSION = "LH-2"

#: §11's six rows, in the order the SPEC lists them, plus the ids that keep each one honest.
METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "L1.assignment_completion": {
        "question": "of the placements the task asked for, how many are measured true when the "
                    "episode ends?",
        "unit": "one goal predicate (one object x one region) x one episode",
        "numerator": "predicates whose last verdict in the episode is `true`",
        "denominator": "every predicate the verifier's own progress list published in the episode, "
                       "unioned over rounds (a goal that stopped being listed stays in)",
        "truth": "`decision_context.progress`, plus a final sample from `finish_check.reports` "
                 "when the agent asked to finish — both produced by `RuntimeVerifier` off the "
                 "simulator, on every arm",
        "forbidden": "an `unknown` last verdict is not a completion and is not a failure either: it "
                     "stays in the denominator and is counted again by L1.unmeasured_at_end",
    },
    "L1.unmeasured_at_end": {
        "question": "how much of the task did the episode end without measuring?",
        "unit": "one goal predicate x one episode",
        "numerator": "predicates whose last verdict is neither `true` nor `false`",
        "denominator": "the same set L1.assignment_completion divides",
        "truth": "the same verdict timeline",
        "forbidden": "never subtracted from L1.assignment_completion's denominator: a goal nobody "
                     "measured is the failure mode §5.8's layering exists to expose",
    },
    "L1.plan_row_completion": {
        "question": "of the rows the plan ever authored, how many were achieved?",
        "unit": "one plan row (row_key) x one episode",
        "numerator": "rows whose predicate's last verdict is `true`",
        "denominator": "every `row_key` that appeared in any `plan` or `plan_revision` view in the "
                       "episode, including rows later dropped",
        "truth": "the verdict timeline, not the row's own `satisfied` field",
        "forbidden": "`not_measured` on an arm that authored no rows — a zero here would credit the "
                     "arm that cannot state a subgoal with perfect subgoal completion",
    },
    "L1.row_claim_vs_key": {
        "question": "does the plan's own account of its rows agree with what the verifier "
                    "measured?",
        "unit": "one definite row claim x the plan view that published it, paired with that same "
                "round's verdict page",
        "numerator": "claims whose `satisfied` differs from the verdict the `decision_context` of "
                     "the same round published for that predicate",
        "denominator": "row claims that are definite (`true`/`false`) and whose predicate appears "
                       "on the page of the round the view was published for",
        "truth": "both records of one round: the view is authored before that round's context and "
                 "both read the same world state, so a disagreement is the loop contradicting "
                 "itself at one instant",
        "forbidden": "never audited against the episode's *final* verdict — a row that was true "
                     "when written and was knocked false two rounds later is L3's regression, not "
                     "a lying plan. The first version of this metric did exactly that and returned "
                     "3/4 disagreement on an episode where all four claims were correct at their "
                     "own instant; `unknown` is not a claim, so a view authored before the first "
                     "measurement contributes nothing and its absence is reported as "
                     "`not_measured` plus a finding, not as a clean sheet",
    },
    "L2.dependency_edge_violation": {
        "question": "did a prerequisite the plan stated get violated while the work ran?",
        "unit": "one dependency edge (from -> on, kind) x one episode",
        "numerator": "edges published with `violated: true` in any plan view, or named in a "
                     "`plan_revision.view.violated`",
        "denominator": "every edge any plan view of the episode carried",
        "truth": "the plan's own published graph — dependency is a property of the plan, not of "
                 "the world, so there is no external key for it",
        "forbidden": "counted per episode and per distinct edge, never per round: `refresh` "
                     "re-publishes an unresolved violation every round and would multiply one "
                     "defect into ten",
    },
    "L2.violations_per_episode": {
        "question": "how many violated edges does an episode end up carrying?",
        "unit": "episodes (a count per episode, not a proportion)",
        "numerator": "all violated-edge counts summed over the episodes in the cell",
        "denominator": "episodes in the cell",
        "truth": "the plan's own published graph, deduplicated across the views of one episode: a "
                 "`refresh` re-publishing an unresolved violation changes the count of rows, not "
                 "the count of edges",
        "forbidden": "a mean over strings or over only the episodes that authored edges; the "
                     "denominator is every episode in the cell, edge-free ones included",
    },
    "L3.regression_rate": {
        "question": "did work that was measured finished later measure unfinished?",
        "unit": "one goal predicate x one episode",
        "numerator": "predicates with at least one `true` followed (ignoring `unknown` samples) by "
                     "a `false` in the verdict timeline",
        "denominator": "predicates that read `true` at least once in the episode",
        "truth": "the verdict timeline",
        "forbidden": "a flip the task itself caused is not counted as the agent's: it is counted by "
                     "L3.environment_caused and reported beside this one",
    },
    "L3.agent_caused_regression": {
        "question": "how much of that loss did the agent's own hands cause?",
        "unit": "one true→false transition x one episode",
        "numerator": "transitions where the interval since the `true` verdict contains an executed "
                     "pick/place of that entity, or names it in the feedback's `state_diff.moved` "
                     "/ `affected_entities`, and no injection named it",
        "denominator": "all true→false transitions",
        "truth": "the episode's own `execution_feedback` and `environment_event` records",
        "forbidden": "an interval with both an agent action and an injection on the entity is "
                     "`L3.ambiguous_regression`, never split between the two causes",
    },
    "L3.environment_caused_regression": {
        "question": "how much of the loss is the scenario doing what it was built to do?",
        "unit": "one true→false transition x one episode",
        "numerator": "transitions where an `environment_event` named that entity in the interval "
                     "and no agent action on the entity did",
        "denominator": "all true→false transitions",
        "truth": "`environment_event.payload.injection.entity_id`, which is the *affected* body, "
                 "resolved by the controller — not the declared trigger",
        "forbidden": "read as a defect count: on these cases a nonzero value is the preflight's "
                     "promise kept (P2-d), and the capability under test is what happens next",
    },
    "L3.ambiguous_regression": {
        "question": "how often can the log not say who moved it?",
        "unit": "one true→false transition x one episode",
        "numerator": "transitions with both an agent action and an injection on the entity in the "
                     "interval",
        "denominator": "all true→false transitions",
        "truth": "the same two record families",
        "forbidden": "being folded into either headline; this number exists so the choice is never "
                     "made silently",
    },
    "L3.unrecovered_regression": {
        "question": "when work was lost, did it come back?",
        "unit": "one true→false transition x one episode",
        "numerator": "transitions after which the predicate never reads `true` again before the "
                     "episode's last verdict",
        "denominator": "all true→false transitions",
        "truth": "the verdict timeline",
        "forbidden": "counting an episode that ended in `BUDGET_EXHAUSTED` as if its last verdict "
                     "were a considered completion; `terminated` travels with the cell",
    },
    "L4.unnecessary_rework": {
        "question": "how often did the agent redo finished work for no reason the record gives?",
        "unit": "one executed `place` x one episode",
        "numerator": "completed places of a pair whose predicate already read `true`, where the "
                     "interval since that verdict names no cause: no injection on the entity and "
                     "no executed pick of it",
        "denominator": "every executed, non-rejected `place` in the episode",
        "truth": "the verdict timeline plus the feedback order",
        "forbidden": "a re-place after a displacement (that is L6's recovery, and charging it twice "
                     "would punish the arm that noticed) and a move the plan asked for as a "
                     "temporary state (L4.plan_asked_move)",
    },
    "L4.plan_asked_move": {
        "question": "how much of the extra handling did the plan itself owe?",
        "unit": "one executed `place` x one episode",
        "numerator": "completed places into a region that no goal predicate names as that object's "
                     "target, or places of a pair whose `recover`/`clear` row is still open",
        "denominator": "every executed, non-rejected `place` in the episode",
        "truth": "the case's goal conjunction as published in the progress list, and the plan rows",
        "forbidden": "summing with L4.unnecessary_rework: one is the designed detour and the other "
                     "is waste, and the difference between them is a large part of what P2 is for",
    },
    "L5.repeated_failure_share": {
        "question": "what share of attempts were second bites at an attempt that had not "
                    "succeeded?",
        "unit": "one skill attempt that produced a verdict x one episode",
        "numerator": "attempts whose (skill, entity, target) triple had an earlier attempt in the "
                     "episode that did not complete — a failed, timed-out, uncertain or rejected "
                     "verdict",
        "denominator": "every skill attempt in the episode's `execution_feedback` stream, including "
                       "the refusals",
        "truth": "`execution_feedback` in log order, keyed on the triple the loop itself publishes",
        "forbidden": "`EpisodeResult.retry_events` (v0.1's own counter) is not mixed in; it is "
                     "printed in the `loop_counters` block because it answers a different question",
    },
    "L5.failure_codes": {
        "question": "what actually went wrong, by kind?",
        "unit": "episodes (a count per episode, not a proportion)",
        "numerator": "all non-completed verdicts of the named `failure_code` in the cell",
        "denominator": "episodes in the cell",
        "truth": "`execution_feedback.feedback.failure_code`",
        "forbidden": "a mean over code strings. Kinds are cross-tabulated; `null` is its own "
                     "category, `no_code_recorded`, and is counted rather than skipped",
    },
    "L6.recovery_effectiveness": {
        "question": "when the agent went back at lost work, did it come back?",
        "unit": "one loss event with a follow-up action x one episode",
        "numerator": "losses after which the lost predicate reads `true` again before the "
                     "episode's last verdict",
        "denominator": "loss events that were followed by another executed pick/place on the same "
                       "entity: a `true`→`false` transition of a goal predicate, **plus** an "
                       "executed `place` whose own record says `completed` while the verifier's "
                       "next reading of that pair still says `false` (the handoff lost between the "
                       "gripper and the measurement, which never appears as a transition because "
                       "the relation was never true to begin with)",
        "truth": "the verdict timeline; the `recovery_action` record is *not* the truth source here",
        "forbidden": "restricting the denominator to episodes that filed a `recovery_action` "
                     "record: `wo_replanning` cannot file one by construction, and a metric that "
                     "needs the record to measure the behaviour would score the switched-off arm "
                     "as having none. Counting only the transitions is the mirror error and is "
                     "what the first version did: it sat at 0/0 on `full/payload` while the arm "
                     "filed two recovery records and re-ran a handoff. A loss with no follow-up "
                     "action is not a recovery and is not in this denominator — for a transition "
                     "that is L3.unrecovered_regression, and for a silent loss there is no row, "
                     "which is said in the finding rather than scored as a failure",
    },
    "L6.recorded_vs_inferred": {
        "question": "does the arm's own record show the recoveries the timeline shows?",
        "unit": "one loss event with a follow-up action x one episode",
        "numerator": "losses with a `recovery_action` record naming the same entity, filed after "
                     "the loss was published and no later than the action taken about it",
        "denominator": "loss events in L6.recovery_effectiveness's denominator",
        "truth": "both record families, side by side",
        "forbidden": "reading 0.0 on `wo_replanning` as a blindness of the agent: it is the switch, "
                     "and that is the whole point of publishing it next to L6.recovery_effectiveness. "
                     "The inverse mismatch — records with no loss event to attach to — is a finding, "
                     "not a silent zero, because it means one of the two instruments is lying",
    },
    "L6.replanning_effectiveness": {
        "question": "when the plan was revised, did the revision make the work closer to done?",
        "unit": "one `plan_revision` event x one episode",
        "numerator": "revisions after which a row the revision named as reopened, added or "
                     "relabelled reads `true` before the episode's last verdict",
        "denominator": "`plan_revision` events in the episode",
        "truth": "the verdict timeline against the revision's published `view.changed`",
        "forbidden": "counting a revision that only re-derived the same rows (`version_bumped` "
                     "without a changed row) as effective work: it is in the denominator and not "
                     "the numerator, which is how a spinning planner shows up",
    },
}

#: §11's rows in the SPEC's own order, so the report's table cannot quietly add a column.
SPEC_ROWS = (
    ("subgoal completion", ("L1.assignment_completion", "L1.plan_row_completion",
                            "L1.row_claim_vs_key", "L1.unmeasured_at_end")),
    ("dependency violations", ("L2.dependency_edge_violation", "L2.violations_per_episode")),
    ("progress regressions", ("L3.regression_rate", "L3.agent_caused_regression",
                              "L3.environment_caused_regression", "L3.ambiguous_regression",
                              "L3.unrecovered_regression")),
    ("unnecessary rework", ("L4.unnecessary_rework", "L4.plan_asked_move")),
    ("repeated failures", ("L5.repeated_failure_share", "L5.failure_codes")),
    ("recovery/replanning effectiveness", ("L6.recovery_effectiveness",
                                           "L6.recorded_vs_inferred",
                                           "L6.replanning_effectiveness")),
)

#: ids whose value is a count per episode rather than a proportion, so no Wilson interval is
#: defined on them (the same distinction `vlm_contrast` draws for its cost ratios).
RATIO_METRICS = {"L2.violations_per_episode", "L5.failure_codes"}

#: episode fields whose unit changes with the arm or the control. `policy` is here because a
#: decision that names no row leaves every commitment open; `unresolved_obligations` is here
#: because it is the loop's account, not the key's.
ARM_ASYMMETRIC_FIELDS = ("policy", "unresolved_obligations", "open_commitments",
                         "recovery_events", "replan_events", "retry_events")

#: What an empty denominator says mechanically. The arm-specific reading of it — switch or
#: blindness — is a claim about a comparison, so it belongs in `arm_contrast` and in the findings,
#: not here: a reason that is only true on one arm is a false statement in the cell of another.
_NOT_MEASURED = {
    "L1.plan_row_completion": "no plan row in this cell carried a predicate the verifier listed, "
                              "so there was nothing to complete",
    "L1.row_claim_vs_key": "no plan view in this cell published a definite `satisfied` for a "
                           "predicate that a verdict page of the same round listed",
    "L2.dependency_edge_violation": "no dependency edge was authored in this cell; a clean sheet "
                                    "here is the switch on `wo_planning`, elsewhere it is a plan "
                                    "that stated no prerequisite",
    "L2.violations_per_episode": "no dependency edge was authored in any plan view of this cell, "
                                 "so no episode carried one; the count per episode has no "
                                 "numerator to sum, which on `wo_planning` is the switch and "
                                 "elsewhere is a plan that stated no prerequisite",
    "L6.recovery_effectiveness": "no loss event in this cell was followed by another pick or place "
                                 "on the same entity",
    "L6.replanning_effectiveness": "no `plan_revision` event was filed in this cell, so there was "
                                   "no revision whose reopened or added rows could be followed to a "
                                   "`true` verdict",
    "L6.recorded_vs_inferred": "no loss event in this cell was followed by another pick or place on "
                               "the same entity",
}

# ------------------------------------------------------------------ reading ----
def _completed(feedback: dict) -> bool:
    """`completed`, stated so an enum that ever reaches a log as `SkillStatus.completed` cannot
    silently stop matching: the JSON contract says the bare value, and this is the one place the
    difference could change a number."""
    return str(feedback.get("status") or "").split(".")[-1].strip().lower() == "completed"


def _verdict(item: dict) -> str:
    """`true` / `false` / `unknown`, and only those three.

    `unmeasured` non-empty means the verifier had a channel that could not answer, which is a
    different fact from `false`; collapsing the two is how a blind sensor becomes a failed agent.
    """
    if item.get("unmeasured"):
        return "unknown"
    value = str(item.get("value") or "").split(".")[-1].strip().lower()
    return value if value in ("true", "false") else "unknown"


def read_episode(episode_dir: str) -> dict:
    """The log, in one pass, as the shapes this module counts.

    Nothing is re-derived from the scene and nothing is looked up in a case file: the artifact has
    to be reproducible from the directory alone, which is the only form a §12.3 claim about an
    offline score can take.
    """
    path = os.path.join(episode_dir, "events.jsonl")
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    rows.sort(key=lambda r: int(r.get("sequence") or 0))

    samples: list[dict] = []
    feedbacks: list[dict] = []
    injections: list[dict] = []
    plan_events: list[dict] = []
    recoveries: list[dict] = []
    memories: list[dict] = []
    obligation: dict | None = None
    ablation: dict | None = None
    episode_end: dict | None = None
    episode_start: dict | None = None
    summary: dict | None = None
    version_of_ref = {r["payload"]["observation"]["observation_ref"]:
                      r["payload"]["observation"].get("state_version")
                      for r in rows if r["type"] == "observation"
                      and isinstance(r.get("payload", {}).get("observation"), dict)}

    for r in rows:
        kind, payload, seq = r["type"], r.get("payload") or {}, int(r.get("sequence") or 0)
        if kind == "decision_context":
            samples.append({"kind": "round", "sequence": seq,
                            "round_index": payload.get("round_index"),
                            "state_version": payload.get("state_version"),
                            "observation_ref": payload.get("observation_ref"),
                            "verdicts": {str(i.get("predicate_id")): _verdict(i)
                                         for i in payload.get("progress") or []
                                         if i.get("predicate_id")}})
        elif kind == "finish_check":
            refs = [ref for rep in payload.get("reports") or []
                    for ref in rep.get("evidence_refs") or []]
            samples.append({"kind": "finish_check", "sequence": seq, "round_index": None,
                            "state_version": version_of_ref.get(refs[0]) if refs else None,
                            "observation_ref": refs[0] if refs else None,
                            "accepted": payload.get("accepted"),
                            "verdicts": {str(rep.get("predicate_id")): _verdict(rep)
                                         for rep in payload.get("reports") or []
                                         if rep.get("predicate_id")}})
        elif kind == "execution_feedback":
            fb = dict(payload.get("feedback") or {})
            fb["sequence"] = seq
            feedbacks.append(fb)
        elif kind == "environment_event":
            inj = dict(payload.get("injection") or {})
            inj["sequence"] = seq
            inj["phase"] = payload.get("phase")
            injections.append(inj)
        elif kind in ("plan", "plan_revision"):
            view = payload.get("view") or {}
            plan_events.append({"type": kind, "sequence": seq, "trigger": payload.get("trigger"),
                                "version": view.get("version"),
                                "rows": view.get("rows") or [],
                                "dependencies": view.get("dependencies") or [],
                                "violated": view.get("violated") or [],
                                "changed": view.get("changed") or {}})
        elif kind == "recovery_action":
            recoveries.append({**payload, "sequence": seq})
        elif kind == "working_memory":
            memories.append(payload)
        elif kind == "obligation_check":
            obligation = payload
        elif kind == "ablation":
            ablation = payload
        elif kind == "episode_start":
            episode_start = payload
        elif kind == "episode_end":
            episode_end = payload

    summary_path = os.path.join(episode_dir, "episode_summary.json")
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as fh:
            summary = json.load(fh)
    return {"episode_dir": episode_dir,
            "episode_id": (episode_end or {}).get("episode_id")
                          or os.path.basename(episode_dir.rstrip("/")),
            "case_id": (episode_start or {}).get("task_id"),
            "mode": (episode_start or {}).get("mode"),
            "types": sorted({r["type"] for r in rows}), "rows": rows,
            "samples": samples, "feedbacks": feedbacks, "injections": injections,
            "plan_events": plan_events, "recoveries": recoveries, "memories": memories,
            "obligation": obligation, "ablation": ablation,
            "episode_start": episode_start, "episode_end": episode_end, "summary": summary}


# ---------------------------------------------------------------- predicates ----
def _split(predicate_id: str) -> tuple[str, str]:
    """`placed:<entity>:<target>` -> (entity, target); anything else -> ("", "").

    Only `placed:` has a verdict in the progress list and therefore a timeline. A `clear:` or
    `maintain:` row is the plan's own business and is counted through L1/L2, never as a relation
    the world flipped.
    """
    parts = str(predicate_id or "").split(":")
    if len(parts) == 3 and parts[0] == "placed":
        return parts[1], parts[2]
    return "", ""


def _series(samples: list[dict]) -> dict[str, list[tuple[int, str]]]:
    """predicate -> [(sample index, verdict)], in log order, with `unknown` kept as a hole."""
    out: dict[str, list[tuple[int, str]]] = {}
    for index, sample in enumerate(samples):
        for pid, verdict in sample["verdicts"].items():
            out.setdefault(pid, []).append((index, verdict))
    return {pid: sorted(seq) for pid, seq in out.items()}


def _definite(series: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(i, v) for i, v in series if v in ("true", "false")]


def _last(series: list[tuple[int, str]]) -> str:
    return series[-1][1] if series else "unknown"


def _transitions(series: list[tuple[int, str]]) -> list[tuple[int, int]]:
    """(index_of_true, index_of_false) pairs, `unknown` samples skipped over.

    Skipping the holes is the conservative direction: a sensor that could not answer between two
    definite readings did not observe a flip, and counting its silence as one would inflate the
    regression rate on exactly the arm whose perception is weakest.
    """
    definite = _definite(series)
    return [(definite[n][0], definite[n + 1][0]) for n in range(len(definite) - 1)
            if definite[n][1] == "true" and definite[n + 1][1] == "false"]


def _cause_window(ep: dict, samples: list[dict], lo: int, hi: int | None,
                  entity: str) -> dict:
    """What the log says moved `entity` between two measurements, as two booleans.

    Returned separately rather than as one label because an interval can carry both signals, and
    the honest answer to that is `ambiguous`, not a coin toss. `hi=None` runs the window to the
    end of the record: an open-ended question ("has anything explained this since?") is not the
    same call as a bounded one, and passing `len(samples)` as if it were an index is an
    IndexError waiting for the first episode that has a re-place to explain.
    """
    seq_lo, seq_hi = samples[lo]["sequence"], (float("inf") if hi is None
                                               else samples[hi]["sequence"])
    agent_direct = agent_knock = environment = False
    for fb in ep["feedbacks"]:
        if not seq_lo < fb["sequence"] < seq_hi:
            continue
        diff = fb.get("state_diff") or {}
        moved = {str(m.get("entity_id")) for m in diff.get("moved") or []}
        moved |= {str(m.get("entity_id")) for m in diff.get("state_changed") or []}
        touched = {str(a) for a in fb.get("affected_entities") or []} | moved
        if str(fb.get("entity_id") or "") == entity and str(fb.get("skill") or "") in ("pick",
                                                                                       "place"):
            agent_direct = True
        elif entity in touched:
            agent_knock = True
    for inj in ep["injections"]:
        if seq_lo < inj["sequence"] < seq_hi and str(inj.get("entity_id") or "") == entity:
            environment = True
    return {"agent_direct": agent_direct, "agent_knock": agent_knock, "environment": environment}


# ------------------------------------------------------------------ counting ----
def _count(counts: dict[str, list[int]], metric: str, num: int = 0, den: int = 0) -> None:
    """Accumulate one contribution. An id with no definition refuses to be counted — a number
    nobody defined is a number that will be misread."""
    if metric not in METRIC_DEFINITIONS:
        raise KeyError(f"{metric!r} has no entry in METRIC_DEFINITIONS")
    slot = counts.setdefault(metric, [0, 0])
    slot[0] += int(num)
    slot[1] += int(den)


def _satisfied(row: dict) -> str:
    """`true` / `false` / `unknown` for a row's own published claim.

    Both `False` the bool and `'False'` the string have to read as `false`. The view serialises
    `str(bool)` today, and `False or ""` is `""` — a plain-or would turn every unmet row into a
    non-claim, which is the `str(SkillStatus)` trap one field over.
    """
    value = row.get("satisfied")
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value if value is not None else "").split(".")[-1].strip().lower()
    return text if text in ("true", "false") else "unknown"


def _page_after(samples: list[dict], sequence: int) -> dict | None:
    """The verdict page of the round a record was written for: the first `decision_context` at or
    after it. A plan view is authored before its round's context and both read one world state,
    so this pairing is an instant, not a lag."""
    return next((s for s in samples if s["kind"] == "round" and s["sequence"] >= sequence), None)


def _row_claims(plan_events: list[dict], samples: list[dict]) -> list[dict]:
    """Every definite row claim, audited against the verdict page of its own round.

    Auditing against the episode's final verdict instead — what the first version of this did —
    charges the plan for every disturbance that happened after it spoke, and calls a correct
    self-report a lie.
    """
    out: list[dict] = []
    for event in plan_events:
        page = _page_after(samples, event["sequence"])
        if page is None:
            continue
        for row in event["rows"]:
            pid = str(row.get("predicate_id") or "")
            claim = _satisfied(row)
            if claim == "unknown" or pid not in page["verdicts"]:
                continue
            out.append({"row_key": str(row.get("row_key") or ""), "predicate_id": pid,
                        "kind": str(row.get("kind") or ""),
                        "plan_sequence": event["sequence"], "plan_version": event.get("version"),
                        "round_index": page.get("round_index"),
                        "state_version": page.get("state_version"),
                        "claim": claim, "verdict": page["verdicts"][pid],
                        "disagree": claim != page["verdicts"][pid]})
    return out


def _loss_events(ep: dict, samples: list[dict], series: dict[str, list[tuple[int, str]]],
                 transitions: list[dict]) -> list[dict]:
    """Every point where the log says work was lost, from either of two directions.

    The visible one is a `true`→`false` transition of a goal predicate. The other is an executed
    `place` whose own record says `completed` while the verifier's next reading of that pair still
    says `false`: the handoff lost between the gripper and the measurement, which cannot appear as
    a transition because the relation was never true to begin with. Only the first is a §11
    regression; both are recovery opportunities, and a denominator built from transitions alone
    measures none of the second.
    """
    out: list[dict] = []
    for t in transitions:
        out.append({"kind": "regression", "predicate_id": t["predicate_id"],
                    "entity_id": t["entity_id"], "target_id": t["target_id"],
                    "at_sequence": samples[t["to_index"]]["sequence"],
                    "at_round": samples[t["to_index"]].get("round_index"),
                    "environment": t["environment"] or t["ambiguous"]})
    for fb in ep["feedbacks"]:
        if str(fb.get("skill") or "") != "place" or not fb.get("executed") or not _completed(fb):
            continue
        entity, target = str(fb.get("entity_id") or ""), str(fb.get("target_id") or "")
        pid = f"placed:{entity}:{target}"
        reading = next((s for s in samples if s["sequence"] > fb["sequence"] and pid in s["verdicts"]),
                       None)
        if reading is None or reading["verdicts"][pid] != "false":
            continue
        if any(v == "true" for i, v in (series.get(pid) or [])
               if samples[i]["sequence"] <= fb["sequence"]):
            continue  # it was true, then this place moved it: that is a relocation, not a loss
        out.append({"kind": "unverified_handoff", "predicate_id": pid, "entity_id": entity,
                    "target_id": target, "at_sequence": fb["sequence"],
                    "at_round": fb.get("round_index"),
                    "environment": any(i["sequence"] <= fb["sequence"]
                                       and str(i.get("entity_id") or "") == entity
                                       for i in ep["injections"])})
    return sorted(out, key=lambda d: (d["at_sequence"], d["predicate_id"]))


def score_episode(episode_dir: str) -> dict:
    """One episode, scored from its log. Returns counts, the evidence behind them, and the
    findings a reader needs to interpret a number that looks wrong."""
    ep = read_episode(episode_dir)
    samples, series = ep["samples"], _series(ep["samples"])
    counts: dict[str, list[int]] = {}
    findings: list[str] = []
    detail: dict[str, Any] = {}

    rounds = [s for s in samples if s["kind"] == "round"]
    goal_pids = sorted({pid for s in rounds for pid in s["verdicts"] if _split(pid)[0]})
    goal_targets: dict[str, set[str]] = {}
    for pid in goal_pids:
        entity, target = _split(pid)
        goal_targets.setdefault(entity, set()).add(target)

    # ---- L1: completion, from the verifier's list, and the plan's own claim audited against it
    finals = {pid: _last(series.get(pid) or []) for pid in goal_pids}
    _count(counts, "L1.assignment_completion",
           sum(1 for v in finals.values() if v == "true"), len(goal_pids))
    _count(counts, "L1.unmeasured_at_end",
           sum(1 for v in finals.values() if v == "unknown"), len(goal_pids))

    last_view: dict[str, dict] = {}
    predicates: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for event in ep["plan_events"]:
        for row in event["rows"]:
            key = str(row.get("row_key") or "")
            if not key:
                continue
            last_view[key] = row
            predicates[key] = str(row.get("predicate_id") or "")
            kinds[key] = str(row.get("kind") or "")
    rows_with_verdict = [k for k in last_view if predicates[k] in finals]
    _count(counts, "L1.plan_row_completion",
           sum(1 for k in rows_with_verdict if finals[predicates[k]] == "true"),
           len(rows_with_verdict))
    claims = _row_claims(ep["plan_events"], samples)
    _count(counts, "L1.row_claim_vs_key", sum(1 for c in claims if c["disagree"]), len(claims))
    detail["row_claims"] = claims
    if claims:
        disagree = [c for c in claims if c["disagree"]]
        if disagree:
            examples = [(c["row_key"], c["claim"], c["verdict"]) for c in disagree[:4]]
            findings.append(
                f"{len(disagree)} of {len(claims)} definite row claim(s) contradicted the verdict "
                f"page of their own round: {examples} — the plan and the verifier read one world "
                f"state in one round, so this is a loop contradicting itself, not a stale "
                f"statement")
    elif ep["plan_events"]:
        versions = [e.get("version") for e in ep["plan_events"]]
        findings.append(
            f"the plan published {len(ep['plan_events'])} view(s) (version(s) {versions}) and not "
            f"one definite row claim that a verdict page could be paired with: every `satisfied` "
            f"was still `unknown` when it was written, so L1.row_claim_vs_key is an instrument gap "
            f"on this cell, not a clean self-report")
    dropped = (sorted(set(kinds) - {str(r.get("row_key") or "")
                                    for r in ep["plan_events"][-1]["rows"]})
               if ep["plan_events"] else [])
    # Only a row whose predicate is on the verdict timeline can be judged at all: a `pick` row or
    # a `clear:` row names no goal the verifier lists, and "it never became true" would be a
    # statement about the sensor, not about the work.
    lost_work = sorted(k for k in dropped
                       if _split(predicates.get(k) or "")[0]
                       and finals.get(predicates[k], "unknown") != "true")
    unjudgeable = sorted(k for k in dropped if not _split(predicates.get(k) or "")[0])
    if lost_work:
        findings.append(f"{len(lost_work)} row(s) left the plan while their predicate was not "
                        f"true: {lost_work[:6]} — §5.2's work object cannot shrink by forgetting")
    if unjudgeable:
        findings.append(f"{len(unjudgeable)} row(s) left the plan naming no goal predicate the "
                        f"verifier lists, so their loss is not judgeable from this log "
                        f"(reported, not counted): {unjudgeable[:6]}")
    detail["goal_predicates"] = {pid: finals[pid] for pid in goal_pids}
    detail["final_verdicts"] = {"n": len(samples), "episodes_with_finish_check":
                                any(s["kind"] == "finish_check" for s in samples)}

    # ---- L2: the plan's own graph, per episode and per distinct edge
    edges: dict[tuple[str, str, str], bool] = {}
    unparsed_violations: set[str] = set()
    for event in ep["plan_events"]:
        for dep in event["dependencies"]:
            key = (str(dep.get("from_subgoal_id") or dep.get("from") or ""),
                   str(dep.get("on_subgoal_id") or dep.get("on") or ""),
                   str(dep.get("kind") or ""))
            edges[key] = edges.get(key, False) or bool(dep.get("violated"))
        for item in event.get("violated") or []:
            name = str(item)
            if not any(name in json.dumps(dep) for dep in event["dependencies"]):
                unparsed_violations.add(name)
    _count(counts, "L2.dependency_edge_violation", sum(1 for v in edges.values() if v), len(edges))
    if edges:
        _count(counts, "L2.violations_per_episode",
               sum(1 for v in edges.values() if v), 1)
    if unparsed_violations:
        findings.append(f"{len(unparsed_violations)} violated-edge name(s) published in "
                        f"`view.violated` matched no edge in any `view.dependencies`; they are not "
                        f"counted in L2 (list: {sorted(unparsed_violations)[:4]})")
    detail["dependency_edges"] = [f"{f}->{o}:{k}" for (f, o, k) in sorted(edges)]
    detail["row_kinds"] = dict(Counter(kinds.values()))
    if ep["plan_events"] and not edges:
        findings.append(
            f"{len(ep['plan_events'])} plan view(s) published {sum(len(e['rows']) for e in ep['plan_events'])} "
            f"row(s) and no dependency edge: §11's dependency row has no denominator on this "
            f"episode. `subgoals._capacity_dependencies` asks for a region whose feasible slot "
            f"count is smaller than the objects still owed to it, and `SHAPE_2_MEASURED` measured "
            f"the trays of this scene at 6-47 clear slots per victim at this object scale, so the "
            f"question is never asked. Reported as not measurable here, not as zero violations")

    # ---- L3: every true→false flip, and who the record says did it
    transitions: list[dict] = []
    ever_true = [pid for pid, seq in series.items() if any(v == "true" for _, v in seq)]
    for pid in ever_true:
        entity, target = _split(pid)
        if not entity:
            continue
        for lo, hi in _transitions(series[pid]):
            cause = _cause_window(ep, samples, lo, hi, entity)
            later = [v for i, v in series[pid] if i > hi]
            agent = bool(cause["agent_direct"] or cause["agent_knock"])
            transitions.append({
                "predicate_id": pid, "entity_id": entity, "target_id": target,
                "from_index": lo, "to_index": hi,
                "from_round": samples[lo].get("round_index"),
                "to_round": samples[hi].get("round_index"),
                "from_ref": samples[lo].get("observation_ref"),
                "to_ref": samples[hi].get("observation_ref"),
                "agent": agent,
                "agent_direct": cause["agent_direct"], "knock_on": cause["agent_knock"],
                "environment": cause["environment"],
                "ambiguous": agent and cause["environment"],
                "recovered": "true" in later})
    _count(counts, "L3.regression_rate",
           len({t["predicate_id"] for t in transitions}), len(ever_true))
    _count(counts, "L3.agent_caused_regression",
           sum(1 for t in transitions if t["agent"] and not t["environment"]), len(transitions))
    _count(counts, "L3.environment_caused_regression",
           sum(1 for t in transitions if t["environment"] and not t["agent"]), len(transitions))
    _count(counts, "L3.ambiguous_regression", sum(1 for t in transitions if t["ambiguous"]),
           len(transitions))
    _count(counts, "L3.unrecovered_regression",
           sum(1 for t in transitions if not t["recovered"]), len(transitions))
    detail["transitions"] = transitions
    if transitions and not ep["injections"]:
        findings.append(f"{len(transitions)} regression(s) in an episode that recorded no "
                        f"environment event: every one of them is the agent's own doing")

    # ---- L4: rework the record gives no reason for, and the detour the plan did ask for
    places = [fb for fb in ep["feedbacks"] if str(fb.get("skill") or "") == "place"
              and fb.get("executed")]
    rework: list[dict] = []
    asked_elsewhere: list[dict] = []
    for fb in places:
        entity, target = str(fb.get("entity_id") or ""), str(fb.get("target_id") or "")
        pid = f"placed:{entity}:{target}"
        prior_true = [i for i, v in (series.get(pid) or [])
                      if v == "true" and samples[i]["sequence"] < fb["sequence"]]
        if _completed(fb) and target in (goal_targets.get(entity) or set()) and prior_true:
            lo = prior_true[-1]
            cause = _cause_window(ep, samples, lo, None, entity)
            picked = any(str(f.get("skill") or "") == "pick"
                         and str(f.get("entity_id") or "") == entity
                         and samples[lo]["sequence"] < f["sequence"] < fb["sequence"]
                         for f in ep["feedbacks"])
            if not cause["environment"] and not picked:
                rework.append({"predicate_id": pid, "decision_id": fb.get("decision_id"),
                               "since_ref": samples[lo].get("observation_ref"),
                               "since_round": samples[lo].get("round_index")})
        if _completed(fb) and target not in (goal_targets.get(entity) or set()):
            asked_elsewhere.append({"entity_id": entity, "target_id": target,
                                    "decision_id": fb.get("decision_id")})
    _count(counts, "L4.unnecessary_rework", len(rework), len(places))
    _count(counts, "L4.plan_asked_move", len(asked_elsewhere), len(places))
    detail["unnecessary_rework"] = rework
    detail["plan_asked_moves"] = asked_elsewhere

    # ---- L5: attempts, and the ones that repeat an attempt that had not succeeded
    seen_not_completed: set[tuple[str, str, str]] = set()
    repeats: list[dict] = []
    attempts = [fb for fb in ep["feedbacks"] if str(fb.get("skill") or "")]
    for fb in attempts:
        key = (str(fb.get("skill")), str(fb.get("entity_id") or ""), str(fb.get("target_id") or ""))
        if key in seen_not_completed:
            repeats.append({"skill": key[0], "entity_id": key[1], "target_id": key[2],
                            "status": str(fb.get("status")), "failure_code": fb.get("failure_code")})
        if not (fb.get("executed") and _completed(fb)):
            seen_not_completed.add(key)
    _count(counts, "L5.repeated_failure_share", len(repeats), len(attempts))
    codes: dict[str, int] = {}
    for fb in attempts:
        if not (fb.get("executed") and _completed(fb)):
            codes[str(fb.get("failure_code") or "no_code_recorded")] = \
                codes.get(str(fb.get("failure_code") or "no_code_recorded"), 0) + 1
    detail["repeated_failures"] = repeats
    detail["failure_codes"] = codes
    detail["rejections"] = sum(1 for fb in attempts if not fb.get("executed"))

    # ---- L6: recovery, measured from the behaviour so an arm that files no record is still
    #      measured; and the record count published beside it as the instrument's own audit.
    losses = _loss_events(ep, samples, series, transitions)
    recoveries: list[dict] = []
    unrepaired: list[dict] = []
    for loss in losses:
        after = sorted((f for f in ep["feedbacks"]
                        if f["sequence"] > loss["at_sequence"]
                        and str(f.get("skill") or "") in ("pick", "place")
                        and str(f.get("entity_id") or "") == loss["entity_id"]),
                       key=lambda f: f["sequence"])
        if not after:
            unrepaired.append(loss)
            continue
        first_seq = after[0]["sequence"]
        later = [v for i, v in series.get(loss["predicate_id"], [])
                 if samples[i]["sequence"] > first_seq]
        filed = [r for r in ep["recoveries"]
                 if str((r.get("args") or {}).get("object_id") or "") == loss["entity_id"]
                 and loss["at_sequence"] < r["sequence"] <= first_seq]
        recoveries.append({"kind": loss["kind"], "predicate_id": loss["predicate_id"],
                           "entity_id": loss["entity_id"], "target_id": loss["target_id"],
                           "at_sequence": loss["at_sequence"], "at_round": loss.get("at_round"),
                           "environment": loss["environment"],
                           "recovered": "true" in later,
                           "recorded": bool(filed),
                           "record_kinds": sorted({str(r.get("choice")) for r in filed}),
                           "first_action": after[0].get("skill")})
    _count(counts, "L6.recovery_effectiveness",
           sum(1 for r in recoveries if r["recovered"]), len(recoveries))
    _count(counts, "L6.recorded_vs_inferred",
           sum(1 for r in recoveries if r["recorded"]), len(recoveries))
    detail["recoveries"] = recoveries
    detail["loss_events"] = losses
    if unrepaired:
        examples = [(u["kind"], u["predicate_id"]) for u in unrepaired[:4]]
        findings.append(f"{len(unrepaired)} loss event(s) were never followed by another pick or "
                        f"place on that entity: {examples} — outside L6's denominator by "
                        f"definition, and inside the record as work that was lost and left lost")
    if ep["recoveries"] and not losses:
        findings.append(
            f"{len(ep['recoveries'])} `recovery_action` record(s) with no loss event in the "
            f"timeline to attach to: the loop recorded a recovery that neither a verdict flip nor "
            f"an unverified handoff supports. One of the two instruments is wrong, and L6's "
            f"denominator is the place it would show up")

    revisions = [e for e in ep["plan_events"] if e["type"] == "plan_revision"]
    effective = 0
    for event in revisions:
        named = sorted({str(k) for field in ("added", "reopened", "relabelled")
                        for k in (event["changed"].get(field) or [])})
        if not named:
            continue
        pid_of = {str(r.get("row_key") or ""): str(r.get("predicate_id") or "")
                  for e in ep["plan_events"] for r in e["rows"]}
        if any("true" in [v for i, v in series.get(pid_of.get(k, ""), [])
                          if samples[i]["sequence"] > event["sequence"]] for k in named):
            effective += 1
    _count(counts, "L6.replanning_effectiveness", effective, len(revisions))
    detail["revisions"] = [{"version": e["version"], "trigger": e["trigger"],
                            "changed": e["changed"], "sequence": e["sequence"]}
                           for e in revisions]

    # ---- the arm's own claim, re-checked here rather than trusted
    claim = ep["ablation"]
    s4_types = sorted({r["type"] for r in ep["rows"]
                       if r.get("schema_version") == SCHEMA_VERSION})
    violations: list[str] = []
    unknown_types: list[str] = []
    if s4_types:
        try:
            arm = Ablation(condition=str((claim or {}).get("condition") or "full"),
                           modules_off=list((claim or {}).get("modules_off") or []))
            violations = list(arm.violations(s4_types))
        except ValueError as e:  # a type nobody attributed is itself the finding
            unknown_types = [str(e)]
    unknown_types += sorted(set(s4_types) - set(V02_EVENT_TYPES))
    if violations:
        findings.append(f"the arm claimed {claim.get('condition')!r} with modules off "
                        f"{list(claim.get('modules_off') or [])} and produced records owned by "
                        f"them: {violations}")
    if unknown_types:
        findings.append(f"unattributed v0.2 record type(s): {unknown_types}")

    result = dict((ep["episode_end"] or {}).get("result") or {})
    obligation = ep["obligation"] or {}
    return {
        "episode_id": ep["episode_id"], "case_id": ep["case_id"], "mode": ep["mode"],
        "metric_version": METRIC_VERSION,
        "arm": (ep["summary"] or {}).get("perception", {}).get("arm") or "unset",
        "channel": (ep["summary"] or {}).get("perception", {}).get("channel"),
        "policy": ((ep["summary"] or {}).get("decision_source") or {}).get("policy", "rule"),
        "counts": {k: [int(v[0]), int(v[1])] for k, v in sorted(counts.items())},
        "metrics": _render(counts),
        "detail": detail,
        "instrument": {
            "samples": len(samples), "rounds": len(rounds), "attempts": len(attempts),
            "executed_places": len(places), "injections": len(ep["injections"]),
            "plan_events": len(ep["plan_events"]), "recovery_records": len(ep["recoveries"]),
            "loss_events": len(losses), "loss_kinds":
                sorted({str(loss["kind"]) for loss in losses}),
            "unrepaired_loss_events": len(unrepaired),
            "definite_row_claims": len(claims),
            "working_memory_records": len(ep["memories"]),
            "obligation_check": bool(ep["obligation"]),
            "ablation_record": bool(claim), "condition": (claim or {}).get("condition"),
            "modules_off": list((claim or {}).get("modules_off") or []),
            "v02_record_types": s4_types, "event_types": ep["types"],
            "arm_claim_violations": violations,
            "asymmetric_fields": list(ARM_ASYMMETRIC_FIELDS)},
        "loop_counters": {k: result.get(k) for k in
                          ("decision_rounds", "skill_calls", "http_requests", "terminal_status",
                           "failure_type", "termination_reason", "objects_completed",
                           "objects_total", "recovery_events", "retry_events", "replan_events",
                           "rejected_decisions", "false_finish_attempts",
                           "identical_invalid_attempts", "identical_repeats_total",
                           "slot_resolution_fallbacks")},
        "obligation_check": {k: obligation.get(k) for k in
                             ("status", "terminal_status", "unresolved_obligations",
                              "owed_subgoals", "unrestored_relations", "invalid_completion",
                              "plan_events", "revision_events", "recovery_events",
                              "sections_seen", "unknown_facts", "failures_never_answered",
                              "invalid_assumptions_acted_on") if obligation},
        "independent_score": (ep["summary"] or {}).get("score") or {},
        "findings": findings,
        "episode_dir": episode_dir,
    }


def _rate(metric: str, numerator: int, denominator: int, **extra) -> dict[str, Any]:
    """A fraction that carries its own definition, and no interval where an interval is not
    defined (`RATIO_METRICS` are counts per episode, not proportions)."""
    d = METRIC_DEFINITIONS[metric]
    n, dn = int(numerator), int(denominator)
    if metric in RATIO_METRICS:
        interval, kind = None, "count_per_episode"
    else:
        if n > dn:
            raise AssertionError(f"{metric}: a proportion cannot have numerator {n} > "
                                 f"denominator {dn}; the counting site is wrong")
        interval = list(wilson_interval(n, dn)) if dn else None
        kind = "proportion"
    return {"metric": metric, "numerator": n, "denominator": dn,
            "value": (round(n / dn, 4) if dn else None),
            "wilson_95": interval, "interval_kind": kind,
            "measured": bool(dn),
            "not_measured_reason": (None if dn else _NOT_MEASURED.get(metric,
                                                                       "no unit fell in this "
                                                                       "cell's denominator")),
            "denominator_too_small_for_a_rate": 0 < dn < 10,
            "spec_row": next((row for row, ids in SPEC_ROWS if metric in ids), None),
            "question": d["question"], "unit": d["unit"],
            "as_computed": f"{d['numerator']} / {d['denominator']}", **extra}


def _render(counts: dict[str, list[int]]) -> dict[str, dict[str, Any]]:
    return {k: _rate(k, *v) for k, v in sorted(counts.items())}


def _merge(target: dict[str, list[int]], counts: dict[str, list[int]]) -> None:
    for k, (n, d) in counts.items():
        slot = target.setdefault(k, [0, 0])
        slot[0] += int(n)
        slot[1] += int(d)


# --------------------------------------------------------------------- runs ----
def _episode_dirs(run_dir: str) -> list[str]:
    root = os.path.join(run_dir, "episodes")
    if not os.path.isdir(root):
        return []
    return sorted(os.path.join(root, d) for d in os.listdir(root)
                  if os.path.exists(os.path.join(root, d, "events.jsonl")))


def score_run(run_dir: str) -> dict:
    """Every episode of one batch, pooled.

    Pooling is by summing the integers of each cell's numerators and denominators, never by
    averaging the per-episode fractions: four episodes of a 4-object task and one of an 8-object
    task are not five equal measurements of completion, and the mean of their rates would say they
    were. `counts` is kept next to `metrics` so the sums a value came from are in the artifact.
    """
    with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    episodes = [score_episode(d) for d in _episode_dirs(run_dir)]
    by_cell: dict[str, dict] = {}
    for episode in episodes:
        cell = f"{episode['arm']}/{episode['policy']}"
        block = by_cell.setdefault(cell, {"arm": episode["arm"], "policy": episode["policy"],
                                          "channel": episode["channel"], "counts": {},
                                          "episodes": [], "failure_codes": {},
                                          "row_kinds": {}})
        _merge(block["counts"], episode["counts"])
        block["episodes"].append(episode["episode_id"])
        for code, n in (episode["detail"]["failure_codes"] or {}).items():
            block["failure_codes"][code] = block["failure_codes"].get(code, 0) + n
        for kind, n in (episode["detail"]["row_kinds"] or {}).items():
            block["row_kinds"][kind] = block["row_kinds"].get(kind, 0) + n
    for block in by_cell.values():
        block["metrics"] = _render(block["counts"])
        block["n_episodes"] = len(block["episodes"])
    return {"run_dir": run_dir, "run_id": manifest.get("run_id"),
            "perception": manifest.get("perception"),
            "decision_source": manifest.get("decision_source"),
            "cases": manifest.get("cases"), "modes": manifest.get("modes"),
            "code": manifest.get("code"),
            "episodes": [{k: episode[k] for k in
                          ("episode_id", "case_id", "arm", "policy", "counts", "findings",
                           "instrument", "loop_counters", "obligation_check",
                           "independent_score", "episode_dir")}
                         for episode in episodes],
            "pools": by_cell,
            "all_findings": {episode["episode_id"]: episode["findings"]
                             for episode in episodes if episode["findings"]}}


def arm_contrast(scored: list[dict], reference: str = "full") -> dict:
    """Every metric of every arm, read against the same metric of the reference arm.

    A cell the reference measured and this one could not is reported as `not_measured_on`, never as
    a difference of zero: the whole point of §9's planning arms is that the switch removes the
    records some of these rates are computed from.
    """
    out: dict[str, Any] = {"reference": reference, "cells": {}, "differences": []}
    for run in scored:
        for cell, block in sorted(run["pools"].items()):
            out["cells"][f"{os.path.basename(run['run_dir'])}::{cell}"] = {
                "arm": block["arm"], "policy": block["policy"],
                "n_episodes": block["n_episodes"],
                "metrics": {k: {"value": m["value"], "numerator": m["numerator"],
                                "denominator": m["denominator"],
                                "not_measured_reason": m["not_measured_reason"]}
                            for k, m in sorted(block["metrics"].items())}}
    by_arm: dict[tuple[str, str], dict] = {}
    for run in scored:
        for cell, block in run["pools"].items():
            by_arm[(block["arm"], block["policy"])] = block
    for (arm, policy), block in sorted(by_arm.items()):
        if arm == reference:
            continue
        base = by_arm.get((reference, policy))
        for metric, mine in sorted(block["metrics"].items()):
            theirs = (base or {}).get("metrics", {}).get(metric)
            if theirs is None:
                out["differences"].append({"arm": arm, "policy": policy, "metric": metric,
                                           "reference_measured": False,
                                           "note": f"{reference!r} has no cell under this policy"})
                continue
            both = mine["measured"] and theirs["measured"]
            out["differences"].append({
                "arm": arm, "policy": policy, "metric": metric,
                "reference": theirs["value"], "value": mine["value"],
                "delta": (round(mine["value"] - theirs["value"], 4) if both else None),
                "reference_denominator": theirs["denominator"],
                "denominator": mine["denominator"],
                "comparable": both,
                "not_measured_on": (None if both else
                                    [n for n, m in ((arm, mine), (reference, theirs))
                                     if not m["measured"]]),
                "asymmetric": metric.startswith(("L1.row", "L6.recorded"))})
    return out


# ------------------------------------------------------------------- output ----
def _fmt(value) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_markdown(result: dict) -> str:
    lines = [f"# 11 Long-horizon behavior, scored offline (口径 {result['metric_version']})", ""]
    from .long_horizon_tasks import SHAPE_2_MEASURED
    lines += [f"- scene: 8 objects / 3 regions max; {result['n_runs']} batch(es), "
              f"{result['n_episodes']} episodes",
              f"- §8 shape 2 (reach past an occupant): **not_applicable** — "
              f"{SHAPE_2_MEASURED['verdict']}; {SHAPE_2_MEASURED['consequence']}", ""]
    for run in result["runs"]:
        lines += [f"## {run['run_id']}", "",
                  f"- arm `{(run['perception'] or {}).get('arm')}`, channel "
                  f"`{(run['perception'] or {}).get('channel')}`, control policy "
                  f"`{(run['decision_source'] or {}).get('policy')}`, "
                  f"{len(run['episodes'])} episode(s)", ""]
        for cell, block in sorted(run["pools"].items()):
            lines += [f"**{cell}** — {block['n_episodes']} episode(s)", "",
                      "| metric | value | num/den | 95% Wilson |", "| --- | --- | --- | --- |"]
            for metric, m in sorted(block["metrics"].items()):
                interval = ("—" if not m["wilson_95"]
                            else f"{m['wilson_95'][0]:.3f}–{m['wilson_95'][1]:.3f}")
                note = "" if m["measured"] else f" ({m['not_measured_reason']})"
                lines.append(f"| `{metric}` | {_fmt(m['value'])} | {m['numerator']}/{m['denominator']} "
                             f"| {interval} |{note}")
            lines += [f"- loop counters (v0.1, reported beside the rates, never inside them): "
                      f"{block['loop_summary']}",
                      f"- failure codes: {block['failure_codes'] or 'none'}",
                      f"- plan rows summed over episodes, by row kind (the population the "
                      f"`L2.dependency_edge_violation` denominator is *not* taken from): "
                      f"{block.get('row_kinds') or 'no plan rows'}",
                      f"- findings: {len(block['finding_ids'])} episode(s) with one; see JSON", ""]
    if result.get("contrast"):
        contrast = result["contrast"]
        lines += [f"## contrast against `{contrast['reference']}`", "",
                  "| arm | policy | metric | full | arm | Δ | num/den | comparable |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for d in contrast["differences"]:
            lines.append(f"| `{d['arm']}` | {d['policy']} | `{d['metric']}` | {_fmt(d.get('reference'))} "
                         f"| {_fmt(d.get('value'))} | {_fmt(d.get('delta'))} | "
                         f"{d.get('numerator', '—')}/{d.get('denominator', '—')} | "
                         f"{'yes' if d.get('comparable') else 'no'} |")
        lines += ["", "A row reading `not measured` on one side is the switch removing the records "
                  "that metric is computed from, not a zero and not a difference.", ""]
    return "\n".join(lines) + "\n"


def measure(run_dirs: list[str], *, reference: str = "full") -> dict:
    """The artifact: every batch scored, pooled, contrasted, and carrying its own definitions."""
    from .long_horizon_tasks import SHAPE_2_MEASURED
    scored = [score_run(d) for d in run_dirs]
    for run in scored:
        for block in run["pools"].values():
            ids = set(block["episodes"])
            episodes = [e for e in run["episodes"] if e["episode_id"] in ids]
            block["loop_summary"] = {
                key: [e["loop_counters"].get(key) for e in episodes]
                for key in ("decision_rounds", "objects_completed", "objects_total",
                            "recovery_events", "replan_events", "rejected_decisions",
                            "false_finish_attempts")}
            block["finding_ids"] = [e["episode_id"] for e in episodes if e["findings"]]
    return {"metric_version": METRIC_VERSION,
            "definitions": METRIC_DEFINITIONS,
            "spec_rows": {row: list(ids) for row, ids in SPEC_ROWS},
            # §8's second shape is not a rate of 0.0 on this set, and the JSON has to say so
            # itself: a reader of one file must not need the .md to learn the difference between
            # "no violation happened" and "no episode in which one could happen was authored".
            "spec8_shape_2": {"status": "not_applicable",
                              "measured_as": SHAPE_2_MEASURED["verdict"],
                              "consequence": SHAPE_2_MEASURED["consequence"],
                              "probes": list(SHAPE_2_MEASURED["probes"])},
            "asymmetric_fields": list(ARM_ASYMMETRIC_FIELDS),
            "not_measured_policy": ("a metric whose denominator is the records an arm does not "
                                    "file is reported as `not_measured` with the reason, never as "
                                    "0.0; see the module docstring, decision 1"),
            "n_runs": len(scored), "n_episodes": sum(len(r["episodes"]) for r in scored),
            "runs": scored, "contrast": arm_contrast(scored, reference=reference)}


def write(result: dict, out_dir: str, name: str = "long_horizon_v0") -> str:
    os.makedirs(out_dir, exist_ok=True)
    for suffix, payload in (("json", result), ("md", render_markdown(result))):
        path = os.path.join(out_dir, f"{name}.{suffix}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=1, default=str)
                     if suffix == "json" else payload)
    return os.path.join(out_dir, f"{name}.json")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="score 11's Long-horizon group from run directories (offline, read-only)")
    parser.add_argument("runs", nargs="+", help="run directories (each with episodes/ and "
                                                "manifest.json)")
    parser.add_argument("--out", default=None, help="write long_horizon_v0.{json,md} here")
    parser.add_argument("--reference", default="full")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the markdown instead of writing artifacts")
    args = parser.parse_args(argv)
    result = measure([os.path.abspath(r) for r in args.runs], reference=args.reference)
    if args.print_only or not args.out:
        print(render_markdown(result))
    if args.out:
        print(write(result, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
