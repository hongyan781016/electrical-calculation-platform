from src.electrical_calc.breaker_selector import (
    BreakerSelectionRequest,
    generate_breaker_candidates,
)
from src.electrical_calc.complete_circuit import CircuitApplication, Phase
from src.electrical_calc.engine import FAIL, PASS, UNKNOWN


def approved_rules():
    return {
        code: {"status": "approved"}
        for code in (
            "ELEC.BREAKER.RATING",
            "ELEC.CABLE.COORDINATION",
            "ELEC.BREAKING.CAPACITY",
            "ELEC.BREAKER.MCB.INSTANTANEOUS",
            "ELEC.BREAKER.ICS.ICW.REFERENCE",
        )
    }


def mcb_request(**changes):
    request = BreakerSelectionRequest(
        node_id="db-1",
        circuit_application=CircuitApplication.LIGHTING_FINAL,
        phase=Phase.THREE,
        system_voltage_v=380,
        design_current_a=30,
        conductor_corrected_ampacity_a=40,
        allowed_families=("MCB",),
        pole_requirement="3P",
        prospective_short_circuit_ka=5,
        minimum_fault_current_a=400,
        mcb_trip_curve="C",
        ics_requirement_mode="at_least_prospective_fault",
        short_time_withstand_required=False,
    )
    return BreakerSelectionRequest(**{**request.__dict__, **changes})


def test_mcb_candidate_satisfies_current_icu_poles_and_guaranteed_action():
    result = generate_breaker_candidates(mcb_request(), approved_rules())
    candidates = result.outputs["candidates"]
    assert len(candidates) == 2
    candidate = candidates[0]
    assert candidate["family"] == "MCB"
    assert candidate["frame_current_a"] == 63
    assert candidate["rated_current_a"] == 32
    assert candidate["selected_icu_ka"] == 6
    assert candidate["adopted_poles"] == "3P"
    assert candidate["guaranteed_action_current_a"] == 320
    assert candidate["automatic_trip_status"] == PASS
    assert candidate["selected_ics_ka"] == 6
    assert candidate["ics_status"] == PASS
    assert candidate["icw_status"] == PASS
    assert candidate["formal_status"] == PASS


def test_candidates_outside_ib_in_iz_relationship_are_rejected_with_reason():
    result = generate_breaker_candidates(
        mcb_request(
            design_current_a=24,
            conductor_corrected_ampacity_a=25,
            pole_requirement="unconfirmed",
            prospective_short_circuit_ka=None,
            minimum_fault_current_a=None,
            mcb_trip_curve=None,
        ),
        approved_rules(),
    )
    assert all(
        24 <= item["rated_current_a"] <= 25
        for item in result.outputs["candidates"]
    )
    reason_codes = {
        item["reason_code"] for item in result.outputs["rejected_candidates"]
    }
    assert "rated_current_below_load" in reason_codes
    assert "rated_current_above_conductor" in reason_codes


def test_missing_short_circuit_and_pole_conditions_keep_design_candidates_unknown():
    result = generate_breaker_candidates(
        mcb_request(
            pole_requirement="unconfirmed",
            prospective_short_circuit_ka=None,
            minimum_fault_current_a=None,
            mcb_trip_curve=None,
        ),
        approved_rules(),
    )
    assert result.outputs["candidates"]
    assert all(
        item["selected_icu_ka"] is None for item in result.outputs["candidates"]
    )
    assert result.provisional_status == PASS
    assert result.status == UNKNOWN
    assert any("Icu只能列出档位" in warning for warning in result.warnings)


def test_mcb_is_rejected_when_minimum_fault_current_cannot_guarantee_trip():
    result = generate_breaker_candidates(
        mcb_request(minimum_fault_current_a=200),
        approved_rules(),
    )
    assert result.outputs["candidates"] == []
    rejected = result.outputs["rejected_candidates"]
    assert any(
        item["reason_code"] == "automatic_trip_not_guaranteed"
        for item in rejected
    )


def test_mccb_distribution_candidates_output_frame_and_required_icu_but_not_trip_curve():
    request = BreakerSelectionRequest(
        node_id="main-feeder",
        circuit_application=CircuitApplication.DISTRIBUTION,
        phase=Phase.THREE,
        system_voltage_v=380,
        design_current_a=125,
        conductor_corrected_ampacity_a=160,
        allowed_families=("MCCB",),
        pole_requirement="4P",
        prospective_short_circuit_ka=30,
        minimum_fault_current_a=1000,
    )
    result = generate_breaker_candidates(request, approved_rules())
    candidates = result.outputs["candidates"]
    assert candidates
    assert candidates[0]["frame_current_a"] == 160
    assert candidates[0]["rated_current_a"] == 125
    assert candidates[0]["selected_icu_ka"] == 35
    assert candidates[0]["guaranteed_action_current_a"] is None
    assert "实际脱扣/整定特性" in candidates[0]["pending_checks"]
    assert candidates[0]["formal_status"] == UNKNOWN
    assert result.status == UNKNOWN


def test_mccb_ics_policy_selects_service_capacity_for_selected_icu():
    request = BreakerSelectionRequest(
        node_id="main",
        circuit_application=CircuitApplication.DISTRIBUTION,
        phase=Phase.THREE,
        system_voltage_v=380,
        design_current_a=125,
        conductor_corrected_ampacity_a=160,
        allowed_families=("MCCB",),
        pole_requirement="4P",
        prospective_short_circuit_ka=30,
        ics_requirement_mode="at_least_prospective_fault",
        short_time_withstand_required=False,
    )
    result = generate_breaker_candidates(request, approved_rules())
    candidate = result.outputs["candidates"][0]
    assert candidate["selected_icu_ka"] == 35
    assert candidate["ics_options_ka"] == [17.5, 24.5, 35]
    assert candidate["selected_ics_ka"] == 35
    assert candidate["ics_status"] == PASS


def test_mccb_icw_is_checked_only_for_explicit_short_time_strategy():
    request = BreakerSelectionRequest(
        node_id="main",
        circuit_application=CircuitApplication.DISTRIBUTION,
        phase=Phase.THREE,
        system_voltage_v=380,
        design_current_a=250,
        conductor_corrected_ampacity_a=400,
        allowed_families=("MCCB",),
        pole_requirement="3P",
        prospective_short_circuit_ka=4,
        ics_requirement_mode="at_least_prospective_fault",
        short_time_withstand_required=True,
        short_time_delay_s=0.5,
    )
    passed = generate_breaker_candidates(request, approved_rules())
    frame_400 = next(
        item
        for item in passed.outputs["candidates"]
        if item["frame_current_a"] == 400
        and item["rated_current_a"] == 250
    )
    assert frame_400["icw_1s_ka"] == 5
    assert frame_400["icw_status"] == PASS

    failed = generate_breaker_candidates(
        BreakerSelectionRequest(
            **{
                **request.__dict__,
                "prospective_short_circuit_ka": 9,
            }
        ),
        approved_rules(),
    )
    assert any(
        item["reason_code"] == "short_time_withstand_insufficient"
        for item in failed.outputs["rejected_candidates"]
    )


def test_acb_keeps_icw_unknown_when_reference_table_does_not_list_it():
    request = BreakerSelectionRequest(
        node_id="main",
        circuit_application=CircuitApplication.DISTRIBUTION,
        phase=Phase.THREE,
        system_voltage_v=380,
        design_current_a=800,
        conductor_corrected_ampacity_a=1000,
        allowed_families=("ACB",),
        pole_requirement="3P",
        prospective_short_circuit_ka=50,
        ics_requirement_mode="at_least_prospective_fault",
        short_time_withstand_required=True,
        short_time_delay_s=0.5,
    )
    result = generate_breaker_candidates(request, approved_rules())
    candidate = result.outputs["candidates"][0]
    assert candidate["selected_icu_ka"] == 65
    assert candidate["ics_options_ka"] == [55.25, 65]
    assert candidate["selected_ics_ka"] == 55.25
    assert candidate["ics_status"] == PASS
    assert candidate["icw_1s_ka"] is None
    assert candidate["icw_table_status"] == "not_tabulated"
    assert candidate["icw_status"] == UNKNOWN


def test_ordinary_module_does_not_invent_generic_d_curve_action_value():
    result = generate_breaker_candidates(
        mcb_request(mcb_trip_curve="D"),
        approved_rules(),
    )
    assert result.outputs["candidates"] == []
    assert result.provisional_status == UNKNOWN
    assert any("通用D型" in warning for warning in result.warnings)


def test_breaking_capacity_failure_is_recorded_instead_of_selecting_larger_current():
    result = generate_breaker_candidates(
        mcb_request(prospective_short_circuit_ka=20),
        approved_rules(),
    )
    assert result.outputs["candidates"] == []
    assert any(
        item["reason_code"] == "breaking_capacity_insufficient"
        for item in result.outputs["rejected_candidates"]
    )
