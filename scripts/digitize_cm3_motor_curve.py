"""从CM3样本C-15～C-17提取电动机型时间-电流曲线保守边界。

固定页码、渲染分辨率、坐标和颜色指纹用于阻止样本变化后静默生成错误数据。
运行本脚本需要pypdfium2和Pillow。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


DPI = 300
EXPECTED_IMAGE_SIZE = (2481, 3367)
X_RANGE_IN = (0.001, 100000.0)
Y_RANGE_S = (10000.0, 0.01)
EXPECTED_RED_PIXEL_RANGE = (15000, 22000)
PIXEL_ALLOWANCE = 2

# 各图的坐标均按同一份官方PDF以300dpi渲染后逐图核验。
CURVES = (
    {
        "curve_id": "in_10a",
        "rated_current_band_a": [10, 10],
        "pdf_page_index": 25,
        "pdf_page": 26,
        "printed_page": "C-15",
        "title": "CM3-63L/M 时间/电流特性曲线（电动机型，In=10A）",
        "plot": {"x0": 417.0, "x1": 2153.0, "top": 479.0, "bottom": 1592.0},
    },
    {
        "curve_id": "in_16a",
        "rated_current_band_a": [16, 16],
        "pdf_page_index": 25,
        "pdf_page": 26,
        "printed_page": "C-15",
        "title": "CM3-63L/M 时间/电流特性曲线（电动机型，In=16A）",
        "plot": {"x0": 417.0, "x1": 2153.0, "top": 1953.0, "bottom": 3063.0},
    },
    {
        "curve_id": "in_20a",
        "rated_current_band_a": [20, 20],
        "pdf_page_index": 26,
        "pdf_page": 27,
        "printed_page": "C-16",
        "title": "CM3-63L/M 时间/电流特性曲线（电动机型，In=20A）",
        "plot": {"x0": 417.0, "x1": 2153.0, "top": 487.0, "bottom": 1600.0},
    },
    {
        "curve_id": "in_25a",
        "rated_current_band_a": [25, 25],
        "pdf_page_index": 26,
        "pdf_page": 27,
        "printed_page": "C-16",
        "title": "CM3-63L/M 时间/电流特性曲线（电动机型，In=25A）",
        "plot": {"x0": 417.0, "x1": 2153.0, "top": 1955.0, "bottom": 3059.0},
    },
    {
        "curve_id": "in_32_63a",
        "rated_current_band_a": [32, 63],
        "pdf_page_index": 27,
        "pdf_page": 28,
        "printed_page": "C-17",
        "title": "CM3-63L/M 时间/电流特性曲线（电动机型，In=32～63A）",
        "plot": {"x0": 417.0, "x1": 2153.0, "top": 1189.0, "bottom": 2296.0},
    },
)


def _is_curve_red(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return (
        red > 170
        and green < 150
        and blue < 150
        and red > green * 1.35
        and red > blue * 1.35
    )


def _x_to_multiple(x: float, plot: dict[str, float]) -> float:
    fraction = (x - plot["x0"]) / (plot["x1"] - plot["x0"])
    return 10 ** (
        math.log10(X_RANGE_IN[0])
        + fraction * (math.log10(X_RANGE_IN[1]) - math.log10(X_RANGE_IN[0]))
    )


def _y_to_seconds(y: float, plot: dict[str, float]) -> float:
    fraction = (y - plot["top"]) / (plot["bottom"] - plot["top"])
    return 10 ** (
        math.log10(Y_RANGE_S[0])
        + fraction * (math.log10(Y_RANGE_S[1]) - math.log10(Y_RANGE_S[0]))
    )


def _extract_curve(image: Any, config: dict[str, Any]) -> dict[str, Any]:
    plot = config["plot"]
    red_pixel_count = 0
    columns: dict[int, list[int]] = {}
    for x in range(int(plot["x0"]), int(plot["x1"]) + 1):
        ys = []
        for y in range(int(plot["top"]), int(plot["bottom"]) + 1):
            if _is_curve_red(image.getpixel((x, y))):
                ys.append(y)
                red_pixel_count += 1
        if ys:
            columns[x] = ys
    if not EXPECTED_RED_PIXEL_RANGE[0] <= red_pixel_count <= EXPECTED_RED_PIXEL_RANGE[1]:
        raise RuntimeError(
            f"{config['curve_id']} red curve fingerprint changed: {red_pixel_count}"
        )

    points = []
    for x in sorted(columns):
        nearby = [
            y
            for sample_x in range(x - 1, x + 2)
            for y in columns.get(sample_x, [])
        ]
        if not nearby:
            continue
        upper_y = max(plot["top"], min(nearby) - PIXEL_ALLOWANCE)
        lower_y = min(plot["bottom"], max(nearby) + PIXEL_ALLOWANCE)
        points.append(
            {
                "current_multiple_in": round(_x_to_multiple(x, plot), 6),
                "maximum_trip_time_s": round(_y_to_seconds(upper_y, plot), 6),
                "minimum_trip_time_s": round(_y_to_seconds(lower_y, plot), 6),
            }
        )
    return {
        "curve_id": config["curve_id"],
        "rated_current_band_a": config["rated_current_band_a"],
        "source": {
            "pdf_page": config["pdf_page"],
            "printed_page": config["printed_page"],
            "curve_title": config["title"],
        },
        "plot_calibration": {
            "pixel_coordinates": plot,
            "x_axis_in": list(X_RANGE_IN),
            "y_axis_s": list(Y_RANGE_S),
        },
        "red_pixel_count": red_pixel_count,
        "sample_count": len(points),
        "points": points,
    }


def extract(pdf_path: Path) -> dict[str, Any]:
    document = pdfium.PdfDocument(str(pdf_path))
    rendered: dict[int, Any] = {}
    extracted = []
    for config in CURVES:
        page_index = config["pdf_page_index"]
        if page_index not in rendered:
            image = document[page_index].render(scale=DPI / 72).to_pil().convert("RGB")
            if image.size != EXPECTED_IMAGE_SIZE:
                raise RuntimeError(f"render fingerprint changed: {image.size}")
            rendered[page_index] = image
        extracted.append(_extract_curve(rendered[page_index], config))
    return {
        "schema_version": 2,
        "source": {
            "document": pdf_path.name,
            "pdf_pages": [26, 27, 28],
            "printed_pages": ["C-15", "C-16", "C-17"],
        },
        "quantity": "trip_time_band",
        "x_unit": "multiple_of_In",
        "y_unit": "s",
        "method": "fixed-resolution raster color extraction; logarithmic-axis calibration",
        "render_calibration": {"dpi": DPI, "image_size": list(EXPECTED_IMAGE_SIZE)},
        "uncertainty": {
            "pixel_allowance": PIXEL_ALLOWANCE,
            "maximum_time_rule": "upper curve shifted upward by pixel allowance",
            "minimum_time_rule": "lower curve shifted downward by pixel allowance",
            "lookup_rule": "no interpolation; use adjacent sample toward conservative side",
        },
        "curves": extracted,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = extract(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
