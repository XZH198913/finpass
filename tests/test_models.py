"""问题卡片双语文案：版式固定、文案随语言切换。

``Question.render()`` 是 notebook 通道的遗留展示层，与 ``session._render_text()`` 各自独立；
两条路径都必须走文案表，不能只双语化其中一条。
"""

from __future__ import annotations

from finpass.models import Question


def _question() -> Question:
    return Question(
        question_id="q_missing_收盘价",
        rule_id="missing_token",
        aggregation_level="column",
        affected_row_keys=["r1", "r2"],
        samples=[{"收盘价": "--"}, {"收盘价": "暂无"}, {"收盘价": "N/A"}],
        options=["Fill with NaN", "Skip and flag"],
        default="Fill with NaN",
        estimated_changes=2,
    )


def test_render_zh(set_lang) -> None:
    set_lang("zh")
    text = _question().render()
    lines = text.splitlines()

    assert lines[0] == "┌─ [column] missing_token  (q_missing_收盘价)"
    assert lines[-1] == "└" + "─" * 58
    assert "影响行数：2" in text and "预估变更：2" in text
    assert "│ 样本：收盘价=--" in text
    assert "可选答案：Fill with NaN / Skip and flag" in text
    assert "默认建议：Fill with NaN" in text


def test_render_en(set_lang) -> None:
    set_lang("en")
    text = _question().render()
    lines = text.splitlines()

    assert lines[0] == "┌─ [column] missing_token  (q_missing_收盘价)"
    assert lines[-1] == "└" + "─" * 58
    assert "affected rows: 2" in text and "estimated changes: 2" in text
    assert "│ sample: 收盘价=--" in text
    assert "options: Fill with NaN / Skip and flag" in text
    assert "default: Fill with NaN" in text
    assert "影响行数" not in text and "可选答案" not in text


def test_render_caps_samples_at_five(set_lang) -> None:
    set_lang("en")
    question = _question()
    question.samples = [{"收盘价": str(index)} for index in range(7)]

    lines = question.render().splitlines()
    sample_lines = [line for line in lines if "│ sample:" in line]

    assert len(sample_lines) == 5
    assert "收盘价=4" in sample_lines[-1]
    assert "收盘价=5" not in question.render()
