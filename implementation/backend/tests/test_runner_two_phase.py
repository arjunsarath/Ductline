"""Runner two-phase reviewer flow (SOLUTION-DESIGN-V2 §5.6).

The reviewer is decoupled from the synchronous detect path: detection
runs to ``assemble_result``, the runner emits ``preliminary_result``,
THEN the reviewer runs and emits ``segment_reviewed`` events as it
processes each draft. ``pipeline_done`` fires only after the reviewer
phase completes.

These tests exercise that ordering directly via
``DetectionPipeline._run_reviewer_phase`` plus a tiny inline driver that
mirrors ``run()``'s phase split. Going through the full ``run()`` would
require stubbing the entire ingest/categorizer/detect chain — out of
scope for ordering tests.
"""

from __future__ import annotations

from typing import Literal

from PIL import Image

from app.pipeline.assemble import assemble_result
from app.pipeline.base import PipelineContext, VLMSegmentDraft
from app.pipeline.runner import DetectionPipeline
from app.schemas import Geometry, PressureClass, ReasoningStep
from app.source.base import DrawingSource
from app.vlm.tools import (
    CategorizePageTool,
    DetectionResult,
    RefineSegmentTool,
    ReviewSegmentTool,
)


class _StubVLM:
    """Implements both VLMClient and ReviewerClient.

    The runner constructs ``ReviewerStage(self._vlm, self._vlm)`` so a
    single stub fills both protocol slots — same pattern the production
    OllamaVisionClient uses.
    """

    def __init__(self) -> None:
        self.refine_calls = 0
        self.review_calls = 0

    def detect(self, image, *, prompt_version="v1"):  # pragma: no cover
        del image, prompt_version
        return DetectionResult(prompt_version="stub", segments=[])

    def disambiguate_region(self, crop, question):  # pragma: no cover
        del crop, question
        return ""

    def categorize_region(self, crop):  # pragma: no cover
        del crop
        return CategorizePageTool(region_kind="unknown")

    def detect_tile(self, crop, *, tile_position, trail_context, legend):  # pragma: no cover
        del crop, tile_position, trail_context, legend
        return DetectionResult(prompt_version="stub", segments=[])

    def refine_segment(self, crop, *, critique, previous):  # pragma: no cover
        del crop, critique
        self.refine_calls += 1
        return RefineSegmentTool(
            bbox_normalized=(0.2, 0.2, 0.8, 0.8),
            shape_hint="rectangular",
            nearby_text=list(previous.nearby_text),
            note="stub refine",
        )

    def review_segment(self, crop, segment, legend):  # noqa: ARG002
        self.review_calls += 1
        return ReviewSegmentTool(verdict="plausible", reason="ok")


def _ctx_with_two_drafts(
    pc_confidence: Literal["high", "medium", "low"] = "medium",
) -> PipelineContext:
    probe = Image.new("RGB", (1000, 1000), color="white")
    src = DrawingSource(
        kind="raster_image",
        pdf_doc=None,
        page=None,
        page_size_pt=None,
        raster_probe=probe,
    )
    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    ctx.source = src
    ctx.width_px, ctx.height_px = src.raster_probe.size
    ctx.segments_draft = [
        VLMSegmentDraft(
            segment_id=sid,
            geometry=Geometry(
                type="bbox",
                points=[(200.0, 200.0), (400.0, 400.0)],
            ),
            shape_hint="rectangular",
            nearby_text=['12" x 8"'],
            reasoning_trace=[
                ReasoningStep(stage="vlm_detect_tile", evidence="initial detection")
            ],
        )
        for sid in ("DUCT-1", "DUCT-2")
    ]
    for draft in ctx.segments_draft:
        ctx.pressure_classes[draft.segment_id] = PressureClass(
            value="LOW",
            confidence=pc_confidence,
            source="schedule:DUCT-SCHED-2/row-B4",
            alternatives=[],
        )
    return ctx


def test_reviewer_phase_emits_preliminary_then_reviewed_then_done() -> None:
    """preliminary_result is emitted BEFORE any segment_reviewed / review_done
    event, and pipeline_done fires AFTER the reviewer phase completes.

    Drives the runner's ``_run_reviewer_phase`` directly and inlines the
    same preliminary-emit + final-assemble that ``run()`` does. This is
    enough to lock the event ordering contract without stubbing the
    full pre-reviewer pipeline.
    """
    captured: list[tuple[str, dict]] = []
    ctx = _ctx_with_two_drafts(pc_confidence="medium")
    ctx.progress = lambda event, payload: captured.append((event, payload))

    pipeline = DetectionPipeline(vlm=_StubVLM(), ocr=object())  # type: ignore[arg-type]

    # Mirror runner.run()'s phase 1 → preliminary → phase 2 → final flow.
    preliminary = assemble_result(ctx)
    if ctx.progress is not None:
        ctx.progress("preliminary_result", {"result": preliminary.model_dump()})
    pipeline._run_reviewer_phase(ctx)
    final = assemble_result(ctx)
    if ctx.progress is not None:
        ctx.progress("pipeline_done", {
            "drawing_id": ctx.drawing_id,
            "segments": final.aggregate.total,
            "errors": len(final.errors),
        })

    event_names = [e for e, _ in captured]

    # 1. preliminary_result fires before any review event.
    assert "preliminary_result" in event_names
    pre_idx = event_names.index("preliminary_result")
    review_indices = [
        i for i, name in enumerate(event_names)
        if name in ("review_start", "review_done", "segment_reviewed")
    ]
    assert review_indices, "expected review_* events after preliminary"
    assert all(i > pre_idx for i in review_indices), (
        f"review events must follow preliminary_result; got {event_names}"
    )

    # 2. preliminary segments carry the default not_reviewed verdict
    #    (the reviewer hasn't run yet).
    pre_payload = captured[pre_idx][1]
    pre_segments = pre_payload["result"]["segments"]
    assert all(s["review_verdict"] == "not_reviewed" for s in pre_segments)

    # 3. pipeline_done is the LAST event and fires after the reviewer.
    assert event_names[-1] == "pipeline_done"
    last_review_idx = max(review_indices)
    assert last_review_idx < len(event_names) - 1

    # 4. Final result reflects post-review verdicts (plausible per stub).
    assert all(s.review_verdict == "plausible" for s in final.segments)


def test_reviewer_phase_records_stage_start_and_done_events() -> None:
    """The reviewer phase brackets itself with stage_start/stage_done so the
    processing UI's stage tracker still sees the review stage transition,
    even though the reviewer no longer lives in ``_post_ingest_stages``."""
    captured: list[tuple[str, dict]] = []
    ctx = _ctx_with_two_drafts()
    ctx.progress = lambda event, payload: captured.append((event, payload))

    pipeline = DetectionPipeline(vlm=_StubVLM(), ocr=object())  # type: ignore[arg-type]
    pipeline._run_reviewer_phase(ctx)

    stage_starts = [
        p for e, p in captured if e == "stage_start" and p.get("stage") == "review"
    ]
    stage_dones = [
        p for e, p in captured if e == "stage_done" and p.get("stage") == "review"
    ]
    assert len(stage_starts) == 1
    assert len(stage_dones) == 1
    assert stage_dones[0]["ok"] is True
