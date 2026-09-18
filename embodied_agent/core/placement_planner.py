"""Placement planner (spec W2.2): footprint-based slot generation with
obstacle awareness and capacity modeling.

Replaces the fixed 5-slot pattern in skills.py with a geometric approach that:
1. Computes object footprints (bounding circles/rectangles)
2. Models tray capacity explicitly
3. Uses already-placed objects as obstacles for slot generation
4. Validates path clearance for descent

This directly addresses 6/12 failures from W2.1:
- capacity: 3/12 (B1) - tray capacity exhaustion
- place_geometry: 5/12 (B1:2, E1:3) - path blocked by placed objects
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class ObjectFootprint:
    """Geometric footprint of an object for placement planning."""
    entity_id: str
    center: np.ndarray  # [x, y] center position
    half_extents: np.ndarray  # [half_x, half_y] bounding box half-extents
    clearance: float = 0.010  # minimum clearance from other objects (10mm)
    
    @property
    def bounding_radius(self) -> float:
        """Circumscribed circle radius for quick collision checks."""
        return float(np.linalg.norm(self.half_extents)) + self.clearance
    
    def intersects_circle(self, other_center: np.ndarray, other_radius: float) -> bool:
        """Check if this footprint overlaps with another circle."""
        dist = np.linalg.norm(self.center[:2] - other_center[:2])
        return dist < (self.bounding_radius + other_radius)
    
    def intersects_rectangle(self, other_center: np.ndarray, other_half: np.ndarray) -> bool:
        """Check if this footprint overlaps with an axis-aligned rectangle."""
        # Convert to AABB check
        dx = abs(self.center[0] - other_center[0])
        dy = abs(self.center[1] - other_center[1])
        return dx < (self.half_extents[0] + other_half[0] + self.clearance) and \
               dy < (self.half_extents[1] + other_half[1] + self.clearance)


@dataclass
class TraySlot:
    """A candidate placement slot inside a tray."""
    position: np.ndarray  # [x, y] center position
    z: float  # placement z height (tray floor + object half_height)
    object_id: str  # which object this slot is for
    footprint: Optional[ObjectFootprint] = None
    clearance_ok: bool = True
    reachability_ok: bool = True
    
    @property
    def is_valid(self) -> bool:
        return self.clearance_ok and self.reachability_ok


@dataclass
class PlacementPlan:
    """Result of placement planning for a target tray."""
    target_id: str
    slots: list[TraySlot] = field(default_factory=list)
    capacity_ok: bool = True
    error: Optional[str] = None
    
    @property
    def valid_slots(self) -> list[TraySlot]:
        return [s for s in self.slots if s.is_valid]
    
    @property
    def num_valid(self) -> int:
        return len(self.valid_slots)


class PlacementPlanner:
    """Geometric placement planner with footprint-based slot generation.
    
    Key improvements over fixed 5-slot pattern:
    1. Capacity modeling: explicitly tracks how many objects fit
    2. Footprint geometry: uses actual object sizes, not arbitrary patterns
    3. Obstacle awareness: already-placed objects block slot generation
    4. Path clearance: validates descent path is not blocked
    """
    
    # Default clearance parameters (spec 11.2)
    DEFAULT_CLEARANCE_M = 0.010  # 10mm between objects
    TRAY_MARGIN_M = 0.005  # 5mm margin from tray edges (spec 11.2)
    DESCENT_CLEARANCE_M = 0.020  # 20mm clearance for descent path
    
    def __init__(self, scene):
        """
        Args:
            scene: PhysicsScene instance for accessing tray/object geometry
        """
        self.scene = scene
    
    def compute_footprint(self, entity_id: str) -> ObjectFootprint:
        """Compute the geometric footprint of an object.
        
        For simple shapes (box, cylinder), uses half_extents directly.
        The footprint is axis-aligned and centered at the object's current position.
        """
        pos, _ = self.scene.object_pose(entity_id)
        
        # Get object size (with fallback for unknown objects)
        if entity_id in self.scene.objects:
            obj = self.scene.objects[entity_id]
            half_xy = obj["half_h"]
        else:
            # Default size for unknown objects (conservative estimate)
            half_xy = 0.030  # 30mm half-height
        
        return ObjectFootprint(
            entity_id=entity_id,
            center=pos[:2].copy(),
            half_extents=np.array([half_xy, half_xy]),
            clearance=self.DEFAULT_CLEARANCE_M,
        )
    
    def get_tray_interior(self, target_id: str) -> tuple[np.ndarray, float, float]:
        """Get tray interior parameters.
        
        Returns:
            (center_xy, inner_half, floor_top_z)
        """
        tray = self.scene.trays[target_id]
        return tray["center"][:2], tray["inner_half"], tray["floor_top"]
    
    def get_placed_objects(self, target_id: str) -> list[ObjectFootprint]:
        """Get footprints of objects already placed in the tray."""
        tray = self.scene.trays[target_id]
        placed = []
        
        for eid, d in self.scene.objects.items():
            pos, _ = self.scene.object_pose(eid)
            
            # Check if object is in this tray (similar to _free_slot logic)
            if (abs(pos[0] - tray["center"][0]) < tray["inner_half"] and
                abs(pos[1] - tray["center"][1]) < tray["inner_half"] and
                abs(pos[2] - (tray["floor_top"] + d["half_h"])) < 0.02):
                placed.append(self.compute_footprint(eid))
        
        return placed
    
    def generate_candidate_slots(
        self,
        target_id: str,
        object_id: str,
        placed_objects: list[ObjectFootprint],
    ) -> list[TraySlot]:
        """Generate candidate placement slots in the tray interior.
        
        Uses a grid-based approach with obstacle-aware spacing:
        1. Start from tray center
        2. Generate concentric rings of candidates
        3. Filter by clearance from placed objects
        4. Filter by tray interior bounds
        """
        center_xy, inner_half, floor_top = self.get_tray_interior(target_id)
        
        # Get object size (with fallback for unknown objects)
        if object_id in self.scene.objects:
            obj = self.scene.objects[object_id]
            half_h = obj["half_h"]
        else:
            # Default size for unknown objects (conservative estimate)
            half_h = 0.030  # 30mm half-height
        slot_z = floor_top + half_h + 0.005  # Slightly above floor for stability
        
        # Object footprint for clearance checks
        obj_footprint = ObjectFootprint(
            entity_id=object_id,
            center=np.zeros(2),  # Will be set at each candidate
            half_extents=np.array([half_h, half_h]),
            clearance=self.DEFAULT_CLEARANCE_M,
        )
        
        # Generate candidates in concentric rings
        candidates = []
        ring_spacing = half_h * 2 + self.DEFAULT_CLEARANCE_M * 2  # Diameter + clearance
        
        # Ring 0: center
        candidates.append((center_xy[0], center_xy[1]))
        
        # Rings 1, 2, 3: outward from center
        for ring in range(1, 4):
            ring_radius = ring * ring_spacing
            num_points = max(4, ring * 4)  # More points in outer rings
            for i in range(num_points):
                angle = 2 * math.pi * i / num_points
                x = center_xy[0] + ring_radius * math.cos(angle)
                y = center_xy[1] + ring_radius * math.sin(angle)
                candidates.append((x, y))
        
        # Filter candidates
        valid_slots = []
        for x, y in candidates:
            # Check tray bounds (with margin)
            if (abs(x - center_xy[0]) > inner_half - self.TRAY_MARGIN_M or
                abs(y - center_xy[1]) > inner_half - self.TRAY_MARGIN_M):
                continue
            
            # Check clearance from placed objects
            candidate_center = np.array([x, y])
            obj_footprint.center = candidate_center
            
            clearance_ok = True
            for placed in placed_objects:
                if obj_footprint.intersects_circle(placed.center, placed.bounding_radius):
                    clearance_ok = False
                    break
            
            if not clearance_ok:
                continue
            
            # Check path clearance (descent won't hit placed objects)
            reachability_ok = self._check_descent_clearance(
                candidate_center, slot_z, placed_objects
            )
            
            slot = TraySlot(
                position=candidate_center,
                z=slot_z,
                object_id=object_id,
                footprint=obj_footprint,
                clearance_ok=clearance_ok,
                reachability_ok=reachability_ok,
            )
            
            if slot.is_valid:
                valid_slots.append(slot)
        
        return valid_slots
    
    def _check_descent_clearance(
        self,
        slot_xy: np.ndarray,
        slot_z: float,
        placed_objects: list[ObjectFootprint],
    ) -> bool:
        """Check if descent path to slot is clear of obstacles.
        
        The descent path is a vertical line from transfer height to slot_z.
        We check if any placed object's bounding circle intersects this path.
        """
        # Transfer height is typically 0.78m (see skills.py)
        transfer_z = 0.78
        
        # Check each placed object
        for placed in placed_objects:
            # Get object height (approximate from footprint)
            # For simplicity, assume objects are ~5cm tall
            obj_height = 0.05
            
            # Check if object is in the descent cylinder
            # Horizontal distance from descent line to object center
            horiz_dist = np.linalg.norm(slot_xy - placed.center)
            
            # Object's top surface is at floor_top + obj_height
            # Descent path goes from transfer_z down to slot_z
            
            # If horizontal distance is less than combined radii,
            # and object top is above slot_z, path might be blocked
            if horiz_dist < (placed.bounding_radius + self.DESCENT_CLEARANCE_M):
                # Object is close horizontally; check if it's tall enough to block
                # For now, be conservative: if object is in the way, mark as blocked
                return False
        
        return True
    
    def plan_placement(
        self,
        target_id: str,
        object_ids: list[str],
    ) -> PlacementPlan:
        """Generate a placement plan for multiple objects in a target tray.
        
        Args:
            target_id: Target tray ID (e.g., "tray_left")
            object_ids: List of object IDs to place in order
            
        Returns:
            PlacementPlan with slots for each object
        """
        plan = PlacementPlan(target_id=target_id)
        
        # Get tray parameters
        try:
            center_xy, inner_half, floor_top = self.get_tray_interior(target_id)
        except KeyError:
            plan.capacity_ok = False
            plan.error = f"Unknown target: {target_id}"
            return plan
        
        # Get already-placed objects
        placed_objects = self.get_placed_objects(target_id)
        
        # Generate slots for each object
        for obj_id in object_ids:
            slots = self.generate_candidate_slots(
                target_id, obj_id, placed_objects
            )
            
            if not slots:
                plan.capacity_ok = False
                plan.error = f"No valid slot for {obj_id} in {target_id}"
                break
            
            # Use the first valid slot (closest to center)
            best_slot = slots[0]
            plan.slots.append(best_slot)
            
            # Add this object's footprint to placed_objects for next iteration
            obj_footprint = self.compute_footprint(obj_id)
            obj_footprint.center = best_slot.position
            placed_objects.append(obj_footprint)
        
        return plan
    
    def find_slot_for_object(
        self,
        target_id: str,
        object_id: str,
    ) -> Optional[TraySlot]:
        """Find a single placement slot for an object.
        
        Convenience method for the common case of placing one object.
        """
        placed_objects = self.get_placed_objects(target_id)
        slots = self.generate_candidate_slots(target_id, object_id, placed_objects)
        
        if slots:
            return slots[0]
        return None
    
    def get_capacity(self, target_id: str, object_half_heights: list[float]) -> int:
        """Estimate maximum capacity of a tray for given object sizes.
        
        Returns:
            Maximum number of objects that can fit (conservative estimate)
        """
        center_xy, inner_half, floor_top = self.get_tray_interior(target_id)
        
        # Available area (square minus margins)
        available_side = (inner_half - self.TRAY_MARGIN_M) * 2
        
        # Each object needs diameter + clearance
        if not object_half_heights:
            return 0
        
        avg_half = np.mean(object_half_heights)
        cell_size = avg_half * 2 + self.DEFAULT_CLEARANCE_M * 2
        
        # Grid estimate
        slots_per_side = max(1, int(available_side / cell_size))
        return slots_per_side * slots_per_side
