"""05 降级通道：``auto_only`` 的会话语义与护照字段。

两条边界必须同时成立：

- ``auto`` 规则**真的改数据**（不是全部打标糊过去）；
- ``human`` 规则**绝不猜**（一律 flagged_kept），所以 ``human_decisions_count == 0``
  且 ``unresolved_issues_count > 0`` 是这对约束的联合指纹。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import finpass as fp

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"
FUTURES_CSV = REPO / "examples" / "futures_daily.csv"

DEMO = {
    "duplicate_key": ("answer", {}),
    "missing_token": ("answer", {}),
    "date_format": ("answer", {}),
    "unit_ambiguity": ("batch_apply", {}),
    "ohlc_contradiction": ("skip", {}),
}


def _passport(written: dict[str, str]) -> dict:
    return json.loads(Path(written["passport"]).read_text(encoding="utf-8"))


def test_auto_only_finishes_without_questions(tmp_path: Path) -> None:
    session = fp.open(DIRTY_CSV, auto_only=True)
    session.start()

    assert session.next() is None, "降级通道不应该再向人提问"
    assert session.queue, "问题仍然要被检出，只是不走人工"


def test_auto_only_events_are_marked_automatic(tmp_path: Path) -> None:
    session = fp.open(DIRTY_CSV, auto_only=True)
    session.start()

    actions = {event["action"] for event in session.events}
    assert actions <= {"auto", "auto_flag"}
    assert "answer" not in actions and "skip" not in actions


def test_auto_only_passport_counters(tmp_path: Path) -> None:
    session = fp.open(DIRTY_CSV, auto_only=True)
    session.start()
    written = session.export(tmp_path)

    passport = _passport(written)
    assert passport["auto_only"] is True
    assert passport["human_decisions_count"] == 0
    assert passport["unresolved_issues_count"] > 0
    assert passport["input_path"].endswith("pingan_bank_daily_dirty.csv")
    assert passport["keys"] == ["日期"]


def test_human_session_passport_differs(tmp_path: Path) -> None:
    session = fp.open(DIRTY_CSV)
    session.start()
    while (question := session.next()) is not None:
        action, params = DEMO[question.rule_id]
        session.answer(question.question_id, action, params, "人工决策")
    written = session.export(tmp_path)

    passport = _passport(written)
    assert passport["auto_only"] is False
    assert passport["human_decisions_count"] == len(session.events) > 0
    assert passport["keys"] == ["日期"]


def test_auto_only_records_composite_keys(tmp_path: Path) -> None:
    """复合主键的 keys 必须原样进护照，否则 verify 无法还原行定位。"""
    session = fp.open(FUTURES_CSV, profile="futures", auto_only=True)
    session.start()
    written = session.export(tmp_path)

    passport = _passport(written)
    assert passport["keys"] == ["合约代码", "日期"]
    assert passport["human_decisions_count"] == 0

    frame = pd.read_parquet(written["changes"])
    assert all("|" in row_key for row_key in frame["row_key"].unique())
