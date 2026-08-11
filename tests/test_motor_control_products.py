from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.motor_control_products import (
    load_motor_control_catalog,
    load_siemens_ie3_starter_catalog,
    select_motor_control_references,
)


def test_nxc_nxr_catalog_is_traceable_and_non_formal():
    catalog = load_motor_control_catalog()

    assert catalog["status"] == "verified"
    assert catalog["formal_calculation_allowed"] is False
    assert catalog["contactor_source"]["selection_reference"] == "PDF第3页，003"
    assert catalog["overload_source"]["coordination_reference"] == "PDF第3页，069"
    siemens = load_siemens_ie3_starter_catalog()
    assert siemens["conditions"]["coordination_type"] == 2
    assert siemens["conditions"]["iq_ka"] == 100
    assert siemens["conditions"]["motor_efficiency_classes"] == ["IE3", "IE4"]


def test_30kw_motor_selects_nxc65_and_all_matching_nxr100_ranges():
    result = select_motor_control_references(
        motor_rated_current_a=57.97,
        motor_starting_current_a=423.2,
        motor_rated_output_power_kw=30,
        system_voltage_v=380,
        motor_efficiency_class="IE3",
    )

    assert result["contactor_candidate"]["model"] == "NXC-65"
    assert result["contactor_candidate"]["rated_current_a"] == 65
    assert result["contactor_candidate"]["motor_power_kw"] == 30
    assert [
        (item["setting_min_a"], item["setting_max_a"])
        for item in result["overload_relay_candidates"]
    ] == [(55, 70), (48, 65)]
    assert all(item["frame"] == "NXR-100" for item in result["overload_relay_candidates"])
    assert result["recommended_overload_relay"]["setting_min_a"] == 48
    assert result["recommended_overload_relay"]["setting_max_a"] == 65
    assert result["manufacturer_pairing_status"] == PASS
    assert result["type_2_coordination_status"] == UNKNOWN
    assert result["type_2_coordination_provisional_status"] == "不适用"
    assert "只标明IE1/IE2" in result["type_2_coordination_applicability"]
    assert result["type_2_coordination_devices"]["fuse_option"] == "gG 100A 或 aM 63A"
    assert result["type_2_coordination_devices"]["mccb_option"] == "NXM-63H系列 / In 63A"
    assert result["integrated_mpcb_candidate"]["mpcb_model"] == "NS2-80/65"
    assert result["integrated_mpcb_candidate"]["setting_min_a"] == 48
    assert result["integrated_mpcb_candidate"]["setting_max_a"] == 65
    assert result["integrated_mpcb_candidate"]["instantaneous_pickup_a"] == 910
    assert result["integrated_mpcb_candidate"]["contactor"] == "NC8-80"
    assert result["integrated_mpcb_provisional_status"] == "不适用"
    assert result["ns2_standalone_candidate"]["model"] == "NS2-80/65"
    assert result["ns2_standalone_candidate"]["overload_setting_target_a"] == 57.97
    assert result["ns2_standalone_candidate"]["instantaneous_no_trip_boundary_a"] == 728
    assert result["ns2_standalone_candidate"]["instantaneous_trip_boundary_a"] == 1092
    assert result["ns2_standalone_candidate"]["starting_instantaneous_status"] == PASS
    assert result["ns2_standalone_candidate"]["system_voltage_status"] == PASS
    assert result["ns2_standalone_provisional_status"] == UNKNOWN
    assert "不代表NS2＋NC8的2类配合" in result["ns2_standalone_applicability"]
    assert [item["code"] for item in result["protection_architectures"]] == [
        "integrated_motor_mccb",
        "separate_overload_relay",
    ]
    assert "不配置独立NXR" in result["protection_architectures"][0]["overload_protection"]
    assert "当前电动机为IE3" in result["protection_architectures"][1]["unresolved"]
    assert result["provisional_status"] == UNKNOWN


def test_type_2_table_matches_ie2_when_fault_level_is_within_50ka():
    result = select_motor_control_references(
        motor_rated_current_a=55,
        motor_starting_current_a=385,
        motor_rated_output_power_kw=30,
        system_voltage_v=400,
        motor_efficiency_class="IE2",
        installation_point_max_short_circuit_ka=49,
    )

    assert result["type_2_coordination_status"] == UNKNOWN
    assert result["type_2_coordination_provisional_status"] == PASS
    assert "49.000kA" in result["type_2_coordination_applicability"]


def test_type_2_table_rejects_ie2_when_fault_level_exceeds_50ka():
    result = select_motor_control_references(
        motor_rated_current_a=55,
        motor_starting_current_a=385,
        motor_rated_output_power_kw=30,
        system_voltage_v=400,
        motor_efficiency_class="IE2",
        installation_point_max_short_circuit_ka=50.001,
    )

    assert result["type_2_coordination_provisional_status"] == FAIL
    assert "超过表列Iq 50kA" in result["type_2_coordination_applicability"]


def test_integrated_mpcb_type_2_candidate_can_set_to_ie2_motor_current():
    result = select_motor_control_references(
        motor_rated_current_a=55,
        motor_starting_current_a=385,
        motor_rated_output_power_kw=30,
        system_voltage_v=380,
        motor_efficiency_class="IE2",
        installation_point_max_short_circuit_ka=20,
    )

    assert result["integrated_mpcb_formal_status"] == UNKNOWN
    assert result["integrated_mpcb_provisional_status"] == PASS
    assert "可整定至55.000A" in result["integrated_mpcb_applicability"]


def test_ns2_standalone_exact_product_points_are_separate_from_type_2_ie_class():
    result = select_motor_control_references(
        motor_rated_current_a=55,
        motor_starting_current_a=55 * 7.2,
        motor_rated_output_power_kw=30,
        system_voltage_v=400,
        motor_starting_time_s=2,
        motor_efficiency_class="IE3",
        installation_point_max_short_circuit_ka=17,
    )

    candidate = result["ns2_standalone_candidate"]
    assert candidate["overload_setting_range_status"] == PASS
    assert candidate["system_voltage_status"] == PASS
    assert candidate["starting_instantaneous_status"] == PASS
    assert candidate["class10_cold_starting_time_status"] == PASS
    assert candidate["standalone_icu_status"] == PASS
    assert candidate["standalone_ics_status"] == PASS
    assert result["ns2_standalone_provisional_status"] == PASS
    assert result["integrated_mpcb_provisional_status"] == "不适用"


def test_ns2_instantaneous_transition_band_is_not_treated_as_exact_curve():
    result = select_motor_control_references(
        motor_rated_current_a=55,
        motor_starting_current_a=900,
        motor_rated_output_power_kw=30,
        system_voltage_v=400,
        motor_efficiency_class="IE3",
        installation_point_max_short_circuit_ka=17,
    )

    candidate = result["ns2_standalone_candidate"]
    assert candidate["starting_instantaneous_status"] == UNKNOWN
    assert result["ns2_standalone_provisional_status"] == UNKNOWN


def test_ns2_terminal_fault_uses_guaranteed_band_but_not_trip_time_as_total_clearing():
    common = dict(
        motor_rated_current_a=55,
        motor_starting_current_a=396,
        motor_rated_output_power_kw=30,
        system_voltage_v=400,
        motor_starting_time_s=2,
        motor_efficiency_class="IE3",
        installation_point_max_short_circuit_ka=17,
        phase_maximum_clearing_time_s=0.1,
        pe_maximum_clearing_time_s=0.2,
    )
    below = select_motor_control_references(
        **common, terminal_minimum_fault_current_a=728
    )["ns2_standalone_candidate"]
    transition = select_motor_control_references(
        **common, terminal_minimum_fault_current_a=900
    )["ns2_standalone_candidate"]
    guaranteed = select_motor_control_references(
        **common, terminal_minimum_fault_current_a=1092
    )["ns2_standalone_candidate"]

    assert below["terminal_guaranteed_instantaneous_status"] == FAIL
    assert transition["terminal_guaranteed_instantaneous_status"] == UNKNOWN
    assert guaranteed["terminal_guaranteed_instantaneous_status"] == PASS
    assert guaranteed["governing_maximum_clearing_time_s"] == 0.1
    assert guaranteed["instantaneous_trip_test_time_upper_bound_s"] == 0.2
    assert guaranteed["total_clearing_time_status"] == UNKNOWN
    assert guaranteed["time_current_curve_reference"]["digitized_for_decision"] is False
    assert guaranteed["time_current_curve_reference"]["let_through_i2t_available"] is False


def test_siemens_ie3_type2_treats_380v_and_400v_as_same_application_band():
    common = dict(
        motor_rated_current_a=55,
        motor_starting_current_a=401.5,
        motor_rated_output_power_kw=30,
        motor_efficiency_class="IE3",
    )
    at_380v = select_motor_control_references(
        **common, system_voltage_v=380, installation_point_max_short_circuit_ka=20
    )
    at_400v = select_motor_control_references(
        **common, system_voltage_v=400, installation_point_max_short_circuit_ka=20
    )

    assert len(at_380v["siemens_ie3_type2_candidates"]) == 2
    assert at_380v["siemens_ie3_type2_provisional_status"] == PASS
    assert "380/400V同一应用电压档" in at_380v["siemens_ie3_type2_applicability"]
    assert at_400v["siemens_ie3_type2_provisional_status"] == PASS
    assert "不超过表列Iq 100kA" in at_400v["siemens_ie3_type2_applicability"]
    assert "不超过第7章脚注的720A" in at_400v["siemens_ie3_type2_applicability"]
    candidate = at_400v["siemens_ie3_type2_candidates"][0]
    assert candidate["trip_class"] == 10
    assert candidate["instantaneous_release_a"] == 845
    assert candidate["standalone_icu_ka_at_400v"] == 65
    assert candidate["starting_current_limit_status"] == PASS
    assert candidate["instantaneous_nominal_ride_through_status"] == PASS
    assert candidate["standalone_icu_status"] == PASS
    assert candidate["actual_starting_multiple_of_setting"] == 7.3
    assert candidate["class10_cold_reference_multiple"] == 7.2
    assert candidate["class10_cold_minimum_trip_time_s"] == 4
    assert candidate["class10_cold_maximum_trip_time_s"] == 10
    assert candidate["class10_cold_starting_time_status"] == UNKNOWN
    assert candidate["trip_time_maximum_deviation_percent_at_or_above_3x"] == 20
    assert candidate["exact_product_curve_available"] is False


def test_siemens_ie3_s2_combination_rejects_starting_current_above_720a():
    result = select_motor_control_references(
        motor_rated_current_a=55,
        motor_starting_current_a=721,
        motor_rated_output_power_kw=30,
        motor_efficiency_class="IE3",
        system_voltage_v=400,
        installation_point_max_short_circuit_ka=20,
    )

    assert result["siemens_ie3_type2_provisional_status"] == FAIL
    assert "超过原表脚注" in result["siemens_ie3_type2_applicability"]


def test_siemens_class10_cold_7_2x_point_is_not_extrapolated():
    common = dict(
        motor_rated_current_a=55,
        motor_rated_output_power_kw=30,
        motor_efficiency_class="IE3",
        system_voltage_v=400,
        installation_point_max_short_circuit_ka=20,
    )
    exact_short_start = select_motor_control_references(
        **common,
        motor_starting_current_a=55 * 7.2,
        motor_starting_time_s=4,
    )["siemens_ie3_type2_candidates"][0]
    exact_long_start = select_motor_control_references(
        **common,
        motor_starting_current_a=55 * 7.2,
        motor_starting_time_s=10.1,
    )["siemens_ie3_type2_candidates"][0]
    nearby_multiple = select_motor_control_references(
        **common,
        motor_starting_current_a=55 * 7.3,
        motor_starting_time_s=1,
    )["siemens_ie3_type2_candidates"][0]

    assert exact_short_start["class10_cold_starting_time_status"] == PASS
    assert exact_long_start["class10_cold_starting_time_status"] == FAIL
    assert nearby_multiple["class10_cold_starting_time_status"] == UNKNOWN


def test_nxr_7_2in_table_is_not_interpolated_for_7_3in_start():
    result = select_motor_control_references(
        motor_rated_current_a=58,
        motor_starting_current_a=58 * 7.3,
        motor_rated_output_power_kw=30,
        system_voltage_v=380,
        motor_starting_time_s=1,
    )

    assert result["overload_starting_time_check"] == UNKNOWN


def test_nxr_marks_long_start_as_failed_when_current_is_at_least_7_2in():
    result = select_motor_control_references(
        motor_rated_current_a=58,
        motor_starting_current_a=58 * 7.2,
        motor_rated_output_power_kw=30,
        system_voltage_v=380,
        motor_starting_time_s=11,
    )

    assert result["overload_starting_time_check"] == FAIL
    assert result["provisional_status"] == FAIL
