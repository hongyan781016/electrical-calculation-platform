"""用户可录入的完整放射式回路输入编排。

本模块把页面能够取得的工程条件转换为既有完整回路引擎输入。它只
负责结构化输入、统一校验和已知量推导，不在页面层重复电气计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import sqrt
from typing import Any

from .cable_selector import CableInstallationConditions, CableSelectionRequest
from .combination_solver import ProtectionPoint
from .complete_circuit import (
    CircuitApplication,
    CircuitNode,
    CircuitSegment,
    CompleteCircuit,
    DutyCharacteristic,
    EarthingSystem,
    InputBasis,
    Load,
    LoadProfile,
    NodeType,
    Phase,
    PowerDefinition,
    PowerSource,
    SegmentType,
    UpstreamNetworkMode,
)
from .complete_circuit_engine import ResolvedSegmentLoadFlow
from .drawing_audit import DrawingCircuitAuditRequest, InstalledBreaker, InstalledCable
from .motor import MotorCatalogQuery
from .motor_catalog import resolve_motor_reference_parameters
from .radial_circuit_service import RadialCircuitCalculationRequest


class CircuitTaskMode(str, Enum):
    DESIGN = "design"
    AUDIT = "audit"


class TerminalLoadKind(str, Enum):
    ORDINARY = "ordinary"
    MOTOR = "motor"


@dataclass(frozen=True)
class ExistingBreakerInput:
    designation: str
    rated_current_a: float | None
    frame_current_a: float | None
    rated_voltage_v: float | None
    breaking_capacity_ka: float | None
    guaranteed_action_current_a: float | None = None


@dataclass(frozen=True)
class FeederSegmentInput:
    id: str
    label: str
    length_m: float
    conductor_family: str = "YJV"
    configuration_code: str = "yjv_4c_3ph_n_pe"
    installation_scenario: str = "tray"
    temperature_c: float = 40.0
    tray_type: str = "horizontal_perforated"
    tray_layers: int = 1
    tray_cables_per_layer: int = 1
    existing_phase_section_mm2: float | None = None
    existing_breaker: ExistingBreakerInput | None = None


@dataclass(frozen=True)
class CircuitNetworkInput:
    task_mode: CircuitTaskMode
    circuit_code: str
    circuit_name: str
    transformer_family: str
    transformer_capacity_kva: float
    transformer_uk_percent: float
    upstream_short_circuit_capacity_mva: float
    load_kind: TerminalLoadKind
    load_basis: InputBasis
    load_value: float
    power_factor: float
    segments: tuple[FeederSegmentInput, ...]
    system_voltage_v: float = 380.0
    line_to_earth_voltage_v: float = 220.0
    voltage_drop_limit_percent: float = 5.0
    motor_poles: int = 4


@dataclass(frozen=True)
class NetworkBuildResult:
    radial_request: RadialCircuitCalculationRequest | None
    audit_request: DrawingCircuitAuditRequest | None
    errors: tuple[str, ...]
    notices: tuple[str, ...]
    derived: dict[str, Any]


def _positive(value: float, label: str, errors: list[str]) -> None:
    if value <= 0:
        errors.append(f"{label}必须大于0。")


def _design_current(
    basis: InputBasis,
    value: float,
    voltage_v: float,
    power_factor: float,
    efficiency: float | None,
) -> float:
    if basis == InputBasis.CURRENT_A:
        return value
    if basis == InputBasis.APPARENT_POWER_KVA:
        return value * 1000 / (sqrt(3) * voltage_v)
    denominator = sqrt(3) * voltage_v * power_factor
    if efficiency is not None:
        denominator *= efficiency
    return value * 1000 / denominator


def build_circuit_network_requests(
    data: CircuitNetworkInput,
    rules: dict[str, dict[str, Any]],
) -> NetworkBuildResult:
    """建立计算请求；所有错误一次返回，不生成部分拓扑。"""

    errors: list[str] = []
    notices: list[str] = []
    if not data.circuit_code.strip():
        errors.append("回路编号不能为空。")
    if not data.circuit_name.strip():
        errors.append("回路名称不能为空。")
    for value, label in (
        (data.transformer_capacity_kva, "变压器容量"),
        (data.transformer_uk_percent, "变压器uk%"),
        (data.upstream_short_circuit_capacity_mva, "上级系统短路容量"),
        (data.load_value, "负荷已知量"),
        (data.system_voltage_v, "系统电压"),
        (data.voltage_drop_limit_percent, "允许电压降"),
    ):
        _positive(value, label, errors)
    if not 0 < data.power_factor <= 1:
        errors.append("功率因数必须大于0且不大于1。")
    if len(data.segments) != 3:
        errors.append("V0.3首版工程化入口必须包含三段连续线路。")
    if len({item.id for item in data.segments}) != len(data.segments):
        errors.append("线路段编号不能重复。")
    for segment in data.segments:
        _positive(segment.length_m, f"{segment.label}长度", errors)
        if segment.conductor_family != "YJV":
            errors.append(f"{segment.label}当前只接入YJV铜芯电缆目录。")
        if segment.configuration_code not in {
            "yjv_3c_3ph_pe",
            "yjv_4c_3ph_n_pe",
            "yjv_5c_3ph_n_pe",
        }:
            errors.append(f"{segment.label}电缆结构不在当前完整回路目录。")
        if segment.installation_scenario not in {"tray", "direct_buried"}:
            errors.append(f"{segment.label}当前只接入槽盒或埋地管槽工况。")
        if data.task_mode == CircuitTaskMode.AUDIT:
            if segment.existing_phase_section_mm2 is None:
                errors.append(f"既有核验必须填写{segment.label}原电缆相线截面。")
            if segment.existing_breaker is None:
                errors.append(f"既有核验必须填写{segment.label}原断路器参数。")

    efficiency: float | None = None
    load_power_factor = data.power_factor
    motor_reference: dict[str, Any] | None = None
    if data.load_kind == TerminalLoadKind.MOTOR:
        if data.load_basis != InputBasis.ACTIVE_POWER_KW:
            errors.append("完整回路中的目录电动机当前必须按铭牌输出功率kW输入。")
        else:
            series_code = "1LE1503" if data.load_value > 30 else "1LE1003"
            catalog = resolve_motor_reference_parameters(
                MotorCatalogQuery(data.load_value, data.motor_poles, series_code),
                rules,
            )
            if not catalog.outputs.get("matched"):
                errors.extend(catalog.warnings)
            else:
                efficiency = float(catalog.outputs["efficiency"])
                load_power_factor = float(catalog.outputs["power_factor"])
                motor_reference = catalog.outputs
                notices.append(
                    "电动机效率、功率因数和启动倍数来自厂家目录精确功率行；正式复核仍以铭牌为准。"
                )

    if errors:
        return NetworkBuildResult(None, None, tuple(errors), tuple(notices), {})

    design_current_a = _design_current(
        data.load_basis,
        data.load_value,
        data.system_voltage_v,
        load_power_factor,
        efficiency,
    )
    application = (
        CircuitApplication.MOTOR_FINAL
        if data.load_kind == TerminalLoadKind.MOTOR
        else CircuitApplication.ORDINARY_EQUIPMENT_FINAL
    )
    load_profile = (
        LoadProfile.MOTOR
        if data.load_kind == TerminalLoadKind.MOTOR
        else LoadProfile.ORDINARY_EQUIPMENT
    )
    node_specs = (
        ("tx", NodeType.TRANSFORMER_LV, "变压器低压出口"),
        ("main", NodeType.MAIN_SWITCHBOARD, "低压馈线柜"),
        ("db", NodeType.DISTRIBUTION_BOARD, "下级配电箱"),
        ("load", NodeType.LOAD_TERMINAL, "用电设备末端"),
    )
    circuit = CompleteCircuit(
        id="engineering-radial-current",
        code=data.circuit_code.strip(),
        name=data.circuit_name.strip(),
        system_voltage_v=data.system_voltage_v,
        line_to_earth_voltage_v=data.line_to_earth_voltage_v,
        frequency_hz=50,
        earthing_system=EarthingSystem.TN_S,
        source=PowerSource(
            transformer_family=data.transformer_family,
            rated_capacity_kva=data.transformer_capacity_kva,
            hv_voltage_kv=10,
            lv_voltage_kv=0.4,
            vector_group="Dyn11",
            uk_percent=data.transformer_uk_percent,
            upstream_network_mode=UpstreamNetworkMode.EXPLICIT_IMPEDANCE,
        ),
        load=Load(
            input_basis=data.load_basis,
            input_value=data.load_value,
            phase=Phase.THREE,
            circuit_application=application,
            load_profile=load_profile,
            duty_characteristic=(
                DutyCharacteristic.HIGH_INRUSH
                if data.load_kind == TerminalLoadKind.MOTOR
                else DutyCharacteristic.ORDINARY_CONTINUOUS
            ),
            power_definition=(
                PowerDefinition.CALCULATED
                if data.load_basis == InputBasis.ACTIVE_POWER_KW
                else None
            ),
            power_factor=load_power_factor,
            efficiency=efficiency,
        ),
        nodes=tuple(
            CircuitNode(node_id, index, node_type, name, data.system_voltage_v)
            for index, (node_id, node_type, name) in enumerate(node_specs)
        ),
        segments=tuple(
            CircuitSegment(
                segment.id,
                index,
                node_specs[index][0],
                node_specs[index + 1][0],
                SegmentType.CABLE,
                Phase.THREE,
                segment.length_m,
                segment.installation_scenario,
                conductor_family=segment.conductor_family,
                construction_code=segment.configuration_code,
                ambient_temperature_c=segment.temperature_c,
            )
            for index, segment in enumerate(data.segments)
        ),
        rule_set_version="v0.3-engineering-input-1",
    )
    flows = tuple(
        ResolvedSegmentLoadFlow(
            segment.id,
            design_current_a,
            load_power_factor,
            Phase.THREE,
            "derived_from_terminal_load",
            ("INPUT:TERMINAL_LOAD",),
        )
        for segment in data.segments
    )

    def cable_request(segment: FeederSegmentInput) -> CableSelectionRequest:
        conditions = CableInstallationConditions(
            temperature_c=segment.temperature_c,
            tray_type=(segment.tray_type if segment.installation_scenario == "tray" else None),
            tray_layers=(segment.tray_layers if segment.installation_scenario == "tray" else None),
            tray_cables_per_layer=(
                segment.tray_cables_per_layer
                if segment.installation_scenario == "tray"
                else None
            ),
            soil_thermal_resistivity_k_m_per_w=(
                1.0 if segment.installation_scenario == "direct_buried" else None
            ),
            buried_circuit_count=(
                1 if segment.installation_scenario == "direct_buried" else None
            ),
            buried_duct_spacing_m=(
                "0.25" if segment.installation_scenario == "direct_buried" else None
            ),
            buried_depth_m=(
                0.7 if segment.installation_scenario == "direct_buried" else None
            ),
        )
        return CableSelectionRequest(
            segment_id=segment.id,
            family=segment.conductor_family,
            configuration_code=segment.configuration_code,
            phase=Phase.THREE,
            system_voltage_v=data.system_voltage_v,
            installation_scenario=segment.installation_scenario,
            minimum_required_ampacity_a=design_current_a,
            neutral_required=False,
            protective_conductor_mode="included",
            conditions=conditions,
        )

    cable_requests = tuple(cable_request(segment) for segment in data.segments)
    protection_points = tuple(
        ProtectionPoint(
            node_specs[index][0],
            segment.id,
            application if index == len(data.segments) - 1 else CircuitApplication.DISTRIBUTION,
            ("MCCB",),
            "3P",
        )
        for index, segment in enumerate(data.segments)
    )
    radial = RadialCircuitCalculationRequest(
        circuit=circuit,
        segment_load_flows=flows,
        cable_requests=cable_requests,
        protection_points=protection_points,
        upstream_short_circuit_capacity_mva=data.upstream_short_circuit_capacity_mva,
        voltage_drop_limit_pct=data.voltage_drop_limit_percent,
        voltage_drop_limit_rule_code="ELEC.VDROP.LIMIT",
        maximum_cable_combinations=10,
        maximum_output_combinations=1,
        maximum_candidates_per_cable_segment=1,
    )

    audit: DrawingCircuitAuditRequest | None = None
    if data.task_mode == CircuitTaskMode.AUDIT:
        installed_cables = tuple(
            InstalledCable(
                segment.id,
                (
                    f"原电缆：{segment.conductor_family} "
                    f"{segment.configuration_code} {segment.existing_phase_section_mm2:g}mm²"
                ),
                float(segment.existing_phase_section_mm2),
                request,
            )
            for segment, request in zip(data.segments, cable_requests, strict=True)
        )
        installed_breakers = tuple(
            InstalledBreaker(
                node_specs[index][0],
                segment.id,
                segment.existing_breaker.designation,
                segment.existing_breaker.rated_current_a,
                segment.existing_breaker.frame_current_a,
                segment.existing_breaker.rated_voltage_v,
                segment.existing_breaker.breaking_capacity_ka,
                segment.existing_breaker.guaranteed_action_current_a,
            )
            for index, segment in enumerate(data.segments)
            if segment.existing_breaker is not None
        )
        audit = DrawingCircuitAuditRequest(radial, installed_cables, installed_breakers)

    derived = {
        "design_current_a": round(design_current_a, 6),
        "power_factor": load_power_factor,
        "efficiency": efficiency,
        "upstream_reference_capacity_mva": data.upstream_short_circuit_capacity_mva,
        "motor_reference": motor_reference,
    }
    notices.append("上级系统短路容量用于折算等值阻抗，不要求用户填写R/X。")
    return NetworkBuildResult(radial, audit, (), tuple(notices), derived)
