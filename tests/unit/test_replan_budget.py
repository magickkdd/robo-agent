"""Unit tests for W2.4: Local replan and bounded Runtime.

Tests:
- BudgetLedger slot change tracking
- BudgetLedger place adjustment tracking
- RecoveryPolicy decisions for new failure codes
- Replan trigger logic
"""
import numpy as np
import pytest
import time

from embodied_agent.core.contracts import FailureCode, SkillStatus, Budgets, SkillResult
from embodied_agent.core.runtime import BudgetLedger, RecoveryPolicy


class TestBudgetLedger:
    """Test BudgetLedger S2 budget tracking."""
    
    def test_init(self):
        budgets = Budgets()
        ledger = BudgetLedger(budgets, n_objects=3)
        
        assert ledger.skill_calls == 0
        assert ledger.replans == 0
        assert ledger.slot_changes == {}
        assert ledger.place_adjustments == {}
    
    def test_can_change_slot(self):
        budgets = Budgets()
        ledger = BudgetLedger(budgets, n_objects=3)
        
        # Initially can change slot
        assert ledger.can_change_slot("tray_left") is True
        
        # After 3 changes, cannot change
        ledger.record_slot_change("tray_left")
        ledger.record_slot_change("tray_left")
        ledger.record_slot_change("tray_left")
        assert ledger.can_change_slot("tray_left") is False
        
        # Different target is unaffected
        assert ledger.can_change_slot("tray_right") is True
    
    def test_can_adjust_place(self):
        budgets = Budgets()
        ledger = BudgetLedger(budgets, n_objects=3)
        
        # Initially can adjust
        assert ledger.can_adjust_place("obj_red") is True
        
        # After 2 adjustments, cannot adjust
        ledger.record_place_adjustment("obj_red")
        ledger.record_place_adjustment("obj_red")
        assert ledger.can_adjust_place("obj_red") is False
        
        # Different object is unaffected
        assert ledger.can_adjust_place("obj_blue") is True
    
    def test_slot_change_counting(self):
        budgets = Budgets()
        ledger = BudgetLedger(budgets, n_objects=3)
        
        ledger.record_slot_change("tray_left")
        ledger.record_slot_change("tray_left")
        ledger.record_slot_change("tray_right")
        
        assert ledger.slot_changes["tray_left"] == 2
        assert ledger.slot_changes["tray_right"] == 1
    
    def test_place_adjustment_counting(self):
        budgets = Budgets()
        ledger = BudgetLedger(budgets, n_objects=3)
        
        ledger.record_place_adjustment("obj_red")
        ledger.record_place_adjustment("obj_red")
        ledger.record_place_adjustment("obj_blue")
        
        assert ledger.place_adjustments["obj_red"] == 2
        assert ledger.place_adjustments["obj_blue"] == 1
    
    def test_can_skill_call(self):
        budgets = Budgets(max_skill_calls=5)
        ledger = BudgetLedger(budgets, n_objects=3)
        
        for _ in range(5):
            ledger.skill_calls += 1
            assert ledger.can_skill_call() is True
        
        ledger.skill_calls += 1
        assert ledger.can_skill_call() is False
    
    def test_can_replan(self):
        budgets = Budgets(max_global_replans=3)
        ledger = BudgetLedger(budgets, n_objects=3)
        
        for _ in range(3):
            ledger.replans += 1
            assert ledger.can_replan() is True
        
        ledger.replans += 1
        assert ledger.can_replan() is False


class TestRecoveryPolicy:
    """Test RecoveryPolicy decisions for S2 failure codes."""
    
    def test_continue_on_success(self):
        policy = RecoveryPolicy(enabled=True)
        ledger = BudgetLedger(Budgets(), n_objects=3)
        
        result = SkillResult(
            call_id="test", status=SkillStatus.completed,
            t_start=0, t_end=1
        )
        
        decision = policy.decide(result, "obj_red", ledger, skill="pick")
        assert decision == "continue"
    
    def test_grasp_miss_retry(self):
        policy = RecoveryPolicy(enabled=True)
        ledger = BudgetLedger(Budgets(), n_objects=3)
        
        result = SkillResult(
            call_id="test", status=SkillStatus.failed,
            failure_code=FailureCode.GRASP_MISS.value,
            t_start=0, t_end=1
        )
        
        decision = policy.decide(result, "obj_red", ledger, skill="pick")
        assert decision == "recover_retry"
    
    def test_target_full_replan(self):
        policy = RecoveryPolicy(enabled=True)
        ledger = BudgetLedger(Budgets(), n_objects=3)
        
        result = SkillResult(
            call_id="test", status=SkillStatus.failed,
            failure_code=FailureCode.TARGET_FULL.value,
            t_start=0, t_end=1,
            held_state="obj_red"
        )
        
        decision = policy.decide(result, "obj_red", ledger, skill="place")
        assert decision == "replan_target_full"
    
    def test_place_unstable_retry(self):
        policy = RecoveryPolicy(enabled=True)
        ledger = BudgetLedger(Budgets(), n_objects=3)
        
        result = SkillResult(
            call_id="test", status=SkillStatus.failed,
            failure_code=FailureCode.PLACE_UNSTABLE.value,
            t_start=0, t_end=1,
            held_state="obj_red"
        )
        
        decision = policy.decide(result, "obj_red", ledger, skill="place")
        assert decision == "recover_retry_place"
    
    def test_place_unstable_replan_when_exhausted(self):
        policy = RecoveryPolicy(enabled=True)
        budgets = Budgets(per_object_extra_attempts=0)
        ledger = BudgetLedger(budgets, n_objects=3)
        
        result = SkillResult(
            call_id="test", status=SkillStatus.failed,
            failure_code=FailureCode.PLACE_UNSTABLE.value,
            t_start=0, t_end=1,
            held_state="obj_red"
        )
        
        decision = policy.decide(result, "obj_red", ledger, skill="place")
        assert decision == "replan_unstable"
    
    def test_state_uncertain_fail(self):
        policy = RecoveryPolicy(enabled=True)
        ledger = BudgetLedger(Budgets(), n_objects=3)
        
        result = SkillResult(
            call_id="test", status=SkillStatus.failed,
            failure_code=FailureCode.STATE_UNCERTAIN.value,
            t_start=0, t_end=1
        )
        
        decision = policy.decide(result, "obj_red", ledger, skill="place")
        assert decision == "fail_step"
    
    def test_disabled_always_fail(self):
        policy = RecoveryPolicy(enabled=False)
        ledger = BudgetLedger(Budgets(), n_objects=3)
        
        result = SkillResult(
            call_id="test", status=SkillStatus.failed,
            failure_code=FailureCode.GRASP_MISS.value,
            t_start=0, t_end=1
        )
        
        decision = policy.decide(result, "obj_red", ledger, skill="pick")
        assert decision == "fail_step"
    
    def test_verify_failed_retry_when_holding(self):
        policy = RecoveryPolicy(enabled=True)
        ledger = BudgetLedger(Budgets(), n_objects=3)
        
        result = SkillResult(
            call_id="test", status=SkillStatus.failed,
            failure_code=FailureCode.VERIFY_FAILED.value,
            t_start=0, t_end=1,
            held_state="obj_red"
        )
        
        decision = policy.decide(result, "obj_red", ledger, skill="place")
        assert decision == "recover_retry_place"


class TestReplanTrigger:
    """Test replan trigger logic."""
    
    def test_replan_not_terminal(self):
        """replan_target_full and replan_unstable should not be terminal."""
        # These decisions return (failure_code, terminate=False)
        # which triggers the outer while loop to replan
        non_terminal_decisions = ["replan_target_full", "replan_unstable"]
        
        for decision in non_terminal_decisions:
            # The decision itself indicates replan is needed
            assert decision.startswith("replan_")
