"""Connector detection — transitions, elbows, tees, Y-branches, equipment (A6).

Connectors are graph nodes; they have fitting K-values but no length or CFM
of their own. Equipment boxes are treated generically in MVP — see A11. The
`kind` field is informational; the network builder treats every connector
the same way.

Approach:
  1. Inflate each duct polygon by a small kernel and union them.
  2. Take all closed contours that are NOT in this union — those are the
     fittings between ducts (transitions/elbows/tees/equipment).
  3. Classify by combining the contour's vertex count, aspect, and a check
     for an internal X (equipment markings).
  4. For each connector, record which duct polygons touch it (incident IDs).
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from app.cv._primitives import binary_ink
from app.cv.types import Connector, ConnectorKind, DuctPolygon

# Pixels by which duct polygons are dilated when looking for incident ducts.
_INCIDENCE_DILATE_PX = 6
# Min connector area; below this it's text or a tick mark.
_MIN_AREA_PX = 400
# Drop anything ≥ this fraction of the page — that's the title-block frame.
_MAX_AREA_FRAC = 0.4
# Vertex-count thresholds after polygon approximation.
_RECT_VERTICES = 4
_TEE_MIN_VERTICES = 5
# Aspect threshold above which a quad reads as a transition (taper) vs a box.
_TRANSITION_ASPECT = 1.25
# Approximation epsilon as a fraction of the contour perimeter.
_APPROX_EPS_FRAC = 0.02


def detect_connectors(
    image: Image.Image, polygons: list[DuctPolygon]
) -> list[Connector]:
    """Identify connector regions and bind them to incident duct polygons.

    Polygons whose shape is `unknown` (equipment squares, trapezoidal
    transitions) are *not* treated as ducts — they are forwarded into the
    fitting mask so they re-emerge as connector contours.
    """
    mask = binary_ink(image)
    duct_polygons = [p for p in polygons if p.shape_hint != "unknown"]
    duct_union = _polygons_to_mask(duct_polygons, mask.shape)
    fitting_mask = cv2.bitwise_and(mask, cv2.bitwise_not(duct_union))

    h, w = mask.shape
    page_area = float(h * w)

    contours, _ = cv2.findContours(fitting_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    connectors: list[Connector] = []
    incidence_lookup = _build_incidence_lookup(duct_polygons, mask.shape)
    for idx, contour in enumerate(contours):
        connector = _contour_to_connector(
            contour,
            page_area=page_area,
            incidence_lookup=incidence_lookup,
            mask=mask,
            connector_id=f"conn_{idx}",
        )
        if connector is not None:
            connectors.append(connector)
    connectors.extend(
        _promote_unknown_polygons(polygons, duct_polygons, mask.shape)
    )
    return _suppress_overlapping(connectors)


def _polygons_to_mask(
    polygons: list[DuctPolygon], shape: tuple[int, int]
) -> np.ndarray:
    canvas = np.zeros(shape, dtype=np.uint8)
    for poly in polygons:
        pts = np.array(
            [[int(round(x)), int(round(y))] for x, y in poly.points], dtype=np.int32
        )
        cv2.fillPoly(canvas, [pts], 255)
    return canvas


def _build_incidence_lookup(
    polygons: list[DuctPolygon], shape: tuple[int, int]
) -> dict[str, np.ndarray]:
    """Pre-dilate each polygon so we can test connector neighbours cheaply."""
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (_INCIDENCE_DILATE_PX, _INCIDENCE_DILATE_PX)
    )
    lookup: dict[str, np.ndarray] = {}
    for poly in polygons:
        canvas = np.zeros(shape, dtype=np.uint8)
        pts = np.array(
            [[int(round(x)), int(round(y))] for x, y in poly.points], dtype=np.int32
        )
        cv2.fillPoly(canvas, [pts], 255)
        lookup[poly.id] = cv2.dilate(canvas, kernel)
    return lookup


def _contour_to_connector(
    contour: np.ndarray,
    *,
    page_area: float,
    incidence_lookup: dict[str, np.ndarray],
    mask: np.ndarray,
    connector_id: str,
) -> Connector | None:
    area = float(cv2.contourArea(contour))
    if area < _MIN_AREA_PX or area > page_area * _MAX_AREA_FRAC:
        return None

    x, y, w, h = cv2.boundingRect(contour)
    if w == 0 or h == 0:
        return None

    eps = _APPROX_EPS_FRAC * cv2.arcLength(contour, closed=True)
    approx = cv2.approxPolyDP(contour, eps, closed=True)
    vertices = len(approx)
    aspect = max(w, h) / float(min(w, h))
    bbox_fill = area / float(w * h)

    incident_ids = _incident_polygon_ids(contour, incidence_lookup)
    kind = _classify_connector(vertices, aspect, bbox_fill, contour, mask)
    moments = cv2.moments(contour)
    if moments["m00"] == 0:
        cx, cy = float(x + w * 0.5), float(y + h * 0.5)
    else:
        cx = moments["m10"] / moments["m00"]
        cy = moments["m01"] / moments["m00"]

    return Connector(
        id=connector_id,
        kind=kind,
        centroid=(float(cx), float(cy)),
        incident_polygon_ids=incident_ids,
        bbox=(float(x), float(y), float(w), float(h)),
    )


def _incident_polygon_ids(
    contour: np.ndarray, incidence_lookup: dict[str, np.ndarray]
) -> list[str]:
    if not incidence_lookup:
        return []
    canvas = np.zeros_like(next(iter(incidence_lookup.values())))
    cv2.drawContours(canvas, [contour], -1, 255, thickness=cv2.FILLED)
    incident: list[str] = []
    for poly_id, dilated in incidence_lookup.items():
        if cv2.bitwise_and(canvas, dilated).any():
            incident.append(poly_id)
    return incident


def _classify_connector(
    vertices: int,
    aspect: float,
    bbox_fill: float,
    contour: np.ndarray,
    mask: np.ndarray,
) -> ConnectorKind:
    """Best-effort classification. Per A11 the network treats all the same."""
    if vertices >= _TEE_MIN_VERTICES and bbox_fill < 0.55:
        return "elbow"
    if vertices >= _TEE_MIN_VERTICES:
        return "tee"
    if vertices == _RECT_VERTICES:
        if _has_internal_cross(contour, mask):
            return "equipment"
        if aspect >= _TRANSITION_ASPECT:
            return "transition"
        return "equipment"
    return "transition"


def _has_internal_cross(contour: np.ndarray, mask: np.ndarray) -> bool:
    """Equipment is drawn as a square with an X. Sample the two diagonals."""
    x, y, w, h = cv2.boundingRect(contour)
    if w < 8 or h < 8:
        return False
    diag_a = _line_ink_ratio(mask, (x, y), (x + w - 1, y + h - 1))
    diag_b = _line_ink_ratio(mask, (x, y + h - 1), (x + w - 1, y))
    return diag_a > 0.4 and diag_b > 0.4


def _line_ink_ratio(
    mask: np.ndarray, a: tuple[int, int], b: tuple[int, int]
) -> float:
    samples = 32
    h, w = mask.shape
    xs = np.linspace(a[0], b[0], samples).astype(int).clip(0, w - 1)
    ys = np.linspace(a[1], b[1], samples).astype(int).clip(0, h - 1)
    return float(np.mean(mask[ys, xs] > 0))


def _promote_unknown_polygons(
    polygons: list[DuctPolygon],
    duct_polygons: list[DuctPolygon],
    shape: tuple[int, int],
) -> list[Connector]:
    """Treat any `unknown`-shape polygon as a connector candidate.

    The `RETR_LIST` contour pass in `duct_outline` picks up trapezoidal
    transitions and elbow polygons; they fail the rectangular/round filters
    and arrive here flagged `unknown`. They are bona-fide connectors and the
    network builder needs them as graph nodes.
    """
    incidence_lookup = _build_incidence_lookup(duct_polygons, shape)
    connectors: list[Connector] = []
    for idx, poly in enumerate(polygons):
        if poly.shape_hint != "unknown" or poly.bbox is None:
            continue
        contour = np.array(
            [[int(round(x)), int(round(y))] for x, y in poly.points], dtype=np.int32
        ).reshape(-1, 1, 2)
        incident = _incident_polygon_ids(contour, incidence_lookup)
        connectors.append(
            Connector(
                id=f"conn_poly_{idx}",
                kind="transition",
                centroid=_polygon_centroid(poly),
                incident_polygon_ids=incident,
                bbox=poly.bbox,
            )
        )
    return connectors


def _polygon_centroid(poly: DuctPolygon) -> tuple[float, float]:
    if poly.bbox is None:
        return (0.0, 0.0)
    x, y, w, h = poly.bbox
    return (float(x + w * 0.5), float(y + h * 0.5))


def _suppress_overlapping(
    connectors: list[Connector], *, iou_threshold: float = 0.6
) -> list[Connector]:
    """Drop connectors whose bbox IoU with a kept connector exceeds threshold."""
    sorted_conns = sorted(
        connectors, key=lambda c: c.bbox[2] * c.bbox[3], reverse=True
    )
    kept: list[Connector] = []
    for cand in sorted_conns:
        if not any(_bbox_iou(cand.bbox, k.bbox) >= iou_threshold for k in kept):
            kept.append(cand)
    return kept


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
