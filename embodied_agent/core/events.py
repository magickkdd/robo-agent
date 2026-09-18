"""EpisodeStore: append-only JSONL event log (spec 8, 14).

Every event carries episode_id plus whatever plan/step/observation ids apply,
so an episode can be reconstructed end to end. S1 responsibility is recording
only — no learning, no strategy change (spec 7 table).
"""
from __future__ import annotations

import json
import os
import threading


class EpisodeStore:
    def __init__(self, run_dir: str, episode_id: str):
        self.run_dir = run_dir
        self.episode_id = episode_id
        os.makedirs(run_dir, exist_ok=True)
        self.path = os.path.join(run_dir, "events.jsonl")
        self._lock = threading.Lock()
        self._seq = 0

    def log(self, event_type: str, **payload):
        """Unified envelope (spec 11): schema_version / event_id / episode_id /
        sequence / sim_time / wall_time / type / payload. Legacy top-level keys
        are kept alongside for backward-compatible readers."""
        import time as _time

        self._seq += 1
        rec = {
            "schema_version": "1",
            "event_id": f"evt_{self.episode_id}_{self._seq}",
            "episode_id": self.episode_id,
            "sequence": self._seq,
            "sim_time": getattr(self, "_sim_time_fn", lambda: 0.0)(),
            "wall_time": _time.time(),
            "type": event_type,
            "payload": dict(payload),
            # legacy flat keys for existing readers
            "seq": self._seq,
            "event": event_type,
            **payload,
        }
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
