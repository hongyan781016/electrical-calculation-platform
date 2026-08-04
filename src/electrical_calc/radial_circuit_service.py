"""面向完整低压放射式回路的统一编排入口。

本模块把用户能够确认的变压器、上级系统、线路和保护点条件解析为
公共计算链所需的结构化电气参数，再调用组合求解器。它不保存项目，
也不把缺少资料的参数替换为经验默认值。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import sqrt
from typing import Any

from .catalog import (
    DEFAULT_CATALOG,
    lookup_transformer_phase_pe_impedance,
    lookup_transformer_positive_sequence_impedance,
)
from .combination_solver import (
    CombinationSolverRequest,
    ProtectionPoint,
    solve_complete_circuit_combinations,
)
from .complete_circuit import CompleteCircuit, UpstreamNetworkMode
from .complete_circuit_engine import (
    ResolvedSegmentElectrical,
    ResolvedSegmentLoadFlow,
    ResolvedSourceElectrical,
)
from .cable_selector import CableSelectionRequest
from .engine import Outcome, UNKNOWN


ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class RadialCircuitCalculationRequest:
    circuit: CompleteCircuit
    segment_load_flows: tuple[ResolvedSegmentLoadFlow, ...]
    cable_requests: tuple[CableSelectionRequest, ...]
    protection_points: tuple[ProtectionPoint, ...]
    voltage_drop_limit_pct: float
    voltage_drop_limit_rule_code: str
    upstream_short_circuit_capacity_mva: float | None = None
    fixed_segment_electrical: tuple[ResolvedSegmentElectrical, ...] = ()
    maximum_short_circuit_voltage_factor: float = 1.05
    minimum_fault_voltage_factor: float = 0.95
    maximum_cable_combinations: int = 500
    maximum_output_combinations: int = 100
    maximum_candidates_per_cable_segment: int = 3


def resolve_radial_source_electrical(
    request: RadialCircuitCalculationRequest,
) -> tuple[ResolvedSourceElectrical | None, list[str]]:
    """由精确表列变压器参数和已知上级系统条件形成源端R/X。"""

    source = request.circuit.source
    warnings: list[str] = []
    positive = lookup_transformer_positive_sequence_impedance(
        source.transformer_family,
        source.rated_capacity_kva,
        source.uk_percent,
    )
    if positive is None:
        warnings.append(
            "变压器系列、容量与uk%没有精确的正序R/X表列组合；不插值。"
        )

    phase_pe = lookup_transformer_phase_pe_impedance(
        source.transformer_family,
        source.rated_capacity_kva,
        source.uk_percent,
    )
    if phase_pe is None:
        warnings.append(
            "变压器系列、容量与uk%没有精确的相—PE R/X表列组合；不插值。"
        )
    if source.vector_group.lower() != "dyn11":
        warnings.append("当前相—PE变压器阻抗目录只适用于Dyn11。")
    if positive is None or phase_pe is None or warnings:
        return None, warnings

    upstream_r = 0.0
    upstream_x = 0.0
    upstream_reference = "USER:UPSTREAM_INFINITE_CAPACITY"
    if source.upstream_network_mode == UpstreamNetworkMode.EXPLICIT_IMPEDANCE:
        if source.upstream_r_ohm is not None and source.upstream_x_ohm is not None:
            if source.upstream_r_ohm < 0 or source.upstream_x_ohm < 0:
                return None, ["上级系统等值R/X不能为负值。"]
            upstream_r = float(source.upstream_r_ohm)
            upstream_x = float(source.upstream_x_ohm)
            upstream_reference = "USER:UPSTREAM_EXPLICIT_RX"
        else:
            capacity = request.upstream_short_circuit_capacity_mva
            if capacity is None or capacity <= 0:
                return None, [
                    "上级系统采用有限容量时，必须提供短路容量MVA或等值R/X。"
                ]
            voltage_v = source.lv_voltage_kv * 1000
            impedance = voltage_v**2 / (capacity * 1_000_000)
            upstream_x = impedance / sqrt(1 + 0.1**2)
            upstream_r = 0.1 * upstream_x
            upstream_reference = "HANDBOOK:4.6-41+USER:SYSTEM_SHORT_CIRCUIT_CAPACITY"

    three_phase_r = (
        float(positive["positive_sequence_resistance_ohm"]) + upstream_r
    )
    three_phase_x = (
        float(positive["positive_sequence_reactance_ohm"]) + upstream_x
    )
    loop_r = float(phase_pe["phase_pe_resistance_ohm"])
    loop_x = float(phase_pe["phase_pe_reactance_ohm"])
    references = (
        "ELEC.TRANSFORMER.POSITIVE_SEQUENCE.IMPEDANCE",
        "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE",
        upstream_reference,
    )
    return (
        ResolvedSourceElectrical(
            three_phase_r_ohm=three_phase_r,
            three_phase_x_ohm=three_phase_x,
            # Dyn11低压侧N与PE在源端等电位连接；两类最小故障均从
            # 同一已核实的变压器低压相保回路表列值起算。
            phase_neutral_r_ohm=loop_r,
            phase_neutral_x_ohm=loop_x,
            phase_pe_r_ohm=loop_r,
            phase_pe_x_ohm=loop_x,
            status="verified",
            source_reference_ids=references,
        ),
        warnings,
    )


def circuit_with_resolved_upstream(
    request: RadialCircuitCalculationRequest,
) -> CompleteCircuit:
    """把短路容量折算结果写入本次不可变计算快照，不改变用户输入。"""

    circuit = request.circuit
    source = circuit.source
    if (
        source.upstream_network_mode == UpstreamNetworkMode.EXPLICIT_IMPEDANCE
        and source.upstream_r_ohm is None
        and source.upstream_x_ohm is None
        and request.upstream_short_circuit_capacity_mva is not None
    ):
        capacity = request.upstream_short_circuit_capacity_mva
        voltage_v = source.lv_voltage_kv * 1000
        impedance = voltage_v**2 / (capacity * 1_000_000)
        upstream_x = impedance / sqrt(1 + 0.1**2)
        upstream_r = 0.1 * upstream_x
        return replace(
            circuit,
            source=replace(
                source,
                upstream_r_ohm=upstream_r,
                upstream_x_ohm=upstream_x,
            ),
        )
    return circuit


def calculate_radial_complete_circuit(
    request: RadialCircuitCalculationRequest,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """一次完成源参数解析、电缆组合、逐节点计算及保护候选校核。"""

    source_electrical, warnings = resolve_radial_source_electrical(request)
    if source_electrical is None:
        return Outcome(
            "完整低压放射式回路",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            {},
            [],
            warnings,
            [
                "ELEC.TRANSFORMER.POSITIVE_SEQUENCE.IMPEDANCE",
                "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE",
            ],
        )

    calculation_circuit = circuit_with_resolved_upstream(request)

    outcome = solve_complete_circuit_combinations(
        CombinationSolverRequest(
            circuit=calculation_circuit,
            source_electrical=source_electrical,
            fixed_segment_electrical=request.fixed_segment_electrical,
            segment_load_flows=request.segment_load_flows,
            cable_requests=request.cable_requests,
            protection_points=request.protection_points,
            maximum_short_circuit_voltage_factor=(
                request.maximum_short_circuit_voltage_factor
            ),
            minimum_fault_voltage_factor=request.minimum_fault_voltage_factor,
            voltage_drop_limit_pct=request.voltage_drop_limit_pct,
            voltage_drop_limit_rule_code=request.voltage_drop_limit_rule_code,
            maximum_cable_combinations=request.maximum_cable_combinations,
            maximum_output_combinations=request.maximum_output_combinations,
            maximum_candidates_per_cable_segment=(
                request.maximum_candidates_per_cable_segment
            ),
        ),
        rules,
        catalog or DEFAULT_CATALOG,
    )
    if warnings:
        outcome.warnings[:0] = warnings
    return outcome
