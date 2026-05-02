"""Shared fixtures for the V2 ADR-0007 ingest tests.

Fixtures are generated in-memory rather than committed binaries — keeps the
fixture intent visible alongside the assertions.
"""

from __future__ import annotations

from io import BytesIO

import pymupdf
import pytest
from PIL import Image


@pytest.fixture
def vector_pdf_bytes() -> bytes:
    """A single-page PDF with a real text layer (>= 50 chars)."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)  # US-letter, in points
    page.insert_text(
        (72, 144),
        "DUCT SCHEDULE — SA-1: 14\" round, LOW pressure. SA-2: 10\" x 8\" rectangular.",
        fontsize=11,
    )
    buf = doc.tobytes()
    doc.close()
    return buf


@pytest.fixture
def vector_pdf_long_text_bytes() -> bytes:
    """A vector PDF with > 100 chars of text at known font sizes (8 pt, 11 pt).

    Used to exercise the Probe OCR text-layer fast path: ``len(get_text())``
    must exceed 100, and the smallest font size determines the expected
    smallest_text_height_px_p5 measurement.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    # Two lines at 11 pt — body text, > 100 chars together.
    page.insert_text(
        (72, 144),
        "DUCT SCHEDULE — SA-1: 14\" round, LOW pressure. SA-2: 10\" x 8\" rectangular.",
        fontsize=11,
    )
    page.insert_text(
        (72, 200),
        "PLAN VIEW — first floor mechanical layout, supply and return air ductwork.",
        fontsize=11,
    )
    # One line at 8 pt — the smallest text on the page; sets the p5 floor.
    page.insert_text(
        (72, 260),
        "Notes: refer to specification 23 31 13 for SMACNA sealing class.",
        fontsize=8,
    )
    buf = doc.tobytes()
    doc.close()
    return buf


@pytest.fixture
def raster_pdf_bytes() -> bytes:
    """A single-page PDF that contains an embedded image and no text layer."""
    img = Image.new("RGB", (400, 300), color="white")
    img_buf = BytesIO()
    img.save(img_buf, format="PNG")

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(72, 72, 472, 372), stream=img_buf.getvalue())
    buf = doc.tobytes()
    doc.close()
    return buf


@pytest.fixture
def raster_image_bytes() -> bytes:
    """A plain PNG, treated as raster_image."""
    img = Image.new("RGB", (320, 240), color=(200, 200, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
