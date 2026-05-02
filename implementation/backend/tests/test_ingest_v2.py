"""ADR-0007 — ingest classifier and DrawingSource behaviour.

Six tests covering: source classification (vector_pdf, raster_pdf, raster_image),
DrawingSource.render at two DPIs for vector, render-ignores-DPI for raster, and
the PipelineContext shape (no `image` field).
"""

from __future__ import annotations

from app.pipeline.base import PipelineContext
from app.pipeline.ingest import IngestStage


def _ingest(file_bytes: bytes, name: str = "drawing.bin") -> PipelineContext:
    ctx = PipelineContext(drawing_id="test", original_filename=name)
    return IngestStage(file_bytes, name).run(ctx)


# ── Classifier tests ─────────────────────────────────────────────────────────


def test_source_classifier_vector_pdf(vector_pdf_bytes: bytes) -> None:
    ctx = _ingest(vector_pdf_bytes, "vector.pdf")
    assert ctx.source is not None
    assert ctx.source.kind == "vector_pdf"
    assert ctx.source.pdf_doc is not None
    assert ctx.source.page is not None
    assert ctx.source.page_size_pt is not None
    # US-letter at the values inserted in the fixture.
    width_pt, height_pt = ctx.source.page_size_pt
    assert (width_pt, height_pt) == (612.0, 792.0)
    ctx.source.close()


def test_source_classifier_raster_pdf(raster_pdf_bytes: bytes) -> None:
    ctx = _ingest(raster_pdf_bytes, "scan.pdf")
    assert ctx.source is not None
    assert ctx.source.kind == "raster_pdf"
    # Raster PDFs do not retain pymupdf state — they go through pdf2image.
    assert ctx.source.pdf_doc is None
    assert ctx.source.page is None
    assert ctx.source.page_size_pt is None
    ctx.source.close()


def test_source_classifier_raster_image(raster_image_bytes: bytes) -> None:
    ctx = _ingest(raster_image_bytes, "image.png")
    assert ctx.source is not None
    assert ctx.source.kind == "raster_image"
    assert ctx.source.pdf_doc is None
    assert ctx.source.page is None
    assert ctx.source.page_size_pt is None
    # Raster_probe is the image itself at native resolution.
    assert ctx.source.raster_probe.size == (320, 240)
    ctx.source.close()


# ── DrawingSource.render tests ───────────────────────────────────────────────


def test_drawing_source_render_vector(vector_pdf_bytes: bytes) -> None:
    """Two DPIs → two different-sized images for vector inputs."""
    ctx = _ingest(vector_pdf_bytes, "vector.pdf")
    assert ctx.source is not None
    rect_pt = (0.0, 0.0, 200.0, 200.0)

    low = ctx.source.render(rect_pt, dpi=72)
    high = ctx.source.render(rect_pt, dpi=300)

    assert low.size != high.size
    # Higher DPI → strictly larger raster on the same clip.
    assert high.size[0] > low.size[0]
    assert high.size[1] > low.size[1]
    ctx.source.close()


def test_drawing_source_render_raster_ignores_dpi(raster_image_bytes: bytes) -> None:
    """Raster inputs treat rect as pixels and ignore DPI — same crop both calls."""
    ctx = _ingest(raster_image_bytes, "image.png")
    assert ctx.source is not None
    rect = (0.0, 0.0, 100.0, 80.0)

    crop_low = ctx.source.render(rect, dpi=72)
    crop_high = ctx.source.render(rect, dpi=600)

    assert crop_low.size == (100, 80)
    assert crop_high.size == (100, 80)
    ctx.source.close()


# ── Context shape ────────────────────────────────────────────────────────────


def test_pipeline_context_no_image_field() -> None:
    """ctx.image must be gone — replaced by ctx.source."""
    ctx = PipelineContext(drawing_id="x", original_filename="y")
    assert not hasattr(ctx, "image")
    assert hasattr(ctx, "source")
    assert ctx.source is None
