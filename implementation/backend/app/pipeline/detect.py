"""Stage 4 — Duct detection (SOLUTION-DESIGN §4 row 4, ADR-0001, ADR-0003).

The only agent stage in the default path. One VLM call returns coarse bboxes
through `DetectDuctsTool`; OpenCV refines the geometry to a polyline. If the
VLM call fails, we degrade to a CV-only candidate sweep and surface a warning
so the UI can render the "geometry-only mode" banner (§9).
"""

from __future__ import annotations

from app.cv.ducts import find_duct_candidates_cv, refine_segment_geometry
from app.pipeline.base import (
    PipelineContext,
    PipelineStage,
    VLMSegmentDraft,
)
from app.schemas import Geometry, ReasoningStep
from app.vlm.base import VLMClient, VLMError
from app.vlm.ollama import normalize_to_pixels


class DuctDetectionStage(PipelineStage):
    name = "duct_detection"

    def __init__(self, vlm: VLMClient) -> None:
        self._vlm = vlm

    def run(self, ctx: PipelineContext) -> PipelineContext:
        assert ctx.source is not None

        try:
            response = self._vlm.detect(ctx.source.raster_probe)
            ctx.segments_draft = self._build_drafts_from_vlm(ctx, response.segments)
        except VLMError as exc:
            ctx.errors.append(f"duct_detection: VLM unavailable ({exc}); CV-only fallback")
            ctx.segments_draft = self._build_drafts_from_cv(ctx)

        return ctx

    # ── VLM path ─────────────────────────────────────────────────────────────

    def _build_drafts_from_vlm(self, ctx: PipelineContext, segments) -> list[VLMSegmentDraft]:
        assert ctx.source is not None
        image = ctx.source.raster_probe
        bboxes = normalize_to_pixels(segments, ctx.width_px, ctx.height_px)

        drafts: list[VLMSegmentDraft] = []
        for index, (segment, bbox) in enumerate(zip(segments, bboxes, strict=False)):
            polyline = refine_segment_geometry(
                image, bbox, shape_hint=segment.shape_hint
            )
            geometry = (
                Geometry(type="polyline", points=polyline)
                if segment.shape_hint != "round"
                else Geometry(
                    type="bbox",
                    points=[
                        (float(bbox[0]), float(bbox[1])),
                        (float(bbox[0] + bbox[2]), float(bbox[1] + bbox[3])),
                    ],
                )
            )
            drafts.append(
                VLMSegmentDraft(
                    segment_id=_segment_id(index),
                    geometry=geometry,
                    shape_hint=segment.shape_hint,
                    nearby_text=segment.nearby_text,
                    reasoning_trace=[
                        ReasoningStep(
                            stage="vlm_detect",
                            evidence=(
                                f"VLM identified a {segment.shape_hint} duct at "
                                f"bbox {bbox}; geometry refined by HoughLinesP"
                            ),
                        )
                    ],
                )
            )
        return drafts

    # ── CV-only fallback ─────────────────────────────────────────────────────

    def _build_drafts_from_cv(self, ctx: PipelineContext) -> list[VLMSegmentDraft]:
        assert ctx.source is not None
        image = ctx.source.raster_probe
        candidates = find_duct_candidates_cv(image)
        drafts: list[VLMSegmentDraft] = []
        for index, bbox in enumerate(candidates):
            polyline = refine_segment_geometry(
                image, bbox, shape_hint="rectangular"
            )
            drafts.append(
                VLMSegmentDraft(
                    segment_id=_segment_id(index),
                    geometry=Geometry(type="polyline", points=polyline),
                    shape_hint="unknown",
                    nearby_text=[],
                    reasoning_trace=[
                        ReasoningStep(
                            stage="cv_fallback",
                            evidence=(
                                "VLM unavailable; geometry sourced from "
                                "HoughLinesP parallel-pair sweep"
                            ),
                        )
                    ],
                )
            )
        return drafts


def _segment_id(index: int) -> str:
    return f"D-{index + 1:03d}"
