"""Find every closed-shape contour in a binarised drawing.

Used as the first detection step after grey-removal: the goal here is RECALL,
not precision — return polygons of any vertex count (rectangles, triangles,
trapezoids, semicircle approximations) so the operator can decide which
classes to filter in subsequent stages.

The function name and exported symbol are kept as ``find_all_rectangles`` to
avoid frontend/schema churn; what it actually returns is "any closed contour
approximated as a polygon."
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

# Skip noise contours below this minimum bounding-box side length (pixels).
_MIN_SIDE_PX = 6
# Approximate-polygon epsilon as a fraction of the contour perimeter; smaller
# values keep more vertices (curves stay curved), larger values simplify hard.
_APPROX_EPSILON_FRAC = 0.01
# A contour is treated as a rectangle (axis-aligned OR rotated) when its area
# fills its minimum-area rotated bbox above this ratio. Triangles ~0.5,
# trapezoids ~0.7. Letter strokes pass this too — relying on the empty/full
# pre-filters in rect_filters.py to drop them downstream.
_RECT_AREA_FILL_RATIO = 0.85


def find_all_rectangles(image: Image.Image) -> list[list[tuple[int, int]]]:
    """Return every closed contour as an N-vertex polygon (N ≥ 3).

    Operates on the binarised raster (ink → black, paper → white). Each result
    is the contour's polygon approximation; vertex count varies by shape
    complexity (4 for axis-aligned rects, 3 for triangles, 5–8 for trapezoids
    and elbows, 12+ for semicircles/curves). Coordinates are integer raster
    pixels in the same space as ``PageDims``.
    """
    luma = np.asarray(image.convert("L"))
    # Ink is black after grey-removal; invert so contours sit on white pixels.
    _, binary = cv2.threshold(luma, 127, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    polygons: list[list[tuple[int, int]]] = []
    for contour in contours:
        if len(contour) < 3:
            continue
        _, _, w, h = cv2.boundingRect(contour)
        if min(w, h) < _MIN_SIDE_PX:
            continue
        epsilon = _APPROX_EPSILON_FRAC * cv2.arcLength(contour, closed=True)
        approx = cv2.approxPolyDP(contour, epsilon, closed=True)
        if len(approx) < 3:
            continue
        # Rotated rectangles staircase across raster pixels and approxPolyDP
        # leaves them with 6–12 noisy vertices. If the contour's area fills
        # its minAreaRect tightly (>85%), the underlying shape IS a rectangle
        # — emit the four clean rotated-bbox corners so downstream filters
        # see a 4-vertex shape and the overlay renders the proper angle.
        contour_area = float(cv2.contourArea(contour))
        rot_rect = cv2.minAreaRect(contour)
        (_, _), (rw, rh), _ = rot_rect
        rot_area = float(rw) * float(rh)
        if rot_area > 0 and contour_area / rot_area > _RECT_AREA_FILL_RATIO:
            box = cv2.boxPoints(rot_rect)
            corners = [(int(round(p[0])), int(round(p[1]))) for p in box]
        else:
            corners = [(int(p[0][0]), int(p[0][1])) for p in approx]
        polygons.append(corners)
    return polygons
