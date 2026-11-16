"""阶段 1：``passport_id`` 的格式、稳定性、可覆盖性与唯一性。

格式定死为 ``{profile_name}-{YYYYMMDDTHHMMSS}-{uuid4前8位}``。
父代关系**不靠命名规则**表达（靠后续阶段的 ``patch_of``），所以这里只验格式与唯一性，
不验「父代 id 前缀」这类约定——那种约定在 rename profile / 迁移目录时会失效。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import finpass as fp

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"
PATTERN = re.compile(r"^a_stock_daily-\d{8}T\d{6}-[0-9a-f]{8}$")


def test_format_matches_profile_timestamp_uuid() -> None:
    session = fp.open(DIRTY_CSV)
    session.start(resolve_auto=True)
    value = session.passport_id()
    assert PATTERN.match(value), value


def test_stable_within_one_session() -> None:
    session = fp.open(DIRTY_CSV)
    session.start(resolve_auto=True)
    assert session.passport_id() == session.passport_id()

def test_override_is_used_verbatim() -> None:
    session = fp.open(DIRTY_CSV, passport_id_override="fixed-id-001")
    session.start(resolve_auto=True)
    assert session.passport_id() == "fixed-id-001"


def test_two_sessions_never_collide() -> None:
    first = fp.open(DIRTY_CSV)
    first.start()
    second = fp.open(DIRTY_CSV)
    second.start()
    assert first.passport_id() != second.passport_id()


def test_exported_passport_carries_id(tmp_path: Path) -> None:
    session = fp.open(DIRTY_CSV, passport_id_override="fixed-id-002")
    session.start(resolve_auto=True)
    written = session.export(str(tmp_path))
    payload = json.loads(Path(written["passport"]).read_text(encoding="utf-8"))
    assert payload["passport_id"] == "fixed-id-002"
