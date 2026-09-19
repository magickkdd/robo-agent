"""The event log and run manifest are the experiment's evidence, so the rules that
keep them trustworthy are tested against the real store (SPEC 7, 8).

Every test here reads what actually landed on disk: an in-memory assertion would
pass on a serializer that mangles the record before it gets there.
"""
from __future__ import annotations

import json
import os
import re
import threading

import numpy as np
import pytest

from embodied_agent.core import events as E
from embodied_agent.core.events import EpisodeStore, git_state, write_manifest

FAKE_KEY = "sk-" + "0123456789abcdef" * 3          # 51 chars, shaped like a real one


@pytest.fixture
def store(tmp_path):
    t = {"sim": 0.0}
    return EpisodeStore(str(tmp_path / "ep"), "ep001.sim", sim_time_fn=lambda: t["sim"]), t


def _lines(store) -> list[str]:
    with open(store.path, encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if ln.strip()]


def _raw(store) -> str:
    return "\n".join(_lines(store))


# --------------------------------------------------------- credentials --------


def test_a_credential_shaped_string_never_reaches_the_file(store):
    s, _ = store
    s.log("provider_error", body=f"HTTP 401 Authorization: Bearer {FAKE_KEY} rejected")
    s.log("model_raw_response", text=f"the key is {FAKE_KEY} please rotate")
    written = _raw(s)
    assert FAKE_KEY not in written, "a credential was stored verbatim"
    assert "Bearer " not in written
    rec = s.read_all()[0]
    assert "***redacted***" in rec["payload"]["body"], rec["payload"]["body"]


def test_a_credential_bearing_key_is_redacted_but_a_usage_counter_is_not(store):
    """SPEC 7 keeps model-return metadata and SPEC 11.3 bills by it, so `*_tokens`
    must survive; `token`/`api_key` must not."""
    s, _ = store
    s.log("model_call", prompt_tokens=1234, completion_tokens=56,
          api_key=FAKE_KEY, access_token=FAKE_KEY, authorization="Bearer " + FAKE_KEY,
          provider_token=FAKE_KEY, password="hunter2xyz")
    p = s.read_all()[0]["payload"]
    assert p["prompt_tokens"] == 1234 and p["completion_tokens"] == 56
    for k in ("api_key", "access_token", "authorization", "provider_token", "password"):
        assert p[k] == "***redacted***", (k, p[k])
    assert FAKE_KEY not in _raw(s)


def test_a_short_value_is_not_mangled_by_the_credential_scan(store):
    """The scan is length-bounded on purpose: `sk-` appears in ordinary ids and
    redacting them would destroy the record without protecting anything."""
    s, _ = store
    s.log("note", text="sk-1")
    assert s.read_all()[0]["payload"]["text"] == "sk-1"


def test_an_artifact_is_redacted_too(store):
    s, _ = store
    path = s.artifact("raw_response", {"choices": [{"text": FAKE_KEY}], "prompt_tokens": 9})
    body = open(path, encoding="utf-8").read()
    assert FAKE_KEY not in body
    assert json.loads(body)["prompt_tokens"] == 9


# ------------------------------------------------------------- the envelope ---


def test_a_payload_key_cannot_overwrite_the_envelope(store):
    s, _ = store
    s.log("decision", sequence=99, type="forged", event_id="evt_fake",
          payload={"nested": 1}, decision_round=3)
    rec = s.read_all()[0]
    assert rec["type"] == "decision" and rec["sequence"] == 1
    assert rec["event_id"] == "evt_ep001.sim_1"
    assert "payload" not in rec["payload"]
    c = rec["payload"]["_clobbered_reserved_keys"]
    assert c["sequence"] == 99 and c["type"] == "forged" and c["event_id"] == "evt_fake"
    assert c["payload"] == {"nested": 1}, "the contested value was dropped, not kept"
    assert rec["payload"]["decision_round"] == 3


def test_the_sequence_is_contiguous_and_unique_under_concurrent_writers(store):
    """The log is append-only and `sequence` is the ordering a replay reads; the
    runtime writes from the skill thread as well as the decision loop."""
    s, _ = store
    errors = []

    def worker(n):
        try:
            for i in range(25):
                s.log("thread_event", worker=n, index=i)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    seqs = [r["sequence"] for r in s.read_all()]
    assert len(seqs) == 200 and sorted(seqs) == list(range(1, 201))
    assert len({r["event_id"] for r in s.read_all()}) == 200


def test_sim_time_comes_from_the_scene_not_the_wall_clock(store):
    s, clock = store
    s.log("before")
    clock["sim"] = 12.5
    s.log("after")
    a, b = s.read_all()
    assert a["sim_time"] == 0.0 and b["sim_time"] == 12.5
    assert b["wall_time"] >= a["wall_time"]


def test_chinese_notes_survive_the_round_trip(store):
    s, _ = store
    s.log("note", text="抓取失败：目标被墙壁挡住，需要重新选择候选位姿")
    assert _raw(s).count("\\u") == 0, "ensure_ascii was lost: the log is no longer readable"
    assert s.read_all()[0]["payload"]["text"] == "抓取失败：目标被墙壁挡住，需要重新选择候选位姿"


def test_a_numpy_measurement_is_stored_as_a_number(store):
    """Scene reads arrive as numpy scalars; `default=str` would have written "3",
    which a report then has to re-parse."""
    s, _ = store
    s.log("measurement", lift_gain_m=np.float64(0.0512), count=np.int64(3))
    p = s.read_all()[0]["payload"]
    assert p["lift_gain_m"] == pytest.approx(0.0512) and p["count"] == 3
    assert isinstance(p["count"], int) and not isinstance(p["count"], str)


# ------------------------------------------------------------ size limit -----


def test_an_oversized_response_becomes_an_artifact_rather_than_a_cut_string(store):
    s, _ = store
    big = "x" * (E.MAX_INLINE_CHARS + 1000)
    s.log("model_raw_response", text=big)
    p = s.read_all()[0]["payload"]["text"]
    assert isinstance(p, dict) and p["truncated"] is False
    assert p["chars"] == len(big)
    path = p["artifact"]
    assert os.path.exists(path) and os.path.getsize(path) == len(big)
    assert open(path, encoding="utf-8").read() == big, "the replay evidence is incomplete"


def test_a_field_at_the_limit_stays_inline(store):
    s, _ = store
    exact = "y" * E.MAX_INLINE_CHARS
    s.log("response", text=exact)
    assert s.read_all()[0]["payload"]["text"] == exact


# -------------------------------------------------------------- manifest -----


def test_the_manifest_carries_provenance_and_no_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_manifest(str(tmp_path / "run"), run_id="r1", planner="deepseek",
                          leaked_key=FAKE_KEY,
                          model={"provider": "deepseek", "api_key": FAKE_KEY,
                                 "prompt_tokens": 10})
    raw = open(path, encoding="utf-8").read()
    assert FAKE_KEY not in raw, "a credential reached the run manifest"
    m = json.loads(raw)
    assert m["model"]["api_key"] == "***redacted***"
    assert m["model"]["prompt_tokens"] == 10
    assert m["environment_variables"].startswith("not recorded")
    for field in ("python", "platform", "dependencies", "code", "schema_version",
                  "recorded_at"):
        assert field in m, field


def test_the_provenance_is_the_checkout_that_built_the_code_even_from_elsewhere(tmp_path,
                                                                                monkeypatch):
    """A run directory under /tmp says nothing about the code; resolving against the
    run dir or the cwd reported `dirty: false` for a working tree that was not clean."""
    monkeypatch.chdir(str(tmp_path))
    code = git_state()
    assert re.fullmatch(r"[0-9a-f]{40}", code["commit"] or ""), code
    assert code["dirty"] in (True, False), code
    pkg_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(E.__file__))))
    assert os.path.exists(os.path.join(code["repo_dir"], ".git")), code["repo_dir"]
    assert code["repo_dir"] == pkg_parent, "provenance resolved against the wrong checkout"


def test_an_unverifiable_tree_is_not_reported_as_clean(monkeypatch):
    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(E.subprocess, "run", boom)
    code = git_state()
    assert code["commit"] == "unknown"
    assert code["dirty"] is None, "an unreadable tree must not be recorded as clean"


def test_the_physics_dependency_version_is_the_installed_one():
    """A result that cannot say which pybullet produced it cannot be re-produced."""
    from importlib.metadata import version

    deps = E._dependency_versions()
    assert deps["pybullet"] == version("pybullet")
    assert deps["pybullet"] != "unknown"
    for name in ("pydantic", "numpy", "Pillow"):
        assert deps[name] not in ("unknown", "absent"), (name, deps[name])
