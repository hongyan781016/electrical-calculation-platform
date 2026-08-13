"""电缆/绝缘电线载流量候选生成器。

本模块只完成结构适用性、基础载流量和已知修正条件筛选。电压降、
短路、故障保护、热稳定及与断路器的配合由完整回路组合校核器完成。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import log, pi
from typing import Any

from .catalog import (
    CONDUCTOR_CONFIGURATIONS,
    DEFAULT_CATALOG,
    INSTALLATION_SCENARIOS,
    lookup_yjv_fault_loop_structure,
    lookup_yjv_four_core_phase_pe_impedance,
    resolve_conductor_ampacity_basis,
)
from .complete_circuit import Phase
from .complete_circuit_engine import ResolvedSegmentElectrical
from .engine import Outcome, PASS, Step, UNKNOWN


ENGINE_VERSION = "0.1.0"

# 《工业与民用供配电设计手册（第四版）》9.4.1.1：铜20℃电阻率；
# 多股导体绞入系数取1.02。表4.2-46规定的低压最小故障计算采用
# 1.5倍电阻。9.4.1.2使用0.778r作为导体几何平均半径。
COPPER_RESISTIVITY_20C_OHM_MM2_PER_M = 0.0172
MULTIWIRE_STRANDING_FACTOR = 1.02
MINIMUM_FAULT_RESISTANCE_MULTIPLIER = 1.5
GEOMETRIC_MEAN_RADIUS_FACTOR = 0.778
CONDUCTOR_INDUCTANCE_COEFFICIENT_H_PER_KM = 2e-4


@dataclass(frozen=True)
class CableInstallationConditions:
    temperature_c: float | None = None
    tray_type: str | None = None
    tray_layers: int | None = None
    tray_cables_per_layer: int | None = None
    enclosed_circuit_count: int | None = None
    soil_thermal_resistivity_k_m_per_w: float | None = None
    buried_circuit_count: int | None = None
    buried_duct_spacing_m: str | None = None
    buried_depth_m: float | None = None


@dataclass(frozen=True)
class CableSelectionRequest:
    segment_id: str
    family: str
    configuration_code: str
    phase: Phase
    system_voltage_v: float
    installation_scenario: str
    minimum_required_ampacity_a: float
    neutral_required: bool
    protective_conductor_mode: str
    conditions: CableInstallationConditions
    separate_protective_section_mm2: float | None = None


def _rule_approved(rules: dict[str, dict[str, Any]], code: str) -> bool:
    return rules.get(code, {}).get("status") == "approved"


def _row_for_section(rows: list[dict[str, Any]], section: float) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in rows
            if float(row.get("section_mm2", -1)) == float(section)
        ),
        None,
    )


def _factor_check(
    code: str,
    name: str,
    status: str,
    factor: float | None,
    source: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "check_code": code,
        "name": name,
        "status": status,
        "factor": factor,
        "source": source or {},
        "note": note,
    }


def _temperature_adjustment(
    request: CableSelectionRequest,
    conductor: dict[str, Any],
    basis: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[float, bool, bool, dict[str, Any], str | None]:
    temperature = request.conditions.temperature_c
    if temperature is None:
        return (
            1.0,
            False,
            False,
            _factor_check(
                "temperature",
                "环境温度修正",
                UNKNOWN,
                None,
                note="未提供实际敷设温度；仅保留基础载流量候选。",
            ),
            None,
        )
    temperature_catalog = catalog.get("temperature_derating", {})
    mode = (
        "ground"
        if request.installation_scenario == "direct_buried"
        else "air"
    )
    table = temperature_catalog.get(mode, {})
    insulation = str(conductor.get("insulation_code") or "")
    factors = table.get("factors", {}).get(insulation, {})
    base_temperature = float(
        basis.get("base_temperature_c", conductor.get("base_temperature_c", 0))
    )
    actual_factor = factors.get(float(temperature))
    base_factor = factors.get(base_temperature)
    if (
        actual_factor is None
        or base_factor in (None, 0)
        or temperature_catalog.get("status") not in {"verified", "approved"}
    ):
        return (
            1.0,
            False,
            True,
            _factor_check(
                "temperature",
                "环境温度修正",
                UNKNOWN,
                None,
                note="实际温度或基础温度不在当前已核实修正表的精确档位内。",
            ),
            "ELEC.CABLE.TEMPERATURE.DERATING",
        )
    relative = float(actual_factor) / float(base_factor)
    return (
        relative,
        True,
        False,
        _factor_check(
            "temperature",
            "环境温度修正",
            PASS,
            round(relative, 6),
            {
                "source": temperature_catalog.get("source"),
                "table": table.get("table"),
                "page": temperature_catalog.get("page"),
                "actual_temperature_c": temperature,
                "base_temperature_c": base_temperature,
            },
        ),
        "ELEC.CABLE.TEMPERATURE.DERATING",
    )


def _grouping_adjustment(
    request: CableSelectionRequest,
    catalog: dict[str, Any],
) -> tuple[float, bool, bool, dict[str, Any], str | None]:
    scenario = request.installation_scenario
    conditions = request.conditions
    if scenario == "tray":
        if (
            not conditions.tray_type
            or conditions.tray_layers is None
            or conditions.tray_cables_per_layer is None
        ):
            return (
                1.0,
                False,
                False,
                _factor_check(
                    "grouping",
                    "槽盒成组修正",
                    UNKNOWN,
                    None,
                    note="缺少槽盒型式、层数或每层线缆数。",
                ),
                None,
            )
        tray = catalog.get("tray_derating", {})
        factor = (
            tray.get(conditions.tray_type, {})
            .get(str(conditions.tray_layers), {})
            .get(str(conditions.tray_cables_per_layer))
        )
        if factor is None or tray.get("status") not in {"verified", "approved"}:
            return (
                1.0,
                False,
                True,
                _factor_check(
                    "grouping",
                    "槽盒成组修正",
                    UNKNOWN,
                    None,
                    note="所选槽盒条件不在当前表6.25接入范围。",
                ),
                "ELEC.CABLE.TRAY.GROUPING",
            )
        return (
            float(factor),
            True,
            False,
            _factor_check(
                "grouping",
                "槽盒成组修正",
                PASS,
                float(factor),
                {
                    "source": tray.get("source"),
                    "table": tray.get("table"),
                    "page": tray.get("page"),
                },
            ),
            "ELEC.CABLE.TRAY.GROUPING",
        )

    if scenario == "conduit":
        count = conditions.enclosed_circuit_count
        if count is None:
            return (
                1.0,
                False,
                False,
                _factor_check(
                    "grouping",
                    "同束/封闭通道成组修正",
                    UNKNOWN,
                    None,
                    note="缺少同束或封闭通道内回路数。",
                ),
                None,
            )
        if count == 1:
            return (
                1.0,
                True,
                False,
                _factor_check(
                    "grouping",
                    "同束/封闭通道成组修正",
                    PASS,
                    1.0,
                    note="单回路，不进行多回路成组降额。",
                ),
                None,
            )
        grouping = catalog.get("enclosed_grouping", {})
        factor = grouping.get("factors", {}).get(count)
        if factor is None or grouping.get("status") not in {"verified", "approved"}:
            return (
                1.0,
                False,
                True,
                _factor_check(
                    "grouping",
                    "同束/封闭通道成组修正",
                    UNKNOWN,
                    None,
                    note="回路数不在当前表6.26接入范围。",
                ),
                "ELEC.CABLE.ENCLOSED.GROUPING",
            )
        return (
            float(factor),
            True,
            False,
            _factor_check(
                "grouping",
                "同束/封闭通道成组修正",
                PASS,
                float(factor),
                {
                    "source": grouping.get("source"),
                    "table": grouping.get("table"),
                    "page": grouping.get("page"),
                },
            ),
            "ELEC.CABLE.ENCLOSED.GROUPING",
        )

    if scenario == "direct_buried":
        count = conditions.buried_circuit_count
        if count is None:
            return (
                1.0,
                False,
                False,
                _factor_check(
                    "grouping",
                    "埋地管槽成组修正",
                    UNKNOWN,
                    None,
                    note="缺少同路径回路数。",
                ),
                None,
            )
        if count == 1:
            return (
                1.0,
                True,
                False,
                _factor_check(
                    "grouping",
                    "埋地管槽成组修正",
                    PASS,
                    1.0,
                    note="单回路，不进行多回路成组降额。",
                ),
                None,
            )
        grouping = catalog.get("buried_duct_grouping", {})
        factor = None
        if (
            conditions.soil_thermal_resistivity_k_m_per_w == 2.5
            and conditions.buried_depth_m == 0.7
            and conditions.buried_duct_spacing_m
        ):
            factor = (
                grouping.get("factors", {})
                .get(conditions.buried_duct_spacing_m, {})
                .get(count)
            )
        if factor is None or grouping.get("status") not in {"verified", "approved"}:
            return (
                1.0,
                False,
                True,
                _factor_check(
                    "grouping",
                    "埋地管槽成组修正",
                    UNKNOWN,
                    None,
                    note="表6.27参考条件或精确档位不匹配。",
                ),
                "ELEC.CABLE.BURIED_DUCT.GROUPING",
            )
        return (
            float(factor),
            True,
            False,
            _factor_check(
                "grouping",
                "埋地管槽成组修正",
                PASS,
                float(factor),
                {
                    "source": grouping.get("source"),
                    "table": grouping.get("table"),
                    "page": grouping.get("page"),
                },
            ),
            "ELEC.CABLE.BURIED_DUCT.GROUPING",
        )

    return (
        1.0,
        False,
        True,
        _factor_check(
            "grouping",
            "成组修正",
            UNKNOWN,
            None,
            note="当前敷设场景没有可用的成组修正规则。",
        ),
        None,
    )


def _allocation(
    request: CableSelectionRequest,
    section_mm2: float,
) -> tuple[bool, float | None, float | None, str | None]:
    mode = request.protective_conductor_mode
    code = request.configuration_code
    if mode not in {"included", "separate", "unconfirmed"}:
        return False, None, None, "保护导体配置值无效。"
    if mode == "unconfirmed":
        return False, None, None, None

    reduced = lookup_yjv_fault_loop_structure(code, section_mm2)
    reduced_section = float(reduced["protective_section_mm2"]) if reduced else None
    if code == "yjv_4c_3ph_n_pe" and reduced_section is None:
        sequence = lookup_yjv_four_core_phase_pe_impedance(section_mm2)
        if sequence:
            reduced_section = float(sequence["protective_section_mm2"])
    neutral_section: float | None = None
    protective_section: float | None = None

    if code == "bv_1ph_2wire_pe":
        neutral_section = section_mm2
        if mode == "included":
            return False, None, None, "BV单芯线组合的PE应作为独立导体配置。"
        if mode == "separate" and request.separate_protective_section_mm2 is not None:
            protective_section = float(request.separate_protective_section_mm2)
    elif code == "bv_3ph_4wire_pe":
        neutral_section = section_mm2
        if mode == "included":
            return False, None, None, "BV单芯线组合的PE应作为独立导体配置。"
    elif code == "bv_3ph_3wire_pe":
        if request.neutral_required:
            return False, None, None, "所选3根单芯线结构没有N导体。"
        if mode == "included":
            return False, None, None, "BV单芯线组合的PE应作为独立导体配置。"
    elif code == "yjv_3c_3ph_pe":
        if request.neutral_required:
            return False, None, None, "YJV三芯结构没有N导体。"
        if mode == "included":
            return False, None, None, "YJV三芯结构不能同时包含三相导体和PE。"
    elif code == "yjv_4c_3ph_n_pe":
        if reduced_section is None:
            return False, None, None, None
        if request.neutral_required:
            if mode == "included":
                return False, None, None, "四芯3+1结构不能同时把第四芯作为N和PE。"
            neutral_section = reduced_section
        elif mode == "included":
            protective_section = reduced_section
    elif code == "yjv_5c_3ph_n_pe":
        if reduced_section is None:
            return False, None, None, None
        neutral_section = reduced_section
        if mode == "included":
            protective_section = reduced_section
    elif code == "yjv_4c_3ph_n_separate_pe":
        neutral_section = section_mm2
        if mode == "included":
            return False, None, None, "四芯L1/L2/L3/N结构的PE应作为独立导体配置。"
        if mode == "separate" and request.separate_protective_section_mm2 is not None:
            protective_section = float(request.separate_protective_section_mm2)

    return True, neutral_section, protective_section, None


def _resolved_electrical(
    request: CableSelectionRequest,
    candidate: dict[str, Any],
    catalog: dict[str, Any],
    source_reference_ids: tuple[str, ...],
    rules: dict[str, dict[str, Any]],
) -> ResolvedSegmentElectrical | None:
    if not candidate["conductor_allocation_confirmed"]:
        return None
    section = float(candidate["phase_section_mm2"])
    impedance_family = (
        catalog.get("voltage_drop_impedance", {}).get(request.family, {})
    )
    impedance_rows = (
        impedance_family.get("scenarios", {}).get(
            request.installation_scenario, []
        )
    )
    voltage_row = _row_for_section(impedance_rows, section)
    voltage_r = (
        float(voltage_row["resistance_ohm_per_km"]) if voltage_row else None
    )
    voltage_x = (
        float(voltage_row["reactance_ohm_per_km"]) if voltage_row else None
    )
    resolved_reference_ids = list(source_reference_ids)
    if voltage_row:
        resolved_reference_ids.append("ELEC.VDROP.IMPEDANCE")

    # 19DX/手册电压降表列R/X同时可作为同一电缆的正序线路参数；
    # 相—PE回路仍必须使用对应结构数据，不能把正序电抗直接翻倍代替。
    three_r = voltage_r
    three_x = voltage_x
    phase_pe_r = phase_pe_x = None
    phase_neutral_r = phase_neutral_x = None
    if request.family == "BV" and request.configuration_code == "bv_1ph_2wire_pe":
        # 近邻铜导体保守常规法：ρ=0.0237Ω·mm²/m、忽略电抗。
        # 该方法只形成末端最小故障电流暂算，不冒充精确序阻抗。
        conventional_rho = 0.0237
        phase_neutral_r = conventional_rho * 1000 * (1 / section + 1 / section)
        phase_neutral_x = 0.0
        pe_section = candidate.get("protective_section_mm2")
        if pe_section is not None:
            phase_pe_r = conventional_rho * 1000 * (1 / section + 1 / float(pe_section))
            phase_pe_x = 0.0
        resolved_reference_ids.append("ELEC.EARTH_FAULT.TN.CONVENTIONAL")
    elif (
        request.family == "YJV"
        and request.configuration_code == "yjv_4c_3ph_n_separate_pe"
    ):
        conventional_rho = 0.0237
        phase_neutral_r = conventional_rho * 1000 * (1 / section + 1 / section)
        phase_neutral_x = 0.0
        pe_section = candidate.get("protective_section_mm2")
        if pe_section is not None:
            phase_pe_r = conventional_rho * 1000 * (1 / section + 1 / float(pe_section))
            phase_pe_x = 0.0
        resolved_reference_ids.append("ELEC.EARTH_FAULT.TN.CONVENTIONAL")
    if request.family == "YJV" and request.configuration_code == "yjv_4c_3ph_n_pe":
        sequence = lookup_yjv_four_core_phase_pe_impedance(section)
        if sequence:
            resolved_reference_ids.append(
                "ELEC.CABLE.YJV.FOUR_CORE.PHASE_PE.IMPEDANCE"
            )
            three_r = float(sequence["positive_sequence_resistance_ohm_per_km"])
            three_x = float(sequence["positive_sequence_reactance_ohm_per_km"])
            if (
                request.protective_conductor_mode == "included"
                and not request.neutral_required
            ):
                phase_pe_r = float(sequence["phase_pe_resistance_ohm_per_km"])
                phase_pe_x = float(sequence["phase_pe_reactance_ohm_per_km"])
        elif (
            request.protective_conductor_mode == "included"
            and not request.neutral_required
        ):
            structure = lookup_yjv_fault_loop_structure(
                request.configuration_code, section
            )
            if structure:
                pe_section = float(structure["protective_section_mm2"])
                phase_pe_r = (
                    MINIMUM_FAULT_RESISTANCE_MULTIPLIER
                    * COPPER_RESISTIVITY_20C_OHM_MM2_PER_M
                    * MULTIWIRE_STRANDING_FACTOR
                    * 1000
                    * (1 / section + 1 / pe_section)
                )
                d = float(structure["phase_pe_center_distance_cm"])
                rp = float(structure["phase_conductor_radius_cm"])
                re = float(structure["protective_conductor_radius_cm"])
                phase_l = CONDUCTOR_INDUCTANCE_COEFFICIENT_H_PER_KM * log(
                    d / (GEOMETRIC_MEAN_RADIUS_FACTOR * rp)
                )
                pe_l = CONDUCTOR_INDUCTANCE_COEFFICIENT_H_PER_KM * log(
                    d / (GEOMETRIC_MEAN_RADIUS_FACTOR * re)
                )
                phase_pe_x = 2 * pi * 50 * (phase_l + pe_l)
                resolved_reference_ids.extend(
                    [
                        "ELEC.CABLE.FAULT_LOOP.RESISTANCE",
                        "ELEC.CABLE.FAULT_LOOP.REACTANCE",
                        "ELEC.CABLE.YJV.STRUCTURE",
                    ]
                )

    resolved_reference_ids = list(dict.fromkeys(resolved_reference_ids))
    parameter_status = (
        "approved"
        if resolved_reference_ids
        and all(_rule_approved(rules, code) for code in resolved_reference_ids)
        else "verified"
    )
    return ResolvedSegmentElectrical(
        segment_id=request.segment_id,
        phase_neutral_applicable=candidate["neutral_section_mm2"] is not None,
        voltage_drop_r_ohm_per_km=voltage_r,
        voltage_drop_x_ohm_per_km=voltage_x,
        three_phase_r_ohm_per_km=three_r,
        three_phase_x_ohm_per_km=three_x,
        phase_neutral_r_ohm_per_km=phase_neutral_r,
        phase_neutral_x_ohm_per_km=phase_neutral_x,
        phase_pe_r_ohm_per_km=phase_pe_r,
        phase_pe_x_ohm_per_km=phase_pe_x,
        corrected_ampacity_a=float(candidate["corrected_ampacity_a"]),
        status=parameter_status,
        source_reference_ids=tuple(resolved_reference_ids),
    )


def generate_cable_candidates(
    request: CableSelectionRequest,
    rules: dict[str, dict[str, Any]],
    catalog: dict[str, Any] | None = None,
) -> Outcome:
    """Generate every table-backed section at or above the requested ampacity."""

    catalog = catalog or DEFAULT_CATALOG
    warnings: list[str] = []
    steps: list[Step] = []
    outputs: dict[str, Any] = {
        "candidates": [],
        "rejected_candidates": [],
        "checks": [],
    }
    rule_codes: list[str] = []

    configuration = CONDUCTOR_CONFIGURATIONS.get(request.configuration_code)
    if not request.segment_id.strip():
        warnings.append("线路段ID不能为空。")
    if request.system_voltage_v <= 0:
        warnings.append("系统电压必须大于0。")
    if request.minimum_required_ampacity_a <= 0:
        warnings.append("所需最小载流量必须大于0。")
    if request.family not in INSTALLATION_SCENARIOS:
        warnings.append("导体型号不在当前目录。")
    if not configuration:
        warnings.append("导体结构不在当前目录。")
    elif (
        configuration.get("family") != request.family
        or request.phase.value not in configuration.get("phases", ())
    ):
        warnings.append("导体结构与型号或相制不匹配。")
    allowed_scenarios = {
        code for code, _ in INSTALLATION_SCENARIOS.get(request.family, ())
    }
    if request.installation_scenario not in allowed_scenarios:
        warnings.append("敷设场景不适用于当前导体型号。")
    if (
        request.protective_conductor_mode == "included"
        and request.configuration_code
        in {
            "bv_1ph_2wire_pe",
            "bv_3ph_3wire_pe",
            "bv_3ph_4wire_pe",
            "yjv_3c_3ph_pe",
        }
    ):
        warnings.append("所选导体结构不能把PE包含在当前芯线组合内。")
    if (
        request.neutral_required
        and request.protective_conductor_mode == "included"
        and request.configuration_code == "yjv_4c_3ph_n_pe"
    ):
        warnings.append("YJV四芯3+1结构不能同时把第四芯作为N和PE。")
    if warnings:
        return Outcome(
            "电缆载流量候选",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    basis_code, basis, basis_note = resolve_conductor_ampacity_basis(
        request.family,
        request.installation_scenario,
        request.phase.value,
        request.configuration_code,
        catalog,
        request.conditions.soil_thermal_resistivity_k_m_per_w,
    )
    if not basis:
        warnings.append("当前目录没有覆盖该型号、结构和敷设条件的基础载流量表。")
        return Outcome(
            "电缆载流量候选",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    conductor = catalog["conductors"][request.family]
    basis_rule = basis.get(
        "rule_code",
        f"ELEC.CABLE.{request.family}.AMPACITY",
    )
    rule_codes.append(basis_rule)
    outputs["basis"] = {
        "code": basis_code,
        "label": basis.get("label"),
        "note": basis_note,
        "source": basis.get("source", conductor.get("source")),
        "table": basis.get("table", conductor.get("table")),
        "page": basis.get("page", conductor.get("page")),
        "reference_condition": basis.get(
            "reference_condition",
            conductor.get("reference_condition"),
        ),
    }
    outputs["checks"].append(
        _factor_check(
            "rated_voltage",
            "导体额定电压适用",
            UNKNOWN,
            None,
            note=(
                "已记录导体电压标识；额定电压适用规则尚未在本候选器内批准，"
                "留待组合校核。"
            ),
        )
    )

    (
        temperature_factor,
        temperature_complete,
        temperature_fatal,
        temperature_check,
        temperature_rule,
    ) = _temperature_adjustment(request, conductor, basis, catalog)
    (
        grouping_factor,
        grouping_complete,
        grouping_fatal,
        grouping_check,
        grouping_rule,
    ) = _grouping_adjustment(request, catalog)
    outputs["checks"].extend([temperature_check, grouping_check])
    for code in (temperature_rule, grouping_rule):
        if code and code not in rule_codes:
            rule_codes.append(code)
    if temperature_fatal or grouping_fatal:
        warnings.extend(
            check["note"]
            for check in (temperature_check, grouping_check)
            if check["status"] == UNKNOWN and check["note"]
        )
        return Outcome(
            "电缆载流量候选",
            ENGINE_VERSION,
            UNKNOWN,
            UNKNOWN,
            outputs,
            steps,
            warnings,
            rule_codes,
        )

    soil_condition_complete = True
    if (
        request.installation_scenario == "direct_buried"
        and basis_code == "yjv_multicore_in_ground_people"
    ):
        soil_condition_complete = False
        outputs["checks"].append(
            _factor_check(
                "soil_condition",
                "地下敷设土壤条件修正",
                UNKNOWN,
                None,
                note=(
                    "表31当前只作为四/五芯地下基础载流量；尚无与该基础表"
                    "配套的土壤热阻修正关系。"
                ),
            )
        )
    conditions_complete = (
        temperature_complete and grouping_complete and soil_condition_complete
    )
    combined_factor = temperature_factor * grouping_factor
    source_reference_ids = tuple(
        dict.fromkeys(
            [
                basis_rule,
                *([temperature_rule] if temperature_rule else []),
                *([grouping_rule] if grouping_rule else []),
            ]
        )
    )
    ampacity_rules_approved = all(
        _rule_approved(rules, code) for code in source_reference_ids
    )
    candidates: list[dict[str, Any]] = []
    for row in basis.get("rows", []):
        section = float(row["section_mm2"])
        corrected_ampacity = float(row["ampacity_a"]) * combined_factor
        if corrected_ampacity < request.minimum_required_ampacity_a:
            outputs["rejected_candidates"].append(
                {
                    "family": request.family,
                    "phase_section_mm2": section,
                    "reason_code": "ampacity_insufficient",
                    "reason": (
                        f"修正后载流量{corrected_ampacity:.6g}A小于"
                        f"所需{request.minimum_required_ampacity_a:.6g}A。"
                    ),
                }
            )
            continue
        (
            allocation_confirmed,
            neutral_section,
            protective_section,
            allocation_error,
        ) = _allocation(request, section)
        if allocation_error:
            outputs["rejected_candidates"].append(
                {
                    "family": request.family,
                    "phase_section_mm2": section,
                    "reason_code": "conductor_allocation_invalid",
                    "reason": allocation_error,
                }
            )
            continue

        structure = (
            lookup_yjv_fault_loop_structure(request.configuration_code, section)
            if request.family == "YJV"
            else None
        )
        candidate = {
            "candidate_id": (
                f"{request.segment_id}:{request.family}:"
                f"{request.configuration_code}:{section:g}"
            ),
            "family": request.family,
            "voltage_designation": (
                "450/750V" if request.family == "BV" else "0.6/1kV"
            ),
            "configuration_code": request.configuration_code,
            "configuration_label": configuration["label"],
            "cable_specification": (
                f"BV-450/750V {section:g}mm²单芯线"
                if request.family == "BV"
                else f"YJV-0.6/1kV {configuration['label']} {section:g}mm²"
            ),
            "phase_section_mm2": section,
            "neutral_section_mm2": neutral_section,
            "protective_section_mm2": protective_section,
            "conductor_allocation_confirmed": allocation_confirmed,
            "base_ampacity_a": float(row["ampacity_a"]),
            "temperature_factor": round(temperature_factor, 6),
            "grouping_factor": round(grouping_factor, 6),
            "combined_factor": round(combined_factor, 6),
            "corrected_ampacity_a": round(corrected_ampacity, 6),
            "minimum_required_ampacity_a": request.minimum_required_ampacity_a,
            "ampacity_provisional_status": (
                PASS if conditions_complete else UNKNOWN
            ),
            "ampacity_formal_status": (
                PASS
                if conditions_complete and ampacity_rules_approved
                else UNKNOWN
            ),
            "fault_loop_structure": structure,
            "pending_checks": [
                "额定电压适用规则",
                "与断路器的过载保护配合",
                "累计电压降",
                "最大短路电流",
                "最小短路或接地故障电流",
                "相导体短路热稳定",
                "PE热稳定",
            ],
        }
        resolved = _resolved_electrical(
            request,
            candidate,
            catalog,
            source_reference_ids,
            rules,
        )
        candidate["resolved_electrical"] = asdict(resolved) if resolved else None
        candidates.append(candidate)

    outputs["candidates"] = candidates
    outputs["combined_derating_factor"] = round(combined_factor, 6)
    outputs["conditions_complete"] = conditions_complete
    outputs["required_ampacity_a"] = request.minimum_required_ampacity_a
    if candidates:
        steps.append(
            Step(
                "载流量候选",
                (
                    "Iz＝基础载流量×温度系数×成组系数，"
                    f"Iz≥{request.minimum_required_ampacity_a:g}"
                ),
                len(candidates),
                "个",
            )
        )
    else:
        warnings.append("所需载流量已超出当前目录，或所选导体结构无法满足N/PE配置。")

    if not conditions_complete:
        warnings.append("温度、成组或土壤修正条件未完整，当前仅保留基础载流量候选。")
    if request.protective_conductor_mode == "unconfirmed":
        warnings.append("N/PE配置尚未确认，不能形成完整线路电气参数。")

    provisional = PASS if candidates else UNKNOWN
    formal = (
        PASS
        if candidates and conditions_complete and ampacity_rules_approved
        else UNKNOWN
    )
    return Outcome(
        "电缆载流量候选",
        ENGINE_VERSION,
        formal,
        provisional,
        outputs,
        steps,
        warnings,
        rule_codes,
    )
