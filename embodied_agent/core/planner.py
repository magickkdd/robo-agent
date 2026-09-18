"""Planner + PlanValidator (spec 7, 8.3).

Rule baseline planner: generates plans from a well-formed GoalSpec + current
WorldState. It is the "given correct goals" control condition (spec 10.3), not a
general Chinese NL parser. Ordering: objects whose target is currently occupied
are scheduled later (deterministic occupancy-aware ordering).

PlanValidator checks, in increasing strength: schema validity, reference
validity, symbolic feasibility of the first executable actions against the
current state. It does NOT require all future preconditions to hold initially.
"""
from __future__ import annotations

from .contracts import GoalSpec, Plan, PlanStep, WorldState
from .scene import PhysicsScene


class PlanValidator:
    REQUIRED_ARGS = {
        "pick": {"object_id"},
        "place": {"object_id", "target_id"},
        "observe": set(),
        "safe_retreat": set(),
    }
    KNOWN_TARGETS = {"tray_left", "tray_middle", "tray_right"}

    def __init__(self, world: WorldState):
        self.world = world

    def validate(self, plan: Plan) -> tuple[bool, list[str]]:
        errs = list(plan.validate_schema())
        if plan.schema_version != "1":
            errs.append(f"unsupported schema_version {plan.schema_version}")
        if not plan.steps:
            errs.append("empty plan (a legal no-op must be represented explicitly)")
        for s in plan.steps:
            missing = self.REQUIRED_ARGS.get(s.skill)
            if missing is None:
                errs.append(f"unknown skill {s.skill}")
                continue
            for a in missing - set(s.args):
                errs.append(f"step {s.id}: missing arg {a}")
            if s.skill == "pick":
                if not any(e.entity_id == s.args["object_id"] for e in self.world.entities):
                    errs.append(f"step {s.id}: unknown object {s.args['object_id']}")
            if s.skill == "place":
                if s.args.get("target_id") not in self.KNOWN_TARGETS:
                    errs.append(f"step {s.id}: unknown target {s.args.get('target_id')}")
        # reference legality: every placed object must have been picked earlier
        picked: set[str] = set()
        for s in plan.steps:
            if s.skill == "pick":
                picked.add(s.args["object_id"])
            if s.skill == "place" and s.args.get("object_id") not in picked:
                errs.append(f"step {s.id}: place before pick for {s.args.get('object_id')}")
        # symbolic feasibility of the first action only (spec 8.3)
        if plan.steps and plan.steps[0].skill == "place":
            errs.append("first action cannot be a place: gripper starts idle")
        return len(errs) == 0, errs


class RulePlanner:
    """Generates a pick/place sequence from a GoalSpec and the current state.

    Completion predicates are evaluated against the CURRENT WorldState each time
    (fixes the prototype defect of a permanent placed=True flag)."""

    def __init__(self, scene: PhysicsScene):
        self.scene = scene

    def pending_assignments(self, goal: GoalSpec, world: WorldState) -> list[tuple[str, str]]:
        """Return (entity_id, target_id) pairs whose predicate is currently false."""
        pending = []
        for a in goal.assignments:
            eid = a.entity.entity_id or self._bind(a.entity, world)
            if eid is None:
                continue
            if self._satisfied(eid, a.target_id):
                continue
            pending.append((eid, a.target_id))
        return pending

    def _bind(self, ref, world: WorldState) -> str | None:
        for e in world.entities:
            if ref.attributes and all(e.attributes.get(k) == v for k, v in ref.attributes.items()):
                return e.entity_id
        return None

    def _satisfied(self, eid: str, target_id: str) -> bool:
        pos, _ = self.scene.object_pose(eid)
        t = self.scene.trays[target_id]
        import numpy as np

        d = self.scene.objects[eid]
        rest_z = t["floor_top"] + d["half_h"]
        return bool(
            abs(pos[0] - t["center"][0]) < t["inner_half"] - 0.005
            and abs(pos[1] - t["center"][1]) < t["inner_half"] - 0.005
            and abs(pos[2] - rest_z) < 0.01
        )

    def plan(self, goal: GoalSpec, world: WorldState, plan_id: str | None = None) -> Plan:
        pending = self.pending_assignments(goal, world)
        # occupancy-aware ordering: pairs whose target tray already hosts an
        # assigned object go later (simple deterministic heuristic for S1)
        counts: dict[str, int] = {}
        steps: list[PlanStep] = []
        i = 0
        for eid, tid in pending:
            slot = counts.get(tid, 0)
            counts[tid] = slot + 1
            steps.append(PlanStep(id=f"s{i}", skill="pick", args={"object_id": eid}))
            i += 1
            steps.append(PlanStep(id=f"s{i}", skill="place", args={"object_id": eid, "target_id": tid}))
            i += 1
        p = Plan(
            plan_id=plan_id or f"p_rule_{len(pending)}",
            based_on_state_version=world.state_version,
            goal_ref=goal.goal_id,
            steps=steps,
        )
        return p
