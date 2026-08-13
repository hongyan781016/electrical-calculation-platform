from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.product_protection import (
    select_tesys_gv2_small_motor_reference,
)


def selection(power_kw: float, *, phase_i2t: float = 736164) -> dict:
    currents = {0.12: (0.413764, 1.489552), 0.18: (0.601919, 2.407674), 0.25: (0.717755, 3.014569)}
    rated, starting = currents[power_kw]
    return select_tesys_gv2_small_motor_reference(
        motor_power_kw=power_kw,
        motor_rated_current_a=rated,
        motor_starting_current_a=starting,
        system_voltage_v=380,
        required_icu_ka=13.850164,
        terminal_fault_current_a=205.127568,
        phase_permitted_i2t_a2s=phase_i2t,
        pe_permitted_i2t_a2s=127806.25,
    )


def test_exact_small_motor_rows_close_all_product_and_cable_checks():
    expected = {0.12: "GV2ME04", 0.18: "GV2ME04", 0.25: "GV2ME05"}
    for power_kw, breaker in expected.items():
        result = selection(power_kw)
        assert result["breaker_model"] == breaker
        assert result["coordination_type"] == 2
        assert result["icu_status"] == PASS
        assert result["phase_thermal_status"] == PASS
        assert result["pe_thermal_status"] == PASS
        assert result["provisional_status"] == PASS


def test_small_motor_phase_thermal_limit_can_reject_a_smaller_conductor():
    result = selection(0.12, phase_i2t=300000)
    assert result["phase_thermal_status"] == FAIL
    assert result["provisional_status"] == FAIL


def test_raster_curve_does_not_approve_a_near_boundary_conductor():
    result = selection(0.12, phase_i2t=327184)
    assert result["phase_thermal_status"] == UNKNOWN
    assert result["provisional_status"] == UNKNOWN


def test_small_motor_catalog_never_interpolates_unlisted_power():
    result = select_tesys_gv2_small_motor_reference(
        motor_power_kw=0.2,
        motor_rated_current_a=0.65,
        motor_starting_current_a=2.6,
        system_voltage_v=380,
        required_icu_ka=10,
        terminal_fault_current_a=100,
        phase_permitted_i2t_a2s=500000,
        pe_permitted_i2t_a2s=500000,
    )
    assert result["provisional_status"] != PASS
    assert "不插值" in result["reason"]
