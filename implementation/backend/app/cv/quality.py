"""Image-quality CV utilities for stage 2 (SOLUTION-DESIGN §4 row 2).

Three independent measurements: blur (Laplacian variance), skew
(projection-profile sweep), and a sample-region OCR confidence number that
stage 2 sources from the OCRExtractor seam, not from here.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL.Image import Image


def laplacian_variance(image: Image) -> float:
    """Higher = sharper. Threshold tuning lives in the caller."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def estimate_skew_degrees(image: Image, search_range: int = 5) -> float:
    """Angle (degrees) that maximizes horizontal-projection variance.

    Engineering drawings are dominated by axis-aligned line work; when they
    are skewed, projecting along rows produces lower variance than at the
    correcting angle. Sweep ±search_range and pick the best.

    Image is downsampled to 1000 px on the long edge before the sweep —
    a full-res sweep on an 8000×8000 raster is wasteful for an angle estimate.
    """
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    gray = _downsample_for_sweep(gray, max_long_edge=1000)

    # Threshold to binary; text + line-work become foreground.
    _, binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )

    best_angle = 0.0
    best_variance = -1.0
    for angle in np.arange(-search_range, search_range + 0.5, 0.5):
        rotated = _rotate(binary, float(angle))
        projection = rotated.sum(axis=1, dtype=np.float64)
        variance = float(projection.var())
        if variance > best_variance:
            best_variance = variance
            best_angle = float(angle)

    return best_angle


# ── Helpers ──────────────────────────────────────────────────────────────────


def _downsample_for_sweep(gray: np.ndarray, max_long_edge: int) -> np.ndarray:
    h, w = gray.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_long_edge:
        return gray
    scale = max_long_edge / long_edge
    return cv2.resize(gray, (int(w * scale), int(h * scale)))


def _rotate(image: np.ndarray, angle: float) -> np.ndarray:
    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, scale=1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
