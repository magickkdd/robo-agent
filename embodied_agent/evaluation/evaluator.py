"""Independent evaluator (SPEC 7, 12.1.1, 12.4).

Scores one frozen `TerminalSnapshot` against an `EvalSpec` written independently
of any model output. Guarantees this file exists to provide:

* it never reads the agent's WorldState, verification reports or plan, so a model
  that mis-binds "red to the left tray" and executes perfectly still scores 0;
* it never steps the simulator: the runtime's single settle protocol produced the
  sample, and judging cannot search for a more favourable instant (SPEC 7);
* geometry alone is not success — termination, budget consumption and
  intervention are part of the final verdict;
* the shared geometry helper is deliberately re-checked by its own boundary test,
  because a shared bug would otherwise let both judges agree on a wrong answer.
"""
from __future__ import annotations

from ..core.contracts import EvalSpec, TargetRegion, TerminalSnapshot, Vec3
from ..core.verify import footprint_inside_region

ALLOWED_MODES = ("A", "B", "C")


class IndependentEvaluator:
    def __init__(self, snapshot: TerminalSnapshot, eval_spec: EvalSpec, *, allowed_modes=ALLOWED_MODES):
        self.snapshot = snapshot
        self.spec = eval_spec
        self.allowed_modes = tuple(allowed_modes)

    def _region(self, target_id: str) -> TargetRegion | None:
        t = self.snapshot.targets.get(target_id)
        if t is None:
            return None
        return TargetRegion(target_id=target_id, label=target_id,
                            center=Vec3(x=t["center"][0], y=t["center"][1], z=t["floor_top_z"]),
                            inner_half=t["inner_half"], floor_top_z=t["floor_top_z"])

    def _assignment_ok(self, entity_id: str, target_id: str) -> tuple[bool, dict]:
        ent = self.snapshot.entities.get(entity_id)
        region = self._region(target_id)
        if ent is None or region is None:
            return False, {"reason": "entity or target absent from the scored snapshot"}
        inside, e_in = footprint_inside_region(
            ent["pos"][:2], ent["footprint_half_xy"], region, self.spec.footprint_margin_m)
        # seating height from the *measured* orientation: a cylinder that came to
        # rest on its side sits one radius, not one half-height, above the floor
        rest_z = region.floor_top_z + ent["rest_extent_z"]
        height_err = abs(ent["pos"][2] - rest_z)
        supported = bool(height_err < self.spec.support_height_tol_m and target_id in ent["contacts"])
        at_rest = bool(ent["lin_speed"] < self.spec.speed_tol_mps
                       and ent["ang_speed"] < self.spec.ang_speed_tol_rps)
        free = bool(ent["in_gripper"] < 0.5)
        return bool(inside and supported and at_rest and free), {
            **e_in, "rest_extent_z": round(ent["rest_extent_z"], 4),
            "height_err_m": round(height_err, 4), "tray_contacts": float(supported),
            "lin_speed": round(ent["lin_speed"], 4), "ang_speed": round(ent["ang_speed"], 4),
            "in_gripper": ent["in_gripper"],
        }

    def score(self, episode=None) -> dict:
        """`episode` is an optional `EpisodeResult`; it contributes the protocol
        conditions (termination, budgets, intervention, allowed mode), never the
        geometry verdict."""
        details, completed = {}, 0
        for a in self.spec.assignments:
            ok, evidence = self._assignment_ok(a.entity_id, a.target_id)
            completed += int(ok)
            details[a.entity_id] = {"target": a.target_id, "satisfied": ok, "evidence": evidence}
        n = len(self.spec.assignments)
        goals_complete = bool(n) and completed == n

        protocol, reasons = True, []
        if episode is not None:
            if episode.terminal_status.value != "success":
                protocol = False
                reasons.append(f"terminal_status={episode.terminal_status.value}")
            if episode.failure_type is not None:
                reasons.append(f"failure_type={episode.failure_type.value}")
            if episode.human_intervention:
                protocol = False
                reasons.append("human intervention occurred")
            if episode.mode not in self.allowed_modes:
                protocol = False
                reasons.append(f"mode {episode.mode} is not a scored mode")
        complete = bool(goals_complete and protocol)
        return {
            "eval_id": self.spec.eval_id,
            "scored_observation_ref": self.snapshot.observation_ref,
            "scored_state_version": self.snapshot.state_version,
            "scored_sim_time": round(self.snapshot.sim_time, 3),
            "settle_seconds": self.snapshot.settle_seconds,
            "objects_total": n,
            "objects_completed": completed,
            "goals_complete_success": goals_complete,
            "protocol_ok": protocol,
            "protocol_notes": reasons,
            "complete_success": complete,
            "details": details,
        }
