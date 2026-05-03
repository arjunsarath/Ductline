"""VLM seam — pluggable agent boundary (SOLUTION-DESIGN §5.1, ADR-0002).

Stage 4 (and the stage 3 fallback) calls into a VLMClient. Implementations are
free to choose their own transport — the only contract is a typed `DetectionResult`
back from `detect`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PIL.Image import Image

    from app.pipeline.base import VLMSegmentDraft
    from app.pipeline.legend import Legend
    from app.vlm.tools import (
        CategorizePageTool,
        DetectionResult,
        PageRegionsTool,
        RefineSegmentTool,
    )


class VLMError(Exception):
    """Stage 4 catches this and degrades to CV-only mode (§9)."""


class VLMClient(Protocol):
    def detect(self, image: Image, *, prompt_version: str = "v1") -> DetectionResult: ...

    def disambiguate_region(self, crop: Image, question: str) -> str: ...

    def categorize_region(self, crop: Image) -> CategorizePageTool: ...

    def detect_page_regions(self, image: Image) -> PageRegionsTool:
        """VLM-first page categorization (SOLUTION-DESIGN-V2 §5.3).

        ``image`` is the full-page raster (typically ``ctx.source.raster_probe``
        at probe DPI). The implementation downscales internally so smaller
        models stay within their native input window. Returned bboxes are
        normalized [0, 1] in the page's coord space (post-rotation, pre-tile);
        the calling stage scales them to ``RectPt``.
        """
        ...

    def detect_tile(
        self,
        crop: Image,
        *,
        tile_position: tuple[int, int, int, int],
        trail_context: list[dict],
        legend: Legend | None,
    ) -> DetectionResult:
        """Per-tile detection (SOLUTION-DESIGN-V2 §5.5, ADR-0008).

        ``tile_position`` is ``(row, col, total_rows, total_cols)`` (0-indexed
        row/col). ``trail_context`` is a list of ``{bbox_normalized, shape_hint}``
        dicts in the CURRENT tile's coord space — segments already detected in
        tiles to the left in this row + tiles in previous rows. ``legend`` is
        the parsed drawing legend (PR-4); None means "no legend context".
        """
        ...

    def refine_segment(
        self,
        crop: Image,
        *,
        critique: str,
        previous: VLMSegmentDraft,
    ) -> RefineSegmentTool:
        """Refine one segment given the reviewer's critique (SOLUTION-DESIGN-V2 §5.6).

        ``crop`` is a high-DPI render of the segment bbox + padding, rendered
        fresh by the reviewer stage (same crop the reviewer saw). ``critique``
        is the reviewer's one-sentence ``reason`` — passed verbatim into the
        prompt. ``previous`` carries the draft as it stood before this
        iteration so the model can reconsider geometry/shape rather than
        starting from scratch.

        Output ``bbox_normalized`` is in the crop's own [0, 1] coord space —
        the calling stage projects back into source coords.
        """
        ...
