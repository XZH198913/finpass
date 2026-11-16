"""判据 2 的 phase 单一来源：字面量只准存在一处。

阶段 5 判据 4 一旦复制 ``initial`` 字面量，就会出现「改一处漏一处」的漂移；
本文件用「字面量出现次数」把它钉死。

4b 的位置变化：阶段名的字面量从 ``passport_compat`` 移到了 ``engine.PHASE_INITIAL``
（``detect`` 的阶段闸门必须有这个名字，而 engine 不能 import 上层的 ``passport_compat``）。
``passport_compat.PHASE_DEFAULT`` 改成别名，于是全包仍然只有一处字面量——判据不变，位置变了。
"""

from __future__ import annotations

from pathlib import Path

from finpass import passport_compat
from finpass.passport_compat import FIELD_COMPAT, PHASE_DEFAULT

SRC = Path(passport_compat.__file__).resolve().parent


def test_phase_default_value() -> None:
    assert PHASE_DEFAULT == "initial"


def test_events_phase_value_is_skip() -> None:
    # skip = 写入端不改旧产物、读端按 PHASE_DEFAULT 处理缺失。
    # 值必须是三值枚举里的 skip，不能把 initial 编进值里（skip_initial 已废弃）。
    assert FIELD_COMPAT["events.phase"] == "skip"


def test_no_second_literal_in_package() -> None:
    hits = sorted(
        path.name for path in SRC.glob("*.py") if f'"{PHASE_DEFAULT}"' in path.read_text(encoding="utf-8")
    )
    assert hits == ["engine.py"], f"phase 默认值的字面量只允许出现在 engine.py（PHASE_INITIAL）：{hits}"
