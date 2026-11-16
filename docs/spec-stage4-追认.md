# 阶段 4 追认：4a（engine 契约）与 4b-prerequisite（角色层）

## 一、4a：Gap 模型 + 五步空签名 + import 白名单

| 项 | 落地 |
|---|---|
| `models.Gap` | `Gap(rule_id, targets, rows, evidence)`；docstring 写死「不是 Question」 |
| `models.DispatchResult` | `DispatchResult(changes, flags)` |
| `engine.detect` | `(df, target_state, policy, phase="initial") -> list[Gap]` |
| `engine.dispatch` | `(gaps, target_state, policy) -> DispatchResult` |
| `engine.fix` | `(df, changes) -> Any` |
| `engine.reverify` | `(df, target_state, policy) -> list[Gap]` |
| `engine.audit` | `(events, changes, flags) -> dict[str, Any]` |
| import 白名单 | `{__future__, typing, dataclasses, pandas, numpy, .models}`，由 `test_engine_contract.py` 用 AST 断言 |

五个函数全部 `raise NotImplementedError`。`engine.sha256` 重生（`a3ebd3…` → `e9afa1…`），
理由写在 `engine.py` 模块 docstring。

**`aggregate` 的处理（偏离已获接受）**：它是 01/02 的旧入口，`session.start()` 仍在调用，
搬它会违反 4a 的「session 行为不变」。4a 只把它标成「旧入口，4c 起由 `dispatch` 取代后删除」，
并**不**纳入五步契约——`test_engine_contract.py` 只对 `STEPS` 里点名的五个函数断言，
没有「engine 内所有公开函数必须属于五步」这类集合断言，也没有 `__all__`。

**`policy` 为什么连缺段回退分支都没有**：schema 2.1.0 起加载期强制「声明了某条规则就必须
给出对应 policy 键」，所以 session 传给 engine 的 `policy` 天然完整；engine 既不接收
`schema_version`，也不读护照。engine 只认「已解析完整的 dict」。

## 二、4b-prerequisite：schema 2.2.0 角色层

### 为什么必须做

`ohlc_contradiction` 需要知道「哪三列是 high / low / close」。迁移前它在 profile 顶层的
`columns:` 里，而 `target_state` 只有 `semantic: price`（`开盘价` 也是 `price`，共 4 列），
`cross_column_rules.involves` 零消费者且顺序未被声明为语义。**对 engine 而言这是结构缺口**：
不改 schema，`engine.detect` 做不了 ohlc 检查。

定性：不是阶段 3 的迁移遗漏（`columns` 当初就是有意保留的角色层），是**阶段 4 的结构缺口**。

### 落地形态

```yaml
target_state:
  "最高价": {semantic: price, dtype: float64, role: high}
  "最低价": {semantic: price, dtype: float64, role: low}
  "收盘价": {semantic: price, dtype: float64, role: close}
```

- 顶层 `columns:` 段**删除**；`Profile.columns` 字段删除，改由 `Profile.role_columns()`
  从 `target_state` 推导，会话层 detect / fix 两处改读它。**不留两份角色来源。**
- 旧 profile 若仍写 `columns:` → 加载期明确拒绝（「columns 段已删除（schema 2.2.0）…」），
  不做静默忽略。
- `required_columns()` 由「`columns.values()`」改为「声明了 `role` 的列」，**集合不变**。

### 四条约束的执行证据

| 约束 | 证据 |
|---|---|
| `cross_column_rules` 不动 | `test_cross_column_rules_untouched_by_role_migration`：两份 profile 的 `id` 与 `involves` 顺序逐字断言（工作区无 `.git`，用断言代替 `git diff`） |
| `role` 唯一性**双向** | `test_duplicate_role_is_rejected`（两列都 `high` → 拒绝）＋ `test_missing_role_is_rejected_when_ohlc_rule_declared`（缺 `close` → 拒绝） |
| `role` 是**规则驱动**的可选字段 | `test_role_is_not_required_without_ohlc_rule`：没有 `ohlc_contradiction` 的 profile，`semantic: price` 也不需要 `role` |
| `required_columns()` 等价性有显式测试 | `test_required_columns_unchanged_after_role_migration`：a_stock 五列、futures 六列逐列断言，并显式断言 `持仓量` 不被拖成必需 |

另外两条守边界：`test_unknown_role_value_is_rejected`（`role: open` → 拒绝，值域封闭）、
`test_legacy_columns_section_is_rejected`（留旧 `columns:` → 拒绝）。

### 范围边界（明确不做）

- 不改 `engine.py`（引擎仍是 stub，不读 role）；`engine.sha256` **未变**；
- 不改 `cross_column_rules`（`involves` 仍是列名，顺序未定义为语义）；
- 不改 `fund_nav.yaml`（仍是占位）；
- 不加 `column_roles` 参数、不改 `involves` 为 role 引用、不扩展 `role: open`。

`cross_column_rules` 的最终处置留到 4b：实现 `ohlc_contradiction` 时会用到 `role`，
届时确认 `ohlc_consistency` 是不是死代码（很可能是——它与 `ohlc_contradiction` 是同一件事的
两种声明），能删就在 4b 里删。

### 基线变更（一处，必须记录）

角色搬家后 `_fixture.py --check` 先报「与基线不一致：**passport.json**」，`--write` 后：

- `passport.json` 指纹 `0294…` → `89ec…`，**唯一变化是 `profile_schema_version` 从 2.1.0 变 2.2.0**；
- `clean.csv` / `changes.parquet` / `events.jsonl` / `report.before.json` / `report.after.json` /
  `diff.html` 六个产物**逐字节未变**——这是「角色搬家没有改变任何检测或修复结果」的机械证据。

## 二·续、schema 2.3.0：主键搬进 `role: key`

### 为什么必须做

4a 钉死的 `detect(df, target_state, policy, phase)` 不含 `key`；但 `Gap.rows` 必须带主键值
（否则 `_gap_to_question` 无法按主键去重、无法造 `affected_row_keys`）。主键列原本在 profile 顶层的
`key:` 段，engine 不 import profile，拿不到。三个选项里：

- **B（`Gap.rows` 存 index）否决**：`_fix_duplicate` 会删行，index 不稳定；
- **C（`detect` 加 `key` 参数）否决**：`target_state` 是「目标态」、`key` 是「结构」，
  混在一个参数里更难维护，也让「engine 只吃目标态」这条变模糊；
- **A（`role: key`）采纳**：`key` 与 `high/low/close` 是同一类东西——「这一列在域里扮演什么」，
  上一次迁移已经把角色从 `columns:` 搬进 `target_state`，`key` 是最后一个还散落在外的角色。

### 拍板：全面迁移，不并存

删 `Profile.keys` 字段，改由 `Profile.key_columns` 从 `target_state` 推导；顶层 `key:` 段删除，
旧 profile 若仍写 `key:` → 加载期明确拒绝（「key 段已删除（schema 2.3.0）…」）。

不选「只迁 detect 读法」的理由：那会让 `Profile.keys` 与 `target_state[*].role: key` 长期并存，
正是阶段 3 明令禁止的双事实源；而且「哪份说了算」会在 4c/7b 再炸一次。改动面实际很小——
`session.keys` 只是一个实例属性，换了来源后 20 多处用法一行没动。

**唯一的顺序语义例外**：`key` 可以多列（复合主键），顺序 = `target_state` 声明顺序，
因为 `row_key` 是 `a|b` 拼接。这是全仓库唯一「声明顺序即语义」的地方，已在 `key_columns`
的 docstring 与两份 yaml 注释里写死；`involves` 的顺序仍然无关（见第四节）。

### 校验

- 值域扩到 `{high, low, close, key}`；
- `key` **豁免唯一性检查**（复合主键），`high/low/close` 仍各自唯一；
- 至少一列 `role: key`，否则加载期拒绝；
- `required_columns()` 用 `key_columns` 补回原来 `key` 段提供的必需列，集合不变。

### 基线

两档 `passport.json` 各变一次（`profile_schema_version` 2.2.0 → 2.3.0），**其余 12 个数据产物
逐字节不变**（`keys` 列表与行定位均未变）。已 `--write` 重生并在 `_fixture.py` 头部记理由。

## 三、两档 fixture（多档守护）

`tests/fixtures/futures.expected.json` 与股票档同级，**引入理由是「多档守护」，不是 4b 专项**：
两个档的主键形态（单列 / 复合）、OHLC 关系、规则集合（`unit_ambiguity` vs `limit_up_down`）都不同，
后续阶段 5（phase）、7a（patch）、7b（范式对齐）都会碰产物结构；只守股票档，每个阶段都只有一半证据。

三条一起做（否则 fixture 会变成「没人敢动的冻结产物」）：

1. **决策集固化**：`tests/_fixture.py` 新增 `DECISIONS_FUTURES`，覆盖期货档 5 条规则
   （`missing_token` / `duplicate_key` / `date_format` / `limit_up_down` / `ohlc_contradiction`）。
   `fixtures/*.expected.json` 里同时存 `decisions`，因此「能读」也「能重生」。
   有测试守这一点：`test_baseline_records_its_decision_set`。
2. **守则写进 `_fixture.py` 模块 docstring**（不是追认文档）：任何 fixture 变红先修实现，不重生基线；
   确要重生必须留一行「为什么这是行为不变下的合法字节变化」。
3. **生成命令统一**：`python tests/_fixture.py --write|--check [--profile a_stock_daily|futures|all]`，
   不允许手跑 `finpass run` 生成 fixture（否则半年后有人摸出漏了某个参数的另一种命令）。

两档自检：`[a_stock_daily] 与基线一致（7 个产物）` / `[futures] 与基线一致（7 个产物）`；
`test_fixture_baseline.py` 参数化为 6 条（3 条断言 × 2 档）。

## 四、`involves` 的顺序：不进入判据

结论：**不进入**。全仓库 `cross_column_rules` 除 `profile.py` 的解析/校验外没有消费者，
也没有 `involves[0]` 这类按下标取列的写法；角色只从 `target_state[*].role` 读。

因此断言从 `list` 改为 `set`（另加一条长度断言防重复）：用 `list` 断言会把「调整清单顺序但语义不变」
卡成阻塞，属于假守护。若将来真要引入顺序语义，必须先在 profile 合同里显式声明，
不能靠某段代码偷偷按下标取——那会让 `role` 与 `involves` 变成两个事实源。

## 五、4b 的启动条件（已满足）

- `engine.detect` 做 ohlc 时从 `target_state` 读 `role`，不需要第 5 个参数；
- 签名不变，`engine.sha256` 在 prerequisite 里未动；
- `Gap.rows` 存「检测到的行」：5 类规则检测行 == 可操作行，duplicate 的 fixer 不读
  `affected_row_keys`（它重算 `duplicated(keep=...)`），故存检测行无副作用；
- duplicate 的 key 去重发生在 `_gap_to_question` 包装层（detect 只报「命中了哪些行」）。

**4b 的硬门槛（扩展）**：`_fixture.py --check` **14 个产物**全部不变（两档 × 7；4b 不升 schema，
护照都不该再变）。若报不一致，说明 `detect` 的粒度或 `_gap_to_question` 的合并方式与今天不同——
先修合并方式，不允许用「重生基线」掩盖行为变化。

**4b 的反悔条件**：若两档无法同时不变（例如股票档过、期货档不过），说明「`engine.detect` 按行返回
+ 包装层合并」这条路走不通，应回到「`engine.detect` 按列聚合」重新设计，而不是硬推。

## 六、验收时序

1. prerequisite 落地 → `test_profile_loader.py` 25 passed、`test_stage3_schema.py` 13 passed；
2. `_fixture.py --check` → 仅 `passport.json` 变 → `--write` → 再 `--check` 一致（7 个产物）；
3. 两档验收：`test_profile_authoring.py` 9 passed；期货端到端 5 规则命中（含 `ohlc_contradiction`，
   命中行 `IC2406|2024-06-05`），`changes.col` 无角色名泄漏，`ohlc_mask` 旧取法（`columns` 原值）
   与新取法（`role_columns()`）逐行相同；
4. `DECISIONS_FUTURES` + `--profile` 落地 → `--write --profile futures` 生成期货基线 →
   `--check` 两档一致（7 + 7）→ `test_fixture_baseline.py` 6 passed；
5. `involves` 顺序确认不进入判据 → 断言改 `set` → `test_profile_loader.py` 25 passed；
6. schema 2.3.0（`role: key`）落地 → 两档 `--check` 只报 `passport.json` → `--write` →
   `--check` 一致（7 + 7）→ `test_fixture_baseline.py` 6 passed、`test_auto_only.py` 5 passed
   （护照 `keys` 未变）、`test_profile_authoring.py` 9 passed、`test_profile_loader.py` 26 passed。

## 七、三个事实（4b 实现前必须知道）

**事实 1：`detect` 当前不消费 `policy`。** 检测是完全扫描：`_rule_*` 里没有任何一处读
`policy`（`_policy(...)` 只出现在 `_fix_duplicate` / `_fix_ohlc`）。`detect` 的 `policy` 参数
是为了五步签名一致而保留，**不要**据此实现「按 policy 决定跑哪些规则」——
那会把「决策范式」误当「规则开关」。4b 要在 docstring 里写明这一点。

**事实 2：`phase="reverify"` 在 4b 里抛 `NotImplementedError`。** 4b 只实现 `phase="initial"`；
`reverify` 的语义（修完再检、产出 residuals）是 4d 的内容，提前写会与 4d 打架。

**事实 3：`duplicate_key` 的 `Gap.targets` = 主键列。** `targets` 的定义是「判据涉及的列」，
而 duplicate 的判据就是主键列（`duplicated(self.keys)`）。填主键列而不是空列表，包装层才能
用同一套逻辑取样本列、算聚合键；空列表会逼 `_gap_to_question` 对 duplicate 写特例分支。

## 八、4b 的执行方式与反悔条件

1. **参考冻结的对象是 `_detect`，不是 `start()`**。`start()` 的队列已经过 `aggregate`，
   拿它当参考等于把「聚合逻辑」混进「检测搬家」：聚合变了但队列相同 → 参考绿、问题留到 4c。
   已落地 `tests/_detect_reference.py` + `tests/fixtures/detect_reference.json`，每条只记
   `rule_id` / `targets` / `rows`（有序）/ `sample_columns`，**不记** `question_id`（聚合产物）、
   `options` / `default`（展示层）、`aggregation_level`（聚合决策）。
   当前冻结：a_stock 6 条、futures 5 条（a_stock 的 `missing_token` 在聚合前是 2 条，
   被 6 条/5 条的差异正好证明「未聚合」这一层确实有信息量）。
2. **对比分两阶段**：
   - **阶段 A**：`engine.detect` 的原始 `Gap` vs `_detect` 的原始 `Question`，按
     `(rule_id, targets)` 分组比 `rows` 的 set —— 这是 4b 的核心验收（检测逻辑搬家没变）；
   - **阶段 B**：`_gap_to_question` 聚合后的队列 vs `start()` 的队列，按 `question_id` 比 ——
     第二道验收（聚合逻辑搬家没变）。
   只做 A 会漏聚合变化，只做 B 会漏「Gap 顺序变了但聚合后队列相同」。
3. **14 产物不变**仍是硬门槛；两阶段对比有差异（哪怕产物相同）也视为 4b 未完成。
4. **同一提交删除 `session._detect`**，不留两套实现；判据：`grep -rn "_rule_" src/finpass/session.py` 为空。
5. **`_rule_*` 是「拆两半」，不是整体搬**：检测逻辑（读 df + target_state）进 `engine.detect`；
   `options` / `default` / `aggregation_level` 的生成留在 `_gap_to_question`（从 profile 读）。
   整体搬会逼 engine 读 profile 的 `options`，直接撞零 import 约束。`Gap` 不需要携带选项上下文。
6. **每个搬运逐条问「4c 之后它还属于 engine 吗」**：凡需要 `self._meta`（question 元数据）
   或选项文案的，留在包装层。搬少了会在 4c 返工，搬多了会让 engine 染上域知识——
   两者在 4b 的测试里都是绿的。

### 三个细节的现状

| 细节 | 现状 |
|---|---|
| `date_column` 回落 | 显式声明优先；未声明且**单主键** → 取主键列；**复合主键** → 加载期拒绝（不再回落到字面量「日期」，那是域词泄漏） |
| `role_columns()` 语义 | 「所有非 `key` 的 role」映射（不是固定三列）；新增角色自动包含，`_rule_ohlc_contradiction` 按名取 `high/low` |
| `key` 顺序 | `test_key_columns_order_is_pinned` 断言 a_stock `["日期"]`、futures `["合约代码","日期"]`（外加 `key_label`）；yaml 调序会红 |

## 九、4b 落地记录（检测搬家）

### 落地形态

| 项 | 落地 |
|---|---|
| `engine.detect` | 按 `__rules__.checks` 的声明顺序逐条跑 6 个检查函数，产出 `Gap`；`phase != "initial"` 抛 `NotImplementedError` |
| 检查函数 | `_check_missing_token` / `_check_duplicate_key` / `_check_ohlc` / `_check_unit_scale` / `_check_date_format` / `_check_range`，由 `_CHECKS` 分发表索引 |
| 掩码纯函数 | `_missing_mask` / `_ohlc_mask` / `_unit_mask` / `_range_mask` / `_date_mask` 从 `session.py` **搬到** `engine.py`（不留副本） |
| `RULE_TYPES` | `frozenset(_CHECKS)`：engine 是「实现了哪些 rule_type」的唯一回答者，会话层 open 阶段用它拒绝未实现 type |
| 会话层 | `_build_queue`（跑 detect + 包装）、`_detect_state`（profile → 入参）、`_check_columns`（规则查哪些列）、`_gap_to_question`（Gap → Question + `_meta`） |
| `_detect` / `_rule_*` | 同提交删除；判据 `grep -rn "_rule_" src/finpass/session.py` 输出为空（`_rule_type` 亦改名 `_type_of`） |

### 规则清单怎么进 engine（4b 唯一的新增契约）

`detect(df, target_state, policy, phase)` 只有两个数据参数，而「跑哪些规则、用哪个 rule_id、
查哪几列」既不在 `target_state` 的列语义里，也不该由 engine 猜。落地方式：会话层把
`business_rules` 解析成 `target_state` 的**保留键** `__rules__`：

```python
target_state["__rules__"] = {
    "missing_tokens": [...],
    "checks": [{"id": "unit_ambiguity", "type": "unit_scale", "columns": ["成交额_元"]}, ...],
}
```

- 否决「按 `type` 当 rule_id」：`unit_ambiguity` / `limit_up_down` 证明 profile 允许「同 type 换名字」，
  engine 自己起名会与 `_spec()`（选项、mode、修复分派都按 id 查）对不上；
- 否决「把清单塞 `policy`」：第七节事实 1 明令 policy 不是规则开关，塞进去等于把决策范式当规则表；
- 否决「engine 从 `target_state` 推断规则是否启用」：`duplicate_key` / `ohlc_contradiction` 没有任何
  列级标记，推断只能靠角色存在性猜，而「有 role 不等于声明了规则」（role 是规则驱动的可选字段）；
- `__rules__` 由会话层在调用前生成，**不落盘、不写 yaml**；事实源仍只有 `profiles/*.yaml`，
  不是第二份清单。engine 侧只认 `type`，不认识 profile 结构。

### Gap 的粒度（与阶段 A 判据的对应）

- `Gap.rows` 是**全量命中行**（主键 + 关注列，按数据行序），不截断：样本截断（前 5 条）在
  `_gap_to_question`；
- `duplicate_key` 的 `rows` 会包含重复的两侧，`affected_row_keys` 的 `unique()` 在包装层做——
  所以阶段 A 的 `rows` 按**集合**比，行序交由阶段 B 的 `affected_row_keys` 守；
- `ohlc_contradiction` 一行一条 `Gap`（聚合由 `aggregate` 决定），`targets` 排序后输出
  （判据是列集合，顺序无语义）；`duplicate_key` 的 `targets` 保持主键声明顺序（`row_key` 靠它拼）。

### 阶段名的唯一来源变了（4a 遗留的一处两写）

4a 把 `phase: str = "initial"` 写进签名时，`test_phase_default.py` 的「字面量只准出现在
`passport_compat.py`」就已经失效。4b 顺手收口：

- `engine.PHASE_INITIAL = "initial"`（**唯一字面量**），`detect` 的默认值与阶段闸门都用它；
- `passport_compat.PHASE_DEFAULT = PHASE_INITIAL`，只做别名（护照读取端继续用它）；
- `test_phase_default.py` 的期望从 `["passport_compat.py"]` 改为 `["engine.py"]`，判据本身没松：
  全包仍然只有一处字面量。

理由：engine 不能 import 上层的 `passport_compat`（4a 白名单），而阶段名属于内核；让
compat 表引用内核常量，比让两处各留一个字面量更符合「单一来源」。

### 验收证据

| 判据 | 结果 |
|---|---|
| `tests/_detect_reference.py --check` | 两档一致（a_stock 6 条 / futures 5 条）——阶段 A |
| `tests/_queue_reference.py --check` | 两档一致（6 条 / 5 条）——阶段 B，参考由搬家前 `start()` 冻结 |
| `python tests/_fixture.py --check` | 14 个产物（两档 × 7）**全部不变** |
| `pytest` | 封版时收集 184 条，按文件分组跑全部通过（`test_patch_chain_criteria` 的 4 条按阶段 0 纪律 xfail）；新增 `test_stage4_detect.py` 5 条（含保留键）、`test_stage3_schema.py` 的 `__` 列名拒绝、`test_engine_contract.py` 的 phase 闸门与角色词表断言。4c 开工前又加了中间态参考 2 条，现为 186 条 |
| `engine.sha256` | `e9afa1…` → `dc461b…`，理由记在 `test_engine_untouched_by_new_profile.py` 的重生记录里（有意的引擎改动，不是新增 profile 顺手改的） |
| `grep -rn "_rule_" src/finpass/session.py` | 无输出 |

### 明确未做

- `dispatch` / `fix` / `reverify` / `audit` 仍是 `NotImplementedError`（4c–4e）；
- `aggregate` 保留（4c 起由 `dispatch` 取代后删除），`session.start` 仍调用它；
- 未改 schema（仍 2.3.0），未改任何 profile。

## 十、封版复核：六项确认

### 1. 域词守护是「B 种情况」：绿，但语义要写清

`test_no_finance_words_in_engine.py` 扫的是 `engine.py` **源码剔除 docstring**——注释与函数名
都在扫描范围内，所以不是「只扫函数体」。它没红，是因为 11 个禁用词全是**数据里的词**
（`收盘` / `开盘` / `最高价` / `最低价` / `涨停` / `跌停` / `涨跌停` / `复权` / `成交量_股` /
`成交额_元` / `换手率`），**不含** `ohlc` / `unit` / `date` 这类 rule_type 名。实测 3 条全绿。

**选择标准（补记，写死）**：禁用词 = 数据里的词（具体列名、占位符、行内取值），不是规则类型名。

- engine 必须认识 `ohlc_contradiction` / `unit_scale` / `date_format` 才能按 type 分派——
  这是**结构化标识**，不是域知识；
- engine 不知道的是「ohlc 检查读哪三列、单位异常按什么系数换算、日期该是什么格式」，
  这些都在 profile。与「engine 不 import profile」是同一件事的两面：
  **知道「有个 ohlc 检查」≠ 知道「ohlc 读哪三列、用什么阈值」**；
- 因此函数名/常量名可以是 rule_type 名，代码里不得出现具体列名与域值词；
- 注意 docstring 豁免只覆盖 docstring，**注释仍被扫描**——举例说明域词只能放模块 docstring，
  `test_scan_ignores_module_docstring` / `test_scan_still_catches_code_lines` 已钉住豁免边界。

### 2. `__rules__`：约定 + 加载期校验 + 测试 + 投影不缓存

| 项 | 落地 |
|---|---|
| 约定 | `engine` 模块 docstring 写死「`__` 开头是元信息键，不是列」；遍历列走 `_is_meta_key`（`_key_columns` / `_role_column` 已改用，不再只认 `RULES_KEY` 一个字面量——将来加第二个保留键不会漏） |
| 加载期 | `profile._parse_target_state` 拒绝 `__` 开头的列名；`test_stage3_schema.py::test_column_name_must_not_use_reserved_prefix` |
| 测试 | `test_stage4_detect.py::test_reserved_key_is_not_treated_as_column` 断言 `Gap.targets` 不含 `__` 键——若被当主键列，`_rows` 直接 KeyError，测试会响 |
| 投影不缓存 | `Session._detect_state()` 在每次 `_build_queue()` 时现算，`__rules__` 不存字段、不落盘；`business_rules` 是唯一来源 |

**不加 schema 版本（决策，依据写死）**：拒绝 `__` 列名不升 schema 版本，因为**受影响的输入集合是空集**
（`__` 开头的列名从来没有合法语义，任何已发布的合法 profile 都不受影响），**不构成加载器合同的变化**；
而升版会改护照里的 `profile_schema_version`，进而动 14 产物——那是 4c 的账，不该记在 4b 头上。
换句话说：判「要不要升版」看的是「有没有合法输入的行为变了」，不是「校验代码有没有多一行」。

### 3. 参考脚本存续：方案 B（只读），两份都做

- `_detect_reference.py` 与 `_queue_reference.py` 的 `--write` 已删除；传入会 `SystemExit(1)` 并说明理由；
- 理由：`_detect` 已删，重生只能用当前实现写，等于「自己跟自己比」；行为真变了就该红，而不是重生刷绿；
- 人工改 JSON 仍可行（4c 换聚合口径时必然要动 `queue_reference.json`），但会出现在 diff 里，必须写理由。

### 4. `RULE_TYPES` 与 capability map：断言已改指向

- `test_capability_map.py`：`from finpass.engine import RULE_TYPES`，断言 `set(matrix) == set(RULE_TYPES)`；
- `session.RULE_HANDLERS` 已彻底删除（全仓 `grep RULE_HANDLERS` 零命中），不存在「旧清单还在」；
- `test_stage3_schema.py` 的 `BUILTIN_RULE_IDS == set(RULE_TYPES)` 同源。

### 5. `PHASE_INITIAL`：方向、断言写法、代价

- 方向是单向 `passport_compat → engine`，无环；engine 仍不 import 任何上层模块
  （`test_engine_imports_are_whitelisted` 继续守）。
- `test_phase_default.py` 是**替换**不是放宽：`assert hits == ["engine.py"]`；`passport_compat`
  里是 `PHASE_DEFAULT = PHASE_INITIAL`（赋值，不含字面量）。
- 代价（实测）：裸 `import finpass.passport_compat` 冷启动 ≈ 0.95 s（pandas 848 ms）。
  **真实路径不吃这个亏**：唯一生产引用方 `passport.py` 本来就 `import pandas / numpy`，
  CLI / verify / export 的启动时间不变；只有裸 import `passport_compat` 的测试慢一点。

### 6. `_meta["col"]`：只存列名

- `_gap_to_question` 里 `col = gap.targets[0]`，`_meta = {"col": col}`——是 `Gap.targets` 的投影，
  **不含** `semantic` / `role` / `format`；
- 消费方拿到列名后，语义仍从 `target_state` 现取（如 `_target_value(col, "unit_scale.factor", …)`）；
- 4c 的 `dispatch` 直接读 `target_state`，届时 `_meta` 随之消失，无需提前埋语义。

### 封版状态

六项确认完毕，4b 封版，4c 可开。

## 十一、4c 开工前的三项判断（已定，开工即用）

### 11.1 `fix` 的语义：纯函数（返回新 df，不改入参）

结论 **A**。依据与代价：

- `replay_changes` 已经是 `df_input.copy()` 的纯函数先例——「输入 + changes = 输出」这条链
  只该有一种形态，fix 与 replay 对齐，验证链才不会出现两种语义；
- 迁移前 `_fix_duplicate` 本来就是 `self.df = self.df.drop_duplicates(...)`（不改原对象），
  只有值写入是原地改（`self.df.at[...] = ...`），所以纯函数不改变 duplicate 的现有行为；
- 代价：每次 `fix` 复制一次 frame。决策逐题发生，百行数据可忽略；上万行再谈 `inplace` 变体，
  但那时也必须是**显式的新函数**，不许偷偷把 `fix` 改成原地。

**必须同时落地的约束（容易漏）**：`_fix_unit` / `_fix_range_check` / `_fix_ohlc` 会做
**列级 dtype 转换**（`pd.to_numeric(...).astype("float64")`），而这一步在 `changes` 里没有痕迹
（`replay_changes` 的 docstring 已说明 dtype 靠 `report.after.dtypes` 通道）。
纯 `fix` 只能从 changes 反推，规则写死：**`after` 是数值时，先把该列转 float64 再写**。

这条规则覆盖迁移前的全部可达路径，逐条核对过：

| fixer | 转换列 | 是否必有 change |
|---|---|---|
| `_fix_unit` | 该列 | 有——受影响行来自量级掩码，非 NaN，逐行 append |
| `_fix_range_check` | 该列 | 有——掩码是「严格越界」，`new != old` 必然成立（等值行会被 `continue` 跳过） |
| `_fix_ohlc` | high/low/close | 有——answer 分支逐行 append `changed_value`；**skip 分支根本不进 fixer**，所以不会出现「只转 dtype 不改值」 |

`_fix_date` 的 `after` 是字符串，不会被误转；`_fix_missing` 写 NaN，列类型不变。

已用一次性脚本验证（跑完即删）：把 `dispatch_reference.json` 的 changes 按上面这条规则纯函数重放
到 start 后的 df 上，两档的 **shape / dtypes / 逐行取值全部一致**。所以
「changes + 这条规则」足以重建 fix 的结果，`fix` 不需要再多一个参数。

### 11.2 `dispatch` 的决策输入：A/B/C 都不成立，结论是 D

三条现成方案各自死在什么地方：

| 方案 | 为什么不行 |
|---|---|
| A（`delete_pending` 延迟到 fix） | `changes` 是**审计与重放**的唯一输入，`replay_changes` 的词表只有 `{flagged_kept, dropped_col, dropped_row, 值写入}`，没有 pending；fix 要解决 pending 还得知道 `keep`，而 `fix(df, changes)` 没有 policy/params——只能再把 keep 塞进 change，那时它已经不 pending 了。`verify` 又要比对 clean.csv 与 replay 结果，A 会凭空多出一个「resolve 阶段」，而产物里没有它的位置 |
| B（决策挂在 `Gap` 上） | `Gap` 是 4b 刚冻结的「检测事实」，`detect` 是纯扫描；挂上决策后阶段 A 参考（`detect_reference.json`）与 `Gap` 的语义同时破 |
| C（`DispatchResult` 加第三个字段） | dispatch 照样要 `keep` 才能算删哪些行，问题原样搬家；且 `DispatchResult(changes, flags)` 与 `audit(events, changes, flags)` 两个签名都要改，`audit` 还要跟着挪字段 |

**D（采纳）**：`dispatch(gaps, decisions, target_state, policy) -> DispatchResult`，
其中 `decisions = {"action": "answer"|"skip"|"batch_apply", "params": {...}}`，session 按「一题一次」调用。

这不是新语义，是补 4a 漏掉的参数：`dispatch` 的 4a docstring 写的就是「把**人工决策**映射为待执行动作」，
而签名里没有决策——签名与它自己的 docstring 不一致。D 让三方各归其位：
`Gap` 只有事实、`changes` 是最终可重放的产物、`fix` 只认 changes。

代价与同提交动作：`test_step_signature_is_pinned` 的 `STEPS["dispatch"]` 加 `decisions`；
第一节表格的 `dispatch` 行改签名并注明「4a 漏参，4c 补上」；`engine.sha256` 在 4c 重生。

**顺带一件 4c 必须一并定的事**：`aggregate` 的归属。`dispatch` 的输入是 `gaps`、输出是 `changes`，
而 `aggregate` 做的是 `Question` 聚合（划定决策单元），两者不是同一件事。
建议 4c 把「gaps → 决策单元」作为 `dispatch` 的第一步，`aggregate` 随之下沉或删除；
但这是**结构变更**，要单独立判据（队列参考 + dispatch 参考双绿），不能顺手删。

### 11.3 `_meta` 使用点清单：4c 后全部消失

全仓 `_meta` 只有 4 处，且只有 `{"col": ...}` 一个键：

| 位置 | 用途 | 4c 后的替代 | 消失? |
|---|---|---|---|
| `_build_queue` | 写 `self._meta[question_id] = {"col": col}` | 不再需要（列名随 change 走） | 消失 |
| `_fix_missing` | `_meta[qid]["col"]` | `change["col"]` | 消失 |
| `_fix_unit` | 同上 | `change["col"]` | 消失 |
| `_fix_range_check` | 同上 | `change["col"]` | 消失 |

- 没有第二个键，产物与护照都不读它，所以 4c 可整体删除（连同初始化），不必推到 5/7；
- 前提本来就成立：`dispatch` 从 `Gap.targets` 取列、`changes` 落 `col`。

### 11.4 中间态参考已冻结：`dispatch_reference.json`

4c 是拆分不是搬家，只冻结最终产物不够——dispatch 算错目标值时，fix 仍可能写出同样的 clean.csv，
错误要推迟到 5（reverify 用 changes 判残差）或 7a（patch 按 changes 重放）才爆。

| 项 | 落地 |
|---|---|
| 工具 | `tests/_dispatch_reference.py`；数据 `tests/fixtures/dispatch_reference.json` |
| 粒度 | 每条决策一条：`question_id` / `rule_id` / `action` / `params` / `changes[{row_key, col, before, after, change_type}]` |
| 不记 | `decision_id`（uuid4）、最终 df（`clean.csv` 已守） |
| 冻结来源 | **4c 之前**的 `_fix_*` 实跑结果（a_stock 6 条 / futures 5 条决策） |
| 只读 | 工具从落地起就没有 `--write`，不存在「4c 后用新实现重生」的窗口 |
| 已接入 pytest | `tests/test_stage4_dispatch.py`（2 条），4c 落地时即中间态硬门槛 |

4c 的三道门槛：`dispatch_reference` 一致 + `queue_reference` 一致 + 14 产物不变。

## 十二、4c 落地记录

### 12.1 `DECISION_PARAMS` 在 engine：已知的边界妥协

4c 起「规则 → 逐题决策参数」的白名单住在 `engine.py`：

```python
DECISION_PARAMS = {
    "duplicate_key": frozenset({"duplicate_keep"}),
    "ohlc_contradiction": frozenset({"rows"}),
}
```

从「禁用词」角度看它合法：key 是 `rule_id`（结构化标识），value 是参数名（不是数据词）。
但从「engine 零域知识」的完整意义上它不干净：

> `DECISION_PARAMS` 目前硬编码在 engine，新增带决策参数的规则需改 engine。这是已知的边界妥协，不是设计目标。将来若要消除，`__rules__` 需增加 `decision_params` 字段，engine 从 `target_state` 的保留键读而不是硬编码。

现状的取舍理由：绑定关系只有两条，硬编码比「从 `__rules__` 解析」简单，且 `__rules__`
目前只传 `id` / `type` / `columns`，不传决策参数白名单。

**记在这里的原因**：不记的话，将来有人加规则时顺手改 `engine.py` 不会被质疑，
「新增数据域只写 yaml、不碰引擎代码」这条边界就悄悄丢了。改 `DECISION_PARAMS` 时必须回头读本节。

## 十三、4d 扩展：`missing_token` 的扫描范围可声明（形态原语，不是域知识）

**触发它的数据**：`data/a_stock_valuation_raw.xlsx.xlsx`（A 股估值指标，5580 只股票 × 79 列，
横截面宽表，主键 `证券代码` 且**没有日期列**）。画像见 `data-profile-a_stock_valuation.md`。

**暴露的隐含假设**：`missing_token` 从 4b 起就是「词表全局、全列扫描」（原 docstring 写「`columns`
不参与」）。在面板时序 profile 上没问题——`a_stock_daily` 只有 7 个指标列，全列扫描最多 7 题。
在宽表上它是**题量 = 列数**：74 个数值列里 69 列含 `--`，于是 69 道 `missing_token` 题 + 2 道
`range_check` = 71 题，`--max-questions` 默认 50 也拦不住（`--auto-only` 会全部打标，产出 71 条
`flagged_kept`），人既答不完也读不完。

**改了什么（一处）**：`engine._check_missing_token` 认 `columns`——空列表保持全列扫描（旧行为不变），
非空则只扫声明列；声明了不存在的列**报错**，不静默退回全列。

**为什么这是「形态」不是「域」**：引擎不知道 `pe_ttm_3m` 是什么，只知道「调用方点名了这三列」。
调用方是 profile（`business_rules[].columns`），不是引擎的默认值——没有新增 schema 字段，
没有给引擎任何列名或阈值。`test_no_finance_words_in_engine.py` 仍然通过。

**判据的落点**：
- `tests/test_stage4_scan_columns.py`（5 例）：声明限范围 / 多列顺序 / 空列表全扫 / 声明缺列报错 /
  声明列进 `required_columns`；
- 全量 `pytest`：见本次提交前的运行记录；
- 估值 profile 的题量从 71 降到 4（2 道 `missing_token`——第三列 `mktcap_total_3m` 没有 `--`，
  不产题；2 道 `range_check`）。

**已知边界，本次不处理**：

1. 横截面 profile 无法表达「我没有日期列」。加载器在单主键时把 `date_column` 默认成主键列
   （`profile.py:461-466`），于是 `a_stock_valuation` 的 `date_column` 是 `证券代码`。
   当前无消费点（`date_format` 未声明），但语义是歪的；修它要动 schema（把 `date_column`
   改成可选），不在 4d 范围。
2. 未加 `ignore_trailing_rows`：表尾说明行已由数据准备脚本按「主键格式」丢掉
   （`scripts/prepare_valuation_data.py`），引擎不需要第二处丢行逻辑。
3. 未加 `column_noise_check`：4 个「分位数」列只有 `100` 与 `--` 两个值（整列恒定），
   在 `scan_columns` 生效后它们根本不进扫描，不产生题、也不阻塞体验。加它是空转。
   若将来某天要审「整列恒定」，那也应该先有一次真实数据证明它阻塞了人。
4. 未做的事还有一件，但值得记：`clean.csv` 里**未被声明的 71 列仍保留 `--`**。
   这是 `scan_columns` 的直接后果——审计范围就是声明范围，不是整表。
   profile 的注释里写明了这一点，README 的「如何支持一种新数据形态」也写了。

## 十四、4e：`verify` 的性能（`_frame_diff` 向量化 + CLI 只跑一次）

**现象**：`finpass verify out/valuation/....passport.json --input ...`（5580 行 × 76 列）实测 **14 分钟**。

**根因不是「Python 双循环」**——42 万次索引按 20µs 算也只有 8 秒。真正的原因在内层：
`rebuilt.iloc[position][col]` **每取一个格子就重建一次整行 Series**，是 O(行×列²) 次取值。
cProfile（20 行）：`fast_xs` 3040 次调用、`blocks.iget` 215,841 次；pandas 3 的 arrow 字符串
单次取值约 12µs，于是 20 行就要 2.2 秒、100 行 20.5 秒。

**改了两处**：

1. `_frame_diff`（`passport.py:295`）改逐列向量化：先 `Series.equals` 整列短路（同值同 dtype
   直接跳过），不等才进 `_column_equal`；差异矩阵按 C 序展平，取前 5 条——**行优先、列序、文案
   逐字不变**。
2. `verify_exit_code(..., *, result=None)` 接住 CLI 已算出的结果，`_cmd_verify` 不再跑第二遍
   （改前一次 verify 命令实际跑两遍 `verify_passport`）。

**结果**：`_frame_diff` >600s → 0.009s（无差异）/ 0.27–0.36s（单格篡改、整列 NaN）；
整条命令 14 分钟 → **1.6–2.6 秒**；三项判据（hash OK / replay OK / events OK）不变；
篡改仍被抓（改 clean.csv 且**同步改掉护照里的 output_file_sha256**、只剩 replay 一道判据，
仍然 `replay 不一致` 且退出码 1）。

**两个坑，写在代码注释里，别踩**：

- 文本通道**不能**用 `Series.astype(str)`：它对 `datetime64` 会省掉时间部分
  （`2024-01-02 00:00:00` → `2024-01-02`），把真差异判成相等；
- `np.isclose` 的参数是 `atol`，`math.isclose` 才是 `abs_tol`；写错直接 TypeError。

---

## 十五、两笔记账（不为它们改代码，但要留档）

### 1. `_same` 与 `_column_equal` 是同一语义的两份实现

- `_same`（`passport.py:57`）：`replay_changes` 用它校验变更的 `before`；
- `_column_equal`（`passport.py:247`）：`_frame_diff` 用它逐格比 replay 结果与 `clean.csv`。

**4e 之前只有 `_same` 一份**（`_frame_diff` 就是逐格调它）。向量化把同一语义变成了两份实现，
于是多出一个漂移风险：改一份不检查另一份，就会出现「replay 说相等、帧比说不等」——
同一份数据在 verify 的两项判据上互相矛盾。

这不是假设，落地时对撞测试当场抓到两处：

| 漂移点 | `_same` | 朴素向量化 | 原因 |
|---|---|---|---|
| `nan` vs 字符串 `"nan"` | 真 | 假 | `str(np.nan) == "nan"`，`_same` 的 str 分支会命中 |
| `"1_000"` vs `1000` | 真 | 假 | `float("1_000")` 能过，`pd.to_numeric` 不认下划线分隔 |

两处都已修（`_column_equal` 现在照 `_same` 的分支逐条抄，数值兜底对解析失败的格子退回逐格 `float()`）。

**现状：已有自动守护。** `tests/test_verify_frame_diff_equivalence.py` 里两条用例——
`test_column_equal_agrees_with_same_on_value_pool`（53 个取值全组合 = 2809 对逐格对撞）、
`test_column_equal_agrees_with_same_on_mixed_columns`（object / float64 / datetime64 / string
四种 dtype 按列对撞）。**改 `_same` 或 `_column_equal` 任一份，必须先跑这两条；新增分支时
先把反例值加进 `VALUE_POOL`，再去改实现。**

### 2. verify 的耗时构成：本体 <0.2 秒，`import` 约 1.8 秒

5580×76 的产物上，校验本体（读护照/哈希/两次 read_csv/replay/比对/事件解析）合计 **0.19 秒**，
而 `import finpass.cli` 单独就要 **1.8 秒**（`python -X importtime` 实测 1.82s，大头是 pandas）。
所以命令行的 2 秒里，九成是启动开销。

**现在不修**：本地单次 verify 用户感知不到。但两种场景下它会累加——CI 每次 push 跑一次 verify、
或 patch 链上每代产物验一次。届时先看 `import` 再看 `_frame_diff`，别一上来就怀疑向量化退化了
（它是 0.01 秒量级，真退化会从「秒」变「分钟」）。

---

## 十六、落地三项：报错文案 / 首屏示例 / 打包

技术修复到此够了，这轮只补「不懂代码的人能不能用」的三个卡点。判据仍是那一条：
不懂代码的金融分析师 30 分钟内能独立产出带护照的干净 CSV。

### 1. 失败路径说人话（`cli.py::_explain`）

改前四类失败全是 traceback：路径写错 → `pandas/io/common.py` 栈；Excel 存成 GBK →
`UnicodeDecodeError` 栈顶在 `parsers`；profile 名打错 / 列名对不上 → `profile.py` 栈
（消息本身是中文，但埋在栈底，用户第一眼看到的是 `Traceback`）。

改后统一在 `main()` 一道闸口：`_explain` 返回一到两行中文 → stderr → 退出码 1。
映射表 = `FileNotFoundError` / `IsADirectoryError` / `UnicodeDecodeError` /
`pd.errors.EmptyDataError` / `pd.errors.ParserError` / `json.JSONDecodeError` /
`yaml.YAMLError` / `ValueError`。

**两个刻意的选择，别「优化」掉**：

- **`ValueError` 是宽口**：profile 加载器的契约就是「结构问题一律 ValueError」，窄口要维护一张
  不断生长的子类表。代价是内部 bug 若恰好是 ValueError 会被吞成一句人话——出口是
  `FINPASS_DEBUG=1`（置上即恢复原始栈，有测试守着）。
- **`_explain` 认不出时返回 `None`，由 `main` 原样抛出**。`KeyError`/`IndexError` 之类
  必须留 traceback：那是 bug，一句「参数有误」只会让报 bug 的人多绕一圈。

**唯一有行为变化的点**：用法错误（缺参数、选项拼错）的退出码 **2 → 1**（`_ArgumentParser`）。
2 在本项目里是 `EXIT_UNRESOLVED`（跑完了、有未解决项，CI 当软失败放行）；参数打错却拿 2，
等于把「命令写错了」报成「数据需要复核」，脚本会继续往下跑。

### 2. README 首屏：5 分钟跑通

补了「装 → run → 7 个产物各是什么 → verify → 退出码三态」一条线，**所有输出都是实测贴回来的**
（`run` 1.9 秒、`verify` 1.5 秒、7 个产物文件名逐个 `ls` 过）。附带一张退出码表，
把「2 不是失败」写清楚——这是新用户最容易误判的一格。

### 3. 打包（`docs/publish.md`）

- **版本号收敛成一处**：`pyproject` 改 `dynamic = ["version"]` + `attr = "finpass.__version__"`，
  之前 pyproject 与 `__init__.py` 各写一份 `0.0.1`，改一处必漂；顺带加了 `finpass --version`。
- **元数据**：classifiers（Alpha / Console / Financial / 3.10–3.13）、keywords；
  `twine check` 双 PASSED（README 能渲染）。
- **wheel 抽检**（`docs/publish.md` §2 是可复制的）：解包到独立目录、`PYTHONPATH` 指过去跑
  `run` + `verify`，确认 4 个 profile 都进了 `finpass/profiles/`、退出码 2、三项判据全 OK。
  **只信「构建成功」不够**：漏 package-data 照样构建成功，装到干净环境才炸，而那时的报错
  看起来像用户参数写错了。
- `finpass` 在 PyPI 查询返回 404（该名字下无公开项目）。

**三件必须由人定的事**（`docs/publish.md` §5）：LICENSE（Apache-2.0 与 MIT 对商用方含义不同）、
项目主页 URL、PyPI token。前两项各一行 yaml，不影响代码。

### 4. 这轮没做，理由写下来

| 没做 | 理由 |
|---|---|
| GBK 自动识别（`read_csv` 加 fallback） | 只给了提示没加兜底：自动猜编码会改变现有产物的读入路径，属行为变更，得单独一轮全量回归 |
| `examples/*.csv` 进 wheel | 示例数据是仓库资产，不是运行时依赖；README 已注明 PyPI 用户换成自己的 CSV |
| CI workflow（GitHub Actions） | 没有公开仓库地址，写了也无处挂；`docs/publish.md` 的步骤如下 |

---

## 十七、发布前冻结清单的收尾（许可证 / diff 限制 / wheel 报错抽检）

### 1. LICENSE 定 Apache-2.0

不是法律问题而是定位问题：对外的承诺是「审计级清洗、决策可追溯」，商用方在意专利风险，
Apache-2.0 第 3 条有明确的专利授权与 retaliation 条款，MIT 没有。

- `LICENSE`：Apache-2.0 全文（190 行）；末尾 `Copyright 2026 FinPass` 是**占位**，上传前换主体名。
- `pyproject`：`license = "Apache-2.0"` + `license-files = ["LICENSE"]`（PEP 639 写法），
  所以 `build-system.requires` 从 `setuptools>=61` 抬到 `>=77`——77 以下不认 SPDX 字符串。
- 构建实测：`License-Expression: Apache-2.0`、`License-File: LICENSE`，
  wheel 内落 `finpass-0.0.1.dist-info/licenses/LICENSE`。
- 守护：`tests/test_packaging.py::test_license_is_declared_and_complete` 钉三件事——
  pyproject 声明、LICENSE 是**全文**（有 `TERMS AND CONDITIONS` 正文，不只是一句「详见链接」）、
  两处都在。**注意 `twine check` 查不出摘要式 LICENSE**，它只校验 README 渲染。

### 2. README 的 diff.html 描述原来是错的（已改）

原文写「逐格改动对照」，读者会以为里面有改前改后两列。实际（查 `diff.py` 与产物 HTML）：
`diff.html` 渲染的是 **clean.csv 的整张表**（`resolved` = replay 结果），逐格用底色标变更类型，
悬停看 `data-rule-id` / `data-reason`。**被修改的格子只有 after，没有 before**；唯一例外是
`dropped_row` / `dropped_col`——它们额外附一行 `（已删除）`，那一行里才是原值（`diff.py:118-129`）。

改法两层：产物表里把这一格写准（「改后 + 逐格标注」），并新增「已知限制」一节写清
「要改前值查 `changes.parquet` 的 `before` 列」，把 before/after 双列渲染明确排到 0.1 之后。
——**文档说错一处，比少说十处更贵**：用户会照着找，找不到就不再相信后面的表。

### 3. wheel 抽检加一条失败路径

只验成功路径不够：用户第一次跑撞上的多是列名与 profile 对不上。所以在 `docs/publish.md` §2
加了「用 wheel 里的代码跑一次故意给错的 CSV」，实测：

```text
[失败] profile a_stock_daily 缺少必需列：日期、最高价、最低价、收盘价、成交额_元
exit=1
```

一句中文、无 traceback。**退出码是 1 不是 2**：2 在本项目是 `EXIT_UNRESOLVED`（跑完了、
有未解决项，CI 当软失败放行）。把「命令没跑起来」也记成 2，等于让 CI 把打错字放行。

---

## 十八、`synonyms` 的处置：删声明，不接线

三件待定项的最终口径（`0.0.1` 冻结）：版权主体保持 `Copyright 2026 FinPass`（用户决定不引入
未经确认的主体名，将来换主体改一行 + 发新版本）；`[project.urls]` 留空（PyPI 允许，填错死链比
不填更糟，等公开仓库建好再补）。

### 1. 为什么删而不是接线

`synonyms` 从 2.0 起就在 schema 里，但全仓库**没有消费点**：`load_profile` 把它读进
`Profile.synonyms`，之后没人读这个字段，`validate_columns` 只做逐字比对
（`profile.py:498`）。两份 profile 声明了 7 / 9 组别名，读者会以为「CSV 列名叫 `close`
也能跑」，实际照样报「缺少必需列」。

接线不是补个映射表：同一列有多个别名时，两份文件同时命中怎么办？别名与其他列撞名怎么办？
这是「列身份解析」的语义，属 0.1.0。**0.0.1 的正确姿态是让承诺与行为一致**——删掉声明，
把「列名必须逐字一致」写进 README 的已知限制。

### 2. 改了什么

| 位置 | 改动 |
|---|---|
| `profiles/a_stock_daily.yaml` / `futures.yaml` | 删掉 `synonyms:` 段，留一行注释说明「schema 保留该键但未接线，列名须逐字一致」 |
| `profile.py::Profile.synonyms` | 字段上补注释：**当前未接线**，只解析不消费，保留仅为兼容旧文件 |
| `profile.py::load_profile` | 解析处补一行注释，指明「只解析、不消费」 |
| `README.md` 已知限制 §3 | 从「能加载但不生效，只是占位」改成「**不支持列名同义词**」，并写明两份 profile 已删声明、接线排在 0.1.0 |
| `README.md` 示例数据一节 | 措辞同步：不支持同义词，差一个字就报「缺少必需列」 |

**保留解析**是刻意的：删字段会让所有旧 profile 文件在加载期炸，而它们的问题是「写了没用的键」，
不是「写错了」。schema 兼容与「仓库内不再示范」是两件事。

### 3. 守护测试（`tests/test_profile_loader.py`）

- `test_shipped_profiles_declare_no_synonyms`：直接读 yaml 原文检查**键是否存在**（不是 grep 字符串，
  注释里出现 `synonyms` 不算），防止有人再往 profile 里加回一段不生效的声明；
- `test_synonyms_still_parse_but_never_relax_column_names`：给 `_minimal()` 喂 `synonyms`，
  断言能解析、且 `validate_columns(["date"])` 仍然报「缺少必需列」——把「解析 ≠ 生效」钉住。

### 4. 验证

- 三份可用 profile 加载正常，`synonyms == {}`，列数不变（a_stock_daily 7 / a_stock_valuation 4 / futures 9）——删的是别名不是列；
- 端到端 `run --auto-only` 退出码 2、`verify` 三项判据 OK；
- 全量 **227 passed, 5 xfailed（收集 232）**：删声明不影响任何用例，正如预期——本来就没消费点；
- `dist/` 重建，`twine check` 双 PASSED，wheel 内 4 份 profile 均无 `synonyms:` 键。
