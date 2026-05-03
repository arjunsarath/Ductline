"""Pipeline primitives — context, stage protocol, errors (SOLUTION-DESIGN §5.1).

The context is a dataclass carrying every cumulative output: each stage reads
what earlier stages wrote and writes its own fields. We don't enforce stage
ordering through types (would explode field-narrowing complexity) — ordering is
enforced by `DetectionPipeline.stages` and validated by the assemble stage.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.ocr.cache import OCRCache
    from app.pipeline.layout import PageLayout
    from app.pipeline.legend import Legend
    from app.schemas import (
        Dimension,
        Geometry,
        PressureClass,
        Quality,
        ReasoningStep,
    )
    from app.source.base import DrawingSource

# Optional progress callback shape — stages call ``ctx.progress(event, payload)``
# when set. The event name is a short snake_case identifier the SSE layer
# turns into a named event; the payload is JSON-serialisable. Stages must
# tolerate ``ctx.progress is None`` — progress is opt-in for the streaming
# endpoint and absent on the test path.
ProgressCallback = Callable[[str, dict[str, Any]], None]

# Optional approval-gate callback for human-in-the-loop pauses. Stages that
# emit a gate (``categorize``, ``tiling``) call ``ctx.approval_gate(gate_name,
# payload)`` and block until the SSE bridge's session is approved. The
# callback returns:
#
#   • ``None`` on timeout / cancellation — the runner treats this as a
#     degradation and aborts the post-gate stages with an error entry.
#   • a ``dict`` on approval. The dict is the corrections payload the
#     client POSTed to the approve endpoint (see app.api.routes). An
#     empty dict means "approve as-is, no corrections"; a dict with a
#     ``layout`` key carries inline edits (categorize gate only).
#
# Cancellation raises (the runner lets it propagate as a degradation).
# When ``ctx.approval_gate is None`` the pipeline runs to completion
# without pausing — that's the test path.
ApprovalGateCallback = Callable[[str, dict[str, Any]], dict[str, Any] | None]


# ── Exceptions surfaced as HTTP errors by app.api.routes (§9). ────────────────


class PipelineError(Exception):
    """Base class for any error the pipeline raises to the API layer."""

    http_status: int = 500


class UnsupportedFileError(PipelineError):
    http_status = 400


class MultiPagePdfError(PipelineError):
    http_status = 400


class FileTooLargeError(PipelineError):
    http_status = 413


# ── Context types built up across stages. ─────────────────────────────────────


@dataclass
class VLMSegmentDraft:
    """Shape returned by stage 4 before stage-5 dimensions / stage-6 PC are merged.

    Mirrors `app.vlm.tools.VLMSegment` plus the refined geometry from OpenCV.
    """

    segment_id: str
    geometry: Geometry
    shape_hint: str  # 'round' | 'rectangular' | 'unknown'
    nearby_text: list[str]
    reasoning_trace: list[ReasoningStep] = field(default_factory=list)
    # Reviewer outcome carried from PR-6 (SOLUTION-DESIGN-V2 §5.6). Plain
    # ``str`` rather than a Literal — dataclass + Literal would force a
    # runtime import of ``typing.Literal`` at module load and the field
    # values are constrained at the schema layer (``Segment.review_verdict``).
    # Default ``"not_reviewed"`` keeps drafts backwards-compatible with the
    # tiled-detect output which never set this field.
    review_verdict: str = "not_reviewed"
    review_iterations: int = 0


@dataclass
class PipelineContext:
    """Mutable shared state passed through stages 1–7.

    Fields populate left-to-right as stages run. None means a stage hasn't
    produced its output yet (or degraded with an entry in `errors`).
    """

    drawing_id: str
    original_filename: str

    # Stage 1 — Ingest (ADR-0007)
    source: DrawingSource | None = None
    # width_px / height_px continue to mean "raster_probe dimensions" so
    # consuming stages that still think in pixel space don't have to special-case
    # the source kind.
    width_px: int = 0
    height_px: int = 0

    # Probe OCR (SOLUTION-DESIGN-V2 §5.2) — runs before quality in v2.
    ocr_cache: OCRCache | None = None

    # Page Categorizer (SOLUTION-DESIGN-V2 §5.3) — populated alongside the v1
    # title_block_bbox / schedule_bbox below. Keeps both seams alive while
    # downstream stages migrate to the richer layout (a later PR).
    layout: PageLayout | None = None

    # Legend Parser (SOLUTION-DESIGN-V2 §5.4) — drawing-specific symbol /
    # abbreviation / line-style conventions. Populated by PR-4; downstream
    # consumers (PR-5 detector, PR-6 reviewer) treat None as "use defaults".
    legend: Legend | None = None

    # Stage 2 — Quality
    quality: Quality | None = None

    # Stage 3 — Region detect
    title_block_bbox: tuple[int, int, int, int] | None = None
    schedule_bbox: tuple[int, int, int, int] | None = None

    # Stage 4 — Duct detection
    segments_draft: list[VLMSegmentDraft] = field(default_factory=list)

    # Stage 5 — Text extraction (per-segment dim + raw nearby text + schedule rows)
    dimensions: dict[str, Dimension | None] = field(default_factory=dict)
    # Schedule rows are stored as raw cell strings (left-to-right). Stage 6
    # scans them for system-tag and pressure-class matches without assuming a
    # column layout — engineering schedules vary too much to interpret blindly.
    schedule_rows: list[list[str]] = field(default_factory=list)

    # Stage 6 — Pressure class (keyed by segment id)
    pressure_classes: dict[str, PressureClass] = field(default_factory=dict)

    # Per-stage degradations surfaced to the client (§9).
    errors: list[str] = field(default_factory=list)

    # Streaming progress hook — set by DetectionPipeline.run() when the
    # caller passes a callback (SSE endpoint). Stages emit at meaningful
    # boundaries (per-tile, per-segment) for the long-running ones.
    progress: ProgressCallback | None = None

    # Human-in-the-loop approval gate (V2 §5.8). Set by DetectionPipeline.run()
    # when a session is supplied. Stages call ``ctx.approval_gate(name, payload)``
    # to pause execution until the SSE bridge gets a POST /approve/{name}.
    # Returns the corrections dict on approval (empty dict if none) or
    # ``None`` on timeout/cancel. Stages must tolerate ``None`` callback.
    approval_gate: ApprovalGateCallback | None = None


# ── Stage protocol. ───────────────────────────────────────────────────────────


class PipelineStage(Protocol):
    name: str

    def run(self, ctx: PipelineContext) -> PipelineContext: ...
