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
