# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/).

During alpha (`0.0.x`), minor releases may include incompatible interface and output-format changes;
such changes are called out explicitly.

## [Unreleased]

### Changed

- `README.md` rewritten in English for readers evaluating the tool: positioning, fit, quickstart,
  outputs, exit codes, input requirements, limitations, comparison. Implementation details and
  refactor invariants moved to `docs/internals.md` without rewording the facts.
- Added `SECURITY.md` (security model, supported versions, private reporting) and this file.

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
