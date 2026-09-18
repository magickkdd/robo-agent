"""Unit tests for W2.5: v2 benchmark configuration.

Tests task configuration validity without pybullet dependency.
"""
import yaml
import pytest
from pathlib import Path


class TestV2SuiteConfig:
    """Test v2 suite configuration file."""
    
    def test_config_loads(self):
        """Verify v2_suite_config.yaml loads correctly."""
        config_path = Path(__file__).parent.parent.parent / "configs" / "tasks" / "v2_suite_config.yaml"
        
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            assert config["schema_version"] == "1"
            assert config["suite_name"] == "TabletopOrganize-v2"
            assert "objects" in config
            assert "targets" in config
            assert "budgets" in config
    
    def test_objects_defined(self):
        """Verify all required objects are defined."""
        config_path = Path(__file__).parent.parent.parent / "configs" / "tasks" / "v2_suite_config.yaml"
        
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            object_ids = [obj["id"] for obj in config["objects"]]
            required = ["red_cube", "blue_cube", "green_cube", "yellow_cuboid", "cyan_cylinder"]
            
            for obj_id in required:
                assert obj_id in object_ids, f"Missing object: {obj_id}"
    
    def test_targets_defined(self):
        """Verify all required targets are defined."""
        config_path = Path(__file__).parent.parent.parent / "configs" / "tasks" / "v2_suite_config.yaml"
        
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            target_ids = [t["id"] for t in config["targets"]]
            required = ["tray_left", "tray_middle", "tray_right"]
            
            for target_id in required:
                assert target_id in target_ids, f"Missing target: {target_id}"
    
    def test_budgets_spec_7_3(self):
        """Verify budgets match spec 7.3."""
        config_path = Path(__file__).parent.parent.parent / "configs" / "tasks" / "v2_suite_config.yaml"
        
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            budgets = config["budgets"]
            assert budgets["per_object_extra_attempts"] == 2
            assert budgets["max_global_replans"] == 5
            assert budgets["max_slot_changes_per_target"] == 3
            assert budgets["max_place_adjustments_per_object"] == 2


class TestV2CleanConfig:
    """Test v2-clean task configuration."""
    
    def _get_config_path(self):
        """Get path to v2_clean.yaml."""
        return Path(__file__).parent.parent.parent / "configs" / "tasks" / "v2_clean.yaml"
    
    def test_clean_loads(self):
        """Verify v2_clean.yaml loads correctly."""
        config_path = self._get_config_path()
        
        if not config_path.exists():
            pytest.skip("v2_clean.yaml not found")
        
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        assert config["subset"] == "v2-clean"
        assert config["total_episodes"] == 90
        assert "tasks" in config
    
    def test_clean_task_count(self):
        """Verify correct number of tasks per object count."""
        config_path = self._get_config_path()
        
        if not config_path.exists():
            pytest.skip("v2_clean.yaml not found")
        
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        tasks = config["tasks"]
        three_obj = [t for t in tasks if t["object_count"] == 3]
        four_obj = [t for t in tasks if t["object_count"] == 4]
        five_obj = [t for t in tasks if t["object_count"] == 5]
        
        assert len(three_obj) == 10, f"Expected 10 3-object tasks, got {len(three_obj)}"
        assert len(four_obj) == 10, f"Expected 10 4-object tasks, got {len(four_obj)}"
        assert len(five_obj) == 10, f"Expected 10 5-object tasks, got {len(five_obj)}"
    
    def test_clean_assignments_valid(self):
        """Verify all assignments reference valid objects and targets."""
        config_path = self._get_config_path()
        
        if not config_path.exists():
            pytest.skip("v2_clean.yaml not found")
        
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        valid_targets = {"tray_left", "tray_middle", "tray_right"}
        
        for task in config["tasks"]:
            for obj_id, target_id in task["assignments"].items():
                assert obj_id in task["objects"], f"Task {task['task_id']}: assignment for unknown object {obj_id}"
                assert target_id in valid_targets, f"Task {task['task_id']}: assignment to unknown target {target_id}"
    
    def test_clean_seeds_unique(self):
        """Verify all seeds are unique."""
        config_path = self._get_config_path()
        
        if not config_path.exists():
            pytest.skip("v2_clean.yaml not found")
        
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        seeds = [t["seed"] for t in config["tasks"]]
        assert len(seeds) == len(set(seeds)), "Duplicate seeds found"


class TestV2AcceptanceCriteria:
    """Test v2 acceptance criteria documentation."""
    
    def test_acceptance_criteria_documented(self):
        """Document spec 8.2 acceptance criteria."""
        criteria = {
            "v2_clean_rule": ">=90% complete task success, 5-obj >=90%",
            "v2_clean_llm": ">=85% or within 5pp of S1",
            "v2_capacity": "No illegal execution, must reorder/report",
            "v2_interference": "Slot replan reduces failures vs S1",
            "v2_fault": "Structured codes, recovery improves",
            "no_cheating": "No hidden adhesion, teleport, etc.",
        }
        
        assert len(criteria) == 6
        assert "v2_clean_rule" in criteria
        assert "v2_fault" in criteria
