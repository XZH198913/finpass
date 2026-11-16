"""阶段 4d：``missing_token`` 的扫描范围可声明（形态原语 ``scan_columns``）。

宽表（几十个数值列，每列都可能含同一个占位符）下，「全列扫描」会把一条规则放大成
几十道同质问题：题量 = 列数，与「有几处需要人拍板」无关。4d 起 ``checks[*].columns``
对 ``missing_token`` 生效，与其余规则同一条约定。

**判据是行为不是实现**：空列表仍全列扫描（时序 profile 的老路径不许变），
声明后只扫声明列，声明了不存在的列必须报错而不是静默退回全列。

``profile`` 侧只多一条约定：声明扫描列 = 这些列成为 ``required_columns`` 的一部分
（打开数据前就缺列，报错要早于检测）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from finpass.engine import RULES_KEY, detect
from finpass.profile import load_profile


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "key": ["a", "b", "c"],
            "left": ["1", "--", "3"],
            "right": ["--", "2", "--"],
            "untouched": ["x", "y", "z"],
        }
    )


def _state(columns: list[str]) -> dict:
    return {
        "key": {"semantic": "symbol", "role": "key"},
        "left": {"semantic": "ratio"},
        "right": {"semantic": "ratio"},
        "untouched": {"semantic": "text"},
        RULES_KEY: {
            "missing_tokens": ["--"],
            "checks": [{"id": "missing_token", "type": "missing_token", "columns": columns}],
        },
    }


def test_declared_columns_limit_scan_scope() -> None:
    """声明 ``left`` 只出一题、只带 ``left``——``right`` 的占位符不参与检测。"""
    gaps = detect(_frame(), _state(["left"]), {})

    assert [gap.targets for gap in gaps] == [["left"]]
    assert gaps[0].evidence == {"count": 1}


def test_declaration_covers_multiple_columns_in_order() -> None:
    """多列按声明顺序各出一题（顺序是包装层排队与事件顺序的依据）。"""
    gaps = detect(_frame(), _state(["right", "left"]), {})

    assert [gap.targets for gap in gaps] == [["right"], ["left"]]
    assert [gap.evidence["count"] for gap in gaps] == [2, 1]


def test_empty_columns_keeps_full_scan() -> None:
    """不声明 = 全列扫描（含主键列以外的所有列），老 profile 的行为不许变。"""
    gaps = detect(_frame(), _state([]), {})

    assert sorted(column for gap in gaps for column in gap.targets) == ["left", "right"]


def test_declaring_absent_column_fails_loudly() -> None:
    """点名了不存在的列要报错：静默退回全列扫描会让「只扫三列」的意图失效且看不出来。"""
    with pytest.raises(ValueError, match="声明扫描的列不存在：nope"):
        detect(_frame(), _state(["nope"]), {})


def test_declared_columns_become_required_columns() -> None:
    """profile 声明扫描列后，这些列进 ``required_columns``：缺列在打开阶段就被拒绝。"""
    profile = load_profile(
        {
            "name": "scan_columns_case",
            "target_state": {
                "key": {"semantic": "symbol", "role": "key"},
                "left": {"semantic": "ratio"},
            },
            "business_rules": [{"id": "missing_token", "columns": ["left"]}],
        }
    )

    assert "left" in profile.required_columns()
