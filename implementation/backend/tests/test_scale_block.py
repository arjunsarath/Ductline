"""Tests for title-block scale extraction.

Real OCR is slow and downloads model weights on first call, so most tests use
an injected stub extractor. One end-to-end test runs against ``testset2.pdf``
to verify the full path including title-block cropping and OCR.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.cv.preprocess_v4 import rasterize_pdf, remove_grey_fill
from app.ocr.base import Bbox, OCRExtractor, OCRMatch
from app.ocr.scale_block import read_title_block_scale

_TESTSET2 = Path(__file__).resolve().parents[2] / "drawings" / "testset2.pdf"


class _StubOCR(OCRExtractor):
    def __init__(self, matches: list[OCRMatch]) -> None:
        self._matches = matches
        self.last_region: Bbox | None = None

    def extract_text(
        self, image: Image.Image, region: Bbox | None = None
    ) -> list[OCRMatch]:
        self.last_region = region
        return self._matches

    def extract_table(self, image: Image.Image, region: Bbox):
        raise NotImplementedError


def test_read_scale_quarter_inch() -> None:
    image = Image.new("RGB", (1000, 800), color="white")
    stub = _StubOCR(
        [OCRMatch(text='SCALE: 1/4" = 1\'-0"', bbox=(0, 0, 200, 30), confidence=0.9)]
    )

    info = read_title_block_scale(image, ocr=stub)

    assert info is not None
    assert info.paper_inches_per_foot == pytest.approx(0.25)
    assert info.source == "title_block"
    assert info.confidence == pytest.approx(0.9)
    # Title-block crop is the right strip.
    assert stub.last_region is not None
    x, _, w, _ = stub.last_region
    assert x + w == 1000


def test_read_scale_eighth_inch_with_smart_quotes() -> None:
    image = Image.new("RGB", (1000, 800), color="white")
    stub = _StubOCR(
        [OCRMatch(text="3/8” = 1’-0”", bbox=(0, 0, 200, 30), confidence=0.85)]
    )

    info = read_title_block_scale(image, ocr=stub)

    assert info is not None
    assert info.paper_inches_per_foot == pytest.approx(0.375)


def test_read_scale_returns_none_when_no_match() -> None:
    image = Image.new("RGB", (1000, 800), color="white")
    stub = _StubOCR(
        [
            OCRMatch(text="DO NOT SCALE DRAWINGS", bbox=(0, 0, 300, 30), confidence=0.95),
            OCRMatch(text="ISSUED FOR PERMIT", bbox=(0, 50, 200, 30), confidence=0.92),
        ]
    )

    assert read_title_block_scale(image, ocr=stub) is None


def test_read_scale_ignores_low_confidence_matches() -> None:
    image = Image.new("RGB", (1000, 800), color="white")
    stub = _StubOCR(
        [OCRMatch(text='1/4" = 1\'-0"', bbox=(0, 0, 200, 30), confidence=0.3)]
    )

    assert read_title_block_scale(image, ocr=stub) is None


def test_read_title_block_scale_testset2() -> None:
    """End-to-end: rasterize, denoise, OCR title block of the real fixture."""
    image = rasterize_pdf(_TESTSET2, dpi=200)
    image = remove_grey_fill(image)

    info = read_title_block_scale(image)

    assert info is not None, "expected a scale read from testset2 title block"
    assert info.paper_inches_per_foot == pytest.approx(0.25)
    assert info.source == "title_block"
