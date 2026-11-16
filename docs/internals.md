# Internals and invariants

This document used to live in `README.md`. It answers "how is this organised, and why do we believe a
refactor changed nothing" — not "should I use this tool". The facts are unchanged from the README
version; only the language and the framing differ.

## Layout

```text
finpass/
├── README.md
├── LICENSE              # Apache-2.0, full text
├── pyproject.toml
├── .gitignore
├── src/finpass/
│   ├── __init__.py
│   ├── models.py
│   ├── engine.py        # five-step kernel, no finance vocabulary
│   ├── profile.py       # profile loading and validation
│   ├── session.py       # session orchestration + rule dispatch
│   ├── passport.py      # output I/O, replay, verify
│   ├── diff.py          # key-aligned diff rendering
│   ├── cli.py
│   ├── narrate.py       # terminal log: templates + derivation
│   ├── passport_compat.py  # compatibility constants (stage 0, wired in 7a)
│   ├── check_counts.py     # count semantics and denominator ledger
│   └── profiles/
│       ├── a_stock_daily.yaml
│       ├── a_stock_valuation.yaml   # cross-sectional wide table; key is not a date
│       ├── futures.yaml
│       └── fund_nav.yaml
├── scripts/
│   └── prepare_valuation_data.py    # xlsx → normalised UTF-8 CSV (data prep, not in src/)
├── docs/
│   ├── publish.md                   # PyPI release runbook
│   └── spec-stage*-追认.md          # per-stage decisions and trade-offs
├── notebooks/
│   ├── 00_quickstart.ipynb
│   ├── 01_minimal_kernel.ipynb
│   ├── 02_interaction_queue.ipynb
│   ├── 03_diff_and_passport.ipynb
│   ├── 04_profile_authoring.ipynb
│   └── 05_headless_and_ci.ipynb
├── examples/
├── tests/               # 32 test files, 232 cases collected (5 xfail), no pytest.skip
│   ├── baselines/engine.sha256      # byte baseline for engine.py
│   ├── fixtures/demo.expected.json  # fingerprint baseline for session outputs
│   └── _fixture.py                  # baseline generation / self-check
└── out/                  # runtime output directory, only .gitkeep is tracked
```

## Rule coverage

Landing status for the six rule types across four profiles. **The table is the source of truth**: it
is parsed and asserted by `tests/test_capability_map.py`. Cells may only contain `完整` (complete),
`部分` (partial), `未做` (not applicable to this profile), or `N/A`; the aggregate column takes the
worst non-`N/A` cell (weakest-link rule).

| rule_type | a_stock_daily | a_stock_valuation | fund_nav | futures | Overall |
|---|---|---|---|---|---|
| missing_token | complete | complete | N/A | complete | complete |
| duplicate_key | complete | complete | N/A | complete | complete |
| ohlc_contradiction | complete | n/a to profile | N/A | complete | incomplete |
| unit_scale | complete | n/a to profile | N/A | n/a to profile | incomplete |
| date_format | complete | n/a to profile | N/A | complete | incomplete |
| range_check | n/a to profile | complete | N/A | complete | incomplete |

How to read it:

- **complete** — the rule type is implemented, and this profile declares it in `business_rules`;
- **n/a to profile** — the type is implemented but this profile does not declare it. `unit_scale` is
  declared only for the A-share amount column (`成交额_元`); `range_check` only for futures
  (`limit_up_down` on `涨跌幅_%`) and valuation (`pe_range` / `pb_range`). The valuation profile has
  no OHLC columns, no date column, and its market-cap unit is already yuan, so three columns are
  intentionally empty — declaring those rules would run them over nothing, not fix a gap;
- **N/A** — the profile itself is unusable. `fund_nav.yaml` is a placeholder that `load_profile`
  rejects. `N/A` says nothing about engine support; that would appear as "n/a to profile" instead.
  `fund_nav` was not migrated to schema 2.1.0 because what it lacks is net-asset-value semantics, not
  structure; the rationale and follow-up are in `docs/spec-stage3-追认.md`.

## Supporting a new data shape

Keep three layers apart; conflating them turns "pluggable" into "everything goes into the engine".

| Layer | Where | Examples | Who changes it |
|---|---|---|---|
| Data preparation | scripts outside the repo | xlsx → CSV, flatten multi-level headers, drop trailing notes | The user |
| Shape primitives | `src/finpass/` | "scan only these columns", "the key can be any column", "ignore N trailing rows" | Engine maintainers; every primitive needs a real dataset behind it |
| Domain knowledge | `profiles/*.yaml` | which column is the key, whether `--` counts as missing, a plausible PE range | Domain experts |

1. **Most new profiles do not need engine changes.** New column names, new rules, new vocabularies
   are all `profiles/*.yaml` work. `futures.yaml` and `a_stock_valuation.yaml` were added that way,
   and the byte baseline for `engine.py` (`tests/baselines/engine.sha256`) did not move.
2. **When does the engine need to change?** When a new dataset brings a shape assumption the engine
   cannot express. The test is not "it crashes" but "it runs and the result is meaningless". The
   valuation dataset is the example: `missing_token` used to ignore `columns` and scan every column,
   turning 74 indicator columns into 71 questions — one per column — which no human can answer. That
   was not a bad profile; the engine was missing a primitive.
3. **The primitive added for it: a declarable scan range.** Declaring `columns: [...]` in
   `business_rules` restricts a rule to those columns; omitting it keeps the old behaviour
   (`missing_token` scans everything). The valuation profile dropped from 71 questions to 4:
   ```bash
   finpass run out/valuation-check/a_stock_valuation_clean.csv \
     --profile a_stock_valuation --out out/valuation --auto-only
   ```
4. **Boundary: the engine knows shapes, not domains.** PE, PB, and market-cap never appear in
   `engine.py` (a guard test scans for them). The engine knows that the caller named some columns; it
   does not know what they mean. Likewise FinPass makes data analysable, it does not analyse data: it
   turns a valuation table into a structurally clean CSV and does not decide whether a stock is cheap
   or whether a negative PE should be fixed.

## Notebooks

| Notebook | Direction | Reader | Purpose | Priority |
|---|---|---|---|---|
| 00_quickstart | Product entry | New users, evaluators | Value in ten minutes: no cleaning code, four API calls, a clean CSV with a passport; also the P0 acceptance script | P0 |
| 01_minimal_kernel | Conceptual baseline | Contributors | A 200-line self-contained kernel proving five steps + event log + key-aligned diff + passport at minimum scale; does not import `finpass` | P0 |
| 02_interaction_queue | Interaction queue | Contributors, product design | Aggregation levels (strategy / column / point), queue ordering, deduplication, batch application | P1 |
| 03_diff_and_passport | Diff and passport | Contributors, auditors | Key-aligned six-colour diff rendering, passport field contract, replay | P1 |
| 04_profile_authoring | Profile authoring | Domain experts | A new domain is a YAML change: vocabulary, units, rule versions, regression tests | P2 |
| 05_headless_and_ci | Headless and CI | Engineering users | Degraded Q&A without widgets, CLI batch runs, exit codes, CI integration | P2 |

Build order: **01 → extract `src/finpass/` → 00**. Both share the input
`examples/pingan_bank_daily.csv` and write to `out/01/` and `out/00/` respectively, so they do not
contaminate each other.

## Current state

- `notebooks/01_minimal_kernel.ipynb`: implemented. Self-contained kernel (no `finpass` import), two
  markdown cells plus ten code cells, 232 lines of code; the replay assertion at the end is its
  acceptance test.
- `notebooks/00_quickstart.ipynb`: implemented. One promise cell, six code cells (import, open,
  start, interact, export, self-check), one acceptance cell; input
  `examples/pingan_bank_daily_dirty.csv`, outputs under `out/00/`.
- `src/finpass/session.py`: implements `open`, `start`, `next`, `answer`, `export`, and
  `profile_info`. Detection is delegated to `engine.detect` (six built-in rules); the session layer
  only wraps `Gap` into `Question` (option wording, aggregation level, default) and runs the
  human interaction.
- The rest of `src/finpass/`: `passport.py` (output I/O, replay, verify), `diff.py`, `profile.py`,
  and `cli.py` are implemented. `engine.py` has landed `detect` (stage 4b), `dispatch` and `fix`
  (stage 4c), and `aggregate`; `reverify` and `audit` still raise `NotImplementedError` and have no
  call site — the CLI never reaches them. Rule parameters and domain knowledge come only from
  `profiles/*.yaml`.
- `tests/`: 32 test files, 232 cases collected, 5 marked `xfail` for known boundaries, all real
  assertions with no `pytest.skip` fallback. The failure-path cases in `test_cli.py` pin both the
  message text and the exit code; pinning only the exit code would still pass if a traceback were
  printed.

## Verification invariants

`finpass verify` does two things: the file-byte SHA-256 of `clean.csv`, and a replay comparison driven
by `changes`.

The passport stores only `output_file_sha256`, never `output_hash`: an in-memory fingerprint cannot
be recomputed from the file and would create a second set of criteria. Leftover `output_hash` fields
in old passports are ignored; an old passport without `output_file_sha256` only warns — it never falls
back to the in-memory hash.

Whether a refactor changed behaviour is decided by `tests/fixtures/demo.expected.json`: run the fixed
decision set and all seven outputs must be byte-identical
(`python tests/_fixture.py --check`). `engine.py` has its own byte baseline,
`tests/baselines/engine.sha256`.

Stage 4b (detection move) adds two finer references: `tests/fixtures/detect_reference.json` (raw
`Gap`s from `engine.detect`, `python tests/_detect_reference.py --check`) and
`tests/fixtures/queue_reference.json` (the aggregated queue,
`python tests/_queue_reference.py --check`). The first catches detection changes, the second catches
wrapping and aggregation changes; both are wired into `pytest`.

Stage 4c (dispatch/fix split) adds an intermediate-state reference:
`tests/fixtures/dispatch_reference.json` (which changes each decision should produce,
`python tests/_dispatch_reference.py --check`). The final output is insensitive to dispatch computing
the right value by accident; only this reference catches it.

Stage 4 log layer: terminal output is a translation of `changes` produced by `src/finpass/narrate.py`,
checked by `tests/test_narrate.py` (four elements per line) and `tests/test_stage2_event_log.py`
(reconciliation with `changes`). Rationale and trade-offs are in `docs/spec-stage4-log-追认.md`.
