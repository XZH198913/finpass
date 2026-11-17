"""判据 1 的落地证据：docs/internals.md 的覆盖度表必须能被机械解析，且与 profile 声明一致。

解析器契约（任一违反都抛错，不允许跳过）：

- 锚点固定为 ``## Rule coverage``（0.0.2 起表格随文档重构从 README 迁入 internals）；
- 表头 = ``rule_type | 每个 profile 一列 | Overall``，中间列必须是真实 profile 名
  （**列数随 ``profiles/`` 增减**，夹具按实际数量生成，不写死 3 列）；
- 格值只允许 ``complete`` / ``n/a to profile`` / ``incomplete`` / ``N/A``；
- ``Overall`` = 各 profile 格的短板聚合：任一 ``n/a to profile``（或 ``incomplete``）即
  ``incomplete``，全 ``N/A`` 时取 ``N/A``，其余为 ``complete``；
- 行序不固定，解析后按 ``rule_type`` 聚合，返回 ``dict[str, dict[str, str]]``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finpass.engine import RULE_TYPES
from finpass.profile import PROFILE_DIR, load_profile

ROOT = Path(__file__).resolve().parents[1]
INTERNALS = ROOT / "docs" / "internals.md"
ANCHOR = "## Rule coverage"
TOTAL_COLUMN = "Overall"
STATUSES = ("complete", "n/a to profile", "incomplete", "N/A")


def profile_names() -> list[str]:
    return sorted(path.stem for path in PROFILE_DIR.glob("*.yaml"))


def aggregate(statuses: list[str]) -> str:
    """短板原则：有未声明的 profile 就是 incomplete；全 N/A 仍是 N/A。"""
    applicable = [status for status in statuses if status != "N/A"]
    if not applicable:
        return "N/A"
    if any(status in ("n/a to profile", "incomplete") for status in applicable):
        return "incomplete"
    if all(status == "complete" for status in applicable):
        return "complete"
    raise AssertionError(f"无法聚合的状态集合：{statuses}")  # pragma: no cover


def _section_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if ANCHOR not in lines:
        raise AssertionError(f"缺少锚点 {ANCHOR!r}：解析器不猜标题，直接抛错")
    start = lines.index(ANCHOR)
    for offset, line in enumerate(lines[start + 1 :]):
        if line.startswith("## "):
            return lines[start + 1 : start + 1 + offset]
    return lines[start + 1 :]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(cell and set(cell) <= set("-: ") for cell in cells)


def parse_matrix(text: str) -> dict[str, dict[str, str]]:
    rows: list[list[str]] = []
    for line in _section_lines(text):
        if not line.strip().startswith("|"):
            if rows:
                break
            continue
        cells = _cells(line)
        if _is_separator(cells):
            continue
        rows.append(cells)

    if not rows:
        raise AssertionError("机制覆盖度节里没有表格")

    header, *body = rows
    expected = ["rule_type", *profile_names(), TOTAL_COLUMN]
    if len(header) != len(expected):
        raise AssertionError(f"表头必须恰好 {len(expected)} 列 {expected}，实际 {header}")
    if header[0] != "rule_type" or header[-1] != TOTAL_COLUMN:
        raise AssertionError(f"表头首列必须是 rule_type、末列必须是 {TOTAL_COLUMN}，实际 {header}")
    if sorted(header[1:-1]) != sorted(profile_names()):
        raise AssertionError(f"表头中间三列必须是 profile 名 {profile_names()}，实际 {header[1:-1]}")
    if not body:
        raise AssertionError("机制覆盖度表没有数据行")

    matrix: dict[str, dict[str, str]] = {}
    for cells in body:
        if len(cells) != len(expected):
            raise AssertionError(f"数据行列数不一致：{cells}")
        rule_type = cells[0]
        if not rule_type:
            raise AssertionError(f"rule_type 为空：{cells}")
        statuses = cells[1:-1]
        for status in [*statuses, cells[-1]]:
            if status not in STATUSES:
                raise AssertionError(f"非法格值 {status!r}，只允许 {list(STATUSES)}")
        expected_total = aggregate(statuses)
        if cells[-1] != expected_total:
            raise AssertionError(
                f"{rule_type} 的总状态 {cells[-1]!r} ≠ 短板聚合结果 {expected_total!r}"
            )
        if rule_type in matrix:
            raise AssertionError(f"rule_type 重复：{rule_type}")
        matrix[rule_type] = {**dict(zip(header[1:-1], statuses)), TOTAL_COLUMN: cells[-1]}
    return matrix


def declared_types(name: str) -> set[str] | None:
    """该 profile 声明的 rule_type 集合；profile 不可用（TODO）时返回 None。"""
    try:
        profile = load_profile(name)
    except ValueError:
        return None
    return {rule.type for rule in profile.rules}


def _text(header: list[str], rows: list[list[str]]) -> str:
    lines = [ANCHOR, "", "| " + " | ".join(header) + " |"]
    lines.append("|" + "|".join("---" for _ in header) + "|")
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _real_matrix() -> dict[str, dict[str, str]]:
    return parse_matrix(INTERNALS.read_text(encoding="utf-8"))


def test_matrix_covers_every_implemented_rule_type() -> None:
    """4b 起「实现了哪些 rule_type」由 ``engine.RULE_TYPES`` 回答，不再是会话层的处理器表。"""
    matrix = _real_matrix()
    assert set(matrix) == set(RULE_TYPES)


def test_total_status_is_short_board() -> None:
    # 解析器内已断言一次，这里复算：避免「聚合函数写错，表也跟着错」一起通过
    for rule_type, row in _real_matrix().items():
        statuses = [row[name] for name in profile_names()]
        assert row[TOTAL_COLUMN] == aggregate(statuses), rule_type


def test_documented_status_matches_profile_declarations() -> None:
    for rule_type, row in _real_matrix().items():
        for name in profile_names():
            declared = declared_types(name)
            if declared is None:
                expected = "N/A"
            elif rule_type in declared:
                expected = "complete"
            else:
                expected = "n/a to profile"
            assert row[name] == expected, f"{rule_type} @ {name}"


def test_unavailable_profile_column_is_all_na() -> None:
    """``fund_nav`` 是占位 profile：整列必须是 ``N/A``，且解析器不因它报错。

    口径写死在这里：``N/A`` **只表示「该 profile 不可用」**，不是第四种「实现程度」——
    ``n/a to profile`` 才是「引擎实现了这个 rule_type、但该 profile 没声明」。
    两者混用会让读者分不清「类型没做」和「profile 不能用」。
    """
    matrix = _real_matrix()
    assert "fund_nav" in profile_names()
    assert declared_types("fund_nav") is None, "占位 profile 必须加载失败，否则 N/A 失去依据"

    column = [row["fund_nav"] for row in matrix.values()]
    assert column, "机制覆盖度表没有数据行"
    assert set(column) == {"N/A"}, f"不可用 profile 的每格都应是 N/A，实际 {set(column)}"


def test_missing_anchor_raises() -> None:
    with pytest.raises(AssertionError, match="缺少锚点"):
        parse_matrix("| rule_type | a_stock_daily | futures | fund_nav | Overall |")


def _statuses(*values: str) -> list[str]:
    """按当前 profile 数补齐格值：``N/A`` 补尾，避免夹具写死「3 个 profile」。"""
    missing = len(profile_names()) - len(values)
    assert missing >= 0, "格值比 profile 还多"
    return [*values, *(["N/A"] * missing)]


def test_unknown_status_value_raises() -> None:
    header = ["rule_type", *profile_names(), TOTAL_COLUMN]
    with pytest.raises(AssertionError, match="非法格值"):
        parse_matrix(_text(header, [["missing_token", *_statuses("complete", "complete"), "invalid"]]))


def test_missing_column_raises() -> None:
    header = ["rule_type", profile_names()[0], profile_names()[1], TOTAL_COLUMN]
    with pytest.raises(AssertionError, match="表头必须恰好"):
        parse_matrix(_text(header, [["missing_token", "complete", "complete", "complete"]]))


def test_row_order_is_not_significant() -> None:
    header = ["rule_type", *profile_names(), TOTAL_COLUMN]
    rows = [
        ["rule_type_b", *_statuses("complete", "n/a to profile"), "incomplete"],
        ["rule_type_a", *_statuses(), "N/A"],
    ]
    assert parse_matrix(_text(header, rows)) == parse_matrix(_text(header, list(reversed(rows))))
