"""Legend Parser (SOLUTION-DESIGN-V2 §5.4).

Five tests covering: textual abbreviation rows, unit detection,
glyph-only VLM fallback, no-legend-region graceful skip, and stage-level
failure degradation.

Tests construct ``DrawingSource``, ``OCRCache`` and ``PageLayout`` directly
so the parser is exercised in isolation. Real Ollama is never contacted;
VLM is stubbed.
"""

from __future__ import annotations

from PIL import Image

from app.ocr.base import OCRMatch
from app.ocr.cache import OCRCache
from app.pipeline.base import PipelineContext
from app.pipeline.layout import PageLayout
from app.pipeline.legend import LegendParserStage
from app.source.base import DrawingSource
from app.vlm.tools import CategorizePageTool, DetectionResult

# ── Stubs ────────────────────────────────────────────────────────────────────


class _StubVLM:
    """VLMClient stub — disambiguate_region returns a preset value or raises.

    detect / categorize_region exist only to satisfy the Protocol; tests
    here never call them.
    """

    def __init__(
        self,
        *,
        glyph_label: str = "Round Duct",
        raise_on_glyph: bool = False,
    ) -> None:
        self._label = glyph_label
        self._raise = raise_on_glyph
        self.glyph_calls = 0

    def detect(self, image: Image.Image, *, prompt_version: str = "v1") -> DetectionResult:
        del image, prompt_version
        return DetectionResult(prompt_version="stub", segments=[])

    def disambiguate_region(self, crop: Image.Image, question: str) -> str:
        del crop, question
        self.glyph_calls += 1
        if self._raise:
            raise RuntimeError("vlm offline")
        return self._label

    def categorize_region(self, crop: Image.Image) -> CategorizePageTool:
        del crop
        return CategorizePageTool(region_kind="unknown")


# ── Fixture builders ─────────────────────────────────────────────────────────


def _raster_source(image: Image.Image) -> DrawingSource:
    """Build a raster_image DrawingSource so RectPt is in pixel space — keeps
    test assertions readable (no points-vs-pixels conversion to track)."""
    return DrawingSource(
        kind="raster_image",
        pdf_doc=None,
        page=None,
        page_size_pt=None,
        raster_probe=image,
    )


def _ocr_cache(matches: list[OCRMatch]) -> OCRCache:
    return OCRCache(
        matches=matches,
        smallest_text_height_px_p5=10.0,
        source="ocr_probe",
        probe_dpi_used=150,
    )


def _ctx_with_legend(
    matches: list[OCRMatch],
    legend_rect: tuple[float, float, float, float],
    *,
    image_size: tuple[int, int] = (800, 600),
) -> PipelineContext:
    img = Image.new("RGB", image_size, color="white")
    src = _raster_source(img)
    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    ctx.source = src
    ctx.ocr_cache = _ocr_cache(matches)
    ctx.layout = PageLayout(
        plan_view=(0.0, 0.0, float(image_size[0]), float(image_size[1])),
        legend=legend_rect,
    )
    ctx.width_px, ctx.height_px = image_size
    return ctx


def _match(text: str, *, x: int, y: int, w: int = 80, h: int = 14) -> OCRMatch:
    return OCRMatch(text=text, bbox=(x, y, w, h), confidence=0.9)


# ── Tests ────────────────────────────────────────────────────────────────────


def test_legend_parse_text_rows() -> None:
    """Three abbreviation rows in the legend rect map to Legend.abbreviations."""
    legend_rect = (400.0, 100.0, 780.0, 400.0)
    # Each row has the abbreviation token followed by its expansion. The
    # row-grouping heuristic clusters by y-coordinate, so each row's
    # matches share a y-band.
    matches = [
        _match("SA", x=420, y=120, w=30),
        _match("Supply Air", x=460, y=120, w=120),
        _match("RA", x=420, y=160, w=30),
        _match("Return Air", x=460, y=160, w=120),
        _match("VAV", x=420, y=200, w=40),
        _match("Variable Air Volume", x=470, y=200, w=200),
    ]
    ctx = _ctx_with_legend(matches, legend_rect)

    LegendParserStage(_StubVLM()).run(ctx)

    assert ctx.legend is not None
    assert "SA" in ctx.legend.abbreviations
    assert ctx.legend.abbreviations["SA"] == "Supply Air"
    assert ctx.legend.abbreviations["RA"] == "Return Air"
    assert ctx.legend.abbreviations["VAV"] == "Variable Air Volume"
    # No errors on the happy path.
    assert not any(e.startswith("legend_parse:") for e in ctx.errors)


def test_legend_parse_unit_detection() -> None:
    """INCHES → 'inches'; MM → 'mm'; absent → 'unknown'."""
    legend_rect = (400.0, 100.0, 780.0, 400.0)

    # Inches declaration.
    matches_in = [
        _match("DIMENSIONS", x=420, y=120, w=120),
        _match("IN INCHES", x=550, y=120, w=120),
    ]
    ctx_in = _ctx_with_legend(matches_in, legend_rect)
    LegendParserStage(_StubVLM()).run(ctx_in)
    assert ctx_in.legend is not None
    assert ctx_in.legend.units == "inches"

    # Millimeter declaration.
    matches_mm = [
        _match("DIMENSIONS", x=420, y=120, w=120),
        _match("IN MM", x=550, y=120, w=80),
    ]
    ctx_mm = _ctx_with_legend(matches_mm, legend_rect)
    LegendParserStage(_StubVLM()).run(ctx_mm)
    assert ctx_mm.legend is not None
    assert ctx_mm.legend.units == "mm"

    # No unit declaration → unknown.
    matches_none = [_match("RANDOM TEXT", x=420, y=120, w=120)]
    ctx_none = _ctx_with_legend(matches_none, legend_rect)
    LegendParserStage(_StubVLM()).run(ctx_none)
    assert ctx_none.legend is not None
    assert ctx_none.legend.units == "unknown"


def test_legend_parse_glyph_fallback() -> None:
    """A row band inside the legend rect with no OCR matches calls the VLM.

    Layout: legend rect spans y=100..400. One textual row sits at y=120;
    the rest of the legend has no OCR matches, so the slab-walk finds
    several empty bands and asks the VLM about them. We assert at least
    one symbols entry came back from the VLM and the call count is > 0.
    """
    legend_rect = (400.0, 100.0, 780.0, 400.0)
    # Single textual row anchors median height calculation; rest of the
    # legend rect is empty → empty bands → VLM fallback.
    matches = [
        _match("SA", x=420, y=120, w=30),
        _match("Supply Air", x=460, y=120, w=120),
    ]
    ctx = _ctx_with_legend(matches, legend_rect)
    vlm = _StubVLM(glyph_label="Round Duct")

    LegendParserStage(vlm).run(ctx)

    assert ctx.legend is not None
    assert vlm.glyph_calls >= 1
    # At least one symbol entry came back from the glyph fallback path —
    # all values are "Round Duct" because the stub returns one label.
    assert any(v == "Round Duct" for v in ctx.legend.symbols.values())


def test_legend_parse_no_legend_region() -> None:
    """ctx.layout.legend is None → ctx.legend = None, no error appended."""
    img = Image.new("RGB", (800, 600), color="white")
    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    ctx.source = _raster_source(img)
    ctx.ocr_cache = _ocr_cache([_match("SA", x=10, y=10)])
    # Layout exists but legend is None — categorizer didn't find a legend.
    ctx.layout = PageLayout(plan_view=(0.0, 0.0, 800.0, 600.0), legend=None)

    LegendParserStage(_StubVLM()).run(ctx)

    assert ctx.legend is None
    # Graceful skip — no legend_parse: error.
    assert not any(e.startswith("legend_parse:") for e in ctx.errors)


def test_legend_parse_failure_is_degradation() -> None:
    """Stage exception → ctx.legend=None; ``legend_parse:`` error appended.

    We trigger the failure by giving the parser a layout claiming a legend
    region but no source — the assertion in _parse fires and is caught by
    the stage's degradation handler.
    """
    ctx = PipelineContext(drawing_id="t", original_filename="t.png")
    ctx.ocr_cache = _ocr_cache([_match("SA", x=10, y=10)])
    ctx.layout = PageLayout(
        plan_view=(0.0, 0.0, 800.0, 600.0),
        legend=(100.0, 100.0, 400.0, 300.0),
    )
    ctx.source = None  # explicit — forces the assertion in _parse to fire

    LegendParserStage(_StubVLM()).run(ctx)

    assert ctx.legend is None
    assert any(e.startswith("legend_parse:") for e in ctx.errors)
