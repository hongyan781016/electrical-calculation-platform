"""项目级图纸回路负荷汇总；不替代单回路计算引擎。"""
from __future__ import annotations

from math import sqrt
from typing import Any

from .engine import FAIL, PASS, UNKNOWN


SOURCE_KEYS = (
    "transformer_code", "transformer_family", "transformer_capacity_kva", "transformer_uk_percent",
)


def _group_rows(active: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for item in active:
        values = tuple(str(item["input_json"].get(key) or "未标识") for key in keys)
        group = groups.setdefault(values, {
            "codes": values, "circuit_count": 0, "arithmetic_total_current_a": 0.0,
            "circuit_codes": [],
        })
        group["circuit_count"] += 1
        group["arithmetic_total_current_a"] += float(item["derived_json"].get("design_current_a") or 0)
        group["circuit_codes"].append(
            item.get("circuit_code") or item["input_json"].get("circuit_code") or "未编号"
        )
    result = []
    for group in groups.values():
        group["arithmetic_total_current_a"] = round(group["arithmetic_total_current_a"], 6)
        result.append(group)
    return sorted(result, key=lambda row: row["codes"])


def _node_short_circuit(item: dict[str, Any], node_id: str) -> float | None:
    chain = item.get("audit_json", {}).get("outputs", {}).get("chain_result", {}).get("outputs", {})
    node = next((row for row in chain.get("node_results", []) if row.get("node_id") == node_id), None)
    return float(node["three_phase_short_circuit_ka"]) if node and node.get("three_phase_short_circuit_ka") is not None else None


def _maximum_group_short_circuit(active: list[dict[str, Any]], codes: tuple[str, ...], node_id: str) -> float | None:
    keys = ("transformer_code", "bus_section_code", "feeder_cabinet_code")[:len(codes)]
    values = [
        value for item in active
        if tuple(str(item["input_json"].get(key) or "未标识") for key in keys) == codes
        and (value := _node_short_circuit(item, node_id)) is not None
    ]
    return max(values) if values else None


def _apply_device_checks(group: dict[str, Any], setting: dict[str, Any], prospective_ka: float | None) -> None:
    group["prospective_short_circuit_ka"] = round(prospective_ka, 6) if prospective_ka is not None else None
    group["short_time_withstand_ka"] = setting.get("short_time_withstand_ka")
    group["breaker_designation"] = setting.get("breaker_designation", "")
    group["breaker_breaking_capacity_ka"] = setting.get("breaker_breaking_capacity_ka")
    group["short_time_withstand_status"] = (
        PASS if prospective_ka is not None and group["short_time_withstand_ka"] is not None and group["short_time_withstand_ka"] >= prospective_ka
        else FAIL if prospective_ka is not None and group["short_time_withstand_ka"] is not None else UNKNOWN
    )
    group["breaking_capacity_status"] = (
        PASS if prospective_ka is not None and group["breaker_breaking_capacity_ka"] is not None and group["breaker_breaking_capacity_ka"] >= prospective_ka
        else FAIL if prospective_ka is not None and group["breaker_breaking_capacity_ka"] is not None else UNKNOWN
    )
    group["selectivity_upstream_designation"] = setting.get("selectivity_upstream_designation", "")
    group["selectivity_downstream_designation"] = setting.get("selectivity_downstream_designation", "")
    group["selectivity_limit_ka"] = setting.get("selectivity_limit_ka")
    group["selectivity_reference"] = setting.get("selectivity_reference", "")
    evidence_complete = all((group["selectivity_upstream_designation"], group["selectivity_downstream_designation"], group["selectivity_reference"])) and group["selectivity_limit_ka"] is not None
    group["selectivity_status"] = (
        PASS if evidence_complete and prospective_ka is not None and prospective_ka <= group["selectivity_limit_ka"]
        else FAIL if evidence_complete and prospective_ka is not None else UNKNOWN
    )


def _project_completeness(
    circuits: list[dict[str, Any]], active: list[dict[str, Any]],
    feeder_groups: list[dict[str, Any]], bus_groups: list[dict[str, Any]],
    transformer_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    check_statuses: list[str] = []

    def add(scope: str, subject: str, check: str, status: str, action: str, detail: str = "") -> None:
        check_statuses.append(status)
        if status != PASS:
            issues.append({
                "priority": "阻断" if status == FAIL else "待补充",
                "scope": scope, "subject": subject, "check": check,
                "status": status, "action": action, "detail": detail,
            })

    active_ids = {item.get("id") for item in active}
    for item in circuits:
        if item.get("id") not in active_ids:
            add("回路", item.get("circuit_code", "未编号"), "当前有效计算", UNKNOWN,
                "完成本回路计算并保存最新结果。")
    for item in active:
        code = item.get("circuit_code") or item["input_json"].get("circuit_code", "未编号")
        outputs = item.get("audit_json", {}).get("outputs", {})
        for component in outputs.get("component_matrix", []):
            subject = f"{code} / {component.get('component_name', component.get('component_type', '部件'))} / {component.get('designation', '')}"
            for check_code, check in component.get("checks", {}).items():
                status = check.get("status", UNKNOWN)
                action = "；".join(component.get("remediation_actions", [])) or check.get("reason") or "补充该项参数或证据后复核。"
                add("回路", subject, check.get("check_name", check_code), status, action, check.get("criterion", ""))
        for check in outputs.get("cross_component_checks", []):
            add("回路", code, check.get("check_name", "跨部件配合"), check.get("status", UNKNOWN),
                check.get("remediation") or "补充配合资料后复核。", check.get("reason", ""))

    labels = {
        "equipment_status": ("负荷与额定电流", "补充本级系数、额定电流及来源，或调整设备容量。"),
        "short_time_withstand_status": ("短时耐受Icw", "补充柜体/母线Icw(1s)铭牌；不足时提高短时耐受等级。"),
        "breaking_capacity_status": ("进线断路器Icu", "补充进线断路器Icu；不足时提高分断能力。"),
        "selectivity_status": ("上下级选择性", "补充上下级具体型号/整定、厂家选择性表页码和选择性极限。"),
    }
    for level, groups in (("馈线柜", feeder_groups), ("母线段", bus_groups), ("变压器", transformer_groups)):
        for group in groups:
            subject = "/".join(group["codes"])
            for key, (label, action) in labels.items():
                add(level, subject, label, group.get(key, UNKNOWN), action)

    counts = {PASS: check_statuses.count(PASS), FAIL: check_statuses.count(FAIL), UNKNOWN: check_statuses.count(UNKNOWN)}
    engineering_status = FAIL if counts[FAIL] else UNKNOWN if counts[UNKNOWN] or not check_statuses else PASS
    all_formal = bool(active) and len(active) == len(circuits) and all(item.get("status") == PASS for item in active)
    formal_status = PASS if engineering_status == PASS and all_formal else FAIL if engineering_status == FAIL else UNKNOWN
    issues.sort(key=lambda row: (0 if row["priority"] == "阻断" else 1, row["scope"], row["subject"], row["check"]))
    return {
        "counts": counts,
        "issue_count": len(issues),
        "blocking_issue_count": sum(item["priority"] == "阻断" for item in issues),
        "issues": issues,
        "engineering_data_gate": engineering_status,
        "formal_release_gate": formal_status,
        "formal_release_reason": (
            "工程数据与正式依据均已闭合。" if formal_status == PASS else
            "存在明确不通过项，禁止发布正式成果。" if engineering_status == FAIL else
            "工程数据尚未闭合，或相关依据仍未达到正式通过状态。"
        ),
    }


def summarize_drawing_circuits(
    circuits: list[dict[str, Any]], simultaneity_factor: float | None,
    group_settings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    active = [item for item in circuits if item.get("latest_run_id") and item.get("derived_json")]
    total_current = sum(float(item["derived_json"].get("design_current_a") or 0) for item in active)
    source_signatures = {
        tuple(item["input_json"].get(key) for key in SOURCE_KEYS) for item in active
    }
    warnings: list[str] = []
    if len(source_signatures) > 1:
        warnings.append("各图纸回路的变压器编号、系列、容量或uk%不一致，不能汇总为同一电源树。")
    factor_valid = simultaneity_factor is not None and 0 < simultaneity_factor <= 1
    if not factor_valid:
        warnings.append("未填写有效同时系数，只显示末端回路Ib算术合计。")
    diversified = total_current * float(simultaneity_factor) if factor_valid else None
    transformer_current = None
    capacity_status = UNKNOWN
    capacity_value = active[0]["input_json"].get("transformer_capacity_kva") if active else None
    if active and len(source_signatures) == 1 and capacity_value not in (None, ""):
        capacity = float(capacity_value)
        voltage = 380.0
        transformer_current = capacity * 1000 / (sqrt(3) * voltage)
        if diversified is not None:
            capacity_status = PASS if diversified <= transformer_current else FAIL
    settings = {
        (row["level"], row["transformer_code"], row.get("bus_section_code", ""),
         row.get("feeder_cabinet_code", "")): row
        for row in (group_settings or [])
    }
    feeder_groups = _group_rows(
        active, "transformer_code", "bus_section_code", "feeder_cabinet_code"
    )
    for group in feeder_groups:
        transformer, bus, feeder = group["codes"]
        setting = settings.get(("feeder", transformer, bus, feeder), {})
        factor = setting.get("factor")
        group.update({
            "factor": factor, "source_note": setting.get("source_note", ""),
            "rated_current_a": setting.get("rated_current_a"),
            "design_current_a": round(group["arithmetic_total_current_a"] * factor, 6) if factor else None,
        })
        group["equipment_status"] = (
            PASS if group["design_current_a"] is not None and group["rated_current_a"] is not None
            and group["design_current_a"] <= group["rated_current_a"] else
            FAIL if group["design_current_a"] is not None and group["rated_current_a"] is not None else UNKNOWN
        )
        _apply_device_checks(group, setting, _maximum_group_short_circuit(active, group["codes"], "main"))

    bus_groups = _group_rows(active, "transformer_code", "bus_section_code")
    for group in bus_groups:
        transformer, bus = group["codes"]
        children = [row for row in feeder_groups if row["codes"][:2] == group["codes"]]
        setting = settings.get(("bus", transformer, bus, ""), {})
        factor = setting.get("factor")
        child_total = sum(row["design_current_a"] for row in children) if children and all(row["design_current_a"] is not None for row in children) else None
        group.update({
            "direct_child_current_a": round(child_total, 6) if child_total is not None else None,
            "factor": factor, "source_note": setting.get("source_note", ""),
            "rated_current_a": setting.get("rated_current_a"),
            "design_current_a": round(child_total * factor, 6) if child_total is not None and factor else None,
        })
        group["equipment_status"] = (
            PASS if group["design_current_a"] is not None and group["rated_current_a"] is not None
            and group["design_current_a"] <= group["rated_current_a"] else
            FAIL if group["design_current_a"] is not None and group["rated_current_a"] is not None else UNKNOWN
        )
        _apply_device_checks(group, setting, _maximum_group_short_circuit(active, group["codes"], "tx"))

    transformer_groups = _group_rows(active, "transformer_code")
    for group in transformer_groups:
        transformer = group["codes"][0]
        children = [row for row in bus_groups if row["codes"][0] == transformer]
        setting = settings.get(("transformer", transformer, "", ""), {})
        factor = setting.get("factor")
        child_total = sum(row["design_current_a"] for row in children) if children and all(row["design_current_a"] is not None for row in children) else None
        related = [item for item in active if str(item["input_json"].get("transformer_code") or "未标识") == transformer]
        capacity_values = {item["input_json"].get("transformer_capacity_kva") for item in related}
        derived_rating = None
        if len(capacity_values) == 1 and next(iter(capacity_values)) not in (None, ""):
            derived_rating = float(next(iter(capacity_values))) * 1000 / (sqrt(3) * 380)
        rated = setting.get("rated_current_a") or derived_rating
        group.update({
            "direct_child_current_a": round(child_total, 6) if child_total is not None else None,
            "factor": factor, "source_note": setting.get("source_note", ""),
            "rated_current_a": round(rated, 6) if rated is not None else None,
            "design_current_a": round(child_total * factor, 6) if child_total is not None and factor else None,
        })
        group["equipment_status"] = (
            PASS if group["design_current_a"] is not None and group["rated_current_a"] is not None
            and group["design_current_a"] <= group["rated_current_a"] else
            FAIL if group["design_current_a"] is not None and group["rated_current_a"] is not None else UNKNOWN
        )
        _apply_device_checks(group, setting, _maximum_group_short_circuit(active, group["codes"], "tx"))

    completeness = _project_completeness(
        circuits, active, feeder_groups, bus_groups, transformer_groups
    )

    return {
        "circuit_count": len(active),
        "arithmetic_total_current_a": round(total_current, 6),
        "simultaneity_factor": simultaneity_factor if factor_valid else None,
        "upstream_design_current_a": round(diversified, 6) if diversified is not None else None,
        "transformer_rated_current_a": round(transformer_current, 6) if transformer_current is not None else None,
        "transformer_capacity_status": capacity_status,
        "source_consistent": len(source_signatures) <= 1,
        "transformer_groups": transformer_groups,
        "bus_section_groups": bus_groups,
        "feeder_cabinet_groups": feeder_groups,
        "completeness": completeness,
        "warnings": warnings,
    }
