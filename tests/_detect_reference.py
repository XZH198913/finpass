"""4b 阶段 A 参考：冻结**搬家前** ``session._detect``（aggregate 之前）的检测产出。

为什么不冻结 ``start()`` 的队列：那个队列是 ``_detect → aggregate`` 之后的产物，
拿它当参考会把「聚合逻辑」混进「检测搬家」的验收——聚合变了但最终队列相同时，
参考对比会绿，问题留到 4c 才爆。所以这里只记 ``_detect`` 的原始产出。

参考由搬家前的 ``_detect`` 生成（2026-11-05），4b 之后由 ``engine.detect`` 复现。
``_detect`` 已删除，所以本脚本**只读**：没有 ``--write``，JSON 永不重生。

为什么不留重生入口（4b 封版决定，方案 B）：

- 现在重生只能用 ``engine.detect`` 写，那就是「engine 跟自己比」，判据失去意义；
- 若将来检测判据确实要变，那是**改行为**，必须单独说明并同提交改
  ``fixtures/detect_reference.json``（人工审 diff），而不是跑一条命令把红刷绿；
- 传 ``--write`` 会明确报错并指回本段，避免有人以为「跑一下就好」。

记录哪些字段（与 ``Gap`` 对应，不记展示层）：

- ``rule_id`` → ``Gap.rule_id``（profile 声明的 id，不是 type：``unit_ambiguity`` / ``limit_up_down``）；
- ``targets`` → ``Gap.targets``：判据涉及的列（duplicate 是主键列）；
- ``rows``：命中行的 row_key，**按数据行序**（JSON 里保留顺序）；
  比对时按集合比：``duplicate_key`` 的两条重复行在检测层都会报出来，
  「去重」是包装层的职责（见 ``spec-stage4-追认.md`` 第五节），所以参考里的去重结果
  不能拿去逐字比对检测层；行序由阶段 B 的 ``affected_row_keys`` 守。
- ``sample_columns``：样本列（有序），用于发现「样本列少了/多了」。

**不记**：``question_id``（聚合产物）、``options`` / ``default``（展示层，4b 由包装层从 profile 读）、
``aggregation_level``（聚合决策）。

阶段 B（聚合之后的队列）见 ``_queue_reference.py``：A 抓不到「聚合变了」，
B 抓不到「Gap 顺序变了但聚合后队列相同」，两个都要跑。

用法（仓库根目录）：

    python tests/_detect_reference.py --check              # 唯一入口
    python tests/_detect_reference.py --check --profile futures
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from _fixture import CASES, REPO, Case

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "detect_reference.json"


def measure(case: Case) -> list[dict[str, Any]]:
    """跑一次 ``engine.detect``（聚合之前），返回与 ``Gap`` 对应的检测清单。"""
    import finpass as fp
    from finpass.engine import detect
    from finpass.passport import row_keys

    session = fp.open(case.csv, profile=case.profile)
    gaps = detect(session.df, session._detect_state(), dict(session.profile_obj.policy))
    records: list[dict[str, Any]] = []
    for gap in gaps:
        keys = row_keys(pd.DataFrame(gap.rows), session.keys) if gap.rows else []
        records.append(
            {
                "rule_id": gap.rule_id,
                "targets": list(gap.targets),
                "rows": [str(key) for key in keys],
                "sample_columns": sorted(gap.rows[0]) if gap.rows else [],
            }
        )
    return records


def comparable(records: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """比对口径：``rows`` 按集合比（去重是包装层的事），其余字段逐字比且保持顺序。"""
    return [
        (
            record["rule_id"],
            tuple(record["targets"]),
            frozenset(record["rows"]),
            tuple(record["sample_columns"]),
        )
        for record in records
    ]


def _diff(expected: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[str]:
    """按顺序逐条比：条数不同或某条不同都点名，最多打印 3 处。"""
    lines: list[str] = []
    if len(expected) != len(current):
        lines.append(f"条数：参考 {len(expected)}，实际 {len(current)}")
    for index, (want, got) in enumerate(zip(expected, current)):
        if comparable([want]) == comparable([got]):
            continue
        for field in ("rule_id", "targets", "rows", "sample_columns"):
            want_value = frozenset(want[field]) if field == "rows" else want.get(field)
            got_value = frozenset(got[field]) if field == "rows" else got.get(field)
            if want_value != got_value:
                lines.append(f"#{index}.{field}：参考 {want_value!r}，实际 {got_value!r}")
        if len(lines) >= 3:
            break
    return lines[:3]


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
                "检测参考已冻结（4b 封版，方案 B）：本脚本只读，没有 --write。"
                "现在重生只能拿 engine.detect 写参考，等于 engine 跟自己比。"
                "检测判据确实要变时，那是改行为——请人工改 fixtures/detect_reference.json 并在提交说明里写理由。"
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
        elif comparable(expected) == comparable(current):
            print(f"[{case.profile}] 与检测参考一致（{len(expected)} 条）")
        else:
            print(f"[{case.profile}] 与检测参考不一致")
            for line in _diff(expected, current):
                print(f"  {line}")
            failed = 1
    return failed


if __name__ == "__main__":
    sys.exit(main(sys.argv))
