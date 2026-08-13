"""Register official web references already downloaded into the project.

This script is intentionally idempotent.  It updates the three reference index
files without changing any source's approval state.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "references" / "index"

SOURCES = [
    {
        "id": "web-gbt13955-2017",
        "relative_path": "data/references/raw/standards/GB_T_13955-2017_官方电子文本.pdf",
        "original_source_path": "https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=92399A3F5A31DC3E05CC07A182497AC7",
        "category": "standards",
        "intended_use": "剩余电流动作保护装置安装和运行规则核验",
        "verification_pages": [],
        "note": "国家标准全文公开系统下载的电子文本；平台声明电子文本仅供参考。尚未逐条核验，未批准。",
    },
    {
        "id": "web-chint-nb1-63-2026",
        "relative_path": "data/references/raw/product-reference/official-web/CHINT_NB1-63_Catalog_2026.pdf",
        "original_source_path": "https://www.chint.net/products/408",
        "category": "product-reference",
        "intended_use": "MCB B/C/D型脱扣特性及时间-电流曲线核验",
        "verification_pages": [2],
        "note": "正泰官方产品页下载；PDF第2页已视觉确认过电流保护特性表及B/C/D型曲线。仅作产品/曲线参考，未批准。",
    },
    {
        "id": "web-chint-nxble-63-2026",
        "relative_path": "data/references/raw/product-reference/official-web/CHINT_NXBLE-63_Catalog_2026.pdf",
        "original_source_path": "https://www.chint.net/products/380",
        "category": "product-reference",
        "intended_use": "RCBO额定剩余动作电流、类型、极数和分断能力核验",
        "verification_pages": [1],
        "note": "正泰官方产品页下载；PDF第1页已视觉确认主要技术参数。仅作产品/曲线参考，未批准。",
    },
    {
        "id": "web-chint-nm8n-2026",
        "relative_path": "data/references/raw/product-reference/official-web/CHINT_NM8N_Catalog_2026-04-29.pdf",
        "original_source_path": "https://www.chint.net/products/34?tp=2",
        "category": "product-reference",
        "intended_use": "MCCB电子脱扣、整定和分断能力资料核验",
        "verification_pages": [],
        "note": "正泰官方产品页下载；文本层提取异常缓慢，关键页仍待逐页视觉核验，未批准。",
    },
    {
        "id": "web-chint-ns2-catalog-2026",
        "relative_path": "data/references/raw/product-reference/official-web/CHINT_NS2_Motor_Protection_Circuit_Breakers_2026.pdf",
        "original_source_path": "https://www.chintglobal.com/content/dam/chint/global/product-center/low-voltage/iec/industrial-control/motor-protective-breaker/ns2/catalog/NS2-MOTOR-PROTECTIVE-BREAKER-Catalog.pdf",
        "category": "product-reference",
        "intended_use": "NS2电动机保护断路器过载级别、瞬时脱扣边界及分断能力核验",
        "verification_pages": [1, 2, 3, 4],
        "status": "verified",
        "note": "正泰国际NS2产品页下载；PDF第1～4页已视觉确认使用条件、Class 10A试验点、NS2-80分断能力及瞬时脱扣边界。只作单品参数参考，不替代制造商2类配合表，未批准。",
    },
    {
        "id": "web-chint-ns2-80-manual-2024",
        "relative_path": "data/references/raw/product-reference/official-web/CHINT_NS2_80_Manual_2024.pdf",
        "original_source_path": "https://www.chintglobal.com/content/dam/chint/global/product-center/low-voltage/iec/industrial-control/motor-protective-breaker/ns2/manual/2004-NS2-80-Motor%20Protective%20Breaker-Manual.pdf",
        "category": "product-reference",
        "intended_use": "NS2-80独立产品主回路参数交叉核对",
        "verification_pages": [3, 4, 5, 6],
        "status": "verified",
        "note": "正泰国际NS2产品页下载；PDF第3～6页已视觉确认NS2-80/65整定、瞬时脱扣、400/415V分断参数及Figure 1时间-电流特性曲线。曲线未标明为总分断时间且没有I²t数据，未批准。",
    },
    {
        "id": "web-delixi-cdm3e-2026",
        "relative_path": "data/references/raw/product-reference/official-web/DELIXI_CDM3E_Electronic_MCCB_2026.pdf",
        "original_source_path": "https://dlxkeezings.oss-cn-shanghai.aliyuncs.com/2026-05-09/1778289288598-%E5%BE%B7%E5%8A%9B%E8%A5%BF%E7%94%B5%E6%B0%94CDM3E%E5%A1%91%E5%A3%B3%E6%96%AD%E8%B7%AF%E5%99%A8%E6%A0%B7%E6%9C%AC2026.05.pdf",
        "category": "product-reference",
        "intended_use": "电动机回路电子式MCCB短路整定、最大断开时间及分断能力核验",
        "verification_pages": [1, 2, 3, 4],
        "status": "verified",
        "note": "德力西电气官网搜索页发布的2026.05样本；PDF第1～4页已视觉确认保护类型、400/415V分断能力、电动机保护曲线、63A控制器整定和短延时最大断开时间。未取得短延时拾取允差及制造商组合1/2类配合表，未批准。",
    },
    {
        "id": "web-schneider-cvs-2024",
        "relative_path": "data/references/raw/product-reference/official-web/Schneider_EasyPact_CVS_Catalog_2024_LVED210011EN.pdf",
        "original_source_path": "https://www.se.com/in/en/download/document/LVED210011EN/",
        "category": "product-reference",
        "intended_use": "MCCB热磁/电子脱扣曲线与分断能力核验",
        "verification_pages": [82],
        "note": "施耐德官方下载页取得；PDF第82页已视觉确认EasyPact CVS热磁脱扣曲线。仅作产品/曲线参考，未批准。",
    },
    {
        "id": "web-schneider-gv2me04-product-2019",
        "relative_path": "data/references/raw/product-reference/official-web/Schneider_GV2ME04_Datasheet_2026.pdf",
        "original_source_path": "https://iportal.se.com/Contents/docs/TESYS%20GV2_GV2ME04_DATA%20SHEET.PDF",
        "category": "product-reference",
        "intended_use": "0.12/0.18kW小功率电动机GV2保护器、分断能力和限流热应力核验",
        "verification_pages": [1, 6],
        "status": "verified",
        "note": "施耐德产品数据表；PDF第1页已视觉确认GV2ME04、0.4～0.63A及400/415V Icu，PDF第6页已视觉确认GV2ME热限制I²t曲线。曲线数字化仅作保守产品参考，未批准。",
    },
    {
        "id": "web-schneider-gv2me05-product-2017",
        "relative_path": "data/references/raw/product-reference/official-web/Schneider_GV2ME05_Datasheet_2026.pdf",
        "original_source_path": "https://iportal2.schneider-electric.com/Contents/docs/SQD-GV2ME05.PDF",
        "category": "product-reference",
        "intended_use": "0.25kW小功率电动机GV2保护器及分断能力核验",
        "verification_pages": [1],
        "status": "verified",
        "note": "施耐德产品数据表；PDF第1页已视觉确认GV2ME05、0.63～1A、0.25kW及400/415V Icu。未批准。",
    },
    {
        "id": "web-schneider-eig-2018",
        "relative_path": "data/references/raw/handbooks/Schneider_Electrical_Installation_Guide_2018.pdf",
        "original_source_path": "https://www.se.com/sa/en/download/document/Electrical_installation_guide/",
        "category": "handbooks",
        "intended_use": "保护导体截面、绝热校核和故障回路计算方法的IEC补充参考",
        "verification_pages": [259, 262],
        "note": "施耐德官方指南；PDF第259、262页已视觉核验。基于IEC，仅作补充方法参考，不替代中国现行规范，未批准。",
    },
    {
        "id": "web-schneider-canalis-ks-debu034en",
        "relative_path": "data/references/raw/product-reference/official-web/Schneider_Canalis_Low_Voltage_DEBU034EN.pdf",
        "original_source_path": "https://download.se.com/files?p_Doc_Ref=DEBU034EN&p_File_Name=DEBU034EN%20%28web%29.pdf",
        "category": "product-reference",
        "intended_use": "Canalis KS 160～800A母线槽相—PE故障回路R/X及短时耐受参数核验",
        "verification_pages": [66],
        "note": "施耐德官方目录；PDF第66页（印刷第64页）已视觉确认KS 160～800A的Fault loop characteristics。仅用于精确系列/额定电流组合，不插值，未批准。",
    },
    {
        "id": "web-schneider-canalis-kta-debu021en",
        "relative_path": "data/references/raw/product-reference/official-web/Schneider_Canalis_KTA_DEBU021EN.pdf",
        "original_source_path": "https://www.se.com/uk/en/download/document/DEBU021EN/",
        "category": "product-reference",
        "intended_use": "Canalis KTA 800～5000A母线槽相—PE故障回路R/X及短时耐受参数核验",
        "verification_pages": [146, 147, 148, 149],
        "note": "施耐德官方目录；PDF第146～149页（印刷第144～147页）已视觉确认外壳PE、内部铝PE及内部铜PE结构的Fault loop characteristics。仅用于精确结构/额定电流组合，不插值，未批准。",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    manifest_path = INDEX / "manifest.json"
    status_path = INDEX / "source-status.json"
    csv_path = INDEX / "manifest.csv"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_id = {record["id"]: record for record in manifest["records"]}

    for source in SOURCES:
        path = ROOT / source["relative_path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        record = {
            **source,
            "file_name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
            "bundle_id": "",
            "source_relative_path": "",
            "status": source.get("status", "pending"),
            "allowed_for_formal_calculation": False,
            "last_imported_at": timestamp,
            "import_action": "registered_web_source",
        }
        if source["id"] in by_id:
            by_id[source["id"]].update(record)
        else:
            manifest["records"].append(record)
            by_id[source["id"]] = record

    manifest["generated_at"] = timestamp
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    fields = list(manifest["records"][0].keys())
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in manifest["records"]:
            row = dict(record)
            row["verification_pages"] = json.dumps(
                row["verification_pages"], ensure_ascii=False
            )
            writer.writerow(row)

    statuses = json.loads(status_path.read_text(encoding="utf-8"))
    status_by_id = {record["id"]: record for record in statuses["records"]}
    for source in SOURCES:
        row = {
            "id": source["id"],
            "status": source.get("status", "pending"),
            "allowed_for_formal_calculation": False,
            "verification_pages": source["verification_pages"],
        }
        if source["id"] in status_by_id:
            status_by_id[source["id"]].update(row)
        else:
            statuses["records"].append(row)
            status_by_id[source["id"]] = row
    statuses["generated_at"] = timestamp
    statuses["formal_calculation_allowed"] = False
    status_path.write_text(
        json.dumps(statuses, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Registered {len(SOURCES)} web references at {timestamp}")


if __name__ == "__main__":
    main()
