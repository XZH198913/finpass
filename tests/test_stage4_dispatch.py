"""阶段 4c 的中间态判据：``dispatch`` 产出的变更必须与拆分前逐条一致。

4c 把「算目标值」和「写值」拆开，最终产物（``clean.csv``）对错误不敏感——
dispatch 算错了，fix 仍可能写出同样的结果。所以这里单独守中间态，
参考见 ``tests/fixtures/dispatch_reference.json``（由 4c 之前的 ``_fix_*`` 冻结）。

变红先修实现；参考只读（``_dispatch_reference.py`` 没有 ``--write``）。
"""

from __future__ import annotations

import json

import pytest

PROFILES = ["a_stock_daily", "futures"]


@pytest.mark.parametrize("profile", PROFILES)
def test_dispatch_output_matches_frozen_reference(profile: str) -> None:
    from _dispatch_reference import FIXTURE, comparison, measure
    from _fixture import CASES

    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))[profile]
    actual = measure(CASES[profile])

    assert comparison(expected) == comparison(actual), (
        f"{profile}：决策 → 变更的中间态与冻结参考不一致（dispatch 拆分改变了行为）"
    )
