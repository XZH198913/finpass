"""命令行入口：finpass run / resume / verify。

05 的三条降级通道共用同一条事件流：

- ``run --interactive cli``：逐题在终端里决策（无 GUI）；
- ``run --questions q.yaml`` + ``resume q.yaml``：先导出问题清单，离线填完再回填；
- ``run --auto-only``：只跑 profile 里 ``mode: auto`` 的规则，其余全部打标保留；
- ``run --resume-answers q.yaml``：一步到位（护照里的 ``cli_replay_cmd`` 就走这条）。

退出码三态（CI 语义，禁止合并成 0/1）：

- ``EXIT_OK = 0`` 全部解决，无 flagged；
- ``EXIT_UNRESOLVED = 2`` 存在 flagged / 未答（软失败，不 fail build；``auto_only`` 本身不再触发）；
- ``EXIT_FAIL = 1`` 文件缺失 / 哈希不符 / replay 失败 / 参数错 / 数据与 profile 不匹配。

``EXIT_FAIL`` 那一行是**所有「命令没跑起来」的归宿**：CLI 在 ``main`` 处把用户能自己修的
失败（路径、编码、列名、profile）翻成一句中文再退出，不打 traceback。映射表见 ``_explain``，
``FINPASS_DEBUG=1`` 可让原始栈照常抛出。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import yaml

from . import __version__
from .passport import (
    EXIT_FAIL,
    EXIT_OK,
    EXIT_UNRESOLVED,
    read_passport,
    verify_exit_code,
    verify_passport,
)
from .messages import SUPPORTED_LANGUAGES, language as current_language
from .messages import set_language, t
from .narrate import narrate
from .session import Session, open as open_session

__all__ = ["EXIT_OK", "EXIT_FAIL", "EXIT_UNRESOLVED", "build_parser", "main"]


def _parse_decision(raw: str, question: Any) -> tuple[str, dict[str, Any], str]:
    """把一行输入解析成 ``(action, params, reason)``。

    支持：``1``..``9`` 选选项 / ``answer`` / ``skip`` / ``batch``，
    并可追加 ``:{"key": value}`` 形式的 JSON 参数。
    """
    text = raw.strip()
    if not text:
        return "answer", {}, t("cli.reason.cli_default")

    head, _, tail = text.partition(":")
    params: dict[str, Any] = json.loads(tail) if tail.strip() else {}
    token = head.strip().lower()

    if token in {"s", "skip"}:
        return "skip", params, t("cli.reason.skip")
    if token in {"b", "batch", "batch_apply"}:
        return "batch_apply", params, t("cli.reason.batch")
    if token.isdigit():
        options = list(question.options or [])
        index = int(token) - 1
        if not 0 <= index < len(options):
            raise ValueError(t("cli.option_out_of_range", token=token, total=len(options)))
        label = options[index]
        # 「跳过」选项在两种语言里文案不同：按文案表判定，不按中文字面量判定
        if label in {t("option.skip"), t("option.ohlc.skip")}:
            return "skip", params, t("cli.reason.choice", label=label)
        if question.rule_id == "duplicate_key":
            # 选项顺序由 session._gap_to_question 固定（1=保留第一条，2=保留最后一条）。
            # 文案留在展示层，会话层只认 first / last——所以这里做的是「选项 → 决策值」映射。
            # 未选第 1/2 项时不传，交给 profile 的 policy.duplicate_keep 兜底。
            if index in (0, 1):
                params.setdefault("duplicate_keep", "first" if index == 0 else "last")
        return "answer", params, t("cli.reason.choice", label=label)
    return "answer", params, t("cli.reason.choice", label=text)


def _ask_interactively(session: Session) -> None:
    """逐题问，逐题答；stdin 提前结束时明确报错，而不是抛 EOFError 栈。"""
    while (question := session.next()) is not None:
        print(
            "\n"
            + t(
                "cli.interactive.header",
                rule=question.rule_id,
                level=question.aggregation_level,
                rows=len(question.affected_row_keys),
            )
        )
        for sample in (question.samples or [])[:3]:
            print(t("cli.interactive.sample", sample=sample))
        for index, option in enumerate(question.options or [], start=1):
            print(f"  {index}) {option}")
        try:
            raw = input(t("cli.interactive.prompt"))
        except EOFError:
            remaining = len([q for q in session.queue if q.question_id not in session._answered])
            raise SystemExit(t("cli.interactive.eof", n=remaining)) from None
        action, params, reason = _parse_decision(raw, question)
        session.answer(question.question_id, action, params, reason)


def export_questions(session: Session, out_path: str | Path) -> Path:
    """兼容入口：问题清单导出逻辑在 ``Session.export_questions_yaml``。"""
    return session.export_questions_yaml(out_path)


def _exit_code_of(written: dict[str, str]) -> int:
    """按刚写出的护照判定 run/resume 的退出码。

    只看本代产物：留下 ``flagged_kept`` 或存在未答（``counts.unanswered``）即 2。
    ``auto_only`` 不再单独触发 2 —— 它只说明「决策都是自动的」，
    真正的失败信号是「有没有留下未解决项」，否则「自动跑完且全清」会被误判成软失败。

    **两个触发源是刻意的双保险，不是冗余**：``unresolved_issues_count`` 计的是
    ``flagged_kept`` 的**变更行数**（一行可标多格），``counts.unanswered`` 计的是**问题数**，
    量级不同、来源不同。``auto_only`` 通道下两者同时成立（非 auto 规则全走 ``_skip``），
    所以 05 那轮去掉 ``auto_only`` 触发后退出码仍是 2 —— **别因此以为只剩一个触发源**：
    若将来让 ``flagged_kept`` 不再计入 ``unresolved_issues_count``，闸门就只剩 counts 这一道。
    """
    passport = read_passport(written["passport"])
    counts = passport.get("counts") or {}
    unresolved = int(passport.get("unresolved_issues_count") or 0)
    unanswered = int(counts.get("unanswered") or 0)
    if unresolved > 0 or unanswered > 0:
        return EXIT_UNRESOLVED
    return EXIT_OK


def _print_summary(session: Session) -> None:
    """汇总行：``[汇总] 检查 N 条规则 / 触发 M 条 / 未动 K 条 / 未答 K' 条``。

    口径（与阶段 1 的三计数对齐，不另立一套）：

    - **N** = profile 声明的规则条数（`profile_obj.rules`，不是 6 类 rule_type）；
    - **M** = detect 命中的**规则条数**（队列里出现的 `rule_id` 去重），
      「触发」指检测命中，与是否已决策无关；
    - **K** = `N - M`，即声明了但这次没命中的规则数；
    - **K'** = `counts.unanswered`，**问题数**（不是规则数）——与阶段 1 的三计数同一来源。

    只用逐条 event 日志时，用户看完 N 行仍不知道「总共几条规则、漏了哪几条」；
    这一行就是给那个问题兜底的。
    """
    total_rules = len(session.profile_obj.rules)
    hit_rules = len({question.rule_id for question in session.queue})
    idle_rules = total_rules - hit_rules
    unanswered = session.counts()["unanswered"]
    print(
        t(
            "cli.summary",
            total=total_rules,
            hit=hit_rules,
            idle=idle_rules,
            unanswered=unanswered,
        )
    )


def _print_event_log(session: Session) -> None:
    """逐条打印处理记录（人话版）：动作 / 对象 / 依据 / 结果，一行一条规则。

    终端不再打 ``event_id``——它是审计标识，外部读者读不懂；可追溯性由
    ``narrate`` 末尾的「详细决策见 events.jsonl」保持，审计拿 ``rule_id`` 检索。
    渲染逻辑全在 ``narrate.py``（模板表在那里），这里只负责打印。
    """
    for line in narrate(session.changes, session.events, session.profile_obj):
        print(line)


def _print_counts(session: Session) -> None:
    """三计数 + K 来源：三行分开打，便于 CI 里直接 grep。"""
    counts = session.counts()
    sources = session.unanswered_sources()
    sep = "、" if current_language() == "zh" else ", "
    label = sep.join(f"{name}={value}" for name, value in sorted(sources.items())) or t(
        "cli.counts.no_source"
    )
    print(t("cli.counts.fix", n=counts["fix"]))
    print(t("cli.counts.flag", n=counts["flag"]))
    print(t("cli.counts.unanswered", n=counts["unanswered"], sources=label))


def _cmd_run(args: argparse.Namespace) -> int:
    # 阶段 1：默认路径不再空转。只有「没人问答、也没导出问题清单 / 没回填」时才自动收尾，
    # 否则会把该问的题先答掉，--questions 导出的清单会变空、--resume-answers 会撞「已答过」。
    resolve_auto = (
        args.interactive == "none" and not args.questions and not args.resume_answers
    )
    session = open_session(
        args.input,
        profile=args.profile,
        auto_only=args.auto_only,
        max_questions_per_session=args.max_questions,
        dry_run=args.dry_run,
        lang=getattr(args, "lang", None),
    )
    stats = session.start(resolve_auto=resolve_auto)
    # 问题数是**队列长度**（同一规则可以出多个问题：missing_token 每列一题），
    # 规则数是去重后的 rule_id 数——两个数不等是常态。这里把「几条规则」也写出来，
    # 否则读者会拿「问题 6 个」去数下面 5 行日志，误以为少了一条。
    print(
        t(
            "cli.question_line",
            total=stats["total_questions"],
            hit=len(stats["rules_hit"]),
            names=", ".join(stats["rules_hit"]) or t("cli.counts.no_source"),
        )
    )

    if args.resume_answers:
        answered = session.apply_answers_yaml(args.resume_answers)
        print(t("cli.resume.applied", n=answered, path=args.resume_answers))
    if args.questions:
        print(t("cli.questions.exported", path=session.export_questions_yaml(args.questions)))
    if args.interactive == "cli" and not args.auto_only and not args.resume_answers:
        _ask_interactively(session)

    _print_summary(session)
    _print_event_log(session)
    _print_counts(session)
    written = session.export(args.out)
    code = _exit_code_of(written)
    print(t("cli.products", path=Path(written["passport"]).parent, code=code))
    return code


def _cmd_resume(args: argparse.Namespace) -> int:
    payload_path = Path(args.state)
    if not payload_path.is_file():
        print(t("cli.missing_answers", name=payload_path.name))
        return EXIT_FAIL

    payload = yaml.safe_load(payload_path.read_text(encoding="utf-8")) or {}
    if not payload.get("input"):
        print(t("cli.answers.no_input"))
        return EXIT_FAIL

    session = open_session(
        payload["input"],
        profile=payload.get("profile", "a_stock_daily"),
        lang=getattr(args, "lang", None),
    )
    session.start()
    answered = session.apply_answers_yaml(payload_path)
    written = session.export(args.out)
    code = _exit_code_of(written)
    print(
        t(
            "cli.resume.done",
            n=answered,
            path=Path(written["passport"]).parent,
            code=code,
        )
    )
    return code


def _cmd_verify(args: argparse.Namespace) -> int:
    result = verify_passport(args.passport, input_path=args.input)
    ok, messages = result
    for message in messages:
        print(message)
    # 复用同一次校验的结果算退出码：verify_passport 要读两遍 CSV + 重放变更，
    # 再调一次 verify_exit_code 等于把整轮 verify 跑两遍（实测各占一半耗时）。
    code = verify_exit_code(args.passport, input_path=args.input, result=result)
    if code == EXIT_OK:
        print(t("cli.verify.pass"))
    elif code == EXIT_UNRESOLVED:
        print(t("cli.verify.pass_unresolved"))
    else:
        print(t("cli.verify.fail"))
    return code


def _user_path(args: argparse.Namespace) -> str:
    """这次失败最可能涉及的那个路径，用于拼提示语（没有就不瞎猜）。"""
    for attr in ("input", "passport", "state"):
        value = getattr(args, attr, None)
        if value:
            return str(value)
    return t("cli.path.none")


def _explain(exc: BaseException, args: argparse.Namespace) -> list[str] | None:
    """把「用户能自己修」的失败翻成一到两行中文；不认识的返回 ``None``（照原样抛）。

    只收四类 + 两个 CSV 解析子类，理由都是同一句：**报错的人修不了 Python 栈**。
    路径写错、Excel 存成 GBK、列名和 profile 对不上、profile 名字打错——这四件事占了
    新用户第一次 run 的绝大多数失败，而栈顶总是 ``pandas/io/parsers`` 或 ``profile.py``，
    对不懂代码的人等于没说。

    ``ValueError`` 是**故意的宽口**：profile 加载器的契约就是「结构问题一律 ValueError」
    （见 ``load_profile`` docstring），窄口反而要维护一张不断生长的子类表。代价是内部 bug
    若恰好是 ValueError 也会被吞成一句人话——``FINPASS_DEBUG=1`` 是出口，调试时置上即可。
    """
    path = _user_path(args)

    # 顺序要紧：EmptyDataError / ParserError 都是 ValueError 的子类，排在 ValueError 前面
    # 才有机会给出比「列名不匹配」更准的话（空文件 vs 列数不齐是两种修法）。
    if isinstance(exc, pd.errors.EmptyDataError):
        return [t("err.empty_csv", path=path), t("err.empty_csv.hint")]
    if isinstance(exc, pd.errors.ParserError):
        return [t("err.parser", path=path), t("err.parser.hint")]
    if isinstance(exc, UnicodeDecodeError):
        return [t("err.encoding", path=path), t("err.encoding.hint")]
    if isinstance(exc, IsADirectoryError):
        return [t("err.is_a_directory", path=path), t("err.is_a_directory.hint")]
    if isinstance(exc, FileNotFoundError):
        missing = getattr(exc, "filename", None) or path
        return [t("err.not_found", path=missing), t("err.not_found.hint")]
    if isinstance(exc, json.JSONDecodeError):
        return [t("err.bad_json", path=path)]
    if isinstance(exc, yaml.YAMLError):
        detail = str(exc).splitlines()[0] if str(exc) else t("err.yaml.no_detail")
        return [t("err.yaml"), t("err.yaml.hint", detail=detail)]
    if isinstance(exc, ValueError):
        # 走到这里基本是 profile 校验（「找不到 profile：x」「profile x 缺少必需列：y」），
        # 加载器写的提示本身已经说明了「哪一步、缺什么」，原样透出即可。
        # 注意：engine / profile 的报错目前只有中文（语言层不覆盖它们，见 messages.py 模块注释）。
        return [str(exc)]
    return None


def _report_failure(lines: list[str]) -> None:
    """打印失败提示：首行带前缀，其余缩进对齐（不随语言变，便于 CI grep）。"""
    print(t("cli.fail.prefix", first=lines[0]), file=sys.stderr)
    for line in lines[1:]:
        print(t("cli.fail.more", line=line), file=sys.stderr)


class _ArgumentParser(argparse.ArgumentParser):
    """用法错误退出码 1，而不是 argparse 默认的 2。

    2 在 FinPass 里是 ``EXIT_UNRESOLVED``（跑完了、但有未解决项，需要人看），
    CI 拿它当「软失败」放行。参数打错却拿到 2，等于把「命令写错了」报成「数据需要复核」——
    脚本会继续往下跑，错误被推迟到后面才炸。用法错误属于 ``EXIT_FAIL``。

    子解析器由 ``add_subparsers`` 按父类类型创建，所以 ``finpass run --拼错`` 也走这里。
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_FAIL, t("cli.usage.error", prog=self.prog, message=message))


def _scan_lang(argv: Sequence[str]) -> str | None:
    """构造 parser **之前**偷看一眼 ``--lang``，让 --help 和用法错误也用所选语言。

    argparse 必须先建 parser 才能解析，而 help 文案是建 parser 时取好的——所以这里只认
    这两种写法：``--lang zh`` / ``--lang=zh``。``--`` 之后是数据文件名，不再扫。
    """
    for position, token in enumerate(argv):
        if token == "--":
            break
        if token == "--lang" and position + 1 < len(argv):
            return argv[position + 1]
        if token.startswith("--lang="):
            return token.partition("=")[2]
    return None


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器（子命令 run / resume / verify）。"""
    parser = _ArgumentParser(
        prog="finpass",
        description=t("cli.description"),
    )
    parser.add_argument("--lang", choices=SUPPORTED_LANGUAGES, default=None, help=t("cli.lang.help"))
    # 版本号取自 ``finpass.__version__``（pyproject 用 dynamic 引用同一处），
    # 所以这里不写字面量——报 bug 时用户贴 `finpass --version` 就够定位。
    parser.add_argument("--version", action="version", version=f"finpass {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="{run,resume,verify}")

    p_run = subparsers.add_parser("run", help=t("cli.run.help"))
    p_run.add_argument("--lang", choices=SUPPORTED_LANGUAGES, default=argparse.SUPPRESS, help=t("cli.lang.help"))
    p_run.add_argument("input", help=t("cli.run.input"))
    p_run.add_argument("--profile", default="a_stock_daily", help=t("cli.run.profile"))
    p_run.add_argument("--out", default="out", help=t("cli.run.out"))
    p_run.add_argument("--interactive", choices=["none", "cli"], default="none", help=t("cli.run.interactive"))
    p_run.add_argument("--questions", help=t("cli.run.questions"))
    p_run.add_argument("--resume-answers", help=t("cli.run.resume_answers"))
    p_run.add_argument("--auto-only", action="store_true", help=t("cli.run.auto_only"))
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help=t("cli.run.dry_run"),
    )
    p_run.add_argument("--max-questions", type=int, default=50, help=t("cli.run.max_questions"))

    p_resume = subparsers.add_parser("resume", help=t("cli.resume.help"))
    p_resume.add_argument("--lang", choices=SUPPORTED_LANGUAGES, default=argparse.SUPPRESS, help=t("cli.lang.help"))
    p_resume.add_argument("state", help=t("cli.resume.state"))
    p_resume.add_argument("--out", default="out", help=t("cli.resume.out"))

    p_verify = subparsers.add_parser("verify", help=t("cli.verify.help"))
    p_verify.add_argument("--lang", choices=SUPPORTED_LANGUAGES, default=argparse.SUPPRESS, help=t("cli.lang.help"))
    p_verify.add_argument("passport", help=t("cli.verify.passport"))
    p_verify.add_argument("--input", help=t("cli.verify.input"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口：唯一 ``sys.exit`` 点，返回码即进程退出码。

    三道闸口都在这里，命令实现里只管「跑通」的那条路：

    1. 参数解析（用法错误 → 1，见 ``_ArgumentParser``）；
    2. 用户可自修的运行时失败 → 一句提示 + 1（见 ``_explain``）；
    3. 其余异常原样抛出——那是 bug，掩盖它只会让报 bug 的人更难查。

    ``--lang`` 优先于 ``FINPASS_LANG``；它在建 parser 前就先扫一遍（见 ``_scan_lang``），
    这样 --help 与用法错误也是所选语言。``FINPASS_DEBUG=1`` 关掉第 2 道闸口，拿回完整 traceback。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    requested = _scan_lang(argv)
    if requested is not None:
        set_language(requested)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lang is not None:
        set_language(args.lang)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    handlers = {"run": _cmd_run, "resume": _cmd_resume, "verify": _cmd_verify}
    try:
        return handlers[args.command](args)
    except Exception as exc:  # noqa: BLE001 —— 宽口是刻意的，见 ``_explain`` 的说明
        if os.environ.get("FINPASS_DEBUG"):
            raise
        lines = _explain(exc, args)
        if lines is None:
            raise
        _report_failure(lines)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
