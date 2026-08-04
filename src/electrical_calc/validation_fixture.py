"""公开版本使用的匿名完整回路验收夹具。"""

from __future__ import annotations

from math import sqrt

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
from .radial_circuit_service import RadialCircuitCalculationRequest


SEGMENT_LABELS = {
    "connection": "变压器低压出口 → 低压柜",
    "feeder": "低压柜 → 分配电箱",
    "final": "分配电箱 → 用电设备",
}


def build_validation_fixture_requests(
    lengths_m: dict[str, float] | None = None,
) -> tuple[RadialCircuitCalculationRequest, DrawingCircuitAuditRequest]:
    """建立不含客户信息的四节点、三线路、三级保护测试链。"""

    lengths = {"connection": 10.0, "feeder": 50.0, "final": 30.0}
    if lengths_m:
        lengths.update(lengths_m)
    final_current = 30_000 / (sqrt(3) * 380 * 0.9)
    segment_specs = (
        ("connection", "tx", "main", lengths["connection"], 200.0),
        ("feeder", "main", "db", lengths["feeder"], 125.0),
        ("final", "db", "load", lengths["final"], final_current),
    )
    circuit = CompleteCircuit(
        id="validation-radial-01",
        code="T1-MDB-DB-L1",
        name="匿名完整低压放射式回路",
        system_voltage_v=380,
        line_to_earth_voltage_v=220,
        frequency_hz=50,
        earthing_system=EarthingSystem.TN_S,
        source=PowerSource(
            transformer_family="scb11",
            rated_capacity_kva=1000,
            hv_voltage_kv=10,
            lv_voltage_kv=0.4,
            vector_group="Dyn11",
            uk_percent=6,
            upstream_network_mode=UpstreamNetworkMode.EXPLICIT_IMPEDANCE,
        ),
        load=Load(
            input_basis=InputBasis.ACTIVE_POWER_KW,
            input_value=30,
            phase=Phase.THREE,
            circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
            load_profile=LoadProfile.ORDINARY_EQUIPMENT,
            duty_characteristic=DutyCharacteristic.ORDINARY_CONTINUOUS,
            power_definition=PowerDefinition.CALCULATED,
            power_factor=0.9,
        ),
        nodes=(
            CircuitNode("tx", 0, NodeType.TRANSFORMER_LV, "变压器低压出口", 380),
            CircuitNode("main", 1, NodeType.MAIN_SWITCHBOARD, "低压柜", 380),
            CircuitNode("db", 2, NodeType.DISTRIBUTION_BOARD, "分配电箱", 380),
            CircuitNode("load", 3, NodeType.LOAD_TERMINAL, "用电设备", 380),
        ),
        segments=tuple(
            CircuitSegment(
                segment_id,
                index,
                from_node,
                to_node,
                SegmentType.CABLE,
                Phase.THREE,
                length,
                "tray",
                conductor_family="YJV",
                construction_code="yjv_4c_3ph_n_pe",
            )
            for index, (segment_id, from_node, to_node, length, _) in enumerate(
                segment_specs
            )
        ),
        rule_set_version="validation-0.1.0",
    )
    flows = tuple(
        ResolvedSegmentLoadFlow(
            segment_id,
            current,
            0.85 if segment_id != "final" else 0.9,
            Phase.THREE,
            "validation_fixture",
            (f"FIXTURE:LOAD_FLOW:{segment_id}",),
        )
        for segment_id, _, _, _, current in segment_specs
    )

    def cable_request(segment_id: str, current: float) -> CableSelectionRequest:
        return CableSelectionRequest(
            segment_id=segment_id,
            family="YJV",
            configuration_code="yjv_4c_3ph_n_pe",
            phase=Phase.THREE,
            system_voltage_v=380,
            installation_scenario="tray",
            minimum_required_ampacity_a=current,
            neutral_required=False,
            protective_conductor_mode="included",
            conditions=CableInstallationConditions(
                temperature_c=40,
                tray_type="horizontal_perforated",
                tray_layers=1,
                tray_cables_per_layer=1,
            ),
        )

    cable_requests = tuple(
        cable_request(segment_id, current)
        for segment_id, _, _, _, current in segment_specs
    )
    radial = RadialCircuitCalculationRequest(
        circuit=circuit,
        segment_load_flows=flows,
        cable_requests=cable_requests,
        protection_points=(
            ProtectionPoint(
                "tx", "connection", CircuitApplication.DISTRIBUTION, ("MCCB",), "3P"
            ),
            ProtectionPoint(
                "main", "feeder", CircuitApplication.DISTRIBUTION, ("MCCB",), "3P"
            ),
            ProtectionPoint(
                "db",
                "final",
                CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
                ("MCCB",),
                "3P",
            ),
        ),
        upstream_short_circuit_capacity_mva=100,
        voltage_drop_limit_pct=5,
        voltage_drop_limit_rule_code="ELEC.VDROP.LIMIT",
        maximum_cable_combinations=100,
        maximum_output_combinations=3,
        maximum_candidates_per_cable_segment=3,
    )
    installed_sections = {"connection": 70.0, "feeder": 35.0, "final": 25.0}
    installed_cables = tuple(
        InstalledCable(
            item.segment_id,
            {
                "connection": "图纸电缆 C1：YJV 4×70+PE35",
                "feeder": "图纸电缆 C2：YJV 4×35+PE16",
                "final": "图纸电缆 C3：YJV 4×25+PE16",
            }[item.segment_id],
            installed_sections[item.segment_id],
            item,
        )
        for item in cable_requests
    )
    installed_breakers = (
        InstalledBreaker("tx", "connection", "图纸断路器 QF0 250A", 250, 400, 400, 35),
        InstalledBreaker("main", "feeder", "图纸断路器 QF1 160A", 160, 250, 400, 35),
        InstalledBreaker("db", "final", "图纸断路器 QF2 63A", 63, 100, 400, 25),
    )
    return radial, DrawingCircuitAuditRequest(
        radial_request=radial,
        installed_cables=installed_cables,
        installed_breakers=installed_breakers,
    )
