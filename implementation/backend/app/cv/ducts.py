"""Duct geometry — Hough-based refinement and CV-only fallback (§4 row 4).

Two entry points:
  • `refine_segment_geometry`: tightens a coarse VLM bbox into a polyline by
    finding the dominant parallel-line pair inside the bbox.
  • `find_duct_candidates_cv`: best-effort CV-only fallback for when the VLM
    stage fails (§9). Tends to over-recall (walls, columns, grid lines look
    like duct walls to a parallel-pair detector) — filters in this module
    drop borders and extreme aspect ratios; expect false positives from
    walls regardless. Surfaced via a warning banner per §9.

Round ducts can't be refined the same way (no parallel sides) — the bbox is
used as-is and the centerline is the bbox center.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL.Image import Image

from app.ocr.base import Bbox

# How close (in pixels) two parallel lines must be to count as a duct's
# opposite walls. Engineering ducts at 200 DPI typically span 20–200 px wide.
_MIN_PAIR_DISTANCE_PX = 8
_MAX_PAIR_DISTANCE_PX = 250

# Lines must be within this many degrees of each other to be considered parallel.
_PARALLEL_ANGLE_TOLERANCE_DEG = 5.0


def refine_segment_geometry(
    image: Image, bbox: Bbox, *, shape_hint: str
) -> list[tuple[float, float]]:
    """Return a polyline approximating the duct centerline.

    For rectangular ducts: the centerline of the dominant parallel-line pair
    inside `bbox`. For round (or unknown) ducts: a horizontal line through the
    bbox center, since we can't recover meaningful geometry from a circle.
    """
    x, y, w, h = bbox
    if shape_hint == "round":
        return _bbox_centerline(bbox)

    crop = np.asarray(image)[y : y + h, x : x + w]
    if crop.size == 0:
        return _bbox_centerline(bbox)

    edges = _edges(crop)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=40,
        minLineLength=max(min(w, h) // 3, 20),
        maxLineGap=10,
    )
    if lines is None or len(lines) < 2:
        return _bbox_centerline(bbox)

    pair = _pick_dominant_parallel_pair(lines)
    if pair is None:
        return _bbox_centerline(bbox)

    centerline = _midline(pair)
    # Translate from crop-local back to image coords.
    return [(px + x, py + y) for px, py in centerline]


def find_duct_candidates_cv(image: Image, *, max_candidates: int = 60) -> list[Bbox]:
    """CV-only fallback. Pairs of long parallel lines become candidate ducts.

    Raw HoughLinesP over an engineering drawing fires on borders, columns,
    grid lines, and hatching — anything with two parallel edges. We filter
    aggressively before returning:
      • drop bboxes touching the page border (sheet rules / title-block frames),
      • drop extreme aspect ratios (> 25:1) and full-page-spanning rectangles,
      • non-max-suppress overlapping candidates so a single duct doesn't show
        up four times,
      • cap the count so the VLM-replacement path doesn't drown stage 5 in OCR.
    """
    array = np.asarray(image)
    h, w = array.shape[:2]
    edges = _edges(array)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=80,
        minLineLength=60,
        maxLineGap=15,
    )
    if lines is None:
        return []

    pairs = _all_parallel_pairs(lines)
    bboxes = [_pair_bbox(pair) for pair in pairs]
    bboxes = [b for b in bboxes if _passes_filters(b, h, w)]
    bboxes = _non_max_suppress(bboxes, iou_threshold=0.4)

    bboxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    return bboxes[:max_candidates]


# ── Internals ────────────────────────────────────────────────────────────────


def _bbox_centerline(bbox: Bbox) -> list[tuple[float, float]]:
    x, y, w, h = bbox
    return [(float(x), float(y + h / 2)), (float(x + w), float(y + h / 2))]


def _edges(array: np.ndarray) -> np.ndarray:
    if array.ndim == 3:
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    else:
        gray = array
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(blurred, 50, 150)


def _line_angle_deg(line: np.ndarray) -> float:
    x1, y1, x2, y2 = line[0]
    return float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))


def _line_length(line: np.ndarray) -> float:
    x1, y1, x2, y2 = line[0]
    return float(np.hypot(x2 - x1, y2 - y1))


def _line_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Perpendicular distance between two roughly-parallel lines.

    Uses midpoint of `b` projected onto the normal of `a`.
    """
    ax1, ay1, ax2, ay2 = a[0]
    bx1, by1, bx2, by2 = b[0]
    bmid = np.array([(bx1 + bx2) / 2, (by1 + by2) / 2])
    direction = np.array([ax2 - ax1, ay2 - ay1], dtype=np.float64)
    norm = np.linalg.norm(direction)
    if norm == 0:
        return float("inf")
    direction /= norm
    normal = np.array([-direction[1], direction[0]])
    offset = bmid - np.array([ax1, ay1])
    return float(abs(np.dot(offset, normal)))


def _pick_dominant_parallel_pair(
    lines: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    sorted_lines = sorted(lines, key=_line_length, reverse=True)
    for i in range(min(len(sorted_lines), 12)):
        for j in range(i + 1, min(len(sorted_lines), 12)):
            a, b = sorted_lines[i], sorted_lines[j]
            angle_diff = abs(_line_angle_deg(a) - _line_angle_deg(b))
            angle_diff = min(angle_diff, 180 - angle_diff)
            if angle_diff > _PARALLEL_ANGLE_TOLERANCE_DEG:
                continue
            distance = _line_distance(a, b)
            if not (_MIN_PAIR_DISTANCE_PX <= distance <= _MAX_PAIR_DISTANCE_PX):
                continue
            return a, b
    return None


def _all_parallel_pairs(lines: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    consumed: set[int] = set()
    for i in range(len(lines)):
        if i in consumed:
            continue
        for j in range(i + 1, len(lines)):
            if j in consumed:
                continue
            a, b = lines[i], lines[j]
            angle_diff = abs(_line_angle_deg(a) - _line_angle_deg(b))
            angle_diff = min(angle_diff, 180 - angle_diff)
            if angle_diff > _PARALLEL_ANGLE_TOLERANCE_DEG:
                continue
            distance = _line_distance(a, b)
            if not (_MIN_PAIR_DISTANCE_PX <= distance <= _MAX_PAIR_DISTANCE_PX):
                continue
            pairs.append((a, b))
            consumed.update({i, j})
            break
    return pairs


def _midline(pair: tuple[np.ndarray, np.ndarray]) -> list[tuple[float, float]]:
    (ax1, ay1, ax2, ay2) = pair[0][0]
    (bx1, by1, bx2, by2) = pair[1][0]
    return [
        ((ax1 + bx1) / 2.0, (ay1 + by1) / 2.0),
        ((ax2 + bx2) / 2.0, (ay2 + by2) / 2.0),
    ]


def _pair_bbox(pair: tuple[np.ndarray, np.ndarray]) -> Bbox:
    xs = [pair[0][0][0], pair[0][0][2], pair[1][0][0], pair[1][0][2]]
    ys = [pair[0][0][1], pair[0][0][3], pair[1][0][1], pair[1][0][3]]
    x_min, y_min = int(min(xs)), int(min(ys))
    x_max, y_max = int(max(xs)), int(max(ys))
    return (x_min, y_min, max(x_max - x_min, 1), max(y_max - y_min, 1))


# ── Candidate filters ────────────────────────────────────────────────────────

# Distance from page edge below which a bbox is considered "touching" the
# border — typical sheet-rule frames sit a few pixels in from the edge.
_BORDER_MARGIN_PX = 30

_MAX_ASPECT_RATIO = 25.0
_MAX_DIMENSION_FRACTION = 0.85  # neither side may span ≥ 85% of the page


def _passes_filters(bbox: Bbox, page_h: int, page_w: int) -> bool:
    x, y, w, h = bbox
    if w < 5 or h < 5:
        return False
    if (
        x < _BORDER_MARGIN_PX
        or y < _BORDER_MARGIN_PX
        or x + w > page_w - _BORDER_MARGIN_PX
        or y + h > page_h - _BORDER_MARGIN_PX
    ):
        return False
    if w / page_w >= _MAX_DIMENSION_FRACTION and h / page_h >= _MAX_DIMENSION_FRACTION:
        return False
    long_edge = max(w, h)
    short_edge = max(min(w, h), 1)
    if long_edge / short_edge > _MAX_ASPECT_RATIO:
        return False
    return True


def _non_max_suppress(bboxes: list[Bbox], *, iou_threshold: float) -> list[Bbox]:
    """Greedy NMS — keep the largest bbox first, drop later ones overlapping it."""
    sorted_bboxes = sorted(bboxes, key=lambda b: b[2] * b[3], reverse=True)
    kept: list[Bbox] = []
    for candidate in sorted_bboxes:
        if all(_iou(candidate, k) < iou_threshold for k in kept):
            kept.append(candidate)
    return kept


def _iou(a: Bbox, b: Bbox) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    inter_x1, inter_y1 = max(ax1, bx1), max(ay1, by1)
    inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)
    inter_w, inter_h = max(inter_x2 - inter_x1, 0), max(inter_y2 - inter_y1, 0)
    inter = inter_w * inter_h
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0
