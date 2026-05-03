"""Auto-orientation detection (V2 §5.8.3 / orientation normalize step).

Engineering PDFs frequently contain landscape drawings rotated 90° to fit
inside a portrait page (or vice versa). The PDF page metadata reports
``rotation=0`` because the rotation is baked into the content, not
recorded as page rotation. Stages downstream of ingest assume canonical
orientation; running them on a rotated page silently breaks every
geometric heuristic (categorizer, tile grid, reviewer crops).

This module detects the rotation from cheap signals so ``IngestStage`` can
normalise once at the door:

  • ``detect_rotation_from_text_layer(page)`` — vector PDFs have a text
    layer; we vote on each span's bbox aspect ratio. Real horizontal text
    is wider than tall (``w > h``); a span whose bbox is narrow + tall is
    rotated 90°. The ``dir`` field on PDF text spans is unreliable on
    rotated content (often reports ``(1.0, 0.0)`` regardless), so we use
    geometry instead.

  • ``detect_rotation_from_image(image, ocr)`` — for raster sources we
    OCR the probe at low DPI and apply the same aspect-ratio vote on
    OCR-match bboxes. Used as a fallback when no text layer exists.

The output is a clockwise rotation amount in degrees: 0, 90, 180, or
270 — applying that rotation to the source brings it to canonical
orientation. v1 of this module distinguishes 0° from 90° only;
distinguishing 90° from 270° requires reading the text's intrinsic
direction (top-of-glyph vector) which a bbox alone doesn't provide. We
default to 90° (the dominant case for landscape-in-portrait drawings on
the benchmark set) and surface the choice in the categorize approval
gate so the user can cancel if it's wrong.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from PIL.Image import Image

from app.ocr.base import OCRExtractor

logger = logging.getLogger(__name__)

# A span's bbox is "horizontal" if its width is at least this multiple of
# its height (and "vertical" by the inverse). Lower values are more
# permissive — we want a clear signal, so 1.3 is conservative.
_ASPECT_THRESHOLD = 1.3

# Spans shorter than this are too noisy to count — single characters,
# stray punctuation, OCR artifacts. Skipping them keeps the vote honest.
_MIN_SPAN_CHARS = 4

# Rotation is applied only when the dominant axis wins by this margin.
# Below the margin we treat orientation as ambiguous and don't rotate.
_VOTE_MARGIN = 1.5


Rotation = Literal[0, 90, 180, 270]


def detect_rotation_from_text_layer(page: Any) -> Rotation:
    """Detect rotation from PDF text-layer bbox aspect ratios.

    Returns the clockwise rotation needed to make the dominant text
    direction canonical (left-to-right, top-to-bottom). Returns 0 when
    text is already canonical or when the vote is ambiguous (no rotation
    applied — fail open, not closed).
    """
    horizontal = 0
    vertical = 0
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if len(text) < _MIN_SPAN_CHARS:
                    continue
                bbox = span.get("bbox")
                if not bbox or len(bbox) < 4:
                    continue
                w = float(bbox[2] - bbox[0])
                h = float(bbox[3] - bbox[1])
                if w <= 0 or h <= 0:
                    continue
                if w >= h * _ASPECT_THRESHOLD:
                    horizontal += 1
                elif h >= w * _ASPECT_THRESHOLD:
                    vertical += 1

    return _vote(horizontal, vertical, source="text-layer")


def detect_rotation_from_image(image: Image, ocr: OCRExtractor) -> Rotation:
    """Detect rotation from OCR-match bbox aspect ratios on a raster.

    Used for raster_pdf and raster_image sources, or as a tie-breaker
    when the text-layer vote is ambiguous. Cost is one OCR pass at the
    probe resolution — a few hundred ms.
    """
    matches = ocr.extract_text(image)
    horizontal = 0
    vertical = 0
    for m in matches:
        if len(m.text.strip()) < _MIN_SPAN_CHARS:
            continue
        # OCRMatch.bbox is (x, y, w, h) per app.ocr.base — width is index 2,
        # height is index 3 (NOT (x0, y0, x1, y1) like PDF text layer).
        w = float(m.bbox[2])
        h = float(m.bbox[3])
        if w <= 0 or h <= 0:
            continue
        if w >= h * _ASPECT_THRESHOLD:
            horizontal += 1
        elif h >= w * _ASPECT_THRESHOLD:
            vertical += 1

    return _vote(horizontal, vertical, source="ocr")


def _vote(horizontal: int, vertical: int, *, source: str) -> Rotation:
    """Apply the orientation vote with a clear-margin gate."""
    total = horizontal + vertical
    if total == 0:
        logger.info("orientation: no countable spans (%s); rotation=0", source)
        return 0

    if vertical >= horizontal * _VOTE_MARGIN and vertical >= 3:
        logger.info(
            "orientation: rotated detected via %s (vertical=%d, horizontal=%d) → 90 CW",
            source, vertical, horizontal,
        )
        # Default to 90° clockwise. Distinguishing 90 vs 270 needs the
        # intrinsic glyph-up direction which a bbox doesn't carry; the
        # categorize approval gate gives the user a chance to verify.
        return 90

    logger.info(
        "orientation: canonical via %s (horizontal=%d, vertical=%d)",
        source, horizontal, vertical,
    )
    return 0
