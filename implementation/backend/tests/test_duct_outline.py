"""Tests for `app.cv.duct_outline`."""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.cv.duct_outline import detect_duct_polygons
from tests.fixtures.v4 import crops


def _draw_rect_duct() -> Image.Image:
    canvas = np.full((400, 800), 255, dtype=np.uint8)
    # Outline only (V4 is outline-based, not fill-driven).
    canvas[100:103, 100:700] = 0  # top edge
    canvas[197:200, 100:700] = 0  # bottom edge
    canvas[100:200, 100:103] = 0  # left edge
    canvas[100:200, 697:700] = 0  # right edge
    return Image.fromarray(canvas, mode="L").convert("RGB")


def _draw_round_duct() -> Image.Image:
    import cv2

    canvas = np.full((400, 400), 255, dtype=np.uint8)
    cv2.circle(canvas, (200, 200), 80, color=0, thickness=3)
    return Image.fromarray(canvas, mode="L").convert("RGB")


def test_synthetic_rectangular_duct_detected() -> None:
    polygons = detect_duct_polygons(_draw_rect_duct())
    assert polygons, "expected at least one duct polygon"
    poly = polygons[0]
    assert poly.shape_hint == "rectangular"
    assert poly.bbox is not None
    assert poly.principal_axis is not None
    # Long axis should run horizontally — |dx| ≫ |dy|.
    dx, dy = poly.principal_axis
    assert abs(dx) > abs(dy)
    assert poly.est_width_px is not None and 80 < poly.est_width_px < 120


def test_synthetic_round_duct_detected() -> None:
    polygons = detect_duct_polygons(_draw_round_duct())
    assert polygons
    # The exterior contour of a thick circle reads as round-ish; we only
    # require that *some* polygon comes back with a reasonable bbox.
    poly = polygons[0]
    assert poly.bbox is not None
    x, y, w, h = poly.bbox
    assert 100 < w < 200 and 100 < h < 200


def test_text_sized_blob_rejected() -> None:
    """A 6 px tick mark is below the duct minimum and must not be reported."""
    canvas = np.full((300, 300), 255, dtype=np.uint8)
    canvas[148:152, 148:154] = 0
    polygons = detect_duct_polygons(Image.fromarray(canvas, mode="L").convert("RGB"))
    assert polygons == []


def test_real_rect_duct_crop() -> None:
    image = crops.get_crop("rect_duct")
    polygons = detect_duct_polygons(image)
    assert polygons, "expected at least one polygon in the rect-duct crop"
    elongated = [p for p in polygons if p.principal_axis is not None]
    assert any(
        abs(p.principal_axis[0]) > abs(p.principal_axis[1]) for p in elongated
    ), "rectangular duct should have a horizontal principal axis"
