"""判据 2 的落地证据：兼容表的事实源是常量，不是文档里的文字。

两张表分开断言，因为它们值域不同：

- ``FIELD_COMPAT``：字段 → 三值，键集合与值域都必须封闭；
- ``SCENARIO_COMPAT``：场景 → 识别行为，只断言「键在、值非空」，不枚举值。
"""

from __future__ import annotations

from finpass.passport_compat import FIELD_COMPAT, SCENARIO_COMPAT

SPEC_FIELDS = {
    "output_file_sha256",
    "profile_schema_version",
    "passport_id",
    "events.phase",
}
SPEC_SCENARIOS = {"dry_run", "auto_only"}
DOCUMENTED_FIELD_VALUES = {"warn", "skip", "error"}


def test_field_compat_keys_match_spec() -> None:
    assert set(FIELD_COMPAT) == SPEC_FIELDS


def test_scenario_compat_keys_match_spec() -> None:
    assert set(SCENARIO_COMPAT) == SPEC_SCENARIOS
    for key, value in SCENARIO_COMPAT.items():
        assert isinstance(value, str) and value.strip(), f"{key} 的值必须非空字符串"


def test_total_is_six_rows() -> None:
    # spec 判据 2：FIELD_COMPAT 4 行 + SCENARIO_COMPAT 2 行 = 兼容表 6 行
    assert len(FIELD_COMPAT) + len(SCENARIO_COMPAT) == 6


def test_field_compat_values_within_documented_enum() -> None:
    """值域封闭性检查：三值之外出现任何取值都是契约破坏。"""
    unknown = {value for value in FIELD_COMPAT.values() if value not in DOCUMENTED_FIELD_VALUES}
    assert not unknown, f"值域外取值：{sorted(unknown)}（契约：{sorted(DOCUMENTED_FIELD_VALUES)}）"


def test_events_phase_is_skip_not_skip_initial() -> None:
    # skip 的语义 = 写入端不补、读端按 PHASE_DEFAULT 处理缺失；
    # 把 initial 编进值里（skip_initial）等于让字典承担默认值的职责，已废弃。
    assert FIELD_COMPAT["events.phase"] == "skip"
