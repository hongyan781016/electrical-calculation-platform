import pytest

from src.electrical_calc.catalog import (
    lookup_busway_phase_pe_impedance,
    lookup_canalis_kta_3lnpe_electrical,
    lookup_transformer_phase_pe_impedance,
    lookup_transformer_positive_sequence_impedance,
    lookup_yjv_four_core_phase_pe_impedance,
    lookup_yjv_fault_loop_structure,
)


def test_kta_3lnpe_complete_electrical_row_is_exact_and_temperature_corrected():
    row = lookup_canalis_kta_3lnpe_electrical(1600, 40)
    assert row["corrected_ampacity_a"] == 1552
    assert row["three_phase_r_ohm_per_km"] == 0.042
    assert row["phase_neutral_x_ohm_per_km"] == 0.030
    assert row["phase_pe_r_ohm_per_km"] == 0.394
    assert row["short_time_withstand_ka_1s"] == 65
    assert lookup_canalis_kta_3lnpe_electrical(1500, 40) is None
    assert lookup_canalis_kta_3lnpe_electrical(1600, 42) is None


def test_scb11_positive_sequence_lookup_keeps_positive_and_phase_pe_x_separate():
    positive = lookup_transformer_positive_sequence_impedance("scb11", 630, 6)
    phase_pe = lookup_transformer_phase_pe_impedance("scb11", 630, 6)

    assert positive is not None
    assert phase_pe is not None
    assert positive["positive_sequence_resistance_ohm"] == pytest.approx(0.00240)
    assert positive["positive_sequence_reactance_ohm"] == pytest.approx(0.0150)
    assert positive["load_loss_kw"] == pytest.approx(5.96)
    assert phase_pe["phase_pe_reactance_ohm"] == pytest.approx(0.0146)


def test_canalis_busway_phase_pe_lookup_requires_exact_series_and_rating():
    ks = lookup_busway_phase_pe_impedance("canalis_ks_casing_pe", 400)
    kta = lookup_busway_phase_pe_impedance("canalis_kta_casing_pe", 1600)
    copper_pe = lookup_busway_phase_pe_impedance(
        "canalis_kta_internal_cu_pe", 1600
    )

    assert ks is not None
    assert ks["resistance_ohm_per_km"] == pytest.approx(1.12)
    assert ks["reactance_ohm_per_km"] == pytest.approx(0.67)
    assert kta is not None
    assert kta["resistance_ohm_per_km"] == pytest.approx(0.394)
    assert kta["reactance_ohm_per_km"] == pytest.approx(0.212)
    assert copper_pe is not None
    assert copper_pe["resistance_ohm_per_km"] == pytest.approx(0.080)
    assert copper_pe["reactance_ohm_per_km"] == pytest.approx(0.026)
    assert lookup_busway_phase_pe_impedance(
        "canalis_kta_casing_pe", 1500
    ) is None
    assert lookup_busway_phase_pe_impedance(
        "canalis_ks_casing_pe", 1000
    ) is None


def test_yjv_round_core_structure_lookup_is_traceable():
    item = lookup_yjv_fault_loop_structure("yjv_4c_3ph_n_pe", 35)

    assert item is not None
    assert item["profile"] == "yjv_3plus1"
    assert item["protective_section_mm2"] == 16
    assert item["phase_conductor_radius_cm"] == pytest.approx(0.35)
    assert item["protective_conductor_radius_cm"] == pytest.approx(0.255)
    assert item["phase_pe_center_distance_cm"] == pytest.approx(0.765)
    assert item["table"] == "表1、表4"
    assert item["status"] == "verified"


def test_yjv_structure_lookup_rejects_unverified_sector_range_and_three_core():
    assert lookup_yjv_fault_loop_structure("yjv_4c_3ph_n_pe", 50) is None
    assert lookup_yjv_fault_loop_structure("yjv_3c_3ph_pe", 35) is None


def test_transformer_phase_pe_lookup_requires_exact_table_combination():
    row = lookup_transformer_phase_pe_impedance("scb11", 1000, 6)

    assert row is not None
    assert row["table"] == "表4.6-13"
    assert row["phase_pe_resistance_ohm"] == pytest.approx(0.0013)
    assert row["phase_pe_reactance_ohm"] == pytest.approx(0.0093)
    assert lookup_transformer_phase_pe_impedance("scb11", 1000, 5.5) is None
    assert lookup_transformer_phase_pe_impedance("unknown", 1000, 6) is None


def test_yjv_four_core_phase_pe_lookup_derives_metallic_pe_loop():
    row = lookup_yjv_four_core_phase_pe_impedance(10)

    assert row is not None
    assert row["cable_specification"] == "YJV-0.6/1kV 3×10+1×6"
    assert row["phase_pe_resistance_20c_ohm_per_km"] == pytest.approx(4.9536)
    assert row["phase_pe_resistance_multiplier"] == pytest.approx(1.5)
    assert row["phase_pe_resistance_ohm_per_km"] == pytest.approx(7.4304)
    assert row["phase_pe_reactance_ohm_per_km"] == pytest.approx(0.186)
    assert row["table"] == "表4.2-46"
    assert lookup_yjv_four_core_phase_pe_impedance(6) is None
