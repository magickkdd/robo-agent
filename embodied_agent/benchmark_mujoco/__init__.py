"""A GUI-capable, socket-bearing benchmark attached to the v0.2 decision loop.

MetaWorld 3.0 (MuJoCo, MIT-licensed) over an isolated interpreter, driven by
`core/runtime.py:Runtime` and the same `PlanningMixin` arms the desktop and ALFWorld
text channels use. See `env.py` for what the adapter is forbidden to do (§10) and
`gui.py` for how to watch an episode in a browser.
"""
from __future__ import annotations

__all__ = ["env", "skills", "state", "executor", "prompts", "runtime", "planned_runtime",
           "policy", "runner", "gui", "cli"]
