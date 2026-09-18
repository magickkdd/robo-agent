"""TabletopOrganize-v1 task set (spec 12.1).

A self-built engineering eval set, NOT a public benchmark. Task cases carry:
natural-language utterance, independent EvalSpec (authored without model
involvement), initial layout + seed, optional frozen fault config, budgets.

Sets: smoke(5) / dev(20) / s1_clean(90: 3 configs x10 x3 runs) / s1_fault(30x3,
paired recovery on/off) / negative(>=10).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.contracts import Budgets, EvalSpec

TRAYS = ["tray_left", "tray_middle", "tray_right"]
COLORS = ["red", "green", "blue", "yellow", "purple"]
COLOR_ZH = {"red": "红色", "green": "绿色", "blue": "蓝色", "yellow": "黄色", "purple": "紫色"}
SHAPES = ["cube", "cuboid", "cylinder"]
SHAPE_ZH = {"cube": "方块", "cuboid": "长方体", "cylinder": "圆柱"}
TARGET_ZH = {"tray_left": "左边托盘", "tray_middle": "中间托盘", "tray_right": "右边托盘"}

# initial placement slots: inside reach (verified base_z=0.30), pairwise
# non-overlapping, none inside a tray footprint (trays occupy x>=0.49,
# |y|<=0.43 bands)
SLOTS = [
    (0.38, -0.16), (0.38, 0.0), (0.38, 0.16),
    (0.445, -0.16), (0.445, 0.0), (0.445, 0.16),
]


@dataclass
class FaultConfig:
    type: str  # "grasp_offset"
    offset: tuple[float, float, float]  # frozen; calibrated on dev, then fixed


@dataclass
class TaskCase:
    task_id: str
    utterance: str
    seed: int
    objects: list[dict]  # entity_id/shape/dims/xy/color/attributes
    eval_spec: EvalSpec
    fault: FaultConfig | None = None
    expected: str = "success"  # success | needs_clarification | plan_rejected
    set_name: str = "smoke"
    notes: str = ""


def make_objects(n: int, seed: int, start_index: int = 0) -> list[dict]:
    import numpy as np

    rng = np.random.default_rng(seed)
    color_cycle = COLORS[(seed + start_index) % len(COLORS):] + COLORS[: (seed + start_index) % len(COLORS)]
    objs = []
    for i in range(n):
        shape = SHAPES[(i + seed) % len(SHAPES)]
        color = color_cycle[i]
        xy = SLOTS[(i + seed) % len(SLOTS)]
        if shape == "cylinder":
            dims = [0.023, 0.042]
        elif shape == "cuboid":
            dims = [0.027, 0.019, 0.035]
        else:
            dims = [0.021, 0.021, 0.021]
        objs.append(
            {
                "entity_id": f"obj_{color}_{i + 1}",
                "shape": shape,
                "dims": dims,
                "xy": list(xy),
                "attributes": {"color": color, "shape": shape},
            }
        )
    return objs


def rgb_colors(objs: list[dict]) -> list[dict]:
    palette = {
        "red": [0.85, 0.1, 0.1, 1], "green": [0.1, 0.7, 0.2, 1], "blue": [0.1, 0.2, 0.85, 1],
        "yellow": [0.9, 0.8, 0.1, 1], "purple": [0.5, 0.2, 0.7, 1],
    }
    for o in objs:
        o.setdefault("color", palette[o["attributes"]["color"]])
    return objs


def build_eval_spec(objs: list[dict], assignment: dict[str, str], seed: int) -> EvalSpec:
    return EvalSpec(
        eval_id=f"eval_{seed}",
        assignments=[{"entity_id": o["entity_id"], "target_id": assignment[o["entity_id"]]} for o in objs],
    )


def explicit_utterance(objs: list[dict], assignment: dict[str, str]) -> str:
    parts = [
        f"把{COLOR_ZH[o['attributes']['color']]}的{SHAPE_ZH[o['attributes']['shape']]}放到{TARGET_ZH[assignment[o['entity_id']]]}"
        for o in objs
    ]
    return "，".join(parts) + "。"


def class_utterance(objs: list[dict], assignment: dict[str, str]) -> str:
    """Attribute-class style: colour only + a rest clause (spec 4.2 example 2)."""
    parts = [
        f"把{COLOR_ZH[o['attributes']['color']]}物体放入{TARGET_ZH[assignment[o['entity_id']]]}"
        for o in objs[:-1]
    ]
    last_target = assignment[objs[-1]["entity_id"]]
    parts.append(f"其余放到{TARGET_ZH[last_target]}")
    return "，".join(parts) + "。"


def assign_targets(n: int, seed: int, objs: list[dict]) -> dict[str, str]:
    import numpy as np

    rng = np.random.default_rng(seed + 1000)
    return {o["entity_id"]: TRAYS[int(rng.integers(0, 3))] for o in objs}


def _case(n_objects: int, seed: int, style: str, set_name: str, fault: FaultConfig | None = None) -> TaskCase:
    objs = rgb_colors(make_objects(n_objects, seed))
    assignment = assign_targets(n_objects, seed, objs)
    utt = explicit_utterance(objs, assignment) if style == "explicit" else class_utterance(objs, assignment)
    return TaskCase(
        task_id=f"{set_name}_{n_objects}obj_s{seed}",
        utterance=utt,
        seed=seed,
        objects=objs,
        eval_spec=build_eval_spec(objs, assignment, seed),
        fault=fault,
        set_name=set_name,
    )


def smoke_set() -> list[TaskCase]:
    """5 readable cases: normal, 3/5 objects, one grasp miss, illegal goal."""
    c0 = _case(3, 7, "explicit", "smoke")
    c1 = _case(3, 11, "class", "smoke")
    c2 = _case(5, 13, "explicit", "smoke")
    c3 = _case(3, 17, "explicit", "smoke", fault=FaultConfig("grasp_offset", (-0.07, 0.0, 0.0)))
    c4 = TaskCase(
        task_id="smoke_illegal_target",
        utterance="把红色的方块放到抽屉里。",
        seed=21,
        objects=rgb_colors(make_objects(1, 21)),
        eval_spec=EvalSpec(eval_id="eval_negative_smoke", assignments=[]),
        expected="needs_clarification",
        set_name="smoke",
        notes="unknown target: rejected at interpretation, never executed (A4)",
    )
    return [c0, c1, c2, c3, c4]


def dev_set(n: int = 20) -> list[TaskCase]:
    return [_case(3 + (i % 3), 100 + i, "explicit" if i % 2 == 0 else "class", "dev") for i in range(n)]


def s1_clean_set() -> list[TaskCase]:
    """3/4/5 objects x 10 pre-fixed configs x 3 independent runs = 90 episodes."""
    cases = []
    for n in (3, 4, 5):
        for cfg in range(10):
            for run in range(3):
                cases.append(_case(n, 500 + cfg * 10 + run, "explicit" if cfg % 2 == 0 else "class", "s1_clean"))
    return cases


def s1_fault_set(n_configs: int = 30) -> list[TaskCase]:
    """Paired fault configs; runner executes each with recovery on AND off."""
    import numpy as np

    cases = []
    for cfg in range(n_configs):
        rng = np.random.default_rng(9000 + cfg)
        # frozen offsets calibrated on dev then fixed for acceptance: magnitudes
        # 60-85mm in directions that keep the shifted grasp point reachable
        # (towards the robot or sideways), so the physics produces a clean
        # grasp miss instead of an unreachable-target artefact
        dirs = [(-1.0, 0.0), (0.0, 1.0), (0.0, -1.0), (-0.707, 0.707), (-0.707, -0.707)]
        dx, dy = dirs[cfg % len(dirs)]
        mag = float(rng.uniform(0.06, 0.085))
        fault = FaultConfig("grasp_offset", (mag * dx, mag * dy, 0.0))
        n = 3 + cfg % 3
        cases.append(_case(n, 700 + cfg, "explicit", "s1_fault", fault=fault))
    return cases


def negative_set() -> list[TaskCase]:
    """>=10 boundary cases: must terminate/clarify within budget; illegal
    actions must never execute."""
    objs3 = [
        {"entity_id": "obj_red_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.44, 0.0], "color": [0.85, 0.1, 0.1, 1], "attributes": {"color": "red", "shape": "cube"}},
        {"entity_id": "obj_green_2", "shape": "cuboid", "dims": [0.027, 0.019, 0.035], "xy": [0.44, 0.16], "color": [0.1, 0.7, 0.2, 1], "attributes": {"color": "green", "shape": "cuboid"}},
        {"entity_id": "obj_blue_3", "shape": "cylinder", "dims": [0.023, 0.042], "xy": [0.44, -0.16], "color": [0.1, 0.2, 0.85, 1], "attributes": {"color": "blue", "shape": "cylinder"}},
    ]
    cases: list[TaskCase] = []
    empty = EvalSpec(eval_id="neg_empty", assignments=[])
    base = dict(seed=77, objects=objs3, eval_spec=empty, set_name="negative")

    def add(tid, utt, expected, notes="", objs=None, spec=None):
        cases.append(
            TaskCase(
                task_id=tid, utterance=utt, seed=77,
                objects=objs if objs is not None else objs3,
                eval_spec=spec if spec is not None else empty,
                expected=expected, set_name="negative", notes=notes,
            )
        )

    add("neg_ambiguous_demo", "把那个东西收好。", "needs_clarification", "no unique referent (spec 4.2)")
    add("neg_unknown_color", "把紫色物体放入左边托盘。", "needs_clarification", "no purple entity")
    add("neg_unknown_target", "把红色物体放到抽屉里。", "needs_clarification", "unknown target region")
    add("neg_empty_goal", "开始吧。", "needs_clarification", "no assignment at all")
    add("neg_conflicting_attr", "把红色的圆柱放入左边托盘。", "needs_clarification", "no red cylinder in seed layout", spec=empty)
    add("neg_unknown_id", "把 obj_ghost_1 放入左边托盘。", "needs_clarification", "nonexistent entity id")
    red_obj = [o for o in objs3 if o["attributes"]["color"] == "red"][0]
    add(
        "neg_both_duplicate", "把红色物体放入左边托盘，红色物体放入右边托盘。",
        "success", "duplicate binds same object; first valid target wins, second is a no-op by predicates",
        spec=EvalSpec(eval_id="neg_7701", assignments=[{"entity_id": red_obj["entity_id"], "target_id": "tray_left"}]),
    )
    add(
        "neg_overspecified", "把红色方块、绿色长方体、蓝色圆柱全部放入左边托盘。",
        "success", "all-to-one is legal: tray holds multiple objects",
        spec=EvalSpec(eval_id="neg_7702", assignments=[{"entity_id": o["entity_id"], "target_id": "tray_left"} for o in objs3]),
    )
    add("neg_wrong_attr_shape", "把蓝色的方块放入中间托盘。", "needs_clarification", "blue object is a cylinder, not a cube", spec=empty)
    add("neg_restack_demand", "把红色方块放到绿色长方体上面。", "needs_clarification", "stacking out of S1 scope (spec 4.1)")
    # target-no-space: five objects to one tray -> only 3 safe slots -> TARGET_FULL, bounded
    objs5 = rgb_colors(make_objects(5, 78))
    utt5 = "，".join(
        f"把{COLOR_ZH[o['attributes']['color']]}物体放入左边托盘" for o in objs5
    ) + "。"
    add(
        "neg_target_no_space", utt5, "bounded_failure",
        "5 objects to one tray exceeds safe slot capacity: TARGET_FULL, bounded exit, never blind-place",
        objs=objs5,
        spec=EvalSpec(eval_id="neg_7703", assignments=[{"entity_id": o["entity_id"], "target_id": "tray_left"} for o in objs5]),
    )
    # budget exhaustion: skill-call budget too small for 3 objects -> deterministic BUDGET_EXHAUSTED
    tight = EvalSpec(eval_id="neg_7704", assignments=[
        {"entity_id": "obj_red_1", "target_id": "tray_left"},
        {"entity_id": "obj_green_2", "target_id": "tray_middle"},
        {"entity_id": "obj_blue_3", "target_id": "tray_right"},
    ], budgets=Budgets(max_skill_calls=4, max_global_replans=2))
    add("neg_budget_exhausted", "把红色物体放入左边托盘，把绿色物体放入中间托盘，把蓝色物体放入右边托盘。",
        "bounded_failure", "max_skill_calls=4 < required 6: budget exhaustion must terminate boundedly",
        spec=tight)
    return cases


def build_set(name: str) -> list[TaskCase]:
    return {
        "smoke": smoke_set,
        "dev": dev_set,
        "s1_clean": s1_clean_set,
        "s1_fault": s1_fault_set,
        "negative": negative_set,
    }[name]()


def task_case_from_yaml(path: str) -> TaskCase:
    """Load a task definition YAML (spec 16 format) into a TaskCase."""
    import yaml

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    palette = {
        "red": [0.85, 0.1, 0.1, 1], "green": [0.1, 0.7, 0.2, 1], "blue": [0.1, 0.2, 0.85, 1],
        "yellow": [0.9, 0.8, 0.1, 1], "purple": [0.5, 0.2, 0.7, 1],
    }
    shape_map = {"box": "cube", "cuboid": "cuboid", "cylinder": "cylinder"}
    objs = []
    for i, o in enumerate(cfg["scene"]["objects"]):
        shape = shape_map[o["shape"]]
        if shape == "cylinder":
            dims = [0.023, 0.042]
        elif shape == "cuboid":
            dims = [0.027, 0.019, 0.035]
        else:
            dims = [0.021, 0.021, 0.021]
        objs.append({
            "entity_id": o["id"], "shape": shape, "dims": dims,
            "xy": list(SLOTS[(i + cfg["seed"]) % len(SLOTS)]),
            "color": palette[o["color"]],
            "attributes": {"color": o["color"], "shape": o["attributes"]["shape"] if "attributes" in o and "shape" in o["attributes"] else shape},
        })
    assignments = [
        {"entity_id": eid, "target_id": tid}
        for eid, tid in cfg["evaluation"]["assignments"].items()
    ]
    faults = []
    for flt in cfg.get("faults", []):
        if flt.get("type") == "grasp_offset":
            faults.append(FaultConfig("grasp_offset", tuple(flt["offset"])))
    return TaskCase(
        task_id=cfg["task_id"],
        utterance=cfg["instruction"],
        seed=cfg["seed"],
        objects=objs,
        eval_spec=EvalSpec(eval_id=f"eval_{cfg['task_id']}", assignments=assignments,
                           settle_time_s=cfg.get("evaluation", {}).get("stable_time_s", 0.5)),
        fault=faults[0] if faults else None,
        set_name="yaml",
    )
