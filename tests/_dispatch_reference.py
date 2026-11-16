"""4c 阶段 0：冻结「决策 → 变更」的中间态，即 ``dispatch`` 的期望产物。

为什么必须单独冻结（4c 是拆分不是搬家）：

- 4b 是搬家，参考 = 最终产物（``Gap`` / 队列 / 14 产物）就够；
- 4c 把一个 ``_fix_*`` 拆成「dispatch 算出要改成什么」+「fix 把值写进去」。若只冻结
  ``clean.csv``，dispatch 可能算错目标值、再由 fix 顺手修正——最终产物仍然对，
  错误会推迟到 5（reverify 拿 dispatch 的 changes 判断残差）或 7a（patch 按 changes 重放）
  才爆。所以这里冻结的是**中间态**：每条决策应由 dispatch 产出哪些变更。

记录什么（每条决策一条）：

- ``question_id`` / ``rule_id``：决策单元的身份（与 ``queue_reference.json`` 对齐）；
- ``action`` / ``params``：决策输入（``skip`` / ``batch_apply`` / 逐题覆盖）；
- ``changes``：该决策产出的变更，逐条记 ``row_key`` / ``col`` / ``before`` / ``after`` /
  ``change_type``。

**不记**：``decision_id``（uuid4，与逻辑无关）、任何最终 ``df`` 状态（那是 ``clean.csv`` 的事）。

口径说明：

- 变更顺序是语义（事件流 → diff 锚点），按列表逐条比，不排序；
- ``before`` / ``after`` 已经是落盘形态（字符串或 ``None``），与 ``changes.parquet`` 一致；
- ``dropped_row`` 的 ``before`` 是整行 JSON——删行不写「改成什么」，所以它是 dispatch 唯一
  「目标值 = 行集合」的规则：具体删哪些由 ``duplicate_keep`` 决定，见追认文档 §十一。

参考由 **4c 之前**的 ``_fix_*`` 生成（2026-11-05）；4c 之后本脚本**只读**，没有 ``--write``。

用法（仓库根目录）：

    python tests/_dispatch_reference.py --check
    python tests/_dispatch_reference.py --check --profile futures
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from _fixture import CASES, REPO, Case

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dispatch_reference.json"

#: 变更里参与判据的字段。``decision_id`` 是 uuid4，故意不进来。
CHANGE_KEYS = ("row_key", "col", "before", "after", "change_type")


def measure(case: Case) -> list[dict[str, Any]]:
    """按固定决策集逐题调 ``engine.dispatch``，记录它产出的变更。

    **不经 ``session.answer``**（4c Step A）：``answer`` 走的是同一条路，
    拿它当参考等于「实现自己跟自己比」——4b 那次参考对象错位就是这么来的。
    这里只借 session 组装输入（``df`` / ``target_state`` / ``policy`` / 每题的差距），
    产出 changes 的必须是被验收的 ``engine.dispatch``。
    """
    import finpass as fp
    from finpass.engine import dispatch, fix

    session = fp.open(case.csv, profile=case.profile)
    session.start()
    state = session._detect_state()
    policy = dict(session.profile_obj.policy)
    records: list[dict[str, Any]] = []
    while (question := session.next()) is not None:
        action, params = case.decisions[question.rule_id]
        result = dispatch(
            session.df,
            session._units[question.question_id],
            {"decision_id": "dispatch-reference", "action": action, "params": params},
            state,
            policy,
        )
        session.df = fix(session.df, result.changes, state)
        session._answered.add(question.question_id)
        session.changes.extend(result.changes)
        records.append(
            {
                "question_id": question.question_id,
                "rule_id": question.rule_id,
                "action": action,
                "params": params,
                "changes": [
                    {key: change[key] for key in CHANGE_KEYS}
                    for change in result.changes
                ],
            }
        )
    return records


def comparison(records: list[dict[str, Any]]) -> list[Any]:
    """比对口径：整条结构逐字比，``params`` 归一成排好序的键值对。"""
    return [
        (
            record["question_id"],
            record["rule_id"],
            record["action"],
            tuple(sorted(record["params"].items())),
            tuple(
                tuple(change[key] for key in CHANGE_KEYS)
                for change in record["changes"]
            ),
        )
        for record in records
    ]


def _diff(expected: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[str]:
    """点名第一处不一致：哪条决策、哪个字段。"""
    lines: list[str] = []
    if len(expected) != len(current):
        lines.append(f"决策条数：参考 {len(expected)}，实际 {len(current)}")
    for index, (want, got) in enumerate(zip(expected, current)):
        if comparison([want]) == comparison([got]):
            continue
        if want["question_id"] != got["question_id"]:
            lines.append(f"#{index}.question_id：参考 {want['question_id']}，实际 {got['question_id']}")
            continue
        want_changes, got_changes = want["changes"], got["changes"]
        if len(want_changes) != len(got_changes):
            lines.append(
                f"#{index}（{want['question_id']}）变更条数：参考 {len(want_changes)}，实际 {len(got_changes)}"
            )
            continue
        for position, (left, right) in enumerate(zip(want_changes, got_changes)):
            if left != right:
                lines.append(f"#{index}（{want['question_id']}）第 {position} 条变更：参考 {left}，实际 {right}")
                break
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
            pass  # 只读模式下 --check 是默认行为
        elif arg == "--write":
            raise SystemExit(
                "dispatch 参考已冻结（4c 封版纪律，方案 B）：本脚本只读。"
                "决策→变更的口径真变了，那是改行为——请人工改 fixtures/dispatch_reference.json 并写理由。"
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
        elif comparison(expected) == comparison(current):
            print(f"[{case.profile}] 与 dispatch 参考一致（{len(expected)} 条决策）")
        else:
            print(f"[{case.profile}] 与 dispatch 参考不一致")
            for line in _diff(expected, current):
                print(f"  {line}")
            failed = 1
    return failed


if __name__ == "__main__":
    sys.exit(main(sys.argv))
