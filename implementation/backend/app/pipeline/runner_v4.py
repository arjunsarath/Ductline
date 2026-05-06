"""V4 pipeline orchestrator (SOLUTION-DESIGN-V4 §3).

Single entry point for the V4 path. Coexists with V3's runner; the API layer
chooses between them. Caller controls scale (when title-block OCR fails) and
flow direction (when auto-inference is wrong) via the optional overrides.
"""

from __future__ import annotations

import base64
import io
import logging
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
    DEFAULT_EPSILON_FRAC,
    DEFAULT_MAX_CORNER_COS,
    DEFAULT_MAX_INK_PCT,
    DEFAULT_MIN_ASPECT_RATIO,
    DEFAULT_MIN_DUCT_ASPECT,
    DEFAULT_MIN_INK_PCT,
    DEFAULT_MIN_WHITE_PCT,
    filter_by_aspect_ratio,
    filter_by_content,
    filter_by_interior_emptiness,
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
    if enable_rectangle:
        emit("filter_rectangle", f"keeping only rectangles (cos≤{corner_cos}, eps={eps_frac})",
             count=sum(1 for t in tagged if t.kept))
        tagged = filter_is_rectangle(
            tagged, epsilon_frac=eps_frac, max_corner_cos=corner_cos,
        )
    if enable_squarish:
        ratio_min = (
            DEFAULT_MIN_DUCT_ASPECT if min_duct_aspect is None else min_duct_aspect
        )
        emit("filter_squarish", f"dropping squarish (aspect < {ratio_min})",
             count=sum(1 for t in tagged if t.kept))
        tagged = filter_squarish(tagged, min_aspect=ratio_min)
    if enable_min_ink:
        ink_min = DEFAULT_MIN_INK_PCT if min_ink_pct is None else min_ink_pct
        emit("filter_min_ink", f"dropping empty interiors (ink < {ink_min:.1%})",
             count=sum(1 for t in tagged if t.kept))
        tagged = filter_min_ink(tagged, cleaned, min_ink_pct=ink_min)
    if enable_max_ink:
        ink_max = DEFAULT_MAX_INK_PCT if max_ink_pct is None else max_ink_pct
        emit("filter_max_ink", f"dropping mostly-ink interiors (ink > {ink_max:.0%})",
             count=sum(1 for t in tagged if t.kept))
        tagged = filter_max_ink(tagged, cleaned, max_ink_pct=ink_max)
    if enable_aspect_ratio:
        emit("filter_aspect_ratio", f"requiring aspect ratio ≥ {aspect}",
             count=sum(1 for t in tagged if t.kept))
        tagged = filter_by_aspect_ratio(tagged, min_ratio=aspect)
    if enable_interior:
        emit("filter_interior", f"requiring interior ≥ {white_pct:.0%} white",
             count=sum(1 for t in tagged if t.kept))
        tagged = filter_by_interior_emptiness(tagged, cleaned, min_white_pct=white_pct)
    if enable_content:
        emit("filter_content", "OCR full page; dropping rects with non-duct text",
             count=sum(1 for t in tagged if t.kept))
        tagged = filter_by_content(tagged, cleaned, RapidOCRExtractor())
    debug_rects = [
        DebugRectangle(
            corners=t.corners, kept=t.kept, drop_reason=t.drop_reason,
        )
        for t in tagged
    ]

    # Always emit one ``DebugOcrMatch`` per kept rectangle so the frontend
    # can render clickable hit-targets and show the exact crop on click.
    # ``text`` stays empty until the operator explicitly opts into the slow
    # VLM pass (``enable_vlm_ocr=true``); flipping the flag re-fires the
    # request and the same rectangles light up with VLM-read text.
    pad_px = 12
    kept_for_ocr = [t for t in tagged if t.kept]
    kept_for_ocr.sort(key=lambda t: -(t.bbox[2] * t.bbox[3]))
    if max_vlm_crops is not None and max_vlm_crops > 0:
        kept_for_ocr = kept_for_ocr[:max_vlm_crops]
    if enable_vlm_ocr:
        emit(
            "ocr_per_crop",
            f"VLM ladder over {len(kept_for_ocr)} rectangles, masked + retried",
            count=len(kept_for_ocr),
        )
        debug_ocr = _ocr_per_crop_with_retry(
            cleaned, kept_for_ocr, pdf_path, pad_px,
            rect_dpi=dpi, ink_threshold=ink_threshold,
        )
    else:
        emit(
            "rectangles_only", f"emitting {len(kept_for_ocr)} rectangles + crops",
            count=len(kept_for_ocr),
        )
        debug_ocr = _ocr_per_crop(
            cleaned, None, kept_for_ocr, pad_px,
            rect_dpi=dpi, ocr_dpi=ocr_dpi, run_ocr=False,
        )
    debug_dimensions: list[DebugDimension] = []

    # ── STEP-DEBUG: short-circuit after rectangle filtering ───────────────
    # Returns the cleaned raster + every detected rectangle (tagged with the
    # filter outcome) so the operator can iterate on filters in the UI
    # before re-enabling downstream stages. Re-enable the full pipeline by
    # removing this early-return block.
    cleaned_url = _encode_png_data_url(cleaned)
    scale_stub = ScaleInfo(paper_inches_per_foot=0.25, source="manual", confidence=0.0)
    kept_count = sum(1 for r in debug_rects if r.kept)
    result = V4Result(
        segments=[], terminals=[], scale=scale_stub, op_vars=op_vars,
        page_dims=page_dims, warnings=warnings, debug=None,
        stage_image_data_url=cleaned_url, stage_stopped_after="ocr_all",
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
# Exponential backoff between retries. attempt 1 → 0s, attempt 2 → 0.5s,
# attempt 3 → 1.0s. Spreads VLM calls so we don't spike the cloud endpoint
# when many rectangles fail simultaneously across the worker pool.
_OCR_BACKOFF_BASE_SEC = 0.5


def _ocr_per_crop_with_retry(
    cleaned_lo: Image.Image,
    kept: list,
    pdf_path: str | Path,
    pad_px: int,
    *,
    rect_dpi: int,
    ink_threshold: int | None,
    max_workers: int = 4,
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
        text = read_text_from_crop(masked)
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

    out: list[DebugOcrMatch] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for result in pool.map(one, kept):
            if result is not None:
                out.append(result)
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
        source = None
        if run_ocr:
            ocr_crop = hi_crop(low_xyxy)
            if ocr_crop is not None:
                result = read_text_smart(ocr_crop)
                text = result.text
                confidence = result.confidence
                source = result.source
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


