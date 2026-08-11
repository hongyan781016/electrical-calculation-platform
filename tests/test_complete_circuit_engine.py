from math import sqrt

import pytest

from src.electrical_calc.complete_circuit import (
    CircuitApplication,
    CircuitNode,
    CircuitSegment,
    CompleteCircuit,
    DutyCharacteristic,
    EarthingSystem,
    InputBasis,
    Load,
    LoadProfile,
    NodeType,
    Phase,
    PowerDefinition,
    PowerSource,
    SegmentType,
    UpstreamNetworkMode,
)
from src.electrical_calc.complete_circuit_engine import (
    CompleteCircuitCalculationInput,
    ResolvedSegmentElectrical,
    ResolvedSegmentLoadFlow,
    ResolvedSourceElectrical,
    calculate_complete_circuit_chain,
)
from src.electrical_calc.engine import PASS, UNKNOWN


def approved_rules():
    return {
        code: {"status": "approved"}
        for code in (
            "ELEC.LOAD.CURRENT",
            "ELEC.VDROP",
            "ELEC.SHORT_CIRCUIT",
            "ELEC.EARTH_FAULT.TN.IMPEDANCE",
        )
    }


def approved_motor_rules():
    rules = approved_rules()
    rules["MOTOR.CURRENT.RATED"] = {"status": "approved"}
    return rules


def circuit(phase=Phase.THREE):
    return CompleteCircuit(
        id="c-1",
        code="WP-01",
        name="完整回路算例",
        system_voltage_v=380,
        line_to_earth_voltage_v=220,
        frequency_hz=50,
        earthing_system=EarthingSystem.TN_S,
        source=PowerSource(
            transformer_family="S11-M",
            rated_capacity_kva=400,
            hv_voltage_kv=10,
            lv_voltage_kv=0.4,
            vector_group="Dyn11",
            uk_percent=4,
            upstream_network_mode=UpstreamNetworkMode.INFINITE_CAPACITY,
        ),
        load=Load(
            input_basis=InputBasis.ACTIVE_POWER_KW,
            input_value=30,
            phase=phase,
            circuit_application=CircuitApplication.LIGHTING_FINAL,
            load_profile=LoadProfile.LIGHTING,
            duty_characteristic=DutyCharacteristic.ORDINARY_CONTINUOUS,
            power_definition=PowerDefinition.CALCULATED,
            power_factor=0.9,
        ),
        nodes=(
            CircuitNode(
                "tx",
                0,
                NodeType.TRANSFORMER_LV,
                "变压器低压端",
                380,
            ),
            CircuitNode(
                "main",
                1,
                NodeType.MAIN_SWITCHBOARD,
                "低压总柜",
                380,
            ),
            CircuitNode(
                "load",
                2,
                NodeType.LOAD_TERMINAL,
                "负荷端",
                220 if phase == Phase.SINGLE else 380,
            ),
        ),
        segments=(
            CircuitSegment(
                "bus",
                0,
                "tx",
                "main",
                SegmentType.BUSWAY,
                Phase.THREE,
                5,
                "indoor",
            ),
            CircuitSegment(
                "line",
                1,
                "main",
                "load",
                SegmentType.CABLE,
                phase,
                50,
                "cable_tray",
                conductor_family="YJV",
            ),
        ),
        rule_set_version="approved-test",
    )


def source(status="approved", phase_pe=True):
    return ResolvedSourceElectrical(
        three_phase_r_ohm=0.005,
        three_phase_x_ohm=0.02,
        phase_neutral_r_ohm=0.01,
        phase_neutral_x_ohm=0.02,
        phase_pe_r_ohm=0.01 if phase_pe else None,
        phase_pe_x_ohm=0.02 if phase_pe else None,
        status=status,
        source_reference_ids=("source-rule",),
    )


def segment(
    segment_id,
    voltage_r,
    voltage_x,
    three_r,
    three_x,
    ln_r,
    ln_x,
    pe_r,
    pe_x,
    status="approved",
):
    return ResolvedSegmentElectrical(
        segment_id=segment_id,
        phase_neutral_applicable=True,
        voltage_drop_r_ohm_per_km=voltage_r,
        voltage_drop_x_ohm_per_km=voltage_x,
        three_phase_r_ohm_per_km=three_r,
        three_phase_x_ohm_per_km=three_x,
        phase_neutral_r_ohm_per_km=ln_r,
        phase_neutral_x_ohm_per_km=ln_x,
        phase_pe_r_ohm_per_km=pe_r,
        phase_pe_x_ohm_per_km=pe_x,
        corrected_ampacity_a=160,
        status=status,
        source_reference_ids=(f"{segment_id}-rule",),
    )


def resolved_segments(phase_pe=True):
    return (
        segment("bus", 0.02, 0.01, 0.02, 0.01, 0.03, 0.02, 0.03, 0.02),
        segment(
            "line",
            0.4,
            0.08,
            0.4,
            0.08,
            0.8,
            0.1,
            0.8 if phase_pe else None,
            0.1 if phase_pe else None,
        ),
    )


def resolved_load_flows(phase=Phase.THREE):
    terminal_current = (
        30_000 / (220 * 0.9)
        if phase == Phase.SINGLE
        else 30_000 / (sqrt(3) * 380 * 0.9)
    )
    return (
        ResolvedSegmentLoadFlow(
            segment_id="bus",
            design_current_a=125,
            power_factor=0.85,
            phase=Phase.THREE,
            status="approved",
            source_reference_ids=("main-load-summary",),
        ),
        ResolvedSegmentLoadFlow(
            segment_id="line",
            design_current_a=terminal_current,
            power_factor=0.9,
            phase=phase,
            status="approved",
            source_reference_ids=("terminal-load",),
        ),
    )


def calculation_input(phase=Phase.THREE, phase_pe=True):
    return CompleteCircuitCalculationInput(
        circuit=circuit(phase),
        source_electrical=source(phase_pe=phase_pe),
        segment_electrical=resolved_segments(phase_pe),
        segment_load_flows=resolved_load_flows(phase),
        maximum_short_circuit_voltage_factor=1.05,
        minimum_fault_voltage_factor=0.8,
    )


def test_complete_chain_calculates_current_cumulative_drop_and_node_faults():
    result = calculate_complete_circuit_chain(calculation_input(), approved_rules())

    expected_current = 30_000 / (sqrt(3) * 380 * 0.9)
    assert result.outputs["design_current_a"] == pytest.approx(expected_current, abs=1e-6)
    assert result.outputs["terminal_voltage_drop_v"] > 0
    nodes = result.outputs["node_results"]
    assert len(nodes) == 3
    assert (
        nodes[0]["three_phase_short_circuit_ka"]
        > nodes[1]["three_phase_short_circuit_ka"]
        > nodes[2]["three_phase_short_circuit_ka"]
    )
    assert (
        nodes[0]["earth_fault_current_a"]
        > nodes[1]["earth_fault_current_a"]
        > nodes[2]["earth_fault_current_a"]
    )
    assert result.provisional_status == PASS
    assert result.status == PASS


def test_motor_running_chain_uses_efficiency_but_not_starting_current():
    base = calculation_input()
    motor_load = Load(
        input_basis=InputBasis.ACTIVE_POWER_KW,
        input_value=30,
        phase=Phase.THREE,
        circuit_application=CircuitApplication.MOTOR_FINAL,
        load_profile=LoadProfile.MOTOR,
        duty_characteristic=DutyCharacteristic.HIGH_INRUSH,
        power_definition=PowerDefinition.CALCULATED,
        power_factor=0.86,
        efficiency=0.91,
    )
    expected = 30_000 / (sqrt(3) * 380 * 0.91 * 0.86)
    motor_circuit = CompleteCircuit(
        **{
            **base.circuit.__dict__,
            "load": motor_load,
        }
    )
    terminal_flow = ResolvedSegmentLoadFlow(
        **{
            **base.segment_load_flows[-1].__dict__,
            "design_current_a": expected,
            "power_factor": 0.86,
        }
    )
    data = CompleteCircuitCalculationInput(
        **{
            **base.__dict__,
            "circuit": motor_circuit,
            "segment_load_flows": (
                base.segment_load_flows[0],
                terminal_flow,
            ),
        }
    )

    result = calculate_complete_circuit_chain(data, approved_motor_rules())

    assert result.status == PASS
    assert result.outputs["design_current_a"] == pytest.approx(expected, abs=1e-6)
    assert "MOTOR.CURRENT.RATED" in result.rule_codes
    assert result.outputs["design_current_a"] < expected * 6.5


def test_unapproved_resolved_parameter_keeps_complete_chain_provisional():
    data = calculation_input()
    data = CompleteCircuitCalculationInput(
        **{
            **data.__dict__,
            "source_electrical": source(status="verified"),
        }
    )
    result = calculate_complete_circuit_chain(data, approved_rules())
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert any("仅用于暂算" in warning for warning in result.warnings)


def test_missing_phase_pe_parameters_preserves_other_results_but_is_unknown():
    result = calculate_complete_circuit_chain(
        calculation_input(phase_pe=False),
        approved_rules(),
    )
    assert result.outputs["terminal_voltage_drop_v"] > 0
    assert result.outputs["terminal_three_phase_short_circuit_ka"] > 0
    assert result.outputs["terminal_earth_fault_current_a"] is None
    assert result.provisional_status == UNKNOWN
    assert any("相—PE" in warning for warning in result.warnings)


def test_single_phase_final_segment_stops_three_phase_fault_but_keeps_ln_and_pe():
    result = calculate_complete_circuit_chain(
        calculation_input(phase=Phase.SINGLE),
        approved_rules(),
    )
    nodes = result.outputs["node_results"]
    assert nodes[1]["three_phase_short_circuit_ka"] is not None
    assert nodes[2]["three_phase_short_circuit_ka"] is None
    assert nodes[2]["phase_neutral_short_circuit_a"] > 0
    assert nodes[2]["earth_fault_current_a"] > 0
    assert result.provisional_status == PASS


def test_missing_segment_parameter_object_blocks_chain_without_guessing():
    data = calculation_input()
    data = CompleteCircuitCalculationInput(
        **{
            **data.__dict__,
            "segment_electrical": data.segment_electrical[:1],
        }
    )
    result = calculate_complete_circuit_chain(data, approved_rules())
    assert result.status == UNKNOWN
    assert result.outputs == {}
    assert any("缺少线路段电气参数" in warning for warning in result.warnings)


def test_each_segment_uses_its_own_downstream_load_flow_for_voltage_drop():
    result = calculate_complete_circuit_chain(calculation_input(), approved_rules())
    segments = result.outputs["segment_results"]
    assert segments[0]["load_flow_design_current_a"] == 125
    assert segments[1]["load_flow_design_current_a"] == pytest.approx(
        result.outputs["design_current_a"],
        abs=1e-6,
    )
    expected_bus_drop = (
        sqrt(3)
        * 125
        * (0.02 * 0.005 * 0.85 + 0.01 * 0.005 * sqrt(1 - 0.85**2))
    )
    assert segments[0]["voltage_drop_v"] == pytest.approx(expected_bus_drop, abs=1e-6)


def test_terminal_load_flow_must_close_with_terminal_load_calculation():
    data = calculation_input()
    wrong_terminal_flow = ResolvedSegmentLoadFlow(
        **{
            **data.segment_load_flows[-1].__dict__,
            "design_current_a": 999,
        }
    )
    data = CompleteCircuitCalculationInput(
        **{
            **data.__dict__,
            "segment_load_flows": (
                data.segment_load_flows[0],
                wrong_terminal_flow,
            ),
        }
    )
    result = calculate_complete_circuit_chain(data, approved_rules())
    assert result.provisional_status == UNKNOWN
    assert "segment_results" not in result.outputs
    assert any("数据链未闭合" in warning for warning in result.warnings)


def test_phase_neutral_fault_is_not_required_when_n_is_not_distributed():
    data = calculation_input()
    no_neutral_line = ResolvedSegmentElectrical(
        **{
            **data.segment_electrical[-1].__dict__,
            "phase_neutral_applicable": False,
            "phase_neutral_r_ohm_per_km": None,
            "phase_neutral_x_ohm_per_km": None,
        }
    )
    data = CompleteCircuitCalculationInput(
        **{
            **data.__dict__,
            "segment_electrical": (
                data.segment_electrical[0],
                no_neutral_line,
            ),
        }
    )
    result = calculate_complete_circuit_chain(data, approved_rules())
    assert result.outputs["terminal_phase_neutral_short_circuit_a"] is None
    assert result.outputs["terminal_earth_fault_current_a"] > 0
    assert result.provisional_status == PASS
