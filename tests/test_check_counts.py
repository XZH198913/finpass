"""判据 5 的落地证据：三计数口径与分母演进规则。

**本文件只做逻辑验证**：等式的结构、分母定义的唯一来源、未答来源封闭、演进只增不减。
全部使用 stub 数值，不读任何产物。真实计数（`改动 + flag + 未答 == 总数`）属于**集成验证**，
在阶段 1 验收里做——阶段 1 的规格必须显式登记这条，不能因为这里测过就跳过。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from finpass import check_counts
from finpass.check_counts import (
    CHECK_COUNT_DEFINITION,
    UNANSWERED_SOURCES,
    CheckEntry,
    Counts,
    DenominatorLedger,
    validate_unanswered_source,
)

SRC = Path(check_counts.__file__).resolve().parent


def test_equation_is_structurally_valid() -> None:
    """逻辑验证：给定 N/M/K，等式函数是否成立。真实计数来自阶段 1 的产物。"""
    counts = Counts(fix=3, flag=2, unanswered=1, checks=6)
    assert counts.balanced
    assert counts.validate() is counts

    with pytest.raises(ValueError, match="三计数不闭合"):
        Counts(fix=3, flag=2, unanswered=1, checks=7).validate()


def test_denominator_definition_is_single_source() -> None:
    """分母的机械定义只允许有一处赋值；别处引用常量，不复制文字。"""
    assert CHECK_COUNT_DEFINITION.strip()
    assert len(_definition_holders()) == 1, f"分母定义出现多处：{_definition_holders()}"


def test_unanswered_sources_enum_is_closed() -> None:
    """未答 K 的来源是封闭三值，出现第四种即口径不一致。"""
    assert UNANSWERED_SOURCES == ("max_questions", "profile_uncovered_column", "policy_undeclared")
    for source in UNANSWERED_SOURCES:
        assert validate_unanswered_source(source) == source
    with pytest.raises(ValueError, match="不在封闭集合"):
        validate_unanswered_source("policy_undeclared_other")


def test_denominator_never_decreases() -> None:
    """D1–D5 逐条验证：只登记增长、改文本不算新增、retired 仍计入、报告带来源。"""
    ledger = DenominatorLedger(40, source="阶段 0 基线")
    seen = [ledger.denominator]

    ledger.register(CheckEntry("stage1.clean_csv", "阶段 1 判据 1", "clean.csv 逐字节可复现"))
    seen.append(ledger.denominator)
    ledger.register(CheckEntry("stage3.target_state", "阶段 3 判据 2", "target_state 枚举封闭"))
    seen.append(ledger.denominator)

    before = ledger.denominator
    # D2：同一个 id 换措辞再登记，不算新增，且必须报错而不是静默覆盖
    with pytest.raises(ValueError, match="重复登记"):
        ledger.register(CheckEntry("stage3.target_state", "阶段 3 判据 2", "换了措辞"))
    # D1/D2：空断言 = 把新检查项合并进旧条目，必须报错
    with pytest.raises(ValueError, match="所属判据与断言文本"):
        ledger.register(CheckEntry("stage5.phase_group", "阶段 5 判据 4", "   "))
    assert ledger.denominator == before

    ledger.register(CheckEntry("stage5.phase_group", "阶段 5 判据 4", "按轮分组校验事件闭合"))
    ledger.retire("stage1.clean_csv")
    seen.append(ledger.denominator)

    # D3：分母全程只增不减，retired 条目仍计入
    assert seen == sorted(seen), f"分母出现回退：{seen}"
    assert ledger.denominator == 43

    # D4：报告必须写出来源，不允许只给数值
    described = ledger.describe()
    assert "来源" in described and "阶段 0 基线" in described and "43" in described


def _definition_holders() -> list[str]:
    holders: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            if "CHECK_COUNT_DEFINITION" in names:
                holders.append(path.name)
    return holders
