from dataclasses import replace

from src.electrical_calc.drawing_audit import (
    DrawingCircuitAuditRequest,
    InstalledAssembly,
    InstalledBreaker,
    InstalledCable,
    audit_drawing_complete_circuit,
)
from src.electrical_calc.engine import FAIL, UNKNOWN
from tests.test_radial_circuit_service import approved_rules, request


def audit_request(first_breaker_icu_ka=35):
    radial = request()
    installed_cables = tuple(
        InstalledCable(
            segment_id=item.segment_id,
            designation={
                "connection": "图纸电缆 C1：YJV 4×70+PE35",
                "feeder": "图纸电缆 C2：YJV 4×35+PE16",
                "final": "图纸电缆 C3：YJV 4×25+PE16",
            }[item.segment_id],
            phase_section_mm2={
                "connection": 70,
                "feeder": 35,
                "final": 25,
            }[item.segment_id],
            selection_request=item,
        )
        for item in radial.cable_requests
    )
    installed_breakers = (
        InstalledBreaker(
            "tx",
            "connection",
            "图纸断路器 QF0 250A",
            250,
            400,
            400,
            first_breaker_icu_ka,
            product_reference="图纸标注；准确脱扣曲线待产品样本",
        ),
        InstalledBreaker(
            "main",
            "feeder",
            "图纸断路器 QF1 160A",
            160,
            250,
            400,
            35,
            product_reference="图纸标注；准确脱扣曲线待产品样本",
        ),
        InstalledBreaker(
            "db",
            "final",
            "图纸断路器 QF2 63A",
            63,
            100,
            400,
            25,
            product_reference="图纸标注；准确脱扣曲线待产品样本",
        ),
    )
    return DrawingCircuitAuditRequest(
        radial_request=replace(radial, maximum_candidates_per_cable_segment=3),
        installed_cables=installed_cables,
        installed_breakers=installed_breakers,
        installed_assemblies=(
            InstalledAssembly("main", "AA1馈线柜", 400, 400, 35, "图纸"),
            InstalledAssembly("db", "AP1配电箱", 160, 400, 25, "图纸"),
        ),
    )


def test_drawing_audit_keeps_original_components_and_does_not_substitute():
    result = audit_drawing_complete_circuit(audit_request(), approved_rules())
    assert result.outputs["audit_subject"] == "drawing_installed_components"
    assert result.outputs["replacement_design_included"] is False
    assert [item["designation"] for item in result.outputs["installed_cables"]] == [
        "图纸电缆 C1：YJV 4×70+PE35",
        "图纸电缆 C2：YJV 4×35+PE16",
        "图纸电缆 C3：YJV 4×25+PE16",
    ]
    breakers = result.outputs["installed_breakers"]
    assert breakers[0]["designation"] == "图纸断路器 QF0 250A"
    assert breakers[0]["checks"]["load_current"]["status"] == "通过"
    assert breakers[0]["checks"]["automatic_disconnection"]["status"] == UNKNOWN
    assert breakers[0]["checks"]["phase_thermal"]["status"] == UNKNOWN
    assert breakers[0]["checks"]["phase_thermal"][
        "maximum_permitted_clearing_time_s"
    ] > 0
    assert breakers[0]["checks"]["pe_thermal"][
        "maximum_permitted_let_through_energy_a2s"
    ] > 0
    assert len(result.outputs["protection_coordination"]) == 2
    assert result.outputs["transformer"]["checks"]["rated_capacity"]["status"] == UNKNOWN
    assert len(result.outputs["installed_assemblies"]) == 2
    assert len(result.outputs["component_matrix"]) == 10
    assert len(result.outputs["cross_component_checks"]) == 3
    assert result.outputs["cross_component_checks"][0]["check_code"] == "voltage_drop"
    assert result.outputs["installed_assemblies"][0]["checks"]["rated_current"]["status"] == "通过"
    assert all(
        item["status"] == UNKNOWN
        for item in result.outputs["protection_coordination"]
    )
    # 原图QF0/QF1的In大于相应图纸电缆修正后Iz，因此即使脱扣曲线
    # 尚未取得，已知条件已经足以判定原设计不通过。
    assert result.provisional_status == FAIL
    assert "replacement" not in str(result.outputs["installed_breakers"]).lower()


def test_drawing_audit_marks_assembly_failed_when_short_time_withstand_is_low():
    base = audit_request()
    result = audit_drawing_complete_circuit(
        replace(
            base,
            installed_assemblies=(
                InstalledAssembly("main", "AA1馈线柜", 400, 400, 1, "图纸"),
            ),
        ),
        approved_rules(),
    )
    assembly = result.outputs["installed_assemblies"][0]
    assert assembly["checks"]["short_time_withstand"]["status"] == FAIL
    assert result.provisional_status == FAIL


def test_drawing_audit_marks_original_breaker_failed_when_icu_is_too_low():
    result = audit_drawing_complete_circuit(
        audit_request(first_breaker_icu_ka=10),
        approved_rules(),
    )
    first = result.outputs["installed_breakers"][0]
    assert first["checks"]["breaking_capacity"]["status"] == FAIL
    assert first["status"] == FAIL
    assert result.provisional_status == FAIL
