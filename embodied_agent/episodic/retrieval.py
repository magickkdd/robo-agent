"""Relevance-filtered retrieval over the store, and the negative-transfer guard (SPEC-v0.2 §5.4).

§5.4's second sentence is the constraint that makes this a research component rather than a lookup:
检索结果作为模型参考，不直接改写 WorldState. So the output here is a list of
`core/v02.py:RecalledExperience` rows — each carrying *why* it was chosen (`relevance`,
`match_terms`) and, after the round, whether it was used (`used_by_model`) — and every one of them
is a claim about the past, never an assertion about the present world.

Four decisions carry the weight, and each is a test:

1. **Relevance is a declared formula over recorded fields**, not a similarity vibe: the weights
   are `RELEVANCE_WEIGHTS`, the query terms come from `query_from`, and a row's score is a sum of
   matched weights. A row scoring zero is not returned, so "nothing relevant" is an empty list and
   cannot be read as "retrieved something and ignored it".
2. **Order is deterministic**: relevance, then `experience_id` ascending. Not `hash()` — P2
   learned that a hash-derived order gives two different trajectories from one payload in two
   processes, and a memory that reorders its own ties is no more reproducible than a plan that
   does.
3. **The guard measures, or says nothing.** `contradicted_current_state` is set only when the
   caller hands in a *measured* verdict (`measured`) that directly opposes what the recalled row
   recorded. An unmeasured predicate is left alone: reading silence as refutation would talk the
   agent out of a correct memory, which is the other half of negative transfer.
4. **"Used" is judged from the action, not the explanation** (§12.3: 以实际动作和最终状态为准).
   `mark_used` compares a round's actual decision against the step the recall describes; a model
   that says "following my earlier experience" while doing something else does not count, and the
   judgement is recorded as `unknown` when the round produced no decision at all.

Retrieval spends no model request and opens no scene: it reads stored strings and a query.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

from ..core.v02 import EpisodicExperience, RecalledExperience, Unknownable

# One weight per recorded field. The point of publishing them as a dict is that §11's
# `retrieval relevance` row is defined *by* this table: changing a weight changes what the metric
# counts, so the table has to be citable rather than buried in an expression.
RELEVANCE_WEIGHTS: dict[str, float] = {
    "task_kind": 3.0,
    "region": 2.0,
    "kind": 2.0,
    "predicate": 1.0,
    "failure_code": 1.0,
}
MIN_RELEVANCE = 1.0
DEFAULT_LIMIT = 3


def _terms(values: Iterable[str], prefix: str) -> set[str]:
    return {f"{prefix}:{v}" for v in values if v}


def query_from(*, task_kind: str = "", regions: Iterable[str] = (), kinds: Iterable[str] = (),
               predicates: Iterable[str] = (), failure_codes: Iterable[str] = ()) -> set[str]:
    """The query side of the same vocabulary the store indexes: five prefixed term sets.

    Prefixing is what keeps `tray_right` as a region from matching `tray_right` as part of a
    predicate string, which would make a score depend on substring luck instead of on which field
    the term came from.
    """
    terms: set[str] = set()
    if task_kind:
        terms |= _terms([task_kind], "task_kind")
    terms |= _terms(regions, "region")
    terms |= _terms(kinds, "kind")
    terms |= _terms(predicates, "predicate")
    terms |= _terms(failure_codes, "failure_code")
    return terms


def indexed_terms(record: EpisodicExperience) -> dict[str, set[str]]:
    """What one stored row can be matched on, in the same prefixed vocabulary as the query.

    The prefix is part of the term on both sides. Matching `tray_right` against
    `placed:obj:tray_right` by substring would make a score depend on spelling rather than on
    which field the term came from, and this module's weights only mean anything if they do.
    """
    regions: set[str] = set()
    predicates: set[str] = set()
    for line in record.state_pattern:
        predicate, _, _recorded = str(line).partition("=")
        if predicate:
            predicates.add(predicate)
            regions.add(predicate.rsplit(":", 1)[-1])
    for line in record.applicability:
        text = str(line)
        if text.startswith("regions:"):
            regions |= {r for r in text[len("regions:"):].split("|")
                        if r and r != "(none named)"}
    codes = {str(s.effect).split(":", 1)[1] for s in record.steps if ":" in str(s.effect)}
    return {"task_kind": _terms([record.task_kind], "task_kind"),
            "region": _terms(regions, "region"),
            "kind": _terms(record.object_kinds, "kind"),
            "predicate": _terms(predicates, "predicate"),
            "failure_code": _terms({c for c in codes if c}, "failure_code")}


def score(record: EpisodicExperience, query: set[str]) -> tuple[float, list[str]]:
    index = indexed_terms(record)
    matched = sorted({t for field, values in index.items() for t in query if t in values})
    total = sum(RELEVANCE_WEIGHTS[field] for field, values in index.items()
                for t in query if t in values)
    return total, matched


def contradicted(record: EpisodicExperience, measured: Optional[Mapping[str, str]]) -> list[str]:
    """Predicates this row recorded one way and the current snapshot measures the other way.

    `measured` maps predicate id -> "true"/"false"/"unknown" (or an omitted key for "never
    looked"). Either side being `unknown` ends the question rather than answering it: a memory
    that says "unmeasured" and a world that has not looked yet are not a contradiction, and
    reading silence as refutation would let the agent talk itself out of a correct experience.
    """
    if not measured:
        return []
    clashes: list[str] = []
    for line in record.state_pattern:
        predicate, _, truth = str(line).partition("=")
        now = str(measured.get(predicate, "")).strip().lower()
        truth = truth.strip().lower()
        if now in ("true", "false") and truth in ("true", "false") and now != truth:
            clashes.append(f"{predicate}: recalled {truth}, this snapshot measures {now}")
    return clashes


def retrieve(query: set[str], store: Iterable[EpisodicExperience], *,
             measured: Optional[Mapping[str, str]] = None,
             limit: int = DEFAULT_LIMIT,
             round_index: Optional[int] = None) -> list[RecalledExperience]:
    """The relevant rows, with the reason each was chosen. Empty is a legitimate answer."""
    rows: list[RecalledExperience] = []
    for record in store:
        relevance, matched = score(record, query)
        if relevance < MIN_RELEVANCE or not matched:
            continue
        clashes = contradicted(record, measured)
        rows.append(RecalledExperience(
            # Derived, not random: `RecalledExperience.recall_id` has a `new_id` default, and these
            # ids appear in the rendered lines a model is prompted with. A random id there means two
            # runs of the same episode with the same store show the same memory under different
            # names, which is the same reproducibility hole P2 closed for plan row keys.
            recall_id=(f"rec_{record.experience_id}_round{round_index}"
                       if round_index is not None else f"rec_{record.experience_id}"),
            experience_id=record.experience_id, relevance=relevance, match_terms=matched,
            injected_into_round=round_index,
            contradicted_current_state=bool(clashes),
            # `steps` is the §5.4 行动序列, copied as recorded. Without it a recall says "four
            # steps, outcome=success" and offers nothing to act on, and §13's RQ3 — does a memory
            # change what a later similar task does — cannot be asked of a row that carries no
            # action. It is a copy of stored strings, not a new claim: nothing here ranks, edits or
            # completes the sequence.
            #
            # `official_success` is deliberately absent. §3.2 allows the hidden verdict to be
            # *stored* for retrieval-time context and forbids it reaching a prompt, and this
            # provenance dict is copied verbatim into the payload's `working_memory.recalled`
            # section. The record's own `outcome` is the loop-visible reading of the same event and
            # is what a model is allowed to weigh.
            provenance={"run_ref": record.run_ref, "outcome": record.outcome.value,
                        "steps": [{"skill": step.skill, "args": dict(step.args or {})}
                                  for step in record.steps],
                        "failure_reason": record.failure_reason,
                        "contradictions": clashes,
                        "transfer_caution": record.transfer_caution}))
    rows.sort(key=lambda r: (-r.relevance, r.experience_id))
    return rows[:limit]


def mark_used(rows: Sequence[RecalledExperience], store: Any,
              decisions: Iterable[Mapping[str, Any]]) -> None:
    """Judge `used_by_model` from what was actually executed, not from what was said.

    A recall counts as used when the round's decision repeats a step *that experience* records —
    same skill, same object, same target. A rationale that mentions memory while the action does
    not match any remembered step is not evidence of use (§12.3), and a round that decided
    nothing is recorded as `unknown` rather than `False`: the absence of a decision is not a
    decision to ignore the recall.
    """
    executed = {(str(d.get("skill") or ""),
                 str((d.get("args") or {}).get("object_id") or ""),
                 str((d.get("args") or {}).get("target_id") or ""))
                for d in list(decisions) if str(d.get("skill") or "")}
    for row in rows:
        record = store.get(row.experience_id) if hasattr(store, "get") else None
        if record is None:
            row.used_by_model = Unknownable(
                value="unknown",
                unmeasured=["the recalled experience is not in the store, so no step of it can be "
                            "compared against this round"])
            continue
        if not executed:
            row.used_by_model = Unknownable(
                value="unknown",
                unmeasured=["the round produced no executed skill to compare against"])
            continue
        remembered = {(s.skill, str(s.args.get("object_id") or ""),
                       str(s.args.get("target_id") or "")) for s in record.steps}
        hits = sorted(f"{skill} {obj} -> {tgt}" for skill, obj, tgt in remembered & executed)
        row.used_by_model = Unknownable(value=bool(hits), evidence_refs=hits)


#: how many of a remembered episode's steps one line lists. The rest are counted, not hidden: the
# full sequence is in the same row's `provenance.steps`, which `memory_view` publishes beside this
# text. The cap exists because §5.4's lines get their own pool (`RECALL_BUDGET_CHARS`) sized to
# admit `DEFAULT_LIMIT` of them, and an experience from a 20-step episode would otherwise be a line
# too long to ever be shown — a memory that cannot fit the page is not a memory the agent has. On
# the `dev_c1` row measured below the cap does not bind (6 steps ≤ 8); it is there for the shape of
# episode the frozen set does not contain yet.
STEP_CAP = 8


def _step_text(step: Mapping[str, Any]) -> str:
    """`place obj_blue_2 -> tray_right` — one stored action, without the log's field names.

    The argument *keys* are dropped, not the arguments: `pick`/`place` take an object and a target
    in that order everywhere this package writes them, and `object_id=`/`target_id=` cost 22
    characters a step to say nothing a reader could not infer. Any other argument is still named,
    so a skill with a third parameter is not silently reduced. The structured `provenance.steps`
    row beside this text keeps its exact `args` dict, which is what `episodic.policy` reads.
    """
    args = dict(step.get("args") or {})
    entity = str(args.pop("object_id", "") or "")
    target = str(args.pop("target_id", "") or "")
    named = f"{entity} -> {target}" if entity and target else (entity or target)
    rest = " ".join(f"{key}={args[key]}" for key in sorted(args))
    return " ".join(part for part in (str(step.get("skill") or ""), named, rest) if part)


#: how many of a row's `match_terms` the line names. The full list reaches the payload beside the
# line, in the row's own `match_terms` field, so naming all of them here is paying twice for one
# fact — and on the one `dev_c1` row this package has ever stored it is the most expensive fact on
# the line: ten terms at relevance 18, a 250-character clause inside a 748-character line, and the
# per-row share of the recall pool is 800 (`work/p3c_cap_cost.py` re-runs that arithmetic).
MATCH_TERM_CAP = 3
#: how many refuted claims one line lists before counting the rest. `STEP_CAP`'s reason, multiplied
# by the number of claims a row can carry. The compaction beside it (`_clash_clause`) is worth more
# than the cap on the measured row: three claims are 135 characters compacted, 228 in the guard's
# own sentences, and the reset-world case puts three claims on every recall of every episode.
CLASH_CAP = 3


def _clash_clause(message: str) -> str:
    """`placed:obj_blue_2:tray_right (true->false)` out of the sentence `contradicted()` writes.

    The two halves of this module's own format, not somebody else's prose: `contradicted()` above
    is the only producer of these strings, and a message that does not match the shape it writes is
    returned unchanged rather than guessed at, so a future edit to one function cannot silently
    render the other one's content as a predicate id.
    """
    predicate, _, tail = message.partition(": ")
    if not tail:
        return message
    recalled = tail.partition("recalled ")[2].partition(",")[0].strip()
    now = tail.rpartition(" ")[2].strip()
    if not recalled or not now:
        return message
    return f"{predicate} ({recalled}->{now})"


def render_rows(rows: Sequence[RecalledExperience],
                store: Mapping[str, EpisodicExperience]) -> list[str]:
    """The model-facing lines. A contradicted row is still shown, labelled as contradicted.

    Hiding it would be the silent edit this project keeps having to design out: an agent that
    never sees "your memory says X and the world now says not-X" cannot learn that its memory was
    the problem, and neither can a reader of the log.

    `as_reference()` already carries `transfer_caution`, so it is not repeated here: the recall
    section competes for a character budget, and a duplicated sentence buys nothing. The step
    sequence *is* added, for the opposite reason — `as_reference()` counts the steps without saying
    what they were, and an experience whose actions cannot be read cannot be followed, which is the
    whole of §13's RQ3. It is added *compactly and capped* (`STEP_CAP`) for a reason measured rather
    than assumed: before the caps, the real `dev_c1` row cost 904 characters on the round its memory
    was most useful, and it shared one 900-character page with the debt block, which had already
    taken 700 of those characters on round 1 and 1030 of them on round 6. The budget therefore
    dropped every memory on every round of a `full` run, and §5.4's module was unfalsifiable by
    construction. A line that can never be shown is not a conservative design; it is a switch nobody
    read.
    """
    lines: list[str] = []
    for row in rows:
        record = store.get(row.experience_id) if hasattr(store, "get") else None
        if record is None:
            lines.append(f"recall[{row.recall_id}] experience {row.experience_id} is not in the "
                         f"store any more: nothing is asserted about it beyond this row")
            continue
        head = (f"recall[{row.recall_id}] {record.as_reference()} ")
        terms = [str(t) for t in (row.match_terms or [])]
        if terms:
            named = ", ".join(terms[:MATCH_TERM_CAP])
            extra = (f", +{len(terms) - MATCH_TERM_CAP} more"
                     if len(terms) > MATCH_TERM_CAP else "")
            head += (f"(relevance={row.relevance:g}, matched {len(terms)} term(s): "
                     f"{named}{extra} — all of them in match_terms)")
        else:
            head += f"(relevance={row.relevance:g})"
        steps = [_step_text(step) for step in (row.provenance.get("steps") or [])]
        if steps:
            if len(steps) > STEP_CAP:
                steps = steps[:STEP_CAP] + [f"(+{len(steps) - STEP_CAP} more, all of them in "
                                            f"provenance.steps)"]
            head += ("\n  did: " + " | ".join(steps) + " in "
                     + str(row.provenance.get("run_ref") or "an unnamed run").rstrip("/").rsplit(
                         "/", 1)[-1])
        clashes = [str(c) for c in (row.provenance.get("contradictions") or [])]
        if clashes:
            listed = [_clash_clause(c) for c in clashes[:CLASH_CAP]]
            if len(clashes) > CLASH_CAP:
                listed.append(f"(+{len(clashes) - CLASH_CAP} more, in "
                              f"provenance.contradictions)")
            head += ("\n  CONTRADICTED by the current snapshot: " + " | ".join(listed))
        lines.append(head)
    return lines
