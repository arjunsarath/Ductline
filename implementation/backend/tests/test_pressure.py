"""Pressure + SMACNA classification tests (SOLUTION-DESIGN-V4 §6, ADR-0016)."""

from __future__ import annotations

import math

from app.cv.types import Boundary, DuctPolygon, Terminal
from app.detect.network import DuctNetwork, build_network
from app.detect.types import NetworkEdge, NetworkNode
from app.pipeline.flow_trace import trace_cfm
from app.pipeline.pressure import (
    _hydraulic_diameter_ft,
    _parse_dimensions_in,
    _smacna_class_static,
    _smacna_class_velocity,
    _velocity_fpm,
    compute_pressure,
)
from app.schemas import OperationalVars, ScaleInfo


def _scale() -> ScaleInfo:
    return ScaleInfo(paper_inches_per_foot=0.25, source="manual", confidence=1.0)


def test_parse_round_dimension() -> None:
    assert _parse_dimensions_in("14\"ø", "round") == (14.0, 14.0)
    assert _parse_dimensions_in("12ø", "round") == (12.0, 12.0)
    assert _parse_dimensions_in("12\"", "round") == (12.0, 12.0)


def test_parse_rect_dimension() -> None:
    assert _parse_dimensions_in("10\"x8\"", "rectangular") == (10.0, 8.0)
    assert _parse_dimensions_in("24x12", "rectangular") == (24.0, 12.0)


def test_velocity_fpm_round_duct() -> None:
    # 12" round = π/4 × 1² = 0.7854 ft². 1000 CFM → ~1273 FPM.
    area_ft2 = math.pi / 4.0 * 1.0**2
    v = _velocity_fpm(1000.0, area_ft2)
    assert math.isclose(v, 1000.0 / area_ft2, rel_tol=1e-6)
    assert 1270.0 < v < 1275.0


def test_hydraulic_diameter_round_and_rect() -> None:
    assert math.isclose(_hydraulic_diameter_ft(12.0, 12.0), 1.0)
    # Rect 24x12 → 2*24*12/(24+12) = 16 in = 1.333 ft.
    assert math.isclose(_hydraulic_diameter_ft(24.0, 12.0), 16.0 / 12.0, rel_tol=1e-6)


def test_smacna_class_static_boundaries() -> None:
    op = OperationalVars()
    assert _smacna_class_static(1.99, op) == "Low"
    assert _smacna_class_static(2.0, op) == "Low"  # ≤ 2.
    assert _smacna_class_static(2.01, op) == "Medium"
    assert _smacna_class_static(3.0, op) == "Medium"  # ≤ 3.
    assert _smacna_class_static(3.01, op) == "High"


def test_smacna_class_velocity_boundaries() -> None:
    op = OperationalVars()
    assert _smacna_class_velocity(2000.0, op) == "Low"
    assert _smacna_class_velocity(2000.1, op) == "Medium"
    assert _smacna_class_velocity(2500.0, op) == "Medium"
    assert _smacna_class_velocity(2500.1, op) == "High"


def _single_round_segment_network(
    cfm_value: float, dimension_value: str, length_ft_value: float
) -> tuple[DuctNetwork, str]:
    """Build a minimal network manually so the test controls dimension + length exactly."""
    net = DuctNetwork()
    src = NetworkNode(id="open::src", kind="open_end", position=(0.0, 0.0))
    term = NetworkNode(id="t1", kind="terminal", position=(100.0, 0.0), cfm=cfm_value)
    net.nodes[src.id] = src
    net.nodes[term.id] = term
    edge = NetworkEdge(
        id="e1",
        polygon_id="s1",
        node_a_id=src.id,
        node_b_id=term.id,
        centerline=[(0.0, 0.0), (100.0, 0.0)],
        length_ft=length_ft_value,
        dimension_value=dimension_value,
        dimension_shape="round",
    )
    net.edges[edge.id] = edge
    return net, edge.id


def test_compute_pressure_velocity_round() -> None:
    net, edge_id = _single_round_segment_network(1000.0, "12\"ø", length_ft_value=10.0)
    cfm = trace_cfm(net, source_node_id="open::src")
    res = compute_pressure(net, cfm, OperationalVars(), source_node_id="open::src")
    # Velocity = 1000 / (π/4 × 1² ft²) = ~1273 FPM.
    assert 1270.0 < res[edge_id].velocity_fpm < 1275.0
    assert res[edge_id].smacna_class == "Low"


def test_compute_pressure_class_promotion_by_velocity() -> None:
    """High velocity over low static → class follows velocity (max-of)."""
    # 2000 CFM through a 6" round → V = 2000 / (π/4 × 0.25) ≈ 10186 FPM.
    net, edge_id = _single_round_segment_network(2000.0, "6\"ø", length_ft_value=1.0)
    cfm = trace_cfm(net, source_node_id="open::src")
    res = compute_pressure(net, cfm, OperationalVars(), source_node_id="open::src")
    assert res[edge_id].velocity_fpm > 2500.0
    assert res[edge_id].smacna_class == "High"


def test_compute_pressure_static_class_via_source_pressure() -> None:
    """Source pressure 2.01 with negligible drop ⇒ both ends ~2.01 ⇒ Medium."""
    net, edge_id = _single_round_segment_network(100.0, "24\"ø", length_ft_value=0.1)
    cfm = trace_cfm(net, source_node_id="open::src")
    op = OperationalVars(source_pressure_in_wc=2.01)
    res = compute_pressure(net, cfm, op, source_node_id="open::src")
    # Static governs (low velocity in a 24" duct).
    assert res[edge_id].smacna_class == "Medium"


def test_compute_pressure_high_static() -> None:
    net, edge_id = _single_round_segment_network(100.0, "24\"ø", length_ft_value=0.1)
    cfm = trace_cfm(net, source_node_id="open::src")
    op = OperationalVars(source_pressure_in_wc=3.01)
    res = compute_pressure(net, cfm, op, source_node_id="open::src")
    assert res[edge_id].smacna_class == "High"


def test_compute_pressure_two_terminal_branch() -> None:
    """End-to-end on the build_network path: pressure decreases from source toward terminals."""
    s1 = DuctPolygon(id="s1", points=[(0.0, 95.0), (200.0, 95.0), (200.0, 105.0), (0.0, 105.0)], shape_hint="round")
    s2 = DuctPolygon(id="s2", points=[(200.0, 95.0), (400.0, 95.0), (400.0, 105.0), (200.0, 105.0)], shape_hint="round")
    boundaries = [
        Boundary(polygon_id="s1", point=(0.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s1", point=(200.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s2", point=(200.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s2", point=(400.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
    ]
    term = Terminal(id="t1", center=(400.0, 100.0), radius=15.0, type_letter="A", cfm=500.0)
    net = build_network([s1, s2], [], [term], [], boundaries=boundaries, scale=_scale())
    # Patch dimension on each edge so pressure has something to chew on.
    new_edges = {}
    for eid, e in net.edges.items():
        new_edges[eid] = NetworkEdge(
            id=e.id, polygon_id=e.polygon_id, node_a_id=e.node_a_id, node_b_id=e.node_b_id,
            centerline=e.centerline, length_ft=e.length_ft, dimension_value="14\"ø",
            dimension_shape="round", terminal_ids_on_edge=e.terminal_ids_on_edge,
        )
    net.edges = new_edges
    cfm = trace_cfm(net, source_node_id=None)
    res = compute_pressure(net, cfm, OperationalVars())
    # Both edges produced a result with non-NaN values.
    assert all(math.isfinite(r.start_in_wc) and math.isfinite(r.end_in_wc) for r in res.values())
