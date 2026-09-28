"""Deterministic reading of ALFWorld's observation text (SPEC-BST 4, 13.5).

The parser has one job: turn what the environment printed into facts that name the
sentence they came from. It is not a world model — every snapshot is read from the
text of *that* snapshot alone, so an object that was in an open drawer four steps
ago is simply not in this snapshot's facts, and "the drawer is closed" never becomes
"the drawer is empty". What no rule matches stays verbatim in `unparsed`, which is
how "the parser lost it" remains distinguishable from "the environment did not say
it".

The rule set is derived from the message shapes observed on the pinned install: 72
development games, 13,865 sampled steps, plus the official handcoded expert's own
solved trajectories (`benchmark/probe.py`, Phase 0 record).
"""
from __future__ import annotations

import re
from typing import Optional

from ..core.contracts import TextFact

# a demangled instance name as the environment writes it: "mug 2", "cabinet 12"
ID = r"([a-z][a-z\-]* \d+)"
# the same thing inside an enumeration, where each item carries its own article
ITEM_FIND = re.compile(r"\b([a-z][a-z\-]+) (\d+)\b")


def _items(text: str) -> list[str]:
    return [f"{kind} {num}" for kind, num in ITEM_FIND.findall(text or "")]


# (rule, pattern) in reading order. The order is a constraint, not a style: a rule
# that *establishes* a receptacle anchor has to be listed before a rule that reads
# the anaphora depending on it, because `parse` runs each rule over the whole text
# in this order. "You open the cabinet 6. In it, you see a mug 4." is the single most
# common observation shape in the measured corpus, and with `opened` listed after
# `contents_in` its contents bound to no holder at all (found by
# tests/unit/test_bst_parser.py, not by the coverage count: every sentence was read,
# and one of them was read wrongly).
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("instruction", re.compile(r"Your task is to: (?P<goal>[^.]*?)\.")),
    ("in_room", re.compile(r"You are in the middle of a room\.")),
    ("room_view", re.compile(r"Looking quickly around you, you see (?P<items>[^.]*?)\.")),
    # ---- anchors: each of these names the receptacle the next sentence may call "it"
    ("arrive", re.compile(r"You arrive at (?P<rec>" + ID + r")\.")),
    ("rec_state", re.compile(r"The (?P<rec>" + ID + r") is (?P<state>closed|open|on|off)\.")),
    ("opened", re.compile(r"You open the (?P<rec>" + ID + r")\.")),
    ("closed", re.compile(r"You close the (?P<rec>" + ID + r")\.")),
    ("used", re.compile(r"You turn (?P<dir>on|off) the (?P<rec>" + ID + r")\.")),
    ("picked_up", re.compile(r"You pick up the (?P<obj>" + ID + r") from the "
                             r"(?P<rec>" + ID + r")\.")),
    ("moved_to", re.compile(r"You move the (?P<obj>" + ID + r") to the (?P<rec>" + ID + r")\.")),
    ("processed", re.compile(r"You (?P<op>heat|cool|clean|sliced?) the (?P<obj>" + ID +
                             r") (?:with|using) the (?P<rec>" + ID + r")\.")),
    # ---- anaphora: reads the anchor the lines above established
    ("contents_in", re.compile(r"In it, you see (?P<items>[^.]*?)\.")),
    ("contents_on", re.compile(r"On the (?P<rec>" + ID + r"), you see (?P<items>[^.]*?)\.")),
    ("facing", re.compile(r"You are facing the (?P<items>[^.]*?)\. Next to it, you see "
                          r"(?P<near>[^.]*?)\.")),
    ("described", re.compile(r"This is a (?P<state>normal|clean|hot|cool|cold)( and "
                             r"(?P<state2>clean|hot|cold))? (?P<obj>" + ID + r")\.")),
    ("carrying", re.compile(r"You are carrying: (?P<items>[^.]*?)\.")),
    ("carrying", re.compile(r"You are not carrying anything\.")),
    ("no_effect", re.compile(r"Nothing happens\.")),
    ("no_new_information", re.compile(r"There's nothing special about (?:the )?(?P<obj>" + ID +
                                      r")\.")),
    ("command_list", re.compile(r"Available commands:[\s\S]*")),
    ("banner", re.compile(r"-= Welcome to TextWorld, ALFRED! =-")),
    ("win", re.compile(r"Success!")),
    ("lose", re.compile(r"Game over\.")),
]

# the rules whose `_apply` sets `last_rec` for a *later* sentence, and the rules that
# read it. `contents_on` names its holder inside its own sentence, so it belongs to
# neither group here. Listing both lets a test state the ordering constraint instead
# of trusting it.
ANCHOR_RULES = ("arrive", "rec_state", "opened", "closed", "used", "picked_up", "moved_to",
                "processed")
ANAPHORA_RULES = ("contents_in", "facing")


class ParsedObservation:
    """What one snapshot says, plus the anchors needed to read its anaphora."""

    def __init__(self) -> None:
        self.facts: list[TextFact] = []
        self.unparsed: list[str] = []
        self.entities: dict[str, dict[str, str]] = {}
        self.location: Optional[str] = None
        self.last_rec: Optional[str] = None
        self.held: list[str] = []
        self.held_said = False
        self.instruction: Optional[str] = None
        self.terminal_word: Optional[str] = None
        self.unread_rules: list[str] = []

    def add(self, kind: str, subject: str = "unknown", related: str = "unknown",
            state: str = "unknown", span=None, text: str = "") -> None:
        self.facts.append(TextFact(kind=kind, subject=subject, related=related, state=state,
                                   span=span, text=text))

    def note(self, eid: str, **attrs: str) -> str:
        self.entities.setdefault(eid, {"kind": eid.rsplit(" ", 1)[0]}).update(attrs)
        return eid

    def anchor(self, rec: str) -> str:
        self.last_rec = self.note(rec, role="receptacle")
        return self.last_rec


def parse(text: str) -> ParsedObservation:
    p = ParsedObservation()
    covered = [False] * len(text)
    for name, pattern in RULES:
        for m in pattern.finditer(text):
            _apply(p, name, m)
            for i in range(m.start(), m.end()):
                covered[i] = True
    for sentence in re.split(r"\n+|(?<=[.!?]) +", text):
        s = sentence.strip()
        if not s:
            continue
        i = text.find(s)
        if i >= 0 and not any(covered[i: i + len(s)]):
            p.unparsed.append(s)
    return p


def _apply(p: ParsedObservation, name: str, m: re.Match) -> None:
    g, span, raw = m.groupdict(), (m.start(), m.end()), m.group(0)
    if name == "instruction":
        p.instruction = (g.get("goal") or "").strip()
        p.add("instruction", subject="task", state=p.instruction, span=span, text=raw)
    elif name == "in_room":
        # the environment says the agent is "in a room"; it never names which one, so
        # no receptacle is invented here
        p.add("location", subject="agent", related="room", state="unnamed", span=span, text=raw)
    elif name == "room_view":
        items = _items(g.get("items"))
        if not items:
            # measured, not hypothetical: after the opening message `look` answers
            # "you see nothing" in every game probed, so the emptiness is the fact
            p.add("room_lists", subject="room", related="none", state="empty",
                  span=span, text=raw)
        for it in items:
            p.note(it, role="receptacle")
            p.add("room_lists", subject="room", related=it, span=span, text=raw)
    elif name == "arrive":
        p.location = p.anchor(g["rec"])
        p.add("location", subject="agent", related=p.location, span=span, text=raw)
    elif name == "rec_state":
        rec = p.anchor(g["rec"])
        p.add("receptacle_state", subject=rec, state=g["state"], span=span, text=raw)
    elif name == "contents_in":
        holder = p.last_rec or "unknown"
        items = _items(g.get("items"))
        if not items:
            p.add("contents", subject=holder, related="none", state="empty", span=span, text=raw)
        for it in items:
            p.note(it)
            p.add("contents", subject=holder, related=it, span=span, text=raw)
    elif name == "contents_on":
        rec = p.anchor(g["rec"])
        items = _items(g.get("items"))
        if not items:
            p.add("contents", subject=rec, related="none", state="empty", span=span, text=raw)
        for it in items:
            p.note(it)
            p.add("contents", subject=rec, related=it, span=span, text=raw)
    elif name == "facing":
        for it in _items(g.get("items")):
            p.note(it, role="receptacle")
            p.add("facing", subject="agent", related=it, span=span, text=raw)
        for it in _items(g.get("near")):
            p.note(it)
            p.add("contents", subject=p.location or p.last_rec or "unknown", related=it,
                  span=span, text=raw)
    elif name == "described":
        obj = p.note(g["obj"], state=g["state"] + (f" and {g['state2']}" if g.get("state2") else ""))
        p.add("object_state", subject=obj,
              state=g["state"] + (f" and {g['state2']}" if g.get("state2") else ""),
              span=span, text=raw)
    elif name == "picked_up":
        obj, rec = p.note(g["obj"], held="true"), p.anchor(g["rec"])
        p.held, p.held_said = [obj], True
        p.add("held", subject="agent", related=obj, span=span, text=raw)
        p.add("left", subject=rec, related=obj, span=span, text=raw)
    elif name == "moved_to":
        obj, rec = p.note(g["obj"]), p.anchor(g["rec"])
        p.held, p.held_said = [], True
        p.add("held", subject="agent", related="none", state="empty", span=span, text=raw)
        p.add("contents", subject=rec, related=obj, span=span, text=raw)
    elif name in ("opened", "closed"):
        rec = p.anchor(g["rec"])
        p.add("receptacle_state", subject=rec, state="open" if name == "opened" else "closed",
              span=span, text=raw)
    elif name == "used":
        rec = p.anchor(g["rec"])
        p.add("receptacle_state", subject=rec, state=g["dir"], span=span, text=raw)
    elif name == "processed":
        obj, rec = p.note(g["obj"], state=g["op"]), p.anchor(g["rec"])
        p.add("operation", subject=obj, related=rec, state=g["op"], span=span, text=raw)
    elif name == "carrying":
        items = _items(g.get("items"))
        p.held_said, p.held = True, items
        if not items:
            p.add("held", subject="agent", related="none", state="empty", span=span, text=raw)
        for it in items:
            p.note(it, held="true")
            p.add("held", subject="agent", related=it, span=span, text=raw)
    elif name == "no_effect":
        p.add("no_effect", subject="environment", state="nothing_happened", span=span, text=raw)
    elif name == "no_new_information":
        p.add("no_new_information", subject=p.note(g["obj"]), span=span, text=raw)
    elif name in ("command_list", "banner"):
        # the environment describing itself is not a fact about the world
        p.add("self_description", subject="environment", state=name, span=span, text=raw[:60])
    elif name in ("win", "lose"):
        p.terminal_word = name
        p.add("lifecycle", subject="environment", state=name, span=span, text=raw)
