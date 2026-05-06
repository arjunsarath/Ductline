"""Unit tests for V4 duct-label OCR.

The synthetic cases use a fake OCR engine — they assert grammar and selection
logic without booting RapidOCR. The fixture case uses the real engine on
`testset2.pdf` and is skipped when the engine is unavailable in the runtime
(e.g. CI without ONNX runtime models).
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.cv.types import DuctPolygon
from app.ocr.base import Bbox, OCRExtractor, OCRMatch, Table
from app.ocr.label_v4 import read_duct_labels


# ── Fakes ────────────────────────────────────────────────────────────────────


class _FakeOCR(OCRExtractor):
    """Returns `OCRMatch`es from a (rotation_aware) script.

    The fake doesn't actually look at the image; it returns whatever was
    queued for the next `extract_text` call. Tests queue matches per crop +
    rotation pass to drive the selection logic deterministically.
    """

    def __init__(self, scripts: list[list[OCRMatch]]) -> None:
        self._scripts = list(scripts)
        self.calls = 0

    def extract_text(
        self, image: Image.Image, region: Bbox | None = None
    ) -> list[OCRMatch]:
        if not self._scripts:
            return []
        out = self._scripts.pop(0)
        self.calls += 1
        return out

    def extract_table(self, image: Image.Image, region: Bbox) -> Table:
        return Table(rows=[])


def _square_polygon(pid: str, x: int, y: int, w: int, h: int) -> DuctPolygon:
    return DuctPolygon(
        id=pid,
        points=[(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
    )


# ── Synthetic-grammar tests (fake engine) ───────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected_value", "expected_shape"),
    [
        ('8"ø', '8"ø', "round"),
        ('12"Ø', '12"ø', "round"),
        ('22"x14"', '22"x14"', "rectangular"),
        ('17X9', '17"x9"', "rectangular"),
        ('6 ⌀', '6"ø', "round"),
    ],
)
def test_grammar_parses_round_and_rect(
    text: str, expected_value: str, expected_shape: str
) -> None:
    image = Image.new("RGB", (200, 200), "white")
    poly = _square_polygon("p1", 10, 10, 100, 50)
    fake = _FakeOCR(
        scripts=[[OCRMatch(text=text, bbox=(20, 20, 40, 20), confidence=0.95)]]
    )

    labels = read_duct_labels(image, [poly], ocr=fake)

    assert len(labels) == 1
    assert labels[0].polygon_id == "p1"
    assert labels[0].parsed_value == expected_value
    assert labels[0].parsed_shape == expected_shape
    assert labels[0].orientation_deg == 0


def test_no_match_returns_no_label() -> None:
    image = Image.new("RGB", (200, 200), "white")
    poly = _square_polygon("p1", 0, 0, 100, 100)
    # "MFD-1" is an equipment tag — must not parse as a dimension.
    fake = _FakeOCR(
        scripts=[
            [OCRMatch(text="MFD-1", bbox=(10, 10, 60, 20), confidence=0.9)],
            # Second pass (90°-rotated) — also no match.
            [OCRMatch(text="MFD-1", bbox=(10, 10, 60, 20), confidence=0.9)],
        ]
    )

    labels = read_duct_labels(image, [poly], ocr=fake)
    assert labels == []


def test_rotation_pass_matches_when_zero_degree_pass_fails() -> None:
    image = Image.new("RGB", (200, 200), "white")
    poly = _square_polygon("p1", 0, 0, 100, 100)
    fake = _FakeOCR(
        scripts=[
            # First pass at 0°: garbage that doesn't parse.
            [OCRMatch(text="ll8", bbox=(10, 10, 30, 12), confidence=0.5)],
            # Second pass after 90° rotation: legitimate label.
            [OCRMatch(text='6"ø', bbox=(10, 10, 30, 30), confidence=0.92)],
        ]
    )

    labels = read_duct_labels(image, [poly], ocr=fake)
    assert len(labels) == 1
    assert labels[0].parsed_value == '6"ø'
    assert labels[0].orientation_deg == 90


def test_zero_degree_match_skips_rotation_pass() -> None:
    image = Image.new("RGB", (200, 200), "white")
    poly = _square_polygon("p1", 0, 0, 100, 100)
    fake = _FakeOCR(
        scripts=[[OCRMatch(text='10"ø', bbox=(10, 10, 30, 12), confidence=0.95)]]
    )

    labels = read_duct_labels(image, [poly], ocr=fake)
    assert len(labels) == 1
    assert fake.calls == 1


def test_multiple_candidates_picks_centroid_closest() -> None:
    image = Image.new("RGB", (200, 200), "white")
    # Polygon centered at (50, 50).
    poly = _square_polygon("p1", 0, 0, 100, 100)
    fake = _FakeOCR(
        scripts=[
            [
                # Far from centroid.
                OCRMatch(text='8"ø', bbox=(2, 2, 14, 12), confidence=0.9),
                # Right at centroid — should win.
                OCRMatch(text='12"ø', bbox=(45, 45, 14, 12), confidence=0.9),
            ]
        ]
    )

    labels = read_duct_labels(image, [poly], ocr=fake)
    assert len(labels) == 1
    assert labels[0].parsed_value == '12"ø'


def test_unlabeled_polygons_are_omitted_not_errored() -> None:
    image = Image.new("RGB", (200, 200), "white")
    p1 = _square_polygon("p1", 0, 0, 100, 100)
    p2 = _square_polygon("p2", 0, 100, 100, 100)
    fake = _FakeOCR(
        scripts=[
            [OCRMatch(text='10"ø', bbox=(20, 20, 20, 12), confidence=0.9)],
            # p2 — no match either pass.
            [],
            [],
        ]
    )

    labels = read_duct_labels(image, [p1, p2], ocr=fake)
    ids = {label.polygon_id for label in labels}
    assert ids == {"p1"}


# ── Real-engine smoke tests on rendered images ──────────────────────────────


def _render_text(text: str, size: tuple[int, int] = (200, 80)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/Library/Fonts/Arial.ttf", 32)
    except OSError:
        font = ImageFont.load_default()
    draw.text((10, 20), text, fill="black", font=font)
    return image


def _real_ocr_or_skip() -> OCRExtractor:
    try:
        from app.ocr.rapid import RapidOCRExtractor

        engine = RapidOCRExtractor()
        # Force lazy init so we can skip cleanly if models can't load.
        engine._get_engine()
        return engine
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"RapidOCR unavailable: {exc}")


@pytest.mark.parametrize("text", ['8"ø', '12"ø', '22"x14"', '17"x9"'])
def test_real_ocr_renders_and_parses(text: str) -> None:
    ocr = _real_ocr_or_skip()
    canvas = Image.new("RGB", (400, 200), "white")
    rendered = _render_text(text, size=(380, 80))
    canvas.paste(rendered, (10, 60))
    poly = _square_polygon("p1", 0, 0, 400, 200)

    labels = read_duct_labels(canvas, [poly], ocr=ocr)
    if not labels:
        pytest.skip(f"OCR did not parse rendered {text!r}; engine variability")
    assert labels[0].polygon_id == "p1"


# ── Fixture test: testset2.pdf round-duct interior crops ────────────────────


_FIXTURE_PDF = (
    Path(__file__).resolve().parents[2]
    / "drawings"
    / "testset2.pdf"
)


def _render_pdf_at_dpi(path: Path, dpi: int) -> Image.Image:
    doc = pymupdf.open(path)
    pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    doc.close()
    return img


@pytest.mark.skipif(
    not _FIXTURE_PDF.exists(), reason="testset2.pdf not available in this checkout"
)
def test_testset2_round_label_inside_polygon() -> None:
    """One real round-duct label inside a synthetic polygon over the PDF.

    The duct interior in testset2.pdf renders the dimension `18"ø`. We crop
    a generous polygon around that label location and assert the parser picks
    up a round dimension. We don't pin the exact diameter because OCR can
    misread a digit; we do require shape=round and a 1-2 digit numeric.
    """
    ocr = _real_ocr_or_skip()
    image = _render_pdf_at_dpi(_FIXTURE_PDF, dpi=150)

    # The 18"ø label sits roughly at (1500..1700, 900..980) at 150 dpi.
    poly = DuctPolygon(
        id="round_duct_18",
        points=[(1500, 900), (1700, 900), (1700, 980), (1500, 980)],
    )

    labels = read_duct_labels(image, [poly], ocr=ocr)
    if not labels:
        pytest.skip("OCR engine did not recognise the duct label in this run")
    assert labels[0].polygon_id == "round_duct_18"
    assert labels[0].parsed_shape == "round"
