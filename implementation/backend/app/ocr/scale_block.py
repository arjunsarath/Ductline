"""Title-block scale extraction (SOLUTION-DESIGN-V4 §3).

Returns ``None`` when the title block is missing, illegible, or the scale text
does not match the supported ``N" = 1'-0"`` family. The caller falls back to a
user-entered scale in that case.
"""

from __future__ import annotations

import re
from fractions import Fraction

from PIL import Image

from app.ocr.base import Bbox, OCRExtractor
from app.ocr.rapid import RapidOCRExtractor
from app.schemas import ScaleInfo

# Title blocks in architectural sheets sit on the right edge or bottom edge
# of the page. We OCR the right 25% strip; that captures the typical
# right-side title block on landscape sheets and still includes a bottom
# title block's right portion. Extending the strip costs OCR time without
# meaningful recall gain on the supported sheet conventions.
_TITLE_BLOCK_RIGHT_FRACTION = 0.25

# Architectural-scale grammar: a fraction of an inch equals one foot.
# Tolerates common typographic variants (smart quotes, hyphen vs en-dash,
# whitespace, apostrophe vs prime, missing inch mark on the LHS).
_SCALE_PATTERN = re.compile(
    r"""
    (?P<num>\d+)\s*/\s*(?P<den>\d+)   # fraction like 1/4
    \s*["”″*°]?             # optional inch mark — OCR commonly mis-reads " as * or °
    \s*=\s*
    1\s*['’′`*°"”″]         # 1 foot mark — same OCR-tolerance set
    \s*[-–—]?\s*
    0\s*["”″*°]?            # 0 inches
    """,
    re.VERBOSE,
)

# OCR text confidence threshold below which we treat the read as unreliable
# and decline to return a scale rather than guess.
_MIN_OCR_CONFIDENCE = 0.5


def read_title_block_scale(
    image: Image.Image, ocr: OCRExtractor | None = None
) -> ScaleInfo | None:
    """OCR the title block region and parse a paper-inches-per-foot ratio.

    The optional ``ocr`` argument lets tests inject a stub; production
    callers can omit it and let RapidOCR construct lazily.
    """
    engine = ocr if ocr is not None else RapidOCRExtractor()
    region = _title_block_region(image.size)
    matches = engine.extract_text(image, region)
    if not matches:
        return None

    best: tuple[float, float] | None = None  # (paper_inches_per_foot, confidence)
    for match in matches:
        if match.confidence < _MIN_OCR_CONFIDENCE:
            continue
        parsed = _parse_scale(match.text)
        if parsed is None:
            continue
        if best is None or match.confidence > best[1]:
            best = (parsed, match.confidence)

    if best is None:
        return None
    return ScaleInfo(
        paper_inches_per_foot=best[0],
        source="title_block",
        confidence=best[1],
    )


def _title_block_region(size: tuple[int, int]) -> Bbox:
    width, height = size
    strip_w = max(1, int(width * _TITLE_BLOCK_RIGHT_FRACTION))
    return (width - strip_w, 0, strip_w, height)


def _parse_scale(text: str) -> float | None:
    """Return paper inches per foot, or None if no supported pattern matches."""
    match = _SCALE_PATTERN.search(text)
    if match is None:
        return None
    num = int(match.group("num"))
    den = int(match.group("den"))
    if den == 0:
        return None
    return float(Fraction(num, den))
