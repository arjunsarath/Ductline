"""Standardize VLM-read text to canonical duct-dimension forms (A1, A3).

Two valid forms after standardization:
  - Round duct       → ``<N>"Ø``    (uppercase Ø, ASCII double-quote inch)
  - Rectangular duct → ``<W>"x<H>"`` (lowercase x, ASCII double-quote inch)

Anything else is rejected. Standardization absorbs OCR mojibake on the
``ø`` glyph (engines emit ``0``, ``o``, ``@``, ``°``, ``#``, ``φ``) and
on the ``"`` mark (``'``, ``′``, backtick, ``*``).
"""

from __future__ import annotations

import re
from typing import Literal

DuctKind = Literal["round", "rectangular"]

# Substitutes commonly returned for ``ø``: the lowercase Latin glyph,
# uppercase Ø, the unicode diameter symbol, plus the misreads we've seen.
_OE_SUBS = r"[øØ⌀0oOe@°#φΦ]"
_INCH_SUBS = r"[\"'′`*]?"  # optional inch mark
_ROUND_RE = re.compile(rf"^\s*(\d{{1,3}})\s*{_INCH_SUBS}\s*{_OE_SUBS}\s*$")
_RECT_RE = re.compile(
    rf"^\s*(\d{{1,3}})\s*{_INCH_SUBS}\s*[xX×]\s*(\d{{1,3}})\s*{_INCH_SUBS}\s*$",
)


def standardize_duct_label(text: str) -> tuple[str, DuctKind] | None:
    """Return ``(canonical, kind)`` or ``None`` if the text isn't a duct label."""
    if not text:
        return None
    # Some VLMs prefix "the text is " — drop quoted phrasing, take first line.
    candidate = text.strip().splitlines()[0].strip().strip("\"'")
    rect = _RECT_RE.match(candidate)
    if rect:
        w, h = int(rect.group(1)), int(rect.group(2))
        return (f'{w}"x{h}"', "rectangular")
    rnd = _ROUND_RE.match(candidate)
    if rnd:
        n = int(rnd.group(1))
        return (f'{n}"Ø', "round")
    return None
