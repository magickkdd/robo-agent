# BST-1.0 文本批次：可执行命令清单（SPEC-BST §12 交付要求）

每条命令都写全**配置文件**与**输出目录**，都是本仓库实际跑过或将实际跑过的那一条；
参数名与 `embodied_agent/benchmark/cli.py` 一致。机器产物一律落在仓库树外（`/tmp/bst_v01/`），
人类交付物在 `docs/bst/`，冻结时由 `run` 复制进 run root。

前置：工作目录是仓库根；密钥只在环境变量或仓库内被 gitignore 的 `.env` 里
（`cli.py` 自己读它，命令行里不出现任何密钥，产物只记 `api_key_env` 的**变量名**）。

```bash
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
```

## 1. 环境自检（Phase 0）

```bash
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.cli env-check \
  --data-dir /home/czx/.cache/alfworld \
  --out /tmp/bst_v01/environment_check.json
```

产出 `environment_check.json`：python/包版本、`pip_freeze_sha256`、两个官方数据 zip 的
url+sha256+bytes、逐 game 的 `game_tw_pddl_sha256`、六类计数、真实 playthrough、
`licenses`、`errors`、`exit_criteria` 四项。

## 2. 任务清单与运行顺序（Phase 1/2）

```bash
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.cli tasks \
  --data-dir /home/czx/.cache/alfworld \
  --out /tmp/bst_v01/task_manifest.json
```

产出 `task_manifest.json`：`dev_train.slots`(12) / `order.screening_slots`(48) /
`order.remainder_slots`(220) / `segments` 段说明，以及 `selection_seed 20260919`、
`run_seeds [20260919, 20260920]`。

## 3. 12 集开发校验（Phase 2，train 任务，不入任何分母）

```bash
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.cli run \
  --segment dev \
  --task-manifest /tmp/bst_v01/task_manifest.json \
  --model-config configs/models/bst_text_agnes.yaml \
  --environment-check /tmp/bst_v01/environment_check.json \
  --compatibility-delta docs/bst/compatibility_delta.md \
  --run-root /tmp/bst_v01/dev2
```

单集闭环（同一命令的 `--single`）：把上面末尾换成
`--run-root /tmp/bst_v01/single --single 0`。

## 4. 48 集筛查（Phase 3，8,000,000 token 硬顶）

```bash
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.cli run \
  --segment screening \
  --task-manifest /tmp/bst_v01/task_manifest.json \
  --model-config configs/models/bst_text_agnes.yaml \
  --environment-check /tmp/bst_v01/environment_check.json \
  --compatibility-delta docs/bst/compatibility_delta.md \
  --run-root /tmp/bst_v01/screen
```

段上限是代码常量（`SEGMENTS`），不由命令行提供；`--token-cap` 只用于预检，
判分批次不得覆盖。首跑会写 `manifest.json` / `frozen_config.json`（provenance），
再跑不会重写，除非显式 `--refreeze`（那是**新的**一份记录，不是编辑旧的）。

## 5. 268 集全量与续跑（Phase 3，40,000,000 token 硬顶）

```bash
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.cli run \
  --segment full \
  --task-manifest /tmp/bst_v01/task_manifest.json \
  --model-config configs/models/bst_text_agnes.yaml \
  --environment-check /tmp/bst_v01/environment_check.json \
  --compatibility-delta docs/bst/compatibility_delta.md \
  --run-root /tmp/bst_v01/full
```

续跑 = 同一条命令再执行一次：`progress.json` 里 `resumable_complete` 的槽位被跳过而不是重跑
（`already complete, not re-run`），基础设施失败的槽位最多补一次（`--fresh` 才会无视账本重来，
本清单不使用它）。

## 6. 离线聚合（Phase 3–5，不再调用模型、不再运行环境）

```bash
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.cli aggregate \
  --run-root /tmp/bst_v01/screen \
  --task-manifest /tmp/bst_v01/task_manifest.json
```

`--task-manifest` 可省：run root 内有自己的副本（§10.4）。产出 `metrics.json`、
`metrics_by_task_type.csv`、`failure_candidates.jsonl`；`planned/started/valid/infra/not-run`
五分母、四个头条率、cluster bootstrap 95% CI、成本分布、以及两条独立的重算路
（`recompute_mismatch` 与 `cross_check_against_ledger.provider_cumulative_series`）。

## 7. 回归（每次改动后）

```bash
# 桌面 v0.1 回归
PYTHONPATH=. /home/czx/miniforge3/envs/embodied/bin/python -m pytest tests/ -q
# 文本离线套件（bstvenv；Pillow 那条桌面依赖断言显式 deselect，见 compatibility_delta 第 6 节）
PYTHONPATH=. /home/czx/bstvenv/bin/python -m pytest tests/unit tests/contract tests/integration -q \
  --ignore=tests/integration/test_bst_alfworld_live.py \
  --deselect tests/unit/test_event_log_safety.py::test_the_physics_dependency_version_is_the_installed_one
# 真实引擎 live
PYTHONPATH=. /home/czx/bstvenv/bin/python -m pytest tests/integration/test_bst_alfworld_live.py -q
# 决策机制哈希必须仍是 23abe58ff33372ffa6f62a608221dfc1af2006f85ae50f7651a0f28451aae7b3
PYTHONPATH=. /home/czx/bstvenv/bin/python -c "from embodied_agent.benchmark.runner import _live_rules_sha256 as f; print(f())"
```

## 8. 退出码

| 码 | 含义 |
|---|---|
| 0 | 批次跑完（**哪怕每一集都失败**：低成功率是被测对象，不是命令错误） |
| 3 | 配置错误（缺清单、缺段名、`--single` 不在该段） |
| 4 | 批次被硬性停止：token 上限、模型身份守卫，或一个未闭合的基础设施工位 |

## 9. 标注、二次复核与判据重哈希闭环（Phase 3d–4）

标注器与复核工具在仓库外（`/tmp/bst_v01/scratch/`），因为它们读的是体量不入 git 的 episode 日志；
但**判据在仓库内**（`docs/bst/annotation_guidelines.md`），它的哈希进证据包头，所以命令写在这里。
生成/校验一律 `bstvenv`；只做文本 diff 的核对脚本用 `miniforge3/envs/embodied` 也行。

```bash
cd /home/czx/embodied-agent-robot-agent-embodied-agent-4
R=/tmp/bst_v01 ; S=$R/scratch ; A=$R/annotations

# 9.1 证据包（每台一行偏差；cap 4 = §10.3 的纳入规则；头部写入判据文件哈希）
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.annotate worklist \
  --run-root $R/full --segment full --out $A/worklist_full.jsonl --cap 4
PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.annotate worklist \
  --run-root $R/screen --segment screening --out $A/worklist_screening.jsonl --cap 4

# 9.2 人工标注 + 停机轮的结构规则（BST_TERMINAL_RULE=1；不加则停机轮走手工表）
BST_TERMINAL_RULE=1 /home/czx/bstvenv/bin/python $S/make_annotations.py \
  $R/full $A/worklist_full.jsonl $A/annotations_full.jsonl
BST_TERMINAL_RULE=1 /home/czx/bstvenv/bin/python $S/make_annotations.py \
  $R/screen $A/worklist_screening.jsonl $A/annotations.jsonl

# 9.3 §9.3 最小证据门槛：invalid / unlabeled 必须为空，否则退出码 4
for f in annotations_full annotations; do
  PYTHONPATH=. /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.annotate validate \
    --annotations $A/$f.jsonl > $A/validate_$f.json; echo "$f exit=$?"; done

# 9.4 二次复核（§10.3，单位是 episode，seed 20260919，>20%）
/home/czx/miniforge3/envs/embodied/bin/python $S/make_second_review.py draw $A/annotations_full.jsonl \
  $A/second_review_sample_full.json                       # 重算抽样，不用记忆的名单
/home/czx/bstvenv/bin/python $S/make_second_review.py worksheet $A/annotations_full.jsonl \
  $A/second_review_worksheet_full.md $A/second_review_sample_full.json
# 一批一批读：spec 里写短柄，解析成全键并拒绝歧义（0 个或 >1 个行匹配都失败）
/home/czx/miniforge3/envs/embodied/bin/python $S/verdict_batch.py $A/annotations_full.jsonl \
  $A/second_review_sample_full.json $S/rv_batch_07_spec.json $S/rv_batch_07.json
/home/czx/miniforge3/envs/embodied/bin/python $S/merge_verdicts.py $A/annotations_full.jsonl \
  $A/second_review_sample_full.json $S/rv_keys_01.json $S/rv_batch_{02..11}.json   # stray 键致命
/home/czx/bstvenv/bin/python $S/make_second_review.py stamp $A/annotations_full.jsonl x \
  $A/second_review_sample_full.json $A/second_review_verdicts_full.jsonl
# 筛查段的判决写在 make_second_review.py 的 VERDICTS 表里（行数小），同一 stamp 不带第 4 参数

# 9.5 核对用的小工具（全部只读 episode 产物）
/home/czx/miniforge3/envs/embodied/bin/python $S/show_row.py slot174 both      # 行 + 持久句子
/home/czx/miniforge3/envs/embodied/bin/python $S/check_devs.py $R/full $A/worklist_full.jsonl 174 25
/home/czx/miniforge3/envs/embodied/bin/python $S/verify_absence.py $R/full slot035   # 类是否整集未出现
/home/czx/miniforge3/envs/embodied/bin/python $S/probe_no_effect_causes.py $R/full   # "Nothing happens." 的形状
/home/czx/miniforge3/envs/embodied/bin/python $S/probe_near_miss.py $R/full $A/worklist_full.jsonl
/home/czx/miniforge3/envs/embodied/bin/python $S/re_read_digest.py $A/annotations_full.jsonl \
  $A/second_review_sample_full.json 0 40                                       # 分页读抽样行
```

**判据一改，必须重跑这条链**（判据哈希在证据包头里，也在校验输出里）：

1. 追加 `annotation_guidelines.md` 的小节（**只允许追加**，§6 的纪律）；
2. 按字节存一份当时的判据：`cp docs/bst/annotation_guidelines.md
   $A/guidelines_bytes_at_<新哈希前12位>.md`，`sha256sum` 要等于 `annotate worklist` 头部与
   `annotate validate` 输出的那个值——没有这一步，记录下来的哈希事后无法复算（§6.7 第 7 条）；
3. `annotate worklist` 重建两段 → **逐行 diff 载荷**，只允许 `written_at` 与 `guidelines.sha256`
   两个头部字段变，行集合必须相等（D39 就是这么自证的）；
4. `make_annotations.py` 重跑两段 → 与旧存档逐键 diff，把改动面写成数字（多少行、哪些字段、
   有没有动标签）写进判据与 `compatibility_delta.md`；
5. `stamp` 重盖判决（抽样键不变才可原样重盖；`stamp` 拒绝对已有判决的行二次覆盖）→
   重跑 `worksheet` → `annotate validate` 的 `invalid`/`unlabeled` 为空；
6. 更新 `$A/guidelines_provenance.json`（哈希史 + 每段行数/标签/判决计数）。生成器自己的改动
   （如 D36 的句子拼接、D40 的形类计数门）同样走这条链，且**不得**顺手改任何标签定义或阈值。

