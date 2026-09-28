"""A rule decision source for the text channel: the payload is the whole world it can see (§3.4, §12.1).

`planning/policy.py` is the desktop template and this module is the same instrument rebuilt for a
backend with no geometry, no candidate registry and no gripper report. Three reasons it exists, in
the order the phase needed them:

* **the arm has to be measurable at zero spend.** P5-b's two-arm contrast (`full` vs `wo_planning`)
  must not be confounded by a model that answers differently in two runs, and no dev batch on this
  channel is billed until the mechanism is shown to close. `http_requests`, `prompt_tokens` and
  `completion_tokens` are class attributes fixed at zero, so the ledger bills an episode exactly the
  perception it did — which here is none.
* **it proves the view is *sufficient to act on*.** If a deterministic policy written against
  `ctx.model_payload()` alone can carry a text game through its instruction, then `plan_view()`
  delivered the information the decision needed. Where it cannot, the gap surfaces as a `blocked`
  decision naming the row and what was missing from the page — a finding on the record, not a silent
  fallback.
* **one decision maker for every arm**, which is what makes §12.1's sameness hold: given a payload
  with a `plan` section it works the plan's ready rows; given one without, it reads the instruction
  sentence it is still shown (`task.utterance`) and works that. The arms differ by what is on the
  page, never by who is reading it.

## What it may know, and where that is written down

The tables below are a world-semantics prior. `APPLIANCE_OF`, `LAMP_KINDS`, `RECEPTACLE_KINDS` and
`OPENABLE_KINDS` are **measured** from the 72-game / 13,865-command dev probe whose own provenance
is recorded in `benchmark/native_actions.py` (`/tmp/bst_v01/env_probe.json`, `command_templates`; the
census is quoted next to each table). `SEARCH_SEMANTICS`, the sentences below, is measured in this
module's own dev runs on the pinned install (`/tmp/p5b_dbg{2,3,4}.py`) and is the reason the step
machine is ordered the way it is:

* an arrival at an **open** place states its contents (`You arrive at sinkbasin 1. On the sinkbasin
  1, you see a butterknife 2, …`), and an arrival at a **closed** one states only that it is closed,
  so `open` is the second half of searching a place and the reply to it states the contents;
* **`look` states no contents at all** after the opening message (`You are facing the drawer 1. Next
  to it, you see nothing.`), so `observe` is not a search step on this channel and the machine below
  never issues one;
* a refused command answers `Nothing happens.`, which names *nothing* — so an invalid attempt leaves
  the page thinner than it was, which is why the memory is updated from every page and not only from
  successful ones.

Three things follow from where the tables live. They are **not task knowledge** (no game, instance
or plan step appears in them; they say which nouns a verb takes, as the public catalogue does in
prose). They are **the decision source's own belief**, so they may be wrong, and §3.4's permission
("a decision source is exactly the thing that may have a strategy") is what puts them here rather
than in the runtime — `AlfredRuntime` carries no such table, so the loop is still not choosing. And
they are **never injected into a payload**: a model arm reads the same catalogue text and has to work
out the appliance itself.

## What it remembers, and what that costs

`PlaceMemory` keeps the page-derived facts one snapshot cannot state: which walkable instances have
been *named* on any page of this episode, which named places have had their contents *stated*, which
*edges* this install has refused, where the agent last established itself, what the last sentence
about the hand said, and which place last named a given instance in its contents. It is the policy's
own state, accumulated only from payloads the loop handed it, and every use of it is written into the
decision's rationale and into `self.trace`, which the runner files beside the episode — so "it knew
the room had 25 receptacles" is checkable rather than assumed.

The third is a measurement of the channel, made on the game this policy first stalled on:
`go to countertop 1` is answered `Nothing happens.` from `coffeemachine 1` and
`You arrive at countertop 1. On the countertop 1, you see a creditcard 3, …` from `countertop 2`, in
the same game and with the same empty hand. A refusal is therefore about the *pair*, not the place,
and a source that re-issued a refused command would spend the action budget on a sentence it had
already read — which is what the loop's own repeat guard then terminates on. What the policy does
instead is record the pair (origin, destination) from the page that proves it (the `go` it published
and a `world.location` that did not become the destination), route around it through a place whose
edge to that destination is still untried, and quote the environment's own sentence in the rationale.

The reason it must exist is a measurement about the channel, not a convenience. The opening message
is the *only* sentence in a game that enumerates the walkable places, and it leaves the payload after
one action: `world` states the current snapshot (SPEC-BST 4.2 — no persistence is claimed for it)
and `recent_feedbacks` carries three sentences, which for a search of a 25-place graph is not the
list. So a source that read each page in isolation could search two places deep and would then report
`blocked` — and that report would be about the *window*, not about planning. A model arm of this
benchmark has the same window and no `PlaceMemory`; P5-b's numbers are therefore a statement about
the plan and the ledger under one fixed decision source, and never a success rate to be compared with
a model arm's.

What stays snapshot-only, deliberately: which object is *in* which place (`inside`) and whether a
place is shut (`states`). Both are claims about the world, and a stale sentence is not evidence —
`benchmark/parser.py` reads each snapshot's text alone for exactly that reason.

## Two limits this policy does not hide

`attempts` is a window of the last `HISTORY_CAP` distinct tuples, so on a long game "where have I
been" can be older than the memory's own names; the two disagree in the direction of revisiting, and a
revisit is visible as a repeated `alfred_go` in the record.

And a `find two X` row can only be answered `true` by a *single* snapshot that names two instances
(`benchmark/obligations.evidence_for`), so the move sentences do not close it: the step machine ends
with a re-read of the destination (`go`, then `open` when the text said it closed). A policy that
skipped that would report an unverified placement as work still owed — the reverse of what SPEC-BST
4.6 forbids.
"""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Optional, Union

from ..core.contracts import Decision, DecisionContext, DecisionExecute, TextFact
from ..core.v02 import Subgoal
from .obligations import OP_OF, evidence_for, obligations_of
from .parser import parse

#: which receptacle class each state-changing verb is accepted *with*: `heat <X> with <microwave>`
# 38/38, `cool <X> with <fridge>` 102/102, `clean <X> with <sinkbasin>` 196/196 of the measured
# sampled commands. One class each, with no exceptions in the sample.
APPLIANCE_OF = {"heat": "microwave", "cool": "fridge", "clean": "sinkbasin"}
#: the only two targets `use` was ever issued for (466 + 27 of 493 `use` commands)
LAMP_KINDS = ("desklamp", "floorlamp")
#: the 28 classes `go to <X>` exists for in the same sample. `parser.py` marks every item of a room
# view `role=receptacle`, including the mug on the shelf, so role alone cannot say what is
# walkable-to; this is the environment's own answer.
RECEPTACLE_KINDS = frozenset("""
    cabinet drawer shelf stoveburner garbagecan sidetable countertop sinkbasin sofa armchair
    diningtable coffeetable microwave bed toaster fridge coffeemachine dresser handtowelholder
    towelholder desk toilet laundryhamper toiletpaperhanger tvstand bathtubbasin safe ottoman
    """.split())
#: the classes the sample was ever issued `open <X>` for (5 kinds, 3465 commands)
OPENABLE_KINDS = frozenset(("cabinet", "drawer", "fridge", "microwave", "safe"))

PLACE_PREFIX = "in"
LOOK_PREFIX = "looked_at"
LIT_PREFIX = "lit"


class PlaceMemory:
    """What no single snapshot of this channel states and the pages of one episode together do:
    the class of a name never seen again, which places have been looked in, which `go` was refused.

    Nothing enters here except through `observe(payload)`, which reads what the loop published:
    `world.entities`, `world.facts` and the `environment_text` of the feedback rows the model was
    also shown. No game file, no `admissible_commands` list and no official flag is ever consulted —
    a source that reached around the page for them would be measuring the harness.
    """

    def __init__(self) -> None:
        #: walkable instance name -> class, every name the page has ever stated
        self.known: dict[str, str] = {}
        #: names whose *contents* a page has stated, by an arrival at an open place or an `open`
        self.searched: set[str] = set()
        #: (origin, destination) -> the environment's own sentence for a `go` that did not arrive
        self.refused: dict[tuple[str, str], str] = {}
        #: the last position this episode could establish, which no page states after an `open`
        self.at: str = ""
        #: what the newest sentence about the hand said: `None` empty, a name held
        self.holding: Optional[str] = None
        #: whether any sentence this episode read stated the hand at all
        self.hand_said: bool = False
        #: instance -> the place whose contents sentence last named it, objects as well as places
        self.resting: dict[str, str] = {}
        #: every payload this episode read, for the audit of what the memory was built from
        self.pages: list[str] = []

    def note_hand(self, value: Union[str, None]) -> None:
        """Record what a sentence said about the hand; say nothing when it said nothing.

        Measured on this install: `take`, `move` and `inventory` replies name the hand
        (`You move the apple 2 to the microwave 1.` states both the contents and an empty hand) while
        the replies to `go`, `open` and `heat` name neither, so a snapshot whose `held_object` is
        `unknown` is not a claim that nothing has been said this episode — it is that *this* sentence
        did not say it.
        """
        if value == "unknown":
            return
        self.hand_said = True
        self.holding = None if value in (None, "", "none") else str(value)

    def note_arrival(self, stated: str, destination: str) -> None:
        """Where the agent now is, as far as a page and a command row together can say.

        The page's own `location` wins; the destination the arrival sentence named is the fallback,
        because `open` and `take` replies state no position at all and the alternative is to plan from
        nowhere.
        """
        self.at = (stated if stated not in ("", "unknown") else str(destination or "")) or self.at

    def note_refusal(self, origin: str, destination: str, sentence: str) -> None:
        """Book the edge the page proves was not walked: this `go` was published, and the location the
        next page states is not where it said it would be.

        Only a real origin is booked: a page whose `location` the parser could not read states
        nothing about any edge, and blaming `unknown` would hide a place from the search on no
        evidence at all.
        """
        if origin in ("", "unknown") or origin == destination:
            return
        self.refused.setdefault((origin, destination), sentence or "no arrival sentence followed")

    def observe(self, payload: dict[str, Any]) -> None:
        world = payload.get("world") or {}
        self.pages.append(str(payload.get("observation_ref") or ""))
        for entity in world.get("entities") or []:
            eid = str(entity.get("entity_id") or "")
            kind = str((entity.get("attributes") or {}).get("kind") or _kind_of(eid))
            if kind in RECEPTACLE_KINDS and eid and eid != "unknown":
                self.known.setdefault(eid, kind)
        for raw in world.get("facts") or []:
            if str(raw.get("kind") or "") == "contents" and str(raw.get("subject") or ""):
                self.searched.add(str(raw["subject"]))
                if str(raw.get("related") or "") not in ("", "none", "unknown"):
                    self.resting[str(raw["related"])] = str(raw["subject"])
        held_raw = world.get("held_object")
        for row in payload.get("recent_feedbacks") or []:
            text = str(row.get("environment_text") or "")
            if not text:
                continue
            parsed = parse(text)
            for name, attrs in parsed.entities.items():
                if str(attrs.get("kind") or _kind_of(name)) in RECEPTACLE_KINDS:
                    self.known.setdefault(name, _kind_of(name))
            for fact in parsed.facts:
                if fact.kind == "contents" and fact.subject and fact.subject != "unknown":
                    self.searched.add(fact.subject)
                    if fact.related not in ("", "none", "unknown"):
                        self.resting[fact.related] = fact.subject
            self.note_hand(_hand_of(parsed))
        # last, because it is the newest: what the snapshot in front of the decision maker says
        if "held_object" in world and str(held_raw) != "unknown":
            self.note_hand(None if held_raw is None else str(held_raw))

    # ---------- the two questions the step machine asks ----------
    def unsearched(self, searched_now: frozenset[str]) -> list[str]:
        return sorted(name for name in self.known
                      if name not in self.searched and name not in searched_now)

    def places(self) -> list[str]:
        return sorted(self.known)


def _kind_of(instance: str) -> str:
    return str(instance or "").rsplit(" ", 1)[0]


def _hand_of(parsed) -> Union[str, None]:
    """What one environment sentence says about the hand: `unknown` is its answer when it says none.

    The same reading `benchmark/state.py` makes of a snapshot (`p.held[0]` for one item, `None` for
    an empty hand, `unknown` for two), applied to a feedback row's text: the rows are where the hand
    is stated on the pages that state it.
    """
    said = [f for f in parsed.facts if f.kind == "held"]
    if not said:
        return "unknown"
    items = [f.related for f in said if f.related not in ("", "none")]
    return items[0] if len(items) == 1 else ("unknown" if items else None)


class TextPlanPolicy:
    """One native command per round, made from the payload text and nothing else."""

    provider = "rule_text_plan"
    http_requests = 0
    prompt_tokens = 0
    completion_tokens = 0

    def __init__(self, *, feedback_depth: int = 3):
        self.feedback_depth = int(feedback_depth)
        self.decisions = 0
        self.seen: list[DecisionContext] = []
        self.memory = PlaceMemory()
        #: destination of the `alfred_go` this policy last published, resolved against the next page
        self._pending_go: Optional[str] = None
        #: rows this round that no action on the page could advance, in the order met
        self.unactuated: list[str] = []
        self.trace: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ one round ----
    def decide(self, ctx: DecisionContext) -> Decision:
        payload = ctx.model_payload(feedback_depth=self.feedback_depth)
        self.decisions += 1
        self.seen.append(ctx)
        self.unactuated = []
        self.memory.observe(payload)
        world = _World.read(payload, memory=self.memory)
        self._resolve_pending_go(payload, world)
        decision = self._choose(payload, world)
        execute = decision.execute
        self._pending_go = (str((execute.args or {}).get("target_id") or "")
                            if decision.action == "execute" and execute is not None
                            and execute.skill == "alfred_go" else None)
        self.trace.append({
            "round_index": payload["round_index"],
            "state_version": payload["state_version"],
            "views": [key for key in ("plan", "working_memory") if key in payload],
            "action": decision.action,
            "skill": execute.skill if execute else None,
            "args": dict(execute.args or {}) if execute else None,
            "subgoal": execute.subgoal if execute else None,
            "at": world.standing, "holding": world.held,
            "places_known": len(self.memory.known), "places_searched": len(self.memory.searched),
            "edges_refused": len(self.memory.refused),
            "unactuated": list(self.unactuated),
            "rationale": decision.rationale})
        return decision

    def _resolve_pending_go(self, payload: dict[str, Any], world: "_World") -> None:
        """Did the `go` this policy published last round arrive?

        The proof is one published field: the environment's own sentence for that command, which the
        channel's parser classifies as a `location` fact when it says `You arrive at <X>` and as
        `no_effect` when it says `Nothing happens.`. Anything else — `open`, a take, a page that
        states no position at all — is an arrival too, and `note_arrival` is what carries the position
        across those pages: measured, `You open the cabinet 2 …` states no location, so a source that
        trusted only the snapshot would be standing nowhere for the rest of the episode.

        A `go` whose row was not executed is booked against no edge, because the environment was
        never asked.
        """
        target, self._pending_go = self._pending_go, None
        if target is None:
            return
        last = payload.get("last_feedback") or {}
        if str(last.get("skill") or "") != "alfred_go" or str(last.get("target_id") or "") != target:
            return
        if not last.get("executed"):
            return
        text = str(last.get("environment_text") or "")
        if any(f.kind == "location" for f in parse(text).facts):
            self.memory.note_arrival(world.location, target)
            return
        self.memory.note_refusal(world.standing, target, text)

    # ---------------------------------------------------------------- the decision ----
    def _choose(self, payload: dict[str, Any], world: "_World") -> Decision:
        plan = payload.get("plan")
        if plan is not None:
            return self._from_plan(payload, world, plan) or self._stuck(payload)
        return self._from_instruction(payload, world)

    def _from_plan(self, payload: dict, world: "_World", plan: dict) -> Optional[Decision]:
        """Work the first ready row, else the predecessor that is holding one up.

        `ready` arrives ordered by `row_key` from `plan_view` and this policy does not re-rank it.
        A row it cannot advance goes to `self.unactuated` and is named in the rationale of whatever
        is done instead, so "the plan looked done because the agent stopped being seen" is prevented
        rather than described.
        """
        rows = {row["subgoal_id"]: row for row in plan.get("rows") or []}
        for subgoal_id in plan.get("ready") or []:
            row = rows.get(subgoal_id)
            if row is None:
                self.unactuated.append(f"{subgoal_id}:ready-but-not-a-row-of-this-plan")
                continue
            decision = self._for_row(payload, world, row)
            if decision is not None:
                return self._note(decision)
        owed = [row for row in (plan.get("rows") or []) if not _closed(row)]
        if not owed:
            gap = self._conjunction_gap(payload, world, plan)
            if gap is not None:
                return self._note(gap)
            return _control(payload, "finish",
                            "every row of this plan measures satisfied" + _tail(self.unactuated))
        for row in sorted(owed, key=lambda r: str(r.get("row_key") or "")):
            for dep in sorted(row.get("depends_on") or []):
                ahead = rows.get(dep)
                if ahead is None or _closed(ahead):
                    continue
                decision = self._for_row(payload, world, ahead)
                if decision is not None:
                    return self._note(decision)
        return None

    def _for_row(self, payload: dict, world: "_World", row: dict) -> Optional[Decision]:
        pid = str(row.get("predicate_id") or "")
        head, _, rest = pid.partition(":")
        if head.startswith(PLACE_PREFIX) and rest.count(":") == 1:
            count, obj_kind, rec_kind = int(head[len(PLACE_PREFIX):] or 1), *rest.split(":")
            return self._placing(payload, world, row, count, obj_kind, rec_kind)
        # The obligation rows name the state the world has to reach (`hot`), and a command names the
        # verb that gets it there (`heat`); `cool` and `clean` are the same word either way, `hot` is
        # not, so the head cannot be looked up in APPLIANCE_OF directly.
        verb = OP_OF.get(head, head)
        if verb in APPLIANCE_OF:
            return self._processing(payload, world, row, verb, rest)
        if head == LOOK_PREFIX and rest:
            return self._looking(payload, world, row, rest)
        if head == LIT_PREFIX and rest:
            return self._lighting(payload, world, row, rest)
        self.unactuated.append(f"{row.get('row_key') or pid}:this view has no action for the "
                               f"predicate {pid or '(none)'}")
        return None

    # ------------------------------------------------------------------ navigation ----
    def _go(self, payload: dict, world: "_World", row: dict, target: str,
            reason: str) -> Optional[Decision]:
        """`go to <target>`, stepping around the edges this install has already refused.

        The measured fact this handles is in the module docstring: a refusal belongs to the *(from,
        to)* pair, so a place that cannot be reached from here may be reachable from somewhere else
        in the same room. A detour is taken only through a place whose own edge to `target` has not
        been refused either, so every round of a detour walk either states new contents or books a
        new refusal — the walk cannot cycle without learning something. When no known place offers an
        untried edge the row is reported unactuated with the refusal count, which is the honest end
        of a search on this channel rather than a private retry budget.
        """
        here = world.standing
        if (here, target) not in world.refused:
            return _exec(payload, "alfred_go", {"target_id": target}, row, reason)
        via = next((p for p in world.known_places()
                    if p not in (here, target) and (here, p) not in world.refused
                    and (p, target) not in world.refused), None)
        if via is None:
            self.unactuated.append(f"{row.get('row_key')}:`go to {target}` was refused from {here} "
                                   f"({world.refused[(here, target)]!r}) and no known place has an "
                                   f"untried edge to it ({len(world.refused)} refusals read)")
            return None
        return _exec(payload, "alfred_go", {"target_id": via}, row,
                     f"{reason}; `go to {target}` from {here} was answered "
                     f"{world.refused[(here, target)]!r}, so the agent first goes to {via}: the "
                     f"first place this episode names whose own edge to {target} is untried")

    # ---------------------------------------------------------------- the step machine ----
    def _conjunction_gap(self, payload: dict, world: "_World", plan: dict) -> Optional[Decision]:
        """A row that reads satisfied while the *conjunction* its edge names does not hold.

        Two of the six sentence shapes ask one thing to be true under two descriptions — "a cool
        lettuce in countertop", "examine the mug with the desklamp" — and `derive_text` records that
        as a dependency edge. The instrument behind the plan's `satisfied` field cannot honour the
        edge, because no sentence on this install states a conjunction: measured, the arrival that
        names a lettuce in a countertop never calls it cool, the cooling reply names no place at all,
        and a mug's description and a lamp's `on` are spoken in different rooms. So a row can latch
        `true` from a snapshot taken *before* the work its edge names, which is what ended
        `/tmp/p5b_cool2` at 18 environment steps with `official_won=false` — the loop had cooled
        `lettuce 1`, stood at the fridge holding it, and `countertop 1`'s older contents fact still
        counted as the second clause.

        The join is made from fields the page already carries: the `attempts` ledger naming the
        instances this episode operated on and described, this snapshot's `contents` facts, and
        `PlaceMemory.resting`. Nothing is claimed about the world beyond them, and the check lives on
        the plan path alone because `depends_on` — the reading that the two clauses are one claim —
        exists only when a plan was authored.
        """
        rows = {str(row.get("subgoal_id")): row for row in plan.get("rows") or []}
        for row in sorted(rows.values(), key=lambda r: str(r.get("row_key") or "")):
            pid = str(row.get("predicate_id") or "")
            deps = sorted(row.get("depends_on") or [])
            if pid.startswith(PLACE_PREFIX) and pid.count(":") == 2:
                heads = [str((rows.get(dep) or {}).get("predicate_id") or "").split(":", 1)[0]
                         for dep in deps]
                head = next((h for h in heads if h in OP_OF), None)
                if head is None:
                    continue
                count = int(pid.split(":")[0][len(PLACE_PREFIX):] or 1)
                obj_kind, rec_kind = pid.split(":")[1], pid.split(":")[2]
                bound = world.operated(OP_OF[head], obj_kind)
                if bound is None:
                    continue              # nothing has been measured through the operation
                where = world.inside.get(bound)
                if where is not None and world.kind_of(where) == rec_kind:
                    continue              # this snapshot states that thing in that place
                decision = self._placing(payload, world, row, count, obj_kind, rec_kind, bound=bound)
                if decision is not None:
                    return decision
                continue
            if pid.startswith(LOOK_PREFIX) and ":" in pid:
                decision = self._examined_with_lamp(
                    payload, world, row, pid.split(":", 1)[1],
                    [rows.get(dep) or {} for dep in deps])
                if decision is not None:
                    return decision
        return None

    def _examined_with_lamp(self, payload: dict, world: "_World", row: dict, obj_kind: str,
                            predecessors: list[dict]) -> Optional[Decision]:
        """Carry the thing this episode described to the place the lamp it switched on rests.

        Measured (`/tmp/p5b_probe_examine.py`): the environment says the conjunction in its own words
        at the moment the two coincide — holding `mug 1` and arriving at desk 1, the place whose
        contents had named the `desklamp 1` already turned on, ended that game as won, while the
        description alone, given two shelves away, did not. If the page has never named a place for
        that lamp there is nothing to join, and no destination is invented for it.
        """
        lamp_row = next((p for p in predecessors
                         if str(p.get("predicate_id") or "").startswith(LIT_PREFIX)), None)
        if lamp_row is None:
            return None
        lamp_kind = str(lamp_row.get("predicate_id")).split(":", 1)[1]
        seen, lamp = world.operated("examine", obj_kind), world.operated("use", lamp_kind)
        if seen is None or lamp is None:
            return None
        place = world.inside.get(lamp) or self.memory.resting.get(lamp) or None
        if place is None or world.standing == place:
            return None
        return self._go(payload, world, row, place,
                        f"{row.get('row_key')} and the `lit:` row it depends on are one event: this "
                        f"episode described {seen} and switched {lamp} on, and {lamp} is stated on "
                        f"{place}, so the agent takes the describing to the lamp rather than claiming "
                        f"two rooms away as one act")

    def _placing(self, payload: dict, world: "_World", row: dict, count: int, obj_kind: str,
                 rec_kind: str, bound: Optional[str] = None) -> Optional[Decision]:
        """Carry one instance of `obj_kind` into a `rec_kind`, or report what blocks that.

        `bound` names the instance the row is *about*, for a caller that knows the row asks for one
        thing under two descriptions. Left unset, any instance of the class serves, which is what a
        class-level row claims.

        The hand is consulted only where a command depends on it. That ordering is a measurement: an
        `inventory` reply names no place at all, so asking it before navigating would replace a page
        that had a destination on it with one that has none.
        """
        dest = world.pick(rec_kind)
        held = world.held
        if isinstance(held, str) and held != "unknown":
            if held == bound or (bound is None and world.kind_of(held) == obj_kind):
                if dest is None:
                    return self._search(payload, world, row, rec_kind)
                if world.standing != dest:
                    return self._go(payload, world, row, dest,
                                    f"{row.get('row_key')} wants a {obj_kind} in a {rec_kind}, "
                                    f"{held} is in hand and {dest} is the {rec_kind} this page names, "
                                    f"so the agent goes to it")
                if world.is_closed(dest):
                    return _exec(payload, "alfred_open", {"target_id": dest}, row,
                                 f"this snapshot says {dest} is closed, and a closed {rec_kind} "
                                 f"cannot receive {held}")
                if held not in world.contents_of(dest):
                    return _exec(payload, "alfred_move", {"object_id": held, "target_id": dest},
                                 row, f"{held} is in hand at {dest}, whose stated contents do not "
                                      f"include it, so it is moved into it",
                                 expected=f"{held} is stated inside {dest}")
                return self._reread(payload, world, row, dest)
            return self._park(payload, world, row, avoid=dest)
        # Measured on `/tmp/p5b_two1` (`find two peppershaker and put them in drawer 1`): 8 of the 60
        # environment steps went to taking `peppershaker 2` out of `drawer 1` and moving it back in,
        # because a row that wants *two* of a class was answered by looking for one of that class
        # anywhere, including the destination itself. The instances this snapshot already counts
        # toward the row are therefore not sources for it — that search wants the next one, and when
        # there is no next one `_search` is bounded and says so.
        already = {obj for obj, holder in world.inside.items()
                   if world.kind_of(obj) == obj_kind and world.kind_of(holder) == rec_kind}
        source = world.where_one_of(obj_kind, bound,
                                    exclude=None if bound is not None else already)
        if source is None:
            if count > 1 and dest is not None and world.moved_into(obj_kind, dest) >= count:
                return self._reread(payload, world, row, dest)
            return self._search(payload, world, row, obj_kind)
        obj, holder = source
        if world.standing != holder:
            return self._go(payload, world, row, holder,
                            f"the page names {obj} inside {holder}, so the agent goes to {holder}")
        if world.is_closed(holder):
            return _exec(payload, "alfred_open", {"target_id": holder}, row,
                         f"{holder} is stated closed with {obj} inside it")
        if held == "unknown" and not (world.last_was("alfred_take") and world.last_said_nothing()):
            # measured: the take's own reply names the hand when it works
            # (`You pick up the saltshaker 1 from the countertop 3.`) and says `Nothing happens.`
            # when the hand is full, so asking first would only cost the page this object is on —
            # `inventory` states the hand and nothing else. It is asked when a refusal says why.
            return _exec(payload, "alfred_take", {"object_id": obj, "target_id": holder}, row,
                         f"{obj} is stated inside {holder} and the agent is there, so it is taken; "
                         f"the reply will name the hand either way",
                         expected=f"the hand holds {obj}")
        if held == "unknown":
            return self._measure_hold(payload, world, row)
        return _exec(payload, "alfred_take", {"object_id": obj, "target_id": holder}, row,
                     f"{obj} is stated inside {holder}, the agent is there and the hand is empty, so "
                     f"it is taken", expected=f"the hand holds {obj}")

    def _processing(self, payload: dict, world: "_World", row: dict, op: str,
                    obj_kind: str) -> Optional[Decision]:
        appliance_kind = APPLIANCE_OF[op]
        held = world.held
        if isinstance(held, str) and held != "unknown":
            if world.kind_of(held) == obj_kind:
                appliance = world.pick(appliance_kind)
                if appliance is None:
                    return self._search(payload, world, row, appliance_kind)
                if world.standing != appliance:
                    return self._go(payload, world, row, appliance,
                                    f"{obj_kind} must be {op}ed, {held} is in hand and {appliance} is "
                                    f"the {appliance_kind} this page names")
                if world.is_closed(appliance):
                    return _exec(payload, "alfred_open", {"target_id": appliance}, row,
                                 f"{appliance} is stated closed and {op} needs the inside of it")
                return _exec(payload, f"alfred_{op}", {"object_id": held, "target_id": appliance},
                             row, f"the catalogue maps {op} to one command with a "
                                  f"{appliance_kind}, and {appliance} is the one named here",
                             expected=f"{held} has been {op}ed")
            return self._park(payload, world, row, avoid=world.pick(appliance_kind))
        source = world.where_one_of(obj_kind)
        if source is None:
            return self._search(payload, world, row, obj_kind)
        obj, holder = source
        if world.standing != holder:
            return self._go(payload, world, row, holder,
                            f"{obj_kind} must be {op}ed and the page names {obj} inside {holder}")
        if world.is_closed(holder):
            return _exec(payload, "alfred_open", {"target_id": holder}, row,
                         f"{holder} is stated closed with {obj} inside it")
        if held == "unknown" and not (world.last_was("alfred_take") and world.last_said_nothing()):
            return _exec(payload, "alfred_take", {"object_id": obj, "target_id": holder}, row,
                         f"{op} acts on a held {obj_kind}, {obj} is the one named here, and this "
                         f"take's own reply will name the hand either way",
                         expected=f"the hand holds {obj}")
        if held == "unknown":
            return self._measure_hold(payload, world, row)
        return _exec(payload, "alfred_take", {"object_id": obj, "target_id": holder}, row,
                     f"{op} acts on a held {obj_kind}, and {obj} is the one named here",
                     expected=f"the hand holds {obj}")

    def _looking(self, payload: dict, world: "_World", row: dict,
                 obj_kind: str) -> Optional[Decision]:
        """Get one object of this class described — which on this install means *in hand*.

        Measured (`/tmp/p5b_probe_examine.py`): `examine mug 1` at shelf 2, whose own arrival sentence
        had just named that very mug, is answered `Nothing happens.`, while the same command one round
        after `take mug 1 from shelf 2` returns `This is a normal mug 1. In it, you see nothing.`,
        which is the `object_state` sentence the `looked_at:` row is read from. A *receptacle* answers
        `examine` where it stands; an object answers it only from the hand, so the row's own step is
        take-then-examine and a name on the page is not enough to examine.
        """
        held = world.held
        if isinstance(held, str) and held != "unknown":
            if world.kind_of(held) == obj_kind:
                return _exec(payload, "alfred_examine", {"target_id": held}, row,
                             f"the row asks what a {obj_kind} is like and {held} is in hand, where "
                             f"measured `examine` answers",
                             expected=f"{held} is described to the agent")
            return self._park(payload, world, row, avoid=None)
        source = world.where_one_of(obj_kind)
        if source is None:
            return self._search(payload, world, row, obj_kind)
        obj, holder = source
        if world.standing != holder:
            return self._go(payload, world, row, holder,
                            f"the page names {obj} inside {holder} and the row asks what it is like; "
                            f"`examine` needs the object in hand, so the agent goes to get it")
        if world.is_closed(holder):
            return _exec(payload, "alfred_open", {"target_id": holder}, row,
                         f"{holder} is stated closed with {obj} inside it")
        if held == "unknown" and not (world.last_was("alfred_take") and world.last_said_nothing()):
            return _exec(payload, "alfred_take", {"object_id": obj, "target_id": holder}, row,
                         f"{obj} is stated here and the row needs it described, which measured only "
                         f"an object in hand answers; this take's own reply names the hand either way",
                         expected=f"the hand holds {obj}")
        if held == "unknown":
            return self._measure_hold(payload, world, row)
        return _exec(payload, "alfred_take", {"object_id": obj, "target_id": holder}, row,
                     f"the row needs a {obj_kind} described, so {obj} is taken from {holder} first",
                     expected=f"the hand holds {obj}")

    def _lighting(self, payload: dict, world: "_World", row: dict,
                  lamp_kind: str) -> Optional[Decision]:
        """Switch a lamp on from the place it rests on, which is not the lamp.

        Measured (`/tmp/p5b_probe_lamp2.py`): at desk 1 `use desklamp 1` is answered `You turn on the
        desklamp 1.` while the same command from shelf 1 is `Nothing happens.`, and `go to desklamp 1`
        never arrives at all — a lamp is not one of the 28 classes `go to` exists for. So the step this
        row needs is an arrival at the place whose contents named the lamp, then `use` there; the
        contents fact that named the lamp is what names that place.
        """
        lamp = world.pick(lamp_kind)
        if lamp is None:
            return self._search(payload, world, row, lamp_kind)
        state = world.states.get(lamp)
        if state == "on":
            # the instrument answers this row from the same sentence, so an `on` lamp should not be
            # in the ready set at all. If it is, the page supports no further step and saying so is
            # the finding — the machine does not re-issue a command it has already seen work.
            self.unactuated.append(f"{row.get('row_key')}:{lamp} is stated on and the row is still "
                                   f"owed, so no command on this page can advance it")
            return None
        place = world.inside.get(lamp)
        if place is None:
            # the page named a lamp without naming where it rests; only a contents sentence does that
            return self._search(payload, world, row, lamp_kind)
        if world.standing != place:
            return self._go(payload, world, row, place,
                            f"a {lamp_kind} has to be switched on and {lamp} is stated on {place}: "
                            f"measured, `use` answers from the place a lamp rests on and nowhere else")
        if world.is_closed(place):
            return _exec(payload, "alfred_open", {"target_id": place}, row,
                         f"{place} is stated closed and {lamp}'s own switch is inside it")
        return _exec(payload, "alfred_use", {"target_id": lamp}, row,
                     f"the catalogue says `use` switches a lamp, the agent is at {place} where "
                     f"{lamp} is stated, and {lamp} is {state or 'unstated'}",
                     expected=f"{lamp} is on")

    def _measure_hold(self, payload: dict, world: "_World", row: dict) -> Optional[Decision]:
        """The take depends on the hand and this snapshot has not said what is in it."""
        if world.last_was("alfred_inventory"):
            self.unactuated.append(f"{row.get('row_key')}:the hand is still unstated after an "
                                   f"`inventory`, so no command below this one is checkable")
            return None
        return _exec(payload, "alfred_inventory", {}, row,
                     "this snapshot does not state what the hand holds and the next command is a "
                     "take, which is refused while holding something else, so the agent asks",
                     expected="the hand can be named")

    def _park(self, payload: dict, world: "_World", row: dict,
              avoid: Optional[str]) -> Optional[Decision]:
        """Put down what is in hand, which is this row's own obstacle rather than the plan's."""
        here = world.standing if world.standing in world.receptacles() else None
        candidates = [here] if here else [r for r in world.receptacles() if r != avoid]
        if not candidates:
            self.unactuated.append(f"{row.get('row_key')}:holding {world.held}, and this page "
                                   f"names no receptacle to put it in")
            return None
        where = candidates[0]
        if here is None:
            return self._go(payload, world, row, where,
                            "the hand holds " + str(world.held) + ", which no row asked for, so the "
                            "agent carries it to " + str(where) + ": the first receptacle this page "
                            "names besides " + (str(avoid) if avoid else "none") + " (the row's own "
                            "destination)")
        return _exec(payload, "alfred_move", {"object_id": str(world.held), "target_id": where},
                     row, f"{world.held} is in hand and the agent is at {where}, so it is released "
                     f"there", expected=f"{world.held} is stated inside {where}")

    def _reread(self, payload: dict, world: "_World", row: dict,
                dest: str) -> Optional[Decision]:
        """The moves are done and the row still asks: have the destination's contents stated.

        Not `look`: measured, `look` states no contents on this install. An arrival at an open place
        and the reply to `open` both do, which is what closes an `in2:` row.

        The third branch is reached standing at the place, and it reports the row unactuated rather
        than spending two commands (leave, arrive again) to make a page say what the last arrival
        already said short. Measured on `/tmp/p5b_matrixA`: on all four games the environment
        terminated the episode on the winning move, so this page is never handed to a live loop, and
        a source that bought a re-read here would be paying for a doubt no page can settle.
        """
        if world.standing != dest:
            return self._go(payload, world, row, dest,
                            f"{row.get('row_key')} asks for the count inside {dest} to be *stated*, "
                            f"which a move sentence does not do, so the agent returns to {dest}")
        if world.is_closed(dest):
            return _exec(payload, "alfred_open", {"target_id": dest}, row,
                         f"{dest} is stated closed and the row asks what is in it")
        self.unactuated.append(f"{row.get('row_key')}:the agent is standing at {dest} and the "
                               f"contents stated there are short of the count the row asks for; "
                               f"nothing this page can reach adds to that count")
        return None

    def _search(self, payload: dict, world: "_World", row: dict,
                wanted_kind: str) -> Optional[Decision]:
        """Look for a class the current snapshot does not name, one place at a time.

        Two steps, in this order, and both are measurements of the engine's own sentences: an open
        place the agent is standing at has already stated its contents with the arrival, a closed one
        has not been looked in until it is `open`ed; only then does the agent move to the next place
        the episode has ever named. Nothing here knows which place hides what — that is the search.
        """
        here = world.standing
        if here in world.receptacles() and world.is_closed(here) and here not in world.searched:
            return _exec(payload, "alfred_open", {"target_id": here}, row,
                         f"no {wanted_kind} is stated here and this snapshot says {here} is closed, "
                         f"so the place has not been looked in yet")
        unsearched = world.unsearched_places()
        if unsearched:
            where = unsearched[0]
            return self._go(payload, world, row, where,
                            f"no {wanted_kind} is named on this page, so the agent goes to {where}: a "
                            f"place an earlier page of this episode named and whose contents no page "
                            f"has stated ({len(unsearched)} unsearched of {len(world.known_places())} "
                            f"known)")
        self.unactuated.append(f"{row.get('row_key')}:no {wanted_kind} is named on this page and "
                               f"every place this episode has been shown ({len(world.known_places())}) "
                               f"has had its contents stated")
        return None

    def _stuck(self, payload: dict) -> Decision:
        return _control(payload, "blocked",
                        "the plan has no row this policy can advance: "
                        + ("; ".join(self.unactuated) or "no ready row and none owed"))

    def _note(self, decision: Decision) -> Decision:
        tail = _tail(self.unactuated)
        return decision if not tail else decision.model_copy(
            update={"rationale": decision.rationale + tail})

    # ------------------------------------------------- the no-plan fallback (wo_planning) ----
    def _from_instruction(self, payload: dict, world: "_World") -> Decision:
        """No `plan` section: the sentence on the page is the work list, as it is for a model.

        No `subgoal` is named on this path, because there is no plan for a row to belong to —
        binding an action to a row the loop never authored would be a record about a document that
        does not exist. Whether a clause is already satisfied is asked of the *same* instrument the
        plan uses (`obligations.evidence_for`), so this arm is short of a plan, not short of eyes.
        """
        sentence = str((payload.get("task") or {}).get("utterance") or "")
        obligations, notes = obligations_of(sentence)
        if not obligations:
            return _control(payload, "blocked",
                            (notes[0] if notes else "no instruction text was carried into this "
                                                    "goal")
                            + "; with no plan section this page has nothing else to work from")
        for ob in sorted(obligations, key=lambda o: o.predicate_id):
            row = {"subgoal_id": "", "row_key": ob.predicate_id, "predicate_id": ob.predicate_id,
                   "statement": ob.statement, "satisfied": "unknown", "status": "pending",
                   "depends_on": [], "target_entity_ids": [ob.obj_kind],
                   "target_region_id": ob.rec_kind or None}
            if world.meets(ob.predicate_id):
                continue
            decision = self._for_row(payload, world, row)
            if decision is not None:
                return self._note(decision)
        if self.unactuated:
            return _control(payload, "blocked",
                            "the instruction has no clause this policy can advance: "
                            + "; ".join(self.unactuated))
        return _control(payload, "finish",
                        "every clause of the instruction this policy could read is satisfied "
                        "by the text on this page")


# ------------------------------------------------------------------ the payload's world ----
def _closed(row: dict) -> bool:
    """Is this row no longer work? `satisfied true`, or an agent-side close the view published."""
    return (str(row.get("satisfied")) == "true"
            or str(row.get("status")) in ("done", "cancelled"))


@dataclass(frozen=True)
class _World:
    """Everything one payload says, read once — snapshot facts first, episode names second.

    `inside`, `states`, `location`, `held`, `attempts` and `recent` are renderings of this page's own
    fields and nothing else: what is where, and whether a place is shut, are claims about the world
    and a stale sentence is not evidence (SPEC-BST 4.2). `known` and `searched` come from
    `PlaceMemory`, which is the policy's, and are used only to answer *where should I look* — the
    one question the channel's own enumeration cannot answer twice.
    """

    kinds: dict[str, list[str]]
    inside: dict[str, str]
    contents: dict[str, list[str]]
    states: dict[str, str]
    location: str
    held: Union[str, None]
    known: dict[str, str]
    searched: frozenset[str]
    refused: dict[tuple[str, str], str] = field(default_factory=dict)
    #: where the agent is, by the last position any page of this episode stated (see `read`)
    standing: str = "unknown"
    attempts: tuple[dict[str, Any], ...] = ()
    recent: tuple[dict[str, Any], ...] = ()
    facts: tuple[TextFact, ...] = ()
    observation_ref: str = ""

    @classmethod
    def read(cls, payload: dict[str, Any], *, memory: Optional[PlaceMemory] = None
             ) -> "_World":
        world = payload.get("world") or {}
        kinds: dict[str, list[str]] = {}
        for entity in world.get("entities") or []:
            eid = str(entity.get("entity_id") or "")
            if not eid or eid in ("task", "agent", "unknown"):
                continue
            kinds.setdefault(str((entity.get("attributes") or {}).get("kind") or _kind_of(eid)),
                             []).append(eid)
        inside: dict[str, str] = {}
        contents: dict[str, list[str]] = {}
        states: dict[str, str] = {}
        facts: list[TextFact] = []
        for raw in world.get("facts") or []:
            try:
                fact = TextFact(**{k: v for k, v in dict(raw).items()
                                   if k in TextFact.model_fields})
            except Exception:                                      # noqa: BLE001
                continue
            facts.append(fact)
            if fact.kind == "contents" and fact.subject and fact.subject != "unknown":
                contents.setdefault(fact.subject, []).append(fact.related)
                if fact.related not in ("", "none", "unknown"):
                    inside.setdefault(fact.related, fact.subject)
            elif fact.kind == "receptacle_state" and fact.subject and fact.state:
                states[fact.subject] = fact.state
        held_raw = world.get("held_object")
        location = str(world.get("location") or "unknown")
        # A snapshot whose hand reads `unknown` is not a claim that nothing has been said this
        # episode: measured, the replies to `go`, `open` and `heat` state no hand at all, and the
        # memory carries the hand across exactly those pages (SPEC-BST 4.2 makes the snapshot
        # per-round, not the world).
        if held_raw == "unknown" and memory is not None and memory.hand_said:
            held: Union[str, None] = memory.holding
        else:
            held = "unknown" if held_raw == "unknown" else (None if held_raw is None
                                                            else str(held_raw))
        return cls(
            kinds={k: sorted(v) for k, v in kinds.items()}, inside=inside,
            contents={k: sorted(v) for k, v in contents.items()}, states=states,
            location=location,
            held=held,
            known=dict(memory.known) if memory is not None else {},
            searched=frozenset(memory.searched) if memory is not None else frozenset(),
            refused=dict(memory.refused) if memory is not None else {},
            standing=(location if location != "unknown" else
                      (str(getattr(memory, "at", "") or "") or "unknown")) if memory is not None
            else location,
            attempts=tuple(dict(a) for a in (payload.get("attempts") or [])),
            recent=tuple(dict(f) for f in (payload.get("recent_feedbacks") or ())),
            facts=tuple(facts), observation_ref=str(payload.get("observation_ref") or ""))

    @staticmethod
    def kind_of(instance: str) -> str:
        return _kind_of(instance)

    def candidates(self, kind: str) -> list[str]:
        """Instances of `kind` this snapshot names, then any this episode has been shown.

        Ordered, so the choice is reproducible: current page first, then the accumulated names by
        their own names. Naming one is never a claim that the others do not also satisfy the row —
        the row is about a class, and `obligations.evidence_for` is what answers it.
        """
        now = [c for c in self.kinds.get(kind, []) if c != "unknown"]
        later = [name for name, k in sorted(self.known.items())
                 if k == kind and name not in now]
        return now + later

    def pick(self, kind: str) -> Optional[str]:
        candidates = self.candidates(kind)
        if self.standing in candidates:
            return self.standing
        return candidates[0] if candidates else None

    def receptacles(self) -> list[str]:
        """Instances of the classes the measured grammar lets `go to` name, on either page."""
        named = {i for kind, ids in self.kinds.items() if kind in RECEPTACLE_KINDS for i in ids}
        named |= {name for name, kind in self.known.items() if kind in RECEPTACLE_KINDS}
        return sorted(named)

    def known_places(self) -> list[str]:
        return sorted(self.known)

    def unsearched_places(self) -> list[str]:
        """Places the episode was shown whose contents no page has stated.

        A place the agent is standing at with its contents named is searched by the arrival sentence;
        a closed one is not searched until it is opened, which is why `is_closed` and not `location`
        decides the first branch of `_search`.
        """
        stated = set(self.searched) | set(self.contents)
        return [p for p in self.known_places() if p not in stated]

    def contents_of(self, instance: str) -> list[str]:
        return list(self.contents.get(instance) or [])

    def where_one_of(self, kind: str, only: Optional[str] = None,
                     exclude: Optional[set] = None) -> Optional[tuple[str, str]]:
        """An instance of `kind` this snapshot states is inside something.

        Current page only, on purpose: `inside` is a claim about where a thing is *now*. `only` narrows
        the answer to one instance, which is what a row about *that* thing — the one the operation was
        performed on — asks for; `None` lets any instance of the class serve, which is what a row about
        a class claims. `exclude` removes names from consideration, which is how a row that counts two
        objects declines to pick up the one it has already put down.
        """
        skip = exclude or set()
        for obj, holder in sorted(self.inside.items()):
            if obj == "none" or obj in skip:
                continue
            if only is not None and obj != only:
                continue
            if self.kind_of(obj) == kind:
                return obj, holder
        return None

    def operated(self, verb: str, obj_kind: str) -> Optional[str]:
        """The instance the published attempts last put through `alfred_<verb>` without failing.

        `payload["attempts"]` is the loop's own ledger of the commands that were issued — page material
        like everything else read here — and it is the only place an object's *changed state* survives
        on this channel: measured, no sentence says both what an object is now and where it now is.
        Either argument slot may carry the name, because the two skills this is asked about put their
        object in different places — `take`/`cool` name an `object_id` (recorded as `entity_id`) while
        `use`/`examine` name only a `target_id`. The class test is what keeps a fridge from being
        mistaken for the lettuce it was used on.
        """
        for attempt in reversed(self.attempts):
            if str(attempt.get("skill") or "") != f"alfred_{verb}" or attempt.get("failure_code"):
                continue
            for slot in ("entity_id", "target_id"):
                name = str(attempt.get(slot) or "")
                if name and self.kind_of(name) == obj_kind:
                    return name
        return None

    def visited(self) -> set[str]:
        return {str(a.get("target_id")) for a in self.attempts
                if str(a.get("skill") or "") == "alfred_go" and a.get("target_id")}

    def moved_into(self, kind: str, dest: str) -> int:
        """How many instances of `kind` the published `attempts` say were moved into `dest`.

        Distinct objects, not command count: the same refused `move` booked twice is one object, and
        the row's `count` is a claim about objects.
        """
        return len({str(a.get("entity_id")) for a in self.attempts
                    if str(a.get("skill") or "") == "alfred_move"
                    and str(a.get("target_id") or "") == dest and not a.get("failure_code")
                    and self.kind_of(str(a.get("entity_id") or "")) == kind})

    def is_closed(self, instance: Optional[str]) -> bool:
        """Did this snapshot's own sentence say the place is shut?

        `OPENABLE_KINDS` is applied because the check is a *command* choice, not a claim about the
        world: a snapshot that called a sinkbasin closed would make `alfred_open` the next step, and
        the measured grammar has no `open` for it (5 kinds, 3465 commands). Where the kind cannot be
        opened, the step below this one is the one the page supports.
        """
        return (str(self.states.get(str(instance or "")) or "") == "closed"
                and self.kind_of(str(instance or "")) in OPENABLE_KINDS)

    def last_was(self, skill: str) -> bool:
        """Was the most recent action whose skill the page states this one?"""
        for feedback in reversed(self.recent):
            if str(feedback.get("skill") or ""):
                return str(feedback.get("skill")) == skill
        return False

    def last_said_nothing(self) -> bool:
        """Did that row's own sentence state no effect? `Nothing happens.` on this install.

        Read with the same parser the loop used to build the page, so a refusal is a published fact
        here rather than a belief: this is the sentence the environment spoke, on the row for the
        command this policy asked for.
        """
        for feedback in reversed(self.recent):
            if str(feedback.get("skill") or ""):
                text = str(feedback.get("environment_text") or "")
                return any(f.kind == "no_effect" for f in parse(text).facts)
        return False

    def meets(self, predicate_id: str) -> bool:
        """Ask the plan's own instrument about one clause, from this page's facts."""
        answer = evidence_for(Subgoal(predicate_id=predicate_id),
                              SimpleNamespace(facts=list(self.facts),
                                              observation_ref=self.observation_ref))
        return bool(answer is not None and str(answer[0].value) == "true")


def _exec(payload: dict, skill: str, args: dict[str, str], row: dict, why: str,
          expected: str = "") -> Decision:
    """One command, naming the row it works.

    `subgoal` is the row's own id so the commitment the memory books is attached to the work rather
    than floating as an unbound action, and `candidate_id` is never set: this backend has no
    geometry to name (`AlfredRuntime._validate_execution` refuses one).
    """
    return Decision(
        context_id=payload["context_id"], based_on_state_version=payload["state_version"],
        goal_ref=payload["goal"]["goal_id"], action="execute",
        execute=DecisionExecute(skill=skill, args={k: v for k, v in args.items() if v},
                                subgoal=(row or {}).get("subgoal_id") or None),
        rationale=why, expected_effect=expected,
        evidence_refs=[payload["observation_ref"]])


def _control(payload: dict, action: str, why: str) -> Decision:
    return Decision(context_id=payload["context_id"],
                    based_on_state_version=payload["state_version"],
                    goal_ref=payload["goal"]["goal_id"], action=action, rationale=why,
                    evidence_refs=[payload["observation_ref"]])


def _tail(unactuated: list[str]) -> str:
    return ("; rows this view could not advance: " + "; ".join(unactuated)) if unactuated else ""


def policy_sha256() -> str:
    """Identity of the whole file: the tables, the step machine and the reason for both.

    The module rather than a chosen list of functions, because the tables above *are* behaviour and
    a reworded docstring that changes a measured census line changes what a reader can check.
    """
    from . import policy as module

    return hashlib.sha256(inspect.getsource(module).encode("utf-8")).hexdigest()


__all__ = ["APPLIANCE_OF", "LAMP_KINDS", "OPENABLE_KINDS", "PlaceMemory", "RECEPTACLE_KINDS",
           "TextPlanPolicy", "policy_sha256"]
