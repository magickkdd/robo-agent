"""Online DeepSeek tests (spec 12.4): opt-in only, never run by default pytest.
Run with: RUN_ONLINE=1 pytest tests/online -v"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ONLINE") != "1", reason="online API tests are opt-in (RUN_ONLINE=1)"
)


def test_online_goal_and_plan():
    from embodied_agent.core.scene import PhysicsScene
    from embodied_agent.core.verify import build_world_state
    from embodied_agent.adapters.deepseek import DeepSeekPlanner

    scene = PhysicsScene(seed=7, object_layout=[
        {"entity_id": "obj_red_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.44, 0.0],
         "color": [0.85, 0.1, 0.1, 1], "attributes": {"color": "red", "shape": "cube"}}])
    w = build_world_state(scene, 0, "o0")
    planner = DeepSeekPlanner.from_env()
    out = planner.plan("把红色物体放到左边托盘。", w,
                       {"pick": {"args": {"object_id": "str"}},
                        "place": {"args": {"object_id": "str", "target_id": "str"}}})
    scene.close()
    assert out["goal_spec"]["assignments"][0]["target_id"] == "tray_left"
    assert out["plan"]["steps"][0]["skill"] == "pick"
