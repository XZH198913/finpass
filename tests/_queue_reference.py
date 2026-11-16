"""4b 阶段 B：冻结 ``start()`` 的**问题队列**（``_detect → aggregate → sort`` 之后）。

与 ``_detect_reference.py`` 分工，两者不能互相替代：

- 阶段 A（`_detect_reference.py`）：``engine.detect`` 的 ``Gap`` vs 搬家前 ``_detect`` 的
  ``Question``，只记检测事实——Gap 顺序变了但聚合后队列相同，A 能抓到，B 抓不到；
- 阶段 B（本文件）：``_gap_to_question`` 聚合后的队列 vs 搬家前 ``start()`` 的队列，
  按 ``question_id`` 比——检测事实没变、但聚合/包装变了，B 能抓到，A 抓不到。

记录哪些字段（这些**正是** ``Gap`` 刻意不带的展示层/聚合层信息）：

- ``question_id``：聚合后的身份（``q_missing_列`` / ``q_dup_key`` / ``q_ohlc_主键`` …）；
- ``rule_id`` / ``level``：rule id 与聚合层级；
- ``options`` / ``default``：选项文案（4b 起由包装层从 profile 读）；
- ``affected_row_keys``：聚合后的受影响行（有序）；
- ``estimated_changes``：聚合累加值；
- ``sample_columns``（有序）+ ``samples_count``：样本列与条数，抓「样本列少了/顺序变了」。

参考由搬家前的 ``start()`` 生成（2026-11-05），4b 之后本脚本**只读**：没有 ``--write``。

理由与 ``_detect_reference.py`` 相同（4b 封版，方案 B）：现在重生只能拿当前实现写，
等于「自己跟自己比」。队列确实要变时（例如 4c 的 dispatch 换了聚合口径），那是改行为，
必须人工改 ``fixtures/queue_reference.json`` 并在提交说明里写理由。

用法（仓库根目录）：

    python tests/_queue_reference.py --check              # 唯一入口
    python tests/_queue_reference.py --check --profile futures
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from _fixture import CASES, REPO, Case

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "queue_reference.json"


def measure(case: Case) -> list[dict[str, Any]]:
    """跑一次 ``start()``（不施加任何决策），返回聚合后的队列清单。"""
    import finpass as fp

    session = fp.open(case.csv, profile=case.profile)
    session.start()
    return [
        {
            "question_id": question.question_id,
            "rule_id": question.rule_id,
            "level": question.aggregation_level,
            "options": list(question.options),
            "default": question.default,
            "affected_row_keys": [str(key) for key in question.affected_row_keys],
            "estimated_changes": question.estimated_changes,
            "sample_columns": list(question.samples[0]) if question.samples else [],
            "samples_count": len(question.samples),
        }
        for question in session.queue
    ]


def _parse(argv: list[str]) -> list[Case]:
    selector = "all"
    args = argv[1:]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--check":
            pass  # 只读模式下 --check 是默认行为，收下是为了让 `--check` 写法合法
        elif arg == "--write":
            raise SystemExit(
                "队列参考已冻结（4b 封版，方案 B）：本脚本只读，没有 --write。"
                "现在重生只能拿当前实现的 start() 写，等于自己跟自己比。"
                "队列确实要变时，那是改行为——请人工改 fixtures/queue_reference.json 并在提交说明里写理由。"
            )
        elif arg == "--profile":
            index += 1
            if index >= len(args):
                raise SystemExit("--profile 需要一个值：a_stock_daily / futures / all")
            selector = args[index]
        elif arg.startswith("--profile="):
            selector = arg.split("=", 1)[1]
        else:
            raise SystemExit(f"未知参数：{arg}（可用 --check / --profile）")
        index += 1

    if selector == "all":
        return list(CASES.values())
    if selector not in CASES:
        raise SystemExit(f"未知 profile：{selector}（可用：{', '.join(CASES)} / all）")
    return [CASES[selector]]


def main(argv: list[str]) -> int:
    cases = _parse(argv)
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    failed = 0
    for case in cases:
        current = measure(case)
        expected = payload.get(case.profile)
        if expected is None:
            print(f"[{case.profile}] 参考缺失：{FIXTURE.relative_to(REPO)}")
            failed = 1
        elif expected == current:
            print(f"[{case.profile}] 与队列参考一致（{len(expected)} 条）")
        else:
            print(f"[{case.profile}] 与队列参考不一致")
            failed = 1
    return failed
    return failed


if __name__ == "__main__":
    sys.exit(main(sys.argv))
