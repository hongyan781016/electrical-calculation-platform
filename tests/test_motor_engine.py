from math import sqrt

import pytest

from src.electrical_calc.engine import PASS, UNKNOWN
from src.electrical_calc.motor import (
    MotorApproximateStartingInput,
    MotorBreakerRequirementInput,
    MotorBusLoadCondition,
    MotorCablePreselectionInput,
    MotorKnownBasis,
    MotorLoadInput,
    MotorStartingNetworkInput,
    MotorStartingFrequency,
    MotorStartingVoltageScenario,
)
from src.electrical_calc.motor_engine import (
    calculate_motor_breaker_requirements,
    calculate_motor_cable_preselection,
    calculate_motor_load,
    calculate_motor_selection_constraints,
    calculate_motor_starting_approximation,
    calculate_motor_starting_network,
    resolve_motor_starting_voltage_requirement,
)


def approved_rules(*codes):
    return {code: {"status": "approved"} for code in codes}


def test_nameplate_current_is_used_without_power_factor_or_efficiency():
    result = calculate_motor_load(
        MotorLoadInput(
            known_basis=MotorKnownBasis.NAMEPLATE_CURRENT_A,
            known_value=58,
            rated_voltage_v=380,
            locked_rotor_current_ratio=7,
        ),
        approved_rules("MOTOR.START.CURRENT"),
    )

    assert result.status == PASS
    assert result.outputs["rated_current_a"] == 58
    assert result.outputs["rated_current_source"] == "nameplate_current"
    assert result.outputs["starting_current_a"] == 406
    assert "MOTOR.CURRENT.RATED" not in result.rule_codes


def test_rated_output_power_uses_efficiency_and_power_factor():
    result = calculate_motor_load(
        MotorLoadInput(
            known_basis=MotorKnownBasis.RATED_OUTPUT_POWER_KW,
            known_value=30,
            rated_voltage_v=380,
            power_factor=0.86,
            efficiency=0.91,
            locked_rotor_current_ratio=6.5,
        ),
        approved_rules("MOTOR.CURRENT.RATED", "MOTOR.START.CURRENT"),
    )

    expected = 30_000 / (sqrt(3) * 380 * 0.91 * 0.86)
    assert result.status == PASS
    assert result.outputs["rated_current_a"] == pytest.approx(expected, abs=1e-6)
    assert result.outputs["starting_current_a"] == pytest.approx(expected * 6.5, abs=1e-6)


def test_missing_locked_rotor_ratio_keeps_rated_current_but_start_is_unknown():
    result = calculate_motor_load(
        MotorLoadInput(
            known_basis=MotorKnownBasis.NAMEPLATE_CURRENT_A,
            known_value=58,
            rated_voltage_v=380,
        ),
        {},
    )

    assert result.provisional_status == UNKNOWN
    assert result.outputs["rated_current_a"] == 58
    assert result.outputs["starting_current_a"] is None
    assert any("堵转电流" in warning for warning in result.warnings)


def test_power_basis_does_not_guess_efficiency_or_power_factor():
    result = calculate_motor_load(
        MotorLoadInput(
            known_basis=MotorKnownBasis.RATED_OUTPUT_POWER_KW,
            known_value=30,
            rated_voltage_v=380,
            locked_rotor_current_ratio=7,
        ),
        {},
    )

    assert result.status == UNKNOWN
    assert result.outputs["rated_current_a"] is None
    assert any("功率因数和效率" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("frequency", "bus_condition", "expected"),
    (
        (
            MotorStartingFrequency.FREQUENT,
            MotorBusLoadCondition.LIGHTING_OR_SENSITIVE_LOADS,
            90,
        ),
        (
            MotorStartingFrequency.INFREQUENT,
            MotorBusLoadCondition.LIGHTING_OR_SENSITIVE_LOADS,
            85,
        ),
        (
            MotorStartingFrequency.INFREQUENT,
            MotorBusLoadCondition.NO_LIGHTING_OR_SENSITIVE_LOADS,
            80,
        ),
    ),
)
def test_starting_bus_voltage_requirement_is_automatic(
    frequency, bus_condition, expected
):
    result = resolve_motor_starting_voltage_requirement(
        MotorStartingVoltageScenario(frequency, bus_condition),
        approved_rules("MOTOR.START.VOLTAGE"),
    )

    assert result.status == PASS
    assert result.outputs["minimum_bus_voltage_percent"] == expected


def test_bus_without_other_loads_requires_torque_check_not_fixed_percentage():
    result = resolve_motor_starting_voltage_requirement(
        MotorStartingVoltageScenario(
            MotorStartingFrequency.INFREQUENT,
            MotorBusLoadCondition.NO_OTHER_LOADS,
        ),
        approved_rules("MOTOR.START.VOLTAGE"),
    )

    assert result.status == UNKNOWN
    assert result.outputs["minimum_bus_voltage_percent"] is None
    assert result.outputs["requires_motor_terminal_torque_check"] is True


def test_unknown_starting_scenario_only_returns_conservative_preselection():
    result = resolve_motor_starting_voltage_requirement(
        MotorStartingVoltageScenario(
            MotorStartingFrequency.UNKNOWN,
            MotorBusLoadCondition.UNKNOWN,
        ),
        approved_rules("MOTOR.START.VOLTAGE"),
    )

    assert result.status == UNKNOWN
    assert result.outputs["conservative_preselection_percent"] == 90


def test_selection_constraints_are_not_product_models():
    result = calculate_motor_selection_constraints(
        rated_current_a=58,
        starting_current_a=406,
        rules=approved_rules(
            "MOTOR.SCPD.BREAKER",
            "MOTOR.CONTACTOR.AC3",
            "MOTOR.OVERLOAD.RELAY",
        ),
    )

    assert result.status == PASS
    assert result.outputs["breaker_instantaneous_setting_min_a"] == 812
    assert result.outputs["breaker_instantaneous_setting_max_a"] == 1015
    assert result.outputs["contactor_ac3_working_current_min_a"] == 58
    assert result.outputs["overload_setting_target_a"] == 58
    assert result.outputs["overload_adjustment_range_min_percent"] == 20
    assert result.outputs["breaker_long_delay_relation"] == "应接近且不小于电动机额定电流"
    assert "model" not in result.outputs


def test_unapproved_motor_rules_never_create_formal_pass():
    result = calculate_motor_selection_constraints(58, 406, {})

    assert result.provisional_status == PASS
    assert result.status == UNKNOWN


def test_motor_breaker_requirements_join_start_fault_and_cable_boundaries():
    result = calculate_motor_breaker_requirements(
        MotorBreakerRequirementInput(
            motor_rated_current_a=58,
            motor_starting_current_a=406,
            system_voltage_v=380,
            conductor_corrected_ampacity_a=75,
            installation_point_max_short_circuit_ka=14,
            terminal_minimum_fault_current_a=500,
            phase_maximum_clearing_time_s=0.02,
            pe_maximum_clearing_time_s=2,
        ),
        approved_rules(
            "MOTOR.SCPD.BREAKER",
            "ELEC.BREAKING.CAPACITY",
            "ELEC.PHASE.THERMAL.WITHSTAND",
            "ELEC.PE.THERMAL.WITHSTAND",
        ),
    )

    required = result.outputs["required_parameters"]
    assert result.status == UNKNOWN
    assert result.provisional_status == PASS
    assert required["rated_current_min_a"] == 58
    assert required["rated_current_max_a"] == 75
    assert required["breaking_capacity_min_ka"] == 14
    assert required["governing_maximum_clearing_time_s"] == 0.02
    mcb = next(item for item in result.outputs["candidates"] if item["family"] == "MCB")
    assert mcb["admissible_instantaneous_min_a"] is None
    assert mcb["instantaneous_terminal_fault_possible"] == UNKNOWN
    mccb = next(item for item in result.outputs["candidates"] if item["family"] == "MCCB")
    assert mccb["rated_current_a"] == 63
    assert mccb["selected_icu_ka"] == 25
    assert mccb["admissible_instantaneous_min_a"] == 812
    assert mccb["admissible_instantaneous_max_a"] == 945
    assert mccb["instantaneous_terminal_fault_possible"] == "不通过"
    assert mccb["actual_curve_check"] == UNKNOWN


def test_motor_breaker_requirements_report_when_no_standard_rating_fits_cable():
    result = calculate_motor_breaker_requirements(
        MotorBreakerRequirementInput(
            motor_rated_current_a=58,
            motor_starting_current_a=406,
            system_voltage_v=380,
            conductor_corrected_ampacity_a=60,
            installation_point_max_short_circuit_ka=14,
            terminal_minimum_fault_current_a=500,
            phase_maximum_clearing_time_s=0.02,
            pe_maximum_clearing_time_s=2,
        ),
        {},
    )

    assert result.outputs["candidates"] == []
    assert result.provisional_status == UNKNOWN
    assert any("没有同时满足" in warning for warning in result.warnings)


def test_starting_network_separates_bus_and_motor_terminal_voltage():
    result = calculate_motor_starting_network(
        MotorStartingNetworkInput(
            nominal_line_voltage_v=380,
            source_open_circuit_voltage_factor=1.0,
            locked_rotor_current_at_nominal_voltage_a=406,
            locked_rotor_power_factor=0.3,
            source_to_bus_r_ohm=0.005,
            source_to_bus_x_ohm=0.02,
            bus_to_motor_r_ohm=0.02,
            bus_to_motor_x_ohm=0.01,
            minimum_bus_voltage_percent=85,
        ),
        approved_rules("MOTOR.START.CURRENT", "MOTOR.START.VOLTAGE.NETWORK"),
    )

    assert result.status == PASS
    assert result.outputs["starting_bus_voltage_percent"] > 85
    assert (
        result.outputs["starting_motor_terminal_voltage_percent"]
        < result.outputs["starting_bus_voltage_percent"]
    )
    assert result.outputs["actual_starting_current_a"] < 406
    assert result.outputs["bus_voltage_check"] == PASS


def test_starting_network_can_fail_bus_voltage_limit():
    result = calculate_motor_starting_network(
        MotorStartingNetworkInput(
            nominal_line_voltage_v=380,
            source_open_circuit_voltage_factor=1.0,
            locked_rotor_current_at_nominal_voltage_a=406,
            locked_rotor_power_factor=0.3,
            source_to_bus_r_ohm=0.08,
            source_to_bus_x_ohm=0.2,
            bus_to_motor_r_ohm=0.02,
            bus_to_motor_x_ohm=0.01,
            minimum_bus_voltage_percent=90,
        ),
        approved_rules("MOTOR.START.CURRENT", "MOTOR.START.VOLTAGE.NETWORK"),
    )

    assert result.provisional_status == "不通过"
    assert result.outputs["bus_voltage_check"] == "不通过"


def test_starting_network_does_not_reuse_running_power_factor():
    result = calculate_motor_starting_network(
        MotorStartingNetworkInput(
            nominal_line_voltage_v=380,
            source_open_circuit_voltage_factor=1.0,
            locked_rotor_current_at_nominal_voltage_a=406,
            locked_rotor_power_factor=None,
            source_to_bus_r_ohm=0.005,
            source_to_bus_x_ohm=0.02,
            bus_to_motor_r_ohm=0.02,
            bus_to_motor_x_ohm=0.01,
            minimum_bus_voltage_percent=85,
        ),
        {},
    )

    assert result.status == UNKNOWN
    assert result.outputs["actual_starting_current_a"] is None
    assert any("堵转功率因数" in warning for warning in result.warnings)


def test_handbook_approximation_does_not_require_locked_rotor_power_factor():
    result = calculate_motor_starting_approximation(
        MotorApproximateStartingInput(
            nominal_network_voltage_kv=0.38,
            system_average_voltage_kv=0.4,
            motor_rated_voltage_kv=0.38,
            motor_rated_current_ka=0.057972,
            locked_rotor_current_ratio=7.3,
            bus_short_circuit_capacity_mva=10,
            preconnected_reactive_load_mvar=0.1,
            motor_feeder_reactance_ohm=0.01,
            minimum_bus_voltage_percent=85,
        ),
        approved_rules("MOTOR.START.VOLTAGE.APPROX"),
    )

    assert result.status == PASS
    assert result.outputs["starting_bus_voltage_percent"] > 85
    assert (
        result.outputs["starting_motor_terminal_voltage_percent"]
        < result.outputs["starting_bus_voltage_percent"]
    )
    assert result.outputs["motor_starting_current_ka"] > 0
    assert any("近似计算" in warning for warning in result.warnings)


def test_handbook_approximation_with_zero_feeder_reactance_has_equal_terminal_voltage():
    result = calculate_motor_starting_approximation(
        MotorApproximateStartingInput(
            nominal_network_voltage_kv=0.38,
            system_average_voltage_kv=0.4,
            motor_rated_voltage_kv=0.38,
            motor_rated_current_ka=0.057972,
            locked_rotor_current_ratio=7.3,
            bus_short_circuit_capacity_mva=10,
            preconnected_reactive_load_mvar=0,
            motor_feeder_reactance_ohm=0,
            minimum_bus_voltage_percent=85,
        ),
        approved_rules("MOTOR.START.VOLTAGE.APPROX"),
    )

    assert result.outputs["line_capacity_mva"] is None
    assert result.outputs["starting_motor_terminal_voltage_percent"] == pytest.approx(
        result.outputs["starting_bus_voltage_percent"]
    )


def test_handbook_approximation_remains_unofficial_until_rule_is_approved():
    result = calculate_motor_starting_approximation(
        MotorApproximateStartingInput(
            nominal_network_voltage_kv=0.38,
            system_average_voltage_kv=0.4,
            motor_rated_voltage_kv=0.38,
            motor_rated_current_ka=0.05,
            locked_rotor_current_ratio=7,
            bus_short_circuit_capacity_mva=5,
            preconnected_reactive_load_mvar=0,
            motor_feeder_reactance_ohm=0.01,
            minimum_bus_voltage_percent=80,
        ),
        {},
    )

    assert result.provisional_status in {PASS, "不通过"}
    assert result.status == UNKNOWN


def test_motor_cable_candidates_use_rated_current_and_calculate_running_drop():
    result = calculate_motor_cable_preselection(
        MotorCablePreselectionInput(
            rated_current_a=57.972,
            running_power_factor=0.84,
            rated_voltage_v=380,
            length_m=50,
            conductor_family="YJV",
            conductor_configuration_code="yjv_3c_3ph_pe",
            installation_scenario="tray",
            installation_temperature_c=40,
            tray_type="horizontal_perforated",
            tray_layers=1,
            tray_cables_per_layer=1,
        ),
        approved_rules(
            "ELEC.CABLE.YJV.AMPACITY",
            "ELEC.CABLE.TEMPERATURE.DERATING",
            "ELEC.CABLE.TRAY.GROUPING",
            "ELEC.VDROP.IMPEDANCE",
            "ELEC.VDROP",
            "MOTOR.CABLE.SELECTION",
        ),
    )

    first = result.outputs["candidates"][0]
    assert first["minimum_required_ampacity_a"] == 57.972
    assert first["corrected_ampacity_a"] >= 57.972
    assert first["running_voltage_drop_v"] is not None
    assert first["running_voltage_drop_percent"] > 0
    assert first["running_voltage_drop_status"] == UNKNOWN
    assert result.status == UNKNOWN


def test_motor_cable_without_derating_inputs_keeps_base_candidates_unofficial():
    result = calculate_motor_cable_preselection(
        MotorCablePreselectionInput(
            rated_current_a=57.972,
            running_power_factor=0.84,
            rated_voltage_v=380,
            length_m=50,
            conductor_family="YJV",
            conductor_configuration_code="yjv_3c_3ph_pe",
            installation_scenario="tray",
        ),
        {},
    )

    assert result.outputs["candidates"]
    assert result.outputs["conditions_complete"] is False
    assert any("基础载流量候选" in warning for warning in result.warnings)
