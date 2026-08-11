"""完整低压放射式回路的公共计算链。

候选目录或具体产品核验层负责把电源和各线路段解析为本模块所需的
电气参数；本模块只做逐段算术，不查产品、不猜缺失阻抗。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from typing import Any

from .complete_circuit import (
    CircuitApplication,
    CompleteCircuit,
    InputBasis,
    Phase,
    PowerDefinition,
)
from .engine import Outcome, PASS, Step, UNKNOWN


ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class ResolvedSourceElectrical:
    three_phase_r_ohm: float | None
    three_phase_x_ohm: float | None
    phase_neutral_r_ohm: float | None
    phase_neutral_x_ohm: float | None
    phase_pe_r_ohm: float | None
    phase_pe_x_ohm: float | None
    status: str
    source_reference_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedSegmentElectrical:
    segment_id: str
    phase_neutral_applicable: bool
    voltage_drop_r_ohm_per_km: float | None
    voltage_drop_x_ohm_per_km: float | None
    three_phase_r_ohm_per_km: float | None
    three_phase_x_ohm_per_km: float | None
    phase_neutral_r_ohm_per_km: float | None
    phase_neutral_x_ohm_per_km: float | None
    phase_pe_r_ohm_per_km: float | None
    phase_pe_x_ohm_per_km: float | None
    corrected_ampacity_a: float | None
    status: str
    source_reference_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedSegmentLoadFlow:
    segment_id: str
    design_current_a: float
    power_factor: float
    phase: Phase
    status: str
    source_reference_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompleteCircuitCalculationInput:
    circuit: CompleteCircuit
    source_electrical: ResolvedSourceElectrical
    segment_electrical: tuple[ResolvedSegmentElectrical, ...]
    segment_load_flows: tuple[ResolvedSegmentLoadFlow, ...]
    maximum_short_circuit_voltage_factor: float
    minimum_fault_voltage_factor: float


def _finite_nonnegative(value: float | None) -> bool:
    return value is not None and value >= 0


def _impedance(r_ohm: float, x_ohm: float) -> float:
    return sqrt(r_ohm**2 + x_ohm**2)


def _current_from_load(circuit: CompleteCircuit) -> tuple[float | None, str | None]:
    load = circuit.load
    if load.input_basis == InputBasis.CURRENT_A:
        return load.input_value, "Ib＝输入电流"

    voltage = (
        circuit.line_to_earth_voltage_v
        if load.phase == Phase.SINGLE
        else circuit.system_voltage_v
    )
    multiplier = 1.0
    if (
        load.input_basis == InputBasis.ACTIVE_POWER_KW
        and load.power_definition == PowerDefinition.INSTALLED
    ):
        if load.demand_factor is None:
            return None, None
        multiplier = load.demand_factor

    if load.input_basis == InputBasis.APPARENT_POWER_KVA:
        apparent_va = load.input_value * multiplier * 1000
        if load.phase == Phase.SINGLE:
            return apparent_va / voltage, "Ib＝S/U"
        return apparent_va / (sqrt(3) * voltage), "Ib＝S/(√3U)"

    if load.power_factor is None:
        return None, None
    active_w = load.input_value * multiplier * 1000
    if load.circuit_application == CircuitApplication.MOTOR_FINAL:
        if load.efficiency is None:
            return None, None
        active_w /= load.efficiency
    if load.phase == Phase.SINGLE:
        return active_w / (voltage * load.power_factor), "Ib＝P/(Ucosφ)"
    if load.circuit_application == CircuitApplication.MOTOR_FINAL:
        return (
            active_w / (sqrt(3) * voltage * load.power_factor),
            "IrM＝PrM/(√3UrMηrcosφr)",
        )
    return (
        active_w / (sqrt(3) * voltage * load.power_factor),
        "Ib＝P/(√3Ucosφ)",
    )


def _rule_is_approved(rules: dict[str, dict[str, Any]], code: str) -> bool:
    return rules.get(code, {}).get("status") == "approved"


def calculate_complete_circuit_chain(
    data: CompleteCircuitCalculationInput,
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """计算负荷电流、累计压降以及各节点故障电流。

    本函数不生成电缆或断路器候选。所有R/X必须由上游解析层提供并
    携带来源；缺少任一分支参数时保留其他已完成结果，整体为无法判断。
    """

    rule_codes = [
        "ELEC.LOAD.CURRENT",
        "ELEC.VDROP",
        "ELEC.SHORT_CIRCUIT",
        "ELEC.EARTH_FAULT.TN.IMPEDANCE",
    ]
    if data.circuit.load.circuit_application == CircuitApplication.MOTOR_FINAL:
        rule_codes.append("MOTOR.CURRENT.RATED")
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {}

    validation_issues = data.circuit.validate()
    blocking = [
        issue for issue in validation_issues if issue.severity in {"error", "unsupported"}
    ]
    if blocking:
        warnings.extend(issue.message for issue in blocking)
        outputs["validation_issues"] = [asdict(issue) for issue in validation_issues]
        return Outcome(
            "完整回路公共计算链",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    if data.maximum_short_circuit_voltage_factor <= 0:
        warnings.append("最大短路电流电压系数必须大于0，平台不得猜测。")
    if data.minimum_fault_voltage_factor <= 0:
        warnings.append("最小故障电流电压系数必须大于0，平台不得猜测。")
    if warnings:
        return Outcome(
            "完整回路公共计算链",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    by_segment_id = {item.segment_id: item for item in data.segment_electrical}
    flow_by_segment_id = {item.segment_id: item for item in data.segment_load_flows}
    if len(by_segment_id) != len(data.segment_electrical):
        warnings.append("线路段电气参数存在重复segment_id。")
    if len(flow_by_segment_id) != len(data.segment_load_flows):
        warnings.append("线路段负荷流存在重复segment_id。")
    missing_segments = [
        segment.id for segment in data.circuit.segments if segment.id not in by_segment_id
    ]
    extra_segments = [
        segment_id
        for segment_id in by_segment_id
        if segment_id not in {segment.id for segment in data.circuit.segments}
    ]
    missing_flows = [
        segment.id
        for segment in data.circuit.segments
        if segment.id not in flow_by_segment_id
    ]
    extra_flows = [
        segment_id
        for segment_id in flow_by_segment_id
        if segment_id not in {segment.id for segment in data.circuit.segments}
    ]
    if missing_segments:
        warnings.append("缺少线路段电气参数：" + "、".join(missing_segments))
    if extra_segments:
        warnings.append("存在不属于当前回路的线路段电气参数：" + "、".join(extra_segments))
    if missing_flows:
        warnings.append("缺少线路段负荷流：" + "、".join(missing_flows))
    if extra_flows:
        warnings.append("存在不属于当前回路的线路段负荷流：" + "、".join(extra_flows))
    if (
        missing_segments
        or extra_segments
        or missing_flows
        or extra_flows
        or len(by_segment_id) != len(data.segment_electrical)
        or len(flow_by_segment_id) != len(data.segment_load_flows)
    ):
        return Outcome(
            "完整回路公共计算链",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    design_current_a, current_expression = _current_from_load(data.circuit)
    if design_current_a is None or current_expression is None:
        warnings.append("负荷电流缺少已解析功率因数或需要系数，不能计算。")
        return Outcome(
            "完整回路公共计算链",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )
    outputs["design_current_a"] = round(design_current_a, 6)
    steps.append(Step("负荷计算电流", current_expression, design_current_a, "A"))
    terminal_segment = max(data.circuit.segments, key=lambda item: item.sequence)
    terminal_flow = flow_by_segment_id[terminal_segment.id]
    current_tolerance = max(0.01, design_current_a * 1e-4)
    if (
        abs(terminal_flow.design_current_a - design_current_a) > current_tolerance
        or terminal_flow.phase != data.circuit.load.phase
    ):
        warnings.append(
            "末端线路负荷流必须与末端负荷计算电流及相制一致；当前数据链未闭合。"
        )
        return Outcome(
            "完整回路公共计算链",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    voltage_drop_available = True
    operating_voltage_v = (
        data.circuit.line_to_earth_voltage_v
        if data.circuit.load.phase == Phase.SINGLE
        else data.circuit.system_voltage_v
    )

    source = data.source_electrical
    three_phase_available = all(
        _finite_nonnegative(value)
        for value in (source.three_phase_r_ohm, source.three_phase_x_ohm)
    )
    phase_neutral_available = all(
        _finite_nonnegative(value)
        for value in (source.phase_neutral_r_ohm, source.phase_neutral_x_ohm)
    )
    terminal_phase_neutral_applicable = all(
        item.phase_neutral_applicable for item in data.segment_electrical
    )
    phase_pe_available = all(
        _finite_nonnegative(value)
        for value in (source.phase_pe_r_ohm, source.phase_pe_x_ohm)
    )
    if not three_phase_available:
        warnings.append("电源缺少三相短路R/X，各节点三相短路电流无法完整计算。")
    if not phase_neutral_available and terminal_phase_neutral_applicable:
        warnings.append("电源缺少相—N回路R/X，各节点单相短路电流无法完整计算。")
    if not phase_pe_available:
        warnings.append("电源缺少相—PE回路R/X，各节点接地故障电流无法完整计算。")

    cumulative_voltage_drop_v = 0.0
    cumulative_three_phase_r = float(source.three_phase_r_ohm or 0)
    cumulative_three_phase_x = float(source.three_phase_x_ohm or 0)
    cumulative_phase_neutral_r = float(source.phase_neutral_r_ohm or 0)
    cumulative_phase_neutral_x = float(source.phase_neutral_x_ohm or 0)
    cumulative_phase_pe_r = float(source.phase_pe_r_ohm or 0)
    cumulative_phase_pe_x = float(source.phase_pe_x_ohm or 0)

    node_results: list[dict[str, Any]] = []
    segment_results: list[dict[str, Any]] = []

    def append_node_result(node_index: int) -> None:
        node = sorted(data.circuit.nodes, key=lambda item: item.sequence)[node_index]
        result: dict[str, Any] = {
            "node_id": node.id,
            "node_name": node.name,
            "cumulative_voltage_drop_v": (
                round(cumulative_voltage_drop_v, 6)
                if voltage_drop_available
                else None
            ),
            "cumulative_voltage_drop_percent": (
                round(cumulative_voltage_drop_v / operating_voltage_v * 100, 6)
                if voltage_drop_available
                else None
            ),
            "three_phase_short_circuit_ka": None,
            "phase_neutral_short_circuit_a": None,
            "earth_fault_current_a": None,
        }
        if three_phase_available:
            z_three = _impedance(
                cumulative_three_phase_r,
                cumulative_three_phase_x,
            )
            if z_three > 0:
                result["three_phase_short_circuit_ka"] = round(
                    (
                        data.maximum_short_circuit_voltage_factor
                        * data.circuit.system_voltage_v
                        / (sqrt(3) * z_three)
                        / 1000
                    ),
                    6,
                )
        if phase_neutral_available:
            z_ln = _impedance(
                cumulative_phase_neutral_r,
                cumulative_phase_neutral_x,
            )
            if z_ln > 0:
                result["phase_neutral_short_circuit_a"] = round(
                    (
                        data.minimum_fault_voltage_factor
                        * data.circuit.line_to_earth_voltage_v
                        / z_ln
                    ),
                    6,
                )
        if phase_pe_available:
            z_pe = _impedance(cumulative_phase_pe_r, cumulative_phase_pe_x)
            if z_pe > 0:
                result["earth_fault_current_a"] = round(
                    (
                        data.minimum_fault_voltage_factor
                        * data.circuit.line_to_earth_voltage_v
                        / z_pe
                    ),
                    6,
                )
                result["fault_loop_impedance_ohm"] = round(z_pe, 9)
        node_results.append(result)

    ordered_segments = sorted(data.circuit.segments, key=lambda item: item.sequence)
    append_node_result(0)
    for index, segment in enumerate(ordered_segments):
        electrical = by_segment_id[segment.id]
        load_flow = flow_by_segment_id[segment.id]
        length_km = segment.length_m / 1000
        segment_result: dict[str, Any] = {
            "segment_id": segment.id,
            "length_m": segment.length_m,
            "phase": segment.phase.value,
            "load_flow_phase": load_flow.phase.value,
            "load_flow_design_current_a": load_flow.design_current_a,
            "load_flow_power_factor": load_flow.power_factor,
            "corrected_ampacity_a": electrical.corrected_ampacity_a,
            "voltage_drop_v": None,
        }

        if (
            load_flow.design_current_a <= 0
            or not 0 < load_flow.power_factor <= 1
            or (segment.phase == Phase.SINGLE and load_flow.phase == Phase.THREE)
        ):
            voltage_drop_available = False
            warnings.append(f"线路段{segment.id}的负荷流参数不适用于本段线路。")

        if voltage_drop_available:
            voltage_values = (
                electrical.voltage_drop_r_ohm_per_km,
                electrical.voltage_drop_x_ohm_per_km,
            )
            if all(_finite_nonnegative(value) for value in voltage_values):
                r_total = float(voltage_values[0]) * length_km
                x_total = float(voltage_values[1]) * length_km
                sin_phi = sqrt(max(0.0, 1 - load_flow.power_factor**2))
                phase_multiplier = 2 if load_flow.phase == Phase.SINGLE else sqrt(3)
                segment_drop = (
                    phase_multiplier
                    * load_flow.design_current_a
                    * (
                        r_total * load_flow.power_factor
                        + x_total * sin_phi
                    )
                )
                cumulative_voltage_drop_v += segment_drop
                segment_result["voltage_drop_v"] = round(segment_drop, 6)
                steps.append(
                    Step(
                        f"线路段{segment.id}电压降",
                        (
                            f"{phase_multiplier:.6g}×Ib×"
                            "(Rcosφ＋Xsinφ)"
                        ),
                        segment_drop,
                        "V",
                    )
                )
            else:
                voltage_drop_available = False
                warnings.append(f"线路段{segment.id}缺少电压降R/X，累计压降在该点中断。")

        three_phase_values = (
            electrical.three_phase_r_ohm_per_km,
            electrical.three_phase_x_ohm_per_km,
        )
        if segment.phase == Phase.THREE and three_phase_available:
            if all(_finite_nonnegative(value) for value in three_phase_values):
                cumulative_three_phase_r += float(three_phase_values[0]) * length_km
                cumulative_three_phase_x += float(three_phase_values[1]) * length_km
            else:
                three_phase_available = False
                warnings.append(f"线路段{segment.id}缺少三相短路R/X。")
        elif segment.phase == Phase.SINGLE:
            three_phase_available = False

        phase_neutral_values = (
            electrical.phase_neutral_r_ohm_per_km,
            electrical.phase_neutral_x_ohm_per_km,
        )
        if not electrical.phase_neutral_applicable:
            phase_neutral_available = False
        elif phase_neutral_available:
            if all(_finite_nonnegative(value) for value in phase_neutral_values):
                cumulative_phase_neutral_r += (
                    float(phase_neutral_values[0]) * length_km
                )
                cumulative_phase_neutral_x += (
                    float(phase_neutral_values[1]) * length_km
                )
            else:
                phase_neutral_available = False
                warnings.append(f"线路段{segment.id}缺少相—N回路R/X。")

        phase_pe_values = (
            electrical.phase_pe_r_ohm_per_km,
            electrical.phase_pe_x_ohm_per_km,
        )
        if phase_pe_available:
            if all(_finite_nonnegative(value) for value in phase_pe_values):
                cumulative_phase_pe_r += float(phase_pe_values[0]) * length_km
                cumulative_phase_pe_x += float(phase_pe_values[1]) * length_km
            else:
                phase_pe_available = False
                warnings.append(f"线路段{segment.id}缺少相—PE回路R/X。")

        segment_results.append(segment_result)
        append_node_result(index + 1)

    outputs["segment_results"] = segment_results
    outputs["node_results"] = node_results
    outputs["terminal_voltage_drop_v"] = node_results[-1]["cumulative_voltage_drop_v"]
    outputs["terminal_voltage_drop_percent"] = node_results[-1][
        "cumulative_voltage_drop_percent"
    ]
    outputs["terminal_three_phase_short_circuit_ka"] = node_results[-1][
        "three_phase_short_circuit_ka"
    ]
    outputs["terminal_phase_neutral_short_circuit_a"] = node_results[-1][
        "phase_neutral_short_circuit_a"
    ]
    outputs["terminal_earth_fault_current_a"] = node_results[-1][
        "earth_fault_current_a"
    ]

    complete = (
        voltage_drop_available
        and (phase_neutral_available or not terminal_phase_neutral_applicable)
        and phase_pe_available
        and (
            data.circuit.load.phase == Phase.SINGLE
            or three_phase_available
        )
    )
    provisional = PASS if complete else UNKNOWN

    parameter_sets = (source, *data.segment_electrical, *data.segment_load_flows)
    parameters_approved = all(item.status == "approved" for item in parameter_sets)
    references_complete = all(item.source_reference_ids for item in parameter_sets)
    rules_approved = all(_rule_is_approved(rules, code) for code in rule_codes)
    formal = (
        PASS
        if provisional == PASS
        and parameters_approved
        and references_complete
        and rules_approved
        else UNKNOWN
    )
    if provisional == PASS and formal == UNKNOWN:
        warnings.append("计算链完整，但公式规则或电气参数尚未全部批准；结果仅用于暂算。")

    return Outcome(
        "完整回路公共计算链",
        ENGINE_VERSION,
        formal,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )
