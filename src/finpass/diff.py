"""按主键对齐的差异渲染：输出可独立打开的 diff.html。

6 种颜色：filled_nan / changed_value / normalized / dropped_row / flagged_kept / dropped_col。
样式全部内联、不引任何外部资源（无 CDN、无外链字体），双击即可打开。
每个被改动的位置都带 data-rule-id / data-decision-id / data-change-type / data-reason。
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Any

import pandas as pd

from .passport import replay_changes, row_keys

COLOR_MAP = {
    "filled_nan": "#d4f8d4",
    "changed_value": "#fff3cd",
    "normalized": "#d1ecf1",
    "dropped_row": "#f8d7da",
    "flagged_kept": "#ffe8b3",
    "dropped_col": "#e2e3e5",
}

_LEGEND = {
    "filled_nan": "缺失值填充",
    "changed_value": "值被修改",
    "normalized": "格式/单位归一",
    "dropped_row": "整行删除",
    "flagged_kept": "跳过打标",
    "dropped_col": "整列删除",
}


def _get(change: Any, field: str, default: Any = None) -> Any:
    """兼容 dict 与 dataclass 两种变更载体。"""
    if isinstance(change, dict):
        return change.get(field, default)
    return getattr(change, field, default)


def _cell_style(change_type: str) -> str:
    if change_type == "flagged_kept":
        # 打标用橙色描边而非底色，避免与「已修改」的黄底混淆
        return f"border:2px solid {COLOR_MAP[change_type]};background:#fffdf5"
    return f"background:{COLOR_MAP.get(change_type, '#ffffff')}"


def _data_attrs(change: Any, reasons: dict[str, str]) -> str:
    decision_id = str(_get(change, "decision_id"))
    rule_id = str(_get(change, "rule_id"))
    change_type = str(_get(change, "change_type"))
    reason = reasons.get(decision_id) or str(_get(change, "reason") or "")
    return (
        f' data-rule-id="{_html.escape(rule_id)}"'
        f' data-decision-id="{_html.escape(decision_id)}"'
        f' data-change-type="{_html.escape(change_type)}"'
        f' data-reason="{_html.escape(reason)}"'
        f' title="{_html.escape(rule_id)} / {_html.escape(decision_id)}"'
    )


def render_diff_html(
    df_before: Any,
    changes: list[Any],
    out_path: str | Path,
    key: str | list[str] | None = None,
    final: Any = None,
    reasons: dict[str, str] | None = None,
) -> Path:
    """渲染 before/after 差异表格。

    - 按主键 `key` 对齐，删行后行号不会错位；
    - `final` 省略时用 replay_changes 由 df_before + changes 重建；
    - `reasons` 是 decision_id → reason，用于 hover 与 data-reason。
    """
    changes = list(changes)
    reasons = reasons or {}
    key = key or str(df_before.columns[0])
    key_columns = [key] if isinstance(key, str) else [str(col) for col in key]
    resolved = replay_changes(df_before, changes, key) if final is None else final
    key_series = row_keys(resolved, key)

    lookup: dict[tuple[str, str], Any] = {}
    dropped_cols: list[Any] = []
    dropped_rows: list[Any] = []
    for change in changes:
        change_type = str(_get(change, "change_type"))
        if change_type == "dropped_row":
            dropped_rows.append(change)
        elif change_type == "dropped_col":
            dropped_cols.append(change)
        else:
            row_key = str(_get(change, "row_key"))
            col = str(_get(change, "col"))
            lookup[(row_key, col)] = change
            # 主键列被改写（如日期归一）后，resolved 里的主键已经是新值：
            # 不按 after 登记别名，这一格就没有颜色、也没有 hover 锚点
            if col in key_columns and len(key_columns) == 1:
                lookup[(str(_get(change, "after")), col)] = change

    rows = []
    for idx, row in resolved.iterrows():
        cells = []
        for col in resolved.columns:
            change = lookup.get((str(key_series.at[idx]), str(col)))
            attrs = _data_attrs(change, reasons) if change else ""
            style = _cell_style(str(_get(change, "change_type"))) if change else ""
            value = "" if pd.isna(row[col]) else row[col]
            cells.append(f'<td style="{style}"{attrs}>{_html.escape(str(value))}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    head = "".join(f"<th>{_html.escape(str(col))}</th>" for col in resolved.columns)
    span = max(len(resolved.columns) - 1, 1)

    for change in dropped_rows:
        rows.append(
            f'<tr style="background:{COLOR_MAP["dropped_row"]}"{_data_attrs(change, reasons)}>'
            f'<td>{_html.escape(str(_get(change, "row_key")))}（已删除）</td>'
            f'<td colspan="{span}">{_html.escape(str(_get(change, "before")))}</td></tr>'
        )
    for change in dropped_cols:
        rows.append(
            f'<tr style="background:{COLOR_MAP["dropped_col"]}"{_data_attrs(change, reasons)}>'
            f'<td>列 {_html.escape(str(_get(change, "col")))}（已删除）</td>'
            f'<td colspan="{span}">{_html.escape(str(_get(change, "before")))}</td></tr>'
        )

    legend = "".join(
        f'<span style="display:inline-block;margin-right:12px;padding:2px 8px;'
        f'background:{color}">{_html.escape(_LEGEND[name])}</span>'
        for name, color in COLOR_MAP.items()
    )
    html = (
        '<html><head><meta charset="utf-8"><title>FinPass diff</title></head><body>'
        f'<div style="margin:8px 0">{legend}</div>'
        f'<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse">'
        f"<tr>{head}</tr>{''.join(rows)}</table></body></html>"
    )
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
