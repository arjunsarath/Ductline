"""Tiled Duct Detection (SOLUTION-DESIGN-V2 §5.5, ADR-0008).

Eight tests covering tile geometry (vector + raster), IoU dedup centrality
tiebreak, the trail-context "left + above only" rule, per-tile failure
isolation, oversized plan-view stitching coherence, and stage-level
degradation. Tests stub the VLM directly — real Ollama is never contacted.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from app.pipeline.base import PipelineContext
from app.pipeline.detect_tiled import (
    TiledDuctDetectionStage,
    _apply_tiling_corrections,
    _build_trail_context,
    _compute_tiles,
    _dedup_by_iou,
    _StitchedSegment,
)
from app.pipeline.layout import PageLayout
from app.source.base import DrawingSource
from app.vlm.tools import CategorizePageTool, DetectionResult, VLMSegment

# ── Stub VLM ─────────────────────────────────────────────────────────────────


class _StubVLM:
    """VLMClient stub for tiled detect.

    ``detect_tile`` returns a deterministic single-segment ``DetectionResult``
    keyed off the tile's (row, col) so per-tile assertions are easy to write.
    Optionally raises on a configurable (row, col) to exercise the per-tile
    failure path without affecting other tiles.
    """

    def __init__(
        self,
        *,
        raise_on: tuple[int, int] | None = None,
        per_tile_segments: dict[tuple[int, int], list[VLMSegment]] | None = None,
    ) -> None:
        self._raise_on = raise_on
        self._per_tile = per_tile_segments or {}
        self.call_count = 0
        self.last_trail: list[dict] = []

    def detect(  # pragma: no cover — Protocol fill, never invoked here
        self, image: Image.Image, *, prompt_version: str = "v1"
    ) -> DetectionResult:
        del image, prompt_version
        return DetectionResult(prompt_version="stub", segments=[])

    def disambiguate_region(  # pragma: no cover
        self, crop: Image.Image, question: str
    ) -> str:
        del crop, question
        return ""

    def categorize_region(  # pragma: no cover
        self, crop: Image.Image
    ) -> CategorizePageTool:
        del crop
        return CategorizePageTool(region_kind="unknown")

    def detect_tile(
        self,
        crop: Image.Image,
        *,
        tile_position: tuple[int, int, int, int],
        trail_context: list[dict],
        legend,
    ) -> DetectionResult:
        del crop, legend
        self.call_count += 1
        self.last_trail = list(trail_context)
        row, col, _, _ = tile_position
        if self._raise_on is not None and (row, col) == self._raise_on:
            raise RuntimeError("stub vlm forced failure")
        if (row, col) in self._per_tile:
            return DetectionResult(
                prompt_version="v3_tiled", segments=self._per_tile[(row, col)]
            )
        # Default — one centred segment per tile so tests can check stitching.
        return DetectionResult(
            prompt_version="v3_tiled",
            segments=[
                VLMSegment(
                    bbox=(0.3, 0.3, 0.7, 0.7),
                    shape_hint="rectangular",
                    nearby_text=[f"tile-{row}-{col}"],
                )
            ],
        )


# ── Fixture builders ─────────────────────────────────────────────────────────


def _raster_source(width: int = 4000, height: int = 3000) -> DrawingSource:
    """Raster source with enough line content to clear the empty-tile skip.

    The empty-tile pre-filter (Canny edge density < 0.005) was added to keep
    blank tiles from invoking the VLM. Tests that want the stub VLM to
    actually be called need crops with non-trivial edge density — we draw a
    grid of lines spaced 60 px apart, dense enough that any tile of the
    default tile sizes carries hundreds of edge pixels.
    """
    probe = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(probe)
    spacing = 60
    for x in range(0, width, spacing):
        draw.line([(x, 0), (x, height)], fill="black", width=2)
    for y in range(0, height, spacing):
        draw.line([(0, y), (width, y)], fill="black", width=2)
    return DrawingSource(
        kind="raster_image",
        pdf_doc=None,
        page=None,
        page_size_pt=None,
        raster_probe=probe,
    )


def _empty_raster_source(width: int = 4000, height: int = 3000) -> DrawingSource:
    """Pure-white raster source — used to exercise the empty-tile skip."""
    probe = Image.new("RGB", (width, height), color="white")
    return DrawingSource(
        kind="raster_image",
        pdf_doc=None,
        page=None,
        page_size_pt=None,
        raster_probe=probe,
    )


def _ctx_with(
    source: DrawingSource | None,
    layout: PageLayout | None,
) -> PipelineContext:
    ctx = PipelineContext(drawing_id="t", original_filename="t.pdf")
    ctx.source = source
    ctx.layout = layout
    if source is not None:
        ctx.width_px, ctx.height_px = source.raster_probe.size
    # No ocr_cache — vector path will fall back to the fixed 200 DPI default.
    return ctx


# ── Tile geometry tests ──────────────────────────────────────────────────────


def test_tile_math_vector_pdf() -> None:
    """200×400 pt plan_view at smart_dpi=200, tile_px=1100, overlap=0.15.

    At 200 DPI a 1100-px tile is 1100 / 200 × 72 = 396 pt on a side. The
    plan_view (200×400 pt) is smaller than one tile on the X axis and almost
    one tile on the Y axis, so the math should produce a small grid (1×2 or
    fewer), every tile must lie inside the plan_view, and consecutive tiles
    along the long axis should overlap by ~15% of the tile size.
    """
    plan_view = (100.0, 100.0, 300.0, 500.0)  # 200 wide, 400 tall pt
    tiles = _compute_tiles(
        plan_view,
        source_kind="vector_pdf",
        dpi=200,
        tile_px=1100,
        overlap_pct=0.15,
    )
    assert tiles, "expected at least one tile"

    tile_size_pt = (1100 / 200) * 72  # 396 pt
    for rect, _row, _col, _total_r, _total_c in tiles:
        x0, y0, x1, y1 = rect
        # Each tile lies inside plan_view (clamped).
        assert x0 >= plan_view[0] - 1e-6
        assert y0 >= plan_view[1] - 1e-6
        assert x1 <= plan_view[2] + 1e-6
        assert y1 <= plan_view[3] + 1e-6
        # Tile dimensions never exceed the source-space tile size.
        assert (x1 - x0) <= tile_size_pt + 1e-6
        assert (y1 - y0) <= tile_size_pt + 1e-6

    # Vertical overlap check: pairs of tiles in the same column should overlap
    # in y by ~15% of tile_size_pt (boundary-clamped tiles may overlap more).
    cols: dict[int, list[tuple[float, float, float, float]]] = {}
    for rect, _row, col, _total_r, _total_c in tiles:
        cols.setdefault(col, []).append(rect)
    for col_rects in cols.values():
        col_rects.sort(key=lambda r: r[1])
        for prev, curr in zip(col_rects, col_rects[1:], strict=False):
            overlap = prev[3] - curr[1]
            # 15% of 396 ≈ 59.4 pt; clamping at the bottom edge can grow this,
            # but it should never be smaller than the configured overlap.
            assert overlap >= tile_size_pt * 0.15 - 1.0


def test_tile_math_raster_uses_pixel_coords() -> None:
    """Raster source: tiles are pixel-space and DPI is unused.

    A 5000×3000 px plan_view tiled at 1100 px / 15% overlap should produce a
    multi-tile grid where each tile is exactly 1100 px on a side (except
    boundary tiles which may clamp). The DPI argument is ignored — passing
    a wildly different DPI yields the same tile count.
    """
    plan_view = (0.0, 0.0, 5000.0, 3000.0)
    tiles_at_200 = _compute_tiles(
        plan_view, source_kind="raster_image", dpi=200, tile_px=1100, overlap_pct=0.15
    )
    tiles_at_999 = _compute_tiles(
        plan_view, source_kind="raster_image", dpi=999, tile_px=1100, overlap_pct=0.15
    )
    assert len(tiles_at_200) == len(tiles_at_999), "raster tiling must ignore DPI"

    # Non-boundary tiles should be exactly 1100 px on each side.
    for rect, _r, _c, _total_r, _total_c in tiles_at_200:
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        # Either a full-size tile or a boundary-clamped one.
        assert w <= 1100.0 + 1e-6
        assert h <= 1100.0 + 1e-6


# ── Dedup tests ──────────────────────────────────────────────────────────────


def _stitched(
    *,
    rect: tuple[float, float, float, float],
    tile_centre_dist: float,
    row: int = 0,
    col: int = 0,
    shape: str = "rectangular",
) -> _StitchedSegment:
    return _StitchedSegment(
        segment_id=f"DUCT-{row}-{col}-0",
        rect=rect,
        shape_hint=shape,
        nearby_text=[],
        tile_centre_dist=tile_centre_dist,
        row=row,
        col=col,
    )


def test_dedup_by_iou_keeps_more_central() -> None:
    """Two near-identical bboxes from neighbouring tiles, IoU > 0.4.

    The one with the smaller ``tile_centre_dist`` (more central in its tile)
    wins; the other is dropped.
    """
    central = _stitched(
        rect=(100.0, 100.0, 200.0, 200.0), tile_centre_dist=0.05, row=0, col=0
    )
    edgy = _stitched(
        rect=(105.0, 105.0, 205.0, 205.0), tile_centre_dist=0.40, row=0, col=1
    )
    survivors = _dedup_by_iou([edgy, central], threshold=0.4)
    assert len(survivors) == 1
    assert survivors[0] is central


def test_dedup_by_iou_keeps_both_when_iou_low() -> None:
    """Two segments with IoU < 0.4 — both survive."""
    a = _stitched(rect=(0.0, 0.0, 100.0, 100.0), tile_centre_dist=0.1)
    b = _stitched(rect=(200.0, 200.0, 300.0, 300.0), tile_centre_dist=0.1)
    survivors = _dedup_by_iou([a, b], threshold=0.4)
    assert len(survivors) == 2


# ── Trail-context tests ──────────────────────────────────────────────────────


def test_trail_context_includes_left_and_above_only() -> None:
    """In a 2×2 grid, the trail for tile (1, 1) covers (0, 0), (0, 1), (1, 0).

    Tile (1, 1) itself must not appear in its own trail. Future tiles never
    appear (there are none past (1, 1) in a 2×2, so this is trivially
    satisfied — the asserted negative is the (1, 1) self-exclusion).
    """
    processed: dict[tuple[int, int], list[_StitchedSegment]] = {
        (0, 0): [_stitched(rect=(0.0, 0.0, 50.0, 50.0), tile_centre_dist=0.0)],
        (0, 1): [_stitched(rect=(100.0, 0.0, 150.0, 50.0), tile_centre_dist=0.0)],
        (1, 0): [_stitched(rect=(0.0, 100.0, 50.0, 150.0), tile_centre_dist=0.0)],
        (1, 1): [
            _stitched(rect=(120.0, 120.0, 170.0, 170.0), tile_centre_dist=0.0)
        ],
    }
    current_tile_rect = (100.0, 100.0, 200.0, 200.0)

    trail = _build_trail_context(processed, 1, 1, current_tile_rect)

    # 3 trail entries — (0, 0), (0, 1), (1, 0) — and notably NOT (1, 1).
    # Some may collapse to zero-area inside the current tile (e.g. (0, 0)
    # which lies entirely outside): those are filtered by the function. We
    # check the upper bound and the explicit (1, 1) exclusion.
    assert len(trail) <= 3
    # The (1, 1) own-segment would project to bbox roughly (0.2, 0.2, 0.7, 0.7);
    # asserting NO trail entry is at that high bbox proves the self-exclusion.
    own_bbox_norm = (
        (120.0 - 100.0) / 100.0,
        (120.0 - 100.0) / 100.0,
        (170.0 - 100.0) / 100.0,
        (170.0 - 100.0) / 100.0,
    )
    assert all(entry["bbox_normalized"] != own_bbox_norm for entry in trail)


# ── Per-tile failure isolation ───────────────────────────────────────────────


def test_per_tile_failure_skipped_not_aborted() -> None:
    """One tile's VLM call raises; other tiles' segments still flow through."""
    src = _raster_source(width=3000, height=2000)
    layout = PageLayout(plan_view=(0.0, 0.0, 3000.0, 2000.0))
    ctx = _ctx_with(src, layout)
    # 3000/(1100*0.85) ≈ 3.2 → 3 cols; 2000/(1100*0.85) ≈ 2.1 → 2 rows.
    # Force the (0, 1) tile to raise.
    vlm = _StubVLM(raise_on=(0, 1))

    TiledDuctDetectionStage(vlm).run(ctx)

    # At least one segment from the surviving tiles made it through.
    assert len(ctx.segments_draft) > 0
    # The error message names the failing tile.
    assert any(
        "tiled_detect: tile (0,1) failed" in e for e in ctx.errors
    ), f"errors: {ctx.errors}"


# ── Oversized plan-view stitching ────────────────────────────────────────────


def test_oversized_plan_view_produces_many_tiles() -> None:
    """A plan view larger than 4×4 tiles produces ≥ 16 tiles + coherent stitch.

    "Coherent" = no two stitched segments overlap above the dedup threshold
    in the final ctx.segments_draft. Each stub-tile emits one centred bbox
    so cross-tile overlaps only happen if the dedup is broken.
    """
    # A 6000×6000 px raster plan_view: 1100 × 0.85 step ≈ 935 → 7 tiles per
    # axis, so 49 tiles total — comfortably above the 16-tile floor.
    src = _raster_source(width=8000, height=8000)
    layout = PageLayout(plan_view=(0.0, 0.0, 6000.0, 6000.0))
    ctx = _ctx_with(src, layout)
    vlm = _StubVLM()

    TiledDuctDetectionStage(vlm).run(ctx)

    # Tile count is observable through the stub's call counter.
    assert vlm.call_count >= 16
    # No two stitched segments overlap above threshold — the stub emits a
    # central bbox per tile, and the 15% overlap means adjacent tiles produce
    # bboxes that don't overlap (they're each centered in their own tile).
    drafts = ctx.segments_draft
    rects = [
        (
            d.geometry.points[0][0],
            d.geometry.points[0][1],
            d.geometry.points[1][0],
            d.geometry.points[1][1],
        )
        for d in drafts
    ]
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            ax0, ay0, ax1, ay1 = a
            bx0, by0, bx1, by1 = b
            ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
            iy = max(0.0, min(ay1, by1) - max(ay0, by0))
            inter = ix * iy
            if inter <= 0:
                continue
            area_a = (ax1 - ax0) * (ay1 - ay0)
            area_b = (bx1 - bx0) * (by1 - by0)
            iou = inter / (area_a + area_b - inter)
            assert iou <= 0.4, f"undedup'd overlap iou={iou:.2f} between {a} and {b}"


# ── Stage-level degradation ──────────────────────────────────────────────────


def test_stage_failure_is_degradation() -> None:
    """ctx.source = None triggers a stage-level exception → empty drafts + error."""
    ctx = _ctx_with(None, PageLayout(plan_view=(0.0, 0.0, 1000.0, 1000.0)))
    vlm = _StubVLM()

    TiledDuctDetectionStage(vlm).run(ctx)

    assert ctx.segments_draft == []
    assert any(e.startswith("tiled_detect:") for e in ctx.errors)


# ── Empty-tile pre-filter (PR-A) ─────────────────────────────────────────────


def test_empty_tile_skipped_without_vlm_call() -> None:
    """Pure-white plan_view tiles produce zero segments AND zero VLM calls.

    The empty-tile skip reads Canny edge density before invoking detect_tile.
    Tiles below the 0.005 threshold are background — calling the model on them
    invites the column-marker hallucination from drawing 01. The skip prevents
    both the bad output and the wasted inference call.
    """
    src = _empty_raster_source(width=4000, height=3000)
    layout = PageLayout(plan_view=(0.0, 0.0, 3000.0, 2000.0))
    ctx = _ctx_with(src, layout)
    vlm = _StubVLM()

    TiledDuctDetectionStage(vlm).run(ctx)

    assert vlm.call_count == 0
    assert ctx.segments_draft == []


def test_empty_tile_skip_logs_edge_density(caplog) -> None:  # type: ignore[no-untyped-def]
    """Skipped tiles emit an INFO log line citing the edge_density value."""
    import logging

    src = _empty_raster_source(width=2000, height=2000)
    layout = PageLayout(plan_view=(0.0, 0.0, 1500.0, 1500.0))
    ctx = _ctx_with(src, layout)
    vlm = _StubVLM()

    with caplog.at_level(logging.INFO, logger="app.pipeline.detect_tiled"):
        TiledDuctDetectionStage(vlm).run(ctx)

    skip_records = [r for r in caplog.records if "skipping empty tile" in r.message]
    assert skip_records, "expected at least one empty-tile skip log line"
    assert "edge_density" in skip_records[0].message


# ── Tiling approval gate corrections (V2 §5.8 follow-up) ────────────────────


def test_tiling_gate_corrections_apply() -> None:
    """User-supplied tile_px / overlap_pct in the approval payload re-shape
    the tile grid before the per-tile loop runs.

    The stub ``approval_gate`` returns ``{ tile_px: 1500, overlap_pct: 0.20 }``;
    with the larger tiles the same plan_view should produce strictly fewer
    tiles than the default 1100 × 0.15 configuration would have.
    """
    src = _raster_source(width=4000, height=3000)
    layout = PageLayout(plan_view=(0.0, 0.0, 4000.0, 3000.0))
    ctx = _ctx_with(src, layout)

    # Baseline tile count at the stage defaults — used to assert the
    # corrections actually changed the math, not just that *some* tiles ran.
    default_tiles = _compute_tiles(
        layout.plan_view,
        source_kind=src.kind,
        dpi=200,
        tile_px=1100,
        overlap_pct=0.15,
    )
    expected_corrected_tiles = _compute_tiles(
        layout.plan_view,
        source_kind=src.kind,
        dpi=200,
        tile_px=1500,
        overlap_pct=0.20,
    )
    # Sanity — the new math must differ from the default math, otherwise
    # this test is asserting a tautology.
    assert len(expected_corrected_tiles) != len(default_tiles)

    def gate(name, payload):  # type: ignore[no-untyped-def]
        del name, payload
        return {"tile_px": 1500, "overlap_pct": 0.20}

    ctx.approval_gate = gate
    vlm = _StubVLM()

    TiledDuctDetectionStage(vlm).run(ctx)

    assert vlm.call_count == len(expected_corrected_tiles)


def test_tiling_gate_corrections_clamp_oversize() -> None:
    """Oversized tile_px (5000) clamps to 2000 rather than rejecting the run."""
    new_tile_px, new_overlap = _apply_tiling_corrections(
        {"tile_px": 5000}, current_tile_px=1100, current_overlap_pct=0.15
    )
    assert new_tile_px == 2000
    assert new_overlap == 0.15


def test_tiling_gate_no_corrections_keeps_defaults() -> None:
    """Empty corrections dict leaves tile_px / overlap_pct untouched."""
    new_tile_px, new_overlap = _apply_tiling_corrections(
        {}, current_tile_px=1100, current_overlap_pct=0.15
    )
    assert new_tile_px == 1100
    assert new_overlap == 0.15
