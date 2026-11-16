"""阶段 4a：engine 契约（``Gap`` 模型 + 五步空签名 + import 白名单）。

三条判据（都靠测试拦，不靠自觉）：

1. ``engine.py`` 的 import 集合 ⊆ 白名单，尤其**不含** ``profile`` / ``session`` / ``passport`` /
   ``cli`` / ``diff``——engine 只收已解析的 ``target_state`` / ``policy``，不读 profile、不读护照；
2. 五个函数的参数名与顺序钉死：改签名必须改这个测试，避免「签名悄悄漂移」；
3. 未落地的步骤必须是 ``NotImplementedError``：4a 五个全空，4b 落了 ``detect``，
   4c 落了 ``dispatch`` / ``fix``，只剩 ``reverify`` / ``audit`` 两个空壳；``Gap`` 只描述差距，
   不带「怎么问人」的字段。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from finpass import engine
from finpass.models import DispatchResult, Gap

ENGINE_PATH = Path(engine.__file__)
SOURCE = ENGINE_PATH.read_text(encoding="utf-8")

# ``datetime`` 是 4b 加的：``date_format`` 的判据要与搬家前逐字相同（``strptime``），
# 不能换成 pandas 的宽松解析。``json`` 是 4c 加的：``dropped_row`` 的 ``before`` 存整行 JSON。
# 两者都是标准库，不属于「上层模块」，所以进白名单。
ALLOWED_IMPORTS = {
    "__future__",
    "typing",
    "dataclasses",
    "pandas",
    "numpy",
    "datetime",
    "json",
    ".models",
}
UPPER_LAYERS = {"profile", "session", "passport", "cli", "diff"}

# 4c 的两处签名修正（见 ``docs/spec-stage4-追认.md`` §十二）：
# - ``dispatch`` 加 ``df``（``changes`` 的 before 必须来自数据）与 ``decisions``（逐题覆盖）；
# - ``fix`` 加 ``target_state``（按 row_key 定位行要知道主键列，主键在 role: key 里）。
STEPS = {
    "detect": ["df", "target_state", "policy", "phase"],
    "dispatch": ["df", "gaps", "decisions", "target_state", "policy"],
    "fix": ["df", "changes", "target_state"],
    "reverify": ["df", "target_state", "policy"],
    "audit": ["events", "changes", "flags"],
}


def _import_targets(source: str) -> set[str]:
    """把源码里的 import 归一成「模块名」集合（相对导入保留前导点）。"""
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.add("." * node.level + (node.module or ""))
    return targets


def test_engine_imports_are_whitelisted() -> None:
    targets = _import_targets(SOURCE)
    assert targets <= ALLOWED_IMPORTS, f"engine 引入了白名单外的模块：{sorted(targets - ALLOWED_IMPORTS)}"


def test_engine_does_not_import_upper_layers() -> None:
    modules = {target.lstrip(".").split(".")[0] for target in _import_targets(SOURCE)}
    assert modules & UPPER_LAYERS == set()


def test_steps_do_not_take_schema_version() -> None:
    """``policy`` 由 session 解析成完整 dict；engine 拿不到 schema 版本，也就无从判断默认值。"""
    for name in STEPS:
        parameters = inspect.signature(getattr(engine, name)).parameters
        assert [p for p in parameters if "schema" in p] == []


@pytest.mark.parametrize("name,params", list(STEPS.items()))
def test_step_signature_is_pinned(name: str, params: list[str]) -> None:
    assert list(inspect.signature(getattr(engine, name)).parameters) == params


PENDING_STEPS = {name: STEPS[name] for name in ("reverify", "audit")}


@pytest.mark.parametrize("name,params", list(PENDING_STEPS.items()))
def test_step_is_still_a_stub(name: str, params: list[str]) -> None:
    """4c 已实现 ``detect`` / ``dispatch`` / ``fix``；只剩两步是空壳，不许半实现。"""
    with pytest.raises(NotImplementedError):
        getattr(engine, name)(*[None] * len(params))


def test_detect_only_implements_initial_phase() -> None:
    """``reverify`` 的语义是 4d 的内容：4b 提前实现会与 4d 打架，所以必须明确拒绝。"""
    with pytest.raises(NotImplementedError, match="phase"):
        engine.detect(None, {}, {}, phase="reverify")


def test_engine_role_vocabulary_matches_profile() -> None:
    """engine 不 import ``profile``，角色字面量是复述；复述漂移必须在这里拦住。"""
    from finpass.profile import COLUMN_ROLES, KEY_ROLE

    assert engine.KEY_ROLE == KEY_ROLE
    assert engine.COLUMN_ROLES == COLUMN_ROLES


def test_engine_decision_vocabulary_matches_profile() -> None:
    """``duplicate_keep`` 的取值域与决策参数白名单同样是复述，必须与加载器一致。"""
    from finpass.profile import POLICY_REQUIREMENTS

    assert engine.DUPLICATE_KEEP == POLICY_REQUIREMENTS["duplicate_key"][1]
    assert set(engine.DECISION_PARAMS) <= set(engine.RULE_TYPES)


def test_no_step_takes_a_profile_object() -> None:
    for name in STEPS:
        assert "profile" not in inspect.signature(getattr(engine, name)).parameters


def test_gap_is_gap_not_question() -> None:
    """``Gap`` 只能有「差距」字段；出现 options / default 就说明展示层又漏进 engine 了。"""
    gap = Gap(rule_id="r", targets=["列"], rows=[{"k": 1}], evidence={"count": 1})

    payload = dataclasses.asdict(gap)
    assert set(payload) == {"rule_id", "targets", "rows", "evidence"}
    assert not {"options", "default", "aggregation_level"} & set(payload)


def test_dispatch_result_holds_changes_and_flags() -> None:
    result = DispatchResult(changes=[], flags=[])

    assert result.changes == []
    assert result.flags == []
