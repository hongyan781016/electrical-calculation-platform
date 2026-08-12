"""Exact product-reference lookup without turning a sample into a generic rule."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .engine import FAIL, PASS, UNKNOWN


DEFAULT_CVS_CATALOG = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "schneider-easypact-cvs-protection.json"
)
DEFAULT_CVS_I2T_CURVES = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "schneider-easypact-cvs-i2t-curves.json"
)
DEFAULT_CVS_TVS_TYPE1 = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "schneider-easypact-tvs-type1-motor-coordination.json"
)
DEFAULT_TESYS_GV2_SMALL_MOTOR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "references"
    / "extracted"
    / "schneider-tesys-gv2-small-motor.json"
)


def load_easypact_cvs_catalog(
    path: Path | None = None,
) -> dict[str, Any]:
    return json.loads(
        (path or DEFAULT_CVS_CATALOG).read_text(encoding="utf-8")
    )


def load_easypact_cvs_i2t_curves(
    path: Path | None = None,
) -> dict[str, Any]:
    return json.loads(
        (path or DEFAULT_CVS_I2T_CURVES).read_text(encoding="utf-8")
    )


def load_easypact_type1_motor_coordination(
    path: Path | None = None,
) -> dict[str, Any]:
    return json.loads((path or DEFAULT_CVS_TVS_TYPE1).read_text(encoding="utf-8"))


def load_tesys_gv2_small_motor_catalog(
    path: Path | None = None,
) -> dict[str, Any]:
    return json.loads(
        (path or DEFAULT_TESYS_GV2_SMALL_MOTOR).read_text(encoding="utf-8")
    )


def select_tesys_gv2_small_motor_reference(
    *,
    motor_power_kw: float,
    motor_rated_current_a: float,
    motor_starting_current_a: float,
    system_voltage_v: float,
    required_icu_ka: float,
    terminal_fault_current_a: float,
    phase_permitted_i2t_a2s: float,
    pe_permitted_i2t_a2s: float,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the verified 0.12-0.25 kW TeSys GV2 Type-2 rows."""

    data = catalog or load_tesys_gv2_small_motor_catalog()
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "formal_calculation_allowed": bool(data.get("formal_calculation_allowed")),
        "source": data.get("source", {}),
    }
    power = float(motor_power_kw)
    rated = float(motor_rated_current_a)
    starting = float(motor_starting_current_a)
    voltage = float(system_voltage_v)
    required_icu = float(required_icu_ka)
    terminal_fault = float(terminal_fault_current_a)
    phase_limit = float(phase_permitted_i2t_a2s)
    pe_limit = float(pe_permitted_i2t_a2s)
    conditions = data["conditions"]
    if not conditions["system_voltage_min_v"] <= voltage <= conditions["system_voltage_max_v"]:
        result["reason"] = "该GV2成套配合表只覆盖380～415V应用电压档。"
        return result
    row = next(
        (item for item in data["rows"] if float(item["power_kw"]) == power),
        None,
    )
    if row is None:
        result["reason"] = "GV2小功率成套配合表没有该功率精确行；不插值。"
        return result

    current_status = (
        PASS if float(row["setting_min_a"]) <= rated <= float(row["setting_max_a"])
        else FAIL
    )
    starting_status = (
        PASS if starting < float(row["magnetic_trip_a"]) else FAIL
    )
    icu_status = (
        PASS if required_icu <= float(conditions["coordination_iq_ka"]) else FAIL
    )
    terminal_status = (
        PASS if terminal_fault >= float(row["magnetic_trip_upper_tolerance_a"])
        else FAIL
    )
    phase_i2t = _interpolate_log_curve(
        data["phase_i2t_upper_envelope"]["points"], required_icu
    )
    # The manufacturer curve is raster-only.  A visually digitised upper
    # envelope must not approve a conductor when the apparent margin is within
    # the reading uncertainty.  Keep at least 5% headroom for a PASS.
    phase_margin_ratio = (
        phase_limit / phase_i2t - 1.0
        if phase_i2t is not None and phase_i2t > 0
        else None
    )
    phase_status = (
        FAIL
        if phase_i2t is not None and phase_i2t > phase_limit
        else PASS
        if phase_margin_ratio is not None and phase_margin_ratio >= 0.05
        else UNKNOWN
    )
    pe_i2t = (
        terminal_fault**2 * float(conditions["pe_fault_maximum_clearing_time_s"])
        if terminal_status == PASS
        else None
    )
    pe_status = (
        PASS if pe_i2t is not None and pe_i2t <= pe_limit
        else FAIL if pe_i2t is not None
        else UNKNOWN
    )
    checks = [
        current_status,
        starting_status,
        icu_status,
        terminal_status,
        phase_status,
        pe_status,
    ]
    provisional = (
        FAIL if FAIL in checks else PASS if all(value == PASS for value in checks) else UNKNOWN
    )
    result.update(
        {
            "provisional_status": provisional,
            "manufacturer": data["manufacturer"],
            "coordination_type": conditions["coordination_type"],
            "coordination_iq_ka": float(conditions["coordination_iq_ka"]),
            "motor_power_kw": power,
            "breaker_model": row["breaker"],
            "setting_min_a": float(row["setting_min_a"]),
            "setting_max_a": float(row["setting_max_a"]),
            "setting_target_a": rated,
            "magnetic_trip_a": float(row["magnetic_trip_a"]),
            "magnetic_trip_upper_tolerance_a": float(
                row["magnetic_trip_upper_tolerance_a"]
            ),
            "contactor_model": conditions["contactor"],
            "motor_current_status": current_status,
            "starting_ride_through_status": starting_status,
            "icu_status": icu_status,
            "terminal_magnetic_trip_status": terminal_status,
            "phase_conservative_i2t_a2s": phase_i2t,
            "phase_permitted_i2t_a2s": phase_limit,
            "phase_i2t_margin_percent": (
                round(phase_margin_ratio * 100.0, 3)
                if phase_margin_ratio is not None
                else None
            ),
            "phase_thermal_status": phase_status,
            "pe_conservative_clearing_time_s": float(
                conditions["pe_fault_maximum_clearing_time_s"]
            ),
            "pe_conservative_i2t_a2s": round(pe_i2t, 3) if pe_i2t is not None else None,
            "pe_permitted_i2t_a2s": pe_limit,
            "pe_thermal_status": pe_status,
            "reason": (
                "按制造商400/415V直接启动2类配合精确行选取；380V按既定同一应用电压档处理。"
                "相导体采用GV2ME热限制图的保守上包络，PE按制造商相—PE回路0.4s表列边界复核。"
            ),
        }
    )
    return result


def select_easypact_type1_motor_reference(
    *,
    motor_power_kw: float,
    motor_rated_current_a: float,
    motor_starting_current_a: float,
    system_voltage_v: float,
    required_icu_ka: float,
    terminal_fault_current_a: float,
    phase_permitted_i2t_a2s: float,
    pe_permitted_i2t_a2s: float,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one exact 380–415 V Type-1 DOL combination and its cable checks."""

    data = catalog or load_easypact_type1_motor_coordination()
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "formal_calculation_allowed": bool(data.get("formal_calculation_allowed")),
        "source": data.get("source", {}),
    }
    try:
        power = float(motor_power_kw)
        rated = float(motor_rated_current_a)
        starting = float(motor_starting_current_a)
        voltage = float(system_voltage_v)
        required_icu = float(required_icu_ka)
        terminal_fault = float(terminal_fault_current_a)
        phase_limit = float(phase_permitted_i2t_a2s)
        pe_limit = float(pe_permitted_i2t_a2s)
    except (TypeError, ValueError):
        result["reason"] = "电动机、短路电流和导体允许热应力必须为有效数值。"
        return result
    if not 380 <= voltage <= 415:
        result["reason"] = "该制造商1类配合表只覆盖380～415V。"
        return result
    row = next((item for item in data.get("rows", []) if float(item["power_kw"]) == power), None)
    if row is None:
        result["reason"] = "制造商1类配合表没有该功率的精确行；不插值。"
        return result

    base = load_easypact_cvs_catalog()
    frame_code = str(row["breaker"]).split("-")[0]
    frame = next((item for item in base["frames"] if item["frame_code"] == frame_code), None)
    if frame is None:
        result["reason"] = "配合表断路器框架未接入EasyPact CVS分断能力表。"
        return result
    level = next(
        ((code, float(icu)) for code, icu in frame["breaking_levels_ka"].items() if float(icu) >= required_icu),
        None,
    )
    if level is None:
        result["reason"] = "安装点短路电流超过该框架已核实的最大Icu。"
        result["icu_status"] = FAIL
        return result
    level_code, icu = level
    overload_min, overload_max = (float(v) for v in row["overload_range_a"])
    table_current = float(
        row["motor_current_380_a"]
        if voltage <= 400
        else row["motor_current_415_a"]
    )
    # 功率模式尚无实物铭牌时，制造商配合表本身给出的电动机电流是该
    # 成套组合的整定依据；计算电流只用于校核，不以小数差异否决表列组合。
    relay_setting_target = (
        rated if overload_min <= rated <= overload_max else table_current
    )
    current_status = PASS if rated <= float(row["maximum_operational_current_a"]) else FAIL
    relay_status = PASS if overload_min <= relay_setting_target <= overload_max else FAIL
    starting_status = PASS if starting < float(row["magnetic_setting_a"]) else FAIL
    terminal_status = PASS if terminal_fault > float(row["magnetic_setting_a"]) else FAIL

    selected = {
        "frame_code": frame_code,
        "performance_level": level_code,
        "icu_ka": icu,
    }
    phase = evaluate_easypact_cvs_phase_thermal_reference(
        selected, required_icu, phase_limit
    )
    phase_status = phase.get("provisional_status", UNKNOWN)
    pe_i2t = terminal_fault**2 * 0.01 if terminal_status == PASS else None
    pe_status = (
        PASS if pe_i2t is not None and pe_i2t <= pe_limit
        else FAIL if pe_i2t is not None
        else UNKNOWN
    )
    checks = [current_status, relay_status, starting_status, terminal_status, phase_status, pe_status]
    provisional = FAIL if FAIL in checks else PASS if all(v == PASS for v in checks) else UNKNOWN
    result.update({
        "provisional_status": provisional,
        "manufacturer": data["manufacturer"],
        "coordination_type": 1,
        "motor_power_kw": power,
        "breaker_model": row["breaker"],
        "frame_code": frame_code,
        "frame_rating_a": float(frame["frame_rating_a"]),
        "ma_rating_a": float(row["ma_rating_a"]),
        "performance_level": level_code,
        "icu_ka": icu,
        "ics_ka": icu,
        "magnetic_setting_multiple": row["magnetic_setting_multiple"],
        "magnetic_setting_a": float(row["magnetic_setting_a"]),
        "contactor_model": row["contactor"],
        "overload_relay_model": row["overload_relay"],
        "overload_range_a": [overload_min, overload_max],
        "overload_setting_target_a": relay_setting_target,
        "coordination_table_motor_current_a": table_current,
        "motor_current_status": current_status,
        "overload_range_status": relay_status,
        "starting_ride_through_status": starting_status,
        "terminal_magnetic_trip_status": terminal_status,
        "phase_thermal": phase,
        "phase_thermal_status": phase_status,
        "pe_conservative_clearing_time_s": 0.01 if terminal_status == PASS else None,
        "pe_conservative_i2t_a2s": round(pe_i2t, 3) if pe_i2t is not None else None,
        "pe_permitted_i2t_a2s": pe_limit,
        "pe_thermal_status": pe_status,
        "reason": (
            "按制造商380～415V直接启动1类配合表精确行选取；相导体采用D-11限流I²t，"
            "末端相—PE故障超过Irm时按D-9给出的t<10ms作保守暂算。"
        ),
    })
    return result


def select_easypact_ma_motor_reference(
    *,
    motor_rated_current_a: float,
    motor_starting_current_a: float,
    system_voltage_v: float,
    required_icu_ka: float,
    terminal_fault_current_a: float,
    phase_permitted_i2t_a2s: float,
    pe_permitted_i2t_a2s: float,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select an exact MA rating where no exact manufacturer starter row exists."""

    data = catalog or load_easypact_cvs_catalog()
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "coordination_type": None,
        "formal_calculation_allowed": False,
    }
    rated = float(motor_rated_current_a)
    starting = float(motor_starting_current_a)
    voltage = float(system_voltage_v)
    required_icu = float(required_icu_ka)
    terminal_fault = float(terminal_fault_current_a)
    if rated <= 0 or starting <= 0 or not 380 <= voltage <= 415:
        result["reason"] = "MA电动机短路保护参考只处理380～415V且电流大于0的回路。"
        return result
    ma_row = next(
        (
            item for item in data["ma_motor"]["ratings"]
            if float(item["rating_a"]) >= rated
            and float(item["setting_multiple"][1]) * float(item["rating_a"])
            >= 1.2 * starting
        ),
        None,
    )
    if ma_row is None:
        result["reason"] = "已核实MA额定档和整定范围不能避开该启动电流。"
        return result
    frame = next(item for item in data["frames"] if item["frame_code"] == ma_row["frame_code"])
    level = next(
        ((code, float(icu)) for code, icu in frame["breaking_levels_ka"].items() if float(icu) >= required_icu),
        None,
    )
    if level is None:
        result["reason"] = "安装点短路电流超过该MA框架已核实的Icu。"
        return result
    level_code, icu = level
    ma_rating = float(ma_row["rating_a"])
    minimum_setting = float(ma_row["setting_multiple"][0]) * ma_rating
    maximum_setting = float(ma_row["setting_multiple"][1]) * ma_rating
    setting = max(minimum_setting, 1.2 * starting)
    terminal_status = PASS if terminal_fault > setting else FAIL
    selected = {"frame_code": ma_row["frame_code"], "performance_level": level_code, "icu_ka": icu}
    phase = evaluate_easypact_cvs_phase_thermal_reference(
        selected, required_icu, float(phase_permitted_i2t_a2s)
    )
    phase_status = phase.get("provisional_status", UNKNOWN)
    pe_i2t = terminal_fault**2 * float(data["ma_motor"]["instantaneous_total_break_time_s"]) if terminal_status == PASS else None
    pe_status = PASS if pe_i2t is not None and pe_i2t <= float(pe_permitted_i2t_a2s) else FAIL if pe_i2t is not None else UNKNOWN
    checks = [terminal_status, phase_status, pe_status]
    provisional = FAIL if FAIL in checks else PASS if all(v == PASS for v in checks) else UNKNOWN
    result.update({
        "provisional_status": provisional,
        "manufacturer": data["manufacturer"],
        "breaker_model": f"{ma_row['frame_code']}-MA",
        "frame_code": ma_row["frame_code"],
        "frame_rating_a": float(frame["frame_rating_a"]),
        "ma_rating_a": ma_rating,
        "performance_level": level_code,
        "icu_ka": icu,
        "ics_ka": icu,
        "magnetic_setting_multiple": ma_row["setting_multiple"],
        "magnetic_setting_a": round(setting, 3),
        "terminal_magnetic_trip_status": terminal_status,
        "phase_thermal": phase,
        "phase_thermal_status": phase_status,
        "pe_conservative_clearing_time_s": data["ma_motor"]["instantaneous_total_break_time_s"],
        "pe_conservative_i2t_a2s": round(pe_i2t, 3) if pe_i2t is not None else None,
        "pe_thermal_status": pe_status,
        "source_document": data["source"]["document"],
        "source_references": [data["source"]["ma_reference"], data["source"]["ma_time_current_reference"], data["source"]["energy_limiting_reference"]],
        "reason": "采用EasyPact CVS MA精确额定档和可调整磁脱扣范围；该路线没有制造商成套配合表，接触器和热继电器须单独校核。",
    })
    return result


def _interpolate_log_curve(
    points: list[dict[str, float]], prospective_current_ka: float
) -> float | None:
    if not points:
        return None
    current = float(prospective_current_ka)
    if current < points[0]["prospective_current_ka"] or current > points[-1][
        "prospective_current_ka"
    ]:
        return None
    for left, right in zip(points, points[1:]):
        x0 = float(left["prospective_current_ka"])
        x1 = float(right["prospective_current_ka"])
        if x0 <= current <= x1:
            if x1 == x0:
                return max(
                    float(left["conservative_i2t_a2s"]),
                    float(right["conservative_i2t_a2s"]),
                )
            fraction = (math.log10(current) - math.log10(x0)) / (
                math.log10(x1) - math.log10(x0)
            )
            log_i2t = math.log10(float(left["conservative_i2t_a2s"])) + fraction * (
                math.log10(float(right["conservative_i2t_a2s"]))
                - math.log10(float(left["conservative_i2t_a2s"]))
            )
            return 10**log_i2t
    return None


def evaluate_easypact_cvs_phase_thermal_reference(
    selected_product: dict[str, Any],
    prospective_short_circuit_current_ka: float,
    permitted_cable_i2t_a2s: float,
    *,
    curves: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare a selected exact CVS reference curve with cable k²S².

    The graph-derived result is deliberately provisional: it can replace a
    guessed clearing time, but cannot turn an unapproved catalogue record into
    a formal conclusion.
    """

    result: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "formal_calculation_allowed": False,
    }
    try:
        current = float(prospective_short_circuit_current_ka)
        permitted = float(permitted_cable_i2t_a2s)
    except (TypeError, ValueError):
        result["reason"] = "预期短路电流和电缆允许热应力必须为有效数值。"
        return result
    if current <= 0 or permitted <= 0:
        result["reason"] = "预期短路电流和电缆允许热应力必须大于0。"
        return result
    frame_code = selected_product.get("frame_code")
    selected_icu = selected_product.get("icu_ka")
    if not frame_code or selected_icu is None:
        result["reason"] = "须先形成包含框架和Icu的精确CVS产品参考候选。"
        return result
    if current > float(selected_icu):
        result.update(
            {
                "provisional_status": FAIL,
                "prospective_short_circuit_current_ka": current,
                "selected_icu_ka": float(selected_icu),
                "reason": "安装点预期短路电流超过所选产品Icu，不能继续采用该产品曲线。",
            }
        )
        return result

    curve_data = curves or load_easypact_cvs_i2t_curves()
    curve = next(
        (
            item
            for item in curve_data.get("curves", [])
            if frame_code in item.get("applicable_frames", [])
        ),
        None,
    )
    if curve is None:
        result["reason"] = "该框架没有已核验的D-11矢量I²t曲线。"
        return result
    let_through = _interpolate_log_curve(curve["points"], current)
    if let_through is None:
        result.update(
            {
                "prospective_short_circuit_current_ka": current,
                "curve_current_range_ka": [
                    curve["points"][0]["prospective_current_ka"],
                    curve["points"][-1]["prospective_current_ka"],
                ],
                "reason": "预期短路电流超出图线绘制范围；不外推I²t。",
            }
        )
        return result

    provisional = PASS if let_through <= permitted else FAIL
    result.update(
        {
            "provisional_status": provisional,
            "frame_code": frame_code,
            "performance_level": selected_product.get("performance_level"),
            "selected_icu_ka": float(selected_icu),
            "prospective_short_circuit_current_ka": current,
            "breaker_conservative_let_through_i2t_a2s": round(let_through, 3),
            "permitted_cable_i2t_a2s": permitted,
            "curve_current_range_ka": [
                curve["points"][0]["prospective_current_ka"],
                curve["points"][-1]["prospective_current_ka"],
            ],
            "curve_index": curve["curve_index"],
            "source_document": curve_data["source"]["document"],
            "source_reference": (
                f"PDF第{curve_data['source']['pdf_page']}页，"
                f"{curve_data['source']['printed_page']}"
            ),
            "evidence_status": "verified_vector_digitization",
            "reason": (
                "已按所选CVS框架的D-11保守上包络I²t暂算复核；"
                "产品资料尚未批准，正式状态仍为无法判断。"
            ),
        }
    )
    return result


def select_easypact_cvs_reference(
    rated_current_a: float,
    required_icu_ka: float,
    *,
    system_voltage_v: float = 400,
    trip_unit_family: str = "TM-D",
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a traceable CVS reference candidate for one exact current rating.

    This is not a generic breaker selector. It only resolves a declared
    manufacturer series against visually verified catalogue rows. The result
    remains non-formal while the source record is not approved.
    """

    data = catalog or load_easypact_cvs_catalog()
    result: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "selection_kind": "manufacturer_reference_candidate",
        "catalog_code": data.get("catalog_code"),
    }
    try:
        current = float(rated_current_a)
        required_icu = float(required_icu_ka)
        voltage = float(system_voltage_v)
    except (TypeError, ValueError):
        result["reason"] = "额定电流、所需Icu和系统电压必须为有效数值。"
        return result
    if current <= 0 or required_icu <= 0 or voltage <= 0:
        result["reason"] = "额定电流、所需Icu和系统电压必须大于0。"
        return result
    low_voltage, high_voltage = data["voltage_range_v"]
    if not low_voltage <= voltage <= high_voltage:
        result["reason"] = "本产品表列Icu仅核验了380/415V范围。"
        return result
    if trip_unit_family not in {"TM-D", "ETS"}:
        result["reason"] = "当前只录入配电保护TM-D和ETS，不套用电动机保护MA脱扣器。"
        return result

    frame = next(
        (
            item
            for item in data["frames"]
            if current in [float(value) for value in item["rated_currents_a"]]
            and (
                trip_unit_family != "ETS"
                or current in [float(value) for value in data["ets"]["rated_currents_a"]]
            )
        ),
        None,
    )
    if frame is None:
        result["reason"] = "所选额定电流与脱扣器组合不在已核验样本表列范围内；不插值。"
        return result
    breaking_level = next(
        (
            (code, float(icu))
            for code, icu in frame["breaking_levels_ka"].items()
            if float(icu) >= required_icu
        ),
        None,
    )
    if breaking_level is None:
        result["reason"] = "所需Icu超过该框架在380/415V下的已核验表列范围。"
        return result

    level_code, selected_icu = breaking_level
    configuration: dict[str, Any]
    current_key = f"{current:g}"
    if trip_unit_family == "ETS":
        pickup = data["ets"]["instantaneous_pickup_a"].get(current_key)
        if pickup is None:
            result["reason"] = "该额定电流没有已核验的ETS瞬时脱扣值。"
            return result
        configuration = {
            "instantaneous_pickup_a": float(pickup),
            "instantaneous_non_tripping_time_ms": data["ets"][
                "instantaneous_non_tripping_time_ms"
            ],
            "instantaneous_maximum_break_time_ms": data["ets"][
                "instantaneous_maximum_break_time_ms"
            ],
            "maximum_break_time_condition": data["ets"][
                "maximum_break_time_condition"
            ],
        }
        trip_reference = data["source"]["ets_reference"]
    else:
        fixed = frame.get("tm_d_fixed_instantaneous_pickup_a", {}).get(
            current_key
        )
        adjustable = frame.get(
            "tm_d_adjustable_instantaneous_pickup_a", {}
        ).get(current_key)
        if fixed is not None:
            configuration = {"instantaneous_pickup_a": float(fixed)}
        elif adjustable is not None:
            configuration = {
                "instantaneous_pickup_range_a": [
                    float(value) for value in adjustable
                ]
            }
        else:
            result["reason"] = "该额定电流没有已核验的TM-D瞬时脱扣值。"
            return result
        trip_reference = data["source"]["tm_d_reference"]

    result.update(
        {
            "manufacturer": data["manufacturer"],
            "series": data["series"],
            "frame_code": frame["frame_code"],
            "frame_rating_a": float(frame["frame_rating_a"]),
            "rated_current_a": current,
            "performance_level": level_code,
            "icu_ka": selected_icu,
            "ics_ka": selected_icu,
            "system_voltage_v": voltage,
            "trip_unit_family": trip_unit_family,
            "trip_configuration": configuration,
            "source_document": data["source"]["document"],
            "characteristics_reference": data["source"][
                "characteristics_reference"
            ],
            "trip_reference": trip_reference,
            "energy_limiting_curve": data["energy_limiting_curve"],
            "evidence_status": data["status"],
            "formal_calculation_allowed": data[
                "formal_calculation_allowed"
            ],
            "reason": (
                "已形成精确产品参考候选；D-11限流I²t矢量图线可用于"
                "电缆热稳定暂算复核，正式结论仍须批准资料。"
            ),
        }
    )
    return result
