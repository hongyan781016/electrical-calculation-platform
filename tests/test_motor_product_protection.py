from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.motor_product_protection import (
    evaluate_cm3_motor_reference,
    load_cm3_motor_catalog,
    load_cm3_motor_curve,
)


def test_cm3_motor_catalog_is_traceable_and_non_formal():
    catalog = load_cm3_motor_catalog()

    assert catalog["status"] == "verified"
    assert catalog["formal_calculation_allowed"] is False
    assert catalog["source"]["motor_short_circuit_reference"] == "PDF第21页，C-10"
    assert catalog["source"]["curve_reference"] == "PDF第26～28页，C-15～C-17"
    curve = load_cm3_motor_curve()
    assert curve["source"]["pdf_pages"] == [26, 27, 28]
    assert len(curve["curves"]) == 5
    assert curve["curves"][0]["sample_count"] > 1000
    assert curve["uncertainty"]["pixel_allowance"] == 2


def test_cm3_63l_motor_reference_checks_instantaneous_boundaries():
    result = evaluate_cm3_motor_reference(
        motor_rated_current_a=57.97,
        motor_starting_current_a=423.2,
        conductor_corrected_ampacity_a=84,
        required_icu_ka=14,
        terminal_minimum_fault_current_a=762,
        phase_maximum_clearing_time_s=0.027,
        pe_maximum_clearing_time_s=3.5,
    )

    assert result["frame_code"] == "CM3-63L"
    assert result["rated_current_a"] == 63
    assert result["icu_ka"] == 35
    assert result["overload_reference_current_a"] == 63
    assert result["overload_current_deviation_percent"] == 8.676902
    assert result["overload_setting_not_below_motor_status"] == PASS
    assert result["overload_setting_closeness_status"] == UNKNOWN
    assert result["cold_no_trip_current_a"] == 63
    assert result["hot_trip_current_a"] == 75.6
    assert result["motor_120_percent_current_a"] == 69.564
    assert result["motor_120_percent_trip_guarantee_status"] == UNKNOWN
    assert result["motor_overload_characteristic_match_status"] == UNKNOWN
    assert result["short_circuit_pickup_nominal_a"] == 756
    assert result["short_circuit_pickup_min_a"] == 604.8
    assert result["short_circuit_pickup_max_a"] == 907.2
    assert result["starting_instantaneous_ride_through_status"] == PASS
    assert result["terminal_instantaneous_trip_status"] == UNKNOWN
    assert result["motor_starting_time_check"] == UNKNOWN
    assert result["phase_fault_clearing_time_check"] == PASS
    assert result["pe_fault_clearing_time_check"] == PASS
    assert result["fault_clearing_time_check"] == PASS
    assert result["status"] == UNKNOWN


def test_cm3_reference_marks_fault_below_pickup_as_not_instantaneous():
    result = evaluate_cm3_motor_reference(
        motor_rated_current_a=57.97,
        motor_starting_current_a=423.2,
        conductor_corrected_ampacity_a=63,
        required_icu_ka=14,
        terminal_minimum_fault_current_a=470,
        phase_maximum_clearing_time_s=0.01,
        pe_maximum_clearing_time_s=3.3,
    )

    assert result["starting_instantaneous_ride_through_status"] == PASS
    assert result["terminal_instantaneous_trip_status"] == FAIL
    assert result["phase_fault_clearing_time_check"] == FAIL
    assert result["pe_fault_clearing_time_check"] == PASS
    assert result["fault_clearing_time_check"] == FAIL


def test_cm3_10_to_25a_uses_fixed_300a_and_its_exact_c15_c16_curve():
    result = evaluate_cm3_motor_reference(
        motor_rated_current_a=21,
        motor_starting_current_a=168,
        conductor_corrected_ampacity_a=40,
        required_icu_ka=14,
        terminal_minimum_fault_current_a=900,
        phase_maximum_clearing_time_s=0.2,
        pe_maximum_clearing_time_s=1.0,
    )

    assert result["rated_current_a"] == 25
    assert result["short_circuit_pickup_rule"] == "fixed"
    assert result["short_circuit_pickup_nominal_a"] == 300
    assert result["curve_applicable_to_selected_rating"] is True
    assert result["curve_id"] == "in_25a"
    assert result["phase_fault_curve_bounds"] is not None
    assert result["pe_fault_curve_bounds"] is not None
    assert result["fault_clearing_time_check"] in {PASS, FAIL}


def test_cm3_curve_uses_actual_starting_time_without_guessing():
    common = dict(
        motor_rated_current_a=57.97,
        motor_starting_current_a=423.2,
        conductor_corrected_ampacity_a=84,
        required_icu_ka=14,
        terminal_minimum_fault_current_a=762,
        phase_maximum_clearing_time_s=0.027,
        pe_maximum_clearing_time_s=3.5,
    )
    short_start = evaluate_cm3_motor_reference(
        **common, motor_starting_time_s=0.2
    )
    long_start = evaluate_cm3_motor_reference(
        **common, motor_starting_time_s=5
    )

    assert short_start["motor_starting_time_check"] == PASS
    assert short_start["provisional_status"] == UNKNOWN
    assert long_start["motor_starting_time_check"] == FAIL
    assert long_start["provisional_status"] == FAIL
