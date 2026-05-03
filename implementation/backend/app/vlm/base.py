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
        LegendRegionTool,
        NotesRegionTool,
        PageRegionsTool,
        PlanViewTool,
        RefineSegmentTool,
        ScheduleTool,
        TitleBlockTool,
    )


class VLMError(Exception):
    """Stage 4 catches this and degrades to CV-only mode (§9)."""


class VLMClient(Protocol):
    def detect(self, image: Image, *, prompt_version: str = "v1") -> DetectionResult: ...

    def disambiguate_region(self, crop: Image, question: str) -> str: ...

    def categorize_region(self, crop: Image) -> CategorizePageTool: ...

    def detect_page_regions(self, image: Image) -> PageRegionsTool:
        """Combined-call page categorization (SOLUTION-DESIGN-V2 §5.3).

        Retained for backward compatibility — the categorizer's VLM-first
        path no longer calls this method. It now uses two focused calls
        (``detect_plan_view``, ``detect_legend``) because manual testing
        showed that asking small VLMs to disambiguate across five region
        types in one call produces consistent failures (over-segmenting
        tables, swapping legend↔notes, clipping plan_view at title
        banners). Implementations may keep this method for callers that
        still rely on the combined schema.
        """
        ...

    def detect_plan_view(self, image: Image) -> PlanViewTool:
        """Focused VLM call for plan-view detection (SOLUTION-DESIGN-V2 §5.3).

        ``image`` is the full-page raster (typically ``ctx.source.raster_probe``
        at probe DPI). The implementation downscales internally so smaller
        models stay within their native input window. Returns a single
        normalized [0, 1] bbox or ``None`` when no plan view is present;
        the calling stage pads and scales the bbox to ``RectPt``.
        """
        ...

    def detect_legend(self, image: Image) -> LegendRegionTool:
        """Focused VLM call for legend detection (SOLUTION-DESIGN-V2 §5.3).

        Same input as ``detect_plan_view``. Returns zero or more normalized
        [0, 1] bboxes — engineering drawings frequently split the legend
        into a symbol box and a separate abbreviation table, so the
        multi-bbox shape is preserved here. An empty list means "no
        legend on this drawing"; the calling stage treats that as a
        non-failure (``layout.legend = None``).
        """
        ...

    def detect_title_block(self, image: Image) -> TitleBlockTool:
        """Focused VLM call for title-block detection (SOLUTION-DESIGN-V2 §5.3).

        Same input shape as ``detect_plan_view``. Returns one normalized
        [0, 1] bbox covering the title banner + sheet-metadata box (when
        present together) or ``None`` when no title block is visible. The
        categorizer's auxiliary-first VLM-first path uses this region to
        clip plan_view's nearest page edge; a missing title block leaves
        that edge un-clipped.
        """
        ...

    def detect_notes(self, image: Image) -> NotesRegionTool:
        """Focused VLM call for notes-region detection (SOLUTION-DESIGN-V2 §5.3).

        Same input shape as ``detect_plan_view``. Returns zero or more
        normalized [0, 1] bboxes — drawings sometimes carry notes in
        multiple non-adjacent columns. Empty list means "no notes on
        this drawing", which is non-failure. The categorizer treats
        each notes bbox as an independent auxiliary region for plan_view
        derivation.
        """
        ...

    def detect_schedule(self, image: Image) -> ScheduleTool:
        """Focused VLM call for schedule-region detection (SOLUTION-DESIGN-V2 §5.3).

        Same input shape as ``detect_plan_view``. Returns one normalized
        [0, 1] bbox covering the equipment / fixture specification table,
        or ``None`` when no schedule is present. Single-bbox shape is
        sufficient because schedules don't split across the page the way
        legends do.
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
