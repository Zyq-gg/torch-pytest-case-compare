#!/usr/bin/env python3
"""Extract failed pytest cases from run_test.py or standalone pytest logs."""

from __future__ import annotations

import argparse
import ast
import csv
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from compare_torch_pytest_sheets import CaseKey, make_key
from log_failure_analysis import error_type_from_message


RUN_TEST_MARKER = "The following tests failed consistently:"
RUN_TEST_HINTS = (
    RUN_TEST_MARKER,
    "Name: tests to run",
    "The following tests failed and then succeeded",
    "FAILED CONSISTENTLY:",
    "run_test.py --include",
)
SUMMARY_RE = re.compile(r"^\s*(FAILED|ERROR)(?:\s+\[[^\]]+\])?\s+(.+)$")


@dataclass
class ExtractedCase:
    key: CaseKey
    source_log: str
    source_line: int
    test_file: str
    class_name: str
    case_name: str
    error_type: str
    error_message: str
    nodeid: str
    raw: str


def split_nodeid(nodeid: str) -> tuple[str, str, str] | None:
    parts = [part.strip() for part in nodeid.strip().split("::")]
    if len(parts) >= 3:
        return parts[0], parts[1], "::".join(parts[2:])
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return None


def parse_run_test_payload(payload: str) -> list[str]:
    payload = payload.strip().lstrip(":").strip()
    try:
        value = ast.literal_eval(payload)
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
    except (SyntaxError, ValueError):
        pass
    match = re.search(r"\[(.*)\]", payload)
    if not match:
        return []
    return [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]


def make_record(path: Path, line_no: int, nodeid: str, error: str, raw: str) -> ExtractedCase | None:
    split = split_nodeid(nodeid)
    if split is None:
        return None
    test_file, class_name, case_name = split
    key = make_key(test_file, class_name, case_name)
    if key is None:
        return None
    return ExtractedCase(
        key=key,
        source_log=str(path),
        source_line=line_no,
        test_file=test_file,
        class_name=class_name,
        case_name=case_name,
        error_type=error_type_from_message(error) if error else "",
        error_message=error,
        nodeid=nodeid,
        raw=raw.strip(),
    )


def extract_log(path: Path, mode: str) -> list[ExtractedCase]:
    records: dict[CaseKey, ExtractedCase] = {}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    has_run_test_context = any(any(hint in line for hint in RUN_TEST_HINTS) for line in lines)
    parse_run_test = mode == "run-test" or (mode == "auto" and has_run_test_context)
    parse_pytest = mode == "pytest" or (mode == "auto" and not has_run_test_context)
    for line_no, line in enumerate(lines, start=1):
        if parse_run_test and RUN_TEST_MARKER in line:
            payload = line.split(RUN_TEST_MARKER, 1)[1]
            for nodeid in parse_run_test_payload(payload):
                record = make_record(path, line_no, nodeid, "", line)
                if record is not None:
                    records.setdefault(record.key, record)

        if not parse_pytest:
            continue
        match = SUMMARY_RE.match(line)
        if not match:
            continue
        remainder = match.group(2).strip()
        if remainder.startswith("CONSISTENTLY:"):
            continue
        nodeid, separator, error = remainder.partition(" - ")
        record = make_record(path, line_no, nodeid.strip(), error.strip() if separator else "", line)
        if record is None:
            continue
        existing = records.get(record.key)
        if existing is None or (record.error_message and not existing.error_message):
            records[record.key] = record
    return list(records.values())


FIELDS = [
    "source_log",
    "source_line",
    "test_file",
    "class_name",
    "case_name",
    "error_type",
    "error_message",
    "nodeid",
    "raw",
]


def record_values(record: ExtractedCase) -> list[object]:
    return [
        record.source_log,
        record.source_line,
        record.test_file,
        record.class_name,
        record.case_name,
        record.error_type,
        record.error_message,
        record.nodeid,
        record.raw,
    ]


def write_csv(path: Path, records: list[ExtractedCase]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(record_values(record) for record in records)


def unique_sheet_name(existing_names: list[str], base: str) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "_", base)
    base = base[:31] or "failures"
    name = base
    index = 1
    while name in existing_names:
        suffix = f"_{index}"
        name = f"{base[:31 - len(suffix)]}{suffix}"
        index += 1
    return name


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def xml_text(value: object) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))
    return escape(text)


def worksheet_xml(rows: list[list[object]]) -> str:
    output = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for row_index, row in enumerate(rows, start=1):
        output.append(f'<row r="{row_index}">')
        for column_index, value in enumerate(row, start=1):
            reference = f"{column_name(column_index)}{row_index}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                output.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                output.append(
                    f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">'
                    f'{xml_text(value)}</t></is></c>'
                )
        output.append("</row>")
    output.append("</sheetData></worksheet>")
    return "".join(output)


def write_xlsx(path: Path, per_log: list[tuple[Path, list[ExtractedCase]]]) -> None:
    sheet_names: list[str] = []
    sheet_rows: list[list[list[object]]] = []
    for log_path, records in per_log:
        sheet_names.append(unique_sheet_name(sheet_names, log_path.stem))
        sheet_rows.append([list(FIELDS), *(record_values(record) for record in records)])

    workbook_sheets = "".join(
        f'<sheet name={quoteattr(name)} sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{workbook_sheets}</sheets></workbook>'
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheet_names) + 1)
    )
    workbook_rels += (
        f'<Relationship Id="rId{len(sheet_names) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    content_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheet_names) + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f'{content_overrides}</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{workbook_rels}</Relationships>',
        )
        archive.writestr("xl/styles.xml", styles)
        for index, rows in enumerate(sheet_rows, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml(rows))


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract failed PyTorch pytest cases from logs.")
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=("auto", "run-test", "pytest"), default="auto")
    args = parser.parse_args()

    missing = [str(path) for path in args.logs if not path.exists()]
    if missing:
        raise SystemExit(f"missing logs: {missing}")
    per_log = [(path, extract_log(path, args.mode)) for path in args.logs]
    records = [record for _path, values in per_log for record in values]
    if args.output.suffix.lower() == ".csv":
        write_csv(args.output, records)
    elif args.output.suffix.lower() == ".xlsx":
        write_xlsx(args.output, per_log)
    else:
        raise SystemExit("--output must end in .csv or .xlsx")
    print(f"output={args.output}")
    print(f"logs={len(per_log)} extracted_rows={len(records)}")
    for path, values in per_log:
        print(f"  {path}: {len(values)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
