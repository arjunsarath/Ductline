"""Outline-based duct polygon detection (SOLUTION-DESIGN-V4 §3, ADR-0015).

V3 detected ducts by their fill colour; V4 walks contours of the cleaned
linework so the same code path works for hatched, unfilled, or differently
coloured drawings.

Approach:
  1. Threshold to an ink mask (grey arch fill is already stripped — A12).
  2. Close small dashes so a dashed run reads as one closed contour. Dilate-
     then-erode is enough — the spacing in the test set is sub-millimetre at
     300 DPI.
  3. Find external contours, filter on area + aspect ratio.
  4. Approximate each contour to a polygon and classify shape via the
     min-area-rect aspect ratio: square-ish → "round" candidate (the bbox
     of an inscribed circle is square), elongated → "rectangular".
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from app.cv._primitives import binary_ink, principal_axis_of
from app.cv.types import DuctPolygon

# Anything smaller than this is text or a tick mark, not a duct.
_MIN_AREA_PX = 800
# A duct that fills the whole page is the title-block frame, not a duct.
_MAX_AREA_FRAC = 0.6
# Reject extreme aspect ratios — those are wall lines, not ducts.
_MAX_ASPECT = 60.0
# Aspect ratio above which we treat the contour as elongated (rectangular).
# Below this the contour is squat enough to be a round duct seen as a circle
# or short rectangle; classification falls back to "unknown" for the caller.
_RECT_ASPECT_FLOOR = 1.6
# Polygon approximation epsilon as a fraction of contour perimeter.
_APPROX_EPS_FRAC = 0.01
# Morphological-close kernel; large enough to bridge dashed-line gaps in a
# 300-DPI raster but small enough not to merge adjacent ducts.
_CLOSE_KERNEL = 5


def detect_duct_polygons(image: Image.Image) -> list[DuctPolygon]:
    """Return one polygon per duct shape found in the cleaned raster."""
    mask = _close_gaps(binary_ink(image))
    h, w = mask.shape
    page_area = float(h * w)

    # `RETR_LIST` walks every contour — outer and inner — without hierarchy.
    # Engineering drawings nest the duct outline inside the page-frame outline
    # and adjacent to text/hatching, so `RETR_EXTERNAL` would lose interior
    # ducts entirely.
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    polygons: list[DuctPolygon] = []
    for idx, contour in enumerate(contours):
        polygon = _contour_to_polygon(contour, page_area, polygon_id=f"duct_{idx}")
        if polygon is not None:
            polygons.append(polygon)
    return _suppress_duplicates(polygons)


def _close_gaps(mask: np.ndarray) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (_CLOSE_KERNEL, _CLOSE_KERNEL))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def _contour_to_polygon(
    contour: np.ndarray, page_area: float, *, polygon_id: str
) -> DuctPolygon | None:
    area = float(cv2.contourArea(contour))
    if area < _MIN_AREA_PX or area > page_area * _MAX_AREA_FRAC:
        return None

    x, y, w, h = cv2.boundingRect(contour)
    long_side, short_side = (max(w, h), min(w, h))
    if short_side == 0:
        return None
    aspect = long_side / short_side
    if aspect > _MAX_ASPECT:
        return None

    axis, est_width_px = principal_axis_of(contour.reshape(-1, 2))
    if est_width_px <= 0:
        return None

    eps = _APPROX_EPS_FRAC * cv2.arcLength(contour, closed=True)
    approx = cv2.approxPolyDP(contour, eps, closed=True)
    points = [(float(p[0][0]), float(p[0][1])) for p in approx]

    shape_hint = _classify_shape(aspect, area, w, h)
    return DuctPolygon(
        id=polygon_id,
        points=points,
        shape_hint=shape_hint,
        bbox=(float(x), float(y), float(w), float(h)),
        principal_axis=axis,
        est_width_px=float(est_width_px),
    )


def _suppress_duplicates(
    polygons: list[DuctPolygon], *, iou_threshold: float = 0.85
) -> list[DuctPolygon]:
    """Drop polygons whose bbox IoU with a kept polygon is above threshold.

    Ink contours come in pairs (outer + inner edge of the same duct outline).
    Keeping both would double-count every duct downstream, so we keep the
    larger of each near-duplicate pair.
    """
    sorted_polys = sorted(
        polygons,
        key=lambda p: (p.bbox[2] * p.bbox[3]) if p.bbox else 0.0,
        reverse=True,
    )
    kept: list[DuctPolygon] = []
    for candidate in sorted_polys:
        if candidate.bbox is None:
            kept.append(candidate)
            continue
        if not any(_bbox_iou(candidate.bbox, k.bbox) >= iou_threshold for k in kept if k.bbox):
            kept.append(candidate)
    # Re-id so callers see contiguous identifiers regardless of suppression.
    return [
        DuctPolygon(
            id=f"duct_{i}",
            points=p.points,
            shape_hint=p.shape_hint,
            bbox=p.bbox,
            principal_axis=p.principal_axis,
            est_width_px=p.est_width_px,
        )
        for i, p in enumerate(kept)
    ]


def _bbox_iou(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    inter_x1 = max(ax, bx)
    inter_y1 = max(ay, by)
    inter_x2 = min(ax + aw, bx + bw)
    inter_y2 = min(ay + ah, by + bh)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    inter = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _classify_shape(aspect: float, area: float, w: int, h: int) -> str:
    """Round vs rectangular vs unknown.

    A round duct rendered as a circle has a bbox aspect ~ 1 and an area ~ π/4
    of its bbox. A long rectangular run has aspect well above 1.
    """
    if aspect >= _RECT_ASPECT_FLOOR:
        return "rectangular"
    bbox_area = float(w * h)
    if bbox_area == 0:
        return "unknown"
    fill = area / bbox_area
    # Circle inside its bbox fills π/4 ≈ 0.785; allow a tolerance band.
    if 0.65 <= fill <= 0.92 and aspect < 1.25:
        return "round"
    return "unknown"
