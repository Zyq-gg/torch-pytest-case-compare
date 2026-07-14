---
name: torch-pytest-case-compare
description: Compare, normalize, deduplicate, and analyze PyTorch pytest cases from mixed XLSX/CSV spreadsheets and pytest/run_test.py logs. Use when collecting case triads such as py name/class/op name or test_file/class_name/case_name, preserving spreadsheet provenance, finding CSV cases not present in marked XLSX workbooks, extracting stable failures from run_test logs, locating case-specific error evidence, or explicitly rerunning selected pytest cases and filling results back into CSV.
---

# Torch Pytest Case Compare

Use spreadsheet comparison as the primary workflow. Add log evidence or pytest
reruns only when requested or when comparison results need deeper analysis.

## Compare Spreadsheets

Run the compatibility entry point:

```bash
python scripts/compare_torch_pytest_sheets.py SHEET_DIR \
  --csv SHEET_DIR/pytest_failures.csv \
  --out-dir OUTPUT_DIR
```

This preserves the complete `torch-pytest-sheet-compare` behavior:

- Detect triad headers such as `py name/class/op name` and
  `test_file/class_name/case_name` in every XLSX sheet.
- Normalize paths and remove repeated leading `test/` for comparison only.
- Keep raw values and every source workbook, sheet, and row.
- Write all rows, unique rows, CSV-only rows, an analyzed CSV, and summary JSON.
- Find a fully empty CSV column for `case标记` before appending one.
- Keep source locations in `case标记`; keep error evidence rather than source
  row locations in `问题结论`.

Use `--marker-column-name NAME` to change the marker header.

## Extract Failed Cases From Logs

```bash
python scripts/extract_pytest_failures.py LOG... --output failures.csv
python scripts/extract_pytest_failures.py LOG... --output failures.xlsx
```

Use `--mode auto` by default. Auto mode recognizes run_test.py structure such
as `Name: tests to run`, transient-rerun summaries, and stable-failure lines;
for those logs it extracts only `The following tests failed consistently` so
intermediate failures that later pass are not reported. Otherwise it extracts
standalone pytest `FAILED`/`ERROR` summary rows. Use `--mode run-test` or
`--mode pytest` to force a parser.

## Add Log Evidence

```bash
python scripts/analyze_pytest_cases.py \
  --input OUTPUT_DIR/pytest_failures_analyzed.csv \
  --logs /path/to/run_test*.log \
  --output OUTPUT_DIR/pytest_failures_log_analyzed.csv \
  --only-marker 新增
```

The script uses the same normalized case key as spreadsheet comparison. It
writes log error, source, and confidence columns separately. Only high- and
medium-confidence matches update `问题类别` and `问题结论`. Use
`--no-update-analysis` to add evidence without changing those two columns.

## Rerun Selected Cases

Reruns are explicit because PyTorch tests can be expensive:

```bash
python scripts/rerun_pytest_cases.py \
  --input OUTPUT_DIR/pytest_failures_analyzed.csv \
  --output OUTPUT_DIR/pytest_failures_rerun.csv \
  --repo /workspace/pytorch \
  --env /path/to/env.sh \
  --only-marker 新增 \
  --timeout 300
```

The script executes an exact `file::class::case` nodeid, deduplicates normalized
keys, stores one full log per case, and fills rerun result columns. It writes a
separate output by default. `--in-place` creates a timestamped backup first.
Use `--limit N` for a bounded trial.

## Supporting Tools

- Use `scripts/check_run_test_log_status.py` to classify run_test files as
  `ok`, `error`, `check`, or `interrupted`.
- Use `scripts/extract_inductor_unique_errors.py` for a compact Inductor error
  taxonomy instead of case-by-case output.

Read [references/workflows.md](references/workflows.md) when changing schemas,
matching logic, output contracts, or rerun behavior.
