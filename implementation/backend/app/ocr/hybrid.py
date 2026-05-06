"""Tesseract-first OCR with VLM fallback for low-confidence crops.

For each crop we run Tesseract (cheap, sub-second). If the average per-token
confidence is high enough OR the crop is empty, we trust the result. Only
ambiguous crops are escalated to the VLM (~5 s/call) so we can stay below
~5% VLM usage on a typical drawing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PIL.Image import Image

from app.ocr.ollama_vision import read_text_from_crop
from app.ocr.tesseract import TesseractExtractor

# Tesseract output above this average per-token confidence is accepted as-is.
TESSERACT_TRUST_THRESHOLD = 0.90

OcrSource = Literal["tesseract", "vlm", "empty"]


@dataclass(frozen=True)
class HybridOcrResult:
    text: str
    confidence: float
    source: OcrSource


# One Tesseract extractor instance per process; pytesseract is process-safe.
_tesseract = TesseractExtractor(min_confidence=0.0)


def read_text_smart(crop: Image) -> HybridOcrResult:
    """Read text from a single crop using Tesseract → VLM-fallback ladder.

    - No Tesseract tokens → ``empty`` (no VLM call; we trust empty).
    - Avg Tesseract confidence ≥ 0.90 → ``tesseract`` result.
    - Otherwise → VLM. If the VLM also returns empty, we report ``empty``.
    """
    matches = _tesseract.extract_text(crop)
    if not matches:
        return HybridOcrResult(text="", confidence=1.0, source="empty")

    avg_conf = sum(m.confidence for m in matches) / len(matches)
    joined = " ".join(m.text for m in matches)
    if avg_conf >= TESSERACT_TRUST_THRESHOLD:
        return HybridOcrResult(text=joined, confidence=avg_conf, source="tesseract")

    vlm_text = read_text_from_crop(crop)
    if not vlm_text or vlm_text in {"EMPTY", "ERROR"}:
        return HybridOcrResult(text="", confidence=1.0, source="empty")
    return HybridOcrResult(text=vlm_text, confidence=0.95, source="vlm")
