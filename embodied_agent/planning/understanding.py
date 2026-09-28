"""Task understanding as a record, before anything is planned (SPEC-v0.2 §4, §5.2's 任务理解).

The v0.1 loop parses an utterance straight into a `GoalSpec` and throws the parse away: the
`decision_context` event shows what the model was told, and `episode_start` shows the budgets,
but nothing on disk says **which parts of the sentence the system could not read** until a
clarification request happens to end the episode. §11 asks whether the agent asked about the
*right* thing, which needs the ambiguity on record before any model answered — hence this
module, and hence the `task_understanding` event v0.2 declares and P0 left unproduced.

One rule: **nothing here adds information**. Every field is read off a `TaskInput`, a `GoalSpec`
and a `WorldState` that other modules already produced; `implicit_requirements` names the
geometric facts the *plan* later turns into dependencies, and `ambiguous_parts` is the
interpreter's own problem list. A field with no source is left empty rather than summarised.
"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Optional, Sequence

from ..core.contracts import GoalSpec, TaskInput, WorldState
from ..core.v02 import TaskUnderstanding
from .subgoals import Instruments, occupants, placement_predicate


def interpreter_sha256(module=None) -> str:
    """Hash of the rule interpreter this record attributes the parse to.

    Not a model hash: `core/interpreter.py` is a template parser, and recording which text of it
    produced a parse is what keeps "the rule parser could not read this clause" distinguishable
    from "the VLM refused to" when the two are compared (§12.3).

    `module` names the interpreter that actually ran. A second channel reads its task text with its
    own rules (`benchmark/obligations.py` for a text game), and filing the desktop parser's hash
    beside that parse would be a record that misattributes its own evidence — so the caller says
    which code produced the reading, and the default is the one every desktop arm uses.
    """
    from ..core import interpreter

    target = module if module is not None else interpreter
    return hashlib.sha256(inspect.getsource(target).encode("utf-8")).hexdigest()


def _repo_path(module) -> str:
    """`core/interpreter.py`-style path of a module, relative to the package root.

    The label is a file path rather than a dotted name because that is the shape every v0.2
    provenance field in this repo already uses (`planning/understanding.py`, `task_planner.py:
    provenance.author`), so a reader comparing two records reads two of the same thing.
    """
    import embodied_agent

    try:
        path = inspect.getfile(module)
    except TypeError:
        return str(getattr(module, "__name__", "unknown"))
    root = str(Path(embodied_agent.__file__).parent) + "/"
    return path.split(root)[-1] if path.startswith(root) else path


def interpreter_label(module=None) -> str:
    """The `rule:<path>.py` string `TaskUnderstanding.model` carries, for whichever parser ran."""
    from ..core import interpreter

    return f"rule:{_repo_path(module if module is not None else interpreter)}"


def understand(task: TaskInput, goal: GoalSpec, world: WorldState, *, progress=None,
               planner=None, extra_problems: Sequence[str] = (),
               parser=None) -> TaskUnderstanding:
    """File what the utterance said, what bound, and what did not.

    `entities_mentioned` is the set that *bound to an entity id in this snapshot*, not every noun
    in the sentence: a mention that resolved to nothing is recorded where it can be acted on —
    an `implicit_requirements` line naming the failed clause, or `ambiguous_parts` — because a
    list of words the parser saw is not information, and the §11 row this record feeds asks
    whether the *unresolved* things were asked about.

    `extra_problems` is how a runtime-side rejection (an envelope failure, a stale reference)
    joins the list without the record pretending the *parser* found it: each string arrives
    already named by its producer.

    `parser` is the module whose rules produced this reading. It changes three things and nothing
    else: the two attribution fields `model` and `prompt_sha256`, and — when the parser exposes
    `normalized_goal(goal, world, progress=...)` — the rendering of what the sentence asked for.
    That hook exists because a text game's instruction never binds a clause to an entity *id* the
    way a desktop `GoalSpec` does (its `WorldState` carries one sentinel progress row for the whole
    instruction, and its entity ids are instance names like `mug 2` rather than assignment targets);
    rendering that row as `place task in benchmark_instruction` would be this record inventing a
    binding no parser read, so the channel that read the sentence says what it read. Nothing here
    adds a fact the parser did not produce.
    """
    bound = {(p.entity_id, p.target_id) for p in (progress or []) if p.entity_id}
    unresolved = [p for p in (progress or [])
                  if "?" in str(p.predicate_id or "") or not p.entity_id]
    normalize = getattr(parser, "normalized_goal", None)
    if normalize is None:
        normalized = "; ".join(f"place {e} in {t}" for e, t in sorted(bound)) or "no binding"
        mentioned = sorted({e for e, _t in bound})
    else:
        normalized, mentioned = normalize(goal, world, progress=progress)
    implicit: list[str] = []
    instruments = Instruments(planner, world)
    for item in progress or []:
        if not item.entity_id or not world.has_entity(item.entity_id):
            continue
        if str(getattr(item.value, "value", "unknown")) in ("false", "unknown") and \
                instruments.have_placement:
            feas = instruments.feasible(item.entity_id, item.target_id)
            if not feas.feasible:
                inside = [o for o in occupants(world, item.target_id) if o != item.entity_id]
                if inside:
                    implicit.append(
                        f"{item.entity_id} -> {item.target_id} is blocked by "
                        f"{', '.join(inside)} in this snapshot: {feas.first_reason}")
                else:
                    implicit.append(f"{item.target_id} currently has no feasible slot for "
                                    f"{item.entity_id}: {feas.first_reason}")
        if str(getattr(item.value, "value", "unknown")) == "unknown":
            implicit.append(f"{placement_predicate(item.entity_id, item.target_id)} is "
                            f"unmeasured in {world.observation_ref or 'this snapshot'}: "
                            f"{', '.join(item.unmeasured) or 'no reason recorded'}")
    for item in unresolved:
        implicit.append(f"the clause for {item.predicate_id or item.target_id} never resolved "
                        f"to an entity this snapshot can see")

    return TaskUnderstanding(
        task_ref=task.task_id, utterance=task.utterance,
        normalized_goal=normalized,
        entities_mentioned=sorted(set(mentioned)),
        constraints=list(task.declared_constraints) + list(goal.prohibitions),
        implicit_requirements=sorted(set(implicit)),
        ambiguous_parts=([goal.clarification_request] if goal.clarification_request else []) +
                        list(extra_problems),
        success_described_by_agent=[],
        model=interpreter_label(parser) if not goal.ambiguous else
              f"{interpreter_label(parser)} (ambiguous)",
        prompt_sha256=interpreter_sha256(parser),
        based_on_state_version=world.state_version,
        based_on_observation_ref=world.observation_ref,
        provenance={"author": "planning/understanding.py",
                    "goal_id": goal.goal_id,
                    "ambiguous": bool(goal.ambiguous),
                    "n_assignments": len(goal.assignments)})


__all__ = ["TaskUnderstanding", "understand", "interpreter_sha256", "interpreter_label"]
