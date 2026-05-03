"""Auto-orientation detection (V2 §5.8.3 / orientation normalize step).

Engineering PDFs frequently contain landscape drawings rotated 90° to fit
inside a portrait page (or vice versa). The PDF page metadata reports
``rotation=0`` because the rotation is baked into the content, not
recorded as page rotation. Stages downstream of ingest assume canonical
orientation; running them on a rotated page silently breaks every
geometric heuristic (categorizer, tile grid, reviewer crops).

This module detects the rotation from cheap signals so ``ProbeOCRStage``
can normalise once at the door:

  • ``detect_rotation_from_text_layer(page)`` — vector PDFs have a text
    layer; we vote on each span's bbox aspect ratio. Real horizontal text
    is wider than tall (``w > h``); a span whose bbox is narrow + tall is
    rotated 90°. The ``dir`` field on PDF text spans is unreliable on
    rotated content (often reports ``(1.0, 0.0)`` regardless), so we use
    geometry instead. Returns ``0`` (canonical) or ``90`` (rotated,
    direction unknown — needs ``resolve_rotation_direction`` to pick
    between 90 and 270 CW).

  • ``detect_rotation_from_image(image, ocr)`` — for raster sources we
    OCR the probe at low DPI and apply the same aspect-ratio vote on
    OCR-match bboxes. Same direction-blind output as the text-layer pass.

  • ``resolve_rotation_direction(...)`` — when the aspect-ratio vote
    flagged "rotated", render the source at each candidate rotation,
    OCR each render at low DPI, and count word-like matches. The correct
    rotation produces dramatically more recognised words than the wrong
    one (rotated/upside-down text is OCR-gibberish or skipped entirely).
    Returns the winner if the margin is ≥ 1.3× else ``0`` (fail open).

The output is a clockwise rotation amount in degrees: 0, 90, 180, or 270
— applying that rotation to the source brings it to canonical
orientation.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from PIL import Image as PILImage
from PIL.Image import Image

from app.ocr.base import OCRExtractor, OCRMatch

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

# Word-like match floor for direction resolution. Real words are mostly
# alphabetic and at least four chars; numeric callouts and OCR noise fall
# below this floor and don't dominate the count.
_WORDLIKE_MIN_CHARS = 4
_WORDLIKE_ALPHA_RATIO = 0.6

# Margin by which the winning candidate must beat the loser. Below this
# we treat the result as ambiguous and don't rotate.
_DIRECTION_MARGIN = 1.3

# Low DPI for direction-resolution renders. The OCR pass only needs
# enough resolution to recognise a few dozen words; 90 DPI is cheap and
# adequate on engineering drawings.
_DIRECTION_PROBE_DPI = 90


Rotation = Literal[0, 90, 180, 270]


def detect_rotation_from_text_layer(page: Any) -> Rotation:
    """Detect rotation from PDF text-layer bbox aspect ratios.

    Returns ``90`` when a clear majority of spans are vertically oriented
    (rotation needed, direction unknown) and ``0`` otherwise. The 90 vs
    270 ambiguity is resolved separately by
    ``resolve_rotation_direction``.
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

    Used for raster_pdf and raster_image sources. Cost is one OCR pass at
    the probe resolution — a few hundred ms. Same direction-blind output
    as ``detect_rotation_from_text_layer``.
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


def resolve_rotation_direction(
    source: Any,
    ocr: OCRExtractor,
    candidates: list[int],
) -> Rotation:
    """Pick the correct rotation by rendering each candidate and OCRing it.

    The aspect-ratio vote can detect that a drawing is rotated but cannot
    distinguish 90° CW from 270° CW (both produce vertical bboxes). This
    function renders the source at each candidate rotation, OCRs each
    render at low DPI, counts word-like matches, and returns the winner
    if the margin is ≥ 1.3×. Tie or below margin → ``0`` (fail open).

    ``source`` is either a pymupdf ``Page`` (for vector PDFs) or a PIL
    ``Image`` (for raster sources). Both render paths produce a PIL
    image suitable for the OCR engine. Page rotation is reset to its
    original value before returning — this function is non-mutating; the
    caller applies the resolved rotation.

    ``candidates`` is the list of rotations to try, typically ``[0, 90,
    270]``. Including ``0`` lets a non-rotated drawing win outright
    without paying the full direction-resolution overhead — its at-zero
    OCR is the cheapest pass.
    """
    if not candidates:
        return 0

    scores: dict[int, int] = {}
    for rot in candidates:
        if rot not in (0, 90, 180, 270):
            continue
        render = _render_at_rotation(source, rot)
        if render is None:
            continue
        matches = ocr.extract_text(render)
        scores[rot] = _word_like_count(matches)

    if not scores:
        logger.info("orientation: direction-resolution produced no candidates")
        return 0

    winner = max(scores, key=lambda r: scores[r])
    runner_up_score = max(
        (s for r, s in scores.items() if r != winner),
        default=0,
    )

    logger.info(
        "orientation: direction-resolution scores=%s winner=%d runner_up=%d",
        scores, winner, runner_up_score,
    )

    if runner_up_score == 0:
        # Winner has any words at all and no rival — accept it as long
        # as it's a real signal (not zero-vs-zero).
        return _as_rotation(winner) if scores[winner] > 0 else 0

    if scores[winner] >= runner_up_score * _DIRECTION_MARGIN:
        return _as_rotation(winner)

    logger.info(
        "orientation: direction-resolution within margin (%.2fx) — fail open",
        scores[winner] / runner_up_score if runner_up_score else float("inf"),
    )
    return 0


# ── Internal helpers. ────────────────────────────────────────────────────────


def _vote(horizontal: int, vertical: int, *, source: str) -> Rotation:
    """Apply the orientation vote with a clear-margin gate.

    Returns ``90`` to signal "rotated, direction unknown" — the caller
    must follow up with ``resolve_rotation_direction`` to pick the
    correct CW amount.
    """
    total = horizontal + vertical
    if total == 0:
        logger.info("orientation: no countable spans (%s); rotation=0", source)
        return 0

    if vertical >= horizontal * _VOTE_MARGIN and vertical >= 3:
        logger.info(
            "orientation: rotated detected via %s (vertical=%d, horizontal=%d) — direction TBD",
            source, vertical, horizontal,
        )
        return 90

    logger.info(
        "orientation: canonical via %s (horizontal=%d, vertical=%d)",
        source, horizontal, vertical,
    )
    return 0


def _word_like_count(matches: list[OCRMatch]) -> int:
    """Count OCR matches whose text looks like a real word.

    Real words: ≥ 4 chars and ≥ 60% alphabetic. Numeric callouts and
    OCR-gibberish from upside-down text fall below the floor — they
    can't dominate the direction vote.
    """
    count = 0
    for m in matches:
        text = m.text.strip()
        n = len(text)
        if n < _WORDLIKE_MIN_CHARS:
            continue
        alpha = sum(1 for c in text if c.isalpha())
        if alpha / n >= _WORDLIKE_ALPHA_RATIO:
            count += 1
    return count


def _render_at_rotation(source: Any, rot: int) -> Image | None:
    """Render ``source`` rotated by ``rot`` degrees clockwise.

    For a pymupdf ``Page`` the rotation is set, the page is rasterised at
    ``_DIRECTION_PROBE_DPI``, and the rotation is restored — non-
    mutating from the caller's perspective. For a PIL ``Image`` we
    rotate in-memory (PIL.rotate is CCW so we pass ``-rot``).

    Returns ``None`` on any render failure so the caller can skip the
    candidate without aborting direction resolution.
    """
    # PIL.Image is duck-typed; check for the pymupdf Page surface first.
    if _is_pymupdf_page(source):
        return _render_page_at_rotation(source, rot)
    if isinstance(source, PILImage.Image):
        if rot == 0:
            return source
        return source.rotate(-rot, expand=True)
    return None


def _is_pymupdf_page(obj: Any) -> bool:
    return hasattr(obj, "get_pixmap") and hasattr(obj, "set_rotation")


def _render_page_at_rotation(page: Any, rot: int) -> Image | None:
    original = getattr(page, "rotation", 0)
    try:
        page.set_rotation(rot)
        pixmap = page.get_pixmap(dpi=_DIRECTION_PROBE_DPI)
        mode = "RGBA" if pixmap.alpha else "RGB"
        return PILImage.frombytes(
            mode, (pixmap.width, pixmap.height), pixmap.samples
        ).convert("RGB")
    except Exception:  # noqa: BLE001 — render failures must not abort resolution
        logger.exception("orientation: page render at rot=%d failed", rot)
        return None
    finally:
        try:
            page.set_rotation(original)
        except Exception:  # noqa: BLE001 — restoration best-effort
            logger.exception("orientation: failed to restore page rotation")


def _as_rotation(rot: int) -> Rotation:
    """Narrow an int to the ``Rotation`` literal type, defaulting to 0."""
    if rot in (0, 90, 180, 270):
        return rot  # type: ignore[return-value]
    return 0
