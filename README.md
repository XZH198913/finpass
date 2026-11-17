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

**Status: `0.0.3`, alpha.** Interfaces and output formats may still change. Not
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
pip install finpass==0.0.3
```

Earlier `0.0.1` / `0.0.2` were TestPyPI-only; `0.0.3` is published on PyPI, so no extra index is
needed (`pandas`, `pyarrow`, and `pyyaml` resolve normally).

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
Questions 6 | 5 rules matched: date_format, duplicate_key, missing_token, ohlc_contradiction, unit_ambiguity
[Summary] 5 rules checked / 5 matched / 0 idle / 0 unanswered
[dedupe] key "日期": duplicates found (1 row involved) → flagged and kept, reason: auto_only: no human decision
[fill]   "收盘价, 成交额_元" 2 rows with placeholders '--' or '暂无' → filled with NaN
[unit]   "成交额_元" 1 row with ambiguous units (contains an unexpected magnitude) → flagged and kept, reason: auto_only: no human decision
[date]   "日期" 1 row with non-conforming values (e.g. 2024/03/18) → converted to datetime64[ns], format %Y-%m-%d
[ohlc]   "最高价, 最低价, 收盘价" 1 row with contradictions → flagged and kept, reason: auto_only: no human decision
full decisions: events.jsonl (search by rule_id)
fixed 3
flagged 3
unanswered 0 (sources: (none))
outputs: out\quickstart | exit code 2
```

Column names and values in quotes (`日期`, `--`, …) come from your CSV and profiles; they are user
data and are never translated. See [`docs/after-log-cli.en.txt`](docs/after-log-cli.en.txt) for a full
default-path transcript (and `docs/after-log-cli.txt` for the Chinese baseline).

### Language

Terminal messages are available in English (default) and Chinese. Two switches select the language:

```bash
FINPASS_LANG=zh finpass run examples/pingan_bank_daily_dirty.csv --out out/zh
finpass --lang zh run examples/pingan_bank_daily_dirty.csv --out out/zh
```

`--lang {en,zh}` takes precedence over `FINPASS_LANG`, which in turn falls back to `en`. The
environment variable also accepts `zh-cn`, `zh_cn`, `cn`, and `chinese` (and `en-us`, `english`).
`--lang` works before or after the subcommand, and applies to `--help` and error messages too.
When `--lang` is used explicitly, the passport's `cli_replay_cmd` records it so the run can be
reproduced in the same language.

Only human-facing text changes: terminal output, question cards, options, reasons, and the
`diff.html` legend. Machine-readable outputs (`events.jsonl`, `changes.parquet`, `passport.json`)
keep the same field names, enum values, and row keys in both languages. Internal contract errors
from the engine and profile loader stay in their original wording.

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
verify passed (unresolved items remain, exit code 2)
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
[error] file not found: nope.csv
        Check the path; relative paths resolve against the current directory (`pwd`).
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
