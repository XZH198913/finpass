"""判据 4 的落地证据：verify 的扩展点 id 必须落在登记表集合内（R1 / R2）。

阶段 0 能断言的是「没有野 id、replay 不是扩展点」——这两条现在就能绿。
「三个 id 都已在 verify 里出现」要等 7a 的 ext 分支落地，故显式 xfail 并写明 reason，
不默默跳过。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from finpass import passport

SRC = Path(passport.__file__).resolve().parent
REGISTERED = {"ext:patch_parent", "ext:dry_run", "ext:phase_group"}
ID_RE = re.compile(r"ext:[a-z_]+")


def _ids_in(path: Path) -> set[str]:
    return set(ID_RE.findall(path.read_text(encoding="utf-8")))


def _ids_in_package() -> set[str]:
    found: set[str] = set()
    for path in SRC.glob("*.py"):
        found |= _ids_in(path)
    return found


def test_no_unregistered_extension_id() -> None:
    # R1：id ⊆ 登记表集合。扫描全包，不只看 verify，防止别处写出拼错的 id
    found = _ids_in_package()
    assert found <= REGISTERED, f"未登记的扩展点 id：{sorted(found - REGISTERED)}"


def test_replay_is_not_an_extension() -> None:
    # R2：不扩展项不分配 id——ext:replay 一旦出现即红
    assert "ext:replay" not in _ids_in_package()


@pytest.mark.xfail(reason="等 7a 实现 ext 分支", strict=True)
def test_verify_carries_all_registered_ids() -> None:
    """三个登记 id 必须都出现在 verify 所在模块；7a 落地后本用例转为绿。"""
    assert REGISTERED <= _ids_in(SRC / "passport.py")
