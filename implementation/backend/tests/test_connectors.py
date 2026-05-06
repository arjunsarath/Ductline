"""Tests for `app.cv.connectors`."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from app.cv.connectors import detect_connectors
from app.cv.duct_outline import detect_duct_polygons
from tests.fixtures.v4 import crops


def _duct_with_equipment_box() -> Image.Image:
    canvas = np.full((400, 900), 255, dtype=np.uint8)
    # Two duct stubs flanking an equipment box.
    canvas[150:155, 100:300] = 0
    canvas[245:250, 100:300] = 0
    canvas[150:250, 100:105] = 0
    canvas[150:250, 295:300] = 0

    canvas[150:155, 600:800] = 0
    canvas[245:250, 600:800] = 0
    canvas[150:250, 600:605] = 0
    canvas[150:250, 795:800] = 0

    # Equipment box (square with X) between them.
    cv2.rectangle(canvas, (380, 130), (520, 270), color=0, thickness=4)
    cv2.line(canvas, (380, 130), (520, 270), color=0, thickness=3)
    cv2.line(canvas, (380, 270), (520, 130), color=0, thickness=3)
    return Image.fromarray(canvas, mode="L").convert("RGB")


def test_equipment_box_detected() -> None:
    image = _duct_with_equipment_box()
    polygons = detect_duct_polygons(image)
    connectors = detect_connectors(image, polygons)
    assert connectors, "expected at least one connector"
    kinds = {c.kind for c in connectors}
    assert "equipment" in kinds


def test_empty_drawing_returns_empty() -> None:
    canvas = np.full((300, 300), 255, dtype=np.uint8)
    image = Image.fromarray(canvas, mode="L").convert("RGB")
    assert detect_connectors(image, []) == []


def test_real_connector_crop_runs() -> None:
    image = crops.get_crop("connector_transition")
    polygons = detect_duct_polygons(image)
    connectors = detect_connectors(image, polygons)
    assert isinstance(connectors, list)
    # The crop is dense; assert ≥ 1 connector found if any polygons exist.
    if polygons:
        assert connectors, "expected ≥1 connector in connector-transition crop"
