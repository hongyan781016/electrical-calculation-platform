"""电气工程计算自动化平台。"""

__version__ = "0.1.1"
from .breaker_selector import generate_breaker_candidates
from .cable_selector import generate_cable_candidates
from .circuit_strategy import (
    combination_inputs_from_strategy,
    resolve_circuit_application_strategy,
)
from .combination_solver import solve_complete_circuit_combinations
from .complete_circuit import CompleteCircuit, affected_calculation_stages
from .complete_circuit_engine import calculate_complete_circuit_chain
from .simple_engine import calculate_simple_load_selection
from .engine import calculate_phase_conductor_thermal_withstand
from .protection_coordination import (
    ManufacturerCoordinationEvidence,
    ProtectionCoordinationInput,
    ProtectionDeviceIdentity,
    evaluate_protection_coordination,
    load_product_coordination_cases,
)
from .product_protection import (
    evaluate_easypact_cvs_phase_thermal_reference,
    load_easypact_cvs_catalog,
    load_easypact_cvs_i2t_curves,
    select_easypact_cvs_reference,
)
from .protective_conductor import calculate_pe_minimum_section_by_table
from .rcd_protection import evaluate_rcd_protection
from .pole_configuration import evaluate_pole_and_neutral_configuration

__all__ = [
    "CompleteCircuit",
    "affected_calculation_stages",
    "calculate_complete_circuit_chain",
    "calculate_simple_load_selection",
    "generate_cable_candidates",
    "generate_breaker_candidates",
    "resolve_circuit_application_strategy",
    "combination_inputs_from_strategy",
    "solve_complete_circuit_combinations",
    "evaluate_rcd_protection",
    "evaluate_pole_and_neutral_configuration",
    "calculate_phase_conductor_thermal_withstand",
    "evaluate_protection_coordination",
    "ManufacturerCoordinationEvidence",
    "ProtectionCoordinationInput",
    "ProtectionDeviceIdentity",
    "load_product_coordination_cases",
    "load_easypact_cvs_catalog",
    "load_easypact_cvs_i2t_curves",
    "select_easypact_cvs_reference",
    "evaluate_easypact_cvs_phase_thermal_reference",
    "calculate_pe_minimum_section_by_table",
]
