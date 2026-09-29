
## 18. §18 把 `full`-on-VLM 的分母算对，并把颜色那条的归属改掉

§9 残留 2 写的是"`full`-on-VLM 的分母是 2 而 `privileged` 是 4"，交付文本里则处处写
"**privileged 4/4 → vlm 0/4**"。P6 回到 E2 的产物上把这件事重算了一遍，**三处要改**。
零花费：全部是 E2 已在盘上的 `run_summary.json` / `episodes.csv` / `episode_summary.json` /
`errors.jsonl`。

### 18.1 匹配分母是 2，而"4/4 → 0/4"比的是两套不同的集

每格 **4 行**（`planned: 4, rows: 4`），两 case × 两 repeat：

| case | vlm | stub | privileged |
|---|---|---|---|
| `lh_c1_shared_pair_restore` r0/r1 | **failed**（6 轮、1/4 物体、`REPEATED_INVALID`） | **failed**（同） | **success**（11 轮、4/4） |
| `lh_c3_capacity_and_shift` r0/r1 | **`infrastructure_error`**（无摘要、无错误条目） | **同** | **success**（13 轮、6/6） |

所以：非 privileged 两通道**真跑出结果的是 c1 的两集**；c3 的两集**没有 episode_summary，
`errors.jsonl` 里也没有任何条目指向它们**。

| 口径 | vlm | stub | privileged |
|---|---|---|---|
| 交付文本写的 | 0/4 | 0/4 | 4/4 |
| **`run_summary` 的 outcomes** | `{failed: 2, infrastructure_error: 2}` | 同 | `{success: 4}` |
| **匹配分母（只有 c1）** | **0/2** | **0/2** | **2/2** |

**匹配集上仍然是 0**，所以"相机通道净负"这个结论**没有被这次更正削弱**；被削弱的是
**"4/4 → 0/4"这个说法本身**——它把一个 4 集的对照和另一个 2 集的比值放在一句话里，
读起来像同一批集上的 100% 落差。

### 18.2 颜色那条，三个说法都要改

交付文本写的是"`lh_c3_capacity_and_shift` 声明了两个黄色物体……而相机通道按颜色给身体命名，
`GroundingMap.from_objects` 因此拒绝 ⇒ **E2 选的两个 case 里有一个在相机通道上根本跑不起来**"。

盘上的证据是：

```
colour 'yellow' is declared for both obj_yellow_1 and obj_yellow_6:
a frame names bodies by colour, so this declaration cannot be grounded by that name
(P1-b verified colour uniqueness over all 47 frozen cases)
```

1. **重复颜色的 case 是 c1，不是 c3。** 错误点名的两个物体在 c1 的场景里；而 c1 的两集
   **跑起来了**——各 6 轮、完成 1/4 物体、以 `REPEATED_INVALID` 失败。所以颜色那条
   **没有让任何一集"跑不起来"**。
2. **不止 vlm 格。** `stub` 格记录 `channel: stub`、`looks: 26`、`look_http_requests: 0`
   ——它同样走相机臂那张接地图，所以同样撞上这条拒绝。`privileged` 格 `looks: 0`、
   `views: null`，才绕开。**成因是"非 privileged 通道共用的接地图"，不是"相机"泛指**；
   原文那句会让人去改相机臂，而 stub 也坏意味着改相机臂**不**解决它。
3. **真正没有产出的是 c3，而它至今没有任何原因记录。** 两集
   `infrastructure_error`、无 `episode_summary`、`errors.jsonl` 里**没有一条提到 c3**。
   **这是一个错误账本的洞**，与残留 1（`wo_vlm` 的 ablation 记录缺失）、残留 3
   （`manifest.offline` 只看 planner）同一族。**它比颜色那条更值得先修**——因为
   "为什么这一集没有结果"目前**无法回答**，而颜色那条至少还留下了一句原文。

顺带一条新发现，也属残留 3 那一族：**三个 manifest 都没有 `perceive` 键**
（`{"cases": [...], "planner": "rule", "offline": ...}`）。所以这一节里通道身份是从
**每集摘要的 `perception.channel`** 读的，不是从 manifest 读的——而 manifest 恰恰是
"这批跑的是什么"该被记录的地方。

### 18.3 三条能走的路，只有一条不花钱

* **换掉或改 c3 的颜色声明** —— 那是**改冻结任务表**，属重冻结与 D 级决定，不塞进报告；
* **给相机臂一个不靠颜色的身体命名通道** —— 产品改动，且 stub 也走这条路，所以要改的是
  共用的接地图而不是 vlm 适配器；
* **按匹配分母报** —— 零花费，就是 §18.1。

本节做第三条，并把前两条留成明确的下一步。**不替 SPEC 做重冻结的决定。**

### 18.4 新的残留

| # | 内容 | 状态 |
|---|---|---|
| 18 | **c3 的两集 `infrastructure_error` 且错误账本里没有任何原因。**`run_summary` 记了，`errors.jsonl` 没记 | **未修，且比颜色那条更该先修**：`planned` 里有、`rows` 里有、结果没有、原因没有。修它需要让 `infrastructure_error` 这一类**必须留下一个可指的原因** |
| 19 | **三个 manifest 都没有 `perceive` 键。**通道身份只能从每集摘要反查 | 与残留 3 合并修：manifest 应当记下这批的感知通道 |
| 20 | 颜色拒绝在 stub 与 vlm **两格**都发生，成因在共用接地图 | 未修：需要一条不依赖颜色的命名通道，或把该性质在 `long_horizon` 集上验证并写进冻结集的前提 |
