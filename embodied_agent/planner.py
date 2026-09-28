from __future__ import annotations

import re
from typing import Dict, Iterable, List

from .models import Observation, PlanStep, TaskSpec


class SimpleTaskParser:
    """Small parser used only to make the demo accept natural language."""

    PATTERN = re.compile(r"(?P<item>[a-zA-Z0-9_-]+)\s*(?:to|into|in)\s*(?P<zone>[a-zA-Z0-9_-]+)")

    def parse(self, text: str, observation: Observation) -> TaskSpec:
        assignments: Dict[str, str] = {}
        lowered = text.lower()
        for item_id in observation.items:
            for zone_id in observation.zones:
                if item_id.lower() in lowered and zone_id.lower() in lowered:
                    assignments[item_id] = zone_id
        for match in self.PATTERN.finditer(text):
            if match.group("item") in observation.items and match.group("zone") in observation.zones:
                assignments[match.group("item")] = match.group("zone")
        if not assignments:
            raise ValueError("task did not name any known item and target zone")
        return TaskSpec(text=text, assignments=assignments)


class RuleBasedPlanner:
    def plan(self, task: TaskSpec, observation: Observation, memory: Iterable[str] = ()) -> List[PlanStep]:
        # Put unplaced items first; previously successful order hints can be
        # supplied by memory but never override explicit task assignments.
        steps: List[PlanStep] = []
        for item_id, zone_id in task.assignments.items():
            if observation.items[item_id].placed:
                continue
            steps.extend([
                PlanStep("pick", {"item_id": item_id}),
                PlanStep("place", {"zone_id": zone_id}),
            ])
        steps.append(PlanStep("verify", {}))
        return steps

