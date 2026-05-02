"""DetectionPipeline — composes stages 1–7 (SOLUTION-DESIGN §5.1).

Stage failures after ingest don't abort the pipeline: they're logged into
`ctx.errors` and the response carries a partial result. Ingest failures (file
type, size, multi-page) propagate up because there's nothing to fall back to.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from app.ocr.base import OCRExtractor
from app.pipeline.assemble import assemble_result
from app.pipeline.base import PipelineContext, PipelineStage
from app.pipeline.classify import PressureClassClassifier
from app.pipeline.detect import DuctDetectionStage
from app.pipeline.extract import TextExtractionStage
from app.pipeline.ingest import IngestStage
from app.pipeline.probe_ocr import ProbeOCRStage
from app.pipeline.quality import QualityCheckStage
from app.pipeline.regions import RegionDetectStage
from app.schemas import DrawingResult
from app.vlm.base import VLMClient

logger = logging.getLogger(__name__)


class DetectionPipeline:
    def __init__(self, vlm: VLMClient, ocr: OCRExtractor) -> None:
        self._vlm = vlm
        self._ocr = ocr

    def run(self, file_bytes: bytes, original_filename: str) -> DrawingResult:
        ctx = PipelineContext(
            drawing_id=str(uuid4()),
            original_filename=original_filename,
        )

        # Ingest is the only stage that's allowed to abort the pipeline — every
        # other stage is wrapped so a failure becomes a degradation, not a 500.
        ctx = IngestStage(file_bytes, original_filename).run(ctx)

        try:
            for stage in self._post_ingest_stages():
                try:
                    ctx = stage.run(ctx)
                except Exception as exc:  # noqa: BLE001 — partial-result by design (§9)
                    logger.exception("stage %s failed", stage.name)
                    ctx.errors.append(f"{stage.name}: {exc}")

            return assemble_result(ctx)
        finally:
            # Release the pymupdf Document for vector-PDF sources (ADR-0007).
            if ctx.source is not None:
                ctx.source.close()

    def _post_ingest_stages(self) -> list[PipelineStage]:
        # Probe OCR runs first (SOLUTION-DESIGN-V2 §5.2): it builds the global
        # text inventory the rest of the pipeline reads from. Quality, regions,
        # and detect each have their own OCR call sites today; cache-consumption
        # refactors land in later v2 PRs.
        return [
            ProbeOCRStage(self._ocr),
            QualityCheckStage(self._ocr),
            RegionDetectStage(self._vlm),
            DuctDetectionStage(self._vlm),
            TextExtractionStage(self._ocr),
            PressureClassClassifier(),
        ]
