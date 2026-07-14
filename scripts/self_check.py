#!/usr/bin/env python3
"""Run a repository-contained portability smoke test with synthetic inputs."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr


SCRIPT_DIR = Path(__file__).resolve().parent


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def worksheet_xml(rows: list[list[str]]) -> str:
    output = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for row_index, row in enumerate(rows, start=1):
        output.append(f'<row r="{row_index}">')
        for column_index, value in enumerate(row, start=1):
            reference = f"{column_name(column_index)}{row_index}"
            output.append(
                f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">'
                f"{escape(str(value))}</t></is></c>"
            )
        output.append("</row>")
    output.append("</sheetData></worksheet>")
    return "".join(output)


def write_xlsx(path: Path, sheets: list[tuple[str, list[list[str]]]]) -> None:
    workbook_sheets = "".join(
        f'<sheet name={quoteattr(name)} sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _rows) in enumerate(sheets, start=1)
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{workbook_rels}</Relationships>"
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        for index, (_name, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml(rows))


def write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def run_script(name: str, *args: object) -> str:
    command = [sys.executable, str(SCRIPT_DIR / name), *(str(arg) for arg in args)]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        raise AssertionError(f"command failed ({process.returncode}): {' '.join(command)}\n{process.stdout}")
    return process.stdout


def check_comparison(root: Path) -> tuple[Path, Path]:
    data_dir = root / "case-data"
    data_dir.mkdir()
    workbook = data_dir / "marked.xlsx"
    write_xlsx(
        workbook,
        [
            (
                "marked_a",
                [
                    ["Synthetic marked cases"],
                    ["py name", "class", "op name", "note"],
                    ["test/test_alpha.py", "TestAlpha", "test_existing", "known issue"],
                    ["test/test_dup.py", "TestDup", "test_dup", "duplicate source A"],
                ],
            ),
            (
                "marked_b",
                [
                    ["Description"],
                    ["test_file", "class_name", "case_name", "error_message"],
                    ["test/test_dup.py", "TestDup", "test_dup", "duplicate source B"],
                ],
            ),
        ],
    )
    input_csv = data_dir / "latest.csv"
    write_csv(
        input_csv,
        ["test_file", "class_name", "case_name", "error_type", "error_message", ""],
        [
            ["test/test_alpha.py", "TestAlpha", "test_existing", "RuntimeError", "known failure", ""],
            ["test/test_beta.py", "TestBeta", "test_new", "RuntimeError", "synthetic beta failure", ""],
            ["test/test_dup.py", "TestDup", "test_dup", "AssertionError", "duplicate", ""],
            ["test/test_functional.py", "", "test_function_case", "ValueError", "function failure", ""],
        ],
    )
    output_dir = root / "compare-out"
    run_script(
        "compare_torch_pytest_sheets.py",
        data_dir,
        "--csv",
        input_csv,
        "--out-dir",
        output_dir,
    )
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    expected = {
        "xlsx_extracted_rows": 3,
        "xlsx_unique_cases": 2,
        "csv_rows": 4,
        "csv_new_cases": 2,
        "csv_rows_skipped_missing_triplet": 0,
        "csv_marker_column_appended": False,
    }
    for key, value in expected.items():
        assert summary[key] == value, (key, summary[key], value)

    analyzed = output_dir / "latest_analyzed.csv"
    rows = {row["case_name"]: row for row in read_csv(analyzed)}
    assert rows["test_existing"]["case标记"].startswith("marked.xlsx/marked_a:row3")
    assert "marked_a:row4" in rows["test_dup"]["case标记"]
    assert "marked_b:row3" in rows["test_dup"]["case标记"]
    assert rows["test_new"]["case标记"] == "新增"
    assert rows["test_function_case"]["case标记"] == "新增"
    return analyzed, output_dir


def check_log_extraction(root: Path) -> tuple[Path, Path]:
    run_test_log = root / "run_test.log"
    run_test_log.write_text(
        "\n".join(
            [
                "Name: tests to run (est. time: 1.0min)",
                "FAILED test/test_alpha.py::TestAlpha::test_existing - RuntimeError: transient",
                "The following tests failed and then succeeded when run in a new process"
                "['test/test_alpha.py::TestAlpha::test_existing']",
                "The following tests failed consistently: "
                "['test/test_beta.py::TestBeta::test_new']",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stable_csv = root / "stable.csv"
    run_script("extract_pytest_failures.py", run_test_log, "--output", stable_csv)
    stable_rows = read_csv(stable_csv)
    assert len(stable_rows) == 1
    assert stable_rows[0]["nodeid"] == "test/test_beta.py::TestBeta::test_new"

    pytest_log = root / "pytest.log"
    pytest_log.write_text(
        "FAILED test/test_functional.py::test_function_case - ValueError: function failure\n",
        encoding="utf-8",
    )
    pytest_csv = root / "pytest.csv"
    run_script("extract_pytest_failures.py", pytest_log, "--output", pytest_csv)
    pytest_rows = read_csv(pytest_csv)
    assert len(pytest_rows) == 1
    assert pytest_rows[0]["class_name"] == ""
    assert pytest_rows[0]["case_name"] == "test_function_case"

    xlsx_output = root / "failures.xlsx"
    run_script("extract_pytest_failures.py", run_test_log, pytest_log, "--output", xlsx_output)
    with zipfile.ZipFile(xlsx_output) as archive:
        assert archive.testzip() is None
        assert "xl/worksheets/sheet1.xml" in archive.namelist()
        assert "xl/worksheets/sheet2.xml" in archive.namelist()
    return run_test_log, pytest_log


def check_log_analysis(root: Path, analyzed: Path) -> None:
    evidence_log = root / "evidence.log"
    evidence_log.write_text(
        "\n".join(
            [
                "________________________ TestBeta.test_new ________________________",
                "test/test_beta.py::TestBeta::test_new",
                "E   RuntimeError: synthetic beta failure",
                "FAILED test/test_beta.py::TestBeta::test_new - RuntimeError: synthetic beta failure",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = root / "log-analyzed.csv"
    run_script(
        "analyze_pytest_cases.py",
        "--input",
        analyzed,
        "--logs",
        evidence_log,
        "--output",
        output,
        "--only-marker",
        "新增",
    )
    rows = {row["case_name"]: row for row in read_csv(output)}
    assert rows["test_new"]["日志匹配置信度"].startswith(("high:", "medium:"))
    assert "synthetic beta failure" in rows["test_new"]["日志错误信息"]
    summary = json.loads(output.with_suffix(".csv.summary.json").read_text(encoding="utf-8"))
    assert summary["selected"] == 2
    assert summary["matched"] >= 1


def check_supporting_tools(root: Path, analyzed: Path) -> None:
    status_log = root / "status.log"
    status_log.write_text(
        "\n".join(
            [
                "Name: tests to run (est. time: 1.0min)",
                "  Serial tests (1):",
                "    test_alpha 1/1",
                "Name: excluded (est. time: 0.0min)",
                "test_alpha.py::TestAlpha::test_existing PASSED [100%]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    status_output = run_script("check_run_test_log_status.py", status_log, "--format", "tsv")
    assert "\ttest_alpha\tok" in status_output

    inductor_log = root / "inductor.log"
    inductor_log.write_text("RuntimeError: synthetic backend failure\n", encoding="utf-8")
    errors_output = root / "unique-errors.txt"
    run_script(
        "extract_inductor_unique_errors.py",
        inductor_log,
        "--mode",
        "one-line",
        "--with-count",
        "--output",
        errors_output,
    )
    assert "RuntimeError: synthetic backend failure" in errors_output.read_text(encoding="utf-8")

    dummy_repo = root / "pytorch"
    dummy_repo.mkdir()
    rerun_output = root / "rerun-zero-selected.csv"
    run_script(
        "rerun_pytest_cases.py",
        "--input",
        analyzed,
        "--output",
        rerun_output,
        "--repo",
        dummy_repo,
        "--only-marker",
        "not-selected",
    )
    assert len(read_csv(rerun_output)) == 4


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="torch-pytest-case-compare-") as temp:
        root = Path(temp)
        analyzed, _output_dir = check_comparison(root)
        check_log_extraction(root)
        check_log_analysis(root, analyzed)
        check_supporting_tools(root, analyzed)
    print("self-check passed: comparison, extraction, analysis, status, and rerun interfaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
