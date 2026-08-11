from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.motor_electronic_mccb import (
    evaluate_cdm3e_motor_reference,
    load_cdm3e_catalog,
)


def test_cdm3e_catalog_is_traceable_and_non_formal():
    catalog = load_cdm3e_catalog()

    assert catalog["status"] == "verified"
    assert catalog["formal_calculation_allowed"] is False
    assert catalog["source"]["setting_reference"] == "PDF第4页，印刷第195～196页"
    assert catalog["products"][0]["short_delay_i2t_off"][
        "maximum_breaking_times_s"
    ] == [0.1, 0.14, 0.22, 0.32, 0.5]


def test_cdm3e_63a_uses_first_nominal_short_delay_above_starting_current():
    result = evaluate_cdm3e_motor_reference(
        motor_rated_current_a=57.972,
        motor_starting_current_a=423.2,
        conductor_corrected_ampacity_a=80,
        system_voltage_v=400,
        required_icu_ka=20,
        terminal_minimum_fault_current_a=470,
        phase_maximum_clearing_time_s=0.2,
        pe_maximum_clearing_time_s=0.13,
    )

    assert result["model"] == "CDM3E-125/63"
    assert result["long_delay_mode"] == "OFF"
    assert result["short_delay_multiplier"] == 7
    assert result["short_delay_pickup_nominal_a"] == 441
    assert result["starting_nominal_ride_through_status"] == PASS
    assert result["terminal_nominal_pickup_status"] == PASS
    assert result["nominal_thermal_time_status"] == PASS
    assert result["short_delay_pickup_guarantee_status"] == UNKNOWN
    assert result["thermal_clearing_formal_status"] == UNKNOWN
    assert result["icu_status"] == PASS
    assert result["type_2_coordination_status"] == UNKNOWN
    assert result["formal_status"] == UNKNOWN


def test_cdm3e_nominal_thermal_check_fails_when_cable_needs_faster_clearing():
    result = evaluate_cdm3e_motor_reference(
        motor_rated_current_a=57.972,
        motor_starting_current_a=423.2,
        conductor_corrected_ampacity_a=80,
        system_voltage_v=400,
        required_icu_ka=20,
        terminal_minimum_fault_current_a=760,
        phase_maximum_clearing_time_s=0.03,
        pe_maximum_clearing_time_s=0.04,
    )

    assert result["short_delay_maximum_breaking_time_s"] == 0.1
    assert result["governing_maximum_clearing_time_s"] == 0.03
    assert result["nominal_thermal_time_status"] == FAIL
    assert result["provisional_status"] == FAIL


def test_cdm3e_does_not_extrapolate_400_415v_breaking_capacity_to_380v():
    result = evaluate_cdm3e_motor_reference(
        motor_rated_current_a=57.972,
        motor_starting_current_a=423.2,
        conductor_corrected_ampacity_a=80,
        system_voltage_v=380,
        required_icu_ka=20,
        terminal_minimum_fault_current_a=760,
        phase_maximum_clearing_time_s=0.2,
        pe_maximum_clearing_time_s=0.2,
    )

    assert result["system_voltage_status"] == UNKNOWN
    assert result["icu_status"] == UNKNOWN
    assert result["provisional_status"] == UNKNOWN
