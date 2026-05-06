"""Internal graph dataclasses for the V4 duct network.

The network is the bridge between geometric detection (CV) and physics
calculation (flow_trace, pressure). Edges carry segment identity, nodes carry
connector/terminal/open-end identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

NodeKind = Literal["connector", "terminal", "open_end", "source"]


@dataclass(frozen=True)
class NetworkNode:
    id: str
    kind: NodeKind
    position: tuple[float, float]
    cfm: float | None = None


@dataclass(frozen=True)
class NetworkEdge:
    id: str
    polygon_id: str
    node_a_id: str
    node_b_id: str
    centerline: list[tuple[float, float]]
    length_ft: float
    dimension_value: str | None
    dimension_shape: Literal["round", "rectangular"] | None
    terminal_ids_on_edge: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TerminalRefInternal:
    terminal_id: str
    distance_along_edge_ft: float
