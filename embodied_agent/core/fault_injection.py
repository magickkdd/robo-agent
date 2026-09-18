"""Fault injection for place operations (W2.3).

Supports:
- PLACE_UNSTABLE: Inject instability during placement (nudge object after release)
- TARGET_FULL: Pre-fill tray to test capacity handling
- PLACE_COLLISION: Inject collision during descent

Usage:
    injector = PlaceFaultInjector(scene)
    injector.apply_fault(oid, tid, fault_type)
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

import numpy as np


class PlaceFaultType(str, enum.Enum):
    NONE = "none"
    PLACE_UNSTABLE = "place_unstable"  # Nudge object after release
    TARGET_FULL = "target_full"  # Pre-fill tray
    PLACE_COLLISION = "place_collision"  # Collision during descent


@dataclass
class PlaceFaultConfig:
    """Configuration for place fault injection."""
    fault_type: PlaceFaultType = PlaceFaultType.NONE
    target_id: Optional[str] = None
    nudge_force: float = 0.05  # N force for PLACE_UNSTABLE
    fill_count: int = 3  # Number of objects to pre-fill for TARGET_FULL
    collision_height_offset: float = 0.02  # Height offset for collision


class PlaceFaultInjector:
    """Injects faults during place operations for testing (W2.3)."""
    
    def __init__(self, scene):
        self.scene = scene
        self._active_fault: Optional[PlaceFaultConfig] = None
    
    def set_fault(self, config: PlaceFaultConfig):
        """Set the active fault to inject."""
        self._active_fault = config
    
    def clear_fault(self):
        """Clear active fault."""
        self._active_fault = None
    
    def apply_fault(self, object_id: str, target_id: str) -> bool:
        """Apply fault during placement. Returns True if fault was applied."""
        if self._active_fault is None or self._active_fault.fault_type == PlaceFaultType.NONE:
            return False
        
        if self._active_fault.target_id is not None and self._active_fault.target_id != target_id:
            return False
        
        if self._active_fault.fault_type == PlaceFaultType.PLACE_UNSTABLE:
            return self._apply_unstable(object_id)
        elif self._active_fault.fault_type == PlaceFaultType.TARGET_FULL:
            return self._apply_target_full(target_id)
        elif self._active_fault.fault_type == PlaceFaultType.PLACE_COLLISION:
            return self._apply_collision(object_id)
        
        return False
    
    def _apply_unstable(self, object_id: str) -> bool:
        """Nudge object after release to create instability."""
        import pybullet as p
        
        if object_id not in self.scene.objects:
            return False
        
        body = self.scene.objects[object_id]["body"]
        pos, _ = self.scene.object_pose(object_id)
        
        # Apply lateral nudge force
        force = np.array([self._active_fault.nudge_force, 0.0, 0.0])
        p.applyExternalForce(body, -1, force.tolist(), pos.tolist(), p.WORLD_FRAME)
        
        return True
    
    def _apply_target_full(self, target_id: str) -> bool:
        """Pre-fill tray with dummy objects to test capacity handling."""
        import pybullet as p
        
        if target_id not in self.scene.trays:
            return False
        
        tray = self.scene.trays[target_id]
        center = tray["center"]
        floor_top = tray["floor_top"]
        
        # Create dummy objects to fill the tray
        dummy_size = 0.025
        positions = [
            [center[0], center[1], floor_top + dummy_size],
            [center[0] - 0.05, center[1], floor_top + dummy_size],
            [center[0] + 0.05, center[1], floor_top + dummy_size],
        ]
        
        for i, pos in enumerate(positions[:self._active_fault.fill_count]):
            col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[dummy_size] * 3)
            body = p.createMultiBody(
                baseMass=0.05,
                baseCollisionShapeIndex=col,
                basePosition=pos,
            )
            p.changeVisualShape(body, -1, rgbaColor=[0.5, 0.5, 0.5, 0.8])
            p.changeDynamics(body, -1, lateralFriction=0.5)
        
        return True
    
    def _apply_collision(self, object_id: str) -> bool:
        """Mark that collision should be simulated during descent.
        
        This is a flag - the actual collision handling is in _do_place.
        """
        # Store collision flag for _do_place to check
        self._collision_pending = True
        return True
    
    def check_collision_pending(self) -> bool:
        """Check if collision should be simulated."""
        if hasattr(self, '_collision_pending') and self._collision_pending:
            self._collision_pending = False
            return True
        return False
