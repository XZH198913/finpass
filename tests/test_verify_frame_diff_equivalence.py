"""``_frame_diff`` 的向量化实现必须与「逐格调 ``_same``」逐字等价。

背景：旧实现 ``rebuilt.iloc[position][col]`` 在内层循环里每取一列就重建整行 Series，
是 O(行×列²) 次取值——5580×76 的产物上单次 verify 要 8 分钟以上。改成逐列向量化后，
**判等规则一个字都不能变**，否则篡改检测的力度会悄悄变化（判松 = 漏报，判紧 = 假报警）。

这里守着两件事：

1. ``_frame_diff`` 与「逐格参照实现」输出逐字相同；
2. ``_column_equal`` 与 ``_same`` 逐格结论相同——向量化之后「同一语义」有了两份实现
   （``_same`` 给 ``replay_changes`` 校验 before，``_column_equal`` 给 ``_frame_diff`` 比帧），
   改任一份不检查另一份就会漂移，后果是同一份数据在两条判据上给出相反结论。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from finpass import passport
from finpass.models import row_keys


def _reference_frame_diff(rebuilt: pd.DataFrame, clean: pd.DataFrame, keys: list[str]) -> list[str]:
    """逐格参照实现：与向量化版改之前的生产代码逐字相同。"""
    problems: list[str] = []
    if list(rebuilt.columns) != list(clean.columns):
        return [f"列集合不一致：replay {list(rebuilt.columns)} != clean {list(clean.columns)}"]
    if len(rebuilt) != len(clean):
        return [f"行数不一致：replay {len(rebuilt)} != clean {len(clean)}"]
    rebuilt_keys = row_keys(rebuilt, keys)
    for position in range(len(rebuilt)):
        for col in rebuilt.columns:
            left = rebuilt.iloc[position][col]
            right = clean.iloc[position][col]
            if not passport._same(left, right):
                problems.append(f"{rebuilt_keys.iloc[position]}.{col}（replay {left!r} != clean {right!r}）")
                if len(problems) >= 5:
                    return problems
    return problems


def _pair(left: list, right: list, columns: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    columns = columns or ["k", "v"]
    return (
        pd.DataFrame({columns[0]: ["a", "b", "c", "d", "e", "f", "g", "h"][: len(left)], columns[1]: left}),
        pd.DataFrame({columns[0]: ["a", "b", "c", "d", "e", "f", "g", "h"][: len(right)], columns[1]: right}),
    )


def test_nan_and_none_are_equal_to_each_other() -> None:
    rebuilt, clean = _pair([np.nan, None, 1.0], [np.nan, np.nan, 1])
    rebuilt["v"] = rebuilt["v"].astype(object)
    clean["v"] = clean["v"].astype(object)

    assert passport._frame_diff(rebuilt, clean, ["k"]) == []


def test_value_vs_missing_is_a_mismatch() -> None:
    rebuilt, clean = _pair(["x", 1.0], [np.nan, 1.0])

    problems = passport._frame_diff(rebuilt, clean, ["k"])

    assert len(problems) == 1
    assert problems[0].startswith("a.v（replay 'x' != clean ")
    assert problems == _reference_frame_diff(rebuilt, clean, ["k"])


def test_numeric_strings_still_compare_numerically() -> None:
    """CSV 往返：replay 侧 float64、clean 侧读回 int64，str 不等但数值相同。"""
    rebuilt, clean = _pair([12000000.0, 0.4711], [12000000, 0.4711])

    assert passport._frame_diff(rebuilt, clean, ["k"]) == []


def test_relative_tolerance_matches_same() -> None:
    """``_same`` 用 rel_tol=1e-9：相对误差在这个范围内必须放行，超出必须报。"""
    inside, clean_inside = _pair([1.0 + 1e-12], [1.0])
    outside, clean_outside = _pair([1.0 + 1e-6], [1.0])

    assert passport._frame_diff(inside, clean_inside, ["k"]) == []
    assert len(passport._frame_diff(outside, clean_outside, ["k"])) == 1


def test_different_junk_strings_are_not_equal() -> None:
    """两侧都解析不成数字时，不能因为「都变 NaN」而判等。"""
    rebuilt, clean = _pair(["--", "N/A"], ["--", "暂无"])

    assert passport._frame_diff(rebuilt, clean, ["k"]) == ["b.v（replay 'N/A' != clean '暂无'）"]


def test_nan_literal_strings_are_not_equal() -> None:
    """字面量 ``'nan'`` 两侧都能 float 成 NaN，但 ``isclose`` 恒 False——与 ``_same`` 一致。"""
    rebuilt, clean = _pair(["nan"], ["NaN"])

    assert len(passport._frame_diff(rebuilt, clean, ["k"])) == 1


def test_datetime_is_compared_by_str_not_by_astype() -> None:
    """``astype(str)`` 会省掉 datetime 的时间部分，必须按 ``str()`` 比：这是真差异。"""
    rebuilt, clean = _pair(
        [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-02 00:00:01")],
        ["2024-01-02", "2024-01-02 00:00:01"],
    )

    problems = passport._frame_diff(rebuilt, clean, ["k"])

    assert len(problems) == 1
    assert problems[0].startswith("a.v（replay Timestamp('2024-01-02 00:00:00')")


def test_reports_at_most_five_in_row_major_order() -> None:
    columns = ["k", "c1", "c2", "c3"]
    rebuilt = pd.DataFrame({"k": ["a", "b"], "c1": [1, 1], "c2": [1, 1], "c3": [1, 1]})
    clean = pd.DataFrame({"k": ["a", "b"], "c1": [2, 2], "c2": [2, 2], "c3": [2, 2]})

    problems = passport._frame_diff(rebuilt, clean, ["k"])

    assert len(problems) == 5
    # 行优先、列序：a.c1 a.c2 a.c3 b.c1 b.c2
    assert [problem.split("（")[0] for problem in problems] == ["a.c1", "a.c2", "a.c3", "b.c1", "b.c2"]
    assert columns  # 保持列名可读性，避免误删


def test_shape_mismatch_short_circuits() -> None:
    rebuilt, clean = _pair([1.0], [1.0, 2.0])

    assert passport._frame_diff(rebuilt, clean, ["k"]) == ["行数不一致：replay 1 != clean 2"]

    rebuilt2 = pd.DataFrame({"k": ["a"], "v": [1.0]})
    clean2 = pd.DataFrame({"k": ["a"], "w": [1.0]})
    assert passport._frame_diff(rebuilt2, clean2, ["k"])[0].startswith("列集合不一致")


def test_empty_frame() -> None:
    rebuilt, clean = _pair([], [])

    assert passport._frame_diff(rebuilt, clean, ["k"]) == []


def test_equivalence_on_mixed_frame() -> None:
    """混合类型的一整表：新旧实现必须给出完全相同的差异列表。"""
    rebuilt = pd.DataFrame(
        {
            "k": ["a", "b", "c", "d"],
            "s": ["中文", "--", np.nan, "x"],
            "n": [1.0, np.nan, 2.5, 3.0],
            "b": [True, False, True, False],
        }
    )
    clean = pd.DataFrame(
        {
            "k": ["a", "b", "c", "d"],
            "s": ["中文", np.nan, np.nan, "x "],
            "n": [1, np.nan, 2.5000000001, 3.0],
            "b": [True, True, True, False],
        }
    )

    assert passport._frame_diff(rebuilt, clean, ["k"]) == _reference_frame_diff(rebuilt, clean, ["k"])


def test_equivalence_on_shuffled_cells() -> None:
    """大量随机篡改：逐格参照与向量化必须逐条一致（含 5 条上限的截断位置）。"""
    rng = np.random.default_rng(20261111)
    n_rows, n_cols = 30, 6
    rebuilt = pd.DataFrame(rng.integers(0, 20, size=(n_rows, n_cols)), columns=[f"c{i}" for i in range(n_cols)])
    rebuilt.insert(0, "k", [f"r{i}" for i in range(n_rows)])
    clean = rebuilt.copy().astype(object)
    for _ in range(40):
        clean.iloc[int(rng.integers(0, n_rows)), int(rng.integers(1, n_cols))] = rng.choice(
            ["改过", np.nan, "3", 3.0, "--"]
        )

    assert passport._frame_diff(rebuilt, clean, ["k"]) == _reference_frame_diff(rebuilt, clean, ["k"])


# 覆盖 ``_same`` 全部分支的取值池：缺失（None 与 NaN 两种）、字面量 "nan"、空串、
# 千分位、单位后缀式不可解析、下划线数字、带空格数字、datetime、bool、相对容差边界。
VALUE_POOL = [
    None,
    np.nan,
    "nan",
    "NaN",
    "None",
    "",
    "  ",
    "--",
    "-",
    "N/A",
    "暂无",
    "x",
    "中文",
    "中文 ",
    "0",
    "0.0",
    0,
    0.0,
    1,
    1.0,
    "1",
    "1.0",
    "+1",
    ".5",
    "0.5",
    "1e3",
    "1000",
    "12000000",
    12000000,
    12000000.0,
    "12000000.0",
    "  7  ",
    7,
    "7 ",
    "1_000",
    1000,
    "1,234",
    1234,
    "inf",
    np.inf,
    "-Infinity",
    True,
    False,
    "True",
    "1.0000000001",
    1.0 + 1e-12,
    pd.Timestamp("2024-01-02"),
    "2024-01-02",
    "2024-01-02 00:00:00",
    "0x10",
    "1e",
    "1.2.3",
    "-0",
]


def test_column_equal_agrees_with_same_on_value_pool() -> None:
    """``_column_equal`` 与 ``_same`` 在全部取值组合上必须给出相同结论。

    这是「同一语义两份实现」的守护：``_same`` 是参照实现，``_column_equal`` 是向量化副本。
    已经真实踩到过的两处漂移，都在这里各有反例用例：

    - ``str(np.nan) == "nan"`` → ``_same(nan, "nan")`` 为真，只按「一侧缺失即不等」判会漏；
    - ``float("1_000")`` 能过、``pd.to_numeric`` 不认 → 只靠批量解析会比 ``_same`` 判得更紧。
    """
    pairs = [(left, right) for left in VALUE_POOL for right in VALUE_POOL]
    vectorised = passport._column_equal(
        pd.Series([pair[0] for pair in pairs], dtype=object),
        pd.Series([pair[1] for pair in pairs], dtype=object),
    )

    disagreed = [
        (left, right, passport._same(left, right), bool(vectorised[position]))
        for position, (left, right) in enumerate(pairs)
        if bool(vectorised[position]) != bool(passport._same(left, right))
    ]

    assert disagreed == []
    assert len(pairs) > 2000


def test_column_equal_agrees_with_same_on_mixed_columns() -> None:
    """按列比较（真实调用形态）与逐格标量比较结论一致，覆盖 object / float / datetime / string dtype。"""
    rng = np.random.default_rng(7)
    frame = pd.DataFrame(
        {
            "k": [f"r{i}" for i in range(20)],
            "obj": rng.choice(np.array(["中文", "--", "nan", "1e3", "1_000", np.nan], dtype=object), size=20),
            "num": rng.normal(size=20),
            "dt": pd.to_datetime(["2024-01-02"] * 20),
            "txt": pd.Series(["x", "y"] * 10, dtype="string"),
        }
    )
    other = frame.copy()
    other["obj"] = other["obj"].astype(object)  # 允许写入非字符串，模拟 replay 侧类型不一致
    other.loc[3, "obj"] = 12000000
    other.loc[5, "num"] = np.nan
    other.loc[7, "dt"] = pd.Timestamp("2024-01-03")
    other.loc[9, "txt"] = pd.NA
    other.loc[11, "obj"] = "1_000"

    for column in frame.columns:
        if column == "k":
            continue
        vectorised = passport._column_equal(frame[column], other[column])
        scalar = [passport._same(frame.at[i, column], other.at[i, column]) for i in frame.index]
        assert list(vectorised) == scalar, column
