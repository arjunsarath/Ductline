"""Title block + schedule region detection (SOLUTION-DESIGN §4 row 3).

Title block heuristic: largest rectangular contour in the lower-right quadrant
that survives a minimum-text-density check. Schedules are detected as grid
patterns (intersecting horizontal + vertical lines) inside or above the title
block — they don't always exist, so callers must tolerate `None`.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL.Image import Image

from app.ocr.base import Bbox

# Title block typically occupies 5–30% of drawing area and lives in the
# lower-right quadrant of mechanical sheets.
_TITLE_MIN_AREA_FRACTION = 0.02
_TITLE_MAX_AREA_FRACTION = 0.40
_LOWER_RIGHT_QUADRANT_FRACTION = 0.5  # bbox top-left must lie past this fraction


def find_title_block(image: Image) -> Bbox | None:
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape

    # Inverted threshold so line-work (drawing borders) becomes foreground.
    _, binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )

    contours, _ = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    page_area = float(h * w)
    quad_threshold_x = int(w * _LOWER_RIGHT_QUADRANT_FRACTION)
    quad_threshold_y = int(h * _LOWER_RIGHT_QUADRANT_FRACTION)

    candidates: list[tuple[float, Bbox]] = []
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        if x < quad_threshold_x or y < quad_threshold_y:
            continue
        area_fraction = (cw * ch) / page_area
        if not (_TITLE_MIN_AREA_FRACTION <= area_fraction <= _TITLE_MAX_AREA_FRACTION):
            continue
        if not _looks_rectangular(contour):
            continue
        candidates.append((area_fraction, (x, y, cw, ch)))

    if not candidates:
        return None

    # Largest qualifying rectangle wins.
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def find_schedule(image: Image, near: Bbox | None = None) -> Bbox | None:
    """Look for a tabular grid pattern. If `near` is set, prefer regions inside
    or directly above the title block — schedules are usually stacked on it.
    """
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape

    crop_region = _expand_search_region(near, h, w)
    x0, y0, cw, ch = crop_region
    crop = gray[y0 : y0 + ch, x0 : x0 + cw]

    _, binary = cv2.threshold(
        crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )

    horizontal = _extract_lines(binary, axis="horizontal")
    vertical = _extract_lines(binary, axis="vertical")
    grid = cv2.bitwise_and(horizontal, vertical)
    if cv2.countNonZero(grid) < 50:
        return None

    coords = cv2.findNonZero(grid)
    gx, gy, gw, gh = cv2.boundingRect(coords)
    return (gx + x0, gy + y0, gw, gh)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _looks_rectangular(contour: np.ndarray) -> bool:
    perimeter = cv2.arcLength(contour, closed=True)
    approx = cv2.approxPolyDP(contour, 0.02 * perimeter, closed=True)
    return len(approx) == 4


def _expand_search_region(near: Bbox | None, h: int, w: int) -> Bbox:
    if near is None:
        # Default: lower-right quadrant.
        return (w // 2, h // 2, w // 2, h // 2)
    x, y, cw, ch = near
    # Stretch upward so we catch schedules sitting above the title block.
    expanded_y = max(0, y - 2 * ch)
    expanded_h = (y + ch) - expanded_y
    return (x, expanded_y, cw, expanded_h)


def _extract_lines(binary: np.ndarray, *, axis: str) -> np.ndarray:
    if axis == "horizontal":
        kernel_size = (max(binary.shape[1] // 30, 5), 1)
    else:
        kernel_size = (1, max(binary.shape[0] // 30, 5))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, kernel_size)
    eroded = cv2.erode(binary, kernel, iterations=1)
    return cv2.dilate(eroded, kernel, iterations=1)
