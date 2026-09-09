from src.electrical_calc.drawing_import import (
    ConfirmationDecision,
    circuit_row_form_candidates,
    DrawingImportError,
    EvidenceState,
    confirm_candidate,
    confirmed_to_complete_circuit_form,
    extract_drawing_bytes,
    extract_circuit_rows,
    extract_feeder_rows,
    extract_transformer_candidates,
    group_panel_regions,
    interpret_electrical,
    parser_capabilities,
    group_drawing_regions,
)
from reportlab.pdfgen import canvas
from io import BytesIO


def dxf_with_text(*texts: str) -> bytes:
    lines = ["0", "SECTION", "2", "ENTITIES"]
    for index, value in enumerate(texts, start=1):
        lines.extend(
            [
                "0",
                "TEXT",
                "5",
                f"{index:X}",
                "8",
                "E-EQUIP",
                "10",
                str(index * 10),
                "20",
                str(index * 20),
                "1",
                value,
            ]
        )
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_parser_capabilities_are_truthful_about_builtin_and_optional_backends():
    capabilities = parser_capabilities()
    assert capabilities["dxf"]["available"] is True
    assert "backend" in capabilities["pdf"]
    assert "backend" in capabilities["dwg"]


def test_ascii_dxf_extraction_preserves_locator_layer_handle_and_hash():
    content = dxf_with_text("回路 C-001", "30kW")
    bundle = extract_drawing_bytes("sample.dxf", content)
    assert bundle.source.filename == "sample.dxf"
    assert bundle.source.sha256
    assert bundle.evidence[0].raw_text == "回路 C-001"
    assert bundle.evidence[0].layer == "E-EQUIP"
    assert bundle.evidence[0].handle == "1"
    assert bundle.evidence[0].x == 10
    assert bundle.evidence[0].y == 20


def test_interpreter_builds_facts_topology_and_segment_values_without_approving():
    bundle = extract_drawing_bytes(
        "sample.dxf",
        dxf_with_text(
            "变压器 T1 SCB11-1000kVA uk=6%",
            "短路容量=100MVA",
            "回路 C-001",
            "馈线柜 AA1",
            "馈线段 AA1→AP1 YJV 4×35+1×16 长度=50m",
            "末端负荷 30kW cosφ=0.9",
        ),
    )
    candidate = interpret_electrical(bundle)
    values = {(fact.field_name, fact.value) for fact in candidate.facts}
    assert ("transformer_actual_model", "SCB11") in values
    assert ("transformer_capacity_kva", "1000") in values
    assert ("transformer_uk_percent", "6") in values
    assert ("upstream_short_circuit_capacity_mva", "100") in values
    assert ("existing_section_feeder", "35") in values
    assert ("length_feeder", "50") in values
    assert candidate.topology[0].from_code == "AA1"
    assert candidate.topology[0].to_code == "AP1"
    assert all(fact.state == EvidenceState.PENDING_CONFIRMATION for fact in candidate.facts)


def test_conflicts_remain_unconfirmed_until_user_selects_one_value():
    candidate = interpret_electrical(
        extract_drawing_bytes("conflict.dxf", dxf_with_text("30kW", "45kW", "回路 C-2"))
    )
    load_facts = [fact for fact in candidate.facts if fact.field_name == "load_value"]
    assert len(load_facts) == 2
    assert all(fact.state == EvidenceState.CONFLICT for fact in load_facts)
    assert candidate.conflicts

    confirmed = confirm_candidate(
        candidate,
        [ConfirmationDecision("load_value", True, "45")],
    )
    assert confirmed.values == {"load_value": "45"}
    assert confirmed.evidence_by_field["load_value"]


def test_only_explicitly_confirmed_values_enter_existing_complete_circuit_form():
    candidate = interpret_electrical(
        extract_drawing_bytes("sample.dxf", dxf_with_text("回路 C-9", "30kW"))
    )
    confirmed = confirm_candidate(
        candidate,
        [
            ConfirmationDecision("circuit_code", True, "C-9"),
            ConfirmationDecision("load_value", False, "30"),
        ],
    )
    form = confirmed_to_complete_circuit_form(
        confirmed,
        {"task_mode": "design", "circuit_code": "DEFAULT", "load_value": "1"},
    )
    assert form["task_mode"] == "audit"
    assert form["circuit_code"] == "C-9"
    assert form["load_value"] == "1"


def test_binary_dwg_is_converted_by_autocad_before_dxf_extraction(monkeypatch):
    converted = dxf_with_text("回路 C-DWG-1")
    monkeypatch.setattr(
        "src.electrical_calc.drawing_import._convert_dwg_to_ascii_dxf",
        lambda content: converted,
    )
    bundle = extract_drawing_bytes("sample.dwg", b"AC1032\x00binary")
    assert bundle.source.parser_name == "autocad-core-console+dxf"
    assert bundle.evidence[0].raw_text == "回路 C-DWG-1"
    assert "原图未保存" in bundle.warnings[0]


def test_pdf_text_layer_is_extracted_with_page_locator():
    stream = BytesIO()
    pdf = canvas.Canvas(stream)
    pdf.drawString(72, 720, "Terminal load 30kW")
    pdf.save()
    bundle = extract_drawing_bytes("sample.pdf", stream.getvalue())
    assert bundle.source.parser_name == "pypdf"
    assert bundle.evidence[0].locator.startswith("PDF第1页")
    assert "30kW" in bundle.evidence[0].raw_text


def test_title_block_drawing_numbers_partition_evidence_into_regions():
    content = ("\n".join([
        "0", "SECTION", "2", "ENTITIES",
        "0", "ATTRIB", "5", "1", "8", "LABEL", "2", "图号", "10", "100", "20", "0", "1", "E-01",
        "0", "ATTRIB", "5", "2", "8", "LABEL", "2", "图名", "10", "100", "20", "10", "1", "配电箱系统图一",
        "0", "TEXT", "5", "3", "8", "E-TEXT", "10", "90", "20", "100", "1", "回路 C-1",
        "0", "ATTRIB", "5", "4", "8", "LABEL", "2", "图号", "10", "1100", "20", "0", "1", "E-02",
        "0", "ATTRIB", "5", "5", "8", "LABEL", "2", "图名", "10", "1100", "20", "10", "1", "一层照明平面图",
        "0", "TEXT", "5", "6", "8", "E-TEXT", "10", "1090", "20", "100", "1", "回路 C-2",
        "0", "ENDSEC", "0", "EOF", "",
    ])).encode("utf-8")
    bundle = extract_drawing_bytes("regions.dxf", content)
    regions = group_drawing_regions(bundle)
    assert [(item.drawing_number, item.region_type) for item in regions] == [
        ("E-01", "distribution_board_system"),
        ("E-02", "plan"),
    ]
    first_members = set(regions[0].evidence_ids)
    assert "E00003" in first_members
    candidate = interpret_electrical(bundle)
    circuit_regions = {
        fact.value: fact.region_id
        for fact in candidate.facts
        if fact.field_name == "circuit_code"
    }
    assert circuit_regions == {"C-1": "E-01", "C-2": "E-02"}


def test_distribution_panel_and_horizontal_circuit_row_are_grouped_with_evidence():
    content = ("\n".join([
        "0", "SECTION", "2", "ENTITIES",
        "0", "ATTRIB", "5", "1", "8", "LABEL", "2", "图号", "10", "100", "20", "0", "1", "E-23",
        "0", "ATTRIB", "5", "2", "8", "LABEL", "2", "图名", "10", "100", "20", "10", "1", "配电箱系统图一",
        "0", "TEXT", "5", "3", "8", "E-TEXT", "10", "1000", "20", "5000", "1", "5AL1-1",
        "0", "TEXT", "5", "4", "8", "E-TEXT", "10", "800", "20", "4500", "1", "Pe=",
        "0", "TEXT", "5", "5", "8", "E-TEXT", "10", "1200", "20", "4300", "1", "Ijs=",
        "0", "TEXT", "5", "51", "8", "E-TEXT", "10", "1400", "20", "4500", "1", "15.0kW",
        "0", "TEXT", "5", "52", "8", "E-TEXT", "10", "1600", "20", "4300", "1", "22.8A",
        "0", "TEXT", "5", "53", "8", "E-TEXT", "10", "900", "20", "2500", "1", "CM3-125L/3P/63A",
        "0", "TEXT", "5", "6", "8", "E-TEXT", "10", "3000", "20", "3500", "1", "CH2-63C/10/1",
        "0", "TEXT", "5", "7", "8", "E-TEXT", "10", "5000", "20", "3500", "1", "WL1",
        "0", "TEXT", "5", "8", "8", "E-TEXT", "10", "6000", "20", "3500", "1", "0.48kW",
        "0", "TEXT", "5", "9", "8", "E-TEXT", "10", "6500", "20", "3500", "1", "车间照明",
        "0", "TEXT", "5", "A", "8", "E-TEXT", "10", "7500", "20", "3500", "1", "ZC-BVV-2*2.5+PE-2.5",
        "0", "ENDSEC", "0", "EOF", "",
    ])).encode("utf-8")
    bundle = extract_drawing_bytes("panel.dxf", content)
    panels = group_panel_regions(bundle)
    assert [(panel.panel_code, panel.drawing_region_id) for panel in panels] == [("5AL1-1", "E-23")]
    assert panels[0].installed_power_kw == "15.0"
    assert panels[0].design_current_a == "22.8"
    assert panels[0].incoming_breaker_spec == "CM3-125L/3P/63A"
    assert panels[0].incoming_breaker_current_a == "63"
    assert panels[0].incoming_breaker_frame_a == "125"
    rows = extract_circuit_rows(bundle, panels)
    assert len(rows) == 1
    assert rows[0].circuit_code == "WL1"
    assert rows[0].breaker_spec == "CH2-63C/10/1"
    assert rows[0].load_kw == "0.48"
    assert rows[0].destination == "车间照明"
    assert rows[0].cable_spec == "ZC-BVV-2*2.5+PE-2.5"
    assert rows[0].evidence_ids
    assert rows[0].breaker_evidence_ids == ("E00009",)
    assert rows[0].cable_evidence_ids == ("E00013",)
    form_candidates = {
        item["field_name"]: item["value"] for item in circuit_row_form_candidates(rows[0].__dict__)
    }
    assert form_candidates["circuit_code"] == "WL1"
    assert form_candidates["circuit_name"] == "车间照明"
    assert form_candidates["load_value"] == "0.48"
    assert form_candidates["existing_section_final"] == "2.5"
    assert form_candidates["existing_pe_section_final"] == "2.5"
    assert form_candidates["breaker_in_final"] == "10"
    assert form_candidates["mcb_trip_curve_final"] == "C"
    assert form_candidates["terminal_phase"] == "1"
    panel_fields = {
        item["field_name"]: item["value"]
        for item in circuit_row_form_candidates(rows[0].__dict__, panel=panels[0].__dict__)
    }
    assert panel_fields["assembly_designation_db"] == "5AL1-1"

    motor_fields = {
        item["field_name"]: item["value"]
        for item in circuit_row_form_candidates(
            {
                "circuit_code": "M1",
                "destination": "4极直接启动电动机",
                "load_kw": "30",
                "destination_evidence_ids": ("E00021",),
            }
        )
    }
    assert motor_fields["load_kind"] == "motor"
    assert motor_fields["terminal_phase"] == "3"

    ambiguous_motor_fields = {
        item["field_name"]: item["value"]
        for item in circuit_row_form_candidates(
            {
                "circuit_code": "M2",
                "destination": "排风机",
                "load_kw": "5.5",
                "destination_evidence_ids": ("E00022",),
            }
        )
    }
    assert "load_kind" not in ambiguous_motor_fields


def test_low_voltage_feeder_column_links_exact_destination_panel():
    content = ("\n".join([
        "0", "SECTION", "2", "ENTITIES",
        "0", "TEXT", "5", "1", "8", "E", "10", "1000", "20", "5000", "1", "D7-1:监管仓库",
        "0", "TEXT", "5", "2", "8", "E", "10", "500", "20", "4600", "1", "CM3-100H/3P",
        "0", "TEXT", "5", "3", "8", "E", "10", "500", "20", "4400", "1", "63A",
        "0", "TEXT", "5", "4", "8", "E", "10", "1500", "20", "4500", "1", "P=31.3kW Ijs=47.6A",
        "0", "TEXT", "5", "5", "8", "E", "10", "1800", "20", "4000", "1", "YJV-1-4*25+1*16",
        "0", "TEXT", "5", "6", "8", "E", "10", "2200", "20", "4600", "1", "5AT2-1",
        "0", "ENDSEC", "0", "EOF", "",
    ])).encode("utf-8")
    bundle = extract_drawing_bytes("feeder.dxf", content)
    feeders = extract_feeder_rows(bundle)
    assert len(feeders) == 1
    feeder = feeders[0]
    assert feeder.cabinet_code == "D7"
    assert feeder.feeder_code == "D7-1"
    assert feeder.destination_panel_code == "5AT2-1"
    assert feeder.breaker_spec == "CM3-100H/3P"
    assert feeder.breaker_current_a == "63"
    assert feeder.design_current_a == "47.6"
    assert feeder.cable_spec == "YJV-1-4*25+1*16"
    fields = {
        item["field_name"]: item["value"]
        for item in circuit_row_form_candidates(
            {"circuit_code": "WL1"},
            feeder.__dict__,
        )
    }
    assert fields["feeder_cabinet_code"] == "D7"
    assert fields["upstream_design_current_a"] == "47.6"
    assert fields["existing_section_feeder"] == "25"
    assert fields["existing_pe_section_feeder"] == "16"


def test_transformer_candidate_keeps_actual_scb14_separate_from_reference_family():
    content = dxf_with_text(
        "D1",
        "TMY－4(100x10)",
        "CW2-2500/4P",
        "In=2000A",
        "SCB14-1000kVA",
        "10/0.4/0.23kV Dyn11",
        "Uk%=6%",
        "B1",
    )
    bundle = extract_drawing_bytes("transformer.dxf", content)
    transformers = extract_transformer_candidates(bundle)
    assert len(transformers) == 1
    transformer = transformers[0]
    assert transformer.transformer_code == "B1"
    assert transformer.actual_model == "SCB14"
    assert transformer.capacity_kva == "1000"
    assert transformer.uk_percent == "6"
    assert transformer.incoming_cabinet_code == "D1"
    assert transformer.busbar_spec == "TMY－4(100X10)"
    assert transformer.main_breaker_spec == "CW2-2500/4P"
    assert transformer.main_breaker_current_a == "2000"
    candidate = interpret_electrical(bundle)
    assert not [fact for fact in candidate.facts if fact.field_name == "transformer_family"]
