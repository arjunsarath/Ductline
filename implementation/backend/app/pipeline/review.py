"""Stage 8 — MEP Reviewer + bounded refinement loop (SOLUTION-DESIGN-V2 §5.6, ADR-0009).

Per-segment second pass. The reviewer judges each draft against MEP domain
priors and the legend conventions of the specific drawing; verdicts adjust
the segment's ``pressure_class.confidence`` band deterministically (no
fabricated floats from the model). When the verdict is not ``plausible`` and
budget remains, the detector VLM is called via ``refine_segment`` and the
reviewer re-runs — bounded by an iteration cap, an oscillation guard, and a
per-drawing total-call budget.

Locked decisions (SOLUTION-DESIGN-V2 §5.6, ADR-0009):

  • V2 does not reject. Even ``implausible`` segments stay in the output
    with their confidence bumped DOWN and the critique threaded into the
    reasoning trace. Users see the multi-agent disagreement and decide.

  • Iteration cap — ``max_iterations: int = 2`` default, configurable up to
    3 via ``__init__``. Per-segment, not per-drawing. (Self-Refine /
    Reflexion literature: most gain at 1→2; 2→3 marginal on small models.)

  • Per-drawing budget — ``per_drawing_budget: int = 40`` total VLM calls
    (review + refine summed across all segments). Hard stop. When
    exhausted, remaining segments retain pre-review state and the stage
    emits ONE ``reviewer: per-drawing budget exhausted`` warning.

  • Oscillation early-exit — if iteration N's geometry IoU > 0.95 vs
    iteration N-1, stop the loop for that segment. Prevents the
    near-infinite ping-pong small models can fall into.

  • Confidence-band mapping — deterministic in code, ladder ``low →
    medium → high``. plausible bumps UP one rung (clamps at high),
    implausible bumps DOWN one rung (clamps at low), uncertain is no-op.
    We adjust ``pressure_class.confidence`` because v1's schema only has
    one per-segment confidence field, and V2 §10 G10 explicitly motivates
    elevating it post-review.

  • Reviewer crop — bbox + 300 px equivalent padding, rendered fresh from
    ``ctx.source.render(rect, dpi=high)``. "high" = the smart per-tile
    DPI logic from PR-5: ``smart_dpi_for_rect`` on the crop rect for
    vector PDFs (fall back to 200 when no OCR cache); native pixel space
    for raster sources.

  • Refinement crop — same as reviewer crop (same rect, same DPI). The
    refine call also gets the reviewer's critique + the previous draft.

  • Per-segment failure — log, leave that segment with pre-review state
    (``review_verdict = "not_reviewed"``, ``review_iterations = 0``,
    pre-review confidence), continue. Append
    ``reviewer: segment <id> failed: <reason>`` to ``ctx.errors``.

  • Whole-stage failure — log, append ``reviewer: <reason>`` to
    ``ctx.errors``. Drafts retain their pre-review state. Do NOT clear
    ``ctx.segments_draft``.

  • Constructor takes engines/clients only. Per-request state (segments,
    source, legend, pressure_classes) is read at run() time from ctx.
"""

from __future__ import annotations

import logging
from typing import Literal

from app.pipeline.base import PipelineContext, PipelineStage, VLMSegmentDraft
from app.schemas import Confidence, PressureClass, ReasoningStep
from app.source.base import DrawingSource, RectPt
from app.vlm.base import VLMClient
from app.vlm.reviewer import ReviewerClient, ReviewerVerdict

logger = logging.getLogger(__name__)

# Padding around the segment bbox before rendering the reviewer crop. 300
# pixels at 72 DPI = ~4 inches of source-space context — enough for a duct
# fitting / terminal device on either end of the segment to be visible. The
# §9 Q5 open question on per-segment-size scaling is deferred to a tuning PR.
_REVIEWER_PADDING_PX = 300

# Fallback DPI for vector_pdf reviewer crops when the OCR cache is absent.
# Mirrors PR-5's ``_VECTOR_FALLBACK_DPI`` — the same "known-good DPI for the
# benchmark drawings" value, kept as a local constant rather than imported
# to avoid coupling reviewer geometry to detect_tiled internals.
_VECTOR_FALLBACK_DPI = 200

# 72 points per inch — converts the 300-px padding target into PDF points
# at the chosen DPI.
_PT_PER_INCH = 72

# Oscillation early-exit threshold (V2 §5.6, ADR-0009). When iteration N's
# geometry IoU vs iteration N-1 exceeds this, the model is producing near-
# identical bboxes — further loops are wasted calls.
_OSCILLATION_IOU_THRESHOLD = 0.95

# Iteration cap upper bound (V2 §5.6, ADR-0009). Iteration count beyond 3 has
# no measurable gain on llama3.2-vision per the Self-Refine prior; we clamp
# the constructor argument here rather than failing loudly.
_MAX_ITERATIONS_CEILING = 3

# Confidence band ladder. Promotion = move UP, demotion = move DOWN. Order
# matters — index defines the ladder rung.
_CONFIDENCE_LADDER: tuple[Confidence, ...] = ("low", "medium", "high")


class ReviewerStage(PipelineStage):
    """Per-segment review + bounded refinement (SOLUTION-DESIGN-V2 §5.6)."""

    name = "review"

    def __init__(
        self,
        reviewer: ReviewerClient,
        vlm: VLMClient,
        *,
        max_iterations: int = 2,
        per_drawing_budget: int = 40,
        max_segments_per_drawing: int | None = 5,
    ) -> None:
        # Per V2 §6.1: stages take engines/clients only. ctx.segments_draft,
        # ctx.source, ctx.legend, ctx.pressure_classes are read at run() time.
        self._reviewer = reviewer
        self._vlm = vlm
        # Clamp the iteration cap at the ceiling — a constructor passing 5
        # is almost certainly a typo, and the 3 ceiling is the documented
        # upper bound (V2 §5.6).
        self._max_iterations = max(1, min(max_iterations, _MAX_ITERATIONS_CEILING))
        self._per_drawing_budget = max(0, per_drawing_budget)
        # Reviewer cap for the example/dev workflow. Real drawings can have
        # 60+ segments and reviewing each takes ~10s on cloud — that's a
        # 10-minute review stage that dwarfs detection. Capping at 5 keeps
        # the demo loop tight; production should pass None to remove the
        # cap (still bounded by per_drawing_budget) and run the reviewer as
        # a background batch.
        self._max_segments_per_drawing = (
            None if max_segments_per_drawing is None else max(0, max_segments_per_drawing)
        )

    def run(self, ctx: PipelineContext) -> PipelineContext:
        try:
            self._review_all(ctx)
        except Exception as exc:  # noqa: BLE001 — degradation by design (§5.6)
            logger.exception("review failed")
            ctx.errors.append(f"reviewer: {exc}")
        return ctx

    # ── Top-level loop over segments ─────────────────────────────────────────

    def _review_all(self, ctx: PipelineContext) -> None:
        """Drive the per-segment review with a shared per-drawing call budget."""
        if ctx.source is None:
            # Defensive — if ingest didn't run we can't render reviewer crops.
            # This is treated as a stage-level failure so ``run()`` records
            # the error without clearing drafts.
            raise RuntimeError("ingest must run before review")

        if not ctx.segments_draft:
            logger.info("review: no draft segments; nothing to review")
            return

        budget = self._per_drawing_budget
        budget_warned = False
        total = len(ctx.segments_draft)
        cap = self._max_segments_per_drawing
        # Effective total exposed to the UI is the smaller of the cap
        # and the actual draft count — so the progress bar shows
        # "5/5" instead of "5/64" when the cap is in effect.
        if cap is not None and cap < total:
            displayed_total = cap
            ctx.errors.append(
                f"reviewer: capping review at first {cap} segments "
                f"(of {total} drafts); raise reviewer_max_segments_per_drawing "
                f"or set to None for full review"
            )
        else:
            displayed_total = total

        for index, draft in enumerate(ctx.segments_draft, start=1):
            # Hard cap before we even emit the progress event — anything
            # past `cap` is left at default not_reviewed / iterations=0
            # silently (the warning above already named the situation).
            if cap is not None and index > cap:
                break
            if ctx.progress is not None:
                ctx.progress("review_start", {
                    "stage": "review",
                    "segment_id": draft.segment_id,
                    "current": index,
                    "total": displayed_total,
                })
            if budget <= 0:
                if not budget_warned:
                    ctx.errors.append(
                        f"reviewer: per-drawing budget exhausted "
                        f"({self._per_drawing_budget} VLM calls); "
                        f"remaining segments retain pre-review state"
                    )
                    budget_warned = True
                # Leave the draft as-is — defaults are already
                # review_verdict="not_reviewed", review_iterations=0.
                if ctx.progress is not None:
                    ctx.progress("review_done", {
                        "stage": "review",
                        "segment_id": draft.segment_id,
                        "current": index,
                        "total": displayed_total,
                        "skipped": "budget_exhausted",
                    })
                continue

            try:
                used = _review_one(
                    ctx,
                    draft,
                    reviewer=self._reviewer,
                    vlm=self._vlm,
                    max_iterations=self._max_iterations,
                    budget_remaining=budget,
                )
            except Exception as exc:  # noqa: BLE001 — per-segment isolation
                logger.warning(
                    "review: segment %s failed: %s", draft.segment_id, exc
                )
                ctx.errors.append(
                    f"reviewer: segment {draft.segment_id} failed: {exc}"
                )
                # Leave the draft at pre-review defaults; the previous
                # exception may have happened mid-iteration but defaults are
                # only changed on success paths inside ``_review_one``.
                if ctx.progress is not None:
                    ctx.progress("review_done", {
                        "stage": "review",
                        "segment_id": draft.segment_id,
                        "current": index,
                        "total": displayed_total,
                        "error": str(exc),
                    })
                continue
            budget -= used
            if ctx.progress is not None:
                ctx.progress("review_done", {
                    "stage": "review",
                    "segment_id": draft.segment_id,
                    "current": index,
                    "total": displayed_total,
                    "verdict": draft.review_verdict,
                    "iterations": draft.review_iterations,
                })

        logger.info(
            "review: budget used=%d/%d",
            self._per_drawing_budget - budget,
            self._per_drawing_budget,
        )


# ── Per-segment loop ─────────────────────────────────────────────────────────


def _review_one(
    ctx: PipelineContext,
    draft: VLMSegmentDraft,
    *,
    reviewer: ReviewerClient,
    vlm: VLMClient,
    max_iterations: int,
    budget_remaining: int,
) -> int:
    """Drive review + refinement for ONE segment. Return calls consumed.

    Iteration semantics (§5.6, ADR-0009):

      • Iteration 1 = initial review. Always runs (unless budget is 0 on
        entry — the caller filters that case).
      • Iterations 2..N = (refine → re-review) pairs, gated on the previous
        iteration's verdict being non-plausible AND the oscillation guard
        AND budget remaining. Each pair counts as 2 calls against the
        budget.
      • Loop exits on: plausible verdict / iteration cap reached / budget
        exhausted / oscillation detected. Final verdict + iteration count
        are written to the draft.
    """
    assert ctx.source is not None  # caller checks; assertion satisfies type narrowing

    if budget_remaining <= 0:
        return 0

    crop_rect = _padded_crop_rect(draft, ctx.source)
    dpi = _resolve_crop_dpi(ctx.source, crop_rect, ctx)

    # First review (iteration 1).
    crop = ctx.source.render(crop_rect, dpi=dpi)
    verdict = reviewer.review_segment(crop, draft, ctx.legend)
    calls_used = 1
    iteration = 1
    _append_critique_step(draft, verdict, iteration)
    last_verdict: ReviewerVerdict = verdict
    last_geometry_rect: RectPt = _draft_rect(draft)

    # Refinement loop. Each iteration after 1 is a (refine → re-review) pair.
    while (
        last_verdict.verdict != "plausible"
        and iteration < max_iterations
        and (budget_remaining - calls_used) >= 2
    ):
        # Refine step.
        refined = vlm.refine_segment(
            crop, critique=last_verdict.reason, previous=draft
        )
        calls_used += 1
        next_iteration = iteration + 1
        new_rect = _project_refined_rect(refined.bbox_normalized, crop_rect)

        # Apply the refinement to the draft. We update geometry + shape +
        # nearby_text in place so the next review call sees the revision.
        _apply_refinement(draft, new_rect, refined)
        _append_refine_step(draft, refined.note, next_iteration)

        # Oscillation guard — compare refined geometry to last iteration's.
        if _iou(new_rect, last_geometry_rect) > _OSCILLATION_IOU_THRESHOLD:
            logger.info(
                "review: segment %s oscillation early-exit at iteration %d",
                draft.segment_id,
                next_iteration,
            )
            iteration = next_iteration
            break

        # Re-review at the same crop (same rect, same DPI — only the bbox
        # within the crop changed; the crop itself is unchanged so we don't
        # re-render).
        verdict = reviewer.review_segment(crop, draft, ctx.legend)
        calls_used += 1
        _append_critique_step(draft, verdict, next_iteration)

        last_verdict = verdict
        last_geometry_rect = new_rect
        iteration = next_iteration

    # Commit final reviewer outcome to the draft.
    draft.review_verdict = last_verdict.verdict
    draft.review_iterations = iteration
    _apply_confidence_bump(ctx, draft, last_verdict.verdict)
    return calls_used


# ── Crop geometry ────────────────────────────────────────────────────────────


def _padded_crop_rect(draft: VLMSegmentDraft, source: DrawingSource) -> RectPt:
    """Compute the source-space rect to render for review/refine.

    Padding is the 300-pixel-equivalent target from §5.6 — converted to the
    crop's coord system (PDF points for vector, pixels for raster). For
    vector inputs we resolve points = pixels × 72 / DPI using the same DPI
    we'll render at.
    """
    rect = _draft_rect(draft)
    if source.kind == "vector_pdf" and source.page_size_pt is not None:
        # Use a nominal DPI for the padding-to-points conversion that matches
        # the typical rendered DPI (200) — this is the padding target, not
        # the rendering DPI itself. The exact DPI used for rendering is
        # resolved separately in ``_resolve_crop_dpi``.
        pad_pt = (_REVIEWER_PADDING_PX / _VECTOR_FALLBACK_DPI) * _PT_PER_INCH
        return _expand_and_clamp(rect, pad_pt, _vector_page_rect(source))
    # Raster: padding is pixels in the probe coord space.
    return _expand_and_clamp(
        rect, float(_REVIEWER_PADDING_PX), _raster_page_rect(source)
    )


def _resolve_crop_dpi(
    source: DrawingSource, crop_rect: RectPt, ctx: PipelineContext
) -> int:
    """Resolve the DPI to render the reviewer crop at.

    Mirrors ``app.pipeline.detect_tiled._resolve_per_tile_dpi``: smart DPI
    for vector PDFs when the OCR cache is available, else the 200-DPI
    fallback. Raster sources structurally ignore DPI in
    ``DrawingSource.render`` — we still pass a value for logging.
    """
    if source.kind == "vector_pdf":
        if ctx.ocr_cache is None:
            return _VECTOR_FALLBACK_DPI
        smart = source.smart_dpi_for_rect(crop_rect, ocr_cache=ctx.ocr_cache)
        return smart if smart > 0 else _VECTOR_FALLBACK_DPI
    return _VECTOR_FALLBACK_DPI


def _draft_rect(draft: VLMSegmentDraft) -> RectPt:
    """Project a draft's geometry to its bounding rect.

    Drafts may carry either a ``bbox`` or ``polyline`` geometry; for the
    reviewer we want the axis-aligned bounding box of whichever points are
    present. Two-point bbox geometries take a fast path.
    """
    points = draft.geometry.points
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _expand_and_clamp(rect: RectPt, pad: float, page_rect: RectPt) -> RectPt:
    """Expand rect by ``pad`` on each side, then clamp to ``page_rect``."""
    x0, y0, x1, y1 = rect
    px0, py0, px1, py1 = page_rect
    return (
        max(x0 - pad, px0),
        max(y0 - pad, py0),
        min(x1 + pad, px1),
        min(y1 + pad, py1),
    )


def _vector_page_rect(source: DrawingSource) -> RectPt:
    assert source.page_size_pt is not None
    w, h = source.page_size_pt
    return (0.0, 0.0, float(w), float(h))


def _raster_page_rect(source: DrawingSource) -> RectPt:
    w, h = source.raster_probe.size
    return (0.0, 0.0, float(w), float(h))


def _project_refined_rect(
    bbox_norm: tuple[float, float, float, float], crop_rect: RectPt
) -> RectPt:
    """Project a crop-normalized bbox back into source coordinate space.

    Mirrors ``detect_tiled._project_bbox_to_source``. The model's output is
    [0, 1] in the crop's frame; we scale by the crop's source-space extent
    and offset to the crop's origin. Out-of-range values are clamped — the
    model occasionally emits slightly < 0 or > 1.
    """
    nx0, ny0, nx1, ny1 = bbox_norm
    nx0 = max(0.0, min(1.0, nx0))
    ny0 = max(0.0, min(1.0, ny0))
    nx1 = max(0.0, min(1.0, nx1))
    ny1 = max(0.0, min(1.0, ny1))

    cx0, cy0, cx1, cy1 = crop_rect
    cw = cx1 - cx0
    ch = cy1 - cy0
    return (
        cx0 + nx0 * cw,
        cy0 + ny0 * ch,
        cx0 + nx1 * cw,
        cy0 + ny1 * ch,
    )


# ── Geometry math ────────────────────────────────────────────────────────────


def _iou(a: RectPt, b: RectPt) -> float:
    """Intersection-over-union for two axis-aligned rects.

    Mirrors ``detect_tiled._iou`` — kept as a local helper rather than
    imported to avoid coupling stages. The function is small and stable.
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    iw = max(ix1 - ix0, 0.0)
    ih = max(iy1 - iy0, 0.0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(ax1 - ax0, 0.0) * max(ay1 - ay0, 0.0)
    area_b = max(bx1 - bx0, 0.0) * max(by1 - by0, 0.0)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


# ── Mutators on the draft ────────────────────────────────────────────────────


def _apply_refinement(
    draft: VLMSegmentDraft,
    new_rect: RectPt,
    refined,  # RefineSegmentTool — typed loosely to avoid a runtime import
) -> None:
    """Replace the draft's geometry / shape / nearby_text with refined values.

    Geometry is rebuilt as a two-point bbox in source coordinates so the
    rest of the pipeline (assemble → frontend) sees the same shape it always
    did. ``shape_hint`` and ``nearby_text`` are taken verbatim from the
    refinement output.
    """
    from app.schemas import Geometry  # local — Geometry is a frozen pydantic model

    x0, y0, x1, y1 = new_rect
    draft.geometry = Geometry(
        type="bbox",
        points=[(float(x0), float(y0)), (float(x1), float(y1))],
    )
    draft.shape_hint = refined.shape_hint
    draft.nearby_text = list(refined.nearby_text)


def _append_critique_step(
    draft: VLMSegmentDraft, verdict: ReviewerVerdict, iteration: int
) -> None:
    """Append one ``reviewer_critique`` step to the draft's reasoning trace.

    Evidence format: ``"<verdict>: <reason>"`` so the popover row reads as a
    single sentence. Iteration is the 1-based reviewer iteration index.
    """
    draft.reasoning_trace.append(
        ReasoningStep(
            stage="reviewer_critique",
            evidence=f"{verdict.verdict}: {verdict.reason}",
            iteration=iteration,
        )
    )


def _append_refine_step(
    draft: VLMSegmentDraft, note: str, iteration: int
) -> None:
    """Append one ``reviewer_refine`` step to the draft's reasoning trace.

    Refine steps and critique steps interleave; the iteration index ties
    them together (``critique@1 → refine@2 → critique@2 → refine@3 → …``).
    """
    draft.reasoning_trace.append(
        ReasoningStep(
            stage="reviewer_refine",
            evidence=note or "(no note)",
            iteration=iteration,
        )
    )


def _apply_confidence_bump(
    ctx: PipelineContext,
    draft: VLMSegmentDraft,
    verdict: Literal["plausible", "implausible", "uncertain"],
) -> None:
    """Adjust the segment's pressure_class.confidence per the verdict.

    Why ``pressure_class.confidence`` specifically? V1's schema only has one
    per-segment confidence field. V2 §10 G10 explicitly motivates elevating
    it post-review — the reviewer's judgement is about the WHOLE detection,
    not just the pressure-class call, but pc.confidence is the only public
    surface we can move without a schema rewrite.

    The pressure_class is _Frozen, so we rebuild it with the new confidence
    rather than mutating in place.
    """
    pc = ctx.pressure_classes.get(draft.segment_id)
    if pc is None:
        # No pressure class on the ctx — categorize hasn't run yet, or the
        # detect / extract stages produced no segment-keyed PC. Nothing to
        # bump; the verdict is still recorded on the draft.
        return

    new_confidence = _bump_confidence(pc.confidence, verdict)
    if new_confidence == pc.confidence:
        return
    ctx.pressure_classes[draft.segment_id] = PressureClass(
        value=pc.value,
        confidence=new_confidence,
        source=pc.source,
        alternatives=pc.alternatives,
    )


def _bump_confidence(
    current: Confidence,
    verdict: Literal["plausible", "implausible", "uncertain"],
) -> Confidence:
    """Move one rung up / down / not-at-all on the confidence ladder.

    Promotion ladder: low → medium → high (clamped at high). Demotion:
    high → medium → low (clamped at low). Uncertain is no-op.
    """
    if verdict == "uncertain":
        return current
    if current not in _CONFIDENCE_LADDER:
        # Defensive — unknown band stays where it is rather than mapping
        # arbitrarily.
        return current
    idx = _CONFIDENCE_LADDER.index(current)
    if verdict == "plausible":
        idx = min(idx + 1, len(_CONFIDENCE_LADDER) - 1)
    else:  # implausible
        idx = max(idx - 1, 0)
    return _CONFIDENCE_LADDER[idx]


__all__ = ["ReviewerStage"]
