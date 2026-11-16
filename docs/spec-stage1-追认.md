# 阶段 1 追认：默认路径不空（行为 ↔ 测试对应表）

> 按拍板，阶段 1 不写 `spec-stage1.md` 全文：代码先行，本文是**追认文档**，不超过 1 页。
> 三处引用不复述，只给指向：`passport_id` 字段 ≠ `FIELD_COMPAT["passport_id"]`（阶段 0 判据 2、8）；
> `--dry-run` 的 verify 行为见判据 4 的 `ext:dry_run`；旧护照缺 `passport_id` 的处理见判据 8。

## 一、三个定死的口径

1. **总数 = question 队列长度**，`N(fix) + M(flag) + K(未问)` 与之一一对应。
   `changes` 条数不参与比较——一个 fix 可改多格、一个 skip 可标多行，对外一律按 `decision_id` 去重。
2. **`--dry-run` = export 时切换 `clean.csv` 来源**（直接复制输入文件，逐字节一致）；
   `_fix_*` 一行未改，`changes` / `events` / `passport` 照常写，changes 的语义是「拟改动清单」。
3. **`passport_id` = `{profile_name}-{YYYYMMDDTHHMMSS}-{uuid4 前 8 位}`**，export 时生成，
   同会话内稳定；`passport_id_override` 可固定取值（供测试与 7a patch 复用）。
   父代关系**不靠命名**，留给后续阶段的 `patch_of`。

## 二、行为 ↔ 测试对应

| 行为 | 测试 | 结果 |
|---|---|---|
| id 格式 / 同会话稳定 / 两会话不撞 / 可覆盖 / 写进护照 | `test_stage1_passport_id.py`（5 条） | 绿 |
| 三计数和 == 队列长度；`decision_id` 去重 == N/M；K 来源标签；护照写 `counts` | `test_stage1_counts.py`（4 条） | 绿 |
| 真实产物上等式成立；dry-run session 侧 + verify 侧 | `test_check_counts_integration.py`（3 条） | 绿 |
| dry-run `clean.csv` 与输入逐字节一致、verify 返回 0 或 2、**不返回 1** | `test_check_counts_integration.py` | 绿 |
| 退出码：`flag`/未答 → 2；`auto_only` 不再单独触发 | `test_cli_exit_codes.py`（3 条） | 绿 |
| 草稿导出（6 题未答）→ 2；交互全答 → 0 | `test_cli.py` | 绿 |
| 基线同变更重生且稳定 | `test_fixture_baseline.py`、`_fixture.py --check` | 绿 |

## 三、与字面文案的偏差（1 处，判断已记录）

判据 4 写「`changes` 每条 replay 到 input 上**不为 NaN**」。实现为：变更行可定位、列存在、
且 **`after` 非空**的落点不是 NaN。原因：`filled_nan`（缺失 token 填充）的 `after` 本身就是 `None`，
字面执行会把合法产物判死。

- 偏离理由已写进代码：`passport._dry_run_problems` 与 `verify_passport` 的 docstring（不只在本文）。
- **登记：`filled_nan` 白名单语义 → 阶段 5 之后待定**。它需要「哪些 `after=None` 合法」的定义，
  而这依赖阶段 3 的 `target_state` 与阶段 5 的 `phase`；现在定等于在没打好的地基上盖楼。
- **两条路径警告已写进代码**：dry-run 分支是独立实现（仍复用 `replay_changes`），
  两条路径的「verify 通过」含义不同，改一条必须检查另一条。

## 四、已知语义残留（一行可改，需拍板）

**`passport["auto_only"]` 当前语义 = 「本次没有人工决策」，不是「走了 `--auto-only` 通道」。**
它按「事件全是自动动作」派生，所以默认路径（阶段 1 起也自动收尾）下同样是 `true`。
一个字段扛两个语义，**阶段 2 必须重命名或拆分**——阶段 2 的日志要区分「这批决策是默认路径做的」
与「用户显式要求 auto-only」，届时这个名字会直接误导日志读者。
本阶段未改：改动会波及 05 的 3 处既有断言，收益只是命名更准，不值得在这里做。

## 五、按划线未做

不碰 `engine.py`；不引入 `patch_of` / `phase` / `target_state`；不加新子命令；
不改产物结构（`changes.parquet` 就是拟改动清单）；不拆退出码两个计数；不写 spec 全文。

## 六、验收结果

- 21 个测试文件**分组跑完**：`106 passed, 5 xfailed, 0 failed`
  （未用单条全量命令——四次被转后台；分组覆盖了全部文件，5 个 xfail 仍是阶段 0 的 patch 链 4 条 + verify 扩展点 1 条）。
- `tests/fixtures/demo.expected.json` 同提交重生，`_fixture.py --check` 一致；
  `tests/baselines/engine.sha256` 未变（engine 未动）。
