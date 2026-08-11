"""Extract EasyPact CVS I²t curves from the catalogue vector page.

The script deliberately checks the expected page geometry before exporting
numeric points.  A changed catalogue must therefore be reviewed instead of
silently producing a different product curve.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import pdfplumber


PDF_PAGE_INDEX = 90
PLOT = {"x0": 93.068, "x1": 301.846, "top": 426.6218, "bottom": 690.2398}
X_RANGE_KA = (2.0, 300.0)
Y_RANGE_A2S = (1.0e9, 2.0e4)
EXPECTED_CURVE_BBOXES = (
    (121.2644, 227.2414, 607.3111, 638.3151),
    (131.1624, 227.6644, 601.3041, 624.3981),
    (151.4644, 240.7984, 571.6741, 596.4831),
    (158.0624, 240.7924, 565.6151, 587.6011),
)
CURVE_FRAME_MAPPING = (
    ("CVS100", "CVS160"),
    ("CVS250",),
    ("CVS400",),
    ("CVS630",),
)
CURVE_LINEWIDTH_PT = 1.438
COORDINATE_TOLERANCE_PT = 0.01
CONSERVATIVE_COORDINATE_ALLOWANCE_PT = 0.25


def _close(actual: float, expected: float, tolerance: float = COORDINATE_TOLERANCE_PT) -> bool:
    return abs(actual - expected) <= tolerance


def _point(value: Any) -> tuple[float, float]:
    return float(value[0]), float(value[1])


def _cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    subdivisions: int,
) -> Iterable[tuple[float, float]]:
    for step in range(1, subdivisions + 1):
        t = step / subdivisions
        u = 1.0 - t
        yield (
            u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
        )


def sample_path(path: list[tuple[Any, ...]], subdivisions: int = 80) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    current: tuple[float, float] | None = None
    for command in path:
        operator = command[0]
        operands = command[1:]
        if operator == "m":
            current = _point(operands[0])
            points.append(current)
        elif operator == "l":
            if current is None:
                raise ValueError("line command before move")
            current = _point(operands[0])
            points.append(current)
        elif operator == "c":
            if current is None:
                raise ValueError("cubic command before move")
            p1, p2, p3 = map(_point, operands)
            points.extend(_cubic(current, p1, p2, p3, subdivisions))
            current = p3
        elif operator == "v":
            if current is None:
                raise ValueError("cubic command before move")
            p2, p3 = map(_point, operands)
            points.extend(_cubic(current, current, p2, p3, subdivisions))
            current = p3
        elif operator == "y":
            if current is None:
                raise ValueError("cubic command before move")
            p1, p3 = map(_point, operands)
            points.extend(_cubic(current, p1, p3, p3, subdivisions))
            current = p3
        else:
            raise ValueError(f"unsupported PDF path operator: {operator}")
    return sorted(points)


def x_to_current_ka(x: float) -> float:
    fraction = (x - PLOT["x0"]) / (PLOT["x1"] - PLOT["x0"])
    return 10 ** (
        math.log10(X_RANGE_KA[0])
        + fraction * (math.log10(X_RANGE_KA[1]) - math.log10(X_RANGE_KA[0]))
    )


def y_to_i2t_a2s(y: float) -> float:
    fraction = (y - PLOT["top"]) / (PLOT["bottom"] - PLOT["top"])
    return 10 ** (
        math.log10(Y_RANGE_A2S[0])
        + fraction * (math.log10(Y_RANGE_A2S[1]) - math.log10(Y_RANGE_A2S[0]))
    )


def extract(pdf_path: Path) -> dict[str, Any]:
    pdf_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    with pdfplumber.open(pdf_path) as document:
        page = document.pages[PDF_PAGE_INDEX]
        words = page.extract_words()
        labels = [
            {
                "text": word["text"],
                "x0": round(float(word["x0"]), 4),
                "top": round(float(word["top"]), 4),
            }
            for word in words
            if 80 <= word["x0"] <= 330 and 400 <= word["top"] <= 710
        ]
        curves = []
        for expected in EXPECTED_CURVE_BBOXES:
            matches = [
                curve
                for curve in page.curves
                if _close(float(curve.get("linewidth", 0)), CURVE_LINEWIDTH_PT)
                and all(
                    _close(float(curve[key]), expected[index])
                    for index, key in enumerate(("x0", "x1", "top", "bottom"))
                )
            ]
            if len(matches) != 1:
                raise RuntimeError(f"curve fingerprint changed: {expected}; matches={len(matches)}")
            curves.append(matches[0])

    conservative_shift = CURVE_LINEWIDTH_PT / 2 + CONSERVATIVE_COORDINATE_ALLOWANCE_PT
    exported_curves = []
    for index, curve in enumerate(curves, start=1):
        samples = sample_path(curve["path"])
        numeric_points = [
            {
                "prospective_current_ka": round(x_to_current_ka(x), 6),
                "conservative_i2t_a2s": round(y_to_i2t_a2s(y - conservative_shift), 3),
            }
            for x, y in samples
        ]
        exported_curves.append(
            {
                "curve_index": index,
                "applicable_frames": list(CURVE_FRAME_MAPPING[index - 1]),
                "vector_bbox": {
                    key: round(float(curve[key]), 4)
                    for key in ("x0", "x1", "top", "bottom")
                },
                "sample_count": len(numeric_points),
                "points": numeric_points,
            }
        )

    return {
        "schema_version": 1,
        "source": {
            "document": pdf_path.as_posix(),
            "sha256": pdf_sha256,
            "pdf_page": 91,
            "printed_page": "D-11",
        },
        "quantity": "I2t",
        "unit": "A2s",
        "method": "direct PDF vector-path extraction; log-axis calibration",
        "mapping_basis": (
            "printed labels and leader alignment on D-11; CVS100 and CVS160 "
            "share the lower curve"
        ),
        "plot_calibration": {
            "pdf_coordinates": PLOT,
            "x_axis_ka": list(X_RANGE_KA),
            "y_axis_a2s": list(Y_RANGE_A2S),
        },
        "uncertainty": {
            "curve_linewidth_pt": CURVE_LINEWIDTH_PT,
            "coordinate_allowance_pt": CONSERVATIVE_COORDINATE_ALLOWANCE_PT,
            "conservative_y_shift_pt": conservative_shift,
            "rule": "shift toward larger I2t by half line width plus coordinate allowance",
        },
        "labels_for_mapping_review": labels,
        "curves": exported_curves,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = extract(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
