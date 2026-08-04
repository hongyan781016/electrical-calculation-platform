"""按回路用途解析电压降、自动切断、RCD与断路器搜索策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import DEFAULT_CATALOG
from .combination_solver import ProtectionPoint
from .complete_circuit import (
    CircuitApplication,
    ConnectionMode,
    EarthingSystem,
    LoadProfile,
    Phase,
)
from .engine import Outcome, PASS, Step, UNKNOWN
from .rcd_protection import (
    RcdProtectionInput,
    evaluate_rcd_protection,
)
from .pole_configuration import PoleAndNeutralInput


ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class CircuitStrategyRequest:
    circuit_application: CircuitApplication
    load_profile: LoadProfile
    phase: Phase
    earthing_system: EarthingSystem
    line_to_earth_voltage_v: float
    circuit_rated_current_a: float | None = None
    connection_mode: ConnectionMode | None = None
    supplies_lighting: bool | None = None
    neutral_required: bool | None = None
    rcd_scenario: str | None = None
    residual_current_waveform: str | None = None
    rcd_required: bool | None = None
    rcd_applicability_reference: str | None = None
    selected_rated_residual_current_ma: float | None = None
    normal_leakage_current_ma: float | None = None
    rcd_downstream_of_pen_split: bool | None = None
    neutral_pole_mode: str = "unconfirmed"
    pen_conductor_present: bool | None = None
    pen_switched_or_isolated: bool | None = None
    ics_requirement_mode: str = "unconfirmed"
    short_time_withstand_required: bool | None = None
    short_time_delay_s: float | None = None


def _approved(rules: dict[str, dict[str, Any]], codes: list[str]) -> bool:
    return bool(codes) and all(
        rules.get(code, {}).get("status") == "approved" for code in codes
    )


def _resolve_connection_mode(
    request: CircuitStrategyRequest,
    warnings: list[str],
) -> tuple[ConnectionMode | None, str | None]:
    expected: ConnectionMode | None = None
    mapping_note: str | None = None
    if request.circuit_application == CircuitApplication.SOCKET_FINAL:
        expected = ConnectionMode.SOCKET
    elif request.circuit_application == CircuitApplication.DISTRIBUTION:
        expected = ConnectionMode.DISTRIBUTION
    elif request.circuit_application == CircuitApplication.LIGHTING_FINAL:
        expected = ConnectionMode.FIXED_CONNECTED
        mapping_note = "平台用途映射：照明终端回路按固定连接用电设备回路处理。"

    if request.connection_mode is not None:
        if expected is not None and request.connection_mode != expected:
            warnings.append(
                "connection_mode与circuit_application冲突，不能确定自动切断时间。"
            )
            return None, None
        return request.connection_mode, mapping_note
    if expected is not None:
        return expected, mapping_note
    warnings.append("普通设备终端回路需明确是固定连接还是经插座连接。")
    return None, None


def _resolve_voltage_drop(
    request: CircuitStrategyRequest,
    catalog: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    data = catalog.get("voltage_drop_limits", {})
    profiles = data.get("profiles", {})
    profile_code: str | None
    mapping_note: str | None = None

    if (
        request.circuit_application == CircuitApplication.LIGHTING_FINAL
        or request.load_profile == LoadProfile.LIGHTING
    ):
        profile_code = "lighting_low_voltage"
    elif request.load_profile == LoadProfile.MIXED_DISTRIBUTION:
        if request.supplies_lighting is None:
            warnings.append("混合配电回路需明确是否供给照明负荷，才能采用电压降限值。")
            profile_code = None
        else:
            profile_code = (
                "lighting_low_voltage"
                if request.supplies_lighting
                else "low_voltage"
            )
    else:
        profile_code = "low_voltage"

    if profile_code is None:
        return {
            "status": UNKNOWN,
            "profile_code": None,
            "limit_pct": None,
            "rule_code": "ELEC.VDROP.LIMIT",
        }
    profile = profiles.get(profile_code)
    if not profile:
        warnings.append(f"电压降目录缺少{profile_code}参数。")
        return {
            "status": UNKNOWN,
            "profile_code": profile_code,
            "limit_pct": None,
            "rule_code": "ELEC.VDROP.LIMIT",
        }
    if profile_code == "lighting_low_voltage":
        mapping_note = profile.get("selection_note")
    return {
        "status": PASS,
        "profile_code": profile_code,
        "profile_name": profile.get("name"),
        "table_value": profile.get("table_value"),
        "limit_pct": profile.get("limit_pct"),
        "boundary": data.get("boundary"),
        "source": data.get("source"),
        "table": data.get("table"),
        "page": data.get("page"),
        "rule_code": "ELEC.VDROP.LIMIT",
        "mapping_note": mapping_note,
    }


def _resolve_disconnection(
    request: CircuitStrategyRequest,
    connection_mode: ConnectionMode | None,
    mapping_note: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "maximum_time_s": None,
        "rule_code": "ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME",
        "connection_mode": (
            connection_mode.value if connection_mode is not None else None
        ),
        "mapping_note": mapping_note,
    }
    if request.earthing_system not in {EarthingSystem.TN_S, EarthingSystem.TN_C_S}:
        warnings.append("当前自动切断策略只覆盖TN-S和TN-C-S系统。")
        return result
    if connection_mode is None:
        return result
    if connection_mode == ConnectionMode.DISTRIBUTION:
        result.update(
            status=PASS,
            maximum_time_s=5.0,
            basis="TN系统配电回路",
        )
        return result

    rating = request.circuit_rated_current_a
    threshold = 63.0 if connection_mode == ConnectionMode.SOCKET else 32.0
    result["fast_time_rating_threshold_a"] = threshold
    if rating is None:
        result["basis"] = (
            f"额定电流不超过{threshold:g}A时需再按U₀确定表41.1时间；"
            "超过时按其他回路5s。"
        )
        warnings.append("尚未选定回路额定电流，最长自动切断时间只能列出判定条件。")
        return result
    if rating <= 0:
        warnings.append("circuit_rated_current_a必须大于0。")
        return result
    if rating > threshold:
        result.update(
            status=PASS,
            maximum_time_s=5.0,
            basis=f"额定电流超过{threshold:g}A，按TN系统其他回路",
        )
        return result
    if 120 < request.line_to_earth_voltage_v <= 230:
        result.update(
            status=PASS,
            maximum_time_s=0.4,
            basis=(
                f"{connection_mode.value}回路≤{threshold:g}A，"
                "120V＜U₀≤230V"
            ),
        )
    else:
        warnings.append("当前已核实表41.1数值只覆盖交流120V＜U₀≤230V。")
    return result


def _breaker_search_strategy(
    request: CircuitStrategyRequest,
) -> dict[str, Any]:
    if request.circuit_application in {
        CircuitApplication.LIGHTING_FINAL,
        CircuitApplication.SOCKET_FINAL,
        CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
    }:
        families = ("MCB", "MCCB")
    else:
        families = ("MCCB", "ACB", "MCB")

    if request.phase == Phase.SINGLE:
        pole_options = ("1P", "1P+N", "2P")
    elif request.neutral_required is False:
        pole_options = ("3P",)
    else:
        pole_options = ("3P", "4P")
    return {
        "allowed_families": list(families),
        "family_order_basis": (
            "平台候选搜索顺序，不是规范对回路用途与断路器类别的强制对应。"
        ),
        "pole_options": list(pole_options),
        "pole_requirement": "unconfirmed",
        "neutral_required": request.neutral_required,
        "neutral_pole_mode": request.neutral_pole_mode,
        "pen_conductor_present": request.pen_conductor_present,
        "pen_switched_or_isolated": request.pen_switched_or_isolated,
        "ics_requirement_mode": request.ics_requirement_mode,
        "short_time_withstand_required": (
            request.short_time_withstand_required
        ),
        "short_time_delay_s": request.short_time_delay_s,
        "pole_note": "极数及N极处理须按配电系统和中性线要求确认。",
    }


def _rcd_strategy(
    request: CircuitStrategyRequest,
    catalog: dict[str, Any],
    rules: dict[str, dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    required = (
        request.rcd_required
        if request.rcd_required is not None
        else (True if request.rcd_scenario is not None else None)
    )
    evaluation = evaluate_rcd_protection(
        RcdProtectionInput(
            required=required,
            applicability_reference=request.rcd_applicability_reference,
            scenario_code=request.rcd_scenario,
            residual_waveform_code=request.residual_current_waveform,
            selected_rated_residual_current_ma=(
                request.selected_rated_residual_current_ma
            ),
            normal_leakage_current_ma=request.normal_leakage_current_ma,
            downstream_of_pen_split=request.rcd_downstream_of_pen_split,
        ),
        request.earthing_system,
        rules,
        catalog,
    )
    warnings.extend(evaluation.warnings)
    return {
        "status": evaluation.provisional_status,
        "formal_status": evaluation.status,
        "required": required,
        "applicability_reference": request.rcd_applicability_reference,
        "selected_rated_residual_current_ma": (
            request.selected_rated_residual_current_ma
        ),
        "normal_leakage_current_ma": request.normal_leakage_current_ma,
        "downstream_of_pen_split": request.rcd_downstream_of_pen_split,
        "assessment_priority": (
            "插座终端回路必须优先核对附加保护适用条件"
            if request.circuit_application == CircuitApplication.SOCKET_FINAL
            else "按具体场所及火灾危险条件核对"
        ),
        "rule_codes": list(evaluation.rule_codes),
        **evaluation.outputs,
    }


def resolve_circuit_application_strategy(
    request: CircuitStrategyRequest,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """Resolve application-specific criteria without inventing project conditions."""

    catalog = catalog or DEFAULT_CATALOG
    warnings: list[str] = []
    steps: list[Step] = []
    rule_codes = [
        "ELEC.VDROP.LIMIT",
        "ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME",
    ]

    if request.circuit_application == CircuitApplication.MOTOR_FINAL:
        warnings.append("电动机终端回路属于后续专用模块，不能套用普通回路策略。")
    compatible_profiles = {
        CircuitApplication.LIGHTING_FINAL: {LoadProfile.LIGHTING},
        CircuitApplication.SOCKET_FINAL: {
            LoadProfile.SOCKET,
            LoadProfile.ORDINARY_EQUIPMENT,
            LoadProfile.MIXED_DISTRIBUTION,
        },
        CircuitApplication.ORDINARY_EQUIPMENT_FINAL: {
            LoadProfile.ORDINARY_EQUIPMENT
        },
        CircuitApplication.MOTOR_FINAL: {LoadProfile.MOTOR},
        CircuitApplication.DISTRIBUTION: set(LoadProfile),
    }
    if request.load_profile not in compatible_profiles[request.circuit_application]:
        warnings.append("load_profile与circuit_application不匹配。")

    connection_mode, mapping_note = _resolve_connection_mode(request, warnings)
    voltage_drop = _resolve_voltage_drop(request, catalog, warnings)
    disconnection = _resolve_disconnection(
        request,
        connection_mode,
        mapping_note,
        warnings,
    )
    breaker_search = _breaker_search_strategy(request)
    rcd = _rcd_strategy(request, catalog, rules, warnings)
    for code in rcd.get("rule_codes", []):
        if code not in rule_codes:
            rule_codes.append(code)

    pending_checks = [
        item
        for item, resolved in (
            ("允许电压降", voltage_drop["status"] == PASS),
            ("最长自动切断时间", disconnection["status"] == PASS),
            ("RCD适用性、动作值与类型", rcd["status"] == PASS),
            (
                "断路器极数及N极处理",
                breaker_search["pole_requirement"] != "unconfirmed",
            ),
        )
        if not resolved
    ]
    if request.earthing_system == EarthingSystem.TN_C_S:
        pending_checks.append("TN-C-S的PEN分开点及其后N、PE不得再合并")

    outputs = {
        "circuit_application": request.circuit_application.value,
        "load_profile": request.load_profile.value,
        "connection_mode": (
            connection_mode.value if connection_mode is not None else None
        ),
        "voltage_drop": voltage_drop,
        "automatic_disconnection": disconnection,
        "breaker_search": breaker_search,
        "rcd": rcd,
        "pending_checks": pending_checks,
    }
    if voltage_drop.get("limit_pct") is not None:
        steps.append(
            Step(
                "回路用途电压降限值",
                str(voltage_drop.get("table_value")),
                float(voltage_drop["limit_pct"]),
                "%",
            )
        )
    if disconnection.get("maximum_time_s") is not None:
        steps.append(
            Step(
                "TN系统最长自动切断时间",
                str(disconnection.get("basis")),
                float(disconnection["maximum_time_s"]),
                "s",
            )
        )

    core_resolved = (
        request.circuit_application != CircuitApplication.MOTOR_FINAL
        and request.load_profile
        in compatible_profiles[request.circuit_application]
        and voltage_drop["status"] == PASS
        and disconnection["status"] == PASS
    )
    provisional = PASS if core_resolved else UNKNOWN
    formal = PASS if provisional == PASS and _approved(rules, rule_codes) else UNKNOWN
    if provisional == PASS and formal == UNKNOWN:
        warnings.append("策略所用依据尚未全部批准，不能形成正式设计结论。")
    return Outcome(
        "回路用途策略",
        ENGINE_VERSION,
        formal,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def combination_inputs_from_strategy(
    strategy: Outcome,
    *,
    node_id: str,
    protected_segment_id: str,
    pole_requirement: str = "unconfirmed",
    mcb_trip_curve: str | None = None,
) -> dict[str, Any]:
    """Build the strategy-controlled fields used by the combination solver."""

    voltage_drop = strategy.outputs.get("voltage_drop", {})
    breaker_search = strategy.outputs.get("breaker_search", {})
    limit = voltage_drop.get("limit_pct")
    rule_code = voltage_drop.get("rule_code")
    if limit is None or not rule_code:
        raise ValueError("回路策略尚未确定允许电压降，不能构造组合求解输入。")
    application = CircuitApplication(strategy.outputs["circuit_application"])
    return {
        "voltage_drop_limit_pct": float(limit),
        "voltage_drop_limit_rule_code": str(rule_code),
        "protection_point": ProtectionPoint(
            node_id=node_id,
            protected_segment_id=protected_segment_id,
            circuit_application=application,
            allowed_families=tuple(breaker_search["allowed_families"]),
            pole_requirement=pole_requirement,
            mcb_trip_curve=mcb_trip_curve,
            connection_mode=(
                ConnectionMode(strategy.outputs["connection_mode"])
                if strategy.outputs.get("connection_mode")
                else None
            ),
            rcd=RcdProtectionInput(
                required=strategy.outputs.get("rcd", {}).get("required"),
                applicability_reference=strategy.outputs.get("rcd", {}).get(
                    "applicability_reference"
                ),
                scenario_code=strategy.outputs.get("rcd", {})
                .get("scenario", {})
                .get("scenario_code"),
                residual_waveform_code=strategy.outputs.get("rcd", {})
                .get("waveform", {})
                .get("waveform_code"),
                selected_rated_residual_current_ma=strategy.outputs.get(
                    "rcd", {}
                ).get("selected_rated_residual_current_ma"),
                normal_leakage_current_ma=strategy.outputs.get("rcd", {}).get(
                    "normal_leakage_current_ma"
                ),
                downstream_of_pen_split=strategy.outputs.get("rcd", {}).get(
                    "downstream_of_pen_split"
                ),
            ),
            pole_and_neutral=PoleAndNeutralInput(
                neutral_required=breaker_search.get("neutral_required"),
                neutral_pole_mode=breaker_search.get(
                    "neutral_pole_mode", "unconfirmed"
                ),
                pen_conductor_present=breaker_search.get(
                    "pen_conductor_present"
                ),
                pen_switched_or_isolated=breaker_search.get(
                    "pen_switched_or_isolated"
                ),
            ),
            ics_requirement_mode=breaker_search.get(
                "ics_requirement_mode", "unconfirmed"
            ),
            short_time_withstand_required=breaker_search.get(
                "short_time_withstand_required"
            ),
            short_time_delay_s=breaker_search.get("short_time_delay_s"),
        ),
        "maximum_disconnection_time_s": strategy.outputs.get(
            "automatic_disconnection", {}
        ).get("maximum_time_s"),
    }
