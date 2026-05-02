"""OCRCache — global text inventory captured by Probe OCR (SOLUTION-DESIGN-V2 §5.2).

Populated once per drawing by ``ProbeOCRStage``. Downstream stages read the
matches and the 5th-percentile smallest character height to drive smart
per-tile DPI selection. The cache is read-only after Probe OCR writes it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.ocr.base import OCRMatch


class OCRCache(BaseModel):
    """Global OCR inventory + smallest-text measurement for one drawing."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    matches: list[OCRMatch]
    smallest_text_height_px_p5: float
    source: Literal["pdf_text_layer", "ocr_probe"]
    probe_dpi_used: int


def target_dpi_for_height(
    current_height_px: float,
    current_dpi: int,
    target_text_px: int = 22,
) -> int:
    """Return the DPI that would scale ``current_height_px`` to ~``target_text_px`` tall.

    Linear in DPI: pixel height scales with DPI, so the DPI that produces
    ``target_text_px`` is ``current_dpi * target_text_px / current_height_px``.
    Clamped to ``[settings.probe_dpi, settings.smart_dpi_ceiling]`` — never go
    below the probe (that would make text smaller, not bigger) and never
    exceed the safety ceiling (large tiles bloat past Ollama payload limits).
    """
    if current_height_px <= 0:
        return settings.probe_dpi
    scaled = current_dpi * (target_text_px / current_height_px)
    return max(settings.probe_dpi, min(settings.smart_dpi_ceiling, int(round(scaled))))
