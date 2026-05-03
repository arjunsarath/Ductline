"""DetectionPipeline — composes stages 1–7 (SOLUTION-DESIGN §5.1).

Stage failures after ingest don't abort the pipeline: they're logged into
`ctx.errors` and the response carries a partial result. Ingest failures (file
type, size, multi-page) propagate up because there's nothing to fall back to.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from app.ocr.base import OCRExtractor
from app.pipeline.assemble import assemble_result
from app.pipeline.base import (
    ApprovalGateCallback,
    PipelineContext,
    PipelineStage,
    ProgressCallback,
)
from app.pipeline.categorize import PageCategorizerStage
from app.pipeline.classify import PressureClassClassifier
from app.pipeline.detect_tiled import TiledDuctDetectionStage
from app.pipeline.extract import TextExtractionStage
from app.pipeline.ingest import IngestStage
from app.pipeline.legend import LegendParserStage
from app.pipeline.probe_ocr import ProbeOCRStage
from app.pipeline.quality import QualityCheckStage
from app.pipeline.regions import RegionDetectStage
from app.pipeline.review import ReviewerStage
from app.schemas import DrawingResult
from app.vlm.base import VLMClient

logger = logging.getLogger(__name__)


class DetectionPipeline:
    def __init__(self, vlm: VLMClient, ocr: OCRExtractor) -> None:
        self._vlm = vlm
        self._ocr = ocr

    def run(
        self,
        file_bytes: bytes,
        original_filename: str,
        *,
        progress: ProgressCallback | None = None,
        approval_gate: ApprovalGateCallback | None = None,
        drawing_id: str | None = None,
    ) -> DrawingResult:
        ctx = PipelineContext(
            drawing_id=drawing_id or str(uuid4()),
            original_filename=original_filename,
            progress=progress,
            approval_gate=approval_gate,
        )

        if progress is not None:
            progress("pipeline_start", {
                "drawing_id": ctx.drawing_id,
                "filename": original_filename,
            })

        # Ingest is the only stage that's allowed to abort the pipeline — every
        # other stage is wrapped so a failure becomes a degradation, not a 500.
        if progress is not None:
            progress("stage_start", {"stage": "ingest", "index": 0, "total": 0})
        ctx = IngestStage(file_bytes, original_filename).run(ctx)
        if progress is not None:
            progress("stage_done", {"stage": "ingest", "ok": ctx.source is not None})

        try:
            stages = self._post_ingest_stages()
            total = len(stages)
            for index, stage in enumerate(stages, start=1):
                if progress is not None:
                    progress("stage_start", {
                        "stage": stage.name,
                        "index": index,
                        "total": total,
                    })
                try:
                    ctx = stage.run(ctx)
                    ok = True
                    err: str | None = None
                except Exception as exc:  # noqa: BLE001 — partial-result by design (§9)
                    logger.exception("stage %s failed", stage.name)
                    ctx.errors.append(f"{stage.name}: {exc}")
                    ok = False
                    err = str(exc)
                if progress is not None:
                    progress("stage_done", {
                        "stage": stage.name,
                        "ok": ok,
                        **({"error": err} if err else {}),
                    })

                # HITL gate: pause after page_categorize so the user can
                # confirm the categorizer's plan_view / legend / heading
                # rects before we commit to the legend parse + tile plan.
                # Only fires when (a) the stage succeeded (no point asking
                # the user to approve a degraded layout) and (b) a gate
                # callback is wired (the test path runs without one).
                if (
                    stage.name == "page_categorize"
                    and ok
                    and ctx.approval_gate is not None
                ):
                    payload = _serialise_layout_for_approval(ctx)
                    if not ctx.approval_gate("categorize", payload):
                        # Timeout (cancellation raises) — abort the run with
                        # a clear error rather than silently continuing.
                        ctx.errors.append(
                            "approval timeout: categorize gate not approved"
                        )
                        break

            result = assemble_result(ctx)
            if progress is not None:
                progress("pipeline_done", {
                    "drawing_id": ctx.drawing_id,
                    "segments": result.aggregate.total,
                    "errors": len(result.errors),
                })
            return result
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
            PageCategorizerStage(self._vlm),
            LegendParserStage(self._vlm),
            QualityCheckStage(self._ocr),
            RegionDetectStage(self._vlm),
            TiledDuctDetectionStage(self._vlm),
            TextExtractionStage(self._ocr),
            PressureClassClassifier(),
            # Reviewer takes the OllamaVisionClient as both VLMClient and
            # ReviewerClient — the same instance implements both Protocols
            # (PR-6). The reviewer mutates segments_draft in place; assemble
            # plumbs review_verdict / review_iterations into final Segments.
            ReviewerStage(self._vlm, self._vlm),
        ]


def _serialise_layout_for_approval(ctx: PipelineContext) -> dict:
    """Build the JSON payload for the categorize approval event.

    Includes the categorized rects (so the frontend can overlay them on the
    page raster) and the raster_probe as a base64 data URL (so the frontend
    has a backdrop to draw on without needing to re-fetch the source). The
    raster is downsampled in app.pipeline.assemble already; we re-encode at
    the same size here.
    """
    layout = ctx.layout
    page_size_pt = ctx.source.page_size_pt if ctx.source is not None else None

    def rect_or_none(rect):
        return list(rect) if rect is not None else None

    return {
        "drawing_id": ctx.drawing_id,
        "coord_space": (
            "pdf_points" if ctx.source and ctx.source.kind == "vector_pdf" else "pixels"
        ),
        "page_size_pt": list(page_size_pt) if page_size_pt is not None else None,
        "raster_probe_size": (
            list(ctx.source.raster_probe.size) if ctx.source is not None else None
        ),
        "raster_probe_data_url": (
            _raster_probe_data_url(ctx) if ctx.source is not None else None
        ),
        "layout": {
            "plan_view": rect_or_none(layout.plan_view) if layout else None,
            "legend": rect_or_none(layout.legend) if layout else None,
            "schedule": rect_or_none(layout.schedule) if layout else None,
            "title_block": rect_or_none(layout.title_block) if layout else None,
            "notes": [list(r) for r in (layout.notes if layout else [])],
        } if layout is not None else None,
        "errors": list(ctx.errors),
    }


def _raster_probe_data_url(ctx: PipelineContext) -> str:
    """Encode ctx.source.raster_probe as a downscaled PNG data URL.

    Reuses assemble.py's display-size cap so the SSE payload stays modest
    even for high-DPI inputs.
    """
    import base64
    from io import BytesIO

    assert ctx.source is not None
    image = ctx.source.raster_probe
    long_edge = max(image.size)
    max_edge = 1600  # matches assemble's _DISPLAY_MAX_LONG_EDGE_PX class
    if long_edge > max_edge:
        scale = max_edge / long_edge
        new_size = (int(image.width * scale), int(image.height * scale))
        image = image.resize(new_size)
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# TYPE_CHECKING used solely to silence linters that warn on unused imports —
# the runtime path does not import the SSE-bridge module.
if TYPE_CHECKING:
    from app.api.sessions import Session  # noqa: F401
