# torch-pytest-case-compare

一个面向 PyTorch pytest 测试结果整理的 Codex skill 和命令行工具集。

它以表格对比为主流程：从多个 XLSX/XLSM 工作簿的所有 sheet 中识别 pytest case，统一三元组格式、去重并保留全部来源，再与新的 CSV 结果比较。需要进一步定位问题时，还可以从 `run_test.py` 或普通 pytest 日志中提取失败 case、为表格补充日志证据，并按精确 nodeid 单独重跑 case 后回填结果。

## 主要能力

| 能力 | 说明 | 入口脚本 |
| --- | --- | --- |
| 表格 case 汇总 | 扫描目录内所有 XLSX/XLSM 和所有 sheet，动态识别表头并抽取 pytest 三元组 | `compare_torch_pytest_sheets.py` |
| 归一化与去重 | 统一路径和空白格式，按 `py_name + class_name + op_name` 去重，同时保留原值和全部来源 | `compare_torch_pytest_sheets.py` |
| CSV 新增 case 对比 | 找出 CSV 中未出现在历史标记表里的 case，并在原 CSV 结构上增加标记与分析列 | `compare_torch_pytest_sheets.py` |
| 稳定失败提取 | 从 `run_test.py` 最终稳定失败列表或普通 pytest summary 中提取失败 case | `extract_pytest_failures.py` |
| 日志证据定位 | 根据文件、类名、case 名和 nodeid 匹配日志中的具体错误，记录来源与置信度 | `analyze_pytest_cases.py` |
| 单 case 重跑 | 使用精确的 `文件::类::case` nodeid 调用 pytest，保存完整日志并回填结论 | `rerun_pytest_cases.py` |
| 日志完整性检查 | 判断 run_test 中各测试文件是完成、失败、待检查还是中断 | `check_run_test_log_status.py` |
| Inductor 错误归类 | 从大型日志中提取去重后的 Error/Exception 信息和出现次数 | `extract_inductor_unique_errors.py` |

表格比较能力完整继承自 `torch-pytest-sheet-compare`。日志提取、错误定位和单 case 重跑能力用于补充对比结果，不会替代主表格流程。

## 环境要求

- Python 3.10 或更高版本。
- 表格比较和日志提取仅使用 Python 标准库，不要求安装 `pandas` 或 `openpyxl`。
- 单 case 重跑需要目标 PyTorch 仓库及其可运行的 pytest 环境。
- 输入工作簿应为标准 Office Open XML 格式，即 `.xlsx` 或 `.xlsm`。
- 仓库不依赖当前机器上的其他 skill、私有脚本或 fixture；解析所需代码均包含在本仓库中。

## 安装

### 作为 Codex skill 安装

将仓库克隆到 Codex skills 目录：

```bash
git clone https://github.com/Zyq-gg/torch-pytest-case-compare.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/torch-pytest-case-compare"
```

安装后，Codex 在处理 PyTorch pytest 表格对比、日志失败 case 提取和重跑分析任务时可以加载该 skill。

更新已有安装：

```bash
git -C "${CODEX_HOME:-$HOME/.codex}/skills/torch-pytest-case-compare" pull --ff-only
```

### 作为普通脚本使用

也可以克隆到任意目录后直接运行：

```bash
git clone https://github.com/Zyq-gg/torch-pytest-case-compare.git
cd torch-pytest-case-compare
python3 scripts/compare_torch_pytest_sheets.py --help
```

克隆后可以先运行仓库自带的自检。它会在临时目录生成合成 XLSX、CSV
和日志，不需要额外下载测试数据，也不会真正执行 pytest：

```bash
python3 scripts/self_check.py
```

## Case 三元组与归一化规则

每一条 pytest case 使用以下三元组标识：

```text
(py_name, class_name, op_name)
```

不同表格可以使用不同列名。例如：

| Python 文件列 | 类名列 | case 列 |
| --- | --- | --- |
| `py name` | `class name` | `op name` |
| `test_file` | `class_name` | `case_name` |
| `file` | `class` | `test_name` |
| `测试文件` | `测试类` | `测试用例` |

脚本会在每个 sheet 中动态查找表头，而不是假设表头固定在第一行，因此表格前面可以存在标题、说明或空行。表头必须能识别出文件列、类名列和 case 列；函数式 pytest case 可以让具体数据行的类名值为空。

比较前会执行以下归一化：

- 去除字段首尾空白，并折叠连续空白。
- 将 Python 路径中的 `\` 转换成 `/`。
- 去掉路径开头的 `./`、`/` 和重复的 `test/`。
- 类名和 case 名不做模糊匹配，只整理空白。
- `py_name` 或 `op_name` 为空的数据行不会形成有效三元组。

归一化只用于建立比较 key。输出中仍保留来源表里的原始三元组值，方便审计。

## 一、汇总 XLSX 并与 CSV 对比

假设目录结构如下：

```text
case-data/
├── marked_cases_a.xlsx
├── marked_cases_b.xlsx
├── fsdp_failed_cases.xlsm
└── latest_pytest_results.csv
```

运行：

```bash
python3 scripts/compare_torch_pytest_sheets.py case-data \
  --csv case-data/latest_pytest_results.csv \
  --out-dir case-data/compare_out
```

脚本会：

1. 扫描输入目录第一层的所有 `.xlsx` 和 `.xlsm` 文件，忽略 `~$` 开头的 Office 临时文件。
2. 遍历每个工作簿的所有 sheet，并独立识别每个 sheet 的三元组表头。
3. 抽取全部有效数据行，保留工作簿名、sheet 名、行号、原始三元组和整行内容。
4. 按归一化三元组去重，同时汇总一个 case 的所有来源。
5. 读取 CSV 并找出未出现在 XLSX case 集合中的新增 case。
6. 在 CSV 中寻找“表头为空且所有数据行也为空”的列作为 `case标记`；找不到时才追加新列。
7. 生成带 `case标记`、`问题类别` 和 `问题结论` 的分析结果。

可以修改标记列名称：

```bash
python3 scripts/compare_torch_pytest_sheets.py case-data \
  --csv case-data/latest_pytest_results.csv \
  --out-dir case-data/compare_out \
  --marker-column-name 对比结果
```

### 表格对比输出

| 输出文件 | 内容 |
| --- | --- |
| `marked_cases_all_rows.csv` | XLSX 中抽取出的每一条有效数据行，不去重；包含来源文件、sheet、行号、原始三元组和整行 JSON |
| `marked_cases_unique.csv` | 去重后的完整 case 集合；`source_count` 和 `sources_json` 保存全部来源 |
| `csv_new_cases.csv` | 只保留 CSV 中新增的 case，并在前面增加归一化后的三元组 |
| `<原CSV文件名>_analyzed.csv` | 保留原 CSV 全部行和原有列，并增加 case 标记、问题类别和问题结论 |
| `summary.json` | 输入文件、抽取数量、去重数量、新增数量、空列复用情况、归一化规则和输出路径 |

所有生成的 CSV 使用 UTF-8 BOM，便于直接用 Excel 打开中文内容。

### 分析结果中的三列

`case标记`：

- CSV case 已在 XLSX 中标记：写入它在所有来源工作簿中的位置，例如 `a.xlsx/Sheet1:row12; b.xlsx/失败项:row8`。
- CSV case 未出现在 XLSX 中：写入 `新增`。
- CSV 行缺少有效三元组：保持为空。

`问题类别`：

- 优先根据 CSV 原有的 `error_type/error_message` 生成可聚合的问题类型。
- 会弱化错误信息中的数字差异，便于将同类错误放到一起观察。
- 没有错误信息时写入 `无报错信息`。

`问题结论`：

- 已标记 case 优先合并 CSV 报错与来源表中的非三元组备注。
- 新增 case 若与已标记 case 有相同错误信息，会引用同类来源备注作为分析参考。
- 没有明确证据时保留原始报错或明确写出“无明确报错信息”，不会用来源文件和行号冒充问题结论。

原始 CSV 不会被覆盖，除非使用者自己把输出路径指回原文件。流程中不再生成多余的中间 `*_annotated.csv`。

## 二、从日志提取失败 case

### 自动识别日志类型

```bash
python3 scripts/extract_pytest_failures.py \
  /path/to/run_test_gpu_0.log \
  /path/to/run_test_gpu_1.log \
  --output failures.csv
```

输出也可以是 XLSX：

```bash
python3 scripts/extract_pytest_failures.py /path/to/run_test_gpu_*.log \
  --output failures.xlsx
```

`--mode auto` 是默认模式：

- 日志包含 `Name: tests to run`、`run_test.py --include`、`FAILED CONSISTENTLY` 或重试成功摘要时，按 run_test 日志处理。
- 对 run_test 日志，只读取最终 `The following tests failed consistently` 列表。
- 首次失败但在新进程重试后成功的 case 不会被当成稳定失败。
- run_test 日志没有最终稳定失败列表时，输出 0 条，而不是退回去收集过程中的 pytest `FAILED` 行。
- 不具备 run_test 特征的日志按普通 pytest 日志处理，从 short summary 中读取 `FAILED` 和 `ERROR` 行。

也可以强制指定解析方式：

```bash
# 强制按 run_test 最终稳定失败列表解析
python3 scripts/extract_pytest_failures.py run_test.log \
  --mode run-test --output stable_failures.csv

# 强制按普通 pytest summary 解析
python3 scripts/extract_pytest_failures.py pytest.log \
  --mode pytest --output pytest_failures.csv
```

提取结果包含：

```text
source_log, source_line, test_file, class_name, case_name,
error_type, error_message, nodeid, raw
```

输出 XLSX 时，每个输入日志对应一个 sheet。

## 三、给对比结果补充日志证据

表格对比完成后，可以对其中的 case 搜索日志证据：

```bash
python3 scripts/analyze_pytest_cases.py \
  --input case-data/compare_out/latest_pytest_results_analyzed.csv \
  --logs /path/to/run_test_gpu_0.log /path/to/run_test_gpu_1.log \
  --output case-data/compare_out/latest_pytest_results_log_analyzed.csv \
  --only-marker 新增
```

不指定 `--only-marker` 时会分析输入 CSV 的所有行。标记列名称不是 `case标记` 时，可传入：

```bash
--marker-column 对比结果
```

脚本按 nodeid、文件路径、类名、case 名和错误特征为日志候选打分，并新增：

| 列 | 含义 |
| --- | --- |
| `日志错误类型` | 从最佳匹配证据中识别出的错误类型 |
| `日志错误信息` | case 附近最有代表性的具体错误信息 |
| `日志来源` | 完整日志路径和行号，用于回溯原文 |
| `日志匹配置信度` | `high/medium/low` 及匹配得分 |

只有高、中置信度证据会更新 `问题类别` 和 `问题结论`。低置信度匹配只保留在日志证据列中，避免把同名 case 或汇总行错误关联到当前 case。

只增加证据列、不改已有分析列：

```bash
python3 scripts/analyze_pytest_cases.py \
  --input input.csv \
  --logs /path/to/run_test.log \
  --output output.csv \
  --no-update-analysis
```

脚本同时生成 `<输出CSV>.summary.json`，记录选中、匹配、各置信度和未匹配的行数。

## 四、精确重跑单个 case 并回填

重跑会真实执行 pytest，建议先用 `--only-marker` 和 `--limit` 做小范围验证：

```bash
python3 scripts/rerun_pytest_cases.py \
  --input case-data/compare_out/latest_pytest_results_analyzed.csv \
  --output case-data/compare_out/latest_pytest_results_rerun.csv \
  --repo /path/to/pytorch \
  --env /path/to/env.sh \
  --only-marker 新增 \
  --limit 10 \
  --timeout 300
```

执行规则：

- 使用表格原始值构造精确 nodeid：`文件::类::case`；类名为空时使用 `文件::case`。
- 文件路径不存在时，会尝试在前面补充 `test/`。
- 相同归一化三元组只执行一次，结果回填到所有对应行。
- 每个 case 保存一份完整 stdout/stderr 合并日志。
- 每完成一个 case 就刷新输出 CSV，长时间运行中也能保留已完成结果。
- 超时返回码按 `124` 处理，并标记为 `超时`。

新增列包括：

```text
重跑状态, 重跑错误类型, 重跑错误信息, 重跑问题类别,
重跑问题结论, 重跑日志, 重跑耗时秒
```

默认写入单独输出文件。若明确需要原地更新，可使用：

```bash
python3 scripts/rerun_pytest_cases.py \
  --input result.csv \
  --in-place \
  --repo /path/to/pytorch \
  --only-marker 新增
```

原地更新前会创建带时间戳的备份文件。所有重跑的结构化结果还会写入日志目录中的 `summary.jsonl`。

不希望重跑结果覆盖原有 `问题类别/问题结论` 时，增加 `--no-update-analysis`。

`--repo` 是必填参数。目标 PyTorch checkout 和其中的 pytest 运行环境属于
用户输入，不是本 skill 的依赖，因此不会被打包进仓库。

## 五、辅助工具

### 检查 run_test 日志是否完整

```bash
python3 scripts/check_run_test_log_status.py \
  /path/to/run_test_gpu_0.log \
  /path/to/run_test_gpu_1.log
```

状态含义：

- `ok`：找到明确的完成标记，或失败 case 在新进程中重试成功。
- `error`：测试文件被 run_test 标记为失败。
- `check`：出现了 pytest nodeid，但缺少明确的结束标记，需要人工检查。
- `interrupted`：测试在执行计划中，但没有找到后续 pytest 记录。

机器可读输出：

```bash
python3 scripts/check_run_test_log_status.py run_test.log --format tsv
```

### 提取 Inductor 唯一错误

提取完整错误块：

```bash
python3 scripts/extract_inductor_unique_errors.py inductor.log \
  --mode block --show-lines \
  --output unique_errors.txt
```

只保留一行错误并统计次数：

```bash
python3 scripts/extract_inductor_unique_errors.py inductor.log \
  --mode one-line --with-count \
  --output unique_errors.txt
```

## 推荐工作流

对于“历史标记表 + 新一轮 pytest CSV + run_test 日志”的典型任务，建议按以下顺序处理：

1. 使用 `compare_torch_pytest_sheets.py` 汇总历史 XLSX，得到去重 case 集合和带标记的分析 CSV。
2. 检查 `summary.json` 中的抽取数量、跳过行数和新增 case 数量。
3. 使用 `check_run_test_log_status.py` 判断日志是否完整，避免把未跑完误判为无失败。
4. 使用 `analyze_pytest_cases.py --only-marker 新增` 为新增 case 定位已有日志证据。
5. 对缺少明确结论、需要确认当前状态的少量 case 使用 `rerun_pytest_cases.py`。
6. 保留日志来源和重跑日志列，确保每个结论都能回溯到原始证据。

## 稳定性与验证基线

原 `torch-pytest-sheet-compare` 的比较脚本以完整文件形式保留，并使用 Torch 2.9 fixture 做回归验证：

| 指标 | 基线值 |
| --- | ---: |
| XLSX 抽取数据行 | 5175 |
| XLSX 去重 case | 2230 |
| 输入 CSV 行数 | 1201 |
| CSV 新增 case | 301 |
| 缺少有效三元组而跳过的 CSV 行 | 0 |

四个核心 CSV 输出与原 skill 基线逐字节一致：

- `marked_cases_all_rows.csv`
- `marked_cases_unique.csv`
- `csv_new_cases.csv`
- `<原CSV文件名>_analyzed.csv`

日志流程还覆盖了以下测试场景：run_test 中间失败和最终稳定失败、重试后成功、普通 pytest summary、类方法和函数式 nodeid、同名方法不同类、日志聚合行、重跑通过、重跑失败、超时以及筛选结果为空。

## 使用限制与注意事项

- 三元组比较是精确比较，不自动推断文件重命名、历史路径映射或 case 别名。
- 工作簿中的公式单元格应包含可读取的缓存结果；复杂 Excel 特性不属于本工具处理范围。
- `run_test` auto 模式以稳定失败为目标。若你确实要研究首次失败或瞬时失败，请显式使用 `--mode pytest` 或直接执行日志证据分析。
- 日志匹配结果带有置信度。低置信度证据不应直接作为最终问题结论。
- pytest 重跑可能消耗大量时间和 GPU 资源，优先使用 `--limit` 和合理的 `--timeout`。
- 重跑使用当前代码、环境和硬件，结果不一定复现历史日志中的失败。

## 仓库结构

```text
torch-pytest-case-compare/
├── SKILL.md
├── README.md
├── agents/
│   └── openai.yaml
├── references/
│   └── workflows.md
└── scripts/
    ├── compare_torch_pytest_sheets.py
    ├── extract_pytest_failures.py
    ├── analyze_pytest_cases.py
    ├── rerun_pytest_cases.py
    ├── log_failure_analysis.py
    ├── check_run_test_log_status.py
    ├── extract_inductor_unique_errors.py
    └── self_check.py
```

- `SKILL.md`：Codex 加载 skill 后遵循的核心工作流程。
- `references/workflows.md`：case identity、输出契约、日志匹配、重跑和验证细节。
- `scripts/`：可独立执行或被 skill 调用的确定性脚本。
- `scripts/self_check.py`：只使用仓库内容和 Python 标准库完成便携性自检。

## License

当前仓库尚未添加独立许可证文件。公开可见不等于自动授予再分发或修改许可；需要复用到其他项目时，请先与仓库维护者确认。
