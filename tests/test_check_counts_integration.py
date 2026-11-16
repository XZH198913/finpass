"""判据 5 的集成验证：用**真实产物**核对 ``改动 + flag + 未问 == 总数``。

阶段 0 的 ``test_check_counts.py`` 只做逻辑验证（stub 数值）；本文件是阶段 1 的兑付证据，
不允许用注释代替（见 ``docs/spec-stage0.md`` 判据 5 与「阶段 1 待写入项」第 8 条）。

顺带覆盖 dry-run 的两侧：session 侧（clean 与输入逐字节一致）+ verify 侧（走 ``ext:dry_run``，
返回 0 或 2，**不返回 1**）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import finpass as fp
from finpass.check_counts import Counts
from finpass.passport import verify_exit_code, verify_passport

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _export_dry_run(tmp_path: Path) -> tuple[dict, dict[str, str], object]:
    session = fp.open(DIRTY_CSV, dry_run=True, passport_id_override="integration")
    stats = session.start(resolve_auto=True)
    written = session.export(str(tmp_path))
    passport = json.loads(Path(written["passport"]).read_text(encoding="utf-8"))
    return passport, written, stats


def test_equation_holds_on_real_artifacts(tmp_path: Path) -> None:
    passport, written, stats = _export_dry_run(tmp_path)
    counts = passport["counts"]

    # 结构自洽：三计数按「问题数」计，总数取 detect 后的队列长度
    Counts(
        fix=counts["fix"],
        flag=counts["flag"],
        unanswered=counts["unanswered"],
        checks=stats["total_questions"],
    ).validate()
    assert sum(counts.values()) == stats["total_questions"]

    # 事件条数 == 已决策问题数；changes 条数不参与比较（可多格 / 多行）
    events = [
        json.loads(line)
        for line in Path(written["events"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(events) == counts["fix"] + counts["flag"]
    assert counts["unanswered"] == 0


def test_dry_run_session_side_keeps_input_bytes(tmp_path: Path) -> None:
    passport, written, _stats = _export_dry_run(tmp_path)
    assert passport["dry_run"] is True
    assert _sha256(written["clean"]) == _sha256(DIRTY_CSV)


def test_dry_run_verify_side_never_returns_one(tmp_path: Path) -> None:
    _passport, written, _stats = _export_dry_run(tmp_path)
    ok, messages = verify_passport(written["passport"], input_path=DIRTY_CSV)
    assert ok, messages
    assert any("dry-run" in message for message in messages)
    assert verify_exit_code(written["passport"], input_path=DIRTY_CSV) in (0, 2)
