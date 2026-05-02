"""ReviewerStage tests (SOLUTION-DESIGN-V2 §5.6, ADR-0009).

Ten tests covering the confidence-band ladder (plausible ↑, implausible ↓,
uncertain no-op), the refinement loop (iteration cap + oscillation
early-exit), the per-drawing budget, per-segment failure isolation,
reasoning-trace plumbing, and end-to-end assembly into the final Segment.
Both reviewer and VLM are stubbed — real Ollama is never contacted.
"""

from __future__ import annotations

from typing import Literal

from PIL import Image

from app.pipeline.assemble import _build_segment
from app.pipeline.base import PipelineContext, VLMSegmentDraft
from app.pipeline.review import ReviewerStage
from app.schemas import Geometry, PressureClass, ReasoningStep
from app.source.base import DrawingSource
from app.vlm.tools import (
    CategorizePageTool,
    DetectionResult,
    RefineSegmentTool,
    ReviewSegmentTool,
)

# ── Stub clients ─────────────────────────────────────────────────────────────


class _StubReviewer:
    """ReviewerClient stub. Returns a scripted sequence of verdicts.

    A single fixed verdict is the common case (most tests). For the
    refinement-loop tests we cycle through a list so the stub can return
    "implausible, implausible, plausible" across successive calls.
    """

    def __init__(
        self,
        *,
        verdicts: list[ReviewSegmentTool] | None = None,
        verdict: ReviewSegmentTool | None = None,
        raise_on_segment: str | None = None,
    ) -> None:
        if verdicts is not None:
            self._verdicts = list(verdicts)
            self._cycle = False
        elif verdict is not None:
            self._verdicts = [verdict]
            self._cycle = True
        else:
            raise ValueError("must provide verdict or verdicts")
        self._raise_on_segment = raise_on_segment
        self.call_count = 0

    def review_segment(self, crop, segment, legend):  # noqa: ARG002
        self.call_count += 1
        if (
            self._raise_on_segment is not None
            and segment.segment_id == self._raise_on_segment
        ):
            raise RuntimeError("stub reviewer forced failure")
        if self._cycle:
            return self._verdicts[0]
        if not self._verdicts:
            # Out of scripted verdicts — return uncertain (loop-exit safe).
            return ReviewSegmentTool(verdict="uncertain", reason="stub exhausted")
        return self._verdicts.pop(0)


class _StubVLM:
    """VLMClient stub. ``refine_segment`` returns a scripted bbox per call.

    The other Protocol members are filled with no-op defaults — the reviewer
    stage only ever calls ``refine_segment``.
    """

    def __init__(
        self,
        *,
        bboxes: list[tuple[float, float, float, float]] | None = None,
        shape_hint: str = "rectangular",
        note: str = "geometry tightened",
    ) -> None:
        self._bboxes = list(bboxes) if bboxes else [(0.2, 0.2, 0.8, 0.8)]
        self._shape_hint = shape_hint
        self._note = note
        self.refine_calls = 0

    # The Protocol members the reviewer stage doesn't touch — return no-op
    # values so any incidental call doesn't blow up.

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

    def refine_segment(self, crop, *, critique, previous):  # noqa: ARG002
        self.refine_calls += 1
        bbox = self._bboxes[0] if len(self._bboxes) == 1 else self._bboxes.pop(0)
        return RefineSegmentTool(
            bbox_normalized=bbox,
            shape_hint=self._shape_hint,  # type: ignore[arg-type]  # narrowed at use
            nearby_text=list(previous.nearby_text),
            note=self._note,
        )


# ── Fixture builders ─────────────────────────────────────────────────────────


def _raster_source(width: int = 2000, height: int = 2000) -> DrawingSource:
    """Raster source — DPI is structurally unused for these tests."""
    probe = Image.new("RGB", (width, height), color="white")
    return DrawingSource(
        kind="raster_image",
        pdf_doc=None,
        page=None,
        page_size_pt=None,
        raster_probe=probe,
    )


def _draft(
    segment_id: str = "DUCT-1",
    *,
    rect: tuple[float, float, float, float] = (500.0, 500.0, 700.0, 700.0),
    shape_hint: str = "rectangular",
) -> VLMSegmentDraft:
    return VLMSegmentDraft(
        segment_id=segment_id,
        geometry=Geometry(
            type="bbox",
            points=[(rect[0], rect[1]), (rect[2], rect[3])],
        ),
        shape_hint=shape_hint,
        nearby_text=["14\" x 8\""],
        reasoning_trace=[
            ReasoningStep(stage="vlm_detect_tile", evidence="initial detection")
        ],
    )


def _ctx_with_drafts(
    drafts: list[VLMSegmentDraft],
    *,
    pc_confidence: Literal["high", "medium", "low"] = "medium",
) -> PipelineContext:
    """Build a context with raster source + per-draft pressure_class entries."""
    ctx = PipelineContext(drawing_id="t", original_filename="t.pdf")
    ctx.source = _raster_source()
    ctx.segments_draft = drafts
    for draft in drafts:
        ctx.pressure_classes[draft.segment_id] = PressureClass(
            value="LOW",
            confidence=pc_confidence,
            source="schedule:DUCT-SCHED-2/row-B4",
            alternatives=[],
        )
    return ctx


# ── Tests ────────────────────────────────────────────────────────────────────


def test_reviewer_plausible_bumps_confidence_up() -> None:
    """plausible verdict + medium pre-review confidence → high post-review.

    Asserts: review_verdict == 'plausible', review_iterations == 1, and the
    pressure_class.confidence promoted one rung on the ladder.
    """
    drafts = [_draft()]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="plausible", reason="terminates at AHU")
    )
    vlm = _StubVLM()

    ReviewerStage(reviewer, vlm).run(ctx)

    assert drafts[0].review_verdict == "plausible"
    assert drafts[0].review_iterations == 1
    assert ctx.pressure_classes["DUCT-1"].confidence == "high"
    # Plausible should NOT trigger refine.
    assert vlm.refine_calls == 0


def test_reviewer_implausible_bumps_confidence_down() -> None:
    """implausible at iteration cap → confidence demoted, segment retained.

    The segment must NOT be removed — V2 has no rejection. The verdict is
    recorded, the critique is in the trace, the confidence drops one rung.
    """
    drafts = [_draft()]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="implausible", reason="terminates in space")
    )
    vlm = _StubVLM(bboxes=[(0.3, 0.3, 0.7, 0.7), (0.31, 0.31, 0.71, 0.71)])

    ReviewerStage(reviewer, vlm).run(ctx)

    assert drafts[0].review_verdict == "implausible"
    assert ctx.pressure_classes["DUCT-1"].confidence == "low"
    # Segment NOT removed.
    assert len(ctx.segments_draft) == 1


def test_reviewer_uncertain_is_noop() -> None:
    """uncertain verdict → no confidence change."""
    drafts = [_draft()]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="uncertain", reason="crop too small")
    )
    vlm = _StubVLM()

    ReviewerStage(reviewer, vlm).run(ctx)

    assert drafts[0].review_verdict == "uncertain"
    assert ctx.pressure_classes["DUCT-1"].confidence == "medium"


def test_reviewer_refine_loop_runs_to_iteration_cap() -> None:
    """All-implausible reviewer + meaningfully-different refines → cap hit.

    Default cap = 2. With sufficiently different refine bboxes (no
    oscillation early-exit) the loop runs review→refine→review and stops.
    Final review_iterations == 2; the VLM saw exactly one refine call.
    """
    drafts = [_draft(rect=(500.0, 500.0, 700.0, 700.0))]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="implausible", reason="check geometry")
    )
    # Refined bbox is far enough from the initial one (and from the next
    # refined one) that IoU < 0.95 — keeps the oscillation guard from firing.
    vlm = _StubVLM(bboxes=[(0.05, 0.05, 0.5, 0.5)])

    ReviewerStage(reviewer, vlm, max_iterations=2).run(ctx)

    assert drafts[0].review_iterations == 2
    # Iteration cap = 2: exactly 1 refine call, 2 review calls.
    assert vlm.refine_calls == 1
    assert reviewer.call_count == 2


def test_reviewer_oscillation_early_exit() -> None:
    """Refine returns near-identical geometry → loop exits at iteration 1.

    The oscillation guard fires when IoU > 0.95 between iterations. The
    refined bbox here covers nearly the entire crop, identical (up to
    sub-pixel projection rounding) to the initial draft's bbox after
    projection, so the early-exit triggers immediately.
    """
    initial_rect = (200.0, 200.0, 1800.0, 1800.0)
    drafts = [_draft(rect=initial_rect)]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="implausible", reason="check geometry")
    )
    # Refine returns a bbox that, after crop-norm → source projection, is
    # essentially the same as the initial geometry — IoU very close to 1.
    # Crop is 0..2000 with 300 px padding → effectively the page rect.
    # bbox 0.1..0.9 of the crop ≈ same source rect as the initial draft.
    vlm = _StubVLM(bboxes=[(0.1, 0.1, 0.9, 0.9)])

    ReviewerStage(reviewer, vlm, max_iterations=3).run(ctx)

    # Loop exits immediately after the first refine — iteration count = 2
    # (the refine itself bumped iteration), but the second review call did
    # not happen because oscillation guard fired before it. Assert the
    # second reviewer call was skipped (oscillation early-exit).
    assert vlm.refine_calls == 1
    assert reviewer.call_count == 1, "second review call should be skipped"
    # iteration index was advanced before the early-exit break.
    assert drafts[0].review_iterations == 2


def test_reviewer_per_drawing_budget_exhaustion() -> None:
    """Tiny budget across many segments → later segments retain pre-review state.

    Budget = 2 calls. With plausible verdicts (1 call each) two segments are
    reviewed and the rest stay at the defaults. The stage emits one
    "budget exhausted" warning to ctx.errors.
    """
    drafts = [_draft(segment_id=f"DUCT-{i}") for i in range(1, 6)]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="plausible", reason="ok")
    )
    vlm = _StubVLM()

    ReviewerStage(reviewer, vlm, per_drawing_budget=2).run(ctx)

    # First two segments reviewed, rest left at defaults.
    assert drafts[0].review_verdict == "plausible"
    assert drafts[1].review_verdict == "plausible"
    assert all(d.review_verdict == "not_reviewed" for d in drafts[2:])
    # Exactly one budget-exhausted warning, regardless of how many segments
    # were skipped.
    budget_errors = [
        e for e in ctx.errors if "per-drawing budget exhausted" in e
    ]
    assert len(budget_errors) == 1
    # Reviewer used exactly 2 calls — the budget cap.
    assert reviewer.call_count == 2


def test_reviewer_per_segment_failure_isolated() -> None:
    """Stub raises on one segment; other segments still process normally."""
    drafts = [
        _draft(segment_id="DUCT-1"),
        _draft(segment_id="DUCT-2"),
        _draft(segment_id="DUCT-3"),
    ]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="plausible", reason="ok"),
        raise_on_segment="DUCT-2",
    )
    vlm = _StubVLM()

    ReviewerStage(reviewer, vlm).run(ctx)

    # Failed segment stays at defaults; others are processed.
    assert drafts[0].review_verdict == "plausible"
    assert drafts[1].review_verdict == "not_reviewed"
    assert drafts[2].review_verdict == "plausible"
    assert any(
        "reviewer: segment DUCT-2 failed" in e for e in ctx.errors
    ), f"errors: {ctx.errors}"


def test_reviewer_attaches_critique_to_reasoning_trace() -> None:
    """Verdict + reason → ReasoningStep(stage='reviewer_critique', iteration=1)."""
    drafts = [_draft()]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="implausible", reason="too small")
    )
    vlm = _StubVLM()

    ReviewerStage(reviewer, vlm, max_iterations=1).run(ctx)

    critique_steps = [
        s for s in drafts[0].reasoning_trace if s.stage == "reviewer_critique"
    ]
    assert len(critique_steps) == 1
    assert "too small" in critique_steps[0].evidence
    assert critique_steps[0].iteration == 1


def test_reviewer_writes_review_iterations_count() -> None:
    """Loop runs to iteration 2 → review_iterations == 2 on draft AND Segment.

    Verifies the assemble seam: the count plumbed from VLMSegmentDraft into
    the final Segment by ``_build_segment``.
    """
    drafts = [_draft()]
    ctx = _ctx_with_drafts(drafts, pc_confidence="medium")
    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="implausible", reason="check geometry")
    )
    # Refined bbox far enough from initial to keep oscillation guard quiet.
    vlm = _StubVLM(bboxes=[(0.05, 0.05, 0.5, 0.5)])

    ReviewerStage(reviewer, vlm, max_iterations=2).run(ctx)

    assert drafts[0].review_iterations == 2

    # And it appears in the final Segment after assemble.
    segment = _build_segment(ctx, drafts[0])
    assert segment.review_iterations == 2
    assert segment.review_verdict == "implausible"


def test_reviewer_stage_failure_is_degradation() -> None:
    """Stage-level exception (no source) → drafts preserved + 'reviewer:' error."""
    drafts = [_draft()]
    ctx = PipelineContext(drawing_id="t", original_filename="t.pdf")
    ctx.source = None  # induces stage-level failure
    ctx.segments_draft = drafts
    ctx.pressure_classes["DUCT-1"] = PressureClass(
        value="LOW",
        confidence="medium",
        source="schedule:DUCT-SCHED-2/row-B4",
        alternatives=[],
    )

    reviewer = _StubReviewer(
        verdict=ReviewSegmentTool(verdict="plausible", reason="ok")
    )
    vlm = _StubVLM()

    ReviewerStage(reviewer, vlm).run(ctx)

    # Drafts preserved at pre-review state.
    assert ctx.segments_draft == drafts
    assert drafts[0].review_verdict == "not_reviewed"
    assert drafts[0].review_iterations == 0
    # Stage-level error recorded.
    assert any(e.startswith("reviewer:") for e in ctx.errors), f"errors: {ctx.errors}"
