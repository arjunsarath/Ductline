"""Air-terminal detection (A5).

A terminal is a circle bisected by a horizontal divider: type letter on top,
numeric CFM on the bottom. This module locates the symbol and exposes the
two half-bboxes; OCR consumes the bottom half to read CFM. Type letter is
captured downstream verbatim — this module does not interpret either.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from app.cv._primitives import binary_ink, to_gray
from app.cv.types import Terminal

# Hough circle search bounds, tuned to terminals at 200–400 DPI.
_MIN_RADIUS_PX = 8
_MAX_RADIUS_PX = 80
# Divider line must span ≥ this fraction of the diameter to count as a hit.
_DIVIDER_SPAN_RATIO = 0.7
# Vertical band around the circle's mid-line to look for the divider, in
# pixels. Small — the divider sits within a couple of pixels of the centre.
_DIVIDER_BAND_PX = 2


def detect_air_terminals(image: Image.Image) -> list[Terminal]:
    """Find every circle-with-horizontal-divider symbol on the page.

    OCR for the type letter and CFM is the OCR layer's job (per
    SOLUTION-DESIGN-V4 §4); we return centre + radius + half-bboxes only.
    """
    gray = to_gray(image)
    blurred = cv2.GaussianBlur(gray, (5, 5), sigmaX=1.2)
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=2 * _MIN_RADIUS_PX,
        param1=120,
        param2=30,
        minRadius=_MIN_RADIUS_PX,
        maxRadius=_MAX_RADIUS_PX,
    )
    if circles is None:
        return []

    mask = binary_ink(image)
    out: list[Terminal] = []
    for idx, (cx, cy, r) in enumerate(circles[0]):
        if not _has_horizontal_divider(mask, cx, cy, r):
            continue
        out.append(_terminal_from_circle(idx, cx, cy, r))
    return out


def _has_horizontal_divider(
    mask: np.ndarray, cx: float, cy: float, radius: float
) -> bool:
    """Sample a horizontal band through the centre; require continuous ink."""
    h, w = mask.shape
    y_lo = int(round(cy - _DIVIDER_BAND_PX))
    y_hi = int(round(cy + _DIVIDER_BAND_PX)) + 1
    x_lo = int(round(cx - radius * 0.95))
    x_hi = int(round(cx + radius * 0.95)) + 1
    if y_lo < 0 or y_hi > h or x_lo < 0 or x_hi > w:
        return False
    band = mask[y_lo:y_hi, x_lo:x_hi]
    if band.size == 0:
        return False
    # Collapse to a 1-D row: a pixel column counts as "on the divider" if any
    # of its rows in the band is inked. This tolerates a divider drawn one
    # pixel above or below the rounded centre.
    column_inked = (band > 0).any(axis=0)
    return float(column_inked.mean()) >= _DIVIDER_SPAN_RATIO


def _terminal_from_circle(idx: int, cx: float, cy: float, r: float) -> Terminal:
    return Terminal(
        id=f"term_{idx}",
        center=(float(cx), float(cy)),
        radius=float(r),
        type_letter=None,
        cfm=None,
    )


def half_bboxes(terminal: Terminal) -> tuple[
    tuple[float, float, float, float], tuple[float, float, float, float]
]:
    """Return (top_half_bbox, bottom_half_bbox) for OCR consumption.

    Exposed alongside the dataclass rather than baked into it because the
    `Terminal` shape is shared with the network builder, which has no use
    for the half-rectangles.
    """
    cx, cy = terminal.center
    r = terminal.radius
    x_lo = cx - r
    width = 2 * r
    top = (float(x_lo), float(cy - r), float(width), float(r))
    bottom = (float(x_lo), float(cy), float(width), float(r))
    return top, bottom
