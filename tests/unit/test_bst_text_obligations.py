"""What one public text sentence owes, and what one text snapshot can answer about it (§5.2, §10).

`benchmark/obligations.py` is the text channel's half of 任务理解 + Goal/Subgoal 生成, and every
sentence quoted below is a sentence a real episode of the pinned install actually printed (the six
instructions are the first game of each type in `task_manifest.json`; the snapshots are the
`environment_text` of `/tmp/p5b_matrixA`). That matters for the same reason it does in
`test_bst_parser.py`: a test written against an invented sentence tests the test.

Four claims, in the order the phase needed them:

* **the reader adds nothing** — every noun of a row is a word of the instruction, and an unread
  sentence authors no row (SPEC §10: 不得替 Agent 规划; §5.2: 目标来自任务);
* **the edges are readings of English, not of the layout** — each `Dependency` says in its own note
  what it rested on, including the one edge this channel cannot close by measurement;
* **silence is not a negative** — three of the four predicate shapes have no `false`, because
  "看不到物体不等于物体不存在" (SPEC-BST 4.2);
* **`lit` is the exception, and it is an exception because the engine states it** (`You turn off the
  desklamp 1.`).
"""
from __future__ import annotations

import re

import pytest

from embodied_agent.benchmark.obligations import (
    RULES, derive_text, evidence_for, normalized_goal, obligations_of, obligations_sha256,
    problems_of)
from embodied_agent.benchmark.state import goal_from_instruction, text_world_state
from embodied_agent.core.contracts import PredicateVerdict, TaskInput

# ------------------------------------------------- the six sentences, as the batch said them ----
SIX = {
    "look_at_obj_in_light": "examine the mug with the desklamp",
    "pick_and_place_simple": "put some saltshaker on drawer",
    "pick_clean_then_place_in_recep": "clean some soapbar and put it in cabinet",
    "pick_cool_then_place_in_recep": "put a cool lettuce in countertop",
    "pick_heat_then_place_in_recep": "put a hot potato in garbagecan",
    "pick_two_obj_and_place": "find two peppershaker and put them in drawer",
}
#: what each of those sentences owes, in `predicate_id` form, measured off the batch's own plans
EXPECTED = {
    "look_at_obj_in_light": ["looked_at:mug", "lit:desklamp"],
    "pick_and_place_simple": ["in1:saltshaker:drawer"],
    "pick_clean_then_place_in_recep": ["clean:soapbar", "in1:soapbar:cabinet"],
    "pick_cool_then_place_in_recep": ["cool:lettuce", "in1:lettuce:countertop"],
    "pick_heat_then_place_in_recep": ["hot:potato", "in1:potato:garbagecan"],
    "pick_two_obj_and_place": ["in2:peppershaker:drawer"],
}

# --------------------------------------------------------- real snapshots, quoted verbatim ----
COOL_LEDGER = "You cool the lettuce 1 using the fridge 1."
HEAT_LEDGER = "You heat the potato 1 using the microwave 1."
TWO_POTATOES = ("You arrive at countertop 2. On the countertop 2, you see a bowl 1, a bread 1, "
                "a butterknife 1, a cellphone 1, a glassbottle 1, a knife 1, a peppershaker 2, "
                "a potato 2, a potato 1, a spoon 2, and a statue 1.")
LETTUCE_IN_COUNTERTOP = ("You arrive at countertop 1. On the countertop 1, you see a apple 1, "
                         "a cellphone 1, a cup 2, a glassbottle 2, a lettuce 1, and a spatula 1.")
EMPTY_DRAWER = "You arrive at drawer 1. The drawer 1 is closed."
LAMP_ON = "You turn on the desklamp 1."
MUG_DESCRIBED = "This is a normal mug 2. In it, you see nothing."
NOTHING = "Nothing happens."


def world_of(text: str, version: int = 1):
    return text_world_state(text, version, f"obs_{version:04d}")


def sub_of(predicate_id: str):
    from embodied_agent.core.v02 import Subgoal

    return Subgoal(statement=predicate_id, kind="achieve", predicate_id=predicate_id)


def task_of(instruction: str) -> TaskInput:
    return TaskInput(task_id="unit", utterance=instruction, language="en",
                     declared_constraints=[])


def verdict(predicate_id: str, text: str, version: int = 1):
    """The instrument's answer to one row about one real snapshot, as the planner sees it."""
    return evidence_for(sub_of(predicate_id), world_of(text, version))


# ------------------------------------------------------------------ the sentence reader ----
@pytest.mark.parametrize("task_type", sorted(SIX))
def test_each_instruction_shape_authors_exactly_the_rows_it_owes(task_type):
    obligations, notes = obligations_of(SIX[task_type])
    assert [o.predicate_id for o in obligations] == EXPECTED[task_type]
    assert notes == [f"the sentence was read by the {obligations[0].rule!r} rule"]


def test_a_count_in_the_sentence_is_a_count_in_the_row_and_two_is_not_assumed():
    # `two` is the only count word this corpus uses, and a row that silently became `in1:` would
    # let an episode finish after one object
    one, _ = obligations_of("put two cd in safe")
    assert [o.predicate_id for o in one] == ["in2:cd:safe"]
    assert one[0].required_count == 2
    plain, _ = obligations_of("put some saltshaker on drawer")
    assert plain[0].required_count == 1


def test_an_unread_sentence_authors_nothing_and_quotes_itself_rather_than_guessing():
    instruction = "make a sandwich in the kitchen"
    obligations, notes = obligations_of(instruction)
    assert obligations == []
    assert len(notes) == 1 and instruction in notes[0]
    # and the arm reports it as a problem instead of running an empty plan silently
    goal = goal_from_instruction(instruction, state_version=0)
    assert problems_of(goal) == notes
    assert problems_of(goal_from_instruction(SIX["pick_heat_then_place_in_recep"],
                                             state_version=0)) == []


def test_no_row_names_a_thing_the_sentence_did_not_say():
    """The predicate *vocabulary* (`in`, `cool`, `looked_at`) is this module's own; the nouns a row
    is built from must be the sentence's. That split is what §10's "not planning for the agent"
    leaves available, so the test reads the row's fields rather than its whole id."""
    for instruction in SIX.values():
        words = set(re.findall(r"[a-z][a-z\-]*", instruction))
        for obligation in obligations_of(instruction)[0]:
            assert obligation.obj_kind in words or obligation.obj_kind == ""
            assert obligation.rec_kind in words or obligation.rec_kind == ""
            source = obligation.source
            assert obligation.obj_kind in source and obligation.rec_kind in source


def test_rows_are_about_classes_because_the_sentence_never_named_an_instance():
    for instruction in SIX.values():
        for obligation in obligations_of(instruction)[0]:
            assert not re.search(r"\b[a-z]+ \d+\b", obligation.predicate_id)
            assert not re.search(r"\d+$", obligation.predicate_id.split(":")[-1])
            # the instance choice is the decision maker's (§3.4), so a row says so where it can
            assert obligation.obj_kind == "" or obligation.obj_kind in obligation.statement


def test_every_rule_is_anchored_so_one_sentence_cannot_match_two():
    for name, pattern in RULES:
        assert pattern.pattern.startswith("^") and pattern.pattern.endswith("$"), name


# --------------------------------------------------------------- the derived graph ----
def derived(instruction: str, version: int = 0):
    goal = goal_from_instruction(instruction, state_version=version)
    return derive_text(goal, world_of(instruction, version), task_of(instruction))


def test_the_state_clause_becomes_an_edge_into_the_placement_row_and_is_mirrored_onto_it():
    derivation = derived(SIX["pick_cool_then_place_in_recep"])
    assert len(derivation.dependencies) == 1
    dep = derivation.dependencies[0]
    assert "counts only for an object that satisfies an lettuce has been cooled" in dep.note
    placement = [s for s in derivation.subgoals if s.predicate_id.startswith("in")][0]
    # `TaskPlanner.ready()` gates on `depends_on`, so an edge that lived only in `dependencies`
    # would publish a constraint the plan does not honour
    assert placement.depends_on == [dep.from_subgoal_id]
    assert placement.subgoal_id == dep.on_subgoal_id


def test_the_verb_form_and_the_adjective_form_of_the_same_task_get_the_same_edge():
    """`put a cool lettuce in countertop` and `cool some lettuce and put it in countertop` are two
    English shapes of one obligation, and the plan must not treat them as two different tasks."""
    adjective = derived(SIX["pick_cool_then_place_in_recep"])
    verb = derived("cool some lettuce and put it in countertop")
    assert ([s.predicate_id for s in verb.subgoals]
            == [s.predicate_id for s in adjective.subgoals])
    assert len(verb.dependencies) == len(adjective.dependencies) == 1


def test_the_light_edge_exists_and_does_not_claim_a_conjunction_no_sentence_states():
    derivation = derived(SIX["look_at_obj_in_light"])
    assert len(derivation.dependencies) == 1
    note = derivation.dependencies[0].note
    assert "no row claims the conjunction" in note
    # measured on this install, and the reason the note says what it now says: `use` answers only
    # where the lamp rests, so the pairing is carried by an act rather than by a row
    assert "`use` answers there and nowhere else" in note


def test_a_sentence_that_owed_nothing_derives_an_empty_plan_rather_than_a_plausible_one():
    derivation = derived("solve the puzzle")
    assert derivation.subgoals == []
    assert derivation.dependencies == []
    assert derivation.notes and "matches no rule" in derivation.notes[0]


def test_declared_constraints_stay_open_rows_that_no_instrument_claims_to_answer():
    task = TaskInput(task_id="unit", utterance=SIX["pick_and_place_simple"], language="en",
                     declared_constraints=["do not use the drawer twice"])
    goal = goal_from_instruction(SIX["pick_and_place_simple"], state_version=0)
    derivation = derive_text(goal, world_of(goal.instruction, 0), task)
    maintain = [s for s in derivation.subgoals if s.kind == "maintain"]
    assert [s.statement for s in maintain] == ["do not use the drawer twice"]
    assert evidence_for(maintain[0], world_of(EMPTY_DRAWER)) is None


# ------------------------------------------------------ per-obligation answering ----
def test_a_placement_row_is_true_only_from_a_snapshot_that_names_enough_instances():
    assert verdict("in1:potato:countertop", TWO_POTATOES)[0] is PredicateVerdict.true
    assert verdict("in2:potato:countertop", TWO_POTATOES)[0] is PredicateVerdict.true
    # one instance is not two
    assert verdict("in2:potato:countertop", LETTUCE_IN_COUNTERTOP) is None


@pytest.mark.parametrize("predicate_id,text", [
    ("in1:lettuce:countertop", EMPTY_DRAWER),        # a page about another place
    ("in1:lettuce:countertop", NOTHING),             # a refused command
    ("cool:lettuce", LETTUCE_IN_COUNTERTOP),         # names the thing, says nothing about cooling
    ("hot:potato", TWO_POTATOES),
    ("looked_at:mug", EMPTY_DRAWER),
])
def test_silence_answers_nothing_and_is_never_read_as_a_negative(predicate_id, text):
    """The three "never `false`" rows of the module docstring's table (SPEC-BST 4.2)."""
    answer = verdict(predicate_id, text)
    assert answer is None or str(answer[0].value) != "false"


def test_an_operation_row_is_answered_by_the_environments_own_ledger_sentence():
    assert verdict("cool:lettuce", COOL_LEDGER)[0] is PredicateVerdict.true
    # `hot:` is the row and `heat` the verb: the one place the two vocabularies part company
    assert verdict("hot:potato", HEAT_LEDGER)[0] is PredicateVerdict.true
    assert verdict("clean:lettuce", COOL_LEDGER) is None


def test_a_describing_sentence_answers_the_looking_row():
    assert verdict("looked_at:mug", MUG_DESCRIBED)[0] is PredicateVerdict.true
    assert verdict("looked_at:mug", COOL_LEDGER) is None


def test_the_lit_row_is_the_one_negation_this_channel_states_in_words():
    assert verdict("lit:desklamp", LAMP_ON)[0] is PredicateVerdict.true
    off = evidence_for(sub_of("lit:desklamp"), world_of("You turn off the desklamp 1."))
    assert off is not None and off[0] is PredicateVerdict.false


def test_every_answer_carries_the_ref_of_the_snapshot_that_gave_it():
    value, refs, unmeasured = verdict("cool:lettuce", COOL_LEDGER, version=7)
    assert value is PredicateVerdict.true
    assert refs == ["obs_0007"]
    assert unmeasured == []


# ---------------------------------------------------------- the record it writes ----
def test_the_understanding_record_names_the_classes_and_filters_the_instances_by_them():
    """`entities_mentioned` is every instance *of a class the sentence named* that the snapshot
    carries. The filter is the assertion: this page names eleven things and the task named two."""
    text, instances = normalized_goal(
        goal_from_instruction(SIX["pick_two_obj_and_place"], state_version=0),
        world_of(TWO_POTATOES))
    assert text == "at least 2 peppershaker are stated inside a drawer"
    assert instances == ["peppershaker 2"]


def test_an_unanswerable_page_leaves_a_row_unknown_rather_than_reopenable():
    """`done` then silence must not become `false`: the row was measured once, and no later
    sentence says the object left."""
    answered = verdict("in1:lettuce:countertop", LETTUCE_IN_COUNTERTOP)
    assert answered[0] is PredicateVerdict.true
    assert verdict("in1:lettuce:countertop", EMPTY_DRAWER) is None


def test_the_reader_has_an_identity_and_it_covers_every_function_a_round_can_consult():
    """A batch files its rows under `obligations_sha256()`, so the digest must be exactly the five
    readers' text: a reworded note that changes what a round is told moves it, and a sixth reader
    added without moving it would be an instrument no batch record names."""
    import hashlib
    import inspect

    readers = (obligations_of, derive_text, evidence_for, normalized_goal, problems_of)
    payload = "\n".join(inspect.getsource(fn) for fn in readers)
    digest = obligations_sha256()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert digest == obligations_sha256()
