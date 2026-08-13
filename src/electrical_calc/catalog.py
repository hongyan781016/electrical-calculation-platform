from __future__ import annotations

from typing import Any


LOAD_TYPE_GROUPS = [
    ("resistive", "纯电阻 / 电热"),
    ("lighting", "照明"),
    ("household", "家用电器"),
    ("kitchen", "厨房 / 炊事设备"),
    ("hvac", "空调 / 制冷设备"),
    ("other", "其他 / 不确定"),
]

# 数值逐项核对自 19DX101-1 表3.38～表3.42（PDF第37～40页）。
# “verified”表示已核对原图，但尚未由使用者批准为正式计算规则。
LOAD_TYPES: dict[str, dict[str, Any]] = {
    "electric_heater": {"group": "resistive", "name": "电加热器 / 电热设备", "pf_min": 1.0, "pf_max": 1.0, "phases": ("1",), "source": "19DX101-1", "table": "表3.39", "page": "PDF第38页", "status": "verified"},
    "electric_floor_heating": {"group": "resistive", "name": "电地暖", "pf_min": 1.0, "pf_max": 1.0, "phases": ("1",), "source": "19DX101-1", "table": "表3.41", "page": "PDF第40页", "status": "verified"},
    "led_over_5w": {"group": "lighting", "name": "LED灯（单灯功率＞5W）", "pf_min": 0.9, "pf_max": 1.0, "phases": ("1", "3"), "source": "19DX101-1", "table": "表3.38", "page": "PDF第37页", "status": "verified"},
    "led_up_to_5w": {"group": "lighting", "name": "LED灯（单灯功率≤5W）", "pf_min": 0.5, "pf_max": 1.0, "phases": ("1", "3"), "source": "19DX101-1", "table": "表3.38", "page": "PDF第37页", "status": "verified"},
    "stage_lighting": {"group": "lighting", "name": "舞台照明", "pf_min": 0.9, "pf_max": 1.0, "phases": ("1", "3"), "source": "19DX101-1", "table": "表3.38", "page": "PDF第37页", "status": "verified"},
    "television": {"group": "household", "name": "电视", "pf_min": 0.7, "pf_max": 0.95, "phases": ("1",), "source": "19DX101-1", "table": "表3.39", "page": "PDF第38页", "status": "verified"},
    "refrigerator": {"group": "household", "name": "冰箱", "pf_min": 0.6, "pf_max": 0.95, "phases": ("1",), "source": "19DX101-1", "table": "表3.39", "page": "PDF第38页", "status": "verified"},
    "household_fan": {"group": "household", "name": "风扇 / 暖风机 / 排气扇", "pf_min": 0.8, "pf_max": 0.8, "phases": ("1",), "source": "19DX101-1", "table": "表3.39", "page": "PDF第38页", "status": "verified"},
    "kitchen_resistive": {"group": "kitchen", "name": "蒸饭柜 / 炸炉 / 开水机等电热设备", "pf_min": 1.0, "pf_max": 1.0, "phases": ("1", "3"), "source": "19DX101-1", "table": "表3.40", "page": "PDF第39页", "status": "verified"},
    "kitchen_processing": {"group": "kitchen", "name": "绞肉机 / 菜馅机 / 切片机", "pf_min": 0.8, "pf_max": 0.8, "phases": ("1",), "source": "19DX101-1", "table": "表3.40", "page": "PDF第39页", "status": "verified"},
    "dough_machine": {"group": "kitchen", "name": "和面机 / 馒头机", "pf_min": 0.85, "pf_max": 0.85, "phases": ("1", "3"), "source": "19DX101-1", "table": "表3.40", "page": "PDF第39页", "status": "verified"},
    "split_ac": {"group": "hvac", "name": "分体空调", "pf_min": 0.8, "pf_max": 0.85, "phases": ("1",), "source": "19DX101-1", "table": "表3.41", "page": "PDF第40页", "status": "verified"},
    "fan_coil": {"group": "hvac", "name": "风机盘管 / 多联机室内机", "pf_min": 0.8, "pf_max": 0.85, "phases": ("1",), "source": "19DX101-1", "table": "表3.41", "page": "PDF第40页", "status": "verified"},
    "cold_storage": {"group": "hvac", "name": "冷冻机房 / 锅炉房", "pf_min": 0.8, "pf_max": 0.85, "phases": ("1", "3"), "source": "19DX101-1", "table": "表3.38", "page": "PDF第37页", "status": "verified"},
    "unknown": {"group": "other", "name": "其他 / 不确定", "pf_min": None, "pf_max": None, "phases": ("1", "3"), "source": "", "table": "", "page": "", "status": "pending"},
}

INSTALLATION_SCENARIOS = {
    "BV": (("conduit", "穿管"), ("unknown", "不知道")),
    "YJV": (
        ("tray", "槽盒"),
        ("conduit", "穿管"),
        ("direct_buried", "埋地管槽"),
        ("unknown", "不知道"),
    ),
}

# 结构仅用于明确客户希望采用的成品电缆/单芯线组合及匹配载流量表。
# 断路器极数由接地系统、N线处理及保护要求决定，不得由电缆芯数推断。
CONDUCTOR_CONFIGURATIONS: dict[str, dict[str, Any]] = {
    "bv_1ph_2wire_pe": {"family": "BV", "phases": ("1",), "label": "2根单芯线", "description": "BV单芯线组合；2根载流导线", "ampacity_supported": True},
    "bv_3ph_3wire_pe": {"family": "BV", "phases": ("3",), "label": "3根单芯线", "description": "BV单芯线组合；3根载流导线", "ampacity_supported": True},
    "bv_3ph_4wire_pe": {"family": "BV", "phases": ("3",), "label": "4根单芯线", "description": "BV单芯线组合；3根载流导线", "ampacity_supported": True},
    "yjv_3c_3ph_pe": {"family": "YJV", "phases": ("3",), "label": "三芯电缆", "description": "YJV三芯电力电缆；当前已核实基础载流量", "ampacity_supported": True},
    "yjv_4c_3ph_n_pe": {"family": "YJV", "phases": ("3",), "label": "四芯电缆", "description": "YJV四芯电力电缆；空气中或地下基础载流量已核实", "ampacity_supported": True},
    "yjv_5c_3ph_n_pe": {"family": "YJV", "phases": ("3",), "label": "五芯电缆", "description": "YJV五芯电力电缆；空气中或地下基础载流量已核实", "ampacity_supported": True},
    "yjv_4c_3ph_n_separate_pe": {"family": "YJV", "phases": ("3",), "label": "四芯电缆＋独立PE", "description": "YJV四根等截面L1/L2/L3/N，另设独立PE", "ampacity_supported": True},
}

TRAY_CONFIGURATION_OPTIONS = {
    "horizontal_perforated": "水平有孔槽盒（19DX原表：托盘 / 梯架）",
    "other": "其他槽盒条件（暂未接入对应降额表）",
}

TRANSFORMER_LV_SHORT_CIRCUIT = {
    "status": "verified",
    "source": "19DX101-1",
    "table": "式(15.9)、表15.7",
    "page": "PDF第299、307页",
    "location": "变压器0.4kV低压出口",
    "assumption": "表15.7以上级系统容量无穷大为计算条件",
    "rows": {
        250: {4.0: {"ik_ka": 9.00, "ip_ka": 22.95}, 4.5: {"ik_ka": 8.00, "ip_ka": 20.40}},
        315: {4.0: {"ik_ka": 11.34, "ip_ka": 28.92}, 4.5: {"ik_ka": 10.08, "ip_ka": 25.70}},
        400: {4.0: {"ik_ka": 14.40, "ip_ka": 36.72}, 4.5: {"ik_ka": 12.80, "ip_ka": 32.64}},
        500: {4.0: {"ik_ka": 18.00, "ip_ka": 45.90}, 4.5: {"ik_ka": 16.00, "ip_ka": 40.80}},
        630: {4.0: {"ik_ka": 22.68, "ip_ka": 57.83}, 4.5: {"ik_ka": 20.16, "ip_ka": 51.41}, 6.0: {"ik_ka": 15.12, "ip_ka": 38.56}},
        800: {6.0: {"ik_ka": 19.20, "ip_ka": 48.96}, 7.0: {"ik_ka": 16.48, "ip_ka": 42.02}, 8.0: {"ik_ka": 14.40, "ip_ka": 36.72}},
        1000: {6.0: {"ik_ka": 24.00, "ip_ka": 61.20}, 7.0: {"ik_ka": 20.60, "ip_ka": 52.53}, 8.0: {"ik_ka": 18.00, "ip_ka": 45.90}},
        1250: {6.0: {"ik_ka": 30.00, "ip_ka": 76.50}, 7.0: {"ik_ka": 25.75, "ip_ka": 65.66}, 8.0: {"ik_ka": 22.50, "ip_ka": 57.38}},
        1600: {6.0: {"ik_ka": 38.40, "ip_ka": 97.92}, 7.0: {"ik_ka": 32.96, "ip_ka": 84.05}, 8.0: {"ik_ka": 28.80, "ip_ka": 73.44}},
        2000: {6.0: {"ik_ka": 48.00, "ip_ka": 122.40}, 7.0: {"ik_ka": 41.20, "ip_ka": 105.06}, 8.0: {"ik_ka": 36.00, "ip_ka": 91.80}},
        2500: {6.0: {"ik_ka": 60.00, "ip_ka": 153.00}, 7.0: {"ik_ka": 51.50, "ip_ka": 131.33}, 8.0: {"ik_ka": 45.00, "ip_ka": 114.75}},
    },
}

# 《工业与民用供配电设计手册（第四版）》表4.6-12、表4.6-13，
# PDF第337页（印刷第305页）。数值均为归算至400V侧的相保阻抗平均值。
# 当前只录入已逐项视觉核验的S11-M与SCB11；不插值、不外推到其他系列。
TRANSFORMER_PHASE_PE_IMPEDANCE = {
    "status": "verified",
    "source": "《工业与民用供配电设计手册（第四版）》",
    "document": "工业与民用供配电设计手册（第四版）.pdf",
    "clause": "第4.6.2节",
    "page": "PDF第336～337页（印刷第304～305页）",
    "vector_groups": ("Dyn11",),
    "high_voltage_kv": (6.0, 10.0),
    "low_voltage_v": 400.0,
    "series": {
        "s11_m": {
            "name": "S11-M型油浸式叠铁芯变压器",
            "table": "表4.6-12",
            "rows": {
                200.0: {4.0: {"r_mohm": 10.9, "x_mohm": 29.1, "positive_x_mohm": 30.0, "pk_w": 2730}},
                250.0: {4.0: {"r_mohm": 8.19, "x_mohm": 23.5, "positive_x_mohm": 24.2, "pk_w": 3200}},
                315.0: {4.0: {"r_mohm": 6.18, "x_mohm": 18.78, "positive_x_mohm": 19.36, "pk_w": 3830}},
                400.0: {4.0: {"r_mohm": 4.52, "x_mohm": 14.9, "positive_x_mohm": 15.3, "pk_w": 4520}},
                500.0: {4.0: {"r_mohm": 3.46, "x_mohm": 11.9, "positive_x_mohm": 12.3, "pk_w": 5410}},
                630.0: {4.5: {"r_mohm": 2.50, "x_mohm": 10.8, "positive_x_mohm": 11.1, "pk_w": 6200}},
                800.0: {4.5: {"r_mohm": 1.88, "x_mohm": 8.60, "positive_x_mohm": 8.80, "pk_w": 7500}},
                1000.0: {4.5: {"r_mohm": 1.65, "x_mohm": 6.87, "positive_x_mohm": 7.01, "pk_w": 10300}},
                1250.0: {4.5: {"r_mohm": 1.23, "x_mohm": 5.53, "positive_x_mohm": 5.63, "pk_w": 12000}},
                1600.0: {4.5: {"r_mohm": 0.91, "x_mohm": 4.35, "positive_x_mohm": 4.41, "pk_w": 14500}},
            },
        },
        "scb11": {
            "name": "SCB11型环氧树脂浇注干式变压器",
            "table": "表4.6-13",
            "rows": {
                200.0: {4.0: {"r_mohm": 10.12, "x_mohm": 29.4, "positive_x_mohm": 30.36, "pk_w": 2530}},
                250.0: {4.0: {"r_mohm": 7.07, "x_mohm": 23.8, "positive_x_mohm": 24.6, "pk_w": 2760}},
                315.0: {4.0: {"r_mohm": 5.60, "x_mohm": 18.9, "positive_x_mohm": 19.5, "pk_w": 3470}},
                400.0: {4.0: {"r_mohm": 3.99, "x_mohm": 15.0, "positive_x_mohm": 15.4, "pk_w": 3990}},
                500.0: {4.0: {"r_mohm": 3.12, "x_mohm": 12.0, "positive_x_mohm": 12.4, "pk_w": 4830}},
                630.0: {
                    4.0: {"r_mohm": 2.37, "x_mohm": 9.6, "positive_x_mohm": 9.8, "pk_w": 5880},
                    6.0: {"r_mohm": 2.40, "x_mohm": 14.6, "positive_x_mohm": 15.0, "pk_w": 5960},
                },
                800.0: {6.0: {"r_mohm": 1.74, "x_mohm": 11.6, "positive_x_mohm": 11.8, "pk_w": 6960}},
                1000.0: {6.0: {"r_mohm": 1.30, "x_mohm": 9.3, "positive_x_mohm": 9.5, "pk_w": 8130}},
                1250.0: {6.0: {"r_mohm": 0.90, "x_mohm": 7.4, "positive_x_mohm": 7.6, "pk_w": 9690}},
                1600.0: {
                    6.0: {"r_mohm": 0.73, "x_mohm": 5.88, "positive_x_mohm": 5.96, "pk_w": 11730},
                    8.0: {"r_mohm": 0.81, "x_mohm": 7.86, "positive_x_mohm": 7.96, "pk_w": 12960},
                },
                2000.0: {
                    6.0: {"r_mohm": 0.58, "x_mohm": 4.73, "positive_x_mohm": 4.77, "pk_w": 14450},
                    8.0: {"r_mohm": 0.64, "x_mohm": 6.32, "positive_x_mohm": 6.37, "pk_w": 15960},
                },
                2500.0: {
                    6.0: {"r_mohm": 0.44, "x_mohm": 3.81, "positive_x_mohm": 3.81, "pk_w": 17170},
                    8.0: {"r_mohm": 0.48, "x_mohm": 5.10, "positive_x_mohm": 5.10, "pk_w": 18890},
                },
            },
        },
    },
}

# 《工业与民用供配电设计手册（第四版）》表4.2-46，
# PDF第245页（印刷第213页）。表列数据为50Hz、土壤电阻率100Ω·m条件下
# 的理论计算数据。这里只采用“电流回路通过N导体”的Z'(0)N列，不采用
# 将大地并联回路计入的Z'(0)NE列；第四芯用于PE时按相同导体几何换算。
YJV_FOUR_CORE_SEQUENCE_IMPEDANCE = {
    "status": "verified",
    "source": "《工业与民用供配电设计手册（第四版）》",
    "document": "工业与民用供配电设计手册（第四版）.pdf",
    "table": "表4.2-46",
    "page": "PDF第245页（印刷第213页）",
    "formula": "式(4.6-44)～式(4.6-46)",
    "formula_page": "PDF第340页（印刷第308页）",
    "calculation_condition_page": "PDF第335页（印刷第303页）",
    "frequency_hz": 50.0,
    "minimum_fault_resistance_multiplier": 1.5,
    "rows": {
        10.0: {"protective_section_mm2": 6.0, "r1": 1.8576, "x1": 0.0830, "r0n": 11.1456, "x0n": 0.3920},
        16.0: {"protective_section_mm2": 10.0, "r1": 1.1810, "x1": 0.0799, "r0n": 6.7338, "x0n": 0.3656},
        25.0: {"protective_section_mm2": 16.0, "r1": 0.7430, "x1": 0.0799, "r0n": 4.2260, "x0n": 0.3602},
        35.0: {"protective_section_mm2": 16.0, "r1": 0.5307, "x1": 0.0763, "r0n": 4.0137, "x0n": 0.3702},
        50.0: {"protective_section_mm2": 25.0, "r1": 0.3715, "x1": 0.0762, "r0n": 2.6005, "x0n": 0.3613},
        70.0: {"protective_section_mm2": 35.0, "r1": 0.2654, "x1": 0.0750, "r0n": 1.8575, "x0n": 0.3572},
        95.0: {"protective_section_mm2": 50.0, "r1": 0.1955, "x1": 0.0735, "r0n": 1.3100, "x0n": 0.3518},
        120.0: {"protective_section_mm2": 70.0, "r1": 0.1548, "x1": 0.0724, "r0n": 0.9510, "x0n": 0.3503},
        150.0: {"protective_section_mm2": 70.0, "r1": 0.1238, "x1": 0.0722, "r0n": 0.9200, "x0n": 0.3606},
        185.0: {"protective_section_mm2": 95.0, "r1": 0.1004, "x1": 0.0723, "r0n": 0.6869, "x0n": 0.3550},
        240.0: {"protective_section_mm2": 120.0, "r1": 0.0774, "x1": 0.0716, "r0n": 0.5418, "x0n": 0.3529},
        300.0: {"protective_section_mm2": 150.0, "r1": 0.0619, "x1": 0.0716, "r0n": 0.4333, "x0n": 0.3528},
    },
}

BV_SECTIONS = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300]
YJV_THREE_CORE_SECTIONS = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300]
YJV_MULTICORE_SECTIONS = [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300]
YJV_MULTICORE_COPPER_AIR_40C = [20, 27, 35, 45, 63, 84, 113, 139, 161, 204, 252, 291, 333, 385, 457, 527]
YJV_MULTICORE_COPPER_GROUND_25C = [31, 41, 53, 66, 90, 117, 151, 181, 210, 257, 310, 351, 393, 445, 516, 583]
YJV_THREE_CORE_BURIED_DUCT_20C: dict[float, list[float]] = {
    1.0: [24, 33, 42, 51, 68, 88, 113, 135, 159, 197, 232, 263, 296, 331, 382, 430],
    1.5: [23, 30, 39, 48, 63, 82, 105, 126, 148, 183, 216, 245, 276, 309, 356, 401],
    2.0: [22, 29, 37, 46, 60, 78, 100, 120, 141, 175, 206, 234, 263, 295, 340, 383],
    2.5: [21, 28, 36, 44, 58, 75, 96, 115, 135, 167, 197, 223, 251, 281, 324, 365],
}

# 人民电器《电线电缆选型手册》0.6/1kV YJV结构表，PDF第35～37页。
# 当前只录入表中未以括号标注的圆形导体范围；扇形导体不得套用圆形线芯几何公式。
YJV_ROUND_CONDUCTOR_GEOMETRY: dict[float, dict[str, float]] = {
    1.5: {"conductor_diameter_mm": 1.38, "insulation_thickness_mm": 0.7},
    2.5: {"conductor_diameter_mm": 1.78, "insulation_thickness_mm": 0.7},
    4: {"conductor_diameter_mm": 2.25, "insulation_thickness_mm": 0.7},
    6: {"conductor_diameter_mm": 2.76, "insulation_thickness_mm": 0.7},
    10: {"conductor_diameter_mm": 4.05, "insulation_thickness_mm": 0.7},
    16: {"conductor_diameter_mm": 5.10, "insulation_thickness_mm": 0.7},
    25: {"conductor_diameter_mm": 6.0, "insulation_thickness_mm": 0.9},
    35: {"conductor_diameter_mm": 7.0, "insulation_thickness_mm": 0.9},
}

YJV_REDUCED_PROTECTIVE_SECTIONS: dict[float, float] = {
    4: 2.5,
    6: 4,
    10: 6,
    16: 10,
    25: 16,
    35: 16,
}

YJV_FAULT_LOOP_STRUCTURE = {
    "status": "verified",
    "source": "人民电器《电线电缆选型手册》",
    "document": "电线电缆造型手册.pdf",
    "voltage": "0.6/1kV",
    "tables": {
        "conductor_geometry": "表1",
        "yjv_3plus1": "表4",
        "yjv_3plus2": "表6",
    },
    "pages": {
        "conductor_geometry": "PDF第35页（印刷第33页）",
        "yjv_3plus1": "PDF第36页（印刷第34页）",
        "yjv_3plus2": "PDF第37页（印刷第35页）",
    },
    "configuration_profiles": {
        "yjv_4c_3ph_n_pe": "yjv_3plus1",
        "yjv_5c_3ph_n_pe": "yjv_3plus2",
    },
}


def _ampacity_rows(sections: list[float], ampacities: list[float]) -> list[dict[str, float]]:
    return [
        {"section_mm2": section, "ampacity_a": ampacity}
        for section, ampacity in zip(sections, ampacities, strict=True)
    ]


def _impedance_rows(
    sections: list[float],
    resistances: list[float],
    reactances: list[float],
) -> list[dict[str, float]]:
    return [
        {
            "section_mm2": section,
            "resistance_ohm_per_km": resistance,
            "reactance_ohm_per_km": reactance,
        }
        for section, resistance, reactance in zip(
            sections, resistances, reactances, strict=True
        )
    ]


VOLTAGE_DROP_LIMITS = {
    "status": "verified",
    "source": "《工业与民用供配电设计手册（第四版）》",
    "document": "工业与民用供配电设计手册（第四版）.pdf",
    "clause": "第6.2.4节",
    "table": "表6.2-6",
    "page": "PDF第497页（印刷第465页）",
    "boundary": "从配电变压器二次侧母线算起",
    "profiles": {
        "low_voltage": {
            "name": "低压线路",
            "table_value": "5",
            "limit_pct": 5.0,
        },
        "lighting_low_voltage": {
            "name": "供给有照明负荷的低压线路",
            "table_value": "3～5",
            "limit_pct": 3.0,
            "selection_note": "平台映射（非表格原文）：表列范围为3%～5%，快速页按下限3%作保守暂算。",
        },
    },
}

YJV_VOLTAGE_DROP_IMPEDANCE = _impedance_rows(
    [4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240],
    [5.332, 3.554, 2.175, 1.359, 0.870, 0.622, 0.435, 0.310, 0.229, 0.181, 0.145, 0.118, 0.091],
    [0.097, 0.092, 0.085, 0.082, 0.082, 0.080, 0.080, 0.078, 0.077, 0.077, 0.077, 0.077, 0.077],
)

BUSWAY_PHASE_PE_IMPEDANCE: dict[str, Any] = {
    "status": "verified",
    "source_rule_code": "ELEC.BUSWAY.CANALIS.PHASE_PE.IMPEDANCE",
    "series": {
        "canalis_ks_casing_pe": {
            "name": "Canalis KS（外壳作PE）",
            "document": "Schneider_Canalis_Low_Voltage_DEBU034EN.pdf",
            "document_reference": "DEBU034EN",
            "page": "PDF第66页（印刷第64页）",
            "heading": "Electrical characteristics — From 160 to 800 A — Fault loop characteristics",
            "condition": "Impedance method；At Inc and at 35°C；50 Hz；Ph/PE",
            "rows": {
                160: {"r": 1.69, "x": 0.88},
                250: {"r": 1.23, "x": 0.76},
                400: {"r": 1.12, "x": 0.67},
                630: {"r": 0.27, "x": 0.51},
                800: {"r": 0.24, "x": 0.48},
            },
        },
        "canalis_kta_casing_pe": {
            "name": "Canalis KTA（外壳作PE）",
            "document": "Schneider_Canalis_KTA_DEBU021EN.pdf",
            "document_reference": "DEBU021EN",
            "page": "PDF第146～147页（印刷第144～145页）",
            "heading": "Characteristics — Canalis KTA 3L + PE / 3L + N + PE — Fault loop characteristics",
            "condition": "Impedance method；At Inc and at 35°C；50 Hz；Ph/PE",
            "rows": {
                800: {"r": 0.706, "x": 0.426},
                1000: {"r": 0.595, "x": 0.329},
                1250: {"r": 0.490, "x": 0.275},
                1600: {"r": 0.394, "x": 0.212},
                2000: {"r": 0.333, "x": 0.170},
                2500: {"r": 0.290, "x": 0.141},
                3200: {"r": 0.229, "x": 0.106},
                4000: {"r": 0.188, "x": 0.084},
                5000: {"r": 0.145, "x": 0.071},
            },
        },
        "canalis_kta_internal_al_pe": {
            "name": "Canalis KTA PER（内部铝PE）",
            "document": "Schneider_Canalis_KTA_DEBU021EN.pdf",
            "document_reference": "DEBU021EN",
            "page": "PDF第148页（印刷第146页）",
            "heading": "Characteristics — Canalis KTA 3L + N + PER — With an internal aluminium PE conductor",
            "condition": "Impedance method；At Inc and at 35°C；50 Hz；Ph/PE",
            "rows": {
                800: {"r": 0.221, "x": 0.095},
                1000: {"r": 0.160, "x": 0.069},
                1250: {"r": 0.137, "x": 0.058},
                1600: {"r": 0.106, "x": 0.044},
                2000: {"r": 0.087, "x": 0.036},
                2500: {"r": 0.078, "x": 0.031},
                3200: {"r": 0.058, "x": 0.023},
                4000: {"r": 0.037, "x": 0.019},
                5000: {"r": 0.040, "x": 0.016},
            },
        },
        "canalis_kta_internal_cu_pe": {
            "name": "Canalis KTA PER（内部铜PE）",
            "document": "Schneider_Canalis_KTA_DEBU021EN.pdf",
            "document_reference": "DEBU021EN",
            "page": "PDF第149页（印刷第147页）",
            "heading": "Characteristics — Canalis KTA 3L + N + PER — With an internal copper PE conductor",
            "condition": "Impedance method；At Inc and at 35°C；50 Hz；Ph/PE",
            "rows": {
                800: {"r": 0.166, "x": 0.047},
                1000: {"r": 0.123, "x": 0.037},
                1250: {"r": 0.105, "x": 0.032},
                1600: {"r": 0.080, "x": 0.026},
                2000: {"r": 0.067, "x": 0.022},
                2500: {"r": 0.057, "x": 0.019},
                3200: {"r": 0.042, "x": 0.014},
                4000: {"r": 0.036, "x": 0.012},
                5000: {"r": 0.029, "x": 0.010},
            },
        },
    },
}

# 同一KTA 3L+N+PE特性表的完整运行、短路和故障参数。所有电阻/电抗
# 原表单位mΩ/m，与平台Ω/km数值相同；只允许精确额定电流行，不插值。
CANALIS_KTA_3LNPE_ELECTRICAL: dict[str, Any] = {
    "status": "verified",
    "source_rule_code": "ELEC.BUSWAY.CANALIS.KTA.3LNPE.ELECTRICAL",
    "series_code": "canalis_kta_3lnpe",
    "series_name": "Canalis KTA 3L+N+PE（外壳作PE）",
    "document": "Schneider_Canalis_KTA_DEBU021EN.pdf",
    "document_reference": "DEBU021EN",
    "page": "PDF第147页（印刷第145页）",
    "condition": "At Inc and at 35°C；50 Hz；standard version 3L+N+PE",
    "rated_voltage_v": 1000.0,
    "temperature_factors": {35: 1.0, 40: 0.97, 45: 0.93, 50: 0.90, 55: 0.86},
    "rows": {
        800: {"r1": 0.096, "x1": 0.018, "rn": 0.194, "xn": 0.064, "rpe": 0.706, "xpe": 0.426, "icw_1s": 31},
        1000: {"r1": 0.069, "x1": 0.016, "rn": 0.140, "xn": 0.047, "rpe": 0.595, "xpe": 0.329, "icw_1s": 50},
        1250: {"r1": 0.056, "x1": 0.015, "rn": 0.120, "xn": 0.040, "rpe": 0.490, "xpe": 0.275, "icw_1s": 50},
        1600: {"r1": 0.042, "x1": 0.013, "rn": 0.092, "xn": 0.030, "rpe": 0.394, "xpe": 0.212, "icw_1s": 65},
        2000: {"r1": 0.034, "x1": 0.011, "rn": 0.075, "xn": 0.024, "rpe": 0.333, "xpe": 0.170, "icw_1s": 70},
        2500: {"r1": 0.028, "x1": 0.008, "rn": 0.066, "xn": 0.021, "rpe": 0.290, "xpe": 0.141, "icw_1s": 80},
        3200: {"r1": 0.021, "x1": 0.007, "rn": 0.049, "xn": 0.016, "rpe": 0.229, "xpe": 0.106, "icw_1s": 86},
        4000: {"r1": 0.017, "x1": 0.007, "rn": 0.039, "xn": 0.013, "rpe": 0.188, "xpe": 0.084, "icw_1s": 90},
        5000: {"r1": 0.014, "x1": 0.004, "rn": 0.033, "xn": 0.011, "rpe": 0.145, "xpe": 0.071, "icw_1s": 120},
    },
}


DEFAULT_CATALOG: dict[str, Any] = {
    "load_types": LOAD_TYPES,
    "transformer_lv_short_circuit": TRANSFORMER_LV_SHORT_CIRCUIT,
    "transformer_phase_pe_impedance": TRANSFORMER_PHASE_PE_IMPEDANCE,
    "yjv_four_core_sequence_impedance": YJV_FOUR_CORE_SEQUENCE_IMPEDANCE,
    "fault_loop_structure": {"YJV": YJV_FAULT_LOOP_STRUCTURE},
    "busway_phase_pe_impedance": BUSWAY_PHASE_PE_IMPEDANCE,
    "canalis_kta_3lnpe_electrical": CANALIS_KTA_3LNPE_ELECTRICAL,
    # 未找到适用于普通负荷的完整断路器产品目录前，不枚举规格。
    # 《施耐德照明电路选择与设计技术手册》PDF第11页仅给出照明回路经验值
    # 10/16/20A，适用范围过窄，不能作为这里的通用目录。
    "breaker_ratings": {"status": "pending", "ratings_a": []},
    # 19DX101-1 表5.11～表5.13是通用技术参数参考，不绑定制造商或型号。
    # 表内数据仅形成设计参数候选；正式选用仍需完成短路、脱扣和选择性校核。
    "breaker_parameters": {
        "status": "verified",
        "source": "19DX101-1",
        "page": "PDF第77～79页",
        "families": {
            "MCB": {
                "name": "微型断路器（MCB）",
                "table": "表5.13",
                "page": "PDF第79页",
                "rated_voltage_v": "230/400",
                "groups": [
                    {"frame_a": 32, "ratings_a": [6, 10, 16, 25, 32], "icu_ka": [4.5], "icu_ics_pairs_ka": [(4.5, 4.5)], "icw_1s_status": "not_applicable", "pole_options": ["1P", "1P+N"]},
                    {"frame_a": 63, "ratings_a": [6, 10, 16, 20, 25, 32, 40, 50, 63], "icu_ka": [4.5, 6, 10], "icu_ics_pairs_ka": [(4.5, 4.5), (6, 6), (10, 7.5), (10, 10)], "icw_1s_status": "not_applicable", "pole_options": ["1P", "2P", "3P", "4P"]},
                    {"frame_a": 125, "ratings_a": [63, 80, 100, 125], "icu_ka": [10, 15], "icu_ics_pairs_ka": [(10, 10), (15, 15)], "icw_1s_status": "not_applicable", "pole_options": ["1P", "2P", "3P", "4P"]},
                ],
            },
            "MCCB": {
                "name": "塑壳断路器（MCCB）",
                "table": "表5.12",
                "page": "PDF第78页",
                "rated_voltage_v": "400",
                "groups": [
                    {"frame_a": 100, "ratings_a": [16, 25, 32, 40, 50, 63, 80, 100], "icu_ka": [25, 35, 70, 150], "ics_percent_icu": [50, 70, 100], "usage_categories": ["A"], "icw_1s_status": "not_applicable", "pole_options": ["3P", "4P"]},
                    {"frame_a": 160, "ratings_a": [32, 40, 50, 63, 80, 100, 125, 160], "icu_ka": [25, 35, 70, 150], "ics_percent_icu": [50, 70, 100], "usage_categories": ["A"], "icw_1s_status": "not_applicable", "pole_options": ["3P", "4P"]},
                    {"frame_a": 250, "ratings_a": [63, 80, 100, 125, 160, 200, 250], "icu_ka": [25, 35, 70, 150], "ics_percent_icu": [50, 70, 100], "usage_categories": ["A"], "icw_1s_status": "not_applicable", "pole_options": ["3P", "4P"]},
                    {"frame_a": 400, "ratings_a": [250, 400], "icu_ka": [35, 70, 150], "ics_percent_icu": [50, 70, 100], "usage_categories": ["A", "B"], "icw_1s_ka": 5, "icw_1s_status": "listed", "pole_options": ["3P", "4P"]},
                    {"frame_a": 630, "ratings_a": [250, 400, 630], "icu_ka": [35, 70, 150], "ics_percent_icu": [50, 70, 100], "usage_categories": ["A", "B"], "icw_1s_ka": 8, "icw_1s_status": "listed", "pole_options": ["3P", "4P"]},
                ],
            },
            "ACB": {
                "name": "框架断路器（ACB）",
                "table": "表5.11",
                "page": "PDF第77页",
                "rated_voltage_v": "400",
                "groups": [
                    {"frame_a": 1600, "ratings_a": [800, 1000, 1250, 1600], "icu_ka": [65, 85, 100], "ics_percent_icu": [85, 100], "icw_1s_status": "not_tabulated", "pole_options": ["3P", "4P"]},
                    {"frame_a": 2000, "ratings_a": [1600, 2000], "icu_ka": [65, 85, 100], "ics_percent_icu": [100], "icw_1s_status": "not_tabulated", "pole_options": ["3P", "4P"]},
                    {"frame_a": 4000, "ratings_a": [2000, 2500, 3200, 4000], "icu_ka": [65, 85, 100], "ics_percent_icu": [100], "icw_1s_status": "not_tabulated", "pole_options": ["3P", "4P"]},
                    {"frame_a": 6300, "ratings_a": [4000, 5000, 6300], "icu_ka": [100, 150], "ics_percent_icu": [100], "icw_1s_status": "not_tabulated", "pole_options": ["3P", "4P"]},
                ],
            },
        },
    },
    "rcd_parameters": {
        "status": "verified",
        "source": "19DX101-1",
        "table": "表5.6",
        "page": "PDF第72页",
        "scenarios": {
            "additional_30ma": {
                "name": "附加保护：手持/移动设备、室外、家用电器或插座等回路",
                "rated_residual_current_max_ma": 30,
                "delay": "无延时",
            },
            "fire_300ma": {
                "name": "持续接地故障引起火灾危险的防护",
                "rated_residual_current_max_ma": 300,
                "delay": "需按上下级选择性确定",
            },
        },
        "waveform_types": {
            "ac": {"label": "仅交流剩余电流", "rcd_type": "AC型"},
            "pulsating_dc": {"label": "交流及脉动直流剩余电流", "rcd_type": "A型或F型"},
            "smooth_dc": {"label": "含平滑直流剩余电流", "rcd_type": "B型"},
        },
        "selection_checks": [
            "应断开被保护回路的所有带电导体。",
            "PE线不应穿过RCD磁回路。",
            "额定剩余动作电流应大于正常泄漏电流的2倍，一般为2.5～4倍。",
            "有上下级RCD时，上级额定剩余动作电流应不小于下级的3倍；上级应选延时型，并留有足够时间级差。",
        ],
    },
    "conductors": {
        "BV": {
            "status": "verified",
            "source": "19DX101-1", "table": "表6.1", "page": "PDF第84页",
            "reference_condition": "环境温度30℃；BV绝缘电线敷设在明敷导管内",
            "base_temperature_c": 30,
            "insulation_code": "PVC",
            "scenarios": {
                "conduit": {
                    "bv_loaded_2": {"label": "2根载流导线", "rows": _ampacity_rows(BV_SECTIONS, [17, 24, 32, 41, 57, 76, 101, 125, 151, 192, 232, 269, 300, 341, 400, 458])},
                    "bv_loaded_3": {"label": "3根载流导线", "rows": _ampacity_rows(BV_SECTIONS, [15, 21, 28, 36, 50, 68, 89, 110, 134, 171, 207, 239, 262, 296, 346, 394])},
                    "bv_loaded_4": {"label": "4根载流导线", "rows": _ampacity_rows(BV_SECTIONS, [13, 19, 25, 32, 45, 60, 80, 100, 120, 153, 185, 215, 240, 272, 320, 366])},
                    "bv_loaded_5_6": {"label": "5～6根载流导线", "rows": _ampacity_rows(BV_SECTIONS, [11, 16, 22, 28, 39, 53, 70, 87, 105, 134, 162, 188, 210, 238, 280, 320])},
                },
            },
        },
        "YJV": {
            "status": "verified",
            "source": "19DX101-1", "table": "表6.10", "page": "PDF第92～93页",
            "reference_condition": "YJV铜芯、三芯、0.6/1kV、导体工作温度90℃；具体环境条件随敷设方式确定",
            "base_temperature_c": 30,
            "insulation_code": "XLPE_EPR",
            "scenarios": {
                "conduit": {
                    "yjv_three_core_exposed_conduit": {"label": "三芯电缆，明敷导管内", "rows": _ampacity_rows(YJV_THREE_CORE_SECTIONS, [19, 26, 35, 44, 60, 80, 105, 128, 154, 194, 233, 268, 300, 340, 398, 455])},
                },
                "tray": {
                    "yjv_three_core_in_air": {"label": "三芯电缆，空气中敷设（槽盒条件仍需确认）", "rows": _ampacity_rows(YJV_THREE_CORE_SECTIONS, [23, 32, 42, 54, 75, 100, 127, 158, 192, 246, 298, 346, 399, 456, 538, 621])},
                    "yjv_multicore_in_air_people": {
                        "label": "四芯/五芯电缆，空气中敷设",
                        "rows": _ampacity_rows(YJV_MULTICORE_SECTIONS, YJV_MULTICORE_COPPER_AIR_40C),
                        "source": "人民电器《电线电缆选型手册》",
                        "table": "表31",
                        "page": "PDF第50页（印刷第48页）",
                        "reference_condition": "0.6/1kV铜芯多芯电缆；空气中敷设；环境温度40℃",
                        "base_temperature_c": 40,
                        "rule_code": "ELEC.CABLE.YJV.MULTICORE.AMPACITY",
                    },
                },
                "direct_buried": {
                    **{
                        f"yjv_three_core_buried_duct_soil_{resistivity:g}": {
                            "label": (
                                "三芯电缆，埋地管槽内；"
                                f"土壤热阻系数{resistivity:g} K·m/W"
                            ),
                            "rows": _ampacity_rows(
                                YJV_THREE_CORE_SECTIONS,
                                ampacities,
                            ),
                            "source": "19DX101-1",
                            "table": "表6.10",
                            "page": "PDF第93页",
                            "reference_condition": (
                                "YJV铜芯三芯电缆；敷设在埋地的管槽内；"
                                f"土壤热阻系数{resistivity:g} K·m/W；"
                                "环境温度20℃；导体工作温度90℃"
                            ),
                            "base_temperature_c": 20,
                        }
                        for resistivity, ampacities
                        in YJV_THREE_CORE_BURIED_DUCT_20C.items()
                    },
                    "yjv_multicore_in_ground_people": {
                        "label": "四芯/五芯电缆，地下敷设",
                        "rows": _ampacity_rows(YJV_MULTICORE_SECTIONS, YJV_MULTICORE_COPPER_GROUND_25C),
                        "source": "人民电器《电线电缆选型手册》",
                        "table": "表31",
                        "page": "PDF第50页（印刷第48页）",
                        "reference_condition": "0.6/1kV铜芯多芯电缆；地下敷设；环境温度25℃",
                        "base_temperature_c": 25,
                        "rule_code": "ELEC.CABLE.YJV.MULTICORE.AMPACITY",
                    },
                },
            },
        },
    },
    "temperature_derating": {
        "status": "verified",
        "source": "19DX101-1",
        "page": "PDF第106页",
        "air": {
            "table": "表6.22",
            "title": "表6.22 环境空气温度不同于30℃时的校正系数（用于敷设在空气中的电缆载流量）",
            "reference_temperature_c": 30,
            "factors": {
                "PVC": {
                    10: 1.22, 15: 1.17, 20: 1.12, 25: 1.06, 30: 1.00,
                    35: 0.94, 40: 0.87, 45: 0.79, 50: 0.71, 55: 0.61,
                    60: 0.50,
                },
                "XLPE_EPR": {
                    10: 1.15, 15: 1.12, 20: 1.08, 25: 1.04, 30: 1.00,
                    35: 0.96, 40: 0.91, 45: 0.87, 50: 0.82, 55: 0.76,
                    60: 0.71, 65: 0.65, 70: 0.58, 75: 0.50, 80: 0.41,
                },
            },
        },
        "ground": {
            "table": "表6.24",
            "title": "表6.24 地下温度不同于20℃时的校正系数（用于埋地管槽中的电缆载流量）",
            "reference_temperature_c": 20,
            "factors": {
                "PVC": {
                    10: 1.10, 15: 1.05, 20: 1.00, 25: 0.95, 30: 0.89,
                    35: 0.84, 40: 0.77, 45: 0.71, 50: 0.63, 55: 0.55,
                    60: 0.45,
                },
                "XLPE_EPR": {
                    10: 1.07, 15: 1.04, 20: 1.00, 25: 0.96, 30: 0.93,
                    35: 0.89, 40: 0.85, 45: 0.80, 50: 0.76, 55: 0.71,
                    60: 0.65, 65: 0.60, 70: 0.53, 75: 0.46, 80: 0.38,
                },
            },
        },
    },
    "tray_derating": {
        "status": "verified",
        "source": "19DX101-1",
        "table": "表6.25",
        "page": "PDF第107页",
        "title": "表6.25 敷设在自由空气中多根多芯线缆束的降低系数",
        "horizontal_perforated": {
            "1": {"1": 1.00, "2": 0.88, "3": 0.82, "4": 0.79, "6": 0.76, "9": 0.73},
            "2": {"1": 1.00, "2": 0.87, "3": 0.80, "4": 0.77, "6": 0.73, "9": 0.68},
            "3": {"1": 1.00, "2": 0.86, "3": 0.79, "4": 0.76, "6": 0.71, "9": 0.66},
            "6": {"1": 1.00, "2": 0.84, "3": 0.77, "4": 0.73, "6": 0.68, "9": 0.64},
        },
    },
    "enclosed_grouping": {
        "status": "verified",
        "source": "19DX101-1",
        "table": "表6.26",
        "page": "PDF第107页",
        "title": "表6.26 多回路或多根电缆成束敷设的降低系数",
        "arrangement": "成束敷设在空气中、沿槽、嵌入或封闭式敷设（电缆相互接触）",
        "application_note": "表注：这些系数适用于尺寸和负荷相同的线缆束。",
        "factors": {
            1: 1.00, 2: 0.80, 3: 0.70, 4: 0.65, 5: 0.60, 6: 0.57,
            7: 0.54, 8: 0.52, 9: 0.50, 12: 0.45, 16: 0.41, 20: 0.38,
        },
    },
    "buried_duct_grouping": {
        "status": "verified",
        "source": "19DX101-1",
        "table": "表6.27",
        "page": "PDF第108页",
        "title": "表6.27 敷设在埋地管槽内多回路电缆的降低系数",
        "reference_condition": "埋地深度0.7m、土壤热阻系数2.5 K·m/W；表注说明在有些情况下误差会达到±10%",
        "factors": {
            "touching": {
                2: 0.85, 3: 0.75, 4: 0.70, 5: 0.65, 6: 0.60,
                7: 0.57, 8: 0.54, 9: 0.52, 10: 0.49, 11: 0.47,
                12: 0.45, 13: 0.44, 14: 0.42, 15: 0.41, 16: 0.39,
                17: 0.38, 18: 0.37, 19: 0.35, 20: 0.34,
            },
            "0.25": {
                2: 0.90, 3: 0.85, 4: 0.80, 5: 0.80, 6: 0.80,
                7: 0.76, 8: 0.74, 9: 0.73, 10: 0.72, 11: 0.70,
                12: 0.69, 13: 0.68, 14: 0.68, 15: 0.67, 16: 0.66,
                17: 0.65, 18: 0.65, 19: 0.64, 20: 0.63,
            },
            "0.5": {
                2: 0.95, 3: 0.90, 4: 0.85, 5: 0.85, 6: 0.80,
                7: 0.80, 8: 0.78, 9: 0.77, 10: 0.76, 11: 0.75,
                12: 0.74, 13: 0.73, 14: 0.72, 15: 0.72, 16: 0.71,
                17: 0.70, 18: 0.70, 19: 0.69, 20: 0.68,
            },
            "1.0": {
                2: 0.95, 3: 0.95, 4: 0.90, 5: 0.90, 6: 0.90,
                7: 0.88, 8: 0.88, 9: 0.87, 10: 0.86, 11: 0.86,
                12: 0.85, 13: 0.85, 14: 0.84, 15: 0.84, 16: 0.83,
                17: 0.83, 18: 0.83, 19: 0.82, 20: 0.82,
            },
        },
    },
    "voltage_drop_limits": VOLTAGE_DROP_LIMITS,
    "voltage_drop_impedance": {
        "BV": {
            "status": "verified",
            "source": "19DX101-1",
            "tables": {
                "1": {
                    "table": "表3.24",
                    "title": "表3.24 单相交流220V及直流聚氯乙烯绝缘铜芯电线的电压降［%/(A·km)］",
                    "page": "PDF第30页",
                },
                "3": {
                    "table": "表3.23",
                    "title": "表3.23 三相380V铜芯导线的电压降［%/(A·km)］",
                    "page": "PDF第29页",
                },
            },
            "scenarios": {
                "conduit": _impedance_rows(
                    [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240],
                    [13.933, 8.360, 5.172, 3.467, 2.040, 1.248, 0.805, 0.579, 0.398, 0.291, 0.217, 0.171, 0.137, 0.112, 0.086],
                    [0.138, 0.127, 0.119, 0.112, 0.108, 0.102, 0.099, 0.095, 0.091, 0.089, 0.088, 0.083, 0.082, 0.082, 0.080],
                ),
            },
        },
        "YJV": {
            "status": "verified",
            "source": "19DX101-1",
            "tables": {
                "1": {
                    "table": "表3.21",
                    "title": "表3.21 1kV交联聚乙烯绝缘电力电缆用于三相380V系统的电压降［%/(A·km)］",
                    "page": "PDF第27页",
                    "application_note": "表列标题适用于三相380V系统；单相时仅采用表列R/X参数并按单相回路公式暂算。",
                },
                "3": {
                    "table": "表3.21",
                    "title": "表3.21 1kV交联聚乙烯绝缘电力电缆用于三相380V系统的电压降［%/(A·km)］",
                    "page": "PDF第27页",
                    "application_note": "平台适用映射（非表格原文）：表3.21未按敷设方式区分R/X，埋地管槽回路暂采用同一表列电缆R/X计算电压降；载流量仍按表6.10的埋地管槽栏选取。",
                },
            },
            "scenarios": {
                "conduit": YJV_VOLTAGE_DROP_IMPEDANCE,
                "tray": YJV_VOLTAGE_DROP_IMPEDANCE,
                "direct_buried": YJV_VOLTAGE_DROP_IMPEDANCE,
            },
        },
    },
}


def grouped_load_types() -> list[dict[str, Any]]:
    result = []
    for code, label in LOAD_TYPE_GROUPS:
        result.append({
            "code": code,
            "label": label,
            "items": [{"code": key, **value} for key, value in LOAD_TYPES.items() if value["group"] == code],
        })
    return result

def conductor_basis_options() -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for family, conductor in DEFAULT_CATALOG["conductors"].items():
        for scenario, bases in conductor.get("scenarios", {}).items():
            for code, basis in bases.items():
                options.append({
                    "code": code,
                    "label": basis["label"],
                    "family": family,
                    "scenario": scenario,
                })
    return options


def resolve_conductor_ampacity_basis(
    family: str,
    scenario: str,
    phase: str,
    configuration_code: str,
    catalog: dict[str, Any] | None = None,
    soil_thermal_resistivity_k_m_per_w: float | None = None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Match one conductor configuration to an exact ampacity table condition."""

    catalog = catalog or DEFAULT_CATALOG
    configuration = CONDUCTOR_CONFIGURATIONS.get(configuration_code)
    conductor = catalog.get("conductors", {}).get(family, {})
    scenario_bases = conductor.get("scenarios", {}).get(scenario, {})
    if not configuration or configuration.get("family") != family:
        return None, None, None
    if phase not in configuration.get("phases", ()):
        return None, None, None
    if family == "BV" and scenario == "conduit":
        code = "bv_loaded_2" if phase == "1" else "bv_loaded_3"
        note = (
            "普通单相回路按2根载流导线查表。"
            if phase == "1"
            else "普通三相平衡回路按3根载流导线查表；中性线谐波或明显不平衡时需另行确认。"
        )
        return code, scenario_bases.get(code), note
    if family == "YJV" and configuration.get("ampacity_supported"):
        core_label = configuration.get("label")
        if core_label == "三芯电缆" and scenario in {"conduit", "tray"}:
            code = (
                "yjv_three_core_exposed_conduit"
                if scenario == "conduit"
                else "yjv_three_core_in_air"
            )
            return (
                code,
                scenario_bases.get(code),
                "当前结构与已核实的YJV三芯基础载流量表一致。",
            )
        if core_label == "三芯电缆" and scenario == "direct_buried":
            if soil_thermal_resistivity_k_m_per_w is None:
                return None, None, None
            code = (
                "yjv_three_core_buried_duct_soil_"
                f"{soil_thermal_resistivity_k_m_per_w:g}"
            )
            return (
                code,
                scenario_bases.get(code),
                "按表6.10的YJV三芯电缆埋地管槽工况查取；不套用YJV22直接敷设在土壤中的数据。",
            )
        if (
            core_label in {"四芯电缆", "五芯电缆", "四芯电缆＋独立PE"}
            and scenario in {"tray", "direct_buried"}
        ):
            code = (
                "yjv_multicore_in_air_people"
                if scenario == "tray"
                else "yjv_multicore_in_ground_people"
            )
            note = (
                "按表31多芯电缆空气中基础载流量，并叠加已选槽盒成组修正。"
                if scenario == "tray"
                else "按表31多芯电缆地下敷设基础初选；土壤条件等修正尚待校核。"
            )
            return code, scenario_bases.get(code), note
    return None, None, None


def lookup_yjv_fault_loop_structure(
    configuration_code: str,
    phase_section_mm2: float,
) -> dict[str, Any] | None:
    """Return traceable round-core geometry for supported YJV 3+1/3+2 cables."""
    profile = YJV_FAULT_LOOP_STRUCTURE["configuration_profiles"].get(configuration_code)
    phase_section = float(phase_section_mm2)
    protective_section = YJV_REDUCED_PROTECTIVE_SECTIONS.get(phase_section)
    phase = YJV_ROUND_CONDUCTOR_GEOMETRY.get(phase_section)
    protective = (
        YJV_ROUND_CONDUCTOR_GEOMETRY.get(protective_section)
        if protective_section is not None
        else None
    )
    if not profile or not phase or not protective:
        return None

    phase_radius_cm = phase["conductor_diameter_mm"] / 20
    protective_radius_cm = protective["conductor_diameter_mm"] / 20
    center_distance_mm = (
        phase["conductor_diameter_mm"] / 2
        + phase["insulation_thickness_mm"]
        + protective["conductor_diameter_mm"] / 2
        + protective["insulation_thickness_mm"]
    )
    table = YJV_FAULT_LOOP_STRUCTURE["tables"][profile]
    page = YJV_FAULT_LOOP_STRUCTURE["pages"][profile]
    return {
        "family": "YJV",
        "voltage": YJV_FAULT_LOOP_STRUCTURE["voltage"],
        "configuration_code": configuration_code,
        "profile": profile,
        "phase_section_mm2": phase_section,
        "protective_section_mm2": protective_section,
        "phase_conductor_radius_cm": round(phase_radius_cm, 6),
        "protective_conductor_radius_cm": round(protective_radius_cm, 6),
        "phase_pe_center_distance_cm": round(center_distance_mm / 10, 6),
        "phase_conductor_diameter_mm": phase["conductor_diameter_mm"],
        "protective_conductor_diameter_mm": protective["conductor_diameter_mm"],
        "phase_insulation_thickness_mm": phase["insulation_thickness_mm"],
        "protective_insulation_thickness_mm": protective["insulation_thickness_mm"],
        "source": YJV_FAULT_LOOP_STRUCTURE["source"],
        "document": YJV_FAULT_LOOP_STRUCTURE["document"],
        "table": f"表1、{table}",
        "page": f"{YJV_FAULT_LOOP_STRUCTURE['pages']['conductor_geometry']}；{page}",
        "status": YJV_FAULT_LOOP_STRUCTURE["status"],
        "geometry_note": "圆形绝缘线芯相邻布置，中心距按两线芯绝缘后半径之和计算。",
    }


def lookup_busway_phase_pe_impedance(
    series_code: str,
    rating_a: float,
) -> dict[str, Any] | None:
    """Return one exact Canalis Ph/PE impedance-method row without interpolation."""
    catalog = BUSWAY_PHASE_PE_IMPEDANCE
    series = catalog["series"].get(str(series_code).lower())
    if not series:
        return None
    rating = float(rating_a)
    row = series["rows"].get(rating)
    if not row:
        return None
    return {
        "series_code": str(series_code).lower(),
        "series_name": series["name"],
        "rating_a": rating,
        # 1 mΩ/m = 1 Ω/km；数值不变。
        "resistance_ohm_per_km": float(row["r"]),
        "reactance_ohm_per_km": float(row["x"]),
        "source": "Schneider Electric",
        "document": series["document"],
        "document_reference": series["document_reference"],
        "heading": series["heading"],
        "page": series["page"],
        "condition": series["condition"],
        "source_rule_code": catalog["source_rule_code"],
        "status": catalog["status"],
        "unit_mapping_note": "平台单位换算（非原表文字）：1 mΩ/m = 1 Ω/km。",
    }


def lookup_canalis_kta_3lnpe_electrical(
    rating_a: float,
    ambient_temperature_c: float,
) -> dict[str, Any] | None:
    catalog = CANALIS_KTA_3LNPE_ELECTRICAL
    row = catalog["rows"].get(float(rating_a))
    factor = catalog["temperature_factors"].get(float(ambient_temperature_c))
    if row is None or factor is None:
        return None
    return {
        "series_code": catalog["series_code"],
        "series_name": catalog["series_name"],
        "rating_a": float(rating_a),
        "rated_voltage_v": catalog["rated_voltage_v"],
        "corrected_ampacity_a": float(rating_a) * factor,
        "temperature_factor": factor,
        "voltage_drop_r_ohm_per_km": row["r1"],
        "voltage_drop_x_ohm_per_km": row["x1"],
        "three_phase_r_ohm_per_km": row["r1"],
        "three_phase_x_ohm_per_km": row["x1"],
        "phase_neutral_r_ohm_per_km": row["rn"],
        "phase_neutral_x_ohm_per_km": row["xn"],
        "phase_pe_r_ohm_per_km": row["rpe"],
        "phase_pe_x_ohm_per_km": row["xpe"],
        "short_time_withstand_ka_1s": row["icw_1s"],
        "source_rule_code": catalog["source_rule_code"],
        "status": catalog["status"],
        "document": catalog["document"],
        "document_reference": catalog["document_reference"],
        "page": catalog["page"],
        "condition": catalog["condition"],
        "unit_mapping_note": "平台单位换算（非原表文字）：1 mΩ/m = 1 Ω/km。",
    }


def lookup_transformer_phase_pe_impedance(
    series_code: str,
    capacity_kva: float,
    uk_percent: float,
) -> dict[str, Any] | None:
    """Return an exact, traceable transformer phase-PE table row without interpolation."""
    catalog = TRANSFORMER_PHASE_PE_IMPEDANCE
    series = catalog["series"].get(str(series_code).lower())
    if not series:
        return None
    row = series["rows"].get(float(capacity_kva), {}).get(float(uk_percent))
    if not row:
        return None
    return {
        "series_code": str(series_code).lower(),
        "series_name": series["name"],
        "capacity_kva": float(capacity_kva),
        "uk_percent": float(uk_percent),
        "phase_pe_resistance_mohm": row["r_mohm"],
        "phase_pe_reactance_mohm": row["x_mohm"],
        "phase_pe_resistance_ohm": row["r_mohm"] / 1000,
        "phase_pe_reactance_ohm": row["x_mohm"] / 1000,
        "source": catalog["source"],
        "document": catalog["document"],
        "clause": catalog["clause"],
        "table": series["table"],
        "page": catalog["page"],
        "status": catalog["status"],
    }


def lookup_transformer_positive_sequence_impedance(
    series_code: str,
    capacity_kva: float,
    uk_percent: float,
) -> dict[str, Any] | None:
    """Return the exact 400 V-side positive-sequence table row, without interpolation."""
    catalog = TRANSFORMER_PHASE_PE_IMPEDANCE
    series = catalog["series"].get(str(series_code).lower())
    if not series:
        return None
    row = series["rows"].get(float(capacity_kva), {}).get(float(uk_percent))
    if not row or "positive_x_mohm" not in row:
        return None
    return {
        "series_code": str(series_code).lower(),
        "series_name": series["name"],
        "capacity_kva": float(capacity_kva),
        "uk_percent": float(uk_percent),
        "positive_sequence_resistance_ohm": row["r_mohm"] / 1000,
        "positive_sequence_reactance_ohm": row["positive_x_mohm"] / 1000,
        "load_loss_kw": row["pk_w"] / 1000,
        "low_voltage_v": catalog["low_voltage_v"],
        "source": catalog["source"],
        "document": catalog["document"],
        "table": series["table"],
        "page": catalog["page"],
        "status": catalog["status"],
    }


def lookup_yjv_four_core_phase_pe_impedance(
    phase_section_mm2: float,
) -> dict[str, Any] | None:
    """Derive the minimum-fault phase-PE R/X of a listed YJV 3+1 cable.

    The fourth conductor is treated as the metallic PE return. Earth parallel
    return is deliberately excluded. With Z2=Z1, equations (4.6-44) to
    (4.6-46) give Zphp=(2Z1+Z0N)/3. The handbook's 1.5 resistance multiplier
    for low-voltage single-phase minimum-fault calculations is then applied.
    """
    catalog = YJV_FOUR_CORE_SEQUENCE_IMPEDANCE
    row = catalog["rows"].get(float(phase_section_mm2))
    if not row:
        return None
    r_php_20 = (2 * row["r1"] + row["r0n"]) / 3
    x_php = (2 * row["x1"] + row["x0n"]) / 3
    multiplier = catalog["minimum_fault_resistance_multiplier"]
    return {
        "family": "YJV",
        "configuration_code": "yjv_4c_3ph_n_pe",
        "cable_specification": (
            f"YJV-0.6/1kV 3×{phase_section_mm2:g}+1×"
            f"{row['protective_section_mm2']:g}"
        ),
        "phase_section_mm2": float(phase_section_mm2),
        "protective_section_mm2": row["protective_section_mm2"],
        "positive_sequence_resistance_ohm_per_km": row["r1"],
        "positive_sequence_reactance_ohm_per_km": row["x1"],
        "zero_sequence_n_resistance_ohm_per_km": row["r0n"],
        "zero_sequence_n_reactance_ohm_per_km": row["x0n"],
        "phase_pe_resistance_20c_ohm_per_km": round(r_php_20, 6),
        "phase_pe_resistance_multiplier": multiplier,
        "phase_pe_resistance_ohm_per_km": round(r_php_20 * multiplier, 6),
        "phase_pe_reactance_ohm_per_km": round(x_php, 6),
        "source": catalog["source"],
        "document": catalog["document"],
        "table": catalog["table"],
        "page": catalog["page"],
        "formula": catalog["formula"],
        "formula_page": catalog["formula_page"],
        "calculation_condition_page": catalog["calculation_condition_page"],
        "status": catalog["status"],
        "boundary_note": (
            "第四芯作为PE金属返回导体；不计大地并联返回；表列理论数据仅供参考。"
        ),
    }
