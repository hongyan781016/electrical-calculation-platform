"""AC-3接触器和热过载继电器的精确产品参考。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import FAIL, PASS, UNKNOWN


DEFAULT_CONTROL_CATALOG = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "chint-motor-control-products.json"
)
DEFAULT_SIEMENS_IE3_STARTER_CATALOG = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "siemens-ie3-type2-motor-starters.json"
)


def _in_380_400v_application_band(system_voltage_v: float) -> bool:
    """项目约定：0.4kV系统中的380V与400V按同一产品应用档处理。"""

    return 380 <= system_voltage_v <= 400


def load_motor_control_catalog(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or DEFAULT_CONTROL_CATALOG).read_text(encoding="utf-8"))


def load_siemens_ie3_starter_catalog(path: Path | None = None) -> dict[str, Any]:
    return json.loads(
        (path or DEFAULT_SIEMENS_IE3_STARTER_CATALOG).read_text(encoding="utf-8")
    )


def select_motor_control_references(
    *,
    motor_rated_current_a: float,
    motor_starting_current_a: float,
    motor_rated_output_power_kw: float | None,
    system_voltage_v: float,
    motor_starting_time_s: float | None = None,
    motor_efficiency_class: str | None = None,
    installation_point_max_short_circuit_ka: float | None = None,
    terminal_minimum_fault_current_a: float | None = None,
    phase_maximum_clearing_time_s: float | None = None,
    pe_maximum_clearing_time_s: float | None = None,
    catalog: dict[str, Any] | None = None,
    siemens_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按380/400V AC-3表和热继电器整定范围形成产品参考。"""

    data = catalog or load_motor_control_catalog()
    siemens = siemens_catalog or load_siemens_ie3_starter_catalog()
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "catalog_code": data.get("catalog_code"),
        "contactor_candidate": None,
        "overload_relay_candidates": [],
    }
    if motor_rated_current_a <= 0 or motor_starting_current_a <= 0:
        result["reason"] = "电动机额定电流和启动电流必须大于0。"
        return result
    if system_voltage_v < 380 or system_voltage_v > 400:
        result["reason"] = "当前只核验NXC在380/400V下的AC-3表列参数。"
        return result
    if motor_rated_output_power_kw is not None and motor_rated_output_power_kw <= 0:
        result["reason"] = "电动机额定输出功率必须大于0或留空。"
        return result
    if motor_starting_time_s is not None and motor_starting_time_s <= 0:
        result["reason"] = "电动机实际启动时间必须大于0或留空。"
        return result

    contactors = [
        item
        for item in data["nxc_ac3_380_400v"]
        if float(item["rated_current_a"]) >= motor_rated_current_a
        and (
            motor_rated_output_power_kw is None
            or float(item["motor_power_kw"]) >= motor_rated_output_power_kw
        )
    ]
    if not contactors:
        result["reason"] = "NXC已录入范围内没有同时满足AC-3电流和功率的候选。"
        return result
    contactor = min(
        contactors,
        key=lambda item: (float(item["rated_current_a"]), float(item["motor_power_kw"])),
    )
    contactor_model = contactor["model"]
    compatible_frames = {
        frame
        for frame, models in data["nxr_contactor_compatibility"].items()
        if contactor_model in models
    }
    relay_candidates = [
        {
            **item,
            "setting_target_a": round(motor_rated_current_a, 6),
            "trip_class": data["nxr_trip"]["trip_class"],
            "phase_failure_protection": data["nxr_trip"]["phase_failure_protection"],
            "temperature_compensation": data["nxr_trip"]["temperature_compensation"],
        }
        for item in data["nxr_ranges"]
        if item["frame"] in compatible_frames
        and float(item["setting_min_a"])
        <= motor_rated_current_a
        <= float(item["setting_max_a"])
    ]
    relay_candidates.sort(
        key=lambda item: (
            float(item["setting_max_a"]) - float(item["setting_min_a"]),
            float(item["setting_min_a"]),
        )
    )
    recommended_overload_relay = min(
        relay_candidates,
        key=lambda item: abs(
            motor_rated_current_a
            - (
                float(item["setting_min_a"])
                + float(item["setting_max_a"])
            )
            / 2
        )
        / (float(item["setting_max_a"]) - float(item["setting_min_a"])),
        default=None,
    )

    starting_multiple = motor_starting_current_a / motor_rated_current_a
    trip_min, trip_max = data["nxr_trip"]["cold_7_2_setting_current_trip_time_s"]
    overload_start_status = UNKNOWN
    if motor_starting_time_s is not None:
        if starting_multiple <= 7.2 and motor_starting_time_s < float(trip_min):
            overload_start_status = PASS
        elif starting_multiple >= 7.2 and motor_starting_time_s > float(trip_max):
            overload_start_status = FAIL

    type_2 = data.get("type_2_coordination", {})
    type_2_row = None
    if motor_rated_output_power_kw is not None:
        type_2_row = type_2.get("rows", {}).get(f"{motor_rated_output_power_kw:g}")
    allowed_ie_classes = type_2.get("conditions", {}).get(
        "motor_efficiency_classes", []
    )
    type_2_formal_status = UNKNOWN
    if type_2_row and motor_efficiency_class in allowed_ie_classes:
        if installation_point_max_short_circuit_ka is None:
            type_2_provisional_status = UNKNOWN
            type_2_applicability = "表列组合适用；尚须校核安装点预期短路电流不超过50kA。"
        elif installation_point_max_short_circuit_ka <= float(
            type_2["conditions"]["iq_ka"]
        ):
            type_2_provisional_status = PASS
            type_2_applicability = (
                f"安装点最大短路电流{installation_point_max_short_circuit_ka:.3f}kA"
                "不超过表列Iq 50kA；表列条件暂算匹配，资料未批准。"
            )
        else:
            type_2_provisional_status = FAIL
            type_2_applicability = (
                f"安装点最大短路电流{installation_point_max_short_circuit_ka:.3f}kA"
                "超过表列Iq 50kA，不能采用该2类配合组合。"
            )
    elif type_2_row and motor_efficiency_class:
        type_2_provisional_status = "不适用"
        type_2_applicability = (
            f"该2类配合表只标明IE1/IE2；当前电动机为{motor_efficiency_class}，不能套用。"
        )
    else:
        type_2_provisional_status = UNKNOWN
        type_2_applicability = "缺少精确表列组合或电动机能效等级，不能判断适用性。"

    type_2_devices = None
    if type_2_row:
        type_2_devices = {
            "fuse_option": (
                f"gG {type_2_row['fuse']['gg_a']}A 或 aM {type_2_row['fuse']['am_a']}A"
            ),
            "mccb_option": (
                f"{type_2_row['mccb']['family']}系列 / In {type_2_row['mccb']['rated_current_a']}A"
            ),
            "contactor": type_2_row["contactor"],
            "overload_relay": (
                f"{type_2_row['overload_relay']['frame']} "
                f"{type_2_row['overload_relay']['setting_min_a']}～"
                f"{type_2_row['overload_relay']['setting_max_a']}A"
            ),
            "iq_ka": type_2["conditions"]["iq_ka"],
        }

    ns2 = data.get("ns2_standalone", {})
    ns2_rows = [
        row
        for row in ns2.get("rows", [])
        if float(row["setting_min_a"])
        <= motor_rated_current_a
        <= float(row["setting_max_a"])
    ]
    ns2_row = min(
        ns2_rows,
        key=lambda item: (
            float(item["setting_max_a"]) - float(item["setting_min_a"]),
            float(item["setting_min_a"]),
        ),
        default=None,
    )
    ns2_candidate = None
    ns2_standalone_provisional_status = UNKNOWN
    ns2_standalone_applicability = "已录入NS2范围内没有覆盖电动机额定电流的候选。"
    if ns2_row:
        ns2_conditions = ns2["conditions"]
        instantaneous_release_a = float(ns2_row["instantaneous_release_a"])
        no_trip_boundary_a = (
            float(ns2_conditions["instantaneous_no_trip_multiplier"])
            * instantaneous_release_a
        )
        trip_boundary_a = (
            float(ns2_conditions["instantaneous_trip_multiplier"])
            * instantaneous_release_a
        )
        if motor_starting_current_a <= no_trip_boundary_a:
            ns2_starting_instantaneous_status = PASS
        elif motor_starting_current_a >= trip_boundary_a:
            ns2_starting_instantaneous_status = FAIL
        else:
            ns2_starting_instantaneous_status = UNKNOWN

        ns2_class10_starting_time_status = UNKNOWN
        if (
            motor_starting_time_s is not None
            and abs(
                starting_multiple
                - float(ns2_conditions["cold_reference_multiple"])
            )
            < 1e-9
        ):
            if motor_starting_time_s <= float(
                ns2_conditions["cold_minimum_trip_time_s"]
            ):
                ns2_class10_starting_time_status = PASS
            elif motor_starting_time_s > float(
                ns2_conditions["cold_maximum_trip_time_s"]
            ):
                ns2_class10_starting_time_status = FAIL

        ns2_voltage_status = (
            PASS if _in_380_400v_application_band(system_voltage_v) else UNKNOWN
        )
        ns2_icu_status = UNKNOWN
        ns2_ics_status = UNKNOWN
        if installation_point_max_short_circuit_ka is not None and ns2_voltage_status == PASS:
            ns2_icu_status = (
                PASS
                if installation_point_max_short_circuit_ka
                <= float(ns2_row["icu_ka_at_400_415v"])
                else FAIL
            )
            ns2_ics_status = (
                PASS
                if installation_point_max_short_circuit_ka
                <= float(ns2_row["ics_ka_at_400_415v"])
                else FAIL
            )

        ns2_terminal_instantaneous_status = UNKNOWN
        if terminal_minimum_fault_current_a is not None:
            if terminal_minimum_fault_current_a >= trip_boundary_a:
                ns2_terminal_instantaneous_status = PASS
            elif terminal_minimum_fault_current_a <= no_trip_boundary_a:
                ns2_terminal_instantaneous_status = FAIL

        available_clearing_times = [
            float(value)
            for value in (
                phase_maximum_clearing_time_s,
                pe_maximum_clearing_time_s,
            )
            if value is not None
        ]
        governing_maximum_clearing_time_s = (
            min(available_clearing_times) if available_clearing_times else None
        )
        ns2_total_clearing_time_status = UNKNOWN

        required_checks = [
            ns2_voltage_status,
            ns2_starting_instantaneous_status,
            ns2_class10_starting_time_status,
            ns2_icu_status,
        ]
        if FAIL in required_checks:
            ns2_standalone_provisional_status = FAIL
        elif all(status == PASS for status in required_checks):
            ns2_standalone_provisional_status = PASS

        ns2_candidate = {
            **ns2_row,
            "overload_setting_target_a": round(motor_rated_current_a, 6),
            "overload_setting_range_status": PASS,
            "system_voltage_status": ns2_voltage_status,
            "starting_current_a": round(motor_starting_current_a, 6),
            "instantaneous_no_trip_boundary_a": round(no_trip_boundary_a, 6),
            "instantaneous_trip_boundary_a": round(trip_boundary_a, 6),
            "starting_instantaneous_status": ns2_starting_instantaneous_status,
            "actual_starting_multiple_of_setting": round(starting_multiple, 6),
            "class10_cold_reference_multiple": ns2_conditions[
                "cold_reference_multiple"
            ],
            "class10_cold_minimum_trip_time_s": ns2_conditions[
                "cold_minimum_trip_time_s"
            ],
            "class10_cold_maximum_trip_time_s": ns2_conditions[
                "cold_maximum_trip_time_s"
            ],
            "class10_cold_starting_time_status": (
                ns2_class10_starting_time_status
            ),
            "instantaneous_test_time_s": ns2_conditions[
                "instantaneous_test_time_s"
            ],
            "standalone_icu_status": ns2_icu_status,
            "standalone_ics_status": ns2_ics_status,
            "terminal_minimum_fault_current_a": terminal_minimum_fault_current_a,
            "terminal_guaranteed_instantaneous_status": (
                ns2_terminal_instantaneous_status
            ),
            "governing_maximum_clearing_time_s": (
                governing_maximum_clearing_time_s
            ),
            "instantaneous_trip_test_time_upper_bound_s": ns2_conditions[
                "instantaneous_test_time_s"
            ],
            "total_clearing_time_status": ns2_total_clearing_time_status,
            "total_clearing_time_evidence": (
                "目录的t<0.2s是瞬时电磁脱扣动作特性试验，未明确为包含触头完全开断的总切除时间；"
                "说明书Figure 1虽提供20℃时间-电流特性曲线，也未标明为总分断时间，且没有限流I²t数据；"
                "不能直接用于相导体或PE热稳定通过判定。"
            ),
            "time_current_curve_reference": ns2[
                "time_current_curve_reference"
            ],
            "source": ns2["source"],
        }
        voltage_note = (
            "按项目约定，380/400V同一应用电压档"
            if ns2_voltage_status == PASS
            else (
                f"产品目录只列400/415V，当前系统为{system_voltage_v:g}V，"
                "分断能力不跨电压外推"
            )
        )
        fault_note = (
            "尚缺安装点最大短路电流"
            if installation_point_max_short_circuit_ka is None
            else (
                f"安装点最大短路电流{installation_point_max_short_circuit_ka:.3f}kA，"
                f"Icu校核{ns2_icu_status}、Ics对照{ns2_ics_status}"
            )
        )
        terminal_note = (
            "尚缺线路末端最小故障电流"
            if terminal_minimum_fault_current_a is None
            else (
                f"末端最小故障电流{terminal_minimum_fault_current_a:.3f}A，"
                f"进入保证瞬时动作区校核{ns2_terminal_instantaneous_status}"
            )
        )
        ns2_standalone_applicability = (
            f"可整定至{motor_rated_current_a:.3f}A；{voltage_note}；"
            f"启动瞬时边界校核{ns2_starting_instantaneous_status}；"
            f"Class 10A精确7.2倍冷态点校核{ns2_class10_starting_time_status}；"
            f"{fault_note}；{terminal_note}；总切除时间校核"
            f"{ns2_total_clearing_time_status}。该结论只针对NS2单品参数，"
            "不代表NS2＋NC8的2类配合。"
        )

    integrated = data.get("integrated_mpcb_type_2_coordination", {})
    integrated_row = None
    if motor_rated_output_power_kw is not None:
        integrated_row = integrated.get("rows", {}).get(
            f"{motor_rated_output_power_kw:g}"
        )
    integrated_candidate = None
    integrated_provisional_status = UNKNOWN
    integrated_applicability = "没有精确表列组合。"
    if integrated_row:
        integrated_candidate = {
            **integrated_row,
            "iq_ka": integrated["conditions"]["iq_ka"],
            "source": integrated["source"],
        }
        integrated_allowed_classes = integrated["conditions"][
            "motor_efficiency_classes"
        ]
        if motor_efficiency_class not in integrated_allowed_classes:
            integrated_provisional_status = "不适用"
            integrated_applicability = (
                f"该组合只标明IE1/IE2；当前电动机为{motor_efficiency_class or '未知'}。"
            )
        elif not (
            float(integrated_row["setting_min_a"])
            <= motor_rated_current_a
            <= float(integrated_row["setting_max_a"])
        ):
            integrated_provisional_status = FAIL
            integrated_applicability = "电动机额定电流不在MPCB表列整定范围内。"
        elif installation_point_max_short_circuit_ka is None:
            integrated_applicability = "整定范围覆盖额定电流；尚缺安装点最大短路电流。"
        elif installation_point_max_short_circuit_ka <= float(
            integrated["conditions"]["iq_ka"]
        ):
            integrated_provisional_status = PASS
            integrated_applicability = (
                f"可整定至{motor_rated_current_a:.3f}A，安装点最大短路电流"
                f"{installation_point_max_short_circuit_ka:.3f}kA不超过表列50kA；"
                "资料未批准。"
            )
        else:
            integrated_provisional_status = FAIL
            integrated_applicability = "安装点最大短路电流超过表列50kA。"

    siemens_rows = []
    if motor_rated_output_power_kw is not None:
        siemens_rows = siemens.get("rows", {}).get(
            f"{motor_rated_output_power_kw:g}", []
        )
    siemens_candidates = []
    for row in siemens_rows:
        if not (
            float(row["setting_min_a"])
            <= motor_rated_current_a
            <= float(row["setting_max_a"])
        ):
            continue
        exact_product_conditions = (
            motor_efficiency_class
            in siemens["conditions"]["motor_efficiency_classes"]
            and _in_380_400v_application_band(system_voltage_v)
        )
        starting_limit_status = (
            PASS
            if exact_product_conditions
            and motor_starting_current_a
            <= float(row["ie3_ie4_starting_current_limit_a"])
            else (
                FAIL
                if exact_product_conditions
                else UNKNOWN
            )
        )
        standalone_icu_status = UNKNOWN
        if exact_product_conditions and installation_point_max_short_circuit_ka is not None:
            standalone_icu_status = (
                PASS
                if installation_point_max_short_circuit_ka
                <= float(row["standalone_icu_ka_at_400v"])
                else FAIL
            )
        trip_reference = siemens["trip_characteristic_reference"]
        starting_multiple = motor_starting_current_a / motor_rated_current_a
        cold_class10_starting_time_status = UNKNOWN
        if (
            motor_starting_time_s is not None
            and abs(
                starting_multiple
                - float(trip_reference["cold_reference_multiple"])
            )
            < 1e-9
        ):
            if motor_starting_time_s <= float(
                trip_reference["cold_minimum_trip_time_s"]
            ):
                cold_class10_starting_time_status = PASS
            elif motor_starting_time_s > float(
                trip_reference["cold_maximum_trip_time_s"]
            ):
                cold_class10_starting_time_status = FAIL
        siemens_candidates.append(
            {
                **row,
                "overload_setting_target_a": round(motor_rated_current_a, 6),
                "overload_setting_range_status": PASS,
                "starting_current_a": round(motor_starting_current_a, 6),
                "starting_current_limit_status": starting_limit_status,
                "instantaneous_nominal_ride_through_status": (
                    PASS
                    if exact_product_conditions
                    and motor_starting_current_a
                    < float(row["instantaneous_release_a"])
                    else (
                        FAIL
                        if exact_product_conditions
                        else UNKNOWN
                    )
                ),
                "standalone_icu_status": standalone_icu_status,
                "actual_starting_multiple_of_setting": round(
                    starting_multiple, 6
                ),
                "class10_cold_reference_multiple": trip_reference[
                    "cold_reference_multiple"
                ],
                "class10_cold_minimum_trip_time_s": trip_reference[
                    "cold_minimum_trip_time_s"
                ],
                "class10_cold_maximum_trip_time_s": trip_reference[
                    "cold_maximum_trip_time_s"
                ],
                "class10_cold_starting_time_status": (
                    cold_class10_starting_time_status
                ),
                "trip_time_maximum_deviation_percent_at_or_above_3x": (
                    trip_reference[
                        "trip_time_maximum_deviation_percent_at_or_above_3x"
                    ]
                ),
                "operating_temperature_time_factor_approx": trip_reference[
                    "operating_temperature_time_factor_approx"
                ],
                "exact_product_curve_available": trip_reference[
                    "exact_product_curve_available"
                ],
            }
        )
    siemens_formal_status = UNKNOWN
    siemens_provisional_status = UNKNOWN
    if not siemens_candidates:
        siemens_applicability = "没有同时覆盖功率和电动机额定电流的精确表列组合。"
    elif motor_efficiency_class not in siemens["conditions"][
        "motor_efficiency_classes"
    ]:
        siemens_provisional_status = "不适用"
        siemens_applicability = "该表只标明IE3/IE4电动机。"
    elif any(
        item["starting_current_limit_status"] == FAIL
        for item in siemens_candidates
    ):
        siemens_provisional_status = FAIL
        siemens_applicability = (
            f"实际启动电流{motor_starting_current_a:.3f}A超过原表脚注允许的"
            "IE3/IE4 S2启动电流720A；不能采用该表列组合。"
        )
    elif installation_point_max_short_circuit_ka is None:
        siemens_applicability = "功率、电流、能效等级和电压匹配；尚缺安装点最大短路电流。"
    elif installation_point_max_short_circuit_ka <= float(
        siemens["conditions"]["iq_ka"]
    ):
        siemens_provisional_status = PASS
        siemens_applicability = (
            "按项目约定，380/400V同一应用电压档；"
            f"安装点最大短路电流{installation_point_max_short_circuit_ka:.3f}kA"
            f"不超过表列Iq 100kA；实际启动电流{motor_starting_current_a:.3f}A"
            "不超过第7章脚注的720A边界，资料未批准。"
        )
    else:
        siemens_provisional_status = FAIL
        siemens_applicability = "安装点最大短路电流超过表列Iq 100kA。"

    result.update(
        {
            "contactor_candidate": {
                **contactor,
                "use_category": "AC-3",
                "system_voltage_v": system_voltage_v,
                "coil_voltage": "待控制回路确定，不从主回路电压推断",
            },
            "overload_relay_candidates": relay_candidates,
            "recommended_overload_relay": recommended_overload_relay,
            "overload_setting_target_a": round(motor_rated_current_a, 6),
            "starting_current_multiple_setting": round(starting_multiple, 6),
            "motor_starting_time_s": motor_starting_time_s,
            "overload_starting_time_check": overload_start_status,
            "manufacturer_pairing_status": PASS if relay_candidates else FAIL,
            "type_2_coordination_status": type_2_formal_status,
            "type_2_coordination_provisional_status": type_2_provisional_status,
            "type_2_coordination_applicability": type_2_applicability,
            "installation_point_max_short_circuit_ka": installation_point_max_short_circuit_ka,
            "type_2_coordination_devices": type_2_devices,
            "ns2_standalone_candidate": ns2_candidate,
            "ns2_standalone_formal_status": UNKNOWN,
            "ns2_standalone_provisional_status": (
                ns2_standalone_provisional_status
            ),
            "ns2_standalone_applicability": ns2_standalone_applicability,
            "integrated_mpcb_candidate": integrated_candidate,
            "integrated_mpcb_formal_status": UNKNOWN,
            "integrated_mpcb_provisional_status": integrated_provisional_status,
            "integrated_mpcb_applicability": integrated_applicability,
            "siemens_ie3_type2_candidates": siemens_candidates,
            "siemens_ie3_type2_formal_status": siemens_formal_status,
            "siemens_ie3_type2_provisional_status": siemens_provisional_status,
            "siemens_ie3_type2_applicability": siemens_applicability,
            "siemens_ie3_type2_source": siemens["source"],
            "protection_architectures": [
                {
                    "code": "integrated_motor_mccb",
                    "title": "路线A：可调整定MPCB＋接触器",
                    "devices": (
                        f"{integrated_row['mpcb_model']}（{integrated_row['setting_min_a']}～"
                        f"{integrated_row['setting_max_a']}A）＋{integrated_row['contactor']}"
                        if integrated_row
                        else "精确表列组合缺失"
                    ),
                    "overload_protection": "由MPCB可调整定承担；不配置独立NXR",
                    "status": integrated_provisional_status,
                    "unresolved": integrated_applicability,
                },
                {
                    "code": "separate_overload_relay",
                    "title": "路线B：短路保护器件＋接触器＋独立热继电器",
                    "devices": (
                        (
                            f"{type_2_devices['fuse_option']}（或{type_2_devices['mccb_option']}）＋"
                            f"{type_2_devices['contactor']}＋{type_2_devices['overload_relay']}"
                            if type_2_devices
                            else f"短路保护器件待定＋{contactor_model}＋"
                        )
                        + ("" if type_2_devices else (
                            " / ".join(
                                f"{item['frame']} {item['setting_min_a']}～{item['setting_max_a']}A"
                                for item in relay_candidates
                            )
                            if relay_candidates
                            else "NXR候选缺失"
                        ))
                    ),
                    "overload_protection": "由NXR独立承担，并整定至电动机额定电流",
                    "status": UNKNOWN,
                    "unresolved": (
                        f"{type_2_applicability}并须确认控制线圈电压和实际启动时间。"
                    ),
                },
            ],
            "source": {
                "contactor": data["contactor_source"],
                "overload": data["overload_source"],
            },
            "formal_calculation_allowed": data["formal_calculation_allowed"],
            "architecture_warning": (
                "CM3电动机型已含热过载保护；若另用NXR，须明确采用独立热继电器"
                "的保护结构并取得短路保护器-接触器-热继电器配合表，不能重复配置后直接判定通过。"
            ),
            "reason": (
                "已按NXC 380/400V AC-3表和NXR整定范围/接触器配合表形成参考；"
                "线圈电压和2类配合仍待控制回路及制造商组合表。"
            ),
        }
    )
    result["provisional_status"] = (
        FAIL
        if not relay_candidates or overload_start_status == FAIL
        else UNKNOWN
    )
    return result
