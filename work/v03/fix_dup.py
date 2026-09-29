"""One-off: remove the `from_config` block that was inserted into `DeepSeekPlanner`
instead of `DeepSeekAdapter` (1-indexed lines 795..845, verified above)."""
p = "embodied_agent/adapters/deepseek.py"
lines = open(p, encoding="utf-8").read().split("\n")
assert lines[794] == "    # ---------- construction ----------", repr(lines[794])
assert lines[844] == "    @classmethod", repr(lines[844])
assert lines[845].startswith('    def from_env('), repr(lines[845])
del lines[794:845]
open(p, "w", encoding="utf-8").write("\n".join(lines))
print("removed; new length", len(lines))
