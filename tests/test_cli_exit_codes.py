"""退出码三态守护：0 = 全清 / 2 = 有未解决项 / 1 = 校验失败。

必须走真实进程（``python -m finpass``）—— 进程内调用 ``main()`` 测不到"返回码被谁吞掉"，
而 CI 只认进程退出码。

**反直觉事实（阶段 1 记录）**：``cli._exit_code_of`` 去掉 ``auto_only`` 触发条件后，
`test_verify_unresolved_soft_fail` 仍然通过——因为 ``auto_only`` 通道下非 auto 规则走
``_skip`` → ``flagged_kept`` → ``unresolved_issues_count > 0``，flag 那个触发源把结论顶住了。
**两条触发源是双保险，不是冗余**：``unresolved_issues_count`` 是变更行数，
``counts.unanswered`` 是问题数。若将来让 ``flagged_kept`` 不计入 ``unresolved_issues_count``，
这里必须重新确认退出码，别以为"反正有 flag 兜底"。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIRTY_CSV = REPO / "examples" / "pingan_bank_daily_dirty.csv"


def _finpass(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "finpass", *args],
        cwd=REPO,
        env=env,
        input=stdin,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=120,
    )


def test_verify_ok(tmp_path: Path) -> None:
    """全部修完、无打标 → 0。"""
    out = tmp_path / "ok"
    run = _finpass("run", str(DIRTY_CSV), "--out", str(out), "--interactive", "cli", stdin="1\n1\n1\nb\n1\n2\n")
    assert run.returncode == 0, run.stderr

    verified = _finpass("verify", str(out / "pingan_bank_daily_dirty.passport.json"))
    assert verified.returncode == 0, verified.stdout
    assert "verify 通过" in verified.stdout


def test_verify_unresolved_soft_fail(tmp_path: Path) -> None:
    """auto_only 必有 flagged → 2（软失败，CI 可继续）。"""
    out = tmp_path / "unresolved"
    run = _finpass("run", str(DIRTY_CSV), "--out", str(out), "--auto-only")
    assert run.returncode == 2, run.stderr

    passport = json.loads((out / "pingan_bank_daily_dirty.passport.json").read_text(encoding="utf-8"))
    assert passport["auto_only"] is True
    assert passport["unresolved_issues_count"] > 0

    verified = _finpass("verify", str(out / "pingan_bank_daily_dirty.passport.json"))
    assert verified.returncode == 2, verified.stdout


def test_verify_tampered_fails_hard(tmp_path: Path) -> None:
    """篡改 clean.csv → 1（硬失败）。"""
    out = tmp_path / "tampered"
    assert _finpass("run", str(DIRTY_CSV), "--out", str(out), "--auto-only").returncode == 2

    clean = out / "pingan_bank_daily_dirty.clean.csv"
    original = clean.read_text(encoding="utf-8")
    clean.write_text(original.replace("10.0", "99.9", 1), encoding="utf-8")
    try:
        verified = _finpass("verify", str(out / "pingan_bank_daily_dirty.passport.json"))
    finally:
        clean.write_text(original, encoding="utf-8")

    assert verified.returncode == 1, verified.stdout
    assert "output_file_sha256 不一致" in verified.stdout
