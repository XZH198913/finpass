# 日志层追认：把 `changes` 翻译成人话

## 一、目标

4c 之后引擎已经能自动决策，但终端只说 `[missing_token] event_id=… 影响 1 行 决策=fix`：
读得懂的人只有作者。日志层要解决的是**一个没读过 README 的人，能不能从终端复述引擎做了什么**。

不是增加日志行数，不是美化输出，是补上「做了什么 / 在哪 / 为什么 / 改成什么」四件事。

## 二、验收标准

### 标准 1（机械层）：四要素

| 要素 | 含义 | 反例（4b/4c 的终端） |
|---|---|---|
| 动作 | 去重 / 填充 / 换算 / 归一 / 裁边 / 打标 | `[duplicate_key]` —— 不知道在干嘛 |
| 对象 | 在哪列 / 哪些行 | `影响 1 行` —— 不知道哪列 |
| 依据 | 发现重复 / 含占位符 / 千分位 / 越界 / 矛盾 | `决策=fix` —— 不知道凭什么 |
| 结果 | 保留首条删 N 行 / 填充 NaN / 元 / dtype + 格式 | `event_id=…` —— 不知道改成什么 |

缺一即不可读。判据：`tests/test_narrate.py::test_every_line_has_the_four_elements`
与 `tests/test_stage2_event_log.py::test_every_log_line_follows_the_readable_format`
（前者在模块层，后者跑真实进程）。

### 标准 2（人工层）：复述测试

把终端输出给一个没读过 README 的人，能否复述出：

> 「它检查了 5 条规则，1 处主键重复没敢动、打了标；2 行占位符填成了空值；
> 日期按 `%Y-%m-%d` 转成了时间；成交额有 1 行单位可疑也打了标；还有 1 行 OHLC 矛盾打了标。」

**这条 agent 不能自查**，必须由人做。实测复述（用户视角，2026-11-05）：

> 引擎检查了 5 条规则。去重发现 1 行重复但没删（默认路径不自动改，打标）；
> 收盘价和成交额有 2 行是占位符，填成了空值；成交额有 1 行单位可疑，打标没改；
> 日期格式不对的 1 行转成了标准日期；最高/最低/收盘价有 1 行对不上，打标没改。
> 自动改了 3 处、打标保留 3 处、退出码 2。想复核这 3 处 flag 为什么打标，看 `events.jsonl`。

判定：**A 档**——Q2 / Q3 / Q7 从 no 变 yes。

复述时卡住的两处，已分别落成 §八 的两条记录（问题数与规则数两套口径、flag 的真实原因是默认路径保守）。

### 标准 3（一致性）：日志是 `changes` 的翻译

日志里的数量、列名、变更条数**只从 `changes` 取**；`events` 只提供 `reason`
（人工决策或降级通道写下的原因）；单位 / 格式 / 边界 / dtype 只从 `profile` 取。
三者不一致时以 `changes` 为准——判据：`test_log_lines_reconcile_with_changes`。

## 三、落地形态

| 项 | 内容 |
|---|---|
| 新增模块 | `src/finpass/narrate.py`（模板表 + 推导 + fallback，约 330 行） |
| 调用点 | `cli.py::_print_event_log` 一处（渲染全在 narrate，cli 只负责打印） |
| 不碰 | `engine.py` / `session.py` / `profile.yaml` / 任何产物 |
| 测试 | `tests/test_narrate.py` 8 条 + `tests/test_stage2_event_log.py` 5 条（改写 + 新增首行口径 1 条） |

### 关键决定

1. **日志不带 `event_id`**：审计标识留在 `events.jsonl`，末行
   「详细决策见 events.jsonl（按 rule_id 可直接检索原事件）」把两条路径接上。
   这是分层，不是放弃可追溯。
2. **一行 = 一个 `(rule_id, 动作)` 分组**，不是一条 event：`missing_token` 在
   「收盘价」「成交额_元」各 1 行 → 合并成一行说清两列。
3. **模板键是 `rule_type` 而不是 `rule_id`**：`unit_ambiguity`（type=`unit_scale`）这类
   自定义 id 才能命中同一份句式；id 只用于查 type，查不到就按 id 当 type。
4. **目标态查列时「数据列优先，退到全组（含主键）」**：日期归一改的正是主键列「日期」，
   只查数据列会拿不到 `format`/`dtype`；反过来，展示列必须排除主键，
   否则 OHLC 会把「日期」写成矛盾列。
5. **fallback 不静默**：新增规则忘写模板 → `[rule_id] N 条变更（暂无日志模板，详见 events.jsonl）`，
   不崩、不报错、也不退化成 event 格式。

## 四、契约变更（必须记录）

`tests/test_stage2_event_log.py` 原判据是「终端每行带 `event_id`，可逐行对回 `events.jsonl`」。
日志层去掉 `event_id` 后该判据失效，改写为：

| 旧判据 | 新判据 |
|---|---|
| 每条日志行匹配 `event_id=<hex> … 决策=fix\|flag` | 每条日志行四要素齐（标签 / 对象 / 依据 / `→`） |
| `event_id` 集合 == `events.jsonl` 的 `event_id` 集合 | 每个有变更的 `rule_id` 都有对应标签行，且行数与 `changes` 一致 |
| 逐行反查 `events.jsonl` | 终端不出现 `event_id`（反查改走产物，末行指路） |

**这是契约变更，不是放宽**：逐行可追溯仍在（`events.jsonl` 原样保留、`rule_id` 可检索），
被替换的是「终端承担审计职责」这一条——那条与「用户可读」直接冲突。

> 终端不再带 `event_id`，是审计职责从终端迁移到 `events.jsonl`，不是可追溯性下降。
> `verify` 的「事件闭合」判据不变，逐条日志仍可通过 `rule_id` 在 `events.jsonl` 里检索到原事件。

写清这句的原因：将来有人看到「测试从带 `event_id` 改成不带」，容易读成「终端日志不再可追溯」，
进而把这条分层当成降级去「修」回去。

## 五、已知缺口（写在这里以免将来被当成 bug）

- **某题已决策但没有产生任何 `changes`** 时，日志里不会出现它（数量口径以 `changes` 为准）。
  这类题只在 `events.jsonl` 里；若将来出现（如某规则答完发现无需改动），
  需要先在 `changes` 里留下一条「无变更」记录，日志层才有的可翻译；
- `{evidence}` 是启发式（从 `before` 值推千分位 / 单位后缀 / 占位符内容），
  推不出就省略该要素，不报错；
- 日志句式不做配置化（`--log-format json|text|table` 属于日志层重构，不在本次范围）。

## 六、验收证据

| 项 | 结果 |
|---|---|
| 终端输出 | `docs/after-log-cli.txt`（与 `docs/baseline-4b-cli.txt` 同一命令、同一输入） |
| 四要素（模块层） | `tests/test_narrate.py` 8 passed |
| 四要素（进程层）+ 与 changes 对账 + 首行口径 | `tests/test_stage2_event_log.py` 5 passed |
| 全量回归 | 190 passed, 5 xfailed（分两批跑完 29 个文件：82 + 108；单次 `pytest -q` 被终端转入后台，未取得单次汇总） |
| 产物不变 | `clean.csv` / `report.*.json` / `passport.json` / `events.jsonl` / `changes.parquet` 语义零变化（只有 uuid / 时间戳 / 解释器路径） |
| 人工复述 | 已做，判 A 档（见 §二 标准 2） |

## 七、Q1–Q7 的变化

| 问题 | 4b / 4c | 日志层后 |
|---|---|---|
| Q1 是否被逐题问 | 否（自动收尾） | 否（不变） |
| Q2 有自动决策日志吗 | 有，只有 `rule_id` + 行数 | 有，说清动作 / 对象 / 依据 / 结果 |
| Q3 日志可读吗 | 否 | 四要素齐（机械可查）；人工复述待填 |
| Q4 有汇总吗 | 有 `[summary]` + 改动/flag/未问 | 不变 |
| Q5 退出码 | 2 | 2（不变，日志层不影响退出码） |
| Q6 产物可懂吗 | 只有 `diff.html` + `clean.csv` | 不变（职责不在产物） |
| Q7 复核看哪里 | 必须读 `changes.parquet` | 看终端够用于「做了什么」；逐格复核仍读产物 |

## 八、日志层暴露的两件事实

日志层不是「换个说法」，它把两件以前藏在产物里的事抖到了终端上。两条都**不是日志层的 bug**，
而是它第一次让这些事可被用户看见。

### 8.1 问题数 ≠ 规则数（首行曾自相矛盾）

改造前首行是 `问题 6 个 | 规则 date_format, …`（5 条），读者会拿 6 去数下面 5 行日志。
两个数本来不同源：**问题数 = 队列长度**（`missing_token` 每列一题，收盘价 / 成交额各一题，
聚合前是 2 个问题、聚合后是 1 行日志），**规则数 = 去重后的 `rule_id` 数**。

修法（不合并、不改语义，只把口径写出来）：

```
问题 6 个 | 命中 5 条规则：date_format, duplicate_key, missing_token, ohlc_contradiction, unit_ambiguity
```

判据：`test_question_line_explains_question_vs_rule_gap`（问题数对 `passport.counts` 求和、
规则数对 `events` 的 `rule_id` 去重，两个来源各自钉死）。

### 8.2 `flag` 的原因不是「引擎不确定」，是「默认路径拒绝自动改」

三条 flag 的原因都是 `默认路径：无人工决策`。这句话说明的是 policy，不是能力：

> 日志层让用户第一次看清「默认路径的 flag 不是『引擎不确定』，是『默认路径拒绝自动改』」。
> 这解释了为什么 4b 的 flag 率（3/6）看起来高——不是数据脏，是默认路径保守。
> 用户如果觉得 flag 太多，正确做法是 profile 里把对应规则的 `mode` 改成 `auto`，
> 不是让日志层少打 flag。

不写这句，用户会以为「引擎搞不定这些」，实际是「引擎被 policy 限制不许动」。
这是日志层最隐蔽的价值：它让 policy 的保守程度变得可见。

### 8.3 `[summary]` → `[汇总]`

钉住的是 N/M/K 的**口径**，不是标签语种。中文日志里混一个英文标签，读者会怀疑
「这是给我看的还是给机器看的」。改标签不改值，`SUMMARY_LINE` 正则同步改。

`docs/spec-stage2-追认.md` 记录的是阶段 2 当时的样子，未改写历史；
标签的现行写法以本文档与 `docs/after-log-cli.txt` 为准。
