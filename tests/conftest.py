"""pytest 全局配置：把 src/ 与 tests/ 挂上 sys.path。

项目未做 editable 安装，测试直接跑 `pytest` 时也能 import finpass 与 _utils。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
