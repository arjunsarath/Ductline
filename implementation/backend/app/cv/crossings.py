"""Dashed-under-solid crossing resolution (A7).

When two ducts visually cross, the one rendered dashed passes underneath; the
solid one is continuous. The dashed run is logically a single segment — we
record the crossing region so the renderer can darken the overlap.

Approach:
  1. Find pairs of duct polygons whose bboxes intersect.
  2. In the overlap region, examine each polygon's edge: a contour with high
     ink density along its boundary is solid; a contour with periodic gaps
     (low density) is dashed.
  3. The dashed polygon is the under-passing duct.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.cv._primitives import binary_ink
from app.cv.types import Crossing, DuctPolygon

# Edge-ink ratios: a solid line samples > _SOLID_INK_MIN of its length;
# a dashed line samples below _DASHED_INK_MAX. Values between are ambiguous.
_SOLID_INK_MIN = 0.75
_DASHED_INK_MAX = 0.55
# Minimum bbox-overlap area in px to treat the pair as a crossing candidate.
_MIN_OVERLAP_PX = 64


def resolve_crossings(
    image: Image.Image, segments: list[DuctPolygon]
) -> list[Crossing]:
    """Detect dashed-under-solid intersections and report their over/under pairs."""
    mask = binary_ink(image)
    crossings: list[Crossing] = []
    for i, a in enumerate(segments):
        for b in segments[i + 1 :]:
            crossing = _check_pair(a, b, mask)
            if crossing is not None:
                crossings.append(crossing)
    return crossings


def _check_pair(
    a: DuctPolygon, b: DuctPolygon, mask: np.ndarray
) -> Crossing | None:
    if a.bbox is None or b.bbox is None:
        return None
    overlap = _bbox_intersection(a.bbox, b.bbox)
    if overlap is None:
        return None
    ox, oy, ow, oh = overlap
    if ow * oh < _MIN_OVERLAP_PX:
        return None

    a_ratio = _edge_ink_ratio(a, mask, overlap)
    b_ratio = _edge_ink_ratio(b, mask, overlap)
    over_id, under_id = _disambiguate(a, b, a_ratio, b_ratio)
    if over_id is None or under_id is None:
        return None
    return Crossing(
        over_segment_id=over_id,
        under_segment_id=under_id,
        region_bbox=(float(ox), float(oy), float(ow), float(oh)),
    )


def _bbox_intersection(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float] | None:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


def _edge_ink_ratio(
    polygon: DuctPolygon, mask: np.ndarray, overlap: tuple[float, float, float, float]
) -> float:
    """Fraction of the polygon's boundary inside `overlap` that is inked.

    A solid line returns ~1.0; a dashed line returns < ~0.5 because the gaps
    between dashes hit white pixels.
    """
    pts = np.array(polygon.points, dtype=np.float32)
    samples_x: list[int] = []
    samples_y: list[int] = []
    h, w = mask.shape
    for i in range(len(pts)):
        p1 = pts[i]
        p2 = pts[(i + 1) % len(pts)]
        n_samples = max(8, int(np.linalg.norm(p2 - p1)))
        for t in np.linspace(0.0, 1.0, n_samples):
            x = float(p1[0] * (1 - t) + p2[0] * t)
            y = float(p1[1] * (1 - t) + p2[1] * t)
            if _inside_overlap(x, y, overlap):
                samples_x.append(int(round(x)))
                samples_y.append(int(round(y)))
    if not samples_x:
        return 0.0
    xs = np.clip(np.array(samples_x), 0, w - 1)
    ys = np.clip(np.array(samples_y), 0, h - 1)
    return float(np.mean(mask[ys, xs] > 0))


def _inside_overlap(
    x: float, y: float, overlap: tuple[float, float, float, float]
) -> bool:
    ox, oy, ow, oh = overlap
    return ox <= x <= ox + ow and oy <= y <= oy + oh


def _disambiguate(
    a: DuctPolygon, b: DuctPolygon, a_ratio: float, b_ratio: float
) -> tuple[str | None, str | None]:
    """Return (over_id, under_id), or (None, None) if both look the same."""
    a_solid = a_ratio >= _SOLID_INK_MIN
    b_solid = b_ratio >= _SOLID_INK_MIN
    a_dashed = a_ratio <= _DASHED_INK_MAX
    b_dashed = b_ratio <= _DASHED_INK_MAX
    if a_solid and b_dashed:
        return a.id, b.id
    if b_solid and a_dashed:
        return b.id, a.id
    return None, None
