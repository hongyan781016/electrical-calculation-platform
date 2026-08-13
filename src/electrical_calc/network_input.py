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
from .catalog import lookup_canalis_kta_3lnpe_electrical
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
from .complete_circuit_engine import ResolvedSegmentElectrical, ResolvedSegmentLoadFlow
from .drawing_audit import (
    DrawingCircuitAuditRequest,
    InstalledAssembly,
    InstalledBreaker,
    InstalledBusway,
    InstalledCable,
    InstalledIncomingBreaker,
)
from .motor import MotorCatalogQuery, MotorKnownBasis, MotorLoadInput
from .motor_catalog import resolve_motor_reference_parameters
from .motor_engine import calculate_motor_load
from .radial_circuit_service import RadialCircuitCalculationRequest
from .engine import UNKNOWN


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
    segment_type: SegmentType = SegmentType.CABLE
    busway_series_code: str | None = None
    busway_rating_a: float | None = None
    phase: Phase = Phase.THREE
    existing_pe_section_mm2: float | None = None
    mcb_trip_curve: str | None = None


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
    installed_assemblies: tuple[InstalledAssembly, ...] = ()
    transformer_actual_model: str = ""
    terminal_phase: Phase = Phase.THREE
    upstream_design_current_a: float | None = None
    installed_incoming_breakers: tuple[InstalledIncomingBreaker, ...] = ()


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
    phase: Phase = Phase.THREE,
) -> float:
    if basis == InputBasis.CURRENT_A:
        return value
    if basis == InputBasis.APPARENT_POWER_KVA:
        return value * 1000 / (
            sqrt(3) * voltage_v if phase == Phase.THREE else voltage_v
        )
    denominator = (
        sqrt(3) * voltage_v * power_factor
        if phase == Phase.THREE
        else voltage_v * power_factor
    )
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
    if data.upstream_design_current_a is not None:
        _positive(data.upstream_design_current_a, "末端配电箱进线计算电流", errors)
    expected_model = {"scb11": "SCB11", "s11_m": "S11-M"}.get(data.transformer_family)
    if data.transformer_actual_model.strip() and expected_model and not data.transformer_actual_model.upper().startswith(expected_model):
        notices.append(
            f"图纸实际型号{data.transformer_actual_model.strip()}与阻抗计算参考系列{expected_model}不一致；本次阻抗仅作参考，正式型号匹配无法判断。"
        )
    if len(data.segments) != 3:
        errors.append("当前完整链路入口必须包含出口连接段、配电馈线段和末端分支段三段连续线路。")
    if len({item.id for item in data.segments}) != len(data.segments):
        errors.append("线路段编号不能重复。")
    for segment in data.segments:
        if segment.segment_type == SegmentType.INTERNAL_CONNECTION:
            if segment.id != "connection":
                errors.append("柜内连接边界只允许作为变压器低压出口连接段。")
            if segment.length_m != 0:
                errors.append("柜内连接边界长度必须为0。")
        else:
            _positive(segment.length_m, f"{segment.label}长度", errors)
        if segment.segment_type == SegmentType.BUSWAY:
            if segment.id != "connection":
                errors.append("当前母线槽只允许作为变压器低压出口连接段。")
            if segment.busway_series_code != "canalis_kta_3lnpe":
                errors.append(f"{segment.label}母线槽系列不在当前精确目录。")
            if segment.busway_rating_a is None:
                errors.append(f"{segment.label}必须填写图纸母线槽额定电流。")
        elif segment.segment_type != SegmentType.INTERNAL_CONNECTION and segment.conductor_family not in {"YJV", "BV"}:
            errors.append(f"{segment.label}当前只接入YJV电缆或BV铜芯绝缘电线目录。")
        if segment.segment_type not in {SegmentType.BUSWAY, SegmentType.INTERNAL_CONNECTION} and segment.configuration_code not in {
            "yjv_3c_3ph_pe",
            "yjv_4c_3ph_n_pe",
            "yjv_5c_3ph_n_pe",
            "yjv_4c_3ph_n_separate_pe",
            "bv_1ph_2wire_pe",
        }:
            errors.append(f"{segment.label}电缆结构不在当前完整回路目录。")
        if segment.segment_type != SegmentType.INTERNAL_CONNECTION and segment.installation_scenario not in {"tray", "direct_buried", "conduit"}:
            errors.append(f"{segment.label}当前只接入槽盒、穿管或埋地管槽工况。")
        if segment.conductor_family == "BV" and (
            segment.phase != Phase.SINGLE
            or segment.configuration_code != "bv_1ph_2wire_pe"
            or segment.installation_scenario != "conduit"
        ):
            errors.append(f"{segment.label}的BV目录当前只支持单相L/N单芯线＋独立PE穿管。")
        if data.task_mode == CircuitTaskMode.AUDIT:
            if (
                segment.segment_type not in {SegmentType.BUSWAY, SegmentType.INTERNAL_CONNECTION}
                and segment.existing_phase_section_mm2 is None
            ):
                errors.append(f"既有核验必须填写{segment.label}原电缆相线截面。")
            if (
                segment.segment_type != SegmentType.INTERNAL_CONNECTION
                and segment.existing_breaker is None
            ):
                errors.append(f"既有核验必须填写{segment.label}原断路器参数。")

    efficiency: float | None = None
    load_power_factor = data.power_factor
    motor_reference: dict[str, Any] | None = None
    motor_calculation: dict[str, Any] | None = None
    if data.load_kind == TerminalLoadKind.MOTOR:
        if data.terminal_phase != Phase.THREE:
            errors.append("当前目录电动机只支持三相末端，不能选择单相照明分支。")
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
                motor_calculation = calculate_motor_load(
                    MotorLoadInput(
                        known_basis=MotorKnownBasis.RATED_OUTPUT_POWER_KW,
                        known_value=data.load_value,
                        rated_voltage_v=data.system_voltage_v,
                        power_factor=load_power_factor,
                        efficiency=efficiency,
                        locked_rotor_current_ratio=float(
                            catalog.outputs["locked_rotor_current_ratio"]
                        ),
                    ),
                    rules,
                ).to_dict()
                notices.append(
                    "电动机效率、功率因数和启动倍数来自厂家目录精确功率行；正式复核仍以铭牌为准。"
                )

    if errors:
        return NetworkBuildResult(None, None, tuple(errors), tuple(notices), {})

    design_current_a = (
        float(motor_calculation["outputs"]["rated_current_a"])
        if motor_calculation is not None
        else _design_current(
            data.load_basis,
            data.load_value,
            (
                data.line_to_earth_voltage_v
                if data.terminal_phase == Phase.SINGLE
                else data.system_voltage_v
            ),
            load_power_factor,
            efficiency,
            data.terminal_phase,
        )
    )
    application = (
        CircuitApplication.MOTOR_FINAL
        if data.load_kind == TerminalLoadKind.MOTOR
        else CircuitApplication.LIGHTING_FINAL
        if data.terminal_phase == Phase.SINGLE
        else CircuitApplication.ORDINARY_EQUIPMENT_FINAL
    )
    load_profile = (
        LoadProfile.MOTOR
        if data.load_kind == TerminalLoadKind.MOTOR
        else LoadProfile.LIGHTING
        if data.terminal_phase == Phase.SINGLE
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
            phase=data.terminal_phase,
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
                segment.segment_type,
                segment.phase,
                segment.length_m,
                segment.installation_scenario,
                conductor_family=(
                    None if segment.segment_type in {SegmentType.BUSWAY, SegmentType.INTERNAL_CONNECTION} else segment.conductor_family
                ),
                construction_code=(
                    segment.busway_series_code if segment.segment_type == SegmentType.BUSWAY
                    else "internal_boundary" if segment.segment_type == SegmentType.INTERNAL_CONNECTION
                    else segment.configuration_code
                ),
                ambient_temperature_c=segment.temperature_c,
            )
            for index, segment in enumerate(data.segments)
        ),
        rule_set_version="v0.3-engineering-input-1",
    )
    terminal_segment_id = data.segments[-1].id
    flows = tuple(
        ResolvedSegmentLoadFlow(
            segment.id,
            (
                design_current_a
                if segment.id == terminal_segment_id
                else data.upstream_design_current_a or design_current_a
            ),
            load_power_factor,
            segment.phase,
            (
                "drawing_distribution_board_current"
                if segment.id != terminal_segment_id and data.upstream_design_current_a
                else "derived_from_terminal_load"
            ),
            (
                ("INPUT:DISTRIBUTION_BOARD_DESIGN_CURRENT",)
                if segment.id != terminal_segment_id and data.upstream_design_current_a
                else ("INPUT:TERMINAL_LOAD",)
            ),
        )
        for segment in data.segments
    )

    flow_by_segment = {item.segment_id: item for item in flows}

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
            phase=segment.phase,
            system_voltage_v=data.system_voltage_v,
            installation_scenario=segment.installation_scenario,
            minimum_required_ampacity_a=flow_by_segment[segment.id].design_current_a,
            neutral_required=(
                segment.phase == Phase.SINGLE
                or data.terminal_phase == Phase.SINGLE
            ),
            protective_conductor_mode=(
                "separate"
                if segment.conductor_family == "BV"
                or segment.configuration_code == "yjv_4c_3ph_n_separate_pe"
                else "included"
            ),
            conditions=conditions,
            separate_protective_section_mm2=segment.existing_pe_section_mm2,
        )

    cable_requests = tuple(
        cable_request(segment)
        for segment in data.segments
        if segment.segment_type not in {SegmentType.BUSWAY, SegmentType.INTERNAL_CONNECTION}
    )
    fixed_segment_electrical: tuple[ResolvedSegmentElectrical, ...] = ()
    busway_inputs: list[dict[str, Any]] = []
    for segment in data.segments:
        if segment.segment_type == SegmentType.INTERNAL_CONNECTION:
            fixed_segment_electrical += (ResolvedSegmentElectrical(
                segment_id=segment.id, phase_neutral_applicable=True,
                voltage_drop_r_ohm_per_km=0.0, voltage_drop_x_ohm_per_km=0.0,
                three_phase_r_ohm_per_km=0.0, three_phase_x_ohm_per_km=0.0,
                phase_neutral_r_ohm_per_km=0.0, phase_neutral_x_ohm_per_km=0.0,
                phase_pe_r_ohm_per_km=0.0, phase_pe_x_ohm_per_km=0.0,
                corrected_ampacity_a=None, status=UNKNOWN,
                source_reference_ids=("INPUT:INTERNAL_CONNECTION_BOUNDARY",),
            ),)
            notices.append("变压器至低压柜按柜内连接边界处理：不计线路长度和阻抗；柜内母排另在上游设备层核验。")
            continue
        if segment.segment_type != SegmentType.BUSWAY:
            continue
        resolved = lookup_canalis_kta_3lnpe_electrical(
            float(segment.busway_rating_a), segment.temperature_c
        )
        if resolved is None:
            errors.append(
                f"{segment.label}母线槽额定电流或环境温度没有精确表列组合；不插值。"
            )
            continue
        fixed_segment_electrical += (ResolvedSegmentElectrical(
            segment_id=segment.id,
            phase_neutral_applicable=True,
            voltage_drop_r_ohm_per_km=resolved["voltage_drop_r_ohm_per_km"],
            voltage_drop_x_ohm_per_km=resolved["voltage_drop_x_ohm_per_km"],
            three_phase_r_ohm_per_km=resolved["three_phase_r_ohm_per_km"],
            three_phase_x_ohm_per_km=resolved["three_phase_x_ohm_per_km"],
            phase_neutral_r_ohm_per_km=resolved["phase_neutral_r_ohm_per_km"],
            phase_neutral_x_ohm_per_km=resolved["phase_neutral_x_ohm_per_km"],
            phase_pe_r_ohm_per_km=resolved["phase_pe_r_ohm_per_km"],
            phase_pe_x_ohm_per_km=resolved["phase_pe_x_ohm_per_km"],
            corrected_ampacity_a=resolved["corrected_ampacity_a"],
            status=resolved["status"],
            source_reference_ids=(resolved["source_rule_code"],),
        ),)
        busway_inputs.append(resolved)
    if errors:
        return NetworkBuildResult(None, None, tuple(errors), tuple(notices), {})
    protection_points = tuple(
        ProtectionPoint(
            node_specs[index][0],
            segment.id,
            application if index == len(data.segments) - 1 else CircuitApplication.DISTRIBUTION,
            (("MCB",) if segment.phase == Phase.SINGLE else ("MCCB",)),
            ("1P" if segment.phase == Phase.SINGLE else "3P"),
            mcb_trip_curve=(segment.mcb_trip_curve if segment.phase == Phase.SINGLE else None),
        )
        for index, segment in enumerate(data.segments)
        if segment.segment_type != SegmentType.INTERNAL_CONNECTION
    )
    radial = RadialCircuitCalculationRequest(
        circuit=circuit,
        segment_load_flows=flows,
        cable_requests=cable_requests,
        protection_points=protection_points,
        upstream_short_circuit_capacity_mva=data.upstream_short_circuit_capacity_mva,
        voltage_drop_limit_pct=data.voltage_drop_limit_percent,
        voltage_drop_limit_rule_code="ELEC.VDROP.LIMIT",
        fixed_segment_electrical=fixed_segment_electrical,
        maximum_cable_combinations=(24 if data.terminal_phase == Phase.SINGLE else 10),
        maximum_output_combinations=1,
        maximum_candidates_per_cable_segment=(4 if data.terminal_phase == Phase.SINGLE else 1),
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
            for segment, request in zip(
                (item for item in data.segments if item.segment_type not in {SegmentType.BUSWAY, SegmentType.INTERNAL_CONNECTION}),
                cable_requests,
                strict=True,
            )
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
        audit = DrawingCircuitAuditRequest(
            radial,
            installed_cables,
            installed_breakers,
            data.installed_assemblies,
            tuple(
                InstalledBusway(
                    segment.id,
                    resolved["series_name"] + f" {resolved['rating_a']:g}A",
                    resolved["rating_a"], resolved["rated_voltage_v"],
                    resolved["short_time_withstand_ka_1s"],
                    resolved["corrected_ampacity_a"],
                    f"{resolved['document_reference']}；{resolved['page']}",
                )
                for segment, resolved in zip(
                    (item for item in data.segments if item.segment_type == SegmentType.BUSWAY),
                    busway_inputs,
                    strict=True,
                )
            ),
            data.installed_incoming_breakers,
        )

    derived = {
        "design_current_a": round(design_current_a, 6),
        "transformer_actual_model": data.transformer_actual_model.strip(),
        "transformer_reference_family": data.transformer_family,
        "power_factor": load_power_factor,
        "efficiency": efficiency,
        "upstream_reference_capacity_mva": data.upstream_short_circuit_capacity_mva,
        "motor_reference": motor_reference,
        "motor_calculation": motor_calculation,
        "busway_inputs": busway_inputs,
        "terminal_phase": data.terminal_phase.value,
        "upstream_design_current_a": data.upstream_design_current_a,
    }
    notices.append("上级系统短路容量用于折算等值阻抗，不要求用户填写R/X。")
    return NetworkBuildResult(radial, audit, (), tuple(notices), derived)
