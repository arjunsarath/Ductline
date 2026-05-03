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
from app.pipeline.layout import PageLayout
from app.pipeline.legend import LegendParserStage
from app.pipeline.probe_ocr import ProbeOCRStage
from app.pipeline.quality import QualityCheckStage
from app.pipeline.regions import RegionDetectStage
from app.pipeline.review import ReviewerStage
from app.schemas import DrawingResult
from app.source.base import RectPt
from app.source.encode import raster_probe_data_url
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
                    corrections = ctx.approval_gate("categorize", payload)
                    if corrections is None:
                        # Timeout (cancellation raises) — abort the run with
                        # a clear error rather than silently continuing.
                        ctx.errors.append(
                            "approval timeout: categorize gate not approved"
                        )
                        break
                    layout_corrections = corrections.get("layout")
                    if layout_corrections is not None:
                        # The user edited the layout in the approval panel.
                        # Apply the corrections before legend_parse runs so
                        # downstream stages see the edited rects.
                        _apply_layout_corrections(ctx, layout_corrections)

            # Phase 1 complete — assemble a "preliminary" DrawingResult
            # whose segments still carry the default review_verdict =
            # "not_reviewed". The SSE bridge forwards this to the client
            # so the result view appears immediately; reviewer verdicts
            # trickle in as `segment_reviewed` events afterward
            # (SOLUTION-DESIGN-V2 §5.6).
            preliminary = assemble_result(ctx)
            if progress is not None:
                progress("preliminary_result", {
                    "result": preliminary.model_dump(),
                })

            # Phase 2 — reviewer. Mutates ctx.segments_draft + ctx.pressure_classes
            # in place; emits review_start / review_done / segment_reviewed
            # progress events so the frontend can update segments live.
            self._run_reviewer_phase(ctx)

            # Re-assemble after the reviewer has updated drafts, so the
            # final DrawingResult reflects the post-review verdicts and
            # confidence bumps.
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

    def _run_reviewer_phase(self, ctx: PipelineContext) -> None:
        """Run the post-assemble reviewer (SOLUTION-DESIGN-V2 §5.6).

        Decoupled from ``_post_ingest_stages`` so the runner can emit
        the preliminary result before the reviewer starts. Failures are
        absorbed (logged into ``ctx.errors``) the same way they were
        when the reviewer was an in-line stage — drafts retain pre-
        review state and the pipeline still produces a final result.
        """
        progress = ctx.progress
        if progress is not None:
            progress("stage_start", {
                "stage": "review",
                "index": 0,
                "total": 0,
            })
        stage = ReviewerStage(self._vlm, self._vlm)
        try:
            stage.run(ctx)
            ok = True
            err: str | None = None
        except Exception as exc:  # noqa: BLE001 — degradation by design (§5.6)
            logger.exception("review phase failed")
            ctx.errors.append(f"reviewer: {exc}")
            ok = False
            err = str(exc)
        if progress is not None:
            progress("stage_done", {
                "stage": "review",
                "ok": ok,
                **({"error": err} if err else {}),
            })

    def _post_ingest_stages(self) -> list[PipelineStage]:
        # Probe OCR runs first (SOLUTION-DESIGN-V2 §5.2): it builds the global
        # text inventory the rest of the pipeline reads from. Quality, regions,
        # and detect each have their own OCR call sites today; cache-consumption
        # refactors land in later v2 PRs.
        #
        # The reviewer is intentionally NOT in this list — it runs AFTER the
        # synchronous detect → assemble path, in ``_run_reviewer_phase``,
        # so the user sees a preliminary result the moment detection is
        # done and reviewer verdicts trickle in over the same SSE stream
        # (SOLUTION-DESIGN-V2 §5.6).
        return [
            ProbeOCRStage(self._ocr),
            PageCategorizerStage(self._vlm),
            LegendParserStage(self._vlm),
            QualityCheckStage(self._ocr),
            RegionDetectStage(self._vlm),
            TiledDuctDetectionStage(self._vlm),
            TextExtractionStage(self._ocr),
            PressureClassClassifier(),
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
            raster_probe_data_url(ctx.source.raster_probe)
            if ctx.source is not None
            else None
        ),
        "rotation_applied": (
            ctx.source.rotation_applied if ctx.source is not None else 0
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


# Fields on PageLayout that the categorize approval gate is allowed to
# correct. Anything outside this set in the corrections dict is dropped —
# unknown keys are not load-bearing in the pipeline schema and silently
# ignoring them is safer than blowing up on a forward-compatible client
# that ships extra metadata.
_LAYOUT_CORRECTION_FIELDS = ("plan_view", "legend", "schedule", "title_block", "notes")


def _coerce_rect(value: object) -> RectPt | None:
    """Coerce a corrections-payload value to a RectPt, or None.

    Accepts ``None`` (passthrough), or a 4-element list/tuple of numbers.
    Returns ``None`` for any other shape — the caller treats ``None`` as
    "this field was deleted" or "this field was not edited" depending on
    whether the key was present in the original corrections dict.
    """
    if value is None:
        return None
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    try:
        return (
            float(value[0]),
            float(value[1]),
            float(value[2]),
            float(value[3]),
        )
    except (TypeError, ValueError):
        return None


def _whole_page_rect_from_ctx(ctx: PipelineContext) -> RectPt:
    """Whole-page rect in the source's coord space — mirror of the §7
    categorizer-failed fallback. Used when corrections delete plan_view."""
    assert ctx.source is not None, "approval gate fired before ingest produced source"
    if ctx.source.kind == "vector_pdf" and ctx.source.page_size_pt is not None:
        w, h = ctx.source.page_size_pt
        return (0.0, 0.0, float(w), float(h))
    w_px, h_px = ctx.source.raster_probe.size
    return (0.0, 0.0, float(w_px), float(h_px))


def _apply_layout_corrections(
    ctx: PipelineContext, layout_dict: dict
) -> None:
    """Replace fields on ``ctx.layout`` from a frontend corrections payload.

    The corrections dict is whatever the approval-panel editor sent in
    the POST body's ``layout`` key. Coordinates arrive in the same
    source-coord space the approval payload was emitted in (RectPt),
    so no conversion is needed.

    Schema (every key optional):
        {
          "plan_view":  [x0, y0, x1, y1] | null,
          "legend":     [x0, y0, x1, y1] | null,
          "schedule":   [x0, y0, x1, y1] | null,
          "title_block":[x0, y0, x1, y1] | null,
          "notes":      [[x0, y0, x1, y1], ...]
        }

    Behaviour:
      • Unknown top-level keys are dropped (logged).
      • Malformed rect values are dropped (logged) — the field keeps
        whatever it had before corrections were applied.
      • ``plan_view: null`` (or absent) explicitly deletes plan_view by
        replacing it with the whole-page rect — same posture as the
        §7 categorizer-failed fallback. The pipeline's downstream
        contract is that ``layout.plan_view`` is non-None.
      • ``notes`` is a list — a missing key leaves notes unchanged; an
        empty list clears notes; well-formed entries replace the list.
    """
    if not isinstance(layout_dict, dict):
        logger.warning(
            "approve(categorize): ignoring non-dict layout corrections (%r)",
            type(layout_dict).__name__,
        )
        return

    if ctx.layout is None:
        # Categorizer was skipped or degraded; we still want to honour the
        # corrections (the user may have drawn rects on the whole-page
        # fallback). Build a minimal layout from the whole page so the
        # field updates have somewhere to land.
        ctx.layout = PageLayout(plan_view=_whole_page_rect_from_ctx(ctx))

    unknown_keys = [k for k in layout_dict if k not in _LAYOUT_CORRECTION_FIELDS]
    if unknown_keys:
        logger.info(
            "approve(categorize): dropping unknown layout keys %s",
            unknown_keys,
        )

    # plan_view: null / absent → whole-page fallback. A user who deletes
    # the plan_view rect is signalling "I don't know"; the pipeline never
    # tolerates a None plan_view so the runner substitutes the page rect.
    if "plan_view" in layout_dict:
        coerced = _coerce_rect(layout_dict["plan_view"])
        if coerced is None:
            ctx.layout.plan_view = _whole_page_rect_from_ctx(ctx)
            logger.info(
                "approve(categorize): plan_view deleted by user — using whole-page fallback"
            )
        else:
            ctx.layout.plan_view = coerced

    for field_name in ("legend", "schedule", "title_block"):
        if field_name in layout_dict:
            setattr(ctx.layout, field_name, _coerce_rect(layout_dict[field_name]))

    if "notes" in layout_dict:
        raw_notes = layout_dict["notes"]
        if isinstance(raw_notes, list):
            corrected_notes: list[RectPt] = []
            for entry in raw_notes:
                rect = _coerce_rect(entry)
                if rect is not None:
                    corrected_notes.append(rect)
            ctx.layout.notes = corrected_notes
        else:
            logger.info(
                "approve(categorize): ignoring non-list notes corrections (%r)",
                type(raw_notes).__name__,
            )

    logger.info(
        "approve(categorize): applied corrections — plan_view=%s legend=%s "
        "schedule=%s title_block=%s notes=%d",
        ctx.layout.plan_view,
        ctx.layout.legend,
        ctx.layout.schedule,
        ctx.layout.title_block,
        len(ctx.layout.notes),
    )


# TYPE_CHECKING used solely to silence linters that warn on unused imports —
# the runtime path does not import the SSE-bridge module.
if TYPE_CHECKING:
    from app.api.sessions import Session  # noqa: F401
