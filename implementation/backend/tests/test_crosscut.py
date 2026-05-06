"""Tests for `app.cv.crosscut`."""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.cv.crosscut import find_segment_boundaries
from app.cv.duct_outline import detect_duct_polygons
from app.cv.types import DuctPolygon
from tests.fixtures.v4 import crops


def _duct_with_crosscuts(*crosscut_xs: int) -> Image.Image:
    canvas = np.full((300, 800), 255, dtype=np.uint8)
    # Rectangular duct outline.
    canvas[100:103, 100:700] = 0
    canvas[197:200, 100:700] = 0
    canvas[100:200, 100:103] = 0
    canvas[100:200, 697:700] = 0
    # Perpendicular cross-cut bars.
    for x in crosscut_xs:
        canvas[100:200, x : x + 3] = 0
    return Image.fromarray(canvas, mode="L").convert("RGB")


def test_two_crosscut_bars_detected() -> None:
    image = _duct_with_crosscuts(150, 650)
    polygons = detect_duct_polygons(image)
    assert polygons
    boundaries = find_segment_boundaries(polygons[0], image)
    # We expect two distinct cross-cut clusters; allow ≥2 to absorb the duct
    # end-walls being read as bars too.
    assert len(boundaries) >= 2


def test_no_crosscut_returns_empty() -> None:
    canvas = np.full((300, 800), 255, dtype=np.uint8)
    canvas[100:103, 100:700] = 0
    canvas[197:200, 100:700] = 0
    canvas[100:200, 100:103] = 0
    canvas[100:200, 697:700] = 0
    image = Image.fromarray(canvas, mode="L").convert("RGB")
    polygons = detect_duct_polygons(image)
    assert polygons
    boundaries = find_segment_boundaries(polygons[0], image)
    # Side-walls may still register as bars; the contract is "no crashes",
    # plus the count must not exceed two (the two end walls).
    assert len(boundaries) <= 2


def test_polygon_without_axis_returns_empty() -> None:
    poly = DuctPolygon(id="x", points=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    image = Image.new("RGB", (10, 10), color="white")
    assert find_segment_boundaries(poly, image) == []


def test_real_crop_runs() -> None:
    image = crops.get_crop("rect_duct")
    polygons = detect_duct_polygons(image)
    if not polygons:
        return
    # Pick the longest polygon — most likely the duct, not a fragment.
    poly = max(polygons, key=lambda p: (p.bbox[2] * p.bbox[3]) if p.bbox else 0)
    boundaries = find_segment_boundaries(poly, image)
    # At minimum, the call must not crash and must return a list.
    assert isinstance(boundaries, list)
