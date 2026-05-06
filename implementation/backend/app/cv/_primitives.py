"""Shared low-level CV helpers for the V4 outline-based detectors.

Kept private (`_primitives`) — these are implementation glue, not public API.
Placed here rather than duplicated across `duct_outline`, `crosscut`,
`connectors`, `terminals`, `crossings` so a single binarisation tweak does
not have to land in five files.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

# Threshold below which a greyscale pixel is treated as ink. Engineering
# drawings come back from the V4 preprocess stage with grey architectural
# fill stripped (A12), so anything still dark is linework.
INK_LEVEL = 200


def to_gray(image: Image.Image) -> np.ndarray:
    """Return an HxW uint8 greyscale array regardless of the input mode."""
    if image.mode != "L":
        image = image.convert("L")
    return np.asarray(image, dtype=np.uint8)


def binary_ink(image: Image.Image, *, level: int = INK_LEVEL) -> np.ndarray:
    """Foreground (ink) mask: 255 where the drawing has ink, 0 elsewhere."""
    gray = to_gray(image)
    _, mask = cv2.threshold(gray, level, 255, cv2.THRESH_BINARY_INV)
    return mask


def principal_axis_of(points: np.ndarray) -> tuple[tuple[float, float], float]:
    """Return (unit-vector along long axis, est_width_px) for a contour.

    Uses cv2.minAreaRect — robust on rotated rectangles and rounded-rect
    duct outlines. Width is the *short* side of the min-area rectangle.
    """
    rect = cv2.minAreaRect(points.astype(np.float32))
    (_cx, _cy), (w, h), angle_deg = rect
    long_side = float(max(w, h))
    short_side = float(min(w, h))
    # cv2 reports the angle of the side referenced as `width`. Reorient so the
    # axis vector always points along the long side, regardless of which side
    # cv2 picked as `width` for this particular rectangle.
    axis_deg = angle_deg if w >= h else angle_deg + 90.0
    rad = np.deg2rad(axis_deg)
    axis = (float(np.cos(rad)), float(np.sin(rad)))
    # `long_side` is unused by the caller today but kept so the helper is the
    # single authority on min-area-rect interpretation.
    _ = long_side
    return axis, short_side
