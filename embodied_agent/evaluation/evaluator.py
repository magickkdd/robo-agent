"""Independent Evaluator (spec 8.2, 11.1, 12.4).

Scores an episode against the EvalSpec authored independently of the agent.
It reads only the physics truth + EvalSpec; it never reads the agent's own
VerificationReport, WorldState flags or plan. A model that mis-binds "red to
the left tray" and then executes its wrong plan perfectly still scores 0.
"""
from __future__ import annotations

from ..core.contracts import EvalSpec
from ..core.scene import PhysicsScene
from ..core.verify import object_at_rest, object_in_region, object_not_held, object_supported


class IndependentEvaluator:
    def __init__(self, scene: PhysicsScene, eval_spec: EvalSpec):
        self.scene = scene
        self.spec = eval_spec

    def score(self) -> dict:
        self.scene.settle(self.spec.settle_time_s)
        details = {}
        completed = 0
        for a in self.spec.assignments:
            region = next(
                {
                    "center": self.scene.trays[t]["center"],
                    "inner_half": self.scene.trays[t]["inner_half"],
                    "floor_top_z": self.scene.trays[t]["floor_top"],
                    "target_id": t,
                }
                for t in self.scene.trays
                if t == a.target_id
            )
            from ..core.contracts import TargetRegion, Vec3

            region = TargetRegion(
                target_id=a.target_id,
                label=a.target_id,
                center=Vec3(x=region["center"][0], y=region["center"][1], z=region["floor_top_z"]),
                inner_half=region["inner_half"],
                floor_top_z=region["floor_top_z"],
            )
            inside, e_in = object_in_region(self.scene, a.entity_id, region, self.spec.footprint_margin_m)
            supported, e_sup = object_supported(self.scene, a.entity_id, region, self.spec.support_height_tol_m)
            rest, e_rest = object_at_rest(self.scene, a.entity_id, self.spec.speed_tol_mps, self.spec.ang_speed_tol_rps)
            free, e_free = object_not_held(self.scene, a.entity_id)
            ok = inside and supported and rest and free
            completed += int(ok)
            details[a.entity_id] = {
                "target": a.target_id,
                "satisfied": ok,
                "inside": inside, "supported": supported, "at_rest": rest, "not_held": free,
                "evidence": {**e_in, **e_sup, **e_rest, **e_free},
            }
        complete = completed == len(self.spec.assignments) and len(self.spec.assignments) > 0
        return {
            "eval_id": self.spec.eval_id,
            "objects_total": len(self.spec.assignments),
            "objects_completed": completed,
            "complete_success": complete,
            "details": details,
        }
