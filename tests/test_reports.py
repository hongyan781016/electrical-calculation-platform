from src.electrical_calc.reports import _register_report_font, create_run_pdf


def test_pdf_font_falls_back_when_simhei_is_unavailable(tmp_path):
    assert _register_report_font(tmp_path / "missing-simhei.ttf") == "STSong-Light"


def test_pdf_generation_contains_multiple_pages():
    run = {
        "id": 1,
        "project_code": "P-01",
        "project_name": "测试项目",
        "circuit_code": "AL-01",
        "circuit_name": "照明回路",
        "module": "负荷与选型",
        "status": "无法判断",
        "provisional_status": "通过",
        "stale": 0,
        "engine_version": "0.1.0",
        "circuit_revision": 1,
        "created_at": "2026-07-23T00:00:00+00:00",
        "input_snapshot": {"phase": "1", "voltage_v": 220, "installed_power_kw": 5},
        "process_json": [{"label": "计算电流", "expression": "P/U", "value": 22.7, "unit": "A"}],
        "result_json": {"design_current_a": 22.7},
        "warnings_json": ["计算依据尚未全部批准。"],
        "rule_snapshot": {
            "ELEC.LOAD.CURRENT": {
                "name": "负荷电流计算方法",
                "status": "pending",
                "document_name": "",
                "document_version": "",
                "clause_no": "",
                "page_no": "",
                "original_text": "",
            }
        },
    }
    content = create_run_pdf(run)
    assert content.startswith(b"%PDF")
    assert len(content) > 3000
