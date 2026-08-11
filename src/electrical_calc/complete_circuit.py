"""完整低压放射式回路的领域模型与计算依赖。

本模块只描述回路结构、输入边界和结果失效关系，不包含规范数值、
设备目录或页面逻辑。照明、插座、普通设备和配电干线共用同一物理
计算内核，通过 circuit_application 与 load_profile 选择不同策略。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable


class StringEnum(str, Enum):
    """可稳定序列化的字符串枚举。"""

    def __str__(self) -> str:
        return self.value


class EarthingSystem(StringEnum):
    TN_S = "TN-S"
    TN_C_S = "TN-C-S"


class Phase(StringEnum):
    SINGLE = "1"
    THREE = "3"


class CircuitApplication(StringEnum):
    LIGHTING_FINAL = "lighting_final"
    SOCKET_FINAL = "socket_final"
    ORDINARY_EQUIPMENT_FINAL = "ordinary_equipment_final"
    MOTOR_FINAL = "motor_final"
    DISTRIBUTION = "distribution"


class ConnectionMode(StringEnum):
    FIXED_CONNECTED = "fixed_connected"
    SOCKET = "socket"
    DISTRIBUTION = "distribution"


class LoadProfile(StringEnum):
    LIGHTING = "lighting"
    SOCKET = "socket"
    ORDINARY_EQUIPMENT = "ordinary_equipment"
    MOTOR = "motor"
    MIXED_DISTRIBUTION = "mixed_distribution"


class InputBasis(StringEnum):
    ACTIVE_POWER_KW = "kw"
    APPARENT_POWER_KVA = "kva"
    CURRENT_A = "a"


class PowerDefinition(StringEnum):
    CALCULATED = "calculated"
    INSTALLED = "installed"


class DutyCharacteristic(StringEnum):
    ORDINARY_CONTINUOUS = "ordinary_continuous"
    INTERMITTENT = "intermittent"
    HIGH_INRUSH = "high_inrush"


class NodeType(StringEnum):
    TRANSFORMER_LV = "transformer_lv"
    MAIN_SWITCHBOARD = "main_switchboard"
    DISTRIBUTION_BOARD = "distribution_board"
    LOAD_TERMINAL = "load_terminal"


class SegmentType(StringEnum):
    CABLE = "cable"
    INSULATED_WIRE = "insulated_wire"
    BUSWAY = "busway"


class UpstreamNetworkMode(StringEnum):
    INFINITE_CAPACITY = "infinite_capacity"
    EXPLICIT_IMPEDANCE = "explicit_impedance"


class CalculationStage(StringEnum):
    LOAD_CURRENT = "load_current"
    SOURCE_IMPEDANCE = "source_impedance"
    BREAKER_CANDIDATES = "breaker_candidates"
    CONDUCTOR_CANDIDATES = "conductor_candidates"
    AMPACITY = "ampacity"
    VOLTAGE_DROP = "voltage_drop"
    THREE_PHASE_SHORT_CIRCUIT = "three_phase_short_circuit"
    EARTH_FAULT = "earth_fault"
    PROTECTION = "protection"
    THERMAL_WITHSTAND = "thermal_withstand"
    SELECTIVITY = "selectivity"
    COMBINATIONS = "combinations"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class CircuitNode:
    id: str
    sequence: int
    node_type: NodeType
    name: str
    nominal_voltage_v: float
    protection_device_id: str | None = None


@dataclass(frozen=True)
class CircuitSegment:
    id: str
    sequence: int
    from_node_id: str
    to_node_id: str
    segment_type: SegmentType
    phase: Phase
    length_m: float
    installation_scenario: str
    conductor_family: str | None = None
    construction_code: str | None = None
    phase_section_mm2: float | None = None
    neutral_section_mm2: float | None = None
    pe_section_mm2: float | None = None
    ambient_temperature_c: float | None = None
    grouping_condition: dict[str, Any] | None = None
    soil_condition: dict[str, Any] | None = None
    impedance_source_mode: str | None = None
    source_reference_id: str | None = None


@dataclass(frozen=True)
class Load:
    input_basis: InputBasis
    input_value: float
    phase: Phase
    circuit_application: CircuitApplication
    load_profile: LoadProfile
    duty_characteristic: DutyCharacteristic
    power_definition: PowerDefinition | None = None
    load_type_code: str | None = None
    power_factor: float | None = None
    efficiency: float | None = None
    demand_factor: float | None = None


@dataclass(frozen=True)
class PowerSource:
    transformer_family: str
    rated_capacity_kva: float
    hv_voltage_kv: float
    lv_voltage_kv: float
    vector_group: str
    uk_percent: float
    upstream_network_mode: UpstreamNetworkMode
    load_loss_kw: float | None = None
    lv_no_load_voltage_v: float | None = None
    upstream_r_ohm: float | None = None
    upstream_x_ohm: float | None = None


@dataclass(frozen=True)
class CompleteCircuit:
    id: str
    code: str
    name: str
    system_voltage_v: float
    line_to_earth_voltage_v: float
    frequency_hz: float
    earthing_system: EarthingSystem
    source: PowerSource
    load: Load
    nodes: tuple[CircuitNode, ...]
    segments: tuple[CircuitSegment, ...]
    rule_set_version: str
    neutral_pe_split_node_id: str | None = None

    def validate(self) -> tuple[ValidationIssue, ...]:
        return validate_complete_circuit(self)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


_APPLICATION_PROFILES = {
    CircuitApplication.LIGHTING_FINAL: frozenset({LoadProfile.LIGHTING}),
    CircuitApplication.SOCKET_FINAL: frozenset(
        {
            LoadProfile.SOCKET,
            LoadProfile.ORDINARY_EQUIPMENT,
            LoadProfile.MIXED_DISTRIBUTION,
        }
    ),
    CircuitApplication.ORDINARY_EQUIPMENT_FINAL: frozenset(
        {LoadProfile.ORDINARY_EQUIPMENT}
    ),
    CircuitApplication.MOTOR_FINAL: frozenset({LoadProfile.MOTOR}),
    # 配电干线按实际下游组成选择负荷特性，不强制视为混合负荷。
    CircuitApplication.DISTRIBUTION: frozenset(LoadProfile),
}


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _positive(
    issues: list[ValidationIssue],
    value: float | None,
    field: str,
    label: str,
) -> None:
    if value is None or value <= 0:
        issues.append(ValidationIssue("value.positive_required", field, f"{label}必须大于0。"))


def validate_complete_circuit(circuit: CompleteCircuit) -> tuple[ValidationIssue, ...]:
    """校验完整回路的结构和V1输入边界，不执行任何工程计算。"""

    issues: list[ValidationIssue] = []
    _positive(issues, circuit.system_voltage_v, "system_voltage_v", "系统电压")
    _positive(
        issues,
        circuit.line_to_earth_voltage_v,
        "line_to_earth_voltage_v",
        "相对地电压",
    )
    _positive(issues, circuit.frequency_hz, "frequency_hz", "频率")
    if not circuit.id.strip():
        issues.append(ValidationIssue("field.required", "id", "完整回路ID不能为空。"))
    if not circuit.code.strip():
        issues.append(ValidationIssue("field.required", "code", "回路编号不能为空。"))
    if not circuit.name.strip():
        issues.append(ValidationIssue("field.required", "name", "回路名称不能为空。"))
    if not circuit.rule_set_version.strip():
        issues.append(
            ValidationIssue("field.required", "rule_set_version", "规则集版本不能为空。")
        )

    source = circuit.source
    if not source.transformer_family.strip():
        issues.append(
            ValidationIssue(
                "field.required",
                "source.transformer_family",
                "变压器系列不能为空。",
            )
        )
    _positive(
        issues,
        source.rated_capacity_kva,
        "source.rated_capacity_kva",
        "变压器额定容量",
    )
    _positive(issues, source.hv_voltage_kv, "source.hv_voltage_kv", "高压侧额定电压")
    _positive(issues, source.lv_voltage_kv, "source.lv_voltage_kv", "低压侧额定电压")
    _positive(issues, source.uk_percent, "source.uk_percent", "短路阻抗电压")
    if not source.vector_group.strip():
        issues.append(
            ValidationIssue("field.required", "source.vector_group", "变压器接线组别不能为空。")
        )
    if source.upstream_network_mode == UpstreamNetworkMode.EXPLICIT_IMPEDANCE:
        if source.upstream_r_ohm is None or source.upstream_x_ohm is None:
            issues.append(
                ValidationIssue(
                    "source.upstream_impedance_required",
                    "source.upstream_r_ohm",
                    "上级系统采用显式阻抗时，R和X必须同时提供。",
                )
            )
        elif source.upstream_r_ohm < 0 or source.upstream_x_ohm < 0:
            issues.append(
                ValidationIssue(
                    "value.nonnegative_required",
                    "source.upstream_r_ohm",
                    "上级系统R和X不能为负数。",
                )
            )

    load = circuit.load
    _positive(issues, load.input_value, "load.input_value", "负荷已知量")
    allowed_profiles = _APPLICATION_PROFILES[load.circuit_application]
    if load.load_profile not in allowed_profiles:
        allowed_text = "、".join(sorted(item.value for item in allowed_profiles))
        issues.append(
            ValidationIssue(
                "load.profile_mismatch",
                "load.load_profile",
                (
                    f"{load.circuit_application.value}不适用于"
                    f"{load.load_profile.value}负荷策略；允许值为{allowed_text}。"
                ),
            )
        )
    if load.input_basis == InputBasis.ACTIVE_POWER_KW:
        if load.power_definition is None:
            issues.append(
                ValidationIssue(
                    "load.power_definition_required",
                    "load.power_definition",
                    "kW输入必须明确是计算功率还是安装功率。",
                )
            )
        if load.power_definition == PowerDefinition.INSTALLED and load.demand_factor is None:
            issues.append(
                ValidationIssue(
                    "load.demand_factor_required",
                    "load.demand_factor",
                    "安装功率必须提供需要系数；平台不得猜测。",
                )
            )
        if load.power_factor is None and not (load.load_type_code or "").strip():
            issues.append(
                ValidationIssue(
                    "load.power_factor_source_required",
                    "load.power_factor",
                    "kW输入必须提供功率因数，或提供可查取参数的负荷类型。",
                )
            )
    for field, value in (
        ("load.power_factor", load.power_factor),
        ("load.efficiency", load.efficiency),
        ("load.demand_factor", load.demand_factor),
    ):
        if value is not None and not 0 < value <= 1:
            issues.append(
                ValidationIssue(
                    "value.factor_range",
                    field,
                    f"{field}必须大于0且不大于1。",
                )
            )
    if load.circuit_application == CircuitApplication.MOTOR_FINAL:
        if load.phase != Phase.THREE:
            issues.append(
                ValidationIssue(
                    "motor.phase_not_supported",
                    "load.phase",
                    "V0.2.0电动机回路仅支持三相电动机。",
                    "unsupported",
                )
            )
        if load.input_basis == InputBasis.APPARENT_POWER_KVA:
            issues.append(
                ValidationIssue(
                    "motor.input_basis_not_supported",
                    "load.input_basis",
                    "V0.2.0电动机回路仅接受额定输出功率或铭牌额定电流。",
                    "unsupported",
                )
            )
        if load.input_basis == InputBasis.ACTIVE_POWER_KW:
            if load.power_definition != PowerDefinition.CALCULATED:
                issues.append(
                    ValidationIssue(
                        "motor.rated_output_power_required",
                        "load.power_definition",
                        "电动机kW输入必须定义为单机铭牌额定输出功率。",
                    )
                )
            if load.efficiency is None:
                issues.append(
                    ValidationIssue(
                        "motor.efficiency_required",
                        "load.efficiency",
                        "按电动机额定输出功率计算电流时必须提供效率。",
                    )
                )

    nodes = tuple(sorted(circuit.nodes, key=lambda item: item.sequence))
    if len(nodes) < 2:
        issues.append(
            ValidationIssue(
                "topology.nodes_required",
                "nodes",
                "完整回路至少需要电源节点和负荷端节点。",
            )
        )
    node_ids = [node.id for node in nodes]
    if any(not node_id.strip() for node_id in node_ids):
        issues.append(ValidationIssue("field.required", "nodes.id", "节点ID不能为空。"))
    if len(set(node_ids)) != len(node_ids):
        issues.append(ValidationIssue("topology.duplicate_node", "nodes", "节点ID不能重复。"))
    sequences = [node.sequence for node in nodes]
    if len(set(sequences)) != len(sequences):
        issues.append(
            ValidationIssue("topology.duplicate_sequence", "nodes.sequence", "节点顺序不能重复。")
        )
    for node in nodes:
        _positive(
            issues,
            node.nominal_voltage_v,
            f"nodes.{node.id}.nominal_voltage_v",
            f"节点{node.name}的标称电压",
        )
    if nodes and nodes[0].node_type != NodeType.TRANSFORMER_LV:
        issues.append(
            ValidationIssue(
                "topology.invalid_start",
                "nodes",
                "V1回路的第一个节点必须是变压器低压端。",
            )
        )
    if nodes and nodes[-1].node_type != NodeType.LOAD_TERMINAL:
        issues.append(
            ValidationIssue(
                "topology.invalid_end",
                "nodes",
                "V1回路的最后一个节点必须是负荷端。",
            )
        )

    if circuit.earthing_system == EarthingSystem.TN_C_S:
        split_id = (circuit.neutral_pe_split_node_id or "").strip()
        if not split_id:
            issues.append(
                ValidationIssue(
                    "earthing.split_node_required",
                    "neutral_pe_split_node_id",
                    "TN-C-S回路必须明确N与PE的分开节点。",
                )
            )
        elif split_id not in set(node_ids):
            issues.append(
                ValidationIssue(
                    "earthing.split_node_not_found",
                    "neutral_pe_split_node_id",
                    "N与PE分开节点不在当前回路拓扑中。",
                )
            )

    segments = tuple(sorted(circuit.segments, key=lambda item: item.sequence))
    if len(segments) != max(len(nodes) - 1, 0):
        issues.append(
            ValidationIssue(
                "topology.segment_count",
                "segments",
                "放射式回路每两个相邻节点之间必须且只能有一个线路段。",
            )
        )
    segment_ids = [segment.id for segment in segments]
    if any(not segment_id.strip() for segment_id in segment_ids):
        issues.append(ValidationIssue("field.required", "segments.id", "线路段ID不能为空。"))
    if len(set(segment_ids)) != len(segment_ids):
        issues.append(
            ValidationIssue("topology.duplicate_segment", "segments", "线路段ID不能重复。")
        )
    segment_sequences = [segment.sequence for segment in segments]
    if len(set(segment_sequences)) != len(segment_sequences):
        issues.append(
            ValidationIssue(
                "topology.duplicate_sequence",
                "segments.sequence",
                "线路段顺序不能重复。",
            )
        )
    for index, segment in enumerate(segments):
        _positive(
            issues,
            segment.length_m,
            f"segments.{segment.id}.length_m",
            f"线路段{segment.id}长度",
        )
        if not segment.installation_scenario.strip():
            issues.append(
                ValidationIssue(
                    "field.required",
                    f"segments.{segment.id}.installation_scenario",
                    f"线路段{segment.id}必须明确敷设场景。",
                )
            )
        if segment.segment_type != SegmentType.BUSWAY and not (
            segment.conductor_family or ""
        ).strip():
            issues.append(
                ValidationIssue(
                    "field.required",
                    f"segments.{segment.id}.conductor_family",
                    f"线路段{segment.id}必须明确允许的导体型号。",
                )
            )
        if index < len(nodes) - 1:
            expected_from = nodes[index].id
            expected_to = nodes[index + 1].id
            if (
                segment.from_node_id != expected_from
                or segment.to_node_id != expected_to
            ):
                issues.append(
                    ValidationIssue(
                        "topology.disconnected_segment",
                        f"segments.{segment.id}",
                        (
                            f"线路段{segment.id}应连接相邻节点"
                            f"{expected_from}→{expected_to}。"
                        ),
                    )
                )

    single_phase_started = False
    for segment in segments:
        if segment.phase == Phase.SINGLE:
            single_phase_started = True
        elif single_phase_started:
            issues.append(
                ValidationIssue(
                    "topology.phase_reversal",
                    f"segments.{segment.id}.phase",
                    "线路进入单相分支后，下游线路段不能重新变为三相。",
                )
            )
    if segments and segments[-1].phase != load.phase:
        issues.append(
            ValidationIssue(
                "topology.load_phase_mismatch",
                f"segments.{segments[-1].id}.phase",
                "最后一个线路段的相制必须与负荷相制一致。",
            )
        )

    return tuple(issues)


_STAGE_ORDER = tuple(CalculationStage)
_ALL_STAGES = frozenset(_STAGE_ORDER)
_CHANGE_IMPACTS = {
    "rules": _ALL_STAGES,
    "catalog": _ALL_STAGES,
    "topology": _ALL_STAGES,
    "nodes": _ALL_STAGES,
    "circuit.earthing_system": _ALL_STAGES,
    "circuit.system_voltage_v": _ALL_STAGES,
    "circuit.line_to_earth_voltage_v": _ALL_STAGES,
    "load": frozenset(
        {
            CalculationStage.LOAD_CURRENT,
            CalculationStage.BREAKER_CANDIDATES,
            CalculationStage.CONDUCTOR_CANDIDATES,
            CalculationStage.AMPACITY,
            CalculationStage.VOLTAGE_DROP,
            CalculationStage.THREE_PHASE_SHORT_CIRCUIT,
            CalculationStage.EARTH_FAULT,
            CalculationStage.PROTECTION,
            CalculationStage.THERMAL_WITHSTAND,
            CalculationStage.SELECTIVITY,
            CalculationStage.COMBINATIONS,
        }
    ),
    "source": frozenset(
        {
            CalculationStage.SOURCE_IMPEDANCE,
            CalculationStage.VOLTAGE_DROP,
            CalculationStage.THREE_PHASE_SHORT_CIRCUIT,
            CalculationStage.EARTH_FAULT,
            CalculationStage.PROTECTION,
            CalculationStage.THERMAL_WITHSTAND,
            CalculationStage.SELECTIVITY,
            CalculationStage.COMBINATIONS,
        }
    ),
    "segments": frozenset(
        {
            CalculationStage.CONDUCTOR_CANDIDATES,
            CalculationStage.AMPACITY,
            CalculationStage.VOLTAGE_DROP,
            CalculationStage.THREE_PHASE_SHORT_CIRCUIT,
            CalculationStage.EARTH_FAULT,
            CalculationStage.PROTECTION,
            CalculationStage.THERMAL_WITHSTAND,
            CalculationStage.SELECTIVITY,
            CalculationStage.COMBINATIONS,
        }
    ),
    "protection": frozenset(
        {
            CalculationStage.BREAKER_CANDIDATES,
            CalculationStage.PROTECTION,
            CalculationStage.THERMAL_WITHSTAND,
            CalculationStage.SELECTIVITY,
            CalculationStage.COMBINATIONS,
        }
    ),
}


def affected_calculation_stages(changed_paths: Iterable[str]) -> tuple[CalculationStage, ...]:
    """返回输入变化后必须失效的计算阶段，顺序固定且可重复。"""

    affected: set[CalculationStage] = set()
    for path in changed_paths:
        normalized = str(path).strip()
        if not normalized:
            continue
        matching_keys = [
            key
            for key in _CHANGE_IMPACTS
            if normalized == key or normalized.startswith(f"{key}.")
        ]
        if matching_keys:
            most_specific = max(matching_keys, key=len)
            affected.update(_CHANGE_IMPACTS[most_specific])
        else:
            # 未登记字段不能静默保留旧结果；采用全量失效的安全路径。
            affected.update(_ALL_STAGES)
    return tuple(stage for stage in _STAGE_ORDER if stage in affected)
