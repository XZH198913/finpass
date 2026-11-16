"""判据 5 的落地：三计数口径与分母演进（纯逻辑，不读产物、不接 CLI）。

分工写死，避免半年后误以为阶段 0 已验过真实计数：

- **本模块 + ``tests/test_check_counts.py``**：结构验证——等式成立、分母定义唯一、
  未答来源封闭、演进只增不减。全部用 stub 数值，不碰产物。
- **阶段 1 验收**：集成验证——用真实产物核对 ``改动 + flag + 未答 == 总数``。
  阶段 1 的规格必须显式登记这条，不能因为「阶段 0 测过等式」就跳过。

对应 spec 判据 5 的 D1–D5：D1 只登记增长、D2 不许靠改定义吸收、D3 只增不减、
D4 报告必须写来源、D5 未答来源与分母同源。
"""

from __future__ import annotations

import dataclasses

#: 「检查项总数」的机械定义。**唯一来源**：其他位置引用本常量，不复制这段文字。
#: 一条判据含 k 个独立断言则计 k；由测试解析阶段规格得出，不由人手工填写。
CHECK_COUNT_DEFINITION = "各阶段出口判据中可执行断言的条数之和（一条判据含 k 个断言则计 k），由测试解析规格得出"

#: 未答 K 的来源枚举（封闭集合）。D5：与分母来自同一次解析结果。
UNANSWERED_SOURCES = (
    "max_questions",
    "profile_uncovered_column",
    "policy_undeclared",
)


@dataclasses.dataclass(frozen=True)
class Counts:
    """三计数口径：``fix + flag + unanswered == checks``。"""

    fix: int
    flag: int
    unanswered: int
    checks: int

    @property
    def balanced(self) -> bool:
        return self.fix + self.flag + self.unanswered == self.checks

    def validate(self) -> "Counts":
        if not self.balanced:
            total = self.fix + self.flag + self.unanswered
            raise ValueError(
                f"三计数不闭合：改动 {self.fix} + flag {self.flag} + 未答 {self.unanswered} "
                f"= {total}，检查项总数 {self.checks}"
            )
        return self


def validate_unanswered_source(source: str) -> str:
    """D5：未答必须能归到三个来源之一，出现第四种就是解析口径不一致。"""
    if source not in UNANSWERED_SOURCES:
        raise ValueError(f"未答来源 {source!r} 不在封闭集合 {list(UNANSWERED_SOURCES)}")
    return source


@dataclasses.dataclass(frozen=True)
class CheckEntry:
    """一条追加登记的检查项：id + 所属判据 + 断言文本，三样缺一不可（D1）。"""

    check_id: str
    criterion: str
    assertion: str


class DenominatorLedger:
    """分母账本。

    - D1 只登记增长：每次增长必须同时给出判据与断言文本；
    - D2 不许靠改定义吸收：id 重复登记直接报错，合并断言（空文本）也报错；
    - D3 只增不减：``retire`` 只做标记，分母不变；
    - D4 报告必须写来源：``describe`` 输出含基线来源与追加条数。

    纯对象、无 IO；阶段 0 不接线到任何报告或 CLI。
    """

    def __init__(self, baseline: int, source: str = "阶段 0 基线") -> None:
        if baseline < 0:
            raise ValueError(f"分母不能为负：{baseline}")
        self._baseline = baseline
        self._source = source
        self._entries: dict[str, CheckEntry] = {}
        self._retired: set[str] = set()

    @property
    def denominator(self) -> int:
        # retired 仍计入（D3），故分母只看 entries 的条数
        return self._baseline + len(self._entries)

    def register(self, entry: CheckEntry) -> None:
        if not entry.criterion.strip() or not entry.assertion.strip():
            raise ValueError(
                f"登记 {entry.check_id!r} 必须同时给出所属判据与断言文本"
                "（D1/D2：不许把多条断言合并成一条来容纳新检查项）"
            )
        if entry.check_id in self._entries:
            raise ValueError(f"检查项 id 重复登记：{entry.check_id!r}（D2：改文本不算新增）")
        self._entries[entry.check_id] = entry

    def retire(self, check_id: str) -> None:
        if check_id not in self._entries:
            raise KeyError(check_id)
        self._retired.add(check_id)

    def describe(self) -> str:
        """D4：分母必须带来源，只给数值或百分比视为无效输出。"""
        return (
            f"分母 = {self.denominator}（来源：{self._source}，追加登记 {len(self._entries)} 条，"
            f"其中 retired {len(self._retired)} 条仍计入）"
        )
