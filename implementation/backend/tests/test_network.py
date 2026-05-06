"""Network graph build tests (SOLUTION-DESIGN-V4 §3)."""

from __future__ import annotations

from app.cv.types import Boundary, Connector, Crossing, DuctPolygon, Terminal
from app.detect.network import build_network
from app.schemas import ScaleInfo


def _scale() -> ScaleInfo:
    return ScaleInfo(paper_inches_per_foot=0.25, source="manual", confidence=1.0)


def _rect_polygon(pid: str, x0: float, y0: float, x1: float, y1: float) -> DuctPolygon:
    return DuctPolygon(
        id=pid,
        points=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        shape_hint="round",
    )


def _mini_network() -> tuple[list[DuctPolygon], list[Connector], list[Terminal], list[Boundary]]:
    """Three segments meeting at a tee, two terminals at the ends.

    Layout (pixels):

        [src open]——s1——[tee]——s2——[term_b]
                          |
                          s3
                          |
                       [term_c]
    """
    s1 = _rect_polygon("s1", 0.0, 95.0, 200.0, 105.0)
    s2 = _rect_polygon("s2", 200.0, 95.0, 400.0, 105.0)
    s3 = _rect_polygon("s3", 195.0, 100.0, 205.0, 300.0)
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
    return [s1, s2, s3], [tee], [term_b, term_c], boundaries


def test_build_network_creates_edges_and_nodes() -> None:
    segments, connectors, terminals, boundaries = _mini_network()
    net = build_network(segments, connectors, terminals, [], boundaries=boundaries, scale=_scale())

    assert len(net.edges) == 3
    assert "tee::1" in net.nodes
    assert net.nodes["tee::1"].kind == "connector"
    assert "term_b" in net.nodes and net.nodes["term_b"].kind == "terminal"
    assert "term_c" in net.nodes and net.nodes["term_c"].kind == "terminal"

    # s1 should have one open_end node and the tee node.
    s1_edge = next(e for e in net.edges.values() if e.polygon_id == "s1")
    assert "tee::1" in (s1_edge.node_a_id, s1_edge.node_b_id)
    other = s1_edge.node_b_id if s1_edge.node_a_id == "tee::1" else s1_edge.node_a_id
    assert net.nodes[other].kind == "open_end"


def test_neighbors_and_endpoints() -> None:
    segments, connectors, terminals, boundaries = _mini_network()
    net = build_network(segments, connectors, terminals, [], boundaries=boundaries, scale=_scale())
    assert len(net.neighbors("tee::1")) == 3
    s1_edge = next(e for e in net.edges.values() if e.polygon_id == "s1")
    a, b = net.endpoints(s1_edge.id)
    assert {a, b} == {s1_edge.node_a_id, s1_edge.node_b_id}


def test_to_dict_serializes_graph() -> None:
    segments, connectors, terminals, boundaries = _mini_network()
    net = build_network(segments, connectors, terminals, [], boundaries=boundaries, scale=_scale())
    payload = net.to_dict()
    assert isinstance(payload["nodes"], list) and isinstance(payload["edges"], list)
    edge_ids = {e["id"] for e in payload["edges"]}
    assert len(edge_ids) == 3


def test_multi_terminal_attachment() -> None:
    """A terminal between the segment endpoints attaches via terminal_ids_on_edge (A10)."""
    s1 = _rect_polygon("s1", 0.0, 95.0, 400.0, 105.0)
    boundaries = [
        Boundary(polygon_id="s1", point=(0.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s1", point=(400.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
    ]
    midway = Terminal(id="term_mid", center=(200.0, 100.0), radius=10.0, type_letter="A", cfm=80.0)
    end_term = Terminal(id="term_end", center=(400.0, 100.0), radius=15.0, type_letter="A", cfm=120.0)
    net = build_network([s1], [], [midway, end_term], [], boundaries=boundaries, scale=_scale())

    edge = next(iter(net.edges.values()))
    assert "term_mid" in edge.terminal_ids_on_edge
    assert "term_end" not in edge.terminal_ids_on_edge  # endpoint, not on-edge.


def test_crossing_collapses_two_polygons_to_one_edge() -> None:
    s_solid = _rect_polygon("solid", 0.0, 95.0, 100.0, 105.0)
    s_dashed = _rect_polygon("dashed", 100.0, 95.0, 200.0, 105.0)
    boundaries = [
        Boundary(polygon_id="solid", point=(0.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="solid", point=(100.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="dashed", point=(100.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="dashed", point=(200.0, 100.0), normal=(1.0, 0.0), kind="open_end"),
    ]
    crossings = [Crossing(over_segment_id="solid", under_segment_id="dashed", region_bbox=(95, 95, 105, 105))]
    net = build_network([s_solid, s_dashed], [], [], crossings, boundaries=boundaries, scale=_scale())
    assert len(net.edges) == 1
