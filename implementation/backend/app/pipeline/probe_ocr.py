"""Stage 2 — Probe OCR (SOLUTION-DESIGN-V2 §5.2).

Builds a global text inventory and measures the 5th-percentile smallest
character height. Two paths:

  • PDF text-layer fast path — for vector PDFs with > 100 chars of real text,
    read font sizes from ``page.get_text("dict")`` directly. Exact, no OCR.
  • OCR fallback — render the full probe and run the OCR engine over it.
    Character-height proxy is the OCR match bbox height.

Failure is a degradation, not an abort: the pipeline continues with
``ctx.ocr_cache = None`` and a single ``probe_ocr: <reason>`` entry in
``ctx.errors``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.ocr.base import OCRExtractor
from app.ocr.cache import OCRCache
from app.pipeline.base import PipelineContext, PipelineStage

logger = logging.getLogger(__name__)

# Minimum text-layer character count for the fast path. Below this, the text
# layer is sparse enough (or absent) that OCR over the rendered probe is the
# more reliable measurement of what the VLM will actually see.
_TEXT_LAYER_MIN_CHARS = 100

# 5th-percentile index — the 5%-smallest character height in a sorted list.
_SMALLEST_PERCENTILE = 0.05


class ProbeOCRStage(PipelineStage):
    name = "probe_ocr"

    def __init__(self, ocr: OCRExtractor) -> None:
        self._ocr = ocr

    def run(self, ctx: PipelineContext) -> PipelineContext:
        try:
            ctx.ocr_cache = self._build_cache(ctx)
        except Exception as exc:  # noqa: BLE001 — degradation by design (§9)
            logger.exception("probe_ocr failed")
            ctx.ocr_cache = None
            ctx.errors.append(f"probe_ocr: {exc}")
        return ctx

    # ── Path selection ───────────────────────────────────────────────────────

    def _build_cache(self, ctx: PipelineContext) -> OCRCache:
        assert ctx.source is not None, "ingest stage must run before probe_ocr"

        if ctx.source.kind == "vector_pdf":
            page = ctx.source.page
            assert page is not None
            if len(page.get_text()) > _TEXT_LAYER_MIN_CHARS:
                return self._build_from_text_layer(page)

        return self._build_from_ocr(ctx)

    # ── Text-layer fast path ─────────────────────────────────────────────────

    def _build_from_text_layer(self, page: Any) -> OCRCache:
        """Read PDF font sizes directly. ``size`` is in PDF points (1/72 inch)."""
        sizes_pt: list[float] = []
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size")
                    if isinstance(size, int | float) and size > 0:
                        sizes_pt.append(float(size))

        if not sizes_pt:
            # Text layer claimed > 100 chars but yielded no sizes — fall through
            # to OCR rather than fabricating a measurement.
            raise RuntimeError("text-layer reported no font sizes")

        # Convert PDF points → pixel height at probe DPI: px = pt * dpi / 72.
        smallest_pt = _percentile_sorted(sorted(sizes_pt), _SMALLEST_PERCENTILE)
        smallest_px = smallest_pt * settings.probe_dpi / 72.0

        return OCRCache(
            matches=[],
            smallest_text_height_px_p5=smallest_px,
            source="pdf_text_layer",
            probe_dpi_used=settings.probe_dpi,
        )

    # ── OCR fallback ─────────────────────────────────────────────────────────

    def _build_from_ocr(self, ctx: PipelineContext) -> OCRCache:
        assert ctx.source is not None
        matches = self._ocr.extract_text(ctx.source.raster_probe)

        heights = sorted(float(m.bbox[3]) for m in matches if m.bbox[3] > 0)
        smallest_px = _percentile_sorted(heights, _SMALLEST_PERCENTILE) if heights else 0.0

        return OCRCache(
            matches=list(matches),
            smallest_text_height_px_p5=smallest_px,
            source="ocr_probe",
            probe_dpi_used=settings.probe_dpi,
        )


# ── Pure helpers. ────────────────────────────────────────────────────────────


def _percentile_sorted(sorted_values: list[float], percentile: float) -> float:
    """Nearest-rank percentile on a pre-sorted ascending list.

    Returns 0.0 for an empty list. For a 5th-percentile request on a short
    list, this collapses to the smallest value — which is the intended
    smallest-text-floor behaviour.
    """
    if not sorted_values:
        return 0.0
    idx = int(percentile * (len(sorted_values) - 1))
    return sorted_values[idx]
