import pytest

from src.electrical_calc.cable_selector import (
    COPPER_RESISTIVITY_20C_OHM_MM2_PER_M,
    CableInstallationConditions,
    CableSelectionRequest,
    MINIMUM_FAULT_RESISTANCE_MULTIPLIER,
    MULTIWIRE_STRANDING_FACTOR,
    generate_cable_candidates,
)
from src.electrical_calc.complete_circuit import Phase
from src.electrical_calc.engine import PASS, UNKNOWN


def approved_rules():
    return {
        code: {"status": "approved"}
        for code in (
            "ELEC.CABLE.BV.AMPACITY",
            "ELEC.CABLE.YJV.AMPACITY",
            "ELEC.CABLE.YJV.MULTICORE.AMPACITY",
            "ELEC.CABLE.TEMPERATURE.DERATING",
            "ELEC.CABLE.TRAY.GROUPING",
            "ELEC.CABLE.ENCLOSED.GROUPING",
            "ELEC.CABLE.BURIED_DUCT.GROUPING",
            "ELEC.VDROP.IMPEDANCE",
            "ELEC.CABLE.YJV.FOUR_CORE.PHASE_PE.IMPEDANCE",
        )
    }


def bv_request(**changes):
    request = CableSelectionRequest(
        segment_id="line-1",
        family="BV",
        configuration_code="bv_3ph_3wire_pe",
        phase=Phase.THREE,
        system_voltage_v=380,
        installation_scenario="conduit",
        minimum_required_ampacity_a=50,
        neutral_required=False,
        protective_conductor_mode="separate",
        conditions=CableInstallationConditions(
            temperature_c=30,
            enclosed_circuit_count=1,
        ),
    )
    return CableSelectionRequest(**{**request.__dict__, **changes})


def test_bv_candidates_start_at_first_corrected_ampacity_match():
    result = generate_cable_candidates(bv_request(), approved_rules())
    candidates = result.outputs["candidates"]
    assert candidates[0]["phase_section_mm2"] == 10
    assert candidates[0]["base_ampacity_a"] == 50
    assert candidates[0]["corrected_ampacity_a"] == 50
    assert candidates[0]["ampacity_formal_status"] == PASS
    assert candidates[0]["resolved_electrical"]["voltage_drop_r_ohm_per_km"] > 0
    assert candidates[0]["resolved_electrical"]["three_phase_r_ohm_per_km"] > 0
    assert result.status == PASS


def test_temperature_and_enclosed_grouping_change_first_bv_section():
    request = bv_request(
        minimum_required_ampacity_a=41,
        conditions=CableInstallationConditions(
            temperature_c=40,
            enclosed_circuit_count=3,
        ),
    )
    result = generate_cable_candidates(request, approved_rules())
    first = result.outputs["candidates"][0]
    assert first["phase_section_mm2"] == 16
    assert first["temperature_factor"] == pytest.approx(0.87)
    assert first["grouping_factor"] == pytest.approx(0.7)
    assert first["corrected_ampacity_a"] == pytest.approx(41.412)


def test_missing_temperature_and_grouping_retains_only_base_candidates():
    request = bv_request(conditions=CableInstallationConditions())
    result = generate_cable_candidates(request, approved_rules())
    assert result.outputs["candidates"][0]["phase_section_mm2"] == 10
    assert result.outputs["conditions_complete"] is False
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert any("基础载流量候选" in warning for warning in result.warnings)


def test_yjv_four_core_conduit_is_not_silently_mapped_to_other_table():
    request = CableSelectionRequest(
        segment_id="line-2",
        family="YJV",
        configuration_code="yjv_4c_3ph_n_pe",
        phase=Phase.THREE,
        system_voltage_v=380,
        installation_scenario="conduit",
        minimum_required_ampacity_a=80,
        neutral_required=False,
        protective_conductor_mode="included",
        conditions=CableInstallationConditions(
            temperature_c=30,
            enclosed_circuit_count=1,
        ),
    )
    result = generate_cable_candidates(request, approved_rules())
    assert result.outputs["candidates"] == []
    assert any("没有覆盖" in warning for warning in result.warnings)


def test_yjv_four_core_tray_candidate_carries_verified_positive_and_phase_pe_rx():
    request = CableSelectionRequest(
        segment_id="line-3",
        family="YJV",
        configuration_code="yjv_4c_3ph_n_pe",
        phase=Phase.THREE,
        system_voltage_v=380,
        installation_scenario="tray",
        minimum_required_ampacity_a=100,
        neutral_required=False,
        protective_conductor_mode="included",
        conditions=CableInstallationConditions(
            temperature_c=40,
            tray_type="horizontal_perforated",
            tray_layers=1,
            tray_cables_per_layer=1,
        ),
    )
    result = generate_cable_candidates(request, approved_rules())
    first = result.outputs["candidates"][0]
    assert first["phase_section_mm2"] == 25
    assert first["protective_section_mm2"] == 16
    resolved = first["resolved_electrical"]
    assert resolved["three_phase_r_ohm_per_km"] > 0
    assert resolved["phase_pe_r_ohm_per_km"] > 0
    assert resolved["phase_neutral_applicable"] is False
    assert resolved["status"] == "approved"


def test_small_four_core_phase_pe_resistance_uses_named_handbook_parameters():
    request = CableSelectionRequest(
        segment_id="line-small",
        family="YJV",
        configuration_code="yjv_4c_3ph_n_pe",
        phase=Phase.THREE,
        system_voltage_v=380,
        installation_scenario="tray",
        minimum_required_ampacity_a=10,
        neutral_required=False,
        protective_conductor_mode="included",
        conditions=CableInstallationConditions(
            temperature_c=40,
            tray_type="horizontal_perforated",
            tray_layers=1,
            tray_cables_per_layer=1,
        ),
    )
    rules = approved_rules() | {
        "ELEC.CABLE.FAULT_LOOP.RESISTANCE": {"status": "approved"},
        "ELEC.CABLE.FAULT_LOOP.REACTANCE": {"status": "approved"},
        "ELEC.CABLE.YJV.STRUCTURE": {"status": "approved"},
    }

    result = generate_cable_candidates(request, rules)
    first = next(
        candidate
        for candidate in result.outputs["candidates"]
        if (candidate.get("resolved_electrical") or {}).get("phase_pe_r_ohm_per_km")
        is not None
    )
    phase_section = first["phase_section_mm2"]
    pe_section = first["protective_section_mm2"]
    expected = (
        MINIMUM_FAULT_RESISTANCE_MULTIPLIER
        * COPPER_RESISTIVITY_20C_OHM_MM2_PER_M
        * MULTIWIRE_STRANDING_FACTOR
        * 1000
        * (1 / phase_section + 1 / pe_section)
    )

    assert phase_section == 4
    assert pe_section == 2.5
    assert first["resolved_electrical"]["phase_pe_r_ohm_per_km"] == pytest.approx(
        expected
    )
    assert first["resolved_electrical"]["status"] == "approved"


def test_four_core_cannot_use_same_reduced_core_as_both_n_and_pe():
    request = CableSelectionRequest(
        segment_id="line-4",
        family="YJV",
        configuration_code="yjv_4c_3ph_n_pe",
        phase=Phase.THREE,
        system_voltage_v=380,
        installation_scenario="tray",
        minimum_required_ampacity_a=63,
        neutral_required=True,
        protective_conductor_mode="included",
        conditions=CableInstallationConditions(
            temperature_c=40,
            tray_type="horizontal_perforated",
            tray_layers=1,
            tray_cables_per_layer=1,
        ),
    )
    result = generate_cable_candidates(request, approved_rules())
    assert result.outputs["candidates"] == []
    assert any("同时把第四芯作为N和PE" in warning for warning in result.warnings)


def test_buried_grouping_requires_exact_table_reference_conditions():
    request = CableSelectionRequest(
        segment_id="line-5",
        family="YJV",
        configuration_code="yjv_3c_3ph_pe",
        phase=Phase.THREE,
        system_voltage_v=380,
        installation_scenario="direct_buried",
        minimum_required_ampacity_a=60,
        neutral_required=False,
        protective_conductor_mode="separate",
        conditions=CableInstallationConditions(
            temperature_c=20,
            soil_thermal_resistivity_k_m_per_w=2.5,
            buried_circuit_count=4,
            buried_duct_spacing_m="0.5",
            buried_depth_m=0.8,
        ),
    )
    result = generate_cable_candidates(request, approved_rules())
    assert result.outputs["candidates"] == []
    assert any("参考条件" in warning for warning in result.warnings)


def test_multicore_ground_table_stays_base_candidate_without_soil_correction_rule():
    request = CableSelectionRequest(
        segment_id="line-6",
        family="YJV",
        configuration_code="yjv_5c_3ph_n_pe",
        phase=Phase.THREE,
        system_voltage_v=380,
        installation_scenario="direct_buried",
        minimum_required_ampacity_a=80,
        neutral_required=True,
        protective_conductor_mode="included",
        conditions=CableInstallationConditions(
            temperature_c=25,
            soil_thermal_resistivity_k_m_per_w=2.5,
            buried_circuit_count=1,
        ),
    )
    result = generate_cable_candidates(request, approved_rules())
    assert result.outputs["candidates"]
    assert result.outputs["conditions_complete"] is False
    soil_check = next(
        item for item in result.outputs["checks"] if item["check_code"] == "soil_condition"
    )
    assert soil_check["status"] == UNKNOWN
    assert result.status == UNKNOWN
