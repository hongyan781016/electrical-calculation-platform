from math import log, pi, sqrt

import pytest

from src.electrical_calc.engine import (
    FAIL,
    PASS,
    UNKNOWN,
    calculate_all,
    calculate_cable_fault_loop_impedance,
    calculate_close_proximity_loop_fault_current,
    calculate_pe_thermal_withstand,
    calculate_phase_conductor_thermal_withstand,
    calculate_load_and_selection,
    calculate_quick_selection,
    calculate_short_circuit,
    calculate_tn_earth_fault_protection,
    calculate_tn_fault_loop_chain,
    calculate_transformer_feeder_three_phase_short_circuit,
    calculate_transformer_lv_nameplate_impedance,
    calculate_transformer_phase_pe_impedance,
    calculate_voltage_drop,
)


def test_close_proximity_loop_calculates_conservative_phase_neutral_current():
    result = calculate_close_proximity_loop_fault_current(
        {
            "loop_kind": "phase_neutral",
            "conductor_material": "copper",
            "phase_section_mm2": 2.5,
            "return_section_mm2": 2.5,
            "length_m": 100,
            "nominal_loop_voltage_v": 220,
            "conductors_in_same_cable_or_close": True,
        },
        {},
    )

    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert result.outputs["resistance_only_loop_ohm"] == pytest.approx(1.896)
    assert result.outputs["minimum_fault_current_a"] == pytest.approx(92.827, abs=1e-4)
    assert result.outputs["exact_impedance_chain_completed"] is False


def test_close_proximity_loop_rejects_large_or_separated_conductors():
    result = calculate_close_proximity_loop_fault_current(
        {
            "loop_kind": "phase_pe",
            "conductor_material": "copper",
            "phase_section_mm2": 150,
            "return_section_mm2": 70,
            "length_m": 100,
            "nominal_loop_voltage_v": 220,
            "conductors_in_same_cable_or_close": False,
        },
        {},
    )

    assert result.provisional_status == UNKNOWN
    assert "minimum_fault_current_a" not in result.outputs
    assert any("120mm²" in warning for warning in result.warnings)
    assert any("同一电缆内或彼此靠近" in warning for warning in result.warnings)


def approved_rules():
    return {
        code: {"status": "approved"}
        for code in [
            "ELEC.LOAD.CURRENT",
            "ELEC.CABLE.COORDINATION",
            "ELEC.VDROP",
            "ELEC.SHORT_CIRCUIT",
            "ELEC.BREAKING.CAPACITY",
        ]
    }


def circuit():
    return {
        "phase": "3",
        "voltage_v": 400,
        "installed_power_kw": 30,
        "demand_factor": 0.8,
        "power_factor": 0.9,
        "efficiency": 0.95,
        "length_m": 80,
        "cable_ampacity_a": 80,
        "cable_r_ohm_per_km": 0.727,
        "cable_x_ohm_per_km": 0.08,
        "voltage_drop_limit_pct": 5,
        "breaker_rating_a": 50,
        "breaking_capacity_ka": 10,
        "source_r_ohm": 0.002,
        "source_x_ohm": 0.01,
        "transformer_r_ohm": 0.005,
        "transformer_x_ohm": 0.02,
    }


def test_load_current_and_coordination():
    result = calculate_load_and_selection(circuit(), approved_rules())
    expected = 24_000 / (sqrt(3) * 400 * 0.9 * 0.95)
    assert result.outputs["design_current_a"] == pytest.approx(expected, abs=1e-4)
    assert result.provisional_status == PASS
    assert result.status == PASS


def test_unapproved_rule_blocks_official_status():
    result = calculate_load_and_selection(circuit(), {})
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert any("不能作为正式设计结论" in message for message in result.warnings)


def test_missing_input_does_not_guess():
    data = circuit()
    data["power_factor"] = None
    result = calculate_load_and_selection(data, approved_rules())
    assert result.status == UNKNOWN
    assert result.outputs == {}
    assert "power_factor" in result.warnings[0]


def test_voltage_drop_formula_and_limit():
    result = calculate_voltage_drop(circuit(), approved_rules(), design_current_a=40)
    assert result.outputs["voltage_drop_v"] > 0
    assert result.outputs["voltage_drop_pct"] < 5
    assert result.status == PASS


def test_short_circuit_three_phase_and_breaking_capacity():
    result = calculate_short_circuit(circuit(), approved_rules())
    assert result.outputs["short_circuit_current_ka"] > 0
    assert result.status == PASS


def test_single_phase_short_circuit_is_out_of_scope():
    data = circuit()
    data["phase"] = "1"
    result = calculate_short_circuit(data, approved_rules())
    assert result.status == UNKNOWN
    assert "仅处理三相对称短路" in result.warnings[0]


def transformer_feeder_data(**changes):
    base = {
        "phase": "3",
        "voltage_v": 380,
        "voltage_factor_c": 1.05,
        # 已由上游电源和变压器资料折算到变压器0.4kV出口的等值阻抗；
        # 本模块不从出口Ik幅值反推R/X分量。
        "upstream_r_ohm": 0.003,
        "upstream_x_ohm": 0.020,
        "upstream_impedance_reference": "上游短路计算书第1页",
        "line_type": "cable",
        "conductor_family": "YJV",
        "installation_scenario": "conduit",
        "line_section_mm2": 50,
        "length_m": 100,
        "breaker_installation_point": "line_end",
        "breaker_icu_ka": 6,
    }
    base.update(changes)
    return base


def transformer_nameplate_data(**changes):
    base = {
        "transformer_capacity_kva": 1000,
        "transformer_lv_rated_voltage_v": 400,
        "transformer_uk_percent": 6,
        "transformer_pk_kw": 10,
    }
    base.update(changes)
    return base


def verified_nameplate_impedance_rules():
    return {"ELEC.TRANSFORMER.IMPEDANCE.NAMEPLATE": {"status": "verified"}}


def transformer_phase_pe_data(**changes):
    base = {
        "transformer_series_code": "scb11",
        "transformer_vector_group": "Dyn11",
        "transformer_capacity_kva": 1000,
        "transformer_uk_percent": 6,
        "transformer_hv_voltage_kv": 10,
        "transformer_lv_rated_voltage_v": 400,
        "fault_loop_origin": "transformer_lv_terminal",
    }
    base.update(changes)
    return base


def test_transformer_nameplate_derives_lv_rx_without_assuming_upstream_source():
    result = calculate_transformer_lv_nameplate_impedance(
        transformer_nameplate_data(), verified_nameplate_impedance_rules()
    )

    rated_current = 1_000_000 / (sqrt(3) * 400)
    impedance = 0.06 * 420**2 / 1_000_000
    resistance = 10_000 / (3 * rated_current**2)
    expected_x = sqrt(impedance**2 - resistance**2)
    equivalent = result.outputs["transformer_equivalent"]
    assert equivalent["rated_current_a"] == pytest.approx(rated_current, abs=1e-4)
    assert equivalent["lv_no_load_voltage_v"] == 420
    assert "1.05×Un" in equivalent["lv_no_load_voltage_method"]
    assert equivalent["impedance_magnitude_ohm"] == pytest.approx(impedance, abs=1e-6)
    assert equivalent["resistance_ohm"] == pytest.approx(resistance, abs=1e-6)
    assert equivalent["reactance_ohm"] == pytest.approx(expected_x, abs=1e-6)
    assert "transformer_lv_outlet_ik_ka" not in result.outputs
    assert result.status == UNKNOWN


def test_transformer_nameplate_outlet_ik_requires_explicit_infinite_source_boundary():
    result = calculate_transformer_lv_nameplate_impedance(
        transformer_nameplate_data(source_impedance_mode="infinite_capacity", voltage_factor_c=1.05),
        verified_nameplate_impedance_rules(),
    )

    impedance = 0.06 * 420**2 / 1_000_000
    expected_ik = 1.05 * 400 / (sqrt(3) * impedance) / 1000
    assert result.outputs["transformer_lv_outlet_ik_ka"] == pytest.approx(expected_ik, abs=1e-4)
    assert "无限大" in result.outputs["outlet_ik_boundary"]


def test_transformer_nameplate_uses_provided_no_load_voltage_when_available():
    result = calculate_transformer_lv_nameplate_impedance(
        transformer_nameplate_data(transformer_lv_no_load_voltage_v=410),
        verified_nameplate_impedance_rules(),
    )
    equivalent = result.outputs["transformer_equivalent"]
    assert equivalent["lv_no_load_voltage_v"] == 410
    assert equivalent["impedance_magnitude_ohm"] == pytest.approx(
        0.06 * 410**2 / 1_000_000
    )


def test_transformer_phase_pe_uses_exact_dyn11_table_row():
    rules = {"ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE": {"status": "approved"}}
    result = calculate_transformer_phase_pe_impedance(transformer_phase_pe_data(), rules)

    equivalent = result.outputs["transformer_phase_pe_equivalent"]
    assert equivalent["phase_pe_resistance_ohm"] == pytest.approx(0.0013)
    assert equivalent["phase_pe_reactance_ohm"] == pytest.approx(0.0093)
    assert equivalent["phase_pe_impedance_ohm"] == pytest.approx(
        sqrt(0.0013**2 + 0.0093**2), abs=1e-6
    )
    assert equivalent["zero_sequence_boundary"] == "低压侧单相短路时不计入高压侧零序阻抗"
    assert result.provisional_status == PASS
    assert result.status == PASS


def test_transformer_phase_pe_rejects_yyn0_and_unlisted_combinations():
    rules = {"ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE": {"status": "verified"}}
    yyn0 = calculate_transformer_phase_pe_impedance(
        transformer_phase_pe_data(transformer_vector_group="Yyn0"), rules
    )
    assert yyn0.provisional_status == UNKNOWN
    assert any("必须采用对应试验数据" in warning for warning in yyn0.warnings)

    unlisted = calculate_transformer_phase_pe_impedance(
        transformer_phase_pe_data(transformer_uk_percent=5.5), rules
    )
    assert unlisted.provisional_status == UNKNOWN
    assert any("不插值" in warning for warning in unlisted.warnings)


def test_transformer_nameplate_rejects_pk_above_physical_limit():
    result = calculate_transformer_lv_nameplate_impedance(
        transformer_nameplate_data(transformer_pk_kw=61),
        verified_nameplate_impedance_rules(),
    )

    assert "transformer_equivalent" not in result.outputs
    assert any("物理上限 60 kW" in warning for warning in result.warnings)


def test_transformer_nameplate_missing_pk_cannot_be_used_for_line_end_calculation():
    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            upstream_r_ohm="",
            upstream_x_ohm="",
            transformer_capacity_kva=1000,
            transformer_lv_rated_voltage_v=400,
            transformer_uk_percent=6,
            transformer_pk_kw="",
            source_impedance_mode="infinite_capacity",
        ),
        verified_nameplate_impedance_rules(),
    )

    assert "terminal_short_circuit_current_ka" not in result.outputs
    assert any("transformer_pk_kw" in warning for warning in result.warnings)


def test_transformer_nameplate_and_explicit_source_are_split_in_feeder_output():
    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            upstream_r_ohm="",
            upstream_x_ohm="",
            transformer_capacity_kva=1000,
            transformer_lv_rated_voltage_v=400,
            transformer_uk_percent=6,
            transformer_pk_kw=10,
            source_impedance_mode="provided",
            source_r_ohm=0.001,
            source_x_ohm=0.005,
        ),
        verified_nameplate_impedance_rules(),
    )

    upstream = result.outputs["upstream_impedance"]
    assert upstream["transformer_equivalent"]["source"] == "变压器铭牌 S/U/uk%/Pk"
    assert upstream["source_equivalent"]["condition"] == "provided"
    assert result.outputs["terminal_short_circuit_current_ka"] > 0


def test_transformer_nameplate_and_upstream_short_circuit_capacity_are_converted_to_lv_rx():
    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            upstream_r_ohm="",
            upstream_x_ohm="",
            transformer_capacity_kva=1000,
            transformer_lv_rated_voltage_v=400,
            transformer_uk_percent=6,
            transformer_pk_kw=10,
            source_impedance_mode="short_circuit_capacity",
            source_short_circuit_capacity_mva=100,
        ),
        verified_nameplate_impedance_rules(),
    )

    source = result.outputs["upstream_impedance"]["source_equivalent"]
    expected_z = 400**2 / (100 * 1_000_000)
    assert source["condition"] == "short_circuit_capacity"
    assert source["impedance_ohm"] == pytest.approx(expected_z)
    assert source["resistance_ohm"] == pytest.approx(expected_z * 0.1 / sqrt(1.01), abs=1e-6)
    assert source["reactance_ohm"] == pytest.approx(expected_z / sqrt(1.01), abs=1e-6)
    assert any("R/X=0.1" in warning for warning in result.warnings)


def test_transformer_feeder_uses_exact_handbook_positive_sequence_row_without_pk_input():
    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            upstream_r_ohm="",
            upstream_x_ohm="",
            transformer_series_code="scb11",
            transformer_capacity_kva=630,
            transformer_lv_rated_voltage_v=400,
            transformer_uk_percent=6,
            transformer_pk_kw="",
            source_impedance_mode="short_circuit_capacity",
            source_short_circuit_capacity_mva=100,
            breaker_icu_ka="",
        ),
        {"ELEC.TRANSFORMER.POSITIVE_SEQUENCE.IMPEDANCE": {"status": "verified"}},
    )

    transformer = result.outputs["upstream_impedance"]["transformer_equivalent"]
    assert transformer["resistance_ohm"] == pytest.approx(0.0024)
    assert transformer["reactance_ohm"] == pytest.approx(0.015)
    assert result.outputs["required_breaking_capacity_ka"] > 0


def test_transformer_nameplate_rx_is_blocked_until_formula_source_is_verified():
    result = calculate_transformer_lv_nameplate_impedance(transformer_nameplate_data(), {})

    assert "transformer_equivalent" not in result.outputs
    assert result.status == UNKNOWN
    assert any("原始公式依据尚未核实" in warning for warning in result.warnings)


def test_transformer_feeder_requires_reference_for_explicit_upstream_rx():
    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(upstream_impedance_reference=""), {}
    )

    assert "terminal_short_circuit_current_ka" not in result.outputs
    assert any("upstream_impedance_reference" in warning for warning in result.warnings)


def test_transformer_feeder_three_phase_short_circuit_uses_catalog_line_rx_and_checks_line_end_icu():
    result = calculate_transformer_feeder_three_phase_short_circuit(transformer_feeder_data(), {})

    line_r = 0.435 * 0.1
    line_x = 0.080 * 0.1
    total_r = 0.003 + line_r
    total_x = 0.020 + line_x
    expected = 1.05 * 380 / (sqrt(3) * sqrt(total_r**2 + total_x**2)) / 1000

    assert result.outputs["line_impedance"]["source"]["table"] == "表3.21"
    assert result.outputs["line_impedance"]["resistance_ohm"] == pytest.approx(line_r)
    assert result.outputs["terminal_short_circuit_current_ka"] == pytest.approx(expected, abs=1e-4)
    assert result.outputs["breaker_icu_check"]["installation_point"] == "line_end"
    assert result.outputs["breaker_icu_check"]["provisional_status"] == PASS
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN


def test_transformer_feeder_checks_feeder_breaker_at_line_start_not_line_end():
    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            breaker_installation_point="line_start",
            breaker_icu_ka=15,
        ),
        {},
    )

    line_start = result.outputs["line_start_short_circuit_current_ka"]
    line_end = result.outputs["terminal_short_circuit_current_ka"]
    assert line_start > line_end
    assert result.outputs["required_breaking_capacity_ka"] == line_start
    assert result.outputs["required_breaking_capacity_point"] == "line_start"
    assert result.outputs["breaker_icu_check"]["installation_point"] == "line_start"
    assert result.outputs["breaker_icu_check"]["prospective_short_circuit_ka"] == line_start


def test_transformer_feeder_remains_unofficial_until_catalog_rx_is_approved():
    rules = {
        "ELEC.SHORT_CIRCUIT": {"status": "approved"},
        "ELEC.BREAKING.CAPACITY": {"status": "approved"},
        "ELEC.VDROP.IMPEDANCE": {"status": "approved"},
    }
    result = calculate_transformer_feeder_three_phase_short_circuit(transformer_feeder_data(), rules)

    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert any("线路 R/X 数据尚未批准" in warning for warning in result.warnings)


def test_transformer_feeder_does_not_infer_upstream_rx_from_outlet_current_only():
    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            upstream_r_ohm="",
            upstream_x_ohm="",
            breaker_installation_point="transformer_lv_outlet",
            transformer_lv_outlet_ik_ka=24,
            breaker_icu_ka=25,
        ),
        {},
    )

    assert "terminal_short_circuit_current_ka" not in result.outputs
    assert result.outputs["breaker_icu_check"]["prospective_short_circuit_ka"] == 24
    assert result.outputs["breaker_icu_check"]["provisional_status"] == PASS
    assert result.provisional_status == UNKNOWN
    assert any("不会由出口 Ik 幅值推定 R/X" in warning for warning in result.warnings)


def test_transformer_feeder_busway_requires_traceable_rx_instead_of_guessing():
    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            line_type="busway",
            conductor_family="",
            installation_scenario="",
            line_section_mm2="",
        ),
        {},
    )

    assert "terminal_short_circuit_current_ka" not in result.outputs
    assert any("母线槽必须提供" in warning for warning in result.warnings)


def test_transformer_feeder_explicit_busway_rx_needs_source_and_can_be_calculated():
    missing_source = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            line_type="busway",
            line_r_ohm_per_km=0.020,
            line_x_ohm_per_km=0.015,
            line_impedance_reference="",
        ),
        {},
    )
    assert any("line_impedance_reference" in warning for warning in missing_source.warnings)

    result = calculate_transformer_feeder_three_phase_short_circuit(
        transformer_feeder_data(
            line_type="busway",
            line_r_ohm_per_km=0.020,
            line_x_ohm_per_km=0.015,
            line_impedance_reference="制造商样本第12页",
        ),
        {},
    )
    assert result.outputs["line_impedance"]["source"]["mode"] == "explicit"
    assert result.outputs["terminal_short_circuit_current_ka"] > 0


def tn_earth_fault_data(**changes):
    base = {
        "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": 230,
        "circuit_application": "socket_final",
        "circuit_rated_current_a": 32,
        "fault_loop_impedance_ohm": 0.8,
        "fault_loop_impedance_reference": "接地故障回路计算书第2页",
        "protection_type": "overcurrent",
        "protective_device_operating_current_a": 200,
        "protective_device_operating_reference": "断路器时间—电流曲线第3页，0.4s",
    }
    base.update(changes)
    return base


def cable_fault_loop_data(**changes):
    base = {
        "conductor_material": "copper",
        "phase_section_mm2": 35,
        "protective_section_mm2": 16,
        "length_m": 100,
        "conductor_temperature_c": 80,
        "phase_conductor_form": "stranded",
        "protective_conductor_form": "stranded",
        "phase_ac_resistance_factor": 1.01,
        "protective_ac_resistance_factor": 1.01,
        "ac_resistance_factor_reference": "电缆结构计算书第1页",
        "loop_reactance_ohm_per_km": 0.16,
        "loop_reactance_reference": "电缆结构计算书第2页",
        "upstream_resistance_ohm": 0.003,
        "upstream_reactance_ohm": 0.020,
        "upstream_impedance_reference": "上游短路计算书第1页",
    }
    base.update(changes)
    return base


def test_cable_fault_loop_calculates_temperature_corrected_l_pe_components():
    result = calculate_cable_fault_loop_impedance(cable_fault_loop_data(), {})

    rho_theta = 0.0172 * (1 + 0.004 * (80 - 20))
    phase_dc = rho_theta * 1.02 * 100 / 35
    pe_dc = rho_theta * 1.02 * 100 / 16
    loop_r = (phase_dc + pe_dc) * 1.01
    loop_x = 0.16 * 0.1
    expected_zs = sqrt((0.003 + loop_r) ** 2 + (0.020 + loop_x) ** 2)

    assert result.outputs["rho20_ohm_mm2_per_m"] == 0.0172
    assert result.outputs["temperature_coefficient_per_c"] == 0.004
    assert result.outputs["line_loop_dc_resistance_ohm"] == pytest.approx(phase_dc + pe_dc, abs=1e-6)
    assert result.outputs["fault_loop_impedance_ohm"] == pytest.approx(expected_zs, abs=1e-6)
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN


def test_cable_fault_loop_does_not_guess_ac_factor_or_reactance():
    result = calculate_cable_fault_loop_impedance(
        cable_fault_loop_data(
            phase_ac_resistance_factor="",
            protective_ac_resistance_factor="",
            ac_resistance_factor_reference="",
            loop_reactance_ohm_per_km="",
            loop_reactance_reference="",
        ),
        {},
    )

    assert "line_loop_dc_resistance_ohm" in result.outputs
    assert "fault_loop_impedance_ohm" not in result.outputs
    assert result.provisional_status == UNKNOWN
    assert any("交流电阻修正系数" in warning for warning in result.warnings)


def test_cable_fault_loop_can_calculate_reactance_from_traceable_geometry():
    result = calculate_cable_fault_loop_impedance(
        cable_fault_loop_data(
            loop_reactance_ohm_per_km="",
            loop_reactance_reference="",
            phase_conductor_radius_cm=0.35,
            protective_conductor_radius_cm=0.24,
            phase_pe_center_distance_cm=1.2,
            frequency_hz=50,
            cable_geometry_reference="电缆结构图第2页",
        ),
        {},
    )

    phase_x = 2 * pi * 50 * 2e-4 * log(1.2 / (0.778 * 0.35))
    pe_x = 2 * pi * 50 * 2e-4 * log(1.2 / (0.778 * 0.24))
    assert result.outputs["reactance_method"] == "geometry"
    assert result.outputs["line_loop_reactance_ohm_per_km"] == pytest.approx(phase_x + pe_x)
    assert result.outputs["fault_loop_impedance_ohm"] > 0


def test_cable_fault_loop_can_use_yjv_structure_catalog_without_geometry_entry():
    result = calculate_cable_fault_loop_impedance(
        cable_fault_loop_data(
            phase_section_mm2=35,
            protective_section_mm2="",
            cable_structure_code="yjv_5c_3ph_n_pe",
            loop_reactance_ohm_per_km="",
            loop_reactance_reference="",
        ),
        {},
    )

    structure = result.outputs["structure_catalog"]
    assert structure["profile"] == "yjv_3plus2"
    assert structure["protective_section_mm2"] == 16
    assert structure["phase_conductor_diameter_mm"] == 7.0
    assert result.outputs["protective_conductor"]["section_mm2"] == 16
    assert result.outputs["reactance_method"] == "geometry"


def test_cable_fault_loop_can_use_traceable_fault_resistance_multiplier():
    result = calculate_cable_fault_loop_impedance(
        cable_fault_loop_data(
            phase_section_mm2=35,
            protective_section_mm2="",
            cable_structure_code="yjv_5c_3ph_n_pe",
            conductor_temperature_c=20,
            phase_ac_resistance_factor="",
            protective_ac_resistance_factor="",
            ac_resistance_factor_reference="",
            fault_resistance_multiplier=1.5,
            fault_resistance_multiplier_reference=(
                "《工业与民用供配电设计手册（第四版）》第4.6.4节(1)第4项"
            ),
            loop_reactance_ohm_per_km="",
            loop_reactance_reference="",
        ),
        {},
    )

    rho20 = 0.0172
    expected_r = (
        rho20 * 1.02 * 100 / 35
        + rho20 * 1.02 * 100 / 16
    ) * 1.5
    assert result.outputs["resistance_calculation_method"] == (
        "fault_resistance_multiplier"
    )
    assert result.outputs["fault_resistance_multiplier"] == 1.5
    assert result.outputs["line_loop_effective_resistance_ohm"] == pytest.approx(
        expected_r, abs=1e-6
    )
    assert result.outputs["reactance_method"] == "geometry"
    assert result.outputs["fault_loop_impedance_ohm"] > 0


def test_cable_fault_loop_can_use_transformer_phase_pe_catalog_as_upstream():
    data = cable_fault_loop_data(
        upstream_resistance_ohm="",
        upstream_reactance_ohm="",
        upstream_impedance_reference="",
        upstream_transformer_phase_pe_data=transformer_phase_pe_data(),
    )
    rules = {"ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE": {"status": "verified"}}
    result = calculate_cable_fault_loop_impedance(data, rules)

    assert result.outputs["upstream_resistance_ohm"] == pytest.approx(0.0013)
    assert result.outputs["upstream_reactance_ohm"] == pytest.approx(0.0093)
    assert result.outputs["upstream_transformer_phase_pe"]["provisional_status"] == PASS
    assert result.outputs["fault_loop_impedance_ohm"] > 0
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN


def test_tn_fault_loop_chain_composes_transformer_main_busway_and_outgoing_cable():
    rules = {
        "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE": {"status": "verified"},
        "PRODUCT.BUSWAY.RX": {"status": "approved"},
    }
    result = calculate_tn_fault_loop_chain(
        {
            "target_point": "line_end",
            "transformer_phase_pe_data": transformer_phase_pe_data(),
            "segments": [
                {
                    "role": "transformer_to_main_switchboard",
                    "segment_type": "busway",
                    "name": "变压器至低压总柜母线槽",
                    "calculation_mode": "explicit_per_km",
                    "resistance_ohm_per_km": 0.020,
                    "reactance_ohm_per_km": 0.015,
                    "length_m": 10,
                    "impedance_reference": "母线槽样本第12页",
                    "source_rule_code": "PRODUCT.BUSWAY.RX",
                },
                {
                    "role": "outgoing_circuit",
                    "segment_type": "cable",
                    "name": "末端YJV回路",
                    "calculation_mode": "copper_cable",
                    "cable_data": cable_fault_loop_data(
                        length_m=50,
                        upstream_resistance_ohm="",
                        upstream_reactance_ohm="",
                        upstream_impedance_reference="",
                    ),
                },
            ],
        },
        rules,
    )

    cable_r = result.outputs["components"][2]["resistance_ohm"]
    cable_x = result.outputs["components"][2]["reactance_ohm"]
    assert result.outputs["fault_loop_total_resistance_ohm"] == pytest.approx(
        0.0013 + 0.0002 + cable_r, abs=1e-6
    )
    assert result.outputs["fault_loop_total_reactance_ohm"] == pytest.approx(
        0.0093 + 0.00015 + cable_x, abs=1e-6
    )
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN


def test_tn_fault_loop_chain_enforces_target_point_segment_boundary():
    rules = {"ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE": {"status": "verified"}}
    main_without_connection = calculate_tn_fault_loop_chain(
        {
            "target_point": "main_switchboard",
            "transformer_phase_pe_data": transformer_phase_pe_data(),
            "segments": [],
        },
        rules,
    )
    assert main_without_connection.provisional_status == UNKNOWN
    assert any("必须且只能" in warning for warning in main_without_connection.warnings)

    line_without_outgoing = calculate_tn_fault_loop_chain(
        {
            "target_point": "line_end",
            "transformer_phase_pe_data": transformer_phase_pe_data(),
            "segments": [{
                "role": "transformer_to_main_switchboard",
                "segment_type": "busway",
                "calculation_mode": "explicit_total",
                "resistance_ohm": 0.0002,
                "reactance_ohm": 0.00015,
                "impedance_reference": "母线槽样本第12页",
            }],
        },
        rules,
    )
    assert line_without_outgoing.provisional_status == UNKNOWN
    assert any("outgoing_circuit" in warning for warning in line_without_outgoing.warnings)


def test_tn_fault_loop_chain_looks_up_yjv_four_core_from_section_and_length():
    rules = {
        "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE": {"status": "approved"},
        "ELEC.CABLE.YJV.FOUR_CORE.PHASE_PE.IMPEDANCE": {"status": "approved"},
    }
    result = calculate_tn_fault_loop_chain(
        {
            "target_point": "line_end",
            "transformer_phase_pe_data": transformer_phase_pe_data(),
            "segments": [{
                "role": "outgoing_circuit",
                "segment_type": "cable",
                "name": "末端YJV四芯回路",
                "calculation_mode": "yjv_four_core_catalog",
                "configuration_code": "yjv_4c_3ph_n_pe",
                "fourth_conductor_role": "PE",
                "phase_section_mm2": 10,
                "length_m": 50,
            }],
        },
        rules,
    )

    cable = result.outputs["components"][1]
    assert cable["cable_specification"] == "YJV-0.6/1kV 3×10+1×6"
    assert cable["resistance_ohm"] == pytest.approx(0.37152)
    assert cable["reactance_ohm"] == pytest.approx(0.0093)
    assert cable["phase_pe_resistance_multiplier"] == pytest.approx(1.5)
    assert result.outputs["fault_loop_total_resistance_ohm"] == pytest.approx(
        0.0013 + 0.37152
    )
    assert result.outputs["fault_loop_total_reactance_ohm"] == pytest.approx(
        0.0093 + 0.0093
    )
    assert result.status == PASS


def test_tn_fault_loop_chain_rejects_four_core_when_fourth_core_is_not_pe():
    result = calculate_tn_fault_loop_chain(
        {
            "target_point": "line_end",
            "transformer_phase_pe_data": transformer_phase_pe_data(),
            "segments": [{
                "role": "outgoing_circuit",
                "segment_type": "cable",
                "calculation_mode": "yjv_four_core_catalog",
                "configuration_code": "yjv_4c_3ph_n_pe",
                "fourth_conductor_role": "N",
                "phase_section_mm2": 35,
                "length_m": 50,
            }],
        },
        {"ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE": {"status": "approved"}},
    )

    assert result.status == UNKNOWN
    assert any("第四芯作为PE" in warning for warning in result.warnings)


def test_tn_earth_fault_uses_complete_chain_before_manual_cable_fallback():
    rules = {
        **approved_tn_earth_fault_rules(),
        "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE": {"status": "verified"},
    }
    earth = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            fault_loop_impedance_ohm="",
            fault_loop_impedance_reference="",
            fault_loop_chain_data={
                "target_point": "transformer_lv_terminal",
                "transformer_phase_pe_data": transformer_phase_pe_data(),
                "segments": [],
            },
        ),
        rules,
    )

    zs = sqrt(0.0013**2 + 0.0093**2)
    assert earth.outputs["calculated_fault_loop_chain"]["outputs"][
        "fault_loop_impedance_ohm"
    ] == pytest.approx(zs, abs=1e-6)
    assert earth.outputs["prospective_earth_fault_current_a"] == pytest.approx(
        230 / round(zs, 6), rel=1e-4
    )
    assert earth.outputs["fault_current_calculation_method"] == "complete_loop_impedance"
    assert earth.status == UNKNOWN


def test_tn_earth_fault_can_use_calculated_fault_loop_without_manual_zs():
    data = tn_earth_fault_data(
        fault_loop_impedance_ohm="",
        fault_loop_impedance_reference="",
        fault_loop_data=cable_fault_loop_data(),
    )
    result = calculate_tn_earth_fault_protection(data, approved_tn_earth_fault_rules())

    assert result.outputs["calculated_fault_loop"]["outputs"]["fault_loop_impedance_ohm"] > 0
    assert result.outputs["prospective_earth_fault_current_a"] > 0
    assert "系统计算：" in result.outputs["fault_loop_impedance_reference"]
    assert result.provisional_status in {PASS, "不通过"}
    assert result.status == UNKNOWN


def test_tn_conventional_method_calculates_minimum_fault_current_without_manual_zs():
    result = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            fault_loop_impedance_ohm="",
            fault_loop_impedance_reference="",
            conventional_method_data={
                "conductor_material": "copper",
                "phase_section_mm2": 10,
                "protective_section_mm2": 6,
                "length_m": 50,
                "conductors_in_same_cable": True,
            },
        ),
        approved_tn_earth_fault_rules(),
    )

    expected_r = 0.0237 * 50 * (1 / 10 + 1 / 6)
    assert result.outputs["conventional_method"]["line_loop_resistance_ohm"] == pytest.approx(
        expected_r
    )
    assert result.outputs["prospective_earth_fault_current_a"] == pytest.approx(
        0.8 * 230 / expected_r, rel=1e-4
    )
    assert result.outputs["fault_current_calculation_method"] == "tn_conventional"
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN


def test_tn_conventional_method_rejects_separated_or_large_conductors():
    separated = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            fault_loop_impedance_ohm="",
            fault_loop_impedance_reference="",
            conventional_method_data={
                "conductor_material": "copper",
                "phase_section_mm2": 10,
                "protective_section_mm2": 6,
                "length_m": 50,
                "conductors_in_same_cable": False,
            },
        ),
        {},
    )
    assert separated.provisional_status == UNKNOWN
    assert any("同一电缆内或彼此靠近" in item for item in separated.warnings)

    too_large = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            fault_loop_impedance_ohm="",
            fault_loop_impedance_reference="",
            conventional_method_data={
                "conductor_material": "copper",
                "phase_section_mm2": 150,
                "protective_section_mm2": 70,
                "length_m": 50,
                "conductors_in_same_cable": True,
            },
        ),
        {},
    )
    assert too_large.provisional_status == UNKNOWN
    assert any("不超过120mm²" in item for item in too_large.warnings)


@pytest.mark.parametrize(("code", "multiplier"), [("mcb_b", 5), ("mcb_c", 10)])
def test_tn_earth_fault_derives_mcb_b_c_operating_current_from_verified_upper_limit(
    code, multiplier
):
    result = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            protective_device_operating_current_a="",
            protective_device_operating_reference="",
            protective_device_characteristic=code,
            protective_device_rated_current_a=32,
        ),
        approved_tn_earth_fault_rules(),
    )

    assert result.outputs["protective_device_operating_current_a"] == 32 * multiplier
    assert (
        result.outputs["protective_device_characteristic"][
            "guaranteed_instantaneous_multiplier"
        ]
        == multiplier
    )
    assert "ELEC.BREAKER.MCB.INSTANTANEOUS" in result.rule_codes
    assert result.status == UNKNOWN


def test_tn_earth_fault_does_not_generalize_d_curve_without_product_curve():
    result = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            protective_device_operating_current_a="",
            protective_device_operating_reference="",
            protective_device_characteristic="mcb_d",
            protective_device_rated_current_a=32,
        ),
        {},
    )
    assert result.provisional_status == UNKNOWN
    assert any("其他特性须按具体产品曲线" in item for item in result.warnings)


def test_pe_thermal_withstand_checks_traceable_breaker_let_through_energy():
    result = calculate_pe_thermal_withstand(
        {
            "protective_conductor_section_mm2": 6,
            "protective_conductor_material": "copper",
            "protective_conductor_insulation": "xlpe",
            "protective_conductor_arrangement": "multicore_cable",
            "let_through_energy_a2s": 500_000,
            "let_through_energy_reference": "断路器样本第12页I²t曲线",
        },
        {"ELEC.PE.THERMAL.WITHSTAND": {"status": "approved"}},
    )

    assert result.outputs["k_a_sqrt_s_per_mm2"] == 143
    assert result.outputs["permitted_thermal_stress_a2s"] == 143**2 * 6**2
    assert result.outputs["required_protective_conductor_section_mm2"] == round(
        500_000**0.5 / 143, 6
    )
    assert result.provisional_status == PASS
    assert result.status == PASS


def test_pe_thermal_withstand_can_use_fault_current_and_clearing_time_but_not_longer_than_five_seconds():
    result = calculate_pe_thermal_withstand(
        {
            "protective_conductor_section_mm2": 6,
            "protective_conductor_material": "copper",
            "protective_conductor_insulation": "pvc",
            "protective_conductor_arrangement": "single_or_bare",
            "prospective_fault_current_a": 1000,
            "fault_clearing_time_s": 0.1,
        },
        {},
    )
    assert result.outputs["actual_thermal_stress_a2s"] == 100_000
    assert result.outputs["permitted_thermal_stress_a2s"] == 143**2 * 6**2
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN

    invalid_time = calculate_pe_thermal_withstand(
        {
            "protective_conductor_section_mm2": 6,
            "protective_conductor_material": "copper",
            "protective_conductor_insulation": "pvc",
            "protective_conductor_arrangement": "single_or_bare",
            "prospective_fault_current_a": 1000,
            "fault_clearing_time_s": 6,
        },
        {},
    )
    assert invalid_time.provisional_status == UNKNOWN
    assert any("不超过5s" in item for item in invalid_time.warnings)


def test_pe_thermal_without_product_curve_returns_permitted_time_and_i2t_constraints():
    result = calculate_pe_thermal_withstand(
        {
            "protective_conductor_section_mm2": 6,
            "protective_conductor_material": "copper",
            "protective_conductor_insulation": "xlpe",
            "protective_conductor_arrangement": "multicore_cable",
            "prospective_fault_current_a": 1000,
        },
        {},
    )

    permitted = 143**2 * 6**2
    assert result.provisional_status == UNKNOWN
    assert result.outputs["maximum_permitted_let_through_energy_a2s"] == permitted
    assert result.outputs["maximum_permitted_clearing_time_s"] == round(
        permitted / 1000**2, 6
    )
    assert "实际保护器件" in result.outputs["thermal_constraint_note"]

    low_fault_current = calculate_pe_thermal_withstand(
        {
            "protective_conductor_section_mm2": 6,
            "protective_conductor_material": "copper",
            "protective_conductor_insulation": "xlpe",
            "protective_conductor_arrangement": "multicore_cable",
            "prospective_fault_current_a": 300,
        },
        {},
    )
    assert low_fault_current.outputs["calculated_thermal_time_limit_s"] > 5
    assert low_fault_current.outputs["maximum_permitted_clearing_time_s"] == 5
    assert low_fault_current.outputs["clearing_time_governing_basis"] == (
        "绝热法当前适用上限5s"
    )


def test_phase_conductor_thermal_uses_phase_insulation_k_values():
    xlpe = calculate_phase_conductor_thermal_withstand(
        {
            "phase_conductor_section_mm2": 4,
            "phase_conductor_material": "copper",
            "phase_conductor_insulation": "xlpe",
            "prospective_fault_current_a": 1000,
            "fault_clearing_time_s": 0.1,
        },
        {"ELEC.PHASE.THERMAL.WITHSTAND": {"status": "approved"}},
    )
    pvc_large = calculate_phase_conductor_thermal_withstand(
        {
            "phase_conductor_section_mm2": 400,
            "phase_conductor_material": "copper",
            "phase_conductor_insulation": "pvc",
            "let_through_energy_a2s": 1_000_000,
            "let_through_energy_reference": "产品I²t曲线",
        },
        {"ELEC.PHASE.THERMAL.WITHSTAND": {"status": "approved"}},
    )
    assert xlpe.outputs["k_a_sqrt_s_per_mm2"] == 143
    assert xlpe.outputs["actual_thermal_stress_a2s"] == 100_000
    assert xlpe.outputs["required_phase_conductor_section_mm2"] == round(
        1000 * (0.1**0.5) / 143,
        6,
    )
    assert xlpe.provisional_status == PASS
    assert pvc_large.outputs["k_a_sqrt_s_per_mm2"] == 103
    assert pvc_large.outputs["k_basis"] == "铜芯PVC绝缘且截面>300mm²"


def test_phase_thermal_without_product_curve_returns_permitted_time_and_i2t_constraints():
    result = calculate_phase_conductor_thermal_withstand(
        {
            "phase_conductor_section_mm2": 10,
            "phase_conductor_material": "copper",
            "phase_conductor_insulation": "xlpe",
            "prospective_fault_current_a": 5000,
        },
        {},
    )

    permitted = 143**2 * 10**2
    assert result.provisional_status == UNKNOWN
    assert result.outputs["maximum_permitted_let_through_energy_a2s"] == permitted
    assert result.outputs["maximum_permitted_clearing_time_s"] == round(
        permitted / 5000**2, 6
    )


def test_phase_conductor_thermal_failure_is_not_confused_with_pe_check():
    result = calculate_phase_conductor_thermal_withstand(
        {
            "phase_conductor_section_mm2": 4,
            "phase_conductor_material": "copper",
            "phase_conductor_insulation": "xlpe",
            "prospective_fault_current_a": 5000,
            "fault_clearing_time_s": 0.1,
        },
        {},
    )
    assert result.provisional_status == FAIL
    assert result.outputs["required_phase_conductor_section_mm2"] > 4


def approved_tn_earth_fault_rules():
    return {
        "ELEC.EARTH_FAULT.TN.IMPEDANCE": {"status": "approved"},
        "ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME": {"status": "approved"},
    }


def test_tn_earth_fault_checks_zs_ia_and_uses_point_four_seconds():
    result = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(), approved_tn_earth_fault_rules()
    )

    assert result.outputs["maximum_disconnection_time_s"] == 0.4
    assert result.outputs["prospective_earth_fault_current_a"] == pytest.approx(287.5)
    assert result.outputs["maximum_permitted_loop_impedance_ohm"] == pytest.approx(1.15)
    assert result.outputs["zs_times_ia_v"] == pytest.approx(160)
    assert result.provisional_status == PASS
    assert result.status == PASS


def test_tn_earth_fault_fails_when_zs_times_ia_exceeds_u0():
    result = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(fault_loop_impedance_ohm=2),
        approved_tn_earth_fault_rules(),
    )

    assert result.provisional_status == "不通过"
    assert result.status == "不通过"


@pytest.mark.parametrize(
    ("application", "rating"),
    [("distribution", None), ("socket_final", 80), ("fixed_equipment_final", 40)],
)
def test_tn_earth_fault_uses_five_seconds_for_distribution_or_larger_final_circuits(application, rating):
    result = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(circuit_application=application, circuit_rated_current_a=rating),
        {},
    )

    assert result.outputs["maximum_disconnection_time_s"] == 5.0
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN


def test_tn_earth_fault_does_not_guess_zs_or_operating_current_sources():
    result = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            fault_loop_impedance_reference="",
            protective_device_operating_reference="",
        ),
        {},
    )

    assert "prospective_earth_fault_current_a" not in result.outputs
    assert result.provisional_status == UNKNOWN
    assert any("fault_loop_impedance_reference" in warning for warning in result.warnings)
    assert any("protective_device_operating_reference" in warning for warning in result.warnings)


def test_tn_earth_fault_rejects_tn_c_and_requires_rcd_after_pen_split():
    tn_c = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(earthing_system="TN-C"), {}
    )
    assert tn_c.provisional_status == UNKNOWN
    assert any("仅支持 TN-S 或 TN-C-S" in warning for warning in tn_c.warnings)

    unsplit = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            earthing_system="TN-C-S",
            protection_type="rcd",
            protective_device_operating_current_a=0.03,
            protective_device_operating_reference="RCD样本第5页",
            rcd_downstream_of_pen_split=False,
        ),
        {},
    )
    assert unsplit.provisional_status == UNKNOWN
    assert any("N线与PE线分开后" in warning for warning in unsplit.warnings)

    split = calculate_tn_earth_fault_protection(
        tn_earth_fault_data(
            earthing_system="TN-C-S",
            protection_type="rcd",
            protective_device_operating_current_a=0.03,
            protective_device_operating_reference="RCD样本第5页",
            rcd_downstream_of_pen_split=True,
        ),
        {},
    )
    assert split.provisional_status == PASS
    assert split.status == UNKNOWN


def test_calculate_all_reuses_load_current():
    results = calculate_all(circuit(), approved_rules())
    assert [item.module for item in results] == ["负荷与选型", "电压降", "短路电流"]
    assert results[1].outputs["voltage_drop_v"] > 0


def test_quick_selection_uses_known_current_and_stays_unofficial():
    result = calculate_quick_selection({"design_current_a": 41, "phase": "3"}, approved_rules())
    assert result.outputs["breaker_candidate"] == "3P 50 A"
    assert result.outputs["cable_candidate"] == "YJV 3×10 mm²"
    assert result.outputs["cable_base_ampacity_a"] == 55
    assert result.provisional_status == PASS
    assert result.status == PASS

    unofficial = calculate_quick_selection({"design_current_a": 41, "phase": "3"}, {})
    assert unofficial.status == UNKNOWN
    assert any("不能作为正式设计结论" in item for item in unofficial.warnings)


def test_quick_selection_rejects_missing_or_out_of_range_current():
    missing = calculate_quick_selection({"design_current_a": "", "phase": "3"}, {})
    assert missing.status == UNKNOWN
    assert "计算电流" in missing.warnings[0]

    too_large = calculate_quick_selection({"design_current_a": 999, "phase": "3"}, {})
    assert too_large.status == UNKNOWN
    assert "超出" in too_large.warnings[0]
