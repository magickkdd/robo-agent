from __future__ import annotations

from copy import deepcopy
from typing import Dict, Iterable, Optional, Set

from .models import ActionResult, Item, Observation, TargetZone


class TabletopSimulator:
    """Dependency-free simulator/test double for a fixed tabletop arm.

    Positions are 2-D and the arm is assumed to have a reliable low-level
    controller. The public methods are intentionally shaped like a future
    PyBullet/MuJoCo adapter.
    """

    def __init__(
        self,
        items: Iterable[Item],
        zones: Iterable[TargetZone],
        fail_once_on: Optional[Set[str]] = None,
    ) -> None:
        self.items: Dict[str, Item] = {item.item_id: deepcopy(item) for item in items}
        self.zones: Dict[str, TargetZone] = {zone.zone_id: deepcopy(zone) for zone in zones}
        self.fail_once_on = set(fail_once_on or set())
        self.step = 0
        self.held_item: Optional[str] = None

    def observe(self) -> Observation:
        return Observation(
            step=self.step,
            items=deepcopy(self.items),
            zones=deepcopy(self.zones),
            held_item=self.held_item,
        )

    def _consume_failure(self, key: str) -> bool:
        if key in self.fail_once_on:
            self.fail_once_on.remove(key)
            return True
        return False

    def pick(self, item_id: str) -> ActionResult:
        self.step += 1
        item = self.items.get(item_id)
        if item is None:
            return ActionResult(False, "pick", f"unknown item: {item_id}")
        if self.held_item is not None:
            return ActionResult(False, "pick", "gripper is already holding an item")
        if item.placed:
            return ActionResult(False, "pick", f"item already placed: {item_id}")
        if self._consume_failure(f"pick:{item_id}"):
            return ActionResult(False, "pick", "injected grasp failure")
        item.held = True
        self.held_item = item_id
        return ActionResult(True, "pick", f"picked {item_id}", {"item_id": item_id})

    def place(self, zone_id: str) -> ActionResult:
        self.step += 1
        if self.held_item is None:
            return ActionResult(False, "place", "gripper is empty")
        zone = self.zones.get(zone_id)
        if zone is None:
            return ActionResult(False, "place", f"unknown zone: {zone_id}")
        item = self.items[self.held_item]
        if self._consume_failure(f"place:{item.item_id}"):
            return ActionResult(False, "place", "injected placement failure")
        if item.category != zone.accepts:
            return ActionResult(False, "place", f"{zone_id} accepts {zone.accepts}, got {item.category}")
        if zone.occupied_by is not None:
            return ActionResult(False, "place", f"zone is occupied: {zone_id}")
        item.position = zone.position
        item.held = False
        item.placed = True
        zone.occupied_by = item.item_id
        self.held_item = None
        return ActionResult(True, "place", f"placed {item.item_id} in {zone_id}", {"zone_id": zone_id})

    def reset_held(self) -> ActionResult:
        """Release a held item so a failed place can be retried safely."""
        self.step += 1
        if self.held_item is None:
            return ActionResult(True, "reset_held", "gripper already empty")
        item = self.items[self.held_item]
        item.held = False
        self.held_item = None
        return ActionResult(True, "reset_held", f"released {item.item_id}")

