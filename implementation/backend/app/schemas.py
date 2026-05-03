"""API contract — single source of truth (SOLUTION-DESIGN §5.2).

Frozen models so a Segment cannot be mutated mid-pipeline; if a stage needs to
change a value, it builds a new instance. Predictability over cleverness.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Reusable confidence vocabulary — every confidence-bearing field uses this
# alias so we can change the levels in one place if the spec ever does.
Confidence = Literal["high", "medium", "low"]
PressureClassValue = Literal["LOW", "MEDIUM", "HIGH"]
DuctShape = Literal["round", "rectangular"]
QualityVerdict = Literal["high", "medium", "low"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Geometry(_Frozen):
    """Polyline (refined) or bbox (coarse VLM output)."""

    type: Literal["polyline", "bbox"]
    points: list[tuple[float, float]]


class Dimension(_Frozen):
    """Round (`14"⌀`) or rectangular (`10" x 8"`) — strings only.

    Values are emitted exactly as OCR found them; clients format for display.
    Strings that don't match the regex grammar (stage 5) are not emitted at all.
    """

    value: str
    shape: DuctShape
    confidence: Confidence
    source: str  # e.g. 'ocr:near_segment(d=12px)'


class PressureClass(_Frozen):
    """Output of the deterministic ranked-policy classifier (ADR-0004).

    `source` names which tier fired so the reasoning trace can cite it.
    `alternatives` lists values from lower-priority tiers, for the popover.
    """

    value: PressureClassValue
    confidence: Confidence
    source: str  # e.g. 'schedule:DUCT-SCHED-2/row-B4'
    alternatives: list[str] = Field(default_factory=list)


class ReasoningStep(_Frozen):
    """One ordered entry in a segment's evidence trail.

    The popover renders these top-down — see UI-SPEC.md "The popover (load-bearing)".
    """

    stage: str  # e.g. 'vlm_detect', 'ocr_callout', 'schedule_lookup'
    evidence: str
    # Populated only on reviewer steps (stage="reviewer_critique" /
    # "reviewer_refine") so the popover can group iterations 1..N together
    # under a single reviewer "thread". None elsewhere — the existing v1
    # stages do not loop. (SOLUTION-DESIGN-V2 §6.2.)
    iteration: int | None = None


class Segment(_Frozen):
    id: str
    geometry: Geometry
    dimension: Dimension | None  # None when stage 5 found no callout in range
    pressure_class: PressureClass
    reasoning_trace: list[ReasoningStep]
    # Reviewer outcome (SOLUTION-DESIGN-V2 §5.6, §6.2). "not_reviewed" means
    # the reviewer stage did not run on this segment — either it was skipped
    # for budget reasons, the per-segment review failed, or the stage itself
    # degraded. Defaults keep this backwards-compatible with v1 callers.
    review_verdict: Literal[
        "plausible", "implausible", "uncertain", "not_reviewed"
    ] = "not_reviewed"
    review_iterations: int = 0


class Quality(_Frozen):
    """Stage 2 output. UI banner appears when overall != 'high'."""

    overall: QualityVerdict
    blur_score: float
    skew_degrees: float
    ocr_confidence_avg: float
    warnings: list[str] = Field(default_factory=list)


class AggregateStats(_Frozen):
    """Sidebar summary card (UI-SPEC.md). Counts only — no linear-ft estimate
    in v1 because drawing-scale detection isn't a v1 stage.
    """

    total: int
    by_pressure_class: dict[PressureClassValue, int]
    by_confidence: dict[Confidence, int]


class SampleDrawing(_Frozen):
    """One bundled benchmark drawing — surfaced by GET /samples for the
    "Try a sample" UI affordance.
    """

    name: str
    size_bytes: int


class DrawingResult(_Frozen):
    """Top-level response for POST /detect.

    `display_image_data_url` is a downsampled PNG of the ingested raster, sent
    back inline so the frontend has exactly one request to make. Detection
    coordinates are in the *original* (`width_px` / `height_px`) space — the
    frontend scales them to the display resolution.

    `coord_space` and `page_size_pt` (ADR-0007) describe the source so the
    frontend can pick a renderer. Geometry is still emitted in pixel coords in
    this PR; point-space conversion lands with the tiling work.
    """

    drawing_id: str
    width_px: int
    height_px: int
    display_image_data_url: str
    quality: Quality
    segments: list[Segment]
    aggregate: AggregateStats
    coord_space: Literal["pdf_points", "pixels"]
    page_size_pt: tuple[float, float] | None = None
    # CW rotation baked into segment coords + page_size_pt. Vector PDF
    # viewers must re-apply this when rendering the original File via
    # PDF.js, otherwise the canvas shows un-rotated content while the
    # overlay sits in rotated space.
    rotation_applied: Literal[0, 90, 180, 270] = 0
    errors: list[str] = Field(default_factory=list)  # Per-stage degradations (§9)
