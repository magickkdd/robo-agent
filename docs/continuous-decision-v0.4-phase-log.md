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
