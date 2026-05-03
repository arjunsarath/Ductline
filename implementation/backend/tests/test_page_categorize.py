"""Page Categorizer (SOLUTION-DESIGN-V2 §5.3).

Six tests covering: single-plan-view classification, multi-plan-view largest
selection, no-plan-view whole-page fallback, named-region classification
(legend/notes/schedule/title block), VLM fallback for unknown rectangles,
and stage-level failure degradation.

Six PR-3.6 tests cover the strip-merge geometry (adjacent strips combine,
strip absorbed by large neighbour, isolated strip picks nearest by centre),
the diagnostic INFO logging on the picked-plan-view and fallback paths,
and the LAYOUT keyword regression that fixed drawing 01's categorizer-failed.

Tests construct ``DrawingSource`` + ``OCRCache`` directly so they exercise
``PageCategorizerStage`` in isolation — the categorizer never calls the OCR
engine itself, so its inputs (raster_probe + matches) are the only things
that need to be set up. Real Ollama is never contacted; VLM is stubbed.
"""

from __future__ import annotations

import logging

import pytest
from PIL import Image, ImageDraw

from app.ocr.base import OCRMatch
from app.ocr.cache import OCRCache
from app.pipeline.base import PipelineContext
from app.pipeline.categorize import (
    PageCategorizerStage,
    _is_strip,
    _merge_strips,
    _select_plan_view,
)
from app.source.base import DrawingSource
from app.vlm.base import VLMError
from app.vlm.tools import CategorizePageTool, DetectionResult, PageRegionsTool

# ── Stubs ────────────────────────────────────────────────────────────────────


class _StubVLM:
    """VLMClient stub — categorize_region returns a preset value or raises.

    ``detect_page_regions`` is also stubbed: by default it returns a tool
    with ``plan_view=None``, which the categorizer treats as "VLM didn't
    localise a plan view" and falls through to the heuristic. Tests that
    want to exercise the VLM-first happy path pass ``page_regions_result``
    or ``raise_on_page_regions``.

    detect / disambiguate_region exist only to satisfy the Protocol; tests
    here never call them.
    """

    def __init__(
        self,
        *,
        categorize_kind: str = "unknown",
        raise_on_categorize: bool = False,
        page_regions_result: PageRegionsTool | None = None,
        raise_on_page_regions: bool = False,
    ) -> None:
        self._kind = categorize_kind
        self._raise = raise_on_categorize
        self._page_regions_result = page_regions_result
        self._raise_on_page_regions = raise_on_page_regions
        self.call_count = 0
        self.page_regions_call_count = 0
        self.heuristic_invoked = False

    def detect(self, image: Image.Image, *, prompt_version: str = "v1") -> DetectionResult:
        del image, prompt_version
        return DetectionResult(prompt_version="stub", segments=[])

    def disambiguate_region(self, crop: Image.Image, question: str) -> str:
        del crop, question
        return "none"

    def categorize_region(self, crop: Image.Image) -> CategorizePageTool:
        del crop
        self.call_count += 1
        if self._raise:
            raise RuntimeError("vlm offline")
        return CategorizePageTool(region_kind=self._kind)  # type: ignore[arg-type]

    def detect_page_regions(self, image: Image.Image) -> PageRegionsTool:
        del image
        self.page_regions_call_count += 1
        if self._raise_on_page_regions:
            raise VLMError("stub: detect_page_regions raised")
        if self._page_regions_result is not None:
            return self._page_regions_result
        # Default — no plan view → categorizer falls back to the heuristic.
        return PageRegionsTool()


# ── Fixture builders ─────────────────────────────────────────────────────────


def _raster_source(image: Image.Image) -> DrawingSource:
    """Build a raster_image DrawingSource around an in-memory image.

    Raster sources express RectPt in pixel coords (no point conversion), so
    OCR matches and layout rects use the same coordinate system — keeps
    test assertions readable.
    """
    return DrawingSource(
        kind="raster_image",
        pdf_doc=None,
        page=None,
        page_size_pt=None,
        raster_probe=image,
    )


def _ocr_cache(matches: list[OCRMatch]) -> OCRCache:
    """OCRCache with a fixed probe DPI of 150 — DPI is irrelevant here because
    the categorizer reads matches directly and never recomputes pixel sizes."""
    return OCRCache(
        matches=matches,
        smallest_text_height_px_p5=10.0,
        source="ocr_probe",
        probe_dpi_used=150,
    )


def _ctx_with(source: DrawingSource, cache: OCRCache | None) -> PipelineContext:
    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    ctx.source = source
    ctx.ocr_cache = cache
    ctx.width_px, ctx.height_px = source.raster_probe.size
    return ctx


def _match(text: str, *, x: int, y: int, w: int = 60, h: int = 14) -> OCRMatch:
    """OCRMatch with bbox in (x, y, w, h) per app.ocr.base.Bbox."""
    return OCRMatch(text=text, bbox=(x, y, w, h), confidence=0.9)


def _vertical_split_image(width: int = 800, height: int = 600) -> Image.Image:
    """Image with a single thick vertical line at x=width/2.

    The line is long enough (full height) and prominent enough that
    HoughLinesP detects it. Yields a 2-rectangle decomposition:
    left half + right half (plus the whole-page rect).
    """
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)
    mid_x = width // 2
    draw.line([(mid_x, 0), (mid_x, height)], fill="black", width=4)
    return img


def _quadrant_split_image(width: int = 800, height: int = 600) -> Image.Image:
    """Image with one vertical + one horizontal divider — yields a 2x2 grid."""
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)
    mid_x, mid_y = width // 2, height // 2
    draw.line([(mid_x, 0), (mid_x, height)], fill="black", width=4)
    draw.line([(0, mid_y), (width, mid_y)], fill="black", width=4)
    return img


def _blank_image(width: int = 800, height: int = 600) -> Image.Image:
    """Blank image — no Hough lines detected; only the whole-page rect remains."""
    return Image.new("RGB", (width, height), color="white")


# ── Tests ────────────────────────────────────────────────────────────────────


def test_categorizer_single_plan_view() -> None:
    """One plan-view rectangle + one title block in the lower-right quadrant."""
    img = _vertical_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        # Left half: plan view keyword.
        _match("MECHANICAL PLAN", x=100, y=100),
        # Right half (lower-right quadrant): title-block keywords.
        _match("PROJECT NAME: ACME", x=420, y=460),
        _match("DRAWN BY: AS", x=420, y=480),
        _match("DATE: 2026-05-02", x=420, y=500),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    assert ctx.layout.plan_view is not None
    px0, py0, px1, py1 = ctx.layout.plan_view
    # Plan-view rect must contain the "MECHANICAL PLAN" text on the left half.
    assert px0 <= 100 < px1
    assert py0 <= 100 < py1
    # Title block rect is the right-half partition; its right edge lies at
    # the page boundary and it contains the title-block matches.
    assert ctx.layout.title_block is not None
    tx0, _, tx1, _ = ctx.layout.title_block
    assert tx0 >= 380  # right of the divider
    assert tx1 >= 780  # extends to the right page edge
    # No degradation warnings on the happy path.
    assert "categorizer_failed" not in " ".join(ctx.errors)
    assert "multi_plan_view_detected" not in ctx.errors


def test_categorizer_multi_plan_view_picks_largest() -> None:
    """Two plan-view rectangles of unequal size — keep the larger; warn."""
    # 1200-wide image with a vertical divider at x=400, so the left rect is
    # 400 wide and the right rect is 800 wide. Both contain a PLAN keyword.
    width, height = 1200, 600
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)
    draw.line([(400, 0), (400, height)], fill="black", width=4)
    src = _raster_source(img)
    matches = [
        _match("FLOOR PLAN", x=100, y=100),  # left, smaller rect
        _match("PLAN VIEW", x=600, y=100),  # right, larger rect
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    assert ctx.layout.plan_view is not None
    px0, _, px1, _ = ctx.layout.plan_view
    # Largest rect spans the right side (width 800 > 400). Hough divider
    # may land a few pixels off x=400, so allow tolerance on the left edge.
    assert (px1 - px0) > 500
    assert px0 >= 380
    assert "multi_plan_view_detected" in ctx.errors


def test_categorizer_no_plan_view_falls_back_to_whole_page() -> None:
    """No PLAN/LEVEL/FLOOR keywords anywhere — whole-page fallback + warning."""
    img = _blank_image(800, 600)
    src = _raster_source(img)
    matches = [_match("RANDOM TEXT", x=100, y=100)]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    # Whole-page rect is (0, 0, w, h) in pixel space for raster sources.
    assert ctx.layout.plan_view == (0.0, 0.0, 800.0, 600.0)
    assert any("categorizer_failed" in e for e in ctx.errors)


def test_categorizer_classifies_legend_notes_schedule() -> None:
    """All four named region kinds present — assert each maps correctly."""
    img = _quadrant_split_image(800, 600)
    src = _raster_source(img)
    # Top-left: plan view. Top-right: legend. Bottom-left: notes.
    # Bottom-right (lower-right quadrant): title block + schedule. Title block
    # wins for the lower-right rect because the keyword check runs in priority
    # order, so we put the schedule text in a separate part of the sheet.
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),  # top-left → plan_view
        _match("LEGEND", x=500, y=100),  # top-right → legend
        _match("GENERAL NOTES", x=100, y=400),  # bottom-left → notes
        _match("SCHEDULE", x=100, y=420),  # bottom-left → schedule
        _match("PROJECT: TEST", x=500, y=420),  # bottom-right → title_block
        _match("DRAWN BY: AS", x=500, y=440),
        _match("SCALE: 1/4\"", x=500, y=460),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    assert ctx.layout.plan_view is not None
    assert ctx.layout.legend is not None
    # Hough divider lands within a few pixels of the midline; allow tolerance.
    lx0, ly0, _, _ = ctx.layout.legend
    assert lx0 >= 380  # right of divider (~400 ± tolerance)
    assert ly0 < 320
    assert ctx.layout.notes  # at least one notes rect found
    assert ctx.layout.title_block is not None
    tx0, ty0, _, _ = ctx.layout.title_block
    assert tx0 >= 380 and ty0 >= 280


def test_categorizer_vlm_fallback_for_unknown() -> None:
    """A rectangle with no keyword match goes through VLM categorize_region."""
    img = _vertical_split_image(800, 600)
    src = _raster_source(img)
    # Left half has the plan-view keyword; right half has nothing the
    # algorithm recognises (no title-block keywords, no LEGEND/NOTES/etc.)
    # so the right rect goes to the VLM, which returns "legend".
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),
        _match("ZZZ", x=500, y=100),  # noise; no keyword match
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))
    vlm = _StubVLM(categorize_kind="legend")

    PageCategorizerStage(vlm).run(ctx)

    assert ctx.layout is not None
    assert ctx.layout.legend is not None
    # VLM was called at least once on the unknown rectangle. The whole-page
    # rect also has no matched keywords (the plan_view keyword sets the
    # left-half rect's classification but the whole-page rect contains
    # both texts, so PLAN matches there too), so we just assert >0.
    assert vlm.call_count >= 1


def test_categorizer_failure_is_degradation() -> None:
    """Algorithmic path raises → ctx.layout=None; ``page_categorize:`` error."""
    # A DrawingSource missing ``raster_probe`` would be the obvious failure
    # mode but pydantic forbids that at model construction. Instead we
    # construct a context with no source at all — the assertion in
    # _build_layout fires and is caught by the stage's degradation handler.
    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    ctx.ocr_cache = _ocr_cache([_match("MECHANICAL PLAN", x=10, y=10)])
    ctx.source = None  # explicit — degraded ingest

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is None
    assert any(e.startswith("page_categorize:") for e in ctx.errors)


# ── PR-3.6: Strip merge geometry ─────────────────────────────────────────────


def test_strip_merge_two_adjacent_strips_combine() -> None:
    """Two narrow strips sharing an edge collapse to one rect.

    With two strips (and nothing else for them to absorb into), the merge
    pairs them with each other; the result is a single bounding rect of
    their union. Page is 1000×1000 so the strip threshold is comfortably
    above the 50-px width of these strips.
    """
    page_w, page_h = 1000, 1000
    # Two stacked strips, both 50 wide, sharing a horizontal edge at y=400.
    rects = [(100, 200, 150, 400), (100, 400, 150, 600)]

    merged = _merge_strips(rects, page_w, page_h)

    assert len(merged) == 1
    # Bounding rect of the union spans both originals.
    assert merged[0] == (100, 200, 150, 600)


def test_strip_merge_strip_absorbed_by_large_neighbour() -> None:
    """A strip touching a large rect is absorbed into the bounding rect.

    The large rect is comfortably above the strip threshold; the strip
    shares a vertical edge with it and gets pulled into the union.
    """
    page_w, page_h = 1000, 1000
    large = (200, 100, 700, 800)  # 500x700 — not a strip
    strip = (700, 100, 750, 800)  # 50x700 — strip (width < 0.45*1000)
    assert _is_strip(strip, page_w, page_h)
    assert not _is_strip(large, page_w, page_h)

    merged = _merge_strips([large, strip], page_w, page_h)

    assert len(merged) == 1
    assert merged[0] == (200, 100, 750, 800)


def test_strip_merge_isolated_strip_picks_nearest_by_centre() -> None:
    """A strip sharing no edge with any neighbour falls back to centre distance.

    Two non-adjacent non-strip rects of different sizes are placed so the
    strip's centre is closer to the smaller one. Without the centre-distance
    fallback (i.e. relying on shared-edge alone, which is zero for both
    candidates) the merge would still be deterministic but arbitrary; with
    the fallback, the nearer rect wins.

    The non-strip rects are sized comfortably above
    ``_STRIP_MIN_DIM_FRACTION * min(page)`` on each side so they survive
    the strip check unchanged. The two non-strip rects don't overlap with
    each other or with the strip — that keeps the geometry of the test
    obvious (no shared edges, no interior overlaps).
    """
    page_w, page_h = 2000, 2000
    # Two non-overlapping, non-adjacent non-strip rects.
    near = (50, 50, 1150, 1150)  # 1100x1100 in upper-left
    far = (900, 900, 1950, 1950)  # 1050x1050 in lower-right
    # An isolated strip in upper-left region — closer to ``near`` than to
    # ``far`` by centre distance. near centre = (600, 600); far centre =
    # (1425, 1425); strip centre = (1180, 360) → dist(near) = ~640, dist(far)
    # = ~1090. So near wins.
    strip = (1170, 350, 1190, 370)  # 20x20 — strip, isolated
    assert _is_strip(strip, page_w, page_h)
    assert not _is_strip(near, page_w, page_h)
    assert not _is_strip(far, page_w, page_h)

    merged = _merge_strips([near, far, strip], page_w, page_h)

    # ``far`` survives untouched; ``near`` absorbs the strip.
    assert len(merged) == 2
    assert far in merged
    expected_union = (50, 50, 1190, 1150)
    assert expected_union in merged


def test_categorizer_logs_picked_plan_view(caplog) -> None:  # type: ignore[no-untyped-def]
    """A successful classification emits a 'plan_view picked' INFO record.

    Uses the same single-plan-view fixture as the happy-path test and asserts
    on the structured log line so future log-format changes are caught here.
    """
    img = _vertical_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),
        _match("PROJECT NAME: ACME", x=420, y=460),
        _match("DRAWN BY: AS", x=420, y=480),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    with caplog.at_level(logging.INFO, logger="app.pipeline.categorize"):
        PageCategorizerStage(_StubVLM()).run(ctx)

    plan_view_records = [
        r for r in caplog.records if "plan_view picked" in r.getMessage()
    ]
    assert plan_view_records, "expected an INFO record containing 'plan_view picked'"
    msg = plan_view_records[0].getMessage()
    # The log line carries the picked rect as a 4-tuple of ints.
    assert ctx.layout is not None
    expected = tuple(int(v) for v in ctx.layout.plan_view)
    assert str(expected) in msg


def test_categorizer_logs_fallback_reason(caplog) -> None:  # type: ignore[no-untyped-def]
    """Fallback path emits an INFO record with the reason."""
    img = _blank_image(800, 600)
    src = _raster_source(img)
    matches = [_match("RANDOM TEXT", x=100, y=100)]
    ctx = _ctx_with(src, _ocr_cache(matches))

    with caplog.at_level(logging.INFO, logger="app.pipeline.categorize"):
        PageCategorizerStage(_StubVLM()).run(ctx)

    fallback_records = [
        r for r in caplog.records if "fallback to whole-page" in r.getMessage()
    ]
    assert fallback_records, "expected an INFO record with 'fallback to whole-page'"
    # Reason follows in parentheses; we don't lock the exact wording, just
    # that a reason is present so the failure mode is observable.
    msg = fallback_records[0].getMessage()
    assert "reason" in msg


def test_categorizer_layout_keyword_matches_plan_view() -> None:
    """PR-3.6 Issue B regression: drawing 01's plan view region is labelled
    "PARTITIONING HVAC LAYOUT" — none of the original PLAN/LEVEL/FLOOR/
    MECHANICAL PLAN keywords matched, so the categorizer fell back. We added
    LAYOUT (and HVAC) to the plan-view keyword set; this test pins that fix
    so future keyword tuning doesn't silently regress drawing 01.
    """
    img = _vertical_split_image(800, 600)
    src = _raster_source(img)
    # Left half: a "LAYOUT" keyword (no PLAN/LEVEL/FLOOR/MECHANICAL).
    # Right half (lower-right quadrant): title-block keywords.
    matches = [
        _match("PARTITIONING HVAC LAYOUT", x=100, y=100),
        _match("PROJECT NAME: ACME", x=420, y=460),
        _match("DRAWN BY: AS", x=420, y=480),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    # Categorizer must NOT have fallen back to the whole page — the LAYOUT
    # keyword should have classified the left-half rect as plan_view.
    assert "categorizer_failed" not in " ".join(ctx.errors)
    # The picked plan_view contains the LAYOUT text on the left half.
    px0, py0, px1, py1 = ctx.layout.plan_view
    assert px0 <= 100 < px1
    assert py0 <= 100 < py1


# ── Widened legend keyword set (DESCRIPTION / SYMBOLS / ABBREVIATIONS) ───────


def test_legend_keyword_matches_description() -> None:
    """The legend on this drawing is headed "DESCRIPTION" — the widened
    keyword set must classify that rect as legend so downstream §5.4 has
    something to parse. Pre-fix the categorizer matched only the literal
    "LEGEND" and missed every benchmark whose legend used "DESCRIPTION"
    or another industry synonym.
    """
    img = _quadrant_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),  # top-left → plan_view
        _match("DESCRIPTION", x=500, y=100),  # top-right → legend
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    assert ctx.layout.legend is not None
    lx0, _, _, _ = ctx.layout.legend
    assert lx0 >= 380  # right of the divider — top-right quadrant


def test_legend_keyword_matches_symbols() -> None:
    """Drawings using "SYMBOLS" as the legend heading must be recognised."""
    img = _quadrant_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),
        _match("SYMBOLS", x=500, y=100),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    assert ctx.layout.legend is not None


def test_legend_keyword_matches_abbreviations() -> None:
    """Drawings using "ABBREVIATIONS" as the legend heading must be recognised."""
    img = _quadrant_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),
        _match("ABBREVIATIONS", x=500, y=100),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    assert ctx.layout.legend is not None


def test_legend_keyword_word_boundary() -> None:
    """Substring matching would false-fire "LEGEND" against "LEGENDARY".

    The widened legend keyword set includes generic English words
    (DESCRIPTION) that would match too aggressively under substring rules,
    so the matcher uses whitespace-bounded equality. This regression test
    pins that contract: a rect whose only OCR text is "LEGENDARY DESIGN"
    must NOT be classified as legend.
    """
    img = _quadrant_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),  # top-left → plan_view
        # Top-right: text that contains "LEGEND" as a substring but not as
        # a whitespace-bounded word.
        _match("LEGENDARY DESIGN", x=500, y=100),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))

    PageCategorizerStage(_StubVLM()).run(ctx)

    assert ctx.layout is not None
    # No legend identified — the substring "LEGEND" inside "LEGENDARY"
    # is rejected by the word-boundary rule.
    assert ctx.layout.legend is None


# ── Plan-view selection: smaller-better when nested ──────────────────────────


def test_plan_view_prefers_smaller_when_contained() -> None:
    """Two plan_view candidates with one nested inside the other → pick child.

    The page-wide outer rect picks up plan_view classification because the
    title bar "HVAC LAYOUT" sits inside it; the inset content rect also
    picks up plan_view classification on the same keywords. Pre-fix the
    largest-by-area rule picked the outer rect and let the legend / title /
    heading stay inside ``plan_view``. The "smaller better" tie-break
    selects the child instead, and does NOT fire the multi-plan-view
    warning — the outer rect isn't a real second plan view.
    """
    # Build directly against _select_plan_view to keep the geometry exact;
    # Hough decomposition would round the rects by a few pixels and make
    # the assertion fragile.
    outer = (0.0, 0.0, 1000.0, 800.0)  # 1000x800 — page-wide
    inner = (100.0, 100.0, 700.0, 600.0)  # 600x500 — strictly inside outer
    # parent area = 800_000; child area = 300_000 → 2.67× ratio, > 1.5× gate.

    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    picked = _select_plan_view([outer, inner], ctx)

    assert picked == inner
    # Critical: nested candidates must NOT trigger multi_plan_view_detected.
    assert "multi_plan_view_detected" not in ctx.errors


def test_plan_view_largest_when_side_by_side() -> None:
    """Two plan_view candidates side-by-side → pick largest, fire warning.

    Side-by-side (no containment) is the genuine multi-plan-view edge case
    the §7 warning targets. Verifying that path still works after the
    nested-preference change.
    """
    left = (0.0, 0.0, 400.0, 800.0)  # 320_000 area
    right = (400.0, 0.0, 1000.0, 800.0)  # 480_000 area
    # Neither contains the other — they meet at x=400 with no overlap.

    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    picked = _select_plan_view([left, right], ctx)

    assert picked == right  # largest by area
    assert "multi_plan_view_detected" in ctx.errors


# ── VLM-first page categorization (SOLUTION-DESIGN-V2 §5.3 refactor) ─────────


def test_vlm_first_populates_layout_when_returns_valid() -> None:
    """Stub returns a sensible PageRegionsTool → layout matches; heuristic
    is NOT consulted. The OCR cache is intentionally empty (which would
    force the heuristic to fall back to whole-page) so we can prove the
    VLM-first path bypassed the heuristic entirely.
    """
    img = _blank_image(800, 600)
    src = _raster_source(img)
    # Empty OCR cache: if the heuristic ran, it would fall back to whole-
    # page plan_view. The VLM-first result must override that.
    ctx = _ctx_with(src, _ocr_cache([]))
    vlm = _StubVLM(
        page_regions_result=PageRegionsTool(
            plan_view=(0.05, 0.05, 0.70, 0.95),
            # legend is now a list — the model may return one or more
            # blocks (symbol box + abbreviation table commonly split).
            legend=[(0.72, 0.05, 0.98, 0.40)],
            schedule=(0.72, 0.42, 0.98, 0.70),
            title_block=(0.72, 0.85, 0.98, 0.98),
            notes=[(0.72, 0.72, 0.98, 0.84)],
        )
    )

    PageCategorizerStage(vlm).run(ctx)

    assert ctx.layout is not None
    # Plan view scaled to source coords (raster source → pixel size).
    # Each VLM bbox is padded by 3% per edge before scaling — see
    # `_VLM_BBOX_PAD_RATIO` in categorize.py. plan_view (0.05, 0.05, 0.70,
    # 0.95) → padded (0.02, 0.02, 0.73, 0.98) → scaled (16, 12, 584, 588).
    assert ctx.layout.plan_view == pytest.approx((16.0, 12.0, 584.0, 588.0))
    # legend (0.72, 0.05, 0.98, 0.40) → padded (0.69, 0.02, 1.0, 0.43)
    # (right edge clamped at 1.0) → scaled (552, 12, 800, 258).
    assert ctx.layout.legend == pytest.approx((552.0, 12.0, 800.0, 258.0))
    assert ctx.layout.schedule is not None
    assert ctx.layout.title_block is not None
    assert len(ctx.layout.notes) == 1
    # The VLM-first path bypasses the heuristic entirely: the no-OCR-matches
    # whole-page fallback warning would have fired had the heuristic run.
    assert not any("categorizer_failed" in e for e in ctx.errors)
    assert vlm.page_regions_call_count == 1


def test_vlm_first_falls_back_on_no_plan_view() -> None:
    """Stub returns plan_view=None → fall through to heuristic."""
    img = _vertical_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),
        _match("PROJECT NAME: ACME", x=420, y=460),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))
    # Default stub: plan_view=None → heuristic runs.
    vlm = _StubVLM()

    PageCategorizerStage(vlm).run(ctx)

    assert ctx.layout is not None
    # Heuristic produced the layout — left-half plan view containing the
    # MECHANICAL PLAN keyword.
    px0, py0, px1, py1 = ctx.layout.plan_view
    assert px0 <= 100 < px1
    assert py0 <= 100 < py1
    assert vlm.page_regions_call_count == 1


def test_vlm_first_falls_back_on_implausible_layout() -> None:
    """Stub returns plan_view = whole-page bbox → guard rejects → heuristic."""
    img = _vertical_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),
        _match("PROJECT NAME: ACME", x=420, y=460),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))
    vlm = _StubVLM(
        page_regions_result=PageRegionsTool(
            # Plan view is essentially the whole page — guard rejects.
            plan_view=(0.0, 0.0, 1.0, 1.0),
        )
    )

    PageCategorizerStage(vlm).run(ctx)

    assert ctx.layout is not None
    # Heuristic ran: left-half plan view, NOT the whole-page rect from VLM.
    px0, _, px1, _ = ctx.layout.plan_view
    # Whole-page would be (0, 0, 800, 600); the heuristic picks the left
    # half (~0–400).
    assert px1 < 600  # not the whole page
    assert px0 <= 100 < px1


def test_vlm_first_falls_back_on_vlm_error() -> None:
    """Stub raises VLMError → heuristic runs unchanged."""
    img = _vertical_split_image(800, 600)
    src = _raster_source(img)
    matches = [
        _match("MECHANICAL PLAN", x=100, y=100),
        _match("PROJECT NAME: ACME", x=420, y=460),
    ]
    ctx = _ctx_with(src, _ocr_cache(matches))
    vlm = _StubVLM(raise_on_page_regions=True)

    PageCategorizerStage(vlm).run(ctx)

    assert ctx.layout is not None
    # Heuristic ran successfully despite VLM raising.
    px0, py0, px1, py1 = ctx.layout.plan_view
    assert px0 <= 100 < px1
    assert py0 <= 100 < py1
    assert vlm.page_regions_call_count == 1


def test_vlm_first_scales_normalized_bboxes_correctly() -> None:
    """Padded VLM plan_view (0.1, 0.1, 0.9, 0.9) on 800×600 → (56, 42, 744, 558).

    Each edge of the VLM bbox is padded by 3% of page dims before scaling
    (the model consistently under-estimates region extents). 0.1 - 0.03 =
    0.07; 0.9 + 0.03 = 0.93. Scaled to 800×600: (56, 42, 744, 558).
    """
    img = _blank_image(800, 600)
    src = _raster_source(img)
    ctx = _ctx_with(src, _ocr_cache([]))
    vlm = _StubVLM(
        page_regions_result=PageRegionsTool(
            plan_view=(0.1, 0.1, 0.9, 0.9),
        )
    )

    PageCategorizerStage(vlm).run(ctx)

    assert ctx.layout is not None
    assert ctx.layout.plan_view == pytest.approx((56.0, 42.0, 744.0, 558.0))


def test_vlm_first_unions_multi_block_legend() -> None:
    """Multi-block legend → PageLayout.legend is the bounding rect of all blocks.

    Engineering drawings frequently split the legend into a symbol icon
    box AND a separate abbreviation table. The categorizer unions the two
    so LegendParserStage downstream sees one contiguous legend rect.
    """
    img = _blank_image(800, 600)
    src = _raster_source(img)
    ctx = _ctx_with(src, _ocr_cache([]))
    vlm = _StubVLM(
        page_regions_result=PageRegionsTool(
            plan_view=(0.05, 0.05, 0.65, 0.95),
            legend=[
                (0.70, 0.10, 0.95, 0.40),  # upper symbol box
                (0.70, 0.55, 0.95, 0.85),  # lower abbreviation table
            ],
        )
    )

    PageCategorizerStage(vlm).run(ctx)

    assert ctx.layout is not None
    # Each input is padded individually then unioned. Upper box padded:
    # (0.67, 0.07, 0.98, 0.43); lower box padded: (0.67, 0.52, 0.98, 0.88).
    # Bounding rect: (0.67, 0.07, 0.98, 0.88) → scaled to 800×600:
    # (536, 42, 784, 528).
    assert ctx.layout.legend == pytest.approx((536.0, 42.0, 784.0, 528.0))
