"""Stage 2 — Probe OCR (SOLUTION-DESIGN-V2 §5.2).

Builds a global text inventory and measures the 5th-percentile smallest
character height. Two paths:

  • PDF text-layer fast path — for vector PDFs with > 100 chars of real text,
    read font sizes from ``page.get_text("dict")`` directly. Exact, no OCR.
  • OCR fallback — render the full probe and run the OCR engine over it.
    Character-height proxy is the OCR match bbox height.

Auto-orientation also runs here (V2 §5.8.3): the bbox aspect-ratio vote
detects "drawing is rotated" but cannot distinguish 90° from 270° CW.
Direction is resolved by rendering the source at each candidate rotation,
OCRing the render at low DPI, and picking the rotation that produces the
most word-like matches. This stage owns the OCR engine, so all
rotation-resolution work lives here — IngestStage stays "no engines".

Failure is a degradation, not an abort: the pipeline continues with
``ctx.ocr_cache = None`` and a single ``probe_ocr: <reason>`` entry in
``ctx.errors``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.ocr.base import OCRExtractor, OCRMatch
from app.ocr.cache import OCRCache
from app.pipeline.base import PipelineContext, PipelineStage
from app.pipeline.orientation import (
    Rotation,
    detect_rotation_from_image,
    detect_rotation_from_text_layer,
    resolve_rotation_direction,
)

logger = logging.getLogger(__name__)

# Minimum text-layer character count for the fast path. Below this, the text
# layer is sparse enough (or absent) that OCR over the rendered probe is the
# more reliable measurement of what the VLM will actually see.
_TEXT_LAYER_MIN_CHARS = 100

# 5th-percentile index — the 5%-smallest character height in a sorted list.
_SMALLEST_PERCENTILE = 0.05

# Candidates tried by ``resolve_rotation_direction``. Including 0 lets a
# non-rotated drawing win at the cheapest pass; 90 and 270 cover the
# landscape-in-portrait CAD export cases. 180 is rare in this corpus.
_DIRECTION_CANDIDATES = [0, 90, 270]


class ProbeOCRStage(PipelineStage):
    name = "probe_ocr"

    def __init__(self, ocr: OCRExtractor) -> None:
        self._ocr = ocr

    def run(self, ctx: PipelineContext) -> PipelineContext:
        try:
            self._normalize_orientation(ctx)
            ctx.ocr_cache = self._build_cache(ctx)
        except Exception as exc:  # noqa: BLE001 — degradation by design (§9)
            logger.exception("probe_ocr failed")
            ctx.ocr_cache = None
            ctx.errors.append(f"probe_ocr: {exc}")
        return ctx

    # ── Orientation normalize ────────────────────────────────────────────────

    def _normalize_orientation(self, ctx: PipelineContext) -> None:
        """Resolve the source rotation and apply it before any OCR cache work.

        Two-step protocol: a cheap aspect-ratio vote first decides
        "rotated or not"; if rotated, ``resolve_rotation_direction``
        renders the source at each candidate and OCRs to pick between
        90 and 270. The cost on non-rotated drawings is one OCR pass
        (the canonical-orientation render dominates the score outright).
        """
        assert ctx.source is not None, "ingest stage must run before probe_ocr"
        if ctx.source.rotation_applied != 0:
            return  # already normalised — idempotent

        kind = ctx.source.kind
        if kind == "vector_pdf":
            page = ctx.source.page
            assert page is not None
            initial = detect_rotation_from_text_layer(page)
            if initial == 0:
                return
            resolved = resolve_rotation_direction(
                page, self._ocr, _DIRECTION_CANDIDATES
            )
            if resolved == 0:
                logger.info(
                    "probe_ocr: text-layer flagged rotated but direction unresolved"
                )
                return
            self._apply_vector_rotation(ctx, resolved)
            return

        # Raster paths — both raster_pdf and raster_image route through
        # the same image-based detection + rotation.
        initial = detect_rotation_from_image(ctx.source.raster_probe, self._ocr)
        if initial == 0:
            return
        resolved = resolve_rotation_direction(
            ctx.source.raster_probe, self._ocr, _DIRECTION_CANDIDATES
        )
        if resolved == 0:
            logger.info(
                "probe_ocr: image-vote flagged rotated but direction unresolved"
            )
            return
        self._apply_raster_rotation(ctx, resolved)

    def _apply_vector_rotation(self, ctx: PipelineContext, rotation: Rotation) -> None:
        assert ctx.source is not None and ctx.source.page is not None
        page = ctx.source.page
        page.set_rotation(rotation)
        # page.rect now reflects the post-rotation extent; refresh the
        # cached probe + page_size_pt so downstream stages see canonical
        # geometry.
        ctx.source.page_size_pt = (page.rect.width, page.rect.height)
        from PIL import Image as PILImage  # local — avoids module-level cycle

        pixmap = page.get_pixmap(dpi=settings.probe_dpi)
        mode = "RGBA" if pixmap.alpha else "RGB"
        ctx.source.raster_probe = PILImage.frombytes(
            mode, (pixmap.width, pixmap.height), pixmap.samples
        ).convert("RGB")
        ctx.source.rotation_applied = rotation
        ctx.width_px, ctx.height_px = ctx.source.raster_probe.size
        logger.info(
            "probe_ocr: applied auto-rotation rotation=%d to vector_pdf",
            rotation,
        )

    def _apply_raster_rotation(self, ctx: PipelineContext, rotation: Rotation) -> None:
        assert ctx.source is not None
        # PIL.Image.rotate is counter-clockwise; pass -rotation for CW.
        # expand=True grows the canvas to fit the rotated image so we
        # don't crop content.
        ctx.source.raster_probe = ctx.source.raster_probe.rotate(
            -rotation, expand=True
        )
        ctx.source.rotation_applied = rotation
        ctx.width_px, ctx.height_px = ctx.source.raster_probe.size
        logger.info(
            "probe_ocr: applied auto-rotation rotation=%d to %s",
            rotation, ctx.source.kind,
        )

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
        """Read PDF font sizes directly. ``size`` is in PDF points (1/72 inch).

        Also synthesises ``OCRMatch`` entries from each text span — the Page
        Categorizer (SOLUTION-DESIGN-V2 §5.3) keyword-classifies Hough-line
        rectangles by the OCR matches contained in them, so an empty match
        list would force every vector PDF down its whole-page fallback.
        """
        # PDF span bbox is (x0, y0, x1, y1) in points; convert to raster_probe
        # pixel space (x, y, w, h) so matches share the OCR-fallback convention.
        pt_to_px = settings.probe_dpi / 72.0

        sizes_pt: list[float] = []
        matches: list[OCRMatch] = []
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size")
                    if isinstance(size, int | float) and size > 0:
                        sizes_pt.append(float(size))

                    text = str(span.get("text", "")).strip()
                    if not text:
                        continue
                    bbox_pt = span.get("bbox")
                    if not (isinstance(bbox_pt, list | tuple) and len(bbox_pt) == 4):
                        continue
                    x0, y0, x1, y1 = (float(v) for v in bbox_pt)
                    x_px = int(round(x0 * pt_to_px))
                    y_px = int(round(y0 * pt_to_px))
                    w_px = int(round((x1 - x0) * pt_to_px))
                    h_px = int(round((y1 - y0) * pt_to_px))
                    matches.append(
                        OCRMatch(text=text, bbox=(x_px, y_px, w_px, h_px), confidence=1.0)
                    )

        if not sizes_pt:
            # Text layer claimed > 100 chars but yielded no sizes — fall through
            # to OCR rather than fabricating a measurement.
            raise RuntimeError("text-layer reported no font sizes")

        # Convert PDF points → pixel height at probe DPI: px = pt * dpi / 72.
        smallest_pt = _percentile_sorted(sorted(sizes_pt), _SMALLEST_PERCENTILE)
        smallest_px = smallest_pt * settings.probe_dpi / 72.0

        return OCRCache(
            matches=matches,
            smallest_text_height_px_p5=smallest_px,
            source="pdf_text_layer",
            probe_dpi_used=settings.probe_dpi,
        )

    # ── OCR fallback ─────────────────────────────────────────────────────────

    def _build_from_ocr(self, ctx: PipelineContext) -> OCRCache:
        assert ctx.source is not None
        # Orientation is already normalised by _normalize_orientation, so
        # the OCR pass here runs against canonical content — matches are
        # in post-rotation coords with no second pass required.
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
