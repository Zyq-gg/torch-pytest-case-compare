#!/usr/bin/env python3
"""Rerun selected pytest cases from a CSV and write results to a new CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from compare_torch_pytest_sheets import (
    CaseKey,
    clean_cell,
    csv_key,
    find_triplet_columns,
    normalize_header,
    read_csv_table,
)
from log_failure_analysis import analyze_output


RESULT_COLUMNS = [
    "重跑状态",
    "重跑错误类型",
    "重跑错误信息",
    "重跑问题类别",
    "重跑问题结论",
    "重跑日志",
    "重跑耗时秒",
]


def find_column(headers: list[str], name: str) -> int | None:
    target = normalize_header(name)
    for index, header in enumerate(headers):
        if normalize_header(header) == target:
            return index
    return None


def ensure_column(headers: list[str], name: str) -> int:
    if name not in headers:
        headers.append(name)
    return headers.index(name)


def raw_value(row: list[str], index: int) -> str:
    return clean_cell(row[index]) if index < len(row) else ""


def pytest_path(repo: Path, value: str) -> str:
    path = value.replace("\\", "/").lstrip("./")
    candidates = [path]
    if not path.startswith("test/"):
        candidates.append(f"test/{path}")
    for candidate in candidates:
        if (repo / candidate).exists():
            return candidate
    return path


def exact_nodeid(repo: Path, row: list[str], columns: tuple[int, int, int]) -> str:
    py_index, class_index, op_index = columns
    py_name = pytest_path(repo, raw_value(row, py_index))
    class_name = raw_value(row, class_index)
    op_name = raw_value(row, op_index)
    return f"{py_name}::{class_name}::{op_name}" if class_name else f"{py_name}::{op_name}"


def safe_log_name(index: int, key: CaseKey) -> str:
    digest = hashlib.sha1(f"{key.py_name}::{key.class_name}::{key.op_name}".encode()).hexdigest()[:10]
    stem = "".join(char if char.isalnum() or char in "._-" else "_" for char in key.op_name)[:70]
    return f"{index:04d}_{digest}_{stem or 'case'}.log"


def run_case(repo: Path, env_script: Path | None, nodeid: str, timeout: int) -> tuple[int, str, float]:
    started = time.monotonic()
    if env_script is not None:
        command = (
            f"source {shlex.quote(str(env_script))} && "
            f"python3 -m pytest -vs --tb=long {shlex.quote(nodeid)}"
        )
        argv = ["bash", "-lc", command]
    else:
        argv = ["python3", "-m", "pytest", "-vs", "--tb=long", nodeid]
    try:
        process = subprocess.run(
            argv,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
        returncode = process.returncode
        output = process.stdout
    except subprocess.TimeoutExpired as error:
        returncode = 124
        stdout = error.stdout or ""
        output = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
        output += f"\nTIMEOUT after {timeout}s\n"
    return returncode, output, time.monotonic() - started


def write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rerun selected pytest cases from a compare-result CSV.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Output CSV; required unless --in-place is used")
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--repo", required=True, type=Path, help="Target PyTorch repository root")
    parser.add_argument("--env", dest="env_script", type=Path)
    parser.add_argument("--marker-column", default="case标记")
    parser.add_argument("--only-marker", help="Only rerun rows whose marker column equals this value, e.g. 新增")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--no-update-analysis", action="store_true")
    args = parser.parse_args()

    if args.in_place and args.output:
        raise SystemExit("use either --output or --in-place, not both")
    if not args.in_place and args.output is None:
        raise SystemExit("--output is required unless --in-place is used")
    for path in [args.input, args.repo, *([args.env_script] if args.env_script else [])]:
        if not path.exists():
            raise SystemExit(f"missing input: {path}")

    output_path = args.input if args.in_place else args.output
    assert output_path is not None
    if args.in_place:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = args.input.with_suffix(args.input.suffix + f".bak_{stamp}")
        shutil.copy2(args.input, backup)
        print(f"backup={backup}")

    headers, rows = read_csv_table(args.input)
    columns = find_triplet_columns(headers)
    if columns is None:
        raise SystemExit(f"could not find triad columns: {headers}")
    output_headers = list(headers)
    result_indices = {name: ensure_column(output_headers, name) for name in RESULT_COLUMNS}
    category_index = ensure_column(output_headers, "问题类别")
    conclusion_index = ensure_column(output_headers, "问题结论")
    marker_index = find_column(output_headers, args.marker_column)
    if args.only_marker is not None and marker_index is None:
        raise SystemExit(f"marker column not found: {args.marker_column}")
    width = len(output_headers)
    output_rows = [list(row) + [""] * max(0, width - len(row)) for row in rows]

    by_key: dict[CaseKey, list[int]] = {}
    for row_index, row in enumerate(rows):
        if args.only_marker is not None:
            marker = raw_value(row, marker_index) if marker_index is not None else ""
            if marker != args.only_marker:
                continue
        key = csv_key(row, columns)
        if key is not None:
            by_key.setdefault(key, []).append(row_index)
    keys = list(by_key)
    if args.limit:
        keys = keys[: args.limit]

    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_dir = args.log_dir or output_path.parent / f"pytest_rerun_logs_{stamp}"
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_path = log_dir / "summary.jsonl"
    summary_path.write_text("", encoding="utf-8")
    status_counts: dict[str, int] = {}
    write_csv(output_path, output_headers, [row[:width] for row in output_rows])

    for index, key in enumerate(keys, start=1):
        first_row = rows[by_key[key][0]]
        nodeid = exact_nodeid(args.repo, first_row, columns)
        log_path = log_dir / safe_log_name(index, key)
        print(f"[{index}/{len(keys)}] {nodeid}", flush=True)
        returncode, output, elapsed = run_case(args.repo, args.env_script, nodeid, args.timeout)
        log_path.write_text(output, encoding="utf-8")
        error_type, error_message, category, conclusion = analyze_output(output, returncode, args.timeout)
        status = "通过" if returncode == 0 else "超时" if returncode == 124 else "失败"
        status_counts[status] = status_counts.get(status, 0) + 1

        for row_index in by_key[key]:
            row = output_rows[row_index]
            values = {
                "重跑状态": status,
                "重跑错误类型": error_type,
                "重跑错误信息": error_message,
                "重跑问题类别": category,
                "重跑问题结论": conclusion,
                "重跑日志": str(log_path),
                "重跑耗时秒": f"{elapsed:.2f}",
            }
            for name, value in values.items():
                row[result_indices[name]] = value
            if not args.no_update_analysis:
                row[category_index] = category
                current = clean_cell(row[conclusion_index])
                addition = f"重跑结果: {error_message}；重跑分析: {conclusion}"
                if addition not in current:
                    row[conclusion_index] = f"{current}；{addition}" if current else addition

        record = {
            "index": index,
            "nodeid": nodeid,
            "returncode": returncode,
            "elapsed_sec": round(elapsed, 2),
            "status": status,
            "error_type": error_type,
            "error_message": error_message,
            "category": category,
            "conclusion": conclusion,
            "log": str(log_path),
            "row_indices_zero_based": by_key[key],
        }
        with summary_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        write_csv(output_path, output_headers, [row[:width] for row in output_rows])

    print(f"output={output_path}")
    print(f"log_dir={log_dir}")
    print(f"unique_cases={len(keys)} status_counts={json.dumps(status_counts, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
