"""Pipeline primitives — context, stage protocol, errors (SOLUTION-DESIGN §5.1).

The context is a dataclass carrying every cumulative output: each stage reads
what earlier stages wrote and writes its own fields. We don't enforce stage
ordering through types (would explode field-narrowing complexity) — ordering is
enforced by `DetectionPipeline.stages` and validated by the assemble stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.ocr.cache import OCRCache
    from app.schemas import (
        Dimension,
        Geometry,
        PressureClass,
        Quality,
        ReasoningStep,
    )
    from app.source.base import DrawingSource


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


# ── Stage protocol. ───────────────────────────────────────────────────────────


class PipelineStage(Protocol):
    name: str

    def run(self, ctx: PipelineContext) -> PipelineContext: ...
