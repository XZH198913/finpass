"""pytest 全局配置：把 src/ 与 tests/ 挂上 sys.path。

同时把测试语言钉在 ``zh``：产物基线与日志断言（``fixtures/queue_reference.json``、
``test_narrate`` / ``test_stage2_event_log`` / ``test_cli`` 的文案）冻结的是中文文案。
产品默认语言是 ``en``（``messages.DEFAULT_LANGUAGE``），这里用 ``setdefault`` 只影响测试，
子进程（CLI 测试）也会继承同一个变量。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("FINPASS_LANG", "zh")


@pytest.fixture
def set_lang():
    """临时切换语言；用例结束后复位，避免显式设置泄漏到后续用例。

    ``None`` 表示恢复跟随 ``FINPASS_LANG``（本测试套件里是 ``zh``）。
    """
    from finpass import messages

    def _set(language: str | None) -> None:
        messages.set_language(language)

    yield _set
    messages.set_language(None)
