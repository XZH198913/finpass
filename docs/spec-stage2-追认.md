# 阶段 2 追认：日志可反查

## 一、改了什么代码

| 文件 | 改动 | 行为变化 |
|---|---|---|
| `src/finpass/cli.py` | 新增 `_print_summary(session)`，`_cmd_run` 在逐条日志前调用 | 终端多一行 `[summary] 检查 N 条规则 / 触发 M 条 / 未动 K 条 / 未答 K' 条` |
| `src/finpass/cli.py` | 新增 `_print_event_log(session)`，`_cmd_run` 在导出前调用 | 终端多出一批形如 `[rule_id] event_id=<hex> 影响 M 行 决策=fix\|flag` 的行；**没有**新增产物文件 |
| `src/finpass/cli.py` | 从 `session` 导入 `FIX_ACTIONS` | `决策=` 的 fix/flag 归类与 `counts()` 同源（一套集合，不复制判断） |
| `src/finpass/passport.py` | `verify_passport` / `_dry_run_problems` 补 docstring | 无行为变化，只把阶段 1 的偏离理由与「两条路径」警告写进代码 |
| `src/finpass/cli.py` | `_exit_code_of` 补注释 | 无行为变化，记录「两个触发源是双保险」（阶段 1 判断 1 的落地） |
| `tests/test_cli_exit_codes.py` | 补 docstring | 同上，写清「去掉 auto_only 触发为何仍为 2」 |
| `tests/test_stage2_event_log.py` | 新建，3 条 | — |

日志行数 = `events.jsonl` 行数；`event_id` 取同一次 `_record` 的 `decision_id`，**逐行可反查**，
不需要另建日志文件。

## 二、行为 ↔ 测试

| 行为 | 测试 |
|---|---|
| 每条处理记录都符合定死格式 | `test_every_log_line_follows_the_format` |
| 汇总行恰好一条；`N` = profile 规则数、`M` = 命中规则数、`K = N-M`、`K'` = `counts.unanswered` | `test_summary_line_matches_rule_and_count_scope` |
| 日志 `event_id` 集合 == `events.jsonl` 的 `event_id` 集合（数量也相等） | `test_log_event_ids_match_events_jsonl_exactly` |
| 逐行反查：`rule_id` / 行数 / fix\|flag 与事件字段一致，找不到 id 即 KeyError | `test_log_line_is_findable_row_by_row` |

## 三、范围与按划线未做

**日志只在 `run` 路径上**（`cli._cmd_run`）。`resume` 不打印；`patch` 尚未存在。
这不是「已经做好了只是没接上」——**日志在阶段 2 只做 `run`**，`resume` / `patch` 的日志
属于各自阶段的范围。

不新增日志文件（终端行即可反查，多一个文件就多一个「和产物不一致」的来源）；
不改 `events.jsonl` 字段；不碰 `engine.py`；不引入 `patch_of` / `phase` / `target_state`。

## 四、未达成判据（阶段 2 日志还不完整）

- **决策来源没进日志**：日志行只有 `决策=fix|flag`，没有「这条决策是默认路径自动做的，
  还是用户 `--auto-only` 要求的」。这是**阶段 2 判据未达成**，不是「做完了还有个待办」——
  原因是它依赖 `passport["auto_only"]` 拆分（一个字段现在扛两个语义，阶段 1 已登记），
  拆分本身是独立改动，不在日志层。
- `filled_nan` 白名单语义：阶段 5 之后待定（阶段 1 登记，本阶段未动）。

## 五、验收

- `pytest tests/test_stage2_event_log.py -q` → `4 passed`（汇总行 1 条 + 逐条反查 3 条）。
- 全量（22 个测试文件，分三段执行以免被后台化）：`110 passed, 5 xfailed, 0 failed`。
  比阶段 1 的 `106` 多 4 条：本阶段新增 4 条测试（汇总行 1 + 逐条反查 3）。
  5 个 xfail 仍是阶段 0 的 patch 链 4 条 + verify 扩展点 1 条。
