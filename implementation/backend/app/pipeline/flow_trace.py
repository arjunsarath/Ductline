"""CFM trace and direction inference (SOLUTION-DESIGN-V4 §6).

Default direction flows from the single non-terminal node (equipment / open
source) toward terminals. The user can override by passing
``source_node_id``. Open-ended runs without a terminal carry zero CFM (A13).

Per-segment CFM is the sum of all terminal CFM reachable through the
segment's downstream end. For multi-terminal segments (A10) the start CFM
includes terminals on the segment beyond the start position; the end CFM
excludes terminals reached only by the start side. We model the segment as
"start = upstream end" and "end = downstream end", so:

    start_cfm = end_cfm + Σ cfm(terminals_on_segment)

If a terminal sits at the end node it contributes once via the end node, not
again as an on-segment terminal.
"""

from __future__ import annotations

from app.detect.network import DuctNetwork
from app.detect.types import NetworkEdge
from app.schemas import CfmRange

SegmentId = str


def _terminal_total(network: DuctNetwork, node_ids: set[str]) -> float:
    total = 0.0
    for nid in node_ids:
        node = network.nodes.get(nid)
        if node is None or node.kind != "terminal":
            continue
        total += node.cfm or 0.0
    return total


def _on_edge_terminal_cfm(network: DuctNetwork, edge: NetworkEdge) -> float:
    total = 0.0
    for tid in edge.terminal_ids_on_edge:
        node = network.nodes.get(tid)
        if node is None or node.kind != "terminal":
            continue
        total += node.cfm or 0.0
    return total


def _pick_source(network: DuctNetwork) -> str | None:
    """Pick a default source if none was provided.

    Source = the unique non-terminal leaf (degree 1, not a terminal). Multiple
    candidates → ambiguous, return None.
    """
    candidates: list[str] = []
    for node in network.nodes.values():
        if node.kind == "terminal":
            continue
        deg = len(network.neighbors(node.id))
        if deg == 1:
            candidates.append(node.id)
    if len(candidates) == 1:
        return candidates[0]
    return None


def trace_cfm(
    network: DuctNetwork, source_node_id: str | None
) -> dict[SegmentId, CfmRange]:
    """Return start/end CFM per segment by summing downstream terminal CFM."""
    cfm_map, _ = trace_cfm_with_orientation(network, source_node_id)
    return cfm_map


def trace_cfm_with_orientation(
    network: DuctNetwork, source_node_id: str | None
) -> tuple[dict[SegmentId, CfmRange], dict[SegmentId, tuple[str, str]]]:
    """Same as ``trace_cfm`` but also returns (upstream, downstream) per edge.

    The pressure walk needs orientation; exposing it here keeps the BFS in one
    place rather than duplicating the traversal in pressure.py.
    """
    if source_node_id is None:
        source_node_id = _pick_source(network)
    if source_node_id is None:
        network.warnings.append(
            "trace_cfm: ambiguous source — multiple non-terminal leaves found"
        )
        empty = {eid: CfmRange(start=0.0, end=0.0) for eid in network.edges}
        return empty, {}
    if source_node_id not in network.nodes:
        raise KeyError(f"source_node_id {source_node_id!r} not in network")

    # BFS from source, recording parent for each edge so we know orientation.
    parent_node: dict[str, str | None] = {source_node_id: None}
    edge_orientation: dict[str, tuple[str, str]] = {}  # edge_id → (upstream, downstream)
    queue: list[str] = [source_node_id]
    while queue:
        node_id = queue.pop(0)
        for edge in network.neighbors(node_id):
            other = edge.node_b_id if edge.node_a_id == node_id else edge.node_a_id
            if edge.id in edge_orientation:
                continue
            edge_orientation[edge.id] = (node_id, other)
            if other not in parent_node:
                parent_node[other] = node_id
                queue.append(other)

    # Reachable downstream node set per edge: walk subtree from edge's downstream node.
    reachable_per_node: dict[str, set[str]] = {}

    def _reach(start: str, blocked_edge_id: str) -> set[str]:
        seen = {start}
        stack = [start]
        while stack:
            n = stack.pop()
            for e in network.neighbors(n):
                if e.id == blocked_edge_id:
                    continue
                other = e.node_b_id if e.node_a_id == n else e.node_a_id
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        return seen

    cfm_map: dict[SegmentId, CfmRange] = {}
    for edge_id, (_upstream, downstream) in edge_orientation.items():
        edge = network.edges[edge_id]
        reach = reachable_per_node.get((downstream, edge_id))
        if reach is None:
            reach = _reach(downstream, edge_id)
            reachable_per_node[(downstream, edge_id)] = reach
        # Flow at the downstream end of the segment — includes any terminal
        # sitting at the downstream node (flow still passes through the
        # segment to reach the terminal at its boundary). Flow at start adds
        # any terminals stripped along the run (A10).
        end_cfm = _terminal_total(network, reach)
        on_edge_cfm = _on_edge_terminal_cfm(network, edge)
        start_cfm = end_cfm + on_edge_cfm
        # Convention: start = upstream node, end = downstream node. Pressure
        # walk in pressure.py relies on this orientation.
        cfm_map[edge_id] = CfmRange(start=start_cfm, end=end_cfm)

    # Edges not reached from source carry no flow.
    for edge_id in network.edges:
        cfm_map.setdefault(edge_id, CfmRange(start=0.0, end=0.0))
    return cfm_map, edge_orientation
