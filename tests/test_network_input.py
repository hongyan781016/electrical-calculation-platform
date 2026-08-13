from math import sqrt

import pytest

from src.electrical_calc.complete_circuit import CircuitApplication, InputBasis, Phase, SegmentType
from src.electrical_calc.network_input import (
    CircuitNetworkInput,
    CircuitTaskMode,
    ExistingBreakerInput,
    FeederSegmentInput,
    TerminalLoadKind,
    build_circuit_network_requests,
)
from src.electrical_calc.drawing_audit import InstalledAssembly


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
        network(
            task_mode=CircuitTaskMode.AUDIT,
            segments=segments(audit=True),
            installed_assemblies=(InstalledAssembly("main", "AA1", 400, 400, 35),),
        ),
        {},
    )

    assert result.errors == ()
    assert result.audit_request is not None
    assert len(result.audit_request.installed_cables) == 3
    assert len(result.audit_request.installed_breakers) == 3
    assert len(result.audit_request.installed_assemblies) == 1


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
    assert any("BV目录" in item for item in result.errors)


def test_lighting_board_chain_uses_three_phase_feeder_and_single_phase_branch():
    items = list(segments())
    items[-1] = FeederSegmentInput(
        id="final",
        label="照明箱至WL1",
        length_m=30,
        conductor_family="BV",
        configuration_code="bv_1ph_2wire_pe",
        installation_scenario="conduit",
        temperature_c=30,
        phase=Phase.SINGLE,
        existing_pe_section_mm2=2.5,
        mcb_trip_curve="C",
    )
    result = build_circuit_network_requests(
        network(
            load_value=0.48,
            power_factor=0.8,
            terminal_phase=Phase.SINGLE,
            upstream_design_current_a=47.6,
            segments=tuple(items),
        ),
        {},
    )

    assert result.errors == ()
    assert result.derived["design_current_a"] == pytest.approx(0.48 * 1000 / (220 * 0.8))
    flows = {item.segment_id: item for item in result.radial_request.segment_load_flows}
    assert flows["connection"].design_current_a == pytest.approx(47.6)
    assert flows["feeder"].design_current_a == pytest.approx(47.6)
    assert flows["final"].design_current_a == pytest.approx(2.7272727)
    assert flows["final"].phase == Phase.SINGLE
    requests = {item.segment_id: item for item in result.radial_request.cable_requests}
    assert requests["final"].family == "BV"
    assert requests["final"].phase == Phase.SINGLE
    assert requests["final"].separate_protective_section_mm2 == 2.5
    points = {item.protected_segment_id: item for item in result.radial_request.protection_points}
    assert points["final"].allowed_families == ("MCB",)
    assert points["final"].pole_requirement == "1P"
    assert points["final"].mcb_trip_curve == "C"
    assert result.radial_request.maximum_candidates_per_cable_segment == 4


def test_internal_connection_boundary_does_not_create_fake_breaker_point():
    items = list(segments())
    items[0] = FeederSegmentInput(
        id="connection",
        label="柜内母排边界",
        length_m=0,
        segment_type=SegmentType.INTERNAL_CONNECTION,
        existing_breaker=None,
    )
    result = build_circuit_network_requests(network(segments=tuple(items)), {})

    assert result.errors == ()
    assert [item.protected_segment_id for item in result.radial_request.protection_points] == [
        "feeder",
        "final",
    ]


def test_exact_motor_catalog_row_derives_motor_parameters_and_application():
    result = build_circuit_network_requests(
        network(load_kind=TerminalLoadKind.MOTOR, load_value=30),
        {},
    )

    assert result.errors == ()
    assert result.derived["efficiency"] == 0.936
    assert result.derived["power_factor"] == 0.84
    assert result.derived["motor_calculation"]["outputs"]["rated_current_a"] == pytest.approx(
        result.derived["design_current_a"]
    )
    assert result.derived["motor_calculation"]["outputs"]["starting_current_a"] is not None
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


def test_kta_busway_replaces_first_cable_with_fixed_electrical_segment():
    items = list(segments(audit=True))
    items[0] = FeederSegmentInput(
        id="connection", label="变压器至低压柜", length_m=5,
        temperature_c=40, existing_breaker=items[0].existing_breaker,
        segment_type=SegmentType.BUSWAY,
        busway_series_code="canalis_kta_3lnpe", busway_rating_a=1600,
    )
    result = build_circuit_network_requests(
        network(task_mode=CircuitTaskMode.AUDIT, segments=tuple(items)), {}
    )
    assert result.errors == ()
    assert result.radial_request.circuit.segments[0].segment_type == SegmentType.BUSWAY
    assert len(result.radial_request.cable_requests) == 2
    fixed = result.radial_request.fixed_segment_electrical[0]
    assert fixed.segment_id == "connection"
    assert fixed.corrected_ampacity_a == 1552
    assert fixed.three_phase_r_ohm_per_km == 0.042
    assert len(result.audit_request.installed_cables) == 2
    assert result.audit_request.installed_busways[0].short_time_withstand_ka_1s == 65
