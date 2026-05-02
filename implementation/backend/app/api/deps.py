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
