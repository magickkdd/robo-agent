"""EpisodeStore: append-only JSONL event log plus the run manifest (SPEC 7, 8).

Recording duties this module enforces:

* The envelope is reserved. A payload key may never overwrite `event_id`,
  `sequence`, `sim_time`, `type` or `payload`; an attempt is kept, values
  included, under `payload["_clobbered_reserved_keys"]` and reported, because a
  silent clobber makes the log disagree with itself (SPEC 8 events: 保护 envelope
  保留字段). Routine nesting under a named key is the caller's job instead.
* Records are stored as valid JSON in full. Truncating a raw model response to
  fit a line corrupts the replay evidence, so oversized values are moved to
  sidecar artifacts and replaced by a reference instead of being cut.
* The run manifest captures code, config, prompt, schema and scoring versions and
  model metadata, and never credentials or the environment block (SPEC 7).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time

from .contracts import SCHEMA_VERSION

RESERVED_KEYS = (
    "schema_version", "event_id", "episode_id", "sequence", "sim_time", "wall_time",
    "type", "payload", "seq", "event", "run_id",
)
MAX_INLINE_CHARS = 400_000  # a single field larger than this becomes an artifact


def _redact(value):
    """Defence in depth: nothing that looks like a credential reaches the log.

    Key names are checked as the tree is walked, and `log()` checks its own
    top-level keyword names too, so a secret cannot enter through a shallow call
    that never forms a dict."""
    if isinstance(value, dict):
        return {k: ("***redacted***" if _is_secret(k) else _redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        for token in ("sk-", "Bearer ", "api_key"):
            if token in value and len(value) > 24:
                return value[:12] + "***redacted***"
        return value
    to_native = getattr(value, "item", None)  # a numpy scalar: record the number, not "3"
    if callable(to_native) and not isinstance(value, (int, float, complex)):
        try:
            return _redact(to_native())
        except Exception:  # noqa: BLE001 - an odd .item() must not break the record
            return value
    return value


def _is_secret(key) -> bool:
    """A key is a credential unless it is a usage *counter*.

    SPEC 7 requires model-return metadata (token counts, provider counters) in
    the record and SPEC 11.3 bills by it, so a `*_tokens` counter must survive
    redaction; a credential field named `token`/`access_token` still does not.
    """
    k = str(key).lower()
    if k.endswith("_tokens"):
        return False
    return any(s in k for s in ("api_key", "apikey", "token", "secret", "password",
                                "credential", "authorization"))


class EpisodeStore:
    """One JSONL stream per episode, plus named artifacts."""

    def __init__(self, run_dir: str, episode_id: str, sim_time_fn=None):
        self.run_dir = run_dir
        self.episode_id = episode_id
        os.makedirs(run_dir, exist_ok=True)
        self.path = os.path.join(run_dir, "events.jsonl")
        self._lock = threading.Lock()
        self._seq = 0
        self._sim_time_fn = sim_time_fn or (lambda: 0.0)

    def artifact(self, name: str, content) -> str:
        """Write a standalone file next to the episode and return its path."""
        os.makedirs(self.run_dir, exist_ok=True)
        path = os.path.join(self.run_dir, f"{self.episode_id}.{name}")
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(content, str):
                f.write(content)
            else:
                json.dump(_redact(content), f, ensure_ascii=False, indent=2, default=str)
        return path

    def log(self, event_type: str, **payload):
        rec_payload = {}
        clobbered = {}
        for k, v in payload.items():
            if k in RESERVED_KEYS:
                clobbered[k] = v
                continue
            v = "***redacted***" if _is_secret(k) else _redact(v)
            if isinstance(v, str) and len(v) > MAX_INLINE_CHARS:
                rec_payload[k] = {"artifact": self.artifact(f"{event_type}_{k}.txt", v),
                                  "truncated": False, "chars": len(v)}
            else:
                rec_payload[k] = v
        if clobbered:
            # the values are kept, not just their names: dropping them would make
            # the log lose a fact precisely because the fact was contested
            rec_payload["_clobbered_reserved_keys"] = {k: clobbered[k] for k in sorted(clobbered)}

        with self._lock:
            self._seq += 1
            rec = {
                "schema_version": SCHEMA_VERSION,
                "event_id": f"evt_{self.episode_id}_{self._seq}",
                "episode_id": self.episode_id,
                "sequence": self._seq,
                "sim_time": round(float(self._sim_time_fn()), 4),
                "wall_time": time.time(),
                "type": event_type,
                "payload": rec_payload,
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


# ---------- run manifest ----------


def _repo_root() -> str:
    """The checkout the *code* came from, not where the run directory happens to be.

    Resolving provenance against `os.getcwd()` or the run dir reported
    `commit: unknown, dirty: false` for every run written outside the repository
    (SPEC 7 records the commit *and* a dirty diff, so an unverifiable tree must not
    be printed as a clean one)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def git_state(repo_dir: str | None = None) -> dict:
    """Commit, and a hash plus short stat of any uncommitted diff. A dirty tree
    is recorded, not hidden: results must state what code produced them."""

    repo_dir = repo_dir or _repo_root()

    def _run(*args):
        try:
            out = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True,
                                 text=True, timeout=20)
            return out.stdout.strip() if out.returncode == 0 else ""
        except Exception:  # noqa: BLE001 - a missing git must not break a run
            return ""

    commit = _run("rev-parse", "HEAD")
    status = _run("status", "--porcelain")
    diff = _run("diff", "--binary")
    return {
        "repo_dir": os.path.abspath(repo_dir),
        "commit": commit or "unknown",
        "dirty": bool(status) if commit else None,
        "changed_files": [ln[3:].split(" -> ")[0] for ln in status.splitlines() if ln],
        "dirty_diff_sha256": hashlib.sha256(diff.encode()).hexdigest()[:16] if diff else None,
        "dirty_diff_stat": _run("diff", "--stat"),
    }


def write_manifest(run_dir: str, **fields) -> str:
    """Run-level provenance. Explicitly excludes environment variables and keys."""
    os.makedirs(run_dir, exist_ok=True)
    import platform
    import sys

    manifest = {
        "recorded_at": time.time(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": _dependency_versions(),
        "code": git_state(),
        "schema_version": SCHEMA_VERSION,
        "environment_variables": "not recorded (SPEC 7: no credentials or full env)",
    }
    manifest.update(_redact(fields))
    path = os.path.join(run_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)
    return path


def _dependency_versions() -> dict:
    """Versions as installed, from the package metadata rather than module
    attributes: `pybullet` exposes no `__version__`, so the attribute probe
    reported 'unknown' for the one dependency that changes physics results."""
    from importlib.metadata import PackageNotFoundError, version

    deps = {}
    for name in ("pybullet", "pydantic", "numpy", "Pillow"):
        try:
            deps[name] = version(name)
        except PackageNotFoundError:
            deps[name] = "absent"
    deps["http_transport"] = "stdlib urllib (no third-party HTTP client in use)"
    return deps
