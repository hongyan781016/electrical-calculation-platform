"""图纸只读提取、证据化候选与人工确认适配。

本模块不执行电气计算，也不把识别置信度当作工程批准。只有经用户
确认的字段才会进入既有完整回路表单，随后仍由 ``network_input`` 和
V0.7 计算内核完成校验与计算。
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable


class DrawingImportError(ValueError):
    """图纸无法安全提取时返回给用户的可操作错误。"""


class EvidenceState(str, Enum):
    EXTRACTED = "extracted"
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    MISSING = "missing"


@dataclass(frozen=True)
class DrawingSource:
    filename: str
    format: str
    sha256: str
    size_bytes: int
    parser_name: str
    parser_version: str


@dataclass(frozen=True)
class DrawingEntityEvidence:
    evidence_id: str
    locator: str
    entity_type: str
    raw_text: str
    layer: str = ""
    block_name: str = ""
    handle: str = ""
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class ExtractedFact:
    field_name: str
    label: str
    value: str
    evidence_ids: tuple[str, ...]
    confidence: float
    state: EvidenceState = EvidenceState.PENDING_CONFIRMATION
    region_id: str = ""


@dataclass(frozen=True)
class TopologyCandidate:
    from_code: str
    to_code: str
    evidence_ids: tuple[str, ...]
    confidence: float
    state: EvidenceState = EvidenceState.PENDING_CONFIRMATION


@dataclass(frozen=True)
class ExtractionBundle:
    source: DrawingSource
    evidence: tuple[DrawingEntityEvidence, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class DrawingRegion:
    region_id: str
    drawing_number: str
    title: str
    region_type: str
    anchor_evidence_id: str
    evidence_ids: tuple[str, ...]
    min_x: float
    min_y: float
    max_x: float
    max_y: float


@dataclass(frozen=True)
class PanelRegion:
    panel_id: str
    drawing_region_id: str
    panel_code: str
    anchor_evidence_id: str
    evidence_ids: tuple[str, ...]
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    installed_power_kw: str = ""
    design_current_a: str = ""
    incoming_breaker_spec: str = ""
    incoming_breaker_current_a: str = ""
    incoming_breaker_frame_a: str = ""
    field_evidence: dict[str, tuple[str, ...]] | None = None


@dataclass(frozen=True)
class CircuitRowCandidate:
    row_id: str
    drawing_region_id: str
    panel_id: str
    panel_code: str
    circuit_code: str
    breaker_spec: str
    load_kw: str
    destination: str
    cable_spec: str
    evidence_ids: tuple[str, ...]
    confidence: float
    circuit_evidence_ids: tuple[str, ...] = ()
    breaker_evidence_ids: tuple[str, ...] = ()
    load_evidence_ids: tuple[str, ...] = ()
    destination_evidence_ids: tuple[str, ...] = ()
    cable_evidence_ids: tuple[str, ...] = ()
    state: EvidenceState = EvidenceState.PENDING_CONFIRMATION
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeederRowCandidate:
    feeder_id: str
    drawing_region_id: str
    cabinet_code: str
    feeder_code: str
    feeder_name: str
    destination_panel_code: str
    breaker_spec: str
    breaker_current_a: str
    load_kw: str
    design_current_a: str
    cable_spec: str
    evidence_ids: tuple[str, ...]
    field_evidence: dict[str, tuple[str, ...]]
    confidence: float
    state: EvidenceState = EvidenceState.PENDING_CONFIRMATION
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransformerCandidate:
    transformer_id: str
    drawing_region_id: str
    transformer_code: str
    actual_model: str
    capacity_kva: str
    uk_percent: str
    voltage_text: str
    incoming_cabinet_code: str
    busbar_spec: str
    main_breaker_spec: str
    main_breaker_current_a: str
    evidence_ids: tuple[str, ...]
    field_evidence: dict[str, tuple[str, ...]]
    confidence: float
    state: EvidenceState = EvidenceState.PENDING_CONFIRMATION
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateNetwork:
    source: DrawingSource
    evidence: tuple[DrawingEntityEvidence, ...]
    regions: tuple[DrawingRegion, ...]
    panels: tuple[PanelRegion, ...]
    circuit_rows: tuple[CircuitRowCandidate, ...]
    feeders: tuple[FeederRowCandidate, ...]
    transformers: tuple[TransformerCandidate, ...]
    facts: tuple[ExtractedFact, ...]
    topology: tuple[TopologyCandidate, ...]
    conflicts: tuple[str, ...]
    missing_fields: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class ConfirmationDecision:
    field_name: str
    accepted: bool
    value: str


@dataclass(frozen=True)
class ConfirmedDrawingModel:
    source: DrawingSource
    values: dict[str, str]
    evidence_by_field: dict[str, tuple[str, ...]]
    rejected_fields: tuple[str, ...]


SUPPORTED_EXTENSIONS = {".dxf", ".pdf", ".dwg"}
REQUIRED_CALCULATION_FIELDS = (
    "circuit_code",
    "transformer_capacity_kva",
    "transformer_uk_percent",
    "load_value",
)

FIELD_LABELS = {
    "circuit_code": "回路编号",
    "circuit_name": "回路名称",
    "transformer_code": "变压器编号",
    "transformer_actual_model": "变压器实际型号",
    "transformer_family": "阻抗计算参考系列",
    "transformer_capacity_kva": "变压器容量（kVA）",
    "transformer_uk_percent": "变压器阻抗电压 uk（%）",
    "upstream_short_circuit_capacity_mva": "上级系统短路容量（MVA）",
    "bus_section_code": "低压母线段编号",
    "feeder_cabinet_code": "馈线柜编号",
    "load_value": "末端负荷功率（kW）",
    "load_kind": "负荷类型",
    "power_factor": "功率因数",
    "length_connection": "出口连接段长度（m）",
    "length_feeder": "馈线段长度（m）",
    "length_final": "末端分支长度（m）",
    "existing_section_connection": "出口连接段相线截面（mm²）",
    "existing_section_feeder": "馈线段相线截面（mm²）",
    "existing_section_final": "末端分支相线截面（mm²）",
    "existing_pe_section_final": "末端分支PE截面（mm²）",
    "breaker_designation_final": "末端分支断路器标注",
    "breaker_in_final": "末端分支断路器额定电流（A）",
    "mcb_trip_curve_final": "末端分支MCB脱扣曲线",
    "terminal_phase": "末端分支相制",
    "configuration_final": "末端分支导体结构",
    "configuration_feeder": "馈线段导体结构",
    "breaker_frame_final": "末端分支断路器壳架电流（A）",
    "upstream_design_current_a": "配电箱进线计算电流（A）",
    "breaker_designation_feeder": "馈线段断路器标注",
    "breaker_in_feeder": "馈线段断路器额定电流（A）",
    "breaker_frame_feeder": "馈线段断路器壳架电流（A）",
}


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def parser_capabilities() -> dict[str, dict[str, Any]]:
    """返回本机实际可用后端；能力探测不打开或修改任何图纸。"""

    autocad_core_path = _autocad_core_console()
    autocad_core = str(autocad_core_path) if autocad_core_path else None
    return {
        "dxf": {"available": True, "backend": "内置ASCII DXF只读提取器"},
        "pdf": {
            "available": importlib.util.find_spec("pypdf") is not None,
            "backend": "pypdf文字提取" if importlib.util.find_spec("pypdf") else "未安装pypdf",
        },
        "dwg": {
            "available": autocad_core is not None,
            "backend_detected": autocad_core is not None,
            "backend": autocad_core or "未发现AutoCAD Core Console或ODA转换器",
            "note": "通过临时副本转换为ASCII DXF；原DWG只读且不会保存。",
        },
    }


def extract_drawing_bytes(filename: str, content: bytes) -> ExtractionBundle:
    """从上传内容生成只读证据包。"""

    safe_name = Path(filename or "").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DrawingImportError("当前只接受DWG、DXF或PDF图纸。")
    if not content:
        raise DrawingImportError("图纸文件为空。")
    digest = hashlib.sha256(content).hexdigest()
    if suffix == ".dxf":
        evidence = _extract_ascii_dxf(content)
        parser_name, parser_version = "builtin-ascii-dxf", "1"
        warnings: tuple[str, ...] = ()
    elif suffix == ".pdf":
        evidence = _extract_pdf(content)
        parser_name, parser_version = "pypdf", "1"
        warnings = (
            "PDF仅提取文字层；扫描页需要OCR，OCR结果必须人工确认。",
        )
    else:
        converted = _convert_dwg_to_ascii_dxf(content)
        evidence = _extract_ascii_dxf(converted)
        parser_name, parser_version = "autocad-core-console+dxf", "1"
        warnings = (
            "DWG通过临时副本转换；原图未保存。外部参照中的内容不保证随主图一并提取。",
        )
    if not evidence:
        raise DrawingImportError("没有提取到可定位的文字或块属性，不能生成候选。")
    return ExtractionBundle(
        source=DrawingSource(
            filename=safe_name,
            format=suffix[1:].upper(),
            sha256=digest,
            size_bytes=len(content),
            parser_name=parser_name,
            parser_version=parser_version,
        ),
        evidence=tuple(evidence),
        warnings=warnings,
    )


def _autocad_core_console() -> Path | None:
    discovered = shutil.which("accoreconsole.exe")
    if discovered:
        return Path(discovered)
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates = sorted(
        program_files.glob(r"Autodesk\AutoCAD *\accoreconsole.exe"),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _convert_dwg_to_ascii_dxf(content: bytes) -> bytes:
    executable = _autocad_core_console()
    if executable is None:
        raise DrawingImportError(
            "未发现AutoCAD Core Console。请安装可用的AutoCAD，或将DWG另存为ASCII DXF后导入。"
        )
    # AutoCAD Core Console对含非ASCII字符的临时路径兼容性不稳定，优先
    # 使用Windows系统临时目录，并为输入、输出采用纯ASCII文件名。
    system_temp = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"
    temp_parent = system_temp if system_temp.is_dir() else None
    try:
        with tempfile.TemporaryDirectory(
            prefix="electrical-calc-dwg-",
            dir=str(temp_parent) if temp_parent else None,
        ) as folder:
            work = Path(folder)
            source = work / "source.dwg"
            target = work / "extracted.dxf"
            script = work / "extract.scr"
            source.write_bytes(content)
            # FILEDIA=0使DXFOUT完全走命令行；16为ASCII DXF精度。
            script.write_text(
                "FILEDIA\n0\nCMDECHO\n0\n_.DXFOUT\n"
                f'"{target}"\n16\n_.QUIT\n_Y\n',
                encoding="ascii",
            )
            completed = subprocess.run(
                [
                    str(executable),
                    "/i",
                    str(source),
                    "/s",
                    str(script),
                    "/l",
                    "en-US",
                ],
                cwd=work,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0 or not target.exists():
                details = "\n".join(
                    (completed.stdout + "\n" + completed.stderr).splitlines()[-8:]
                ).strip()
                raise DrawingImportError(
                    "AutoCAD未能生成临时DXF。"
                    + (f"末尾日志：{details}" if details else "请确认图纸可在AutoCAD中正常打开。")
                )
            converted = target.read_bytes()
            if not converted:
                raise DrawingImportError("AutoCAD生成的临时DXF为空。")
            return converted
    except PermissionError as exc:
        raise DrawingImportError("无法建立DWG转换临时目录，请检查Windows临时目录权限。") from exc
    except subprocess.TimeoutExpired as exc:
        raise DrawingImportError("DWG转换超过120秒，已停止；请拆分图纸或另存为DXF。") from exc


def _decode_dxf(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DrawingImportError("DXF文字编码无法识别，请从CAD另存为UTF-8或ANSI DXF。")


def _extract_ascii_dxf(content: bytes) -> list[DrawingEntityEvidence]:
    text = _decode_dxf(content)
    lines = [line.rstrip("\r") for line in text.splitlines()]
    if len(lines) < 4 or len(lines) % 2:
        raise DrawingImportError("DXF组码结构不完整，可能是二进制DXF。")
    pairs = [(lines[index].strip(), lines[index + 1]) for index in range(0, len(lines), 2)]
    records: list[tuple[str, list[tuple[str, str]]]] = []
    current_type = ""
    current: list[tuple[str, str]] = []
    in_entities = False
    for code, value in pairs:
        normalized = value.strip().upper()
        if code == "0" and normalized == "SECTION":
            continue
        if code == "2" and normalized == "ENTITIES" and not current_type:
            in_entities = True
            continue
        if code == "0" and normalized == "ENDSEC":
            if current_type:
                records.append((current_type, current))
            current_type, current, in_entities = "", [], False
            continue
        if not in_entities:
            continue
        if code == "0":
            if current_type:
                records.append((current_type, current))
            current_type, current = normalized, []
        elif current_type:
            current.append((code, value.strip()))

    evidence: list[DrawingEntityEvidence] = []
    for entity_type, values in records:
        if entity_type not in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "INSERT"}:
            continue
        grouped: dict[str, list[str]] = {}
        for code, value in values:
            grouped.setdefault(code, []).append(value)
        raw_text = "".join(grouped.get("3", [])) + "".join(grouped.get("1", []))
        block_name = (grouped.get("2") or [""])[0]
        if entity_type == "INSERT" and not raw_text:
            raw_text = block_name
        raw_text = _clean_cad_text(raw_text)
        if not raw_text:
            continue
        evidence.append(
            DrawingEntityEvidence(
                evidence_id=f"E{len(evidence) + 1:05d}",
                locator=f"Model/实体{len(evidence) + 1}",
                entity_type=entity_type,
                raw_text=raw_text,
                layer=(grouped.get("8") or [""])[0],
                block_name=block_name,
                handle=(grouped.get("5") or [""])[0],
                x=_first_float(grouped.get("10")),
                y=_first_float(grouped.get("20")),
            )
        )
    return evidence


def _extract_pdf(content: bytes) -> list[DrawingEntityEvidence]:
    if importlib.util.find_spec("pypdf") is None:
        raise DrawingImportError("PDF文字提取需要安装pypdf；当前环境尚未安装。")
    from pypdf import PdfReader  # type: ignore[import-not-found]

    evidence: list[DrawingEntityEvidence] = []
    try:
        reader = PdfReader(BytesIO(content))
        for page_index, page in enumerate(reader.pages, start=1):
            for line_index, line in enumerate((page.extract_text() or "").splitlines(), start=1):
                cleaned = " ".join(line.split())
                if cleaned:
                    evidence.append(
                        DrawingEntityEvidence(
                            evidence_id=f"E{len(evidence) + 1:05d}",
                            locator=f"PDF第{page_index}页/文字{line_index}",
                            entity_type="PDF_TEXT",
                            raw_text=cleaned,
                        )
                    )
    except Exception as exc:  # pypdf会为损坏或加密PDF抛出多种异常
        raise DrawingImportError(f"PDF无法读取：{exc}") from exc
    return evidence


def _clean_cad_text(value: str) -> str:
    value = value.replace("\\P", " ").replace("%%d", "°")
    value = re.sub(r"\\[A-Za-z][^;]*;", "", value)
    return " ".join(value.split())


def _first_float(values: list[str] | None) -> float | None:
    if not values:
        return None
    try:
        return float(values[0])
    except ValueError:
        return None


def interpret_electrical(bundle: ExtractionBundle) -> CandidateNetwork:
    """从证据生成保守候选；不确认任何事实。"""

    regions = group_drawing_regions(bundle)
    panels = group_panel_regions(bundle, regions)
    circuit_rows = extract_circuit_rows(bundle, panels)
    feeders = extract_feeder_rows(bundle, regions)
    transformers = extract_transformer_candidates(bundle, regions)
    evidence_region = {
        evidence_id: region.region_id
        for region in regions
        for evidence_id in region.evidence_ids
    }
    found: dict[str, list[tuple[str, str, float, str]]] = {}
    topology: list[TopologyCandidate] = []

    def add(field: str, value: str, evidence_id: str, confidence: float) -> None:
        normalized = value.strip()
        if normalized:
            found.setdefault(field, []).append(
                (normalized, evidence_id, confidence, evidence_region.get(evidence_id, ""))
            )

    for item in bundle.evidence:
        text = item.raw_text
        upper = text.upper().replace("％", "%")
        transformer = re.search(r"\b(SCB\d+(?:-[A-Z]+)?|S11(?:-M)?)\b", upper)
        if transformer:
            model = transformer.group(1)
            add("transformer_actual_model", model, item.evidence_id, 0.95)
            if model.startswith("SCB11"):
                add("transformer_family", "scb11", item.evidence_id, 0.9)
            elif model.startswith("S11"):
                add("transformer_family", "s11_m", item.evidence_id, 0.9)
        capacity = re.search(r"(?<![.\d])(\d{2,5}(?:\.\d+)?)\s*KVA\b", upper)
        if capacity:
            add("transformer_capacity_kva", capacity.group(1), item.evidence_id, 0.95)
        uk = re.search(r"(?:UK\s*%?|阻抗电压)\s*(?:=|:|：)?\s*(\d+(?:\.\d+)?)\s*%", upper.replace("%%%", "%"))
        if uk:
            add("transformer_uk_percent", uk.group(1), item.evidence_id, 0.95)
        ssc = re.search(r"(?:SSC|短路容量)\s*(?:=|:|：)?\s*(\d+(?:\.\d+)?)\s*MVA", upper)
        if ssc:
            add("upstream_short_circuit_capacity_mva", ssc.group(1), item.evidence_id, 0.95)
        pf = re.search(r"(?:COS\s*[ΦФ]|功率因数)\s*(?:=|:|：)?\s*(0(?:\.\d+)?|1(?:\.0+)?)", upper)
        if pf:
            add("power_factor", pf.group(1), item.evidence_id, 0.9)
        load = re.search(r"(?<![.\d])(\d+(?:\.\d+)?)\s*KW\b", upper)
        if load and not transformer:
            add("load_value", load.group(1), item.evidence_id, 0.75)

        _extract_identifiers(text, item.evidence_id, add)
        _extract_segment_values(text, item.evidence_id, add)

        relation = re.search(
            r"([A-Z0-9#_.-]{2,})\s*(?:→|->|至|引至)\s*([A-Z0-9#_.-]{2,})",
            upper,
        )
        if relation:
            topology.append(
                TopologyCandidate(
                    relation.group(1),
                    relation.group(2),
                    (item.evidence_id,),
                    0.95,
                )
            )

    facts: list[ExtractedFact] = []
    conflicts: list[str] = []
    for field_name, candidates in found.items():
        grouped: dict[tuple[str, str], list[tuple[str, float]]] = {}
        for value, evidence_id, confidence, region_id in candidates:
            grouped.setdefault((region_id, value), []).append((evidence_id, confidence))
        if len(grouped) > 1:
            conflicts.append(
                f"{FIELD_LABELS.get(field_name, field_name)}存在多个候选："
                + "、".join(value for _, value in grouped)
            )
        for (region_id, value), entries in grouped.items():
            facts.append(
                ExtractedFact(
                    field_name=field_name,
                    label=FIELD_LABELS.get(field_name, field_name),
                    value=value,
                    evidence_ids=tuple(dict.fromkeys(entry[0] for entry in entries)),
                    confidence=max(entry[1] for entry in entries),
                    state=(EvidenceState.CONFLICT if len(grouped) > 1 else EvidenceState.PENDING_CONFIRMATION),
                    region_id=region_id,
                )
            )
    found_fields = set(found)
    missing = tuple(FIELD_LABELS[field] for field in REQUIRED_CALCULATION_FIELDS if field not in found_fields)
    return CandidateNetwork(
        source=bundle.source,
        evidence=bundle.evidence,
        regions=regions,
        panels=panels,
        circuit_rows=circuit_rows,
        feeders=feeders,
        transformers=transformers,
        facts=tuple(sorted(facts, key=lambda fact: (fact.field_name, fact.value))),
        topology=tuple(topology),
        conflicts=tuple(conflicts),
        missing_fields=missing,
        warnings=bundle.warnings,
    )


def group_drawing_regions(bundle: ExtractionBundle) -> tuple[DrawingRegion, ...]:
    """按标题栏图号将模型空间证据分配到最近图纸区域。

    该步骤只使用显式标题栏属性和实体坐标。没有图号属性时不虚构图框，
    返回空集合并继续保留全图证据。
    """

    anchors = [
        item
        for item in bundle.evidence
        if item.entity_type in {"ATTRIB", "ATTDEF"}
        and item.block_name.strip().upper() in {"图号", "DRAWING_NO", "DRAWING NUMBER"}
        and item.raw_text.strip()
        and item.x is not None
        and item.y is not None
    ]
    if not anchors:
        return ()
    titles = [
        item
        for item in bundle.evidence
        if item.entity_type in {"ATTRIB", "ATTDEF"}
        and (
            item.block_name.strip().startswith("图名")
            or item.block_name.strip().upper() in {"DRAWING_NAME", "DRAWING TITLE"}
        )
        and item.raw_text.strip()
        and item.x is not None
        and item.y is not None
    ]
    members: dict[str, list[DrawingEntityEvidence]] = {anchor.evidence_id: [] for anchor in anchors}
    # 图号属性通常位于图框右下角。先从同一水平排布的相邻图号推定图宽，
    # 再按“实体位于图号左侧且上方”分配；这比把图号当图纸中心做Voronoi
    # 更符合CAD成组排图，也可避免下一张图左半区落入上一张图。
    row_groups: list[list[DrawingEntityEvidence]] = []
    for anchor in sorted(anchors, key=lambda item: (float(item.y), float(item.x))):
        group = next(
            (items for items in row_groups if abs(float(items[0].y) - float(anchor.y)) <= 3000),
            None,
        )
        if group is None:
            group = []
            row_groups.append(group)
        group.append(anchor)
    inferred_width: dict[str, float] = {}
    for group in row_groups:
        ordered = sorted(group, key=lambda item: float(item.x))
        for index, anchor in enumerate(ordered):
            distances = []
            if index:
                distances.append(float(anchor.x) - float(ordered[index - 1].x))
            if index + 1 < len(ordered):
                distances.append(float(ordered[index + 1].x) - float(anchor.x))
            inferred_width[anchor.evidence_id] = min(distances) if distances else 100000.0
    for item in bundle.evidence:
        if item.x is None or item.y is None:
            continue
        box_candidates = []
        for anchor in anchors:
            width = inferred_width[anchor.evidence_id]
            if not (float(anchor.x) - width * 0.99 <= float(item.x) <= float(anchor.x) + width * 0.03):
                continue
            vertical_offset = float(item.y) - float(anchor.y)
            if vertical_offset >= -5000:
                box_candidates.append((vertical_offset, abs(float(item.x) - float(anchor.x)), anchor))
        nearest = (
            min(box_candidates, key=lambda entry: (entry[0], entry[1]))[2]
            if box_candidates
            else min(
                anchors,
                key=lambda anchor: (item.x - float(anchor.x)) ** 2
                + (item.y - float(anchor.y)) ** 2,
            )
        )
        members[nearest.evidence_id].append(item)

    result: list[DrawingRegion] = []
    used_ids: dict[str, int] = {}
    for anchor in anchors:
        region_members = members[anchor.evidence_id]
        nearest_titles = sorted(
            titles,
            key=lambda title: (float(title.x) - float(anchor.x)) ** 2
            + (float(title.y) - float(anchor.y)) ** 2,
        )
        title = nearest_titles[0].raw_text if nearest_titles else "未识别图名"
        base_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", anchor.raw_text).strip("-") or anchor.evidence_id
        used_ids[base_id] = used_ids.get(base_id, 0) + 1
        region_id = base_id if used_ids[base_id] == 1 else f"{base_id}-{used_ids[base_id]}"
        xs = [float(item.x) for item in region_members if item.x is not None]
        ys = [float(item.y) for item in region_members if item.y is not None]
        result.append(
            DrawingRegion(
                region_id=region_id,
                drawing_number=anchor.raw_text,
                title=title,
                region_type=_classify_drawing_title(title),
                anchor_evidence_id=anchor.evidence_id,
                evidence_ids=tuple(item.evidence_id for item in region_members),
                min_x=min(xs) if xs else float(anchor.x),
                min_y=min(ys) if ys else float(anchor.y),
                max_x=max(xs) if xs else float(anchor.x),
                max_y=max(ys) if ys else float(anchor.y),
            )
        )
    return tuple(sorted(result, key=lambda item: (item.drawing_number, item.region_id)))


def _classify_drawing_title(title: str) -> str:
    if "配电箱" in title and "系统图" in title:
        return "distribution_board_system"
    if "低压" in title and "系统图" in title:
        return "low_voltage_system"
    if "干线" in title and "系统图" in title:
        return "riser_system"
    if "系统图" in title:
        return "other_system"
    if "平面图" in title:
        return "plan"
    return "other"


_PANEL_CODE = re.compile(
    r"^(?:\d+[#-]?)?(?:ALE|APE|MCC|AL|AT|AP|AA)[A-Z0-9#.-]*$",
    re.IGNORECASE,
)
_CIRCUIT_ROW_CODE = re.compile(r"^W[A-Z]{0,3}\d+(?:-\d+)?$", re.IGNORECASE)


def group_panel_regions(
    bundle: ExtractionBundle,
    regions: tuple[DrawingRegion, ...] | None = None,
) -> tuple[PanelRegion, ...]:
    """在配电箱系统图中识别有 ``Pe=``、``Ijs=`` 支撑的配电箱表头。

    仅凭一个类似设备编号的文字不足以建立配电箱节点；附近同时出现负荷和
    计算电流标签，才把它作为锚点。随后按坐标最近原则划分本图内证据。
    """

    drawing_regions = regions if regions is not None else group_drawing_regions(bundle)
    evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
    result: list[PanelRegion] = []
    used_ids: dict[str, int] = {}
    for region in drawing_regions:
        if region.region_type != "distribution_board_system":
            continue
        members = [
            evidence_by_id[evidence_id]
            for evidence_id in region.evidence_ids
            if evidence_id in evidence_by_id
            and evidence_by_id[evidence_id].x is not None
            and evidence_by_id[evidence_id].y is not None
        ]
        pe_labels = [item for item in members if re.fullmatch(r"P[ej]\s*=", item.raw_text, re.I)]
        ijs_labels = [item for item in members if re.fullmatch(r"Ijs\s*=", item.raw_text, re.I)]
        anchors: list[DrawingEntityEvidence] = []
        for item in members:
            code = item.raw_text.strip().upper()
            if not _PANEL_CODE.fullmatch(code):
                continue
            has_pe = any(
                abs(float(label.x) - float(item.x)) <= 3000
                and abs(float(label.y) - float(item.y)) <= 1800
                for label in pe_labels
            )
            has_ijs = any(
                abs(float(label.x) - float(item.x)) <= 3000
                and abs(float(label.y) - float(item.y)) <= 1800
                for label in ijs_labels
            )
            if has_pe and has_ijs:
                anchors.append(item)
        # 同一表头可能由重复文字实体组成，只保留坐标近邻中的第一个。
        deduplicated: list[DrawingEntityEvidence] = []
        for item in sorted(anchors, key=lambda value: (str(value.raw_text), -float(value.y), float(value.x))):
            if any(
                other.raw_text.upper() == item.raw_text.upper()
                and abs(float(other.x) - float(item.x)) < 100
                and abs(float(other.y) - float(item.y)) < 100
                for other in deduplicated
            ):
                continue
            deduplicated.append(item)
        assigned: dict[str, list[DrawingEntityEvidence]] = {
            anchor.evidence_id: [] for anchor in deduplicated
        }
        for item in members:
            if not deduplicated:
                break
            # 配电箱系统图通常从表头向下展开支路。对同一列，优先归入
            # 位于实体上方且最近的表头，避免两张上下相邻的箱表在中点串行。
            anchors_above = [
                # 首行支路编号在本样式中可比箱编号高约360个图形单位。
                anchor for anchor in deduplicated if float(anchor.y) >= float(item.y) - 600
            ]
            candidates = anchors_above or deduplicated
            nearest = min(
                candidates,
                key=lambda anchor: abs(float(item.x) - float(anchor.x))
                + abs(float(item.y) - float(anchor.y)),
            )
            assigned[nearest.evidence_id].append(item)
        for anchor in deduplicated:
            panel_members = assigned[anchor.evidence_id]
            xs = [float(item.x) for item in panel_members]
            ys = [float(item.y) for item in panel_members]
            header_members = [
                item
                for item in panel_members
                if abs(float(item.x) - float(anchor.x)) <= 3000
                and abs(float(item.y) - float(anchor.y)) <= 1800
            ]
            power_pairs = tuple(
                (match.group(1), item.evidence_id)
                for item in header_members
                if (match := re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*kW\s*", item.raw_text, re.I))
            )
            current_pairs = tuple(
                (match.group(1), item.evidence_id)
                for item in header_members
                if (match := re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*A\s*", item.raw_text, re.I))
            )
            power_values = _unique_text(value for value, _ in power_pairs)
            current_values = _unique_text(value for value, _ in current_pairs)
            incoming_breaker_pairs = tuple(
                (item.raw_text.strip(), item.evidence_id)
                for item in panel_members
                if abs(float(item.x) - float(anchor.x)) <= 3000
                and float(anchor.y) - 9000 <= float(item.y) <= float(anchor.y) + 500
                and re.search(
                    r"(?:CM|NX|NSX|CVS|DZ)[A-Z0-9-]*/(?:3|4)P/\d+(?:\.\d+)?A$",
                    item.raw_text,
                    re.I,
                )
            )
            incoming_breaker_values = _unique_text(value for value, _ in incoming_breaker_pairs)
            incoming_current = ""
            incoming_frame = ""
            if len(incoming_breaker_values) == 1:
                current_match = re.search(r"/(\d+(?:\.\d+)?)A$", incoming_breaker_values[0], re.I)
                frame_match = re.search(r"-(\d+)[A-Z]?(?:/|$)", incoming_breaker_values[0], re.I)
                incoming_current = current_match.group(1) if current_match else ""
                incoming_frame = frame_match.group(1) if frame_match else ""
            base_id = f"{region.region_id}:{anchor.raw_text.upper()}"
            used_ids[base_id] = used_ids.get(base_id, 0) + 1
            panel_id = base_id if used_ids[base_id] == 1 else f"{base_id}:{used_ids[base_id]}"
            result.append(
                PanelRegion(
                    panel_id=panel_id,
                    drawing_region_id=region.region_id,
                    panel_code=anchor.raw_text,
                    anchor_evidence_id=anchor.evidence_id,
                    evidence_ids=tuple(item.evidence_id for item in panel_members),
                    min_x=min(xs),
                    min_y=min(ys),
                    max_x=max(xs),
                    max_y=max(ys),
                    installed_power_kw=power_values[0] if len(power_values) == 1 else "",
                    design_current_a=current_values[0] if len(current_values) == 1 else "",
                    incoming_breaker_spec=(
                        incoming_breaker_values[0] if len(incoming_breaker_values) == 1 else ""
                    ),
                    incoming_breaker_current_a=incoming_current,
                    incoming_breaker_frame_a=incoming_frame,
                    field_evidence={
                        "code": (anchor.evidence_id,),
                        "power": _evidence_for_unique_value(power_pairs, power_values),
                        "design_current": _evidence_for_unique_value(current_pairs, current_values),
                        "incoming_breaker": _evidence_for_unique_value(
                            incoming_breaker_pairs, incoming_breaker_values
                        ),
                    },
                )
            )
    return tuple(sorted(result, key=lambda item: (item.drawing_region_id, item.panel_code, item.panel_id)))


def extract_circuit_rows(
    bundle: ExtractionBundle,
    panels: tuple[PanelRegion, ...],
) -> tuple[CircuitRowCandidate, ...]:
    """按分支编号的水平行提取断路器、负荷、用途和电缆候选。"""

    evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
    rows: list[CircuitRowCandidate] = []
    for panel in panels:
        members = [
            evidence_by_id[evidence_id]
            for evidence_id in panel.evidence_ids
            if evidence_id in evidence_by_id
            and evidence_by_id[evidence_id].x is not None
            and evidence_by_id[evidence_id].y is not None
        ]
        anchors = [item for item in members if _CIRCUIT_ROW_CODE.fullmatch(item.raw_text.strip())]
        seen: set[tuple[str, int]] = set()
        for anchor in sorted(anchors, key=lambda item: (-float(item.y), float(item.x))):
            row_key = (anchor.raw_text.upper(), round(float(anchor.y) / 100))
            if row_key in seen:
                continue
            seen.add(row_key)
            band = [
                item
                for item in members
                if abs(float(item.y) - float(anchor.y)) <= 450
                and float(anchor.x) - 6000 <= float(item.x) <= float(anchor.x) + 9000
            ]
            band.sort(key=lambda item: float(item.x))
            breaker_pairs = tuple(
                (item.raw_text.strip(), item.evidence_id)
                for item in band
                if _looks_like_breaker(item.raw_text)
            )
            breaker_values = _unique_text(value for value, _ in breaker_pairs)
            load_pairs = tuple(
                (match.group(1), item.evidence_id)
                for item in band
                if (match := re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*kW\s*", item.raw_text, re.I))
            )
            load_values = _unique_text(value for value, _ in load_pairs)
            cable_pairs = tuple(
                (item.raw_text.strip(), item.evidence_id)
                for item in band
                if re.search(r"(?:YJ[LVY]|BVV?|WDZ|ZC-)", item.raw_text, re.I)
                and ("*" in item.raw_text or "×" in item.raw_text or "X" in item.raw_text.upper())
            )
            cable_values = _unique_text(value for value, _ in cable_pairs)
            destination_pairs = _destination_candidates(band, anchor, cable_values)
            destination_values = _unique_text(value for value, _ in destination_pairs)
            conflicts: list[str] = []
            for label, values in (
                ("断路器", breaker_values),
                ("负荷", load_values),
                ("电缆", cable_values),
                ("用途", destination_values),
            ):
                if len(values) > 1:
                    conflicts.append(f"{label}存在多个同行候选：{'、'.join(values)}")
            populated = sum(bool(values) for values in (breaker_values, load_values, cable_values))
            rows.append(
                CircuitRowCandidate(
                    row_id=f"{panel.panel_id}:{anchor.raw_text}:{anchor.evidence_id}",
                    drawing_region_id=panel.drawing_region_id,
                    panel_id=panel.panel_id,
                    panel_code=panel.panel_code,
                    circuit_code=anchor.raw_text,
                    breaker_spec=breaker_values[0] if len(breaker_values) == 1 else "",
                    load_kw=load_values[0] if len(load_values) == 1 else "",
                    destination=destination_values[0] if len(destination_values) == 1 else "",
                    cable_spec=cable_values[0] if len(cable_values) == 1 else "",
                    evidence_ids=tuple(item.evidence_id for item in band),
                    confidence=min(0.95, 0.55 + populated * 0.12),
                    circuit_evidence_ids=(anchor.evidence_id,),
                    breaker_evidence_ids=_evidence_for_unique_value(breaker_pairs, breaker_values),
                    load_evidence_ids=_evidence_for_unique_value(load_pairs, load_values),
                    destination_evidence_ids=_evidence_for_unique_value(destination_pairs, destination_values),
                    cable_evidence_ids=_evidence_for_unique_value(cable_pairs, cable_values),
                    state=EvidenceState.CONFLICT if conflicts else EvidenceState.PENDING_CONFIRMATION,
                    conflicts=tuple(conflicts),
                )
            )
    return tuple(rows)


_FEEDER_CODE = re.compile(r"^([A-Z]+\d+)-(\d+)\s*:\s*(.*)$", re.IGNORECASE)


def extract_feeder_rows(
    bundle: ExtractionBundle,
    regions: tuple[DrawingRegion, ...] | None = None,
) -> tuple[FeederRowCandidate, ...]:
    """从低压柜竖列的一个馈线单元提取上游候选。

    馈线编号自身给出柜号前缀；目的配电箱、断路器、电流、负荷和电缆
    必须落在同一竖列带内。这里只生成候选，不据坐标自动批准连接。
    """

    drawing_regions = regions if regions is not None else group_drawing_regions(bundle)
    region_by_evidence = {
        evidence_id: region.region_id
        for region in drawing_regions
        for evidence_id in region.evidence_ids
    }
    positioned = [item for item in bundle.evidence if item.x is not None and item.y is not None]
    rows: list[FeederRowCandidate] = []
    for anchor in positioned:
        match = _FEEDER_CODE.fullmatch(anchor.raw_text.strip())
        if not match:
            continue
        cabinet_code = match.group(1).upper()
        feeder_code = f"{cabinet_code}-{match.group(2)}"
        feeder_name = match.group(3).strip()
        band = [
            item
            for item in positioned
            if abs(float(item.x) - float(anchor.x)) <= 6000
            and float(anchor.y) - 1700 <= float(item.y) <= float(anchor.y) + 250
        ]
        breaker_pairs = tuple(
            (item.raw_text.strip(), item.evidence_id)
            for item in band
            if _looks_like_breaker(item.raw_text)
        )
        breaker_values = _unique_text(value for value, _ in breaker_pairs)
        current_pairs = tuple(
            (m.group(1), item.evidence_id)
            for item in band
            if (m := re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*A\s*", item.raw_text, re.I))
        )
        current_values = _unique_text(value for value, _ in current_pairs)
        combined_pairs = tuple(
            ((m.group(1), m.group(2)), item.evidence_id)
            for item in band
            if (m := re.search(
                r"P\s*=\s*(\d+(?:\.\d+)?)\s*kW.*?Ijs\s*=\s*(\d+(?:\.\d+)?)\s*A",
                item.raw_text,
                re.I,
            ))
        )
        load_values = _unique_text(pair[0][0] for pair in combined_pairs)
        design_current_values = _unique_text(pair[0][1] for pair in combined_pairs)
        cable_pairs = tuple(
            (item.raw_text.strip(), item.evidence_id)
            for item in band
            if re.search(r"(?:YJ[LVY]|BVV?|WDZ|ZC-)", item.raw_text, re.I)
            and ("*" in item.raw_text or "×" in item.raw_text or "X" in item.raw_text.upper())
        )
        cable_values = _unique_text(value for value, _ in cable_pairs)
        destination_pairs = tuple(
            (item.raw_text.strip(), item.evidence_id)
            for item in band
            if _PANEL_CODE.fullmatch(item.raw_text.strip())
        )
        destination_values = _unique_text(value for value, _ in destination_pairs)
        conflicts = []
        for label, values in (
            ("断路器", breaker_values),
            ("额定电流", current_values),
            ("负荷", load_values),
            ("计算电流", design_current_values),
            ("电缆", cable_values),
            ("目的配电箱", destination_values),
        ):
            if len(values) > 1:
                conflicts.append(f"{label}存在多个同列候选：{'、'.join(values)}")
        if not destination_values and not cable_values:
            continue
        combined_evidence = tuple(dict.fromkeys(item[1] for item in combined_pairs))
        populated = sum(
            len(values) == 1
            for values in (breaker_values, current_values, load_values, design_current_values, cable_values, destination_values)
        )
        rows.append(
            FeederRowCandidate(
                feeder_id=f"{feeder_code}:{anchor.evidence_id}",
                drawing_region_id=region_by_evidence.get(anchor.evidence_id, ""),
                cabinet_code=cabinet_code,
                feeder_code=feeder_code,
                feeder_name=feeder_name,
                destination_panel_code=destination_values[0] if len(destination_values) == 1 else "",
                breaker_spec=breaker_values[0] if len(breaker_values) == 1 else "",
                breaker_current_a=current_values[0] if len(current_values) == 1 else "",
                load_kw=load_values[0] if len(load_values) == 1 else "",
                design_current_a=design_current_values[0] if len(design_current_values) == 1 else "",
                cable_spec=cable_values[0] if len(cable_values) == 1 else "",
                evidence_ids=tuple(item.evidence_id for item in band),
                field_evidence={
                    "cabinet_code": (anchor.evidence_id,),
                    "feeder_code": (anchor.evidence_id,),
                    "destination": _evidence_for_unique_value(destination_pairs, destination_values),
                    "breaker": _evidence_for_unique_value(breaker_pairs, breaker_values),
                    "breaker_current": _evidence_for_unique_value(current_pairs, current_values),
                    "load": combined_evidence if len(load_values) == 1 else (),
                    "design_current": combined_evidence if len(design_current_values) == 1 else (),
                    "cable": _evidence_for_unique_value(cable_pairs, cable_values),
                },
                confidence=min(0.96, 0.48 + populated * 0.08),
                state=EvidenceState.CONFLICT if conflicts else EvidenceState.PENDING_CONFIRMATION,
                conflicts=tuple(conflicts),
            )
        )
    return tuple(rows)


def extract_transformer_candidates(
    bundle: ExtractionBundle,
    regions: tuple[DrawingRegion, ...] | None = None,
) -> tuple[TransformerCandidate, ...]:
    """识别低压系统图中由编号、型号/容量、uk和电压共同支撑的变压器。"""

    drawing_regions = regions if regions is not None else group_drawing_regions(bundle)
    region_by_evidence = {
        evidence_id: region.region_id
        for region in drawing_regions
        for evidence_id in region.evidence_ids
    }
    positioned = [item for item in bundle.evidence if item.x is not None and item.y is not None]
    result = []
    for anchor in positioned:
        code = anchor.raw_text.strip().upper()
        if not re.fullmatch(r"B\d+", code):
            continue
        band = [
            item
            for item in positioned
            if abs(float(item.x) - float(anchor.x)) <= 6500
            and float(anchor.y) - 10000 <= float(item.y) <= float(anchor.y) + 500
        ]
        model_pairs: list[tuple[str, str]] = []
        capacity_pairs: list[tuple[str, str]] = []
        uk_pairs: list[tuple[str, str]] = []
        voltage_pairs: list[tuple[str, str]] = []
        cabinet_pairs: list[tuple[str, str]] = []
        busbar_pairs: list[tuple[str, str]] = []
        breaker_pairs: list[tuple[str, str]] = []
        breaker_current_pairs: list[tuple[str, str]] = []
        for item in band:
            normalized = item.raw_text.upper().replace("%%%", "%")
            model = re.search(r"\b(SCB\d+(?:-[A-Z]+)?|S11(?:-M)?)\b", normalized)
            capacity = re.search(r"(?<![.\d])(\d{2,5}(?:\.\d+)?)\s*KVA\b", normalized)
            uk = re.search(r"(?:UK\s*%?|阻抗电压)\s*(?:=|:|：)?\s*(\d+(?:\.\d+)?)\s*%", normalized)
            voltage = re.search(r"\b\d+(?:\.\d+)?/\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?\s*KV\b", normalized)
            cabinet = re.fullmatch(r"D\d+", normalized.strip())
            busbar = re.search(r"\bTMY\s*[-－]\s*\d+\s*\([^)]*\)", normalized)
            breaker = re.search(r"\bCW\d+-\d+/(?:3|4)P", normalized)
            breaker_current = re.search(r"\bIN\s*=\s*(\d+(?:\.\d+)?)\s*A", normalized)
            if model:
                model_pairs.append((model.group(1), item.evidence_id))
            if capacity:
                capacity_pairs.append((capacity.group(1), item.evidence_id))
            if uk:
                uk_pairs.append((uk.group(1), item.evidence_id))
            if voltage:
                voltage_pairs.append((voltage.group(0), item.evidence_id))
            if cabinet:
                cabinet_pairs.append((cabinet.group(0), item.evidence_id))
            if busbar:
                busbar_pairs.append((busbar.group(0), item.evidence_id))
            if breaker:
                breaker_pairs.append((breaker.group(0), item.evidence_id))
            if breaker_current:
                breaker_current_pairs.append((breaker_current.group(1), item.evidence_id))
        model_values = _unique_text(value for value, _ in model_pairs)
        capacity_values = _unique_text(value for value, _ in capacity_pairs)
        uk_values = _unique_text(value for value, _ in uk_pairs)
        voltage_values = _unique_text(value for value, _ in voltage_pairs)
        cabinet_values = _unique_text(value for value, _ in cabinet_pairs)
        busbar_values = _unique_text(value for value, _ in busbar_pairs)
        breaker_values = _unique_text(value for value, _ in breaker_pairs)
        breaker_current_values = _unique_text(value for value, _ in breaker_current_pairs)
        if not model_values or not capacity_values:
            continue
        conflicts = []
        for label, values in (
            ("型号", model_values),
            ("容量", capacity_values),
            ("uk", uk_values),
            ("电压", voltage_values),
            ("低压进线柜", cabinet_values),
            ("低压母排", busbar_values),
            ("低压进线断路器", breaker_values),
            ("低压进线断路器额定电流", breaker_current_values),
        ):
            if len(values) > 1:
                conflicts.append(f"变压器{label}存在多个邻近候选：{'、'.join(values)}")
        result.append(
            TransformerCandidate(
                transformer_id=f"{code}:{anchor.evidence_id}",
                drawing_region_id=region_by_evidence.get(anchor.evidence_id, ""),
                transformer_code=code,
                actual_model=model_values[0] if len(model_values) == 1 else "",
                capacity_kva=capacity_values[0] if len(capacity_values) == 1 else "",
                uk_percent=uk_values[0] if len(uk_values) == 1 else "",
                voltage_text=voltage_values[0] if len(voltage_values) == 1 else "",
                incoming_cabinet_code=cabinet_values[0] if len(cabinet_values) == 1 else "",
                busbar_spec=busbar_values[0] if len(busbar_values) == 1 else "",
                main_breaker_spec=breaker_values[0] if len(breaker_values) == 1 else "",
                main_breaker_current_a=(
                    breaker_current_values[0] if len(breaker_current_values) == 1 else ""
                ),
                evidence_ids=tuple(item.evidence_id for item in band),
                field_evidence={
                    "code": (anchor.evidence_id,),
                    "model": _evidence_for_unique_value(tuple(model_pairs), model_values),
                    "capacity": _evidence_for_unique_value(tuple(capacity_pairs), capacity_values),
                    "uk": _evidence_for_unique_value(tuple(uk_pairs), uk_values),
                    "voltage": _evidence_for_unique_value(tuple(voltage_pairs), voltage_values),
                    "incoming_cabinet": _evidence_for_unique_value(
                        tuple(cabinet_pairs), cabinet_values
                    ),
                    "busbar": _evidence_for_unique_value(tuple(busbar_pairs), busbar_values),
                    "main_breaker": _evidence_for_unique_value(
                        tuple(breaker_pairs), breaker_values
                    ),
                    "main_breaker_current": _evidence_for_unique_value(
                        tuple(breaker_current_pairs), breaker_current_values
                    ),
                },
                confidence=min(0.96, 0.64 + 0.08 * sum(bool(v) for v in (model_values, capacity_values, uk_values))),
                state=EvidenceState.CONFLICT if conflicts else EvidenceState.PENDING_CONFIRMATION,
                conflicts=tuple(conflicts),
            )
        )
    return tuple(result)


def _unique_text(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _evidence_for_unique_value(
    pairs: tuple[tuple[str, str], ...],
    values: tuple[str, ...],
) -> tuple[str, ...]:
    if len(values) != 1:
        return ()
    return tuple(dict.fromkeys(evidence_id for value, evidence_id in pairs if value == values[0]))


def _looks_like_breaker(text: str) -> bool:
    normalized = text.strip().upper()
    return bool(
        re.search(r"(?:CH|CM|CW|NX|NSX|IC65|C65|EZD|CVS|DZ|MT)[A-Z0-9-]*", normalized)
        and ("/" in normalized or re.search(r"\d+A\b", normalized))
    )


def _destination_candidates(
    band: list[DrawingEntityEvidence],
    anchor: DrawingEntityEvidence,
    cable_values: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    cable_x = min(
        (
            float(item.x)
            for item in band
            if item.raw_text.strip() in cable_values and item.x is not None
        ),
        default=float(anchor.x) + 9000,
    )
    excluded = re.compile(r"^(?:P|Pe|Ijs|cos|L)\s*=|^W[A-Z]*\d+", re.I)
    values: list[tuple[str, str]] = []
    for item in band:
        text = item.raw_text.strip()
        if not (float(anchor.x) < float(item.x) < cable_x):
            continue
        if not re.search(r"[\u4e00-\u9fff]", text) or excluded.search(text):
            continue
        if _looks_like_breaker(text) or re.search(r"(?:YJ[LVY]|BVV?|WDZ|ZC-)", text, re.I):
            continue
        if len(text) <= 20:
            values.append((text, item.evidence_id))
    return tuple(dict.fromkeys(values))


def circuit_row_form_candidates(
    row: dict[str, Any],
    feeder: dict[str, Any] | None = None,
    transformer: dict[str, Any] | None = None,
    panel: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """把一条已选择的支路转换为可逐项确认的V0.7表单候选。"""

    candidates: list[dict[str, Any]] = []

    def add(field: str, value: str, evidence_key: str, basis: str = "图纸同行文字") -> None:
        if value:
            candidates.append(
                {
                    "field_name": field,
                    "label": FIELD_LABELS.get(field, field),
                    "value": value,
                    "evidence_ids": tuple(row.get(evidence_key, ())),
                    "basis": basis,
                }
            )

    circuit_code = str(row.get("circuit_code", "")).strip()
    destination = str(row.get("destination", "")).strip()
    load_kw = str(row.get("load_kw", "")).strip()
    breaker = str(row.get("breaker_spec", "")).strip()
    cable = str(row.get("cable_spec", "")).strip()
    add("circuit_code", circuit_code, "circuit_evidence_ids")
    add("circuit_name", destination, "destination_evidence_ids")
    add("load_value", load_kw, "load_evidence_ids")
    if re.search(
        r"(?:4\s*极.*直接启动.*(?:电动机|电机)|(?:电动机|电机).*4\s*极.*直接启动|"
        r"DOL.*(?:MOTOR|电动机|电机)|(?:MOTOR|电动机|电机).*DOL)",
        destination,
        re.IGNORECASE,
    ):
        add("load_kind", "motor", "destination_evidence_ids", "图纸明确标注4极直接启动/DOL电动机")
        add("terminal_phase", "3", "destination_evidence_ids", "4极直接启动电动机按三相末端候选")
    add("breaker_designation_final", breaker, "breaker_evidence_ids")
    section = re.search(r"(?:[*×xX])\s*(\d+(?:\.\d+)?)", cable)
    if section:
        add(
            "existing_section_final",
            section.group(1),
            "cable_evidence_ids",
            "由所选电缆标注解析",
        )
    pe_section = re.search(r"PE\s*[-×xX]?\s*(\d+(?:\.\d+)?)", cable, re.I)
    if pe_section:
        add(
            "existing_pe_section_final",
            pe_section.group(1),
            "cable_evidence_ids",
            "由所选电缆PE标注解析",
        )
    breaker_current = re.search(r"/(\d+(?:\.\d+)?)(?:A)?(?:/|$)", breaker, re.I)
    if breaker_current:
        add(
            "breaker_in_final",
            breaker_current.group(1),
            "breaker_evidence_ids",
            "由所选断路器标注解析",
        )
    trip_curve = re.search(r"\d+([BCD])(?:/|$)", breaker, re.I)
    if trip_curve:
        add(
            "mcb_trip_curve_final",
            trip_curve.group(1).upper(),
            "breaker_evidence_ids",
            "由所选断路器标注解析",
        )
    if re.search(r"(?:^|[-/])(?:1|2P)(?:/|$)", breaker, re.I) or re.search(
        r"(?:^|[-])2[*×xX]", cable
    ):
        add("terminal_phase", "1", "breaker_evidence_ids", "由极数/芯数标注解析")
    branch_frame = re.search(r"-(\d+)[A-Z]?(?:/|$)", breaker, re.I)
    if branch_frame:
        add("breaker_frame_final", branch_frame.group(1), "breaker_evidence_ids", "由产品标注解析")
    if re.search(r"BVV?|(?:^|-)BV-", cable, re.I):
        add("configuration_final", "bv_1ph_2wire_pe", "cable_evidence_ids", "由BV/BVV标注解析")
    elif re.search(r"YJ[LVY].*[-×xX*]3\s*[*×xX]", cable, re.I):
        add("configuration_final", "yjv_3c_3ph_pe", "cable_evidence_ids", "由YJV三芯＋PE标注解析")
    if feeder:
        feeder_evidence = feeder.get("field_evidence", {})

        def add_feeder(field: str, value_key: str, evidence_key: str, basis: str) -> None:
            value = str(feeder.get(value_key, "")).strip()
            if value:
                candidates.append(
                    {
                        "field_name": field,
                        "label": FIELD_LABELS.get(field, field),
                        "value": value,
                        "evidence_ids": tuple(feeder_evidence.get(evidence_key, ())),
                        "basis": basis,
                    }
                )

        add_feeder("feeder_cabinet_code", "cabinet_code", "cabinet_code", "由馈线编号前缀解析")
        add_feeder("upstream_design_current_a", "design_current_a", "design_current", "图纸馈线负荷标注")
        add_feeder("breaker_designation_feeder", "breaker_spec", "breaker", "图纸同列馈线标注")
        add_feeder("breaker_in_feeder", "breaker_current_a", "breaker_current", "图纸同列额定电流")
        feeder_frame = re.search(r"-(\d+)[A-Z]?(?:/|$)", str(feeder.get("breaker_spec", "")), re.I)
        if feeder_frame:
            candidates.append(
                {
                    "field_name": "breaker_frame_feeder",
                    "label": FIELD_LABELS["breaker_frame_feeder"],
                    "value": feeder_frame.group(1),
                    "evidence_ids": tuple(feeder_evidence.get("breaker", ())),
                    "basis": "由馈线断路器产品标注解析",
                }
            )
        feeder_cable = str(feeder.get("cable_spec", ""))
        feeder_section = re.search(r"(?:[*×xX])\s*(\d+(?:\.\d+)?)", feeder_cable)
        if feeder_section:
            candidates.append(
                {
                    "field_name": "existing_section_feeder",
                    "label": FIELD_LABELS["existing_section_feeder"],
                    "value": feeder_section.group(1),
                    "evidence_ids": tuple(feeder_evidence.get("cable", ())),
                    "basis": "由上游馈线电缆标注解析",
                }
            )
        feeder_pe = re.search(r"\+\s*1[*×xX]\s*(\d+(?:\.\d+)?)", feeder_cable)
        if feeder_pe:
            candidates.append(
                {
                    "field_name": "existing_pe_section_feeder",
                    "label": "馈线段独立PE截面（mm²）",
                    "value": feeder_pe.group(1),
                    "evidence_ids": tuple(feeder_evidence.get("cable", ())),
                    "basis": "由上游馈线电缆标注解析",
                }
            )
            candidates.append(
                {
                    "field_name": "configuration_feeder",
                    "label": FIELD_LABELS["configuration_feeder"],
                    "value": "yjv_4c_3ph_n_separate_pe",
                    "evidence_ids": tuple(feeder_evidence.get("cable", ())),
                    "basis": "由4×相/N导体＋独立PE标注解析",
                }
            )
    if transformer:
        transformer_evidence = transformer.get("field_evidence", {})
        for field, value_key, evidence_key, label in (
            ("transformer_code", "transformer_code", "code", "图纸变压器编号"),
            ("transformer_actual_model", "actual_model", "model", "图纸变压器实际型号"),
            ("transformer_capacity_kva", "capacity_kva", "capacity", "图纸变压器容量"),
            ("transformer_uk_percent", "uk_percent", "uk", "图纸变压器阻抗电压"),
        ):
            value = str(transformer.get(value_key, "")).strip()
            if value:
                candidates.append(
                    {
                        "field_name": field,
                        "label": FIELD_LABELS[field],
                        "value": value,
                        "evidence_ids": tuple(transformer_evidence.get(evidence_key, ())),
                        "basis": label,
                    }
                )
        incoming_cabinet = str(transformer.get("incoming_cabinet_code", "")).strip()
        if incoming_cabinet:
            candidates.append(
                {
                    "field_name": "assembly_designation_main",
                    "label": "低压进线柜图纸标识",
                    "value": incoming_cabinet,
                    "evidence_ids": tuple(transformer_evidence.get("incoming_cabinet", ())),
                    "basis": "变压器邻近低压进线柜编号",
                }
            )
        main_breaker = str(transformer.get("main_breaker_spec", "")).strip()
        main_breaker_current = str(transformer.get("main_breaker_current_a", "")).strip()
        if main_breaker:
            candidates.append(
                {
                    "field_name": "breaker_designation_connection",
                    "label": "低压进线断路器标注",
                    "value": main_breaker,
                    "evidence_ids": tuple(transformer_evidence.get("main_breaker", ())),
                    "basis": "变压器邻近低压进线单元",
                }
            )
            frame = re.search(r"-(\d+)(?:/|$)", main_breaker)
            if frame:
                candidates.append(
                    {
                        "field_name": "breaker_frame_connection",
                        "label": "低压进线断路器壳架电流（A）",
                        "value": frame.group(1),
                        "evidence_ids": tuple(transformer_evidence.get("main_breaker", ())),
                        "basis": "由低压进线断路器产品标注解析",
                    }
                )
        if main_breaker_current:
            candidates.append(
                {
                    "field_name": "breaker_in_connection",
                    "label": "低压进线断路器额定电流（A）",
                    "value": main_breaker_current,
                    "evidence_ids": tuple(transformer_evidence.get("main_breaker_current", ())),
                    "basis": "图纸低压进线断路器In标注",
                }
            )
    if panel:
        panel_evidence = panel.get("field_evidence") or {}
        candidates.append(
            {
                "field_name": "assembly_designation_db",
                "label": "下级配电箱图纸标识",
                "value": str(panel.get("panel_code", "")),
                "evidence_ids": tuple(panel_evidence.get("code", ())),
                "basis": "图纸配电箱表头",
            }
        )
        incoming_breaker = str(panel.get("incoming_breaker_spec", "")).strip()
        if incoming_breaker:
            for field, value_key, label in (
                ("incoming_breaker_designation_db", "incoming_breaker_spec", "配电箱进线断路器标注"),
                ("incoming_breaker_in_db", "incoming_breaker_current_a", "配电箱进线断路器额定电流（A）"),
                ("incoming_breaker_frame_db", "incoming_breaker_frame_a", "配电箱进线断路器壳架电流（A）"),
            ):
                value = str(panel.get(value_key, "")).strip()
                if value:
                    candidates.append(
                        {
                            "field_name": field,
                            "label": label,
                            "value": value,
                            "evidence_ids": tuple(panel_evidence.get("incoming_breaker", ())),
                            "basis": "图纸配电箱进线单元标注",
                        }
                    )
    return tuple(candidates)


def _extract_identifiers(text: str, evidence_id: str, add: Any) -> None:
    patterns = {
        "circuit_code": r"(?:回路编号|回路)\s*(?:=|:|：)?\s*([A-Za-z0-9#_.-]+)",
        "transformer_code": r"(?:变压器编号|变压器)\s*(?:=|:|：)?\s*([A-Za-z0-9#_.-]+)",
        "bus_section_code": r"(?:低压母线段编号|母线段)\s*(?:=|:|：)?\s*([A-Za-z0-9ⅠⅡⅢⅣ一二三四#_.-]+)",
        "feeder_cabinet_code": r"(?:馈线柜编号|馈线柜)\s*(?:=|:|：)?\s*([A-Za-z0-9#_.-]+)",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            add(field, match.group(1), evidence_id, 0.9)


def _extract_segment_values(text: str, evidence_id: str, add: Any) -> None:
    segment = None
    if re.search(r"出口连接段|变压器低压出口", text):
        segment = "connection"
    elif re.search(r"馈线段|馈线柜.*配电箱", text):
        segment = "feeder"
    elif re.search(r"末端分支|配电箱.*(?:末端|用电设备)", text):
        segment = "final"
    if segment is None:
        return
    length = re.search(r"(?:长度|L)\s*(?:=|:|：)?\s*(\d+(?:\.\d+)?)\s*[mM]\b", text)
    if length:
        add(f"length_{segment}", length.group(1), evidence_id, 0.9)
    section = re.search(r"(?:×|x|X)\s*(\d+(?:\.\d+)?)", text)
    if section:
        add(f"existing_section_{segment}", section.group(1), evidence_id, 0.8)


def confirm_candidate(
    candidate: CandidateNetwork,
    decisions: Iterable[ConfirmationDecision],
) -> ConfirmedDrawingModel:
    """只接受显式勾选的候选，未确认字段不会泄漏到计算输入。"""

    fact_lookup: dict[tuple[str, str], ExtractedFact] = {
        (fact.field_name, fact.value): fact for fact in candidate.facts
    }
    values: dict[str, str] = {}
    evidence_by_field: dict[str, tuple[str, ...]] = {}
    rejected: list[str] = []
    for decision in decisions:
        if not decision.accepted:
            rejected.append(decision.field_name)
            continue
        fact = fact_lookup.get((decision.field_name, decision.value))
        if fact is None:
            # 用户修订值属于用户补充，不伪装成原图提取事实。
            values[decision.field_name] = decision.value.strip()
            evidence_by_field[decision.field_name] = ()
        else:
            values[decision.field_name] = fact.value
            evidence_by_field[decision.field_name] = fact.evidence_ids
    return ConfirmedDrawingModel(
        source=candidate.source,
        values=values,
        evidence_by_field=evidence_by_field,
        rejected_fields=tuple(dict.fromkeys(rejected)),
    )


def confirmed_to_complete_circuit_form(
    confirmed: ConfirmedDrawingModel,
    defaults: dict[str, str],
) -> dict[str, str]:
    """把已确认事实单向适配到V0.7表单；不执行领域计算。"""

    form = dict(defaults)
    form["task_mode"] = "audit"
    for field_name, value in confirmed.values.items():
        if field_name in form and value:
            form[field_name] = value
    return form
