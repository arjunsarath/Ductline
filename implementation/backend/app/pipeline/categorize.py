"""Stage 3 — Page Categorizer (SOLUTION-DESIGN-V2 §5.3).

Decomposes the drawing into named regions (title block, schedule, legend,
notes, plan view) so downstream tiled detect (§5.5) only runs on plan-view
geometry. VLM-first: four sequential auxiliary-region calls — title_block,
legend, notes, schedule — each return their own rough bboxes. plan_view is
DERIVED, never asked: starting from the page rect we clip each auxiliary
region's nearest page edge to its inner edge (with a 1% safety pad), capping
any single-edge clip at 35% of the page dimension to reject mis-identified
auxiliaries. Each focused prompt is 30-50 tokens with a single question and
schema, which is in llama3.2-vision's sweet spot. The Hough-line + keyword
heuristic stays as a fallback, used when the derived plan_view fails the
soft plausibility guard or every auxiliary call errors. The heuristic still
populates ``title_block`` / ``schedule`` / ``notes`` on best-effort when it
runs; on the VLM-first path we surface the auxiliary regions we successfully
identified into the layout fields.

Locked decisions (SOLUTION-DESIGN-V2 §5.3, §7):

  • Single plan view assumed. If multiple plan-view-shaped rectangles are
    detected we keep the largest by area and append a
    ``multi_plan_view_detected`` warning to ``ctx.errors``.

  • Categorizer-failed fallback. If no plan-view rectangle is identified we
    fall back to the whole page and append ``categorizer_failed: …``. The
    pipeline never gets a None ``plan_view`` — tiled detect always has a
    rect to run against.

  • Coordinate space matches the source. ``vector_pdf`` rects are returned in
    PDF points; raster rects are pixel coords expressed as the same
    ``RectPt`` tuple. There is no separate pixel-rect type — see
    ``app.source.base.RectPt``.

  • Failure of the stage as a whole is degradation, not abort: any exception
    leaves ``ctx.layout = None`` and a ``page_categorize: <reason>`` entry
    in ``ctx.errors``. Mirrors ``probe_ocr`` (§5.2).

  • The OCR cache is read-only here. If ``ctx.ocr_cache is None`` (probe_ocr
    degraded) we fall back to whole-page plan_view + warning rather than
    invoking the OCR engine — that's probe_ocr's job, not ours.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from typing import Literal, TypeVar

import cv2
import numpy as np
from PIL import Image

from app.ocr.base import OCRMatch
from app.pipeline.base import PipelineContext, PipelineStage
from app.pipeline.layout import PageLayout
from app.source.base import DrawingSource, RectPt
from app.vlm.base import VLMClient, VLMError

logger = logging.getLogger(__name__)

# Hough-line tuning — drawings have very long axis-aligned lines for borders
# and region dividers. We want only lines long enough to plausibly partition
# the sheet, and we filter to near-axis-aligned.
_HOUGH_RHO = 1
_HOUGH_THETA = np.pi / 180
_HOUGH_VOTE_THRESHOLD = 100
_HOUGH_MIN_LINE_LENGTH_FRACTION = 0.20  # fraction of the long edge
_HOUGH_MAX_LINE_GAP = 20
# A line with absolute slope below 0.05 is treated as horizontal; above 20 as
# vertical. Skewed scans get folded toward axis-aligned by this tolerance.
_AXIS_ALIGNED_SLOPE_TOL = 0.05

# Minimum side length of a candidate rectangle as a fraction of the page —
# anything smaller is below the resolution at which Hough partitioning is
# meaningful. The whole-page rect is always considered too.
_MIN_RECT_FRACTION = 0.05

# Plan-view geometry must be the largest region for it to count — title
# blocks and schedules occupy ~20% on busy sheets, plan views routinely
# exceed 50%. We keep rectangles of any size as candidates and pick by
# keyword + area in classify_rectangles().
# PR-3.6 Issue B: drawing 01 (cleanest CAD) hit categorizer_failed because its
# only plan-view-region label reads "PARTITIONING HVAC LAYOUT" — none of the
# original PLAN/LEVEL/FLOOR keywords matched. "LAYOUT" is a standard MEP
# synonym for "PLAN" on layout drawings; "HVAC" tags any rect that's about
# the mechanical system. Adding both rescues the cleanest drawing in the
# benchmark set without false-positiving title-block text (which is dominated
# by SCALE/DRAWN BY/PROJECT keywords already gated to the lower-right).
_PLAN_VIEW_KEYWORDS = ("PLAN", "LEVEL", "FLOOR", "MECHANICAL PLAN", "LAYOUT", "HVAC")
# Legend headings vary widely by drawing house: this drawing uses
# "DESCRIPTION", others use "SYMBOLS"/"ABBREVIATIONS"/"LEGEND OF SYMBOLS".
# Matching is exact-word (whitespace-bounded) — see
# ``_text_blob_contains_keyword`` — so "LEGEND" does NOT false-match
# "LEGENDARY". Multi-word phrases must appear as that contiguous phrase,
# also whitespace-bounded.
_LEGEND_KEYWORDS = (
    "LEGEND",
    "SYMBOLS",
    "ABBREVIATIONS",
    "DESCRIPTION",
    "LEGEND OF SYMBOLS",
)
_NOTES_KEYWORDS = ("NOTES", "GENERAL NOTES")
_SCHEDULE_KEYWORDS = ("SCHEDULE",)
_TITLE_BLOCK_KEYWORDS = ("PROJECT", "DRAWN BY", "DATE", "SCALE")
# Fraction of the page below/right of which a rectangle is considered to live
# in the lower-right quadrant — the conventional title-block location.
_TITLE_BLOCK_QUADRANT_FRACTION = 0.5

# Strip-merge tunables. A "strip" is a Hough-decomposed rectangle so narrow
# or so elongated that classifying it as plan_view via a stray keyword (e.g.
# "FLOOR PLAN" bleeding in from a title-block divider) would feed PR-5
# (Tiled Detect) garbage. We merge strips into their longest-shared-edge
# neighbour BEFORE classification — see PR-3.6 spec.
#
#   • _STRIP_MIN_DIM_FRACTION: a rect is a strip if its shorter dimension is
#     below this fraction of the page's SHORT edge. We scale against the
#     short edge (not the long edge as in the spec example) because the
#     spec example fails on portrait drawings — a 43-px-wide rect on a
#     595×841 page passes 0.05 × 841 = 42 by a single pixel. Scaling
#     against min(595, 841) = 595 produces a single threshold that's
#     orientation-independent. 0.45 was tuned against the 5-drawing
#     benchmark: it forces a fallback on drawings where the plan view is
#     a single quadrant (drawings 02/04/05) — fallback to whole-page is
#     the V2 §7 spec'd behaviour for "categorizer can't find a plan view"
#     and is preferable to picking a 5%-of-page region as plan_view.
#   • _STRIP_ASPECT_RATIO: long-thin rects are strips even when both sides
#     individually clear the dimension threshold (e.g. a 200×1500 sliver).
#   • _STRIP_MERGE_MAX_ITERATIONS: a merged rectangle may itself still be a
#     strip; we re-check until none remain. The cap is a guard against a
#     pathological loop, not an expected operating point — typical
#     convergence is 1–3 sweeps.
_STRIP_MIN_DIM_FRACTION = 0.45
_STRIP_ASPECT_RATIO = 6.0
_STRIP_MERGE_MAX_ITERATIONS = 10

# VLM-first plausibility guard (SOLUTION-DESIGN-V2 §5.3).
#
# The VLM may return degenerate output: tiny bboxes (<1% area), page-spanning
# bboxes (>99% area, indistinguishable from "I don't know"), or a plan_view
# rect that's effectively the page itself (within 5% of page bounds on every
# side, which provides no signal beyond the whole-page fallback). Any of
# these is a signal the call failed informatively; we drop the result and
# fall through to the heuristic.
_VLM_REGION_MIN_AREA = 0.01
_VLM_REGION_MAX_AREA = 0.99
_VLM_PLAN_VIEW_PAGE_BOUNDS_TOL = 0.05

# Backend hallucination guard for the small-VLM auxiliary path.
#
# Manual testing on llama3.2-vision exposed a failure mode where every
# auxiliary detector returns a plausible-looking bbox, but the bboxes are
# all clustered at the same spot (typically the top-left corner). The
# focused prompts each succeed in isolation — none of the per-call
# plausibility guards (sub-1% / super-99% / page-bounds) catch this — yet
# the result is meaningless: title / legend / schedule / notes can't all
# share the same rectangle.
#
# We detect the cluster by computing pairwise IoU across every auxiliary
# bbox the four detectors produced. If any pair exceeds this threshold
# we drop the entire VLM-first result and fall through to the heuristic.
# 0.3 is permissive enough that legitimately-near auxiliaries (e.g. legend
# + notes column adjacent on the right edge) don't trip it, but tight
# enough that the "all four bboxes stacked at top-left" pattern reliably
# rejects.
_VLM_AUX_OVERLAP_REJECT_IOU = 0.3

# Plan-view derivation tunables (SOLUTION-DESIGN-V2 §5.3, third revision).
#
# The auxiliary-first VLM-first path identifies title / legend / notes /
# schedule rectangles, then derives plan_view as the page rect with each
# auxiliary's nearest page edge clipped to the auxiliary's inner edge.
#
#   • _PLAN_VIEW_DERIVE_SAFETY_PAD: gap (in normalized [0, 1] page units)
#     left between the auxiliary's inner edge and the new plan_view edge,
#     so plan_view doesn't hug a region whose extent slightly exceeded the
#     model's reported bbox. Tuned conservatively at 1% — large enough to
#     avoid clipping plan-view content, small enough to not over-shrink.
#   • _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE: cap on how much any single
#     auxiliary region is allowed to shrink one edge. A real auxiliary
#     never takes more than a third of a page dimension; if the VLM
#     reports a region that would force a >35% clip, the model almost
#     certainly mis-identified what it returned (whole-page hallucination,
#     or a region tagged with the wrong type). We log + skip rather than
#     accept the over-clip.
_PLAN_VIEW_DERIVE_SAFETY_PAD = 0.01
_PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE = 0.35

PageEdge = Literal["top", "bottom", "left", "right"]
_T = TypeVar("_T")


class PageCategorizerStage(PipelineStage):
    name = "page_categorize"

    def __init__(self, vlm: VLMClient) -> None:
        # Per SOLUTION-DESIGN-V2 §6.1: stages take engines/clients only.
        # OCR cache is read at run() time from ctx, never injected here.
        self._vlm = vlm

    def run(self, ctx: PipelineContext) -> PipelineContext:
        try:
            # VLM-first: one whole-page call gives us rough bboxes for every
            # major region. If the call errors or returns implausible output
            # we fall through to the heuristic (Hough + keyword) path.
            try:
                vlm_layout = self._build_layout_via_vlm(ctx)
            except VLMError as exc:
                logger.warning(
                    "page_categorize: VLM-first failed (%s); using heuristic", exc
                )
                vlm_layout = None

            if vlm_layout is not None:
                logger.info("page_categorize: using VLM-first layout")
                ctx.layout = vlm_layout
                return ctx

            logger.info("page_categorize: falling back to heuristic")
            ctx.layout = self._build_layout_via_heuristic(ctx)
        except Exception as exc:  # noqa: BLE001 — degradation by design (§7)
            logger.exception("page_categorize failed")
            ctx.layout = None
            ctx.errors.append(f"page_categorize: {exc}")
        return ctx

    # ── VLM-first layout build (primary path) ───────────────────────────────

    def _build_layout_via_vlm(self, ctx: PipelineContext) -> PageLayout | None:
        """Four focused auxiliary-region VLM calls → derived PageLayout, or None.

        Auxiliary-first refactor (SOLUTION-DESIGN-V2 §5.3, third revision):
        instead of asking the model to localise plan_view directly (which
        it consistently over-bounds, returning whole-page rects), we ask
        for each auxiliary region with its own focused call:

          1. ``detect_title_block`` — banner / sheet-metadata box.
          2. ``detect_legend``      — symbols + abbreviations table(s).
          3. ``detect_notes``       — prose paragraphs of instructions.
          4. ``detect_schedule``    — equipment specification table.

        Each call is independent: a VLMError on any one call logs +
        skips that region and the next call still runs. An empty / null
        return is the legitimate "no such region on this drawing"
        answer, also non-failure.

        plan_view is then DERIVED: starting from the page rect, we
        identify each auxiliary's nearest page edge and clip plan_view
        on that edge to the auxiliary's inner edge, with a 1% safety
        pad. Single-edge clips are capped at 35% of the page dimension —
        an auxiliary that would force a deeper clip is almost certainly
        a mis-identification (whole-page hallucination), logged + skipped.

        The derived plan_view runs through the same plausibility guard
        as before (sub-1% / super-99% / page-bounds-within-5%). On
        failure we return None and the heuristic fallback runs. On
        success we return a PageLayout populated with both the derived
        plan_view AND the auxiliary regions we successfully identified
        — they're load-bearing inputs to plan_view here, so it's natural
        to surface them.
        """
        assert ctx.source is not None, "ingest must run before page_categorize"

        page_w, page_h = _page_dimensions(ctx.source)
        image = ctx.source.raster_probe

        def _project(bbox: tuple[float, float, float, float]) -> RectPt:
            # Pad each VLM bbox by ~3% per edge before scaling. The model
            # consistently under-estimates region extents — page-region
            # tasks aren't its training sweet spot and it tends to bound
            # tight to the densest visible content, clipping outer text
            # and graphics. A small uniform pad recovers most of the
            # missed periphery without significantly polluting plan_view.
            padded = _pad_normalized_bbox(bbox, _VLM_BBOX_PAD_RATIO)
            return _scale_normalized_to_source(padded, page_w, page_h)

        # ── Sequential auxiliary calls. Each one's failure is isolated. ──

        title_tool = _try_detect(
            self._vlm.detect_title_block,
            image,
            label="title_block",
        )
        legend_tool = _try_detect(
            self._vlm.detect_legend,
            image,
            label="legend",
        )
        notes_tool = _try_detect(
            self._vlm.detect_notes,
            image,
            label="notes",
        )
        schedule_tool = _try_detect(
            self._vlm.detect_schedule,
            image,
            label="schedule",
        )

        # Collect normalized auxiliary bboxes for plan_view derivation.
        # Each is a list (legend / notes are multi-bbox; title / schedule
        # contribute at most one). Padding is NOT applied here — we use
        # the raw model bbox for clipping geometry to avoid double-padding
        # the boundary plan_view sits next to. Padding is still applied
        # to the auxiliary regions we emit into the layout fields.
        aux_normalized: list[tuple[float, float, float, float]] = []
        if title_tool is not None and title_tool.bbox is not None:
            aux_normalized.append(title_tool.bbox)
        if legend_tool is not None:
            aux_normalized.extend(legend_tool.bboxes)
        if notes_tool is not None:
            aux_normalized.extend(notes_tool.bboxes)
        if schedule_tool is not None and schedule_tool.bbox is not None:
            aux_normalized.append(schedule_tool.bbox)

        logger.info(
            "page_categorize: vlm-first: auxiliaries identified — "
            "title=%s legend=%d notes=%d schedule=%s",
            "yes" if (title_tool and title_tool.bbox) else "no",
            len(legend_tool.bboxes) if legend_tool else 0,
            len(notes_tool.bboxes) if notes_tool else 0,
            "yes" if (schedule_tool and schedule_tool.bbox) else "no",
        )

        # Backend hallucination guard: if any pair of auxiliaries overlap
        # heavily the VLM almost certainly clustered every detector at the
        # same spot. Reject the whole VLM result and fall through to the
        # heuristic — same posture as the per-call plausibility guards.
        overlap_pair = _pairwise_overlap_above(
            aux_normalized, _VLM_AUX_OVERLAP_REJECT_IOU
        )
        if overlap_pair is not None:
            a, b, iou = overlap_pair
            logger.warning(
                "page_categorize: vlm-first: rejecting result — auxiliary bboxes "
                "%s and %s have IoU %.2f > %.2f (clustered hallucination); "
                "falling back to heuristic",
                tuple(round(v, 3) for v in a),
                tuple(round(v, 3) for v in b),
                iou,
                _VLM_AUX_OVERLAP_REJECT_IOU,
            )
            return None

        derived_plan = _derive_plan_view_normalized(aux_normalized)
        if derived_plan is None:
            logger.info(
                "page_categorize: vlm-first: derived plan_view collapsed; "
                "falling back to heuristic"
            )
            return None

        if not _is_plan_view_bbox_plausible(derived_plan):
            logger.info(
                "page_categorize: vlm-first: failing back to heuristic "
                "(derived plan_view failed plausibility guard: %s)",
                derived_plan,
            )
            return None

        plan_view = _project(derived_plan)
        logger.info(
            "page_categorize: vlm-first: derived plan_view=%s",
            tuple(round(v, 3) for v in derived_plan),
        )

        # Surface the auxiliaries we identified into the layout — they're
        # load-bearing here (they shaped plan_view) so the consumer side
        # benefits from the same regions. Each is padded + scaled to
        # source coords; multi-block legend / notes are unioned via
        # _bounding_rect for fields that expect a single rect.
        title_block: RectPt | None = None
        if title_tool is not None and title_tool.bbox is not None:
            title_block = _project(title_tool.bbox)

        legend: RectPt | None = None
        if legend_tool is not None and legend_tool.bboxes:
            legend = _bounding_rect([_project(b) for b in legend_tool.bboxes])

        notes: list[RectPt] = []
        if notes_tool is not None:
            notes = [_project(b) for b in notes_tool.bboxes]

        schedule: RectPt | None = None
        if schedule_tool is not None and schedule_tool.bbox is not None:
            schedule = _project(schedule_tool.bbox)

        return PageLayout(
            plan_view=plan_view,
            legend=legend,
            schedule=schedule,
            title_block=title_block,
            notes=notes,
        )

    # ── Heuristic layout build (fallback) ───────────────────────────────────

    def _build_layout_via_heuristic(self, ctx: PipelineContext) -> PageLayout:
        assert ctx.source is not None, "ingest must run before page_categorize"

        whole_page_rect = _whole_page_rect(ctx.source)

        # Probe OCR degraded, or vector_pdf text-layer fast path with no OCR
        # matches available — we cannot keyword-classify rectangles. Fall back
        # to whole-page plan_view + warning. This is the explicit contract:
        # we never call the OCR engine ourselves in this stage.
        if ctx.ocr_cache is None or not ctx.ocr_cache.matches:
            ctx.errors.append(
                "categorizer_failed: no plan view identified (no OCR matches available)"
            )
            return PageLayout(plan_view=whole_page_rect)

        # Decompose the probe raster into candidate axis-aligned rectangles.
        rectangles_px = _decompose_into_rectangles(ctx.source.raster_probe)
        logger.info(
            "page_categorize: hough produced %d candidate rectangles", len(rectangles_px)
        )
        if not rectangles_px:
            ctx.errors.append(
                "categorizer_failed: no plan view identified (no candidate rectangles)"
            )
            logger.info(
                "page_categorize: fallback to whole-page (reason: no candidate rectangles)"
            )
            return PageLayout(plan_view=whole_page_rect)

        # Pre-classification strip merge: collapse Hough over-segmentation
        # artefacts (narrow strips that arise when a long line cuts through a
        # functional region) into their nearest neighbour. This must happen
        # before classification — a strip that absorbs a stray keyword like
        # "FLOOR PLAN" from a title-block divider would otherwise be picked
        # as plan_view and feed PR-5 garbage. See PR-3.6 spec.
        probe_w, probe_h = ctx.source.raster_probe.size
        merged_px = _merge_strips(rectangles_px, probe_w, probe_h)
        logger.info(
            "page_categorize: merged %d strips; %d rectangles remaining",
            len(rectangles_px) - len(merged_px),
            len(merged_px),
        )

        # Translate pixel rects to source coordinate space (points for vector,
        # passthrough for raster) and classify each one.
        candidates = [_pixel_rect_to_source(rect, ctx.source) for rect in merged_px]

        plan_view, named_regions = self._classify_rectangles(
            candidates, ctx.ocr_cache.matches, ctx
        )

        if plan_view is None:
            ctx.errors.append("categorizer_failed: no plan view identified")
            logger.info(
                "page_categorize: fallback to whole-page "
                "(reason: no rectangle classified as plan_view)"
            )
            plan_view = whole_page_rect
        else:
            logger.info(
                "page_categorize: plan_view picked = %s",
                tuple(int(v) for v in plan_view),
            )
        return PageLayout(plan_view=plan_view, **named_regions)

    # ── Classification ───────────────────────────────────────────────────────

    def _classify_rectangles(
        self,
        rectangles: list[RectPt],
        matches: list[OCRMatch],
        ctx: PipelineContext,
    ) -> tuple[RectPt | None, dict[str, RectPt | None | list[RectPt]]]:
        """Classify each rectangle by contained OCR text. Unknowns go to VLM.

        Returns ``(plan_view_or_none, named_regions_kwargs)``. Splitting
        plan_view out lets the caller detect the no-plan-view case and swap
        in the whole-page fallback without round-tripping through a partial
        Pydantic model — ``PageLayout.plan_view`` is non-None.
        """
        # OCR matches are in raster_probe pixel coords; convert each rect we
        # check to pixel coords for the containment test. We carry both forms.
        assert ctx.source is not None

        title_block: RectPt | None = None
        schedule: RectPt | None = None
        legend: RectPt | None = None
        notes: list[RectPt] = []
        plan_views: list[RectPt] = []

        page_pixel_rect = _whole_page_pixel_rect(ctx.source.raster_probe)

        for rect_pt in rectangles:
            rect_px = _source_rect_to_pixel(rect_pt, ctx.source)
            contained = _matches_in_rect(matches, rect_px)
            text_blob = " ".join(m.text.upper() for m in contained)

            kind = _classify_by_keywords(text_blob, rect_px, page_pixel_rect)
            classifier = "keyword"
            if kind == "unknown":
                kind = self._vlm_categorize(rect_pt, ctx.source)
                classifier = "vlm"
            logger.info(
                "page_categorize: classified rect %s = %s via %s",
                tuple(int(v) for v in rect_pt),
                kind,
                classifier,
            )

            if kind == "title_block" and title_block is None:
                title_block = rect_pt
            elif kind == "schedule" and schedule is None:
                schedule = rect_pt
            elif kind == "legend" and legend is None:
                legend = rect_pt
            elif kind == "notes":
                notes.append(rect_pt)
            elif kind == "plan_view":
                plan_views.append(rect_pt)
            # section_detail / unknown are intentionally dropped — they are
            # neither plan view nor named regions we surface in this PR.

        plan_view = _select_plan_view(plan_views, ctx)
        named_regions: dict[str, RectPt | None | list[RectPt]] = {
            "title_block": title_block,
            "schedule": schedule,
            "legend": legend,
            "notes": notes,
        }
        return plan_view, named_regions

    # ── VLM fallback ─────────────────────────────────────────────────────────

    def _vlm_categorize(self, rect_pt: RectPt, source: DrawingSource) -> str:
        """Call CategorizePageTool on the rectangle crop. Map errors → ``unknown``.

        We don't fail the stage on VLM errors — a categorizer that can't
        classify a rectangle simply leaves it as unknown, which matches the
        algorithmic ``unknown`` outcome and gets dropped.
        """
        try:
            crop = source.render(rect_pt, dpi=150)
            result = self._vlm.categorize_region(crop)
        except (VLMError, Exception) as exc:  # noqa: BLE001
            logger.warning("categorize_region failed for rect %s: %s", rect_pt, exc)
            return "unknown"
        return result.region_kind


# ── VLM-first plausibility + scaling helpers ─────────────────────────────────


def _try_detect(
    fn: Callable[[Image.Image], _T],
    image: Image.Image,
    *,
    label: str,
) -> _T | None:
    """Run a focused auxiliary VLM call with isolated failure handling.

    Each auxiliary detector (title / legend / notes / schedule) is
    independent — a VLMError on one of them must not block the others.
    Returns the tool result on success or ``None`` on VLMError, with a
    WARNING log naming the affected detector. The caller treats ``None``
    as "this region wasn't successfully identified" and skips it during
    plan_view derivation.
    """
    try:
        return fn(image)
    except VLMError as exc:
        logger.warning(
            "page_categorize: vlm-first: %s call failed (%s); skipping region",
            label,
            exc,
        )
        return None


def _nearest_edge(
    bbox: tuple[float, float, float, float],
) -> PageEdge:
    """Pick the page edge closest to a normalized [0, 1] bbox.

    Computes distance from each of the four page edges to the bbox's
    nearest-side coordinate and returns the smallest. Ties broken in
    deterministic order (top, bottom, left, right) — this matches the
    intuitive "title at the very top of the sheet sits on the top edge"
    case; deeper nesting falls into stable buckets.
    """
    x0, y0, x1, y1 = bbox
    distances: list[tuple[float, PageEdge]] = [
        (y0, "top"),
        (1.0 - y1, "bottom"),
        (x0, "left"),
        (1.0 - x1, "right"),
    ]
    # min() with a stable iteration order — Python's min is left-stable,
    # matching our preferred tie-break (top before bottom before left
    # before right). The float key dominates; the tuple ordering only
    # disambiguates exact ties.
    return min(distances, key=lambda d: d[0])[1]


def _derive_plan_view_normalized(
    aux_regions: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    """Derive plan_view from page rect minus identified auxiliaries.

    Operates entirely in normalized [0, 1] coords for clarity — the
    caller scales to source coords via ``_scale_normalized_to_source``.
    Algorithm:

      1. Start with the full page rect (0, 0, 1, 1).
      2. For each auxiliary region, identify its nearest page edge.
      3. Clip plan_view's edge to the auxiliary's inner edge minus a
         1% safety pad (so plan_view doesn't hug the auxiliary's
         reported boundary).
      4. Reject any clip that would shrink a single edge by more than
         35% of the page dimension — that's a mis-identification
         signal (real auxiliaries don't take up a third of the page).
         Log the skip and continue.
      5. When multiple auxiliaries land on the same edge, the deepest
         clip wins automatically (we take the running min/max).
      6. Returns ``None`` if the resulting rect has any non-positive
         dimension — the auxiliaries collectively consumed plan_view.

    The 35% cap is per-edge per-region: each auxiliary is checked
    independently against the page's relevant dimension (page_h for
    top/bottom edges, page_w for left/right). Page is normalized so
    page_w = page_h = 1.0 here.
    """
    # Plan view starts as the full page; each clip shrinks one edge.
    px0, py0, px1, py1 = 0.0, 0.0, 1.0, 1.0
    for aux in aux_regions:
        edge = _nearest_edge(aux)
        ax0, ay0, ax1, ay1 = aux
        # The "inner edge" of an auxiliary is the side facing the rest
        # of the page — opposite the page edge it sits on.
        if edge == "top":
            new_y0 = ay1 + _PLAN_VIEW_DERIVE_SAFETY_PAD
            clip_amount = new_y0 - py0
            if clip_amount > _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE:
                logger.warning(
                    "page_categorize: vlm-first: skipping aux region %s — "
                    "would clip top edge by %.2f (cap %.2f)",
                    tuple(round(v, 3) for v in aux),
                    clip_amount,
                    _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE,
                )
                continue
            if new_y0 > py0:
                py0 = new_y0
        elif edge == "bottom":
            new_y1 = ay0 - _PLAN_VIEW_DERIVE_SAFETY_PAD
            clip_amount = py1 - new_y1
            if clip_amount > _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE:
                logger.warning(
                    "page_categorize: vlm-first: skipping aux region %s — "
                    "would clip bottom edge by %.2f (cap %.2f)",
                    tuple(round(v, 3) for v in aux),
                    clip_amount,
                    _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE,
                )
                continue
            if new_y1 < py1:
                py1 = new_y1
        elif edge == "left":
            new_x0 = ax1 + _PLAN_VIEW_DERIVE_SAFETY_PAD
            clip_amount = new_x0 - px0
            if clip_amount > _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE:
                logger.warning(
                    "page_categorize: vlm-first: skipping aux region %s — "
                    "would clip left edge by %.2f (cap %.2f)",
                    tuple(round(v, 3) for v in aux),
                    clip_amount,
                    _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE,
                )
                continue
            if new_x0 > px0:
                px0 = new_x0
        else:  # right
            new_x1 = ax0 - _PLAN_VIEW_DERIVE_SAFETY_PAD
            clip_amount = px1 - new_x1
            if clip_amount > _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE:
                logger.warning(
                    "page_categorize: vlm-first: skipping aux region %s — "
                    "would clip right edge by %.2f (cap %.2f)",
                    tuple(round(v, 3) for v in aux),
                    clip_amount,
                    _PLAN_VIEW_DERIVE_MAX_CLIP_PER_EDGE,
                )
                continue
            if new_x1 < px1:
                px1 = new_x1
        logger.info(
            "page_categorize: vlm-first: clipped %s edge by aux=%s",
            edge,
            tuple(round(v, 3) for v in aux),
        )

    if px1 - px0 <= 0 or py1 - py0 <= 0:
        return None
    return (px0, py0, px1, py1)


def _normalized_bbox_area(bbox: tuple[float, float, float, float]) -> float:
    """Area of an [x0, y0, x1, y1] bbox in normalized [0, 1] coords."""
    x0, y0, x1, y1 = bbox
    return max(x1 - x0, 0.0) * max(y1 - y0, 0.0)


def _normalized_iou(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Intersection-over-union for two normalized [0, 1] bboxes.

    Returns 0.0 if either bbox is degenerate or the boxes do not overlap.
    Used by the auxiliary-overlap guard to detect clustered VLM
    hallucinations (every auxiliary returned the same rect).
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    inter = max(ix1 - ix0, 0.0) * max(iy1 - iy0, 0.0)
    if inter <= 0.0:
        return 0.0
    union = _normalized_bbox_area(a) + _normalized_bbox_area(b) - inter
    if union <= 0.0:
        return 0.0
    return inter / union


def _pairwise_overlap_above(
    bboxes: list[tuple[float, float, float, float]],
    threshold: float,
) -> tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    float,
] | None:
    """Find the first pair of bboxes whose IoU exceeds ``threshold``.

    Returns ``(a, b, iou)`` for the offending pair, or ``None`` if no such
    pair exists (including the trivial ``len < 2`` case). The caller logs
    the offending pair before falling back to the heuristic.
    """
    n = len(bboxes)
    if n < 2:
        return None
    for i in range(n):
        for j in range(i + 1, n):
            iou = _normalized_iou(bboxes[i], bboxes[j])
            if iou > threshold:
                return bboxes[i], bboxes[j], iou
    return None


def _is_plan_view_bbox_plausible(
    bbox: tuple[float, float, float, float],
) -> bool:
    """Soft sanity check on the focused plan-view bbox — SOLUTION-DESIGN-V2 §5.3.

    Reject bboxes that are sub-1% / super-99% in area or essentially the
    whole page (within 5% slop on every edge). Any of those signals "the
    model didn't actually localise a plan view" and we should fall back
    to the heuristic. The categorizer no longer evaluates legend/schedule/
    notes/title_block on the VLM-first path, so this guard is plan-view-
    only — legend bboxes are accepted as-is from the focused legend call.
    """
    area = _normalized_bbox_area(bbox)
    if area < _VLM_REGION_MIN_AREA or area > _VLM_REGION_MAX_AREA:
        return False
    x0, y0, x1, y1 = bbox
    tol = _VLM_PLAN_VIEW_PAGE_BOUNDS_TOL
    if x0 <= tol and y0 <= tol and x1 >= 1.0 - tol and y1 >= 1.0 - tol:
        # Plan view is essentially the page — degenerate, fall back.
        return False
    return True


def _page_dimensions(source: DrawingSource) -> tuple[float, float]:
    """Page width/height in source coordinate space (points or pixels).

    Mirrors ``_whole_page_rect``: PDF points for vector_pdf, raster_probe
    pixel size for raster sources.
    """
    if source.kind == "vector_pdf" and source.page_size_pt is not None:
        return float(source.page_size_pt[0]), float(source.page_size_pt[1])
    w, h = source.raster_probe.size
    return float(w), float(h)


def _scale_normalized_to_source(
    bbox: tuple[float, float, float, float],
    page_w: float,
    page_h: float,
) -> RectPt:
    """Scale a normalized [0, 1] bbox to the source's coord space."""
    x0, y0, x1, y1 = bbox
    return (x0 * page_w, y0 * page_h, x1 * page_w, y1 * page_h)


# Per-edge padding applied to VLM-returned normalized bboxes before
# scaling to source coords. Empirically the model under-estimates region
# extents — see commit message. Padding is conservative (3%) so it
# rarely overlaps neighbouring regions, but large enough to recover
# clipped edge-of-region content. Clamped to [0, 1] to stay on-page.
_VLM_BBOX_PAD_RATIO = 0.03


def _pad_normalized_bbox(
    bbox: tuple[float, float, float, float],
    ratio: float,
) -> tuple[float, float, float, float]:
    """Expand a normalized [0, 1] bbox by ``ratio`` of page dims on each side.

    Stays inside the page (clamped to [0, 1]) so a region near the page
    edge doesn't grow off-page. Width and height are scaled by the same
    ratio rather than by the bbox's own dimensions — the model's clipping
    error is page-relative, not bbox-relative.
    """
    x0, y0, x1, y1 = bbox
    return (
        max(0.0, x0 - ratio),
        max(0.0, y0 - ratio),
        min(1.0, x1 + ratio),
        min(1.0, y1 + ratio),
    )


def _bounding_rect(rects: list[RectPt]) -> RectPt | None:
    """Smallest axis-aligned rect enclosing every rect in ``rects``.

    Used to merge multiple VLM-returned legend blocks (symbols + abbr-
    eviations split across the page) into the single rect PageLayout
    expects. Returns None on empty input so callers can keep the field
    typed as ``RectPt | None``.
    """
    if not rects:
        return None
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[2] for r in rects)
    y1 = max(r[3] for r in rects)
    return (x0, y0, x1, y1)


# ── Geometry helpers (pixel ↔ source coords) ─────────────────────────────────


def _whole_page_rect(source: DrawingSource) -> RectPt:
    """Whole-page rect in source coordinate space (points or pixels)."""
    if source.kind == "vector_pdf" and source.page_size_pt is not None:
        w, h = source.page_size_pt
        return (0.0, 0.0, float(w), float(h))
    w, h = source.raster_probe.size
    return (0.0, 0.0, float(w), float(h))


def _whole_page_pixel_rect(probe: Image.Image) -> tuple[int, int, int, int]:
    """Whole-page rect in raster_probe pixel space — used for quadrant tests."""
    w, h = probe.size
    return (0, 0, w, h)


def _pixel_rect_to_source(
    rect_px: tuple[int, int, int, int], source: DrawingSource
) -> RectPt:
    """Convert a probe-pixel rect to source space.

    For vector_pdf, scales pixel coords back to PDF points using the probe
    DPI implied by raster_probe vs page_size_pt. For raster sources the
    pixel rect IS the source rect (RectPt is the same tuple type).
    """
    x0, y0, x1, y1 = rect_px
    if source.kind != "vector_pdf" or source.page_size_pt is None:
        return (float(x0), float(y0), float(x1), float(y1))
    pw, ph = source.raster_probe.size
    page_w, page_h = source.page_size_pt
    sx = page_w / pw
    sy = page_h / ph
    return (x0 * sx, y0 * sy, x1 * sx, y1 * sy)


def _source_rect_to_pixel(
    rect_pt: RectPt, source: DrawingSource
) -> tuple[int, int, int, int]:
    """Inverse of ``_pixel_rect_to_source`` — used for OCR containment tests
    (OCR matches are always in raster_probe pixel coords)."""
    x0, y0, x1, y1 = rect_pt
    if source.kind != "vector_pdf" or source.page_size_pt is None:
        return (int(x0), int(y0), int(x1), int(y1))
    pw, ph = source.raster_probe.size
    page_w, page_h = source.page_size_pt
    sx = pw / page_w
    sy = ph / page_h
    return (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))


# ── Hough-line rectangle decomposition ───────────────────────────────────────


def _decompose_into_rectangles(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Find candidate axis-aligned rectangles via Hough-line partitioning.

    Algorithm: detect long axis-aligned line segments via HoughLinesP, project
    them to the x or y axis to get a sorted list of "vertical splits" and
    "horizontal splits", then form a grid of candidate rectangles from the
    Cartesian product. Always include the whole-page rect itself so a sheet
    with no internal dividers still yields a candidate.

    This is intentionally simple — the v2 spec §5.3 calls for a Hough-line
    partition, not full geometry reconstruction. The classifier below picks
    out the rectangles whose contained OCR text matches a known keyword set.
    """
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    long_edge = max(h, w)

    edges = cv2.Canny(gray, 50, 150)
    min_line_length = max(int(long_edge * _HOUGH_MIN_LINE_LENGTH_FRACTION), 30)

    raw_lines = cv2.HoughLinesP(
        edges,
        _HOUGH_RHO,
        _HOUGH_THETA,
        _HOUGH_VOTE_THRESHOLD,
        minLineLength=min_line_length,
        maxLineGap=_HOUGH_MAX_LINE_GAP,
    )

    horizontal_ys: list[int] = [0, h]
    vertical_xs: list[int] = [0, w]
    if raw_lines is not None:
        for line in raw_lines:
            x1, y1, x2, y2 = line[0]
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            if dx == 0 and dy == 0:
                continue
            if dx == 0 or (dy / max(dx, 1)) > (1 / _AXIS_ALIGNED_SLOPE_TOL):
                # Vertical line.
                vertical_xs.append(int(round((x1 + x2) / 2)))
            elif dy == 0 or (dy / max(dx, 1)) < _AXIS_ALIGNED_SLOPE_TOL:
                # Horizontal line.
                horizontal_ys.append(int(round((y1 + y2) / 2)))

    horizontal_ys = _dedupe_close(sorted(horizontal_ys), tolerance=10)
    vertical_xs = _dedupe_close(sorted(vertical_xs), tolerance=10)

    min_w = int(w * _MIN_RECT_FRACTION)
    min_h = int(h * _MIN_RECT_FRACTION)

    rectangles: list[tuple[int, int, int, int]] = []
    for i in range(len(vertical_xs) - 1):
        for j in range(len(horizontal_ys) - 1):
            x0, x1 = vertical_xs[i], vertical_xs[i + 1]
            y0, y1 = horizontal_ys[j], horizontal_ys[j + 1]
            if (x1 - x0) < min_w or (y1 - y0) < min_h:
                continue
            rectangles.append((x0, y0, x1, y1))

    # Whole-page fallback: if Hough found no internal partitions the only
    # candidate is the whole sheet. Adding the whole-page rect when sub-
    # rectangles already exist would let it shadow them — its contained-OCR
    # blob is the union of every sub-rect's blob, so any keyword anywhere
    # on the page matches the whole-page rect too. The largest-by-area
    # tiebreak then always picks whole-page over the real sub-region.
    if not rectangles:
        rectangles.append((0, 0, w, h))

    return rectangles


def _dedupe_close(values: list[int], *, tolerance: int) -> list[int]:
    """Collapse near-duplicates from a sorted list of split points."""
    if not values:
        return []
    deduped = [values[0]]
    for v in values[1:]:
        if v - deduped[-1] > tolerance:
            deduped.append(v)
    return deduped


# ── Strip-merge (pre-classification Hough cleanup) ───────────────────────────


def _is_strip(rect: tuple[int, int, int, int], page_w: int, page_h: int) -> bool:
    """A rectangle qualifies as a strip if either:

      • its shorter dimension is below ``_STRIP_MIN_DIM_FRACTION * min(page)``
        — captures rects too narrow to plausibly be a functional region,
        regardless of page orientation, or
      • its aspect ratio exceeds ``_STRIP_ASPECT_RATIO`` — captures long-thin
        rects whose individual sides each clear the dimension threshold but
        which are still clearly artefacts (e.g. a 200×1500 sliver).

    Scaling against ``min(page)`` rather than ``max(page)`` is the natural
    measure: a 250-px-wide rect on a 595×841 portrait page is a real
    region (42% of page width), but the same 250-px width measured against
    the 841-tall axis would falsely flag it as narrow. Threshold values
    are tuned against the 5-drawing benchmark — see the module docstring
    in PR-3.6.
    """
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0
    if w <= 0 or h <= 0:
        return True
    short = min(w, h)
    long = max(w, h)
    short_page = min(page_w, page_h)
    if short < _STRIP_MIN_DIM_FRACTION * short_page:
        return True
    return (long / short) > _STRIP_ASPECT_RATIO


def _shared_edge_length(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> int:
    """Length of the shared boundary between two axis-aligned rectangles.

    Two rectangles share an edge when one of their sides lies on the same line
    AND their projection onto the perpendicular axis overlaps. Returns 0 for
    rectangles that touch only at a corner or do not touch at all. Overlapping
    interiors return the overlap length on whichever side is shared.
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b

    # Vertical shared edge — a's right edge meets b's left edge, or vice versa.
    # The horizontal overlap (y-axis) is what we measure.
    vertical_share = ax1 == bx0 or bx1 == ax0
    # Horizontal shared edge — a's bottom edge meets b's top edge, or vice versa.
    horizontal_share = ay1 == by0 or by1 == ay0

    if vertical_share:
        overlap = min(ay1, by1) - max(ay0, by0)
        return max(overlap, 0)
    if horizontal_share:
        overlap = min(ax1, bx1) - max(ax0, bx0)
        return max(overlap, 0)
    return 0


def _centre_distance(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> float:
    """Euclidean distance between two rectangle centres — fallback metric for
    isolated strips that share no edge with any neighbour."""
    ax = (a[0] + a[2]) / 2.0
    ay = (a[1] + a[3]) / 2.0
    bx = (b[0] + b[2]) / 2.0
    by = (b[1] + b[3]) / 2.0
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _bounding_rect_of_pair(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """Axis-aligned bounding rectangle of the union of two rectangles.

    Two non-aligned rects merge to their bounding rect, which may overshoot
    by introducing empty area; the alternative (concave union) is incompatible
    with RectPt and the downstream consumers — accept the overshoot.
    """
    return (
        min(a[0], b[0]),
        min(a[1], b[1]),
        max(a[2], b[2]),
        max(a[3], b[3]),
    )


def _merge_strips(
    rects: list[tuple[int, int, int, int]], page_w: int, page_h: int
) -> list[tuple[int, int, int, int]]:
    """Iteratively merge each strip into its longest-shared-edge neighbour.

    Each iteration is a sweep that pairs every strip with its best non-
    consumed partner (longest shared edge; centre-distance tie-break for
    isolated strips). The whole sweep commits at once, then we re-check —
    a merged rectangle may itself still be a strip. Convergence on real
    drawings typically happens in 1–3 sweeps because most strips are
    sub-cells of a larger functional region (a title block carved up by
    Hough divider lines) and re-absorb naturally.

    Termination: when no strip remains, when every rect is a strip (no
    valid absorption target), or when ``_STRIP_MERGE_MAX_ITERATIONS`` is
    hit. The cap is a guard against a pathological loop, not a feature.
    """
    if len(rects) <= 1:
        return list(rects)

    current = list(rects)
    for _ in range(_STRIP_MERGE_MAX_ITERATIONS):
        strip_indices = [
            i for i, r in enumerate(current) if _is_strip(r, page_w, page_h)
        ]
        if not strip_indices:
            return current

        # One sweep: each strip picks its best partner from the CURRENT list.
        # Picks are committed via a ``consumed`` set so two strips don't both
        # try to merge with the same neighbour in a single sweep.
        consumed: set[int] = set()
        merged_rects: list[tuple[int, int, int, int]] = []
        for strip_idx in strip_indices:
            if strip_idx in consumed:
                continue
            strip = current[strip_idx]
            best_idx = _best_neighbour(strip, strip_idx, current, consumed)
            if best_idx is None:
                # Every other rect already consumed this sweep; defer this
                # strip to the next iteration.
                continue
            consumed.add(strip_idx)
            consumed.add(best_idx)
            merged_rects.append(_bounding_rect_of_pair(strip, current[best_idx]))

        if not merged_rects:
            # No merge was possible this sweep (every strip's neighbours were
            # already taken). Returning prevents an infinite no-op loop.
            return current
        survivors = [r for i, r in enumerate(current) if i not in consumed]
        current = survivors + merged_rects

    # Hit the iteration cap — return whatever survives. Logged at the call
    # site via the "merged k strips" line; no warning here because the cap
    # is a guard against a pathological loop, not a normal termination path.
    return current


def _best_neighbour(
    strip: tuple[int, int, int, int],
    strip_idx: int,
    rects: list[tuple[int, int, int, int]],
    consumed: set[int],
) -> int | None:
    """Pick the neighbour to merge ``strip`` into.

    Primary key: longest shared edge. Tie-break (which also handles the all-
    zero "isolated strip" case): smallest centre distance. Returns ``None``
    only when every other rectangle has already been consumed this sweep.
    """
    best_idx: int | None = None
    best_edge = -1
    best_dist = float("inf")
    for j, other in enumerate(rects):
        if j == strip_idx or j in consumed:
            continue
        edge = _shared_edge_length(strip, other)
        dist = _centre_distance(strip, other)
        if edge > best_edge or (edge == best_edge and dist < best_dist):
            best_edge = edge
            best_dist = dist
            best_idx = j
    return best_idx


# ── OCR / keyword classification ─────────────────────────────────────────────


def _matches_in_rect(
    matches: Iterable[OCRMatch], rect_px: tuple[int, int, int, int]
) -> list[OCRMatch]:
    """Return matches whose top-left lies inside the rectangle.

    OCRMatch.bbox is (x, y, w, h) per ``app.ocr.base.Bbox``. We use top-left
    containment rather than full-bbox intersection because Hough partitions
    can clip matches at borders; top-left is a stable single test.
    """
    x0, y0, x1, y1 = rect_px
    contained: list[OCRMatch] = []
    for m in matches:
        mx, my, _, _ = m.bbox
        if x0 <= mx < x1 and y0 <= my < y1:
            contained.append(m)
    return contained


def _text_blob_contains_keyword(text_blob: str, keyword: str) -> bool:
    """Whitespace-bounded keyword match against a contained-OCR text blob.

    Used by the legend keyword check so "LEGEND" does not false-match
    "LEGENDARY" and "DESCRIPTION" does not false-match "MISDESCRIPTIONS".
    Multi-word keywords like "LEGEND OF SYMBOLS" must appear as that
    contiguous phrase. Both inputs are upper-cased by the caller already.
    """
    pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
    return re.search(pattern, text_blob) is not None


def _classify_by_keywords(
    text_blob: str,
    rect_px: tuple[int, int, int, int],
    page_pixel_rect: tuple[int, int, int, int],
) -> str:
    """Map a rectangle's contained text to a region kind.

    Order matters — the most specific keywords win. Title-block detection
    layers a position constraint (lower-right quadrant) on top of its keyword
    set because PROJECT/DATE/SCALE words occur in legends and notes too.
    Returns ``unknown`` if no keyword matches.

    Legend keywords use word-boundary matching (see
    ``_text_blob_contains_keyword``) because the widened set includes generic
    English words like "DESCRIPTION" — substring matching against those
    would false-match too much surrounding text. The other keyword sets stay
    on substring matching: "FLOOR PLAN" must still classify as plan_view via
    the "PLAN" keyword, "GENERAL NOTES" via "NOTES", etc.
    """
    if any(_text_blob_contains_keyword(text_blob, k) for k in _LEGEND_KEYWORDS):
        return "legend"
    if any(k in text_blob for k in _NOTES_KEYWORDS):
        return "notes"
    if any(k in text_blob for k in _SCHEDULE_KEYWORDS):
        return "schedule"
    if any(k in text_blob for k in _PLAN_VIEW_KEYWORDS):
        return "plan_view"
    if _is_lower_right(rect_px, page_pixel_rect) and any(
        k in text_blob for k in _TITLE_BLOCK_KEYWORDS
    ):
        return "title_block"
    return "unknown"


def _is_lower_right(
    rect_px: tuple[int, int, int, int],
    page_pixel_rect: tuple[int, int, int, int],
) -> bool:
    """Title-block heuristic: rectangle centre sits in the lower-right quadrant.

    Centre rather than top-left because Hough partitions yield rects that
    span half the sheet — a left-half rect with title-block-like text would
    have a top-left at (0,0) and never qualify by the strict top-left rule.
    The centre-based check folds half-rects into their dominant quadrant.
    """
    rx0, ry0, rx1, ry1 = rect_px
    _, _, pw, ph = page_pixel_rect
    cx = (rx0 + rx1) / 2.0
    cy = (ry0 + ry1) / 2.0
    return (
        cx >= pw * _TITLE_BLOCK_QUADRANT_FRACTION
        and cy >= ph * _TITLE_BLOCK_QUADRANT_FRACTION
    )


# Containment thresholds for the "smaller better" tie-break in
# ``_select_plan_view``. A parent must be strictly larger than the child by
# this area ratio (so two rects of nearly identical size don't pretend to
# nest), and the child's outer boundary must lie inside the parent's outer
# boundary with at most this much slop on every side. The slop is expressed
# as a fraction of the PARENT's side length on that axis — small Hough
# rounding errors at shared dividers can push a child a few pixels past the
# parent edge without it actually escaping the region.
_PLAN_VIEW_PARENT_AREA_RATIO = 1.5
_PLAN_VIEW_CONTAINMENT_SLOP = 0.05


def _is_contained(child: RectPt, parent: RectPt) -> bool:
    """True if ``child`` lies inside ``parent`` with ≤ 5% boundary slop."""
    cx0, cy0, cx1, cy1 = child
    px0, py0, px1, py1 = parent
    pw = max(px1 - px0, 0.0)
    ph = max(py1 - py0, 0.0)
    if pw <= 0 or ph <= 0:
        return False
    slop_x = pw * _PLAN_VIEW_CONTAINMENT_SLOP
    slop_y = ph * _PLAN_VIEW_CONTAINMENT_SLOP
    return (
        cx0 >= px0 - slop_x
        and cy0 >= py0 - slop_y
        and cx1 <= px1 + slop_x
        and cy1 <= py1 + slop_y
    )


def _select_plan_view(
    candidates: list[RectPt], ctx: PipelineContext
) -> RectPt | None:
    """Pick the plan-view rect from a list of plan-view candidates.

    Selection rules:

      • Zero candidates → ``None`` (caller falls back to whole-page per §7).
      • One candidate → that candidate.
      • Multiple candidates with a containment relationship → the deepest
        child (smaller is better). This fixes the "outer rect with HVAC
        LAYOUT title" anti-pattern: a page-wide rect that contains the
        title-bar text "HVAC LAYOUT" picks up plan_view classification
        even though the actual plan view is the inset rect, which also
        contains those keywords. Without this tie-break the largest-by-
        area rule picked the outer (page-wide) rect and the legend +
        title + heading stayed inside ``plan_view``, defeating §5.3's
        reason for existing.
      • Multiple candidates with NO containment relationship (e.g. side-by-
        side multi-plan-view sheet) → largest by area, with the
        ``multi_plan_view_detected`` warning. This preserves V2 §7's
        documented behaviour.

    A "containment relationship" requires both: parent area > 1.5× child
    area AND child geometrically inside parent with ≤ 5% boundary slop.
    The area ratio gate prevents two near-equal rects from triggering the
    smaller-better path on noise.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # Build a containment graph: ``children[i]`` lists the indices of every
    # rect that is strictly contained inside candidates[i] (with the area
    # ratio + slop constraints above). The deepest child in this DAG is
    # the rect we want — it's the most-inset rectangle that still picked up
    # plan_view keywords, and it cannot itself contain any other plan_view
    # candidate.
    children: dict[int, list[int]] = {i: [] for i in range(len(candidates))}
    has_parent: set[int] = set()
    for i, parent in enumerate(candidates):
        for j, child in enumerate(candidates):
            if i == j:
                continue
            if (
                _rect_area(parent) > _PLAN_VIEW_PARENT_AREA_RATIO * _rect_area(child)
                and _is_contained(child, parent)
            ):
                children[i].append(j)
                has_parent.add(j)

    any_containment = any(children[i] for i in children)
    if not any_containment:
        # No nesting at all → side-by-side multi-plan-view sheet. Preserve
        # V2 §7 behaviour: largest by area + warn.
        ctx.errors.append("multi_plan_view_detected")
        picked = max(candidates, key=_rect_area)
        logger.info(
            "page_categorize: picked largest of %d plan_view candidate(s) "
            "(no containment relation)",
            len(candidates),
        )
        return picked

    # Containment exists. Pick the deepest child — i.e. a rect that is
    # contained by something AND contains nothing itself in the candidate
    # list. If multiple leaves exist (the containment DAG branches),
    # smallest by area wins — that's the most-inset region and the safest
    # plan_view. Don't fire the multi-plan-view warning here: nested
    # candidates are an artefact of one real plan view inside an outer
    # shell, not two real plan views.
    leaves = [i for i in children if not children[i] and i in has_parent]
    if not leaves:
        # Pathological: every candidate has a child but also a parent.
        # Should not occur for axis-aligned rects (DAG must have a leaf),
        # but defend with the largest-by-area fallback.
        return max(candidates, key=_rect_area)
    picked_idx = min(leaves, key=lambda i: _rect_area(candidates[i]))
    logger.info(
        "page_categorize: picked nested plan_view (%d candidates, %d leaves)",
        len(candidates),
        len(leaves),
    )
    return candidates[picked_idx]


def _rect_area(rect: RectPt) -> float:
    x0, y0, x1, y1 = rect
    return max(x1 - x0, 0.0) * max(y1 - y0, 0.0)


