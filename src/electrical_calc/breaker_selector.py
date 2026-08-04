"""不绑定品牌的断路器设计参数候选生成器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import DEFAULT_CATALOG
from .complete_circuit import CircuitApplication, Phase
from .engine import FAIL, Outcome, PASS, Step, UNKNOWN


ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class BreakerSelectionRequest:
    node_id: str
    circuit_application: CircuitApplication
    phase: Phase
    system_voltage_v: float
    design_current_a: float
    conductor_corrected_ampacity_a: float
    allowed_families: tuple[str, ...]
    pole_requirement: str
    prospective_short_circuit_ka: float | None = None
    minimum_fault_current_a: float | None = None
    mcb_trip_curve: str | None = None
    ics_requirement_mode: str = "unconfirmed"
    short_time_withstand_required: bool | None = None
    short_time_delay_s: float | None = None


def _rule_approved(rules: dict[str, dict[str, Any]], code: str) -> bool:
    return rules.get(code, {}).get("status") == "approved"


def _voltage_limit(family: str) -> float:
    return 400.0 if family in {"MCB", "MCCB", "ACB"} else 0.0


def _mcb_action_current(
    curve: str | None,
    rated_current_a: float,
) -> tuple[float | None, str | None]:
    if curve == "B":
        return 5 * rated_current_a, "B型按保证瞬时动作边界5In"
    if curve == "C":
        return 10 * rated_current_a, "C型按保证瞬时动作边界10In"
    return None, None


def _ics_options_for_selected_icu(
    group: dict[str, Any],
    selected_icu_ka: float | None,
) -> list[float]:
    if selected_icu_ka is None:
        return []
    pairs = group.get("icu_ics_pairs_ka")
    if pairs:
        return sorted(
            {
                float(ics)
                for icu, ics in pairs
                if float(icu) == selected_icu_ka
            }
        )
    percentages = group.get("ics_percent_icu", [])
    return sorted(
        {
            round(selected_icu_ka * float(percent) / 100, 6)
            for percent in percentages
        }
    )


def generate_breaker_candidates(
    request: BreakerSelectionRequest,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """Generate generic MCB/MCCB/ACB parameter candidates and rejection reasons."""

    catalog = catalog or DEFAULT_CATALOG
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "candidates": [],
        "rejected_candidates": [],
    }
    rule_codes = ["ELEC.BREAKER.RATING", "ELEC.CABLE.COORDINATION"]

    if not request.node_id.strip():
        warnings.append("保护安装节点ID不能为空。")
    if request.system_voltage_v <= 0:
        warnings.append("系统电压必须大于0。")
    if request.design_current_a <= 0:
        warnings.append("计算电流必须大于0。")
    if request.conductor_corrected_ampacity_a <= 0:
        warnings.append("导体修正后载流量必须大于0。")
    allowed = tuple(dict.fromkeys(family.upper() for family in request.allowed_families))
    invalid_families = [
        family for family in allowed if family not in {"MCB", "MCCB", "ACB"}
    ]
    if not allowed or invalid_families:
        warnings.append("允许的断路器类别必须从MCB、MCCB、ACB中明确选择。")
    if request.pole_requirement not in {
        "unconfirmed",
        "1P",
        "1P+N",
        "2P",
        "3P",
        "4P",
    }:
        warnings.append("断路器极数条件无效。")
    if request.prospective_short_circuit_ka is not None:
        if request.prospective_short_circuit_ka <= 0:
            warnings.append("安装点最大预期短路电流必须大于0。")
        else:
            rule_codes.append("ELEC.BREAKING.CAPACITY")
    if request.minimum_fault_current_a is not None and request.minimum_fault_current_a <= 0:
        warnings.append("线路末端最小故障电流必须大于0。")
    if request.mcb_trip_curve not in {None, "", "B", "C", "D"}:
        warnings.append("MCB脱扣曲线仅接受B、C、D或留空。")
    if request.mcb_trip_curve == "D":
        warnings.append("普通回路不采用无具体产品依据的通用D型保证动作值。")
    if request.ics_requirement_mode not in {
        "unconfirmed",
        "at_least_prospective_fault",
    }:
        warnings.append("Ics要求模式仅支持unconfirmed或at_least_prospective_fault。")
    if request.short_time_delay_s is not None and request.short_time_delay_s <= 0:
        warnings.append("短延时时间必须大于0。")
    if warnings:
        return Outcome(
            "断路器设计参数候选",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    parameters = catalog.get("breaker_parameters", {})
    if parameters.get("status") not in {"verified", "approved"}:
        warnings.append("断路器设计参数目录尚未核实。")
        return Outcome(
            "断路器设计参数候选",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    pole_known = request.pole_requirement != "unconfirmed"
    breaking_known = request.prospective_short_circuit_ka is not None
    any_trip_check_complete = False

    for family in allowed:
        family_data = parameters.get("families", {}).get(family)
        if not family_data:
            continue
        if request.system_voltage_v > _voltage_limit(family):
            outputs["rejected_candidates"].append(
                {
                    "family": family,
                    "reason_code": "rated_voltage_insufficient",
                    "reason": (
                        f"系统电压{request.system_voltage_v:g}V超过当前"
                        f"{family}参数目录电压范围。"
                    ),
                }
            )
            continue
        if family != "MCB" and request.mcb_trip_curve:
            warnings.append(f"{family}不采用MCB的B/C/D曲线参数。")

        for group in family_data.get("groups", []):
            pole_options = tuple(group.get("pole_options", ()))
            if pole_known and request.pole_requirement not in pole_options:
                continue
            for rating in group.get("ratings_a", []):
                rated_current = float(rating)
                candidate_identity = {
                    "family": family,
                    "frame_current_a": float(group["frame_a"]),
                    "rated_current_a": rated_current,
                }
                if rated_current < request.design_current_a:
                    outputs["rejected_candidates"].append(
                        {
                            **candidate_identity,
                            "reason_code": "rated_current_below_load",
                            "reason": (
                                f"In={rated_current:g}A小于"
                                f"Ib={request.design_current_a:g}A。"
                            ),
                        }
                    )
                    continue
                if rated_current > request.conductor_corrected_ampacity_a:
                    outputs["rejected_candidates"].append(
                        {
                            **candidate_identity,
                            "reason_code": "rated_current_above_conductor",
                            "reason": (
                                f"In={rated_current:g}A大于"
                                f"Iz={request.conductor_corrected_ampacity_a:g}A。"
                            ),
                        }
                    )
                    continue

                icu_options = sorted(float(value) for value in group.get("icu_ka", []))
                selected_icu = None
                if request.prospective_short_circuit_ka is not None:
                    selected_icu = next(
                        (
                            value
                            for value in icu_options
                            if value >= request.prospective_short_circuit_ka
                        ),
                        None,
                    )
                    if selected_icu is None:
                        outputs["rejected_candidates"].append(
                            {
                                **candidate_identity,
                                "reason_code": "breaking_capacity_insufficient",
                                "reason": (
                                    f"表列最大Icu={max(icu_options, default=0):g}kA"
                                    f"小于安装点{request.prospective_short_circuit_ka:g}kA。"
                                ),
                            }
                        )
                        continue

                action_current = action_note = None
                automatic_trip_status = UNKNOWN
                if family == "MCB":
                    action_current, action_note = _mcb_action_current(
                        request.mcb_trip_curve,
                        rated_current,
                    )
                    if action_current is not None:
                        if "ELEC.BREAKER.MCB.INSTANTANEOUS" not in rule_codes:
                            rule_codes.append("ELEC.BREAKER.MCB.INSTANTANEOUS")
                        if request.minimum_fault_current_a is not None:
                            any_trip_check_complete = True
                            automatic_trip_status = (
                                PASS
                                if request.minimum_fault_current_a >= action_current
                                else FAIL
                            )
                            if automatic_trip_status == FAIL:
                                outputs["rejected_candidates"].append(
                                    {
                                        **candidate_identity,
                                        "reason_code": "automatic_trip_not_guaranteed",
                                        "reason": (
                                            f"最小故障电流"
                                            f"{request.minimum_fault_current_a:g}A"
                                            f"小于保证动作电流{action_current:g}A。"
                                        ),
                                    }
                                )
                                continue

                candidate_rule_codes = list(rule_codes)
                if "ELEC.BREAKER.ICS.ICW.REFERENCE" not in candidate_rule_codes:
                    candidate_rule_codes.append(
                        "ELEC.BREAKER.ICS.ICW.REFERENCE"
                    )
                if "ELEC.BREAKER.ICS.ICW.REFERENCE" not in rule_codes:
                    rule_codes.append("ELEC.BREAKER.ICS.ICW.REFERENCE")
                ics_options = _ics_options_for_selected_icu(
                    group,
                    selected_icu,
                )
                selected_ics = None
                ics_status = UNKNOWN
                if (
                    request.ics_requirement_mode
                    == "at_least_prospective_fault"
                    and request.prospective_short_circuit_ka is not None
                ):
                    selected_ics = next(
                        (
                            value
                            for value in ics_options
                            if value
                            >= request.prospective_short_circuit_ka
                        ),
                        None,
                    )
                    ics_status = PASS if selected_ics is not None else FAIL
                    if ics_status == FAIL:
                        outputs["rejected_candidates"].append(
                            {
                                **candidate_identity,
                                "reason_code": "service_breaking_capacity_insufficient",
                                "reason": (
                                    "当前Icu档位对应的表列Ics均小于"
                                    f"{request.prospective_short_circuit_ka:g}kA。"
                                ),
                            }
                        )
                        continue

                icw_status = UNKNOWN
                icw_1s = group.get("icw_1s_ka")
                icw_table_status = group.get(
                    "icw_1s_status", "not_tabulated"
                )
                if request.short_time_withstand_required is False:
                    icw_status = PASS
                elif request.short_time_withstand_required is True:
                    if (
                        request.short_time_delay_s is None
                        or request.short_time_delay_s > 1
                    ):
                        icw_status = UNKNOWN
                    elif (
                        icw_1s is not None
                        and request.prospective_short_circuit_ka is not None
                    ):
                        icw_status = (
                            PASS
                            if float(icw_1s)
                            >= request.prospective_short_circuit_ka
                            else FAIL
                        )
                        if icw_status == FAIL:
                            outputs["rejected_candidates"].append(
                                {
                                    **candidate_identity,
                                    "reason_code": "short_time_withstand_insufficient",
                                    "reason": (
                                        f"Icw/1s={float(icw_1s):g}kA小于"
                                        f"安装点{request.prospective_short_circuit_ka:g}kA。"
                                    ),
                                }
                            )
                            continue
                    elif icw_table_status == "not_applicable":
                        outputs["rejected_candidates"].append(
                            {
                                **candidate_identity,
                                "reason_code": "short_time_withstand_not_available",
                                "reason": "该表列参数组不提供Icw，不能用于所选短延时策略。",
                            }
                        )
                        continue
                critical_known = (
                    breaking_known
                    and pole_known
                    and ics_status == PASS
                    and icw_status == PASS
                )
                if family == "MCB":
                    critical_known = (
                        critical_known
                        and action_current is not None
                        and request.minimum_fault_current_a is not None
                    )
                else:
                    critical_known = False
                candidate_formal = (
                    PASS
                    if critical_known
                    and all(_rule_approved(rules, code) for code in candidate_rule_codes)
                    else UNKNOWN
                )
                outputs["candidates"].append(
                    {
                        "candidate_id": (
                            f"{request.node_id}:{family}:"
                            f"{float(group['frame_a']):g}:{rated_current:g}"
                        ),
                        **candidate_identity,
                        "rated_voltage_v": family_data.get("rated_voltage_v"),
                        "pole_options": list(pole_options),
                        "adopted_poles": (
                            request.pole_requirement if pole_known else None
                        ),
                        "icu_options_ka": icu_options,
                        "selected_icu_ka": selected_icu,
                        "ics_options_ka": ics_options,
                        "selected_ics_ka": selected_ics,
                        "ics_status": ics_status,
                        "icw_1s_ka": (
                            float(icw_1s) if icw_1s is not None else None
                        ),
                        "icw_table_status": icw_table_status,
                        "icw_status": icw_status,
                        "mcb_trip_curve": (
                            request.mcb_trip_curve if family == "MCB" else None
                        ),
                        "guaranteed_action_current_a": action_current,
                        "action_current_basis": action_note,
                        "automatic_trip_status": automatic_trip_status,
                        "current_coordination_status": PASS,
                        "provisional_status": PASS,
                        "formal_status": candidate_formal,
                        "source": parameters.get("source"),
                        "table": family_data.get("table"),
                        "page": family_data.get("page"),
                        "pending_checks": [
                            item
                            for item in (
                                None if pole_known else "极数及N极处理",
                                None if breaking_known else "安装点最大预期短路电流与Icu",
                                (
                                    None
                                    if ics_status == PASS
                                    else "Ics采用策略及档位"
                                ),
                                (
                                    None
                                    if icw_status == PASS
                                    else "Icw及短延时条件（适用时）"
                                ),
                                (
                                    None
                                    if family == "MCB" and any_trip_check_complete
                                    else "实际脱扣/整定特性"
                                ),
                                "导体短路热稳定",
                                "上下级选择性与后备保护",
                                "RCD条件（适用时）",
                            )
                            if item
                        ],
                    }
                )

    outputs["required_parameters"] = {
        "rated_current_min_a": request.design_current_a,
        "rated_current_max_a": request.conductor_corrected_ampacity_a,
        "required_breaking_capacity_ka": request.prospective_short_circuit_ka,
        "minimum_fault_current_a": request.minimum_fault_current_a,
        "pole_requirement": request.pole_requirement,
        "ics_requirement_mode": request.ics_requirement_mode,
        "short_time_withstand_required": (
            request.short_time_withstand_required
        ),
        "short_time_delay_s": request.short_time_delay_s,
    }
    if outputs["candidates"]:
        steps.append(
            Step(
                "断路器设计参数候选",
                "Ib≤In≤Iz，并按安装点短路电流筛选Icu",
                len(outputs["candidates"]),
                "组",
            )
        )
    else:
        warnings.append("当前参数目录中没有满足已知硬条件的断路器候选。")
    if not breaking_known:
        warnings.append("缺少安装点最大预期短路电流，Icu只能列出档位，不能确定采用值。")
    if not pole_known:
        warnings.append("极数及N极处理尚未确认；系统只列出表中可选极数。")
    if any(family in {"MCCB", "ACB"} for family in allowed):
        warnings.append("MCCB/ACB必须按具体脱扣器整定和产品曲线继续校核。")

    provisional = PASS if outputs["candidates"] else UNKNOWN
    formal = (
        PASS
        if outputs["candidates"]
        and all(item["formal_status"] == PASS for item in outputs["candidates"])
        else UNKNOWN
    )
    return Outcome(
        "断路器设计参数候选",
        ENGINE_VERSION,
        formal,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )
