# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/).

During alpha (`0.0.x`), minor releases may include incompatible interface and output-format changes;
such changes are called out explicitly.

## [Unreleased]

## [0.0.3] - 2026-11-17

Bilingual release, published to PyPI.

### Added

- Bilingual terminal layer: `src/finpass/messages.py` holds aligned `en` / `zh` catalogs with
  `t(...)`, `set_language()`, and missing-key errors. English is the default; select Chinese with
  `FINPASS_LANG=zh` or the new `--lang {en,zh}` switch (also accepted after the subcommand, and
  applied to `--help` and usage errors). An explicit `--lang` is recorded in
  `passport.json.cli_replay_cmd` for reproducible runs.
- English question cards, options, reasons, log lines, verify messages, and `diff.html` legend;
  problem cards (`Question.render()`) and the widget label follow the selected language too.
- `docs/after-log-cli.en.txt`: raw English terminal transcript of the default path; the existing
  `docs/after-log-cli.txt` is kept as the Chinese baseline.
- Bilingual test guards: catalog key/placeholder parity, language precedence and aliases, English
  assertions for narration, verify, diff, question cards, date-option text, and CLI switches.

### Changed

- `README.md` now documents the language switches and shows English terminal samples; the old
  "English catalog on the roadmap" note is gone.
- `tests/test_capability_map.py` parses the English coverage table in `docs/internals.md`
  (`## Rule coverage`), following its move out of `README.md` in 0.0.2.
- Machine-readable outputs and exit-code semantics are unchanged; profile-declared `options` /
  `default` remain user data and are never translated.

## [0.0.2] - 2026-11-16

Documentation and packaging only; no behaviour change.

### Changed

- `README.md` rewritten in English for readers evaluating the tool: positioning, fit, quickstart,
  outputs, exit codes, input requirements, limitations, comparison. Implementation details and
  refactor invariants moved to `docs/internals.md` without rewording the facts.
- Added `SECURITY.md` (security model, supported versions, private reporting) and this file.
- `pyproject.toml` gains `[project.urls]` (Homepage, Repository, Issues, Changelog).
- `.gitignore` covers `.pytest_cache/`, `.ruff_cache/`, `.coverage`, and the local tool workspace.

## [0.0.1] - 2026-11-16

First release on TestPyPI.

### Added

- `finpass` CLI with `run`, `resume`, and `verify`; `--version` reads the package version from a
  single source of truth.
- Five-step kernel skeleton `detect → dispatch → fix → reverify → audit`. The first three are
  implemented; `reverify` and `audit` raise `NotImplementedError` and are not on any CLI path.
- Domain knowledge lives in `profiles/*.yaml`: `a_stock_daily`, `a_stock_valuation`, and `futures`
  are usable; `fund_nav` is a placeholder that `load_profile` rejects. `engine.py` contains no
  finance vocabulary — a guard test enforces this.
- Six rule types: `missing_token`, `duplicate_key`, `ohlc_contradiction`, `unit_scale`,
  `date_format`, `range_check`. A rule may declare `columns` to narrow its scan range.
- Seven outputs: `clean.csv`, `diff.html`, `passport.json`, `events.jsonl`, `changes.parquet`,
  `report.before.json`, `report.after.json`.
- Three-state exit codes: `0` fully resolved, `2` completed with flagged or unanswered items (soft
  failure), `1` command did not run.
- Terminal log: one line per `(rule_id, action)` group, each carrying action, target, reason, and
  result; the last line points to `events.jsonl`. `event_id` is no longer printed.
- `finpass verify`: file-byte hash, replay from `changes`, and event-chain closure.

### Known limitations

- `diff.html` shows post-change values only; cell-level `before → after` requires `changes.parquet`.
- Input is UTF-8 CSV only; no GBK transcoding, and `.xlsx` must be saved as CSV UTF-8 first.
- Column synonyms are not supported (the schema keeps `synonyms`, but nothing consumes it).
- No GUI; questions are answered via `--interactive cli` or `--questions` + `resume`.
