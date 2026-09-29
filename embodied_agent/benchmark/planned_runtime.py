"""The v0.2 planning arm installed on the ALFWorld text loop (§5.2, §12.1, §13 P5-b).

Three classes, and the reason all three are needed is one sentence: the loop imports its
decision loop, so an arm cannot be bolted on by editing a loop. `planning/arm.py:PlanningMixin`
is written against `core/runtime.py:Runtime`, and `benchmark/runtime_alfred.py:AlfredRuntime` is
that same loop with a text backend underneath it. Mixing the two gives the text game the plan,
the memory, the gate and the nine records, and gives them *without a second agent* — the
round order, the budgets, the repeat guard, the rejection rules and the artifact seal are the
code `runtime_alfred.py`'s module docstring already says is imported rather than copied.

What each class adds, and what it deliberately does not:

* `V02TextDecisionContext` — the page. `V02DecisionContext.model_payload` inserts the `plan`
  and `working_memory` sections and calls its superclass for the rest; `TextDecisionContext
  .model_payload` builds a geometry-free page from scratch and ends at the same
  `current_subgoal` anchor the insertion is keyed to. Composed in that order the two are one
  rendering, which is why this class has no body: a subclass that re-implemented either would
  be a third view of the round, and §12.1's sameness across arms dies with it.
* `TextV02Verifier` — the instrument. `planning/task_planner.py:evidence_for` asks *the
  verifier of the channel* whether a row's predicate holds, because predicate ids are the
  channel's own vocabulary (`in2:peppershaker:drawer` can never be answered by
  `verify_placement`). The text rows are answered by `benchmark/obligations.py:evidence_for`
  from the facts this snapshot stated, so the answer is delegated rather than reimplemented,
  and the memory's `discharged`/`suspended` transitions see the same instrument the plan
  revision does.
* `AlfredPlannedRuntime` — the arm. `derivation=derive_text` (which rows this task obliges)
  and `understanding_parser=obligations` (which code read the sentence) are the two channel
  facts it declares; `context_class` is the page; `_ablation_note` is this channel's own
  disclosure. Nothing here picks an action, and no table of which receptacle hides which
  object is offered to the runtime — that belief lives in the decision source
  (`benchmark/policy.py`) where §3.4 puts it.

## What is *not* changed

The v0.1 text loop stays exactly as the 268-episode batch ran it: this module subclasses it.
`benchmark/state.py:TextVerifier` still answers the instruction with one `unknown` row, and
that answer is still what `TaskPlanner` sees for the goal itself — the plan's rows are answered
by the obligation instrument, the task's satisfaction is answered by no one, because no public
text says it (SPEC-BST 4.6). A `full` episode on this channel therefore ends on the agent's own
`finish`, and `official_won` is read after the loop by the runner, never inside it.

## §9 conditions this loop can carry

`PerceptRuntime` refuses an arm whose claim contradicts its camera channel
(`perception/grounding.py:arm_coherence`). This channel has a similar contradiction and
`text_arm_coherence` names it: switching off `vlm`, `episodic_memory` or `skill_acquisition`
here changes nothing the episode produces, because no such instrument is installed, so the
"arm" would be a second copy of `full` filed under a different word. The refusal is raised in
the constructor — before a run directory exists — for the standing reason that a refusal must
not leave a half-written batch behind.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.v02 import Ablation
from ..episodic.arm import EpisodicMixin
from ..episodic.retrieval import DEFAULT_LIMIT
from ..planning.arm import PlanningMixin
from ..planning.task_planner import TaskPlanner
from ..planning.view import V02DecisionContext
from . import obligations
from .obligations import derive_text
from .prompts import TextDecisionContext
from .runtime_alfred import AlfredRuntime
from .state import TextVerifier

#: the capabilities this loop can actually switch off: the three `PlanningMixin` gates.
#: A §9 condition naming anything else claims a difference this channel cannot show.
#:
#: This is the loop **without** §5.4. `AlfredPlannedRuntime` keeps it as its class attribute
#: `installed_modules` and the gate below reads `type(self).installed_modules` rather than this
#: module-level name, for the reason H-58 gave on the MuJoCo channel: a loop that widens its
#: own gate by declaring a wider set would otherwise be checked against a constant it can no
#: longer override. So the constant says what the *bare* loop installs, and the experienced
#: subclass below declares the wider set that is actually true of it.
TEXT_ARM_MODULES = ("planning", "working_memory", "replanning")
#: the same loop with §5.4 connected, which is one more thing it can switch off
TEXT_EXPERIENCED_ARM_MODULES = TEXT_ARM_MODULES + ("episodic_memory",)


class V02TextDecisionContext(V02DecisionContext, TextDecisionContext):
    """The text page, with the plan and the ledger on it.

    No body. The MRO is the implementation: `model_payload` resolves to
    `V02DecisionContext.model_payload`, whose `super()` call resolves to
    `TextDecisionContext.model_payload`, so the base rendering is the geometry-free one and the
    two v0.2 sections are inserted before the `current_subgoal` anchor that both classes key on.
    With neither view set — the `wo_planning` + `wo_working_memory` combination, or the unarmed
    v0.1 path — `V02DecisionContext.model_payload` returns its base dict untouched, so the
    payload is byte-identical to the one `AlfredRuntime` has always sent.
    """


class TextV02Verifier(TextVerifier):
    """`TextVerifier` plus the one method the v0.2 planner asks that it not have to guess about.

    The desktop half is untouched on purpose: `progress`, `verify_goals` and `verify_action`
    keep answering about the *instruction* the way SPEC-BST 4.6 requires, so the loop's finish
    protocol is unchanged. What is added answers about a *row*, and a row is a claim about this
    snapshot's text, which is a different question and has a different instrument.
    """

    @staticmethod
    def evidence_for(sub, world):
        """`(verdict, refs, unmeasured)` for one plan row, or None when the text says nothing.

        Delegated rather than reimplemented, so the plan, the working memory and any reader of
        the record all consult the one rule set in `benchmark/obligations.py`. `None` is a real
        answer here and not a failure: "this snapshot does not mention it" is not information
        about the world (SPEC-BST 4.2), so the row keeps the reading it already had.
        """
        return obligations.evidence_for(sub, world)


def text_arm_coherence(ablation: Optional[Ablation],
                       installed: tuple[str, ...] = TEXT_ARM_MODULES) -> list[str]:
    """Why this §9 condition cannot be *shown* on this loop, as reasons rather than an exception.

    Returns [] for None and for every condition whose module the loop gates. The wording is
    about the record, not about the concept: `wo_vlm` is a coherent experiment on a camera channel
    and an unmeasurable one here, and the difference is whether the episode's own events would
    change.

    `installed` is a parameter rather than this module's `TEXT_ARM_MODULES` so a caller can ask
    the question about the loop it actually has. That is not a convenience: after E5 the text
    channel has two loops, one of which installs `episodic_memory`, and a gate that always
    answered about the narrower one would refuse a `wo_episodic_memory` row on a loop that can
    show it — which is the same defect H-58 fixed on the MuJoCo channel, where the check became
    `type(self).installed_modules` for exactly this reason.
    """
    if ablation is None:
        return []
    reasons: list[str] = []
    for module in list(ablation.modules_off):
        if module in installed:
            continue
        reasons.append(
            f"condition {ablation.condition!r} switches off {module!r}, and this text-channel "
            f"loop installs no instrument for it (gated here: {list(installed)}), so the "
            f"episode's records would be identical to a `full` one and the row would be a "
            f"duplicate filed under a different word")

    return reasons


class AlfredPlannedRuntime(PlanningMixin, AlfredRuntime):
    """`AlfredRuntime` with the plan offered, the ledger kept, and §9's planning gate on it.

    Constructed with the same arguments as `AlfredRuntime` plus `ablation` and `task_planner`:
    the text backend owns its own `TextScene`/executor pair, so a caller passes the *game*, not
    a scene.
    """

    context_class = V02TextDecisionContext
    #: the sentence reader named by the `task_understanding` record
    understanding_parser: Any = obligations
    #: which rows this channel's task obliges, instead of `subgoals.derive`'s placements
    task_planner_derivation: Any = derive_text
    #: `AlfredRuntime` files no arm claim of its own (it is the v0.1 loop), so the arm makes it
    files_ablation_claim = True
    #: what this loop can switch off, read by the gate through `type(self)` so the subclass
    #: below can widen it truthfully (see `TEXT_ARM_MODULES`)
    installed_modules = TEXT_ARM_MODULES

    def __init__(self, *args, ablation: Optional[Ablation] = None,
                 task_planner: Optional[TaskPlanner] = None, **kw):
        clash = text_arm_coherence(ablation, installed=type(self).installed_modules)
        if clash:
            raise ValueError("; ".join(clash))
        super().__init__(*args, task_planner=task_planner, **kw)
        self.ablation = ablation

    # ---------- the channel's answers ----------
    def _verifier(self, world):
        return TextV02Verifier(world, self.config)

    def _understanding_problems(self, task, goal, world, progress) -> list[str]:
        """An unread instruction is a problem the *reader* can name and `understand` cannot find.

        The desktop record derives its problems from `GoalSpec` ambiguity and failed placement
        clauses; a text sentence that matched no rule produced no rows at all, so nothing would
        appear wrong unless the module that tried to read it said so here.
        """
        return obligations.problems_of(goal)

    def _ablation_note(self) -> str:
        """This channel's disclosure, which is not the desktop one.

        "A privileged world" would be false here: a text game's snapshot *is* the public
        response, and there is no vision model to abstain from. What has to be said instead is
        that the decision source spent no request either, because §9's `full` means a system
        whose perception and decision both come from a model.
        """
        return (f"planning arm on the ALFWorld text channel: modules off "
                f"{list(self.ablation.modules_off) or 'none'}; the snapshot is the public "
                f"response, so no vision model was consulted, and this row is the "
                f"planning contrast at {self.ablation.condition!r} on a text backend, "
                f"not §9's full system")


class AlfredExperiencedRuntime(EpisodicMixin, AlfredPlannedRuntime):
    """`AlfredPlannedRuntime` with an experience store connected to the round (§5.4).

    The same shape as `MujocoExperiencedRuntime`, and the same reason for it: bases declared
    once here so which loop ran is answerable from a class name, and the mixin first so its
    `_offer_memories` / `_record_feedback` / `_begin_episode` are the implementations the loop
    calls. Everything below the memory seam is the identical planned loop, which is what makes
    a `full` vs `wo_episodic_memory` difference on this channel attributable to the module and
    nothing else — the reason SPEC-v0.3 §6 E5 calls this "the same family as H-58".

    What the desktop and MuJoCo channels each needed a vocabulary decision that this one does
    not: §5.4's query is built from `world.targets` (regions), `world.entities` and the goal
    assignments (kinds), plus a `task_kind` term the caller names because the loop cannot invent
    a set name from an utterance. On ALFWorld the snapshot *is* the public response, so its
    regions and entities are exactly the ones the verifier already reports — the same fields
    `TextV02Verifier` reads. The `task_kind` is the text backend's own task family (the
    `task_type` in the frozen slot list), so two games of the same family share a memory and two
    families do not.

    The store is connected by the caller (`run_slot`, from `--experience-store`); the gate
    refuses a memory-arm claim on the bare loop, and the runner refuses "memory arm claimed, no
    store connected" before a directory exists — the two refusals between them close the loop
    that would measure a cold start and report it as a memory.
    """

    installed_modules = TEXT_EXPERIENCED_ARM_MODULES

    def __init__(self, *args, experience_store: Optional[Any] = None,
                 memory_task_kind: str = "", memory_limit: int = DEFAULT_LIMIT, **kw):
        super().__init__(*args, **kw)
        self.experience_store = experience_store
        self.memory_task_kind = memory_task_kind
        self.memory_limit = int(memory_limit)

    def _ablation_note(self) -> str:
        return (f"planning arm + §5.4 episodic memory on the ALFWorld text channel: modules off "
                f"{list(self.ablation.modules_off) or 'none'}; an experience store at "
                f"{getattr(self.experience_store, 'path', None)} is connected to the round "
                f"({len(self.experience_store) if self.experience_store is not None else 0} "
                f"row(s) at episode start, task kind {self.memory_task_kind!r}); the snapshot is "
                f"the public response, so no vision model was consulted, and this row is the "
                f"planning and memory contrast at {self.ablation.condition!r} on a text backend, "
                f"not §9's full system")


__all__ = ["AlfredPlannedRuntime", "AlfredExperiencedRuntime", "TEXT_ARM_MODULES",
           "TEXT_EXPERIENCED_ARM_MODULES", "TextV02Verifier",
           "V02TextDecisionContext", "text_arm_coherence"]
