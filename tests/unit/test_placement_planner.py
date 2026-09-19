"""Placement candidates on the real scene geometry (SPEC 5.5).

Every claim tested here is one the experiment's placements rest on, and every
number comes from the built PyBullet world — no hand-written `inner_half`, no
mock tray. A mock that copies the constant it is checking proves only that the
test and the code were written in the same minute.

The checks split the way the contract splits: a candidate is *geometry* first
(identity, footprint, seating height) and *claims* second (boundary, occupancy,
reachability), and an unchecked claim must never read as a proven one.
"""
import math

import pytest

from embodied_agent.core.contracts import CheckVerdict, PlacementCandidate
from embodied_agent.core.placement_planner import (
    CandidateRegistry,
    PlacementPlanner,
    candidate_id_for,
)
from embodied_agent.core.verify import build_world_state
from embodied_agent.evaluation.tasks import find_case


@pytest.fixture(scope="module")
def env():
    """One real scene + one real measured snapshot, shared by the read-only tests."""
    scene = None
    try:
        scene = _build()
        case = find_case("smoke_clean")
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        yield scene, world, PlacementPlanner(scene, case.verify)
    finally:
        if scene is not None:
            scene.close()


def _build():
    from embodied_agent.core.scene import PhysicsScene

    case = find_case("smoke_clean")
    return PhysicsScene(seed=case.seed, object_layout=case.objects)


def objs(scene, world):
    return [e.entity_id for e in world.entities if e.attributes.get("shape") == "cube"]


# ------------------------------------------------------------ identity ------


def test_candidate_id_names_the_geometry_not_a_slot_index(env):
    scene, world, planner = env
    oid = objs(scene, world)[0]
    a = planner._make("tray_left", oid, 0.66, 0.0, world)
    b = planner._make("tray_left", oid, 0.68, 0.0, world)
    assert a.candidate_id != b.candidate_id
    assert a.candidate_id == candidate_id_for("tray_left", oid, 0.66, 0.0)
    assert "x660y0" in a.candidate_id


def test_regenerating_the_same_option_yields_the_same_id(env):
    scene, world, planner = env
    oid = objs(scene, world)[0]
    first = planner.generate("tray_left", oid, world, limit=4)
    again = planner.generate("tray_left", oid, world, limit=4)
    assert [c.candidate_id for c in first] == [c.candidate_id for c in again]
    assert len({c.candidate_id for c in first}) == len(first)


def test_an_id_never_re_points_at_other_geometry(env):
    """The registry refuses the swap rather than logging a lie (SPEC 5.5)."""
    scene, world, planner = env
    oid = objs(scene, world)[0]
    cand = planner._make("tray_left", oid, 0.66, 0.0, world)
    impostor = cand.model_copy(update={"position_xy": (0.70, 0.04)})
    with pytest.raises(ValueError, match="would name a different geometry"):
        planner.registry.remember(impostor)


def test_resolve_only_answers_for_ids_ever_offered(env):
    scene, world, planner = env
    oid = objs(scene, world)[0]
    cand = planner.generate("tray_left", oid, world, limit=1)[0]
    assert planner.resolve(cand.candidate_id) is cand
    assert planner.resolve("cand-tray_left-%s-x99y99" % oid) is None


# ------------------------------------------------------------- geometry -----


def test_circumscribed_radius_is_the_upright_yaw_bound(env):
    """A cylinder claims its radius, a box its diagonal — the horizontal bound an
    upright body sweeps, never the vertical half-height it stands at."""
    scene, world, planner = env
    for e in world.entities:
        geom = scene.geometry(e.entity_id)
        got = planner.circumscribed_radius(e.entity_id)
        if geom.shape == "cylinder":
            assert got == pytest.approx(geom.radius)
            assert got != pytest.approx(geom.half_h_vertical), "the vertical size leaked in"
        else:
            assert got == pytest.approx(math.hypot(geom.half_extents.x, geom.half_extents.y))


def test_rest_and_hand_off_height_are_the_same_definition_the_descent_uses(env):
    scene, world, planner = env
    oid = objs(scene, world)[0]
    tray = scene.trays["tray_left"]
    rest = planner.rest_z(oid, "tray_left")
    assert rest == pytest.approx(tray["floor_top"] + scene.geometry(oid).half_h_vertical)
    assert planner.hand_off_z(oid, "tray_left") == pytest.approx(
        rest + PlacementPlanner.HAND_OFF_CLEARANCE_M)


def test_generated_candidates_all_fit_the_region_they_are_offered_in(env):
    scene, world, planner = env
    tray = scene.trays["tray_left"]
    limit = tray["inner_half"] - planner.config.footprint_margin_m
    cx, cy = tray["center"]
    got = planner.generate("tray_left", objs(scene, world)[0], world, limit=None)
    assert got, "a real tray with a real cube must offer at least one slot"
    for c in got:
        r = planner.circumscribed_radius(c.entity_id)
        assert abs(c.position_xy[0] - cx) + r <= limit + 1e-9
        assert abs(c.position_xy[1] - cy) + r <= limit + 1e-9
        assert c.position_xy == tuple(round(v, 4) for v in c.position_xy)
        assert c.generated_at_state_version == world.state_version
        # an offered candidate carries its claims as *unchecked* until re-checked
        assert c.boundary_ok == CheckVerdict.unchecked
        assert c.occupancy_ok == CheckVerdict.unchecked


def test_generation_is_ordered_from_the_tray_centre_outwards(env):
    scene, world, planner = env
    cx, cy = scene.trays["tray_left"]["center"][:2]
    got = planner.generate("tray_left", objs(scene, world)[0], world, limit=6)
    dists = [math.hypot(c.position_xy[0] - cx, c.position_xy[1] - cy) for c in got]
    assert dists == sorted(dists)


def test_a_target_with_no_room_offers_nothing_rather_than_a_lie(env):
    scene, world, planner = env
    assert planner.generate("tray_nowhere", objs(scene, world)[0], world) == []
    assert planner.generate("tray_left", "obj_ghost_1", world) == []


# --------------------------------------------------------------- claims -----


def test_recheck_reports_each_dimension_separately(env):
    scene, world, planner = env
    oid = objs(scene, world)[0]
    cand = planner.generate("tray_left", oid, world, limit=1)[0]
    chk = planner.recheck(cand, world)
    assert chk.ok, chk.reasons
    checked = chk.candidate
    for field in ("params_ok", "boundary_ok", "occupancy_ok", "reachability_ok"):
        assert getattr(checked, field) == CheckVerdict.ok, field
    assert checked.checked_at_state_version == world.state_version
    # the stored candidate keeps its original claim: the record of what was
    # offered must not be rewritten by a later check
    assert cand.boundary_ok == CheckVerdict.unchecked


def test_a_slot_across_the_wall_is_a_boundary_failure_not_an_option(env):
    scene, world, planner = env
    oid = objs(scene, world)[0]
    tray = scene.trays["tray_left"]
    r = planner.circumscribed_radius(oid)
    too_far = planner._make("tray_left", oid, tray["center"][0] + tray["inner_half"] + r,
                            tray["center"][1], world)
    chk = planner.recheck(too_far, world)
    assert not chk.ok
    assert chk.candidate.boundary_ok == CheckVerdict.fail
    assert any("wall or margin" in m for m in chk.reasons), chk.reasons


def test_hand_sweep_envelope_is_wider_than_the_footprint_rule(env):
    """The descent clears more than the resting footprint needs.

    Measured in work/p0_sweep.py: 65 mm centre-to-centre from a standing cylinder
    shoved it 69-94 mm and tipped it 19 mm over; 75 mm left the neighbour moved
    <= 8 mm and never tipped. Two footprints may legally touch closer than that,
    so the two rules must disagree in this direction — if they ever agreed, a
    placement would be destroying one the episode had already made true.

    What the *physics* does to a real neighbour is tested with a real placement in
    `test_place_hand_sweep.py`; this is the rule that reads a snapshot.
    """
    scene, world, planner = env
    cyl = next(e.entity_id for e in world.entities if e.attributes.get("shape") == "cylinder")
    cube = next(e.entity_id for e in world.entities if e.attributes.get("shape") == "cube")
    r_cyl, r_cube = planner.circumscribed_radius(cyl), planner.circumscribed_radius(cube)
    footprints_touch = r_cyl + r_cube + planner.CLEARANCE_M
    sweep_needs = r_cyl + PlacementPlanner.HAND_SWEEP_RADIUS_M
    assert footprints_touch < sweep_needs, "the envelope rule has collapsed into the footprint rule"

    cx, cy = scene.trays["tray_left"]["center"][:2]
    crowded = _occupying(world, scene, cyl, "tray_left", (cx, cy))
    inside = planner._make("tray_left", cube, cx + sweep_needs - 0.010, cy, crowded)
    chk = planner.recheck(inside, crowded)
    assert not chk.ok and chk.candidate.occupancy_ok == CheckVerdict.fail
    assert not any("footprint crosses" in m for m in chk.reasons), \
        "the slot is legal geometry; only the hand's envelope rules it out"
    assert any("descent envelope" in m and cyl in m for m in chk.reasons), chk.reasons

    outside = planner._make("tray_left", cube, cx + sweep_needs + 0.005, cy, crowded)
    chk_out = planner.recheck(outside, crowded)
    assert chk_out.candidate.occupancy_ok == CheckVerdict.ok, chk_out.reasons


def _occupying(world, scene, who, tray_id, xy):
    """A copy of the measured snapshot in which `who` rests at `xy` in `tray_id`.

    Only positions are rewritten; geometry, ids, region facts and the state
    version stay those of the real snapshot, so what the rule under test reads is
    the same shape of evidence the runtime hands it."""
    from embodied_agent.core.contracts import OccupancyRecord, Vec3

    ent = world.entity(who)
    rest_z = scene.trays[tray_id]["floor_top"] + ent.geometry.half_h_vertical
    moved = ent.model_copy(update={"pose": ent.pose.model_copy(
        update={"position": Vec3(x=xy[0], y=xy[1], z=rest_z)}), "supported_by": tray_id})
    ents = [moved if e.entity_id == who else e for e in world.entities]
    occ = [o for o in world.occupancy if o.entity_id != who] + [OccupancyRecord(
        target_id=tray_id, entity_id=who, rest_xy=(xy[0], xy[1]),
        footprint_half_xy=tuple(round(v, 4) for v in scene.footprint_half_xy(who)),
        fully_inside=True)]
    return world.model_copy(update={"entities": ents, "occupancy": occ})


def test_best_feasible_searches_the_whole_lattice_not_a_sample(env):
    """`generate(limit=8)` is a prompt budget; answering "no slot is free" from it
    would hand both modes a fake TARGET_NO_FREE_SLOT."""
    scene, world, planner = env
    oid = objs(scene, world)[0]
    cand, notes = planner.best_feasible("tray_left", oid, world)
    assert cand is not None and cand.candidate_id
    full = planner.generate("tray_left", oid, world, limit=None)
    assert len(full) > 8, "this test needs a lattice larger than the prompt sample"


def test_registry_is_per_executor_so_two_episodes_do_not_share_ids(env):
    scene, world, planner = env
    other = PlacementPlanner(scene, planner.config, registry=CandidateRegistry())
    oid = objs(scene, world)[0]
    cand = planner.generate("tray_left", oid, world, limit=1)[0]
    assert other.resolve(cand.candidate_id) is None
