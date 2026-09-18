import unittest

from embodied_agent.agent import EmbodiedAgent
from embodied_agent.models import Item, TargetZone
from embodied_agent.simulator import TabletopSimulator


def make_sim(fail=False):
    return TabletopSimulator(
        [Item("red_cube", "red", (0, 0)), Item("blue_cylinder", "blue", (1, 0))],
        [TargetZone("red_bin", "red", (0, 1)), TargetZone("blue_bin", "blue", (1, 1))],
        {"place:blue_cylinder"} if fail else set(),
    )


class MvpTests(unittest.TestCase):
    def test_recovery_completes_multi_object_task(self):
        agent = EmbodiedAgent(make_sim(fail=True))
        result = agent.run("red_cube to red_bin, blue_cylinder to blue_bin")
        self.assertTrue(result["success"])
        self.assertTrue(any("injected placement failure" in f for f in result["failures"]))
        self.assertIn("verify", result["actions"])

    def test_baseline_without_recovery_exposes_failure(self):
        agent = EmbodiedAgent(make_sim(fail=True), max_retries=0)
        result = agent.run("red_cube to red_bin, blue_cylinder to blue_bin")
        self.assertFalse(result["success"])

    def test_verification_rejects_wrong_target(self):
        agent = EmbodiedAgent(make_sim())
        result = agent.run("red_cube to blue_bin")
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
