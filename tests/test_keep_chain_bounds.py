"""判据 9 的落地证据：``N`` 的数值语义与边界拒绝行为。

阶段 0 只测校验入口（``validate_keep_chain``），不测 CLI 接线——接线在 7a，
且阶段 0 明确「不改 CLI 行为」。
"""

from __future__ import annotations

import pytest

from finpass.passport_compat import (
    KEEP_CHAIN_DEFAULT,
    KEEP_CHAIN_MAX,
    KEEP_CHAIN_MIN,
    validate_keep_chain,
)


def test_default_and_range_are_pinned() -> None:
    # 默认值变更须同提交改测试（spec 判据 9），本断言就是那个测试
    assert (KEEP_CHAIN_DEFAULT, KEEP_CHAIN_MIN, KEEP_CHAIN_MAX) == (5, 2, 100)


@pytest.mark.parametrize("n", [KEEP_CHAIN_MIN, KEEP_CHAIN_DEFAULT, KEEP_CHAIN_MAX])
def test_in_range_values_accepted(n: int) -> None:
    assert validate_keep_chain(n) == n


@pytest.mark.parametrize("n", [1, 0, -1])
def test_below_min_rejected(n: int) -> None:
    with pytest.raises(ValueError, match="至少 2"):
        validate_keep_chain(n)


def test_above_max_rejected() -> None:
    with pytest.raises(ValueError, match="100"):
        validate_keep_chain(KEEP_CHAIN_MAX + 1)


@pytest.mark.parametrize("value", [True, 5.0, "5"])
def test_non_integer_rejected(value: object) -> None:
    # bool 是 int 的子类，必须显式排除，否则 True 会被当成 1 混进去
    with pytest.raises(ValueError, match="必须是整数"):
        validate_keep_chain(value)  # type: ignore[arg-type]
