"""会话层：把 01_minimal_kernel 验证过的内核提炼成 5 个公开 API。

公开接口只有 open / start / next / answer / export，另加 open 时设置的 profile_info 属性。
规则清单与列名全部来自 profiles/*.yaml（04 起下沉）；05 追加两条无头通道：
``render_question`` 在无 widget 环境返回纯文本，``Session.apply_answers_yaml`` 支持离线回填。
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .check_counts import validate_unanswered_source
from .diff import render_diff_html
from .engine import RULES_KEY, RULE_TYPES, aggregate, detect, dispatch, fix
from .models import Gap, Question, UnansweredQuestion
from .passport import row_keys, sha256_file, write_changes, write_events, write_passport
from .profile import (
    PROFILE_SCHEMA_VERSION,
    Profile,
    RuleSpec,
    load_profile,
    validate_columns,
)

CHANGE_COLUMNS = ["row_key", "col", "before", "after", "change_type", "rule_id", "decision_id"]
LEVEL_ORDER = {"strategy": 0, "column": 1, "point": 2}

# 降级通道产生的事件动作：不记人工决策，护照据此判定 auto_only
AUTO_ACTIONS = {"auto", "auto_flag"}

# 三计数口径（阶段 1 定死）：**总数 = question 队列长度**，N/M/K 与之一一对应。
# 注意 ``changes`` 的条数 ≠ N 或 M：一个 fix 决策可改多格，一个 skip 决策可标多行，
# 所以对外只按 ``decision_id`` 去重后与 N/M 比较，不按行数比较。
FIX_ACTIONS = {"answer", "batch_apply", "auto"}
FLAG_ACTIONS = {"skip", "auto_flag"}

# 会话内部的截断原因 → ``check_counts.UNANSWERED_SOURCES`` 的封闭枚举。
# 未因上限截断、只是「还没问」的剩余题不在此列（它们没有截断来源，故不带标签）。
_UNANSWERED_SOURCE = {"max_questions_exceeded": "max_questions"}

# 检测逻辑 4b 起在 ``engine.detect``：会话层只校验 profile 声明的 type 已被引擎实现，
# 未实现的 type 在 open 阶段直接报错，不会静默跳过（04：缺必需列 / 未实现规则都必须"明确拒绝"）。
# 规则清单与选项文案仍只来自 profile，会话层不写规则分支。

# 行级决策范式（``policy`` 段）的键与取值定义在 ``profile.POLICY_REQUIREMENTS``：
# 那是 schema 合同，加载期校验；会话层只是读值，不重复声明、也不留默认值。

#: 决策参数字段（逐题覆盖）与「变更记录怎么落盘」都在上层实现：
#: 参数白名单在 ``engine.DECISION_PARAMS``（决策由 dispatch 做），
#: 记录形态在 ``models.make_change``（engine 产出、session 落盘共用一份）。


def _unit_signature(question: Question) -> tuple[Any, ...]:
    """与 ``engine.aggregate`` 的聚合键同一口径：rule id + 层级 + 影响列集合。

    ``_units``（决策单元 → 差距）靠它把聚合前后的题对上；两处口径必须一致，
    否则聚合后题目与差距会错配。``test_stage4_dispatch.py`` 有覆盖度断言。
    """
    columns = {str(column) for sample in question.samples for column in sample}
    return (question.rule_id, question.aggregation_level, tuple(sorted(columns)))


def frame_hash(df: pd.DataFrame) -> str:
    """内存 DataFrame 的 16 位指纹，只用于事件流留痕与护照历史字段。"""
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()[:16]


def _render_text(question: Question) -> str:
    """问题的纯文本卡片：无 widget 环境（CI / 纯终端）也能完整呈现。"""
    lines = [
        f"[{question.rule_id}] 层级={question.aggregation_level} 影响 {len(question.affected_row_keys)} 行",
        f"默认决策：{question.default}",
    ]
    for index, option in enumerate(question.options or [], start=1):
        lines.append(f"  {index}) {option}")
    for sample in (question.samples or [])[:3]:
        lines.append(f"  样本：{sample}")
    return "\n".join(lines)


def _render_widget(question: Question) -> Any:
    """有 ipywidgets 时才构造 widget；渲染逻辑与文本卡片同源。"""
    import html as _html

    import ipywidgets

    return ipywidgets.VBox(
        [
            ipywidgets.HTML(f"<pre>{_html.escape(_render_text(question))}</pre>"),
            ipywidgets.RadioButtons(options=list(question.options or []), description="决策"),
        ]
    )


def render_question(question: Question) -> Any:
    """降级两层：先看 ``FINPASS_NO_WIDGET``，再试 import ipywidgets，都没有就返回 str。"""
    if os.environ.get("FINPASS_NO_WIDGET") == "1":
        return _render_text(question)
    try:
        import ipywidgets  # noqa: F401
    except ImportError:
        return _render_text(question)
    return _render_widget(question)


class Session:
    """一次清洗会话：open 时读盘，start 后产生问题队列，answer 逐题推进。"""

    def __init__(
        self,
        path: str,
        profile: str | Path | dict[str, Any] | Profile = "a_stock_daily",
        version: str | None = None,
        key: str | list[str] | None = None,
        max_questions_per_session: int = 50,
        auto_only: bool = False,
        dry_run: bool = False,
        passport_id_override: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.profile_obj = load_profile(profile)
        self.profile = self.profile_obj.name
        self.version = version or self.profile_obj.version
        self.keys = [key] if isinstance(key, str) else list(key or self.profile_obj.key_columns)
        # self.key 是给人看的标签（单主键时即列名，复合主键时是「合约代码+日期」）；
        # 取值一律走 _row_keys()，不要拿它去索引 DataFrame。
        self.key = self.keys[0] if len(self.keys) == 1 else self.profile_obj.key_label
        self.date_column = self.profile_obj.date_column
        self.missing_tokens = self.profile_obj.missing_tokens
        for spec in self.profile_obj.rules:
            if spec.type not in RULE_TYPES:
                raise ValueError(f"profile {self.profile} 声明了未实现的规则类型：{spec.type}（{spec.id}）")

        self.max_questions_per_session = max_questions_per_session
        self.auto_only = auto_only
        self.dry_run = dry_run
        self.passport_id_override = passport_id_override
        self._passport_id_value: str | None = None
        self._auto_resolved = False
        self.answers_path: str | None = None
        self.original = pd.read_csv(self.path)
        validate_columns(self.original.columns, self.profile_obj)
        self.df = self.original.copy()
        # 原始快照：dry-run 导出时 clean.csv 用它（其余产物照常按 self.df 生成）。
        # 不复用 self.original 是为了让两者职责分开：original 是「读盘结果」，input_df 是「不改数据的基准」。
        self.input_df = self.original.copy()
        self.events: list[dict[str, Any]] = []
        self.changes: list[dict[str, Any]] = []
        self.queue: list[Question] = []
        self.unanswered: list[UnansweredQuestion] = []
        self._answered: set[str] = set()
        #: 决策单元 → 该题的差距（dispatch 的入参）。4c 起取代 ``_meta``：
        #: 列名由 ``Gap.targets`` / ``change["col"]`` 承载，会话层不再缓存列语义。
        self._units: dict[str, list[Gap]] = {}
        self._decision_index: dict[str, str] = {}
        self.profile_info = {
            "profile": self.profile,
            "version": self.version,
            "key": self.key,
            "keys": list(self.keys),
            "rules": len(self.profile_obj.rules),
            "rule_ids": self.profile_obj.rule_ids,
            "source": self.profile_obj.source,
            "rows": int(len(self.original)),
            "cols": int(self.original.shape[1]),
            "columns": list(self.original.columns),
        }

    # ---------------------------------------------------------------- 五步
    def start(self, resolve_auto: bool = False) -> dict[str, Any]:
        """执行检测 + 聚合 + 排序，产出问题队列，返回队列统计（原有 key 只增不改）。

        ``auto_only=True`` 或 ``resolve_auto=True`` 时紧接着自动收尾：profile 里 ``mode: auto``
        的规则直接执行，其余规则一律 ``flagged_kept`` 打标保留，人工决策数保持 0。

        ``resolve_auto`` 供 CLI 默认路径（``--interactive none``）使用——阶段 1 的「默认路径不空」。
        程序化 API 默认仍不自动收尾，交给调用方逐题 ``answer``。
        """
        self._build_queue()
        if self.auto_only or resolve_auto:
            self._auto_resolve()
            self._auto_resolved = True
        levels = {"strategy": 0, "column": 0, "point": 0}
        for q in self.queue:
            levels[q.aggregation_level] = levels.get(q.aggregation_level, 0) + 1
        return {
            "total_questions": len(self.queue),
            **levels,
            "total_affected_rows": sum(len(q.affected_row_keys) for q in self.queue),
            "rules_hit": sorted({q.rule_id for q in self.queue}),
            "unanswered_count": len(self.unanswered),
        }

    def next(self) -> Question | None:
        """返回下一个未回答问题；队列空或达到会话上限时返回 None。"""
        if len(self._answered) >= self.max_questions_per_session:
            self._drain_unanswered()
            return None
        for q in self.queue:
            if q.question_id not in self._answered:
                return q
        return None

    def _drain_unanswered(self) -> None:
        """把超出会话上限的剩余问题登记到 unanswered（幂等）。"""
        known = {item.question_id for item in self.unanswered}
        for q in self.queue:
            if q.question_id in self._answered or q.question_id in known:
                continue
            self.unanswered.append(
                UnansweredQuestion(
                    question_id=q.question_id,
                    rule_id=q.rule_id,
                    aggregation_level=q.aggregation_level,
                    affected_row_keys=list(q.affected_row_keys),
                    reason="max_questions_exceeded",
                )
            )

    def answer(
        self,
        question_id: str,
        action: str = "answer",
        params: dict[str, Any] | None = None,
        reason: str = "",
    ) -> list[dict[str, Any]]:
        """回答一个问题：写一条事件 + 若干条单元格变更，二者用 decision_id 关联。"""
        q = self._find(question_id)
        if question_id in self._answered:
            raise ValueError(f"问题已答过：{question_id}")

        params = params or {}
        decision_id = uuid.uuid4().hex
        before_hash = self._hash(self.df)
        changed = self._apply(q, decision_id, action, params)
        self._record(q, action, params, reason, decision_id, before_hash, changed)
        return changed

    def _apply(self, q: Question, decision_id: str, action: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """四步内核的第 2–3 步：``dispatch`` 定变更，``fix`` 落数据。

        会话层不再有按 rule_id 的分支（4c 起）：它只把「这道题命中哪些差距」和
        「人怎么答的」交给引擎，然后把引擎返回的变更落进事件流。
        """
        state = self._detect_state()
        result = dispatch(
            self.df,
            self._units[q.question_id],
            {"decision_id": decision_id, "action": action, "params": params},
            state,
            dict(self.profile_obj.policy),
        )
        self.df = fix(self.df, result.changes, state)
        return result.changes

    def _record(
        self,
        q: Question,
        action: str,
        params: dict[str, Any],
        reason: str,
        decision_id: str,
        before_hash: str,
        changed: list[dict[str, Any]],
    ) -> None:
        """落一条事件 + 变更 + 决策索引，问答与降级通道共用同一条事件流。"""
        self.events.append(
            {
                "event_id": decision_id,
                "ts": datetime.now().isoformat(),
                "question_id": q.question_id,
                "rule_id": q.rule_id,
                "action": action,
                "batch": action == "batch_apply",
                "params": params,
                "reason": reason,
                "affected_row_keys": q.affected_row_keys,
                "data_hash_before": before_hash,
                "data_hash_after": self._hash(self.df),
            }
        )
        self.changes.extend(changed)
        self._answered.add(q.question_id)
        self._decision_index[q.question_id] = decision_id

    def _auto_resolve(self) -> None:
        """无人工通道：auto 规则直接执行，human 规则打标保留（全部记为自动决策）。"""
        for q in list(self.queue):
            if q.question_id in self._answered:
                continue
            decision_id = uuid.uuid4().hex
            before_hash = self._hash(self.df)
            if self._spec(q.rule_id).mode == "auto":
                changed = self._apply(q, decision_id, "answer", {})
                action = "auto"
            else:
                changed = self._apply(q, decision_id, "skip", {})
                action = "auto_flag"
            reason = "auto_only：无人工决策" if self.auto_only else "默认路径：无人工决策"
            self._record(q, action, {}, reason, decision_id, before_hash, changed)

    # ---------------------------------------------------------------- 计数
    def counts(self) -> dict[str, int]:
        """三计数口径：``fix + flag + unanswered == len(queue)``。

        - ``fix``：决策为 fix 的**问题数**（``answer`` / ``batch_apply`` / ``auto``）；
        - ``flag``：决策为打标的**问题数**（``skip`` / ``auto_flag``）；
        - ``unanswered``：既没 fix 也没 flag 的问题数。

        这三个数按 question 计，**不按 changes 行数计**：一个 fix 可改多格，
        一个 skip 可标多行。对外比较时先把 ``decision_id`` 去重。
        """
        fix = sum(1 for event in self.events if event["action"] in FIX_ACTIONS)
        flag = sum(1 for event in self.events if event["action"] in FLAG_ACTIONS)
        unanswered = len(self.queue) - len(self._answered)
        if fix + flag != len(self._answered):
            raise ValueError(
                f"事件流与已答集合不一致：fix {fix} + flag {flag} != 已答 {len(self._answered)}"
                "（同一问题被记录了多条事件）"
            )
        return {"fix": fix, "flag": flag, "unanswered": unanswered}

    def unanswered_sources(self) -> dict[str, int]:
        """未答 K 的来源标签，取值必须落在 ``check_counts.UNANSWERED_SOURCES`` 里。

        只有「被 ``max_questions`` 截断」的题带标签；只是「还没问」的剩余题不带标签，
        它们不属于三种截断来源，混进标签会污染 D5 的同源口径。
        """
        sources: dict[str, int] = {}
        for item in self.unanswered:
            source = _UNANSWERED_SOURCE.get(item.reason, item.reason)
            validate_unanswered_source(source)
            sources[source] = sources.get(source, 0) + 1
        return sources

    def passport_id(self) -> str:
        """本代会话的护照 id：``{profile}-{YYYYMMDDTHHMMSS}-{uuid4前8位}``。

        同一次会话内稳定（导出两次不会换 id）；父代关系不靠命名表达，靠后续阶段的
        ``patch_of`` 字段，所以这里只保证「唯一 + 可读 + 不被命名规则耦合」。
        测试与 7a 可用 ``passport_id_override`` 固定取值。
        """
        if self._passport_id_value is None:
            if self.passport_id_override:
                self._passport_id_value = self.passport_id_override
            else:
                stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
                self._passport_id_value = f"{self.profile}-{stamp}-{uuid.uuid4().hex[:8]}"
        return self._passport_id_value

    def export_questions_yaml(self, out_path: str | Path) -> Path:
        """把待回答问题导出成 yaml 模板，人填 ``answer`` 后交给 ``apply_answers_yaml``。"""
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "input": str(self.path),
            "profile": self.profile,
            "questions": [
                {
                    "question_id": question.question_id,
                    "rule_id": question.rule_id,
                    "level": question.aggregation_level,
                    "options": list(question.options or []),
                    "default": question.default,
                    "affected_row_keys": list(question.affected_row_keys),
                    "answer": None,
                }
                for question in self.queue
            ],
        }
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    def load_answers_yaml(self, path: str | Path) -> list[dict[str, Any]]:
        """读回问题清单（含已填答案）。"""
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return list(payload.get("questions") or [])

    def apply_answers_yaml(self, path: str | Path) -> int:
        """按 yaml 里的答案逐题作答，返回回填条数；记录来源供护照写复现命令。"""
        self.answers_path = str(path)
        answered = 0
        for item in self.load_answers_yaml(path):
            answer = item.get("answer") or {}
            self.answer(
                item["question_id"],
                answer.get("action", "skip"),
                answer.get("params") or {},
                answer.get("reason", "yaml 回填"),
            )
            answered += 1
        return answered

    def _replay_cmd(self, out_dir: Path) -> str:
        """写进护照的复现命令：由会话生成，调用方只读不拼。"""
        base = f"{shlex.quote(sys.executable)} -m finpass"
        out = shlex.quote(str(out_dir))
        dry = " --dry-run" if self.dry_run else ""
        if self.auto_only:
            return f"{base} run {shlex.quote(str(self.path))} --profile {self.profile} --auto-only --out {out}{dry}"
        if self.answers_path:
            return f"{base} resume {shlex.quote(str(self.answers_path))} --out {out}"
        if self._auto_resolved:
            # 阶段 1：默认路径已自动收尾，复现不需要再指定 --interactive
            return f"{base} run {shlex.quote(str(self.path))} --profile {self.profile} --out {out}{dry}"
        return f"{base} run {shlex.quote(str(self.path))} --profile {self.profile} --interactive cli --out {out}{dry}"

    def export(self, out_dir: str) -> dict[str, str]:
        """写出 7 个产物，文件名前缀取输入 CSV 的 stem。

        ``dry_run=True`` 只切换 ``clean.csv`` 的来源：**直接复制输入文件**（逐字节一致，
        不经过 ``to_csv`` 重新格式化），其余产物照常写——``changes`` 是「拟改动清单」，
        语义是「如果应用会改成什么」，不是「已经改成什么」。
        """
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = self.path.stem
        written: dict[str, str] = {}

        clean = out / f"{stem}.clean.csv"
        if self.dry_run:
            shutil.copyfile(self.path, clean)
        else:
            self.df.to_csv(clean, index=False)
        written["clean"] = str(clean)
        # dry-run 时产物状态就是输入本身；非 dry-run 时就是清洗后的 df。
        # report.after 描述「实际写出的 clean.csv」，diff 仍按「拟改动」渲染（二者职责不同）。
        artifact_frame = self.input_df if self.dry_run else self.df

        events_path = out / f"{stem}.events.jsonl"
        write_events(self.events, events_path)
        written["events"] = str(events_path)

        changes_path = out / f"{stem}.changes.parquet"
        write_changes(self.changes, changes_path)
        written["changes"] = str(changes_path)

        counts = self.counts()
        passport = {
            "passport_id": self.passport_id(),
            "dry_run": bool(self.dry_run),
            "counts": counts,
            "input_hash": self._hash(self.original),
            "output_file_sha256": sha256_file(clean),
            "input_path": str(self.path),
            "keys": list(self.keys),
            "profile_name": self.profile,
            "profile_version": self.version,
            # schema 版本是**加载器合同**（``PROFILE_SCHEMA_VERSION``），不是 profile 声明的属性：
            # ``version`` 回答「这份 profile 的内容何时改过」，本字段回答「产物出自哪种结构」。
            # 两个字段并存，审计员才能区分「加载器合同升级」与「profile 内容变了」。
            "profile_schema_version": PROFILE_SCHEMA_VERSION,
            "human_decisions_count": sum(1 for e in self.events if e["action"] not in AUTO_ACTIONS),
            "unresolved_issues_count": sum(1 for c in self.changes if c["change_type"] == "flagged_kept"),
            "auto_only": bool(self.events) and all(e["action"] in AUTO_ACTIONS for e in self.events),
            "cli_replay_cmd": self._replay_cmd(out),
            "reproduce": f"finpass verify {stem}.passport.json",
        }
        passport_path = out / f"{stem}.passport.json"
        write_passport(passport, passport_path)
        written["passport"] = str(passport_path)

        before_path = out / f"{stem}.report.before.json"
        before_path.write_text(json.dumps(self._report(self.original), ensure_ascii=False, indent=2), encoding="utf-8")
        written["report_before"] = str(before_path)

        after_path = out / f"{stem}.report.after.json"
        after_path.write_text(json.dumps(self._report(artifact_frame), ensure_ascii=False, indent=2), encoding="utf-8")
        written["report_after"] = str(after_path)

        diff_path = out / f"{stem}.diff.html"
        render_diff_html(
            self.original,
            self.changes,
            diff_path,
            key=self.keys[0] if len(self.keys) == 1 else list(self.keys),
            final=self.df,
            reasons={event["event_id"]: event["reason"] for event in self.events},
        )
        written["diff"] = str(diff_path)
        return written

    # ---------------------------------------------------------------- 检测
    def _build_queue(self) -> None:
        """跑 ``engine.detect``，把每条 ``Gap`` 包装成问题；同时记下「这道题命中哪些差距」。

        聚合与排序仍在这里（``engine.aggregate``）：聚合键含 ``aggregation_level`` 与
        问题列集合，都是展示层信息，engine 的 ``dispatch`` 拿不到。``_units`` 让聚合前后
        的题与差距对上——用同一个键（``_unit_signature``），聚合把题并了，差距也跟着并。
        """
        state = self._detect_state()
        policy = dict(self.profile_obj.policy)
        pairs = [(self._gap_to_question(gap), gap) for gap in detect(self.df, state, policy)]
        self.queue = aggregate([question for question, _ in pairs])
        self.queue.sort(key=lambda q: LEVEL_ORDER.get(q.aggregation_level, 9))
        self._units = {}
        for question in self.queue:
            signature = _unit_signature(question)
            self._units[question.question_id] = [
                gap for raw, gap in pairs if _unit_signature(raw) == signature
            ]

    def _detect_state(self) -> dict[str, Any]:
        """把 profile 解析成 ``engine.detect`` 的入参：列级目标态 + 保留键 ``__rules__``。

        engine 的签名里没有「规则清单」参数，但它必须知道跑哪些规则、用哪个 rule_id、
        查哪几列——这几件事只来自 profile 的 ``business_rules``。这里解析一次传过去，
        不落盘、不写 yaml，事实源仍只有 profile 一份（见 ``engine`` 模块 docstring）。
        """
        state: dict[str, Any] = {
            column: {"semantic": target.semantic, **target.options}
            for column, target in self.profile_obj.target_state.items()
        }
        state[RULES_KEY] = {
            "missing_tokens": list(self.missing_tokens),
            "checks": [
                {"id": spec.id, "type": spec.type, "columns": self._check_columns(spec)}
                for spec in self.profile_obj.rules
            ],
        }
        return state

    def _check_columns(self, spec: RuleSpec) -> list[str]:
        """规则检查哪些列：声明优先；``date_format`` 没写列时用 profile 的 ``date_column``。

        其余规则的空列表是「按类型自己决定」：``missing_token`` 空列表扫全部列、
        声明了则只扫声明列（4d 起；宽表靠它把题量从「每列一题」压到「点名几题」），
        ``duplicate_key`` 用主键列，``ohlc_contradiction`` 用角色列。
        """
        if spec.columns:
            return list(spec.columns)
        if spec.type == "date_format":
            return [self.date_column]
        return []

    def _gap_to_question(self, gap: Gap) -> Question:
        """把一条差距包装成问题：``question_id`` / 层级 / 选项 / 默认值全在这一层定。

        ``Gap`` 只带检测事实（rule_id / targets / rows），所以选项文案与聚合层级是展示层的
        职责——engine 不读 profile 的 ``options``，也不背 ``q_missing_{列}`` 这类命名。
        4c 起这里**不返回列缓存**：列名随 ``Gap.targets`` 进 dispatch、随 ``change["col"]`` 落盘。
        """
        if not gap.rows:
            raise ValueError(f"规则 {gap.rule_id} 的 Gap 没有任何命中行：engine.detect 只应有差距才产出 Gap")
        rule_type = self._type_of(gap.rule_id)
        keys = row_keys(pd.DataFrame(gap.rows), self.keys)
        samples = list(gap.rows[:5])
        if rule_type == "duplicate_key":
            return Question(
                question_id="q_dup_key",
                rule_id=gap.rule_id,
                aggregation_level="strategy",
                affected_row_keys=keys.unique().tolist(),
                samples=samples,
                options=["保留第一条", "保留最后一条", "跳过打标"],
                default="保留第一条",
            )
        if rule_type == "ohlc_contradiction":
            row_key = str(keys.iloc[0])
            return Question(
                question_id=f"q_ohlc_{row_key}",
                rule_id=gap.rule_id,
                aggregation_level="point",
                affected_row_keys=[row_key],
                samples=samples,
                options=["标记跳过", "以收盘价为准修正"],
                default="标记跳过",
            )

        col = gap.targets[0]
        if rule_type == "missing_token":
            question_id, options, default = f"q_missing_{col}", ["填 NaN", "跳过打标"], "填 NaN"
        elif rule_type == "unit_scale":
            question_id = f"q_unit_{col}"
            options, default = ["按万元折算异常行", "跳过打标"], "按万元折算异常行"
        elif rule_type == "date_format":
            # 文案与检测同源：值仍来自 target_state[列].format，这里只取来拼选项
            fmt = str(self._target_value(col, "format", gap.rule_id))
            question_id = f"q_date_{col}"
            options, default = [f"转换为 {fmt}", "跳过打标"], f"转换为 {fmt}"
        else:  # range_check：选项与默认值可由规则声明覆盖
            spec = self._spec(gap.rule_id)
            question_id = f"q_{gap.rule_id}_{col}"
            options = list(spec.get("options") or ["按边界裁剪", "跳过打标"])
            default = str(spec.get("default", "按边界裁剪"))
        return Question(
            question_id=question_id,
            rule_id=gap.rule_id,
            aggregation_level="column",
            affected_row_keys=keys.tolist(),
            samples=samples,
            options=options,
            default=default,
        )

    def _spec(self, rule_id: str) -> RuleSpec:
        for spec in self.profile_obj.rules:
            if spec.id == rule_id:
                return spec
        raise KeyError(f"profile 未声明规则：{rule_id}")

    def _type_of(self, rule_id: str) -> str:
        return self._spec(rule_id).type

    # ------------------------------------------------------- 参数来源（阶段 3）
    def _target_value(self, col: str, path: str, rule_id: str) -> Any:
        """按 ``unit_scale.factor`` 这种点路径读列级目标态。

        4c 起这里只剩**展示层**用途（拼日期选项文案）；数值换算系数、区间边界已由
        ``engine.dispatch`` 直接从 ``target_state`` 读，不再经会话层中转。
        """
        node: Any = self._target_options(col, rule_id)
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ValueError(
                    f"profile {self.profile}：规则 {rule_id} 需要 target_state[{col}].{path}，但未声明"
                )
            node = node[part]
        if node is None:
            raise ValueError(
                f"profile {self.profile}：规则 {rule_id} 需要 target_state[{col}].{path}，但值为空"
            )
        return node

    def _target_options(self, col: str, rule_id: str) -> dict[str, Any]:
        target = self.profile_obj.target(col)
        if target is None:
            raise ValueError(f"profile {self.profile}：规则 {rule_id} 引用的列 {col} 没有 target_state")
        return dict(target.options)

    def _row_keys(self, frame: pd.DataFrame | None = None) -> pd.Series:
        """主键取值：单主键取该列，复合主键用 ``|`` 连接成唯一字符串（即 row_key 形态）。"""
        return row_keys(self.df if frame is None else frame, self.keys)

    # ---------------------------------------------------------------- 工具
    def _find(self, question_id: str) -> Question:
        for q in self.queue:
            if q.question_id == question_id:
                return q
        raise KeyError(f"队列中没有这个问题：{question_id}")

    def _hash(self, df: pd.DataFrame) -> str:
        return frame_hash(df)

    def _report(self, df: pd.DataFrame) -> dict[str, Any]:
        return {
            "rows": int(len(df)),
            "cols": int(df.shape[1]),
            "null_cells": int(df.isna().sum().sum()),
            "duplicates_on_key": int(df.duplicated(self.keys).sum()),
            "dtypes": {str(col): str(dtype) for col, dtype in df.dtypes.items()},
            "hash": self._hash(df),
        }


def open(path: str, profile: str = "a_stock_daily", **kwargs: Any) -> Session:  # noqa: A001 - 对外 API 名称按规划固定
    """工厂函数：读盘并返回一个尚未 start 的 Session。"""
    return Session(path, profile=profile, **kwargs)


def start(session: Session) -> dict[str, Any]:
    """模块级入口，委托给 Session.start。"""
    return session.start()


def next(session: Session) -> Question | None:  # noqa: A001 - 对外 API 名称按规划固定
    """模块级入口，委托给 Session.next。"""
    return session.next()


def answer(
    session: Session,
    question_id: str,
    action: str = "answer",
    params: dict[str, Any] | None = None,
    reason: str = "",
) -> list[dict[str, Any]]:
    """模块级入口，委托给 Session.answer。"""
    return session.answer(question_id, action=action, params=params, reason=reason)


def export(session: Session, out_dir: str) -> dict[str, str]:
    """模块级入口，委托给 Session.export。"""
    return session.export(out_dir)
