"""Grounding: from the name a frame gave a body to the name the task gives it.

P1-c produced a snapshot whose entities are named the way pixels name them — `seen:red`,
because colour is the one attribute that identifies a body in this cell and an environment
entity id "is a name the *environment* chose, and a percept that produced one would be
reading it from somewhere the contract forbids" (`observe.percept_id`). The rest of the loop
cannot use that name: the goal assignment, the candidate generator, the skill executor and
the physics all address `obj_red_1`, and `PlacementPlanner.generate` returns no candidates at
all for an id it does not recognise. So a percept-derived snapshot is either renamed by an
explicit, auditable act of grounding or the sensing arm cannot act — and the middle route,
letting a *plan* name a `seen:*` id, is closed by the same fact that `body_id=-1` for every
entity this channel produces.

This module is that act, kept in one place so §11's *semantic grounding accuracy* has a
single table to grade. Three rules, all of them consequences of the same worry:

1. **The bridge is a declaration, not a measurement.** `GroundingMap` is built from the
   task's own object list (`case.objects`: `entity_id` plus its declared `color`), which is
   the public half of the case — `TaskCase.public_payload` withholds the scoring block and
   the injected events, not the inventory. Nothing here looks at where a body is, at what the
   goal assigns it, or at anything the evaluator knows. A colour word is the whole key, and
   colours never repeat inside a frozen case (checked over all 47 cases in P1-b), which is
   why the map refuses a duplicate rather than picking one.
2. **Only a body the frame named can be grounded, and only once.** Two detections claiming
   `red` in one frame is conflicting evidence about an unmoved table, so *neither* gets a
   task name; a colour the declaration does not carry (`seen:teal`, or the shape-only
   `seen:shape:cylinder` a colourless box keeps) is reported `ungrounded` and keeps the
   frame's own name. An ungrounded entity is not dropped — it is in the picture, and the
   verifier must be able to say what it declined to answer about it.
3. **Grounding a name does not transfer any other fact.** The renamed entity keeps
   `body_id=-1` (this channel never learns a physics handle, and the name bridge is not one:
   `entity_id → body` still lives in the scene), `orientation_measured=False`, `held`
   unknown, and every `attributes` value the assembler measured. `seen_as` is kept
   *beside* the new name so a reviewer can walk a claim back to the detection that produced
   it, which is what makes a wrong binding auditable instead of merely invisible.

`held` is the one field that does not come from the reading. A camera on a table cannot see
what the hand holds — P1-c left `held_object="unknown"` for exactly that reason — but the
gripper reports it, and §5.8's first layer is a separate, legitimate source. Without it
`PlanValidator` refuses every pick and place ("pick while the hold state is unknown"), so the
arm would be structurally unable to act on anything it saw. The value therefore comes from
`SkillExecutor.held_state()` — the same reading `_do_pick` consults before it moves — and is
labelled `held_source: actuator` in the snapshot, never folded into the visual attributes.

Which source spoke is itself recorded, because a benchmark's gripper is not always able to:
where the hand reports `unknown` and the *frame* can settle it (a body whose measured position
puts it above every surface it could be resting on), `held_from="frame"` files the same field
under a different authority. What must not happen is the sentence claiming the gripper said
something the pixels said. See `HELD_AUTHORITIES`.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Optional, Sequence

from ..core.contracts import (
    EntityState,
    NotSeen,
    TextFact,
    WorldState,
)
from .frames import VIEWS

#: what a renamed entity records about where its name came from
GROUNDED = "grounded"
UNGROUNDED = "ungrounded"
CONFLICTING = "conflicting"
#: the frame's own name, kept beside the task's so a binding can be walked back
SEEN_AS = "seen_as"
GROUNDING = "grounding"
HELD_SOURCE = "held_source"
#: who answered the grasp question, and the words the snapshot says it in. `actuator` is the
#: gripper's own §5.8 layer-1 report, which is what every desktop snapshot files; a channel
#: that settles the question from measured geometry names itself instead, and an authority
#: that did not speak is not a word a report may borrow. An unlisted tag is used as its own
#: phrase rather than being quietly read as the gripper's.
HELD_AUTHORITIES = {"actuator": "the gripper", "frame": "the frame"}


def colour_of(entity_id: str) -> str:
    """The frame's colour word for `seen:red`; "" for a name that carries none."""
    return entity_id.split(":", 1)[1] if entity_id.startswith("seen:") else ""


class GroundingMap:
    """`declared colour word -> task entity id`, with the declaration it came from.

    Immutable and hashable by content: the digest goes into the run's manifest, so a
    re-labelled declaration is a different experiment even when the pictures are identical."""

    def __init__(self, by_colour: dict[str, str], *, source: str = "task declaration"):
        dupes = {}
        for colour, entity_id in by_colour.items():
            dupes.setdefault(entity_id, []).append(colour)
        reverse = {}
        for entity_id, colours in sorted(dupes.items()):
            if len(colours) > 1:
                raise ValueError(
                    f"{entity_id} is declared under {len(colours)} colour words "
                    f"{colours}: a name bridge with two spellings of one body is not a "
                    f"function and would ground a detection to whichever was looked up")
            reverse[entity_id] = colours[0]
        self.by_colour = dict(sorted(by_colour.items()))
        self.by_entity = dict(sorted(reverse.items()))
        self.source = source

    @classmethod
    def from_objects(cls, objects: Sequence[dict], **kw) -> "GroundingMap":
        """The declaration half of a case: the objects it ships with, not where they are."""
        table: dict[str, str] = {}
        for d in objects:
            colour = str((d.get("attributes") or {}).get("color") or "").strip().lower()
            entity_id = str(d.get("entity_id") or "")
            if not colour or not entity_id:
                continue
            if colour in table:
                raise ValueError(
                    f"colour {colour!r} is declared for both {table[colour]} and {entity_id}: "
                    f"a frame names bodies by colour, so this declaration cannot be grounded "
                    f"by that name (P1-b verified colour uniqueness over all 47 frozen cases)")
            table[colour] = entity_id
        return cls(table, **kw)

    def bind(self, colour: str) -> Optional[str]:
        return self.by_colour.get(str(colour or "").strip().lower())

    def colour(self, entity_id: str) -> Optional[str]:
        return self.by_entity.get(entity_id)

    def sha256(self) -> str:
        payload = "|".join(f"{c}={e}" for c, e in sorted(self.by_colour.items()))
        return hashlib.sha256(f"{self.source}|{payload}".encode("utf-8")).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {"source": self.source, "bound": dict(self.by_colour),
                "map_sha256": self.sha256()}


def _rename(entity: EntityState, gmap: GroundingMap,
            claimed: dict[str, list[str]]) -> EntityState:
    colour = str(entity.attributes.get("color") or "").strip().lower() or colour_of(
        entity.entity_id)
    target = gmap.bind(colour)
    attributes = dict(entity.attributes)
    attributes[SEEN_AS] = entity.entity_id
    if target is None:
        attributes[GROUNDING] = UNGROUNDED
        return entity.model_copy(update={"attributes": attributes})
    if len(claimed.get(target, [])) > 1:
        # two detections, one declared body: the frame is contradicted by itself, and
        # handing the task name to either one silently chooses a winner
        attributes[GROUNDING] = CONFLICTING
        attributes["conflicting_detections"] = ",".join(claimed[target])
        return entity.model_copy(update={"attributes": attributes})
    attributes[GROUNDING] = GROUNDED
    return entity.model_copy(update={"entity_id": target, "attributes": attributes})


def _claimed_targets(entities: Iterable[EntityState], gmap: GroundingMap) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in entities:
        colour = str(e.attributes.get("color") or "").strip().lower() or colour_of(e.entity_id)
        target = gmap.bind(colour)
        if target:
            out.setdefault(target, []).append(e.entity_id)
    return out


def ground_state(state: WorldState, gmap: GroundingMap, *,
                 not_seen: Iterable[tuple] = (), held: Optional[str] = "unknown",
                 view: str = "", held_from: str = "actuator") -> WorldState:
    """The snapshot the loop may act on: same evidence, task names, gripper report added.

    `held` is the answer to the grasp question, in the task's own naming, and `held_from` says
    who gave it: `"actuator"` for the gripper's report — which is what `SkillExecutor`
    `held_state()` reads off the physics body, so the value never needs the bridge above — or
    `"frame"` for a channel that settled it from measured geometry. The phrase is not
    decoration: `held_object` is one field whichever instrument filled it, and a snapshot that
    credits the hand for an answer the pixels gave has destroyed the distinction §5.8 draws
    between its two layers. `None` is a *report*, not a missing one: an empty hand is the
    contract's own value for `held_object`, and leaving it as `"unknown"` would let the
    snapshot claim not to know that nothing is held — which is how `PlanValidator`'s "pick
    while the hold state is unknown" refusal becomes permanent and the arm is structurally
    unable to act. `not_seen` is the tracker's rows for this view, in the frame's names, and
    they are grounded through the same table: an ungroundable body stays `seen:teal` in the
    `not_seen` row too, because that is the name the frame can defend.
    """
    claimed = _claimed_targets(state.entities, gmap)
    entities = [_rename(e, gmap, claimed) for e in state.entities]
    # the frame's name -> the task's name, for the two row-sets that carry the old key.
    # Ungrounded entities are absent from this table and so keep the name they arrived with
    by_seen = {str(e.attributes[SEEN_AS]): e.entity_id for e in entities if SEEN_AS in e.attributes}
    occupancy = [o.model_copy(update={"entity_id": by_seen.get(o.entity_id, o.entity_id)})
                 for o in state.occupancy]
    rows: list[NotSeen] = []
    for eid, _visible, occluded_by in not_seen:
        target = gmap.bind(colour_of(str(eid)))
        if target and len(claimed.get(target, [])) <= 1:
            eid = target
        rows.append(NotSeen(entity_id=str(eid), view=view, occluded_by=occluded_by))
    updates: dict[str, Any] = {"entities": entities, "occupancy": occupancy, "not_seen": rows}
    if held != "unknown":
        # The answer, in the task's naming already: both instruments name a *declared* entity
        # id (the gripper reads the physics body, a frame-side instrument answers about a body
        # this channel's own colour word binds), so neither needs the bridge above.
        says = HELD_AUTHORITIES.get(held_from, held_from)
        updates["held_object"] = held
        updates["facts"] = list(state.facts) + [TextFact(
            kind="hold_report", subject=held, state="empty" if held is None else "holding",
            observation_ref=state.observation_ref,
            text=(f"{says} reports holding nothing" if held is None
                  else f"{says} reports holding {held}"))]
        for n, e in enumerate(entities):
            if held is not None and e.entity_id == held and e.held is not True:
                updates["entities"] = list(entities)
                updates["entities"][n] = e.model_copy(update={
                    "held": True, "attributes": {**e.attributes, HELD_SOURCE: held_from}})
                break
    return state.model_copy(update=updates)


# ------------------------------------------------------------------ arms -----

#: what each perception channel is, and which §9 arm it can carry
PERCEIVE_CHANNELS = ("privileged", "stub", "vlm")
#: the module a channel's records belong to (`v02.EVENT_MODULE`'s `perception` owner)
CHANNEL_PRODUCES_PERCEPTION = {"vlm": True, "stub": False, "privileged": False}


def channel_is_declared(channel: str) -> bool:
    return channel in PERCEIVE_CHANNELS


def arm_coherence(ablation, channel: str) -> list[str]:
    """Why this arm and this perception channel cannot (or can) be run together.

    `wo_vlm` means no VLM produced anything, and the only record the VLM module owns in the
    v0.2 vocabulary is `perception` — so a `wo_vlm` episode that files one is claiming the
    module was off while showing its output. That is what §9's `w/o VLM (privileged *or
    limited semantic baseline*)` alternative is: the privileged arm reads simulator state,
    the limited-semantic arm reads a deterministic colour-segmentation reading of the same
    frame, and *neither* consults a vision model, so neither may file the record. A `full`
    episode that does not consult one either is not the full system, and the second refusal
    below is that claim.

    Returns the reasons it does not fit, so a caller can refuse with all of them at once."""
    reasons: list[str] = []
    if ablation is None or channel == "":
        return reasons
    if not channel_is_declared(channel):
        return [f"unknown perception channel {channel!r}; declared: {list(PERCEIVE_CHANNELS)}"]
    off = list(ablation.modules_off)
    produces = CHANNEL_PRODUCES_PERCEPTION[channel]
    if "vlm" in off and produces:
        reasons.append("condition wo_vlm turns off the module that owns the `perception` "
                       "record, and this channel produces one")
    if "vlm" not in off and not produces:
        reasons.append(f"condition {ablation.condition} leaves the vlm module on, but channel "
                       f"{channel!r} never consults a vision model")
    return reasons


def channel_readiness(channel: str, adapter=None) -> list[str]:
    """Why this channel cannot run *here*, as reasons rather than an exception.

    `arm_coherence` answers "does this arm's claim match this channel's behaviour"; this
    answers "can the channel be built at all". They are different questions and a caller needs
    both, because the second is what an operator hits first: `vlm` is a coherent `full` arm and
    this checkout still has no vision model behind it.

    The refusal is stated once, here, so `cli`, `run_group` and `build_arm` cannot drift into
    three different explanations of the same absence — and so the sentence that says "no vision
    model is configured" is never the place a run quietly becomes a stub run."""
    reasons: list[str] = []
    if channel == "vlm" and adapter is None:
        reasons.append(
            "the vlm channel is not runnable here: no vision model is configured "
            "(no `--model-config` naming a config with a vision-capable model was passed, "
            "and no adapter was built from one). "
            "Falling back to the deterministic stub would report the same episode under the "
            "name `full`, which is the one substitution this gate exists to prevent. The "
            "first real request also needs its budget stated before it is spent (SPEC 7)")
    return reasons


def declared_views(views: Optional[Iterable[str]] = None, *,
                   known: Optional[Iterable[str]] = None) -> tuple[str, ...]:
    """Check a set of view names against the table that says which cameras exist.

    `known` is what a second benchmark supplies: `VIEWS` is *this* cell's camera rig, and
    a MuJoCo bench has its own (`corner2`, `gripperPOV`, read out of its model file). The
    check itself is not cell-specific and must not be skipped for a new one — an
    `alt_views` entry that names no camera makes `_locate`'s "look from somewhere else"
    advice unfollowable, and a view the model can ask for and the arm cannot measure is a
    refusal the runtime would have to invent. Default `None` keeps every desktop call
    checking against the desktop rig."""
    allowed = tuple(VIEWS) if known is None else tuple(known)
    out = tuple(views) if views else allowed
    undeclared = [v for v in out if v not in allowed]
    if undeclared:
        raise ValueError(f"undeclared views {undeclared}; declared: {sorted(allowed)}")
    return out


__all__ = ["GroundingMap", "colour_of", "ground_state",
           "arm_coherence", "channel_readiness", "declared_views", "channel_is_declared",
           "PERCEIVE_CHANNELS",
           "CHANNEL_PRODUCES_PERCEPTION", "GROUNDED", "UNGROUNDED", "CONFLICTING",
           "SEEN_AS", "GROUNDING", "HELD_SOURCE"]
