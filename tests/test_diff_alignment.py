"""diff 对齐：删行后按主键重建表格，行号不得错位。

这是 replay 的地基：``render_diff_html`` 必须用 row_key 认行，而不是用位置下标。
一旦退化成按 index 对齐，删掉第 2 行后，第 4 行的变更会被画到第 5 行上，
而 HTML 里看起来完全正常 —— 只有断言能抓住。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from finpass import diff


def _rows(html: str) -> list[str]:
    """取出所有 <tr> 片段（含表头行）。"""
    return re.findall(r"<tr>(.*?)</tr>", html, flags=re.S)


def _before_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            "收盘价": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )


def test_diff_aligned_by_row_key_not_index(tmp_path: Path) -> None:
    before = _before_frame()
    after = before.drop(index=1).reset_index(drop=True)  # 删第 2 行，后面的行整体前移
    after.loc[after["日期"] == "2024-01-04", "收盘价"] = 40.5

    changes = [
        {
            "row_key": "2024-01-04",
            "col": "收盘价",
            "before": "40.0",
            "after": "40.5",
            "change_type": "changed_value",
            "rule_id": "align_probe",
            "decision_id": "d-align",
        }
    ]
    path = diff.render_diff_html(before, changes, tmp_path / "align.html", key="日期", final=after)
    html = path.read_text(encoding="utf-8")

    marked = [row for row in _rows(html) if 'data-change-type="changed_value"' in row]
    assert len(marked) == 1, "变更标记只能落在被改的那一行"
    assert "2024-01-04" in marked[0]
    assert "40.5" in marked[0]

    # 关键：位置下标对齐会把标记画到 2024-01-05 上，其他行必须干净
    others = [row for row in _rows(html) if row not in marked]
    assert all("data-change-type" not in row for row in others)


SYNTHETIC_CHANGES = [
    {"row_key": "A", "col": "收盘价", "before": "1.0", "after": None, "change_type": "filled_nan", "rule_id": "r1", "decision_id": "d1"},
    {"row_key": "B", "col": "收盘价", "before": "2.0", "after": "2.5", "change_type": "changed_value", "rule_id": "r2", "decision_id": "d2"},
    {"row_key": "B", "col": "日期", "before": "B", "after": "B", "change_type": "normalized", "rule_id": "r3", "decision_id": "d3"},
    {"row_key": "B", "col": "成交量_股", "before": "20", "after": "21", "change_type": "flagged_kept", "rule_id": "r4", "decision_id": "d4"},
    {"row_key": "C", "col": "__row__", "before": "{}", "after": None, "change_type": "dropped_row", "rule_id": "r5", "decision_id": "d5"},
    {"row_key": "__col__", "col": "成交额_元", "before": None, "after": None, "change_type": "dropped_col", "rule_id": "r6", "decision_id": "d6"},
]


def test_diff_marks_all_change_types(tmp_path: Path) -> None:
    before = pd.DataFrame(
        {
            "日期": ["A", "B", "C"],
            "收盘价": [1.0, 2.0, 3.0],
            "成交量_股": [10, 20, 30],
            "成交额_元": [100, 200, 300],
        }
    )
    after = pd.DataFrame({"日期": ["A", "B"], "收盘价": [None, 2.5], "成交量_股": [10, 21]})
    reasons = {"d1": "缺失填 NaN", "d2": "手工修正", "d3": "日期归一", "d4": "打标保留", "d5": "重复行清除", "d6": "整列删除"}
    path = diff.render_diff_html(before, SYNTHETIC_CHANGES, tmp_path / "six.html", key="日期", final=after, reasons=reasons)
    html = path.read_text(encoding="utf-8")

    # 1) 6 种语义色全部出现，且调色板就是 6 个
    assert len(diff.COLOR_MAP) == 6
    for change_type, color in diff.COLOR_MAP.items():
        assert color in html, f"{change_type} 的颜色 {color} 未渲染"

    # 2) 6 条变更各带一组 data-* 溯源属性
    for attr in ("data-rule-id", "data-decision-id", "data-change-type", "data-reason"):
        assert html.count(attr) == len(SYNTHETIC_CHANGES), f"{attr} 数量与变更条数不一致"
    assert "data-change-type=\"dropped_row\"" in html

    # 3) reason 落到 hover 属性上
    assert "缺失填 NaN" in html

    # 4) 自包含：无 CDN / 外链资源
    assert "http://" not in html and "https://" not in html
    assert '<meta charset="utf-8">' in html


def test_diff_legend_and_dropped_markers_in_english(tmp_path: Path, set_lang) -> None:
    set_lang("en")
    before = pd.DataFrame(
        {
            "日期": ["A", "B", "C"],
            "收盘价": [1.0, 2.0, 3.0],
            "成交量_股": [10, 20, 30],
            "成交额_元": [100, 200, 300],
        }
    )
    after = pd.DataFrame({"日期": ["A", "B"], "收盘价": [None, 2.5], "成交量_股": [10, 21]})
    path = diff.render_diff_html(before, SYNTHETIC_CHANGES, tmp_path / "en.html", key="日期", final=after)
    html = path.read_text(encoding="utf-8")

    for label in (
        "filled missing values",
        "value changed",
        "format / unit normalized",
        "row dropped",
        "flagged and kept",
        "column dropped",
    ):
        assert label in html, f"英文图例缺 {label}"
    assert "C (dropped)" in html
    assert "column 成交额_元 (dropped)" in html
    assert "缺失值填充" not in html

