# FinPass

![status: alpha](https://img.shields.io/badge/status-alpha-orange)
![python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
[![license: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/XZH198913/finpass/blob/main/LICENSE)

**Clean a messy financial CSV into a dataset you can hand off, and leave behind a record that proves what changed.**

FinPass is a command-line tool. One command produces four things: a cleaned CSV, a cell-level change
table (`before → after`), a replayable event stream, and a quality passport that `finpass verify` can
check against the output file.

It does not decide your business rules for you — what each column means, which unit is correct, what
counts as out of range, and which columns form the key all live in your own `profiles/*.yaml`.
It does not pretend to be fully automatic either: when a cell cannot be decided safely, it is flagged
and kept (`flagged_kept`) rather than silently rewritten.

> Traceable decisions, not guaranteed answers.

**Status: `0.0.2`, alpha, TestPyPI only.** Interfaces and output formats may still change. Not
recommended for decisions that need to be defensible in production yet.

### What it is good at

- **Explaining itself.** Every change is reported as action, target, reason, and result — readable
  without knowing the internals, and traceable back to a decision event.
- **Repeating itself.** The same input and the same decisions produce byte-identical output;
  `verify` recomputes the hash and replays the change set to prove it.
- **Staying out of your domain.** The engine knows shapes (keys, roles, declared scan ranges), not
  vocabulary: `engine.py` contains no PE, PB, or market-cap terms, and a test enforces that.
- **Refusing to guess.** Unresolved items stay flagged and are reported through the exit code, so a
  soft failure is visible in CI instead of buried in the output.

## Install

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple \
            finpass==0.0.2
```

`--extra-index-url` is required: TestPyPI only hosts FinPass itself, while its dependencies
(`pandas`, `pyarrow`, `pyyaml`) come from PyPI. Without it, install fails to resolve them.

Requires Python 3.10 or newer. From a source checkout: `pip install -e ".[dev]"`.
If `finpass` is not on your `PATH` (common with Windows `Scripts` directories), `python -m finpass`
is equivalent; every command below works either way.

## Quickstart

The repository ships a small deliberately messy CSV: duplicate dates, `--` placeholders,
ambiguous units, and an OHLC contradiction.

```bash
finpass run examples/pingan_bank_daily_dirty.csv --out out/quickstart --auto-only
finpass verify out/quickstart/pingan_bank_daily_dirty.passport.json
```

`--auto-only` means "do not ask me anything", so the three undecidable items stay flagged:

```text
问题 6 个 | 命中 5 条规则：date_format, duplicate_key, missing_token, ohlc_contradiction, unit_ambiguity
[汇总] 检查 5 条规则 / 触发 5 条 / 未动 0 条 / 未答 0 条
[去重]     主键「日期」发现 1 行重复 → 打标保留，原因：auto_only：无人工决策
[缺失填充] 「收盘价、成交额_元」2 行含占位符「--」「暂无」 → 填充为 NaN
[单位换算] 「成交额_元」1 行发现单位歧义（含异常量级） → 打标保留，原因：auto_only：无人工决策
[日期归一] 「日期」1 行含格式不符的值（如「2024/03/18」） → 转换为 datetime64[ns]，格式 %Y-%m-%d
[矛盾打标] 「最高价、最低价、收盘价」1 行矛盾 → 打标保留，原因：auto_only：无人工决策
详细决策见 events.jsonl（按 rule_id 可直接检索原事件）
改动 3 / flag 3 / 未问 0
产物：out/quickstart | 退出码 2
```

> Terminal messages are currently Chinese. An English message catalog is on the roadmap; the
> machine-readable outputs (`events.jsonl`, `changes.parquet`, `passport.json`) are language-neutral.

### Outputs

Seven files land in `--out`:

| File | Contents | When you need it |
|---|---|---|
| `*.clean.csv` | The cleaned dataset | This is the deliverable |
| `*.changes.parquet` | Cell-level changes: `row_key`, `col`, `before`, `after`, `change_type`, `rule_id` | To see what a cell used to be |
| `*.events.jsonl` | One line per decision: rule, reason, affected rows | Audit, review, retrieval |
| `*.diff.html` | The cleaned table with per-cell highlighting; hover shows the rule and reason | To see at a glance which cells moved |
| `*.passport.json` | Quality passport: input path and hash, output hash, counts, replay command | Ship it with the data; `verify` reads only this |
| `*.report.before.json`, `*.report.after.json` | Profiles before and after cleaning (rows, dtypes, missingness) | To measure what was cleaned |

## What `verify` checks

```text
hash OK
replay OK
events OK
verify 通过（存在未解决项，退出码 2）
```

Three independent checks: the SHA-256 of `clean.csv` matches the passport; replaying `changes`
reconstructs that file cell by cell; every change resolves to a decision event with no gaps in the
chain.

## Exit codes

| Code | Meaning | What CI should do |
|---|---|---|
| `0` | Everything resolved, nothing flagged | Pass |
| `2` | Run completed, but items remain flagged or unanswered | Soft failure: outputs are usable, a human must look |
| `1` | The command did not run (bad path, encoding, column name, profile, or argument) | Fail: a single message on stderr, no traceback |

**Exit code 2 is not a failure.** The data was not damaged and no flags were lost. To reach `0`,
answer the open questions:

```bash
# Answer in the terminal (each question lists its options; Enter accepts the default)
finpass run examples/pingan_bank_daily_dirty.csv --out out/quickstart --interactive cli

# Or export the questions, fill them in offline, then resume
finpass run examples/pingan_bank_daily_dirty.csv --out out/quickstart --questions q.yaml
finpass resume q.yaml --out out/quickstart
```

Exit code `1` is written for people who do not read tracebacks — for example:

```text
[失败] 找不到文件：nope.csv
       检查路径；相对路径按「当前目录」解析，先 `pwd` 看一眼你在哪。
```

Set `FINPASS_DEBUG=1` to see the original stack trace when reporting a bug.

## Input requirements

| Requirement | Detail |
|---|---|
| Encoding | UTF-8. GBK is not silently transcoded; the run fails with an explicit message |
| Format | CSV. Save `.xlsx` as "CSV UTF-8" first |
| Column names | Must match the profile's `target_state` keys exactly. Synonyms are not supported |
| Key | Declared in the profile with `role: key` (composite keys allowed); duplicate rows are handled by the `duplicate_key` rule |
| Rules | Only rules declared in the profile's `business_rules` run |

A missing column fails with a message naming that column. Rename columns during data preparation —
`scripts/prepare_valuation_data.py` is an example.

## Limitations

1. **`diff.html` shows only post-change values.** Deleted rows and columns keep their original
   values; modified cells do not. For cell-level `before → after`, read `changes.parquet` and match on
   `row_key` + `col`. Two-column rendering is scheduled after 0.0.x.
2. **No column synonyms.** The profile schema keeps a `synonyms` key for compatibility, but nothing
   consumes it, so the built-in profiles no longer declare one: a promise with no implementation is
   worse than no promise.
3. **No graphical interface.** Ask questions with `--interactive cli`, or export them with
   `--questions` and fill them in offline. `--auto-only` finishes without a human, which means zero
   human decisions and every undecidable item flagged.

## How it compares

Constraint tools answer "does this data satisfy the contract?". FinPass answers "once we find a
problem, how is it fixed, who decided, and can it be replayed?". The two compose: point a contract
checker at FinPass output.

| | Contract checks | Cell-level fixes | Replayable fixes | Decision audit |
|---|---|---|---|---|
| pandas script | Write your own | Write your own | Usually none | None |
| Great Expectations / pandera | Yes | No | No | No |
| FinPass | Declared in the profile | Yes | Yes | Yes |

Positions are as documented by each project as of November 2026.

## Documentation

- [`docs/internals.md`](https://github.com/XZH198913/finpass/blob/main/docs/internals.md) —
  architecture, rule coverage matrix, and the invariants each refactor must keep
- [`docs/`](https://github.com/XZH198913/finpass/tree/main/docs) — design decisions and trade-offs,
  stage by stage (Chinese), plus the release runbook
- [`CHANGELOG.md`](https://github.com/XZH198913/finpass/blob/main/CHANGELOG.md) — version history
- [`SECURITY.md`](https://github.com/XZH198913/finpass/blob/main/SECURITY.md) — security model and
  how to report a vulnerability
- [`notebooks/`](https://github.com/XZH198913/finpass/tree/main/notebooks) — end-to-end walkthrough
  and a self-contained kernel

## License

Apache-2.0; see [`LICENSE`](https://github.com/XZH198913/finpass/blob/main/LICENSE). Chosen over MIT
because FinPass is about auditable cleaning, and commercial adopters care about patent risk:
Apache-2.0 section 3 grants an explicit patent license with a retaliation clause, which MIT does not.
