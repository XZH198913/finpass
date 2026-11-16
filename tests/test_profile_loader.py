"""profile 加载器契约：yaml / 路径 / 字典三种入口，结构错误必须在加载时炸。

会话层的所有域知识都从这里进来，所以「少字段」「重复 rule id」「找不到 profile」
必须是加载期的 ``ValueError``，不能拖到规则执行时才暴露。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from finpass.profile import PROFILE_DIR, load_profile, validate_columns

REPO = Path(__file__).resolve().parents[1]


def _minimal(**overrides: object) -> dict:
    # schema 2.0.0 起 target_state 必填；旧结构（无 target_state）由
    # tests/test_stage3_schema.py 单独验「必须被拒绝」。
    payload: dict = {
        "name": "demo",
        # schema 2.3.0：主键列由 target_state[列].role: key 声明，顶层 key 段已删除
        "target_state": {"日期": {"semantic": "date", "role": "key"}},
        "business_rules": ["missing_token"],
    }
    payload.update(overrides)
    return payload


def test_loads_both_shipped_profiles() -> None:
    a_stock = load_profile("a_stock_daily")
    futures = load_profile("futures")

    assert a_stock.key_columns == ["日期"]
    # 复合主键的顺序必须原样保留：row_key 是 "a|b"，顺序变了字符串就变
    assert futures.key_columns == ["合约代码", "日期"]
    assert futures.key_label == "合约代码+日期"
    assert a_stock.source.endswith("a_stock_daily.yaml")
    assert futures.rule_ids[-1] == "limit_up_down"


def test_accepts_yaml_path_and_dict() -> None:
    by_path = load_profile(PROFILE_DIR / "futures.yaml")
    by_dict = load_profile(_minimal())

    assert by_path.source.endswith("futures.yaml")
    assert by_dict.name == "demo"
    assert by_dict.source == "<dict>"


def test_rule_shorthand_and_dict_share_one_shape() -> None:
    profile = load_profile(
        _minimal(
            business_rules=[
                "missing_token",
                {"id": "limit_up_down", "type": "range_check", "column": "涨跌幅_%", "min": -10, "max": 10},
            ]
        )
    )

    assert profile.rules[0].id == "missing_token"
    assert profile.rules[0].type == "missing_token"
    assert profile.rules[1].type == "range_check"
    assert profile.rules[1].columns == ["涨跌幅_%"]


def test_rule_mode_defaults_to_human() -> None:
    profile = load_profile(_minimal())

    assert profile.rules[0].mode == "human"


def test_rule_mode_can_be_declared_auto() -> None:
    profile = load_profile(_minimal(business_rules=[{"id": "date_format", "mode": "auto"}]))

    assert profile.rules[0].mode == "auto"


def test_invalid_rule_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="mode 非法：auto_maybe"):
        load_profile(_minimal(business_rules=[{"id": "date_format", "mode": "auto_maybe"}]))


def test_shipped_profiles_declare_auto_rules() -> None:
    """降级通道只跑 mode: auto 的规则，出厂 profile 必须真的声明了它们。"""
    for name in ("a_stock_daily", "futures"):
        modes = {rule.id: rule.mode for rule in load_profile(name).rules}
        assert modes["missing_token"] == "auto"
        assert modes["date_format"] == "auto"
        assert modes["ohlc_contradiction"] == "human"


def test_key_columns_come_from_role_not_from_a_top_level_field() -> None:
    """主键来自 ``role: key``；单列时 ``key_label`` 就是列名。"""
    profile = load_profile(_minimal())

    assert profile.key_columns == ["日期"]
    assert profile.key_label == "日期"
    assert profile.date_column == "日期", "单主键未声明 date_column 时默认取主键列"


def test_key_columns_order_is_pinned() -> None:
    """复合主键的顺序显式钉住。

    ``row_key`` 是 ``"a|b"`` 拼接，调整 yaml 里两列的顺序就会让 12 个数据产物全红——
    产物不变只是「当前碰巧对了」，这条测试才是「改了就红」。
    """
    assert load_profile("a_stock_daily").key_columns == ["日期"]
    assert load_profile("futures").key_columns == ["合约代码", "日期"]
    assert load_profile("futures").key_label == "合约代码+日期"


def test_composite_key_requires_explicit_date_column() -> None:
    """复合主键时不回落到字面量「日期」：有多个候选日期列时，「哪个」必须显式声明。"""
    payload = _minimal(
        target_state={
            "合约代码": {"semantic": "symbol", "role": "key"},
            "日期": {"semantic": "date", "role": "key"},
        }
    )

    with pytest.raises(ValueError, match="必须显式声明 date_column"):
        load_profile(payload)


def test_legacy_key_section_is_rejected() -> None:
    """留着顶层 ``key:`` 就是两份主键来源（且 engine 读不到），必须明确拒绝。"""
    payload = _minimal()
    payload["key"] = ["日期"]

    with pytest.raises(ValueError, match="key 段已删除"):
        load_profile(payload)


def test_profile_without_any_key_role_is_rejected() -> None:
    payload = _minimal(target_state={"日期": {"semantic": "date"}})

    with pytest.raises(ValueError, match="没有任何 role: key"):
        load_profile(payload)


@pytest.mark.parametrize("field", ["name", "business_rules"])
def test_missing_required_field_names_itself(field: str) -> None:
    payload = _minimal()
    payload.pop(field)

    with pytest.raises(ValueError, match=f"缺少必需字段：{field}"):
        load_profile(payload)


def test_duplicate_rule_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="重复 id：missing_token"):
        load_profile(_minimal(business_rules=["missing_token", "missing_token"]))


def test_placeholder_profile_says_it_is_a_placeholder() -> None:
    """``fund_nav`` 保留占位：报错必须说明「占位 / 未迁移」，不能只说缺字段。"""
    with pytest.raises(ValueError) as excinfo:
        load_profile("fund_nav")

    message = str(excinfo.value)
    assert "占位文件" in message
    assert "schema" in message, "note 要说明「未迁移到当前 schema」，不钉死版本号（否则每次升版都红）"
    assert "缺少必需字段" not in message, "占位不等于配置写错，不能退化成缺字段提示"


def test_placeholder_is_a_hard_marker_even_with_complete_fields() -> None:
    """``placeholder: true`` 是硬标记：字段齐全也不许加载成功。

    否则「补了字段但忘了删 placeholder」时，「加载成功」与「加载失败」两种解释都说得通，
    这个标记就退化成暧昧的注释。正确语义是：**它不是 profile，是占位说明，没有字段能救它**。
    """
    complete = {
        "name": "placeholder_demo",
        "target_state": {"日期": {"semantic": "date", "role": "key"}},
        "business_rules": ["missing_token"],
        "placeholder": True,
        "note": "这是占位说明，不是 profile",
    }

    with pytest.raises(ValueError) as excinfo:
        load_profile(complete)

    message = str(excinfo.value)
    assert "这是占位说明，不是 profile" in message, "note 必须原样带出，便于作者理解"
    assert "缺少必需字段" not in message, "字段是齐全的，报缺字段会误导作者去补字段"


def _ohlc_profile(**overrides: object) -> dict:
    """声明了 ohlc_contradiction 的最小 profile：三个角色各一列。"""
    target = {
        "日期": {"semantic": "date", "role": "key"},
        "最高价": {"semantic": "price", "role": "high"},
        "最低价": {"semantic": "price", "role": "low"},
        "收盘价": {"semantic": "price", "role": "close"},
    }
    payload = _minimal(
        target_state=target,
        business_rules=["missing_token", "ohlc_contradiction"],
        # 2.1.0 起：声明了 ohlc_contradiction 就必须给出 default_row_action
        policy={"default_row_action": "answer"},
    )
    payload.update(overrides)
    return payload


def test_role_maps_to_column_without_columns_section() -> None:
    profile = load_profile(_ohlc_profile())

    assert profile.role_columns() == {"high": "最高价", "low": "最低价", "close": "收盘价"}


def test_duplicate_role_is_rejected() -> None:
    """唯一性要双向：两个列都声明 high 时，role→列 的映射会取决于书写顺序。"""
    payload = _ohlc_profile()
    payload["target_state"] = {**payload["target_state"], "开盘价": {"semantic": "price", "role": "high"}}

    with pytest.raises(ValueError, match="role 重复"):
        load_profile(payload)


def test_missing_role_is_rejected_when_ohlc_rule_declared() -> None:
    payload = _ohlc_profile()
    payload["target_state"] = {
        column: target for column, target in payload["target_state"].items() if target.get("role") != "close"
    }

    with pytest.raises(ValueError, match="缺少 role：close"):
        load_profile(payload)


def test_role_is_not_required_without_ohlc_rule() -> None:
    """强制条件是「声明了依赖它的规则」，不是「列语义是 price」。"""
    profile = load_profile(
        _minimal(
            target_state={
                "日期": {"semantic": "date", "role": "key"},
                "收盘价": {"semantic": "price"},
            }
        )
    )

    assert profile.role_columns() == {}


def test_unknown_role_value_is_rejected() -> None:
    payload = _ohlc_profile()
    payload["target_state"] = {**payload["target_state"], "开盘价": {"semantic": "price", "role": "open"}}

    with pytest.raises(ValueError, match="role 非法"):
        load_profile(payload)


def test_legacy_columns_section_is_rejected() -> None:
    """留着旧的 columns: 会静默失效，等于两份角色来源，必须明确拒绝。"""
    with pytest.raises(ValueError, match="columns 段已删除"):
        load_profile(_ohlc_profile(columns={"high": "最高价"}))


def test_required_columns_unchanged_after_role_migration() -> None:
    """必需列集合必须与迁移前逐列一致——人工核对不算数。"""
    a_stock = set(load_profile("a_stock_daily").required_columns())
    futures = set(load_profile("futures").required_columns())

    assert a_stock == {"日期", "最高价", "最低价", "收盘价", "成交额_元"}
    assert futures == {"合约代码", "日期", "最高价", "最低价", "收盘价", "涨跌幅_%"}
    assert "持仓量" not in futures, "role 不能把未被引用的列拖成必需列"


def test_cross_column_rules_untouched_by_role_migration() -> None:
    """prerequisite 只改角色来源：cross_column_rules 的 id 与涉及列必须原样。

    工作区没有 git（``.git`` 不存在），用断言代替 ``git diff`` 做机械证据。
    **两份 profile 各写一份期望值**：共用一个常量会引入「futures 换列名就被强行对齐到
    a_stock」的隐性耦合，等于只守护了一份。

    用 ``set`` 而不是 ``list`` 断言：``involves`` 的顺序**不进入任何判据**——全仓库除本测试外
    没有消费者（``grep cross_column_rules`` 只有 ``profile.py`` 的解析与校验），代码里也没有
    ``involves[0]`` 这类按下标取列的写法。角色只从 ``target_state[*].role`` 读。
    用 list 断言会把「调整清单顺序但语义不变」卡成阻塞，属于假守护。
    """
    expected = {
        "a_stock_daily": {"最高价", "最低价", "收盘价"},
        "futures": {"最高价", "最低价", "收盘价"},
    }
    for name, involves in expected.items():
        rules = load_profile(name).cross_column_rules
        assert len(rules) == 1, name
        assert rules[0].id == "ohlc_consistency", name
        assert set(rules[0].involves) == involves, name
        assert len(rules[0].involves) == len(involves), name


def test_unknown_profile_points_at_searched_path() -> None:
    with pytest.raises(ValueError) as excinfo:
        load_profile("no_such_profile")

    assert "找不到 profile" in str(excinfo.value)
    assert "no_such_profile.yaml" in str(excinfo.value)


def test_required_columns_cover_key_date_roles_and_rules() -> None:
    profile = load_profile("futures")
    required = profile.required_columns()

    assert required[0] == "合约代码"
    assert {"日期", "最高价", "最低价", "收盘价", "涨跌幅_%"} <= set(required)
    assert len(required) == len(set(required)), "必需列不应重复"
    assert "持仓量" not in required, "未被主键/规则引用的列不该是必需列"


def test_validate_columns_names_every_absent_column() -> None:
    profile = load_profile("futures")

    with pytest.raises(ValueError) as excinfo:
        validate_columns(["日期", "最高价", "最低价", "收盘价"], profile)

    message = str(excinfo.value)
    assert "profile futures_daily 缺少必需列" in message
    assert "合约代码" in message and "涨跌幅_%" in message
    assert "日期" not in message.split("缺少必需列：")[1]


def test_shipped_profiles_declare_no_synonyms() -> None:
    """仓库内 profile 不再写 ``synonyms``：留着就是把不生效的键写成承诺。

    加载器仍解析该键（旧文件不炸），但全仓库没有消费点——声明它等于告诉作者
    「列名可以不一样」。发布版宁可让作者当场撞上「缺少必需列」，也不要他先
    以为自己写对了。
    """
    payloads = {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for path in sorted(PROFILE_DIR.glob("*.yaml"))
    }

    offenders = [name for name, payload in payloads.items() if "synonyms" in payload]
    assert offenders == [], f"这些 profile 又声明了未接线的 synonyms：{offenders}"


def test_synonyms_still_parse_but_never_relax_column_names() -> None:
    """兼容旧文件：能解析；但别名不会让 ``validate_columns`` 放过真列名。"""
    profile = load_profile(_minimal(synonyms={"日期": ["date", "trade_date"]}))

    assert profile.synonyms == {"日期": ["date", "trade_date"]}
    with pytest.raises(ValueError) as excinfo:
        validate_columns(["date"], profile)
    assert "缺少必需列" in str(excinfo.value)
