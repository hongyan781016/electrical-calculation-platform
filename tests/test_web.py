from fastapi.testclient import TestClient
import pytest

from src.electrical_calc.database import Database
from src.electrical_calc import web


def capture_template_context(monkeypatch):
    captured = {}
    original = web.templates.TemplateResponse

    def capture(*args, **kwargs):
        context = kwargs.get("context")
        if context is None and len(args) >= 3:
            context = args[2]
        captured.update(context or {})
        return original(*args, **kwargs)

    monkeypatch.setattr(web.templates, "TemplateResponse", capture)
    return captured


def test_health_and_project_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "web.db"))
    client = TestClient(web.app)
    assert client.get("/health").json() == {"status": "ok", "version": "0.1.1"}

    response = client.post(
        "/projects",
        data={"code": "WEB-01", "name": "网页测试项目", "description": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    project_url = response.headers["location"]
    page = client.get(project_url)
    assert page.status_code == 200
    assert "网页测试项目" in page.text

    response = client.post(
        project_url + "/circuits",
        data={
            "code": "AL-01",
            "name": "照明",
            "phase": "1",
            "voltage_v": "220",
            "installed_power_kw": "5",
            "demand_factor": "0.8",
            "power_factor": "0.9",
            "efficiency": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    circuit = web.db.list_circuits(1)[0]
    response = client.post(f"/circuits/{circuit['id']}/calculate", follow_redirects=False)
    assert response.status_code == 303
    run_page = client.get(response.headers["location"])
    assert run_page.status_code == 200
    assert "负荷与选型" in run_page.text


def test_rule_cannot_be_approved_without_source(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "web.db"))
    client = TestClient(web.app)
    rule = web.db.list_rules()[0]
    original_status = rule["status"]
    response = client.post(
        f"/rules/{rule['id']}",
        data={"name": rule["name"], "status": "approved"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert web.db.list_rules()[0]["status"] == original_status


def test_quick_selection_does_not_require_project_or_excel(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick.db"))
    client = TestClient(web.app)

    before_rules = len(web.db.list_rules())
    page = client.get("/quick")
    assert page.status_code == 200
    assert "0.4kV 回路计算" in page.text
    assert 'value="380"' in page.text
    assert 'name="circuit_role"' in page.text
    assert 'name="conductor_basis"' not in page.text
    assert 'name="cable_r_ohm_per_km"' not in page.text
    assert 'name="cable_x_ohm_per_km"' not in page.text
    assert 'name="power_factor"' in page.text
    assert 'name="prospective_short_circuit_ka"' not in page.text
    assert "上级变压器资料" in page.text
    assert 'name="short_circuit_method"' in page.text
    assert 'name="transformer_capacity_kva"' in page.text
    assert 'name="transformer_uk_percent"' in page.text
    assert 'name="fault_transformer_series_code"' in page.text
    assert 'name="fault_transformer_capacity_kva"' in page.text
    assert 'name="fault_fourth_conductor_role"' in page.text
    assert 'name="fault_busway_series_code"' in page.text
    assert 'name="fault_busway_rating_a"' in page.text
    assert 'name="pe_thermal_enabled"' not in page.text
    assert 'name="phase_thermal_enabled"' not in page.text
    assert "热稳定约束由系统自动形成" in page.text
    assert 'name="fault_clearing_time_s"' in page.text
    assert 'name="voltage_drop_limit_pct"' not in page.text
    assert "允许电压降：系统自动采用" in page.text
    assert 'name="installation_temperature_c"' in page.text
    assert 'name="enclosed_grouping_circuit_count"' in page.text
    assert "空气中敷设采用表6.22；埋地管槽采用表6.24" in page.text
    assert 'name="soil_thermal_resistivity_k_m_per_w"' in page.text
    assert 'name="buried_circuit_count"' in page.text
    assert 'name="buried_duct_spacing_m"' in page.text
    assert 'name="buried_depth_m"' in page.text
    assert ">埋地管槽<" in page.text
    assert "YJV22“敷设在土壤中”的数据不用于YJV" in page.text
    assert ">槽盒<" in page.text
    assert 'value="trunking"' not in page.text
    assert "线槽基础载流量依据尚未核实" in page.text
    assert 'name="rcd_scenario"' in page.text
    assert "负荷类型（用于功率因数）" in page.text
    assert "这不是需要系数。需要系数只在“安装功率”模式单独填写。" in page.text
    assert ">2根单芯线<" in page.text
    assert ">3根单芯线<" in page.text
    assert ">三芯电缆<" in page.text
    assert "L1、L2、L3、N各一根" not in page.text

    result = client.post("/quick", data={
        "input_basis": "kva", "input_value": "30", "phase": "3", "voltage_v": "380",
        "load_type_code": "", "conductor_family": "YJV", "installation_scenario": "tray",
    })
    assert result.status_code == 200
    assert "45.5803 A" in result.text
    assert "断路器设计参数要求" in result.text
    assert "微型断路器（MCB）" in result.text
    assert "塑壳断路器（MCCB）" not in result.text
    assert "Icu待算" in result.text
    assert "N/PE" in result.text
    assert "当前结构与已核实的YJV三芯基础载流量表一致" in result.text
    assert "极数：待按接地系统与保护要求确定" in result.text
    assert "框架断路器（ACB）" not in result.text
    assert "该芯数仅用于电缆/导线查表，不决定断路器极数" in result.text
    assert web.db.list_projects() == []
    assert len(web.db.list_rules()) == before_rules


def test_complete_circuit_page_accepts_user_design_lengths_without_claiming_dwg_source(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "complete-circuit.db"))
    client = TestClient(web.app)
    page = client.get("/complete-circuit")
    assert page.status_code == 200
    assert "图纸完整回路核验" in page.text
    assert "变压器 → 低压柜 → 分配电箱 → 用电设备" in page.text
    assert 'name="length_connection"' in page.text
    assert 'name="length_feeder"' in page.text
    assert 'name="length_final"' in page.text
    assert "不要求用户填写R/X、Zs或I²t" in page.text

    response = client.post(
        "/complete-circuit",
        data={
            "length_connection": "10",
            "length_feeder": "50",
            "length_final": "30",
        },
    )
    assert response.status_code == 200
    assert "原图设计复核" in response.text
    assert "替代设计未计入本栏结论" in response.text
    assert "图纸电缆 C1：YJV 4×70+PE35" in response.text
    assert "图纸断路器 QF0 250A" in response.text
    assert "原器件结论：不通过" in response.text
    assert "独立替代设计候选" in response.text
    assert "不代表原图合规" in response.text
    assert "MCCB 通用参数候选" in response.text


def test_quick_transformer_lv_outlet_short_circuit_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-short-circuit.db"))
    client = TestClient(web.app)

    result = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "380",
        "conductor_family": "BV", "installation_scenario": "conduit",
        "short_circuit_method": "transformer_lv_table",
        "transformer_capacity_kva": "1000", "transformer_uk_percent": "6",
    })

    assert result.status_code == 200
    assert "变压器0.4kV出口短路暂算" in result.text
    assert "24.0 kA" in result.text
    assert "61.2 kA" in result.text
    assert "表15.7" in result.text
    assert "Icu 要求超出当前表列档位" in result.text
    assert "塑壳断路器（MCCB）" not in result.text


def test_quick_unknown_kw_accepts_user_power_factor(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-pf.db"))
    client = TestClient(web.app)

    result = client.post("/quick", data={
        "input_basis": "kw", "input_value": "10", "phase": "1", "voltage_v": "220",
        "load_type_code": "unknown", "power_factor": "0.8",
        "conductor_family": "BV", "installation_scenario": "conduit",
    })

    assert result.status_code == 200
    assert "56.8182 A" in result.text
    assert "用户输入（应与铭牌或厂家资料核对）" in result.text
    assert "正式使用前应与设备铭牌或厂家资料核对" in result.text


def test_quick_line_end_short_circuit_is_opt_in_and_does_not_change_old_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-line-disabled.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV", "installation_scenario": "conduit",
        "length_m": "100", "transformer_capacity_kva": "1000", "transformer_uk_percent": "6",
    })

    assert response.status_code == 200
    assert "line_end_short_circuit" not in captured["result"]


def test_quick_line_end_short_circuit_uses_verified_nameplate_rx_case(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-line-complete.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "400",
        "conductor_family": "YJV", "installation_scenario": "conduit", "length_m": "100",
        "short_circuit_line_enabled": "on",
        "short_circuit_line_type": "cable", "short_circuit_line_section_mm2": "50",
        "transformer_capacity_kva": "1000", "transformer_uk_percent": "6",
        "transformer_pk_kw": "10",
        "source_impedance_mode": "infinite_capacity",
        "voltage_factor_c": "1.05",
        "breaker_installation_point": "line_end",
        "breaker_icu_ka": "25",
    })

    line_result = captured["result"]["line_end_short_circuit"]
    assert response.status_code == 200
    assert line_result["outputs"]["terminal_short_circuit_current_ka"] > 0
    assert line_result["outputs"]["upstream_impedance"]["method"].startswith("变压器铭牌 R/X")
    assert line_result["status"] == "无法判断"


def test_quick_line_end_short_circuit_converts_source_short_circuit_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-line-source-capacity.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "400",
        "conductor_family": "YJV", "installation_scenario": "conduit", "length_m": "100",
        "short_circuit_line_enabled": "on", "short_circuit_line_type": "cable",
        "transformer_capacity_kva": "1000", "transformer_uk_percent": "6", "transformer_pk_kw": "10",
        "source_impedance_mode": "short_circuit_capacity", "source_short_circuit_capacity_mva": "100",
        "voltage_factor_c": "1.05", "breaker_installation_point": "line_end", "breaker_icu_ka": "25",
    })

    line_result = captured["result"]["line_end_short_circuit"]
    source = line_result["outputs"]["upstream_impedance"]["source_equivalent"]
    assert response.status_code == 200
    assert source["short_circuit_capacity_mva"] == 100
    assert source["impedance_ohm"] == pytest.approx(0.0016)


def test_quick_shared_transformer_fields_derive_rx_and_icu_requirement(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-shared-transformer.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV", "conductor_configuration": "yjv_5c_3ph_n_pe",
        "installation_scenario": "tray", "tray_type": "horizontal_perforated",
        "tray_layers": "1", "tray_cables_per_layer": "1",
        "installation_temperature_c": "40", "length_m": "100",
        "short_circuit_line_enabled": "on", "short_circuit_line_type": "cable",
        "transformer_series_code": "scb11", "transformer_capacity_kva": "630",
        "transformer_uk_percent": "6", "source_impedance_mode": "short_circuit_capacity",
        "source_short_circuit_capacity_mva": "100",
    })

    line_result = captured["result"]["line_end_short_circuit"]
    transformer = line_result["outputs"]["upstream_impedance"]["transformer_equivalent"]
    assert response.status_code == 200
    assert transformer["resistance_ohm"] == pytest.approx(0.0024)
    assert transformer["reactance_ohm"] == pytest.approx(0.015)
    assert line_result["outputs"]["required_breaking_capacity_ka"] > 0
    assert line_result["outputs"]["breaker_icu_check"]["note"].startswith("设计模式")
    short_stage = next(
        item for item in captured["result"]["outputs"]["workflow_stages"]
        if item["code"] == "short_circuit"
    )
    assert short_stage == {
        "code": "short_circuit", "label": "短路电流与Icu要求", "state": "completed"
    }
    assert "已选断路器Icu实物复核" in captured["result"]["outputs"]["incomplete_checks"]
    assert not any(
        warning.startswith("未提供安装点预期短路电流")
        for warning in captured["result"]["warnings"]
    )


def test_quick_line_end_short_circuit_missing_nameplate_or_source_stays_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-line-missing.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)
    base = {
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "400",
        "conductor_family": "YJV", "installation_scenario": "conduit", "length_m": "100",
        "short_circuit_line_enabled": "on",
        "short_circuit_line_type": "cable", "short_circuit_line_section_mm2": "50",
        "transformer_capacity_kva": "1000", "transformer_uk_percent": "6",
        "voltage_factor_c": "1.05",
    }

    missing_pk = client.post("/quick", data={
        **base, "source_impedance_mode": "infinite_capacity",
    })
    missing_pk_result = captured["result"]["line_end_short_circuit"]
    assert missing_pk.status_code == 200
    assert missing_pk_result["status"] == "无法判断"
    assert any("transformer_pk_kw" in item for item in missing_pk_result["warnings"])

    missing_source = client.post("/quick", data={
        **base, "transformer_pk_kw": "10",
    })
    missing_source_result = captured["result"]["line_end_short_circuit"]
    assert missing_source.status_code == 200
    assert missing_source_result["status"] == "无法判断"
    assert any("未明确上级系统无限容量" in item for item in missing_source_result["warnings"])


def test_quick_line_end_short_circuit_maps_template_busway_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-line-busway.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "400",
        "conductor_family": "YJV", "installation_scenario": "tray", "length_m": "50",
        "short_circuit_line_enabled": "true", "short_circuit_line_type": "busway",
        "upstream_r_ohm": "0.003", "upstream_x_ohm": "0.020",
        "upstream_impedance_reference": "上游短路计算书第1页", "voltage_factor_c": "1.05",
        "busway_r_ohm_per_km": "0.020", "busway_x_ohm_per_km": "0.015",
        "busway_impedance_reference": "制造商样本第12页",
    })

    line_result = captured["result"]["line_end_short_circuit"]
    assert response.status_code == 200
    assert line_result["outputs"]["line_impedance"]["source"]["mode"] == "explicit"
    assert line_result["outputs"]["terminal_short_circuit_current_ka"] > 0


def test_quick_cable_does_not_reuse_submitted_busway_rx(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-line-cable.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "400",
        "conductor_family": "YJV", "installation_scenario": "conduit", "length_m": "50",
        "short_circuit_line_enabled": "true", "short_circuit_line_type": "cable",
        "short_circuit_line_section_mm2": "50",
        "upstream_r_ohm": "0.003", "upstream_x_ohm": "0.020",
        "upstream_impedance_reference": "上游短路计算书第1页", "voltage_factor_c": "1.05",
        "busway_r_ohm_per_km": "0.020", "busway_x_ohm_per_km": "0.015",
        "busway_impedance_reference": "不应被采用",
    })

    line_result = captured["result"]["line_end_short_circuit"]
    assert response.status_code == 200
    assert line_result["outputs"]["line_impedance"]["source"]["table"] == "表3.21"
    assert line_result["outputs"]["line_impedance"]["source"].get("mode") != "explicit"


def test_quick_earth_fault_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-earth-disabled.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "32", "phase": "1", "voltage_v": "220",
        "conductor_family": "BV", "installation_scenario": "conduit",
    })

    assert response.status_code == 200
    assert "earth_fault" not in captured["result"]


def test_quick_earth_fault_maps_traceable_zs_and_ia(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-earth-complete.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "32", "phase": "1", "voltage_v": "220",
        "conductor_family": "BV", "installation_scenario": "conduit",
        "earth_fault_enabled": "true", "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": "230", "circuit_application": "socket_final",
        "circuit_rated_current_a": "32", "fault_loop_impedance_ohm": "0.8",
        "fault_loop_impedance_reference": "接地故障回路计算书第2页",
        "protection_type": "overcurrent", "protective_device_operating_current_a": "200",
        "protective_device_operating_reference": "断路器时间—电流曲线第3页，0.4s",
    })

    earth = captured["result"]["earth_fault"]
    assert response.status_code == 200
    assert earth["outputs"]["maximum_disconnection_time_s"] == 0.4
    assert earth["outputs"]["prospective_earth_fault_current_a"] == 275
    assert earth["provisional_status"] == "通过"
    assert earth["status"] == "无法判断"
    assert "接地故障与自动切断暂算" in response.text


def test_quick_four_core_renders_multicore_ampacity_and_structure_source(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-yjv-four-core.db"))
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_4c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
    })

    assert response.status_code == 200
    assert "表31" in response.text
    assert "推荐最小截面：YJV 10.0 mm²" in response.text
    assert "表列中性线（接地线） 6 mm²" in response.text


def test_quick_short_circuit_uses_selected_cable_section_and_derives_u0(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-derived-short-circuit.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV", "installation_scenario": "conduit", "length_m": "50",
        "short_circuit_line_enabled": "true", "short_circuit_line_type": "cable",
        "transformer_capacity_kva": "1000", "transformer_uk_percent": "6",
        "transformer_pk_kw": "10", "source_impedance_mode": "infinite_capacity",
        "voltage_factor_c": "1.05", "breaker_installation_point": "line_end",
        "breaker_icu_ka": "25",
        "earth_fault_enabled": "true", "earthing_system": "TN-S",
        "circuit_application": "distribution", "protection_type": "overcurrent",
        "protective_device_characteristic": "mcb_c",
    })

    result = captured["result"]
    selection = result["line_short_circuit_selection"]
    earth = result["earth_fault"]
    candidate = result["outputs"]["cable_candidates"][0]
    assert response.status_code == 200
    assert selection["section_mm2"] == str(candidate["section_mm2"])
    assert selection["source"] == "采用本次电缆初选后的最终截面"
    assert earth["outputs"]["nominal_line_to_earth_voltage_v"] == 220
    assert earth["outputs"]["nominal_line_to_earth_voltage_source"] == (
        "三相380V系统：U0自动采用220V。"
    )


def test_quick_earth_fault_uses_selected_yjv_conventional_method(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-yjv-earth.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_4c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "50",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": "230",
        "circuit_application": "distribution",
        "protection_type": "overcurrent",
        "protective_device_characteristic": "mcb_c",
        "fault_fourth_conductor_role": "PE",
    })

    earth = captured["result"]["earth_fault"]
    assert response.status_code == 200
    assert earth["outputs"]["fault_current_calculation_method"] == "tn_conventional"
    assert earth["outputs"]["conventional_method"]["phase_section_mm2"] == 10
    assert earth["outputs"]["conventional_method"]["protective_section_mm2"] == 6
    assert earth["outputs"]["protective_device_operating_current_a"] == 500
    assert "TN常规法" in response.text


def test_quick_earth_fault_builds_transformer_to_yjv_complete_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-yjv-complete-loop.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_4c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "50",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": "230",
        "circuit_application": "distribution",
        "protection_type": "overcurrent",
        "protective_device_characteristic": "mcb_c",
        "fault_transformer_series_code": "s11_m",
        "fault_transformer_capacity_kva": "400",
        "fault_transformer_uk_percent": "4",
        "fault_transformer_hv_voltage_kv": "10",
        "fault_transformer_vector_group": "Dyn11",
        "fault_connection_type": "direct",
        "fault_fourth_conductor_role": "PE",
    })

    earth = captured["result"]["earth_fault"]
    chain = earth["outputs"]["calculated_fault_loop_chain"]["outputs"]
    cable = chain["components"][1]
    assert response.status_code == 200
    assert earth["outputs"]["fault_current_calculation_method"] == "complete_loop_impedance"
    assert chain["target_point"] == "line_end"
    assert cable["cable_specification"] == "YJV-0.6/1kV 3×10+1×6"
    assert cable["length_m"] == 50
    assert cable["phase_pe_resistance_multiplier"] == 1.5
    assert earth["outputs"]["prospective_earth_fault_current_a"] > 0
    assert "完整回路 R / X / Zs" in response.text


def test_quick_complete_loop_looks_up_canalis_busway_phase_pe_rx(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-canalis-loop.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_4c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "50",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": "230",
        "circuit_application": "distribution",
        "protection_type": "overcurrent",
        "protective_device_characteristic": "mcb_c",
        "fault_transformer_series_code": "s11_m",
        "fault_transformer_capacity_kva": "400",
        "fault_transformer_uk_percent": "4",
        "fault_transformer_hv_voltage_kv": "10",
        "fault_transformer_vector_group": "Dyn11",
        "fault_connection_type": "busway",
        "fault_busway_series_code": "canalis_kta_casing_pe",
        "fault_busway_rating_a": "1600",
        "fault_connection_length_m": "5",
        "fault_fourth_conductor_role": "PE",
    })

    selection = captured["result"]["busway_phase_pe_selection"]
    earth = captured["result"]["earth_fault"]
    chain = earth["outputs"]["calculated_fault_loop_chain"]["outputs"]
    busway = chain["components"][1]
    assert response.status_code == 200
    assert selection["series_name"] == "Canalis KTA（外壳作PE）"
    assert busway["resistance_ohm_per_km"] == pytest.approx(0.394)
    assert busway["reactance_ohm_per_km"] == pytest.approx(0.212)
    assert busway["resistance_ohm"] == pytest.approx(0.00197)
    assert busway["reactance_ohm"] == pytest.approx(0.00106)
    assert "ELEC.BUSWAY.CANALIS.PHASE_PE.IMPEDANCE" in (
        earth["outputs"]["calculated_fault_loop_chain"]["rule_codes"]
    )
    assert "母线槽相—PE参数" in response.text
    assert "DEBU021EN" in response.text


def test_quick_five_core_builds_complete_loop_from_verified_geometry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-five-core-loop.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_5c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "50",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": "230",
        "circuit_application": "distribution",
        "protection_type": "overcurrent",
        "protective_device_characteristic": "mcb_c",
        "fault_transformer_series_code": "s11_m",
        "fault_transformer_capacity_kva": "400",
        "fault_transformer_uk_percent": "4",
        "fault_transformer_hv_voltage_kv": "10",
        "fault_transformer_vector_group": "Dyn11",
        "fault_connection_type": "direct",
        "fault_fourth_conductor_role": "PE",
    })

    earth = captured["result"]["earth_fault"]
    chain = earth["outputs"]["calculated_fault_loop_chain"]["outputs"]
    cable = chain["components"][1]
    cable_calculation = cable["cable_calculation"]["outputs"]
    assert response.status_code == 200
    assert earth["outputs"]["fault_current_calculation_method"] == (
        "complete_loop_impedance"
    )
    assert cable["name"] == "线路末端YJV五芯回路"
    assert cable_calculation["structure_catalog"]["profile"] == "yjv_3plus2"
    assert cable_calculation["fault_resistance_multiplier"] == 1.5
    assert cable_calculation["reactance_method"] == "geometry"
    assert earth["outputs"]["prospective_earth_fault_current_a"] > 0


def test_quick_five_core_auto_uses_one_core_as_pe_for_design_selection(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-unconfirmed-pe.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_5c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "50",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": "230",
        "circuit_application": "distribution",
        "protection_type": "overcurrent",
        "fault_transformer_series_code": "s11_m",
        "fault_transformer_capacity_kva": "400",
        "fault_transformer_uk_percent": "4",
        "fault_transformer_hv_voltage_kv": "10",
        "fault_transformer_vector_group": "Dyn11",
    })

    earth = captured["result"]["earth_fault"]
    stages = captured["result"]["outputs"]["workflow_stages"]
    earth_stage = next(stage for stage in stages if stage["code"] == "earth_fault")
    curve_candidates = earth["outputs"]["protective_device_curve_candidates"]
    assert response.status_code == 200
    assert captured["form"]["fault_fourth_conductor_role"] == "PE"
    assert earth["outputs"]["protective_core_role"] == "PE"
    assert "五芯电缆设计初选" in earth["outputs"]["protective_core_role_source"]
    assert "calculated_fault_loop_chain" in earth["outputs"]
    assert earth["outputs"]["fault_current_calculation_method"] == (
        "complete_loop_impedance"
    )
    assert earth["outputs"]["prospective_earth_fault_current_a"] > 0
    assert earth["outputs"]["maximum_permitted_operating_current_a"] > 0
    assert earth["provisional_status"] == "无法判断"
    assert [candidate["curve"] for candidate in curve_candidates] == ["B", "C"]
    assert all(
        candidate["operating_current_a"] > 0 for candidate in curve_candidates
    )
    assert earth_stage["label"] == "接地故障与保护约束"
    assert earth_stage["state"] == "candidate"
    assert "保护器件曲线/整定实物复核" in (
        captured["result"]["outputs"]["incomplete_checks"]
    )
    assert "参数候选暂算" in response.text


def test_quick_pe_thermal_uses_selected_yjv_pe_and_calculated_fault_current(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-pe-thermal.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_4c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "50",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": "230",
        "circuit_application": "distribution",
        "protection_type": "overcurrent",
        "protective_device_characteristic": "mcb_c",
        "fault_fourth_conductor_role": "PE",
        "pe_thermal_enabled": "true",
        "protective_core_confirmed": "true",
        "fault_clearing_time_s": "0.1",
    })

    pe = captured["result"]["pe_thermal"]
    assert response.status_code == 200
    assert pe["outputs"]["protective_conductor_section_mm2"] == 6
    assert pe["outputs"]["k_a_sqrt_s_per_mm2"] == 143
    assert pe["outputs"]["actual_thermal_stress_a2s"] > 0
    assert pe["provisional_status"] == "通过"
    assert pe["status"] == "无法判断"
    assert "PE导体短路热稳定暂算" in response.text
    assert "PE导体热稳定" not in captured["result"]["outputs"]["incomplete_checks"]


def test_quick_pe_thermal_reuses_confirmed_earth_fault_pe_role_without_duplicate_checkbox(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-pe-missing.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_4c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "50",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "nominal_line_to_earth_voltage_v": "230",
        "circuit_application": "distribution",
        "protection_type": "overcurrent",
        "protective_device_characteristic": "mcb_c",
        "fault_fourth_conductor_role": "PE",
        "pe_thermal_enabled": "true",
        "fault_clearing_time_s": "0.1",
    })

    pe = captured["result"]["pe_thermal"]
    assert response.status_code == 200
    assert pe["provisional_status"] == "通过"
    assert pe["outputs"]["protective_conductor_section_mm2"] == 6
    assert "PE导体热稳定" not in captured["result"]["outputs"]["incomplete_checks"]


def test_quick_four_core_does_not_infer_pe_when_role_is_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-four-core-no-pe.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_4c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "50",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "circuit_application": "distribution",
    })

    assert response.status_code == 200
    assert "pe_thermal" not in captured["result"]
    assert captured["form"]["fault_fourth_conductor_role"] == ""
    assert "PE导体热稳定" in captured["result"]["outputs"]["incomplete_checks"]


def test_quick_auto_builds_short_circuit_icu_and_thermal_constraints_without_expert_values(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-auto-protection.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_5c_3ph_n_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "100",
        "transformer_series_code": "scb11",
        "transformer_capacity_kva": "630",
        "transformer_uk_percent": "6",
        "earth_fault_enabled": "true",
        "earthing_system": "TN-S",
        "circuit_application": "distribution",
    })

    result = captured["result"]
    line = result["line_end_short_circuit"]["outputs"]
    phase = result["phase_thermal"]
    pe = result["pe_thermal"]
    stages = {item["code"]: item for item in result["outputs"]["workflow_stages"]}
    assert response.status_code == 200
    assert captured["form"]["short_circuit_line_enabled"] == "true"
    assert captured["form"]["source_impedance_mode"] == "short_circuit_capacity"
    assert captured["form"]["source_short_circuit_capacity_mva"] == "100"
    assert captured["form"]["breaker_installation_point"] == "line_start"
    assert line["terminal_short_circuit_current_ka"] > 0
    assert line["line_start_short_circuit_current_ka"] > line["terminal_short_circuit_current_ka"]
    assert line["required_breaking_capacity_ka"] == line["line_start_short_circuit_current_ka"]
    assert line["required_breaking_capacity_point"] == "line_start"
    assert phase["provisional_status"] == "无法判断"
    assert phase["outputs"]["maximum_permitted_clearing_time_s"] > 0
    assert phase["outputs"]["maximum_permitted_let_through_energy_a2s"] > 0
    assert pe["provisional_status"] == "无法判断"
    assert pe["outputs"]["maximum_permitted_clearing_time_s"] > 0
    assert pe["outputs"]["maximum_permitted_let_through_energy_a2s"] > 0
    assert stages["short_circuit"]["state"] == "completed"
    assert stages["phase_thermal"]["state"] == "candidate"
    assert stages["pe_thermal"]["state"] == "candidate"
    assert "相导体切除时间/I²t实物复核" in result["outputs"]["incomplete_checks"]
    assert "PE切除时间/I²t实物复核" in result["outputs"]["incomplete_checks"]


def test_quick_only_uses_cvs_curve_after_explicit_product_series_selection(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-cvs-review.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)
    base_data = {
        "input_basis": "current", "input_value": "50", "phase": "3",
        "voltage_v": "380", "conductor_family": "YJV",
        "conductor_configuration": "yjv_5c_3ph_n_pe",
        "installation_scenario": "tray", "tray_type": "horizontal_perforated",
        "tray_layers": "1", "tray_cables_per_layer": "1", "length_m": "100",
        "transformer_series_code": "scb11", "transformer_capacity_kva": "630",
        "transformer_uk_percent": "6",
    }

    response = client.post("/quick", data=base_data)
    assert response.status_code == 200
    assert "existing_breaker_product_reference" not in captured["result"]

    response = client.post("/quick", data={
        **base_data,
        "existing_breaker_series": "SCHNEIDER.EASYPACT.CVS.2024",
        "existing_breaker_trip_unit_family": "TM-D",
    })
    product = captured["result"]["existing_breaker_product_reference"]
    thermal = captured["result"]["existing_breaker_phase_thermal"]

    assert response.status_code == 200
    assert product["frame_code"] == "CVS100"
    assert product["rated_current_source"].startswith("复用本次通用断路器初选")
    assert product["icu_ka"] >= thermal["prospective_short_circuit_current_ka"]
    assert thermal["provisional_status"] in {"通过", "不通过"}
    assert thermal["status"] == "无法判断"
    assert "断路器产品参考复核" in response.text
    assert "瞬时脱扣值" in response.text
    assert "性能等级由本次所需Icu自动匹配" in response.text


def test_quick_cvs_review_respects_user_confirmed_nameplate_current(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-cvs-nameplate.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3",
        "voltage_v": "380", "conductor_family": "YJV",
        "conductor_configuration": "yjv_5c_3ph_n_pe",
        "installation_scenario": "tray", "tray_type": "horizontal_perforated",
        "tray_layers": "1", "tray_cables_per_layer": "1", "length_m": "100",
        "transformer_series_code": "scb11", "transformer_capacity_kva": "630",
        "transformer_uk_percent": "6",
        "existing_breaker_series": "SCHNEIDER.EASYPACT.CVS.2024",
        "existing_breaker_rated_current_a": "125",
        "existing_breaker_trip_unit_family": "TM-D",
    })
    product = captured["result"]["existing_breaker_product_reference"]

    assert response.status_code == 200
    assert product["frame_code"] == "CVS160"
    assert product["rated_current_a"] == 125
    assert product["rated_current_source"] == "用户填写的现场设备额定电流"


def test_quick_phase_thermal_uses_line_start_maximum_short_circuit_current(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-phase-thermal.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "100", "phase": "3", "voltage_v": "400",
        "conductor_family": "YJV", "installation_scenario": "conduit", "length_m": "100",
        "short_circuit_line_enabled": "on",
        "short_circuit_line_type": "cable", "short_circuit_line_section_mm2": "50",
        "transformer_capacity_kva": "1000", "transformer_uk_percent": "6",
        "transformer_pk_kw": "10",
        "source_impedance_mode": "infinite_capacity",
        "voltage_factor_c": "1.05",
        "breaker_installation_point": "line_end", "breaker_icu_ka": "25",
        "phase_thermal_enabled": "true", "fault_clearing_time_s": "0.1",
    })

    phase = captured["result"]["phase_thermal"]
    assert response.status_code == 200
    assert phase["outputs"]["phase_conductor_section_mm2"] > 0
    assert phase["outputs"]["prospective_phase_short_circuit_current_a"] > 0
    assert phase["outputs"]["actual_thermal_stress_a2s"] > 0
    assert phase["provisional_status"] in {"通过", "不通过"}
    assert phase["status"] == "无法判断"
    assert "相导体短路热稳定暂算" in response.text
    assert "相导体热稳定" not in captured["result"]["outputs"]["incomplete_checks"]


def test_quick_phase_thermal_does_not_use_breaker_rated_current_as_fault_current(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-phase-thermal-missing.db"))
    captured = capture_template_context(monkeypatch)
    client = TestClient(web.app)

    response = client.post("/quick", data={
        "input_basis": "current", "input_value": "50", "phase": "3", "voltage_v": "380",
        "conductor_family": "YJV", "installation_scenario": "tray",
        "phase_thermal_enabled": "true", "fault_clearing_time_s": "0.1",
    })

    phase = captured["result"]["phase_thermal"]
    assert response.status_code == 200
    assert phase["provisional_status"] == "无法判断"
    assert any("prospective_fault_current_a" in warning for warning in phase["warnings"])
    assert "相导体热稳定" in captured["result"]["outputs"]["incomplete_checks"]


def test_quick_group_load_and_voltage_drop_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "db", Database(tmp_path / "quick-group.db"))
    client = TestClient(web.app)

    result = client.post("/quick", data={
        "circuit_role": "group_load",
        "input_basis": "kw",
        "input_value": "30",
        "power_definition": "design",
        "phase": "3",
        "voltage_v": "380",
        "load_type_code": "fan_coil",
        "power_factor": "0.8",
        "conductor_family": "YJV",
        "conductor_configuration": "yjv_3c_3ph_pe",
        "installation_scenario": "tray",
        "tray_type": "horizontal_perforated",
        "tray_layers": "1",
        "tray_cables_per_layer": "1",
        "length_m": "100",
        "voltage_drop_limit_pct": "5",
    })

    assert result.status_code == 200
    assert "三相汇总说明" in result.text
    assert "电压降率" in result.text
    assert "推荐最小截面" in result.text
    assert "等待必要参数" in result.text
    assert "所选设备子类不适用于当前相制" not in result.text
