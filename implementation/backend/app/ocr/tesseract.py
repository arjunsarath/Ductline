"""Tesseract implementation of the OCRExtractor protocol.

Tesseract 5.x (LSTM) is dramatically better than the RapidOCR/PaddleOCR
default at sparse technical text — testset2.pdf produces ~890 readable
tokens vs. RapidOCR's ~80 mangled ones. The Mac binary at
``/opt/homebrew/bin/tesseract`` is wired via the ``pytesseract`` shim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytesseract

from app.ocr.base import Bbox, OCRExtractor, OCRMatch, Table

if TYPE_CHECKING:
    from PIL.Image import Image

# Page-segmentation mode 11 = "sparse text, no specific orientation". Best
# for CAD drawings where labels are scattered and not part of a page layout.
_DEFAULT_PSM = 11
# Tesseract aggressively OCRs duct outlines and equipment symbols as letters.
# Drop tokens with confidence below this floor — empirically removes ~half
# of the false positives without losing real labels.
_MIN_CONFIDENCE = 0.40


class TesseractExtractor(OCRExtractor):
    def __init__(
        self, psm: int = _DEFAULT_PSM, min_confidence: float = _MIN_CONFIDENCE,
    ) -> None:
        self._psm = psm
        self._min_confidence = min_confidence

    def extract_text(
        self, image: "Image", region: Bbox | None = None,
    ) -> list[OCRMatch]:
        crop = image
        ox = oy = 0
        if region is not None:
            x, y, w, h = region
            crop = image.crop((x, y, x + w, y + h))
            ox, oy = x, y
        data = pytesseract.image_to_data(
            crop,
            config=f"--psm {self._psm}",
            output_type=pytesseract.Output.DICT,
        )
        out: list[OCRMatch] = []
        n = len(data.get("text", []))
        for i in range(n):
            text = data["text"][i].strip()
            if not text or not _looks_like_text(text):
                continue
            try:
                conf_raw = int(data["conf"][i])
            except (TypeError, ValueError):
                conf_raw = -1
            confidence = max(0.0, conf_raw / 100.0) if conf_raw >= 0 else 0.0
            if confidence < self._min_confidence:
                continue
            bbox = (
                int(data["left"][i]) + ox,
                int(data["top"][i]) + oy,
                int(data["width"][i]),
                int(data["height"][i]),
            )
            out.append(OCRMatch(text=text, bbox=bbox, confidence=confidence))
        return out


def _looks_like_text(token: str) -> bool:
    """True iff at least one alphanumeric character is present.

    Tesseract often emits pure-punctuation tokens (`'`, `.`, `~~~`) when it
    misreads dashed lines or hatching as text. Those are noise.
    """
    return any(ch.isalnum() for ch in token)

    def extract_table(self, image: "Image", region: Bbox) -> Table:
        raise NotImplementedError("Tesseract extractor does not implement table OCR")
