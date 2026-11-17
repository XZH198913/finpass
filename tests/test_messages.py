"""文案表自检：两套语言的键与占位符必须一一对应。

缺键只在**运行时**才炸（``t`` 故意抛 ``KeyError``，不静默返回空串），而默认语言是 ``en``，
CI 里钉的是 ``zh``——所以键集漂移（只往 zh 补、忘了 en）靠人工看不出来，必须由测试守住。
"""

from __future__ import annotations

import string

import pytest

from finpass import messages
from finpass.messages import CATALOGS, SUPPORTED_LANGUAGES, language, normalize, set_language, t


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_languages_are_covered() -> None:
    assert tuple(CATALOGS) == SUPPORTED_LANGUAGES


def test_key_sets_are_identical() -> None:
    baseline = set(CATALOGS[SUPPORTED_LANGUAGES[0]])
    for language in SUPPORTED_LANGUAGES[1:]:
        assert set(CATALOGS[language]) == baseline, f"{language} 的键集与基准语言不一致"


def test_placeholders_match_per_key() -> None:
    baseline = CATALOGS[SUPPORTED_LANGUAGES[0]]
    for language in SUPPORTED_LANGUAGES[1:]:
        catalog = CATALOGS[language]
        for key, template in baseline.items():
            assert _placeholders(catalog[key]) == _placeholders(template), f"{language}:{key}"


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_missing_key_raises(language: str) -> None:
    """缺键宁可崩：``t`` 不返回空串，否则用户看到的是空白而非错误。"""
    set_language(language)
    try:
        with pytest.raises(KeyError):
            t("no.such.key")
    finally:
        set_language(None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("en", "en"),
        ("EN-US", "en"),
        ("english", "en"),
        ("zh-cn", "zh"),
        ("zh_cn", "zh"),
        ("cn", "zh"),
        ("chinese", "zh"),
        ("fr", "en"),
        ("", "en"),
    ],
)
def test_normalize_aliases(value: str, expected: str) -> None:
    assert normalize(value) == expected


def test_language_precedence(monkeypatch: pytest.MonkeyPatch, set_lang) -> None:
    """显式 set_language > 环境变量 > 默认英文；复位后重新跟随环境变量。"""
    monkeypatch.setenv("FINPASS_LANG", "zh-cn")
    set_lang(None)
    assert language() == "zh"
    assert t("option.skip") == "跳过打标"

    set_lang("en")
    assert language() == "en"
    assert t("option.skip") == "Skip and flag"

    monkeypatch.delenv("FINPASS_LANG", raising=False)
    set_lang(None)
    assert language() == "en"
    assert messages.DEFAULT_LANGUAGE == "en"
