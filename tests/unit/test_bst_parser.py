"""Deterministic reading of the environment's sentences (SPEC-BST 4.2, 4.4, 13.5).

Phase 0 measured 0 sentences left unread across 1,202 complete observations, so this
file is not about coverage. It is about the four ways a reader can lie: inventing a
fact the text did not state, treating silence as a negative, carrying a fact across
snapshots, and hiding a sentence it could not read. Each test below takes one
message shape the installed grammar was observed to emit and asks what the snapshot
is allowed to say.
"""
from __future__ import annotations

import pytest

from embodied_agent.benchmark.parser import (
    ANCHOR_RULES,
    ANAPHORA_RULES,
    RULES,
    parse,
)
from embodied_agent.benchmark.state import parse_status_of, text_world_state

RESET = ("-= Welcome to TextWorld, ALFRED! =-\n\nYou are in the middle of a room. Looking "
         "quickly around you, you see a cabinet 6, a shelf 2, and a mug 2.\n\nYour task is "
         "to: put some mug on shelf.")


def kinds(p):
    return [f.kind for f in p.facts]


def of(p, kind: str):
    return [f for f in p.facts if f.kind == kind]


# ------------------------------------------------------------ what is stated ----


def test_the_instruction_is_kept_word_for_word():
    p = parse(RESET)
    assert p.instruction == "put some mug on shelf"
    row = of(p, "instruction")[0]
    assert row.subject == "task" and row.state == "put some mug on shelf"
    assert RESET[row.span[0]:row.span[1]] == row.text        # the span is the sentence


def test_a_room_view_lists_only_the_instances_the_sentence_names():
    p = parse(RESET)
    listed = {f.related for f in of(p, "room_lists")}
    assert listed == {"cabinet 6", "shelf 2", "mug 2"}
    assert set(p.entities) == {"cabinet 6", "shelf 2", "mug 2"}
    assert p.entities["cabinet 6"]["role"] == "receptacle"
    # the room is never given a name the text did not use
    assert of(p, "location")[0].related == "room" and p.location is None


def test_an_empty_room_view_is_a_stated_emptiness_not_an_absence():
    p = parse("You are in the middle of a room. Looking quickly around you, you see nothing.")
    rows = of(p, "room_lists")
    assert [(r.related, r.state) for r in rows] == [("none", "empty")]
    assert p.entities == {}


def test_the_anaphora_resolves_to_the_receptacle_the_previous_sentence_named():
    p = parse("You open the cabinet 6. In it, you see a mug 4.")
    assert of(p, "receptacle_state")[0].state == "open"
    contents = of(p, "contents")[0]
    assert (contents.subject, contents.related) == ("cabinet 6", "mug 4")


@pytest.mark.parametrize("text,holder", [
    ("You open the cabinet 6. In it, you see a mug 4.", "cabinet 6"),
    ("You arrive at cabinet 6. The cabinet 6 is closed. In it, you see a mug 4.", "cabinet 6"),
    ("You close the fridge 1. In it, you see a potato 2.", "fridge 1"),
    ("You turn on the desklamp 1. In it, you see nothing.", "desklamp 1"),
    ("You pick up the egg 1 from the microwave 1. In it, you see a cup 1.", "microwave 1"),
    ("You move the mug 2 to the shelf 2. In it, you see a book 1.", "shelf 2"),
    ("You heat the potato 1 with the stoveburner 2. In it, you see a pan 1.", "stoveburner 2"),
])
def test_opening_a_container_is_the_shape_that_most_needs_the_holder_to_be_right(
        text, holder):
    """"You open the X. In it, you see Y." is the most frequent two-sentence reply in
    the measured corpus, so `last_rec` has to be set by the time `In it` is read.
    A contents fact attached to no holder is not a conservative reading: it turns the
    object's location into `unknown` and the model then has nowhere to put anything."""
    p = parse(text)
    rows = of(p, "contents")
    assert rows and all(r.subject == holder for r in rows), [(r.subject, r.related) for r in rows]
    assert p.entities[holder]["role"] == "receptacle"


def test_no_rule_that_only_sets_an_anchor_is_listed_after_one_that_reads_it():
    order = [name for name, _ in RULES]
    first_reader = min(order.index(r) for r in ANAPHORA_RULES)
    late = [r for r in ANCHOR_RULES if order.index(r) > first_reader]
    assert late == [], f"{late} run after the anaphora that needs them"


def test_an_anaphora_with_no_anchor_stays_unknown_rather_than_guessing():
    """"In it" with no receptacle named before it has no antecedent in this snapshot.
    The nearest noun, the last location, the model's intent: each would be invented."""
    p = parse("In it, you see a mug 4.")
    contents = of(p, "contents")[0]
    assert contents.subject == "unknown" and contents.related == "mug 4"


def test_arriving_sets_the_location_that_a_later_listing_is_read_against():
    p = parse("You arrive at dresser 1. On the dresser 1, you see a watch 1 and a mug 3.")
    assert p.location == "dresser 1"
    assert of(p, "location")[0].related == "dresser 1"
    assert sorted(f.related for f in of(p, "contents")) == ["mug 3", "watch 1"]


def test_holding_is_read_from_the_sentence_that_reports_it():
    p = parse("You pick up the mug 2 from the shelf 2.")
    assert p.held_said is True and p.held == ["mug 2"]
    assert of(p, "held")[0].related == "mug 2" and of(p, "left")[0].subject == "shelf 2"

    empty = parse("You are carrying: nothing.")
    assert empty.held_said is True and empty.held == []
    assert of(empty, "held")[0].state == "empty"


def test_an_appliance_state_comes_from_the_verb_not_from_the_object_name():
    p = parse("You turn on the desklamp 1.")
    row = of(p, "receptacle_state")[0]
    assert (row.subject, row.state) == ("desklamp 1", "on")
    assert parse("You heat the mug 2 with the microwave 1.").facts
    assert of(parse("You heat the mug 2 with the microwave 1."), "operation")[0].state == "heat"


# ------------------------------------------------- silence is not a negative ----


def test_a_response_that_says_nothing_about_hands_leaves_the_hands_unknown():
    """The environment reporting a shelf is not the environment reporting an empty
    hand, and `held_object=None` would be a claim about the gripper."""
    w = text_world_state("You arrive at shelf 2. On the shelf 2, you see a book 1.", 1, "obs_1")
    assert w.held_object == "unknown"
    assert {str(e.held) for e in w.entities} == {"unknown"}


def test_a_refusal_is_the_environment_s_own_fact_and_is_not_widened():
    p = parse("Nothing happens.")
    assert kinds(p) == ["no_effect"]
    assert of(p, "no_effect")[0].state == "nothing_happened"
    # the sentence says the command had no stated effect; it does not say why, and a
    # cause (not carrying it / too far / closed) would be a fabricated failure reason
    assert not any(k in kinds(p) for k in ("contents", "held", "location"))


def test_no_geometry_position_or_support_is_invented_for_a_text_thing():
    w = text_world_state(RESET, 1, "obs_1")
    for e in w.entities:
        assert e.pose is None and e.geometry is None
        assert e.supported_by == "unknown" and e.at_rest == "unknown"
        assert str(e.body_id) != ""           # the placeholder the contract allows
    assert w.targets == [] and w.occupancy == []
    assert w.location == "unknown" and w.parse_status == "parsed"


# ------------------------------------------------------- one snapshot, one read ----


def test_a_second_snapshot_remembers_nothing_from_the_first():
    """No persistent object map (SPEC-BST 4.2): the facts of a snapshot are the facts
    of its own text, so a book that stopped being named is not a book that moved."""
    seen = text_world_state("You arrive at shelf 2. On the shelf 2, you see a book 1.",
                            1, "obs_1")
    later = text_world_state("You arrive at cabinet 6. The cabinet 6 is closed.", 2, "obs_2")
    assert any(f.related == "book 1" for f in seen.facts)
    assert not any("book 1" in (f.subject, f.related) for f in later.facts)
    assert "book 1" not in [e.entity_id for e in later.entities]
    assert all(f.observation_ref == "obs_1" for f in seen.facts)
    assert all(f.observation_ref == "obs_2" for f in later.facts)


def test_every_fact_carries_the_ref_of_the_snapshot_that_stated_it():
    w = text_world_state(RESET, 7, "obs_0007")
    assert w.state_version == 7 and w.observation_ref == "obs_0007"
    assert {f.observation_ref for f in w.facts} == {"obs_0007"}


# --------------------------------------------------------------- unread text ----


def test_a_sentence_no_rule_reads_is_kept_verbatim_and_the_status_says_so():
    odd = "You contemplated the shelf 2 for a moment."
    w = text_world_state(f"{RESET}\n\n{odd}", 1, "obs_1")
    assert w.unparsed == [odd] and w.parse_status == "partial"
    assert odd in w.raw_observation
    assert parse_status_of([], [odd]) == "unparsed"
    assert parse_status_of(list(w.facts), []) == "parsed"


def test_the_whole_of_an_unreadable_text_is_unreadable_rather_than_empty():
    w = text_world_state("Zzz. Blah blah 42.", 1, "obs_1")
    assert w.parse_status == "unparsed" and w.facts == []
    assert w.unparsed == ["Zzz.", "Blah blah 42."]      # per sentence, each verbatim
    assert w.entities == []
    # an empty string is not "text the reader failed on" — there was nothing to read,
    # and `unparsed` must not become the place where a missing response is guessed at
    assert text_world_state("", 1, "obs_1").unparsed == []


def test_the_engine_describing_itself_is_not_a_fact_about_the_world():
    """Phase 0 measured that this benchmark never asks for a command list; if one ever
    arrives in the text it is labelled self-description and contributes no entity, no
    location and no candidate (SPEC-BST 4.5)."""
    p = parse("Available commands:\n> go to shelf 2\n> put mug 2 in shelf 2\n")
    assert kinds(p) == ["self_description"]
    assert p.entities == {} and p.location is None and p.held == []
    assert p.facts[0].subject == "environment"


def test_the_end_words_are_lifecycle_not_a_grade():
    won, lost = parse("Success!"), parse("Game over.")
    assert of(won, "lifecycle")[0].state == "win" and won.terminal_word == "win"
    assert of(lost, "lifecycle")[0].state == "lose" and lost.terminal_word == "lose"
    assert not any(k in kinds(won) for k in ("score", "reward"))


# --------------------------------------------------------------- the rule set ----

# one text per message shape the installed grammar was measured emitting; the point
# of the list is that no rule is dead weight, and every rule is answerable to a shape
# a real episode produced
CORPUS = [
    RESET,
    "You are in the middle of a room. Looking quickly around you, you see nothing.",
    "You arrive at dresser 1. On the dresser 1, you see a watch 1 and a mug 3.",
    "The cabinet 6 is closed.",
    "You open the cabinet 6. In it, you see a mug 4.",
    "You are facing the nightstand 2. Next to it, you see nothing.",
    "This is a clean mug 2.",
    "This is a hot and clean apple 1.",
    "You pick up the mug 2 from the shelf 2.",
    "You move the mug 2 to the shelf 2.",
    "You close the cabinet 6.",
    "You turn on the desklamp 1.",
    "You heat the mug 2 with the microwave 1.",
    "You are carrying: a mug 2.",
    "You are not carrying anything.",
    "Nothing happens.",
    "There's nothing special about the mug 2.",
    "Available commands:\n> go to shelf 2\n",
    "-= Welcome to TextWorld, ALFRED! =-",
    "Success!",
    "Game over.",
]


def test_every_rule_matches_at_least_one_measured_shape():
    unused = [name for name, pattern in RULES if not any(pattern.search(t) for t in CORPUS)]
    assert unused == []


def test_the_measured_shapes_are_all_read():
    """0 unread sentences across this corpus is the same property Phase 0 measured
    over 1,202 real observations, kept here where a rule edit has to answer to it."""
    for text in CORPUS:
        assert not parse(text).unparsed, text


def test_facts_are_named_by_kind_and_never_depend_on_a_snapshot_that_changed():
    kinds_seen = set()
    for text in CORPUS:
        p = parse(text)
        kinds_seen |= set(kinds(p))
        for f in p.facts:
            assert f.kind and f.subject
            # the quoted text is the span's own text, possibly the self-
            # description rules' 60-character cut
            assert f.span is None or text[f.span[0]:].startswith(f.text)
    assert {"instruction", "room_lists", "contents", "receptacle_state", "held",
            "operation", "no_effect", "self_description", "lifecycle"} <= kinds_seen
