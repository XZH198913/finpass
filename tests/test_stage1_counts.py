"""阶段 1：三计数口径。

**总数 = question 队列长度**，``fix + flag + unanswered == 总数``，一一对应。
对外比较时先按 ``decision_id`` 去重——``changes`` 的条数不等于 N 或 M：
一个 fix 决策可改多格，一个 skip 决策可标多行。
"""

from __future__ import annotations

import json
from pathlib import Path

import finpass as fp
from finpass.passport import read_changes

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"


def test_sum_equals_question_count() -> None:
    session = fp.open(DIRTY_CSV)
    stats = session.start(resolve_auto=True)
    counts = session.counts()
    assert sum(counts.values()) == stats["total_questions"] == len(session.queue)
    assert counts["unanswered"] == 0  # 默认路径不空：每道题都有决策


def test_decision_ids_dedup_match_n_and_m(tmp_path: Path) -> None:
    session = fp.open(DIRTY_CSV, passport_id_override="counts-1")
    session.start(resolve_auto=True)
    written = session.export(str(tmp_path))
    changes = read_changes(written["changes"])

    flag_ids = {c["decision_id"] for c in changes if c["change_type"] == "flagged_kept"}
    fix_ids = {c["decision_id"] for c in changes if c["change_type"] != "flagged_kept"}
    counts = session.counts()

    assert len(flag_ids) == counts["flag"]
    assert len(fix_ids) == counts["fix"]
    # 反向确认「按行数计」是错的：changes 行数通常大于 N + M
    assert len(changes) >= counts["fix"] + counts["flag"]


def test_max_questions_answers_are_labelled() -> None:
    session = fp.open(DIRTY_CSV, max_questions_per_session=2)
    session.start()
    while (question := session.next()) is not None:
        session.answer(question.question_id, "skip", {}, "test")

    counts = session.counts()
    assert counts["unanswered"] == len(session.queue) - 2
    assert session.unanswered_sources() == {"max_questions": counts["unanswered"]}


def test_passport_writes_three_counts(tmp_path: Path) -> None:
    session = fp.open(DIRTY_CSV, passport_id_override="counts-2")
    session.start(resolve_auto=True)
    written = session.export(str(tmp_path))
    payload = json.loads(Path(written["passport"]).read_text(encoding="utf-8"))
    assert set(payload["counts"]) == {"fix", "flag", "unanswered"}
    assert payload["counts"] == session.counts()
