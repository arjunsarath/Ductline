"""CFM trace tests (SOLUTION-DESIGN-V4 §6)."""

from __future__ import annotations

import math

from app.cv.types import Boundary, Connector, DuctPolygon, Terminal
from app.detect.network import build_network
from app.pipeline.flow_trace import trace_cfm
from app.schemas import ScaleInfo


def _scale() -> ScaleInfo:
    return ScaleInfo(paper_inches_per_foot=0.25, source="manual", confidence=1.0)


def _rect(pid: str, x0: float, y0: float, x1: float, y1: float) -> DuctPolygon:
    return DuctPolygon(
        id=pid, points=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], shape_hint="round"
    )


def _two_terminal_network():
    s1 = _rect("s1", 0.0, 95.0, 200.0, 105.0)
    s2 = _rect("s2", 200.0, 95.0, 400.0, 105.0)
    s3 = _rect("s3", 195.0, 100.0, 205.0, 300.0)
    tee = Connector(
        id="tee::1",
        kind="tee",
        centroid=(200.0, 100.0),
        incident_polygon_ids=["s1", "s2", "s3"],
        bbox=(195.0, 95.0, 205.0, 105.0),
    )
    term_b = Terminal(id="term_b", center=(400.0, 100.0), radius=15.0, type_letter="A", cfm=100.0)
    term_c = Terminal(id="term_c", center=(200.0, 300.0), radius=15.0, type_letter="A", cfm=150.0)
    boundaries = [
        Boundary(polygon_id="s1", point=(0.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s1", point=(200.0, 100.0), normal=(1.0, 0.0), kind="connector_face"),
        Boundary(polygon_id="s2", point=(200.0, 100.0), normal=(1.0, 0.0), kind="connector_face"),
        Boundary(polygon_id="s2", point=(400.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s3", point=(200.0, 100.0), normal=(0.0, 1.0), kind="connector_face"),
        Boundary(polygon_id="s3", point=(200.0, 300.0), normal=(0.0, 1.0), kind="open_end"),
    ]
    return build_network([s1, s2, s3], [tee], [term_b, term_c], [], boundaries=boundaries, scale=_scale())


def test_trace_single_source_two_terminals() -> None:
    net = _two_terminal_network()
    cfm = trace_cfm(net, source_node_id=None)  # single non-terminal leaf is the open_end on s1.
    s1 = next(e.id for e in net.edges.values() if e.polygon_id == "s1")
    s2 = next(e.id for e in net.edges.values() if e.polygon_id == "s2")
    s3 = next(e.id for e in net.edges.values() if e.polygon_id == "s3")
    # s1 sits upstream of the tee — both terminals are downstream.
    assert math.isclose(cfm[s1].start, 250.0)
    assert math.isclose(cfm[s1].end, 250.0)
    # s2 / s3 carry their respective terminals all the way to the segment end.
    assert math.isclose(cfm[s2].start, 100.0)
    assert math.isclose(cfm[s2].end, 100.0)
    assert math.isclose(cfm[s3].start, 150.0)
    assert math.isclose(cfm[s3].end, 150.0)


def test_trace_multi_terminal_segment_range() -> None:
    """A run with three vents along it: each strips its CFM from the through flow."""
    s1 = _rect("s1", 0.0, 95.0, 600.0, 105.0)
    boundaries = [
        Boundary(polygon_id="s1", point=(0.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s1", point=(600.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
    ]
    end_term = Terminal(id="t_end", center=(600.0, 100.0), radius=15.0, type_letter="A", cfm=80.0)
    mid_a = Terminal(id="t_mid_a", center=(200.0, 100.0), radius=10.0, type_letter="A", cfm=70.0)
    mid_b = Terminal(id="t_mid_b", center=(400.0, 100.0), radius=10.0, type_letter="A", cfm=70.0)
    net = build_network([s1], [], [end_term, mid_a, mid_b], [], boundaries=boundaries, scale=_scale())

    edge = next(iter(net.edges.values()))
    assert sorted(edge.terminal_ids_on_edge) == ["t_mid_a", "t_mid_b"]

    cfm = trace_cfm(net, source_node_id=None)
    rng = cfm[edge.id]
    assert math.isclose(rng.start, 220.0)  # 80 + 70 + 70.
    assert math.isclose(rng.end, 80.0)  # only the endpoint terminal beyond the run.


def test_trace_explicit_source_overrides_default() -> None:
    net = _two_terminal_network()
    open_id = next(n.id for n in net.nodes.values() if n.kind == "open_end")
    cfm = trace_cfm(net, source_node_id=open_id)
    s1_edge_id = next(e.id for e in net.edges.values() if e.polygon_id == "s1")
    assert math.isclose(cfm[s1_edge_id].start, 250.0)


def test_trace_ambiguous_source_warns_and_zeros() -> None:
    """Two non-terminal leaves with no terminals: ambiguous → warning, zero CFM."""
    s1 = _rect("s1", 0.0, 95.0, 100.0, 105.0)
    boundaries = [
        Boundary(polygon_id="s1", point=(0.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s1", point=(100.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
    ]
    net = build_network([s1], [], [], [], boundaries=boundaries, scale=_scale())
    cfm = trace_cfm(net, source_node_id=None)
    edge_id = next(iter(net.edges.values())).id
    assert cfm[edge_id].start == 0.0
    assert any("ambiguous source" in w for w in net.warnings)
