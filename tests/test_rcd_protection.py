from __future__ import annotations

from src.electrical_calc.complete_circuit import EarthingSystem
from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.rcd_protection import (
    RcdProtectionInput,
    evaluate_rcd_protection,
)


def approved_rules() -> dict[str, dict[str, str]]:
    return {
        "ELEC.RCD.PARAMETERS": {"status": "approved"},
        "ELEC.EARTH_FAULT.RCD.TN_ARRANGEMENT": {"status": "approved"},
    }


def test_rcd_applicability_is_not_guessed():
    result = evaluate_rcd_protection(
        RcdProtectionInput(),
        EarthingSystem.TN_S,
        approved_rules(),
    )
    assert result.provisional_status == UNKNOWN
    assert result.outputs["applicability"]["status"] == UNKNOWN


def test_complete_tncs_rcd_requirement_checks_all_independent_conditions():
    result = evaluate_rcd_protection(
        RcdProtectionInput(
            required=True,
            applicability_reference="插座附加保护设计条件",
            scenario_code="additional_30ma",
            residual_waveform_code="pulsating_dc",
            selected_rated_residual_current_ma=30,
            normal_leakage_current_ma=5,
            downstream_of_pen_split=True,
        ),
        EarthingSystem.TN_C_S,
        approved_rules(),
    )
    assert result.outputs["scenario"]["rated_residual_current_max_ma"] == 30
    assert result.outputs["waveform"]["rcd_type"] == "A型或F型"
    assert result.outputs["tn_arrangement"]["status"] == PASS
    assert result.outputs["rated_residual_current"]["status"] == PASS
    assert result.outputs["leakage_coordination"]["status"] == PASS
    assert result.provisional_status == PASS
    assert result.status == PASS


def test_rcd_rejects_value_above_scenario_limit_and_wrong_tncs_position():
    result = evaluate_rcd_protection(
        RcdProtectionInput(
            required=True,
            scenario_code="additional_30ma",
            residual_waveform_code="ac",
            selected_rated_residual_current_ma=100,
            normal_leakage_current_ma=10,
            downstream_of_pen_split=False,
        ),
        EarthingSystem.TN_C_S,
        approved_rules(),
    )
    assert result.outputs["rated_residual_current"]["status"] == FAIL
    assert result.outputs["tn_arrangement"]["status"] == FAIL
    assert result.provisional_status == FAIL
    assert result.status == FAIL


def test_rcd_leakage_coordination_requires_more_than_twice_normal_leakage():
    result = evaluate_rcd_protection(
        RcdProtectionInput(
            required=True,
            scenario_code="fire_300ma",
            residual_waveform_code="smooth_dc",
            selected_rated_residual_current_ma=100,
            normal_leakage_current_ma=50,
        ),
        EarthingSystem.TN_S,
        approved_rules(),
    )
    assert result.outputs["waveform"]["rcd_type"] == "B型"
    assert result.outputs["leakage_coordination"]["status"] == FAIL
    assert result.provisional_status == FAIL


def test_not_required_decision_needs_reference_and_stays_nonformal():
    missing = evaluate_rcd_protection(
        RcdProtectionInput(required=False),
        EarthingSystem.TN_S,
        approved_rules(),
    )
    referenced = evaluate_rcd_protection(
        RcdProtectionInput(
            required=False,
            applicability_reference="项目采用条件核对记录RCD-01",
        ),
        EarthingSystem.TN_S,
        approved_rules(),
    )
    assert missing.provisional_status == UNKNOWN
    assert referenced.provisional_status == PASS
    assert referenced.status == UNKNOWN
