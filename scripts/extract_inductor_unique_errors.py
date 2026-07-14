#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extract unique meaningful Error/Exception messages from Inductor pytest logs.

Use this when the user wants a compact error taxonomy from a huge Inductor log
instead of a case-by-case Excel. It is useful for quickly answering:

  - What unique exception messages occurred?
  - How many times did each occur?
  - Where did each first appear?

Modes:

  - one-line: capture only the final concrete exception line.
  - block:    capture the exception line plus a small number of following detail
              lines, useful for Triton/ptxas/LLVM backend errors.

Typical use:

  python3 scripts/extract_inductor_unique_errors.py LOG --mode block --show-lines

  python3 scripts/extract_inductor_unique_errors.py LOG \
    --mode one-line \
    --output extracted_errors.txt \
    --with-count
"""

from __future__ import annotations

import argparse
import re
from collections import OrderedDict
from pathlib import Path


ERROR_TYPES = [
    "TypeError",
    "RuntimeError",
    "AttributeError",
    "ValueError",
    "AssertionError",
    "IndexError",
    "KeyError",
    "ImportError",
    "ModuleNotFoundError",
    "NotImplementedError",
    "OSError",
    "FileNotFoundError",
    "PermissionError",
]

INNER_ERROR_RE = re.compile(
    r"(?P<err>"
    r"(?:(?:"
    + "|".join(ERROR_TYPES)
    + r"|triton\.[\w.]*Error|torch\.[\w.]*Error)"
    r":\s*.*))"
)

GENERIC_ERROR_RE = re.compile(
    r"(?P<err>(?:[A-Za-z_][\w.]*\.)*[A-Za-z_]\w*(?:Error|Exception|Failure):\s*.*)"
)

WRAPPER_HINTS = ("BackendCompilerFailed:", "LoweringException:")
STOP_PREFIXES = (
    "Traceback ",
    "During handling ",
    "The above exception ",
    "Set TORCH_LOGS",
    "You can suppress",
    "To execute this test",
    "This message can be suppressed",
    "FAILED ",
    "PASSED ",
    "SKIPPED ",
    "ERROR ",
)
STOP_CONTAINS = (
    " short test summary info ",
    " FAILURES ",
    " ERRORS ",
    " warnings summary ",
    " test session starts ",
)


def normalize_line(line: str) -> str:
    return line.strip().replace("\x1b", "")


def is_stack_frame(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith('File "') or stripped.startswith("File '")


def is_stop_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if any(stripped.startswith(prefix) for prefix in STOP_PREFIXES):
        return True
    if any(token in stripped for token in STOP_CONTAINS):
        return True
    return stripped.startswith("=" * 10) or stripped.startswith("-" * 10) or stripped.startswith("_" * 10)


def extract_inner_error(line: str) -> str | None:
    stripped = normalize_line(line)
    matches = list(INNER_ERROR_RE.finditer(stripped))
    if matches:
        err = matches[-1].group("err").strip()
        if err.startswith("RuntimeError: backend="):
            return None
        return err

    match = GENERIC_ERROR_RE.search(stripped)
    if not match:
        return None

    err = match.group("err").strip()
    if any(hint in err for hint in WRAPPER_HINTS) and " raised:" in err:
        return None
    return err


def should_capture_continuation(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if is_stack_frame(line) or is_stop_line(line):
        return False
    if extract_inner_error(line):
        return False
    continuation_keywords = (
        "ptxas fatal",
        "ptxas error",
        "LLVM ERROR",
        "Internal Triton",
        "CompilationError",
        "No such file",
        "not defined for option",
        "failed",
        "invalid",
        "undefined",
        "cannot",
        "Can't",
        "Error",
        "error",
        "fatal",
    )
    return any(token in stripped for token in continuation_keywords) or line.startswith((" ", "\t"))


def cleanup_message(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(line.rstrip() for line in lines).strip()


def extract_errors(log_path: Path, mode: str, max_lookahead: int) -> OrderedDict[str, dict]:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    results: OrderedDict[str, dict] = OrderedDict()

    index = 0
    while index < len(lines):
        err = extract_inner_error(lines[index])
        if not err:
            index += 1
            continue

        msg_lines = [err]
        start_line = index + 1

        if mode == "block":
            blank_seen = 0
            lookahead = index + 1
            while lookahead < len(lines) and lookahead <= index + max_lookahead:
                nxt = lines[lookahead]
                if not should_capture_continuation(nxt):
                    break
                if not nxt.strip():
                    blank_seen += 1
                    if blank_seen >= 2:
                        break
                    lookahead += 1
                    continue
                blank_seen = 0
                msg_lines.append(nxt.strip())
                lookahead += 1

        msg = cleanup_message(msg_lines)
        if msg:
            info = results.setdefault(msg, {"count": 0, "first_line": start_line, "lines": []})
            info["count"] += 1
            info["lines"].append(start_line)
        index += 1

    return results


def format_results(results: OrderedDict[str, dict], mode: str, with_count: bool, show_lines: bool) -> str:
    output: list[str] = []
    if mode == "block":
        output.append(f"Unique errors: {len(results)}")
        output.append("")
    for idx, (message, info) in enumerate(results.items(), start=1):
        if mode == "one-line":
            if with_count:
                output.append(f"[count={info['count']} first_line={info['first_line']}] {message}")
            else:
                output.append(message)
            continue
        output.append(f"[{idx}] count={info['count']} first_line={info['first_line']}")
        if show_lines:
            lines = info["lines"]
            output.append(f"    lines={lines[:30]}" + (" ..." if len(lines) > 30 else ""))
        output.append(message)
        output.append("-" * 100)
    return "\n".join(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract unique Inductor errors from pytest logs.")
    parser.add_argument("logfile", type=Path, help="pytest/Inductor log file")
    parser.add_argument("--mode", choices=("block", "one-line"), default="block")
    parser.add_argument("--max-lookahead", type=int, default=12, help="Detail lines to inspect in block mode")
    parser.add_argument("--with-count", action="store_true", help="In one-line mode, include count and first line")
    parser.add_argument("--show-lines", action="store_true", help="In block mode, include all matched line numbers")
    parser.add_argument("-o", "--output", type=Path, help="Output text file")
    args = parser.parse_args()

    if not args.logfile.exists():
        raise SystemExit(f"missing log: {args.logfile}")

    results = extract_errors(args.logfile, args.mode, args.max_lookahead)
    text = format_results(results, args.mode, args.with_count, args.show_lines)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"saved={args.output}")
        print(f"unique_errors={len(results)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
