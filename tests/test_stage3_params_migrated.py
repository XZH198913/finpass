"""阶段 3 验收：参数只有一份来源——profile 的 ``target_state`` / ``policy``。

三条硬约束（对应 ``docs/stage3-unmigrated-params.md`` 的清空判据）：

1. 模块级 ``DATE_FMT`` / ``UNIT_SPREAD`` 已**删除**，不是改成别名或保留兜底；
2. 源码里不再出现 ``0.0001`` / ``"%Y-%m-%d"`` 这类域参数字面量；
3. ``params.get(...)`` 只剩**决策层**的两个键（``rows`` / ``duplicate_keep``），
   其余参数一律从 profile 读，缺了就明确拒绝。

判据口径（阶段 3 升级，写死在这里）：
``params.get`` 只允许出现 ``rows`` / ``duplicate_keep``；**任何第三个键出现即视为范式参数回流**。
清单里「清空」的从来是**范式参数**（A1–A5 / B1–B5），决策层参数保留是设计，不是遗漏。

用源码扫描而不是 shell grep：本地 grep 过了、CI 没跑，等于没验。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import finpass as fp
from finpass import engine as engine_module
from finpass import session as session_module
from finpass.profile import PROFILE_DIR

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"
SESSION_SOURCE = (REPO / "src" / "finpass" / "session.py").read_text(encoding="utf-8")
# 4c 起决策由 ``dispatch`` 做，逐题参数的白名单与 ``params.get`` 都在 engine 里。
ENGINE_SOURCE = (REPO / "src" / "finpass" / "engine.py").read_text(encoding="utf-8")
CLI_SOURCE = (REPO / "src" / "finpass" / "cli.py").read_text(encoding="utf-8")
A_STOCK_RAW = yaml.safe_load((PROFILE_DIR / "a_stock_daily.yaml").read_text(encoding="utf-8"))


def _profile(**overrides: object) -> dict:
    """以出厂 profile 为底稿改一两处，避免在测试里重抄整份 yaml。"""
    payload = {**A_STOCK_RAW, **overrides}
    return payload


def _question(session: fp.Session, rule_id: str):
    return next(item for item in session.queue if item.rule_id == rule_id)


# ------------------------------------------------------------------ 源码侧


def test_module_constants_are_deleted_not_aliased() -> None:
    assert not hasattr(session_module, "DATE_FMT")
    assert not hasattr(session_module, "UNIT_SPREAD")
    assert "DATE_FMT" not in SESSION_SOURCE
    assert "UNIT_SPREAD" not in SESSION_SOURCE


def test_no_hardcoded_unit_factor_or_date_pattern() -> None:
    assert "0.0001" not in SESSION_SOURCE
    assert "%Y-%m-%d" not in SESSION_SOURCE


def test_decision_params_whitelist_is_pinned() -> None:
    """白名单必须逐字钉住：多一个键就意味着范式参数又藏回代码里了。"""
    assert engine_module.DECISION_PARAMS == {
        "duplicate_key": frozenset({"duplicate_keep"}),
        "ohlc_contradiction": frozenset({"rows"}),
    }


def test_params_get_only_covers_decision_layer() -> None:
    """``params.get("X")`` 的键集合 == 决策层白名单；多一个就红。"""
    allowed = set().union(*engine_module.DECISION_PARAMS.values())
    keys = set(re.findall(r'params\.get\("(\w+)"\)', ENGINE_SOURCE))

    assert keys == allowed


def test_session_has_no_params_get_left() -> None:
    """决策层整层搬到 engine，session 里一处 ``params.get`` 都不许剩。

    上游断言已改看 engine：若 ``_fix_*`` 搬家时漏了某条异常分支，只改扫描对象就抓不到，
    「搬家搬干净」必须有自己的机械证据。
    """
    assert not re.findall(r'params\.get\("(\w+)"\)', SESSION_SOURCE)


def test_cli_no_longer_sends_keep() -> None:
    assert '"keep"' not in CLI_SOURCE
    assert '"scale"' not in CLI_SOURCE


# ------------------------------------------------------------------ 行为侧


def test_unit_factor_comes_from_target_state() -> None:
    """检测只看 spread，换算系数要到修复时才读——缺 factor 必须在修复前就报错。"""
    target = dict(A_STOCK_RAW["target_state"])
    target["成交额_元"] = {"semantic": "amount", "unit_scale": {"from": "万元", "spread": 1000}}
    session = fp.open(DIRTY_CSV, profile=_profile(target_state=target))
    session.start()

    with pytest.raises(ValueError, match=r"target_state\[成交额_元\]\.unit_scale\.factor"):
        session.answer(_question(session, "unit_ambiguity").question_id, "batch_apply", {})


def test_stale_scale_param_is_rejected() -> None:
    """旧 api 的 ``scale`` 必须报错而不是静默回退到 profile 的 factor。"""
    session = fp.open(DIRTY_CSV)
    session.start()
    question = _question(session, "unit_ambiguity")

    with pytest.raises(ValueError, match="不接受参数"):
        session.answer(question.question_id, "batch_apply", {"scale": 0.001})


def test_missing_policy_key_is_rejected_at_open() -> None:
    with pytest.raises(ValueError, match="policy.duplicate_keep 缺失"):
        fp.open(DIRTY_CSV, profile=_profile(policy={"default_row_action": "answer"}))


def test_illegal_policy_value_is_rejected_at_open() -> None:
    with pytest.raises(ValueError, match="policy.duplicate_keep 非法"):
        fp.open(
            DIRTY_CSV,
            profile=_profile(policy={"duplicate_keep": "middle", "default_row_action": "answer"}),
        )


def test_range_bounds_come_from_target_state() -> None:
    futures = yaml.safe_load((PROFILE_DIR / "futures.yaml").read_text(encoding="utf-8"))
    target = dict(futures["target_state"])
    target["涨跌幅_%"] = {key: value for key, value in target["涨跌幅_%"].items() if key != "range"}

    session = fp.open(REPO / "examples" / "futures_daily.csv", profile={**futures, "target_state": target})

    with pytest.raises(ValueError, match=r"target_state\[涨跌幅_%\]\.range"):
        session.start()


def test_date_option_text_follows_target_state() -> None:
    """改 profile 的 format，选项文案跟着变——证明文案与检测/修复同源。"""
    target = dict(A_STOCK_RAW["target_state"])
    target["日期"] = {**target["日期"], "format": "%Y/%m/%d"}
    session = fp.open(DIRTY_CSV, profile=_profile(target_state=target))
    session.start()

    assert _question(session, "date_format").options[0] == "转换为 %Y/%m/%d"


def test_english_date_option_text_follows_target_state(set_lang) -> None:
    set_lang("en")
    target = dict(A_STOCK_RAW["target_state"])
    target["日期"] = {**target["日期"], "format": "%d.%m.%Y"}
    session = fp.open(DIRTY_CSV, profile=_profile(target_state=target))
    session.start()

    assert _question(session, "date_format").options[0] == "Convert to %d.%m.%Y"


def test_duplicate_keep_override_is_honoured() -> None:
    session = fp.open(DIRTY_CSV)
    session.start()
    question = _question(session, "duplicate_key")

    before = session.df.copy()
    keys = session._row_keys(before)
    expected = keys[before.duplicated(session.keys, keep="last")].tolist()

    changed = session.answer(question.question_id, "answer", {"duplicate_keep": "last"}, "保留最后一条")

    assert sorted(change["row_key"] for change in changed) == sorted(expected)
    assert not session.df.duplicated(session.keys).any()
