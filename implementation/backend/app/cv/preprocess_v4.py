"""Preprocessing for the V4 outline-based pipeline.

V3 leaned on color fills to find ducts; V4 starts by *removing* the grey
architectural fill (assumption A12) so the remaining black linework is
unambiguous, then rasterizes the source PDF at a fixed DPI.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

# Binary luma threshold for ink-vs-paper. Below the threshold → pure black,
# above → pure white. This collapses every grey (architectural shading,
# anti-aliasing fuzz, faint hatching) to one of two values. Tune lower if
# thin linework is being lost; tune higher if faint traces still leak through.
_INK_LUMA_THRESHOLD = 90  # 0..255


def mask_outside_area(
    image: Image.Image, area: tuple[int, int, int, int],
) -> Image.Image:
    """Whitewash every pixel outside the (x, y, w, h) rectangle.

    Used to restrict downstream contour detection to the operator-chosen
    drawing region, killing title-block text, plan notes, schedules, etc.
    Coordinates are clamped to the image bounds; an out-of-image area returns
    an all-white image of the same size.
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    x0, y0, aw, ah = area
    x1 = max(0, min(w, x0 + aw))
    y1 = max(0, min(h, y0 + ah))
    x0c = max(0, min(w, x0))
    y0c = max(0, min(h, y0))
    out = np.full_like(rgb, 255)
    if x1 > x0c and y1 > y0c:
        out[y0c:y1, x0c:x1] = rgb[y0c:y1, x0c:x1]
    return Image.fromarray(out, mode="RGB")


def remove_grey_fill(image: Image.Image, threshold: int | None = None) -> Image.Image:
    """Binarise the page: ink → #000000, everything else → #FFFFFF (A12).

    Drawings are line-art on white, so the cleanest filter is a luma threshold:
    every pixel darker than the cutoff becomes pure black, every pixel above
    becomes pure white. This removes architectural shading, faint hatching,
    and JPEG-style grey halos in one pass while sharpening the linework.
    """
    cutoff = _INK_LUMA_THRESHOLD if threshold is None else int(threshold)
    luma = np.asarray(image.convert("L"))
    ink = luma < cutoff
    out = np.full((*luma.shape, 3), 255, dtype=np.uint8)
    out[ink] = (0, 0, 0)
    return Image.fromarray(out, mode="RGB")


def read_page_rotation(pdf_path: str | Path) -> int:
    """Return the page-level /Rotate value (0/90/180/270) from a single-page PDF."""
    doc = pymupdf.open(str(pdf_path))
    try:
        if doc.page_count != 1:
            raise ValueError(
                f"single-page PDF required (got {doc.page_count} pages)"
            )
        return int(doc.load_page(0).rotation) % 360
    finally:
        doc.close()


def rasterize_pdf(pdf_path: str | Path, dpi: int = 300) -> Image.Image:
    """Render a single-page PDF to RGB at the requested DPI (A15).

    Multi-page documents raise ``ValueError``; the user is expected to pick
    one page upstream. Page rotation metadata is honoured by pymupdf so the
    output orientation matches what the user sees in a PDF viewer.
    """
    doc = pymupdf.open(str(pdf_path))
    try:
        if doc.page_count != 1:
            raise ValueError(
                f"single-page PDF required (got {doc.page_count} pages); "
                "select a page before calling rasterize_pdf"
            )
        page = doc.load_page(0)
        pixmap = page.get_pixmap(dpi=dpi)
        mode = "RGBA" if pixmap.alpha else "RGB"
        image = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)
        return image.convert("RGB")
    finally:
        doc.close()
