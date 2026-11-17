"""终端文案的语言层：英文（默认）与中文两套，`FINPASS_LANG` 切换。

**为什么只有终端层**：`engine.py` 的 import 白名单是契约（`tests/test_engine_contract.py`），
引入本模块会破坏「engine 不含上层依赖」，并让 `tests/baselines/engine.sha256` 失效。
所以 engine / profile 的校验报错仍是中文——它们是给维护者看的内部合同，
要翻译需要先把它们改成**错误码**再由表现层翻，那是下一步，不是把 `messages` 塞进引擎。

用法：

    from .messages import t
    print(t("cli.summary", total=5, hit=5, idle=0, unanswered=0))

语言解析顺序：显式 `set_language()` > 环境变量 `FINPASS_LANG` > 默认 `en`。
取值 `en` / `zh`（也接受 `zh-cn` / `zh_cn` / `cn`）。未识别的值不报错，落回默认。
"""

from __future__ import annotations

import os

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "zh")

#: 显式设置的语言（测试用）；``None`` 表示跟随环境变量 / 默认值。
_override: str | None = None

_ALIASES = {
    "en": "en",
    "en-us": "en",
    "en_us": "en",
    "english": "en",
    "zh": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "cn": "zh",
    "chinese": "zh",
}

CATALOGS: dict[str, dict[str, str]] = {
    "en": {
        # ---------------------------------------------------------------- run 概览
        "cli.question_line": "Questions {total} | {hit} rules matched: {names}",
        "cli.summary": (
            "[Summary] {total} rules checked / {hit} matched / {idle} idle / {unanswered} unanswered"
        ),
        "cli.counts.fix": "fixed {n}",
        "cli.counts.flag": "flagged {n}",
        "cli.counts.unanswered": "unanswered {n} (sources: {sources})",
        "cli.counts.no_source": "(none)",
        "cli.products": "outputs: {path} | exit code {code}",
        "cli.resume.applied": "applied {n} answers from {path}",
        "cli.questions.exported": "questions written to {path}",
        "cli.missing_answers": "missing file: {name}",
        "cli.answers.no_input": "questions.yaml has no 'input' field",
        "cli.resume.done": "applied {n} answers | outputs: {path} | exit code {code}",
        # ---------------------------------------------------------------- --help
        "cli.lang.help": "output language (en / zh); overrides FINPASS_LANG",
        "cli.description": "Decisions are traceable; correct answers are not guaranteed.",
        "cli.run.help": "run the five-step core on the input: detect / decide / fix / recheck / record",
        "cli.run.input": "input CSV path",
        "cli.run.profile": "profile name or yaml path",
        "cli.run.out": "output directory",
        "cli.run.interactive": "per-question terminal prompt",
        "cli.run.questions": "export pending questions as yaml for offline filling",
        "cli.run.resume_answers": "fill decisions from a completed questions.yaml, then export",
        "cli.run.auto_only": "degraded path: run only mode: auto rules, flag the rest",
        "cli.run.dry_run": (
            "planned changes only: clean.csv stays byte-identical to the input, "
            "changes / passport still written"
        ),
        "cli.run.max_questions": "max questions answered per session",
        "cli.resume.help": "fill decisions from questions.yaml, then export",
        "cli.resume.state": "path to questions.yaml",
        "cli.resume.out": "output directory",
        "cli.verify.help": "replay from the passport; compare the output cell by cell with the changes",
        "cli.verify.passport": "path to passport.json",
        "cli.verify.input": "original CSV path (for old passports without input_path)",
        # ---------------------------------------------------------------- verify
        "cli.verify.pass": "verify passed",
        "cli.verify.pass_unresolved": "verify passed (unresolved items remain, exit code 2)",
        "cli.verify.fail": "verify failed",
        # ---------------------------------------------------------------- 交互问答
        "cli.interactive.header": "[{rule}] level={level} affects {rows} rows",
        "cli.interactive.sample": "    sample: {sample}",
        "cli.interactive.prompt": "decision (number / answer / skip / batch[:json])> ",
        "cli.interactive.eof": (
            "not enough answers: {n} questions remain (use --auto-only or questions.yaml)"
        ),
        "cli.reason.cli_default": "cli default",
        "cli.reason.skip": "cli: skip",
        "cli.reason.batch": "cli: batch_apply",
        "cli.reason.choice": "cli: {label}",
        "cli.option_out_of_range": "option out of range: {token} (valid: 1..{total})",
        # ---------------------------------------------------------------- 失败提示
        "cli.fail.prefix": "[error] {first}",
        "cli.fail.more": "        {line}",
        "cli.usage.error": "[error] bad arguments: {prog}: {message}\n       see `finpass --help`.\n",
        "cli.path.none": "(no path given)",
        "err.empty_csv": "no readable header in this file (empty, or blank lines only): {path}",
        "err.empty_csv.hint": "Use a CSV whose first row is the header, then rerun.",
        "err.parser": "this file is not a well-formed CSV: {path}",
        "err.parser.hint": (
            "Usually a row has more fields than the header (an unquoted comma) or an unclosed "
            "quote; open the first and last few lines in a spreadsheet."
        ),
        "err.encoding": "file is not UTF-8: {path}",
        "err.encoding.hint": (
            "Excel saves CSV as GBK by default: use Save as -> CSV UTF-8, or "
            "`iconv -f gbk -t utf-8 in.csv > out.csv`."
        ),
        "err.is_a_directory": "{path} is a directory, not a file",
        "err.is_a_directory.hint": "Give a full file name, e.g. `data/pingan.csv`.",
        "err.not_found": "file not found: {path}",
        "err.not_found.hint": (
            "Check the path; relative paths resolve against the current directory (`pwd`)."
        ),
        "err.bad_json": "{path} is not valid JSON; the file may be truncated or edited",
        "err.yaml": "yaml parse failed (bad indentation or a missing space after ':' in a profile "
        "or questions.yaml)",
        "err.yaml.hint": "{detail} - yaml only accepts space indentation, never tabs.",
        "err.yaml.no_detail": "(no detail)",
        # ---------------------------------------------------------------- 会话原因
        "session.reason.auto_only": "auto_only: no human decision",
        "session.reason.default_path": "default path: no human decision",
        "session.reason.yaml": "yaml fill-in",
        # ---------------------------------------------------------------- 选项文案
        "option.duplicate.first": "Keep the first row",
        "option.duplicate.last": "Keep the last row",
        "option.skip": "Skip and flag",
        "option.ohlc.skip": "Flag and skip",
        "option.ohlc.fix": "Fix from the close price",
        "option.missing.fill": "Fill with NaN",
        "option.unit.convert": "Convert the affected rows as declared",
        "option.date.convert": "Convert to {fmt}",
        "option.range.clip": "Clip to the bounds",
        # ---------------------------------------------------------------- 问题卡片
        "question.header": "[{rule}] level={level} affects {rows} rows",
        "question.default": "default: {default}",
        "question.options": "options: {options}",
        "question.samples": "samples: {samples}",
        "question.card.stats": "│ affected rows: {rows}    estimated changes: {n}",
        "question.card.samples": "│ sample: {samples}",
        "question.card.options": "│ options: {options}",
        "question.card.default": "│ default: {default}",
        "question.widget.description": "Decision",
        # ---------------------------------------------------------------- verify 明细
        "verify.missing_passport": "missing file: {name} ({path})",
        "verify.schema_warn": "[warn] profile_schema_version missing; reading as 2.0.0",
        "verify.schema_error": "profile_schema_version missing (compat table requires an error)",
        "verify.missing_artifact": "missing output: {name}",
        "verify.no_input": (
            "cannot locate the original CSV (passport has no input_path, and no same-named csv "
            "beside it)"
        ),
        "verify.old_passport": "[warn] old passport has no output_file_sha256; skipping file hash",
        "verify.hash_mismatch": "output_file_sha256 mismatch (clean.csv and passport disagree)",
        "verify.dryrun_ok": "dry-run: clean.csv is byte-identical to the input",
        "verify.dryrun_mismatch": "dry-run: clean.csv differs from the input (should be identical)",
        "verify.replay_mismatch": "replay mismatch: {detail}",
        "verify.replay_failed": "replay failed: {detail}",
        "verify.event_chain": "event chain broken: change {decision_id} has no matching event",
        "verify.columns": "columns differ: replay {replay} != clean {clean}",
        "verify.rows": "row count differs: replay {replay} != clean {clean}",
        "verify.cell_mismatch": "{row_key}.{col} (replay {replay!r} != clean {clean!r})",
        "verify.dryrun.row": "dry-run: changed row {row_key} cannot be located in the output",
        "verify.dryrun.col": "dry-run: changed column {col} is not in the output",
        "verify.dryrun.nan": "dry-run: {row_key}.{col} is NaN after replay",
        "replay.missing_dropped_key": "replay cannot find the dropped key: {row_key}",
        "replay.missing_key": "replay cannot find the key: {row_key}",
        "replay.before_mismatch": (
            "before mismatch: {row_key}.{col} expected {expected!r} got {actual!r}"
        ),
        # ---------------------------------------------------------------- diff.html
        "diff.legend.filled_nan": "filled missing values",
        "diff.legend.changed_value": "value changed",
        "diff.legend.normalized": "format / unit normalized",
        "diff.legend.dropped_row": "row dropped",
        "diff.legend.flagged_kept": "flagged and kept",
        "diff.legend.dropped_col": "column dropped",
        "diff.dropped_row": "{row_key} (dropped)",
        "diff.dropped_col": "column {col} (dropped)",
        # ---------------------------------------------------------------- 杂项
        "misc.hash_ok": "hash OK",
        "misc.replay_ok": "replay OK",
        "misc.events_ok": "events OK",
    },
    "zh": {
        "cli.question_line": "问题 {total} 个 | 命中 {hit} 条规则：{names}",
        "cli.summary": "[汇总] 检查 {total} 条规则 / 触发 {hit} 条 / 未动 {idle} 条 / 未答 {unanswered} 条",
        "cli.counts.fix": "改动 {n}",
        "cli.counts.flag": "flag {n}",
        "cli.counts.unanswered": "未问 {n}（来源：{sources}）",
        "cli.counts.no_source": "（无）",
        "cli.products": "产物：{path} | 退出码 {code}",
        "cli.resume.applied": "回填 {n} 题：{path}",
        "cli.questions.exported": "问题清单：{path}",
        "cli.missing_answers": "缺少产物：{name}",
        "cli.answers.no_input": "questions.yaml 缺少 input 字段",
        "cli.resume.done": "回填 {n} 题 | 产物：{path} | 退出码 {code}",
        "cli.lang.help": "输出语言（en / zh）；优先于 FINPASS_LANG",
        "cli.description": "承诺决策可追溯，不承诺自动得到正确答案。",
        "cli.run.help": "对输入数据跑五步内核：检测 / 决策 / 修复 / 复检 / 留痕",
        "cli.run.input": "输入数据路径（CSV）",
        "cli.run.profile": "profile 名称或 yaml 路径",
        "cli.run.out": "产物输出目录",
        "cli.run.interactive": "终端逐题问答通道",
        "cli.run.questions": "把待回答问题导出成 yaml，供离线填写",
        "cli.run.resume_answers": "直接按已填好的 questions.yaml 回填后导出",
        "cli.run.auto_only": "降级通道：只跑 mode: auto 的规则，其余打标",
        "cli.run.dry_run": "只出拟改动清单：clean.csv 与输入逐字节一致，changes / passport 照常写",
        "cli.run.max_questions": "单次会话最多回答多少题",
        "cli.resume.help": "从 questions.yaml 回填决策并继续导出",
        "cli.resume.state": "questions.yaml 路径",
        "cli.resume.out": "产物输出目录",
        "cli.verify.help": "按护照 replay，校验输出与变更记录逐单元格一致",
        "cli.verify.passport": "passport.json 路径",
        "cli.verify.input": "原始 CSV 路径（旧护照没记录 input_path 时用）",
        "cli.verify.pass": "verify 通过",
        "cli.verify.pass_unresolved": "verify 通过（存在未解决项，退出码 2）",
        "cli.verify.fail": "verify 失败",
        "cli.interactive.header": "[{rule}] 层级={level} 影响 {rows} 行",
        "cli.interactive.sample": "    样本：{sample}",
        "cli.interactive.prompt": "决策（序号 / answer / skip / batch[:json]）> ",
        "cli.interactive.eof": "决策输入不足：还剩 {n} 题未答（可用 --auto-only 或 questions.yaml）",
        "cli.reason.cli_default": "cli 默认",
        "cli.reason.skip": "cli: skip",
        "cli.reason.batch": "cli: batch_apply",
        "cli.reason.choice": "cli: {label}",
        "cli.option_out_of_range": "选项超出范围：{token}（共 {total} 项）",
        "cli.fail.prefix": "[失败] {first}",
        "cli.fail.more": "       {line}",
        "cli.usage.error": "[失败] 参数有误：{prog}: {message}\n       用法见 `finpass --help`。\n",
        "cli.path.none": "（未提供路径）",
        "err.empty_csv": "文件里没有可读的表头（空文件，或只有空行）：{path}",
        "err.empty_csv.hint": "换成「第一行是列名」的 CSV 再跑。",
        "err.parser": "这个文件不是规整的 CSV：{path}",
        "err.parser.hint": (
            "常见原因是某行比表头多出几列（字段里带了逗号却没加引号），或引号没闭合；"
            "用表格软件打开看首尾几行。"
        ),
        "err.encoding": "文件不是 UTF-8 编码：{path}",
        "err.encoding.hint": (
            "Excel 另存的 CSV 默认是 GBK：用「另存为 → CSV UTF-8」重存，"
            "或命令行 `iconv -f gbk -t utf-8 原文件 > 新文件.csv`。"
        ),
        "err.is_a_directory": "{path} 是个目录，不是文件",
        "err.is_a_directory.hint": "路径要具体到文件名，比如 `data/pingan.csv`。",
        "err.not_found": "找不到文件：{path}",
        "err.not_found.hint": "检查路径；相对路径按「当前目录」解析，先 `pwd` 看一眼你在哪。",
        "err.bad_json": "{path} 不是合法 JSON，文件可能被截断或改坏了",
        "err.yaml": "yaml 解析失败（profile 或 questions.yaml 的缩进 / 冒号后空格写错了）",
        "err.yaml.hint": "{detail}——yaml 只认空格缩进，别用 tab。",
        "err.yaml.no_detail": "（无详细信息）",
        "session.reason.auto_only": "auto_only：无人工决策",
        "session.reason.default_path": "默认路径：无人工决策",
        "session.reason.yaml": "yaml 回填",
        "option.duplicate.first": "保留第一条",
        "option.duplicate.last": "保留最后一条",
        "option.skip": "跳过打标",
        "option.ohlc.skip": "标记跳过",
        "option.ohlc.fix": "以收盘价为准修正",
        "option.missing.fill": "填 NaN",
        "option.unit.convert": "按万元折算异常行",
        "option.date.convert": "转换为 {fmt}",
        "option.range.clip": "按边界裁剪",
        "question.header": "[{rule}] 层级={level} 影响 {rows} 行",
        "question.default": "默认决策：{default}",
        "question.options": "可选答案：{options}",
        "question.samples": "样本：{samples}",
        "question.card.stats": "│ 影响行数：{rows}    预估变更：{n}",
        "question.card.samples": "│ 样本：{samples}",
        "question.card.options": "│ 可选答案：{options}",
        "question.card.default": "│ 默认建议：{default}",
        "question.widget.description": "决策",
        "verify.missing_passport": "缺少产物：{name}（{path}）",
        "verify.schema_warn": "[warn] profile_schema_version 缺失，按 2.0.0 处理",
        "verify.schema_error": "profile_schema_version 缺失（兼容表要求 error）",
        "verify.missing_artifact": "缺少产物：{name}",
        "verify.no_input": "无法定位原始 CSV（护照无 input_path，同目录也没有同名 csv）",
        "verify.old_passport": "[warn] 旧护照无 output_file_sha256，跳过文件哈希校验",
        "verify.hash_mismatch": "output_file_sha256 不一致（clean.csv 与护照不配套）",
        "verify.dryrun_ok": "dry-run：clean.csv 与输入逐字节一致",
        "verify.dryrun_mismatch": "dry-run：clean.csv 与输入不一致（应逐字节相同）",
        "verify.replay_mismatch": "replay 不一致：{detail}",
        "verify.replay_failed": "replay 失败：{detail}",
        "verify.event_chain": "事件链断裂：变更 {decision_id} 找不到对应事件",
        "verify.columns": "列集合不一致：replay {replay} != clean {clean}",
        "verify.rows": "行数不一致：replay {replay} != clean {clean}",
        "verify.cell_mismatch": "{row_key}.{col}（replay {replay!r} != clean {clean!r}）",
        "verify.dryrun.row": "dry-run：变更行 {row_key} 在产物中不可定位",
        "verify.dryrun.col": "dry-run：变更列 {col} 不在产物里",
        "verify.dryrun.nan": "dry-run：{row_key}.{col} replay 后为 NaN",
        "replay.missing_dropped_key": "replay 找不到被删主键：{row_key}",
        "replay.missing_key": "replay 找不到主键：{row_key}",
        "replay.before_mismatch": "before 不一致：{row_key}.{col} 期望 {expected!r} 实际 {actual!r}",
        "diff.legend.filled_nan": "缺失值填充",
        "diff.legend.changed_value": "值被修改",
        "diff.legend.normalized": "格式/单位归一",
        "diff.legend.dropped_row": "整行删除",
        "diff.legend.flagged_kept": "跳过打标",
        "diff.legend.dropped_col": "整列删除",
        "diff.dropped_row": "{row_key}（已删除）",
        "diff.dropped_col": "列 {col}（已删除）",
        "misc.hash_ok": "hash OK",
        "misc.replay_ok": "replay OK",
        "misc.events_ok": "events OK",
    },
}


def normalize(language: str | None) -> str:
    """把各种写法归一成 ``en`` / ``zh``；不认识或为空时返回默认语言。"""
    if not language:
        return DEFAULT_LANGUAGE
    return _ALIASES.get(str(language).strip().lower(), DEFAULT_LANGUAGE)


def language() -> str:
    """当前语言：显式设置优先，其次 ``FINPASS_LANG``，最后默认 ``en``。"""
    if _override is not None:
        return _override
    return normalize(os.environ.get("FINPASS_LANG"))


def set_language(language_: str | None) -> str:
    """显式设置语言（``None`` = 恢复跟随环境变量），返回生效值。"""
    global _override
    _override = None if language_ is None else normalize(language_)
    return language()


def current() -> str:
    return language()


def t(key: str, **kwargs: object) -> str:
    """取一条文案并格式化。缺键时抛 ``KeyError``（宁可测试崩，也不要静默出空白）。"""
    catalog = CATALOGS[language()]
    template = catalog[key]
    return template.format(**kwargs) if kwargs else template
