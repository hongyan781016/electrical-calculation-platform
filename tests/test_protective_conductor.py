from src.electrical_calc.engine import FAIL, PASS, UNKNOWN
from src.electrical_calc.protective_conductor import (
    calculate_pe_minimum_section_by_table,
)


def calculate(phase_section, pe_section, **changes):
    data = {
        "phase_conductor_section_mm2": phase_section,
        "phase_conductor_material": "copper",
        "protective_conductor_material": "copper",
        "protective_conductor_section_mm2": pe_section,
        "separate_protective_conductor": False,
    }
    data.update(changes)
    return calculate_pe_minimum_section_by_table(data, {})


def test_table_54_2_same_material_boundaries():
    assert calculate(16, 16).outputs["required_minimum_pe_section_mm2"] == 16
    assert calculate(25, 16).outputs["required_minimum_pe_section_mm2"] == 16
    assert calculate(50, 25).outputs["required_minimum_pe_section_mm2"] == 25


def test_table_54_2_rejects_undersized_pe():
    result = calculate(50, 16)
    assert result.provisional_status == FAIL
    assert result.status == UNKNOWN


def test_separate_pe_applies_mechanical_minimum():
    result = calculate(
        2.5,
        2.5,
        separate_protective_conductor=True,
        mechanical_damage_protected=False,
    )
    assert result.outputs["required_minimum_pe_section_mm2"] == 4
    assert result.provisional_status == FAIL


def test_separate_pe_requires_mechanical_condition():
    result = calculate(
        6,
        6,
        separate_protective_conductor=True,
        mechanical_damage_protected=None,
    )
    assert result.provisional_status == UNKNOWN
    assert "机械" in result.warnings[0]


def test_approved_rule_can_produce_formal_pass():
    result = calculate_pe_minimum_section_by_table(
        {
            "phase_conductor_section_mm2": 35,
            "phase_conductor_material": "copper",
            "protective_conductor_material": "copper",
            "protective_conductor_section_mm2": 16,
            "separate_protective_conductor": False,
        },
        {"ELEC.PE.MIN_SECTION.TABLE54_2": {"status": "approved"}},
    )
    assert result.provisional_status == PASS
    assert result.status == PASS
