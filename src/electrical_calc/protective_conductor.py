"""保护接地导体最小截面积的独立判定。"""

from __future__ import annotations

from typing import Any

from .engine import FAIL, Outcome, PASS, Step, UNKNOWN


RULE_CODE = "ELEC.PE.MIN_SECTION.TABLE54_2"
ENGINE_VERSION = "0.1.0"


def calculate_pe_minimum_section_by_table(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """Apply GB/T 16895.3-2017 table 54.2 for same-material copper PE."""

    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "scope": "铜芯线导体与铜芯保护接地导体，按表54.2选择",
        "source": "GB/T 16895.3-2017",
        "clause": "543.1.1、543.1.3、表54.2",
        "location": "CHM /规范/GB16895.3-2017/03.htm",
    }
    try:
        phase_section = float(data["phase_conductor_section_mm2"])
    except (KeyError, TypeError, ValueError):
        phase_section = 0
    phase_material = str(data.get("phase_conductor_material") or "").lower()
    pe_material = str(data.get("protective_conductor_material") or "").lower()
    separate = data.get("separate_protective_conductor")
    mechanical_protection = data.get("mechanical_damage_protected")
    selected = data.get("protective_conductor_section_mm2")

    if phase_section <= 0:
        warnings.append("相导体截面积必须大于0。")
    if phase_material != "copper" or pe_material != "copper":
        warnings.append("当前表54.2自动路径只接入线导体与PE均为铜芯的情况。")
    if separate not in {True, False}:
        warnings.append("必须确认PE是否为电缆组成部分或与线导体共处同一外护物。")
    if separate is True and mechanical_protection not in {True, False}:
        warnings.append("独立PE必须确认是否有防机械损伤保护。")
    if warnings:
        return Outcome(
            "PE最小截面积",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            [RULE_CODE],
        )

    if phase_section <= 16:
        table_minimum = phase_section
        table_basis = "S≤16mm²时，SPE=S"
    elif phase_section <= 35:
        table_minimum = 16.0
        table_basis = "16mm²＜S≤35mm²时，SPE=16mm²"
    else:
        table_minimum = phase_section / 2
        table_basis = "S＞35mm²时，SPE=S/2"

    mechanical_minimum = 0.0
    if separate is True:
        mechanical_minimum = 2.5 if mechanical_protection else 4.0
    required = max(table_minimum, mechanical_minimum)
    outputs.update(
        {
            "phase_conductor_section_mm2": phase_section,
            "table_minimum_pe_section_mm2": table_minimum,
            "table_basis": table_basis,
            "mechanical_minimum_pe_section_mm2": mechanical_minimum,
            "required_minimum_pe_section_mm2": required,
            "separate_protective_conductor": separate,
            "mechanical_damage_protected": mechanical_protection,
        }
    )
    steps.append(
        Step("表54.2最小截面", table_basis, table_minimum, "mm²")
    )
    if separate is True:
        steps.append(
            Step(
                "独立PE机械强度最小截面",
                "543.1.3",
                mechanical_minimum,
                "mm²",
            )
        )

    provisional = PASS
    if selected is not None:
        try:
            selected_value = float(selected)
        except (TypeError, ValueError):
            selected_value = 0
        outputs["selected_protective_conductor_section_mm2"] = selected_value
        provisional = PASS if selected_value >= required else FAIL
        steps.append(
            Step("PE截面校核", "SPE,选≥SPE,min", provisional)
        )
    outputs["provisional_status"] = provisional
    formal = (
        provisional
        if rules.get(RULE_CODE, {}).get("status") == "approved"
        else UNKNOWN
    )
    return Outcome(
        "PE最小截面积",
        ENGINE_VERSION,
        formal,
        provisional,
        outputs,
        steps,
        warnings,
        [RULE_CODE],
    )
