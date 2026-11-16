# 阶段 3 未迁移参数清单（A / B 两类）

> 判据：**清单清空才算阶段 3 完成**；非空则阶段 4（engine 收口）不能开始。
> 状态：**清单已清空**（Commit 3 完成）。Commit 1/2 落了 schema 与 profile 结构，参数搬迁在 Commit 3。
> 口径补记：schema 已升到 `2.3.0`（`2.0.0 = target_state`、`2.1.0 = +policy`、
> `2.2.0 = +target_state[*].role`、`2.3.0 = 主键改用 role: key`，见
> `docs/spec-stage4-追认.md`）；判据升级
> （决策层参数 `rows` / `duplicate_keep` 保留是设计）与三条待确认事实见
> `docs/spec-stage3-追认.md`。

## A 类：`params.get("X")` 的 X

| # | 位置 | 参数 | 原默认 | 迁往 | 状态 |
|---|---|---|---|---|---|
| A1 | `session._fix_duplicate` | `keep` | `"保留第一条"` → `keep="first"` | 默认值 → `policy.duplicate_keep: first`；逐题覆盖键改名 `duplicate_keep: first\|last`（决策层） | 已迁移 |
| A2 | `session._fix_unit` | `scale` | `0.0001` | `target_state[列].unit_scale.factor` | 已迁移 |
| A3 | `session._fix_ohlc` | `rows` | 无（逐行决策输入） | **不迁**：它不是默认值，是「同一问题内逐行不同决策」的载体，属决策层 | 不迁移（理由见下） |
| A4 | `session._fix_range_check` | `params`（声明了但未使用） | — | 删掉形参；边界读 `target_state[列].range: {min, max}` | 已迁移 |
| A5 | `cli._parse_decision` | `keep` | 由选项文本映射 | CLI 不再传 `keep`，改传语义值 `duplicate_keep: first\|last`（中文文案留在展示层） | 已迁移 |

## B 类：`_fix_*` / `_rule_*` 里硬编码的常量

| # | 位置 | 常量 | 原值 | 迁往 | 状态 |
|---|---|---|---|---|---|
| B1 | `session._fix_duplicate` | `"保留第一条"` / `"first"` | 字面量 | 随 A1（取值合法性由 `POLICY_REQUIREMENTS` 校验） | 已迁移 |
| B2 | `session._fix_unit` | `0.0001` | 字面量 | 随 A2 | 已迁移 |
| B3 | `session._fix_ohlc` | 行决策缺省 `"answer"` | 字面量 | `policy.default_row_action` | 已迁移 |
| B4 | `session.DATE_FMT` | `"%Y-%m-%d"` | 模块常量 | `target_state[日期].format`（检测、修复、选项文案三处同源） | 已迁移（常量已删除） |
| B5 | `session.UNIT_SPREAD` | `1000` | 模块常量（`_rule_unit_scale` 的兜底） | `target_state[列].unit_scale.spread`；代码不再有兜底 | 已迁移（常量已删除） |

## 明确「不迁移」的两项

**A3（`rows`）**：逐行决策输入，不是范式；`_fix_ohlc` 的 `params["rows"]` 保留。

**B6 UI 选项文案**：`["填 NaN", "跳过打标"]`、`["保留第一条", "保留最后一条", "跳过打标"]`、
`["标记跳过", "以收盘价为准修正"]`、`["按边界裁剪", "跳过打标"]` 等。

理由：它们是**给人看的选项标签**，与域知识无关；写进 profile 会让每份 yaml 多出几行纯 UI 文本，
而且改文案变成改 profile（连带 schema 版本讨论）。**因此不计入 B 类清空判据**——
这一点必须写死，否则「清单清空」永远做不到。

## 迁移后：参数只有两处来源

| 参数 | 唯一来源 | 谁读它 |
|---|---|---|
| 单位换算系数 | `target_state[列].unit_scale.factor` | `_fix_unit` |
| 量级跳变阈值 | `target_state[列].unit_scale.spread` | `_rule_unit_scale` |
| 区间边界 | `target_state[列].range.{min,max}` | `_rule_range_check` / `_fix_range_check` |
| 日期目标格式 | `target_state[日期].format` | `_rule_date_format` / `_fix_date` / 选项文案 |
| 重复保留哪条 | `policy.duplicate_keep`（默认） | `_fix_duplicate` |
| 逐行缺省动作 | `policy.default_row_action` | `_fix_ohlc` |

三处约定：

1. **不允许并存期**：`DATE_FMT` / `UNIT_SPREAD` 是**删除**，不是改别名；代码里没有「profile 缺了就退回常量」的分支。
2. **缺参数就明确拒绝**：`_target_value()` / `_validate_policy()` 在 open 或首次使用前抛 `ValueError`，
   错误信息点名 `profile`、规则 id 与 `target_state[列].字段`，不静默降级。
3. **决策参数逐条放行**：`DECISION_PARAMS` 只登记 `rows` / `duplicate_keep`；
   再传旧键（如 `scale`）直接报错，避免「用户以为 `0.001` 生效了，实际吃的是 profile 的 factor」。

## 自检（已跑过，结果如下）

```bash
grep -rn "0\.0001" src/finpass/*.py                       # 无命中（profiles/*.yaml 的 0.0001 是目标值）
grep -rn "UNIT_SPREAD\|DATE_FMT" src/finpass/session.py   # 无命中（常量已删除）
grep -rn "params\.get" src/finpass/session.py             # 只剩 rows / duplicate_keep 两个决策层键
python tests/_fixture.py --check                          # 与基线一致（7 个产物）
```

验收测试：`tests/test_stage3_params_migrated.py` —— 用源码扫描断言上述事实（不依赖 shell），
另加行为断言：缺 `unit_scale.factor` / 缺 `range` / 缺 `policy` 键必须报错，传 `scale` 必须被拒绝。

基线变更：`tests/fixtures/demo.expected.json` 重生过一次，**只有 `events.jsonl` 变**——
决策集里的 `{"scale": 0.0001}` 随参数迁移删除，事件记录的 `params` 因此为空；
`clean.csv` / `changes` / reports / `diff.html` 逐字节未变（理由记在 `tests/_fixture.py` 头部）。
