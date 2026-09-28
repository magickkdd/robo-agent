"""What the cell declares about itself — the percept's calibration (SPEC-v0.2 §5.1).

A vision system is not given the world; it is given a *sensor*, some optics and a
manual. The manual is this file: the vocabulary of colours and shapes this cell can
contain, how big each shape is, and where the target regions are. Every one of those
facts is the same in every episode of the benchmark, which is what makes it
calibration rather than an answer.

What is deliberately **not** here is anything per-episode: which objects were placed
where, which entity id carries which colour, what is held, what has moved. A percept
has to earn all of that from the picture, and the P1-f contrast is the measurement of
how often it does.

The catalog is passed *in* by the caller (the runtime, from the task declaration).
This module never reaches for a scene object, so the information-flow rule holds by
construction instead of by a reviewer's memory.
"""
from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from ..core.contracts import GeometrySpec, StrictModel, TargetRegion, Vec3

# The visual shape words of this cell, mapped to the collision geometry the same
# word names. `cube` and `cuboid` are one collision shape and two *appearances*,
# which is exactly the distinction a privileged snapshot never has to make and a
# percept always does.
SHAPE_WORDS = ("cube", "cuboid", "cylinder")


def geometry_for(shape_word: str, dims: list[float]) -> GeometrySpec:
    """Declared half-extents, in the same convention `PhysicsScene._make_object`
    builds the collision shape with: `[hx, hy, hz]` for a box, `[radius, half_h]`
    for a cylinder."""
    if shape_word == "cylinder":
        return GeometrySpec(shape="cylinder", radius=float(dims[0]), half_h=float(dims[1]))
    if len(dims) != 3:
        raise ValueError(f"{shape_word} geometry needs three half-extents, got {dims}")
    return GeometrySpec(shape="box",
                        half_extents=Vec3(x=float(dims[0]), y=float(dims[1]), z=float(dims[2])))


class RegionDecl(StrictModel):
    """One target region as the cell describes it: where it is and what it is called.

    `label` is the word a *person* uses for it (`TARGET_ZH` in the task list), so a
    percept can carry "the red cube is in the left tray" without ever learning the
    id `tray_left` from anywhere but this declaration.

    `capacity` is how many objects the cell may rest in it without one spoiling
    another's validity. It is calibration because it is *measured once* and is the
    same in every episode (`_respect_region_capacity` refuses to author a task that
    exceeds it), and it is a declaration rather than a law: a percept that counts a
    fourth occupant says so instead of dropping the detection.
    """

    target_id: str
    label: str = ""
    center_xy: tuple[float, float]
    inner_half: float
    floor_top_z: float
    wall_top_z: float
    capacity: int = 0
    accepts: list[str] = Field(default_factory=list)

    def as_region(self) -> TargetRegion:
        return TargetRegion(target_id=self.target_id, label=self.label or self.target_id,
                            center=Vec3(x=self.center_xy[0], y=self.center_xy[1],
                                        z=self.floor_top_z),
                            inner_half=self.inner_half, floor_top_z=self.floor_top_z,
                            wall_top_z=self.wall_top_z)

    def contains_xy(self, x: float, y: float, *, margin_m: float = 0.0) -> bool:
        """Whether a measured point is inside the *free interior* of this region.

        `inner_half` is the interior half-width the wall actually leaves (the scene
        measures it, it is not the nominal mould size), so this is the same test the
        verifier applies to a placement — a percept and a verdict must not disagree
        about what "inside the tray" means."""
        dx, dy = float(x) - self.center_xy[0], float(y) - self.center_xy[1]
        return abs(dx) <= self.inner_half - margin_m and abs(dy) <= self.inner_half - margin_m

    def capacity_note(self) -> str:
        if not self.capacity:
            return "capacity not declared for this region"
        return (f"declared capacity {self.capacity} (measured slot floor, work/p0_capacity.py; "
                f"interior half {self.inner_half:.3f} m)")

    def named(self, word: str) -> bool:
        """Whether a model's word refers to this region, accepting either the label
        or the id, in either language the declaration offers."""
        w = word.strip().lower()
        return w in {self.target_id.lower(), self.label.lower(), word.strip()} or w == self.label


class PerceptionCatalog(StrictModel):
    """The static manual a perceiver is allowed to consult."""

    version: str = "calib-v1"
    colours: dict[str, tuple[float, float, float]] = Field(default_factory=dict)
    shapes: dict[str, list[float]] = Field(default_factory=dict)
    regions: list[RegionDecl] = Field(default_factory=list)
    # the surface words a person uses for each attribute word, in either direction.
    # A model answers "红色方块"; `attributes_for_word` is the only thing that turns
    # that into {color: red, shape: cube}, so the mapping is declared once, here,
    # instead of being guessed by whoever assembles a percept.
    colour_words: dict[str, str] = Field(default_factory=dict)
    shape_words: dict[str, str] = Field(default_factory=dict)
    # the height a free-standing object's support surface must be near for the
    # geometry channel to accept a sample at all; outside it, the percept says unknown
    table_top_z: float = 0.0
    support_z_bounds: tuple[float, float] = (0.0, 0.0)
    support_z_tol_m: float = 0.012

    @model_validator(mode="after")
    def _check(self):
        for word, dims in self.shapes.items():
            geometry_for(word, dims)          # fail at construction, not mid-episode
        if not self.colours:
            raise ValueError("a catalog with no colours cannot ground a percept")
        if not self.regions:
            raise ValueError("a catalog with no regions has nowhere to put anything")
        ids = [r.target_id for r in self.regions]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate target_id in one catalog: {ids}")
        return self

    @classmethod
    def from_declared(cls, *, palette: dict[str, list[float]], dims: dict[str, list[float]],
                      trays: list[dict], table_top_z: float,
                      labels: Optional[dict[str, str]] = None,
                      colour_words: Optional[dict[str, str]] = None,
                      shape_words: Optional[dict[str, str]] = None,
                      capacity: int = 0,
                      version: str = "calib-v1") -> "PerceptionCatalog":
        """Build the manual from the task list's own declarations.

        `trays` is the list of *static* tray dictionaries the scene was built from —
        positions and sizes, which are the same in every episode. Nothing in it names
        an object: which entity sits in which tray is what a percept has to find out,
        and it is not in here."""
        regions = [RegionDecl(target_id=t["target_id"],
                              label=(labels or {}).get(t["target_id"], t["target_id"]),
                              center_xy=(float(t["center"][0]), float(t["center"][1])),
                              inner_half=float(t["inner_half"]),
                              floor_top_z=float(t["floor_top"]),
                              wall_top_z=float(t["wall_top"]),
                              capacity=int(t.get("capacity", capacity)),
                              accepts=[str(a) for a in t.get("accepts", [])])
                   for t in trays]
        return cls(
            version=version,
            colours={name: (float(v[0]), float(v[1]), float(v[2]))
                     for name, v in palette.items() if len(v) >= 3},
            shapes={name: [float(v) for v in d] for name, d in dims.items()},
            regions=regions, table_top_z=float(table_top_z),
            colour_words=dict(colour_words or {}), shape_words=dict(shape_words or {}),
            support_z_bounds=(float(table_top_z) - 0.06, float(table_top_z) + 0.12))

    # ---------- lookups a perceiver needs ----------
    def geometry(self, shape_word: str) -> GeometrySpec:
        if shape_word not in self.shapes:
            raise ValueError(f"shape {shape_word!r} is not in this cell's catalog "
                             f"({sorted(self.shapes)})")
        return geometry_for(shape_word, self.shapes[shape_word])

    def half_height(self, shape_word: str) -> float:
        """How far the centre sits above whatever it rests on, *upright*.

        Upright is the assumption a plan makes before the object is anywhere; the
        measured orientation is what verification uses later (§5.1)."""
        geom = self.geometry(shape_word)
        return geom.half_h_vertical

    def region(self, target_id: str) -> Optional[RegionDecl]:
        return next((r for r in self.regions if r.target_id == target_id), None)

    def region_by_word(self, word: str) -> Optional[RegionDecl]:
        return next((r for r in self.regions if r.named(word)), None)

    def region_containing(self, x: float, y: float, *, margin_m: float = 0.0) -> Optional[RegionDecl]:
        """Which declared region this measured point falls in, if any.

        `next(...)` rather than a first-match shortcut over overlaps: the regions are
        declared non-overlapping, and if that ever stops being true the caller gets
        the lowest-id one, which is a fact to fix in the declaration, not a tie to
        hide here."""
        return next((r for r in self.regions if r.contains_xy(x, y, margin_m=margin_m)), None)

    def known_attribute(self, key: str, value: str) -> bool:
        if key == "color":
            return value in self.colours
        if key == "shape":
            return value in self.shapes
        return False

    def attributes_for_word(self, word: str) -> dict[str, str]:
        """Resolve a surface expression like `红色方块` / `red cube` to attributes.

        Partial on purpose: `红色` alone resolves to `{"color": "red"}` and the caller
        decides whether a partial binding is enough. A word that matches nothing in
        the declared vocabulary returns `{}` — an invented attribute name is not the
        percept's fault to explain, and §5.1's uncertainty channel is where it goes."""
        text = str(word or "").strip().lower()
        found: dict[str, str] = {}
        for en, zh in self.colour_words.items():
            if en.lower() in text or str(zh) in str(word):
                found["color"] = en
        for en, zh in self.shape_words.items():
            if en.lower() in text or str(zh) in str(word):
                found["shape"] = en
        return found

    def word_for_attributes(self, attributes: dict[str, str]) -> str:
        """The reverse direction: how a person would name this detection."""
        colour = self.colour_words.get(attributes.get("color", ""), attributes.get("color", ""))
        shape = self.shape_words.get(attributes.get("shape", ""), attributes.get("shape", ""))
        return f"{colour}{shape}".strip()

    # ---------- what the model is told ----------
    def prompt_context(self) -> str:
        """The closed vocabulary and the region names, as text.

        The model may only use these words, which is what makes its answer checkable:
        an attribute outside the list is a parse failure to be reported, not a new
        kind of object to be invented."""
        colours = "、".join(f"{w}({self.colour_words.get(w, '')})" for w in sorted(self.colours))
        shapes = "、".join(f"{w}(尺寸 {self.shapes[w]}，{self.shape_words.get(w, '')})"
                          for w in sorted(self.shapes))
        regions = "、".join(f"{r.target_id}(称呼「{r.label}」，中心 x={r.center_xy[0]:.3f} "
                          f"y={r.center_xy[1]:.3f}，半宽 {r.inner_half:.3f} m，底面高 "
                          f"{r.floor_top_z:.3f} m)" for r in self.regions)
        return (f"这个单元格只会出现以下属性词。\ncolor 取值：{colours}。\n"
                f"shape 取值：{shapes}。\n区域（id=称呼）：{regions}。\n"
                f"桌面高度 {self.table_top_z:.3f} m；托盘底面比桌面高，托盘内最多放 "
                f"{self.regions[0].capacity or '若干'} 个物体。")

    def sha256(self) -> str:
        """Digest of the calibration a percept was assembled against (§7: 版本入清单)."""
        import hashlib

        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()
