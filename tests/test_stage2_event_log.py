"""阶段 2：终端日志与汇总行口径（日志层之后的新契约）。

两件事各自有判据：

- **逐条**：一行 = 一个 ``(rule_id, 动作)`` 分组的**人话翻译**，四要素齐（动作 / 对象 / 依据 / 结果），
  不再打 ``event_id``——审计退到 ``events.jsonl``（日志末行指路）。**这是契约变更，不是放宽**：
  旧的「逐行 ``event_id`` 反查」换成「按 ``rule_id`` 与 ``changes`` 对账」，
  可追溯性仍在，只是从终端挪到产物；
- **汇总**：``[汇总] 检查 N 条规则 / 触发 M 条 / 未动 K 条 / 未答 K' 条``，
  N/M/K 是**规则**口径，K' 是**问题**口径（与阶段 1 的 ``counts.unanswered`` 同源）。
  标签在日志层改成中文 ``[汇总]``（原 ``[summary]``）：钉住的是 N/M/K 的口径，不是标签语种。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

from finpass.narrate import LABELS, TRACE_LINE
from finpass.profile import load_profile

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"
STEM = DIRTY_CSV.stem
SUMMARY_LINE = re.compile(
    r"^\[汇总\] 检查 (?P<total>\d+) 条规则 / 触发 (?P<hit>\d+) 条 / "
    r"未动 (?P<idle>\d+) 条 / 未答 (?P<unanswered>\d+) 条$"
)
LOG_LINE = re.compile(r"^(?P<header>\[[^\]]+\]) (?P<body>.+)$")
QUESTION_LINE = re.compile(
    r"^问题 (?P<questions>\d+) 个 \| 命中 (?P<rules>\d+) 条规则：(?P<names>.+)$"
)
ROWS_IN_LINE = re.compile(r"(\d+) 行")
PROFILE = load_profile("a_stock_daily")


def _run(tmp_path: Path) -> tuple[list[str], list[dict], dict, pd.DataFrame]:
    """跑一次真实进程，返回（stdout 行, events, passport, changes）。"""
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "finpass", "run", str(DIRTY_CSV), "--out", str(tmp_path)],
        cwd=REPO,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 2, proc.stderr  # 默认路径有 flag → 软失败

    events = [
        json.loads(line)
        for line in (tmp_path / f"{STEM}.events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    passport = json.loads((tmp_path / f"{STEM}.passport.json").read_text(encoding="utf-8"))
    changes = pd.read_parquet(tmp_path / f"{STEM}.changes.parquet")
    return proc.stdout.splitlines(), events, passport, changes


def _log_lines(stdout: list[str]) -> list[str]:
    """日志正文行：去掉 ``[汇总]``、统计行与末行的「详细决策见…」提示。"""
    return [
        line
        for line in stdout
        if line.startswith("[") and not line.startswith("[汇总]") and line != TRACE_LINE
    ]


def _label_of(rule_id: str) -> str:
    """``rule_id`` → 方括号标签：先查 profile 的 type，再查模板表，最后退回 id 本身。"""
    types = {spec.id: spec.type for spec in PROFILE.rules}
    return LABELS.get(types.get(rule_id, rule_id), rule_id)


def test_every_log_line_follows_the_readable_format(tmp_path: Path) -> None:
    stdout, _events, _passport, _changes = _run(tmp_path)
    lines = _log_lines(stdout)
    assert lines, "终端没有输出任何处理记录"
    for line in lines:
        parsed = LOG_LINE.match(line)
        assert parsed, f"格式不符（缺方括号标签）：{line!r}"
        body = parsed["body"]
        assert any(marker in body for marker in ("列", "行", "主键")), f"缺对象：{line!r}"
        assert any(marker in body for marker in ("发现", "含", "矛盾", "越界", "处")), f"缺依据：{line!r}"
        assert "→" in body, f"缺结果：{line!r}"


def test_summary_line_matches_rule_and_count_scope(tmp_path: Path) -> None:
    stdout, events, passport, _changes = _run(tmp_path)
    summaries = [line for line in stdout if line.startswith("[汇总]")]
    assert len(summaries) == 1, f"汇总行应当恰好一行，实际 {len(summaries)}"

    parsed = SUMMARY_LINE.match(summaries[0])
    assert parsed, f"汇总行格式不符：{summaries[0]!r}"
    total = int(parsed["total"])
    hit = int(parsed["hit"])
    idle = int(parsed["idle"])
    unanswered = int(parsed["unanswered"])

    # N：profile 声明的规则条数（不是 6 类 rule_type）
    assert total == len(PROFILE.rules)
    # M：detect 命中的规则条数；K：声明了但没命中
    assert hit == len({event["rule_id"] for event in events})
    assert idle == total - hit
    # K'：问题数，与阶段 1 的三计数同源
    assert unanswered == passport["counts"]["unanswered"]


def test_log_lines_reconcile_with_changes(tmp_path: Path) -> None:
    """日志是 ``changes`` 的翻译：每个有变更的规则都有一行，且行数对得上产物。"""
    stdout, _events, _passport, changes = _run(tmp_path)
    by_label: dict[str, list[str]] = {}
    for line in _log_lines(stdout):
        by_label.setdefault(LOG_LINE.match(line)["header"][1:-1].strip(), []).append(line)

    for rule_id, group in changes.groupby("rule_id"):
        label = _label_of(str(rule_id))
        assert label in by_label, f"规则 {rule_id} 有 {len(group)} 条变更，但日志里没有对应行"

        # 数量按「行」计：同一行在多列命中只算一行（与模板口径一致）
        kept = group[group["change_type"] == "flagged_kept"]
        effective = group if kept.empty else group[group["change_type"] != "flagged_kept"]
        expected = effective["row_key"].nunique() if not effective.empty else kept["row_key"].nunique()
        assert any(ROWS_IN_LINE.search(line) and int(ROWS_IN_LINE.search(line).group(1)) == expected
                   for line in by_label[label]), (
            f"{label} 的日志行数与 changes 不一致（期望 {expected} 行）：{by_label[label]}"
        )


def test_log_does_not_expose_event_ids(tmp_path: Path) -> None:
    """审计标识留在产物里：终端出现 ``event_id`` 就是「用户可读」被审计需求污染。"""
    stdout, _events, _passport, _changes = _run(tmp_path)
    assert not [line for line in stdout if "event_id" in line]


def test_question_line_explains_question_vs_rule_gap(tmp_path: Path) -> None:
    """首行必须把「问题数」和「规则数」分开写。

    两者本来就不等（``missing_token`` 每列一题），只写「问题 6 个 | 规则 a, b, …」
    会让读者拿 6 去数下面 5 行日志。这里同时钉住两个数各自的来源。
    """
    stdout, events, passport, _changes = _run(tmp_path)
    lines = [line for line in stdout if line.startswith("问题 ")]
    assert len(lines) == 1, f"问题行应当恰好一行：{lines}"

    parsed = QUESTION_LINE.match(lines[0])
    assert parsed, f"问题行格式不符：{lines[0]!r}"
    # 问题数：与 passport 的三计数同源（fix + flag + unanswered）
    assert int(parsed["questions"]) == sum(passport["counts"].values())
    # 规则数：命中规则的去重 rule_id 数
    assert int(parsed["rules"]) == len({event["rule_id"] for event in events})
    assert parsed["names"].split(", ") == sorted({event["rule_id"] for event in events})
