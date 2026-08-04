from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


HEADERS = [
    ("回路编号", "code", True, "文本，项目内唯一"),
    ("回路名称", "name", True, "文本"),
    ("相制", "phase", True, "1 或 3"),
    ("电压(V)", "voltage_v", True, "内部单位：V"),
    ("安装功率(kW)", "installed_power_kw", True, "内部单位：kW"),
    ("需用系数", "demand_factor", True, "0 < 值 ≤ 1"),
    ("功率因数", "power_factor", True, "0 < 值 ≤ 1"),
    ("效率", "efficiency", True, "0 < 值 ≤ 1"),
    ("线路长度(m)", "length_m", False, "内部单位：m"),
    ("电缆规格", "cable_spec", False, "仅作记录"),
    ("电缆载流量(A)", "cable_ampacity_a", False, "必须来自已核实产品数据"),
    ("线路电阻(Ω/km)", "cable_r_ohm_per_km", False, "必须注明采用工况"),
    ("线路电抗(Ω/km)", "cable_x_ohm_per_km", False, "必须注明采用工况"),
    ("电压降限值(%)", "voltage_drop_limit_pct", False, "须有已批准依据"),
    ("保护器件型号", "breaker_model", False, "仅作记录"),
    ("保护器件额定电流(A)", "breaker_rating_a", False, "来自产品参数"),
    ("分断能力(kA)", "breaking_capacity_ka", False, "来自产品参数"),
    ("电源电阻(Ω)", "source_r_ohm", False, "折算至回路电压侧"),
    ("电源电抗(Ω)", "source_x_ohm", False, "折算至回路电压侧"),
    ("变压器电阻(Ω)", "transformer_r_ohm", False, "折算至回路电压侧"),
    ("变压器电抗(Ω)", "transformer_x_ohm", False, "折算至回路电压侧"),
]

HEADER_MAP = {label: key for label, key, _, _ in HEADERS}
TEXT_FIELDS = {"code", "name", "phase", "cable_spec", "breaker_model"}
REQUIRED_FIELDS = {key for _, key, required, _ in HEADERS if required}

NAVY = "17324D"
TEAL = "008B8B"
PALE = "EAF3F5"
ORANGE = "F4A261"
WHITE = "FFFFFF"
GRID = Side(style="thin", color="D8E1E8")


def _style_title(sheet, last_col: int, title: str, subtitle: str) -> None:
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    sheet["A1"] = title
    sheet["A1"].font = Font(name="Microsoft YaHei", size=18, bold=True, color=WHITE)
    sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
    sheet["A1"].alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 34
    sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    sheet["A2"] = subtitle
    sheet["A2"].font = Font(name="Microsoft YaHei", size=10, color="52606D")
    sheet["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[2].height = 32


def _fit_columns(sheet, widths: list[float]) -> None:
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = min(width, 30)


def create_input_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "回路导入"
    _style_title(
        ws,
        len(HEADERS),
        "电气回路批量导入模板",
        "黄色表头为必填。请勿修改表头；示例数据仅用于说明字段格式，不是设计参数或规范取值。",
    )
    labels = [item[0] for item in HEADERS]
    ws.append([])
    ws.append(labels)
    example = [
        "AL-01", "示例照明回路", "1", 220, 5.0, 0.8, 0.9, 1.0, 35,
        "示例-请替换", None, None, None, None, "示例-请替换", None, None,
        None, None, None, None,
    ]
    ws.append(example)
    for col, (_, _, required, _) in enumerate(HEADERS, 1):
        cell = ws.cell(4, col)
        cell.font = Font(name="Microsoft YaHei", size=10, bold=True, color=WHITE if not required else NAVY)
        cell.fill = PatternFill("solid", fgColor=ORANGE if required else TEAL)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=GRID)
        ws.cell(5, col).fill = PatternFill("solid", fgColor="FFF8E8")
        ws.cell(5, col).font = Font(name="Microsoft YaHei", italic=True, color="7A5A00")
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:{get_column_letter(len(HEADERS))}5"
    ws.sheet_view.showGridLines = False
    phase_validation = DataValidation(type="list", formula1='"1,3"', allow_blank=False)
    ws.add_data_validation(phase_validation)
    phase_col = labels.index("相制") + 1
    phase_validation.add(f"{get_column_letter(phase_col)}5:{get_column_letter(phase_col)}1000")
    _fit_columns(ws, [14, 20, 8, 12, 16, 12, 12, 10, 14, 18] + [17] * 11)

    guide = wb.create_sheet("字段说明")
    _style_title(guide, 4, "字段说明", "内部单位固定；缺失、不适用与数值 0 必须区分。")
    guide.append([])
    guide.append(["字段", "必填", "内部字段", "规则"])
    for label, key, required, description in HEADERS:
        guide.append([label, "是" if required else "否", key, description])
    for cell in guide[4]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(name="Microsoft YaHei", bold=True, color=WHITE)
    guide.freeze_panes = "A5"
    guide.sheet_view.showGridLines = False
    _fit_columns(guide, [24, 10, 28, 52])
    for row in guide.iter_rows(min_row=5):
        row[3].alignment = Alignment(wrap_text=True, vertical="top")

    stream = BytesIO()
    wb.save(stream)
    return stream.getvalue()


def parse_circuit_workbook(content: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        return [], [f"无法读取 Excel 文件：{exc}"]
    ws = wb["回路导入"] if "回路导入" in wb.sheetnames else wb.active
    header_row = None
    mapping: dict[int, str] = {}
    for row_index in range(1, min(ws.max_row, 15) + 1):
        candidates = {index: HEADER_MAP.get(str(cell.value).strip()) for index, cell in enumerate(ws[row_index], 1) if cell.value is not None}
        if "code" in candidates.values() and "name" in candidates.values():
            header_row = row_index
            mapping = {index: key for index, key in candidates.items() if key}
            break
    if header_row is None:
        return [], ["未找到有效表头；请使用平台下载的导入模板。"]
    missing_headers = REQUIRED_FIELDS - set(mapping.values())
    if missing_headers:
        return [], ["缺少必填列：" + "、".join(sorted(missing_headers))]

    seen: set[str] = set()
    for row_index in range(header_row + 1, ws.max_row + 1):
        values = {key: ws.cell(row_index, col).value for col, key in mapping.items()}
        if not any(value not in (None, "") for value in values.values()):
            continue
        normalized: dict[str, Any] = {}
        row_errors: list[str] = []
        for key in HEADER_MAP.values():
            value = values.get(key)
            if key in TEXT_FIELDS:
                normalized[key] = "" if value is None else str(value).strip()
            elif value in (None, ""):
                normalized[key] = None
            else:
                try:
                    normalized[key] = float(value)
                except (TypeError, ValueError):
                    row_errors.append(f"{key} 不是有效数值")
        code = normalized.get("code", "")
        if not code:
            row_errors.append("回路编号不能为空")
        elif code in seen:
            row_errors.append(f"回路编号 {code} 在文件内重复")
        seen.add(code)
        for field in REQUIRED_FIELDS:
            if normalized.get(field) in (None, ""):
                row_errors.append(f"{field} 为必填项")
        if normalized.get("phase") not in {"1", "3"}:
            row_errors.append("相制必须为 1 或 3")
        for field in ("demand_factor", "power_factor", "efficiency"):
            value = normalized.get(field)
            if value is not None and not 0 < value <= 1:
                row_errors.append(f"{field} 必须大于 0 且不大于 1")
        nonnegative = [
            "voltage_v", "installed_power_kw", "length_m", "cable_ampacity_a",
            "cable_r_ohm_per_km", "cable_x_ohm_per_km", "voltage_drop_limit_pct",
            "breaker_rating_a", "breaking_capacity_ka", "source_r_ohm", "source_x_ohm",
            "transformer_r_ohm", "transformer_x_ohm",
        ]
        for field in nonnegative:
            value = normalized.get(field)
            if value is not None and value < 0:
                row_errors.append(f"{field} 不能为负数")
        if row_errors:
            errors.extend(f"第 {row_index} 行：{message}" for message in row_errors)
        else:
            rows.append(normalized)
    if not rows and not errors:
        errors.append("文件中没有可导入的数据行。")
    return rows, errors


def create_project_export(
    project: dict[str, Any],
    circuits: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> bytes:
    wb = Workbook()
    summary = wb.active
    summary.title = "项目汇总"
    _style_title(summary, 4, f"{project['name']} - 电气计算成果", "本文件为计算快照；正式性取决于各计算依据的批准状态。")
    summary.append([])
    summary.append(["项目编号", project["code"], "项目名称", project["name"]])
    summary.append(["回路数量", len(circuits), "计算记录", len(runs)])
    summary.append(["导出说明", "未批准依据的结果均为暂算，不能作为正式设计结论。", "", ""])
    summary.merge_cells("B7:D7")
    for row in range(4, 8):
        summary.cell(row, 1).font = Font(bold=True, color=NAVY)
    summary.sheet_view.showGridLines = False
    _fit_columns(summary, [18, 32, 18, 32])

    circuits_ws = wb.create_sheet("回路输入快照")
    circuit_headers = [label for label, _, _, _ in HEADERS] + ["数据修订版"]
    circuits_ws.append(circuit_headers)
    for circuit in circuits:
        circuits_ws.append([circuit.get(key) for _, key, _, _ in HEADERS] + [circuit.get("revision")])
    _format_table(circuits_ws, len(circuit_headers))

    result_ws = wb.create_sheet("计算记录")
    result_headers = ["记录ID", "回路编号", "模块", "正式状态", "暂算状态", "是否过期", "引擎版本", "计算时间"]
    result_ws.append(result_headers)
    for run in runs:
        result_ws.append(
            [
                run["id"], run["circuit_code"], run["module"], run["status"],
                run["provisional_status"], "是" if run["stale"] else "否",
                run["engine_version"], run["created_at"],
            ]
        )
    _format_table(result_ws, len(result_headers))

    detail_ws = wb.create_sheet("结果明细")
    detail_headers = ["记录ID", "回路编号", "模块", "结果项", "结果值", "正式状态", "暂算状态", "是否过期"]
    detail_ws.append(detail_headers)
    process_ws = wb.create_sheet("计算过程")
    process_headers = ["记录ID", "回路编号", "模块", "步骤序号", "步骤", "表达式", "结果", "单位"]
    process_ws.append(process_headers)
    warning_ws = wb.create_sheet("警告清单")
    warning_headers = ["记录ID", "回路编号", "模块", "正式状态", "是否过期", "警告"]
    warning_ws.append(warning_headers)
    snapshot_ws = wb.create_sheet("计算输入快照")
    snapshot_headers = ["记录ID", "回路编号", "模块", "字段", "值", "回路修订"]
    snapshot_ws.append(snapshot_headers)
    for run in runs:
        result_data = json.loads(run["result_json"]) if isinstance(run.get("result_json"), str) else run.get("result_json", {})
        process_data = json.loads(run["process_json"]) if isinstance(run.get("process_json"), str) else run.get("process_json", [])
        warning_data = json.loads(run["warnings_json"]) if isinstance(run.get("warnings_json"), str) else run.get("warnings_json", [])
        input_data = json.loads(run["input_snapshot"]) if isinstance(run.get("input_snapshot"), str) else run.get("input_snapshot", {})
        for key, value in result_data.items():
            detail_ws.append([run["id"], run["circuit_code"], run["module"], key, value, run["status"], run["provisional_status"], "是" if run["stale"] else "否"])
        for index, step in enumerate(process_data, 1):
            process_ws.append([run["id"], run["circuit_code"], run["module"], index, step.get("label"), step.get("expression"), step.get("value"), step.get("unit")])
        for warning in warning_data:
            warning_ws.append([run["id"], run["circuit_code"], run["module"], run["status"], "是" if run["stale"] else "否", warning])
        for key, value in input_data.items():
            if key not in {"id", "project_id", "created_at", "updated_at"}:
                snapshot_ws.append([run["id"], run["circuit_code"], run["module"], key, value, run.get("circuit_revision")])
    _format_table(detail_ws, len(detail_headers))
    _format_table(process_ws, len(process_headers))
    _format_table(warning_ws, len(warning_headers))
    _format_table(snapshot_ws, len(snapshot_headers))
    warning_ws.column_dimensions["F"].width = 60
    process_ws.column_dimensions["F"].width = 48

    rule_ws = wb.create_sheet("依据清单")
    rule_headers = ["依据编号", "名称", "状态", "文件", "版本", "条文号", "原文", "页码", "备注"]
    rule_ws.append(rule_headers)
    for rule in rules:
        rule_ws.append(
            [
                rule["code"], rule["name"], rule["status"], rule["document_name"],
                rule["document_version"], rule["clause_no"], rule["original_text"],
                rule["page_no"], rule["note"],
            ]
        )
    _format_table(rule_ws, len(rule_headers))
    rule_ws.column_dimensions["G"].width = 60
    for row in rule_ws.iter_rows(min_row=2):
        row[6].alignment = Alignment(wrap_text=True, vertical="top")

    stream = BytesIO()
    wb.save(stream)
    return stream.getvalue()


def _format_table(ws, last_col: int) -> None:
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(last_col)}{max(ws.max_row, 1)}"
    ws.sheet_view.showGridLines = False
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(name="Microsoft YaHei", bold=True, color=WHITE)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row_index in range(2, ws.max_row + 1):
        if row_index % 2 == 0:
            for cell in ws[row_index]:
                cell.fill = PatternFill("solid", fgColor=PALE)
    for column in range(1, last_col + 1):
        values = [str(ws.cell(row, column).value or "") for row in range(1, min(ws.max_row, 60) + 1)]
        ws.column_dimensions[get_column_letter(column)].width = min(max(len(value) for value in values) * 1.2 + 2, 24)
