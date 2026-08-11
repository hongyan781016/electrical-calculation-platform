from src.electrical_calc.complete_circuit import (
    CalculationStage,
    CircuitApplication,
    CircuitNode,
    CircuitSegment,
    CompleteCircuit,
    DutyCharacteristic,
    EarthingSystem,
    InputBasis,
    Load,
    LoadProfile,
    NodeType,
    Phase,
    PowerDefinition,
    PowerSource,
    SegmentType,
    UpstreamNetworkMode,
    affected_calculation_stages,
)


def valid_circuit(**changes):
    circuit = CompleteCircuit(
        id="circuit-1",
        code="AL-01",
        name="研发中心照明回路",
        system_voltage_v=380,
        line_to_earth_voltage_v=220,
        frequency_hz=50,
        earthing_system=EarthingSystem.TN_S,
        source=PowerSource(
            transformer_family="S11-M",
            rated_capacity_kva=400,
            hv_voltage_kv=10,
            lv_voltage_kv=0.4,
            vector_group="Dyn11",
            uk_percent=4,
            upstream_network_mode=UpstreamNetworkMode.INFINITE_CAPACITY,
        ),
        load=Load(
            input_basis=InputBasis.ACTIVE_POWER_KW,
            input_value=30,
            phase=Phase.THREE,
            circuit_application=CircuitApplication.LIGHTING_FINAL,
            load_profile=LoadProfile.LIGHTING,
            duty_characteristic=DutyCharacteristic.ORDINARY_CONTINUOUS,
            power_definition=PowerDefinition.CALCULATED,
            load_type_code="led_over_5w",
        ),
        nodes=(
            CircuitNode(
                id="tx-lv",
                sequence=0,
                node_type=NodeType.TRANSFORMER_LV,
                name="变压器低压端",
                nominal_voltage_v=380,
            ),
            CircuitNode(
                id="main-db",
                sequence=1,
                node_type=NodeType.MAIN_SWITCHBOARD,
                name="低压总柜",
                nominal_voltage_v=380,
                protection_device_id="qf-main",
            ),
            CircuitNode(
                id="load",
                sequence=2,
                node_type=NodeType.LOAD_TERMINAL,
                name="照明负荷端",
                nominal_voltage_v=380,
            ),
        ),
        segments=(
            CircuitSegment(
                id="busway-1",
                sequence=0,
                from_node_id="tx-lv",
                to_node_id="main-db",
                segment_type=SegmentType.BUSWAY,
                phase=Phase.THREE,
                length_m=5,
                installation_scenario="indoor",
                construction_code="KTA",
            ),
            CircuitSegment(
                id="cable-1",
                sequence=1,
                from_node_id="main-db",
                to_node_id="load",
                segment_type=SegmentType.CABLE,
                phase=Phase.THREE,
                length_m=50,
                installation_scenario="cable_tray",
                conductor_family="YJV",
            ),
        ),
        rule_set_version="rules-2026-07-30",
    )
    values = {**circuit.__dict__, **changes}
    return CompleteCircuit(**values)


def test_valid_lighting_radial_circuit_has_no_issues():
    circuit = valid_circuit()
    assert circuit.validate() == ()
    serialized = circuit.to_dict()
    assert serialized["load"]["circuit_application"] == "lighting_final"
    assert serialized["earthing_system"] == "TN-S"


def test_application_and_load_profile_are_separate_and_must_match():
    circuit = valid_circuit(
        load=Load(
            input_basis=InputBasis.CURRENT_A,
            input_value=32,
            phase=Phase.SINGLE,
            circuit_application=CircuitApplication.SOCKET_FINAL,
            load_profile=LoadProfile.LIGHTING,
            duty_characteristic=DutyCharacteristic.ORDINARY_CONTINUOUS,
        )
    )
    issues = circuit.validate()
    assert any(item.code == "load.profile_mismatch" for item in issues)


def test_distribution_feeder_can_supply_lighting_or_mixed_loads():
    for profile in (LoadProfile.LIGHTING, LoadProfile.MIXED_DISTRIBUTION):
        circuit = valid_circuit(
            load=Load(
                input_basis=InputBasis.CURRENT_A,
                input_value=125,
                phase=Phase.THREE,
                circuit_application=CircuitApplication.DISTRIBUTION,
                load_profile=profile,
                duty_characteristic=DutyCharacteristic.ORDINARY_CONTINUOUS,
            )
        )
        assert not any(
            item.code == "load.profile_mismatch" for item in circuit.validate()
        )


def test_three_phase_motor_rated_output_power_is_valid_for_v02_running_chain():
    circuit = valid_circuit(
        load=Load(
            input_basis=InputBasis.ACTIVE_POWER_KW,
            input_value=15,
            phase=Phase.THREE,
            circuit_application=CircuitApplication.MOTOR_FINAL,
            load_profile=LoadProfile.MOTOR,
            duty_characteristic=DutyCharacteristic.HIGH_INRUSH,
            power_definition=PowerDefinition.CALCULATED,
            power_factor=0.85,
            efficiency=0.9,
        )
    )
    issues = circuit.validate()
    assert not issues


def test_motor_power_input_requires_efficiency_and_three_phase():
    circuit = valid_circuit(
        load=Load(
            input_basis=InputBasis.ACTIVE_POWER_KW,
            input_value=15,
            phase=Phase.SINGLE,
            circuit_application=CircuitApplication.MOTOR_FINAL,
            load_profile=LoadProfile.MOTOR,
            duty_characteristic=DutyCharacteristic.HIGH_INRUSH,
            power_definition=PowerDefinition.CALCULATED,
            power_factor=0.85,
        )
    )

    issue_codes = {item.code for item in circuit.validate()}
    assert "motor.phase_not_supported" in issue_codes
    assert "motor.efficiency_required" in issue_codes


def test_installed_kw_requires_demand_factor_and_power_factor_source():
    circuit = valid_circuit(
        load=Load(
            input_basis=InputBasis.ACTIVE_POWER_KW,
            input_value=50,
            phase=Phase.THREE,
            circuit_application=CircuitApplication.DISTRIBUTION,
            load_profile=LoadProfile.MIXED_DISTRIBUTION,
            duty_characteristic=DutyCharacteristic.ORDINARY_CONTINUOUS,
            power_definition=PowerDefinition.INSTALLED,
        )
    )
    issue_codes = {item.code for item in circuit.validate()}
    assert "load.demand_factor_required" in issue_codes
    assert "load.power_factor_source_required" in issue_codes


def test_tn_c_s_requires_existing_neutral_pe_split_node():
    missing = valid_circuit(earthing_system=EarthingSystem.TN_C_S)
    assert any(
        item.code == "earthing.split_node_required" for item in missing.validate()
    )

    valid = valid_circuit(
        earthing_system=EarthingSystem.TN_C_S,
        neutral_pe_split_node_id="main-db",
    )
    assert not any(item.code.startswith("earthing.") for item in valid.validate())


def test_radial_topology_rejects_segment_that_skips_adjacent_node():
    circuit = valid_circuit()
    broken = CircuitSegment(
        **{
            **circuit.segments[0].__dict__,
            "to_node_id": "load",
        }
    )
    circuit = valid_circuit(segments=(broken, circuit.segments[1]))
    assert any(
        item.code == "topology.disconnected_segment" for item in circuit.validate()
    )


def test_single_phase_load_may_branch_from_three_phase_feeder():
    circuit = valid_circuit()
    single_phase_segment = CircuitSegment(
        **{
            **circuit.segments[1].__dict__,
            "phase": Phase.SINGLE,
        }
    )
    single_phase_load = Load(
        **{
            **circuit.load.__dict__,
            "phase": Phase.SINGLE,
        }
    )
    circuit = valid_circuit(
        load=single_phase_load,
        segments=(circuit.segments[0], single_phase_segment),
    )
    assert not any(
        item.code in {"topology.phase_reversal", "topology.load_phase_mismatch"}
        for item in circuit.validate()
    )


def test_dependency_invalidation_is_scoped_and_deterministic():
    load_stages = affected_calculation_stages(["load.input_value"])
    assert load_stages[0] == CalculationStage.LOAD_CURRENT
    assert CalculationStage.SOURCE_IMPEDANCE not in load_stages
    assert CalculationStage.VOLTAGE_DROP in load_stages
    assert CalculationStage.PROTECTION in load_stages

    protection_stages = affected_calculation_stages(["protection.qf-main.rating"])
    assert protection_stages == (
        CalculationStage.BREAKER_CANDIDATES,
        CalculationStage.PROTECTION,
        CalculationStage.THERMAL_WITHSTAND,
        CalculationStage.SELECTIVITY,
        CalculationStage.COMBINATIONS,
    )


def test_unknown_change_path_invalidates_every_stage():
    assert affected_calculation_stages(["future.unknown_field"]) == tuple(CalculationStage)
