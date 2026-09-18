from __future__ import annotations

from typing import Any, Dict, List

from .memory import MemoryStore
from .models import EpisodeRecord, TaskSpec
from .planner import RuleBasedPlanner, SimpleTaskParser
from .simulator import TabletopSimulator
from .skills import SkillLibrary
from .verification import Verifier


class EmbodiedAgent:
    def __init__(self, simulator: TabletopSimulator, memory: MemoryStore | None = None, max_retries: int = 1) -> None:
        self.simulator = simulator
        self.memory = memory or MemoryStore()
        self.parser = SimpleTaskParser()
        self.planner = RuleBasedPlanner()
        self.skills = SkillLibrary(simulator)
        self.verifier = Verifier()
        self.max_retries = max_retries

    def run(self, task_text: str) -> Dict[str, Any]:
        observation = self.simulator.observe()
        task: TaskSpec = self.parser.parse(task_text, observation)
        actions: List[str] = []
        failures: List[str] = []
        retries = 0

        while True:
            observation = self.simulator.observe()
            plan = self.planner.plan(task, observation, self.memory.retrieve(task_text))
            replanned = False
            for step in plan:
                if step.skill == "verify":
                    ok, evidence = self.verifier.check(task, self.simulator.observe())
                    actions.append("verify")
                    if ok:
                        record = EpisodeRecord(task_text, True, actions, failures, "ordered pick/place with verification")
                        self.memory.record(record)
                        return {"success": True, "actions": actions, "failures": failures, "evidence": evidence, "memory_size": len(self.memory.records)}
                    failures.append(f"verification failed: {evidence}")
                    replanned = True
                    break

                result = self.skills.execute(step.skill, step.args)
                actions.append(f"{step.skill}({step.args})")
                if result.success:
                    continue
                failures.append(result.message)
                if retries >= self.max_retries:
                    record = EpisodeRecord(task_text, False, actions, failures, "recovery budget exhausted")
                    self.memory.record(record)
                    return {"success": False, "actions": actions, "failures": failures, "memory_size": len(self.memory.records)}
                retries += 1
                self.skills.execute("reset_held", {})
                actions.append("reset_held({})")
                replanned = True
                break
            if not replanned:
                # Defensive exit if a malformed planner ever emits no verify.
                failures.append("planner ended without verification")
                record = EpisodeRecord(task_text, False, actions, failures, "invalid plan")
                self.memory.record(record)
                return {"success": False, "actions": actions, "failures": failures, "memory_size": len(self.memory.records)}

