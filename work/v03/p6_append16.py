"""Append §16 to the v0.3 report, and renumber the two duplicate residual-14 rows.

Written as a script because the section is Chinese prose full of backticks, and a shell heredoc
mangled it badly enough to truncate the report mid-write. The file is restored from the commit
first, then the section is appended by this script, and the report's byte length is checked
before and after so a truncation cannot pass unnoticed.
"""
import os

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
REPORT = os.path.join(REPO, "docs", "continuous-decision-v0.3-final-report.md")
SECTION = os.path.join(REPO, "work", "v03", "p6_section16.md")

before = os.path.getsize(REPORT)
text = open(REPORT, encoding="utf-8").read()
assert "## 16." not in text, "§16 already present; refusing to append twice"

# the residual table shipped with two rows numbered 14
old = ("| 14 | `calls_without_an_answered_identifier: 16`（`lh_full` 格）——有 16 次调用没有被记录"
       "应答标识，而 `identities` 仍显示\"请求什么就答什么\" | 仪器自己分了两列（`identities` 与 "
       "`calls_without_an_answered_identifier`）；本轮未追查这 16 次是哪种形状 |")
new_tail = ("| 15 | `calls_without_an_answered_identifier: 16`（`lh_full` 格）——有 16 次调用没有被"
            "记录应答标识，而 `identities` 仍显示\"请求什么就答什么\" | 仪器自己分了两列"
            "（`identities` 与 `calls_without_an_answered_identifier`）；本轮未追查这 16 次是哪种"
            "形状 |\n"
            "| 16 | **模型席上没有 policy，于是 MEM-1 有 5 行没有仪器**：那 5 行读 "
            "`episode_summary.policy.trace`（`memory_followed` / `memory_declined` / "
            "`memory_order`），而 trace 由 `MemoryPolicy` 逐轮写下，模型席上决策者是模型规划器、"
            "`policy=None`，**没人写它** | **已修**（P6-d）：这 5 行在模型席上改为**结构性 "
            "not-measured** 并写明该读哪一行，**不再报 0**。此前它们读出 `0/15`，那是**编出来的"
            "负数**——0 读起来像证据，缺席的仪器不像 |\n"
            "| 17 | **一个\"界内通过\"的假检查**：停止规则按\"我传给 `run_group` 的目录\"找账本，"
            "而 `run_group` 在它下面建带时间戳的子目录，于是读到空树、求和 0、打印 "
            "`spent 0, completed within bound`——**一次对着空检查的干净通过** | **已修**（P6-d）："
            "按每批**自报的 run root** 读，且**读到 0 一律拒绝继续**。MEM-1 那批实际 216 < 411，"
            "**界是靠投影的运气守住的，不是靠检查** |")
assert old in text, "the residual row to renumber was not found"
text = text.replace(old, new_tail, 1)

section = open(SECTION, encoding="utf-8").read()
text = text.rstrip("\n") + "\n" + section

with open(REPORT, "w", encoding="utf-8") as f:
    f.write(text)

after = os.path.getsize(REPORT)
check = open(REPORT, encoding="utf-8").read()
print(f"  before {before:,} bytes -> after {after:,} bytes")
print(f"  §16 present        : {'## 16.' in check}")
print(f"  ends with §16.4    : {check.rstrip().endswith(chr(36825) + chr(20854) + chr(36890))}")
print(f"  residual rows 14-17: "
      f"{all(f'| {n} |' in check for n in (14, 15, 16, 17))}")
print(f"  duplicate row 14   : {check.count('| 14 | `calls_without')}")
assert after > before, "the report shrank"
assert "## 16." in check
# the last line ends `...**" + "可测" + "**" + '"' + "。` — asserted on the punctuation, because
# an earlier version of this assert omitted the closing quote and fired on a complete file
assert check.rstrip().endswith(chr(12290)), "the append was truncated"
print("OK")
