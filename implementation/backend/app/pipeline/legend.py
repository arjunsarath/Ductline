"""Legend — output of the Legend Parser (SOLUTION-DESIGN-V2 §5.4, §6.1).

The shape ships ahead of the stage so PR-5 (Tiled Detect) can be developed
in parallel: ``ctx.legend`` is allowed to be None throughout v2 (the parser
is a P1 amplifier, not a precondition), and downstream consumers must
handle that None gracefully.

Locked decisions (SOLUTION-DESIGN-V2 §5.4, §7):

  • Two-pass parse. OCR for textual rows first (regex-grouped from
    ``ctx.ocr_cache.matches`` filtered to those inside ``ctx.layout.legend``),
    VLM glyph fallback for rows where OCR found no matches.

  • Row grouping. Cluster OCR matches inside the legend rect into
    horizontal rows by y-coordinate. Use an adaptive band ~1.5× median
    match height — legend rows are roughly horizontal but heights vary
    with font size and OCR jitter.

  • Row-to-field mapping. Each row's text is parsed into one of
    ``line_styles``, ``symbols``, ``abbreviations``, ``units``. The
    abbreviation-vs-symbol tie-break: leading token all-uppercase 2–6
    chars → abbreviation; otherwise symbol. Documented inline.

  • VLM glyph fallback. For row bands inside the legend rect with no
    OCR matches, crop the row pixel rect from ``ctx.source.raster_probe``
    and call ``vlm.disambiguate_region(crop, "What is this symbol or
    line style? Reply with one short label, no prose.")``. Capped at
    6 calls per drawing — legend rows are usually ≤ 12, this protects
    the per-drawing VLM budget.

  • Failure posture. Any exception in run() leaves ``ctx.legend = None``
    and appends ``legend_parse: <reason>`` to ``ctx.errors``. Mirrors
    ``probe_ocr`` and ``page_categorize``.

  • Graceful skip when no legend region. If ``ctx.layout`` is None
    (categorizer degraded) or ``ctx.layout.legend`` is None (no legend
    identified) we leave ``ctx.legend = None`` and emit a single info
    log line — this is the §7 spec'd behaviour, not an error.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.ocr.base import OCRMatch
from app.pipeline.base import PipelineContext, PipelineStage
from app.source.base import DrawingSource, RectPt
from app.vlm.base import VLMClient

logger = logging.getLogger(__name__)


class Legend(BaseModel):
    """Drawing-specific symbol / abbreviation / line-style conventions.

    Populated by ``LegendParserStage`` (PR-4) when a legend region is
    identified; left as None when no legend exists or the parser
    degrades. Detector and reviewer prompts include this dictionary as
    LEGEND CONTEXT to ground their output in the conventions of the
    specific drawing under analysis.
    """

    line_styles: dict[str, str] = Field(default_factory=dict)
    symbols: dict[str, str] = Field(default_factory=dict)
    abbreviations: dict[str, str] = Field(default_factory=dict)
    units: Literal["inches", "mm", "unknown"] = "unknown"


# Row-band heuristic: cluster OCR matches whose y-tops differ by less than
# this multiple of the median match height. 1.5 is wide enough to absorb
# OCR jitter and ascender/descender variance, narrow enough to keep
# vertically-stacked legend rows separate.
_ROW_BAND_HEIGHT_MULTIPLIER = 1.5

# Glyph-fallback budget. Legend rows are usually ≤ 12; capping at 6 VLM
# calls protects the per-drawing budget while still catching the common
# "couple of pure-glyph rows" case.
_GLYPH_FALLBACK_CAP = 6

# Abbreviation tie-break: a leading token that is all-uppercase and 2–6
# characters is treated as an abbreviation (e.g. SA, RA, VAV, CFM).
# Outside that band the row is classified as a symbol — single glyph
# tokens (R, ⌀) and longer all-caps phrases ("RETURN AIR") fall through
# to the symbol bucket where the key/value framing is more forgiving.
_ABBREV_PATTERN = re.compile(r"^([A-Z]{2,6})\b\s*[-–—:]?\s*(.+)$")

# Line-style rows lead with a non-alphanumeric "glyph" token (a dash run,
# arrow, or ASCII art) and a label. We accept any leading run of one or
# more non-alphanumeric characters followed by whitespace and an
# alphanumeric label.
_LINE_STYLE_PATTERN = re.compile(r"^([^\w\s]{1,8})\s+([A-Za-z][\w\s/-]+)$")

# Symbol rows lead with a single glyph or short token (1–3 chars) and a
# label. The token may be alphanumeric (R for "Return Grille") or
# non-alphanumeric (⌀ for "Round Duct").
_SYMBOL_PATTERN = re.compile(r"^(\S{1,3})\s+([A-Za-z][\w\s/-]+)$")

# Unit declarations on legends typically read "DIMENSIONS IN INCHES" or
# similar. We scan the whole legend text blob, not individual rows,
# because the declaration is often a header outside the row grid.
_UNIT_INCH_PATTERNS = (
    re.compile(r"\bINCHES\b"),
    re.compile(r"\bINCH\b"),
    re.compile(r'(?<=\d)"'),  # 14" — a dimension callout, implies inches
)
_UNIT_MM_PATTERNS = (
    re.compile(r"\bMILLIMETERS?\b"),
    re.compile(r"\bMM\b"),
)


class LegendParserStage(PipelineStage):
    """Parse the legend region into a typed Legend (SOLUTION-DESIGN-V2 §5.4)."""

    name = "legend_parse"

    def __init__(self, vlm: VLMClient) -> None:
        # Per SOLUTION-DESIGN-V2 §6.1: stages take engines/clients only.
        # OCR cache and layout are read at run() time from ctx.
        self._vlm = vlm

    def run(self, ctx: PipelineContext) -> PipelineContext:
        try:
            ctx.legend = self._parse(ctx)
        except Exception as exc:  # noqa: BLE001 — degradation by design (§7)
            logger.exception("legend_parse failed")
            ctx.legend = None
            ctx.errors.append(f"legend_parse: {exc}")
        return ctx

    # ── Top-level parse ──────────────────────────────────────────────────────

    def _parse(self, ctx: PipelineContext) -> Legend | None:
        assert ctx.source is not None, "ingest must run before legend_parse"

        # Graceful skips per §7 — no legend region means "use defaults",
        # not "fail". Mirrors the reviewer/detector contract that None
        # is a valid Legend value.
        if ctx.layout is None or ctx.layout.legend is None:
            logger.info("legend_parse: no legend region identified; skipping")
            return None
        if ctx.ocr_cache is None:
            logger.info("legend_parse: ocr_cache absent; skipping")
            return None

        legend_rect_pt = ctx.layout.legend
        legend_rect_px = _source_rect_to_pixel(legend_rect_pt, ctx.source)

        # Pass 1: OCR rows inside the legend rect.
        contained = _filter_matches_in_rect(ctx.ocr_cache.matches, legend_rect_px)
        rows = _group_into_rows(contained)
        logger.info(
            "legend_parse: %d OCR matches inside legend; %d rows grouped",
            len(contained),
            len(rows),
        )

        line_styles: dict[str, str] = {}
        symbols: dict[str, str] = {}
        abbreviations: dict[str, str] = {}

        # Concatenate the full legend text once for unit detection — the
        # "DIMENSIONS IN INCHES" line is often a header outside the row grid.
        full_text = " ".join(m.text for m in contained)
        units = _detect_units(full_text)

        for row in rows:
            row_text = _row_text(row)
            kind, key, value = _classify_row(row_text)
            if kind == "line_style" and key and value:
                line_styles[key] = value
            elif kind == "symbol" and key and value:
                symbols[key] = value
            elif kind == "abbreviation" and key and value:
                abbreviations[key] = value
            # "unit" rows are absorbed into the global scan above; "none"
            # rows are intentionally dropped (legend headers, page text
            # bleed, OCR noise).

        # Pass 2: VLM glyph fallback for empty row bands inside the legend.
        glyph_results = self._glyph_fallback(ctx, legend_rect_px, contained)
        symbols.update(glyph_results)

        return Legend(
            line_styles=line_styles,
            symbols=symbols,
            abbreviations=abbreviations,
            units=units,
        )

    # ── VLM glyph fallback ───────────────────────────────────────────────────

    def _glyph_fallback(
        self,
        ctx: PipelineContext,
        legend_rect_px: tuple[int, int, int, int],
        contained: list[OCRMatch],
    ) -> dict[str, str]:
        """Identify row bands inside the legend with no OCR matches and ask the VLM.

        We slice the legend region into horizontal slabs of approximately
        median match height and pick out slabs that contain no OCR match
        top-left. Each surviving slab is rendered from the source and
        passed to ``vlm.disambiguate_region``. Capped at
        ``_GLYPH_FALLBACK_CAP`` calls to protect the per-drawing budget.
        """
        # Need a height reference to know how thick a "row band" is. Without
        # any OCR matches inside the legend we can't infer the row pitch,
        # so we skip the fallback rather than guess.
        if not contained:
            return {}

        median_h = _median_match_height(contained)
        if median_h <= 0:
            return {}

        empty_bands = _find_empty_bands(legend_rect_px, contained, median_h)
        if not empty_bands:
            return {}

        results: dict[str, str] = {}
        # Convert each pixel band back to source coordinates so render()
        # can re-render at the right DPI for vector PDFs.
        for index, band_px in enumerate(empty_bands[:_GLYPH_FALLBACK_CAP]):
            band_pt = _pixel_rect_to_source(band_px, ctx.source)
            try:
                crop = ctx.source.render(band_pt, dpi=200)
                label = self._vlm.disambiguate_region(
                    crop,
                    "What is this symbol or line style? Reply with one short label, no prose.",
                )
            except Exception as exc:  # noqa: BLE001 — VLM degradation is non-fatal
                logger.warning("legend_parse: glyph VLM call failed: %s", exc)
                continue
            label = label.strip()
            if not label:
                continue
            results[f"glyph_{index}"] = label
        return results


# ── Coordinate helpers (mirror app.pipeline.categorize) ──────────────────────


def _source_rect_to_pixel(
    rect_pt: RectPt, source: DrawingSource
) -> tuple[int, int, int, int]:
    """Convert source-space rect (points or pixels) to raster_probe pixel coords.

    OCRMatch.bbox is always in raster_probe pixel space, so containment
    checks must be in pixel space too. For raster sources the rect is
    already pixel coords (RectPt is the same tuple type).
    """
    x0, y0, x1, y1 = rect_pt
    if source.kind != "vector_pdf" or source.page_size_pt is None:
        return (int(x0), int(y0), int(x1), int(y1))
    pw, ph = source.raster_probe.size
    page_w, page_h = source.page_size_pt
    sx = pw / page_w
    sy = ph / page_h
    return (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))


def _pixel_rect_to_source(
    rect_px: tuple[int, int, int, int], source: DrawingSource
) -> RectPt:
    """Inverse of _source_rect_to_pixel — used to render bands back from pixels."""
    x0, y0, x1, y1 = rect_px
    if source.kind != "vector_pdf" or source.page_size_pt is None:
        return (float(x0), float(y0), float(x1), float(y1))
    pw, ph = source.raster_probe.size
    page_w, page_h = source.page_size_pt
    sx = page_w / pw
    sy = page_h / ph
    return (x0 * sx, y0 * sy, x1 * sx, y1 * sy)


# ── OCR filtering / row grouping ─────────────────────────────────────────────


def _filter_matches_in_rect(
    matches: list[OCRMatch], rect_px: tuple[int, int, int, int]
) -> list[OCRMatch]:
    """Return matches whose top-left lies inside the rectangle.

    Top-left containment matches the categorizer's convention — a stable
    single test that doesn't false-negative on matches clipped at the
    rect's right/bottom edges.
    """
    x0, y0, x1, y1 = rect_px
    contained: list[OCRMatch] = []
    for m in matches:
        mx, my, _, _ = m.bbox
        if x0 <= mx < x1 and y0 <= my < y1:
            contained.append(m)
    return contained


def _median_match_height(matches: list[OCRMatch]) -> float:
    """Median bbox height — used to size the row band and the glyph slabs."""
    if not matches:
        return 0.0
    heights = sorted(float(m.bbox[3]) for m in matches if m.bbox[3] > 0)
    if not heights:
        return 0.0
    return heights[len(heights) // 2]


def _group_into_rows(matches: list[OCRMatch]) -> list[list[OCRMatch]]:
    """Cluster OCR matches into horizontal rows by y-coordinate.

    Rows are roughly horizontal on a legend; we sort by y, then start a
    new row whenever the next match's y-top exceeds the current row's
    band ceiling. Within each row, matches are sorted left-to-right so
    ``_row_text`` reads naturally.
    """
    if not matches:
        return []
    median_h = _median_match_height(matches)
    band = max(median_h * _ROW_BAND_HEIGHT_MULTIPLIER, 1.0)

    by_y = sorted(matches, key=lambda m: (m.bbox[1], m.bbox[0]))
    rows: list[list[OCRMatch]] = []
    current_row: list[OCRMatch] = []
    current_top: float | None = None
    for m in by_y:
        y_top = float(m.bbox[1])
        if current_top is None or y_top - current_top <= band:
            current_row.append(m)
            if current_top is None:
                current_top = y_top
        else:
            rows.append(sorted(current_row, key=lambda mm: mm.bbox[0]))
            current_row = [m]
            current_top = y_top
    if current_row:
        rows.append(sorted(current_row, key=lambda mm: mm.bbox[0]))
    return rows


def _row_text(row: list[OCRMatch]) -> str:
    return " ".join(m.text for m in row).strip()


# ── Row classification ───────────────────────────────────────────────────────


def _classify_row(
    text: str,
) -> tuple[
    Literal["line_style", "symbol", "abbreviation", "unit", "none"], str | None, str | None
]:
    """Map a row's concatenated text to (kind, key, value).

    Heuristic order — the most specific patterns first. Abbreviations
    win the symbol tie-break when the leading token is all-uppercase 2–6
    chars; otherwise the symbol pattern claims the row. Line styles
    require a non-alphanumeric leading token, distinguishing them from
    short-glyph symbols.
    """
    text = text.strip()
    if not text:
        return "none", None, None

    # Line style: leading non-alphanumeric run + label.
    line_match = _LINE_STYLE_PATTERN.match(text)
    if line_match:
        return "line_style", line_match.group(1), line_match.group(2).strip()

    # Abbreviation: leading 2–6 char ALL-CAPS token + label.
    abbrev_match = _ABBREV_PATTERN.match(text)
    if abbrev_match:
        return "abbreviation", abbrev_match.group(1), abbrev_match.group(2).strip()

    # Symbol: short leading token + label.
    sym_match = _SYMBOL_PATTERN.match(text)
    if sym_match:
        return "symbol", sym_match.group(1), sym_match.group(2).strip()

    return "none", None, None


def _detect_units(text: str) -> Literal["inches", "mm", "unknown"]:
    """Scan a text blob for unit declarations.

    Inches takes precedence on tie because mixed-unit drawings in the US
    market overwhelmingly default to inches; the explicit MM declaration
    is only honoured when no inch indicator is present.
    """
    upper = text.upper()
    has_inch = any(p.search(upper) for p in _UNIT_INCH_PATTERNS)
    has_mm = any(p.search(upper) for p in _UNIT_MM_PATTERNS)
    if has_inch:
        return "inches"
    if has_mm:
        return "mm"
    return "unknown"


# ── Empty-band detection for glyph fallback ──────────────────────────────────


def _find_empty_bands(
    legend_rect_px: tuple[int, int, int, int],
    matches: list[OCRMatch],
    median_h: float,
) -> list[tuple[int, int, int, int]]:
    """Slice the legend into row-height slabs and return slabs containing no OCR.

    Slab height is twice the median match height — wide enough to enclose
    a typical legend row's glyph + label, narrow enough that two adjacent
    text rows don't end up sharing a slab. Slabs whose top-left coverage
    contains an OCR match are filtered out; the remainder are the
    candidate glyph rows for the VLM fallback.
    """
    x0, y0, x1, y1 = legend_rect_px
    slab_h = max(int(median_h * 2.0), 1)
    if slab_h <= 0 or y1 <= y0:
        return []

    bands: list[tuple[int, int, int, int]] = []
    y = y0
    while y < y1:
        slab_top = y
        slab_bot = min(y + slab_h, y1)
        # A slab is "empty" if no OCR match's top-left lies within it.
        has_match = any(
            x0 <= m.bbox[0] < x1 and slab_top <= m.bbox[1] < slab_bot for m in matches
        )
        if not has_match:
            bands.append((x0, slab_top, x1, slab_bot))
        y += slab_h
    return bands


__all__ = ["Legend", "LegendParserStage"]
