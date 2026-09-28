"""The native action catalogue of the MuJoCo channel (SPEC-v0.2 §10, SPEC-BST 3.4).

Four names, and they are the four the frozen `core/contracts.py:SkillName` already
carries: `observe`, `pick`, `place`, `safe_retreat`. Nothing was added. That is a
deliberate constraint, and it is the point of this file — the v0.2 schema is frozen
(`configs/experiment/v02_schema_freeze.json`), and a benchmark that could only be run
by widening the action vocabulary would not be measuring transfer of the same agent,
it would be measuring a different agent. The verbs mean what they mean on the desktop
sim; what changed is the world underneath them: one `pick` here is a closed-loop
Cartesian servo with a constant gripper effort, not an IK move.

Args are entity *names*, never coordinates. The agent says `place(peg 1, socket 1)`;
the measured pose of `socket 1` is read from the world the decision was based on and
recorded beside the execution so the two can be compared afterwards. Choosing numbers
in a workspace would hand the decision layer a job the perceptual layer already does,
and would make an unparsable float a decision failure rather than a sensor failure.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

#: the same instance-reference shape the text channel uses, so one rule covers both
INSTANCE_RE = re.compile(r"^(peg|socket|hand) \d+$")

#: skill -> (required args, optional args). `safe_retreat` takes none of its own.
#: `observe`'s optional `view` is the camera to measure with, and it is legal here because
#: `perception/arm.py` implements the whole request (`_before_execute` files it, `_capture_world`
#: measures from it, `_validate_execution` refuses a name that is no camera). Before #117 the
#: grammar said `((), ())`, so a model told "a further look from 'gripperPOV' could resolve this"
#: had no act that could do it: `core/planner.py`'s OPTIONAL_ARGS had allowed `view` all along.
SIGNATURES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "observe": ((), ("view",)),
    "pick": (("object_id",), ()),
    "place": (("object_id", "target_id"), ()),
    "safe_retreat": ((), ("object_id",)),
}

#: verbs that belong to another backend, named so a refusal can say why
FOREIGN = ("alfred_go", "alfred_take", "alfred_move", "alfred_open", "alfred_close",
           "alfred_use", "alfred_clean", "alfred_heat", "alfred_cool",
           "alfred_examine", "alfred_inventory")

CATALOGUE: dict[str, dict[str, Any]] = {
    "observe": {
        "description": "Take a fresh measurement of the scene from the cameras. Costs no "
                       "environment step.",
        "args": {"view": "which camera to measure with; the cameras this run declares are "
                         "named in this entry, and leaving it out measures again from the last "
                         "one used"}, "pre": ["none"],
        "post": ["a snapshot whose entities carry the poses measured at this instant"]},
    "pick": {
        "description": "Move the gripper to the named object, close on it, and lift it to "
                       "carry height. One skill call; the motion underneath it is the "
                       "robot's actuator, not a plan the system is offering you.",
        "args": {"object_id": "the object to grasp, named exactly as the snapshot named it"},
        "pre": ["the named object is one this snapshot measured a position for",
                "the gripper is open, or already holds that same object"],
        "post": ["the snapshot after the call states where the object ended up, and whether "
                 "it is measured as held"]},
    "place": {
        "description": "With the named object held, drive it to the named target's measured "
                       "position and push it in. Whether it stays is the world's answer.",
        "args": {"object_id": "the held object", "target_id": "the target to insert into"},
        "pre": ["this snapshot measures the object as held",
                "the target is one this snapshot measured a position for"],
        "post": ["the snapshot after the call states the object's measured position relative "
                 "to the target; it does not state whether the benchmark scored it"]},
    "safe_retreat": {
        "description": "Open the gripper and raise the hand clear of the workspace. Use it "
                       "when the last measurement says the object is not where you meant it.",
        "args": {"object_id": "optional: what you are giving up"},
        "pre": ["none"],
        "post": ["the hand is at a measured retreat pose and the gripper is open"]},
}


def render(skill: str, args: dict[str, str]) -> tuple[Optional[dict[str, str]], list[str]]:
    """One legal skill call -> the resolved argument pair, or the reasons it is not one.

    Returns `(None, reasons)` for anything this world does not implement. It never
    guesses which of two objects was meant and never fills a missing one: that would be
    the adapter planning (§10)."""
    reasons: list[str] = []
    if skill in FOREIGN:
        return None, [f"skill {skill!r} belongs to the ALFWorld text backend; this "
                      f"environment offers {', '.join(sorted(SIGNATURES))}"]
    if skill not in SIGNATURES:
        return None, [f"unknown skill {skill!r}"]
    required, optional = SIGNATURES[skill]
    allowed = set(required) | set(optional)
    cleaned: dict[str, str] = {}
    for name in required:
        value = args.get(name)
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"{skill} requires args[{name!r}] as an instance name")
            continue
        value = value.strip()
        if not INSTANCE_RE.match(value):
            reasons.append(f"args[{name!r}]={value!r} is not an instance name of the form "
                           f"'<kind> <number>' as the snapshot wrote it")
            continue
        cleaned[name] = value
    for name in args:
        if name not in allowed:
            reasons.append(f"{skill} does not take args[{name!r}]; it accepts "
                           f"{sorted(allowed) or 'no arguments'}")
    if reasons:
        return None, reasons
    return cleaned, []


def catalogue_sha256() -> str:
    payload = json.dumps({"signatures": {k: [list(v)] for k, v in sorted(SIGNATURES.items())},
                          "catalogue": CATALOGUE, "foreign": list(FOREIGN)},
                          ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["CATALOGUE", "SIGNATURES", "FOREIGN", "INSTANCE_RE", "render", "catalogue_sha256"]
