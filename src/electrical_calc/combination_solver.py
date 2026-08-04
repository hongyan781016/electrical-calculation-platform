"""电缆—断路器—完整回路的候选组合求解器。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Any

from .breaker_selector import BreakerSelectionRequest, generate_breaker_candidates
from .cable_selector import CableSelectionRequest, generate_cable_candidates
from .catalog import DEFAULT_CATALOG
from .complete_circuit import CircuitApplication, CompleteCircuit, ConnectionMode
from .complete_circuit_engine import (
    CompleteCircuitCalculationInput,
    ResolvedSegmentElectrical,
    ResolvedSegmentLoadFlow,
    ResolvedSourceElectrical,
    calculate_complete_circuit_chain,
)
from .engine import (
    FAIL,
    Outcome,
    PASS,
    Step,
    UNKNOWN,
    calculate_phase_conductor_thermal_withstand,
    calculate_pe_thermal_withstand,
)
from .rcd_protection import RcdProtectionInput, evaluate_rcd_protection
from .pole_configuration import (
    PoleAndNeutralInput,
    evaluate_pole_and_neutral_configuration,
)
from .protection_coordination import (
    ManufacturerCoordinationEvidence,
    ProtectionCoordinationInput,
    ProtectionDeviceIdentity,
    evaluate_protection_coordination,
)
from .protective_conductor import calculate_pe_minimum_section_by_table


ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class ProtectionPoint:
    node_id: str
    protected_segment_id: str
    circuit_application: CircuitApplication
    allowed_families: tuple[str, ...]
    pole_requirement: str
    mcb_trip_curve: str | None = None
    connection_mode: ConnectionMode | None = None
    rcd: RcdProtectionInput | None = None
    pole_and_neutral: PoleAndNeutralInput | None = None
    ics_requirement_mode: str = "unconfirmed"
    short_time_withstand_required: bool | None = None
    short_time_delay_s: float | None = None
    product_identity: ProtectionDeviceIdentity | None = None
    backup_protection_required: bool = False


@dataclass(frozen=True)
class CombinationSolverRequest:
    circuit: CompleteCircuit
    source_electrical: ResolvedSourceElectrical
    fixed_segment_electrical: tuple[ResolvedSegmentElectrical, ...]
    segment_load_flows: tuple[ResolvedSegmentLoadFlow, ...]
    cable_requests: tuple[CableSelectionRequest, ...]
    protection_points: tuple[ProtectionPoint, ...]
    maximum_short_circuit_voltage_factor: float
    minimum_fault_voltage_factor: float
    voltage_drop_limit_pct: float
    voltage_drop_limit_rule_code: str
    maximum_cable_combinations: int = 500
    maximum_output_combinations: int = 100
    maximum_candidates_per_cable_segment: int | None = None
    coordination_evidence: tuple[
        ManufacturerCoordinationEvidence, ...
    ] = ()


def _node_by_id(circuit: CompleteCircuit) -> dict[str, Any]:
    return {node.id: node for node in circuit.nodes}


def _segment_by_id(circuit: CompleteCircuit) -> dict[str, Any]:
    return {segment.id: segment for segment in circuit.segments}


def _fault_current_for_breaker(node_result: dict[str, Any]) -> float | None:
    currents = [
        float(value)
        for value in (
            node_result.get("phase_neutral_short_circuit_a"),
            node_result.get("earth_fault_current_a"),
        )
        if value is not None
    ]
    return min(currents) if currents else None


def _prospective_short_circuit_ka(node_result: dict[str, Any]) -> float | None:
    currents_ka = [
        float(value)
        for value in (
            node_result.get("three_phase_short_circuit_ka"),
            (
                float(node_result["phase_neutral_short_circuit_a"]) / 1000
                if node_result.get("phase_neutral_short_circuit_a") is not None
                else None
            ),
        )
        if value is not None
    ]
    return max(currents_ka) if currents_ka else None


def _connection_mode_for_point(
    point: ProtectionPoint,
) -> tuple[ConnectionMode | None, str | None]:
    if point.connection_mode is not None:
        return point.connection_mode, None
    if point.circuit_application == CircuitApplication.SOCKET_FINAL:
        return ConnectionMode.SOCKET, None
    if point.circuit_application == CircuitApplication.DISTRIBUTION:
        return ConnectionMode.DISTRIBUTION, None
    if point.circuit_application == CircuitApplication.LIGHTING_FINAL:
        return (
            ConnectionMode.FIXED_CONNECTED,
            "平台用途映射：照明终端回路按固定连接用电设备回路处理。",
        )
    return None, None


def _candidate_disconnection_check(
    point: ProtectionPoint,
    candidate: dict[str, Any],
    line_to_earth_voltage_v: float,
) -> dict[str, Any]:
    mode, mapping_note = _connection_mode_for_point(point)
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "maximum_time_s": None,
        "connection_mode": mode.value if mode is not None else None,
        "mapping_note": mapping_note,
        "rule_code": "ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME",
    }
    if mode is None:
        result["reason"] = "普通设备终端回路尚未明确固定连接或插座连接。"
        return result

    rating = float(candidate["rated_current_a"])
    if mode == ConnectionMode.DISTRIBUTION:
        maximum_time = 5.0
        basis = "TN系统配电回路"
    else:
        threshold = 63.0 if mode == ConnectionMode.SOCKET else 32.0
        result["fast_time_rating_threshold_a"] = threshold
        if rating > threshold:
            maximum_time = 5.0
            basis = f"In>{threshold:g}A，按TN系统其他回路"
        elif 120 < line_to_earth_voltage_v <= 230:
            maximum_time = 0.4
            basis = (
                f"In≤{threshold:g}A，且120V＜U₀≤230V"
            )
        else:
            result["reason"] = (
                "当前已核实表41.1数值只覆盖交流120V＜U₀≤230V。"
            )
            return result

    result["maximum_time_s"] = maximum_time
    result["basis"] = basis
    if (
        candidate.get("family") == "MCB"
        and candidate.get("automatic_trip_status") == PASS
    ):
        result.update(
            status=PASS,
            check_method="最小故障电流达到B/C型保证瞬时动作电流",
        )
    elif candidate.get("family") == "MCB":
        result["reason"] = "缺少可用的B/C型保证动作电流或末端最小故障电流。"
    else:
        result["reason"] = "MCCB/ACB需具体脱扣器整定及时间—电流曲线。"
    return result


def _candidate_pe_thermal_check(
    cable_candidate: dict[str, Any] | None,
    breaker_candidate: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "rule_code": "ELEC.PE.THERMAL.WITHSTAND",
    }
    if cable_candidate is None:
        return {
            **base,
            "reason": "被保护线路不是本轮生成的电缆候选，尚无可用PE截面。",
        }
    pe_section = cable_candidate.get("protective_section_mm2")
    if pe_section is None:
        return {
            **base,
            "reason": "电缆候选尚未确认PE导体截面。",
        }
    if cable_candidate.get("family") != "YJV":
        return {
            **base,
            "reason": "当前自动路径只覆盖已确认内置铜PE的YJV多芯电缆。",
        }
    disconnection = breaker_candidate.get("automatic_disconnection", {})
    if (
        breaker_candidate.get("family") != "MCB"
        or disconnection.get("status") != PASS
    ):
        return {
            **base,
            "reason": "缺少可用于绝热法的产品I²t或已核实故障切除时间。",
        }
    fault_current = breaker_candidate.get("earth_fault_current_a")
    if fault_current is None:
        return {
            **base,
            "reason": "缺少被保护线路末端最小接地故障电流。",
        }

    outcome = calculate_pe_thermal_withstand(
        {
            "protective_conductor_section_mm2": pe_section,
            "protective_conductor_material": "copper",
            "protective_conductor_insulation": "xlpe",
            "protective_conductor_arrangement": "multicore_cable",
            "prospective_fault_current_a": fault_current,
            # 已核实B/C型保证瞬时动作边界为<0.1s；按0.1s保守计算。
            "fault_clearing_time_s": 0.1,
        },
        rules,
    )
    result = outcome.to_dict()
    result["calculation_basis"] = (
        "YJV内置铜PE；B/C型MCB达到保证瞬时动作电流；"
        "按<0.1s的上界0.1s保守计算。"
    )
    return result


def _candidate_phase_thermal_check(
    cable_candidate: dict[str, Any] | None,
    breaker_candidate: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "rule_code": "ELEC.PHASE.THERMAL.WITHSTAND",
    }
    if cable_candidate is None:
        return {
            **base,
            "reason": "被保护线路不是本轮生成的电缆候选，尚无可用相导体截面。",
        }
    section = cable_candidate.get("phase_section_mm2")
    if section is None:
        return {**base, "reason": "电缆候选尚未确认相导体截面。"}
    family = cable_candidate.get("family")
    if family not in {"BV", "YJV"}:
        return {**base, "reason": "当前自动路径只覆盖铜芯BV和YJV。"}
    if (
        breaker_candidate.get("family") != "MCB"
        or breaker_candidate.get("automatic_disconnection", {}).get("status")
        != PASS
    ):
        return {
            **base,
            "reason": "缺少可用于绝热法的产品I²t或已核实故障切除时间。",
        }
    fault_current = breaker_candidate.get(
        "maximum_phase_short_circuit_current_a"
    )
    if fault_current is None:
        return {
            **base,
            "reason": "缺少被保护线路起点的最大相间短路电流。",
        }
    outcome = calculate_phase_conductor_thermal_withstand(
        {
            "phase_conductor_section_mm2": section,
            "phase_conductor_material": "copper",
            "phase_conductor_insulation": (
                "pvc" if family == "BV" else "xlpe"
            ),
            "prospective_fault_current_a": fault_current,
            # 已核实B/C型保证瞬时动作边界为<0.1s；按0.1s保守计算。
            "fault_clearing_time_s": 0.1,
        },
        rules,
    )
    result = outcome.to_dict()
    result["calculation_basis"] = (
        f"{family}铜相导体；B/C型MCB达到保证瞬时动作电流；"
        "按<0.1s的上界0.1s保守计算。"
    )
    return result


def _candidate_pe_minimum_section_check(
    cable_candidate: dict[str, Any] | None,
    rules: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    base = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "rule_code": "ELEC.PE.MIN_SECTION.TABLE54_2",
    }
    if cable_candidate is None:
        return {**base, "reason": "被保护线路没有本轮电缆候选。"}
    phase_section = cable_candidate.get("phase_section_mm2")
    pe_section = cable_candidate.get("protective_section_mm2")
    if phase_section is None or pe_section is None:
        return {
            **base,
            "reason": "尚未确认PE截面及其是否为电缆组成部分。",
        }
    outcome = calculate_pe_minimum_section_by_table(
        {
            "phase_conductor_section_mm2": phase_section,
            "phase_conductor_material": "copper",
            "protective_conductor_material": "copper",
            "protective_conductor_section_mm2": pe_section,
            "separate_protective_conductor": False,
        },
        rules,
    )
    return outcome.to_dict()


def _combination_rank(item: dict[str, Any]) -> tuple[Any, ...]:
    cable_sections = tuple(
        float(candidate["phase_section_mm2"])
        for candidate in item["cables"]
    )
    breaker_ratings = tuple(
        float(candidate["rated_current_a"])
        for candidate in item["breakers"]
    )
    breaker_frames = tuple(
        float(candidate["frame_current_a"])
        for candidate in item["breakers"]
    )
    return (
        sum(cable_sections),
        cable_sections,
        sum(breaker_ratings),
        breaker_ratings,
        breaker_frames,
        item["combination_id"],
    )


def _candidate_coordination_checks(
    protection_points: tuple[ProtectionPoint, ...],
    breaker_candidates: tuple[dict[str, Any], ...],
    segments: dict[str, Any],
    evidence_records: tuple[ManufacturerCoordinationEvidence, ...],
    system_voltage_v: float | None = None,
) -> list[dict[str, Any]]:
    """Build explicit adjacent protection pairs in circuit order.

    Generic breaker parameter candidates do not identify actual products, so
    this path deliberately remains UNKNOWN until product evidence is supplied.
    """

    ordered = sorted(
        zip(protection_points, breaker_candidates, strict=True),
        key=lambda pair: segments[pair[0].protected_segment_id].sequence,
    )
    checks: list[dict[str, Any]] = []
    for (upstream_point, upstream), (downstream_point, downstream) in zip(
        ordered,
        ordered[1:],
    ):
        upstream_identity = upstream_point.product_identity
        downstream_identity = downstream_point.product_identity
        if upstream_identity is not None and (
            upstream_identity.family != upstream.get("family")
            or upstream_identity.rated_current_a
            != upstream.get("rated_current_a")
        ):
            upstream_identity = None
        if downstream_identity is not None and (
            downstream_identity.family != downstream.get("family")
            or downstream_identity.rated_current_a
            != downstream.get("rated_current_a")
        ):
            downstream_identity = None
        matching_evidence = next(
            (
                item
                for item in evidence_records
                if upstream_identity is not None
                and downstream_identity is not None
                and item.upstream_product_code
                == upstream_identity.product_code
                and item.upstream_configuration_reference
                == upstream_identity.configuration_reference
                and item.downstream_product_code
                == downstream_identity.product_code
                and item.downstream_configuration_reference
                == downstream_identity.configuration_reference
            ),
            None,
        )
        fault_current_a = downstream.get(
            "maximum_phase_short_circuit_current_a"
        )
        check = evaluate_protection_coordination(
            ProtectionCoordinationInput(
                upstream=upstream_identity,
                downstream=downstream_identity,
                downstream_prospective_short_circuit_ka=(
                    float(fault_current_a) / 1000
                    if fault_current_a is not None
                    else None
                ),
                evidence=matching_evidence,
                backup_protection_required=(
                    downstream_point.backup_protection_required
                ),
                system_voltage_v=system_voltage_v,
            )
        )
        checks.append(
            {
                **check,
                "upstream_node_id": upstream_point.node_id,
                "upstream_candidate_id": upstream["candidate_id"],
                "downstream_node_id": downstream_point.node_id,
                "downstream_candidate_id": downstream["candidate_id"],
            }
        )
    return checks


def solve_complete_circuit_combinations(
    request: CombinationSolverRequest,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """Search deterministic cable/breaker combinations without guessing missing checks."""

    catalog = catalog or DEFAULT_CATALOG
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "viable_combinations": [],
        "incomplete_combinations": [],
        "rejected_combinations": [],
        "cable_generation": {},
    }
    rule_codes = [request.voltage_drop_limit_rule_code]
    if "ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME" not in rule_codes:
        rule_codes.append("ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME")

    circuit_issues = [
        issue
        for issue in request.circuit.validate()
        if issue.severity in {"error", "unsupported"}
    ]
    if circuit_issues:
        warnings.extend(issue.message for issue in circuit_issues)
    if request.voltage_drop_limit_pct <= 0:
        warnings.append("允许电压降必须由适用规则提供且大于0。")
    if request.maximum_cable_combinations <= 0:
        warnings.append("最大电缆组合数必须大于0。")
    if request.maximum_output_combinations <= 0:
        warnings.append("最大输出组合数必须大于0。")
    if (
        request.maximum_candidates_per_cable_segment is not None
        and request.maximum_candidates_per_cable_segment <= 0
    ):
        warnings.append("每段电缆候选数限制必须大于0。")

    segments = _segment_by_id(request.circuit)
    nodes = _node_by_id(request.circuit)
    flow_by_segment = {
        item.segment_id: item for item in request.segment_load_flows
    }
    cable_segment_ids = [item.segment_id for item in request.cable_requests]
    fixed_segment_ids = [
        item.segment_id for item in request.fixed_segment_electrical
    ]
    if len(set(cable_segment_ids)) != len(cable_segment_ids):
        warnings.append("电缆候选请求存在重复线路段。")
    if len(set(fixed_segment_ids)) != len(fixed_segment_ids):
        warnings.append("固定线路参数存在重复线路段。")
    if set(cable_segment_ids) & set(fixed_segment_ids):
        warnings.append("同一线路段不能同时作为候选段和固定参数段。")
    if set(cable_segment_ids) | set(fixed_segment_ids) != set(segments):
        warnings.append("候选线路段和固定线路段必须完整覆盖回路拓扑。")

    for cable_request in request.cable_requests:
        segment = segments.get(cable_request.segment_id)
        flow = flow_by_segment.get(cable_request.segment_id)
        if not segment:
            warnings.append(f"电缆候选线路段{cable_request.segment_id}不在回路中。")
            continue
        if segment.segment_type.value not in {"cable", "insulated_wire"}:
            warnings.append(f"线路段{segment.id}不是电缆或绝缘电线候选段。")
        if segment.phase != cable_request.phase:
            warnings.append(f"线路段{segment.id}相制与电缆候选请求不一致。")
        if not flow:
            warnings.append(f"线路段{segment.id}缺少负荷流。")
        elif abs(
            cable_request.minimum_required_ampacity_a - flow.design_current_a
        ) > max(0.01, flow.design_current_a * 1e-4):
            warnings.append(
                f"线路段{segment.id}的首轮电缆候选必须以本段Ib为最低载流量。"
            )

    for point in request.protection_points:
        segment = segments.get(point.protected_segment_id)
        if point.node_id not in nodes:
            warnings.append(f"保护安装节点{point.node_id}不在回路中。")
        if not segment:
            warnings.append(f"被保护线路段{point.protected_segment_id}不在回路中。")
        elif segment.from_node_id != point.node_id:
            warnings.append(
                f"保护点{point.node_id}必须位于线路段{segment.id}的电源侧起点。"
            )

    if warnings:
        return Outcome(
            "完整回路组合求解",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    cable_candidate_lists: list[list[dict[str, Any]]] = []
    for cable_request in request.cable_requests:
        generated = generate_cable_candidates(cable_request, rules, catalog)
        outputs["cable_generation"][cable_request.segment_id] = generated.to_dict()
        for code in generated.rule_codes:
            if code not in rule_codes:
                rule_codes.append(code)
        candidates = generated.outputs.get("candidates", [])
        if request.maximum_candidates_per_cable_segment is not None:
            candidates = candidates[: request.maximum_candidates_per_cable_segment]
        if not candidates:
            warnings.append(f"线路段{cable_request.segment_id}没有电缆候选。")
        cable_candidate_lists.append(candidates)
    if warnings:
        return Outcome(
            "完整回路组合求解",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    raw_cable_combinations = 1
    for candidates in cable_candidate_lists:
        raw_cable_combinations *= len(candidates)
    if raw_cable_combinations > request.maximum_cable_combinations:
        warnings.append(
            f"电缆组合数{raw_cable_combinations}超过限制"
            f"{request.maximum_cable_combinations}；请收窄候选范围。"
        )
        return Outcome(
            "完整回路组合求解",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    fixed_by_segment = {
        item.segment_id: item for item in request.fixed_segment_electrical
    }
    node_index = {
        item.id: index
        for index, item in enumerate(
            sorted(request.circuit.nodes, key=lambda node: node.sequence)
        )
    }
    attempted_cable_combinations = 0
    viable: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for cable_tuple in product(*cable_candidate_lists):
        attempted_cable_combinations += 1
        cable_by_segment = {
            candidate_request.segment_id: candidate
            for candidate_request, candidate in zip(
                request.cable_requests,
                cable_tuple,
                strict=True,
            )
        }
        cable_identity = "|".join(
            candidate["candidate_id"] for candidate in cable_tuple
        )
        unresolved = [
            candidate["candidate_id"]
            for candidate in cable_tuple
            if candidate.get("resolved_electrical") is None
        ]
        if unresolved:
            incomplete.append(
                {
                    "combination_id": cable_identity,
                    "cables": list(cable_tuple),
                    "breakers": [],
                    "known_checks_status": UNKNOWN,
                    "overall_status": UNKNOWN,
                    "missing_items": [
                        "以下电缆候选尚未确认N/PE配置：" + "、".join(unresolved)
                    ],
                }
            )
            continue

        resolved_by_segment = dict(fixed_by_segment)
        for segment_id, candidate in cable_by_segment.items():
            resolved_by_segment[segment_id] = ResolvedSegmentElectrical(
                **candidate["resolved_electrical"]
            )
        ordered_electrical = tuple(
            resolved_by_segment[segment.id]
            for segment in sorted(
                request.circuit.segments,
                key=lambda item: item.sequence,
            )
        )
        chain = calculate_complete_circuit_chain(
            CompleteCircuitCalculationInput(
                circuit=request.circuit,
                source_electrical=request.source_electrical,
                segment_electrical=ordered_electrical,
                segment_load_flows=request.segment_load_flows,
                maximum_short_circuit_voltage_factor=(
                    request.maximum_short_circuit_voltage_factor
                ),
                minimum_fault_voltage_factor=request.minimum_fault_voltage_factor,
            ),
            rules,
        )
        for code in chain.rule_codes:
            if code not in rule_codes:
                rule_codes.append(code)

        voltage_drop = chain.outputs.get("terminal_voltage_drop_percent")
        if (
            voltage_drop is not None
            and float(voltage_drop) > request.voltage_drop_limit_pct
        ):
            rejected.append(
                {
                    "combination_id": cable_identity,
                    "cables": list(cable_tuple),
                    "breakers": [],
                    "reason_code": "voltage_drop_exceeded",
                    "reason": (
                        f"累计电压降{float(voltage_drop):.6g}%超过"
                        f"允许值{request.voltage_drop_limit_pct:g}%。"
                    ),
                    "chain_result": chain.to_dict(),
                }
            )
            continue

        chain_nodes = {
            item["node_id"]: item
            for item in chain.outputs.get("node_results", [])
        }
        breaker_lists: list[list[dict[str, Any]]] = []
        breaker_generation: dict[str, Any] = {}
        hard_breaker_failure = False
        for point in request.protection_points:
            protected_segment = segments[point.protected_segment_id]
            electrical = resolved_by_segment[point.protected_segment_id]
            flow = flow_by_segment[point.protected_segment_id]
            installation_node_result = chain_nodes.get(point.node_id, {})
            terminal_node_result = chain_nodes.get(
                protected_segment.to_node_id,
                {},
            )
            minimum_fault_current = _fault_current_for_breaker(
                terminal_node_result
            )
            earth_fault_current = terminal_node_result.get(
                "earth_fault_current_a"
            )
            maximum_phase_short_circuit_current = (
                (
                    float(
                        installation_node_result[
                            "three_phase_short_circuit_ka"
                        ]
                    )
                    * 1000
                )
                if protected_segment.phase.value == "3"
                and installation_node_result.get(
                    "three_phase_short_circuit_ka"
                )
                is not None
                else installation_node_result.get(
                    "phase_neutral_short_circuit_a"
                )
            )
            breaker_outcome = generate_breaker_candidates(
                BreakerSelectionRequest(
                    node_id=point.node_id,
                    circuit_application=point.circuit_application,
                    phase=protected_segment.phase,
                    system_voltage_v=request.circuit.system_voltage_v,
                    design_current_a=flow.design_current_a,
                    conductor_corrected_ampacity_a=float(
                        electrical.corrected_ampacity_a or 0
                    ),
                    allowed_families=point.allowed_families,
                    pole_requirement=point.pole_requirement,
                    prospective_short_circuit_ka=(
                        _prospective_short_circuit_ka(
                            installation_node_result
                        )
                    ),
                    minimum_fault_current_a=minimum_fault_current,
                    mcb_trip_curve=point.mcb_trip_curve,
                    ics_requirement_mode=point.ics_requirement_mode,
                    short_time_withstand_required=(
                        point.short_time_withstand_required
                    ),
                    short_time_delay_s=point.short_time_delay_s,
                ),
                rules,
                catalog,
            )
            breaker_generation[point.node_id] = breaker_outcome.to_dict()
            for code in breaker_outcome.rule_codes:
                if code not in rule_codes:
                    rule_codes.append(code)
            candidates = breaker_outcome.outputs.get("candidates", [])
            candidates = [
                {
                    **candidate,
                    "minimum_fault_current_a": minimum_fault_current,
                    "earth_fault_current_a": earth_fault_current,
                    "maximum_phase_short_circuit_current_a": (
                        maximum_phase_short_circuit_current
                    ),
                    "automatic_disconnection": _candidate_disconnection_check(
                        point,
                        candidate,
                        request.circuit.line_to_earth_voltage_v,
                    ),
                    "pole_and_neutral": (
                        evaluate_pole_and_neutral_configuration(
                            point.pole_and_neutral or PoleAndNeutralInput(),
                            phase=protected_segment.phase,
                            selected_poles=candidate.get("adopted_poles"),
                            available_pole_options=tuple(
                                candidate.get("pole_options", ())
                            ),
                            rules=rules,
                        ).to_dict()
                    ),
                }
                for candidate in candidates
            ]
            if not candidates:
                hard_breaker_failure = True
            breaker_lists.append(candidates)

        if hard_breaker_failure:
            rejected.append(
                {
                    "combination_id": cable_identity,
                    "cables": list(cable_tuple),
                    "breakers": [],
                    "reason_code": "no_breaker_candidate",
                    "reason": "至少一个保护点没有满足已知硬条件的断路器候选。",
                    "chain_result": chain.to_dict(),
                    "breaker_generation": breaker_generation,
                }
            )
            continue

        for breaker_tuple in product(*breaker_lists):
            breakers_with_thermal: list[dict[str, Any]] = []
            pe_thermal_failed = False
            pe_minimum_section_failed = False
            phase_thermal_failed = False
            rcd_failed = False
            pole_failed = False
            for point, breaker_candidate in zip(
                request.protection_points,
                breaker_tuple,
                strict=True,
            ):
                pe_thermal = _candidate_pe_thermal_check(
                    cable_by_segment.get(point.protected_segment_id),
                    breaker_candidate,
                    rules,
                )
                if (
                    "ELEC.PE.THERMAL.WITHSTAND" not in rule_codes
                    and pe_thermal.get("outputs")
                ):
                    rule_codes.append("ELEC.PE.THERMAL.WITHSTAND")
                if pe_thermal.get("provisional_status") == FAIL:
                    pe_thermal_failed = True
                pe_minimum_section = _candidate_pe_minimum_section_check(
                    cable_by_segment.get(point.protected_segment_id),
                    rules,
                )
                if (
                    "ELEC.PE.MIN_SECTION.TABLE54_2" not in rule_codes
                    and pe_minimum_section.get("outputs")
                ):
                    rule_codes.append("ELEC.PE.MIN_SECTION.TABLE54_2")
                if pe_minimum_section.get("provisional_status") == FAIL:
                    pe_minimum_section_failed = True
                phase_thermal = _candidate_phase_thermal_check(
                    cable_by_segment.get(point.protected_segment_id),
                    breaker_candidate,
                    rules,
                )
                if (
                    "ELEC.PHASE.THERMAL.WITHSTAND" not in rule_codes
                    and phase_thermal.get("outputs")
                ):
                    rule_codes.append("ELEC.PHASE.THERMAL.WITHSTAND")
                if phase_thermal.get("provisional_status") == FAIL:
                    phase_thermal_failed = True
                rcd_check = evaluate_rcd_protection(
                    point.rcd or RcdProtectionInput(),
                    request.circuit.earthing_system,
                    rules,
                    catalog,
                ).to_dict()
                for code in rcd_check.get("rule_codes", []):
                    if code not in rule_codes:
                        rule_codes.append(code)
                if rcd_check.get("provisional_status") == FAIL:
                    rcd_failed = True
                pole_check = breaker_candidate["pole_and_neutral"]
                for code in pole_check.get("rule_codes", []):
                    if code not in rule_codes:
                        rule_codes.append(code)
                if pole_check.get("provisional_status") == FAIL:
                    pole_failed = True
                breakers_with_thermal.append(
                    {
                        **breaker_candidate,
                        "pe_thermal": pe_thermal,
                        "pe_minimum_section": pe_minimum_section,
                        "phase_thermal": phase_thermal,
                        "rcd": rcd_check,
                    }
                )
            breaker_tuple = tuple(breakers_with_thermal)
            breaker_identity = "|".join(
                candidate["candidate_id"] for candidate in breaker_tuple
            )
            if pe_thermal_failed:
                rejected.append(
                    {
                        "combination_id": (
                            f"{cable_identity}||{breaker_identity}"
                        ),
                        "cables": list(cable_tuple),
                        "breakers": list(breaker_tuple),
                        "reason_code": "pe_thermal_withstand_failed",
                        "reason": "至少一个保护点的PE导体绝热热稳定校核不通过。",
                        "chain_result": chain.to_dict(),
                        "breaker_generation": breaker_generation,
                    }
                )
                continue
            if phase_thermal_failed:
                rejected.append(
                    {
                        "combination_id": (
                            f"{cable_identity}||{breaker_identity}"
                        ),
                        "cables": list(cable_tuple),
                        "breakers": list(breaker_tuple),
                        "reason_code": "phase_conductor_thermal_withstand_failed",
                        "reason": "至少一个保护点的相导体绝热热稳定校核不通过。",
                        "chain_result": chain.to_dict(),
                        "breaker_generation": breaker_generation,
                    }
                )
                continue
            if pe_minimum_section_failed:
                rejected.append(
                    {
                        "combination_id": (
                            f"{cable_identity}||{breaker_identity}"
                        ),
                        "cables": list(cable_tuple),
                        "breakers": list(breaker_tuple),
                        "reason_code": "pe_minimum_section_failed",
                        "reason": "至少一个保护点的PE截面小于表54.2要求。",
                        "chain_result": chain.to_dict(),
                        "breaker_generation": breaker_generation,
                    }
                )
                continue
            if rcd_failed:
                rejected.append(
                    {
                        "combination_id": (
                            f"{cable_identity}||{breaker_identity}"
                        ),
                        "cables": list(cable_tuple),
                        "breakers": list(breaker_tuple),
                        "reason_code": "rcd_configuration_failed",
                        "reason": "至少一个保护点的RCD已知配置不符合校核条件。",
                        "chain_result": chain.to_dict(),
                        "breaker_generation": breaker_generation,
                    }
                )
                continue
            if pole_failed:
                rejected.append(
                    {
                        "combination_id": (
                            f"{cable_identity}||{breaker_identity}"
                        ),
                        "cables": list(cable_tuple),
                        "breakers": list(breaker_tuple),
                        "reason_code": "pole_or_neutral_configuration_failed",
                        "reason": "至少一个保护点的极数、N极或PEN配置存在已知矛盾。",
                        "chain_result": chain.to_dict(),
                        "breaker_generation": breaker_generation,
                    }
                )
                continue
            disconnection_missing = [
                (
                    f"{candidate['candidate_id']}："
                    f"{candidate['automatic_disconnection'].get('reason')}"
                )
                for candidate in breaker_tuple
                if candidate["automatic_disconnection"]["status"] != PASS
            ]
            pe_thermal_missing = [
                (
                    f"{candidate['candidate_id']}："
                    f"{candidate['pe_thermal'].get('reason', '尚未完成')}"
                )
                for candidate in breaker_tuple
                if candidate["pe_thermal"].get("provisional_status") != PASS
            ]
            pe_minimum_section_missing = [
                (
                    f"{candidate['candidate_id']}："
                    f"{candidate['pe_minimum_section'].get('reason', '尚未完成')}"
                )
                for candidate in breaker_tuple
                if candidate["pe_minimum_section"].get("provisional_status")
                != PASS
            ]
            phase_thermal_missing = [
                (
                    f"{candidate['candidate_id']}："
                    f"{candidate['phase_thermal'].get('reason', '尚未完成')}"
                )
                for candidate in breaker_tuple
                if candidate["phase_thermal"].get("provisional_status")
                != PASS
            ]
            rcd_missing = [
                candidate["candidate_id"]
                for candidate in breaker_tuple
                if candidate["rcd"].get("provisional_status") != PASS
            ]
            pole_missing = [
                candidate["candidate_id"]
                for candidate in breaker_tuple
                if candidate["pole_and_neutral"].get(
                    "provisional_status"
                )
                != PASS
            ]
            breaking_parameter_missing = [
                candidate["candidate_id"]
                for candidate in breaker_tuple
                if candidate.get("ics_status") != PASS
                or candidate.get("icw_status") != PASS
            ]
            coordination_checks = _candidate_coordination_checks(
                request.protection_points,
                breaker_tuple,
                segments,
                request.coordination_evidence,
                request.circuit.system_voltage_v,
            )
            coordination_missing = [
                (
                    f"{item['upstream_node_id']}→"
                    f"{item['downstream_node_id']}：{item['reason']}"
                )
                for item in coordination_checks
                if item["provisional_status"] != PASS
            ]
            combination = {
                "combination_id": f"{cable_identity}||{breaker_identity}",
                "cables": list(cable_tuple),
                "breakers": list(breaker_tuple),
                "chain_result": chain.to_dict(),
                "breaker_generation": breaker_generation,
                "voltage_drop_check": {
                    "actual_percent": voltage_drop,
                    "limit_percent": request.voltage_drop_limit_pct,
                    "status": PASS if voltage_drop is not None else UNKNOWN,
                    "rule_code": request.voltage_drop_limit_rule_code,
                },
                "protection_coordination": coordination_checks,
                "known_checks_status": PASS,
                "overall_status": UNKNOWN,
                "missing_items": [
                    *(
                        [
                            "相导体短路热稳定："
                            + "；".join(phase_thermal_missing)
                        ]
                        if phase_thermal_missing
                        else []
                    ),
                    *(
                        ["PE热稳定：" + "；".join(pe_thermal_missing)]
                        if pe_thermal_missing
                        else []
                    ),
                    *(
                        [
                            "PE最小截面积："
                            + "；".join(pe_minimum_section_missing)
                        ]
                        if pe_minimum_section_missing
                        else []
                    ),
                    *(
                        [
                            "Ics/Icw独立校核："
                            + "、".join(breaking_parameter_missing)
                        ]
                        if breaking_parameter_missing
                        else []
                    ),
                    *(
                        [
                            "上下级选择性与后备保护："
                            + "；".join(coordination_missing)
                        ]
                        if coordination_missing
                        else []
                    ),
                    *(
                        ["RCD独立校核：" + "、".join(rcd_missing)]
                        if rcd_missing
                        else []
                    ),
                    *(
                        ["断路器极数与N极：" + "、".join(pole_missing)]
                        if pole_missing
                        else []
                    ),
                    *(
                        ["自动切断时间：" + "；".join(disconnection_missing)]
                        if disconnection_missing
                        else []
                    ),
                    "具体产品I²t及脱扣/整定（已由B/C型保证动作值完成的项目除外）",
                ],
            }
            if (
                voltage_drop is None
                or chain.provisional_status == UNKNOWN
                or any(
                    candidate["provisional_status"] == UNKNOWN
                    for candidate in breaker_tuple
                )
            ):
                combination["known_checks_status"] = UNKNOWN
                incomplete.append(combination)
            else:
                viable.append(combination)

    viable.sort(key=_combination_rank)
    incomplete.sort(key=_combination_rank)
    rejected.sort(key=lambda item: item["combination_id"])
    total_output = len(viable) + len(incomplete)
    output_truncated = total_output > request.maximum_output_combinations
    remaining = request.maximum_output_combinations
    outputs["viable_combinations"] = viable[:remaining]
    remaining -= len(outputs["viable_combinations"])
    outputs["incomplete_combinations"] = incomplete[: max(remaining, 0)]
    outputs["rejected_combinations"] = rejected
    outputs["search_summary"] = {
        "raw_cable_combinations": raw_cable_combinations,
        "attempted_cable_combinations": attempted_cable_combinations,
        "viable_combination_count": len(viable),
        "incomplete_combination_count": len(incomplete),
        "rejected_cable_combination_count": len(rejected),
        "output_truncated": output_truncated,
        "maximum_output_combinations": request.maximum_output_combinations,
        "ranking_note": (
            "当前仅按截面、额定电流和框架电流作确定性排列，"
            "不代表经济最优或正式推荐顺序。"
        ),
    }
    if output_truncated:
        warnings.append("可行/未完成组合超过输出上限，已按确定性排序截断展示。")
    if viable:
        steps.append(
            Step(
                "完整回路候选组合",
                "逐组复算压降、故障电流及断路器硬条件",
                len(viable),
                "组可继续候选",
            )
        )
    elif incomplete:
        warnings.append("存在未发现已知失败的组合，但关键校核参数仍不完整。")
    else:
        warnings.append("当前候选中没有可继续的完整回路组合。")

    provisional = PASS if viable else UNKNOWN
    # 通用参数模式仍缺产品I²t、Ics/Icw和选择性，不产生正式通过。
    return Outcome(
        "完整回路组合求解",
        ENGINE_VERSION,
        UNKNOWN,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )
