from __future__ import annotations

from dataclasses import asdict
from typing import List

from .models import EpisodeRecord


class MemoryStore:
    """Append-only episode memory with a deliberately small retrieval API."""

    def __init__(self) -> None:
        self.records: List[EpisodeRecord] = []

    def record(self, record: EpisodeRecord) -> None:
        self.records.append(record)

    def retrieve(self, task_text: str, limit: int = 3) -> List[str]:
        tokens = set(task_text.lower().split())
        scored = []
        for record in self.records:
            score = len(tokens & set(record.task.lower().split()))
            scored.append((score, record))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record.strategy for score, record in scored[:limit] if score > 0]

    def export(self) -> List[dict]:
        return [asdict(record) for record in self.records]

