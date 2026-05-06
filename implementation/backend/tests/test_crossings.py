"""Tests for `app.cv.crossings`."""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.cv.crossings import resolve_crossings
from app.cv.duct_outline import detect_duct_polygons
from app.cv.types import DuctPolygon
from tests.fixtures.v4 import crops


def _solid_horizontal_duct(canvas: np.ndarray, y: int) -> None:
    canvas[y : y + 3, 50:750] = 0
    canvas[y + 60 : y + 63, 50:750] = 0
    canvas[y : y + 63, 50:53] = 0
    canvas[y : y + 63, 747:750] = 0


def _dashed_vertical_duct(canvas: np.ndarray, x: int) -> None:
    # Short dashes: stride 16, dash 6 — ratio ~0.4 reads as dashed, and the
    # column gaps remain wide enough that the duct_outline close kernel does
    # not merge the dashed edges into the solid run.
    for top in range(40, 760, 16):
        canvas[top : top + 6, x : x + 3] = 0
        canvas[top : top + 6, x + 80 : x + 83] = 0


def _draw_crossing() -> Image.Image:
    canvas = np.full((800, 800), 255, dtype=np.uint8)
    _solid_horizontal_duct(canvas, y=400)
    _dashed_vertical_duct(canvas, x=350)
    return Image.fromarray(canvas, mode="L").convert("RGB")


def test_synthetic_crossing_detected() -> None:
    image = _draw_crossing()
    # Hand-build the two polygons rather than rely on `detect_duct_polygons`:
    # a dashed run and a solid run that overlap render as a single merged
    # contour in some pre-processings, which is a separate concern from the
    # crossings detector under test.
    solid = DuctPolygon(
        id="solid",
        points=[(50.0, 400.0), (750.0, 400.0), (750.0, 462.0), (50.0, 462.0)],
        shape_hint="rectangular",
        bbox=(50.0, 400.0, 700.0, 62.0),
    )
    dashed = DuctPolygon(
        id="dashed",
        points=[(350.0, 40.0), (432.0, 40.0), (432.0, 760.0), (350.0, 760.0)],
        shape_hint="rectangular",
        bbox=(350.0, 40.0, 82.0, 720.0),
    )
    crossings = resolve_crossings(image, [solid, dashed])
    assert crossings, "expected at least one crossing"
    crossing = crossings[0]
    assert crossing.over_segment_id == "solid"
    assert crossing.under_segment_id == "dashed"


def test_no_overlap_no_crossings() -> None:
    canvas = np.full((400, 800), 255, dtype=np.uint8)
    _solid_horizontal_duct(canvas, y=50)
    _solid_horizontal_duct(canvas, y=300)
    image = Image.fromarray(canvas, mode="L").convert("RGB")
    polygons = detect_duct_polygons(image)
    assert resolve_crossings(image, polygons) == []


def test_polygons_without_bbox_skipped() -> None:
    poly = DuctPolygon(id="a", points=[(0.0, 0.0), (1.0, 0.0)])
    image = Image.new("RGB", (10, 10), color="white")
    assert resolve_crossings(image, [poly, poly]) == []


def test_real_crossing_crop_runs() -> None:
    image = crops.get_crop("dashed_crossing")
    polygons = detect_duct_polygons(image)
    crossings = resolve_crossings(image, polygons)
    # The crop has a single visible crossing; the call must not crash.
    assert isinstance(crossings, list)
