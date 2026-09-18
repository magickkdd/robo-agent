from __future__ import annotations

from typing import Dict

from .models import ActionResult
from .simulator import TabletopSimulator


class SkillLibrary:
    def __init__(self, simulator: TabletopSimulator) -> None:
        self.simulator = simulator

    def execute(self, name: str, args: Dict[str, str]) -> ActionResult:
        if name == "pick":
            return self.simulator.pick(args["item_id"])
        if name == "place":
            return self.simulator.place(args["zone_id"])
        if name == "reset_held":
            return self.simulator.reset_held()
        return ActionResult(False, name, f"unknown skill: {name}")

