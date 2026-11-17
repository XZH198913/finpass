"""05 验收：``verify`` 只信产物本身，篡改必须被抓。

四项检查各有独立用例：产物齐全 / 文件哈希 / replay 一致 / 事件链闭合。
最后一格用「旧护照」验证降级路径：没有 ``output_file_sha256`` 只告警，不假装通过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import finpass as fp
from finpass import passport

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"

DEMO = {
    "duplicate_key": ("answer", {}),
    "missing_token": ("answer", {}),
    "date_format": ("answer", {}),
    "unit_ambiguity": ("batch_apply", {}),
    "ohlc_contradiction": ("skip", {}),
}


def _export(tmp_path: Path, *, auto_only: bool = False) -> dict[str, str]:
    session = fp.open(DIRTY_CSV, auto_only=auto_only)
    session.start()
    if not auto_only:
        while (question := session.next()) is not None:
            action, params = DEMO[question.rule_id]
            session.answer(question.question_id, action, params, "verify 测试")
    return session.export(tmp_path)


@pytest.fixture()
def exported(tmp_path: Path) -> dict[str, str]:
    return _export(tmp_path)


def test_verify_passes_on_fresh_export(exported: dict[str, str]) -> None:
    ok, messages = passport.verify_passport(exported["passport"])

    assert ok, messages
    assert {"hash OK", "replay OK", "events OK"} <= set(messages)


def test_verify_catches_tampered_clean(exported: dict[str, str]) -> None:
    clean = Path(exported["clean"])
    original = clean.read_text(encoding="utf-8")
    clean.write_text(original.replace("10.0", "99.9", 1), encoding="utf-8")
    try:
        ok, messages = passport.verify_passport(exported["passport"])
    finally:
        clean.write_text(original, encoding="utf-8")

    assert not ok
    assert any("output_file_sha256 不一致" in message for message in messages)


def test_verify_catches_tampered_changes(exported: dict[str, str]) -> None:
    changes_path = Path(exported["changes"])
    pytest.importorskip("pyarrow")
    frame = pd.read_parquet(changes_path)
    target = frame.index[frame["change_type"].isin(["normalized", "changed_value"])]
    assert len(target) > 0, "该用例需要至少一条写值的变更（normalized / changed_value）"

    backup = changes_path.read_bytes()
    frame.loc[target[0], "after"] = "99999.0"
    frame.to_parquet(changes_path, index=False)
    try:
        ok, messages = passport.verify_passport(exported["passport"])
    finally:
        changes_path.write_bytes(backup)

    assert not ok
    assert any("replay 不一致" in message for message in messages)


def test_verify_catches_tampered_before(exported: dict[str, str]) -> None:
    """改 ``before`` 字段：replay 的写前校验直接失败，而不是静默重放。"""
    changes_path = Path(exported["changes"])
    pytest.importorskip("pyarrow")
    frame = pd.read_parquet(changes_path)
    # flagged_kept / dropped_row 不参与 before 校验，必须挑真正写值的变更
    target = frame.index[frame["change_type"].isin(["normalized", "changed_value", "filled_nan"])]
    assert len(target) > 0

    backup = changes_path.read_bytes()
    frame.loc[target[0], "before"] = "被改过的旧值"
    frame.to_parquet(changes_path, index=False)
    try:
        ok, messages = passport.verify_passport(exported["passport"])
    finally:
        changes_path.write_bytes(backup)

    assert not ok
    assert any("replay 不一致" in message and "before" in message for message in messages)


def test_verify_catches_orphan_decision_id(exported: dict[str, str]) -> None:
    """变更指向不存在的事件：事件溯源链断裂，即使数据本身能重建出来。"""
    changes_path = Path(exported["changes"])
    pytest.importorskip("pyarrow")
    frame = pd.read_parquet(changes_path)
    backup = changes_path.read_bytes()
    frame.loc[frame.index[0], "decision_id"] = "deadbeef"
    frame.to_parquet(changes_path, index=False)
    try:
        ok, messages = passport.verify_passport(exported["passport"])
    finally:
        changes_path.write_bytes(backup)

    assert not ok
    assert any("事件链断裂" in message and "deadbeef" in message for message in messages)


def test_verify_reports_missing_artifact(exported: dict[str, str]) -> None:
    events = Path(exported["events"])
    backup = events.read_bytes()
    events.unlink()
    try:
        ok, messages = passport.verify_passport(exported["passport"])
    finally:
        events.write_bytes(backup)

    assert not ok
    assert messages == ["缺少产物：events.jsonl"]


def test_verify_reports_missing_passport(tmp_path: Path) -> None:
    ok, messages = passport.verify_passport(tmp_path / "nope.passport.json")

    assert not ok
    assert "缺少产物" in messages[0]


def test_passport_carries_only_file_hash(exported: dict[str, str]) -> None:
    """护照只留文件字节哈希；内存指纹已移除，避免同一件事两套判据。"""
    payload = json.loads(Path(exported["passport"]).read_text(encoding="utf-8"))

    assert "output_file_sha256" in payload
    assert "output_hash" not in payload


def test_verify_ignores_legacy_memory_hash(exported: dict[str, str]) -> None:
    """旧护照里残留的 output_hash 一律忽略：它不参与任何比较。"""
    passport_path = Path(exported["passport"])
    payload = json.loads(passport_path.read_text(encoding="utf-8"))
    payload["output_hash"] = "deadbeefdeadbeef"
    passport_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, messages = passport.verify_passport(passport_path)

    assert ok, messages
    assert "hash OK" in messages


def test_old_passport_without_file_hash_only_warns(exported: dict[str, str]) -> None:
    """00/01/02 的旧护照没有 output_file_sha256：告警但 replay 通过即算通过。"""
    passport_path = Path(exported["passport"])
    payload = json.loads(passport_path.read_text(encoding="utf-8"))
    payload.pop("output_file_sha256", None)
    passport_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, messages = passport.verify_passport(passport_path)

    assert ok, messages
    assert any(message.startswith("[warn]") for message in messages)


def test_auto_only_passport_is_verifiable(tmp_path: Path) -> None:
    written = _export(tmp_path, auto_only=True)
    payload = json.loads(Path(written["passport"]).read_text(encoding="utf-8"))

    assert payload["auto_only"] is True
    assert payload["human_decisions_count"] == 0
    assert payload["unresolved_issues_count"] > 0

    ok, messages = passport.verify_passport(written["passport"])
    assert ok, messages


def test_auto_only_applies_only_auto_rules(tmp_path: Path) -> None:
    """auto 规则产生真实修改，human 规则只打标不猜。"""
    written = _export(tmp_path, auto_only=True)
    frame = pd.read_parquet(written["changes"])

    by_rule = frame.groupby("rule_id")["change_type"].unique().to_dict()
    assert set(by_rule["missing_token"]) == {"filled_nan"}
    assert set(by_rule["date_format"]) == {"normalized"}
    assert set(by_rule["duplicate_key"]) == {"flagged_kept"}
    assert set(by_rule["ohlc_contradiction"]) == {"flagged_kept"}


def test_verify_messages_in_english(exported: dict[str, str], set_lang) -> None:
    """verify 的每条结论都走文案表：默认英文、篡改提示、缺产物提示各有判据。"""
    set_lang("en")
    ok, messages = passport.verify_passport(exported["passport"])
    assert ok, messages
    assert {"hash OK", "replay OK", "events OK"} <= set(messages)

    clean = Path(exported["clean"])
    original = clean.read_text(encoding="utf-8")
    clean.write_text(original.replace("10.0", "99.9", 1), encoding="utf-8")
    try:
        ok, messages = passport.verify_passport(exported["passport"])
    finally:
        clean.write_text(original, encoding="utf-8")
    assert not ok
    assert any("output_file_sha256 mismatch" in message for message in messages)

    events = Path(exported["events"])
    backup = events.read_bytes()
    events.unlink()
    try:
        ok, messages = passport.verify_passport(exported["passport"])
    finally:
        events.write_bytes(backup)
    assert not ok
    assert messages == ["missing output: events.jsonl"]

