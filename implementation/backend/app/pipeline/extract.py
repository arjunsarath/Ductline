"""Stage 5 — Text extraction (SOLUTION-DESIGN §4 row 5).

Two OCR passes:
  • A neighborhood crop around each segment, parsed for dimension callouts.
  • The schedule region, flattened into row-major cell strings for stage 6.

Dimension confidence reflects OCR confidence — we map the float into the
high/medium/low buckets the API contract uses.
"""

from __future__ import annotations

from app.ocr.base import Bbox, OCRExtractor
from app.ocr.grammar import parse_dimension
from app.pipeline.base import PipelineContext, PipelineStage, VLMSegmentDraft
from app.schemas import Geometry, ReasoningStep

# How far past a segment's bbox we look for callouts. UI-SPEC.md uses
# "within 30 px radius" as the language users see in the reasoning trace.
_CALLOUT_SEARCH_RADIUS_PX = 30

# OCR confidence → dimension-confidence band.
_HIGH_OCR = 0.85
_MEDIUM_OCR = 0.65


class TextExtractionStage(PipelineStage):
    name = "text_extraction"

    def __init__(self, ocr: OCRExtractor) -> None:
        self._ocr = ocr

    def run(self, ctx: PipelineContext) -> PipelineContext:
        assert ctx.image is not None

        for draft in ctx.segments_draft:
            self._extract_segment_dimension(ctx, draft)

        if ctx.schedule_bbox is not None:
            ctx.schedule_rows = self._extract_schedule_rows(ctx, ctx.schedule_bbox)

        return ctx

    # ── Per-segment dimension lookup ─────────────────────────────────────────

    def _extract_segment_dimension(
        self, ctx: PipelineContext, draft: VLMSegmentDraft
    ) -> None:
        assert ctx.image is not None

        bbox = _geometry_bbox(draft.geometry)
        search_region = _expand_bbox(
            bbox,
            radius=_CALLOUT_SEARCH_RADIUS_PX,
            max_w=ctx.width_px,
            max_h=ctx.height_px,
        )
        matches = self._ocr.extract_text(ctx.image, region=search_region)

        for match in matches:
            confidence_band = _bucket_ocr_confidence(match.confidence)
            distance = _bbox_distance(bbox, match.bbox)
            source = f"ocr:near_segment(d={distance}px)"
            dimension = parse_dimension(
                match.text, source=source, confidence=confidence_band
            )
            if dimension is not None:
                ctx.dimensions[draft.segment_id] = dimension
                draft.reasoning_trace.append(
                    ReasoningStep(
                        stage="ocr_callout",
                        evidence=(
                            f'"{match.text}" found {distance} px from segment '
                            f"({confidence_band} OCR confidence)"
                        ),
                    )
                )
                return

        ctx.dimensions[draft.segment_id] = None
        draft.reasoning_trace.append(
            ReasoningStep(
                stage="ocr_callout",
                evidence=(
                    f"no callout text within {_CALLOUT_SEARCH_RADIUS_PX} px radius"
                ),
            )
        )

    # ── Schedule extraction ──────────────────────────────────────────────────

    def _extract_schedule_rows(
        self, ctx: PipelineContext, region: Bbox
    ) -> list[list[str]]:
        assert ctx.image is not None
        table = self._ocr.extract_table(ctx.image, region)
        return [[cell.text for cell in row] for row in table.rows]


# ── Pure helpers. ────────────────────────────────────────────────────────────


def _geometry_bbox(geometry: Geometry) -> Bbox:
    xs = [p[0] for p in geometry.points]
    ys = [p[1] for p in geometry.points]
    x_min, y_min = int(min(xs)), int(min(ys))
    x_max, y_max = int(max(xs)), int(max(ys))
    return (x_min, y_min, max(x_max - x_min, 1), max(y_max - y_min, 1))


def _expand_bbox(bbox: Bbox, *, radius: int, max_w: int, max_h: int) -> Bbox:
    x, y, w, h = bbox
    new_x = max(x - radius, 0)
    new_y = max(y - radius, 0)
    new_w = min(w + 2 * radius, max_w - new_x)
    new_h = min(h + 2 * radius, max_h - new_y)
    return (new_x, new_y, new_w, new_h)


def _bbox_distance(a: Bbox, b: Bbox) -> int:
    ax = a[0] + a[2] / 2
    ay = a[1] + a[3] / 2
    bx = b[0] + b[2] / 2
    by = b[1] + b[3] / 2
    return int(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5)


def _bucket_ocr_confidence(confidence: float) -> str:
    if confidence >= _HIGH_OCR:
        return "high"
    if confidence >= _MEDIUM_OCR:
        return "medium"
    return "low"
