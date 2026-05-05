"""Full-page OCR + regex token classification (SOLUTION-DESIGN-V3 §5.6).

V1/V2 ran OCR per-segment. V3 inverts that: full-page OCR once, then
regex-classify every match. Tokens are kept as a single token table that
attribute.py then filters geometrically.

Tile + dedupe is here because RapidOCR's detection model auto-downscales
inputs above ~960 px on the long edge — for a 7000 px adaptive-DPI render
we tile to keep small text from getting halved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image

from app.ocr.base import OCRExtractor, OCRMatch

# ── Regex grammar ────────────────────────────────────────────────────────────
# Bounds chosen to admit imperial duct dims (4–144 in) and metric duct dims
# (50–2400 mm, divisible by 5). Tokens that pass both bounds carry both
# unit candidates; page-unit detection resolves the ambiguity (§5.6).
#
# The first side of the dim is always two digits or more — drawing scales
# never put a 1×N duct on a plan, so "1x6" or "9x9" is too narrow to be a
# legitimate duct callout. The second side admits a single digit because
# imperial branch ducts commonly carry a single-digit short dim — e.g.,
# "16x6 SG-1" labels a 16-inch wide × 6-inch deep supply branch. The
# in-bounds plausibility check (4 ≤ a, b ≤ 144) further filters spurious
# matches like "10x9 ELBOW" picked up from random text.
# Rectangular duct dim. Three real-world flavours:
#   • bare:           ``15x13``       (drawing 03)
#   • inch-marked:    ``12"x10"``     (drawing 05 — Federal/SmithGroup)
#   • mixed:          ``28"x18"``     (drawing 02 callout style)
# The optional quote characters between the number and the ``x`` are the
# critical bit; without them ``12"x10"`` (the most common US convention)
# wouldn't match. ``[\"”]`` covers both straight and curly quotes.
_DIM_RECT = re.compile(
    r"(?<!\d)(\d{2,4})\s*[\"”]?\s*[xX×]\s*[\"”]?\s*(\d{1,4})(?!\d)"
)
# Round-duct callouts. The native diameter symbol (Ø) is often misread by
# OCR as digit ``0`` or capital ``O`` — both observed in drawing 03's
# bottom plan where ``13"Ø`` was read as ``13"0``. We accept the canonical
# Ø family and the misread ``["”]\s*[0OQD]`` pattern. The required quote
# mark prevents matches against feet-inches text like ``13'-10"`` whose
# tail digits aren't preceded by a quote.
_DIM_ROUND = re.compile(
    r"(?<!\d)(\d{1,4})\s*"
    r"(?:[\"”]?\s*[øØ⌀∅]|[\"”]\s*[0OQD](?!\d))"
)
_CFM = re.compile(r"(?<!\d)(\d{1,5})\s*CFM", re.IGNORECASE)
_LPS = re.compile(r"(?<!\d)(\d{1,5})\s*L/?S", re.IGNORECASE)


def _is_imperial_pair(a: int, b: int) -> bool:
    return 4 <= a <= 144 and 4 <= b <= 144


def _is_metric_pair(a: int, b: int) -> bool:
    return 50 <= a <= 2400 and 50 <= b <= 2400 and (a % 5 == 0) and (b % 5 == 0)


@dataclass
class ClassifiedToken:
    """One OCR match that passed regex classification.

    ``bbox`` is in pixel coordinates of the OCR'd image (after rendering
    at the adaptive DPI). The full original text is preserved because
    downstream display + reasoning trace want the literal string the
    engineer wrote, not the parsed regex group.
    """

    text: str
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    conf: float
    kind: Literal["dim_rect", "dim_round", "flow"]
    # dim_rect:
    a: int | None = None
    b: int | None = None
    units_candidate: list[Literal["in", "mm"]] = field(default_factory=list)
    # dim_round:
    diameter: int | None = None
    # flow:
    flow_value: int | None = None
    flow_unit: Literal["CFM", "L/s"] | None = None


def classify(text: str) -> tuple[Literal["dim_rect", "dim_round", "flow"], dict] | None:
    t = text.strip()
    m = _DIM_RECT.search(t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        units: list[Literal["in", "mm"]] = []
        if _is_imperial_pair(a, b):
            units.append("in")
        if _is_metric_pair(a, b):
            units.append("mm")
        if units:
            return ("dim_rect", {"a": a, "b": b, "units_candidate": units})

    m = _DIM_ROUND.search(t)
    if m:
        d = int(m.group(1))
        if (4 <= d <= 96) or (100 <= d <= 2400):
            return ("dim_round", {"diameter": d})

    m = _CFM.search(t)
    if m:
        v = int(m.group(1))
        if 10 <= v <= 99999:
            return ("flow", {"flow_value": v, "flow_unit": "CFM"})

    m = _LPS.search(t)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 9999:
            return ("flow", {"flow_value": v, "flow_unit": "L/s"})

    return None


# ── Tile + dedupe ────────────────────────────────────────────────────────────


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def ocr_full_page(
    img_pil: Image.Image,
    ocr: OCRExtractor,
    *,
    tile_size: int = 2000,
    overlap: int = 200,
    long_edge_threshold: int = 3500,
) -> list[OCRMatch]:
    """OCR the full page, tiling if the long edge exceeds the threshold.

    Tiles overlap by ``overlap`` px so tokens that straddle a boundary
    appear in at least one tile fully. Duplicate detections (same text
    in overlapping tiles) are deduped by bbox IoU > 0.5 + text equality.
    """
    width, height = img_pil.size
    if max(width, height) <= long_edge_threshold:
        return ocr.extract_text(img_pil)

    raw: list[OCRMatch] = []
    step = tile_size - overlap
    nx = max(1, (width - overlap + step - 1) // step)
    ny = max(1, (height - overlap + step - 1) // step)

    for iy in range(ny):
        for ix in range(nx):
            x0 = ix * step
            y0 = iy * step
            x1 = min(x0 + tile_size, width)
            y1 = min(y0 + tile_size, height)
            tile = img_pil.crop((x0, y0, x1, y1))
            matches = ocr.extract_text(tile)
            # offset back into page coordinates
            for m in matches:
                bx, by, bw, bh = m.bbox
                raw.append(
                    OCRMatch(
                        text=m.text,
                        bbox=(bx + x0, by + y0, bw, bh),
                        confidence=m.confidence,
                    )
                )

    # Dedupe — same text + IoU > 0.5
    deduped: list[OCRMatch] = []
    for tok in raw:
        is_dup = False
        for ex in deduped:
            if ex.text == tok.text and _iou(ex.bbox, tok.bbox) > 0.5:
                is_dup = True
                break
        if not is_dup:
            deduped.append(tok)
    return deduped


def classify_all(matches: list[OCRMatch]) -> list[ClassifiedToken]:
    out: list[ClassifiedToken] = []
    for m in matches:
        result = classify(m.text)
        if result is None:
            continue
        kind, vals = result
        out.append(
            ClassifiedToken(
                text=m.text,
                bbox=m.bbox,
                conf=m.confidence,
                kind=kind,
                **vals,
            )
        )
    return out


def detect_page_unit(tokens: list[ClassifiedToken]) -> Literal["in", "mm"]:
    """Page unit from flow token majority. Default ``in`` if no flow tokens."""
    cfm = sum(1 for t in tokens if t.kind == "flow" and t.flow_unit == "CFM")
    lps = sum(1 for t in tokens if t.kind == "flow" and t.flow_unit == "L/s")
    if lps > cfm:
        return "mm"
    return "in"


def filter_for_page_unit(
    tokens: list[ClassifiedToken], unit: Literal["in", "mm"]
) -> list[ClassifiedToken]:
    """Keep tokens whose unit candidacy includes the resolved page unit.

    For ``dim_rect``: only emit when ``unit`` is in ``units_candidate``.
    For ``dim_round`` and ``flow``: kept regardless (flow tokens carry
    their own unit; round dims don't have an A/B ambiguity).
    """
    out: list[ClassifiedToken] = []
    for t in tokens:
        if t.kind == "dim_rect":
            if unit in t.units_candidate:
                out.append(t)
        else:
            out.append(t)
    return out
