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

from . import __version__
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
from .motor import (
    MotorBusLoadCondition,
    MotorCablePreselectionInput,
    MotorCatalogQuery,
    MotorKnownBasis,
    MotorLoadInput,
    MotorNetworkInput,
    MotorStartingFrequency,
    MotorStartingVoltageScenario,
)
from .motor_catalog import (
    AVAILABLE_RATED_OUTPUT_POWERS_KW,
    COMPLETE_SELECTION_POWERS_KW,
    resolve_motor_reference_parameters,
)
from .motor_circuit_service import evaluate_motor_cable_candidates_in_network
from .motor_control_products import select_motor_control_references
from .motor_engine import (
    calculate_motor_cable_preselection,
    calculate_motor_load,
    calculate_motor_selection_constraints,
    resolve_motor_starting_voltage_requirement,
)
from .product_protection import (
    evaluate_easypact_cvs_phase_thermal_reference,
    select_easypact_cvs_reference,
)
from .drawing_audit import (
    InstalledAssembly,
    InstalledIncomingBreaker,
    audit_drawing_complete_circuit,
)
from .drawing_project_summary import summarize_drawing_circuits
from .network_input import (
    CircuitNetworkInput,
    CircuitTaskMode,
    ExistingBreakerInput,
    FeederSegmentInput,
    TerminalLoadKind,
    build_circuit_network_requests,
)
from .complete_circuit import InputBasis, Phase, SegmentType
from .pole_configuration import (
    PoleAndNeutralInput,
    evaluate_pole_and_neutral_configuration,
)
from .radial_circuit_service import calculate_radial_complete_circuit
from .reports import NETWORK_INPUT_LABELS, create_drawing_project_pdf, create_motor_run_pdf, create_network_run_pdf, create_run_pdf
from .spreadsheets import (
    create_input_template,
    create_drawing_project_export,
    create_motor_run_export,
    create_network_run_export,
    create_project_export,
    parse_circuit_workbook,
)


PACKAGE_DIR = Path(__file__).resolve().parent
db = Database(os.environ.get("ELECTRICAL_CALC_DB", DEFAULT_DB))
app = FastAPI(title="电气工程计算自动化平台", version=__version__)
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


def _motor_form_defaults() -> dict[str, str]:
    return {
        "project_id": "",
        "circuit_code": "M-001",
        "circuit_name": "电动机回路",
        "known_basis": "rated_output_power_kw",
        "known_value": "30",
        "rated_voltage_v": "380",
        "poles": "4",
        "motor_efficiency_percent": "",
        "motor_power_factor": "",
        "locked_rotor_current_ratio": "",
        "starting_frequency": "infrequent",
        "bus_load_condition": "lighting_or_sensitive_loads",
        "conductor_configuration": "yjv_4c_3ph_n_pe",
        "cable_path_adjustment": "",
        "installation_scenario": "tray",
        "length_m": "50",
        "installation_temperature_c": "40",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "enclosed_circuit_count": "",
        "transformer_family": "scb11",
        "transformer_actual_model": "SCB11",
        "transformer_capacity_kva": "630",
        "transformer_uk_percent": "6",
        "upstream_short_circuit_capacity_mva": "100",
        "preconnected_reactive_load_mvar": "",
        "motor_starting_time_s": "",
    }


@app.get("/motor", response_class=HTMLResponse)
def motor_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="motor.html",
        context={
            "form": _motor_form_defaults(),
            "errors": [],
            "catalog_result": None,
            "load_result": None,
            "voltage_requirement": None,
            "selection_result": None,
            "cable_result": None,
            "network_result": None,
            "network_fault_checks_complete": False,
            "network_thermal_checks_complete": False,
            "control_product_result": None,
            "cable_configuration_notice": None,
            "motor_catalog_powers_kw": AVAILABLE_RATED_OUTPUT_POWERS_KW,
            "motor_complete_selection_powers_kw": COMPLETE_SELECTION_POWERS_KW,
            "projects": db.list_projects(),
        },
    )


@app.post("/motor", response_class=HTMLResponse)
async def motor_calculate(request: Request):
    submitted = await request.form()
    form = {
        key: str(submitted.get(key, "")).strip()
        for key in _motor_form_defaults()
    }
    cable_configuration_notice = None
    if form["cable_path_adjustment"] == "four_core_conduit_to_tray":
        cable_configuration_notice = (
            "当前资料没有YJV四芯穿管基础载流量表，系统保留四芯结构并改用"
            "有已核实表格的槽盒敷设。若必须穿管，请改选YJV三芯电缆＋独立PE。"
        )
    elif form["cable_path_adjustment"] == "four_core_conduit_to_three_core" or (
        form["conductor_configuration"] == "yjv_4c_3ph_n_pe"
        and form["installation_scenario"] == "conduit"
    ):
        form["conductor_configuration"] = "yjv_3c_3ph_pe"
        cable_configuration_notice = (
            "当前资料没有YJV四芯穿管基础载流量表，系统已改用有已核实表格的"
            "YJV三芯电缆＋独立PE。若必须采用四芯结构，请改选槽盒或埋地管槽。"
        )
    errors: list[str] = []
    try:
        known_value = float(form["known_value"])
        voltage_v = float(form["rated_voltage_v"])
        poles = int(form["poles"])
        if known_value <= 0 or voltage_v <= 0:
            raise ValueError
    except ValueError:
        errors.append("额定功率或电流、电压必须填写大于0的数值。")
        known_value = 0.0
        voltage_v = 0.0
        poles = 4

    try:
        known_basis = MotorKnownBasis(form["known_basis"])
        starting_frequency = MotorStartingFrequency(form["starting_frequency"])
        bus_condition = MotorBusLoadCondition(form["bus_load_condition"])
    except ValueError:
        errors.append("电动机输入方式或启动场景无效。")
        known_basis = MotorKnownBasis.RATED_OUTPUT_POWER_KW
        starting_frequency = MotorStartingFrequency.UNKNOWN
        bus_condition = MotorBusLoadCondition.UNKNOWN

    motor_starting_time_s = None
    if form["motor_starting_time_s"]:
        try:
            motor_starting_time_s = float(form["motor_starting_time_s"])
            if motor_starting_time_s <= 0:
                raise ValueError
        except ValueError:
            errors.append("电动机实际启动时间必须填写大于0的数值或留空。")

    rules = db.rules_by_code()
    catalog_result = None
    efficiency = None
    power_factor = None
    locked_ratio = None
    motor_efficiency_class = None
    motor_parameter_source = None

    manual_efficiency = None
    manual_power_factor = None
    if form["motor_efficiency_percent"]:
        try:
            manual_efficiency_percent = float(form["motor_efficiency_percent"])
            if not 0 < manual_efficiency_percent <= 100:
                raise ValueError
            manual_efficiency = manual_efficiency_percent / 100
        except ValueError:
            errors.append("效率必须大于0且不大于100%。")
    if form["motor_power_factor"]:
        try:
            manual_power_factor = float(form["motor_power_factor"])
            if not 0 < manual_power_factor <= 1:
                raise ValueError
        except ValueError:
            errors.append("运行功率因数必须大于0且不大于1。")
    if (
        known_basis == MotorKnownBasis.RATED_OUTPUT_POWER_KW
        and bool(form["motor_efficiency_percent"])
        != bool(form["motor_power_factor"])
    ):
        errors.append("按输出功率计算时，效率和运行功率因数必须同时填写或同时留空。")

    if known_basis == MotorKnownBasis.RATED_OUTPUT_POWER_KW and not errors:
        catalog_result = resolve_motor_reference_parameters(
            MotorCatalogQuery(known_value, poles), rules
        )
        if catalog_result.outputs["matched"]:
            efficiency = catalog_result.outputs["efficiency"]
            power_factor = catalog_result.outputs["power_factor"]
            locked_ratio = catalog_result.outputs["locked_rotor_current_ratio"]
            motor_efficiency_class = catalog_result.outputs["source"].get(
                "efficiency_class"
            )
            motor_parameter_source = "catalog"

        if manual_efficiency is not None and manual_power_factor is not None:
            efficiency = manual_efficiency
            power_factor = manual_power_factor
            motor_efficiency_class = None
            motor_parameter_source = "manual"
    elif known_basis == MotorKnownBasis.NAMEPLATE_CURRENT_A:
        power_factor = manual_power_factor
        motor_parameter_source = "nameplate_current"

    if form["locked_rotor_current_ratio"]:
        try:
            locked_ratio = float(form["locked_rotor_current_ratio"])
            if locked_ratio <= 0:
                raise ValueError
        except ValueError:
            errors.append("堵转电流倍数必须填写大于0的数值。")

    load_result = None
    voltage_requirement = None
    selection_result = None
    cable_result = None
    network_result = None
    network_recommended_candidate = None
    network_recommendation_level = None
    network_fault_checks_complete = False
    network_thermal_checks_complete = False
    control_product_result = None
    motor_input = None
    cable_input = None
    if not errors:
        motor_input = MotorLoadInput(
                known_basis=known_basis,
                known_value=known_value,
                rated_voltage_v=voltage_v,
                power_factor=power_factor,
                efficiency=efficiency,
                locked_rotor_current_ratio=locked_ratio,
        )
        load_result = calculate_motor_load(motor_input, rules)
        voltage_requirement = resolve_motor_starting_voltage_requirement(
            MotorStartingVoltageScenario(starting_frequency, bus_condition), rules
        )
        rated_current = load_result.outputs.get("rated_current_a")
        starting_current = load_result.outputs.get("starting_current_a")
        if rated_current is not None and starting_current is not None:
            selection_result = calculate_motor_selection_constraints(
                rated_current, starting_current, rules
            )
            control_product_result = select_motor_control_references(
                motor_rated_current_a=rated_current,
                motor_starting_current_a=starting_current,
                motor_rated_output_power_kw=(
                    known_value
                    if known_basis == MotorKnownBasis.RATED_OUTPUT_POWER_KW
                    else None
                ),
                system_voltage_v=voltage_v,
                motor_starting_time_s=motor_starting_time_s,
                motor_efficiency_class=motor_efficiency_class,
            )
        if rated_current is not None:
            try:
                length_m = float(form["length_m"])
                temperature_c = (
                    float(form["installation_temperature_c"])
                    if form["installation_temperature_c"]
                    else None
                )
                tray_layers = (
                    int(form["tray_layers"]) if form["tray_layers"] else None
                )
                tray_cables = (
                    int(form["tray_cables_per_layer"])
                    if form["tray_cables_per_layer"]
                    else None
                )
                enclosed_count = (
                    int(form["enclosed_circuit_count"])
                    if form["enclosed_circuit_count"]
                    else None
                )
                configuration = form["conductor_configuration"]
                family = "BV" if configuration.startswith("bv_") else "YJV"
                cable_input = MotorCablePreselectionInput(
                        rated_current_a=rated_current,
                        running_power_factor=power_factor,
                        rated_voltage_v=voltage_v,
                        length_m=length_m,
                        conductor_family=family,
                        conductor_configuration_code=configuration,
                        installation_scenario=form["installation_scenario"],
                        installation_temperature_c=temperature_c,
                        tray_type=(
                            "horizontal_perforated"
                            if form["installation_scenario"] == "tray"
                            else None
                        ),
                        tray_layers=tray_layers,
                        tray_cables_per_layer=tray_cables,
                        enclosed_circuit_count=enclosed_count,
                )
                cable_result = calculate_motor_cable_preselection(
                    cable_input, rules
                )
            except ValueError:
                errors.append("线路长度及补充敷设条件必须填写有效数值。")
        network_fields = (
            form["transformer_family"],
            form["transformer_capacity_kva"],
            form["transformer_uk_percent"],
        )
        if all(network_fields) and motor_input is not None and cable_input is not None:
            try:
                motor_u0, _ = _derive_nominal_line_to_earth_voltage(
                    "3", str(voltage_v)
                )
                network_result = evaluate_motor_cable_candidates_in_network(
                    motor_input,
                    cable_input,
                    MotorNetworkInput(
                        transformer_family=form["transformer_family"],
                        transformer_capacity_kva=float(
                            form["transformer_capacity_kva"]
                        ),
                        transformer_uk_percent=float(
                            form["transformer_uk_percent"]
                        ),
                        upstream_short_circuit_capacity_mva=float(
                            form["upstream_short_circuit_capacity_mva"] or "100"
                        ),
                        system_voltage_v=voltage_v,
                        line_to_earth_voltage_v=float(motor_u0),
                        minimum_starting_bus_voltage_percent=(
                            voltage_requirement.outputs.get(
                                "minimum_bus_voltage_percent"
                            )
                        ),
                        preconnected_reactive_load_mvar=(
                            float(form["preconnected_reactive_load_mvar"])
                            if form["preconnected_reactive_load_mvar"]
                            else None
                        ),
                        motor_starting_time_s=motor_starting_time_s,
                    ),
                    rules,
                )
                network_candidates = network_result.outputs.get("candidates", [])
                recommended_position = network_result.outputs.get(
                    "recommended_candidate_position"
                )
                if recommended_position is not None:
                    network_recommended_candidate = network_candidates[
                        int(recommended_position)
                    ]
                    network_recommendation_level = "network_checked"
                elif network_candidates:
                    network_recommended_candidate = network_candidates[0]
                    network_recommendation_level = "basic_only"
                network_fault_checks_complete = bool(network_candidates) and all(
                    item["chain"]["outputs"].get(
                        "terminal_three_phase_short_circuit_ka"
                    )
                    is not None
                    and item["chain"]["outputs"].get(
                        "terminal_earth_fault_current_a"
                    )
                    is not None
                    for item in network_candidates
                )
                network_thermal_checks_complete = bool(network_candidates) and all(
                    item["phase_thermal_constraint"]["outputs"].get(
                        "maximum_permitted_clearing_time_s"
                    )
                    is not None
                    and item.get("pe_thermal_constraint") is not None
                    and item["pe_thermal_constraint"]["outputs"].get(
                        "maximum_permitted_clearing_time_s"
                    )
                    is not None
                    for item in network_candidates
                )
                if control_product_result and network_candidates:
                    nodes = network_candidates[0]["chain"]["outputs"].get(
                        "node_results", []
                    )
                    installation_ik_ka = (
                        nodes[0].get("three_phase_short_circuit_ka")
                        if nodes
                        else None
                    )
                    control_product_result = select_motor_control_references(
                        motor_rated_current_a=float(rated_current),
                        motor_starting_current_a=float(starting_current),
                        motor_rated_output_power_kw=(
                            known_value
                            if known_basis
                            == MotorKnownBasis.RATED_OUTPUT_POWER_KW
                            else None
                        ),
                        system_voltage_v=voltage_v,
                        motor_starting_time_s=motor_starting_time_s,
                        motor_efficiency_class=motor_efficiency_class,
                        installation_point_max_short_circuit_ka=(
                            float(installation_ik_ka)
                            if installation_ik_ka is not None
                            else None
                        ),
                    )
            except ValueError:
                errors.append("变压器及上级系统条件必须填写有效数值。")

    catalog_dict = catalog_result.to_dict() if catalog_result else None
    load_dict = load_result.to_dict() if load_result else None
    selection_dict = selection_result.to_dict() if selection_result else None
    cable_dict = cable_result.to_dict() if cable_result else None
    network_dict = network_result.to_dict() if network_result else None
    if form["project_id"] and not errors and network_dict and network_recommended_candidate:
        try:
            project_id = int(form["project_id"])
        except ValueError:
            errors.append("保存项目无效。")
        else:
            if not db.get_project(project_id):
                errors.append("保存项目不存在。")
            else:
                snapshot = {
                    "catalog": catalog_dict,
                    "load": load_dict,
                    "selection": selection_dict,
                    "cable": cable_dict,
                    "network": network_dict,
                    "recommended_candidate": network_recommended_candidate,
                    "motor_parameter_source": motor_parameter_source,
                    "motor_parameter_values": {
                        "efficiency": efficiency,
                        "power_factor": power_factor,
                        "locked_rotor_current_ratio": locked_ratio,
                    },
                    "status": network_dict.get("status", "无法判断"),
                    "provisional_status": network_dict.get("provisional_status", "无法判断"),
                    "warnings": list(network_dict.get("warnings", [])),
                }
                motor = db.save_project_motor(project_id, form)
                rule_codes = _collect_rule_codes(snapshot)
                snapshot_rules = {
                    code: rules[code] for code in sorted(rule_codes) if code in rules
                }
                run_id = db.create_motor_run(
                    project_id, motor, engine_version=__version__,
                    input_snapshot=form, result=snapshot, rule_snapshot=snapshot_rules,
                )
                return RedirectResponse(f"/motor-runs/{run_id}", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="motor.html",
        context={
            "form": form,
            "errors": errors,
            "catalog_result": catalog_dict,
            "load_result": load_dict,
            "voltage_requirement": (
                voltage_requirement.to_dict() if voltage_requirement else None
            ),
            "selection_result": (
                selection_dict
            ),
            "cable_result": cable_dict,
            "network_result": network_dict,
            "network_recommended_candidate": network_recommended_candidate,
            "network_recommendation_level": network_recommendation_level,
            "network_fault_checks_complete": network_fault_checks_complete,
            "network_thermal_checks_complete": network_thermal_checks_complete,
            "control_product_result": control_product_result,
            "cable_configuration_notice": cable_configuration_notice,
            "motor_catalog_powers_kw": AVAILABLE_RATED_OUTPUT_POWERS_KW,
            "motor_complete_selection_powers_kw": COMPLETE_SELECTION_POWERS_KW,
            "motor_parameter_source": motor_parameter_source,
            "motor_parameter_values": {
                "efficiency": efficiency,
                "power_factor": power_factor,
                "locked_rotor_current_ratio": locked_ratio,
            },
            "projects": db.list_projects(),
        },
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


def _preferred_quick_breaker_candidate(result: dict[str, object]) -> dict[str, object]:
    candidates = result.get("outputs", {}).get("breaker_design_candidates", [])
    usable = [
        item for item in candidates if item.get("selected_icu_ka") is not None
    ]
    if not usable:
        usable = list(candidates)
    return min(
        usable,
        key=lambda item: (
            float(item.get("rated_current_a") or float("inf")),
            0 if item.get("family_code") == "MCB" else 1,
        ),
        default={},
    )


def _quick_pole_configuration(
    form: dict[str, str],
    breaker: dict[str, object],
    rules: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    if not breaker:
        return None
    phase = Phase.SINGLE if form.get("phase") == "1" else Phase.THREE
    configuration = form.get("conductor_configuration", "")
    neutral_required = configuration in {
        "bv_1ph_2wire_pe",
        "bv_3ph_4wire_pe",
        "yjv_5c_3ph_n_pe",
    }
    rcd_required = form.get("rcd_scenario") not in {"", "unknown"}
    if phase == Phase.SINGLE:
        selected_poles = "2P" if rcd_required else "1P+N"
        neutral_mode = "switched_unprotected"
    elif neutral_required and rcd_required:
        selected_poles = "4P"
        neutral_mode = "switched_unprotected"
    else:
        selected_poles = "3P"
        neutral_mode = "not_switched" if neutral_required else "absent"

    earthing_system = form.get("earthing_system")
    if earthing_system == "TN-S":
        pen_present: bool | None = False
    elif earthing_system == "TN-C-S" and rcd_required:
        pen_present = False
    else:
        pen_present = None
    outcome = evaluate_pole_and_neutral_configuration(
        PoleAndNeutralInput(
            neutral_required=neutral_required,
            neutral_pole_mode=neutral_mode,
            pen_conductor_present=pen_present,
            pen_switched_or_isolated=False if pen_present is False else None,
        ),
        phase=phase,
        selected_poles=selected_poles,
        available_pole_options=tuple(breaker.get("available_pole_options", [])),
        rules=rules,
    ).to_dict()
    outcome.setdefault("outputs", {}).update({
        "selected_poles": selected_poles,
        "neutral_required": neutral_required,
        "neutral_pole_mode": neutral_mode,
        "selection_basis": (
            "RCD用途已明确，按全部带电导体同时断开配置"
            if rcd_required
            else "按相制及已声明的中性线结构配置"
        ),
    })
    return outcome


def _product_operating_current(product: dict[str, object]) -> float | None:
    trip = product.get("trip_configuration") or {}
    fixed = trip.get("instantaneous_pickup_a")
    if fixed is not None:
        return float(fixed)
    adjustable = trip.get("instantaneous_pickup_range_a") or []
    if adjustable:
        return max(float(value) for value in adjustable)
    return None


@app.get("/complete-circuit", response_class=HTMLResponse)
def complete_circuit_page(request: Request):
    form = _complete_circuit_form_defaults()
    return templates.TemplateResponse(
        request=request,
        name="circuit_audit.html",
        context={
            "form": form,
            "segment_labels": _ENGINEERING_SEGMENT_LABELS,
            "errors": [],
            "notices": [],
            "derived": None,
            "audit_result": None,
            "alternative_result": None,
            "transformer_capacities": _engineering_transformer_capacities(),
            "projects": db.list_projects(),
        },
    )


@app.post("/complete-circuit", response_class=HTMLResponse)
async def complete_circuit_preview(request: Request):
    submitted = await request.form()
    context = _calculate_complete_circuit_context(dict(submitted))
    return templates.TemplateResponse(
        request=request,
        name="circuit_audit.html",
        context=context,
    )


def _calculate_complete_circuit_context(submitted: dict[str, object]) -> dict[str, object]:
    form = {
        key: str(submitted.get(key, default)).strip()
        for key, default in _complete_circuit_form_defaults().items()
    }
    errors: list[str] = []

    def number(field: str, label: str, *, optional: bool = False) -> float | None:
        raw = form[field]
        if optional and not raw:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            errors.append(f"{label}必须填写数值。")
            return None

    transformer_capacity = number("transformer_capacity_kva", "变压器容量")
    transformer_uk = number("transformer_uk_percent", "变压器uk%")
    upstream_capacity = number(
        "upstream_short_circuit_capacity_mva", "上级系统短路容量"
    )
    load_value = number("load_value", "负荷已知量")
    power_factor = number("power_factor", "功率因数")
    voltage_drop_limit = number("voltage_drop_limit_percent", "允许电压降")
    upstream_design_current = number(
        "upstream_design_current_a", "末端配电箱进线计算电流", optional=True
    )
    segments: list[FeederSegmentInput] = []
    for segment_id, label in _ENGINEERING_SEGMENT_LABELS.items():
        segment_type = (
            SegmentType.BUSWAY if segment_id == "connection" and form["connection_line_type"] == "busway"
            else SegmentType.INTERNAL_CONNECTION if segment_id == "connection" and form["connection_line_type"] == "internal_connection"
            else SegmentType.CABLE
        )
        length = number(f"length_{segment_id}", f"{label}长度")
        temperature = number(f"temperature_{segment_id}", f"{label}环境温度")
        existing_section = number(
            f"existing_section_{segment_id}",
            f"{label}原电缆截面",
            optional=True,
        )
        existing_pe_section = number(
            f"existing_pe_section_{segment_id}",
            f"{label}原PE截面",
            optional=True,
        )
        breaker = None
        if (
            form["task_mode"] == CircuitTaskMode.AUDIT.value
            and segment_type != SegmentType.INTERNAL_CONNECTION
        ):
            breaker_rated_current = number(
                f"breaker_in_{segment_id}", f"{label}断路器In", optional=True
            )
            breaker_action_current = number(
                f"breaker_action_{segment_id}",
                f"{label}断路器保证动作电流",
                optional=True,
            )
            if (
                breaker_action_current is None
                and breaker_rated_current is not None
                and form[f"mcb_trip_curve_{segment_id}"] in {"B", "C"}
            ):
                breaker_action_current = breaker_rated_current * (
                    5 if form[f"mcb_trip_curve_{segment_id}"] == "B" else 10
                )
            breaker = ExistingBreakerInput(
                designation=form[f"breaker_designation_{segment_id}"],
                rated_current_a=breaker_rated_current,
                frame_current_a=number(
                    f"breaker_frame_{segment_id}",
                    f"{label}断路器壳架电流",
                    optional=True,
                ),
                rated_voltage_v=number(
                    f"breaker_voltage_{segment_id}",
                    f"{label}断路器额定电压",
                    optional=True,
                ),
                breaking_capacity_ka=number(
                    f"breaker_icu_{segment_id}",
                    f"{label}断路器分断能力",
                    optional=True,
                ),
                guaranteed_action_current_a=breaker_action_current,
            )
        if length is not None and temperature is not None:
            segments.append(
                FeederSegmentInput(
                    id=segment_id,
                    label=label,
                    length_m=length,
                    conductor_family=("BV" if segment_id == "final" and form["terminal_phase"] == "1" else "YJV"),
                    configuration_code=form[f"configuration_{segment_id}"],
                    installation_scenario=form[f"scenario_{segment_id}"],
                    temperature_c=temperature,
                    existing_phase_section_mm2=(
                        None if segment_type in {SegmentType.BUSWAY, SegmentType.INTERNAL_CONNECTION} else existing_section
                    ),
                    existing_pe_section_mm2=existing_pe_section,
                    mcb_trip_curve=(form[f"mcb_trip_curve_{segment_id}"] or None),
                    existing_breaker=breaker,
                    segment_type=segment_type,
                    busway_series_code=(
                        form["busway_series_code"] if segment_type == SegmentType.BUSWAY else None
                    ),
                    busway_rating_a=(
                        number("busway_rating_a", "母线槽额定电流", optional=True)
                        if segment_type == SegmentType.BUSWAY else None
                    ),
                    phase=(Phase.SINGLE if segment_id == "final" and form["terminal_phase"] == "1" else Phase.THREE),
                )
            )

    build_result = None
    required_numbers = (
        transformer_capacity,
        transformer_uk,
        upstream_capacity,
        load_value,
        power_factor,
        voltage_drop_limit,
    )
    if not errors and all(value is not None for value in required_numbers):
        try:
            network_input = CircuitNetworkInput(
                task_mode=CircuitTaskMode(form["task_mode"]),
                circuit_code=form["circuit_code"],
                circuit_name=form["circuit_name"],
                transformer_family=form["transformer_family"],
                transformer_actual_model=form["transformer_actual_model"],
                transformer_capacity_kva=float(transformer_capacity),
                transformer_uk_percent=float(transformer_uk),
                upstream_short_circuit_capacity_mva=float(upstream_capacity),
                load_kind=TerminalLoadKind(form["load_kind"]),
                load_basis=InputBasis(form["load_basis"]),
                load_value=float(load_value),
                power_factor=float(power_factor),
                terminal_phase=Phase(form["terminal_phase"]),
                upstream_design_current_a=upstream_design_current,
                voltage_drop_limit_percent=float(voltage_drop_limit),
                segments=tuple(segments),
                installed_assemblies=(
                    tuple(
                        InstalledAssembly(
                            node_id,
                            form[f"assembly_designation_{node_id}"],
                            number(
                                f"assembly_current_{node_id}",
                                f"{label}额定电流",
                                optional=True,
                            ),
                            number(
                                f"assembly_voltage_{node_id}",
                                f"{label}额定电压",
                                optional=True,
                            ),
                            number(
                                f"assembly_icw_{node_id}",
                                f"{label}短时耐受电流",
                                optional=True,
                            ),
                            form[f"assembly_reference_{node_id}"] or None,
                        )
                        for node_id, label in {
                            "main": "低压馈线柜",
                            "db": "下级配电箱",
                        }.items()
                    )
                    if form["task_mode"] == CircuitTaskMode.AUDIT.value
                    else ()
                ),
                installed_incoming_breakers=(
                    (
                        InstalledIncomingBreaker(
                            "db",
                            form["incoming_breaker_designation_db"],
                            upstream_design_current,
                            number("incoming_breaker_in_db", "照明箱进线断路器In", optional=True),
                            number("incoming_breaker_frame_db", "照明箱进线断路器壳架", optional=True),
                            number("incoming_breaker_voltage_db", "照明箱进线断路器Ue", optional=True),
                            number("incoming_breaker_icu_db", "照明箱进线断路器Icu", optional=True),
                            form["incoming_breaker_reference_db"] or None,
                        ),
                    )
                    if form["task_mode"] == CircuitTaskMode.AUDIT.value
                    and form["incoming_breaker_designation_db"]
                    else ()
                ),
            )
        except ValueError:
            errors.append("任务类型、负荷类型或已知量类型无效。")
        else:
            rules = {item["code"]: item for item in db.list_rules()}
            build_result = build_circuit_network_requests(network_input, rules)
            errors.extend(build_result.errors)
    context = {
        "form": form,
        "segment_labels": _ENGINEERING_SEGMENT_LABELS,
        "errors": errors,
        "notices": list(build_result.notices) if build_result else [],
        "derived": build_result.derived if build_result else None,
        "audit_result": None,
        "alternative_result": None,
        "transformer_capacities": _engineering_transformer_capacities(),
        "projects": db.list_projects(),
        "network_input": None,
    }
    if not errors and build_result and build_result.radial_request:
        rules = {item["code"]: item for item in db.list_rules()}
        if build_result.audit_request is not None:
            context["audit_result"] = audit_drawing_complete_circuit(
                build_result.audit_request,
                rules,
            ).to_dict()
        context["alternative_result"] = calculate_radial_complete_circuit(
            build_result.radial_request,
            rules,
        ).to_dict()
        context["network_input"] = network_input
    return context


_ENGINEERING_SEGMENT_LABELS = {
    "connection": "变压器低压出口 → 低压馈线柜",
    "feeder": "低压馈线柜 → 下级配电箱",
    "final": "下级配电箱 → 用电设备末端",
}


def _complete_circuit_form_defaults() -> dict[str, str]:
    form = {
        "task_mode": "design",
        "circuit_code": "C-001",
        "circuit_name": "完整低压放射式回路",
        "transformer_code": "T1",
        "bus_section_code": "Ⅰ段",
        "feeder_cabinet_code": "AA1",
        "transformer_family": "scb11",
        "transformer_actual_model": "SCB11",
        "transformer_capacity_kva": "1000",
        "transformer_uk_percent": "6",
        "upstream_short_circuit_capacity_mva": "100",
        "load_kind": "ordinary",
        "terminal_phase": "3",
        "upstream_design_current_a": "",
        "load_basis": "kw",
        "load_value": "30",
        "power_factor": "0.9",
        "voltage_drop_limit_percent": "5",
        "connection_line_type": "cable",
        "busway_series_code": "canalis_kta_3lnpe",
        "busway_rating_a": "1600",
        "incoming_breaker_designation_db": "",
        "incoming_breaker_in_db": "",
        "incoming_breaker_frame_db": "",
        "incoming_breaker_voltage_db": "",
        "incoming_breaker_icu_db": "",
        "incoming_breaker_reference_db": "",
    }
    installed_sections = {"connection": "70", "feeder": "35", "final": "25"}
    breaker_defaults = {
        "connection": ("QF0 250A", "250", "400", "400", "35"),
        "feeder": ("QF1 160A", "160", "250", "400", "35"),
        "final": ("QF2 63A", "63", "100", "400", "25"),
    }
    assembly_defaults = {
        "main": ("低压馈线柜", "400", "400", "35", "图纸标注/成套设备铭牌"),
        "db": ("下级配电箱", "160", "400", "25", "图纸标注/成套设备铭牌"),
    }
    for node_id, values in assembly_defaults.items():
        designation, current, voltage, icw, reference = values
        form.update({
            f"assembly_designation_{node_id}": designation,
            f"assembly_current_{node_id}": current,
            f"assembly_voltage_{node_id}": voltage,
            f"assembly_icw_{node_id}": icw,
            f"assembly_reference_{node_id}": reference,
        })
    lengths = {"connection": "10", "feeder": "50", "final": "30"}
    for segment_id in _ENGINEERING_SEGMENT_LABELS:
        designation, rated, frame, voltage, icu = breaker_defaults[segment_id]
        form.update(
            {
                f"length_{segment_id}": lengths[segment_id],
                f"configuration_{segment_id}": "yjv_4c_3ph_n_pe",
                f"scenario_{segment_id}": "tray",
                f"temperature_{segment_id}": "40",
                f"existing_section_{segment_id}": installed_sections[segment_id],
                f"breaker_designation_{segment_id}": designation,
                f"breaker_in_{segment_id}": rated,
                f"breaker_frame_{segment_id}": frame,
                f"breaker_voltage_{segment_id}": voltage,
                f"breaker_icu_{segment_id}": icu,
                f"breaker_action_{segment_id}": "",
                f"existing_pe_section_{segment_id}": "",
                f"mcb_trip_curve_{segment_id}": "",
            }
        )
    return form


def _drawing_circuit_form_defaults() -> dict[str, str]:
    """图纸核验默认到照明箱后的单相照明分支。"""

    form = _complete_circuit_form_defaults()
    form.update({
        "task_mode": "audit",
        "terminal_phase": "1",
        "load_value": "0.48",
        "power_factor": "0.8",
        "configuration_final": "bv_1ph_2wire_pe",
        "configuration_feeder": "yjv_4c_3ph_n_separate_pe",
        "scenario_final": "conduit",
        "temperature_final": "30",
        "existing_section_final": "2.5",
        "existing_pe_section_feeder": "16",
        "breaker_designation_final": "分支MCB C10",
        "breaker_in_final": "10",
        "breaker_frame_final": "63",
        "breaker_voltage_final": "230",
        "breaker_icu_final": "10",
        "existing_pe_section_final": "2.5",
        "mcb_trip_curve_final": "C",
        "incoming_breaker_designation_db": "末端照明配电箱进线断路器",
        "incoming_breaker_in_db": "63",
        "incoming_breaker_frame_db": "100",
        "incoming_breaker_voltage_db": "400",
        "incoming_breaker_icu_db": "50",
        "incoming_breaker_reference_db": "图纸标注/产品样本",
    })
    return form


def _engineering_transformer_capacities() -> list[float]:
    return sorted(
        {
            float(capacity)
            for series in TRANSFORMER_PHASE_PE_IMPEDANCE["series"].values()
            for capacity in series["rows"]
        }
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
    if form["circuit_role"] == "feeder":
        form["circuit_application"] = "distribution"
    elif not form["circuit_application"]:
        form["circuit_application"] = (
            "fixed_equipment_final"
            if form["circuit_role"] == "single_device"
            else "distribution"
        )
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
    preferred_breaker = _preferred_quick_breaker_candidate(result)
    pole_configuration = _quick_pole_configuration(form, preferred_breaker, rules)
    if pole_configuration:
        result["pole_configuration"] = pole_configuration
        selected_poles = pole_configuration.get("outputs", {}).get(
            "selected_poles"
        )
        if selected_poles:
            preferred_breaker["poles"] = selected_poles
            preferred_breaker["adopted_poles"] = selected_poles

    line_outputs = result.get("line_end_short_circuit", {}).get("outputs", {})
    required_icu = line_outputs.get("required_breaking_capacity_ka")
    auto_product_reference = (
        form["phase"] == "3"
        and preferred_breaker.get("rated_current_a") is not None
        and required_icu is not None
        and not form["existing_breaker_series"]
    )
    if auto_product_reference:
        form["existing_breaker_series"] = "SCHNEIDER.EASYPACT.CVS.2024"
        form["existing_breaker_trip_unit_family"] = "TM-D"
    if form["existing_breaker_series"] == "SCHNEIDER.EASYPACT.CVS.2024":
        rated_current = (
            form["existing_breaker_rated_current_a"]
            or preferred_breaker.get("rated_current_a")
            or ""
        )
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
                else (
                    "系统从通用参数候选自动匹配；作为样本参数参考，非品牌推荐"
                    if auto_product_reference
                    else "复用本次通用断路器初选额定电流；现场铭牌仍须确认"
                )
            )
            product_reference["adopted_poles"] = (
                pole_configuration.get("outputs", {}).get("selected_poles")
                if pole_configuration else None
            )
            result["existing_breaker_product_reference"] = product_reference
            if product_reference.get("frame_code"):
                result["warnings"] = [
                    warning for warning in result.get("warnings", [])
                    if not warning.startswith(
                        "线路末端三相短路电流及Icu最低要求已算出"
                    )
                ]
                result["warnings"].append(
                    "线路起点短路电流、断路器Icu及脱扣参数已按已核验样本"
                    "自动联动；样本仅作等效参数参考，不构成品牌推荐。"
                )

    result["selectivity_scope"] = {
        "status": "不适用",
        "reason": (
            "快速计算仅包含一个保护点，没有上下级保护器件组合；"
            "本计算边界内不存在选择性校核对象。完整回路存在上下级器件时，"
            "在完整回路模块按制造商选择性表校核。"
        ),
    }
    incomplete = result.get("outputs", {}).get("incomplete_checks", [])
    incomplete = [item for item in incomplete if item != "选择性"]
    cable_candidates = result.get("outputs", {}).get("cable_candidates", [])
    selected_structure = (
        cable_candidates[0].get("fault_loop_structure")
        if cable_candidates else None
    )
    if selected_structure and (
        form["fault_fourth_conductor_role"] == "PE"
        or form["conductor_configuration"] == "yjv_5c_3ph_n_pe"
    ):
        incomplete = [
            item for item in incomplete if item != "芯数及 N/PE 配置"
        ]
    if (
        pole_configuration
        and pole_configuration.get("provisional_status") == "通过"
    ):
        incomplete = [item for item in incomplete if item != "断路器极数"]
    product_reference = result.get("existing_breaker_product_reference", {})
    if product_reference.get("frame_code"):
        incomplete = [
            item for item in incomplete
            if item not in {"断路器脱扣特性", "已选断路器Icu实物复核"}
        ]
    result["outputs"]["incomplete_checks"] = incomplete

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
        if not circuit_rating and preferred_breaker:
            circuit_rating = str(preferred_breaker.get("rated_current_a") or "")
            circuit_rating_source = "本次断路器自动初选额定电流"
        form["circuit_rated_current_a"] = circuit_rating
        product_reference = result.get("existing_breaker_product_reference", {})
        product_operating_current = _product_operating_current(product_reference)
        if (
            not form["protective_device_operating_current_a"]
            and product_operating_current is not None
        ):
            form["protective_device_operating_current_a"] = (
                f"{product_operating_current:g}"
            )
            form["protective_device_operating_reference"] = (
                f"{product_reference.get('manufacturer')} "
                f"{product_reference.get('series')} "
                f"{product_reference.get('trip_reference')}；"
                "采用瞬时脱扣固定值或可调整范围上限作为保守Ia"
            )
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
            and structure.get("geometry_available", True)
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
        pe_clearing_time = form["fault_clearing_time_s"]
        pe_clearing_time_source = "用户提供的保护器件切除时间"
        if (
            not pe_clearing_time
            and earth_fault_result.get("provisional_status") == "通过"
            and earth_outputs.get("maximum_disconnection_time_s") is not None
        ):
            pe_clearing_time = str(
                earth_outputs["maximum_disconnection_time_s"]
            )
            pe_clearing_time_source = (
                "采用本回路自动切断校核允许的最长时间作热稳定保守上界"
            )
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
                    "fault_clearing_time_s": pe_clearing_time,
                    "let_through_energy_a2s": form["let_through_energy_a2s"],
                    "let_through_energy_reference": form[
                        "let_through_energy_reference"
                    ],
                },
                rules,
            ).to_dict()
            result["pe_thermal"] = pe_thermal_result
            if pe_clearing_time:
                pe_thermal_result.setdefault("outputs", {})[
                    "adopted_clearing_time_source"
                ] = pe_clearing_time_source
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
    product_reference = result.get("existing_breaker_product_reference", {})
    if product_reference.get("frame_code"):
        permitted_i2t = (
            result.get("phase_thermal", {})
            .get("outputs", {})
            .get("maximum_permitted_let_through_energy_a2s")
        )
        line_start_ik = line_outputs.get("line_start_short_circuit_current_ka")
        if permitted_i2t is not None and line_start_ik is not None:
            product_thermal = evaluate_easypact_cvs_phase_thermal_reference(
                product_reference,
                line_start_ik,
                permitted_i2t,
            )
            result["existing_breaker_phase_thermal"] = product_thermal
            if product_thermal.get("provisional_status") in {"通过", "不通过"}:
                incomplete = result.get("outputs", {}).get(
                    "incomplete_checks", []
                )
                result["outputs"]["incomplete_checks"] = [
                    item
                    for item in incomplete
                    if item not in {
                        "相导体热稳定",
                        "相导体切除时间/I²t实物复核",
                    }
                ]
                for stage in result.get("outputs", {}).get(
                    "workflow_stages", []
                ):
                    if stage.get("code") == "phase_thermal":
                        stage["label"] = "相导体热稳定（产品I²t）"
                        stage["state"] = "completed"
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
    network = db.get_project_network(project_id)
    if network:
        network["changed_fields_display"] = [
            NETWORK_INPUT_LABELS.get(field, field)
            for field in network["changed_fields_json"]
        ]
    drawing_circuits = db.list_project_drawing_circuits(project_id)
    drawing_settings = db.get_project_drawing_settings(project_id)
    drawing_group_settings = db.list_drawing_group_settings(project_id)
    drawing_summary = summarize_drawing_circuits(
        drawing_circuits, drawing_settings.get("simultaneity_factor"), drawing_group_settings
    )
    return templates.TemplateResponse(
        request=request,
        name="project.html",
        context={
            "project": project,
            "circuits": db.list_circuits(project_id),
            "runs": db.list_runs(project_id),
            "network": network,
            "network_runs": db.list_network_runs(project_id),
            "motor_runs": db.list_motor_runs(project_id),
            "drawing_circuits": drawing_circuits,
            "drawing_settings": drawing_settings,
            "drawing_group_settings": drawing_group_settings,
            "drawing_summary": drawing_summary,
            "message": message,
        },
    )


@app.post("/projects/{project_id}/drawing-settings")
def save_project_drawing_settings(
    project_id: int,
    simultaneity_factor: str = Form(""),
    source_note: str = Form(""),
):
    if not db.get_project(project_id): raise HTTPException(404, "项目不存在")
    try:
        factor = float(simultaneity_factor) if simultaneity_factor.strip() else None
        db.save_project_drawing_settings(project_id, factor, source_note)
    except ValueError as exc:
        return RedirectResponse(f"/projects/{project_id}?message={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/drawing-group-settings")
def save_project_drawing_group_setting(
    project_id: int, level: str = Form(...), transformer_code: str = Form(...),
    bus_section_code: str = Form(""), feeder_cabinet_code: str = Form(""),
    factor: str = Form(""), rated_current_a: str = Form(""), source_note: str = Form(""),
    short_time_withstand_ka: str = Form(""), breaker_designation: str = Form(""),
    breaker_breaking_capacity_ka: str = Form(""),
    selectivity_upstream_designation: str = Form(""),
    selectivity_downstream_designation: str = Form(""),
    selectivity_limit_ka: str = Form(""), selectivity_reference: str = Form(""),
):
    if not db.get_project(project_id): raise HTTPException(404, "项目不存在")
    try:
        db.save_drawing_group_setting(
            project_id, level, transformer_code, bus_section_code, feeder_cabinet_code,
            float(factor) if factor.strip() else None,
            float(rated_current_a) if rated_current_a.strip() else None, source_note,
            float(short_time_withstand_ka) if short_time_withstand_ka.strip() else None,
            breaker_designation,
            float(breaker_breaking_capacity_ka) if breaker_breaking_capacity_ka.strip() else None,
            selectivity_upstream_designation, selectivity_downstream_designation,
            float(selectivity_limit_ka) if selectivity_limit_ka.strip() else None,
            selectivity_reference,
        )
    except ValueError as exc:
        return RedirectResponse(f"/projects/{project_id}?message={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}/drawing-audit.xlsx")
def export_project_drawing_audit(project_id: int):
    project = db.get_project(project_id)
    if not project: raise HTTPException(404, "项目不存在")
    circuits = db.list_project_drawing_circuits(project_id)
    settings = db.get_project_drawing_settings(project_id)
    summary = summarize_drawing_circuits(circuits, settings.get("simultaneity_factor"), db.list_drawing_group_settings(project_id))
    content = create_drawing_project_export(project, circuits, summary, settings)
    filename = quote(f"{project['code']}-图纸逐回路核验汇总.xlsx")
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@app.get("/projects/{project_id}/drawing-audit.pdf")
def export_project_drawing_audit_pdf(project_id: int):
    project = db.get_project(project_id)
    if not project: raise HTTPException(404, "项目不存在")
    circuits = db.list_project_drawing_circuits(project_id)
    settings = db.get_project_drawing_settings(project_id)
    summary = summarize_drawing_circuits(circuits, settings.get("simultaneity_factor"), db.list_drawing_group_settings(project_id))
    content = create_drawing_project_pdf(project, circuits, summary, settings)
    filename = quote(f"{project['code']}-图纸逐回路核验汇总.pdf")
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@app.get("/projects/{project_id}/drawing-circuits/new", response_class=HTMLResponse)
def new_project_drawing_circuit(request: Request, project_id: int):
    project = db.get_project(project_id)
    if not project: raise HTTPException(404, "项目不存在")
    context = _calculate_complete_circuit_context(
        _drawing_circuit_form_defaults()
    )
    context.update({"project": project, "selected_project_id": project_id, "drawing_circuit_mode": True})
    return templates.TemplateResponse(request=request, name="circuit_audit.html", context=context)


@app.get("/projects/{project_id}/drawing-circuits/{circuit_code}", response_class=HTMLResponse)
def edit_project_drawing_circuit(request: Request, project_id: int, circuit_code: str):
    project = db.get_project(project_id)
    circuit = db.get_drawing_circuit(project_id, circuit_code)
    if not project or not circuit: raise HTTPException(404, "图纸回路不存在")
    context = _calculate_complete_circuit_context(circuit["input_json"])
    context.update({"project": project, "selected_project_id": project_id, "drawing_circuit_mode": True})
    return templates.TemplateResponse(request=request, name="circuit_audit.html", context=context)


@app.post("/projects/{project_id}/drawing-circuits")
async def save_project_drawing_circuit(request: Request, project_id: int):
    project = db.get_project(project_id)
    if not project: raise HTTPException(404, "项目不存在")
    submitted = dict(await request.form()); submitted["task_mode"] = "audit"
    context = _calculate_complete_circuit_context(submitted)
    if context["errors"] or not context["audit_result"]:
        context.update({"request": request, "project": project, "selected_project_id": project_id, "drawing_circuit_mode": True})
        return templates.TemplateResponse(request=request, name="circuit_audit.html", context=context, status_code=422)
    circuit = db.save_project_drawing_circuit(project_id, context["form"])
    rule_codes = _collect_rule_codes(context["alternative_result"])
    rule_codes.update(_collect_rule_codes(context["audit_result"]))
    rules = db.rules_by_code(); snapshot = {code: rules[code] for code in sorted(rule_codes) if code in rules}
    run_id = db.create_drawing_circuit_run(
        project_id, circuit, engine_version=__version__, input_snapshot=context["form"],
        derived=context["derived"] or {}, audit_result=context["audit_result"],
        result=context["alternative_result"], rule_snapshot=snapshot,
    )
    return RedirectResponse(f"/drawing-circuit-runs/{run_id}", status_code=303)


@app.get("/drawing-circuit-runs/{run_id}", response_class=HTMLResponse)
def drawing_circuit_run_page(request: Request, run_id: int):
    run = db.get_drawing_circuit_run(run_id)
    if not run: raise HTTPException(404, "图纸回路核验记录不存在")
    return templates.TemplateResponse(request=request, name="network_run.html", context={"run": run})


@app.get("/drawing-circuit-runs/{run_id}/report.pdf")
def export_drawing_circuit_run_pdf(run_id: int):
    run=db.get_drawing_circuit_run(run_id)
    if not run: raise HTTPException(404, "图纸回路核验记录不存在")
    return Response(create_network_run_pdf(run), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(run['project_code']+'-'+run['circuit_code']+'-核验.pdf')}"})


@app.get("/drawing-circuit-runs/{run_id}/export.xlsx")
def export_drawing_circuit_run_excel(run_id: int):
    run=db.get_drawing_circuit_run(run_id)
    if not run: raise HTTPException(404, "图纸回路核验记录不存在")
    return Response(create_network_run_export(run), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(run['project_code']+'-'+run['circuit_code']+'-核验.xlsx')}"})


@app.get("/projects/{project_id}/complete-circuit", response_class=HTMLResponse)
def project_complete_circuit_page(request: Request, project_id: int):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    network = db.get_project_network(project_id)
    context = _calculate_complete_circuit_context(
        network["input_json"] if network else _complete_circuit_form_defaults()
    )
    context.update({"project": project, "selected_project_id": project_id})
    return templates.TemplateResponse(
        request=request, name="circuit_audit.html", context=context
    )


@app.post("/projects/{project_id}/complete-circuit")
async def save_project_complete_circuit(request: Request, project_id: int):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    submitted = dict(await request.form())
    context = _calculate_complete_circuit_context(submitted)
    if context["errors"] or not context["alternative_result"]:
        context.update({"request": request, "project": project, "selected_project_id": project_id})
        return templates.TemplateResponse(
            request=request, name="circuit_audit.html", context=context, status_code=422
        )
    network = db.save_project_network(project_id, context["form"])
    rule_codes = _collect_rule_codes(context["alternative_result"])
    if context["audit_result"]:
        rule_codes.update(_collect_rule_codes(context["audit_result"]))
    rules = db.rules_by_code()
    snapshot = {code: rules[code] for code in sorted(rule_codes) if code in rules}
    run_id = db.create_network_run(
        project_id,
        network,
        engine_version=__version__,
        task_mode=context["form"]["task_mode"],
        input_snapshot=context["form"],
        derived=context["derived"] or {},
        audit_result=context["audit_result"],
        result=context["alternative_result"],
        rule_snapshot=snapshot,
    )
    return RedirectResponse(f"/network-runs/{run_id}", status_code=303)


@app.get("/network-runs/{run_id}", response_class=HTMLResponse)
def network_run_page(request: Request, run_id: int):
    run = db.get_network_run(run_id)
    if not run:
        raise HTTPException(404, "完整回路计算记录不存在")
    return templates.TemplateResponse(
        request=request, name="network_run.html", context={"run": run}
    )


@app.get("/network-runs/{run_id}/report.pdf")
def export_network_run_pdf(run_id: int):
    run = db.get_network_run(run_id)
    if not run:
        raise HTTPException(404, "完整回路计算记录不存在")
    content = create_network_run_pdf(run)
    filename = (
        f"{run['project_code']}-{run['input_snapshot'].get('circuit_code', '完整回路')}"
        f"-V{run['network_revision']}-计算书.pdf"
    )
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/network-runs/{run_id}/export.xlsx")
def export_network_run_excel(run_id: int):
    run = db.get_network_run(run_id)
    if not run:
        raise HTTPException(404, "完整回路计算记录不存在")
    content = create_network_run_export(run)
    filename = (
        f"{run['project_code']}-{run['input_snapshot'].get('circuit_code', '完整回路')}"
        f"-V{run['network_revision']}-成果表.xlsx"
    )
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/motor-runs/{run_id}", response_class=HTMLResponse)
def motor_run_page(request: Request, run_id: int):
    run = db.get_motor_run(run_id)
    if not run:
        raise HTTPException(404, "电动机计算记录不存在")
    return templates.TemplateResponse(request=request, name="motor_run.html", context={"run": run})


@app.get("/motor-runs/{run_id}/report.pdf")
def export_motor_run_pdf(run_id: int):
    run = db.get_motor_run(run_id)
    if not run:
        raise HTTPException(404, "电动机计算记录不存在")
    content = create_motor_run_pdf(run)
    filename = f"{run['project_code']}-{run['input_snapshot'].get('circuit_code','电动机')}-V{run['motor_revision']}-计算书.pdf"
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.get("/motor-runs/{run_id}/export.xlsx")
def export_motor_run_excel(run_id: int):
    run = db.get_motor_run(run_id)
    if not run:
        raise HTTPException(404, "电动机计算记录不存在")
    content = create_motor_run_export(run)
    filename = f"{run['project_code']}-{run['input_snapshot'].get('circuit_code','电动机')}-V{run['motor_revision']}-成果表.xlsx"
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


def _collect_rule_codes(payload: object) -> set[str]:
    codes: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "rule_codes" and isinstance(value, (list, tuple)):
                codes.update(str(item) for item in value)
            else:
                codes.update(_collect_rule_codes(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            codes.update(_collect_rule_codes(item))
    return codes


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
