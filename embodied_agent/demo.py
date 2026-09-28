from __future__ import annotations

import json

from .agent import EmbodiedAgent
from .models import Item, TargetZone
from .simulator import TabletopSimulator


def main() -> None:
    simulator = TabletopSimulator(
        items=[
            Item("red_cube", "red", (0.1, 0.1)),
            Item("blue_cylinder", "blue", (0.3, 0.1)),
            Item("green_triangle", "green", (0.5, 0.1)),
        ],
        zones=[
            TargetZone("red_bin", "red", (0.1, 0.8)),
            TargetZone("blue_bin", "blue", (0.3, 0.8)),
            TargetZone("green_bin", "green", (0.5, 0.8)),
        ],
        fail_once_on={"place:blue_cylinder"},
    )
    agent = EmbodiedAgent(simulator)
    task = "整理桌面：red_cube to red_bin, blue_cylinder to blue_bin, green_triangle to green_bin。每一步完成后验证。"
    print(json.dumps(agent.run(task), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

