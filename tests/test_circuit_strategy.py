from __future__ import annotations

import pytest

from src.electrical_calc.circuit_strategy import (
    CircuitStrategyRequest,
    combination_inputs_from_strategy,
    resolve_circuit_application_strategy,
)
from src.electrical_calc.complete_circuit import (
    CircuitApplication,
    ConnectionMode,
    EarthingSystem,
    LoadProfile,
    Phase,
)
from src.electrical_calc.engine import PASS, UNKNOWN


def approved_rules() -> dict[str, dict[str, str]]:
    return {
        "ELEC.VDROP.LIMIT": {"status": "approved"},
        "ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME": {"status": "approved"},
        "ELEC.RCD.PARAMETERS": {"status": "approved"},
    }


def request(**changes: object) -> CircuitStrategyRequest:
    values: dict[str, object] = {
        "circuit_application": CircuitApplication.LIGHTING_FINAL,
        "load_profile": LoadProfile.LIGHTING,
        "phase": Phase.SINGLE,
        "earthing_system": EarthingSystem.TN_S,
        "line_to_earth_voltage_v": 230,
        "circuit_rated_current_a": 16,
        "neutral_required": True,
    }
    values.update(changes)
    return CircuitStrategyRequest(**values)


def test_lighting_strategy_uses_conservative_three_percent_and_fixed_time():
    result = resolve_circuit_application_strategy(request(), approved_rules())
    assert result.outputs["voltage_drop"]["table_value"] == "3～5"
    assert result.outputs["voltage_drop"]["limit_pct"] == 3
    assert "平台映射" in result.outputs["voltage_drop"]["mapping_note"]
    assert result.outputs["connection_mode"] == "fixed_connected"
    assert result.outputs["automatic_disconnection"]["maximum_time_s"] == 0.4
    assert result.provisional_status == PASS
    assert result.status == PASS


def test_socket_63a_boundary_and_over_boundary_time():
    at_boundary = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.SOCKET_FINAL,
            load_profile=LoadProfile.SOCKET,
            circuit_rated_current_a=63,
        ),
        approved_rules(),
    )
    over_boundary = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.SOCKET_FINAL,
            load_profile=LoadProfile.SOCKET,
            circuit_rated_current_a=80,
        ),
        approved_rules(),
    )
    assert at_boundary.outputs["automatic_disconnection"]["maximum_time_s"] == 0.4
    assert over_boundary.outputs["automatic_disconnection"]["maximum_time_s"] == 5


def test_ordinary_equipment_requires_connection_mode():
    result = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
            load_profile=LoadProfile.ORDINARY_EQUIPMENT,
            circuit_rated_current_a=32,
        ),
        approved_rules(),
    )
    assert result.outputs["automatic_disconnection"]["status"] == UNKNOWN
    assert result.provisional_status == UNKNOWN
    assert any("固定连接还是经插座" in item for item in result.warnings)


def test_ordinary_fixed_equipment_32a_boundary():
    result = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.ORDINARY_EQUIPMENT_FINAL,
            load_profile=LoadProfile.ORDINARY_EQUIPMENT,
            connection_mode=ConnectionMode.FIXED_CONNECTED,
            circuit_rated_current_a=32,
        ),
        approved_rules(),
    )
    assert result.outputs["automatic_disconnection"]["maximum_time_s"] == 0.4


def test_distribution_mixed_load_requires_lighting_presence_to_set_limit():
    unresolved = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.DISTRIBUTION,
            load_profile=LoadProfile.MIXED_DISTRIBUTION,
            phase=Phase.THREE,
            circuit_rated_current_a=400,
            neutral_required=True,
            supplies_lighting=None,
        ),
        approved_rules(),
    )
    lighting = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.DISTRIBUTION,
            load_profile=LoadProfile.MIXED_DISTRIBUTION,
            phase=Phase.THREE,
            circuit_rated_current_a=400,
            neutral_required=True,
            supplies_lighting=True,
        ),
        approved_rules(),
    )
    assert unresolved.outputs["voltage_drop"]["limit_pct"] is None
    assert unresolved.provisional_status == UNKNOWN
    assert lighting.outputs["voltage_drop"]["limit_pct"] == 3
    assert lighting.outputs["automatic_disconnection"]["maximum_time_s"] == 5


def test_rcd_value_and_type_only_resolve_after_scenario_and_waveform():
    unresolved = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.SOCKET_FINAL,
            load_profile=LoadProfile.SOCKET,
        ),
        approved_rules(),
    )
    resolved = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.SOCKET_FINAL,
            load_profile=LoadProfile.SOCKET,
            rcd_scenario="additional_30ma",
            residual_current_waveform="pulsating_dc",
        ),
        approved_rules(),
    )
    assert unresolved.outputs["rcd"]["status"] == UNKNOWN
    assert resolved.outputs["rcd"]["scenario"]["rated_residual_current_max_ma"] == 30
    assert resolved.outputs["rcd"]["waveform"]["rcd_type"] == "A型或F型"


def test_helper_builds_combination_solver_control_fields():
    strategy = resolve_circuit_application_strategy(request(), approved_rules())
    fields = combination_inputs_from_strategy(
        strategy,
        node_id="db",
        protected_segment_id="lighting-line",
        pole_requirement="1P+N",
        mcb_trip_curve="C",
    )
    assert fields["voltage_drop_limit_pct"] == 3
    assert fields["maximum_disconnection_time_s"] == 0.4
    assert fields["protection_point"].allowed_families == ("MCB", "MCCB")
    assert fields["protection_point"].pole_requirement == "1P+N"
    assert fields["protection_point"].pole_and_neutral is not None
    assert fields["protection_point"].pole_and_neutral.neutral_required is True


def test_helper_preserves_complete_rcd_design_inputs():
    strategy = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.SOCKET_FINAL,
            load_profile=LoadProfile.SOCKET,
            rcd_required=True,
            rcd_applicability_reference="插座附加保护条件",
            rcd_scenario="additional_30ma",
            residual_current_waveform="pulsating_dc",
            selected_rated_residual_current_ma=30,
            normal_leakage_current_ma=5,
        ),
        approved_rules(),
    )
    fields = combination_inputs_from_strategy(
        strategy,
        node_id="db",
        protected_segment_id="socket-line",
    )
    rcd = fields["protection_point"].rcd
    assert rcd is not None
    assert rcd.required is True
    assert rcd.scenario_code == "additional_30ma"
    assert rcd.residual_waveform_code == "pulsating_dc"
    assert rcd.selected_rated_residual_current_ma == 30
    assert rcd.normal_leakage_current_ma == 5


def test_helper_preserves_ics_and_short_time_strategy():
    strategy = resolve_circuit_application_strategy(
        request(
            ics_requirement_mode="at_least_prospective_fault",
            short_time_withstand_required=True,
            short_time_delay_s=0.5,
        ),
        approved_rules(),
    )
    fields = combination_inputs_from_strategy(
        strategy,
        node_id="db",
        protected_segment_id="line",
    )
    point = fields["protection_point"]
    assert point.ics_requirement_mode == "at_least_prospective_fault"
    assert point.short_time_withstand_required is True
    assert point.short_time_delay_s == 0.5


def test_helper_rejects_unresolved_voltage_limit():
    strategy = resolve_circuit_application_strategy(
        request(
            circuit_application=CircuitApplication.DISTRIBUTION,
            load_profile=LoadProfile.MIXED_DISTRIBUTION,
            phase=Phase.THREE,
            supplies_lighting=None,
        ),
        approved_rules(),
    )
    with pytest.raises(ValueError, match="允许电压降"):
        combination_inputs_from_strategy(
            strategy,
            node_id="main",
            protected_segment_id="feeder",
        )
