"""DrawingSource — single seam containing the vector / raster split (ADR-0007).

Replaces ctx.image. Stages call ctx.source.raster_probe for full-sheet image
work and ctx.source.render(rect, dpi) for re-render at a chosen DPI. Vector
inputs render losslessly at any DPI; raster inputs ignore the DPI argument
because the underlying image is fixed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    import pymupdf

    from app.ocr.cache import OCRCache

# A rectangle in PDF points (72/inch) for vector inputs, or pixels for raster
# inputs. Order: (x0, y0, x1, y1).
RectPt = tuple[float, float, float, float]


class DrawingSource(BaseModel):
    """Canonical pipeline-context field for the source drawing.

    For ``vector_pdf`` the pymupdf ``Document`` and ``Page`` are kept open for
    the request lifetime so tiles can be rendered on demand. For raster inputs
    only ``raster_probe`` is populated; ``render`` falls back to cropping it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: Literal["vector_pdf", "raster_pdf", "raster_image"]
    pdf_doc: Any | None = None  # pymupdf.Document — kept open
    page: Any | None = None  # pymupdf.Page — page 0
    page_size_pt: tuple[float, float] | None = None
    raster_probe: Image.Image  # always populated

    def render(self, rect_pt: RectPt, dpi: int) -> Image.Image:
        """Render a region.

        Vector PDFs use ``page.get_pixmap(clip=Rect(rect_pt), dpi=dpi)`` —
        lossless at any DPI. For ``raster_pdf`` and ``raster_image`` the DPI
        argument is ignored because re-render is unavailable; the rect is
        treated as pixel coordinates and the probe is cropped.
        """
        if self.kind == "vector_pdf":
            import pymupdf

            assert self.page is not None
            clip = pymupdf.Rect(*rect_pt)
            pixmap = self.page.get_pixmap(clip=clip, dpi=dpi)
            mode = "RGBA" if pixmap.alpha else "RGB"
            return Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples).convert(
                "RGB"
            )

        # Raster path — DPI is unused; rect is pixel coords.
        x0, y0, x1, y1 = rect_pt
        return self.raster_probe.crop((int(x0), int(y0), int(x1), int(y1)))

    def smart_dpi_for_rect(
        self,
        rect_pt: RectPt,
        *,
        ocr_cache: OCRCache,
        target_text_px: int = 22,
    ) -> int:
        """Return the DPI that puts the smallest text in this rect at ~target_text_px.

        For ``vector_pdf``, derives the answer from the OCR cache's smallest-
        text-height measurement and the probe DPI it was measured at — see
        ``app.ocr.cache.target_dpi_for_height``. The rect is currently
        unused for vector inputs because v2's smallest-text measurement is
        global, not per-rect (per-rect refinement is an ADR-0008 concern).

        For raster sources, smart-DPI is unavailable: the underlying image
        was rasterised at fixed DPI on ingest and cannot be re-rendered, so
        this returns 0 to signal "no upgrade possible". Callers must handle
        the zero by falling back to the cached probe.
        """
        from app.ocr.cache import target_dpi_for_height  # local — avoids cycle

        del rect_pt  # reserved for ADR-0008 per-rect text measurement
        if self.kind != "vector_pdf":
            return 0
        return target_dpi_for_height(
            ocr_cache.smallest_text_height_px_p5,
            ocr_cache.probe_dpi_used,
            target_text_px=target_text_px,
        )

    def close(self) -> None:
        """Release the pymupdf Document. Pipeline runner owns the finally block."""
        doc: pymupdf.Document | None = self.pdf_doc
        if doc is not None:
            doc.close()
        self.pdf_doc = None
        self.page = None
