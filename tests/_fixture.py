"""重构判据：一次会话的 7 个产物，按**文件字节**取指纹。

判据只有一条：同一决策集跑出来的产物，规范化后逐字节相同。

规范化只抹掉天生随机、与逻辑无关的字段：

- ``events.jsonl``：``event_id``（uuid4）、``ts``（当前时间）；
- ``changes.parquet``：``decision_id``（uuid4）；
- ``passport.json``：``input_path`` / ``cli_replay_cmd``（绝对路径与输出目录）；
- ``diff.html``：单元格上的 ``data-decision-id``（uuid4 十六进制）。

``clean.csv`` / ``report.before.json`` / ``report.after.json`` 不做任何规范化，
直接取原始字节 —— 它们本来就该逐字节确定。

刻意**不**引入的第二套算法：不重算内存哈希（``output_hash`` 那套已从护照移除），也不做数值容差比较。
重构前后要证明的是「产物没变」，不是「数据算起来一样」。

用法（在仓库根目录）：

    python tests/_fixture.py --write                     # 重生两个档的基线（仅在语义确实要变时）
    python tests/_fixture.py --check                     # 自检：两个档是否仍与基线一致
    python tests/_fixture.py --check --profile futures   # 只查期货档

**两个 fixture 的守则相同**：4b 起任何 fixture 变红，先修实现（``_gap_to_question`` 的合并方式、
``engine.detect`` 的粒度），**不重生基线**；确要重生，必须在这里留一行「为什么这个变化是
行为不变下的合法字节变化」。这条写在这里而不是追认文档里，因为这里才是会被反复读的地方。

为什么有期货档（不止为守 4b）：两个档的主键形态（单列 / 复合）、OHLC 关系、规则集合都不同，
后续阶段 5（phase）、7a（patch）、7b（范式对齐）都会碰产物结构，只守股票档等于每阶段只有
一半证据。期货档从 4b 起与股票档同级守护。

基线变更记录（重生基线必须在这里留一行理由）：

- 2026-11-04 ``diff.html``：修复主键列被改写（日期归一）时锚点丢失 —— ``diff.py``
  的 lookup 增加按 ``after`` 登记的别名。其余 6 个产物字节未变。
- 2026-11-04 ``passport.json``：护照移除 ``output_hash``（内存指纹），只保留
  ``output_file_sha256``，``verify`` 不再读它。其余 6 个产物字节未变。
- 2026-11-05 ``passport.json``：阶段 1 新增 ``passport_id`` / ``dry_run`` / ``counts`` 三个字段；
  规范化把 ``passport_id`` 抹成 ``<passport_id>``（含时间戳与 uuid 片段）。其余 6 个产物字节未变。
- 2026-11-05 ``events.jsonl``：阶段 3 把换算系数收进 profile 的 ``target_state.unit_scale.factor``，
  决策集里的 ``{"scale": 0.0001}`` 随之删除，事件记录的 ``params`` 因此为空。
  迁移前后 clean.csv / changes / reports / diff 逐字节相同 —— 变的是「记了什么决策」，
  不是「数据怎么改」。
- 2026-11-05 ``passport.json``：Commit 4 新增 ``profile_schema_version``（取加载器常量，
  不是 profile 声明的 ``version``）。它是确定值，不需要规范化；其余 6 个产物字节未变。
- 2026-11-05 ``passport.json``：schema 升到 ``2.2.0``（角色从顶层 ``columns`` 搬到
  ``target_state[*].role``），护照里记录的 ``profile_schema_version`` 随之从 2.1.0 变 2.2.0。
  **只是这一个字段的值变了**：clean / changes / events / 两份 report / diff 全部逐字节相同，
  说明角色搬家没有改变任何检测或修复结果。
- 2026-11-05 新增 ``futures.expected.json``（首版，无「变红」可比）：期货档 5 条规则各 1 行的
  首份基线，为 4b 起提供「第二档」守护。决策集见 ``DECISIONS_FUTURES``。
- 2026-11-05 两档基线统一格式：``demo.expected.json`` 补 ``profile`` 键（两档都由统一入口
  ``--write --profile`` 生成）。**7 个产物指纹一字未变**，只是基线元数据补齐。
- 2026-11-05 两档 ``passport.json``：schema 升到 ``2.3.0``（主键从顶层 ``key:`` 搬到
  ``target_state[*].role: key``）。护照里的 ``keys`` 列表与其余字段一字未变，唯一变化是
  ``profile_schema_version``；两档共 12 个数据产物（clean / changes / events / 两份 report /
  diff）逐字节相同，说明主键搬家没有改变行定位。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

import finpass as fp  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

# 决策集是 fixture 的一部分：改这里等于改判据，必须同时说明理由。
DECISIONS: dict[str, tuple[str, dict[str, Any]]] = {
    "missing_token": ("answer", {}),
    "duplicate_key": ("answer", {}),
    "date_format": ("answer", {}),
    # 阶段 3：换算系数来自 profile 的 target_state，决策集里不再带 scale
    "unit_ambiguity": ("batch_apply", {}),
    "ohlc_contradiction": ("skip", {}),
}

# 期货档：规则集合不同（limit_up_down 取代 unit_ambiguity），主键是复合主键。
# ``ohlc_contradiction`` 同样取 skip —— 与股票档保持「同一类规则用同一类决策」，
# 这样两档产物的差异只来自域，不来自决策风格。
DECISIONS_FUTURES: dict[str, tuple[str, dict[str, Any]]] = {
    "missing_token": ("answer", {}),
    "duplicate_key": ("answer", {}),
    "date_format": ("answer", {}),
    "limit_up_down": ("batch_apply", {}),
    "ohlc_contradiction": ("skip", {}),
}

ARTIFACTS = (
    "clean.csv",
    "changes.parquet",
    "events.jsonl",
    "passport.json",
    "report.before.json",
    "report.after.json",
    "diff.html",
)

_UUID_HEX = re.compile(r"\b[0-9a-f]{32}\b")


@dataclass(frozen=True)
class Case:
    """一份 fixture 的完整定义：跑哪份数据、用哪组决策、写到哪个文件。"""

    profile: str
    csv: Path
    fixture: Path
    decisions: dict[str, tuple[str, dict[str, Any]]]

    @property
    def stem(self) -> str:
        return self.csv.stem


CASES: dict[str, Case] = {
    "a_stock_daily": Case(
        profile="a_stock_daily",
        csv=REPO / "examples" / "pingan_bank_daily_dirty.csv",
        fixture=FIXTURE_DIR / "demo.expected.json",
        decisions=DECISIONS,
    ),
    "futures": Case(
        profile="futures",
        csv=REPO / "examples" / "futures_daily.csv",
        fixture=FIXTURE_DIR / "futures.expected.json",
        decisions=DECISIONS_FUTURES,
    ),
}


def run_demo(case: Case, out_dir: Path, decisions: dict[str, Any] | None = None) -> dict[str, str]:
    """按固定决策集跑完一遍会话并导出到 ``out_dir``。"""
    plan = decisions or case.decisions
    session = fp.open(case.csv, profile=case.profile)
    session.start()
    while (question := session.next()) is not None:
        action, params = plan[question.rule_id]
        session.answer(question.question_id, action, params, "fixture")
    return session.export(str(out_dir))


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fingerprint_one(name: str, path: Path) -> str:
    if name == "passport.json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        for volatile in ("input_path", "cli_replay_cmd"):
            payload.pop(volatile, None)
        # 阶段 1：passport_id 含运行时间戳与 uuid4 片段。抹成占位而不是删键，
        # 这样「字段还在」这件事仍被基线覆盖；dry_run / counts 是确定值，原样参与指纹。
        payload["passport_id"] = "<passport_id>"
        return _digest(_canonical_json(payload))

    if name == "changes.parquet":
        records = pd.read_parquet(path).to_dict("records")
        for record in records:
            record["decision_id"] = "<decision>"
        return _digest(_canonical_json(records))

    if name == "events.jsonl":
        events = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for event in events:
            event["event_id"] = "<event>"
            event["ts"] = "<ts>"
        return _digest(_canonical_json(events))

    if name == "diff.html":
        return _digest(_UUID_HEX.sub("<id>", path.read_text(encoding="utf-8")).encode("utf-8"))

    return _digest(path.read_bytes())


def fingerprint(case: Case, out_dir: Path) -> dict[str, str]:
    """7 个产物的规范指纹；缺文件直接报错，不静默跳过。"""
    result: dict[str, str] = {}
    for name in ARTIFACTS:
        path = out_dir / f"{case.stem}.{name}"
        if not path.is_file():
            raise FileNotFoundError(f"产物缺失：{path.name}")
        result[name] = _fingerprint_one(name, path)
    return result


def measure(case: Case) -> dict[str, Any]:
    """在临时目录跑一遍，返回完整基线内容（不落盘）。"""
    with tempfile.TemporaryDirectory(prefix=f"finpass-fixture-{case.profile}-") as tmp:
        run_demo(case, Path(tmp))
        artifacts = fingerprint(case, Path(tmp))
    return {
        "note": "会话产物指纹基线：重构/迁移后必须逐字节一致；随机字段已规范化。",
        "profile": case.profile,
        "input": case.csv.relative_to(REPO).as_posix(),
        "decisions": {rule: list(value) for rule, value in case.decisions.items()},
        "artifacts": artifacts,
    }


def _parse(argv: list[str]) -> tuple[str, list[Case]]:
    mode = "--check"
    selector = "all"
    args = argv[1:]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("--write", "--check"):
            mode = arg
        elif arg == "--profile":
            index += 1
            if index >= len(args):
                raise SystemExit("--profile 需要一个值：a_stock_daily / futures / all")
            selector = args[index]
        elif arg.startswith("--profile="):
            selector = arg.split("=", 1)[1]
        else:
            raise SystemExit(f"未知参数：{arg}（可用 --write / --check / --profile）")
        index += 1

    if selector == "all":
        return mode, list(CASES.values())
    if selector not in CASES:
        raise SystemExit(f"未知 profile：{selector}（可用：{', '.join(CASES)} / all）")
    return mode, [CASES[selector]]


def main(argv: list[str]) -> int:
    mode, cases = _parse(argv)
    failed = 0
    for case in cases:
        current = measure(case)
        if mode == "--write":
            case.fixture.parent.mkdir(parents=True, exist_ok=True)
            case.fixture.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[{case.profile}] 已写入 {case.fixture.relative_to(REPO)}")
            for name, digest in current["artifacts"].items():
                print(f"  {name:22} {digest[:16]}")
            continue

        expected = json.loads(case.fixture.read_text(encoding="utf-8"))
        diffs = [
            name
            for name, digest in expected["artifacts"].items()
            if current["artifacts"].get(name) != digest
        ]
        if diffs:
            print(f"[{case.profile}] 与基线不一致：" + ", ".join(diffs))
            failed = 1
        else:
            print(f"[{case.profile}] 与基线一致（{len(expected['artifacts'])} 个产物）")
    return failed


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
