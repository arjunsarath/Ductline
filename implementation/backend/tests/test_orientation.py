"""Auto-orientation detection (V2 §5.8.3).

Pure-function tests on app.pipeline.orientation. No live OCR — fake page
objects with hand-crafted text spans, fake OCR matches with hand-crafted
bboxes. The integration with IngestStage / ProbeOCRStage is exercised
indirectly by the existing pipeline tests; here we lock the heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from app.pipeline.orientation import (
    detect_rotation_from_image,
    detect_rotation_from_text_layer,
    resolve_rotation_direction,
)

# ── Fake page objects matching pymupdf.Page.get_text("dict") shape. ──────────


@dataclass
class _FakePage:
    spans: list[dict]

    def get_text(self, _what: str) -> dict:
        return {
            "blocks": [
                {"lines": [{"spans": self.spans}]},
            ],
        }


def _span(text: str, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"text": text, "bbox": (x0, y0, x1, y1)}


def test_text_layer_canonical_returns_zero() -> None:
    """Wide-text spans → already canonical → rotation 0."""
    page = _FakePage(
        [
            _span("DESCRIPTION", 100.0, 50.0, 200.0, 65.0),  # 100 × 15
            _span("DUCT SCHEDULE", 100.0, 80.0, 220.0, 95.0),  # 120 × 15
            _span("PROJECT NAME", 100.0, 110.0, 210.0, 125.0),  # 110 × 15
            _span("LEGEND", 300.0, 50.0, 360.0, 65.0),  # 60 × 15
        ],
    )

    assert detect_rotation_from_text_layer(page) == 0


def test_text_layer_rotated_returns_90() -> None:
    """Narrow-tall spans (drawing rotated 90° within page) → rotation 90."""
    page = _FakePage(
        [
            _span("OFFICE PARTITIONING HVAC LAYOUT", 503.0, 211.0, 528.0, 628.0),  # 25 × 417
            _span("GAS REFRIGERANT PIPE", 379.0, 732.0, 383.0, 759.0),  # 4 × 27
            _span("FRESH AIR SUPPLY DUCT", 391.0, 732.0, 395.0, 760.0),  # 4 × 28
            _span("ME-03", 61.0, 766.0, 73.0, 798.0),  # 12 × 32
        ],
    )

    assert detect_rotation_from_text_layer(page) == 90


def test_text_layer_short_spans_skipped() -> None:
    """Spans of < 4 chars don't count — they're noise / glyph fragments."""
    page = _FakePage(
        [
            _span("A", 10.0, 10.0, 12.0, 25.0),  # would vote vertical, skipped
            _span("B", 30.0, 10.0, 32.0, 25.0),  # ditto
            _span("WIDE TEXT", 50.0, 10.0, 200.0, 25.0),  # the only counted span
        ],
    )

    assert detect_rotation_from_text_layer(page) == 0


def test_text_layer_ambiguous_returns_zero() -> None:
    """Margin-fail (no clear majority) → no rotation applied (fail open)."""
    page = _FakePage(
        [
            _span("DESCRIPTION", 100.0, 50.0, 200.0, 65.0),
            _span("DUCT SCHEDULE", 100.0, 80.0, 220.0, 95.0),
            _span("OFFICE LAYOUT", 503.0, 211.0, 528.0, 350.0),
            _span("GAS REFRIGERANT", 379.0, 732.0, 383.0, 800.0),
        ],
    )

    # 2 horizontal vs 2 vertical — below the 1.5× margin → 0 (no rotation)
    assert detect_rotation_from_text_layer(page) == 0


def test_text_layer_empty_returns_zero() -> None:
    """No spans → no signal → rotation 0 (don't fabricate a vote)."""
    page = _FakePage([])
    assert detect_rotation_from_text_layer(page) == 0


# ── OCR-path detection. Same logic, different bbox shape. ────────────────────


@dataclass
class _FakeOCRMatch:
    text: str
    bbox: tuple[int, int, int, int]
    confidence: float = 1.0


class _FakeOCR:
    def __init__(self, matches: list[_FakeOCRMatch]) -> None:
        self._matches = matches

    def extract_text(self, _image) -> list[_FakeOCRMatch]:
        return self._matches


def test_image_canonical_returns_zero() -> None:
    """OCR matches with w >> h → rotation 0."""
    ocr = _FakeOCR(
        [
            _FakeOCRMatch("DESCRIPTION", (100, 50, 200, 18)),  # w=200, h=18
            _FakeOCRMatch("FRESH AIR", (100, 80, 150, 18)),
            _FakeOCRMatch("ME-03", (50, 110, 80, 20)),
        ],
    )
    # Image arg unused by the fake OCR; pass None
    assert detect_rotation_from_image(None, ocr) == 0  # type: ignore[arg-type]


def test_image_rotated_returns_90() -> None:
    """OCR matches with h >> w → rotation 90."""
    ocr = _FakeOCR(
        [
            _FakeOCRMatch("OFFICE PARTITIONING", (503, 211, 25, 417)),
            _FakeOCRMatch("GAS REFRIGERANT", (379, 732, 4, 27)),
            _FakeOCRMatch("FRESH AIR DUCT", (391, 732, 4, 28)),
            _FakeOCRMatch("ME-03", (61, 766, 12, 32)),
        ],
    )
    assert detect_rotation_from_image(None, ocr) == 90  # type: ignore[arg-type]


# ── Direction resolution. ────────────────────────────────────────────────────
#
# The aspect-ratio vote can detect "rotated" but not "which way" — both
# 90° CW and 270° CW produce vertical bboxes. resolve_rotation_direction
# renders the source at each candidate and OCRs the result; word-like
# match counts pick the winner. The fakes below let us steer the OCR
# output per candidate without spinning up RapidOCR.


class _DirectionalFakeOCR:
    """OCR stub that returns different match lists for different inputs.

    The image is keyed by ``id()`` since PIL.Image equality is heavy and
    we want strict per-render scoring. Built via ``register(image,
    matches)`` — each candidate render gets its own match list.
    """

    def __init__(self) -> None:
        self._by_id: dict[int, list[_FakeOCRMatch]] = {}

    def register(self, image: Image.Image, matches: list[_FakeOCRMatch]) -> None:
        self._by_id[id(image)] = matches

    def extract_text(self, image: Image.Image) -> list[_FakeOCRMatch]:
        return self._by_id.get(id(image), [])


def _word_matches(words: list[str]) -> list[_FakeOCRMatch]:
    return [
        _FakeOCRMatch(text=w, bbox=(0, 0, max(len(w) * 6, 1), 14)) for w in words
    ]


def test_resolves_to_270_when_270_yields_more_words() -> None:
    """The 270° render produces real words; 90° produces gibberish/empty.

    Drives ``resolve_rotation_direction`` past the 1.3× margin to 270.
    """
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    # The PIL renders at 90 / 270 are different objects; intercept them
    # by monkey-patching the rotate call to register fixed images.
    img_90 = Image.new("RGB", (10, 10), (200, 200, 200))
    img_270 = Image.new("RGB", (10, 10), (100, 100, 100))

    def fake_rotate(angle: int, expand: bool = False) -> Image.Image:
        # PIL's rotate is CCW; -90 → CW 90, -270 → CW 270.
        del expand
        return img_90 if angle == -90 else img_270

    base.rotate = fake_rotate  # type: ignore[method-assign]

    ocr = _DirectionalFakeOCR()
    ocr.register(base, _word_matches(["12", "34"]))  # rot=0 noise — non-word
    ocr.register(img_90, _word_matches(["xy", "1234"]))  # gibberish/numeric
    ocr.register(
        img_270,
        _word_matches(["DUCT", "SCHEDULE", "PROJECT", "LEGEND", "OFFICE"]),
    )

    assert resolve_rotation_direction(base, ocr, [0, 90, 270]) == 270


def test_resolves_to_90_when_90_yields_more_words() -> None:
    """Mirror — the 90° render wins by margin."""
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    img_90 = Image.new("RGB", (10, 10), (200, 200, 200))
    img_270 = Image.new("RGB", (10, 10), (100, 100, 100))

    def fake_rotate(angle: int, expand: bool = False) -> Image.Image:
        del expand
        return img_90 if angle == -90 else img_270

    base.rotate = fake_rotate  # type: ignore[method-assign]

    ocr = _DirectionalFakeOCR()
    ocr.register(base, _word_matches(["12"]))
    ocr.register(
        img_90,
        _word_matches(["DUCT", "SCHEDULE", "PROJECT", "LEGEND", "OFFICE"]),
    )
    ocr.register(img_270, _word_matches(["xy", "1234"]))

    assert resolve_rotation_direction(base, ocr, [0, 90, 270]) == 90


def test_resolves_to_zero_on_close_tie() -> None:
    """Within the 1.3× margin → fail open and return 0."""
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    img_90 = Image.new("RGB", (10, 10), (200, 200, 200))
    img_270 = Image.new("RGB", (10, 10), (100, 100, 100))

    def fake_rotate(angle: int, expand: bool = False) -> Image.Image:
        del expand
        return img_90 if angle == -90 else img_270

    base.rotate = fake_rotate  # type: ignore[method-assign]

    ocr = _DirectionalFakeOCR()
    # Canonical has real word-like matches too — close enough to the
    # rotated candidates that the canonical-dominated rule doesn't
    # kick in (canonical=2, best=3, ratio is 1.5× < 5×). 90 and 270
    # also tie within 1.3× margin → fail-open path returns 0.
    ocr.register(base, _word_matches(["LEGEND", "OFFICE"]))
    ocr.register(img_90, _word_matches(["DUCT", "SCHEDULE", "PROJECT"]))
    ocr.register(img_270, _word_matches(["NOTES", "PLAN", "TITLE"]))

    assert resolve_rotation_direction(base, ocr, [0, 90, 270]) == 0


def test_canonical_dominated_picks_best_rotated_on_tight_margin() -> None:
    """When canonical (rot=0) clearly loses but 90 vs 270 are close,
    pick the higher-scored rotation rather than failing open.

    This is the drawing-01 case in the benchmark: scores were
    {0:1, 90:25, 270:26}. Within the 1.3× margin (270/25 = 1.04×) but
    canonical (1) is 25× smaller than the best rotated (26) — clearly
    rotated, just unclear which way. The rule picks 270 (the winner)
    so the drawing isn't left un-rotated when no-rotation is the
    worst possible answer.
    """
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    img_90 = Image.new("RGB", (10, 10), (200, 200, 200))
    img_270 = Image.new("RGB", (10, 10), (100, 100, 100))

    def fake_rotate(angle: int, expand: bool = False) -> Image.Image:
        del expand
        return img_90 if angle == -90 else img_270

    base.rotate = fake_rotate  # type: ignore[method-assign]

    ocr = _DirectionalFakeOCR()
    # canonical: 0 word-like matches (numeric only, filtered out).
    ocr.register(base, _word_matches(["12"]))
    # 90 vs 270 within margin (10 vs 12 = 1.2×) — but both dwarf canonical.
    ocr.register(
        img_90,
        _word_matches(["DUCT", "GRILLE", "SUPPLY", "RETURN", "PLENUM",
                       "EXHAUST", "RIGID", "FLEX", "INTAKE", "OFFSET"]),
    )
    ocr.register(
        img_270,
        _word_matches(["DUCT", "GRILLE", "SUPPLY", "RETURN", "PLENUM",
                       "EXHAUST", "RIGID", "FLEX", "INTAKE", "OFFSET",
                       "DAMPER", "RISER"]),
    )

    assert resolve_rotation_direction(base, ocr, [0, 90, 270]) == 270
