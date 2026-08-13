"""Import the approved initial local electrical reference bundle.

The source list is intentionally small.  Imported PDFs remain ``pending`` and
cannot be used as formal calculation rules until separately verified/approved.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCES = (
    {
        "id": "19DX101-1",
        "category": "standards",
        "source": Path(r"E:\安装工程资料\建筑安装工程规范图集\建筑设计规范\电气设计规范\19DX101-1建筑电气常用数据国标图集.pdf"),
        "purpose": "负荷参数、电缆基础数据及后续表格核验",
        "note": "已有项目内图表索引；原始文件仍须逐页视觉核验。",
    },
    {
        "id": "GB51348-2019",
        "category": "standards",
        "source": Path(r"E:\安装工程资料\建筑安装工程规范图集\建筑设计规范\电气设计规范\GB51348-2019《民用建筑电气设计标准》.pdf"),
        "purpose": "低压回路正式规则依据候选",
        "note": "仅作为待核实原件，不代表其条文已批准进入计算。",
    },
    {
        "id": "industrial-civil-power-supply-handbook-4",
        "category": "handbooks",
        "source": Path(r"E:\安装工程资料\建筑安装工程规范图集\建筑设计规范\电气设计规范\工业与民用供配电设计手册（第四版）\工业与民用供配电设计手册（第四版）.pdf"),
        "purpose": "短路计算方法逐式核验",
        "note": "仅用于定位和核验计算方法。",
    },
    {
        "id": "transformer-outlet-short-circuit-workbook",
        "category": "handbooks",
        "source": Path(r"E:\电力学习\《在创作-资料》VIP工程师：资料中心\A.标准化-会员-工程师设计工具包\作业手册：10-0.4kV变压器出口断路电流计算及与低压断路器、互感器及母线等配合.pdf"),
        "purpose": "变压器出口及低压侧短路核算对照",
        "note": "作为核算对照资料，不能替代正式规则依据。",
    },
    {
        "id": "wire-cable-handbook",
        "category": "cable-data",
        "source": Path(r"E:\电力学习\《在创作-资料》VIP工程师：资料中心\基础元素3：元件样本库\(分享) 收集的15个电力计算EXCEL表格、软件的合集等多个文件\电线电缆造型手册.pdf"),
        "purpose": "电缆结构、参数和线路阻抗资料核验",
        "note": "待确认版本、适用范围和表格条件。",
    },
    {
        "id": "schneider-cvs-mccb",
        "category": "product-reference",
        "source": Path(r"E:\电力学习\《在创作-资料》VIP工程师：资料中心\基础元素3：元件样本库\(分享) 收集的15个电力计算EXCEL表格、软件的合集等多个文件\施耐德\施耐德CVS塑壳断路器样本.pdf"),
        "purpose": "既有施耐德设备分断能力、脱扣参数核对",
        "note": "仅用于既有设备核对；不进入品牌绑定的通用候选目录。",
    },
    {
        "id": "delixi-cdm6ei-mccb",
        "category": "product-reference",
        "source": Path(r"E:\电力学习\《在创作-资料》VIP工程师：资料中心\基础元素3：元件样本库\(分享) 收集的15个电力计算EXCEL表格、软件的合集等多个文件\德力西\德力西电气产品样本\CDM6Ei电子式塑壳断路器.pdf"),
        "purpose": "既有德力西设备分断能力、整定参数核对",
        "note": "仅用于既有设备核对；不进入品牌绑定的通用候选目录。",
    },
)

BUNDLES = (
    {
        "id": "electrical-calculation-reference-bundle",
        "category": "calculation-reference",
        "target_folder": "15-electrical-calculation-reference",
        "source_dir": Path(r"E:\电力学习\(分享) 收集的15个电力计算EXCEL表格、软件的合集等多个文件\(分享) 收集的15个电力计算EXCEL表格、软件的合集"),
        "purpose": "人工计算结果对照、结构研究和后续回归样例参考",
        "note": "仅作人工对照资料；其中的公式、参数、Excel、压缩包和可执行文件均不得直接进入正式计算规则或参数库。可执行文件不得执行。",
    },
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_items() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for item in SOURCES:
        items.append({
            **item,
            "bundle_id": "",
            "source_relative_path": "",
            "target_relative_path": Path(str(item["category"])) / Path(item["source"]).name,
        })
    for bundle in BUNDLES:
        source_dir = Path(bundle["source_dir"])
        if not source_dir.is_dir():
            raise FileNotFoundError(f"资料合集目录未找到：{source_dir}")
        for source in sorted(path for path in source_dir.rglob("*") if path.is_file()):
            relative = source.relative_to(source_dir)
            items.append({
                "id": f"{bundle['id']}:{relative.as_posix()}",
                "category": bundle["category"],
                "source": source,
                "purpose": bundle["purpose"],
                "note": bundle["note"],
                "bundle_id": bundle["id"],
                "source_relative_path": relative.as_posix(),
                "target_relative_path": Path(str(bundle["category"])) / str(bundle["target_folder"]) / relative,
            })
    return items


def import_sources(project_root: Path, dry_run: bool = False) -> list[dict[str, object]]:
    reference_root = project_root / "data" / "references"
    raw_root = reference_root / "raw"
    history_root = reference_root / "history"
    index_root = reference_root / "index"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: list[dict[str, object]] = []

    for item in source_items():
        source = Path(item["source"])
        if not source.is_file():
            raise FileNotFoundError(f"资料原件未找到：{source}")
        target = raw_root / Path(item["target_relative_path"])
        source_hash = sha256(source)
        action = "copied"
        if target.exists():
            if sha256(target) == source_hash:
                action = "unchanged"
            else:
                archived = history_root / target.relative_to(raw_root).parent / f"{target.stem}--{sha256(target)[:12]}{target.suffix}"
                action = f"replaced; previous copy archived at {archived.relative_to(reference_root).as_posix()}"
                if not dry_run:
                    archived.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(target, archived)
        if action != "unchanged" and not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        record = {
            "id": item["id"],
            "relative_path": target.relative_to(project_root).as_posix(),
            "original_source_path": str(source),
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": source_hash,
            "category": item["category"],
            "bundle_id": item["bundle_id"],
            "source_relative_path": item["source_relative_path"],
            "intended_use": item["purpose"],
            "status": "pending",
            "allowed_for_formal_calculation": False,
            "verification_pages": [],
            "note": item["note"],
            "last_imported_at": now,
            "import_action": action,
        }
        records.append(record)

    if dry_run:
        return records

    index_root.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "generated_at": now, "records": records}
    (index_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = list(records[0])
    with (index_root / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["verification_pages"] = json.dumps(row["verification_pages"], ensure_ascii=False)
            writer.writerow(row)
    statuses = {
        "schema_version": 1,
        "generated_at": now,
        "formal_calculation_allowed": False,
        "records": [
            {
                "id": record["id"],
                "status": record["status"],
                "allowed_for_formal_calculation": record["allowed_for_formal_calculation"],
                "verification_pages": record["verification_pages"],
            }
            for record in records
        ],
    }
    (index_root / "source-status.json").write_text(
        json.dumps(statuses, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="导入首批本地电气资料并生成索引")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    records = import_sources(args.project_root.resolve(), dry_run=args.dry_run)
    for record in records:
        print(f"{record['id']}: {record['import_action']}")


if __name__ == "__main__":
    main()
