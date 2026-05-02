"""Probe OCR + smart per-tile DPI (SOLUTION-DESIGN-V2 §5.2).

Seven tests covering: PDF text-layer fast path, OCR fallback for raster,
short-text PDF falling through to OCR, smart_dpi for vector + raster,
the pure target_dpi_for_height helper, and degradation on OCR failure.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.config import settings
from app.ocr.base import Bbox, OCRMatch, Table
from app.ocr.cache import OCRCache, target_dpi_for_height
from app.pipeline.base import PipelineContext
from app.pipeline.ingest import IngestStage
from app.pipeline.probe_ocr import ProbeOCRStage
from app.source.base import DrawingSource

# ── Stubs ────────────────────────────────────────────────────────────────────


class _StubOCR:
    """OCRExtractor stub returning preset matches and never touching the engine."""

    def __init__(self, matches: list[OCRMatch]) -> None:
        self._matches = matches

    def extract_text(
        self, image: Image.Image, region: Bbox | None = None
    ) -> list[OCRMatch]:
        del image, region
        return list(self._matches)

    def extract_table(self, image: Image.Image, region: Bbox) -> Table:
        del image, region
        return Table(rows=[])


class _RaisingOCR:
    """OCR stub that raises on extract_text — exercises the degradation path."""

    def extract_text(
        self, image: Image.Image, region: Bbox | None = None
    ) -> list[OCRMatch]:
        del image, region
        raise RuntimeError("ocr engine offline")

    def extract_table(self, image: Image.Image, region: Bbox) -> Table:
        del image, region
        return Table(rows=[])


def _ingest(file_bytes: bytes, name: str = "drawing.bin") -> PipelineContext:
    ctx = PipelineContext(drawing_id="test", original_filename=name)
    return IngestStage(file_bytes, name).run(ctx)


def _match(height: int, *, text: str = "x", confidence: float = 0.9) -> OCRMatch:
    return OCRMatch(text=text, bbox=(0, 0, 10, height), confidence=confidence)


# ── ProbeOCRStage tests ──────────────────────────────────────────────────────


def test_probe_ocr_text_layer_path(vector_pdf_long_text_bytes: bytes) -> None:
    """Vector PDF with > 100 text-layer chars uses font sizes directly."""
    ctx = _ingest(vector_pdf_long_text_bytes, "vector.pdf")
    # Stub never gets called on the text-layer path; an empty stub proves it.
    ctx = ProbeOCRStage(_StubOCR(matches=[])).run(ctx)

    assert ctx.ocr_cache is not None
    assert ctx.ocr_cache.source == "pdf_text_layer"
    assert ctx.ocr_cache.matches == []
    assert ctx.ocr_cache.probe_dpi_used == settings.probe_dpi

    # Smallest font is 8 pt → 8 * probe_dpi / 72 px.
    expected_px = 8.0 * settings.probe_dpi / 72.0
    assert ctx.ocr_cache.smallest_text_height_px_p5 == pytest.approx(expected_px, rel=0.01)
    assert ctx.errors == []
    assert ctx.source is not None
    ctx.source.close()


def test_probe_ocr_fallback_for_raster(raster_image_bytes: bytes) -> None:
    """Raster source has no text layer → OCR pass populates matches + p5."""
    ctx = _ingest(raster_image_bytes, "image.png")
    matches = [_match(15), _match(10), _match(5)]
    ctx = ProbeOCRStage(_StubOCR(matches=matches)).run(ctx)

    assert ctx.ocr_cache is not None
    assert ctx.ocr_cache.source == "ocr_probe"
    assert ctx.ocr_cache.matches == matches
    # _percentile_sorted on [5, 10, 15] at 0.05 → idx int(0.05*2)=0 → 5.
    assert ctx.ocr_cache.smallest_text_height_px_p5 == pytest.approx(5.0)
    assert ctx.errors == []
    assert ctx.source is not None
    ctx.source.close()


def test_probe_ocr_skipped_short_text_pdf(vector_pdf_bytes: bytes) -> None:
    """Vector PDF with <= 100 text-layer chars falls through to OCR path."""
    ctx = _ingest(vector_pdf_bytes, "short.pdf")
    matches = [_match(20), _match(40)]
    ctx = ProbeOCRStage(_StubOCR(matches=matches)).run(ctx)

    assert ctx.ocr_cache is not None
    assert ctx.ocr_cache.source == "ocr_probe"
    assert ctx.ocr_cache.matches == matches
    assert ctx.errors == []
    assert ctx.source is not None
    ctx.source.close()


def test_probe_ocr_failure_is_degradation(raster_image_bytes: bytes) -> None:
    """An OCR exception leaves ocr_cache=None and a single probe_ocr error."""
    ctx = _ingest(raster_image_bytes, "image.png")
    ctx = ProbeOCRStage(_RaisingOCR()).run(ctx)

    assert ctx.ocr_cache is None
    assert len(ctx.errors) == 1
    assert ctx.errors[0].startswith("probe_ocr:")
    assert "ocr engine offline" in ctx.errors[0]
    assert ctx.source is not None
    ctx.source.close()


# ── DrawingSource.smart_dpi_for_rect tests ───────────────────────────────────


def _vector_source_with_pages(page_size_pt: tuple[float, float] = (612.0, 792.0)) -> DrawingSource:
    """Build a minimal vector_pdf DrawingSource for smart-DPI math tests."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=page_size_pt[0], height=page_size_pt[1])
    pixmap = page.get_pixmap(dpi=72)
    probe = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return DrawingSource(
        kind="vector_pdf",
        pdf_doc=doc,
        page=page,
        page_size_pt=page_size_pt,
        raster_probe=probe,
    )


def test_smart_dpi_for_rect_vector() -> None:
    """Vector path scales DPI to put smallest text at target px; clamps at ceiling."""
    src = _vector_source_with_pages()
    try:
        # 10 px @ 150 DPI → need 150 * 22/10 = 330 DPI to reach 22 px.
        cache = OCRCache(
            matches=[],
            smallest_text_height_px_p5=10.0,
            source="pdf_text_layer",
            probe_dpi_used=150,
        )
        assert src.smart_dpi_for_rect((0, 0, 100, 100), ocr_cache=cache) == 330

        # 2 px @ 150 DPI → 150 * 22/2 = 1650 DPI → clamped to ceiling.
        cache_tiny = OCRCache(
            matches=[],
            smallest_text_height_px_p5=2.0,
            source="pdf_text_layer",
            probe_dpi_used=150,
        )
        assert src.smart_dpi_for_rect(
            (0, 0, 100, 100), ocr_cache=cache_tiny
        ) == settings.smart_dpi_ceiling
    finally:
        src.close()


def test_smart_dpi_for_rect_raster_returns_zero(raster_image_bytes: bytes) -> None:
    """Raster sources cannot re-render — smart-DPI is unavailable, returns 0."""
    ctx = _ingest(raster_image_bytes, "image.png")
    assert ctx.source is not None
    cache = OCRCache(
        matches=[],
        smallest_text_height_px_p5=5.0,
        source="ocr_probe",
        probe_dpi_used=150,
    )
    assert ctx.source.smart_dpi_for_rect((0, 0, 50, 50), ocr_cache=cache) == 0
    ctx.source.close()


# ── target_dpi_for_height pure helper ────────────────────────────────────────


@pytest.mark.parametrize(
    ("height_px", "current_dpi", "target_text_px", "expected"),
    [
        # Already at target → DPI unchanged (within rounding).
        (22.0, 150, 22, 150),
        # Half-target text → double DPI.
        (11.0, 150, 22, 300),
        # Tiny text → clamp at ceiling.
        (1.0, 150, 22, settings.smart_dpi_ceiling),
        # Huge text → would shrink DPI; clamp at probe floor.
        (1000.0, 150, 22, settings.probe_dpi),
        # Zero height (no measurement) → probe floor.
        (0.0, 150, 22, settings.probe_dpi),
        # Negative-ish → probe floor (defensive).
        (-5.0, 150, 22, settings.probe_dpi),
    ],
)
def test_target_dpi_for_height_pure_helper(
    height_px: float, current_dpi: int, target_text_px: int, expected: int
) -> None:
    assert target_dpi_for_height(height_px, current_dpi, target_text_px) == expected
