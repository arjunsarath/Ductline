"""V3DetectionPipeline — composes the V3 stages (SOLUTION-DESIGN-V3 §4).

Reuses V1/V2's ``IngestStage`` and ``ProbeOCRStage`` (they handle file
parsing + rotation + smallest-text measurement). Everything after that
is V3-specific and deterministic.

This runner does not share V1/V2's ``DetectionPipeline`` class — the
two coexist in the codebase. V3 is wired through ``app.api.v3_routes``
(``POST /v3/render`` and ``POST /v3/detect``); ``scripts/run_v3.py``
also drives it for CLI smoke checks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from app.config import settings
from app.ocr.base import OCRExtractor, OCRMatch
from app.pipeline.base import PipelineContext
from app.pipeline.ingest import IngestStage
from app.pipeline.probe_ocr import ProbeOCRStage
from app.pipeline.v3.attribute import (
    AttributedToken,
    attribute_flow_in_mask,
    attribute_in_mask,
    attribute_round_in_mask,
)
from app.pipeline.v3.calibrate import (
    CalibrationResult,
    calibrate,
    resolve_visible_sides,
)
from app.pipeline.v3.color_mask import SystemMask, build_all_system_masks
from app.pipeline.v3.config import ColorPick, V3PipelineConfig
from app.pipeline.v3.ocr_classify import (
    classify_all,
    detect_page_unit,
    filter_for_page_unit,
    ocr_full_page,
)
from app.pipeline.v3.pressure import PressureResult, from_flow, from_size_only
from app.source.base import DrawingSource

logger = logging.getLogger(__name__)


# ── Result types — typed dicts (not pydantic) so the runner is unit-testable
# without serialisation getting in the way. The CLI script handles JSON
# encoding; API integration in a later PR will wrap these in pydantic
# models that mirror schemas_v3 §7 of the design doc. ───────────────────


@dataclass
class V3Segment:
    id: str
    system_id: str
    # Shape determines how the dim values are interpreted:
    #   • "rectangular" — ``visible_unit × hidden_unit`` (page-unit), one
    #     calibrated pair per OCR'd ``AxB`` token after side-disambig.
    #   • "round" — ``visible_unit`` is the diameter (in page-unit); the
    #     ``hidden_unit`` field repeats the diameter for downstream code
    #     that pre-existed the round path (sidebar / pressure / popover).
    shape: Literal["rectangular", "round"]
    visible_unit: int
    hidden_unit: int
    page_unit: Literal["in", "mm"]
    pixel_width: float
    chosen_ppu: float
    delta_pct: float
    dim_confidence: Literal["high", "medium", "low"]
    dim_source: str
    # Which attribution rule produced this segment — propagated from
    # ``AttributedToken.rule`` so the popover can surface the reasoning
    # trace V3 §5.7 specifies (in_mask vs proximity).
    rule: Literal["in_mask", "proximity"]
    pressure: PressureResult
    skel_xy: tuple[int, int]
    token_text: str


@dataclass
class V3SystemSummary:
    system_id: str
    label: str
    pattern: str
    kind: str
    mask_pixels: int
    filled_pixels: int
    n_segments: int


@dataclass
class V3Result:
    drawing_id: str
    width_px: int
    height_px: int
    rotation_applied: int
    page_unit: Literal["in", "mm"]
    ppu: float | None
    target_dpi: int
    rendered_size: tuple[int, int]
    systems: list[V3SystemSummary]
    segments: list[V3Segment]
    n_tokens_total: int
    n_dim_rect_tokens: int
    n_flow_tokens: int
    n_attributed_rect: int
    n_attributed_flow: int
    calibration: CalibrationResult
    errors: list[str] = field(default_factory=list)


@dataclass
class V3RenderArtifacts:
    """Non-serialisable companion to ``V3Result`` carrying numpy arrays.

    The API layer uses these to produce the overlay PNG without re-running
    the pipeline. Not part of the JSON wire shape.
    """

    rendered_bgr: NDArray[np.uint8]
    system_masks: list[SystemMask]


class V3DetectionPipeline:
    def __init__(self, ocr: OCRExtractor) -> None:
        self._ocr = ocr

    def run(
        self,
        file_bytes: bytes,
        original_filename: str,
        config: V3PipelineConfig,
        *,
        drawing_id: str | None = None,
    ) -> V3Result:
        """Convenience wrapper — see ``run_with_artifacts`` for the full return."""
        result, _ = self.run_with_artifacts(
            file_bytes,
            original_filename,
            config,
            drawing_id=drawing_id,
        )
        return result

    def run_with_artifacts(
        self,
        file_bytes: bytes,
        original_filename: str,
        config: V3PipelineConfig,
        *,
        drawing_id: str | None = None,
    ) -> tuple[V3Result, V3RenderArtifacts | None]:
        """Run the pipeline and return both the JSON-serialisable result and
        the numpy artefacts needed to render an overlay PNG.

        Returns ``(result, None)`` when the pipeline aborts early
        (e.g. raster source below resolution floor) so callers can still
        emit a structured error response with no overlay.
        """
        ctx = PipelineContext(
            drawing_id=drawing_id or str(uuid4()),
            original_filename=original_filename,
        )

        # ── Stage 1: ingest (reused) ──────────────────────────────────────
        ctx = IngestStage(file_bytes, original_filename).run(ctx)
        assert ctx.source is not None, "ingest must produce a source"

        # ── Raster-source resolution gate (V3 §5.3) ──────────────────────
        if ctx.source.kind != "vector_pdf":
            long_edge = max(ctx.width_px, ctx.height_px)
            if long_edge < config.raster_min_long_edge_px:
                # Tag in errors and return early — no point running the rest
                ctx.errors.append(
                    f"raster source too low resolution ({long_edge}px long edge); "
                    f"need >= {config.raster_min_long_edge_px}px"
                )
                return _empty_result(ctx, config, ctx.source), None

        # ── Stage 2: probe OCR + rotation (reused) ────────────────────────
        ctx = ProbeOCRStage(self._ocr).run(ctx)

        # ── Stage 3: render-for-OCR at adaptive DPI ──────────────────────
        target_dpi, rendered_pil = self._render_for_ocr(ctx.source, ctx, config)
        rendered_bgr = _pil_to_bgr(rendered_pil)
        height_px, width_px = rendered_bgr.shape[:2]

        # ── Stage 6: full-page OCR + classify ────────────────────────────
        # OCR runs first now so its bboxes can prune text-glyph false
        # positives from the color masks (a maroon TEXT label sharing
        # the picked hue would otherwise flood into a phantom duct).
        matches = ocr_full_page(rendered_pil, self._ocr)
        all_tokens = classify_all(matches)
        page_unit = detect_page_unit(all_tokens)
        page_tokens = filter_for_page_unit(all_tokens, page_unit)

        # ── Stage 5: color mask + segment ────────────────────────────────
        # Build the text-exclusion mask from OCR. PaddleOCR's detection
        # head is trained primarily on horizontal text and routinely
        # misses vertical/rotated labels — exactly the labels that share
        # the duct hue and survive the area filter as false positives.
        # Run a second OCR pass on the page rotated 90° CW and remap
        # those bboxes back to original coords. Mask-only use; we do
        # *not* feed the rotated matches into ``classify_all`` because
        # the classifier downstream assumes text orientation matches
        # the dim-token grammar.
        rotated_pil = rendered_pil.rotate(-90, expand=True)
        matches_v = ocr_full_page(rotated_pil, self._ocr)
        matches_v_remapped = _remap_bboxes_from_cw90(
            matches_v,
            original_hw=(height_px, width_px),
        )
        all_text_for_mask = list(matches) + matches_v_remapped
        text_mask = _ocr_text_mask(
            (height_px, width_px),
            all_text_for_mask,
            pad_px=3,
        )
        system_masks = build_all_system_masks(
            rendered_bgr,
            config,
            text_mask=text_mask,
        )

        rect_tokens = [t for t in page_tokens if t.kind == "dim_rect"]
        round_tokens = [t for t in page_tokens if t.kind == "dim_round"]
        flow_tokens = [t for t in page_tokens if t.kind == "flow"]

        # ── Stage 7: attribute (in-mask, Pattern B) ──────────────────────
        rect_pairs = attribute_in_mask(rect_tokens, system_masks, config)
        round_pairs = attribute_round_in_mask(round_tokens, system_masks, config)
        flow_pairs = attribute_flow_in_mask(flow_tokens, system_masks, config)

        # ── Stage 8: calibrate (histogram of candidates) ─────────────────
        # Calibration uses rectangular pairs only: each ``AxB`` token
        # offers two ppu candidates (one per side) which is what makes
        # the histogram-of-candidates rule converge. Round tokens only
        # carry a single diameter value, so they can't bootstrap ppu;
        # we apply the rect-derived ppu to them downstream.
        cal = calibrate(rect_pairs, config)
        resolved = resolve_visible_sides(rect_pairs, cal, config)

        # ── Stage 10: pressure class per resolved segment ────────────────
        flows_by_system: dict[int, list[AttributedToken]] = {}
        for fp in flow_pairs:
            flows_by_system.setdefault(fp.system_index, []).append(fp)

        segments: list[V3Segment] = []
        for i, r in enumerate(resolved):
            sm = system_masks[r.attributed.system_index]
            pick = sm.pick
            visible_unit = r.visible
            hidden_unit = r.hidden
            flow_for_seg = _nearest_flow(
                r.attributed, flows_by_system.get(r.attributed.system_index, [])
            )
            if flow_for_seg is not None:
                pressure = from_flow(
                    width_unit=float(visible_unit),
                    height_unit=float(hidden_unit),
                    flow_value=float(flow_for_seg.token.flow_value or 0),
                    flow_unit=flow_for_seg.token.flow_unit
                    or ("CFM" if page_unit == "in" else "L/s"),
                    page_unit=page_unit,
                )
            else:
                pressure = from_size_only(
                    width_unit=float(visible_unit),
                    height_unit=float(hidden_unit),
                    page_unit=page_unit,
                )
            segments.append(
                V3Segment(
                    id=f"seg_{i:04d}",
                    system_id=pick.system_id or _default_system_id(pick),
                    shape="rectangular",
                    visible_unit=visible_unit,
                    hidden_unit=hidden_unit,
                    page_unit=page_unit,
                    pixel_width=r.attributed.width_px,
                    chosen_ppu=r.chosen_ppu,
                    delta_pct=r.delta_pct,
                    dim_confidence=r.confidence,
                    dim_source="ocr:in_mask",
                    rule=r.attributed.rule,
                    pressure=pressure,
                    skel_xy=r.attributed.skel_xy,
                    token_text=r.attributed.token.text,
                )
            )

        # ── Round-duct segments — only emitted once we have a global ppu
        # to validate against. Each round token's implied ppu is
        # ``pixel_diameter / token.diameter``. We accept tokens whose
        # implied ppu is within ±config.inlier_band_pct of global ppu;
        # outside that band we still emit but mark confidence=low. ──────
        if cal.ppu is not None:
            for p in round_pairs:
                tok = p.token
                if tok.diameter is None or p.width_px <= 0:
                    continue
                implied_ppu = p.width_px / tok.diameter
                delta_pct = (implied_ppu - cal.ppu) / cal.ppu * 100.0
                if abs(delta_pct) <= config.inlier_band_pct:
                    confidence: Literal["high", "medium", "low"] = "high"
                elif abs(delta_pct) <= 2 * config.inlier_band_pct:
                    confidence = "medium"
                else:
                    confidence = "low"
                sm = system_masks[p.system_index]
                pick = sm.pick
                # Pressure-class for round: area = π(d/2)²
                # We don't have a perfect ``from_flow`` round-aware path;
                # compute area in page units and pass to ``from_size_only``
                # via an equivalent square width × height that gives the
                # same area. Tracked in V3 §10 as phase-2 work.
                d = float(tok.diameter)
                pressure = from_size_only(
                    width_unit=d,
                    height_unit=d,
                    page_unit=page_unit,
                )
                segments.append(
                    V3Segment(
                        id=f"seg_{len(segments):04d}",
                        system_id=pick.system_id or _default_system_id(pick),
                        shape="round",
                        visible_unit=int(d),
                        hidden_unit=int(d),
                        page_unit=page_unit,
                        pixel_width=p.width_px,
                        chosen_ppu=implied_ppu,
                        delta_pct=delta_pct,
                        dim_confidence=confidence,
                        dim_source="ocr:in_mask",
                        rule=p.rule,
                        pressure=pressure,
                        skel_xy=p.skel_xy,
                        token_text=tok.text,
                    )
                )

        system_summaries = [
            V3SystemSummary(
                system_id=sm.pick.system_id or _default_system_id(sm.pick),
                label=sm.pick.label,
                pattern=sm.pick.pattern,
                kind=sm.pick.kind,
                mask_pixels=int(sm.mask.sum() // 255),
                filled_pixels=int(sm.filled.sum() // 255),
                n_segments=sum(
                    1
                    for s in segments
                    if s.system_id == (sm.pick.system_id or _default_system_id(sm.pick))
                ),
            )
            for sm in system_masks
        ]

        result = V3Result(
            drawing_id=ctx.drawing_id,
            width_px=width_px,
            height_px=height_px,
            rotation_applied=int(ctx.source.rotation_applied),
            page_unit=page_unit,
            ppu=cal.ppu,
            target_dpi=target_dpi,
            rendered_size=(width_px, height_px),
            systems=system_summaries,
            segments=segments,
            n_tokens_total=len(matches),
            n_dim_rect_tokens=len(rect_tokens),
            n_flow_tokens=len(flow_tokens),
            n_attributed_rect=len(rect_pairs),
            n_attributed_flow=len(flow_pairs),
            calibration=cal,
            errors=list(ctx.errors),
        )
        artifacts = V3RenderArtifacts(
            rendered_bgr=rendered_bgr,
            system_masks=system_masks,
        )
        return result, artifacts

    # ── helpers ──────────────────────────────────────────────────────────

    def _render_for_ocr(
        self,
        source: DrawingSource,
        ctx: PipelineContext,
        config: V3PipelineConfig,
    ) -> tuple[int, Image.Image]:
        """Pick adaptive DPI; render the full page at that DPI.

        For vector PDFs: re-render losslessly at the chosen DPI.
        For raster sources: return the existing probe (DPI is fixed by ingest).
        """
        if source.kind == "vector_pdf":
            target_dpi = config.min_dpi
            if ctx.ocr_cache is not None:
                target_dpi = source.smart_dpi_for_rect(
                    rect_pt=(0.0, 0.0, source.page_size_pt[0], source.page_size_pt[1])
                    if source.page_size_pt
                    else (0.0, 0.0, 1.0, 1.0),
                    ocr_cache=ctx.ocr_cache,
                    target_text_px=config.target_text_height_px,
                )
                if target_dpi <= 0:
                    target_dpi = config.min_dpi
            target_dpi = max(config.min_dpi, min(target_dpi, config.max_dpi))
            assert source.page_size_pt is not None
            page_w_pt, page_h_pt = source.page_size_pt
            rendered = source.render(
                rect_pt=(0.0, 0.0, page_w_pt, page_h_pt),
                dpi=target_dpi,
            )
            return target_dpi, rendered
        # raster — no re-render
        return settings.raster_dpi, source.raster_probe


def _remap_bboxes_from_cw90(
    matches: list[OCRMatch], original_hw: tuple[int, int]
) -> list[_BBoxOnly]:
    """Remap OCR bboxes from a 90°-CW-rotated page back to original coords.

    The bboxes are wrapped in lightweight stand-in objects with the same
    ``.bbox`` interface as ``OCRMatch`` so ``_ocr_text_mask`` can consume
    them uniformly. We don't carry ``text``/``confidence`` because the
    rotated-OCR matches are only used for the text-exclusion mask, never
    for downstream classification.
    """
    h, w = original_hw  # original page dims (rotated dims are swapped)
    out: list[_BBoxOnly] = []
    for m in matches:
        rx, ry, rbw, rbh = m.bbox
        # PIL.Image.rotate(-90, expand=True) places original (x, y) at
        # (h - 1 - y, x) in the rotated frame. Inverting:
        #   original.x = ry
        #   original.y = h - 1 - rx - rbw
        #   original.w = rbh
        #   original.h = rbw
        ox = ry
        oy = (h - 1) - rx - rbw
        ow = rbh
        oh = rbw
        out.append(_BBoxOnly(bbox=(ox, oy, ow, oh)))
    # eliminate boxes outside the page (numerical noise near edges)
    return [b for b in out if 0 <= b.bbox[0] < w and 0 <= b.bbox[1] < h]


@dataclass(frozen=True)
class _BBoxOnly:
    """Minimal stand-in for ``OCRMatch`` carrying just the rect — used
    by the rotated-OCR pass to feed ``_ocr_text_mask``. Avoids importing
    OCRMatch's required ``text``/``confidence`` fields when we don't need
    them for mask construction.
    """

    bbox: tuple[int, int, int, int]


def _ocr_text_mask(
    shape_hw: tuple[int, int],
    matches: list[OCRMatch] | list[_BBoxOnly] | list,
    pad_px: int,
) -> NDArray[np.uint8]:
    """Binary mask of OCR text bboxes — used to exclude text glyphs from
    the color inRange. Padding covers the anti-aliased fringe pixels of
    each glyph that would otherwise leak past a tight bbox.
    """
    h, w = shape_hw
    mask = np.zeros((h, w), dtype=np.uint8)
    for m in matches:
        x, y, bw, bh = m.bbox
        x0 = max(0, x - pad_px)
        y0 = max(0, y - pad_px)
        x1 = min(w, x + bw + pad_px)
        y1 = min(h, y + bh + pad_px)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = 255
    return mask


def _pil_to_bgr(img: Image.Image) -> NDArray[np.uint8]:
    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.asarray(img)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _default_system_id(pick: ColorPick) -> str:
    safe = pick.label.lower().replace(" ", "_")
    return f"sys_{safe}"


def _nearest_flow(
    rect_pair: AttributedToken,
    flows: list[AttributedToken],
    max_dist_px: float = 250.0,
) -> AttributedToken | None:
    """Match a rect-segment to its co-located flow within ``max_dist_px``.

    Both anchors are skeleton points within the same system's mask. A
    flow token whose skeleton anchor is more than ``max_dist_px`` away
    is not "this segment's flow" — it belongs to a different run that
    happens to be on the same connected component. Beyond that distance
    we'd be pulling a downstream diffuser's CFM into a main duct's
    velocity computation, which is the engineering error V3 §10 calls
    out as phase-2 work (duct topology + downstream aggregation).
    """
    if not flows:
        return None
    rx, ry = rect_pair.skel_xy
    best = None
    best_d2 = max_dist_px**2
    for f in flows:
        fx, fy = f.skel_xy
        d2 = (fx - rx) ** 2 + (fy - ry) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = f
    return best


def _empty_result(
    ctx: PipelineContext,
    config: V3PipelineConfig,
    source: DrawingSource,
) -> V3Result:
    return V3Result(
        drawing_id=ctx.drawing_id,
        width_px=ctx.width_px,
        height_px=ctx.height_px,
        rotation_applied=int(source.rotation_applied),
        page_unit="in",
        ppu=None,
        target_dpi=0,
        rendered_size=(ctx.width_px, ctx.height_px),
        systems=[],
        segments=[],
        n_tokens_total=0,
        n_dim_rect_tokens=0,
        n_flow_tokens=0,
        n_attributed_rect=0,
        n_attributed_flow=0,
        calibration=CalibrationResult(ppu=None, n_pairs=0, n_in_band=0, band_lo=None, band_hi=None),
        errors=list(ctx.errors),
    )
