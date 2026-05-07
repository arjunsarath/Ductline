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


# ---------------------------------------------------------------------------
# V4 — length, CFM trace, and pressure (SOLUTION-DESIGN-V4 §4–§6).
# ---------------------------------------------------------------------------

SmacnaClass = Literal["Low", "Medium", "High"]
ScaleSource = Literal["title_block", "manual"]


class SmacnaThresholds(_Frozen):
    """Per-class pressure ceilings in inches of water column (SOLUTION-DESIGN-V4 §6)."""

    low_max_in_wc: float = 2.0
    medium_max_in_wc: float = 3.0


class VelocityThresholds(_Frozen):
    """Secondary velocity check in feet per minute (SOLUTION-DESIGN-V4 §6)."""

    low_max_fpm: float = 2000.0
    medium_max_fpm: float = 2500.0


class OperationalVars(_Frozen):
    """User-editable physics inputs for pressure calculation.

    Defaults track ASHRAE/SMACNA convention for galvanized-steel rigid duct at
    standard air conditions; the UI exposes all of these for override.
    """

    air_density_lb_ft3: float = 0.075
    friction_factor: float = 0.02
    fitting_k_table: dict[str, float] = Field(
        default_factory=lambda: {
            "elbow": 0.3,
            "tee": 0.5,
            "y_branch": 0.4,
            "transition": 0.15,
            "equipment": 0.0,
            "terminal": 0.2,
        }
    )
    # Atmospheric reference at the open / equipment end (in. w.c.). Defaults to
    # 0 — the entire network is reported as a drop relative to this datum.
    source_pressure_in_wc: float = 0.0
    flex_equiv_length_ft: float = 5.0
    smacna_thresholds_in_wc: SmacnaThresholds = Field(default_factory=SmacnaThresholds)
    velocity_thresholds_fpm: VelocityThresholds = Field(default_factory=VelocityThresholds)


class ScaleInfo(_Frozen):
    """Drawing scale used to convert pixels to feet (SOLUTION-DESIGN-V4 §5)."""

    paper_inches_per_foot: float
    source: ScaleSource
    confidence: float


class CfmRange(_Frozen):
    """CFM at each end of a segment after flow tracing (SOLUTION-DESIGN-V4 §6)."""

    start: float
    end: float


class PressureResult(_Frozen):
    """Per-segment pressure value pair plus SMACNA classification."""

    start_in_wc: float
    end_in_wc: float
    smacna_class: SmacnaClass
    velocity_fpm: float


class TerminalRef(_Frozen):
    """Lightweight pointer attaching a terminal to its host segment."""

    terminal_id: str
    distance_along_segment_ft: float
    cfm: float


class V4Terminal(_Frozen):
    id: str
    center: tuple[float, float]
    radius: float
    type_letter: str | None
    cfm: float | None


class V4Segment(_Frozen):
    id: str
    dimension: str
    length_ft: float
    cfm_range: CfmRange
    pressure: PressureResult
    polygon: list[tuple[float, float]]
    terminals_on_segment: list[TerminalRef] = Field(default_factory=list)


class PageDims(_Frozen):
    """Rasterized-page dimensions in pixels at the runner-chosen DPI.

    Frontend overlay viewBox uses these so polygon/terminal coordinates
    (emitted in raster pixel space) align with the rendered PDF page even
    when the data bbox doesn't extend to the page edges.
    """

    width_px: int
    height_px: int
    dpi: int
    # PDF page rotation (0/90/180/270) honoured during rasterisation; the
    # frontend must apply the same rotation when rendering the PDF underneath
    # so its display orientation matches the overlay's pixel space.
    rotation: int = 0


DropReason = Literal["shape_unknown", "diameter_out_of_range", "no_label"]
RectDropReason = Literal[
    "oversized", "non_duct_text", "low_aspect_ratio", "interior_not_empty",
    "not_rectangle", "interior_no_ink", "too_square", "interior_too_full",
    "not_circle", "no_horizontal_divider", "no_three_digit",
]


class DebugRectangle(_Frozen):
    """A rectangle contour plus the filter outcome that decided its fate."""

    corners: list[tuple[int, int]]
    kept: bool
    drop_reason: RectDropReason | None = None


class DebugDimension(_Frozen):
    """One OCR match whose text parses as a duct cross-section dimension."""

    text: str
    kind: Literal["round", "rectangular"]
    bbox: tuple[int, int, int, int]  # (x, y, w, h) in raster pixel space


class DebugOcrMatch(_Frozen):
    """One raw OCR token, unfiltered."""

    text: str
    bbox: tuple[int, int, int, int]  # (x, y, w, h) in raster pixel space
    confidence: float
    # The exact image patch sent to the OCR engine, base64 PNG data URL.
    # Used by the click-to-inspect debug overlay so the operator can see
    # what the model actually saw.
    crop_data_url: str | None = None
    # Which engine produced the text — ``tesseract`` (fast path),
    # ``vlm`` (escalated when Tesseract's confidence < 0.90), or ``empty``
    # (no text detected, no VLM call needed).
    source: Literal["tesseract", "vlm", "empty"] | None = None
    # Four corners of the contour's minimum-area rotated bbox. Lets the
    # overlay draw the bbox tilted at the rectangle's actual angle instead
    # of the axis-aligned bbox, which over-shoots on rotated ducts.
    oriented_corners: list[tuple[int, int]] | None = None
    # Duct length in feet, derived after OCR by combining the rotated bbox's
    # long side with a global px-per-inch scale (median of per-duct scales
    # computed from each label's first dimension). ``None`` for terminals or
    # ducts whose label couldn't be parsed.
    length_ft: float | None = None
    # MVP airflow attribution: duct gets the CFM of any single terminal that
    # is directly bbox-adjacent (i.e. one black-pixel hop away on the plan).
    # Anything more — branched trees, summed downstream CFMs — is the
    # full-simulation problem and out of scope for the demo. ``None`` when
    # zero or multiple terminals sit adjacent to the duct, or for terminals
    # themselves.
    cfm: float | None = None
    velocity_fpm: float | None = None
    # Friction-loss estimate over this duct's length using Darcy: f·(L/Dh)·Vp
    # with f from ``OperationalVars.friction_factor``. Single-duct number,
    # not cumulative system static pressure.
    pressure_drop_in_wc: float | None = None
    smacna_class: SmacnaClass | None = None
    # Bbox of the directly-adjacent terminal that supplied this duct's CFM.
    # Lets the frontend highlight the linked airflow valve when a duct is
    # clicked. ``None`` for terminals or unattributed ducts.
    adjacent_terminal_bbox: tuple[int, int, int, int] | None = None
    # True when the velocity used for the pressure-drop estimate was the
    # default fallback (1500 fpm) rather than measured from a connected
    # terminal's CFM. The number is then a length × dimension heuristic.
    pressure_estimated: bool = False


class DebugPolygon(_Frozen):
    """Every polygon `detect_duct_polygons` returned, tagged with its outcome.

    Emitted only when the runner is invoked with debug=True so the operator can
    see what was kept versus filtered and why. Coordinates are raster pixels at
    the runner-chosen DPI — same space as `V4Segment.polygon` and `PageDims`.
    """

    id: str
    bbox: tuple[int, int, int, int]
    polygon: list[tuple[int, int]]
    shape_hint: Literal["round", "rectangular", "unknown"]
    est_width_px: float
    est_diameter_in: float | None
    kept: bool
    drop_reason: DropReason | None


class V4Debug(_Frozen):
    polygons: list[DebugPolygon]


class V4Result(_Frozen):
    segments: list[V4Segment]
    terminals: list[V4Terminal]
    scale: ScaleInfo
    op_vars: OperationalVars
    page_dims: PageDims
    warnings: list[str] = Field(default_factory=list)
    debug: V4Debug | None = None
    # Step-debug payload: when the runner is short-circuited after a stage
    # (e.g. grey_removal), this holds the intermediate raster as a base64
    # PNG data URL so the frontend can display the partial output.
    stage_image_data_url: str | None = None
    stage_stopped_after: str | None = None
    # Every rectangle contour found on the cleaned raster, tagged with the
    # filter outcome (kept or which filter dropped it). Operator-debug payload.
    debug_rectangles: list["DebugRectangle"] = Field(default_factory=list)
    # OCR matches whose text parses as a duct cross-section dimension.
    # Highlighted only for troubleshooting; not part of the live workflow.
    debug_dimensions: list["DebugDimension"] = Field(default_factory=list)
    # Every raw OCR token returned by the engine on the cleaned page,
    # un-filtered. Operator-debug payload — clickable to read the raw text.
    debug_ocr: list["DebugOcrMatch"] = Field(default_factory=list)
