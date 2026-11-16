"""公共数据结构：Question / Decision / CellChange / Passport / Gap / DispatchResult。

除了 dataclass，这里还承载两件**跨层契约**（4c 起）——放这里是因为引擎、会话、护照、
diff 四处都要用同一份，各写一份就是两份事实源：

- ``key_columns`` / ``row_keys``：行身份（复合主键 ``a|b`` 拼接）的唯一实现；
- ``cell_text`` / ``make_change``：变更记录落盘形态（字符串或 ``None``）的唯一实现，
  由 ``engine.dispatch`` 产出、``session`` 落盘，两边不能各有一套格式化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Question:
    """一条待人工确认的问题（由 detect 产生）。"""

    question_id: str
    rule_id: str
    aggregation_level: str  # strategy | column | point
    affected_row_keys: list[Any] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    default: str = ""
    context_samples: list[dict[str, Any]] = field(default_factory=list)
    estimated_changes: int = 0

    def __post_init__(self) -> None:
        # 校验占位：待 02_interaction_queue 填充
        pass

    def render(self) -> str:
        """纯文本问题卡片：不引 ipywidgets、不阻塞、无副作用。"""
        lines = [
            f"┌─ [{self.aggregation_level}] {self.rule_id}  ({self.question_id})",
            f"│ 影响行数：{len(self.affected_row_keys)}    预估变更：{self.estimated_changes}",
        ]
        for sample in self.samples[:5]:
            lines.append("│ 样本：" + "  ".join(f"{k}={v}" for k, v in sample.items()))
        lines.append("│ 可选答案：" + " / ".join(self.options))
        lines.append(f"│ 默认建议：{self.default}")
        lines.append("└" + "─" * 58)
        return "\n".join(lines)


@dataclass
class Decision:
    """一条人工决策事件（事件流的一行）。"""

    decision_id: str
    question_id: str
    action: str  # answer | skip | batch_apply | undo
    params: dict[str, Any] = field(default_factory=dict)
    ts: str = ""

    def __post_init__(self) -> None:
        # 校验占位：待 02_interaction_queue 填充
        pass


@dataclass
class CellChange:
    """一条单元格级变更，通过 decision_id 关联到 Decision。"""

    row_key: Any
    col: str
    before: Any
    after: Any
    change_type: str  # filled_nan | changed_value | normalized | dropped_row | flagged_kept
    rule_id: str
    decision_id: str

    def __post_init__(self) -> None:
        # 校验占位：待 03_diff_and_passport 填充
        pass


@dataclass
class Passport:
    """质量护照：一次会话的可复现摘要。"""

    input_hash: str
    output_file_sha256: str
    profile_name: str
    profile_version: str
    decisions_count: int = 0
    auto_only: bool = False
    unresolved_issues_count: int = 0

    def __post_init__(self) -> None:
        # 校验占位：待 03_diff_and_passport 填充
        pass


@dataclass
class UnansweredQuestion:
    """因会话问题上限而未作答的问题（挂在 session.unanswered 上）。"""

    question_id: str
    rule_id: str
    aggregation_level: str
    affected_row_keys: list[Any] = field(default_factory=list)
    reason: str = "max_questions_exceeded"

    def __post_init__(self) -> None:
        pass


@dataclass
class Gap:
    """引擎层的公共语言：**实际数据与目标态之间的差距**。

    刻意不是 ``Question``：``options`` / ``default`` / ``aggregation_level`` 是「怎么问人」，
    属于展示层；engine 只描述「哪里不达标、证据是什么」。
    ``session`` 负责把 ``Gap`` 包装成 ``Question``（阶段 4b 起），因此 engine 不产出问题。
    """

    rule_id: str
    targets: list[str] = field(default_factory=list)  # 涉及的列（target_state 的 key）
    rows: list[dict[str, Any]] = field(default_factory=list)  # 命中的行（主键 + 关注列）
    evidence: dict[str, Any] = field(default_factory=dict)  # 判据痕迹：阈值、命中数等


@dataclass
class DispatchResult:
    """决策 → 待执行动作的分派结果：``changes`` 落副作用，``flags`` 只打标保留。"""

    changes: list[CellChange] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)


def key_columns(key: str | list[str]) -> list[str]:
    """主键统一成列名列表（单列 / 复合都认），顺序即 ``row_key`` 的拼接顺序。"""
    return [key] if isinstance(key, str) else [str(column) for column in key]


def row_keys(frame: Any, key: str | list[str]) -> Any:
    """主键取值序列：单主键取该列，复合主键按 ``|`` 连接（全仓同一形态）。"""
    columns = key_columns(key)
    if len(columns) == 1:
        return frame[columns[0]].astype(str)
    return frame[columns].astype(str).agg("|".join, axis=1)


def cell_text(value: Any) -> Any:
    """变更记录的取值形态：``None`` / ``NaN`` → ``None``，其余转字符串。

    统一在这里做，是因为 ``changes.parquet`` 的 ``before`` / ``after`` 只有这一种形态；
    engine 产出与 session 落盘若各转一次，浮点表示（``1200.0``）与 ``NaN`` 处理就会漂移。
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass  # 列表 / 字典等不可判空的值，按 str 走
    return str(value)


def make_change(
    row_key: Any,
    col: str,
    before: Any,
    after: Any,
    change_type: str,
    rule_id: str,
    decision_id: str,
) -> dict[str, Any]:
    """构造一条单元格变更记录（落盘形态固定为字符串或 ``None``）。"""
    return {
        "row_key": cell_text(row_key),
        "col": col,
        "before": cell_text(before),
        "after": cell_text(after),
        "change_type": change_type,
        "rule_id": rule_id,
        "decision_id": decision_id,
    }
