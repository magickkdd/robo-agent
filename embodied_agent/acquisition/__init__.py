"""SPEC-v0.2 §5.6 Skill Acquisition: find where the library ran out, write a program over the
existing primitives, run it in a sandbox under the same verifier everything else gets, validate it
on configurations it was not proposed for, and only then let it into memory.

Four modules, in pipeline order — §5.6's five boxes, with the LLM proposal box belonging to the
model-facing side of the runtime rather than to this package:

* `program` — the representation. A `SkillSpec(level="acquired")` whose every step names a
  `SkillRegistry.CATALOGUE` primitive, plus the gate that refuses a proposal which is not an
  executable program (unbound placeholder, unevaluated `when`, a skill the executor does not
  implement, an integer argument that will not parse).
* `gap` — §5.6's first step. Reads one episode's `events.jsonl` and emits `SkillGap` rows for the
  recurrence that is evidence about the *library*, and says nothing about the refusals that are
  evidence about the plan.
* `sandbox` — execution and verification. Builds a world from a description, runs the bound program on
  it through the shipped `SkillExecutor`, asks `RuntimeVerifier` the question the candidate's own
  `expected_effects` asks, and audits every attribute the run reached on the scene. The four things
  §5.6 forbids (teleport, editing the evaluator, reading hidden state, writing the world) are
  structures here, not rules: no pybullet handle, no ledger import, no live scene parameter, and a
  writer reached from outside the three shipped implementation files becomes a `violations` row.
* `validation` + `memory` — the last two boxes. A pre-registered matrix with one instance per unseen
  object / layout / parameter, the frozen `SkillValidationReport` assembled from those runs, and a
  store that admits a skill only with a report that passed *both* the frozen rule and the stricter
  reading of §5.6's sentence.

Why this package is called `acquisition` and not `skills`: `embodied_agent/skills.py` is the v0.1
MVP's `SkillLibrary`, still imported by `agent.py`, and a package of that name would shadow it —
`from .skills import SkillLibrary` would resolve to this directory and fail. The naming follows the
other v0.2 modules (`planning/`, `episodic/`, `perception/`), which are named for the capability,
and matches the module owner `skill_acquisition` that `core/v02.py:EVENT_MODULE` already assigns the
four §5.6 event types.
"""
