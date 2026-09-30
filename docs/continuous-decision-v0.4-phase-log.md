# Continuous-Decision Embodied Agent v0.4 —— 阶段日志

执行纪律沿 v0.3：**先预登记、先测量、后说话；判据进测试与仪器；每条读数带论域；负结果保留并给
原因；`cost_estimate_usd` 无单价时保持 `None`。** SPEC 是
`Continuous-Decision-Embodied-Agent-SPEC-v0.4.md`；读数（P4 之后）将记在 v0.4 最终报告。

---

## P0 预检（零成本，2026-09-30）

**边界验证**（当场重跑）：`freeze --check` → `2f1c74f52e91`（rc=0）；`prereg --check` →
`23abe58ff333…`（rc=0）；`em-pairs --check` → `7df449ed2f4d`（rc=0）；
`tests/contract/test_v02_contracts.py` **25 passed**（fingerprint 与 `v02_schema_freeze.json`
逐项相等的承载判据）⇒ R1/R2/R3 的落点（`evaluation/run.py`、`core/events.py` 写侧、
`perception/grounding.py`）都不在 `core/v02.py`，冻结面约束成立。

**D1'–D4' 落定**（用户裁定，记于 SPEC §13）：主攻 v0.4；三残留全修；免费档两 API
**批间轮换、批内单席**；Next-Stage S2 正式归档不补跑（裁定文件
`docs/next-stage-s2-pivot-ruling.md`）。D5'（E1' 规模）与 D6'（sensenova 上岗资格）待 P2。

**提交**：`9471ee8`（SPEC + 裁定 + `work/v03/p7_msg.txt` 删除入库）。

---

## P1 开工前的三处盘上重判（2026-09-30，全部先量后说）

SPEC §5 的三道修复按纪律"先量后改"，动第一行代码之前回到产物上复核各残留的事实基础。
**三处里有两处的事实基础与盘上证据不符**，另有一处是 v0.3 报告 §18 自己的新错误。
证据全部可重跑（本节末尾给出逐条命令），文件 mtime 均为批次当时（2026-09-29 17:5x），
不是事后补写。

### 重判一（#19 → 仪器致盲）：manifest 一直记着通道，P6 的仪器找错了键名

- **残留 #19 原文**："三个 manifest 都没有 `perceive` 键……通道身份只能从每集摘要反查。"
- **盘上事实**：E2 三格的 `manifest.json` 都带 `perception` 键，内含
  `"channel": "privileged|stub|vlm"`（另有 arm/modules_off/views/default_view）。这个键自
  `run.py` 进入版本库（`230f4dc`）起就在写侧里。
- **致盲源**：`work/v03/p6g_missing.py` 的键清单写的是**`perceive`**（CLI 旗标名），manifest
  的键叫 **`perception`**（事件流名）。仪器按自己的键清单挑字段，把 `perception` 整块滤掉了，
  报告据打印结果写下"没有键"。这是 H-56 #39 那一族错误的再次发作：**"每格都要说出它读的是
  哪个键"——仪器读了 `perceive`，没人产出 `perceive`。**
- **处置**：R2 从"加键"改为**更正记录 + 钉住性质**。不新增 `perceive` 别名键（一份身份两个
  名字是两处可以不一致的地方）；契约测试把"批次 manifest 记录感知通道"钉成永久性质；
  v0.3 报告追加 §19 更正（§18.4 残留表第 19 行就地标注）。

### 重判二（#18 → 前提不成立，代码洞仍然真实）：c3 的死因在账本里，缺口在另两条路

- **残留 #18 原文**："c3 的两集 `infrastructure_error` 且错误账本里没有任何原因……
  errors.jsonl 没记。"
- **盘上事实**：vlm 与 stub 两格的 `errors.jsonl` **各有恰好两行**，`episode_id` /
  `case_id` 都是 `lh_c3_capacity_and_shift`，error 字段一字不差是颜色拒绝的
  `ValueError`。mtime = 批次当时。⇒ c3 的四集非特权死因**当时就记了账**。
- **代码洞仍然真实**：`_infra_row` 的三个装配点里只有集内崩溃路写账本（E2 的 c3 正好死在
  这条路上，所以有账）；`resolve_goal` 异常路与 `resolution.error` 路**不写**。E1 的
  16 行 `goal_resolution_error`（逐格 1+3+1+1+1+1+2+1+1+4）全部不在任何 errors.jsonl 里
  ——E1 十二格**没有一个** `errors.jsonl` 文件。它们的原因记在行本身与
  `goal_resolutions/` 产物里，可答，但 `cli` 打印的"see errors.jsonl"指向的账本不知道它们。
- **处置**：R1 的改动照做（三路统一写账本 + `goal_resolution_error` 入账），动机更正为
  **覆盖补全**："一个非成功结局有没有原因"不应当取决于它死在哪条路上。契约测试先红后绿。
  历史产物不回填（E1 的 16 行原因在行上，补账本是给未来的批）。

### 重判三（§18.2 的 c1/c3 倒置）：重复颜色在 c3，不在 c1；原交付文本是对的

- **§18.2 原文**："重复颜色的 case 是 c1，不是 c3……颜色那条没有让任何一集跑不起来。"
- **盘上事实（四条证据互锁）**：
  1. 冻结任务表（`frozen_long_horizon_v1.json`，sha `2f1c74f52e910073`，与 E2 运行时
     `task_manifest.json` 的 sha 一致，也与今日 `freeze --check` 一致）：c1 的黄色只有
     `obj_yellow_3`（唯一）；**c3 有 `obj_yellow_1`（cube）与 `obj_yellow_6`（cuboid）**；
  2. 错误行点名的正是 `obj_yellow_1` 与 `obj_yellow_6`，且错误行的 `case_id` 是
     `lh_c3_capacity_and_shift`；
  3. `episodes.csv`：c3 两格全部 `infrastructure_error`（无 episode_summary——死在集起点的
     声明绑定上），c1 两格 `failed`（跑满 6 轮的 REPEATED_INVALID）；
  4. v0.3 交付时的 P3' 阶段日志原文（"`lh_c3` 声明了两个黄色物体……因此拒绝 ⇒ 两格 0/4"
     的形状）与此完全一致。
- **结论**：§18.2 的更正本身是**倒置的**——原交付没有说错，P6 的重判把错误行归属读反了
  （同名 `yellow` 词出现在两格声明里，仪器没有按 `case_id` 过滤）。§18.1 的匹配分母算术
  （0/2 对 4/4）与 §18.2 的第 2 点（stub 也走同一张接地图，成因在共用桥不在"相机"泛指）
  **不受影响，仍然成立**；被推翻的是第 1、3 两点与残留 18 的表述。
  §17.3 的教训原文是"先写下的断言不会被后面算出的数追回注意力"——§18 自己犯了同一形状的错，
  本节按同一条纪律更正它。
- **处置**：R3 不受影响、反而更干净（拒绝真实杀掉了 c3 的四集非特权 episodes；复合键
  (colour, shape) 恰好是 c3 两黄的区分键）。v0.3 报告 §19 一并更正 §18.2 的第 1、3 点。

### 重判的复现入口（每条命令独立可跑）

```bash
# 重判一：三格 manifest 都有 perception.channel
python3 -c "import json,glob;[print(m.split('/e2/')[1].split('/')[0],
  json.load(open(m))['perception']['channel']) for m in sorted(glob.glob(
  '/home/czx/embodied-agent-batches/v03/e2/long_horizon_*/**/manifest.json',recursive=True))]"
# 重判二：vlm/stub 两格 errors.jsonl 各两行、全指 c3
grep -l . /home/czx/embodied-agent-batches/v03/e2/long_horizon_{vlm,stub}/*/errors.jsonl
# 重判三：冻结表里 c3 两黄、c1 一黄；错误行 case_id 全是 c3
python3 -c "import json;d=json.load(open('configs/experiment/frozen_long_horizon_v1.json'));\
print([(c['task_id'],[o['entity_id'] for o in c['objects'] if (o.get('attributes') or {}).get('color')=='yellow']) for c in d['cases']])"
```

### P1 之后的工序

R1/R2/R3 按 SPEC §5 的验收定义执行（R2 的验收条目按重判一改写为"性质钉住"）；
然后 P2 探针 → P3 预登记 → P4 三批 → P5 报告。

---

## P2 判据（先于任何请求落盘；2026-09-30，SPEC §4 原则 15）

探针脚本：`work/v04/probe_seats.py`（沿用 v0.3 `seat_probe.py` 的形态；新增分档与限流窗两格）。
**以下判据在本节落盘时，本轮尚未向任何端点发出第一个请求。**每一格"跑完不回头改判据"。

| 探针 | 对象 | 判据（事先写死） | 花费 |
| --- | --- | --- | --- |
| A 席位健康 | `bst_text_agnes.yaml` | 配置可读 + key 可装载（零花费）+ 1 次最小请求 `raw_chars>0` | 1 次请求 |
| B 席位健康 | `agnes-vision-r4.yaml` | 同 A（文本侧；视觉侧另由 E2' 探针批覆盖） | 1 次请求 |
| C 上岗资格 | `sensenova-vision.yaml`（声明 text 能力，直接用于文本探针；不合格则**不**另建文本配置） | 分档 `max_tokens ∈ {1200, 4096, 16384}` × **决策级提示**（~4k token 载荷，`GRADE_USER`）；**任一档 `raw_chars>0` ⇒ 合格；全 0 字符 ⇒ 不合格**，记具名负结果，D3' 降级为 agnes 单席 | 3 次请求 |
| D 限流窗口 | agnes 文本席 | 10 次最小请求、间隔 1 s；429 次数与间距**是读数不是判决**，供上界与停止规则 | 10 次请求 |
| E 目标解析余量 | E1 归档（离线，零请求） | 重导 `requests_per_row`（v0.3 实测 2.027）与 goal-parse 占比（356/3,292 = 10.8%）；**上界公式 = 计划集数 × requests_per_row × 1.25 + 目标解析请求数** | 0 |

合计 ≤15 次请求，全部免费层。合格与否的读数将记在本节下方，不改判据原文。

### P2 读数（2026-09-30 当场跑，判据见上表，跑后未改判据一字）

| 探针 | 读数 | 判定 |
| --- | --- | --- |
| A agnes 文本 | `{"ok": true}` 12 字符、stop、7.2 s、reasoning 0 | **健康**（判据满足） |
| B agnes-vision-r4 文本侧 | 同形，0.5 s | **健康**（判据满足） |
| C sensenova 分档 | **三档全 stop**、正文 33 字符（`{"ok": true, "rounds_seen": 40}`）、reasoning 390–513 字符、2.2–2.5 s、prompt 2,975 tokens | **合格**（判据满足）——**适用域写死见下** |
| D agnes 限流窗口 | 10 次：9 成功、1 次 429（第 10 次，重试 5 次全 429，15.6 s） | 读数：免费档窗口窄，"3 连 429 停批"仍必要；×1.25 余量合理 |
| E 目标解析余量 | `batch_spend` 逐格重导：12 格 **3,292 = 逐集 2,936 + goal 356**（占比 10.8%），与 v0.3 报告逐位相符；**lh 格 63 集 1,960 请求（每集 31.1）、em 格 90 集 1,332（每集 14.8）** | 公式成立；E1' 上界输入见下 |

**C 格的适用域（不写这句就会重演 P0' 的教训）**：GRADE 载荷是 ~3k token；P0' 的
"0 字符"死法是 survey 级**大载荷**触发的（v1 实测 decide 轮载荷 ~58k token 级）。
sensenova 的合格结论只覆盖**短载荷席位**（探针、诊断、短轮次批）；它上 E1' 主力席
（模型决策轮）的资格**未测**，上岗前必须在真实载荷尺寸上重测一次。D3' 的轮用方案据此：
**E2' 视觉席 = agnes（已验证），E1' 主力 = agnes，sensenova 列为短载荷备席**。

**D5' 的裁定输入（E1' 上界公式）**：lh-only 模型决策批的上界 ≈ 计划集数 × 31.1 × 1.25。
L2 需要两位数触发集：v0.3 的触发率 2/12（full 格），两位数 ≈ 60 集 ≈ **1,870 请求**；
L6 跨席同分母由 rule 席免费重跑补齐（rule 臂 0 请求）。E2' 上界：rule 决策 + vlm looks，
v0.3 实测 73 请求/12 集（c3 两通道当时未跑），全跑估 150–250，×1.25 ⇒ **预登记定 400**。
探针产物（无凭据）在 `/tmp/v04_probe_*.json`，读数以本节为准。

---

## P3 预登记（部分落盘，2026-09-30）

- **E2'**：`configs/experiment/v04_e2_preregistration.json` —— 3 格 × 2 case × 2 repeats
  = 12 集，匹配分母 4/格；上界 400 请求（依据：v0.3 实测 73/12 且 c3 两通道未跑，
  全跑估 150–250 × 1.25）；席位 agnes-vision-r4（探针 B）；前提四条（P1 提交、复合键
  套件测试、fingerprint 不动、三门 rc=0）；桥 digest 变更作为**声明过的身份变更**入档
  （§2.5），行与 v0.3 并排不合并。不设独立探针批：`--perceive vlm` 可达性 v0.3 已证，
  本次重跑的 blocker 已修并有契约测试。
- **MEM-1'**：`configs/experiment/v04_mem1_amendment_1.json` —— 原预登记一字不改，
  只补缺席的第 3、4 对；上界 250（原批 2 对 216 请求的外推）；n=4 的读法**预写死**：
  与随机不可分即交付区间不交付结论。产物目录 v04/mem1_model，v0.3 的两对原样保留，
  已付费对不重跑。
- **E1'**：等待 D5'（用户裁定）。裁定输入已在 P2 落账：lh 格每集 31.1 请求、
  L2 两位数触发 ≈60 集 ≈1,870 请求、L6 的 rule 席零花费。

---

## P4（一）：E2' 正式批读数（2026-09-30，SPEC §7 E2'，预登记 `v04_e2_preregistration.json`）

批于 13:03–13:19 跑完，三格全部 rc=0，**132 / 400 请求**（上界 33%），R1 活体核验
三格全绿（非成功结局与账本行逐格相符），R2 manifest 感知键三格逐格相符。

### RQ8 的第一个干净答案（匹配分母 4，v0.3 是 4/2/2）

| 格 | 成功 | 失败形态 | 集数 | 请求 | 说明 |
| --- | --- | --- | --- | --- | --- |
| privileged | **4/4** | — | 4 | 0 | 与 v0.3 相同（11/11/13/13 轮） |
| stub | **0/4** | c1 两集 REPEATED_INVALID（1/4 物体）；c3 两集 blocked（1/6） | 4 | 0 | **c3 两集第一次跑起来**（v0.3 是 infrastructure_error） |
| vlm | **0/4** | c1 两集 **BUDGET_EXHAUSTED**（1/4）；c3 两集 blocked（**0/6**） | 4 | 132 | 92 条 `perception` 记录；每集 looks 22–28 |

**负结果在无混淆条件下成立**：同一个 rule 决策器，特权态 4/4、相机态 0/4、
限定语义态 0/4——瓶颈在感知接口给出的世界（它能不能被行动），不在决策器。
v0.3 的 0/2（匹配分母 2）在 n=4 上复现，且这次**没有任何一集死于基础设施**。

### R3 在计费通道上的直接证据

c3 的 vlm 集事件流里 `obj_yellow_6` 出现 32 次——模型通过 (colour, shape) 复合键
真实绑定并尝试操作那具黄 cuboid（v0.3 时这一格的声明绑定直接拒绝整集）。
c3 的四集（stub/vlm）全部以 `blocked` 结束：**模型（此处为 rule 决策器）在第 4 轮
宣告无可行动作**——这是 R3 修复新暴露的代码面（旧桥下这些格根本跑不到这里），
"blocked 不证明任务不可解"的守卫注释原样在事件里。

### 一条新观察（记录，不在本轮修）

`blocked` 终止的集，`episode_summary.failure_type=None` 且 `termination_reason=None`——
原因只存在于事件流的 `terminated_blocked` 事件里（"blocked by model"），CSV/摘要的读者
看不见。这是 #18 的集内表亲（"失败无类型"），与 R1 的账本洞同族但更深一层（它有事件、
无摘要字段）。记为 v0.4 残留 #21，处置留给 v0.5（涉及 `_finalize` 的摘要字段，
超出本轮预登记的改动形状）。

### 花费

vlm 格 132 请求、prompt 137,299、completion 37,931；privileged/stub 零请求。
`pricing_configured=False`，无单价编数不合法。产物 `/home/czx/embodied-agent-batches/v04/e2/`，
批级摘要 `e2_prime_report.json` 由驱动逐格落盘。

---

## P4（二）前的更正：MEM-1' 增补的范围写反了，已按盘上证据改写

第一版增补写的是"补缺席的第 3、4 对"。回 `v03/mem1_model` 盘上核对：
`pairs_run.json` 覆盖**全部四对**（模型席），而 `episodic_memory_MEM_1.json` 的
`pairs_ran` 只有 [em_p3, em_p4]——因为 **em_p1/em_p2 的对照臂 reader 集缺失**
（各 0 集；其余 14 个槽位各有 1 集）。P6 报告压缩成"2/4 对"的实际含义是
"两对在读数层面各缺一个对照集"。增补已改写为：**跑 em_p1/em_p2 的
wo_episodic_memory reader（连带 writer，对照臂无库、对 v0.3 存储无副作用），
上界 100 请求**，四对读数按引用并表，已付费集不重跑。

---

## P4（二）：MEM-1' 读数（2026-09-30，SPEC §7 MEM-1'，增补 `v04_mem1_amendment_1.json`）

**48 / 100 请求，4 集全部 success，一次停止规则未触发**：
em_p1/em_p2 的对照臂（wo_episodic_memory）writer+reader 在模型席上补齐——
- `em_p1_write_two` success 5 轮 7 请求；`em_p1_read_three` success 8 轮 18 请求
- `em_p2_write_two` success 5 轮 6 请求；`em_p2_read_three` success 8 轮 17 请求
- 身份：planner deepseek、model agnes-2.5-flash、dirty=False、manifest 感知键 privileged（R2 逐格相符）

至此**四对 × 两臂的 16 个槽位全部有集**（v0.3 根 14 + v0.4 根 2 的对照补集）。
对照臂零库设计使 v0.3 的两个库未受触碰（arm_coherence 由各自 pairs_run.json 记录）。

**待 P5 的并表**：四对 M1–M4 的重印需要一台跨根读数仪器（v0.3 根的 full 臂 + p3/p4
两臂，v0.4 根的 p1/p2 对照臂，按引用并表不重跑）——预写死的读法不变：n=4 上与随机
不可分即交付 Wilson 区间不交付结论。这不在本轮：它属于 P5 报告仪器，且 D5' 未决
不影响它。

**v0.4 至此花费合计**：探针 15 + E2' 132 + MEM-1' 48 = **195 请求**（两免费端点的
agnes 席；全部免费层，无单价，无美元列）。

---

## D5'/D6' 裁定落账（2026-09-30，用户裁定"按推荐执行"）

- **D5'**：E1' = **60 集**（long_horizon 全集 × arm `full` × repeats 10），目标 L2 两位数触发；
  上界 **2,340 请求**（60 × 31.1 × 1.25）。L6 的跨席同分母由 **rule 席同格 60 集**补齐
  （零请求）。分块：5 块 × repeats 2（每块 12 集 ≈ 370 请求），块级断点——击杀最多损失一块。
- **D6'**：sensenova 真实载荷**重测一次**（判据见下）；合格 ⇒ E1' 以**格（块）为粒度**
  两席轮用（批内单席原则的块级落实：每块一个席位，逐块 manifest 记身份，读数按席位分层）；
  不合格 ⇒ agnes 单席跑完 60 集。

### sensenova 真实载荷重测判据（先于请求落盘）

- 载荷：决策级提示按 **~45k prompt tokens** 定标（MEM-1 模型席实测的 em 族 decide 轮
  每集 prompt 均值 45,855——本项目在盘的最接近 E1' 决策轮的真实尺寸）；
- 档位：max_tokens ∈ {2048, 4096, 8192}（配置默认 1200 不在档内，按调用覆盖）；
- **合格** = 任一档 `finish_reason=stop` 且正文非空；全档 length/0 字符 ⇒ 不合格；
- 花费 ≤ 3 次请求；读数跑后不改判据。

### D6' 重测读数（2026-09-30 当场跑，判据见上）

**合格**：payload 实测 prompt **45,129 tokens**（608 轮合成块），三档 max_tokens
{2048, 4096, 8192} 全部 `finish_reason=stop`、正文 34 字符、reasoning 300–418 字符、
墙钟 4–10 s。P0' 的 0 字符死法在 45k 尺寸上**未复现**。

**适用域照写**：合成载荷的内容是"读轮数、答 ok"——尺寸是真实的（45k），推理需求
不是。sensenova 的真实决策轮健康度由它在 E1' 里的第一个实际块证明。

**席位计划（对 D5' 选项原文的一处收窄，先于第一个请求落账）**：选项原文说
"合格则两席分格轮用"；落预登记前发现**对半轮用会稀释 L2 的每席触发分母**
（~10 个预期触发对半成每席 ~5，恰好毁掉"两位数触发"这个批的存在理由）。
收窄为：**agnes 主席跑满 60 集；sensenova（新配置 `sensenova-text.yaml`）仅作
块界故障切换席**——quota-stop 在块界触发后，下一块可换席（逐块 manifest 记身份，
读数按席位分层）。收窄理由在此落账，不在报告里被发现。

E1' 预登记：`configs/experiment/v04_e1_preregistration.json`（60 集、5 块 × 2 repeats、
上界 2,340、rule 席同格 60 集零花费对照）。

---

## P4（三）：E1' 的 rule 席半边读数（2026-09-30，零花费，L6 跨席同分母）

**60 集（lh 全集 × arm full × repeats 10，rule 规划器、特权态）**：**50/60 success、
10/60 failed**——失败**全部**是 `lh_c6_two_disruptions` 的 INVALID_DECISION，10 次重复
**确定性**失败：rule 规划器过不了 c6 的任务结构。与接地无关（特权态不走接地图），
也与模型无关（这一半就是 rule 对照）。其余五格 case 10/10 全胜。

- **L6 的 rule 席分母就此成立**：60 集、50 成功——与模型席 60 集（跑批中）同分母对照。
- **一条顺带的干净负结果**：c6 在 rule 席上 0/10。v0.3 的 full 格 12 集里 c6 只出现 2 次
  （当时 2 failed 之一），repeats=10 把它从"个案"变成"确定性失败"——rule 规划器对
  双扰动任务结构的无能第一次有了确定的分母。c6 也是重复颜色 case，但它在这一格的失败
  与颜色无关，不得读成 R3 的读数。
- **身份**：manifest `dirty: true`——批于 13:37 落 manifest 时工作树里有未提交的
  `work/v04/mem1_join.py`（当时正写 MEM-1 并表仪器）；`dirty_diff_sha256` 已把
  这份差钉住，产品代码零改动。13:44 起的模型席批 manifest `dirty: false`（5ca1495b）。
  不重跑：零花费批、身份可完整复原。

---

## P4（四）：E1' 模型席批——按停止规则止于第 3 块，读数改写了规模裁定（2026-09-30）

### 批的执行与停止

34/60 集（blocks 1–3），实际花费 **700/2,340 请求**（逐块 batch_spend 重导：265/155/280，
goal-parse 各 21/12/26），prompt 合计 4.83M。结局：25 success / 9 failed /
2 goal_resolution_error（34 摘要 + 2 目标解析死行 = 36 计划行）。

- block 1（agnes）：11 集、8 次 429 连死 → **故障切换武装**（预登记的块界换席第一次真实触发）；
- block 2（sensenova-text）：**12 集零配额死亡、rc=0**——故障切换席的第一个真实块即健康；
- block 3（agnes，切换席已用尽回主席）：8 次 429 连死 → **停止**（无席可换）。

**驱动的一个真 bug，当场修正记录**：`batch_spend(cell)` 传的是格子目录而它要 run 根
（差一层 `<run_id>`），于是累计花费全程显示 0、**上界守卫从未真正武装**——批是被
独立的配额停止规则拦下的，不是被上界拦下的。修正后的重导即本节的 700/2,340。
（教训同族于 P5' 的"两账本一起读"：这次是**读数的目录深度**错了，账目数字本身
逐块落盘所以能事后重导。）

### LH-2 重跑（34 集）与 RQ9 的两个答案

- **L2：触发率 1/34（run 1 的 1 个触发；run 2/3 零触发——"零依赖边被写下"）**。
  v0.3 的 2/12 触发率是小样本高估；按 1/34 的实测率，两位数触发需要 ~680 集，
  **两位数目标在任何可负担规模上都不可达**（原计划的 60 集也只给出 ~2 个）。
- **L6（模型席）**：三块合并 **0.5（10/20）**——agnes 6/11 与 3/5、sensenova 1/4。
  与 v0.3 的 0.5789 (11/19) 同域，与随机不可分（p=1.0）。
- **L6（rule 席，60 集不同场景）**：**0.5（10/20）**、recovery_effectiveness 1.0 (20/20)、
  L2 结构性 0/0（rule 从不写依赖边，与 v0.3 一致）。repeat 指数变种子已验证
  （c1 r0 ≠ r1），所以这是 60 个不同场景上的真读数。
- **RQ9 的 L6 半边就此有了决定性负答案：跨席 0.5 vs 0.5，无可分之差**。
- **一条未决差异，如实记录不调和**：v0.3 报告引用的 v0.2 时代 rule 读数是 L6 0.0 (0/4)；
  今天同一 LH-2 版本、同一冻结集、planner 零改动测得 0.5 (10/20)。v0.2 时代的
  LH-2 产物在 /tmp 已灭失（v0.3 报告 §0 引的 /tmp/p5c/lh_score），无法回产物核对；
  候选解释（v0.2 的 mode-B 决策环与今日 RulePlanner 本就不是同一实现）写进报告，
  不算作已解决。

### 停在 34 集的裁定（不为上界而花钱）

原计划的 blocks 4–5 还值不值 1,640 请求：L2 预期再得 ~1 个触发（两位数目标已证不可达）、
L6 分母 20→35（0.5 vs 0.5 的结论不会变）。**上界是为目标服务的，不是为了被花掉**——
§12 的兜底条款（交付"预登记规模 × 实达规模"差值表与原因分桶）正是为这一刻写的：

| 项 | 预登记 | 实达 | 差因 |
| --- | --- | --- | --- |
| 模型席集数 | 60 | 34 | agnes 免费档配额两窗口各 8 连死；切换席只用一次（预登记字面） |
| L2 触发数 | ~10（两位数） | **1** | 触发率实测 1/34，D5' 的 2/12 估计被大样本修正 |
| L6 模型席分母 | ~35 轮 | 20 轮 | 同上（集数差） |
| L6 rule 席分母 | 60 集 | 60 集 | 达成 |
| 请求 | ≤2,340 | 700 | 34 集即停 |

恢复路径保留：断点设计完好，`--resume` 一条命令即可从 block 4 续跑（需先清 stopped
标记），但本日志的裁定是**不恢复**——除非用户推翻。
