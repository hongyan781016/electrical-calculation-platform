from src.electrical_calc.product_protection import (
    evaluate_easypact_cvs_phase_thermal_reference,
    load_easypact_cvs_catalog,
    load_easypact_cvs_i2t_curves,
    select_easypact_cvs_reference,
)


def test_cvs_catalog_allows_only_provisional_vector_curve_evaluation():
    catalog = load_easypact_cvs_catalog()

    assert catalog["status"] == "verified"
    assert catalog["formal_calculation_allowed"] is False
    assert catalog["energy_limiting_curve"]["available"] is True
    assert catalog["energy_limiting_curve"][
        "automatic_evaluation_allowed"
    ] is True
    assert catalog["energy_limiting_curve"]["formal_calculation_allowed"] is False


def test_cvs_vector_curves_are_traceable_and_share_only_declared_frame_curve():
    curves = load_easypact_cvs_i2t_curves()

    assert curves["source"]["pdf_page"] == 91
    assert curves["source"]["document"] == (
        "Schneider_EasyPact_CVS_Catalog_2024_LVED210011EN.pdf"
    )
    assert "sha256" not in curves["source"]
    assert len(curves["curves"]) == 4
    assert curves["curves"][0]["applicable_frames"] == ["CVS100", "CVS160"]
    assert curves["curves"][1]["applicable_frames"] == ["CVS250"]
    assert all(curve["sample_count"] >= 161 for curve in curves["curves"])


def test_cvs_tm_d_reference_selects_exact_frame_level_and_pickup():
    result = select_easypact_cvs_reference(63, 13.8)

    assert result["frame_code"] == "CVS100"
    assert result["performance_level"] == "B"
    assert result["icu_ka"] == 25
    assert result["ics_ka"] == 25
    assert result["trip_configuration"]["instantaneous_pickup_a"] == 500
    assert result["formal_calculation_allowed"] is False


def test_cvs_reference_uses_next_exact_breaking_level_without_interpolation():
    result = select_easypact_cvs_reference(125, 30)

    assert result["frame_code"] == "CVS160"
    assert result["performance_level"] == "F"
    assert result["icu_ka"] == 36
    assert result["trip_configuration"]["instantaneous_pickup_a"] == 1250


def test_cvs_ets_reference_prefills_exact_maximum_break_time():
    result = select_easypact_cvs_reference(
        400, 40, trip_unit_family="ETS"
    )

    assert result["frame_code"] == "CVS400"
    assert result["performance_level"] == "N"
    assert result["trip_configuration"]["instantaneous_pickup_a"] == 4800
    assert result["trip_configuration"][
        "instantaneous_maximum_break_time_ms"
    ] == 50


def test_cvs_reference_rejects_unlisted_rating_and_voltage():
    unlisted_rating = select_easypact_cvs_reference(110, 25)
    wrong_voltage = select_easypact_cvs_reference(63, 10, system_voltage_v=440)

    assert "不插值" in unlisted_rating["reason"]
    assert "380/415V" in wrong_voltage["reason"]


def test_cvs_product_curve_provisionally_checks_phase_conductor_i2t():
    product = select_easypact_cvs_reference(63, 13.8)
    passed = evaluate_easypact_cvs_phase_thermal_reference(
        product, 13.8, 500_000
    )
    failed = evaluate_easypact_cvs_phase_thermal_reference(
        product, 13.8, 100_000
    )

    assert passed["provisional_status"] == "通过"
    assert failed["provisional_status"] == "不通过"
    assert passed["status"] == "无法判断"
    assert passed["breaker_conservative_let_through_i2t_a2s"] > 100_000
    assert passed["curve_index"] == 1


def test_cvs_product_curve_does_not_extrapolate_or_ignore_icu():
    product = select_easypact_cvs_reference(63, 13.8)
    below_curve = evaluate_easypact_cvs_phase_thermal_reference(
        product, 2.0, 1_000_000
    )
    over_icu = evaluate_easypact_cvs_phase_thermal_reference(
        product, 30.0, 1_000_000
    )

    assert below_curve["provisional_status"] == "无法判断"
    assert "不外推" in below_curve["reason"]
    assert over_icu["provisional_status"] == "不通过"
    assert "超过所选产品Icu" in over_icu["reason"]
