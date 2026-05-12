from __future__ import annotations

import json
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from extractor import extract_pdf
from preprocess import build_preprocessed_svg
from scale_detector import detect_scale_callouts

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="Ductline PDF Extractor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Element(BaseModel):
    id: str
    type: Literal[
        "line",
        "rect",
        "rect_curve",
        "rect_partial",
        "inferred_rect",
        "curve",
        "char",
        "word",
    ]
    x0: float | None = None
    top: float | None = None
    x1: float | None = None
    bottom: float | None = None
    linewidth: float | None = None
    stroke: str | None = None
    fill: str | None = None
    points: list[list[float]] | None = None
    text: str | None = None
    fontname: str | None = None
    size: float | None = None

    model_config = {"extra": "allow"}


class Page(BaseModel):
    page_number: int
    width: float
    height: float
    elements: list[Element]


class ExtractResponse(BaseModel):
    filename: str
    page_count: int
    pages: list[Page]


class BBoxModel(BaseModel):
    x0: float
    top: float
    x1: float
    bottom: float


class WallSegment(BaseModel):
    x0: float
    top: float
    x1: float
    bottom: float


class WallPair(BaseModel):
    a: WallSegment
    b: WallSegment
    distance_pts: float


class CalloutResult(BaseModel):
    id: str
    text: str
    diameter_in: float
    raw_text: str
    confidence: int
    bbox: BBoxModel
    enclosing_rect: BBoxModel | None = None
    duct_bbox: BBoxModel | None = None
    drawn_diameter_pts: float | None = None
    scale_pts_per_inch: float | None = None
    wall_pairs: list[WallPair] = []


class ScaleResponse(BaseModel):
    page_number: int
    dpi: int
    callouts: list[CalloutResult]
    drawing_scale_pts_per_inch: float | None = None
    callout_count: int


def _parse_crop(raw: str | None) -> dict[int, tuple[float, float, float, float]] | None:
    if not raw:
        return None
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid crop JSON: {e}")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="crop must be a JSON array")

    out: dict[int, tuple[float, float, float, float]] = {}
    for entry in items:
        try:
            page = int(entry["page"])
            x0 = float(entry["x0"])
            top = float(entry["top"])
            x1 = float(entry["x1"])
            bottom = float(entry["bottom"])
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid crop entry: {e}")
        if x1 <= x0 or bottom <= top:
            raise HTTPException(status_code=400, detail="crop bbox must have x1>x0 and bottom>top")
        out[page] = (x0, top, x1, bottom)
    return out or None


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/extract", response_model=ExtractResponse)
async def extract(
    file: UploadFile = File(...),
    crop: str | None = Form(default=None),
) -> ExtractResponse:
    data = await file.read()

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")

    # Trust the magic bytes over the client-supplied content-type.
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File is not a valid PDF")

    crops = _parse_crop(crop)

    try:
        result = extract_pdf(data, file.filename or "upload.pdf", crops)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")

    return ExtractResponse(**result)


def _parse_single_crop(raw: str) -> tuple[float, float, float, float]:
    try:
        obj = json.loads(raw)
        x0 = float(obj["x0"])
        top = float(obj["top"])
        x1 = float(obj["x1"])
        bottom = float(obj["bottom"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid crop: {e}")
    if x1 <= x0 or bottom <= top:
        raise HTTPException(status_code=400, detail="crop bbox must have x1>x0 and bottom>top")
    return x0, top, x1, bottom


@app.post("/api/preprocess")
async def preprocess(
    file: UploadFile = File(...),
    page_number: int = Form(...),
    crop: str = Form(...),
    black_threshold: float = Form(default=0.02),
) -> Response:
    """Return a single-page PDF showing only the masked crop, binarised to
    pure black/white at the requested threshold. Used by the viewer as the
    debug display source instead of the original PDF."""
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File is not a valid PDF")
    if page_number < 1:
        raise HTTPException(status_code=400, detail="page_number must be >= 1")
    if not 0.0 <= black_threshold <= 1.0:
        raise HTTPException(status_code=400, detail="black_threshold must be in [0, 1]")

    crop_bbox = _parse_single_crop(crop)

    try:
        svg = build_preprocessed_svg(data, page_number, crop_bbox, black_threshold)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to preprocess: {e}")

    return Response(content=svg, media_type="image/svg+xml")


@app.post("/api/detect-scale", response_model=ScaleResponse)
async def detect_scale(
    file: UploadFile = File(...),
    page_number: int = Form(...),
    crop: str = Form(...),
    black_threshold: float = Form(default=0.02),
) -> ScaleResponse:
    data = await file.read()

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File is not a valid PDF")
    if page_number < 1:
        raise HTTPException(status_code=400, detail="page_number must be >= 1")
    if not 0.0 <= black_threshold <= 1.0:
        raise HTTPException(status_code=400, detail="black_threshold must be in [0, 1]")

    crop_bbox = _parse_single_crop(crop)

    try:
        result = detect_scale_callouts(
            data, page_number, crop_bbox, black_threshold=black_threshold
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to detect scale: {e}")

    return ScaleResponse(**result)
