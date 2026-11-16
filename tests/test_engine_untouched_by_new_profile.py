"""守护测试：新增 profile 不得改动 engine.py。

判据是**基线 hash**而不是 git diff：`tests/baselines/engine.sha256` 是对 engine.py 取的
sha256，任何改动（哪怕只改一个注释）都会让测试失败。

基线重生记录（每次重生必须留一行理由）：

- 阶段 4a：五个步骤落签名（`a3ebd3…` → `e9afa1…`）；
- 阶段 4b：`detect` 从 ``session._detect`` 搬入，随后补「保留键约定」（`_is_meta_key`）
  （`e9afa1…` → `dc461b…`）。
  这是**有意的引擎改动**，不是「新增 profile 顺手改了引擎」——判据本身没有放松：
  重生之后，新增域仍然只能写 ``profiles/*.yaml``。
- 阶段 4d：``missing_token`` 的扫描范围改由 ``checks[*].columns`` 声明（`dc461b…` → `92ecc6…`）。
  理由是一次真实数据暴露：估值宽表 74 个数值列 → 71 道同质题，题量随列数线性膨胀，
  用户无法逐题决策。**加 profile 本身没有改引擎**（`a_stock_valuation.yaml` 之前一版
  加入后基线不变）；改引擎是因为缺一个「扫描范围可声明」的形态原语，登记见
  `docs/spec-stage4-追认.md` §十三。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "src" / "finpass" / "engine.py"
BASELINE = Path(__file__).resolve().parent / "baselines" / "engine.sha256"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_engine_matches_baseline() -> None:
    expected = BASELINE.read_text(encoding="ascii").strip()
    assert expected, "基线文件为空：tests/baselines/engine.sha256"

    assert _sha256(ENGINE) == expected, (
        "engine.py 与基线不一致：新增域只应新增 profiles/*.yaml，"
        "确需改引擎请说明理由后重新生成基线"
    )


def test_baseline_covers_current_file() -> None:
    """基线与被测文件必须是同一份内容量级，防止基线指向空文件/旧路径。"""
    assert ENGINE.is_file() and ENGINE.stat().st_size > 0
    assert len(BASELINE.read_text(encoding="ascii").strip()) == 64
