"""Pressure value + SMACNA classification per segment (SOLUTION-DESIGN-V4 §6).

Velocity pressure uses the standard-air shortcut ``VP = (V/4005)² × (ρ/0.075)``
in inches of water column. Friction loss along a segment is Darcy-form:

    ΔP_friction = f · (L_ft / Dh_ft) · VP

Fitting losses at incident connectors (and an optional terminal at the
downstream end) are ``K · VP``. The flex-duct equivalent length is added to
the friction term whenever a connector kind matching ``"flex"`` is incident.

Pressure walk: BFS from the source node. The source carries
``op_vars.source_pressure_in_wc`` (default 0). Pressure drops in the
direction of flow, so downstream pressures are smaller (more negative for a
return run; for the supply path we report drop magnitude as positive numbers
relative to the source datum).

SMACNA class: max of the static-pressure class and the velocity class
(ADR-0016). We classify on the higher of |start|/|end| static pressure
since the segment's worst-case static governs the duct construction class.
"""

from __future__ import annotations

import math
import re

from app.detect.network import DuctNetwork
from app.detect.types import NetworkEdge, NetworkNode
from app.pipeline.flow_trace import SegmentId, trace_cfm_with_orientation
from app.schemas import (
    CfmRange,
    OperationalVars,
    PressureResult,
    SmacnaClass,
)

ROUND_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\"|ø|in)?\s*(?:ø|⌀|round)", re.IGNORECASE)
ROUND_BARE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:\"|ø|in)?$")
RECT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[\"']?\s*[xX×]\s*(\d+(?:\.\d+)?)\s*[\"']?"
)


def _parse_dimensions_in(value: str | None, shape_hint: str | None) -> tuple[float, float]:
    """Parse a dimension label into (a_in, b_in). Round → (D, D); rect → (W, H).

    Falls back to (0, 0) when nothing parses. Caller treats zero-area as a
    warning and reports zero pressure drop.
    """
    if not value:
        return 0.0, 0.0
    rect = RECT_RE.search(value)
    if rect:
        return float(rect.group(1)), float(rect.group(2))
    if shape_hint == "round" or "ø" in value or "⌀" in value or "round" in value.lower():
        m = ROUND_RE.search(value) or ROUND_BARE_RE.match(value.strip())
        if m:
            d = float(m.group(1))
            return d, d
    bare = ROUND_BARE_RE.match(value.strip())
    if bare:
        d = float(bare.group(1))
        return d, d
    return 0.0, 0.0


def _hydraulic_diameter_ft(a_in: float, b_in: float) -> float:
    if a_in <= 0 or b_in <= 0:
        return 0.0
    if a_in == b_in:
        return a_in / 12.0
    # Rectangular: Dh = 4A / Pwet = 2WH / (W+H).
    return 2.0 * a_in * b_in / (a_in + b_in) / 12.0


def _area_ft2(a_in: float, b_in: float) -> float:
    if a_in <= 0 or b_in <= 0:
        return 0.0
    if a_in == b_in:
        return math.pi / 4.0 * (a_in / 12.0) ** 2
    return (a_in / 12.0) * (b_in / 12.0)


def _velocity_fpm(cfm: float, area_ft2: float) -> float:
    if area_ft2 <= 0:
        return 0.0
    return cfm / area_ft2


def _velocity_pressure_in_wc(velocity_fpm: float, density_lb_ft3: float) -> float:
    return (velocity_fpm / 4005.0) ** 2 * (density_lb_ft3 / 0.075)


def _connector_k(node: NetworkNode | None, op_vars: OperationalVars) -> float:
    if node is None or node.kind != "connector":
        return 0.0
    # Connector kind is encoded by the upstream detector and stored on the
    # NetworkNode position metadata path; for the math layer we look up by the
    # node id prefix — runner_v4 names connector ids "<kind>::<n>".
    kind = node.id.split("::", 1)[0] if "::" in node.id else "elbow"
    return float(op_vars.fitting_k_table.get(kind, 0.0))


def _terminal_k(node: NetworkNode | None, op_vars: OperationalVars) -> float:
    if node is None or node.kind != "terminal":
        return 0.0
    return float(op_vars.fitting_k_table.get("terminal", 0.0))


def _smacna_class_static(p_in_wc: float, op_vars: OperationalVars) -> SmacnaClass:
    th = op_vars.smacna_thresholds_in_wc
    if p_in_wc <= th.low_max_in_wc:
        return "Low"
    if p_in_wc <= th.medium_max_in_wc:
        return "Medium"
    return "High"


def _smacna_class_velocity(velocity_fpm: float, op_vars: OperationalVars) -> SmacnaClass:
    th = op_vars.velocity_thresholds_fpm
    if velocity_fpm <= th.low_max_fpm:
        return "Low"
    if velocity_fpm <= th.medium_max_fpm:
        return "Medium"
    return "High"


_CLASS_RANK: dict[SmacnaClass, int] = {"Low": 0, "Medium": 1, "High": 2}


def _max_class(a: SmacnaClass, b: SmacnaClass) -> SmacnaClass:
    return a if _CLASS_RANK[a] >= _CLASS_RANK[b] else b


def _edge_drop_in_wc(
    edge: NetworkEdge,
    cfm_in: CfmRange,
    op_vars: OperationalVars,
    upstream_node: NetworkNode | None,
    downstream_node: NetworkNode | None,
) -> tuple[float, float]:
    """Return (drop_in_wc, velocity_fpm) for a single edge.

    Velocity is computed from the upstream-end CFM (worst case, A10).
    """
    a_in, b_in = _parse_dimensions_in(edge.dimension_value, edge.dimension_shape)
    area = _area_ft2(a_in, b_in)
    dh = _hydraulic_diameter_ft(a_in, b_in)
    velocity = _velocity_fpm(cfm_in.start, area)
    if dh == 0 or velocity == 0:
        return 0.0, velocity
    vp = _velocity_pressure_in_wc(velocity, op_vars.air_density_lb_ft3)
    length_total = edge.length_ft + (
        op_vars.flex_equiv_length_ft
        if upstream_node and upstream_node.id.startswith("flex::")
        else 0.0
    )
    friction = op_vars.friction_factor * (length_total / dh) * vp
    k_total = _connector_k(upstream_node, op_vars) + _terminal_k(downstream_node, op_vars)
    return friction + k_total * vp, velocity


def compute_pressure(
    network: DuctNetwork,
    cfm_map: dict[SegmentId, CfmRange],
    op_vars: OperationalVars,
    source_node_id: str | None = None,
) -> dict[SegmentId, PressureResult]:
    """Per-segment pressure pair, velocity, and SMACNA class.

    Re-runs ``trace_cfm_with_orientation`` to recover (upstream, downstream)
    per edge so the caller doesn't have to thread orientation through. The
    extra traversal is O(V+E) — cheap relative to the rest of the pipeline.
    """
    _, orientation = trace_cfm_with_orientation(network, source_node_id)

    # Pressure datum at source(s).
    p_at_node: dict[str, float] = {}
    if source_node_id is None:
        sources = [
            n.id
            for n in network.nodes.values()
            if n.kind != "terminal" and len(network.neighbors(n.id)) == 1
        ]
        if len(sources) == 1:
            p_at_node[sources[0]] = op_vars.source_pressure_in_wc
    else:
        p_at_node[source_node_id] = op_vars.source_pressure_in_wc

    # Walk BFS in orientation order.
    queue: list[str] = list(p_at_node.keys())
    visited_edges: set[str] = set()
    drops: dict[str, tuple[float, float]] = {}

    while queue:
        node_id = queue.pop(0)
        for edge in network.neighbors(node_id):
            if edge.id in visited_edges or edge.id not in orientation:
                continue
            up_id, down_id = orientation[edge.id]
            if up_id != node_id:
                continue
            cfm = cfm_map.get(edge.id, CfmRange(start=0.0, end=0.0))
            drop, vel = _edge_drop_in_wc(
                edge,
                cfm,
                op_vars,
                network.nodes.get(up_id),
                network.nodes.get(down_id),
            )
            drops[edge.id] = (drop, vel)
            p_at_node[down_id] = p_at_node[up_id] - drop
            visited_edges.add(edge.id)
            queue.append(down_id)

    results: dict[SegmentId, PressureResult] = {}
    for edge_id in network.edges:
        drop, vel = drops.get(edge_id, (0.0, 0.0))
        if edge_id in orientation:
            up_id, down_id = orientation[edge_id]
            start_p = p_at_node.get(up_id, 0.0)
            end_p = p_at_node.get(down_id, start_p - drop)
        else:
            start_p, end_p = 0.0, 0.0
        worst = max(abs(start_p), abs(end_p))
        cls = _max_class(
            _smacna_class_static(worst, op_vars),
            _smacna_class_velocity(vel, op_vars),
        )
        results[edge_id] = PressureResult(
            start_in_wc=start_p,
            end_in_wc=end_p,
            smacna_class=cls,
            velocity_fpm=vel,
        )
    return results
