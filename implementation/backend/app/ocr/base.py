"""OCR seam — pluggable engine boundary (SOLUTION-DESIGN §5.1, ADR-0006).

Stage 2 calls `extract_text` on a sample region for the OCR-confidence average.
Stage 5 calls `extract_text` per segment neighborhood and `extract_table` over
the schedule region. One Protocol covers both call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PIL.Image import Image


# (x, y, w, h) in pixels — same convention as OpenCV ROIs.
Bbox = tuple[int, int, int, int]


@dataclass(frozen=True)
class OCRMatch:
    text: str
    bbox: Bbox
    confidence: float  # [0.0, 1.0]


@dataclass(frozen=True)
class TableCell:
    row: int
    col: int
    text: str
    confidence: float


@dataclass(frozen=True)
class Table:
    rows: list[list[TableCell]]


class OCRExtractor(Protocol):
    def extract_text(
        self, image: Image, region: Bbox | None = None
    ) -> list[OCRMatch]: ...

    def extract_table(self, image: Image, region: Bbox) -> Table: ...
