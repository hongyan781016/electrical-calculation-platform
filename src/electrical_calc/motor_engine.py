"""电动机回路的纯计算与通用设计约束。"""

from __future__ import annotations

from math import isfinite, sqrt
from typing import Any, Iterable

from .cable_selector import (
    CableInstallationConditions,
    CableSelectionRequest,
    generate_cable_candidates,
)
from .catalog import DEFAULT_CATALOG
from .complete_circuit import Phase
from .engine import FAIL, Outcome, PASS, Step, UNKNOWN, calculate_voltage_drop
from .motor import (
    MotorApproximateStartingInput,
    MotorBreakerRequirementInput,
    MotorBusLoadCondition,
    MotorKnownBasis,
    MotorCablePreselectionInput,
    MotorLoadInput,
    MotorStartingNetworkInput,
    MotorStartingFrequency,
    MotorStartingVoltageScenario,
)


ENGINE_VERSION = "0.2.0"


def _approved(codes: Iterable[str], rules: dict[str, dict[str, Any]]) -> bool:
    selected = tuple(codes)
    return bool(selected) and all(
        rules.get(code, {}).get("status") == "approved" for code in selected
    )


def _status(
    provisional_status: str,
    rule_codes: list[str],
    rules: dict[str, dict[str, Any]],
    warnings: list[str],
) -> str:
    if provisional_status == UNKNOWN:
        return UNKNOWN
    if not _approved(rule_codes, rules):
        warnings.append("电动机计算依据尚未全部批准；当前结果仅用于设计参数初选。")
        return UNKNOWN
    return provisional_status


def _positive(value: float | None) -> bool:
    return value is not None and isfinite(value) and value > 0


def calculate_motor_load(
    data: MotorLoadInput,
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """计算三相电动机额定电流和直接启动电流。

    铭牌电流优先直接采用。仅当输入为轴上额定输出功率时才使用效率和
    功率因数反算；启动电流倍数必须先由铭牌、厂家样本或已核实目录解析。
    """

    rule_codes: list[str] = []
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "phase": "3",
        "starting_method": "direct_on_line",
        "rated_current_a": None,
        "starting_current_a": None,
    }

    if not _positive(data.known_value):
        warnings.append("铭牌额定功率或铭牌额定电流必须大于0。")
    if not _positive(data.rated_voltage_v):
        warnings.append("电动机额定电压必须大于0。")
    if warnings:
        return Outcome(
            "电动机负荷",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    if data.known_basis == MotorKnownBasis.NAMEPLATE_CURRENT_A:
        rated_current_a = data.known_value
        rated_source = "nameplate_current"
        steps.append(Step("电动机额定电流", "IrM＝铭牌额定电流", rated_current_a, "A"))
    elif data.known_basis == MotorKnownBasis.RATED_OUTPUT_POWER_KW:
        rule_codes.append("MOTOR.CURRENT.RATED")
        if not _positive(data.power_factor) or not _positive(data.efficiency):
            warnings.append("按额定输出功率计算电流时，必须取得功率因数和效率。")
            return Outcome(
                "电动机负荷",
                ENGINE_VERSION,
                UNKNOWN,
                UNKNOWN,
                outputs,
                steps,
                warnings,
                rule_codes,
            )
        if data.power_factor > 1 or data.efficiency > 1:
            warnings.append("功率因数和效率必须大于0且不大于1。")
            return Outcome(
                "电动机负荷",
                ENGINE_VERSION,
                UNKNOWN,
                UNKNOWN,
                outputs,
                steps,
                warnings,
                rule_codes,
            )
        rated_current_a = (
            data.known_value
            * 1000
            / (sqrt(3) * data.rated_voltage_v * data.efficiency * data.power_factor)
        )
        rated_source = "calculated_from_rated_output_power"
        steps.append(
            Step(
                "电动机额定电流",
                "IrM＝PrM/(√3UrMηrcosφr)",
                rated_current_a,
                "A",
            )
        )
    else:
        warnings.append("不支持的电动机已知量类型。")
        return Outcome(
            "电动机负荷",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    outputs["rated_current_a"] = round(rated_current_a, 6)
    outputs["rated_current_source"] = rated_source

    provisional_status = PASS
    ratio = data.locked_rotor_current_ratio
    if not _positive(ratio):
        warnings.append("缺少堵转电流/额定电流比，启动电流无法判断。")
        provisional_status = UNKNOWN
    else:
        rule_codes.append("MOTOR.START.CURRENT")
        starting_current_a = rated_current_a * ratio
        outputs["locked_rotor_current_ratio"] = ratio
        outputs["starting_current_a"] = round(starting_current_a, 6)
        steps.append(
            Step(
                "电动机直接启动电流",
                "IstM＝(堵转电流/额定电流比)×IrM",
                starting_current_a,
                "A",
            )
        )

    return Outcome(
        "电动机负荷",
        ENGINE_VERSION,
        _status(provisional_status, rule_codes, rules, warnings),
        provisional_status,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def resolve_motor_starting_voltage_requirement(
    scenario: MotorStartingVoltageScenario,
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """按启动频繁性和同母线负荷自动取得母线电压限值。"""

    rule_codes = ["MOTOR.START.VOLTAGE"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "minimum_bus_voltage_percent": None,
        "requires_motor_terminal_torque_check": False,
    }

    if scenario.bus_load_condition == MotorBusLoadCondition.NO_OTHER_LOADS:
        outputs["requires_motor_terminal_torque_check"] = True
        warnings.append("母线上无其他用电设备，应按启动转矩确定，不直接采用固定百分比。")
        provisional_status = UNKNOWN
    elif (
        scenario.starting_frequency == MotorStartingFrequency.FREQUENT
        and scenario.bus_load_condition != MotorBusLoadCondition.UNKNOWN
    ):
        outputs["minimum_bus_voltage_percent"] = 90.0
        steps.append(Step("启动时母线最低电压", "频繁启动：Ubus≥90%Un", 90.0, "%"))
        provisional_status = PASS
    elif (
        scenario.starting_frequency == MotorStartingFrequency.INFREQUENT
        and scenario.bus_load_condition
        == MotorBusLoadCondition.NO_LIGHTING_OR_SENSITIVE_LOADS
    ):
        outputs["minimum_bus_voltage_percent"] = 80.0
        steps.append(
            Step(
                "启动时母线最低电压",
                "不频繁启动且母线无照明或敏感负荷：Ubus≥80%Un",
                80.0,
                "%",
            )
        )
        provisional_status = PASS
    elif (
        scenario.starting_frequency == MotorStartingFrequency.INFREQUENT
        and scenario.bus_load_condition
        == MotorBusLoadCondition.LIGHTING_OR_SENSITIVE_LOADS
    ):
        outputs["minimum_bus_voltage_percent"] = 85.0
        steps.append(
            Step(
                "启动时母线最低电压",
                "一般不频繁启动：Ubus≥85%Un",
                85.0,
                "%",
            )
        )
        provisional_status = PASS
    else:
        outputs["conservative_preselection_percent"] = 90.0
        warnings.append("启动频繁性或同母线负荷情况不清楚；90%仅作保守初选，不能形成正式结论。")
        provisional_status = UNKNOWN

    return Outcome(
        "电动机启动电压要求",
        ENGINE_VERSION,
        _status(provisional_status, rule_codes, rules, warnings),
        provisional_status,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def calculate_motor_selection_constraints(
    rated_current_a: float,
    starting_current_a: float,
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """生成断路器、AC-3接触器和热继电器的通用参数约束。"""

    rule_codes = [
        "MOTOR.SCPD.BREAKER",
        "MOTOR.CONTACTOR.AC3",
        "MOTOR.OVERLOAD.RELAY",
    ]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {}
    if not _positive(rated_current_a) or not _positive(starting_current_a):
        warnings.append("额定电流和启动电流必须大于0。")
        return Outcome(
            "电动机通用选型约束",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    instant_min_a = 2.0 * starting_current_a
    instant_max_a = 2.5 * starting_current_a
    outputs.update(
        {
            "breaker_long_delay_min_a": round(rated_current_a, 6),
            "breaker_long_delay_relation": "应接近且不小于电动机额定电流",
            "breaker_instantaneous_setting_min_a": round(instant_min_a, 6),
            "breaker_instantaneous_setting_max_a": round(instant_max_a, 6),
            "contactor_ac3_working_current_min_a": round(rated_current_a, 6),
            "overload_setting_target_a": round(rated_current_a, 6),
            "overload_setting_relation": "应接近且不小于电动机额定电流",
            "overload_adjustment_range_min_percent": 20.0,
        }
    )
    steps.extend(
        (
            Step("断路器瞬时整定下限", "Ii,min＝2IstM", instant_min_a, "A"),
            Step("断路器瞬时整定上限", "Ii,max＝2.5IstM", instant_max_a, "A"),
            Step("AC-3接触器工作电流下限", "Ie,AC-3≥IrM", rated_current_a, "A"),
            Step("热继电器本回路计算整定值", "Ir,OL＝IrM", rated_current_a, "A"),
        )
    )
    warnings.append("具体产品仍须核对调节范围、启动时间、分断能力及制造商1类/2类配合表。")
    provisional_status = PASS
    return Outcome(
        "电动机通用选型约束",
        ENGINE_VERSION,
        _status(provisional_status, rule_codes, rules, warnings),
        provisional_status,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def calculate_motor_breaker_requirements(
    data: MotorBreakerRequirementInput,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """形成某档电缆对应的断路器硬条件和通用参数候选。

    额定电流、Icu和瞬时整定范围只用于筛选产品。没有实际脱扣曲线、
    调节步距和制造商配合表时，正式状态始终为“无法判断”。
    """

    catalog = catalog or DEFAULT_CATALOG
    rule_codes = [
        "MOTOR.SCPD.BREAKER",
        "ELEC.BREAKING.CAPACITY",
        "ELEC.PHASE.THERMAL.WITHSTAND",
        "ELEC.PE.THERMAL.WITHSTAND",
    ]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {"candidates": []}
    positive_fields = (
        (data.motor_rated_current_a, "电动机额定电流"),
        (data.motor_starting_current_a, "电动机启动电流"),
        (data.system_voltage_v, "系统电压"),
        (data.conductor_corrected_ampacity_a, "导体修正后载流量"),
        (data.installation_point_max_short_circuit_ka, "安装点最大短路电流"),
        (data.terminal_minimum_fault_current_a, "线路末端最小故障电流"),
        (data.phase_maximum_clearing_time_s, "相导体最大允许切除时间"),
        (data.pe_maximum_clearing_time_s, "PE最大允许切除时间"),
    )
    for value, label in positive_fields:
        if not _positive(value):
            warnings.append(f"{label}必须大于0。")
    if data.motor_rated_current_a > data.conductor_corrected_ampacity_a:
        warnings.append("电动机额定电流大于导体修正后载流量。")
    parameters = catalog.get("breaker_parameters", {})
    if parameters.get("status") not in {"verified", "approved"}:
        warnings.append("断路器通用设计参数目录尚未核实。")
    if warnings:
        return Outcome(
            "电动机断路器设计要求",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    start_setting_min = 2.0 * data.motor_starting_current_a
    start_setting_max = 2.5 * data.motor_starting_current_a
    required_clearing_time = min(
        data.phase_maximum_clearing_time_s,
        data.pe_maximum_clearing_time_s,
    )
    outputs["required_parameters"] = {
        "rated_voltage_min_v": data.system_voltage_v,
        "rated_current_min_a": round(data.motor_rated_current_a, 6),
        "rated_current_max_a": round(data.conductor_corrected_ampacity_a, 6),
        "breaking_capacity_min_ka": round(
            data.installation_point_max_short_circuit_ka, 6
        ),
        "instantaneous_setting_start_min_a": round(start_setting_min, 6),
        "instantaneous_setting_start_max_a": round(start_setting_max, 6),
        "terminal_minimum_fault_current_a": round(
            data.terminal_minimum_fault_current_a, 6
        ),
        "phase_maximum_clearing_time_s": round(
            data.phase_maximum_clearing_time_s, 6
        ),
        "pe_maximum_clearing_time_s": round(data.pe_maximum_clearing_time_s, 6),
        "governing_maximum_clearing_time_s": round(required_clearing_time, 6),
        "simultaneous_phase_disconnection_required": True,
        "product_poles": "待按回路是否引出中性线确认；电动机三相导体须同时断开",
    }

    for family in ("MCB", "MCCB"):
        family_data = parameters.get("families", {}).get(family, {})
        for group in family_data.get("groups", []):
            for rating_value in group.get("ratings_a", []):
                rating = float(rating_value)
                if not (
                    data.motor_rated_current_a
                    <= rating
                    <= data.conductor_corrected_ampacity_a
                ):
                    continue
                icu_options = sorted(float(value) for value in group.get("icu_ka", []))
                selected_icu = next(
                    (
                        value
                        for value in icu_options
                        if value >= data.installation_point_max_short_circuit_ka
                    ),
                    None,
                )
                if selected_icu is None:
                    continue

                # 19DX101-1表5.3给出直接启动用电动机保护断路器的
                # 瞬时脱扣范围。它不能代替通用MCB的D型产品曲线，故只对
                # 拟采用电动机保护脱扣器的MCCB形成设计区间。
                motor_trip_range_applicable = family == "MCCB"
                table_min = 8.0 * rating if motor_trip_range_applicable else None
                table_max = 15.0 * rating if motor_trip_range_applicable else None
                overlap_min = (
                    max(start_setting_min, table_min)
                    if table_min is not None
                    else None
                )
                overlap_max = (
                    min(start_setting_max, table_max)
                    if table_max is not None
                    else None
                )
                overlap_exists = (
                    overlap_min is not None
                    and overlap_max is not None
                    and overlap_min <= overlap_max
                )
                instantaneous_fault_possible = (
                    data.terminal_minimum_fault_current_a >= overlap_min
                    if overlap_exists and overlap_min is not None
                    else None
                )
                outputs["candidates"].append(
                    {
                        "family": family,
                        "frame_current_a": float(group["frame_a"]),
                        "rated_current_a": rating,
                        "selected_icu_ka": selected_icu,
                        "icu_options_ka": icu_options,
                        "long_delay_setting_min_a": round(
                            data.motor_rated_current_a, 6
                        ),
                        "long_delay_setting_max_a": rating,
                        "table_instantaneous_min_a": (
                            round(table_min, 6) if table_min is not None else None
                        ),
                        "table_instantaneous_max_a": (
                            round(table_max, 6) if table_max is not None else None
                        ),
                        "admissible_instantaneous_min_a": (
                            round(overlap_min, 6) if overlap_exists else None
                        ),
                        "admissible_instantaneous_max_a": (
                            round(overlap_max, 6) if overlap_exists else None
                        ),
                        "starting_ride_through_overlap": (
                            (PASS if overlap_exists else FAIL)
                            if motor_trip_range_applicable
                            else UNKNOWN
                        ),
                        "instantaneous_terminal_fault_possible": (
                            (PASS if instantaneous_fault_possible else FAIL)
                            if instantaneous_fault_possible is not None
                            else UNKNOWN
                        ),
                        "actual_curve_check": UNKNOWN,
                        "formal_status": UNKNOWN,
                        "pending_checks": [
                            "实际脱扣器Ir定义、调节范围及步距",
                            (
                                "D型MCB具体产品保证动作区间"
                                if family == "MCB"
                                else "MCCB电动机保护脱扣器适用性"
                            ),
                            "启动时间内不误动作",
                            "末端故障在允许时间内切除",
                            "制造商时间—电流曲线或I²t",
                            "与接触器、热继电器的1类/2类配合",
                            "上下级选择性及后备保护",
                        ],
                    }
                )

    steps.extend(
        (
            Step(
                "额定电流边界",
                "IrM≤In≤Iz",
                data.motor_rated_current_a,
                "A",
            ),
            Step(
                "分断能力下限",
                "Icu≥安装点最大预期短路电流",
                data.installation_point_max_short_circuit_ka,
                "kA",
            ),
            Step(
                "启动避让瞬时整定范围",
                "2IstM≤Ii≤2.5IstM",
                start_setting_min,
                "A（下限）",
            ),
            Step(
                "保护允许最长切除时间",
                "min(t相导体,tPE)",
                required_clearing_time,
                "s",
            ),
        )
    )
    if not outputs["candidates"]:
        warnings.append("通用参数目录中没有同时满足In、Iz和Icu边界的候选档位。")
    warnings.append(
        "末端最小故障电流低于瞬时整定下限时，不能据此直接否定候选；"
        "必须用实际反时限曲线核对切除时间。"
    )
    warnings.append("MCB未套用表5.3范围；D型保证动作区间须由具体产品曲线取得。")
    warnings.append("当前只形成设计参数候选，不代表MCB或MCCB具体产品已经选定。")
    provisional = PASS if outputs["candidates"] else UNKNOWN
    return Outcome(
        "电动机断路器设计要求",
        ENGINE_VERSION,
        UNKNOWN,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def calculate_motor_starting_network(
    data: MotorStartingNetworkInput,
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """按每相等值阻抗计算直接启动时的母线和电动机端子电压。

    source_to_bus表示电源至被校核配电母线的等值阻抗，bus_to_motor表示
    该母线至电动机端子的线路阻抗。堵转功率因数必须由厂家资料或已核实
    参数目录解析，不能由额定运行功率因数代替。
    """

    rule_codes = ["MOTOR.START.CURRENT", "MOTOR.START.VOLTAGE.NETWORK"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "actual_starting_current_a": None,
        "starting_bus_voltage_v": None,
        "starting_bus_voltage_percent": None,
        "starting_motor_terminal_voltage_v": None,
        "starting_motor_terminal_voltage_percent": None,
        "bus_voltage_check": UNKNOWN,
    }

    positive_values = (
        (data.nominal_line_voltage_v, "系统标称线电压"),
        (data.source_open_circuit_voltage_factor, "电源空载电压系数"),
        (
            data.locked_rotor_current_at_nominal_voltage_a,
            "额定电压下堵转电流",
        ),
    )
    for value, label in positive_values:
        if not _positive(value):
            warnings.append(f"{label}必须大于0。")
    impedance_values = (
        (data.source_to_bus_r_ohm, "电源至母线R"),
        (data.source_to_bus_x_ohm, "电源至母线X"),
        (data.bus_to_motor_r_ohm, "母线至电动机R"),
        (data.bus_to_motor_x_ohm, "母线至电动机X"),
    )
    for value, label in impedance_values:
        if not isfinite(value) or value < 0:
            warnings.append(f"{label}必须为非负有限值。")
    if data.locked_rotor_power_factor is None:
        warnings.append("缺少堵转功率因数，不能建立电动机启动等值阻抗。")
    elif not isfinite(data.locked_rotor_power_factor) or not (
        0 < data.locked_rotor_power_factor <= 1
    ):
        warnings.append("堵转功率因数必须大于0且不大于1。")
    if data.minimum_bus_voltage_percent is not None and not (
        isfinite(data.minimum_bus_voltage_percent)
        and 0 < data.minimum_bus_voltage_percent <= 100
    ):
        warnings.append("启动时母线最低电压限值必须大于0且不大于100%。")
    if warnings:
        return Outcome(
            "电动机启动网络",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    assert data.locked_rotor_power_factor is not None
    nominal_phase_voltage_v = data.nominal_line_voltage_v / sqrt(3)
    motor_impedance_magnitude_ohm = (
        nominal_phase_voltage_v / data.locked_rotor_current_at_nominal_voltage_a
    )
    motor_r_ohm = (
        motor_impedance_magnitude_ohm * data.locked_rotor_power_factor
    )
    motor_x_ohm = motor_impedance_magnitude_ohm * sqrt(
        1 - data.locked_rotor_power_factor**2
    )
    source_impedance = complex(data.source_to_bus_r_ohm, data.source_to_bus_x_ohm)
    feeder_impedance = complex(data.bus_to_motor_r_ohm, data.bus_to_motor_x_ohm)
    motor_impedance = complex(motor_r_ohm, motor_x_ohm)
    total_impedance = source_impedance + feeder_impedance + motor_impedance
    if abs(total_impedance) == 0:
        warnings.append("启动回路总阻抗不能为0。")
        return Outcome(
            "电动机启动网络",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    source_phase_voltage = (
        nominal_phase_voltage_v * data.source_open_circuit_voltage_factor
    )
    starting_current = complex(source_phase_voltage, 0) / total_impedance
    bus_phase_voltage = complex(source_phase_voltage, 0) - (
        starting_current * source_impedance
    )
    motor_phase_voltage = starting_current * motor_impedance
    actual_starting_current_a = abs(starting_current)
    bus_line_voltage_v = abs(bus_phase_voltage) * sqrt(3)
    motor_line_voltage_v = abs(motor_phase_voltage) * sqrt(3)
    bus_percent = bus_line_voltage_v / data.nominal_line_voltage_v * 100
    motor_percent = motor_line_voltage_v / data.nominal_line_voltage_v * 100

    outputs.update(
        {
            "motor_start_r_ohm": round(motor_r_ohm, 9),
            "motor_start_x_ohm": round(motor_x_ohm, 9),
            "actual_starting_current_a": round(actual_starting_current_a, 6),
            "starting_bus_voltage_v": round(bus_line_voltage_v, 6),
            "starting_bus_voltage_percent": round(bus_percent, 6),
            "starting_motor_terminal_voltage_v": round(motor_line_voltage_v, 6),
            "starting_motor_terminal_voltage_percent": round(motor_percent, 6),
        }
    )
    steps.extend(
        (
            Step(
                "电动机每相堵转阻抗",
                "|ZstM|＝(Un/√3)/IstM",
                motor_impedance_magnitude_ohm,
                "Ω",
            ),
            Step(
                "实际启动电流",
                "Ist＝(kU×Un/√3)/(Zsource＋Zfeeder＋ZstM)",
                actual_starting_current_a,
                "A",
            ),
            Step(
                "启动时配电母线电压",
                "Ubus＝√3×|Ephase－Ist×Zsource|",
                bus_line_voltage_v,
                "V",
            ),
            Step(
                "启动时电动机端子电压",
                "Umotor＝√3×|Ist×ZstM|",
                motor_line_voltage_v,
                "V",
            ),
        )
    )

    if data.minimum_bus_voltage_percent is None:
        warnings.append("未取得适用的启动母线电压限值；仅输出母线和端子电压。")
        provisional_status = UNKNOWN
    elif bus_percent + 1e-9 >= data.minimum_bus_voltage_percent:
        outputs["bus_voltage_check"] = PASS
        provisional_status = PASS
    else:
        outputs["bus_voltage_check"] = FAIL
        provisional_status = FAIL

    return Outcome(
        "电动机启动网络",
        ENGINE_VERSION,
        _status(provisional_status, rule_codes, rules, warnings),
        provisional_status,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def calculate_motor_starting_approximation(
    data: MotorApproximateStartingInput,
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """按设计手册表6.5-4的全压启动容量法近似计算启动电压。"""

    rule_codes = ["MOTOR.START.VOLTAGE.APPROX"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "calculation_method": "handbook_table_6_5_4_approximation",
        "starting_bus_voltage_percent": None,
        "starting_motor_terminal_voltage_percent": None,
        "bus_voltage_check": UNKNOWN,
    }
    positive_values = (
        (data.nominal_network_voltage_kv, "网络标称电压"),
        (data.system_average_voltage_kv, "系统平均电压"),
        (data.motor_rated_voltage_kv, "电动机额定电压"),
        (data.motor_rated_current_ka, "电动机额定电流"),
        (data.locked_rotor_current_ratio, "堵转电流倍数"),
        (data.bus_short_circuit_capacity_mva, "母线短路容量"),
        (data.source_bus_voltage_pu, "电源母线电压相对值"),
    )
    for value, label in positive_values:
        if not _positive(value):
            warnings.append(f"{label}必须大于0。")
    if (
        not isfinite(data.preconnected_reactive_load_mvar)
        or data.preconnected_reactive_load_mvar < 0
    ):
        warnings.append("预接负荷无功功率必须为非负有限值。")
    if (
        not isfinite(data.motor_feeder_reactance_ohm)
        or data.motor_feeder_reactance_ohm < 0
    ):
        warnings.append("电动机馈线电抗必须为非负有限值。")
    if data.minimum_bus_voltage_percent is not None and not (
        isfinite(data.minimum_bus_voltage_percent)
        and 0 < data.minimum_bus_voltage_percent <= 100
    ):
        warnings.append("启动时母线最低电压限值必须大于0且不大于100%。")
    if warnings:
        return Outcome(
            "电动机启动电压近似计算",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    rated_apparent_power_mva = (
        sqrt(3) * data.motor_rated_voltage_kv * data.motor_rated_current_ka
    )
    rated_starting_capacity_mva = (
        data.locked_rotor_current_ratio * rated_apparent_power_mva
    )
    if data.motor_feeder_reactance_ohm == 0:
        starting_circuit_capacity_mva = rated_starting_capacity_mva
        line_capacity_mva: float | None = None
        capacity_expression = "Xl＝0时，Sst＝SstM"
    else:
        line_capacity_mva = (
            data.system_average_voltage_kv**2
            / data.motor_feeder_reactance_ohm
        )
        starting_circuit_capacity_mva = 1 / (
            1 / rated_starting_capacity_mva + 1 / line_capacity_mva
        )
        capacity_expression = "Sst＝1/(1/SstM＋Xl/Uav²)"

    bus_voltage_pu = (
        data.source_bus_voltage_pu
        * data.bus_short_circuit_capacity_mva
        / (
            data.bus_short_circuit_capacity_mva
            + data.preconnected_reactive_load_mvar
            + starting_circuit_capacity_mva
        )
    )
    motor_terminal_voltage_pu = (
        bus_voltage_pu
        * starting_circuit_capacity_mva
        / rated_starting_capacity_mva
    )
    starting_circuit_current_ka = (
        bus_voltage_pu
        * starting_circuit_capacity_mva
        / (sqrt(3) * data.nominal_network_voltage_kv)
    )
    motor_starting_current_ka = (
        motor_terminal_voltage_pu
        * rated_starting_capacity_mva
        / (sqrt(3) * data.motor_rated_voltage_kv)
    )
    bus_percent = bus_voltage_pu * 100
    motor_percent = motor_terminal_voltage_pu * 100
    outputs.update(
        {
            "motor_rated_apparent_power_mva": round(rated_apparent_power_mva, 9),
            "motor_rated_starting_capacity_mva": round(
                rated_starting_capacity_mva, 9
            ),
            "line_capacity_mva": (
                round(line_capacity_mva, 9)
                if line_capacity_mva is not None
                else None
            ),
            "starting_circuit_capacity_mva": round(
                starting_circuit_capacity_mva, 9
            ),
            "starting_bus_voltage_percent": round(bus_percent, 6),
            "starting_motor_terminal_voltage_percent": round(motor_percent, 6),
            "starting_circuit_current_ka": round(starting_circuit_current_ka, 9),
            "motor_starting_current_ka": round(motor_starting_current_ka, 9),
        }
    )
    steps.extend(
        (
            Step(
                "电动机额定容量",
                "SrM＝√3UrMIrM",
                rated_apparent_power_mva,
                "MVA",
            ),
            Step(
                "电动机额定启动容量",
                "SstM＝kstSrM",
                rated_starting_capacity_mva,
                "MVA",
            ),
            Step(
                "启动回路计算容量",
                capacity_expression,
                starting_circuit_capacity_mva,
                "MVA",
            ),
            Step(
                "启动时母线电压",
                "ustB＝usSscB/(SscB＋QL＋Sst)",
                bus_percent,
                "%",
            ),
            Step(
                "启动时电动机端子电压",
                "ustM＝ustB×Sst/SstM",
                motor_percent,
                "%",
            ),
        )
    )
    warnings.append(
        "本结果采用设计手册表6.5-4容量法，仅为近似计算；必要时应使用完整网络R/X法。"
    )
    if data.minimum_bus_voltage_percent is None:
        warnings.append("未取得适用的启动母线电压限值；仅输出母线和端子电压。")
        provisional_status = UNKNOWN
    elif bus_percent + 1e-9 >= data.minimum_bus_voltage_percent:
        outputs["bus_voltage_check"] = PASS
        provisional_status = PASS
    else:
        outputs["bus_voltage_check"] = FAIL
        provisional_status = FAIL

    return Outcome(
        "电动机启动电压近似计算",
        ENGINE_VERSION,
        _status(provisional_status, rule_codes, rules, warnings),
        provisional_status,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def calculate_motor_cable_preselection(
    data: MotorCablePreselectionInput,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """按运行电流生成电动机末端线路基础候选并逐项计算运行压降。"""

    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {"candidates": [], "conditions_complete": False}
    if not _positive(data.rated_current_a):
        warnings.append("电动机额定电流必须大于0。")
    if not _positive(data.rated_voltage_v):
        warnings.append("电动机额定电压必须大于0。")
    if not _positive(data.length_m):
        warnings.append("线路长度必须大于0。")
    if data.running_power_factor is not None and (
        not _positive(data.running_power_factor) or data.running_power_factor > 1
    ):
        warnings.append("运行功率因数必须大于0且不大于1。")
    if warnings:
        return Outcome(
            "电动机电缆初选",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            [],
        )

    protective_mode = (
        "included"
        if data.conductor_configuration_code == "yjv_4c_3ph_n_pe"
        else "separate"
    )
    cable_result = generate_cable_candidates(
        CableSelectionRequest(
            segment_id="motor-final-circuit",
            family=data.conductor_family,
            configuration_code=data.conductor_configuration_code,
            phase=Phase.THREE,
            system_voltage_v=data.rated_voltage_v,
            installation_scenario=data.installation_scenario,
            minimum_required_ampacity_a=data.rated_current_a,
            neutral_required=False,
            protective_conductor_mode=protective_mode,
            conditions=CableInstallationConditions(
                temperature_c=data.installation_temperature_c,
                tray_type=data.tray_type,
                tray_layers=data.tray_layers,
                tray_cables_per_layer=data.tray_cables_per_layer,
                enclosed_circuit_count=data.enclosed_circuit_count,
            ),
        ),
        rules,
        catalog or DEFAULT_CATALOG,
    )
    warnings.extend(cable_result.warnings)
    outputs["conditions_complete"] = cable_result.outputs.get(
        "conditions_complete", False
    )
    outputs["ampacity_checks"] = cable_result.outputs.get("checks", [])

    for candidate in cable_result.outputs.get("candidates", []):
        item = dict(candidate)
        resolved = candidate.get("resolved_electrical") or {}
        resistance = resolved.get("voltage_drop_r_ohm_per_km")
        reactance = resolved.get("voltage_drop_x_ohm_per_km")
        if (
            resistance is None
            or reactance is None
            or data.running_power_factor is None
        ):
            item["running_voltage_drop_v"] = None
            item["running_voltage_drop_percent"] = None
            item["running_voltage_drop_status"] = UNKNOWN
            item["pending_checks"] = [
                *item.get("pending_checks", []),
                (
                    "缺少运行功率因数"
                    if data.running_power_factor is None
                    else "缺少该候选的运行压降R/X"
                ),
            ]
        else:
            voltage_drop = calculate_voltage_drop(
                {
                    "phase": "3",
                    "voltage_v": data.rated_voltage_v,
                    "length_m": data.length_m,
                    "cable_r_ohm_per_km": resistance,
                    "cable_x_ohm_per_km": reactance,
                    "power_factor": data.running_power_factor,
                },
                rules,
                design_current_a=data.rated_current_a,
            )
            item["running_voltage_drop_v"] = voltage_drop.outputs.get(
                "voltage_drop_v"
            )
            item["running_voltage_drop_percent"] = voltage_drop.outputs.get(
                "voltage_drop_pct"
            )
            item["running_voltage_drop_status"] = UNKNOWN
        outputs["candidates"].append(item)

    if outputs["candidates"]:
        steps.append(
            Step(
                "电动机电缆载流量候选",
                "Iz≥IrM",
                len(outputs["candidates"]),
                "个",
            )
        )
    warnings.extend(
        (
            "电动机主回路载流量候选以额定电流为下限；经常接近满负荷时的适当裕量尚未形成批准规则。",
            "已计算运行压降数值，但电动机回路适用限值尚未批准，因此不据此淘汰截面。",
            "启动电压、短路保护、相导体及PE热稳定仍须在后续完整网络中独立校核。",
        )
    )
    return Outcome(
        "电动机电缆初选",
        ENGINE_VERSION,
        UNKNOWN,
        PASS if outputs["candidates"] else UNKNOWN,
        outputs,
        steps,
        warnings,
        list(dict.fromkeys(cable_result.rule_codes + ["MOTOR.CABLE.SELECTION", "ELEC.VDROP"])),
    )
