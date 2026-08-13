from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "electrical_calc.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS circuits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    voltage_v REAL,
                    installed_power_kw REAL,
                    demand_factor REAL,
                    power_factor REAL,
                    efficiency REAL,
                    length_m REAL,
                    cable_spec TEXT NOT NULL DEFAULT '',
                    cable_ampacity_a REAL,
                    cable_r_ohm_per_km REAL,
                    cable_x_ohm_per_km REAL,
                    voltage_drop_limit_pct REAL,
                    breaker_model TEXT NOT NULL DEFAULT '',
                    breaker_rating_a REAL,
                    breaking_capacity_ka REAL,
                    source_r_ohm REAL,
                    source_x_ohm REAL,
                    transformer_r_ohm REAL,
                    transformer_x_ohm REAL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, code)
                );
                CREATE TABLE IF NOT EXISTS reference_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','verified','approved')),
                    document_name TEXT NOT NULL DEFAULT '',
                    document_version TEXT NOT NULL DEFAULT '',
                    clause_no TEXT NOT NULL DEFAULT '',
                    original_text TEXT NOT NULL DEFAULT '',
                    page_no TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calculation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    circuit_id INTEGER NOT NULL REFERENCES circuits(id) ON DELETE CASCADE,
                    circuit_revision INTEGER NOT NULL,
                    module TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    input_snapshot TEXT NOT NULL,
                    rule_snapshot TEXT NOT NULL,
                    process_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provisional_status TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_networks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL DEFAULT 1,
                    input_hash TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS network_calculation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    network_id INTEGER NOT NULL REFERENCES project_networks(id) ON DELETE CASCADE,
                    network_revision INTEGER NOT NULL,
                    engine_version TEXT NOT NULL,
                    task_mode TEXT NOT NULL,
                    input_snapshot TEXT NOT NULL,
                    derived_json TEXT NOT NULL,
                    audit_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    rule_snapshot TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provisional_status TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_network_runs_project
                    ON network_calculation_runs(project_id,id DESC);
                CREATE INDEX IF NOT EXISTS idx_network_runs_network
                    ON network_calculation_runs(network_id,network_revision);
                CREATE TABLE IF NOT EXISTS project_motors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    circuit_code TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    input_hash TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id,circuit_code)
                );
                CREATE TABLE IF NOT EXISTS motor_calculation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    motor_id INTEGER NOT NULL REFERENCES project_motors(id) ON DELETE CASCADE,
                    motor_revision INTEGER NOT NULL,
                    engine_version TEXT NOT NULL,
                    input_snapshot TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    rule_snapshot TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provisional_status TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_motor_runs_project
                    ON motor_calculation_runs(project_id,id DESC);
                """
            )
            count = conn.execute("SELECT COUNT(*) FROM reference_rules").fetchone()[0]
            if count == 0:
                now = utc_now()
                conn.executemany(
                    """
                    INSERT INTO reference_rules
                    (code,name,status,note,updated_at)
                    VALUES (?,?, 'pending', ?, ?)
                    """,
                    [
                        ("ELEC.LOAD.CURRENT", "负荷电流计算方法", "待提供规范或设计手册原文。", now),
                        ("ELEC.CABLE.COORDINATION", "导线与保护器件配合", "待提供规范及产品参数。", now),
                        ("ELEC.VDROP", "线路电压降计算与限值", "待提供规范或设计手册原文。", now),
                        ("ELEC.SHORT_CIRCUIT", "三相短路电流简化计算", "待提供计算依据与适用范围。", now),
                        ("ELEC.BREAKING.CAPACITY", "保护器件分断能力校核", "待提供规范及产品参数。", now),
                    ],
                )

            # 新规则采用幂等插入，不覆盖用户已经核实或批准的内容。
            now = utc_now()
            conn.executemany(
                """
                INSERT OR IGNORE INTO reference_rules
                (code,name,status,document_name,document_version,clause_no,original_text,page_no,note,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    ("ELEC.LOAD.POWER_FACTOR", "普通负荷功率因数参数", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表3.38～表3.42", "表3.38 需要系数及自然功率因数表\n表3.39 一般家用电器用电负荷、功率因数表\n表3.40 常用炊事电器用电负荷、功率因数表\n表3.41 空调末端设备用电负荷、功率因数表\n表3.42 冷藏冷冻机冷饮水类电器用电负荷、功率因数表", "PDF第37～40页", "已逐页核对原图；参数尚未批准。", now),
                    ("ELEC.CABLE.BV.AMPACITY", "BV基础载流量", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表6.1", "表6.1 BV绝缘电线敷设在明敷导管内的持续载流量（A）", "PDF第84页", "表格已核对并接入30℃基础值；必须由用户明确载流导线根数，环境温度及成组修正仍未完成，尚未批准自动选型。", now),
                    ("ELEC.CABLE.YJV.AMPACITY", "YJV基础载流量", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表6.10", "表6.10 YJV、YJLV三芯电力电缆的持续载流量（A）", "PDF第92～93页", "表格已逐页视觉核验。当前接入三芯YJV在30℃明敷导管、30℃空气中，以及20℃埋地管槽内且土壤热阻系数1.0、1.5、2.0、2.5 K·m/W的基础值；不把YJV22敷设在土壤中的数据套用于YJV。芯数与实际条件须由用户确认，尚未批准自动选型。", now),
                    ("ELEC.CABLE.YJV.MULTICORE.AMPACITY", "YJV多芯电缆基础载流量", "verified", "电线电缆造型手册.pdf", "人民电器《电线电缆选型手册》", "表31", "表31 0.6/1kV 交联聚乙烯绝缘电力电缆允许持续载流量（A）", "PDF第50页（印刷第48页）", "已逐页视觉核验。当前接入铜芯二芯、三芯、四芯、三加一芯、三加二芯、四加一芯、五芯电缆在空气40℃及地下25℃的表列基础值；槽盒成组修正另按19DX101-1表6.25处理，其他修正未完成，尚未批准。", now),
                    ("ELEC.CABLE.TEMPERATURE.DERATING", "电线电缆载流量温度修正", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表6.22、表6.24", "表6.22 环境空气温度不同于30℃时的校正系数（用于敷设在空气中的电缆载流量）\n表6.24 地下温度不同于20℃时的校正系数（用于埋地管槽中的电缆载流量）", "PDF第106页", "已逐页视觉核验。平台按实际温度对应系数除以基础载流量表基准温度对应系数进行相对换算；该换算为平台处理，不是表格原文。未批准。", now),
                    ("ELEC.CABLE.TRAY.GROUPING", "自由空气中多根多芯线缆束降低系数", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表6.25", "表6.25 敷设在自由空气中多根多芯线缆束的降低系数", "PDF第107页", "已逐页视觉核验。当前只接入水平有孔托盘/梯架的表列层数和每层线缆数精确档位；未批准。", now),
                    ("ELEC.CABLE.BURIED_DUCT.GROUPING", "埋地管槽内多回路电缆降低系数", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表6.27", "表6.27 敷设在埋地管槽内多回路电缆的降低系数", "PDF第108页", "已逐页视觉核验并接入2～20回路、无间距/0.25m/0.5m/1.0m的表列值。表注参考条件为埋地深度0.7m、土壤热阻系数2.5 K·m/W，且有些情况下误差会达到±10%；未批准。", now),
                    ("ELEC.CABLE.ENCLOSED.GROUPING", "成束或封闭敷设多回路降低系数", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表6.26", "表6.26 多回路或多根电缆成束敷设的降低系数\n成束敷设在空气中、沿槽、嵌入或封闭式敷设", "PDF第107页", "已逐页视觉核验。当前只接入电缆相互接触且成束、沿槽、嵌入或封闭式敷设这一行的1～9、12、16、20回路档位；表注要求尺寸和负荷相同。墙上、天花板、托盘、梯架等其他行未在穿管场景自动套用；未批准。", now),
                    ("ELEC.VDROP.IMPEDANCE", "线路电压降R/X参数", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表3.21、表3.23、表3.24", "表3.21 1kV交联聚乙烯绝缘电力电缆用于三相380V系统的电压降［%/(A·km)］\n表3.23 三相380V铜芯导线的电压降［%/(A·km)］\n表3.24 单相交流220V及直流聚氯乙烯绝缘铜芯电线的电压降［%/(A·km)］", "PDF第27、29～30页", "已逐页核对原图并接入表列R/X；仅用于当前表格覆盖组合的自动暂算，尚未批准。", now),
                    ("ELEC.VDROP.LIMIT", "低压线路允许电压降", "verified", "工业与民用供配电设计手册（第四版）.pdf", "第四版", "第6.2.4节，表6.2-6", "在配电设计中，应按照用电设备端子电压偏差允许值的要求和地区电网电压偏差的具体情况，确定电压降允许值。当缺乏详细计算资料时，线路电压降允许值可参考表6.2-6。\n表6.2-6 线路电压降允许值\n从配电变压器二次侧母线算起的低压线路：5\n从配电变压器二次侧母线算起的供给有照明负荷的低压线路：3～5", "PDF第497页（印刷第465页）", "已逐页视觉核验。快速页普通低压线路取表列5%；照明负荷表列3%～5%，平台按下限3%保守暂算。该映射为平台处理，不是表格原文；规则未批准。", now),
                    ("ELEC.BREAKER.RATING", "断路器设计参数档位", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表5.11～表5.13", "表5.11 框架断路器（ACB）技术参数\n表5.12 塑壳断路器（MCCB）技术参数\n表5.13 微型断路器（MCB）技术参数", "PDF第77～79页", "已逐页核对原图；表中数据仅供参考，当前仅作为设计参数候选，不代表产品型号或正式选型。", now),
                    ("ELEC.BREAKER.ICS.ICW.REFERENCE", "断路器Ics与Icw通用参数", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表5.11～表5.13", "表5.11 框架断路器（ACB）技术参数\nIcs（%Icu）　Ue=380V/415V\n表5.12 塑壳断路器（MCCB）技术参数\nIcs（%Icu）　Ue=380V/415V\n额定短时耐受电流Icw/1s（kA）\n表5.13 微型断路器（MCB）技术参数\n运行短路分断能力Ics（kA）", "PDF第77～79页", "已逐页视觉核验；表中数据仅供参考。Ics必须与所选Icu档位对应；Icw只按表列1s值和适用壳架使用。未批准。", now),
                    ("ELEC.BREAKER.MCB.INSTANTANEOUS", "MCB B/C型保证瞬时动作电流", "verified", "工业与民用供配电设计手册（第四版）.pdf", "第四版", "11.3.2.2，表11.3-4、表11.3-5", "表11.3-4 瞬时脱扣范围\nB：3In～5In（含5In）\nC：5In～10In（含10In）\n表11.3-5 时间-电流动作特性", "PDF第1010页（印刷第978页）", "已逐页视觉核验。当前只按范围上限5In/10In取得B/C型MCB保证瞬时动作（<0.1s）的Ia；D型因通用手册与具体产品范围不同，不自动取得；规则未批准。", now),
                    ("ELEC.RCD.PARAMETERS", "剩余电流动作保护参数", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "表5.6", "表5.6 剩余电流动作保护电器（RCD）的选择", "PDF第72页", "已逐页核对原图；动作电流和类型必须按回路用途、场所及负载电流波形确定，尚未批准。", now),
                    ("ELEC.SHORT_CIRCUIT.TRANSFORMER_LV", "变压器0.4kV低压出口短路电流速查", "verified", "19DX101-1建筑电气常用数据国标图集.pdf", "19DX101-1", "式(15.9)、表15.7", "Ik≈144.34×ST/uk%\n表15.7 变压器0.4kV低压出口处短路电流速查表", "PDF第299、307页", "已逐页视觉核验；表15.7以上级系统容量无穷大为计算条件，未批准前仅用于暂算。", now),
                    ("ELEC.TRANSFORMER.IMPEDANCE.NAMEPLATE", "变压器铭牌参数换算低压侧R/X", "pending", "", "", "", "", "", "S/U/uk%/Pk换算R/X的原始公式及适用条件尚未核实；核实前计算引擎禁止采用。", now),
                    ("ELEC.TRANSFORMER.PHASE_PE.IMPEDANCE", "10/0.4kV Dyn11变压器相保阻抗", "verified", "工业与民用供配电设计手册（第四版）.pdf", "第四版", "第4.6.2节、表4.6-12～表4.6-19、式(4.6-44)～式(4.6-46)", "10（6）/0.4kV三相双绕组变压器的阻抗：配电变压器的正序阻抗可按照表4.6-3中有关公式计算，变压器的负序阻抗等于正序阻抗。Yyn0联结组别的变压器的零序阻抗比正序阻抗大得多，其值由制造厂通过测试提供；Dyn11联结组别的变压器的零序阻抗如果没有测试数据时，可取其值等于正序阻抗值，即相阻抗。", "PDF第336～340页（印刷第304～308页）", "首批仅接入已视觉核验的S11-M与SCB11表列组合；限6/10kV至0.4kV、Dyn11、归算至400V侧，不插值，未批准。", now),
                    ("ELEC.TRANSFORMER.POSITIVE_SEQUENCE.IMPEDANCE", "10/0.4kV变压器正负序阻抗", "verified", "工业与民用供配电设计手册（第四版）.pdf", "第四版", "表4.6-12、表4.6-13", "表4.6-12　S11-M型油浸式叠铁芯变压器阻抗平均值（归算至400V侧）\n表4.6-13　SCB11型环氧树脂浇注干式变压器阻抗平均值（归算至400V侧）", "PDF第337页（印刷第305页）", "已逐项视觉核验负载损耗、正负序电阻及正负序电抗；当前只按S11-M、SCB11精确系列/容量/uk%组合查表，不插值，未批准。", now),
                    ("ELEC.CABLE.YJV.FOUR_CORE.PHASE_PE.IMPEDANCE", "YJV四芯3+1电缆相保阻抗", "verified", "工业与民用供配电设计手册（第四版）.pdf", "第四版", "表4.2-46、式(4.6-44)～式(4.6-46)、第4.6.4节(1)第4项", "表4.2-46　YJV-0.6/1kV 4芯（非等截面）电缆（铜芯）电气参数\n电流回路通过N导体零序阻抗\n在计算单相短路电流时，假设的计算温度升高，电阻值增大，其值一般为20℃时电阻的1.5倍。", "PDF第245、335、340页（印刷第213、303、308页）", "只接入表列圆形导体3+1规格；第四芯明确作为PE金属返回导体时，采用Z′(0)N且不计大地并联返回，并按相保阻抗关系计算；表注说明数据为理论计算数据且仅供参考，未批准。", now),
                    ("ELEC.BUSWAY.CANALIS.PHASE_PE.IMPEDANCE", "Canalis母线槽相—PE故障回路阻抗", "verified", "Schneider_Canalis_Low_Voltage_DEBU034EN.pdf；Schneider_Canalis_KTA_DEBU021EN.pdf", "DEBU034EN；DEBU021EN", "Fault loop characteristics", "Fault loop characteristics\nImpedance method\nAt Inc and at 35°C　Average resistance　Ph/PE\nAt Inc at 35°C and at 50 Hz　Average reactance　Ph/PE", "DEBU034EN PDF第66页（印刷第64页）；DEBU021EN PDF第146～149页（印刷第144～147页）", "已逐页视觉核验。仅按Canalis KS/KTA精确系列、PE结构和额定电流采用表列Ph/PE R/X；单位mΩ/m按等值数值换算为Ω/km，不插值；未批准。", now),
                    ("ELEC.EARTH_FAULT.TN.IMPEDANCE", "TN系统故障回路阻抗与保护电器", "verified", "规范大全-2025.12.7b.chm", "GB/T 16895.21-2020", "411.4.4、411.4.5", "411.4.4　保护电器（见411.4.5）的特性以及回路的阻抗应满足公式（1）：\nZₛ×Iₐ≤U₀　…………（1）\n式中：\nZₛ——故障回路的阻抗，单位为欧姆（Ω），它包括下列部分的阻抗：\n• 电源；\n• 至故障点的线导体；和\n• 故障点和电源之间的保护导体。\nIₐ——在411.3.2.2表41.1、411.3.2.3规定的时间内能使切断电器自动动作的电流，单位为安培（A）。采用剩余电流保护器（RCD）时，是在411.3.2.2规定的时间内切断电源的剩余动作电流。\nU₀——交流或直流线对地的标称电压，单位为伏特（V）。\n411.4.5　下列保护电器可用作TN系统的故障防护（间接接触防护）：\n——过电流保护器；\n——剩余电流保护器（RCD）。", "CHM /规范/GB16895.21-2020/02.htm", "已逐条核实；Ia仍须按具体保护器件时间—电流或RCD动作特性取得；未批准。", now),
                    ("ELEC.EARTH_FAULT.TN.DISCONNECTION_TIME", "TN系统最长切断电源时间", "verified", "规范大全-2025.12.7b.chm", "GB/T 16895.21-2020", "411.3.2.2、411.3.2.3、表41.1", "411.3.2.2　对于不超过如下额定电流的终端回路，其最长的切断电源的时间应符合表41.1的规定：\n——装1个或多个插座的回路为63A；和\n——只供电给固定连接用电设备的回路为32A。\n411.3.2.3　在TN系统内配电回路和411.3.2.2规定之外的回路，其切断电源的时间不可超过5s。\n表41.1　最长的切断电源时间\n| 系统 | 120 V＜U₀≤230 V（s） | 120 V＜U₀≤230 V（s） |\n| --- | --- | --- |\n| 电压 | a.c. | d.c. |\n| TN | 0.4 | 1 |\nU₀：交流或直流线对地的标称电压。", "CHM /规范/GB16895.21-2020/02.htm", "已核实；表41.1本项只录入TN系统交流120V＜U₀≤230V单元格；未批准。", now),
                    ("ELEC.EARTH_FAULT.TN.CONVENTIONAL", "TN系统接地故障常规法", "verified", "Schneider_Electrical_Installation_Guide_2018.pdf", "Electrical Installation Guide 2018", "F 5.3 Conventional method", "Lmax＝0.8U₀Sph/[ρ(1＋m)Ia]\nm＝Sph/SPE", "PDF第185页（F15）", "已逐页视觉核验。当前仅接入铜芯、相导体与PE导体在同一电缆内或彼此靠近、截面不超过120mm²的常规法；采用ρ＝23.7×10^-3Ω·mm²/m及0.8电压系数，规则未批准。", now),
                    ("ELEC.EARTH_FAULT.RCD.TN_ARRANGEMENT", "TN系统采用RCD的接线边界", "verified", "规范大全-2025.12.7b.chm", "GB/T 13955-2017", "4.2.2.2", "4.2.2.2　在TN系统中，必须将TN-C系统改造为TN-C-S、TN-S系统或局部TT系统后，方可安装使用RCD。在TN-C-S系统中，RCD只允许使用在N线与PE线分开部分。", "CHM正文4.2.2.2", "已核实；未批准。", now),
                    ("ELEC.PEN.NO_SWITCHING", "PEN导体禁止设置开关或隔离器件", "verified", "GB51348-2019《民用建筑电气设计标准》.pdf", "GB 51348-2019", "7.7.7第2款", "2　固定安装的电气装置，当满足现行国家标准《低压电气装置　第5-54部分：电气设备的选择和安装　接地配置和保护导体》GB/T 16895.3的有关要求时，可用一根导体兼作保护接地中性导体。但在保护接地中性导体中不应设置任何开关或隔离器件。", "PDF第107页", "已逐页核验；仅用于PEN导体禁开断边界，未批准。", now),
                    ("ELEC.CABLE.FAULT_LOOP.RESISTANCE", "相线—PE/PEN导体电阻计算", "verified", "工业与民用供配电设计手册（第四版）.pdf", "第四版", "9.4.1.1，式(9.4-1)、式(9.4-2)", "Rθ＝ρθcjL/S\nρθ＝ρ20[1＋α(θ－20)]\nρ20——导线温度为20℃时的电阻率，铝线芯（包括铝电线、铝电缆、硬铝母线）为0.0282Ω·mm²/m，铜线芯（包括铜电线、铜电缆、硬铜母线）为0.0172Ω·mm²/m；\nα——电阻温度系数，铝和铜都取0.004；", "PDF第893页（印刷第861页）", "已逐页视觉核验；绞入系数单股取1、多股取1.02。仅完成导体电阻分量，交流电阻修正和回路电抗仍须另有来源；未批准。", now),
                    ("ELEC.CABLE.FAULT_LOOP.REACTANCE", "相线—PE/PEN回路电抗参数", "verified", "工业与民用供配电设计手册（第四版）.pdf", "第四版", "9.4.1.2，式(9.4-6)～式(9.4-8)", "9.4.1.2 导线电抗计算\n配电工程中，架空线各相导体一般不换位，为简化计算，假设各相电抗相等。另外，由于容抗对感抗而言，正好起抵消的作用，虽然有些电缆线路其容抗值不小，但为了简化计算，线路容抗常可忽略不计，因此，导线电抗值实际上只计入感抗值。这样的计算结果往往趋保守。", "PDF第895页（印刷第863页）", "已核实计算关系。只有取得导体半径、绝缘厚度和几何均距等可追溯结构量时才按公式计算；不得固定采用0.08Ω/km，未批准。", now),
                    ("ELEC.PE.THERMAL.WITHSTAND", "PE导体短路热稳定绝热法", "verified", "Schneider_Electrical_Installation_Guide_2018.pdf", "Electrical Installation Guide 2018", "G 5.2、G 6.2，Fig. G52、G59、G60", "For a period of 5 seconds or less, the relationship I2t = k2S2 characterizes the time in seconds during which a conductor of c.s.a. S (in mm2) can be allowed to carry a current I, before its temperature reaches a level which would damage the surrounding insulation.\nAdiabatic method Any size: S_PE/PEN = I√t/k.\nFig. G60  k factor values for LV PE conductors.", "PDF第259、262～263页（G33、G36～G37）", "已逐页视觉核验。当前只接入铜PE、PVC/XLPE及已确认的单芯/裸导体或多芯电缆结构；须输入有来源的产品I²t，或故障电流与不超过5s的切除时间；未批准。", now),
                    ("ELEC.PHASE.THERMAL.WITHSTAND", "相导体短路热稳定绝热法", "verified", "Schneider_Electrical_Installation_Guide_2018.pdf", "Electrical Installation Guide 2018", "G 5.2，Fig. G52", "For a period of 5 seconds or less, the relationship I²t = k²S² characterizes the time in seconds during which a conductor of c.s.a. S (in mm²) can be allowed to carry a current I, before its temperature reaches a level which would damage the surrounding insulation.\nFig. G52　Value of the constant k according to table 43A of IEC 60364-4-43", "PDF第259页（G33）", "已逐页视觉核验。当前接入铜相导体：PVC≤300mm²取k=115，PVC>300mm²取k=103，EPR/XLPE取k=143；须输入产品I²t，或短路电流与不超过5s的切除时间；未批准。", now),
                    ("ELEC.PE.MIN_SECTION.TABLE54_2", "保护接地导体最小截面积", "verified", "规范大全-2025.12.7b.chm", "GB/T 16895.3-2017", "543.1.1、543.1.3、表54.2", "543．1．1　每根保护接地导体的截面积都应满足GB/T 16895．21-2011中411．3．2关于自动切断电源所要求的条件，且能承受保护电器切断时间内预期故障电流引起的机械和热应力。\n保护接地导体的截面积可按543．1．2的公式计算，也可按表54．2进行选择。这两种方法都应考虑543．1．3的要求。\n表54．2 保护接地导体的最小截面积（如不根据543．1．2的公式计算）\n线导体截面积S（铜）：S≤16；16＜S≤35；S＞35。\n保护接地导体与线导体使用相同材料：S；16；S/2。\n543．1．3　不是电缆的组成部分或不与线导体共处于同一外护物之内的每根保护接地导体，其截面积不应小于：——有防机械损伤保护，2．5mm²铜，16mm²铝；——无防机械损伤保护，4mm²铜，16mm²铝。", "CHM /规范/GB16895.3-2017/03.htm", "已逐项视觉核验表54.2图像。当前自动路径只接入线导体与PE均为铜芯；不同材料须采用k1/k2，本轮不猜。PEN另按543.4处理，不套用本规则。未批准。", now),
                    ("ELEC.CABLE.YJV.STRUCTURE", "0.6/1kV YJV圆形线芯结构尺寸", "verified", "电线电缆造型手册.pdf", "人民电器《电线电缆选型手册》", "表1、表4、表6", "表1 单芯交联聚乙烯绝缘聚氯乙烯/聚乙烯护套电力电缆结构尺寸及重量\n表4 三加一芯交联聚乙烯绝缘聚氯乙烯/聚乙烯护套电力电缆结构尺寸及重量\n表6 三加二芯交联聚乙烯绝缘聚氯乙烯/聚乙烯护套电力电缆结构尺寸及重量", "PDF第35～37页（印刷第33～35页）", "已逐页视觉核验。当前仅接入4～35mm²未以括号标注的圆形主导体，并按表列主导体/中性线（接地线）组合取得PE截面；圆形绝缘线芯中心距为计算派生值，规则未批准。", now),
                ],
            )
            # 只更新仍为旧默认文案的记录，不覆盖用户编辑或批准状态。
            catalog_note_updates = [
                (
                    "ELEC.CABLE.BV.AMPACITY",
                    "表格已核对；载流导线根数及修正条件未确认，尚未批准自动选型。",
                    "表格已核对并接入30℃基础值；必须由用户明确载流导线根数，环境温度及成组修正仍未完成，尚未批准自动选型。",
                ),
                (
                    "ELEC.CABLE.YJV.AMPACITY",
                    "表格已核对；芯数、敷设及修正条件未确认，尚未批准自动选型。",
                    "表格已核对；当前仅接入PDF第92页三芯、30℃、明敷导管及空气中基础值，芯数须由用户确认，直埋与各项修正未接入，尚未批准自动选型。",
                ),
                (
                    "ELEC.CABLE.YJV.AMPACITY",
                    "表格已核对；当前仅接入PDF第92页三芯、30℃、明敷导管及空气中基础值，芯数须由用户确认，直埋与各项修正未接入，尚未批准自动选型。",
                    "表格已逐页视觉核验。当前接入三芯YJV在30℃明敷导管、30℃空气中，以及20℃埋地管槽内且土壤热阻系数1.0、1.5、2.0、2.5 K·m/W的基础值；不把YJV22敷设在土壤中的数据套用于YJV。芯数与实际条件须由用户确认，尚未批准自动选型。",
                ),
            ]
            for code, old_note, new_note in catalog_note_updates:
                conn.execute(
                    """
                    UPDATE reference_rules SET note=?,updated_at=?
                    WHERE code=? AND status='verified' AND note=?
                    """,
                    (new_note, utc_now(), code, old_note),
                )
            conn.execute(
                """
                UPDATE reference_rules
                SET status='verified', document_name=?, document_version=?, clause_no=?,
                    original_text=?, page_no=?, note=?, updated_at=?
                WHERE code='ELEC.TRANSFORMER.IMPEDANCE.NAMEPLATE'
                  AND status='pending' AND document_name=''
                """,
                (
                    "Schneider_Electrical_Installation_Guide_2018.pdf",
                    "Electrical Installation Guide 2018",
                    "G 4.1，Fig. G37",
                    "Ztr＝U20²/Sn×Usc/100（mΩ）\n"
                    "Pcu＝3In²×Rtr，Rtr＝Pcu×10³/(3In²)（mΩ）\n"
                    "Xtr＝√(Ztr²－Rtr²)",
                    "PDF第251页（G25）",
                    "已逐页视觉核验。当前按变压器低压空载线电压U20换算；U20未提供时，原页允许按1.05×Un近似。只形成变压器本体R/X；上级系统阻抗仍须明确为无限容量或提供R/X。未批准。",
                    utc_now(),
                ),
            )
            conn.execute(
                """
                UPDATE reference_rules
                SET status='verified',
                    clause_no='9.4.1.2，式(9.4-6)～式(9.4-8)',
                    original_text=?,
                    page_no='PDF第895页（印刷第863页）',
                    note=?,
                    updated_at=?
                WHERE code='ELEC.CABLE.FAULT_LOOP.REACTANCE'
                  AND status='pending'
                  AND COALESCE(original_text,'')=''
                """,
                (
                    "9.4.1.2 导线电抗计算\n"
                    "配电工程中，架空线各相导体一般不换位，为简化计算，假设各相电抗相等。"
                    "另外，由于容抗对感抗而言，正好起抵消的作用，虽然有些电缆线路其容抗值不小，"
                    "但为了简化计算，线路容抗常可忽略不计，因此，导线电抗值实际上只计入感抗值。"
                    "这样的计算结果往往趋保守。",
                    "已核实计算关系。只有取得导体半径、绝缘厚度和几何均距等可追溯结构量时才按公式计算；"
                    "不得固定采用0.08Ω/km，未批准。",
                    utc_now(),
                ),
            )
            verified_rules = [
                (
                    "ELEC.BREAKER.RATING",
                    "verified",
                    "19DX101-1建筑电气常用数据国标图集.pdf",
                    "19DX101-1",
                    "表5.11～表5.13",
                    "表5.11 框架断路器（ACB）技术参数\n表5.12 塑壳断路器（MCCB）技术参数\n表5.13 微型断路器（MCB）技术参数",
                    "PDF第77～79页",
                    "已逐页核对原图；表中数据仅供参考，当前仅作为设计参数候选，不代表产品型号或正式选型。",
                ),
                (
                    "ELEC.LOAD.CURRENT",
                    "verified",
                    "GB51348-2019《民用建筑电气设计标准》.pdf",
                    "GB 51348-2019",
                    "3.5.1、3.5.2",
                    "3.5.1 负荷计算应包括下列内容：\n1 有功功率、无功功率、视在功率、无功补偿；\n2 一级、二级及三级负荷容量；\n3 季节性负荷容量。\n3.5.2 方案设计阶段可采用单位指标法；初步设计及施工图设计阶段，宜采用需要系数法。",
                    "PDF第40页",
                    "项目内来源：GB51348-2019《民用建筑电气设计标准》.pdf。条文已核实，但未给出当前引擎采用的计算电流公式，因此不得批准该算法。",
                ),
                (
                    "ELEC.CABLE.COORDINATION",
                    "verified",
                    "GB51348-2019《民用建筑电气设计标准》.pdf",
                    "GB 51348-2019",
                    "7.4.2、7.5.1第1款",
                    "7.4.2 低压配电导体截面积的选择应符合下列要求：\n1 导体的载流量不应小于预期负荷的最大计算电流和按保护条件所确定的电流，并应按敷设方式和环境条件进行修正；\n2 线路电压损失不应超过规定的允许值；\n3 导体应满足动稳定与热稳定的要求；\n4 导体最小截面积应满足机械强度的要求，配电线路每一相导体截面积不应小于表7.4.2的规定。\n7.5.1 低压电器的选择应符合下列规定：\n1 选用的电器应满足下列要求：\n1)电器的额定电压、额定频率应与所在回路标称电压及标称频率相适应；\n2)电器的额定电流不应小于所在回路的计算电流；\n3)电器应适应所在场所的环境条件；\n4)电器应满足短路条件下的动稳定与热稳定的要求，用于断开短路电流的电器，应满足短路条件下的通断能力。",
                    "PDF第93-94、98页",
                    "项目内来源：GB51348-2019《民用建筑电气设计标准》.pdf。厂家载流量、敷设及环境修正数据尚未核实，不能批准自动选型。",
                ),
                (
                    "ELEC.VDROP",
                    "verified",
                    "GB51348-2019《民用建筑电气设计标准》.pdf",
                    "GB 51348-2019",
                    "7.4.2第2款",
                    "7.4.2 低压配电导体截面积的选择应符合下列要求：\n2 线路电压损失不应超过规定的允许值；",
                    "PDF第93-94页",
                    "项目内来源：GB51348-2019《民用建筑电气设计标准》.pdf。条文未给出当前引擎采用的电压降公式及具体允许值，不能批准算法和限值。",
                ),
                (
                    "ELEC.BREAKING.CAPACITY",
                    "verified",
                    "GB51348-2019《民用建筑电气设计标准》.pdf",
                    "GB 51348-2019",
                    "7.6.2第1款",
                    "7.6.2 配电线路的短路保护应符合下列规定：\n1 短路保护电器的分断能力不应小于保护电器安装处的预期短路电流。",
                    "PDF第102页",
                    "项目内来源：GB51348-2019《民用建筑电气设计标准》.pdf。产品额定分断能力仍须由厂家资料核实。",
                ),
            ]
            for rule in verified_rules:
                conn.execute(
                    """
                    UPDATE reference_rules
                    SET status=?,document_name=?,document_version=?,clause_no=?,original_text=?,page_no=?,note=?,updated_at=?
                    WHERE code=? AND status='pending' AND original_text=''
                    """,
                    (*rule[1:], utc_now(), rule[0]),
                )
            conn.execute(
                """
                UPDATE reference_rules SET note=?,updated_at=?
                WHERE code='ELEC.SHORT_CIRCUIT' AND status='pending' AND original_text=''
                """,
                (
                    "已定位《工业与民用供配电设计手册（第四版）》4.3短路电流计算，PDF第259页起；当前简化等值阻抗算法的适用范围和公式尚未完成逐式视觉核验，保持待核实。",
                    utc_now(),
                ),
            )

    @staticmethod
    def row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, COUNT(c.id) AS circuit_count
                FROM projects p LEFT JOIN circuits c ON c.project_id=p.id
                GROUP BY p.id ORDER BY p.updated_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def create_project(self, code: str, name: str, description: str = "") -> int:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO projects(code,name,description,created_at,updated_at) VALUES (?,?,?,?,?)",
                (code.strip(), name.strip(), description.strip(), now, now),
            )
            return int(cursor.lastrowid)

    def get_project(self, project_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.row(conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    def list_circuits(self, project_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM circuits WHERE project_id=? ORDER BY code", (project_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_circuit(self, circuit_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.row(conn.execute("SELECT * FROM circuits WHERE id=?", (circuit_id,)).fetchone())

    def upsert_circuit(self, project_id: int, data: dict[str, Any]) -> int:
        fields = [
            "code", "name", "phase", "voltage_v", "installed_power_kw", "demand_factor",
            "power_factor", "efficiency", "length_m", "cable_spec", "cable_ampacity_a",
            "cable_r_ohm_per_km", "cable_x_ohm_per_km", "voltage_drop_limit_pct",
            "breaker_model", "breaker_rating_a", "breaking_capacity_ka", "source_r_ohm",
            "source_x_ohm", "transformer_r_ohm", "transformer_x_ohm",
        ]
        values = [data.get(field) for field in fields]
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id,revision FROM circuits WHERE project_id=? AND code=?",
                (project_id, data.get("code")),
            ).fetchone()
            if existing:
                assignments = ",".join(f"{field}=?" for field in fields)
                conn.execute(
                    f"UPDATE circuits SET {assignments}, revision=revision+1, updated_at=? WHERE id=?",
                    (*values, now, existing["id"]),
                )
                conn.execute("UPDATE calculation_runs SET stale=1 WHERE circuit_id=?", (existing["id"],))
                circuit_id = int(existing["id"])
            else:
                placeholders = ",".join("?" for _ in fields)
                cursor = conn.execute(
                    f"""
                    INSERT INTO circuits(project_id,{','.join(fields)},created_at,updated_at)
                    VALUES (?,{placeholders},?,?)
                    """,
                    (project_id, *values, now, now),
                )
                circuit_id = int(cursor.lastrowid)
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
            return circuit_id

    def rules_by_code(self) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM reference_rules ORDER BY code").fetchall()
            return {row["code"]: dict(row) for row in rows}

    def list_rules(self) -> list[dict[str, Any]]:
        return list(self.rules_by_code().values())

    def update_rule(self, rule_id: int, data: dict[str, Any]) -> None:
        fields = [
            "name", "status", "document_name", "document_version", "clause_no",
            "original_text", "page_no", "note",
        ]
        with self.connect() as conn:
            conn.execute(
                f"UPDATE reference_rules SET {','.join(f'{x}=?' for x in fields)},updated_at=? WHERE id=?",
                (*(data.get(field, "") for field in fields), utc_now(), rule_id),
            )

    def create_runs(
        self,
        project_id: int,
        circuit: dict[str, Any],
        outcomes: list[dict[str, Any]],
        rule_snapshot: dict[str, dict[str, Any]],
    ) -> list[int]:
        ids: list[int] = []
        with self.connect() as conn:
            for outcome in outcomes:
                cursor = conn.execute(
                    """
                    INSERT INTO calculation_runs
                    (project_id,circuit_id,circuit_revision,module,engine_version,input_snapshot,
                     rule_snapshot,process_json,result_json,warnings_json,status,provisional_status,
                     stale,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                    """,
                    (
                        project_id,
                        circuit["id"],
                        circuit["revision"],
                        outcome["module"],
                        outcome["version"],
                        json.dumps(circuit, ensure_ascii=False),
                        json.dumps(
                            {code: rule_snapshot.get(code, {}) for code in outcome["rule_codes"]},
                            ensure_ascii=False,
                        ),
                        json.dumps(outcome["steps"], ensure_ascii=False),
                        json.dumps(outcome["outputs"], ensure_ascii=False),
                        json.dumps(outcome["warnings"], ensure_ascii=False),
                        outcome["status"],
                        outcome["provisional_status"],
                        utc_now(),
                    ),
                )
                ids.append(int(cursor.lastrowid))
        return ids

    def list_runs(self, project_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*,c.code AS circuit_code,c.name AS circuit_name
                FROM calculation_runs r JOIN circuits c ON c.id=r.circuit_id
                WHERE r.project_id=? ORDER BY r.id DESC LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*,c.code AS circuit_code,c.name AS circuit_name,p.name AS project_name,
                       p.code AS project_code
                FROM calculation_runs r
                JOIN circuits c ON c.id=r.circuit_id
                JOIN projects p ON p.id=r.project_id
                WHERE r.id=?
                """,
                (run_id,),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            for key in ("input_snapshot", "rule_snapshot", "process_json", "result_json", "warnings_json"):
                data[key] = json.loads(data[key])
            return data

    @staticmethod
    def _canonical_json(data: dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def save_project_network(
        self, project_id: int, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """保存当前完整回路；内容变化时递增版本并使旧结果过期。"""

        serialized = self._canonical_json(input_data)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        now = utc_now()
        with self.connect() as conn:
            project = conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ValueError("项目不存在")
            existing = conn.execute(
                "SELECT * FROM project_networks WHERE project_id=?", (project_id,)
            ).fetchone()
            if existing and existing["input_hash"] == digest:
                return {
                    "id": int(existing["id"]),
                    "revision": int(existing["revision"]),
                    "changed": False,
                    "changed_fields": [],
                }

            changed_fields: list[str] = []
            if existing:
                previous = json.loads(existing["input_json"])
                changed_fields = sorted(
                    key
                    for key in set(previous) | set(input_data)
                    if previous.get(key) != input_data.get(key)
                )
                revision = int(existing["revision"]) + 1
                conn.execute(
                    """
                    UPDATE project_networks
                    SET revision=?,input_hash=?,input_json=?,changed_fields_json=?,updated_at=?
                    WHERE id=?
                    """,
                    (
                        revision,
                        digest,
                        serialized,
                        json.dumps(changed_fields, ensure_ascii=False),
                        now,
                        existing["id"],
                    ),
                )
                conn.execute(
                    "UPDATE network_calculation_runs SET stale=1 WHERE network_id=?",
                    (existing["id"],),
                )
                network_id = int(existing["id"])
            else:
                revision = 1
                changed_fields = sorted(input_data)
                cursor = conn.execute(
                    """
                    INSERT INTO project_networks
                    (project_id,revision,input_hash,input_json,changed_fields_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        project_id,
                        revision,
                        digest,
                        serialized,
                        json.dumps(changed_fields, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                network_id = int(cursor.lastrowid)
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
            return {
                "id": network_id,
                "revision": revision,
                "changed": True,
                "changed_fields": changed_fields,
            }

    def get_project_network(self, project_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_networks WHERE project_id=?", (project_id,)
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            data["input_json"] = json.loads(data["input_json"])
            data["changed_fields_json"] = json.loads(data["changed_fields_json"])
            return data

    def create_network_run(
        self,
        project_id: int,
        network: dict[str, Any],
        *,
        engine_version: str,
        task_mode: str,
        input_snapshot: dict[str, Any],
        derived: dict[str, Any],
        audit_result: dict[str, Any] | None,
        result: dict[str, Any],
        rule_snapshot: dict[str, dict[str, Any]],
    ) -> int:
        warnings = list(result.get("warnings", []))
        if audit_result:
            warnings.extend(audit_result.get("warnings", []))
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO network_calculation_runs
                (project_id,network_id,network_revision,engine_version,task_mode,input_snapshot,
                 derived_json,audit_json,result_json,rule_snapshot,warnings_json,status,
                 provisional_status,stale,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                """,
                (
                    project_id,
                    network["id"],
                    network["revision"],
                    engine_version,
                    task_mode,
                    self._canonical_json(input_snapshot),
                    self._canonical_json(derived),
                    self._canonical_json(audit_result or {}),
                    self._canonical_json(result),
                    self._canonical_json(rule_snapshot),
                    json.dumps(warnings, ensure_ascii=False),
                    result.get("status", "无法判断"),
                    result.get("provisional_status", "无法判断"),
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_network_runs(self, project_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM network_calculation_runs
                WHERE project_id=? ORDER BY id DESC LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_network_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*,p.code AS project_code,p.name AS project_name
                FROM network_calculation_runs r
                JOIN projects p ON p.id=r.project_id
                WHERE r.id=?
                """,
                (run_id,),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            for key in (
                "input_snapshot", "derived_json", "audit_json", "result_json",
                "rule_snapshot", "warnings_json",
            ):
                data[key] = json.loads(data[key])
            return data

    def save_project_motor(
        self, project_id: int, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        serialized = self._canonical_json(input_data)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        code = str(input_data.get("circuit_code", "M-001")).strip()
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM project_motors WHERE project_id=? AND circuit_code=?",
                (project_id, code),
            ).fetchone()
            if existing and existing["input_hash"] == digest:
                return {"id": int(existing["id"]), "revision": int(existing["revision"]), "changed": False, "changed_fields": []}
            if existing:
                previous = json.loads(existing["input_json"])
                changed = sorted(
                    key for key in set(previous) | set(input_data)
                    if previous.get(key) != input_data.get(key)
                )
                revision = int(existing["revision"]) + 1
                conn.execute(
                    "UPDATE project_motors SET revision=?,input_hash=?,input_json=?,changed_fields_json=?,updated_at=? WHERE id=?",
                    (revision, digest, serialized, json.dumps(changed, ensure_ascii=False), now, existing["id"]),
                )
                conn.execute(
                    "UPDATE motor_calculation_runs SET stale=1 WHERE motor_id=?",
                    (existing["id"],),
                )
                motor_id = int(existing["id"])
            else:
                revision = 1
                changed = sorted(input_data)
                cursor = conn.execute(
                    "INSERT INTO project_motors (project_id,circuit_code,revision,input_hash,input_json,changed_fields_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (project_id, code, revision, digest, serialized, json.dumps(changed, ensure_ascii=False), now, now),
                )
                motor_id = int(cursor.lastrowid)
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
            return {"id": motor_id, "revision": revision, "changed": True, "changed_fields": changed}

    def create_motor_run(
        self, project_id: int, motor: dict[str, Any], *, engine_version: str,
        input_snapshot: dict[str, Any], result: dict[str, Any],
        rule_snapshot: dict[str, dict[str, Any]],
    ) -> int:
        warnings = list(result.get("warnings", []))
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO motor_calculation_runs (project_id,motor_id,motor_revision,engine_version,input_snapshot,result_json,rule_snapshot,warnings_json,status,provisional_status,stale,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)",
                (project_id, motor["id"], motor["revision"], engine_version,
                 self._canonical_json(input_snapshot), self._canonical_json(result),
                 self._canonical_json(rule_snapshot), json.dumps(warnings, ensure_ascii=False),
                 result.get("status", "无法判断"), result.get("provisional_status", "无法判断"), utc_now()),
            )
            return int(cursor.lastrowid)

    def list_motor_runs(self, project_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM motor_calculation_runs WHERE project_id=? ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()]

    def get_motor_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT r.*,p.code AS project_code,p.name AS project_name FROM motor_calculation_runs r JOIN projects p ON p.id=r.project_id WHERE r.id=?",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            for key in ("input_snapshot", "result_json", "rule_snapshot", "warnings_json"):
                data[key] = json.loads(data[key])
            return data
