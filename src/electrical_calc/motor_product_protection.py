"""电动机保护器件的精确产品参考，不把样本扩展成通用规则。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import FAIL, PASS, UNKNOWN


DEFAULT_CM3_MOTOR_CATALOG = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "cm3-motor-protection.json"
)
DEFAULT_CM3_MOTOR_CURVE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "cm3-63lm-motor-time-current-curve.json"
)


def load_cm3_motor_catalog(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or DEFAULT_CM3_MOTOR_CATALOG).read_text(encoding="utf-8"))


def load_cm3_motor_curve(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or DEFAULT_CM3_MOTOR_CURVE).read_text(encoding="utf-8"))


def _conservative_curve_bounds(
    current_multiple: float,
    curve: dict[str, Any],
) -> dict[str, float] | None:
    points = curve.get("points", [])
    if not points:
        return None
    if not (
        float(points[0]["current_multiple_in"])
        <= current_multiple
        <= float(points[-1]["current_multiple_in"])
    ):
        return None
    lower_current_point = max(
        (
            point
            for point in points
            if float(point["current_multiple_in"]) <= current_multiple
        ),
        key=lambda point: float(point["current_multiple_in"]),
    )
    higher_current_point = min(
        (
            point
            for point in points
            if float(point["current_multiple_in"]) >= current_multiple
        ),
        key=lambda point: float(point["current_multiple_in"]),
    )
    return {
        "minimum_trip_time_s": float(higher_current_point["minimum_trip_time_s"]),
        "maximum_trip_time_s": float(lower_current_point["maximum_trip_time_s"]),
    }


def _time_check(bounds: dict[str, float] | None, limit_s: float) -> str:
    if bounds is None:
        return UNKNOWN
    if bounds["maximum_trip_time_s"] <= limit_s:
        return PASS
    if bounds["minimum_trip_time_s"] > limit_s:
        return FAIL
    return UNKNOWN


def _curve_for_rating(curve_data: dict[str, Any], rating: float) -> dict[str, Any] | None:
    """按样本明确标注的In档选择曲线，不跨档套用。"""

    if "curves" not in curve_data:
        return curve_data
    return next(
        (
            item
            for item in curve_data["curves"]
            if float(item["rated_current_band_a"][0])
            <= rating
            <= float(item["rated_current_band_a"][1])
        ),
        None,
    )


def evaluate_cm3_motor_reference(
    *,
    motor_rated_current_a: float,
    motor_starting_current_a: float,
    conductor_corrected_ampacity_a: float,
    required_icu_ka: float,
    terminal_minimum_fault_current_a: float,
    phase_maximum_clearing_time_s: float,
    pe_maximum_clearing_time_s: float,
    motor_starting_time_s: float | None = None,
    system_voltage_v: float = 380,
    catalog: dict[str, Any] | None = None,
    curve: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按CM3-63L/M电动机型已核验表列数据形成参考候选。"""

    data = catalog or load_cm3_motor_catalog()
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "selection_kind": "manufacturer_motor_reference_candidate",
        "catalog_code": data.get("catalog_code"),
    }
    values = (
        motor_rated_current_a,
        motor_starting_current_a,
        conductor_corrected_ampacity_a,
        required_icu_ka,
        terminal_minimum_fault_current_a,
        phase_maximum_clearing_time_s,
        pe_maximum_clearing_time_s,
        system_voltage_v,
    )
    if any(not isinstance(value, (int, float)) or value <= 0 for value in values):
        result["reason"] = "电流、载流量、Icu、切除时间和系统电压必须为正数。"
        return result
    if motor_starting_time_s is not None and motor_starting_time_s <= 0:
        result["reason"] = "实际启动时间必须大于0或留空。"
        return result
    if system_voltage_v > float(data["system_voltage_v"]):
        result["reason"] = "系统电压超过当前产品参考表列电压。"
        return result

    ratings = sorted(
        {
            float(rating)
            for frame in data["frames"]
            for rating in frame["rated_currents_a"]
        }
    )
    rating = next(
        (
            value
            for value in ratings
            if motor_rated_current_a <= value <= conductor_corrected_ampacity_a
        ),
        None,
    )
    if rating is None:
        result["reason"] = "没有同时满足电动机额定电流和电缆载流量的表列额定电流。"
        return result
    frame = next(
        (
            item
            for item in data["frames"]
            if rating in [float(value) for value in item["rated_currents_a"]]
            and float(item["icu_ka"]) >= required_icu_ka
        ),
        None,
    )
    if frame is None:
        result["reason"] = "CM3-63L/M表列Icu不能满足安装点最大短路电流。"
        return result

    protection = data["motor_protection"]
    overload = protection["overload_characteristic"]
    current_deviation_percent = (rating / motor_rated_current_a - 1) * 100
    overload_not_below_status = PASS
    overload_closeness_status = (
        PASS if abs(rating - motor_rated_current_a) <= 1e-9 else UNKNOWN
    )
    product_guaranteed_hot_trip_current_a = (
        float(overload["hot_trip_current_multiple_in"]) * rating
    )
    motor_120_percent_current_a = 1.2 * motor_rated_current_a
    motor_120_percent_trip_guarantee_status = (
        PASS
        if motor_120_percent_current_a >= product_guaranteed_hot_trip_current_a
        else UNKNOWN
    )
    motor_overload_characteristic_match_status = UNKNOWN
    pickup_rule = next(
        (
            item
            for item in protection["short_circuit_pickup_rules"]
            if float(item["rated_current_band_a"][0])
            <= rating
            <= float(item["rated_current_band_a"][1])
        ),
        None,
    )
    if pickup_rule is None:
        result["reason"] = "当前额定电流没有对应的短路保护整定表列规则。"
        return result
    nominal_pickup = (
        float(pickup_rule["fixed_pickup_a"])
        if "fixed_pickup_a" in pickup_rule
        else float(pickup_rule["pickup_multiplier"]) * rating
    )
    tolerance = float(protection["pickup_tolerance_percent"]) / 100
    pickup_min = nominal_pickup * (1 - tolerance)
    pickup_max = nominal_pickup * (1 + tolerance)
    start_instantaneous_status = PASS if motor_starting_current_a < pickup_min else FAIL
    if terminal_minimum_fault_current_a >= pickup_max:
        terminal_instantaneous_status = PASS
    elif terminal_minimum_fault_current_a < pickup_min:
        terminal_instantaneous_status = FAIL
    else:
        terminal_instantaneous_status = UNKNOWN

    curve_data = curve or load_cm3_motor_curve()
    selected_curve = _curve_for_rating(curve_data, rating)
    curve_applicable = selected_curve is not None
    start_multiple = motor_starting_current_a / rating
    phase_multiple = required_icu_ka * 1000 / rating
    pe_multiple = terminal_minimum_fault_current_a / rating
    start_bounds = (
        _conservative_curve_bounds(start_multiple, selected_curve)
        if curve_applicable
        else None
    )
    phase_bounds = (
        _conservative_curve_bounds(phase_multiple, selected_curve)
        if curve_applicable
        else None
    )
    pe_bounds = (
        _conservative_curve_bounds(pe_multiple, selected_curve)
        if curve_applicable
        else None
    )
    if motor_starting_time_s is None or start_bounds is None:
        start_time_status = UNKNOWN
    elif motor_starting_time_s < start_bounds["minimum_trip_time_s"]:
        start_time_status = PASS
    elif motor_starting_time_s > start_bounds["maximum_trip_time_s"]:
        start_time_status = FAIL
    else:
        start_time_status = UNKNOWN
    phase_time_status = _time_check(phase_bounds, phase_maximum_clearing_time_s)
    pe_time_status = _time_check(pe_bounds, pe_maximum_clearing_time_s)
    if FAIL in {phase_time_status, pe_time_status}:
        fault_time_status = FAIL
    elif phase_time_status == pe_time_status == PASS:
        fault_time_status = PASS
    else:
        fault_time_status = UNKNOWN

    result.update(
        {
            "manufacturer": data["manufacturer"],
            "series": data["series"],
            "frame_code": frame["frame_code"],
            "frame_rating_a": float(frame["frame_rating_a"]),
            "rated_current_a": rating,
            "poles": frame["poles"],
            "icu_ka": float(frame["icu_ka"]),
            "ics_ka": float(frame["ics_ka"]),
            "trip_class": protection["trip_class"],
            "overload_reference_current_a": rating,
            "overload_current_deviation_percent": round(
                current_deviation_percent, 6
            ),
            "overload_setting_not_below_motor_status": overload_not_below_status,
            "overload_setting_closeness_status": overload_closeness_status,
            "cold_no_trip_current_a": round(
                float(overload["cold_no_trip_current_multiple_in"]) * rating, 6
            ),
            "cold_no_trip_duration_h": float(overload["cold_no_trip_duration_h"]),
            "hot_trip_current_a": round(product_guaranteed_hot_trip_current_a, 6),
            "hot_trip_max_duration_h": float(overload["hot_trip_max_duration_h"]),
            "motor_120_percent_current_a": round(motor_120_percent_current_a, 6),
            "motor_120_percent_trip_guarantee_status": motor_120_percent_trip_guarantee_status,
            "motor_overload_characteristic_match_status": motor_overload_characteristic_match_status,
            "short_circuit_pickup_nominal_a": round(nominal_pickup, 6),
            "short_circuit_pickup_rule": (
                "fixed"
                if "fixed_pickup_a" in pickup_rule
                else "multiple_of_in"
            ),
            "short_circuit_pickup_min_a": round(pickup_min, 6),
            "short_circuit_pickup_max_a": round(pickup_max, 6),
            "starting_instantaneous_ride_through_status": start_instantaneous_status,
            "terminal_instantaneous_trip_status": terminal_instantaneous_status,
            "motor_starting_current_multiple_in": round(start_multiple, 6),
            "motor_starting_time_s": motor_starting_time_s,
            "motor_starting_curve_bounds": start_bounds,
            "motor_starting_time_check": start_time_status,
            "phase_fault_current_multiple_in": round(phase_multiple, 6),
            "phase_fault_curve_bounds": phase_bounds,
            "phase_maximum_clearing_time_s": phase_maximum_clearing_time_s,
            "phase_fault_clearing_time_check": phase_time_status,
            "pe_fault_current_multiple_in": round(pe_multiple, 6),
            "pe_fault_curve_bounds": pe_bounds,
            "pe_maximum_clearing_time_s": pe_maximum_clearing_time_s,
            "pe_fault_clearing_time_check": pe_time_status,
            "fault_clearing_time_check": fault_time_status,
            "terminal_minimum_fault_current_a": terminal_minimum_fault_current_a,
            "curve_digitized": protection["curve_digitized"],
            "curve_applicable_to_selected_rating": curve_applicable,
            "curve_id": selected_curve.get("curve_id") if selected_curve else None,
            "curve_evidence_status": protection["curve_digitization_status"],
            "curve_source": {
                **curve_data["source"],
                **(selected_curve.get("source", {}) if selected_curve else {}),
            },
            "source": data["source"],
            "formal_calculation_allowed": data["formal_calculation_allowed"],
            "reason": (
                "已按CM3电动机保护型表列数据暂算；C-15～C-17分别按In档选用曲线；"
                "固定In档与电动机过载特性是否匹配仍须电动机允许过载曲线证明；"
                "正式状态还受产品资料批准、实际启动时间及曲线图读数精度约束。"
            ),
        }
    )
    if FAIL in {start_instantaneous_status, start_time_status, fault_time_status}:
        result["provisional_status"] = FAIL
    elif (
        start_time_status == PASS
        and fault_time_status == PASS
        and motor_overload_characteristic_match_status == PASS
    ):
        result["provisional_status"] = PASS
    else:
        result["provisional_status"] = UNKNOWN
    return result
