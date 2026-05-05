"""V3 result overlay rendering (SOLUTION-DESIGN-V3 §5.11).

Produces the "this is what we detected" annotation as a *transparent*
RGBA PNG. The overlay is intended to be stacked over the rendered page
in the browser so the user can grayscale the page underneath without
desaturating the detection output.

Layers, bottom-up:
  1. Filled-mask tint: each system's filled mask is painted in its
     display color at α=0.4. Pixels outside any mask remain transparent.
  2. Contour borders at full alpha.

Per-segment markers and dim/pressure-class labels are deliberately *not*
baked into the PNG — they would pixelate at high zoom. The frontend
draws them as SVG ``<circle>`` + ``<text>`` on top of this PNG so they
stay crisp at any scale.

Coordinates baked into the overlay (mask pixels) are in the rendered-
page space, so the frontend can stack the PNG directly without coord
transforms — same dimensions as the page render that ``/v3/detect``
returns alongside.
"""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from app.pipeline.v3.color_mask import SystemMask


def render_overlay(
    rendered_bgr: NDArray[np.uint8],
    system_masks: list[SystemMask],
    *,
    mask_alpha: float = 0.4,
    contour_thickness: int = 3,
) -> NDArray[np.uint8]:
    """Return an RGBA image: transparent background + mask tint + contours.

    ``rendered_bgr`` is used only for its dimensions (height/width); the
    output is a freshly-allocated RGBA canvas. Frontends layer this over
    the page render and draw segment markers/labels in SVG above it.
    """
    height, width = rendered_bgr.shape[:2]
    out = np.zeros((height, width, 4), dtype=np.uint8)

    # Stage 1 — fill tint at mask_alpha
    alpha_byte = int(round(mask_alpha * 255))
    for sm in system_masks:
        if not sm.filled.any():
            continue
        b, g, r = sm.pick.display_color_bgr
        sel = sm.filled > 0
        out[sel] = (b, g, r, alpha_byte)

    # Stage 2 — contour borders at full alpha
    for sm in system_masks:
        if not sm.filled.any():
            continue
        contours, _ = cv2.findContours(sm.filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        b, g, r = sm.pick.display_color_bgr
        cv2.drawContours(out, contours, -1, (b, g, r, 255), thickness=contour_thickness)

    return out
