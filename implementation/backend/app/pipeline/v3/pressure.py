"""SMACNA pressure-class derivation (V3 §5.10).

Two paths:

  • flow-driven (preferred): compute velocity = flow / cross_section_area
    and bucket per SMACNA's standard tiers.
  • size-only fallback: a heuristic based on duct perimeter. Always
    emitted with source="estimated:size_only" and confidence="low" so
    the UI can surface the disclaimer. Never claimed as real engineering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PressureValue = Literal["LOW", "MEDIUM", "HIGH"]


# SMACNA velocity bands for galvanized steel commercial duct (fpm = feet/min).
# These are the conventional 2,000 / 4,000 cutoffs the V3 doc commits to (§5.10).
_LOW_FPM_CEILING = 2000
_MEDIUM_FPM_CEILING = 4000


@dataclass
class PressureResult:
    value: PressureValue
    confidence: Literal["high", "medium", "low"]
    source: Literal["extracted", "estimated:size_only"]
    flow_value: float | None
    flow_unit: Literal["CFM", "L/s"] | None
    velocity_fpm: float | None
    material: str = "galvanized_steel"


def _flow_to_cfm(flow_value: float, flow_unit: Literal["CFM", "L/s"]) -> float:
    if flow_unit == "CFM":
        return flow_value
    # 1 L/s = 2.11888 CFM
    return flow_value * 2.118880003


def _area_sqft(width_unit: float, height_unit: float, page_unit: Literal["in", "mm"]) -> float:
    """Cross-section area in square feet — flow_to_velocity needs imperial."""
    if page_unit == "in":
        return (width_unit * height_unit) / 144.0
    # metric → mm² → m² → ft² (1 m² = 10.7639 sqft)
    return (width_unit * height_unit) / 1_000_000.0 * 10.7639


def from_flow(
    width_unit: float,
    height_unit: float,
    flow_value: float,
    flow_unit: Literal["CFM", "L/s"],
    page_unit: Literal["in", "mm"],
    material: str = "galvanized_steel",
) -> PressureResult:
    cfm = _flow_to_cfm(flow_value, flow_unit)
    area = _area_sqft(width_unit, height_unit, page_unit)
    velocity = cfm / area if area > 0 else 0.0
    if velocity < _LOW_FPM_CEILING:
        value: PressureValue = "LOW"
    elif velocity < _MEDIUM_FPM_CEILING:
        value = "MEDIUM"
    else:
        value = "HIGH"
    return PressureResult(
        value=value,
        confidence="high",
        source="extracted",
        flow_value=flow_value,
        flow_unit=flow_unit,
        velocity_fpm=velocity,
        material=material,
    )


def from_size_only(
    width_unit: float,
    height_unit: float,
    page_unit: Literal["in", "mm"],
    material: str = "galvanized_steel",
) -> PressureResult:
    """Heuristic from perimeter — engineering-dishonest if reported as fact.

    Always returns ``confidence="low"`` and ``source="estimated:size_only"``;
    the UI surfaces "Pressure class estimated from size — no CFM/L/s
    extracted. User override available." See V3 §5.10 + §8.
    """
    if page_unit == "in":
        perimeter_in = 2 * (width_unit + height_unit)
    else:
        perimeter_in = 2 * (width_unit + height_unit) / 25.4
    if perimeter_in < 60:
        value: PressureValue = "LOW"
    elif perimeter_in < 120:
        value = "MEDIUM"
    else:
        value = "HIGH"
    return PressureResult(
        value=value,
        confidence="low",
        source="estimated:size_only",
        flow_value=None,
        flow_unit=None,
        velocity_fpm=None,
        material=material,
    )
