"""Point the four ambiguous artefact citations at their real paths.

The v0.3 document gate flagged them: the report and the log named `e1_state.json`,
`e1_table.json`, `e3_criterion4.json` and `e3_rq6_answer.json` as bare filenames while they
live in different subdirectories of the archive, so a reader would have to guess which. A
citation that has to be guessed is not a citation.
"""
import os

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
BT = chr(96)  # a backtick, built rather than escaped

EDITS = {
    "docs/continuous-decision-v0.3-final-report.md": [
        (f"格级摘要 {BT}e1_state.json{BT}、全表 {BT}e1_table.json{BT}",
         f"格级摘要 {BT}e1/e1_state.json{BT}、全表 {BT}e1/e1_table.json{BT}"),
        (f"{BT}e1_state.json{BT} 从盘上重导",
         f"{BT}e1/e1_state.json{BT} 从盘上重导"),
    ],
    "docs/continuous-decision-v0.3-phase-log.md": [
        (f"{BT}e1_state.json{BT} 里存着修复前",
         f"{BT}e1/e1_state.json{BT} 里存着修复前"),
        (f"已写进 {BT}e3_rq6_answer.json{BT} 的",
         f"已写进 {BT}e3/e3_rq6_answer.json{BT} 的"),
        (f"（{BT}e3_criterion4.json{BT} 的",
         f"（{BT}e3/e3_criterion4.json{BT} 的"),
        (f"第二次：{BT}e1_state.json{BT} 里存着",
         f"第二次：{BT}e1/e1_state.json{BT} 里存着"),
    ],
}

os.chdir(REPO)
for path, pairs in EDITS.items():
    text = open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in text:
            print(f"  MISS {os.path.basename(path)}: {old[:46]}")
            continue
        text = text.replace(old, new)
    open(path, "w", encoding="utf-8").write(text)
    print(f"rewrote {path}")
