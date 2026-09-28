"""The cross-episode store: an append-only JSONL file and nothing else (SPEC-v0.2 §5.4).

Three properties, each of which is a test rather than an intention:

* **Append-only.** A stored experience is never rewritten, because retrieval rows point at it by
  id and a mutated row would silently change what a *past* round was shown. A second `append` of
  the same `experience_id` is refused, not deduplicated.
* **Loadable before a batch runs.** §13's RQ3 ("does memory help on a later similar task") needs
  the store to exist independently of the episodes that will read it, so `load` on a missing path
  is an empty store — a cold start, which is a measured condition, not an error.
* **It is not a world model.** Nothing in this module can write a `WorldState`, and nothing here
  reads one either: the store holds *residues*, and §3's separation says a residue is evidence a
  model may consider, never a fact the world has. Retrieval therefore happens against a snapshot
  handed in by the caller (`retrieval.py`), and the contradiction check lives there.

The file format is one `EpisodicExperience` per line, written through the frozen record model —
so `schema_version`, `episode_id`, `source`, `provenance` and the state references §6 requires are
in the bytes, and a store produced by a different schema version is refused at load rather than
silently parsed.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Iterable, Iterator, Optional

from ..core.v02 import SCHEMA_VERSION, EpisodicExperience


class ExperienceStore:
    """An ordered, id-keyed set of experiences backed by one JSONL file."""

    def __init__(self, path: Optional[str] = None, records: Optional[Iterable[EpisodicExperience]] = None):
        self.path = path
        self._by_id: dict[str, EpisodicExperience] = {}
        self._order: list[str] = []
        for record in records or ():
            self._put(record)

    # ---------------------------------------------------------------- construction ----
    def _put(self, record: EpisodicExperience) -> None:
        if record.experience_id in self._by_id:
            raise ValueError(f"experience {record.experience_id} is already stored; the store is "
                             f"append-only because a retrieval row cites an id, and a rewritten "
                             f"row would change what an earlier round was shown")
        if not record.run_ref:
            raise ValueError("an experience with no 经验来源 cannot be stored")
        self._by_id[record.experience_id] = record
        self._order.append(record.experience_id)

    @classmethod
    def load(cls, path: str) -> "ExperienceStore":
        """Read every line. A missing file is a cold start, not a failure."""
        store = cls(path)
        if not os.path.exists(path):
            return store
        with open(path, encoding="utf-8") as fh:
            for number, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
                    raise ValueError(f"{path}:{number}: schema_version "
                                     f"{payload.get('schema_version')!r}, this build writes "
                                     f"{SCHEMA_VERSION!r} — refuse to mix two record generations")
                store._put(EpisodicExperience.model_validate(payload))
        return store

    def write(self, records: Iterable[EpisodicExperience]) -> "ExperienceStore":
        """Create the file from scratch (the only operation that rewrites, and only for a store
        built in one go — e.g. a frozen seed set that a batch is then run against)."""
        rebuilt = ExperienceStore(self.path, records)
        if self.path:
            rebuilt.flush()
        return rebuilt

    def flush(self) -> str:
        if not self.path:
            raise ValueError("an in-memory store has no file to flush to")
        existing = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        payload = json.loads(line)
                        existing[str(payload.get("experience_id"))] = line
        order = list(existing) + [i for i in self._order if i not in existing]
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for key in order:
                if key in existing:
                    fh.write(existing[key] + "\n")
                else:
                    fh.write(json.dumps(self._by_id[key].model_dump(mode="json"),
                                        ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, self.path)
        return self.path

    # ------------------------------------------------------------------- access ----
    def append(self, record: EpisodicExperience) -> EpisodicExperience:
        self._put(record)
        if self.path:
            self.flush()
        return record

    def get(self, experience_id: str) -> Optional[EpisodicExperience]:
        return self._by_id.get(experience_id)

    def ids(self) -> list[str]:
        return list(self._order)

    def all(self) -> list[EpisodicExperience]:
        return [self._by_id[i] for i in self._order]

    def __iter__(self) -> Iterator[EpisodicExperience]:
        return iter(self.all())

    def __len__(self) -> int:
        return len(self._order)

    # --------------------------------------------------------------- integrity ----
    def fingerprint(self) -> Optional[str]:
        """Hash of the stored bytes, so a manifest can name *this* store and not just its path."""
        if not self.path or not os.path.exists(self.path):
            return None
        with open(self.path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def find(self, *, task_kind: Optional[str] = None,
             outcome: Optional[str] = None) -> list[EpisodicExperience]:
        return [r for r in self.all()
                if (task_kind is None or r.task_kind == task_kind)
                and (outcome is None or r.outcome.value == outcome)]
