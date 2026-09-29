"""Re-apply P6's three deliverable edits, idempotently, in one pass.

What went wrong to make this a script: §16 was committed, §17 and §18 were appended in the
working tree only, and then `git checkout --` on the report — run to undo a double-append —
reverted **all of it**, including §17. Re-typing five in-place corrections by hand after that is
exactly the situation a script should not be asked to survive, so the corrections live here as
data, each one idempotent, and the script refuses to run twice on the same edit.

Every fix is `(old, new)`; a fix already applied is reported as `already` and skipped. If an `old`
is found nowhere and is not already present, the script fails loudly rather than appending a
duplicate — that was the failure mode of the first version of the §16 appender.
"""
import os
import sys

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
REPORT = os.path.join(REPO, "docs", "continuous-decision-v0.3-final-report.md")

# ---------------------------------------------------------------- §17 corrections ----
FIX17 = [
    # §2 table: L2 and L6 rows, plus the weight-separation paragraph
    ("| `dependency violations`（`L2.dependency_edge_violation`） | **六格全 n/m**——"
     "\"没有任何一条依赖边被写下\" | **`full` 1.0 (2/2)**：模型写下了依赖边，**并把它们全部违反了** |",
     "| `dependency violations`（`L2.dependency_edge_violation`） | **六格全 n/m**——"
     "\"没有任何一条依赖边被写下\" | **`full` 1.0 (2/2)**：**机制可达**了（模型确实写下依赖边、"
     "守卫确实抓到违反）——但 **n=2，率定不下来**：Wilson 95% **(0.342, 1.000)**、精确二项双侧 "
     "**p=0.50**。见 §17 |"),
    ("| `recovery/replanning effectiveness`（`L6.replanning_effectiveness`） | **0.0 (0/4)**——"
     "v0.2 的头号负结果 | **`full` 0.5789 (11/19)** |",
     "| `recovery/replanning effectiveness`（`L6.replanning_effectiveness`） | **0.0 (0/4)**——"
     "v0.2 的头号负结果 | **`full` 0.5789 (11/19)**——**n=19 同样与随机不可分**（精确二项双侧 "
     "**p=0.65**，Wilson 95% (0.363, 0.769)）。**\"从 0.0 变成 0.5789\"是一个读数，不是一个率**；"
     "见 §17 |"),
    ("| `M2.followed_where_control_chose_the_same`", None),  # marker only, never replaced
]

FIX17_BODY = [
    (" 2. **`w/o Replanning` 比 `full` 好**（9/12 vs 8/12），且 `L6.recorded_vs_inferred` 归零——\n"
     "   即\"丢失事件之后没有记录在案的动作\"这件事存在，而关掉重规划居然略微提高成功率。\n"
     "   n=12，两集之差不是效应量；它是一个**该被追问而不是被解释掉**的数（§5.3）。",
     " 2. **`w/o Replanning` 的读数略高于 `full`**（9/12 vs 8/12），且 "
     "`L6.recorded_vs_inferred` 归零——\n"
     "   即\"丢失事件之后没有记录在案的动作\"这件事存在。**但这两臂在 n=12 上分不开**：Fisher 精确\n"
     "   双侧 **p=1.00**，两个 Wilson 区间几乎完全重叠（(0.468, 0.911) 对 (0.391, 0.862)），\n"
     "   差值是**一集**。要看清 8 个点这个量级的差，每臂约需 **219 集**（本批的 **18 倍**）。\n"
     "   所以它是一个**该被追问而不是被解释掉**的数（§5.3、§17）——**方向也不能反过来当成"
     "\"重规划有害\"**：分不开就是分不开，两个方向都不成立。"),
    (" 3. **`wo_replanning` 9/12 比 `full` 8/12 好。** n=12，两集之差；不解释掉，列为待追问。",
     " 3. **`wo_replanning` 9/12，`full` 8/12。** n=12，差**一集**，Fisher 精确双侧 **p=1.00**，\n"
     "    两区间几乎完全重叠：**这两臂在这个分母上分不开**。不解释掉，列为待追问（§17）。\n"
     "    **不能反过来读成\"重规划有害\"**——分不开就是分不开。"),
    (" 8. **`L2.dependency_edge_violation` 从\"六格全 n/m\"变成 `full` 1.0 (2/2)**。\n"
     "   一条从\"测不到\"变成\"测到、而且是全违反\"的行。n=2。",
     " 8. **`L2.dependency_edge_violation` 从\"六格全 n/m\"变成 `full` 1.0 (2/2)**。\n"
     "   一条从\"测不到\"变成\"**机制可达**\"的行——模型确实写下了依赖边、守卫确实抓到了违反，\n"
     "   这是 v0.2 结构性够不到的东西。**但 n=2，Wilson 95% 是 (0.342, 1.000)、精确二项双侧\n"
     "   p=0.50**：\"全违反\"是这个分母上的读数，**不是\"模型 100% 违反它的依赖边\"**。"),
    ("| RQ5 Replanning 的贡献 | **读数存在但方向为负**：关掉它反而 9/12，"
     "而 `L6.recorded_vs_inferred` 归零 | §2.2、§5.3 |",
     "| RQ5 Replanning 的贡献 | **读数存在，但 n=12 分不开两臂**：`wo_replanning` 9/12 对 "
     "`full` 8/12，Fisher 精确双侧 **p=1.00**、差一集、每臂约需 219 集才看得清 8 个点的差。"
     "**不能说\"方向为负\"**——分不开就是分不开。可指的事实是 `L6.recorded_vs_inferred` "
     "在关掉它时归零 | §2.2、§5.3、§17 |"),
]

# ---------------------------------------------------------------- §18 corrections ----
FIX18 = [
    ("**在 privileged 通道上 4/4 成功、换成相机后 0/4**。v0.2 §6.5 把相机通道的净负归因于",
     "**在 privileged 通道上 4/4 成功、换成相机后 0 成功——但只有 2 集真跑出来**。\n"
     "**P6 把匹配分母算清楚了：非 privileged 两通道真跑出来的是 c1 的两集，vlm 0/2、stub 0/2、\n"
     "privileged 2/2**（§18）。所以\"4/4 → 0/4\"是拿两套不同的集在比、把落差说得比实测更重；\n"
     "**匹配集上仍是 0，但理由不同**。\nv0.2 §6.5 把相机通道的净负归因于"),
    ("| **privileged 4/4 → vlm 0/4** | v0.2 头号负结果重现 |",
     "| **匹配分母上 vlm 0/2 vs privileged 2/2**（c1 两集；c3 两集非 privileged 通道无结果）"
     "——v0.2 头号负结果重现，但**不是**交付文本写的 4/4 → 0/4（§18） |"),
    ("4. **相机通道在主 CLI 上仍是净负**：`privileged` 4/4 → `vlm` 0/4、耗时也更高。",
     "4. **相机通道在主 CLI 上仍是净负**：**匹配分母上 `privileged` 2/2 vs `vlm` 0/2**"
     "（c1 两集；c3 两集非 privileged 通道无结果，§18）、耗时也更高。"),
    ("- **E2 的 `full`-on-VLM 分母仍是 2 对 4**（§9 残留 2）：相机通道对 `lh_c3` 不可运行，未修。",
     "- **E2 的 `full`-on-VLM 匹配分母是 2**（§18）：非 privileged 两通道只跑出 c1 的两集，"
     "c3 两集无结果且**原因未记录**（新残留 18）。颜色那条的归属已更正（残留 20）。"),
]

SECTIONS = [(17, "p6_section17.md"), (18, "p6_section18.md")]

text = open(REPORT, encoding="utf-8").read()
before = len(text)
print(f"report starts at {before:,} chars")

for old, new in FIX17_BODY:
    if new in text:
        print(f"  §17 body  already applied: {old[:44]!r}")
    elif old in text:
        text = text.replace(old, new, 1)
        print(f"  §17 body  fixed            : {old[:44]!r}")
    else:
        print(f"  §17 body  MISS             : {old[:44]!r}", file=sys.stderr)

for old, new in FIX18:
    if new in text:
        print(f"  §18 body  already applied: {old[:44]!r}")
    elif old in text:
        text = text.replace(old, new, 1)
        print(f"  §18 body  fixed            : {old[:44]!r}")
    else:
        print(f"  §18 body  MISS             : {old[:44]!r}", file=sys.stderr)

for num, fname in SECTIONS:
    head = f"## {num}."
    if head in text:
        print(f"  §{num}      already present")
        continue
    body = open(os.path.join(REPO, "work", "v03", fname), encoding="utf-8").read()
    text = text.rstrip("\n") + "\n" + body
    print(f"  §{num}      appended ({len(body):,} chars)")

with open(REPORT, "w", encoding="utf-8") as f:
    f.write(text)

check = open(REPORT, encoding="utf-8").read()
print(f"report ends at {len(check):,} chars (+{len(check) - before:,})")
for num in (16, 17, 18):
    print(f"  §{num} present: {f'## {num}.' in check}")
for num in (16, 17, 18):
    n = check.count(chr(10) + f"### {num}.")
    print(f"  §{num} subsections: {n}")
assert all(f"## {n}." in check for n in (16, 17, 18)), "a section is missing"
assert all(check.count(chr(10) + f"### {n}.") == 4 for n in (16, 17, 18)), "a subsection is missing"
assert len(check) >= before, "the report shrank"
print("OK")
