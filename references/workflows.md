# Workflow Reference

## Contents

1. Case identity
2. Spreadsheet outputs
3. Log extraction
4. Log matching
5. Pytest reruns
6. Validation

## Case Identity

Use the normalized tuple `(py_name, class_name, op_name)` everywhere.

- Normalize whitespace in all fields.
- Normalize `\` to `/` in the Python path.
- Remove leading `./`, `/`, and repeated `test/` from the comparison path.
- Preserve raw values for output and pytest execution.
- Require a recognized class column in spreadsheet headers, but allow an empty
  class value for function-style pytest nodeids.
- Require non-empty Python and case/op values. This skips owner and separator
  rows that only populate column A.

Do not add broader fuzzy matching to the case key. Put historical path aliases
or project-specific equivalence rules behind an explicit option.

## Spreadsheet Outputs

`compare_torch_pytest_sheets.py` writes:

- `marked_cases_all_rows.csv`: every XLSX case row and raw row JSON.
- `marked_cases_unique.csv`: normalized unique cases and all source locations.
- `csv_new_cases.csv`: input CSV rows absent from the XLSX case set.
- `<csv-stem>_analyzed.csv`: original rows plus `case标记`, `问题类别`, and
  `问题结论`.
- `summary.json`: inputs, counts, output paths, and normalization rules.

Preserve this contract when extending comparison behavior. In particular:

- Put workbook/sheet/row provenance in `case标记` or structured source fields.
- Put source-sheet error and analysis text in `问题结论`.
- Reuse a CSV column only when its header and every row value are empty.
- Do not recreate the removed intermediate annotated CSV.

## Log Extraction

`extract_pytest_failures.py` supports two log shapes:

- run_test.py: parse the final `The following tests failed consistently` list.
- standalone pytest: parse `FAILED` or `ERROR` summary rows.

In auto mode, run_test structure such as `Name: tests to run`, a run_test.py
command, `FAILED CONSISTENTLY`, or a transient-rerun summary selects the
run_test parser for that file. Only the stable-failure list becomes output.
This intentionally excludes failures that later succeeded in a fresh process,
including logs that have no stable-failure line at all.

CSV/XLSX extraction fields are:

`source_log`, `source_line`, `test_file`, `class_name`, `case_name`,
`error_type`, `error_message`, `nodeid`, and `raw`.

CSV/XLSX extraction and spreadsheet comparison use only the Python standard
library.

## Log Matching

`analyze_pytest_cases.py` builds a reusable index from failure headers and
nodeid-bearing lines. Exclude aggregate lines such as run plans, final stable
failure lists, and `FAILED CONSISTENTLY` markers from evidence candidates.

Score candidates using exact nodeid, path, class, case name, and strong error
markers. Select the best candidate after scoring all candidates.

- `high`: exact, case-specific evidence suitable for analysis updates.
- `medium`: sufficiently specific evidence suitable for analysis updates.
- `low`: store as provenance only; do not update the conclusion.

Write provenance to `日志来源` and `日志匹配置信度`. Do not embed source line
locations in `问题结论`.

## Pytest Reruns

Use reruns only after log matching is insufficient or the user requests current
evidence.

- Build pytest nodeids from raw table values, not normalized paths.
- Try the raw file path and then `test/<path>` under the requested repo.
- Include class in the nodeid when present.
- Group duplicate rows by normalized key and run each key once.
- Save full output and a JSONL record for every executed case.
- Require an output path unless `--in-place` is explicit.
- Back up the CSV before an in-place run.
- Support marker filtering, limits, and per-case timeouts.

## Validation

For the original torch 2.9 fixture set, preserve these regression values:

- XLSX extracted rows: 5175
- XLSX unique cases: 2230
- CSV rows: 1201
- CSV new cases: 301
- CSV rows skipped for missing triad: 0

Compare all four generated CSV files byte-for-byte against the established
`torch-pytest-sheet-compare` outputs.

For log workflows, test at least:

- A run_test log containing intermediate failures and a final stable list.
- A standalone pytest summary.
- A class-based and a function-style nodeid.
- Aggregate run_test lines containing many nodeids.
- Multiple cases with the same method name in different classes.
- A failing rerun, a passing rerun, a timeout, and zero selected rows.
- Repeated analysis without duplicate conclusion text.
