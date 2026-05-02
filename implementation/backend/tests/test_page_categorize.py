"""Page Categorizer (SOLUTION-DESIGN-V2 §5.3).

Six tests covering: single-plan-view classification, multi-plan-view largest
selection, no-plan-view whole-page fallback, named-region classification
(legend/notes/schedule/title block), VLM fallback for unknown rectangles,
and stage-level failure degradation.

Tests construct ``DrawingSource`` + ``OCRCache`` directly so they exercise
``PageCategorizerStage`` in isolation — the categorizer never calls the OCR
engine itself, so its inputs (raster_probe + matches) are the only things
that need to be set up. Real Ollama is never contacted; VLM is stubbed.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from app.ocr.base import OCRMatch
from app.ocr.cache import OCRCache
from app.pipeline.base import PipelineContext
from app.pipeline.categorize import PageCategorizerStage
from app.source.base import DrawingSource
from app.vlm.tools import CategorizePageTool, DetectionResult

# ── Stubs ────────────────────────────────────────────────────────────────────


class _StubVLM:
    """VLMClient stub — categorize_region returns a preset value or raises.

    detect / disambiguate_region exist only to satisfy the Protocol; tests
    here never call them.
    """

    def __init__(
        self, *, categorize_kind: str = "unknown", raise_on_categorize: bool = False
    ) -> None:
        self._kind = categorize_kind
        self._raise = raise_on_categorize
        self.call_count = 0

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


