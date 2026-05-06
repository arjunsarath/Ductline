"""Centerline + length + pixel-width sizing (SOLUTION-DESIGN-V4 §5).

Lengths come from the centerline polyline times scale. Unlabeled segments are
sized directly from pixel width (A9) — no inheritance from upstream segments.

Conventions
-----------
- Pixels are rasterized at ``dpi`` (default 300). The architectural scale is
  expressed as ``paper_inches_per_foot`` — paper inches drawn per real foot
  (e.g. 1/4" = 1'-0" → 0.25). Real feet per pixel = 1/dpi/paper_inches_per_foot.
- Polygon "pixel width" is the shorter side of the minimum-area rectangle of
  the polygon; "length axis" is the longer side. This gives a stable estimate
  for both axis-aligned and rotated runs.
"""

from __future__ import annotations

import math

from app.cv.types import Boundary, CenterlinePolyline, DuctPolygon
from app.schemas import ScaleInfo

DEFAULT_DPI = 300


def _pixels_to_feet(pixels: float, scale: ScaleInfo, dpi: int) -> float:
    """real_feet = pixels / dpi / paper_inches_per_foot."""
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    if scale.paper_inches_per_foot <= 0:
        raise ValueError("paper_inches_per_foot must be positive")
    return pixels / dpi / scale.paper_inches_per_foot


def _polyline_pixel_length(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def _min_area_rect(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Return (long_side_px, short_side_px) of the minimum-area bounding rect.

    Implemented via a rotating-calipers sweep over the convex hull. Pure-Python
    so the math layer stays free of OpenCV import in unit tests.
    """
    hull = _convex_hull(points)
    if len(hull) < 2:
        return 0.0, 0.0
    if len(hull) == 2:
        (x0, y0), (x1, y1) = hull
        return math.hypot(x1 - x0, y1 - y0), 0.0

    best_area = math.inf
    best_long = 0.0
    best_short = 0.0
    n = len(hull)
    for i in range(n):
        x0, y0 = hull[i]
        x1, y1 = hull[(i + 1) % n]
        edge_len = math.hypot(x1 - x0, y1 - y0)
        if edge_len == 0:
            continue
        ux, uy = (x1 - x0) / edge_len, (y1 - y0) / edge_len
        # Perpendicular axis (rotated 90°).
        vx, vy = -uy, ux
        u_min = u_max = (hull[0][0] - x0) * ux + (hull[0][1] - y0) * uy
        v_min = v_max = (hull[0][0] - x0) * vx + (hull[0][1] - y0) * vy
        for px, py in hull[1:]:
            up = (px - x0) * ux + (py - y0) * uy
            vp = (px - x0) * vx + (py - y0) * vy
            u_min, u_max = min(u_min, up), max(u_max, up)
            v_min, v_max = min(v_min, vp), max(v_max, vp)
        w, h = u_max - u_min, v_max - v_min
        area = w * h
        if area < best_area:
            best_area = area
            best_long = max(w, h)
            best_short = min(w, h)
    return best_long, best_short


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def segment_centerline(
    polygon: DuctPolygon, boundaries: list[Boundary]
) -> CenterlinePolyline:
    """Polyline along the principal axis between the segment's two boundary lines.

    For the math-layer MVP we approximate the centerline as the straight line
    between the two boundary midpoints. Curved runs are decomposed upstream
    into multiple polygons joined at connectors, so a straight midline matches
    the segment definition in §3 / A6.
    """
    ends = [b for b in boundaries if b.polygon_id == polygon.id]
    if len(ends) < 2:
        return CenterlinePolyline(polygon_id=polygon.id, points=[], pixel_length=0.0)
    a, b = ends[0].point, ends[1].point
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    return CenterlinePolyline(polygon_id=polygon.id, points=[a, b], pixel_length=length)


def length_ft(polyline: CenterlinePolyline, scale: ScaleInfo, dpi: int = DEFAULT_DPI) -> float:
    """Convert pixel length to feet using the title-block (or override) scale."""
    pixels = polyline.pixel_length or _polyline_pixel_length(polyline.points)
    return _pixels_to_feet(pixels, scale, dpi)


def diameter_from_pixel_width(
    polygon: DuctPolygon, scale: ScaleInfo, dpi: int = DEFAULT_DPI
) -> float:
    """Diameter in inches from polygon pixel width × paper scale (A9).

    Rounds **up** to the nearest whole inch — duct sizing is by stocked-size
    increments, never fractional.
    """
    _, short_side_px = _min_area_rect(polygon.points)
    real_feet = _pixels_to_feet(short_side_px, scale, dpi)
    inches = real_feet * 12.0
    return float(math.ceil(inches))


def cross_check_scale(
    polygon_pixel_width: float,
    labeled_diameter_in: float,
    scale: ScaleInfo,
    dpi: int = DEFAULT_DPI,
) -> float:
    """Return % deviation between labeled diameter and pixel-width-derived size.

    Caller decides the tolerance (design §11 uses ±3%).
    """
    if labeled_diameter_in <= 0:
        raise ValueError("labeled_diameter_in must be positive")
    derived_in = _pixels_to_feet(polygon_pixel_width, scale, dpi) * 12.0
    return abs(derived_in - labeled_diameter_in) / labeled_diameter_in * 100.0
