from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.protection_coordination import (
    ManufacturerCoordinationEvidence,
    ProtectionCoordinationInput,
    ProtectionDeviceIdentity,
    evaluate_protection_coordination,
    load_product_coordination_cases,
)


UPSTREAM = ProtectionDeviceIdentity(
    "UP-250-TMU", "Ir=200A; Isd=8Ir", "MCCB", 200
)
DOWNSTREAM = ProtectionDeviceIdentity(
    "DOWN-100-TMD", "In=63A; Im=10In", "MCCB", 63
)


def evidence(**changes):
    values = {
        "evidence_id": "table-a-1",
        "source": "厂家选择性与级联表",
        "reference": "表A-1，PDF第10页",
        "status": "verified",
        "upstream_product_code": UPSTREAM.product_code,
        "upstream_configuration_reference": (
            UPSTREAM.configuration_reference
        ),
        "downstream_product_code": DOWNSTREAM.product_code,
        "downstream_configuration_reference": (
            DOWNSTREAM.configuration_reference
        ),
        "selectivity_limit_ka": 15,
        "backup_protection_limit_ka": 25,
    }
    values.update(changes)
    return ManufacturerCoordinationEvidence(**values)


def test_coordination_never_guesses_without_exact_product_evidence():
    result = evaluate_protection_coordination(
        ProtectionCoordinationInput(UPSTREAM, DOWNSTREAM, 10)
    )
    assert result["provisional_status"] == UNKNOWN
    assert "厂家" in result["reason"]


def test_verified_table_produces_provisional_not_formal_result():
    result = evaluate_protection_coordination(
        ProtectionCoordinationInput(
            UPSTREAM,
            DOWNSTREAM,
            10,
            evidence(),
            backup_protection_required=True,
        )
    )
    assert result["provisional_status"] == PASS
    assert result["status"] == UNKNOWN
    assert result["selectivity"]["provisional_status"] == PASS
    assert result["backup_protection"]["provisional_status"] == PASS


def test_approved_table_can_formally_fail_selectivity():
    result = evaluate_protection_coordination(
        ProtectionCoordinationInput(
            UPSTREAM,
            DOWNSTREAM,
            20,
            evidence(status="approved"),
        )
    )
    assert result["provisional_status"] == FAIL
    assert result["status"] == FAIL


def test_evidence_must_match_trip_settings_not_only_product_codes():
    result = evaluate_protection_coordination(
        ProtectionCoordinationInput(
            UPSTREAM,
            ProtectionDeviceIdentity(
                "DOWN-100-TMD", "In=80A; Im=10In", "MCCB", 80
            ),
            10,
            evidence(),
        )
    )
    assert result["provisional_status"] == UNKNOWN
    assert "不完全匹配" in result["reason"]


def test_verified_schneider_case_is_loaded_with_exact_page_and_limit():
    case = load_product_coordination_cases()[0]
    assert case["evidence"].selectivity_limit_ka == 1
    assert "PDF第98页" in case["evidence"].reference
    below = evaluate_protection_coordination(
        ProtectionCoordinationInput(
            case["upstream"],
            case["downstream"],
            0.8,
            case["evidence"],
            system_voltage_v=400,
        )
    )
    above = evaluate_protection_coordination(
        ProtectionCoordinationInput(
            case["upstream"],
            case["downstream"],
            1.2,
            case["evidence"],
            system_voltage_v=400,
        )
    )
    assert below["provisional_status"] == PASS
    assert above["provisional_status"] == FAIL


def test_product_table_voltage_scope_is_enforced():
    case = load_product_coordination_cases()[0]
    result = evaluate_protection_coordination(
        ProtectionCoordinationInput(
            case["upstream"],
            case["downstream"],
            0.8,
            case["evidence"],
            system_voltage_v=690,
        )
    )
    assert result["provisional_status"] == UNKNOWN
    assert "电压" in result["reason"]
