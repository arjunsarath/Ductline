"""Auto-orientation detection (V2 §5.8.3).

Pure-function tests on app.pipeline.orientation. No live OCR — fake page
objects with hand-crafted text spans, fake OCR matches with hand-crafted
bboxes. The integration with IngestStage / ProbeOCRStage is exercised
indirectly by the existing pipeline tests; here we lock the heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.pipeline.orientation import (
    detect_rotation_from_image,
    detect_rotation_from_text_layer,
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
