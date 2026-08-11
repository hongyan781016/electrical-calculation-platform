"""电动机产品样本参考参数。

这些数据用于在用户只有额定输出功率时减少手工输入，不代表通用电动机
参数，也不构成品牌或型号推荐。只允许精确匹配，不插值、不跨极数套用。
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from .engine import Outcome, PASS, Step, UNKNOWN
from .motor import MotorCatalogQuery


ENGINE_VERSION = "0.2.0"
RULE_CODE = "MOTOR.PARAMETERS.SIEMENS.4P.400V.50HZ"

SOURCE = {
    "manufacturer": "Siemens",
    "document": "SIMOTICS Motors D 81.1",
    "edition": "09/2023",
    "catalog_page": "3/14",
    "pdf_page": 152,
    "series_code": "1LE1003",
    "efficiency_class": "IE3",
    "poles": 4,
    "frequency_hz": 50,
    "rated_voltage_v": 400,
    "status": "verified",
    "formal_calculation_allowed": False,
}

SOURCE_HIGH_POWER = {
    **SOURCE,
    "catalog_page": "3/20",
    "pdf_page": 158,
    "series_code": "1LE1503",
}

# 表中只保留当前计算需要的列：额定输出功率、满载效率、满载功率因数、
# 400 V额定电流和堵转电流/额定电流比。数值逐行来自目录页3/14。
_ROWS: dict[float, dict[str, float]] = {
    0.12: {"efficiency": 0.648, "power_factor": 0.68, "rated_current_a": 0.39, "locked_rotor_current_ratio": 3.6},
    0.18: {"efficiency": 0.699, "power_factor": 0.65, "rated_current_a": 0.57, "locked_rotor_current_ratio": 4.0},
    0.25: {"efficiency": 0.735, "power_factor": 0.72, "rated_current_a": 0.68, "locked_rotor_current_ratio": 4.2},
    0.37: {"efficiency": 0.773, "power_factor": 0.70, "rated_current_a": 0.99, "locked_rotor_current_ratio": 4.8},
    0.55: {"efficiency": 0.808, "power_factor": 0.78, "rated_current_a": 1.26, "locked_rotor_current_ratio": 5.9},
    0.75: {"efficiency": 0.825, "power_factor": 0.75, "rated_current_a": 1.75, "locked_rotor_current_ratio": 7.1},
    1.1: {"efficiency": 0.841, "power_factor": 0.78, "rated_current_a": 2.4, "locked_rotor_current_ratio": 6.9},
    1.5: {"efficiency": 0.853, "power_factor": 0.80, "rated_current_a": 3.15, "locked_rotor_current_ratio": 7.3},
    2.2: {"efficiency": 0.867, "power_factor": 0.82, "rated_current_a": 4.45, "locked_rotor_current_ratio": 8.3},
    3.0: {"efficiency": 0.877, "power_factor": 0.80, "rated_current_a": 6.2, "locked_rotor_current_ratio": 8.0},
    4.0: {"efficiency": 0.886, "power_factor": 0.82, "rated_current_a": 7.9, "locked_rotor_current_ratio": 7.1},
    5.5: {"efficiency": 0.896, "power_factor": 0.82, "rated_current_a": 10.8, "locked_rotor_current_ratio": 8.5},
    7.5: {"efficiency": 0.904, "power_factor": 0.80, "rated_current_a": 15.0, "locked_rotor_current_ratio": 8.5},
    11.0: {"efficiency": 0.914, "power_factor": 0.82, "rated_current_a": 21.0, "locked_rotor_current_ratio": 8.0},
    15.0: {"efficiency": 0.921, "power_factor": 0.83, "rated_current_a": 28.5, "locked_rotor_current_ratio": 7.9},
    18.5: {"efficiency": 0.926, "power_factor": 0.82, "rated_current_a": 35.0, "locked_rotor_current_ratio": 7.2},
    22.0: {"efficiency": 0.930, "power_factor": 0.83, "rated_current_a": 41.0, "locked_rotor_current_ratio": 6.8},
    30.0: {"efficiency": 0.936, "power_factor": 0.84, "rated_current_a": 55.0, "locked_rotor_current_ratio": 7.3},
    37.0: {"efficiency": 0.939, "power_factor": 0.86, "rated_current_a": 66.0, "locked_rotor_current_ratio": 6.4, "source": SOURCE_HIGH_POWER},
    45.0: {"efficiency": 0.942, "power_factor": 0.86, "rated_current_a": 80.0, "locked_rotor_current_ratio": 6.6, "source": SOURCE_HIGH_POWER},
    55.0: {"efficiency": 0.946, "power_factor": 0.87, "rated_current_a": 96.0, "locked_rotor_current_ratio": 6.8, "source": SOURCE_HIGH_POWER},
    75.0: {"efficiency": 0.950, "power_factor": 0.86, "rated_current_a": 133.0, "locked_rotor_current_ratio": 6.9, "source": SOURCE_HIGH_POWER},
    90.0: {"efficiency": 0.952, "power_factor": 0.87, "rated_current_a": 157.0, "locked_rotor_current_ratio": 7.2, "source": SOURCE_HIGH_POWER},
    110.0: {"efficiency": 0.954, "power_factor": 0.87, "rated_current_a": 191.0, "locked_rotor_current_ratio": 6.8, "source": SOURCE_HIGH_POWER},
    132.0: {"efficiency": 0.956, "power_factor": 0.87, "rated_current_a": 230.0, "locked_rotor_current_ratio": 7.3, "source": SOURCE_HIGH_POWER},
    160.0: {"efficiency": 0.958, "power_factor": 0.87, "rated_current_a": 275.0, "locked_rotor_current_ratio": 7.3, "source": SOURCE_HIGH_POWER},
    200.0: {"efficiency": 0.960, "power_factor": 0.88, "rated_current_a": 340.0, "locked_rotor_current_ratio": 7.4, "source": SOURCE_HIGH_POWER},
}

# 页面只把这些已经逐行核验的功率作为厂家目录建议项；目录外功率仍可
# 输入，但不能用相邻档位插值。
AVAILABLE_RATED_OUTPUT_POWERS_KW: tuple[float, ...] = tuple(_ROWS)

# 页面只把已经具备当前产品路线和完整网络复核路径的精确目录功率标为
# “完整选型”。0.37～200kW采用EasyPact制造商1类配合精确行；
# 0.12～0.25kW采用EasyPact MA短路保护与正泰控制器件的跨品牌暂选路线。
COMPLETE_SELECTION_POWERS_KW: tuple[float, ...] = AVAILABLE_RATED_OUTPUT_POWERS_KW


def resolve_motor_reference_parameters(
    query: MotorCatalogQuery,
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """精确取得一个产品系列的计算参考参数。"""

    outputs: dict[str, Any] = {
        "matched": False,
        "efficiency": None,
        "power_factor": None,
        "rated_current_a_at_catalog_voltage": None,
        "locked_rotor_current_ratio": None,
        "locked_rotor_power_factor": None,
        "source": SOURCE.copy(),
    }
    warnings: list[str] = []
    steps: list[Step] = []

    if not isfinite(query.rated_output_power_kw) or query.rated_output_power_kw <= 0:
        warnings.append("电动机额定输出功率必须大于0。")
    allowed_series = {SOURCE["series_code"], SOURCE_HIGH_POWER["series_code"]}
    if query.series_code.upper() not in allowed_series:
        warnings.append("当前参考目录不包含所选电动机系列。")
    if query.poles != SOURCE["poles"]:
        warnings.append("当前已核验参数只覆盖4极电动机，不能跨极数套用。")
    row = _ROWS.get(float(query.rated_output_power_kw))
    if row is None:
        warnings.append("产品样本中没有完全相同的额定功率；系统不插值、不套用相邻功率。")
    if warnings:
        return Outcome(
            "电动机样本参数",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            [RULE_CODE],
        )

    assert row is not None
    row_source = row.get("source", SOURCE)
    outputs.update(
        {
            "matched": True,
            "rated_output_power_kw": float(query.rated_output_power_kw),
            "efficiency": row["efficiency"],
            "power_factor": row["power_factor"],
            "rated_current_a_at_catalog_voltage": row["rated_current_a"],
            "locked_rotor_current_ratio": row["locked_rotor_current_ratio"],
            "locked_rotor_current_a_at_catalog_voltage": round(
                row["rated_current_a"] * row["locked_rotor_current_ratio"], 6
            ),
            "source": row_source.copy(),
        }
    )
    steps.extend(
        (
            Step("额定效率", "产品样本精确行", row["efficiency"] * 100, "%"),
            Step("额定功率因数", "产品样本精确行", row["power_factor"]),
            Step(
                "堵转电流倍数",
                "产品样本ILR/Irated",
                row["locked_rotor_current_ratio"],
            ),
        )
    )
    warnings.extend(
        (
            f"这是{row_source['series_code']}系列、4极、400 V、50 Hz产品的计算参考，不是通用电动机参数或购买推荐。",
            "该样本未给出堵转功率因数；启动网络R/X精确计算仍不能自动完成。",
            "正式复核应以拟购或现场电动机铭牌及厂家数据替换参考参数。",
        )
    )
    provisional_status = PASS
    status = provisional_status
    if rules.get(RULE_CODE, {}).get("status") != "approved":
        status = UNKNOWN
        warnings.append("该产品样本参数尚未批准进入正式计算。")
    return Outcome(
        "电动机样本参数",
        ENGINE_VERSION,
        status,
        provisional_status,
        outputs,
        steps,
        warnings,
        [RULE_CODE],
    )
