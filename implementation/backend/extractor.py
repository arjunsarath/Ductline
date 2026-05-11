from __future__ import annotations

import io
from typing import Any

import fitz  # PyMuPDF — respects PDF optional-content (layer) visibility
import pdfplumber

from scale_detector import _is_rect_partial, _is_rectlike_curve, rect_corners_from_curve


def _visible_drawing_bboxes(pdf_bytes: bytes, page_number: int) -> list[tuple[float, float, float, float]]:
    """Bboxes of every vector drawing PyMuPDF reports as visible on the page.

    pdfplumber ignores PDF Optional Content Groups (layers), so it returns
    paths from hidden layers (equipment markers, dimension lines, etc.) that
    don't render in the SVG. We cross-check pdfplumber's element list against
    this set to drop everything the user can't see."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_number - 1]
        bboxes: list[tuple[float, float, float, float]] = []
        for d in page.get_drawings():
            r = d.get("rect")
            if r is None:
                continue
            bboxes.append((float(r[0]), float(r[1]), float(r[2]), float(r[3])))
        return bboxes
    finally:
        doc.close()


def _bbox_overlaps_any(
    x0: float, top: float, x1: float, bottom: float,
    visible: list[tuple[float, float, float, float]],
    tol: float = 2.0,
) -> bool:
    for vx0, vy0, vx1, vy1 in visible:
        if x1 < vx0 - tol or x0 > vx1 + tol:
            continue
        if bottom < vy0 - tol or top > vy1 + tol:
            continue
        return True
    return False

BBox = tuple[float, float, float, float]  # (x0, top, x1, bottom)


def _color_to_hex(value: Any) -> str | None:
    # pdfplumber returns colours as tuples of floats 0–1 (grayscale,
    # RGB, or CMYK), ints, or None. CAD-authored PDFs commonly use CMYK; the
    # earlier "return None for 4-tuples" path mis-tagged CMYK-black callouts
    # as colourless, then the frontend threshold filter rejected them. Mirror
    # the CMYK logic from scale_detector._is_black here so the colour parity
    # between detection and threshold-filtering matches.
    if value is None:
        return None
    if not isinstance(value, (tuple, list)):
        return None
    try:
        nums = [float(c) for c in value]
    except (TypeError, ValueError):
        return None
    if len(nums) == 1:
        r = g = b = nums[0]
    elif len(nums) == 3:
        r, g, b = nums
    elif len(nums) == 4:
        # CMYK → RGB with the standard subtractive-mix approximation.
        c, m, y, k = nums
        if not all(0.0 <= v <= 1.0 for v in nums):
            return None
        r = (1.0 - c) * (1.0 - k)
        g = (1.0 - m) * (1.0 - k)
        b = (1.0 - y) * (1.0 - k)
    else:
        return None
    if not all(0.0 <= c <= 1.0 for c in (r, g, b)):
        return None
    return "#{:02x}{:02x}{:02x}".format(
        round(r * 255), round(g * 255), round(b * 255)
    )


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _intersects(el: dict[str, Any], crop: BBox) -> bool:
    x0, top, x1, bottom = el.get("x0"), el.get("top"), el.get("x1"), el.get("bottom")
    if x0 is None or top is None or x1 is None or bottom is None:
        return False
    cx0, ctop, cx1, cbottom = crop
    return not (x1 < cx0 or x0 > cx1 or bottom < ctop or top > cbottom)


def _extract_page(
    page: pdfplumber.page.Page,
    page_number: int,
    crop: BBox | None,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []

    for i, ln in enumerate(page.lines, start=1):
        elements.append({
            "id": f"line#{i:04d}",
            "type": "line",
            "x0": _f(ln.get("x0")),
            "top": _f(ln.get("top")),
            "x1": _f(ln.get("x1")),
            "bottom": _f(ln.get("bottom")),
            "linewidth": _f(ln.get("linewidth")),
            "stroke": _color_to_hex(ln.get("stroke_color") or ln.get("non_stroking_color")),
        })

    for i, r in enumerate(page.rects, start=1):
        elements.append({
            "id": f"rect#{i:04d}",
            "type": "rect",
            "x0": _f(r.get("x0")),
            "top": _f(r.get("top")),
            "x1": _f(r.get("x1")),
            "bottom": _f(r.get("bottom")),
            "fill": _color_to_hex(r.get("non_stroking_color")),
            "stroke": _color_to_hex(r.get("stroke_color")),
        })

    # Curve buckets:
    #   rect_curve   — full rectangle (any angle); `corners` returned so the
    #                  frontend can draw the rotated polygon instead of the
    #                  axis-aligned bbox.
    #   rect_partial — strict U-shape (see _is_rect_partial for filter rules).
    #   curve        — everything else.
    # inferred_rect pairing has been disabled — it produced too many false
    # matches in real drawings. We keep `_partials_pair` available for future
    # use but skip the pass.
    page_h = float(page.height)
    curve_idx = 0
    rect_curve_idx = 0
    rect_partial_idx = 0

    for c in page.curves:
        pts_raw = c.get("pts") or []
        points: list[list[float]] = []
        for p in pts_raw:
            try:
                x, y = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            points.append([x, page_h - y])

        is_filled = bool(c.get("fill", False))
        if _is_rectlike_curve(c):
            corners_bl = rect_corners_from_curve(c)
            corners_tl: list[list[float]] = []
            if corners_bl is not None:
                corners_tl = [[x, page_h - y] for x, y in corners_bl]
            rect_curve_idx += 1
            elements.append({
                "id": f"rect_curve#{rect_curve_idx:04d}",
                "type": "rect_curve",
                "x0": _f(c.get("x0")),
                "top": _f(c.get("top")),
                "x1": _f(c.get("x1")),
                "bottom": _f(c.get("bottom")),
                "points": points,
                "corners": corners_tl,
                "stroke": _color_to_hex(
                    c.get("stroking_color") or c.get("stroke_color")
                ),
                "fill": _color_to_hex(c.get("non_stroking_color")) if is_filled else None,
            })
            continue

        partial_bbox = _is_rect_partial(c)
        if partial_bbox is not None:
            x0_bl, y0_bl, x1_bl, y1_bl = partial_bbox
            rect_partial_idx += 1
            elements.append({
                "id": f"rect_partial#{rect_partial_idx:04d}",
                "type": "rect_partial",
                "x0": x0_bl,
                "top": page_h - y1_bl,
                "x1": x1_bl,
                "bottom": page_h - y0_bl,
                "points": points,
                "stroke": _color_to_hex(
                    c.get("stroking_color") or c.get("stroke_color")
                ),
                "fill": _color_to_hex(c.get("non_stroking_color")) if is_filled else None,
            })
            continue

        curve_idx += 1
        elements.append({
            "id": f"curve#{curve_idx:04d}",
            "type": "curve",
            "x0": _f(c.get("x0")),
            "top": _f(c.get("top")),
            "x1": _f(c.get("x1")),
            "bottom": _f(c.get("bottom")),
            "points": points,
        })

    for i, ch in enumerate(page.chars, start=1):
        elements.append({
            "id": f"char#{i:05d}",
            "type": "char",
            "text": ch.get("text", ""),
            "x0": _f(ch.get("x0")),
            "top": _f(ch.get("top")),
            "x1": _f(ch.get("x1")),
            "bottom": _f(ch.get("bottom")),
            "fontname": ch.get("fontname"),
            "size": _f(ch.get("size")),
            "fill": _color_to_hex(ch.get("non_stroking_color")),
        })

    for i, w in enumerate(page.extract_words(), start=1):
        elements.append({
            "id": f"word#{i:04d}",
            "type": "word",
            "text": w.get("text", ""),
            "x0": _f(w.get("x0")),
            "top": _f(w.get("top")),
            "x1": _f(w.get("x1")),
            "bottom": _f(w.get("bottom")),
        })

    if crop is not None:
        elements = [el for el in elements if _intersects(el, crop)]

    return {
        "page_number": page_number,
        "width": float(page.width),
        "height": page_h,
        "elements": elements,
    }


def extract_pdf(
    data: bytes,
    filename: str,
    crops: dict[int, BBox] | None = None,
) -> dict[str, Any]:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if crops:
            pages = [
                _extract_page(pdf.pages[n - 1], n, bbox)
                for n, bbox in sorted(crops.items())
                if 1 <= n <= len(pdf.pages)
            ]
        else:
            pages = [
                _extract_page(p, idx, None)
                for idx, p in enumerate(pdf.pages, start=1)
            ]
        return {
            "filename": filename,
            "page_count": len(pdf.pages),
            "pages": pages,
        }
