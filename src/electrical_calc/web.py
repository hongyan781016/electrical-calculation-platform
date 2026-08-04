from __future__ import annotations

import os
import sqlite3
from math import sqrt
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import DEFAULT_DB, Database
from .catalog import (
    BUSWAY_PHASE_PE_IMPEDANCE,
    CONDUCTOR_CONFIGURATIONS,
    INSTALLATION_SCENARIOS,
    TRANSFORMER_LV_SHORT_CIRCUIT,
    TRANSFORMER_PHASE_PE_IMPEDANCE,
    TRAY_CONFIGURATION_OPTIONS,
    grouped_load_types,
    lookup_busway_phase_pe_impedance,
)
from .engine import (
    calculate_all,
    calculate_phase_conductor_thermal_withstand,
    calculate_pe_thermal_withstand,
    calculate_tn_earth_fault_protection,
    calculate_transformer_feeder_three_phase_short_circuit,
)
from .simple_engine import calculate_simple_load_selection
from .product_protection import (
    evaluate_easypact_cvs_phase_thermal_reference,
    select_easypact_cvs_reference,
)
from .drawing_audit import audit_drawing_complete_circuit
from .radial_circuit_service import calculate_radial_complete_circuit
from .validation_fixture import SEGMENT_LABELS, build_validation_fixture_requests
from .reports import create_run_pdf
from .spreadsheets import create_input_template, create_project_export, parse_circuit_workbook


PACKAGE_DIR = Path(__file__).resolve().parent
db = Database(os.environ.get("ELECTRICAL_CALC_DB", DEFAULT_DB))
app = FastAPI(title="电气工程计算自动化平台", version="0.1.0")
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, message: str = ""):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"projects": db.list_projects(), "message": message},
    )



@app.get("/quick", response_class=HTMLResponse)
def quick_page(request: Request):
    form = {
        "circuit_role": "single_device",
        "input_basis": "kw",
        "power_definition": "design",
        "phase": "3",
        "voltage_v": "380",
        "power_factor": "",
        "demand_factor": "",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_3c_3ph_pe",
        "installation_scenario": "tray",
        "soil_thermal_resistivity_k_m_per_w": "",
        "buried_circuit_count": "",
        "buried_duct_spacing_m": "",
        "buried_depth_m": "",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "enclosed_grouping_circuit_count": "",
        "installation_temperature_c": "",
        "length_m": "",
        "short_circuit_method": "none",
        "transformer_series_code": "",
        "transformer_capacity_kva": "",
        "transformer_uk_percent": "",
        "transformer_lv_no_load_voltage_v": "",
        "rcd_scenario": "unknown",
        "rcd_residual_waveform": "unknown",
        "nominal_line_to_earth_voltage_v": "220",
        "fault_transformer_series_code": "",
        "fault_transformer_capacity_kva": "",
        "fault_transformer_uk_percent": "",
        "fault_transformer_hv_voltage_kv": "10",
        "fault_transformer_vector_group": "Dyn11",
        "fault_connection_type": "direct",
        "fault_fourth_conductor_role": "",
        "fault_busway_series_code": "",
        "fault_busway_rating_a": "",
        "pe_thermal_enabled": "",
        "phase_thermal_enabled": "",
        "protective_core_confirmed": "",
        "fault_clearing_time_s": "",
        "let_through_energy_a2s": "",
        "let_through_energy_reference": "",
        "existing_breaker_series": "",
        "existing_breaker_rated_current_a": "",
        "existing_breaker_trip_unit_family": "TM-D",
        "source_impedance_mode": "short_circuit_capacity",
        "source_short_circuit_capacity_mva": "100",
        "voltage_factor_c": "1.05",
    }
    return templates.TemplateResponse(
        request=request, name="quick.html",
        context={"form": form, "result": None, "load_groups": grouped_load_types(), "scenarios": INSTALLATION_SCENARIOS, "conductor_configurations": CONDUCTOR_CONFIGURATIONS, "tray_options": TRAY_CONFIGURATION_OPTIONS, "transformer_capacities": sorted(TRANSFORMER_LV_SHORT_CIRCUIT["rows"]), "fault_transformer_series": TRANSFORMER_PHASE_PE_IMPEDANCE["series"], "fault_transformer_capacities": sorted({capacity for series in TRANSFORMER_PHASE_PE_IMPEDANCE["series"].values() for capacity in series["rows"]}), "busway_phase_pe_series": BUSWAY_PHASE_PE_IMPEDANCE["series"], "busway_phase_pe_ratings": sorted({rating for series in BUSWAY_PHASE_PE_IMPEDANCE["series"].values() for rating in series["rows"]})},
    )


def _positive_number(value: str) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _derive_nominal_line_to_earth_voltage(
    phase: str, line_voltage_v: str
) -> tuple[str, str]:
    """Return the nominal U0 used for TN checks from the circuit supply input.

    The fast page accepts a line-to-line voltage for three-phase circuits and a
    line-to-neutral voltage for single-phase circuits.  U0 is therefore an
    internal derived value, not a second customer-entered voltage.
    """
    try:
        voltage = float(line_voltage_v)
    except (TypeError, ValueError):
        return "", "相制或电压未能形成U0。"
    if voltage <= 0:
        return "", "相制或电压未能形成U0。"
    if phase == "1":
        return f"{voltage:g}", "单相回路：U0采用输入电压。"
    if phase == "3":
        if abs(voltage - 380) < 0.01:
            return "220", "三相380V系统：U0自动采用220V。"
        if abs(voltage - 400) < 0.01:
            return "230", "三相400V系统：U0自动采用230V。"
        return (
            f"{voltage / sqrt(3):.4f}",
            "非标准三相线电压：U0按ULL/√3计算。",
        )
    return "", "相制或电压未能形成U0。"


@app.get("/complete-circuit", response_class=HTMLResponse)
def complete_circuit_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="circuit_audit.html",
        context={
            "lengths": {"connection": "10", "feeder": "50", "final": "30"},
            "segment_labels": SEGMENT_LABELS,
            "errors": [],
            "audit_result": None,
            "alternative_result": None,
        },
    )


@app.post("/complete-circuit", response_class=HTMLResponse)
async def complete_circuit_preview(request: Request):
    submitted = await request.form()
    length_values: dict[str, str] = {}
    errors: list[str] = []
    for segment_id, label in SEGMENT_LABELS.items():
        raw_value = str(submitted.get(f"length_{segment_id}", "")).strip()
        length_values[segment_id] = raw_value
        if not _positive_number(raw_value):
            errors.append(f"{label}长度必须填写大于0的数值。")
    context = {
        "lengths": length_values,
        "segment_labels": SEGMENT_LABELS,
        "errors": errors,
        "audit_result": None,
        "alternative_result": None,
    }
    if not errors:
        radial, audit_request = build_validation_fixture_requests(
            {key: float(value) for key, value in length_values.items()}
        )
        rules = {item["code"]: item for item in db.list_rules()}
        context["audit_result"] = audit_drawing_complete_circuit(
            audit_request,
            rules,
        ).to_dict()
        context["alternative_result"] = calculate_radial_complete_circuit(
            radial,
            rules,
        ).to_dict()
    return templates.TemplateResponse(
        request=request,
        name="circuit_audit.html",
        context=context,
    )


@app.post("/quick", response_class=HTMLResponse)
async def quick_calculate(request: Request):
    submitted = await request.form()
    form = {key: str(submitted.get(key, "")).strip() for key in (
        "circuit_role", "input_basis", "input_value", "power_definition", "demand_factor",
        "phase", "voltage_v", "load_type_code", "power_factor",
        "conductor_family", "conductor_configuration", "installation_scenario",
        "soil_thermal_resistivity_k_m_per_w",
        "buried_circuit_count", "buried_duct_spacing_m", "buried_depth_m",
        "tray_type", "tray_layers", "tray_cables_per_layer",
        "enclosed_grouping_circuit_count",
        "installation_temperature_c", "length_m",
        "short_circuit_method", "transformer_series_code", "transformer_capacity_kva", "transformer_uk_percent", "transformer_lv_no_load_voltage_v",
        "rcd_scenario", "rcd_residual_waveform",
        "short_circuit_line_enabled", "short_circuit_line_type", "short_circuit_line_section_mm2",
        "transformer_pk_kw", "source_impedance_mode", "source_short_circuit_capacity_mva", "source_r_ohm", "source_x_ohm", "voltage_factor_c",
        "upstream_r_ohm", "upstream_x_ohm", "upstream_impedance_reference",
        "busway_r_ohm_per_km", "busway_x_ohm_per_km", "busway_impedance_reference",
        "breaker_icu_ka", "breaker_installation_point",
        "earth_fault_enabled", "earthing_system", "nominal_line_to_earth_voltage_v",
        "circuit_application", "circuit_rated_current_a", "fault_loop_impedance_ohm",
        "fault_loop_impedance_reference", "protection_type",
        "protective_device_characteristic", "protective_device_rated_current_a",
        "protective_device_operating_current_a", "protective_device_operating_reference",
        "rcd_downstream_of_pen_split",
        "fault_transformer_series_code", "fault_transformer_hv_voltage_kv",
        "fault_transformer_capacity_kva", "fault_transformer_uk_percent",
        "fault_transformer_vector_group", "fault_connection_type",
        "fault_busway_series_code", "fault_busway_rating_a",
        "fault_fourth_conductor_role", "fault_connection_length_m",
        "fault_connection_r_ohm_per_km", "fault_connection_x_ohm_per_km",
        "fault_connection_impedance_reference",
        "phase_thermal_enabled", "pe_thermal_enabled", "protective_core_confirmed",
        "fault_clearing_time_s", "let_through_energy_a2s",
        "let_through_energy_reference",
        "existing_breaker_series", "existing_breaker_rated_current_a",
        "existing_breaker_trip_unit_family",
    )}
    nominal_u0, nominal_u0_source = _derive_nominal_line_to_earth_voltage(
        form["phase"], form["voltage_v"]
    )
    form["nominal_line_to_earth_voltage_v"] = nominal_u0
    # One transformer identity is shared by outlet short-circuit, line-end
    # short-circuit and TN earth-fault calculations. Legacy fault_* fields are
    # accepted only as a backward-compatible fallback.
    form["transformer_series_code"] = (
        form["transformer_series_code"] or form["fault_transformer_series_code"]
    )
    form["transformer_capacity_kva"] = (
        form["transformer_capacity_kva"] or form["fault_transformer_capacity_kva"]
    )
    form["transformer_uk_percent"] = (
        form["transformer_uk_percent"] or form["fault_transformer_uk_percent"]
    )
    form["fault_transformer_series_code"] = form["transformer_series_code"]
    form["fault_transformer_capacity_kva"] = form["transformer_capacity_kva"]
    form["fault_transformer_uk_percent"] = form["transformer_uk_percent"]
    form["fault_transformer_hv_voltage_kv"] = (
        form["fault_transformer_hv_voltage_kv"] or "10"
    )
    form["fault_transformer_vector_group"] = (
        form["fault_transformer_vector_group"] or "Dyn11"
    )
    form["fault_connection_type"] = form["fault_connection_type"] or "direct"
    # Standard 380/400 V system maximum three-phase short-circuit calculation.
    # Non-standard voltages remain explicit expert inputs.
    try:
        if form["phase"] == "3" and float(form["voltage_v"]) in {380.0, 400.0}:
            form["voltage_factor_c"] = "1.05"
    except ValueError:
        pass
    rules = db.rules_by_code()
    result = calculate_simple_load_selection(form, rules).to_dict()
    explicit_line_short_circuit = form["short_circuit_line_enabled"].lower() in {
        "1", "true", "on", "yes"
    }
    automatic_line_short_circuit = bool(
        form["phase"] == "3"
        and form["length_m"]
        and form["transformer_series_code"]
        and form["transformer_capacity_kva"]
        and form["transformer_uk_percent"]
        and form["conductor_family"] in {"BV", "YJV"}
    )
    if automatic_line_short_circuit and not explicit_line_short_circuit:
        form["short_circuit_line_enabled"] = "true"
        form["short_circuit_line_type"] = form["short_circuit_line_type"] or "cable"
        form["source_impedance_mode"] = (
            form["source_impedance_mode"] or "short_circuit_capacity"
        )
        form["source_short_circuit_capacity_mva"] = (
            form["source_short_circuit_capacity_mva"] or "100"
        )
        form["breaker_installation_point"] = (
            form["breaker_installation_point"] or "line_start"
        )
    if explicit_line_short_circuit or automatic_line_short_circuit:
        cable_candidates = result.get("outputs", {}).get("cable_candidates", [])
        selected_cable = cable_candidates[0] if cable_candidates else {}
        line_section_mm2 = form["short_circuit_line_section_mm2"] or str(
            selected_cable.get("section_mm2") or ""
        )
        line_data = {
            "phase": form["phase"],
            "voltage_v": form["voltage_v"],
            "transformer_series_code": form["transformer_series_code"],
            "transformer_capacity_kva": form["transformer_capacity_kva"],
            "transformer_lv_rated_voltage_v": "400" if form["phase"] == "3" and form["voltage_v"] in {"380", "380.0", "400", "400.0"} else form["voltage_v"],
            "transformer_lv_no_load_voltage_v": form["transformer_lv_no_load_voltage_v"],
            "transformer_uk_percent": form["transformer_uk_percent"],
            "transformer_pk_kw": form["transformer_pk_kw"],
            "source_impedance_mode": form["source_impedance_mode"],
            "source_short_circuit_capacity_mva": form["source_short_circuit_capacity_mva"],
            "source_r_ohm": form["source_r_ohm"],
            "source_x_ohm": form["source_x_ohm"],
            "upstream_r_ohm": form["upstream_r_ohm"],
            "upstream_x_ohm": form["upstream_x_ohm"],
            "upstream_impedance_reference": form["upstream_impedance_reference"],
            "length_m": form["length_m"],
            "line_type": form["short_circuit_line_type"],
            "conductor_family": form["conductor_family"],
            "installation_scenario": form["installation_scenario"],
            "line_section_mm2": line_section_mm2,
            "breaker_icu_ka": form["breaker_icu_ka"],
            "breaker_installation_point": form["breaker_installation_point"] or "line_start",
            "voltage_factor_c": form["voltage_factor_c"],
        }
        if form["short_circuit_line_type"] == "busway":
            line_data.update({
                "line_r_ohm_per_km": form["busway_r_ohm_per_km"],
                "line_x_ohm_per_km": form["busway_x_ohm_per_km"],
                "line_impedance_reference": form["busway_impedance_reference"],
            })
        estimate = result.get("outputs", {}).get("short_circuit_estimate", {})
        if estimate.get("mode") == "exact_table":
            line_data["transformer_lv_outlet_ik_ka"] = estimate.get("ik_ka")
        result["line_end_short_circuit"] = calculate_transformer_feeder_three_phase_short_circuit(
            line_data, rules
        ).to_dict()
        line_outputs = result["line_end_short_circuit"].get("outputs", {})
        if line_outputs.get("terminal_short_circuit_current_ka") is not None:
            for stage in result.get("outputs", {}).get("workflow_stages", []):
                if stage.get("code") == "short_circuit":
                    stage["label"] = "短路电流与Icu要求"
                    stage["state"] = "completed"
            incomplete = result.get("outputs", {}).get("incomplete_checks", [])
            result["outputs"]["incomplete_checks"] = [
                "已选断路器Icu实物复核"
                if item == "短路电流与分断能力"
                else item
                for item in incomplete
            ]
            result["warnings"] = [
                warning
                for warning in result.get("warnings", [])
                if not warning.startswith("未提供安装点预期短路电流")
                and not warning.startswith("以上为普通负荷回路连续暂算")
            ]
            result["warnings"].append(
                "线路末端三相短路电流及Icu最低要求已算出；"
                "选定具体断路器后仍须按产品样本复核实际Icu、脱扣特性和选择性。"
            )
        if line_section_mm2:
            result["line_short_circuit_selection"] = {
                "section_mm2": line_section_mm2,
                "source": (
                    "采用本次电缆初选后的最终截面"
                    if not form["short_circuit_line_section_mm2"]
                    else "兼容接口传入的既有线路截面"
                ),
            }
    if form["earth_fault_enabled"].lower() in {"1", "true", "on", "yes"}:
        mcb_candidate = next(
            (
                item
                for item in result.get("outputs", {}).get(
                    "breaker_design_candidates", []
                )
                if item.get("family_code") == "MCB"
            ),
            None,
        )
        circuit_rating = form["circuit_rated_current_a"]
        circuit_rating_source = "用户提供的既有回路额定电流"
        if not circuit_rating and mcb_candidate:
            circuit_rating = str(mcb_candidate.get("rated_current_a") or "")
            circuit_rating_source = "本次断路器初选的MCB额定电流"
        form["circuit_rated_current_a"] = circuit_rating
        earth_fault_data = {
            "earthing_system": form["earthing_system"],
            "nominal_line_to_earth_voltage_v": nominal_u0,
            "circuit_application": form["circuit_application"],
            "circuit_rated_current_a": circuit_rating,
            "fault_loop_impedance_ohm": form["fault_loop_impedance_ohm"],
            "fault_loop_impedance_reference": form["fault_loop_impedance_reference"],
            "protection_type": form["protection_type"],
            "protective_device_characteristic": form["protective_device_characteristic"],
            "protective_device_rated_current_a": form["protective_device_rated_current_a"],
            "protective_device_operating_current_a": form["protective_device_operating_current_a"],
            "protective_device_operating_reference": form["protective_device_operating_reference"],
            "rcd_downstream_of_pen_split": form["rcd_downstream_of_pen_split"],
        }
        cable_candidates = result.get("outputs", {}).get("cable_candidates", [])
        selected_cable = cable_candidates[0] if cable_candidates else {}
        protective_core_role_source = "用户确认"
        if form["conductor_configuration"] == "yjv_5c_3ph_n_pe":
            if not form["fault_fourth_conductor_role"]:
                form["fault_fourth_conductor_role"] = "PE"
            if form["fault_fourth_conductor_role"] == "PE":
                protective_core_role_source = (
                    "五芯电缆设计初选：系统按其中一芯为PE组成故障回路；"
                    "正式结果仍需按实际芯线用途确认"
                )
        if (
            form["protective_device_characteristic"] in {"mcb_b", "mcb_c"}
            and not earth_fault_data["protective_device_rated_current_a"]
        ):
            if mcb_candidate:
                earth_fault_data["protective_device_rated_current_a"] = (
                    mcb_candidate.get("rated_current_a")
                )
        structure = selected_cable.get("fault_loop_structure")
        if (
            not form["fault_loop_impedance_ohm"]
            and form["conductor_configuration"]
            in {"yjv_4c_3ph_n_pe", "yjv_5c_3ph_n_pe"}
            and form["fault_fourth_conductor_role"] == "PE"
            and isinstance(structure, dict)
            and selected_cable.get("section_mm2") is not None
            and form["length_m"]
            and form["fault_transformer_series_code"]
            and form["fault_transformer_capacity_kva"]
            and form["fault_transformer_uk_percent"]
        ):
            segments = []
            if form["fault_connection_type"] == "busway":
                busway_impedance = None
                try:
                    if (
                        form["fault_busway_series_code"]
                        and form["fault_busway_rating_a"]
                    ):
                        busway_impedance = lookup_busway_phase_pe_impedance(
                            form["fault_busway_series_code"],
                            float(form["fault_busway_rating_a"]),
                        )
                except ValueError:
                    busway_impedance = None
                segments.append({
                    "role": "transformer_to_main_switchboard",
                    "segment_type": "busway",
                    "name": "变压器至低压总柜母线槽",
                    "calculation_mode": "explicit_per_km",
                    "resistance_ohm_per_km": (
                        busway_impedance["resistance_ohm_per_km"]
                        if busway_impedance
                        else form["fault_connection_r_ohm_per_km"]
                    ),
                    "reactance_ohm_per_km": (
                        busway_impedance["reactance_ohm_per_km"]
                        if busway_impedance
                        else form["fault_connection_x_ohm_per_km"]
                    ),
                    "length_m": form["fault_connection_length_m"],
                    "impedance_reference": (
                        f"{busway_impedance['source']} "
                        f"{busway_impedance['document_reference']}，"
                        f"{busway_impedance['page']}，"
                        f"{busway_impedance['condition']}"
                        if busway_impedance
                        else form["fault_connection_impedance_reference"]
                    ),
                    "source_rule_code": (
                        busway_impedance["source_rule_code"]
                        if busway_impedance
                        else ""
                    ),
                })
                if busway_impedance:
                    result["busway_phase_pe_selection"] = busway_impedance
            if form["conductor_configuration"] == "yjv_4c_3ph_n_pe":
                segments.append({
                    "role": "outgoing_circuit",
                    "segment_type": "cable",
                    "name": "线路末端YJV四芯回路",
                    "calculation_mode": "yjv_four_core_catalog",
                    "configuration_code": form["conductor_configuration"],
                    "fourth_conductor_role": form["fault_fourth_conductor_role"],
                    "phase_section_mm2": selected_cable["section_mm2"],
                    "length_m": form["length_m"],
                })
            else:
                segments.append({
                    "role": "outgoing_circuit",
                    "segment_type": "cable",
                    "name": "线路末端YJV五芯回路",
                    "calculation_mode": "copper_cable",
                    "cable_data": {
                        "conductor_material": "copper",
                        "phase_section_mm2": selected_cable["section_mm2"],
                        "protective_section_mm2": "",
                        "length_m": form["length_m"],
                        "conductor_temperature_c": 20,
                        "phase_conductor_form": "stranded",
                        "protective_conductor_form": "stranded",
                        "fault_resistance_multiplier": 1.5,
                        "fault_resistance_multiplier_reference": (
                            "《工业与民用供配电设计手册（第四版）》"
                            "第4.6.4节(1)第4项，PDF第335页"
                        ),
                        "cable_structure_code": form[
                            "conductor_configuration"
                        ],
                        "frequency_hz": 50,
                    },
                })
            earth_fault_data["fault_loop_chain_data"] = {
                "target_point": "line_end",
                "transformer_phase_pe_data": {
                    "transformer_series_code": form[
                        "fault_transformer_series_code"
                    ],
                    "transformer_vector_group": form[
                        "fault_transformer_vector_group"
                    ],
                    "transformer_capacity_kva": form[
                        "fault_transformer_capacity_kva"
                    ],
                    "transformer_uk_percent": form[
                        "fault_transformer_uk_percent"
                    ],
                    "transformer_hv_voltage_kv": form[
                        "fault_transformer_hv_voltage_kv"
                    ],
                    "transformer_lv_rated_voltage_v": "400",
                    "fault_loop_origin": "transformer_lv_terminal",
                },
                "segments": segments,
            }
        if (
            not form["fault_loop_impedance_ohm"]
            and "fault_loop_chain_data" not in earth_fault_data
            and isinstance(structure, dict)
            and form["length_m"]
            and form["fault_fourth_conductor_role"] == "PE"
        ):
            earth_fault_data["conventional_method_data"] = {
                "conductor_material": "copper",
                "phase_section_mm2": structure.get("phase_section_mm2"),
                "protective_section_mm2": structure.get("protective_section_mm2"),
                "length_m": form["length_m"],
                "conductors_in_same_cable": True,
            }
        earth_fault_result = calculate_tn_earth_fault_protection(
            earth_fault_data, rules
        ).to_dict()
        earth_outputs = earth_fault_result.setdefault("outputs", {})
        earth_outputs.update({
            "nominal_line_to_earth_voltage_source": nominal_u0_source,
            "circuit_rated_current_source": circuit_rating_source,
            "protective_core_role": form["fault_fourth_conductor_role"] or "无法判断",
            "protective_core_role_source": protective_core_role_source,
        })
        try:
            earth_outputs.setdefault(
                "nominal_line_to_earth_voltage_v", float(nominal_u0)
            )
        except (TypeError, ValueError):
            pass
        try:
            earth_outputs.setdefault("circuit_rated_current_a", float(circuit_rating))
        except (TypeError, ValueError):
            pass
        maximum_ia = earth_outputs.get("maximum_permitted_operating_current_a")
        if mcb_candidate and maximum_ia is not None:
            try:
                mcb_in = float(mcb_candidate.get("rated_current_a"))
                maximum_ia_value = float(maximum_ia)
            except (TypeError, ValueError):
                mcb_in = 0
                maximum_ia_value = 0
            if mcb_in > 0 and maximum_ia_value > 0:
                curve_candidates = []
                for curve, multiplier in (("B", 5.0), ("C", 10.0)):
                    operating_current = mcb_in * multiplier
                    curve_candidates.append({
                        "curve": curve,
                        "rated_current_a": mcb_in,
                        "guaranteed_operating_multiplier": multiplier,
                        "operating_current_a": operating_current,
                        "maximum_permitted_operating_current_a": maximum_ia_value,
                        "provisional_status": (
                            "通过" if operating_current <= maximum_ia_value else "不通过"
                        ),
                        "source": (
                            "《工业与民用供配电设计手册（第四版）》"
                            "表11.3-4、表11.3-5，PDF第1010页（印刷第978页）"
                        ),
                    })
                earth_outputs["protective_device_curve_candidates"] = curve_candidates
                earth_outputs["protective_device_selection_note"] = (
                    "系统同时校核B/C型MCB参数候选，不替用户猜曲线；"
                    "实际产品曲线或整定值确认后再形成自动切断结论。"
                )
                earth_fault_result["warnings"] = [
                    warning
                    for warning in earth_fault_result.get("warnings", [])
                    if not warning.startswith("请选择已核实的MCB B/C特性")
                ]
        result["earth_fault"] = earth_fault_result
        earth_constraint_ready = (
            earth_outputs.get("prospective_earth_fault_current_a") is not None
            and earth_outputs.get("maximum_permitted_operating_current_a") is not None
        )
        for stage in result.get("outputs", {}).get("workflow_stages", []):
            if stage.get("code") == "earth_fault":
                if earth_fault_result.get("provisional_status") in {"通过", "不通过"}:
                    stage["state"] = "completed"
                elif earth_constraint_ready:
                    stage["label"] = "接地故障与保护约束"
                    stage["state"] = "candidate"
        if earth_fault_result.get("provisional_status") == "通过":
            incomplete = result.get("outputs", {}).get("incomplete_checks", [])
            result["outputs"]["incomplete_checks"] = [
                item for item in incomplete if item != "故障防护"
            ]
        elif earth_constraint_ready:
            incomplete = result.get("outputs", {}).get("incomplete_checks", [])
            result["outputs"]["incomplete_checks"] = [
                "保护器件曲线/整定实物复核" if item == "故障防护" else item
                for item in incomplete
            ]
        explicit_pe_thermal = form["pe_thermal_enabled"].lower() in {
            "1", "true", "on", "yes"
        }
        automatic_pe_thermal = bool(
            earth_outputs.get("prospective_earth_fault_current_a") is not None
            and isinstance(structure, dict)
            and form["fault_fourth_conductor_role"] == "PE"
        )
        if explicit_pe_thermal or automatic_pe_thermal:
            pe_section = ""
            if isinstance(structure, dict) and form["fault_fourth_conductor_role"] == "PE":
                pe_section = structure.get("protective_section_mm2", "")
            pe_thermal_result = calculate_pe_thermal_withstand(
                {
                    "protective_conductor_section_mm2": pe_section,
                    "protective_conductor_material": "copper",
                    "protective_conductor_insulation": "xlpe",
                    "protective_conductor_arrangement": "multicore_cable",
                    "prospective_fault_current_a": earth_fault_result.get(
                        "outputs", {}
                    ).get("prospective_earth_fault_current_a"),
                    "fault_clearing_time_s": form["fault_clearing_time_s"],
                    "let_through_energy_a2s": form["let_through_energy_a2s"],
                    "let_through_energy_reference": form[
                        "let_through_energy_reference"
                    ],
                },
                rules,
            ).to_dict()
            result["pe_thermal"] = pe_thermal_result
            for stage in result.get("outputs", {}).get("workflow_stages", []):
                if stage.get("code") == "pe_thermal":
                    if pe_thermal_result.get("provisional_status") in {"通过", "不通过"}:
                        stage["state"] = "completed"
                    elif pe_thermal_result.get("outputs", {}).get(
                        "maximum_permitted_clearing_time_s"
                    ) is not None:
                        stage["label"] = "PE热稳定约束"
                        stage["state"] = "candidate"
            if pe_thermal_result.get("provisional_status") == "通过":
                incomplete = result.get("outputs", {}).get(
                    "incomplete_checks", []
                )
                result["outputs"]["incomplete_checks"] = [
                    item for item in incomplete if item != "PE导体热稳定"
                ]
            elif pe_thermal_result.get("outputs", {}).get(
                "maximum_permitted_clearing_time_s"
            ) is not None:
                incomplete = result.get("outputs", {}).get("incomplete_checks", [])
                result["outputs"]["incomplete_checks"] = [
                    "PE切除时间/I²t实物复核" if item == "PE导体热稳定" else item
                    for item in incomplete
                ]
    line_short_circuit = result.get("line_end_short_circuit", {})
    line_outputs = line_short_circuit.get("outputs", {})
    explicit_phase_thermal = form["phase_thermal_enabled"].lower() in {
        "1", "true", "on", "yes"
    }
    automatic_phase_thermal = bool(
        line_outputs.get("terminal_short_circuit_current_ka") is not None
        and result.get("outputs", {}).get("cable_candidates")
    )
    if explicit_phase_thermal or automatic_phase_thermal:
        cable_candidates = result.get("outputs", {}).get("cable_candidates", [])
        selected_cable = cable_candidates[0] if cable_candidates else {}
        phase_short_circuit_current_a = None
        phase_short_circuit_source = ""
        try:
            phase_short_circuit_current_a = 1000 * float(
                line_outputs.get("line_start_short_circuit_current_ka")
            )
            phase_short_circuit_source = (
                "复用本次短路模块已计算的线路起点最大三相短路电流；"
                "该点对应馈线保护器安装位置和被保护电缆起点。"
            )
        except (TypeError, ValueError):
            pass
        phase_thermal_result = calculate_phase_conductor_thermal_withstand(
            {
                "phase_conductor_section_mm2": selected_cable.get("section_mm2", ""),
                "phase_conductor_material": "copper",
                "phase_conductor_insulation": (
                    "pvc" if selected_cable.get("family") == "BV" else "xlpe"
                    if selected_cable.get("family") == "YJV" else ""
                ),
                "prospective_fault_current_a": phase_short_circuit_current_a,
                "fault_clearing_time_s": form["fault_clearing_time_s"],
                "let_through_energy_a2s": form["let_through_energy_a2s"],
                "let_through_energy_reference": form[
                    "let_through_energy_reference"
                ],
            },
            rules,
        ).to_dict()
        if phase_short_circuit_current_a is not None:
            phase_thermal_result.setdefault("outputs", {}).update({
                "prospective_phase_short_circuit_current_a": round(
                    phase_short_circuit_current_a, 4
                ),
                "prospective_phase_short_circuit_source": phase_short_circuit_source,
            })
        result["phase_thermal"] = phase_thermal_result
        for stage in result.get("outputs", {}).get("workflow_stages", []):
            if stage.get("code") == "phase_thermal":
                if phase_thermal_result.get("provisional_status") in {"通过", "不通过"}:
                    stage["state"] = "completed"
                elif phase_thermal_result.get("outputs", {}).get(
                    "maximum_permitted_clearing_time_s"
                ) is not None:
                    stage["label"] = "相导体热稳定约束"
                    stage["state"] = "candidate"
        if phase_thermal_result.get("provisional_status") in {"通过", "不通过"}:
            incomplete = result.get("outputs", {}).get("incomplete_checks", [])
            result["outputs"]["incomplete_checks"] = [
                item for item in incomplete if item != "相导体热稳定"
            ]
        elif phase_thermal_result.get("outputs", {}).get(
            "maximum_permitted_clearing_time_s"
        ) is not None:
            incomplete = result.get("outputs", {}).get("incomplete_checks", [])
            result["outputs"]["incomplete_checks"] = [
                "相导体切除时间/I²t实物复核"
                if item == "相导体热稳定"
                else item
                for item in incomplete
            ]
    if form["existing_breaker_series"] == "SCHNEIDER.EASYPACT.CVS.2024":
        breaker_candidates = result.get("outputs", {}).get(
            "breaker_design_candidates", []
        )
        generic_breaker = breaker_candidates[0] if breaker_candidates else {}
        rated_current = (
            form["existing_breaker_rated_current_a"]
            or generic_breaker.get("rated_current_a")
            or ""
        )
        required_icu = line_outputs.get("required_breaking_capacity_ka")
        if rated_current and required_icu is not None:
            product_reference = select_easypact_cvs_reference(
                rated_current,
                required_icu,
                system_voltage_v=form["voltage_v"],
                trip_unit_family=(
                    form["existing_breaker_trip_unit_family"] or "TM-D"
                ),
            )
            product_reference["rated_current_source"] = (
                "用户填写的现场设备额定电流"
                if form["existing_breaker_rated_current_a"]
                else "复用本次通用断路器初选额定电流；现场铭牌仍须确认"
            )
            result["existing_breaker_product_reference"] = product_reference
            permitted_i2t = (
                result.get("phase_thermal", {})
                .get("outputs", {})
                .get("maximum_permitted_let_through_energy_a2s")
            )
            line_start_ik = line_outputs.get(
                "line_start_short_circuit_current_ka"
            )
            if (
                product_reference.get("frame_code")
                and permitted_i2t is not None
                and line_start_ik is not None
            ):
                result["existing_breaker_phase_thermal"] = (
                    evaluate_easypact_cvs_phase_thermal_reference(
                        product_reference,
                        line_start_ik,
                        permitted_i2t,
                    )
                )
        else:
            result["existing_breaker_product_reference"] = {
                "status": "无法判断",
                "provisional_status": "无法判断",
                "reason": (
                    "须先形成断路器额定电流候选和线路起点短路电流，"
                    "才能核对EasyPact CVS。"
                ),
            }
    return templates.TemplateResponse(
        request=request, name="quick.html",
        context={"form": form, "result": result, "load_groups": grouped_load_types(), "scenarios": INSTALLATION_SCENARIOS, "conductor_configurations": CONDUCTOR_CONFIGURATIONS, "tray_options": TRAY_CONFIGURATION_OPTIONS, "transformer_capacities": sorted(TRANSFORMER_LV_SHORT_CIRCUIT["rows"]), "fault_transformer_series": TRANSFORMER_PHASE_PE_IMPEDANCE["series"], "fault_transformer_capacities": sorted({capacity for series in TRANSFORMER_PHASE_PE_IMPEDANCE["series"].values() for capacity in series["rows"]}), "busway_phase_pe_series": BUSWAY_PHASE_PE_IMPEDANCE["series"], "busway_phase_pe_ratings": sorted({rating for series in BUSWAY_PHASE_PE_IMPEDANCE["series"].values() for rating in series["rows"]})},
    )

@app.post("/projects")
def create_project(code: str = Form(...), name: str = Form(...), description: str = Form("")):
    try:
        project_id = db.create_project(code, name, description)
    except sqlite3.IntegrityError:
        return RedirectResponse("/?message=" + quote("项目编号已存在。"), status_code=303)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: int, message: str = ""):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    return templates.TemplateResponse(
        request=request,
        name="project.html",
        context={
            "project": project,
            "circuits": db.list_circuits(project_id),
            "runs": db.list_runs(project_id),
            "message": message,
        },
    )


@app.post("/projects/{project_id}/circuits")
async def save_circuit(request: Request, project_id: int):
    if not db.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    form = await request.form()
    data = _form_to_circuit(form)
    if not data["code"] or not data["name"] or data["phase"] not in {"1", "3"}:
        return RedirectResponse(
            f"/projects/{project_id}?message=" + quote("回路编号、名称和相制填写不完整。"),
            status_code=303,
        )
    try:
        circuit_id = db.upsert_circuit(project_id, data)
    except sqlite3.IntegrityError as exc:
        return RedirectResponse(
            f"/projects/{project_id}?message=" + quote(f"保存失败：{exc}"),
            status_code=303,
        )
    return RedirectResponse(
        f"/projects/{project_id}?message=" + quote(f"回路 {data['code']} 已保存；旧计算记录已保留并标记过期。"),
        status_code=303,
    )


@app.post("/circuits/{circuit_id}/calculate")
def calculate_circuit(circuit_id: int):
    circuit = db.get_circuit(circuit_id)
    if not circuit:
        raise HTTPException(404, "回路不存在")
    rules = db.rules_by_code()
    outcomes = [outcome.to_dict() for outcome in calculate_all(circuit, rules)]
    run_ids = db.create_runs(circuit["project_id"], circuit, outcomes, rules)
    return RedirectResponse(f"/runs/{run_ids[0]}", status_code=303)


@app.post("/projects/{project_id}/calculate-all")
def calculate_project(project_id: int):
    circuits = db.list_circuits(project_id)
    rules = db.rules_by_code()
    count = 0
    for circuit in circuits:
        outcomes = [outcome.to_dict() for outcome in calculate_all(circuit, rules)]
        db.create_runs(project_id, circuit, outcomes, rules)
        count += 1
    return RedirectResponse(
        f"/projects/{project_id}?message=" + quote(f"已为 {count} 个回路生成新的计算记录。"),
        status_code=303,
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: int):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "计算记录不存在")
    return templates.TemplateResponse(request=request, name="run.html", context={"run": run})


@app.get("/runs/{run_id}/report.pdf")
def export_run_pdf(run_id: int):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "计算记录不存在")
    content = create_run_pdf(run)
    filename = f"{run['project_code']}-{run['circuit_code']}-{run['module']}.pdf"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/templates/circuits.xlsx")
def download_template():
    template_path = PACKAGE_DIR.parents[1] / "data" / "templates" / "circuit-import-template.xlsx"
    content = template_path.read_bytes() if template_path.exists() else create_input_template()
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''circuit-import-template.xlsx"},
    )


@app.post("/projects/{project_id}/import")
async def import_circuits(project_id: int, file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        return RedirectResponse(
            f"/projects/{project_id}?message=" + quote("请选择 .xlsx 文件。"), status_code=303
        )
    rows, errors = parse_circuit_workbook(await file.read())
    if errors:
        message = "导入失败：" + "；".join(errors[:8])
        if len(errors) > 8:
            message += f"；另有 {len(errors)-8} 项错误"
        return RedirectResponse(f"/projects/{project_id}?message=" + quote(message), status_code=303)
    for row in rows:
        db.upsert_circuit(project_id, row)
    return RedirectResponse(
        f"/projects/{project_id}?message=" + quote(f"成功导入 {len(rows)} 个回路。"),
        status_code=303,
    )


@app.get("/projects/{project_id}/export.xlsx")
def export_project(project_id: int):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    content = create_project_export(
        project, db.list_circuits(project_id), db.list_runs(project_id, 1000), db.list_rules()
    )
    filename = f"{project['code']}-电气计算成果.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, message: str = ""):
    return templates.TemplateResponse(
        request=request, name="rules.html", context={"rules": db.list_rules(), "message": message}
    )


@app.post("/rules/{rule_id}")
async def update_rule(request: Request, rule_id: int):
    form = await request.form()
    status = str(form.get("status", "pending"))
    required_for_approval = [
        str(form.get("document_name", "")).strip(),
        str(form.get("clause_no", "")).strip(),
        str(form.get("original_text", "")).strip(),
        str(form.get("page_no", "")).strip(),
    ]
    if status == "approved" and not all(required_for_approval):
        return RedirectResponse(
            "/rules?message=" + quote("批准失败：文件、条文号、原文和页码必须完整。"),
            status_code=303,
        )
    db.update_rule(rule_id, {key: str(form.get(key, "")).strip() for key in (
        "name", "status", "document_name", "document_version", "clause_no",
        "original_text", "page_no", "note",
    )})
    return RedirectResponse("/rules?message=" + quote("依据记录已更新。"), status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


def _form_to_circuit(form) -> dict[str, object]:
    text_fields = {"code", "name", "phase", "cable_spec", "breaker_model"}
    fields = [
        "code", "name", "phase", "voltage_v", "installed_power_kw", "demand_factor",
        "power_factor", "efficiency", "length_m", "cable_spec", "cable_ampacity_a",
        "cable_r_ohm_per_km", "cable_x_ohm_per_km", "voltage_drop_limit_pct",
        "breaker_model", "breaker_rating_a", "breaking_capacity_ka", "source_r_ohm",
        "source_x_ohm", "transformer_r_ohm", "transformer_x_ohm",
    ]
    data: dict[str, object] = {}
    for field in fields:
        raw = str(form.get(field, "")).strip()
        if field in text_fields:
            data[field] = raw
        elif raw == "":
            data[field] = None
        else:
            try:
                data[field] = float(raw)
            except ValueError:
                data[field] = None
    return data
