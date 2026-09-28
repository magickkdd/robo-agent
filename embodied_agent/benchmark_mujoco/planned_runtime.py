"""The v0.2 planning arm installed on the MuJoCo loop (§5.2, §10).

One class with no body and one with four attributes, for the same reason the text
channel has the pair: the decision loop is imported, not copied, so an arm cannot be
bolted on by editing the loop. `PlanningMixin` is written against `core/runtime.py:
Runtime` and `MujocoRuntime` is that loop over a MuJoCo backend; mixing the two gives
this benchmark the plan, the ledger, the §9 gate and the v0.2 records without giving it
a second agent.

`task_planner_derivation` is left at the desktop default (`subgoals.derive`), and that
is the whole argument for reusing `pick`/`place` as the action vocabulary: a placement
read off a `GoalSpec` and a snapshot is the same work object here as on the PyBullet
bench. Nothing in this module authors a plan by hand.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.v02 import Ablation
from ..episodic.arm import EpisodicMixin
from ..episodic.retrieval import DEFAULT_LIMIT
from ..planning.arm import PlanningMixin
from ..planning.task_planner import TaskPlanner
from ..planning.view import V02DecisionContext
from .prompts import MujocoDecisionContext
from .runtime import MujocoRuntime

#: the capabilities this loop can actually switch off: the three `PlanningMixin` gates.
MW_ARM_MODULES = ("planning", "working_memory", "replanning")
#: the same loop with §5.4's module installed — the arm `MujocoExperiencedRuntime` runs,
#: which a `wo_episodic_memory` row must be checked against rather than refused by.
MW_EXPERIENCED_ARM_MODULES = MW_ARM_MODULES + ("episodic_memory",)


class V02MujocoDecisionContext(V02DecisionContext, MujocoDecisionContext):
    """The measured page, with the plan and the ledger on it. No body: the MRO is the
    implementation, exactly as on the text channel, so one rendering serves every arm."""


def mw_arm_coherence(ablation: Optional[Ablation],
                     installed: tuple[str, ...] = MW_ARM_MODULES) -> list[str]:
    """Why a §9 condition this loop cannot show is refused rather than run twice."""
    if ablation is None:
        return []
    reasons: list[str] = []
    for module in list(ablation.modules_off):
        if module in installed:
            continue
        reasons.append(
            f"condition {ablation.condition!r} switches off {module!r}, and this MuJoCo-channel "
            f"loop installs no instrument for it (gated here: {list(installed)}), so the "
            f"episode's records would be identical to a `full` one and the row would be a "
            f"duplicate filed under a different word")
    return reasons


class MujocoPlannedRuntime(PlanningMixin, MujocoRuntime):
    """`MujocoRuntime` with the plan offered, the ledger kept and §9's gate on it."""

    context_class = V02MujocoDecisionContext
    understanding_parser: Any = None
    task_planner_derivation: Any = None
    files_ablation_claim = True
    #: what `mw_arm_coherence` checks this class's own refusals against, read off
    # `type(self)` so a subclass that installs another module widens exactly its own gate
    # and the bare loop's answer is unchanged.
    installed_modules = MW_ARM_MODULES

    def __init__(self, *args, ablation: Optional[Ablation] = None,
                 task_planner: Optional[TaskPlanner] = None, **kw):
        clash = mw_arm_coherence(ablation, installed=type(self).installed_modules)
        if clash:
            raise ValueError("; ".join(clash))
        super().__init__(*args, task_planner=task_planner, **kw)
        self.ablation = ablation

    def _verifier(self, world):
        from .state import MujocoVerifier
        return MujocoVerifier(world, self.config)

    def _ablation_note(self) -> str:
        return (f"planning arm on the MuJoCo benchmark channel: modules off "
                f"{list(self.ablation.modules_off) or 'none'}; the snapshot is measured "
                f"simulator geometry labelled `privileged`, no vision model was consulted, "
                f"and this row is the planning contrast at {self.ablation.condition!r} on a "
                f"continuous-control backend, not §9's full system")


class MujocoExperiencedRuntime(EpisodicMixin, MujocoPlannedRuntime):
    """`MujocoPlannedRuntime` with an experience store connected to the round (§5.4).

    Bases declared once, here, for the same reason the desktop `ExperiencedPlannedRuntime`
    declares its three: which loop ran has to be answerable from a class name. The mixin
    sits first so its `_offer_memories` / `_record_feedback` / `_begin_episode` are the
    implementations the loop calls, and everything below the memory seam is the identical
    planned loop — which is what makes a `full` vs `wo_episodic_memory` difference on this
    channel attributable to the module and nothing else.

    The store is connected by the caller (`run_one_episode`, from `--experience-store`);
    the coherence gate above refuses a memory-arm claim on the bare loop, and the runner
    refuses the pair "memory arm claimed, no store connected" before a directory exists —
    the two refusals between them close the loop that would measure a cold start and
    report it as a memory.
    """

    installed_modules = MW_EXPERIENCED_ARM_MODULES

    def __init__(self, *args, experience_store: Optional[Any] = None,
                 memory_task_kind: str = "", memory_limit: int = DEFAULT_LIMIT, **kw):
        super().__init__(*args, **kw)
        self.experience_store = experience_store
        self.memory_task_kind = memory_task_kind
        self.memory_limit = int(memory_limit)

    def _ablation_note(self) -> str:
        return (f"planning arm + §5.4 episodic memory on the MuJoCo benchmark channel: "
                f"modules off {list(self.ablation.modules_off) or 'none'}; an experience "
                f"store at {getattr(self.experience_store, 'path', None)} is connected to "
                f"the round ({len(self.experience_store) if self.experience_store is not None else 0} "
                f"row(s) at episode start); the snapshot is measured simulator geometry "
                f"labelled `privileged`, no vision model was consulted")


__all__ = ["MujocoPlannedRuntime", "MujocoExperiencedRuntime", "V02MujocoDecisionContext",
           "MW_ARM_MODULES", "MW_EXPERIENCED_ARM_MODULES", "mw_arm_coherence"]
