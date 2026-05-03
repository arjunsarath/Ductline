"""Ollama implementation of VLMClient (ADR-0002).

llama3.2-vision via host Ollama, JSON-mode output. We don't use Ollama's
function-calling API because vision-capable models in Ollama often don't honor
tool schemas reliably — JSON mode + Pydantic validation is the predictable path.

Drawings rendered at 200 DPI are large; we downscale to a long-edge cap before
the call (vision models have native input resolutions and we waste tokens
otherwise). Returned normalized coords are remapped back to original-resolution
pixel space before they leave this module — stage 4 only ever sees absolute coords.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from io import BytesIO
from pathlib import Path
from statistics import mean, pstdev
from typing import TYPE_CHECKING

import httpx
from PIL.Image import Image
from pydantic import ValidationError

from app.vlm.base import VLMClient, VLMError
from app.vlm.reviewer import ReviewerVerdict
from app.vlm.tools import (
    CategorizePageTool,
    DetectDuctsTool,
    DetectionResult,
    LegendRegionTool,
    NotesRegionTool,
    PageRegionsTool,
    PlanViewTool,
    RefineSegmentTool,
    ReviewSegmentTool,
    ScheduleTool,
    TitleBlockTool,
    VLMSegment,
)

if TYPE_CHECKING:
    from app.pipeline.base import VLMSegmentDraft
    from app.pipeline.legend import Legend

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_VLM_LONG_EDGE_PX = 1568  # llama3.2-vision native input edge
_OLLAMA_TIMEOUT_S = 120.0

# Inline prompt — short enough that a separate prompt file would cost more in
# indirection than it saves. Mirrors the categorizer rectangle taxonomy from
# SOLUTION-DESIGN-V2 §5.3.
_CATEGORIZE_PROMPT = (
    "You are looking at one rectangular region of an HVAC mechanical drawing. "
    "Classify the region as exactly one of: title_block, schedule, legend, notes, "
    "plan_view, section_detail, unknown. Respond with JSON of the form "
    '{"region_kind": "<one_of_the_kinds>"} and nothing else.'
)


class OllamaVisionClient(VLMClient):
    def __init__(self, host_url: str, model: str) -> None:
        self._host_url = host_url.rstrip("/")
        self._model = model

    def detect(self, image: Image, *, prompt_version: str = "v2") -> DetectionResult:
        prompt = _load_prompt(prompt_version)
        downscaled, _ = _downscale_for_vlm(image, _VLM_LONG_EDGE_PX)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(downscaled)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }

        t0 = time.monotonic()
        logger.info(
            "vlm.detect: start prompt=%s image=%dx%d",
            prompt_version,
            downscaled.width,
            downscaled.height,
        )
        raw_response = self._post("/api/generate", payload).get("response", "")
        tool = _parse_tool_call(raw_response)
        logger.info(
            "vlm.detect: done prompt=%s segments=%d response_len=%d elapsed=%.2fs",
            prompt_version,
            len(tool.segments),
            len(raw_response),
            time.monotonic() - t0,
        )

        # The model saw a downscaled image but we want coords in the original
        # space. Bbox values are normalized [0, 1] so the conversion is trivial:
        # callers (stage 4) multiply by original width/height.
        return DetectionResult(prompt_version=prompt_version, segments=tool.segments)

    def disambiguate_region(self, crop: Image, question: str) -> str:
        payload = {
            "model": self._model,
            "prompt": question,
            "images": [_encode_png_b64(crop)],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        return self._post("/api/generate", payload).get("response", "").strip()

    def detect_tile(
        self,
        crop: Image,
        *,
        tile_position: tuple[int, int, int, int],
        trail_context: list[dict],
        legend: Legend | None,
    ) -> DetectionResult:
        """Per-tile detect (SOLUTION-DESIGN-V2 §5.5, ADR-0008).

        Reads the v3 tiled prompt, substitutes legend / tile-position / trail
        blocks, and posts the tile crop to Ollama. Tiles are already sized to
        the model's native window (~1100 px) so we bypass the v1 long-edge
        downscale — re-downscaling here would discard the small-text gain
        that motivates tiling in the first place.
        """
        prompt = _render_tile_prompt(tile_position, trail_context, legend)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(crop)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        row, col, total_rows, total_cols = tile_position
        t0 = time.monotonic()
        logger.info(
            "vlm.detect_tile: start tile=(%d,%d)/(%d,%d) crop=%dx%d trail=%d legend=%s",
            row, col, total_rows, total_cols,
            crop.width, crop.height,
            len(trail_context),
            "yes" if legend is not None else "no",
        )
        raw_response = self._post("/api/generate", payload).get("response", "")
        tool = _parse_tool_call(raw_response)
        logger.info(
            "vlm.detect_tile: done tile=(%d,%d) segments=%d sample_bboxes=%s elapsed=%.2fs",
            row, col,
            len(tool.segments),
            _format_bbox_sample(tool.segments),
            time.monotonic() - t0,
        )
        return DetectionResult(prompt_version="v3_tiled", segments=tool.segments)

    def categorize_region(self, crop: Image) -> CategorizePageTool:
        """Page Categorizer VLM fallback (SOLUTION-DESIGN-V2 §5.3).

        Same JSON-mode posture as ``detect``: prompt for a tiny typed payload,
        validate with Pydantic, surface schema failures as VLMError. The model
        sees one rectangle of the drawing and must place it in one of seven
        kinds — no prose, no confidence score.
        """
        payload = {
            "model": self._model,
            "prompt": _CATEGORIZE_PROMPT,
            "images": [_encode_png_b64(crop)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM categorize_region")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM categorize JSON invalid: {exc}") from exc
        try:
            tool = CategorizePageTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM categorize JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.categorize_region: kind=%s crop=%dx%d elapsed=%.2fs",
            tool.region_kind,
            crop.width,
            crop.height,
            time.monotonic() - t0,
        )
        return tool

    def detect_page_regions(self, image: Image) -> PageRegionsTool:
        """VLM-first page categorization (SOLUTION-DESIGN-V2 §5.3).

        Single whole-page call: the model sees the full sheet (downscaled to
        the model's native input window) and emits one normalized bbox per
        major region. Same JSON-mode + Pydantic-validate posture as
        ``categorize_region``; schema failures surface as VLMError so the
        calling stage can fall back to the heuristic path.
        """
        prompt = _load_model_specific_prompt("detect_page_regions.txt", self._model)

        downscaled, _ = _downscale_for_vlm(image, _VLM_LONG_EDGE_PX)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(downscaled)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        logger.info(
            "vlm.detect_page_regions: start image=%dx%d",
            downscaled.width,
            downscaled.height,
        )
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM detect_page_regions")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM page-regions JSON invalid: {exc}") from exc
        try:
            tool = PageRegionsTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM page-regions JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.detect_page_regions: done plan_view=%s legend=%d schedule=%s "
            "title_block=%s notes=%d elapsed=%.2fs",
            tool.plan_view is not None,
            len(tool.legend),
            tool.schedule is not None,
            tool.title_block is not None,
            len(tool.notes),
            time.monotonic() - t0,
        )
        return tool

    def detect_plan_view(self, image: Image) -> PlanViewTool:
        """Focused plan-view detection (SOLUTION-DESIGN-V2 §5.3).

        Single-question prompt asking only for the plan-view bbox. Same
        JSON-mode + Pydantic-validate posture as ``categorize_region``;
        schema failures surface as VLMError so the calling stage can fall
        back to the heuristic path. Replaces the multi-region call from
        the VLM-first path because small VLMs handle one focused
        question better than five disambiguated ones.
        """
        prompt = _load_model_specific_prompt("detect_plan_view.txt", self._model)

        downscaled, _ = _downscale_for_vlm(image, _VLM_LONG_EDGE_PX)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(downscaled)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        logger.info(
            "vlm.detect_plan_view: start image=%dx%d",
            downscaled.width,
            downscaled.height,
        )
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM detect_plan_view")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM plan_view JSON invalid: {exc}") from exc
        try:
            tool = PlanViewTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM plan_view JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.detect_plan_view: done bbox=%s elapsed=%.2fs",
            tool.bbox,
            time.monotonic() - t0,
        )
        return tool

    def detect_legend(self, image: Image) -> LegendRegionTool:
        """Focused legend detection (SOLUTION-DESIGN-V2 §5.3).

        Same posture as ``detect_plan_view`` but allows multiple bboxes —
        legends are commonly split into symbol box + abbreviation table.
        The calling stage unions them. An empty list is the "no legend
        on this drawing" outcome and is non-failure.
        """
        prompt = _load_model_specific_prompt("detect_legend.txt", self._model)

        downscaled, _ = _downscale_for_vlm(image, _VLM_LONG_EDGE_PX)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(downscaled)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        logger.info(
            "vlm.detect_legend: start image=%dx%d",
            downscaled.width,
            downscaled.height,
        )
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM detect_legend")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM legend JSON invalid: {exc}") from exc
        try:
            tool = LegendRegionTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM legend JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.detect_legend: done bboxes=%d elapsed=%.2fs",
            len(tool.bboxes),
            time.monotonic() - t0,
        )
        return tool

    def detect_title_block(self, image: Image) -> TitleBlockTool:
        """Focused title-block detection (SOLUTION-DESIGN-V2 §5.3).

        Same posture as ``detect_plan_view``: single-question prompt,
        JSON-mode + Pydantic-validate, schema failures surface as
        VLMError. Used by the auxiliary-first VLM-first path (third
        revision of §5.3) — plan_view is derived from the page rect
        minus the auxiliaries, so this call's job is just to localise
        the title banner / metadata box.
        """
        prompt = _load_model_specific_prompt("detect_title_block.txt", self._model)

        downscaled, _ = _downscale_for_vlm(image, _VLM_LONG_EDGE_PX)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(downscaled)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        logger.info(
            "vlm.detect_title_block: start image=%dx%d",
            downscaled.width,
            downscaled.height,
        )
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM detect_title_block")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM title_block JSON invalid: {exc}") from exc
        try:
            tool = TitleBlockTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM title_block JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.detect_title_block: done bbox=%s elapsed=%.2fs",
            tool.bbox,
            time.monotonic() - t0,
        )
        return tool

    def detect_notes(self, image: Image) -> NotesRegionTool:
        """Focused notes-region detection (SOLUTION-DESIGN-V2 §5.3).

        Same posture as ``detect_legend`` — multi-bbox shape lets a
        drawing return non-adjacent notes columns separately. Empty
        list is "no notes on this drawing" and is non-failure.
        """
        prompt = _load_model_specific_prompt("detect_notes.txt", self._model)

        downscaled, _ = _downscale_for_vlm(image, _VLM_LONG_EDGE_PX)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(downscaled)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        logger.info(
            "vlm.detect_notes: start image=%dx%d",
            downscaled.width,
            downscaled.height,
        )
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM detect_notes")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM notes JSON invalid: {exc}") from exc
        try:
            tool = NotesRegionTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM notes JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.detect_notes: done bboxes=%d elapsed=%.2fs",
            len(tool.bboxes),
            time.monotonic() - t0,
        )
        return tool

    def detect_schedule(self, image: Image) -> ScheduleTool:
        """Focused schedule-region detection (SOLUTION-DESIGN-V2 §5.3).

        Same posture as ``detect_plan_view``: single-bbox payload,
        JSON-mode + Pydantic-validate, schema failures surface as
        VLMError. ``None`` is the legitimate "no schedule visible"
        answer.
        """
        prompt = _load_model_specific_prompt("detect_schedule.txt", self._model)

        downscaled, _ = _downscale_for_vlm(image, _VLM_LONG_EDGE_PX)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(downscaled)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        logger.info(
            "vlm.detect_schedule: start image=%dx%d",
            downscaled.width,
            downscaled.height,
        )
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM detect_schedule")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM schedule JSON invalid: {exc}") from exc
        try:
            tool = ScheduleTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM schedule JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.detect_schedule: done bbox=%s elapsed=%.2fs",
            tool.bbox,
            time.monotonic() - t0,
        )
        return tool

    def review_segment(
        self,
        crop: Image,
        segment: VLMSegmentDraft,
        legend: Legend | None,
    ) -> ReviewerVerdict:
        """Reviewer call (SOLUTION-DESIGN-V2 §5.6, ADR-0009).

        Same JSON-mode posture as ``detect`` and ``categorize_region``: prompt
        for a tiny typed payload, validate with Pydantic, surface schema
        failures as VLMError. Discrete verdict only — no continuous scores.
        The calling stage (``ReviewerStage``) handles per-segment exceptions
        as "this segment stays not_reviewed", so we let validation errors
        bubble up here.
        """
        prompt = _render_review_prompt(segment, legend)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(crop)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM review_segment")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM review JSON invalid: {exc}") from exc
        try:
            verdict = ReviewSegmentTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM review JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.review_segment: id=%s verdict=%s reason=%r elapsed=%.2fs",
            segment.segment_id,
            verdict.verdict,
            verdict.reason[:80],
            time.monotonic() - t0,
        )
        return verdict

    def refine_segment(
        self,
        crop: Image,
        *,
        critique: str,
        previous: VLMSegmentDraft,
    ) -> RefineSegmentTool:
        """Refinement call (SOLUTION-DESIGN-V2 §5.6, ADR-0009).

        Reads the refine_segment prompt template, substitutes critique +
        previous-detection blocks, posts the crop. Output coords are in the
        crop's own [0, 1] frame; the calling stage projects back to source
        space (mirrors the per-tile projection in detect_tiled).
        """
        prompt = _render_refine_prompt(critique, previous)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [_encode_png_b64(crop)],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        t0 = time.monotonic()
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM refine_segment")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM refine JSON invalid: {exc}") from exc
        try:
            refined = RefineSegmentTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM refine JSON failed schema: {exc.error_count()} errors"
            ) from exc
        logger.info(
            "vlm.refine_segment: id=%s bbox=%s shape=%s note=%r elapsed=%.2fs",
            previous.segment_id,
            tuple(round(v, 3) for v in refined.bbox_normalized),
            refined.shape_hint,
            refined.note[:60],
            time.monotonic() - t0,
        )
        return refined

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._host_url}{path}"
        try:
            with httpx.Client(timeout=_OLLAMA_TIMEOUT_S) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise VLMError(f"Ollama request failed: {exc}") from exc


# ── Pure helpers (testable without a live Ollama). ───────────────────────────


def _load_prompt(version: str) -> str:
    path = _PROMPTS_DIR / f"detect_{version}.md"
    if not path.exists():
        raise VLMError(f"prompt version not found: {version}")
    return path.read_text(encoding="utf-8")


def _load_model_specific_prompt(filename: str, model: str) -> str:
    """Load a prompt file, preferring a model-specific variant if present.

    Resolution order:
      1. ``prompts/{model_slug}/{filename}`` — per-VLM tuned variant.
      2. ``prompts/{filename}`` — default fallback shared across models.

    The model slug is the model name with ``:tag`` stripped (e.g.
    ``llama3.2-vision:latest`` → ``llama3.2-vision``). Different VLMs
    have very different sweet spots — small local models like
    llama3.2-vision want short, abstract, schema-only prompts; larger
    hosted models can handle longer instructions and chain-of-thought.
    Per-VLM versioning lets us refine each one independently without
    cross-model regression risk.

    Raises ``VLMError`` if neither variant exists.
    """
    model_slug = model.split(":")[0]
    specific = _PROMPTS_DIR / model_slug / filename
    if specific.exists():
        return specific.read_text(encoding="utf-8")
    default = _PROMPTS_DIR / filename
    if not default.exists():
        raise VLMError(
            f"prompt missing: {filename} (no model-specific variant for '{model_slug}', "
            f"no default at {default})"
        )
    return default.read_text(encoding="utf-8")


def _render_tile_prompt(
    tile_position: tuple[int, int, int, int],
    trail_context: list[dict],
    legend: Legend | None,
) -> str:
    """Inject legend / tile-position / trail blocks into the v3 tile template.

    The template lives in ``prompts/detect_v3_tiled.txt`` (not ``.md``) so the
    legacy ``_load_prompt`` lookup never picks it up by accident — tile prompts
    are templated, not version-pinned.
    """
    template_path = _PROMPTS_DIR / "detect_v3_tiled.txt"
    if not template_path.exists():
        raise VLMError("tile prompt template missing: detect_v3_tiled.txt")
    template = template_path.read_text(encoding="utf-8")

    legend_block = _format_legend_block(legend)
    tile_position_block = (
        f"(row {tile_position[0]}, col {tile_position[1]}) "
        f"of ({tile_position[2]}, {tile_position[3]})"
    )
    trail_block = _format_trail_block(trail_context)

    return (
        template
        .replace("{LEGEND_BLOCK}", legend_block)
        .replace("{TILE_POSITION_BLOCK}", tile_position_block)
        .replace("{TRAIL_CONTEXT_BLOCK}", trail_block)
    )


def _render_review_prompt(
    segment: VLMSegmentDraft, legend: Legend | None
) -> str:
    """Inject segment + legend blocks into the review_segment template.

    Same templating posture as ``_render_tile_prompt`` — keeps prompt files
    out of the version-pinned ``detect_*.md`` lookup. Reviewer prompts are
    templated, not version-pinned.
    """
    template_path = _PROMPTS_DIR / "review_segment.txt"
    if not template_path.exists():
        raise VLMError("review prompt template missing: review_segment.txt")
    template = template_path.read_text(encoding="utf-8")

    return (
        template
        .replace("{SEGMENT_CONTEXT_BLOCK}", _format_segment_context(segment))
        .replace("{LEGEND_BLOCK}", _format_legend_block(legend))
    )


def _render_refine_prompt(critique: str, previous: VLMSegmentDraft) -> str:
    """Inject critique + previous-detection blocks into the refine template."""
    template_path = _PROMPTS_DIR / "refine_segment.txt"
    if not template_path.exists():
        raise VLMError("refine prompt template missing: refine_segment.txt")
    template = template_path.read_text(encoding="utf-8")

    critique_block = critique.strip() or "(no critique provided)"
    return (
        template
        .replace("{CRITIQUE_BLOCK}", critique_block)
        .replace("{PREVIOUS_BLOCK}", _format_segment_context(previous))
    )


def _format_segment_context(segment: VLMSegmentDraft) -> str:
    """Render a draft segment as a short, readable block for the prompt.

    The reviewer cares about shape_hint and nearby_text for the domain-prior
    checks; geometry is rendered as the source-space rect so the agent can
    cross-reference what it sees in the crop with where the box came from.
    """
    pts = segment.geometry.points
    if len(pts) >= 2:
        (x0, y0), (x1, y1) = pts[0], pts[1]
        bbox_str = f"({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})"
    else:
        bbox_str = "(unknown)"
    nearby = ", ".join(segment.nearby_text) if segment.nearby_text else "(none)"
    return (
        f"id: {segment.segment_id}\n"
        f"bbox (source coords): {bbox_str}\n"
        f"shape_hint: {segment.shape_hint}\n"
        f"nearby_text: {nearby}"
    )


def _format_legend_block(legend: Legend | None) -> str:
    """Return the LEGEND CONTEXT prompt block, or empty when no legend exists.

    Empty (rather than a placeholder header) when legend is None — the trail
    and tile-position blocks are still useful, but a header without a body
    invites the model to fabricate legend entries.
    """
    if legend is None:
        return ""
    lines: list[str] = ["LEGEND CONTEXT (drawing-specific conventions)"]
    if legend.line_styles:
        lines.append("Line styles:")
        for k, v in legend.line_styles.items():
            lines.append(f"  {k} = {v}")
    if legend.symbols:
        lines.append("Symbols:")
        for k, v in legend.symbols.items():
            lines.append(f"  {k} = {v}")
    if legend.abbreviations:
        lines.append("Abbreviations:")
        for k, v in legend.abbreviations.items():
            lines.append(f"  {k} = {v}")
    if legend.units != "unknown":
        lines.append(f"Units: {legend.units}")
    if len(lines) == 1:
        # Header only — don't emit a bare header.
        return ""
    return "\n".join(lines)


def _format_trail_block(trail_context: list[dict]) -> str:
    """Render trail entries as bullet lines; explicit "do not re-detect" instruction.

    Each entry carries a ``bbox_normalized`` already projected into the
    CURRENT tile's coord space (caller's responsibility) and a
    ``shape_hint``. We do not surface segment_ids — the model only needs to
    know "this region was already taken" to avoid double-counting.
    """
    if not trail_context:
        return "No segments have been detected yet."
    lines = [
        "The following segments were already detected in neighbouring tiles. "
        "Do NOT re-detect them — they are listed in this tile's coord space "
        "for reference only:"
    ]
    for entry in trail_context:
        bbox = entry.get("bbox_normalized")
        shape = entry.get("shape_hint", "unknown")
        if bbox is None:
            continue
        lines.append(f"  - bbox={list(bbox)} shape={shape}")
    return "\n".join(lines)


def _downscale_for_vlm(image: Image, max_long_edge: int) -> tuple[Image, float]:
    long_edge = max(image.size)
    if long_edge <= max_long_edge:
        return image, 1.0
    scale = max_long_edge / long_edge
    new_size = (int(image.width * scale), int(image.height * scale))
    return image.resize(new_size), scale


def _encode_png_b64(image: Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _parse_tool_call(raw: str) -> DetectDuctsTool:
    if not raw:
        raise VLMError("empty response from VLM")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VLMError(f"VLM did not return valid JSON: {exc}") from exc

    # Some models wrap the payload in additional keys; tolerate that as long
    # as a `segments` field exists somewhere obvious.
    if isinstance(data, dict) and "segments" not in data:
        for value in data.values():
            if isinstance(value, dict) and "segments" in value:
                data = value
                break

    try:
        tool = DetectDuctsTool.model_validate(data)
    except ValidationError as exc:
        # Pydantic's full error dump is multi-paragraph; the surfaced message
        # ends up in `errors[]` on the API response so we collapse it.
        missing = sorted({str(err["loc"][-1]) for err in exc.errors() if err["type"] == "missing"})
        if missing:
            raise VLMError(
                f"VLM JSON missing required fields: {', '.join(missing)}"
            ) from exc
        raise VLMError(f"VLM JSON failed schema: {exc.error_count()} errors") from exc

    _reject_if_hallucinated(tool)
    return tool


# Smaller vision models (notably llama3.2-vision 11B) often hallucinate
# bbox responses with four tells: duplicate bboxes, coords confined to a
# tenth-grid like 0.1 / 0.2 / 0.3 …, absurdly long lists, or suspiciously
# uniform bbox dimensions (the column-marker / grid-line failure mode that
# manifests on tile crops covering page header / footer strips). Detecting
# these lets stage 4 / stage 5 fall back to "skip this tile" instead of
# feeding the pipeline garbage.
# Tenth-grid check: a coordinate is "on the tenth grid" when it is essentially
# a multiple of 0.1 (e.g. 0.0, 0.1, 0.2 …). The original implementation used
# ``round(c, 1) in _GRID_VALUES`` which is True for ANY value in [0, 1] —
# every float rounds to some tenth — so the heuristic was effectively a
# no-op against real-valued model output. Fixed: a value is clean-tenth iff
# its distance to its nearest tenth is below ``_TENTH_GRID_TOLERANCE``.
_TENTH_GRID_TOLERANCE = 0.001
_HALLUCINATED_DUPLICATE_THRESHOLD = 0.5  # ≥50% duplicates → reject
_HALLUCINATED_GRID_THRESHOLD = 0.9       # ≥90% on the tenth grid → reject
_HALLUCINATED_COUNT_LIMIT = 80           # > this many segments is implausible
# Uniform-pattern check: if ≥4 segments AND the coefficient of variation
# (stddev/mean) of both width and height is below this, the model is almost
# certainly emitting one box per repeating geometric element rather than
# detecting real ducts.
_HALLUCINATED_UNIFORM_CV_THRESHOLD = 0.10
_HALLUCINATED_UNIFORM_MIN_COUNT = 4


def _reject_if_hallucinated(tool: DetectDuctsTool) -> None:
    segments = tool.segments
    if not segments:
        return
    if len(segments) > _HALLUCINATED_COUNT_LIMIT:
        raise VLMError(f"VLM returned {len(segments)} segments — likely hallucinated")

    bboxes = [tuple(round(v, 3) for v in s.bbox) for s in segments]
    duplicate_fraction = 1 - (len(set(bboxes)) / len(bboxes))
    if duplicate_fraction >= _HALLUCINATED_DUPLICATE_THRESHOLD and len(segments) > 2:
        raise VLMError("VLM returned duplicate bboxes — likely hallucinated")

    on_grid = sum(
        1
        for s in segments
        if all(abs(c - round(c, 1)) < _TENTH_GRID_TOLERANCE for c in s.bbox)
    )
    if len(segments) >= 4 and on_grid / len(segments) >= _HALLUCINATED_GRID_THRESHOLD:
        raise VLMError("VLM bboxes lie on a tenth-grid — likely hallucinated")

    # Uniform-pattern check — catches column-marker / grid-line hallucinations
    # that don't trip the tenth-grid heuristic. Real ducts on a plan have
    # diverse sizes; a row of identical-shaped bboxes is the model copying a
    # repeating visual element it mistook for ducts.
    if len(segments) >= _HALLUCINATED_UNIFORM_MIN_COUNT:
        widths = [abs(s.bbox[2] - s.bbox[0]) for s in segments]
        heights = [abs(s.bbox[3] - s.bbox[1]) for s in segments]
        if mean(widths) > 0 and mean(heights) > 0:
            cv_w = pstdev(widths) / mean(widths)
            cv_h = pstdev(heights) / mean(heights)
            if cv_w < _HALLUCINATED_UNIFORM_CV_THRESHOLD and cv_h < _HALLUCINATED_UNIFORM_CV_THRESHOLD:
                raise VLMError(
                    f"VLM returned {len(segments)} uniformly-shaped bboxes "
                    f"(cv_w={cv_w:.3f}, cv_h={cv_h:.3f}) — likely hallucinated"
                )


def _format_bbox_sample(segments: list[VLMSegment], limit: int = 3) -> str:
    """Render the first ``limit`` segment bboxes for log lines."""
    if not segments:
        return "[]"
    sample = [tuple(round(v, 3) for v in s.bbox) for s in segments[:limit]]
    suffix = "" if len(segments) <= limit else f"+{len(segments) - limit}more"
    return f"{sample}{suffix}"


def normalize_to_pixels(
    segments: list[VLMSegment], width_px: int, height_px: int
) -> list[tuple[int, int, int, int]]:
    """Convert each segment's normalized bbox to absolute (x, y, w, h) in pixels.

    Public so stage 4 can use it without needing access to private helpers.
    """
    pixel_bboxes: list[tuple[int, int, int, int]] = []
    for segment in segments:
        x_min, y_min, x_max, y_max = segment.bbox
        x = int(x_min * width_px)
        y = int(y_min * height_px)
        w = int((x_max - x_min) * width_px)
        h = int((y_max - y_min) * height_px)
        pixel_bboxes.append((x, y, max(w, 1), max(h, 1)))
    return pixel_bboxes
