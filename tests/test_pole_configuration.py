from __future__ import annotations

from src.electrical_calc.complete_circuit import Phase
from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.pole_configuration import (
    PoleAndNeutralInput,
    evaluate_pole_and_neutral_configuration,
)


def approved_rules() -> dict[str, dict[str, str]]:
    return {
        "ELEC.BREAKER.RATING": {"status": "approved"},
        "ELEC.PEN.NO_SWITCHING": {"status": "approved"},
    }


def test_single_phase_1p_plus_n_requires_matching_n_pole_mode():
    result = evaluate_pole_and_neutral_configuration(
        PoleAndNeutralInput(
            neutral_required=True,
            neutral_pole_mode="switched_unprotected",
            pen_conductor_present=False,
        ),
        phase=Phase.SINGLE,
        selected_poles="1P+N",
        available_pole_options=("1P", "1P+N", "2P"),
        rules=approved_rules(),
    )
    assert result.outputs["pole_selection"]["status"] == PASS
    assert result.outputs["neutral_pole"]["status"] == PASS
    assert result.provisional_status == PASS
    assert result.status == PASS


def test_three_phase_four_pole_rejects_not_switched_n_mode():
    result = evaluate_pole_and_neutral_configuration(
        PoleAndNeutralInput(
            neutral_required=True,
            neutral_pole_mode="not_switched",
            pen_conductor_present=False,
        ),
        phase=Phase.THREE,
        selected_poles="4P",
        available_pole_options=("3P", "4P"),
        rules=approved_rules(),
    )
    assert result.outputs["neutral_pole"]["status"] == FAIL
    assert result.provisional_status == FAIL


def test_pole_count_is_checked_against_phase_structure():
    result = evaluate_pole_and_neutral_configuration(
        PoleAndNeutralInput(
            neutral_required=True,
            neutral_pole_mode="switched_protected",
            pen_conductor_present=False,
        ),
        phase=Phase.SINGLE,
        selected_poles="4P",
        available_pole_options=("3P", "4P"),
        rules=approved_rules(),
    )
    assert result.outputs["pole_selection"]["status"] == FAIL


def test_pen_must_not_be_switched_or_isolated():
    result = evaluate_pole_and_neutral_configuration(
        PoleAndNeutralInput(
            neutral_required=False,
            neutral_pole_mode="absent",
            pen_conductor_present=True,
            pen_switched_or_isolated=True,
        ),
        phase=Phase.THREE,
        selected_poles="3P",
        available_pole_options=("3P", "4P"),
        rules=approved_rules(),
    )
    assert result.outputs["pen"]["status"] == FAIL
    assert result.provisional_status == FAIL
    assert any("不应设置任何开关" in item for item in result.warnings)


def test_unconfirmed_poles_and_n_conditions_stay_unknown():
    result = evaluate_pole_and_neutral_configuration(
        PoleAndNeutralInput(),
        phase=Phase.THREE,
        selected_poles=None,
        available_pole_options=("3P", "4P"),
        rules=approved_rules(),
    )
    assert result.outputs["pole_selection"]["status"] == UNKNOWN
    assert result.outputs["neutral_pole"]["status"] == UNKNOWN
    assert result.provisional_status == UNKNOWN
