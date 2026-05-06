"""Tests for V4 preprocessing — rasterization and grey-fill removal."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image

from app.cv.preprocess_v4 import rasterize_pdf, remove_grey_fill

_TESTSET2 = Path(__file__).resolve().parents[2] / "drawings" / "testset2.pdf"


def test_rasterize_pdf_produces_rgb_image_at_dpi() -> None:
    image = rasterize_pdf(_TESTSET2, dpi=100)

    assert image.mode == "RGB"
    # 100 DPI on any reasonable architectural sheet still produces a multi-
    # thousand-pixel raster on at least one axis.
    assert max(image.size) > 1000


def test_rasterize_pdf_rejects_multipage(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.new_page(width=612, height=792)
    pdf_path = tmp_path / "two_page.pdf"
    doc.save(pdf_path)
    doc.close()

    with pytest.raises(ValueError, match="single-page"):
        rasterize_pdf(pdf_path)


def test_rasterize_pdf_honours_page_rotation(tmp_path: Path) -> None:
    """A rotated source page must rasterize to its post-rotation dims (A15)."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)  # portrait
    page.set_rotation(90)
    pdf_path = tmp_path / "rotated.pdf"
    doc.save(pdf_path)
    doc.close()

    image = rasterize_pdf(pdf_path, dpi=72)

    # 90 deg rotation swaps the visible aspect; the rasterized image must
    # be wider than tall, matching post-rotation page geometry.
    assert image.size[0] > image.size[1]


def test_remove_grey_fill_binarises_to_black_and_white() -> None:
    arr = np.full((100, 100, 3), 255, dtype=np.uint8)  # white background
    arr[10:40, 10:40] = (200, 200, 200)  # light grey trace (above ink threshold)
    arr[50:60, 10:90] = (0, 0, 0)  # black linework
    arr[70:90, 70:90] = (50, 50, 50)  # dark grey (below ink threshold → ink)
    image = Image.fromarray(arr, mode="RGB")

    cleaned = np.asarray(remove_grey_fill(image))

    # White background stays white.
    assert (cleaned[5, 5] == (255, 255, 255)).all()
    # Light grey trace is whitewashed.
    assert (cleaned[20, 20] == (255, 255, 255)).all()
    # Black linework collapses to pure #000000.
    assert (cleaned[55, 50] == (0, 0, 0)).all()
    # Dark grey is treated as ink (luma < threshold) → pure #000000.
    assert (cleaned[80, 80] == (0, 0, 0)).all()
