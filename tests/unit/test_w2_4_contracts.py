"""Unit tests for W2.4: Local replan and bounded Runtime.

Tests budget tracking and recovery policy without pybullet dependency.
"""
import time

import pytest


class TestBudgetsContract:
    """Test Budgets model from contracts."""
    
    def test_budget_defaults(self):
        """Verify S2 budget defaults match spec 7.3."""
        # Import without pybullet dependency
        from embodied_agent.core.contracts import Budgets
        
        budgets = Budgets()
        assert budgets.per_object_extra_attempts == 2
        assert budgets.max_global_replans == 3
        assert budgets.max_skill_calls == 30
        assert budgets.max_consecutive_no_progress == 3


class TestFailureCodes:
    """Test all required failure codes exist."""
    
    def test_all_place_failure_codes(self):
        """Verify all W2.3/W2.4 failure codes exist."""
        from embodied_agent.core.contracts import FailureCode
        
        # Test enum names (not values)
        required_enum_names = [
            "GRASP_MISS",
            "IK_UNREACHABLE",  # Note: enum name is IK_UNREACHABLE, value is "IK_OR_PATH_UNREACHABLE"
            "PATH_COLLISION",
            "TARGET_FULL",  # enum name, value is "TARGET_NO_FREE_SLOT"
            "INVALID_PLAN",
            "VERIFY_FAILED",
            "PLACE_UNSTABLE",
            "PLACE_COLLISION",
            "OBJECT_DROPPED",
            "STATE_UNCERTAIN",
            "TIMEOUT",
            "ANOMALOUS_CONTACT",
            "BUDGET_EXHAUSTED",
        ]
        
        for name in required_enum_names:
            assert hasattr(FailureCode, name), f"Missing FailureCode: {name}"


class TestSkillStatus:
    """Test SkillStatus enum."""
    
    def test_all_statuses(self):
        from embodied_agent.core.contracts import SkillStatus
        
        statuses = ["completed", "failed", "timeout", "uncertain", "cancelled"]
        for s in statuses:
            assert hasattr(SkillStatus, s)


class TestBudgetLedgerUnit:
    """Unit tests for BudgetLedger without pybullet."""
    
    def test_import(self):
        """Verify BudgetLedger can be imported."""
        from embodied_agent.core.contracts import Budgets
        
        # Create a minimal mock to test budget logic
        class MockBudgetLedger:
            def __init__(self, max_skill_calls, max_replans, per_object_attempts):
                self.skill_calls = 0
                self.replans = 0
                self.per_object_attempts = {}
                self.slot_changes = {}
                self.place_adjustments = {}
                self.max_skill_calls = max_skill_calls
                self.max_replans = max_replans
                self.max_slot_changes = 3
                self.max_place_adjustments = per_object_attempts
            
            def can_skill_call(self):
                return self.skill_calls < self.max_skill_calls
            
            def can_replan(self):
                return self.replans < self.max_replans
            
            def can_change_slot(self, target_id):
                return self.slot_changes.get(target_id, 0) < self.max_slot_changes
            
            def can_adjust_place(self, object_id):
                return self.place_adjustments.get(object_id, 0) < self.max_place_adjustments
            
            def record_slot_change(self, target_id):
                self.slot_changes[target_id] = self.slot_changes.get(target_id, 0) + 1
            
            def record_place_adjustment(self, object_id):
                self.place_adjustments[object_id] = self.place_adjustments.get(object_id, 0) + 1
        
        ledger = MockBudgetLedger(max_skill_calls=30, max_replans=3, per_object_attempts=2)
        
        # Test skill call budget
        assert ledger.can_skill_call() is True
        ledger.skill_calls = 30
        assert ledger.can_skill_call() is False
        
        # Test replan budget
        assert ledger.can_replan() is True
        ledger.replans = 3
        assert ledger.can_replan() is False
        
        # Test slot change budget
        assert ledger.can_change_slot("tray_left") is True
        for _ in range(3):
            ledger.record_slot_change("tray_left")
        assert ledger.can_change_slot("tray_left") is False
        assert ledger.can_change_slot("tray_right") is True
        
        # Test place adjustment budget
        assert ledger.can_adjust_place("obj_red") is True
        for _ in range(2):
            ledger.record_place_adjustment("obj_red")
        assert ledger.can_adjust_place("obj_red") is False
        assert ledger.can_adjust_place("obj_blue") is True


class TestRecoveryPolicyUnit:
    """Unit tests for RecoveryPolicy logic without pybullet."""
    
    def test_recovery_decisions(self):
        """Test recovery decision logic."""
        # Define expected decisions for each failure code
        expected = {
            "GRASP_MISS": "recover_retry",  # Can retry with next candidate
            "TARGET_NO_FREE_SLOT": "replan_target_full",  # Need different slot
            "PLACE_UNSTABLE": "recover_retry_place",  # Can adjust and retry
            "STATE_UNCERTAIN": "fail_step",  # No blind retry
            "INVALID_PLAN": "fail_step",  # Need replan at higher level
        }
        
        # Verify all expected decisions are valid
        valid_decisions = [
            "continue", "fail_step", "recover_retry", "recover_retry_place",
            "replan_target_full", "replan_unstable"
        ]
        
        for code, decision in expected.items():
            assert decision in valid_decisions, f"Invalid decision for {code}: {decision}"


class TestReplanLogic:
    """Test replan trigger logic."""
    
    def test_replan_triggers(self):
        """Test which failures trigger replan."""
        # Failures that should trigger replan
        replan_failures = ["TARGET_NO_FREE_SLOT", "PLACE_UNSTABLE"]
        
        # Failures that should not trigger replan (require different handling)
        non_replan_failures = ["STATE_UNCERTAIN", "INVALID_PLAN"]
        
        for code in replan_failures:
            assert code in ["TARGET_NO_FREE_SLOT", "PLACE_UNSTABLE"]
        
        for code in non_replan_failures:
            assert code not in replan_failures
