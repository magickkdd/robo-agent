"""The static native action catalogue (SPEC-BST 3.4, 4.5, 13.1).

`render` is the only place this benchmark turns a skill name into a string the
engine can read, so the claim under test is narrow and expensive: one legal call
becomes *exactly one* command, that command is the verb the environment itself
accepts, and everything else is a refusal that says why. Nothing here may compose
two verbs, supply a missing navigation step, or resolve a name the model did not
observe — each of those would be the adapter acting for the subject.
"""
from __future__ import annotations

import copy
import json

import pytest

from embodied_agent.benchmark.native_actions import (
    ARG_DOC,
    CATALOGUE,
    DESKTOP_ONLY,
    INSTANCE_RE,
    TEMPLATES,
    catalogue_sha256,
    render,
)

# one plausible instance reference per required argument, so every template can be
# exercised without the test deciding what the model "meant"
GOOD = {"target_id": "shelf 2", "object_id": "mug 2"}


def _args(skill: str) -> dict[str, str]:
    required, _ = TEMPLATES[skill][1], TEMPLATES[skill][2]
    return {name: GOOD[name] for name in required}


# ------------------------------------------------------- one call, one command --


@pytest.mark.parametrize("skill", sorted(TEMPLATES))
def test_every_catalogued_skill_renders_exactly_one_command(skill):
    command, reasons = render(skill, _args(skill))
    assert reasons == []
    assert isinstance(command, str) and command
    # one command: no sequencing, no conjunction, no trailing prompt text
    assert "\n" not in command and ";" not in command and " and then " not in command
    assert command == TEMPLATES[skill][0].format(**_args(skill))


@pytest.mark.parametrize("skill", sorted(TEMPLATES))
def test_the_catalogue_documents_every_skill_and_only_those_skills(skill):
    entry = CATALOGUE[skill]
    assert set(entry) == {"description", "args", "pre", "post"}
    required, optional = TEMPLATES[skill][1], TEMPLATES[skill][2]
    assert set(entry["args"]) == set(required) | set(optional)
    assert entry["args"] == {k: ARG_DOC[k] for k in entry["args"]}
    # a description of an effect is not a plan: nothing in the entry names a target
    text = json.dumps(entry, ensure_ascii=False)
    for name in sorted(TEMPLATES):
        assert name not in text, f"{skill}'s entry points at another skill {name}"


def test_no_skill_body_is_issued_and_the_refusal_says_which_backend_it_belongs_to():
    for skill in DESKTOP_ONLY:
        command, reasons = render(skill, {"object_id": "mug 2", "target_id": "shelf 2"})
        assert command is None
        assert any("desktop backend" in r for r in reasons), (skill, reasons)
    assert set(DESKTOP_ONLY) & set(TEMPLATES) == set()


def test_the_environment_offers_no_put_verb_and_none_is_invented():
    """Phase 0 sampled 13,865 admissible commands over 72 development games and the
    grammar never offered `put … in/on …`; placing is `move … to …`. A friendlier
    alias here would be a command the engine answers only with "Nothing happens.",
    which would read as a model failure."""
    assert not any("put" in TEMPLATES[s][0] for s in TEMPLATES)
    assert TEMPLATES["alfred_move"][0] == "move {object_id} to {target_id}"
    for alias in ("alfred_put", "alfred_place", "put"):
        command, reasons = render(alias, _args("alfred_move"))
        assert command is None and any("unknown skill" in r for r in reasons)


# ---------------------------------------------------------------- refusals -----


@pytest.mark.parametrize("skill", sorted(s for s, t in TEMPLATES.items() if t[1]))
def test_a_missing_argument_is_a_refusal_not_a_placeholder(skill):
    for name in TEMPLATES[skill][1]:
        args = _args(skill)
        del args[name]
        command, reasons = render(skill, args)
        assert command is None
        assert any(name in r for r in reasons), (skill, name, reasons)


@pytest.mark.parametrize("bad", ["mug", "the mug", "Mug 2", "mug 2a", "mug  2", "", "  ",
                                 "shelf", "1", "mug two"])
def test_a_name_that_is_not_an_observed_instance_reference_is_refused(bad):
    """`mug` is not `mug 2` and not `mug 3`: choosing between them is the model's
    job, and the regex is a shape check, not a fuzzy match (SPEC-BST 3.5)."""
    assert not INSTANCE_RE.match(bad.strip())
    command, reasons = render("alfred_move", {"object_id": bad, "target_id": "shelf 2"})
    assert command is None
    assert any("instance reference" in r or "named instance" in r
               for r in reasons), (bad, reasons)


def test_surrounding_whitespace_is_the_only_thing_stripped():
    command, reasons = render("alfred_move", {"object_id": "  mug 2 ", "target_id": "shelf 2"})
    assert reasons == [] and command == "move mug 2 to shelf 2"


def test_an_argument_the_template_does_not_use_is_refused_rather_than_dropped():
    """Silently ignoring `placement` would let a desktop-shaped decision become a
    half-filled text command."""
    command, reasons = render("alfred_move", {**_args("alfred_move"), "placement": "left"})
    assert command is None
    assert any("does not take args['placement']" in r for r in reasons)


def test_a_navigation_command_takes_the_place_of_nothing():
    """`go to` has one argument and gets no `open`, no `look inside`: the arriving is
    the whole action, and whatever the environment says next is the model's to read."""
    command, reasons = render("alfred_go", {"target_id": "cabinet 6"})
    assert (command, reasons) == ("go to cabinet 6", [])
    for extra in ("object_id", "open", "on_open"):
        command, reasons = render("alfred_go", {"target_id": "cabinet 6", extra: "mug 2"})
        assert command is None


@pytest.mark.parametrize("value", [None, 2, ["mug 2"], {"id": "mug 2"}, True])
def test_a_non_string_argument_is_refused_not_stringified(value):
    command, reasons = render("alfred_move", {"object_id": value, "target_id": "shelf 2"})
    assert command is None
    assert any("named instance" in r for r in reasons)


# ------------------------------------------------------------- the fingerprint --


def test_the_catalogue_fingerprint_pins_templates_and_descriptions():
    a = catalogue_sha256()
    assert a == catalogue_sha256() and len(a) == 64
    saved = copy.deepcopy(TEMPLATES["alfred_move"])
    try:
        TEMPLATES["alfred_move"] = ("put {object_id} on {target_id}", saved[1], saved[2])
        assert catalogue_sha256() != a
    finally:
        TEMPLATES["alfred_move"] = saved
