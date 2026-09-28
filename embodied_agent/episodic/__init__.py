"""SPEC-v0.2 §5.4: episodic memory — what one episode leaves behind, and how a later one finds it.

Named `episodic`, not `memory`: `embodied_agent/memory.py` is v0.1's `MemoryStore` and is
re-exported at the package root, and a directory called `memory` would shadow it silently.

Three modules, one boundary each. `experience` turns a finished episode's own records into an
`EpisodicExperience`; `store` keeps those rows across episodes in an append-only file; `retrieval`
answers "is any of this relevant to what I am facing now" and, when the current snapshot refutes a
recalled row, says so. `arm.py` (P3-c) is the only thing that connects them to the decision loop,
and it connects them as *text a model reads*: nothing in this package can write a `WorldState`,
which is §5.4's hard rule about retrieval and §3.5's separation of world facts from model
intentions from historical experience from evaluation truth.

`arm` and `policy` are resolved lazily, and that is the same rule as a design fact rather than an
import nicety: the memory-only modules above must stay loadable without a simulator, because
§3's separation claims the extractor reaches for no world. A package-level eager `from .arm import
...` would make that claim false the moment anyone wrote `from embodied_agent.episodic import
ExperienceStore` — `arm` needs the runtime, the runtime needs `scene`, `scene` needs pybullet — and
`test_the_extractor_opens_two_files_and_reaches_for_nothing_else` reads this package's own import
graph to check it. Lazy attributes keep the convenience of the short name without the cycle.
"""
from typing import Any

from .experience import (experience_from_episode, experience_id_for, last_working_memory_status,
                         read_episode_dir, state_pattern_of)
from .retrieval import (DEFAULT_LIMIT, MIN_RELEVANCE, RELEVANCE_WEIGHTS, contradicted,
                        indexed_terms, mark_used, query_from, render_rows, retrieve, score)
from .store import ExperienceStore

#: name -> module that owns it. Everything here imports `core.runtime`; see the module docstring.
_LAZY: dict[str, str] = {
    "EPISODIC_ARMS": "arm",
    "EpisodicMixin": "arm",
    "ExperiencedPerceptRuntime": "arm",
    "ExperiencedPlannedRuntime": "arm",
    "build_episodic_arm": "arm",
    "MemoryPolicy": "policy",
}

__all__ = ["ExperienceStore", "experience_from_episode", "experience_id_for", "read_episode_dir",
           "state_pattern_of", "last_working_memory_status",
           "query_from", "retrieve", "score", "mark_used", "render_rows", "contradicted",
           "indexed_terms", "RELEVANCE_WEIGHTS", "MIN_RELEVANCE", "DEFAULT_LIMIT"] + sorted(_LAZY)


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals()) + __all__)
