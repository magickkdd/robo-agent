from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Item:
    item_id: str
    category: str
    position: Tuple[float, float]
    held: bool = False
    placed: bool = False


@dataclass
class TargetZone:
    zone_id: str
    accepts: str
    position: Tuple[float, float]
    occupied_by: Optional[str] = None


@dataclass
class Observation:
    step: int
    items: Dict[str, Item]
    zones: Dict[str, TargetZone]
    held_item: Optional[str]


@dataclass
class TaskSpec:
    text: str
    assignments: Dict[str, str]


@dataclass
class ActionResult:
    success: bool
    action: str
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanStep:
    skill: str
    args: Dict[str, str]


@dataclass
class EpisodeRecord:
    task: str
    success: bool
    actions: List[str]
    failures: List[str]
    strategy: str

