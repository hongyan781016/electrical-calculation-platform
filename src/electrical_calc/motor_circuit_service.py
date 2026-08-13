"""单台三相直接启动电动机的一段式完整网络编排。"""

from __future__ import annotations

from dataclasses import asdict, replace
from math import sqrt
from typing import Any

from .catalog import (
    DEFAULT_CATALOG,
    lookup_transformer_phase_pe_impedance,
    lookup_transformer_positive_sequence_impedance,
)
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
    calculate_pe_thermal_withstand,
    calculate_phase_conductor_thermal_withstand,
)
from .motor import (
    MotorApproximateStartingInput,
    MotorBreakerRequirementInput,
    MotorCablePreselectionInput,
    MotorKnownBasis,
    MotorLoadInput,
    MotorNetworkInput,
)
from .motor_engine import (
    ENGINE_VERSION,
    calculate_motor_breaker_requirements,
    calculate_motor_cable_preselection,
    calculate_motor_load,
    calculate_motor_starting_approximation,
)
from .motor_control_products import select_motor_control_references
from .motor_electronic_mccb import evaluate_cdm3e_motor_reference
from .motor_product_protection import evaluate_cm3_motor_reference
from .product_protection import (
    select_easypact_ma_motor_reference,
    select_easypact_type1_motor_reference,
    select_tesys_gv2_small_motor_reference,
)


def _source_electrical(
    data: MotorNetworkInput,
) -> tuple[ResolvedSourceElectrical | None, list[str]]:
    warnings: list[str] = []
    if data.transformer_vector_group.lower() != "dyn11":
        warnings.append("当前变压器相—PE阻抗目录只覆盖Dyn11。")
    positive = lookup_transformer_positive_sequence_impedance(
        data.transformer_family,
        data.transformer_capacity_kva,
        data.transformer_uk_percent,
    )
    phase_pe = lookup_transformer_phase_pe_impedance(
        data.transformer_family,
        data.transformer_capacity_kva,
        data.transformer_uk_percent,
    )
    if positive is None:
        warnings.append("变压器系列、容量和uk%没有精确正序R/X表列组合；不插值。")
    if phase_pe is None:
        warnings.append("变压器系列、容量和uk%没有精确相—PE R/X表列组合；不插值。")
    if data.upstream_short_circuit_capacity_mva <= 0:
        warnings.append("上级系统短路容量必须大于0。")
    if warnings:
        return None, warnings
    assert positive is not None and phase_pe is not None

    voltage_v = data.system_voltage_v
    upstream_z = voltage_v**2 / (
        data.upstream_short_circuit_capacity_mva * 1_000_000
    )
    upstream_x = upstream_z / sqrt(1 + 0.1**2)
    upstream_r = 0.1 * upstream_x
    return (
        ResolvedSourceElectrical(
            three_phase_r_ohm=(
                float(positive["positive_sequence_resistance_ohm"])
                + upstream_r
            ),
            three_phase_x_ohm=(
                float(positive["positive_sequence_reactance_ohm"])
                + upstream_x
            ),
            phase_neutral_r_ohm=float(phase_pe["phase_pe_resistance_ohm"]),
            phase_neutral_x_ohm=float(phase_pe["phase_pe_reactance_ohm"]),
            phase_pe_r_ohm=float(phase_pe["phase_pe_resistance_ohm"]),
            phase_pe_x_ohm=float(phase_pe["phase_pe_reactance_ohm"]),
            status="verified",
            source_reference_ids=(
                "ELEC.TRANSFORMER.POSITIVE_SEQUENCE.IMPEDANCE",
                "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE",
                "HANDBOOK:4.6-41+USER:SYSTEM_SHORT_CIRCUIT_CAPACITY",
            ),
        ),
        warnings,
    )


def _circuit(
    motor: MotorLoadInput,
    network: MotorNetworkInput,
    cable: MotorCablePreselectionInput,
) -> CompleteCircuit:
    input_basis = (
        InputBasis.ACTIVE_POWER_KW
        if motor.known_basis == MotorKnownBasis.RATED_OUTPUT_POWER_KW
        else InputBasis.CURRENT_A
    )
    upstream_z = network.system_voltage_v**2 / (
        network.upstream_short_circuit_capacity_mva * 1_000_000
    )
    upstream_x = upstream_z / sqrt(1 + 0.1**2)
    upstream_r = 0.1 * upstream_x
    return CompleteCircuit(
        id="motor-single-circuit",
        code="MOTOR-01",
        name="单台电动机直接启动回路",
        system_voltage_v=network.system_voltage_v,
        line_to_earth_voltage_v=network.line_to_earth_voltage_v,
        frequency_hz=50,
        earthing_system=EarthingSystem.TN_S,
        source=PowerSource(
            transformer_family=network.transformer_family,
            rated_capacity_kva=network.transformer_capacity_kva,
            hv_voltage_kv=network.hv_voltage_kv,
            lv_voltage_kv=0.4,
            vector_group=network.transformer_vector_group,
            uk_percent=network.transformer_uk_percent,
            upstream_network_mode=UpstreamNetworkMode.EXPLICIT_IMPEDANCE,
            upstream_r_ohm=upstream_r,
            upstream_x_ohm=upstream_x,
        ),
        load=Load(
            input_basis=input_basis,
            input_value=motor.known_value,
            phase=Phase.THREE,
            circuit_application=CircuitApplication.MOTOR_FINAL,
            load_profile=LoadProfile.MOTOR,
            duty_characteristic=DutyCharacteristic.HIGH_INRUSH,
            power_definition=(
                PowerDefinition.CALCULATED
                if input_basis == InputBasis.ACTIVE_POWER_KW
                else None
            ),
            power_factor=motor.power_factor,
            efficiency=motor.efficiency,
        ),
        nodes=(
            CircuitNode("tx", 0, NodeType.TRANSFORMER_LV, "变压器低压母线", network.system_voltage_v),
            CircuitNode("motor", 1, NodeType.LOAD_TERMINAL, "电动机端子", network.system_voltage_v),
        ),
        segments=(
            CircuitSegment(
                "motor-line",
                0,
                "tx",
                "motor",
                SegmentType.CABLE if cable.conductor_family == "YJV" else SegmentType.INSULATED_WIRE,
                Phase.THREE,
                cable.length_m,
                cable.installation_scenario,
                conductor_family=cable.conductor_family,
                construction_code=cable.conductor_configuration_code,
            ),
        ),
        rule_set_version="v0.2-motor-candidate",
    )


def _build_primary_motor_scheme(
    candidate: dict[str, Any],
    control_reference: dict[str, Any] | None,
) -> dict[str, Any]:
    """把同一电缆、保护器和接触器收敛为一套主方案。

    “有条件采用”不是正式批准：它表示已知网络条件下的载流量、压降、
    分断能力和故障热稳定已经闭合，但仍有必须在采购/调试前确认的设备
    条件。制造商2类配合和上下级选择性单列为深化项，不混入基础选型。
    """

    cable = candidate["cable"]
    cm3 = candidate.get("cm3_motor_reference")
    contactor = (
        control_reference.get("contactor_candidate")
        if control_reference
        else None
    )
    relay = (
        control_reference.get("recommended_overload_relay")
        if control_reference
        else None
    )
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "status_label": "无法形成主方案",
        "selection_basis": (
            "不按品牌优先级选取；在正泰、德力西、施耐德及其他已核实样本中，"
            "优先采用能同时闭合载流量、压降、分断能力、动作时间和热稳定的证据链"
        ),
        "architecture": "电磁式短路保护断路器＋AC-3接触器＋独立热继电器",
        "cable": cable.get("cable_specification"),
        "breaker": None,
        "contactor": None,
        "overload_device": None,
        "closed_checks": [],
        "purchase_conditions": [],
        "professional_pending": ["电动机端子启动转矩", "上下级选择性或后备保护"],
    }
    if not cm3 or cm3.get("frame_code") is None or not contactor or not relay:
        result["purchase_conditions"].append(
            "尚未形成同一候选下的短路保护器、接触器和热继电器"
        )
        return result

    pickup_label = (
        f"固定{cm3['short_circuit_pickup_nominal_a']:g}A"
        if cm3.get("short_circuit_pickup_rule") == "fixed"
        else f"12In={cm3['short_circuit_pickup_nominal_a']:g}A"
    )
    result["breaker"] = (
        f"{cm3['frame_code']} 电动机用途、电磁式脱扣（方式代码2） / 3P / 400V / "
        f"壳架{cm3['frame_rating_a']:g} A / In {cm3['rated_current_a']:g} A / "
        f"Icu {cm3['icu_ka']:g} kA / Ics {cm3['ics_ka']:g} kA / "
        f"瞬时{pickup_label}（±20%）"
    )
    result["contactor"] = (
        f"{contactor['model']} / AC-3 {contactor['rated_current_a']:g} A / "
        f"{contactor['motor_power_kw']:g} kW"
    )
    result["overload_device"] = (
        f"{relay['frame']} {relay['setting_min_a']:g}～"
        f"{relay['setting_max_a']:g} A，整定至"
        f"{relay['setting_target_a']:.2f} A（按电动机实际铭牌电流复核）"
    )
    checks = {
        "电缆综合复核": candidate.get("cable_decision_status", UNKNOWN),
        "启动电流避开瞬时段": cm3.get(
            "starting_instantaneous_ride_through_status", UNKNOWN
        ),
        "相导体及PE故障切除热稳定": cm3.get(
            "fault_clearing_time_check", UNKNOWN
        ),
        "短路保护器额定电流不小于电动机额定电流": cm3.get(
            "overload_setting_not_below_motor_status", UNKNOWN
        ),
        "热继电器整定范围覆盖电动机额定电流": PASS,
    }
    result["closed_checks"] = [
        label for label, status in checks.items() if status == PASS
    ]
    if FAIL in checks.values():
        result["status"] = FAIL
        result["status_label"] = "不采用"
        result["purchase_conditions"] = [
            label for label, status in checks.items() if status == FAIL
        ]
        return result
    if UNKNOWN in checks.values():
        result["purchase_conditions"].extend(
            label for label, status in checks.items() if status == UNKNOWN
        )

    result["purchase_conditions"].append(
        "热继电器最终整定值按拟购电动机铭牌额定电流设定"
    )
    result["purchase_conditions"].append(
        "接触器线圈电压按实际控制电源选择，不能由380/400V主回路推断"
    )
    result["professional_pending"].extend(
        ["制造商1类/2类配合等级", "产品资料批准状态"]
    )

    if all(status == PASS for status in checks.values()):
        result["status"] = "有条件采用"
        result["status_label"] = "网络安全校核已闭合，器件适用条件待确认"
    return result


def _build_motor_product_scheme_candidates(
    candidate: dict[str, Any],
    control_reference: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """按同一组硬条件比较已有产品路线，并把可闭环路线排在前面。"""

    primary = _build_primary_motor_scheme(candidate, control_reference)
    primary.update(
        {
            "scheme_id": "cm3_magnetic_nxc_nxr",
            "brand_scope": "常熟开关短路保护器＋正泰接触器及热继电器",
            "blocking_reasons": list(primary["purchase_conditions"]),
        }
    )
    schemes: list[dict[str, Any]] = []
    gv2 = candidate.get("tesys_gv2_reference")
    if gv2 and gv2.get("breaker_model"):
        gv2_checks = {
            "电动机额定电流在热整定范围内": gv2.get("motor_current_status", UNKNOWN),
            "启动电流避开磁脱扣": gv2.get("starting_ride_through_status", UNKNOWN),
            "安装点短路电流不超过表列Iq": gv2.get("icu_status", UNKNOWN),
            "末端故障进入保证磁脱扣区": gv2.get("terminal_magnetic_trip_status", UNKNOWN),
            "相导体限流热稳定": gv2.get("phase_thermal_status", UNKNOWN),
            "PE故障切除热稳定": gv2.get("pe_thermal_status", UNKNOWN),
            "电缆综合复核": candidate.get("cable_decision_status", UNKNOWN),
        }
        gv2_status = (
            FAIL if FAIL in gv2_checks.values()
            else "有条件采用" if all(value == PASS for value in gv2_checks.values())
            else UNKNOWN
        )
        schemes.append(
            {
                "scheme_id": "schneider_tesys_gv2_type2_dol",
                "brand_scope": "施耐德TeSys GV2＋TeSys Deca接触器",
                "status": gv2_status,
                "status_label": (
                    "制造商2类配合及网络安全校核已闭合"
                    if gv2_status == "有条件采用"
                    else "存在硬条件不满足" if gv2_status == FAIL
                    else "产品级证据仍有未闭合项"
                ),
                "selection_basis": "制造商IEC 60947-4-1、400/415V直接启动2类配合精确行；380V按同一应用电压档处理",
                "architecture": "热磁式电动机保护断路器＋AC-3接触器（过载保护由GV2承担）",
                "cable": candidate["cable"].get("cable_specification"),
                "breaker": (
                    f"{gv2['breaker_model']} / 3P / 380～415V / "
                    f"热整定{gv2['setting_min_a']:g}～{gv2['setting_max_a']:g}A，"
                    f"整定至{gv2['setting_target_a']:.3f}A / "
                    f"磁脱扣{gv2['magnetic_trip_a']:g}A（上限{gv2['magnetic_trip_upper_tolerance_a']:g}A） / "
                    f"表列Iq {gv2['coordination_iq_ka']:g}kA"
                ),
                "contactor": f"{gv2['contactor_model']} / AC-3",
                "overload_device": "由GV2热脱扣承担，不另配热继电器",
                "closed_checks": [
                    label for label, status in gv2_checks.items() if status == PASS
                ],
                "purchase_conditions": [
                    "保护器最终按拟购电动机铭牌额定电流整定",
                    "接触器线圈电压按实际控制电源选择",
                ],
                "professional_pending": [
                    "电动机端子启动转矩",
                    "上下级选择性或后备保护",
                    "产品资料批准状态",
                ],
                "blocking_reasons": [
                    label for label, status in gv2_checks.items() if status != PASS
                ],
            }
        )
    schneider = candidate.get("schneider_type1_reference")
    if schneider and schneider.get("breaker_model"):
        schneider_checks = {
            "电动机电流在表列范围内": schneider.get("motor_current_status", UNKNOWN),
            "热继电器范围覆盖额定电流": schneider.get("overload_range_status", UNKNOWN),
            "启动电流避开磁脱扣": schneider.get("starting_ride_through_status", UNKNOWN),
            "末端故障进入磁脱扣区": schneider.get("terminal_magnetic_trip_status", UNKNOWN),
            "相导体限流热稳定": schneider.get("phase_thermal_status", UNKNOWN),
            "PE故障切除热稳定": schneider.get("pe_thermal_status", UNKNOWN),
            "电缆综合复核": candidate.get("cable_decision_status", UNKNOWN),
        }
        schneider_status = (
            FAIL if FAIL in schneider_checks.values()
            else "有条件采用" if all(v == PASS for v in schneider_checks.values())
            else UNKNOWN
        )
        schemes.append({
            "scheme_id": "schneider_easypact_type1_dol",
            "brand_scope": "施耐德EasyPact CVS＋EasyPact TVS",
            "status": schneider_status,
            "status_label": (
                "制造商1类配合及网络安全校核已闭合"
                if schneider_status == "有条件采用"
                else "存在硬条件不满足" if schneider_status == FAIL
                else "产品级证据仍有未闭合项"
            ),
            "selection_basis": "制造商IEC/EN 60947-4-1、380～415V直接启动1类配合表精确行",
            "architecture": "MA电磁式短路保护断路器＋AC-3接触器＋独立热继电器",
            "cable": candidate["cable"].get("cable_specification"),
            "breaker": (
                f"{schneider['breaker_model']} / 3P / 380～415V / "
                f"MA {schneider['ma_rating_a']:g}A / Irm {schneider['magnetic_setting_a']:g}A / "
                f"Icu {schneider['icu_ka']:g}kA（{schneider['performance_level']}级）"
            ),
            "contactor": f"{schneider['contactor_model']} / AC-3",
            "overload_device": (
                f"{schneider['overload_relay_model']} "
                f"{schneider['overload_range_a'][0]:g}～{schneider['overload_range_a'][1]:g}A，"
                f"整定至{schneider['overload_setting_target_a']:.2f}A"
            ),
            "closed_checks": [label for label, status in schneider_checks.items() if status == PASS],
            "purchase_conditions": [
                "热继电器最终按拟购电动机铭牌额定电流整定",
                "接触器线圈电压按实际控制电源选择",
            ],
            "professional_pending": ["电动机端子启动转矩", "上下级选择性或后备保护", "产品资料批准状态"],
            "blocking_reasons": [label for label, status in schneider_checks.items() if status != PASS],
        })
    schneider_ma = candidate.get("schneider_ma_reference")
    contactor = (
        control_reference.get("contactor_candidate")
        if control_reference else None
    )
    relay = (
        control_reference.get("recommended_overload_relay")
        if control_reference else None
    )
    if schneider_ma and schneider_ma.get("breaker_model") and contactor and relay:
        ma_checks = {
            "末端故障进入磁脱扣区": schneider_ma.get("terminal_magnetic_trip_status", UNKNOWN),
            "相导体限流热稳定": schneider_ma.get("phase_thermal_status", UNKNOWN),
            "PE故障切除热稳定": schneider_ma.get("pe_thermal_status", UNKNOWN),
            "热继电器范围覆盖额定电流": PASS,
            "电缆综合复核": candidate.get("cable_decision_status", UNKNOWN),
        }
        ma_status = FAIL if FAIL in ma_checks.values() else "有条件采用" if all(v == PASS for v in ma_checks.values()) else UNKNOWN
        schemes.append({
            "scheme_id": "schneider_ma_chint_control",
            "brand_scope": "施耐德短路保护＋正泰控制与过载保护",
            "status": ma_status,
            "status_label": "网络安全校核已闭合，跨品牌成套配合待确认" if ma_status == "有条件采用" else "存在未闭合项",
            "selection_basis": "EasyPact CVS MA磁脱扣、D-11限流I²t及正泰AC-3/热继电器精确可调范围",
            "architecture": "MA电磁式短路保护断路器＋AC-3接触器＋独立热继电器",
            "cable": candidate["cable"].get("cable_specification"),
            "breaker": (
                f"{schneider_ma['breaker_model']} / 3P / 380～415V / MA {schneider_ma['ma_rating_a']:g}A / "
                f"Irm整定{schneider_ma['magnetic_setting_a']:g}A / Icu {schneider_ma['icu_ka']:g}kA（{schneider_ma['performance_level']}级）"
            ),
            "contactor": f"{contactor['model']} / AC-3 {contactor['rated_current_a']:g}A",
            "overload_device": (
                f"{relay['frame']} {relay['setting_min_a']:g}～{relay['setting_max_a']:g}A，"
                f"整定至{relay['setting_target_a']:.3f}A"
            ),
            "closed_checks": [label for label, status in ma_checks.items() if status == PASS],
            "purchase_conditions": [
                "跨品牌组合没有制造商1类/2类配合声明，采购前由成套方确认",
                "热继电器最终按拟购电动机铭牌额定电流整定",
                "接触器线圈电压按实际控制电源选择",
            ],
            "professional_pending": ["电动机端子启动转矩", "制造商成套配合等级", "上下级选择性或后备保护", "产品资料批准状态"],
            "blocking_reasons": [label for label, status in ma_checks.items() if status != PASS],
        })
    schemes.append(primary)
    contactor = (
        control_reference.get("contactor_candidate")
        if control_reference
        else None
    )

    ns2 = candidate.get("ns2_motor_reference")
    if ns2 and ns2.get("model"):
        ns2_blockers: list[str] = []
        ns2_checks = {
            "过载整定范围覆盖电动机额定电流": ns2.get(
                "overload_setting_range_status", UNKNOWN
            ),
            "启动电流避开瞬时脱扣": ns2.get(
                "starting_instantaneous_status", UNKNOWN
            ),
            "安装点分断能力": ns2.get("standalone_icu_status", UNKNOWN),
            "末端故障进入保证动作区": ns2.get(
                "terminal_guaranteed_instantaneous_status", UNKNOWN
            ),
            "故障总切除时间满足电缆热稳定": ns2.get(
                "total_clearing_time_status", UNKNOWN
            ),
        }
        ns2_blockers.extend(
            label for label, status in ns2_checks.items() if status != PASS
        )
        ns2_status = (
            FAIL
            if FAIL in ns2_checks.values()
            else UNKNOWN
            if UNKNOWN in ns2_checks.values()
            else "有条件采用"
        )
        schemes.append(
            {
                "scheme_id": "chint_ns2_adjustable_mpcb",
                "brand_scope": "正泰",
                "status": ns2_status,
                "status_label": (
                    "产品级故障切除证据不足"
                    if ns2_status == UNKNOWN
                    else "存在硬条件不满足"
                    if ns2_status == FAIL
                    else "网络安全校核已闭合，器件适用条件待确认"
                ),
                "architecture": "可调整定MPCB＋AC-3接触器",
                "cable": candidate["cable"].get("cable_specification"),
                "breaker": (
                    f"{ns2['model']} / 整定{ns2['setting_min_a']:g}～"
                    f"{ns2['setting_max_a']:g} A / Icu "
                    f"{ns2['icu_ka_at_400_415v']:g} kA"
                ),
                "contactor": (
                    f"{contactor['model']} / AC-3 {contactor['rated_current_a']:g} A"
                    if contactor
                    else None
                ),
                "overload_device": "由可调整定MPCB承担，不另配热继电器",
                "closed_checks": [
                    label for label, status in ns2_checks.items() if status == PASS
                ],
                "purchase_conditions": [],
                "professional_pending": [
                    "制造商1类/2类配合等级",
                    "上下级选择性或后备保护",
                ],
                "blocking_reasons": ns2_blockers,
            }
        )

    cdm3e = candidate.get("cdm3e_motor_reference")
    relay = (
        control_reference.get("recommended_overload_relay")
        if control_reference
        else None
    )
    if cdm3e and cdm3e.get("model"):
        cdm3e_checks = {
            "电缆载流量": cdm3e.get("conductor_ampacity_status", UNKNOWN),
            "启动电流避开短延时标称拾取": cdm3e.get(
                "starting_nominal_ride_through_status", UNKNOWN
            ),
            "安装点分断能力": cdm3e.get("icu_status", UNKNOWN),
            "短延时拾取保证": cdm3e.get(
                "short_delay_pickup_guarantee_status", UNKNOWN
            ),
            "故障总切除时间满足电缆热稳定": cdm3e.get(
                "nominal_thermal_time_status", UNKNOWN
            ),
        }
        cdm3e_status = (
            FAIL
            if FAIL in cdm3e_checks.values()
            else UNKNOWN
            if UNKNOWN in cdm3e_checks.values()
            else "有条件采用"
        )
        schemes.append(
            {
                "scheme_id": "delixi_cdm3e_independent_overload",
                "brand_scope": "德力西保护器＋正泰控制器",
                "status": cdm3e_status,
                "status_label": (
                    "存在硬条件不满足"
                    if cdm3e_status == FAIL
                    else "产品级拾取或分断证据不足"
                    if cdm3e_status == UNKNOWN
                    else "网络安全校核已闭合，器件适用条件待确认"
                ),
                "architecture": "电子式MCCB短路保护＋AC-3接触器＋独立热继电器",
                "cable": candidate["cable"].get("cable_specification"),
                "breaker": (
                    f"{cdm3e['model']} / 长延时OFF / 短延时"
                    f"{cdm3e['short_delay_multiplier']:g}×"
                    f"{cdm3e['controller_rated_current_a']:g} A"
                ),
                "contactor": (
                    f"{contactor['model']} / AC-3 {contactor['rated_current_a']:g} A"
                    if contactor
                    else None
                ),
                "overload_device": (
                    f"{relay['frame']} {relay['setting_min_a']:g}～"
                    f"{relay['setting_max_a']:g} A，整定至"
                    f"{relay['setting_target_a']:.2f} A"
                    if relay
                    else "尚未形成"
                ),
                "closed_checks": [
                    label for label, status in cdm3e_checks.items() if status == PASS
                ],
                "purchase_conditions": [],
                "professional_pending": [
                    "制造商1类/2类配合等级",
                    "上下级选择性或后备保护",
                ],
                "blocking_reasons": [
                    label for label, status in cdm3e_checks.items() if status != PASS
                ],
            }
        )

    cable = candidate["cable"]
    route = candidate.get("route", {})
    basis = candidate.get("design_basis", {})
    phase_section = float(cable["phase_section_mm2"])
    pe_section = cable.get("protective_section_mm2")
    if cable.get("configuration_code") == "yjv_4c_3ph_n_pe" and pe_section:
        cable_purchase_spec = (
            f"YJV-{cable['voltage_designation']} 3×{phase_section:g}＋"
            f"1×{float(pe_section):g} mm² 铜芯电缆"
        )
    elif cable.get("configuration_code") == "yjv_3c_3ph_pe" and pe_section:
        cable_purchase_spec = (
            f"YJV-{cable['voltage_designation']} 3×{phase_section:g} mm² 铜芯电缆"
            f"＋独立PE {float(pe_section):g} mm²"
        )
    else:
        cable_purchase_spec = cable.get("cable_specification")

    for scheme in schemes:
        scheme["purchase_summary"] = {
            "decision": scheme["status"],
            "design_basis": (
                f"三相{basis.get('system_voltage_v', 0):g}V，"
                f"电动机额定电流{basis.get('rated_current_a', 0):.2f}A，"
                f"直接启动电流{basis.get('starting_current_a', 0):.1f}A"
            ),
            "items": [
                {
                    "category": "电缆",
                    "specification": cable_purchase_spec,
                    "quantity": f"{route.get('length_m', 0):g} m",
                    "note": (
                        f"{route.get('installation_scenario_label', '敷设方式待确认')}；"
                        f"修正后载流量{float(cable['corrected_ampacity_a']):g}A"
                    ),
                },
                {
                    "category": "短路及过载保护",
                    "specification": scheme.get("breaker") or "尚未形成",
                    "quantity": "1 台",
                    "note": scheme["architecture"],
                },
                {
                    "category": "接触器",
                    "specification": scheme.get("contactor") or "尚未形成",
                    "quantity": "1 台",
                    "note": "AC-3使用类别；线圈电压按控制回路确定",
                },
                {
                    "category": "独立热继电器",
                    "specification": scheme.get("overload_device") or "尚未形成",
                    "quantity": (
                        "不另配"
                        if "不另配" in (scheme.get("overload_device") or "")
                        else "1 台"
                    ),
                    "note": "不得与保护器的同一过载保护功能重复配置",
                },
            ],
            "must_confirm": list(scheme.get("purchase_conditions", [])),
        }

    rank = {"有条件采用": 0, UNKNOWN: 1, FAIL: 2}
    return sorted(schemes, key=lambda item: rank.get(item["status"], 3))


def evaluate_motor_cable_candidates_in_network(
    motor: MotorLoadInput,
    cable: MotorCablePreselectionInput,
    network: MotorNetworkInput,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """对每档电缆重新计算运行压降、故障电流、启动电压和热约束。"""

    catalog = catalog or DEFAULT_CATALOG
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {"candidates": []}
    motor_result = calculate_motor_load(motor, rules)
    rated_current = motor_result.outputs.get("rated_current_a")
    starting_current = motor_result.outputs.get("starting_current_a")
    if rated_current is None or starting_current is None:
        return Outcome(
            "电动机完整网络候选",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            motor_result.warnings,
            motor_result.rule_codes,
        )
    source, source_warnings = _source_electrical(network)
    if source is None:
        return Outcome(
            "电动机完整网络候选",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            source_warnings,
            [
                "ELEC.TRANSFORMER.POSITIVE_SEQUENCE.IMPEDANCE",
                "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE",
            ],
        )

    cable_input = replace(
        cable,
        rated_current_a=float(rated_current),
        running_power_factor=motor.power_factor,
        rated_voltage_v=network.system_voltage_v,
    )
    cable_result = calculate_motor_cable_preselection(
        cable_input, rules, catalog
    )
    circuit = _circuit(motor, network, cable_input)
    for cable_candidate in cable_result.outputs.get("candidates", []):
        resolved_data = cable_candidate.get("resolved_electrical")
        if not resolved_data:
            continue
        resolved = ResolvedSegmentElectrical(
            **{**resolved_data, "segment_id": "motor-line"}
        )
        load_flow = ResolvedSegmentLoadFlow(
            segment_id="motor-line",
            design_current_a=float(rated_current),
            power_factor=float(motor.power_factor or 0),
            phase=Phase.THREE,
            status="verified",
            source_reference_ids=("MOTOR.CURRENT.RATED",),
        )
        if load_flow.power_factor <= 0:
            continue
        chain = calculate_complete_circuit_chain(
            CompleteCircuitCalculationInput(
                circuit=circuit,
                source_electrical=source,
                segment_electrical=(resolved,),
                segment_load_flows=(load_flow,),
                maximum_short_circuit_voltage_factor=network.maximum_short_circuit_voltage_factor,
                minimum_fault_voltage_factor=network.minimum_fault_voltage_factor,
            ),
            rules,
        )
        chain_outputs = chain.outputs
        drop_pct = chain_outputs.get("terminal_voltage_drop_percent")
        voltage_status = (
            PASS
            if drop_pct is not None
            and float(drop_pct) <= network.running_voltage_drop_limit_percent
            else ("不通过" if drop_pct is not None else UNKNOWN)
        )
        nodes = chain_outputs.get("node_results", [])
        source_ik_ka = nodes[0].get("three_phase_short_circuit_ka") if nodes else None
        terminal_if_a = nodes[-1].get("earth_fault_current_a") if nodes else None
        phase_thermal = calculate_phase_conductor_thermal_withstand(
            {
                "phase_conductor_section_mm2": cable_candidate["phase_section_mm2"],
                "phase_conductor_material": "copper",
                "phase_conductor_insulation": "xlpe" if cable_input.conductor_family == "YJV" else "pvc",
                "prospective_fault_current_a": (
                    float(source_ik_ka) * 1000 if source_ik_ka is not None else None
                ),
            },
            rules,
        )
        pe_thermal = None
        pe_section = cable_candidate.get("protective_section_mm2")
        if pe_section is not None:
            pe_thermal = calculate_pe_thermal_withstand(
                {
                    "protective_conductor_section_mm2": pe_section,
                    "protective_conductor_material": "copper",
                    "protective_conductor_insulation": "xlpe" if cable_input.conductor_family == "YJV" else "pvc",
                    "protective_conductor_arrangement": "multicore_cable" if cable_input.conductor_family == "YJV" else "single_or_bare",
                    "prospective_fault_current_a": terminal_if_a,
                },
                rules,
            )

        breaker_requirements = None
        cm3_motor_reference = None
        ns2_motor_reference = None
        control_product_reference = None
        cdm3e_motor_reference = None
        schneider_type1_reference = None
        schneider_ma_reference = None
        tesys_gv2_reference = None
        phase_max_time = phase_thermal.outputs.get(
            "maximum_permitted_clearing_time_s"
        )
        pe_max_time = (
            pe_thermal.outputs.get("maximum_permitted_clearing_time_s")
            if pe_thermal
            else None
        )
        corrected_ampacity = cable_candidate.get("corrected_ampacity_a")
        if all(
            value is not None
            for value in (
                source_ik_ka,
                terminal_if_a,
                phase_max_time,
                pe_max_time,
                corrected_ampacity,
            )
        ):
            breaker_requirements = calculate_motor_breaker_requirements(
                MotorBreakerRequirementInput(
                    motor_rated_current_a=float(rated_current),
                    motor_starting_current_a=float(starting_current),
                    system_voltage_v=network.system_voltage_v,
                    conductor_corrected_ampacity_a=float(corrected_ampacity),
                    installation_point_max_short_circuit_ka=float(source_ik_ka),
                    terminal_minimum_fault_current_a=float(terminal_if_a),
                    phase_maximum_clearing_time_s=float(phase_max_time),
                    pe_maximum_clearing_time_s=float(pe_max_time),
                ),
                rules,
                catalog,
            )
            required = breaker_requirements.outputs.get("required_parameters", {})
            if required:
                cm3_motor_reference = evaluate_cm3_motor_reference(
                    motor_rated_current_a=float(rated_current),
                    motor_starting_current_a=float(starting_current),
                    conductor_corrected_ampacity_a=float(corrected_ampacity),
                    required_icu_ka=float(source_ik_ka),
                    terminal_minimum_fault_current_a=float(terminal_if_a),
                    phase_maximum_clearing_time_s=float(phase_max_time),
                    pe_maximum_clearing_time_s=float(pe_max_time),
                    motor_starting_time_s=network.motor_starting_time_s,
                    system_voltage_v=network.system_voltage_v,
                )
                ns2_result = select_motor_control_references(
                    motor_rated_current_a=float(rated_current),
                    motor_starting_current_a=float(starting_current),
                    motor_rated_output_power_kw=(
                        motor.known_value
                        if motor.known_basis
                        == MotorKnownBasis.RATED_OUTPUT_POWER_KW
                        else None
                    ),
                    system_voltage_v=network.system_voltage_v,
                    motor_starting_time_s=network.motor_starting_time_s,
                    installation_point_max_short_circuit_ka=float(source_ik_ka),
                    terminal_minimum_fault_current_a=float(terminal_if_a),
                    phase_maximum_clearing_time_s=float(phase_max_time),
                    pe_maximum_clearing_time_s=float(pe_max_time),
                )
                control_product_reference = ns2_result
                ns2_candidate = ns2_result.get("ns2_standalone_candidate")
                if ns2_candidate:
                    ns2_motor_reference = {
                        **ns2_candidate,
                        "formal_status": ns2_result[
                            "ns2_standalone_formal_status"
                        ],
                        "provisional_status": ns2_result[
                            "ns2_standalone_provisional_status"
                        ],
                        "applicability": ns2_result[
                            "ns2_standalone_applicability"
                        ],
                    }
                cdm3e_motor_reference = evaluate_cdm3e_motor_reference(
                    motor_rated_current_a=float(rated_current),
                    motor_starting_current_a=float(starting_current),
                    conductor_corrected_ampacity_a=float(corrected_ampacity),
                    system_voltage_v=network.system_voltage_v,
                    required_icu_ka=float(source_ik_ka),
                    terminal_minimum_fault_current_a=float(terminal_if_a),
                    phase_maximum_clearing_time_s=float(phase_max_time),
                    pe_maximum_clearing_time_s=float(pe_max_time),
                )
                if (
                    motor.known_basis == MotorKnownBasis.RATED_OUTPUT_POWER_KW
                    and float(motor.known_value) >= 0.37
                ):
                    schneider_type1_reference = select_easypact_type1_motor_reference(
                        motor_power_kw=float(motor.known_value),
                        motor_rated_current_a=float(rated_current),
                        motor_starting_current_a=float(starting_current),
                        system_voltage_v=network.system_voltage_v,
                        required_icu_ka=float(source_ik_ka),
                        terminal_fault_current_a=float(terminal_if_a),
                        phase_permitted_i2t_a2s=float(
                            phase_thermal.outputs["permitted_thermal_stress_a2s"]
                        ),
                        pe_permitted_i2t_a2s=float(
                            pe_thermal.outputs["permitted_thermal_stress_a2s"]
                        ),
                    )
                elif motor.known_basis == MotorKnownBasis.RATED_OUTPUT_POWER_KW:
                    tesys_gv2_reference = select_tesys_gv2_small_motor_reference(
                        motor_power_kw=float(motor.known_value),
                        motor_rated_current_a=float(rated_current),
                        motor_starting_current_a=float(starting_current),
                        system_voltage_v=network.system_voltage_v,
                        required_icu_ka=float(source_ik_ka),
                        terminal_fault_current_a=float(terminal_if_a),
                        phase_permitted_i2t_a2s=float(
                            phase_thermal.outputs["permitted_thermal_stress_a2s"]
                        ),
                        pe_permitted_i2t_a2s=float(
                            pe_thermal.outputs["permitted_thermal_stress_a2s"]
                        ),
                    )
                    schneider_ma_reference = select_easypact_ma_motor_reference(
                        motor_rated_current_a=float(rated_current),
                        motor_starting_current_a=float(starting_current),
                        system_voltage_v=network.system_voltage_v,
                        required_icu_ka=float(source_ik_ka),
                        terminal_fault_current_a=float(terminal_if_a),
                        phase_permitted_i2t_a2s=float(
                            phase_thermal.outputs["permitted_thermal_stress_a2s"]
                        ),
                        pe_permitted_i2t_a2s=float(
                            pe_thermal.outputs["permitted_thermal_stress_a2s"]
                        ),
                    )

        start_result = None
        if network.preconnected_reactive_load_mvar is not None:
            source_z = sqrt(source.three_phase_r_ohm**2 + source.three_phase_x_ohm**2)
            bus_scc_mva = (network.system_voltage_v / 1000) ** 2 / source_z
            # 表6.5-4的Xl不是直接照搬电缆表纯电抗。低压铜芯线路
            # 较长时须计入电阻因素，按原表符号说明给出的等效式形成。
            section_mm2 = float(cable_candidate["phase_section_mm2"])
            length_km = cable_input.length_m / 1000
            line_x_ohm = (
                (0.08 + 6.1 / section_mm2) * length_km
                if section_mm2 > 150
                else (18.3 / section_mm2) * length_km
            )
            if line_x_ohm is not None and motor.locked_rotor_current_ratio is not None:
                start_result = calculate_motor_starting_approximation(
                    MotorApproximateStartingInput(
                        nominal_network_voltage_kv=network.system_voltage_v / 1000,
                        system_average_voltage_kv=0.4,
                        motor_rated_voltage_kv=motor.rated_voltage_v / 1000,
                        motor_rated_current_ka=float(rated_current) / 1000,
                        locked_rotor_current_ratio=motor.locked_rotor_current_ratio,
                        bus_short_circuit_capacity_mva=bus_scc_mva,
                        preconnected_reactive_load_mvar=network.preconnected_reactive_load_mvar,
                        motor_feeder_reactance_ohm=line_x_ohm,
                        minimum_bus_voltage_percent=network.minimum_starting_bus_voltage_percent,
                    ),
                    rules,
                )

        selected_phase_status = (
            tesys_gv2_reference.get("phase_thermal_status", UNKNOWN)
            if tesys_gv2_reference and tesys_gv2_reference.get("breaker_model")
            else
            schneider_type1_reference.get("phase_thermal_status", UNKNOWN)
            if schneider_type1_reference and schneider_type1_reference.get("breaker_model")
            else schneider_ma_reference.get("phase_thermal_status", UNKNOWN)
            if schneider_ma_reference and schneider_ma_reference.get("breaker_model")
            else cm3_motor_reference.get("phase_fault_clearing_time_check", UNKNOWN)
            if cm3_motor_reference
            else UNKNOWN
        )
        selected_pe_status = (
            tesys_gv2_reference.get("pe_thermal_status", UNKNOWN)
            if tesys_gv2_reference and tesys_gv2_reference.get("breaker_model")
            else
            schneider_type1_reference.get("pe_thermal_status", UNKNOWN)
            if schneider_type1_reference and schneider_type1_reference.get("breaker_model")
            else schneider_ma_reference.get("pe_thermal_status", UNKNOWN)
            if schneider_ma_reference and schneider_ma_reference.get("breaker_model")
            else cm3_motor_reference.get("pe_fault_clearing_time_check", UNKNOWN)
            if cm3_motor_reference
            else UNKNOWN
        )
        cable_decision_checks = {
            "running_voltage_drop": voltage_status,
            "terminal_three_phase_short_circuit": (
                PASS
                if chain_outputs.get("terminal_three_phase_short_circuit_ka")
                is not None
                else UNKNOWN
            ),
            "terminal_earth_fault": (
                PASS if terminal_if_a is not None else UNKNOWN
            ),
            "phase_thermal_with_selected_reference": (
                selected_phase_status
            ),
            "pe_thermal_with_selected_reference": (
                selected_pe_status
            ),
            "starting_voltage": (
                start_result.outputs.get("bus_voltage_check", UNKNOWN)
                if start_result
                else UNKNOWN
            ),
            "motor_terminal_starting_torque": (
                start_result.outputs.get("motor_terminal_torque_check", UNKNOWN)
                if start_result
                else UNKNOWN
            ),
        }
        governing_checks = [
            cable_decision_checks["running_voltage_drop"],
            cable_decision_checks["terminal_three_phase_short_circuit"],
            cable_decision_checks["terminal_earth_fault"],
            cable_decision_checks["phase_thermal_with_selected_reference"],
            cable_decision_checks["pe_thermal_with_selected_reference"],
        ]
        if start_result is not None:
            governing_checks.append(cable_decision_checks["starting_voltage"])
        if FAIL in governing_checks:
            cable_decision_status = FAIL
        elif all(status == PASS for status in governing_checks):
            cable_decision_status = PASS
        else:
            cable_decision_status = UNKNOWN
        cable_pending_checks = [
            label
            for code, label in (
                ("terminal_three_phase_short_circuit", "末端三相短路电流"),
                ("terminal_earth_fault", "末端相—PE故障电流"),
                ("phase_thermal_with_selected_reference", "相导体热稳定"),
                ("pe_thermal_with_selected_reference", "PE热稳定"),
                ("starting_voltage", "启动电压"),
            )
            if cable_decision_checks[code] == UNKNOWN
            and (code != "starting_voltage" or start_result is not None)
        ]

        cable_controlling_checks = [
            label
            for code, label in (
                ("running_voltage_drop", "运行电压降"),
                ("terminal_three_phase_short_circuit", "末端三相短路电流"),
                ("terminal_earth_fault", "末端相—PE故障电流"),
                ("phase_thermal_with_selected_reference", "相导体短路热稳定"),
                ("pe_thermal_with_selected_reference", "PE导体热稳定"),
                ("starting_voltage", "启动时母线电压"),
            )
            if cable_decision_checks[code] != PASS
            and (code != "starting_voltage" or start_result is not None)
        ]

        candidate_result = {
                "cable": cable_candidate,
                "design_basis": {
                    "system_voltage_v": network.system_voltage_v,
                    "rated_current_a": float(rated_current),
                    "starting_current_a": float(starting_current),
                },
                "route": {
                    "length_m": cable_input.length_m,
                    "installation_scenario": cable_input.installation_scenario,
                    "installation_scenario_label": {
                        "tray": "槽盒敷设",
                        "conduit": "穿管敷设",
                        "direct_buried": "直埋敷设",
                    }.get(cable_input.installation_scenario, "敷设方式待确认"),
                },
                "chain": chain.to_dict(),
                "running_voltage_drop_check": {
                    "actual_percent": drop_pct,
                    "limit_percent": network.running_voltage_drop_limit_percent,
                    "status": voltage_status,
                },
                "phase_thermal_constraint": phase_thermal.to_dict(),
                "pe_thermal_constraint": pe_thermal.to_dict() if pe_thermal else None,
                "breaker_requirements": (
                    breaker_requirements.to_dict()
                    if breaker_requirements
                    else None
                ),
                "cm3_motor_reference": cm3_motor_reference,
                "ns2_motor_reference": ns2_motor_reference,
                "cdm3e_motor_reference": cdm3e_motor_reference,
                "schneider_type1_reference": schneider_type1_reference,
                "schneider_ma_reference": schneider_ma_reference,
                "tesys_gv2_reference": tesys_gv2_reference,
                "control_product_reference": control_product_reference,
                "starting_voltage": start_result.to_dict() if start_result else None,
                "cable_decision_status": cable_decision_status,
                "cable_decision_checks": cable_decision_checks,
                "cable_pending_checks": cable_pending_checks,
                "cable_controlling_checks": cable_controlling_checks,
            }
        product_schemes = _build_motor_product_scheme_candidates(
            candidate_result, control_product_reference
        )
        candidate_result["product_scheme_candidates"] = product_schemes
        candidate_result["primary_scheme"] = product_schemes[0]
        outputs["candidates"].append(candidate_result)

    warnings.extend(cable_result.warnings)
    if network.preconnected_reactive_load_mvar is None:
        warnings.append("同母线预接负荷无功尚未形成，启动电压近似法未执行。")
    warnings.append("尚未选择实际保护器件；当前已形成每档电缆对应的In、Icu、瞬时整定和最长切除时间要求。")
    recommended_position = next(
        (
            index
            for index, item in enumerate(outputs["candidates"])
            if item["cable_decision_status"] == PASS
        ),
        None,
    )
    outputs["recommended_candidate_position"] = recommended_position
    if recommended_position is None and outputs["candidates"]:
        warnings.append(
            "没有任何电缆候选完成全部关键网络校核；只能保留最小基础候选，不能作为最终规格。"
        )
    elif recommended_position is not None:
        recommended = outputs["candidates"][recommended_position]
        outputs["recommended_cable_specification"] = recommended["cable"][
            "cable_specification"
        ]
        outputs["primary_scheme"] = recommended["primary_scheme"]
    provisional = PASS if outputs["candidates"] else UNKNOWN
    steps.append(Step("逐候选完整网络复算", "每档电缆独立重算", len(outputs["candidates"]), "个"))
    return Outcome(
        "电动机完整网络候选",
        ENGINE_VERSION,
        UNKNOWN,
        provisional,
        outputs,
        steps,
        warnings,
        list(dict.fromkeys(motor_result.rule_codes + cable_result.rule_codes + ["MOTOR.SCPD.BREAKER", "ELEC.BREAKING.CAPACITY", "ELEC.SHORT_CIRCUIT", "ELEC.EARTH_FAULT.TN.IMPEDANCE", "ELEC.PHASE.THERMAL.WITHSTAND", "ELEC.PE.THERMAL.WITHSTAND"])),
    )
