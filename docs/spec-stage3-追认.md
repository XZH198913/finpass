# 阶段 3 追认：参数迁移、`policy` 段与判据升级

## 一、改了什么代码

| 文件 | 改动 | 行为变化 |
|---|---|---|
| `src/finpass/session.py` | 删除 `DATE_FMT` / `UNIT_SPREAD`，`_fix_unit` / `_fix_date` / `_rule_unit_scale` / `_rule_range_check` / `_fix_range_check` 改读 `target_state` | 换算系数 0.0001、日期格式 `%Y-%m-%d`、量级阈值 1000、区间边界**不再有代码来源** |
| `src/finpass/session.py` | 新增 `_target_value` / `_target_map` / `_target_options` / `_range_bounds` / `_policy` | 缺参数时抛 `ValueError` 并点名 `profile`、规则 id、`target_state[列].字段` |
| `src/finpass/session.py` | 新增 `DECISION_PARAMS`，`_fix()` 先做参数白名单 | 传旧键 `scale` 直接报错（不降级为警告，见第三节） |
| `src/finpass/profile.py` | 新增可选 `policy` 段 + `POLICY_REQUIREMENTS` + `_validate_policy`（**加载期**） | 声明了 `duplicate_key` / `ohlc_contradiction` 却没给 `policy` 键的 profile，在 `load_profile` 就被拒 |
| `src/finpass/profile.py` | `PROFILE_SCHEMA_VERSION` `2.0.0` → `2.1.0`，docstring 记版本历史 | 见第四节 |
| `src/finpass/cli.py` | `_parse_decision` 不再传 `keep`，改传 `duplicate_keep: first\|last` | 终端选「保留最后一条」仍然生效（选项序号 → 决策值，文案留在展示层） |
| `profiles/a_stock_daily.yaml` / `futures.yaml` | 加 `policy`；`unit_scale` / `range` / `format` 进 `target_state`；规则声明去掉 `spread` / `min` / `max` | 规则声明只留「查哪几列 + 选项文案」 |
| `tests/_fixture.py` | 决策集去掉 `{"scale": 0.0001}`；基线重生并记理由 | 只有 `events.jsonl` 的 `params` 变，其余 6 个产物逐字节不变 |
| `tests/test_stage3_params_migrated.py` | 新建 12 条 | 见第六节 |
| `src/finpass/passport.py` | 注释措辞（去掉 `0.0001` 这个举例字面量） | 无行为变化，只是让「源码无 0.0001」的扫描判据站得住 |
| `src/finpass/session.py` | Commit 4：护照写入 `profile_schema_version = PROFILE_SCHEMA_VERSION` | `passport.json` 多一个字段，与 `profile_version` 并存 |
| `src/finpass/passport.py` | Commit 4：`verify_passport` 按 `FIELD_COMPAT` 对缺字段告警 | 旧护照多一行 `[warn] … 按 2.0.0 处理`，`ok` 与退出码不变 |
| `src/finpass/profile.py` | `placeholder: true` 的 profile 在必填字段校验**之前**报错 | `fund_nav` 的报错从「缺少必需字段」变成「占位文件，未迁移」 |
| `profiles/fund_nav.yaml` | 声明 `placeholder: true` + `note` | 保留占位，不迁 v2 |
| `README.md` | 机制覆盖度表的 `N/A` 口径加脚注 | 区分「该 profile 不可用」与「rule_type 未实现」 |
| `tests/test_passport_schema_version.py` | 新建 3 条 | 见第六节 |

## 二、判据升级：范式参数 vs 决策层参数

**必须先区分两件事**，否则「清单清空」会被误读成「代码里不能再出现 `params`」：

- **范式参数**（A1–A5 / B1–B5）：描述「这一类数据应该长什么样」——换算系数、日期格式、
  量级阈值、区间边界、保留哪条、逐行缺省动作。**已全部迁到 `target_state` / `policy`**，
  代码里一份都不留。
- **决策层参数**：同一问题内逐行/逐题不同的人工选择——`rows`（哪几行 skip）、
  `duplicate_keep`（这次保留最后一条）。**保留在 `params` 是设计，不是遗漏**；
  把它们搬进 profile 反而会把「这次怎么决定」写成「这个域永远这样」。

因此阶段 3 的判据现在读作（写死在 `tests/test_stage3_params_migrated.py`）：

```bash
grep -rn "0\.0001\|UNIT_SPREAD\|DATE_FMT" src/finpass/*.py   # 必须为空
grep -rn "params\.get" src/finpass/session.py               # 只允许 rows / duplicate_keep
```

任何**第三个** `params.get` 出现即视为范式参数回流。白名单另有逐字断言
（`test_decision_params_whitelist_is_pinned`），加键必须改测试，不允许悄悄扩大。

## 三、三处偏离的处置

1. **`duplicate_keep` 两层拆分**：接受。默认值 → `policy.duplicate_keep`，
   逐题覆盖 → `params["duplicate_keep"]`。若 CLI 不再传任何键，用户选「保留最后一条」
   会静默变成保留第一条——所以这一处保留决策层参数。
2. **`policy` 顶层段**：接受。`duplicate_keep` / `default_row_action` 是行级范式，
   挂在「日期」「合约代码」的列目标态下语义错误。
3. **`scale` 报错而非忽略**：接受，且**拒绝降级为警告**。警告等于「代码里还认 `scale`，
   只是不报错」，正是阶段 3 要消除的并存期。

## 四、schema 版本：2.0.0 → 2.1.0

上一轮口径：major = 破坏性、minor = 兼容性新增、patch = 不影响加载。
`policy` 是**会改变加载器行为**的可选字段（声明了那两条规则的 profile 现在必须有对应键），
属于 minor，不是 patch。已改：

- `PROFILE_SCHEMA_VERSION = "2.1.0"`，docstring 里留版本记录：
  `2.0.0 = target_state 必填`、`2.1.0 = +policy（行级范式，加载期校验）`；
- `tests/test_stage3_schema.py` 的 pin 同步改为 `2.1.0`，
  并新增「只声明 missing_token 的旧 profile 仍可加载」（兼容性新增的证据）与
  「声明 `duplicate_key` 缺 `policy` 在加载期被拒」两条；
- 两份 yaml 的头注释标 `schema 2.1.0`。

`placeholder: true`（`fund_nav` 用）**不触发** schema 升版：它只改变一个**本来就会被拒**的
文件的报错文案，不改变任何可取用 profile 的加载结果，也没有引入新的可选结构——
按「patch = 不影响加载」的口径，它连 patch 都算不上，只是同一版里的错误信息改进。

**Commit 4 已落地**：`session.export` 写入 `profile_schema_version = PROFILE_SCHEMA_VERSION`，
与 `profile_version` 并存。旧产物（2.1.0 之前导出）缺该字段时，`verify` 输出
`[warn] profile_schema_version 缺失，按 2.0.0 处理`：不改 `ok`、不影响退出码、不跳过其余校验。
处理方式的**事实源**是 `passport_compat.FIELD_COMPAT["profile_schema_version"] == "warn"`，
`verify` 里不再另写一份三值判断。

### `profile.version` vs `profile_schema_version`

| 字段 | 回答的问题 | 唯一来源 |
|---|---|---|
| `profile_version` | 这份 profile 的**内容**何时改过（作者版本） | yaml 顶层的 `version` |
| `profile_schema_version` | 产物出自**加载器**的哪种结构合同 | `PROFILE_SCHEMA_VERSION` 常量 |

两者**独立演进**，没有强制对应关系：`a_stock_daily` 现在是
`profile_version=0.1.0` + `profile_schema_version=2.1.0`。
审计员靠两个字段并存区分「profile 内容变了」与「加载器合同变了」。
**`profile_schema_version` 永远不是 profile 声明的**——不要去 yaml 里找它，
也不要把「引擎无法区分 v2.0.0 / v2.1.0 的 profile」这类说法当成 profile 属性问题。


## 五、三条事实

**事实 1：Commit 1 / Commit 2 已完成（同一轮落地，未拆成两次提交）。**

- `PROFILE_SCHEMA_VERSION` 在 `src/finpass/profile.py`，不是每份 yaml 顶层；
- 旧 profile（无 `target_state`）**报错**而不是 warn：
  `profile 缺少必需字段：target_state（schema 2.0.0 起必填；…）`，
  由 `test_v1_profile_without_target_state_is_rejected` 守；
- `futures.yaml` 已是 v2 结构（`target_state` 逐列语义 + `cross_column_rules`）；
- **`fund_nav.yaml` 保留占位，不迁 v2**（拍板见下）：`load_profile("fund_nav")` 现在报的是
  `fund_nav.yaml 是占位文件，尚未迁移到当前 schema；在补齐 target_state（含 role: key）与 policy 前不可用`，
  **不再**退化成「缺少必需字段：name、key、business_rules」。

**事实 1 的补充：`fund_nav` 的处置（保留占位 + 明说 + 脚注）**

三个选项都过了一遍：迁成「每列 unknown」（把占位包装成「可用但全空」，且缺的是语义不是结构）、
删除（要动 README / notebook / examples，且以后要重写）、保留占位（报错清晰、零新增工作量）。
选**保留占位**，并补两件事：

1. `fund_nav.yaml` 自己声明 `placeholder: true` + `note:`，`load_profile` 在必填字段校验**之前**
   就抛这条 note（`test_placeholder_profile_says_it_is_a_placeholder` 守；
   断言里明确禁止退化成「缺少必需字段」）；
2. README 机制覆盖度的 `N/A` 口径加了一句：**`N/A` 只表示「该 profile 不可用」**，
   不是「引擎没实现这个 rule_type」（后者表现为 `未做`），并指向本文档。

不迁的理由：迁移需要的不是「每列 unknown」这个机械动作，而是基金净值的正确语义——
key 用净值日期还是（基金代码 + 日期）、复权与不复权算不算两种目标态、单位是元还是份额。
语义没定就硬迁，只会造出一份看起来完整、实际全错的 schema。

**事实 2：`profile_schema_version` 已进护照（Commit 4 完成）。**

`a_stock_daily` 导出后护照里同时有 `profile_version=0.1.0` 与
`profile_schema_version=2.1.0`；旧产物缺字段时按 `warn` 处理（见第四节）。
engine 现在可以假设「2.1.0 之后导出的产物一定带该字段」，并对 v2.0.0 产物走
「warn 后按 2.0.0 处理」的分支。

**事实 3：`default_row_action: answer` 的语义——不是「要求用户回答」。**

它是 `_fix_ohlc` 的**逐行兜底动作**：

- 问题本身**照样进队列**等人工决策（`_rule_ohlc_contradiction` 产 question，
  `mode` 默认 `human`，`auto_only` 通道走 `_skip` 而不是 `_fix`）；
- 只有当人工把该问题回答为**修正**、而 `params["rows"]` 没覆盖到某一行时，
  `answer` 才表示「这一行按『以收盘价为准修正』执行」（写 high/low = close），
  `skip` 表示「这一行打标保留」；
- 所以它**既不是产出 flag，也不是自动放行**，而是「已决修正之后的逐行缺省」。
  已写进 `profile.POLICY_REQUIREMENTS` 上方的注释，阶段 4 不必再猜。

命名确实有歧义（`answer` 与决策动作 `action="answer"` 同名）。**本次不改**，
因为它是 profile 里的公开取值，改名要连带两份 yaml、`POLICY_REQUIREMENTS` 与本阶段测试一起动。
已登记进下面的「未来改名」清单，等下一次破坏性升版（3.0.0）一起做。

### 未来改名清单（等 schema 3.0.0）

| 现名 | 建议 | 理由 |
|---|---|---|
| `policy.default_row_action: answer \| skip` | `fix \| flag` | `answer` 与决策动作 `action="answer"` 同名；实际语义是「逐行兜底：修还是标」 |

登记在这里而不是代码 TODO：改名是 schema 破坏性变更，必须和 3.0.0 的迁移脚本一起排期，
写在代码里会变成永久 TODO。

## 六、行为 ↔ 测试

| 行为 | 测试 |
|---|---|
| 模块常量已删除（不是改别名） | `test_module_constants_are_deleted_not_aliased` |
| 源码无 `0.0001` / `%Y-%m-%d` | `test_no_hardcoded_unit_factor_or_date_pattern` |
| `params.get` 键集合 == 决策层白名单 | `test_params_get_only_covers_decision_layer` + `test_decision_params_whitelist_is_pinned` |
| CLI 不再传 `keep` / `scale` | `test_cli_no_longer_sends_keep` |
| 缺 `unit_scale.factor` / 缺 `range` 即报错 | `test_unit_factor_comes_from_target_state` / `test_range_bounds_come_from_target_state` |
| 旧 `scale` 参数被拒 | `test_stale_scale_param_is_rejected` |
| 缺 `policy` 键 / 值非法在**加载期**被拒 | `test_missing_policy_key_is_rejected_at_open` / `test_illegal_policy_value_is_rejected_at_open`（+ `test_stage3_schema.py` 两条） |
| 日期选项文案跟随 `target_state.format` | `test_date_option_text_follows_target_state` |
| `duplicate_keep` 逐题覆盖生效 | `test_duplicate_keep_override_is_honoured` |
| `policy` 是兼容性新增（无该段仍加载） | `test_policy_section_is_optional_without_row_level_rules` |
| **兼容性新增的另一半**：声明 `duplicate_key` 缺 `policy` → 加载报错，且消息点名规则与字段 | `test_profile_declaring_duplicate_key_without_policy_is_rejected` |
| 护照写入 `profile_schema_version`（= 加载器常量，不是 profile 的 `version`） | `test_export_writes_loader_schema_version` / `test_schema_version_is_not_the_profile_version` |
| 旧护照缺该字段只 warn（`ok` 不变、退出码不变成 1） | `test_old_passport_without_schema_version_only_warns` |
| `fund_nav` 是占位：报错说明「占位 / 未迁移」，不退化成「缺少必需字段」 | `test_placeholder_profile_says_it_is_a_placeholder` |

## 七、验收与时序

时序钉死，后续不必重跑：

1. Commit 3 产品代码改动（删常量、改 fixer、加白名单、`policy` 加载期校验）**全部完成**；
2. `python tests/_fixture.py --check` → 先报「与基线不一致：**events.jsonl**」（只有它），
   证明数据侧零变化；随后 `--write` 重生基线，理由写进 `tests/_fixture.py` 头部；
3. `python -m pytest -q` → **`119 passed, 5 xfailed`**（此时代码已定稿；
   其后只新增 11 条测试、补注释、补文档，未改产品代码）；
4. 升 schema 到 `2.1.0` + policy 校验移到加载期后，按文件分批复跑：
   `49 passed`（stage3_schema / stage3_params_migrated / profile_loader / profile_authoring）、
   `40 passed`（fixture_baseline / verify / replay / auto_only / stage3_schema）、
   `44 passed + 4 xfailed`（守护类 + 加载器）、`35 passed + 1 xfailed`（阶段 1/2 + verify）、
   `6 passed`（CLI 子进程）、`3 passed`（CLI 退出码）；`_fixture.py --check` 仍是「与基线一致（7 个产物）」。
5. Commit 4（护照写 `profile_schema_version`）落地后：`_fixture.py --check` 先报
   「与基线不一致：**passport.json**」（只有它），`--write` 重生（理由记在 `_fixture.py` 头部，
   其余 6 个产物指纹不变），然后 `--check` 回到「与基线一致（7 个产物）」。
6. Commit 4 + `fund_nav` 处置的复跑：
   `3 passed`（`test_passport_schema_version.py`）、`29 passed`（`test_stage3_schema.py` + `test_profile_loader.py`）、
   `19 passed`（`test_capability_map.py` + `test_stage3_params_migrated.py`，验证 README 脚注没破坏解析器）、
   `12 passed`（`test_verify.py`，新增 warn 分支未影响既有断言）、`3 passed`（`test_replay_consistency.py`）。
7. 手动 `--interactive cli` 全流程：6 个 fix、0 个 flag、退出码 0。

## 八、阶段 4 放行条件

| 条件 | 状态 |
|---|---|
| 未迁移参数清单清空（判据已升级为「范式参数为空、决策层仅两键」） | ✅ |
| `target_state` 与 `_fix_*` 对接 | ✅ |
| `policy` 段落地 + 加载期校验 | ✅（schema 2.1.0） |
| `profile_schema_version` 进护照（值取加载器常量） | ✅（Commit 4） |
| 旧产物（无该字段）的兼容策略定死 | ✅ `warn` → 按 2.0.0 处理，不改退出码 |
| 3 个 profile 的状态明确（2 个 v2 + `fund_nav` 保留占位） | ✅（占位明说 + README 脚注） |
| `test_profile_loader.py` 等断言更新 | ✅（含反向测试与占位测试） |
| `profile.version` 与 `profile_schema_version` 语义区分文档化 | ✅（第四节 + 代码注释） |

结论：**放行条件全部满足，可以开始阶段 4**。engine 可以假设
「2.1.0 之后导出的产物一定带 `profile_schema_version`」，
对缺字段的旧产物走「warn 后按 2.0.0 处理」；`policy` 缺段的默认行为按 schema 版本分支，
不需要在 engine 里再猜 profile 意图。
