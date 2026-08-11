from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


_SIMHEI_PATH = Path(r"C:\Windows\Fonts\simhei.ttf")


def _register_report_font(font_path: Path = _SIMHEI_PATH) -> str:
    if font_path.is_file():
        font_name = "SimHei"
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
        return font_name

    # GitHub 的 Windows 测试镜像未安装 SimHei。ReportLab 内置的中文 CID
    # 字体不依赖系统字体文件，可保证导入、测试和基础中文 PDF 输出可用。
    font_name = "STSong-Light"
    pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    return font_name


FONT = _register_report_font()

NETWORK_INPUT_LABELS = {
    "task_mode": "计算任务", "circuit_code": "回路编号", "circuit_name": "回路名称",
    "transformer_family": "变压器系列", "transformer_capacity_kva": "变压器容量(kVA)",
    "transformer_uk_percent": "变压器uk(%)",
    "upstream_short_circuit_capacity_mva": "上级系统短路容量(MVA)",
    "load_kind": "负荷类型", "load_basis": "已知量类型", "load_value": "负荷数值",
    "power_factor": "功率因数", "voltage_drop_limit_percent": "允许累计电压降(%)",
    "length_connection": "连接段长度(m)", "length_feeder": "馈线段长度(m)",
    "length_final": "末端段长度(m)", "configuration_connection": "连接段电缆结构",
    "configuration_feeder": "馈线段电缆结构", "configuration_final": "末端段电缆结构",
    "scenario_connection": "连接段敷设方式", "scenario_feeder": "馈线段敷设方式",
    "scenario_final": "末端段敷设方式", "temperature_connection": "连接段环境温度(℃)",
    "temperature_feeder": "馈线段环境温度(℃)", "temperature_final": "末端段环境温度(℃)",
}
NETWORK_VALUE_LABELS = {
    "design": "快速设计", "audit": "既有设计核验", "kw": "有功功率(kW)",
    "kva": "视在功率(kVA)", "a": "已知电流(A)", "ordinary": "普通三相负荷",
    "motor": "直接启动电动机", "tray": "槽盒", "direct_buried": "埋地管槽",
    "scb11": "SCB11干式变压器", "s11_m": "S11-M油浸式变压器",
    "yjv_3c_3ph_pe": "YJV三芯+独立PE", "yjv_4c_3ph_n_pe": "YJV四芯(3相+PE)",
    "yjv_5c_3ph_n_pe": "YJV五芯(3相+N+PE)",
}
NETWORK_DERIVED_LABELS = {
    "design_current_a": "末端计算电流(A)", "power_factor": "采用功率因数",
    "efficiency": "采用效率", "upstream_reference_capacity_mva": "上级系统参考容量(MVA)",
    "motor_reference": "电动机目录参数",
}
NETWORK_SEGMENT_LABELS = {
    "connection": "变压器低压出口-低压馈线柜",
    "feeder": "低压馈线柜-下级配电箱",
    "final": "下级配电箱-用电设备末端",
}


def network_input_rows(input_data: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    audit_mode = input_data.get("task_mode") == "audit"
    for key, value in input_data.items():
        if not audit_mode and (key.startswith("breaker_") or key.startswith("existing_section_")):
            continue
        label = NETWORK_INPUT_LABELS.get(key)
        if label is None and audit_mode:
            label = key
        if label is not None:
            rows.append([label, NETWORK_VALUE_LABELS.get(value, "" if value is None else value)])
    return rows


def network_derived_rows(derived: dict[str, Any]) -> list[list[Any]]:
    return [
        [NETWORK_DERIVED_LABELS.get(key, key), "" if value is None else value]
        for key, value in derived.items()
        if value not in (None, {}, [])
    ]


def create_run_pdf(run: dict[str, Any]) -> bytes:
    stream = BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"{run['project_name']}-{run['circuit_code']}-{run['module']}",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "cn-title", parent=styles["Title"], fontName=FONT, fontSize=19,
        leading=26, textColor=colors.HexColor("#17324D"), alignment=TA_LEFT,
    )
    heading = ParagraphStyle(
        "cn-heading", parent=styles["Heading2"], fontName=FONT, fontSize=12,
        leading=18, textColor=colors.HexColor("#17324D"), spaceBefore=10, spaceAfter=5,
    )
    body = ParagraphStyle(
        "cn-body", parent=styles["BodyText"], fontName=FONT, fontSize=9.5,
        leading=15, textColor=colors.HexColor("#243746"),
    )
    small = ParagraphStyle(
        "cn-small", parent=body, fontSize=8, leading=12, textColor=colors.HexColor("#52606D"),
    )
    status_color = "#B42318" if run["status"] != "通过" else "#067647"
    story: list[Any] = [
        Paragraph("电气工程计算书", title),
        Paragraph(
            f"{_e(run['project_code'])} / {_e(run['project_name'])} · "
            f"{_e(run['circuit_code'])} / {_e(run['circuit_name'])}",
            body,
        ),
        Spacer(1, 5 * mm),
        _table(
            [
                ["计算模块", run["module"], "记录编号", str(run["id"])],
                ["正式状态", run["status"], "暂算状态", run["provisional_status"]],
                ["计算版本", run["engine_version"], "是否过期", "是" if run["stale"] else "否"],
                ["计算时间", run["created_at"], "回路修订", str(run["circuit_revision"])],
            ],
            [28 * mm, 52 * mm, 28 * mm, 48 * mm],
        ),
        Spacer(1, 3 * mm),
        Paragraph(
            f'<font color="{status_color}">注意：正式状态为“{_e(run["status"])}”。'
            "未批准依据的暂算结果不能作为正式设计结论。</font>",
            body,
        ),
        Paragraph("一、输入快照", heading),
    ]
    input_rows = [["字段", "值"]]
    for key, value in run["input_snapshot"].items():
        if key in {"id", "project_id", "created_at", "updated_at"}:
            continue
        input_rows.append([key, "" if value is None else str(value)])
    story.append(_table(input_rows, [58 * mm, 98 * mm], header=True))
    story.extend([Paragraph("二、计算过程", heading)])
    process_rows = [["步骤", "表达式", "结果", "单位"]]
    for step in run["process_json"]:
        process_rows.append(
            [step.get("label", ""), step.get("expression", ""), str(step.get("value", "")), step.get("unit", "")]
        )
    story.append(_table(process_rows, [36 * mm, 72 * mm, 32 * mm, 16 * mm], header=True))
    story.extend([Paragraph("三、结果与警告", heading)])
    result_rows = [["结果项", "值"]]
    for key, value in run["result_json"].items():
        result_rows.append([key, str(value)])
    story.append(_table(result_rows, [72 * mm, 84 * mm], header=True))
    if run["warnings_json"]:
        story.append(Spacer(1, 2 * mm))
        for warning in run["warnings_json"]:
            story.append(Paragraph("- " + _e(warning), body))

    story.extend([PageBreak(), Paragraph("四、计算依据", heading)])
    approved_count = 0
    for code, rule in run["rule_snapshot"].items():
        story.append(Paragraph(f"{_e(code)} · {_e(rule.get('name', ''))}", body))
        story.append(
            _table(
                [
                    ["状态", rule.get("status", "")],
                    ["文件", rule.get("document_name", "")],
                    ["版本", rule.get("document_version", "")],
                    ["条文号", rule.get("clause_no", "")],
                    ["页码", rule.get("page_no", "")],
                    ["原文", rule.get("original_text", "")],
                ],
                [28 * mm, 128 * mm],
            )
        )
        story.append(Spacer(1, 3 * mm))
        approved_count += int(rule.get("status") == "approved")
    if approved_count == 0:
        story.append(Paragraph("当前没有已批准依据，故本计算书只能用于原型验证和算术复核。", body))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("生成说明：计算记录采用输入快照和依据快照，后续修改不会覆盖本记录。", small))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor("#66788A"))
        canvas.drawString(18 * mm, 10 * mm, f"记录 #{run['id']} · {run['module']}")
        canvas.drawRightString(192 * mm, 10 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def create_network_run_pdf(run: dict[str, Any]) -> bytes:
    """从不可变完整回路快照生成计算书，不重新执行计算。"""

    stream = BytesIO()
    input_data = run["input_snapshot"]
    result = run["result_json"]
    outputs = result.get("outputs", {})
    choices = outputs.get("viable_combinations") or outputs.get("incomplete_combinations") or []
    choice = choices[0] if choices else {}
    chain = choice.get("chain_result", {}).get("outputs", {})
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=17 * mm,
        bottomMargin=17 * mm,
        title=f"{run['project_name']}-{input_data.get('circuit_code', '')}-完整回路V{run['network_revision']}",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "network-title", parent=styles["Title"], fontName=FONT, fontSize=19,
        leading=26, textColor=colors.HexColor("#17324D"), alignment=TA_LEFT,
    )
    heading = ParagraphStyle(
        "network-heading", parent=styles["Heading2"], fontName=FONT, fontSize=12,
        leading=18, textColor=colors.HexColor("#17324D"), spaceBefore=9, spaceAfter=5,
    )
    body = ParagraphStyle(
        "network-body", parent=styles["BodyText"], fontName=FONT, fontSize=9,
        leading=14, textColor=colors.HexColor("#243746"),
    )
    small = ParagraphStyle(
        "network-small", parent=body, fontSize=8, leading=12,
        textColor=colors.HexColor("#52606D"),
    )
    status_color = "#067647" if run["status"] == "通过" else "#B54708"
    story: list[Any] = [
        Paragraph("完整低压回路计算书", title),
        Paragraph(
            f"{_e(run['project_code'])} / {_e(run['project_name'])} · "
            f"{_e(input_data.get('circuit_code'))} / {_e(input_data.get('circuit_name'))}",
            body,
        ),
        Spacer(1, 4 * mm),
        _table(
            [
                ["计算版本", f"V{run['network_revision']} / #{run['id']}", "任务", "既有设计核验" if run["task_mode"] == "audit" else "快速设计"],
                ["正式状态", run["status"], "暂算状态", run["provisional_status"]],
                ["引擎版本", run["engine_version"], "是否过期", "是" if run["stale"] else "否"],
                ["计算时间", run["created_at"], "输入快照", "已冻结"],
            ],
            [27 * mm, 54 * mm, 27 * mm, 54 * mm],
        ),
        Spacer(1, 3 * mm),
        Paragraph(
            f'<font color="{status_color}">正式状态为“{_e(run["status"])}”；'
            f'暂算状态为“{_e(run["provisional_status"])}”。未批准依据不能形成正式结论。</font>',
            body,
        ),
        Paragraph("一、项目输入条件", heading),
        _table(
            [["字段", "值"]]
            + network_input_rows(input_data),
            [63 * mm, 99 * mm],
            header=True,
        ),
        Paragraph("二、系统推导值", heading),
        _table(
            [["参数", "数值"]]
            + network_derived_rows(run["derived_json"]),
            [63 * mm, 99 * mm],
            header=True,
        ),
        Paragraph("三、主方案", heading),
    ]
    cable_rows = [["线路段", "电缆规格", "Ib(A)", "Iz(A)"]]
    for cable in choice.get("cables", []):
        cable_rows.append(
            [
                NETWORK_SEGMENT_LABELS.get(
                    str(cable.get("candidate_id", "")).split(":")[0],
                    str(cable.get("candidate_id", "")).split(":")[0],
                ),
                cable.get("cable_specification", ""),
                cable.get("minimum_required_ampacity_a", ""),
                cable.get("corrected_ampacity_a", ""),
            ]
        )
    story.append(_table(cable_rows, [27 * mm, 69 * mm, 33 * mm, 33 * mm], header=True))
    breaker_rows = [["安装点", "类别", "In(A)", "壳架(A)", "Ue(V)", "Icu(kA)"]]
    for index, breaker in enumerate(choice.get("breakers", []), 1):
        breaker_rows.append(
            [
                str(index), breaker.get("family", ""), breaker.get("rated_current_a", ""),
                breaker.get("frame_current_a", ""), breaker.get("rated_voltage_v", ""),
                breaker.get("selected_icu_ka", ""),
            ]
        )
    story.append(Spacer(1, 2 * mm))
    story.append(_table(breaker_rows, [23 * mm, 31 * mm, 25 * mm, 27 * mm, 27 * mm, 29 * mm], header=True))
    audit_outputs = run.get("audit_json", {}).get("outputs", {})
    if audit_outputs:
        story.append(Paragraph("四、原设计核验", heading))
        audit_rows = [["对象", "原规格/标识", "结论"]]
        for cable in audit_outputs.get("installed_cables", []):
            audit_rows.append([
                NETWORK_SEGMENT_LABELS.get(cable.get("segment_id", ""), cable.get("segment_id", "")),
                cable.get("designation", ""),
                cable.get("status", cable.get("reason", "无法判断")),
            ])
        for breaker in audit_outputs.get("installed_breakers", []):
            audit_rows.append([
                "断路器", breaker.get("designation", ""), breaker.get("status", "无法判断"),
            ])
        story.append(_table(audit_rows, [32 * mm, 86 * mm, 44 * mm], header=True))
    node_section = 5 if audit_outputs else 4
    story.append(Paragraph(f"{node_section_cn(node_section)}、逐节点结果", heading))
    node_rows = [["节点", "累计压降(%)", "最大三相短路(kA)", "最小相-PE故障(A)"]]
    for node in chain.get("node_results", []):
        node_rows.append(
            [
                node.get("node_name", ""), node.get("cumulative_voltage_drop_percent", ""),
                node.get("three_phase_short_circuit_ka", ""), node.get("earth_fault_current_a", ""),
            ]
        )
    story.append(_table(node_rows, [45 * mm, 36 * mm, 40 * mm, 41 * mm], header=True))
    story.append(Paragraph(f"{node_section_cn(node_section + 1)}、警告及未闭合项", heading))
    warnings = list(run.get("warnings_json", [])) + list(choice.get("missing_items", []))
    if warnings:
        for warning in dict.fromkeys(str(item) for item in warnings):
            story.append(Paragraph("- " + _e(warning), body))
    else:
        story.append(Paragraph("没有警告。", body))

    story.extend([PageBreak(), Paragraph(f"{node_section_cn(node_section + 2)}、计算依据快照", heading)])
    for code, rule in run["rule_snapshot"].items():
        story.append(Paragraph(f"{_e(code)} · {_e(rule.get('name', ''))}", body))
        story.append(
            _table(
                [
                    ["状态", rule.get("status", "")], ["文件", rule.get("document_name", "")],
                    ["条文/表号", rule.get("clause_no", "")], ["页码", rule.get("page_no", "")],
                    ["原文", rule.get("original_text", "")],
                ],
                [30 * mm, 132 * mm],
            )
        )
        story.append(Spacer(1, 3 * mm))
    if not run["rule_snapshot"]:
        story.append(Paragraph("本版本未返回可识别的依据编号。", body))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("本计算书直接使用不可覆盖的输入、结果和依据快照生成，导出时未重新计算。", small))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor("#66788A"))
        canvas.drawString(16 * mm, 9 * mm, f"完整回路版本 #{run['id']} · V{run['network_revision']}")
        canvas.drawRightString(194 * mm, 9 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def _table(rows: list[list[Any]], widths: list[float], header: bool = False) -> Table:
    normalized = [
        [Paragraph(_e(value), ParagraphStyle("cell", fontName=FONT, fontSize=8.5, leading=12)) for value in row]
        for row in rows
    ]
    table = Table(normalized, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D8E1E8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ]
        )
    else:
        commands.append(("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF3F5")))
    table.setStyle(TableStyle(commands))
    return table


def node_section_cn(value: int) -> str:
    return {4: "四", 5: "五", 6: "六", 7: "七"}[value]


def _e(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("²", "^2")
    )
