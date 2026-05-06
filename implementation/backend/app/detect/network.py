"""Duct network graph (SOLUTION-DESIGN-V4 §3).

Nodes are connectors, terminals, and open ends. Edges are segments — each
edge carries its centerline polyline, length, dimension, and any terminals
that sit along its run (A10).

Crossings (A7): a single logical run rendered as solid + dashed sub-segments
collapses to a single edge. The crossing record names the over/under polygon
ids — both map to the same logical edge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.cv.types import Boundary, Connector, Crossing, DuctPolygon, Terminal
from app.detect.geometry import DEFAULT_DPI, length_ft, segment_centerline
from app.detect.types import NetworkEdge, NetworkNode
from app.schemas import ScaleInfo


@dataclass
class DuctNetwork:
    nodes: dict[str, NetworkNode] = field(default_factory=dict)
    edges: dict[str, NetworkEdge] = field(default_factory=dict)
    crossings: list[Crossing] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def neighbors(self, node_id: str) -> list[NetworkEdge]:
        return [e for e in self.edges.values() if node_id in (e.node_a_id, e.node_b_id)]

    def segments_incident_to(self, node_id: str) -> list[NetworkEdge]:
        return self.neighbors(node_id)

    def endpoints(self, segment_id: str) -> tuple[str, str]:
        edge = self.edges[segment_id]
        return edge.node_a_id, edge.node_b_id

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {
                    "id": n.id,
                    "kind": n.kind,
                    "position": list(n.position),
                    "cfm": n.cfm,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "id": e.id,
                    "polygon_id": e.polygon_id,
                    "node_a_id": e.node_a_id,
                    "node_b_id": e.node_b_id,
                    "centerline": [list(p) for p in e.centerline],
                    "length_ft": e.length_ft,
                    "dimension_value": e.dimension_value,
                    "dimension_shape": e.dimension_shape,
                    "terminal_ids_on_edge": list(e.terminal_ids_on_edge),
                }
                for e in self.edges.values()
            ],
            "warnings": list(self.warnings),
        }


def _connector_for_polygon(
    polygon_id: str, connectors: list[Connector]
) -> list[Connector]:
    return [c for c in connectors if polygon_id in c.incident_polygon_ids]


def _nearest_terminal(
    point: tuple[float, float], terminals: list[Terminal]
) -> Terminal | None:
    best: Terminal | None = None
    best_d = math.inf
    for t in terminals:
        d = math.hypot(point[0] - t.center[0], point[1] - t.center[1])
        if d <= t.radius and d < best_d:
            best_d = d
            best = t
    return best


def _resolve_endpoint(
    polygon: DuctPolygon,
    boundary: Boundary,
    connectors: list[Connector],
    terminals: list[Terminal],
    network: DuctNetwork,
) -> str:
    """Return the node id for one end of a segment; create the node if needed."""
    incident = _connector_for_polygon(polygon.id, connectors)
    for c in incident:
        d = math.hypot(boundary.point[0] - c.centroid[0], boundary.point[1] - c.centroid[1])
        # Connector face boundaries sit on the connector itself; pick the closest.
        if boundary.kind == "connector_face" or d < 50.0:
            if c.id not in network.nodes:
                network.nodes[c.id] = NetworkNode(id=c.id, kind="connector", position=c.centroid)
            return c.id

    term = _nearest_terminal(boundary.point, terminals)
    if term is not None:
        if term.id not in network.nodes:
            network.nodes[term.id] = NetworkNode(
                id=term.id, kind="terminal", position=term.center, cfm=term.cfm
            )
        return term.id

    open_id = f"open::{polygon.id}::{boundary.point[0]:.1f}_{boundary.point[1]:.1f}"
    if open_id not in network.nodes:
        network.nodes[open_id] = NetworkNode(id=open_id, kind="open_end", position=boundary.point)
    return open_id


def _terminals_along_edge(
    polygon: DuctPolygon,
    centerline_pts: list[tuple[float, float]],
    terminals: list[Terminal],
    endpoint_terminal_ids: set[str],
) -> list[str]:
    """Terminals whose center sits near the segment's interior (A10)."""
    if len(centerline_pts) < 2:
        return []
    (x0, y0), (x1, y1) = centerline_pts[0], centerline_pts[-1]
    seg_len = math.hypot(x1 - x0, y1 - y0)
    if seg_len == 0:
        return []
    on_edge: list[str] = []
    for t in terminals:
        if t.id in endpoint_terminal_ids:
            continue
        # Distance from point to segment (perpendicular).
        dx, dy = x1 - x0, y1 - y0
        tx, ty = t.center[0] - x0, t.center[1] - y0
        proj = (tx * dx + ty * dy) / (seg_len * seg_len)
        if not (0.0 <= proj <= 1.0):
            continue
        cross = abs(tx * dy - ty * dx) / seg_len
        if cross <= t.radius:
            on_edge.append(t.id)
    return on_edge


def _crossing_partner(polygon_id: str, crossings: list[Crossing]) -> str | None:
    for cr in crossings:
        if cr.over_segment_id == polygon_id:
            return cr.under_segment_id
        if cr.under_segment_id == polygon_id:
            return cr.over_segment_id
    return None


def build_network(
    segments: list[DuctPolygon],
    connectors: list[Connector],
    terminals: list[Terminal],
    crossings: list[Crossing],
    boundaries: list[Boundary] | None = None,
    scale: ScaleInfo | None = None,
    dpi: int = DEFAULT_DPI,
) -> DuctNetwork:
    """Stitch detected geometry into a connected graph for flow tracing.

    ``boundaries`` and ``scale`` are optional only so legacy callers in tests
    can build skeletal networks; the production runner always passes both.
    """
    network = DuctNetwork(crossings=list(crossings))
    boundaries = boundaries or []

    # Map each polygon to a logical edge id. Crossings collapse two polygons
    # to the same logical edge (A7).
    edge_id_for: dict[str, str] = {}
    for seg in segments:
        partner = _crossing_partner(seg.id, crossings)
        if partner is not None and partner in edge_id_for:
            edge_id_for[seg.id] = edge_id_for[partner]
        else:
            edge_id_for[seg.id] = f"edge::{seg.id}"

    endpoint_terminals: set[str] = set()
    for seg in segments:
        seg_boundaries = [b for b in boundaries if b.polygon_id == seg.id]
        for b in seg_boundaries:
            t = _nearest_terminal(b.point, terminals)
            if t is not None:
                endpoint_terminals.add(t.id)

    seen_edges: set[str] = set()
    for seg in segments:
        edge_id = edge_id_for[seg.id]
        if edge_id in seen_edges:
            continue
        seg_boundaries = [b for b in boundaries if b.polygon_id == seg.id]
        if len(seg_boundaries) < 2:
            network.warnings.append(f"segment {seg.id} missing boundaries; skipped")
            continue
        node_a = _resolve_endpoint(seg, seg_boundaries[0], connectors, terminals, network)
        node_b = _resolve_endpoint(seg, seg_boundaries[1], connectors, terminals, network)

        centerline = segment_centerline(seg, seg_boundaries)
        edge_len = (
            length_ft(centerline, scale, dpi)
            if scale is not None and centerline.points
            else 0.0
        )
        on_edge = _terminals_along_edge(
            seg, centerline.points, terminals, endpoint_terminals
        )
        # Register on-edge terminals as nodes so flow_trace/pressure can read
        # their CFM uniformly. They are not graph nodes (no edges incident on
        # them); treat them as decorations carrying CFM data only.
        for tid in on_edge:
            t = next((t for t in terminals if t.id == tid), None)
            if t is not None and t.id not in network.nodes:
                network.nodes[t.id] = NetworkNode(
                    id=t.id, kind="terminal", position=t.center, cfm=t.cfm
                )
        network.edges[edge_id] = NetworkEdge(
            id=edge_id,
            polygon_id=seg.id,
            node_a_id=node_a,
            node_b_id=node_b,
            centerline=list(centerline.points),
            length_ft=edge_len,
            dimension_value=None,
            dimension_shape=seg.shape_hint if seg.shape_hint != "unknown" else None,
            terminal_ids_on_edge=on_edge,
        )
        seen_edges.add(edge_id)

    return network
