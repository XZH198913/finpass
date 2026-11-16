"""阶段 3：profile schema 2.1.0 的加载契约。

四条硬约束：

1. ``PROFILE_SCHEMA_VERSION`` 是**全局常量**（加载器约定），不写在每份 yaml 顶层；
2. ``target_state`` 必填（2.0.0 起）——旧 profile 在加载期就被拒绝，不拖到规则执行才炸；
3. ``policy`` 是 2.1.0 的兼容性新增：**声明了** ``duplicate_key`` / ``ohlc_contradiction``
   的 profile 必须给出 ``duplicate_keep`` / ``default_row_action``，没声明这两条规则的
   旧 profile 照旧加载；
4. ``business_rules`` 的每条 id 要么是内置规则，要么在 ``cross_column_rules`` 里有实体。
"""

from __future__ import annotations

import pytest

from finpass.engine import RULE_TYPES
from finpass.profile import (
    BUILTIN_RULE_IDS,
    MAX_CROSS_COLUMN_RULES,
    PROFILE_SCHEMA_VERSION,
    load_profile,
)


def _minimal(**overrides: object) -> dict:
    payload: dict = {
        "name": "demo",
        "target_state": {"日期": {"semantic": "date", "role": "key"}},
        "business_rules": ["missing_token"],
    }
    payload.update(overrides)
    return payload


def test_schema_version_is_pinned() -> None:
    # 升版必须同提交改这里：2.0.0 = target_state 必填，2.1.0 = +policy（行级范式），
    # 2.2.0 = +target_state[*].role（删掉顶层 columns 角色映射），
    # 2.3.0 = 主键改 role: key（删掉顶层 key 段）
    assert PROFILE_SCHEMA_VERSION == "2.3.0"


def test_v1_profile_without_target_state_is_rejected() -> None:
    payload = _minimal()
    payload.pop("target_state")

    with pytest.raises(ValueError, match="target_state"):
        load_profile(payload)


def test_target_state_entry_requires_semantic() -> None:
    with pytest.raises(ValueError, match="缺少 semantic"):
        load_profile(_minimal(target_state={"日期": {"dtype": "object"}}))


def test_column_name_must_not_use_reserved_prefix() -> None:
    """``__`` 开头是 engine 的元信息键（``__rules__``）：用户列必须在这里被拦下。

    反方向的约定在 ``engine._is_meta_key``；本用例是防线——没有它，某份 profile 恰好有一列
    叫 ``__rules__`` 时会覆盖会话层生成的保留键，行为不可预测。
    """
    target = {
        "日期": {"semantic": "date", "role": "key"},
        "__rules__": {"semantic": "text"},
    }

    with pytest.raises(ValueError, match="不能以 \"__\" 开头"):
        load_profile(_minimal(target_state=target))


def test_cross_column_rules_are_capped() -> None:
    target = {"日期": {"semantic": "date"}, **{f"列{i}": {"semantic": "price"} for i in range(6)}}
    rules = [{"id": f"r{i}", "involves": ["日期", f"列{i}"]} for i in range(MAX_CROSS_COLUMN_RULES + 1)]

    with pytest.raises(ValueError, match="最多 3 条"):
        load_profile(_minimal(target_state=target, cross_column_rules=rules))


def test_cross_rule_must_reference_declared_columns() -> None:
    with pytest.raises(ValueError, match="target_state 里没有的列"):
        load_profile(_minimal(cross_column_rules=[{"id": "x", "involves": ["日期", "不存在"]}]))


def test_business_rule_without_entity_is_rejected() -> None:
    with pytest.raises(ValueError, match="没有目标态承载"):
        load_profile(_minimal(business_rules=[{"id": "ghost_rule", "mode": "auto"}]))


def test_cross_rule_id_can_back_a_business_rule() -> None:
    profile = load_profile(
        _minimal(
            target_state={
                "日期": {"semantic": "date", "role": "key"},
                "最高价": {"semantic": "price"},
            },
            cross_column_rules=[{"id": "ohlc_consistency", "involves": ["日期", "最高价"]}],
            business_rules=[{"id": "ohlc_consistency", "mode": "human"}],
        )
    )

    assert [rule.id for rule in profile.cross_column_rules] == ["ohlc_consistency"]


def test_policy_section_is_optional_without_row_level_rules() -> None:
    """2.1.0 是兼容性新增：只声明 missing_token 的旧 profile 不因缺 policy 被拒。"""
    profile = load_profile(_minimal())

    assert profile.policy == {}
    assert profile.schema_version == PROFILE_SCHEMA_VERSION


def test_profile_declaring_duplicate_key_without_policy_is_rejected() -> None:
    """兼容性新增的另一半证据：声明了需要 policy 的规则 → 加载期强制。

    前半是上一条（不声明就不强制）。缺这一半，「2.0.0 → 2.1.0 只是新增」就成了单向论证，
    改 ``POLICY_REQUIREMENTS`` 时没有回归保护。
    """
    with pytest.raises(ValueError) as excinfo:
        load_profile(_minimal(business_rules=["duplicate_key"]))

    message = str(excinfo.value)
    assert "duplicate_key" in message, "报错必须点名是哪条规则"
    assert "policy.duplicate_keep" in message, "报错必须点名缺哪个字段"


def test_illegal_policy_value_is_rejected_at_load() -> None:
    with pytest.raises(ValueError, match="policy.duplicate_keep 非法"):
        load_profile(_minimal(business_rules=["duplicate_key"], policy={"duplicate_keep": "middle"}))


def test_shipped_profiles_declare_policy_for_their_rules() -> None:
    for name in ("a_stock_daily", "futures"):
        profile = load_profile(name)
        assert profile.policy["duplicate_keep"] in {"first", "last"}
        assert profile.policy["default_row_action"] in {"answer", "skip"}


def test_shipped_profiles_match_current_schema() -> None:
    for name in ("a_stock_daily", "futures"):
        profile = load_profile(name)
        assert profile.schema_version == PROFILE_SCHEMA_VERSION
        assert profile.target_state, name
        assert "日期" in profile.target_state
        assert profile.target("日期").semantic == "date"


def test_builtin_rule_ids_do_not_drift_from_engine_rule_types() -> None:
    """profile.py 的内置清单与引擎实现的规则类型必须一一对应，防止两边各自加规则。

    4b 起检测逻辑在 ``engine.detect``，所以判据从「会话层处理器表」改为 ``engine.RULE_TYPES``。
    """
    assert BUILTIN_RULE_IDS == set(RULE_TYPES)
