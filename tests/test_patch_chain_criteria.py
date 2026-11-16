"""判据 3 的落地证据：patch 链四条检查**先于实现存在**。

阶段 0 只要求「检查占位在」，不要求可跑——四条的真实实现在 7a。
因此这里全部 ``strict=True`` 的 xfail：一旦 7a 把它们改成真断言而忘了摘标记，
pytest 会因 xpass 报错，不会悄悄留在 xfail 名单里。
"""

from __future__ import annotations

import pytest

REASON = "7a 实现"


@pytest.mark.xfail(reason=REASON, strict=True)
def test_chain_is_traceable() -> None:
    """可追溯：每代护照能沿 parent 指针走回首个 run。"""
    raise NotImplementedError(f"{REASON}：链可追溯检查")


@pytest.mark.xfail(reason=REASON, strict=True)
def test_parent_tampering_detected() -> None:
    """父代被篡改：``parent_clean_sha256`` 与父代实际产物字节不一致时报错。"""
    raise NotImplementedError(f"{REASON}：父代篡改检查")


@pytest.mark.xfail(reason=REASON, strict=True)
def test_broken_chain_detected() -> None:
    """断链：父代产物缺失时报错，不降级为 warn。"""
    raise NotImplementedError(f"{REASON}：断链检查")


@pytest.mark.xfail(reason=REASON, strict=True)
def test_cycle_or_generation_skip_detected() -> None:
    """环或越代：父代自身在后代链上、或跳代引用时报错。"""
    raise NotImplementedError(f"{REASON}：环/越代检查")
