from __future__ import annotations

from typing import Dict

from .models import Observation, TaskSpec


class Verifier:
    def check(self, task: TaskSpec, observation: Observation) -> tuple[bool, Dict[str, bool]]:
        evidence = {}
        for item_id, zone_id in task.assignments.items():
            item = observation.items[item_id]
            zone = observation.zones[zone_id]
            evidence[item_id] = item.placed and zone.occupied_by == item_id and item.category == zone.accepts
        return all(evidence.values()), evidence

