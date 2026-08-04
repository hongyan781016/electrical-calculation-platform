from src.electrical_calc.database import Database
from src.electrical_calc.engine import calculate_all


def sample_circuit(code="AL-01"):
    return {
        "code": code,
        "name": "照明回路",
        "phase": "1",
        "voltage_v": 220,
        "installed_power_kw": 5,
        "demand_factor": 0.8,
        "power_factor": 0.9,
        "efficiency": 1,
        "length_m": 30,
        "cable_spec": "",
        "cable_ampacity_a": None,
        "cable_r_ohm_per_km": None,
        "cable_x_ohm_per_km": None,
        "voltage_drop_limit_pct": None,
        "breaker_model": "",
        "breaker_rating_a": None,
        "breaking_capacity_ka": None,
        "source_r_ohm": None,
        "source_x_ohm": None,
        "transformer_r_ohm": None,
        "transformer_x_ohm": None,
    }


def test_runs_are_immutable_and_become_stale(tmp_path):
    database = Database(tmp_path / "test.db")
    project_id = database.create_project("P-01", "测试项目")
    circuit_id = database.upsert_circuit(project_id, sample_circuit())
    circuit = database.get_circuit(circuit_id)
    rules = database.rules_by_code()
    outcomes = [item.to_dict() for item in calculate_all(circuit, rules)]
    run_ids = database.create_runs(project_id, circuit, outcomes, rules)
    assert len(run_ids) == 3
    assert all(run["stale"] == 0 for run in database.list_runs(project_id))

    changed = sample_circuit()
    changed["installed_power_kw"] = 6
    database.upsert_circuit(project_id, changed)
    runs = database.list_runs(project_id)
    assert all(run["stale"] == 1 for run in runs)
    assert database.get_circuit(circuit_id)["revision"] == 2
    assert database.get_run(run_ids[0])["input_snapshot"]["installed_power_kw"] == 5


def test_project_circuit_code_is_unique_per_project(tmp_path):
    database = Database(tmp_path / "test.db")
    first = database.create_project("P-01", "项目一")
    second = database.create_project("P-02", "项目二")
    database.upsert_circuit(first, sample_circuit("C-01"))
    database.upsert_circuit(second, sample_circuit("C-01"))
    assert len(database.list_circuits(first)) == 1
    assert len(database.list_circuits(second)) == 1


def test_transformer_lv_short_circuit_rule_is_seeded_idempotently(tmp_path):
    path = tmp_path / "rules.db"
    first = Database(path)
    second = Database(path)

    rule = second.rules_by_code()["ELEC.SHORT_CIRCUIT.TRANSFORMER_LV"]
    assert rule["status"] == "verified"
    assert rule["clause_no"] == "式(15.9)、表15.7"
    assert rule["page_no"] == "PDF第299、307页"
    assert len([
        item for item in first.list_rules()
        if item["code"] == "ELEC.SHORT_CIRCUIT.TRANSFORMER_LV"
    ]) == 1


def test_transformer_phase_pe_rule_is_seeded_idempotently(tmp_path):
    path = tmp_path / "phase-pe-rules.db"
    first = Database(path)
    second = Database(path)

    rule = second.rules_by_code()["ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE"]
    assert rule["status"] == "verified"
    assert "表4.6-12～表4.6-19" in rule["clause_no"]
    assert rule["page_no"] == "PDF第336～340页（印刷第304～308页）"
    assert len([
        item for item in first.list_rules()
        if item["code"] == "ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE"
    ]) == 1


def test_tray_grouping_rule_is_seeded_idempotently(tmp_path):
    path = tmp_path / "tray-rule.db"
    first = Database(path)
    second = Database(path)

    rule = second.rules_by_code()["ELEC.CABLE.TRAY.GROUPING"]
    assert rule["status"] == "verified"
    assert rule["clause_no"] == "表6.25"
    assert rule["page_no"] == "PDF第107页"
    assert len([
        item for item in first.list_rules()
        if item["code"] == "ELEC.CABLE.TRAY.GROUPING"
    ]) == 1


def test_temperature_derating_rule_is_seeded_idempotently(tmp_path):
    path = tmp_path / "temperature-rules.db"
    first = Database(path)
    second = Database(path)

    rule = second.rules_by_code()["ELEC.CABLE.TEMPERATURE.DERATING"]
    assert rule["status"] == "verified"
    assert rule["clause_no"] == "表6.22、表6.24"
    assert rule["page_no"] == "PDF第106页"
    assert len([
        item for item in first.list_rules()
        if item["code"] == "ELEC.CABLE.TEMPERATURE.DERATING"
    ]) == 1
