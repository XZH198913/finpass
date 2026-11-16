# Security Policy

FinPass handles financial data. This document covers two things: what the tool does with your data,
and how to report a vulnerability.

## Security model — what it does not do

- **Runs entirely locally.** No network requests, no uploads, no telemetry or usage collection.
- **Reads one input, writes one directory.** It reads the CSV you name and writes to `--out`.
  It does not modify the input file and does not touch other paths.
- **Does not execute data.** CSV content is parsed as data: no `eval`, no pickle loading, no formula
  evaluation.
- **Does not guess encodings.** Non-UTF-8 input fails with an explicit message instead of being
  silently transcoded.

Confidentiality of the data itself is your deployment's responsibility: outputs such as `clean.csv`
and `changes.parquet` still contain the original values, so handle them under the same rules as the
source file.

## Supported versions

| Version | Status | Security fixes |
|---|---|---|
| `0.0.x` | alpha (TestPyPI) | Latest patch release only |
| `< 0.0.1` | Not released | Not applicable |

Interfaces and output formats may change during alpha. Security fixes ship as a new patch release;
older releases are not backported.

## Reporting a vulnerability

**Please do not open a public issue.** Report privately, and include: affected version, reproduction
steps (a minimal CSV is ideal), and the impact you believe it has — data exposure, tampered output,
arbitrary file write, and so on.

- Email: **zhihanxu198913@outlook.com**
- Alternatively, use the repository's **Security → Report a vulnerability** private advisory.

We will acknowledge within 7 days. Once a fix is released, it is recorded in `CHANGELOG.md` without
disclosing exploitation details.

## Supply chain

Runtime dependencies are `pandas`, `pyarrow`, and `pyyaml`, installed from the official Python
Package Index. Builds use `python -m build` and `twine`; the wheel contents are spot-checked before
every upload, as described in `docs/publish.md`.
