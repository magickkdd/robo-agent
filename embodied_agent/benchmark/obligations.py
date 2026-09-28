"""What a text task sentence *owes*, and what a text snapshot can answer about it (§5.2, §10).

This is the text channel's half of §5.2's first two jobs — 任务理解 and Goal/Subgoal 生成 — and the
reason it exists is a measurement, not a preference. Before this module, the v0.2 planning arm run
on ALFWorld authored **zero rows** for all six task types: `planning/subgoals.derive` builds one
`achieve` row per `GoalSpec` assignment, and `benchmark/state.py:goal_from_instruction` deliberately
leaves `assignments` empty, because filling it from the game's own goal record would be the answer
sheet (SPEC-BST 4.6). An empty plan makes `full` and `wo_planning` the same episode, so §11's
long-horizon rows had no denominator on this channel at all.

## Where the information comes from, and why that is not the adapter planning for the agent

§10 forbids a benchmark adapter from planning *for* the agent, and this module is not the adapter:
the adapter is `backend.py`, whose whole vocabulary is `reset`/`step`/`eval_view`. What happens here
happens inside the agent's planning layer, from one input — **the instruction sentence the
environment printed as public text**, which the model already reads verbatim in `task.utterance` and
`goal.instruction`. Three consequences, each of which is a test:

* **no new facts.** Every noun in a row is a word from the sentence: `in2:peppershaker:drawer` can
  only appear for a sentence that contains both `peppershaker` and `drawer`. Nothing is read from
  `eval_view`, from `admissible_commands` (which never enters this process at all), from the game
  file, or from a task-type label — the six types are not named here, the shapes are.
* **no binding to instances.** The sentence says "a cabinet", not "cabinet 6", and choosing which
  cabinet is a strategy that belongs to the decision maker (§3.4). A row is therefore a claim about
  a *class* of thing, and the instrument below answers it from the instances this snapshot names.
* **an unread sentence authors nothing.** Every rule is anchored, and a form outside them produces
  no rows plus one note quoting the sentence — never a guess, and never a silently shorter plan.

## The predicate vocabulary, and the one thing it can never say

Four shapes, keyed so that `planning/subgoals.row_key` gives one row per obligation:

| predicate | claim | `true` when | `false` when |
|---|---|---|---|
| `in<N>:<obj>:<rec>` | N or more instances of `<obj>` are stated inside a receptacle of kind `<rec>` | this snapshot's `contents` facts name N or more | **never** |
| `<op>:<obj>` (`hot:`/`cool:`/`clean:`) | an instance of `<obj>` has been through that operation | an `operation` or `object_state` fact says so | **never** |
| `looked_at:<obj>` | an instance of `<obj>` was described to the agent | a `described`/`no_new_information` fact names one | **never** |
| `lit:<lamp>` | every lamp of this class this snapshot mentions is on | they are all stated `on` | one is stated `off` while none is `on` |

The three "never"s are not a missing instrument; they are what SPEC-BST 4.2 says a sentence can
prove. `In it, you see nothing.` is exhaustive about *one* receptacle, and the row asks about a
*class* of them, so a snapshot that stops naming an object does not state that the object left — it
stops saying. An instrument that read silence as `false` would manufacture §11's
`progress_regressions` out of nothing, and "看不到物体不等于物体不存在" is the rule that forbids it.
The consequence is stated where it will be read: on this channel a `done` placement row can only
reopen through an instrument this module does not have, and the `lit` row has a real negation because
the environment states that one in its own words (`You turn off the desklamp 1.`).

## Why the dependency edges are here at all

The desktop graph's edges come from measurements (a blocked tray, a contested slot). The edges below
come from the *sentence*: "clean some pan and put it in countertop" orders cleaning before placing
because "and" sequences it, and "put a clean mug in coffeemachine" orders them because the mug is
asked for already clean. That is a reading of English, not a measurement of the layout, and every
`Dependency` authored here says so in its own `note` — so a reader of §11's `dependency_violations`
knows what the edge rested on, and knows this channel has no instrument that could find a measured
obstacle at all (there is no geometry to occupy).
"""
from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass
from typing import Optional

from ..core.contracts import GoalSpec, PredicateVerdict, TaskInput, WorldState
from ..core.v02 import Dependency, DependencyKind, Status, Subgoal
from ..planning.subgoals import Derivation

#: a thing-class as the instruction writes it: one lower-case word (`cd`, `soapbar`, `toiletpaper`)
KIND = r"[a-z][a-z\-]*"
#: the determiners the frozen corpus uses. `two` is a count, not an article, and is matched where a
# count matters rather than here.
DET = r"(?:a|an|the|some)"

#: (rule name, pattern). Every pattern is anchored, so at most one can match one sentence.
RULES: list[tuple[str, re.Pattern[str]]] = [
    # `put a clean bowl in cabinet` — the adjective is the requirement, the place is the goal
    ("state_then_place",
     re.compile(rf"^put (?:a|an|some) (?P<op>hot|cool|clean) (?P<obj>{KIND}) (?:in|on) "
                rf"(?P<rec>{KIND})$")),
    # `clean some pan and put it in countertop`
    ("verb_then_place",
     re.compile(rf"^(?P<verb>clean|cool|heat) some (?P<obj>{KIND}) and put it in "
                rf"(?P<rec>{KIND})$")),
    # `find two peppershaker and put them in drawer`
    ("find_two_then_place",
     re.compile(rf"^find two (?P<obj>{KIND}) and put them (?:in|on) (?P<rec>{KIND})$")),
    # `put two cd in safe`
    ("place_two",
     re.compile(rf"^put two (?P<obj>{KIND}) (?:in|on) (?P<rec>{KIND})$")),
    # `put some saltshaker on drawer`
    ("place",
     re.compile(rf"^put (?:{DET}) (?P<obj>{KIND}) (?:in|on) (?P<rec>{KIND})$")),
    # `examine the mug with the desklamp` / `look at bowl under the desklamp`
    ("examine_with",
     re.compile(rf"^examine the (?P<obj>{KIND}) with the (?P<lamp>{KIND})$")),
    ("look_under",
     re.compile(rf"^look at (?:{DET} )?(?P<obj>{KIND}) under the (?P<lamp>{KIND})$")),
]

OP_OF = {"hot": "heat", "cool": "cool", "clean": "clean"}
STATE_OF = {"heat": "hot", "cool": "cool", "clean": "clean"}
#: `examine` answers "This is a cold potato." about a cooled one, so the reported word is not always
# the word the sentence used. The alternatives are the parser's own `described` set.
STATES_READ_AS = {"hot": {"hot"}, "cool": {"cool", "cold"}, "clean": {"clean"}}


@dataclass(frozen=True)
class TextObligation:
    """One thing the sentence says must be true, before anyone is asked how to bring it about."""

    predicate_id: str
    statement: str
    obj_kind: str
    rec_kind: str
    required_count: int
    source: str
    rule: str

    @property
    def is_state(self) -> bool:
        return self.predicate_id.split(":", 1)[0] in ("hot", "cool", "clean")

    @property
    def is_place(self) -> bool:
        return self.predicate_id.startswith("in")


def obligations_of(instruction: str) -> tuple[list[TextObligation], list[str]]:
    """Read one public sentence into obligations. Returns (rows, notes).

    An unread sentence yields no rows and a note that quotes it. The alternative — authoring a
    plausible row from the first noun found — would put a claim in the plan that no public text
    supports, and would make `full` vs `wo_planning` a comparison of two different tasks.
    """
    text = " ".join(str(instruction or "").split()).rstrip(".").strip().lower()
    if not text:
        return [], ["no instruction text was carried into this goal"]
    for name, pattern in RULES:
        m = pattern.match(text)
        if not m:
            continue
        g = m.groupdict()
        out: list[TextObligation] = []
        if name == "state_then_place":
            out.append(_state(g["op"], g["obj"], text, name))
            out.append(_place(g["obj"], g["rec"], 1, text, name))
        elif name == "verb_then_place":
            out.append(_state(STATE_OF[g["verb"]], g["obj"], text, name))
            out.append(_place(g["obj"], g["rec"], 1, text, name))
        elif name == "find_two_then_place":
            out.append(_place(g["obj"], g["rec"], 2, text, name))
        elif name == "place_two":
            out.append(_place(g["obj"], g["rec"], 2, text, name))
        elif name == "place":
            out.append(_place(g["obj"], g["rec"], 1, text, name))
        elif name in ("examine_with", "look_under"):
            out.append(TextObligation(f"looked_at:{g['obj']}",
                                      f"an {g['obj']} is described to the agent", g["obj"], "", 1,
                                      text, name))
            out.append(TextObligation(f"lit:{g['lamp']}", f"a {g['lamp']} is switched on",
                                      g["lamp"], "", 1, text, name))
        return out, [f"the sentence was read by the {name!r} rule"]
    return [], [f"instruction {instruction!r} matches no rule in benchmark/obligations.py: no "
                f"obligation was authored for it rather than guessed"]


def _place(obj: str, rec: str, n: int, source: str, rule: str) -> TextObligation:
    return TextObligation(f"in{n}:{obj}:{rec}",
                          (f"at least {n} {obj} are stated inside a {rec}" if n > 1
                           else f"a {obj} is stated inside a {rec}"),
                          obj, rec, n, source, rule)


def _state(op: str, obj: str, source: str, rule: str) -> TextObligation:
    return TextObligation(f"{op}:{obj}", f"an {obj} has been {OP_OF[op]}ed", obj, "", 1, source,
                          rule)


# ---------------------------------------------------------------------- the graph ----
def _achieve(ob: TextObligation) -> Subgoal:
    return Subgoal(statement=ob.statement, kind="achieve", status=Status.pending,
                   target_entity_ids=([ob.obj_kind] if ob.obj_kind else []),
                   target_region_id=(ob.rec_kind or None), predicate_id=ob.predicate_id,
                   preconditions=[
                       f"read from the public instruction by rule {ob.rule!r}; the sentence names "
                       f"a class, not an instance, so which {ob.obj_kind} and which "
                       f"{ob.rec_kind or '-'} satisfies it is the decision maker's choice"])


def derive_text(goal: GoalSpec, world: WorldState, task: Optional[TaskInput] = None, *,
                planner=None, progress=None) -> Derivation:
    """The text channel's `subgoals.derive`: same return type, different instrument.

    `planner` is accepted and unused on purpose — a text world has no placement generator, and
    `Instruments.have_placement` is what keeps the desktop graph from claiming a blocker it cannot
    see. Ignoring it is also what makes an empty `dependencies` list mean "nothing was derived"
    rather than "nothing was checked".
    """
    instruction = str(getattr(goal, "instruction", "") or "")
    obligations, notes = obligations_of(instruction)
    subgoals = [_achieve(ob) for ob in obligations]
    row_of = {ob.predicate_id: sub for ob, sub in zip(obligations, subgoals)}
    dependencies: list[Dependency] = []
    for ob in obligations:
        if not ob.is_place:
            continue
        op = next((o for o in obligations if o.obj_kind == ob.obj_kind and o.is_state), None)
        if op is None:
            continue
        dependencies.append(Dependency(
            from_subgoal_id=row_of[op.predicate_id].subgoal_id,
            on_subgoal_id=row_of[ob.predicate_id].subgoal_id, kind=DependencyKind.state,
            note=f"{ob.statement} counts only for an object that satisfies {op.statement}: the "
                 f"ordering is read off the instruction sentence, not measured from the layout"))
    for ob in obligations:
        if not ob.predicate_id.startswith("looked_at:"):
            continue
        lamp = next((o for o in obligations if o.predicate_id.startswith("lit:")), None)
        if lamp is None:
            continue
        dependencies.append(Dependency(
            from_subgoal_id=row_of[lamp.predicate_id].subgoal_id,
            on_subgoal_id=row_of[ob.predicate_id].subgoal_id, kind=DependencyKind.state,
            note="the sentence asks for the looking to happen under the lamp. That the lamp is on "
                 "is a measurable precondition; that the looking and the light coincided is stated "
                 "by no single sentence of this backend, so no row claims the conjunction, and the "
                 "pairing is held by the act that carries the described object to the place the lamp "
                 "rests on (`use` answers there and nowhere else, measured)"))
    for text in list(task.declared_constraints if task is not None else []):
        subgoals.append(Subgoal(
            statement=text, kind="maintain", status=Status.pending,
            preconditions=["stated by the user and answerable by no instrument this arm has; it "
                           "stays an open obligation until something measures it"]))
    # `Dependency` rows are the *explanation*; `Subgoal.depends_on` is what `TaskPlanner.ready()`
    # actually gates on, and the two must say the same thing in the same round or the plan publishes
    # an edge it does not honour. `planning/subgoals.derive` sets both for the same reason.
    for dep in dependencies:
        row_of_next = {s.subgoal_id: s for s in subgoals}[dep.on_subgoal_id]
        row_of_next.depends_on = list(row_of_next.depends_on) + [dep.from_subgoal_id]
    return Derivation(subgoals=subgoals, dependencies=dependencies, notes=notes)


# ----------------------------------------------------------- the understanding record ----
def normalized_goal(goal: GoalSpec, world: WorldState, *, progress=None) -> tuple[str, list[str]]:
    """What the sentence asked for, and which instances of those classes this snapshot names.

    `planning/understanding.py` asks the channel's own reader this question because its desktop
    rendering — one `place <entity_id> in <target_id>` per bound assignment — has no meaning here:
    a text `GoalSpec` carries no assignments, and the single progress row its verifier reports is a
    placeholder for the whole instruction (`entity_id="task"`), so rendering it would print
    `place task in benchmark_instruction` as if a parser had bound it.

    The two answers are the honest versions of the same two fields:

    * `normalized_goal` is the obligation list in the sentence's own words. Nothing is added, and an
      unread sentence is reported as unread rather than as an empty goal.
    * `entities_mentioned` is every instance *of a class the sentence named* that this snapshot
      carries. Listing all of them is the point: picking one would be the strategy the decision
      maker owns, and listing none would hide that the environment already named three `mug`s.
    """
    instruction = str(getattr(goal, "instruction", "") or "")
    obligations, notes = obligations_of(instruction)
    if not obligations:
        return (notes[0] if notes else "no obligation was read"), []
    kinds = {ob.obj_kind for ob in obligations if ob.obj_kind}
    kinds |= {ob.rec_kind for ob in obligations if ob.rec_kind}
    mentioned = sorted({e.entity_id for e in world.entities
                        if _instance(e.entity_id)[0] in kinds})
    return "; ".join(ob.statement for ob in obligations), mentioned


def problems_of(goal: GoalSpec) -> list[str]:
    """The reader's own problem list: notes that say it could not read, not the rule it used.

    A matched sentence yields one note naming its rule, which is provenance rather than an
    ambiguity; an unmatched one yields the note that quotes it. The distinction is made by whether
    any obligation came out, so a change to a note's wording cannot move a real problem into the
    provenance field where §11 would stop seeing it.
    """
    instruction = str(getattr(goal, "instruction", "") or "")
    obligations, notes = obligations_of(instruction)
    return [] if obligations else list(notes)


# ------------------------------------------------------------------ the instrument ----
def _instance(name: str) -> tuple[str, str]:
    parts = str(name or "").rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 and parts[1].isdigit() else (str(name), "")


def _stated_inside(world: WorldState, obj_kind: str, rec_kind: str) -> list[str]:
    out = set()
    for f in world.facts:
        if f.kind != "contents" or f.related == "none":
            continue
        if _instance(f.related)[0] == obj_kind and _instance(f.subject)[0] == rec_kind:
            out.add(str(f.related))
    return sorted(out)


def _lamp_state(world: WorldState, lamp_kind: str) -> Optional[str]:
    """`on` when every lamp of this class named here is on, `off` when all are off, else None."""
    states = {f.state for f in world.facts
              if f.kind == "receptacle_state" and _instance(f.subject)[0] == lamp_kind
              and f.state in ("on", "off")}
    if not states:
        return None
    return "on" if "on" in states else "off"


def evidence_for(sub: Subgoal, world: WorldState):
    """Answer one text row from one snapshot: `(verdict, refs, unmeasured)`, or None.

    `None` is the same answer `planning/task_planner.evidence_for` gives when no instrument can
    answer: the row keeps the reading it had, because in this channel "this sentence does not
    mention it" is not new information about the world (SPEC-BST 4.2)."""
    pid = str(sub.predicate_id or "")
    if not pid:
        return None
    ref = world.observation_ref or ""
    if pid.startswith("in") and pid.count(":") == 2:
        count, obj_kind, rec_kind = _place_key(pid)
        if len(_stated_inside(world, obj_kind, rec_kind)) >= count:
            return (PredicateVerdict.true, [ref], [])
        return None
    if pid.split(":", 1)[0] in ("hot", "cool", "clean"):
        op, obj_kind = pid.split(":", 1)
        for f in world.facts:
            if f.kind == "operation" and _instance(f.subject)[0] == obj_kind \
                    and STATE_OF.get(str(f.state), str(f.state)) == op:
                return (PredicateVerdict.true, [ref], [])
            if f.kind == "object_state" and _instance(f.subject)[0] == obj_kind \
                    and str(f.state or "") in STATES_READ_AS.get(op, set()):
                return (PredicateVerdict.true, [ref], [])
        return None
    if pid.startswith("looked_at:"):
        obj_kind = pid.split(":", 1)[1]
        for f in world.facts:
            if _instance(f.subject)[0] != obj_kind:
                continue
            if f.kind in ("object_state", "no_new_information"):
                return (PredicateVerdict.true, [ref], [])
        return None
    if pid.startswith("lit:"):
        state = _lamp_state(world, pid.split(":", 1)[1])
        if state == "on":
            return (PredicateVerdict.true, [ref], [])
        if state == "off":
            # the one negation this channel's environment states in its own words
            return (PredicateVerdict.false, [ref], [])
        return None
    return None


def _place_key(pid: str) -> tuple[int, str, str]:
    head, obj_kind, rec_kind = pid.split(":", 2)
    return int(head[2:] or 1), obj_kind, rec_kind


def obligations_sha256() -> str:
    """Identity of the sentence reader, so a batch says which reading produced its rows.

    Every function a decision round can consult is hashed, including the two that only reshape what
    `obligations_of` already returned: a batch whose `task_understanding` text changed would
    otherwise be filed under a hash that says the reader is unchanged.
    """
    payload = "\n".join(inspect.getsource(obj) for obj in
                        (obligations_of, derive_text, evidence_for, normalized_goal, problems_of))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["DET", "KIND", "OP_OF", "RULES", "STATE_OF", "STATES_READ_AS", "TextObligation",
           "derive_text", "evidence_for", "normalized_goal", "obligations_of", "obligations_sha256",
           "problems_of"]
