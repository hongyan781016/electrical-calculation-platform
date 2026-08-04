"""剩余电流保护的用途参数、波形类型和TN接线独立校核。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import DEFAULT_CATALOG
from .complete_circuit import EarthingSystem
from .engine import FAIL, Outcome, PASS, Step, UNKNOWN


ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class RcdProtectionInput:
    required: bool | None = None
    applicability_reference: str | None = None
    scenario_code: str | None = None
    residual_waveform_code: str | None = None
    selected_rated_residual_current_ma: float | None = None
    normal_leakage_current_ma: float | None = None
    downstream_of_pen_split: bool | None = None


def _subcheck(status: str, basis: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "basis": basis, **values}


def evaluate_rcd_protection(
    data: RcdProtectionInput,
    earthing_system: EarthingSystem,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """Evaluate RCD design requirements without inventing applicability."""

    catalog = catalog or DEFAULT_CATALOG
    rcd_catalog = catalog.get("rcd_parameters", {})
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {}
    rule_codes: list[str] = []

    if data.required is None:
        outputs["applicability"] = _subcheck(
            UNKNOWN,
            "尚未判定该回路是否需要RCD。",
        )
        warnings.append("RCD适用性不能仅由负荷电流确定，需结合回路用途和场所。")
        return Outcome(
            "RCD独立校核",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    if data.required is False:
        if not (data.applicability_reference or "").strip():
            outputs["applicability"] = _subcheck(
                UNKNOWN,
                "已选择不设置RCD，但未提供适用性判定依据。",
            )
            warnings.append("不设置RCD必须记录适用性判定依据。")
            provisional = UNKNOWN
        else:
            outputs["applicability"] = _subcheck(
                PASS,
                str(data.applicability_reference).strip(),
                rcd_required=False,
            )
            provisional = PASS
        # 自由文本依据尚未绑定已批准规则，不产生正式通过。
        return Outcome(
            "RCD独立校核",
            ENGINE_VERSION,
            UNKNOWN,
            provisional,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    rule_codes.append("ELEC.RCD.PARAMETERS")
    outputs["applicability"] = _subcheck(
        PASS,
        (data.applicability_reference or "用户明确该回路采用RCD。").strip(),
        rcd_required=True,
    )

    scenario = rcd_catalog.get("scenarios", {}).get(data.scenario_code or "")
    if scenario is None:
        outputs["scenario"] = _subcheck(
            UNKNOWN,
            "尚未从已核实用途档位中选择RCD参数。",
        )
        warnings.append("采用RCD时必须明确用途/场所档位。")
    else:
        outputs["scenario"] = _subcheck(
            PASS,
            scenario["name"],
            scenario_code=data.scenario_code,
            rated_residual_current_max_ma=scenario[
                "rated_residual_current_max_ma"
            ],
            delay=scenario["delay"],
            source=rcd_catalog.get("source"),
            table=rcd_catalog.get("table"),
            page=rcd_catalog.get("page"),
        )

    waveform = rcd_catalog.get("waveform_types", {}).get(
        data.residual_waveform_code or ""
    )
    if waveform is None:
        outputs["waveform"] = _subcheck(
            UNKNOWN,
            "负载剩余电流波形尚未确认。",
        )
        warnings.append("RCD类型必须按负载剩余电流波形确定。")
    else:
        outputs["waveform"] = _subcheck(
            PASS,
            waveform["label"],
            waveform_code=data.residual_waveform_code,
            rcd_type=waveform["rcd_type"],
        )

    if earthing_system == EarthingSystem.TN_C_S:
        if "ELEC.EARTH_FAULT.RCD.TN_ARRANGEMENT" not in rule_codes:
            rule_codes.append("ELEC.EARTH_FAULT.RCD.TN_ARRANGEMENT")
        if data.downstream_of_pen_split is True:
            outputs["tn_arrangement"] = _subcheck(
                PASS,
                "RCD位于N线与PE线分开后的部分。",
            )
        elif data.downstream_of_pen_split is False:
            outputs["tn_arrangement"] = _subcheck(
                FAIL,
                "RCD不在N线与PE线分开后的部分。",
            )
            warnings.append("TN-C-S系统中的RCD只允许用于N线与PE线分开后的部分。")
        else:
            outputs["tn_arrangement"] = _subcheck(
                UNKNOWN,
                "尚未确认RCD是否位于PEN分开点之后。",
            )
            warnings.append("TN-C-S系统采用RCD时必须确认PEN分开点和安装位置。")
    else:
        outputs["tn_arrangement"] = _subcheck(
            PASS,
            "TN-S系统无PEN分开点位置条件；仍须按实际N、PE接线核对。",
        )

    selected = data.selected_rated_residual_current_ma
    maximum = (
        float(scenario["rated_residual_current_max_ma"])
        if scenario is not None
        else None
    )
    if selected is None:
        outputs["rated_residual_current"] = _subcheck(
            UNKNOWN,
            "尚未确定实际额定剩余动作电流。",
            required_maximum_ma=maximum,
        )
    elif selected <= 0:
        outputs["rated_residual_current"] = _subcheck(
            FAIL,
            "额定剩余动作电流必须大于0。",
            selected_ma=selected,
            required_maximum_ma=maximum,
        )
    elif maximum is None:
        outputs["rated_residual_current"] = _subcheck(
            UNKNOWN,
            "缺少用途档位上限，无法校核实际动作值。",
            selected_ma=selected,
        )
    else:
        status = PASS if selected <= maximum else FAIL
        outputs["rated_residual_current"] = _subcheck(
            status,
            "IΔn不大于用途档位表列上限。",
            selected_ma=selected,
            required_maximum_ma=maximum,
        )
        steps.append(
            Step(
                "RCD额定剩余动作电流",
                "IΔn≤表列上限",
                status,
            )
        )

    leakage = data.normal_leakage_current_ma
    if selected is None or leakage is None:
        outputs["leakage_coordination"] = _subcheck(
            UNKNOWN,
            "需同时取得实际IΔn和正常泄漏电流。",
        )
    elif leakage < 0:
        outputs["leakage_coordination"] = _subcheck(
            FAIL,
            "正常泄漏电流不能为负数。",
        )
    else:
        status = PASS if selected > 2 * leakage else FAIL
        outputs["leakage_coordination"] = _subcheck(
            status,
            "额定剩余动作电流应大于正常泄漏电流的2倍。",
            selected_rated_residual_current_ma=selected,
            normal_leakage_current_ma=leakage,
        )
        steps.append(
            Step(
                "正常泄漏电流配合",
                "IΔn>2×I泄漏",
                status,
            )
        )

    outputs["selection_checks"] = list(
        rcd_catalog.get("selection_checks", [])
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
        and rule_codes
        and all(
            rules.get(code, {}).get("status") == "approved"
            for code in rule_codes
        )
        else UNKNOWN
    )
    if provisional != UNKNOWN and formal == UNKNOWN:
        warnings.append("RCD相关依据尚未全部批准，不能形成正式结论。")
    return Outcome(
        "RCD独立校核",
        ENGINE_VERSION,
        formal,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )
