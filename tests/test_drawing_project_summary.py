from math import sqrt

from src.electrical_calc.drawing_project_summary import summarize_drawing_circuits


def _circuit(code: str, current: float, capacity: float = 1000) -> dict:
    return {
        "latest_run_id": 1,
        "derived_json": {"design_current_a": current},
        "audit_json": {"outputs": {"chain_result": {"outputs": {"node_results": [
            {"node_id": "tx", "three_phase_short_circuit_ka": 22.0},
            {"node_id": "main", "three_phase_short_circuit_ka": 18.0},
        ]}}}},
        "input_json": {
            "circuit_code": code,
            "transformer_code": "T1",
            "bus_section_code": "I",
            "feeder_cabinet_code": "AA1",
            "transformer_family": "SCB14",
            "transformer_capacity_kva": capacity,
            "transformer_uk_percent": 6,
        },
    }


def test_project_summary_requires_explicit_simultaneity_factor():
    summary = summarize_drawing_circuits([_circuit("C1", 100), _circuit("C2", 150)], None)
    assert summary["arithmetic_total_current_a"] == 250
    assert summary["upstream_design_current_a"] is None
    assert summary["transformer_capacity_status"] == "无法判断"


def test_project_summary_checks_transformer_capacity_after_diversity():
    summary = summarize_drawing_circuits([_circuit("C1", 100), _circuit("C2", 150)], 0.8)
    assert summary["upstream_design_current_a"] == 200
    assert summary["transformer_rated_current_a"] == round(1000 * 1000 / (sqrt(3) * 380), 6)
    assert summary["transformer_capacity_status"] == "通过"


def test_project_summary_rejects_mixed_source_tree_for_capacity_check():
    summary = summarize_drawing_circuits([_circuit("C1", 100), _circuit("C2", 150, 800)], 0.8)
    assert summary["source_consistent"] is False
    assert summary["transformer_capacity_status"] == "无法判断"
    assert any("不一致" in warning for warning in summary["warnings"])


def test_project_summary_groups_circuits_by_transformer_bus_and_cabinet():
    c1 = _circuit("C1", 100)
    c2 = _circuit("C2", 150)
    c3 = _circuit("C3", 80)
    c3["input_json"]["feeder_cabinet_code"] = "AA2"
    summary = summarize_drawing_circuits([c1, c2, c3], 0.8)
    assert len(summary["transformer_groups"]) == 1
    assert len(summary["bus_section_groups"]) == 1
    assert [group["arithmetic_total_current_a"] for group in summary["feeder_cabinet_groups"]] == [250.0, 80.0]
    assert [group["circuit_codes"] for group in summary["feeder_cabinet_groups"]] == [["C1", "C2"], ["C3"]]


def test_project_summary_calculates_each_level_from_direct_children_only():
    settings = [
        {"level":"feeder","transformer_code":"T1","bus_section_code":"I","feeder_cabinet_code":"AA1","factor":0.8,"rated_current_a":250,"source_note":"馈线条件"},
        {"level":"bus","transformer_code":"T1","bus_section_code":"I","feeder_cabinet_code":"","factor":0.9,"rated_current_a":250,"source_note":"母线条件"},
        {"level":"transformer","transformer_code":"T1","bus_section_code":"","feeder_cabinet_code":"","factor":1.0,"rated_current_a":300,"source_note":"变压器条件"},
    ]
    summary = summarize_drawing_circuits([_circuit("C1",100),_circuit("C2",150)], None, settings)
    assert summary["feeder_cabinet_groups"][0]["design_current_a"] == 200
    assert summary["bus_section_groups"][0]["direct_child_current_a"] == 200
    assert summary["bus_section_groups"][0]["design_current_a"] == 180
    assert summary["transformer_groups"][0]["direct_child_current_a"] == 180
    assert summary["transformer_groups"][0]["design_current_a"] == 180
    assert summary["transformer_groups"][0]["equipment_status"] == "通过"


def test_project_summary_does_not_skip_missing_child_level_factor():
    settings = [{"level":"transformer","transformer_code":"T1","bus_section_code":"","feeder_cabinet_code":"","factor":0.8,"rated_current_a":300,"source_note":""}]
    summary = summarize_drawing_circuits([_circuit("C1",100)], None, settings)
    assert summary["feeder_cabinet_groups"][0]["design_current_a"] is None
    assert summary["bus_section_groups"][0]["design_current_a"] is None
    assert summary["transformer_groups"][0]["design_current_a"] is None


def test_group_equipment_short_circuit_checks_use_maximum_node_value_and_evidence():
    c1=_circuit("C1",100); c2=_circuit("C2",100)
    c2["audit_json"]["outputs"]["chain_result"]["outputs"]["node_results"][1]["three_phase_short_circuit_ka"]=20
    settings=[{"level":"feeder","transformer_code":"T1","bus_section_code":"I","feeder_cabinet_code":"AA1",
               "factor":1.0,"rated_current_a":250,"short_time_withstand_ka":25,
               "breaker_designation":"QF1","breaker_breaking_capacity_ka":18,
               "selectivity_upstream_designation":"QF1 Ir=200A","selectivity_downstream_designation":"QF2 In=63A",
               "selectivity_limit_ka":25,"selectivity_reference":"厂家表第10页","source_note":"铭牌"}]
    group=summarize_drawing_circuits([c1,c2],None,settings)["feeder_cabinet_groups"][0]
    assert group["prospective_short_circuit_ka"] == 20
    assert group["short_time_withstand_status"] == "通过"
    assert group["breaking_capacity_status"] == "不通过"
    assert group["selectivity_status"] == "通过"


def test_selectivity_stays_unknown_without_complete_manufacturer_evidence():
    settings=[{"level":"feeder","transformer_code":"T1","bus_section_code":"I","feeder_cabinet_code":"AA1",
               "factor":1.0,"rated_current_a":250,"selectivity_limit_ka":25,"source_note":""}]
    group=summarize_drawing_circuits([_circuit("C1",100)],None,settings)["feeder_cabinet_groups"][0]
    assert group["selectivity_status"] == "无法判断"


def test_completeness_prioritizes_failures_and_blocks_release():
    circuit=_circuit("C1",100)
    circuit.update({"id":1,"circuit_code":"C1","status":"通过"})
    circuit["audit_json"]["outputs"]["component_matrix"]=[{
        "component_type":"breaker","component_name":"断路器","designation":"QF1",
        "checks":{"breaking_capacity":{"status":"不通过","check_name":"分断能力"}},
        "remediation_actions":["提高Icu"],
    }]
    settings=[{"level":"feeder","transformer_code":"T1","bus_section_code":"I","feeder_cabinet_code":"AA1",
               "factor":1,"rated_current_a":250,"short_time_withstand_ka":25,
               "breaker_breaking_capacity_ka":25,"breaker_designation":"QF1",
               "selectivity_upstream_designation":"QF1","selectivity_downstream_designation":"QF2",
               "selectivity_limit_ka":25,"selectivity_reference":"厂家表"}]
    result=summarize_drawing_circuits([circuit],None,settings)["completeness"]
    assert result["engineering_data_gate"] == "不通过"
    assert result["formal_release_gate"] == "不通过"
    assert result["issues"][0]["priority"] == "阻断"
    assert any(issue["check"]=="分断能力" for issue in result["issues"])


def test_completeness_lists_uncomputed_circuit_as_unknown():
    circuit={"id":2,"circuit_code":"C2","circuit_name":"未计算","input_json":{},"derived_json":{}}
    result=summarize_drawing_circuits([circuit],None,[])["completeness"]
    assert result["engineering_data_gate"] == "无法判断"
    assert result["issues"][0]["check"] == "当前有效计算"
