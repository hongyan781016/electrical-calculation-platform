from math import sqrt

from src.electrical_calc.cable_selector import (
    CableInstallationConditions,
    CableSelectionRequest,
)
from src.electrical_calc.combination_solver import ProtectionPoint
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
from src.electrical_calc.complete_circuit_engine import ResolvedSegmentLoadFlow
from src.electrical_calc.radial_circuit_service import (
    RadialCircuitCalculationRequest,
    calculate_radial_complete_circuit,
    resolve_radial_source_electrical,
)


def approved_rules():
    codes = (
        "ELEC.LOAD.CURRENT",
        "ELEC.VDROP",
        "ELEC.VDROP.IMPEDANCE",
        "ELEC.VDROP.LIMIT",
        "ELEC.SHORT_CIRCUIT",
        "ELEC.EARTH_FAULT.TN.IMPEDANCE",
        "ELEC.CABLE.YJV.MULTICORE.AMPACITY",
        "ELEC.CABLE.TEMPERATURE.DERATING",
        "ELEC.CABLE.TRAY.GROUPING",
        "ELEC.CABLE.YJV.FOUR_CORE.PHASE_PE.IMPEDANCE",
        "ELEC.BREAKER.RATING",
        "ELEC.CABLE.COORDINATION",
        "ELEC.BREAKING.CAPACITY",
        "ELEC.BREAKER.MCB.INSTANTANEOUS",
        "ELEC.PE.THERMAL.WITHSTAND",
        "ELEC.PEN.NO_SWITCHING",
        "ELEC.BREAKER.ICS.ICW.REFERENCE",
        "ELEC.PHASE.THERMAL.WITHSTAND",
        "ELEC.TRANSFORMER.POSITIVE_SEQUENCE.IMPEDANCE",
        "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE",
    )
    return {code: {"status": "approved"} for code in codes}


def request():
    final_current = 30_000 / (sqrt(3) * 380 * 0.9)
    nodes = (
        CircuitNode("tx", 0, NodeType.TRANSFORMER_LV, "变压器低压出口", 380),
        CircuitNode("main", 1, NodeType.MAIN_SWITCHBOARD, "低压柜", 380),
        CircuitNode("db", 2, NodeType.DISTRIBUTION_BOARD, "分配电箱", 380),
        CircuitNode("load", 3, NodeType.LOAD_TERMINAL, "用电设备", 380),
    )
    segment_specs = (
        ("connection", "tx", "main", 10.0, 200.0),
        ("feeder", "main", "db", 50.0, 125.0),
        ("final", "db", "load", 30.0, final_current),
    )
    segments = tuple(
        CircuitSegment(
            segment_id,
            index,
            from_node,
            to_node,
            SegmentType.CABLE,
            Phase.THREE,
            length,
            "tray",
            conductor_family="YJV",
            construction_code="yjv_4c_3ph_n_pe",
        )
        for index, (segment_id, from_node, to_node, length, _) in enumerate(
            segment_specs
        )
    )
    circuit = CompleteCircuit(
        id="validation-radial-01",
        code="T1-MDB-DB-L1",
        name="匿名完整低压放射式回路",
        system_voltage_v=380,
        line_to_earth_voltage_v=220,
        frequency_hz=50,
        earthing_system=EarthingSystem.TN_S,
        source=PowerSource(
            transformer_family="scb11",
            rated_capacity_kva=1000,
            hv_voltage_kv=10,
            lv_voltage_kv=0.4,
            vector_group="Dyn11",
            uk_percent=6,
            upstream_network_mode=UpstreamNetworkMode.EXPLICIT_IMPEDANCE,
        ),
        load=Load(
            input_basis=InputBasis.ACTIVE_POWER_KW,
            input_value=30,
            phase=Phase.THREE,
            circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
            load_profile=LoadProfile.ORDINARY_EQUIPMENT,
            duty_characteristic=DutyCharacteristic.ORDINARY_CONTINUOUS,
            power_definition=PowerDefinition.CALCULATED,
            power_factor=0.9,
        ),
        nodes=nodes,
        segments=segments,
        rule_set_version="validation-0.1.0",
    )
    flows = tuple(
        ResolvedSegmentLoadFlow(
            segment_id,
            current,
            0.85 if segment_id != "final" else 0.9,
            Phase.THREE,
            "user_confirmed" if segment_id != "final" else "derived",
            (f"FIXTURE:LOAD_FLOW:{segment_id}",),
        )
        for segment_id, _, _, _, current in segment_specs
    )

    def cable_request(segment_id, current):
        return CableSelectionRequest(
            segment_id=segment_id,
            family="YJV",
            configuration_code="yjv_4c_3ph_n_pe",
            phase=Phase.THREE,
            system_voltage_v=380,
            installation_scenario="tray",
            minimum_required_ampacity_a=current,
            neutral_required=False,
            protective_conductor_mode="included",
            conditions=CableInstallationConditions(
                temperature_c=40,
                tray_type="horizontal_perforated",
                tray_layers=1,
                tray_cables_per_layer=1,
            ),
        )

    return RadialCircuitCalculationRequest(
        circuit=circuit,
        segment_load_flows=flows,
        cable_requests=tuple(
            cable_request(segment_id, current)
            for segment_id, _, _, _, current in segment_specs
        ),
        protection_points=(
            ProtectionPoint(
                "tx", "connection", CircuitApplication.DISTRIBUTION, ("MCCB",), "3P"
            ),
            ProtectionPoint(
                "main", "feeder", CircuitApplication.DISTRIBUTION, ("MCCB",), "3P"
            ),
            ProtectionPoint(
                "db",
                "final",
                CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
                ("MCCB",),
                "3P",
            ),
        ),
        upstream_short_circuit_capacity_mva=100,
        voltage_drop_limit_pct=5,
        voltage_drop_limit_rule_code="ELEC.VDROP.LIMIT",
        maximum_cable_combinations=100,
        maximum_output_combinations=3,
    )


def test_source_is_automatically_resolved_from_transformer_and_100mva_system():
    source, warnings = resolve_radial_source_electrical(request())
    assert warnings == []
    assert source is not None
    assert source.three_phase_r_ohm > 0
    assert source.three_phase_x_ohm > source.three_phase_r_ohm
    assert source.phase_neutral_r_ohm == source.phase_pe_r_ohm
    assert "HANDBOOK:4.6-41" in source.source_reference_ids[-1]


def test_one_call_closes_transformer_to_load_calculation_chain():
    result = calculate_radial_complete_circuit(request(), approved_rules())
    combinations = (
        result.outputs["viable_combinations"]
        + result.outputs["incomplete_combinations"]
    )
    assert combinations
    first = combinations[0]
    assert len(first["cables"]) == 3
    assert len(first["breakers"]) == 3
    nodes = first["chain_result"]["outputs"]["node_results"]
    assert [node["node_name"] for node in nodes] == [
        "变压器低压出口",
        "低压柜",
        "分配电箱",
        "用电设备",
    ]
    assert all(node["three_phase_short_circuit_ka"] for node in nodes)
    assert all(node["earth_fault_current_a"] for node in nodes)
    assert nodes[0]["three_phase_short_circuit_ka"] > nodes[-1]["three_phase_short_circuit_ka"]
    assert first["chain_result"]["outputs"]["terminal_voltage_drop_percent"] > 0
    assert len(first["protection_coordination"]) == 2
