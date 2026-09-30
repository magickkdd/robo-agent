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
