"""断路器极数、N极方式及PEN禁开断的独立校核。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .complete_circuit import Phase
from .engine import FAIL, Outcome, PASS, UNKNOWN


ENGINE_VERSION = "0.1.0"
_NEUTRAL_MODES = {
    "unconfirmed",
    "absent",
    "not_switched",
    "switched_unprotected",
    "switched_protected",
}


@dataclass(frozen=True)
class PoleAndNeutralInput:
    neutral_required: bool | None = None
    neutral_pole_mode: str = "unconfirmed"
    pen_conductor_present: bool | None = None
    pen_switched_or_isolated: bool | None = None


def _check(status: str, basis: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "basis": basis, **values}


def evaluate_pole_and_neutral_configuration(
    data: PoleAndNeutralInput,
    *,
    phase: Phase,
    selected_poles: str | None,
    available_pole_options: tuple[str, ...],
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """Check structural pole/N consistency without deriving poles from cable cores."""

    warnings: list[str] = []
    outputs: dict[str, Any] = {}
    rule_codes = ["ELEC.BREAKER.RATING"]

    if selected_poles in {None, "", "unconfirmed"}:
        outputs["pole_selection"] = _check(
            UNKNOWN,
            "断路器极数尚未确认。",
            available_pole_options=list(available_pole_options),
        )
    else:
        phase_options = (
            {"1P", "1P+N", "2P"}
            if phase == Phase.SINGLE
            else {"3P", "4P"}
        )
        if selected_poles not in available_pole_options:
            outputs["pole_selection"] = _check(
                FAIL,
                "所选极数不在该断路器参数组的表列选项中。",
                selected_poles=selected_poles,
                available_pole_options=list(available_pole_options),
            )
        elif selected_poles not in phase_options:
            outputs["pole_selection"] = _check(
                FAIL,
                "所选极数与回路相制结构不一致。",
                selected_poles=selected_poles,
                phase=phase.value,
            )
        else:
            outputs["pole_selection"] = _check(
                PASS,
                "所选极数与相制结构及当前参数组相符。",
                selected_poles=selected_poles,
                phase=phase.value,
            )

    mode = data.neutral_pole_mode
    if mode not in _NEUTRAL_MODES:
        outputs["neutral_pole"] = _check(
            FAIL,
            "neutral_pole_mode不在允许枚举中。",
            neutral_pole_mode=mode,
        )
    elif data.neutral_required is None:
        outputs["neutral_pole"] = _check(
            UNKNOWN,
            "尚未明确该回路是否需要中性线。",
        )
    elif data.neutral_required is False:
        status = PASS if mode in {"absent", "unconfirmed"} else FAIL
        outputs["neutral_pole"] = _check(
            status,
            (
                "回路不需要中性线。"
                if status == PASS
                else "回路声明不需要中性线，但又配置了N极方式。"
            ),
            neutral_pole_mode=mode,
        )
    elif mode == "unconfirmed":
        outputs["neutral_pole"] = _check(
            UNKNOWN,
            "回路需要中性线，但N线是否随开关断开及N极保护方式尚未确认。",
        )
    elif mode == "absent":
        outputs["neutral_pole"] = _check(
            FAIL,
            "回路需要中性线，不能将N极方式设为absent。",
        )
    elif selected_poles in {"1P", "3P"} and mode != "not_switched":
        outputs["neutral_pole"] = _check(
            FAIL,
            "1P/3P配置不包含可开断N极，与所填N极方式矛盾。",
        )
    elif selected_poles == "1P+N" and mode != "switched_unprotected":
        outputs["neutral_pole"] = _check(
            FAIL,
            "1P+N配置的N极方式应明确为随动开断且不设过电流保护。",
        )
    elif selected_poles in {"2P", "4P"} and mode not in {
        "switched_unprotected",
        "switched_protected",
    }:
        outputs["neutral_pole"] = _check(
            FAIL,
            "2P/4P配置需明确N极为开断不保护或开断并保护。",
        )
    elif selected_poles in {None, "", "unconfirmed"}:
        outputs["neutral_pole"] = _check(
            UNKNOWN,
            "需先确认断路器极数，才能核对N极方式。",
        )
    else:
        outputs["neutral_pole"] = _check(
            PASS,
            "N极方式与所选极数及中性线需求结构一致。",
            neutral_pole_mode=mode,
        )

    if data.pen_conductor_present is None:
        outputs["pen"] = _check(
            UNKNOWN,
            "尚未确认保护点是否有PEN导体通过。",
        )
    elif data.pen_conductor_present is False:
        outputs["pen"] = _check(
            PASS,
            "保护点无PEN导体通过。",
        )
    else:
        rule_codes.append("ELEC.PEN.NO_SWITCHING")
        if data.pen_switched_or_isolated is True:
            outputs["pen"] = _check(
                FAIL,
                "PEN导体中设置了开关或隔离器件。",
            )
            warnings.append("PEN导体中不应设置任何开关或隔离器件。")
        elif data.pen_switched_or_isolated is False:
            outputs["pen"] = _check(
                PASS,
                "PEN导体连续通过，未设置开关或隔离器件。",
            )
        else:
            outputs["pen"] = _check(
                UNKNOWN,
                "有PEN导体通过，但尚未确认其是否被开关或隔离。",
            )

    statuses = [
        value["status"]
        for value in outputs.values()
        if isinstance(value, dict) and "status" in value
    ]
    if FAIL in statuses:
        provisional = FAIL
    elif statuses and all(status == PASS for status in statuses):
        provisional = PASS
    else:
        provisional = UNKNOWN
    formal = (
        provisional
        if provisional != UNKNOWN
        and all(
            rules.get(code, {}).get("status") == "approved"
            for code in rule_codes
        )
        else UNKNOWN
    )
    if provisional != UNKNOWN and formal == UNKNOWN:
        warnings.append("极数/N极/PEN相关依据尚未全部批准，不能形成正式结论。")
    return Outcome(
        "断路器极数与N极",
        ENGINE_VERSION,
        formal,
        provisional,
        outputs,
        [],
        warnings,
        rule_codes,
    )
