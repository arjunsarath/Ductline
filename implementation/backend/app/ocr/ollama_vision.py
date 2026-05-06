"""Ollama-hosted vision LLM as a per-crop OCR engine.

Vision LLMs hallucinate pixel coordinates, so we never ask them for bboxes.
Instead the runner crops each candidate rectangle (from CV) and sends each
crop here for a TEXT-ONLY read. Bboxes come from the CV pipeline; text
comes from the VLM. Best of both worlds.

Default model is ``qwen3-vl:235b-cloud`` because empirical testing on
testset2.pdf showed it reads partial / vertical / mangled labels much
better than the local ``llama3.2-vision``.
"""

from __future__ import annotations

import base64
import io
import json
from urllib import error, request

from PIL import Image

# qwen3-vl returns the round-duct mark as Greek `φ` (visually identical to ø);
# the runner normalises this back to `ø` so downstream regex is stable.
_OE_NORMALISE = str.maketrans({"φ": "ø", "Φ": "Ø"})

_DEFAULT_PROMPT = (
    "find the text in the image and return it and return nothing else. "
    'Only what you see. If empty, return "EMPTY"'
)
_OLLAMA_URL = "http://localhost:11434/api/generate"
_DEFAULT_MODEL = "qwen3-vl:235b-cloud"


def read_text_from_crop(
    image: Image.Image,
    *,
    model: str = _DEFAULT_MODEL,
    prompt: str = _DEFAULT_PROMPT,
    timeout: float = 60.0,
) -> str:
    """OCR a single small image crop via the Ollama vision API.

    Returns the raw recognised text (with ``φ`` normalised to ``ø``).
    Empty regions return the literal ``"EMPTY"``. Connection failures and
    JSON errors return the literal ``"ERROR"`` so the pipeline doesn't crash.
    """
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    body = {
        "model": model,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
        "options": {"temperature": 0, "num_predict": 32},
    }
    req = request.Request(
        _OLLAMA_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except (error.URLError, OSError, json.JSONDecodeError):
        return "ERROR"

    text = payload.get("response", "").strip()
    return text.translate(_OE_NORMALISE)
