"""守护测试：engine.py 中不得出现任何金融业务词。

域知识必须下沉到 profiles/*.yaml，引擎只认 rule_id 与结构化字段。
扫描前先用 `_utils.engine_source_without_docstrings` 剔除 docstring ——
模块 docstring 本身以禁用词举例说明约束，不能被判定为违规。
（注意：豁免只覆盖 docstring，**注释仍被扫描**，见 `test_scan_still_catches_code_lines`。）

**禁用词的边界（4b 澄清，写在这里而不只是追认文档）**：禁用词 = **数据里的词**
（具体列名、占位符、行内取值），**不是 rule_type 名**。

- engine 必须认识 `ohlc_contradiction` / `unit_scale` / `date_format` 才能按 type 分派，
  所以 `_check_ohlc` / `_check_unit_scale` / `_check_date_format` 这类函数名是**合法**的；
- engine 不知道的是「ohlc 读哪三列、单位异常按什么系数换算、日期该是什么格式」——那些在 profile；
- 这与「engine 不 import profile」是同一件事的两面：知道「有个 ohlc 检查存在」
  ≠ 知道「ohlc 读哪三列、用什么阈值」。

所以新增检查时：函数名 / 常量名可以用 rule_type 名，代码里不得出现具体列名与域值词。
若某个新检查确实需要一个新的中文列名，那是**词表该扩**，不是这条守护该松——
扩词表必须同提交说明理由，并确认它真的是「数据里的词」而不是规则类型名。
"""

from __future__ import annotations

from pathlib import Path

from _utils import engine_source_without_docstrings

FIN_WORDS = [
    "收盘",
    "开盘",
    "最高价",
    "最低价",
    "涨停",
    "跌停",
    "涨跌停",
    "复权",
    "成交量_股",
    "成交额_元",
    "换手率",
]

ENGINE_PATH = Path(__file__).resolve().parents[1] / "src" / "finpass" / "engine.py"


def scan_finance_words(source: str, words: list[str] | None = None) -> list[str]:
    """返回 source 中命中的金融词；docstring 不参与扫描。"""
    words = FIN_WORDS if words is None else words
    body = engine_source_without_docstrings(source)
    return [word for word in words if word in body]


def test_engine_has_no_finance_words() -> None:
    hits = scan_finance_words(ENGINE_PATH.read_text(encoding="utf-8"))
    assert hits == [], f"engine.py 出现金融词：{hits}，请下沉到 profiles/*.yaml"


def test_scan_ignores_module_docstring() -> None:
    """docstring 里的举例说明不算违规，否则约束无法自述。"""
    source = '"""本模块禁止出现 收盘 / 涨停 这类词。"""\n\nVALUE = 1\n'

    assert scan_finance_words(source) == []


def test_scan_still_catches_code_lines() -> None:
    """剔除 docstring 后，代码行里的金融词仍必须被抓住。"""
    source = '"""说明文字。"""\nCOLUMN = "收盘价"\n'

    assert scan_finance_words(source) == ["收盘"]
