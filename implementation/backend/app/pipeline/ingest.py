"""Stage 1 — Ingest (ADR-0007).

Three-way classifier:
  • vector_pdf   — PDF whose page 0 has >= 50 chars of text. pymupdf opens the
                   doc; raster_probe is rendered at probe_dpi for stages that
                   still need a full-sheet raster.
  • raster_pdf   — PDF with < 50 chars of text and at least one image (a scan
                   wrapped in PDF). Falls back to v1's pdf2image rasterizer.
  • raster_image — PNG / JPG. Opened with PIL at native resolution.

Multi-page PDFs are rejected. Size and dimension caps are preserved verbatim
from v1.
"""

from __future__ import annotations

from io import BytesIO

import pymupdf
from pdf2image import convert_from_bytes
from pdf2image.exceptions import PDFPageCountError
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.pipeline.base import (
    FileTooLargeError,
    MultiPagePdfError,
    PipelineContext,
    PipelineStage,
    UnsupportedFileError,
)
from app.source.base import DrawingSource

_PDF_MAGIC = b"%PDF"
# Threshold for vector vs raster PDF classification — drawings exported from
# CAD tools carry the schedule and callouts as text; scans wrapped in PDF
# carry only an embedded image and have effectively no text layer.
_VECTOR_TEXT_THRESHOLD_CHARS = 50


class IngestStage(PipelineStage):
    name = "ingest"

    def __init__(self, file_bytes: bytes, original_filename: str) -> None:
        self._bytes = file_bytes
        self._filename = original_filename

    def run(self, ctx: PipelineContext) -> PipelineContext:
        self._enforce_size_cap()
        source = self._classify_and_load()
        self._enforce_dimension_cap(source.raster_probe)

        ctx.source = source
        ctx.width_px, ctx.height_px = source.raster_probe.size
        return ctx

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _enforce_size_cap(self) -> None:
        if len(self._bytes) > settings.max_upload_bytes:
            raise FileTooLargeError(
                f"upload exceeds {settings.max_upload_bytes // (1024 * 1024)} MB cap"
            )

    def _classify_and_load(self) -> DrawingSource:
        # PDF detection by magic bytes — file extension is unreliable from a
        # multipart upload.
        if self._bytes[:4] == _PDF_MAGIC:
            return self._load_pdf()
        return self._load_raster_image()

    def _load_pdf(self) -> DrawingSource:
        try:
            doc = pymupdf.open(stream=self._bytes, filetype="pdf")
        except Exception as exc:  # noqa: BLE001 — pymupdf raises a variety of types
            raise UnsupportedFileError("could not read PDF") from exc

        if doc.page_count == 0:
            doc.close()
            raise UnsupportedFileError("PDF has no pages")
        if doc.page_count > 1:
            doc.close()
            raise MultiPagePdfError("multi-page PDF — upload one page at a time")

        page = doc.load_page(0)
        text_len = len(page.get_text())

        if text_len >= _VECTOR_TEXT_THRESHOLD_CHARS:
            return self._build_vector_source(doc, page)

        # Raster PDF (scan wrapped in PDF). Fall back to v1's pdf2image path
        # at raster_dpi; the pymupdf doc isn't retained because downstream
        # stages won't re-render from it.
        doc.close()
        return self._build_raster_pdf_source()

    def _build_vector_source(
        self, doc: pymupdf.Document, page: pymupdf.Page
    ) -> DrawingSource:
        # Auto-orientation lives in ProbeOCRStage — it has access to the
        # OCR engine needed to disambiguate 90° from 270° via rendered-OCR
        # voting. Ingest stays "no engines" by contract; ``rotation_applied``
        # defaults to 0 here and is mutated by ProbeOCRStage if needed.
        page_size_pt = (page.rect.width, page.rect.height)
        pixmap = page.get_pixmap(dpi=settings.probe_dpi)
        mode = "RGBA" if pixmap.alpha else "RGB"
        probe = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples).convert(
            "RGB"
        )
        return DrawingSource(
            kind="vector_pdf",
            pdf_doc=doc,
            page=page,
            page_size_pt=page_size_pt,
            raster_probe=probe,
        )

    def _build_raster_pdf_source(self) -> DrawingSource:
        try:
            pages = convert_from_bytes(
                self._bytes,
                dpi=settings.raster_dpi,
                last_page=2,
            )
        except PDFPageCountError as exc:
            raise UnsupportedFileError("could not read PDF") from exc

        if not pages:
            raise UnsupportedFileError("PDF has no pages")

        return DrawingSource(
            kind="raster_pdf",
            pdf_doc=None,
            page=None,
            page_size_pt=None,
            raster_probe=pages[0].convert("RGB"),
        )

    def _load_raster_image(self) -> DrawingSource:
        try:
            image = Image.open(BytesIO(self._bytes))
        except UnidentifiedImageError as exc:
            raise UnsupportedFileError(
                "unsupported file type — upload PDF, PNG, or JPG"
            ) from exc
        return DrawingSource(
            kind="raster_image",
            pdf_doc=None,
            page=None,
            page_size_pt=None,
            raster_probe=image.convert("RGB"),
        )

    def _enforce_dimension_cap(self, image: Image.Image) -> None:
        max_dim = settings.max_image_dimension_px
        if max(image.size) > max_dim:
            raise FileTooLargeError(
                f"image dimension exceeds {max_dim} px cap"
            )
