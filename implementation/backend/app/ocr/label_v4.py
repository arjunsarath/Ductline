"""Duct label OCR for V4 — axis-aligned only (A3, A8).

Two regex grammars: round (`Nø` / `N"⌀`) and rectangular (`WxH`). Labels are
always 0° or 90° (A8); the recognizer rotates the crop 90° and runs OCR a
second time when nothing matches at 0°, rather than searching arbitrary angles.

The OCR engine is injected (default: V3's `RapidOCRExtractor`) so callers and
tests can substitute a stub without booting the model. ADR-0006 / V3 reuse.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from PIL import Image

from app.cv.types import DuctPolygon, Label
from app.ocr.base import OCRExtractor, OCRMatch
from app.ocr.rapid import RapidOCRExtractor

logger = logging.getLogger(__name__)

# Strict per SOLUTION-DESIGN-V4 §2 (A3) — the duct interior holds at most one
# numeric token, axis-aligned, in one of these two shapes.
_ROUND_RE = re.compile(r'^\s*(\d{1,2})\s*"?\s*[øØ⌀]\s*$')
_RECT_RE = re.compile(r'^\s*(\d{1,2})\s*"?\s*[xX×]\s*(\d{1,2})\s*"?\s*$')


@dataclass(frozen=True)
class _Candidate:
    parsed_value: str
    parsed_shape: str  # "round" | "rectangular"
    raw_text: str
    confidence: float
    bbox: tuple[float, float, float, float]  # absolute page coords
    orientation_deg: int  # 0 | 90


def read_duct_labels(
    image: Image.Image,
    polygons: list[DuctPolygon],
    ocr: OCRExtractor | None = None,
) -> list[Label]:
    """Return one label per polygon when a matching axis-aligned token is found.

    Polygons with no parseable label are simply omitted; downstream pixel
    measurement (A9) handles unlabeled segments.
    """
    engine = ocr if ocr is not None else RapidOCRExtractor()
    results: list[Label] = []
    for poly in polygons:
        label = _label_for_polygon(image, poly, engine)
        if label is not None:
            results.append(label)
    return results


def _label_for_polygon(
    image: Image.Image, polygon: DuctPolygon, ocr: OCRExtractor
) -> Label | None:
    bbox = _polygon_bbox(polygon.points)
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox

    interior = image.crop((x0, y0, x1, y1))
    centroid = _polygon_centroid(polygon.points)

    candidates = _scan_axis_aligned(ocr, interior, (x0, y0))
    if not candidates:
        return None

    chosen = _pick_closest(candidates, centroid)
    if len(candidates) > 1:
        logger.warning(
            "polygon %s: %d label candidates inside interior — picked %r",
            polygon.id,
            len(candidates),
            chosen.parsed_value,
        )

    return Label(
        polygon_id=polygon.id,
        raw_text=chosen.raw_text,
        bbox=chosen.bbox,
        orientation_deg=0 if chosen.orientation_deg == 0 else 90,
        parsed_value=chosen.parsed_value,
        parsed_shape=chosen.parsed_shape,  # type: ignore[arg-type]
    )


def _scan_axis_aligned(
    ocr: OCRExtractor,
    interior: Image.Image,
    origin: tuple[float, float],
) -> list[_Candidate]:
    """Run OCR at 0° and (only if needed) at 90° rotation.

    A8 says labels are always axis-aligned. We avoid the 90° pass when the 0°
    pass already produced a parseable token — saves the second engine call.
    """
    pass_0 = _match_pass(ocr, interior, origin, rotation=0)
    if pass_0:
        return pass_0
    return _match_pass(ocr, interior, origin, rotation=90)


def _match_pass(
    ocr: OCRExtractor,
    interior: Image.Image,
    origin: tuple[float, float],
    rotation: int,
) -> list[_Candidate]:
    image = interior if rotation == 0 else interior.rotate(-rotation, expand=True)
    matches = ocr.extract_text(image)
    out: list[_Candidate] = []
    for m in matches:
        parsed = _parse_token(m.text)
        if parsed is None:
            continue
        value, shape = parsed
        abs_bbox = _to_absolute_bbox(m.bbox, origin, interior.size, rotation)
        out.append(
            _Candidate(
                parsed_value=value,
                parsed_shape=shape,
                raw_text=m.text,
                confidence=m.confidence,
                bbox=abs_bbox,
                orientation_deg=rotation,
            )
        )
    return out


def _parse_token(text: str) -> tuple[str, str] | None:
    cleaned = text.strip()
    rect = _RECT_RE.match(cleaned)
    if rect:
        return f'{rect.group(1)}"x{rect.group(2)}"', "rectangular"
    rnd = _ROUND_RE.match(cleaned)
    if rnd:
        return f'{rnd.group(1)}"ø', "round"
    return None


def _polygon_bbox(
    points: list[tuple[float, float]],
) -> tuple[int, int, int, int] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1


def _polygon_centroid(
    points: list[tuple[float, float]],
) -> tuple[float, float]:
    n = len(points)
    return sum(p[0] for p in points) / n, sum(p[1] for p in points) / n


def _pick_closest(
    candidates: list[_Candidate], centroid: tuple[float, float]
) -> _Candidate:
    cx, cy = centroid

    def distance(c: _Candidate) -> float:
        x, y, w, h = c.bbox
        bx, by = x + w / 2, y + h / 2
        return (bx - cx) ** 2 + (by - cy) ** 2

    return min(candidates, key=distance)


def _to_absolute_bbox(
    bbox: tuple[int, int, int, int],
    origin: tuple[float, float],
    interior_size: tuple[int, int],
    rotation: int,
) -> tuple[float, float, float, float]:
    """Map a match bbox from the (possibly rotated) crop frame to page coords."""
    x, y, w, h = bbox
    ox, oy = origin
    if rotation == 0:
        return float(ox + x), float(oy + y), float(w), float(h)
    # Rotation was -90° about the crop origin (PIL rotate is counter-clockwise
    # by positive angle; we passed `-rotation`). Inverse maps (x',y') in the
    # rotated frame back to the original interior frame.
    interior_w, interior_h = interior_size
    if rotation == 90:
        orig_x = y
        orig_y = interior_h - (x + w)
        return float(ox + orig_x), float(oy + orig_y), float(h), float(w)
    return float(ox + x), float(oy + y), float(w), float(h)


# Re-export for convenience — ensures the symbol is importable from this module
# even when callers stub the engine.
__all__ = ["read_duct_labels", "OCRMatch"]
