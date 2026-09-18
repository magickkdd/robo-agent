"""Unit tests for PlacementPlanner (W2.2).

Tests footprint geometry, capacity modeling, obstacle-aware slot generation,
and path clearance validation.
"""
import math

import numpy as np
import pytest

from embodied_agent.core.placement_planner import (
    ObjectFootprint,
    PlacementPlan,
    PlacementPlanner,
    TraySlot,
)


class MockTray:
    """Mock tray for testing without PyBullet."""
    def __init__(self, center, inner_half, floor_top):
        self._data = {
            "center": np.array(center),
            "inner_half": inner_half,
            "floor_top": floor_top,
        }
    
    def __getitem__(self, key):
        return self._data[key]
    
    def __contains__(self, key):
        return key in self._data


class MockObject:
    """Mock object for testing without PyBullet."""
    def __init__(self, half_h):
        self._data = {
            "half_h": half_h,
        }
    
    def __getitem__(self, key):
        return self._data[key]
    
    def __contains__(self, key):
        return key in self._data


class MockScene:
    """Mock scene for testing without PyBullet."""
    def __init__(self):
        self.trays = {
            "tray_left": MockTray([-0.3, 0.0, 0.6], 0.12, 0.6),
            "tray_middle": MockTray([0.0, 0.0, 0.6], 0.12, 0.6),
            "tray_right": MockTray([0.3, 0.0, 0.6], 0.12, 0.6),
        }
        self.objects = {
            "obj_red": MockObject(0.025),
            "obj_blue": MockObject(0.025),
            "obj_green": MockObject(0.025),
        }
        # Objects are NOT in trays initially (outside tray bounds)
        self._poses = {
            "obj_red": (np.array([0.45, -0.10, 0.625]), None),
            "obj_blue": (np.array([0.45, 0.12, 0.625]), None),
            "obj_green": (np.array([0.75, -0.05, 0.625]), None),
        }
    
    def object_pose(self, entity_id):
        return self._poses.get(entity_id, (np.zeros(3), None))
    
    def held_bodies(self):
        return set()


class TestObjectFootprint:
    """Test ObjectFootprint geometry calculations."""
    
    def test_bounding_radius(self):
        fp = ObjectFootprint(
            entity_id="test",
            center=np.array([0.0, 0.0]),
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        # sqrt(0.025^2 + 0.025^2) + 0.010 ≈ 0.045
        assert abs(fp.bounding_radius - 0.045) < 0.001
    
    def test_intersects_circle_no_overlap(self):
        fp1 = ObjectFootprint(
            entity_id="test1",
            center=np.array([0.0, 0.0]),
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        fp2 = ObjectFootprint(
            entity_id="test2",
            center=np.array([0.1, 0.0]),
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        # Distance = 0.1, sum of radii ≈ 0.09, no overlap
        assert not fp1.intersects_circle(fp2.center, fp2.bounding_radius)
    
    def test_intersects_circle_overlap(self):
        fp1 = ObjectFootprint(
            entity_id="test1",
            center=np.array([0.0, 0.0]),
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        fp2 = ObjectFootprint(
            entity_id="test2",
            center=np.array([0.05, 0.0]),
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        # Distance = 0.05, sum of radii ≈ 0.09, overlap
        assert fp1.intersects_circle(fp2.center, fp2.bounding_radius)
    
    def test_intersects_rectangle_no_overlap(self):
        fp = ObjectFootprint(
            entity_id="test",
            center=np.array([0.0, 0.0]),
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        other_center = np.array([0.1, 0.0])
        other_half = np.array([0.025, 0.025])
        # Distance = 0.1, sum of extents = 0.06, no overlap
        assert not fp.intersects_rectangle(other_center, other_half)
    
    def test_intersects_rectangle_overlap(self):
        fp = ObjectFootprint(
            entity_id="test",
            center=np.array([0.0, 0.0]),
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        other_center = np.array([0.04, 0.0])
        other_half = np.array([0.025, 0.025])
        # Distance = 0.04, sum of extents = 0.06, overlap
        assert fp.intersects_rectangle(other_center, other_half)


class TestPlacementPlanner:
    """Test PlacementPlanner core functionality."""
    
    def test_init(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        assert planner.scene == scene
    
    def test_compute_footprint(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        fp = planner.compute_footprint("obj_red")
        assert fp.entity_id == "obj_red"
        assert fp.center.shape == (2,)
        assert fp.half_extents.shape == (2,)
        assert fp.clearance == 0.010
    
    def test_get_tray_interior(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        center, half, floor = planner.get_tray_interior("tray_left")
        np.testing.assert_array_equal(center, [-0.3, 0.0])
        assert half == 0.12
        assert floor == 0.6
    
    def test_get_placed_objects_empty_tray(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        placed = planner.get_placed_objects("tray_left")
        # No objects in tray initially
        assert len(placed) == 0
    
    def test_generate_candidate_slots(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        placed = []
        slots = planner.generate_candidate_slots("tray_left", "obj_red", placed)
        
        # Should have at least one valid slot
        assert len(slots) > 0
        assert all(s.is_valid for s in slots)
    
    def test_generate_candidate_slots_with_obstacle(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        # Add an obstacle at center
        obstacle = ObjectFootprint(
            entity_id="obstacle",
            center=np.array([-0.3, 0.0]),  # tray_left center
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        placed = [obstacle]
        
        slots = planner.generate_candidate_slots("tray_left", "obj_red", placed)
        
        # Slots should avoid the obstacle
        for slot in slots:
            dist = np.linalg.norm(slot.position - obstacle.center)
            assert dist > obstacle.bounding_radius + 0.010
    
    def test_find_slot_for_object(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        slot = planner.find_slot_for_object("tray_left", "obj_red")
        assert slot is not None
        assert slot.is_valid
    
    def test_plan_placement_single_object(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        plan = planner.plan_placement("tray_left", ["obj_red"])
        assert plan.capacity_ok
        assert len(plan.slots) == 1
        assert plan.slots[0].is_valid
    
    def test_plan_placement_multiple_objects(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        plan = planner.plan_placement("tray_left", ["obj_red", "obj_blue"])
        assert plan.capacity_ok
        assert len(plan.slots) == 2
        
        # Slots should not overlap
        fp1 = ObjectFootprint(
            entity_id="obj_red",
            center=plan.slots[0].position,
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        fp2 = ObjectFootprint(
            entity_id="obj_blue",
            center=plan.slots[1].position,
            half_extents=np.array([0.025, 0.025]),
            clearance=0.010,
        )
        assert not fp1.intersects_circle(fp2.center, fp2.bounding_radius)
    
    def test_plan_placement_capacity_exceeded(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        # Try to place 10 objects in a small tray
        object_ids = [f"obj_{i}" for i in range(10)]
        # Mock objects don't exist, so this should fail gracefully
        plan = planner.plan_placement("tray_left", object_ids)
        # Should fail because objects don't exist in scene
        assert not plan.capacity_ok
    
    def test_get_capacity(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        # With 25mm half-height objects, should fit ~4 in a 240mm tray
        capacity = planner.get_capacity("tray_left", [0.025])
        assert capacity >= 1
    
    def test_unknown_target(self):
        scene = MockScene()
        planner = PlacementPlanner(scene)
        
        plan = planner.plan_placement("tray_unknown", ["obj_red"])
        assert not plan.capacity_ok
        assert "Unknown target" in plan.error


class TestTraySlot:
    """Test TraySlot properties."""
    
    def test_valid_slot(self):
        slot = TraySlot(
            position=np.array([0.0, 0.0]),
            z=0.625,
            object_id="test",
            clearance_ok=True,
            reachability_ok=True,
        )
        assert slot.is_valid
    
    def test_invalid_slot_clearance(self):
        slot = TraySlot(
            position=np.array([0.0, 0.0]),
            z=0.625,
            object_id="test",
            clearance_ok=False,
            reachability_ok=True,
        )
        assert not slot.is_valid
    
    def test_invalid_slot_reachability(self):
        slot = TraySlot(
            position=np.array([0.0, 0.0]),
            z=0.625,
            object_id="test",
            clearance_ok=True,
            reachability_ok=False,
        )
        assert not slot.is_valid


class TestPlacementPlan:
    """Test PlacementPlan properties."""
    
    def test_valid_slots(self):
        plan = PlacementPlan(target_id="tray_left")
        plan.slots = [
            TraySlot(
                position=np.array([0.0, 0.0]),
                z=0.625,
                object_id="obj1",
                clearance_ok=True,
                reachability_ok=True,
            ),
            TraySlot(
                position=np.array([0.05, 0.0]),
                z=0.625,
                object_id="obj2",
                clearance_ok=False,
                reachability_ok=True,
            ),
        ]
        
        assert plan.num_valid == 1
        assert len(plan.valid_slots) == 1
        assert plan.valid_slots[0].object_id == "obj1"
    
    def test_empty_plan(self):
        plan = PlacementPlan(target_id="tray_left")
        assert plan.num_valid == 0
        assert len(plan.valid_slots) == 0
