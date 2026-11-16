"""测试共用工具：CSV 往返归一化、engine 源码去 docstring。

两处逻辑只在这里实现一次：replay 一致性测试与 engine 守护测试共用，
避免两边判据漂移。
"""

from __future__ import annotations

import ast
from typing import Any

import pandas as pd


def normalize_frame(frame: Any) -> pd.DataFrame:
    """把 frame 归一到「CSV 读回后」的世界，供跨 dtype 比较。

    CSV 不保留 dtype：内存里 ``int64 1200`` 与读回后的 ``float64 1200.0``
    是同一数值，但 ``astype(str)`` 会给出 ``"1200"`` / ``"1200.0"``，
    所以必须先按列试数值化，而不是无脑字符串化。

    守卫：仅当「非空单元格数 == 可数值化单元格数」才转 float。
    真实文本列（如被改坏的旧值）不会被静默吞成 NaN，差异仍会触发断言。
    """
    out = frame.copy()
    for col in out.columns:
        numeric = pd.to_numeric(out[col], errors="coerce")
        if numeric.notna().sum() == out[col].notna().sum():
            out[col] = numeric.astype(float)
        else:
            out[col] = out[col].astype(str).replace({"nan": "<NA>", "None": "<NA>", "": "<NA>"})
    return out


def engine_source_without_docstrings(source: str) -> str:
    """剔除模块 / 类 / 函数 docstring 后的源码文本。

    engine.py 的模块 docstring 本身以禁用词举例说明约束，
    若不去掉会把「说明」误判成「违规」。注释保留：
    注释不是域知识下沉的载体，但仍会在扫描里暴露金融词。
    """
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        start = body[0].lineno
        end = getattr(body[0], "end_lineno", start) or start
        for lineno in range(start, end + 1):
            lines[lineno - 1] = ""
    return "\n".join(lines)
