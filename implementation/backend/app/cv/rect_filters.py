"""Rectangle filters — narrow the recall-first contour list down to ducts.

Filters are applied in order of cheapness so expensive checks (OCR overlap)
only run on rectangles that survived the cheap ones (size). Each filter
returns the input list with a `drop_reason` annotation rather than dropping
the rectangle so the operator can see what each filter ate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from PIL import Image

from app.ocr.base import OCRExtractor

DropReason = Literal[
    "oversized", "non_duct_text", "low_aspect_ratio", "interior_not_empty",
    "not_rectangle", "interior_no_ink", "too_square", "interior_too_full",
]

# A rectangle bigger than this fraction of the page is the page frame, the
# title block, or a cell of the plan-notes table — never an actual duct.
_OVERSIZE_AREA_FRACTION = 0.20
# Real ducts are elongated. min(w,h) is height, max(w,h) is run length.
DEFAULT_MIN_ASPECT_RATIO = 6.0
# Required pure-white fraction inside the rectangle (excluding a small border
# to ignore anti-aliased edge pixels). Real duct interiors are mostly white.
DEFAULT_MIN_WHITE_PCT = 0.85
# Border (in pixels) to ignore when measuring interior whiteness.
_INTERIOR_BORDER_PX = 2
# A duct rectangle with a dimension label inside has ~2–8% ink density.
# A rectangle whose interior is below this floor is just an outline with
# nothing inside — drop it as "empty" (not a duct candidate).
DEFAULT_MIN_INK_PCT = 0.005  # 0.5%
# A rectangle whose interior is mostly ink (>30% black pixels) is a flex-duct
# stripe, a letter stroke, or a hatched fill — never a duct dimension label.
DEFAULT_MAX_INK_PCT = 0.30
# Aspect ratio max/min: real ducts are elongated. Square-ish shapes are
# keynote bubbles, air terminals, equipment symbols — drop them.
DEFAULT_MIN_DUCT_ASPECT = 1.5
# Rectangle-shape filter: corner cosines ≤ this value mean the angle is within
# ±arccos(0.25) ≈ ±75°–105° of square. Lower = stricter rectangularity.
DEFAULT_MAX_CORNER_COS = 0.25
# approxPolyDP epsilon as a fraction of perimeter. 0.02 smooths small notches
# enough to resolve a rasterized rectangle to 4 vertices.
DEFAULT_EPSILON_FRAC = 0.02

# Duct dimension grammar (A1 + A3): round `8"ø` and rectangular `22"x14"`.
# Tolerant of OCR mojibake on `ø` (RapidOCR sometimes returns `0` or `o`).
_DUCT_LABEL_RE = re.compile(
    r"^\s*\d{1,2}\s*\"?\s*(?:[øØ⌀0o]|x\s*\d{1,2}\s*\"?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaggedRect:
    """A rectangle plus its filter outcome."""

    corners: list[tuple[int, int]]
    bbox: tuple[int, int, int, int]  # x, y, w, h (axis-aligned)
    kept: bool
    drop_reason: DropReason | None


def _axis_aligned_bbox(corners: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def filter_oversized(
    rectangles: list[list[tuple[int, int]]],
    page_w: int,
    page_h: int,
) -> list[TaggedRect]:
    """Drop rectangles whose axis-aligned bbox covers > 20% of the page."""
    page_area = max(page_w * page_h, 1)
    out: list[TaggedRect] = []
    for corners in rectangles:
        bbox = _axis_aligned_bbox(corners)
        area = bbox[2] * bbox[3]
        oversized = area / page_area > _OVERSIZE_AREA_FRACTION
        out.append(TaggedRect(
            corners=corners,
            bbox=bbox,
            kept=not oversized,
            drop_reason="oversized" if oversized else None,
        ))
    return out


def filter_by_content(
    tagged: list[TaggedRect],
    image: Image.Image,
    ocr: OCRExtractor,
) -> list[TaggedRect]:
    """Drop rectangles whose interior contains text NOT matching the duct grammar.

    Strategy: OCR the full image once, then for each rectangle test whether any
    OCR token sits inside it. If the token's text fails the duct regex, drop
    the rect. Empty interiors are kept (they may be unlabelled ducts that the
    A9 pixel-measurement fallback will size later). Already-dropped rects are
    passed through untouched.
    """
    if not tagged:
        return tagged
    matches = ocr.extract_text(image)
    out: list[TaggedRect] = []
    for rect in tagged:
        if not rect.kept:
            out.append(rect)
            continue
        rx, ry, rw, rh = rect.bbox
        intersecting_text = [
            m.text.strip() for m in matches if _bbox_intersects(m.bbox, rx, ry, rw, rh)
        ]
        if not intersecting_text:
            out.append(rect)
            continue
        if any(_DUCT_LABEL_RE.match(t) for t in intersecting_text):
            out.append(rect)
            continue
        out.append(TaggedRect(
            corners=rect.corners,
            bbox=rect.bbox,
            kept=False,
            drop_reason="non_duct_text",
        ))
    return out


def _bbox_intersects(
    bbox: tuple[float, float, float, float],
    rx: int, ry: int, rw: int, rh: int,
) -> bool:
    """Treat OCR bbox centroid as inside the rectangle."""
    bx, by, bw, bh = bbox
    cx = bx + bw / 2.0
    cy = by + bh / 2.0
    return rx <= cx <= rx + rw and ry <= cy <= ry + rh


def filter_is_rectangle(
    tagged: list[TaggedRect],
    *,
    epsilon_frac: float = DEFAULT_EPSILON_FRAC,
    max_corner_cos: float = DEFAULT_MAX_CORNER_COS,
) -> list[TaggedRect]:
    """Drop polygons that aren't rectangles.

    Approximates each polygon at ``epsilon_frac × perimeter`` and requires
    exactly four convex vertices with corner cosines below ``max_corner_cos``
    (≈ ±15° tolerance from a right angle at 0.25). Reusable on already-tagged
    inputs: kept stays kept; failures get ``drop_reason='not_rectangle'``.
    """
    out: list[TaggedRect] = []
    for rect in tagged:
        if not rect.kept:
            out.append(rect)
            continue
        if _is_rectangle(rect.corners, epsilon_frac, max_corner_cos):
            out.append(rect)
            continue
        out.append(TaggedRect(
            corners=rect.corners, bbox=rect.bbox,
            kept=False, drop_reason="not_rectangle",
        ))
    return out


def _is_rectangle(
    corners: list[tuple[int, int]],
    epsilon_frac: float,
    max_corner_cos: float,
) -> bool:
    if len(corners) < 4:
        return False
    contour = np.array(corners, dtype=np.int32).reshape(-1, 1, 2)
    peri = cv2.arcLength(contour, closed=True)
    if peri <= 0:
        return False
    approx = cv2.approxPolyDP(contour, epsilon_frac * peri, closed=True)
    if len(approx) != 4:
        return False
    if not cv2.isContourConvex(approx):
        return False
    pts = approx.reshape(-1, 2).astype(np.float32)
    for i in range(4):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % 4]
        ba, bc = a - b, c - b
        denom = float(np.linalg.norm(ba) * np.linalg.norm(bc)) + 1e-9
        if abs(float(np.dot(ba, bc)) / denom) > max_corner_cos:
            return False
    return True


def filter_min_ink(
    tagged: list[TaggedRect],
    image: Image.Image,
    min_ink_pct: float = DEFAULT_MIN_INK_PCT,
) -> list[TaggedRect]:
    """Drop rectangles whose interior has less ink than ``min_ink_pct``.

    Operates on the binary cleaned image (ink = pixel value 0). Does not
    need OCR — empty rectangles have a few border pixels at most. Useful
    pre-filter to keep VLM costs down on rectangles that obviously have
    no text inside.
    """
    out: list[TaggedRect] = []
    luma = np.asarray(image.convert("L"))
    h_img, w_img = luma.shape
    pad = _INTERIOR_BORDER_PX
    for rect in tagged:
        if not rect.kept:
            out.append(rect)
            continue
        x, y, w, h = rect.bbox
        x0 = max(0, x + pad)
        y0 = max(0, y + pad)
        x1 = min(w_img, x + w - pad)
        y1 = min(h_img, y + h - pad)
        if x1 <= x0 or y1 <= y0:
            out.append(rect)
            continue
        crop = luma[y0:y1, x0:x1]
        if crop.size == 0:
            out.append(rect)
            continue
        ink_pct = float((crop == 0).mean())
        if ink_pct < min_ink_pct:
            out.append(TaggedRect(
                corners=rect.corners, bbox=rect.bbox,
                kept=False, drop_reason="interior_no_ink",
            ))
            continue
        out.append(rect)
    return out


def filter_max_ink(
    tagged: list[TaggedRect],
    image: Image.Image,
    max_ink_pct: float = DEFAULT_MAX_INK_PCT,
) -> list[TaggedRect]:
    """Drop rectangles whose interior is mostly ink.

    A real duct rectangle has a thin outline plus a small dimension label
    inside — ink density 2–8%. A flex-duct stripe (parallel bellow lines),
    a letter stroke, or a hatched fill all sit at 60–90% ink density. This
    filter draws the line so any of those drop without dragging valid ducts
    with them.
    """
    out: list[TaggedRect] = []
    luma = np.asarray(image.convert("L"))
    h_img, w_img = luma.shape
    pad = _INTERIOR_BORDER_PX
    for rect in tagged:
        if not rect.kept:
            out.append(rect)
            continue
        x, y, w, h = rect.bbox
        x0 = max(0, x + pad)
        y0 = max(0, y + pad)
        x1 = min(w_img, x + w - pad)
        y1 = min(h_img, y + h - pad)
        if x1 <= x0 or y1 <= y0:
            out.append(rect)
            continue
        crop = luma[y0:y1, x0:x1]
        if crop.size == 0:
            out.append(rect)
            continue
        ink_pct = float((crop == 0).mean())
        if ink_pct > max_ink_pct:
            out.append(TaggedRect(
                corners=rect.corners, bbox=rect.bbox,
                kept=False, drop_reason="interior_too_full",
            ))
            continue
        out.append(rect)
    return out


def filter_squarish(
    tagged: list[TaggedRect],
    min_aspect: float = DEFAULT_MIN_DUCT_ASPECT,
) -> list[TaggedRect]:
    """Drop rectangles whose long/short ratio is below ``min_aspect``.

    Aspect is measured along the rotated bbox's own axes (corner side
    lengths) — a 45°-rotated long duct has axis-aligned bbox aspect ~1.0
    but its true rotated aspect is the duct's W:H. Without this we'd drop
    every diagonal duct.
    """
    out: list[TaggedRect] = []
    for rect in tagged:
        if not rect.kept:
            out.append(rect)
            continue
        sides = _rotated_side_lengths(rect.corners) or _bbox_side_lengths(rect.bbox)
        if not sides:
            out.append(rect)
            continue
        short = min(sides)
        long_side = max(sides)
        if short <= 0 or long_side / short < min_aspect:
            out.append(TaggedRect(
                corners=rect.corners, bbox=rect.bbox,
                kept=False, drop_reason="too_square",
            ))
            continue
        out.append(rect)
    return out


def _rotated_side_lengths(
    corners: list[tuple[int, int]],
) -> tuple[float, float] | None:
    """Side-pair lengths of the rotated bbox represented by ``corners``."""
    import math

    if len(corners) < 4:
        return None
    a, b, c = corners[0], corners[1], corners[2]
    side1 = math.hypot(b[0] - a[0], b[1] - a[1])
    side2 = math.hypot(c[0] - b[0], c[1] - b[1])
    return side1, side2


def _bbox_side_lengths(
    bbox: tuple[int, int, int, int],
) -> tuple[float, float] | None:
    _, _, w, h = bbox
    if w <= 0 or h <= 0:
        return None
    return float(w), float(h)


def filter_by_aspect_ratio(
    tagged: list[TaggedRect],
    min_ratio: float = DEFAULT_MIN_ASPECT_RATIO,
) -> list[TaggedRect]:
    """Drop rectangles whose long-side / short-side falls below the threshold."""
    out: list[TaggedRect] = []
    for rect in tagged:
        if not rect.kept:
            out.append(rect)
            continue
        _, _, w, h = rect.bbox
        short = min(w, h)
        long = max(w, h)
        if short <= 0 or long / short < min_ratio:
            out.append(TaggedRect(
                corners=rect.corners, bbox=rect.bbox,
                kept=False, drop_reason="low_aspect_ratio",
            ))
            continue
        out.append(rect)
    return out


def filter_by_interior_emptiness(
    tagged: list[TaggedRect],
    image: Image.Image,
    min_white_pct: float = DEFAULT_MIN_WHITE_PCT,
) -> list[TaggedRect]:
    """Drop rectangles whose interior (excluding a 2px border) isn't mostly white.

    Real duct rectangles enclose nearly-empty space (the dimension label is the
    only ink inside, occupying a tiny fraction). Line-fragment contours trapped
    by ``find_all_rectangles`` have ink running through them and fail this.
    """
    out: list[TaggedRect] = []
    luma = np.asarray(image.convert("L"))
    h_img, w_img = luma.shape
    pad = _INTERIOR_BORDER_PX
    for rect in tagged:
        if not rect.kept:
            out.append(rect)
            continue
        x, y, w, h = rect.bbox
        x0 = max(0, x + pad)
        y0 = max(0, y + pad)
        x1 = min(w_img, x + w - pad)
        y1 = min(h_img, y + h - pad)
        if x1 <= x0 or y1 <= y0:
            out.append(rect)
            continue
        crop = luma[y0:y1, x0:x1]
        if crop.size == 0:
            out.append(rect)
            continue
        white_pct = float((crop == 255).mean())
        if white_pct < min_white_pct:
            out.append(TaggedRect(
                corners=rect.corners, bbox=rect.bbox,
                kept=False, drop_reason="interior_not_empty",
            ))
            continue
        out.append(rect)
    return out
