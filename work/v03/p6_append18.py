"""Append §18, with the same truncation guard as §16 and §17.

Also: the delivered text quotes "privileged 4/4 -> vlm 0/4" in three places. §18 says that
comparison mixes two episode sets, so those three are corrected in place — the conclusion
("the camera channel is net negative") survives, but the numbers attached to it do not.
"""
import os

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
REPORT = os.path.join(REPO, "docs", "continuous-decision-v0.3-final-report.md")
SECTION = os.path.join(REPO, "work", "v03", "p6_section18.md")

before = os.path.getsize(REPORT)
text = open(REPORT, encoding="utf-8").read()
assert "## 18." not in text, "§18 already present; refusing to append twice"

# the two remaining "4/4 -> 0/4" quotations outside §2.5
fixes = [
    ("| **privileged 4/4 → vlm 0/4** | v0.2 头号负结果重现 |",
     "| **匹配分母上 vlm 0/2 vs privileged 2/2**（c1 两集；c3 两集非 privileged 通道无结果）"
     "——v0.2 头号负结果重现，但**不是**交付文本写的 4/4 → 0/4（§18） |"),
    ("4. **相机通道在主 CLI 上仍是净负**：`privileged` 4/4 → `vlm` 0/4、耗时也更高。",
     "4. **相机通道在主 CLI 上仍是净负**：**匹配分母上 `privileged` 2/2 vs `vlm` 0/2**"
     "（c1 两集；c3 两集非 privileged 通道无结果，§18）、耗时也更高。"),
    ("5. **`perception` 记录有了（51 条），但四集因颜色重复跑不起来**（§2.5 第 2 点）：\n"
     "   `long_horizon` 集不满足相机通道依赖的颜色唯一性，而这条性质只在 47 个感知 case 上被验证过。",
     "5. **`perception` 记录有了（51 条），但四集里两集失败、两集无结果**（§2.5 第 2 点、§18）：\n"
     "   颜色唯一性这条性质只在 47 个感知 case 上被验证过；重查后**重复颜色的 case 是 c1、"
     "且它跑起来了（6 轮后 `REPEATED_INVALID` 失败）**，而**真正没有产出的是 c3，两集 "
     "`infrastructure_error` 且错误账本里没有任何原因**——这条比颜色那条更该先修。"),
    ("- **E2 的 `full`-on-VLM 分母仍是 2 对 4**（§9 残留 2）：相机通道对 `lh_c3` 不可运行，未修。",
     "- **E2 的 `full`-on-VLM 匹配分母是 2**（§18）：非 privileged 两通道只跑出 c1 的两集，"
     "c3 两集无结果且**原因未记录**（新残留 18）。颜色那条的归属已更正（残留 20）。"),
    ("| 2 | `full`-on-VLM 的分母是 2 而 `privileged` 是 4 | `long_horizon` 集里不满足颜色唯一性的"
     " case 要么换掉、要么相机通道要一个不靠颜色的身体命名 |",
     "| 2 | `full`-on-VLM 的匹配分母是 2（只有 c1 两集真跑出结果），`privileged` 是 4 | "
     "**已更正归属**（§18）：重复颜色的 case 是 c1 且它跑起来了；c3 两集 `infrastructure_error` "
     "且**原因未记录**（新残留 18）；颜色拒绝在 stub 与 vlm 两格都发生，成因在共用接地图，"
     "所以改 vlm 适配器**不**解决它（残留 20） |"),
]
for old, new in fixes:
    if old not in text:
        print(f"  MISS  {old[:56]!r}")
        continue
    text = text.replace(old, new, 1)
    print(f"  fixed {old[:48]!r}")

section = open(SECTION, encoding="utf-8").read()
text = text.rstrip("\n") + "\n" + section
with open(REPORT, "w", encoding="utf-8") as f:
    f.write(text)

after = os.path.getsize(REPORT)
check = open(REPORT, encoding="utf-8").read()
print(f"  before {before:,} -> after {after:,} bytes")
print(f"  §16/§17/§18 all present: "
      f"{all(f'## {n}.' in check for n in (16, 17, 18))}")
print(f"  §18 subsections: {check.count(chr(10) + '### 18.')}")
assert after > before, "the report shrank"
assert all(f"## {n}." in check for n in (16, 17, 18))
assert check.count(chr(10) + "### 18.") == 4, "§18 is missing a subsection"
# §18 ends on a table row, so the guard looks for that row's own last clause rather than a full
# stop — the previous version of this assert expected 。 and fired on a complete file, twice now
# in this session's history
assert "long_horizon` 集上验证并写进冻结集的前提 |" in check, "the append was truncated"
print("OK")
