"""Dimension grammar — regex over OCR strings (SOLUTION-DESIGN §4 row 5).

Strings that don't match the grammar are not emitted as dimensions. We accept
ASCII fallbacks that OCR engines commonly emit instead of `⌀` / `Ø`: `O`, `0`,
`DIA`, and `OD` / `ID` markers.
"""

from __future__ import annotations

import re
from typing import Literal

from app.schemas import Dimension

# Round duct examples found in real drawings:
#   14"⌀     14"Ø     14" DIA     14"O.D.     14"
# We require either an explicit diameter marker or the trailing inch mark with
# a circle hint to avoid matching every plain number on the sheet.
_ROUND_PATTERNS = [
    re.compile(r'(\d{1,3})\s*["\']?\s*[⌀Øø]', re.IGNORECASE),
    re.compile(r'(\d{1,3})\s*["\']?\s*(?:DIA|D\.I\.A\.)', re.IGNORECASE),
    re.compile(r'(\d{1,3})\s*["\']?\s*(?:O\.?D\.?|I\.?D\.?)', re.IGNORECASE),
]

# Rectangular: 14" x 8"  /  14 x 8  /  14"x8"
_RECTANGULAR_PATTERN = re.compile(
    r'(\d{1,3})\s*["\']?\s*[xX×]\s*(\d{1,3})\s*["\']?'
)


def parse_dimension(text: str, *, source: str, confidence: str) -> Dimension | None:
    """Return a `Dimension` if `text` matches the grammar, else `None`."""
    cleaned = _normalize(text)

    rect_match = _RECTANGULAR_PATTERN.search(cleaned)
    if rect_match:
        width, height = rect_match.group(1), rect_match.group(2)
        return Dimension(
            value=f'{width}" x {height}"',
            shape="rectangular",
            confidence=_typed_confidence(confidence),
            source=source,
        )

    for pattern in _ROUND_PATTERNS:
        round_match = pattern.search(cleaned)
        if round_match:
            return Dimension(
                value=f'{round_match.group(1)}"⌀',
                shape="round",
                confidence=_typed_confidence(confidence),
                source=source,
            )

    return None


def _normalize(text: str) -> str:
    # Collapse whitespace and trim — OCR often inserts spaces around symbols.
    return re.sub(r"\s+", " ", text).strip()


def _typed_confidence(value: str) -> Literal["high", "medium", "low"]:
    if value not in {"high", "medium", "low"}:
        return "low"
    return value  # type: ignore[return-value]
