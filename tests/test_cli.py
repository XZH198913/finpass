"""05 验收：CLI 三条降级通道 + verify 退出码 + 失败路径的表述。

全部走 ``python -m finpass.cli`` 子进程，验证的是**用户真正拿到的东西**：
stdin 协议、yaml 回填格式、退出码。进程内调用只能证明函数能跑，证明不了这些。
唯一的例外是 ``_explain`` 的映射表——它是纯函数，逐类对拍比开 7 个子进程便宜得多。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pandas.errors import EmptyDataError, ParserError

from finpass.cli import EXIT_FAIL, _explain

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"


def _cli(*args: str, stdin: str | None = None, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8"}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "finpass.cli", *args],
        cwd=REPO,
        env=env,
        input=stdin,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=120,
    )


def _passport(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_interactive_exports_all_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "cli"
    # 队列顺序：duplicate / missing×2 / unit / date / ohlc；ohlc 选 2=修正（选 1 会打标 → 退出码 2）
    result = _cli(
        "run",
        str(DIRTY_CSV),
        "--out",
        str(out),
        "--interactive",
        "cli",
        stdin="1\n1\n1\nb\n1\n2\n",
    )

    assert result.returncode == 0, result.stderr
    names = sorted(path.name for path in out.iterdir())
    assert names == sorted(
        [
            "pingan_bank_daily_dirty.changes.parquet",
            "pingan_bank_daily_dirty.clean.csv",
            "pingan_bank_daily_dirty.diff.html",
            "pingan_bank_daily_dirty.events.jsonl",
            "pingan_bank_daily_dirty.passport.json",
            "pingan_bank_daily_dirty.report.after.json",
            "pingan_bank_daily_dirty.report.before.json",
        ]
    )

    passport = _passport(out / "pingan_bank_daily_dirty.passport.json")
    assert passport["human_decisions_count"] == 6
    assert passport["auto_only"] is False


def test_interactive_stops_cleanly_on_short_stdin(tmp_path: Path) -> None:
    """CI 里 stdin 不够时必须给出一句人话，而不是 EOFError 栈。"""
    result = _cli("run", str(DIRTY_CSV), "--out", str(tmp_path / "short"), "--interactive", "cli", stdin="1\n")

    assert result.returncode != 0
    assert "决策输入不足" in (result.stderr or result.stdout)


def test_run_auto_only_passport_is_marked(tmp_path: Path) -> None:
    out = tmp_path / "auto"
    result = _cli("run", str(DIRTY_CSV), "--out", str(out), "--auto-only")

    # auto_only 必有 flagged 项 → 软失败退出码 2，不是 0
    assert result.returncode == 2, result.stderr
    passport = _passport(out / "pingan_bank_daily_dirty.passport.json")
    assert passport["auto_only"] is True
    assert passport["human_decisions_count"] == 0
    assert passport["unresolved_issues_count"] > 0

    verified = _cli("verify", str(out / "pingan_bank_daily_dirty.passport.json"))
    assert verified.returncode == 2, verified.stdout
    assert "退出码 2" in verified.stdout


def test_questions_yaml_roundtrip(tmp_path: Path) -> None:
    questions = tmp_path / "questions.yaml"
    drafted = _cli("run", str(DIRTY_CSV), "--out", str(tmp_path / "draft"), "--questions", str(questions))
    # 阶段 1 起「未答」也计入软失败：草稿导出后 6 题都还没有决策，退出码是 2 而不是 0。
    # 这条不是「让测试通过」的妥协——draft 产物确实留下了 6 个未解决项。
    assert drafted.returncode == 2, drafted.stderr

    payload = yaml.safe_load(questions.read_text(encoding="utf-8"))
    assert payload["profile"] == "a_stock_daily"
    assert len(payload["questions"]) == 6
    assert all(item["answer"] is None for item in payload["questions"])

    fills = {
        "duplicate_key": {"action": "answer", "params": {}},
        "missing_token": {"action": "answer", "params": {}},
        "unit_ambiguity": {"action": "batch_apply", "params": {}},
        "date_format": {"action": "answer", "params": {}},
        "ohlc_contradiction": {"action": "answer", "params": {}},
    }
    for item in payload["questions"]:
        item["answer"] = {**fills[item["rule_id"]], "reason": "yaml 回填"}
    questions.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")

    out = tmp_path / "resumed"
    resumed = _cli("resume", str(questions), "--out", str(out))
    assert resumed.returncode == 0, resumed.stderr
    assert "回填 6 题" in resumed.stdout

    events = (out / "pingan_bank_daily_dirty.events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(events) == 6

    verified = _cli("verify", str(out / "pingan_bank_daily_dirty.passport.json"))
    assert verified.returncode == 0, verified.stdout


def test_verify_exit_code_reflects_tampering(tmp_path: Path) -> None:
    out = tmp_path / "tamper"
    assert _cli("run", str(DIRTY_CSV), "--out", str(out), "--auto-only").returncode == 2
    passport_path = out / "pingan_bank_daily_dirty.passport.json"

    clean = out / "pingan_bank_daily_dirty.clean.csv"
    original = clean.read_text(encoding="utf-8")
    clean.write_text(original.replace("10.0", "99.9", 1), encoding="utf-8")
    try:
        tampered = _cli("verify", str(passport_path))
    finally:
        clean.write_text(original, encoding="utf-8")

    assert tampered.returncode == 1
    assert "output_file_sha256 不一致" in tampered.stdout
    assert "verify 失败" in tampered.stdout

    # 还原后回到“有未解决项”的软失败态，而不是 0
    assert _cli("verify", str(passport_path)).returncode == 2


def test_help_lists_three_subcommands() -> None:
    result = _cli("--help")

    assert result.returncode == 0
    for command in ("run", "resume", "verify"):
        assert command in result.stdout


def test_lang_switch_selects_help_language() -> None:
    """--lang 优先于 FINPASS_LANG（conftest 钉成 zh），且 --help 自身也跟着变。"""
    zh = _cli("--lang", "zh", "--help")
    en = _cli("--lang=en", "--help")

    assert zh.returncode == en.returncode == 0
    assert "承诺决策可追溯" in zh.stdout
    assert "Decisions are traceable" in en.stdout
    assert "--lang" in en.stdout


def test_lang_switch_applies_to_run_and_replay_command(tmp_path: Path) -> None:
    out = tmp_path / "lang_zh"
    zh = _cli("--lang", "zh", "run", str(DIRTY_CSV), "--out", str(out))
    assert zh.returncode == 2, zh.stderr
    assert "[汇总]" in zh.stdout

    passport_path = out / f"{DIRTY_CSV.stem}.passport.json"
    passport = _passport(passport_path)
    assert "--lang zh" in passport["cli_replay_cmd"]

    # 子命令后写法同样生效；verify 的提示也按所选语言输出
    en = _cli("run", "--lang=en", str(DIRTY_CSV), "--out", str(tmp_path / "lang_en"))
    assert en.returncode == 2, en.stderr
    assert "[Summary]" in en.stdout
    verified = _cli("--lang", "en", "verify", str(passport_path))
    assert "verify passed" in verified.stdout


def test_finpass_lang_env_and_flag_precedence() -> None:
    env_zh = _cli("--help", env_extra={"FINPASS_LANG": "zh"})
    env_en = _cli("--help", env_extra={"FINPASS_LANG": "en"})
    assert "承诺决策可追溯" in env_zh.stdout
    assert "Decisions are traceable" in env_en.stdout

    # --lang 覆盖环境变量：中文环境下仍输出英文 help
    flag_wins = _cli("--lang", "en", "--help", env_extra={"FINPASS_LANG": "zh"})
    assert "Decisions are traceable" in flag_wins.stdout
    assert "承诺决策可追溯" not in flag_wins.stdout


# ---------------------------------------------------------------- 失败路径的表述
#
# 判据只有一条：**不懂代码的人看完这句就知道该改什么**。所以每个用例都同时钉住
# 「第一行认得出是哪类错」和「没有 traceback」——只钉退出码的话，把栈原样打出来也能过。

def _ns(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


@pytest.mark.parametrize(
    ("exc", "args", "expected"),
    [
        (FileNotFoundError(2, "No such file or directory", "nope.csv"), _ns(input="nope.csv"), "找不到文件：nope.csv"),
        (IsADirectoryError(13, "Permission denied", "data"), _ns(input="data"), "是个目录，不是文件"),
        (UnicodeDecodeError("utf-8", b"\xd1", 0, 1, "invalid start byte"), _ns(input="gbk.csv"), "不是 UTF-8 编码：gbk.csv"),
        (EmptyDataError("No columns to parse from file"), _ns(input="empty.csv"), "没有可读的表头"),
        (ParserError("Error tokenizing data"), _ns(input="ragged.csv"), "不是规整的 CSV：ragged.csv"),
        (ValueError("找不到 profile：nope（已查找 x/nope.yaml）"), _ns(profile="nope"), "找不到 profile：nope"),
        (ValueError("profile a_stock_daily 缺少必需列：成交额_元"), _ns(input="futures.csv"), "缺少必需列：成交额_元"),
        (yaml.YAMLError("bad indentation of a mapping entry"), _ns(), "yaml 解析失败"),
    ],
)
def test_explain_maps_each_failure_to_one_chinese_line(
    exc: Exception, args: argparse.Namespace, expected: str
) -> None:
    lines = _explain(exc, args)

    assert lines, f"{type(exc).__name__} 没有被映射"
    assert expected in lines[0]
    assert all("Traceback" not in line for line in lines)


def test_explain_leaves_unknown_errors_alone() -> None:
    """认不出的异常必须返回 None（照原样抛）——否则 bug 会被一句「参数有误」盖住。"""
    assert _explain(KeyError("内核崩了"), _ns(input="a.csv")) is None


def test_missing_input_exits_1_without_traceback(tmp_path: Path) -> None:
    result = _cli("run", str(tmp_path / "nope.csv"), "--out", str(tmp_path / "out"))

    assert result.returncode == EXIT_FAIL
    assert "找不到文件" in result.stderr
    assert "Traceback" not in result.stderr + result.stdout


def test_usage_error_exits_1_not_unresolved_2() -> None:
    """参数打错必须是 1。2 是「跑完了但有未解决项」，CI 会当软失败放行。"""
    result = _cli("run")  # 缺 input

    assert result.returncode == EXIT_FAIL
    assert "参数有误" in result.stderr


def test_debug_env_gives_back_the_traceback(tmp_path: Path) -> None:
    """报 bug 的人需要原始栈；``FINPASS_DEBUG=1`` 是那条出口。"""
    result = _cli("run", str(tmp_path / "nope.csv"), "--out", str(tmp_path / "out"), env_extra={"FINPASS_DEBUG": "1"})

    assert result.returncode == EXIT_FAIL
    assert "Traceback" in result.stderr
