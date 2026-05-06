"""Find every duct cross-section dimension token on a cleaned drawing.

Runs OCR once on the post-grey-removal raster and returns each match that
parses as a duct dimension grammar (``N"ø`` or ``WxH``). Used as a debug
overlay so the operator can see which dimension labels OCR caught.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from PIL import Image

from app.ocr.base import OCRExtractor

DimensionKind = Literal["round", "rectangular"]

# RapidOCR mangles the round-duct mark `ø` into a wide variety of look-alike
# glyphs (`e`, `°`, `#`, `*`, `0`, `o`, `′`). We accept any of them for the
# debug overlay so visibly-wrong OCR still produces a highlight the operator
# can see and map back to a real duct.
_OE_CHARS = r"[øØ⌀0oOe°#*'′`]"
# Inch mark variants (real `"`, OCR'd to `'`, `′`, backtick, asterisk).
_INCH_OPT = r"[\"'′`*]?"
_ROUND_RE = re.compile(
    rf"^\s*(\d{{1,2}})\s*{_INCH_OPT}\s*{_OE_CHARS}+\s*$", re.IGNORECASE,
)
_RECT_RE = re.compile(
    rf"^\s*(\d{{1,2}})\s*{_INCH_OPT}\s*x\s*(\d{{1,2}})\s*{_INCH_OPT}\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DimensionMatch:
    text: str
    kind: DimensionKind
    bbox: tuple[int, int, int, int]  # (x, y, w, h) in raster pixels


def find_dimension_text(
    image: Image.Image, ocr: OCRExtractor,
) -> list[DimensionMatch]:
    """Return every OCR token that parses as a duct cross-section dimension."""
    out: list[DimensionMatch] = []
    for match in ocr.extract_text(image):
        text = match.text.strip()
        if _RECT_RE.match(text):
            out.append(DimensionMatch(text=text, kind="rectangular", bbox=match.bbox))
        elif _ROUND_RE.match(text):
            out.append(DimensionMatch(text=text, kind="round", bbox=match.bbox))
    return out
