"""阶段 4b：检测搬家（``session._detect`` → ``engine.detect``）的两阶段等价判据。

两阶段各自会漏掉一类变化，所以两个都要跑（判据见 ``docs/spec-stage4-追认.md`` 第八节）：

- **阶段 A**（``detect_reference.json``）：``engine.detect`` 的原始 ``Gap`` vs 搬家前
  ``_detect`` 的原始 ``Question``。抓「检测逻辑变了」，但抓不到聚合变化；
- **阶段 B**（``queue_reference.json``）：``start()`` 聚合后的队列 vs 搬家前的队列，
  抓「包装/聚合变了」，也守住 ``affected_row_keys`` 的顺序；但 Gap 顺序变了而聚合后
  队列相同时它看不出来——那是 A 的活。

两份参考都由**搬家前**的 ``session._detect`` 生成，JSON 一旦冻结就不再重生：
本测试红了先修实现（与 ``_fixture.py`` 同一守则）。
"""

from __future__ import annotations

import json

import pytest

PROFILES = ["a_stock_daily", "futures"]


@pytest.mark.parametrize("profile", PROFILES)
def test_detect_matches_frozen_reference(profile: str) -> None:
    from _detect_reference import FIXTURE, comparable, measure
    from _fixture import CASES

    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))[profile]
    actual = measure(CASES[profile])

    assert comparable(expected) == comparable(actual), (
        f"{profile}：engine.detect 与冻结的检测参考不一致（检测搬家改变了行为）"
    )


def test_reserved_key_is_not_treated_as_column() -> None:
    """``target_state`` 的元信息键不是列：``Gap.targets`` 里不能出现 ``__`` 开头的键。

    这是「保留键约定」的机械证据——约定写在 ``engine`` 模块 docstring，加载期校验挡住用户列，
    本用例证明 engine 自己确实跳过了它（把 ``__rules__`` 当主键列会让 ``_rows`` 直接 KeyError）。
    """
    import pandas as pd

    from finpass.engine import RULES_KEY, detect

    frame = pd.DataFrame({"日期": ["2024-03-12", "2024-03-12"], "数值": ["--", 1.0]})
    target_state = {
        "日期": {"semantic": "date", "role": "key"},
        "数值": {"semantic": "amount"},
        RULES_KEY: {
            "missing_tokens": ["--"],
            "checks": [
                {"id": "duplicate_key", "type": "duplicate_key", "columns": []},
                {"id": "missing_token", "type": "missing_token", "columns": []},
            ],
        },
    }

    gaps = detect(frame, target_state, {})

    assert {gap.rule_id for gap in gaps} == {"duplicate_key", "missing_token"}
    targets = {column for gap in gaps for column in gap.targets}
    assert targets == {"日期", "数值"}
    assert not [column for column in targets if column.startswith("__")]


@pytest.mark.parametrize("profile", PROFILES)
def test_queue_matches_frozen_reference(profile: str) -> None:
    from _fixture import CASES
    from _queue_reference import FIXTURE, measure

    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))[profile]
    actual = measure(CASES[profile])

    assert actual == expected, f"{profile}：聚合后的队列与冻结参考不一致（包装/聚合变了）"
