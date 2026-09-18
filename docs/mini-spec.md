# Mini specification: first executable slice

## Product target

Build a reusable framework around a fixed robotic arm that can complete multi-object tabletop organization tasks. The first executable slice must expose the complete control loop:

`language task -> plan -> skill calls -> simulator -> verification -> recovery/replan -> episode memory`

The initial task family is sorting/put-away: each object has a category and each target zone accepts one category. A task can contain multiple objects and requires an ordered sequence of moves.

## MVP boundaries

- Simulator: deterministic 2-D tabletop abstraction. It models object positions, target zones, grasp/place preconditions, and injected failures. It is a test double for a later PyBullet or MuJoCo backend.
- Perception: privileged simulator observation (`object pose`, `target pose`, `held object`). This keeps the first control loop cheap and debuggable.
- Planning: deterministic baseline planner. It parses a small natural-language grammar and emits `pick -> place -> verify` steps. The planner interface is model-agnostic so a DeepSeek adapter can be added without changing execution.
- Control: mature low-level components remain behind a simulator interface. This slice does not implement IK or dynamics.
- Recovery: one bounded retry after re-observation; a failed attempt is recorded and the task is replanned from the current state.
- Memory: append-only episode records plus retrieval of prior strategy summaries. Memory is advisory and cannot silently change the world state.

## Contracts to keep stable

1. `Simulator.observe()`, `pick()`, and `place()` are the embodiment boundary.
2. `Skill.execute()` returns a structured `ActionResult` with evidence and failure reason.
3. `Planner.plan(task, observation, memory)` returns typed steps, never raw code.
4. `Verifier.check(task, observation)` returns pass/fail plus per-object evidence.
5. `MemoryStore.record()` and `retrieve()` are independent of the model provider.

## First acceptance gate

The fixed seeded scene contains three objects and three target zones. The demo must finish with every object in the matching zone despite one deterministic injected place failure. The test suite also checks that the no-recovery baseline fails on the injected error, making the value of recovery measurable.

## Next iteration after this slice

Replace the simulator with a PyBullet adapter, retain the same contracts, and add an RGB-D observation type alongside privileged state. Only then connect the planner to a live DeepSeek API behind the `ModelAdapter` protocol.
