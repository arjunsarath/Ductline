"""Typed tool schema the VLM is required to populate (SOLUTION-DESIGN §5.4).

The pipeline never reads freeform model prose. Stage 4 prompts the model to
emit JSON matching `DetectDuctsTool`; we parse and validate that JSON. If
parsing fails the pipeline degrades to CV-only detection (§9).

Bbox convention: normalized to [0, 1] in the *image as the VLM saw it*. The
client converts back to absolute pixel coords in the original-resolution image
before handing results to stage 4.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ShapeHint = Literal["round", "rectangular", "unknown"]


class VLMSegment(BaseModel):
    bbox: tuple[float, float, float, float] = Field(
        description="(x_min, y_min, x_max, y_max) in normalized [0, 1] coords"
    )
    shape_hint: ShapeHint
    nearby_text: list[str] = Field(
        default_factory=list,
        description="Raw text strings the VLM observed nearby; OCR remains the source of truth",
    )


class DetectDuctsTool(BaseModel):
    """Single tool the VLM is allowed to call. One call per drawing."""

    segments: list[VLMSegment] = Field(default_factory=list)


class DetectionResult(BaseModel):
    """Wrapper returned to stage 4. Carries the typed payload plus a reference
    to which prompt version produced it (for offline debugging).
    """

    prompt_version: str
    segments: list[VLMSegment]


class CategorizePageTool(BaseModel):
    """Page Categorizer fallback (SOLUTION-DESIGN-V2 §5.3, §6.3).

    The algorithmic pass (Hough-line decomposition + OCR keyword match)
    handles the common case. Rectangles it leaves as ``unknown`` are sent
    to the VLM, which must classify them via this single typed field.
    No continuous score, no prose — same posture as the other tools.
    """

    region_kind: Literal[
        "title_block",
        "schedule",
        "legend",
        "notes",
        "plan_view",
        "section_detail",
        "unknown",
    ]


class PageRegionsTool(BaseModel):
    """Page Categorizer VLM-first wire format (SOLUTION-DESIGN-V2 §5.3).

    A single whole-page VLM call returns rough bboxes for the major regions
    on the sheet. Coords are normalized [0, 1] floats (x0, y0, x1, y1) in
    the page's own coord space (post-rotation, pre-tile). The categorizer
    scales them to ``RectPt`` before populating ``PageLayout``.

    All fields are optional except ``notes`` (defaults to []) — the model
    may not see every region on every drawing, and a missing region is more
    informative than a hallucinated one. Per-region positional accuracy of
    5-10% is tolerable; downstream stages (tiled detect) re-tile around the
    plan-view rect and don't need pixel-perfect inputs.

    Retained for backward compatibility — the categorizer's VLM-first path
    no longer calls ``detect_page_regions`` (it now uses two focused calls,
    ``detect_plan_view`` and ``detect_legend``, see ``PlanViewTool`` /
    ``LegendRegionTool``). Implementations may keep the method as a thin
    wrapper for callers that still rely on the combined schema.
    """

    plan_view: tuple[float, float, float, float] | None = None
    # legend is a list — engineering drawings frequently split the legend
    # into a symbol box AND a separate abbreviation table. Returning both
    # rather than guessing a bounding rect over empty space lets the
    # categorizer compute the union deliberately. Empty list ⇒ no legend.
    legend: list[tuple[float, float, float, float]] = Field(default_factory=list)
    schedule: tuple[float, float, float, float] | None = None
    title_block: tuple[float, float, float, float] | None = None
    notes: list[tuple[float, float, float, float]] = Field(default_factory=list)


class PlanViewTool(BaseModel):
    """Focused VLM wire format for plan-view detection (SOLUTION-DESIGN-V2 §5.3).

    Output of ``VLMClient.detect_plan_view`` — one normalized [0, 1] bbox
    in the page's coord space, or ``None`` when the model can't see a plan
    view at all. Single-question schema (no notes, no legend, no
    disambiguation) keeps small VLMs like llama3.2-vision in their sweet
    spot; the categorizer pads + scales the bbox to ``RectPt`` and runs
    the same plausibility guard as the previous combined schema.
    """

    bbox: tuple[float, float, float, float] | None = None


class LegendRegionTool(BaseModel):
    """Focused VLM wire format for legend detection (SOLUTION-DESIGN-V2 §5.3).

    Output of ``VLMClient.detect_legend`` — zero or more normalized [0, 1]
    bboxes in the page's coord space. The list shape preserves the
    multi-block split case (symbols box + abbreviation table on opposite
    sides of the sheet); the categorizer unions them into a single rect
    via the existing ``_bounding_rect`` helper. An empty list means "no
    legend on this drawing", which is non-failure — many sheets ship
    without one.
    """

    bboxes: list[tuple[float, float, float, float]] = Field(default_factory=list)


class TitleBlockTool(BaseModel):
    """Focused VLM wire format for title-block detection (SOLUTION-DESIGN-V2 §5.3).

    Output of ``VLMClient.detect_title_block`` — one normalized [0, 1] bbox
    in the page's coord space, or ``None`` when no title block is visible.
    Same single-question, schema-only posture as ``PlanViewTool`` /
    ``LegendRegionTool``: small VLMs (llama3.2-vision) handle a focused
    "where is the title banner?" reliably, but degrade fast when the same
    call is asked to disambiguate against legends/notes/plan_view.

    A "title block" here covers both the banner-shaped header strip AND the
    sheet-metadata box (project / drawn-by / date / scale). When both
    appear on the page the model is asked to return ONE bbox covering
    them together — the categorizer uses this to clip a single page edge,
    so a unioned rect is sufficient. ``None`` is the legitimate "no title
    block visible" answer, treated as non-failure.
    """

    bbox: tuple[float, float, float, float] | None = None


class NotesRegionTool(BaseModel):
    """Focused VLM wire format for notes-region detection (SOLUTION-DESIGN-V2 §5.3).

    Output of ``VLMClient.detect_notes`` — zero or more normalized [0, 1]
    bboxes in the page's coord space. Notes are paragraphs of written
    instructions ("GENERAL NOTES", numbered text blocks, abbreviation
    keys with prose definitions). The list shape lets a drawing return
    multiple notes columns separately when they sit at non-adjacent
    positions on the sheet; adjacent notes blocks should be returned as
    a single bbox covering the column.

    Empty list is the legitimate "no notes on this drawing" answer.
    Notes are intentionally distinguished from legend (symbol/abbr table)
    and schedule (equipment table) by prompt wording — the categorizer
    treats each region's bboxes independently.
    """

    bboxes: list[tuple[float, float, float, float]] = Field(default_factory=list)


class ScheduleTool(BaseModel):
    """Focused VLM wire format for schedule-region detection (SOLUTION-DESIGN-V2 §5.3).

    Output of ``VLMClient.detect_schedule`` — one normalized [0, 1] bbox
    in the page's coord space, or ``None`` when no schedule is present.
    A "schedule" is the equipment / fixture specification table:
    multi-row tabular data with columns of duct sizes, equipment IDs,
    CFM ratings, etc. Distinct from a legend (symbol key) and notes
    (prose paragraphs) by being a dense grid of numbers and codes.

    Same single-bbox posture as ``TitleBlockTool``: schedules don't
    typically split across the page the way legends do, so a single rect
    is sufficient. ``None`` is the legitimate "no schedule visible"
    answer.
    """

    bbox: tuple[float, float, float, float] | None = None


class ReviewSegmentTool(BaseModel):
    """Reviewer wire format (SOLUTION-DESIGN-V2 §5.6, §6.3).

    Discrete verdict only — no continuous confidence score. Small models
    fabricate floats; the system handles the band promotion in code (see
    ``app.pipeline.review._bump_confidence``).
    """

    verdict: Literal["plausible", "implausible", "uncertain"]
    reason: str  # one short sentence — why the verdict


class RefineSegmentTool(BaseModel):
    """Refinement wire format (SOLUTION-DESIGN-V2 §5.6, §6.3).

    Output of ``VLMClient.refine_segment`` — one segment with possibly
    revised geometry, given the reviewer's critique and the previous
    detection. ``bbox_normalized`` is in the refinement crop's own coord
    space, mirroring the per-tile ``VLMSegment.bbox`` convention.
    """

    bbox_normalized: tuple[float, float, float, float]
    shape_hint: ShapeHint
    nearby_text: list[str] = Field(default_factory=list)
    note: str  # e.g. "geometry tightened" / "shape reclassified"
