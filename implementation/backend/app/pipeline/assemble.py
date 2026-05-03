"""Stage 7 — Assemble (SOLUTION-DESIGN §4 row 7).

Per-segment merge of stage outputs into the final response shape, plus
aggregate stats (UI-SPEC.md sidebar "stats" card) and a downsampled display
raster the frontend can render directly.
"""

from __future__ import annotations

import base64
import logging
from collections import Counter
from io import BytesIO

from PIL.Image import Image

from app.pipeline.base import PipelineContext, VLMSegmentDraft
from app.schemas import (
    AggregateStats,
    Confidence,
    DrawingResult,
    PressureClassValue,
    Quality,
    Segment,
)

logger = logging.getLogger(__name__)

# 200 DPI rasters are huge; the display copy is capped so the JSON payload
# stays reasonable. Detection coords stay in the original-resolution space.
_DISPLAY_MAX_LONG_EDGE_PX = 2000


def assemble_result(ctx: PipelineContext) -> DrawingResult:
    assert ctx.source is not None, "ingest must produce a source before assemble runs"

    segments = [_build_segment(ctx, draft) for draft in ctx.segments_draft]
    coord_space = "pdf_points" if ctx.source.kind == "vector_pdf" else "pixels"
    aggregate = _aggregate(segments)
    logger.info(
        "assemble: drawing_id=%s segments=%d by_pc=%s by_conf=%s coord_space=%s "
        "errors=%d quality=%s",
        ctx.drawing_id,
        aggregate.total,
        dict(aggregate.by_pressure_class),
        dict(aggregate.by_confidence),
        coord_space,
        len(ctx.errors),
        ctx.quality.overall if ctx.quality else "missing",
    )
    return DrawingResult(
        drawing_id=ctx.drawing_id,
        width_px=ctx.width_px,
        height_px=ctx.height_px,
        display_image_data_url=_to_display_data_url(ctx.source.raster_probe),
        quality=ctx.quality or _empty_quality(),
        segments=segments,
        aggregate=aggregate,
        coord_space=coord_space,
        page_size_pt=ctx.source.page_size_pt,
        rotation_applied=_absolute_rotation(ctx),
        errors=ctx.errors,
    )


def _build_segment(ctx: PipelineContext, draft: VLMSegmentDraft) -> Segment:
    # Reviewer outcome (SOLUTION-DESIGN-V2 §5.6) is carried on the draft so
    # the reviewer stage doesn't need a parallel ctx field. Drafts that pre-
    # date the reviewer (or were produced when the stage degraded) keep the
    # default "not_reviewed" / 0 — handled at the dataclass field level.
    verdict = draft.review_verdict
    if verdict not in ("plausible", "implausible", "uncertain", "not_reviewed"):
        # Defensive — narrows the dataclass ``str`` to the schema's Literal.
        verdict = "not_reviewed"
    return Segment(
        id=draft.segment_id,
        geometry=draft.geometry,
        dimension=ctx.dimensions.get(draft.segment_id),
        pressure_class=ctx.pressure_classes[draft.segment_id],
        reasoning_trace=draft.reasoning_trace,
        review_verdict=verdict,  # type: ignore[arg-type]  # narrowed above
        review_iterations=draft.review_iterations,
    )


def _aggregate(segments: list[Segment]) -> AggregateStats:
    pc_counter: Counter[PressureClassValue] = Counter()
    conf_counter: Counter[Confidence] = Counter()
    for segment in segments:
        pc_counter[segment.pressure_class.value] += 1
        conf_counter[segment.pressure_class.confidence] += 1

    return AggregateStats(
        total=len(segments),
        by_pressure_class={
            "LOW": pc_counter.get("LOW", 0),
            "MEDIUM": pc_counter.get("MEDIUM", 0),
            "HIGH": pc_counter.get("HIGH", 0),
        },
        by_confidence={
            "high": conf_counter.get("high", 0),
            "medium": conf_counter.get("medium", 0),
            "low": conf_counter.get("low", 0),
        },
    )


def _absolute_rotation(ctx: PipelineContext) -> int:
    """Return the page's absolute rotation in pymupdf terms.

    Vector PDFs may carry a non-zero intrinsic ``/Rotate`` that probe_ocr
    leaves alone (already canonical). ``ctx.source.rotation_applied`` only
    tracks rotations probe_ocr applied, so reading it would miss the
    intrinsic case and leave the frontend's PDF.js render un-rotated while
    segments are in rotated coords.
    """
    if (
        ctx.source is not None
        and ctx.source.kind == "vector_pdf"
        and ctx.source.page is not None
    ):
        return int(ctx.source.page.rotation) % 360
    return ctx.source.rotation_applied if ctx.source is not None else 0


def _empty_quality() -> Quality:
    """Last-resort fallback if the quality stage failed before producing output."""
    return Quality(
        overall="low",
        blur_score=0.0,
        skew_degrees=0.0,
        ocr_confidence_avg=0.0,
        warnings=["quality check did not run"],
    )


def _to_display_data_url(image: Image) -> str:
    long_edge = max(image.size)
    if long_edge > _DISPLAY_MAX_LONG_EDGE_PX:
        scale = _DISPLAY_MAX_LONG_EDGE_PX / long_edge
        new_size = (int(image.width * scale), int(image.height * scale))
        display = image.resize(new_size)
    else:
        display = image

    buffer = BytesIO()
    display.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
