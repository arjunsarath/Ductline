"""VLM seam — pluggable agent boundary (SOLUTION-DESIGN §5.1, ADR-0002).

Stage 4 (and the stage 3 fallback) calls into a VLMClient. Implementations are
free to choose their own transport — the only contract is a typed `DetectionResult`
back from `detect`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PIL.Image import Image

    from app.vlm.tools import DetectionResult


class VLMError(Exception):
    """Stage 4 catches this and degrades to CV-only mode (§9)."""


class VLMClient(Protocol):
    def detect(self, image: Image, *, prompt_version: str = "v1") -> DetectionResult: ...

    def disambiguate_region(self, crop: Image, question: str) -> str: ...
