from __future__ import annotations

from math import sqrt
from typing import Any

from .catalog import (
    CONDUCTOR_CONFIGURATIONS,
    DEFAULT_CATALOG,
    INSTALLATION_SCENARIOS,
    lookup_yjv_fault_loop_structure,
    resolve_conductor_ampacity_basis,
)
from .engine import FAIL, Outcome, PASS, Step, UNKNOWN
from .protective_conductor import calculate_pe_minimum_section_by_table


ENGINE_VERSION = "1.16.0"


def _number(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _transformer_lv_short_circuit(
    data: dict[str, Any],
    catalog: dict[str, Any],
    outputs: dict[str, Any],
    steps: list[Step],
    warnings: list[str],
    rule_codes: list[str],
) -> float | None:
    if str(data.get("short_circuit_method") or "none") != "transformer_lv_table":
        return None

    table = catalog.get("transformer_lv_short_circuit", {})
    if table.get("status") not in {"verified", "approved"}:
        warnings.append("变压器低压出口短路电流速查表尚未核实，不能暂算。")
        return None

    capacity = _number(data, "transformer_capacity_kva")
    if capacity is None or capacity <= 0:
        warnings.append("请选择变压器容量，才能查取0.4kV低压出口短路电流。")
        return None
    capacity_key = int(capacity)
    capacity_rows = table.get("rows", {}).get(capacity_key)
    if capacity != capacity_key or not capacity_rows:
        warnings.append("所选变压器容量不在19DX101-1表15.7覆盖范围内。")
        return None

    base = {
        "method": "19DX101-1表15.7速查",
        "location": table["location"],
        "transformer_capacity_kva": capacity_key,
        "assumption": table["assumption"],
        "source": table["source"],
        "table": table["table"],
        "page": table["page"],
        "status": UNKNOWN,
    }
    rule_codes.append("ELEC.SHORT_CIRCUIT.TRANSFORMER_LV")
    uk = _number(data, "transformer_uk_percent")
    if uk is None:
        values = list(capacity_rows.values())
        ik_values = [float(item["ik_ka"]) for item in values]
        ip_values = [float(item["ip_ka"]) for item in values]
        adopted = max(ik_values)
        outputs["short_circuit_estimate"] = {
            **base,
            "mode": "range",
            "available_uk_percent": sorted(float(value) for value in capacity_rows),
            "ik_min_ka": min(ik_values),
            "ik_max_ka": max(ik_values),
            "ip_min_ka": min(ip_values),
            "ip_max_ka": max(ip_values),
            "adopted_for_breaking_capacity_ka": adopted,
        }
        steps.append(
            Step(
                "变压器低压出口短路电流范围",
                f"表15.7，{capacity_key} kVA，uk%待确认",
                f"{min(ik_values):g}～{max(ik_values):g}",
                "kA",
            )
        )
        warnings.append(
            "未提供变压器铭牌uk%，已显示表15.7覆盖范围；分断能力暂按范围上限初选，正式结果必须核对铭牌。"
        )
        return adopted

    selected = capacity_rows.get(float(uk))
    if selected is None:
        available = "、".join(f"{value:g}%" for value in sorted(capacity_rows))
        warnings.append(
            f"表15.7未列出{capacity_key} kVA、uk={uk:g}%组合；该容量可查值为{available}。"
        )
        return None

    ik = float(selected["ik_ka"])
    ip = float(selected["ip_ka"])
    outputs["short_circuit_estimate"] = {
        **base,
        "mode": "exact_table",
        "transformer_uk_percent": uk,
        "ik_ka": ik,
        "ip_ka": ip,
        "adopted_for_breaking_capacity_ka": ik,
    }
    steps.append(
        Step(
            "变压器低压出口三相短路电流",
            f"表15.7，{capacity_key} kVA，uk={uk:g}%",
            ik,
            "kA",
        )
    )
    return ik


def _breaker_design_candidates(
    current: float,
    prospective_short_circuit_ka: float | None,
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    parameters = catalog.get("breaker_parameters", {})
    if parameters.get("status") not in {"verified", "approved"}:
        return []

    candidates: list[dict[str, Any]] = []
    for code, family in parameters.get("families", {}).items():
        # 普通负荷快速页覆盖MCB和配电保护型MCCB。电动机专用D型MCB、
        # MA磁脱扣器等仍由电动机模块按制造商配合表处理。
        if code not in {"MCB", "MCCB"}:
            continue
        selected_group = None
        selected_rating = None
        for group in family.get("groups", []):
            rating = next(
                (float(value) for value in group.get("ratings_a", []) if float(value) >= current),
                None,
            )
            if rating is not None:
                selected_group = group
                selected_rating = rating
                break
        if selected_group is None or selected_rating is None:
            continue

        available_icu = [float(value) for value in selected_group.get("icu_ka", [])]
        selected_icu = None
        if prospective_short_circuit_ka is not None:
            selected_icu = next(
                (value for value in available_icu if value >= prospective_short_circuit_ka),
                None,
            )
        candidates.append({
            "family_code": code,
            "family_name": family["name"],
            "frame_rating_a": float(selected_group["frame_a"]),
            "frame_label": {
                "MCB": "电流规格等级",
                "MCCB": "壳架电流",
            }[code],
            "rated_current_a": selected_rating,
            "rated_voltage_v": family["rated_voltage_v"],
            "poles": "待按接地系统与保护要求确定",
            "available_pole_options": list(selected_group.get("pole_options", [])),
            "available_icu_ka": available_icu,
            "required_icu_ka": prospective_short_circuit_ka,
            "selected_icu_ka": selected_icu,
            "breaking_capacity_status": (
                "待输入安装点预期短路电流"
                if prospective_short_circuit_ka is None
                else ("满足表列档位" if selected_icu is not None else "超出表列档位")
            ),
            "source": parameters["source"],
            "table": family["table"],
            "page": family.get("page", parameters["page"]),
            "status": parameters["status"],
        })
    return candidates


def _automatic_conductor_basis(
    family: str,
    scenario: str,
    phase: str,
    configuration_code: str,
    catalog: dict[str, Any],
    soil_thermal_resistivity_k_m_per_w: float | None = None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    return resolve_conductor_ampacity_basis(
        family,
        scenario,
        phase,
        configuration_code,
        catalog,
        soil_thermal_resistivity_k_m_per_w,
    )


def _legacy_default_configuration(family: str, phase: str) -> str:
    return {
        ("BV", "1"): "bv_1ph_2wire_pe",
        ("BV", "3"): "bv_3ph_3wire_pe",
        ("YJV", "3"): "yjv_3c_3ph_pe",
    }.get((family, phase), "")


def calculate_simple_load_selection(
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """普通负荷单回路初选；单台设备不使用需要系数。"""
    catalog = catalog or DEFAULT_CATALOG
    basis = str(data.get("input_basis") or "")
    phase = str(data.get("phase") or "")
    circuit_role = str(data.get("circuit_role") or "single_device")
    power_definition = str(data.get("power_definition") or "design")
    family = str(data.get("conductor_family") or "")
    scenario = str(data.get("installation_scenario") or "")
    configuration_code = str(data.get("conductor_configuration") or "")
    if not configuration_code:
        configuration_code = _legacy_default_configuration(family, phase)
    configuration = CONDUCTOR_CONFIGURATIONS.get(configuration_code)
    value = _number(data, "input_value")
    voltage = _number(data, "voltage_v")
    power_factor_raw = data.get("power_factor")
    user_power_factor = _number(data, "power_factor")
    soil_thermal_resistivity = _number(
        data, "soil_thermal_resistivity_k_m_per_w"
    )
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "core_configuration": "待确认",
        "neutral_pe_configuration": "待确认",
        "breaker_poles": "待确认",
        "conductor_basis": "待确认",
        "circuit_role": circuit_role,
    }
    cable_rule = f"ELEC.CABLE.{family}.AMPACITY"
    rule_codes = [
        "ELEC.LOAD.CURRENT",
        "ELEC.BREAKER.RATING",
        "ELEC.BREAKING.CAPACITY",
        "ELEC.RCD.PARAMETERS",
        cable_rule,
    ]

    missing = []
    if basis not in {"kw", "kva", "current"}:
        missing.append("已知量")
    if value is None:
        missing.append("输入数值")
    if phase not in {"1", "3"}:
        missing.append("相制")
    if circuit_role not in {"single_device", "group_load", "feeder"}:
        missing.append("回路角色")
    if voltage is None:
        missing.append("电压")
    if family not in INSTALLATION_SCENARIOS:
        missing.append("导体类型")
    if configuration is None:
        missing.append("导体型号/结构")
    elif configuration["family"] != family or phase not in configuration["phases"]:
        missing.append("与相制匹配的导体结构")
    allowed_scenarios = {item[0] for item in INSTALLATION_SCENARIOS.get(family, ())}
    if scenario not in allowed_scenarios:
        missing.append("敷设场景")
    if missing:
        warnings.append("请填写：" + "、".join(dict.fromkeys(missing)) + "。")
        return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    assert value is not None and voltage is not None
    assert configuration is not None
    outputs.update({
        "conductor_configuration": {
            "code": configuration_code,
            "label": configuration["label"],
            "description": configuration["description"],
        },
        "core_configuration": configuration["label"],
        "breaker_poles": "待按接地系统与保护要求确定",
    })
    if value <= 0 or voltage <= 0:
        warnings.append("输入数值和电压必须大于 0。")
        return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    if power_factor_raw not in (None, "") and user_power_factor is None:
        warnings.append("输入的功率因数不是有效数字。")
        return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
    if user_power_factor is not None and not 0 < user_power_factor <= 1:
        warnings.append("功率因数必须大于 0 且不大于 1。")
        return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)

    calculation_value = value
    adopted_power_factor: float | None = user_power_factor
    if basis == "kw":
        rule_codes.insert(1, "ELEC.LOAD.POWER_FACTOR")
        load = catalog.get("load_types", {}).get(str(data.get("load_type_code") or ""))
        if not load:
            warnings.append("kW 模式必须选择具体负荷类型。")
            return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
        if circuit_role == "single_device" and phase not in load.get("phases", ()):
            warnings.append("所选设备子类不适用于当前相制。")
            return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
        if circuit_role != "single_device" and phase not in load.get("phases", ()):
            warnings.append("所选设备子类通常为单相；本次按三相回路中的同类负荷汇总处理，必须确认各相负荷分配基本平衡。")
            outputs["phase_application"] = "按三相均衡汇总，待确认相负荷分配"
        if power_definition not in {"design", "installed"}:
            warnings.append("请选择输入功率是计算功率还是安装功率。")
            return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
        if power_definition == "installed" and circuit_role == "single_device":
            # 单台设备回路按该台设备的输入功率承载。需要系数属于同类
            # 负荷汇总或馈线层的需求计算，不在此处折减设备回路电流。
            if data.get("demand_factor") not in (None, ""):
                warnings.append("单台设备回路不采用需要系数；已按输入功率计算。")
            outputs.update({
                "input_power_kw": value,
                "calculation_power_kw": round(calculation_value, 4),
                "power_definition": "single_device_input_power",
            })
            steps.append(
                Step("单台设备计算功率", "按输入功率；不使用需要系数", round(calculation_value, 4), "kW")
            )
        elif power_definition == "installed":
            demand_factor = _number(data, "demand_factor")
            if demand_factor is None:
                warnings.append("安装功率模式必须填写需要系数；系统不会猜测默认值。")
                return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            if not 0 < demand_factor <= 1:
                warnings.append("需要系数必须大于 0 且不大于 1。")
                return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
            calculation_value = value * demand_factor
            outputs.update({
                "installed_power_kw": value,
                "demand_factor": demand_factor,
                "calculation_power_kw": round(calculation_value, 4),
            })
            steps.append(
                Step("计算功率", f"{value:g} × {demand_factor:g}", round(calculation_value, 4), "kW")
            )
        pf = user_power_factor if user_power_factor is not None else load.get("pf_min")
        if pf is None:
            warnings.append("“其他 / 不确定”必须填写铭牌或厂家功率因数，才能由 kW 计算电流。")
            return Outcome("普通负荷快速计算", ENGINE_VERSION, UNKNOWN, UNKNOWN, outputs, steps, warnings, rule_codes)
        using_user_power_factor = user_power_factor is not None
        outputs["parameter"] = {
            "name": load["name"], "power_factor_min": load["pf_min"],
            "power_factor_max": load["pf_max"], "adopted_power_factor": pf,
            "conservative": not using_user_power_factor and load["pf_min"] != load["pf_max"],
            "adopted_source": "用户输入（应与铭牌或厂家资料核对）" if using_user_power_factor else "资料范围下限（保守初选）",
            "source": load["source"], "table": load["table"], "page": load["page"],
            "status": load["status"],
        }
        if using_user_power_factor:
            warnings.append("功率因数采用用户输入值；正式使用前应与设备铭牌或厂家资料核对。")
        adopted_power_factor = pf
        current = calculation_value * 1000 / ((sqrt(3) if phase == "3" else 1) * voltage * pf)
        expression = "P×1000/(√3×U×cosφ)" if phase == "3" else "P×1000/(U×cosφ)"
    elif basis == "kva":
        current = value * 1000 / ((sqrt(3) if phase == "3" else 1) * voltage)
        expression = "S×1000/(√3×U)" if phase == "3" else "S×1000/U"
    else:
        current = value
        expression = "I = 输入电流"
    steps.append(Step("计算电流" if basis != "current" else "采用已知电流", expression, round(current, 4), "A"))
    outputs.update({
        "input_basis": basis, "input_value": value, "phase": phase, "voltage_v": voltage,
        "design_current_a": round(current, 4), "required_breaker_rating_a": round(current, 4),
        "power_definition": power_definition,
    })

    prospective_short_circuit_ka = _transformer_lv_short_circuit(
        data, catalog, outputs, steps, warnings, rule_codes
    )
    prospective_short_circuit_raw = data.get("prospective_short_circuit_ka")
    if prospective_short_circuit_ka is None and prospective_short_circuit_raw not in (None, ""):
        prospective_short_circuit_ka = _number(data, "prospective_short_circuit_ka")
        if prospective_short_circuit_ka is None or prospective_short_circuit_ka <= 0:
            warnings.append("安装点预期短路电流必须是大于 0 的有效数值。")
            prospective_short_circuit_ka = None
        else:
            outputs["prospective_short_circuit_ka"] = prospective_short_circuit_ka

    breaker_design_candidates = _breaker_design_candidates(
        current,
        prospective_short_circuit_ka,
        catalog,
    )
    outputs["breaker_design_candidates"] = breaker_design_candidates
    provisional_breaker_rating = min(
        (item["rated_current_a"] for item in breaker_design_candidates),
        default=None,
    )
    if provisional_breaker_rating is not None:
        outputs["provisional_breaker_rating_a"] = provisional_breaker_rating
        steps.append(
            Step(
                "断路器额定电流参数候选",
                f"19DX101-1表列首个 In ≥ {current:.4f}",
                provisional_breaker_rating,
                "A",
            )
        )
    if prospective_short_circuit_ka is None:
        warnings.append("未提供安装点预期短路电流，不能确定断路器分断能力；当前仅列出19DX101-1表列Icu档位。")
    elif breaker_design_candidates and not any(
        item["selected_icu_ka"] is not None for item in breaker_design_candidates
    ):
        warnings.append("安装点预期短路电流已超过当前19DX101-1候选表列分断能力档位。")

    rcd_scenario = str(data.get("rcd_scenario") or "unknown")
    rcd_waveform = str(data.get("rcd_residual_waveform") or "unknown")
    rcd_catalog = catalog.get("rcd_parameters", {})
    rcd_parameter = rcd_catalog.get("scenarios", {}).get(rcd_scenario)
    if rcd_parameter and rcd_catalog.get("status") in {"verified", "approved"}:
        waveform = rcd_catalog.get("waveform_types", {}).get(rcd_waveform)
        outputs["rcd_requirement"] = {
            **rcd_parameter,
            "source": rcd_catalog["source"],
            "table": rcd_catalog["table"],
            "page": rcd_catalog["page"],
            "status": rcd_catalog["status"],
            "rcd_type": waveform["rcd_type"] if waveform else "待按负载电流波形确认",
            "residual_waveform": waveform["label"] if waveform else "负载剩余电流波形未确认",
            "selection_checks": list(rcd_catalog.get("selection_checks", [])),
        }
        if waveform is None:
            warnings.append("RCD类型必须按负载剩余电流波形确认；当前不猜测AC/A/F/B型。")
    else:
        outputs["rcd_requirement"] = {
            "name": "回路用途/场所未明确",
            "rated_residual_current_max_ma": None,
            "delay": "无法判断",
            "rcd_type": "无法判断",
            "residual_waveform": "无法判断",
            "selection_checks": [],
        }
        warnings.append("漏电保护参数不能由功率或计算电流单独确定；请补充回路用途/场所。")

    breaker_catalog = catalog.get("breaker_ratings", {})
    breaker_approved = (
        rules.get("ELEC.BREAKER.RATING", {}).get("status") == "approved"
        and breaker_catalog.get("status") in {"verified", "approved"}
    )
    breaker = next(
        (float(x) for x in breaker_catalog.get("ratings_a", []) if float(x) >= current),
        None,
    ) if breaker_approved else None
    if breaker is not None:
        outputs["breaker_candidate_rating_a"] = breaker
        outputs["breaker_reference"] = {
            key: breaker_catalog.get(key, "") for key in ("source", "table", "page")
        }
        steps.append(Step("断路器额定电流候选", f"首个 In ≥ {current:.4f}", breaker, "A"))
    elif breaker_design_candidates:
        warnings.append("19DX101-1断路器参数表已核实但尚未批准；当前候选仅供设计参数参考，不代表正式选型或产品型号。")
    else:
        warnings.append("断路器额定电流系列尚未批准、未核实完整产品目录或已超出目录；仅给出所需最小额定电流。")

    required_ampacity = (
        breaker
        if breaker is not None
        else (provisional_breaker_rating if provisional_breaker_rating is not None else current)
    )
    outputs["required_cable_ampacity_a"] = round(required_ampacity, 4)
    conductor = catalog.get("conductors", {}).get(family, {})
    conductor_basis, selected_basis, basis_note = _automatic_conductor_basis(
        family,
        scenario,
        phase,
        configuration_code,
        catalog,
        soil_thermal_resistivity,
    )
    if selected_basis:
        basis_rule_code = selected_basis.get("rule_code", cable_rule)
        if basis_rule_code not in rule_codes:
            rule_codes.append(basis_rule_code)
        outputs["conductor_basis"] = {
            "code": conductor_basis,
            "label": selected_basis["label"],
            "selection_method": "系统根据导体型号、敷设场景及回路相制自动匹配",
            "note": basis_note,
            "source": selected_basis.get("source", conductor.get("source", "")),
            "table": selected_basis.get("table", conductor.get("table", "")),
            "page": selected_basis.get("page", conductor.get("page", "")),
            "reference_condition": selected_basis.get(
                "reference_condition", conductor.get("reference_condition", "")
            ),
        }
    elif scenario != "unknown":
        if (
            family == "YJV"
            and configuration.get("label") == "三芯电缆"
            and scenario == "direct_buried"
            and soil_thermal_resistivity is None
        ):
            warnings.append(
                "YJV三芯埋地管槽必须选择土壤热阻系数，才能按19DX101-1表6.10查取基础载流量；系统不猜默认值。"
            )
        elif (
            family == "YJV"
            and configuration.get("label") == "三芯电缆"
            and scenario == "direct_buried"
        ):
            warnings.append(
                "所选土壤热阻系数不在19DX101-1表6.10的1.0、1.5、2.0、2.5 K·m/W档位内。"
            )
        else:
            warnings.append("当前核验目录没有覆盖该导体与敷设场景，不能自动查表初选截面。")

    basis_rule_code = selected_basis.get("rule_code", cable_rule) if selected_basis else cable_rule
    cable_approved = (
        rules.get(basis_rule_code, {}).get("status") == "approved"
        and conductor.get("status") == "approved"
    )
    cable_table_available = (
        selected_basis is not None
        and conductor.get("status") in {"verified", "approved"}
    )
    temperature_factor = 1.0
    temperature_confirmed = False
    temperature_raw = data.get("installation_temperature_c")
    if selected_basis and temperature_raw not in (None, ""):
        temperature = _number(data, "installation_temperature_c")
        temperature_catalog = catalog.get("temperature_derating", {})
        temperature_mode = "ground" if scenario == "direct_buried" else "air"
        temperature_table = temperature_catalog.get(temperature_mode, {})
        insulation_code = str(conductor.get("insulation_code") or "")
        temperature_factors = temperature_table.get("factors", {}).get(insulation_code, {})
        base_temperature = float(
            selected_basis.get(
                "base_temperature_c",
                conductor.get("base_temperature_c", 0),
            )
        )
        actual_table_factor = temperature_factors.get(temperature)
        base_table_factor = temperature_factors.get(base_temperature)
        if (
            temperature is None
            or actual_table_factor is None
            or base_table_factor in (None, 0)
            or temperature_catalog.get("status") not in {"verified", "approved"}
        ):
            cable_table_available = False
            warnings.append(
                "所选温度超出当前温度修正表覆盖范围，或基础载流量工况无法与修正表对应；"
                "不能给出修正后截面。"
            )
        else:
            temperature_factor = float(actual_table_factor) / float(base_table_factor)
            temperature_confirmed = True
            if "ELEC.CABLE.TEMPERATURE.DERATING" not in rule_codes:
                rule_codes.append("ELEC.CABLE.TEMPERATURE.DERATING")
            outputs["temperature_correction"] = {
                "mode": temperature_mode,
                "mode_label": "地下温度" if temperature_mode == "ground" else "环境空气温度",
                "actual_temperature_c": temperature,
                "base_temperature_c": base_temperature,
                "table_factor_at_actual_temperature": float(actual_table_factor),
                "table_factor_at_base_temperature": float(base_table_factor),
                "relative_factor": round(temperature_factor, 4),
                "source": temperature_catalog["source"],
                "table": temperature_table["table"],
                "title": temperature_table["title"],
                "page": temperature_catalog["page"],
                "status": temperature_catalog["status"],
            }
            steps.append(
                Step(
                    "温度修正系数",
                    (
                        f"{temperature_table['table']}："
                        f"k({temperature:g}℃)/k({base_temperature:g}℃)"
                    ),
                    round(temperature_factor, 4),
                    "",
                )
            )
    elif selected_basis:
        warnings.append(
            "未确认实际敷设环境温度；当前按基础载流量表的温度工况显示，"
            "尚未完成温度修正。"
        )

    tray_derating_factor = 1.0
    if scenario == "tray":
        tray_type = str(data.get("tray_type") or "")
        tray_layers = str(data.get("tray_layers") or "")
        tray_cables = str(data.get("tray_cables_per_layer") or "")
        tray_catalog = catalog.get("tray_derating", {})
        factor = tray_catalog.get(tray_type, {}).get(tray_layers, {}).get(tray_cables)
        if factor is None:
            cable_table_available = False
            warnings.append("槽盒条件未选择为已核实的水平有孔工况，或层数、每层线缆数超出表6.25覆盖范围，不能给出修正后截面。")
        else:
            tray_derating_factor = float(factor)
            if "ELEC.CABLE.TRAY.GROUPING" not in rule_codes:
                rule_codes.append("ELEC.CABLE.TRAY.GROUPING")
            outputs["tray_configuration"] = {
                "type": tray_type,
                "layers": int(tray_layers),
                "cables_per_layer": int(tray_cables),
                "derating_factor": tray_derating_factor,
                "source": tray_catalog["source"],
                "table": tray_catalog["table"],
                "page": tray_catalog["page"],
            }
            steps.append(Step("槽盒线缆束降额系数", f"{tray_layers}层，每层{tray_cables}根", tray_derating_factor, ""))
    enclosed_grouping_factor = 1.0
    enclosed_grouping_confirmed = False
    enclosed_grouping_requires_rule = False
    if scenario == "conduit":
        enclosed_count_raw = data.get("enclosed_grouping_circuit_count")
        enclosed_count = _number(data, "enclosed_grouping_circuit_count")
        if enclosed_count_raw in (None, ""):
            warnings.append(
                "未确认同束/封闭通道内回路数；当前仍显示基础截面，尚未完成成组修正。"
            )
        elif (
            enclosed_count is None
            or enclosed_count != int(enclosed_count)
        ):
            warnings.append("同束/封闭通道内回路数必须选择表6.26列出的整数档位。")
        elif int(enclosed_count) == 1:
            enclosed_grouping_confirmed = True
            outputs["enclosed_grouping"] = {
                "circuit_count": 1,
                "derating_factor": 1.0,
                "source": "单回路，无多回路成束降额",
                "status": "confirmed",
            }
        else:
            grouping_catalog = catalog.get("enclosed_grouping", {})
            factor = grouping_catalog.get("factors", {}).get(int(enclosed_count))
            if (
                factor is None
                or grouping_catalog.get("status") not in {"verified", "approved"}
            ):
                warnings.append(
                    "所选回路数不在表6.26当前接入的1～9、12、16、20档位内。"
                )
            else:
                enclosed_grouping_factor = float(factor)
                enclosed_grouping_confirmed = True
                enclosed_grouping_requires_rule = True
                if "ELEC.CABLE.ENCLOSED.GROUPING" not in rule_codes:
                    rule_codes.append("ELEC.CABLE.ENCLOSED.GROUPING")
                outputs["enclosed_grouping"] = {
                    "circuit_count": int(enclosed_count),
                    "derating_factor": enclosed_grouping_factor,
                    "source": grouping_catalog["source"],
                    "table": grouping_catalog["table"],
                    "title": grouping_catalog["title"],
                    "arrangement": grouping_catalog["arrangement"],
                    "application_note": grouping_catalog["application_note"],
                    "page": grouping_catalog["page"],
                    "status": grouping_catalog["status"],
                }
                steps.append(
                    Step(
                        "同束/封闭通道多回路降低系数",
                        f"{int(enclosed_count)}回路，电缆相互接触",
                        enclosed_grouping_factor,
                        "",
                    )
                )
    buried_grouping_factor = 1.0
    buried_grouping_confirmed = False
    buried_grouping_requires_rule = False
    if scenario == "direct_buried":
        circuit_count_raw = data.get("buried_circuit_count")
        circuit_count = _number(data, "buried_circuit_count")
        if circuit_count_raw in (None, ""):
            warnings.append(
                "未确认埋地管槽内同路径回路数；当前仍显示基础截面，尚未完成多回路成组修正。"
            )
        elif (
            circuit_count is None
            or circuit_count != int(circuit_count)
            or not 1 <= circuit_count <= 20
        ):
            warnings.append("埋地管槽内回路数必须是1～20的整数。")
        elif int(circuit_count) == 1:
            buried_grouping_confirmed = True
            outputs["buried_grouping"] = {
                "circuit_count": 1,
                "spacing": "仅本回路",
                "derating_factor": 1.0,
                "source": "单回路，无多回路成组降额",
                "status": "confirmed",
            }
        else:
            spacing = str(data.get("buried_duct_spacing_m") or "")
            burial_depth = _number(data, "buried_depth_m")
            grouping_catalog = catalog.get("buried_duct_grouping", {})
            factor = None
            if soil_thermal_resistivity == 2.5 and burial_depth == 0.7:
                factor = grouping_catalog.get("factors", {}).get(spacing, {}).get(
                    int(circuit_count)
                )
            if (
                factor is None
                or grouping_catalog.get("status") not in {"verified", "approved"}
            ):
                if soil_thermal_resistivity != 2.5 or burial_depth != 0.7:
                    warnings.append(
                        "表6.27多回路降低系数的参考条件为埋深0.7m、土壤热阻系数2.5 K·m/W；"
                        "当前条件不一致或未确认，不能直接套用该表。"
                    )
                else:
                    warnings.append(
                        "多回路埋地管槽必须选择管槽间距，且回路数、间距须在表6.27覆盖范围内。"
                    )
            else:
                buried_grouping_factor = float(factor)
                buried_grouping_confirmed = True
                buried_grouping_requires_rule = True
                if "ELEC.CABLE.BURIED_DUCT.GROUPING" not in rule_codes:
                    rule_codes.append("ELEC.CABLE.BURIED_DUCT.GROUPING")
                outputs["buried_grouping"] = {
                    "circuit_count": int(circuit_count),
                    "spacing": spacing,
                    "spacing_label": (
                        "无间距（电缆相互接触）"
                        if spacing == "touching"
                        else f"{spacing} m"
                    ),
                    "derating_factor": buried_grouping_factor,
                    "source": grouping_catalog["source"],
                    "table": grouping_catalog["table"],
                    "title": grouping_catalog["title"],
                    "page": grouping_catalog["page"],
                    "reference_condition": grouping_catalog["reference_condition"],
                    "status": grouping_catalog["status"],
                }
                steps.append(
                    Step(
                        "埋地管槽多回路降低系数",
                        (
                            f"{int(circuit_count)}回路，"
                            f"{outputs['buried_grouping']['spacing_label']}"
                        ),
                        buried_grouping_factor,
                        "",
                    )
                )
    rows = selected_basis.get("rows", []) if cable_table_available else []
    grouping_factor = (
        tray_derating_factor
        * enclosed_grouping_factor
        * buried_grouping_factor
    )
    combined_derating_factor = temperature_factor * grouping_factor
    temperature_rule_approved = (
        temperature_confirmed
        and rules.get("ELEC.CABLE.TEMPERATURE.DERATING", {}).get("status") == "approved"
    )
    cable_approved = cable_approved and temperature_rule_approved
    if scenario == "direct_buried":
        buried_grouping_rule_approved = (
            not buried_grouping_requires_rule
            or rules.get("ELEC.CABLE.BURIED_DUCT.GROUPING", {}).get("status")
            == "approved"
        )
        cable_approved = (
            cable_approved
            and buried_grouping_confirmed
            and buried_grouping_rule_approved
        )
    if scenario == "tray":
        cable_approved = (
            cable_approved
            and rules.get("ELEC.CABLE.TRAY.GROUPING", {}).get("status")
            == "approved"
        )
    if scenario == "conduit":
        enclosed_grouping_rule_approved = (
            not enclosed_grouping_requires_rule
            or rules.get("ELEC.CABLE.ENCLOSED.GROUPING", {}).get("status")
            == "approved"
        )
        cable_approved = (
            cable_approved
            and enclosed_grouping_confirmed
            and enclosed_grouping_rule_approved
        )
    ampacity_candidates = [
        {
            "family": family,
            "section_mm2": float(row["section_mm2"]),
            "base_ampacity_a": float(row["ampacity_a"]),
            "temperature_corrected_ampacity_a": round(
                float(row["ampacity_a"]) * temperature_factor, 4
            ),
            "temperature_factor": round(temperature_factor, 4),
            "grouping_factor": round(grouping_factor, 4),
            "combined_derating_factor": round(combined_derating_factor, 4),
            "corrected_ampacity_a": round(
                float(row["ampacity_a"]) * combined_derating_factor, 4
            ),
            "basis_label": selected_basis["label"],
            "status": "正式候选" if cable_approved and breaker is not None else "基础初选",
        }
        for row in rows
        if float(row["ampacity_a"]) * combined_derating_factor >= required_ampacity
    ]
    # 无长度时只能按载流量给出最小截面；有长度时由电压降步骤在所有
    # 载流量合格的截面中选择最小合格项。
    candidates = ampacity_candidates[:1]
    outputs["cable_candidates"] = candidates
    if candidates:
        steps.append(Step("导体截面候选", f"修正后载流量 Iz ≥ {required_ampacity:g} A", candidates[0]["section_mm2"], "mm²"))
        if not cable_approved:
            warnings.append("载流量表已核实但尚未批准；已显示基础截面初选，不能作为正式选型结论。")
    elif not selected_basis:
        warnings.append("敷设场景或载流条件不足；仅给出所需最小基础载流量。")
    else:
        warnings.append("所需载流量已超出当前核验目录。")

    _append_voltage_drop(
        data=data,
        rules=rules,
        catalog=catalog,
        family=family,
        scenario=scenario,
        phase=phase,
        voltage=voltage,
        current=current,
        power_factor=adopted_power_factor,
        cable_candidates=candidates,
        ampacity_candidates=ampacity_candidates,
        outputs=outputs,
        steps=steps,
        warnings=warnings,
        rule_codes=rule_codes,
    )

    if candidates and family == "YJV":
        fault_loop_structure = lookup_yjv_fault_loop_structure(
            configuration_code, candidates[0]["section_mm2"]
        )
        if (
            fault_loop_structure is None
            and configuration_code in {"yjv_4c_3ph_n_pe", "yjv_5c_3ph_n_pe"}
        ):
            pe_selection = calculate_pe_minimum_section_by_table(
                {
                    "phase_conductor_section_mm2": candidates[0]["section_mm2"],
                    "phase_conductor_material": "copper",
                    "protective_conductor_material": "copper",
                    "separate_protective_conductor": False,
                },
                rules,
            )
            pe_section = pe_selection.outputs.get(
                "required_minimum_pe_section_mm2"
            )
            if pe_section is not None:
                fault_loop_structure = {
                    "family": "YJV",
                    "configuration_code": configuration_code,
                    "phase_section_mm2": candidates[0]["section_mm2"],
                    "protective_section_mm2": float(pe_section),
                    "geometry_available": False,
                    "impedance_method": "tn_conventional",
                    "source": pe_selection.outputs.get("source"),
                    "table": pe_selection.outputs.get("clause"),
                    "page": pe_selection.outputs.get("location"),
                    "status": "verified",
                    "geometry_note": (
                        "该截面超出当前圆形线芯几何表覆盖范围；"
                        "PE截面按表54.2取得，故障电流采用TN常规法保守计算。"
                    ),
                }
                if "ELEC.PE.MIN_SECTION.TABLE54_2" not in rule_codes:
                    rule_codes.append("ELEC.PE.MIN_SECTION.TABLE54_2")
        if fault_loop_structure:
            candidates[0]["fault_loop_structure"] = fault_loop_structure
            if "ELEC.CABLE.YJV.STRUCTURE" not in rule_codes:
                rule_codes.append("ELEC.CABLE.YJV.STRUCTURE")

    outputs["workflow_stages"] = [
        {"code": "load_current", "label": "负荷与计算电流", "state": "completed"},
        {
            "code": "breaker",
            "label": "断路器初选",
            "state": "candidate" if breaker is not None or breaker_design_candidates else "minimum",
        },
        {
            "code": "conductor",
            "label": "导体初选",
            "state": "candidate" if candidates else "minimum",
        },
        {
            "code": "voltage_drop",
            "label": "电压降",
            "state": "completed" if outputs.get("voltage_drop", {}).get("calculated") else "waiting",
        },
        {
            "code": "short_circuit",
            "label": "短路与分断能力",
            "state": "candidate" if outputs.get("short_circuit_estimate") else "waiting",
        },
        {"code": "earth_fault", "label": "接地故障与自动切断", "state": "waiting"},
        {"code": "phase_thermal", "label": "相导体热稳定", "state": "waiting"},
        {"code": "pe_thermal", "label": "PE导体热稳定", "state": "waiting"},
    ]

    incomplete = ["环境温度修正", "成组敷设修正", "断路器脱扣特性", "短路电流与分断能力", "选择性", "故障防护", "相导体热稳定", "PE导体热稳定", "芯数及 N/PE 配置", "断路器极数"]
    if temperature_confirmed:
        incomplete.remove("环境温度修正")
    if (
        outputs.get("tray_configuration")
        or outputs.get("enclosed_grouping")
        or outputs.get("buried_grouping")
    ):
        incomplete.remove("成组敷设修正")
    if not outputs.get("voltage_drop", {}).get("calculated"):
        incomplete.insert(2, "电压降")
    if not selected_basis:
        incomplete.insert(0, "载流导线根数 / 电缆结构")
    if scenario == "unknown":
        incomplete.insert(0, "实际敷设方式")
    outputs["incomplete_checks"] = incomplete
    warnings.append(
        "以上为普通负荷回路连续暂算；仍需完成未列为已完成的载流量修正、"
        "短路、保护配合及故障防护校核。配电箱或干线还应确认需要系数、"
        "同时系数是否适用。"
    )
    voltage_provisional = outputs.get("voltage_drop", {}).get("provisional_status")
    provisional_status = FAIL if voltage_provisional == FAIL else PASS
    return Outcome(
        "普通负荷快速计算",
        ENGINE_VERSION,
        UNKNOWN,
        provisional_status,
        outputs,
        steps,
        warnings,
        rule_codes,
    )


def _append_voltage_drop(
    *,
    data: dict[str, Any],
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
    family: str,
    scenario: str,
    phase: str,
    voltage: float,
    current: float,
    power_factor: float | None,
    cable_candidates: list[dict[str, Any]],
    ampacity_candidates: list[dict[str, Any]],
    outputs: dict[str, Any],
    steps: list[Step],
    warnings: list[str],
    rule_codes: list[str],
) -> None:
    """仅接收长度，按初选截面和已核实目录自动取得 R/X 后暂算。"""
    if data.get("length_m") in (None, ""):
        return

    length = _number(data, "length_m")
    if length is None or length <= 0:
        outputs["voltage_drop"] = {"calculated": False}
        warnings.append("线路长度必须是大于 0 的有效数值。")
        return
    if not cable_candidates:
        outputs["voltage_drop"] = {
            "calculated": False,
            "missing": ["可查表的导体初选截面"],
        }
        warnings.append("尚未得到可查表的导体初选截面，不能自动计算电压降。")
        return

    impedance_catalog = catalog.get("voltage_drop_impedance", {}).get(family, {})
    table_reference = impedance_catalog.get("tables", {}).get(phase, {})
    if (
        impedance_catalog.get("status") not in {"verified", "approved"}
        or not table_reference
    ):
        outputs["voltage_drop"] = {"calculated": False}
        warnings.append(
            f"当前核验的电压降参数表未覆盖 {family}、"
            f"{'单相' if phase == '1' else '三相'}、{scenario} 组合，不能自动补值。"
        )
        return

    limit_catalog = catalog.get("voltage_drop_limits", {})
    load = catalog.get("load_types", {}).get(str(data.get("load_type_code") or ""), {})
    limit_profile_code = "lighting_low_voltage" if load.get("group") == "lighting" else "low_voltage"
    limit_profile = limit_catalog.get("profiles", {}).get(limit_profile_code, {})
    limit = _number(limit_profile, "limit_pct")
    if limit_catalog.get("status") not in {"verified", "approved"} or limit is None:
        outputs["voltage_drop"] = {"calculated": False, "missing": ["已核实的允许电压降限值"]}
        warnings.append("允许电压降限值目录尚未核实，不能自动进行限值校核。")
        return

    conservative_pf = power_factor is None
    phase_factor = sqrt(3) if phase == "3" else 2
    impedance_rows = {
        float(row["section_mm2"]): row
        for row in impedance_catalog.get("scenarios", {}).get(scenario, [])
    }
    evaluated: list[tuple[dict[str, Any], dict[str, Any], float, float, float]] = []
    for candidate in ampacity_candidates:
        section = float(candidate["section_mm2"])
        impedance_row = impedance_rows.get(section)
        if impedance_row is None:
            continue
        resistance = float(impedance_row["resistance_ohm_per_km"])
        reactance = float(impedance_row["reactance_ohm_per_km"])
        candidate_power_factor = power_factor
        if candidate_power_factor is None:
            candidate_power_factor = max(
                (0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
                key=lambda pf: resistance * pf
                + reactance * sqrt(max(0.0, 1 - pf**2)),
            )
        candidate_sin_phi = sqrt(max(0.0, 1 - candidate_power_factor**2))
        drop_v = phase_factor * current * (
            resistance * candidate_power_factor + reactance * candidate_sin_phi
        ) * length / 1000
        evaluated.append(
            (
                candidate,
                impedance_row,
                drop_v,
                drop_v / voltage * 100,
                candidate_power_factor,
            )
        )
    if not evaluated:
        outputs["voltage_drop"] = {"calculated": False}
        warnings.append("当前电压降参数表未覆盖可选导体截面，不能自动进行截面校核。")
        return

    selected_candidate, impedance_row, drop_v, drop_pct, selected_power_factor = next(
        (item for item in evaluated if item[3] <= limit), evaluated[-1]
    )
    cable_candidates[:] = [selected_candidate]
    selected_section = float(selected_candidate["section_mm2"])
    resistance = float(impedance_row["resistance_ohm_per_km"])
    reactance = float(impedance_row["reactance_ohm_per_km"])
    power_factor = selected_power_factor
    sin_phi = sqrt(max(0.0, 1 - power_factor**2))
    if drop_pct > limit:
        warnings.append(
            "当前已核验目录内没有同时满足载流量和允许电压降的导体截面。"
        )
    elif selected_section != float(ampacity_candidates[0]["section_mm2"]):
        steps.append(
            Step(
                "电压降调整截面",
                f"最小载流量截面压降超限，改用最小满足{limit:g}%限值的截面",
                selected_section,
                "mm²",
            )
        )
    provisional = PASS if drop_pct <= limit else "不通过"
    formal = provisional if (
        rules.get("ELEC.VDROP.LIMIT", {}).get("status") == "approved"
        and rules.get("ELEC.VDROP.IMPEDANCE", {}).get("status") == "approved"
    ) else UNKNOWN
    outputs["voltage_drop"] = {
        "calculated": True,
        "length_m": length,
        "selected_section_mm2": selected_section,
        "resistance_ohm_per_km": resistance,
        "reactance_ohm_per_km": reactance,
        "adopted_power_factor": power_factor,
        "power_factor_source": (
            "未提供功率因数，在表列0.5～1.0范围内取电压降最大值作保守暂算"
            if conservative_pf
            else "负荷电流计算采用值 / 用户输入值"
        ),
        "voltage_drop_v": round(drop_v, 4),
        "voltage_drop_pct": round(drop_pct, 4),
        "limit_pct": limit,
        "limit_source": {
            "source": limit_catalog["source"],
            "clause": limit_catalog["clause"],
            "table": limit_catalog["table"],
            "page": limit_catalog["page"],
            "boundary": limit_catalog["boundary"],
            "classification": limit_profile["name"],
            "table_value": limit_profile["table_value"],
            "selection_note": limit_profile.get("selection_note"),
            "status": limit_catalog["status"],
        },
        "provisional_status": provisional,
        "status": formal,
        "parameter_source": {
            "source": impedance_catalog["source"],
            "table": table_reference["table"],
            "title": table_reference["title"],
            "page": table_reference["page"],
            "application_note": table_reference.get("application_note"),
        },
    }
    rule_codes.extend(["ELEC.VDROP.LIMIT", "ELEC.VDROP.IMPEDANCE"])
    steps.append(
        Step(
            "自动查取线路参数",
            f"{family} {selected_section:g} mm²，{table_reference['table']}",
            f"R={resistance:g}，X={reactance:g}",
            "Ω/km",
        )
    )
    steps.append(
        Step(
            "线路电压降",
            (
                f"{phase_factor:.6g}×{current:.4f}×"
                f"({resistance:g}×{power_factor:g}+{reactance:g}×{sin_phi:.6f})"
                f"×{length:g}/1000"
            ),
            round(drop_v, 4),
            "V",
        )
    )
    steps.append(
        Step(
            "自动采用允许电压降",
            f"{limit_catalog['table']}：{limit_profile['name']}（表列{limit_profile['table_value']}%）",
            limit,
            "%",
        )
    )
    if (
        rules.get("ELEC.VDROP.LIMIT", {}).get("status") != "approved"
        or rules.get("ELEC.VDROP.IMPEDANCE", {}).get("status") != "approved"
    ):
        warnings.append("允许电压降已按表6.2-6自动采用；限值或线路参数依据尚未批准，本次结果仅用于暂算复核。")
