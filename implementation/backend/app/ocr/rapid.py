"""RapidOCR implementation of the OCRExtractor protocol.

RapidOCR is a wrapper that runs PaddleOCR's models via ONNX Runtime. We chose
it over native PaddleOCR because paddlepaddle has no reliable arm64 Linux wheel
(SOLUTION-DESIGN §11 open question 2 — documented swap path from ADR-0006).

The engine is heavy on first call (model files download under ~50 MB) so we
defer initialization behind a lazy property.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from app.ocr.base import Bbox, OCRExtractor, OCRMatch, Table, TableCell

if TYPE_CHECKING:
    from PIL.Image import Image


class RapidOCRExtractor(OCRExtractor):
    def __init__(self) -> None:
        self._engine: Any | None = None

    # ── Public API ───────────────────────────────────────────────────────────

    def extract_text(
        self, image: Image, region: Bbox | None = None
    ) -> list[OCRMatch]:
        engine = self._get_engine()
        crop, offset = _crop(image, region)
        result, _elapsed = engine(np.asarray(crop))
        return _parse_results(result, offset)

    def extract_table(self, image: Image, region: Bbox) -> Table:
        # Schedule cells are clustered by y (rows) then x (columns) — simpler
        # than running PP-Structure and adequate for the schedule-lookup tier
        # of the pressure-class policy (ADR-0004).
        matches = self.extract_text(image, region)
        return _matches_to_table(matches)

    # ── Lazy engine init ─────────────────────────────────────────────────────

    def _get_engine(self) -> Any:
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine


# ── Pure helpers (testable without the engine). ──────────────────────────────


def _crop(image: Image, region: Bbox | None) -> tuple[Image, tuple[int, int]]:
    if region is None:
        return image, (0, 0)
    x, y, w, h = region
    return image.crop((x, y, x + w, y + h)), (x, y)


def _parse_results(raw: Any, offset: tuple[int, int]) -> list[OCRMatch]:
    """RapidOCR returns `[[poly, text, confidence], ...]` or None when no text.

    Defensive against engine signature drift — we expect a 3-element row but
    treat anything else as a malformed entry and skip it.
    """
    if not raw:
        return []

    ox, oy = offset
    matches: list[OCRMatch] = []
    for entry in raw:
        if len(entry) < 3:
            continue
        poly, text, confidence = entry[0], entry[1], entry[2]
        x_coords = [p[0] for p in poly]
        y_coords = [p[1] for p in poly]
        x_min, y_min = int(min(x_coords)), int(min(y_coords))
        x_max, y_max = int(max(x_coords)), int(max(y_coords))
        bbox: Bbox = (x_min + ox, y_min + oy, x_max - x_min, y_max - y_min)
        matches.append(OCRMatch(text=text, bbox=bbox, confidence=float(confidence)))
    return matches


def _matches_to_table(matches: list[OCRMatch]) -> Table:
    if not matches:
        return Table(rows=[])

    sorted_by_y = sorted(matches, key=lambda m: m.bbox[1])
    median_height = sorted([m.bbox[3] for m in matches])[len(matches) // 2]
    row_threshold = max(median_height * 0.6, 4)

    rows: list[list[OCRMatch]] = []
    for match in sorted_by_y:
        row_y = match.bbox[1] + match.bbox[3] / 2
        if rows and abs(row_y - _row_centroid(rows[-1])) <= row_threshold:
            rows[-1].append(match)
        else:
            rows.append([match])

    table_rows = [
        [
            TableCell(row=r_idx, col=c_idx, text=m.text, confidence=m.confidence)
            for c_idx, m in enumerate(sorted(row, key=lambda m: m.bbox[0]))
        ]
        for r_idx, row in enumerate(rows)
    ]
    return Table(rows=table_rows)


def _row_centroid(row: list[OCRMatch]) -> float:
    return sum(m.bbox[1] + m.bbox[3] / 2 for m in row) / len(row)
