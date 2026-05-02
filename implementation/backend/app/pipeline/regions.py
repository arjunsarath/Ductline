"""Stage 3 — Region detect (SOLUTION-DESIGN §4 row 3).

Classical CV first; VLM fallback only if the classical pass fails to find a
title block. The schedule is best-effort — its absence just means stage 6
tier 2 (schedule lookup) won't fire for any segment.
"""

from __future__ import annotations

from app.cv.regions import find_schedule, find_title_block
from app.pipeline.base import PipelineContext, PipelineStage
from app.vlm.base import VLMClient, VLMError


class RegionDetectStage(PipelineStage):
    name = "region_detect"

    def __init__(self, vlm: VLMClient) -> None:
        self._vlm = vlm

    def run(self, ctx: PipelineContext) -> PipelineContext:
        assert ctx.source is not None
        image = ctx.source.raster_probe

        title = find_title_block(image)
        if title is None:
            title = self._vlm_fallback(ctx)
            if title is not None:
                ctx.errors.append("region_detect: classical pass failed; VLM fallback used")

        ctx.title_block_bbox = title
        ctx.schedule_bbox = find_schedule(image, near=title)
        return ctx

    def _vlm_fallback(self, ctx: PipelineContext) -> tuple[int, int, int, int] | None:
        """Best-effort VLM disambiguation. We don't fail the pipeline if it returns
        garbage — title block absence just means region context is missing.
        """
        assert ctx.source is not None
        try:
            response = self._vlm.disambiguate_region(
                ctx.source.raster_probe,
                "Where is the title block on this drawing? Respond with normalized "
                "[x_min, y_min, x_max, y_max] in [0,1] coordinates as a JSON array. "
                "If you don't see one, respond with 'none'.",
            )
        except VLMError:
            return None
        return _parse_normalized_bbox(response, ctx.width_px, ctx.height_px)


def _parse_normalized_bbox(
    raw: str, width_px: int, height_px: int
) -> tuple[int, int, int, int] | None:
    import json

    raw = raw.strip()
    if not raw or raw.lower() == "none":
        return None
    try:
        coords = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(coords, list) or len(coords) != 4:
        return None

    try:
        x_min, y_min, x_max, y_max = (float(c) for c in coords)
    except (TypeError, ValueError):
        return None

    return (
        int(x_min * width_px),
        int(y_min * height_px),
        int(max(x_max - x_min, 0.001) * width_px),
        int(max(y_max - y_min, 0.001) * height_px),
    )
