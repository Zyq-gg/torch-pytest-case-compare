#!/usr/bin/env python3
"""Locate and summarize PyTorch pytest failures in one or more logs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from compare_torch_pytest_sheets import CaseKey, clean_cell


EXCEPTION_RE = re.compile(
    r"((?:(?:torch|triton)\.[\w.]+|[A-Za-z_][\w.]*)?"
    r"(?:[A-Za-z_]\w*Error|Exception|Unsupported|Failure):\s*.*|AssertionError$)"
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STRONG_ERROR_MARKERS = (
    "AssertionError:",
    "RuntimeError:",
    "TypeError:",
    "ValueError:",
    "AttributeError:",
    "ImportError:",
    "ModuleNotFoundError:",
    "Unsupported:",
    "InductorError:",
    "HSACOError:",
    "SubprocException:",
    "HSA_STATUS_ERROR_MEMORY_APERTURE_VIOLATION",
    "KERNEL VMFault",
    "Cannot select:",
    "No valid triton configs",
    "OutOfResources",
    "Unexpected success",
    "Fatal Python error",
)


@dataclass(frozen=True)
class LogDocument:
    path: Path
    lines: list[str]
    lower: list[str]


@dataclass(frozen=True)
class LogEvidence:
    path: Path
    line: int
    confidence: str
    score: int
    error_type: str
    error_message: str
    category: str
    conclusion: str
    block: str


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", ANSI_RE.sub("", line).strip())


def path_variants(py_name: str) -> set[str]:
    path = clean_cell(py_name).replace("\\", "/").lstrip("./")
    while path.startswith("test/"):
        path = path[5:]
    return {path.lower(), f"test/{path}".lower()} if path else set()


def nodeid_needles(key: CaseKey) -> set[str]:
    suffix = f"::{key.class_name}::{key.op_name}" if key.class_name else f"::{key.op_name}"
    return {f"{path}{suffix}".lower() for path in path_variants(key.py_name)}


def block_end(lines: Sequence[str], start: int) -> int:
    limit = min(len(lines), start + 900)
    for index in range(start + 1, limit):
        if "To execute this test, run the following from the base repo dir:" in lines[index]:
            return min(len(lines), index + 3)
    for index in range(start + 6, limit):
        line = lines[index].strip()
        if re.match(r"^=+\s+(RERUNS|FAILURES|ERRORS|short test summary info)", line):
            return index
        if re.match(r"^_+\s+.+\s+_+$", line):
            return index
    return min(len(lines), start + 360)


def concise_error(output: str, returncode: int = 1, timeout: int = 0) -> str:
    if returncode == 0:
        return "PASSED"
    if returncode == 124:
        return f"TIMEOUT after {timeout}s"

    lines = [clean_line(line) for line in output.splitlines()]
    lines = [line for line in lines if line]

    for marker in (
        "HSA_STATUS_ERROR_MEMORY_APERTURE_VIOLATION",
        "KERNEL VMFault",
        "Fatal Python error",
    ):
        for line in lines:
            if marker in line:
                return line[:500]

    for line in lines:
        if "Unexpected success" in line:
            return "Unexpected success"

    for index, line in enumerate(lines):
        if "HSACOError" not in line:
            continue
        nearby = next((item for item in lines[index : index + 80] if "Cannot select:" in item), "")
        return f"{line}; {nearby}"[:500] if nearby else line[:500]

    for line in reversed(lines):
        if " - " in line and ("FAILED " in line or "ERROR " in line):
            detail = line.split(" - ", 1)[1].strip()
            if EXCEPTION_RE.search(detail):
                return detail[:500]

    candidates: list[str] = []
    for line in lines:
        match = EXCEPTION_RE.search(line)
        if match and not match.group(1).startswith("Exception: Caused by sample input"):
            candidates.append(match.group(1))
    if candidates:
        return candidates[-1][:500]

    for line in reversed(lines):
        if "No valid triton configs" in line or "OutOfResources" in line:
            return line[:500]
    for line in reversed(lines):
        if line.startswith(("FAILED ", "ERROR ")):
            return line[:500]
    return f"returncode={returncode}"


def error_type_from_message(message: str) -> str:
    if message == "PASSED":
        return "PASSED"
    if message.startswith("TIMEOUT"):
        return "TIMEOUT"
    if "Unexpected success" in message:
        return "UnexpectedSuccess"
    if "HSA_STATUS_ERROR_MEMORY_APERTURE_VIOLATION" in message or "KERNEL VMFault" in message:
        return "ROCm/HSA VMFault"
    match = re.search(r"((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Unsupported|Failure)):", message)
    return match.group(1) if match else "PytestFailure"


def classify_failure(message: str, text: str = "") -> tuple[str, str]:
    lower = f"{message}\n{text}".lower()
    if message == "PASSED":
        return "重跑通过", "当前重跑通过；原失败可能已修复，或受环境、时序、随机性影响。"
    if message.startswith("TIMEOUT"):
        return "测试超时", "pytest 重跑超时；需要结合完整日志判断停留在编译、autotune 还是测试执行阶段。"
    if "memory_aperture_violation" in lower or "kernel vmfault" in lower:
        return "ROCm/HSA 非法访存", "GPU kernel 发生 ROCm/HSA 非法访存，优先检查 Inductor/Triton 生成代码的索引、mask、stride 和动态 shape 边界。"
    if "hsacoerror" in lower or "cannot select:" in lower:
        return "ROCm HSACO 编译失败", "ROCm LLVM/HSACO 后端无法编译生成 kernel；需要区分后端能力缺口和上游生成了不支持的 IR。"
    if "outofresources" in lower or "no valid triton configs" in lower:
        return "Triton 资源或配置问题", "Triton/Inductor 候选配置超出硬件资源或没有有效配置，应检查 block、warp、stage 和后端 fallback。"
    if "tensor-likes are not close" in lower or "scalars are not close" in lower:
        return "数值正确性问题", "实际结果与参考结果超过容差；应比较 eager、编译路径、dtype、布局和后端算法选择。"
    if "unexpected success" in lower or "expected to fail, but actually passed" in lower:
        return "XFAIL 预期不一致", "测试被标记为预期失败但当前通过，应确认修复是否稳定并收窄或移除 xfail。"
    if "modulenotfounderror" in lower or "importerror" in lower:
        return "环境或依赖问题", "测试导入失败；应检查源码、安装包、PYTHONPATH、构建产物和测试依赖是否一致。"
    if "assertionerror" in lower:
        return "断言失败", "测试期望与实际行为不一致；需根据断言位置确认是数值、错误语义、图结构还是计数变化。"
    if "runtimeerror" in lower or "torchruntimeerror" in lower:
        return "运行时错误", "执行或编译阶段触发运行时异常；需结合 traceback 定位具体算子、shape、设备或编译路径。"
    error_type = error_type_from_message(message)
    return error_type, f"日志最终错误为 {message}" if message else "日志中未提取到明确错误。"


def analyze_output(output: str, returncode: int, timeout: int = 0) -> tuple[str, str, str, str]:
    message = concise_error(output, returncode, timeout)
    error_type = error_type_from_message(message)
    category, conclusion = classify_failure(message, output)
    return error_type, message, category, conclusion


class LogCorpus:
    def __init__(self, paths: Iterable[Path]):
        self.documents: list[LogDocument] = []
        self.blocks: list[tuple[LogDocument, int, str, str]] = []
        self.nodeid_lines: list[tuple[LogDocument, int, str]] = []
        for path in paths:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            document = LogDocument(path=path, lines=lines, lower=[line.lower() for line in lines])
            self.documents.append(document)
            self._index(document)

    def _index(self, document: LogDocument) -> None:
        for index, line in enumerate(document.lines):
            lower_line = line.lower()
            aggregate_line = (
                ("running " in lower_line and " items in this shard:" in lower_line)
                or "the following tests failed consistently:" in lower_line
                or "the following tests failed and then succeeded" in lower_line
                or lower_line.strip().startswith("failed consistently:")
            )
            if "::" in line and not aggregate_line:
                self.nodeid_lines.append((document, index, line))
            stripped = line.strip()
            if not re.match(r"^_+\s+.+\s+_+$", stripped):
                continue
            end = block_end(document.lines, index)
            text = "\n".join(document.lines[max(0, index - 1) : end])
            if any(marker.lower() in text.lower() for marker in STRONG_ERROR_MARKERS):
                self.blocks.append((document, index, stripped, text))

    @staticmethod
    def _score(text: str, key: CaseKey, exact_nodeid: bool) -> int:
        lower = text.lower()
        score = 130 if exact_nodeid else 0
        if any(path in lower for path in path_variants(key.py_name)):
            score += 55
        if key.class_name and key.class_name.lower() in lower:
            score += 35
        if key.op_name.lower() in lower:
            score += 45
        if any(marker.lower() in lower for marker in STRONG_ERROR_MARKERS):
            score += 45
        if " failures " in lower or " reruns " in lower:
            score += 10
        return score

    def find(self, key: CaseKey) -> LogEvidence | None:
        needles = nodeid_needles(key)
        candidates: list[tuple[int, LogDocument, int, str]] = []

        for document, index, line in self.nodeid_lines:
            line_lower = line.lower()
            exact = any(needle in line_lower for needle in needles)
            if not exact:
                continue
            start = max(0, index - 140)
            end = min(len(document.lines), index + 360)
            block = "\n".join(document.lines[start:end])
            candidates.append((self._score(block, key, True), document, index, block))

        for document, index, header, block in self.blocks:
            lower = block.lower()
            exact = any(needle in lower for needle in needles)
            header_match = key.op_name.lower() in header.lower() and (
                not key.class_name or key.class_name.lower() in header.lower()
            )
            if exact or header_match:
                candidates.append((self._score(block, key, exact), document, index, block))

        if not candidates:
            fallback = [item for item in self.blocks if key.op_name.lower() in item[3].lower()]
            if len(fallback) == 1:
                document, index, _header, block = fallback[0]
                candidates.append((self._score(block, key, False), document, index, block))

        if not candidates:
            return None
        score, document, index, block = max(candidates, key=lambda item: item[0])
        confidence = "high" if score >= 230 else "medium" if score >= 150 else "low"
        message = concise_error(block)
        error_type = error_type_from_message(message)
        category, conclusion = classify_failure(message, block)
        return LogEvidence(
            path=document.path,
            line=index + 1,
            confidence=confidence,
            score=score,
            error_type=error_type,
            error_message=message,
            category=category,
            conclusion=conclusion,
            block=block,
        )
