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
# show up when the slash is faint; `@`/`°` when it's bold; `6` when the loop is open.
_O_SLASH_SUBSTITUTES = "@°ØøOoDd6"
_CALLOUT_RE = re.compile(
    rf'^\s*(\d+(?:\.\d+)?)\s*(?:"|″)?\s*([{re.escape(_O_SLASH_SUBSTITUTES)}])\s*$'
)

# Tesseract config. LSTM-only engine (`--oem 1`) is the most accurate. Sparse
# text segmentation (`--psm 11`) matches engineering-drawing layouts where
# labels are scattered, not paragraphed. No character whitelist — restricting
# the vocabulary forces wrong substitutions for `"` and tanks confidence on
# legit callouts (verified empirically: `14"@` drops from 76% to 0% with a
# whitelist that excludes `"`, and `"` can't be safely included because
# pytesseract's shlex-split chokes on it).
_TESSERACT_CONFIG = '--oem 1 --psm 11'

# Tesseract confidence floor. The `@`/`°`/`6` substitution for `ø` is itself
# a Tesseract miss-read, so legit duct callouts often score in the 40s–60s.
# We rely on the gap-plausibility check below to reject the truly-wrong reads.
_MIN_CONF = 45
# Real HVAC ducts in typical drawings sit inside this range; rejects stray
# year numbers ("2024@") and footer page numbers that pass the regex.
_MIN_DIAMETER_IN = 2.0
_MAX_DIAMETER_IN = 80.0

# Search window around a callout centre for candidate duct walls (PDF points).
# 120pt ≈ 1.7 inches at 1:1 — wide enough for the longest duct labels seen,
# narrow enough to avoid grabbing the neighbouring duct.
_GEOMETRY_SEARCH_RADIUS = 120.0
# ±5° tolerance pairs up walls drawn slightly off-axis (CAD snap drift).
_PARALLEL_TOL_DEG = 5.0
# A duct wall pair under this is almost certainly a leader-line arrowhead,
# not the duct itself.
_MIN_WALL_GAP = 3.0
# Plausible drawing scales for HVAC plans: 0.3 to 5 PDF pts per real-world inch.
# 5 pts/in ≈ 1:14 (drawings rarely go larger than 1:16 for whole-floor plans);
# 0.3 ≈ 1:240. A detected gap must fit inside this range when divided by the
# declared diameter — kills pairs grabbed from grid/border lines far across the page.
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


def _ocr_callouts(
    img: Image.Image,
    crop_bbox: BBox,
    dpi: int,
) -> list[dict[str, Any]]:
    """OCR the crop and emit normalised callouts with PDF-point bboxes."""
    data = pytesseract.image_to_data(
        img, config=_TESSERACT_CONFIG, output_type=pytesseract.Output.DICT
    )
    px_to_pt = 72.0 / dpi
    crop_x0, crop_top, _, _ = crop_bbox

    # Diagnostic dump: every token Tesseract returned with its confidence.
    all_tokens = [
        (data["text"][i], data["conf"][i])
        for i in range(len(data["text"]))
        if data["text"][i] and data["text"][i].strip()
    ]
    print(f"[detect-scale] {len(all_tokens)} tokens: {all_tokens[:60]}")

    callouts: list[dict[str, Any]] = []
    for i, raw in enumerate(data["text"]):
        if not raw or not raw.strip():
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (TypeError, ValueError):
            continue
        if conf < _MIN_CONF:
            continue
        normalised = _normalise_callout(raw)
        if normalised is None:
            continue
        text, diameter_in = normalised
        print(f"[detect-scale] match: {raw!r} → {text!r} conf={conf}")

        # Pixel bbox → PDF-point bbox (crop image origin maps to crop_x0/crop_top).
        left_px = data["left"][i]
        top_px = data["top"][i]
        w_px = data["width"][i]
        h_px = data["height"][i]
        bbox: BBox = (
            crop_x0 + left_px * px_to_pt,
            crop_top + top_px * px_to_pt,
            crop_x0 + (left_px + w_px) * px_to_pt,
            crop_top + (top_px + h_px) * px_to_pt,
        )
        callouts.append({
            "raw_text": raw,
            "text": text,
            "diameter_in": diameter_in,
            "confidence": conf,
            "bbox": bbox,
        })
    return callouts


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


def _enclosing_rect(
    bbox: BBox, rects: list[dict[str, float]]
) -> dict[str, float] | None:
    """Return the smallest rectangle whose interior contains the bbox centre."""
    cx = 0.5 * (bbox[0] + bbox[2])
    cy = 0.5 * (bbox[1] + bbox[3])
    matches = [
        r for r in rects
        if r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"]
    ]
    if not matches:
        return None
    return min(matches, key=lambda r: (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]))


def _line_angle_deg(ln: dict[str, float]) -> float:
    """Angle in degrees, normalised to [0, 180)."""
    dx = ln["x1"] - ln["x0"]
    dy = ln["bottom"] - ln["top"]
    angle = math.degrees(math.atan2(dy, dx))
    return angle % 180.0


def _perpendicular_distance(a: dict[str, float], b: dict[str, float]) -> float:
    """Distance from midpoint of `b` to the infinite line through `a`."""
    ax, ay = a["x0"], a["top"]
    bx, by = a["x1"], a["bottom"]
    px = 0.5 * (b["x0"] + b["x1"])
    py = 0.5 * (b["top"] + b["bottom"])
    dx, dy = bx - ax, by - ay
    norm = math.hypot(dx, dy)
    if norm == 0:
        return math.hypot(px - ax, py - ay)
    return abs(dy * px - dx * py + bx * ay - by * ax) / norm


def _infer_geometry(
    callout_bbox: BBox,
    diameter_in: float,
    lines: list[dict[str, float]],
) -> tuple[BBox, float, float, list[dict[str, Any]]] | None:
    """Return (duct_bbox, drawn_diameter_pts, scale_pts_per_inch, wall_pairs)
    or None.

    For a single callout, we pair up nearby parallel lines and pick the
    *largest* perpendicular gap that still implies a plausible drawing scale
    — this matches the duct's outer envelope rather than the inner cavity
    when both are drawn. The plausibility window is what stops a stray pair
    of axis/grid lines from getting picked up as walls."""
    cx = 0.5 * (callout_bbox[0] + callout_bbox[2])
    cy = 0.5 * (callout_bbox[1] + callout_bbox[3])

    # Diameter-specific gap window. Capped by the search radius too.
    min_gap = max(_MIN_WALL_GAP, _MIN_SCALE_PTS_PER_IN * diameter_in)
    max_gap = min(_GEOMETRY_SEARCH_RADIUS, _MAX_SCALE_PTS_PER_IN * diameter_in)
    if min_gap >= max_gap:
        return None

    nearby: list[tuple[dict[str, float], float]] = []
    for ln in lines:
        lcx = 0.5 * (ln["x0"] + ln["x1"])
        lcy = 0.5 * (ln["top"] + ln["bottom"])
        d = math.hypot(lcx - cx, lcy - cy)
        if d <= _GEOMETRY_SEARCH_RADIUS:
            nearby.append((ln, _line_angle_deg(ln)))

    best: tuple[float, dict[str, float], dict[str, float]] | None = None
    for i in range(len(nearby)):
        a, ang_a = nearby[i]
        for j in range(i + 1, len(nearby)):
            b, ang_b = nearby[j]
            diff = abs(ang_a - ang_b)
            diff = min(diff, 180.0 - diff)
            if diff > _PARALLEL_TOL_DEG:
                continue
            gap = _perpendicular_distance(a, b)
            if gap < min_gap or gap > max_gap:
                continue
            # Outer-wall preference: keep the largest qualifying gap.
            if best is None or gap > best[0]:
                best = (gap, a, b)

    if best is None:
        return None

    drawn_pts, a, b = best
    wall_pair = {
        "a": {"x0": a["x0"], "top": a["top"], "x1": a["x1"], "bottom": a["bottom"]},
        "b": {"x0": b["x0"], "top": b["top"], "x1": b["x1"], "bottom": b["bottom"]},
        "distance_pts": drawn_pts,
    }
    duct_bbox: BBox = (
        min(a["x0"], b["x0"]),
        min(a["top"], b["top"]),
        max(a["x1"], b["x1"]),
        max(a["bottom"], b["bottom"]),
    )
    scale = drawn_pts / diameter_in
    return duct_bbox, drawn_pts, scale, [wall_pair]


def detect_scale_callouts(
    pdf_bytes: bytes,
    page_number: int,
    crop_bbox: BBox,
    dpi: int = 600,
    black_threshold: float = 0.05,
) -> dict[str, Any]:
    """Detect diameter callouts in `crop_bbox` and infer a drawing scale.

    Returns the payload described in the API contract; the caller maps it to a
    Pydantic response model.
    """
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        page_count = len(pdf)
        if not (1 <= page_number <= page_count):
            raise ValueError(f"page_number {page_number} out of range (1..{page_count})")
        img = _render_crop(pdf, page_number - 1, crop_bbox, dpi)
    finally:
        pdf.close()

    img = _strip_non_black(img, black_threshold)
    raw_callouts = _ocr_callouts(img, crop_bbox, dpi)
    lines, rects = _load_page_lines_and_rects(
        pdf_bytes, page_number, crop_bbox, black_threshold
    )
    print(f"[detect-scale] {len(rects)} rectangles in crop")

    # Keep only callouts whose centre falls inside a pdfplumber-detected
    # rectangle. Engineering-drawing callouts are boxed; loose text like the
    # `10"-0"` architectural-scale label is not.
    boxed: list[tuple[dict[str, Any], dict[str, float]]] = []
    for c in raw_callouts:
        rect = _enclosing_rect(c["bbox"], rects)
        if rect is None:
            print(f"[detect-scale] drop unboxed: {c['raw_text']!r}")
            continue
        boxed.append((c, rect))

    out_callouts: list[dict[str, Any]] = []
    scales: list[float] = []
    for idx, (c, rect) in enumerate(boxed, start=1):
        geo = _infer_geometry(c["bbox"], c["diameter_in"], lines)
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
        "dpi": dpi,
        "callouts": out_callouts,
        "drawing_scale_pts_per_inch": median(scales) if scales else None,
        "callout_count": len(out_callouts),
    }
