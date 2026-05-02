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
