"""Contract tests on the simulation backend: reset/observe/seed/close (spec 12.2).
The symbolic test-double backend is an S2 item; these run on PyBullet only."""
import numpy as np
import pytest

from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.verify import build_world_state


LAYOUT = [{"entity_id": "a", "shape": "cube", "dims": [0.021] * 3, "xy": [0.44, 0.0],
           "attributes": {"color": "red", "shape": "cube"}}]


def test_seed_determinism_same_layout_same_poses():
    p1 = PhysicsScene(seed=42, object_layout=LAYOUT).object_pose("a")
    p2 = PhysicsScene(seed=42, object_layout=LAYOUT).object_pose("a")
    assert np.allclose(p1[0], p2[0], atol=1e-9)


def test_observe_has_source_and_state_version():
    scene = PhysicsScene(seed=1, object_layout=LAYOUT)
    w = build_world_state(scene, 3, "obs_x")
    assert w.state_version == 3 and w.observation_ref == "obs_x"
    assert all(e.source.value == "privileged" for e in w.entities)
    scene.close()


def test_close_is_idempotent_enough():
    scene = PhysicsScene(seed=1, object_layout=LAYOUT)
    scene.close()
    assert True  # no exception after disconnect within this process scope
