"""The text channel's zero-spend decision source, replayed against real transcripts (§3.4, §12.1).

Every row of the `PAGES` block below is one round of a real episode: the page the armed
`AlfredPlannedRuntime` handed its decision source, and the command that source answered with. They
are the four complete winning episodes of `/tmp/p5b_matrixA`, row for row, and the first page of
each game — the opening message, the only sentence that enumerates the room — was taken from the
same game file by a `reset` and nothing else, because the episode log does not carry it.

Nothing here runs ALFWorld: each page is rebuilt with the arm's own pieces (`text_world_state`,
`TaskPlanner` over `derive_text`, `TextV02Verifier`, `plan_view`, `V02TextDecisionContext`) from
those sentences, so the source is decided against the view it was handed in the live game, and the
test runs in the desktop environment.

The reason it is written as a replay rather than as hand-built worlds: a synthetic page is a claim
about what the channel can say, and on this channel that claim is exactly what the phase had to
measure. Three defects were found by running real games and are pinned here the same way:

* **`hot:` vs `heat`** — the row names the state, the command names the verb, and looking one up in
  `APPLIANCE_OF` directly found nothing (`pick_heat`, AGENT_BLOCKED at round 1);
* **the conjunction no sentence states** — a placement row latched `true` from a snapshot taken
  *before* the operation its edge names, so the loop finished a game it had not won (`cool` ended at
  env 18 with `official_won=false`; `look_at` at env 6);
* **a `find two X` row whose only candidate was already in the destination** — the loop took
  `peppershaker 2` back out of `drawer 1` and moved it in again for 8 of its 60 steps.

A fourth finding is why this file carries two hundred lines of transcript instead of a fixture: the
first decision of a game is not replayable from the log at all. The opening message is the only page
that names the room, and a replay that starts at the log's first reply sees one place, reports
every row unactuated for want of a place to search, and answers `blocked` — a correct reading of a
page the live loop was never handed.
"""
from __future__ import annotations

import inspect
import re

import pytest

from embodied_agent.benchmark import policy as policy_module
from embodied_agent.benchmark.obligations import derive_text
from embodied_agent.benchmark.planned_runtime import V02TextDecisionContext, TextV02Verifier
from embodied_agent.benchmark.policy import PlaceMemory, TextPlanPolicy, _World
from embodied_agent.benchmark.state import goal_from_instruction, text_world_state
from embodied_agent.core.contracts import AttemptRecord, ExecutionFeedback, TaskInput, VerifyConfig
from embodied_agent.core.runtime import HISTORY_CAP
from embodied_agent.planning.task_planner import TaskPlanner
from embodied_agent.planning.view import plan_view

# --------------------------------------------------------------- the four real episodes ----
# one row per round: (the page the source was handed, the skill it answered with, its object slot,
# its target slot); the last row of a game is the winning move's reply, which the live loop never
# answered because the environment terminated the episode on that action
#: generated from `/tmp/p5b_matrixA` plus the four opening messages, and checked against the log's
#: own text and against the task sentence inside each opening before it was pasted here
PAGES = {
    'cool': ('put a cool lettuce in countertop', [
        (
            '-= Welcome to TextWorld, ALFRED! =- You are in the '
            'middle of a room. Looking quickly around you, you '
            'see a cabinet 6, a cabinet 5, a cabinet 4, a '
            'cabinet 3, a cabinet 2, a cabinet 1, a '
            'coffeemachine 1, a countertop 3, a countertop 2, a '
            'countertop 1, a drawer 3, a drawer 2, a drawer 1, '
            'a fridge 1, a garbagecan 1, a microwave 1, a shelf '
            '3, a shelf 2, a shelf 1, a sinkbasin 1, a '
            'stoveburner 4, a stoveburner 3, a stoveburner 2, a '
            'stoveburner 1, and a toaster 1. Your task is to: '
            'put a cool lettuce in countertop.', 'alfred_go', None, 'cabinet 1'),
        (
            'You arrive at cabinet 1. On the cabinet 1, you see '
            'nothing.', 'alfred_go', None, 'cabinet 2'),
        ('You arrive at cabinet 2. The cabinet 2 is closed.', 'alfred_open', None, 'cabinet 2'),
        (
            'You open the cabinet 2. The cabinet 2 is open. In '
            'it, you see a mug 1.', 'alfred_go', None, 'cabinet 3'),
        (
            'You arrive at cabinet 3. On the cabinet 3, you see '
            'a bowl 2.', 'alfred_go', None, 'cabinet 4'),
        (
            'You arrive at cabinet 4. On the cabinet 4, you see '
            'nothing.', 'alfred_go', None, 'cabinet 5'),
        ('You arrive at cabinet 5. The cabinet 5 is closed.', 'alfred_open', None, 'cabinet 5'),
        (
            'You open the cabinet 5. The cabinet 5 is open. In '
            'it, you see a glassbottle 1.', 'alfred_go', None, 'cabinet 6'),
        ('You arrive at cabinet 6. The cabinet 6 is closed.', 'alfred_open', None, 'cabinet 6'),
        (
            'You open the cabinet 6. The cabinet 6 is '
            'open. In it, you see a peppershaker 2, and a '
            'plate 3.', 'alfred_go', None, 'coffeemachine 1'),
        (
            'You arrive at coffeemachine 1. On the '
            'coffeemachine 1, you see nothing.', 'alfred_go', None, 'countertop 1'),
        ('Nothing happens.', 'alfred_go', None, 'countertop 1'),
        ('Nothing happens.', 'alfred_go', None, 'cabinet 1'),
        (
            'You arrive at cabinet 1. On the cabinet 1, you '
            'see nothing.', 'alfred_go', None, 'countertop 1'),
        (
            'You arrive at countertop 1. On the '
            'countertop 1, you see a apple 1, a '
            'cellphone 1, a cup 2, a glassbottle 2, '
            'a lettuce 1, and a spatula 1.', 'alfred_take', 'lettuce 1', 'countertop 1'),
        ('You pick up the lettuce 1 from the countertop 1.', 'alfred_go', None, 'fridge 1'),
        ('You arrive at fridge 1. The fridge 1 is closed.', 'alfred_open', None, 'fridge 1'),
        (
            'You open the fridge 1. The fridge 1 is '
            'open. In it, you see a bowl 3, a bread 1, '
            'a cup 1, a egg 1, a pan 1, a plate 2, a '
            'potato 1, and a tomato 2.', 'alfred_cool', 'lettuce 1', 'fridge 1'),
        ('You cool the lettuce 1 using the fridge 1.', 'alfred_go', None, 'countertop 1'),
        (
            'You arrive at countertop 1. On the '
            'countertop 1, you see a apple 1, a '
            'cellphone 1, a cup 2, a glassbottle 2, '
            'and a spatula 1.', 'alfred_move', 'lettuce 1', 'countertop 1'),
        ('You move the lettuce 1 to the countertop 1.', None, None, None),
        ]),
    'look': ('examine the mug with the desklamp', [
        (
            '-= Welcome to TextWorld, ALFRED! =- You are in the '
            'middle of a room. Looking quickly around you, you see '
            'a bed 1, a desk 2, a desk 1, a drawer 6, a drawer 5, a '
            'drawer 4, a drawer 3, a drawer 2, a drawer 1, a '
            'garbagecan 1, a laundryhamper 1, a safe 1, a shelf 6, '
            'a shelf 5, a shelf 4, a shelf 3, a shelf 2, and a '
            'shelf 1. Your task is to: examine the mug with the '
            'desklamp.', 'alfred_go', None, 'bed 1'),
        (
            'You arrive at bed 1. On the bed 1, you see a book 1, '
            'a pillow 3, a pillow 2, and a pillow 1.', 'alfred_go', None, 'desk 1'),
        (
            'You arrive at desk 1. On the desk 1, you see a '
            'bowl 1, a desklamp 1, a laptop 1, and a pen 1.', 'alfred_use', None, 'desklamp 1'),
        ('You turn on the desklamp 1.', 'alfred_go', None, 'desk 2'),
        (
            'You arrive at desk 2. On the desk 2, you see a '
            'alarmclock 3, a bowl 2, a keychain 2, a keychain '
            '1, a mug 3, and a mug 2.', 'alfred_take', 'mug 2', 'desk 2'),
        ('You pick up the mug 2 from the desk 2.', 'alfred_examine', None, 'mug 2'),
        ('This is a normal mug 2. In it, you see nothing.', 'alfred_go', None, 'desk 1'),
        (
            'You arrive at desk 1. On the desk 1, you see a bowl 1, a '
            'desklamp 1, a laptop 1, and a pen 1.', None, None, None),
        ]),
    'two': ('find two peppershaker and put them in drawer', [
        (
            '-= Welcome to TextWorld, ALFRED! =- You are in the '
            'middle of a room. Looking quickly around you, you '
            'see a cabinet 6, a cabinet 5, a cabinet 4, a '
            'cabinet 3, a cabinet 2, a cabinet 1, a '
            'coffeemachine 1, a countertop 3, a countertop 2, a '
            'countertop 1, a drawer 3, a drawer 2, a drawer 1, '
            'a fridge 1, a garbagecan 1, a microwave 1, a shelf '
            '3, a shelf 2, a shelf 1, a sinkbasin 1, a '
            'stoveburner 4, a stoveburner 3, a stoveburner 2, a '
            'stoveburner 1, and a toaster 1. Your task is to: '
            'find two peppershaker and put them in drawer.', 'alfred_go', None, 'cabinet 1'),
        (
            'You arrive at cabinet 1. On the '
            'cabinet 1, you see a dishsponge 1, a '
            'glassbottle 1, and a peppershaker 2.', 'alfred_take', 'peppershaker 2', 'cabinet 1'),
        ('You pick up the peppershaker 2 from the cabinet 1.', 'alfred_go', None, 'drawer 1'),
        ('You arrive at drawer 1. The drawer 1 is closed.', 'alfred_open', None, 'drawer 1'),
        (
            'You open the drawer 1. The drawer 1 '
            'is open. In it, you see a creditcard '
            '1, and a spatula 1.', 'alfred_move', 'peppershaker 2', 'drawer 1'),
        ('You move the peppershaker 2 to the drawer 1.', 'alfred_go', None, 'cabinet 2'),
        ('You arrive at cabinet 2. The cabinet 2 is closed.', 'alfred_open', None, 'cabinet 2'),
        (
            'You open the cabinet 2. The cabinet 2 is open. In '
            'it, you see nothing.', 'alfred_go', None, 'cabinet 3'),
        (
            'You arrive at cabinet 3. On the cabinet 3, you see '
            'a plate 1.', 'alfred_go', None, 'cabinet 4'),
        (
            'You arrive at cabinet 4. On the '
            'cabinet 4, you see a peppershaker 1.', 'alfred_take', 'peppershaker 1', 'cabinet 4'),
        ('You pick up the peppershaker 1 from the cabinet 4.', 'alfred_go', None, 'drawer 1'),
        (
            'You arrive at drawer 1. The drawer 1 '
            'is open. In it, you see a creditcard '
            '1, a peppershaker 2, and a spatula 1.', 'alfred_move', 'peppershaker 1', 'drawer 1'),
        ('You move the peppershaker 1 to the drawer 1.', None, None, None),
        ]),
    'heat': ('put a hot potato in garbagecan', [
        (
            '-= Welcome to TextWorld, ALFRED! =- You are in the '
            'middle of a room. Looking quickly around you, you '
            'see a cabinet 6, a cabinet 5, a cabinet 4, a '
            'cabinet 3, a cabinet 2, a cabinet 1, a '
            'coffeemachine 1, a countertop 3, a countertop 2, a '
            'countertop 1, a drawer 3, a drawer 2, a drawer 1, '
            'a fridge 1, a garbagecan 1, a microwave 1, a shelf '
            '3, a shelf 2, a shelf 1, a sinkbasin 1, a '
            'stoveburner 4, a stoveburner 3, a stoveburner 2, a '
            'stoveburner 1, and a toaster 1. Your task is to: '
            'put a hot potato in garbagecan.', 'alfred_go', None, 'cabinet 1'),
        (
            'You arrive at cabinet 1. On the cabinet 1, you see '
            'a bowl 2, a dishsponge 1, and a vase 1.', 'alfred_go', None, 'cabinet 2'),
        ('You arrive at cabinet 2. The cabinet 2 is closed.', 'alfred_open', None, 'cabinet 2'),
        (
            'You open the cabinet 2. The cabinet 2 is open. In '
            'it, you see a plate 1.', 'alfred_go', None, 'cabinet 3'),
        (
            'You arrive at cabinet 3. On the cabinet 3, you see '
            'a saltshaker 1.', 'alfred_go', None, 'cabinet 4'),
        (
            'You arrive at cabinet 4. On the cabinet 4, you see '
            'nothing.', 'alfred_go', None, 'cabinet 5'),
        ('You arrive at cabinet 5. The cabinet 5 is closed.', 'alfred_open', None, 'cabinet 5'),
        (
            'You open the cabinet 5. The cabinet 5 is open. In '
            'it, you see nothing.', 'alfred_go', None, 'cabinet 6'),
        ('You arrive at cabinet 6. The cabinet 6 is closed.', 'alfred_open', None, 'cabinet 6'),
        (
            'You open the cabinet 6. The cabinet 6 is '
            'open. In it, you see a saltshaker 2, and a '
            'vase 2.', 'alfred_go', None, 'coffeemachine 1'),
        (
            'You arrive at coffeemachine 1. On the '
            'coffeemachine 1, you see nothing.', 'alfred_go', None, 'countertop 1'),
        ('Nothing happens.', 'alfred_go', None, 'countertop 1'),
        ('Nothing happens.', 'alfred_go', None, 'cabinet 1'),
        (
            'You arrive at cabinet 1. On the cabinet 1, you '
            'see a bowl 2, a dishsponge 1, and a vase 1.', 'alfred_go', None, 'countertop 1'),
        (
            'You arrive at countertop 1. On the countertop '
            '1, you see a apple 2, a cellphone 2, a pan 1, '
            'and a soapbottle 1.', 'alfred_go', None, 'countertop 2'),
        (
            'You arrive at countertop 2. On the '
            'countertop 2, you see a bowl 1, a bread '
            '1, a butterknife 1, a cellphone 1, a '
            'glassbottle 1, a knife 1, a '
            'peppershaker 2, a potato 2, a potato 1, '
            'a spoon 2, and a statue 1.', 'alfred_take', 'potato 1', 'countertop 2'),
        ('You pick up the potato 1 from the countertop 2.', 'alfred_go', None, 'microwave 1'),
        (
            'You arrive at microwave 1. The microwave 1 is '
            'closed.', 'alfred_open', None, 'microwave 1'),
        (
            'You open the microwave 1. The microwave '
            '1 is open. In it, you see nothing.', 'alfred_heat', 'potato 1', 'microwave 1'),
        ('You heat the potato 1 using the microwave 1.', 'alfred_go', None, 'garbagecan 1'),
        (
            'You arrive at garbagecan 1. On the '
            'garbagecan 1, you see a apple 3, a egg '
            '1, and a soapbottle 2.', 'alfred_move', 'potato 1', 'garbagecan 1'),
        ('You move the potato 1 to the garbagecan 1.', None, None, None),
        ]),
}


COOL, LOOK, TWO, HEAT = (PAGES[k] for k in ("cool", "look", "two", "heat"))


# ------------------------------------------------------------------ the replay harness ----
def _rows(name: str):
    return PAGES[name][1]


def _world(name: str, at: int):
    """The snapshot of page `at`, under the loop's own numbering: page 0 is version 1."""
    return text_world_state(_rows(name)[at][0], at + 1, f"obs_{at + 1:04d}")


def _feedbacks(name: str, before: int):
    """The rows the loop would have published for the commands already issued.

    A command's `environment_text` is the *next* page: `Runtime` builds a feedback as
    `environment_text=post_world.raw_observation`, so the reply to command `j` is the page row
    `j + 1` carries. The window is the loop's own: oldest dropped past `HISTORY_CAP`.
    """
    rows = _rows(name)
    out = [ExecutionFeedback(skill=rows[j][1], entity_id=rows[j][2], target_id=rows[j][3],
                             executed=True, status="completed",
                             pre_observation_ref=f"obs_{j + 1:04d}",
                             post_observation_ref=f"obs_{j + 2:04d}", pre_state_version=j + 1,
                             post_state_version=j + 2, environment_text=rows[j + 1][0])
           for j in range(before)]
    return out[-HISTORY_CAP:]


def _attempts(name: str, before: int):
    """`Runtime._record_feedback`'s own merge: one row per distinct
    (skill, entity, target, candidate, failure) tuple, refreshed in place, capped at `HISTORY_CAP`.
    """
    rows, out = _rows(name), []
    for j in range(before):
        key = (rows[j][1], rows[j][2], rows[j][3], None, None)
        for a in out:
            if (a.skill, a.entity_id, a.target_id, a.candidate_id, a.failure_code) == key:
                a.count += 1
                a.at_state_version = j + 2
                a.last_observation_ref = f"obs_{j + 2:04d}"
                break
        else:
            out.append(AttemptRecord(skill=rows[j][1], entity_id=rows[j][2], target_id=rows[j][3],
                                     at_state_version=j + 2,
                                     last_observation_ref=f"obs_{j + 2:04d}"))
    return out[-HISTORY_CAP:]


def page(name: str, at: int):
    """The page the armed loop handed the policy before command `at` of one real episode.

    The plan on page 0 is the plan the loop authored before its first decision; the plan on every
    later page is that ledger rolled forward over the snapshots the episode actually published, in
    the order it published them, which is what makes a row that latched `true` three pages ago
    present itself as the same row rather than as a fresh reading.
    """
    instruction = PAGES[name][0]
    task = TaskInput(task_id=f"p5b.{name}", utterance=instruction, language="en",
                     declared_constraints=[])
    world = _world(name, at)
    goal = goal_from_instruction(instruction, state_version=at + 1)
    planner = TaskPlanner(derivation=derive_text)
    first = _world(name, 0)
    plan = planner.initial(task, goal, first, verifier=TextV02Verifier(first, VerifyConfig()))
    for j in range(1, at + 1):
        earlier = _world(name, j)
        plan = planner.refresh(plan, earlier, verifier=TextV02Verifier(earlier, VerifyConfig()),
                               task=task, goal=goal).plan
    feedbacks = _feedbacks(name, at)
    return V02TextDecisionContext(
        episode_id=f"p5b.{name}", round_index=at + 1, state_version=at + 1,
        observation_ref=world.observation_ref, task=task, goal=goal, world=world,
        recent_feedbacks=feedbacks, last_feedback=(feedbacks[-1] if feedbacks else None),
        attempts=_attempts(name, at),
        planning_view=plan_view(plan, ready=planner.ready(plan)))


def replay(name: str, *, through: int | None = None):
    """Run the policy over a real episode's pages, stopping at page `through` or at its `finish`.

    One `TextPlanPolicy` for the whole walk, as in the live episode: its memory is what carries the
    hand across the pages that state no hand and the position across the replies that state no
    position.
    """
    rows = _rows(name)
    policy = TextPlanPolicy()
    last = len(rows) - 1 if through is None else through
    decision = None
    for at in range(last + 1):
        decision = policy.decide(page(name, at))
        if decision.action != "execute":
            return policy, at, decision
    return policy, last, decision


def decision_at(name: str, at: int):
    """What the source says on page `at`, *after walking the pages before it* as the game did.

    A fresh instance is not the live reader. On the page after the cool, a policy with no memory of
    the take treats a countertop that named the lettuce four pages earlier as still naming it, and
    the three defects below are exactly the readings that mistake produces — they were measured
    mid-walk, with the pages behind them, so that is how they are reproduced.
    """
    policy, walked, decision = replay(name, through=at)
    assert walked == at, f"the walk stopped early at page {walked}: {decision.rationale}"
    return policy, decision


def walked_memory(name: str, through: int):
    """The page-memory an episode had built by page `through`: every page up to it, observed."""
    memory = PlaceMemory()
    for at in range(through + 1):
        memory.observe(page(name, at).model_payload())
    return memory


def issued(policy: TextPlanPolicy):
    """The commands the walk asked for, in order; a `finish`/`blocked` row is not a command."""
    return [(row["skill"], dict(row["args"] or {})) for row in policy.trace if row["skill"]]


def recorded(name: str):
    """The commands the live armed loop issued, as the source would have issued them."""
    return [(row[1], ({"object_id": row[2], "target_id": row[3]} if row[2]
                      else {"target_id": row[3]})) for row in _rows(name) if row[1]]


# ------------------------------------------------------------ what the fixture is made of ----
@pytest.mark.parametrize("name", ["cool", "look", "two", "heat"])
def test_every_game_starts_on_the_opening_that_named_its_own_task(name):
    """A pasted opening that belonged to another game would still be a valid page and a wrong room.

    The one thing the log can confirm is that the sentence the game opened with ends with the task
    the same log recorded, so that is what the fixture is checked against here.
    """
    assert _rows(name)[0][0].endswith(f"task is to: {PAGES[name][0]}.")


def test_the_replay_page_carries_every_part_of_the_live_page_the_source_can_read():
    """A replay is evidence about the live loop only if the source reads nothing the replay lacks.

    Checked by listing the payload keys the module names, not by asserting a fixed set: the live
    page also carries a `working_memory` section this harness does not rebuild, and if the source
    ever grows a reader for it the claim has to be re-earned rather than restated.
    """
    source = TextPlanPolicy()
    keys = set(page("cool", 5).model_payload(feedback_depth=source.feedback_depth))
    used = set(re.findall(r'payload(?:\.get\(|\[)"([a-z_]+)"', inspect.getsource(policy_module)))
    assert used, "no payload key is read: the expression that looks for them is stale"
    assert used - keys == set(), f"the source reads {sorted(used - keys)} off no page"


# ---------------------------------------------------------------- the three defects ----
def test_a_hot_row_is_worked_with_the_heat_command():
    """The one measured block on this channel's first page is the row that names a state and the
    command that reaches it being two different words (`hot:potato` -> `alfred_heat`)."""
    policy = TextPlanPolicy()
    for at in range(len(_rows("heat"))):
        decision = policy.decide(page("heat", at))
        if decision.action == "execute" and decision.execute.skill == "alfred_heat":
            assert decision.execute.args == {"object_id": "potato 1", "target_id": "microwave 1"}
            assert "heat" in decision.rationale and "microwave" in decision.rationale
            return
    pytest.fail(f"no `alfred_heat` was ever issued: {issued(policy)}")


def test_cooling_the_lettuce_does_not_finish_the_episode_before_the_placement():
    """The page right after `You cool the lettuce 1 using the fridge 1.`:

    both rows measure `true` there — `cool:lettuce` from the operation sentence, and
    `in1:lettuce:countertop` from the older arrival that named the lettuce on the countertop — and
    that is exactly the false finish `/tmp/p5b_cool2` ended on at env 18. The plan's own
    edge is what says the two are one claim about one thing, and the thing is not in that place.
    """
    at = next(i for i, row in enumerate(COOL[1]) if row[1] == "alfred_cool") + 1
    _policy, decision = decision_at("cool", at)
    assert decision.action == "execute"
    assert (decision.execute.skill, decision.execute.args) == ("alfred_go",
                                                               {"target_id": "countertop 1"})
    assert "countertop 1" in decision.rationale


def test_the_looking_and_the_light_are_joined_by_an_act_not_by_a_row():
    """After `examine`, the `looked_at:mug` row reads true and no sentence says the mug was in the
    light: `/tmp/p5b_look1` answered that page with `finish` at env 6 and lost."""
    at = next(i for i, row in enumerate(LOOK[1]) if row[1] == "alfred_examine") + 1
    _policy, decision = decision_at("look", at)
    assert decision.action == "execute"
    assert (decision.execute.skill, decision.execute.args) == ("alfred_go",
                                                               {"target_id": "desk 1"})
    assert "lit:" in decision.rationale


def test_a_two_object_row_does_not_unmake_the_object_it_already_placed():
    """After the first `move`, `drawer 1` names `peppershaker 2` and the row wants *two*: the source
    search must not offer the instance that is already in the destination (`/tmp/p5b_two1`)."""
    at = next(i for i, row in enumerate(TWO[1]) if row[1] == "alfred_move") + 1
    _policy, decision = decision_at("two", at)
    assert decision.action == "execute"
    assert decision.execute.skill == "alfred_go"
    assert decision.execute.args["target_id"] != "drawer 1"


@pytest.mark.parametrize("name,answer", [("cool", "finish"), ("look", "finish"),
                                         ("two", "blocked"), ("heat", "finish")])
def test_the_policy_reproduces_the_measured_winning_command_sequence(name, answer):
    """The whole point of P5-b-4: the view is *sufficient to act on*, and on these four games the
    commands the zero-spend source chose are the commands the live armed loop ran, in order, from
    the opening page to the last sentence the episode published.

    That last page is the winning move's own reply, which the live loop never asked about: the
    environment terminated these games on that move. What the source says there is therefore its own
    reading of the same sentence, and the four split the way the channel splits — three games' last
    reply states the placement the row asked for, and the two-object game's does not restate the
    destination's contents, so its count stays short of what the row wants.
    """
    policy, at, stopped = replay(name)
    assert issued(policy) == recorded(name), (
        f"the replay diverged from the ledger: {policy.trace[-1] if policy.trace else None}")
    assert (at, stopped.action) == (len(_rows(name)) - 1, answer), stopped.rationale
    if answer == "blocked":
        assert "in2:peppershaker:drawer" in stopped.rationale


# ------------------------------------------------------- what the source may and may not use ----
def test_the_source_bills_nothing_and_reads_only_the_page():
    assert (TextPlanPolicy.http_requests, TextPlanPolicy.prompt_tokens,
            TextPlanPolicy.completion_tokens) == (0, 0, 0)
    policy = TextPlanPolicy()
    ctx = page("cool", 15)
    decision = policy.decide(ctx)
    assert decision.evidence_refs == [ctx.observation_ref]
    assert decision.based_on_state_version == ctx.state_version
    assert policy.trace[-1]["round_index"] == 16
    assert not any(hasattr(policy, a) for a in ("backend", "game", "env", "admissible"))


def test_the_refused_edge_is_a_pair_not_a_place_and_the_walk_routes_around_it():
    """Commands 11-13 of the cool episode: `go to countertop 1` is refused twice from
    `coffeemachine 1` and succeeds from `cabinet 1`, so a source that re-issued it or gave up on the
    place would either burn the action budget or report the task impossible."""
    _policy, decision = decision_at("cool", 14)
    edges = [(row["args"] or {}).get("target_id") for row in _policy.trace[10:14]]
    assert edges == ["countertop 1", "countertop 1", "cabinet 1", "countertop 1"]
    assert (decision.execute.skill, decision.execute.args) == ("alfred_take",
                                                              {"object_id": "lettuce 1",
                                                               "target_id": "countertop 1"})
    assert list(_policy.memory.refused) == [("coffeemachine 1", "countertop 1")]


def test_the_memory_keeps_the_last_place_that_named_a_thing():
    """`resting` is what lets the lamp's position survive the pages that state no contents
    at all."""
    memory = PlaceMemory()
    memory.observe(page("look", 2).model_payload())
    assert memory.resting.get("desklamp 1") == "desk 1"
    memory.observe(page("look", 4).model_payload())
    assert memory.resting.get("desklamp 1") == "desk 1"        # not forgotten by a page about mug 2


def test_operated_reads_either_slot_because_use_and_take_put_their_object_in_different_ones():
    ledger = COOL[1]
    world = _World.read(page("cool", 19).model_payload(), memory=walked_memory("cool", 19))
    assert world.operated("cool", "lettuce") == "lettuce 1"
    assert world.operated("cool", "potato") is None            # the fridge was not the thing cooled
    assert world.operated("take", "lettuce") == "lettuce 1"
    assert ledger[17][1] == "alfred_cool"


def test_a_search_that_came_up_empty_reports_the_row_unactuated_instead_of_looping():
    """`_search` ends: after every place the episode was shown has had its contents stated, the row
    is named with the count and no further command is issued for it.

    Both halves are measured on the `two` game's own pages and the memory those pages build: the
    opening message named 26 places, so at page 9 a search still has places to try; a memory that
    has stated the contents of every place it knows has none left, which is what the source reports
    rather than re-issuing a `go` it has already spent.
    """
    world = _World.read(page("two", 9).model_payload(), memory=walked_memory("two", 8))
    unsearched = world.unsearched_places()
    assert "cabinet 1" not in unsearched and "cabinet 5" in unsearched
    assert all(p in _rows("two")[0][0] for p in unsearched)   # nothing beyond the shown room
    drained = PlaceMemory()
    drained.known = dict(world.known)
    drained.searched = set(world.known)
    assert _World.read(page("two", 9).model_payload(), memory=drained).unsearched_places() == []
