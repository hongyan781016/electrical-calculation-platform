"""既有图纸完整回路的原设计核验。

本模块只评价图纸中已经指定的电缆和保护器件。它不会用候选产品
替换原器件，也不会把替代设计的结果写回原设计合规结论。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cable_selector import CableSelectionRequest, generate_cable_candidates
from .complete_circuit_engine import (
    CompleteCircuitCalculationInput,
    ResolvedSegmentElectrical,
    calculate_complete_circuit_chain,
)
from .engine import (
    FAIL,
    Outcome,
    PASS,
    UNKNOWN,
    calculate_pe_thermal_withstand,
    calculate_phase_conductor_thermal_withstand,
)
from .protective_conductor import calculate_pe_minimum_section_by_table
from .radial_circuit_service import (
    RadialCircuitCalculationRequest,
    circuit_with_resolved_upstream,
    resolve_radial_source_electrical,
)


ENGINE_VERSION = "0.2.0"
CHECK_LABELS = {
    "rated_capacity": "变压器容量边界", "voltage": "电压匹配", "impedance": "短路阻抗输入",
    "rated_voltage": "额定电压", "rated_current": "额定电流", "short_time_withstand": "短时耐受",
    "ampacity": "载流量", "pe_section": "PE截面", "load_current": "负荷电流",
    "cable_coordination": "电缆保护配合", "breaking_capacity": "分断能力",
    "automatic_disconnection": "自动切断", "phase_thermal": "相导体热稳定", "pe_thermal": "PE热稳定",
}


@dataclass(frozen=True)
class InstalledCable:
    segment_id: str
    designation: str
    phase_section_mm2: float
    selection_request: CableSelectionRequest


@dataclass(frozen=True)
class InstalledBreaker:
    node_id: str
    protected_segment_id: str
    designation: str
    rated_current_a: float | None
    frame_current_a: float | None
    rated_voltage_v: float | None
    breaking_capacity_ka: float | None
    guaranteed_action_current_a: float | None = None
    product_reference: str | None = None


@dataclass(frozen=True)
class InstalledIncomingBreaker:
    """配电箱进线器件；不冒充任一箱后分支的线路保护器。"""

    node_id: str
    designation: str
    design_current_a: float | None
    rated_current_a: float | None
    frame_current_a: float | None
    rated_voltage_v: float | None
    breaking_capacity_ka: float | None
    product_reference: str | None = None


@dataclass(frozen=True)
class InstalledAssembly:
    """图纸中已有的低压成套设备，不把柜体参数与内部断路器混用。"""

    node_id: str
    designation: str
    rated_current_a: float | None
    rated_voltage_v: float | None
    short_time_withstand_ka: float | None
    product_reference: str | None = None


@dataclass(frozen=True)
class InstalledBusway:
    segment_id: str
    designation: str
    rated_current_a: float
    rated_voltage_v: float
    short_time_withstand_ka_1s: float
    corrected_ampacity_a: float
    source_reference: str


@dataclass(frozen=True)
class DrawingCircuitAuditRequest:
    radial_request: RadialCircuitCalculationRequest
    installed_cables: tuple[InstalledCable, ...]
    installed_breakers: tuple[InstalledBreaker, ...]
    installed_assemblies: tuple[InstalledAssembly, ...] = ()
    installed_busways: tuple[InstalledBusway, ...] = ()
    installed_incoming_breakers: tuple[InstalledIncomingBreaker, ...] = ()


def _status_all(checks: list[str]) -> str:
    if FAIL in checks:
        return FAIL
    if UNKNOWN in checks:
        return UNKNOWN
    return PASS


def _remediation_for_check(code: str, check: dict[str, Any]) -> str | None:
    if check.get("status") == PASS:
        return None
    fixed = {
        "rated_capacity": "汇总该变压器全部回路负荷后，再复核容量、负载率及运行方式。",
        "voltage": "核对变压器低压侧额定电压与系统标称电压。",
        "impedance": "补录变压器铭牌uk%及对应分接位置。",
        "rated_voltage": "补录铭牌Ue；若Ue低于系统电压，应更换相应电压等级设备。",
        "rated_current": "补录铭牌额定电流；不足时提高成套设备额定电流并复核温升。",
        "short_time_withstand": "补录Icw及持续时间；不足时提高短时耐受等级。",
        "ampacity": "提高电缆截面或改善敷设条件，使修正载流量满足负荷和保护配合。",
        "pe_section": "按相导体截面、材料及敷设结构调整PE截面。",
        "load_current": "核对负荷计算电流与断路器额定/长延时整定值。",
        "cable_coordination": "调整断路器额定/长延时整定值或电缆截面，使保护值不超过Iz。",
        "breaking_capacity": "取得产品分断能力并与安装点Ikmax比较；不足时提高Icu/Icn。",
        "automatic_disconnection": "取得准确脱扣曲线或整定值，并与线路末端最小故障电流复核。",
        "phase_thermal": "取得断路器切除时间或I²t曲线，完成相导体热稳定复核。",
        "pe_thermal": "取得断路器切除时间或I²t曲线，完成PE热稳定复核。",
    }
    return fixed.get(code)


def _attach_remediation(component_matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for component in component_matrix:
        for code, check in component.get("checks", {}).items():
            check.setdefault("check_name", CHECK_LABELS.get(code, code))
        actions = [
            action
            for code, check in component.get("checks", {}).items()
            if (action := _remediation_for_check(code, check))
        ]
        component["remediation_actions"] = list(dict.fromkeys(actions))
    return component_matrix


def _prospective_short_circuit_ka(node: dict[str, Any]) -> float | None:
    values = [
        float(value)
        for value in (
            node.get("three_phase_short_circuit_ka"),
            (
                float(node["phase_neutral_short_circuit_a"]) / 1000
                if node.get("phase_neutral_short_circuit_a") is not None
                else None
            ),
        )
        if value is not None
    ]
    return max(values) if values else None


def _minimum_fault_current_a(node: dict[str, Any]) -> float | None:
    values = [
        float(value)
        for value in (
            node.get("phase_neutral_short_circuit_a"),
            node.get("earth_fault_current_a"),
        )
        if value is not None
    ]
    return min(values) if values else None


def _audit_transformer(request: DrawingCircuitAuditRequest) -> dict[str, Any]:
    source = request.radial_request.circuit.source
    load_flows = request.radial_request.segment_load_flows
    design_current = max((item.design_current_a for item in load_flows), default=None)
    rated_current = (
        float(source.rated_capacity_kva) * 1000
        / (3 ** 0.5 * request.radial_request.circuit.system_voltage_v)
        if source.rated_capacity_kva
        else None
    )
    checks = {
        "rated_capacity": {
            "status": UNKNOWN,
            "design_current_a": design_current,
            "transformer_rated_current_a": rated_current,
            "single_circuit_compatible": (
                None if design_current is None or rated_current is None
                else design_current <= rated_current
            ),
            "criterion": "本回路Ib可与变压器额定电流比较；变压器容量是否满足须汇总其全部负荷",
            "reason": "当前只录入一条出线回路，不能据此判定变压器总容量合格。",
        },
        "voltage": {
            "status": (
                UNKNOWN if source.lv_voltage_kv is None
                else PASS
                if abs(source.lv_voltage_kv * 1000 - request.radial_request.circuit.system_voltage_v)
                <= source.lv_voltage_kv * 1000 * 0.05
                else FAIL
            ),
            "rated_lv_voltage_kv": source.lv_voltage_kv,
            "system_voltage_v": request.radial_request.circuit.system_voltage_v,
            "criterion": "变压器低压额定电压与系统标称电压相符",
        },
        "impedance": {
            "status": PASS if source.uk_percent and source.uk_percent > 0 else UNKNOWN,
            "uk_percent": source.uk_percent,
            "criterion": "短路阻抗必须有铭牌或图纸数值，作为短路计算输入",
        },
    }
    return {
        "component_type": "transformer",
        "component_name": "变压器",
        "designation": f"{source.transformer_family} {source.rated_capacity_kva:g}kVA",
        "checks": checks,
        "status": _status_all([item["status"] for item in checks.values()]),
    }


def _audit_assemblies(
    assemblies: tuple[InstalledAssembly, ...],
    node_results: dict[str, dict[str, Any]],
    flow_results: dict[str, Any],
    calculation_circuit: Any,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    incoming_flow_by_node: dict[str, float] = {}
    for segment in calculation_circuit.segments:
        flow = flow_results.get(segment.id)
        if flow is not None:
            incoming_flow_by_node[segment.to_node_id] = flow.design_current_a
    for item in assemblies:
        design_current = incoming_flow_by_node.get(item.node_id)
        prospective_ka = _prospective_short_circuit_ka(node_results.get(item.node_id, {}))
        checks = {
            "rated_voltage": {
                "status": (
                    UNKNOWN if item.rated_voltage_v is None
                    else PASS if item.rated_voltage_v >= calculation_circuit.system_voltage_v else FAIL
                ),
                "rated_voltage_v": item.rated_voltage_v,
                "system_voltage_v": calculation_circuit.system_voltage_v,
                "criterion": "Ue≥系统电压",
            },
            "rated_current": {
                "status": (
                    UNKNOWN if item.rated_current_a is None or design_current is None
                    else PASS if item.rated_current_a >= design_current else FAIL
                ),
                "rated_current_a": item.rated_current_a,
                "design_current_a": design_current,
                "criterion": "成套设备额定电流≥通过该节点的计算电流",
            },
            "short_time_withstand": {
                "status": (
                    UNKNOWN if item.short_time_withstand_ka is None or prospective_ka is None
                    else PASS if item.short_time_withstand_ka >= prospective_ka else FAIL
                ),
                "short_time_withstand_ka": item.short_time_withstand_ka,
                "prospective_short_circuit_ka": prospective_ka,
                "criterion": "声明的短时耐受电流≥安装点预期短路电流；持续时间仍按铭牌复核",
            },
        }
        results.append({
            "component_type": "assembly",
            "component_name": "低压成套设备",
            "node_id": item.node_id,
            "designation": item.designation,
            "product_reference": item.product_reference,
            "checks": checks,
            "status": _status_all([check["status"] for check in checks.values()]),
        })
    return results


def _resolve_installed_cable(
    installed: InstalledCable,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    generated = generate_cable_candidates(installed.selection_request, rules, catalog)
    candidate = next(
        (
            item
            for item in generated.outputs.get("candidates", [])
            if float(item["phase_section_mm2"])
            == float(installed.phase_section_mm2)
        ),
        None,
    )
    if candidate is None:
        return None, "图纸电缆截面没有进入当前已核实目录；不插值。"
    if candidate.get("resolved_electrical") is None:
        return None, "图纸电缆的N/PE结构或阻抗参数尚未闭合。"
    return candidate, None


def audit_drawing_complete_circuit(
    request: DrawingCircuitAuditRequest,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """按原图指定部件完成逐段复算，不生成替代型号。"""

    warnings: list[str] = []
    source, source_warnings = resolve_radial_source_electrical(
        request.radial_request
    )
    warnings.extend(source_warnings)
    if source is None:
        return Outcome(
            "图纸完整回路核验",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            {},
            [],
            warnings,
            [],
        )

    installed_by_segment = {
        item.segment_id: item for item in request.installed_cables
    }
    if len(installed_by_segment) != len(request.installed_cables):
        warnings.append("图纸电缆记录存在重复线路段。")
    cable_segment_ids = {
        item.id
        for item in request.radial_request.circuit.segments
        if item.segment_type.value in {"cable", "insulated_wire"}
    }
    if set(installed_by_segment) != cable_segment_ids:
        warnings.append("图纸电缆必须逐段覆盖完整回路中的全部电缆段。")

    cable_results: list[dict[str, Any]] = []
    resolved_segments: list[ResolvedSegmentElectrical] = list(
        request.radial_request.fixed_segment_electrical
    )
    for segment in sorted(
        request.radial_request.circuit.segments,
        key=lambda item: item.sequence,
    ):
        if segment.segment_type.value == "busway":
            continue
        installed = installed_by_segment.get(segment.id)
        if installed is None:
            continue
        candidate, reason = _resolve_installed_cable(installed, rules, catalog)
        if candidate is None:
            cable_results.append(
                {
                    "segment_id": segment.id,
                    "designation": installed.designation,
                    "phase_section_mm2": installed.phase_section_mm2,
                    "status": UNKNOWN,
                    "reason": reason,
                }
            )
            warnings.append(f"线路段{segment.id}：{reason}")
            continue
        resolved_segments.append(
            ResolvedSegmentElectrical(**candidate["resolved_electrical"])
        )
        pe_minimum = calculate_pe_minimum_section_by_table(
            {
                "phase_conductor_section_mm2": candidate["phase_section_mm2"],
                "phase_conductor_material": "copper",
                "protective_conductor_material": "copper",
                "protective_conductor_section_mm2": candidate[
                    "protective_section_mm2"
                ],
                "separate_protective_conductor": False,
            },
            rules,
        ).to_dict()
        cable_status = _status_all(
            [
                candidate["ampacity_provisional_status"],
                pe_minimum["provisional_status"],
            ]
        )
        cable_results.append(
            {
                "segment_id": segment.id,
                "designation": installed.designation,
                "phase_section_mm2": installed.phase_section_mm2,
                "corrected_ampacity_a": candidate["corrected_ampacity_a"],
                "minimum_required_ampacity_a": candidate[
                    "minimum_required_ampacity_a"
                ],
                "ampacity_status": candidate["ampacity_provisional_status"],
                "protective_section_mm2": candidate[
                    "protective_section_mm2"
                ],
                "pe_minimum_section": pe_minimum,
                "status": cable_status,
                "source": candidate.get("fault_loop_structure", {}),
            }
        )

    all_segment_ids = {item.id for item in request.radial_request.circuit.segments}
    if warnings or {item.segment_id for item in resolved_segments} != all_segment_ids:
        return Outcome(
            "图纸完整回路核验",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            {"installed_cables": cable_results},
            [],
            warnings,
            [],
        )

    calculation_circuit = circuit_with_resolved_upstream(
        request.radial_request
    )
    chain = calculate_complete_circuit_chain(
        CompleteCircuitCalculationInput(
            circuit=calculation_circuit,
            source_electrical=source,
            segment_electrical=tuple(resolved_segments),
            segment_load_flows=request.radial_request.segment_load_flows,
            maximum_short_circuit_voltage_factor=(
                request.radial_request.maximum_short_circuit_voltage_factor
            ),
            minimum_fault_voltage_factor=(
                request.radial_request.minimum_fault_voltage_factor
            ),
        ),
        rules,
    )
    node_results = {
        item["node_id"]: item
        for item in chain.outputs.get("node_results", [])
    }
    flow_results = {
        item.segment_id: item
        for item in request.radial_request.segment_load_flows
    }
    electrical_by_segment = {
        item.segment_id: item for item in resolved_segments
    }
    cable_result_by_segment = {
        item["segment_id"]: item for item in cable_results
    }

    breaker_results: list[dict[str, Any]] = []
    for breaker in request.installed_breakers:
        flow = flow_results.get(breaker.protected_segment_id)
        electrical = electrical_by_segment.get(breaker.protected_segment_id)
        installation_node = node_results.get(breaker.node_id, {})
        segment = next(
            (
                item
                for item in calculation_circuit.segments
                if item.id == breaker.protected_segment_id
            ),
            None,
        )
        terminal_node = (
            node_results.get(segment.to_node_id, {}) if segment else {}
        )
        checks: dict[str, dict[str, Any]] = {}

        if flow is None or breaker.rated_current_a is None:
            checks["load_current"] = {
                "status": UNKNOWN,
                "reason": "缺少本段Ib或断路器额定电流In。",
            }
        else:
            checks["load_current"] = {
                "status": PASS if flow.design_current_a <= breaker.rated_current_a else FAIL,
                "design_current_a": flow.design_current_a,
                "rated_current_a": breaker.rated_current_a,
                "criterion": "Ib≤In",
            }

        if electrical is None or breaker.rated_current_a is None:
            checks["cable_coordination"] = {
                "status": UNKNOWN,
                "reason": "缺少修正后载流量Iz或断路器额定电流In。",
            }
        else:
            checks["cable_coordination"] = {
                "status": (
                    PASS
                    if breaker.rated_current_a
                    <= float(electrical.corrected_ampacity_a or 0)
                    else FAIL
                ),
                "rated_current_a": breaker.rated_current_a,
                "corrected_ampacity_a": electrical.corrected_ampacity_a,
                "criterion": "In≤Iz",
            }

        prospective_ka = _prospective_short_circuit_ka(installation_node)
        if prospective_ka is None or breaker.breaking_capacity_ka is None:
            checks["breaking_capacity"] = {
                "status": UNKNOWN,
                "reason": "缺少安装点最大预期短路电流或器件分断能力。",
            }
        else:
            checks["breaking_capacity"] = {
                "status": PASS if breaker.breaking_capacity_ka >= prospective_ka else FAIL,
                "prospective_short_circuit_ka": prospective_ka,
                "breaking_capacity_ka": breaker.breaking_capacity_ka,
                "criterion": "Icu/Icn≥Ikmax",
            }

        minimum_fault_a = _minimum_fault_current_a(terminal_node)
        if minimum_fault_a is None or breaker.guaranteed_action_current_a is None:
            checks["automatic_disconnection"] = {
                "status": UNKNOWN,
                "reason": "缺少线路末端最小故障电流或准确保证动作电流。",
            }
        else:
            checks["automatic_disconnection"] = {
                "status": (
                    PASS
                    if minimum_fault_a >= breaker.guaranteed_action_current_a
                    else FAIL
                ),
                "minimum_fault_current_a": minimum_fault_a,
                "guaranteed_action_current_a": breaker.guaranteed_action_current_a,
                "criterion": "If,min≥Ia",
            }

        cable_result = cable_result_by_segment.get(
            breaker.protected_segment_id,
            {},
        )
        phase_thermal = calculate_phase_conductor_thermal_withstand(
            {
                "phase_conductor_section_mm2": cable_result.get(
                    "phase_section_mm2"
                ),
                "phase_conductor_material": "copper",
                "phase_conductor_insulation": "xlpe",
                "prospective_fault_current_a": (
                    prospective_ka * 1000 if prospective_ka is not None else None
                ),
            },
            rules,
        ).to_dict()
        pe_thermal = calculate_pe_thermal_withstand(
            {
                "protective_conductor_section_mm2": cable_result.get(
                    "protective_section_mm2"
                ),
                "protective_conductor_material": "copper",
                "protective_conductor_insulation": "xlpe",
                "protective_conductor_arrangement": "multicore_cable",
                "prospective_fault_current_a": terminal_node.get(
                    "earth_fault_current_a"
                ),
            },
            rules,
        ).to_dict()
        checks["phase_thermal"] = {
            "status": phase_thermal["provisional_status"],
            "maximum_permitted_clearing_time_s": phase_thermal[
                "outputs"
            ].get("maximum_permitted_clearing_time_s"),
            "maximum_permitted_let_through_energy_a2s": phase_thermal[
                "outputs"
            ].get("maximum_permitted_let_through_energy_a2s"),
            "reason": "缺少原断路器准确切除时间或产品I²t时，只输出导体允许约束。",
        }
        checks["pe_thermal"] = {
            "status": pe_thermal["provisional_status"],
            "maximum_permitted_clearing_time_s": pe_thermal["outputs"].get(
                "maximum_permitted_clearing_time_s"
            ),
            "maximum_permitted_let_through_energy_a2s": pe_thermal[
                "outputs"
            ].get("maximum_permitted_let_through_energy_a2s"),
            "reason": "缺少原断路器准确切除时间或产品I²t时，只输出PE允许约束。",
        }

        statuses = [item["status"] for item in checks.values()]
        breaker_results.append(
            {
                "node_id": breaker.node_id,
                "protected_segment_id": breaker.protected_segment_id,
                "designation": breaker.designation,
                "product_reference": breaker.product_reference,
                "checks": checks,
                "status": _status_all(statuses),
            }
        )

    incoming_breaker_results: list[dict[str, Any]] = []
    for breaker in request.installed_incoming_breakers:
        node = node_results.get(breaker.node_id, {})
        checks: dict[str, dict[str, Any]] = {}
        if breaker.design_current_a is None or breaker.rated_current_a is None:
            checks["load_current"] = {"status": UNKNOWN, "reason": "缺少配电箱进线Ib或In。"}
        else:
            checks["load_current"] = {
                "status": PASS if breaker.design_current_a <= breaker.rated_current_a else FAIL,
                "design_current_a": breaker.design_current_a,
                "rated_current_a": breaker.rated_current_a,
                "criterion": "配电箱Ib≤进线断路器In",
            }
        checks["rated_voltage"] = (
            {"status": UNKNOWN, "reason": "缺少进线断路器额定电压。"}
            if breaker.rated_voltage_v is None
            else {
                "status": PASS if breaker.rated_voltage_v >= calculation_circuit.system_voltage_v else FAIL,
                "rated_voltage_v": breaker.rated_voltage_v,
                "system_voltage_v": calculation_circuit.system_voltage_v,
                "criterion": "Ue≥系统电压",
            }
        )
        prospective_ka = _prospective_short_circuit_ka(node)
        checks["breaking_capacity"] = (
            {"status": UNKNOWN, "reason": "缺少照明箱安装点Ikmax或进线器件Icu。"}
            if prospective_ka is None or breaker.breaking_capacity_ka is None
            else {
                "status": PASS if breaker.breaking_capacity_ka >= prospective_ka else FAIL,
                "prospective_short_circuit_ka": prospective_ka,
                "breaking_capacity_ka": breaker.breaking_capacity_ka,
                "criterion": "Icu≥照明箱安装点Ikmax",
            }
        )
        incoming_breaker_results.append({
            "node_id": breaker.node_id,
            "designation": breaker.designation,
            "product_reference": breaker.product_reference,
            "frame_current_a": breaker.frame_current_a,
            "checks": checks,
            "status": _status_all([item["status"] for item in checks.values()]),
        })

    ordered_protection_devices = [
        *request.installed_breakers[:-1],
        *request.installed_incoming_breakers,
        *request.installed_breakers[-1:],
    ]
    coordination_results = [
        {
            "upstream_node_id": upstream.node_id,
            "downstream_node_id": downstream.node_id,
            "upstream_designation": upstream.designation,
            "downstream_designation": downstream.designation,
            "status": UNKNOWN,
            "reason": "未提供原图上下级器件的准确厂家选择性/后备保护表及整定组合。",
        }
        for upstream, downstream in zip(
            ordered_protection_devices,
            ordered_protection_devices[1:],
        )
    ]

    transformer_result = _audit_transformer(request)
    assembly_results = _audit_assemblies(
        request.installed_assemblies,
        node_results,
        flow_results,
        calculation_circuit,
    )
    busway_results: list[dict[str, Any]] = []
    for item in request.installed_busways:
        node = next(
            (
                result for result in node_results.values()
                if result.get("node_id")
                == next(
                    (
                        segment.from_node_id
                        for segment in calculation_circuit.segments
                        if segment.id == item.segment_id
                    ),
                    None,
                )
            ),
            {},
        )
        prospective_ka = _prospective_short_circuit_ka(node)
        flow = flow_results.get(item.segment_id)
        checks = {
            "rated_voltage": {
                "status": PASS if item.rated_voltage_v >= calculation_circuit.system_voltage_v else FAIL,
                "rated_voltage_v": item.rated_voltage_v,
                "system_voltage_v": calculation_circuit.system_voltage_v,
                "criterion": "Ue≥系统电压",
            },
            "ampacity": {
                "status": (
                    UNKNOWN if flow is None
                    else PASS if item.corrected_ampacity_a >= flow.design_current_a else FAIL
                ),
                "design_current_a": flow.design_current_a if flow else None,
                "corrected_ampacity_a": item.corrected_ampacity_a,
                "criterion": "环境温度修正后Iz≥Ib",
            },
            "short_time_withstand": {
                "status": (
                    UNKNOWN if prospective_ka is None
                    else PASS if item.short_time_withstand_ka_1s >= prospective_ka else FAIL
                ),
                "prospective_short_circuit_ka": prospective_ka,
                "short_time_withstand_ka_1s": item.short_time_withstand_ka_1s,
                "criterion": "Icw(1s)≥安装点Ikmax；实际切除时间超过1s时另行复核",
            },
        }
        busway_results.append({
            "component_type": "busway", "component_name": "母线槽",
            "designation": item.designation, "segment_id": item.segment_id,
            "source_reference": item.source_reference, "checks": checks,
            "rated_current_a": item.rated_current_a,
            "corrected_ampacity_a": item.corrected_ampacity_a,
            "short_time_withstand_ka_1s": item.short_time_withstand_ka_1s,
            "status": _status_all([check["status"] for check in checks.values()]),
        })

    terminal_drop = chain.outputs.get("terminal_voltage_drop_percent")
    voltage_drop_status = (
        UNKNOWN
        if terminal_drop is None
        else (
            PASS
            if float(terminal_drop)
            <= request.radial_request.voltage_drop_limit_pct
            else FAIL
        )
    )
    cross_component_checks = [
        {
            "check_code": "voltage_drop",
            "check_name": "全回路累计电压降",
            "status": voltage_drop_status,
            "actual_percent": terminal_drop,
            "limit_percent": request.radial_request.voltage_drop_limit_pct,
            "criterion": "末端累计电压降≤本回路采用限值",
            "remediation": (
                None if voltage_drop_status == PASS
                else "复核线路长度与负荷条件，并调整电缆截面或供电路径。"
            ),
        },
        *[
            {
                "check_code": "selectivity",
                "check_name": f"{item['upstream_designation']}→{item['downstream_designation']}选择性",
                "status": item["status"],
                "reason": item["reason"],
                "criterion": "按同厂家选择性/后备保护表及实际整定组合核验",
                "remediation": "取得上下级器件厂家配合表和实际整定组合后复核。",
            }
            for item in coordination_results
        ],
    ]
    cable_statuses = [item["status"] for item in cable_results]
    breaker_statuses = [item["status"] for item in breaker_results] + [
        item["status"] for item in incoming_breaker_results
    ]
    component_statuses = [transformer_result["status"]] + [
        item["status"] for item in assembly_results
    ] + [item["status"] for item in busway_results]
    known_status = _status_all(
        component_statuses
        + cable_statuses
        + breaker_statuses
        + [item["status"] for item in cross_component_checks]
    )
    component_matrix = [transformer_result, *assembly_results, *busway_results]
    component_matrix.extend(
        {
            "component_type": "cable",
            "component_name": "电缆",
            "designation": item["designation"],
            "segment_id": item["segment_id"],
            "status": item["status"],
            "checks": {
                "ampacity": {"status": item.get("ampacity_status", UNKNOWN)},
                "pe_section": {
                    "status": item.get("pe_minimum_section", {}).get("provisional_status", UNKNOWN)
                },
            },
        }
        for item in cable_results
    )
    component_matrix.extend(
        {
            "component_type": "breaker",
            "component_name": "断路器",
            "designation": item["designation"],
            "node_id": item["node_id"],
            "status": item["status"],
            "checks": item["checks"],
        }
        for item in breaker_results
    )
    component_matrix.extend(
        {
            "component_type": "incoming_breaker",
            "component_name": "末端配电箱进线断路器",
            "designation": item["designation"],
            "node_id": item["node_id"],
            "status": item["status"],
            "checks": item["checks"],
        }
        for item in incoming_breaker_results
    )
    component_matrix.append({
        "component_type": "load",
        "component_name": "末端负荷",
        "designation": request.radial_request.circuit.load.circuit_application.value,
        "status": PASS,
        "checks": {
            "load_current": {
                "status": PASS,
                "design_current_a": max(
                    (item.design_current_a for item in request.radial_request.segment_load_flows),
                    default=None,
                ),
                "criterion": "按图纸负荷条件统一推导本回路Ib，并传递至各段校核",
            }
        },
    })
    component_matrix = _attach_remediation(component_matrix)
    outputs = {
        "audit_subject": "drawing_installed_components",
        "replacement_design_included": False,
        "installed_cables": cable_results,
        "installed_breakers": breaker_results,
        "installed_incoming_breakers": incoming_breaker_results,
        "transformer": transformer_result,
        "installed_assemblies": assembly_results,
        "installed_busways": busway_results,
        "component_matrix": component_matrix,
        "protection_coordination": coordination_results,
        "cross_component_checks": cross_component_checks,
        "chain_result": chain.to_dict(),
        "voltage_drop_check": {
            "actual_percent": terminal_drop,
            "limit_percent": request.radial_request.voltage_drop_limit_pct,
            "status": voltage_drop_status,
        },
    }
    formal = PASS if known_status == PASS and chain.status == PASS else UNKNOWN
    return Outcome(
        "图纸完整回路核验",
        ENGINE_VERSION,
        formal,
        known_status,
        outputs,
        [],
        list(chain.warnings),
        list(chain.rule_codes),
    )
