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


ENGINE_VERSION = "0.1.0"


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
class DrawingCircuitAuditRequest:
    radial_request: RadialCircuitCalculationRequest
    installed_cables: tuple[InstalledCable, ...]
    installed_breakers: tuple[InstalledBreaker, ...]


def _status_all(checks: list[str]) -> str:
    if FAIL in checks:
        return FAIL
    if UNKNOWN in checks:
        return UNKNOWN
    return PASS


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
    circuit_segment_ids = {
        item.id for item in request.radial_request.circuit.segments
    }
    if set(installed_by_segment) != circuit_segment_ids:
        warnings.append("图纸电缆必须逐段覆盖完整回路。")

    cable_results: list[dict[str, Any]] = []
    resolved_segments: list[ResolvedSegmentElectrical] = []
    for segment in sorted(
        request.radial_request.circuit.segments,
        key=lambda item: item.sequence,
    ):
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

    if warnings or len(resolved_segments) != len(circuit_segment_ids):
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
            request.installed_breakers,
            request.installed_breakers[1:],
        )
    ]

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
    cable_statuses = [item["status"] for item in cable_results]
    breaker_statuses = [item["status"] for item in breaker_results]
    known_status = _status_all(
        cable_statuses + breaker_statuses + [voltage_drop_status]
    )
    outputs = {
        "audit_subject": "drawing_installed_components",
        "replacement_design_included": False,
        "installed_cables": cable_results,
        "installed_breakers": breaker_results,
        "protection_coordination": coordination_results,
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
