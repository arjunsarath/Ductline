"""Stage 5 — Tiled Duct Detection (SOLUTION-DESIGN-V2 §5.5, ADR-0008).

Replaces v1's single-shot full-sheet VLM call with per-tile calls on the
``plan_view`` region only. Each tile is rendered directly from the source at a
per-tile DPI driven by the smallest text in the drawing (V2 §5.2), and the VLM
prompt carries a *trail context* — segments already detected in tiles to the
left in this row + tiles in earlier rows. The stage stitches per-tile bboxes
back into source coordinate space and dedupes cross-tile overlaps by IoU.

Locked decisions (SOLUTION-DESIGN-V2 §5.5, ADR-0008):

  • Tile size — 1100 px square at the per-tile rendered DPI. Matches
    llama3.2-vision's ~1120 px native input window. Configurable via
    ``__init__``.

  • Overlap — 15% of the tile dimension. Configurable. Large enough that any
    duct > 150 px appears whole in at least one tile.

  • Per-tile DPI (vector_pdf) — pulled from
    ``DrawingSource.smart_dpi_for_rect(plan_view, ocr_cache=ctx.ocr_cache)``.
    A return of 0 (raster source) or the absence of an OCR cache falls back to
    a fixed 200 DPI for vector and the native ``raster_probe`` pixel space for
    raster sources (no re-render).

  • Tile rect math (vector_pdf) — tiles are computed in PDF points by inverting
    the per-tile DPI: ``tile_size_pt = tile_px * 72 / dpi``. Boundary tiles are
    clamped to the plan_view rect rather than allowed to overshoot.

  • Tile rect math (raster) — tiles are pixel-space rectangles of size
    ``tile_px`` with the same overlap ratio. DPI is unused.

  • Stitching — per-tile bboxes are tile-normalized [0, 1]; we project to
    source space using each tile's rect:
    ``x_src = tile_rect.x0 + bbox_norm.x0 * tile_rect.width``.

  • Dedup — IoU > 0.4. When two stitched bboxes overlap above threshold, KEEP
    the one whose tile-normalized bbox sits closer to its OWN tile centre. The
    IoU threshold is the V2 §9 Q2 starting point — tuning is deferred to a
    follow-up benchmark PR.

  • Segment IDs — initially ``DUCT-{row}-{col}-{idx}`` per tile, then
    re-numbered globally to ``DUCT-{1..N}`` after dedup so the output is
    stable regardless of tile traversal order.

  • Failure posture — per-tile VLM failures are skipped (other tiles continue,
    error appended); whole-stage exceptions reset ``ctx.segments_draft = []``
    and append a single ``tiled_detect: <reason>`` to ``ctx.errors``. Mirrors
    ``page_categorize`` and ``probe_ocr``.

  • This stage is read-only with respect to ``ctx.layout`` and ``ctx.legend``.
    The categorizer always populates a ``plan_view`` (whole-page on fallback),
    so the None guard is defensive.

  • Output goes to ``ctx.segments_draft`` — the same field v1's
    ``DuctDetectionStage`` populates. The runner swap is a supervisor concern.
"""

from __future__ import annotations

import logging
import math

import cv2
import numpy as np
from PIL.Image import Image as PILImage

from app.config import settings
from app.pipeline.base import PipelineContext, PipelineStage, VLMSegmentDraft
from app.schemas import Geometry, ReasoningStep
from app.source.base import DrawingSource, RectPt
from app.vlm.base import VLMClient
from app.vlm.tools import VLMSegment

logger = logging.getLogger(__name__)

# IoU threshold above which two stitched bboxes are treated as duplicates of
# the same physical duct (SOLUTION-DESIGN-V2 §5.5, §9 Q2). 0.4 is the V2
# starting guess — tune in a follow-up benchmark PR, not here.
_IOU_DEDUP_THRESHOLD = 0.4

# Fallback DPI for vector_pdf tiles when smart_dpi_for_rect returns 0 or the
# OCR cache is unavailable. Matches the v1 raster_dpi default — a known-good
# DPI for the drawing types in our benchmark set.
_VECTOR_FALLBACK_DPI = 200

# 72 points per inch — the PDF point system. Multiplying ``tile_px / dpi``
# converts a pixel target to a point-space rect side length.
_PT_PER_INCH = 72

# Empty-tile skip: tiles whose Canny edge density is below this threshold are
# almost entirely background (white space, page margins, or sparse column
# markers). Calling the VLM on them invites hallucination — the model will
# fabricate ducts from the few stray lines it can see — and wastes a slow
# inference call. 0.005 = 0.5% of pixels are edges. Real plan-view content
# sits at 5-15%; column-marker-only strips sit below 0.5%.
_EMPTY_TILE_EDGE_DENSITY_THRESHOLD = 0.005


class TiledDuctDetectionStage(PipelineStage):
    name = "duct_detect_tiled"

    def __init__(
        self,
        vlm: VLMClient,
        *,
        tile_px: int = 1100,
        overlap_pct: float = 0.15,
    ) -> None:
        self._vlm = vlm
        self._tile_px = tile_px
        self._overlap_pct = overlap_pct

    def run(self, ctx: PipelineContext) -> PipelineContext:
        try:
            ctx.segments_draft = self._build_drafts(ctx)
        except Exception as exc:  # noqa: BLE001 — degradation by design (§5.5)
            logger.exception("tiled_detect failed")
            ctx.segments_draft = []
            ctx.errors.append(f"tiled_detect: {exc}")
        return ctx

    # ── Top-level build ──────────────────────────────────────────────────────

    def _build_drafts(self, ctx: PipelineContext) -> list[VLMSegmentDraft]:
        assert ctx.source is not None, "ingest must run before tiled_detect"

        # Defensive — categorizer always populates plan_view (whole-page on
        # fallback per V2 §7), so this branch is the "categorizer degraded
        # entirely" case.
        if ctx.layout is None or ctx.layout.plan_view is None:
            logger.info(
                "tiled_detect: no plan_view available; emitting empty draft list"
            )
            return []

        plan_view = ctx.layout.plan_view
        dpi = _resolve_per_tile_dpi(ctx.source, plan_view, ctx)

        tiles = _compute_tiles(
            plan_view,
            source_kind=ctx.source.kind,
            dpi=dpi,
            tile_px=self._tile_px,
            overlap_pct=self._overlap_pct,
        )
        logger.info(
            "tiled_detect: plan_view=%s dpi=%d tiles=%d (rows=%d cols=%d)",
            tuple(round(v, 1) for v in plan_view),
            dpi,
            len(tiles),
            tiles[-1][3] if tiles else 0,
            tiles[-1][4] if tiles else 0,
        )

        # HITL gate: pause before any VLM call so the user can confirm the
        # tile grid (size / DPI / count). Skipped on the test path
        # (ctx.approval_gate is None). On timeout the run aborts with an
        # empty draft list rather than silently calling 40+ VLM tiles.
        if ctx.approval_gate is not None and tiles:
            payload = {
                "drawing_id": ctx.drawing_id,
                "plan_view": list(plan_view),
                "dpi": dpi,
                "tile_px": self._tile_px,
                "overlap_pct": self._overlap_pct,
                "tile_count": len(tiles),
                "tiles": [
                    {
                        "rect": list(tile_rect),
                        "row": row,
                        "col": col,
                        "total_rows": total_rows,
                        "total_cols": total_cols,
                    }
                    for tile_rect, row, col, total_rows, total_cols in tiles
                ],
            }
            # Tiling gate has no inline-correction UI; the dict body is
            # ignored on approval. ``None`` means timeout / cancel — abort
            # the tile loop with an error rather than silently continuing.
            # An empty corrections dict still counts as approval, so the
            # check explicitly compares to None rather than relying on
            # truthiness (an empty dict is falsy).
            if ctx.approval_gate("tiling", payload) is None:
                logger.warning(
                    "tiled_detect: tiling gate timed out; aborting tile loop"
                )
                ctx.errors.append("approval timeout: tiling gate not approved")
                return []

        # Per-tile call. We track results in tile order so trail context is
        # built from already-processed neighbours; see _build_trail_context.
        processed_by_tile: dict[tuple[int, int], list[_StitchedSegment]] = {}
        for tile_index, (tile_rect, row, col, total_rows, total_cols) in enumerate(tiles, start=1):
            if ctx.progress is not None:
                ctx.progress("tile_start", {
                    "stage": "duct_detect_tiled",
                    "row": row,
                    "col": col,
                    "current": tile_index,
                    "total": len(tiles),
                })
            trail = _build_trail_context(
                processed_by_tile, row, col, tile_rect
            )
            stitched = self._call_tile(
                ctx,
                tile_rect=tile_rect,
                row=row,
                col=col,
                total_rows=total_rows,
                total_cols=total_cols,
                trail=trail,
                dpi=dpi,
            )
            processed_by_tile[(row, col)] = stitched
            if ctx.progress is not None:
                ctx.progress("tile_done", {
                    "stage": "duct_detect_tiled",
                    "row": row,
                    "col": col,
                    "current": tile_index,
                    "total": len(tiles),
                    "segments_found": len(stitched),
                })

        # Flatten and dedup across tiles.
        all_segments: list[_StitchedSegment] = [
            seg for segs in processed_by_tile.values() for seg in segs
        ]
        deduped = _dedup_by_iou(all_segments, threshold=_IOU_DEDUP_THRESHOLD)
        logger.info(
            "tiled_detect: stitched %d segments from %d tiles (dedup removed %d)",
            len(deduped),
            len(tiles),
            len(all_segments) - len(deduped),
        )

        return _to_drafts(deduped)

    # ── Per-tile call ────────────────────────────────────────────────────────

    def _call_tile(
        self,
        ctx: PipelineContext,
        *,
        tile_rect: RectPt,
        row: int,
        col: int,
        total_rows: int,
        total_cols: int,
        trail: list[dict],
        dpi: int,
    ) -> list[_StitchedSegment]:
        """One VLM call on one tile crop. Errors degrade to "skip this tile"."""
        assert ctx.source is not None
        try:
            crop = ctx.source.render(tile_rect, dpi=dpi)
        except Exception as exc:  # noqa: BLE001 — render failure is per-tile only
            logger.warning(
                "tiled_detect: render failed for tile (%d,%d): %s", row, col, exc
            )
            ctx.errors.append(
                f"tiled_detect: tile ({row},{col}) failed: render error: {exc}"
            )
            return []

        # Empty-tile skip: tiles covering page margins / column-header strips
        # are mostly white. Calling the VLM on them invites hallucination
        # (the model fabricates ducts from a handful of column-marker lines)
        # and burns ~10s per call. Skip without invoking the model.
        edge_density = _tile_edge_density(crop)
        if edge_density < _EMPTY_TILE_EDGE_DENSITY_THRESHOLD:
            logger.info(
                "tiled_detect: skipping empty tile (%d,%d) edge_density=%.4f rect=%s",
                row, col, edge_density,
                tuple(round(v, 1) for v in tile_rect),
            )
            return []

        try:
            response = self._vlm.detect_tile(
                crop,
                tile_position=(row, col, total_rows, total_cols),
                trail_context=trail,
                legend=ctx.legend,
            )
        except Exception as exc:  # noqa: BLE001 — VLM failure is per-tile only
            logger.warning(
                "tiled_detect: VLM failed for tile (%d,%d): %s", row, col, exc
            )
            ctx.errors.append(
                f"tiled_detect: tile ({row},{col}) failed: {exc}"
            )
            return []

        stitched = _project_segments_to_source(
            response.segments, tile_rect, row, col
        )
        logger.info(
            "tiled_detect: tile (%d,%d) → %d segments after stitching",
            row, col, len(stitched),
        )
        return stitched


# ── Tile geometry ────────────────────────────────────────────────────────────


def _compute_tiles(
    plan_view: RectPt,
    *,
    source_kind: str,
    dpi: int,
    tile_px: int,
    overlap_pct: float,
) -> list[tuple[RectPt, int, int, int, int]]:
    """Compute axis-aligned tile rects covering ``plan_view``.

    For vector PDFs the tile size in PDF points is derived from the per-tile
    DPI: ``tile_px / dpi`` inches × 72 = points. For raster sources tiles are
    pixel-space rectangles of size ``tile_px`` (DPI is unused — re-render is
    unavailable for raster sources).

    Tiles are computed left-to-right, top-to-bottom (row-major). Boundary
    tiles are clamped to ``plan_view`` so we never request a render outside
    the plan view rect.

    Returns a list of ``(rect, row, col, total_rows, total_cols)`` tuples.
    """
    x0, y0, x1, y1 = plan_view
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        return []

    if source_kind == "vector_pdf":
        # PDF points per tile side: tile_px pixels at the per-tile DPI, in inches,
        # × 72 points/inch.
        tile_size = (tile_px / max(dpi, 1)) * _PT_PER_INCH
    else:
        # Raster — DPI is unused; tile_px IS the side length in pixels.
        tile_size = float(tile_px)

    overlap = tile_size * overlap_pct
    step = tile_size - overlap
    if step <= 0:
        # Degenerate config — overlap >= 100%. Treat the whole plan_view as a
        # single tile rather than loop forever.
        return [(plan_view, 0, 0, 1, 1)]

    total_cols = max(1, math.ceil((width - overlap) / step)) if width > tile_size else 1
    total_rows = max(1, math.ceil((height - overlap) / step)) if height > tile_size else 1

    tiles: list[tuple[RectPt, int, int, int, int]] = []
    for row in range(total_rows):
        for col in range(total_cols):
            tx0 = x0 + col * step
            ty0 = y0 + row * step
            tx1 = min(tx0 + tile_size, x1)
            ty1 = min(ty0 + tile_size, y1)
            # Clamp the leading edge too — at the right/bottom boundary the
            # final tile may push past the plan_view. We accept the slightly
            # smaller tile (it still overlaps the previous one).
            if tx1 - tx0 <= 0 or ty1 - ty0 <= 0:
                continue
            tiles.append(((tx0, ty0, tx1, ty1), row, col, total_rows, total_cols))
    return tiles


def _tile_edge_density(crop: PILImage) -> float:
    """Fraction of pixels in ``crop`` that are Canny edges.

    Used as an empty-tile pre-filter before the VLM call. Tiles covering
    page margins / column-header strips have edge density well below 0.5%;
    real plan-view content sits at 5-15%. The threshold lives in
    ``_EMPTY_TILE_EDGE_DENSITY_THRESHOLD``. Pure helper (no side effects)
    so unit tests can hit it directly.
    """
    arr = np.asarray(crop.convert("L"))
    if arr.size == 0:
        return 0.0
    edges = cv2.Canny(arr, threshold1=50, threshold2=150)
    return float(np.count_nonzero(edges)) / float(edges.size)


def _resolve_per_tile_dpi(
    source: DrawingSource, plan_view: RectPt, ctx: PipelineContext
) -> int:
    """Resolve the DPI to render each tile at.

    For vector_pdf, defer to ``smart_dpi_for_rect`` when the OCR cache is
    available; otherwise use the fixed vector fallback. For raster sources,
    DPI is structurally unused (``DrawingSource.render`` ignores it) — we
    return the v1 raster_dpi for logging visibility.
    """
    if source.kind == "vector_pdf":
        if ctx.ocr_cache is None:
            return _VECTOR_FALLBACK_DPI
        smart = source.smart_dpi_for_rect(plan_view, ocr_cache=ctx.ocr_cache)
        return smart if smart > 0 else _VECTOR_FALLBACK_DPI
    return settings.raster_dpi


# ── Stitching: tile-normalized bbox → source space ───────────────────────────


def _project_bbox_to_source(
    bbox_norm: tuple[float, float, float, float], tile_rect: RectPt
) -> RectPt:
    """Project a tile-normalized bbox into the source coordinate system.

    ``bbox_norm`` is [0, 1] in the tile's own frame; ``tile_rect`` carries the
    tile's extent in source space (PDF points or pixels). Coordinates are
    clamped to the tile rect — a model that emitted slightly out-of-range
    norms shouldn't produce a bbox outside the tile.
    """
    nx0, ny0, nx1, ny1 = bbox_norm
    # Defensive clamp before scaling — the model occasionally emits values
    # outside [0, 1].
    nx0 = max(0.0, min(1.0, nx0))
    ny0 = max(0.0, min(1.0, ny0))
    nx1 = max(0.0, min(1.0, nx1))
    ny1 = max(0.0, min(1.0, ny1))

    tx0, ty0, tx1, ty1 = tile_rect
    tw = tx1 - tx0
    th = ty1 - ty0
    return (
        tx0 + nx0 * tw,
        ty0 + ny0 * th,
        tx0 + nx1 * tw,
        ty0 + ny1 * th,
    )


def _project_segments_to_source(
    segments: list[VLMSegment],
    tile_rect: RectPt,
    row: int,
    col: int,
) -> list[_StitchedSegment]:
    """Project per-tile VLM output into source space + carry forward metadata.

    Each segment is wrapped with the source-space rect, the tile centre
    distance (used for the dedup tiebreaker), and the original tile
    coordinates so the global re-numbering pass can produce a deterministic
    segment_id.
    """
    stitched: list[_StitchedSegment] = []
    for idx, seg in enumerate(segments):
        bbox_norm = tuple(float(v) for v in seg.bbox)
        if len(bbox_norm) != 4:
            continue
        rect_src = _project_bbox_to_source(bbox_norm, tile_rect)  # type: ignore[arg-type]
        cx = (bbox_norm[0] + bbox_norm[2]) / 2.0
        cy = (bbox_norm[1] + bbox_norm[3]) / 2.0
        # Distance from tile centre (0.5, 0.5) — smaller is "more central",
        # which the dedup tiebreaker prefers.
        centre_dist = math.hypot(cx - 0.5, cy - 0.5)
        stitched.append(
            _StitchedSegment(
                segment_id=f"DUCT-{row}-{col}-{idx}",
                rect=rect_src,
                shape_hint=seg.shape_hint,
                nearby_text=list(seg.nearby_text),
                tile_centre_dist=centre_dist,
                row=row,
                col=col,
            )
        )
    return stitched


# ── Trail context ────────────────────────────────────────────────────────────


def _build_trail_context(
    processed_by_tile: dict[tuple[int, int], list[_StitchedSegment]],
    current_row: int,
    current_col: int,
    current_tile_rect: RectPt,
) -> list[dict]:
    """Build the trail-context entries for the tile being processed.

    "Trail" = tiles to the LEFT in the same row + tiles in PREVIOUS rows. We
    do NOT include the current tile or any future tile. Each entry's bbox is
    projected from source space back into the CURRENT tile's normalized [0, 1]
    coords so the model can locate already-detected segments in its frame
    without coordinate-system gymnastics.
    """
    entries: list[dict] = []
    cx0, cy0, cx1, cy1 = current_tile_rect
    cw = max(cx1 - cx0, 1e-6)
    ch = max(cy1 - cy0, 1e-6)

    for (r, c), segments in processed_by_tile.items():
        if r > current_row:
            continue
        if r == current_row and c >= current_col:
            continue
        for seg in segments:
            sx0, sy0, sx1, sy1 = seg.rect
            # Project to current tile's normalized frame; clamp to [0, 1] so
            # segments outside the current tile collapse to its border (still
            # informative — "duct ends near this edge" — but never emit
            # coordinates outside [0, 1]).
            nx0 = max(0.0, min(1.0, (sx0 - cx0) / cw))
            ny0 = max(0.0, min(1.0, (sy0 - cy0) / ch))
            nx1 = max(0.0, min(1.0, (sx1 - cx0) / cw))
            ny1 = max(0.0, min(1.0, (sy1 - cy0) / ch))
            # Skip entries that collapse to a zero-area point inside the
            # current tile — they carry no useful spatial signal.
            if nx0 == nx1 or ny0 == ny1:
                continue
            entries.append(
                {
                    "bbox_normalized": (nx0, ny0, nx1, ny1),
                    "shape_hint": seg.shape_hint,
                }
            )
    return entries


# ── IoU dedup ────────────────────────────────────────────────────────────────


def _iou(a: RectPt, b: RectPt) -> float:
    """Intersection-over-union for two axis-aligned rects."""
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


def _dedup_by_iou(
    segments: list[_StitchedSegment],
    *,
    threshold: float,
) -> list[_StitchedSegment]:
    """Greedy IoU dedup. When two segments overlap above threshold, keep the
    one whose tile-normalized bbox is more central to its OWN tile.

    Greedy O(n²) is acceptable here: typical drawings produce 5–50 segments
    after stitching, well below any threshold where we'd need a spatial
    index. The traversal order is sorted by ``tile_centre_dist`` so the
    "most central" segment encountered first wins ties cleanly — its
    duplicates are skipped on subsequent visits.
    """
    if not segments:
        return []

    # Sort so the most central (smallest dist) segment is considered first.
    ordered = sorted(segments, key=lambda s: s.tile_centre_dist)
    survivors: list[_StitchedSegment] = []
    for candidate in ordered:
        is_duplicate = False
        for kept in survivors:
            if _iou(candidate.rect, kept.rect) > threshold:
                # Already kept a more-central segment for this duct.
                is_duplicate = True
                break
        if not is_duplicate:
            survivors.append(candidate)
    return survivors


# ── Output assembly ──────────────────────────────────────────────────────────


def _to_drafts(stitched: list[_StitchedSegment]) -> list[VLMSegmentDraft]:
    """Convert stitched segments into ``VLMSegmentDraft`` with stable IDs.

    Re-number to ``DUCT-1..N`` in source-space row-major order so the output
    is independent of which tile produced each segment. Sorting by ``y0`` then
    ``x0`` (source space) gives a deterministic top-to-bottom, left-to-right
    sequence regardless of dedup order.
    """
    ordered = sorted(stitched, key=lambda s: (s.rect[1], s.rect[0]))
    drafts: list[VLMSegmentDraft] = []
    for index, seg in enumerate(ordered, start=1):
        x0, y0, x1, y1 = seg.rect
        geometry = Geometry(
            type="bbox",
            points=[(float(x0), float(y0)), (float(x1), float(y1))],
        )
        drafts.append(
            VLMSegmentDraft(
                segment_id=f"DUCT-{index}",
                geometry=geometry,
                shape_hint=seg.shape_hint,
                nearby_text=list(seg.nearby_text),
                reasoning_trace=[
                    ReasoningStep(
                        stage="vlm_detect_tile",
                        evidence=(
                            f"tile ({seg.row},{seg.col}) detected a {seg.shape_hint} "
                            f"duct at source bbox "
                            f"({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f})"
                        ),
                    )
                ],
            )
        )
    return drafts


# ── Internal record types ────────────────────────────────────────────────────


class _StitchedSegment:
    """Pre-draft record carrying everything dedup + final assembly need.

    Not a dataclass / pydantic model — kept as a thin internal container so
    the public surface of this module stays the stage class + helpers, with
    the stitching record private to its only call sites.
    """

    __slots__ = (
        "col",
        "nearby_text",
        "rect",
        "row",
        "segment_id",
        "shape_hint",
        "tile_centre_dist",
    )

    def __init__(
        self,
        *,
        segment_id: str,
        rect: RectPt,
        shape_hint: str,
        nearby_text: list[str],
        tile_centre_dist: float,
        row: int,
        col: int,
    ) -> None:
        self.segment_id = segment_id
        self.rect = rect
        self.shape_hint = shape_hint
        self.nearby_text = nearby_text
        self.tile_centre_dist = tile_centre_dist
        self.row = row
        self.col = col
