"""Append §17, the same way §16 was appended: by script, with a truncation guard.

The report has now been truncated once by a shell heredoc that choked on backticks, so the
append is done in Python and the result is checked — the file must grow, must end on §17.4's
sentence, and must now carry both section numbers.
"""
import os

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
REPORT = os.path.join(REPO, "docs", "continuous-decision-v0.3-final-report.md")
SECTION = os.path.join(REPO, "work", "v03", "p6_section17.md")

before = os.path.getsize(REPORT)
text = open(REPORT, encoding="utf-8").read()
assert "## 17." not in text, "§17 already present; refusing to append twice"

section = open(SECTION, encoding="utf-8").read()
text = text.rstrip("\n") + "\n" + section
with open(REPORT, "w", encoding="utf-8") as f:
    f.write(text)

after = os.path.getsize(REPORT)
check = open(REPORT, encoding="utf-8").read()
print(f"  before {before:,} -> after {after:,} bytes")
print(f"  §16 and §17 both present: "
      f"{'## 16.' in check and '## 17.' in check}")
print(f"  §17 subsections          : {check.count(chr(10) + '### 17.')}")
print(f"  ends on §17.4            : {check.rstrip().endswith(chr(12290))}")
assert after > before, "the report shrank"
assert "## 17." in check
assert check.count(chr(10) + "### 17.") == 4, "§17 is missing a subsection"
assert check.rstrip().endswith(chr(12290)), "the append was truncated"
print("OK")
