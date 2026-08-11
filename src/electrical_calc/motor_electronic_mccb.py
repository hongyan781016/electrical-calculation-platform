"""电子式MCCB在独立过载继电器路线中的产品级参考。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import FAIL, PASS, UNKNOWN


DEFAULT_CDM3E_CATALOG = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "delixi-cdm3e-electronic-mccb.json"
)


def load_cdm3e_catalog(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or DEFAULT_CDM3E_CATALOG).read_text(encoding="utf-8"))


def evaluate_cdm3e_motor_reference(
    *,
    motor_rated_current_a: float,
    motor_starting_current_a: float,
    conductor_corrected_ampacity_a: float,
    system_voltage_v: float,
    required_icu_ka: float,
    terminal_minimum_fault_current_a: float,
    phase_maximum_clearing_time_s: float,
    pe_maximum_clearing_time_s: float,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """形成CDM3E短路保护器件＋独立过载继电器候选。

    长延时关闭，过载保护由外部热继电器承担。短延时只作标称整定
    暂算；样本未给拾取允差，因此不能产生正式热稳定通过结论。
    """

    data = catalog or load_cdm3e_catalog()
    products = [
        product
        for product in data["products"]
        if float(product["controller_rated_current_a"]) >= motor_rated_current_a
    ]
    if not products:
        return {
            "formal_status": UNKNOWN,
            "provisional_status": UNKNOWN,
            "reason": "CDM3E已核验控制器范围内没有覆盖电动机额定电流的候选。",
        }
    product = min(products, key=lambda item: float(item["controller_rated_current_a"]))
    base_a = float(product["controller_rated_current_a"])
    pickup_options = [
        (int(multiplier), base_a * float(multiplier))
        for multiplier in product["short_delay_multipliers"]
        if base_a * float(multiplier) > motor_starting_current_a
    ]
    if not pickup_options:
        return {
            "formal_status": UNKNOWN,
            "provisional_status": FAIL,
            "model": product["model"],
            "reason": "全部短延时标称整定值均不能避开电动机启动电流。",
        }
    multiplier, pickup_a = min(pickup_options, key=lambda item: item[1])
    shortest_delay_s = float(product["short_delay_i2t_off"]["delay_settings_s"][0])
    maximum_breaking_time_s = float(
        product["short_delay_i2t_off"]["maximum_breaking_times_s"][0]
    )
    governing_time_s = min(
        float(phase_maximum_clearing_time_s), float(pe_maximum_clearing_time_s)
    )
    ampacity_status = (
        PASS if conductor_corrected_ampacity_a >= motor_rated_current_a else FAIL
    )
    starting_nominal_status = (
        PASS if motor_starting_current_a < pickup_a else FAIL
    )
    terminal_nominal_pickup_status = (
        PASS if terminal_minimum_fault_current_a >= pickup_a else FAIL
    )
    nominal_thermal_status = (
        PASS
        if terminal_nominal_pickup_status == PASS
        and maximum_breaking_time_s <= governing_time_s
        else FAIL
    )
    voltage_status = (
        PASS
        if system_voltage_v
        in [float(v) for v in data["conditions"]["breaking_capacity_table_voltages_v"]]
        else UNKNOWN
    )
    icu_status = (
        PASS
        if voltage_status == PASS
        and required_icu_ka <= float(product["icu_ka_at_400_415v"])
        else (
            FAIL
            if voltage_status == PASS
            and required_icu_ka > float(product["icu_ka_at_400_415v"])
            else UNKNOWN
        )
    )
    provisional_status = (
        FAIL
        if FAIL in (ampacity_status, starting_nominal_status, nominal_thermal_status, icu_status)
        else UNKNOWN
    )
    return {
        **product,
        "catalog_code": data["catalog_code"],
        "formal_status": UNKNOWN,
        "provisional_status": provisional_status,
        "architecture": "短路保护器件＋接触器＋独立过载继电器",
        "long_delay_mode": "OFF",
        "overload_protection_device": "独立热过载继电器",
        "short_delay_i2t_mode": "OFF",
        "short_delay_multiplier": multiplier,
        "short_delay_pickup_nominal_a": round(pickup_a, 6),
        "short_delay_setting_s": shortest_delay_s,
        "short_delay_maximum_breaking_time_s": maximum_breaking_time_s,
        "starting_current_a": round(motor_starting_current_a, 6),
        "starting_nominal_ride_through_status": starting_nominal_status,
        "terminal_minimum_fault_current_a": round(
            terminal_minimum_fault_current_a, 6
        ),
        "terminal_nominal_pickup_status": terminal_nominal_pickup_status,
        "governing_maximum_clearing_time_s": round(governing_time_s, 9),
        "nominal_thermal_time_status": nominal_thermal_status,
        "short_delay_pickup_guarantee_status": UNKNOWN,
        "thermal_clearing_formal_status": UNKNOWN,
        "conductor_ampacity_status": ampacity_status,
        "system_voltage_status": voltage_status,
        "required_icu_ka": round(required_icu_ka, 6),
        "icu_status": icu_status,
        "type_2_coordination_status": UNKNOWN,
        "applicability": (
            "采用CDM3E长延时OFF、I²t OFF短延时及独立热继电器路线。"
            f"按标称Isd={multiplier}×{base_a:g}={pickup_a:g}A暂算，"
            f"tsd={shortest_delay_s:g}s时样本最大断开时间"
            f"{maximum_breaking_time_s:g}s；标称热稳定校核{nominal_thermal_status}。"
            "样本未给短延时拾取允差，也未给该器件与接触器/热继电器的"
            "1类或2类配合表，因此正式状态仍为无法判断。"
        ),
        "source": data["source"],
    }
