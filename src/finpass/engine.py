"""五步内核：detect / dispatch / fix / reverify / audit。

边界（阶段 4 定死，靠测试守，不靠自觉）：

- **零域知识**：不 import ``profile`` / ``session`` / ``passport`` / ``cli`` / ``diff``，
  也不出现任何金融业务词（收盘 / 涨停 / 复权 / 成交量_股 / 涨跌停等），域知识必须下沉到 profiles/*.yaml；
- **参数只有两个来源**：``target_state``（列级目标态）与 ``policy``（行级决策范式）；
- **两者都是「已解析完整」的 dict**，由 session 在调用前定稿：可选段的缺省值
  （如某个历史 schema 的 profile 没有 ``policy`` 段时用什么）由 session / 加载器决定。
  engine 不接收 schema 版本、不读护照、不持有任何默认值——否则默认值就有第二份来源。
  自 schema 2.1.0 起，加载器已在加载期强制「声明了某条规则就必须给出对应 ``policy`` 键」，
  所以 session 传进来的 ``policy`` 天然完整，engine 里没有「缺段回退」这条分支；
- **产出 ``Gap`` 而不是 ``Question``**：把 Gap 包装成问题（选项文案、聚合层级、默认建议）
  是展示层的事，见 ``models.Gap`` 的 docstring。

落地进度：4a 定死五个签名与 ``Gap``；4b 实现 ``detect``（``phase="initial"``）；
4c 实现 ``dispatch`` / ``fix``（两处签名修正：``dispatch`` 加 ``df`` / ``decisions``，
``fix`` 加 ``target_state``，见 ``spec-stage4-追认.md`` §十二）。
``reverify`` / ``audit`` 仍是 ``NotImplementedError``，按 4d–4e 搬入。

``aggregate`` 是 01/02 的旧入口，``session.start`` 仍在调用。4c 原计划由 ``dispatch`` 取代后删除，
**未执行**：聚合是「怎么问人」（``question_id`` / 层级都在展示层），engine 拿不到也不该造它；
删它必须先给 dispatch 一个不依赖展示层的决策单元定义，留给 4d 与 ``reverify`` 一起定。

**规则清单怎么进来（4b 定死）**：``detect`` 的入参只有 ``target_state`` / ``policy``，
而「跑哪些规则、用哪个 rule_id、查哪几列」不属于 engine 的域知识，也不该由 engine 猜。
所以调用方把 ``business_rules`` 解析成 ``target_state`` 的保留键 ``__rules__``：

``{"missing_tokens": [...], "checks": [{"id": 规则 id, "type": 规则类型, "columns": [...]}]}``

engine 只按 ``type`` 分发（``RULE_TYPES``），不认识的 type 明确拒绝。

**保留键约定（4b 定死）**：``target_state`` 里以 ``__`` 开头的键是**元信息键**，不是列
（目前只有 ``__rules__``）。engine 遍历列时必须走 ``_is_reserved_key`` 跳过，否则会把
``__rules__`` 当成「某列」去检查，产出的 ``Gap.targets`` 会带上一个不存在的列名——
未必崩，但结果是错的。反方向由加载器守：profile 的 ``target_state`` 不允许 ``__`` 开头的
列名，避免用户列覆盖会话层生成的保留键。

``__rules__`` 由会话层在**每次调用前**从 ``business_rules`` 生成（``Session._detect_state``），
不缓存、不落盘——它是投影不是来源，事实源仍只有 ``profiles/*.yaml``，没有第二份清单。

**``checks[*].columns`` 的语义（4d 定死）**：只声明「查哪几列」，不声明「查什么」。
空列表 = 按规则类型自己决定（主键列 / 角色列 / 全列）；非空 = 就查这几列。
4d 之前 ``missing_token`` 是「只看词表、无视 ``columns``」的唯一例外：宽表（几十个数值列，
每列都可能含占位符）下它会放大成几十道同质问题，题量随列数线性膨胀。4d 起它与其他规则
同一约定。这是**形态原语**——调用方点名扫描范围，engine 仍不认识任何一个列名。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from .models import CellChange, DispatchResult, Gap, Question, make_change, row_keys

#: 第 1 步（初始检测）的阶段名。``phase`` 的**唯一来源**在这里：``passport_compat``
#: 的 ``PHASE_DEFAULT`` 引用本常量，不再各写一份字面量（``test_phase_default.py`` 钉住
#: 「包里只有一处字面量」）。4a 只把默认值写在签名里，4b 实现阶段闸门时必须有个名字。
PHASE_INITIAL = "initial"

#: ``target_state`` 的保留键：规则清单与缺失词表。它**不是列名**，见模块 docstring。
RULES_KEY = "__rules__"

#: 主键角色（schema 2.3.0）。engine 不 import ``profile``，角色字面量在这里复述一次；
#: ``test_engine_contract.py`` 断言它与 ``profile.COLUMN_ROLES`` / ``profile.KEY_ROLE`` 相等。
KEY_ROLE = "key"
COLUMN_ROLES = frozenset({"high", "low", "close", KEY_ROLE})


def detect(
    df: Any,
    target_state: dict[str, Any],
    policy: dict[str, Any],
    phase: str = PHASE_INITIAL,
) -> list[Gap]:
    """第 1 步：按 ``target_state`` / ``policy`` 扫描数据，产出「与目标态的差距」。

    ``phase`` 区分 ``initial`` 与 ``reverify``：同一套判据，进入时与修完后各跑一次。
    ``policy`` 必须是**已解析完整**的 dict（session 负责补缺省），这里不做任何兜底。

    4b 只实现 ``phase="initial"``；``reverify`` 的语义（修完再检、产出 residuals）是 4d 的内容。

    **``policy`` 目前不参与检测**：检测是全量扫描，没有一处「按 policy 决定跑不跑」。留着这个
    参数只为五步签名一致——不要据此实现「用 policy 当规则开关」，那会把决策范式误读成规则清单。

    返回顺序 = ``__rules__.checks`` 的声明顺序；同一规则内按数据行序。包装层依赖这个顺序
    （队列顺序 → 决策顺序 → 事件顺序），所以不排序、不按字典重排。
    """
    if phase != PHASE_INITIAL:
        raise NotImplementedError(f"阶段 4b 只实现 phase={PHASE_INITIAL!r}；reverify 见 4d")

    rules = target_state.get(RULES_KEY) or {}
    keys = _key_columns(target_state)
    tokens = [str(token) for token in (rules.get("missing_tokens") or [])]
    gaps: list[Gap] = []
    for check in rules.get("checks") or []:
        rule_type = str(check.get("type") or "")
        rule_id = str(check.get("id") or rule_type)
        handler = _CHECKS.get(rule_type)
        if handler is None:
            raise ValueError(f"engine.detect 未实现的规则类型：{rule_type}（规则 {rule_id}）")
        columns = [str(column) for column in (check.get("columns") or [])]
        gaps.extend(handler(df, rule_id, columns, target_state, keys, tokens))
    return gaps


#: 单个检查函数的签名：数据、rule id、检查列、列级目标态、主键列、缺失词表。
CheckHandler = Callable[[Any, str, list[str], dict[str, Any], list[str], list[str]], list[Gap]]


def _is_reserved_key(column: Any) -> bool:
    """``target_state`` 里的键是不是保留键（``__`` 开头），见模块 docstring 的保留键约定。"""
    return str(column).startswith("__")


def _key_columns(target_state: dict[str, Any]) -> list[str]:
    """主键列，顺序 = ``target_state`` 的声明顺序（复合主键的 row_key 是 ``a|b`` 拼接）。"""
    return [
        column
        for column, state in target_state.items()
        if not _is_reserved_key(column) and isinstance(state, dict) and state.get("role") == KEY_ROLE
    ]


def _role_column(target_state: dict[str, Any], role: str) -> str:
    """单值角色对应的列；未声明返回空串（由调用方决定是否跳过）。"""
    for column, state in target_state.items():
        if not _is_reserved_key(column) and isinstance(state, dict) and state.get("role") == role:
            return column
    return ""


def _sample_columns(keys: list[str], columns: list[str]) -> list[str]:
    """样本列：主键列在前，规则关注列在后（与 ``session`` 的样本形态一致）。"""
    return [*keys, *[column for column in columns if column not in keys]]


def _rows(frame: Any, mask: Any, keys: list[str], columns: list[str]) -> list[dict[str, Any]]:
    """命中行：主键列 + 关注列，按数据行序，**不截断**（样本截断是包装层的事）。"""
    return frame.loc[mask, _sample_columns(keys, columns)].to_dict("records")


def _missing_mask(frame: Any, col: str, tokens: list[str]) -> Any:
    """该列里等于「缺失占位符」的单元格掩码。"""
    return frame[col].astype(str).isin(tokens)


def _ohlc_mask(frame: Any, high_col: str, low_col: str) -> Any:
    """``high`` 角色列低于 ``low`` 角色列的行掩码；非数值一律按「不矛盾」处理。"""
    high = pd.to_numeric(frame[high_col], errors="coerce")
    low = pd.to_numeric(frame[low_col], errors="coerce")
    return high < low


def _unit_mask(frame: Any, col: str, spread: float) -> Any:
    """量级异常行掩码：极差超过 ``spread`` 时，以中位数为界挑出离群行。

    前置条件不满足（有效值不足 2 个、无非正值、极差未超阈值）时返回全 False，
    与「规则不命中」等价。
    """
    empty = pd.Series(False, index=frame.index)
    values = pd.to_numeric(frame[col], errors="coerce")
    valid = values.dropna()
    if len(valid) < 2:
        return empty
    positive = valid[valid > 0]
    if positive.empty or positive.max() / positive.min() <= spread:
        return empty
    median = valid.median()
    return (values > median * spread) | (values < median / spread)


def _range_mask(frame: Any, col: str, lower: Any, upper: Any) -> Any:
    """越界行掩码：阈值来自目标态，缺省一侧不限。"""
    values = pd.to_numeric(frame[col], errors="coerce")
    mask = pd.Series(False, index=frame.index)
    if lower is not None:
        mask = mask | (values < float(lower))
    if upper is not None:
        mask = mask | (values > float(upper))
    return mask


def _is_iso_date(value: Any, fmt: str) -> bool:
    try:
        datetime.strptime(str(value), fmt)
    except ValueError:
        return False
    return True


def _date_mask(frame: Any, col: str, fmt: str) -> Any:
    """不符合目标格式的行掩码；``fmt`` 来自 ``target_state[列].format``。"""
    return ~frame[col].map(lambda value: _is_iso_date(value, fmt))


def _check_missing_token(
    df: Any,
    rule_id: str,
    columns: list[str],
    target_state: dict[str, Any],
    keys: list[str],
    tokens: list[str],
) -> list[Gap]:
    """缺失占位符：词表是全局的，**扫描范围**由 ``columns`` 声明。

    ``columns`` 为空 = 全列扫描（时序 profile 的老行为，向后兼容）；
    声明了 = 只扫这几列。宽表里「哪几列是本次要审的」属于数据形态，不是域知识：
    engine 不认识列名，只认识「调用方点名了这几列」。声明了不存在的列直接报错，
    不静默退回全列扫描——那会让「只扫三列」的意图失效且看不出来。
    """
    if columns:
        absent = [column for column in columns if column not in df.columns]
        if absent:
            raise ValueError(f"规则 {rule_id} 声明扫描的列不存在：{'、'.join(absent)}")
        scan = list(columns)
    else:
        scan = list(df.columns)
    gaps: list[Gap] = []
    for column in scan:
        mask = _missing_mask(df, column, tokens)
        if not mask.any():
            continue
        gaps.append(
            Gap(
                rule_id=rule_id,
                targets=[column],
                rows=_rows(df, mask, keys, [column]),
                evidence={"count": int(mask.sum())},
            )
        )
    return gaps


def _check_duplicate_key(
    df: Any,
    rule_id: str,
    columns: list[str],
    target_state: dict[str, Any],
    keys: list[str],
    tokens: list[str],
) -> list[Gap]:
    """主键重复：``targets`` 填主键列（判据就是主键本身），行去重留给包装层。"""
    if not keys:
        return []
    mask = df.duplicated(keys, keep=False)
    if not mask.any():
        return []
    return [
        Gap(
            rule_id=rule_id,
            targets=list(keys),
            rows=df.loc[mask, keys].to_dict("records"),
            evidence={"count": int(mask.sum())},
        )
    ]


def _check_ohlc(
    df: Any,
    rule_id: str,
    columns: list[str],
    target_state: dict[str, Any],
    keys: list[str],
    tokens: list[str],
) -> list[Gap]:
    """跨列矛盾：一行一条 ``Gap``（聚合层级由包装层定，engine 不决定「合并成几题」）。"""
    high = _role_column(target_state, "high")
    low = _role_column(target_state, "low")
    close = _role_column(target_state, "close")
    if high not in df.columns or low not in df.columns:
        return []
    mask = _ohlc_mask(df, high, low)
    if not mask.any():
        return []
    involved = [column for column in (high, low, close) if column]
    # ``targets`` 只描述「涉及哪些列」，顺序无语义；排序让输出不随角色声明顺序变化。
    targets = sorted(involved)
    gaps: list[Gap] = []
    for row in _rows(df, mask, keys, involved):
        gaps.append(Gap(rule_id=rule_id, targets=list(targets), rows=[row], evidence={"count": 1}))
    return gaps


def _check_unit_scale(
    df: Any,
    rule_id: str,
    columns: list[str],
    target_state: dict[str, Any],
    keys: list[str],
    tokens: list[str],
) -> list[Gap]:
    """量级跳变：阈值 ``target_state[列].unit_scale.spread``，缺失即配置错误。"""
    gaps: list[Gap] = []
    for column in columns:
        if column not in df.columns:
            continue
        span = (target_state.get(column) or {}).get("unit_scale") or {}
        spread = span.get("spread") if isinstance(span, dict) else None
        if spread is None:
            raise ValueError(f"规则 {rule_id} 需要 target_state[{column}].unit_scale.spread，但未声明")
        mask = _unit_mask(df, column, float(spread))
        if not mask.any():
            continue
        gaps.append(
            Gap(
                rule_id=rule_id,
                targets=[column],
                rows=_rows(df, mask, keys, [column]),
                evidence={"count": int(mask.sum()), "spread": float(spread)},
            )
        )
    return gaps


def _check_date_format(
    df: Any,
    rule_id: str,
    columns: list[str],
    target_state: dict[str, Any],
    keys: list[str],
    tokens: list[str],
) -> list[Gap]:
    """日期格式：列由调用方解析进来（profile 的 ``date_column``），格式来自目标态。"""
    gaps: list[Gap] = []
    for column in columns:
        if column not in df.columns:
            continue
        fmt = (target_state.get(column) or {}).get("format")
        if fmt is None:
            raise ValueError(f"规则 {rule_id} 需要 target_state[{column}].format，但未声明")
        mask = _date_mask(df, column, str(fmt))
        if not mask.any():
            continue
        gaps.append(
            Gap(
                rule_id=rule_id,
                targets=[column],
                rows=_rows(df, mask, keys, [column]),
                evidence={"count": int(mask.sum()), "format": str(fmt)},
            )
        )
    return gaps


def _check_range(
    df: Any,
    rule_id: str,
    columns: list[str],
    target_state: dict[str, Any],
    keys: list[str],
    tokens: list[str],
) -> list[Gap]:
    """通用区间校验：边界 ``target_state[列].range``，缺省一侧不限。"""
    gaps: list[Gap] = []
    for column in columns:
        if column not in df.columns:
            continue
        span = (target_state.get(column) or {}).get("range")
        if not isinstance(span, dict):
            raise ValueError(f"规则 {rule_id} 需要 target_state[{column}].range，但未声明")
        lower, upper = span.get("min"), span.get("max")
        mask = _range_mask(df, column, lower, upper)
        if not mask.any():
            continue
        gaps.append(
            Gap(
                rule_id=rule_id,
                targets=[column],
                rows=_rows(df, mask, keys, [column]),
                evidence={"count": int(mask.sum()), "min": lower, "max": upper},
            )
        )
    return gaps


#: 规则类型 → 检查函数。``RULE_TYPES`` 由它派生，避免「表里能跑、清单里没有」的漂移。
_CHECKS: dict[str, CheckHandler] = {
    "missing_token": _check_missing_token,
    "duplicate_key": _check_duplicate_key,
    "ohlc_contradiction": _check_ohlc,
    "unit_scale": _check_unit_scale,
    "date_format": _check_date_format,
    "range_check": _check_range,
}

#: ``detect`` 实现的规则类型。会话层用它在 open 阶段拒绝未实现的 type
#: （与 ``profile.BUILTIN_RULE_IDS`` 由测试钉成同一集合）。
RULE_TYPES = frozenset(_CHECKS)


#: 每类规则接受的**逐题决策**参数（决策层，不是范式层）。
#: 其余键一律明确拒绝：已废除的 ``scale`` 传进来就报错，而不是静默忽略后
#: 让用户以为 ``0.001`` 生效了（实际吃的是 ``target_state`` 的 factor）。
DECISION_PARAMS: dict[str, frozenset[str]] = {
    "duplicate_key": frozenset({"duplicate_keep"}),
    "ohlc_contradiction": frozenset({"rows"}),
}

#: ``duplicate_keep`` 的合法取值。engine 不 import ``profile``，这里复述一次；
#: ``test_engine_contract.py`` 断言它与 ``profile.POLICY_REQUIREMENTS["duplicate_key"][1]`` 相等。
DUPLICATE_KEEP = frozenset({"first", "last"})


def dispatch(
    df: Any,
    gaps: list[Gap],
    decisions: dict[str, Any],
    target_state: dict[str, Any],
    policy: dict[str, Any],
) -> DispatchResult:
    """第 2 步：把**一条人工决策**映射为待执行动作，产出 ``changes``。

    ``decisions`` = ``{"decision_id", "action", "params"}``：

    - ``action == "skip"``：不改值，逐（行 × 样本列）产 ``flagged_kept``；
    - 其余 action（``answer`` / ``batch_apply`` / ``auto``）按 ``rule_type`` 分派。

    两处签名修正（4a 漏参，4c 补上，见 ``spec-stage4-追认.md`` §十二）：

    - 多 ``df``：``changes`` 的 ``before`` 必须来自数据，删行还要整行 JSON——
      只给 ``gaps`` 写不出可重放的审计记录；
    - 多 ``decisions``：4a 的 docstring 写的是「把人工决策映射为待执行动作」，
      可签名里没有决策，逐题覆盖（``duplicate_keep`` / ``rows``）无处安放。

    **不做聚合**：决策单元（一道题）由调用方划定，``gaps`` 就是这道题命中的差距。
    聚合是「怎么问人」——``question_id`` / ``aggregation_level`` 都在展示层，
    engine 拿不到也不该造它，否则 engine 就得认识 ``q_missing_列`` 这类命名。
    """
    action = str(decisions.get("action") or "answer")
    params = dict(decisions.get("params") or {})
    decision_id = str(decisions.get("decision_id") or "")
    keys = _key_columns(target_state)
    changes: list[dict[str, Any]] = []
    for gap in gaps:
        rule_type = _rule_type_of(gap.rule_id, target_state)
        if action == "skip":
            # 迁移前 ``_skip`` 也不校验 params：skip 不读参数，报错只会挡住人。
            changes.extend(_flag_changes(gap, keys, rule_type, decision_id))
            continue
        allowed = DECISION_PARAMS.get(rule_type, frozenset())
        unexpected = sorted(set(params) - allowed)
        if unexpected:
            raise ValueError(
                f"规则 {gap.rule_id}（{rule_type}）不接受参数 {unexpected}；"
                f"本类规则允许的决策参数：{sorted(allowed) or '无'}"
                "（范式参数已迁到 profile 的 target_state / policy）"
            )
        handler = _DISPATCHERS.get(rule_type)
        if handler is None:
            raise ValueError(f"未知规则类型：{rule_type}（规则 {gap.rule_id}）")
        changes.extend(handler(df, gap, params, target_state, policy, keys, decision_id))
    return DispatchResult(changes=changes)


#: 单个分派函数的签名：数据、差距、决策参数、目标态、范式、主键列、决策 id。
DispatchHandler = Callable[
    [Any, Gap, dict[str, Any], dict[str, Any], dict[str, Any], list[str], str],
    list[dict[str, Any]],
]


def _dispatch_missing_token(
    df: Any,
    gap: Gap,
    params: dict[str, Any],
    target_state: dict[str, Any],
    policy: dict[str, Any],
    keys: list[str],
    decision_id: str,
) -> list[dict[str, Any]]:
    """缺失占位符：填 NaN。``col`` 只从 ``Gap.targets`` 取（不再有会话层的列缓存）。"""
    column = gap.targets[0]
    changes: list[dict[str, Any]] = []
    for row_key in _unit_row_keys(gap, keys, "missing_token"):
        index = _locate(df, row_key, keys)
        if index is None:
            continue
        changes.append(
            make_change(row_key, column, df.at[index, column], None, "filled_nan", gap.rule_id, decision_id)
        )
    return changes


def _dispatch_duplicate_key(
    df: Any,
    gap: Gap,
    params: dict[str, Any],
    target_state: dict[str, Any],
    policy: dict[str, Any],
    keys: list[str],
    decision_id: str,
) -> list[dict[str, Any]]:
    """重复主键：删哪几行由 ``duplicate_keep`` 决定（逐题覆盖优先于 policy 默认）。"""
    keep = str(params.get("duplicate_keep") or policy["duplicate_keep"])
    if keep not in DUPLICATE_KEEP:
        raise ValueError(f"duplicate_keep 只接受 {'/'.join(sorted(DUPLICATE_KEEP))}，收到：{keep!r}")
    if not keys:
        return []
    row_key_series = row_keys(df, keys)
    return [
        make_change(
            row_key_series.at[index],
            "__row__",
            json.dumps(df.loc[index].to_dict(), ensure_ascii=False, default=str),
            None,
            "dropped_row",
            gap.rule_id,
            decision_id,
        )
        for index in df.index[df.duplicated(keys, keep=keep)]
    ]


def _dispatch_ohlc_contradiction(
    df: Any,
    gap: Gap,
    params: dict[str, Any],
    target_state: dict[str, Any],
    policy: dict[str, Any],
    keys: list[str],
    decision_id: str,
) -> list[dict[str, Any]]:
    """OHLC 矛盾：逐行决策（``params["rows"]``），未指定的行取 ``policy.default_row_action``。"""
    row_decisions = {str(key): value for key, value in (params.get("rows") or {}).items()}
    fallback = str(policy["default_row_action"])
    close_column = _role_column(target_state, "close")
    price_columns = [
        column
        for column in (_role_column(target_state, "high"), _role_column(target_state, "low"))
        if column
    ]
    flagged_columns = list(gap.rows[0]) if gap.rows else list(keys)
    changes: list[dict[str, Any]] = []
    for row_key in _unit_row_keys(gap, keys, "ohlc_contradiction"):
        if row_decisions.get(str(row_key), fallback) == "skip":
            changes.extend(
                make_change(row_key, column, None, None, "flagged_kept", gap.rule_id, decision_id)
                for column in flagged_columns
            )
            continue
        index = _locate(df, row_key, keys)
        if index is None:
            continue
        after = _numeric_cell(df, index, close_column) if close_column in df.columns else None
        for column in price_columns:
            before = _numeric_cell(df, index, column)
            changes.append(
                make_change(row_key, column, before, after, "changed_value", gap.rule_id, decision_id)
            )
    return changes


def _dispatch_unit_scale(
    df: Any,
    gap: Gap,
    params: dict[str, Any],
    target_state: dict[str, Any],
    policy: dict[str, Any],
    keys: list[str],
    decision_id: str,
) -> list[dict[str, Any]]:
    """量级跳变：换算系数只有一处来源（``target_state[列].unit_scale.factor``）。"""
    column = gap.targets[0]
    scale = float(_target_node(target_state, column, gap.rule_id, "unit_scale", "factor"))
    changes: list[dict[str, Any]] = []
    for row_key in _unit_row_keys(gap, keys, "unit_scale"):
        index = _locate(df, row_key, keys)
        if index is None:
            continue
        before = _numeric_cell(df, index, column)
        if before is None:
            continue
        changes.append(
            make_change(row_key, column, before, before * scale, "normalized", gap.rule_id, decision_id)
        )
    return changes


def _dispatch_date_format(
    df: Any,
    gap: Gap,
    params: dict[str, Any],
    target_state: dict[str, Any],
    policy: dict[str, Any],
    keys: list[str],
    decision_id: str,
) -> list[dict[str, Any]]:
    """日期格式：目标格式来自 ``target_state[列].format``，转换用 ``strftime``（与迁移前一致）。"""
    column = gap.targets[0]
    fmt = (target_state.get(column) or {}).get("format")
    if fmt is None:
        raise ValueError(f"规则 {gap.rule_id} 需要 target_state[{column}].format，但未声明")
    changes: list[dict[str, Any]] = []
    for row_key in _unit_row_keys(gap, keys, "date_format"):
        index = _locate(df, row_key, keys)
        if index is None:
            continue
        before = df.at[index, column]
        after = pd.to_datetime(str(before), dayfirst=False).strftime(str(fmt))
        changes.append(
            make_change(row_key, column, before, after, "normalized", gap.rule_id, decision_id)
        )
    return changes


def _dispatch_range_check(
    df: Any,
    gap: Gap,
    params: dict[str, Any],
    target_state: dict[str, Any],
    policy: dict[str, Any],
    keys: list[str],
    decision_id: str,
) -> list[dict[str, Any]]:
    """通用区间：按 ``target_state[列].range`` 把越界值裁到边界，已经合规的行不产变更。"""
    column = gap.targets[0]
    span = (target_state.get(column) or {}).get("range")
    if not isinstance(span, dict):
        raise ValueError(f"规则 {gap.rule_id} 需要 target_state[{column}].range，但未声明")
    lower, upper = span.get("min"), span.get("max")
    changes: list[dict[str, Any]] = []
    for row_key in _unit_row_keys(gap, keys, "range_check"):
        index = _locate(df, row_key, keys)
        if index is None:
            continue
        before = _numeric_cell(df, index, column)
        if before is None:
            continue
        after = before
        if lower is not None:
            after = max(after, float(lower))
        if upper is not None:
            after = min(after, float(upper))
        if after == before:
            continue
        changes.append(
            make_change(row_key, column, before, after, "changed_value", gap.rule_id, decision_id)
        )
    return changes


#: 规则类型 → 分派函数。与 ``_CHECKS`` 同构：检测与决策各有一张表，键集必须一致。
_DISPATCHERS: dict[str, DispatchHandler] = {
    "missing_token": _dispatch_missing_token,
    "duplicate_key": _dispatch_duplicate_key,
    "ohlc_contradiction": _dispatch_ohlc_contradiction,
    "unit_scale": _dispatch_unit_scale,
    "date_format": _dispatch_date_format,
    "range_check": _dispatch_range_check,
}


def _rule_type_of(rule_id: str, target_state: dict[str, Any]) -> str:
    """``rule_id → type`` 从保留键 ``__rules__`` 查（profile 允许同 type 换名字）。"""
    rules = target_state.get(RULES_KEY) or {}
    for check in rules.get("checks") or []:
        if str(check.get("id") or check.get("type")) == rule_id:
            return str(check.get("type") or "")
    raise ValueError(f"target_state 的 {RULES_KEY}.checks 里没有规则 {rule_id}")


def _unit_row_keys(gap: Gap, keys: list[str], rule_type: str) -> list[str]:
    """这道题影响的行键，按数据行序。

    ``duplicate_key`` 去重（同一主键重复出现只算一行）——与包装层
    ``affected_row_keys`` 用 ``keys.unique()`` 的口径一致；其余规则原样保留。
    """
    if not gap.rows:
        return []
    values = [str(value) for value in row_keys(pd.DataFrame(gap.rows), keys)]
    if rule_type != "duplicate_key":
        return values
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _numeric_cell(df: Any, index: Any, column: str) -> float | None:
    """按迁移前 fixer 的口径读一个数值单元格：先转数值，再判 NaN。

    迁移前的 ``_fix_unit`` / ``_fix_range_check`` / ``_fix_ohlc`` 都先做列级
    ``to_numeric`` 再取值，所以 ``before`` 记的是「转成数值之后」的形态
    （``12000000.0`` 而不是原始字符串 ``12000000``）；脏 token 在那一层就变 NaN，
    对应行随后被 ``continue`` 跳过。dispatch 只读不写，所以要自己复现这一步。
    """
    value = pd.to_numeric(pd.Series([df.at[index, column]]), errors="coerce").iloc[0]
    return None if pd.isna(value) else float(value)


def _locate(df: Any, row_key: str, keys: list[str]) -> Any:
    """按 row_key 找行位置；找不到返回 ``None``（行可能已被前面的删重带走）。"""
    index = df.index[row_keys(df, keys) == str(row_key)]
    return index[0] if len(index) else None


def _flag_changes(gap: Gap, keys: list[str], rule_type: str, decision_id: str) -> list[dict[str, Any]]:
    """``skip`` 的产物：逐（行 × 样本列）打标保留，不改值。"""
    columns = list(gap.rows[0]) if gap.rows else list(keys)
    return [
        make_change(row_key, column, None, None, "flagged_kept", gap.rule_id, decision_id)
        for row_key in _unit_row_keys(gap, keys, rule_type)
        for column in columns
    ]


def _target_node(
    target_state: dict[str, Any],
    column: str,
    rule_id: str,
    *sections: str,
) -> Any:
    """按点路径读列级目标态，缺失即配置错误（与迁移前的 ``_target_value`` 同语义）。"""
    node: Any = target_state.get(column) or {}
    for part in sections:
        if not isinstance(node, dict) or part not in node:
            path = ".".join(sections)
            raise ValueError(f"规则 {rule_id} 需要 target_state[{column}].{path}，但未声明")
        node = node[part]
    if node is None:
        path = ".".join(sections)
        raise ValueError(f"规则 {rule_id} 需要 target_state[{column}].{path}，但值为空")
    return node


def fix(df: Any, changes: list[CellChange], target_state: dict[str, Any]) -> Any:
    """第 3 步：按 ``changes`` 改写数据，**返回新的数据对象**（不改入参）。

    第三处签名修正（4a 漏参，4c 补上）：多一个 ``target_state``。``changes`` 只记
    ``row_key``，要定位行必须知道主键列；主键自 schema 2.3.0 起就在
    ``target_state[*].role: key``，engine 本该从那里读，不必再加一个 ``keys`` 参数。

    两条实现规则（逐条对齐迁移前的 fixer）：

    - ``dropped_row``：删该主键的**末次出现**，与 ``passport.replay_changes`` 同一语义
      （``keep="last"`` 的重放在迁移前就不支持，不是 4c 引入的缺口）；
    - 值写入：``after`` 能解析成数值、且规则类型是数值类（unit / range / ohlc）时，
      先把整列转 ``float64`` 再写——迁移前三个 fixer 都做列级转换，而 changes 里没有
      dtype 痕迹（见 ``replay_changes`` 的 dtypes 通道）；``date_format`` 写的是字符串，
      绝不做这一步（否则 ``%Y%m%d`` 这种纯数字日期会被误转成数值）。
    """
    out = df.copy()
    keys = _key_columns(target_state)
    numeric_columns: set[str] = set()
    for change in changes:
        change_type = str(change["change_type"])
        if change_type == "flagged_kept":
            continue
        column = str(change["col"])
        if change_type == "dropped_row":
            index = out.index[row_keys(out, keys) == str(change["row_key"])]
            if len(index) == 0:
                raise AssertionError(f"fix 找不到被删主键：{change['row_key']}")
            out = out.drop(index[-1])
            continue
        located = out.index[row_keys(out, keys) == str(change["row_key"])]
        if len(located) == 0:
            continue  # 与迁移前一致：行已被前面的删重带走时静默跳过
        position = located[0]
        after = change["after"]
        number = _as_number(after) if _rule_type_of(str(change["rule_id"]), target_state) in _NUMERIC_RULES else None
        if number is None:
            out.at[position, column] = np.nan if after is None else after
            continue
        if column not in numeric_columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").astype("float64")
            numeric_columns.add(column)
        out.at[position, column] = number
    return out


#: 写数值的规则类型：只有它们的 ``after`` 需要「先转 float64 再写」。
_NUMERIC_RULES = frozenset({"unit_scale", "range_check", "ohlc_contradiction"})


def _as_number(value: Any) -> float | None:
    """``after`` 的数值形态；不是数值返回 ``None``（日期、字符串值走这条）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reverify(
    df: Any,
    target_state: dict[str, Any],
    policy: dict[str, Any],
) -> list[Gap]:
    """第 4 步：修完后复检，返回仍未解决的差距（阶段 5 才接进 ``run``）。"""
    raise NotImplementedError("阶段 4a：签名先定；实现见 4d")


def audit(
    events: list[Any],
    changes: list[CellChange],
    flags: list[Any],
) -> dict[str, Any]:
    """第 5 步：汇总事件 / 变更 / 打标，产出护照主体（写文件由调用方负责）。"""
    raise NotImplementedError("阶段 4a：签名先定；实现从 session.export 搬入，见 4e")


def aggregate(raw_questions: list[Question]) -> list[Question]:
    """**旧入口**（01/02）：把同签名的问题合并成一个，返回新列表。

    4c 没有删它（原计划「由 dispatch 取代」）：聚合接收的是展示层的 ``Question``，
    它的键含 ``aggregation_level`` 与问题列集合，dispatch 按契约拿不到这些。
    聚合键 = (rule_id, aggregation_level, 影响列集合)，与具体行号无关。
    删除的前提是先定义「不依赖展示层命名的决策单元」，留给 4d。
    """
    merged: dict[tuple[Any, ...], Question] = {}
    order: list[tuple[Any, ...]] = []

    for question in raw_questions:
        signature = (question.rule_id, question.aggregation_level, tuple(sorted(_columns(question))))
        if signature not in merged:
            merged[signature] = Question(
                question_id=question.question_id,
                rule_id=question.rule_id,
                aggregation_level=question.aggregation_level,
                affected_row_keys=list(question.affected_row_keys),
                samples=list(question.samples),
                options=list(question.options),
                default=question.default,
                context_samples=list(question.samples),
                estimated_changes=len(question.affected_row_keys),
            )
            order.append(signature)
            continue

        head = merged[signature]
        head.affected_row_keys.extend(question.affected_row_keys)
        head.context_samples.extend(question.samples)
        head.samples = head.context_samples[:5]
        head.estimated_changes += len(question.affected_row_keys)

    return [merged[signature] for signature in order]


def _columns(question: Question) -> set[str]:
    """从样本里推断问题涉及的列集合；无样本时退化为空集。"""
    columns: set[str] = set()
    for sample in question.samples:
        columns.update(str(col) for col in sample)
    return columns
