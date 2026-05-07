"""V4 pipeline orchestrator (SOLUTION-DESIGN-V4 §3).

Single entry point for the V4 path. Coexists with V3's runner; the API layer
chooses between them. Caller controls scale (when title-block OCR fails) and
flow direction (when auto-inference is wrong) via the optional overrides.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import math
import re
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from app.cv.connectors import detect_connectors
from app.cv.crosscut import find_segment_boundaries
from app.cv.crossings import resolve_crossings
from app.cv.duct_outline import detect_duct_polygons
from app.cv.dimensions import find_dimension_text
from app.cv.clip_render import (
    mask_outside_polygon, project_polygon_to_clip, render_rectangle_clip,
)
from app.cv.preprocess_v4 import (
    mask_outside_area, rasterize_pdf, read_page_rotation, remove_grey_fill,
)
from app.cv.rect_filters import (
    TaggedRect,
    DEFAULT_EPSILON_FRAC,
    DEFAULT_MAX_CORNER_COS,
    DEFAULT_MAX_INK_PCT,
    DEFAULT_MIN_ASPECT_RATIO,
    DEFAULT_MIN_CIRCULARITY,
    DEFAULT_MIN_DIVIDER_INK_PCT,
    DEFAULT_MIN_DUCT_ASPECT,
    DEFAULT_MIN_INK_PCT,
    DEFAULT_MIN_WHITE_PCT,
    filter_by_aspect_ratio,
    filter_by_content,
    filter_by_interior_emptiness,
    filter_has_horizontal_divider,
    filter_is_circle,
    filter_is_rectangle,
    filter_max_ink,
    filter_min_ink,
    filter_oversized,
    filter_squarish,
)
from app.cv.rectangles import find_all_rectangles
from app.cv.terminals import detect_air_terminals
from app.cv.types import Boundary, Connector, DuctPolygon, Label, Terminal
from app.detect.geometry import cross_check_scale, diameter_from_pixel_width
from app.detect.network import DuctNetwork, build_network
from app.detect.types import NetworkEdge
from app.ocr.base import OCRExtractor
from app.ocr.duct_grammar import standardize_duct_label
from app.ocr.hybrid import read_text_smart
from app.ocr.label_v4 import read_duct_labels
from app.ocr.ollama_vision import read_text_from_crop
from app.ocr.rapid import RapidOCRExtractor
from app.ocr.scale_block import read_title_block_scale
from app.ocr.tesseract import TesseractExtractor
from app.pipeline.flow_trace import trace_cfm
from app.pipeline.pressure import compute_pressure
from app.schemas import (
    CfmRange,
    DebugDimension,
    DebugOcrMatch,
    DebugPolygon,
    DebugRectangle,
    DropReason,
    OperationalVars,
    PageDims,
    PressureResult,
    ScaleInfo,
    TerminalRef,
    V4Debug,
    V4Result,
    V4Segment,
    V4Terminal,
)

ProgressFn = Callable[[str, dict], None]

logger = logging.getLogger(__name__)

# RapidOCR mistakes the round-duct mark `ø` for `0`; strict label_v4 regex
# rejects those, so we re-OCR with this loose pattern and require pixel-width
# context confirmation before substituting back to `ø`.
_ROUND_FALLBACK_RE = re.compile(r"^\s*(\d{1,2})\s*\"?\s*0\s*$")
_FALLBACK_DIAMETER_TOL_IN = 2.0
_FALLBACK_ASPECT_MAX = 2.5
# Real ducts on these drawings sit in this inch-diameter band. Anything outside
# is the page frame, title block, or a plan-note rectangle leaking through CV.
_PLAUSIBLE_DIAMETER_IN = (2.0, 48.0)
_SCALE_DEVIATION_WARN_PCT = 3.0
# Cap raster size — the connector contour pass goes O(N²) on full ARCH-D.
_MAX_RASTER_PIXELS = 9_000_000
_MIN_DPI = 100
_MAX_DPI = 300


def _make_emit(progress: ProgressFn | None, started: float) -> Callable[..., None]:
    """Build the per-run progress emitter; no-op when progress is None."""
    def emit(stage: str, message: str, **extra: object) -> None:
        if progress is None:
            return
        payload: dict[str, object] = {
            "stage": stage, "message": message,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        payload.update(extra)
        progress(stage, payload)
    return emit


def run_v4(
    pdf_path: str | Path,
    op_vars: OperationalVars | None = None,
    scale_override: ScaleInfo | None = None,
    source_node_id: str | None = None,
    progress: ProgressFn | None = None,
    debug: bool = False,
    min_aspect_ratio: float | None = None,
    min_white_pct: float | None = None,
    enable_oversized: bool = True,
    enable_aspect_ratio: bool = False,
    enable_interior: bool = False,
    enable_content: bool = False,
    enable_rectangle: bool = True,
    epsilon_frac: float | None = None,
    max_corner_cos: float | None = None,
    crop_area: tuple[int, int, int, int] | None = None,
    stop_after: str | None = None,
    dpi_override: int | None = None,
    max_vlm_crops: int | None = None,
    enable_vlm_ocr: bool = False,
    ink_threshold: int | None = None,
    rect_dpi: int = 100,
    ocr_dpi: int = 600,
    enable_min_ink: bool = True,
    min_ink_pct: float | None = None,
    enable_max_ink: bool = True,
    max_ink_pct: float | None = None,
    enable_squarish: bool = True,
    min_duct_aspect: float | None = None,
    enable_circle: bool = False,
    min_circularity: float | None = None,
    enable_divider: bool = False,
    min_divider_ink_pct: float | None = None,
    enable_three_digit: bool = False,
) -> V4Result:
    """Run preprocess → detect → OCR → graph → CFM → pressure → V4Result."""
    op_vars = op_vars or OperationalVars()
    warnings: list[str] = []
    emit = _make_emit(progress, time.monotonic())

    # Rectangle stage uses ``rect_dpi``; OCR stage rasterises again at
    # ``ocr_dpi`` and scales the locked bboxes by ``ocr_dpi / rect_dpi`` so
    # text reads happen on a high-resolution crop while shape detection stays
    # cheap on the low-resolution one.
    dpi = int(dpi_override) if dpi_override else int(rect_dpi)
    emit("rasterize", f"rasterizing PDF @ {dpi} DPI for rectangles", dpi=dpi)
    raster = rasterize_pdf(pdf_path, dpi=dpi)
    rotation = read_page_rotation(pdf_path)
    page_dims = PageDims(
        width_px=raster.size[0], height_px=raster.size[1], dpi=dpi, rotation=rotation,
    )

    emit("grey_removal", "removing grey architectural fill")
    cleaned = remove_grey_fill(raster, threshold=ink_threshold)
    if crop_area is not None:
        emit("mask_outside", "masking outside operator-defined drawing area")
        cleaned = mask_outside_area(cleaned, crop_area)

    # Workflow short-circuit: the UI calls run_v4 once with stop_after="grey_removal"
    # to render the cleaned page so the operator can mark the drawing area, then
    # calls again with crop_area populated and stop_after=None for full detection.
    if stop_after == "grey_removal":
        cleaned_url = _encode_png_data_url(cleaned)
        scale_stub = ScaleInfo(
            paper_inches_per_foot=0.25, source="manual", confidence=0.0,
        )
        emit("done", "stopped after grey_removal")
        return V4Result(
            segments=[], terminals=[], scale=scale_stub, op_vars=op_vars,
            page_dims=page_dims, warnings=warnings, debug=None,
            stage_image_data_url=cleaned_url, stage_stopped_after="grey_removal",
        )

    aspect = DEFAULT_MIN_ASPECT_RATIO if min_aspect_ratio is None else min_aspect_ratio
    white_pct = DEFAULT_MIN_WHITE_PCT if min_white_pct is None else min_white_pct
    eps_frac = DEFAULT_EPSILON_FRAC if epsilon_frac is None else epsilon_frac
    corner_cos = DEFAULT_MAX_CORNER_COS if max_corner_cos is None else max_corner_cos
    emit("find_rectangles", "finding all rectangular contours")
    rectangles = find_all_rectangles(cleaned)
    if enable_oversized:
        emit("filter_oversized", "dropping rectangles > 20% of page",
             count=len(rectangles))
        tagged = filter_oversized(rectangles, cleaned.width, cleaned.height)
    else:
        tagged = filter_oversized(rectangles, cleaned.width, cleaned.height)
        # Re-mark previously-oversized rects as kept so the filter is bypassed.
        tagged = [
            t if t.kept else type(t)(
                corners=t.corners, bbox=t.bbox, kept=True, drop_reason=None,
            )
            for t in tagged
        ]
    # V4.5 dual-branch: same post-oversized contour list feeds two filter
    # chains in parallel — rectangles (ducts) and circles (air terminals) —
    # then the survivors of either branch are merged. Each branch starts from
    # ``post_oversized`` independently; filters create new TaggedRect
    # instances so the lists don't share state.
    post_oversized = tagged

    # ---- Duct branch: rectangle filters ----
    duct_tagged = post_oversized
    if enable_rectangle:
        emit("filter_rectangle",
             f"keeping only rectangles (cos≤{corner_cos}, eps={eps_frac})",
             count=sum(1 for t in duct_tagged if t.kept))
        duct_tagged = filter_is_rectangle(
            duct_tagged, epsilon_frac=eps_frac, max_corner_cos=corner_cos,
        )
    if enable_squarish:
        ratio_min = (
            DEFAULT_MIN_DUCT_ASPECT if min_duct_aspect is None else min_duct_aspect
        )
        emit("filter_squarish", f"dropping squarish (aspect < {ratio_min})",
             count=sum(1 for t in duct_tagged if t.kept))
        duct_tagged = filter_squarish(duct_tagged, min_aspect=ratio_min)
    if enable_min_ink:
        ink_min = DEFAULT_MIN_INK_PCT if min_ink_pct is None else min_ink_pct
        emit("filter_min_ink", f"dropping empty interiors (ink < {ink_min:.1%})",
             count=sum(1 for t in duct_tagged if t.kept))
        duct_tagged = filter_min_ink(duct_tagged, cleaned, min_ink_pct=ink_min)
    if enable_max_ink:
        ink_max = DEFAULT_MAX_INK_PCT if max_ink_pct is None else max_ink_pct
        emit("filter_max_ink", f"dropping mostly-ink interiors (ink > {ink_max:.0%})",
             count=sum(1 for t in duct_tagged if t.kept))
        duct_tagged = filter_max_ink(duct_tagged, cleaned, max_ink_pct=ink_max)
    if enable_aspect_ratio:
        emit("filter_aspect_ratio", f"requiring aspect ratio ≥ {aspect}",
             count=sum(1 for t in duct_tagged if t.kept))
        duct_tagged = filter_by_aspect_ratio(duct_tagged, min_ratio=aspect)
    if enable_interior:
        emit("filter_interior", f"requiring interior ≥ {white_pct:.0%} white",
             count=sum(1 for t in duct_tagged if t.kept))
        duct_tagged = filter_by_interior_emptiness(
            duct_tagged, cleaned, min_white_pct=white_pct,
        )
    if enable_content:
        emit("filter_content", "OCR full page; dropping rects with non-duct text",
             count=sum(1 for t in duct_tagged if t.kept))
        duct_tagged = filter_by_content(duct_tagged, cleaned, RapidOCRExtractor())

    # ---- Terminal branch: circle filters ----
    term_tagged = post_oversized
    if enable_circle:
        circ_min = (
            DEFAULT_MIN_CIRCULARITY if min_circularity is None else min_circularity
        )
        emit("filter_circle",
             f"keeping only circles (circularity ≥ {circ_min:.2f})",
             count=sum(1 for t in term_tagged if t.kept))
        term_tagged = filter_is_circle(term_tagged, min_circularity=circ_min)
    if enable_divider:
        div_min = (
            DEFAULT_MIN_DIVIDER_INK_PCT
            if min_divider_ink_pct is None else min_divider_ink_pct
        )
        emit("filter_divider",
             f"keeping bisected circles (centre row ink ≥ {div_min:.0%})",
             count=sum(1 for t in term_tagged if t.kept))
        term_tagged = filter_has_horizontal_divider(
            term_tagged, cleaned, min_ink_pct=div_min,
        )
    # ---- Terminal branch: 3-digit OCR (slow; opt-in, runs on circles only) ----
    three_digit_text_by_id: dict[int, str] = {}
    if enable_three_digit:
        candidates = sum(1 for t in term_tagged if t.kept)
        emit("filter_three_digit",
             f"OCR ladder (Tesseract→VLM×3) on {candidates} bbox(es)",
             count=candidates, total=candidates, done=0, kept=0)

        def _three_digit_progress(done: int, total: int, kept: int) -> None:
            emit("filter_three_digit_progress",
                 f"{done}/{total} bbox(es) processed ({kept} kept)",
                 done=done, total=total, kept=kept)

        term_tagged, three_digit_text_by_id = _apply_three_digit_filter(
            term_tagged, pdf_path, rect_dpi=dpi, ink_threshold=ink_threshold,
            on_progress=_three_digit_progress,
        )

    # ---- Merge branches: a contour kept by either side survives ----
    tagged = []
    for orig, dr, tr in zip(post_oversized, duct_tagged, term_tagged):
        if not orig.kept:
            tagged.append(orig)
        elif dr.kept:
            tagged.append(dr)
        elif tr.kept:
            tagged.append(tr)
        else:
            # Surface the duct-branch reason since rectangles fire first;
            # the operator can flip toggles to see which branch dropped what.
            tagged.append(dr)
    debug_rects = [
        DebugRectangle(
            corners=t.corners, kept=t.kept, drop_reason=t.drop_reason,
        )
        for t in tagged
    ]

    # ---- Duct-branch OCR ladder (rect grammar) — opt-in ----
    # When ``enable_vlm_ocr`` is on, the duct-branch survivors run the rect-
    # grammar VLM ladder (10x12, 22"x14", 14"ø) and only matches stay in
    # ``debug_ocr_ducts``. The 3-digit terminal OCR has its own results in
    # ``three_digit_text_by_id``. Both lists are merged for the final overlay.
    pad_px = 12
    duct_kept = [t for t in duct_tagged if t.kept]
    duct_kept.sort(key=lambda t: -(t.bbox[2] * t.bbox[3]))
    if max_vlm_crops is not None and max_vlm_crops > 0:
        duct_kept = duct_kept[:max_vlm_crops]
    if enable_vlm_ocr and duct_kept:
        duct_total = len(duct_kept)
        emit("ocr_per_crop",
             f"rect-grammar VLM ladder over {duct_total} bbox(es)",
             count=duct_total, total=duct_total, done=0, kept=0)

        def _duct_ocr_progress(done: int, total: int, kept: int) -> None:
            emit("ocr_per_crop_progress",
                 f"{done}/{total} duct bbox(es) processed ({kept} kept)",
                 done=done, total=total, kept=kept)

        debug_ocr_ducts = _ocr_per_crop_with_retry(
            cleaned, duct_kept, pdf_path, pad_px,
            rect_dpi=dpi, ink_threshold=ink_threshold,
            on_progress=_duct_ocr_progress,
        )
        debug_ocr_ducts, px_per_inch = _populate_duct_lengths(
            debug_ocr_ducts, duct_kept,
        )
        debug_ocr_ducts = _attribute_cfm_and_pressure(
            debug_ocr_ducts, duct_kept,
            [t for t in term_tagged if t.kept],
            three_digit_text_by_id, op_vars,
            px_per_inch=px_per_inch,
        )
    else:
        debug_ocr_ducts = _ocr_per_crop(
            cleaned, None, duct_kept, pad_px,
            rect_dpi=dpi, ocr_dpi=ocr_dpi, run_ocr=False,
        )

    # Terminal-branch survivors carry their OCR text in ``three_digit_text_by_id``;
    # ``_ocr_per_crop`` packs them into the ``DebugOcrMatch`` shape for the overlay.
    term_kept = [t for t in term_tagged if t.kept]
    term_kept.sort(key=lambda t: -(t.bbox[2] * t.bbox[3]))
    debug_ocr_terms = _ocr_per_crop(
        cleaned, None, term_kept, pad_px,
        rect_dpi=dpi, ocr_dpi=ocr_dpi, run_ocr=False,
        text_by_id=three_digit_text_by_id or None,
    )

    # Merge — keep both, dropping duplicates by bbox identity (in case a contour
    # somehow ended up in both branches' kept lists).
    seen_bboxes: set[tuple[int, int, int, int]] = set()
    debug_ocr: list[DebugOcrMatch] = []
    for match in debug_ocr_ducts + debug_ocr_terms:
        if match.bbox in seen_bboxes:
            continue
        seen_bboxes.add(match.bbox)
        debug_ocr.append(match)
    debug_dimensions: list[DebugDimension] = []

    # Final result: omit ``stage_image_data_url`` so the frontend renders the
    # original PDF via PDF.js (high-DPI underlay) instead of the low-res
    # cleaned binary. Overlays sit on top in raster pixel space; PDF.js scales
    # to match. The cleaned image is only sent during the mark-area gate.
    scale_stub = ScaleInfo(paper_inches_per_foot=0.25, source="manual", confidence=0.0)
    kept_count = sum(1 for r in debug_rects if r.kept)
    result = V4Result(
        segments=[], terminals=[], scale=scale_stub, op_vars=op_vars,
        page_dims=page_dims, warnings=warnings, debug=None,
        stage_stopped_after="ocr_all",
        debug_rectangles=debug_rects,
        debug_dimensions=debug_dimensions,
        debug_ocr=debug_ocr,
    )
    emit("done", "pipeline complete (stopped after ocr_all)",
         segments=0, terminals=0, rectangles_total=len(debug_rects),
         rectangles_kept=kept_count, ocr_tokens=len(debug_ocr))
    return result
    # ── END STEP-DEBUG ────────────────────────────────────────────────────

    # emit("scale", "resolving drawing scale")
    # scale = _resolve_scale(cleaned, scale_override, warnings)
    #
    # emit("detect_ducts", "detecting duct polygons")
    # polygons = detect_duct_polygons(cleaned)
    # emit("detect_boundaries", "detecting segment boundaries", count=len(polygons))
    # boundaries = _collect_boundaries(polygons, cleaned)
    # emit("detect_connectors", "detecting connectors")
    # connectors = detect_connectors(cleaned, polygons)
    # emit("detect_terminals", "detecting air terminals")
    # terminals = detect_air_terminals(cleaned)
    # emit("detect_crossings", "resolving crossings")
    # crossings = resolve_crossings(cleaned, polygons)
    #
    # emit("ocr_labels", "OCR duct labels", count=len(polygons))
    # labels = _ocr_labels_with_fallback(cleaned, polygons, scale, warnings, dpi=dpi)
    # labels = _synthesize_missing_labels(polygons, labels, scale, warnings, dpi=dpi)
    # # A polygon without a dimension (OCR, fallback, or plausibility-gated
    # # synthesis) is treated as not-a-duct and dropped silently — segments
    # # without dimensions can't carry length/CFM/pressure, so they're not useful.
    # labelled_ids = {lbl.polygon_id for lbl in labels}
    # duct_polygons = [
    #     p for p in polygons if p.shape_hint != "unknown" and p.id in labelled_ids
    # ]
    # debug_payload = (
    #     _build_debug_payload(polygons, labelled_ids, scale, dpi) if debug else None
    # )
    # _check_scale_against_labels(duct_polygons, labels, scale, warnings, dpi=dpi)
    #
    # emit("build_network", "building duct network", count=len(duct_polygons))
    # network = build_network(
    #     segments=duct_polygons, connectors=_rename_connectors(connectors),
    #     terminals=terminals, crossings=crossings, boundaries=boundaries,
    #     scale=scale, dpi=dpi,
    # )
    # _attach_dimensions(network, labels)
    # warnings.extend(network.warnings)
    #
    # emit("flow_trace", "tracing CFM through network")
    # cfm_map = trace_cfm(network, source_node_id)
    # emit("pressure", "computing pressure")
    # pressure_map = compute_pressure(network, cfm_map, op_vars, source_node_id)
    #
    # result = V4Result(
    #     segments=_build_v4_segments(network, cfm_map, pressure_map, labels, terminals),
    #     terminals=_build_v4_terminals(terminals),
    #     scale=scale, op_vars=op_vars, page_dims=page_dims, warnings=warnings,
    #     debug=debug_payload,
    # )
    # emit("done", "pipeline complete",
    #      segments=len(result.segments), terminals=len(result.terminals))
    # return result


_DISPLAY_MAX_WIDTH = 4096


# DPI ladder for the masked-clip OCR retries. Each attempt re-rasterizes
# only the rectangle's bbox (cheap), masks outside the rotated polygon, and
# asks the VLM. We accept the first attempt whose result standardizes to a
# duct grammar; otherwise the rectangle is dropped.
_OCR_DPI_LADDER = (600, 900, 1200)
# Process-local OCR cache. Keyed by SHA-1 of the binarised image's raw pixel
# bytes (plus mode/size to disambiguate). Same masked crop → same OCR result,
# so repeat runs (slider tweaks that don't affect the masked image) skip the
# expensive cloud VLM round-trip. Cleared on uvicorn reload — that's fine,
# it's a soft cache. Threadsafe under CPython's GIL: dict get/set are atomic.
_OCR_TEXT_CACHE: dict[str, str] = {}


def _image_cache_key(image: Image.Image) -> str:
    """SHA-1 hash of an image's raw pixel bytes — stable for identical pixels."""
    return hashlib.sha1(
        f"{image.mode}|{image.size}|".encode() + image.tobytes()
    ).hexdigest()


def _cached_vlm_read(image: Image.Image) -> str:
    """``read_text_from_crop`` with module-level memoisation."""
    key = "vlm:" + _image_cache_key(image)
    cached = _OCR_TEXT_CACHE.get(key)
    if cached is not None:
        return cached
    text = read_text_from_crop(image) or ""
    _OCR_TEXT_CACHE[key] = text
    return text


def _cached_tesseract_read(
    extractor: TesseractExtractor, image: Image.Image,
) -> str:
    """Tesseract pass with module-level memoisation."""
    key = "tess:" + _image_cache_key(image)
    cached = _OCR_TEXT_CACHE.get(key)
    if cached is not None:
        return cached
    matches = extractor.extract_text(image)
    text = " ".join(m.text for m in matches)
    _OCR_TEXT_CACHE[key] = text
    return text


# Duct-label first-dimension parser. Extracts the leading inch value out of
# the canonical forms emitted by ``standardize_duct_label`` (e.g. '22"x14"',
# '14"ø'). The first number is treated as the plan-visible cross-section
# (the duct's W); the rectangle's pixel-short side maps to it.
_DUCT_FIRST_DIM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[\"']?\s*(?:[xX×]|[øØ⌀])"
)


def _parse_first_dim_inches(text: str) -> float | None:
    """Return the first numeric dimension (inches) from a duct label."""
    if not text:
        return None
    m = _DUCT_FIRST_DIM_RE.search(text)
    if m is None:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _rotated_short_long(
    corners: list[tuple[int, int]],
) -> tuple[float, float]:
    """Return (short_side_px, long_side_px) of a 4-corner rotated bbox."""
    if len(corners) < 4:
        return 0.0, 0.0
    a, b, c = corners[0], corners[1], corners[2]
    side1 = math.hypot(b[0] - a[0], b[1] - a[1])
    side2 = math.hypot(c[0] - b[0], c[1] - b[1])
    return min(side1, side2), max(side1, side2)


def _populate_duct_lengths(
    debug_ocr_ducts: list[DebugOcrMatch],
    duct_kept: list[TaggedRect],
) -> tuple[list[DebugOcrMatch], float | None]:
    """Fill ``length_ft`` on each duct OCR match using a median-derived scale.

    For each labeled duct: per-duct scale = ``short_px / first_dim_inches``.
    The global scale is the median (robust to single-rect OCR errors). Each
    duct's length is ``long_px / global_scale / 12`` feet. Returns a new
    list with ``length_ft`` populated where derivable plus the px-per-inch
    scale itself (``None`` when no labels parsed).
    """
    by_bbox: dict[tuple[int, int, int, int], list[tuple[int, int]]] = {
        tuple(r.bbox): list(r.corners) for r in duct_kept
    }
    parsed: list[tuple[int, float]] = []  # (match_index, long_px)
    scales: list[float] = []
    for idx, match in enumerate(debug_ocr_ducts):
        cross_in = _parse_first_dim_inches(match.text)
        if cross_in is None:
            continue
        corners = by_bbox.get(tuple(match.bbox))
        if corners is None:
            continue
        short_px, long_px = _rotated_short_long(corners)
        if short_px <= 0 or long_px <= 0:
            continue
        scales.append(short_px / cross_in)
        parsed.append((idx, long_px))
    if not scales:
        return debug_ocr_ducts, None
    scales.sort()
    global_scale = scales[len(scales) // 2]
    if global_scale <= 0:
        return debug_ocr_ducts, None
    logger.info(
        "v4: duct length scale = %.3f px/in (median of %d duct(s))",
        global_scale, len(scales),
    )
    out = list(debug_ocr_ducts)
    for idx, long_px in parsed:
        m = out[idx]
        length_ft = (long_px / global_scale) / 12.0
        out[idx] = DebugOcrMatch(
            text=m.text, bbox=m.bbox, confidence=m.confidence,
            crop_data_url=m.crop_data_url, source=m.source,
            oriented_corners=m.oriented_corners,
            length_ft=round(length_ft, 2),
        )
    return out, global_scale


# Adjacency threshold for "directly connected" duct↔terminal. A few pixels
# of slack lets anti-aliased outlines "touch"; anything more easily picks up
# unrelated neighbouring contours. Stays in pixels because "touching" is a
# raster-level test, not a physical-distance test.
_ADJACENCY_PX = 6
# Real-world neighborhood radius for the CFM proxy: a duct without a directly-
# adjacent terminal borrows CFM from terminals within this many feet. Converted
# to pixels at run time using the drawing's pixels-per-inch scale derived from
# duct OCR, so the radius stays correct across DPIs and drawing scales.
_NEIGHBORHOOD_FT = 4.0
# Floor velocity used when a duct sits in dead space with no nearby terminals.
_FALLBACK_VELOCITY_FPM = 1500.0
# Velocity assumed when computing a "what would this CFM look like in this
# duct's cross-section" proxy. Used as a sanity bound — we cap the proxied
# velocity at this number to avoid pressure-class explosions from a 700-CFM
# terminal flowing through a tiny imagined cross-section.
_NEIGHBORHOOD_VELOCITY_CAP_FPM = 4000.0


_DUCT_RECT_DIMS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[\"']?\s*[xX×]\s*(\d+(?:\.\d+)?)"
)
_DUCT_ROUND_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[\"']?\s*[øØ⌀]")


def _parse_duct_dims(text: str) -> tuple[float, float | None] | None:
    """Return (W, H) inches for rectangular labels, (D, None) for round."""
    if not text:
        return None
    m = _DUCT_RECT_DIMS_RE.search(text)
    if m:
        try:
            w, h = float(m.group(1)), float(m.group(2))
        except ValueError:
            return None
        return (w, h) if w > 0 and h > 0 else None
    m = _DUCT_ROUND_DIM_RE.search(text)
    if m:
        try:
            d = float(m.group(1))
        except ValueError:
            return None
        return (d, None) if d > 0 else None
    return None


def _cross_section_area_sqft(dims: tuple[float, float | None]) -> float:
    """Area in ft² from inch dimensions. Round → π·(D/2)²."""
    w, h = dims
    if h is None:
        return math.pi * (w / 2.0) ** 2 / 144.0
    return (w * h) / 144.0


def _hydraulic_diameter_ft(dims: tuple[float, float | None]) -> float:
    """Dh in feet. Round → D; rect → 2WH/(W+H). Convert from inches."""
    w, h = dims
    if h is None:
        return w / 12.0
    if w + h <= 0:
        return 0.0
    return (2.0 * w * h) / (w + h) / 12.0


def _bbox_edge_distance(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int],
) -> float:
    """Closest L2 distance between two axis-aligned bboxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dx = max(0, max(bx - (ax + aw), ax - (bx + bw)))
    dy = max(0, max(by - (ay + ah), ay - (by + bh)))
    return math.hypot(dx, dy)


def _neighborhood_cfm_proxy(
    duct_bbox: tuple[int, int, int, int],
    terms_with_cfm: list[tuple[TaggedRect, float]],
    radius_px: float,
) -> float | None:
    """Inverse-distance-weighted CFM proxy from terminals near ``duct_bbox``.

    Terminals further than ``radius_px`` are ignored. Closer terminals weigh
    more; high-CFM terminals carry their value through the weighting. Returns
    ``None`` when no terminal falls inside the radius.
    """
    weights: list[tuple[float, float]] = []  # (weight, cfm)
    for term, cfm in terms_with_cfm:
        d = _bbox_edge_distance(duct_bbox, term.bbox)
        if d > radius_px:
            continue
        # +1 prevents a divide-by-zero for terminals touching the duct.
        weights.append((1.0 / (d + 1.0), cfm))
    if not weights:
        return None
    total_weight = sum(w for w, _ in weights)
    if total_weight <= 0:
        return None
    weighted_cfm = sum(w * c for w, c in weights) / total_weight
    return weighted_cfm


def _classify_smacna(
    pressure_drop_in_wc: float, op_vars: OperationalVars,
) -> "str":
    """Map a pressure value to a SMACNA class via op_vars thresholds."""
    th = op_vars.smacna_thresholds_in_wc
    if pressure_drop_in_wc <= th.low_max_in_wc:
        return "Low"
    if pressure_drop_in_wc <= th.medium_max_in_wc:
        return "Medium"
    return "High"


def _attribute_cfm_and_pressure(
    debug_ocr_ducts: list[DebugOcrMatch],
    duct_kept: list[TaggedRect],
    term_kept: list[TaggedRect],
    three_digit_text_by_id: dict[int, str],
    op_vars: OperationalVars,
    *,
    px_per_inch: float | None = None,
) -> list[DebugOcrMatch]:
    """MVP airflow attribution — direct bbox-adjacency only.

    For every duct OCR match: parse the cross-section dim; find any kept
    terminal whose bbox sits within ``_ADJACENCY_PX`` of the duct's bbox.
    With exactly one such terminal, attribute its CFM, derive velocity from
    cross-section area, friction drop from length × Darcy, and SMACNA class
    from ``op_vars`` thresholds. With zero or multiple — leave fields None.
    """
    # Parse terminal CFM values once.
    terms_with_cfm: list[tuple[TaggedRect, float]] = []
    for term in term_kept:
        text = three_digit_text_by_id.get(id(term), "")
        m = _THREE_DIGIT_RE.search(text or "")
        if m is None:
            continue
        try:
            cfm = float(m.group(0))
        except ValueError:
            continue
        terms_with_cfm.append((term, cfm))
    if not terms_with_cfm:
        return debug_ocr_ducts

    duct_by_bbox: dict[tuple[int, int, int, int], TaggedRect] = {
        tuple(d.bbox): d for d in duct_kept
    }
    # Convert the real-world neighborhood radius (4 ft) to pixels using the
    # OCR-derived scale; fall back to a generic 80px when the scale wasn't
    # available (no labels parsed) so the heuristic still does something.
    if px_per_inch is not None and px_per_inch > 0:
        neighborhood_px = px_per_inch * 12.0 * _NEIGHBORHOOD_FT
        logger.info(
            "v4: CFM-proxy radius = %.0fpx (%.1f ft @ %.2f px/in)",
            neighborhood_px, _NEIGHBORHOOD_FT, px_per_inch,
        )
    else:
        neighborhood_px = 80.0
        logger.info(
            "v4: CFM-proxy radius = %.0fpx (drawing scale unavailable)",
            neighborhood_px,
        )
    out = list(debug_ocr_ducts)
    attributed = 0
    estimated = 0
    for idx, match in enumerate(debug_ocr_ducts):
        duct = duct_by_bbox.get(tuple(match.bbox))
        if duct is None:
            continue
        dims = _parse_duct_dims(match.text)
        if dims is None:
            continue
        area_sqft = _cross_section_area_sqft(dims)
        dh_ft = _hydraulic_diameter_ft(dims)
        length_ft = match.length_ft or 0.0
        if area_sqft <= 0 or dh_ft <= 0 or length_ft <= 0:
            continue
        nearby = [
            (term, cfm) for term, cfm in terms_with_cfm
            if _bbox_edge_distance(duct.bbox, term.bbox) <= _ADJACENCY_PX
        ]
        if len(nearby) == 1:
            adj_term, cfm_val = nearby[0]
            velocity_fpm = cfm_val / area_sqft
            adj_bbox: tuple[int, int, int, int] | None = tuple(adj_term.bbox)
            cfm: float | None = cfm_val
            is_estimated = False
            attributed += 1
        else:
            # Area-aware fallback: borrow CFM from terminals within the
            # scale-derived radius via inverse-distance weighting. A nearby
            # 700-CFM terminal pushes this duct's pressure class up; a duct
            # that sits in dead space lands on the floor velocity.
            proxied = _neighborhood_cfm_proxy(
                duct.bbox, terms_with_cfm, neighborhood_px,
            )
            if proxied is not None:
                velocity_fpm = min(
                    _NEIGHBORHOOD_VELOCITY_CAP_FPM,
                    proxied / area_sqft,
                )
            else:
                velocity_fpm = _FALLBACK_VELOCITY_FPM
            adj_bbox = None
            cfm = round(proxied, 0) if proxied is not None else None
            is_estimated = True
            estimated += 1
        velocity_pressure = (velocity_fpm / 4005.0) ** 2
        pressure_drop = (
            op_vars.friction_factor * (length_ft / dh_ft) * velocity_pressure
        )
        cls = _classify_smacna(pressure_drop, op_vars)
        out[idx] = DebugOcrMatch(
            text=match.text, bbox=match.bbox, confidence=match.confidence,
            crop_data_url=match.crop_data_url, source=match.source,
            oriented_corners=match.oriented_corners, length_ft=match.length_ft,
            cfm=cfm,
            velocity_fpm=round(velocity_fpm, 0),
            pressure_drop_in_wc=round(pressure_drop, 3),
            smacna_class=cls,
            adjacent_terminal_bbox=adj_bbox,
            pressure_estimated=is_estimated,
        )
    logger.info(
        "v4: pressure attribution — measured %d, estimated %d (of %d duct OCR matches)",
        attributed, estimated, len(debug_ocr_ducts),
    )
    return out


# CFM-on-air-terminal grammar (A5): the bottom half of a bisected circle
# carries a 3-digit airflow figure. Match a standalone run of exactly three
# digits — leading/trailing punctuation tolerated. Tighter than ``\d{3}``
# alone because we want the OCR to read a discrete token, not part of a year
# or grid coordinate that happens to contain three contiguous digits.
_THREE_DIGIT_RE = re.compile(r"(?<!\d)\d{3}(?!\d)")
_THREE_DIGIT_DPI_LADDER = (600, 900, 1200)
# Exponential backoff between retries. attempt 1 → 0s, attempt 2 → 0.5s,
# attempt 3 → 1.0s. Spreads VLM calls so we don't spike the cloud endpoint
# when many rectangles fail simultaneously across the worker pool.
_OCR_BACKOFF_BASE_SEC = 0.5


def _has_three_digit(text: str) -> bool:
    """True when ``text`` contains a standalone 3-digit token (A5 CFM)."""
    return bool(text and _THREE_DIGIT_RE.search(text))


def _apply_three_digit_filter(
    tagged: list[TaggedRect],
    pdf_path: str | Path,
    *,
    rect_dpi: int,
    ink_threshold: int | None,
    on_progress: Callable[[int, int, int], None] | None = None,
    max_workers: int = 8,
) -> tuple[list[TaggedRect], dict[int, str]]:
    """Per-bbox OCR ladder. Drop kept rects whose OCR yields no 3-digit token.

    For every kept rect:
      1. Render the bbox as a fresh PDF clip at 600 DPI; binarise; mask
         outside the contour polygon so the VLM/Tesseract only see the
         circle's interior (no neighbouring labels).
      2. Try Tesseract on the masked crop — fast and free.
      3. If no 3-digit token, ladder VLM at 600 → 900 → 1200 DPI.
      4. First read with a 3-digit token wins; ladder exhausted = drop.

    Returns the updated rect list and a ``id(rect) -> text`` dict so the
    debug overlay can show what each survivor actually read.
    """
    from concurrent.futures import ThreadPoolExecutor

    tesseract = TesseractExtractor()

    def render(rect: TaggedRect, dpi: int) -> Image.Image:
        clip_img, origin_pt = render_rectangle_clip(
            pdf_path, rect.bbox, rect_dpi=rect_dpi, target_dpi=dpi,
        )
        clip_bin = remove_grey_fill(clip_img, threshold=ink_threshold)
        polygon_clip_px = project_polygon_to_clip(
            rect.corners, rect_dpi=rect_dpi, target_dpi=dpi,
            clip_origin_pt=origin_pt,
        )
        return mask_outside_polygon(clip_bin, polygon_clip_px)

    def one(rect: TaggedRect) -> tuple[TaggedRect, str]:
        if not rect.kept:
            return rect, ""
        started = time.monotonic()
        last_text = ""
        try:
            masked = render(rect, 600)
            tess_text = _cached_tesseract_read(tesseract, masked)
            if _has_three_digit(tess_text):
                logger.info(
                    "v4: 3-digit %s → kept %r via tesseract@600 in %.1fs",
                    rect.bbox, tess_text.strip(), time.monotonic() - started,
                )
                return rect, tess_text.strip()
            last_text = tess_text.strip()
        except Exception:
            logger.debug("v4: tesseract pass failed on %s", rect.bbox, exc_info=True)
        for attempt_idx, dpi in enumerate(_THREE_DIGIT_DPI_LADDER):
            if attempt_idx > 0:
                time.sleep(_OCR_BACKOFF_BASE_SEC * (2 ** (attempt_idx - 1)))
            try:
                masked = render(rect, dpi)
                vlm_text = _cached_vlm_read(masked)
            except Exception:
                continue
            if vlm_text in {"EMPTY", "ERROR"}:
                continue
            if _has_three_digit(vlm_text):
                logger.info(
                    "v4: 3-digit %s → kept %r via vlm@%d in %.1fs",
                    rect.bbox, vlm_text.strip(), dpi,
                    time.monotonic() - started,
                )
                return rect, vlm_text.strip()
            last_text = last_text or vlm_text.strip()
        dropped = TaggedRect(
            corners=rect.corners, bbox=rect.bbox,
            kept=False, drop_reason="no_three_digit",
        )
        logger.info(
            "v4: 3-digit %s → DROP after %.1fs (last raw=%r)",
            rect.bbox, time.monotonic() - started, last_text,
        )
        return dropped, last_text

    from concurrent.futures import as_completed

    candidate_indices = [i for i, r in enumerate(tagged) if r.kept]
    total = len(candidate_indices)
    started = time.monotonic()
    logger.info(
        "v4: 3-digit filter starting on %d candidate(s) with %d worker(s)",
        total, max_workers,
    )
    out: list[TaggedRect] = list(tagged)
    text_by_id: dict[int, str] = {}
    done = 0
    done_kept = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_to_idx = {pool.submit(one, tagged[i]): i for i in candidate_indices}
        for fut in as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            new_rect, text = fut.result()
            out[idx] = new_rect
            if text:
                text_by_id[id(new_rect)] = text
            done += 1
            if new_rect.kept:
                done_kept += 1
            if on_progress is not None:
                on_progress(done, total, done_kept)
    logger.info(
        "v4: 3-digit filter done — %d/%d kept in %.1fs",
        done_kept, total, time.monotonic() - started,
    )
    return out, text_by_id


def _ocr_per_crop_with_retry(
    cleaned_lo: Image.Image,
    kept: list,
    pdf_path: str | Path,
    pad_px: int,
    *,
    rect_dpi: int,
    ink_threshold: int | None,
    on_progress: Callable[[int, int, int], None] | None = None,
    max_workers: int = 8,
) -> list[DebugOcrMatch]:
    """OCR each rectangle at increasing DPI until a duct-grammar match.

    For every rectangle:
      1. Render a fresh PDF clip of just that bbox at the current DPI.
      2. Re-binarise with the same ink threshold.
      3. Mask everything outside the rotated polygon → VLM sees only the
         rectangle's interior (no neighbouring labels).
      4. Send to the VLM, standardize the response with ``duct_grammar``.
      5. Match → keep; no match → next DPI; ladder exhausted → drop entry.
    """
    from concurrent.futures import ThreadPoolExecutor

    def lo_crop(rect) -> Image.Image:
        x, y, w, h = rect.bbox
        x0 = max(0, x - pad_px)
        y0 = max(0, y - pad_px)
        x1 = min(cleaned_lo.width, x + w + pad_px)
        y1 = min(cleaned_lo.height, y + h + pad_px)
        return cleaned_lo.crop((x0, y0, x1, y1))

    def attempt(rect, dpi: int) -> tuple[Image.Image, str] | None:
        clip_img, origin_pt = render_rectangle_clip(
            pdf_path, rect.bbox, rect_dpi=rect_dpi, target_dpi=dpi,
        )
        clip_bin = remove_grey_fill(clip_img, threshold=ink_threshold)
        polygon_clip_px = project_polygon_to_clip(
            rect.corners, rect_dpi=rect_dpi, target_dpi=dpi,
            clip_origin_pt=origin_pt,
        )
        masked = mask_outside_polygon(clip_bin, polygon_clip_px)
        text = _cached_vlm_read(masked)
        return masked, text

    def one(rect) -> DebugOcrMatch | None:
        display_crop = lo_crop(rect)
        last_image = display_crop
        last_raw = ""
        for attempt_idx, dpi in enumerate(_OCR_DPI_LADDER):
            if attempt_idx > 0:
                time.sleep(_OCR_BACKOFF_BASE_SEC * (2 ** (attempt_idx - 1)))
            try:
                ocr_img, raw_text = attempt(rect, dpi)
            except Exception:
                continue
            last_image = ocr_img
            last_raw = raw_text
            if not raw_text or raw_text in {"EMPTY", "ERROR"}:
                continue
            standardized = standardize_duct_label(raw_text)
            if standardized is not None:
                canonical, _kind = standardized
                return DebugOcrMatch(
                    text=canonical, bbox=rect.bbox, confidence=1.0,
                    crop_data_url=_encode_png_data_url(last_image),
                    source="vlm",
                    oriented_corners=_oriented_corners(rect.corners),
                )
        # All retries exhausted — drop this rectangle entirely.
        logger.info(
            "v4: dropping rect %s after %d VLM attempts; last raw=%r",
            rect.bbox, len(_OCR_DPI_LADDER), last_raw,
        )
        return None

    from concurrent.futures import as_completed

    total = len(kept)
    out: list[DebugOcrMatch] = []
    done = 0
    done_kept = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(one, rect) for rect in kept]
        for fut in as_completed(futures):
            result = fut.result()
            done += 1
            if result is not None:
                out.append(result)
                done_kept += 1
            if on_progress is not None:
                on_progress(done, total, done_kept)
    return out


def _ocr_per_crop(
    cleaned_lo: Image.Image,
    cleaned_hi: Image.Image | None,
    kept: list,
    pad_px: int,
    *,
    rect_dpi: int,
    ocr_dpi: int,
    run_ocr: bool,
    max_workers: int = 8,
    text_by_id: dict[int, str] | None = None,
) -> list[DebugOcrMatch]:
    """Crop every kept rectangle; optionally OCR each at the high-DPI raster.

    ``cleaned_lo`` is the low-DPI binary image used to draw the rectangles
    (and the crop the operator inspects). ``cleaned_hi`` (only built when
    ``run_ocr`` is True) is the high-DPI binary used for the actual OCR call
    so digits and the ``ø`` glyph are large enough to read accurately.

    For each rectangle:
      • OCR-disabled path: encode the low-DPI crop, return empty text.
      • OCR-enabled path: scale bbox by ``ocr_dpi/rect_dpi`` to address the
        same region on ``cleaned_hi``, run the Tesseract→VLM ladder
        (``read_text_smart``), and report which engine produced the text.
    """
    from concurrent.futures import ThreadPoolExecutor

    scale = float(ocr_dpi) / float(rect_dpi) if run_ocr else 1.0

    def lo_crop(rect) -> tuple[Image.Image, tuple[int, int, int, int]]:
        x, y, w, h = rect.bbox
        x0 = max(0, x - pad_px)
        y0 = max(0, y - pad_px)
        x1 = min(cleaned_lo.width, x + w + pad_px)
        y1 = min(cleaned_lo.height, y + h + pad_px)
        return cleaned_lo.crop((x0, y0, x1, y1)), (x0, y0, x1, y1)

    def hi_crop(low_xyxy: tuple[int, int, int, int]) -> Image.Image | None:
        if cleaned_hi is None:
            return None
        x0, y0, x1, y1 = low_xyxy
        return cleaned_hi.crop((
            int(round(x0 * scale)), int(round(y0 * scale)),
            int(round(x1 * scale)), int(round(y1 * scale)),
        ))

    def one(rect) -> DebugOcrMatch:
        display_crop, low_xyxy = lo_crop(rect)
        text = ""
        confidence = 0.0
        source: str | None = None
        if run_ocr:
            ocr_crop = hi_crop(low_xyxy)
            if ocr_crop is not None:
                result = read_text_smart(ocr_crop)
                text = result.text
                confidence = result.confidence
                source = result.source
        elif text_by_id is not None:
            preread = text_by_id.get(id(rect))
            if preread:
                text = preread
                confidence = 1.0
                source = "vlm"
        return DebugOcrMatch(
            text=text, bbox=rect.bbox, confidence=confidence,
            crop_data_url=_encode_png_data_url(display_crop),
            source=source,
            oriented_corners=_oriented_corners(rect.corners),
        )

    if not run_ocr:
        return [one(rect) for rect in kept]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(one, kept))


def _oriented_corners(
    corners: list[tuple[int, int]],
) -> list[tuple[int, int]] | None:
    """Return 4 corners of the contour's minimum-area rotated bounding box.

    For axis-aligned rectangles the result matches the bbox exactly; for
    rotated ones (e.g., the 22"x14" diagonal duct) the result is the tilted
    box that hugs the ink. ``None`` if the contour has fewer than 3 corners.
    """
    if len(corners) < 3:
        return None
    import cv2

    arr = np.array(corners, dtype=np.int32).reshape(-1, 1, 2)
    rect = cv2.minAreaRect(arr)
    box = cv2.boxPoints(rect)
    return [(int(round(float(p[0]))), int(round(float(p[1])))) for p in box]


def _encode_png_data_url(image: Image.Image) -> str:
    """Encode an image as a base64 PNG data URL for inline frontend display.

    Caps width at ``_DISPLAY_MAX_WIDTH`` (preserving aspect) using
    nearest-neighbor — keeps the cleaned image binary and prevents the
    browser from bilinear-blurring a high-DPI raster down to viewport size.
    OCR still runs on the original full-DPI image upstream of this call.
    """
    if image.width > _DISPLAY_MAX_WIDTH:
        scale = _DISPLAY_MAX_WIDTH / image.width
        new_size = (_DISPLAY_MAX_WIDTH, max(1, int(image.height * scale)))
        # LANCZOS preserves thin lines as soft-edged dark gradients (NEAREST
        # stochastically drops pixels and breaks 1-px linework). We then
        # re-binarise: any pixel below the cutoff is ink → pure black, every-
        # thing else → pure white. Result is sharp B&W at display size.
        downsampled = image.convert("L").resize(new_size, Image.LANCZOS)
        arr = np.asarray(downsampled, dtype=np.uint8)
        binary = np.where(arr < 200, 0, 255).astype(np.uint8)
        rgb = np.stack([binary, binary, binary], axis=-1)
        image = Image.fromarray(rgb, mode="RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


def _pick_dpi(pdf_path: str | Path) -> int:
    """Pick a DPI that keeps the rasterized page under ``_MAX_RASTER_PIXELS``.

    The O(N²) connector-incidence pass dominates at full ARCH-D, 300 DPI; we
    trade precision for tractability while keeping length cross-check
    sub-percent (§11).
    """
    import pymupdf as _fitz

    doc = _fitz.open(str(pdf_path))
    try:
        rect = doc.load_page(0).rect
        page_w_in = float(rect.width) / 72.0
        page_h_in = float(rect.height) / 72.0
    finally:
        doc.close()
    if page_w_in <= 0 or page_h_in <= 0:
        return _MAX_DPI
    max_dpi = (_MAX_RASTER_PIXELS / (page_w_in * page_h_in)) ** 0.5
    return max(_MIN_DPI, min(_MAX_DPI, int(max_dpi)))


def _resolve_scale(
    image: Image.Image,
    scale_override: ScaleInfo | None,
    warnings: list[str],
) -> ScaleInfo:
    if scale_override is not None:
        return scale_override
    detected = read_title_block_scale(image)
    if detected is not None:
        return detected
    warnings.append("scale: title block unreadable; using manual=0.25 (1/4\"=1'-0\")")
    logger.warning("v4: title-block scale missing; falling back to manual default")
    return ScaleInfo(paper_inches_per_foot=0.25, source="manual", confidence=0.0)


def _collect_boundaries(
    polygons: list[DuctPolygon], image: Image.Image
) -> list[Boundary]:
    out: list[Boundary] = []
    for poly in polygons:
        out.extend(find_segment_boundaries(poly, image))
    return out


def _ocr_labels_with_fallback(
    image: Image.Image,
    polygons: list[DuctPolygon],
    scale: ScaleInfo,
    warnings: list[str],
    *,
    dpi: int,
    ocr: OCRExtractor | None = None,
) -> list[Label]:
    """Run strict label OCR, then a tolerant pass for round ducts (Issue 1)."""
    engine = ocr if ocr is not None else RapidOCRExtractor()
    primary = read_duct_labels(image, polygons, engine)
    labelled_ids = {lbl.polygon_id for lbl in primary}
    fallback: list[Label] = []
    for poly in polygons:
        if poly.id in labelled_ids:
            continue
        recovered = _try_round_fallback(image, poly, scale, engine, dpi=dpi)
        if recovered is not None:
            fallback.append(recovered)
            warnings.append(
                f"label: round-fallback applied to {poly.id} (OCR read 0; pixel width confirms ø)"
            )
    return primary + fallback


def _try_round_fallback(
    image: Image.Image,
    polygon: DuctPolygon,
    scale: ScaleInfo,
    engine: OCRExtractor,
    *,
    dpi: int,
) -> Label | None:
    if polygon.bbox is None or polygon.shape_hint == "rectangular":
        return None
    x, y, w, h = polygon.bbox
    if w <= 0 or h <= 0 or max(w, h) / min(w, h) > _FALLBACK_ASPECT_MAX:
        return None
    crop = image.crop((int(x), int(y), int(x + w) + 1, int(y + h) + 1))
    matches = engine.extract_text(crop)
    if not matches:
        return None
    derived = diameter_from_pixel_width(polygon, scale, dpi)
    for match in matches:
        m = _ROUND_FALLBACK_RE.match(match.text.strip())
        if m is None:
            continue
        candidate_in = float(m.group(1))
        if abs(candidate_in - derived) > _FALLBACK_DIAMETER_TOL_IN:
            continue
        mx, my, mw, mh = match.bbox
        return Label(
            polygon_id=polygon.id,
            raw_text=match.text,
            bbox=(float(x + mx), float(y + my), float(mw), float(mh)),
            orientation_deg=0,
            parsed_value=f'{int(candidate_in)}"ø',
            parsed_shape="round",
        )
    return None


def _synthesize_missing_labels(
    polygons: list[DuctPolygon],
    labels: list[Label],
    scale: ScaleInfo,
    warnings: list[str],
    *,
    dpi: int,
) -> list[Label]:
    """For unlabeled duct polygons, derive the diameter from pixel width (A9)."""
    have = {lbl.polygon_id for lbl in labels}
    out = list(labels)
    for poly in polygons:
        if poly.id in have or poly.shape_hint == "unknown" or poly.bbox is None:
            continue
        derived_in = diameter_from_pixel_width(poly, scale, dpi)
        lo, hi = _PLAUSIBLE_DIAMETER_IN
        if derived_in <= 0 or not (lo <= derived_in <= hi):
            continue
        bx, by, bw, bh = poly.bbox
        out.append(
            Label(
                polygon_id=poly.id,
                raw_text="(pixel-derived)",
                bbox=(float(bx), float(by), float(bw), float(bh)),
                orientation_deg=0,
                parsed_value=f'{int(derived_in)}"ø',
                parsed_shape="round",
            )
        )
        warnings.append(f"label: synthesized ø={int(derived_in)} for {poly.id}")
    return out


def _check_scale_against_labels(
    polygons: list[DuctPolygon],
    labels: list[Label],
    scale: ScaleInfo,
    warnings: list[str],
    *,
    dpi: int,
) -> None:
    poly_lookup = {p.id: p for p in polygons}
    for lbl in labels:
        if lbl.parsed_shape != "round" or lbl.raw_text == "(pixel-derived)":
            continue
        poly = poly_lookup.get(lbl.polygon_id)
        if poly is None or poly.est_width_px is None or lbl.parsed_value is None:
            continue
        digits = re.match(r"(\d+)", lbl.parsed_value)
        labelled_in = float(digits.group(1)) if digits else 0.0
        if labelled_in <= 0:
            continue
        deviation = cross_check_scale(poly.est_width_px, labelled_in, scale, dpi)
        if deviation > _SCALE_DEVIATION_WARN_PCT:
            warnings.append(
                f"scale: {poly.id} deviates {deviation:.1f}% from ø{int(labelled_in)}"
            )


def _rename_connectors(connectors: list[Connector]) -> list[Connector]:
    """Re-id connectors as ``"<kind>::<n>"`` so pressure.py can read fitting kind."""
    counters: dict[str, int] = defaultdict(int)
    out: list[Connector] = []
    for c in connectors:
        new_id = f"{c.kind}::{counters[c.kind]}"
        counters[c.kind] += 1
        out.append(
            Connector(
                id=new_id,
                kind=c.kind,
                centroid=c.centroid,
                incident_polygon_ids=list(c.incident_polygon_ids),
                bbox=c.bbox,
            )
        )
    return out


def _attach_dimensions(network: DuctNetwork, labels: list[Label]) -> None:
    by_polygon: dict[str, Label] = {lbl.polygon_id: lbl for lbl in labels}
    for edge_id, edge in list(network.edges.items()):
        lbl = by_polygon.get(edge.polygon_id)
        if lbl is None or lbl.parsed_value is None:
            continue
        network.edges[edge_id] = NetworkEdge(
            id=edge.id,
            polygon_id=edge.polygon_id,
            node_a_id=edge.node_a_id,
            node_b_id=edge.node_b_id,
            centerline=list(edge.centerline),
            length_ft=edge.length_ft,
            dimension_value=lbl.parsed_value,
            dimension_shape=lbl.parsed_shape,
            terminal_ids_on_edge=list(edge.terminal_ids_on_edge),
        )


_NULL_PRESSURE = PressureResult(
    start_in_wc=0.0, end_in_wc=0.0, smacna_class="Low", velocity_fpm=0.0
)


def _classify_drop(
    poly: DuctPolygon,
    labelled: bool,
    derived_in: float | None,
) -> DropReason | None:
    """Mirror the runner's three filter points: shape, plausibility, label."""
    if poly.shape_hint == "unknown":
        return "shape_unknown"
    if labelled:
        return None
    lo, hi = _PLAUSIBLE_DIAMETER_IN
    if derived_in is None or derived_in <= 0 or not (lo <= derived_in <= hi):
        return "diameter_out_of_range"
    return "no_label"


def _polygon_bbox_int(poly: DuctPolygon) -> tuple[int, int, int, int]:
    """Bbox as ints; fall back to a points scan when the dataclass field is None."""
    if poly.bbox is not None:
        x, y, w, h = poly.bbox
        return int(x), int(y), int(w), int(h)
    xs = [p[0] for p in poly.points]
    ys = [p[1] for p in poly.points]
    if not xs or not ys:
        return 0, 0, 0, 0
    return int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys))


def _build_debug_payload(
    polygons: list[DuctPolygon],
    labelled_ids: set[str],
    scale: ScaleInfo,
    dpi: int,
) -> V4Debug:
    out: list[DebugPolygon] = []
    for poly in polygons:
        derived: float | None
        try:
            derived = diameter_from_pixel_width(poly, scale, dpi)
        except Exception:
            derived = None
        labelled = poly.id in labelled_ids
        kept = labelled and poly.shape_hint != "unknown"
        drop = None if kept else _classify_drop(poly, labelled, derived)
        out.append(
            DebugPolygon(
                id=poly.id,
                bbox=_polygon_bbox_int(poly),
                polygon=[(int(x), int(y)) for x, y in poly.points],
                shape_hint=poly.shape_hint,
                est_width_px=float(poly.est_width_px or 0.0),
                est_diameter_in=derived,
                kept=kept,
                drop_reason=drop,
            )
        )
    return V4Debug(polygons=out)


def _build_v4_segments(
    network: DuctNetwork,
    cfm_map: dict[str, CfmRange],
    pressure_map: dict[str, PressureResult],
    labels: list[Label],
    terminals: list[Terminal],
) -> list[V4Segment]:
    # Centerline doubles as the polygon hint — the CV polygon points aren't
    # threaded into DuctNetwork; the centerline is what the UI annotates.
    by_polygon: dict[str, Label] = {lbl.polygon_id: lbl for lbl in labels}
    term_lookup: dict[str, Terminal] = {t.id: t for t in terminals}
    out: list[V4Segment] = []
    for edge in network.edges.values():
        lbl = by_polygon.get(edge.polygon_id)
        dimension = lbl.parsed_value if lbl and lbl.parsed_value else "unknown"
        out.append(
            V4Segment(
                id=edge.id,
                dimension=dimension,
                length_ft=edge.length_ft,
                cfm_range=cfm_map.get(edge.id, CfmRange(start=0.0, end=0.0)),
                pressure=pressure_map.get(edge.id, _NULL_PRESSURE),
                polygon=list(edge.centerline),
                terminals_on_segment=_terminal_refs(edge, term_lookup),
            )
        )
    return out


def _terminal_refs(edge: NetworkEdge, lookup: dict[str, Terminal]) -> list[TerminalRef]:
    return [
        TerminalRef(terminal_id=tid, distance_along_segment_ft=0.0,
                    cfm=(lookup[tid].cfm or 0.0))
        for tid in edge.terminal_ids_on_edge if tid in lookup
    ]


def _build_v4_terminals(terminals: list[Terminal]) -> list[V4Terminal]:
    return [
        V4Terminal(id=t.id, center=t.center, radius=t.radius,
                   type_letter=t.type_letter, cfm=t.cfm)
        for t in terminals
    ]


