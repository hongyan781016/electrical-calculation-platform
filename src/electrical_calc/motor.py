"""三相低压异步电动机末端回路的领域输入。

本模块只描述电动机铭牌事实和启动场景，不包含产品目录、页面字段或
规范数值。V0.2.0仅支持单台三相异步电动机直接启动。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class MotorKnownBasis(StringEnum):
    RATED_OUTPUT_POWER_KW = "rated_output_power_kw"
    NAMEPLATE_CURRENT_A = "nameplate_current_a"


class MotorStartingFrequency(StringEnum):
    FREQUENT = "frequent"
    INFREQUENT = "infrequent"
    UNKNOWN = "unknown"


class MotorBusLoadCondition(StringEnum):
    LIGHTING_OR_SENSITIVE_LOADS = "lighting_or_sensitive_loads"
    NO_LIGHTING_OR_SENSITIVE_LOADS = "no_lighting_or_sensitive_loads"
    NO_OTHER_LOADS = "no_other_loads"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MotorCatalogQuery:
    """产品样本参数的精确查询条件。

    V0.2.0不按功率插值，也不跨极数或跨系列套用参数。
    """

    rated_output_power_kw: float
    poles: int = 4
    series_code: str = "1LE1003"


@dataclass(frozen=True)
class MotorLoadInput:
    known_basis: MotorKnownBasis
    known_value: float
    rated_voltage_v: float
    power_factor: float | None = None
    efficiency: float | None = None
    locked_rotor_current_ratio: float | None = None


@dataclass(frozen=True)
class MotorStartingVoltageScenario:
    starting_frequency: MotorStartingFrequency
    bus_load_condition: MotorBusLoadCondition


@dataclass(frozen=True)
class MotorStartingNetworkInput:
    nominal_line_voltage_v: float
    source_open_circuit_voltage_factor: float
    locked_rotor_current_at_nominal_voltage_a: float
    locked_rotor_power_factor: float | None
    source_to_bus_r_ohm: float
    source_to_bus_x_ohm: float
    bus_to_motor_r_ohm: float
    bus_to_motor_x_ohm: float
    minimum_bus_voltage_percent: float | None = None


@dataclass(frozen=True)
class MotorApproximateStartingInput:
    """手册表6.5-4全压启动近似计算所需的结构化输入。"""

    nominal_network_voltage_kv: float
    system_average_voltage_kv: float
    motor_rated_voltage_kv: float
    motor_rated_current_ka: float
    locked_rotor_current_ratio: float
    bus_short_circuit_capacity_mva: float
    preconnected_reactive_load_mvar: float
    motor_feeder_reactance_ohm: float
    source_bus_voltage_pu: float = 1.05
    minimum_bus_voltage_percent: float | None = None


@dataclass(frozen=True)
class MotorCablePreselectionInput:
    rated_current_a: float
    running_power_factor: float | None
    rated_voltage_v: float
    length_m: float
    conductor_family: str
    conductor_configuration_code: str
    installation_scenario: str
    installation_temperature_c: float | None = None
    tray_type: str | None = None
    tray_layers: int | None = None
    tray_cables_per_layer: int | None = None
    enclosed_circuit_count: int | None = None


@dataclass(frozen=True)
class MotorNetworkInput:
    transformer_family: str
    transformer_capacity_kva: float
    transformer_uk_percent: float
    upstream_short_circuit_capacity_mva: float
    system_voltage_v: float = 380.0
    line_to_earth_voltage_v: float = 220.0
    hv_voltage_kv: float = 10.0
    transformer_vector_group: str = "Dyn11"
    maximum_short_circuit_voltage_factor: float = 1.05
    minimum_fault_voltage_factor: float = 0.8
    running_voltage_drop_limit_percent: float = 5.0
    minimum_starting_bus_voltage_percent: float | None = None
    preconnected_reactive_load_mvar: float | None = None
    motor_starting_time_s: float | None = None


@dataclass(frozen=True)
class MotorBreakerRequirementInput:
    """某一电缆候选对应的电动机断路器设计边界。

    这里描述必须满足的电气条件，不代表任何品牌或具体产品已经通过。
    """

    motor_rated_current_a: float
    motor_starting_current_a: float
    system_voltage_v: float
    conductor_corrected_ampacity_a: float
    installation_point_max_short_circuit_ka: float
    terminal_minimum_fault_current_a: float
    phase_maximum_clearing_time_s: float
    pe_maximum_clearing_time_s: float
