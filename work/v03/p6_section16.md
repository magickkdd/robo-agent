
## 16. P6：补齐交付时唯一没合上的那一行

v0.3 交付时 §13.2 第 2 行是**部分达成**，唯一原因是结构性的：`cli em-pairs` 没有 `--planner`，
而 §11 的 Memory 组只在**配对协议**上有定义，于是 M1–M4 在模型决策源上**一条读数都没有**。
P6 补上这个席位并跑了 **MEM-1 模型席**。预登记在请求之前落盘并提交
（`configs/experiment/v03_mem1_preregistration.json`，界 **411** 请求；**文件里没有一个手打的
数字**——每集成本、投影、上界全部从 E1 自己的账本用 `batch_spend` 读出）。

### 16.1 读数

**MEM-1（2/4 对；`em_p1`/`em_p2` 具名缺席，带 429 原文）**

| 行 | 模型席 | rule 席（对照，P6-b 排练） | 口径 |
|---|---|---|---|
| `M1.rows_per_round` | **1.0 (15/15)** | 1.0 (28/28) | 逐轮检索记录 |
| `M1.matched_beyond_the_batch_name` | **1.0 (15/15)** | 1.0 (28/28) | 同上 |
| `M1.rows_refuted` | **0.333 (5/15)** | 0.286 (8/28) | 同上 |
| `M1.distinct_rows_vs_store` | **1.0 (2/2)** | 1.0 (4/4) | 同上 |
| `M2.trajectory_changed_and_completed` | **0.0 (0/2)** | 0.5 (2/4) | 行为，与席无关 |
| `M2.outcome_improvement` | **0.0 (0/2)** | 0 (0/4) | 行为，与席无关 |
| `M3.treatment_worse_or_costlier` | **0.5 (1/2)** | 0 (0/4) | 行为，与席无关 |
| `M3.decline_branch_reachable` | **0.0 (0/2)** | 0.0 (0/4) | store 形状，与席无关 |
| `M2.followed_round_share` | **结构性 not-measured** | 0.5 (14/28) | 读 policy trace |
| `M2.followed_where_control_chose_the_same` | **结构性 not-measured** | 0.429 (6/14) | 读 policy trace |
| `M3.declines_per_refuted_round` | **结构性 not-measured** | 0 (0/8) | 读 policy trace |
| `M4.refuted_and_still_governing` | **结构性 not-measured** | 1.0 (8/8) | 读 policy trace |
| `M4.uncheckable_and_governing` | **未产生**（无对声明不可查） | 0.571 (4/7) | 读 policy trace |

**读法**：模型席上**检索是真的**（M1 四行全部有读数），但**没有可归因于记忆的行为差异**，
而**唯一一次出现的差异朝反方向走**（`M3` 0.5，1/2 对）。n=4 里只有 2 对：
**这是存在性证明，不是样本**——MEM-1 自己的 `unit_note` 一直这么写。

**两个席的表不可直接相比**，这一条写进产物而不是只写在这里：模型席的 reader 是随机的，
两臂差在**记忆 + 抽样**；rule 席的 reader 是 `MemoryPolicy`，两臂**只**差在记忆。
`pairs_run.json`、`pairs_measured.json` 与 `episodic_memory_MEM_1.json` 三处都带
`planner` / `policy` / `model_config` 与一条 `seat_note`。

### 16.2 这批自己撞出来的三件事

**一、停止规则报了一次"界内通过"，而它什么都没量到。**第一版按"我传给 `run_group` 的那个
目录"找账本，而 `run_group` 在它下面建带时间戳的子目录；glob 找到空树、求和 0、打印
`spent 0, completed within bound`。这批**确实**在界内（**216 / 411**，余量 195，均值投影的
91.1%），所以**界是靠投影的运气守住的，不是靠检查**。已改为按每批**自报的 run root** 读，
且**读到 0 一律拒绝继续**——零读数是检查的失败，不是预算的胜利。与残留 13 同类：读数指错了
地方。区别是**那个报成功**。

**二、`M2.followed_round_share` 读出 `0/15`，那是编出来的负数。**那一行读
`episode_summary.policy.trace`，由 `MemoryPolicy` 逐轮写下；模型席上 `policy=None`，
**没有 policy 跑过，那条 trace 没人写**。实测：rule 席 **16/16** 集摘要带 `policy` 块
（100 条 trace，14 条 `memory_followed` true），模型席 **0/14**。**0 读起来像证据，缺席的仪器
不像**——这是本项目最危险的形状。而且不止两行：**5 行**读同一条 trace，把 M3/M4 报成 0
是同一个编造的负数换了个名字。被抑制的格子**保留它本来在数什么**（`suppressed_as`），
否则读者分不清"真的是 0"和"这个席没资格数"。

**三、我把 v0.2 一条有意做的决定反转了，然后才发现。**v0.2 的契约写着"账本说跑了但盘上没有
那一集，**必须拒绝**，不能拿活下来的配对去算一个率"。我第一版把 manifest 行直接当成运行，
等于用**悄悄缩小分母**换掉了它防的东西——正是它要防的那个失败。两种顾虑都成立，而状态
**可以分辨**，所以现在是三态：

| 状态 | 判据 | 处理 |
|---|---|---|
| **跑了** | 有 episode 目录 | 进分母 |
| **失败且有记录** | 根目录在、无 episode、**目标账本写了错** | 缺席，**并带上那个错**（429 那一批） |
| **不明** | 根路径压根不存在 | 交给 reader；生产里 `read_pair` 会带路径名拒绝 |
| **损坏**（不是状态，是拒绝） | 根目录在、无 episode、**什么都没记** | **拒绝**——v0.2 的拒绝原样保留，只收窄到它本来针对的**静默**情形 |

MEM-1 那 2 集丢在第二种：目标解析五次 429 全败，所以根目录在、目标账本里有
`HTTP Error 429: Too Many Requests` 与 `transport_attempts: 5`、没有 episode。

### 16.3 花费

| | 值 |
|---|---|
| 集 / store | 16 集（计划 16）/ 8 |
| 实际拿到 episode | 14（2 集丢在目标解析的 429 上） |
| **请求（发出）** | **216** / 界 411，余量 195 |
| 请求（拿到回应） | 186 |
| 什么都没返回 | **30**，跨 6 个错误行 |
| prompt / completion | 553,603 / 12,262 |
| 界怎么定的 | E1 实测逐集请求均值 14.81、最坏格 17.13；最坏格 × 16 × 1.5 = **411** |
| 为什么取最坏格 | E1 有 707/3,292（21.5%）的请求什么都没返回，**限流才是决定这批拿到多少集的东西**；均值是 Mostly 在躲过限流的格上算出来的 |
| （USD） | **`null`**，`pricing_configured: false` |

### 16.4 留着的

- **E2 的 `full`-on-VLM 分母仍是 2 对 4**（§9 残留 2）：相机通道对 `lh_c3` 不可运行，未修。
- **E1 的 `L2.dependency_edge_violation 2/2`（n=2）与 `wo_replanning 9 > 8`（n=12）** 未追。
- **这一行本身的残留**：n=2 对、且 2 对因限流缺席，所以它**不足以**支持任何关于
  "模型用不用记忆"的肯定结论；它支持的是"**这条通道在模型席上可测**"。
