from math import sqrt

import pytest

from src.electrical_calc.complete_circuit import CircuitApplication, InputBasis
from src.electrical_calc.network_input import (
    CircuitNetworkInput,
    CircuitTaskMode,
    ExistingBreakerInput,
    FeederSegmentInput,
    TerminalLoadKind,
    build_circuit_network_requests,
)


def segments(*, audit: bool = False, scenario: str = "tray"):
    result = []
    for segment_id, label, length, section, rated in (
        ("connection", "变压器低压出口至馈线柜", 10, 70, 250),
        ("feeder", "馈线柜至配电箱", 50, 35, 160),
        ("final", "配电箱至设备", 30, 25, 63),
    ):
        result.append(
            FeederSegmentInput(
                id=segment_id,
                label=label,
                length_m=length,
                installation_scenario=scenario,
                existing_phase_section_mm2=section if audit else None,
                existing_breaker=(
                    ExistingBreakerInput(
                        f"QF-{segment_id}", rated, rated, 400, 35
                    )
                    if audit
                    else None
                ),
            )
        )
    return tuple(result)


def network(**overrides):
    values = {
        "task_mode": CircuitTaskMode.DESIGN,
        "circuit_code": "C-001",
        "circuit_name": "匿名完整回路",
        "transformer_family": "scb11",
        "transformer_capacity_kva": 1000,
        "transformer_uk_percent": 6,
        "upstream_short_circuit_capacity_mva": 100,
        "load_kind": TerminalLoadKind.ORDINARY,
        "load_basis": InputBasis.ACTIVE_POWER_KW,
        "load_value": 30,
        "power_factor": 0.9,
        "segments": segments(),
    }
    values.update(overrides)
    return CircuitNetworkInput(**values)


def test_ordinary_load_builds_three_segment_radial_request_without_manual_rx():
    result = build_circuit_network_requests(network(), {})

    assert result.errors == ()
    assert result.audit_request is None
    assert result.radial_request is not None
    assert len(result.radial_request.circuit.nodes) == 4
    assert len(result.radial_request.circuit.segments) == 3
    expected = 30_000 / (sqrt(3) * 380 * 0.9)
    assert result.derived["design_current_a"] == pytest.approx(expected)
    assert result.radial_request.upstream_short_circuit_capacity_mva == 100
    assert all(
        flow.design_current_a == pytest.approx(expected)
        for flow in result.radial_request.segment_load_flows
    )


def test_audit_mode_builds_installed_cables_and_breakers_for_all_segments():
    result = build_circuit_network_requests(
        network(task_mode=CircuitTaskMode.AUDIT, segments=segments(audit=True)),
        {},
    )

    assert result.errors == ()
    assert result.audit_request is not None
    assert len(result.audit_request.installed_cables) == 3
    assert len(result.audit_request.installed_breakers) == 3


def test_invalid_inputs_return_all_errors_without_partial_topology():
    result = build_circuit_network_requests(
        network(
            circuit_code="",
            transformer_capacity_kva=0,
            power_factor=1.2,
            segments=(FeederSegmentInput("connection", "首段", 0, conductor_family="BV"),),
        ),
        {},
    )

    assert result.radial_request is None
    assert result.audit_request is None
    assert len(result.errors) >= 5
    assert any("回路编号" in item for item in result.errors)
    assert any("三段连续线路" in item for item in result.errors)
    assert any("YJV" in item for item in result.errors)


def test_exact_motor_catalog_row_derives_motor_parameters_and_application():
    result = build_circuit_network_requests(
        network(load_kind=TerminalLoadKind.MOTOR, load_value=30),
        {},
    )

    assert result.errors == ()
    assert result.derived["efficiency"] == 0.936
    assert result.derived["power_factor"] == 0.84
    assert result.radial_request.circuit.load.circuit_application == CircuitApplication.MOTOR_FINAL


def test_unlisted_motor_power_is_not_interpolated():
    result = build_circuit_network_requests(
        network(load_kind=TerminalLoadKind.MOTOR, load_value=20),
        {},
    )

    assert result.radial_request is None
    assert any("不插值" in item for item in result.errors)


def test_direct_buried_request_uses_existing_catalog_spacing_key():
    result = build_circuit_network_requests(
        network(segments=segments(scenario="direct_buried")),
        {},
    )

    assert result.errors == ()
    conditions = result.radial_request.cable_requests[0].conditions
    assert conditions.buried_duct_spacing_m == "0.25"
    assert conditions.buried_depth_m == 0.7
