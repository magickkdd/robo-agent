"""The static native action catalogue of the ALFWorld text backend (SPEC-BST 3.4, 4).

One entry == one command the environment itself accepts. Nothing here chooses a
target, fills in a missing navigation step or combines two verbs: `render` turns a
skill name plus argument ids into exactly one command string, and refuses with a
reason when it cannot do that literally.

The frames are the ones the installed grammar was observed to emit: 13,865 sampled
admissible commands over 72 development games (benchmark/probe.py) produced
`go to`, `open`, `close`, `take … from …`, `move … to …`, `use`, `heat/cool/clean …
with …`, `examine`, `look`, `inventory` — and no `put … in/on …` at all, which is
why placing is named `alfred_move` after the verb the environment actually accepts.

One frame in that sample is not in the catalogue: `slice <bread> with <butterknife>`
appeared once, and no task type in the six this benchmark scores asks for a cut thing.
Exposing it would add a verb the frozen core cannot be graded on, so it is recorded
here as a measured omission rather than silently left out (SPEC-BST 3.6: 仅暴露该版本
实际存在的命令 — "exists" and "is reachable by a scored task" are different claims).
"""
from __future__ import annotations

import re
from typing import Any, Optional

# an instance reference as the demangled environment names it: "mug 2", "cabinet 6"
INSTANCE_RE = re.compile(r"^[a-z][a-z\-]* \d+$")

# skill -> (command template, required args, optional args)
TEMPLATES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "alfred_go": ("go to {target_id}", ("target_id",), ()),
    "alfred_open": ("open {target_id}", ("target_id",), ()),
    "alfred_close": ("close {target_id}", ("target_id",), ()),
    "alfred_use": ("use {target_id}", ("target_id",), ()),
    "alfred_take": ("take {object_id} from {target_id}", ("object_id", "target_id"), ()),
    "alfred_move": ("move {object_id} to {target_id}", ("object_id", "target_id"), ()),
    "alfred_heat": ("heat {object_id} with {target_id}", ("object_id", "target_id"), ()),
    "alfred_cool": ("cool {object_id} with {target_id}", ("object_id", "target_id"), ()),
    "alfred_clean": ("clean {object_id} with {target_id}", ("object_id", "target_id"), ()),
    "alfred_examine": ("examine {target_id}", ("target_id",), ()),
    "observe": ("look", (), ()),
    "alfred_inventory": ("inventory", (), ()),
}

ARG_DOC = {
    "target_id": "one receptacle or thing named exactly as the observation named it",
    "object_id": "one portable object named exactly as the observation named it",
}

CATALOGUE: dict[str, dict[str, Any]] = {
    "alfred_go": {"description": "Move to a named receptacle. The environment reports what is "
                                 "on or in it once you arrive.",
                  "args": {"target_id": ARG_DOC["target_id"]},
                  "pre": ["the receptacle is one this game has, by that name"],
                  "post": ["your location changes; the environment's sentence is the result"]},
    "alfred_open": {"description": "Open a named receptacle.",
                    "args": {"target_id": ARG_DOC["target_id"]},
                    "pre": ["the named receptacle is the one you mean"],
                    "post": ["the environment says whether it opened and what it contains"]},
    "alfred_close": {"description": "Close a named receptacle.",
                     "args": {"target_id": ARG_DOC["target_id"]},
                     "pre": ["the named receptacle is the one you mean"],
                     "post": ["the environment says whether it closed"]},
    "alfred_use": {"description": "Use a named receptacle (switches a lamp on or off).",
                   "args": {"target_id": ARG_DOC["target_id"]},
                   "pre": ["the named receptacle is usable"],
                   "post": ["the environment says what happened"]},
    "alfred_take": {"description": "Pick up a named object from a named receptacle.",
                    "args": {"object_id": ARG_DOC["object_id"], "target_id": ARG_DOC["target_id"]},
                    "pre": ["both names are the ones in the current text",
                            "your hands are free or already hold object_id"],
                    "post": ["the environment says whether you are now carrying it"]},
    "alfred_move": {"description": "Release the object you hold onto or into a named receptacle.",
                    "args": {"object_id": ARG_DOC["object_id"], "target_id": ARG_DOC["target_id"]},
                    "pre": ["you hold object_id"],
                    "post": ["the environment says where the object ended up"]},
    "alfred_heat": {"description": "Heat the held object with a named appliance.",
                    "args": {"object_id": ARG_DOC["object_id"], "target_id": ARG_DOC["target_id"]},
                    "pre": ["you hold object_id", "the appliance is the one you mean"],
                    "post": ["the environment says whether it heated"]},
    "alfred_cool": {"description": "Cool the held object with a named appliance.",
                    "args": {"object_id": ARG_DOC["object_id"], "target_id": ARG_DOC["target_id"]},
                    "pre": ["you hold object_id", "the appliance is the one you mean"],
                    "post": ["the environment says whether it cooled"]},
    "alfred_clean": {"description": "Clean the held object with a named fixture.",
                     "args": {"object_id": ARG_DOC["object_id"], "target_id": ARG_DOC["target_id"]},
                     "pre": ["you hold object_id", "the fixture is the one you mean"],
                     "post": ["the environment says whether it cleaned"]},
    "alfred_examine": {"description": "Look closely at one named thing.",
                       "args": {"target_id": ARG_DOC["target_id"]},
                       "pre": ["the name is in the current text"],
                       "post": ["the environment describes it"]},
    "observe": {"description": "Look around your current location.",
                "args": {}, "pre": ["none"],
                "post": ["a fresh observation naming what the environment reports here"]},
    "alfred_inventory": {"description": "Check what you are carrying.",
                         "args": {}, "pre": ["none"],
                         "post": ["a fresh observation about your hands"]},
}

# The verbs a text episode may never reach for, named so a rejection can say why.
DESKTOP_ONLY = ("pick", "place", "safe_retreat")


def render(skill: str, args: dict[str, str]) -> tuple[Optional[str], list[str]]:
    """One legal skill call -> exactly one command string, or the reasons it is not one.

    Names are used verbatim: an id that was not observed is a refusal, never a
    substring match, because choosing which of two `mug`s the model meant would be
    the adapter acting for it (SPEC-BST 3.5)."""
    reasons: list[str] = []
    if skill in DESKTOP_ONLY:
        return None, [f"skill {skill!r} belongs to the desktop backend; this environment "
                      f"offers {', '.join(sorted(TEMPLATES))}"]
    if skill not in TEMPLATES:
        return None, [f"unknown skill {skill!r}"]
    template, required, optional = TEMPLATES[skill]
    allowed = set(required) | set(optional)
    for name in required:
        value = args.get(name)
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"{skill} requires args[{name!r}] as a named instance")
            continue
        if not INSTANCE_RE.match(value.strip()):
            reasons.append(f"args[{name!r}]={value!r} is not an instance reference of the form "
                           f"'<kind> <number>' as the observation wrote it")
    for name in args:
        if name not in allowed:
            reasons.append(f"{skill} does not take args[{name!r}]; it maps to "
                           f"'{template}'")
    if reasons:
        return None, reasons
    cleaned = {k: str(v).strip() for k, v in args.items() if k in allowed}
    return template.format(**cleaned), []


def catalogue_sha256() -> str:
    import hashlib
    import json
    payload = json.dumps({"templates": {k: list(v) for k, v in sorted(TEMPLATES.items())},
                          "catalogue": CATALOGUE}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
