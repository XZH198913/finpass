"""打包契约：验的是「装完之后」的样子，不是仓库里的样子。

README 与能力表都承诺「4 个 profile 随包发布」。这条承诺只在 wheel / site-packages 里成立：
``profiles/`` 若被挪出包目录，本地 `pip install -e .` 照样跑得通（源码就在旁边），
装到干净环境才炸，而那时的报错看起来像用户把参数写错了。

同名版本号还有第二处漂移风险：``pyproject`` 用 ``dynamic`` 引用 ``finpass.__version__``，
但**装好的元数据不会跟着改**——改版本号不重装，`finpass --version` 报的还是旧值。
"""

from __future__ import annotations

from importlib.metadata import entry_points, version
from pathlib import Path

import finpass
from finpass.profile import PROFILE_DIR

REPO = Path(__file__).resolve().parents[1]

#: 能力表（README「机制覆盖度」）列出的 profile。新增 profile 时同步这一行。
SHIPPED_PROFILES = {"a_stock_daily", "a_stock_valuation", "fund_nav", "futures"}


def test_metadata_version_matches_dunder_version() -> None:
    assert version("finpass") == finpass.__version__, (
        "包元数据与 finpass.__version__ 不一致：改完版本号要重装一次（`pip install -e .`）"
    )


def test_console_script_points_at_cli_main() -> None:
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}

    assert scripts.get("finpass") == "finpass.cli:main"


def test_profiles_ship_inside_the_package() -> None:
    package_dir = Path(finpass.__file__).parent

    assert PROFILE_DIR.parent == package_dir, f"profiles 不在包目录里：{PROFILE_DIR}"
    assert {path.stem for path in PROFILE_DIR.glob("*.yaml")} == SHIPPED_PROFILES


def test_license_is_declared_and_complete() -> None:
    """许可证要在两处对齐：`pyproject` 声明，`LICENSE` 是完整原文（不是摘要/链接）。

    文本级断言而非解析 TOML：`requires-python >= 3.10`，而 `tomllib` 3.11 才有；
    为一条断言引入 tomli 不划算。换版权主体不影响这两条（只认 `Copyright 20xx`）。
    """
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    license_text = (REPO / "LICENSE").read_text(encoding="utf-8")

    assert 'license = "Apache-2.0"' in pyproject
    assert 'license-files = ["LICENSE"]' in pyproject
    assert "Version 2.0, January 2004" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
    assert "Copyright 20" in license_text
    # 摘要式 LICENSE（「本项目采用 Apache-2.0，详见 http://...」）会让 twine check 也过、
    # 但用户在包里拿不到条款原文，所以这里钉住正文关键句。
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in license_text
