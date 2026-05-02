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
from io import BytesIO
from pathlib import Path

import httpx
from PIL.Image import Image
from pydantic import ValidationError

from app.vlm.base import VLMClient, VLMError
from app.vlm.tools import CategorizePageTool, DetectDuctsTool, DetectionResult, VLMSegment

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

        raw_response = self._post("/api/generate", payload).get("response", "")
        tool = _parse_tool_call(raw_response)

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
        raw = self._post("/api/generate", payload).get("response", "")
        if not raw:
            raise VLMError("empty response from VLM categorize_region")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VLMError(f"VLM categorize JSON invalid: {exc}") from exc
        try:
            return CategorizePageTool.model_validate(data)
        except ValidationError as exc:
            raise VLMError(
                f"VLM categorize JSON failed schema: {exc.error_count()} errors"
            ) from exc

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
# bbox responses with three tells: duplicate bboxes, coords confined to a
# tenth-grid like 0.1 / 0.2 / 0.3 …, or absurdly long lists. Detecting these
# lets stage 4 fall back to filtered CV detection instead of feeding the
# pipeline garbage.
_GRID_VALUES = {round(i * 0.1, 1) for i in range(11)}
_HALLUCINATED_DUPLICATE_THRESHOLD = 0.5  # ≥50% duplicates → reject
_HALLUCINATED_GRID_THRESHOLD = 0.9       # ≥90% on the tenth grid → reject
_HALLUCINATED_COUNT_LIMIT = 80           # > this many segments is implausible


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
        1 for s in segments if all(round(c, 1) in _GRID_VALUES for c in s.bbox)
    )
    if len(segments) >= 4 and on_grid / len(segments) >= _HALLUCINATED_GRID_THRESHOLD:
        raise VLMError("VLM bboxes lie on a tenth-grid — likely hallucinated")


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
