"""Stage 2 — Quality check (SOLUTION-DESIGN §4 row 2).

Three numeric scores → three thresholds → one verdict + warning list.
The verdict is the worst of the three; the user sees a banner unless every
score is in the 'high' band (UI-SPEC.md "Quality banner").
"""

from __future__ import annotations

from app.cv.quality import estimate_skew_degrees, laplacian_variance
from app.ocr.base import OCRExtractor
from app.pipeline.base import PipelineContext, PipelineStage
from app.schemas import Quality, QualityVerdict

# Thresholds — calibrated against typical engineering-drawing renderings at
# 200 DPI. Loosen / tighten if the day-1 sweep on Sample-HVAC reveals miscalls.
_BLUR_HIGH = 100.0
_BLUR_MEDIUM = 50.0

_SKEW_HIGH_DEG = 1.0
_SKEW_MEDIUM_DEG = 3.0

_OCR_HIGH = 0.80
_OCR_MEDIUM = 0.60

# OCR confidence is sampled from a center crop, not the whole drawing —
# stage 5 does the full-coverage OCR run.
_OCR_SAMPLE_SIZE_PX = 1000


class QualityCheckStage(PipelineStage):
    name = "quality"

    def __init__(self, ocr: OCRExtractor) -> None:
        self._ocr = ocr

    def run(self, ctx: PipelineContext) -> PipelineContext:
        assert ctx.source is not None, "ingest stage must run before quality"
        image = ctx.source.raster_probe

        blur = laplacian_variance(image)
        skew = estimate_skew_degrees(image)
        ocr_conf = self._sample_ocr_confidence(ctx)

        warnings: list[str] = []
        verdicts: list[QualityVerdict] = [
            _bucket(blur, _BLUR_HIGH, _BLUR_MEDIUM, higher_is_better=True),
            _bucket(abs(skew), _SKEW_HIGH_DEG, _SKEW_MEDIUM_DEG, higher_is_better=False),
            _bucket(ocr_conf, _OCR_HIGH, _OCR_MEDIUM, higher_is_better=True),
        ]

        if verdicts[0] != "high":
            warnings.append(f"low sharpness (Laplacian variance {blur:.0f})")
        if verdicts[1] != "high":
            warnings.append(f"skew detected ({skew:+.1f}°)")
        if verdicts[2] != "high":
            warnings.append(f"OCR confidence low (avg {ocr_conf:.2f})")

        ctx.quality = Quality(
            overall=_worst(verdicts),
            blur_score=blur,
            skew_degrees=skew,
            ocr_confidence_avg=ocr_conf,
            warnings=warnings,
        )
        return ctx

    def _sample_ocr_confidence(self, ctx: PipelineContext) -> float:
        assert ctx.source is not None
        crop_size = min(_OCR_SAMPLE_SIZE_PX, ctx.width_px, ctx.height_px)
        x = (ctx.width_px - crop_size) // 2
        y = (ctx.height_px - crop_size) // 2
        matches = self._ocr.extract_text(
            ctx.source.raster_probe, region=(x, y, crop_size, crop_size)
        )
        if not matches:
            return 0.0
        return sum(m.confidence for m in matches) / len(matches)


# ── Bucketing helpers. ───────────────────────────────────────────────────────


def _bucket(
    value: float, high_threshold: float, medium_threshold: float, *, higher_is_better: bool
) -> QualityVerdict:
    if higher_is_better:
        if value >= high_threshold:
            return "high"
        if value >= medium_threshold:
            return "medium"
        return "low"
    if value <= high_threshold:
        return "high"
    if value <= medium_threshold:
        return "medium"
    return "low"


_RANK = {"high": 0, "medium": 1, "low": 2}


def _worst(verdicts: list[QualityVerdict]) -> QualityVerdict:
    return max(verdicts, key=lambda v: _RANK[v])
