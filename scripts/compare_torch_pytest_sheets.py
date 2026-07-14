#!/usr/bin/env python3
"""Extract marked PyTorch pytest cases from XLSX files and compare a pytest CSV.

The script intentionally uses only Python's standard library so it works in
minimal CI/debug environments without openpyxl or pandas.
"""

from __future__ import annotations

import argparse
import csv
import json
import posixpath
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET


XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

PY_COLS = {
    "pyname",
    "py_name",
    "testfile",
    "test_file",
    "file",
    "测试文件",
}
CLASS_COLS = {
    "class",
    "classname",
    "class_name",
    "测试类",
}
OP_COLS = {
    "opname",
    "op_name",
    "casename",
    "case_name",
    "testname",
    "test_name",
    "测试项",
    "测试用例",
}


@dataclass(frozen=True)
class CaseKey:
    py_name: str
    class_name: str
    op_name: str


@dataclass
class SourceRow:
    file: str
    sheet: str
    row: int
    py_name_raw: str
    class_name_raw: str
    op_name_raw: str
    values: List[str]
    headers: List[str]
    triplet_columns: Tuple[int, int, int]


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_header(value: object) -> str:
    text = clean_cell(value).lower()
    text = text.replace("-", "_").replace(" ", "_")
    return re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "", text)


def normalize_py_name(value: object) -> str:
    text = clean_cell(value).replace("\\", "/")
    text = re.sub(r"^\./+", "", text)
    text = re.sub(r"^/+", "", text)
    while text.startswith("test/"):
        text = text[len("test/") :]
    return text


def normalize_name(value: object) -> str:
    return clean_cell(value)


def unique_preserve_order(values: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        value = clean_cell(value)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def make_key(py_name: object, class_name: object, op_name: object) -> Optional[CaseKey]:
    key = CaseKey(
        normalize_py_name(py_name),
        normalize_name(class_name),
        normalize_name(op_name),
    )
    if not key.py_name or not key.op_name:
        return None
    return key


def col_to_index(cell_ref: str) -> Optional[int]:
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return None
    index = 0
    for char in match.group(1):
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def read_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    strings: List[str] = []
    for item in root.findall(XLSX_NS + "si"):
        strings.append("".join(node.text or "" for node in item.iter(XLSX_NS + "t")))
    return strings


def get_cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(XLSX_NS + "t"))
    value_node = cell.find(XLSX_NS + "v")
    if value_node is None:
        return ""
    text = value_node.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(text)]
        except (ValueError, IndexError):
            return text
    if cell_type == "b":
        return "TRUE" if text == "1" else "FALSE"
    return text


def workbook_sheets(zf: zipfile.ZipFile) -> List[Tuple[str, str]]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall(PKG_REL_NS + "Relationship")
    }
    sheets: List[Tuple[str, str]] = []
    for sheet in workbook.find(XLSX_NS + "sheets").findall(XLSX_NS + "sheet"):
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get(OFFICE_REL_NS + "id", "")
        target = rid_to_target.get(rid, "")
        if not target.startswith("/"):
            target = posixpath.normpath(posixpath.join("xl", target))
        else:
            target = target.lstrip("/")
        sheets.append((name, target))
    return sheets


def iter_xlsx_rows(path: Path) -> Iterator[Tuple[str, int, List[str]]]:
    with zipfile.ZipFile(path) as zf:
        shared_strings = read_shared_strings(zf)
        for sheet_name, sheet_target in workbook_sheets(zf):
            root = ET.fromstring(zf.read(sheet_target))
            sheet_data = root.find(XLSX_NS + "sheetData")
            if sheet_data is None:
                continue
            for row in sheet_data.findall(XLSX_NS + "row"):
                values_by_col: Dict[int, str] = {}
                max_col = -1
                for cell in row.findall(XLSX_NS + "c"):
                    index = col_to_index(cell.attrib.get("r", ""))
                    if index is None:
                        index = max_col + 1
                    values_by_col[index] = clean_cell(get_cell_value(cell, shared_strings))
                    max_col = max(max_col, index)
                values = [values_by_col.get(index, "") for index in range(max_col + 1)]
                while values and not values[-1]:
                    values.pop()
                row_number = int(row.attrib.get("r", "0") or "0")
                yield sheet_name, row_number, values


def find_triplet_columns(header: Sequence[str]) -> Optional[Tuple[int, int, int]]:
    normalized = [normalize_header(value) for value in header]

    def first_index(candidates: set[str]) -> Optional[int]:
        for index, value in enumerate(normalized):
            if value in candidates:
                return index
        return None

    py_index = first_index(PY_COLS)
    class_index = first_index(CLASS_COLS)
    op_index = first_index(OP_COLS)
    if py_index is None or class_index is None or op_index is None:
        return None
    return py_index, class_index, op_index


def extract_xlsx_cases(xlsx_paths: Sequence[Path]) -> Tuple[Dict[CaseKey, List[SourceRow]], List[SourceRow]]:
    unique: Dict[CaseKey, List[SourceRow]] = {}
    all_rows: List[SourceRow] = []
    for path in xlsx_paths:
        columns_by_sheet: Dict[str, Tuple[int, int, int]] = {}
        headers_by_sheet: Dict[str, List[str]] = {}
        for sheet_name, row_number, values in iter_xlsx_rows(path):
            triplet_columns = find_triplet_columns(values)
            if triplet_columns:
                columns_by_sheet[sheet_name] = triplet_columns
                headers_by_sheet[sheet_name] = [clean_cell(value) for value in values]
                continue
            columns = columns_by_sheet.get(sheet_name)
            if columns is None:
                continue
            py_index, class_index, op_index = columns
            max_needed = max(columns)
            if len(values) <= max_needed:
                continue
            key = make_key(values[py_index], values[class_index], values[op_index])
            if key is None:
                continue
            source = SourceRow(
                file=path.name,
                sheet=sheet_name,
                row=row_number,
                py_name_raw=clean_cell(values[py_index]),
                class_name_raw=clean_cell(values[class_index]),
                op_name_raw=clean_cell(values[op_index]),
                values=[clean_cell(value) for value in values],
                headers=headers_by_sheet.get(sheet_name, []),
                triplet_columns=columns,
            )
            all_rows.append(source)
            unique.setdefault(key, []).append(source)
    return unique, all_rows


def read_csv_table(path: Path) -> Tuple[List[str], List[List[str]]]:
    last_error: Optional[Exception] = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                try:
                    fieldnames = next(reader)
                except StopIteration:
                    return [], []
                rows = [[clean_cell(value) for value in row] for row in reader]
                return [clean_cell(value) for value in fieldnames], rows
        except UnicodeDecodeError as error:
            last_error = error
    raise RuntimeError(f"Could not decode CSV {path}: {last_error}")


def csv_key(row: Sequence[str], columns: Tuple[int, int, int]) -> Optional[CaseKey]:
    py_index, class_index, op_index = columns

    def value_at(index: int) -> str:
        if index >= len(row):
            return ""
        return row[index]

    return make_key(value_at(py_index), value_at(class_index), value_at(op_index))


def write_all_rows(path: Path, rows: Sequence[SourceRow]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source_file",
                "source_sheet",
                "source_row",
                "py_name_raw",
                "class_name_raw",
                "op_name_raw",
                "row_values_json",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "source_file": row.file,
                    "source_sheet": row.sheet,
                    "source_row": row.row,
                    "py_name_raw": row.py_name_raw,
                    "class_name_raw": row.class_name_raw,
                    "op_name_raw": row.op_name_raw,
                    "row_values_json": json.dumps(row.values, ensure_ascii=False),
                }
            )


def write_unique_cases(path: Path, cases: Dict[CaseKey, List[SourceRow]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "py_name",
                "class_name",
                "op_name",
                "source_count",
                "sources_json",
            ],
        )
        writer.writeheader()
        for key in sorted(cases, key=lambda item: (item.py_name, item.class_name, item.op_name)):
            sources = [
                {
                    "file": source.file,
                    "sheet": source.sheet,
                    "row": source.row,
                    "py_name_raw": source.py_name_raw,
                    "class_name_raw": source.class_name_raw,
                    "op_name_raw": source.op_name_raw,
                }
                for source in cases[key]
            ]
            writer.writerow(
                {
                    "py_name": key.py_name,
                    "class_name": key.class_name,
                    "op_name": key.op_name,
                    "source_count": len(sources),
                    "sources_json": json.dumps(sources, ensure_ascii=False),
                }
            )


def write_new_csv_cases(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Tuple[CaseKey, Sequence[str]]],
) -> None:
    output_fields = [
        "normalized_py_name",
        "normalized_class_name",
        "normalized_op_name",
        *fieldnames,
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(output_fields)
        for key, row in rows:
            padded_row = list(row) + [""] * max(0, len(fieldnames) - len(row))
            writer.writerow([key.py_name, key.class_name, key.op_name, *padded_row[: len(fieldnames)]])


def source_summary(sources: Sequence[SourceRow]) -> str:
    return "; ".join(f"{source.file}/{source.sheet}:row{source.row}" for source in sources)


def source_notes(sources: Sequence[SourceRow]) -> str:
    notes: List[str] = []
    triad_headers = PY_COLS | CLASS_COLS | OP_COLS
    weak_values = {"0", "1", "true", "false"}
    for source in sources:
        triplet_columns = set(source.triplet_columns)
        for index, value in enumerate(source.values):
            value = clean_cell(value)
            if not value or index in triplet_columns:
                continue
            if value.lower() in weak_values:
                continue
            header = source.headers[index] if index < len(source.headers) else ""
            normalized_header = normalize_header(header)
            if normalized_header in triad_headers:
                continue
            if header:
                notes.append(f"{header}: {value}")
            else:
                notes.append(value)
    return "；".join(unique_preserve_order(notes))


def get_row_value(row: Sequence[str], fieldnames: Sequence[str], normalized_names: set[str]) -> str:
    for index, field in enumerate(fieldnames):
        if normalize_header(field) in normalized_names and index < len(row):
            return clean_cell(row[index])
    return ""


def csv_error_text(row: Sequence[str], fieldnames: Sequence[str]) -> str:
    error_type = get_row_value(row, fieldnames, {"errortype", "error_type"})
    error_message = get_row_value(row, fieldnames, {"errormessage", "error_message"})
    if error_type and error_message:
        return f"{error_type}: {error_message}"
    return error_message or error_type


def issue_category(row: Sequence[str], fieldnames: Sequence[str]) -> str:
    error_type = get_row_value(row, fieldnames, {"errortype", "error_type"})
    error_message = get_row_value(row, fieldnames, {"errormessage", "error_message"})
    if error_message:
        compact_message = re.sub(r"\d+(?:\.\d+)?", "<num>", error_message)
        compact_message = re.sub(r"_[0-9]+", "_<num>", compact_message)
        return f"{error_type}: {compact_message}" if error_type else compact_message
    return error_type or "无报错信息"


def build_similar_notes_by_error(
    rows: Sequence[Sequence[str]],
    fieldnames: Sequence[str],
    keys: Sequence[Optional[CaseKey]],
    cases: Dict[CaseKey, List[SourceRow]],
) -> Dict[str, str]:
    similar: Dict[str, str] = {}
    for row, key in zip(rows, keys):
        if key is None or key not in cases:
            continue
        error_message = get_row_value(row, fieldnames, {"errormessage", "error_message"})
        if not error_message or error_message in similar:
            continue
        notes = source_notes(cases[key])
        if notes:
            similar[error_message] = notes
    return similar


def case_conclusion(
    row: Sequence[str],
    fieldnames: Sequence[str],
    key: Optional[CaseKey],
    cases: Dict[CaseKey, List[SourceRow]],
    similar_notes_by_error: Dict[str, str],
) -> str:
    error_text = csv_error_text(row, fieldnames)
    if key is None:
        return f"无法识别三元组；CSV报错: {error_text}" if error_text else "无法识别三元组"
    if key in cases:
        notes = source_notes(cases[key])
        if notes and error_text:
            return f"CSV报错: {error_text}；来源信息: {notes}"
        if notes:
            return notes
        return f"CSV报错: {error_text}" if error_text else "已标记；来源表无明确备注"
    error_message = get_row_value(row, fieldnames, {"errormessage", "error_message"})
    similar_notes = similar_notes_by_error.get(error_message, "")
    if similar_notes:
        if error_text:
            return f"新增；CSV报错: {error_text}；同类已标记问题参考: {similar_notes}"
        return f"新增；同类已标记问题参考: {similar_notes}"
    return f"新增；CSV报错: {error_text}" if error_text else "新增；CSV无明确报错信息"


def find_empty_or_append_column(
    fieldnames: Sequence[str],
    rows: Sequence[Sequence[str]],
    preferred_name: str,
) -> Tuple[int, List[str], bool]:
    max_width = max([len(fieldnames), *(len(row) for row in rows)] or [0])
    headers = list(fieldnames) + [""] * max(0, max_width - len(fieldnames))
    for index in range(max_width):
        header_empty = not clean_cell(headers[index])
        values_empty = all(index >= len(row) or not clean_cell(row[index]) for row in rows)
        if header_empty and values_empty:
            headers[index] = preferred_name
            return index, headers, False
    headers.append(preferred_name)
    return len(headers) - 1, headers, True


def marker_for_key(key: Optional[CaseKey], cases: Dict[CaseKey, List[SourceRow]]) -> str:
    if key is None:
        return ""
    if key in cases:
        return source_summary(cases[key])
    return "新增"


def write_analyzed_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Sequence[str]],
    keys: Sequence[Optional[CaseKey]],
    cases: Dict[CaseKey, List[SourceRow]],
    marker_column_name: str,
) -> None:
    similar_notes_by_error = build_similar_notes_by_error(rows, fieldnames, keys, cases)
    marker_index, output_headers, _ = find_empty_or_append_column(fieldnames, rows, marker_column_name)
    for column in ("问题类别", "问题结论"):
        if column not in output_headers:
            output_headers.append(column)
    category_index = output_headers.index("问题类别")
    conclusion_index = output_headers.index("问题结论")
    width = len(output_headers)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(output_headers)
        for row, key in zip(rows, keys):
            output_row = list(row) + [""] * max(0, width - len(row))
            output_row[marker_index] = marker_for_key(key, cases)
            output_row[category_index] = issue_category(row, fieldnames)
            output_row[conclusion_index] = case_conclusion(row, fieldnames, key, cases, similar_notes_by_error)
            writer.writerow(output_row[:width])


def collect_xlsx_paths(input_dir: Path, csv_path: Optional[Path]) -> List[Path]:
    paths = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".xlsx", ".xlsm"}
        and not path.name.startswith("~$")
    )
    if csv_path:
        csv_path = csv_path.resolve()
        paths = [path for path in paths if path.resolve() != csv_path]
    return paths


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract unique marked PyTorch pytest cases from XLSX files and compare a CSV for new cases."
    )
    parser.add_argument("input_dir", type=Path, help="Directory containing XLSX files and optionally the CSV file.")
    parser.add_argument("--csv", dest="csv_path", type=Path, required=True, help="Pytest CSV to compare.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to <input_dir>/torch_pytest_sheet_compare_out.",
    )
    parser.add_argument(
        "--marker-column-name",
        default="case标记",
        help="Column header to use when no fully empty CSV column exists. Defaults to case标记.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_dir = args.input_dir.resolve()
    csv_path = args.csv_path.resolve()
    out_dir = (args.out_dir or input_dir / "torch_pytest_sheet_compare_out").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    xlsx_paths = collect_xlsx_paths(input_dir, csv_path)
    if not xlsx_paths:
        print(f"No XLSX files found in {input_dir}", file=sys.stderr)
        return 2

    unique_cases, all_rows = extract_xlsx_cases(xlsx_paths)
    csv_fieldnames, csv_rows = read_csv_table(csv_path)
    csv_columns = find_triplet_columns(csv_fieldnames)
    if csv_columns is None:
        print(f"Could not find CSV triad columns in header: {csv_fieldnames}", file=sys.stderr)
        return 2
    marked_keys = set(unique_cases)
    new_rows: List[Tuple[CaseKey, Sequence[str]]] = []
    csv_keys: List[Optional[CaseKey]] = []
    skipped_csv_rows = 0
    for row in csv_rows:
        key = csv_key(row, csv_columns)
        csv_keys.append(key)
        if key is None:
            skipped_csv_rows += 1
            continue
        if key not in marked_keys:
            new_rows.append((key, row))

    all_rows_path = out_dir / "marked_cases_all_rows.csv"
    unique_path = out_dir / "marked_cases_unique.csv"
    new_path = out_dir / "csv_new_cases.csv"
    analyzed_path = out_dir / f"{csv_path.stem}_analyzed.csv"
    summary_path = out_dir / "summary.json"

    write_all_rows(all_rows_path, all_rows)
    write_unique_cases(unique_path, unique_cases)
    write_new_csv_cases(new_path, csv_fieldnames, new_rows)
    write_analyzed_csv(analyzed_path, csv_fieldnames, csv_rows, csv_keys, unique_cases, args.marker_column_name)
    marker_index, _, marker_appended = find_empty_or_append_column(
        csv_fieldnames,
        csv_rows,
        args.marker_column_name,
    )
    summary = {
        "input_dir": str(input_dir),
        "csv": str(csv_path),
        "xlsx_files": [path.name for path in xlsx_paths],
        "xlsx_extracted_rows": len(all_rows),
        "xlsx_unique_cases": len(unique_cases),
        "csv_rows": len(csv_rows),
        "csv_new_cases": len(new_rows),
        "csv_rows_skipped_missing_triplet": skipped_csv_rows,
        "csv_marker_column": args.marker_column_name,
        "csv_marker_column_index_zero_based": marker_index,
        "csv_marker_column_appended": marker_appended,
        "normalization": {
            "py_name": "trim, slash-normalize, remove leading ./, /, and repeated test/",
            "class_name": "trim and collapse whitespace",
            "op_name": "trim and collapse whitespace",
        },
        "outputs": {
            "marked_cases_all_rows": str(all_rows_path),
            "marked_cases_unique": str(unique_path),
            "csv_new_cases": str(new_path),
            "csv_analyzed": str(analyzed_path),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
