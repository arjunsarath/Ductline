from __future__ import annotations

import io
import math
import re
from statistics import median
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
import pytesseract
from PIL import Image

BBox = tuple[float, float, float, float]  # (x0, top, x1, bottom) — top-left origin, PDF points

# Tesseract reads the `ø` glyph in CAD fonts as one of these chars. `O`/`o`/`D`/`d`
# show up when the slash is faint; `@`/`°` when it's bold; `6` when the loop is
# open; `¢` when the slash is read as the cent sign. The glyph also routinely
# splits into TWO characters in a row (`°@`, `°¢`) when Tesseract treats the
# top of the circle and the body as separate tokens — hence the `+` below.
_O_SLASH_SUBSTITUTES = "@°ØøOoDd6¢"
_CALLOUT_RE = re.compile(
    rf'^\s*(\d+(?:\.\d+)?)\s*(?:"|″)?\s*([{re.escape(_O_SLASH_SUBSTITUTES)}]+)\s*$'
)

# Single-line OCR config for the per-box pass. PSM 7 = "treat the image as a
# single text line" — the right segmenter for a label-sized crop containing
# just the callout. LSTM-only (`--oem 1`) is the most accurate engine. No
# whitelist (see below).
#
# We deliberately do NOT restrict the character set: empirically `14"@` drops
# from 76% to 0% confidence with a whitelist that excludes `"`, and `"` can't
# be safely included because pytesseract's shlex-split chokes on it.
_BOX_OCR_CONFIG = '--oem 1 --psm 7'
# Tiny crops can afford much higher DPI than the full-page pass — a 40pt × 16pt
# callout at 1200 DPI is ~667 × 267 px, still cheap, and gives Tesseract crisp
# edges instead of the aliased glyphs from the 600 DPI full-crop pass.
_BOX_OCR_DPI = 1200
# Inset before rendering so the box's own border rule doesn't intrude on the
# OCR'd glyphs.
_BOX_INSET_PT = 1.0

# Tesseract confidence floor. The `@`/`°`/`¢`/`6` substitution for `ø` is itself
# a miss-read, so legit duct callouts often score in the 20s–60s — especially
# when Tesseract splits Ø into two characters (e.g. `°@`) and reports a low
# confidence on each piece. We rely on the downstream gates (regex shape,
# diameter range, geometry plausibility, band-mean outliers) to reject the
# truly-wrong reads, so this floor only has to filter out random gibberish.
_MIN_CONF = 20
# Real HVAC ducts in typical drawings sit inside this range; rejects stray
# year numbers ("2024@") and footer page numbers that pass the regex.
_MIN_DIAMETER_IN = 2.0
_MAX_DIAMETER_IN = 80.0

# A callout box is small and elongated — wide enough for "NN\"Ø" at ~10pt
# font, tall enough for one line of text. These bounds reject duct rectangles
# (much larger) and stray glyph fragments (smaller).
_MIN_CALLOUT_WIDTH_PT = 15.0
_MAX_CALLOUT_WIDTH_PT = 80.0
_MIN_CALLOUT_HEIGHT_PT = 8.0
_MAX_CALLOUT_HEIGHT_PT = 22.0
_MIN_CALLOUT_ASPECT = 1.4

# All callouts on one drawing share an exact scale; differences come from
# picking the wrong wall pair on a single duct, not measurement noise. Keep
# pts/in values within ±10% of the median, mean the survivors.
_SCALE_BAND_PCT = 0.10

# Search window around a callout centre for candidate duct rectangles (PDF
# points). 120pt ≈ 1.7 inches at 1:1 — wide enough for the longest duct labels
# seen, narrow enough to avoid grabbing the neighbouring duct.
_GEOMETRY_SEARCH_RADIUS = 120.0
# Plausible drawing scales for HVAC plans: 0.3 to 5 PDF pts per real-world inch.
# 5 pts/in ≈ 1:14 (drawings rarely go larger than 1:16 for whole-floor plans);
# 0.3 ≈ 1:240. The duct rect's shorter side, divided by the declared diameter,
# must fall inside this range — kills equipment boxes, frames, and callout
# boxes that happen to sit near the callout but aren't ducts.
_MIN_SCALE_PTS_PER_IN = 0.3
_MAX_SCALE_PTS_PER_IN = 5.0


def _normalise_callout(raw: str) -> tuple[str, float] | None:
    """Return (normalised_text, diameter_inches) or None if the token isn't a callout."""
    m = _CALLOUT_RE.match(raw)
    if not m:
        return None
    try:
        diameter = float(m.group(1))
    except ValueError:
        return None
    if not (_MIN_DIAMETER_IN <= diameter <= _MAX_DIAMETER_IN):
        return None
    # Canonical form for the response uses the real ø glyph.
    text = f'{m.group(1).rstrip(".")}"ø' if "." in m.group(1) else f'{int(diameter)}"ø'
    return text, diameter


def _render_crop(
    pdf: pdfium.PdfDocument,
    page_index: int,
    crop_bbox: BBox,
    dpi: int,
) -> Image.Image:
    """Render only the crop region at the requested DPI. Returns RGB PIL image."""
    page = pdf[page_index]
    page_w = page.get_width()
    page_h = page.get_height()
    x0, top, x1, bottom = crop_bbox

    # pypdfium2 crop = (left, bottom, right, top), each value the distance to
    # cut from that page edge. `top` in top-left coords is already the distance
    # from the top edge; (page_h - bottom) gives the distance from the bottom.
    crop_tuple = (
        max(0.0, x0),
        max(0.0, page_h - bottom),
        max(0.0, page_w - x1),
        max(0.0, top),
    )
    scale = dpi / 72.0
    bitmap = page.render(scale=scale, crop=crop_tuple)
    return bitmap.to_pil().convert("RGB")


def _strip_non_black(img: Image.Image, max_luma: float) -> Image.Image:
    """Reduce a rendered RGB crop to pure-black-on-white before OCR.

    The PDF is vector, so non-ink content (hatching, screened fills, light
    annotations) renders as predictable greys. Knocking those out before
    Tesseract sees them cuts false positives from line art that resembles text.
    `max_luma` is in [0, 1] and is converted to the 0–255 cutoff."""
    cutoff = int(round(max_luma * 255))
    return (
        img.convert("L")
        .point(lambda p: 0 if p < cutoff else 255, mode="L")
        .convert("RGB")
    )


def _filter_callout_candidate_boxes(
    rects: list[dict[str, float]],
) -> list[dict[str, float]]:
    """Keep rectangles sized like callout labels: small, elongated. Drops the
    duct rectangles themselves (much wider) and stray glyph fragments."""
    out: list[dict[str, float]] = []
    for r in rects:
        w = r["x1"] - r["x0"]
        h = r["bottom"] - r["top"]
        if w <= 0 or h <= 0:
            continue
        if not (_MIN_CALLOUT_WIDTH_PT <= w <= _MAX_CALLOUT_WIDTH_PT):
            continue
        if not (_MIN_CALLOUT_HEIGHT_PT <= h <= _MAX_CALLOUT_HEIGHT_PT):
            continue
        if max(w / h, h / w) < _MIN_CALLOUT_ASPECT:
            continue
        out.append(r)
    return out


def _dedupe_boxes(
    boxes: list[dict[str, float]], tol: float = 2.0
) -> list[dict[str, float]]:
    """Drop boxes whose centres coincide. CAD exporters routinely emit both a
    `re` operator and a 4-line path for the same rectangle — we'd otherwise
    OCR the same callout twice and double-count its scale."""
    kept: list[dict[str, float]] = []
    for b in boxes:
        cx = 0.5 * (b["x0"] + b["x1"])
        cy = 0.5 * (b["top"] + b["bottom"])
        if any(
            abs(0.5 * (k["x0"] + k["x1"]) - cx) < tol
            and abs(0.5 * (k["top"] + k["bottom"]) - cy) < tol
            for k in kept
        ):
            continue
        kept.append(b)
    return kept


def _ocr_callout_box(
    pdf: pdfium.PdfDocument,
    page_index: int,
    box: dict[str, float],
    black_threshold: float,
) -> tuple[str, float, int, BBox] | None:
    """Render `box` at high DPI and OCR with PSM 7. Returns
    (canonical_text, diameter_inches, confidence, text_bbox_pdf) or None.

    The inset trims the box's own border out of the OCR'd image; otherwise
    Tesseract sees the rule as a vertical bar and corrupts the leading digit."""
    inset: BBox = (
        box["x0"] + _BOX_INSET_PT,
        box["top"] + _BOX_INSET_PT,
        box["x1"] - _BOX_INSET_PT,
        box["bottom"] - _BOX_INSET_PT,
    )
    if inset[2] <= inset[0] or inset[3] <= inset[1]:
        return None

    img = _render_crop(pdf, page_index, inset, _BOX_OCR_DPI)
    img = _strip_non_black(img, black_threshold)
    data = pytesseract.image_to_data(
        img, config=_BOX_OCR_CONFIG, output_type=pytesseract.Output.DICT
    )

    tokens: list[tuple[str, int, tuple[int, int, int, int]]] = []
    for i in range(len(data["text"])):
        txt = data["text"][i]
        if not txt or not txt.strip():
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (TypeError, ValueError):
            continue
        tokens.append(
            (
                txt,
                conf,
                (
                    int(data["left"][i]),
                    int(data["top"][i]),
                    int(data["width"][i]),
                    int(data["height"][i]),
                ),
            )
        )
    if not tokens:
        return None

    # PSM 7 typically returns one word per token. Try the joined run first
    # (covers `"8"`, `"`, `"Ø"` split into three), then individual tokens
    # (covers `"8\"Ø"` returned as one).
    joined = "".join(t[0] for t in tokens)
    matched = _normalise_callout(joined)
    if matched is None:
        for txt, _, _ in tokens:
            matched = _normalise_callout(txt)
            if matched is not None:
                break
    if matched is None:
        print(f"[detect-scale] no callout in box {joined!r}")
        return None

    text, diameter_in = matched
    min_conf = min(t[1] for t in tokens)
    if min_conf < _MIN_CONF:
        print(f"[detect-scale] low conf {min_conf} for {joined!r} → {text!r}")
        return None

    # Tight text bbox = union of token sub-bboxes, mapped back to PDF pts.
    left_px = min(t[2][0] for t in tokens)
    top_px = min(t[2][1] for t in tokens)
    right_px = max(t[2][0] + t[2][2] for t in tokens)
    bottom_px = max(t[2][1] + t[2][3] for t in tokens)
    px_to_pt = 72.0 / _BOX_OCR_DPI
    text_bbox: BBox = (
        inset[0] + left_px * px_to_pt,
        inset[1] + top_px * px_to_pt,
        inset[0] + right_px * px_to_pt,
        inset[1] + bottom_px * px_to_pt,
    )
    print(f"[detect-scale] box-OCR {joined!r} → {text!r} conf={min_conf}")
    return text, diameter_in, min_conf, text_bbox


def _aggregate_scale(scales: list[float]) -> float | None:
    """Mean of pts/in values within ±_SCALE_BAND_PCT of the median. Real
    callouts on one drawing all share an exact scale, so outliers are extreme
    (wrong wall pair, leader line) rather than Gaussian noise — band+mean
    rejects them cleanly. Falls back to the median when nothing survives the
    band (defensive; shouldn't occur since the median is itself in band)."""
    if not scales:
        return None
    med = median(scales)
    lo = med * (1 - _SCALE_BAND_PCT)
    hi = med * (1 + _SCALE_BAND_PCT)
    kept = [v for v in scales if lo <= v <= hi]
    rejected = len(scales) - len(kept)
    if rejected:
        print(f"[detect-scale] dropped {rejected} outlier scale(s); mean of {len(kept)}")
    return sum(kept) / len(kept) if kept else med


def _is_black(color: Any, max_luma: float) -> bool:
    """True for pure-ish black ink. Mirrors the raster `_strip_non_black` pass
    so vector + raster preprocessing stay consistent. `None` is treated as
    black because pdfplumber omits the colour key when the PDF leaves it at
    the default (which is black in DeviceGray)."""
    if color is None:
        return True
    if not isinstance(color, (tuple, list)):
        return False
    try:
        nums = [float(c) for c in color]
    except (TypeError, ValueError):
        return False
    if len(nums) == 1:
        return nums[0] <= max_luma
    if len(nums) == 3:
        return max(nums) <= max_luma
    if len(nums) == 4:  # CMYK — black means K high, CMY low.
        return nums[3] >= 1 - max_luma and max(nums[:3]) <= max_luma
    return False


def _round_pts(pts: list[Any]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in pts:
        if len(p) < 2:
            continue
        try:
            out.append((round(float(p[0]), 1), round(float(p[1]), 1)))
        except (TypeError, ValueError):
            continue
    return out


def _dedupe_consecutive(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or out[-1] != p:
            out.append(p)
    # Drop a final repeated start (closed path).
    if len(out) >= 2 and out[0] == out[-1]:
        out = out[:-1]
    return out


def _has_axis_rectangle_in_path(rounded: list[tuple[float, float]]) -> bool:
    """The path contains an axis-aligned rectangle: all 4 bbox corners are
    visited AND at least 4 distinct corner-to-corner segments are axis-aligned.
    The segment count is what rejects an X-marker, whose 4 endpoints happen to
    coincide with bbox corners but whose diagonal strokes contribute only 0–2
    axis-aligned corner-segments instead of the rectangle's 4."""
    if len(rounded) < 4:
        return False
    x_min = min(p[0] for p in rounded)
    x_max = max(p[0] for p in rounded)
    y_min = min(p[1] for p in rounded)
    y_max = max(p[1] for p in rounded)
    if x_max - x_min < 3 or y_max - y_min < 3:
        return False
    corners = {(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)}
    pts_set = set(rounded)
    if not corners.issubset(pts_set):
        return False
    edges: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for i in range(len(rounded)):
        a = rounded[i]
        b = rounded[(i + 1) % len(rounded)]
        if a == b:
            continue
        if a not in corners or b not in corners:
            continue
        if a[0] != b[0] and a[1] != b[1]:
            continue  # diagonal between two corners — ignore
        edges.add(tuple(sorted([a, b])))
    return len(edges) >= 4


def _convex_hull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone-chain convex hull. Returns hull vertices in CCW order."""
    pts = sorted(set(pts))
    if len(pts) <= 1:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _is_rotated_rectangle(pts: list[tuple[float, float]]) -> bool:
    """4 vertices in cyclic order forming a rectangle. Diagonals must share
    the same midpoint (parallelogram) and be equal length (which then forces
    the parallelogram to be a rectangle)."""
    if len(pts) != 4:
        return False
    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0
    import math as _math
    ordered = sorted(pts, key=lambda p: _math.atan2(p[1] - cy, p[0] - cx))
    mx1 = (ordered[0][0] + ordered[2][0]) / 2.0
    my1 = (ordered[0][1] + ordered[2][1]) / 2.0
    mx2 = (ordered[1][0] + ordered[3][0]) / 2.0
    my2 = (ordered[1][1] + ordered[3][1]) / 2.0
    if abs(mx1 - cx) > 1.0 or abs(my1 - cy) > 1.0:
        return False
    if abs(mx2 - cx) > 1.0 or abs(my2 - cy) > 1.0:
        return False
    diag1 = _math.hypot(ordered[0][0] - ordered[2][0], ordered[0][1] - ordered[2][1])
    diag2 = _math.hypot(ordered[1][0] - ordered[3][0], ordered[1][1] - ordered[3][1])
    if diag1 < 3 or diag2 < 3:
        return False
    return abs(diag1 - diag2) <= max(1.0, 0.02 * max(diag1, diag2))


def rect_corners_from_curve(curve: dict[str, Any]) -> list[list[float]] | None:
    """Return the 4 corner points of the rectangle the curve represents.
    For axis-aligned paths the bbox corners; for rotated rectangles the 4
    hull vertices in CCW order. None if the curve is not a rectangle."""
    pts = curve.get("pts") or []
    rounded = _round_pts(pts)
    if len(rounded) < 4:
        return None
    if _has_axis_rectangle_in_path(rounded):
        x_min = min(p[0] for p in rounded)
        x_max = max(p[0] for p in rounded)
        y_min = min(p[1] for p in rounded)
        y_max = max(p[1] for p in rounded)
        return [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]
    hull = _convex_hull(rounded)
    if len(hull) == 4 and _is_rotated_rectangle(hull):
        import math as _math
        cx = sum(p[0] for p in hull) / 4.0
        cy = sum(p[1] for p in hull) / 4.0
        ordered = sorted(hull, key=lambda p: _math.atan2(p[1] - cy, p[0] - cx))
        return [[x, y] for x, y in ordered]
    return None


def _is_rectlike_curve(curve: dict[str, Any]) -> bool:
    """True when the curve's path encloses a rectangle. Covers:
    1. Axis-aligned rectangles (with or without redundant moveto's)
    2. Axis-aligned rectangles with extra interior strokes (e.g. X markers)
    3. Rotated rectangles, regardless of point ordering or extra path points
    Rejects U-curves, triangles, lone axis-aligned X-markers."""
    pts = curve.get("pts") or []
    if len(pts) < 4:
        return False
    rounded = _round_pts(pts)
    if len(rounded) < 4:
        return False

    # Case 1 & 2: axis-aligned rectangle, possibly with interior strokes like
    # an X marker. Inside `_has_axis_rectangle_in_path` we already require ≥4
    # axis-aligned corner-segments, which rejects a lone axis-aligned X.
    if _has_axis_rectangle_in_path(rounded):
        return True

    # Case 3: rotated. The convex hull is order- and noise-tolerant — a
    # rotated rectangle has exactly 4 hull vertices satisfying the
    # equal-diagonal / shared-midpoint conditions. We do NOT require the
    # path to trace the hull sides: CAD exporters often emit each side via a
    # `c` (cubic) operator whose control points break the consecutive-pair
    # check, so a strict side-drawn requirement throws away real rectangles.
    # A rotated X-marker false positive is rare in HVAC plans.
    hull = _convex_hull(rounded)
    return len(hull) == 4 and _is_rotated_rectangle(hull)


def _is_rect_partial(curve: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Detect a 3-segment axis-aligned U-shape — the visible end-cap of a
    rectangle whose middle is occluded by another element. Returns
    (x0, y0, x1, y1) in PDF bottom-left coords if matched, else None.

    Tight requirements (loose was too permissive — leader-line arrows and
    glyph fragments were sneaking through):
    - 4 distinct vertices; path does not close.
    - 3 axis-aligned segments forming a literal U (parallel arms, perpendicular cap).
    - Arm length ≥ 2× cap length (real duct ends are long arms with a short cap).
    - Bbox at least 6×6pt.
    """
    pts = curve.get("pts") or []
    if len(pts) < 4:
        return None
    rounded = _round_pts(pts)
    if len(rounded) < 4:
        return None
    deduped = _dedupe_consecutive(rounded)
    if len(deduped) != 4:
        return None
    if deduped[0] == deduped[-1]:
        return None
    for i in range(3):
        a, b = deduped[i], deduped[i + 1]
        if a[0] != b[0] and a[1] != b[1]:
            return None
    s0 = (deduped[1][0] - deduped[0][0], deduped[1][1] - deduped[0][1])
    s1 = (deduped[2][0] - deduped[1][0], deduped[2][1] - deduped[1][1])
    s2 = (deduped[3][0] - deduped[2][0], deduped[3][1] - deduped[2][1])
    s0_horiz = s0[1] == 0 and s0[0] != 0
    s0_vert = s0[0] == 0 and s0[1] != 0
    s2_horiz = s2[1] == 0 and s2[0] != 0
    s2_vert = s2[0] == 0 and s2[1] != 0
    if not ((s0_horiz and s2_horiz) or (s0_vert and s2_vert)):
        return None
    if s0_horiz and not (s1[0] == 0 and s1[1] != 0):
        return None
    if s0_vert and not (s1[1] == 0 and s1[0] != 0):
        return None
    s0_len = abs(s0[0]) + abs(s0[1])
    s1_len = abs(s1[0]) + abs(s1[1])
    s2_len = abs(s2[0]) + abs(s2[1])
    # Both arms should be longer than the cap, and similar to each other.
    arm = min(s0_len, s2_len)
    cap = s1_len
    if arm < 2 * cap:
        return None
    if abs(s0_len - s2_len) > 0.3 * max(s0_len, s2_len):
        return None
    xs = [p[0] for p in deduped]
    ys = [p[1] for p in deduped]
    if (max(xs) - min(xs)) < 6 or (max(ys) - min(ys)) < 6:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _partials_pair(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """Two U-shaped partials face each other when their long sides are
    collinear and their cap sides face inward, leaving a gap between. Returns
    the inferred enclosing rectangle (a's bbox merged with b's) or None.

    The partials are bbox tuples (x0, y0, x1, y1) in PDF bottom-left coords."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    eps = 1.0
    # Horizontal duct: long sides collinear (top + bottom y values aligned)
    # and the partials sit on opposite ends of the duct so their x ranges
    # don't overlap.
    if abs(ay0 - by0) <= eps and abs(ay1 - by1) <= eps:
        x_overlap = min(ax1, bx1) - max(ax0, bx0)
        if x_overlap > 0:
            return None  # overlap — not a duct with a gap in the middle
        return (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1))
    # Vertical duct: long sides collinear (left + right x values aligned).
    if abs(ax0 - bx0) <= eps and abs(ax1 - bx1) <= eps:
        y_overlap = min(ay1, by1) - max(ay0, by0)
        if y_overlap > 0:
            return None
        return (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1))
    return None


def _load_page_lines_and_rects(
    pdf_bytes: bytes,
    page_number: int,
    crop_bbox: BBox,
    max_luma: float,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """Black-stroked lines and rectangles (top-left origin) inside the crop.

    Rectangles include both `page.rects` (true `re` operator) and rect-like
    entries from `page.curves` (4-line paths that CAD exporters emit instead
    of using `re`). Same colour filter applies to both."""
    cx0, ctop, cx1, cbottom = crop_bbox
    lines: list[dict[str, float]] = []
    rects: list[dict[str, float]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as plumber:
        page = plumber.pages[page_number - 1]
        for ln in page.lines:
            if not _is_black(
                ln.get("stroking_color") or ln.get("stroke_color"), max_luma
            ):
                continue
            x0, top = ln.get("x0"), ln.get("top")
            x1, bottom = ln.get("x1"), ln.get("bottom")
            if None in (x0, top, x1, bottom):
                continue
            if x1 < cx0 or x0 > cx1 or bottom < ctop or top > cbottom:
                continue
            lines.append({
                "x0": float(x0), "top": float(top),
                "x1": float(x1), "bottom": float(bottom),
            })

        def _add_rect(src: dict[str, Any]) -> None:
            stroke = src.get("stroking_color") or src.get("stroke_color")
            fill = src.get("non_stroking_color")
            if not (_is_black(stroke, max_luma) or _is_black(fill, max_luma)):
                return
            x0, top = src.get("x0"), src.get("top")
            x1, bottom = src.get("x1"), src.get("bottom")
            if None in (x0, top, x1, bottom):
                return
            if x1 < cx0 or x0 > cx1 or bottom < ctop or top > cbottom:
                return
            rects.append({
                "x0": float(x0), "top": float(top),
                "x1": float(x1), "bottom": float(bottom),
            })

        for r in page.rects:
            _add_rect(r)
        for cv in page.curves:
            if _is_rectlike_curve(cv):
                _add_rect(cv)
    return lines, rects


def _same_rect(a: dict[str, float], b: dict[str, float], tol: float = 1.0) -> bool:
    return (
        abs(a["x0"] - b["x0"]) < tol
        and abs(a["top"] - b["top"]) < tol
        and abs(a["x1"] - b["x1"]) < tol
        and abs(a["bottom"] - b["bottom"]) < tol
    )


def _wall_pair_from_rect(
    rect: dict[str, float], drawn_pts: float
) -> dict[str, Any]:
    """Synthesise a wall pair from a duct rectangle's two long edges. Used by
    the overlay to draw the measurement line so the user can see WHICH rect
    the algorithm chose to associate with each callout."""
    x0, top, x1, bottom = rect["x0"], rect["top"], rect["x1"], rect["bottom"]
    w = x1 - x0
    h = bottom - top
    if w >= h:
        # Horizontal duct: the two long edges are the top and bottom of the
        # rect, perpendicular to the diameter direction.
        a = {"x0": x0, "top": top, "x1": x1, "bottom": top}
        b = {"x0": x0, "top": bottom, "x1": x1, "bottom": bottom}
    else:
        # Vertical duct: the left and right edges.
        a = {"x0": x0, "top": top, "x1": x0, "bottom": bottom}
        b = {"x0": x1, "top": top, "x1": x1, "bottom": bottom}
    return {"a": a, "b": b, "distance_pts": drawn_pts}


def _infer_geometry(
    callout_bbox: BBox,
    diameter_in: float,
    all_rects: list[dict[str, float]],
    exclude_rect: dict[str, float],
) -> tuple[BBox, float, float, list[dict[str, Any]]] | None:
    """Find the duct rectangle for this callout and read its drawn diameter
    off the rect's shorter side. Returns (duct_bbox, drawn_pts, scale,
    wall_pairs) or None.

    Two-stage selection:
      1. ASSOCIATED — rects whose bbox, expanded by one callout's worth of
         padding in each direction, contains the callout's centre. This
         picks up the duct whether the callout sits inside it (callout
         centred on the duct) or just outside it (leader-line layout).
      2. NEAR       — fallback for callouts with no associated rect: pick
         the rect whose centre is closest, ties broken by larger area.

    Within ASSOCIATED we always prefer the **largest** qualifying
    rectangle by area. Real ducts are long, thick rectangles; the false
    positives we want to avoid (dimension stubs, arrowhead boxes, leader
    marks) are short and narrow, so largest-area is what disambiguates the
    duct from those near-callout artefacts.

    `exclude_rect` is the callout's own bordered box; it's always
    ASSOCIATED and would win otherwise."""
    cx = 0.5 * (callout_bbox[0] + callout_bbox[2])
    cy = 0.5 * (callout_bbox[1] + callout_bbox[3])
    cw = callout_bbox[2] - callout_bbox[0]
    ch = callout_bbox[3] - callout_bbox[1]
    # Expansion = one callout's worth in each direction. With a 30×15 callout
    # this admits ducts up to ~30pt away from the callout's bbox — enough to
    # cover typical leader-line layouts without sweeping in unrelated rects.
    expansion = max(cw, ch)

    min_drawn = _MIN_SCALE_PTS_PER_IN * diameter_in
    max_drawn = min(_GEOMETRY_SEARCH_RADIUS, _MAX_SCALE_PTS_PER_IN * diameter_in)
    if min_drawn >= max_drawn:
        return None

    associated: list[tuple[float, dict[str, float]]] = []
    nearby: list[tuple[float, float, dict[str, float]]] = []

    for r in all_rects:
        if _same_rect(r, exclude_rect):
            continue
        w = r["x1"] - r["x0"]
        h = r["bottom"] - r["top"]
        if w <= 0 or h <= 0:
            continue
        shorter = min(w, h)
        if not (min_drawn <= shorter <= max_drawn):
            continue
        area = w * h

        if (r["x0"] - expansion) <= cx <= (r["x1"] + expansion) \
                and (r["top"] - expansion) <= cy <= (r["bottom"] + expansion):
            associated.append((area, r))
            continue

        rcx = 0.5 * (r["x0"] + r["x1"])
        rcy = 0.5 * (r["top"] + r["bottom"])
        d = math.hypot(rcx - cx, rcy - cy)
        if d <= _GEOMETRY_SEARCH_RADIUS:
            nearby.append((d, area, r))

    if associated:
        rect = max(associated, key=lambda x: x[0])[1]
    elif nearby:
        nearby.sort(key=lambda x: (x[0], -x[1]))
        rect = nearby[0][2]
    else:
        return None

    drawn_pts = min(rect["x1"] - rect["x0"], rect["bottom"] - rect["top"])
    duct_bbox: BBox = (rect["x0"], rect["top"], rect["x1"], rect["bottom"])
    scale = drawn_pts / diameter_in
    return duct_bbox, drawn_pts, scale, [_wall_pair_from_rect(rect, drawn_pts)]


def detect_scale_callouts(
    pdf_bytes: bytes,
    page_number: int,
    crop_bbox: BBox,
    black_threshold: float = 0.05,
) -> dict[str, Any]:
    """Detect diameter callouts in `crop_bbox` and infer a drawing scale.

    Pipeline:
      1. Vector pass — pull every black rectangle in the crop, filter to
         callout-sized boxes (small, elongated), dedupe.
      2. OCR pass — render each candidate at high DPI and OCR with PSM 7.
         Tiny, clean crops dramatically outperform sparse OCR on the whole
         crop, and unrelated black ink in the page can't trigger a false
         positive because we only OCR inside callout-shaped boxes.
      3. Geometry pass — for each decoded callout, find the duct wall pair
         and derive a per-callout pts/in.
      4. Aggregation — band-mean across callouts (outliers dropped).

    Returns the payload described in the API contract; the caller maps it to
    a Pydantic response model.
    """
    if page_number < 1:
        raise ValueError(f"page_number must be >= 1, got {page_number}")

    _, all_rects = _load_page_lines_and_rects(
        pdf_bytes, page_number, crop_bbox, black_threshold
    )
    candidates = _dedupe_boxes(_filter_callout_candidate_boxes(all_rects))
    print(
        f"[detect-scale] {len(all_rects)} rects in crop → "
        f"{len(candidates)} callout candidates"
    )

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        page_count = len(pdf)
        if page_number > page_count:
            raise ValueError(
                f"page_number {page_number} out of range (1..{page_count})"
            )
        page_index = page_number - 1
        decoded: list[tuple[dict[str, Any], dict[str, float]]] = []
        for box in candidates:
            res = _ocr_callout_box(pdf, page_index, box, black_threshold)
            if res is None:
                continue
            text, diameter_in, conf, text_bbox = res
            decoded.append(
                (
                    {
                        "text": text,
                        "raw_text": text,
                        "diameter_in": diameter_in,
                        "confidence": conf,
                        "bbox": text_bbox,
                    },
                    box,
                )
            )
    finally:
        pdf.close()
    print(f"[detect-scale] {len(decoded)} callouts decoded")

    out_callouts: list[dict[str, Any]] = []
    scales: list[float] = []
    for idx, (c, rect) in enumerate(decoded, start=1):
        geo = _infer_geometry(c["bbox"], c["diameter_in"], all_rects, rect)
        if geo is not None:
            duct_bbox, drawn_pts, scale, wall_pairs = geo
            scales.append(scale)
        else:
            duct_bbox, drawn_pts, scale, wall_pairs = None, None, None, []

        out_callouts.append({
            "id": f"callout#{idx:04d}",
            "text": c["text"],
            "diameter_in": c["diameter_in"],
            "raw_text": c["raw_text"],
            "confidence": c["confidence"],
            "bbox": {
                "x0": c["bbox"][0],
                "top": c["bbox"][1],
                "x1": c["bbox"][2],
                "bottom": c["bbox"][3],
            },
            "enclosing_rect": rect,
            "duct_bbox": (
                {
                    "x0": duct_bbox[0],
                    "top": duct_bbox[1],
                    "x1": duct_bbox[2],
                    "bottom": duct_bbox[3],
                }
                if duct_bbox is not None
                else None
            ),
            "drawn_diameter_pts": drawn_pts,
            "scale_pts_per_inch": scale,
            "wall_pairs": wall_pairs,
        })

    return {
        "page_number": page_number,
        "dpi": _BOX_OCR_DPI,
        "callouts": out_callouts,
        "drawing_scale_pts_per_inch": _aggregate_scale(scales),
        "callout_count": len(out_callouts),
    }
