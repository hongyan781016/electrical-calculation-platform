from src.electrical_calc.engine import PASS, UNKNOWN
from src.electrical_calc.motor import MotorCatalogQuery
from src.electrical_calc.motor_catalog import (
    RULE_CODE,
    resolve_motor_reference_parameters,
)


def test_exact_catalog_row_returns_traceable_motor_parameters():
    result = resolve_motor_reference_parameters(
        MotorCatalogQuery(rated_output_power_kw=30, poles=4),
        {RULE_CODE: {"status": "approved"}},
    )

    assert result.status == PASS
    assert result.outputs["matched"] is True
    assert result.outputs["efficiency"] == 0.936
    assert result.outputs["power_factor"] == 0.84
    assert result.outputs["rated_current_a_at_catalog_voltage"] == 55
    assert result.outputs["locked_rotor_current_ratio"] == 7.3
    assert result.outputs["locked_rotor_power_factor"] is None
    assert result.outputs["source"]["pdf_page"] == 152
    assert result.outputs["source"]["efficiency_class"] == "IE3"


def test_catalog_does_not_interpolate_unlisted_power():
    result = resolve_motor_reference_parameters(
        MotorCatalogQuery(rated_output_power_kw=20, poles=4), {}
    )

    assert result.status == UNKNOWN
    assert result.outputs["matched"] is False
    assert any("不插值" in warning for warning in result.warnings)


def test_catalog_does_not_reuse_four_pole_row_for_other_poles():
    result = resolve_motor_reference_parameters(
        MotorCatalogQuery(rated_output_power_kw=30, poles=2), {}
    )

    assert result.status == UNKNOWN
    assert result.outputs["matched"] is False
    assert any("不能跨极数" in warning for warning in result.warnings)


def test_verified_but_unapproved_product_reference_is_not_formal_pass():
    result = resolve_motor_reference_parameters(
        MotorCatalogQuery(rated_output_power_kw=30, poles=4), {}
    )

    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert any("尚未批准" in warning for warning in result.warnings)


def test_high_power_catalog_extends_to_200kw_with_its_own_source_row():
    result = resolve_motor_reference_parameters(
        MotorCatalogQuery(rated_output_power_kw=200, poles=4),
        {RULE_CODE: {"status": "approved"}},
    )

    assert result.status == PASS
    assert result.outputs["rated_current_a_at_catalog_voltage"] == 340
    assert result.outputs["locked_rotor_current_ratio"] == 7.4
    assert result.outputs["source"]["series_code"] == "1LE1503"
    assert result.outputs["source"]["pdf_page"] == 158
