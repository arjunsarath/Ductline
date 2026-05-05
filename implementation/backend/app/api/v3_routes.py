"""V3 HTTP routes — color-driven deterministic pipeline (SOLUTION-DESIGN-V3).

Three endpoints:

  • ``POST /v3/render``  — accept a PDF/image, return a rendered page
    (PNG, base64 in JSON) ready for the color picker. The render uses
    the same ingest + adaptive-DPI logic the detect endpoint will, so
    the picker's pixel coordinates line up with what detect runs over.

  • ``POST /v3/detect``  — accept the same source plus a ``picks`` JSON,
    run the V3 pipeline, return the result + an overlay PNG (base64).

  • ``GET  /v3/samples`` — bundled benchmark drawings (mirror of the
    legacy ``/agent/samples`` so the V3 frontend can fetch the test PDF
    without touching the agent surface).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any, Literal

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

from app.api.deps import build_ocr
from app.config import settings
from app.ocr.base import OCRExtractor
from app.pipeline.base import PipelineContext
from app.pipeline.ingest import IngestStage
from app.pipeline.probe_ocr import ProbeOCRStage
from app.pipeline.v3.config import ColorPick, HSVRange, V3PipelineConfig
from app.pipeline.v3.render import render_overlay
from app.pipeline.v3.runner import V3DetectionPipeline, V3Result

logger = logging.getLogger(__name__)
router = APIRouter()


# Docker compose mounts the host's sample-HVAC folder at /drawings.
# Bare host-side dev (uvicorn outside docker) doesn't, so honour an
# env override and fall back to the in-tree sample-HVAC folder so the
# upload screen has something to offer.
_SAMPLES_DIR = Path(
    os.environ.get("V3_SAMPLES_DIR")
    or (
        "/drawings"
        if Path("/drawings").exists()
        else str(Path(__file__).resolve().parents[4] / "sample-HVAC")
    )
)
_SAMPLE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}

# Target for the histogram subsampler in ``_extract_swatches``: aim for
# ~1 megapixel of sampled pixels regardless of source size, so a 100 MP
# A1-at-600 DPI render still gives a fast and representative palette.
_SWATCH_SUBSAMPLE_TARGET_PX = 1024 * 1024


# ── Request shapes ──────────────────────────────────────────────────────────


class HSVBand(BaseModel):
    h_lo: int = Field(ge=0, le=180)
    h_hi: int = Field(ge=0, le=180)
    s_lo: int = Field(ge=0, le=255)
    s_hi: int = Field(ge=0, le=255)
    v_lo: int = Field(ge=0, le=255)
    v_hi: int = Field(ge=0, le=255)


class PickPayload(BaseModel):
    """Wire shape for one color pick from the frontend's color-picker UI."""

    label: str
    pattern: Literal["outline", "centerline"] = "outline"
    kind: Literal["supply", "return", "exhaust", "outside", "other"] = "other"
    primary: HSVBand
    second: HSVBand | None = None
    display_color_bgr: tuple[int, int, int] = (255, 100, 0)
    system_id: str = ""


# ── /v3/render ──────────────────────────────────────────────────────────────


class Swatch(BaseModel):
    """A dominant page color the picker can offer as a one-click duct
    pick. ``r/g/b`` are the bin's representative pixel; ``count`` is the
    number of saturated pixels that fell into the bin (so the UI can sort
    by prominence). ``hsv`` is the OpenCV-space HSV of the same color so
    the picker can build a tight inRange band without redoing the math.
    ``sample_x/y`` is one representative pixel coord (in rendered-page
    pixel space) so the picker can drop a marker on the canvas at that
    point and the user immediately sees which on-page color each swatch
    refers to.
    """

    r: int
    g: int
    b: int
    count: int
    h: int  # 0..180 (OpenCV)
    s: int  # 0..255
    v: int  # 0..255
    sample_x: int
    sample_y: int


class RenderResponse(BaseModel):
    drawing_id: str
    width_px: int
    height_px: int
    target_dpi: int
    rotation_applied: int
    rendered_png_base64: str
    smallest_text_height_px_p5: float | None
    swatches: list[Swatch]
    errors: list[str]


def _build_renderer_context(
    file_bytes: bytes,
    filename: str,
    ocr: OCRExtractor,
) -> PipelineContext:
    """Run ingest + probe-OCR so we have rotation + adaptive-DPI inputs."""
    ctx = PipelineContext(drawing_id="", original_filename=filename)
    ctx = IngestStage(file_bytes, filename).run(ctx)
    if ctx.source is None:
        raise HTTPException(status_code=400, detail="ingest failed")
    ctx = ProbeOCRStage(ocr).run(ctx)
    return ctx


def _render_full_page(
    ctx: PipelineContext,
    *,
    target_text_height_px: int,
    min_dpi: int,
    max_dpi: int,
) -> tuple[int, bytes, int, int, Image.Image]:
    """Render the page at adaptive DPI and return (dpi, png_bytes, w, h, pil)."""
    src = ctx.source
    assert src is not None

    if src.kind == "vector_pdf":
        target_dpi = min_dpi
        if ctx.ocr_cache is not None:
            target_dpi = src.smart_dpi_for_rect(
                rect_pt=(0.0, 0.0, src.page_size_pt[0], src.page_size_pt[1])
                if src.page_size_pt
                else (0.0, 0.0, 1.0, 1.0),
                ocr_cache=ctx.ocr_cache,
                target_text_px=target_text_height_px,
            )
            if target_dpi <= 0:
                target_dpi = min_dpi
        target_dpi = max(min_dpi, min(target_dpi, max_dpi))
        assert src.page_size_pt is not None
        page_w_pt, page_h_pt = src.page_size_pt
        rendered = src.render(
            rect_pt=(0.0, 0.0, page_w_pt, page_h_pt),
            dpi=target_dpi,
        )
    else:
        target_dpi = settings.raster_dpi
        rendered = src.raster_probe

    buf = BytesIO()
    rendered.save(buf, format="PNG")
    return target_dpi, buf.getvalue(), rendered.width, rendered.height, rendered


def _extract_swatches(
    rendered: Image.Image,
    *,
    max_swatches: int = 12,
) -> list[Swatch]:
    """Histogram-quantize the page to its dominant saturated colors.

    Drops near-greyscale pixels (low S) and near-black/near-white before
    counting so paper background, text, and grid lines don't dominate the
    palette. Bins are 6-bit-per-channel (4096 cells) — narrow enough to
    distinguish the system colors typical CAD palettes use, wide enough
    to absorb anti-aliased fringe pixels into the same bin as the line core.
    """
    rgb = np.asarray(rendered.convert("RGB"), dtype=np.uint8)
    h, w, _ = rgb.shape
    # Subsample for speed — at 600 DPI a full A1 page is 100M pixels.
    step = max(1, int(np.sqrt(h * w / _SWATCH_SUBSAMPLE_TARGET_PX)))
    sub = rgb[::step, ::step]
    sh, sw = sub.shape[:2]
    px = sub.reshape(-1, 3)
    # Original-coord lookup parallel to ``px`` so we can return a
    # representative on-page (x, y) for each swatch.
    ys, xs = np.meshgrid(
        np.arange(sh, dtype=np.int32) * step,
        np.arange(sw, dtype=np.int32) * step,
        indexing="ij",
    )
    coords = np.stack([xs.ravel(), ys.ravel()], axis=1)  # (N, 2) → x, y
    bgr = px[:, ::-1]
    hsv = cv2.cvtColor(bgr.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    keep = (hsv[:, 1] >= 60) & (hsv[:, 2] >= 60) & (hsv[:, 2] <= 240)
    if not keep.any():
        return []
    px = px[keep]
    hsv = hsv[keep]
    coords = coords[keep]

    # 6-bit-per-channel quantization → 64^3 cells. Use a single int key
    # so np.unique is fast even on millions of pixels.
    q = px.astype(np.uint32) >> 2
    keys = (q[:, 0] << 12) | (q[:, 1] << 6) | q[:, 2]
    unique_keys, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    order = np.argsort(-counts)[:max_swatches]
    swatches: list[Swatch] = []
    for idx in order:
        # Representative pixel = the brightest, most-saturated pixel in
        # the bin so the UI swatch matches the duct *line* color rather
        # than the paler anti-aliased average. The sample coord is the
        # original-image (x, y) of the same pixel, used by the picker
        # to drop a marker on the canvas.
        bin_mask = inverse == idx
        bin_px = px[bin_mask]
        bin_hsv = hsv[bin_mask]
        bin_coords = coords[bin_mask]
        # Score: saturation × value, picks the most-saturated mid-bright sample
        score = bin_hsv[:, 1].astype(np.int32) * bin_hsv[:, 2].astype(np.int32)
        rep = int(np.argmax(score))
        r, g, b = (int(c) for c in bin_px[rep])
        hh, ss, vv = (int(c) for c in bin_hsv[rep])
        sx, sy = (int(c) for c in bin_coords[rep])
        swatches.append(
            Swatch(
                r=r,
                g=g,
                b=b,
                count=int(counts[idx]),
                h=hh,
                s=ss,
                v=vv,
                sample_x=sx,
                sample_y=sy,
            )
        )
    return swatches


@router.post("/render", response_model=RenderResponse)
async def v3_render(
    file: Annotated[UploadFile, File(...)],
    target_text_height_px: Annotated[int, Form()] = 24,
    min_dpi: Annotated[int, Form()] = 200,
    max_dpi: Annotated[int, Form()] = 600,
    ocr: Annotated[OCRExtractor, Depends(build_ocr)] = None,  # type: ignore[assignment]
) -> RenderResponse:
    """Ingest, rotate, render at adaptive DPI; return the page as a PNG.

    The frontend uses this to drive the color picker. Coordinates the
    user clicks on the returned PNG are in the same pixel space the
    detect endpoint will run over, so picks transfer 1:1.
    """
    file_bytes = await file.read()
    if ocr is None:
        ocr = build_ocr()
    ctx = _build_renderer_context(file_bytes, file.filename or "uploaded", ocr)
    target_dpi, png_bytes, w, h, pil = _render_full_page(
        ctx,
        target_text_height_px=target_text_height_px,
        min_dpi=min_dpi,
        max_dpi=max_dpi,
    )
    smallest = ctx.ocr_cache.smallest_text_height_px_p5 if ctx.ocr_cache else None
    rotation = int(ctx.source.rotation_applied) if ctx.source else 0
    swatches = _extract_swatches(pil)
    return RenderResponse(
        drawing_id=ctx.drawing_id,
        width_px=w,
        height_px=h,
        target_dpi=target_dpi,
        rotation_applied=rotation,
        rendered_png_base64=base64.b64encode(png_bytes).decode("ascii"),
        smallest_text_height_px_p5=smallest,
        swatches=swatches,
        errors=list(ctx.errors),
    )


# ── /v3/detect ──────────────────────────────────────────────────────────────


class DetectResponse(BaseModel):
    result: dict
    # The rendered page used by the pipeline. Frontends stack the overlay
    # on top so a grayscale toggle on the page underneath doesn't desaturate
    # the colored mask + segment markers.
    page_png_base64: str | None
    overlay_png_base64: str | None
    errors: list[str]


def _picks_to_config(picks_payload: list[PickPayload]) -> V3PipelineConfig:
    picks: list[ColorPick] = []
    for i, p in enumerate(picks_payload):
        primary = HSVRange(
            lo=(p.primary.h_lo, p.primary.s_lo, p.primary.v_lo),
            hi=(p.primary.h_hi, p.primary.s_hi, p.primary.v_hi),
        )
        secondary: HSVRange | None = None
        if p.second is not None:
            secondary = HSVRange(
                lo=(p.second.h_lo, p.second.s_lo, p.second.v_lo),
                hi=(p.second.h_hi, p.second.s_hi, p.second.v_hi),
            )
        picks.append(
            ColorPick(
                label=p.label,
                primary_range=primary,
                second_range=secondary,
                pattern=p.pattern,
                kind=p.kind,
                display_color_bgr=tuple(p.display_color_bgr),
                system_id=p.system_id or f"sys_{i:02d}",
            )
        )
    return V3PipelineConfig(picks=picks)


def _result_to_dict(result: V3Result) -> dict[str, Any]:
    """Same JSON shape as scripts/run_v3.py — kept in one place to make the CLI
    and the API output isomorphic.
    """
    return {
        "drawing_id": result.drawing_id,
        "width_px": result.width_px,
        "height_px": result.height_px,
        "rotation_applied": result.rotation_applied,
        "page_unit": result.page_unit,
        "ppu": result.ppu,
        "target_dpi": result.target_dpi,
        "rendered_size": list(result.rendered_size),
        "systems": [asdict(s) for s in result.systems],
        "segments": [
            {
                **{k: v for k, v in asdict(seg).items() if k != "pressure" and k != "skel_xy"},
                "skel_xy": list(seg.skel_xy),
                "pressure": asdict(seg.pressure),
            }
            for seg in result.segments
        ],
        "n_tokens_total": result.n_tokens_total,
        "n_dim_rect_tokens": result.n_dim_rect_tokens,
        "n_flow_tokens": result.n_flow_tokens,
        "n_attributed_rect": result.n_attributed_rect,
        "n_attributed_flow": result.n_attributed_flow,
        "calibration": asdict(result.calibration),
        "errors": result.errors,
    }


@router.post("/detect", response_model=DetectResponse)
async def v3_detect(
    file: Annotated[UploadFile, File(...)],
    picks_json: Annotated[str, Form(description="JSON-encoded list[PickPayload]")],
    ocr: Annotated[OCRExtractor, Depends(build_ocr)] = None,  # type: ignore[assignment]
) -> DetectResponse:
    """Run V3 pipeline: source bytes + picks → result + overlay PNG."""
    file_bytes = await file.read()
    if ocr is None:
        ocr = build_ocr()
    try:
        picks_raw = json.loads(picks_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"picks_json malformed: {exc}") from exc
    if not isinstance(picks_raw, list):
        raise HTTPException(status_code=400, detail="picks_json must be a list")
    try:
        picks = [PickPayload(**item) for item in picks_raw]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"picks_json item invalid: {exc}") from exc

    if not picks:
        raise HTTPException(status_code=400, detail="picks must not be empty")

    config = _picks_to_config(picks)
    pipe = V3DetectionPipeline(ocr=ocr)
    result, artifacts = pipe.run_with_artifacts(
        file_bytes,
        file.filename or "uploaded",
        config,
    )

    overlay_b64: str | None = None
    page_b64: str | None = None
    if artifacts is not None:
        overlay_rgba = render_overlay(artifacts.rendered_bgr, artifacts.system_masks)
        ok, png = cv2.imencode(".png", overlay_rgba)
        if ok:
            overlay_b64 = base64.b64encode(png.tobytes()).decode("ascii")
        ok, page_png = cv2.imencode(".png", artifacts.rendered_bgr)
        if ok:
            page_b64 = base64.b64encode(page_png.tobytes()).decode("ascii")

    return DetectResponse(
        result=_result_to_dict(result),
        page_png_base64=page_b64,
        overlay_png_base64=overlay_b64,
        errors=result.errors,
    )


# ── Samples (mirror of agent's /samples for the V3 frontend) ────────────────


@router.get("/samples")
def list_samples() -> list[dict]:
    if not _SAMPLES_DIR.exists():
        return []
    return sorted(
        (
            {"name": p.name, "size_bytes": p.stat().st_size}
            for p in _SAMPLES_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in _SAMPLE_EXTENSIONS
        ),
        key=lambda s: s["name"],
    )


@router.get("/samples/{name}")
def get_sample(name: str) -> FileResponse:
    candidate = (_SAMPLES_DIR / name).resolve()
    if _SAMPLES_DIR.resolve() not in candidate.parents and candidate != _SAMPLES_DIR.resolve():
        raise HTTPException(status_code=400, detail="invalid sample name")
    if not candidate.exists() or candidate.suffix.lower() not in _SAMPLE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="sample not found")
    return FileResponse(candidate, filename=candidate.name)
