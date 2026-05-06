"""Internal CV dataclasses passed between detection stages.

Pydantic is reserved for the API boundary (`schemas.py`); inside the pipeline
we use plain dataclasses so stages can attach numpy arrays and OpenCV handles
without serialization overhead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PolygonPoints = list[tuple[float, float]]
Polyline = list[tuple[float, float]]

ConnectorKind = Literal["transition", "elbow", "tee", "y_branch", "equipment"]
BoundaryKind = Literal["crosscut", "connector_face", "open_end"]


@dataclass(frozen=True)
class DuctPolygon:
    id: str
    points: PolygonPoints
    shape_hint: Literal["round", "rectangular", "unknown"] = "unknown"
    # Optional geometric metadata produced by `duct_outline.detect_duct_polygons`.
    # Defaults keep older call sites that build a polygon from points alone valid.
    bbox: tuple[float, float, float, float] | None = None
    principal_axis: tuple[float, float] | None = None
    est_width_px: float | None = None


@dataclass(frozen=True)
class Boundary:
    polygon_id: str
    point: tuple[float, float]
    normal: tuple[float, float]
    kind: BoundaryKind
    # Signed distance along the polygon's principal axis from the bbox centroid.
    # `None` for boundaries created without an axis reference.
    position_along_axis: float | None = None


@dataclass(frozen=True)
class Connector:
    id: str
    kind: ConnectorKind
    centroid: tuple[float, float]
    incident_polygon_ids: list[str]
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Terminal:
    id: str
    center: tuple[float, float]
    radius: float
    type_letter: str | None
    cfm: float | None


@dataclass(frozen=True)
class Crossing:
    over_segment_id: str
    under_segment_id: str
    region_bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Label:
    polygon_id: str
    raw_text: str
    bbox: tuple[float, float, float, float]
    orientation_deg: Literal[0, 90]
    parsed_value: str | None = None
    parsed_shape: Literal["round", "rectangular"] | None = None


@dataclass(frozen=True)
class CenterlinePolyline:
    polygon_id: str
    points: Polyline
    pixel_length: float = field(default=0.0)
