"""回放一致性：重放 changes 必须能重建 clean 数据。

03 定稿两档判据，缺一不可：

- 内存档：``replay(original, changes)`` 与 ``session.df`` **严格相等** ——
  同一 dtype 世界里不该有任何漂移，这一档能抓出 replay 赋值把列 dtype 放宽的 bug；
- 文件档：replay 输出与 ``clean.csv`` 读回 **归一化相等** —— CSV 不保留 dtype，
  ``int64 1200`` 与 ``float64 1200.0`` 是同一数值，必须归一化后才能比。

临时探针已废，两档都在这里。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import finpass as fp
from finpass import passport

from _utils import normalize_frame

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"
KEY = "日期"

# 与 02 的交互队列一一对应；03 只关心"答完后 changes 能否重放"。
DEMO = {
    "missing_token": ("answer", {}, "缺失填 NaN"),
    "duplicate_key": ("answer", {}, "重复保留第一条"),
    "date_format": ("answer", {}, "日期归一"),
    "ohlc_contradiction": ("skip", {}, "打标保留"),
    "unit_ambiguity": ("batch_apply", {}, "单位折算"),
}


def _run_session() -> fp.Session:
    """跑完一遍演示会话，返回已答完问题的 Session。"""
    session = fp.open(DIRTY_CSV)
    session.start()
    while (question := session.next()) is not None:
        action, params, reason = DEMO[question.rule_id]
        session.answer(question.question_id, action, params, reason)
    return session


def test_replay_rebuilds_clean_data() -> None:
    """内存档：重放结果与 session.df 严格相等（dtype 也相等，索引按位置对齐）。

    两处必须说明：

    - 索引：删行后 session.df 留下空洞（0,1,2,3,5,6,7），replay 统一 reset_index；
      clean.csv 以 ``index=False`` 落盘，索引不参与数据身份。
    - dtype：changes 只记「值」，不记「列从 str 被转成 float64」，所以严格相等
      必须补 ``dtypes`` 通道（即 ``report.after.json`` 的 ``dtypes`` 字段契约）。
    """
    session = _run_session()
    target_dtypes = {str(col): str(dtype) for col, dtype in session.df.dtypes.items()}
    rebuilt = passport.replay_changes(session.original, session.changes, key=KEY, dtypes=target_dtypes)

    pd.testing.assert_frame_equal(rebuilt, session.df.reset_index(drop=True))


def test_replay_matches_exported_clean_csv(tmp_path: Path) -> None:
    """文件档：重放结果与 clean.csv 读回归一化后相等。"""
    pytest.importorskip("pyarrow", reason="export 走真实 parquet，无 pyarrow 无法读回 changes")

    session = _run_session()
    written = session.export(tmp_path)

    changes = passport.read_changes(written["changes"])
    rebuilt = passport.replay_changes(session.original, changes, key=KEY)
    clean = pd.read_csv(written["clean"])

    pd.testing.assert_frame_equal(normalize_frame(rebuilt), normalize_frame(clean))


def test_replay_is_idempotent() -> None:
    """同一输入重放两次，结果必须逐字节一致（不只数值相等）。"""
    session = _run_session()
    first = passport.replay_changes(session.original, session.changes, key=KEY)
    second = passport.replay_changes(session.original, session.changes, key=KEY)

    pd.testing.assert_frame_equal(first, second, check_exact=True)
