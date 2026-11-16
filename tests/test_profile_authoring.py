"""04 验收：新增一个域 = 新增一个 yaml，引擎与规则实现不动。

三条硬约束：

1. futures 的复合主键 ``["合约代码", "日期"]`` 能被加载并真的用于定位行；
2. futures 特有的 ``limit_up_down`` 规则来自 yaml 的通用 ``range_check`` 声明；
3. 用 futures profile 打开 A 股 CSV 必须**明确拒绝**（缺必需列），而不是静默降级。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import finpass as fp
from finpass import passport
from finpass.profile import load_profile

from _utils import normalize_frame

REPO = Path(__file__).resolve().parents[1]
FUTURES_CSV = REPO / "examples" / "futures_daily.csv"
A_STOCK_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"


def test_futures_profile_declares_composite_key() -> None:
    profile = load_profile("futures")

    assert profile.name == "futures_daily"
    # schema 2.3.0：主键由 target_state[*].role: key 声明，声明顺序即拼接顺序
    assert profile.key_columns == ["合约代码", "日期"]
    assert profile.key_label == "合约代码+日期"
    assert "limit_up_down" in profile.rule_ids
    assert profile.rules[-1].type == "range_check"


def test_futures_csv_opens_and_hits_limit_rule() -> None:
    session = fp.open(FUTURES_CSV, profile="futures")
    stats = session.start()

    assert len(session.queue) >= 1
    assert "limit_up_down" in stats["rules_hit"]
    # 复合主键的 row_key 形态是「合约代码|日期」，否则 diff / replay 对不上
    assert all("|" in str(key) for key in session.queue[0].affected_row_keys)


def test_futures_queue_covers_other_declared_rules() -> None:
    """同一份 yaml 声明了 5 条规则，10 行脏数据应把它们都戳中（不含 unit_scale）。"""
    session = fp.open(FUTURES_CSV, profile="futures")
    stats = session.start()

    assert {"missing_token", "duplicate_key", "ohlc_contradiction", "date_format", "limit_up_down"} <= set(
        stats["rules_hit"]
    )


def test_missing_key_column_is_rejected() -> None:
    with pytest.raises(ValueError) as excinfo:
        fp.open(A_STOCK_CSV, profile="futures")

    message = str(excinfo.value)
    assert "缺少必需列" in message
    assert "合约代码" in message
    assert "profile futures_daily" in message


def test_unknown_rule_type_is_rejected() -> None:
    # schema 2.0.0：made_up 不是内置规则，必须在 cross_column_rules 里有实体才能通过加载期校验，
    # 这样才轮得到会话层去拒绝「未实现的规则类型」——这条测试验的是后者。
    bad = {
        "name": "broken",
        "target_state": {"日期": {"semantic": "date", "role": "key"}, "最高价": {"semantic": "price"}},
        "cross_column_rules": [{"id": "made_up", "involves": ["日期", "最高价"]}],
        "business_rules": [{"id": "made_up", "type": "no_such_type"}],
    }
    with pytest.raises(ValueError, match="未实现的规则类型"):
        fp.open(A_STOCK_CSV, profile=bad)


def test_a_stock_profile_keeps_single_key() -> None:
    session = fp.open(A_STOCK_CSV)

    assert session.keys == ["日期"]
    assert session.key == "日期"
    assert session.profile == "a_stock_daily"


def test_shipped_a_stock_csvs_still_open() -> None:
    """04 引入 open 阶段列校验后，00/02 用的 7 列 CSV 必须仍能打开。"""
    for path in (A_STOCK_CSV, REPO / "examples" / "pingan_bank_daily.csv"):
        session = fp.open(path)
        assert session.keys == ["日期"]
        assert session.start()["rules_hit"]


def test_a_stock_required_columns_fit_seven_column_csv() -> None:
    """a_stock_daily 的必需列必须是 7 列 CSV 的子集，否则 02 重跑会炸。"""
    profile = load_profile("a_stock_daily")
    columns = set(pd.read_csv(A_STOCK_CSV, nrows=0).columns)

    assert set(profile.required_columns()) <= columns
    assert len(columns) == 7


def _answer_all(session: fp.Session) -> None:
    demo = {
        "duplicate_key": ("answer", {}),
        "missing_token": ("answer", {}),
        "date_format": ("answer", {}),
        "ohlc_contradiction": ("skip", {}),
        "limit_up_down": ("answer", {}),
        "unit_ambiguity": ("batch_apply", {}),
    }
    while (question := session.next()) is not None:
        action, params = demo[question.rule_id]
        session.answer(question.question_id, action, params, "04 演示")


def test_futures_export_replays_with_composite_key(tmp_path: Path) -> None:
    """复合主键必须能一路穿到 export → changes → replay，否则 diff/replay 会对不上行。"""
    pytest.importorskip("pyarrow", reason="export 走真实 parquet")

    session = fp.open(FUTURES_CSV, profile="futures")
    session.start()
    _answer_all(session)
    written = session.export(tmp_path)

    changes = passport.read_changes(written["changes"])
    rebuilt = passport.replay_changes(session.original, changes, key=["合约代码", "日期"])
    clean = pd.read_csv(written["clean"])

    pd.testing.assert_frame_equal(normalize_frame(rebuilt), normalize_frame(clean))
