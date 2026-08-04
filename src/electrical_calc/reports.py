from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


FONT = "SimHei"
pdfmetrics.registerFont(TTFont(FONT, r"C:\Windows\Fonts\simhei.ttf"))


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
            story.append(Paragraph("• " + _e(warning), body))

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


def _e(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
