from math import sqrt

import pytest

from src.electrical_calc.catalog import DEFAULT_CATALOG
from src.electrical_calc.engine import PASS, UNKNOWN
from src.electrical_calc.simple_engine import (
    _maximum_voltage_drop_power_factor,
    calculate_simple_load_selection,
)


def data(**changes):
    base = {
        "input_basis": "kw",
        "input_value": 30,
        "phase": "3",
        "voltage_v": 380,
        "load_type_code": "led_over_5w",
        "conductor_family": "BV",
        "installation_scenario": "conduit",
    }
    base.update(changes)
    return base


def test_kw_uses_power_factor_lower_bound_but_not_efficiency():
    result = calculate_simple_load_selection({**data(), "efficiency": 0.1, "demand_factor": 0.1}, {}, DEFAULT_CATALOG)
    assert result.outputs["design_current_a"] == pytest.approx(30_000 / (sqrt(3) * 380 * 0.9), abs=1e-4)
    assert result.outputs["parameter"]["conservative"] is True
    assert result.outputs["parameter"]["adopted_power_factor"] == 0.9
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN


@pytest.mark.parametrize(
    ("basis", "phase", "voltage", "value", "expected"),
    [
        ("kw", "1", 220, 5, 5000 / 220),
        ("kva", "1", 220, 10, 10_000 / 220),
        ("kva", "3", 380, 30, 30_000 / (sqrt(3) * 380)),
        ("current", "1", 230, 41, 41),
        ("current", "3", 400, 63, 63),
    ],
)
def test_input_modes(basis, phase, voltage, value, expected):
    load_code = "electric_heater" if basis == "kw" else ""
    result = calculate_simple_load_selection(
        data(input_basis=basis, input_value=value, phase=phase, voltage_v=voltage, load_type_code=load_code),
        {},
    )
    assert result.outputs["design_current_a"] == pytest.approx(expected, abs=1e-4)


def test_unknown_kw_does_not_guess_power_factor_but_kva_ignores_it():
    unknown = calculate_simple_load_selection(data(load_type_code="unknown"), {})
    assert unknown.status == UNKNOWN
    assert "必须填写铭牌或厂家功率因数" in unknown.warnings[0]
    kva = calculate_simple_load_selection(data(input_basis="kva", load_type_code="unknown"), {})
    assert kva.outputs["design_current_a"] > 0
    assert "parameter" not in kva.outputs


def test_user_power_factor_allows_unknown_kw_and_overrides_catalog_value():
    unknown = calculate_simple_load_selection(
        data(load_type_code="unknown", power_factor=0.82),
        {},
    )
    assert unknown.outputs["design_current_a"] == pytest.approx(
        30_000 / (sqrt(3) * 380 * 0.82), abs=1e-4
    )
    assert unknown.outputs["parameter"]["adopted_source"].startswith("用户输入")
    assert unknown.outputs["parameter"]["power_factor_min"] is None

    override = calculate_simple_load_selection(
        data(load_type_code="led_over_5w", power_factor=0.95),
        {},
    )
    assert override.outputs["design_current_a"] == pytest.approx(
        30_000 / (sqrt(3) * 380 * 0.95), abs=1e-4
    )
    assert override.outputs["parameter"]["adopted_power_factor"] == 0.95
    assert override.outputs["parameter"]["conservative"] is False


@pytest.mark.parametrize("power_factor", [0, -0.1, 1.01, "not-a-number"])
def test_user_power_factor_is_validated(power_factor):
    result = calculate_simple_load_selection(
        data(load_type_code="unknown", power_factor=power_factor),
        {},
    )
    assert result.status == UNKNOWN
    assert "design_current_a" not in result.outputs
    assert any("功率因数" in warning for warning in result.warnings)


def test_bv_cannot_be_direct_buried():
    result = calculate_simple_load_selection(data(installation_scenario="direct_buried"), {})
    assert result.status == UNKNOWN
    assert "敷设场景" in result.warnings[0]


def test_verified_tables_return_provisional_section_without_inferring_breaker_poles():
    result = calculate_simple_load_selection(data(input_basis="current", input_value=41), {})
    assert result.outputs["required_breaker_rating_a"] == 41
    assert result.outputs["provisional_breaker_rating_a"] == 50
    assert result.outputs["required_cable_ampacity_a"] == 50
    assert "breaker_candidate_rating_a" not in result.outputs
    assert result.outputs["breaker_design_candidates"][0]["family_code"] == "MCB"
    assert result.outputs["breaker_design_candidates"][0]["frame_label"] == "电流规格等级"
    assert result.outputs["breaker_design_candidates"][0]["frame_rating_a"] == 63
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 10
    assert len(result.outputs["cable_candidates"]) == 1
    assert result.outputs["cable_candidates"][0]["status"] == "基础初选"
    assert result.outputs["core_configuration"] == "3根单芯线"
    assert result.outputs["breaker_poles"] == "待按接地系统与保护要求确定"
    assert result.outputs["breaker_design_candidates"][0]["poles"] == "待按接地系统与保护要求确定"


def test_approved_catalog_selects_breaker_before_conductor():
    catalog = {
        **DEFAULT_CATALOG,
        "breaker_ratings": {"status": "verified", "ratings_a": [16, 25, 40, 50, 63]},
        "conductors": {
            **DEFAULT_CATALOG["conductors"],
            "BV": {
                "status": "verified",
                "source": "测试来源", "table": "测试表", "page": "测试页",
                "reference_condition": "测试条件",
                "scenarios": {"conduit": {"bv_loaded_3": {
                    "label": "3根载流导线",
                    "rows": [
                        {"section_mm2": 6, "ampacity_a": 42},
                        {"section_mm2": 10, "ampacity_a": 57},
                    ],
                }}},
            },
        },
    }
    rules = {
        "ELEC.LOAD.CURRENT": {"status": "approved"},
        "ELEC.BREAKER.RATING": {"status": "approved"},
        "ELEC.CABLE.BV.AMPACITY": {"status": "approved"},
    }
    result = calculate_simple_load_selection(data(input_basis="current", input_value=41), rules, catalog)
    assert result.outputs["breaker_candidate_rating_a"] == 50
    assert result.outputs["required_cable_ampacity_a"] == 50
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 10
    assert result.status == UNKNOWN
    assert "短路电流与分断能力" in result.outputs["incomplete_checks"]

def test_conductor_catalog_uses_verified_19dx_values():
    bv = DEFAULT_CATALOG["conductors"]["BV"]["scenarios"]["conduit"]["bv_loaded_3"]["rows"]
    yjv = DEFAULT_CATALOG["conductors"]["YJV"]["scenarios"]["conduit"]["yjv_three_core_exposed_conduit"]["rows"]
    assert next(row for row in bv if row["section_mm2"] == 10)["ampacity_a"] == 50
    assert next(row for row in yjv if row["section_mm2"] == 10)["ampacity_a"] == 60


def test_conductor_basis_is_automatically_matched_from_phase_and_family():
    catalog = {
        **DEFAULT_CATALOG,
        "breaker_ratings": {"status": "verified", "ratings_a": [50, 63]},
    }
    rules = {
        "ELEC.BREAKER.RATING": {"status": "approved"},
        "ELEC.CABLE.BV.AMPACITY": {"status": "approved"},
    }
    result = calculate_simple_load_selection(
        data(input_basis="current", input_value=41), rules, catalog,
    )
    assert result.outputs["breaker_candidate_rating_a"] == 50
    assert result.outputs["conductor_basis"]["code"] == "bv_loaded_3"
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 10
    assert result.outputs["core_configuration"] == "3根单芯线"


def test_group_load_allows_single_phase_equipment_on_balanced_three_phase_circuit():
    result = calculate_simple_load_selection(
        data(
            circuit_role="group_load",
            input_value=1000,
            load_type_code="fan_coil",
            phase="3",
            voltage_v=380,
        ),
        {},
    )

    assert result.outputs["design_current_a"] == pytest.approx(
        1_000_000 / (sqrt(3) * 380 * 0.8), abs=1e-4
    )
    assert "三相均衡汇总" in result.outputs["phase_application"]
    assert any("各相负荷分配" in warning for warning in result.warnings)


def test_installed_power_requires_user_demand_factor():
    missing = calculate_simple_load_selection(
        data(circuit_role="feeder", power_definition="installed", demand_factor=""),
        {},
    )
    assert "design_current_a" not in missing.outputs
    assert any("必须填写需要系数" in warning for warning in missing.warnings)

    calculated = calculate_simple_load_selection(
        data(
            circuit_role="feeder",
            input_value=100,
            power_definition="installed",
            demand_factor=0.6,
        ),
        {},
    )
    assert calculated.outputs["calculation_power_kw"] == 60
    assert calculated.outputs["design_current_a"] == pytest.approx(
        60_000 / (sqrt(3) * 380 * 0.9), abs=1e-4
    )
    assert calculated.steps[0].label == "计算功率"


def test_single_device_never_reduces_input_power_with_demand_factor():
    result = calculate_simple_load_selection(
        data(
            circuit_role="single_device",
            input_value=30,
            power_definition="installed",
            demand_factor=0.5,
        ),
        {},
    )

    assert result.outputs["calculation_power_kw"] == 30
    assert result.outputs["design_current_a"] == pytest.approx(
        30_000 / (sqrt(3) * 380 * 0.9), abs=1e-4
    )
    assert any("不采用需要系数" in warning for warning in result.warnings)


def test_voltage_drop_only_needs_length_and_uses_table_parameters():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            power_factor=0.8,
            length_m=100,
            load_type_code="electric_heater",
        ),
        {},
    )
    expected_v = sqrt(3) * 50 * (2.040 * 0.8 + 0.108 * 0.6) * 100 / 1000
    assert result.outputs["voltage_drop"]["voltage_drop_v"] == pytest.approx(expected_v, abs=1e-4)
    assert result.outputs["voltage_drop"]["selected_section_mm2"] == 10
    assert result.outputs["voltage_drop"]["resistance_ohm_per_km"] == 2.040
    assert result.outputs["voltage_drop"]["parameter_source"]["table"] == "表3.23"
    assert result.outputs["voltage_drop"]["limit_pct"] == 5
    assert result.outputs["voltage_drop"]["limit_source"]["table"] == "表6.2-6"
    assert result.outputs["voltage_drop"]["provisional_status"] == PASS
    assert result.outputs["voltage_drop"]["status"] == UNKNOWN
    assert next(
        stage for stage in result.outputs["workflow_stages"] if stage["code"] == "voltage_drop"
    )["state"] == "completed"


@pytest.mark.parametrize(
    ("resistance", "reactance", "expected"),
    [
        (0.398, 0.091, 0.398 / sqrt(0.398**2 + 0.091**2)),
        (0.01, 1.0, 0.5),
        (1.0, 0.0, 1.0),
    ],
)
def test_unknown_power_factor_uses_exact_voltage_drop_extreme(
    resistance, reactance, expected
):
    assert _maximum_voltage_drop_power_factor(resistance, reactance) == pytest.approx(
        expected
    )


def test_unknown_power_factor_voltage_drop_is_not_underestimated_by_discrete_grid():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=120,
            power_factor="",
            length_m=50,
            load_type_code="electric_heater",
        ),
        {},
    )

    voltage_drop = result.outputs["voltage_drop"]
    resistance = voltage_drop["resistance_ohm_per_km"]
    reactance = voltage_drop["reactance_ohm_per_km"]
    adopted_pf = voltage_drop["adopted_power_factor"]
    exact_pf = resistance / sqrt(resistance**2 + reactance**2)
    expected_drop = (
        sqrt(3)
        * 120
        * (resistance * exact_pf + reactance * sqrt(1 - exact_pf**2))
        * 50
        / 1000
    )

    assert adopted_pf == pytest.approx(exact_pf)
    assert voltage_drop["voltage_drop_v"] == pytest.approx(expected_drop, abs=1e-4)
    assert "解析极值" in voltage_drop["power_factor_source"]


def test_lighting_voltage_drop_uses_table_range_lower_bound_and_increases_section():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            power_factor=0.8,
            length_m=100,
            voltage_drop_limit_pct=99,
        ),
        {},
    )

    voltage_drop = result.outputs["voltage_drop"]
    assert voltage_drop["limit_pct"] == 3
    assert voltage_drop["limit_source"]["table_value"] == "3～5"
    assert voltage_drop["provisional_status"] == PASS
    assert voltage_drop["selected_section_mm2"] == 16
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 16
    assert voltage_drop["status"] == UNKNOWN


def test_yjv_buried_duct_voltage_drop_uses_table_3_21_impedance():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            power_factor=0.8,
            load_type_code="electric_heater",
            conductor_family="YJV",
            conductor_configuration="yjv_3c_3ph_pe",
            installation_scenario="direct_buried",
            soil_thermal_resistivity_k_m_per_w=2.0,
            installation_temperature_c=20,
            buried_circuit_count=1,
            length_m=100,
        ),
        {},
    )

    voltage_drop = result.outputs["voltage_drop"]
    expected_v = sqrt(3) * 50 * (2.175 * 0.8 + 0.085 * 0.6) * 100 / 1000
    assert voltage_drop["calculated"] is True
    assert voltage_drop["selected_section_mm2"] == 10
    assert voltage_drop["voltage_drop_v"] == pytest.approx(expected_v, abs=1e-4)
    assert voltage_drop["parameter_source"]["table"] == "表3.21"
    assert voltage_drop["parameter_source"]["page"] == "PDF第27页"
    assert "平台适用映射（非表格原文）" in voltage_drop["parameter_source"]["application_note"]
    assert voltage_drop["provisional_status"] == PASS
    assert voltage_drop["status"] == UNKNOWN


def test_quick_page_uses_mcb_and_distribution_mccb_parameters_for_icu():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=41,
            prospective_short_circuit_ka=5,
        ),
        {},
    )
    mcb = next(
        item for item in result.outputs["breaker_design_candidates"]
        if item["family_code"] == "MCB"
    )
    assert mcb["rated_current_a"] == 50
    assert mcb["rated_voltage_v"] == "230/400"
    assert mcb["frame_rating_a"] == 63
    assert mcb["frame_label"] == "电流规格等级"
    assert mcb["page"] == "PDF第79页"
    assert mcb["selected_icu_ka"] == 6
    assert [item["family_code"] for item in result.outputs["breaker_design_candidates"]] == ["MCB", "MCCB"]
    mccb = next(
        item for item in result.outputs["breaker_design_candidates"]
        if item["family_code"] == "MCCB"
    )
    assert mccb["rated_current_a"] == 50
    assert mccb["frame_label"] == "壳架电流"


def test_transformer_lv_outlet_short_circuit_uses_verified_table_value():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=100,
            short_circuit_method="transformer_lv_table",
            transformer_capacity_kva=1000,
            transformer_uk_percent=6,
        ),
        {},
    )

    short_circuit = result.outputs["short_circuit_estimate"]
    assert short_circuit["mode"] == "exact_table"
    assert short_circuit["ik_ka"] == 24
    assert short_circuit["ip_ka"] == 61.2
    assert short_circuit["table"] == "式(15.9)、表15.7"
    assert any(
        item["required_icu_ka"] == 24
        for item in result.outputs["breaker_design_candidates"]
    )


def test_transformer_capacity_only_returns_table_range_and_uses_upper_bound():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=100,
            short_circuit_method="transformer_lv_table",
            transformer_capacity_kva=800,
            transformer_uk_percent="",
        ),
        {},
    )

    short_circuit = result.outputs["short_circuit_estimate"]
    assert short_circuit["mode"] == "range"
    assert short_circuit["available_uk_percent"] == [6.0, 7.0, 8.0]
    assert short_circuit["ik_min_ka"] == 14.4
    assert short_circuit["ik_max_ka"] == 19.2
    assert short_circuit["adopted_for_breaking_capacity_ka"] == 19.2
    assert any("铭牌uk%" in warning for warning in result.warnings)


def test_rcd_value_requires_application_scenario():
    unknown = calculate_simple_load_selection(
        data(input_basis="current", input_value=16),
        {},
    )
    assert unknown.outputs["rcd_requirement"]["rated_residual_current_max_ma"] is None

    additional = calculate_simple_load_selection(
        data(input_basis="current", input_value=16, rcd_scenario="additional_30ma"),
        {},
    )
    assert additional.outputs["rcd_requirement"]["rated_residual_current_max_ma"] == 30
    assert additional.outputs["rcd_requirement"]["delay"] == "无延时"
    assert additional.outputs["rcd_requirement"]["rcd_type"] == "待按负载电流波形确认"
    assert "额定剩余动作电流应大于正常泄漏电流的2倍" in additional.outputs["rcd_requirement"]["selection_checks"][2]
    assert any("不猜测AC/A/F/B型" in warning for warning in additional.warnings)


def test_rcd_waveform_selects_type_from_verified_table_without_product_selection():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=16,
            rcd_scenario="additional_30ma",
            rcd_residual_waveform="pulsating_dc",
        ),
        {},
    )
    requirement = result.outputs["rcd_requirement"]
    assert requirement["rcd_type"] == "A型或F型"
    assert requirement["residual_waveform"] == "交流及脉动直流剩余电流"


def test_four_core_uses_verified_multicore_table_without_determining_breaker_poles():
    four_core = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_4c_3ph_n_pe",
            installation_scenario="tray",
            tray_type="horizontal_perforated",
            tray_layers="1",
            tray_cables_per_layer="1",
        ),
        {},
    )
    assert four_core.outputs["breaker_poles"] == "待按接地系统与保护要求确定"
    assert all(
        item["poles"] == "待按接地系统与保护要求确定"
        for item in four_core.outputs["breaker_design_candidates"]
    )
    assert all(item["family_code"] != "ACB" for item in four_core.outputs["breaker_design_candidates"])
    assert four_core.outputs["conductor_basis"]["table"] == "表31"
    assert four_core.outputs["conductor_basis"]["source"] == "人民电器《电线电缆选型手册》"
    candidate = four_core.outputs["cable_candidates"][0]
    assert candidate["section_mm2"] == 10
    assert candidate["fault_loop_structure"]["protective_section_mm2"] == 6
    assert "ELEC.CABLE.YJV.MULTICORE.AMPACITY" in four_core.rule_codes
    assert "ELEC.CABLE.YJV.STRUCTURE" in four_core.rule_codes


def test_five_core_direct_buried_uses_multicore_ground_table():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_5c_3ph_n_pe",
            installation_scenario="direct_buried",
        ),
        {},
    )
    assert result.outputs["conductor_basis"]["table"] == "表31"
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 4
    assert result.outputs["cable_candidates"][0]["base_ampacity_a"] == 53


def test_three_core_buried_duct_requires_soil_thermal_resistivity():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_3c_3ph_pe",
            installation_scenario="direct_buried",
        ),
        {},
    )

    assert result.outputs["cable_candidates"] == []
    assert any("必须选择土壤热阻系数" in warning for warning in result.warnings)


def test_three_core_buried_duct_uses_selected_soil_thermal_resistivity_column():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_3c_3ph_pe",
            installation_scenario="direct_buried",
            soil_thermal_resistivity_k_m_per_w=2.0,
            installation_temperature_c=20,
        ),
        {},
    )

    basis = result.outputs["conductor_basis"]
    candidate = result.outputs["cable_candidates"][0]
    assert basis["table"] == "表6.10"
    assert basis["page"] == "PDF第93页"
    assert "埋地管槽" in basis["label"]
    assert "2 K·m/W" in basis["label"]
    assert candidate["section_mm2"] == 10
    assert candidate["base_ampacity_a"] == 60
    assert result.outputs["temperature_correction"]["base_temperature_c"] == 20
    assert candidate["corrected_ampacity_a"] == 60


def test_three_core_buried_duct_all_table_6_10_copper_columns_are_preserved():
    underground = DEFAULT_CATALOG["conductors"]["YJV"]["scenarios"]["direct_buried"]
    expected = {
        1.0: [24, 33, 42, 51, 68, 88, 113, 135, 159, 197, 232, 263, 296, 331, 382, 430],
        1.5: [23, 30, 39, 48, 63, 82, 105, 126, 148, 183, 216, 245, 276, 309, 356, 401],
        2.0: [22, 29, 37, 46, 60, 78, 100, 120, 141, 175, 206, 234, 263, 295, 340, 383],
        2.5: [21, 28, 36, 44, 58, 75, 96, 115, 135, 167, 197, 223, 251, 281, 324, 365],
    }

    for resistivity, ampacities in expected.items():
        rows = underground[
            f"yjv_three_core_buried_duct_soil_{resistivity:g}"
        ]["rows"]
        assert [row["ampacity_a"] for row in rows] == ampacities


def test_buried_duct_table_6_27_grouping_changes_selected_section():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_3c_3ph_pe",
            installation_scenario="direct_buried",
            soil_thermal_resistivity_k_m_per_w=2.5,
            installation_temperature_c=20,
            buried_circuit_count=4,
            buried_duct_spacing_m="0.25",
            buried_depth_m=0.7,
        ),
        {},
    )

    grouping = result.outputs["buried_grouping"]
    candidate = result.outputs["cable_candidates"][0]
    assert grouping["table"] == "表6.27"
    assert grouping["derating_factor"] == 0.8
    assert candidate["section_mm2"] == 16
    assert candidate["base_ampacity_a"] == 75
    assert candidate["corrected_ampacity_a"] == 60
    assert "成组敷设修正" not in result.outputs["incomplete_checks"]


def test_buried_duct_grouping_does_not_apply_table_6_27_outside_reference_conditions():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_3c_3ph_pe",
            installation_scenario="direct_buried",
            soil_thermal_resistivity_k_m_per_w=2.0,
            installation_temperature_c=20,
            buried_circuit_count=4,
            buried_duct_spacing_m="0.25",
            buried_depth_m=0.7,
        ),
        {},
    )

    assert "buried_grouping" not in result.outputs
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 10
    assert "成组敷设修正" in result.outputs["incomplete_checks"]
    assert any("不能直接套用该表" in warning for warning in result.warnings)


def test_single_buried_duct_circuit_needs_no_table_6_27_factor():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_3c_3ph_pe",
            installation_scenario="direct_buried",
            soil_thermal_resistivity_k_m_per_w=2.0,
            installation_temperature_c=20,
            buried_circuit_count=1,
        ),
        {},
    )

    assert result.outputs["buried_grouping"]["derating_factor"] == 1.0
    assert "成组敷设修正" not in result.outputs["incomplete_checks"]


def test_four_core_conduit_remains_unsupported_without_matching_table():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_4c_3ph_n_pe",
            installation_scenario="conduit",
        ),
        {},
    )
    assert result.outputs["cable_candidates"] == []
    assert any("没有覆盖" in warning for warning in result.warnings)


def test_horizontal_perforated_tray_derating_changes_selected_section():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=50,
            conductor_family="YJV",
            conductor_configuration="yjv_3c_3ph_pe",
            installation_scenario="tray",
            tray_type="horizontal_perforated",
            tray_layers="1",
            tray_cables_per_layer="3",
        ),
        {},
    )
    assert result.outputs["tray_configuration"]["derating_factor"] == 0.82
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 10
    assert result.outputs["cable_candidates"][0]["corrected_ampacity_a"] == pytest.approx(61.5)


def test_bv_air_temperature_derating_changes_selected_section():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=41,
            installation_temperature_c=40,
        ),
        {},
    )

    correction = result.outputs["temperature_correction"]
    candidate = result.outputs["cable_candidates"][0]
    assert correction["table"] == "表6.22"
    assert correction["base_temperature_c"] == 30
    assert correction["relative_factor"] == 0.87
    assert candidate["section_mm2"] == 16
    assert candidate["temperature_corrected_ampacity_a"] == pytest.approx(59.16)
    assert "环境温度修正" not in result.outputs["incomplete_checks"]


def test_conduit_table_6_26_grouping_changes_selected_section():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=40,
            installation_temperature_c=30,
            enclosed_grouping_circuit_count=3,
        ),
        {},
    )

    grouping = result.outputs["enclosed_grouping"]
    candidate = result.outputs["cable_candidates"][0]
    assert grouping["table"] == "表6.26"
    assert grouping["derating_factor"] == 0.7
    assert candidate["section_mm2"] == 16
    assert candidate["base_ampacity_a"] == 68
    assert candidate["corrected_ampacity_a"] == pytest.approx(47.6)
    assert "成组敷设修正" not in result.outputs["incomplete_checks"]


def test_unknown_conduit_grouping_keeps_base_candidate_but_check_incomplete():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=40,
            installation_temperature_c=30,
        ),
        {},
    )

    assert "enclosed_grouping" not in result.outputs
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 10
    assert "成组敷设修正" in result.outputs["incomplete_checks"]
    assert any("未确认同束/封闭通道内回路数" in warning for warning in result.warnings)


def test_yjv_ground_temperature_is_relative_to_source_table_base_temperature():
    result = calculate_simple_load_selection(
        data(
            input_basis="current",
            input_value=70,
            conductor_family="YJV",
            conductor_configuration="yjv_4c_3ph_n_pe",
            installation_scenario="direct_buried",
            installation_temperature_c=40,
        ),
        {},
    )

    correction = result.outputs["temperature_correction"]
    candidate = result.outputs["cable_candidates"][0]
    assert correction["table"] == "表6.24"
    assert correction["base_temperature_c"] == 25
    assert correction["table_factor_at_actual_temperature"] == 0.85
    assert correction["table_factor_at_base_temperature"] == 0.96
    assert correction["relative_factor"] == pytest.approx(0.8854)
    assert candidate["section_mm2"] == 16
    assert candidate["corrected_ampacity_a"] == pytest.approx(103.5938)


def test_unknown_temperature_keeps_base_candidate_and_incomplete_check():
    result = calculate_simple_load_selection(
        data(input_basis="current", input_value=41),
        {},
    )

    assert "temperature_correction" not in result.outputs
    assert result.outputs["cable_candidates"][0]["section_mm2"] == 10
    assert "环境温度修正" in result.outputs["incomplete_checks"]
    assert any("未确认实际敷设环境温度" in warning for warning in result.warnings)
