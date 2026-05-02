"""Stage 1 — Ingest.

PDF → 200 DPI raster (pdf2image), PNG/JPG → passthrough. RGB-normalize.
Reject multi-page PDFs and oversized files per SOLUTION-DESIGN §9.

Pure algorithmic — no inference, no I/O beyond reading the upload bytes.
"""

from __future__ import annotations

from io import BytesIO

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

_PDF_MAGIC = b"%PDF"


class IngestStage(PipelineStage):
    name = "ingest"

    def __init__(self, file_bytes: bytes, original_filename: str) -> None:
        self._bytes = file_bytes
        self._filename = original_filename

    def run(self, ctx: PipelineContext) -> PipelineContext:
        self._enforce_size_cap()
        image = self._rasterize()
        self._enforce_dimension_cap(image)

        ctx.image = image
        ctx.width_px, ctx.height_px = image.size
        return ctx

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _enforce_size_cap(self) -> None:
        if len(self._bytes) > settings.max_upload_bytes:
            raise FileTooLargeError(
                f"upload exceeds {settings.max_upload_bytes // (1024 * 1024)} MB cap"
            )

    def _rasterize(self) -> Image.Image:
        # PDF detection by magic bytes — file extension is unreliable from a
        # multipart upload.
        if self._bytes[:4] == _PDF_MAGIC:
            return self._rasterize_pdf()
        return self._open_raster_image()

    def _rasterize_pdf(self) -> Image.Image:
        try:
            # last_page=2 lets us detect multi-page without rendering them all.
            pages = convert_from_bytes(
                self._bytes,
                dpi=settings.raster_dpi,
                last_page=2,
            )
        except PDFPageCountError as exc:
            raise UnsupportedFileError("could not read PDF") from exc

        if len(pages) > 1:
            raise MultiPagePdfError(
                "multi-page PDF — upload one page at a time"
            )
        if not pages:
            raise UnsupportedFileError("PDF has no pages")

        return pages[0].convert("RGB")

    def _open_raster_image(self) -> Image.Image:
        try:
            image = Image.open(BytesIO(self._bytes))
        except UnidentifiedImageError as exc:
            raise UnsupportedFileError(
                "unsupported file type — upload PDF, PNG, or JPG"
            ) from exc
        return image.convert("RGB")

    def _enforce_dimension_cap(self, image: Image.Image) -> None:
        max_dim = settings.max_image_dimension_px
        if max(image.size) > max_dim:
            raise FileTooLargeError(
                f"image dimension exceeds {max_dim} px cap"
            )
