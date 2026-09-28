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
import re
import subprocess
import threading
import time

from .contracts import SCHEMA_VERSION

RESERVED_KEYS = (
    "schema_version", "event_id", "episode_id", "sequence", "sim_time", "wall_time",
    "type", "payload", "seq", "event", "run_id",
)
MAX_INLINE_CHARS = 400_000  # a single field larger than this becomes an artifact

# What counts as a credential *inside* a value. A bare `sk-` substring was the old test and
# it ate real records: `pick_and_place_simple-Mug-None-Desk-308/trial_…` contains `sk-`
# inside `Desk-`, so six task ids and one model rationale in the 268-episode batch were
# truncated to twelve characters. Requiring the `sk-` to start a token (not follow a letter
# or digit) keeps every key shape the old rule caught — a real key stands at the start of a
# value, after `=` or after a space — and lets identifiers through. `Bearer ` and `api_key`
# are unchanged.
_CREDENTIAL_IN_TEXT = re.compile(r"(?<![0-9A-Za-z])sk-[^\s\"']{2,}|Bearer |api_key")


def _redact(value):
    """Defence in depth: nothing that looks like a credential reaches the log.

    Key names are checked as the tree is walked, and `log()` checks its own
    top-level keyword names too, so a secret cannot enter through a shallow call
    that never forms a dict."""
    if isinstance(value, dict):
        return {k: ("***redacted***" if _is_secret(k, v) else _redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        if len(value) > 24 and _CREDENTIAL_IN_TEXT.search(value):
            return value[:12] + "***redacted***"
        return value
    to_native = getattr(value, "item", None)  # a numpy scalar: record the number, not "3"
    if callable(to_native) and not isinstance(value, (int, float, complex)):
        try:
            return _redact(to_native())
        except Exception:  # noqa: BLE001 - an odd .item() must not break the record
            return value
    return value


def _is_secret(key, value=None) -> bool:
    """A key is a credential unless it is a usage *counter*.

    SPEC 7 requires model-return metadata (token counts, provider counters) in
    the record and SPEC 11.3 bills by it, so a `*_tokens` counter must survive
    redaction; a credential field named `token`/`access_token` still does not.

    A whole usage mapping is filed under the bare names `tokens` and `usage` by
    `benchmark_mujoco/perceive.py:1487,1511`, and `token` is one of the
    substrings below, so the name alone destroyed it — batch 7's five perception
    records reached the log as `"tokens": "***redacted***"`, which is
    unrecoverable in an archived run and leaves the episode total as the only
    surviving record of a reading's cost. The value's shape settles the
    distinction: a counter is a mapping or a number, a credential is a string,
    and a string in either seat still falls through to the rules below.
    """
    k = str(key).lower()
    if k.endswith("_tokens"):
        return False
    if k in ("tokens", "usage") and not isinstance(value, str):
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

    def log(self, event_type: str, contract_version: str | None = None, **payload):
        """Append one event.

        `contract_version` names which *contract generation* is being written. v0.2 adds a
        second one (`core/v02.py`, schema "4") that shares this store with the v0.1 records,
        and a reader that filters on `type` alone would otherwise pool the two and report
        them as one statistic (SPEC-v0.2 §6, §12.1). Left out, the envelope follows the
        version the record itself declares, which for every v0.1 record is the schema-3
        constant it always was. An explicit version that contradicts the record is refused
        rather than quietly relabelled.
        """
        rec_payload = {}
        clobbered = {}
        for k, v in payload.items():
            if k in RESERVED_KEYS:
                clobbered[k] = v
                continue
            v = "***redacted***" if _is_secret(k, v) else _redact(v)
            if isinstance(v, str) and len(v) > MAX_INLINE_CHARS:
                rec_payload[k] = {"artifact": self.artifact(f"{event_type}_{k}.txt", v),
                                  "truncated": False, "chars": len(v)}
            else:
                rec_payload[k] = v
        if clobbered:
            # the values are kept, not just their names: dropping them would make
            # the log lose a fact precisely because the fact was contested
            rec_payload["_clobbered_reserved_keys"] = {k: clobbered[k] for k in sorted(clobbered)}
        declared = clobbered.get("schema_version")
        if declared is not None and contract_version is not None \
                and str(declared) != str(contract_version):
            raise ValueError(
                f"{event_type}: the record declares schema {declared!r} but the caller "
                f"asked to file it as {contract_version!r}. Write it with log_record() or "
                f"pass the version the record itself carries.")
        record_episode = clobbered.get("episode_id")
        if record_episode is not None and str(record_episode) != str(self.episode_id):
            raise ValueError(
                f"{event_type}: the record belongs to episode {record_episode!r} but this "
                f"store writes {self.episode_id!r}; a record in the wrong episode file "
                f"would be read back as if it belonged there (SPEC-v0.2 §6).")
        version = str(contract_version or declared or SCHEMA_VERSION)

        with self._lock:
            self._seq += 1
            rec = {
                "schema_version": version,
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

    def log_record(self, event_type: str, record, **payload):
        """Append an event whose envelope version is read off the record being written.

        This exists so a schema-4 record cannot be filed as schema-3 by an omitted
        argument."""
        version = str(getattr(record, "schema_version", SCHEMA_VERSION))
        dump = record.model_dump(mode="json") if hasattr(record, "model_dump") else dict(record)
        return self.log(event_type, contract_version=version, **payload, **dump)

    def read_all(self, schema_version: str | None = None) -> list[dict]:
        """Every event, or only those of one contract generation."""
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        if schema_version is None:
            return events
        return [e for e in events if str(e.get("schema_version")) == str(schema_version)]


# ---------- run manifest ----------


def _repo_root() -> str:
    """The checkout the *code* came from, not where the run directory happens to be.

    Resolving provenance against `os.getcwd()` or the run dir reported
    `commit: unknown, dirty: false` for every run written outside the repository
    (SPEC 7 records the commit *and* a dirty diff, so an unverifiable tree must not
    be printed as a clean one)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _git(repo_dir: str, *args) -> str:
    """Git, or the empty string. A missing git must not break a run, and a tree whose
    provenance cannot be read is reported as unreadable rather than as clean."""
    try:
        out = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True,
                             text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - a missing git must not break a run
        return ""


# What counts as "the code that ran". A declaration, filed beside the digest it produces:
# importable source under these prefixes. Data, artifacts and scratch files are out of it
# by construction, so an identity moves when the package changes and not when a run writes.
UNTRACKED_CODE_PREFIXES = ("embodied_agent/",)


def untracked_code_state(repo_dir: str | None = None, *,
                         prefixes: tuple[str, ...] = UNTRACKED_CODE_PREFIXES) -> dict | None:
    """A content identity for code git cannot see.

    `dirty_diff_sha256` is a hash of `git diff --binary`, and a diff is a statement about
    *tracked* files. While a whole package is untracked, every edit to it leaves that field
    byte-identical, so two runs that executed different code record the same provenance --
    phase-log H-15 读数三 measured exactly that across the Fix B edit, where batch5b L4 and
    batch6 L4 carried one identity for two trees.

    The digest is over `"<relpath> <git blob id>"` lines in sorted order, so it pins content
    and placement, and the file list is recoverable by re-running the same two commands.
    `None` means git could not be read, which is a different claim from an empty list.
    """
    repo_dir = repo_dir or _repo_root()
    if not _git(repo_dir, "rev-parse", "HEAD"):
        return None
    others = _git(repo_dir, "ls-files", "--others", "--exclude-standard", "--full-name")
    paths = sorted(p for p in others.splitlines()
                   if p.endswith(".py") and p.startswith(prefixes))
    if not paths:
        return {"prefixes": list(prefixes), "files": 0, "sha256": None}
    blobs = _git(repo_dir, "hash-object", "--", *paths).splitlines()
    if len(blobs) != len(paths):
        return {"prefixes": list(prefixes), "files": len(paths), "sha256": None,
                "unreadable": "git hash-object returned no blob id for every path"}
    pairs = "\n".join(f"{p} {b}" for p, b in zip(paths, blobs))
    return {"prefixes": list(prefixes), "files": len(paths),
            "sha256": hashlib.sha256(pairs.encode()).hexdigest()[:16]}


def git_state(repo_dir: str | None = None) -> dict:
    """Commit, and a hash plus short stat of any uncommitted diff. A dirty tree
    is recorded, not hidden: results must state what code produced them.

    The tracked diff alone is not enough on a checkout whose code is untracked, so
    `untracked_code` carries a content identity for it -- see `untracked_code_state`."""

    repo_dir = repo_dir or _repo_root()

    def _run(*args):
        return _git(repo_dir, *args)

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
        "untracked_code": untracked_code_state(repo_dir),
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
