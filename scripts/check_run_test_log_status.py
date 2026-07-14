#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Summarize run_test.py shard/file-level completion status from PyTorch optest logs.

Use it before extracting failed cases when a large run_test.py log may have been
interrupted. It reads the "Name: tests to run" section, then checks each listed
test file against later log lines and classifies it as:

  - error:       the log contains "<test> 1/1 failed!"
  - ok:          the log contains pytest progress ending at [100%], or the
                 known run_test.py message that failed tests succeeded in a
                 fresh process
  - check:       the test appears to have emitted pytest nodeids but does not
                 have a clear [100%] completion marker
  - interrupted: the test was listed but no later pytest nodeid was found

Typical use:

  python3 scripts/check_run_test_log_status.py \
    /path/to/run_test_gpu_0.log \
    /path/to/run_test_gpu_1.log

For machine-readable output:

  python3 scripts/check_run_test_log_status.py LOG --format tsv
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path


def extract_tests_from_log(log_file: Path) -> list[str]:
    lines = log_file.read_text(errors="replace").splitlines()
    tests: list[str] = []
    in_tests_section = False

    for line in lines:
        if "Name: tests to run" in line:
            in_tests_section = True
            continue
        if in_tests_section and "Name: excluded" in line:
            break
        if in_tests_section:
            match = re.match(r"\s*(\S+)\s+(\d+/\d+)", line)
            if match:
                tests.append(match.group(1))

    return tests


def check_test_status(log_file: Path, tests: list[str]) -> dict[str, str]:
    lines = log_file.read_text(errors="replace").splitlines()
    results: dict[str, str] = {}

    for test in tests:
        failure_str = f"{test} 1/1 failed!"
        success_str = f"{test}.py::"

        if any(failure_str in line for line in reversed(lines)):
            results[test] = "error"
            continue

        matched_nodeid = None
        for line in reversed(lines):
            if success_str in line:
                matched_nodeid = line
                break

        if matched_nodeid is None:
            results[test] = "interrupted"
        elif (
            "[100%]" in matched_nodeid
            or "The following tests failed and then succeeded when run in a new process" in matched_nodeid
        ):
            results[test] = "ok"
        else:
            results[test] = "check"

    return results


def print_human(log_file: Path, results: dict[str, str]) -> None:
    counts = Counter(results.values())
    print(f"\nlog: {log_file}")
    print(
        "summary:",
        f"total={len(results)}",
        f"ok={counts['ok']}",
        f"error={counts['error']}",
        f"check={counts['check']}",
        f"interrupted={counts['interrupted']}",
    )
    max_length = max((len(test) for test in results), default=0)
    for test, status in results.items():
        print(f"{test.ljust(max_length)} : {status}")

    for status in ("error", "check", "interrupted"):
        selected = [test for test, value in results.items() if value == status]
        if selected:
            print(f"{status} tests: {' '.join(selected)}")
    check_tests = [test + ".py::" for test, value in results.items() if value == "check"]
    if check_tests:
        print("check nodeid prefixes:")
        print("\n".join(check_tests))


def print_tsv(log_file: Path, results: dict[str, str]) -> None:
    for test, status in results.items():
        print(f"{log_file}\t{test}\t{status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check run_test.py log completion status by test file.")
    parser.add_argument("logs", nargs="+", type=Path, help="run_test.py generated optest log files")
    parser.add_argument("--format", choices=("human", "tsv"), default="human")
    args = parser.parse_args()

    for log_file in args.logs:
        tests = extract_tests_from_log(log_file)
        results = check_test_status(log_file, tests)
        if args.format == "tsv":
            print_tsv(log_file, results)
        else:
            print_human(log_file, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
