"""Geometry math layer tests (SOLUTION-DESIGN-V4 §5)."""

from __future__ import annotations

import math

import pytest

from app.cv.types import Boundary, CenterlinePolyline, DuctPolygon
from app.detect.geometry import (
    cross_check_scale,
    diameter_from_pixel_width,
    length_ft,
    segment_centerline,
)
from app.schemas import ScaleInfo


def _scale_quarter_inch_per_foot() -> ScaleInfo:
    # 1/4" = 1'-0" → paper_inches_per_foot = 0.25.
    return ScaleInfo(paper_inches_per_foot=0.25, source="manual", confidence=1.0)


def test_length_ft_straight_polyline_at_300_dpi() -> None:
    # 1 foot at 1/4" scale @ 300 dpi = 0.25 in × 300 px/in = 75 px.
    poly = CenterlinePolyline(polygon_id="s1", points=[(0.0, 0.0), (75.0, 0.0)], pixel_length=75.0)
    assert math.isclose(length_ft(poly, _scale_quarter_inch_per_foot()), 1.0, rel_tol=1e-6)

    poly10 = CenterlinePolyline(polygon_id="s1", points=[(0.0, 0.0), (750.0, 0.0)], pixel_length=750.0)
    assert math.isclose(length_ft(poly10, _scale_quarter_inch_per_foot()), 10.0, rel_tol=1e-6)


def test_length_ft_dpi_override() -> None:
    poly = CenterlinePolyline(polygon_id="s1", points=[(0.0, 0.0), (37.5, 0.0)], pixel_length=37.5)
    assert math.isclose(length_ft(poly, _scale_quarter_inch_per_foot(), dpi=150), 1.0, rel_tol=1e-6)


def test_length_ft_recomputes_when_pixel_length_zero() -> None:
    poly = CenterlinePolyline(polygon_id="s1", points=[(0.0, 0.0), (75.0, 0.0)], pixel_length=0.0)
    assert math.isclose(length_ft(poly, _scale_quarter_inch_per_foot()), 1.0, rel_tol=1e-6)


def test_segment_centerline_uses_two_boundary_midpoints() -> None:
    poly = DuctPolygon(id="s1", points=[(0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0)])
    boundaries = [
        Boundary(polygon_id="s1", point=(0.0, 5.0), normal=(1.0, 0.0), kind="open_end"),
        Boundary(polygon_id="s1", point=(100.0, 5.0), normal=(1.0, 0.0), kind="open_end"),
    ]
    cl = segment_centerline(poly, boundaries)
    assert cl.points == [(0.0, 5.0), (100.0, 5.0)]
    assert math.isclose(cl.pixel_length, 100.0)


def test_diameter_from_pixel_width_round_up() -> None:
    # A 12" round duct at 1/4"/ft @300 dpi: diameter px = 12 in × 1/12 ft × 0.25 paper-in/ft × 300 px/paper-in
    # = 12/12 * 0.25 * 300 = 75 px width.
    poly = DuctPolygon(
        id="s1", points=[(0.0, 0.0), (300.0, 0.0), (300.0, 75.0), (0.0, 75.0)]
    )
    d = diameter_from_pixel_width(poly, _scale_quarter_inch_per_foot())
    assert d == pytest.approx(12.0, abs=1.0)


def test_diameter_round_up_to_next_inch() -> None:
    # 74 px → derives 11.84" → ceil to 12".
    poly = DuctPolygon(
        id="s1", points=[(0.0, 0.0), (300.0, 0.0), (300.0, 74.0), (0.0, 74.0)]
    )
    assert diameter_from_pixel_width(poly, _scale_quarter_inch_per_foot()) == 12.0


def test_cross_check_scale_within_tolerance() -> None:
    # 75 px width matches a 12" labeled diameter exactly at 1/4"/ft @300 dpi.
    pct = cross_check_scale(75.0, 12.0, _scale_quarter_inch_per_foot())
    assert pct < 3.0


def test_cross_check_scale_flags_mismatch() -> None:
    # 100 px width vs 12" labeled — derives 16", 33% off.
    pct = cross_check_scale(100.0, 12.0, _scale_quarter_inch_per_foot())
    assert pct > 3.0


def test_cross_check_scale_rejects_zero_diameter() -> None:
    with pytest.raises(ValueError):
        cross_check_scale(75.0, 0.0, _scale_quarter_inch_per_foot())
