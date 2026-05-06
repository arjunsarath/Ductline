"""Cross-cut bar detection — the geometric definition of a segment (A6).

A segment is a duct region bounded by two perpendicular bars at its ends.
Connectors (transitions, elbows, tees, equipment) live *between* these bars
and are handled in `connectors.py`.

Algorithm:
  1. Build a strip mask over the polygon's bbox.
  2. Walk along the principal axis in fixed-pixel steps.
  3. At each step take a 1-D profile across the duct width perpendicular to
     the axis. A cross-cut bar shows as a strong, narrow ink stripe spanning
     ≥ ~80 % of the duct width.
  4. Cluster contiguous high-response steps; emit one boundary per cluster.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from app.cv._primitives import binary_ink
from app.cv.types import Boundary, DuctPolygon

# Duct-width fraction the bar must span to count as a cross-cut. The bars in
# testset2 span the full duct; 0.7 leaves headroom for binarisation noise.
_SPAN_FRACTION = 0.7
# Minimum ink-density along the perpendicular profile to count as a hit.
_PROFILE_INK_MIN = 0.25
# Step in pixels along the axis when scanning. 2 px at 300 DPI is plenty.
_AXIS_STEP_PX = 2
# Cluster contiguous hits separated by at most this many steps.
_CLUSTER_GAP_STEPS = 2


def find_segment_boundaries(polygon: DuctPolygon, image: Image.Image) -> list[Boundary]:
    """Locate the perpendicular bar(s) that delimit this polygon's segment ends."""
    if polygon.bbox is None or polygon.principal_axis is None:
        return []

    mask = binary_ink(image)
    bbox_mask = _polygon_mask(polygon, mask.shape)
    duct_ink = cv2.bitwise_and(mask, mask, mask=bbox_mask)

    axis = np.array(polygon.principal_axis, dtype=np.float32)
    perp = np.array((-axis[1], axis[0]), dtype=np.float32)
    centroid = _centroid_of_bbox(polygon.bbox)
    half_width = max(polygon.est_width_px or 0.0, 1.0) * 0.5
    half_length = _half_length_along_axis(polygon, centroid, axis)

    hits: list[tuple[int, tuple[float, float]]] = []
    step_count = int(half_length // _AXIS_STEP_PX)
    for step in range(-step_count, step_count + 1):
        offset = float(step * _AXIS_STEP_PX)
        sample = centroid + axis * offset
        if _bar_present(duct_ink, sample, perp, half_width):
            hits.append((step, (float(sample[0]), float(sample[1]))))

    clusters = _cluster_steps(hits)
    return [
        Boundary(
            polygon_id=polygon.id,
            point=point,
            normal=(float(perp[0]), float(perp[1])),
            kind="crosscut",
            position_along_axis=float(step * _AXIS_STEP_PX),
        )
        for step, point in clusters
    ]


def _polygon_mask(polygon: DuctPolygon, shape: tuple[int, int]) -> np.ndarray:
    canvas = np.zeros(shape, dtype=np.uint8)
    pts = np.array([[int(round(x)), int(round(y))] for x, y in polygon.points], dtype=np.int32)
    cv2.fillPoly(canvas, [pts], 255)
    return canvas


def _centroid_of_bbox(bbox: tuple[float, float, float, float]) -> np.ndarray:
    x, y, w, h = bbox
    return np.array((x + w * 0.5, y + h * 0.5), dtype=np.float32)


def _half_length_along_axis(
    polygon: DuctPolygon, centroid: np.ndarray, axis: np.ndarray
) -> float:
    pts = np.array(polygon.points, dtype=np.float32) - centroid
    projections = pts @ axis
    return float(np.max(np.abs(projections))) if projections.size else 0.0


def _bar_present(
    mask: np.ndarray, center: np.ndarray, perp: np.ndarray, half_width: float
) -> bool:
    """Sample mask along the perpendicular line; return True if span is inked."""
    samples = max(8, int(half_width * 2))
    h, w = mask.shape
    inked = 0
    valid = 0
    for t in np.linspace(-half_width, half_width, samples):
        x = int(round(center[0] + perp[0] * t))
        y = int(round(center[1] + perp[1] * t))
        if 0 <= x < w and 0 <= y < h:
            valid += 1
            if mask[y, x] > 0:
                inked += 1
    if valid == 0:
        return False
    span_ratio = inked / valid
    return span_ratio >= _SPAN_FRACTION and span_ratio >= _PROFILE_INK_MIN


def _cluster_steps(
    hits: list[tuple[int, tuple[float, float]]],
) -> list[tuple[int, tuple[float, float]]]:
    if not hits:
        return []
    clusters: list[list[tuple[int, tuple[float, float]]]] = [[hits[0]]]
    for hit in hits[1:]:
        if hit[0] - clusters[-1][-1][0] <= _CLUSTER_GAP_STEPS:
            clusters[-1].append(hit)
        else:
            clusters.append([hit])
    return [_cluster_centroid(c) for c in clusters]


def _cluster_centroid(
    cluster: list[tuple[int, tuple[float, float]]],
) -> tuple[int, tuple[float, float]]:
    mid_step = cluster[len(cluster) // 2][0]
    xs = [pt[0] for _, pt in cluster]
    ys = [pt[1] for _, pt in cluster]
    return mid_step, (float(np.mean(xs)), float(np.mean(ys)))
