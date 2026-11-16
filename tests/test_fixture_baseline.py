"""重构判据：两个档的会话产物都必须与各自基线逐字节一致。

这条测试守的是「重构不改行为」，不是「数据算得对」：

- 判据 = 文件字节的 sha256（随机字段先规范化），与 ``verify`` 同一套算法；
- 不重算内存哈希，不做数值容差比较，避免两套判据同时跑；
- 基线由 ``python tests/_fixture.py --write`` 两档一起生成，重构前后必须不变。

两个档的分工：``a_stock_daily`` 是单列主键 + 单位换算，``futures`` 是复合主键 + 区间规则 +
OHLC 关系。只守股票档，等于每个阶段只有一半证据——「股票档没暴露、期货档暴露」是最常见的
漏网形态。

变红时先修实现，不重生基线（守则写在 ``tests/_fixture.py`` 模块 docstring）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PROFILES = ["a_stock_daily", "futures"]


def _case(profile: str):
    from _fixture import CASES

    return CASES[profile]


@pytest.mark.parametrize("profile", PROFILES)
def test_artifacts_match_baseline(tmp_path: Path, profile: str) -> None:
    pytest.importorskip("pyarrow", reason="changes.parquet 需要 pyarrow")
    from _fixture import fingerprint, run_demo

    case = _case(profile)
    assert case.fixture.is_file(), f"缺少基线：python tests/_fixture.py --write --profile {profile}"
    baseline = json.loads(case.fixture.read_text(encoding="utf-8"))

    run_demo(case, tmp_path, baseline["decisions"])
    actual = fingerprint(case, tmp_path)

    mismatched = [
        f"{name}: 基线 {baseline['artifacts'].get(name)} != 实跑 {digest}"
        for name, digest in actual.items()
        if baseline["artifacts"].get(name) != digest
    ]
    assert not mismatched, f"{profile} 产物与基线不一致（重构改变了行为）：\n" + "\n".join(mismatched)


@pytest.mark.parametrize("profile", PROFILES)
def test_fixture_decisions_cover_queue(profile: str) -> None:
    """决策集必须覆盖该档队列里的全部规则，否则「跑完一遍」是假的。"""
    from _fixture import CASES

    import finpass as fp

    case = CASES[profile]
    session = fp.open(case.csv, profile=case.profile)
    session.start()

    assert {question.rule_id for question in session.queue} == set(case.decisions)


@pytest.mark.parametrize("profile", PROFILES)
def test_baseline_records_its_decision_set(profile: str) -> None:
    """基线自带决策集：只固化产物不固化决策，等于「能读不能重生」。"""
    from _fixture import CASES

    case = CASES[profile]
    baseline = json.loads(case.fixture.read_text(encoding="utf-8"))

    assert baseline["profile"] == case.profile
    assert baseline["input"] == case.csv.relative_to(REPO).as_posix()
    assert {rule: list(value) for rule, value in case.decisions.items()} == baseline["decisions"]
