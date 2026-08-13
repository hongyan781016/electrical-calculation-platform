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
    "transformer_code": "变压器编号", "bus_section_code": "低压母线段编号",
    "feeder_cabinet_code": "馈线柜编号",
    "transformer_actual_model": "图纸实际变压器型号",
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
    "connection_line_type": "变压器出口连接部件", "busway_series_code": "母线槽系列",
    "busway_rating_a": "母线槽额定电流(A)",
}
NETWORK_VALUE_LABELS = {
    "design": "快速设计", "audit": "既有设计核验", "kw": "有功功率(kW)",
    "kva": "视在功率(kVA)", "a": "已知电流(A)", "ordinary": "普通三相负荷",
    "motor": "直接启动电动机", "tray": "槽盒", "direct_buried": "埋地管槽",
    "cable": "YJV电缆", "busway": "母线槽", "canalis_kta_3lnpe": "Canalis KTA 3L+N+PE",
    "scb11": "SCB11干式变压器", "s11_m": "S11-M油浸式变压器",
    "yjv_3c_3ph_pe": "YJV三芯+独立PE", "yjv_4c_3ph_n_pe": "YJV四芯(3相+PE)",
    "yjv_5c_3ph_n_pe": "YJV五芯(3相+N+PE)",
}
NETWORK_DERIVED_LABELS = {
    "design_current_a": "末端计算电流(A)", "power_factor": "采用功率因数",
    "efficiency": "采用效率", "upstream_reference_capacity_mva": "上级系统参考容量(MVA)",
    "motor_reference": "电动机目录参数",
    "busway_inputs": "母线槽查表参数",
}
NETWORK_SEGMENT_LABELS = {
    "connection": "变压器低压出口-低压馈线柜",
    "feeder": "低压馈线柜-下级配电箱",
    "final": "下级配电箱-用电设备末端",
}
AUDIT_CHECK_LABELS = {
    "rated_capacity": "变压器容量边界", "voltage": "电压匹配", "impedance": "短路阻抗输入",
    "rated_voltage": "额定电压", "rated_current": "额定电流", "short_time_withstand": "短时耐受",
    "ampacity": "载流量", "pe_section": "PE截面", "load_current": "负荷电流",
    "cable_coordination": "电缆保护配合", "breaking_capacity": "分断能力",
    "automatic_disconnection": "自动切断", "phase_thermal": "相导体热稳定", "pe_thermal": "PE热稳定",
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
        audit_rows = [["部件", "原规格/标识", "核验项", "结论"]]
        for component in audit_outputs.get("component_matrix", []):
            for code, check in component.get("checks", {}).items():
                audit_rows.append([
                    component.get("component_name", component.get("component_type", "")),
                    component.get("designation", ""),
                    AUDIT_CHECK_LABELS.get(code, code),
                    check.get("status", "无法判断"),
                ])
        for check in audit_outputs.get("cross_component_checks", []):
            audit_rows.append([
                "跨部件配合", check.get("check_name", ""), "系统配合",
                check.get("status", "无法判断"),
            ])
        story.append(_table(audit_rows, [25 * mm, 65 * mm, 42 * mm, 30 * mm], header=True))
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


def create_motor_run_pdf(run: dict[str, Any]) -> bytes:
    """从不可变电动机快照生成简明计算书。"""
    stream = BytesIO()
    result = run["result_json"]
    load = result.get("load", {}).get("outputs", {})
    candidate = result.get("recommended_candidate") or {}
    scheme = candidate.get("primary_scheme") or {}
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=17*mm, bottomMargin=17*mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("motor-title", parent=styles["Title"], fontName=FONT, fontSize=19, leading=26, textColor=colors.HexColor("#17324D"), alignment=TA_LEFT)
    heading = ParagraphStyle("motor-heading", parent=styles["Heading2"], fontName=FONT, fontSize=12, leading=18, textColor=colors.HexColor("#17324D"))
    body = ParagraphStyle("motor-body", parent=styles["BodyText"], fontName=FONT, fontSize=9, leading=14)
    story: list[Any] = [Paragraph("三相电动机回路计算书", title), Paragraph(f"{_e(run['project_code'])} / {_e(run['project_name'])} · {_e(run['input_snapshot'].get('circuit_code'))}", body), Spacer(1, 4*mm)]
    story.append(_table([["计算版本", f"V{run['motor_revision']} / #{run['id']}", "引擎", run['engine_version']], ["正式状态", run['status'], "暂算状态", run['provisional_status']], ["额定电流(A)", load.get("rated_current_a", ""), "启动电流(A)", load.get("starting_current_a", "")]], [30*mm, 51*mm, 30*mm, 51*mm]))
    story.extend([Paragraph("一、输入快照", heading), _table([["字段", "值"]] + [[key, value] for key,value in run["input_snapshot"].items()], [63*mm, 99*mm], header=True), Paragraph("二、主方案", heading)])
    story.append(_table([["电缆", candidate.get("cable", {}).get("cable_specification", "")], ["短路保护", scheme.get("breaker", "")], ["接触器", scheme.get("contactor", "")], ["过载保护", scheme.get("overload_device", "")]], [40*mm, 122*mm]))
    starting = candidate.get("starting_voltage", {}).get("outputs", {})
    story.extend([Paragraph("三、启动与故障校核", heading), _table([["启动母线电压(%)", starting.get("starting_bus_voltage_percent", "")], ["电动机端子电压(%)", starting.get("starting_motor_terminal_voltage_percent", "")], ["安装点最大短路(kA)", candidate.get("chain", {}).get("outputs", {}).get("node_results", [{}])[0].get("three_phase_short_circuit_ka", "")], ["末端相-PE故障(A)", candidate.get("chain", {}).get("outputs", {}).get("terminal_earth_fault_current_a", "")]], [63*mm,99*mm])])
    story.append(Paragraph("四、未闭合项", heading))
    for item in scheme.get("professional_pending", []): story.append(Paragraph("- " + _e(item), body))
    story.append(PageBreak()); story.append(Paragraph("五、计算依据快照", heading))
    for code, rule in run["rule_snapshot"].items(): story.append(Paragraph(f"{_e(code)} · {_e(rule.get('document_name',''))} · {_e(rule.get('clause_no',''))} · {_e(rule.get('page_no',''))}", body))
    doc.build(story)
    return stream.getvalue()


def create_drawing_project_pdf(
    project: dict[str, Any], circuits: list[dict[str, Any]], summary: dict[str, Any],
    settings: dict[str, Any],
) -> bytes:
    """生成项目当前图纸回路核验汇总，不重新执行单回路计算。"""
    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=14*mm, leftMargin=14*mm,
                            topMargin=16*mm, bottomMargin=16*mm,
                            title=f"{project.get('name')}-图纸逐回路核验汇总")
    styles = getSampleStyleSheet()
    title = ParagraphStyle("drawing-project-title", parent=styles["Title"], fontName=FONT,
                           fontSize=18, leading=25, textColor=colors.HexColor("#17324D"), alignment=TA_LEFT)
    heading = ParagraphStyle("drawing-project-heading", parent=styles["Heading2"], fontName=FONT,
                             fontSize=12, leading=18, textColor=colors.HexColor("#17324D"), spaceBefore=8)
    body = ParagraphStyle("drawing-project-body", parent=styles["BodyText"], fontName=FONT,
                          fontSize=8.5, leading=13, textColor=colors.HexColor("#243746"))
    story = [Paragraph("图纸逐回路核验汇总", title), Paragraph(
        f"项目：{_e(project.get('code'))} · {_e(project.get('name'))}", body), Spacer(1, 4*mm),
        Paragraph("项目级负荷汇总", heading),
        _table([
            ["有效回路", str(summary.get("circuit_count", 0)), "Ib算术合计", f"{_e(summary.get('arithmetic_total_current_a'))} A"],
            ["同时系数", _e(summary.get("simultaneity_factor")), "上游计算电流", f"{_e(summary.get('upstream_design_current_a'))} A"],
            ["变压器额定电流", f"{_e(summary.get('transformer_rated_current_a'))} A", "容量复核", _e(summary.get("transformer_capacity_status"))],
            ["系数来源/说明", _e(settings.get("source_note")), "电源树一致", "是" if summary.get("source_consistent") else "否"],
        ], [34*mm, 56*mm, 34*mm, 56*mm]),
        Paragraph("当前有效回路", heading),
    ]
    rows = [["回路", "名称", "Ib(A)", "正式状态", "暂算状态", "修订"]]
    for item in circuits:
        rows.append([_e(item.get("circuit_code")), _e(item.get("circuit_name")),
                     _e(item.get("derived_json", {}).get("design_current_a")),
                     _e(item.get("status") or "未计算"), _e(item.get("provisional_status") or "未计算"),
                     f"V{item.get('revision')}"])
    story.append(_table(rows, [24*mm, 54*mm, 25*mm, 27*mm, 27*mm, 16*mm], header=True))
    story.append(Paragraph("上游配电分组", heading))
    group_rows = [["层级/标识", "设计/额定(A)", "Ikmax(kA)", "Icw", "Icu", "选择性"]]
    for level, key in (("馈线柜", "feeder_cabinet_groups"), ("母线段", "bus_section_groups"), ("变压器", "transformer_groups")):
        for group in summary.get(key, []):
            direct = group.get("arithmetic_total_current_a") if level == "馈线柜" else group.get("direct_child_current_a")
            group_rows.append([f"{level} {'/'.join(group['codes'])}",
                               f"{_e(group.get('design_current_a'))}/{_e(group.get('rated_current_a'))}",
                               _e(group.get("prospective_short_circuit_ka")),
                               f"{_e(group.get('short_time_withstand_ka'))} · {_e(group.get('short_time_withstand_status'))}",
                               f"{_e(group.get('breaker_breaking_capacity_ka'))} · {_e(group.get('breaking_capacity_status'))}",
                               _e(group.get("selectivity_status"))])
    story.append(_table(group_rows, [52*mm, 35*mm, 26*mm, 28*mm, 28*mm, 22*mm], header=True))
    story.append(Paragraph("每层系数只作用于直接下级汇总；任一级缺失时不越级计算。", body))
    completeness = summary.get("completeness", {})
    story.append(Paragraph("完整性总览与发布闸门", heading))
    story.append(_table([
        ["通过", completeness.get("counts", {}).get("通过", 0), "不通过", completeness.get("counts", {}).get("不通过", 0)],
        ["无法判断", completeness.get("counts", {}).get("无法判断", 0), "问题总数", completeness.get("issue_count", 0)],
        ["工程数据闭合", completeness.get("engineering_data_gate", ""), "正式成果发布", completeness.get("formal_release_gate", "")],
    ], [34*mm, 55*mm, 34*mm, 55*mm]))
    story.append(Paragraph(_e(completeness.get("formal_release_reason", "")), body))
    if completeness.get("issues"):
        issue_rows = [["优先级", "范围/对象", "校核项", "状态", "处理要求"]]
        for issue in completeness["issues"]:
            issue_rows.append([issue["priority"], f"{issue['scope']} / {issue['subject']}",
                               issue["check"], issue["status"], issue["action"]])
        story.append(_table(issue_rows, [18*mm, 52*mm, 32*mm, 22*mm, 65*mm], header=True))
    story.append(Paragraph("警告与边界", heading))
    warnings = summary.get("warnings", [])
    if warnings:
        for warning in warnings: story.append(Paragraph("- " + _e(warning), body))
    else: story.append(Paragraph("项目汇总未返回警告。", body))
    story.append(Paragraph("本文件汇总各回路最新的不可覆盖结果快照；详细部件核验、依据和过程请查看单回路计算书。", body))
    doc.build(story)
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
