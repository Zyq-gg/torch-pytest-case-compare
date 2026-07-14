#!/usr/bin/env python3
"""Enrich a compare-result CSV with failure evidence located in pytest logs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from compare_torch_pytest_sheets import (
    clean_cell,
    csv_key,
    find_triplet_columns,
    normalize_header,
    read_csv_table,
)
from log_failure_analysis import LogCorpus


LOG_COLUMNS = ["日志错误类型", "日志错误信息", "日志来源", "日志匹配置信度"]


def find_column(headers: list[str], names: set[str]) -> int | None:
    for index, header in enumerate(headers):
        if normalize_header(header) in names:
            return index
    return None


def ensure_column(headers: list[str], name: str) -> int:
    if name not in headers:
        headers.append(name)
    return headers.index(name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Add log-derived failure evidence to a pytest case CSV.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--logs", required=True, nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--marker-column", default="case标记")
    parser.add_argument("--only-marker", help="Only analyze rows whose marker column equals this value, e.g. 新增")
    parser.add_argument("--no-update-analysis", action="store_true", help="Keep existing 问题类别/问题结论 unchanged")
    args = parser.parse_args()

    missing = [str(path) for path in [args.input, *args.logs] if not path.exists()]
    if missing:
        raise SystemExit(f"missing inputs: {missing}")
    headers, rows = read_csv_table(args.input)
    columns = find_triplet_columns(headers)
    if columns is None:
        raise SystemExit(f"could not find triad columns: {headers}")

    output_headers = list(headers)
    indices = {name: ensure_column(output_headers, name) for name in LOG_COLUMNS}
    category_index = ensure_column(output_headers, "问题类别")
    conclusion_index = ensure_column(output_headers, "问题结论")
    marker_index = find_column(output_headers, {normalize_header(args.marker_column)})
    if args.only_marker is not None and marker_index is None:
        raise SystemExit(f"marker column not found: {args.marker_column}")
    corpus = LogCorpus(args.logs)

    counts = {"rows": len(rows), "selected": 0, "matched": 0, "high": 0, "medium": 0, "low": 0, "unmatched": 0}
    output_rows: list[list[str]] = []
    width = len(output_headers)
    for row in rows:
        output_row = list(row) + [""] * max(0, width - len(row))
        if args.only_marker is not None:
            marker = output_row[marker_index] if marker_index is not None else ""
            if clean_cell(marker) != args.only_marker:
                output_rows.append(output_row[:width])
                continue
        counts["selected"] += 1
        key = csv_key(row, columns)
        evidence = corpus.find(key) if key is not None else None
        if evidence is None:
            counts["unmatched"] += 1
            output_rows.append(output_row[:width])
            continue

        counts["matched"] += 1
        counts[evidence.confidence] += 1
        output_row[indices["日志错误类型"]] = evidence.error_type
        output_row[indices["日志错误信息"]] = evidence.error_message
        output_row[indices["日志来源"]] = f"{evidence.path}:{evidence.line}"
        output_row[indices["日志匹配置信度"]] = f"{evidence.confidence}:{evidence.score}"
        if not args.no_update_analysis and evidence.confidence in {"high", "medium"}:
            output_row[category_index] = evidence.category
            current = clean_cell(output_row[conclusion_index])
            addition = f"日志报错: {evidence.error_message}；日志分析: {evidence.conclusion}"
            if addition not in current:
                output_row[conclusion_index] = f"{current}；{addition}" if current else addition
        output_rows.append(output_row[:width])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(output_headers)
        writer.writerows(output_rows)
    summary_path = args.output.with_suffix(args.output.suffix + ".summary.json")
    summary = {**counts, "input": str(args.input), "logs": [str(path) for path in args.logs], "output": str(args.output)}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
