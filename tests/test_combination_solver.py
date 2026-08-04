from math import sqrt
from types import SimpleNamespace

from src.electrical_calc.cable_selector import (
    CableInstallationConditions,
    CableSelectionRequest,
)
from src.electrical_calc.combination_solver import (
    CombinationSolverRequest,
    ProtectionPoint,
    _candidate_disconnection_check,
    _candidate_pe_thermal_check,
    _candidate_phase_thermal_check,
    _candidate_coordination_checks,
    _candidate_pe_minimum_section_check,
    solve_complete_circuit_combinations,
)
from src.electrical_calc.complete_circuit import (
    CircuitApplication,
    CircuitNode,
    CircuitSegment,
    CompleteCircuit,
    ConnectionMode,
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
    ResolvedSegmentElectrical,
    ResolvedSegmentLoadFlow,
    ResolvedSourceElectrical,
)
from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.rcd_protection import RcdProtectionInput
from src.electrical_calc.pole_configuration import PoleAndNeutralInput
from src.electrical_calc.protection_coordination import (
    ManufacturerCoordinationEvidence,
    ProtectionDeviceIdentity,
    load_product_coordination_cases,
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
    )
    return {code: {"status": "approved"} for code in codes}


def complete_circuit():
    return CompleteCircuit(
        id="c-closed-loop",
        code="AL-01",
        name="长距离普通三相回路",
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
            phase=Phase.THREE,
            circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
            load_profile=LoadProfile.ORDINARY_EQUIPMENT,
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
                "load",
                1,
                NodeType.LOAD_TERMINAL,
                "负荷端",
                380,
            ),
        ),
        segments=(
            CircuitSegment(
                "line",
                0,
                "tx",
                "load",
                SegmentType.CABLE,
                Phase.THREE,
                500,
                "tray",
                conductor_family="YJV",
                construction_code="yjv_4c_3ph_n_pe",
            ),
        ),
        rule_set_version="approved-test",
    )


def solver_request(**changes):
    design_current = 30_000 / (sqrt(3) * 380 * 0.9)
    circuit = complete_circuit()
    request = CombinationSolverRequest(
        circuit=circuit,
        source_electrical=ResolvedSourceElectrical(
            three_phase_r_ohm=0.003,
            three_phase_x_ohm=0.008,
            phase_neutral_r_ohm=0.01,
            phase_neutral_x_ohm=0.02,
            phase_pe_r_ohm=0.01,
            phase_pe_x_ohm=0.02,
            status="approved",
            source_reference_ids=("source-approved",),
        ),
        fixed_segment_electrical=(),
        segment_load_flows=(
            ResolvedSegmentLoadFlow(
                segment_id="line",
                design_current_a=design_current,
                power_factor=0.9,
                phase=Phase.THREE,
                status="approved",
                source_reference_ids=("load-approved",),
            ),
        ),
        cable_requests=(
            CableSelectionRequest(
                segment_id="line",
                family="YJV",
                configuration_code="yjv_4c_3ph_n_pe",
                phase=Phase.THREE,
                system_voltage_v=380,
                installation_scenario="tray",
                minimum_required_ampacity_a=design_current,
                neutral_required=False,
                protective_conductor_mode="included",
                conditions=CableInstallationConditions(
                    temperature_c=40,
                    tray_type="horizontal_perforated",
                    tray_layers=1,
                    tray_cables_per_layer=1,
                ),
            ),
        ),
        protection_points=(
            ProtectionPoint(
                node_id="tx",
                protected_segment_id="line",
                circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
                allowed_families=("MCCB",),
                pole_requirement="3P",
                connection_mode=ConnectionMode.FIXED_CONNECTED,
            ),
        ),
        maximum_short_circuit_voltage_factor=1.05,
        minimum_fault_voltage_factor=0.8,
        voltage_drop_limit_pct=5,
        voltage_drop_limit_rule_code="ELEC.VDROP.LIMIT",
        maximum_cable_combinations=50,
        maximum_output_combinations=30,
    )
    return CombinationSolverRequest(**{**request.__dict__, **changes})


def test_solver_rejects_small_sections_by_voltage_drop_and_keeps_larger_combination():
    result = solve_complete_circuit_combinations(
        solver_request(),
        approved_rules(),
    )
    viable = result.outputs["viable_combinations"]
    assert viable
    first = viable[0]
    assert first["cables"][0]["phase_section_mm2"] == 50
    assert first["breakers"][0]["family"] == "MCCB"
    assert first["breakers"][0]["selected_icu_ka"] == 35
    rejected = result.outputs["rejected_combinations"]
    assert any(
        item["reason_code"] == "voltage_drop_exceeded"
        and item["cables"][0]["phase_section_mm2"] < 50
        for item in rejected
    )
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert any("PE热稳定" in item for item in first["missing_items"])
    assert first["breakers"][0]["automatic_disconnection"]["maximum_time_s"] == 5
    assert first["breakers"][0]["automatic_disconnection"]["status"] == UNKNOWN
    assert first["breakers"][0]["rcd"]["provisional_status"] == UNKNOWN
    assert any("MCCB/ACB" in item for item in first["missing_items"])
    assert any("RCD独立校核" in item for item in first["missing_items"])
    assert any("Ics/Icw独立校核" in item for item in first["missing_items"])
    assert any(
        "相导体短路热稳定" in item for item in first["missing_items"]
    )


def test_candidate_disconnection_time_uses_application_and_candidate_rating():
    lighting = ProtectionPoint(
        node_id="db",
        protected_segment_id="line",
        circuit_application=CircuitApplication.LIGHTING_FINAL,
        allowed_families=("MCB",),
        pole_requirement="1P+N",
        mcb_trip_curve="C",
    )
    socket = ProtectionPoint(
        node_id="db",
        protected_segment_id="line",
        circuit_application=CircuitApplication.SOCKET_FINAL,
        allowed_families=("MCB",),
        pole_requirement="1P+N",
        mcb_trip_curve="C",
    )
    base_candidate = {
        "candidate_id": "db:MCB:63:32",
        "family": "MCB",
        "rated_current_a": 32,
        "automatic_trip_status": PASS,
    }
    lighting_check = _candidate_disconnection_check(
        lighting, base_candidate, 220
    )
    socket_boundary = _candidate_disconnection_check(
        socket,
        {**base_candidate, "rated_current_a": 63},
        220,
    )
    socket_over = _candidate_disconnection_check(
        socket,
        {**base_candidate, "rated_current_a": 80},
        220,
    )
    assert lighting_check["maximum_time_s"] == 0.4
    assert lighting_check["status"] == PASS
    assert socket_boundary["maximum_time_s"] == 0.4
    assert socket_over["maximum_time_s"] == 5


def test_mccb_disconnection_remains_unknown_without_product_trip_curve():
    point = ProtectionPoint(
        node_id="main",
        protected_segment_id="feeder",
        circuit_application=CircuitApplication.DISTRIBUTION,
        allowed_families=("MCCB",),
        pole_requirement="3P",
    )
    result = _candidate_disconnection_check(
        point,
        {
            "candidate_id": "main:MCCB:160:125",
            "family": "MCCB",
            "rated_current_a": 125,
            "automatic_trip_status": UNKNOWN,
        },
        220,
    )
    assert result["maximum_time_s"] == 5
    assert result["status"] == UNKNOWN
    assert "具体脱扣器" in result["reason"]


def test_solver_rejects_explicitly_noncompliant_rcd_configuration():
    base = solver_request()
    point = ProtectionPoint(
        node_id="tx",
        protected_segment_id="line",
        circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
        allowed_families=("MCCB",),
        pole_requirement="3P",
        connection_mode=ConnectionMode.FIXED_CONNECTED,
        rcd=RcdProtectionInput(
            required=True,
            scenario_code="additional_30ma",
            residual_waveform_code="ac",
            selected_rated_residual_current_ma=100,
            normal_leakage_current_ma=10,
        ),
    )
    result = solve_complete_circuit_combinations(
        CombinationSolverRequest(
            **{
                **base.__dict__,
                "protection_points": (point,),
            }
        ),
        {
            **approved_rules(),
            "ELEC.RCD.PARAMETERS": {"status": "approved"},
        },
    )
    assert result.outputs["viable_combinations"] == []
    assert any(
        item["reason_code"] == "rcd_configuration_failed"
        for item in result.outputs["rejected_combinations"]
    )


def test_solver_rejects_switched_pen_configuration():
    base = solver_request()
    point = ProtectionPoint(
        node_id="tx",
        protected_segment_id="line",
        circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
        allowed_families=("MCCB",),
        pole_requirement="3P",
        connection_mode=ConnectionMode.FIXED_CONNECTED,
        pole_and_neutral=PoleAndNeutralInput(
            neutral_required=False,
            neutral_pole_mode="absent",
            pen_conductor_present=True,
            pen_switched_or_isolated=True,
        ),
    )
    result = solve_complete_circuit_combinations(
        CombinationSolverRequest(
            **{
                **base.__dict__,
                "protection_points": (point,),
            }
        ),
        approved_rules(),
    )
    assert result.outputs["viable_combinations"] == []
    assert any(
        item["reason_code"] == "pole_or_neutral_configuration_failed"
        for item in result.outputs["rejected_combinations"]
    )


def test_yjv_pe_thermal_uses_mcb_instantaneous_upper_time_bound():
    cable = {
        "family": "YJV",
        "protective_section_mm2": 6,
    }
    breaker = {
        "family": "MCB",
        "earth_fault_current_a": 1000,
        "automatic_disconnection": {"status": PASS},
    }
    result = _candidate_pe_thermal_check(
        cable,
        breaker,
        approved_rules(),
    )
    assert result["provisional_status"] == PASS
    assert result["outputs"]["actual_thermal_stress_a2s"] == 100_000
    assert result["outputs"]["k_a_sqrt_s_per_mm2"] == 143
    assert result["outputs"]["required_protective_conductor_section_mm2"] == round(
        1000 * (0.1**0.5) / 143,
        6,
    )
    assert "0.1s保守计算" in result["calculation_basis"]


def test_pe_thermal_failure_is_available_as_hard_combination_condition():
    result = _candidate_pe_thermal_check(
        {"family": "YJV", "protective_section_mm2": 6},
        {
            "family": "MCB",
            "earth_fault_current_a": 5000,
            "automatic_disconnection": {"status": PASS},
        },
        approved_rules(),
    )
    assert result["provisional_status"] == "不通过"


def test_phase_thermal_uses_maximum_short_circuit_and_phase_section():
    result = _candidate_phase_thermal_check(
        {"family": "YJV", "phase_section_mm2": 6},
        {
            "family": "MCB",
            "maximum_phase_short_circuit_current_a": 1000,
            "automatic_disconnection": {"status": PASS},
        },
        approved_rules(),
    )
    assert result["provisional_status"] == PASS
    assert result["outputs"]["phase_conductor_section_mm2"] == 6
    assert result["outputs"]["k_a_sqrt_s_per_mm2"] == 143
    assert result["outputs"]["actual_thermal_stress_a2s"] == 100_000


def test_phase_thermal_failure_is_available_as_hard_combination_condition():
    result = _candidate_phase_thermal_check(
        {"family": "BV", "phase_section_mm2": 4},
        {
            "family": "MCB",
            "maximum_phase_short_circuit_current_a": 5000,
            "automatic_disconnection": {"status": PASS},
        },
        approved_rules(),
    )
    assert result["provisional_status"] == FAIL
    assert result["outputs"]["k_a_sqrt_s_per_mm2"] == 115


def test_yjv_included_pe_is_checked_against_table_54_2():
    passing = _candidate_pe_minimum_section_check(
        {
            "family": "YJV",
            "phase_section_mm2": 50,
            "protective_section_mm2": 25,
        },
        approved_rules(),
    )
    failing = _candidate_pe_minimum_section_check(
        {
            "family": "YJV",
            "phase_section_mm2": 50,
            "protective_section_mm2": 16,
        },
        approved_rules(),
    )
    assert passing["provisional_status"] == PASS
    assert failing["provisional_status"] == FAIL


def test_solver_uses_installation_node_short_circuit_to_reject_mcb_icu():
    base = solver_request()
    point = ProtectionPoint(
        node_id="tx",
        protected_segment_id="line",
        circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
        allowed_families=("MCB",),
        pole_requirement="3P",
        mcb_trip_curve="C",
    )
    request = CombinationSolverRequest(
        **{
            **base.__dict__,
            "protection_points": (point,),
        }
    )
    result = solve_complete_circuit_combinations(request, approved_rules())
    assert result.outputs["viable_combinations"] == []
    assert any(
        item["reason_code"] == "no_breaker_candidate"
        for item in result.outputs["rejected_combinations"]
    )


def test_unconfirmed_conductor_allocation_is_retained_as_incomplete_not_passed():
    base = solver_request()
    cable_request = CableSelectionRequest(
        **{
            **base.cable_requests[0].__dict__,
            "protective_conductor_mode": "unconfirmed",
        }
    )
    request = CombinationSolverRequest(
        **{
            **base.__dict__,
            "cable_requests": (cable_request,),
        }
    )
    result = solve_complete_circuit_combinations(request, approved_rules())
    assert result.outputs["viable_combinations"] == []
    assert result.outputs["incomplete_combinations"]
    assert result.provisional_status == UNKNOWN
    assert any(
        "N/PE配置" in item
        for item in result.outputs["incomplete_combinations"][0]["missing_items"]
    )


def test_solver_rejects_cable_request_not_based_on_segment_load_current():
    base = solver_request()
    wrong_request = CableSelectionRequest(
        **{
            **base.cable_requests[0].__dict__,
            "minimum_required_ampacity_a": 999,
        }
    )
    request = CombinationSolverRequest(
        **{
            **base.__dict__,
            "cable_requests": (wrong_request,),
        }
    )
    result = solve_complete_circuit_combinations(request, approved_rules())
    assert result.outputs["viable_combinations"] == []
    assert any("必须以本段Ib" in warning for warning in result.warnings)


def test_solver_output_order_and_cap_are_deterministic():
    first = solve_complete_circuit_combinations(
        solver_request(maximum_output_combinations=2),
        approved_rules(),
    )
    second = solve_complete_circuit_combinations(
        solver_request(maximum_output_combinations=2),
        approved_rules(),
    )
    first_ids = [
        item["combination_id"]
        for item in first.outputs["viable_combinations"]
        + first.outputs["incomplete_combinations"]
    ]
    second_ids = [
        item["combination_id"]
        for item in second.outputs["viable_combinations"]
        + second.outputs["incomplete_combinations"]
    ]
    assert first_ids == second_ids
    assert len(first_ids) == 2
    assert first.outputs["search_summary"]["output_truncated"] is True


def test_multisegment_chain_uses_fixed_connection_and_two_independent_protection_points():
    base = solver_request()
    original = base.circuit
    nodes = (
        CircuitNode("tx", 0, NodeType.TRANSFORMER_LV, "变压器低压端", 380),
        CircuitNode("main", 1, NodeType.MAIN_SWITCHBOARD, "低压总柜", 380),
        CircuitNode("db", 2, NodeType.DISTRIBUTION_BOARD, "分配电箱", 380),
        CircuitNode("load", 3, NodeType.LOAD_TERMINAL, "负荷端", 380),
    )
    segments = (
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
            "feeder",
            1,
            "main",
            "db",
            SegmentType.CABLE,
            Phase.THREE,
            50,
            "tray",
            conductor_family="YJV",
            construction_code="yjv_4c_3ph_n_pe",
        ),
        CircuitSegment(
            "final",
            2,
            "db",
            "load",
            SegmentType.CABLE,
            Phase.THREE,
            30,
            "tray",
            conductor_family="YJV",
            construction_code="yjv_4c_3ph_n_pe",
        ),
    )
    circuit = CompleteCircuit(
        **{
            **original.__dict__,
            "nodes": nodes,
            "segments": segments,
        }
    )
    terminal_current = 30_000 / (sqrt(3) * 380 * 0.9)

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

    request = CombinationSolverRequest(
        **{
            **base.__dict__,
            "circuit": circuit,
            "fixed_segment_electrical": (
                ResolvedSegmentElectrical(
                    segment_id="bus",
                    phase_neutral_applicable=False,
                    voltage_drop_r_ohm_per_km=0.04,
                    voltage_drop_x_ohm_per_km=0.02,
                    three_phase_r_ohm_per_km=0.04,
                    three_phase_x_ohm_per_km=0.02,
                    phase_neutral_r_ohm_per_km=None,
                    phase_neutral_x_ohm_per_km=None,
                    phase_pe_r_ohm_per_km=0.394,
                    phase_pe_x_ohm_per_km=0.212,
                    corrected_ampacity_a=1600,
                    status="approved",
                    source_reference_ids=("busway-approved",),
                ),
            ),
            "segment_load_flows": (
                ResolvedSegmentLoadFlow(
                    "bus",
                    200,
                    0.85,
                    Phase.THREE,
                    "approved",
                    ("main-summary",),
                ),
                ResolvedSegmentLoadFlow(
                    "feeder",
                    125,
                    0.88,
                    Phase.THREE,
                    "approved",
                    ("db-summary",),
                ),
                ResolvedSegmentLoadFlow(
                    "final",
                    terminal_current,
                    0.9,
                    Phase.THREE,
                    "approved",
                    ("terminal-load",),
                ),
            ),
            "cable_requests": (
                cable_request("feeder", 125),
                cable_request("final", terminal_current),
            ),
            "protection_points": (
                ProtectionPoint(
                    "main",
                    "feeder",
                    CircuitApplication.DISTRIBUTION,
                    ("MCCB",),
                    "3P",
                ),
                ProtectionPoint(
                    "db",
                    "final",
                    CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
                    ("MCCB",),
                    "3P",
                ),
            ),
            "maximum_cable_combinations": 250,
            "maximum_output_combinations": 5,
        }
    )
    result = solve_complete_circuit_combinations(request, approved_rules())
    assert result.outputs["viable_combinations"]
    first = result.outputs["viable_combinations"][0]
    assert {item["candidate_id"].split(":")[0] for item in first["cables"]} == {
        "feeder",
        "final",
    }
    assert len(first["breakers"]) == 2
    node_results = first["chain_result"]["outputs"]["node_results"]
    assert len(node_results) == 4
    drops = [item["cumulative_voltage_drop_v"] for item in node_results]
    assert drops == sorted(drops)
    coordination = first["protection_coordination"]
    assert len(coordination) == 1
    assert coordination[0]["upstream_node_id"] == "main"
    assert coordination[0]["downstream_node_id"] == "db"
    assert coordination[0]["provisional_status"] == UNKNOWN
    assert any(
        "main→db" in item and "具体产品编号" in item
        for item in first["missing_items"]
    )


def test_single_protection_point_has_no_internal_selectivity_pair():
    result = solve_complete_circuit_combinations(
        solver_request(),
        approved_rules(),
    )
    first = result.outputs["viable_combinations"][0]
    assert first["protection_coordination"] == []
    assert not any(
        "上下级选择性" in item for item in first["missing_items"]
    )


def test_coordination_pair_can_consume_exact_product_table():
    upstream_identity = ProtectionDeviceIdentity(
        "UP-250", "Ir=200A; Isd=8Ir", "MCCB", 200
    )
    downstream_identity = ProtectionDeviceIdentity(
        "DOWN-100", "In=63A; Im=10In", "MCCB", 63
    )
    points = (
        ProtectionPoint(
            "main",
            "feeder",
            CircuitApplication.DISTRIBUTION,
            ("MCCB",),
            "3P",
            product_identity=upstream_identity,
        ),
        ProtectionPoint(
            "db",
            "final",
            CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
            ("MCCB",),
            "3P",
            product_identity=downstream_identity,
            backup_protection_required=True,
        ),
    )
    evidence = ManufacturerCoordinationEvidence(
        "table-1",
        "厂家选择性与级联表",
        "表1，PDF第10页",
        "verified",
        upstream_identity.product_code,
        upstream_identity.configuration_reference,
        downstream_identity.product_code,
        downstream_identity.configuration_reference,
        selectivity_limit_ka=15,
        backup_protection_limit_ka=25,
    )
    checks = _candidate_coordination_checks(
        points,
        (
            {
                "candidate_id": "main:MCCB:250:200",
                "family": "MCCB",
                "rated_current_a": 200,
            },
            {
                "candidate_id": "db:MCCB:100:63",
                "family": "MCCB",
                "rated_current_a": 63,
                "maximum_phase_short_circuit_current_a": 10_000,
            },
        ),
        {
            "feeder": SimpleNamespace(sequence=1),
            "final": SimpleNamespace(sequence=2),
        },
        (evidence,),
    )
    assert checks[0]["provisional_status"] == PASS
    assert checks[0]["status"] == UNKNOWN
    assert checks[0]["backup_protection"]["provisional_status"] == PASS


def test_solver_coordination_pair_consumes_verified_schneider_case():
    case = load_product_coordination_cases()[0]
    points = (
        ProtectionPoint(
            "main",
            "feeder",
            CircuitApplication.DISTRIBUTION,
            ("MCCB",),
            "3P",
            product_identity=case["upstream"],
        ),
        ProtectionPoint(
            "db",
            "final",
            CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
            ("MCB",),
            "3P",
            product_identity=case["downstream"],
        ),
    )
    checks = _candidate_coordination_checks(
        points,
        (
            {
                "candidate_id": "main:MCCB:100:100",
                "family": "MCCB",
                "rated_current_a": 100,
            },
            {
                "candidate_id": "db:MCB:63:50",
                "family": "MCB",
                "rated_current_a": 50,
                "maximum_phase_short_circuit_current_a": 800,
            },
        ),
        {
            "feeder": SimpleNamespace(sequence=1),
            "final": SimpleNamespace(sequence=2),
        },
        (case["evidence"],),
        400,
    )
    assert checks[0]["provisional_status"] == PASS
    assert checks[0]["selectivity"]["selectivity_limit_ka"] == 1
