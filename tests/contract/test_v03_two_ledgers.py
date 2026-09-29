"""The camera arm's two ledgers: what the guard is for, and what it was firing on.

`PerceptRuntime._capture_world` refuses a snapshot whose `state_version` disagrees with the
version the loop asked for, and its refusal says so plainly: "two ledgers: the tracker
stamped v12 for the snapshot the loop asked for as v13". That guard is right and should stay
strict.

It was also firing on something else. `Runtime.observe()` claimed `_obs_seq`/`state_version`
*before* asking for the measurement, so a capture that never produced one — a refused look, a
dead request, a renderer fault — left the loop's counter permanently one ahead of the camera
tracker's, and every later look tripped the guard. Measured on the E2 probe batch: a 429 killed
a look, and the next successful look raised this at v12-vs-v13. A privileged read of simulator
state cannot fail that way, which is why the guard had never fired — the camera channel was
unreachable behind its own readiness gate (SPEC-v0.3 P0').

So this file holds two things, in this order:

* **the repair** — a measurement that did not happen gets no version number, and the ref it
  would have used is returned with it;
* **the guard is not weakened** — a tracker that really has diverged is still refused, and the
  test for that is written against the production seam so it cannot be satisfied by deleting
  the check.

Both are asserted on the production classes. Nothing here renders or dials a provider.
"""
from __future__ import annotations

import pytest

from embodied_agent.evaluation.tasks import find_case


# --------------------------------------------------------------------- fixtures ----
# The `case`/`scene` fixtures in `test_v02_perception_arm.py` are file-local, and a
# fixture that is only visible in the file that defines it is a fixture other files
# cannot rely on.


@pytest.fixture(scope="module")
def case():
    return find_case("dev_c5")


@pytest.fixture()
def scene(case):
    from embodied_agent.evaluation.run import build_scene

    s = build_scene(case)
    yield s
    s.close()


def _a_runtime(case, scene, tmp_path) -> "Runtime":
    """A real `Runtime` on a real scene with a real executor, so the counter under test is
    the production one and the v0.1 path is exercised as it ships."""
    from embodied_agent.core.events import EpisodeStore
    from embodied_agent.core.runtime import Runtime
    from embodied_agent.core.skills import SkillExecutor

    return Runtime(scene, SkillExecutor(scene, world_provider=lambda: None, config=case.verify),
                   run_dir=str(tmp_path), budgets=case.budgets, episode_id="e0",
                   config=case.verify, store=EpisodeStore(str(tmp_path), "e0"))


def _an_arm_with_a_perceiver_returning(state_version: int, tmp_path):
    """A `PerceptRuntime` whose only job is to hand `_capture_world` a snapshot stamped
    `state_version`, so the guard can be asked a question with a known answer."""
    from embodied_agent.core.events import EpisodeStore
    from embodied_agent.perception.arm import PerceptRuntime

    class _Stamped:
        def __init__(self, version):
            self.state_version = version
            self.observation_ref = "look_0001_main"
            self.entities = []
            self.targets = []
            self.held_object = None
            self.source = "vlm"
            self.sim_time = 0.0
            self.wall_time = 0.0

    class _Percept:
        """What `_capture_world` reads off the last percept once the two ledgers agree: the
        provenance is how a look's spend is added to the unbilled tally, so it has to be a
        mapping even in a stub."""

        def __init__(self, view):
            self.view = view
            self.provenance = {"http_requests_this_call": 0}

    class _Perceiver:
        views = ("main",)
        default_view = "main"
        channel = "vlm"

        def __init__(self, version):
            self._version = version
            self.percepts = []

        def look(self, view, *, held=None):
            self.percepts.append(_Percept(view))
            return _Stamped(self._version)

    class _Executor:
        """`_capture_world` asks the gripper what it holds before it grounds anything, and
        this file is not about the gripper."""

        def held_state(self):
            return None

    rt = PerceptRuntime.__new__(PerceptRuntime)
    rt.perceiver = _Perceiver(state_version)
    rt.executor = _Executor()
    rt.gmap = None
    rt.ablation = None
    rt._requested_view = None
    rt._last_view = "main"
    rt._looks_unbilled = 0
    rt.episode_id = "e0"
    rt.store = EpisodeStore(str(tmp_path), "e0")
    return rt


def test_a_failed_measurement_gives_its_version_back(case, scene, tmp_path, monkeypatch):
    """The defect, written as the reading that found it: one dead capture, then a live one.

    Before the repair the second observation came back as v2 while the camera tracker, which
    had only ever measured once, said v1 — the same pair as the probe batch's v13/v12."""
    rt = _a_runtime(case, scene, tmp_path)
    real = rt._capture_world
    calls = {"n": 0}

    def _capture(state_version, observation_ref):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the measurement did not happen")
        return real(state_version, observation_ref)

    monkeypatch.setattr(rt, "_capture_world", _capture)

    with pytest.raises(RuntimeError, match="did not happen"):
        rt.observe()

    assert rt._obs_seq == 0, "a measurement that did not happen must not keep a version"
    assert rt.state_version == 0
    assert rt.observations == [], "and must not file an observation record"

    w = rt.observe()
    assert w.state_version == 1
    assert w.observation_ref == "obs_0001", \
        "the ref of a measurement nobody made is not spent, so the live one takes it"
    assert [o.observation_ref for o in rt.observations] == ["obs_0001"]


def test_versions_stay_monotonic_across_several_dead_captures(case, scene, tmp_path,
                                                               monkeypatch):
    """Three dead captures, then three live ones: the counters must not drift, or the guard
    would fire on the fourth look with nothing in the log saying which capture broke it."""
    rt = _a_runtime(case, scene, tmp_path)
    real = rt._capture_world
    state = {"fail": 3}

    def _capture(state_version, observation_ref):
        if state["fail"] > 0:
            state["fail"] -= 1
            raise RuntimeError("dead")
        return real(state_version, observation_ref)

    monkeypatch.setattr(rt, "_capture_world", _capture)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            rt.observe()
    for i in (1, 2, 3):
        assert rt.observe().state_version == i
    assert [o.state_version for o in rt.observations] == [1, 2, 3]


def test_an_ordinary_privileged_batch_is_unchanged(case, scene, tmp_path):
    """The v0.1 path this whole repair must not disturb: no failures, one step per look."""
    rt = _a_runtime(case, scene, tmp_path)
    versions = [rt.observe().state_version for _ in range(3)]
    assert versions == [1, 2, 3]
    assert [o.state_version for o in rt.observations] == [1, 2, 3]
    assert [o.observation_ref for o in rt.observations] == ["obs_0001", "obs_0002", "obs_0003"]


def test_the_guard_still_refuses_a_tracker_that_really_diverged(tmp_path):
    """The half a repair may weaken least, asked of the production seam.

    The repair is in `Runtime.observe`, one layer above, so `_capture_world` is untouched and
    a tracker that is genuinely out of step must still be refused — and the refusal must name
    both numbers, because a divergence nobody can locate is not a diagnosis."""
    from embodied_agent.perception.arm import PerceptRuntime

    behind = _an_arm_with_a_perceiver_returning(12, tmp_path)
    with pytest.raises(RuntimeError) as e:
        PerceptRuntime._capture_world(behind, 13, "obs_0013")
    assert "two ledgers" in str(e.value)
    assert "v12" in str(e.value) and "v13" in str(e.value)

    ahead = _an_arm_with_a_perceiver_returning(6, tmp_path)
    with pytest.raises(RuntimeError) as e:
        PerceptRuntime._capture_world(ahead, 5, "obs_0005")
    assert "two ledgers" in str(e.value)
    assert "v6" in str(e.value) and "v5" in str(e.value)


def test_the_guard_passes_when_the_two_ledgers_agree(tmp_path):
    """The other direction, so the test above cannot be satisfied by refusing everything."""
    from embodied_agent.perception.arm import PerceptRuntime

    rt = _an_arm_with_a_perceiver_returning(13, tmp_path)
    w = PerceptRuntime._capture_world(rt, 13, "obs_0013")
    assert w.state_version == 13
    assert len(rt.perceiver.percepts) == 1, "and the look actually happened"
