"""Tests for `app.cv.terminals`."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from app.cv.terminals import detect_air_terminals, half_bboxes
from tests.fixtures.v4 import crops


def _draw_terminal(radius: int = 30) -> Image.Image:
    canvas = np.full((200, 200), 255, dtype=np.uint8)
    cv2.circle(canvas, (100, 100), radius, color=0, thickness=2)
    cv2.line(canvas, (100 - radius, 100), (100 + radius, 100), color=0, thickness=2)
    return Image.fromarray(canvas, mode="L").convert("RGB")


def _draw_circle_no_divider(radius: int = 30) -> Image.Image:
    canvas = np.full((200, 200), 255, dtype=np.uint8)
    cv2.circle(canvas, (100, 100), radius, color=0, thickness=2)
    return Image.fromarray(canvas, mode="L").convert("RGB")


def test_synthetic_terminal_detected() -> None:
    image = _draw_terminal()
    terminals = detect_air_terminals(image)
    assert len(terminals) == 1
    t = terminals[0]
    assert 90 <= t.center[0] <= 110
    assert 90 <= t.center[1] <= 110
    assert 25 <= t.radius <= 35


def test_circle_without_divider_rejected() -> None:
    image = _draw_circle_no_divider()
    assert detect_air_terminals(image) == []


def test_half_bboxes_split_at_centre() -> None:
    image = _draw_terminal(radius=20)
    terminals = detect_air_terminals(image)
    assert terminals
    top, bottom = half_bboxes(terminals[0])
    assert top[1] + top[3] <= bottom[1] + 0.5  # top ends at centre y
    assert top[2] == bottom[2]  # widths equal


def test_real_terminal_crop() -> None:
    image = crops.get_crop("air_terminal")
    terminals = detect_air_terminals(image)
    # The crop centres on a single C/150 terminal.
    assert terminals, "expected ≥1 terminal in air-terminal crop"
