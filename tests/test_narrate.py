"""日志层：四要素 + 六类句式 + fallback。

判据口径（与 ``docs/spec-stage4-log-追认.md`` 第二节一致）：

1. **四要素**（机械检查，本文件）：每条日志行必须有
   动作（方括号标签）、对象（列 / 行 / 主键）、依据（发现 / 含 / 越界 / 矛盾）、结果（``→``）；
2. **可读**（人工检查，本文件管不了）：用户隔一天能否复述引擎做了什么；
3. **一致性**：数量与对象只从 ``changes`` 取，日志是 ``changes`` 的翻译而非第二事实源。

第 3 条在本文件里体现为「断言里的数字都是从构造的 changes 数出来的」——不写死句式之外的量。
"""

from __future__ import annotations

import re
from typing import Any

from finpass.models import make_change
from finpass.narrate import TRACE_LINE, narrate
from finpass.profile import load_profile

A_STOCK = load_profile("a_stock_daily")
FUTURES = load_profile("futures")


def _change(
    rule_id: str,
    col: str,
    before: Any = None,
    after: Any = None,
    change_type: str = "changed_value",
    row_key: str = "r1",
) -> dict[str, Any]:
    return make_change(row_key, col, before, after, change_type, rule_id, "decision")


def _event(rule_id: str, reason: str = "", **params: Any) -> dict[str, Any]:
    return {"rule_id": rule_id, "action": "auto", "reason": reason, "params": dict(params)}


def _lines_with(changes: list[dict[str, Any]], events: list[dict[str, Any]] | None = None, profile: Any = A_STOCK) -> list[str]:
    return narrate(changes, events or [], profile)


# ---------------------------------------------------------------- 标准 1：四要素


def test_every_line_has_the_four_elements() -> None:
    """每条日志都要能被机械拆出四要素；不合格的行就是「有日志但不可读」。"""
    changes = [
        _change("duplicate_key", "日期", None, None, "flagged_kept", "2024-03-14"),
        _change("missing_token", "收盘价", "--", None, "filled_nan", "2024-03-12"),
        _change("missing_token", "成交额_元", "暂无", None, "filled_nan", "2024-03-13"),
        _change("unit_ambiguity", "成交额_元", "12,000", "1200.0", "changed_value", "2024-03-19"),
        _change("date_format", "日期", "2024/03/18", "2024-03-18", "normalized", "2024/03/18"),
        _change("ohlc_contradiction", "最高价", None, None, "flagged_kept", "2024-03-15"),
    ]
    lines = _lines_with(changes, [_event("duplicate_key", "默认路径：无人工决策")])

    body = [line for line in lines if line != TRACE_LINE]
    assert len(body) == 5, f"5 个规则分组应当各自一行，实际：{body}"
    for line in body:
        assert re.match(r"^\[[^\]]+\] ", line), f"缺动作标签：{line}"
        assert any(marker in line for marker in ("列", "行", "主键")), f"缺对象：{line}"
        assert any(marker in line for marker in ("发现", "含", "矛盾", "越界", "处")), f"缺依据：{line}"
        assert "→" in line, f"缺结果：{line}"
        assert "event_id" not in line, f"日志不该带审计标识：{line}"
    assert lines[-1] == TRACE_LINE


# ---------------------------------------------------------------- 标准 3：句式与产物同源


def test_duplicate_key_says_what_was_dropped_and_kept() -> None:
    changes = [
        _change("duplicate_key", "__row__", "整行 JSON", None, "dropped_row", "2024-03-14"),
        _change("duplicate_key", "__row__", "整行 JSON", None, "dropped_row", "2024-03-20"),
    ]

    line = _lines_with(changes)[0]
    assert line.startswith("[去重]")
    assert "主键「日期」发现 2 行重复" in line
    assert "保留首条，删除 2 行" in line  # policy.duplicate_keep = first

    overridden = _lines_with(changes, [_event("duplicate_key", "cli: 保留最后一条", duplicate_keep="last")])[0]
    assert "保留最后一条" in overridden


def test_missing_token_merges_columns_into_one_line() -> None:
    changes = [
        _change("missing_token", "收盘价", "--", None, "filled_nan", "2024-03-12"),
        _change("missing_token", "成交额_元", "暂无", None, "filled_nan", "2024-03-13"),
    ]

    lines = _lines_with(changes)
    body = [line for line in lines if line != TRACE_LINE]
    assert len(body) == 1, f"同一规则的多次触发应合并成一行：{body}"
    assert "「收盘价、成交额_元」" in body[0]
    assert "2 行" in body[0]
    assert "「--」「暂无」" in body[0]  # 依据取自 before 实际值
    assert "填充为 NaN" in body[0]


def test_unit_scale_carries_source_unit_and_factor() -> None:
    changes = [_change("unit_ambiguity", "成交额_元", "12,000", "1200.0")]

    line = _lines_with(changes)[0]
    assert line.startswith("[单位换算]")
    assert "「成交额_元」" in line
    assert "（含千分位）" in line
    assert "按 万元→元 折算" in line
    assert "factor 0.0001" in line
    assert "dtype float64" in line


def test_date_format_carries_dtype_and_format() -> None:
    changes = [_change("date_format", "日期", "2024/03/18", "2024-03-18", "normalized")]

    line = _lines_with(changes)[0]
    assert line.startswith("[日期归一]")
    assert "「日期」1 行" in line
    assert "datetime64[ns]" in line
    assert "格式 %Y-%m-%d" in line


def test_range_check_carries_bounds_from_target_state() -> None:
    changes = [_change("limit_up_down", "涨跌幅_%", "-12.5", "-10.0")]

    line = _lines_with(changes, profile=FUTURES)[0]
    assert line.startswith("[范围校验]")
    assert "「涨跌幅_%」1 行越界" in line
    assert "[-10.0, 10.0]" in line  # 边界来自 target_state，不来自日志层


def test_ohlc_flag_reports_reason_and_skips_key_column() -> None:
    changes = [
        _change("ohlc_contradiction", "日期", None, None, "flagged_kept", "2024-03-15"),
        _change("ohlc_contradiction", "最高价", None, None, "flagged_kept", "2024-03-15"),
        _change("ohlc_contradiction", "最低价", None, None, "flagged_kept", "2024-03-15"),
        _change("ohlc_contradiction", "收盘价", None, None, "flagged_kept", "2024-03-15"),
    ]

    line = _lines_with(changes, [_event("ohlc_contradiction", "默认路径：无人工决策")])[0]
    assert line.startswith("[矛盾打标]")
    assert "「最高价、最低价、收盘价」" in line, f"主键列不该被当成矛盾列：{line}"
    assert "1 行矛盾" in line
    assert "打标保留，原因：默认路径：无人工决策" in line


# ---------------------------------------------------------------- fallback


def test_unknown_rule_falls_back_instead_of_breaking() -> None:
    """新增规则忘写模板时：不崩、不静默退化成 event 格式，而是明说「暂无日志模板」。"""
    changes = [
        _change("cashflow_threshold", "成交额_元", "1", "2"),
        _change("cashflow_threshold", "成交额_元", "3", "4", row_key="r2"),
    ]

    lines = _lines_with(changes)
    assert lines[0] == "[cashflow_threshold] 2 条变更（暂无日志模板，详见 events.jsonl）"
    assert lines[-1] == TRACE_LINE
