"""质量护照与产物读写：events.jsonl / changes.parquet / passport.json / 哈希校验。

术语约定（03 起固定，勿再混用）：
- ``unresolved_issues_count``：``flagged_kept`` 的**单元格**条数，写入护照；
- ``session.unanswered``：因会话上限而未作答的**问题**条数，不写入护照（留给 05）。

校验只认一条判据：``clean.csv`` 的**文件字节 sha256** + ``replay`` 重放比对。

护照里只有 ``output_file_sha256``，**没有** ``output_hash``：内存指纹既无法从文件重算，
又会与文件哈希形成两套判据，已于 2026-11 移除。旧护照里残留的 ``output_hash`` 一律忽略；
缺 ``output_file_sha256`` 的旧护照只告警，不退回内存哈希。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .models import key_columns, row_keys
from .passport_compat import FIELD_COMPAT

CHANGE_COLUMNS = ["row_key", "col", "before", "after", "change_type", "rule_id", "decision_id"]

# 退出码三态（CI 语义，不许合并成 0/1）：
# 0 = 全部解决；2 = 有未解决/打标/降级（软失败，不 fail build）；1 = 产物损坏或校验失败
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_UNRESOLVED = 2


def _as_dict(item: Any) -> dict[str, Any]:
    """兼容 dict 与 dataclass 两种载体。"""
    if isinstance(item, dict):
        return dict(item)
    return dataclasses.asdict(item)


def _clean(value: Any) -> Any:
    """parquet 往返后的 NaN / None 统一成 None。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _same(current: Any, expected: Any) -> bool:
    """比较 before 与现场值：先比 str，再退化为数值比较。

    退化是必须的：CSV 往返会把 12000000.0 读回 int64，str 不等但数值相同。
    """
    if current is None or expected is None:
        return _clean(current) is None and _clean(expected) is None
    try:
        if pd.isna(current) and pd.isna(expected):
            return True
    except (TypeError, ValueError):
        pass
    if str(current) == str(expected):
        return True
    try:
        left, right = float(str(current)), float(str(expected))
    except (TypeError, ValueError):
        return False
    # CSV 只保留 repr 位数的十进制，replay 走的是浮点乘法（换算系数相乘会带出精度尾差），
    # 因此数值比较必须带相对容差，否则跨「重算」与「读回」两个世界永远不相等。
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)


def _key_columns(key: str | list[str]) -> list[str]:
    """主键统一成列名列表；实现只在 ``models.key_columns`` 一处（4c 起）。"""
    return key_columns(key)


def write_events(events: list[Any], out_path: str | Path) -> Path:
    """把决策事件流写成 jsonl（一行一个事件）。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(_as_dict(event), ensure_ascii=False, default=str) + "\n")
    return path


def write_changes(changes: list[Any], out_path: str | Path) -> Path:
    """把单元格变更写成 parquet（允许 0 行，但列必须齐全）。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([_as_dict(change) for change in changes], columns=CHANGE_COLUMNS)
    frame.to_parquet(path, index=False)
    return path


def write_passport(passport: Any, out_path: str | Path) -> Path:
    """把护照写成 json；字段名与 01 / 00 保持一字不差。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _as_dict(passport)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def read_passport(passport_path: str | Path) -> dict[str, Any]:
    """读回护照，供 replay / verify 使用。"""
    return json.loads(Path(passport_path).read_text(encoding="utf-8"))


def read_changes(changes_path: str | Path) -> list[dict[str, Any]]:
    """读回变更记录，缺失值统一成 None。"""
    frame = pd.read_parquet(changes_path)
    return [{key: _clean(value) for key, value in record.items()} for record in frame.to_dict("records")]


def replay_changes(
    df_input: Any,
    changes: list[Any],
    key: str | list[str] = "日期",
    dtypes: dict[str, str] | None = None,
) -> Any:
    """由原始数据 + 变更记录重建最终数据。

    - ``flagged_kept`` 不改值，跳过；
    - ``dropped_row`` 删该主键的末次出现（keep="first" 的镜像）；
    - 其余按 (row_key, col) 定位，写前先校验 ``before``，不一致直接 AssertionError。

    ``key`` 可以是单列名或列名列表；复合主键按 ``|`` 连接成 row_key 字符串，
    与 session 里的 ``_row_keys()`` 同一形态，diff 与 replay 才对得上。

    ``dtypes`` 是**列类型通道**：changes 只记「值」，不记「这一列被转成了数值」，
    像 ``成交额_元`` 从 ``str`` 变 ``float64`` 这类列级转换在 changes 里没有痕迹。
    传 ``dtypes``（通常取 ``report.after.json`` 的 ``dtypes`` 字段）后按目标 schema
    还原列类型；不传则只保证值一致，dtype 可能与导出时不同。
    """
    df = df_input.copy()
    keys = _key_columns(key)
    for change in changes:
        change = _as_dict(change)
        change_type = change["change_type"]
        col = change["col"]

        if change_type == "flagged_kept":
            continue
        if change_type == "dropped_col":
            if col in df.columns:
                df = df.drop(columns=[col])
            continue
        if change_type == "dropped_row":
            idx = df.index[row_keys(df, keys) == str(change["row_key"])]
            if len(idx) == 0:
                raise AssertionError(f"replay 找不到被删主键：{change['row_key']}")
            df = df.drop(idx[-1])
            continue

        # 优先按 row_key 定位（新产物改主键时记的是旧主键）；
        # 00/01/02 的旧产物记的是新主键，单主键时退回 before 里的旧值。
        target = change["row_key"]
        idx = df.index[row_keys(df, keys) == str(target)]
        if len(idx) == 0 and col in keys and len(keys) == 1 and change.get("before") is not None:
            target = change["before"]
            idx = df.index[row_keys(df, keys) == str(target)]
        if len(idx) == 0:
            raise AssertionError(f"replay 找不到主键：{target}")
        current = df.at[idx[0], col]
        if not _same(current, change.get("before")):
            raise AssertionError(
                f"before 不一致：{change['row_key']}.{col} 期望 {change.get('before')!r} 实际 {current!r}"
            )

        after = change.get("after")
        new_value = np.nan if after is None else after
        try:
            df.at[idx[0], col] = new_value
        except TypeError:
            # pandas 3 的 str / int64 列不接受跨类型赋值，先放宽 dtype
            df[col] = df[col].astype("float64" if isinstance(new_value, (int, float)) else object)
            df.at[idx[0], col] = new_value

    df = df.reset_index(drop=True)
    if dtypes:
        df = cast_dtypes(df, dtypes)
    return df


def cast_dtypes(df: Any, dtypes: dict[str, str]) -> Any:
    """按报告里的 dtype 字符串还原列类型；不认识的类型原样保留。"""
    for col, dtype in dtypes.items():
        if col not in df.columns:
            continue
        try:
            df[col] = df[col].astype(dtype)
        except (TypeError, ValueError):
            continue
    return df


def sha256_file(path: str | Path) -> str:
    """文件字节的 sha256。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_hash(clean_path: str | Path, passport_path: str | Path) -> bool:
    """校验 clean 文件与护照是否配套（按文件字节哈希）。

    护照里没有 ``output_file_sha256``（00/01/02 的旧护照）时返回 False，表示无法确认。
    """
    expected = read_passport(passport_path).get("output_file_sha256")
    if not expected:
        return False
    return sha256_file(clean_path) == expected


def _numeric_close(left_text: np.ndarray, right_text: np.ndarray) -> np.ndarray:
    """对**字符串形态**的取值做 ``_same`` 的数值兜底：两侧都能 ``float()`` 且 ``isclose`` 才算相等。

    先走 ``to_numeric`` 批量解析，再把解析失败的格子退回逐格 ``float()``——两者接受集不同
    （``float("1_000")`` 能过，``to_numeric`` 不认下划线分隔），只靠前者会让向量化版比
    ``_same`` 判得更紧，两个实现就漂移了。
    """
    left_num = np.asarray(pd.to_numeric(pd.Series(left_text), errors="coerce"), dtype="float64")
    right_num = np.asarray(pd.to_numeric(pd.Series(right_text), errors="coerce"), dtype="float64")
    # NaN（解析失败）在 isclose 里恒为 False；先算一遍，再把解析失败的格子逐个重判
    close = np.isclose(left_num, right_num, rtol=1e-9, atol=1e-12)
    for position in np.flatnonzero(np.isnan(left_num) | np.isnan(right_num)):
        try:
            close[position] = math.isclose(
                float(left_text[position]), float(right_text[position]), rel_tol=1e-9, abs_tol=1e-12
            )
        except (TypeError, ValueError):
            close[position] = False
    return close


def _column_equal(left: Any, right: Any) -> np.ndarray:
    """按 ``_same`` 的语义**逐格向量化**比较两列（按位置对齐），返回 bool 数组。

    这是 ``_same`` 的第二份实现，两者必须逐格一致（``tests/test_verify_frame_diff_equivalence.py``
    里有对撞测试守着）。规则照 ``_same`` 的分支抄：

    1. 任一侧是 ``None``（Python ``None``，不是 NaN）→ 只有两侧都缺失才算相等；
    2. 两侧都缺失 → 相等；
    3. 否则比 ``str()``，再退化成数值比较。

    两个坑，改这段前先看清楚：

    - **不能**用 ``Series.astype(str)`` 代替逐元素 ``str()``：它对 ``datetime64`` 会省掉时间部分
      （``2024-01-02 00:00:00`` → ``2024-01-02``），会把真差异判成相等；
    - 第 3 条**必须对「一侧是 NaN、另一侧不是」的格子也生效**，因为 ``str(nan) == "nan"``：
      ``_same(nan, "nan")`` 是 ``True``。少了这条，向量化版会在这种格子上比旧实现判得更紧。
    """
    left = left.reset_index(drop=True)
    right = right.reset_index(drop=True)
    left_values = left.tolist()
    right_values = right.tolist()

    none_left = np.array([value is None for value in left_values], dtype=bool)
    none_right = np.array([value is None for value in right_values], dtype=bool)
    na_left = np.asarray(pd.isna(left), dtype=bool)
    na_right = np.asarray(pd.isna(right), dtype=bool)

    # 规则 1 + 2：两侧都缺失即相等；但任一侧是 Python None 时，不再往下走文本/数值通道
    equal = na_left & na_right
    comparable = ~(none_left | none_right) & ~equal
    if not comparable.any():
        return equal

    positions = np.flatnonzero(comparable)
    left_text = np.array([str(left_values[position]) for position in positions], dtype=object)
    right_text = np.array([str(right_values[position]) for position in positions], dtype=object)

    # 规则 3 前半：str() 相等
    text_equal = left_text == right_text
    equal[positions[text_equal]] = True

    # 规则 3 后半：str 不等的格子才做数值兜底（CSV 往返会把 12000000 读成 int64、replay 侧是 float64）
    numeric = np.flatnonzero(~text_equal)
    if len(numeric):
        equal[positions[numeric]] = _numeric_close(left_text[numeric], right_text[numeric])
    return equal


def _frame_diff(rebuilt: Any, clean: Any, keys: list[str]) -> list[str]:
    """逐单元格比较 replay 结果与 clean.csv 读回，返回前几条差异描述。

    实现是**逐列向量化**，语义与「逐格调 ``_same``」完全一致（见 ``_column_equal``）。
    旧实现 ``rebuilt.iloc[position][col]`` 在内层循环里每取一列就重建整行 Series，
    是 O(行×列²) 次取值：5580×76 的产物实测单次要 8 分钟以上，占 verify 全部耗时。
    """
    if list(rebuilt.columns) != list(clean.columns):
        return [f"列集合不一致：replay {list(rebuilt.columns)} != clean {list(clean.columns)}"]
    if len(rebuilt) != len(clean):
        return [f"行数不一致：replay {len(rebuilt)} != clean {len(clean)}"]
    columns = list(rebuilt.columns)
    if not columns or not len(rebuilt):
        return []

    mismatch = np.zeros((len(rebuilt), len(columns)), dtype=bool)
    for col_index in range(len(columns)):
        left = rebuilt.iloc[:, col_index]
        right = clean.iloc[:, col_index]
        if left.equals(right):
            continue  # 同值同 dtype：整列短路，走不到逐格比较
        mismatch[:, col_index] = ~_column_equal(left, right)

    # C 序展平 = 旧实现的「按行、按列序」扫描顺序，前 5 条差异的文案与顺序都不变
    flat = np.flatnonzero(mismatch.ravel())[:5]
    if not len(flat):
        return []
    rebuilt_keys = row_keys(rebuilt, keys)
    problems: list[str] = []
    for index in flat:
        row_position, col_index = divmod(int(index), len(columns))
        col = columns[col_index]
        left = rebuilt.iat[row_position, col_index]
        right = clean.iat[row_position, col_index]
        problems.append(f"{rebuilt_keys.iloc[row_position]}.{col}（replay {left!r} != clean {right!r}）")
    return problems


def _dry_run_problems(
    rebuilt: Any,
    original: Any,
    changes: list[dict[str, Any]],
    keys: list[str],
) -> list[str]:
    """dry-run 的第二判据：拟改动必须能落回输入，且「写成非空值」的落点不能是 NaN。

    **为什么偏离「replay 后不为 NaN」的字面判据**：``filled_nan``（缺失 token 填充）的
    ``after`` 就是 ``None``，字面执行会把所有填充动作判死。所以这里只对 ``after is not None``
    的变更要求非 NaN。「哪些 ``after=None`` 是合法的」需要一个白名单语义，而它依赖阶段 3 的
    ``target_state`` 与阶段 5 的 ``phase``——**登记为「阶段 5 之后待定」，不在阶段 1 硬定**。

    **两条路径警告**：本函数是**独立实现**，不是 ``replay_changes`` 的一个开关。dry-run 仍先跑
    ``replay_changes``（复用 before 校验与主键定位），再走本函数「跳过 clean 比对」。
    因此两条路径的「verify 通过」含义不同：非 dry-run = 产物可由 input + changes 重建；
    dry-run = changes 在 input 上自洽且不产生非法 NaN。**改一条必须检查另一条。**
    """
    rebuilt_positions = {str(key): pos for pos, key in enumerate(row_keys(rebuilt, keys))}
    original_positions = {str(key): pos for pos, key in enumerate(row_keys(original, keys))}
    # 主键列本身被改过（如 date_format）时，rebuilt 的键已换成新值，只能按原始位置对齐；
    # 行数变化（删重）时位置会错位，此时不做回退，宁可报「不可定位」也不静默查错格。
    aligned = len(rebuilt) == len(original)
    problems: list[str] = []
    for change in changes:
        if change.get("change_type") in {"flagged_kept", "dropped_row", "dropped_col"}:
            continue
        row_key = str(change.get("row_key"))
        col = change.get("col")
        position = rebuilt_positions.get(row_key)
        if position is None and aligned:
            position = original_positions.get(row_key)
        if position is None:
            problems.append(f"dry-run：变更行 {row_key} 在产物中不可定位")
        elif col not in rebuilt.columns:
            problems.append(f"dry-run：变更列 {col} 不在产物里")
        elif change.get("after") is not None and pd.isna(rebuilt.iloc[position][col]):
            problems.append(f"dry-run：{row_key}.{col} replay 后为 NaN")
        if len(problems) >= 5:
            break
    return problems


def verify_passport(passport_path: str | Path, input_path: str | Path | None = None) -> tuple[bool, list[str]]:
    """按护照校验四项：产物齐全 / 文件哈希 / replay 一致 / 事件链闭合。

    返回 ``(ok, messages)``；messages 里的 ``[warn]`` 不改变 ok，只说明某项无法确认
    （如 00/01/02 的旧护照没有 ``output_file_sha256``）。

    **``ext:dry_run`` 分支（阶段 0 判据 4）**：护照带 ``dry_run: true`` 时改为校验
    (a) ``clean.csv`` 与输入逐字节相同；(b) ``changes`` 能落回输入且非空落点不为 NaN
    （见 ``_dry_run_problems``），**不要求** ``clean == replay(input)``——dry-run 的 clean 就是输入。
    该分支的 NaN 判据故意比字面宽：``filled_nan`` 的 ``after`` 本就是 ``None``，
    白名单语义等阶段 5 之后定。
    """
    passport_path = Path(passport_path)
    if not passport_path.is_file():
        return False, [f"缺少产物：{passport_path.name}（{passport_path}）"]
    passport = read_passport(passport_path)
    out_dir = passport_path.parent
    stem = passport_path.name.removesuffix(".passport.json")
    messages: list[str] = []
    ok = True

    # 旧护照（schema 2.1.0 之前导出）没有这个字段：按 FIELD_COMPAT 的 ``warn`` 处理——
    # 只提示「按 2.0.0 读」，不改 ok、不跳过其余校验。处理方式的**事实源**是兼容表，
    # 这里不另写一份三值判断，否则表与实现会各说一套。
    if not passport.get("profile_schema_version"):
        behavior = FIELD_COMPAT["profile_schema_version"]
        if behavior == "warn":
            messages.append("[warn] profile_schema_version 缺失，按 2.0.0 处理")
        elif behavior == "error":
            ok = False
            messages.append("profile_schema_version 缺失（兼容表要求 error）")

    artifacts = {
        "clean.csv": out_dir / f"{stem}.clean.csv",
        "changes.parquet": out_dir / f"{stem}.changes.parquet",
        "events.jsonl": out_dir / f"{stem}.events.jsonl",
    }
    absent = [name for name, path in artifacts.items() if not path.is_file()]
    if absent:
        return False, [f"缺少产物：{name}" for name in absent]

    dry_run = bool(passport.get("dry_run"))

    original_path = Path(input_path) if input_path else None
    if original_path is None:
        recorded = passport.get("input_path")
        if recorded and Path(recorded).is_file():
            original_path = Path(recorded)
        elif (out_dir / f"{stem}.csv").is_file():
            original_path = out_dir / f"{stem}.csv"
    if original_path is None or not original_path.is_file():
        return False, [*messages, "无法定位原始 CSV（护照无 input_path，同目录也没有同名 csv）"]

    expected_sha = passport.get("output_file_sha256")
    if not expected_sha:
        messages.append("[warn] 旧护照无 output_file_sha256，跳过文件哈希校验")
    elif sha256_file(artifacts["clean.csv"]) != expected_sha:
        ok = False
        messages.append("output_file_sha256 不一致（clean.csv 与护照不配套）")
    else:
        messages.append("hash OK")

    if dry_run:
        # 扩展点二（阶段 0 判据 4 / ext:dry_run）：dry-run 产物不改数据，
        # 所以 clean.csv 必须与输入逐字节相同——这是比「哈希配套」更强的一条。
        if sha256_file(artifacts["clean.csv"]) == sha256_file(original_path):
            messages.append("dry-run：clean.csv 与输入逐字节一致")
        else:
            ok = False
            messages.append("dry-run：clean.csv 与输入不一致（应逐字节相同）")

    original = pd.read_csv(original_path)
    keys = passport.get("keys") or ([passport["key"]] if passport.get("key") else [str(original.columns[0])])

    report_after = out_dir / f"{stem}.report.after.json"
    dtypes = json.loads(report_after.read_text(encoding="utf-8")).get("dtypes") if report_after.is_file() else None

    changes = read_changes(artifacts["changes.parquet"])
    try:
        rebuilt = replay_changes(original, changes, key=keys, dtypes=dtypes)
        if dry_run:
            # 不要求 clean == replay(input)：dry-run 的 clean 就是输入本身。
            # 只要求「每条拟改动都能落回输入」，且写成非空值的落点不是 NaN。
            problems = _dry_run_problems(rebuilt, original, changes, keys)
        else:
            clean = pd.read_csv(artifacts["clean.csv"])
            problems = _frame_diff(rebuilt, clean, keys)
    except AssertionError as exc:
        # replay 写值前会校验 before；before 对不上说明 changes 被改过，不是数据问题
        return False, [*messages, f"replay 不一致：{exc}"]
    except (KeyError, ValueError) as exc:
        return False, [*messages, f"replay 失败：{exc}"]
    if problems:
        ok = False
        messages.extend(f"replay 不一致：{problem}" for problem in problems)
    else:
        messages.append("replay OK")

    event_ids: set[str] = set()
    for line in artifacts["events.jsonl"].read_text(encoding="utf-8").splitlines():
        if line.strip():
            event_ids.add(str(json.loads(line).get("event_id")))
    orphan = sorted({str(change.get("decision_id")) for change in changes} - event_ids)
    if orphan:
        ok = False
        messages.append(f"事件链断裂：变更 {orphan[0]} 找不到对应事件")
    else:
        messages.append("events OK")

    return ok, messages


def verify_exit_code(
    passport_path: str | Path,
    input_path: str | Path | None = None,
    *,
    result: tuple[bool, list[str]] | None = None,
) -> int:
    """把校验结果折算成 CI 退出码：0 全清 / 2 有未解决项 / 1 校验失败。

    判定顺序：产物与哈希先过（失败即 1），再看护照里的 ``auto_only`` 与
    ``unresolved_issues_count``（任一成立即 2），否则 0。

    ``result`` 是**已跑过**的 ``verify_passport`` 结果：CLI 拿走它就不必再跑一遍
    （一次 verify 要读两遍 CSV、重放一遍变更，重复调用是纯浪费）。不传则本函数自己跑。
    """
    ok, _messages = result if result is not None else verify_passport(passport_path, input_path)
    if not ok:
        return EXIT_FAIL
    passport = read_passport(passport_path)
    unresolved = int(passport.get("unresolved_issues_count") or 0)
    if passport.get("auto_only") or unresolved > 0:
        return EXIT_UNRESOLVED
    return EXIT_OK
