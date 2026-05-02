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
