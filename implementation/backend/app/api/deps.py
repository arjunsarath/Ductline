"""Dependency wiring — single pipeline instance shared across requests.

VLM and OCR clients are expensive to initialize (the OCR engine downloads
~50 MB of model files on first call). We construct them once at import time
so every request reuses them.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.ocr.rapid import RapidOCRExtractor
from app.pipeline.runner import DetectionPipeline
from app.vlm.factory import build_vlm_client


@lru_cache(maxsize=1)
def get_pipeline() -> DetectionPipeline:
    return DetectionPipeline(
        vlm=build_vlm_client(settings),
        ocr=RapidOCRExtractor(),
    )


@lru_cache(maxsize=1)
def build_ocr() -> RapidOCRExtractor:
    """Process-singleton OCR engine, shared across V3 requests.

    The first call lazy-loads the ONNX models (~50 MB on disk after the
    initial download). Caching avoids re-walking that path per request,
    which made the browser-driven /v3/render flow appear stuck for
    10–20 seconds on each call.
    """
    return RapidOCRExtractor()
