"""日志层：把 ``changes`` 翻译成「没读过 README 也能复述」的处理记录。

三条分工（不一致时以此为准——日志不是第二事实源）：

1. **做了什么**（动作 / 对象 / 数量 / 结果）**只从 ``changes`` 读**：
   ``changes.parquet`` 是事实源，日志只是它的翻译；
2. **为什么这么做**只从 ``events[].reason`` 读（人工决策或降级通道写下的原因）；
3. **应该是什么样**（单位 / 日期格式 / 区间边界 / dtype / 主键名）只从 ``profile`` 读，
   缺字段就省略该要素、不让日志崩——日志层不阻塞流程，也不改退出码。

刻意不做的事：

- **不带 ``event_id``**：那是审计标识，外部读者读不懂；可追溯性由末行
  「详细决策见 events.jsonl」保持，审计拿 ``rule_id`` 检索即可。
  这是把「用户可读」与「审计可追溯」分层，不是放弃可追溯；
- **不反推**：行数、列名、变更条数一律从 ``changes`` 数出来，不从 ``events`` 推；
- **不覆盖「某题已决策但没有产生任何变更」的情况**：没有 ``changes`` 就没有可翻译的事实，
  这类题只在 ``events.jsonl`` 里。数量口径以 ``changes`` 为准，这条写在这里以便将来追溯。

一条日志 = 一个 ``(rule_id, 动作)`` 分组，不是一条 ``event``：同一规则在多列命中时
合并成一行（``missing_token`` 在「收盘价」「成交额_元」各 1 行 → 一行说清两列）。
"""

from __future__ import annotations

import unicodedata
from typing import Any, Iterable, Sequence

#: 规则**类型** → 方括号里的动作标签。
#: 键是 ``rule_type`` 而不是 ``rule_id``，这样 ``unit_ambiguity``（type=unit_scale）
#: 这类自定义 id 也能命中同一份句式；查不到就退回 ``rule_id`` 本身。
LABELS: dict[str, str] = {
    "duplicate_key": "去重",
    "missing_token": "缺失填充",
    "unit_scale": "单位换算",
    "date_format": "日期归一",
    "range_check": "范围校验",
    "ohlc_contradiction": "矛盾打标",
}

#: 句式模板：``rule_type`` → 动作 → 句。四要素必须都在句子里：
#: 动作（方括号标签）、对象（``{cols}`` / ``{keys}``）、依据（「发现 / 含 / 越界 / 矛盾」）、
#: 结果（``→`` 之后的半句）。
NARRATIVE_TEMPLATES: dict[str, dict[str, str]] = {
    "duplicate_key": {
        "fix": "主键「{keys}」发现 {n} 行重复 → 保留{keep}，删除 {m} 行",
        "flag": "主键「{keys}」发现 {n} 行重复 → 打标保留，原因：{reason}",
    },
    "missing_token": {
        "fix": "{cols}{n} 行含占位符{evidence} → 填充为 {fill}",
        "flag": "{cols}{n} 行含占位符{evidence} → 打标保留，原因：{reason}",
    },
    "unit_scale": {
        "fix": "{cols}{n} 行{evidence} → {conv}",
        "flag": "{cols}{n} 行发现单位歧义{evidence} → 打标保留，原因：{reason}",
    },
    "date_format": {
        "fix": "{cols}{n} 行含格式不符的值{evidence} → 转换为 {conv}",
        "flag": "{cols}{n} 行含格式不符的值{evidence} → 打标保留，原因：{reason}",
    },
    "range_check": {
        "fix": "{cols}{n} 行越界{bounds} → 按边界裁剪",
        "flag": "{cols}{n} 行越界{bounds} → 打标保留，原因：{reason}",
    },
    "ohlc_contradiction": {
        "fix": "{cols}{n} 行矛盾 → 以收盘价为准修正",
        "flag": "{cols}{n} 行矛盾 → 打标保留，原因：{reason}",
    },
}

#: 模板缺失时的兜底（新增规则忘了写模板时可见，而不是静默退化成 event 格式）。
FALLBACK_SUFFIX = "条变更（暂无日志模板，详见 events.jsonl）"

#: 末行固定提示：把「看终端」与「查事件流」两条路径接上。
TRACE_LINE = "详细决策见 events.jsonl（按 rule_id 可直接检索原事件）"

#: 变更记录里的非数据列：``dropped_row`` 的 ``col`` 是它，主键列也会出现在打标行里。
RESERVED_COLUMNS = frozenset({"__row__"})


def narrate(
    changes: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    profile: Any = None,
) -> list[str]:
    """把一次会话的变更翻译成人话日志（一行一条，已按 ``(rule_id, 动作)`` 合并）。"""
    groups = _group(changes)
    if not groups:
        return []

    notes = _decision_notes(events)
    labels = {rule_id: _label_of(rule_id, profile) for rule_id, _ in groups}
    width = max(_display_width(label) for label in labels.values())

    lines: list[str] = []
    for (rule_id, action), group in groups.items():
        header = _header(labels[rule_id], width)
        rule_type = _rule_type(rule_id, profile)
        template = NARRATIVE_TEMPLATES.get(rule_type, {}).get(action)
        if template is None:
            lines.append(f"{header} {len(group)} {FALLBACK_SUFFIX}")
            continue
        fields = _template_fields(group, rule_type, profile, notes.get(rule_id) or {})
        lines.append(f"{header} " + template.format(**fields))
    lines.append(TRACE_LINE)
    return lines


# ------------------------------------------------------------------ 分组与查表


def _group(changes: Iterable[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """按 ``(rule_id, 动作)`` 分组，保持出现顺序。

    动作由 ``change_type`` 决定：全是 ``flagged_kept`` 才是 flag，否则是 fix——
    同一规则既改值又打标时（OHLC 逐行决策只覆盖部分行）会正确拆成两行。
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for change in changes:
        action = "flag" if change.get("change_type") == "flagged_kept" else "fix"
        key = (str(change.get("rule_id") or ""), action)
        groups.setdefault(key, []).append(change)
    return groups


def _rule_type(rule_id: str, profile: Any) -> str:
    """``rule_id`` → ``rule_type``（``unit_ambiguity`` → ``unit_scale``）。

    没有 profile（单测直接调 narrate）时按「id 即类型」处理，自定义 id 会走 fallback。
    """
    for spec in getattr(profile, "rules", None) or []:
        if spec.id == rule_id:
            return str(spec.type)
    return rule_id


def _label_of(rule_id: str, profile: Any) -> str:
    return LABELS.get(_rule_type(rule_id, profile), rule_id)


def _decision_notes(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按 ``rule_id`` 汇总事件里的 ``reason`` 与 ``params``。

    一个 rule_id 可能有多条事件：``reason`` 取**最后一条非空**（最接近最终决策），
    ``params`` 取**最后一次出现的显式值**（``duplicate_keep: last`` 这类逐题覆盖）。
    """
    notes: dict[str, dict[str, Any]] = {}
    for event in events:
        rule_id = str(event.get("rule_id") or "")
        if not rule_id:
            continue
        note = notes.setdefault(rule_id, {"reason": "", "params": {}})
        reason = str(event.get("reason") or "").strip()
        if reason:
            note["reason"] = reason
        params = event.get("params") or {}
        if isinstance(params, dict):
            note["params"].update({key: value for key, value in params.items() if value is not None})
    return notes


# ------------------------------------------------------------------ 四要素推导


def _template_fields(
    group: list[dict[str, Any]],
    rule_type: str,
    profile: Any,
    note: dict[str, Any],
) -> dict[str, Any]:
    """凑齐模板变量。所有键**始终存在**（缺值给空串），避免 ``str.format`` 抛 KeyError。"""
    key_columns = _key_columns(profile)
    columns = _columns(group, key_columns)
    # 目标态先查数据列，查不到再退到全组（含主键）：日期归一改的正是主键列「日期」，
    # 若只查数据列就拿不到 format / dtype。展示用 ``columns``，查 profile 才用 ``_all_columns``。
    options = _target_options(profile, columns) or _target_options(profile, _all_columns(group))
    return {
        "keys": _key_label(profile, key_columns),
        "n": len({str(change.get("row_key")) for change in group}),
        "m": sum(1 for change in group if change.get("change_type") == "dropped_row"),
        # 数据列为空时退到全组列（日期归一只改主键列「日期」，否则会写成「相关列」）
        "cols": _quote(columns or _all_columns(group)) or "相关列",
        "evidence": _evidence(group, rule_type),
        "fill": _fill_text(group),
        "keep": _keep_text(profile, note),
        "conv": _conversion(rule_type, profile, columns, options),
        "bounds": _bounds(options),
        "reason": str(note.get("reason") or "") or "需人工确认",
    }


def _key_columns(profile: Any) -> list[str]:
    return [str(column) for column in getattr(profile, "key_columns", None) or []]


def _key_label(profile: Any, key_columns: list[str]) -> str:
    label = str(getattr(profile, "key_label", "") or "")
    return label or "、".join(key_columns) or "主键"


def _columns(group: list[dict[str, Any]], key_columns: list[str]) -> list[str]:
    """本组涉及的**数据列**，按首次出现顺序。

    去掉主键列（打标行会重复记主键）与 ``__row__``（删行记录的占位列）：
    否则 OHLC 会写成「日期、最高价…矛盾」，把 row_key 当成矛盾列。
    """
    reserved = set(key_columns) | RESERVED_COLUMNS
    seen: dict[str, None] = {}
    for change in group:
        column = str(change.get("col") or "")
        if column and column not in reserved:
            seen.setdefault(column, None)
    return list(seen)


def _all_columns(group: list[dict[str, Any]]) -> list[str]:
    """本组出现的所有列（**含主键**），按首次出现顺序。

    与 ``_columns`` 的区别只在用途：展示要排除主键（否则「日期」被当成矛盾列），
    查 ``target_state`` 却不能排除（日期列常常就是主键，format / dtype 挂在它身上）。
    """
    seen: dict[str, None] = {}
    for change in group:
        column = str(change.get("col") or "")
        if column and column not in RESERVED_COLUMNS:
            seen.setdefault(column, None)
    return list(seen)


def _target_options(profile: Any, columns: list[str]) -> dict[str, Any]:
    """取首个数据列的 ``target_state`` 选项（单位 / 格式 / 边界 / dtype 都在这里）。"""
    for column in columns:
        target = profile.target(column) if hasattr(profile, "target") else None
        if target is not None:
            return dict(getattr(target, "options", {}) or {})
    return {}


def _quote(columns: Sequence[str]) -> str:
    return "「" + "、".join(columns) + "」" if columns else ""


def _evidence(group: list[dict[str, Any]], rule_type: str) -> str:
    """依据的数据侧痕迹：从 ``before`` 推「占位符内容 / 千分位 / 单位后缀 / 样例外」。

    纯启发式，**推不出就返回空串**（模板里该要素自然消失）：日志层不是判据层，
    宁可少说一句，也不能因为推导失败挡住运行或误报。
    """
    befores = [str(change.get("before")) for change in group if change.get("before") not in (None, "")]
    if rule_type == "missing_token":
        return "".join(f"「{value}」" for value in list(dict.fromkeys(befores))[:2])
    if rule_type == "date_format":
        return f"（如「{befores[0]}」）" if befores else ""
    if rule_type == "unit_scale":
        marks: list[str] = []
        if any("," in value or "，" in value for value in befores):
            marks.append("千分位")
        if any(char in value for value in befores for char in "万亿"):
            marks.append("单位后缀")
        return "（含" + "、".join(marks or ["异常量级"]) + "）"
    return ""


def _fill_text(group: list[dict[str, Any]]) -> str:
    """填充结果：``filled_nan`` 的 ``after``（``None`` 即 NaN）；取不到就说 NaN。"""
    values = {str(change.get("after")) for change in group if change.get("change_type") == "filled_nan"}
    values.discard("None")
    return " / ".join(sorted(values)) if values else "NaN"


def _keep_text(profile: Any, note: dict[str, Any]) -> str:
    """重复主键保留哪条：逐题覆盖（``events.params``）优先，其次 profile 的 policy。"""
    policy = getattr(profile, "policy", None) or {}
    value = str((note.get("params") or {}).get("duplicate_keep") or policy.get("duplicate_keep") or "first")
    return {"first": "首条", "last": "最后一条"}.get(value, value)


def _conversion(rule_type: str, profile: Any, columns: Sequence[str], options: dict[str, Any]) -> str:
    """结果侧的目标态：单位换算（源单位 / 系数 / 目标单位）或日期归一（dtype / 格式）。

    每段都是可选的，缺一段就不写那一段；全缺时给一句「换算为目标单位」保底，
    保证 ``→`` 后面永远有内容（结果要素不能空）。
    """
    if rule_type == "unit_scale":
        scale = options.get("unit_scale") or {}
        source = str(scale.get("from") or "")
        unit = _unit_name(profile, columns) or str(options.get("unit") or "")
        parts: list[str] = []
        if source and unit:
            parts.append(f"按 {source}→{unit} 折算")
        if scale.get("factor") is not None:
            parts.append(f"factor {scale['factor']}")
        if options.get("dtype"):
            parts.append(f"dtype {options['dtype']}")
        return "，".join(parts) or "换算为目标单位"

    dtype = str(options.get("dtype") or "")
    fmt = str(options.get("format") or "")
    parts = [value for value in (dtype, f"格式 {fmt}" if fmt else "") if value]
    return "，".join(parts) or "目标格式"


def _unit_name(profile: Any, columns: Sequence[str]) -> str:
    """目标单位：``profile.units`` 优先（``元``），其次 ``target_state[*].unit``（``CNY``）。"""
    units = getattr(profile, "units", None) or {}
    for column in columns:
        if column in units:
            return str(units[column])
    return ""


def _bounds(options: dict[str, Any]) -> str:
    """区间边界：``target_state[*].range`` 的 ``min`` / ``max``；未声明就留空。"""
    bounds = options.get("range") or {}
    low, high = bounds.get("min"), bounds.get("max")
    if low is None and high is None:
        return ""
    return f" [{low if low is not None else '-'}, {high if high is not None else '-'}]"


# ------------------------------------------------------------------ 对齐


def _display_width(text: str) -> int:
    """终端显示宽度：东亚宽字符算 2，其余算 1（只用于标签对齐，不影响内容）。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _header(label: str, width: int) -> str:
    """``[标签]`` + 空格补到同一显示宽度：右边界对齐，正文从同一列开始。

    空格按**显示宽度**补（中文标签宽度是偶数，故普通空格刚好填满差值），
    不在方括号里塞全角空格——空括号读起来像内容缺失。
    """
    gap = max(width - _display_width(label), 0)
    return f"[{label}]" + " " * gap
