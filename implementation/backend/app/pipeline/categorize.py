"""Stage 3 — Page Categorizer (SOLUTION-DESIGN-V2 §5.3).

Decomposes the drawing into named regions (title block, schedule, legend,
notes, plan view) so downstream tiled detect (§5.5) only runs on plan-view
geometry. Algorithmic-first — Hough-line decomposition + OCR keyword match
handles the common case; the VLM is called only on rectangles the algorithm
leaves as ``unknown``.

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
from collections.abc import Iterable

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
_PLAN_VIEW_KEYWORDS = ("PLAN", "LEVEL", "FLOOR", "MECHANICAL PLAN")
_LEGEND_KEYWORDS = ("LEGEND",)
_NOTES_KEYWORDS = ("NOTES", "GENERAL NOTES")
_SCHEDULE_KEYWORDS = ("SCHEDULE",)
_TITLE_BLOCK_KEYWORDS = ("PROJECT", "DRAWN BY", "DATE", "SCALE")
# Fraction of the page below/right of which a rectangle is considered to live
# in the lower-right quadrant — the conventional title-block location.
_TITLE_BLOCK_QUADRANT_FRACTION = 0.5


class PageCategorizerStage(PipelineStage):
    name = "page_categorize"

    def __init__(self, vlm: VLMClient) -> None:
        # Per SOLUTION-DESIGN-V2 §6.1: stages take engines/clients only.
        # OCR cache is read at run() time from ctx, never injected here.
        self._vlm = vlm

    def run(self, ctx: PipelineContext) -> PipelineContext:
        try:
            ctx.layout = self._build_layout(ctx)
        except Exception as exc:  # noqa: BLE001 — degradation by design (§7)
            logger.exception("page_categorize failed")
            ctx.layout = None
            ctx.errors.append(f"page_categorize: {exc}")
        return ctx

    # ── Top-level layout build ───────────────────────────────────────────────

    def _build_layout(self, ctx: PipelineContext) -> PageLayout:
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
        if not rectangles_px:
            ctx.errors.append(
                "categorizer_failed: no plan view identified (no candidate rectangles)"
            )
            return PageLayout(plan_view=whole_page_rect)

        # Translate pixel rects to source coordinate space (points for vector,
        # passthrough for raster) and classify each one.
        candidates = [_pixel_rect_to_source(rect, ctx.source) for rect in rectangles_px]

        plan_view, named_regions = self._classify_rectangles(
            candidates, ctx.ocr_cache.matches, ctx
        )

        if plan_view is None:
            ctx.errors.append("categorizer_failed: no plan view identified")
            plan_view = whole_page_rect
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
            if kind == "unknown":
                kind = self._vlm_categorize(rect_pt, ctx.source)

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

        plan_view = _pick_largest_plan_view(plan_views, ctx)
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
    """
    if any(k in text_blob for k in _LEGEND_KEYWORDS):
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


def _pick_largest_plan_view(
    plan_views: list[RectPt], ctx: PipelineContext
) -> RectPt | None:
    """Pick the largest plan-view rect by area; warn if more than one."""
    if not plan_views:
        return None
    if len(plan_views) > 1:
        ctx.errors.append("multi_plan_view_detected")
    return max(plan_views, key=_rect_area)


def _rect_area(rect: RectPt) -> float:
    x0, y0, x1, y1 = rect
    return max(x1 - x0, 0.0) * max(y1 - y0, 0.0)


