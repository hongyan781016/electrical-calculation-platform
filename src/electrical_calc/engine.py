from __future__ import annotations

from dataclasses import asdict, dataclass
from math import log, pi, sqrt
from typing import Any, Iterable

from .catalog import (
    DEFAULT_CATALOG,
    lookup_transformer_positive_sequence_impedance,
    lookup_transformer_phase_pe_impedance,
    lookup_yjv_four_core_phase_pe_impedance,
    lookup_yjv_fault_loop_structure,
)


UNKNOWN = "无法判断"
PASS = "通过"
FAIL = "不通过"


# 待核实数据，只用于快速初选。来源为历史计算参考表中的
# “导线载流量”和“电线及电缆数据表”；原始参考文件不随公开仓库发布。
# 在规范条文、敷设条件和厂家参数批准前，不得作为正式设计依据。
PROVISIONAL_BREAKER_RATINGS_A = (16, 20, 25, 30, 40, 50, 63, 80, 100, 125, 140, 160, 200, 225, 250)
PROVISIONAL_YJV_AMPACITY_A = {
    "1": (
        (1.5, 16), (2.5, 35), (4, 38), (6, 55), (10, 75), (16, 108),
        (25, 140), (35, 175), (50, 210), (70, 265), (95, 330), (120, 410),
    ),
    "3": (
        (1.5, 18), (2.5, 22), (4, 34), (6, 40), (10, 55), (16, 75),
        (25, 100), (35, 130), (50, 160), (70, 210), (95, 260), (120, 300),
    ),
}


@dataclass(frozen=True)
class Step:
    label: str
    expression: str
    value: float | str | None
    unit: str = ""


@dataclass(frozen=True)
class Outcome:
    module: str
    version: str
    status: str
    provisional_status: str
    outputs: dict[str, Any]
    steps: list[Step]
    warnings: list[str]
    rule_codes: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [asdict(item) for item in self.steps]
        return data


def _number(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _approved(rule_codes: Iterable[str], rules: dict[str, dict[str, Any]]) -> bool:
    codes = list(rule_codes)
    return bool(codes) and all(rules.get(code, {}).get("status") == "approved" for code in codes)


def _final_status(
    provisional_status: str,
    rule_codes: list[str],
    rules: dict[str, dict[str, Any]],
    warnings: list[str],
) -> str:
    if provisional_status == UNKNOWN:
        return UNKNOWN
    if not _approved(rule_codes, rules):
        warnings.append("计算依据尚未全部批准；本结果仅用于算术核对，不能作为正式设计结论。")
        return UNKNOWN
    return provisional_status


def calculate_load_and_selection(
    data: dict[str, Any], rules: dict[str, dict[str, Any]]
) -> Outcome:
    rule_codes = ["ELEC.LOAD.CURRENT", "ELEC.CABLE.COORDINATION"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {}

    required = ["installed_power_kw", "demand_factor", "voltage_v", "power_factor", "efficiency"]
    missing = [key for key in required if _number(data, key) is None]
    phase = str(data.get("phase") or "")
    if phase not in {"1", "3"}:
        missing.append("phase")
    if missing:
        warnings.append("缺少必要输入：" + "、".join(dict.fromkeys(missing)))
        return Outcome("负荷与选型", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    installed = _number(data, "installed_power_kw")
    demand = _number(data, "demand_factor")
    voltage = _number(data, "voltage_v")
    power_factor = _number(data, "power_factor")
    efficiency = _number(data, "efficiency")
    assert None not in {installed, demand, voltage, power_factor, efficiency}

    if installed < 0 or not 0 < demand <= 1 or voltage <= 0 or not 0 < power_factor <= 1 or not 0 < efficiency <= 1:
        warnings.append("负荷、系数、电压、功率因数或效率超出本原型允许范围。")
        return Outcome("负荷与选型", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    demand_power = installed * demand
    denominator = (sqrt(3) if phase == "3" else 1) * voltage * power_factor * efficiency
    current = demand_power * 1000 / denominator
    steps.extend(
        [
            Step("计算有功功率", f"{installed:g} × {demand:g}", round(demand_power, 4), "kW"),
            Step(
                "计算电流",
                (
                    "P×1000/(√3×U×cosφ×η)"
                    if phase == "3"
                    else "P×1000/(U×cosφ×η)"
                ),
                round(current, 4),
                "A",
            ),
        ]
    )
    outputs.update({"demand_power_kw": round(demand_power, 4), "design_current_a": round(current, 4)})

    ampacity = _number(data, "cable_ampacity_a")
    breaker = _number(data, "breaker_rating_a")
    checks: list[bool] = []
    if ampacity is None:
        warnings.append("未提供经核实的电缆载流量，无法完成电缆校核。")
    else:
        cable_ok = current <= ampacity
        checks.append(cable_ok)
        outputs["cable_current_check"] = PASS if cable_ok else FAIL
        steps.append(Step("电缆电流校核", f"{current:.4f} ≤ {ampacity:g}", PASS if cable_ok else FAIL))

    if breaker is None:
        warnings.append("未提供保护器件额定电流，无法完成保护配合校核。")
    elif ampacity is None:
        breaker_ok = current <= breaker
        checks.append(breaker_ok)
        outputs["breaker_current_check"] = PASS if breaker_ok else FAIL
        steps.append(Step("保护器件电流校核", f"{current:.4f} ≤ {breaker:g}", PASS if breaker_ok else FAIL))
    else:
        breaker_ok = current <= breaker <= ampacity
        checks.append(breaker_ok)
        outputs["breaker_current_check"] = PASS if breaker_ok else FAIL
        steps.append(
            Step(
                "保护配合暂算",
                f"{current:.4f} ≤ {breaker:g} ≤ {ampacity:g}",
                PASS if breaker_ok else FAIL,
            )
        )

    provisional = UNKNOWN if not checks else (PASS if all(checks) else FAIL)
    status = _final_status(provisional, rule_codes, rules, warnings)
    return Outcome("负荷与选型", "0.1.0", status, provisional, outputs, steps, warnings, rule_codes)



def calculate_quick_selection(
    data: dict[str, Any], rules: dict[str, dict[str, Any]]
) -> Outcome:
    """Use a known design current to produce provisional cable and breaker candidates."""
    rule_codes = ["ELEC.CABLE.COORDINATION"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {}

    current = _number(data, "design_current_a")
    phase = str(data.get("phase") or "")
    missing: list[str] = []
    if current is None:
        missing.append("计算电流")
    if phase not in {"1", "3"}:
        missing.append("相制")
    if missing:
        warnings.append("请填写：" + "、".join(missing) + "。")
        return Outcome("快速初选", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    assert current is not None
    if current <= 0:
        warnings.append("计算电流必须大于 0 A。")
        return Outcome("快速初选", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    breaker = next((rating for rating in PROVISIONAL_BREAKER_RATINGS_A if rating >= current), None)
    if breaker is None:
        warnings.append("计算电流超出当前待核实断路器候选表范围。")
        return Outcome("快速初选", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    core_count = 2 if phase == "1" else 3
    cable_rows = PROVISIONAL_YJV_AMPACITY_A[phase]
    suitable = [(section, ampacity) for section, ampacity in cable_rows if ampacity >= breaker]
    if not suitable:
        warnings.append("计算电流超出当前待核实电缆候选表范围。")
        return Outcome("快速初选", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    section, ampacity = suitable[0]
    candidates = [
        {"spec": f"YJV {core_count}×{candidate_section:g} mm²", "base_ampacity_a": candidate_ampacity}
        for candidate_section, candidate_ampacity in suitable[:3]
    ]
    pole_count = "2P" if phase == "1" else "3P"
    outputs.update(
        {
            "design_current_a": round(current, 3),
            "breaker_candidate": f"{pole_count} {breaker:g} A",
            "cable_candidate": f"YJV {core_count}×{section:g} mm²",
            "cable_base_ampacity_a": ampacity,
            "cable_candidates": candidates,
            "data_status": "待核实",
        }
    )
    steps.extend(
        [
            Step("断路器额定电流初选", f"选择候选表中首个 In ≥ {current:g} A", breaker, "A"),
            Step("电缆截面初选", f"选择候选表中首个基础载流量 Iz ≥ In={breaker:g} A", f"YJV {core_count}×{section:g}", "mm²"),
            Step("电流配合暂算", f"{current:g} ≤ {breaker:g} ≤ {ampacity:g}", PASS),
        ]
    )
    warnings.extend(
        [
            "电缆载流量来自待核实的旧版综合设计表，目前未计入敷设方式、环境温度、成组及隔热修正。",
            "断路器仅完成额定电流初选，尚未校核脱扣特性、选择性、短路电流和分断能力。",
            f"当前按{'两芯' if phase == '1' else '三芯'} YJV 基础载流量暂选；实际芯数和线路结构必须由专业人员确认。",
        ]
    )
    status = _final_status(PASS, rule_codes, rules, warnings)
    return Outcome("快速初选", "0.2.0", status, PASS, outputs, steps, warnings, rule_codes)

def calculate_voltage_drop(
    data: dict[str, Any], rules: dict[str, dict[str, Any]], design_current_a: float | None = None
) -> Outcome:
    rule_codes = ["ELEC.VDROP"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {}
    current = design_current_a if design_current_a is not None else _number(data, "design_current_a")
    voltage = _number(data, "voltage_v")
    length = _number(data, "length_m")
    resistance = _number(data, "cable_r_ohm_per_km")
    reactance = _number(data, "cable_x_ohm_per_km")
    power_factor = _number(data, "power_factor")
    phase = str(data.get("phase") or "")
    missing = [
        name
        for name, value in (
            ("design_current_a", current),
            ("voltage_v", voltage),
            ("length_m", length),
            ("cable_r_ohm_per_km", resistance),
            ("cable_x_ohm_per_km", reactance),
            ("power_factor", power_factor),
        )
        if value is None
    ]
    if phase not in {"1", "3"}:
        missing.append("phase")
    if missing:
        warnings.append("缺少必要输入：" + "、".join(dict.fromkeys(missing)))
        return Outcome("电压降", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    assert None not in {current, voltage, length, resistance, reactance, power_factor}
    if min(current, voltage, length, resistance, reactance) < 0 or not 0 < power_factor <= 1 or voltage == 0:
        warnings.append("电压降输入超出本原型允许范围。")
        return Outcome("电压降", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    sin_phi = sqrt(max(0, 1 - power_factor**2))
    factor = sqrt(3) if phase == "3" else 2
    drop_v = factor * current * (resistance * power_factor + reactance * sin_phi) * length / 1000
    drop_pct = drop_v / voltage * 100
    outputs.update({"voltage_drop_v": round(drop_v, 4), "voltage_drop_pct": round(drop_pct, 4)})
    steps.extend(
        [
            Step("无功系数", f"√(1-{power_factor:g}²)", round(sin_phi, 6)),
            Step(
                "线路电压降",
                f"{factor:.6g}×{current:.4f}×({resistance:g}×{power_factor:g}+{reactance:g}×{sin_phi:.6f})×{length:g}/1000",
                round(drop_v, 4),
                "V",
            ),
            Step("电压降率", f"{drop_v:.4f}/{voltage:g}×100%", round(drop_pct, 4), "%"),
        ]
    )

    limit = _number(data, "voltage_drop_limit_pct")
    if limit is None:
        warnings.append("未提供经核实的电压降限值，无法判定是否满足要求。")
        provisional = UNKNOWN
    else:
        ok = drop_pct <= limit
        provisional = PASS if ok else FAIL
        outputs["limit_check"] = provisional
        steps.append(Step("限值暂算", f"{drop_pct:.4f}% ≤ {limit:g}%", provisional))
    status = _final_status(provisional, rule_codes, rules, warnings)
    return Outcome("电压降", "0.1.0", status, provisional, outputs, steps, warnings, rule_codes)


def _line_impedance_from_catalog(
    data: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[float | None, float | None, dict[str, Any] | None, list[str]]:
    """Return a single cable segment's R/X, without extrapolating a table row."""
    warnings: list[str] = []
    family = str(data.get("conductor_family") or "")
    scenario = str(data.get("installation_scenario") or "")
    section = _number(data, "line_section_mm2")
    if family not in {"BV", "YJV"}:
        warnings.append("电缆线路必须填写已覆盖的导体型号（BV 或 YJV）。")
        return None, None, None, warnings
    if section is None or section <= 0:
        warnings.append("电缆线路必须填写正数截面 line_section_mm2。")
        return None, None, None, warnings

    family_catalog = catalog.get("voltage_drop_impedance", {}).get(family, {})
    if family_catalog.get("status") not in {"verified", "approved"}:
        warnings.append(f"{family} 线路 R/X 参数表尚未核实，不能用于三相短路暂算。")
        return None, None, None, warnings
    rows = family_catalog.get("scenarios", {}).get(scenario)
    if not rows:
        warnings.append(f"当前 R/X 参数表未覆盖 {family} 的“{scenario or '未填写'}”敷设场景。")
        return None, None, None, warnings
    row = next((item for item in rows if float(item["section_mm2"]) == section), None)
    if row is None:
        warnings.append(f"当前 R/X 参数表未列出 {family} {section:g} mm²；不进行插值或猜测。")
        return None, None, None, warnings

    table = family_catalog.get("tables", {}).get("3", {})
    return (
        float(row["resistance_ohm_per_km"]),
        float(row["reactance_ohm_per_km"]),
        {
            "mode": "catalog",
            "source": family_catalog.get("source"),
            "table": table.get("table"),
            "page": table.get("page"),
            "status": family_catalog.get("status"),
            "conductor_family": family,
            "installation_scenario": scenario,
            "section_mm2": section,
        },
        warnings,
    )


def _single_segment_line_impedance(
    data: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[float | None, float | None, dict[str, Any] | None, list[str], str | None]:
    """Resolve one cable/busway segment and return its R/X per km and provenance."""
    warnings: list[str] = []
    line_type = str(data.get("line_type") or "")
    if line_type not in {"cable", "busway"}:
        return None, None, None, ["请填写线路类型 line_type（cable 或 busway）。"], None

    parallel_paths = _number(data, "parallel_paths")
    if parallel_paths is not None and parallel_paths != 1:
        return None, None, None, ["当前仅处理单段、单回线路；并联线路不得按单段阻抗计算。"], None

    explicit_r = _number(data, "line_r_ohm_per_km")
    explicit_x = _number(data, "line_x_ohm_per_km")
    if (explicit_r is None) != (explicit_x is None):
        return None, None, None, ["线路 R/X 必须同时填写 line_r_ohm_per_km 和 line_x_ohm_per_km。"], None
    if explicit_r is not None and explicit_x is not None:
        reference = str(data.get("line_impedance_reference") or "").strip()
        if not reference:
            return None, None, None, ["手工输入线路 R/X 时必须填写 line_impedance_reference，说明参数来源。"], None
        if explicit_r < 0 or explicit_x < 0:
            return None, None, None, ["线路 R/X 不能为负值。"], None
        return (
            explicit_r,
            explicit_x,
            {
                "mode": "explicit",
                "reference": reference,
                "line_type": line_type,
                "status": "unverified_input",
            },
            warnings,
            "ELEC.LINE.IMPEDANCE",
        )

    if line_type == "busway":
        return None, None, None, ["母线槽必须提供制造商或项目资料中的 R/X 及其来源；当前目录没有母线槽参数表。"], None

    resistance, reactance, source, lookup_warnings = _line_impedance_from_catalog(data, catalog)
    warnings.extend(lookup_warnings)
    return resistance, reactance, source, warnings, "ELEC.VDROP.IMPEDANCE"


def _transformer_nameplate_equivalent(
    data: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Derive a transformer LV-side R/X from S, U, uk% and rated short-circuit loss.

    The result is the transformer itself only.  It is not the full upstream network
    impedance and must not be used as one unless the source condition is explicit.
    """
    warnings: list[str] = []
    capacity_kva = _number(data, "transformer_capacity_kva")
    lv_voltage = _number(data, "transformer_lv_rated_voltage_v")
    no_load_voltage = _number(data, "transformer_lv_no_load_voltage_v")
    uk_percent = _number(data, "transformer_uk_percent")
    pk_kw = _number(data, "transformer_pk_kw")
    missing = [
        name
        for name, value in (
            ("transformer_capacity_kva", capacity_kva),
            ("transformer_lv_rated_voltage_v", lv_voltage),
            ("transformer_uk_percent", uk_percent),
            ("transformer_pk_kw", pk_kw),
        )
        if value is None
    ]
    if missing:
        warnings.append("变压器铭牌换算缺少：" + "、".join(missing) + "。")
        return None, warnings
    assert capacity_kva is not None and lv_voltage is not None
    assert uk_percent is not None and pk_kw is not None
    if capacity_kva <= 0 or lv_voltage <= 0:
        warnings.append("变压器额定容量和低压额定电压必须大于 0。")
        return None, warnings
    if not 0 < uk_percent < 100:
        warnings.append("变压器阻抗电压 uk% 必须大于 0 且小于 100。")
        return None, warnings
    if pk_kw <= 0:
        warnings.append("变压器短路损耗 Pk 必须大于 0。")
        return None, warnings

    if no_load_voltage is not None and no_load_voltage <= 0:
        warnings.append("变压器低压空载线电压 U20 必须大于 0。")
        return None, warnings
    if no_load_voltage is None:
        no_load_voltage = 1.05 * lv_voltage
        no_load_voltage_method = "U20未提供，按Schneider G25允许的1.05×Un近似"
    else:
        no_load_voltage_method = "用户提供的变压器低压空载线电压U20"

    rated_current = capacity_kva * 1000 / (sqrt(3) * lv_voltage)
    base_impedance = no_load_voltage**2 / (capacity_kva * 1000)
    impedance = uk_percent / 100 * base_impedance
    # 3×In²×|Z| = Sn×uk%; Pk cannot exceed this or X would be imaginary.
    maximum_pk_kw = capacity_kva * uk_percent / 100
    if pk_kw > maximum_pk_kw:
        warnings.append(
            f"变压器短路损耗 Pk={pk_kw:g} kW 超过由 S×uk% 得到的物理上限 {maximum_pk_kw:g} kW；无法推导 X。"
        )
        return None, warnings
    resistance = pk_kw * 1000 / (3 * rated_current**2)
    reactance_squared = impedance**2 - resistance**2
    if reactance_squared < 0:
        warnings.append("变压器铭牌参数导致 X² 小于 0，无法推导等值电抗。")
        return None, warnings

    return {
        "capacity_kva": capacity_kva,
        "lv_rated_voltage_v": lv_voltage,
        "lv_no_load_voltage_v": no_load_voltage,
        "lv_no_load_voltage_method": no_load_voltage_method,
        "uk_percent": uk_percent,
        "pk_kw": pk_kw,
        "rated_current_a": rated_current,
        "base_impedance_ohm": base_impedance,
        "impedance_magnitude_ohm": impedance,
        "resistance_ohm": resistance,
        "reactance_ohm": sqrt(reactance_squared),
    }, warnings


def calculate_transformer_lv_nameplate_impedance(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """Calculate transformer-only LV equivalent R/X from nameplate data.

    Required: transformer_capacity_kva (kVA), transformer_lv_rated_voltage_v (V),
    transformer_uk_percent (%), transformer_pk_kw (kW).  Outlet Ik is available only
    when source_impedance_mode is explicitly ``infinite_capacity`` and voltage_factor_c
    is provided; this excludes any finite upstream source impedance.
    """
    impedance_rule_code = "ELEC.TRANSFORMER.IMPEDANCE.NAMEPLATE"
    rule_codes = [
        "ELEC.SHORT_CIRCUIT",
        "ELEC.SHORT_CIRCUIT.TRANSFORMER_LV",
        impedance_rule_code,
    ]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "scope": "变压器本体低压侧等值阻抗暂算，不含上级系统阻抗",
        "formal_status_note": "变压器阻抗及短路规则尚未批准，本模块不产生正式结论。",
    }
    if rules.get(impedance_rule_code, {}).get("status") not in {"verified", "approved"}:
        warnings.append(
            "用 S/U/uk%/Pk 推导变压器低压侧 R/X 的原始公式依据尚未核实，"
            "本次不进行铭牌阻抗换算。可继续使用19DX101-1式(15.9)、表15.7的出口短路速查。"
        )
        return Outcome("变压器铭牌等值阻抗", "0.2.1", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    equivalent, equivalent_warnings = _transformer_nameplate_equivalent(data)
    warnings.extend(equivalent_warnings)
    if equivalent is None:
        return Outcome("变压器铭牌等值阻抗", "0.2.1", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    outputs["transformer_equivalent"] = {
        key: round(value, 6) if isinstance(value, (int, float)) else value
        for key, value in equivalent.items()
    }
    steps.extend([
        Step("变压器额定电流", "S×1000/(√3×U)", round(equivalent["rated_current_a"], 4), "A"),
        Step("变压器空载二次电压U20", equivalent["lv_no_load_voltage_method"], round(equivalent["lv_no_load_voltage_v"], 4), "V"),
        Step("变压器阻抗基准值", "U20²/(Sn×1000)", round(equivalent["base_impedance_ohm"], 6), "Ω"),
        Step("变压器阻抗幅值", "uk%/100×Zb", round(equivalent["impedance_magnitude_ohm"], 6), "Ω"),
        Step("变压器等值电阻", "Pk×1000/(3×In²)", round(equivalent["resistance_ohm"], 6), "Ω"),
        Step("变压器等值电抗", "√(|Z|²-R²)", round(equivalent["reactance_ohm"], 6), "Ω"),
    ])

    source_mode = str(data.get("source_impedance_mode") or "")
    voltage_factor = _number(data, "voltage_factor_c")
    if source_mode == "infinite_capacity":
        if voltage_factor not in {1.05, 1.10}:
            warnings.append("上级系统明确为无限容量时，仍须按19DX101-1表15.1填写 voltage_factor_c（1.05 或 1.10）才能计算出口 Ik。")
        else:
            outlet_ik = (
                voltage_factor * equivalent["lv_rated_voltage_v"]
                / (sqrt(3) * equivalent["impedance_magnitude_ohm"])
                / 1000
            )
            outputs["transformer_lv_outlet_ik_ka"] = round(outlet_ik, 4)
            outputs["outlet_ik_boundary"] = "仅在用户明确上级系统容量无限大、忽略上级系统阻抗时适用。"
            steps.append(Step("变压器低压出口初始三相短路电流", "c×U/(√3×|Z变压器|)", round(outlet_ik, 4), "kA"))
    elif source_mode in {"", "provided"}:
        warnings.append("变压器本体 R/X 已算出；未明确上级系统无限容量，故不以变压器 |Z| 单独计算出口 Ik。")
    else:
        warnings.append("source_impedance_mode 仅支持 provided 或 infinite_capacity。")

    return Outcome("变压器铭牌等值阻抗", "0.2.1", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)


def calculate_transformer_phase_pe_impedance(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """Look up a verified Dyn11 transformer phase-PE equivalent at its LV terminals.

    This expert path accepts exact table combinations only. It does not interpolate,
    does not apply Dyn11 data to Yyn0, and does not include any LV connection segment.
    """
    rule_code = "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE"
    rule_codes = [rule_code]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "scope": "变压器低压端子处相保阻抗；不含低压端子之后的母线、电缆或PE段",
        "lookup_mode": "设计手册表列值精确匹配；不插值",
    }
    if rules.get(rule_code, {}).get("status") not in {"verified", "approved"}:
        warnings.append("变压器相保阻抗表尚未核实，本次不进行目录查表。")
        return Outcome("变压器相保阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    series_code = str(data.get("transformer_series_code") or "").lower()
    vector_group = str(data.get("transformer_vector_group") or "")
    capacity_kva = _number(data, "transformer_capacity_kva")
    uk_percent = _number(data, "transformer_uk_percent")
    hv_voltage_kv = _number(data, "transformer_hv_voltage_kv")
    lv_voltage_v = _number(data, "transformer_lv_rated_voltage_v")
    fault_loop_origin = str(data.get("fault_loop_origin") or "")
    missing = [
        name
        for name, value in (
            ("transformer_series_code", series_code),
            ("transformer_vector_group", vector_group),
            ("transformer_capacity_kva", capacity_kva),
            ("transformer_uk_percent", uk_percent),
            ("transformer_hv_voltage_kv", hv_voltage_kv),
            ("transformer_lv_rated_voltage_v", lv_voltage_v),
            ("fault_loop_origin", fault_loop_origin),
        )
        if value in (None, "")
    ]
    if missing:
        warnings.append("变压器相保阻抗查表缺少：" + "、".join(missing) + "。")
        return Outcome("变压器相保阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    assert capacity_kva is not None and uk_percent is not None
    assert hv_voltage_kv is not None and lv_voltage_v is not None
    if vector_group.lower() != "dyn11":
        warnings.append("当前相保阻抗目录只支持Dyn11；Yyn0及其他联结组别必须采用对应试验数据。")
        return Outcome("变压器相保阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if hv_voltage_kv not in {6.0, 10.0} or lv_voltage_v != 400.0:
        warnings.append("当前表列范围仅限6/0.4kV或10/0.4kV，并已归算至400V侧。")
        return Outcome("变压器相保阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if fault_loop_origin != "transformer_lv_terminal":
        warnings.append(
            "表列相保阻抗只到变压器低压端子；fault_loop_origin必须明确为"
            "transformer_lv_terminal，端子后的连接段须另行计入。"
        )
        return Outcome("变压器相保阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    row = lookup_transformer_phase_pe_impedance(series_code, capacity_kva, uk_percent)
    if row is None:
        warnings.append("变压器系列、容量与Uk%组合未进入已核验表列目录；本模块不插值或套用相邻规格。")
        return Outcome("变压器相保阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    resistance = float(row["phase_pe_resistance_ohm"])
    reactance = float(row["phase_pe_reactance_ohm"])
    impedance = sqrt(resistance**2 + reactance**2)
    outputs["transformer_phase_pe_equivalent"] = {
        **row,
        "vector_group": "Dyn11",
        "high_voltage_kv": hv_voltage_kv,
        "low_voltage_v": lv_voltage_v,
        "phase_pe_impedance_ohm": round(impedance, 6),
        "zero_sequence_boundary": "低压侧单相短路时不计入高压侧零序阻抗",
    }
    steps.extend([
        Step("变压器相保电阻", f"{row['phase_pe_resistance_mohm']:g}/1000", round(resistance, 6), "Ω"),
        Step("变压器相保电抗", f"{row['phase_pe_reactance_mohm']:g}/1000", round(reactance, 6), "Ω"),
        Step("变压器相保阻抗", "√(Rphp²+Xphp²)", round(impedance, 6), "Ω"),
    ])
    status = _final_status(PASS, rule_codes, rules, warnings)
    return Outcome("变压器相保阻抗", "0.1.0", status, PASS, outputs, steps, warnings, rule_codes)


def _resolve_feeder_upstream_impedance(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve either an explicit outlet equivalent or transformer plus explicit source."""
    warnings: list[str] = []
    upstream_r = _number(data, "upstream_r_ohm")
    upstream_x = _number(data, "upstream_x_ohm")
    if (upstream_r is None) != (upstream_x is None):
        return None, ["upstream_r_ohm 与 upstream_x_ohm 必须同时填写。"]
    if upstream_r is not None and upstream_x is not None:
        reference = str(data.get("upstream_impedance_reference") or "").strip()
        if not reference:
            return None, ["填写变压器低压出口总等值 R/X 时，必须同时填写 upstream_impedance_reference 说明来源。"]
        if upstream_r < 0 or upstream_x < 0:
            return None, ["变压器低压出口等值 R/X 不能为负值。"]
        if upstream_r == 0 and upstream_x == 0:
            return None, ["变压器低压出口等值 R/X 不能同时为 0。"]
        return {
            "resistance_ohm": upstream_r,
            "reactance_ohm": upstream_x,
            "method": "已提供的出口总等值阻抗",
            "reference": reference,
        }, warnings

    transformer_fields = (
        "transformer_series_code",
        "transformer_capacity_kva",
        "transformer_lv_rated_voltage_v",
        "transformer_uk_percent",
        "transformer_pk_kw",
    )
    if not any(data.get(key) not in (None, "") for key in transformer_fields):
        return None, ["缺少 upstream_r_ohm/upstream_x_ohm；或填写完整变压器铭牌和上级系统阻抗条件。"]

    transformer = None
    series_code = str(data.get("transformer_series_code") or "").strip()
    capacity_kva = _number(data, "transformer_capacity_kva")
    uk_percent = _number(data, "transformer_uk_percent")
    if series_code and capacity_kva is not None and uk_percent is not None:
        table_row = lookup_transformer_positive_sequence_impedance(
            series_code, capacity_kva, uk_percent
        )
        if table_row:
            transformer = {
                "resistance_ohm": table_row["positive_sequence_resistance_ohm"],
                "reactance_ohm": table_row["positive_sequence_reactance_ohm"],
                "impedance_magnitude_ohm": sqrt(
                    table_row["positive_sequence_resistance_ohm"] ** 2
                    + table_row["positive_sequence_reactance_ohm"] ** 2
                ),
                "lv_rated_voltage_v": table_row["low_voltage_v"],
                "lv_no_load_voltage_v": table_row["low_voltage_v"],
                "lv_no_load_voltage_method": "手册表列阻抗已归算至400V侧",
                "load_loss_kw": table_row["load_loss_kw"],
                "source_rule_code": "ELEC.TRANSFORMER.POSITIVE_SEQUENCE.IMPEDANCE",
                "source": (
                    f"{table_row['source']} {table_row['table']}，{table_row['page']}"
                ),
                "calculation_method": "手册正负序阻抗平均值精确查表",
            }
        else:
            warnings.append("所选变压器系列、容量与uk%没有精确表列组合；不插值，改查铭牌参数路径。")

    if transformer is None:
        impedance_rule_code = "ELEC.TRANSFORMER.IMPEDANCE.NAMEPLATE"
        if rules.get(impedance_rule_code, {}).get("status") not in {"verified", "approved"}:
            return None, warnings + [
                "用 S/U/uk%/Pk 推导变压器低压侧 R/X 的原始公式依据尚未核实，"
                "线路末端计算暂不采用该路径；请提供变压器低压出口总等值 R/X。"
            ]

        transformer, transformer_warnings = _transformer_nameplate_equivalent(data)
        warnings.extend(transformer_warnings)
        if transformer is None:
            return None, warnings
        transformer["source_rule_code"] = impedance_rule_code
        transformer["source"] = "变压器铭牌 S/U/uk%/Pk"
        transformer["calculation_method"] = "变压器铭牌 R/X 参数换算"
    source_mode = str(data.get("source_impedance_mode") or "")
    if source_mode == "short_circuit_capacity":
        source_capacity_mva = _number(data, "source_short_circuit_capacity_mva")
        if source_capacity_mva is None or source_capacity_mva <= 0:
            return None, warnings + [
                "source_impedance_mode=short_circuit_capacity 时必须填写大于 0 的上级系统短路容量（MVA）。"
            ]

        # Handbook equation (4.6-41): when exact source R/X is unavailable,
        # R=0.1X and X=0.995Z after referring the source to the LV side.
        source_r_to_x = 0.1
        source_z = transformer["lv_rated_voltage_v"] ** 2 / (source_capacity_mva * 1_000_000)
        source_x = source_z / sqrt(1 + source_r_to_x**2)
        source_r = source_r_to_x * source_x
        source_reference = "《工业与民用供配电设计手册（第四版）》式(4.6-41)，PDF第336页"
        warnings.append(
            "上级系统 R/X 由短路容量按手册式(4.6-41)折算（R/X=0.1）；"
            "上级短路容量仍应以项目供电资料复核。"
        )
        return {
            "resistance_ohm": transformer["resistance_ohm"] + source_r,
            "reactance_ohm": transformer["reactance_ohm"] + source_x,
            "method": f"{transformer['calculation_method']} + 上级短路容量折算",
            "transformer_equivalent": transformer,
            "source_equivalent": {
                "resistance_ohm": source_r,
                "reactance_ohm": source_x,
                "impedance_ohm": source_z,
                "short_circuit_capacity_mva": source_capacity_mva,
                "r_to_x_ratio": source_r_to_x,
                "condition": "short_circuit_capacity",
                "reference": source_reference,
            },
        }, warnings
    if source_mode == "infinite_capacity":
        return {
            "resistance_ohm": transformer["resistance_ohm"],
            "reactance_ohm": transformer["reactance_ohm"],
            "method": f"{transformer['calculation_method']}；用户明确上级系统容量无限大",
            "transformer_equivalent": transformer,
            "source_equivalent": {"resistance_ohm": 0.0, "reactance_ohm": 0.0, "condition": "infinite_capacity"},
        }, warnings
    if source_mode != "provided":
        return None, warnings + ["未明确上级系统无限容量且未提供上级等值 R/X，不能把变压器本体阻抗当作完整上游阻抗。"]

    source_r = _number(data, "source_r_ohm")
    source_x = _number(data, "source_x_ohm")
    if source_r is None or source_x is None:
        return None, warnings + ["source_impedance_mode=provided 时必须同时填写 source_r_ohm 与 source_x_ohm。"]
    if source_r < 0 or source_x < 0:
        return None, warnings + ["上级系统等值 R/X 不能为负值。"]
    return {
        "resistance_ohm": transformer["resistance_ohm"] + source_r,
        "reactance_ohm": transformer["reactance_ohm"] + source_x,
        "method": f"{transformer['calculation_method']} + 已提供的上级系统等值 R/X",
        "transformer_equivalent": transformer,
        "source_equivalent": {"resistance_ohm": source_r, "reactance_ohm": source_x, "condition": "provided"},
    }, warnings


def calculate_transformer_feeder_three_phase_short_circuit(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """Calculate maximum three-phase fault current at both ends of one feeder.

    The transformer outlet may be supplied as total R/X, or constructed from complete
    transformer nameplate data plus an explicit upstream-source condition. An outlet Ik
    value alone is intentionally not converted into R/X because it only gives the
    impedance magnitude. A feeder protective breaker is normally installed at
    the line start, so its Icu must be checked at that point; the line-end value
    is retained for downstream protection checks. This function does not
    calculate earth faults.
    """
    catalog = catalog or DEFAULT_CATALOG
    rule_codes = ["ELEC.SHORT_CIRCUIT", "ELEC.BREAKING.CAPACITY"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "scope": "变压器低压出口至单段线路末端的三相对称短路暂算",
        "formal_status_note": "线路阻抗和短路规则尚未批准，本模块不产生正式结论。",
    }

    if str(data.get("phase") or "") != "3":
        warnings.append("本模块仅处理三相对称短路；单相接地故障另行计算。")
        return Outcome("变压器—线路末端三相短路", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    voltage = _number(data, "voltage_v")
    voltage_factor = _number(data, "voltage_factor_c")
    upstream, upstream_warnings = _resolve_feeder_upstream_impedance(data, rules)
    warnings.extend(upstream_warnings)
    if upstream and "transformer_equivalent" in upstream:
        transformer_rule = upstream["transformer_equivalent"].get("source_rule_code")
        if transformer_rule:
            rule_codes.append(transformer_rule)
    upstream_r = upstream.get("resistance_ohm") if upstream else None
    upstream_x = upstream.get("reactance_ohm") if upstream else None
    length_m = _number(data, "length_m")
    missing = [
        name
        for name, value in (
            ("voltage_v", voltage),
            ("voltage_factor_c", voltage_factor),
            ("变压器低压出口总等值R/X", upstream_r if upstream_x is not None else None),
            ("length_m", length_m),
        )
        if value is None
    ]
    if missing:
        warnings.append("缺少线路末端短路计算必要输入：" + "、".join(missing) + "。")

    if voltage is not None and voltage <= 0:
        warnings.append("voltage_v 必须大于 0。")
    if voltage_factor is not None and voltage_factor not in {1.05, 1.10}:
        warnings.append("最大三相短路电流的 voltage_factor_c 必须按19DX101-1表15.1填写 1.05 或 1.10。")
    if length_m is not None and length_m < 0:
        warnings.append("length_m 不能小于 0。")
    line_r, line_x, line_source, line_warnings, line_rule_code = _single_segment_line_impedance(data, catalog)
    warnings.extend(line_warnings)
    if line_rule_code:
        rule_codes.append(line_rule_code)

    endpoint_ready = (
        not missing
        and voltage is not None
        and voltage_factor is not None
        and upstream_r is not None
        and upstream_x is not None
        and length_m is not None
        and line_r is not None
        and line_x is not None
        and not any("必须" in warning or "不能" in warning or "未覆盖" in warning or "不得" in warning for warning in warnings)
    )
    line_start_current_ka: float | None = None
    endpoint_current_ka: float | None = None
    if endpoint_ready:
        assert voltage is not None and voltage_factor is not None
        assert upstream_r is not None and upstream_x is not None and length_m is not None
        assert line_r is not None and line_x is not None
        line_r_ohm = line_r * length_m / 1000
        line_x_ohm = line_x * length_m / 1000
        total_r = upstream_r + line_r_ohm
        total_x = upstream_x + line_x_ohm
        impedance = sqrt(total_r**2 + total_x**2)
        line_start_impedance = sqrt(upstream_r**2 + upstream_x**2)
        if line_start_impedance == 0:
            warnings.append("线路起点上游等值阻抗为 0，无法计算该处短路电流。")
        else:
            line_start_current_ka = (
                voltage_factor
                * voltage
                / (sqrt(3) * line_start_impedance)
                / 1000
            )
        if impedance == 0:
            warnings.append("上游和线路总阻抗为 0，无法计算短路电流。")
        else:
            endpoint_current_ka = voltage_factor * voltage / (sqrt(3) * impedance) / 1000
            outputs.update({
                "voltage_v": voltage,
                "voltage_factor_c": voltage_factor,
                "upstream_impedance": {
                    "resistance_ohm": round(upstream_r, 6),
                    "reactance_ohm": round(upstream_x, 6),
                    "magnitude_ohm": round(sqrt(upstream_r**2 + upstream_x**2), 6),
                    "method": upstream["method"],
                    "reference": upstream.get("reference", ""),
                },
                "line_impedance": {
                    "length_m": length_m,
                    "resistance_ohm_per_km": line_r,
                    "reactance_ohm_per_km": line_x,
                    "resistance_ohm": round(line_r_ohm, 6),
                    "reactance_ohm": round(line_x_ohm, 6),
                    "source": line_source,
                },
                "terminal_total_resistance_ohm": round(total_r, 6),
                "terminal_total_reactance_ohm": round(total_x, 6),
                "terminal_total_impedance_ohm": round(impedance, 6),
                "terminal_short_circuit_current_ka": round(endpoint_current_ka, 4),
            })
            if line_start_current_ka is not None:
                outputs.update({
                    "line_start_impedance_ohm": round(
                        line_start_impedance, 6
                    ),
                    "line_start_short_circuit_current_ka": round(
                        line_start_current_ka, 4
                    ),
                })
            if "transformer_equivalent" in upstream:
                transformer = upstream["transformer_equivalent"]
                source = upstream["source_equivalent"]
                outputs["upstream_impedance"]["transformer_equivalent"] = {
                    "resistance_ohm": round(transformer["resistance_ohm"], 6),
                    "reactance_ohm": round(transformer["reactance_ohm"], 6),
                    "impedance_magnitude_ohm": round(transformer["impedance_magnitude_ohm"], 6),
                    "lv_no_load_voltage_v": round(transformer["lv_no_load_voltage_v"], 4),
                    "lv_no_load_voltage_method": transformer["lv_no_load_voltage_method"],
                    "source": transformer.get("source", "变压器阻抗来源未记录"),
                }
                outputs["upstream_impedance"]["source_equivalent"] = {
                    "resistance_ohm": round(source["resistance_ohm"], 6),
                    "reactance_ohm": round(source["reactance_ohm"], 6),
                    "condition": source["condition"],
                }
                if source["condition"] == "short_circuit_capacity":
                    outputs["upstream_impedance"]["source_equivalent"].update({
                        "impedance_ohm": round(source["impedance_ohm"], 6),
                        "short_circuit_capacity_mva": source["short_circuit_capacity_mva"],
                        "r_to_x_ratio": source["r_to_x_ratio"],
                        "reference": source["reference"],
                    })
            steps.extend([
                Step(
                    "线路起点三相短路电流",
                    "c×U/(√3×|Z上游|)",
                    round(line_start_current_ka, 4)
                    if line_start_current_ka is not None
                    else None,
                    "kA",
                ),
                Step("线路电阻", f"{line_r:g}×{length_m:g}/1000", round(line_r_ohm, 6), "Ω"),
                Step("线路电抗", f"{line_x:g}×{length_m:g}/1000", round(line_x_ohm, 6), "Ω"),
                Step("末端总电阻", "R上游+R线路", round(total_r, 6), "Ω"),
                Step("末端总电抗", "X上游+X线路", round(total_x, 6), "Ω"),
                Step("末端总阻抗", "√(R²+X²)", round(impedance, 6), "Ω"),
                Step("线路末端三相短路电流", "c×U/(√3×|Z|)", round(endpoint_current_ka, 4), "kA"),
            ])
            if "transformer_equivalent" in upstream:
                transformer = upstream["transformer_equivalent"]
                source = upstream["source_equivalent"]
                steps[0:0] = [
                    Step("变压器等值电阻", transformer["calculation_method"], round(transformer["resistance_ohm"], 6), "Ω"),
                    Step("变压器等值电抗", transformer["calculation_method"], round(transformer["reactance_ohm"], 6), "Ω"),
                    Step("上级系统等值电阻", "R上级", round(source["resistance_ohm"], 6), "Ω"),
                    Step("上级系统等值电抗", "X上级", round(source["reactance_ohm"], 6), "Ω"),
                    Step("出口总等值电阻", "R变压器+R上级", round(upstream_r, 6), "Ω"),
                    Step("出口总等值电抗", "X变压器+X上级", round(upstream_x, 6), "Ω"),
                ]
    else:
        warnings.append("未以出口等值 R/X 和单段线路 R/X 完成线路末端短路电流暂算；系统不会由出口 Ik 幅值推定 R/X 分量。")

    breaker_icu = _number(data, "breaker_icu_ka")
    breaker_point = str(
        data.get("breaker_installation_point") or "line_start"
    )
    breaker_check: str | None = None
    prospective_at_breaker: float | None = None
    if breaker_point == "line_start":
        prospective_at_breaker = line_start_current_ka
    elif breaker_point == "line_end":
        prospective_at_breaker = endpoint_current_ka
    elif breaker_point == "transformer_lv_outlet":
        prospective_at_breaker = _number(
            data, "transformer_lv_outlet_ik_ka"
        )
        if prospective_at_breaker is None and line_start_current_ka is not None:
            prospective_at_breaker = line_start_current_ka
    else:
        warnings.append(
            "breaker_installation_point 必须为 line_start、line_end 或 transformer_lv_outlet。"
        )
    if prospective_at_breaker is not None:
        outputs["required_breaking_capacity_ka"] = round(
            prospective_at_breaker, 4
        )
        outputs["required_breaking_capacity_point"] = breaker_point
        outputs["required_breaking_capacity_note"] = (
            "断路器Icu不得小于其安装点最大预期短路电流；"
            "馈线起点与线路末端不得混用。"
        )
    if breaker_icu is None and breaker_point:
        outputs["breaker_icu_check"] = {
            "installation_point": breaker_point,
            "required_minimum_icu_ka": (
                round(prospective_at_breaker, 4)
                if prospective_at_breaker is not None
                else None
            ),
            "provisional_status": UNKNOWN,
            "note": "设计模式已给出Icu最低要求；选择具体产品后再校核实际Icu。",
        }
    elif breaker_icu is not None and breaker_icu <= 0:
        warnings.append("breaker_icu_ka 必须大于 0。")
    elif breaker_icu is not None:
        if breaker_point == "line_start":
            prospective_at_breaker = line_start_current_ka
            if prospective_at_breaker is None:
                warnings.append(
                    "线路起点短路电流未算出，不能比较馈线断路器 Icu。"
                )
        elif breaker_point == "line_end":
            prospective_at_breaker = endpoint_current_ka
            if prospective_at_breaker is None:
                warnings.append("线路末端短路电流未算出，不能比较该处断路器 Icu。")
        elif breaker_point == "transformer_lv_outlet":
            prospective_at_breaker = _number(data, "transformer_lv_outlet_ik_ka")
            if prospective_at_breaker is None or prospective_at_breaker <= 0:
                warnings.append("变压器低压出口断路器 Icu 比较必须提供 transformer_lv_outlet_ik_ka。")
                prospective_at_breaker = None
        else:
            prospective_at_breaker = None

        if prospective_at_breaker is not None:
            breaker_check = PASS if breaker_icu >= prospective_at_breaker else FAIL
            outputs["breaker_icu_check"] = {
                "installation_point": breaker_point,
                "prospective_short_circuit_ka": round(prospective_at_breaker, 4),
                "breaker_icu_ka": breaker_icu,
                "provisional_status": breaker_check,
            }
            steps.append(
                Step(
                    "断路器 Icu 暂算",
                    f"{breaker_icu:g} ≥ {prospective_at_breaker:.4f}",
                    breaker_check,
                )
            )
    else:
        outputs["breaker_icu_check"] = {
            "installation_point": breaker_point,
            "required_minimum_icu_ka": (
                round(prospective_at_breaker, 4)
                if prospective_at_breaker is not None
                else None
            ),
            "provisional_status": UNKNOWN,
            "note": "尚未选定具体产品，当前只输出Icu最低要求。",
        }

    provisional = breaker_check if endpoint_current_ka is not None and breaker_check is not None else UNKNOWN
    status = _final_status(provisional, rule_codes, rules, warnings)
    line_source_status = line_source.get("status") if line_source else None
    if status != UNKNOWN and line_source_status != "approved":
        warnings.append("线路 R/X 数据尚未批准；本结果仅用于暂算，不能作为正式设计结论。")
        status = UNKNOWN
    return Outcome("变压器—线路末端三相短路", "0.2.0", status, provisional, outputs, steps, warnings, rule_codes)


def calculate_tn_fault_loop_chain(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """Compose a traceable TN phase-PE impedance chain to a declared target point.

    The transformer component is obtained from the verified Dyn11 table path.
    At most one transformer-to-main-switchboard segment and one outgoing-circuit
    segment are accepted. Explicit segment R/X requires a reference; a copper
    cable segment may reuse the existing cable component calculator.
    """
    warnings: list[str] = []
    steps: list[Step] = []
    rule_codes: list[str] = []
    outputs: dict[str, Any] = {
        "scope": "变压器低压端子至指定安装点的TN相—PE故障回路阻抗链",
        "formal_status_note": "各分段依据未全部批准时，只输出暂算结果。",
    }

    target_point = str(data.get("target_point") or "")
    target_names = {
        "transformer_lv_terminal": "变压器低压端子",
        "main_switchboard": "低压总柜",
        "line_end": "线路末端",
    }
    if target_point not in target_names:
        warnings.append(
            "target_point仅支持transformer_lv_terminal、main_switchboard或line_end。"
        )
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    outputs["target_point"] = target_point
    outputs["target_point_name"] = target_names[target_point]

    transformer_data = data.get("transformer_phase_pe_data")
    if not isinstance(transformer_data, dict):
        warnings.append("必须提供transformer_phase_pe_data作为故障回路电源端阻抗。")
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    transformer_result = calculate_transformer_phase_pe_impedance(transformer_data, rules)
    outputs["transformer_phase_pe"] = transformer_result.to_dict()
    for code in transformer_result.rule_codes:
        if code not in rule_codes:
            rule_codes.append(code)
    transformer = transformer_result.outputs.get("transformer_phase_pe_equivalent")
    if not isinstance(transformer, dict):
        warnings.extend(transformer_result.warnings)
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    raw_segments = data.get("segments", [])
    if raw_segments in (None, ""):
        raw_segments = []
    if not isinstance(raw_segments, list) or not all(
        isinstance(item, dict) for item in raw_segments
    ):
        warnings.append("segments必须为结构化分段列表。")
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    allowed_roles = {"transformer_to_main_switchboard", "outgoing_circuit"}
    segment_roles = [str(item.get("role") or "") for item in raw_segments]
    if any(role not in allowed_roles for role in segment_roles):
        warnings.append(
            "分段role仅支持transformer_to_main_switchboard或outgoing_circuit。"
        )
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if len(segment_roles) != len(set(segment_roles)):
        warnings.append("同一故障回路链中每种分段role最多出现一次。")
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if target_point == "transformer_lv_terminal" and raw_segments:
        warnings.append("目标为变压器低压端子时不得再填写下游连接段。")
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if target_point == "main_switchboard" and segment_roles != ["transformer_to_main_switchboard"]:
        warnings.append("目标为低压总柜时必须且只能提供变压器至低压总柜连接段。")
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if target_point == "line_end" and "outgoing_circuit" not in segment_roles:
        warnings.append("目标为线路末端时必须提供outgoing_circuit分段。")
        return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    total_r = float(transformer["phase_pe_resistance_ohm"])
    total_x = float(transformer["phase_pe_reactance_ohm"])
    components: list[dict[str, Any]] = [{
        "role": "transformer",
        "name": transformer["series_name"],
        "resistance_ohm": total_r,
        "reactance_ohm": total_x,
        "reference": (
            f"{transformer['source']}，{transformer['table']}，{transformer['page']}"
        ),
    }]
    steps.extend([
        Step("变压器相保电阻", "查表Rphp", round(total_r, 6), "Ω"),
        Step("变压器相保电抗", "查表Xphp", round(total_x, 6), "Ω"),
    ])
    unbound_explicit_source = False

    for segment in raw_segments:
        role = str(segment.get("role"))
        segment_type = str(segment.get("segment_type") or "")
        name = str(segment.get("name") or role)
        calculation_mode = str(segment.get("calculation_mode") or "")
        segment_r: float | None = None
        segment_x: float | None = None
        reference = ""
        component_detail: dict[str, Any] = {}

        if calculation_mode == "yjv_four_core_catalog":
            configuration_code = str(segment.get("configuration_code") or "")
            fourth_conductor_role = str(
                segment.get("fourth_conductor_role") or ""
            ).upper()
            phase_section = _number(segment, "phase_section_mm2")
            length_m = _number(segment, "length_m")
            if configuration_code != "yjv_4c_3ph_n_pe":
                warnings.append(f"{name}仅支持已核验的YJV四芯（3+1）表列结构。")
                return Outcome("TN故障回路阻抗链", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            if fourth_conductor_role != "PE":
                warnings.append(f"{name}必须确认第四芯作为PE，不能把N芯直接当作另设PE。")
                return Outcome("TN故障回路阻抗链", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            if phase_section is None or length_m is None or length_m <= 0:
                warnings.append(f"{name}必须填写表列相导体截面和有效长度。")
                return Outcome("TN故障回路阻抗链", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            impedance = lookup_yjv_four_core_phase_pe_impedance(phase_section)
            if not impedance:
                warnings.append(f"{name}的YJV四芯规格不在手册表4.2-46已核验范围内。")
                return Outcome("TN故障回路阻抗链", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            segment_r = (
                impedance["phase_pe_resistance_ohm_per_km"] * length_m / 1000
            )
            segment_x = (
                impedance["phase_pe_reactance_ohm_per_km"] * length_m / 1000
            )
            source_rule_code = "ELEC.CABLE.YJV.FOUR_CORE.PHASE_PE.IMPEDANCE"
            if source_rule_code not in rule_codes:
                rule_codes.append(source_rule_code)
            reference = (
                f"{impedance['source']}，{impedance['table']}，{impedance['page']}；"
                f"{impedance['formula']}，{impedance['formula_page']}；"
                f"低压单相短路电阻1.5倍，{impedance['calculation_condition_page']}"
            )
            component_detail.update({
                "cable_specification": impedance["cable_specification"],
                "phase_section_mm2": phase_section,
                "protective_section_mm2": impedance["protective_section_mm2"],
                "length_m": length_m,
                "phase_pe_resistance_20c_ohm_per_km": impedance[
                    "phase_pe_resistance_20c_ohm_per_km"
                ],
                "phase_pe_resistance_multiplier": impedance[
                    "phase_pe_resistance_multiplier"
                ],
                "resistance_ohm_per_km": impedance[
                    "phase_pe_resistance_ohm_per_km"
                ],
                "reactance_ohm_per_km": impedance[
                    "phase_pe_reactance_ohm_per_km"
                ],
                "boundary_note": impedance["boundary_note"],
            })
        elif calculation_mode == "copper_cable":
            cable_data = segment.get("cable_data")
            if not isinstance(cable_data, dict):
                warnings.append(f"{name}缺少结构化cable_data。")
                return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            isolated_cable_data = {
                **cable_data,
                "upstream_resistance_ohm": 0,
                "upstream_reactance_ohm": 0,
                "upstream_impedance_reference": "分段计算零基准（非物理电源阻抗）",
            }
            cable_result = calculate_cable_fault_loop_impedance(
                isolated_cable_data, rules
            )
            component_detail["cable_calculation"] = cable_result.to_dict()
            for code in cable_result.rule_codes:
                if code not in rule_codes:
                    rule_codes.append(code)
            if (
                cable_result.outputs.get("line_loop_ac_resistance_ohm") is None
                or cable_result.outputs.get("line_loop_reactance_ohm") is None
            ):
                warnings.append(f"{name}尚未形成完整的电缆相—PE分段R/X。")
                warnings.extend(cable_result.warnings)
                return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            segment_r = float(cable_result.outputs["line_loop_ac_resistance_ohm"])
            segment_x = float(cable_result.outputs["line_loop_reactance_ohm"])
            reference = (
                "系统分段计算："
                f"{cable_result.outputs.get('resistance_reference', '')}；"
                f"{cable_result.outputs.get('loop_reactance_reference', '')}"
            )
        elif calculation_mode in {"explicit_total", "explicit_per_km"}:
            reference = str(segment.get("impedance_reference") or "").strip()
            if not reference:
                warnings.append(f"{name}的手工R/X必须填写impedance_reference。")
                return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            if calculation_mode == "explicit_total":
                segment_r = _number(segment, "resistance_ohm")
                segment_x = _number(segment, "reactance_ohm")
            else:
                r_per_km = _number(segment, "resistance_ohm_per_km")
                x_per_km = _number(segment, "reactance_ohm_per_km")
                length_m = _number(segment, "length_m")
                if (
                    r_per_km is None
                    or x_per_km is None
                    or length_m is None
                    or length_m <= 0
                ):
                    warnings.append(f"{name}按单位长度R/X计算时必须填写有效R、X和长度。")
                    return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
                segment_r = r_per_km * length_m / 1000
                segment_x = x_per_km * length_m / 1000
                component_detail.update({
                    "resistance_ohm_per_km": r_per_km,
                    "reactance_ohm_per_km": x_per_km,
                    "length_m": length_m,
                })
            source_rule_code = str(segment.get("source_rule_code") or "").strip()
            if source_rule_code:
                if source_rule_code not in rule_codes:
                    rule_codes.append(source_rule_code)
            else:
                unbound_explicit_source = True
        else:
            warnings.append(
                f"{name}的calculation_mode仅支持yjv_four_core_catalog、"
                "copper_cable、explicit_total或explicit_per_km。"
            )
            return Outcome("TN故障回路阻抗链", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

        if (
            segment_r is None
            or segment_x is None
            or segment_r < 0
            or segment_x < 0
            or (segment_r == 0 and segment_x == 0)
        ):
            warnings.append(f"{name}的分段R/X必须为非负数且不能同时为0。")
            return Outcome("TN故障回路阻抗链", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

        total_r += segment_r
        total_x += segment_x
        components.append({
            "role": role,
            "segment_type": segment_type,
            "name": name,
            "calculation_mode": calculation_mode,
            "resistance_ohm": round(segment_r, 6),
            "reactance_ohm": round(segment_x, 6),
            "reference": reference,
            **component_detail,
        })
        steps.extend([
            Step(f"{name}相保电阻", "分段R", round(segment_r, 6), "Ω"),
            Step(f"{name}相保电抗", "分段X", round(segment_x, 6), "Ω"),
        ])

    zs = sqrt(total_r**2 + total_x**2)
    calculation_reference = "系统链式计算：" + "；".join(
        component["reference"] for component in components
    )
    outputs.update({
        "components": components,
        "fault_loop_total_resistance_ohm": round(total_r, 6),
        "fault_loop_total_reactance_ohm": round(total_x, 6),
        "fault_loop_impedance_ohm": round(zs, 6),
        "calculation_reference": calculation_reference,
    })
    steps.extend([
        Step("故障回路总电阻", "ΣRphp", round(total_r, 6), "Ω"),
        Step("故障回路总电抗", "ΣXphp", round(total_x, 6), "Ω"),
        Step("完整故障回路阻抗", "√[(ΣR)²+(ΣX)²]", round(zs, 6), "Ω"),
    ])

    status = _final_status(PASS, rule_codes, rules, warnings)
    if unbound_explicit_source:
        warnings.append("手工分段R/X尚未绑定已批准的source_rule_code，不能形成正式结论。")
        status = UNKNOWN
    return Outcome("TN故障回路阻抗链", "0.2.0", status, PASS, outputs, steps, warnings, rule_codes)


def calculate_close_proximity_loop_fault_current(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """Conservatively calculate the minimum current at the end of a close loop.

    This is the resistance-only conventional method for copper conductors that
    run in the same cable or in close proximity and do not exceed 120 mm².  It
    deliberately remains separate from the exact R/X impedance chain.
    """

    loop_kind = str(data.get("loop_kind") or "")
    loop_labels = {
        "phase_neutral": ("相—N", "N"),
        "phase_pe": ("相—PE", "PE"),
    }
    rule_codes = ["ELEC.EARTH_FAULT.TN.CONVENTIONAL"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "method": "近邻铜导体保守常规法",
        "exact_impedance_chain_completed": False,
    }

    if loop_kind not in loop_labels:
        warnings.append("loop_kind仅支持phase_neutral或phase_pe。")
    material = str(data.get("conductor_material") or "").lower()
    if material != "copper":
        warnings.append("近邻导体常规法当前只接入铜芯导体。")
    phase_section = _number(data, "phase_section_mm2")
    return_section = _number(data, "return_section_mm2")
    length_m = _number(data, "length_m")
    voltage = _number(data, "nominal_loop_voltage_v")
    close_raw = data.get("conductors_in_same_cable_or_close")
    close_proximity = close_raw is True or str(close_raw).lower() in {
        "1", "true", "yes", "on"
    }
    if (
        phase_section is None
        or return_section is None
        or phase_section <= 0
        or return_section <= 0
    ):
        warnings.append("近邻导体常规法缺少有效的相导体或返回导体截面。")
    elif max(phase_section, return_section) > 120:
        warnings.append("近邻导体常规法忽略电抗的适用范围为导体截面不超过120mm²。")
    if length_m is None or length_m <= 0:
        warnings.append("近邻导体常规法必须填写大于0的线路长度。")
    if voltage is None or voltage <= 0:
        warnings.append("近邻导体常规法必须填写大于0的回路标称电压。")
    if not close_proximity:
        warnings.append("近邻导体常规法只适用于同一电缆内或彼此靠近的导体。")

    ready = not warnings
    if ready:
        assert phase_section is not None and return_section is not None
        assert length_m is not None and voltage is not None
        loop_label, return_label = loop_labels[loop_kind]
        copper_resistivity = 0.0237
        voltage_factor = 0.8
        loop_resistance = copper_resistivity * length_m * (
            1 / phase_section + 1 / return_section
        )
        minimum_current = voltage_factor * voltage / loop_resistance
        outputs.update({
            "loop_kind": loop_kind,
            "loop_label": loop_label,
            "return_conductor_label": return_label,
            "phase_section_mm2": phase_section,
            "return_section_mm2": return_section,
            "length_m": length_m,
            "nominal_loop_voltage_v": voltage,
            "copper_resistivity_ohm_mm2_per_m": copper_resistivity,
            "voltage_factor": voltage_factor,
            "resistance_only_loop_ohm": round(loop_resistance, 6),
            "minimum_fault_current_a": round(minimum_current, 4),
            "maximum_permitted_instantaneous_operating_current_a": round(
                minimum_current, 4
            ),
            "source": "Schneider Electric, Electrical Installation Guide 2018",
            "section": "F 5.3, Conventional method",
            "page": "PDF第185页（F15）",
            "applicability": (
                "铜芯；相导体与返回导体在同一电缆内或彼此靠近；"
                "截面不超过120mm²"
            ),
            "boundary_note": (
                "该结果是末端最小故障电流的保守暂算，不等同于已取得完整R/X。"
            ),
        })
        steps.extend([
            Step(
                f"{loop_label}电阻回路",
                f"ρ×L×(1/Sph+1/S{return_label})",
                round(loop_resistance, 6),
                "Ω",
            ),
            Step(
                f"{loop_label}末端最小故障电流",
                "0.8×U₀/Rloop",
                round(minimum_current, 4),
                "A",
            ),
        ])
        if loop_kind == "phase_neutral":
            rule_codes.append("ELEC.SHORT_CIRCUIT")
            warnings.append(
                "相—N结果是将同页近邻导体电阻回路方法用于L—N回路的工程暂算；"
                "未批准前不形成正式短路结论。"
            )

    provisional = PASS if ready else UNKNOWN
    status = _final_status(provisional, rule_codes, rules, warnings)
    return Outcome(
        "近邻导体末端最小故障电流",
        "0.1.0",
        status,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def calculate_tn_earth_fault_protection(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """Check TN automatic disconnection from traceable Zs and Ia inputs.

    Zs can be supplied with a reference, produced by
    ``calculate_cable_fault_loop_impedance`` from a ``fault_loop_data`` mapping.
    A separate conservative conventional-method path is available for close
    phase/PE conductors in the same low-voltage cable up to 120 mm².
    Ia is never derived from a breaker rating and must retain its own reference.
    """
    rule_codes = [
        "ELEC.EARTH_FAULT.TN.IMPEDANCE",
        "ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME",
    ]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "scope": "TN-S/TN-C-S交流回路接地故障与自动切断校核",
        "formal_status_note": "相关规则尚未批准时，只输出暂算结果。",
    }

    earthing_system = str(data.get("earthing_system") or "").upper()
    if earthing_system not in {"TN-S", "TN-C-S"}:
        warnings.append("earthing_system 仅支持 TN-S 或 TN-C-S；本模块不处理 TN-C、TT 或 IT。")

    voltage = _number(data, "nominal_line_to_earth_voltage_v")
    if voltage is None or voltage <= 0:
        warnings.append("请填写大于0的相导体对地标称交流电压 nominal_line_to_earth_voltage_v。")

    application = str(data.get("circuit_application") or "")
    circuit_rating = _number(data, "circuit_rated_current_a")
    max_time: float | None = None
    time_basis = ""
    if application == "socket_final":
        if circuit_rating is None:
            warnings.append("插座终端回路必须填写 circuit_rated_current_a，以判断是否不超过63A。")
        elif circuit_rating <= 0:
            warnings.append("circuit_rated_current_a 必须大于0。")
        elif circuit_rating <= 63:
            if voltage is not None and 120 < voltage <= 230:
                max_time = 0.4
                time_basis = "终端插座回路≤63A，120V＜U₀≤230V"
            else:
                warnings.append("当前已核实的表41.1数值仅覆盖TN系统交流120V＜U₀≤230V。")
        else:
            max_time = 5.0
            time_basis = "超过63A的插座回路，按411.3.2.3所述其他回路"
    elif application == "fixed_equipment_final":
        if circuit_rating is None:
            warnings.append("固定设备终端回路必须填写 circuit_rated_current_a，以判断是否不超过32A。")
        elif circuit_rating <= 0:
            warnings.append("circuit_rated_current_a 必须大于0。")
        elif circuit_rating <= 32:
            if voltage is not None and 120 < voltage <= 230:
                max_time = 0.4
                time_basis = "固定设备终端回路≤32A，120V＜U₀≤230V"
            else:
                warnings.append("当前已核实的表41.1数值仅覆盖TN系统交流120V＜U₀≤230V。")
        else:
            max_time = 5.0
            time_basis = "超过32A的固定设备回路，按411.3.2.3所述其他回路"
    elif application == "distribution":
        max_time = 5.0
        time_basis = "TN系统配电回路"
    elif application:
        warnings.append("circuit_application 仅支持 socket_final、fixed_equipment_final 或 distribution。")
    else:
        warnings.append("请填写 circuit_application，以确定最长切断时间。")

    if max_time is not None:
        outputs["maximum_disconnection_time_s"] = max_time
        outputs["disconnection_time_basis"] = time_basis

    loop_impedance = _number(data, "fault_loop_impedance_ohm")
    loop_reference = str(data.get("fault_loop_impedance_reference") or "").strip()
    conventional_fault_current: float | None = None
    conventional_loop_resistance: float | None = None
    calculated_loop: Outcome | None = None
    calculated_chain: Outcome | None = None
    fault_loop_chain_data = data.get("fault_loop_chain_data")
    if loop_impedance is None and isinstance(fault_loop_chain_data, dict):
        calculated_chain = calculate_tn_fault_loop_chain(
            fault_loop_chain_data, rules
        )
        outputs["calculated_fault_loop_chain"] = calculated_chain.to_dict()
        for code in calculated_chain.rule_codes:
            if code not in rule_codes:
                rule_codes.append(code)
        if calculated_chain.outputs.get("fault_loop_impedance_ohm") is not None:
            loop_impedance = float(calculated_chain.outputs["fault_loop_impedance_ohm"])
            loop_reference = str(
                calculated_chain.outputs.get("calculation_reference") or ""
            )
        else:
            warnings.append("故障回路链尚未形成完整Zs，请补齐各安装点之间的分段阻抗。")
            warnings.extend(calculated_chain.warnings)
    fault_loop_data = data.get("fault_loop_data")
    if loop_impedance is None and isinstance(fault_loop_data, dict):
        calculated_loop = calculate_cable_fault_loop_impedance(fault_loop_data, rules)
        outputs["calculated_fault_loop"] = calculated_loop.to_dict()
        for code in calculated_loop.rule_codes:
            if code not in rule_codes:
                rule_codes.append(code)
        if calculated_loop.outputs.get("fault_loop_impedance_ohm") is not None:
            loop_impedance = float(calculated_loop.outputs["fault_loop_impedance_ohm"])
            loop_reference = str(calculated_loop.outputs.get("calculation_reference") or "")
        else:
            warnings.append("已计算导体电阻，但尚未形成完整Zs；请补齐计算结果列出的缺失阻抗参数。")
    conventional_data = data.get("conventional_method_data")
    if loop_impedance is None and isinstance(conventional_data, dict):
        rule_codes.append("ELEC.EARTH_FAULT.TN.CONVENTIONAL")
        material = str(conventional_data.get("conductor_material") or "").lower()
        phase_section = _number(conventional_data, "phase_section_mm2")
        protective_section = _number(conventional_data, "protective_section_mm2")
        length_m = _number(conventional_data, "length_m")
        close_proximity_raw = conventional_data.get("conductors_in_same_cable")
        close_proximity = (
            close_proximity_raw is True
            or str(close_proximity_raw).lower() in {"1", "true", "yes", "on"}
        )
        conventional_valid = True
        if material != "copper":
            warnings.append("TN常规法当前只接入铜芯导体。")
            conventional_valid = False
        if (
            phase_section is None
            or protective_section is None
            or phase_section <= 0
            or protective_section <= 0
        ):
            warnings.append("TN常规法缺少有效的相导体或PE导体截面。")
            conventional_valid = False
        elif max(phase_section, protective_section) > 120:
            warnings.append("TN常规法忽略电抗的当前适用范围为电缆截面不超过120mm²。")
            conventional_valid = False
        if length_m is None or length_m <= 0:
            warnings.append("TN常规法必须填写大于0的线路长度。")
            conventional_valid = False
        if not close_proximity:
            warnings.append("TN常规法只适用于相导体与PE导体在同一电缆内或彼此靠近的线路。")
            conventional_valid = False
        if conventional_valid and voltage is not None and voltage > 0:
            assert phase_section is not None and protective_section is not None
            assert length_m is not None
            copper_resistivity = 0.0237
            conventional_loop_resistance = copper_resistivity * length_m * (
                1 / phase_section + 1 / protective_section
            )
            conventional_fault_current = 0.8 * voltage / conventional_loop_resistance
            outputs["conventional_method"] = {
                "method": "TN常规法（保守暂算）",
                "phase_section_mm2": phase_section,
                "protective_section_mm2": protective_section,
                "length_m": length_m,
                "copper_resistivity_ohm_mm2_per_m": copper_resistivity,
                "voltage_factor": 0.8,
                "line_loop_resistance_ohm": round(conventional_loop_resistance, 6),
                "source": "Schneider Electric, Electrical Installation Guide 2018",
                "section": "F 5.3, Conventional method",
                "page": "PDF第185页（F15）",
                "applicability": "相导体与PE导体在同一电缆内或彼此靠近；截面不超过120mm²",
            }
            steps.extend([
                Step(
                    "线路相—PE回路电阻",
                    "ρ×L×(1/Sph+1/SPE)",
                    round(conventional_loop_resistance, 6),
                    "Ω",
                ),
                Step(
                    "TN常规法最小接地故障电流",
                    "0.8×U₀/Rloop",
                    round(conventional_fault_current, 4),
                    "A",
                ),
            ])
    if conventional_fault_current is None:
        if loop_impedance is None or loop_impedance <= 0:
            warnings.append(
                "请填写有来源的故障回路阻抗，提供 fault_loop_data，"
                "或提供适用的 conventional_method_data。"
            )
        elif not loop_reference:
            warnings.append("故障回路阻抗必须填写 fault_loop_impedance_reference 说明来源。")

    # In design mode the user should not need to know Ia in advance.  Once Zs
    # is available, expose the maximum Ia that a future protective device may
    # have; an actual product curve can be checked later in expert mode.
    if voltage is not None and voltage > 0:
        if conventional_fault_current is not None:
            outputs["fault_current_calculation_method"] = "tn_conventional"
            outputs["maximum_permitted_operating_current_a"] = round(
                conventional_fault_current, 4
            )
            outputs["prospective_earth_fault_current_a"] = round(
                conventional_fault_current, 4
            )
        elif loop_impedance is not None and loop_impedance > 0 and loop_reference:
            prospective_fault_current = voltage / loop_impedance
            outputs.update({
                "fault_current_calculation_method": "complete_loop_impedance",
                "fault_loop_impedance_ohm": loop_impedance,
                "fault_loop_impedance_reference": loop_reference,
            })
            outputs["maximum_permitted_operating_current_a"] = round(
                prospective_fault_current, 4
            )
            outputs["prospective_earth_fault_current_a"] = round(
                prospective_fault_current, 4
            )
        if "maximum_permitted_operating_current_a" in outputs:
            outputs["operating_current_design_note"] = (
                "所选保护器件在规定切断时间内的动作电流Ia不得大于该值；"
                "实际Ia由产品时间—电流曲线或整定值复核。"
            )

    operating_current = _number(data, "protective_device_operating_current_a")
    operating_reference = str(data.get("protective_device_operating_reference") or "").strip()
    characteristic_code = str(data.get("protective_device_characteristic") or "manual")
    characteristic_rating = _number(data, "protective_device_rated_current_a")
    if operating_current is None and characteristic_code in {"mcb_b", "mcb_c"}:
        rule_codes.append("ELEC.BREAKER.MCB.INSTANTANEOUS")
        if characteristic_rating is None or characteristic_rating <= 0:
            warnings.append("自动取得MCB动作电流时必须提供大于0的断路器额定电流In。")
        else:
            multiplier = 5.0 if characteristic_code == "mcb_b" else 10.0
            operating_current = characteristic_rating * multiplier
            curve_name = "B" if characteristic_code == "mcb_b" else "C"
            operating_reference = (
                "《工业与民用供配电设计手册（第四版）》表11.3-4、表11.3-5，"
                "PDF第1010页（印刷第978页）"
            )
            outputs["protective_device_characteristic"] = {
                "device_family": "MCB",
                "curve": curve_name,
                "rated_current_a": characteristic_rating,
                "guaranteed_instantaneous_multiplier": multiplier,
                "operating_current_a": operating_current,
                "operating_time_s": "<0.1",
                "source": operating_reference,
                "selection_note": "按瞬时脱扣范围上限保守取得Ia",
            }
            steps.append(
                Step(
                    "MCB保证瞬时动作电流Ia",
                    f"{multiplier:g}×In",
                    round(operating_current, 4),
                    "A",
                )
            )
    elif operating_current is None and characteristic_code not in {"", "manual"}:
        warnings.append("当前只能自动取得MCB B型或C型的Ia；其他特性须按具体产品曲线填写。")
    if operating_current is None or operating_current <= 0:
        warnings.append(
            "请选择已核实的MCB B/C特性，或按保护器件在规定时间内的动作曲线填写"
            " protective_device_operating_current_a；不能只凭额定电流猜测。"
        )
    elif not operating_reference:
        warnings.append("保护器件动作电流必须填写 protective_device_operating_reference 说明曲线或产品资料来源。")

    protection_type = str(data.get("protection_type") or "")
    if protection_type not in {"overcurrent", "rcd"}:
        warnings.append("protection_type 仅支持 overcurrent 或 rcd。")
    elif protection_type == "rcd":
        rule_codes.append("ELEC.EARTH_FAULT.RCD.TN_ARRANGEMENT")
    rcd_arrangement_ok: bool | None = None
    if protection_type == "rcd" and earthing_system == "TN-C-S":
        raw_split = data.get("rcd_downstream_of_pen_split")
        rcd_arrangement_ok = raw_split is True or str(raw_split).lower() in {"1", "true", "yes", "on"}
        outputs["rcd_downstream_of_pen_split"] = rcd_arrangement_ok
        if not rcd_arrangement_ok:
            warnings.append("TN-C-S采用RCD时，必须确认RCD位于N线与PE线分开后的部分。")

    loop_method_ready = (
        conventional_fault_current is not None
        or (
            loop_impedance is not None
            and loop_impedance > 0
            and bool(loop_reference)
        )
    )
    ready = (
        earthing_system in {"TN-S", "TN-C-S"}
        and voltage is not None
        and voltage > 0
        and max_time is not None
        and loop_method_ready
        and operating_current is not None
        and operating_current > 0
        and bool(operating_reference)
        and protection_type in {"overcurrent", "rcd"}
        and rcd_arrangement_ok is not False
    )
    provisional = UNKNOWN
    if ready:
        assert voltage is not None and operating_current is not None
        outputs.update({
            "earthing_system": earthing_system,
            "nominal_line_to_earth_voltage_v": voltage,
            "protective_device_operating_current_a": operating_current,
            "protective_device_operating_reference": operating_reference,
            "protection_type": protection_type,
        })
        if conventional_fault_current is not None:
            assert conventional_loop_resistance is not None
            maximum_line_loop_resistance = 0.8 * voltage / operating_current
            provisional = PASS if conventional_fault_current >= operating_current else FAIL
            outputs.update({
                "fault_current_calculation_method": "tn_conventional",
                "prospective_earth_fault_current_a": round(conventional_fault_current, 4),
                "maximum_permitted_line_loop_resistance_ohm": round(
                    maximum_line_loop_resistance, 6
                ),
                "provisional_status": provisional,
            })
            steps.append(
                Step(
                    "规定时间内自动切断条件",
                    "If,min≥Ia",
                    f"{conventional_fault_current:.4f}≥{operating_current:g}",
                    "A",
                )
            )
        else:
            assert loop_impedance is not None
            fault_current = voltage / loop_impedance
            maximum_loop_impedance = voltage / operating_current
            impedance_product = loop_impedance * operating_current
            provisional = PASS if impedance_product <= voltage else FAIL
            outputs.update({
                "fault_current_calculation_method": "complete_loop_impedance",
                "fault_loop_impedance_ohm": loop_impedance,
                "fault_loop_impedance_reference": loop_reference,
                "prospective_earth_fault_current_a": round(fault_current, 4),
                "maximum_permitted_loop_impedance_ohm": round(maximum_loop_impedance, 6),
                "zs_times_ia_v": round(impedance_product, 4),
                "provisional_status": provisional,
            })
            steps.extend([
                Step("预期接地故障电流", "U₀/Zs", round(fault_current, 4), "A"),
                Step("允许的最大故障回路阻抗", "U₀/Ia", round(maximum_loop_impedance, 6), "Ω"),
                Step("自动切断条件", "Zs×Ia≤U₀", f"{impedance_product:.4f}≤{voltage:g}", "V"),
            ])

    status = _final_status(provisional, rule_codes, rules, warnings)
    return Outcome("TN接地故障与自动切断", "0.1.0", status, provisional, outputs, steps, warnings, rule_codes)


def calculate_cable_fault_loop_impedance(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
) -> Outcome:
    """Calculate the traceable impedance components of a copper L-PE/PEN loop.

    The handbook resistance equations are used only for the conductor resistance
    component.  The function deliberately does not assume a generic cable
    reactance or that AC resistance equals DC resistance.
    """
    rule_codes = [
        "ELEC.CABLE.FAULT_LOOP.RESISTANCE",
        "ELEC.CABLE.FAULT_LOOP.REACTANCE",
    ]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "scope": "铜芯相导体—PE/PEN故障回路阻抗分量",
        "method": "设计手册导体电阻公式；电抗及交流电阻修正须有独立来源",
    }

    material = str(data.get("conductor_material") or "").lower()
    phase_section = _number(data, "phase_section_mm2")
    protective_section = _number(data, "protective_section_mm2")
    length_m = _number(data, "length_m")
    temperature = _number(data, "conductor_temperature_c")
    phase_form = str(data.get("phase_conductor_form") or "").lower()
    protective_form = str(data.get("protective_conductor_form") or "").lower()
    structure_code = str(data.get("cable_structure_code") or "")
    structure: dict[str, Any] | None = None
    if structure_code and phase_section is not None:
        structure = lookup_yjv_fault_loop_structure(structure_code, phase_section)
        if structure:
            protective_section = float(structure["protective_section_mm2"])
            outputs["structure_catalog"] = structure
            rule_codes.append("ELEC.CABLE.YJV.STRUCTURE")
        else:
            warnings.append("所选YJV结构或截面尚未进入已核实的圆形线芯结构目录。")

    missing: list[str] = []
    if material != "copper":
        missing.append("conductor_material=copper")
    for key, value in (
        ("phase_section_mm2", phase_section),
        ("protective_section_mm2", protective_section),
        ("length_m", length_m),
        ("conductor_temperature_c", temperature),
    ):
        if value is None:
            missing.append(key)
    if phase_form not in {"solid", "stranded"}:
        missing.append("phase_conductor_form")
    if protective_form not in {"solid", "stranded"}:
        missing.append("protective_conductor_form")
    if missing:
        warnings.append("缺少或不支持的导体电阻输入：" + "、".join(missing) + "。")
        return Outcome("电缆故障回路阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    assert phase_section is not None and protective_section is not None
    assert length_m is not None and temperature is not None
    if phase_section <= 0 or protective_section <= 0:
        warnings.append("相导体和保护导体截面必须大于0。")
        return Outcome("电缆故障回路阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if length_m < 0:
        warnings.append("length_m 不能小于0。")
        return Outcome("电缆故障回路阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if not -50 <= temperature <= 250:
        warnings.append("conductor_temperature_c 超出本模块允许的核对范围（-50～250℃）。")
        return Outcome("电缆故障回路阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    rho20 = 0.0172
    alpha = 0.004
    rho_theta = rho20 * (1 + alpha * (temperature - 20))
    phase_stranding = 1.0 if phase_form == "solid" else 1.02
    protective_stranding = 1.0 if protective_form == "solid" else 1.02
    phase_dc_r_per_km = rho_theta * phase_stranding * 1000 / phase_section
    protective_dc_r_per_km = rho_theta * protective_stranding * 1000 / protective_section
    phase_dc_r = phase_dc_r_per_km * length_m / 1000
    protective_dc_r = protective_dc_r_per_km * length_m / 1000

    outputs.update({
        "conductor_material": "copper",
        "rho20_ohm_mm2_per_m": rho20,
        "temperature_coefficient_per_c": alpha,
        "conductor_temperature_c": temperature,
        "rho_theta_ohm_mm2_per_m": round(rho_theta, 8),
        "phase_conductor": {
            "section_mm2": phase_section,
            "form": phase_form,
            "stranding_factor": phase_stranding,
            "dc_resistance_ohm_per_km": round(phase_dc_r_per_km, 6),
            "dc_resistance_ohm": round(phase_dc_r, 6),
        },
        "protective_conductor": {
            "section_mm2": protective_section,
            "form": protective_form,
            "stranding_factor": protective_stranding,
            "dc_resistance_ohm_per_km": round(protective_dc_r_per_km, 6),
            "dc_resistance_ohm": round(protective_dc_r, 6),
        },
        "line_loop_dc_resistance_ohm": round(phase_dc_r + protective_dc_r, 6),
        "resistance_reference": "《工业与民用供配电设计手册（第四版）》9.4.1.1，PDF第893页（印刷第861页）",
    })
    steps.extend([
        Step("温度下电阻率", "ρ20×[1+α×(θ-20)]", round(rho_theta, 8), "Ω·mm²/m"),
        Step(
            "相导体直流电阻",
            f"{rho_theta:.8f}×{phase_stranding:g}×{length_m:g}/{phase_section:g}",
            round(phase_dc_r, 6),
            "Ω",
        ),
        Step(
            "保护导体直流电阻",
            f"{rho_theta:.8f}×{protective_stranding:g}×{length_m:g}/{protective_section:g}",
            round(protective_dc_r, 6),
            "Ω",
        ),
    ])

    fault_multiplier = _number(data, "fault_resistance_multiplier")
    fault_multiplier_reference = str(
        data.get("fault_resistance_multiplier_reference") or ""
    ).strip()
    if fault_multiplier is not None or fault_multiplier_reference:
        if (
            fault_multiplier is None
            or fault_multiplier < 1
            or not fault_multiplier_reference
        ):
            warnings.append(
                "故障计算电阻倍率必须不小于1，并同时提供来源。"
            )
            return Outcome("电缆故障回路阻抗", "0.2.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
        line_loop_r = (phase_dc_r + protective_dc_r) * fault_multiplier
        outputs.update({
            "fault_resistance_multiplier": fault_multiplier,
            "fault_resistance_multiplier_reference": fault_multiplier_reference,
            "line_loop_effective_resistance_ohm": round(line_loop_r, 6),
            # 保留旧字段供现有分段链读取；method字段明确其不是交流电阻系数。
            "line_loop_ac_resistance_ohm": round(line_loop_r, 6),
            "resistance_calculation_method": "fault_resistance_multiplier",
        })
        resistance_step_expression = (
            f"(R相,20+RPE,20)×{fault_multiplier:g}"
        )
    else:
        phase_ac_factor = _number(data, "phase_ac_resistance_factor")
        protective_ac_factor = _number(data, "protective_ac_resistance_factor")
        ac_factor_reference = str(data.get("ac_resistance_factor_reference") or "").strip()
        if (
            phase_ac_factor is None
            or protective_ac_factor is None
            or phase_ac_factor < 1
            or protective_ac_factor < 1
            or not ac_factor_reference
        ):
            warnings.append(
                "尚缺有来源的交流电阻修正系数（集肤与邻近效应），或有来源的故障计算电阻倍率；当前只输出温度修正后的直流电阻分量。"
            )
            return Outcome("电缆故障回路阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

        phase_ac_r = phase_dc_r * phase_ac_factor
        protective_ac_r = protective_dc_r * protective_ac_factor
        line_loop_r = phase_ac_r + protective_ac_r
        outputs.update({
            "phase_ac_resistance_factor": phase_ac_factor,
            "protective_ac_resistance_factor": protective_ac_factor,
            "ac_resistance_factor_reference": ac_factor_reference,
            "line_loop_ac_resistance_ohm": round(line_loop_r, 6),
            "resistance_calculation_method": "ac_resistance_factors",
        })
        resistance_step_expression = "R相,dc×K相+RPE,dc×KPE"
    steps.append(
        Step(
            "线路故障回路有效电阻",
            resistance_step_expression,
            round(line_loop_r, 6),
            "Ω",
        )
    )

    loop_x_per_km = _number(data, "loop_reactance_ohm_per_km")
    loop_x_reference = str(data.get("loop_reactance_reference") or "").strip()
    reactance_method = "explicit"
    phase_radius_cm = _number(data, "phase_conductor_radius_cm")
    protective_radius_cm = _number(data, "protective_conductor_radius_cm")
    phase_pe_distance_cm = _number(data, "phase_pe_center_distance_cm")
    frequency_hz = _number(data, "frequency_hz")
    geometry_reference = str(data.get("cable_geometry_reference") or "").strip()
    if structure:
        phase_radius_cm = float(structure["phase_conductor_radius_cm"])
        protective_radius_cm = float(structure["protective_conductor_radius_cm"])
        phase_pe_distance_cm = float(structure["phase_pe_center_distance_cm"])
        geometry_reference = (
            f"{structure['source']}，{structure['table']}，{structure['page']}"
        )
    geometry_values_present = any(
        value is not None for value in (phase_radius_cm, protective_radius_cm, phase_pe_distance_cm)
    )
    if loop_x_per_km is None and geometry_values_present:
        if frequency_hz is None:
            frequency_hz = 50.0
        geometry_valid = (
            phase_radius_cm is not None
            and protective_radius_cm is not None
            and phase_pe_distance_cm is not None
            and phase_radius_cm > 0
            and protective_radius_cm > 0
            and phase_pe_distance_cm > max(0.778 * phase_radius_cm, 0.778 * protective_radius_cm)
            and frequency_hz > 0
            and bool(geometry_reference)
        )
        if geometry_valid:
            assert phase_radius_cm is not None and protective_radius_cm is not None
            assert phase_pe_distance_cm is not None and frequency_hz is not None
            phase_l_h_per_km = 2e-4 * log(phase_pe_distance_cm / (0.778 * phase_radius_cm))
            protective_l_h_per_km = 2e-4 * log(
                phase_pe_distance_cm / (0.778 * protective_radius_cm)
            )
            phase_x_per_km = 2 * pi * frequency_hz * phase_l_h_per_km
            protective_x_per_km = 2 * pi * frequency_hz * protective_l_h_per_km
            loop_x_per_km = phase_x_per_km + protective_x_per_km
            reactance_method = "geometry"
            loop_x_reference = (
                "《工业与民用供配电设计手册（第四版）》9.4.1.2式(9.4-6)～(9.4-8)，"
                f"结构尺寸来源[{geometry_reference}]"
            )
            outputs["reactance_geometry"] = {
                "frequency_hz": frequency_hz,
                "phase_conductor_radius_cm": phase_radius_cm,
                "protective_conductor_radius_cm": protective_radius_cm,
                "phase_pe_center_distance_cm": phase_pe_distance_cm,
                "phase_reactance_ohm_per_km": round(phase_x_per_km, 6),
                "protective_reactance_ohm_per_km": round(protective_x_per_km, 6),
                "geometry_reference": geometry_reference,
            }
        else:
            warnings.append(
                "按几何尺寸计算电抗时，须提供大于0的相线/PE半径、两线芯中心距、频率及结构尺寸来源。"
            )
    upstream_r = _number(data, "upstream_resistance_ohm")
    upstream_x = _number(data, "upstream_reactance_ohm")
    upstream_reference = str(data.get("upstream_impedance_reference") or "").strip()
    transformer_phase_pe_data = data.get("upstream_transformer_phase_pe_data")
    if (
        upstream_r is None
        and upstream_x is None
        and isinstance(transformer_phase_pe_data, dict)
    ):
        transformer_phase_pe = calculate_transformer_phase_pe_impedance(
            transformer_phase_pe_data, rules
        )
        outputs["upstream_transformer_phase_pe"] = transformer_phase_pe.to_dict()
        for code in transformer_phase_pe.rule_codes:
            if code not in rule_codes:
                rule_codes.append(code)
        equivalent = transformer_phase_pe.outputs.get("transformer_phase_pe_equivalent")
        if isinstance(equivalent, dict):
            upstream_r = float(equivalent["phase_pe_resistance_ohm"])
            upstream_x = float(equivalent["phase_pe_reactance_ohm"])
            upstream_reference = (
                f"{equivalent['source']}，{equivalent['table']}，{equivalent['page']}"
            )
        else:
            warnings.extend(transformer_phase_pe.warnings)
    if loop_x_per_km is None or loop_x_per_km < 0 or not loop_x_reference:
        warnings.append("尚缺可计算的电缆结构尺寸或有来源的相线—PE/PEN回路电抗，不能用固定0.08Ω/km代替。")
    if upstream_r is None or upstream_x is None or upstream_r < 0 or upstream_x < 0 or not upstream_reference:
        warnings.append("尚缺折算到回路电源点的上游R/X及来源，不能形成规范定义的完整Zs。")
    if (
        loop_x_per_km is None
        or loop_x_per_km < 0
        or not loop_x_reference
        or upstream_r is None
        or upstream_x is None
        or upstream_r < 0
        or upstream_x < 0
        or not upstream_reference
    ):
        return Outcome("电缆故障回路阻抗", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    line_loop_x = loop_x_per_km * length_m / 1000
    total_r = upstream_r + line_loop_r
    total_x = upstream_x + line_loop_x
    zs = sqrt(total_r**2 + total_x**2)
    resistance_reference_detail = (
        fault_multiplier_reference
        if fault_multiplier is not None
        else outputs["ac_resistance_factor_reference"]
    )
    calculation_reference = (
        "系统计算：手册9.4.1.1导体电阻；"
        f"有效电阻处理[{resistance_reference_detail}]；回路电抗[{loop_x_reference}]；"
        f"上游阻抗[{upstream_reference}]"
    )
    outputs.update({
        "line_loop_reactance_ohm_per_km": loop_x_per_km,
        "line_loop_reactance_ohm": round(line_loop_x, 6),
        "reactance_method": reactance_method,
        "loop_reactance_reference": loop_x_reference,
        "upstream_resistance_ohm": upstream_r,
        "upstream_reactance_ohm": upstream_x,
        "upstream_impedance_reference": upstream_reference,
        "fault_loop_total_resistance_ohm": round(total_r, 6),
        "fault_loop_total_reactance_ohm": round(total_x, 6),
        "fault_loop_impedance_ohm": round(zs, 6),
        "calculation_reference": calculation_reference,
    })
    steps.extend([
        Step("线路故障回路电抗", f"{loop_x_per_km:g}×{length_m:g}/1000", round(line_loop_x, 6), "Ω"),
        Step("故障回路总电阻", "R上游+R相+RPE", round(total_r, 6), "Ω"),
        Step("故障回路总电抗", "X上游+X线路回路", round(total_x, 6), "Ω"),
        Step("故障回路阻抗", "√(R总²+X总²)", round(zs, 6), "Ω"),
    ])
    status = _final_status(PASS, rule_codes, rules, warnings)
    return Outcome("电缆故障回路阻抗", "0.2.0", status, PASS, outputs, steps, warnings, rule_codes)


def _resolve_adiabatic_thermal_stress(
    data: dict[str, Any],
    warnings: list[str],
    steps: list[Step],
) -> tuple[float | None, str]:
    let_through = _number(data, "let_through_energy_a2s")
    let_through_reference = str(
        data.get("let_through_energy_reference") or ""
    ).strip()
    fault_current = _number(data, "prospective_fault_current_a")
    clearing_time = _number(data, "fault_clearing_time_s")
    if let_through is not None:
        if let_through <= 0:
            warnings.append("let_through_energy_a2s 必须大于0。")
        elif not let_through_reference:
            warnings.append(
                "填写保护器件I²t时必须同时填写 let_through_energy_reference。"
            )
        else:
            steps.append(
                Step(
                    "保护器件通过能量I²t",
                    "产品样本/曲线给定值",
                    round(let_through, 4),
                    "A²·s",
                )
            )
            return let_through, let_through_reference
        return None, ""

    if fault_current is None or fault_current <= 0:
        warnings.append(
            "未填写保护器件I²t时，必须填写大于0的 prospective_fault_current_a。"
        )
    if clearing_time is None or clearing_time <= 0:
        warnings.append(
            "尚未取得保护器件通过能量I²t或故障切除时间；"
            "当前只输出热稳定允许约束，不判定是否通过。"
        )
    elif clearing_time > 5:
        warnings.append("绝热法当前仅适用于故障切除时间不超过5s。")
    if (
        fault_current is not None
        and fault_current > 0
        and clearing_time is not None
        and 0 < clearing_time <= 5
    ):
        thermal_stress = fault_current**2 * clearing_time
        steps.append(
            Step(
                "故障热应力I²t",
                "I²×t",
                round(thermal_stress, 4),
                "A²·s",
            )
        )
        return thermal_stress, "按本次输入的故障电流与切除时间计算"
    return None, ""


def _adiabatic_limits(
    section_mm2: float,
    k_a_sqrt_s_per_mm2: float,
    thermal_stress_a2s: float,
) -> tuple[float, float]:
    return (
        k_a_sqrt_s_per_mm2**2 * section_mm2**2,
        sqrt(thermal_stress_a2s) / k_a_sqrt_s_per_mm2,
    )


def calculate_pe_thermal_withstand(
    data: dict[str, Any], rules: dict[str, dict[str, Any]]
) -> Outcome:
    """Check a copper PE conductor by the adiabatic I²t method.

    This is deliberately an expert calculation path. It does not infer clearing
    time or let-through energy from a breaker rating: either a traceable I²t
    value from the protective-device manufacturer or both fault current and
    clearing time must be supplied.
    """
    rule_codes = ["ELEC.PE.THERMAL.WITHSTAND"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "scope": "铜PE导体短路热稳定绝热法暂算（t≤5s）",
        "formal_status_note": "规则未批准时，只输出暂算结果。",
    }

    section = _number(data, "protective_conductor_section_mm2")
    material = str(data.get("protective_conductor_material") or "").lower()
    insulation = str(data.get("protective_conductor_insulation") or "").lower()
    arrangement = str(data.get("protective_conductor_arrangement") or "").lower()
    if section is None or section <= 0:
        warnings.append("必须填写大于0的 protective_conductor_section_mm2。")
    if material != "copper":
        warnings.append("当前PE热稳定暂算只接入铜导体。")

    k_values = {
        ("pvc", "single_or_bare"): 143.0,
        ("pvc", "multicore_cable"): 115.0,
        ("xlpe", "single_or_bare"): 176.0,
        ("xlpe", "multicore_cable"): 143.0,
    }
    k = k_values.get((insulation, arrangement))
    if k is None:
        warnings.append(
            "必须确认PE绝缘类型（pvc/xlpe）和结构（single_or_bare/multicore_cable），不能猜测k值。"
        )

    fault_current = _number(data, "prospective_fault_current_a")
    thermal_stress, stress_source = _resolve_adiabatic_thermal_stress(
        data, warnings, steps
    )

    base_ready = (
        section is not None
        and section > 0
        and material == "copper"
        and k is not None
    )
    if base_ready:
        assert section is not None and k is not None
        permitted_energy = k**2 * section**2
        outputs.update({
            "protective_conductor_section_mm2": section,
            "protective_conductor_material": "copper",
            "protective_conductor_insulation": insulation,
            "protective_conductor_arrangement": arrangement,
            "k_a_sqrt_s_per_mm2": k,
            "permitted_thermal_stress_a2s": round(permitted_energy, 4),
            "maximum_permitted_let_through_energy_a2s": round(
                permitted_energy, 4
            ),
            "source": "Schneider Electric, Electrical Installation Guide 2018",
            "section": "G 5.2, Fig. G52; G 6.2, Fig. G59–G60",
            "pages": "PDF第259、262～263页（G33、G36～G37）",
        })
        if fault_current is not None and fault_current > 0:
            thermal_time_limit = permitted_energy / fault_current**2
            outputs.update({
                "prospective_fault_current_a": round(fault_current, 4),
                "calculated_thermal_time_limit_s": round(
                    thermal_time_limit, 6
                ),
                "maximum_permitted_clearing_time_s": round(
                    min(thermal_time_limit, 5.0), 6
                ),
                "clearing_time_governing_basis": (
                    "绝热法当前适用上限5s"
                    if thermal_time_limit > 5.0
                    else "导体允许热应力"
                ),
                "thermal_constraint_note": (
                    "实际保护器件的切除时间不得大于该值，或产品通过能量I²t不得大于允许值。"
                ),
            })

    if not base_ready or thermal_stress is None:
        return Outcome("PE导体热稳定", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    assert section is not None and k is not None
    permitted_energy, required_section = _adiabatic_limits(
        section, k, thermal_stress
    )
    provisional = PASS if thermal_stress <= permitted_energy else FAIL
    outputs.update({
        "protective_conductor_section_mm2": section,
        "protective_conductor_material": "copper",
        "protective_conductor_insulation": insulation,
        "protective_conductor_arrangement": arrangement,
        "k_a_sqrt_s_per_mm2": k,
        "actual_thermal_stress_a2s": round(thermal_stress, 4),
        "thermal_stress_source": stress_source,
        "permitted_thermal_stress_a2s": round(permitted_energy, 4),
        "required_protective_conductor_section_mm2": round(
            required_section, 6
        ),
        "provisional_status": provisional,
        "source": "Schneider Electric, Electrical Installation Guide 2018",
        "section": "G 5.2, Fig. G52; G 6.2, Fig. G59–G60",
        "pages": "PDF第259、262～263页（G33、G36～G37）",
    })
    steps.append(
        Step("PE允许热应力", "k²×SPE²", round(permitted_energy, 4), "A²·s")
    )
    steps.append(
        Step(
            "PE热稳定所需最小截面",
            "√(I²t)/k",
            round(required_section, 6),
            "mm²",
        )
    )
    steps.append(Step("PE热稳定校核", "I²t≤k²×SPE²", provisional))
    status = _final_status(provisional, rule_codes, rules, warnings)
    return Outcome("PE导体热稳定", "0.1.0", status, provisional, outputs, steps, warnings, rule_codes)


def calculate_phase_conductor_thermal_withstand(
    data: dict[str, Any], rules: dict[str, dict[str, Any]]
) -> Outcome:
    """Check a copper phase conductor using the common adiabatic I²t kernel."""

    rule_codes = ["ELEC.PHASE.THERMAL.WITHSTAND"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "scope": "铜相导体短路热稳定绝热法暂算（t≤5s）",
        "formal_status_note": "规则未批准时，只输出暂算结果。",
    }
    section = _number(data, "phase_conductor_section_mm2")
    material = str(data.get("phase_conductor_material") or "").lower()
    insulation = str(data.get("phase_conductor_insulation") or "").lower()
    if section is None or section <= 0:
        warnings.append("必须填写大于0的 phase_conductor_section_mm2。")
    if material != "copper":
        warnings.append("当前相导体热稳定暂算只接入铜导体。")

    k: float | None = None
    k_basis = ""
    if insulation == "pvc" and section is not None and section > 0:
        if section <= 300:
            k = 115.0
            k_basis = "铜芯PVC绝缘且截面≤300mm²"
        else:
            k = 103.0
            k_basis = "铜芯PVC绝缘且截面>300mm²"
    elif insulation == "xlpe":
        k = 143.0
        k_basis = "铜芯EPR/XLPE绝缘"
    else:
        warnings.append("必须确认相导体绝缘类型（pvc/xlpe），不能猜测k值。")

    fault_current = _number(data, "prospective_fault_current_a")
    thermal_stress, stress_source = _resolve_adiabatic_thermal_stress(
        data, warnings, steps
    )
    base_ready = (
        section is not None
        and section > 0
        and material == "copper"
        and k is not None
    )
    if base_ready:
        assert section is not None and k is not None
        permitted_energy = k**2 * section**2
        outputs.update({
            "phase_conductor_section_mm2": section,
            "phase_conductor_material": "copper",
            "phase_conductor_insulation": insulation,
            "k_a_sqrt_s_per_mm2": k,
            "k_basis": k_basis,
            "permitted_thermal_stress_a2s": round(permitted_energy, 4),
            "maximum_permitted_let_through_energy_a2s": round(
                permitted_energy, 4
            ),
            "source": "Schneider Electric, Electrical Installation Guide 2018",
            "section": "G 5.2, Fig. G52",
            "pages": "PDF第259页（G33）",
        })
        if fault_current is not None and fault_current > 0:
            thermal_time_limit = permitted_energy / fault_current**2
            outputs.update({
                "prospective_fault_current_a": round(fault_current, 4),
                "calculated_thermal_time_limit_s": round(
                    thermal_time_limit, 6
                ),
                "maximum_permitted_clearing_time_s": round(
                    min(thermal_time_limit, 5.0), 6
                ),
                "clearing_time_governing_basis": (
                    "绝热法当前适用上限5s"
                    if thermal_time_limit > 5.0
                    else "导体允许热应力"
                ),
                "thermal_constraint_note": (
                    "实际保护器件的切除时间不得大于该值，或产品通过能量I²t不得大于允许值。"
                ),
            })
    if not base_ready or thermal_stress is None:
        return Outcome(
            "相导体热稳定",
            "0.1.0",
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    assert section is not None and k is not None
    permitted_energy, required_section = _adiabatic_limits(
        section, k, thermal_stress
    )
    provisional = PASS if thermal_stress <= permitted_energy else FAIL
    outputs.update(
        {
            "phase_conductor_section_mm2": section,
            "phase_conductor_material": "copper",
            "phase_conductor_insulation": insulation,
            "k_a_sqrt_s_per_mm2": k,
            "k_basis": k_basis,
            "actual_thermal_stress_a2s": round(thermal_stress, 4),
            "thermal_stress_source": stress_source,
            "permitted_thermal_stress_a2s": round(permitted_energy, 4),
            "required_phase_conductor_section_mm2": round(
                required_section, 6
            ),
            "provisional_status": provisional,
            "source": "Schneider Electric, Electrical Installation Guide 2018",
            "section": "G 5.2, Fig. G52",
            "pages": "PDF第259页（G33）",
        }
    )
    steps.extend(
        [
            Step(
                "相导体允许热应力",
                "k²×S²",
                round(permitted_energy, 4),
                "A²·s",
            ),
            Step(
                "相导体热稳定所需最小截面",
                "√(I²t)/k",
                round(required_section, 6),
                "mm²",
            ),
            Step("相导体热稳定校核", "I²t≤k²×S²", provisional),
        ]
    )
    status = _final_status(provisional, rule_codes, rules, warnings)
    return Outcome(
        "相导体热稳定",
        "0.1.0",
        status,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def calculate_short_circuit(data: dict[str, Any], rules: dict[str, dict[str, Any]]) -> Outcome:
    rule_codes = ["ELEC.SHORT_CIRCUIT", "ELEC.BREAKING.CAPACITY"]
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {}
    if str(data.get("phase") or "") != "3":
        warnings.append("短路电流原型当前仅处理三相对称短路。")
        return Outcome("短路电流", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    voltage = _number(data, "voltage_v")
    values = {
        "source_r_ohm": _number(data, "source_r_ohm"),
        "source_x_ohm": _number(data, "source_x_ohm"),
        "transformer_r_ohm": _number(data, "transformer_r_ohm"),
        "transformer_x_ohm": _number(data, "transformer_x_ohm"),
        "cable_r_ohm_per_km": _number(data, "cable_r_ohm_per_km"),
        "cable_x_ohm_per_km": _number(data, "cable_x_ohm_per_km"),
        "length_m": _number(data, "length_m"),
    }
    missing = [key for key, value in values.items() if value is None]
    if voltage is None:
        missing.append("voltage_v")
    if missing:
        warnings.append("缺少必要输入：" + "、".join(missing))
        return Outcome("短路电流", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    assert voltage is not None
    assert all(value is not None for value in values.values())
    if voltage <= 0 or any(float(value) < 0 for value in values.values()):
        warnings.append("短路计算输入超出本原型允许范围。")
        return Outcome("短路电流", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    length_km = float(values["length_m"]) / 1000
    total_r = float(values["source_r_ohm"]) + float(values["transformer_r_ohm"]) + float(values["cable_r_ohm_per_km"]) * length_km
    total_x = float(values["source_x_ohm"]) + float(values["transformer_x_ohm"]) + float(values["cable_x_ohm_per_km"]) * length_km
    impedance = sqrt(total_r**2 + total_x**2)
    if impedance == 0:
        warnings.append("总阻抗为 0，无法计算短路电流。")
        return Outcome("短路电流", "0.1.0", UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    current_ka = voltage / (sqrt(3) * impedance) / 1000
    outputs.update(
        {
            "total_r_ohm": round(total_r, 6),
            "total_x_ohm": round(total_x, 6),
            "impedance_ohm": round(impedance, 6),
            "short_circuit_current_ka": round(current_ka, 4),
        }
    )
    steps.extend(
        [
            Step("总电阻", "R电源+R变压器+R线路", round(total_r, 6), "Ω"),
            Step("总电抗", "X电源+X变压器+X线路", round(total_x, 6), "Ω"),
            Step("总阻抗", "√(R²+X²)", round(impedance, 6), "Ω"),
            Step("三相短路电流", "U/(√3×|Z|)", round(current_ka, 4), "kA"),
        ]
    )
    breaking = _number(data, "breaking_capacity_ka")
    if breaking is None:
        warnings.append("未提供保护器件分断能力，无法完成分断能力校核。")
        provisional = UNKNOWN
    else:
        ok = breaking >= current_ka
        provisional = PASS if ok else FAIL
        outputs["breaking_capacity_check"] = provisional
        steps.append(Step("分断能力暂算", f"{breaking:g} ≥ {current_ka:.4f}", provisional))
    status = _final_status(provisional, rule_codes, rules, warnings)
    return Outcome("短路电流", "0.1.0", status, provisional, outputs, steps, warnings, rule_codes)


def calculate_all(data: dict[str, Any], rules: dict[str, dict[str, Any]]) -> list[Outcome]:
    load = calculate_load_and_selection(data, rules)
    current = load.outputs.get("design_current_a")
    return [
        load,
        calculate_voltage_drop(data, rules, current),
        calculate_short_circuit(data, rules),
    ]
