"""面向用户工作流的单回路验收样例。

这些不是公式单元测试：每例都模拟客户从已知量、负荷、线路到候选结果
的一次完整提交。规则尚未批准时正式状态必须保持“无法判断”。
"""

import pytest

from src.electrical_calc.engine import PASS, UNKNOWN
from src.electrical_calc.simple_engine import calculate_simple_load_selection


@pytest.mark.parametrize(
    ("data", "expected_current", "expected_section", "expected_breaker", "expected_drop"),
    [
        (
            {
                "circuit_role": "single_device",
                "input_basis": "kw",
                "input_value": 3,
                "phase": "1",
                "voltage_v": 220,
                "load_type_code": "led_over_5w",
                "conductor_family": "BV",
                "conductor_configuration": "bv_1ph_2wire_pe",
                "installation_scenario": "conduit",
                "length_m": 30,
            },
            15.1515,
            4,
            16,
            1.9449,
        ),
        (
            {
                "circuit_role": "single_device",
                "input_basis": "kva",
                "input_value": 30,
                "phase": "3",
                "voltage_v": 380,
                "conductor_family": "YJV",
                "conductor_configuration": "yjv_4c_3ph_n_pe",
                "installation_scenario": "tray",
                "tray_type": "horizontal_perforated",
                "tray_layers": "1",
                "tray_cables_per_layer": "1",
                "length_m": 100,
            },
            45.5803,
            10,
            50,
            4.5187,
        ),
    ],
)
def test_user_can_complete_a_normal_single_circuit_initial_selection(
    data, expected_current, expected_section, expected_breaker, expected_drop
):
    result = calculate_simple_load_selection(data, {})

    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert result.outputs["design_current_a"] == pytest.approx(expected_current)
    assert result.outputs["provisional_breaker_rating_a"] == expected_breaker
    assert result.outputs["cable_candidates"][0]["section_mm2"] == expected_section
    assert result.outputs["voltage_drop"]["voltage_drop_pct"] == pytest.approx(
        expected_drop
    )
    assert result.outputs["voltage_drop"]["provisional_status"] == PASS
    assert result.outputs["incomplete_checks"]
