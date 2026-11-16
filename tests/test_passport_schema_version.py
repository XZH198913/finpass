"""Commit 4（阶段 3 收尾）：护照写入 ``profile_schema_version``。

两条硬约束：

1. 值取**加载器常量** ``PROFILE_SCHEMA_VERSION``，不是 profile 自己声明的 ``version``：
   前者回答「产物出自哪种结构」，后者回答「这份 profile 的内容何时改过」，两者独立演进；
2. 旧产物（2.1.0 之前导出）没有该字段时按 ``FIELD_COMPAT["profile_schema_version"]``
   的 ``warn`` 处理：输出一行提示、按 2.0.0 读，不改 ``ok``、不影响退出码。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import finpass as fp
from finpass.passport import EXIT_FAIL, read_passport, verify_exit_code, verify_passport
from finpass.profile import PROFILE_SCHEMA_VERSION

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"


def _export(tmp_path: Path) -> dict[str, str]:
    pytest.importorskip("pyarrow", reason="export 走真实 parquet")
    session = fp.open(DIRTY_CSV)
    session.start(resolve_auto=True)
    return session.export(tmp_path)


def test_export_writes_loader_schema_version(tmp_path: Path) -> None:
    passport = read_passport(_export(tmp_path)["passport"])

    assert passport["profile_schema_version"] == PROFILE_SCHEMA_VERSION


def test_schema_version_is_not_the_profile_version(tmp_path: Path) -> None:
    """两个字段并存的意义：一个记加载器合同，一个记 profile 内容。"""
    passport = read_passport(_export(tmp_path)["passport"])

    assert passport["profile_version"] == "0.1.0"
    assert passport["profile_schema_version"] != passport["profile_version"]


def test_old_passport_without_schema_version_only_warns(tmp_path: Path) -> None:
    path = Path(_export(tmp_path)["passport"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("profile_schema_version")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, messages = verify_passport(path)

    assert ok, messages
    assert "[warn] profile_schema_version 缺失，按 2.0.0 处理" in messages
    assert verify_exit_code(path) != EXIT_FAIL
