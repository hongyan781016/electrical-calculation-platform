from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.motor import (
    MotorCablePreselectionInput,
    MotorKnownBasis,
    MotorLoadInput,
    MotorNetworkInput,
)
from src.electrical_calc.motor_circuit_service import (
    evaluate_motor_cable_candidates_in_network,
)
from src.electrical_calc.motor_catalog import (
    AVAILABLE_RATED_OUTPUT_POWERS_KW,
    COMPLETE_SELECTION_POWERS_KW,
    resolve_motor_reference_parameters,
)
from src.electrical_calc.motor import MotorCatalogQuery


def rules():
    codes = (
        "MOTOR.CURRENT.RATED",
        "MOTOR.START.CURRENT",
        "MOTOR.START.VOLTAGE.APPROX",
        "MOTOR.CABLE.SELECTION",
        "ELEC.CABLE.YJV.MULTICORE.AMPACITY",
        "ELEC.CABLE.TEMPERATURE.DERATING",
        "ELEC.CABLE.TRAY.GROUPING",
        "ELEC.CABLE.YJV.FOUR_CORE.PHASE_PE.IMPEDANCE",
        "ELEC.VDROP",
        "ELEC.VDROP.IMPEDANCE",
        "ELEC.SHORT_CIRCUIT",
        "ELEC.EARTH_FAULT.TN.IMPEDANCE",
        "ELEC.PHASE.THERMAL.WITHSTAND",
        "ELEC.PE.THERMAL.WITHSTAND",
    )
    return {code: {"status": "approved"} for code in codes}


def request(preconnected_mvar=0.1):
    motor = MotorLoadInput(
        known_basis=MotorKnownBasis.RATED_OUTPUT_POWER_KW,
        known_value=30,
        rated_voltage_v=380,
        power_factor=0.84,
        efficiency=0.936,
        locked_rotor_current_ratio=7.3,
    )
    cable = MotorCablePreselectionInput(
        rated_current_a=1,
        running_power_factor=None,
        rated_voltage_v=380,
        length_m=50,
        conductor_family="YJV",
        conductor_configuration_code="yjv_4c_3ph_n_pe",
        installation_scenario="tray",
        installation_temperature_c=40,
        tray_type="horizontal_perforated",
        tray_layers=1,
        tray_cables_per_layer=1,
    )
    network = MotorNetworkInput(
        transformer_family="scb11",
        transformer_capacity_kva=630,
        transformer_uk_percent=6,
        upstream_short_circuit_capacity_mva=100,
        minimum_starting_bus_voltage_percent=85,
        preconnected_reactive_load_mvar=preconnected_mvar,
    )
    return motor, cable, network


def test_each_motor_cable_candidate_is_recalculated_in_complete_network():
    motor, cable, network = request()
    result = evaluate_motor_cable_candidates_in_network(
        motor, cable, network, rules()
    )

    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    candidates = result.outputs["candidates"]
    assert len(candidates) > 1
    first, second = candidates[:2]
    assert first["chain"]["outputs"]["terminal_voltage_drop_percent"] > second["chain"]["outputs"]["terminal_voltage_drop_percent"]
    assert first["chain"]["outputs"]["terminal_three_phase_short_circuit_ka"] < second["chain"]["outputs"]["terminal_three_phase_short_circuit_ka"]
    assert first["chain"]["outputs"]["terminal_earth_fault_current_a"] < second["chain"]["outputs"]["terminal_earth_fault_current_a"]
    assert first["phase_thermal_constraint"]["outputs"]["maximum_permitted_clearing_time_s"] > 0
    assert first["pe_thermal_constraint"]["outputs"]["maximum_permitted_clearing_time_s"] > 0
    breaker = first["breaker_requirements"]
    assert breaker is not None
    assert breaker["status"] == UNKNOWN
    assert breaker["outputs"]["required_parameters"]["breaking_capacity_min_ka"] == first["chain"]["outputs"]["node_results"][0]["three_phase_short_circuit_ka"]
    assert breaker["outputs"]["required_parameters"]["terminal_minimum_fault_current_a"] == first["chain"]["outputs"]["terminal_earth_fault_current_a"]
    assert breaker["outputs"]["required_parameters"]["governing_maximum_clearing_time_s"] == min(
        first["phase_thermal_constraint"]["outputs"]["maximum_permitted_clearing_time_s"],
        first["pe_thermal_constraint"]["outputs"]["maximum_permitted_clearing_time_s"],
    )
    assert first["schneider_type1_reference"]["terminal_magnetic_trip_status"] == FAIL
    assert second["schneider_type1_reference"]["terminal_magnetic_trip_status"] == FAIL
    position = result.outputs["recommended_candidate_position"]
    assert position == 2
    recommended = candidates[position]
    assert recommended["cable_decision_status"] == PASS
    assert result.outputs["recommended_cable_specification"] == recommended["cable"]["cable_specification"]
    scheme = result.outputs["primary_scheme"]
    assert scheme == recommended["primary_scheme"]
    assert scheme["status"] == "有条件采用"
    assert scheme["status_label"] == "制造商1类配合及网络安全校核已闭合"
    assert "IEC/EN 60947-4-1" in scheme["selection_basis"]
    assert scheme["cable"] == recommended["cable"]["cable_specification"]
    assert "CVS100-MA" in scheme["breaker"]
    assert "LC1E65" in scheme["contactor"]
    summary = scheme["purchase_summary"]
    assert summary["decision"] == "有条件采用"
    assert "57.97A" in summary["design_basis"]
    assert summary["items"][0]["specification"] == (
        "YJV-0.6/1kV 3×25＋1×16 mm² 铜芯电缆"
    )
    assert summary["items"][0]["quantity"] == "50 m"
    assert summary["items"][1]["quantity"] == "1 台"
    assert summary["items"][3]["quantity"] == "1 台"
    assert "电缆综合复核" in scheme["closed_checks"]
    assert any("铭牌额定电流" in item for item in scheme["purchase_conditions"])
    assert "产品资料批准状态" in scheme["professional_pending"]
    product_schemes = recommended["product_scheme_candidates"]
    assert [item["scheme_id"] for item in product_schemes] == [
        "schneider_easypact_type1_dol",
        "cm3_magnetic_nxc_nxr",
        "chint_ns2_adjustable_mpcb",
        "delixi_cdm3e_independent_overload",
    ]
    assert product_schemes[0]["status"] == "有条件采用"
    assert first["starting_voltage"]["outputs"]["starting_bus_voltage_percent"] is not None
    assert first["starting_voltage"]["outputs"]["starting_motor_terminal_voltage_percent"] < second["starting_voltage"]["outputs"]["starting_motor_terminal_voltage_percent"]
    assert first["starting_voltage"]["outputs"]["starting_motor_terminal_voltage_percent"] < 90
    reference = recommended["schneider_type1_reference"]
    assert reference["phase_thermal_status"] == PASS
    assert reference["pe_thermal_status"] == PASS
    assert reference["terminal_magnetic_trip_status"] == PASS


def test_verified_motor_power_coverage_matrix_does_not_overstate_purchase_scope():
    purchase_ready: list[float] = []
    for power_kw in AVAILABLE_RATED_OUTPUT_POWERS_KW:
        reference = resolve_motor_reference_parameters(
            MotorCatalogQuery(power_kw, 4), rules()
        )
        motor, cable, network = request(preconnected_mvar=None)
        motor = MotorLoadInput(
            known_basis=MotorKnownBasis.RATED_OUTPUT_POWER_KW,
            known_value=power_kw,
            rated_voltage_v=380,
            power_factor=reference.outputs["power_factor"],
            efficiency=reference.outputs["efficiency"],
            locked_rotor_current_ratio=reference.outputs[
                "locked_rotor_current_ratio"
            ],
        )
        result = evaluate_motor_cable_candidates_in_network(
            motor, cable, network, rules()
        )
        position = result.outputs.get("recommended_candidate_position")
        if position is not None:
            scheme = result.outputs["candidates"][position]["primary_scheme"]
            if scheme["status"] == "有条件采用":
                purchase_ready.append(power_kw)

    assert tuple(purchase_ready) == COMPLETE_SELECTION_POWERS_KW


def test_smallest_catalog_motor_uses_limit_current_evidence_not_oversized_cm3_route():
    reference = resolve_motor_reference_parameters(
        MotorCatalogQuery(0.12, 4), rules()
    )
    _, cable, network = request(preconnected_mvar=None)
    motor = MotorLoadInput(
        known_basis=MotorKnownBasis.RATED_OUTPUT_POWER_KW,
        known_value=0.12,
        rated_voltage_v=380,
        power_factor=reference.outputs["power_factor"],
        efficiency=reference.outputs["efficiency"],
        locked_rotor_current_ratio=reference.outputs["locked_rotor_current_ratio"],
    )
    result = evaluate_motor_cable_candidates_in_network(
        motor, cable, network, rules()
    )
    position = result.outputs["recommended_candidate_position"]
    candidate = result.outputs["candidates"][position]

    assert candidate["cable"]["phase_section_mm2"] == 6
    assert candidate["primary_scheme"]["scheme_id"] == "schneider_ma_chint_control"
    assert candidate["primary_scheme"]["status"] == "有条件采用"
    assert "制造商成套配合等级" in candidate["primary_scheme"]["professional_pending"]


def test_missing_preconnected_load_keeps_starting_voltage_pending_but_other_checks_run():
    motor, cable, network = request(preconnected_mvar=None)
    result = evaluate_motor_cable_candidates_in_network(
        motor, cable, network, rules()
    )

    assert result.outputs["candidates"]
    assert result.outputs["candidates"][0]["starting_voltage"] is None
    assert result.outputs["candidates"][0]["chain"]["outputs"]["terminal_voltage_drop_percent"] is not None
    assert any("预接负荷无功" in warning for warning in result.warnings)


def test_explicit_400v_network_uses_matching_motor_and_network_voltages():
    motor, cable, network = request()
    motor = MotorLoadInput(
        **{**motor.__dict__, "rated_voltage_v": 400}
    )
    cable = MotorCablePreselectionInput(
        **{**cable.__dict__, "rated_voltage_v": 400}
    )
    network = MotorNetworkInput(
        **{
            **network.__dict__,
            "system_voltage_v": 400,
            "line_to_earth_voltage_v": 230,
        }
    )

    result = evaluate_motor_cable_candidates_in_network(
        motor, cable, network, rules()
    )

    assert result.outputs["candidates"]
    first = result.outputs["candidates"][0]
    assert first["chain"]["outputs"]["terminal_voltage_drop_percent"] is not None
    assert first["chain"]["outputs"]["node_results"][0]["three_phase_short_circuit_ka"] is not None
    assert first["chain"]["outputs"]["terminal_earth_fault_current_a"] is not None
    ns2 = first["ns2_motor_reference"]
    assert ns2["system_voltage_status"] == PASS
    assert ns2["standalone_icu_status"] == PASS
    assert ns2["standalone_ics_status"] == PASS


def test_transformer_catalog_combination_must_match_exactly():
    motor, cable, network = request()
    network = MotorNetworkInput(
        **{**network.__dict__, "transformer_capacity_kva": 700}
    )
    result = evaluate_motor_cable_candidates_in_network(
        motor, cable, network, rules()
    )

    assert result.status == UNKNOWN
    assert result.outputs["candidates"] == []
    assert any("不插值" in warning for warning in result.warnings)
