# 4d 落地报告：让 FinPass 承认「横截面」是合法形态

数据：`data/a_stock_valuation_raw.xlsx.xlsx`（A 股估值指标，5580 只 × 79 列）
画像与四次失败见 `data-profile-a_stock_valuation.md`（本报告不重复）。

---

## 一、数据准备结果

脚本：`scripts/prepare_valuation_data.py`（**不进 `src/`**，只做形态抹平，不改值）

```
源文件      : data\a_stock_valuation_raw.xlsx.xlsx
清理前      : 5582 行 × 79 列
丢尾行      : 2 行（证券代码不匹配 6 位.交易所：全空行 + 「数据来源：同花顺iFinD」）
丢空列      : 3 列 ['per_lyr_3m', 'peg_hist_3m', 'pb_forecast_2004_3m']
清理后      : 5580 行 × 76 列
输出 CSV    : out\valuation-check\a_stock_valuation_clean.csv
映射表      : out\valuation-check\header_mapping.md
保留 -- 的列: 69 列
```

- 表头归一按「列名首行 = 指标名」映射，`[交易日期]` / `[单位]` / `[年度]` 等元数据**不进列名**，
  但完整原文保留在 `header_mapping.md`（79 行，逐行标注「保留 / 丢弃」）。
  映射规则写死在脚本的 `NAME_MAP`（79 条），没有一行落在 `src/`。
- 尾行不靠行号识别，靠「`证券代码` 必须匹配 `^\d{6}\.(SH|SZ|BJ)$`」——换一份导出也成立。
- 空列也不是硬编码名单，是「除 `--` / 空串外无值」检测出来的，恰为那 3 列。
- **值一个字没动**：86,315 个 `--` 全部原样留给 FinPass；「缺失怎么填」是清洗决策，不是数据准备。

映射表节选（全表 79 行见 `header_mapping.md`）：

| 原列名 | 短名 |
|---|---|
| `市盈率（PE，TTM）⏎[交易日期] 3月前⏎[TTM基准日] 报表截止日期` | `pe_ttm_3m` |
| `市净率（PB，MRQ）⏎[交易日期] 3月前` | `pb_mrq_3m` |
| `总市值⏎[交易日期] 3月前⏎[单位] 元` | `mktcap_total_3m` |
| `市盈率（PE，TTM）分位数⏎[交易日期] 最新⏎…` | `pe_ttm_percentile` |

---

## 二、第一次跑通：profile 撞了 4 次墙，跑通后 71 题

按任务给的 yaml 原文写入（一字未加），逐步补齐——每一步都是**加载期**拒绝，不是运行期崩：

| # | 报错（原文末行） | 性质 |
|---|---|---|
| r1 | `ValueError: profile 缺少必需字段：name` | yaml 不完整 |
| r2 | `ValueError: target_state[证券代码] 缺少 semantic` | yaml 不完整 |
| r3 | `ValueError: profile 声明了 duplicate_key，但 policy.duplicate_keep 缺失（可选：first/last）` | yaml 不完整 |
| r5 | `ValueError: 规则 pe_range 需要 target_state[pe_ttm_3m].range，但未声明` | **参数放错层**：边界属列级目标态，不属规则 |
| r6 | 跑通：`问题 2 个`（只有 2 道 range），`missing_token` **0 题** | yaml 少 `missing_tokens`，规则空转 |
| r7 | 补 `missing_tokens` 后：`问题 71 个` | **B 类卡点暴露** |

r7 的完整结果（`out/valuation-minimal`）：

```
问题 71 个 | 命中 3 条规则：missing_token, pb_range, pe_range
[汇总] 检查 4 条规则 / 触发 3 条 / 未动 1 条 / 未答 0 条
改动 0 / flag 71 / 未问 0 | 退出码 2
```

逐项回答第三步的问题（以 r7 为对象）：

1. **退出码**：2（有 flag，无失败）。
2. **7 个产物**：齐（clean.csv / changes.parquet / diff.html / events.jsonl / passport.json /
   report.before.json / report.after.json）。
3. **`missing_token` 题数**：**69 道**，扫描全部 76 列，69 列命中（`--` 落在 69 列）。
   > 10 道 → **全列扫描是阻塞点**，且不是「答不完」而是「答了也没意义」：69 题里 68 题是同一句
   > 「这一列有 `--`，填还是留」，人无法逐题做 69 次相同判断。
4. **4 个分位数列**：只被 `missing_token` 抓到（各 1 题）；`range_check` 没覆盖它们，
   也没有任何「整列恒定 / 整列为空」的检查。实测这 4 列只有两个值：`100` 与 `--`。
   → **列级噪声检查确实缺失**，但**不阻塞**：`scan_columns` 生效后它们根本不进扫描。
5. **表尾 2 行**：不在了（clean.csv 5580 行，尾行 `302132.SZ`）。
6. **verify**：跑通了，但见第五节——**14 分钟**。

---

## 三、卡点分类

| 类 | 卡点 | 证据 | 处理 |
|---|---|---|---|
| **C** | yaml 缺 `name` / `target_state.semantic` / `policy.duplicate_keep` | r1–r3 三次加载期报错 | 改 yaml（声明不完整，不是域知识缺失） |
| **C** | `range` 的 min/max 写在规则里，engine 只读 `target_state[列].range` | r5 报错 + `engine.py:340` | 改 yaml（参数放错层） |
| **C** | `missing_tokens` 未声明 → `missing_token` 空转（0 题） | r6：`问题 2 个` | 改 yaml |
| **B** | `missing_token` 无视 `columns`，全列扫描 → 69 题 | r7：69 道同质题；`engine.py:195` 原 docstring 写「columns 不参与」 | **改引擎**（唯一一处） |
| A | 无 | 表头/尾行/空列都在数据准备阶段解决，r5 之后没再出现 | — |

**判断依据**：C 类是「我知道要什么，只是写错了地方」；B 类是「引擎没有表达这个形态的位置」。
后者不能靠 profile 绕过——`--max-questions` 调到 200 也只是让 69 道题全进队列。

---

## 四、引擎改动（唯一一处）

```diff
 def _check_missing_token(df, rule_id, columns, target_state, keys, tokens) -> list[Gap]:
-    """缺失占位符：全列扫描（``columns`` 不参与——词表是全局的，不按列声明）。"""
-    gaps: list[Gap] = []
-    for column in df.columns:
+    """缺失占位符：词表是全局的，**扫描范围**由 ``columns`` 声明。"""
+    if columns:
+        absent = [column for column in columns if column not in df.columns]
+        if absent:
+            raise ValueError(f"规则 {rule_id} 声明扫描的列不存在：{'、'.join(absent)}")
+        scan = list(columns)
+    else:
+        scan = list(df.columns)
+    gaps: list[Gap] = []
+    for column in scan:
```

三处配套（都不是新逻辑，是把约定说清楚）：

- `engine.py` 模块 docstring 加一段：`checks[*].columns` **只声明「查哪几列」，不声明「查什么」**；
  空列表 = 按类型自己决定，非空 = 就查这几列。并写明 4d 之前 `missing_token` 是唯一例外。
- `session._check_columns` 的 docstring 同步（它本来就是「声明优先」，只是没人告诉它这条也适用于 `missing_token`）。
- 声明了不存在的列**报错**，不静默退回全列扫描——静默退回会让「只扫三列」的意图失效且看不出来。

**为什么这是「形态」不是「域」**：引擎不知道 `pe_ttm_3m` 是什么，只知道「调用方点名了这三列」。
零新增 schema 字段（复用 `business_rules[].columns` 这条既有通道），零新增常量。
`test_no_finance_words_in_engine.py` 仍然通过——引擎里没有一个估值词。

**测试**：

- 新增 `tests/test_stage4_scan_columns.py`（5 例）：声明限范围 / 多列按声明顺序 / 空列表全扫 /
  声明缺列报错 / 声明列进 `required_columns`。
- 基线重生：`tests/baselines/engine.sha256` `89c997…` → `92ecc6…`，
  理由已按该文件约定记进 `tests/test_engine_untouched_by_new_profile.py` 的「基线重生记录（4d）」。
- 登记：`docs/spec-stage4-追认.md` §十三（含触发数据、改动、边界、不做的理由）。
- **加 profile 暴露的测试硬编码**：`tests/test_capability_map.py` 两个用例把「3 个 profile」写死在
  格值列表里，加第 4 个 profile 就报「数据行列数不一致」。改成按 `profile_names()` 实际数量生成夹具
  （`_statuses()`），并把 docstring 的「表头恰好 5 列」改成「列数随 profiles/ 变」。
- 全量：**195 passed, 5 xfailed**（改动前 190 passed；+5 = 新增用例数）。

---

## 五、最终跑通

```bash
finpass run out/valuation-check/a_stock_valuation_clean.csv \
  --profile a_stock_valuation --out out/valuation --auto-only
```

```
问题 4 个 | 命中 3 条规则：missing_token, pb_range, pe_range
[汇总] 检查 4 条规则 / 触发 3 条 / 未动 1 条 / 未答 0 条
[缺失填充] 「pe_ttm_3m、pb_mrq_3m」42 行含占位符 → 打标保留，原因：auto_only：无人工决策
[范围校验] 「pe_ttm_3m」12 行越界 [-10000, 100000] → 打标保留
[范围校验] 「pb_mrq_3m」1 行越界 [-1000, 10000] → 打标保留
改动 0 / flag 4 / 未问 0 | 退出码 2
```

| 验收项 | 结果 |
|---|---|
| 退出码 | 2（有 flag；无失败） |
| 7 个产物 | 齐；clean.csv 3.2 MB / diff.html 10.5 MB / changes.parquet 4.9 KB |
| `missing_token` 题数 | **2**（`pe_ttm_3m`、`pb_mrq_3m` 各 42 行；`mktcap_total_3m` 无 `--`，不产题） |
| 总题数 | **4**（≤10）|
| events.jsonl | 4 条事件 / 4 题，一一对应 |
| changes | 194 条，全部 `flagged_kept`（42×2 列 + 42×2 主键格 + 1+1+12+12） |
| passport | `counts={fix:0, flag:4, unanswered:0}`、`unresolved_issues_count=194`、`keys=['证券代码']`、`profile_schema_version=2.3.0` |
| verify | `hash OK / replay OK / events OK` → **verify 通过（存在未解决项，退出码 2）**，耗时 **14 分 0 秒** |
| 引擎无估值词 | `grep -icE "\b(pe\|pb\|ps\|pcf\|mktcap\|valuation\|ev)\b"` → **0 行**；中文域词（市盈率/市净率/估值/市值/股本/股息）→ **0 行**；`test_no_finance_words_in_engine.py` 通过 |

**verify 的 14 分钟要单独说明**（不是这次改坏的）：`_frame_diff` 是 Python 级逐单元格比对
（`for position in range(len(df)): for col in df.columns: df.iloc[position][col]`，`passport.py:235-243`），
成本 O(行 × 列)；5580×76 = 424,080 次索引。而 CLI 入口**跑两遍**（`_cmd_verify` 先
`verify_passport`，`verify_exit_code` 内部再调一次），所以 7 分钟 × 2。
样例 3 行时是 0.2 秒，规模一上来就不可用。**本次未修**（属性能问题，不是形态缺原语），见第七节。

---

## 六、README 更新

新增一节 **「如何支持一种新数据形态」**（在「机制覆盖度」与「Notebook 矩阵」之间），内容四层：

1. **三层分界表**：数据准备（`scripts/`，用户自己） / 形态原语（`src/`，需一次数据暴露作理由） / 域知识（`profiles/*.yaml`，领域专家）。
2. **加 profile 要改引擎吗**：大多数不用，`futures.yaml` 与 `a_stock_valuation.yaml` 加完 `engine.sha256` 不变。
3. **什么时候要改**：判据不是「跑不通」，是「跑得通但结果没意义」——估值数据就是例子（71 题 → 4 题），并给了本次的复现命令。
4. **边界**：引擎认识形态、不认识域；**FinPass 负责让数据可分析，不负责分析数据**。

同一次提交里顺手修正了三处已过期的 README 断言（都属「文档说的和代码不一样」）：

- 「机制覆盖度」表：3 列 → 4 列（加 `a_stock_valuation` 列，`ohlc` / `date_format` / `unit_scale` 标 `未做` 并说明「声明了也只会空转」）。
- 「示例数据」段：原文说「列名对齐 `synonyms`，换自己的 CSV 走同样流程」——`synonyms` 至今没有消费点，已改为「列名与 `target_state` 键逐字一致；改列名的映射由数据准备脚本做」。
- 「当前状态」段：原文说 `dispatch` / `fix` 仍是 `NotImplementedError`（4c 已落地），已改；并注明 `reverify` / `audit` 仍是空壳且**当前无调用点**。

---

## 七、没做的

| 项 | 为什么不做 |
|---|---|
| `ignore_trailing_rows` 原语 | 表尾说明行已由数据准备脚本按主键格式丢掉（且不是硬编码行号）。引擎再加一处丢行逻辑，等于同一件事有两个执行点，且没有第二份数据证明必要 |
| `column_noise_check`（整列恒定 / 整列为空） | 4 个分位数列只有 `100` 和 `--`，`scan_columns` 生效后它们不进扫描、不产题、不阻塞体验。为一个不产生噪声的列加规则，是给自己加空转 |
| 横截面 profile 表达「我没有日期列」 | 加载器在单主键时把 `date_column` 默认成主键列（`profile.py:461-466`），于是本 profile 的 `date_column` 是 `证券代码`。当前无消费点（未声明 `date_format`），但语义是歪的；修它要动 schema（`date_column` 改可选），不在 4d 范围 |
| `verify` 的 O(行 × 列) 比对 + CLI 跑两遍 | 5580×76 要 14 分钟。属性能问题，不是「引擎没有表达横截面」——改了它并不会让任何新用户少撞一次墙。**但这是下一个真实数据规模下的必然阻塞项，建议单独立项** |
| clean.csv 的数值重新序列化 | 未声明 `dtype` 的列被 pandas 推断为 float64，导出时 `1362364800` → `1362364800.0`（4,679 格 / 3 列）。不改变数值语义、不影响 diff（diff 画的是 changes 不是帧比对），但「逐字节与输入一致」不成立 |
| 未声明列里的 `--` 仍保留 | `clean.csv` 里 69 列仍有 `--`。这是 `scan_columns` 的直接后果：**审计范围 = 声明范围，不是整表**。已在 profile 注释与 README 里写明，没有偷偷「顺手清全表」 |
| flag 落在主键列 | `changes` 里 `证券代码` 有 84 条 `flagged_kept`（每行都有一条落在主键格上）。这是 `_flag_changes` 的既有行为（按样本列打标，含主键），三个 profile 一致；不属于本次改动 |
| xlsx 读取 / `--encoding` / `synonyms` 接线 | 与本文方向相反：数据准备在外面做；`synonyms` 是另一条独立缺口（见落地评估报告） |
