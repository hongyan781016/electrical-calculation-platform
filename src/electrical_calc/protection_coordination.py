"""上下级保护选择性与后备保护的产品证据判定。

本模块不按额定电流大小猜测选择性。只有上下级产品身份、配置以及
厂家表列极限均能精确匹配时，才比较安装点预期短路电流。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .engine import FAIL, PASS, UNKNOWN


@dataclass(frozen=True)
class ProtectionDeviceIdentity:
    product_code: str
    configuration_reference: str
    family: str
    rated_current_a: float


@dataclass(frozen=True)
class ManufacturerCoordinationEvidence:
    evidence_id: str
    source: str
    reference: str
    status: str
    upstream_product_code: str
    upstream_configuration_reference: str
    downstream_product_code: str
    downstream_configuration_reference: str
    selectivity_limit_ka: float | None = None
    backup_protection_limit_ka: float | None = None
    allowed_system_voltages_v: tuple[float, ...] = ()
    applicability_note: str = ""


@dataclass(frozen=True)
class ProtectionCoordinationInput:
    upstream: ProtectionDeviceIdentity | None
    downstream: ProtectionDeviceIdentity | None
    downstream_prospective_short_circuit_ka: float | None
    evidence: ManufacturerCoordinationEvidence | None = None
    backup_protection_required: bool = False
    system_voltage_v: float | None = None


def load_product_coordination_cases(
    path: Path | None = None,
) -> tuple[dict[str, Any], ...]:
    """Load visually verified product-pair evidence from project data."""

    catalog_path = path or (
        Path(__file__).resolve().parents[2]
        / "data"
        / "references"
        / "extracted"
        / "product-coordination.json"
    )
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for item in raw["cases"]:
        evidence_data = dict(item["evidence"])
        evidence_data["allowed_system_voltages_v"] = tuple(
            evidence_data.get("allowed_system_voltages_v", ())
        )
        cases.append(
            {
                "case_id": item["case_id"],
                "upstream": ProtectionDeviceIdentity(**item["upstream"]),
                "downstream": ProtectionDeviceIdentity(**item["downstream"]),
                "evidence": ManufacturerCoordinationEvidence(
                    **evidence_data
                ),
            }
        )
    return tuple(cases)


def _unknown_item(reason: str) -> dict[str, Any]:
    return {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "reason": reason,
    }


def evaluate_protection_coordination(
    data: ProtectionCoordinationInput,
) -> dict[str, Any]:
    """Evaluate a declared upstream/downstream product pair.

    A verified evidence record can produce a provisional result. Only an
    approved record can turn that provisional result into a formal result.
    """

    result: dict[str, Any] = {
        "status": UNKNOWN,
        "provisional_status": UNKNOWN,
        "method": "manufacturer_coordination_table",
        "selectivity": _unknown_item("尚未取得可精确匹配的厂家选择性表。"),
        "backup_protection": (
            _unknown_item("本回路要求后备保护，但尚未取得厂家级联/后备保护表。")
            if data.backup_protection_required
            else {
                "status": "不适用",
                "provisional_status": "不适用",
                "reason": "本次未声明需要利用上级器件的后备保护能力。",
            }
        ),
        "evidence": asdict(data.evidence) if data.evidence else None,
    }
    if data.upstream is None or data.downstream is None:
        result["reason"] = "必须明确上下级具体产品编号和脱扣/整定配置。"
        return result
    fault_current = data.downstream_prospective_short_circuit_ka
    if fault_current is None or fault_current <= 0:
        result["reason"] = "缺少下级保护安装点最大预期短路电流。"
        return result
    evidence = data.evidence
    if evidence is None:
        result["reason"] = "缺少该上下级产品组合的厂家选择性或级联表。"
        return result
    if evidence.status not in {"verified", "approved"}:
        result["reason"] = "产品配合证据尚未核实。"
        return result
    if not evidence.source.strip() or not evidence.reference.strip():
        result["reason"] = "产品配合证据缺少来源或表格/页码定位。"
        return result
    expected = (
        data.upstream.product_code,
        data.upstream.configuration_reference,
        data.downstream.product_code,
        data.downstream.configuration_reference,
    )
    recorded = (
        evidence.upstream_product_code,
        evidence.upstream_configuration_reference,
        evidence.downstream_product_code,
        evidence.downstream_configuration_reference,
    )
    if expected != recorded:
        result["reason"] = "厂家证据与本次上下级产品或脱扣/整定配置不完全匹配。"
        return result
    if evidence.allowed_system_voltages_v:
        if data.system_voltage_v is None:
            result["reason"] = "厂家证据限定了系统电压，但本次未提供系统电压。"
            return result
        if data.system_voltage_v not in evidence.allowed_system_voltages_v:
            result["reason"] = "本次系统电压不在厂家选择性表适用电压范围内。"
            return result

    if evidence.selectivity_limit_ka is None:
        selectivity = _unknown_item("厂家证据未给出选择性极限电流。")
    elif evidence.selectivity_limit_ka <= 0:
        selectivity = _unknown_item("厂家选择性极限电流数据无效。")
    else:
        provisional = (
            PASS
            if fault_current <= evidence.selectivity_limit_ka
            else FAIL
        )
        selectivity = {
            "status": (
                provisional if evidence.status == "approved" else UNKNOWN
            ),
            "provisional_status": provisional,
            "prospective_short_circuit_ka": fault_current,
            "selectivity_limit_ka": evidence.selectivity_limit_ka,
            "criterion": "Ik,max≤厂家表列选择性极限",
        }
    result["selectivity"] = selectivity

    if data.backup_protection_required:
        if evidence.backup_protection_limit_ka is None:
            backup = _unknown_item("厂家证据未给出级联/后备保护极限电流。")
        elif evidence.backup_protection_limit_ka <= 0:
            backup = _unknown_item("厂家级联/后备保护极限电流数据无效。")
        else:
            provisional = (
                PASS
                if fault_current <= evidence.backup_protection_limit_ka
                else FAIL
            )
            backup = {
                "status": (
                    provisional
                    if evidence.status == "approved"
                    else UNKNOWN
                ),
                "provisional_status": provisional,
                "prospective_short_circuit_ka": fault_current,
                "backup_protection_limit_ka": (
                    evidence.backup_protection_limit_ka
                ),
                "criterion": "Ik,max≤厂家表列级联/后备保护极限",
            }
        result["backup_protection"] = backup

    provisional_items = [selectivity["provisional_status"]]
    if data.backup_protection_required:
        provisional_items.append(
            result["backup_protection"]["provisional_status"]
        )
    if FAIL in provisional_items:
        overall_provisional = FAIL
    elif all(item == PASS for item in provisional_items):
        overall_provisional = PASS
    else:
        overall_provisional = UNKNOWN
    result["provisional_status"] = overall_provisional
    if evidence.status == "approved" and overall_provisional != UNKNOWN:
        result["status"] = overall_provisional
    result["reason"] = (
        "按精确匹配的厂家产品配合表比较。"
        if overall_provisional != UNKNOWN
        else "厂家证据已匹配，但至少一个所需参数缺失。"
    )
    return result
