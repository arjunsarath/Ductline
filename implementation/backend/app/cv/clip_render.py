"""High-DPI per-rectangle clip rendering with rotated-polygon masking.

For OCR retries we need a clean, sharp crop of just one duct rectangle —
rendering the full ARCH-D page at 1200 DPI is ~3 GB of RAM, so we instead
clip the PDF page to the rectangle's bbox in PDF-point coordinates and
render only that sliver. Then we mask everything outside the rotated
polygon to pure white so the VLM only sees ink that belongs to this duct.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

# Padding around each clip in PDF points (1 pt = 1/72 inch). Just enough so
# the rotated polygon doesn't graze the clip edge.
_CLIP_PAD_PT = 4.0


def render_rectangle_clip(
    pdf_path: str | Path,
    bbox_in_rect_dpi: tuple[int, int, int, int],
    rect_dpi: int,
    target_dpi: int,
) -> tuple[Image.Image, tuple[float, float]]:
    """Re-rasterize the rectangle's bbox region from the PDF at ``target_dpi``.

    Returns ``(image, clip_origin_pt)`` where ``clip_origin_pt`` is the top-left
    of the clip in PDF-point coordinates — caller needs it to translate the
    rectangle's polygon corners into the clip's local pixel space.
    """
    bx, by, bw, bh = bbox_in_rect_dpi
    pt_per_px = 72.0 / rect_dpi
    x0_pt = bx * pt_per_px - _CLIP_PAD_PT
    y0_pt = by * pt_per_px - _CLIP_PAD_PT
    x1_pt = (bx + bw) * pt_per_px + _CLIP_PAD_PT
    y1_pt = (by + bh) * pt_per_px + _CLIP_PAD_PT

    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc.load_page(0)
        clip = pymupdf.Rect(x0_pt, y0_pt, x1_pt, y1_pt)
        pix = page.get_pixmap(clip=clip, dpi=target_dpi)
        mode = "RGBA" if pix.alpha else "RGB"
        image = Image.frombytes(
            mode, (pix.width, pix.height), pix.samples,
        ).convert("RGB")
    finally:
        doc.close()
    return image, (x0_pt, y0_pt)


def mask_outside_polygon(
    image: Image.Image,
    polygon_pixels: list[tuple[int, int]],
) -> Image.Image:
    """Set every pixel outside ``polygon_pixels`` to white.

    Used after re-rasterizing so the VLM only reads ink that's strictly
    inside the rotated rectangle — neighbouring ducts / labels in the
    axis-aligned bbox crop are zapped to paper before the model sees them.
    """
    if len(polygon_pixels) < 3:
        return image
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
    mask = np.zeros(arr.shape[:2], dtype=np.uint8)
    pts = np.array(polygon_pixels, dtype=np.int32)
    cv2.fillPoly(mask, [pts], color=255)
    arr[mask == 0] = 255
    return Image.fromarray(arr, mode="RGB")


def project_polygon_to_clip(
    polygon_in_rect_dpi: list[tuple[int, int]],
    rect_dpi: int,
    target_dpi: int,
    clip_origin_pt: tuple[float, float],
) -> list[tuple[int, int]]:
    """Convert polygon corners from rect-DPI space to the high-DPI clip space."""
    ox_pt, oy_pt = clip_origin_pt
    pt_per_px_in = 72.0 / rect_dpi
    px_per_pt_out = target_dpi / 72.0
    out: list[tuple[int, int]] = []
    for x, y in polygon_in_rect_dpi:
        x_pt = x * pt_per_px_in - ox_pt
        y_pt = y * pt_per_px_in - oy_pt
        out.append((
            int(round(x_pt * px_per_pt_out)),
            int(round(y_pt * px_per_pt_out)),
        ))
    return out
